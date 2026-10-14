"""MARS valuation chain — sequence stages 07-18, DISPLAY ONLY.

Owner order 2026-10-14 ("lets hit the formulas work completion") after the
2026-10-12 task book (Tasks · Sequence map · Sector methods · Finance terms).
Money impact: NONE. Nothing in this package is read by engine.scan,
risk.can_open, orders, or the danger layer (test-pinned, the same boundary
as lens_v2 / register_v4 / triggers). The engine keeps buying on the
momentum score; stage 21 (permitted decision conditions), item 19
(valuation-derived exits) and Phase 4 (valuation as the base) are NOT here
and wait on the pre-registered test + the owner's go.

PACKAGE CONTRACT (every module follows it, tests pin it)
---------------------------------------------------------
* PURE: no network, no config mutation, no Mongo writes. Inputs come from
  the committed data files (data/fundamentals_yf_statements_2026-08-09.zip,
  data/fundamentals_n200_2026-08-09.csv + the Mongo overlay already merged
  by trading.register_v4.load_fundamentals, data/shares_outstanding.csv,
  data/corporate_actions_2010_2026.csv, data/usdinr_daily.csv,
  data/index_constituents/*nifty500list.csv) through small loader
  functions that a test can monkeypatch.
* NO FAKE NUMBERS: a missing input is `dn(reason)` — the DATA_NEEDED dict —
  never 0, never a guess. A result that depends on a DATA_NEEDED input is
  itself DATA_NEEDED and names the input.
* ASSUMED vs MEASURED: every constant that is not a published accounting
  identity lives in the module's `ASSUMED` dict with a one-line source
  ("owner sheet row 158", "task book", "ASSUMED here") so the Den can print
  it. Nothing is silently hard-coded.
* UNITS: rupees ABSOLUTE inside (the zip's unit), crore only when printing.
  Currency is locked at stage 08 (snapshot.py) — the ADR trap: INFY's
  statements arrive in US dollars.
* Every stage returns a plain dict (JSON-safe: floats, str, None, lists,
  dicts) so a record can be stored and read back unchanged.
"""
from __future__ import annotations

from typing import Any, Optional

DATA_NEEDED = "DATA_NEEDED"


def dn(reason: str, **extra: Any) -> dict:
    """The one DATA_NEEDED shape: {"status": "DATA_NEEDED", "reason": ...}."""
    out = {"status": DATA_NEEDED, "reason": str(reason)}
    out.update(extra)
    return out


def is_dn(v: Any) -> bool:
    return isinstance(v, dict) and v.get("status") == DATA_NEEDED


def num(v: Any) -> Optional[float]:
    """A finite float or None (NaN, DATA_NEEDED dicts, blanks → None)."""
    if v is None or is_dn(v):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


# Canonical field names of a locked snapshot (stage 08). Rupees absolute.
# snapshot.py maps the Yahoo statement line items onto these; every other
# module reads ONLY these names so a source change touches one file.
SNAPSHOT_FIELDS: tuple[str, ...] = (
    # income statement (annual, latest FY first in `history`)
    "revenue", "cost_of_revenue", "gross_profit", "operating_income",
    "ebitda", "ebit", "d_and_a", "interest_expense", "interest_income",
    "pretax_income", "tax", "tax_rate", "net_income", "net_income_common",
    "minority_interest", "unusual_items", "special_income_charges",
    "normalized_income_yahoo", "basic_shares_avg", "diluted_shares_avg",
    "basic_eps", "diluted_eps", "employee_cost",
    # balance sheet
    "shares_outstanding", "total_debt", "cash", "total_assets", "equity",
    "minority_interest_bs", "receivables", "inventory", "payables",
    "current_assets", "current_liabilities", "goodwill", "intangibles",
    "invested_capital", "working_capital", "net_ppe",
    # cash flow
    "ocf", "capex", "fcf", "dividends_paid", "buybacks",
)

__all__ = ["DATA_NEEDED", "dn", "is_dn", "num", "SNAPSHOT_FIELDS"]
