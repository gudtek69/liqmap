#!/usr/bin/env python3
"""Walk-forward backtest + conservative parameter search for the liquidation-map model. Stdlib only, read-only.
1) Pulls ~HIST_H hours of Hyperliquid 1h candles + funding and OKX OI / long-short history per coin (cached in data/hist/).
2) Replays the live model hourly-ish (100h lookback, same as the page) to generate historical calls; resolves each with
   liqmodel.resolve (hit target before 1.5x adverse move, 24h horizon).
3) Random/grid search on TRAIN (older 2/3); new params adopted only if VALIDATION (newer 1/3) expectancy improves
   by >= MIN_GAIN R with >= MIN_N validation calls and hit-rate not worse. Confidence thresholds/weights calibrated on
   train and kept only if 'strong' beats 'weak' on validation.
Writes data/backtest.json, params.json (+params_history/vN.json), stats.json (via tracker.write_stats)."""
import json, os, sys, time, random, math, copy, datetime
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import liqdata, liqmodel
DATA = os.environ.get("LIQMAP_DATA", os.path.join(HERE, "data"))
H = 3600000; HIST_H = int(os.environ.get("HIST_H", 720)); LOOKBACK = 100; STEP = int(os.environ.get("STEP", 3))
MIN_GAIN = 0.05; MIN_N = 80; N_CAND = int(os.environ.get("N_CAND", 24))
def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
def fetch_hist(coin):
    p = f"{DATA}/hist/{coin}.json"
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 1800: return json.load(open(p))
    now = int(time.time() * 1000); start = now - HIST_H * H
    c = liqdata.candles(coin, start); f = liqdata.funding_hist(coin, start)
    oi = liqdata.okx_series(coin, "oi", HIST_H); ls = liqdata.okx_series(coin, "ls", HIST_H)
    d = {"coin": coin, "ts": now, "candles": c, "funding": f, "oi": oi, "ls": ls}
    os.makedirs(f"{DATA}/hist", exist_ok=True); json.dump(d, open(f"{DATA}/hist/{coin}.json", "w")); return d
def replay(args):
    """Generate + resolve calls for one coin under params P. Last candle is in-progress, excluded."""
    d, P = args; C = d["candles"][:-1]; oi = d["oi"]; ls = d["ls"]; fund = {int(k): v for k, v in d["funding"].items()}
    out = []; oi_t0 = oi[0][0] if oi else 0
    for h in range(LOOKBACK, len(C), STEP):
        win = C[h - LOOKBACK + 1:h + 1]
        if win[0][0] < oi_t0: continue
        tclose = win[-1][0] + H                       # call made at close of candle h
        oiw = [x for x in oi if win[0][0] <= x[0] <= tclose]
        if len(oiw) < 50: continue
        spot = win[-1][4]
        M = liqmodel.model(win, oiw, ls, spot, P)
        f = fund.get(win[-1][0], 0.0)
        r = liqmodel.call(win, M, f, P)
        if not r: continue
        after = C[h + 1:]
        res = liqmodel.resolve(r["dir"], spot, r["target"], after, P["invalidation_mult"], P["horizon_h"] * H, tclose)
        if not res: continue
        mt = spot - (r["target"] - spot)               # mirror target: same distance, opposite direction (random-ish baseline)
        mres = liqmodel.resolve("down" if r["dir"] == "up" else "up", spot, mt, after, P["invalidation_mult"], P["horizon_h"] * H, tclose)
        l1 = win[-1]
        out.append({"t": tclose, "coin": d["coin"], "dir": r["dir"], "pct": r["pct"], "rel": r["rel"], "m4": r["m4"],
                    "m1": (l1[4] - l1[1]) / l1[1], "f": f, "score": r["score"], "conf": r["conf"], **res, "mirror": mres["outcome"] if mres else None})
    return out
def summarize(calls):
    n = len(calls)
    if not n: return {"n": 0}
    hits = [c for c in calls if c["outcome"] == "hit"]; tth = [c["tth_h"] for c in hits]
    mir = [c for c in calls if c.get("mirror")]
    s = {"n": n, "hit_rate": len(hits) / n, "miss_rate": sum(c["outcome"] == "miss" for c in calls) / n,
         "expectancy_r": sum(c["r"] for c in calls) / n, "avg_tth_h": sum(tth) / len(tth) if tth else None,
         "avg_mae_pct": sum(c["mae_pct"] for c in calls) / n,
         "mirror_hit_rate": sum(c["mirror"] == "hit" for c in mir) / len(mir) if mir else None}
    s["by_conf"] = {k: {"n": len(g), "hit_rate": sum(c["outcome"] == "hit" for c in g) / len(g) if g else None}
                    for k in ("strong", "moderate", "weak") for g in [[c for c in calls if c["conf"] == k]]}
    return s
def run_all(hist, P, pool):
    res = pool.map(replay, [(d, P) for d in hist.values()]); return [c for r in res for c in r]
def split(calls, t_split): return [c for c in calls if c["t"] < t_split], [c for c in calls if c["t"] >= t_split]
def rescore(calls, P):
    K = P["conf"]
    for c in calls:
        d = 1 if c["dir"] == "up" else -1; up = d > 0
        sc = c["rel"] / math.sqrt(max(.3, abs(c["pct"])))
        sc += (K["m4_agree"] if liqmodel.sign(c["m4"]) == d else K["m4_disagree"]) + (K["m1_agree"] if liqmodel.sign(c["m1"]) == d else K["m1_disagree"])
        if abs(c["f"]) > K["funding_min"]: sc += K["funding"] if (c["f"] > 0) != up else -K["funding"]
        c["score"] = sc; c["conf"] = "strong" if sc > K["strong"] else "moderate" if sc > K["moderate"] else "weak"
    return calls
def spread(calls):
    s = [c for c in calls if c["conf"] == "strong"]; w = [c for c in calls if c["conf"] == "weak"]
    if len(s) < 15 or len(w) < 15: return -1
    hr = lambda g: sum(c["outcome"] == "hit" for c in g) / len(g); return hr(s) - hr(w)
def calibrate(train, val, P, rng):
    """Search confidence weights; thresholds = 67th/33rd percentile of train scores (terciles)."""
    best = None
    for k in range(200):
        Q = copy.deepcopy(P); K = Q["conf"]
        if k:
            for key in ("m4_agree", "m1_agree", "funding"): K[key] = round(rng.choice([0, .05, .1, .2, .3]), 3)
            for key in ("m4_disagree", "m1_disagree"): K[key] = round(-rng.choice([0, .05, .1, .15, .3]), 3)
        sc = sorted(c["score"] for c in rescore([dict(c) for c in train], Q))
        K["moderate"] = round(sc[len(sc) // 3], 4); K["strong"] = round(sc[2 * len(sc) // 3], 4)
        sp = spread(rescore([dict(c) for c in train], Q))
        if best is None or sp > best[0]: best = (sp, Q)
    Q = best[1]; vs = spread(rescore([dict(c) for c in val], Q)); vb = spread(rescore([dict(c) for c in val], P))
    return Q, best[0], vs, vb
def mutate(P, rng):
    Q = copy.deepcopy(P)
    Q["decay"] = rng.choice([.97, .98, .985, .99, .995]); Q["bin_pct"] = rng.choice([.0015, .0025, .004])
    Q["smooth"] = rng.choice([2, 3, 4]); Q["min_dist"] = rng.choice([.002, .004, .006, .01]); Q["max_dist"] = rng.choice([.06, .10])
    Q["lev"] = rng.choice([[[5,.14],[10,.30],[25,.26],[50,.18],[100,.12]], [[5,.25],[10,.35],[25,.25],[50,.10],[100,.05]], [[5,.05],[10,.20],[25,.30],[50,.25],[100,.20]]])
    Q["ls_weight"] = rng.choice([.4, .6, .8]); Q["candle_tilt"] = rng.choice([0, .08, .15]); return Q
def main():
    rng = random.Random(int(os.environ.get("SEED", 7)))
    P = liqmodel.load_params(); log("current params v", P["version"])
    hist = {}
    for c in liqdata.COINS:
        try: hist[c] = fetch_hist(c); log(c, len(hist[c]["candles"]), "candles", len(hist[c]["oi"]), "OI pts")
        except Exception as e: log(c, "fetch failed", e)
    t0 = time.time()
    with Pool(min(8, os.cpu_count() or 2)) as pool:
        base = run_all(hist, P, pool); log(f"baseline replay {len(base)} calls in {time.time()-t0:.0f}s")
        ts = sorted(c["t"] for c in base); t_split = ts[int(len(ts) * 2 / 3)]
        btr, bva = split(base, t_split); bt, bv = summarize(btr), summarize(bva)
        log(f"baseline train n={bt['n']} hit={bt['hit_rate']:.3f} E={bt['expectancy_r']:.3f} | val n={bv['n']} hit={bv['hit_rate']:.3f} E={bv['expectancy_r']:.3f}")
        cands = []
        for i in range(N_CAND):
            Q = mutate(P, rng); calls = run_all(hist, Q, pool); tr, va = split(calls, t_split)
            st, sv = summarize(tr), summarize(va); cands.append((st.get("expectancy_r", -9), Q, st, sv, calls))
            log(f"cand {i+1}/{N_CAND} train n={st['n']} E={st.get('expectancy_r',0):.3f} hit={st.get('hit_rate',0):.3f} | val E={sv.get('expectancy_r',0):.3f}")
    cands.sort(key=lambda x: -x[0]); top = [c for c in cands if c[2]["n"] >= MIN_N][:1]
    decision = {"adopted": False, "reason": "no candidate"}; newP = P; final_calls = base
    if top:
        _, Q, st, sv, calls = top[0]
        gain = sv["expectancy_r"] - bv["expectancy_r"]
        if st["expectancy_r"] > bt["expectancy_r"] and gain >= MIN_GAIN and sv["n"] >= MIN_N and sv["hit_rate"] >= bv["hit_rate"]:
            newP = Q; final_calls = calls; decision = {"adopted": True, "reason": f"val expectancy +{gain:.3f}R (n={sv['n']})"}
        else:
            decision = {"adopted": False, "reason": f"best-train candidate val gain {gain:+.3f}R, train {st['expectancy_r']:.3f} vs {bt['expectancy_r']:.3f}, val hit {sv['hit_rate']:.3f} vs {bv['hit_rate']:.3f} — below adoption bar (+{MIN_GAIN}R, n>={MIN_N})"}
    tr, va = split(final_calls, t_split)
    Q, sp_tr, sp_va, sp_va_old = calibrate(tr, va, newP, rng)
    cal = {"train_spread": sp_tr, "val_spread_new": sp_va, "val_spread_old": sp_va_old}
    if sp_va > max(0.03, sp_va_old):
        newP = Q; cal["adopted"] = True
    else: cal["adopted"] = False
    changed = decision["adopted"] or cal["adopted"]
    if changed:
        newP = copy.deepcopy(newP); newP["version"] = P["version"] + 1
        newP["created"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        newP["note"] = f"optimize.py: model {'changed' if decision['adopted'] else 'kept'}; confidence calibration {'changed' if cal['adopted'] else 'kept'}"
        os.makedirs(f"{HERE}/params_history", exist_ok=True)
        json.dump(newP, open(f"{HERE}/params_history/v{newP['version']}.json", "w"), indent=1)
        json.dump(newP, open(f"{HERE}/params.json.tmp", "w"), indent=1); os.replace(f"{HERE}/params.json.tmp", f"{HERE}/params.json")
    final_calls = rescore(final_calls, newP); tr, va = split(final_calls, t_split)
    per = {c: {"all": summarize([x for x in final_calls if x["coin"] == c]), "val": summarize([x for x in va if x["coin"] == c])} for c in liqdata.COINS}
    bj = {"ts": int(time.time() * 1000), "params_version": newP["version"], "hist_hours": HIST_H, "step_h": STEP, "lookback_h": LOOKBACK,
          "split_ts": t_split, "decision": decision, "calibration": cal, "changed": changed,
          "overall": {"all": summarize(final_calls), "train": summarize(tr), "val": summarize(va)}, "baseline_v": {"train": bt, "val": bv},
          "per_coin": per, "random_baseline_note": "With a 1.5x-distance stop, a driftless random walk hits the target first ~60% of the time; 'mirror_hit_rate' = same distance, opposite direction."}
    json.dump(bj, open(f"{DATA}/backtest.json", "w"), indent=1)
    log("decision:", decision, "calibration:", cal)
    import tracker; tracker.write_stats()
if __name__ == "__main__": main()
