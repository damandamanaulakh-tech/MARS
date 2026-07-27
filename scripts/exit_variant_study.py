"""Exit-variant study — audit finding #4 (2026-07-27).

THE PROBLEM (verified in code, trading/exits.py:82-103): exits are checked in
order stop -> target -> trail. With the live momentum bracket (stop -5%,
target +12%) the -15% trailing stop can only fire while price is above the
stop AND below the target, which requires peak > entry*1.1176 — but the +12%
target closes the trade at entry*1.12 first. The trail's entire arming window
is peak in (+11.76%, +12.00%), evaluated once a day. It is effectively dead:
a stock can run +11% and fall all the way back to -5% with nothing in between.
(The one exception: score-decay tightening cuts the trail to -9%, which arms
above peak +4.4%.)

THE QUESTION (owner's #4): "either widen the target or tighten the trail so an
exit exists between -5% and +12%." Which, and by how much? Numbers, not
opinions — same discipline as docs/PARAM_SEARCH_V1..4.

VARIANTS
  A  baseline        stop -5, target +12, trail -15   (live today; trail dead)
  B6 tight trail     stop -5, target +12, trail -6    (arms above peak +1.1%)
  B8 tight trail     stop -5, target +12, trail -8    (arms above peak +3.3%)
  B10 tight trail    stop -5, target +12, trail -10   (arms above peak +5.6%)
  C  breakeven       A + stop lifts to entry once peak >= +5%
  D  wider target    stop -5, target +20, trail -15   (trail arms above +17.6%)
  D15 wider target   stop -5, target +15, trail -12   (trail arms above +13.6%)

METHOD: trading/backtest.run_backtest — the REAL engine replay: real
check_exit, real scorers, real sizing, real costs (0.05% slippage/side, STT,
DP). Only the exit parameters differ between variants. Variant C is the one
rule that does not exist in exits.py yet; it is simulated by a wrapper around
check_exit that lifts position.stop_price to entry when the running peak
clears the trigger — exactly what the wired rule would do.

DATA: data/history_upstox_export_2026-07-19.zip (191 tickers x 12y, the app's
own Upstox store) + data/index_daily_15y.csv for the Nifty benchmark. Fully
offline, byte-reproducible.

WINDOWS (same spirit as v3's unseen windows):
  2019 | 2020-2021 | 2022-2023 | 2024-2026H1

CAVEATS carried from the harness: survivorship-biased universe (current
members only), fundamentals neutral-50, no news. Same bias for every variant,
so the COMPARISON is fair even where the levels are optimistic.

Run: python scripts/exit_variant_study.py
Writes docs/exit_variant_study_progress.jsonl + docs/exit_variant_study_results.json
"""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from datetime import datetime

import pandas as pd

sys.path.insert(0, ".")

from trading.config import Config           # noqa: E402
import trading.backtest as bt               # noqa: E402
from trading.exits import check_exit as real_check_exit  # noqa: E402

ZIP = "data/history_upstox_export_2026-07-19.zip"
IDX = "data/index_daily_15y.csv"
OUT_JSONL = "docs/exit_variant_study_progress.jsonl"
OUT_JSON = "docs/exit_variant_study_results.json"

WINDOWS = [
    ("2019", "2019-01-01", "2019-12-31"),
    ("2020-21", "2020-01-01", "2021-12-31"),
    ("2022-23", "2022-01-01", "2023-12-31"),
    ("2024-26H1", "2024-01-01", "2026-06-30"),
]

# name -> (stop, target, trail, breakeven_trigger or None)
VARIANTS = {
    "A_baseline_-5_+12_t15": (-0.05, 0.12, -0.15, None),
    "B6_trail-6":            (-0.05, 0.12, -0.06, None),
    "B8_trail-8":            (-0.05, 0.12, -0.08, None),
    "B10_trail-10":          (-0.05, 0.12, -0.10, None),
    "C_breakeven_at_+5":     (-0.05, 0.12, -0.15, 0.05),
    "D_target+20_t15":       (-0.05, 0.20, -0.15, None),
    "D15_target+15_t12":     (-0.05, 0.15, -0.12, None),
}


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


def make_cfg(tickers, stop, target, trail) -> Config:
    cfg = Config(mode="nifty200_momentum", universe=tickers,
                 state_dir="/tmp/exit_variant_study")
    cfg.apply_mode_preset()          # then override ONLY the exit knobs:
    cfg.stop_loss_pct = stop
    cfg.take_profit_pct = target
    cfg.trailing_stop_pct = trail
    return cfg


def breakeven_wrapper(trigger: float):
    """The variant-C rule: once peak clears entry*(1+trigger), the stop can
    never be below entry again — a winner cannot turn into a loser."""
    def wrapped(position, current_price, current_score, cfg, now=None, history=None):
        peak = max(position.peak_price, current_price)
        if peak >= position.entry_price * (1.0 + trigger):
            snap = getattr(position, "stop_price", 0.0) or 0.0
            position.stop_price = max(snap, position.entry_price)
        return real_check_exit(position, current_price, current_score, cfg,
                               now=now, history=history)
    return wrapped


def main() -> None:
    hist = load_history()
    tickers = sorted(t for t in hist if t != "^NSEI")
    print(f"{len(tickers)} tickers loaded; running "
          f"{len(VARIANTS)} variants x {len(WINDOWS)} windows")

    results: dict = {"generated": datetime.utcnow().isoformat(),
                     "variants": {}, "windows": [w[0] for w in WINDOWS]}
    prog = open(OUT_JSONL, "a")

    for vname, (stop, target, trail, be) in VARIANTS.items():
        bt.check_exit = breakeven_wrapper(be) if be else real_check_exit
        vres = []
        for wname, ws, we in WINDOWS:
            t0 = time.time()
            cfg = make_cfg(tickers, stop, target, trail)
            r = bt.run_backtest(cfg, ws, we, universe=tickers,
                                preloaded_history=hist)
            reasons: dict = {}
            for t in r.trades:
                reasons[t.reason] = reasons.get(t.reason, 0) + 1
            row = {
                "variant": vname, "window": wname,
                "return_pct": round(r.total_return_pct, 2),
                "alpha_pct": round(r.alpha_pct, 2),
                "bench_pct": round(r.benchmark_total_return_pct, 2),
                "max_dd_pct": round(r.max_drawdown_pct, 2),
                "trades": r.num_trades,
                "win_rate_pct": round(r.win_rate_pct, 1),
                "sharpe": round(r.sharpe_ratio, 2),
                "exit_reasons": reasons,
                "secs": round(time.time() - t0, 1),
            }
            vres.append(row)
            prog.write(json.dumps(row) + "\n")
            prog.flush()
            print(f"  {vname:24s} {wname:10s} ret {row['return_pct']:+7.2f}% "
                  f"dd {row['max_dd_pct']:+7.2f}% trades {row['trades']:3d} "
                  f"({row['secs']:.0f}s)  exits={reasons}")
        results["variants"][vname] = vres

    bt.check_exit = real_check_exit
    with open(OUT_JSON, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nwrote {OUT_JSON}")


if __name__ == "__main__":
    main()
