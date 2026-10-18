"""Free public data access (stdlib only). Hyperliquid info API + OKX rubik stats. Read-only, no keys.
All requests are HTTPS-only to an allowlist of hosts, with timeouts, a response-size cap, no redirects,
and plain JSON parsing (never eval/pickle). Responses are shape-checked; bad data raises ValueError."""
import json, time, urllib.request, urllib.parse, urllib.error, ssl, math, re
import os
COIN_RE = re.compile(r"^[A-Z0-9]{1,15}$")
COINS = [c for c in (c.strip().upper() for c in os.environ.get("LIQMAP_COINS", "BTC,ETH,SOL").split(",")) if COIN_RE.match(c)] or ["BTC", "ETH", "SOL"]
UA = {"User-Agent": "liqmap-tracker/1.1", "Content-Type": "application/json", "Accept": "application/json"}
H = 3600000
API_HOSTS = {"api.hyperliquid.xyz", "www.okx.com"}
MAX_BYTES = 16 * 1024 * 1024
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): raise urllib.error.URLError("redirects are not followed")
_OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
def num(x):
    v = float(x)
    if not math.isfinite(v): raise ValueError("non-finite number")
    return v
def get(url, body=None, tries=3):
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname not in API_HOSTS: raise ValueError("refusing non-HTTPS or non-allowlisted URL")
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=UA)
            with _OPENER.open(req, timeout=20) as r: raw = r.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES: raise ValueError("response too large")
            return json.loads(raw)
        except Exception:
            if k == tries - 1: raise
            time.sleep(2 * (k + 1))
def hl(body): return get("https://api.hyperliquid.xyz/info", body)
def candles(coin, start_ms, interval="1h", end_ms=None):
    if not COIN_RE.match(str(coin)): raise ValueError("bad coin")
    req = {"coin": coin, "interval": interval, "startTime": int(start_ms)}
    if end_ms: req["endTime"] = int(end_ms)
    k = hl({"type": "candleSnapshot", "req": req})
    if not isinstance(k, list): raise ValueError("unexpected candle response shape")
    return [[int(num(x["t"])), num(x["o"]), num(x["h"]), num(x["l"]), num(x["c"]), num(x["v"])] for x in k if isinstance(x, dict)]
def ctx():
    r = hl({"type": "metaAndAssetCtxs"})
    if not (isinstance(r, list) and len(r) == 2 and isinstance(r[0], dict) and isinstance(r[0].get("universe"), list) and isinstance(r[1], list)):
        raise ValueError("unexpected meta response shape")
    out = {}
    for a, c in zip(r[0]["universe"], r[1]):
        if not (isinstance(a, dict) and isinstance(c, dict) and isinstance(a.get("name"), str)): continue
        try: out[a["name"]] = {"oi": num(c["openInterest"]), "funding": num(c["funding"]), "mark": num(c["markPx"])}
        except (KeyError, TypeError, ValueError): continue  # skip malformed entries instead of crashing
    return out
def funding_hist(coin, start_ms):
    if not COIN_RE.match(str(coin)): raise ValueError("bad coin")
    out = {}; s = int(start_ms)
    for _ in range(1000):  # hard page limit
        r = hl({"type": "fundingHistory", "coin": coin, "startTime": s})
        if not isinstance(r, list): raise ValueError("unexpected funding response shape")
        r = [x for x in r if isinstance(x, dict) and "time" in x and "fundingRate" in x]
        if not r: break
        for x in r: out[int(num(x["time"])) // H * H] = num(x["fundingRate"])
        lt = int(num(r[-1]["time"]))
        if len(r) < 500 or lt + 1 <= s: break
        s = lt + 1; time.sleep(0.3)
    return out
def okx_series(coin, kind, hours=100):
    """kind: 'oi' -> [[t, usd]], 'ls' -> [[t, ratio]]; paginates with end= to go back `hours`."""
    if not COIN_RE.match(str(coin)): raise ValueError("bad coin")
    path = {"oi": "open-interest-history", "ls": "long-short-account-ratio-contract"}[kind]
    col = 3 if kind == "oi" else 1
    out = {}; end = None
    for _ in range(200):  # hard page limit
        if len(out) >= hours: break
        url = f"https://www.okx.com/api/v5/rubik/stat/contracts/{path}?instId={coin}-USDT-SWAP&period=1H&limit=100" + (f"&end={end}" if end else "")
        time.sleep(0.5); r = get(url)
        if not isinstance(r, dict) or not isinstance(r.get("data"), list): raise ValueError("unexpected OKX response shape")
        d = [x for x in r["data"] if isinstance(x, list) and len(x) > col]
        if not d: break
        before = len(out)
        for x in d: out[int(num(x[0]))] = num(x[col])
        end = min(int(num(x[0])) for x in d)
        if len(out) == before: break
        if len(d) < 100: break
    return sorted(out.items())
