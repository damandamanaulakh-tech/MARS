"""Indian Ocean engine — deep reversion (contrarian/value).

Buys high-quality companies after panic crashes. Active only when
trading.regime says correction or panic.

Selection rules (stock must pass ALL):

  Survival check (fundamentals):
    1. Debt-to-Equity < 0.5
    2. Current ratio > 1.5
    3. Free Cash Flow positive last 3 years

  Capitulation trigger (technicals):
    4. Price >= 30% below 200-day SMA  (deep discount)
    5. Weekly RSI < 25                  (extreme oversold)
    6. Recent climax volume: at least one day in last 5 trading days
       with volume > 3x its 20-day average

Tranche execution:
  - Position size: max 10% of capital per stock (already config-capped)
  - Buy in 3 stages: 30% at trigger, 30% at first higher low,
    40% above the 20-day EMA reclaim
  - Hard stop: 12% below entry on each tranche
  - Exit half at 50-day SMA reversion; trail remainder under 20-day EMA

Honest limitation: fundamentals (D/E, current ratio, FCF) come from
yfinance.Ticker.info which is current-snapshot only. In backtest mode
these checks are skipped (returns no Ocean picks during replay) to
avoid lookahead bias.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from ..config import Config
from ..data import is_backtest_mode
from .. import decision_evidence as evidence

log = logging.getLogger(__name__)


@dataclass
class OceanCandidate:
    ticker: str
    price: float
    pct_below_200dma: float
    weekly_rsi: float
    climax_volume_ratio: float    # max(vol[-5:]) / vol_20d_mean
    survival_passed: bool
    reasons: list[str]


# ---------------- Technical filters (pure functions of OHLCV) -----------------

def _sma(s: pd.Series, n: int) -> Optional[float]:
    if s is None or len(s.dropna()) < n:
        return None
    return float(s.dropna().rolling(n).mean().iloc[-1])


def weekly_rsi(daily_close: pd.Series, length: int = 14) -> Optional[float]:
    """RSI on weekly resampled bars."""
    if daily_close is None or len(daily_close.dropna()) < length * 7:
        return None
    weekly = daily_close.dropna().resample("W").last().dropna()
    if len(weekly) < length + 1:
        return None
    delta = weekly.diff().dropna()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / length, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / length, adjust=False).mean()
    eps = 1e-9
    rs = roll_up / roll_down.where(roll_down > 0, eps)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.dropna()
    return float(val.iloc[-1]) if not val.empty else None


def climax_volume_ratio(volume: pd.Series, lookback: int = 5, baseline: int = 20) -> Optional[float]:
    """max(vol[-lookback:]) / mean(vol[-baseline:])."""
    if volume is None or len(volume.dropna()) < baseline:
        return None
    recent_max = float(volume.iloc[-lookback:].max())
    base_mean = float(volume.iloc[-baseline:].mean())
    if base_mean <= 0:
        return None
    return recent_max / base_mean


# ---------------- Fundamental survival check ---------------------------------
# REWRITTEN 2026-08-26 (owner order: wire Ocean with the crash guard). The
# old check called yfinance .info live — Yahoo 429-blocks Render, so in
# production it ALWAYS failed and Ocean could never have picked a single
# stock even when enabled. It now reads the fundamentals STORE (committed
# CSV + the monthly Node 3 refresh via data_refresh's injected reader):
# D/E < 0.5 and latest annual FCF > 0. The original spec's current-ratio
# leg is dropped honestly — current assets/liabilities are not in the
# store; the two remaining legs are the original thresholds. Missing
# fundamentals = FAIL (a dip-buyer must prove survival, never assume it).

_fund_cache: dict[str, dict[str, dict[str, float]]] = {}


def _fundamentals(symbol: str) -> dict[str, dict[str, float]]:
    global _fund_cache
    if not _fund_cache:
        import csv
        from pathlib import Path
        path = (Path(__file__).resolve().parents[2] / "data" /
                "fundamentals_n200_2026-08-09.csv")
        try:
            with open(path) as f:
                for r in csv.DictReader(f):
                    _fund_cache.setdefault(r["symbol"], {}).setdefault(
                        r["field"], {})[r["as_of"]] = float(r["value"])
        except OSError:
            log.warning("ocean: fundamentals CSV missing")
        try:
            from trading import data_refresh as _dr
            for r in _dr.stored_fundamentals_rows():
                _fund_cache.setdefault(r["symbol"], {}).setdefault(
                    r["field"], {})[r["as_of"]] = float(r["value"])
        except Exception as e:                                # noqa: BLE001
            log.warning("ocean: fundamentals overlay failed: %s", e)
    return _fund_cache.get(symbol, {})


def survival_check(ticker: str) -> tuple[bool, list[str]]:
    """(passes, reasons) from the fundamentals store. Backtests return
    False — no historical snapshot exists, and pretending otherwise would
    be lookahead."""
    if is_backtest_mode():
        evidence.record("ocean_fundamentals", state="unavailable", reason="No historical fundamental snapshot in replay")
        return False, ["backtest-mode: fundamentals skipped (no snapshot)"]
    f = _fundamentals(ticker.replace(".NS", ""))
    debt = f.get("annualTotalDebt", {})
    eq = f.get("annualStockholdersEquity", {})
    fcf = f.get("annualFreeCashFlow", {})
    evidence.record("ocean_fundamentals", {"debt": debt, "equity": eq, "fcf": fcf},
                    "All three series required", state="passed" if debt and eq and fcf else "unavailable")
    if not debt or not eq or not fcf:
        return False, ["fundamentals missing from store"]
    d_latest = debt[max(debt)]
    e_latest = eq[max(eq)]
    evidence.record("ocean_equity", e_latest, "> 0", passed=e_latest > 0)
    if e_latest <= 0:
        return False, ["negative/zero equity"]
    de = d_latest / e_latest
    evidence.record("ocean_debt_equity", de, "< 0.5", passed=de < 0.5)
    if de >= 0.5:
        return False, [f"D/E {de:.2f} >= 0.5"]
    fcf_latest = fcf[max(fcf)]
    evidence.record("ocean_fcf", fcf_latest, "> 0", passed=fcf_latest > 0)
    if fcf_latest <= 0:
        return False, [f"latest FCF {fcf_latest:.0f} not positive"]
    return True, [f"D/E {de:.2f} ok", f"FCF positive ({max(fcf)})"]


# ---------------- Crash guard (owner-approved 2026-08-26) ---------------------
# Four rails; every threshold traces to the owner's own evidence:
#   R1 VIX knife  — v4 study: VIX-crush was the ONLY dip rule that tested
#                   positive (+2.3pp); buying while VIX spikes is the knife.
#   R2 damage     — the canonical tail_down3_5d store (Node 9), judged
#                   against the PANEL'S OWN rolling 90th percentile.
#                   OWNER ORDER 2026-10-04 ("own-panel damage rule"): it
#                   used the frozen 0.213 from the owner's 1996-2024 file,
#                   which on our stock list is ON 84% of days (calm bull
#                   days included) — measured on the committed 191-stock
#                   panel, 2014-2026, with Ocean opening ZERO positions in
#                   12 years. The own-panel line is ON ~10% of days. The
#                   same correction is in docs/PREFALL_FRAMEWORK_2026-09-29.md.
#   R3 reclaim    — their reclaim family: buy the turn, never the fall.
#   R4 danger veto— the 12 calibration-VERIFIED tripwires.

VIX_Q_WINDOW = 120
VIX_Q = 0.90


def crash_guard_market(vix_close: Optional[pd.Series],
                       damage_store: Optional[dict]) -> tuple[bool, str]:
    """(ok, reason). Fail-SAFE: anything unknowable blocks — a dip-buyer
    must prove the market is not in free-fall, never assume it."""
    if vix_close is None or len(vix_close.dropna()) < VIX_Q_WINDOW:
        evidence.record("ocean_vix", threshold={"history": VIX_Q_WINDOW}, state="unavailable", reason="VIX history missing or too short")
        return False, "vix_unknown"
    v = vix_close.dropna()
    q90 = float(v.tail(VIX_Q_WINDOW).quantile(VIX_Q))
    last = float(v.iloc[-1])
    rising = len(v) >= 6 and last > float(v.iloc[-6])
    evidence.record("ocean_vix", {"last": last, "rising": rising, "sixth_last": float(v.iloc[-6])},
                    {"q90": q90, "window": VIX_Q_WINDOW}, passed=not (last > q90 and rising))
    if last > q90 and rising:
        # spike still building — unless the crush rule says the fear break
        # already happened (the one measured re-entry, +2.3pp in v4)
        try:
            from trading.regime import vix_crush_ok
            crush = vix_crush_ok(v)
            evidence.record("ocean_vix_crush", crush, True, passed=crush, reason="Override for rising VIX above q90")
            if not crush:
                return False, f"vix_knife({last:.1f}>q90 {q90:.1f}, rising)"
        except Exception:
            evidence.record("ocean_vix_crush", state="unavailable", reason="VIX crush check raised")
            return False, "vix_knife_unverifiable"
    try:
        from trading import internals
        fresh = internals.is_fresh(damage_store)
        evidence.record("ocean_damage", damage_store, {"fresh": True, "q90_cluster": False},
                        state="unavailable" if not fresh or (damage_store or {}).get("q90_cluster") is None else "failed" if damage_store.get("q90_cluster") else "passed")
        if not fresh:
            return False, "damage_reading_missing_or_stale"
        q90_cluster = damage_store.get("q90_cluster")
        if q90_cluster is None:
            return False, "damage_reading_unreadable"
        if q90_cluster:
            return False, "damage_cluster_own_q90"
    except Exception:
        evidence.record("ocean_damage", state="unavailable", reason="Damage check raised")
        return False, "damage_reading_unreadable"
    return True, "ok"


def crash_guard_stock(df: pd.DataFrame,
                      danger_fired: list[str]) -> tuple[bool, str]:
    """(ok, reason) for one candidate. R3 reclaim + R4 danger veto."""
    evidence.record("ocean_danger", danger_fired, [], state="unavailable" if "danger_unverifiable" in danger_fired else "failed" if danger_fired else "passed")
    if danger_fired:
        return False, "danger_" + ",".join(danger_fired)
    try:
        close = df["Close"].dropna()
        low = df["Low"].dropna()
        high = df["High"].dropna()
        evidence.record("ocean_reclaim_history", len(close), ">= 22", state="passed" if len(close) >= 22 else "unavailable")
        if len(close) < 22:
            return False, "too_short"
        evidence.record("ocean_fresh_low", float(low.iloc[-1]), {"above_prior_20_low": float(low.iloc[-21:-1].min())}, passed=float(low.iloc[-1]) > float(low.iloc[-21:-1].min()))
        if float(low.iloc[-1]) <= float(low.iloc[-21:-1].min()):
            return False, "fresh_20d_low"
        evidence.record("ocean_reclaim", float(close.iloc[-1]), {"above_prior_high": float(high.iloc[-2])}, passed=float(close.iloc[-1]) > float(high.iloc[-2]))
        if float(close.iloc[-1]) <= float(high.iloc[-2]):
            return False, "no_reclaim_above_prior_high"
    except (KeyError, ValueError, IndexError):
        evidence.record("ocean_reclaim", state="unavailable", reason="OHLC unreadable")
        return False, "ohlc_unreadable"
    return True, "ok"


# ---------------- Composite selector ------------------------------------------

def evaluate(ticker: str, history: pd.DataFrame, cfg: Config) -> Optional[OceanCandidate]:
    """Returns an OceanCandidate if the stock passes all rules; else None."""
    evidence.record("history", len(history) if history is not None else None, ">= 200",
                    state="unavailable" if history is None or len(history) < 200 else "passed")
    if history is None or len(history) < 200:
        return None
    close = history["Close"]
    volume = history["Volume"]

    last_price = float(close.iloc[-1])
    sma200 = _sma(close, 200)
    if sma200 is None or sma200 <= 0:
        evidence.record("ocean_discount", state="unavailable", reason="No usable 200-day average")
        return None

    pct_below_200 = (last_price / sma200 - 1) * 100
    evidence.record("ocean_discount", {"pct": pct_below_200, "price": last_price, "sma200": sma200}, "pct <= -30", passed=pct_below_200 <= -30)
    if pct_below_200 > -30:
        return None       # not deep enough

    wrsi = weekly_rsi(close)
    evidence.record("ocean_weekly_rsi", wrsi, "< 25", state="unavailable" if wrsi is None else "passed" if wrsi < 25 else "failed")
    if wrsi is None or wrsi >= 25:
        return None

    cvr = climax_volume_ratio(volume)
    evidence.record("ocean_climax", cvr, ">= 3", state="unavailable" if cvr is None else "passed" if cvr >= 3 else "failed")
    if cvr is None or cvr < 3.0:
        return None

    survived, surv_reasons = survival_check(ticker)
    if not survived:
        return None

    reasons = [
        f"{pct_below_200:.1f}% below 200-DMA",
        f"Weekly RSI {wrsi:.1f}",
        f"Climax volume {cvr:.1f}x average",
        *surv_reasons,
    ]

    return OceanCandidate(
        ticker=ticker, price=last_price,
        pct_below_200dma=pct_below_200,
        weekly_rsi=wrsi,
        climax_volume_ratio=cvr,
        survival_passed=True,
        reasons=reasons,
    )


def scan(history_map: dict[str, pd.DataFrame], cfg: Config) -> list[OceanCandidate]:
    """Returns Ocean candidates ranked by depth-below-200-DMA (deepest first)."""
    out: list[OceanCandidate] = []
    context = evidence.current() or {}
    catalog = context.get("ocean_catalog", {})
    for ticker, hist in history_map.items():
        trace = evidence.new(ticker, cfg.mode, "ocean", context.get("at"), hist)
        catalog[ticker] = trace
        try:
            with evidence.capture(trace):
                c = evaluate(ticker, hist, cfg)
            if c is not None:
                out.append(c)
        except Exception as e:
            evidence.put(trace, "ocean_evaluation", state="unavailable", reason="Evaluation raised: " + type(e).__name__)
            log.warning("Ocean evaluate failed for %s: %s", ticker, e)
            continue
    out.sort(key=lambda c: c.pct_below_200dma)   # deepest first
    return out
