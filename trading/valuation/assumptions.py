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
    module takes, so a key the chain does not consume cannot exist here;
  * a row with `no_effect` set is editable but changes no target, range,
    case or verdict yet (VAL-11, owner order 2026-10-21): the Den labels it
    and `effective_keys()` leaves it out of the "draft in use" count.
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

VERSION = "2026-10-21"

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
         cell: str = "", no_effect: Optional[str] = None) -> dict:
    return {"key": key, "group": group, "label": label, "default": default,
            "source": source, "kind": kind, "lo": lo, "hi": hi, "cell": cell,
            "no_effect": no_effect}


#: The typed-value band's default lives in scenarios (builder A's
#: TYPED_VALUE_BAND_PCT); 15 if that constant is absent.
TYPED_VALUE_BAND_PCT: float = float(getattr(SC, "TYPED_VALUE_BAND_PCT", 15.0))
TYPED_VALUE_BAND_SOURCE: str = (
    ((getattr(SC, "ASSUMED", {}) or {}).get("typed_value_band_pct") or {}).get("source")
    or "owner choice 2026-10-21 (VAL-01 / VAL-14b): typed value × (1 ∓ 15%) — ASSUMED")


#: VAL-11 (owner order 2026-10-21): editable, but nothing the chain prints
#: moves when they change. Delete a line here when its number is connected.
NO_EFFECT_REASONS: Dict[str, str] = {
    "verdict.max_uncertainty_pct":
        "the chain states no uncertainty figure yet, so this limit is never tested",
    "verdict.watch_band_pct":
        "it changes only the wording of the WATCH reason; the verdict is decided by "
        "the attractive margin, the unattractive premium and the range high",
    "terms.dcf_growth_cap_pct":
        "the chain builds no cash-flow forecast yet, so the DCF value is always "
        "DATA_NEEDED and the cap is never applied",
    "snapshot.publication_lag_days":
        "the Den passes no as-of date, so the lag moves only the printed publication "
        "date; the same year's figures are used whatever the lag",
}

#: A no-effect key that does take effect when the chain is given this input.
_EFFECT_WHEN: Dict[str, str] = {"snapshot.publication_lag_days": "asof"}

# ---------------------------------------------------------------------------
# VAL-11 extension (owner order 2026-10-21): a sheet sensitivity multiplies
# one input row of the bridge or the cost chain. While the app feeds that
# input as a neutral constant, editing the sensitivity changes nothing, so
# the key gets a "no effect yet" reason — worked out PER RENDER from which
# inputs are really fed (`live`). The label goes away once its input is live.
# ---------------------------------------------------------------------------

#: The input each sheet sensitivity waits for.
SHEET_INPUT: Dict[str, str] = {
    **{f"sheet.{k}": "drivers" for k in (
        "data_centre_share_pct", "electricity_share_pct", "facilities_share_pct",
        "dc_change_per_10pp_wfh_pct", "electricity_change_per_10pp_wfh_pct",
        "facility_change_per_10pp_wfh_pct", "capex_per_10pp_wfh",
        "employee_cost_per_1pp_attrition_pct", "hardware_share_pct", "travel_share_pct",
        "fuel_share_of_travel_pct", "other_costs_share_pct", "capex_useful_life_years",
        "capex_debt_funded_pct", "debt_interest_rate_pct")},
    "sheet.margin_gain_per_1pct_rupee_pp": "usdinr",
    "sheet.beta": "market",
    "sheet.sensex_weight_pct": "market",
    "sheet.pe_per_1pp_repo_pct": "repo",
    "sheet.inflation_comfort_pct": "cpi",
    "sheet.pe_per_1pp_inflation_pct": "cpi",
    "sheet.gdp_trend_pct": "gdp",
    "sheet.pe_per_1pp_gdp_pct": "gdp",
    "sheet.pe_per_100pct_crude_pct": "brent",
    "sheet.pe_per_sentiment_point_pct": "sentiment",
    "sheet.pe_per_1pp_inst_holding_pct": "inst_holding",
    "sheet.delivery_benchmark_pct": "delivery",
    "sheet.pe_per_1pp_delivery_pct": "delivery",
    "sheet.pe_per_chart_point_pct": "chart",
}

#: Why a key has no effect while its input is not fed.
INPUT_NOT_LIVE_REASON: Dict[str, str] = {
    "drivers": "every cost, capex, work-from-home and attrition driver is 0 in the app "
               "(your sheet types them per company), so this number multiplies 0",
    "usdinr": "the rupee row counts 0 for this stock (IT exporters only, and it needs "
              "USD/INR now and at the base date)",
    "market": "no Sensex / Nifty level since the base period end, so the market row "
              "counts 0",
    "repo": "no RBI repo rate on file for both the base period end and now, so the "
            "rates row counts 0",
    "cpi": "no CPI feed (owner chose none), so the inflation row counts 0",
    "gdp": "no GDP feed (owner chose none), so the GDP row counts 0",
    "brent": "no Brent feed (owner chose none), so the crude row counts 0",
    "sentiment": "no sentiment score is fed, so the sentiment row counts 0",
    "inst_holding": "no FII + DII holding on file for both the base quarter and the "
                    "latest quarter, so the institutional row counts 0",
    "delivery": "the delivery % feed is frozen since 2026-08-07 (laid off), so the "
                "delivery row counts 0",
    "chart": "no chart-pattern score is fed, so the chart row counts 0",
    "chain": "this stock's route runs no scenario chain (book / NAV / sum-of-parts), "
             "so no sheet number reaches it",
}

#: What the live app feeds when no render says otherwise: the index store
#: (market) and USD/INR (IT exporters); every other input is a neutral 0.
DEFAULT_LIVE: Dict[str, bool] = {
    "market": True, "usdinr": True, "repo": False, "cpi": False, "gdp": False,
    "brent": False, "sentiment": False, "inst_holding": False, "delivery": False,
    "chart": False, "drivers": False, "chain": True,
}


def live_inputs(live: Optional[dict] = None) -> Dict[str, bool]:
    """DEFAULT_LIVE with the render's own readings laid over it."""
    out = dict(DEFAULT_LIVE)
    for k, v in (live or {}).items():
        if k in out:
            out[k] = bool(v)
    return out


def no_effect_reason(key: str, live: Optional[dict] = None) -> Optional[str]:
    """Why an editable key changes no result in this render (None = it does).
    A fixed reason (NO_EFFECT_REASONS) or, for a sheet sensitivity, the
    reason its input is not fed."""
    if key in NO_EFFECT_REASONS:
        return NO_EFFECT_REASONS[key]
    inp = SHEET_INPUT.get(key)
    if inp is None:
        return None
    lv = live_inputs(live)
    # the Den prints "no effect yet — " in front of the reason
    if not lv["chain"]:
        return INPUT_NOT_LIVE_REASON["chain"]
    if not lv.get(inp, True):
        return INPUT_NOT_LIVE_REASON[inp]
    return None


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
        # VAL-01 / VAL-14b (owner 2026-10-21): typed NAV, embedded value and
        # conglomerate sum-of-parts range = value × (1 ∓ this %)
        _row("range.typed_value_band_pct", "range",
             "Typed NAV / embedded value / sum-of-parts: range = value × (1 ∓ this %)",
             TYPED_VALUE_BAND_PCT, TYPED_VALUE_BAND_SOURCE, lo=0.0, hi=90.0),
    ]
    for k, lab, lo, hi in (
            ("attractive_margin_pct", "ATTRACTIVE when price is at least this % below base", 0.0, 90.0),
            ("watch_band_pct", "WATCH when price is within ± this % of base", 0.0, 90.0),
            ("unattractive_premium_pct", "UNATTRACTIVE when price is above base by more than this %", 0.0, 500.0),
            ("max_range_width_pct", "INSUFFICIENT when (high − low) / base is above this %", 1.0, 1000.0),
            ("max_uncertainty_pct", "INSUFFICIENT when the stated uncertainty is above this %", 1.0, 1000.0)):
        rows.append(_row(f"verdict.{k}", "verdict", lab, float(A.ASSUMED[k]["value"]),
                         A.ASSUMED[k]["source"], lo=lo, hi=hi,
                         no_effect=NO_EFFECT_REASONS.get(f"verdict.{k}")))
    rows += [
        _row("snapshot.publication_lag_days", "snapshot",
             "Publication date = period end + this many days", int(S.ASSUMED["publication_lag_days"]),
             S.ASSUMED_SOURCE["publication_lag_days"], kind="int", lo=0, hi=365,
             no_effect=NO_EFFECT_REASONS["snapshot.publication_lag_days"]),
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
             lo=0.0, hi=30.0, no_effect=NO_EFFECT_REASONS["terms.dcf_growth_cap_pct"]),
    ]
    return rows


EDITABLE: List[dict] = _build()
BY_KEY: Dict[str, dict] = {r["key"]: r for r in EDITABLE}


def no_effect_keys(live: Optional[dict] = None) -> List[str]:
    """The editable keys that change no result in this render (VAL-11 and
    its 2026-10-21 extension). `live` = which inputs the render really fed
    ({"repo": True, "cpi": False, …}; DEFAULT_LIVE fills the rest). A sheet
    key leaves this list once its input is fed."""
    return sorted(r["key"] for r in EDITABLE if no_effect_reason(r["key"], live))


def effective_keys(values: Optional[dict], *, asof: Optional[str] = None,
                   live: Optional[dict] = None) -> List[str]:
    """The drafted keys that really change a result — what the "DRAFT
    ASSUMPTIONS IN USE" banner, its count and the per-row "draft" tag
    should read. A fixed no-effect key counts only when the input it waits
    for is given (the publication lag with an `asof` date); a sheet key
    only while its input is live (`live`, see no_effect_keys)."""
    given = {"asof": asof}
    out = []
    for k in clean(values):
        if k in NO_EFFECT_REASONS:
            if not given.get(_EFFECT_WHEN.get(k, "")):
                continue
        elif no_effect_reason(k, live):
            continue
        out.append(k)
    return sorted(out)


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


def split(values: Optional[dict], live: Optional[dict] = None) -> dict:
    """Group a draft into the keyword arguments each module takes. `live`
    (see no_effect_keys) decides which drafted sheet keys count as in use."""
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
    eff = effective_keys(v, live=live)
    return {"values": v, "edited": sorted(v), "n_edited": len(v),
            "effective": eff, "n_effective": len(eff),
            "no_effect_edited": sorted(k for k in v if k not in eff),
            "sheet": sheet, "case_deltas": deltas, "verdict": verdict,
            "quality": quality, "range": rng, "snapshot": snap,
            "shares": shares, "terms": terms}


def describe(values: Optional[dict], live: Optional[dict] = None) -> List[dict]:
    """Every editable row with the default, the draft value (if any) and
    an `edited` flag — what the Den §13 table prints. `no_effect` carries
    the row's reason for THIS render (`live`, see no_effect_keys)."""
    v = clean(values)
    out = []
    for r in EDITABLE:
        cur = v.get(r["key"], r["default"])
        out.append({**r, "value": cur, "edited": r["key"] in v,
                    "no_effect": no_effect_reason(r["key"], live),
                    "group_label": GROUP_LABEL.get(r["group"], r["group"])})
    return out


__all__ = ["EDITABLE", "BY_KEY", "GROUPS", "GROUP_LABEL", "VERSION", "NO_EFFECT_REASONS",
           "SHEET_INPUT", "INPUT_NOT_LIVE_REASON", "DEFAULT_LIVE", "TYPED_VALUE_BAND_PCT",
           "defaults", "clean", "coerce", "split", "describe", "no_effect_keys",
           "no_effect_reason", "live_inputs", "effective_keys"]
