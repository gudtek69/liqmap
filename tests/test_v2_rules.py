"""Safety fixes for map v2: finite ATR, shared cluster stop, late expiry, page/tracker cluster choice."""
import datetime, json, math, os, subprocess, sys, tempfile, textwrap, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("LIQMAP_DATA", tempfile.mkdtemp(prefix="liqmap-test-"))
os.environ.setdefault("LIQMAP_WEB", tempfile.mkdtemp(prefix="liqmap-web-"))
sys.path.insert(0, os.path.join(ROOT, "tracker"))
import liqmodel, tracker

NOW = 1_700_000_000_000
H = 3600000
HORIZON = 24 * H


def bars(n=20, now=NOW, high=1005, low=995, close=1000):
    out = []
    for i in range(n):
        t = now - (n - i) * H
        hi = high(i, n) if callable(high) else high
        out.append([t, 1000, hi, low, close, 1])
    return out


def cl(price, size=100, rel=1.0):
    return {"price": price, "size": size, "rel": rel, "dist": abs(price - 1000) / 1000, "side": "short" if price > 1000 else "long"}


class FiniteAtr(unittest.TestCase):
    def setUp(self):
        self.P = liqmodel.load_params()
        self.C = bars()
        self.opp = cl(960, 80, 0.8)

    def call(self, atr, target=1020, extra=None, opp=True):
        path = [cl(target)]
        clusters = path + ([extra] if extra else []) + ([self.opp] if opp else [])
        M = {"spot": 1000, "path": path, "cl": clusters}
        return liqmodel.call_v2(self.C, M, 0.0, self.P, atr)

    def test_expression_rejects_non_finite_atr(self):
        with open(os.path.join(ROOT, "tracker", "liqmodel.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("datr = D / atr if atr and math.isfinite(atr) and atr > 0 else None", src)
        with open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("Number.isFinite(atr)&&atr>0", js)
        for atr in (float("nan"), float("inf"), float("-inf"), 0, -1, None):
            v = self.call(atr)
            self.assertFalse(v["pass"], atr)
            self.assertIn("no ATR", v["why"])

    def test_non_finite_datr_and_asym_are_suppressed(self):
        v = self.call(10, target=float("inf"))
        self.assertFalse(v["pass"])
        self.assertIn("no ATR", v["why"])
        # The non-finite price is the only opposite cluster, so asym itself is non-finite.
        v = self.call(10, extra=cl(float("-inf"), 80, 0.5), opp=False)
        self.assertFalse(v["pass"])
        self.assertIn("bad data", v["why"])

    def test_finite_atr_still_passes(self):
        atr, _ = liqmodel.hourly_atr(self.C, NOW)
        self.assertAlmostEqual(atr, 10)
        v = self.call(atr)
        self.assertTrue(v["pass"], v["why"])
        self.assertAlmostEqual(v["target"], 1016)  # 80% of 20
        self.assertAlmostEqual(v["datr"], 2)
        self.assertAlmostEqual(v["asym"], 2)


class SharedStop(unittest.TestCase):
    def test_v2_uses_original_cluster_distance(self):
        entry, cluster, v2 = 1000.0, 1100.0, 1080.0
        # 870 is through the wrong 1.2×D stop (880) and short of the shared 1.5×D stop (850).
        shallow = [[0, 1000, 1010, 870, 1000, 1]]
        wrong = liqmodel.resolve("up", entry, v2, shallow, 1.5, HORIZON, 0, 300000, now_ms=1000)
        self.assertEqual(wrong["outcome"], "miss")
        old, new = tracker.grade("up", entry, cluster, v2, shallow, 1.5, HORIZON, 0, 1000)
        self.assertIsNone(old)
        self.assertIsNone(new)
        deep = [[0, 1000, 1010, 840, 1000, 1]]
        old, new = tracker.grade("up", entry, cluster, v2, deep, 1.5, HORIZON, 0, 1000)
        self.assertEqual((old["outcome"], new["outcome"]), ("miss", "miss"))
        self.assertEqual((old["r"], new["r"]), (-1.5, -1.5))
        touched = [[0, 1000, 1080, 900, 1050, 1]]
        old, new = tracker.grade("up", entry, cluster, v2, touched, 1.5, HORIZON, 0, 1000)
        self.assertIsNone(old)
        self.assertEqual(new["outcome"], "hit")
        self.assertEqual(new["r"], 1.0)
        # Down: a print at 1.2×D must not stop the v2 leg either.
        high = [[0, 1000, 1120, 990, 1000, 1]]
        old, new = tracker.grade("down", 1000, 900, 920, high, 1.5, HORIZON, 0, 1000)
        self.assertIsNone(old)
        self.assertIsNone(new)
        self.assertEqual(liqmodel.cluster_stop("up", 1000, 1100, 1.5), 850)
        self.assertEqual(liqmodel.cluster_stop("down", 1000, 900, 1.5), 1150)


class LateExpiry(unittest.TestCase):
    def test_grace_is_two_hours_past_the_horizon(self):
        self.assertEqual(liqmodel.EXPIRE_GRACE_MS, 2 * H)
        iv = 300000
        short = [[20 * H, 100, 101, 99, 102, 1]]
        kw = dict(direction="up", entry=100, target=110, after=short, inv_mult=1.5, horizon_ms=HORIZON, t0=0, iv_ms=iv)
        self.assertIsNone(liqmodel.resolve(**kw, now_ms=HORIZON + liqmodel.EXPIRE_GRACE_MS - 1))
        self.assertIsNone(liqmodel.resolve(**kw, now_ms=None))
        res = liqmodel.resolve(**kw, now_ms=HORIZON + liqmodel.EXPIRE_GRACE_MS)
        self.assertEqual(res["outcome"], "expired")
        self.assertAlmostEqual(res["r"], 0.2)  # last close inside the horizon, 102 vs 100 over a distance of 10
        # A candle that only opens at the horizon is not "inside" it.
        gapped = short + [[HORIZON, 100, 101, 99, 150, 1]]
        res = liqmodel.resolve("up", 100, 110, gapped, 1.5, HORIZON, 0, iv, now_ms=HORIZON)
        self.assertEqual(res["outcome"], "expired")
        self.assertAlmostEqual(res["r"], 0.2)
        # Full coverage still expires at the horizon, not only after the grace.
        covered = [[HORIZON - iv, 100, 101, 99, 103, 1]]
        self.assertIsNone(liqmodel.resolve("up", 100, 110, covered, 1.5, HORIZON, 0, iv, now_ms=HORIZON - 1))
        res = liqmodel.resolve("up", 100, 110, covered, 1.5, HORIZON, 0, iv, now_ms=HORIZON)
        self.assertEqual(res["outcome"], "expired")
        self.assertAlmostEqual(res["r"], 0.3)
        self.assertEqual(liqmodel.resolve("up", 100, 110, covered, 1.5, HORIZON, 0, iv, now_ms=None)["outcome"], "expired")
        # A hit is not rewritten into an expiry just because the clock is past 26h.
        hit = [[0, 100, 110, 99, 110, 1]]
        self.assertEqual(liqmodel.resolve("up", 100, 110, hit, 1.5, HORIZON, 0, iv, now_ms=30 * H)["outcome"], "hit")
        self.assertIsNone(liqmodel.resolve("up", 100, 110, [], 1.5, HORIZON, 0, iv, now_ms=30 * H))


class HitFilterAgreement(unittest.TestCase):
    def test_page_and_tracker_keep_the_same_cluster(self):
        C = bars()
        path = [cl(1040, 50, 1), cl(1020, 40, 0.8)]
        hits = [{"p": 1040, "t": NOW - H}]
        kept = liqmodel.filter_hit_path(path, hits, NOW)
        self.assertEqual([c["price"] for c in kept], [1020])
        # Boundaries match the page: strict < on both age and tolerance.
        # 2.5/1000 = 0.0025, which is not < tol, so the cluster stays.
        px = [cl(1000)]
        self.assertEqual([c["price"] for c in liqmodel.filter_hit_path(px, [{"p": 1002.5, "t": NOW - 1}], NOW)], [1000])
        self.assertEqual(liqmodel.filter_hit_path(px, [{"p": 1002.4, "t": NOW - 1}], NOW), [])
        self.assertEqual([c["price"] for c in liqmodel.filter_hit_path(px, [{"p": 1000, "t": NOW - liqmodel.HIT_WINDOW_MS}], NOW)], [1000])
        self.assertEqual(liqmodel.filter_hit_path(px, [{"p": 1000, "t": NOW - liqmodel.HIT_WINDOW_MS + 1}], NOW), [])
        self.assertEqual([c["price"] for c in liqmodel.filter_hit_path(px, [{"p": 1000, "t": NOW + H}], NOW)], [1000])
        atr, _ = liqmodel.hourly_atr(C, NOW)
        P = liqmodel.load_params()
        clusters = path + [cl(960, 80, 0.7)]
        blocked = liqmodel.call_v2(C, {"spot": 1000, "path": path, "cl": clusters}, 0.0, P, atr)
        chosen = liqmodel.call_v2(C, {"spot": 1000, "path": kept, "cl": clusters}, 0.0, P, atr)
        self.assertFalse(blocked["pass"])  # 40 / atr 10 = 4 > 3
        self.assertTrue(chosen["pass"], chosen["why"])
        self.assertAlmostEqual(chosen["target"], 1016)
        js = json.loads(subprocess.check_output(
            ["node", os.path.join(ROOT, "tests", "check_page_rules.js"), str(NOW)], cwd=ROOT, text=True))
        self.assertEqual(js["kept"], [1020])
        self.assertEqual(js["tol_edge"], [1000])
        self.assertEqual(js["tol_inside"], [])
        self.assertEqual(js["age_edge"], [1000])
        self.assertEqual(js["age_inside"], [])
        self.assertFalse(js["unfiltered"]["pass"])
        self.assertTrue(js["filtered"]["pass"])
        self.assertAlmostEqual(js["filtered"]["target"], chosen["target"])
        self.assertAlmostEqual(js["atr"], atr)
        self.assertFalse(js["nan"]["pass"])
        self.assertIn("no ATR", js["nan"]["why"])
        self.assertFalse(js["flat"]["pass"])
        self.assertIn("no ATR", js["flat"]["why"])
        self.assertFalse(js["asym"]["pass"])
        self.assertIn("bad data", js["asym"]["why"])
        self.assertEqual(js["call_price"], 1020)
        self.assertTrue(js["call_pass"])
        self.assertAlmostEqual(js["call_target"], 1016)
        self.assertTrue(js["valid_ok"])
        self.assertFalse(js["valid_missing"])
        self.assertFalse(js["valid_nan"])
        self.assertIn("1.08K", js["banner_pass"]["bc"])
        self.assertNotIn("1.10K", js["banner_pass"]["bc"])
        self.assertIn("Next target", js["banner_pass"]["bs"])
        self.assertIn("75% of cluster distance", js["banner_pass"]["bs"])
        self.assertIn("Shadow (old target)", js["banner_pass"]["bs"])
        self.assertEqual(js["banner_pass"]["bf"], "strong")
        self.assertEqual(js["banner_pass"]["bn"], "banner up")
        self.assertIn("No call (suppressed: no ATR)", js["banner_sup"]["bc"])
        self.assertEqual(js["banner_sup"]["bf"], "no call")
        self.assertEqual(js["banner_sup"]["bn"], "banner")
        self.assertIn("Shadow (old target)", js["banner_sup"]["bs"])
        self.assertTrue(any(t.startswith("target 1.08K") for t in js["chart_pass"]))
        self.assertTrue(any("shadow 1.10K" in t for t in js["chart_pass"]))
        self.assertTrue(any("1.00K → 1.08K" in t for t in js["chart_pass"]))
        self.assertTrue(any("No call (suppressed: no ATR)" in t for t in js["chart_sup"]))
        self.assertFalse(any(t.startswith("target ") for t in js["chart_sup"]))
        self.assertIn("No call (suppressed: no ATR)", js["overview"])
        self.assertIn("shadow", js["path_pass"])
        self.assertIn("1.08K", js["path_pass"])


class FeaturesAndUtc(unittest.TestCase):
    def test_prev_outcome_is_the_last_resolved_call(self):
        con = tracker.db()
        tracker.insert_call(con, {"ts": 100, "coin": "PREV", "price": 1, "dir": "up", "target": 2})
        con.execute("UPDATE calls SET outcome='miss', r=-1.5, tth_h=NULL, mae_pct=1, resolved_ts=150 WHERE coin='PREV' AND ts=100")
        tracker.insert_call(con, {"ts": 200, "coin": "PREV", "price": 1, "dir": "down", "target": 0.5})
        con.commit()
        self.assertEqual(tracker.previous_resolved(con, "PREV", 300), ("miss", -1.5))
        self.assertEqual(tracker.previous_resolved(con, "PREV", 100), (None, None))
        outcome, r = tracker.previous_resolved(con, "PREV", 400)
        tracker.insert_call(con, {"ts": 400, "coin": "PREV", "price": 1, "dir": "up", "target": 3, "prev_outcome": outcome, "prev_r": r})
        con.commit()
        self.assertEqual(con.execute("SELECT prev_outcome, prev_r FROM calls WHERE ts=400").fetchone(), ("miss", -1.5))
        con.close()

    def test_recent_hits_round_trip_into_stats(self):
        con = tracker.db()
        ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000) - 1000
        tracker.insert_call(con, {"ts": ts, "coin": "ZZ", "price": 1, "dir": "up", "target": 42})
        con.execute("UPDATE calls SET outcome='hit', r=1, tth_h=1, mae_pct=0, resolved_ts=? WHERE coin='ZZ' AND ts=?", (ts, ts))
        con.commit()
        con.close()
        tracker.write_stats()
        with open(os.path.join(os.environ["LIQMAP_DATA"], "stats.json"), encoding="utf-8") as f:
            st = json.load(f)
        self.assertTrue(any(h["coin"] == "ZZ" and h["p"] == 42 for h in st["recent_hits"]))

    def test_utc_day_ignores_host_timezone(self):
        script = textwrap.dedent("""
            import datetime, os, sys, tempfile, time
            os.environ["TZ"] = "America/New_York"
            os.environ["LIQMAP_DATA"] = tempfile.mkdtemp()
            os.environ["LIQMAP_WEB"] = tempfile.mkdtemp()
            time.tzset()
            sys.path.insert(0, "tracker")
            import tracker
            ts = int(datetime.datetime(2026, 10, 22, 2, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
            local = datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            if local == "2026-10-22":
                raise SystemExit("TZ did not move local midnight off UTC; cannot prove the grouping")
            if tracker.utc_day(ts) != "2026-10-22":
                raise SystemExit("utc_day returned " + tracker.utc_day(ts))
            print("ok")
        """)
        env = dict(os.environ)
        env["TZ"] = "America/New_York"
        out = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, text=True, capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        with open(os.path.join(ROOT, "tracker", "tracker.py"), encoding="utf-8") as f:
            self.assertIn("utc_day", f.read().split("def headline", 1)[1])


if __name__ == "__main__":
    unittest.main()
