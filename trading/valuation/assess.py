"""MARS valuation chain — sequence stage 18, DISPLAY ONLY.

  18  FINANCIAL-SELECTION ASSESSMENT: where today's price sits against the
      stage-17 justified range → attractive / watch / unattractive /
      insufficient.

Owner order 2026-10-14 ("lets hit the formulas work completion").
MONEY IMPACT: NONE. The verdict is NOT a trade permission — every output
carries `not_a_trade_permission: True`; stage 21 (permitted decision
conditions) is not built and waits on the pre-registered test and the
owner's go. Nothing here is read by engine.scan, risk.can_open, orders or
the danger layer (trading/tests/test_valuation_boundary_2026_10_14.py).

Section H of the owner's sheet (the additive what-if P/E bridge) reaches
this stage only through the earnings-route range it shaped; this module
judges the range it is given and says when that range is too wide to judge.

ASSUMED (printed on the Den; every one a choice, not a measurement):
  * attractive  = price ≤ base × (1 − 10%) — the SAME base buy-discount
                  valuation_context uses (base_buy_discount_pct), read by
                  name so the two stages can never quote different numbers;
  * watch       = within ±10% of base, or between the range low and base;
  * unattractive= price above the range high, or above base × 1.10 with
                  no thesis recorded;
  * insufficient= low / base / high missing (DATA_NEEDED), range width
                  (high − low) / base above 60%, or a stated uncertainty
                  above 60%, or the cases out of order ("CASES INVERTED —
                  …": stage 17 never sorts them, owner choice 2026-10-21).
A thesis {text, horizon, falsifier} is RECORDED (stage 14 is not built —
this is its placeholder). Its content is never judged; the only place it
counts is the rule above: a price between base + 10% and the high stays on
WATCH when a thesis exists, and "no thesis recorded" is listed in reasons.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from trading import valuation_context as _vc
from trading.valuation import is_dn, num

ASSUMED: Dict[str, dict] = {
    "attractive_margin_pct": {
        "value": float(_vc.ASSUMED["base_buy_discount_pct"]),
        "source": "valuation_context.ASSUMED base_buy_discount_pct — the same base "
                  "buy-discount the context stage uses"},
    "watch_band_pct": {"value": 10.0, "source": "ASSUMED here: ±10% of base"},
    "unattractive_premium_pct": {"value": 10.0,
                                 "source": "ASSUMED here: above base × 1.10 with no thesis"},
    "max_range_width_pct": {"value": 60.0,
                            "source": "ASSUMED here: (high − low) / base wider than this "
                                      "is too uncertain to judge"},
    "max_uncertainty_pct": {"value": 60.0,
                            "source": "ASSUMED here: a stated uncertainty above this is "
                                      "too uncertain to judge"},
}

VERDICTS = ("attractive", "watch", "unattractive", "insufficient")


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 2)


def _thesis(thesis: Any) -> Optional[dict]:
    if not isinstance(thesis, dict):
        return None
    text = str(thesis.get("text") or "").strip()
    if not text:
        return None
    return {"text": text,
            "horizon": (str(thesis.get("horizon")).strip() or None)
            if thesis.get("horizon") is not None else None,
            "falsifier": (str(thesis.get("falsifier")).strip() or None)
            if thesis.get("falsifier") is not None else None}


def _inverted_reason(rng: dict, lo: float, ba: float, hi: float) -> str:
    """Plain words for a range whose cases are out of order."""
    note = rng.get("inverted_note")
    if not (isinstance(note, str) and note.startswith("CASES INVERTED")):
        bits = []
        if lo > ba:
            bits.append(f"downside ₹{lo:,.2f} above base ₹{ba:,.2f}")
        if ba > hi:
            bits.append(f"base ₹{ba:,.2f} above upside ₹{hi:,.2f}")
        if not bits:
            bits.append(f"downside ₹{lo:,.2f} above upside ₹{hi:,.2f}")
        note = "CASES INVERTED — " + "; ".join(bits)
    return note + " — the cases are shown in their own order; no verdict on an unordered range"


def assess(price: Any, rng: dict, *, thesis: Optional[dict] = None,
           uncertainty_pct: Optional[float] = None,
           bands: Optional[dict] = None) -> dict:
    """Stage 18 verdict for `price` against a stage-17 range {low, base, high}.

    `bands` (Den draft, owner order 2026-10-14) replaces any of the ASSUMED
    band numbers by name for this call only; the module defaults never
    change."""
    rng = rng if isinstance(rng, dict) else {}
    a = {k: v["value"] for k, v in ASSUMED.items()}
    for k, v in (bands or {}).items():
        if k in a and num(v) is not None:
            a[k] = float(v)
    p = num(price)
    lo, ba, hi = num(rng.get("low")), num(rng.get("base")), num(rng.get("high"))
    unc = num(uncertainty_pct)
    th = _thesis(thesis)
    reasons: List[str] = []

    out: Dict[str, Any] = {
        "verdict": "insufficient",
        "price": p, "low": lo, "base": ba, "high": hi,
        "family": rng.get("family") if isinstance(rng.get("family"), str) else None,
        "upside_to_base_pct": None, "discount_to_low_pct": None,
        "premium_to_high_pct": None, "range_width_pct": None,
        "uncertainty_pct": unc, "thesis": th, "reasons": reasons,
        "inverted": False,
        "not_a_trade_permission": True,
        "assumptions": {k: dict(v) for k, v in ASSUMED.items()},
    }

    # ---- what is computable regardless of the verdict
    if p is not None and p > 0:
        if ba is not None and ba > 0:
            out["upside_to_base_pct"] = _r((ba / p - 1) * 100)
        if lo is not None and lo > 0:
            out["discount_to_low_pct"] = _r((1 - p / lo) * 100)
        if hi is not None and hi > 0:
            out["premium_to_high_pct"] = _r((p / hi - 1) * 100)
    if None not in (lo, ba, hi) and ba > 0:
        out["range_width_pct"] = _r((hi - lo) / ba * 100)

    # ---- insufficient
    if p is None or p <= 0:
        reasons.append("price missing")
    for name, v in (("low", lo), ("base", ba), ("high", hi)):
        if v is None:
            raw = rng.get(name)
            why = raw.get("reason") if is_dn(raw) else "not given"
            reasons.append(f"range {name} DATA_NEEDED ({why})")
    if None not in (lo, ba, hi):
        if ba <= 0:
            reasons.append("range base at or below zero")
        elif not (lo <= ba <= hi):
            # VAL-07 (owner 2026-10-21): stage 17 never sorts the cases, so
            # an unordered range reaches here as downside / base / upside.
            out["inverted"] = True
            reasons.append(_inverted_reason(rng, lo, ba, hi))
        elif out["range_width_pct"] > a["max_range_width_pct"]:
            reasons.append(f"range width {out['range_width_pct']:g}% above "
                           f"{a['max_range_width_pct']:g}% — too uncertain to judge")
    if unc is not None and unc > a["max_uncertainty_pct"]:
        reasons.append(f"stated uncertainty {unc:g}% above {a['max_uncertainty_pct']:g}%")
    if th is None:
        reasons.append("no thesis recorded (stage 14 not built)")
    if any(not r.startswith("no thesis") for r in reasons):
        return out

    # ---- judged verdicts
    m = a["attractive_margin_pct"] / 100.0
    band = a["watch_band_pct"] / 100.0
    prem = a["unattractive_premium_pct"] / 100.0
    if p <= ba * (1 - m):
        verdict = "attractive"
        reasons.insert(0, f"price {p:g} at or below base {ba:g} less {a['attractive_margin_pct']:g}% "
                          f"({ba * (1 - m):.2f})")
        if p < lo:
            reasons.insert(1, f"price is also below the range low {lo:g}")
    elif p > hi:
        verdict = "unattractive"
        reasons.insert(0, f"price {p:g} above the range high {hi:g}")
    elif p > ba * (1 + prem) and th is None:
        verdict = "unattractive"
        reasons.insert(0, f"price {p:g} above base {ba:g} plus {a['unattractive_premium_pct']:g}% "
                          f"({ba * (1 + prem):.2f}) with no thesis")
    else:
        verdict = "watch"
        if p > ba * (1 + prem):
            reasons.insert(0, f"price {p:g} above base + {a['unattractive_premium_pct']:g}% but "
                              f"within the high {hi:g}; a thesis is recorded")
        elif abs(p / ba - 1) <= band:
            reasons.insert(0, f"price {p:g} within ±{a['watch_band_pct']:g}% of base {ba:g}")
        else:
            reasons.insert(0, f"price {p:g} between the range low {lo:g} and base {ba:g}")
        if p < lo:
            reasons.insert(1, f"price is below the range low {lo:g} but not by the "
                              f"{a['attractive_margin_pct']:g}% margin under base")
    out["verdict"] = verdict
    return out


__all__ = ["ASSUMED", "VERDICTS", "assess"]
