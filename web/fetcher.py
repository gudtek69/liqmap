#!/usr/bin/env python3
"""Free, read-only data fetcher for the multi-coin liquidation-map predictor.
Every REFRESH seconds pulls public data (no keys) for the configured coins, writes ONE combined
data.json next to index.html, and serves this folder on http://HOST:PORT (default 127.0.0.1:8787).
Sources: Hyperliquid info API (hourly candles, OI, funding, mark) and OKX rubik public stats
(OI history + long/short account ratio; OKX sends no CORS headers, so the browser can't call it directly).
Requests are staggered: the OKX rubik limit is 5 req / 2 s, so ~0.5 s between OKX calls.

Config (environment variables, see .env.example):
  LIQMAP_COINS    comma-separated Hyperliquid/OKX tickers (default BTC,ETH,SOL)
  LIQMAP_HOST     bind address (default 127.0.0.1)
  LIQMAP_PORT     port (default 8787)
  LIQMAP_REFRESH  seconds between fetches (default 60)
  LIQMAP_TRACKER  tracker folder; stats.json / params.json fall back to it (and its data/) (default ../tracker)"""
import json, time, threading, urllib.request, os, sys, http.server, functools
D = os.path.dirname(os.path.abspath(__file__))
COINS = [c.strip().upper() for c in os.environ.get("LIQMAP_COINS", "BTC,ETH,SOL").split(",") if c.strip()]
HOST = os.environ.get("LIQMAP_HOST", "127.0.0.1"); PORT = int(os.environ.get("LIQMAP_PORT", 8787))
REFRESH = int(os.environ.get("LIQMAP_REFRESH", 60))
TRK = os.environ.get("LIQMAP_TRACKER", os.path.join(os.path.dirname(D), "tracker"))
UA = {"User-Agent": "liqmap/1.1", "Content-Type": "application/json"}
def get(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r: return json.loads(r.read())
def hl(body): return get("https://api.hyperliquid.xyz/info", body)
def okx(path):
    time.sleep(0.5); return get("https://www.okx.com/api/v5/rubik/stat/contracts/" + path)["data"]
def fetch():
    now = int(time.time() * 1000); out = {"ts": now, "coins": {}, "sources": {}}
    try:
        meta, ctx = hl({"type": "metaAndAssetCtxs"}); names = [a["name"] for a in meta["universe"]]
    except Exception as e: meta = None; out["sources"]["hyperliquid_meta"] = f"fail: {e}"
    for c in COINS:
        d = {"ts": now, "sources": {}}
        try:
            k = hl({"type": "candleSnapshot", "req": {"coin": c, "interval": "1h", "startTime": now - 100 * 3600 * 1000}})
            d["candles"] = [[x["t"], float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"]), float(x["v"])] for x in k]
            a = ctx[names.index(c)]
            d["hl"] = {"oi_btc": float(a["openInterest"]), "funding": float(a["funding"]), "mark": float(a["markPx"])}
            d["sources"]["hyperliquid"] = "ok"
        except Exception as e: d["sources"]["hyperliquid"] = f"fail: {e}"
        try:
            d["okx_oi"] = sorted([[int(x[0]), float(x[3])] for x in okx(f"open-interest-history?instId={c}-USDT-SWAP&period=1H&limit=100")])
            d["okx_ls"] = sorted([[int(x[0]), float(x[1])] for x in okx(f"long-short-account-ratio-contract?instId={c}-USDT-SWAP&period=1H&limit=100")])
            d["sources"]["okx"] = "ok"
        except Exception as e: d["sources"]["okx"] = f"fail: {e}"
        out["coins"][c] = d
        time.sleep(0.3)
    tmp = os.path.join(D, "data.json.tmp"); json.dump(out, open(tmp, "w")); os.replace(tmp, os.path.join(D, "data.json"))
    print(time.strftime("%H:%M:%S"), {c: v["sources"] for c, v in out["coins"].items()}, flush=True)
def loop():
    while True:
        t = time.time()
        try: fetch()
        except Exception as e: print("err", e, flush=True)
        time.sleep(max(5, REFRESH - (time.time() - t)))
if __name__ == "__main__":
    if "--once" in sys.argv: fetch(); sys.exit()
    threading.Thread(target=loop, daemon=True).start()
    class Handler(http.server.SimpleHTTPRequestHandler):
        # stats.json / params.json are served from this folder if present, else from the tracker folder
        def translate_path(self, path):
            p = super().translate_path(path); name = os.path.basename(p)
            if name in ("stats.json", "params.json") and not os.path.exists(p):
                for cand in (os.path.join(TRK, name), os.path.join(TRK, "data", name)):
                    if os.path.exists(cand): return cand
            return p
        def log_message(self, *a): pass
    h = functools.partial(Handler, directory=D)
    print(f"Serving http://{HOST}:{PORT}/  (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer((HOST, PORT), h).serve_forever()
