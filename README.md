# liqmap: liquidation map predictor (BTC / ETH / SOL)

A small, dependency-free toy that estimates where leveraged positions on crypto perps are likely to get liquidated, draws that as a heatmap, and estimates the **next liquidity sweep**: which cluster price is likely to run into first. It also **grades its own calls**. A tracker logs each prediction and later checks whether price hit the target before a stop level, and an optimizer re-tunes the model on a walk-forward backtest.

> **Honest accuracy note.** Over about **1,500 tracked live calls** (as of Oct 2026, from the author's local run; that data isn't included), about **59%** hit their target first. That is close to the **~60% you would expect from a random walk** with the same stop distance (target vs. a 1.5× adverse move), so **this is not a proven edge**. (The optimizer also prints a backtest `mirror_hit_rate`, around 43%, which tests the same distance in the opposite direction. That gap is not proof of an edge either; the ~60% random-walk baseline is the fairer comparison.) Treat it as a visualization and research toy. **Not financial advice. For fun and research only.**

## What it does

- **Map page** (`web/index.html`): a single static HTML file. For each coin it shows hourly candles, an estimated liquidation-density heatmap within ±15% of spot, the largest clusters, a predicted path (e.g. `68.2K → 69.5K → 66.9K`), a call banner with a confidence grade (strong / moderate / weak), and the backtest and live hit rates from `stats.json`.
- **Data fetcher and local server** (`web/fetcher.py`): every 60 s it pulls public market data and writes `web/data.json`, then serves the `web/` folder at `http://127.0.0.1:8787`. If `data.json` is missing or stale, the page falls back to calling Hyperliquid directly from the browser (no OKX data in that mode).
- **Tracker / grader** (`tracker/tracker.py`): every 5 min it computes each coin's call with the same model and params as the page. It logs a call to SQLite when the call changes (or hourly) and resolves open calls on 5-minute candles. It then writes `stats.json` with hit rate, expectancy (R), time-to-hit, and drawdown, both overall and by confidence.
- **Optimizer** (`tracker/optimize.py`): runs a walk-forward backtest over ~30 days of hourly data and a conservative random search over model parameters. It splits the data into train (older 2/3) and validation (newer 1/3). A new parameter set is adopted only if validation expectancy improves by ≥ 0.05R with ≥ 80 validation calls and the hit rate doesn't drop. Confidence thresholds are recalibrated and kept only if "strong" beats "weak" on validation. Each adoption bumps `params.json` to a new version (the shipped file is **v8**) and saves a copy in `tracker/params_history/`.
- **Model** (`tracker/liqmodel.py`): a line-for-line Python port of `model()` / `call()` in `index.html`. Both read the same `params.json`. **Keep the two in sync** if you change one.

## How the model works

1. Hourly **open-interest changes** (OKX OI history, scaled up to include Hyperliquid OI) are treated as new positions opened at that candle's typical price. Falling OI shrinks existing positions proportionally.
2. New OI is split into longs and shorts using the OKX **long/short account ratio** plus a small tilt from the candle's colour.
3. Each slice is spread over assumed **leverage tiers** (5×, 10×, 25×, 50×, 100× with weights from `params.json`). Its liquidation price is roughly `entry × (1 ∓ 1/L ± maintenance margin)`.
4. Positions decay over time (`decay`) and are removed once price trades through their liquidation level.
5. The surviving liquidation levels are binned (`bin_pct` of spot), smoothed, and local peaks become **clusters**.
6. The **target** is the cluster with the best size / √distance score. The "then" target is the strongest cluster on the opposite side, or the next one further out.
7. **Confidence** = cluster strength / √distance, adjusted by 4h and 1h momentum agreement and funding, then bucketed by calibrated thresholds.
8. **Grading**: a call is a *hit* if price touches the target before moving `invalidation_mult` (1.5) × the target distance against it, within `horizon_h` (24h). A same-candle tie counts as a miss. Unresolved calls *expire* at the horizon.

This is a heuristic. Real positions, margin modes, cross-margin, and exchange liquidation engines all differ from these assumptions.

## Data sources (all public, no API keys)

| Source | Used for |
|---|---|
| Hyperliquid info API, `https://api.hyperliquid.xyz/info` | hourly / 5m candles, mark price, open interest, funding, funding history |
| OKX rubik public stats, `https://www.okx.com/api/v5/rubik/stat/contracts/...` | hourly OI history, long/short account ratio (`<COIN>-USDT-SWAP`) |

Be gentle with rate limits. OKX rubik allows about 5 requests per 2 s, and the scripts sleep between calls.

## Requirements

- Python 3.9+ (standard library only, no `pip install` needed)
- Any modern browser

## Run it

```bash
cp .env.example .env          # optional; edit coins/port
./start.sh                    # starts tracker + fetcher/server in the background (logs in ./logs)
# open http://127.0.0.1:8787/   (add #ETH or #SOL to pick a coin)
```

Or run the pieces by hand:

```bash
python3 web/fetcher.py --once        # fetch data.json once and exit
python3 web/fetcher.py               # fetch every 60 s + serve web/ on :8787
python3 tracker/tracker.py --once    # one tracker cycle (log + resolve calls, write stats.json)
python3 tracker/tracker.py           # loop every 5 min
python3 tracker/tracker.py --stats   # only rebuild stats.json
python3 tracker/optimize.py          # backtest + parameter search (a few minutes; uses multiprocessing)
```

Opening `web/index.html` straight from disk also works, using the browser-only Hyperliquid fallback. For the full model with OKX OI and L/S data, use the fetcher.

## Configuration

Everything is set with environment variables (see `.env.example`). None of them are secrets.

| Variable | Default | Meaning |
|---|---|---|
| `LIQMAP_COINS` | `BTC,ETH,SOL` | Coins to fetch / track. Each needs a Hyperliquid perp and an OKX `<COIN>-USDT-SWAP` contract. |
| `LIQMAP_HOST` / `LIQMAP_PORT` | `127.0.0.1` / `8787` | Local web server bind address |
| `LIQMAP_REFRESH` | `60` | Seconds between data fetches |
| `LIQMAP_WEB` | `./web` | Where the tracker copies `stats.json` / `params.json` for the page |
| `LIQMAP_TRACKER` | `./tracker` | Fallback folder the server reads `stats.json` / `params.json` from |
| `LIQMAP_DATA` | `./tracker/data` | `tracker.db`, `backtest.json`, `stats.json`, history cache |
| `HIST_H`, `STEP`, `N_CAND`, `SEED` | `720`, `3`, `24`, `7` | Optimizer: history hours, replay step, candidates, RNG seed |

Model parameters live in `tracker/params.json`: leverage tiers, decay, bin size, distance limits, confidence weights and thresholds, the invalidation multiple, and the horizon. The page loads the same file, so edits apply to both.

## Files

```
web/index.html        map page (model + chart + track record), no build step
web/fetcher.py        public-data fetcher + tiny static server
tracker/liqdata.py    Hyperliquid / OKX public API helpers
tracker/liqmodel.py   Python port of the page's model + call + resolve logic
tracker/tracker.py    live call logger / grader -> data/tracker.db, stats.json
tracker/optimize.py   walk-forward backtest + conservative param search -> params.json, data/backtest.json
tracker/params.json   current model parameters (v8)
start.sh              start tracker + fetcher in the background
```

## Limitations

- About 59% live hit rate is roughly the random-walk baseline for this stop distance. The optimizer's adoption bar is deliberately strict, and "strong" vs. "weak" separation is small and unstable.
- Liquidation levels are **inferred**, not observed. Nobody publishes real per-position liquidation prices.
- Browser-side "last target hit" tracking uses `localStorage` and only knows what that browser has seen.
- Read-only: there is no trading code here, and none should be added to it without serious risk controls.

## License

MIT. See [LICENSE](LICENSE). Not financial advice.
