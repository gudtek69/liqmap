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
    return c
def log(*a): print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)
def cycle():
    P = liqmodel.load_params(); con = db(); now = int(time.time() * 1000); ctx = liqdata.ctx()
    for coin in liqdata.COINS:
        try:
            C = liqdata.candles(coin, now - 100 * H); oi = liqdata.okx_series(coin, "oi", 100); ls = liqdata.okx_series(coin, "ls", 100)
            x = ctx[coin]; spot = x["mark"]; last = oi[-1][1]; scale = (x["oi"] * spot + last) / last
            M = liqmodel.model(C, oi, ls, spot, P, scale); r = liqmodel.call(C, M, x["funding"], P)
            prev = con.execute("SELECT ts,dir,target FROM calls WHERE coin=? ORDER BY ts DESC LIMIT 1", (coin,)).fetchone()
            if r:
                changed = not prev or prev[1] != r["dir"] or abs(prev[2] - r["target"]) / r["target"] > 0.0025
                if changed or now - prev[0] >= H:
                    con.execute("INSERT INTO calls(ts,coin,price,dir,target,then_target,dist_pct,conf,score,params_version,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (now, coin, spot, r["dir"], r["target"], r["then"], r["pct"], r["conf"], r["score"], P["version"], "change" if changed else "hourly"))
                    log(coin, "logged", r["dir"], round(r["target"], 6), f"{r['pct']:+.2f}%", r["conf"], "change" if changed else "hourly")
            # resolve open calls
            for row in con.execute("SELECT id,ts,price,dir,target FROM calls WHERE coin=? AND outcome IS NULL", (coin,)).fetchall():
                cid, ts, px, d, tg = row
                after = [c for c in liqdata.candles(coin, ts, "5m") if c[0] >= ts]
                res = liqmodel.resolve(d, px, tg, after, P["invalidation_mult"], P["horizon_h"] * H, ts, 300000)
                if res:
                    con.execute("UPDATE calls SET outcome=?,tth_h=?,mae_pct=?,r=?,resolved_ts=? WHERE id=?", (res["outcome"], res["tth_h"], res["mae_pct"], res["r"], now, cid))
                    log(coin, "resolved", cid, res["outcome"])
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
def write_stats():
    P = liqmodel.load_params(); con = db()
    bt = json.load(open(f"{DATA}/backtest.json")) if os.path.exists(f"{DATA}/backtest.json") else None
    st = {"ts": int(time.time() * 1000), "params_version": P["version"], "params_created": P.get("created"), "params_note": P.get("note"),
          "backtest": None, "live": {}, "coins": {}}
    q = "SELECT outcome,tth_h,mae_pct,r,conf FROM calls WHERE outcome IS NOT NULL"
    st["live"]["all"] = live_summary(con.execute(q).fetchall())
    st["live"]["open"] = con.execute("SELECT COUNT(*) FROM calls WHERE outcome IS NULL").fetchone()[0]
    for c in liqdata.COINS:
        st["coins"][c] = {"live": live_summary(con.execute(q + " AND coin=?", (c,)).fetchall()),
                          "backtest": (bt or {}).get("per_coin", {}).get(c, {}).get("all")}
    if bt: st["backtest"] = {k: bt[k] for k in ("ts", "params_version", "hist_hours", "step_h", "decision", "calibration", "changed", "overall", "random_baseline_note")}
    con.close()
    os.makedirs(WEB, exist_ok=True)
    for dst in (f"{DATA}/stats.json", f"{WEB}/stats.json"):
        json.dump(st, open(dst + ".tmp", "w"), indent=1); os.replace(dst + ".tmp", dst)
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
