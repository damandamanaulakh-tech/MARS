"""MARS valuation chain — sequence stages 07-08: financial snapshot,
point-in-time validation and SOURCE + CURRENCY LOCK. DISPLAY ONLY.

Owner order 2026-10-14 ("lets hit the formulas work completion"), after the
2026-10-12 task book (Sequence map stages 07-08, the ADR trap found reading
for the valuation layer). Money impact: NONE — nothing in the engine, risk,
orders or the danger layer reads this module (package boundary, test-pinned).

WHAT IT DOES
------------
`snapshot(ticker)` reads ONE company's committed statements (the Yahoo
statements zip, data/fundamentals_yf_statements_2026-08-09.zip, through
trading.register_v4.load_statements; the curated fundamentals CSV +
Mongo overlay through register_v4.load_fundamentals when the zip has no
file for the name) and returns a JSON-safe, hash-locked record of the
canonical SNAPSHOT_FIELDS (trading.valuation) at one annual period:

* LINE ITEMS — every canonical field maps to an ordered list of Yahoo row
  labels (`LINE_ITEMS`); the first label holding a value at the chosen
  period wins and is recorded in `sources`. That ONE label then feeds the
  field's whole history, so a year is never compared with a differently
  defined year. A label absent for a name, or blank at the period, is
  DATA_NEEDED naming the label — banks legitimately have no EBITDA or cost
  of revenue. Capex is kept AS REPORTED: Yahoo stores "Capital
  Expenditure" NEGATIVE (cash out); free cash flow = OCF + capex.
* POINT IN TIME — the zip's columns are period ENDS, not publication
  dates. publication_date = the supplied result date for that period, else
  period end + ASSUMED["publication_lag_days"] (flagged
  publication_assumed). With `asof`, only periods (annual AND quarterly)
  published on or before asof are visible: the chosen period, the history,
  the quarters and the TTM never read a figure the market had not seen.
  A current-snapshot share count is never used for an `asof` run (it
  would leak past asof).
* CURRENCY LOCK (stage 08, the ADR trap) — Yahoo serves names with a US
  ADR in US DOLLARS (INFY FY2026 Basic EPS 0.81, revenue 20.158 bn) while
  others on the same list arrive in rupees (HDFCBANK Basic EPS 45.89). So
  the list alone NEVER converts: a name on `USD_SUSPECTS` is converted
  only when the price/EPS signature agrees (implied P/E above
  ASSUMED["usd_signature_pe"]); a signature on a name NOT on the list, or
  a listed name with nothing to check against, locks the currency as
  DATA_NEEDED and every money field with it. Never converted silently;
  when converted, every period carries the rate and the rate's date.
* BASIS — Yahoo does not say consolidated vs standalone. `basis` is
  "unknown" and data_needed["basis"] says why; never guessed.
* TTM — the sum of the newest 4 CONSECUTIVE published quarters. Yahoo
  often skips a quarter (TCS has no 2025-09-30 column), and four quarters
  spanning 15 months are not a trailing twelve months, so a gap is
  DATA_NEEDED naming the missing quarter end.
* LOCK — `lock_hash` = sha256 of the canonical JSON of {ticker, source,
  period_end, currency, fields}: identical inputs give the identical hash,
  any field change moves it.

Units: rupees ABSOLUTE (the zip's unit); converted names in rupees at the
period-end USDINR close (data/usdinr_daily.csv). Shares are counts.

ASSUMED (every number here that is not a published identity; printed with
its source by the Den): publication_lag_days 45 · usd_signature_pe 300 ·
the USD_SUSPECTS list · the FX rule (period-end close for every money
field, flows included). Nothing else is a choice.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from trading.valuation import SNAPSHOT_FIELDS, dn, is_dn, num

log = logging.getLogger(__name__)

VERSION = "2026-10-14"

_ROOT = Path(__file__).resolve().parents[2]
_USDINR_CSV = _ROOT / "data" / "usdinr_daily.csv"
_SHARES_CSV = _ROOT / "data" / "shares_outstanding.csv"

# Names with a US ADR — Yahoo may serve their statements in US dollars.
USD_SUSPECTS: Tuple[str, ...] = (
    "INFY", "WIPRO", "HDFCBANK", "ICICIBANK", "DRREDDY", "TATAMOTORS",
)

ASSUMED: Dict[str, Any] = {
    "publication_lag_days": 45,
    "usd_signature_pe": 300.0,
    "usd_suspects": USD_SUSPECTS,
    "fx_rule": "period_end_close",
}
ASSUMED_SOURCE: Dict[str, str] = {
    "publication_lag_days": (
        "task book 2026-10-12 — Yahoo gives period ends, not publication "
        "dates; NSE result dates not fetched yet (SEBI LODR allows 60 days "
        "for the annual audited result, so 45 can be early for a late "
        "filer)"),
    "usd_signature_pe": (
        "ASSUMED here — a rupee price over a dollar EPS reads ~85-95x the "
        "true P/E; no Indian large cap trades above 300x on rupee EPS"),
    "usd_suspects": "task book 2026-10-12 — names with a US ADR",
    "fx_rule": (
        "ASSUMED here — every money field (flows included) converted at the "
        "USDINR close on or before its period end; average-rate translation "
        "not used"),
}

# Canonical field → ordered Yahoo row labels as (statement, label).
# Statement keys: 'is' income, 'bs' balance sheet, 'cf' cash flow.
LINE_ITEMS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    # income statement
    "revenue": (("is", "Total Revenue"), ("is", "Operating Revenue")),
    "cost_of_revenue": (("is", "Cost Of Revenue"),),
    "gross_profit": (("is", "Gross Profit"),),
    "operating_income": (("is", "Operating Income"),
                         ("is", "Total Operating Income As Reported")),
    "ebitda": (("is", "EBITDA"),),
    "ebit": (("is", "EBIT"),),
    "d_and_a": (("is", "Reconciled Depreciation"),
                ("is", "Depreciation And Amortization In Income Statement")),
    "interest_expense": (("is", "Interest Expense"),),
    "interest_income": (("is", "Interest Income"),),
    "pretax_income": (("is", "Pretax Income"),),
    "tax": (("is", "Tax Provision"),),
    "tax_rate": (("is", "Tax Rate For Calcs"),),
    "net_income": (("is", "Net Income"),),
    "net_income_common": (("is", "Net Income Common Stockholders"),
                          ("is", "Net Income")),
    "minority_interest": (("is", "Minority Interests"),),
    "unusual_items": (("is", "Total Unusual Items"),),
    "special_income_charges": (("is", "Special Income Charges"),),
    "normalized_income_yahoo": (("is", "Normalized Income"),),
    "basic_shares_avg": (("is", "Basic Average Shares"),),
    "diluted_shares_avg": (("is", "Diluted Average Shares"),),
    "basic_eps": (("is", "Basic EPS"),),
    "diluted_eps": (("is", "Diluted EPS"),),
    "employee_cost": (("is", "Salaries And Wages"),),
    # balance sheet
    "shares_outstanding": (("bs", "Ordinary Shares Number"),
                           ("bs", "Share Issued")),   # then shares_csv
    "total_debt": (("bs", "Total Debt"),),
    "cash": (("bs", "Cash And Cash Equivalents"),
             ("bs", "Cash Cash Equivalents And Short Term Investments"),
             ("cf", "End Cash Position")),
    "total_assets": (("bs", "Total Assets"),),
    "equity": (("bs", "Stockholders Equity"), ("bs", "Common Stock Equity")),
    "minority_interest_bs": (("bs", "Minority Interest"),),
    "receivables": (("bs", "Accounts Receivable"), ("bs", "Receivables")),
    "inventory": (("bs", "Inventory"),),
    "payables": (("bs", "Accounts Payable"), ("bs", "Payables")),
    "current_assets": (("bs", "Current Assets"),),
    "current_liabilities": (("bs", "Current Liabilities"),),
    "goodwill": (("bs", "Goodwill"),),
    "intangibles": (("bs", "Other Intangible Assets"),),
    "invested_capital": (("bs", "Invested Capital"),),
    "working_capital": (("bs", "Working Capital"),),
    "net_ppe": (("bs", "Net PPE"),),
    # cash flow (capex is NEGATIVE as Yahoo reports it — kept as reported)
    "ocf": (("cf", "Operating Cash Flow"),),
    "capex": (("cf", "Capital Expenditure"),),
    "fcf": (("cf", "Free Cash Flow"),),
    "dividends_paid": (("cf", "Cash Dividends Paid"),),
    "buybacks": (("cf", "Repurchase Of Capital Stock"),),
}
assert tuple(LINE_ITEMS) == SNAPSHOT_FIELDS, "LINE_ITEMS must cover SNAPSHOT_FIELDS in order"

# Two balance-sheet lines outside the canonical set, carried for Altman Z's
# retained-earnings leg (terms.py). Chosen period only; money, so converted.
EXTRA_LINE_ITEMS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "retained_earnings": (("bs", "Retained Earnings"),),
    "capital_stock": (("bs", "Capital Stock"),),
}

# Curated fundamentals CSV (source "fundamentals_csv") → canonical names.
FUND_ANNUAL: Dict[str, str] = {
    "revenue": "annualTotalRevenue",
    "gross_profit": "annualGrossProfit",
    "operating_income": "annualOperatingIncome",
    "net_income": "annualNetIncome",
    "basic_eps": "annualBasicEPS",
    "fcf": "annualFreeCashFlow",
    "ocf": "annualOperatingCashFlow",
    "equity": "annualStockholdersEquity",
    "total_assets": "annualTotalAssets",
    "total_debt": "annualTotalDebt",
}
FUND_QUARTERLY: Dict[str, str] = {
    "revenue": "quarterlyTotalRevenue",
    "net_income": "quarterlyNetIncome",
    "operating_income": "quarterlyOperatingIncome",
    "basic_eps": "quarterlyBasicEPS",
}

QUARTERLY_FIELDS: Tuple[str, ...] = ("revenue", "net_income",
                                     "operating_income", "basic_eps")
QUARTERS_KEPT = 6
MAX_ANNUAL_PERIODS = 5

# Counts and ratios are never currency-converted.
NON_MONEY: frozenset = frozenset({"basic_shares_avg", "diluted_shares_avg",
                                  "shares_outstanding", "tax_rate"})
MONEY_FIELDS: Tuple[str, ...] = tuple(f for f in SNAPSHOT_FIELDS
                                      if f not in NON_MONEY)

BASIS_REASON = ("Yahoo does not say consolidated vs standalone; "
                "NSE filing needed")


# ------------------------------------------------------------------ loaders
def _default_statements(sym: str) -> dict:
    from trading import register_v4          # display-only sibling
    return register_v4.load_statements(sym)


def _default_fundamentals(sym: str) -> dict:
    from trading import register_v4          # display-only sibling
    return register_v4.load_fundamentals(sym)


_FX: Optional[Tuple[List[str], List[float]]] = None


def _load_usdinr() -> Tuple[List[str], List[float]]:
    global _FX
    if _FX is None:
        rows: List[Tuple[str, float]] = []
        try:
            with open(_USDINR_CSV) as f:
                for r in csv.DictReader(f):
                    v = num(r.get("close"))
                    d = (r.get("date") or "")[:10]
                    if v is not None and v > 0 and len(d) == 10:
                        rows.append((d, v))
        except OSError:
            log.warning("valuation.snapshot: %s missing", _USDINR_CSV)
        rows.sort()
        _FX = ([d for d, _ in rows], [v for _, v in rows])
    return _FX


def usdinr_at(date_iso: str) -> Optional[Tuple[float, str]]:
    """(rate, fx_date) = the USDINR close on or before `date_iso`, or None
    when the file has no close that early (or is missing)."""
    dates, vals = _load_usdinr()
    i = bisect_right(dates, str(date_iso)[:10])
    if i == 0:
        return None
    return float(vals[i - 1]), dates[i - 1]


_SHARES: Optional[Dict[str, float]] = None


def _load_shares_csv() -> Dict[str, float]:
    global _SHARES
    if _SHARES is None:
        out: Dict[str, float] = {}
        try:
            with open(_SHARES_CSV) as f:
                for r in csv.DictReader(f):
                    v = num(r.get("shares_outstanding"))
                    if v is not None and v > 0:
                        out[_norm(r.get("symbol", ""))] = v
        except OSError:
            log.warning("valuation.snapshot: %s missing", _SHARES_CSV)
        _SHARES = out
    return _SHARES


# ------------------------------------------------------------------ helpers
def _norm(ticker: str) -> str:
    s = str(ticker or "").strip().upper()
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s


def _iso(v: Any) -> Optional[str]:
    s = str(v)[:10]
    try:
        date.fromisoformat(s)
    except ValueError:
        return None
    return s


def _plus_days(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=int(days))).isoformat()


def _ym(iso: str) -> int:
    return int(iso[:4]) * 12 + int(iso[5:7]) - 1


def _ym_label(ym: int) -> str:
    y, m = divmod(ym, 12)
    m += 1
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - timedelta(days=1)).isoformat()


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def lock_hash_of(record: dict) -> str:
    """sha256 hex of the canonical JSON of the locked part of a record."""
    locked = {k: record.get(k) for k in
              ("ticker", "source", "period_end", "currency", "fields")}
    return hashlib.sha256(_canon(locked).encode("utf-8")).hexdigest()


class _Src:
    """Uniform view over one source: candidate series per canonical field.

    candidates(field) → [(label, {period: value})] in priority order, only
    for labels the source actually carries. wanted(field) → every label
    that would have been accepted (for the DATA_NEEDED reason)."""

    def __init__(self, kind: str, annual: Dict[str, List[Tuple[str, Dict[str, float]]]],
                 wanted: Dict[str, List[str]], periods: List[str],
                 quarterly: Dict[str, List[Tuple[str, Dict[str, float]]]],
                 quarters: List[str]):
        self.kind = kind
        self.annual = annual
        self.wanted = wanted
        self.periods = periods          # newest first
        self.quarterly = quarterly
        self.quarters = quarters        # newest first

    def candidates(self, field: str):
        return self.annual.get(field, [])


def _frame_series(df: Any, label: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    try:
        row = df.loc[label]
    except Exception:                                          # noqa: BLE001
        return out
    if hasattr(row, "ndim") and getattr(row, "ndim", 1) > 1:  # duplicate label
        row = row.iloc[0]
    for col, v in row.items():
        p = _iso(col)
        f = num(v)
        if p is not None and f is not None:
            out[p] = f
    return out


def _has_label(df: Any, label: str) -> bool:
    try:
        return label in df.index
    except Exception:                                          # noqa: BLE001
        return False


def _cols(df: Any) -> List[str]:
    try:
        return [c for c in (_iso(x) for x in df.columns) if c]
    except Exception:                                          # noqa: BLE001
        return []


def _zip_source(stm: dict) -> Optional[_Src]:
    frames = {k: stm.get(k) for k in ("is", "bs", "cf", "qis")
              if stm.get(k) is not None}
    if not any(k in frames for k in ("is", "bs", "cf")):
        return None
    periods = sorted({p for k in ("is", "bs", "cf") if k in frames
                      for p in _cols(frames[k])}, reverse=True)
    annual, wanted = {}, {}
    for field, items in list(LINE_ITEMS.items()) + list(EXTRA_LINE_ITEMS.items()):
        wanted[field] = [f"{k}:{lab}" for k, lab in items]
        annual[field] = [(f"{k}:{lab}", _frame_series(frames[k], lab))
                         for k, lab in items
                         if k in frames and _has_label(frames[k], lab)]
    quarters: List[str] = []
    quarterly: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
    if "qis" in frames:
        quarters = sorted(set(_cols(frames["qis"])), reverse=True)
        for field in QUARTERLY_FIELDS:
            quarterly[field] = [(f"qis:{lab}", _frame_series(frames["qis"], lab))
                                for k, lab in LINE_ITEMS[field]
                                if k == "is" and _has_label(frames["qis"], lab)]
    return _Src("yahoo_statements_zip", annual, wanted, periods,
                quarterly, quarters)


def _fund_source(fund: dict) -> Optional[_Src]:
    if not fund:
        return None
    annual, wanted = {}, {}
    periods: set = set()
    for field in list(LINE_ITEMS) + list(EXTRA_LINE_ITEMS):
        name = FUND_ANNUAL.get(field)
        wanted[field] = [f"fundamentals_csv:{name}"] if name else []
        series: Dict[str, float] = {}
        if name and name in fund:
            for k, v in (fund.get(name) or {}).items():
                p, f = _iso(k), num(v)
                if p is not None and f is not None:
                    series[p] = f
            annual[field] = [(f"fundamentals_csv:{name}", series)]
            periods.update(series)
        else:
            annual[field] = []
    if not periods:
        return None
    quarterly: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
    qs: set = set()
    for field, name in FUND_QUARTERLY.items():
        series = {}
        for k, v in (fund.get(name) or {}).items():
            p, f = _iso(k), num(v)
            if p is not None and f is not None:
                series[p] = f
        quarterly[field] = [(f"fundamentals_csv:{name}", series)] if series else []
        qs.update(series)
    return _Src("fundamentals_csv", annual, wanted,
                sorted(periods, reverse=True), quarterly,
                sorted(qs, reverse=True))


def _result_dates_for(result_dates: Optional[dict], sym: str,
                      ticker: str) -> Dict[str, str]:
    if not result_dates:
        return {}
    for key in (sym, f"{sym}.NS", ticker, str(ticker).upper()):
        got = result_dates.get(key)
        if isinstance(got, dict):
            return {str(k)[:10]: str(v)[:10] for k, v in got.items()
                    if _iso(k) and _iso(v)}
    return {}


def _empty_record(sym: str, source: str) -> dict:
    return {
        "ticker": sym, "version": VERSION, "source": source,
        "period_end": None, "publication_date": None,
        "publication_assumed": None, "basis": "unknown",
        "currency": None, "fx": {}, "fields": {}, "extras": {},
        "history": {}, "quarterly": {}, "ttm": {}, "ttm_quarters": [],
        "sources": {}, "currency_check": {}, "warnings": [],
        "data_needed": {"basis": BASIS_REASON}, "assumed": dict(
            (k, list(v) if isinstance(v, tuple) else v)
            for k, v in ASSUMED.items()),
    }


def _all_dn(rec: dict, reason: str) -> dict:
    for f in SNAPSHOT_FIELDS:
        rec["fields"][f] = dn(reason)
        rec["data_needed"][f] = reason
    for f in EXTRA_LINE_ITEMS:
        rec["extras"][f] = dn(reason)
    for f in QUARTERLY_FIELDS:
        rec["ttm"][f] = dn(reason)
    return rec


def _finish(rec: dict) -> dict:
    rec["lock_hash"] = lock_hash_of(rec)
    return rec


# ----------------------------------------------------------------- snapshot
def snapshot(ticker: str, *, asof: Optional[str] = None,
             price: Optional[float] = None,
             statements: Optional[dict] = None,
             fundamentals: Optional[dict] = None,
             usdinr: Optional[Callable[[str], Optional[Tuple[float, str]]]] = None,
             result_dates: Optional[dict] = None,
             shares_csv: Optional[dict] = None) -> dict:
    """Stages 07-08: the locked financial snapshot of one company.

    Every data input defaults to the committed-file loader and can be
    injected (`statements={}` means "no statements", None means "load").
    `price` is the caller's stored close in rupees — used ONLY for the
    currency signature check. `asof` (ISO date) makes the record
    point-in-time: nothing published after it is read.
    """
    sym = _norm(ticker)
    asof_iso = _iso(asof) if asof is not None else None
    fx_fn = usdinr if usdinr is not None else usdinr_at
    rd = _result_dates_for(result_dates, sym, ticker)
    lag = int(ASSUMED["publication_lag_days"])

    stm = statements if statements is not None else _default_statements(sym)
    src = _zip_source(stm or {})
    if src is None:
        fund = fundamentals if fundamentals is not None else _default_fundamentals(sym)
        src = _fund_source(fund or {})
    if src is None:
        rec = _empty_record(sym, "none")
        reason = (f"no statements for {sym} in the statements zip or the "
                  "fundamentals csv")
        rec["currency"] = dn(reason)
        rec["data_needed"]["currency"] = reason
        return _finish(_all_dn(rec, reason))

    rec = _empty_record(sym, src.kind)
    warnings: List[str] = rec["warnings"]
    data_needed: Dict[str, str] = rec["data_needed"]

    def pub(p: str) -> Tuple[str, bool]:
        if p in rd:
            return rd[p], False
        return _plus_days(p, lag), True

    def visible(p: str) -> bool:
        return asof_iso is None or pub(p)[0] <= asof_iso

    # ---- choose the period (newest visible one that has Net Income)
    ni_series: Dict[str, float] = {}
    for _, s in src.candidates("net_income"):
        for p, v in s.items():
            ni_series.setdefault(p, v)
    periods = [p for p in src.periods if visible(p)][:MAX_ANNUAL_PERIODS]
    chosen = next((p for p in periods if p in ni_series), None)
    if chosen is None:
        if asof_iso is not None:
            first = min((pub(p)[0] for p in src.periods if p in ni_series),
                        default=None)
            reason = (f"no statement published by {asof_iso}"
                      + (f" (earliest publication {first})" if first else ""))
        else:
            reason = f"no income statement with Net Income for {sym}"
        rec["currency"] = dn(reason)
        data_needed["currency"] = reason
        return _finish(_all_dn(rec, reason))

    rec["period_end"] = chosen
    rec["publication_date"], rec["publication_assumed"] = pub(chosen)
    if rec["publication_assumed"]:
        warnings.append(
            f"publication date ASSUMED: period end {chosen} + {lag} days "
            f"({ASSUMED_SOURCE['publication_lag_days']})")
    hist_periods = [p for p in periods if p <= chosen]

    # ---- fields at the chosen period + one label per field for history
    raw_fields: Dict[str, Any] = {}
    raw_hist: Dict[str, Dict[str, float]] = {}
    for field in list(LINE_ITEMS) + list(EXTRA_LINE_ITEMS):
        cands = src.candidates(field)
        label, series = next(((lab, s) for lab, s in cands if chosen in s),
                             (None, None))
        if label is not None:
            raw_fields[field] = series[chosen]
            rec["sources"][field] = label
        else:
            hist_hit = next(((lab, s) for lab, s in cands
                             if any(p in s for p in hist_periods)), None)
            if hist_hit is not None:
                label, series = hist_hit
                rec["sources"][field] = label
                raw_fields[field] = dn(
                    f"line item {label} blank for {sym} at {chosen}")
            elif cands:
                raw_fields[field] = dn(
                    f"line item {cands[0][0]} blank for {sym} at {chosen}")
            else:
                want = " | ".join(src.wanted.get(field) or []) or field
                why = (f"line item {want} not in statements for {sym}"
                       if src.kind == "yahoo_statements_zip" else
                       f"{field} not carried by the fundamentals csv "
                       f"(no statements zip file for {sym})")
                raw_fields[field] = dn(why)
        if series and field in LINE_ITEMS:
            h = {p: series[p] for p in hist_periods if p in series}
            if h:
                raw_hist[field] = h

    # shares_outstanding: the shares csv is a CURRENT snapshot, not dated.
    if is_dn(raw_fields.get("shares_outstanding")):
        sh_map = shares_csv if shares_csv is not None else _load_shares_csv()
        row = (sh_map or {}).get(sym)
        v = num(row.get("shares_outstanding")) if isinstance(row, dict) else num(row)
        if v is not None and v > 0:
            if asof_iso is not None:
                raw_fields["shares_outstanding"] = dn(
                    "balance-sheet share count absent; the shares csv is a "
                    f"current snapshot and would leak past asof {asof_iso}")
            else:
                raw_fields["shares_outstanding"] = v
                rec["sources"]["shares_outstanding"] = (
                    "shares_csv:data/shares_outstanding.csv")
                warnings.append(
                    "shares_outstanding from data/shares_outstanding.csv is a "
                    f"current snapshot, not dated {chosen}")

    # ---- quarters (published ones only), newest 6
    qcols = [q for q in src.quarters if visible(q)][:QUARTERS_KEPT]
    raw_q: Dict[str, Dict[str, float]] = {}
    qsrc: Dict[str, str] = {}
    for field in QUARTERLY_FIELDS:
        for lab, s in src.quarterly.get(field, []):
            vals = {q: s[q] for q in qcols if q in s}
            if vals:
                raw_q[field] = vals
                qsrc[field] = lab
                break
    for f, lab in qsrc.items():
        rec["sources"][f"quarterly.{f}"] = lab

    # ---- TTM: newest 4 consecutive quarters, all present
    ttm_reason: Optional[str] = None
    ttm_qs: List[str] = []
    if not qcols:
        ttm_reason = "no published quarterly statement"
    else:
        anchor = _ym(qcols[0])
        want = [anchor - 3 * i for i in range(4)]
        have = {_ym(q): q for q in qcols}
        missing = [_ym_label(m) for m in want if m not in have]
        if missing:
            ttm_reason = ("TTM needs 4 consecutive quarters; quarter end(s) "
                          f"{', '.join(missing)} missing from the statements")
        else:
            ttm_qs = [have[m] for m in want]
    rec["ttm_quarters"] = ttm_qs

    # ---- currency lock
    on_list = sym in USD_SUSPECTS
    eps = num(raw_fields.get("basic_eps"))
    ni = num(raw_fields.get("net_income"))
    px = num(price)
    implied_pe = None
    if px is not None and px > 0 and eps is not None and eps > 0 \
            and ni is not None and ni > 0:
        implied_pe = px / eps
    signature = (implied_pe is not None
                 and implied_pe > float(ASSUMED["usd_signature_pe"]))
    rec["currency_check"] = {
        "on_adr_list": on_list, "price": px,
        "implied_pe": None if implied_pe is None else round(implied_pe, 4),
        "signature": signature,
        "signature_pe": float(ASSUMED["usd_signature_pe"]),
    }
    convert = False
    currency_dn: Optional[dict] = None
    if on_list and signature:
        convert = True
    elif signature:
        currency_dn = dn(f"currency unverified: price/EPS implies P/E "
                         f"{implied_pe:.0f}; not on the ADR list")
    elif on_list and implied_pe is None:
        if px is None:
            currency_dn = dn("currency unverified: on the ADR list but no "
                             "price to check")
        else:
            currency_dn = dn("currency unverified: on the ADR list but "
                             f"EPS / net income cannot be checked at {chosen}")
    if currency_dn is not None:
        rec["currency"] = currency_dn
        data_needed["currency"] = currency_dn["reason"]
    elif convert:
        rec["currency"] = "USD→INR (converted)"
    else:
        rec["currency"] = "INR"

    money = set(MONEY_FIELDS) | set(EXTRA_LINE_ITEMS)

    def fx_at(p: str) -> Optional[Tuple[float, str]]:
        got = fx_fn(p)
        if not got:
            return None
        rate, fx_date = got
        rate = num(rate)
        if rate is None or rate <= 0:
            return None
        rec["fx"][p] = {"rate": float(rate), "fx_date": str(fx_date)[:10]}
        return float(rate), str(fx_date)[:10]

    def money_value(field: str, value: Any, p: str) -> Any:
        """The value as it may be shown: converted, refused, or as-is."""
        if is_dn(value) or field not in money:
            return value
        if currency_dn is not None:
            return dn(currency_dn["reason"])
        if convert:
            got = fx_at(p)
            if got is None:
                return dn(f"no USDINR close on or before {p}")
            return float(value) * got[0]
        return float(value)

    for field in SNAPSHOT_FIELDS:
        v = money_value(field, raw_fields.get(field), chosen)
        rec["fields"][field] = v if is_dn(v) else float(v)
        if is_dn(v):
            data_needed[field] = v["reason"]
    for field in EXTRA_LINE_ITEMS:
        v = money_value(field, raw_fields.get(field), chosen)
        rec["extras"][field] = v if is_dn(v) else float(v)

    for field, h in raw_hist.items():
        if field in money and currency_dn is not None:
            rec["history"][field] = dn(currency_dn["reason"])
            continue
        out_h: Dict[str, float] = {}
        for p, v in h.items():
            mv = money_value(field, v, p)
            if is_dn(mv):
                warnings.append(f"history {field} {p}: {mv['reason']}")
            else:
                out_h[p] = float(mv)
        rec["history"][field] = out_h

    conv_q: Dict[str, Dict[str, float]] = {}
    for field, vals in raw_q.items():
        if currency_dn is not None:          # every quarterly field is money
            rec["quarterly"][field] = dn(currency_dn["reason"])
            continue
        out_q: Dict[str, float] = {}
        for q, v in vals.items():
            mv = money_value(field, v, q)
            if is_dn(mv):
                warnings.append(f"quarterly {field} {q}: {mv['reason']}")
            else:
                out_q[q] = float(mv)
        rec["quarterly"][field] = out_q
        conv_q[field] = out_q

    for field in QUARTERLY_FIELDS:
        if currency_dn is not None:
            rec["ttm"][field] = dn(currency_dn["reason"])
        elif ttm_reason is not None:
            rec["ttm"][field] = dn(ttm_reason)
        else:
            vals = conv_q.get(field, {})
            gap = [q for q in ttm_qs if q not in vals]
            if gap:
                rec["ttm"][field] = dn(
                    f"TTM {field}: quarter(s) {', '.join(gap)} blank")
            else:
                rec["ttm"][field] = float(sum(vals[q] for q in ttm_qs))

    if convert:
        r = rec["fx"].get(chosen)
        warnings.append(
            "money fields converted USD→INR at the period-end USDINR close"
            + (f" ({r['rate']} on {r['fx_date']} for {chosen})" if r else "")
            + "; shares, tax rate untouched")
        warnings.append(
            "year-on-year changes of converted flows include the USDINR move "
            "between period ends (spot, not average-rate translation)")
    return _finish(rec)


__all__ = ["snapshot", "usdinr_at", "lock_hash_of", "LINE_ITEMS",
           "EXTRA_LINE_ITEMS", "ASSUMED", "ASSUMED_SOURCE", "USD_SUSPECTS",
           "MONEY_FIELDS", "VERSION"]
