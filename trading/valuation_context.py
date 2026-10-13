"""Valuation CONTEXT stage — market, sector, rates and sentiment applied to a
standalone target (owner order 2026-10-12: "when we are evaluating the price
with connection of the market as well as the stock sector … if IT sector
globally is down by 2%, the entire IT company's evaluation is dropped by 2%
… as well as inflation rate at that time … dip in sensation … to verify and
to provide a target for any kind of stock" → picks "both, indian sector
only, discount rate - go").

WHAT THIS IS. A pure arithmetic layer that takes a dated STANDALONE target
(normalised earnings × justified P/E — typed by the owner until the
valuation sequence's stages 09-17 produce it) and returns:

  1. SECTOR LEG — the target moves with the stock's NSE sector index since
     the valuation date. Two columns side by side, the owner's pick:
       one_to_one : target × (1 + sector move)            (their 1:1 rule)
       measured   : target × (1 + β × sector move)         (the stock's own
                    sensitivity, β = slope of daily returns on the sector
                    over the last 252 sessions, at least 120 observations)
     Indian sector indices only (16 real NSE indices, SECTOR_INDEX_FOR);
     a name with no mapped index uses Nifty 50 as its leg and says so.
  2. RATES / INFLATION LEG — through the discount rate, never a flat cut:
       justified P/E ∝ 1 / (r − g),  r = 10-year yield + equity risk premium
       multiplier = (r_then − g) / (r_now + inflation shift − g)
     At r − g = 4.2 points (10y 7.2 + ERP 5 − growth 8) one extra point of
     inflation cuts the P/E by 4.2/5.2 − 1 = −19%. The sensitivity is
     printed with every reading so the size of the leg is never hidden.
  3. SENTIMENT MARGIN — a WIDER BUY DISCOUNT, never a forecast. The 09-09
     and 09-29 studies measured VIX, breadth, FII flow and the damage
     cluster as predictors of the next move and every one failed the
     holdout; so these gauges only widen the margin below the context
     target at which a buy is sensible (base 10%, +2.5 points per gauge in
     fear). A risk choice, labelled ASSUMED on screen.
  4. VERIFY — the stock's actual move since the valuation date split into
     the sector part (β × sector move) and the stock-specific residual; a
     residual beyond ±10% flags the standalone valuation for a RE-RUN (the
     owner's event loop), with the stock's news verdict shown beside it.

WHAT IT IS NOT. Display and research only. No gate, no stop, no size reads
this module (boundary test: engine/risk/orders/danger never import it; it
never imports them, the network or web.db). Nothing here is measured as
an edge: the constants are ASSUMED and say so; the owner's 1:1 rule is
their order, shown beside the measured β so both can be read.

Every input is dated and every missing input is DATA_NEEDED with its
reason — a sector index stored from September cannot carry a valuation
dated June, and the page says exactly that instead of printing 0%.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- mapping
# App sector label (cfg.sector_map vocabulary) → the NSE sector index name
# as data_refresh.SECTOR_INDEX_KEYS spells it. Only indices VERIFIED live on
# 2026-09-09 are used; a sector with no honest index is absent here and
# falls back to the market leg (Nifty 50), labelled as such. Utilities map
# to Nifty Energy because NSE's energy index holds the power names (NTPC,
# POWERGRID, TATAPOWER, ADANIGREEN, JSWENERGY, NHPC). Financial maps to the
# broad Fin Service index (banks + NBFCs + insurers + AMCs).
SECTOR_INDEX_FOR: Dict[str, str] = {
    "Auto": "Auto",
    "Financial": "Fin Service",
    "IT": "IT",
    "Pharma": "Pharma",
    "FMCG": "FMCG",
    "Metal": "Metal",
    "Energy": "Energy",
    "Utilities": "Energy",
    "Media": "Media",
    "Construction": "Infra",
}
# Realty names sit under the app's "Construction" label with cement and
# L&T; NSE lists them in Nifty Realty, which is the honest index for them.
TICKER_INDEX_OVERRIDE: Dict[str, str] = {
    "DLF.NS": "Realty", "GODREJPROP.NS": "Realty", "LODHA.NS": "Realty",
    "OBEROIRLTY.NS": "Realty", "BRIGADE.NS": "Realty",
    "PRESTIGE.NS": "Realty", "PHOENIXLTD.NS": "Realty",
}
MARKET_INDEX_LABEL = "Nifty 50"

# ---------------------------------------------------------------- ASSUMED
# Every number below is a choice, not a measurement. They are printed on
# the Den beside the reading; the owner can type the first four per run.
ASSUMED: Dict[str, float] = {
    "equity_risk_premium_pct": 5.0,   # r = 10y yield + this
    "long_run_growth_pct": 8.0,       # g in P/E ∝ 1/(r − g)
    "inflation_shift_pp": 0.0,        # extra points on r_now for a what-if
    "base_buy_discount_pct": 10.0,    # buy below the context target by this
    "fear_step_pp": 2.5,              # widened by this per gauge in fear
    "rerun_residual_pct": 10.0,       # |stock-specific move| ≥ this = re-run
    "beta_window_days": 252,          # sessions in the β regression
    "beta_min_obs": 120,              # fewer paired returns = no β
    "vix_fear_window": 120,           # Ocean rail R1's definition
    "vix_fear_quantile": 0.90,
    "fii_fear_5d_cr": -5000.0,        # the flows module's own "very bearish"
}


# ------------------------------------------------------------------ utils

def _as_ts(when: Any) -> Optional[pd.Timestamp]:
    if when is None or when == "":
        return None
    try:
        ts = pd.Timestamp(when)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    return ts.normalize()


def _clean(s: Optional[pd.Series]) -> Optional[pd.Series]:
    """Float series on a sorted, tz-naive DatetimeIndex, NaNs dropped."""
    if s is None or len(s) == 0:
        return None
    try:
        out = pd.Series(pd.to_numeric(s, errors="coerce").to_numpy(),
                        index=pd.to_datetime(s.index, errors="coerce"))
    except (TypeError, ValueError):
        return None
    out = out[out.index.notna()].dropna().sort_index()
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.duplicated(keep="last")]
    return out if len(out) else None


def close_at(s: Optional[pd.Series], when: Any) -> Optional[tuple]:
    """(date, value) of the last close ON OR BEFORE `when`; None when the
    series does not reach back that far (a value is never invented)."""
    s = _clean(s)
    ts = _as_ts(when)
    if s is None or ts is None:
        return None
    part = s[s.index <= ts]
    if part.empty:
        return None
    return (part.index[-1].date(), float(part.iloc[-1]))


def move_between(s: Optional[pd.Series], start: Any, end: Any = None) -> dict:
    """Percentage move from the close at/before `start` to the close
    at/before `end` (default: the last close). DATA_NEEDED with a reason
    when either end is missing."""
    s = _clean(s)
    if s is None:
        return {"status": "DATA_NEEDED", "why": "no series"}
    a = close_at(s, start)
    if a is None:
        return {"status": "DATA_NEEDED",
                "why": f"series starts {s.index[0].date().isoformat()}, "
                       f"after the valuation date"}
    b = close_at(s, end) if end is not None else (s.index[-1].date(), float(s.iloc[-1]))
    if b is None:
        return {"status": "DATA_NEEDED", "why": "no close at the end date"}
    if a[1] <= 0:
        return {"status": "DATA_NEEDED", "why": "non-positive start close"}
    return {"status": "OK", "start_date": a[0].isoformat(), "start": a[1],
            "end_date": b[0].isoformat(), "end": b[1],
            "pct": (b[1] / a[1] - 1.0) * 100.0}


def beta(stock: Optional[pd.Series], bench: Optional[pd.Series],
         window: int = int(ASSUMED["beta_window_days"]),
         min_obs: int = int(ASSUMED["beta_min_obs"]),
         end: Any = None) -> dict:
    """OLS slope of the stock's daily returns on the benchmark's, over the
    last `window` paired sessions ending at `end` (default: the last date
    both series share). Fewer than `min_obs` pairs = no β, with the count."""
    a, b = _clean(stock), _clean(bench)
    if a is None or b is None:
        return {"beta": None, "n": 0, "why": "no series"}
    ts = _as_ts(end)
    if ts is not None:
        a, b = a[a.index <= ts], b[b.index <= ts]
    df = pd.concat([a.rename("s"), b.rename("b")], axis=1, join="inner")
    r = df.pct_change(fill_method=None).dropna()
    r = r[np.isfinite(r["s"]) & np.isfinite(r["b"])]
    r = r.tail(int(window))
    n = int(len(r))
    if n < int(min_obs):
        return {"beta": None, "n": n,
                "why": f"{n} paired sessions — β needs {int(min_obs)}"}
    var = float(r["b"].var(ddof=1))
    if not np.isfinite(var) or var <= 0:
        return {"beta": None, "n": n, "why": "benchmark has no variance"}
    cov = float(r["s"].cov(r["b"]))
    return {"beta": float(cov / var), "n": n,
            "window_start": r.index[0].date().isoformat(),
            "window_end": r.index[-1].date().isoformat()}


# ------------------------------------------------------------------- legs

def sector_leg(target: float, sector_move_pct: Optional[float],
               beta_value: Optional[float]) -> dict:
    """Both of the owner's columns. None where an input is missing."""
    out: dict = {"one_to_one": None, "measured": None}
    if target is None or sector_move_pct is None:
        return out
    m = float(sector_move_pct) / 100.0
    out["one_to_one"] = float(target) * (1.0 + m)
    if beta_value is not None and np.isfinite(beta_value):
        out["measured"] = float(target) * (1.0 + float(beta_value) * m)
    return out


def rates_leg(target: Optional[float], rate_then_pct: Optional[float],
              rate_now_pct: Optional[float],
              erp_pct: float = ASSUMED["equity_risk_premium_pct"],
              growth_pct: float = ASSUMED["long_run_growth_pct"],
              inflation_shift_pp: float = ASSUMED["inflation_shift_pp"]) -> dict:
    """P/E ∝ 1/(r − g): multiplier = (r_then − g) / (r_now + shift − g).

    `per_pp_pct` = what ONE extra point on r_now would do from here — the
    honest size of the leg, printed with every reading. A spread at or
    below zero has no P/E answer (growth at or above the discount rate) and
    returns DATA_NEEDED rather than a number."""
    if rate_then_pct is None or rate_now_pct is None:
        return {"status": "DATA_NEEDED", "multiplier": None, "adjusted": None,
                "why": "10-year yield missing at one of the two dates"}
    r0 = float(rate_then_pct) + float(erp_pct)
    r1 = float(rate_now_pct) + float(inflation_shift_pp) + float(erp_pct)
    s0, s1 = r0 - float(growth_pct), r1 - float(growth_pct)
    base = {"r_then": r0, "r_now": r1, "spread_then": s0, "spread_now": s1,
            "rate_then_pct": float(rate_then_pct),
            "rate_now_pct": float(rate_now_pct),
            "inflation_shift_pp": float(inflation_shift_pp),
            "erp_pct": float(erp_pct), "growth_pct": float(growth_pct)}
    if s0 <= 0 or s1 <= 0:
        return {**base, "status": "DATA_NEEDED", "multiplier": None,
                "adjusted": None, "per_pp_pct": None,
                "why": "growth at or above the discount rate — P/E ∝ 1/(r−g) "
                       "has no answer; lower the growth assumption"}
    mult = s0 / s1
    per_pp = (s1 / (s1 + 1.0) - 1.0) * 100.0
    return {**base, "status": "OK", "multiplier": float(mult),
            "adjusted": (float(target) * mult) if target is not None else None,
            "per_pp_pct": float(per_pp)}


def sentiment_margin(vix: Optional[pd.Series] = None,
                     breadth_pct: Optional[float] = None,
                     breadth_low_pct: float = 40.0,
                     fii_5d_net_cr: Optional[float] = None,
                     damage_q90_on: Optional[bool] = None,
                     base_pct: float = ASSUMED["base_buy_discount_pct"],
                     step_pp: float = ASSUMED["fear_step_pp"]) -> dict:
    """A WIDER buy discount when the fear gauges are on — a margin, not a
    forecast (every gauge failed as a predictor in the 09-09/09-29 runs).
    Each gauge reads ON / off / DATA_NEEDED; only ON widens."""
    gauges = []
    v = _clean(vix)
    if v is None or len(v) < 30:
        gauges.append({"name": "VIX above its 120-day 90th pct", "state": "DATA_NEEDED",
                       "detail": "no VIX series"})
    else:
        w = int(ASSUMED["vix_fear_window"])
        # today against the PRIOR window's 90th percentile (today itself
        # is never part of the bar it is measured against)
        tail = v.iloc[:-1].tail(w)
        p90 = float(tail.quantile(ASSUMED["vix_fear_quantile"]))
        last = float(v.iloc[-1])
        gauges.append({"name": "VIX above its 120-day 90th pct",
                       "state": "ON" if last > p90 else "off",
                       "detail": f"VIX {last:.2f} vs p90 {p90:.2f} "
                                 f"({v.index[-1].date().isoformat()})"})
    if breadth_pct is None:
        gauges.append({"name": f"Breadth below {breadth_low_pct:g}% (washout)",
                       "state": "DATA_NEEDED", "detail": "no stored breadth reading"})
    else:
        gauges.append({"name": f"Breadth below {breadth_low_pct:g}% (washout)",
                       "state": "ON" if float(breadth_pct) < float(breadth_low_pct) else "off",
                       "detail": f"{float(breadth_pct):.1f}% of names above their 200-DMA"})
    if fii_5d_net_cr is None:
        gauges.append({"name": "FII 5-day net selling ≤ ₹5,000 Cr",
                       "state": "DATA_NEEDED", "detail": "no FII reading inside 45 days"})
    else:
        gauges.append({"name": "FII 5-day net selling ≤ ₹5,000 Cr",
                       "state": "ON" if float(fii_5d_net_cr) <= ASSUMED["fii_fear_5d_cr"] else "off",
                       "detail": f"₹{float(fii_5d_net_cr):,.0f} Cr over 5 days"})
    if damage_q90_on is None:
        gauges.append({"name": "Damage cluster (panel's own q90)",
                       "state": "DATA_NEEDED", "detail": "no stored damage reading"})
    else:
        gauges.append({"name": "Damage cluster (panel's own q90)",
                       "state": "ON" if damage_q90_on else "off",
                       "detail": "cluster ON" if damage_q90_on else "quiet"})
    n_on = sum(1 for g in gauges if g["state"] == "ON")
    n_unknown = sum(1 for g in gauges if g["state"] == "DATA_NEEDED")
    discount = float(base_pct) + float(step_pp) * n_on
    return {"discount_pct": discount, "base_pct": float(base_pct),
            "step_pp": float(step_pp), "n_on": n_on, "n_unknown": n_unknown,
            "gauges": gauges}


def decompose(stock_move_pct: Optional[float], sector_move_pct: Optional[float],
              beta_value: Optional[float],
              threshold_pct: float = ASSUMED["rerun_residual_pct"]) -> dict:
    """stock move = β × sector move + residual. β missing → the 1:1 split
    (β = 1) is used and labelled. |residual| ≥ threshold = RE-RUN flag."""
    if stock_move_pct is None or sector_move_pct is None:
        return {"status": "DATA_NEEDED", "why": "a move is missing"}
    b = 1.0 if (beta_value is None or not np.isfinite(beta_value)) else float(beta_value)
    part = b * float(sector_move_pct)
    resid = float(stock_move_pct) - part
    return {"status": "OK", "stock_pct": float(stock_move_pct),
            "sector_pct": float(sector_move_pct), "beta_used": b,
            "beta_assumed_one": beta_value is None,
            "sector_part_pct": part, "residual_pct": resid,
            "rerun": bool(abs(resid) >= float(threshold_pct)),
            "threshold_pct": float(threshold_pct)}


# ------------------------------------------------------------ orchestrator

def context(ticker: str, target: float, asof: Any,
            stock: Optional[pd.Series],
            bench: Optional[pd.Series], bench_label: str,
            bond: Optional[pd.Series] = None,
            rate_now_pct: Optional[float] = None,
            erp_pct: float = ASSUMED["equity_risk_premium_pct"],
            growth_pct: float = ASSUMED["long_run_growth_pct"],
            inflation_shift_pp: float = ASSUMED["inflation_shift_pp"],
            sentiment: Optional[dict] = None,
            news_verdict: Optional[str] = None,
            bench_why: str = "") -> dict:
    """One stock's full context reading. Pure: every input is passed in.

    `bench` is the sector index closes (or Nifty 50 for an unmapped name,
    with `bench_label` saying which). `bond` is the 10-year yield series;
    `rate_now_pct` overrides its last value (the committed file ends in
    June — the owner types today's yield). `sentiment` carries the raw
    gauge inputs: {vix, breadth_pct, breadth_low_pct, fii_5d_net_cr,
    damage_q90_on}.
    """
    out: dict = {"ticker": ticker, "target": float(target),
                 "asof": str(asof), "bench_label": bench_label,
                 "legs": {}, "status": "OK", "why": ""}
    ts = _as_ts(asof)
    if ts is None:
        out.update(status="DATA_NEEDED", why="valuation date unreadable")
        return out
    if target is None or float(target) <= 0:
        out.update(status="DATA_NEEDED", why="standalone target must be above zero")
        return out

    # --- price then / now
    pm = move_between(stock, ts)
    if pm["status"] != "OK":
        out.update(status="DATA_NEEDED",
                   why=f"stock closes: {pm['why']}")
        return out
    out["price_then"], out["price_then_date"] = pm["start"], pm["start_date"]
    out["price_now"], out["price_now_date"] = pm["end"], pm["end_date"]
    out["stock_move_pct"] = pm["pct"]
    end_ts = pd.Timestamp(pm["end_date"])

    # --- sector leg (both columns)
    bm = move_between(bench, ts, end_ts)
    bt = beta(stock, bench, end=end_ts)
    sl: dict = {"label": bench_label, "beta": bt.get("beta"), "beta_n": bt.get("n"),
                "beta_why": bt.get("why"), "beta_window": None}
    if bt.get("beta") is not None:
        sl["beta_window"] = f"{bt['window_start']} → {bt['window_end']}"
    if bm["status"] == "OK":
        sl.update(status="OK", move_pct=bm["pct"], from_date=bm["start_date"],
                  to_date=bm["end_date"],
                  **sector_leg(float(target), bm["pct"], bt.get("beta")))
    else:
        why = bench_why if (bench is None and bench_why) else bm["why"]
        sl.update(status="DATA_NEEDED", move_pct=None, one_to_one=None,
                  measured=None, why=why)
    out["legs"]["sector"] = sl

    # --- rates leg
    r_then = close_at(bond, ts)
    r_last = close_at(bond, end_ts)
    rl_in_now = rate_now_pct
    now_src = "typed"
    if rl_in_now is None and r_last is not None:
        rl_in_now, now_src = r_last[1], f"file {r_last[0].isoformat()}"
    rl = rates_leg(float(target), r_then[1] if r_then else None, rl_in_now,
                   erp_pct, growth_pct, inflation_shift_pp)
    rl["rate_then_date"] = r_then[0].isoformat() if r_then else None
    rl["rate_now_source"] = now_src if rl_in_now is not None else None
    if r_then is None:
        rl["why"] = "10-year yield file has no reading at the valuation date"
    out["legs"]["rates"] = rl
    mult = rl.get("multiplier")

    # --- context targets = sector column × rates multiplier
    def _ctx(v):
        if v is None:
            return None
        return float(v) * (mult if mult is not None else 1.0)
    base_1 = sl.get("one_to_one")
    base_b = sl.get("measured")
    out["context_one_to_one"] = _ctx(base_1)
    out["context_measured"] = _ctx(base_b)
    out["context_rates_only"] = _ctx(float(target))   # when the sector leg is DATA_NEEDED
    pn = out["price_now"]
    for k in ("context_one_to_one", "context_measured", "context_rates_only"):
        v = out[k]
        out[k + "_vs_price_pct"] = ((v / pn - 1.0) * 100.0) if (v and pn) else None

    # --- sentiment margin → buy-below prices
    sm_in = sentiment or {}
    sm = sentiment_margin(sm_in.get("vix"), sm_in.get("breadth_pct"),
                          float(sm_in.get("breadth_low_pct", 40.0)),
                          sm_in.get("fii_5d_net_cr"), sm_in.get("damage_q90_on"))
    d = 1.0 - sm["discount_pct"] / 100.0
    sm["buy_below_one_to_one"] = (out["context_one_to_one"] * d) if out["context_one_to_one"] else None
    sm["buy_below_measured"] = (out["context_measured"] * d) if out["context_measured"] else None
    sm["buy_below_rates_only"] = (out["context_rates_only"] * d) if out["context_rates_only"] else None
    out["legs"]["sentiment"] = sm

    # --- verify: split the move, flag a re-run
    dc = decompose(out["stock_move_pct"], sl.get("move_pct"), bt.get("beta"))
    dc["news_verdict"] = news_verdict or "DATA_NEEDED"
    if dc.get("status") == "OK":
        if dc["rerun"]:
            if (news_verdict or "").upper() == "TRIGGER":
                dc["reading"] = "stock-specific move beyond the band and news is on record — re-run the standalone valuation on the event"
            elif (news_verdict or "").upper() in ("PASS", "NO_NEWS"):
                dc["reading"] = "stock-specific move beyond the band with no news explaining it — re-run the standalone valuation"
            else:
                dc["reading"] = "stock-specific move beyond the band — re-run the standalone valuation (news verdict unknown)"
        else:
            dc["reading"] = "stock-specific move inside the band — the standalone valuation stands"
    out["legs"]["verify"] = dc
    return out
