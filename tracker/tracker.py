#!/usr/bin/env python3
"""Live call tracker (stdlib only, sqlite). Every 5 min: compute each coin's call with liqmodel (same model + params.json
as index.html), log a row when the call changes or hourly, resolve open calls on Hyperliquid 5m candles, write stats.json.
Read-only: public data only, no keys, no trading."""
import json, os, sys, time, sqlite3, shutil, traceback, datetime
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import liqdata, liqmodel
WEB = os.environ.get("LIQMAP_WEB", os.path.join(os.path.dirname(HERE), "web"))
DATA = os.environ.get("LIQMAP_DATA", os.path.join(HERE, "data")); DB = f"{DATA}/tracker.db"; os.makedirs(os.path.dirname(DB), exist_ok=True); H = 3600000; INTERVAL = 300
def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, ts INTEGER, coin TEXT, price REAL, dir TEXT, target REAL,
      then_target REAL, dist_pct REAL, conf TEXT, score REAL, params_version INTEGER, reason TEXT,
      outcome TEXT, tth_h REAL, mae_pct REAL, r REAL, resolved_ts INTEGER)""")
    have = {r[1] for r in c.execute("PRAGMA table_info(calls)")}
    for col, typ in NEWCOLS:
        if col not in have: c.execute(f"ALTER TABLE calls ADD COLUMN {col} {typ}")
    return c
# 2026-10-22: per-call features (all observed at or before ts: look-ahead safe; *_ts = time the datum refers to) +
# v2 (new method) call/outcome. The original columns (target/outcome/r/...) remain the OLD method = shadow.
NEWCOLS = [("oi_usd", "REAL"), ("oi_ts", "INTEGER"), ("funding", "REAL"), ("ls_ratio", "REAL"), ("ls_ts", "INTEGER"),
           ("target_size", "REAL"), ("opp_price", "REAL"), ("opp_size", "REAL"), ("atr_h", "REAL"), ("atr_ts", "INTEGER"),
           ("datr", "REAL"), ("asym", "REAL"), ("session", "TEXT"), ("feat_ts", "INTEGER"),
           ("v2_pass", "INTEGER"), ("v2_why", "TEXT"), ("v2_target", "REAL"),
           ("v2_outcome", "TEXT"), ("v2_tth_h", "REAL"), ("v2_mae_pct", "REAL"), ("v2_r", "REAL"), ("v2_resolved_ts", "INTEGER"),
           ("prev_outcome", "TEXT"), ("prev_r", "REAL")]
# Columns written when a call is logged. Outcome fields are filled later by grade().
CALL_COLS = ["ts","coin","price","dir","target","then_target","dist_pct","conf","score","params_version","reason",
             "oi_usd","oi_ts","funding","ls_ratio","ls_ts","target_size","opp_price","opp_size","atr_h","atr_ts",
             "datr","asym","session","feat_ts","v2_pass","v2_why","v2_target","v2_outcome","prev_outcome","prev_r"]

def log(*a): print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)
def utc_day(ts_ms):
    """UTC calendar day. Coin-day bootstrap groups must not follow the host timezone."""
    return datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
def previous_resolved(con, coin, now_ms):
    """Outcome and R of the latest call for this coin that had already resolved before now_ms.
    Hourly calls stay open for the horizon, so the immediately previous row is usually still open;
    the last resolved one is the look-ahead-safe feature. Returns (None, None) when there isn't one."""
    row = con.execute(
        "SELECT outcome, r FROM calls WHERE coin=? AND outcome IS NOT NULL AND ts<? ORDER BY ts DESC LIMIT 1",
        (coin, now_ms)).fetchone()
    return (row[0], row[1]) if row else (None, None)
def recent_hits(con, now_ms, coin=None):
    """Calls that resolved as a hit in the last 12h. Page and tracker both feed these to the hit filter."""
    since = now_ms - liqmodel.HIT_WINDOW_MS
    if coin is None:
        rows = con.execute(
            "SELECT coin, target, resolved_ts FROM calls WHERE outcome='hit' AND target IS NOT NULL AND resolved_ts>=?",
            (since,)).fetchall()
        return [{"coin": c, "p": p, "t": t} for c, p, t in rows]
    rows = con.execute(
        "SELECT target, resolved_ts FROM calls WHERE coin=? AND outcome='hit' AND target IS NOT NULL AND resolved_ts>=?",
        (coin, since)).fetchall()
    return [{"p": p, "t": t} for p, t in rows]
def insert_call(con, row):
    con.execute(f"INSERT INTO calls({','.join(CALL_COLS)}) VALUES({','.join('?' * len(CALL_COLS))})", [row.get(c) for c in CALL_COLS])
def grade(direction, entry, cluster_target, v2_target, after, inv_mult, horizon_ms, t0, now_ms, iv_ms=300000):
    """Resolve the shadow and the v2 target against one stop: 1.5× the original cluster distance.
    v2_target None (suppressed) skips the v2 leg. Returns (old_or_None, v2_or_None)."""
    stop = liqmodel.cluster_stop(direction, entry, cluster_target, inv_mult)
    old = liqmodel.resolve(direction, entry, cluster_target, after, inv_mult, horizon_ms, t0, iv_ms, now_ms, stop=stop)
    new = liqmodel.resolve(direction, entry, v2_target, after, inv_mult, horizon_ms, t0, iv_ms, now_ms, stop=stop) if v2_target is not None else None
    return old, new
def cycle():
    P = liqmodel.load_params(); con = db(); now = int(time.time() * 1000); ctx = liqdata.ctx()
    for coin in liqdata.COINS:
        try:
            C = liqdata.candles(coin, now - 100 * H); oi = liqdata.okx_series(coin, "oi", 100); ls = liqdata.okx_series(coin, "ls", 100)
            if coin not in ctx or not C or not oi: raise ValueError("missing market data")
            x = ctx[coin]; spot = x["mark"]; last = oi[-1][1]
            if spot <= 0 or last <= 0: raise ValueError("non-positive price or OI")
            scale = (x["oi"] * spot + last) / last
            M = liqmodel.model(C, oi, ls, spot, P, scale)
            # Same 12h / 0.25% hit filter as the page (filterHitPath on STATS.recent_hits).
            M = dict(M, path=liqmodel.filter_hit_path(M["path"], recent_hits(con, now, coin), now))
            r = liqmodel.call(C, M, x["funding"], P)
            atr, atr_ts = liqmodel.hourly_atr(C, now); v = liqmodel.call_v2(C, M, x["funding"], P, atr) if r else None
            prev = con.execute("SELECT ts,dir,target FROM calls WHERE coin=? ORDER BY ts DESC LIMIT 1", (coin,)).fetchone()
            if r and v:
                changed = not prev or prev[1] != r["dir"] or abs(prev[2] - r["target"]) / r["target"] > 0.0025
                if changed or now - prev[0] >= H:
                    prev_outcome, prev_r = previous_resolved(con, coin, now)
                    insert_call(con, {"ts": now, "coin": coin, "price": spot, "dir": r["dir"], "target": r["target"],
                        "then_target": r["then"], "dist_pct": r["pct"], "conf": r["conf"], "score": r["score"],
                        "params_version": P["version"], "reason": "change" if changed else "hourly",
                        "oi_usd": x["oi"] * spot, "oi_ts": oi[-1][0] if oi else None, "funding": x["funding"],
                        "ls_ratio": ls[-1][1] if ls else None, "ls_ts": ls[-1][0] if ls else None,
                        "target_size": v["target_size"], "opp_price": v["opp_price"], "opp_size": v["opp_size"],
                        "atr_h": atr, "atr_ts": atr_ts, "datr": v["datr"], "asym": v["asym"],
                        "session": liqmodel.session_tag(now), "feat_ts": now,
                        "v2_pass": int(v["pass"]), "v2_why": v["why"], "v2_target": v["target"] if v["pass"] else None,
                        "v2_outcome": None if v["pass"] else "suppressed", "prev_outcome": prev_outcome, "prev_r": prev_r})
                    vt = v["target"]
                    log(coin, "logged", r["dir"], round(r["target"], 6), f"{r['pct']:+.2f}%", r["conf"], "change" if changed else "hourly",
                        "v2", "PASS" if v["pass"] else "suppressed:" + v["why"],
                        round(vt, 6) if isinstance(vt, (int, float)) and vt == vt else None)
            # resolve open calls (old method = shadow) and v2 calls against the same cluster stop
            rows = con.execute("SELECT id,ts,price,dir,target,outcome,v2_target,v2_outcome FROM calls WHERE coin=? AND (outcome IS NULL OR (v2_target IS NOT NULL AND v2_outcome IS NULL))", (coin,)).fetchall()
            for cid, ts, px, d, tg, oc, v2t, v2o in rows:
                after = [c for c in liqdata.candles(coin, ts, "5m") if c[0] >= ts]
                hz = P["horizon_h"] * H
                if oc is None or (v2t is not None and v2o is None):
                    old, new = grade(d, px, tg, v2t, after, P["invalidation_mult"], hz, ts, now)
                    if oc is None and old:
                        con.execute("UPDATE calls SET outcome=?,tth_h=?,mae_pct=?,r=?,resolved_ts=? WHERE id=?", (old["outcome"], old["tth_h"], old["mae_pct"], old["r"], now, cid))
                        log(coin, "resolved", cid, old["outcome"])
                    if v2t is not None and v2o is None and new:
                        con.execute("UPDATE calls SET v2_outcome=?,v2_tth_h=?,v2_mae_pct=?,v2_r=?,v2_resolved_ts=? WHERE id=?", (new["outcome"], new["tth_h"], new["mae_pct"], new["r"], now, cid))
                        log(coin, "v2 resolved", cid, new["outcome"])
                time.sleep(0.2)
            con.commit()
        except Exception as e: log(coin, "ERROR", e)
        time.sleep(0.5)
    con.close(); write_stats()
def live_summary(rows):
    n = len(rows)
    if not n: return {"n": 0}
    hits = [r for r in rows if r[0] == "hit"]
    s = {"n": n, "hit_rate": len(hits) / n, "expectancy_r": sum(r[3] for r in rows) / n,
         "avg_tth_h": sum(r[1] for r in hits) / len(hits) if hits else None, "avg_mae_pct": sum(r[2] for r in rows) / n}
    s["by_conf"] = {k: {"n": len(g), "hit_rate": sum(r[0] == "hit" for r in g) / len(g) if g else None} for k in ("strong", "moderate", "weak") for g in [[r for r in rows if r[4] == k]]}
    return s
def r_ci(rows, key_r=0, key_grp=1, B=1000, seed=7):
    """Mean R per call with a 90% bootstrap CI, resampling whole coin-days (calls within a coin-day are correlated)."""
    import random
    rows = [x for x in rows if x[key_r] is not None]
    if not rows: return {"n": 0}
    g = {}
    for x in rows: g.setdefault(x[key_grp], []).append(x[key_r])
    keys = list(g); rng = random.Random(seed); bs = []
    for _ in range(B):
        tot = n = 0
        for k in (rng.choice(keys) for _ in keys): tot += sum(g[k]); n += len(g[k])
        bs.append(tot / n)
    bs.sort(); m = sum(x[key_r] for x in rows) / len(rows)
    return {"n": len(rows), "r_per_call": round(m, 4), "ci90": [round(bs[int(.05 * B)], 4), round(bs[int(.95 * B) - 1], 4)], "clusters": len(keys)}
def headline(con, since_ms=0):
    day = utc_day
    old = [(r, f"{c}:{day(ts)}") for ts, c, r in con.execute("SELECT ts,coin,r FROM calls WHERE outcome IS NOT NULL AND ts>=?", (since_ms,))]
    v2 = [(r, f"{c}:{day(ts)}") for ts, c, r in con.execute("SELECT ts,coin,v2_r FROM calls WHERE v2_outcome IS NOT NULL AND v2_outcome!='suppressed' AND ts>=?", (since_ms,))]
    old_same = [(r, f"{c}:{day(ts)}") for ts, c, r in con.execute("SELECT ts,coin,r FROM calls WHERE v2_outcome IS NOT NULL AND v2_outcome!='suppressed' AND outcome IS NOT NULL AND ts>=?", (since_ms,))]
    hr = lambda q: (lambda a: {"hit_rate": round(a[0] / a[1], 4) if a[1] else None})(con.execute(q, (since_ms,)).fetchone())
    return {"metric": "R per call (hit=+1, miss=-invalidation_mult, expired=signed move/target dist), 90% CI by coin-day bootstrap",
            "v2_new_method": {**r_ci(v2), **hr("SELECT SUM(v2_outcome='hit'),COUNT(*) FROM calls WHERE v2_outcome IN ('hit','miss','expired') AND ts>=?")},
            "old_method_shadow_all": {**r_ci(old), **hr("SELECT SUM(outcome='hit'),COUNT(*) FROM calls WHERE outcome IS NOT NULL AND ts>=?")},
            "old_method_on_v2_passing_calls": r_ci(old_same),
            "v2_suppressed": con.execute("SELECT COUNT(*) FROM calls WHERE v2_outcome='suppressed' AND ts>=?", (since_ms,)).fetchone()[0],
            "v2_since": since_ms}
def write_stats():
    P = liqmodel.load_params(); con = db()
    bt = None
    if os.path.exists(f"{DATA}/backtest.json"):
        try:
            with open(f"{DATA}/backtest.json") as f: bt = json.load(f)
            if not isinstance(bt, dict): bt = None
        except (OSError, ValueError): bt = None
    st = {"ts": int(time.time() * 1000), "params_version": P["version"], "params_created": P.get("created"), "params_note": P.get("note"),
          "backtest": None, "live": {}, "coins": {}}
    q = "SELECT outcome,tth_h,mae_pct,r,conf FROM calls WHERE outcome IS NOT NULL"
    st["live"]["all"] = live_summary(con.execute(q).fetchall())
    st["live"]["open"] = con.execute("SELECT COUNT(*) FROM calls WHERE outcome IS NULL").fetchone()[0]
    now_ms = st["ts"]
    st["recent_hits"] = recent_hits(con, now_ms)
    try:
        first_v2 = con.execute("SELECT MIN(ts) FROM calls WHERE v2_pass IS NOT NULL").fetchone()[0] or 0
        st["headline"] = headline(con, first_v2)
        st["headline_all_time_old_method"] = r_ci([(r, f"{c}:{utc_day(ts)}") for ts, c, r in con.execute("SELECT ts,coin,r FROM calls WHERE outcome IS NOT NULL")])
    except Exception as e: st["headline"] = {"error": repr(e)}

    for c in liqdata.COINS:
        st["coins"][c] = {"live": live_summary(con.execute(q + " AND coin=?", (c,)).fetchall()),
                          "backtest": (bt or {}).get("per_coin", {}).get(c, {}).get("all")}
    if bt: st["backtest"] = {k: bt.get(k) for k in ("ts", "params_version", "hist_hours", "step_h", "decision", "calibration", "changed", "overall", "random_baseline_note")}
    con.close()
    os.makedirs(WEB, exist_ok=True)
    for dst in (f"{DATA}/stats.json", f"{WEB}/stats.json"):
        with open(dst + ".tmp", "w") as f: json.dump(st, f, indent=1)
        os.replace(dst + ".tmp", dst)
    shutil.copyfile(f"{HERE}/params.json", f"{WEB}/params.json")
if __name__ == "__main__":
    if "--stats" in sys.argv: write_stats(); sys.exit()
    once = "--once" in sys.argv
    while True:
        t = time.time()
        try: cycle()
        except Exception: log("cycle error", traceback.format_exc())
        if once: break
        time.sleep(max(10, INTERVAL - (time.time() - t)))
