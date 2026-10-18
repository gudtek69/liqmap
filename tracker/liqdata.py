"""Free public data access (stdlib only). Hyperliquid info API + OKX rubik stats. Read-only, no keys."""
import json, time, urllib.request
import os
COINS = [c.strip().upper() for c in os.environ.get("LIQMAP_COINS", "BTC,ETH,SOL").split(",") if c.strip()]
UA = {"User-Agent": "liqmap-tracker/1.0", "Content-Type": "application/json"}
H = 3600000
def get(url, body=None, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r: return json.loads(r.read())
        except Exception:
            if k == tries - 1: raise
            time.sleep(2 * (k + 1))
def hl(body): return get("https://api.hyperliquid.xyz/info", body)
def candles(coin, start_ms, interval="1h", end_ms=None):
    req = {"coin": coin, "interval": interval, "startTime": int(start_ms)}
    if end_ms: req["endTime"] = int(end_ms)
    return [[x["t"], float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"]), float(x["v"])] for x in hl({"type": "candleSnapshot", "req": req})]
def ctx():
    meta, c = hl({"type": "metaAndAssetCtxs"}); names = [a["name"] for a in meta["universe"]]
    return {n: {"oi": float(c[i]["openInterest"]), "funding": float(c[i]["funding"]), "mark": float(c[i]["markPx"])} for i, n in enumerate(names)}
def funding_hist(coin, start_ms):
    out = {}; s = int(start_ms)
    while True:
        r = hl({"type": "fundingHistory", "coin": coin, "startTime": s})
        for x in r: out[x["time"] // H * H] = float(x["fundingRate"])
        if len(r) < 500 or r[-1]["time"] + 1 <= s: break
        s = r[-1]["time"] + 1; time.sleep(0.3)
    return out
def okx_series(coin, kind, hours=100):
    """kind: 'oi' -> [[t, usd]], 'ls' -> [[t, ratio]]; paginates with end= to go back `hours`."""
    path = {"oi": "open-interest-history", "ls": "long-short-account-ratio-contract"}[kind]
    out = {}; end = None
    while len(out) < hours:
        url = f"https://www.okx.com/api/v5/rubik/stat/contracts/{path}?instId={coin}-USDT-SWAP&period=1H&limit=100" + (f"&end={end}" if end else "")
        time.sleep(0.5); d = get(url)["data"]
        if not d: break
        before = len(out)
        for x in d: out[int(x[0])] = float(x[3] if kind == "oi" else x[1])
        end = min(int(x[0]) for x in d)
        if len(out) == before: break
        if len(d) < 100: break
    return sorted(out.items())
