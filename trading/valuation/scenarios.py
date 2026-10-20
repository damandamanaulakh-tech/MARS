"""MARS valuation chain — sequence stages 15-17, DISPLAY ONLY.

  15  revenue / margin / financing SCENARIOS
  16  scenario EPS (or BVPS / EBITDA per share / revenue per share per route)
  17  JUSTIFIED VALUATION RANGE (low ≤ base ≤ high) for the stock's route

Owner order 2026-10-14 ("lets hit the formulas work completion"), built on
the owner's own example: the Tata Technologies IPO→FY27E P/E-driver sheet
of 2026-10-09 (research/valuation/Tata_Technologies_IPO_valuation_model_
2026-10-09.xlsx). `fy_scenario` reproduces that sheet's FY27E column cell
for cell — every step ROUNDed exactly as the sheet rounds (Excel ROUND =
half away from zero on a 15-significant-digit value, `xround` here) — and
generalises it to any company: the caller supplies the base year, the
drivers and the market inputs as plain dicts.

MONEY IMPACT: NONE. Nothing here is read by engine.scan, risk.can_open,
orders or the danger layer (trading/tests/test_valuation_boundary_2026_10_
14.py). The engine keeps buying on the momentum score.

SECTION H IS THE OWNER'S ADDITIVE WHAT-IF. The sheet's P/E bridge (rows
120-131) adds a percentage per factor (market × beta, sector, repo,
inflation, GDP, crude, sentiment, institutional flow, delivery, chart,
event, peer gap) and applies the SUM to the opening P/E. The owner's own
docx (2026-10-11 review) flags it as what-if, not a measured model: the
factor sizes are the sheet's 29 sensitivities, every one typed "Assumption"
in column F. So every output carries `pe_bridge_additive_whatif: True` and
lists each factor row — none is hidden inside the closing P/E.

ASSUMED (printed on the Den, the owner can replace any of them):
  * SHEET_ASSUMPTIONS — the sheet's 29 sensitivities, rows 140-168, each
    with its row source ("owner sheet 2026-10-09, row N").
  * CASE_DELTAS — downside / upside = revenue growth ∓5pp, base EBITDA
    margin ∓1pp, ∓10 points added to the P/E bridge total (E132).
  * the multiple band: peer + own-history → [min(peer, own low),
    mean(peer, own median), max(peer, own high)]; without own history the
    earnings route falls back to the case P/Es alone and SAYS so; the EV
    routes accept a peer-only point band and say so.
  * book route: ROE ∓ 2pp for downside / upside; COE = rf + β × ERP with
    ERP and long-run growth read BY NAME from valuation_context.ASSUMED
    (equity_risk_premium_pct 5.0, long_run_growth_pct 8.0) so the two
    stages can never quote different numbers.
  * sensitivity grid steps: growth {−1, 0, +1}pp × margin {−1, 0, +1}pp,
    plus a multiple row of {−10, 0, +10} points on the P/E bridge (the same
    lever CASE_DELTAS uses). Every cell is re-run through fy_scenario —
    nothing is scaled.

CORRECTED ON THE OWNER'S ORDER 2026-10-21 (TT01-TT03, faults in their own
sheet). At the sheet's own inputs (no extra capex, bridge above −100%)
every cell is unchanged:
  * TT01 E22 PAT: the extra depreciation + interest (E118) is a pre-tax
    cost, so it comes off net of tax: E118 × (1 − D25/100).
  * TT02 E35 borrowings: + the debt-funded share of the new capex,
    E117 × B154/100 (the sheet kept D35).
  * TT03 E134 closing P/E: a bridge total E132 below −100% is DATA_NEEDED,
    and so is every price built on it — never a negative multiple.
  * E101 prints its base as "USD-INR at the base year-end date": the code
    always divided by that input, never by the sheet's typed 94.9.

Inputs are crore and percent exactly as the sheet holds them (20 means
20%); per-share values in rupees. A missing input is DATA_NEEDED naming it
and every row that depends on it is DATA_NEEDED too; nothing raises.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Any, Callable, Dict, List, Optional

from trading import valuation_context as _vc
from trading.valuation import dn, is_dn, num

SHEET_SOURCE = "owner sheet 2026-10-09"

# Printed beside the three corrected cells (owner order 2026-10-21).
TT_NOTE: Dict[str, str] = {
    "E22": " — corrected on the owner's order 2026-10-21 (TT01: tax saving on the extra "
           "depreciation and interest)",
    "E35": " — corrected on the owner's order 2026-10-21 (TT02: debt-funded share of the "
           "new capex adds to borrowings)",
    "E134": " — corrected on the owner's order 2026-10-21 (TT03: a bridge total below "
            "−100% has no answer, never a negative P/E)",
}

# E101's base is the USD-INR rate on the base year-end date (the input
# usdinr_base), not the sheet's typed 94.9.
E101_FORMULA = "ROUND((E78 / USD-INR at the base year-end date - 1)*100,2)"


def e101_formula(base_rate: Any = None, base_date: Any = None) -> str:
    """E101's formula text for one stock row: the generic text plus the
    base rate actually used (and its date when known)."""
    r = num(base_rate)
    if r is None:
        return E101_FORMULA
    when = f" on {base_date}" if base_date else ""
    return f"ROUND((E78 / {r:.2f} - 1)*100,2) — {r:.2f} = USD-INR at the base year-end date{when}"


def _closing_pe(pe0: float, total_pct: float) -> Any:
    """E134 (TT03): below −100% the multiple would be negative — DATA_NEEDED."""
    if total_pct < -100:
        return dn(f"P/E bridge total E132 = {total_pct:g}% is below −100%: the closing "
                  "P/E would be negative, so there is no answer (TT03)",
                  missing=["E132 at or above −100%"])
    return xround(pe0 * (1 + total_pct / 100), 2)

# ------------------------------------------------------------ sheet inputs
# The owner's Tata Technologies example. Another company replaces every
# company-specific key; these are the defaults the Den pre-fills.
SHEET_BASE: Dict[str, float] = {
    "revenue": 5505.57, "ebitda": 852.96, "pat": 546.59, "tax": 218.13,
    "employee_cost": 2737.42, "shares": 40.6, "price_fy_end": 509.0,
    "net_cash": 698.0, "borrowings": 665.56, "equity": 3923.37,
    "dividend_ps": 11.7,
    # optional — display rows only
    "prior_revenue": 5168.45, "ipo_price": 500.0,
}
SHEET_DRIVERS: Dict[str, float] = {
    "revenue_growth_pct": 20.0, "base_ebitda_margin_pct": 16.1,
    "pat_pct_of_ebitda": 67.6, "payout_pct": 80.0, "net_cash": 880.0,
    "sector_move_pct": 0.0, "wfh_shift_pp": 0.0, "other_capex": 0.0,
    "electricity_tariff_pct": 0.0, "fuel_price_pct": 0.0,
    "hardware_cost_pct": 0.0, "rent_facility_pct": 0.0,
    "wage_hike_pct": 0.0, "attrition_change_pp": -0.2,
    "other_cost_inflation_pct": 0.0, "order_wins_pct": 0.0,
    "bid_discount_pct": 0.0, "tax_rate_change_pp": 0.0,
    "one_off_costs": 0.0,
    # NOT a sheet cell: points added to the bridge total E132 (three_cases
    # and the multiple row use it). Absent = 0 = the sheet's own chain.
    "pe_bridge_extra_pct": 0.0,
}
SHEET_MARKET: Dict[str, float] = {
    "sensex_start": 71947.55, "sensex_end": 72472.33,
    "nifty_start": 22331.4, "nifty_end": 22520.45,
    "repo_start": 5.25, "repo_end": 5.5, "cpi": 5.2, "gdp": 7.1,
    "brent_start": 106.67, "brent_end": 104.43,
    "usdinr": 96.74, "usdinr_base": 94.9, "vix": 14.36, "sentiment": 0.0,
    "inst_holding_start": 9.21, "inst_holding_end": 10.78,
    "volume_ratio": 1.0, "delivery_pct": 45.0, "chart_score": 0.0,
    "event_pct": 0.0, "gap_close_pct": 0.0, "peer_pe": 28.5,
    # optional — display rows only (B61, B70)
    "latest_price": 715.85,
}

_A = [  # (name, cell, value, label) — column B rows 140-168, all "Assumption"
    ("data_centre_share_pct", "B140", 6.0, "Data centre and cloud (% of operating cost)"),
    ("electricity_share_pct", "B141", 1.5, "Electricity (% of operating cost)"),
    ("facilities_share_pct", "B142", 4.0, "Office facilities and rent (% of operating cost)"),
    ("dc_change_per_10pp_wfh_pct", "B143", 5.0, "Data centre cost change per +10 pp WFH (%)"),
    ("electricity_change_per_10pp_wfh_pct", "B144", 3.0, "Electricity change per +10 pp WFH (%)"),
    ("facility_change_per_10pp_wfh_pct", "B145", -6.0, "Facility cost change per +10 pp WFH (%)"),
    ("capex_per_10pp_wfh", "B146", 15.0, "Capex per +10 pp WFH (₹ cr)"),
    ("employee_cost_per_1pp_attrition_pct", "B147", 0.3, "Employee cost change per +1 pp attrition (%)"),
    ("hardware_share_pct", "B148", 18.0, "Hardware, licences and materials (% of operating cost)"),
    ("travel_share_pct", "B149", 3.0, "Travel and fuel (% of operating cost)"),
    ("fuel_share_of_travel_pct", "B150", 50.0, "Fuel share of travel line (%)"),
    ("other_costs_share_pct", "B151", 8.0, "Other costs (% of operating cost)"),
    ("margin_gain_per_1pct_rupee_pp", "B152", 0.25, "EBITDA margin gain per 1% rupee depreciation (pp)"),
    ("capex_useful_life_years", "B153", 5.0, "Useful life of capex (years)"),
    ("capex_debt_funded_pct", "B154", 50.0, "Share of capex funded by debt (%)"),
    ("debt_interest_rate_pct", "B155", 9.0, "Interest rate on debt (%)"),
    ("beta", "B156", 1.0, "Beta vs market"),
    ("sensex_weight_pct", "B157", 50.0, "Sensex weight, rest is Nifty (%)"),
    ("pe_per_1pp_repo_pct", "B158", -5.0, "P/E effect per +1 pp repo rate (%)"),
    ("inflation_comfort_pct", "B159", 4.0, "Inflation comfort level (%)"),
    ("pe_per_1pp_inflation_pct", "B160", -2.0, "P/E effect per +1 pp inflation above comfort (%)"),
    ("gdp_trend_pct", "B161", 6.5, "GDP trend growth (%)"),
    ("pe_per_1pp_gdp_pct", "B162", 3.0, "P/E effect per +1 pp GDP above trend (%)"),
    ("pe_per_100pct_crude_pct", "B163", -10.0, "P/E effect of a 100% rise in crude (%)"),
    ("pe_per_sentiment_point_pct", "B164", 1.0, "P/E effect per sentiment point (%)"),
    ("pe_per_1pp_inst_holding_pct", "B165", 0.5, "P/E effect per +1 pp institutional holding (%)"),
    ("delivery_benchmark_pct", "B166", 45.0, "Delivery benchmark (%)"),
    ("pe_per_1pp_delivery_pct", "B167", 0.1, "P/E effect per +1 pp delivery above benchmark (%)"),
    ("pe_per_chart_point_pct", "B168", 1.5, "P/E effect per chart point (%)"),
]
SHEET_ASSUMPTIONS: Dict[str, dict] = {
    name: {"value": val, "cell": cell, "label": label,
           "source": f"{SHEET_SOURCE}, row {cell[1:]}"}
    for name, cell, val, label in _A
}

# Where each input lives in the owner's sheet — the reproduction test reads
# the workbook through this map. None = a constant inside a formula.
INPUT_CELLS: Dict[str, Dict[str, Optional[str]]] = {
    "base": {"revenue": "D17", "ebitda": "D19", "pat": "D22", "tax": "D24",
             "employee_cost": "D26", "shares": "D28", "price_fy_end": "D39",
             "net_cash": "D36", "borrowings": "D35", "equity": "D32",
             "dividend_ps": "D30", "prior_revenue": "C17", "ipo_price": "B4"},
    "drivers": {"revenue_growth_pct": "E18", "base_ebitda_margin_pct": "E21",
                "pat_pct_of_ebitda": "E23", "payout_pct": "E31", "net_cash": "E36",
                "sector_move_pct": "E72", "wfh_shift_pp": "E90", "other_capex": "E91",
                "electricity_tariff_pct": "E92", "fuel_price_pct": "E93",
                "hardware_cost_pct": "E94", "rent_facility_pct": "E95",
                "wage_hike_pct": "E96", "attrition_change_pp": "E97",
                "other_cost_inflation_pct": "E98", "order_wins_pct": "E99",
                "bid_discount_pct": "E100", "tax_rate_change_pp": "E102",
                "one_off_costs": "E103", "pe_bridge_extra_pct": None},
    "market": {"sensex_start": "D50", "sensex_end": "E50", "nifty_start": "D52",
               "nifty_end": "E52", "repo_start": "D73", "repo_end": "E73",
               "cpi": "E75", "gdp": "E76", "brent_start": "D77", "brent_end": "E77",
               "usdinr": "E78", "usdinr_base": None, "vix": "E79",
               "sentiment": "E80", "inst_holding_start": "D81",
               "inst_holding_end": "E81", "volume_ratio": "E83",
               "delivery_pct": "E84", "chart_score": "E85", "event_pct": "E86",
               "gap_close_pct": "E87", "peer_pe": "E88", "latest_price": "B57"},
    "assumptions": {name: cell for name, cell, _v, _l in _A},
}

# ----------------------------------------------------------------- ASSUMED
CASE_DELTAS: Dict[str, Dict[str, float]] = {
    "downside": {"revenue_growth_pct": -5.0, "base_ebitda_margin_pct": -1.0,
                 "pe_bridge_extra_pct": -10.0},
    "base": {"revenue_growth_pct": 0.0, "base_ebitda_margin_pct": 0.0,
             "pe_bridge_extra_pct": 0.0},
    "upside": {"revenue_growth_pct": 5.0, "base_ebitda_margin_pct": 1.0,
               "pe_bridge_extra_pct": 10.0},
}
GRID_GROWTH_STEPS_PP: List[float] = [-1.0, 0.0, 1.0]
GRID_MARGIN_STEPS_PP: List[float] = [-1.0, 0.0, 1.0]
GRID_MULTIPLE_STEPS_PCT: List[float] = [-10.0, 0.0, 10.0]
BOOK_ROE_STEP_PP = 2.0

ASSUMED: Dict[str, dict] = {
    "case_deltas": {"value": CASE_DELTAS,
                    "source": "ASSUMED here 2026-10-14: growth ∓5pp, base margin ∓1pp, "
                              "∓10 points on the P/E bridge total (E132); the owner can replace"},
    "band_mid_rule": {"value": "mean of peer multiple and own-history median; own median "
                               "alone without a peer",
                      "source": "ASSUMED here"},
    "earnings_band_fallback": {"value": "no own history → case closing P/Es alone "
                                        "(\"no peer/own band: case P/E only\")",
                               "source": "ASSUMED here"},
    "ev_peer_only_band": {"value": "peer multiple alone = a point band (low = mid = high)",
                          "source": "ASSUMED here"},
    "book_roe_step_pp": {"value": BOOK_ROE_STEP_PP,
                         "source": "ASSUMED here: downside / upside ROE ∓ 2pp"},
    "equity_risk_premium_pct": {"value": float(_vc.ASSUMED["equity_risk_premium_pct"]),
                                "source": "valuation_context.ASSUMED (same name, same number)"},
    "long_run_growth_pct": {"value": float(_vc.ASSUMED["long_run_growth_pct"]),
                            "source": "valuation_context.ASSUMED (same name, same number)"},
    "grid_growth_steps_pp": {"value": GRID_GROWTH_STEPS_PP, "source": "ASSUMED here"},
    "grid_margin_steps_pp": {"value": GRID_MARGIN_STEPS_PP, "source": "ASSUMED here"},
    "grid_multiple_steps_pct": {"value": GRID_MULTIPLE_STEPS_PCT,
                                "source": "ASSUMED here: points on the P/E bridge, the "
                                          "CASE_DELTAS lever"},
}

CASES = ("downside", "base", "upside")
FAMILIES = ("earnings", "book", "ev_ebitda", "ev_sales", "nav",
            "embedded_value", "sotp")

# Reasons from the task book's "Sector methods" sheet (2026-10-12).
_UNHELD = {
    "embedded_value": "embedded value (P/EV) needs EV, VNB margin and persistency — insurers "
                      "publish them in investor decks, the statements store does not hold "
                      "them (task book, Sector methods: Insurance)",
    "sotp": "sum-of-parts needs the listed-stake map and unlisted asset values with a "
            "holding discount (40-60%) — not held (task book item 11, Sector methods: "
            "Holding companies)",
    "nav": "project-level NAV not held; P/B shown as fallback (task book, Sector "
           "methods: Real estate — pre-sales and NAV missing)",
}


# ------------------------------------------------------------------ utils

def xround(x: float, nd: int = 2) -> float:
    """Excel ROUND: half away from zero, on the value as Excel sees it
    (15 significant digits) — Python's round() is banker's rounding on the
    binary float and drifts a cent on the sheet's .xx5 values."""
    d = Decimal(format(float(x), ".15g"))
    return float(d.quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP))


def _finite(v: Any) -> bool:
    return isinstance(v, float) and v == v and v not in (float("inf"), float("-inf"))


def _clean(d: Any) -> dict:
    """JSON-safe copy of an input dict: numbers stay numbers, the rest None."""
    if not isinstance(d, dict):
        return {}
    return {str(k): num(v) for k, v in d.items()}


def _assumption_values(assumptions: Optional[dict]) -> dict:
    out = {k: v["value"] for k, v in SHEET_ASSUMPTIONS.items()}
    if isinstance(assumptions, dict):
        for k, v in assumptions.items():
            out[str(k)] = v
    return out


class _Chain:
    """Cell-by-cell evaluator: a row whose inputs include DATA_NEEDED is
    DATA_NEEDED naming the root inputs; a zero divisor is DATA_NEEDED too."""

    def __init__(self) -> None:
        self.c: Dict[str, Any] = {}
        self.rows: List[tuple] = []          # (cell, key, label, formula)
        self.required_missing: set = set()
        self.optional_missing: set = set()

    def put(self, cell: str, key: str, label: str, value: Any, formula: str) -> None:
        self.c[cell] = value
        self.rows.append((cell, key, label, formula))

    def inp(self, cell: str, key: str, label: str, src: Any, src_name: str,
            name: str, required: bool = True) -> None:
        v = num(src.get(name)) if isinstance(src, dict) else None
        if v is None:
            tag = f"{src_name}.{name}"
            v = dn(f"input {tag} missing", missing=[tag])
            (self.required_missing if required else self.optional_missing).add(tag)
        self.put(cell, key, label, v, "input")

    def f(self, cell: str, key: str, label: str, formula: str,
          deps: List[str], fn: Callable[..., Any]) -> None:
        vals = [self.c[d] for d in deps]
        bad = [v for v in vals if is_dn(v)]
        if bad:
            miss = sorted({m for b in bad for m in b.get("missing", [])})
            val: Any = dn("needs " + ", ".join(miss), missing=miss)
        else:
            try:
                val = fn(*vals)
                if is_dn(val):
                    pass                    # a formula may refuse (E134 below −100%)
                elif not isinstance(val, str):
                    val = float(val)
                    if not _finite(val):
                        val = dn(f"{cell} ({key}) is not a finite number",
                                 missing=[f"{cell} finite"])
            except ZeroDivisionError:
                val = dn(f"division by zero in {cell} ({key})",
                         missing=[f"{cell} non-zero divisor"])
        self.put(cell, key, label, val, formula)


R = xround


# ------------------------------------------------------------ stage 15-16

def fy_scenario(base: dict, drivers: dict, market: dict,
                assumptions: Optional[dict] = None) -> dict:
    """One forward year (the sheet's column E) from a base year (column D).

    Returns every sheet row as a readable key (base-year rows prefixed
    `fy0_`), a `cells` dict keyed by the sheet cell (cells["E29"] = EPS),
    the ordered `chain` (cell, key, label, formula, value), the P/E bridge
    factor by factor, the `inputs` used (so a grid can re-run the case),
    `status` OK / DATA_NEEDED and `data_needed` (missing required inputs)."""
    base = base if isinstance(base, dict) else {}
    drivers = dict(drivers) if isinstance(drivers, dict) else {}
    market = market if isinstance(market, dict) else {}
    av = _assumption_values(assumptions)
    if num(drivers.get("pe_bridge_extra_pct")) is None:
        drivers["pe_bridge_extra_pct"] = 0.0   # not a sheet cell: absent = the sheet's chain

    ch = _Chain()
    In, F = ch.inp, ch.f

    # ---- sensitivities (column B, rows 140-168)
    for name, cell, _v, label in _A:
        In(cell, name, label, av, "assumptions", name)

    # ---- base year inputs (column D)
    In("D17", "fy0_revenue", "Revenue from operations (₹ cr)", base, "base", "revenue")
    In("D19", "fy0_ebitda", "Operating EBITDA (₹ cr)", base, "base", "ebitda")
    In("D22", "fy0_pat", "PAT (₹ cr)", base, "base", "pat")
    In("D24", "fy0_tax", "Tax expense (₹ cr)", base, "base", "tax")
    In("D26", "fy0_employee_cost", "Employee benefit expense (₹ cr)", base, "base", "employee_cost")
    In("D28", "fy0_shares", "Shares outstanding (cr)", base, "base", "shares")
    In("D30", "fy0_dividend_ps", "Dividend per share (₹)", base, "base", "dividend_ps")
    In("D32", "fy0_equity", "Net worth, total equity (₹ cr)", base, "base", "equity")
    In("D35", "fy0_borrowings", "Borrowings (₹ cr)", base, "base", "borrowings")
    In("D36", "fy0_net_cash", "Net cash incl. investments (₹ cr)", base, "base", "net_cash")
    In("D39", "fy0_price_fy_end", "Share price at period end (₹)", base, "base", "price_fy_end")
    In("C17", "prior_revenue", "Revenue, year before base (₹ cr)", base, "base",
      "prior_revenue", required=False)
    In("B4", "ipo_price", "IPO issue price (₹)", base, "base", "ipo_price", required=False)

    # ---- base year derived (column D)
    F("D18", "fy0_revenue_growth_pct", "Revenue growth (%)", "ROUND((D17/C17-1)*100,1)",
      ["D17", "C17"], lambda a, b: R((a / b - 1) * 100, 1))
    F("D20", "fy0_ebitda_margin_pct", "EBITDA margin (%)", "ROUND(D19/D17*100,1)",
      ["D19", "D17"], lambda a, b: R(a / b * 100, 1))
    F("D23", "fy0_pat_pct_of_ebitda", "PAT as % of EBITDA", "ROUND(D22/D19*100,1)",
      ["D22", "D19"], lambda a, b: R(a / b * 100, 1))
    F("D25", "fy0_tax_rate_pct", "Effective tax rate (%)", "ROUND(D24/(D22+D24)*100,1)",
      ["D24", "D22"], lambda t, p: R(t / (p + t) * 100, 1))
    F("D27", "fy0_employee_cost_pct", "Employee cost as % of revenue", "ROUND(D26/D17*100,2)",
      ["D26", "D17"], lambda e, r: R(e / r * 100, 2))
    F("D29", "fy0_eps", "EPS (₹)", "ROUND(D22/D28,2)",
      ["D22", "D28"], lambda p, s: R(p / s, 2))
    F("D31", "fy0_payout_pct", "Dividend payout (%)", "ROUND(D30/D29*100,1)",
      ["D30", "D29"], lambda d, e: R(d / e * 100, 1))
    F("D33", "fy0_bvps", "Book value per share (₹)", "ROUND(D32/D28,2)",
      ["D32", "D28"], lambda q, s: R(q / s, 2))
    F("D34", "fy0_roe_pct", "ROE (%)", "ROUND(D22/D32*100,1)",
      ["D22", "D32"], lambda p, q: R(p / q * 100, 1))
    F("D37", "fy0_debt_to_equity", "Debt to equity (x)", "ROUND(D35/D32,2)",
      ["D35", "D32"], lambda b, q: R(b / q, 2))
    F("D40", "fy0_market_cap", "Market cap (₹ cr)", "ROUND(D39*D28,0)",
      ["D39", "D28"], lambda p, s: R(p * s, 0))
    if num(base.get("opening_pe")) is not None:
        In("D41", "opening_pe", "P/E at base year end (x) — typed", base, "base", "opening_pe")
    else:
        F("D41", "opening_pe", "P/E at base year end (x)", "ROUND(D39/D29,1)",
          ["D39", "D29"], lambda p, e: R(p / e, 1))
    F("D42", "fy0_pb", "P/B (x)", "ROUND(D39/D33,2)",
      ["D39", "D33"], lambda p, b: R(p / b, 2))
    F("D43", "fy0_ev_ebitda", "EV/EBITDA (x)", "ROUND((D40-D36)/D19,1)",
      ["D40", "D36", "D19"], lambda m, c, e: R((m - c) / e, 1))
    F("D44", "fy0_dividend_yield_pct", "Dividend yield (%)", "ROUND(D30/D39*100,2)",
      ["D30", "D39"], lambda d, p: R(d / p * 100, 2))

    # ---- drivers (column E inputs)
    for cell, name, label in (
            ("E18", "revenue_growth_pct", "Revenue growth (%)"),
            ("E21", "base_ebitda_margin_pct", "Base EBITDA margin before drivers (%)"),
            ("E23", "pat_pct_of_ebitda", "PAT as % of EBITDA"),
            ("E31", "payout_pct", "Dividend payout (%)"),
            ("E36", "net_cash", "Net cash incl. investments (₹ cr)"),
            ("E72", "sector_move_pct", "Sector move vs market (%)"),
            ("E90", "wfh_shift_pp", "Shift to work from home (pp of staff)"),
            ("E91", "other_capex", "Other extra capex (₹ cr)"),
            ("E92", "electricity_tariff_pct", "Electricity tariff change (%)"),
            ("E93", "fuel_price_pct", "Fuel price change (%)"),
            ("E94", "hardware_cost_pct", "Hardware, licence and material cost change (%)"),
            ("E95", "rent_facility_pct", "Rent and facility cost change (%)"),
            ("E96", "wage_hike_pct", "Wage hike above base (%)"),
            ("E97", "attrition_change_pp", "Attrition change (pp)"),
            ("E98", "other_cost_inflation_pct", "General inflation on other costs (%)"),
            ("E99", "order_wins_pct", "Order wins from bidding, extra revenue (%)"),
            ("E100", "bid_discount_pct", "Bid price discount (%)"),
            ("E102", "tax_rate_change_pp", "Change in effective tax rate (pp)"),
            ("E103", "one_off_costs", "One-off costs (₹ cr)")):
        In(cell, name, label, drivers, "drivers", name)
    In("X_EXTRA", "pe_bridge_extra_pct", "Case points added to the P/E bridge (not a sheet cell)",
      drivers, "drivers", "pe_bridge_extra_pct")

    # ---- market inputs
    for cell, name, label, req in (
            ("E49", "sensex_start", "Sensex at period start", True),
            ("E50", "sensex_end", "Sensex at period end", True),
            ("E51", "nifty_start", "Nifty 50 at period start", True),
            ("E52", "nifty_end", "Nifty 50 at period end", True),
            ("D73", "repo_start", "RBI repo rate at base year end (%)", True),
            ("E73", "repo_end", "RBI repo rate at period end (%)", True),
            ("E75", "cpi", "CPI inflation (%)", True),
            ("E76", "gdp", "Real GDP growth (%)", True),
            ("D77", "brent_start", "Brent crude at base year end (US$)", True),
            ("E77", "brent_end", "Brent crude (US$ per barrel)", True),
            ("E78", "usdinr", "USD/INR", True),
            ("X_USDINR_BASE", "usdinr_base", "USD/INR at the base year-end date (inside E101)", True),
            ("E79", "vix", "India VIX (display only, no formula reads it)", False),
            ("E80", "sentiment", "Investor sentiment score (-5 to +5)", True),
            ("D81", "inst_holding_start", "Institutional holding at base year end (%)", True),
            ("E81", "inst_holding_end", "Institutional holding, FII + DII (%)", True),
            ("E83", "volume_ratio", "Volume ratio (latest vs 20-day average)", True),
            ("E84", "delivery_pct", "Delivery (% of traded volume)", True),
            ("E85", "chart_score", "Chart pattern score (-2 to +2)", True),
            ("E86", "event_pct", "Event impact (%)", True),
            ("E87", "gap_close_pct", "Share of valuation gap to peers that closes (%)", True),
            ("E88", "peer_pe", "Peer average P/E (x)", True),
            ("B57", "latest_price", "Latest share price (₹)", False)):
        In(cell, name, label, market, "market", name, required=req)

    # ---- FY+1 chain (column E), sheet order of dependency
    F("E17", "revenue", "Revenue from operations (₹ cr)",
      "ROUND(D17*(1+E18/100)*(1+E99/100)*(1-E100/100),2)", ["D17", "E18", "E99", "E100"],
      lambda r, g, w, b: R(r * (1 + g / 100) * (1 + w / 100) * (1 - b / 100), 2))
    F("E105", "base_operating_cost", "Base operating cost (₹ cr)", "ROUND(E17*(1-E21/100),2)",
      ["E17", "E21"], lambda r, m: R(r * (1 - m / 100), 2))
    F("E106", "employee_cost_impact", "Employee cost impact",
      "ROUND(E17*D27/100*(E96+E97*B147)/100,2)", ["E17", "D27", "E96", "E97", "B147"],
      lambda r, ep, w, a, k: R(r * ep / 100 * (w + a * k) / 100, 2))
    F("E107", "data_centre_impact", "Data centre and cloud impact",
      "ROUND(E105*B140/100*E90/10*B143/100,2)", ["E105", "B140", "E90", "B143"],
      lambda c, s, h, k: R(c * s / 100 * h / 10 * k / 100, 2))
    F("E108", "electricity_impact", "Electricity impact",
      "ROUND(E105*B141/100*(E92+E90/10*B144)/100,2)", ["E105", "B141", "E92", "E90", "B144"],
      lambda c, s, t, h, k: R(c * s / 100 * (t + h / 10 * k) / 100, 2))
    F("E109", "facility_impact", "Facility and rent impact",
      "ROUND(E105*B142/100*(E95+E90/10*B145)/100,2)", ["E105", "B142", "E95", "E90", "B145"],
      lambda c, s, t, h, k: R(c * s / 100 * (t + h / 10 * k) / 100, 2))
    F("E110", "hardware_impact", "Hardware, licence and material impact",
      "ROUND(E105*B148/100*E94/100,2)", ["E105", "B148", "E94"],
      lambda c, s, t: R(c * s / 100 * t / 100, 2))
    F("E111", "travel_fuel_impact", "Travel and fuel impact",
      "ROUND(E105*B149/100*E93/100*B150/100,2)", ["E105", "B149", "E93", "B150"],
      lambda c, s, t, k: R(c * s / 100 * t / 100 * k / 100, 2))
    F("E112", "other_cost_impact", "Other cost inflation impact",
      "ROUND(E105*B151/100*E98/100,2)", ["E105", "B151", "E98"],
      lambda c, s, t: R(c * s / 100 * t / 100, 2))
    F("E113", "bid_discount_cost", "Bid discount, costs not saved",
      "ROUND(E17/(1-E100/100)*E100/100*(1-E21/100),2)", ["E17", "E100", "E21"],
      lambda r, b, m: R(r / (1 - b / 100) * b / 100 * (1 - m / 100), 2))
    F("E101", "rupee_depreciation_pct", "Rupee depreciation vs base (%)",
      E101_FORMULA, ["E78", "X_USDINR_BASE"],
      lambda u, b: R((u / b - 1) * 100, 2))
    F("E114", "currency_effect", "Currency effect", "ROUND(-E17*E101*B152/100,2)",
      ["E17", "E101", "B152"], lambda r, d, k: R(-r * d * k / 100, 2))
    F("E115", "one_off_impact", "One-off costs", "E103", ["E103"], lambda x: x)
    F("E116", "net_cost_impact", "Net cost impact on EBITDA (₹ cr)", "ROUND(SUM(E106:E115),2)",
      ["E106", "E107", "E108", "E109", "E110", "E111", "E112", "E113", "E114", "E115"],
      lambda *xs: R(sum(xs), 2))
    F("E117", "extra_capex", "Extra capex, total (₹ cr)", "ROUND(E91+E90/10*B146,2)",
      ["E91", "E90", "B146"], lambda c, h, k: R(c + h / 10 * k, 2))
    F("E118", "extra_dep_interest", "Extra depreciation and interest (₹ cr)",
      "ROUND(E117/B153+E117*B154/100*B155/100,2)", ["E117", "B153", "B154", "B155"],
      lambda x, life, dshare, rate: R(x / life + x * dshare / 100 * rate / 100, 2))
    F("E19", "ebitda", "Operating EBITDA (₹ cr)", "ROUND(E17*E21/100-E116,2)",
      ["E17", "E21", "E116"], lambda r, m, c: R(r * m / 100 - c, 2))
    F("E20", "ebitda_margin_pct", "EBITDA margin (%)", "ROUND(E19/E17*100,1)",
      ["E19", "E17"], lambda e, r: R(e / r * 100, 1))
    F("E25", "tax_rate_pct", "Effective tax rate (%)", "D25+E102",
      ["D25", "E102"], lambda t, d: t + d)
    # TT01: the extra depreciation + interest is a pre-tax cost, so it comes
    # off PAT net of the tax it saves (× (1 − D25/100)).
    F("E22", "pat", "PAT (₹ cr)",
      "ROUND((E19*E23/100-E118*(1-D25/100))*(1-E102/(100-D25)),2)" + TT_NOTE["E22"],
      ["E19", "E23", "E118", "E102", "D25"],
      lambda e, k, x, d, t: R((e * k / 100 - x * (1 - t / 100)) * (1 - d / (100 - t)), 2))
    F("E26", "employee_cost", "Employee benefit expense (₹ cr)", "ROUND(E17*D27/100+E106,2)",
      ["E17", "D27", "E106"], lambda r, ep, i: R(r * ep / 100 + i, 2))
    F("E27", "employee_cost_pct", "Employee cost as % of revenue", "ROUND(E26/E17*100,2)",
      ["E26", "E17"], lambda e, r: R(e / r * 100, 2))
    F("E28", "shares", "Shares outstanding (cr)", "D28", ["D28"], lambda s: s)
    F("E29", "eps", "EPS (₹)", "ROUND(E22/E28,2)", ["E22", "E28"], lambda p, s: R(p / s, 2))
    F("E30", "dividend_ps", "Dividend per share (₹)", "ROUND(E29*E31/100,2)",
      ["E29", "E31"], lambda e, k: R(e * k / 100, 2))
    F("E32", "equity", "Net worth, total equity (₹ cr)", "ROUND(D32+E22-E30*E28,2)",
      ["D32", "E22", "E30", "E28"], lambda q, p, d, s: R(q + p - d * s, 2))
    F("E33", "bvps", "Book value per share (₹)", "ROUND(E32/E28,2)",
      ["E32", "E28"], lambda q, s: R(q / s, 2))
    F("E34", "roe_pct", "ROE (%)", "ROUND(E22/E32*100,1)",
      ["E22", "E32"], lambda p, q: R(p / q * 100, 1))
    # TT02: the debt-funded share of the new capex adds to borrowings.
    F("E35", "borrowings", "Borrowings (₹ cr)", "ROUND(D35+E117*B154/100,2)" + TT_NOTE["E35"],
      ["D35", "E117", "B154"], lambda b, x, dshare: R(b + x * dshare / 100, 2))
    F("E37", "debt_to_equity", "Debt to equity (x)", "ROUND(E35/E32,2)",
      ["E35", "E32"], lambda b, q: R(b / q, 2))
    F("E47", "eps_growth_pct", "EPS growth (%)", "ROUND((E29/D29-1)*100,1)",
      ["E29", "D29"], lambda e, e0: R((e / e0 - 1) * 100, 1))

    # ---- section H: the owner's ADDITIVE what-if P/E bridge
    F("E74", "repo_change_pp", "Repo rate change in period (pp)", "E73-D73",
      ["E73", "D73"], lambda a, b: a - b)
    F("E82", "inst_holding_change_pp", "Change in institutional holding (pp)",
      "ROUND(E81-D81,2)", ["E81", "D81"], lambda a, b: R(a - b, 2))
    F("E120", "pe_market", "Market, Sensex and Nifty x beta",
      "ROUND(((E50/E49-1)*B157/100+(E52/E51-1)*(1-B157/100))*B156*100,2)",
      ["E50", "E49", "B157", "E52", "E51", "B156"],
      lambda s1, s0, w, n1, n0, b: R(((s1 / s0 - 1) * w / 100 + (n1 / n0 - 1) * (1 - w / 100)) * b * 100, 2))
    F("E121", "pe_sector", "Sector", "E72", ["E72"], lambda x: x)
    F("E122", "pe_rates", "Interest rates", "ROUND(E74*B158,2)",
      ["E74", "B158"], lambda d, k: R(d * k, 2))
    F("E123", "pe_inflation", "Inflation", "ROUND((E75-B159)*B160,2)",
      ["E75", "B159", "B160"], lambda c, comfort, k: R((c - comfort) * k, 2))
    F("E124", "pe_gdp", "GDP growth", "ROUND((E76-B161)*B162,2)",
      ["E76", "B161", "B162"], lambda g, trend, k: R((g - trend) * k, 2))
    F("E125", "pe_crude", "Crude oil", "ROUND((E77/D77-1)*B163,2)",
      ["E77", "D77", "B163"], lambda b1, b0, k: R((b1 / b0 - 1) * k, 2))
    F("E126", "pe_sentiment", "Investor sentiment", "ROUND(E80*B164,2)",
      ["E80", "B164"], lambda s, k: R(s * k, 2))
    F("E127", "pe_institutional", "Institutional flows", "ROUND(E82*B165,2)",
      ["E82", "B165"], lambda d, k: R(d * k, 2))
    F("E128", "pe_delivery", "Delivery", "ROUND((E84-B166)*B167,2)",
      ["E84", "B166", "B167"], lambda d, bench, k: R((d - bench) * k, 2))
    F("E129", "pe_chart", "Chart pattern x volume", "ROUND(E85*B168*MIN(E83,2),2)",
      ["E85", "B168", "E83"], lambda c, k, v: R(c * k * min(v, 2), 2))
    F("E130", "pe_events", "Events", "E86", ["E86"], lambda x: x)
    F("E131", "pe_peer_gap", "Valuation gap to peers", "ROUND(E87/100*(E88/D41-1)*100,2)",
      ["E87", "E88", "D41"], lambda g, peer, pe0: R(g / 100 * (peer / pe0 - 1) * 100, 2))
    factor_cells = ["E120", "E121", "E122", "E123", "E124", "E125", "E126",
                    "E127", "E128", "E129", "E130", "E131"]
    F("X_SHEET_SUM", "pe_bridge_sheet_sum_pct", "Sum of the factor rows (%)",
      "ROUND(SUM(E120:E131),2)", factor_cells, lambda *xs: R(sum(xs), 2))
    F("E132", "pe_total_impact_pct", "Total impact on P/E (%) incl. case points",
      "ROUND(SUM(E120:E131)+case points,2)", factor_cells + ["X_EXTRA"],
      lambda *xs: R(sum(xs[:-1]) + xs[-1], 2))
    F("E133", "bridge_opening_pe", "Opening P/E, base year end (x)", "D41",
      ["D41"], lambda x: x)
    # TT03: a bridge total below −100% would give a negative P/E — no answer.
    F("E134", "closing_pe", "Closing P/E, FY+1 (x)",
      'IF(E132<-100,"DATA_NEEDED",ROUND(E133*(1+E132/100),2))' + TT_NOTE["E134"],
      ["E133", "E132"], _closing_pe)
    F("E135", "model_price", "Model share price, FY+1 (₹)", "ROUND(E29*E134,2)",
      ["E29", "E134"], lambda e, pe: R(e * pe, 2))
    F("E136", "model_market_cap", "Model valuation, market cap (₹ cr)", "ROUND(E135*E28,0)",
      ["E135", "E28"], lambda p, s: R(p * s, 0))
    F("E137", "peg", "PEG ratio", 'IF(E47>0,ROUND(E134/E47,2),"n/a")',
      ["E47", "E134"], lambda g, pe: R(pe / g, 2) if g > 0 else "n/a")
    F("E138", "premium_to_peer_pe_pct", "Premium to peer P/E (%)", "ROUND((E134/E88-1)*100,1)",
      ["E134", "E88"], lambda pe, peer: R((pe / peer - 1) * 100, 1))

    # ---- section C at FY+1 (price = the model price)
    F("E39", "price_fy_end", "Share price at period end (₹) = model price", "E135",
      ["E135"], lambda p: p)
    F("E40", "market_cap", "Market cap (₹ cr)", "ROUND(E39*E28,0)",
      ["E39", "E28"], lambda p, s: R(p * s, 0))
    F("E41", "pe", "P/E (x)", "ROUND(E39/E29,1)", ["E39", "E29"], lambda p, e: R(p / e, 1))
    F("E42", "pb", "P/B (x)", "ROUND(E39/E33,2)", ["E39", "E33"], lambda p, b: R(p / b, 2))
    F("E43", "ev_ebitda", "EV/EBITDA (x)", "ROUND((E40-E36)/E19,1)",
      ["E40", "E36", "E19"], lambda m, c, e: R((m - c) / e, 1))
    F("E44", "dividend_yield_pct", "Dividend yield (%)", "ROUND(E30/E39*100,2)",
      ["E30", "E39"], lambda d, p: R(d / p * 100, 2))
    F("E45", "price_change_pct", "Price change in period (%)", "ROUND((E39/D39-1)*100,1)",
      ["E39", "D39"], lambda p, p0: R((p / p0 - 1) * 100, 1))
    F("E46", "return_vs_ipo_pct", "Return vs IPO issue price (%)", "ROUND((E39/B4-1)*100,1)",
      ["E39", "B4"], lambda p, i: R((p / i - 1) * 100, 1))
    F("E48", "pe_change_pct", "P/E change, re-rating (%)", "ROUND((E41/D41-1)*100,1)",
      ["E41", "D41"], lambda pe, pe0: R((pe / pe0 - 1) * 100, 1))
    F("E53", "sensex_change_pct", "Sensex change (%)", "ROUND((E50/E49-1)*100,2)",
      ["E50", "E49"], lambda a, b: R((a / b - 1) * 100, 2))
    F("E54", "nifty_change_pct", "Nifty 50 change (%)", "ROUND((E52/E51-1)*100,2)",
      ["E52", "E51"], lambda a, b: R((a / b - 1) * 100, 2))
    F("E55", "stock_vs_nifty_pp", "Stock vs Nifty (percentage points)", "ROUND(E45-E54,1)",
      ["E45", "E54"], lambda a, b: R(a - b, 1))
    F("B61", "forward_pe_at_latest", "Forward P/E on FY+1 EPS at the latest price (x)",
      "ROUND(B57/E29,1)", ["B57", "E29"], lambda p, e: R(p / e, 1))
    F("B70", "model_vs_latest_pct", "Model FY+1 price vs latest price (%)",
      "ROUND((E39/B57-1)*100,1)", ["E39", "B57"], lambda m, p: R((m / p - 1) * 100, 1))

    # ---- assemble
    out: Dict[str, Any] = {}
    chain: List[dict] = []
    cells: Dict[str, Any] = {}
    for cell, key, label, formula in ch.rows:
        val = ch.c[cell]
        out[key] = val
        chain.append({"cell": None if cell.startswith("X_") else cell, "key": key,
                      "label": label, "formula": formula, "value": val})
        if not cell.startswith("X_"):
            cells[cell] = val
    factors = [{"cell": c, "key": k, "label": lbl, "pct": ch.c[c]}
               for (c, k, lbl, _f) in ch.rows if c in factor_cells]
    required_missing = sorted(ch.required_missing)
    status = "OK" if (not required_missing and not is_dn(ch.c["E135"])) else "DATA_NEEDED"
    out.update({
        "status": status,
        "data_needed": required_missing,
        "optional_missing": sorted(ch.optional_missing),
        "cells": cells,
        "chain": chain,
        "pe_bridge_additive_whatif": True,
        "pe_bridge": {
            "additive_whatif": True,
            "note": "Section H of the owner's sheet: each factor ADDS a percentage to the "
                    "opening P/E and the sum is applied once. The owner's own review "
                    "flags it as what-if; the sizes are the sheet's sensitivities, every "
                    "one an assumption.",
            "factors": factors,
            "sheet_sum_pct": ch.c["X_SHEET_SUM"],
            "case_extra_pct": ch.c["X_EXTRA"],
            "total_pct": ch.c["E132"],
            "opening_pe": ch.c["E133"],
            "closing_pe": ch.c["E134"],
        },
        "inputs": {"base": _clean(base), "drivers": _clean(drivers),
                   "market": _clean(market), "assumptions": _clean(av)},
        "source": f"{SHEET_SOURCE} (Tata Technologies example) generalised",
    })
    if status == "DATA_NEEDED":
        if required_missing:
            out["reason"] = "missing: " + ", ".join(required_missing)
        elif is_dn(ch.c["E134"]) and "TT03" in str(ch.c["E134"].get("reason")):
            out["reason"] = ch.c["E134"]["reason"]
        else:
            out["reason"] = "model price could not be computed — see the chain"
    return out


def three_cases(base: dict, drivers: dict, market: dict,
                assumptions: Optional[dict] = None,
                deltas: Optional[dict] = None) -> dict:
    """Downside / base / upside: the same chain with CASE_DELTAS (ASSUMED)
    added to the drivers. Each case carries `case` and `case_deltas` so the
    Den prints exactly what moved. A delta on a missing driver leaves it
    missing (DATA_NEEDED flows through)."""
    deltas = deltas if isinstance(deltas, dict) else CASE_DELTAS
    drivers = drivers if isinstance(drivers, dict) else {}
    out: Dict[str, Any] = {}
    for case in CASES:
        d_case = {str(k): float(v) for k, v in (deltas.get(case) or {}).items()
                  if num(v) is not None}
        d2 = dict(drivers)
        for k, step in d_case.items():
            v = num(d2.get(k))
            if v is None and k == "pe_bridge_extra_pct":
                v = 0.0
            if v is not None:
                d2[k] = v + step
        sc = fy_scenario(base, d2, market, assumptions)
        sc["case"] = case
        sc["case_deltas"] = d_case
        out[case] = sc
    return out


def chain_formulas() -> List[dict]:
    """[{cell, key, label, formula}] for every computed cell of the chain
    (inputs excluded) — what the Den's formulas section prints. Runs the
    chain once on a synthetic, fully populated base so every formula is
    emitted; the values are discarded (owner order 2026-10-14)."""
    base = {"revenue": 1000.0, "ebitda": 180.0, "pat": 100.0, "tax": 30.0,
            "employee_cost": 500.0, "shares": 10.0, "dividend_ps": 2.0,
            "equity": 600.0, "borrowings": 50.0, "cash": 100.0,
            "revenue_prev": 900.0, "pat_prev": 90.0, "eps_prev": 9.0,
            "price": 150.0, "pe": 15.0, "net_cash": 50.0}
    drivers = {k: (v if isinstance(v, (int, float)) else 0.0)
               for k, v in SHEET_DRIVERS.items()} if isinstance(SHEET_DRIVERS, dict) else {}
    market = {k: (v if isinstance(v, (int, float)) else 0.0)
              for k, v in SHEET_MARKET.items()} if isinstance(SHEET_MARKET, dict) else {}
    try:
        sc = fy_scenario(base, drivers, market)
        chain = sc.get("chain") or []
    except Exception:                                      # noqa: BLE001
        chain = []
    out = []
    for c in chain:
        if not isinstance(c, dict) or c.get("formula") in (None, "", "input", "base",
                                                           "drivers", "market", "assumptions"):
            continue
        out.append({"cell": c.get("cell", ""), "key": c.get("key", ""),
                    "label": c.get("label", ""), "formula": c.get("formula", "")})
    return out


# -------------------------------------------------------------- stage 17

def _case_num(cases: Any, case: str, key: str) -> Optional[float]:
    if not isinstance(cases, dict):
        return None
    sc = cases.get(case)
    return num(sc.get(key)) if isinstance(sc, dict) else None


def multiple_band(peer_multiple: Any = None,
                  own_multiple_history: Optional[list] = None,
                  *, peer_only_point: bool = False) -> Optional[dict]:
    """[min(peer, own low), mean(peer, own median), max(peer, own high)].
    Own history alone → [low, median, high]. Peer alone → a point band only
    when `peer_only_point` (the EV routes); else None."""
    hist = [h for h in (num(x) for x in (own_multiple_history or [])) if h is not None and h > 0]
    p = num(peer_multiple)
    if p is not None and p <= 0:
        p = None
    if hist:
        lo, hi, med = min(hist), max(hist), float(median(hist))
        if p is not None:
            return {"low": min(p, lo), "mid": (p + med) / 2.0, "high": max(p, hi),
                    "source": f"peer {p:.2f} + own history ({len(hist)} readings, "
                              f"low {lo:.2f} / median {med:.2f} / high {hi:.2f})"}
        return {"low": lo, "mid": med, "high": hi,
                "source": f"own history only ({len(hist)} readings)"}
    if p is not None and peer_only_point:
        return {"low": p, "mid": p, "high": p,
                "source": f"peer {p:.2f} only — a single multiple, no own history"}
    return None


def _per_case_metric(cases, overrides, case, single, num_key, den_key="shares"):
    if isinstance(overrides, dict) and num(overrides.get(case)) is not None:
        return num(overrides.get(case)), "override"
    if num(single) is not None:
        return num(single), "argument"
    a, b = _case_num(cases, case, num_key), _case_num(cases, case, den_key)
    if num_key == "eps":
        return a, "case"
    if a is None or b is None or b == 0:
        return None, "case"
    return a / b, "case"


def _finish(family: str, method_text: str, vals: Dict[str, Any], band: Any,
            per_share: dict, bridge: dict, sensitivity: Any, assumptions_used: dict,
            data_needed: List[str], notes: List[str], price: Any) -> dict:
    low, base, high = vals.get("low"), vals.get("base"), vals.get("high")
    nums = [num(low), num(base), num(high)]
    if all(x is not None for x in nums):
        if not (nums[0] <= nums[1] <= nums[2]):
            nums = sorted(nums)
            notes.append("a case inverted (downside above base or base above upside) — "
                         "values sorted so low ≤ base ≤ high")
        low, base, high = (xround(x, 2) for x in nums)
        status = "OK"
    else:
        reason = "; ".join(data_needed) or "range incomplete"
        low = low if num(low) is not None else dn(reason)
        base = base if num(base) is not None else dn(reason)
        high = high if num(high) is not None else dn(reason)
        status = "DATA_NEEDED"
    p = num(price)
    vs = None
    if p is not None and p > 0 and status == "OK":
        vs = {"low": round((low / p - 1) * 100, 2), "base": round((base / p - 1) * 100, 2),
              "high": round((high / p - 1) * 100, 2)}
    out = {"family": family, "method_text": method_text, "status": status,
           "low": low, "base": base, "high": high,
           "multiple_band": band, "per_share": per_share, "bridge": bridge,
           "sensitivity": sensitivity, "assumptions_used": assumptions_used,
           "data_needed": data_needed, "notes": notes,
           "price": p, "vs_price_pct": vs}
    if status != "OK":
        out["reason"] = "; ".join(data_needed) or "range incomplete"
    return out


def earnings_grid(cases: Any) -> dict:
    """3×3 price grid over growth {−1,0,+1}pp × margin {−1,0,+1}pp plus a
    multiple row of {−10,0,+10} points on the P/E bridge. Every cell is a
    fresh fy_scenario run from the base case's own inputs — nothing scaled."""
    sc = cases.get("base") if isinstance(cases, dict) else None
    inp = sc.get("inputs") if isinstance(sc, dict) else None
    if not isinstance(inp, dict):
        return dn("the base case carries no inputs — the grid needs a fy_scenario case")
    b, d, m, a = (inp.get(k) or {} for k in ("base", "drivers", "market", "assumptions"))
    g0, m0 = num(d.get("revenue_growth_pct")), num(d.get("base_ebitda_margin_pct"))
    if g0 is None or m0 is None:
        return dn("revenue_growth_pct / base_ebitda_margin_pct missing in the base case")
    x0 = num(d.get("pe_bridge_extra_pct")) or 0.0
    prices: List[List[Any]] = []
    for dg in GRID_GROWTH_STEPS_PP:
        row = []
        for dm in GRID_MARGIN_STEPS_PP:
            s = fy_scenario(b, {**d, "revenue_growth_pct": g0 + dg,
                                "base_ebitda_margin_pct": m0 + dm}, m, a)
            row.append(s["model_price"])
        prices.append(row)
    mult_row = []
    for dx in GRID_MULTIPLE_STEPS_PCT:
        s = fy_scenario(b, {**d, "pe_bridge_extra_pct": x0 + dx}, m, a)
        mult_row.append({"multiple_pct": dx, "closing_pe": s["closing_pe"],
                         "price": s["model_price"]})
    return {"status": "OK",
            "method": "each cell re-run through fy_scenario from the base case's inputs",
            "rows_are": "revenue growth step (pp)", "cols_are": "base EBITDA margin step (pp)",
            "growth_steps_pp": list(GRID_GROWTH_STEPS_PP),
            "margin_steps_pp": list(GRID_MARGIN_STEPS_PP),
            "prices": prices, "centre": prices[1][1],
            "multiple_steps_pct": list(GRID_MULTIPLE_STEPS_PCT),
            "multiple_row": mult_row,
            "multiple_note": "points added to the P/E bridge total (E132), the CASE_DELTAS lever"}


def justified_range(family: str, cases: dict, *, price: Optional[float],
                    peer_multiple: Optional[float] = None,
                    own_multiple_history: Optional[List[float]] = None,
                    per_share_overrides: Optional[dict] = None,
                    roe_pct: Optional[float] = None, bvps: Optional[float] = None,
                    ebitda_per_share: Optional[float] = None,
                    net_debt_per_share: Optional[float] = None,
                    minorities_per_share: Optional[float] = None,
                    rf_pct: Optional[float] = None, beta: Optional[float] = None,
                    erp_pct: Optional[float] = None,
                    growth_pct: Optional[float] = None,
                    roe_step_pp: Optional[float] = None) -> dict:
    """Stage 17: the justified per-share range for the stock's route.

    `roe_step_pp` (Den draft, owner order 2026-10-14) replaces the ASSUMED
    BOOK_ROE_STEP_PP for the bank route's downside / upside ROE; None = the
    module default.

    `per_share_overrides` = {case: metric} replaces the per-share metric the
    route reads from the cases (EPS for earnings, BVPS for book, EBITDA per
    share for ev_ebitda, revenue per share for ev_sales) — e.g. a metal's
    MID-CYCLE EBITDA per share."""
    fam = str(family or "").strip().lower()
    overrides = per_share_overrides if isinstance(per_share_overrides, dict) else {}
    notes: List[str] = []
    missing: List[str] = []
    erp = num(erp_pct) if num(erp_pct) is not None else ASSUMED["equity_risk_premium_pct"]["value"]
    g = num(growth_pct) if num(growth_pct) is not None else ASSUMED["long_run_growth_pct"]["value"]

    if fam == "earnings":
        eps, src = {}, {}
        for c in CASES:
            eps[c], src[c] = _per_case_metric(cases, overrides, c, None, "eps")
        pe = {c: _case_num(cases, c, "closing_pe") for c in CASES}
        per_share = {}
        for c in CASES:
            if eps[c] is not None and pe[c] is not None:
                per_share[c] = xround(eps[c] * pe[c], 2)
            else:
                per_share[c] = dn(f"{c}: EPS or closing P/E missing")
        band = multiple_band(peer_multiple, own_multiple_history)
        used = {"band_mid_rule": ASSUMED["band_mid_rule"]}
        if band is None:
            peer_note = (f" (peer P/E {num(peer_multiple):.2f} noted; own 5-year history "
                         f"missing, so no band)") if num(peer_multiple) is not None else ""
            if all(pe[c] is not None for c in CASES):
                band = {"low": pe["downside"], "mid": pe["base"], "high": pe["upside"],
                        "source": "no peer/own band: case P/E only" + peer_note}
            else:
                band = dn("no peer/own band and the cases carry no closing P/E")
            used["earnings_band_fallback"] = ASSUMED["earnings_band_fallback"]
            vals = {"low": per_share["downside"], "base": per_share["base"],
                    "high": per_share["upside"]}
        else:
            vals = {}
            for c, edge in (("downside", "low"), ("base", "mid"), ("upside", "high")):
                vals[{"downside": "low", "base": "base", "upside": "high"}[c]] = (
                    xround(eps[c] * band[edge], 2) if eps[c] is not None
                    else dn(f"{c}: EPS missing"))
        for c in CASES:
            if eps[c] is None:
                missing.append(f"{c} EPS")
        b_eps = eps["base"]
        bridge = {"eps": eps, "eps_source": src, "case_closing_pe": pe,
                  "forward_pe_at_price": (round(num(price) / b_eps, 2)
                                          if num(price) and b_eps else None),
                  "pe_bridge_additive_whatif": True}
        method = ("price = EPS × P/E per case. With a peer/own band: low = downside EPS × "
                  "band low, base = base EPS × band mid, high = upside EPS × band high; "
                  "without one, each case's own closing P/E (the owner's additive "
                  "what-if bridge).")
        sens = earnings_grid(cases)
        used["case_deltas"] = ASSUMED["case_deltas"]
        used["grid_steps"] = {"growth_pp": GRID_GROWTH_STEPS_PP, "margin_pp": GRID_MARGIN_STEPS_PP,
                              "multiple_pct": GRID_MULTIPLE_STEPS_PCT}
        return _finish(fam, method, vals, band, per_share, bridge, sens, used,
                       missing, notes, price)

    if fam == "book":
        rf, bt = num(rf_pct), num(beta)
        roe = num(roe_pct) if num(roe_pct) is not None else _case_num(cases, "base", "roe_pct")
        bv = num(bvps) if num(bvps) is not None else _case_num(cases, "base", "bvps")
        for name, v in (("rf_pct", rf), ("beta", bt), ("roe_pct", roe), ("bvps", bv)):
            if v is None:
                missing.append(f"{name} missing")
        used = {"book_roe_step_pp": ASSUMED["book_roe_step_pp"],
                "equity_risk_premium_pct": {"value": erp, "source": ASSUMED[
                    "equity_risk_premium_pct"]["source"] if num(erp_pct) is None else "typed"},
                "long_run_growth_pct": {"value": g, "source": ASSUMED[
                    "long_run_growth_pct"]["source"] if num(growth_pct) is None else "typed"}}
        method = ("justified P/B = (ROE − g) / (COE − g), COE = rf + β × ERP; price = "
                  "justified P/B × BVPS; downside / upside = ROE ∓ 2pp")
        if missing:
            return _finish(fam, method, {}, None, {c: dn("; ".join(missing)) for c in CASES},
                           {}, dn("grid built for the earnings route only"), used,
                           missing, notes, price)
        coe = rf + bt * erp
        bridge: Dict[str, Any] = {"coe_pct": coe, "rf_pct": rf, "beta": bt, "erp_pct": erp,
                                  "growth_pct": g, "roe_pct": {}, "justified_pb": {},
                                  "bvps": {}, "peer_pb": num(peer_multiple)}
        if coe <= g:
            missing.append(f"cost of equity {coe:g}% at or below growth {g:g}% — "
                           "(ROE−g)/(COE−g) has no answer")
            return _finish(fam, method, {}, None, {c: dn(missing[-1]) for c in CASES},
                           bridge, dn("grid built for the earnings route only"), used,
                           missing, notes, price)
        per_share = {}
        rs = float(num(roe_step_pp)) if num(roe_step_pp) is not None else BOOK_ROE_STEP_PP
        for c, step in (("downside", -rs), ("base", 0.0), ("upside", rs)):
            r = roe + step
            bvc = num(overrides.get(c)) if num(overrides.get(c)) is not None else bv
            pb = (r - g) / (coe - g)
            bridge["roe_pct"][c] = r
            bridge["justified_pb"][c] = round(pb, 4)
            bridge["bvps"][c] = bvc
            if pb <= 0:
                per_share[c] = dn(f"{c}: ROE {r:g}% at or below growth {g:g}% — "
                                  "no positive justified P/B")
                missing.append(per_share[c]["reason"])
            else:
                per_share[c] = xround(pb * bvc, 2)
        jp = bridge["justified_pb"]
        band = {"low": jp["downside"], "mid": jp["base"], "high": jp["upside"],
                "source": "justified P/B (ROE − g)/(COE − g), ROE ∓ 2pp"}
        if bridge["peer_pb"] is not None:
            notes.append(f"peer P/B {bridge['peer_pb']:.2f} shown as a cross-check only")
        vals = {"low": per_share["downside"], "base": per_share["base"],
                "high": per_share["upside"]}
        return _finish(fam, method, vals, band, per_share, bridge,
                       dn("grid built for the earnings route only"), used, missing,
                       notes, price)

    if fam in ("ev_ebitda", "ev_sales"):
        label = "EV/EBITDA" if fam == "ev_ebitda" else "EV/Sales"
        mkey = "ebitda" if fam == "ev_ebitda" else "revenue"
        single = ebitda_per_share if fam == "ev_ebitda" else None
        band = multiple_band(peer_multiple, own_multiple_history, peer_only_point=True)
        used = {"band_mid_rule": ASSUMED["band_mid_rule"],
                "ev_peer_only_band": ASSUMED["ev_peer_only_band"]}
        if band is None:
            missing.append(f"no {label} band (peer multiple or own history needed)")
        metric, msrc = {}, {}
        for c in CASES:
            metric[c], msrc[c] = _per_case_metric(cases, overrides, c, single, mkey)
            if metric[c] is None:
                missing.append(f"{c} {mkey} per share missing")
        nd = num(net_debt_per_share)
        nd_src = "argument"
        if nd is None:
            nc, sh = _case_num(cases, "base", "net_cash"), _case_num(cases, "base", "shares")
            if nc is not None and sh:
                nd, nd_src = -nc / sh, "base case net cash (E36) ÷ shares (E28), sign flipped"
            else:
                missing.append("net_debt_per_share missing (no case net cash either)")
        mi = num(minorities_per_share)
        if mi is None:
            missing.append("minorities_per_share missing (pass 0 when there are none)")
        method = (f"EV per share = {label} multiple × {mkey} per share; equity per share = "
                  "EV − net debt per share − minorities per share; low / base / high = "
                  "downside × band low / base × band mid / upside × band high")
        if fam == "ev_ebitda":
            notes.append("Metals and other cyclicals: feed MID-CYCLE EBITDA per share "
                         "(per_share_overrides), never a peak or trough year — task book "
                         "Sector methods")
        bridge = {"metric": mkey, "metric_per_share": metric, "metric_source": msrc,
                  "net_debt_per_share": nd, "net_debt_source": nd_src if nd is not None else None,
                  "minorities_per_share": mi, "ev_per_share": {}}
        per_share = {}
        edge = {"downside": "low", "base": "mid", "upside": "high"}
        for c in CASES:
            if band is None or metric[c] is None or nd is None or mi is None:
                per_share[c] = dn("; ".join(missing) or "input missing")
                continue
            ev = metric[c] * band[edge[c]]
            bridge["ev_per_share"][c] = round(ev, 4)
            per_share[c] = xround(ev - nd - mi, 2)
        vals = {"low": per_share["downside"], "base": per_share["base"],
                "high": per_share["upside"]}
        return _finish(fam, method, vals, band if band is not None else dn(missing[0]),
                       per_share, bridge, dn("grid built for the earnings route only"),
                       used, missing, notes, price)

    if fam == "nav":
        reason = _UNHELD["nav"]
        bv_have = num(bvps) is not None or _case_num(cases, "base", "bvps") is not None
        fallback = None
        if bv_have:
            fallback = justified_range("book", cases, price=price,
                                       peer_multiple=peer_multiple,
                                       per_share_overrides=per_share_overrides,
                                       roe_pct=roe_pct, bvps=bvps, rf_pct=rf_pct,
                                       beta=beta, erp_pct=erp_pct, growth_pct=growth_pct,
                                       roe_step_pp=roe_step_pp)
        else:
            notes.append("no BVPS — the P/B fallback cannot be shown either")
        out = _finish(fam, "NAV per share (discounted land / project value) − net debt",
                      {}, dn(reason), {c: dn(reason) for c in CASES}, {},
                      dn("grid built for the earnings route only"), {}, [reason],
                      notes, price)
        out["fallback"] = fallback
        return out

    if fam in _UNHELD:
        reason = _UNHELD[fam]
        return _finish(fam, {"embedded_value": "P/EV on embedded value per share",
                             "sotp": "sum of the parts less a holding discount"}[fam],
                       {}, dn(reason), {c: dn(reason) for c in CASES}, {},
                       dn("grid built for the earnings route only"), {}, [reason],
                       notes, price)

    reason = f"unknown valuation family {family!r} (known: {', '.join(FAMILIES)})"
    return _finish(fam or str(family), "—", {}, dn(reason), {c: dn(reason) for c in CASES},
                   {}, dn(reason), {}, [reason], notes, price)


__all__ = ["SHEET_BASE", "SHEET_DRIVERS", "SHEET_MARKET", "SHEET_ASSUMPTIONS",
           "INPUT_CELLS", "CASE_DELTAS", "ASSUMED", "CASES", "FAMILIES",
           "xround", "fy_scenario", "three_cases", "multiple_band", "earnings_grid",
           "justified_range", "TT_NOTE", "E101_FORMULA", "e101_formula"]
