"""Breadth-gate study (A3 — the one idea from the early-July review never
built anywhere). PREREGISTERED before the first run, no tuning afterwards:

  GATE: new entries allowed only when >50% of the panel's tickers closed
  above their own 200-day moving average on the prior day. Exits always
  allowed. Everything else = today's live rules (stop -5 / target +12 /
  trail -15 / floor 55 / regime brake / abs-momentum gate).

Baseline = variant A of docs/exit_variant_study_results.json (same harness,
same windows, same data — directly comparable).

Implementation: breadth series precomputed from the 191-ticker panel;
backtest._regime_allows_entries is wrapped to AND-in the breadth check.
Same arena: signal replay via trading/backtest.run_backtest, preloaded
offline data, four OOS windows. Writes docs/breadth_gate_results.json.
"""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile

import pandas as pd

sys.path.insert(0, ".")

ZIP = "data/history_upstox_export_2026-07-19.zip"
IDX = "data/index_daily_15y.csv"
OUT = "docs/breadth_gate_results.json"
BREADTH_MIN = 0.50          # >50% of universe above its own 200DMA
DMA = 200

WINDOWS = [
    ("2019",      "2019-01-01", "2019-12-31"),
    ("2020-21",   "2020-01-01", "2021-12-31"),
    ("2022-23",   "2022-01-01", "2023-12-31"),
    ("2024-26H1", "2024-01-01", "2026-07-17"),
]


def load_history() -> dict:
    z = zipfile.ZipFile(ZIP)
    out = {}
    for name in z.namelist():
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(z.read(name)), parse_dates=["Date"])
            out[name[:-4]] = df.set_index("Date").sort_index()
    idx = pd.read_csv(IDX, parse_dates=["trade_date"], dayfirst=True)
    idx = idx.set_index("trade_date").sort_index()
    out["^NSEI"] = pd.DataFrame({
        "Open": idx["nifty_open"], "High": idx["nifty_high"],
        "Low": idx["nifty_low"], "Close": idx["nifty_close"], "Volume": 0.0,
    })
    return out


def breadth_series(hist: dict) -> pd.Series:
    closes = pd.DataFrame({t: df["Close"] for t, df in hist.items()
                           if t != "^NSEI"}).sort_index()
    above = closes.gt(closes.rolling(DMA).mean())
    valid = closes.notna() & closes.rolling(DMA).mean().notna()
    return above.sum(axis=1) / valid.sum(axis=1).clip(lower=1)


def main() -> None:
    t0 = time.time()
    hist = load_history()
    tickers = sorted(t for t in hist if t != "^NSEI")
    breadth = breadth_series(hist)
    print(f"{len(tickers)} tickers, breadth series {breadth.index.min().date()}"
          f" -> {breadth.index.max().date()} ({time.time()-t0:.0f}s)")

    import trading.backtest as bt
    from trading.config import Config

    orig_gate = bt._regime_allows_entries

    def gated(bench_as_of):
        ok = orig_gate(bench_as_of)
        if not ok:
            return False
        try:
            day = bench_as_of.index[-1]
            b = breadth.loc[:day]
            return bool(len(b) and b.iloc[-1] > BREADTH_MIN)
        except Exception:
            return ok      # breadth unavailable -> fall back to live behavior
    bt._regime_allows_entries = gated

    results = {}
    try:
        for name, ws, we in WINDOWS:
            t1 = time.time()
            cfg = Config(mode="nifty200_momentum", universe=tickers,
                         state_dir="/tmp/breadth_gate_study")
            cfg.apply_mode_preset()
    # ERA PIN (AUDIT FIX D3, 2026-08-09): the committed results in docs/ were
    # generated 2026-07-27 at entry floor 55; the live preset later moved to
    # 62, which silently changed what a "re-run" computes. Pinning the era
    # keeps the committed evidence regenerable; delete the pin (and the
    # committed *_progress.jsonl cache!) to study the CURRENT rules instead.
    cfg.min_score_to_enter = 55.0
            r = bt.run_backtest(cfg, ws, we, universe=tickers,
                                preloaded_history=hist)
            results[name] = {
                "return_pct": round(r.total_return_pct, 2),
                "max_dd_pct": round(r.max_drawdown_pct, 2),
                "trades": r.num_trades,
                "win_rate_pct": round(r.win_rate_pct, 1),
            }
            print(name, results[name], f"({time.time()-t1:.0f}s)")
    finally:
        bt._regime_allows_entries = orig_gate

    with open(OUT, "w") as f:
        json.dump({"preregistered": True, "gate": f"breadth > {BREADTH_MIN}",
                   "windows": results}, f, indent=2)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
