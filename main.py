"""
Meta Horizon Username Checker
"""
import aiohttp
import asyncio
import collections
import concurrent.futures
import time
import os
import json
import threading
import re
import uuid
import random
import atexit
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:    import pyperclip
except: pyperclip = None
try:    import requests as _requests
except: _requests = None

def _keep_awake():
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)
    except: pass
def _restore():
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    except: pass
_keep_awake()
atexit.register(_restore)

import colorsys

def _hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h/360, max(0,min(1,s)), max(0,min(1,v)))
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

def _build_palette(hue, sat):
    # atmosphere colors shift with hue
    # functional colors (text, green, red, etc) stay constant
    return {
        "bg":         _hsv(hue, 0.10, 0.04),
        "panel":      _hsv(hue, 0.15, 0.10),
        "border":     _hsv(hue, 0.30, 0.22),
        "border_hi":  _hsv(hue, 0.40, 0.48),
        "accent":     _hsv(hue, 0.70, 1.0),
        "accent2":    _hsv(hue, 0.50, 0.75),
        "text":       _hsv(0, 0, 0.95),
        "dim":        _hsv(0, 0, 0.45),
        "dim2":       _hsv(0, 0, 0.28),
        "green":      _hsv(140, 0.60, 0.88),
        "red":        _hsv(355, 0.70, 0.95),
        "yellow":     _hsv(44,  0.70, 0.95),
        "taken":      _hsv(0, 0, 0.24),
        "entry_bg":   _hsv(hue, 0.12, 0.08),
    }

PRESETS = {
    "Purple": {"hue": 270, "sat": 0.92},
    "Teal":   {"hue": 165, "sat": 0.85},
    "Amber":  {"hue": 35,  "sat": 0.90},
    "Red":    {"hue": 348, "sat": 0.85},
    "Blue":   {"hue": 198, "sat": 0.72},
    "Green":  {"hue": 105, "sat": 0.92},
    "Orange": {"hue": 18,  "sat": 0.95},
}

C = _build_palette(270, 0.92)

FONT_BASE = ("Consolas", 10)
FONT_BOLD = ("Consolas", 10, "bold")
FONT_S    = ("Consolas", 9)
FONT_XS   = ("Consolas", 8)
FONT_H1   = ("Consolas", 12, "bold")
FONT_H2   = ("Consolas", 10)
FONT_LBL  = ("Segoe UI", 9)
FONT_STAT = ("Consolas", 10, "bold")
FONT_TINY = ("Consolas", 6)

BD = 2  # global border width

CFG_FILE = "checker_config.json"
CFG_DEF  = {
    "concurrency": 500, "loop_mode": True, "snipe_mode": False,
    "copy_clipboard": True, "sound_alert": True,
    "webhook_enabled": False, "webhook_url": "",
    "webhook_template": "**Available** `{name}`",
    "cycle_delay": 0, "selected_list": "",
    "timeout_total": 0.5, "timeout_connect": 0.1,
    "theme_hue": 270, "theme_sat": 0.92, "opacity": 1.0, "preset": "Purple",
    "log_filter": "all",
}

def cfg_load():
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE) as f: d = json.load(f)
            for k, v in CFG_DEF.items(): d.setdefault(k, v)
            return d
        except: pass
    return dict(CFG_DEF)

def cfg_save(d):
    try:
        with open(CFG_FILE, "w") as f: json.dump(d, f, indent=2)
    except: pass

LISTS_DIR = "lists"
def lists_ensure(): os.makedirs(LISTS_DIR, exist_ok=True)
def lists_all():
    lists_ensure()
    return sorted(f for f in os.listdir(LISTS_DIR) if f.endswith(".txt"))

def names_load(path):
    try:
        with open(path, encoding="utf-8") as f:
            raw = [l.strip() for l in f if l.strip()]
        seen, out = set(), []
        for n in raw:
            if n.lower() not in seen: seen.add(n.lower()); out.append(n)
        if len(out) != len(raw):
            with open(path, "w", encoding="utf-8") as f: f.write("\n".join(out) + "\n")
        return out
    except: return []

def parse_status(code, location, final_url, name):
    slug = f"/profile/{name}"
    if code in (301, 302, 303, 307, 308):
        if location == "https://horizon.meta.com/": return "AVAILABLE"
        if slug in location: return "TAKEN"
        return "UNKNOWN"
    if code == 200: return "TAKEN" if slug in final_url else "AVAILABLE"
    if code == 404: return "AVAILABLE"
    if code == 429: return "RATE"
    return "UNKNOWN"

# ═══════════════════════════════════════════════════════════════════════════════
# CREDS
# ═══════════════════════════════════════════════════════════════════════════════
CREDS_DIR = "creds"
CREDS_FILE = os.path.join(CREDS_DIR, "creds.json")

def creds_ensure():
    os.makedirs(CREDS_DIR, exist_ok=True)
    if not os.path.exists(CREDS_FILE):
        with open(CREDS_FILE, "w") as f: json.dump([], f)

def creds_load():
    creds_ensure()
    try:
        with open(CREDS_FILE) as f: data = json.load(f)
        for c in data:
            c.setdefault('username', None); c.setdefault('sniped', None)
            c.setdefault('rate_limited', False); c.setdefault('rate_limited_since', None)
            c.setdefault('sniper_enabled', True); c.setdefault('locked', False)
        return data
    except: return []

def creds_save(data):
    creds_ensure()
    with open(CREDS_FILE, "w") as f: json.dump(data, f, indent=2)

def creds_add(profile_id, fs_token, username=""):
    data = creds_load()
    data.append({"PROFILE_ID": profile_id.strip(), "fs": fs_token.strip(),
                 "username": username.strip() or None, "sniped": None,
                 "rate_limited": False, "rate_limited_since": None,
                 "sniper_enabled": True, "locked": False})
    creds_save(data)

def creds_remove(index):
    data = creds_load()
    if 0 <= index < len(data): data.pop(index); creds_save(data)

def creds_update(index, key, value):
    data = creds_load()
    if 0 <= index < len(data): data[index][key] = value; creds_save(data)

# ═══════════════════════════════════════════════════════════════════════════════
# GRAPHQL SNIPER
# ═══════════════════════════════════════════════════════════════════════════════
API_URL = 'https://accountscenter.meta.com/api/graphql/'
TOKEN_CACHE = {}
TOKEN_CACHE_LOCK = threading.Lock()
RATE_LIMIT_COOLDOWN = 90   # seconds — Meta rate limits typically clear in 30–120s

# ── Sniper Pool ───────────────────────────────────────────────────────────────
# Pre-initializes everything so fire() has zero startup cost:
#   - Persistent MetaUsernameSniper instances (warm sessions, pre-built payloads)
#   - Live ThreadPoolExecutor (no creation cost at snipe time)
#   - Background token refresher keeps payloads always current
# When AVAILABLE fires → fire() is a single .submit() call, nothing else.
class SniperPool:
    REFRESH_INTERVAL = 210  # seconds — full rebuild of all accounts every 3.5 min
    RETRY_INTERVAL   = 30   # seconds — retry only failed-token accounts every 30s

    def __init__(self):
        self._snipers: list[tuple[int, dict, 'MetaUsernameSniper']] = []
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._refresh_stop = threading.Event()
        self._refresh_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._on_log = None
        self._failed_warm: list = []
        # In-memory mirror of per-account status — eliminates disk read on hot path.
        # Keyed by account index; updated atomically by _set_state().
        self._state: dict[int, dict] = {}

    def start(self, on_log=None):
        self._on_log = on_log
        self._rebuild()
        # Start background refresher
        self._refresh_stop.clear()
        if not (self._refresh_thread and self._refresh_thread.is_alive()):
            self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
            self._refresh_thread.start()

    def stop(self):
        self._refresh_stop.set()

    def _log(self, msg, tag="info"):
        if self._on_log: self._on_log(msg, tag)

    def _rebuild(self):
        """Load creds, create/reuse sniper instances, warm tokens — all in parallel."""
        if not _requests: return
        creds = creds_load()
        if not creds: return

        entries = []
        for i, c in enumerate(creds):
            rl = (time.time() - c.get('rate_limited_since', 0)) < RATE_LIMIT_COOLDOWN if c.get('rate_limited') else False
            if c.get('sniper_enabled', True) and not rl:
                entries.append((i, c, MetaUsernameSniper(c)))

        # Warm tokens in parallel — track failures for fast retry
        failed = []
        def _warm(entry):
            i, c, sniper = entry
            ok = False
            try: ok = sniper.refresh_tokens()
            except: pass
            if not ok:
                uname = c.get('username') or c['PROFILE_ID'][:8]
                self._log(f"account {i+1} ({uname}): token refresh failed — retrying in {self.RETRY_INTERVAL}s", "warn")
                failed.append(entry)

        if entries:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(entries)) as ex:
                list(ex.map(_warm, entries))

        # Populate in-memory state from the freshly-loaded creds (all accounts, not just active)
        new_state = {}
        for i, c in enumerate(creds):
            new_state[i] = {
                'locked':              c.get('locked', False),
                'sniped':              c.get('sniped'),
                'rate_limited':        c.get('rate_limited', False),
                'rate_limited_since':  c.get('rate_limited_since'),
                'sniper_enabled':      c.get('sniper_enabled', True),
            }

        with self._lock:
            self._snipers = entries
            self._failed_warm = failed
            self._state = new_state
            if self._pool:
                try: self._pool.shutdown(wait=False, cancel_futures=True)
                except: self._pool.shutdown(wait=False)
            n = max(len(entries), 1)
            self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=n, thread_name_prefix="snipe")

        ok_n = len(entries) - len(failed)
        self._log(f"sniper ready — {ok_n}/{len(entries)} accounts with fresh tokens", "info")

    def _retry_failed_tokens(self):
        """Retry only accounts whose last token refresh failed — runs every RETRY_INTERVAL."""
        with self._lock:
            to_retry = list(self._failed_warm)
        if not to_retry: return

        still_failed = []
        for i, c, sniper in to_retry:
            ok = False
            try: ok = sniper.refresh_tokens()
            except: pass
            uname = c.get('username') or c['PROFILE_ID'][:8]
            if ok:
                self._log(f"account {i+1} ({uname}): tokens recovered ✓", "info")
            else:
                still_failed.append((i, c, sniper))

        with self._lock:
            self._failed_warm = still_failed

    def _set_state(self, idx, key, value):
        """Update in-memory state and write through to disk. Zero disk reads on the call side."""
        with self._lock:
            if idx in self._state:
                self._state[idx][key] = value
        creds_update(idx, key, value)

    def _get_state(self, idx):
        with self._lock:
            return dict(self._state.get(idx, {}))

    def _refresh_loop(self):
        """Full rebuild every REFRESH_INTERVAL; retry failed-token accounts every RETRY_INTERVAL."""
        next_full = time.time() + self.REFRESH_INTERVAL
        while not self._refresh_stop.wait(self.RETRY_INTERVAL):
            if time.time() >= next_full:
                self._rebuild()
                next_full = time.time() + self.REFRESH_INTERVAL
            else:
                self._retry_failed_tokens()

    def fire(self, name, on_log=None):
        """
        Called the instant AVAILABLE is detected.
        Cross-references live creds for stale lock/snipe status, then submits
        all available accounts to the live pool simultaneously.
        Returns immediately — results arrive via on_log asynchronously.
        """
        log = on_log or self._on_log or (lambda m, t: None)

        with self._lock:
            sniper_map = {c['PROFILE_ID']: (i, c, s) for i, c, s in self._snipers}
            pool = self._pool

        # Pool not ready (startup race) — do one synchronous rebuild, no recursion.
        if not pool:
            if not _requests:
                log("snipe: requests library not installed", "err"); return
            live_check = creds_load() or []
            if not live_check:
                log("snipe: no accounts in creds.json", "err"); return
            log("snipe: pool not ready — doing one-shot rebuild", "warn")
            self._rebuild()
            with self._lock:
                sniper_map = {c['PROFILE_ID']: (i, c, s) for i, c, s in self._snipers}
                pool = self._pool
            if not pool:
                log("snipe: pool still unavailable after rebuild — aborting", "err"); return

        # Use in-memory state — zero disk reads on the hot path
        snipers = []
        skipped_rl = []
        now = time.time()
        for pid, (i, _cached_c, sniper) in sniper_map.items():
            st = self._get_state(i)
            sniped = st.get('sniped')
            if sniped and sniped not in ('', 'null'): continue
            if st.get('locked'): continue
            if not st.get('sniper_enabled', True): continue
            if st.get('rate_limited'):
                since = st.get('rate_limited_since') or 0
                elapsed = now - since
                if elapsed < RATE_LIMIT_COOLDOWN:
                    skipped_rl.append((i, int(RATE_LIMIT_COOLDOWN - elapsed)))
                    continue
                # Cooldown expired — clear and include
                self._set_state(i, 'rate_limited', False)
                self._set_state(i, 'rate_limited_since', None)
                log(f"account {i+1}: rate limit cleared, back in rotation", "info")
            snipers.append((i, _cached_c, sniper))

        if skipped_rl:
            for i, remaining in skipped_rl:
                log(f"account {i+1}: rate limited — recovers in {remaining}s", "warn")

        if not snipers:
            log("snipe: no available accounts (all locked/rate-limited/used)", "warn"); return

        log(f"FIRING {len(snipers)} accounts simultaneously for '{name}'", "snipe")

        winner = [None]
        winner_lock = threading.Lock()

        def _attempt(entry):
            idx, c, sniper = entry
            uname = c.get('username') or c['PROFILE_ID'][:8]
            result = sniper.change(name)
            if result.get('success'):
                with winner_lock:
                    if winner[0] is None:
                        winner[0] = idx
                        self._set_state(idx, 'sniped', name)
                        self._set_state(idx, 'username', name)
                        self._set_state(idx, 'locked', True)
                        log(f"✓ SNIPED '{name}' — account {idx+1} ({uname})", "available")
                        def _confirm():
                            actual = fetch_horizon_username(c['PROFILE_ID'])
                            if actual:
                                self._set_state(idx, 'username', actual)
                                log(f"confirmed: {actual}", "info")
                        threading.Thread(target=_confirm, daemon=True).start()
            else:
                err = result.get('error', 'unknown')
                log(f"account {idx+1} ({uname}): {err}", "err")
                if any(kw in str(err).lower() for kw in ('rate', 'spam', 'limit', 'block')):
                    self._set_state(idx, 'rate_limited', True)
                    self._set_state(idx, 'rate_limited_since', time.time())

        futures = [pool.submit(_attempt, entry) for entry in snipers]
        threading.Thread(target=lambda: concurrent.futures.wait(futures), daemon=True).start()

    def swap_fire(self, name, interval=0.1, duration=5.0, on_log=None):
        """
        SWAP MODE — hammers the claim every `interval` seconds for up to `duration` seconds.
        Does NOT wait for the checker to detect AVAILABLE first. Call this the instant
        you initiate the swap on your phone so the claim lands the moment the window opens.
        """
        log = on_log or self._on_log or (lambda m, t: None)

        def _run():
            # Ensure pool is ready
            with self._lock:
                pool = self._pool
                sniper_map = list(self._snipers)
            if not pool:
                if not _requests:
                    log("swap: requests not installed", "err"); return
                self._rebuild()
                with self._lock:
                    pool = self._pool
                    sniper_map = list(self._snipers)
            if not pool:
                log("swap: pool unavailable after rebuild", "err"); return

            # Filter using in-memory state — zero disk reads
            snipers = []
            now = time.time()
            for i, _c, sniper in sniper_map:
                st = self._get_state(i)
                sniped = st.get('sniped')
                if sniped and sniped not in ('', 'null'): continue
                if st.get('locked'): continue
                if not st.get('sniper_enabled', True): continue
                if st.get('rate_limited'):
                    since = st.get('rate_limited_since') or 0
                    if now - since < RATE_LIMIT_COOLDOWN:
                        remaining = int(RATE_LIMIT_COOLDOWN - (now - since))
                        log(f"account {i+1}: rate limited — recovers in {remaining}s", "warn")
                        continue
                    self._set_state(i, 'rate_limited', False)
                    self._set_state(i, 'rate_limited_since', None)
                    log(f"account {i+1}: rate limit cleared, back in rotation", "info")
                snipers.append((i, _c, sniper))

            if not snipers:
                log("swap: no available accounts (all locked/rate-limited/used)", "warn"); return

            n_acc = len(snipers)
            n_rounds = int(duration / interval)
            log(f"SWAP ARMED — '{name}' · {n_acc} acct(s) · every {int(interval*1000)}ms · {duration}s window", "snipe")

            won = threading.Event()
            winner_lock = threading.Lock()

            def _attempt(idx, c, sniper):
                if won.is_set(): return
                uname = c.get('username') or c['PROFILE_ID'][:8]
                result = sniper.change(name, retries=1, retry_delay=0)
                if result.get('success'):
                    with winner_lock:
                        if not won.is_set():
                            won.set()
                            self._set_state(idx, 'sniped', name)
                            self._set_state(idx, 'username', name)
                            self._set_state(idx, 'locked', True)
                            log(f"✓ SNIPED '{name}' on attempt (acct {idx+1} · {uname})", "available")
                            def _confirm():
                                actual = fetch_horizon_username(c['PROFILE_ID'])
                                if actual:
                                    self._set_state(idx, 'username', actual)
                                    log(f"confirmed username: {actual}", "info")
                            threading.Thread(target=_confirm, daemon=True).start()

            deadline = time.time() + duration
            for rnd in range(n_rounds):
                if won.is_set(): break
                round_start = time.time()
                futures = [pool.submit(_attempt, i, c, s) for i, c, s in snipers]
                concurrent.futures.wait(futures, timeout=interval * 0.9)
                if won.is_set(): break
                sleep_rem = interval - (time.time() - round_start)
                if sleep_rem > 0: time.sleep(sleep_rem)

            if not won.is_set():
                log(f"swap: window closed — '{name}' not claimed after {n_rounds} rounds", "warn")

        threading.Thread(target=_run, daemon=True).start()

SNIPER_POOL = SniperPool()

# Persistent per-account sessions — TCP+TLS connections stay alive so
# the snipe POST has zero handshake overhead when it fires.
_SNIPE_SESSIONS: dict = {}
_SNIPE_SESSIONS_LOCK = threading.Lock()

def _get_snipe_session(cred):
    pid = cred['PROFILE_ID']
    with _SNIPE_SESSIONS_LOCK:
        if pid not in _SNIPE_SESSIONS:
            if not _requests: return None
            s = _requests.Session()
            s.cookies.update({'fs': cred['fs'], 'locale': 'en_US'})
            s.headers.update({
                'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15',
                'accept-language': 'en-US,en;q=0.9',
            })
            _SNIPE_SESSIONS[pid] = s
        return _SNIPE_SESSIONS[pid]

class MetaUsernameSniper:
    def __init__(self, cred):
        self.profile_id = cred['PROFILE_ID']
        self.cred = cred
        self.session = _get_snipe_session(cred)
        self.tokens = None
        self._cached_headers = None
        self._base_data = None

    @property
    def page_url(self):
        return f'https://accountscenter.meta.com/profiles/{self.profile_id}/username/'

    def refresh_tokens(self):
        cached = TOKEN_CACHE.get(self.profile_id)
        if cached and time.time() - cached['timestamp'] < 300:
            self.tokens = cached['tokens'].copy()
            self._build_cached_headers()
            return True
        try:
            r = self.session.get(self.page_url, timeout=15); html = r.text
            dtsg = re.search(r'DTSGInitialData",\[\],\{"token":"([^"]+)"', html)
            lsd  = re.search(r'"LSD",\[\],\{"token":"([^"]+)"\}', html)
            rev  = re.search(r'"client_revision":(\d+)', html)
            hsi  = re.search(r'"haste_session":"([^"]+)"', html)
            self.tokens = {'fb_dtsg': dtsg.group(1) if dtsg else None,
                           'lsd': lsd.group(1) if lsd else None,
                           '__rev': rev.group(1) if rev else None,
                           '__hsi': hsi.group(1) if hsi else None}
            with TOKEN_CACHE_LOCK:
                TOKEN_CACHE[self.profile_id] = {'tokens': self.tokens.copy(), 'timestamp': time.time()}
            self._build_cached_headers()
            return True
        except: return False

    def _build_cached_headers(self):
        """Pre-build the static headers so change() does zero dict work at snipe time."""
        if not self.tokens: return
        self._cached_headers = {
            'x-fb-lsd': self.tokens['lsd'],
            'x-fb-friendly-name': 'useFXIMUpdateUsernameMutation',
            'x-asbd-id': '359341',
            'content-type': 'application/x-www-form-urlencoded',
            'referer': self.page_url,
            'origin': 'https://accountscenter.meta.com',
        }
        # Pre-build the static parts of the POST body (everything except username)
        self._base_data = {
            'av': self.profile_id,
            'fb_dtsg': self.tokens['fb_dtsg'],
            'lsd': self.tokens['lsd'],
            '__rev': self.tokens['__rev'],
            '__hsi': self.tokens['__hsi'],
            'doc_id': '9672408826128267',
            'fb_api_req_friendly_name': 'useFXIMUpdateUsernameMutation',
            'fb_api_caller_class': 'RelayModern',
            'server_timestamps': 'true',
        }

    def change(self, username, retries=3, retry_delay=0.15):
        """
        Claim `username`. Retries up to `retries` times — critical for swap windows
        where Meta's servers haven't fully committed the change yet.
        GENERIC_ERROR / mutation_error_requires_reauth = name already taken or transient
        server error (snipe was too slow). Treated as a retriable collision, not a session
        issue — accounts are confirmed working if they can claim other names.
        """
        if not self.tokens and not self.refresh_tokens():
            return {'success': False, 'error': 'Failed tokens'}

        def _build_data():
            variables = json.dumps({
                "client_mutation_id": str(uuid.uuid4()),
                "family_device_id": "device_id_fetch_datr",
                "identity_ids": [self.profile_id],
                "target_fx_identifier": self.profile_id,
                "username": username,
                "interface": "FRL_WEB",
            })
            d = dict(self._base_data) if self._base_data else {}
            d['variables'] = variables
            return d

        last_err = 'unknown'
        for attempt in range(max(1, retries)):
            try:
                r = self.session.post(API_URL, headers=self._cached_headers,
                                      data=_build_data(), timeout=5)
                result = r.json()
                mut = result.get('data', {}).get('fxim_update_identity_username') or {}
                err_obj = mut.get('error') or (mut if mut.get('error_code') else None)
                if err_obj:
                    err_str = str(err_obj)
                    last_err = err_str
                    # Rate / spam — stop immediately, don't burn more attempts
                    if any(k in err_str.lower() for k in ('rate', 'spam', 'limit', 'block', 'flood')):
                        return {'success': False, 'error': f'rate_limited: {err_str}'}
                    # Token errors — refresh once then retry
                    if any(k in err_str.lower() for k in ('auth', 'session', 'login', 'token')):
                        if attempt == 0: self.refresh_tokens()
                        time.sleep(retry_delay); continue
                    # Everything else (GENERIC_ERROR, collision, swap-too-slow) — retry quickly
                    if attempt < retries - 1:
                        time.sleep(retry_delay); continue
                    return {'success': False, 'error': err_str}
                rules = (mut.get('ui_response', {})
                            .get('fx_identity_management', {})
                            .get('screen_rules_v2', {})
                            .get('username', {}))
                return {'success': True, 'error': None,
                        'attempt': attempt + 1,
                        'cooldown': rules.get('cooldown_status'),
                        'last_changed': rules.get('last_username_changed_date_text')}
            except Exception as e:
                last_err = str(e)
                if attempt < retries - 1:
                    time.sleep(retry_delay)
        return {'success': False, 'error': last_err}

    def check_token_valid(self):
        if not self.refresh_tokens(): return False, "token refresh failed"
        variables = json.dumps({
            "action_source": "EDIT", "identity_ids": [self.profile_id],
            "username": "test", "included_app_validations": []})
        data = {'av': self.profile_id, 'fb_dtsg': self.tokens['fb_dtsg'],
                'lsd': self.tokens['lsd'], '__rev': self.tokens['__rev'],
                '__hsi': self.tokens['__hsi'], 'variables': variables,
                'doc_id': '9751320178292067',
                'fb_api_req_friendly_name': 'useFXIMUsernameValidatorBaseQuery',
                'fb_api_caller_class': 'RelayModern', 'server_timestamps': 'true'}
        headers = {'x-fb-lsd': self.tokens['lsd'],
                   'x-fb-friendly-name': 'useFXIMUsernameValidatorBaseQuery',
                   'x-asbd-id': '359341', 'content-type': 'application/x-www-form-urlencoded',
                   'referer': self.page_url, 'origin': 'https://accountscenter.meta.com'}
        try:
            r = self.session.post(API_URL, headers=headers, data=data, timeout=15)
            result = r.json()
            status = result['data']['fx_identity_management']['validate_username_v4']['status_code']
            if status in ('SUCCESS', 'OK_GENERIC', 'ERROR_COLLISION'): return True, "ok"
            return False, f"unexpected: {status}"
        except Exception as e: return False, str(e)

def snipe_claim(name, on_log=None):
    """Thin wrapper — delegates to SNIPER_POOL which is already warm and ready."""
    SNIPER_POOL.fire(name, on_log=on_log)

def fetch_horizon_username(profile_id):
    """Scrape the Meta Horizon profile page to get the current username."""
    if not _requests: return None
    try:
        url = f'https://horizon.meta.com/profile/{profile_id}/?locale=en_US'
        r = _requests.get(url, timeout=15, headers={
            'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15'
        })
        html = r.text
        match = re.search(r'"display_name_ignore_block"\s*:\s*"([^"]+)"', html)
        if match: return match.group(1)
        match = re.search(r'<meta property="og:title" content="Check out ([^\'"]+)&#039;s profile', html)
        if match: return match.group(1)
        match = re.search(r'<title>Check out ([^\'\"]+)\'s profile', html)
        if match: return match.group(1)
    except: pass
    return None

def refresh_account_usernames(on_log=None):
    """Fetch current usernames from Horizon profile pages for all accounts."""
    if not _requests:
        if on_log: on_log("requests not installed", "err")
        return 0
    creds = creds_load()
    if not creds:
        if on_log: on_log("no accounts to refresh", "warn")
        return 0
    updated = 0
    for i, c in enumerate(creds):
        pid = c.get('PROFILE_ID')
        if not pid: continue
        if on_log: on_log(f"fetching account {i+1}...", "info")
        username = fetch_horizon_username(pid)
        if username:
            creds_update(i, 'username', username)
            if on_log: on_log(f"account {i+1}: {username}", "info")
            updated += 1
        else:
            if on_log: on_log(f"account {i+1}: could not fetch", "warn")
        time.sleep(0.3 + random.uniform(0, 0.3))
    creds_save(creds_load())
    return updated

def snipe_test_account(idx):
    creds = creds_load()
    if idx >= len(creds): return False, "invalid index"
    c = creds[idx]
    sniper = MetaUsernameSniper(c)
    ok, msg = sniper.check_token_valid()
    if not ok:
        creds_update(idx, 'rate_limited', True)
        creds_update(idx, 'rate_limited_since', time.time())
    else:
        creds_update(idx, 'rate_limited', False)
        creds_update(idx, 'rate_limited_since', None)
    return ok, msg

# ═══════════════════════════════════════════════════════════════════════════════
# CHECKER ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
class Engine:
    def __init__(self, cfg, on_log, on_status, on_found, on_stats, on_snipe):
        self.cfg, self.on_log, self.on_status = cfg, on_log, on_status
        self.on_found, self.on_stats, self.on_snipe = on_found, on_stats, on_snipe
        self.running, self.paused = False, False
        self._cache, self._found, self._cycle = set(), 0, 0
        self._total_checks = 0
        self._canaries, self._throttled_until, self._backoff = collections.deque(maxlen=4), 0.0, 0.5

    def start(self):
        if self.running: return
        self.running, self.paused = True, False
        self._cache.clear(); self._found = 0; self._cycle = 0
        self._total_checks = 0
        SNIPER_POOL.start(on_log=self.on_log)  # pre-warm all snipe sessions & tokens
        def _go():
            try:
                import uvloop; uvloop.install()
            except Exception: pass
            asyncio.run(self._run())
        threading.Thread(target=_go, daemon=True).start()

    def pause(self): self.paused = True
    def resume(self): self.paused = False

    def stop(self):
        self.running = False; self.paused = False

    async def _run(self):
        cfg = self.cfg
        path = cfg.get("selected_list", "")
        if not path or not os.path.exists(path):
            self.on_log("no list selected", "err"); self.running = False; return
        names = names_load(path)
        if not names:
            self.on_log("list is empty", "err"); self.running = False; return

        # Sane timeouts: 0 / huge values in old configs mean "hang forever", which
        # pins a worker slot on a dead socket.
        tc = float(cfg.get("timeout_total", 0) or 0)
        cc = float(cfg.get("timeout_connect", 0) or 0)
        tc = tc if 0 < tc <= 10 else 3.0
        cc = cc if 0 < cc <= 10 else 2.0
        # Global in-flight cap. Burst tests stayed clean at 64 (~230 names/s); the
        # old 5-per-name fan-out was 5x the requests for no faster detection.
        conc = int(cfg.get("concurrency", 0) or 0)
        conc = conc if 0 < conc <= 512 else 64
        loop_mode = cfg.get("loop_mode", True)
        self.on_log(f"{len(names)} names — HEAD x{conc} in flight, {tc}s timeout", "info")

        _timeout = aiohttp.ClientTimeout(total=tc, connect=cc)
        _hdrs = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US",
                 "Connection": "keep-alive", "Accept-Encoding": "identity"}
        conn = aiohttp.TCPConnector(
            limit=conc, limit_per_host=conc,
            use_dns_cache=True, ttl_dns_cache=600,
            keepalive_timeout=120, enable_cleanup_closed=True, ssl=False,
        )
        session = aiohttp.ClientSession(connector=conn, headers=_hdrs,
                                        timeout=_timeout, connector_owner=True)

        _log_q: collections.deque = collections.deque()

        async def _log_drain():
            while self.running:
                await asyncio.sleep(0.05)
                while _log_q:
                    msg, tag = _log_q.popleft()
                    self.on_log(msg, tag)

        t_start = time.perf_counter()

        async def _stats_loop():
            while self.running:
                await asyncio.sleep(0.25)
                el = time.perf_counter() - t_start
                cps = self._total_checks / el if el > 0 else 0
                self._cycle = self._total_checks // max(len(names), 1)
                self.on_stats(self._cycle, self._found, 0, cps, self._total_checks)

        # One shared cursor; every worker pulls the next name, so each name is
        # hit as often as the pipe allows with no per-request task/semaphore churn.
        idx = [0]
        n_names = len(names)

        async def _worker():
            while self.running:
                while self.paused and self.running: await asyncio.sleep(0.02)
                if not self.running: break
                wait = self._throttled_until - time.perf_counter()
                if wait > 0: await asyncio.sleep(min(wait, 0.5)); continue
                i = idx[0]; idx[0] += 1
                if not loop_mode and i >= n_names: break
                await self._check(session, names[i % n_names], _log_q)
                self._total_checks += 1

        try:
            self.on_status("running")
            self._throttled_until = 0.0; self._backoff = 0.5
            bg_tasks = [asyncio.create_task(_log_drain()),
                        asyncio.create_task(_stats_loop())]
            # Warm every pooled connection first (cold TLS handshake is ~500ms).
            await asyncio.gather(*[self._head(session, names[0]) for _ in range(conc)],
                                 return_exceptions=True)
            workers = [asyncio.create_task(_worker()) for _ in range(conc)]
            await asyncio.gather(*workers, return_exceptions=True)
            self.running = False
            for t in bg_tasks: t.cancel()
            while _log_q:
                msg, tag = _log_q.popleft(); self.on_log(msg, tag)
        finally:
            try: await session.close()
            except: pass
        self.running = False; self.on_status("stopped"); self.on_log("stopped", "info")

    async def _head(self, session, name):
        """One existence probe. HEAD + no redirect-follow: ~100ms, 0KB body."""
        url = f"https://horizon.meta.com/profile/{name}/"
        async with session.head(url, allow_redirects=False) as r:
            return parse_status(r.status, r.headers.get("Location", ""), url, name)

    async def _verify(self, session, name):
        """A bounce means 'no such profile' OR 'you are throttled'. Re-probe the name
        and a known-existing canary in parallel (one round trip). Only a repeat bounce
        with a canary that still resolves is a real AVAILABLE."""
        seed = self.cfg.get("canary")
        pool = [c for c in list(self._canaries) + ([seed] if seed else []) if c != name]
        if not pool: return False   # nothing known-good to compare against: can't confirm
        res = await asyncio.gather(self._head(session, name), self._head(session, pool[-1]),
                                   return_exceptions=True)
        again = res[0]
        if res[1] != "TAKEN":
            # Canary stopped resolving -> we are being throttled; back off, discard.
            self._throttled_until = time.perf_counter() + self._backoff
            self._backoff = min(self._backoff * 2, 10.0)
            return False
        self._backoff = 0.5
        return again == "AVAILABLE"

    async def _check(self, session, name, log_q: collections.deque):
        t = time.perf_counter()
        try:
            status = await self._head(session, name)
            ms = f"{(time.perf_counter() - t)*1000:.0f}ms"
            if status == "TAKEN":
                if name not in self._canaries: self._canaries.append(name)
                self._cache.discard(name)   # re-arm: alert again if it frees later
                log_q.append((f"TAKEN      {name:<22} {ms}", "taken"))
            elif status == "AVAILABLE":
                if name in self._cache: return
                if not await self._verify(session, name):
                    log_q.append((f"THROTTLED  {name:<22} bounce unconfirmed", "warn")); return
                if name not in self._cache:
                    self._cache.add(name); self._found += 1; self._alert(name)
                self.on_log(f"AVAILABLE  {name:<22} {ms}", "available")
            elif status == "RATE":
                self._throttled_until = time.perf_counter() + self._backoff
                self._backoff = min(self._backoff * 2, 10.0)
                log_q.append((f"RATE       {name:<22}", "warn"))
            else:
                log_q.append((f"UNKNOWN    {name:<22} {ms}", "unknown"))
        except asyncio.TimeoutError:
            log_q.append((f"TIMEOUT    {name:<22}", "timeout"))
        except (aiohttp.ClientConnectorError, aiohttp.ServerConnectionError):
            log_q.append((f"CONN ERR   {name:<22}", "timeout"))
        except Exception as e:
            log_q.append((f"ERROR      {name}  {e}", "err"))

    def _alert(self, name):
        cfg = self.cfg
        if cfg.get("snipe_mode", False): self.on_snipe(name)
        self.on_found(name)
        if pyperclip:
            try: pyperclip.copy(name)
            except: pass
        if cfg.get("sound_alert"):
            try: import winsound; winsound.Beep(1100, 150)
            except: pass
        if cfg.get("webhook_enabled") and cfg.get("webhook_url") and _requests:
            def _send_hook(url=cfg["webhook_url"], msg=cfg.get("webhook_template","**AVAILABLE** `{name}`").format(name=name)):
                try: _requests.post(url, json={"content": msg}, timeout=4)
                except: pass
            threading.Thread(target=_send_hook, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════════════════
# TOGGLE
# ═══════════════════════════════════════════════════════════════════════════════
class Toggle(tk.Canvas):
    TW, TH, KR = 44, 22, 8
    def __init__(self, parent, var, color=None, bg=None, **kw):
        self._bg = bg or C["panel"]
        super().__init__(parent, width=self.TW, height=self.TH, bg=self._bg,
                         highlightthickness=0, cursor="hand2", **kw)
        self._var, self._col = var, color or C["accent"]
        self._x = float(self.TW - self.KR - 3) if var.get() else float(self.KR + 3)
        self._anim = False
        self.bind("<Button-1>", lambda _: self._toggle()); self._draw()

    def _toggle(self):
        self._var.set(not self._var.get())
        if not self._anim: self._anim = True; self._step()

    def _step(self):
        tgt = float(self.TW - self.KR - 3) if self._var.get() else float(self.KR + 3)
        diff = tgt - self._x
        if abs(diff) < 0.7: self._x = tgt; self._anim = False; self._draw(); return
        self._x += diff * 0.26; self._draw(); self.after(14, self._step)

    def _draw(self):
        self.delete("all")
        on = self._var.get(); col = self._col if on else C["dim"]
        x, h, w, r, pad = self._x, self.TH, self.TW, self.KR, 3
        tf = C["border"] if on else C["entry_bg"]
        self.create_oval(pad, pad, h-pad, h-pad, fill=tf, outline=col, width=1)
        self.create_oval(w-h+pad, pad, w-pad, h-pad, fill=tf, outline=col, width=1)
        self.create_rectangle(h//2, pad, w-h//2, h-pad, fill=tf, outline=tf)
        self.create_line(h//2, pad, w-h//2, pad, fill=col, width=1)
        self.create_line(h//2, h-pad, w-h//2, h-pad, fill=col, width=1)
        self.create_oval(x-r, (h-r*2)//2, x+r, (h-r*2)//2+r*2, fill=col, outline="")

    def theme_update(self, bg=None, col=None):
        if bg: self._bg = bg; self.configure(bg=bg)
        if col: self._col = col
        self._draw()

# ═══════════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
def _fr(p, **kw):
    if "bg" not in kw: kw["bg"] = C["bg"]
    if "highlightthickness" not in kw: kw["highlightthickness"] = 0
    if "bd" not in kw: kw["bd"] = 0
    return tk.Frame(p, **kw)

def _sep(p, h=BD):
    return tk.Frame(p, bg=C["border"], height=h, bd=0, highlightthickness=0)

def _lbl(p, text, font=FONT_LBL, fg=None, bg=None, **kw):
    return tk.Label(p, text=text, bg=bg or C["panel"], fg=fg or C["text"],
                    font=font, bd=0, highlightthickness=0, **kw)

def _entry(p, **kw):
    return tk.Entry(p, bg=C["entry_bg"], fg=C["text"], insertbackground=C["accent"],
                    relief="flat", font=FONT_BASE, bd=4,
                    highlightthickness=BD, highlightbackground=C["border_hi"],
                    highlightcolor=C["accent"], **kw)

def _textw(p, **kw):
    return tk.Text(p, bg=C["entry_bg"], fg=C["text"], insertbackground=C["accent"],
                   relief="flat", font=FONT_BASE, bd=4,
                   highlightthickness=BD, highlightbackground=C["border_hi"],
                   highlightcolor=C["accent"],
                   selectbackground=C["border_hi"], selectforeground=C["text"], **kw)

def _btn(p, text, cmd, fg=None, bg=None, font=FONT_S, **kw):
    bg_ = bg or C["border"]
    fg_ = fg or C["text"]
    b = tk.Button(p, text=text, command=cmd, bg=bg_, fg=fg_,
                  activebackground=C["border_hi"], activeforeground=fg_,
                  relief="flat", font=font, cursor="hand2",
                  bd=0, highlightthickness=0, padx=10, pady=2, **kw)
    return b

def _abtn(p, text, cmd, col=None, bg=None, font=FONT_BOLD, **kw):
    c = col or C["accent"]
    # Use a darker/lighter variant of the color as background for contrast
    bg_ = bg or C["panel"]
    b = tk.Button(p, text=text.upper(), command=cmd, bg=bg_, fg=c,
                  activebackground=C["border_hi"], activeforeground="#ffffff",
                  relief="flat", font=font, cursor="hand2",
                  bd=0, highlightthickness=0, padx=14, pady=5, **kw)
    return b

def _section(p, title):
    wrap = _fr(p, bg=C["bg"]); wrap.pack(fill="x", pady=1)
    box = tk.Frame(wrap, bg=C["panel"], bd=0,
                   highlightthickness=BD, highlightbackground=C["border"])
    box.pack(fill="x")
    hdr = tk.Frame(box, bg=C["border"], bd=0, highlightthickness=0, height=22)
    hdr.pack(fill="x"); hdr.pack_propagate(False)
    _lbl(hdr, f"  {title.upper()}", font=FONT_XS, fg=C["dim"], bg=C["border"]).pack(side="left", pady=2)
    body = tk.Frame(box, bg=C["panel"], bd=0, highlightthickness=0, padx=6, pady=4)
    body.pack(fill="x")
    return body

def _toggle_row(p, label, var, color):
    row = tk.Frame(p, bg=C["panel"], cursor="hand2", bd=0, highlightthickness=0)
    row.pack(fill="x", pady=1)
    _lbl(row, label, font=FONT_LBL, fg=C["text"], bg=C["panel"]).pack(side="left", fill="x", expand=True)
    tog = Toggle(row, var, color, bg=C["panel"]); tog.pack(side="right")
    row.bind("<Button-1>", lambda _: var.set(not var.get()))
    return tog

# ═══════════════════════════════════════════════════════════════════════════════
# THEME DIALOG
# ═══════════════════════════════════════════════════════════════════════════════
class ThemeDialog(tk.Toplevel):
    def __init__(self, master, on_apply):
        super().__init__(master)
        self._master, self._on_apply = master, on_apply
        self.title("Theme"); self.configure(bg=C["bg"])
        self.resizable(False, False); self.grab_set()
        W, H = 480, 340
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}"); self.attributes("-topmost", True)

        tb = tk.Frame(self, bg=C["border"], height=28, bd=0); tb.pack(fill="x"); tb.pack_propagate(False)
        _lbl(tb, "  Theme", font=FONT_BOLD, fg=C["accent"], bg=C["border"]).pack(side="left", padx=6)
        tk.Button(tb, text="X", bg=C["border"], fg=C["dim"], relief="flat", bd=0,
                  font=FONT_BASE, cursor="hand2", command=self.destroy,
                  activebackground=C["red"], activeforeground="#fff").pack(side="right", padx=4, pady=2)

        body = tk.Frame(self, bg=C["bg"], bd=0, padx=12, pady=8); body.pack(fill="both", expand=True)

        grid = tk.Frame(body, bg=C["bg"], bd=0); grid.pack(fill="x")
        for ci in range(4): grid.columnconfigure(ci, weight=1)

        self._op = tk.DoubleVar(value=float(master.cfg.get("opacity", 1.0)))
        sel_name = master.cfg.get("preset", "Purple")
        
        for i, (name, p) in enumerate(PRESETS.items()):
            ri, ci = i // 4, i % 4
            pc = _build_palette(p["hue"], p["sat"])
            is_sel = (name == sel_name)
            card = tk.Frame(grid, bg=pc["panel"], cursor="hand2", bd=0,
                            highlightthickness=BD, highlightbackground=pc["accent"] if is_sel else pc["border"])
            card.grid(row=ri, column=ci, padx=3, pady=3, sticky="nsew")
            tk.Frame(card, bg=pc["accent"], height=BD, bd=0).pack(fill="x")
            _lbl(card, name, font=("Consolas", 9, "bold"), fg=pc["accent"], bg=pc["panel"]).pack(pady=(2, 0))
            dots = tk.Frame(card, bg=pc["panel"], bd=0); dots.pack(pady=(1, 3))
            for dc in ["accent", "green", "red", "yellow"]:
                _lbl(dots, "o", font=FONT_TINY, fg=pc[dc], bg=pc["panel"]).pack(side="left", padx=1)
            def _sel(n=name, pp=p):
                self._on_apply(pp["hue"], pp["sat"], self._op.get(), n); self.destroy()
            for w in (card,):
                w.bind("<Button-1>", lambda _, fn=_sel: fn())
                for c in w.winfo_children():
                    c.bind("<Button-1>", lambda _, fn=_sel: fn())

        _sep(body).pack(fill="x", pady=6)

        self._hue = tk.IntVar(value=int(master.cfg.get("theme_hue", 270)))
        self._sat = tk.DoubleVar(value=float(master.cfg.get("theme_sat", 0.85)))
        
        sf = tk.Frame(body, bg=C["bg"], bd=0); sf.pack(fill="x")
        for lt, var, lo, hi in [("Hue", self._hue, 0, 359), ("Sat", self._sat, 0.1, 1.0),
                                 ("Opacity", self._op, 0.4, 1.0)]:
            r = tk.Frame(sf, bg=C["bg"], bd=0); r.pack(fill="x", pady=1)
            _lbl(r, lt, font=FONT_S, fg=C["text"], bg=C["bg"]).pack(side="left", padx=(0, 6))
            tk.Scale(sf, from_=lo, to=hi, orient="horizontal", variable=var,
                     resolution=1 if var == self._hue else 0.01, showvalue=False,
                     bg=C["bg"], fg=C["dim"], troughcolor=C["border"],
                     activebackground=C["accent"], bd=0, highlightthickness=0,
                     sliderlength=12, command=lambda v: self._preview()).pack(fill="x")

        btns = tk.Frame(body, bg=C["bg"], bd=0); btns.pack(fill="x", pady=(4, 0))
        _abtn(btns, "Apply", self._apply_custom, col=C["accent"], bg=C["border"], font=FONT_S).pack(side="right")

    def _preview(self, *_):
        global C
        C = _build_palette(self._hue.get(), self._sat.get())
        try: self._master.attributes("-alpha", self._op.get())
        except: pass

    def _apply_custom(self):
        self._on_apply(self._hue.get(), self._sat.get(), self._op.get(), "Custom"); self.destroy()

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ═══════════════════════════════════════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.cfg, self.engine, self.found_list = cfg_load(), None, []
        self._log_buf, self._log_lock = [], threading.Lock()
        self._log_filter = self.cfg.get("log_filter", "all")

        global C
        C = _build_palette(self.cfg.get("theme_hue", 270), self.cfg.get("theme_sat", 0.92))
        self.title("Meta Horizon Username Checker")
        self.geometry("1100x640"); self.minsize(900, 540)
        self.configure(bg=C["bg"]); self.option_add("*tearOff", False)
        self._style()
        self._build()
        self._refresh_lists(); self._load_cfg(); self._refresh_creds_list()
        self._flush_log()
        self.after(500, self._reveal)
        # Start pool immediately at launch so tokens are warm before checker starts.
        # Runs in a background thread so the UI isn't blocked during initial token fetch.
        self.after(800, lambda: threading.Thread(
            target=lambda: SNIPER_POOL.start(on_log=self._push_log),
            daemon=True, name="sniper-pool-init").start())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        if self.engine: self.engine.stop()
        self.destroy()

    def _reveal(self):
        self.attributes("-alpha", float(self.cfg.get("opacity", 1.0)))
        self.deiconify(); self.lift()

    def _style(self):
        s = ttk.Style(self); s.theme_use("clam")
        s.configure("TCombobox", fieldbackground=C["entry_bg"], background=C["entry_bg"],
                    foreground=C["text"], selectbackground=C["border"],
                    selectforeground=C["text"], arrowcolor=C["dim"])
        s.map("TCombobox", fieldbackground=[("readonly", C["entry_bg"])],
              background=[("readonly", C["entry_bg"])])
        s.configure("TSpinbox", fieldbackground=C["entry_bg"], background=C["entry_bg"],
                    foreground=C["text"], arrowcolor=C["dim"])
        s.configure("Treeview", background=C["entry_bg"], foreground=C["text"],
                    fieldbackground=C["entry_bg"], borderwidth=0, rowheight=26)
        s.configure("Treeview.Heading", background=C["border"], foreground=C["dim"],
                     relief="flat", borderwidth=0)
        s.map("Treeview", background=[("selected", C["border_hi"])],
              foreground=[("selected", C["text"])])
        s.map("Treeview.Heading", background=[("active", C["border_hi"])])

    def _build(self):
        # -- Header (simple, no clutter) --
        h = _fr(self, bg=C["bg"]); h.pack(fill="x", padx=8, pady=(4, 0))

        _lbl(h, "META HORIZON", font=FONT_H1, fg=C["accent"], bg=C["bg"]).pack(side="left")
        _lbl(h, "  USERNAME CHECKER", font=FONT_H2, fg=C["dim"], bg=C["bg"]).pack(side="left")
        
        self._sv_preset = tk.StringVar(value=self.cfg.get("preset", "Purple"))
        _lbl(h, "", font=FONT_S, fg=C["dim"], bg=C["bg"], textvariable=self._sv_preset).pack(side="right", padx=4)
        _btn(h, "Theme", self._open_theme, fg=C["dim"], bg=C["border"]).pack(side="right")

        self._sv_status = tk.StringVar(value="idle")
        _lbl(h, "", font=FONT_S, fg=C["dim"], bg=C["bg"], textvariable=self._sv_status).pack(side="right", padx=6)

        _sep(self, h=BD).pack(fill="x")

        # -- Body --
        body = _fr(self, bg=C["bg"]); body.pack(fill="both", expand=True, padx=6, pady=(2, 0))


        # Sidebar
        SB_W = 420
        sb = tk.Frame(body, bg=C["bg"], bd=0, width=SB_W)
        sb.pack(side="left", fill="y"); sb.pack_propagate(False)


        lc = tk.Canvas(sb, bg=C["bg"], highlightthickness=0, bd=0)
        scr = ttk.Scrollbar(sb, orient="vertical", command=lc.yview)
        lc.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); lc.pack(side="left", fill="both", expand=True)
        self._sb_lc = lc
        
        left = _fr(lc, bg=C["bg"])
        lc.create_window((0, 0), window=left, anchor="nw", width=SB_W - 18)
        left.bind("<Configure>", lambda e: lc.configure(scrollregion=lc.bbox("all")))

        # Quick Check
        qcs = _section(left, "QUICK CHECK")
        qc_row = _fr(qcs, bg=C["panel"]); qc_row.pack(fill="x")
        self._quick_check_var = tk.StringVar()
        qc_entry = _entry(qc_row); qc_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        qc_entry.configure(textvariable=self._quick_check_var)
        qc_entry.bind("<Return>", lambda _: self._quick_check())
        _abtn(qc_row, "CHECK", self._quick_check, col=C["green"], bg=C["panel"], font=FONT_S).pack(side="right")

        # List
        ls = _section(left, "LIST")
        self._list_var = tk.StringVar()
        self._list_cb = ttk.Combobox(ls, textvariable=self._list_var, state="readonly", font=FONT_BASE)
        self._list_cb.pack(fill="x", pady=(1, 0))
        self._list_cb.bind("<<ComboboxSelected>>", self._on_list_sel)
        self._list_info = tk.StringVar(value="no list")
        _lbl(ls, "", font=FONT_XS, fg=C["dim"], bg=C["panel"], textvariable=self._list_info).pack(anchor="w")
        btn_row = _fr(ls, bg=C["panel"]); btn_row.pack(fill="x", pady=(2, 0))
        _btn(btn_row, "Refresh", self._refresh_lists).pack(side="right")
        _btn(btn_row, "Dedup", self._dedup_list, fg=C["yellow"]).pack(side="right", padx=2)

        # Accounts
        acs = _section(left, "ACCOUNTS")
        add_row = _fr(acs, bg=C["panel"]); add_row.pack(fill="x")
        _abtn(add_row, "+ ADD ACCOUNT", self._open_add_account, col=C["green"], bg=C["panel"],
              font=FONT_S).pack(fill="x")

        tv_frame = tk.Frame(acs, bg=C["panel"], bd=0); tv_frame.pack(fill="x", pady=(2, 0))
        self._creds_tree = ttk.Treeview(tv_frame, columns=("username", "pid", "status"),
                                         show="headings", height=10)
        self._creds_tree.heading("username", text="User"); self._creds_tree.heading("pid", text="Profile ID")
        self._creds_tree.heading("status", text="Status")
        self._creds_tree.column("username", width=80); self._creds_tree.column("pid", width=120)
        self._creds_tree.column("status", width=90)
        self._creds_tree.pack(fill="x")
        self._creds_tree.bind("<Double-1>", lambda _: self._cred_double_click())

        ctrl_row = _fr(acs, bg=C["panel"]); ctrl_row.pack(fill="x", pady=(2, 0))
        _btn(ctrl_row, "Remove", self._remove_cred, fg=C["red"]).pack(side="right")
        _btn(ctrl_row, "Lock", self._toggle_lock, fg=C["yellow"]).pack(side="right", padx=2)
        _btn(ctrl_row, "Sniper", self._toggle_sniper, fg=C["accent2"]).pack(side="right", padx=2)
        _btn(ctrl_row, "Unuse", self._unuse_cred, fg=C["accent"]).pack(side="right", padx=2)
        
        ctrl_row2 = _fr(acs, bg=C["panel"]); ctrl_row2.pack(fill="x", pady=(2, 0))
        _btn(ctrl_row2, "Test", self._test_cred, fg=C["accent"]).pack(side="right")
        _btn(ctrl_row2, "Refresh", self._refresh_usernames, fg=C["accent2"]).pack(side="right", padx=2)
        self._creds_info = tk.StringVar(value="0 accounts")
        _lbl(acs, "", font=FONT_XS, fg=C["dim"], bg=C["panel"], textvariable=self._creds_info).pack(anchor="w", pady=(1, 0))

        # Settings
        ss = _section(left, "SETTINGS")
        self._v_loop  = tk.BooleanVar(value=True)
        self._v_snipe = tk.BooleanVar(value=False)
        self._v_copy  = tk.BooleanVar(value=True)
        self._v_sound = tk.BooleanVar(value=True)
        _toggle_row(ss, "Loop",         self._v_loop,  C["accent"])
        _toggle_row(ss, "Snipe (GQL)",  self._v_snipe, C["red"])
        _toggle_row(ss, "Clipboard",    self._v_copy,  C["accent2"])
        _toggle_row(ss, "Sound",        self._v_sound, C["yellow"])
        _sep(ss).pack(fill="x", pady=3)

        lf = _fr(ss, bg=C["panel"]); lf.pack(fill="x", pady=(0, 3))
        _lbl(lf, "Log:", font=FONT_XS, fg=C["dim"], bg=C["panel"]).pack(side="left")
        self._v_lf = tk.StringVar(value=self.cfg.get("log_filter", "all"))
        for val, txt in [("all", "All"), ("no-taken", "No taken"), ("available", "Found only")]:
            tk.Radiobutton(lf, text=txt, variable=self._v_lf, value=val,
                           bg=C["panel"], fg=C["dim"], activebackground=C["panel"],
                           activeforeground=C["accent"], selectcolor=C["entry_bg"],
                           font=FONT_XS, command=self._apply_lf).pack(side="left", padx=2)

        _sep(ss).pack(fill="x", pady=3)
        nr = _fr(ss, bg=C["panel"]); nr.pack(fill="x")
        for i, (lt, attr, lo, hi, dv) in enumerate([
            ("Concurrency", "_v_conc", 1, 500, 30),
            ("Cycle delay", "_v_delay", 0, 3600, 0),
            ("Timeout s",   "_v_tc", 0.5, 30, 8.0),
            ("Connect s",   "_v_cc", 0.1, 10, 2.0),
        ]):
            c2, r2 = (i % 2) * 2, i // 2
            _lbl(nr, lt, font=FONT_XS, fg=C["dim"], bg=C["panel"]).grid(row=r2*2, column=c2, sticky="w")
            v = tk.DoubleVar(value=dv) if isinstance(dv, float) else tk.IntVar(value=dv)
            setattr(self, attr, v)
            ttk.Spinbox(nr, from_=lo, to=hi, textvariable=v, width=7).grid(row=r2*2+1, column=c2, sticky="w", padx=(0, 8))
        nr.columnconfigure(0, weight=1); nr.columnconfigure(2, weight=1)

        # Webhook
        ws = _section(left, "WEBHOOK")
        self._v_hook = tk.BooleanVar(value=False)
        _toggle_row(ws, "Enable", self._v_hook, C["accent2"])
        _lbl(ws, "URL", font=FONT_XS, fg=C["dim"], bg=C["panel"]).pack(anchor="w")
        self._hook_url = _entry(ws); self._hook_url.pack(fill="x", pady=(1, 2))
        self._hook_tmpl = _textw(ws, height=2); self._hook_tmpl.pack(fill="x")
        _btn(ws, "Test", self._test_webhook, fg=C["accent2"]).pack(anchor="e", pady=(1, 0))

        # Controls
        ctrl = _fr(left, bg=C["bg"]); ctrl.pack(fill="x", pady=3)
        self._btn_start = _abtn(ctrl, "START", self._start, col=C["green"], bg=C["panel"])
        self._btn_start.pack(side="left", fill="x", expand=True, padx=(0, 2))

        self._btn_pause = _abtn(ctrl, "PAUSE", self._pause, col=C["yellow"], bg=C["panel"])
        self._btn_pause.configure(state="disabled", fg=C["dim"])
        self._btn_pause.pack(side="left", fill="x", expand=True, padx=(0, 2))

        self._btn_stop = _abtn(ctrl, "STOP", self._stop, col=C["red"], bg=C["panel"])
        self._btn_stop.configure(state="disabled", fg=C["dim"])
        self._btn_stop.pack(side="left", fill="x", expand=True)

        # Swap Fire — hammers the claim without waiting for checker detection
        sf = _section(left, "⚡ SWAP FIRE")
        _lbl(sf, "Target name to claim:", font=FONT_XS, fg=C["dim"], bg=C["panel"]).pack(anchor="w")
        self._swap_name = _entry(sf); self._swap_name.pack(fill="x", pady=(1, 4))
        sf_row = _fr(sf, bg=C["panel"]); sf_row.pack(fill="x")
        _lbl(sf_row, "Window (s):", font=FONT_XS, fg=C["dim"], bg=C["panel"]).pack(side="left")
        self._swap_dur = tk.DoubleVar(value=5.0)
        ttk.Spinbox(sf_row, from_=1, to=30, textvariable=self._swap_dur,
                    width=5, increment=0.5).pack(side="left", padx=(2, 8))
        _lbl(sf_row, "Every (ms):", font=FONT_XS, fg=C["dim"], bg=C["panel"]).pack(side="left")
        self._swap_interval = tk.IntVar(value=100)
        ttk.Spinbox(sf_row, from_=50, to=1000, textvariable=self._swap_interval,
                    width=5, increment=50).pack(side="left", padx=(2, 0))
        self._btn_swap = _abtn(sf, "⚡ FIRE NOW", self._do_swap_fire, col=C["yellow"], bg=C["panel"])
        self._btn_swap.pack(fill="x", pady=(4, 0))

        # Save config below the control buttons
        save_ctrl = _fr(left, bg=C["bg"]); save_ctrl.pack(fill="x", pady=(4, 0))
        _abtn(save_ctrl, "SAVE CONFIG", self._save_cfg, col=C["text"], bg=C["border"]).pack(fill="x")

        # Separator + Right panel
        tk.Frame(body, bg=C["dim2"], width=BD, bd=0).pack(side="left", fill="y", pady=4)
        right = _fr(body, bg=C["bg"]); right.pack(side="left", fill="both", expand=True, padx=(4, 0))

        # Log
        lh = _fr(right, bg=C["bg"]); lh.pack(fill="x")
        _lbl(lh, "LOG", font=("Consolas", 9, "bold"), fg=C["dim"], bg=C["bg"]).pack(side="left")
        _btn(lh, "Clear", self._clear_log, bg=C["bg"]).pack(side="right")
        self._log = tk.Text(right, bg=C["panel"], fg=C["text"], font=FONT_BASE,
                            relief="flat", bd=4, state="disabled",
                            highlightthickness=BD, highlightbackground=C["border"],
                            selectbackground=C["border_hi"], selectforeground=C["text"], wrap="none")
        self._log.pack(fill="both", expand=True, pady=(2, 2))
        self._log.tag_config("available", foreground=C["green"])
        self._log.tag_config("taken",     foreground=C["taken"])
        self._log.tag_config("timeout",   foreground=C["dim2"])
        self._log.tag_config("warn",      foreground=C["yellow"])
        self._log.tag_config("err",       foreground=C["red"])
        self._log.tag_config("info",      foreground=C["dim"])
        self._log.tag_config("snipe",     foreground=C["accent2"])

        # Found
        fh = _fr(right, bg=C["bg"]); fh.pack(fill="x")
        _lbl(fh, "FOUND", font=("Consolas", 9, "bold"), fg=C["green"], bg=C["bg"]).pack(side="left")
        self._found_cnt = tk.StringVar(value="0")
        _lbl(fh, "", font=FONT_S, fg=C["dim"], bg=C["bg"], textvariable=self._found_cnt).pack(side="left", padx=4)
        _btn(fh, "Save", self._save_found, bg=C["bg"]).pack(side="right")
        _btn(fh, "Clear", self._clear_found, bg=C["bg"]).pack(side="right", padx=2)
        self._found_box = tk.Text(right, bg=C["panel"], fg=C["green"], font=FONT_BOLD,
                                  relief="flat", bd=4, height=5, state="disabled",
                                  highlightthickness=BD, highlightbackground=C["border"],
                                  selectbackground=C["border_hi"], selectforeground=C["text"])
        self._found_box.pack(fill="x", pady=(2, 0))
        self._found_box.bind("<Double-1>", self._found_double_click)

        # -- Stats bar (bottom, spreads full width) --
        stat_bar = _fr(self, bg=C["bg"]); stat_bar.pack(fill="x", side="bottom", padx=6, pady=(2, 3))
        cards = _fr(stat_bar, bg=C["bg"]); cards.pack(fill="x", expand=True)
        self._sc_cycle  = self._stat(cards, "CYCLE", C["dim"])
        self._sc_prog   = self._stat(cards, "DONE",  C["dim"])
        self._sc_found  = self._stat(cards, "FOUND", C["green"])
        self._sc_speed  = self._stat(cards, "ms/name", C["accent"])
        self._sc_cps    = self._stat(cards, "names/s", C["accent2"])
        self._sc_sniper = self._stat(cards, "SNIPER", C["yellow"])

    def _stat(self, p, label, color=None):
        c = color or C["accent"]
        card = tk.Frame(p, bg=C["panel"], bd=0, highlightthickness=BD, highlightbackground=C["border"])
        card.pack(side="left", fill="x", expand=True, padx=1)

        _lbl(card, label, font=FONT_TINY, fg=C["dim"], bg=C["panel"]).pack(padx=6, pady=(2, 0))
        v = tk.StringVar(value="-")
        _lbl(card, "", font=FONT_STAT, fg=c, bg=C["panel"], textvariable=v).pack(padx=6, pady=(0, 2))
        return v

    def _test_webhook(self):
        url = self._hook_url.get().strip()
        if not url: messagebox.showwarning("No URL", "Enter a webhook URL first."); return
        if not _requests: messagebox.showerror("Missing", "requests not installed."); return
        try:
            tmpl = self._hook_tmpl.get("1.0", "end").strip() or "**AVAILABLE** `{name}`"
            r = _requests.post(url, json={"content": tmpl.format(name="TestUser")}, timeout=5)
            if r.status_code in (200, 204): messagebox.showinfo("OK", f"Sent ({r.status_code})")
            else: messagebox.showwarning("Failed", f"{r.status_code}: {r.text[:200]}")
        except Exception as e: messagebox.showerror("Error", str(e))

    # ── List ──────────────────────────────────────────────────────────────────
    def _refresh_lists(self):
        lists_ensure(); files = lists_all()
        self._list_cb["values"] = files
        if files:
            prev = os.path.basename(self.cfg.get("selected_list", ""))
            self._list_var.set(prev if prev in files else files[0])
            self._on_list_sel()
        else: self._list_info.set("no .txt files in lists/")

    def _on_list_sel(self, *_):
        fn = self._list_var.get()
        if not fn: return
        fp = os.path.join(LISTS_DIR, fn)
        n = names_load(fp); self.cfg["selected_list"] = fp
        self._list_info.set(f"{len(n)} names")

    def _dedup_list(self):
        fn = self._list_var.get()
        if not fn: return
        fp = os.path.join(LISTS_DIR, fn)
        try:
            with open(fp, encoding="utf-8") as f: raw = [l.strip() for l in f if l.strip()]
            seen, dd = set(), []
            for n in raw:
                if n.lower() not in seen: seen.add(n.lower()); dd.append(n)
            removed = len(raw) - len(dd)
            with open(fp, "w", encoding="utf-8") as f: f.write("\n".join(dd) + "\n")
            self._list_info.set(f"{len(dd)} names")
            self._push_log(f"dedup: removed {removed}, kept {len(dd)}", "info")
        except Exception as e: self._push_log(f"dedup failed: {e}", "err")

    # ── Accounts ──────────────────────────────────────────────────────────────
    def _refresh_creds_list(self):
        self._creds_tree.delete(*self._creds_tree.get_children())
        creds = creds_load(); ready = 0
        for c in creds:
            pid = c.get('PROFILE_ID', '')[:16]
            uname = c.get('username') or '-'
            sniped, locked = c.get('sniped'), c.get('locked', False)
            rl = c.get('rate_limited', False)
            enabled = c.get('sniper_enabled', True)
            tags = []
            if locked: tags.append("LOCK")
            if rl: tags.append("RL")
            if sniped and sniped != 'null': tags.append("USED")
            if not enabled: tags.append("OFF")
            status = " ".join(tags) if tags else "ready"
            item = self._creds_tree.insert("", tk.END, values=(uname, pid, status))
            if not locked and not rl and not (sniped and sniped != 'null') and enabled:
                self._creds_tree.tag_configure("ready", foreground=C["green"])
                self._creds_tree.item(item, tags=("ready",)); ready += 1
            elif locked or (sniped and sniped != 'null'):
                self._creds_tree.tag_configure("dim", foreground=C["dim"])
                self._creds_tree.item(item, tags=("dim",))
            else:
                self._creds_tree.tag_configure("warn", foreground=C["yellow"])
                self._creds_tree.item(item, tags=("warn",))
        self._creds_info.set(f"{len(creds)} accounts, {ready} ready")
        self._sc_sniper.set(ready)

    def _cred_double_click(self):
        sel = self._creds_tree.selection()
        if not sel: return
        values = self._creds_tree.item(sel[0], "values")
        if values:
            try: self.clipboard_clear(); self.clipboard_append(values[0]); self.update()
            except: pass

    def _open_add_account(self):
        dialog = tk.Toplevel(self)
        dialog.title("Add Account")
        dialog.configure(bg=C["bg"])
        dialog.resizable(False, False)
        dialog.grab_set()
        W, H = 420, 200
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        dialog.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")
        dialog.attributes("-topmost", True)
        
        body = tk.Frame(dialog, bg=C["bg"], padx=14, pady=12)
        body.pack(fill="both", expand=True)
        
        _lbl(body, "Profile ID:", font=FONT_S, fg=C["dim"], bg=C["bg"]).pack(anchor="w", pady=(0, 2))
        pid_entry = _entry(body); pid_entry.pack(fill="x", pady=(0, 8))
        
        _lbl(body, "FS Token:", font=FONT_S, fg=C["dim"], bg=C["bg"]).pack(anchor="w", pady=(0, 2))
        fs_entry = _entry(body); fs_entry.pack(fill="x", pady=(0, 8))
        
        btn_row = tk.Frame(body, bg=C["bg"]); btn_row.pack(fill="x")
        
        def _do_add():
            pid = pid_entry.get().strip()
            fs = fs_entry.get().strip()
            if not pid or not fs: return
            
            username = None
            if _requests:
                try:
                    s = _requests.Session()
                    s.cookies.update({'fs': fs, 'locale': 'en_US'})
                    s.headers.update({'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15'})
                    r = s.get(f'https://horizon.meta.com/profile/{pid}/?locale=en_US', timeout=15)
                    match = re.search(r'"display_name_ignore_block"\s*:\s*"([^"]+)"', r.text)
                    if match:
                        username = match.group(1)
                except:
                    pass
            
            creds_add(pid, fs, username or "")
            self._refresh_creds_list()
            if username:
                self._push_log(f"added account: {username}", "info")
            else:
                self._push_log("added account (username unknown)", "info")
            dialog.destroy()
        
        _abtn(btn_row, "ADD", _do_add, col=C["green"], bg=C["panel"], font=FONT_S).pack(side="right", padx=2)
        _btn(btn_row, "Cancel", dialog.destroy, bg=C["border"]).pack(side="right")
        
        pid_entry.focus()

    def _add_cred(self):
        pid, fs = self._add_pid.get().strip(), self._add_fs.get().strip()
        if not pid or not fs: return
        creds_add(pid, fs)
        self._add_pid.delete(0, tk.END); self._add_fs.delete(0, tk.END)
        self._refresh_creds_list(); self._push_log("added account", "info")

    def _remove_cred(self):
        sel = self._creds_tree.selection()
        if not sel: return
        idx = self._creds_tree.index(sel[0])
        creds_remove(idx); self._refresh_creds_list()
        self._push_log("removed account", "info")

    def _toggle_lock(self):
        sel = self._creds_tree.selection()
        if not sel: return
        idx = self._creds_tree.index(sel[0])
        creds = creds_load()
        creds_update(idx, 'locked', not creds[idx].get('locked', False))
        self._refresh_creds_list()

    def _unuse_cred(self):
        sel = self._creds_tree.selection()
        if not sel: return
        idx = self._creds_tree.index(sel[0])
        creds_update(idx, 'sniped', None)
        creds_update(idx, 'locked', False)
        self._refresh_creds_list()
        self._push_log(f"account {idx+1} marked as unused", "info")

    def _toggle_sniper(self):
        sel = self._creds_tree.selection()
        if not sel: return
        idx = self._creds_tree.index(sel[0])
        creds = creds_load()
        creds_update(idx, 'sniper_enabled', not creds[idx].get('sniper_enabled', True))
        self._refresh_creds_list()

    def _quick_check(self):
        name = self._quick_check_var.get().strip()
        if not name: return
        self._quick_check_var.set("")
        self._push_log(f"checking '{name}' via GraphQL...", "info")
        
        def _run():
            creds = creds_load()
            if not creds:
                self._push_log("no accounts configured", "err")
                return
            
            # Find first usable account
            sniper = None
            for c in creds:
                if not c.get('rate_limited') and not c.get('locked'):
                    sniper = MetaUsernameSniper(c)
                    break
            if not sniper:
                sniper = MetaUsernameSniper(creds[0])
            
            # Use the check_token_valid method but with the actual username
            if not sniper.refresh_tokens():
                self._push_log("failed to refresh tokens", "err")
                return
            
            variables = json.dumps({
                "action_source": "EDIT", "identity_ids": [sniper.profile_id],
                "username": name, "included_app_validations": []})
            data = {'av': sniper.profile_id, 'fb_dtsg': sniper.tokens['fb_dtsg'],
                    'lsd': sniper.tokens['lsd'], '__rev': sniper.tokens['__rev'],
                    '__hsi': sniper.tokens['__hsi'], 'variables': variables,
                    'doc_id': '9751320178292067',
                    'fb_api_req_friendly_name': 'useFXIMUsernameValidatorBaseQuery',
                    'fb_api_caller_class': 'RelayModern', 'server_timestamps': 'true'}
            headers = {'x-fb-lsd': sniper.tokens['lsd'],
                       'x-fb-friendly-name': 'useFXIMUsernameValidatorBaseQuery',
                       'x-asbd-id': '359341', 'content-type': 'application/x-www-form-urlencoded',
                       'referer': sniper.page_url, 'origin': 'https://accountscenter.meta.com'}
            
            try:
                r = sniper.session.post(API_URL, headers=headers, data=data, timeout=15)
                result = r.json()
                status = result['data']['fx_identity_management']['validate_username_v4']['status_code']
                
                if status in ('SUCCESS', 'OK_GENERIC'):
                    self._push_log(f"'{name}' is AVAILABLE", "available")
                    self._on_found(name)
                    
                    snipe_on = self._v_snipe.get()
                    self._push_log(f"snipe_mode toggle: {snipe_on}", "info")
                    
                    if snipe_on:
                        self._push_log(f"sniping '{name}'...", "snipe")
                        snipe_claim(name, on_log=self._push_log)
                        self._refresh_creds_list()
                    else:
                        self._push_log("snipe_mode is OFF - enable it in settings", "warn")
                elif status == 'ERROR_COLLISION':
                    self._push_log(f"'{name}' is TAKEN", "taken")
                elif status == 'ERROR_INVALID':
                    self._push_log(f"'{name}' is INVALID", "warn")
                elif status == 'ERROR_RESERVED':
                    self._push_log(f"'{name}' is RESERVED", "warn")
                else:
                    self._push_log(f"'{name}': {status}", "warn")
            except Exception as e:
                self._push_log(f"check failed: {e}", "err")
        
        threading.Thread(target=_run, daemon=True).start()

    def _test_cred(self):
        sel = self._creds_tree.selection()
        if not sel: return
        idx = self._creds_tree.index(sel[0])
        self._push_log(f"testing account {idx+1}...", "info")
        def _run():
            ok, msg = snipe_test_account(idx)
            self._push_log(f"account {idx+1}: {'ok' if ok else msg}", "info" if ok else "warn")
            self._refresh_creds_list()
        threading.Thread(target=_run, daemon=True).start()

    def _refresh_usernames(self):
        if not _requests:
            self._push_log("requests not installed", "err")
            return
        self._push_log("refreshing usernames...", "info")
        def _run():
            updated = refresh_account_usernames(on_log=self._push_log)
            self._push_log(f"updated {updated} usernames", "info")
            self._refresh_creds_list()
        threading.Thread(target=_run, daemon=True).start()

    # ── Config ────────────────────────────────────────────────────────────────
    def _apply_lf(self): self._log_filter = self._v_lf.get()

    def _load_cfg(self):
        c = self.cfg
        self._v_loop.set(c.get("loop_mode", True)); self._v_snipe.set(c.get("snipe_mode", False))
        self._v_copy.set(c.get("copy_clipboard", True)); self._v_sound.set(c.get("sound_alert", True))
        self._v_hook.set(c.get("webhook_enabled", False))
        self._v_conc.set(c.get("concurrency", 30)); self._v_delay.set(c.get("cycle_delay", 0))
        self._v_tc.set(c.get("timeout_total", 8.0)); self._v_cc.set(c.get("timeout_connect", 2.0))
        self._v_lf.set(c.get("log_filter", "all"))
        self._sv_preset.set(c.get("preset", "Purple"))
        self._hook_url.delete(0, "end"); self._hook_url.insert(0, c.get("webhook_url", ""))
        self._hook_tmpl.delete("1.0", "end")
        self._hook_tmpl.insert("end", c.get("webhook_template",
            "**Available** `{name}`"))

    def _collect_cfg(self):
        self.cfg.update({
            "loop_mode": self._v_loop.get(), "snipe_mode": self._v_snipe.get(),
            "copy_clipboard": self._v_copy.get(), "sound_alert": self._v_sound.get(),
            "webhook_enabled": self._v_hook.get(), "webhook_url": self._hook_url.get().strip(),
            "webhook_template": self._hook_tmpl.get("1.0", "end").strip(),
            "concurrency": int(self._v_conc.get()), "cycle_delay": int(self._v_delay.get()),
            "timeout_total": float(self._v_tc.get()), "timeout_connect": float(self._v_cc.get()),
            "log_filter": self._v_lf.get(),
        })
        return self.cfg

    def _save_cfg(self):
        cfg_save(self._collect_cfg()); self._push_log("config saved", "info")

    # ── Swap Fire ─────────────────────────────────────────────────────────────
    def _do_swap_fire(self):
        name = self._swap_name.get().strip()
        if not name:
            self._push_log("swap: enter a target name first", "err"); return
        if not _requests:
            self._push_log("swap: requests library not installed", "err"); return
        dur = float(self._swap_dur.get())
        interval = max(0.05, float(self._swap_interval.get()) / 1000.0)
        # Arm the pool if it hasn't been started yet (checker might not be running)
        if not SNIPER_POOL._pool:
            SNIPER_POOL.start(on_log=self._push_log)
        self._btn_swap.configure(state="disabled", fg=C["dim"], cursor="arrow")
        def _re_enable():
            self.after(int(dur * 1000) + 500,
                       lambda: self._btn_swap.configure(
                           state="normal", fg=C["yellow"], cursor="hand2"))
        _re_enable()
        SNIPER_POOL.swap_fire(name, interval=interval, duration=dur,
                              on_log=self._push_log)
        self.after(int(dur * 1000) + 1500, self._refresh_creds_list)

    # ── Snipe ─────────────────────────────────────────────────────────────────
    def _do_snipe(self, name=""):
        if not name:
            self._push_log("snipe: no name provided", "err"); return
        if not _requests:
            self._push_log("snipe: requests not installed", "err"); return
        # fire() is non-blocking — submits to the live thread pool and returns instantly.
        # No disk reads, no thread creation overhead in the hot path.
        SNIPER_POOL.fire(name, on_log=self._push_log)
        # Refresh UI after a short delay to reflect any lock changes
        self.after(1500, self._refresh_creds_list)

    # ── Start / Pause / Stop ──────────────────────────────────────────────────
    def _start(self):
        if self.engine and self.engine.running:
            if self.engine.paused: self._resume_engine()
            return
        cfg = self._collect_cfg()
        if not cfg.get("selected_list") or not os.path.exists(cfg["selected_list"]):
            messagebox.showerror("No list", "Select a list first."); return
        self.found_list.clear()
        self._found_box.configure(state="normal"); self._found_box.delete("1.0", "end")
        self._found_box.configure(state="disabled")
        self._found_cnt.set("0"); self._sc_found.set(0); self._sc_prog.set("-")
        self.engine = Engine(cfg=cfg, on_log=self._push_log, on_status=self._set_status,
                             on_found=self._on_found, on_stats=self._on_stats, on_snipe=self._do_snipe)
        self.engine.start()
        self._btn_start.configure(state="disabled", fg=C["dim"], cursor="arrow")
        self._btn_pause.configure(state="normal", fg=C["yellow"], cursor="hand2", text="PAUSE")
        self._btn_stop.configure(state="normal", fg=C["red"], cursor="hand2")
        self._poll()

    def _pause(self):
        if not self.engine: return
        if self.engine.paused: self._resume_engine()
        else:
            self.engine.pause()
            self._btn_pause.configure(text="RESUME", fg=C["green"], cursor="hand2")
            self._btn_start.configure(state="disabled", fg=C["dim"], cursor="arrow")
            self._push_log("paused", "info")

    def _resume_engine(self):
        if not self.engine: return
        self.engine.resume()
        self._btn_pause.configure(text="PAUSE", fg=C["yellow"], cursor="hand2")
        self._btn_start.configure(state="disabled", fg=C["dim"], cursor="arrow")
        self._push_log("resumed", "info")

    def _stop(self):
        if self.engine: self.engine.stop()
        self._btn_start.configure(state="normal", fg=C["green"], cursor="hand2")
        self._btn_pause.configure(state="disabled", fg=C["dim"], cursor="arrow", text="PAUSE")
        self._btn_stop.configure(state="disabled", fg=C["dim"], cursor="arrow")

    def _poll(self):
        if self.engine and self.engine.running: self.after(500, self._poll)
        else:
            self._btn_start.configure(state="normal", fg=C["green"], cursor="hand2")
            self._btn_pause.configure(state="disabled", fg=C["dim"], cursor="arrow", text="PAUSE")
            self._btn_stop.configure(state="disabled", fg=C["dim"], cursor="arrow")

    # ── Log ───────────────────────────────────────────────────────────────────
    def _should_log(self, tag):
        f = self._log_filter
        if f == "all": return True
        if f == "available": return tag in ("available", "err", "warn", "info", "snipe")
        if f == "no-taken": return tag != "taken"
        return True

    def _push_log(self, msg, tag="info"):
        if not self._should_log(tag): return
        with self._log_lock:
            self._log_buf.append((f"{time.strftime('%H:%M:%S')}  {msg}\n", tag))

    def _flush_log(self):
        with self._log_lock: batch, self._log_buf = self._log_buf[:], []
        if batch:
            self._log.configure(state="normal")
            for line, tag in batch: self._log.insert("end", line, tag)
            self._log.see("end")
            n = int(self._log.index("end").split(".")[0])
            if n > 5200: self._log.delete("1.0", f"{n-5000}.0")
            self._log.configure(state="disabled")
        self.after(40, self._flush_log)

    def _clear_log(self):
        self._log.configure(state="normal"); self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _set_status(self, msg): self.after(0, lambda: self._sv_status.set(msg))

    def _on_stats(self, cycle, found, ms, cps=0.0, checked=0):
        def _do():
            self._sc_cycle.set(cycle); self._sc_found.set(found)
            if ms > 0: self._sc_speed.set(f"{ms:.1f}")
            if cps > 0: self._sc_cps.set(f"{cps:.0f}")
            if checked > 0: self._sc_prog.set(str(checked))
            self._found_cnt.set(str(found))
        self.after(0, _do)

    def _on_found(self, name):
        def _do():
            try: self.clipboard_clear(); self.clipboard_append(name); self.update()
            except: pass
            self.found_list.append(name)
            self._found_box.configure(state="normal")
            self._found_box.insert("end", name + "\n")
            self._found_box.see("end")
            self._found_box.configure(state="disabled")
            self._found_cnt.set(str(len(self.found_list))); self._sc_found.set(len(self.found_list))
        self.after(0, _do)

    def _found_double_click(self, event):
        idx = self._found_box.index(f"@{event.x},{event.y}")
        line_start = self._found_box.index(f"{idx} linestart")
        line_end = self._found_box.index(f"{idx} lineend")
        name = self._found_box.get(line_start, line_end).strip()
        if name:
            try: self.clipboard_clear(); self.clipboard_append(name); self.update()
            except: pass
            if pyperclip:
                try: pyperclip.copy(name)
                except: pass

    def _save_found(self):
        if not self.found_list: return
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="available.txt")
        if path:
            with open(path, "w", encoding="utf-8") as f: f.write("\n".join(self.found_list))

    def _clear_found(self):
        self.found_list.clear(); self._found_cnt.set("0"); self._sc_found.set(0); self._sc_prog.set("-")
        if self.engine: self.engine._found = 0; self.engine._cache.clear(); self.engine._checked_this_cycle = 0
        self._found_box.configure(state="normal"); self._found_box.delete("1.0", "end")
        self._found_box.configure(state="disabled")

    # ── Theme ─────────────────────────────────────────────────────────────────
    def _rebuild_ui(self):
        """Destroy all children and rebuild the entire UI with current palette."""
        for child in self.winfo_children():
            child.destroy()
        self._style()
        self._build()
        self._refresh_lists()
        self._refresh_creds_list()

    def _open_theme(self):
        def _apply(hue, sat, op, preset_name):
            global C
            C = _build_palette(hue, sat)
            self.cfg.update({"theme_hue": hue, "theme_sat": sat, "opacity": op, "preset": preset_name})
            cfg_save(self.cfg)
            self.attributes("-alpha", op)
            self._sv_preset.set(preset_name)
            self._rebuild_ui()
        ThemeDialog(self, _apply)

if __name__ == "__main__":
    lists_ensure()
    creds_ensure()
    App().mainloop()