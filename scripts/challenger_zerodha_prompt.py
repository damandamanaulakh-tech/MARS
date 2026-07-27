"""Challenger backtest: the owner's pasted "Zerodha tool" prompt vs AM07.

The pasted prompt (2026-07-27) names indicator families but NO numbers, so it
cannot be run as written. This script pins its words to standard textbook
values — PREREGISTERED here before the first run, no tuning afterwards:

  Its words                          -> the number used
  "EMA crossovers"                   -> EMA20 > EMA50 (daily closes)
  "MACD"                             -> MACD(12,26,9) line above signal
  "RSI"                              -> RSI(14) in 50..70 (strong, not overbought)
  "breakout zones"                   -> close at/above the prior 20-day high
  "volume spikes"                    -> day volume >= 1.5x its 20-day average
  "high volume stocks only"          -> 60d median rupee turnover >= Rs 1 Cr
  "max capital per trade 2-5%"       -> 5% (their upper bound, most favorable)
  "risk-reward >= 1:2, SL mandatory" -> stop -3%, target +6%
  "trailing stop-loss"               -> stop moves to breakeven at +3%
  "hold days to weeks"               -> time exit after 20 trading days
  "daily loss limit 2-3%"            -> -2.5% on the day halts NEW entries

Untestable parts of the prompt, excluded and disclosed: intraday engine
(needs 5-min bars; we hold daily), social sentiment (no historical
X/Telegram data exists to replay), LLM/LSTM prediction layer (no spec).

Same arena as every AM07 study: 191-ticker Upstox export (2014-2026),
signal on day t close -> fill at day t+1 open, 0.15% cost per side, same
four walk-forward windows. Writes docs/challenger_zerodha_results.json.
"""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile

import numpy as np
import pandas as pd

ZIP = "data/history_upstox_export_2026-07-19.zip"
OUT = "docs/challenger_zerodha_results.json"
CAPITAL = 25_00_000.0
COST = 0.0015                 # per side, matches param_search_v3
PER_TRADE = 0.05              # 5% of capital per position -> max 20 slots
STOP, TARGET = -0.03, 0.06    # RR 1:2, their rule
BREAKEVEN_AT = 0.03           # trail: stop -> entry once +3%
MAX_HOLD = 20                 # trading days ("days to weeks")
DAY_HALT = -0.025             # -2.5% daily loss halts new entries
TURNOVER_FLOOR = 1e7          # Rs 1 Cr median 60d rupee turnover

WINDOWS = [
    ("2019",      "2019-01-01", "2019-12-31"),
    ("2020-21",   "2020-01-01", "2021-12-31"),
    ("2022-23",   "2022-01-01", "2023-12-31"),
    ("2024-26H1", "2024-01-01", "2026-07-17"),
]


def load_panel() -> dict[str, pd.DataFrame]:
    z = zipfile.ZipFile(ZIP)
    out = {}
    for name in z.namelist():
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(z.read(name)), parse_dates=["Date"])
            out[name[:-4]] = df.set_index("Date").sort_index()
    return out


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    c, v = df["Close"], df["Volume"]
    out = pd.DataFrame(index=df.index)
    ema20, ema50 = c.ewm(span=20).mean(), c.ewm(span=50).mean()
    macd = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    out["signal"] = (
        (ema20 > ema50)
        & (macd > macd.ewm(span=9).mean())
        & rsi.between(50, 70)
        & (c >= df["High"].rolling(20).max().shift(1))
        & (v >= 1.5 * v.rolling(20).mean().shift(1))
        & ((c * v).rolling(60).median() >= TURNOVER_FLOOR)
    )
    out["vol_spike"] = v / v.rolling(20).mean().shift(1)   # rank key
    return out


def run_window(panel, sigs, start, end) -> dict:
    dates = sorted(set().union(*[
        set(df.loc[start:end].index) for df in panel.values()
    ]))
    cash, positions, trades = CAPITAL, {}, []
    equity_curve, halted_day = [], None
    day_start_eq = CAPITAL

    for i, day in enumerate(dates):
        # --- exits first (on today's bar) ---
        for tic in list(positions):
            pos, df = positions[tic], panel[tic]
            if day not in df.index:
                continue
            bar = df.loc[day]
            pos["days"] += 1
            if bar["High"] >= pos["entry"] * (1 + BREAKEVEN_AT):
                pos["stop"] = max(pos["stop"], pos["entry"])
            px, reason = None, None
            if bar["Low"] <= pos["stop"]:
                px, reason = pos["stop"], "STOP"
            elif bar["High"] >= pos["entry"] * (1 + TARGET):
                px, reason = pos["entry"] * (1 + TARGET), "TARGET"
            elif pos["days"] >= MAX_HOLD:
                px, reason = bar["Close"], "TIME"
            if px is not None:
                proceeds = pos["qty"] * px * (1 - COST)
                cash += proceeds
                trades.append({"t": tic, "pnl": proceeds - pos["cost_in"],
                               "reason": reason})
                del positions[tic]

        eq = cash + sum(
            p["qty"] * float(panel[t].loc[:day]["Close"].iloc[-1])
            for t, p in positions.items()
        )
        equity_curve.append(eq)
        if i > 0 and dates[i - 1].date() != day.date():
            day_start_eq = equity_curve[-2] if len(equity_curve) > 1 else CAPITAL
        halted = (eq / day_start_eq - 1) <= DAY_HALT

        # --- entries: yesterday's signals fill at today's open ---
        if halted or i == 0:
            continue
        prev = dates[i - 1]
        cands = []
        for tic, s in sigs.items():
            if tic in positions or prev not in s.index or day not in panel[tic].index:
                continue
            if bool(s.loc[prev, "signal"]):
                cands.append((float(s.loc[prev, "vol_spike"]), tic))
        for _, tic in sorted(cands, reverse=True):
            slot = CAPITAL * PER_TRADE
            if cash < slot or len(positions) >= int(1 / PER_TRADE):
                break
            fill = float(panel[tic].loc[day, "Open"]) * (1 + COST)
            qty = int(slot / fill)
            if qty <= 0:
                continue
            cost_in = qty * fill
            cash -= cost_in
            positions[tic] = {"entry": fill, "qty": qty, "days": 0,
                              "stop": fill * (1 + STOP), "cost_in": cost_in}

    # liquidate at window end
    for tic, pos in positions.items():
        px = float(panel[tic].loc[:dates[-1]]["Close"].iloc[-1])
        proceeds = pos["qty"] * px * (1 - COST)
        cash += proceeds
        trades.append({"t": tic, "pnl": proceeds - pos["cost_in"], "reason": "END"})

    eqs = pd.Series(equity_curve)
    dd = float(((eqs - eqs.cummax()) / eqs.cummax()).min() * 100) if len(eqs) else 0.0
    wins = [t for t in trades if t["pnl"] > 0]
    return {
        "return_pct": round((cash / CAPITAL - 1) * 100, 2),
        "max_dd_pct": round(dd, 2),
        "trades": len(trades),
        "win_rate_pct": round(100 * len(wins) / max(len(trades), 1), 1),
        "exit_mix": {r: sum(1 for t in trades if t["reason"] == r)
                     for r in ("STOP", "TARGET", "TIME", "END")},
    }


def main() -> None:
    t0 = time.time()
    panel = load_panel()
    print(f"{len(panel)} tickers loaded in {time.time()-t0:.0f}s")
    sigs = {t: indicators(df) for t, df in panel.items()}
    print(f"indicators built in {time.time()-t0:.0f}s")

    results = {}
    for name, ws, we in WINDOWS:
        t1 = time.time()
        results[name] = run_window(panel, sigs, ws, we)
        print(name, results[name], f"({time.time()-t1:.0f}s)")

    with open(OUT, "w") as f:
        json.dump({"preregistered": True, "windows": results}, f, indent=2)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
