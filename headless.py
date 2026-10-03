import os
import sys
import time
import json
import subprocess
import threading
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_tk = types.ModuleType("tkinter")
_tk.__path__ = []

def _make_dummy(name):
    return type(name, (), {})

def _tk_getattr(name):
    if name in ("ttk", "filedialog", "messagebox"):
        full = f"tkinter.{name}"
        if full not in sys.modules:
            sub = types.ModuleType(full)
            sub.__getattr__ = lambda n: _make_dummy(n)
            sub.__path__ = []
            sys.modules[full] = sub
        return sys.modules[full]
    return _make_dummy(name)

_tk.__getattr__ = _tk_getattr
sys.modules["tkinter"] = _tk

import main as M

CFG = M.cfg_load()

if os.environ.get("WEBHOOK_URL"):
    CFG["webhook_url"] = os.environ["WEBHOOK_URL"]
    CFG["webhook_enabled"] = True

LIST = CFG.get("selected_list", "").replace("\\", "/")
CFG["selected_list"] = LIST

os.makedirs("lists", exist_ok=True)
os.makedirs("creds", exist_ok=True)

if not LIST or not os.path.exists(LIST):
    print(f"[FATAL] list not found: {LIST}", flush=True)
    sys.exit(1)

NAMES = M.names_load(LIST)
if not NAMES:
    print("[FATAL] list is empty", flush=True)
    sys.exit(1)

MAX_RUNTIME = int(os.environ.get("MAX_RUNTIME", "0"))

_QUIET = {"taken", "timeout", "unknown"}


def log(msg, tag="info"):
    if tag in _QUIET:
        return
    print(f"[{time.strftime('%H:%M:%S')}] [{tag}] {msg}", flush=True)

def send_hook(content):
    if not CFG.get("webhook_enabled") or not CFG.get("webhook_url"):
        return
    try:
        import requests as _requests

        _requests.post(
            CFG["webhook_url"],
            json={
                "content": content,
                "allowed_mentions": {"parse": ["everyone"]},
            },
            timeout=5,
        )
    except Exception:
        pass

def send_available(name):
    tpl = CFG.get(
        "webhook_template",
        "**Available** `{name}` — https://horizon.meta.com/profile/{name}/",
    )
    send_hook(tpl.format(name=name))

def send_claimed(name, acct=""):
    send_hook(
        f"@everyone **Successfully claimed `{name}`!** (acct: {acct}) — "
        f"https://horizon.meta.com/profile/{name}/"
    )

REPORT_EVERY = float(os.environ.get("REPORT_HOURS") or CFG.get("report_hours", 4)) * 3600
STATS_FILE = os.path.join("state", "stats.json")
os.makedirs("state", exist_ok=True)

def _fresh_stats():
    return {"since": time.time(), "checks": 0, "runtime": 0.0, "runs": 0,
            "found": [], "claims": [], "attempts": 0, "throttled": 0, "false_pos": 0}

try:
    with open(STATS_FILE) as _f:
        stats = {**_fresh_stats(), **json.load(_f)}
except Exception:
    stats = _fresh_stats()
stats["runs"] += 1

def save_stats():
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f)
    except Exception:
        pass

def build_report():
    hrs = max((time.time() - stats["since"]) / 3600, 0.01)
    cps = stats["checks"] / stats["runtime"] if stats["runtime"] > 0 else 0
    uniq = sorted(set(stats["found"]))
    lines = [
        f"**Update** — last {hrs:.1f}h",
        f"Checks: **{stats['checks']:,}** (avg {cps:.0f}/s) across {len(NAMES)} names, {stats['runs']} run(s)",
        f"Available (confirmed): **{len(uniq)}**" + (f" — {', '.join(f'`{n}`' for n in uniq[:20])}" if uniq else ""),
        f"Claimed: **{len(stats['claims'])}**" + (f" — {', '.join(f'`{n}`' for n in stats['claims'])}" if stats["claims"] else ""),
        f"Claim attempts: {stats['attempts']} | throttle events: {stats['throttled']} | false positives filtered: {stats['false_pos']}",
    ]
    return "\n".join(lines)

def post_report():
    msg = build_report()
    log(msg.replace("\n", " | "), "report")
    send_hook(msg)
    stats.clear(); stats.update(_fresh_stats()); stats["runs"] = 1
    save_stats()

_seen = {"checks": 0, "t": time.time(), "thr": 0, "fp": 0}

def flush_stats(final=False):
    eng = globals().get("engine")
    now = time.time()
    if eng is not None:
        stats["checks"] += max(eng._total_checks - _seen["checks"], 0)
        stats["throttled"] += max(eng._throttle_events - _seen["thr"], 0)
        stats["false_pos"] += max(eng._false_pos - _seen["fp"], 0)
        _seen.update(checks=eng._total_checks, thr=eng._throttle_events, fp=eng._false_pos)
    stats["runtime"] += now - _seen["t"]
    _seen["t"] = now
    save_stats()
    if not final and (now - stats["since"] >= REPORT_EVERY or os.environ.get("REPORT_NOW")):
        os.environ.pop("REPORT_NOW", None)
        post_report()

def _stats_thread():
    while True:
        time.sleep(60)
        flush_stats()

threading.Thread(target=_stats_thread, daemon=True).start()

_orig_change = M.MetaUsernameSniper.change

def _patched_change(self, name, retries=3, retry_delay=0.15):
    res = _orig_change(self, name, retries, retry_delay)
    stats["attempts"] += 1
    if res.get("success"):
        stats["claims"].append(name)
        acct = self.cred.get("username") or self.profile_id[:8]
        send_claimed(name, acct)
    return res

M.MetaUsernameSniper.change = _patched_change

last_stats = [0.0]

def on_stats(cycle, found, ms, cps, checked):
    now = time.time()
    if now - last_stats[0] > 2:
        eng = globals().get("engine")
        extra = f" | limit {eng._limit} | timeouts {eng._timeouts} ({eng._to_frac*100:.0f}%)" if eng else ""
        log(f"cycle {cycle} | found {found} | cps {cps:.0f} | checked {checked}{extra}", "stats")
        last_stats[0] = now

def on_status(s):
    log(f"status: {s}", "info")

def on_found(name):
    log(f"FOUND {name}", "available")
    stats["found"].append(name)

def on_snipe(name):
    log(f"SNIPE {name}", "snipe")
    M.snipe_claim(name, on_log=log)

log(
    f"loaded {len(NAMES)} names | snipe={CFG.get('snipe_mode', False)} | "
    f"webhook={CFG.get('webhook_enabled', False)}",
    "info",
)

M.refresh_account_usernames(on_log=log)

engine = M.Engine(CFG, log, on_status, on_found, on_stats, on_snipe)
_git_lock = threading.Lock()


def on_rename(old, new):
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    with _git_lock:
        g = ["git", "-c", "user.name=bot", "-c", "user.email=bot@users.noreply.github.com"]
        try:
            subprocess.run(g + ["add", LIST], check=True, timeout=30)
            subprocess.run(g + ["commit", "-m", "update list"], check=True, timeout=30)
            if subprocess.run(g + ["pull", "--rebase", "origin", "main"], timeout=60).returncode:
                subprocess.run(g + ["rebase", "--abort"], timeout=30)
            subprocess.run(g + ["push", "origin", "HEAD:main"], check=True, timeout=60)
            log(f"list updated: {old} -> {new}", "info")
        except Exception as e:
            log(f"list update not pushed: {e}", "warn")


engine.on_rename = on_rename
engine.start()
if time.time() - stats["since"] >= REPORT_EVERY:
    flush_stats()

try:
    if MAX_RUNTIME > 0:
        log(f"running for max {MAX_RUNTIME}s", "info")
        time.sleep(MAX_RUNTIME)
        log("max runtime reached, stopping", "warn")
        engine.stop()
    else:
        while engine.running:
            time.sleep(1)
except KeyboardInterrupt:
    log("interrupted, stopping", "warn")
    engine.stop()
finally:
    time.sleep(2)
    flush_stats(final=True)
