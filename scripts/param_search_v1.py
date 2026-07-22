"""Walk-forward parameter search v1 — the first systematic search for a
positive-outcome configuration, on the committed 10-year clean set.

Method (no curve-fitting allowed to leak):
  * Universe: the 10 tickers with full 2016-2026 coverage.
  * Benchmark: equal-weight basket of the same 10 (self-basket — stated
    limitation; no index series exists in the committed data).
  * 4 walk-forward windows. For each: every config runs on the OPTIMIZE
    span (past data only), configs are ranked by Sharpe, and ONLY the
    winner is then run once on the unseen VALIDATE span. Out-of-sample
    numbers are the only numbers that count.
  * Grid (36): quality_blend {0, .35, .6} x score floor {50, 60}
    x exits {momentum defaults, no-target trail-only, tight trail}
    x candle gate {on, off}.
  * Costs are modelled inside run_backtest (STT + slippage + brokerage).
  * Survivorship: these 10 survived into 2026 — every result is tagged
    SURVIVORSHIP-BIASED by the PIT layer and must be read as optimistic.

Writes: scratch progress JSONL + final JSON. The wrapper commits a
human-readable report after completion.
"""

from __future__ import annotations

import json
import sys
import time
import traceback

import pandas as pd

sys.path.insert(0, ".")
from trading.backtest import run_backtest
from trading.config import Config

DATA = "data/history_15stock"
TICKERS = ["BHARTIARTL", "DRREDDY", "HDFCBANK", "HINDUNILVR", "INFY",
           "MARUTI", "RELIANCE", "SBIN", "SUNPHARMA", "TATASTEEL"]
OUT_JSONL = "docs/param_search_v1_progress.jsonl"
OUT_JSON = "docs/param_search_v1_results.json"

WINDOWS = [
    {"name": "W1_2019",   "opt": ("2016-03-01", "2018-12-31"), "val": ("2019-01-01", "2019-12-31")},
    {"name": "W2_2020",   "opt": ("2016-03-01", "2019-12-31"), "val": ("2020-01-01", "2020-12-31")},
    {"name": "W3_2022",   "opt": ("2016-03-01", "2021-12-31"), "val": ("2022-01-01", "2022-12-31")},
    {"name": "W4_2024_25","opt": ("2016-03-01", "2023-12-31"), "val": ("2024-01-01", "2026-05-31")},
]

EXIT_STYLES = {
    "mom_default": {},                                            # -12/+30 trail -15
    "trail_only":  {"take_profit_pct": 9.99},                     # never target-out, ride trend
    "tight_trail": {"trailing_stop_pct": -0.10},
}


def load_history() -> dict:
    hist = {}
    for t in TICKERS:
        df = pd.read_csv(f"{DATA}/{t}.csv", parse_dates=["Date"], index_col="Date")
        hist[t + ".NS"] = df.astype("float32")
    # equal-weight basket of normalized closes as the benchmark series
    closes = pd.DataFrame({t: hist[t + ".NS"]["Close"] for t in TICKERS}).dropna(how="all")
    closes = closes.ffill()
    basket = (closes / closes.iloc[0]).mean(axis=1) * 100.0
    bdf = pd.DataFrame({"Open": basket, "High": basket, "Low": basket,
                        "Close": basket, "Volume": 0.0}).astype("float32")
    hist["BASKET"] = bdf
    return hist


def make_cfg(blend: float, floor: float, exits: str, candle: bool) -> Config:
    cfg = Config()
    cfg.mode = "nifty200_momentum"
    cfg.apply_mode_preset()
    cfg.disable_exits = False
    cfg.quality_blend_pct = blend
    cfg.min_score_to_enter = floor
    cfg.candle_confirm_enabled = candle
    for k, v in EXIT_STYLES[exits].items():
        setattr(cfg, k, v)
    # 10-ticker universe needs wider per-position sizing to deploy at all
    cfg.max_position_pct = 0.10
    cfg.max_positions = 8
    cfg.max_sector_positions = 99
    cfg.max_sector_exposure_pct = 1.0
    return cfg


def basket_return(hist: dict, start: str, end: str) -> float:
    b = hist["BASKET"]["Close"]
    b = b[(b.index >= start) & (b.index <= end)]
    return float((b.iloc[-1] / b.iloc[0] - 1) * 100) if len(b) > 1 else 0.0


def one_run(hist, cfg, start, end) -> dict:
    r = run_backtest(cfg, start, end, universe=[t + ".NS" for t in TICKERS],
                     benchmark="BASKET", preloaded_history=hist)
    return {"ret": round(r.total_return_pct, 2), "sharpe": round(r.sharpe_ratio, 3),
            "dd": round(r.max_drawdown_pct, 2), "trades": r.num_trades,
            "time_in_mkt": round(r.time_in_market_pct, 1)}


def log(rec: dict) -> None:
    with open(OUT_JSONL, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def main() -> None:
    hist = load_history()
    grid = [{"blend": b, "floor": f, "exits": e, "candle": c}
            for b in (0.0, 0.35, 0.6) for f in (50.0, 60.0)
            for e in EXIT_STYLES for c in (True, False)]
    log({"event": "start", "grid": len(grid), "windows": len(WINDOWS)})
    results = {"windows": [], "grid_size": len(grid)}
    t0 = time.time()

    for w in WINDOWS:
        opt_scores = []
        for gi, g in enumerate(grid):
            try:
                cfg = make_cfg(**g)
                s = one_run(hist, cfg, *w["opt"])
                opt_scores.append({"cfg": g, **s})
                log({"event": "opt", "window": w["name"], "i": gi, **g, **s,
                     "elapsed_min": round((time.time() - t0) / 60, 1)})
            except Exception as e:
                log({"event": "opt_fail", "window": w["name"], "i": gi, **g,
                     "err": f"{type(e).__name__}: {str(e)[:90]}"})
        if not opt_scores:
            continue
        opt_scores.sort(key=lambda r: (-r["sharpe"], -r["ret"]))
        winner = opt_scores[0]
        try:
            val = one_run(hist, make_cfg(**winner["cfg"]), *w["val"])
        except Exception as e:
            val = {"err": f"{type(e).__name__}: {str(e)[:90]}"}
        bench_val = basket_return(hist, *w["val"])
        wrec = {"window": w["name"], "opt_span": w["opt"], "val_span": w["val"],
                "winner_cfg": winner["cfg"], "winner_opt_stats": {k: winner[k] for k in ("ret", "sharpe", "dd", "trades")},
                "validation": val, "basket_buyhold_val": round(bench_val, 2),
                "oos_alpha": (round(val["ret"] - bench_val, 2) if "ret" in val else None),
                "top3_opt": opt_scores[:3]}
        results["windows"].append(wrec)
        log({"event": "window_done", **{k: wrec[k] for k in ("window", "winner_cfg", "validation", "basket_buyhold_val", "oos_alpha")}})

    alphas = [w["oos_alpha"] for w in results["windows"] if w["oos_alpha"] is not None]
    results["summary"] = {
        "windows_run": len(results["windows"]),
        "positive_oos_alpha_windows": sum(1 for a in alphas if a > 0),
        "avg_oos_alpha": round(sum(alphas) / len(alphas), 2) if alphas else None,
        "runtime_min": round((time.time() - t0) / 60, 1),
        "caveats": ["self-basket benchmark (no index series in committed data)",
                    "SURVIVORSHIP-BIASED: 10 tickers that lived to 2026",
                    "10-ticker breadth — momentum's natural edge needs wider universe"],
    }
    with open(OUT_JSON, "w") as fh:
        json.dump(results, fh, indent=2)
    log({"event": "done", **results["summary"]})


if __name__ == "__main__":
    main()
