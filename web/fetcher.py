#!/usr/bin/env python3
"""Free, read-only data fetcher for the multi-coin liquidation-map predictor.
Every REFRESH seconds pulls public data (no keys) for the configured coins, writes ONE combined
data.json next to index.html, and serves a small whitelist of files from this folder on
http://HOST:PORT (default 127.0.0.1:8787, loopback only).
Sources: Hyperliquid info API (hourly candles, OI, funding, mark) and OKX rubik public stats
(OI history + long/short account ratio; OKX sends no CORS headers, so the browser can't call it directly).
Requests are staggered: the OKX rubik limit is 5 req / 2 s, so ~0.5 s between OKX calls.

Config (environment variables, see .env.example):
  LIQMAP_COINS          comma-separated Hyperliquid/OKX tickers (default BTC,ETH,SOL; letters/digits only)
  LIQMAP_HOST           bind address (default 127.0.0.1). Changing it (e.g. 0.0.0.0) exposes the server to
                        your network. That is RISKY and not recommended.
  LIQMAP_ALLOWED_HOSTS  extra Host header values to accept (comma-separated) if you change LIQMAP_HOST
  LIQMAP_PORT           port (default 8787)
  LIQMAP_REFRESH        seconds between fetches (default 60, minimum 15)
  LIQMAP_TRACKER        tracker folder; stats.json / params.json fall back to it (and its data/) (default ../tracker)"""
import json, time, threading, urllib.request, urllib.parse, urllib.error, os, sys, http.server, math, re, ssl, ipaddress

D = os.path.dirname(os.path.abspath(__file__))
COIN_RE = re.compile(r"^[A-Z0-9]{1,15}$")
COINS = [c for c in (c.strip().upper() for c in os.environ.get("LIQMAP_COINS", "BTC,ETH,SOL").split(",")) if COIN_RE.match(c)] or ["BTC", "ETH", "SOL"]
HOST = os.environ.get("LIQMAP_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.environ.get("LIQMAP_PORT", 8787))
REFRESH = max(15, int(os.environ.get("LIQMAP_REFRESH", 60)))
TRK = os.environ.get("LIQMAP_TRACKER", os.path.join(os.path.dirname(D), "tracker"))

# ---------------------------------------------------------------- outbound HTTP (HTTPS only, allowlisted hosts)
API_HOSTS = {"api.hyperliquid.xyz", "www.okx.com"}
MAX_BYTES = 8 * 1024 * 1024
UA = {"User-Agent": "liqmap/1.2", "Content-Type": "application/json", "Accept": "application/json"}
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): raise urllib.error.URLError("redirects are not followed")
_OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
def get(url, body=None):
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname not in API_HOSTS: raise ValueError("refusing non-HTTPS or non-allowlisted URL")
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=UA)
    with _OPENER.open(req, timeout=15) as r:
        raw = r.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES: raise ValueError("response too large")
    return json.loads(raw)  # plain JSON only; never eval/pickle
def hl(body): return get("https://api.hyperliquid.xyz/info", body)
def okx(path):
    time.sleep(0.5); r = get("https://www.okx.com/api/v5/rubik/stat/contracts/" + path)
    if not isinstance(r, dict) or not isinstance(r.get("data"), list): raise ValueError("unexpected OKX response shape")
    return r["data"]

# ---------------------------------------------------------------- response validation
def num(x):
    v = float(x)
    if not math.isfinite(v): raise ValueError("non-finite number")
    return v
def parse_candles(k):
    if not isinstance(k, list) or len(k) > 5000: raise ValueError("unexpected candle response shape")
    return [[int(num(x["t"])), num(x["o"]), num(x["h"]), num(x["l"]), num(x["c"]), num(x["v"])] for x in k if isinstance(x, dict)]
def parse_ctx(r):
    if not (isinstance(r, list) and len(r) == 2 and isinstance(r[0], dict) and isinstance(r[1], list)): raise ValueError("unexpected meta shape")
    uni = r[0].get("universe")
    if not isinstance(uni, list): raise ValueError("unexpected meta shape")
    out = {}
    for a, c in zip(uni, r[1]):
        if isinstance(a, dict) and isinstance(c, dict) and isinstance(a.get("name"), str): out[a["name"]] = c
    return out
def parse_okx(rows, col):
    out = []
    for x in rows[:1000]:
        if isinstance(x, list) and len(x) > col: out.append([int(num(x[0])), num(x[col])])
    return sorted(out)
def err(e): return ("fail: " + type(e).__name__ + ": " + str(e))[:120]

def fetch():
    now = int(time.time() * 1000); out = {"ts": now, "coins": {}, "sources": {}}
    try: ctx = parse_ctx(hl({"type": "metaAndAssetCtxs"}))
    except Exception as e: ctx = None; out["sources"]["hyperliquid_meta"] = err(e)
    for c in COINS:
        d = {"ts": now, "sources": {}}
        try:
            d["candles"] = parse_candles(hl({"type": "candleSnapshot", "req": {"coin": c, "interval": "1h", "startTime": now - 100 * 3600 * 1000}}))
            if ctx is None or c not in ctx: raise ValueError("coin not in Hyperliquid meta")
            a = ctx[c]
            d["hl"] = {"oi_btc": num(a["openInterest"]), "funding": num(a["funding"]), "mark": num(a["markPx"])}
            d["sources"]["hyperliquid"] = "ok"
        except Exception as e: d["sources"]["hyperliquid"] = err(e)
        try:
            d["okx_oi"] = parse_okx(okx(f"open-interest-history?instId={c}-USDT-SWAP&period=1H&limit=100"), 3)
            d["okx_ls"] = parse_okx(okx(f"long-short-account-ratio-contract?instId={c}-USDT-SWAP&period=1H&limit=100"), 1)
            d["sources"]["okx"] = "ok"
        except Exception as e: d["sources"]["okx"] = err(e)
        out["coins"][c] = d
        time.sleep(0.3)
    tmp = os.path.join(D, "data.json.tmp")
    with open(tmp, "w") as f: json.dump(out, f)
    os.replace(tmp, os.path.join(D, "data.json"))
    print(time.strftime("%H:%M:%S"), {c: v["sources"] for c, v in out["coins"].items()}, flush=True)
def loop():
    while True:
        t = time.time()
        try: fetch()
        except Exception as e: print("err", type(e).__name__, e, flush=True)
        time.sleep(max(5, REFRESH - (time.time() - t)))

# ---------------------------------------------------------------- local static server (whitelist only)
FILES = {  # URL path -> (file name in web/, content type). Nothing else is ever served.
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/data.json": ("data.json", "application/json; charset=utf-8"),
    "/stats.json": ("stats.json", "application/json; charset=utf-8"),
    "/params.json": ("params.json", "application/json; charset=utf-8"),
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self' https://api.hyperliquid.xyz; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SEC_HEADERS = {
    "Content-Security-Policy": CSP, "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer", "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin", "Cache-Control": "no-store",
}
def is_loopback(h):
    if h == "localhost": return True
    try: return ipaddress.ip_address(h).is_loopback
    except ValueError: return False
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", HOST} | {h.strip().lower() for h in os.environ.get("LIQMAP_ALLOWED_HOSTS", "").split(",") if h.strip()}
def resolve_file(name):
    p = os.path.join(D, name)
    if os.path.isfile(p): return p
    if name in ("stats.json", "params.json"):  # fall back to the tracker folder
        for cand in (os.path.join(TRK, name), os.path.join(TRK, "data", name)):
            if os.path.isfile(cand): return cand
    return None
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "liqmap"; sys_version = ""
    def version_string(self): return "liqmap"
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", head=False):
        self.send_response(code)
        for k, v in SEC_HEADERS.items(): self.send_header(k, v)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head: self.wfile.write(body)
    def _serve(self, head):
        host = (self.headers.get("Host") or "").strip().lower()
        hostname = host.rsplit(":", 1)[0] if not host.endswith("]") else host  # strip :port (keeps [::1])
        if hostname not in ALLOWED_HOSTS: return self._send(403, b"forbidden host\n", head=head)  # DNS-rebinding guard
        path = urllib.parse.urlsplit(self.path).path  # exact match on the raw path: no decoding, no traversal
        entry = FILES.get(path)
        if not entry: return self._send(404, b"not found\n", head=head)
        p = resolve_file(entry[0])
        if not p: return self._send(404, b"not found\n", head=head)
        try:
            with open(p, "rb") as f: body = f.read()
        except OSError: return self._send(404, b"not found\n", head=head)
        self._send(200, body, entry[1], head)
    def do_GET(self): self._serve(False)
    def do_HEAD(self): self._serve(True)
    def _deny(self): self._send(405, b"method not allowed\n")
    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _deny
    def log_message(self, *a): pass

if __name__ == "__main__":
    if "--once" in sys.argv: fetch(); sys.exit()
    if not is_loopback(HOST):
        print(f"WARNING: LIQMAP_HOST={HOST!r} is not a loopback address; the map server will be reachable from other "
              "machines. This is risky and not recommended. Use 127.0.0.1 unless you know what you are doing.", flush=True)
    threading.Thread(target=loop, daemon=True).start()
    srv = http.server.ThreadingHTTPServer((HOST, PORT), Handler); srv.daemon_threads = True
    print(f"Serving http://{HOST}:{PORT}/  (Ctrl+C to stop)", flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
