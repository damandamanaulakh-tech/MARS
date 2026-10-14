"""Editable assumptions of the valuation chain — owner order 2026-10-14
("put the formulas in the den now … need editable as said in 40+ points").

ONE registry (`EDITABLE`) of every number the chain treats as ASSUMED,
each with its module default, its source and its bounds. The Den (§13)
prints the registry and lets the owner type replacements; the
replacements are a DRAFT stored in the settings doc
"valuation_assumptions" (web/valuation_chain_view.py) and handed to
`pipeline.evaluate(..., overrides=...)` on the Den render only.

Rules
  * the module defaults are never mutated — a draft is passed DOWN as
    arguments (snapshot lag and USD signature, share tolerance, quality
    thresholds, the sheet's 29 sensitivities, the case deltas, the range
    inputs, the verdict bands, the DCF growth cap);
  * nothing in trading/engine, risk, orders, danger or sizing reads this
    module or the draft (boundary test);
  * a blank field = the default (dropped); a non-number, NaN/inf, a
    fraction where a whole number is required, or a value outside the
    row's bounds is refused with the key named — nothing is stored;
  * `split()` groups a draft into exactly the keyword arguments each
    module takes, so a key the chain does not consume cannot exist here.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from trading import valuation_context as _vc
from trading.valuation import num
from trading.valuation import assess as A
from trading.valuation import normalise as N
from trading.valuation import scenarios as SC
from trading.valuation import shares as SH
from trading.valuation import snapshot as S
from trading.valuation import terms as T

VERSION = "2026-10-14"

GROUPS: List[Tuple[str, str]] = [
    ("sheet", "Your sheet's sensitivities (stages 15-16, column B rows 140-168)"),
    ("case", "Case deltas: what moves between downside / base / upside (stages 15-16)"),
    ("range", "Justified-range inputs (stage 17)"),
    ("verdict", "Verdict bands (stage 18)"),
    ("snapshot", "Snapshot lock (stages 07-08)"),
    ("shares", "Share bridge (stage 10)"),
    ("quality", "Earnings-quality thresholds (stage 12)"),
    ("terms", "Finance terms"),
]
GROUP_LABEL: Dict[str, str] = dict(GROUPS)


def _row(key: str, group: str, label: str, default: float, source: str, *,
         kind: str = "num", lo: Optional[float] = None, hi: Optional[float] = None,
         cell: str = "") -> dict:
    return {"key": key, "group": group, "label": label, "default": default,
            "source": source, "kind": kind, "lo": lo, "hi": hi, "cell": cell}


def _build() -> List[dict]:
    rows: List[dict] = []
    for name, cell, val, label in SC._A:
        rows.append(_row(f"sheet.{name}", "sheet", label, float(val),
                         SC.SHEET_ASSUMPTIONS[name]["source"], cell=cell,
                         lo=-1000.0, hi=1000.0))
    case_src = SC.ASSUMED["case_deltas"]["source"]
    for case in ("downside", "upside"):
        for k, lab in (("revenue_growth_pct", "revenue growth (pp added)"),
                       ("base_ebitda_margin_pct", "EBITDA margin (pp added)"),
                       ("pe_bridge_extra_pct", "P/E bridge (points added)")):
            rows.append(_row(f"case.{case}.{k}", "case", f"{case}: {lab}",
                             float(SC.CASE_DELTAS[case][k]), case_src, lo=-100.0, hi=100.0))
    rows += [
        _row("range.book_roe_step_pp", "range", "Bank route: ROE step down / up (pp)",
             float(SC.BOOK_ROE_STEP_PP), SC.ASSUMED["book_roe_step_pp"]["source"],
             lo=0.0, hi=50.0),
        _row("range.equity_risk_premium_pct", "range", "Equity risk premium (%) — COE = rf + β × ERP",
             float(_vc.ASSUMED["equity_risk_premium_pct"]),
             SC.ASSUMED["equity_risk_premium_pct"]["source"], lo=0.0, hi=30.0),
        _row("range.long_run_growth_pct", "range", "Long-run growth g (%) — bank route and DCF",
             float(_vc.ASSUMED["long_run_growth_pct"]),
             SC.ASSUMED["long_run_growth_pct"]["source"], lo=-10.0, hi=30.0),
    ]
    for k, lab, lo, hi in (
            ("attractive_margin_pct", "ATTRACTIVE when base is at least this % above price", 0.0, 90.0),
            ("watch_band_pct", "WATCH when price is within ± this % of base", 0.0, 90.0),
            ("unattractive_premium_pct", "UNATTRACTIVE when price is above base by more than this %", 0.0, 500.0),
            ("max_range_width_pct", "INSUFFICIENT when (high − low) / base is above this %", 1.0, 1000.0),
            ("max_uncertainty_pct", "INSUFFICIENT when the stated uncertainty is above this %", 1.0, 1000.0)):
        rows.append(_row(f"verdict.{k}", "verdict", lab, float(A.ASSUMED[k]["value"]),
                         A.ASSUMED[k]["source"], lo=lo, hi=hi))
    rows += [
        _row("snapshot.publication_lag_days", "snapshot",
             "Publication date = period end + this many days", int(S.ASSUMED["publication_lag_days"]),
             S.ASSUMED_SOURCE["publication_lag_days"], kind="int", lo=0, hi=365),
        _row("snapshot.usd_signature_pe", "snapshot",
             "USD signature: price ÷ EPS above this (ADR list AND signature must agree)",
             float(S.ASSUMED["usd_signature_pe"]), S.ASSUMED_SOURCE["usd_signature_pe"],
             lo=20.0, hi=10000.0),
        _row("shares.reconcile_tol_pct", "shares", "Share bridge reconciles within ± this %",
             float(SH.ASSUMED["reconcile_tol_pct"]), SH.ASSUMED["reconcile_tol_source"],
             lo=0.0, hi=100.0),
        _row("quality.cash_conversion_min", "quality", "Cash conversion (OCF ÷ PAT) below this = flag",
             float(N.ASSUMED["thresholds"]["cash_conversion_min"]), N.ASSUMED["thresholds_source"],
             lo=0.0, hi=10.0),
        _row("quality.net_debt_ebitda_max", "quality", "Net debt ÷ EBITDA above this = flag",
             float(N.ASSUMED["thresholds"]["net_debt_ebitda_max"]), N.ASSUMED["thresholds_source"],
             lo=0.0, hi=50.0),
        _row("quality.interest_cover_min", "quality", "Interest cover (EBIT ÷ interest) below this = flag",
             float(N.ASSUMED["thresholds"]["interest_cover_min"]), N.ASSUMED["thresholds_source"],
             lo=0.0, hi=100.0),
        _row("terms.dcf_growth_cap_pct", "terms", "DCF terminal growth cap (%)",
             float(T.ASSUMED["dcf_growth_cap_pct"]), T.ASSUMED_SOURCE["dcf_growth_cap_pct"],
             lo=0.0, hi=30.0),
    ]
    return rows


EDITABLE: List[dict] = _build()
BY_KEY: Dict[str, dict] = {r["key"]: r for r in EDITABLE}


def defaults() -> Dict[str, float]:
    return {r["key"]: r["default"] for r in EDITABLE}


def clean(values: Optional[dict]) -> Dict[str, float]:
    """Known keys with finite numeric values only; a value equal to the
    default is dropped (a draft holds differences, never the whole table)."""
    out: Dict[str, float] = {}
    for k, v in (values or {}).items():
        row = BY_KEY.get(str(k))
        x = num(v)
        if row is None or x is None or not math.isfinite(x):
            continue
        x = int(x) if row["kind"] == "int" else float(x)
        if x != row["default"]:
            out[row["key"]] = x
    return out


def coerce(raw: Dict[str, Any]) -> Tuple[Dict[str, float], List[str]]:
    """Form fields → draft. Blank = default (dropped). Every refusal names
    its key; with any refusal the caller must store NOTHING."""
    values: Dict[str, float] = {}
    errors: List[str] = []
    for key, row in BY_KEY.items():
        s = raw.get(key)
        if s is None:
            continue
        s = str(s).strip().replace(",", "")
        if s == "":
            continue
        try:
            v = float(s)
        except ValueError:
            errors.append(f"{key}: '{s}' is not a number")
            continue
        if not math.isfinite(v):
            errors.append(f"{key}: must be a finite number")
            continue
        if row["kind"] == "int":
            if v != int(v):
                errors.append(f"{key}: must be a whole number")
                continue
            v = int(v)
        lo, hi = row["lo"], row["hi"]
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            errors.append(f"{key}: {v:g} is outside {lo:g}..{hi:g}")
            continue
        if v != row["default"]:
            values[key] = v
    return values, errors


def split(values: Optional[dict]) -> dict:
    """Group a draft into the keyword arguments each module takes."""
    v = clean(values)
    deltas = {c: dict(d) for c, d in SC.CASE_DELTAS.items()}
    sheet: Dict[str, float] = {}
    verdict: Dict[str, float] = {}
    quality: Dict[str, float] = {}
    rng: Dict[str, float] = {}
    snap: Dict[str, float] = {}
    shares: Dict[str, float] = {}
    terms: Dict[str, float] = {}
    for k, val in v.items():
        group, rest = k.split(".", 1)
        if group == "sheet":
            sheet[rest] = float(val)
        elif group == "case":
            case, field = rest.split(".", 1)
            deltas[case][field] = float(val)
        elif group == "verdict":
            verdict[rest] = float(val)
        elif group == "quality":
            quality[rest] = float(val)
        elif group == "range":
            rng[rest] = float(val)
        elif group == "snapshot":
            snap[rest] = val
        elif group == "shares":
            shares[rest] = float(val)
        elif group == "terms":
            terms[rest] = float(val)
    return {"values": v, "edited": sorted(v), "n_edited": len(v),
            "sheet": sheet, "case_deltas": deltas, "verdict": verdict,
            "quality": quality, "range": rng, "snapshot": snap,
            "shares": shares, "terms": terms}


def describe(values: Optional[dict]) -> List[dict]:
    """Every editable row with the default, the draft value (if any) and
    an `edited` flag — what the Den §13 table prints."""
    v = clean(values)
    out = []
    for r in EDITABLE:
        cur = v.get(r["key"], r["default"])
        out.append({**r, "value": cur, "edited": r["key"] in v,
                    "group_label": GROUP_LABEL.get(r["group"], r["group"])})
    return out


__all__ = ["EDITABLE", "BY_KEY", "GROUPS", "GROUP_LABEL", "VERSION",
           "defaults", "clean", "coerce", "split", "describe"]
