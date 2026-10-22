#!/usr/bin/env python3
"""Live call tracker (stdlib only, sqlite). Every 5 min: compute each coin's call with liqmodel (same model + params.json
as index.html), log a row when the call changes or hourly, resolve open calls on Hyperliquid 5m candles, write stats.json.
Read-only: public data only, no keys, no trading."""
import json, os, sys, time, sqlite3, shutil, traceback
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

def log(*a): print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)
def cycle():
    P = liqmodel.load_params(); con = db(); now = int(time.time() * 1000); ctx = liqdata.ctx()
    for coin in liqdata.COINS:
        try:
            C = liqdata.candles(coin, now - 100 * H); oi = liqdata.okx_series(coin, "oi", 100); ls = liqdata.okx_series(coin, "ls", 100)
            if coin not in ctx or not C or not oi: raise ValueError("missing market data")
            x = ctx[coin]; spot = x["mark"]; last = oi[-1][1]
            if spot <= 0 or last <= 0: raise ValueError("non-positive price or OI")
            scale = (x["oi"] * spot + last) / last
            M = liqmodel.model(C, oi, ls, spot, P, scale); r = liqmodel.call(C, M, x["funding"], P)
            atr, atr_ts = liqmodel.hourly_atr(C, now); v = liqmodel.call_v2(C, M, x["funding"], P, atr) if r else None
            prev = con.execute("SELECT ts,dir,target FROM calls WHERE coin=? ORDER BY ts DESC LIMIT 1", (coin,)).fetchone()
            if r:
                changed = not prev or prev[1] != r["dir"] or abs(prev[2] - r["target"]) / r["target"] > 0.0025
                if changed or now - prev[0] >= H:
                    con.execute("""INSERT INTO calls(ts,coin,price,dir,target,then_target,dist_pct,conf,score,params_version,reason,
                        oi_usd,oi_ts,funding,ls_ratio,ls_ts,target_size,opp_price,opp_size,atr_h,atr_ts,datr,asym,session,feat_ts,v2_pass,v2_why,v2_target,v2_outcome)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (now, coin, spot, r["dir"], r["target"], r["then"], r["pct"], r["conf"], r["score"], P["version"], "change" if changed else "hourly",
                                 x["oi"] * spot, oi[-1][0] if oi else None, x["funding"], ls[-1][1] if ls else None, ls[-1][0] if ls else None,
                                 v["target_size"], v["opp_price"], v["opp_size"], atr, atr_ts, v["datr"], v["asym"], liqmodel.session_tag(now), now,
                                 int(v["pass"]), v["why"], v["target"] if v["pass"] else None, None if v["pass"] else "suppressed"))
                    log(coin, "logged", r["dir"], round(r["target"], 6), f"{r['pct']:+.2f}%", r["conf"], "change" if changed else "hourly",
                        "v2", "PASS" if v["pass"] else "suppressed:" + v["why"], round(v["target"], 6))
            # resolve open calls (old method = shadow) and v2 calls; expiry only after the full horizon
            rows = con.execute("SELECT id,ts,price,dir,target,outcome,v2_target,v2_outcome FROM calls WHERE coin=? AND (outcome IS NULL OR (v2_target IS NOT NULL AND v2_outcome IS NULL))", (coin,)).fetchall()
            for cid, ts, px, d, tg, oc, v2t, v2o in rows:
                after = [c for c in liqdata.candles(coin, ts, "5m") if c[0] >= ts]
                hz = P["horizon_h"] * H
                if oc is None:
                    res = liqmodel.resolve(d, px, tg, after, P["invalidation_mult"], hz, ts, 300000, now)
                    if res:
                        con.execute("UPDATE calls SET outcome=?,tth_h=?,mae_pct=?,r=?,resolved_ts=? WHERE id=?", (res["outcome"], res["tth_h"], res["mae_pct"], res["r"], now, cid))
                        log(coin, "resolved", cid, res["outcome"])
                if v2t is not None and v2o is None:
                    res = liqmodel.resolve(d, px, v2t, after, P["invalidation_mult"], hz, ts, 300000, now)
                    if res:
                        con.execute("UPDATE calls SET v2_outcome=?,v2_tth_h=?,v2_mae_pct=?,v2_r=?,v2_resolved_ts=? WHERE id=?", (res["outcome"], res["tth_h"], res["mae_pct"], res["r"], now, cid))
                        log(coin, "v2 resolved", cid, res["outcome"])
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
    import datetime as _dt
    day = lambda ts: _dt.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
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
    try:
        first_v2 = con.execute("SELECT MIN(ts) FROM calls WHERE v2_pass IS NOT NULL").fetchone()[0] or 0
        st["headline"] = headline(con, first_v2); st["headline_all_time_old_method"] = r_ci([(r, f"{c}:{ts // 86400000}") for ts, c, r in con.execute("SELECT ts,coin,r FROM calls WHERE outcome IS NOT NULL")])
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
