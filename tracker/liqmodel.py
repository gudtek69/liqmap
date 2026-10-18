"""Liquidation-cluster model + call logic. Line-for-line port of model()/call() in index.html.
Both read the same params.json. Keep the two in sync."""
import math, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
def load_params(path=None):
    with open(path or os.path.join(HERE, "params.json")) as f: return json.load(f)
def nice(x):
    e = 10 ** math.floor(math.log10(x)); m = x / e
    return e * (1 if m < 1.5 else 2 if m < 2.25 else 2.5 if m < 3.5 else 5 if m < 7.5 else 10)
def sign(x): return (x > 0) - (x < 0)
def model(candles, oi, ls, spot, P, scale=1.0):
    """candles: [[t,o,h,l,c,v]], oi: [[t,usd]] sorted, ls: [[t,ratio]]. Returns dict like JS model()."""
    C = candles; n = len(C)
    if oi and len(oi) > 10:
        m = dict(oi); last = oi[-1][1]; dOI = []
        for i, c in enumerate(C):
            a = m.get(c[0]); b = m.get(c[0] + 3600000)
            if b is None and i == n - 1: b = last
            dOI.append((b - a) * scale if a is not None and b is not None else 0.0)
    else:
        avg = sum(c[5] for c in C) / n; dOI = [(c[5] - avg) * c[4] * 0.3 for c in C]
    lsm = dict(ls or []); pos = []  # [side, size, liq, dead]
    for i in range(n):
        t, o, h, l, c = C[i][:5]
        for p in pos:
            p[1] *= P["decay"]
            if not p[3] and ((p[0] > 0 and l <= p[2]) or (p[0] < 0 and h >= p[2])): p[3] = 1
        x = dOI[i]; tp = (h + l + c) / 3
        if x > 0:
            r = lsm.get(t, 1.2)
            lf = r / (1 + r) * P["ls_weight"] + (1 - P["ls_weight"]) / 2 + (P["candle_tilt"] if c > o else -P["candle_tilt"])
            lf = min(.8, max(.2, lf))
            for L, w in P["lev"]:
                pos.append([1, x * lf * w, tp * (1 - 1 / L + P["mm"]), 0])
                pos.append([-1, x * (1 - lf) * w, tp * (1 + 1 / L - P["mm"]), 0])
        elif x < 0:
            tot = sum(p[1] for p in pos if not p[3])
            if tot > 0:
                f = max(0, 1 + x / tot)
                for p in pos: p[1] *= f
    bw = nice(spot * P["bin_pct"]); R = P["range"]
    lo = math.floor(spot * (1 - R) / bw) * bw; nb = math.ceil(spot * 2 * R / bw)
    tot = [0.0] * nb
    for p in pos:
        if p[3]: continue
        if (p[0] > 0 and p[2] >= spot) or (p[0] < 0 and p[2] <= spot): continue
        k = math.floor((p[2] - lo) / bw)
        if 0 <= k < nb: tot[k] += p[1]
    w = P["smooth"]; sm = []
    for i in range(nb):
        s = ww = 0
        for j in range(-w, w + 1):
            k = i + j
            if 0 <= k < nb: q = w + 1 - abs(j); s += tot[k] * q; ww += q
        sm.append(s / ww)
    g = lambda k: tot[k] if 0 <= k < nb else None
    cl = []
    for i in range(2, nb - 2):
        if sm[i] > 0 and sm[i] >= sm[i-1] and sm[i] > sm[i+1] and sm[i] >= sm[i-2] and sm[i] > sm[i+2]:
            s = sum(g(i + j) or 0 for j in range(-4, 5))
            pk = i
            for j in range(-3, 4):
                v = g(i + j)
                if v is not None and v > tot[pk]: pk = i + j
            price = lo + (pk + .5) * bw
            cl.append({"price": price, "size": s, "side": "short" if price > spot else "long", "dist": abs(price - spot) / spot})
    cl = sorted([c for c in cl if P["min_dist"] < c["dist"] < P["max_dist"]], key=lambda c: -c["size"])
    mx = cl[0]["size"] if cl else 1
    for c in cl: c["rel"] = c["size"] / mx
    above = [c for c in cl if c["price"] > spot][:4]; below = [c for c in cl if c["price"] < spot][:4]
    score = lambda c: c["size"] / math.sqrt(c["dist"] * 100)
    path = []; cand = sorted(above + below, key=lambda c: -score(c))
    if cand:
        first = cand[0]; path.append(first)
        opp = sorted([c for c in (below if first["price"] > spot else above) if c["rel"] > P["opp_min_rel"]], key=lambda c: -score(c))
        if opp:
            sec = opp[0]; path.append(sec)
            th = sorted([c for c in (above if sec["price"] > spot else below) if c is not first and abs(c["price"] - spot) > abs(sec["price"] - spot) and c["size"] > sec["size"] * P["third_min_ratio"]], key=lambda c: -score(c))
            if th: path.append(th[0])
        else:
            nx = [c for c in (above if first["price"] > spot else below) if abs(c["price"] - spot) > abs(first["price"] - spot) and c["size"] > first["size"] * P["next_min_ratio"]]
            if nx: path.append(nx[0])
    return {"spot": spot, "bw": bw, "cl": cl, "path": path}
def call(candles, M, funding, P):
    """Same as JS call() minus browser hit-tracking. Returns None if no target."""
    if not M["path"]: return None
    spot = M["spot"]; t = M["path"][0]; up = t["price"] > spot; pct = (t["price"] - spot) / spot * 100
    K = P["conf"]; d = 1 if up else -1
    sc = t["rel"] / math.sqrt(max(.3, abs(pct)))
    l4 = candles[-4:]; l1 = candles[-1]
    m4 = (l4[-1][4] - l4[0][1]) / l4[0][1]; m1 = (l1[4] - l1[1]) / l1[1]
    sc += (K["m4_agree"] if sign(m4) == d else K["m4_disagree"]) + (K["m1_agree"] if sign(m1) == d else K["m1_disagree"])
    if abs(funding) > K["funding_min"]: sc += K["funding"] if (funding > 0) != up else -K["funding"]
    conf = "strong" if sc > K["strong"] else "moderate" if sc > K["moderate"] else "weak"
    return {"dir": "up" if up else "down", "target": t["price"], "then": M["path"][1]["price"] if len(M["path"]) > 1 else None,
            "pct": pct, "rel": t["rel"], "score": sc, "conf": conf, "m4": m4}
def resolve(direction, entry, target, after, inv_mult, horizon_ms, t0, iv_ms=3600000):
    """after: candles strictly after call time [[t,o,h,l,c,v]] (any interval). Hit = target touched before price moves
    inv_mult*|distance| against. Same-candle ambiguity counts as miss (conservative).
    Returns dict(outcome, tth_h, mae_pct, r) or None if unresolved and horizon not yet elapsed."""
    up = direction == "up"; dist = abs(target - entry); inv = entry - inv_mult * dist if up else entry + inv_mult * dist
    mae = 0.0
    for c in after:
        if c[0] >= t0 + horizon_ms: break
        adverse = (entry - c[3]) if up else (c[2] - entry); mae = max(mae, adverse)
        hit = c[2] >= target if up else c[3] <= target; bad = c[3] <= inv if up else c[2] >= inv
        if bad: return {"outcome": "miss", "tth_h": None, "mae_pct": mae / entry * 100, "r": -inv_mult}
        if hit: return {"outcome": "hit", "tth_h": (c[0] + iv_ms - t0) / 3600000, "mae_pct": mae / entry * 100, "r": 1.0}
    if after and after[-1][0] >= t0 + horizon_ms - 3600000:
        last = [c for c in after if c[0] < t0 + horizon_ms][-1][4]
        return {"outcome": "expired", "tth_h": None, "mae_pct": mae / entry * 100, "r": ((last - entry) if up else (entry - last)) / dist}
    return None
