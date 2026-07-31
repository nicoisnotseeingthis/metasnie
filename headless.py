"""
Headless runner for the Meta Horizon username checker.
Imports main.py with a tkinter stub so the same checking, refreshing,
claiming and webhook logic runs without a GUI inside GitHub Actions.
"""
import os
import sys
import time
import threading
import types

# Make sure main.py (same folder) is importable
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---------------------------------------------------------------------------
# tkinter stub so main.py loads on a headless Linux/GitHub runner
# ---------------------------------------------------------------------------
_tk = types.ModuleType("tkinter")
_tk.__path__ = []


def _make_dummy(name):
    """Return a do-nothing class for any missing tkinter symbol."""
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

# ---------------------------------------------------------------------------
# Load main.py's logic unchanged
# ---------------------------------------------------------------------------
import main as M

CFG = M.cfg_load()

# Allow secret override of the webhook URL
if os.environ.get("WEBHOOK_URL"):
    CFG["webhook_url"] = os.environ["WEBHOOK_URL"]
    CFG["webhook_enabled"] = True

# Normalize Windows backslashes for Linux runners
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


def log(msg, tag="info"):
    print(f"[{time.strftime('%H:%M:%S')}] [{tag}] {msg}", flush=True)


def send_hook(content):
    """Post to Discord. allowed_mentions is required to actually ping @everyone."""
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


# ---------------------------------------------------------------------------
# Patch the sniper so a successful claim immediately sends the @everyone webhook
# ---------------------------------------------------------------------------
_orig_change = M.MetaUsernameSniper.change


def _patched_change(self, name, retries=3, retry_delay=0.15):
    res = _orig_change(self, name, retries, retry_delay)
    if res.get("success"):
        acct = self.cred.get("username") or self.profile_id[:8]
        send_claimed(name, acct)
    return res


M.MetaUsernameSniper.change = _patched_change

# ---------------------------------------------------------------------------
# Engine callbacks
# ---------------------------------------------------------------------------
last_stats = [0.0]


def on_stats(cycle, found, ms, cps, checked):
    now = time.time()
    if now - last_stats[0] > 2:
        log(f"cycle {cycle} | found {found} | cps {cps:.0f} | checked {checked}", "stats")
        last_stats[0] = now


def on_status(s):
    log(f"status: {s}", "info")


def on_found(name):
    log(f"FOUND {name}", "available")


def on_snipe(name):
    log(f"SNIPE {name}", "snipe")
    M.snipe_claim(name, on_log=log)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
log(
    f"loaded {len(NAMES)} names | snipe={CFG.get('snipe_mode', False)} | "
    f"webhook={CFG.get('webhook_enabled', False)}",
    "info",
)

# Same refresh as the manual "Refresh" button in the GUI
M.refresh_account_usernames(on_log=log)

engine = M.Engine(CFG, log, on_status, on_found, on_stats, on_snipe)
engine.start()

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
