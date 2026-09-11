"""The three-formula view — AM07, ASH08 and AshStocks side by side.

OWNER ORDER 2026-09-11: "go with ash formula wiring" -> "go with these
formula" (with the three-column table on screen) -> "ash08 68".

What this is: every scanned stock carries all three scores, each against
its OWN floor, with a plain PASS/FAIL — the owner's table, live.

WHAT THE MEASUREMENT SAYS (research/ash_formula_study/, 23,507 observations,
train to 2020-06-30 / test after, outcome under the live -5%/+20%/200d
bracket). These numbers ride on the page beside every verdict, because a
formula shown without its record invites the reader to trust it:

  * AshStocks' python brain is AM07's formula. Spearman +1.000 — its
    momentum is 50 + avg(r6/v6, r12/v12) * 25 and its quality is
    (low-vol + liquidity)/2, the same arithmetic as our momentum_score and
    quality_score. So its column is DELIBERATELY computed from our own
    score rather than re-implemented: re-implementing it would invite drift
    between two things that are one thing. All it contributes is a HIGHER
    FLOOR (70 vs our 62).
  * ASH08's "quality" is 100 * n_bars / 126 — DATA COVERAGE, not company
    quality; all 191 names read 100.0 in its own committed snapshot. Its
    floor of 68 therefore solves to "6-month return above +0.385%", which
    is AM07's absolute-momentum gate with a rounding difference. It passes
    97.0% of our candidates.
  * AshStocks' LIVE rule (20-session momentum, top decile) measured
    NEGATIVE in both halves: -0.77 train, -0.45 test.

SCOPE: display by default. `gate_blocks()` exists so the owner can switch
the filter on (cfg.multi_formula_gate), and it defaults OFF — turning it on
changes what a Rs 5 crore book buys, and the evidence above says it removes
almost nothing (ASH08) or costs money (the 20-day rule). The switch is the
owner's; the default is the evidence's.

PURE: no network, no config mutation, no engine/risk/orders/danger import —
the same contract as trading/internals.py, which engine may import.
"""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd

# Owner-set floors. AM07's own floor is NOT duplicated here — it lives in
# the mode preset (cfg.min_score_to_enter) and is passed in, so this module
# can never disagree with the engine about our own bar.
ASH08_FLOOR = 68.0        # owner message 2026-09-11: "ash08 68"
ASHSTOCKS_FLOOR = 70.0    # AshStocks' own SELECT floor, read from its repo

# ASH08's own constants, read from that repo's advisory.py / metrics.py.
ASH08_MOM_CAL_DAYS = 182   # CALENDAR days, not bars — their choice
ASH08_MIN_SPAN_DAYS = 120
ASH08_COVERAGE_BARS = 126.0
ASH08_MOM_WEIGHT = 0.65

MOM20_BARS = 21            # AshStocks' live 20-session window

# Every claim on the page traces to the committed run.
EVIDENCE_RUN = "research/ash_formula_study/ (2026-09-11, 23,507 observations)"
VERDICTS = {
    "am07": ("LIVE — this is the score that buys. Holdout 4.17% vs ASH08 "
             "3.87% and 20-day 3.51% (top 30/month, live bracket)."),
    "ash08": ("NO MEASURED EDGE. Its 'quality' leg is data coverage, so the "
              "68 floor means '6-month return above +0.385%' — a gate AM07 "
              "already runs. Passes 97.0% of our candidates; every threshold "
              "tested was negative on the train half."),
    "ashstocks": ("SAME FORMULA AS AM07 (Spearman +1.000) — this column is "
                  "our own score read against AshStocks' higher floor of 70. "
                  "It adds a stricter bar, not a second opinion."),
    "ashstocks_live": ("MEASURED NEGATIVE both halves (-0.77 train, -0.45 "
                       "test). Buying the most-extended names made our picks "
                       "worse: a stock up ~17% in 20 days hit the -5% stop "
                       "68.4% of the time versus 63.4% for one that eased "
                       "back."),
}


def ash08_score(close: pd.Series) -> Optional[float]:
    """ASH08's advisory score, their formula verbatim.

    0.65 * clip(50 + mom6 * 200) + 0.35 * clip(100 * n_bars / 126)

    Returns None when their own minimum span is not met — never a guess.
    """
    if close is None:
        return None
    c = close.dropna()
    if len(c) < 2:
        return None
    last_d = c.index[-1]
    window = c[c.index >= last_d - pd.Timedelta(days=ASH08_MOM_CAL_DAYS)]
    if window.empty:
        return None
    if (last_d - window.index[0]).days < ASH08_MIN_SPAN_DAYS:
        return None
    first = float(window.iloc[0])
    if first <= 0:
        return None
    mom6 = float(window.iloc[-1]) / first - 1.0
    mom_s = max(0.0, min(100.0, 50.0 + mom6 * 200.0))
    coverage = max(0.0, min(100.0, 100.0 * len(c) / ASH08_COVERAGE_BARS))
    score = ASH08_MOM_WEIGHT * mom_s + (1 - ASH08_MOM_WEIGHT) * coverage
    return round(float(score), 1)


def mom20(close: pd.Series) -> Optional[float]:
    """AshStocks' live selector input: plain 20-session return."""
    if close is None:
        return None
    c = close.dropna()
    if len(c) < MOM20_BARS:
        return None
    prior = float(c.iloc[-MOM20_BARS])
    if prior <= 0:
        return None
    return round(float(c.iloc[-1]) / prior - 1.0, 4)


def evaluate(close: pd.Series, am07_score: Optional[float],
             am07_floor: float) -> dict:
    """All three verdicts for one stock.

    `am07_score` is passed in rather than recomputed so this module can
    never disagree with the engine about our own number — and because the
    AshStocks column IS that number (Spearman +1.000), read against a
    different floor.
    """
    am = None if am07_score is None else round(float(am07_score), 1)
    a8 = ash08_score(close)
    m20 = mom20(close)

    def verdict(score, floor):
        if score is None:
            return {"score": None, "floor": floor, "pass": None,
                    "label": "DATA_NEEDED"}
        ok = score >= floor
        return {"score": score, "floor": floor, "pass": bool(ok),
                "label": "PASS" if ok else "fail"}

    rows = {
        "am07": verdict(am, float(am07_floor)),
        "ash08": verdict(a8, ASH08_FLOOR),
        "ashstocks": verdict(am, ASHSTOCKS_FLOOR),
    }
    judged = [r["pass"] for r in rows.values() if r["pass"] is not None]
    return {
        "rows": rows,
        "mom20": m20,
        # How many of the three say yes. None of them is a second opinion in
        # the statistical sense (see VERDICTS) — this counts agreement, it
        # does not manufacture confidence.
        "agree": sum(1 for x in judged if x),
        "judged": len(judged),
        "all_pass": bool(judged) and all(judged),
    }


def gate_blocks(view: Optional[dict]) -> Optional[str]:
    """OWNER-SWITCHED filter: does the three-formula panel block this entry?

    Returns a reason string when it blocks, else None. Only consulted when
    cfg.multi_formula_gate is on — which it is NOT by default.

    FAIL-OPEN on missing data: a stock the outside formulas cannot judge is
    not thereby suspect, and a gate that blocks on DATA_NEEDED would quietly
    shrink the money universe every time a history tail was short. AM07's
    own floor is enforced by the engine either way.
    """
    if not view:
        return None
    rows = view.get("rows") or {}
    failed = [name for name, r in rows.items()
              if name != "am07" and r.get("pass") is False]
    if not failed:
        return None
    bits = []
    for name in failed:
        r = rows[name]
        bits.append(f"{name} {r['score']} < {r['floor']:g}")
    return "multi-formula gate: " + ", ".join(bits)
