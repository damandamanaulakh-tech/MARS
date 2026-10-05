"""Two exit fixes from the 19-name replay (owner 2026-10-05: "test A and B
on the 12 year panel").

  A  a stop that widens with the stock's own daily move:
       stop distance = max(5%, k x ATR14 / price), capped at 15%
       k in {1.5, 2.0}
  B  a wait before re-buying a stock that was just stopped out:
       after a STOP_LOSS exit the same ticker cannot be bought for N
       trading days, N in {5, 10, 20}
  base  the live rule: fixed -5% stop, re-buy allowed the next day.

WHY THE HARNESS IS "LIVE-LIKE", applied to EVERY arm (base included):
  * stops and targets are checked against the day's LOW and HIGH, not only
    the close — the live app checks every 30 minutes, so an intraday dip
    through the stop is a real stop live. Fill = the stop price, or the
    open when the day gaps through it (stop first when both are touched —
    the conservative order).
  * targets are k x ATR per stock (trading/targets.py), as live since
    2026-09-10; the stock backtest still uses a flat +20%.
  Everything else is trading/backtest.run_backtest unchanged: real scorers,
  live floor 62, live bracket, real costs, the committed 191-ticker panel.

PRE-REGISTERED DECISION RULE (written before any run):
  1. TRAIN = 2015-01-01 .. 2020-06-30 (three windows). For A and for B pick
     the ONE setting with the best total train return.
  2. TEST = 2020-07-01 .. 2026-06-30 (three windows). The picked setting
     passes only if it beats base on total return in TRAIN AND in TEST,
     and its worst-window drawdown is no worse than base by more than
     2 points. Anything else = not earned, nothing wired.
  Test windows are never used to choose.

NOT modelled (same for every arm, so the comparison stays fair): breadth
gate, category cash shares, live slab ladder (backtest sizes at the slab),
danger layer, rotation, Ocean; the backtest's regime brake is ON while live
runs it OFF. Panel = today's survivors.

Usage: python scripts/stop_cooldown_study.py <ARM>
  ARM in base, A15, A20, B5, B10, B20
Writes research/stop_cooldown_study/<ARM>.json, one window at a time
(a killed run resumes at the next window).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from exit_variant_study import load_history, make_cfg  # noqa: E402
import trading.backtest as bt                           # noqa: E402
from trading import targets as T                         # noqa: E402

WINDOWS = [
    ("T1_2015-16", "2015-01-01", "2016-12-31", "train"),
    ("T2_2017-18", "2017-01-01", "2018-12-31", "train"),
    ("T3_2019-20H1", "2019-01-01", "2020-06-30", "train"),
    ("H1_2020H2-22", "2020-07-01", "2022-12-31", "test"),
    ("H2_2023-24", "2023-01-01", "2024-12-31", "test"),
    ("H3_2025-26H1", "2025-01-01", "2026-06-30", "test"),
]
ARMS = {
    "base": {"atr_k": None, "cooldown": 0},
    "A15": {"atr_k": 1.5, "cooldown": 0},
    "A20": {"atr_k": 2.0, "cooldown": 0},
    "B5": {"atr_k": None, "cooldown": 5},
    "B10": {"atr_k": None, "cooldown": 10},
    "B20": {"atr_k": None, "cooldown": 20},
}
STOP_FLOOR = 0.05
STOP_CAP = 0.15
OUT_DIR = ROOT / "research" / "stop_cooldown_study"


class Harness:
    """Monkeypatches around run_backtest for one arm, one window."""

    def __init__(self, arm: dict, bench_index: pd.DatetimeIndex):
        self.atr_k = arm["atr_k"]
        self.cooldown = int(arm["cooldown"])
        self.bench_index = bench_index
        self.fill: dict = {}            # ticker -> intraday fill price
        self.stopped: dict = {}         # ticker -> date of last STOP_LOSS
        self.stop_widths: list = []
        self.blocked_reentries = 0
        self.fast_rebuys = 0            # re-bought within 5 days of a stop
        self._orig = (bt.check_exit, bt._close_position, bt._try_open_positions)

    # -------------------------------------------------- entry: stop + target
    def try_open(self, cfg, portfolio, risk, history_as_of, bench_as_of,
                 all_hist, next_date):
        today = bench_as_of.index[-1] if bench_as_of is not None else None
        hist_in = history_as_of
        if self.cooldown and today is not None:
            keep = {}
            for t, h in history_as_of.items():
                last = self.stopped.get(t)
                if (last is not None and t not in portfolio.positions
                        and self._tdays(last, today) < self.cooldown):
                    self.blocked_reentries += 1
                    continue
                keep[t] = h
            hist_in = keep
        before = set(portfolio.positions)
        self._orig[2](cfg, portfolio, risk, hist_in, bench_as_of,
                      all_hist, next_date)
        for t in set(portfolio.positions) - before:
            pos = portfolio.positions[t]
            h = history_as_of.get(t)
            entry = float(pos.entry_price)
            tp, _ = T.target_price_for(cfg.mode, entry, h, cfg.take_profit_pct)
            pos.target_price = tp
            width = STOP_FLOOR
            if self.atr_k is not None:
                a = T.atr(h)
                if a is not None and h is not None:
                    width = min(STOP_CAP, max(STOP_FLOOR,
                                              self.atr_k * a / float(h["Close"].iloc[-1])))
            self.stop_widths.append(width)
            pos.stop_price = round(entry * (1.0 - width), 2)
            last = self.stopped.get(t)
            if last is not None and today is not None and self._tdays(last, today) < 5:
                self.fast_rebuys += 1

    # -------------------------------------------------- exits: intraday
    def check_exit(self, pos, price, score, cfg, now, history=None):
        if history is not None and len(history):
            bar = history.iloc[-1]
            o, lo, hi = float(bar["Open"]), float(bar["Low"]), float(bar["High"])
            entry_day = pos.entry_date.date() == history.index[-1].date()
            sp = float(pos.stop_price or 0)
            tp = float(pos.target_price or 0)
            # on the entry day the fill WAS the open: only the part of the bar
            # after the open is reachable, so the open itself never triggers.
            if sp > 0 and lo <= sp:
                self.fill[pos.ticker] = sp if (entry_day or o > sp) else o
                return True, "STOP_LOSS"
            if tp > 0 and hi >= tp:
                self.fill[pos.ticker] = tp if (entry_day or o < tp) else o
                return True, "TAKE_PROFIT"
        return self._orig[0](pos, price, score, cfg, now, history=history)

    def close(self, portfolio, ticker, raw_price, reason, date):
        px = self.fill.pop(ticker, raw_price)
        if reason == "STOP_LOSS":
            self.stopped[ticker] = pd.Timestamp(date)
        return self._orig[1](portfolio, ticker, px, reason, date)

    def _tdays(self, a, b) -> int:
        i = np.searchsorted(self.bench_index.values, np.datetime64(a))
        j = np.searchsorted(self.bench_index.values, np.datetime64(b))
        return int(j - i)

    def __enter__(self):
        bt.check_exit = self.check_exit
        bt._close_position = self.close
        bt._try_open_positions = self.try_open
        return self

    def __exit__(self, *exc):
        bt.check_exit, bt._close_position, bt._try_open_positions = self._orig


def main(arm_name: str) -> None:
    arm = ARMS[arm_name]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{arm_name}.json"
    done = json.loads(out_path.read_text()) if out_path.exists() else {}
    hist = load_history()
    tickers = sorted(t for t in hist if t != "^NSEI")
    for wname, start, end, half in WINDOWS:
        if wname in done:
            print(f"{arm_name} {wname}: cached", flush=True)
            continue
        cfg = make_cfg(tickers, -0.05, 0.20, -0.15)
        cfg.state_dir = f"/tmp/stop_cooldown_{arm_name}"
        cfg.apply_mode_preset()            # live floor + live bracket
        t0 = time.time()
        with Harness(arm, hist["^NSEI"].index) as h:
            r = bt.run_backtest(cfg, start, end, universe=tickers,
                                preloaded_history=hist)
        trades = r.trades
        reasons: dict = {}
        nets = []
        for t in trades:
            reasons[t.get("reason", "?")] = reasons.get(t.get("reason", "?"), 0) + 1
            cost = float(t.get("entry_price", 0)) * float(t.get("qty", 0))
            if cost > 0:
                nets.append(float(t.get("net_pnl", 0)) / cost * 100)
        row = {
            "arm": arm_name, "window": wname, "half": half,
            "entry_floor": cfg.min_score_to_enter,
            "return_pct": round(r.total_return_pct, 2),
            "alpha_pct": round(r.alpha_pct, 2),
            "bench_pct": round(r.benchmark_total_return_pct, 2),
            "max_dd_pct": round(r.max_drawdown_pct, 2),
            "trades": r.num_trades,
            "win_rate_pct": round(r.win_rate_pct, 1),
            "avg_trade_pct": round(float(np.mean(nets)), 3) if nets else None,
            "net_pnl": round(sum(float(t.get("net_pnl", 0)) for t in trades)),
            "exit_reasons": reasons,
            "avg_stop_width_pct": (round(100 * float(np.mean(h.stop_widths)), 2)
                                   if h.stop_widths else None),
            "blocked_reentries": h.blocked_reentries,
            "fast_rebuys": h.fast_rebuys,
            "secs": round(time.time() - t0, 1),
        }
        done[wname] = row
        out_path.write_text(json.dumps(done, indent=1))
        print(f"{arm_name} {wname}: {row['return_pct']:+.2f}% "
              f"({row['trades']} trades)", flush=True)
    print(f"{arm_name} DONE", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
