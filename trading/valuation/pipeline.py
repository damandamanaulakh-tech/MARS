"""Valuation chain orchestrator — sequence stages 07-18 in one pass,
DISPLAY ONLY (owner order 2026-10-14: "lets hit the formulas work
completion"). Money impact: NONE — nothing here is read by engine.scan,
risk.can_open, orders or the danger layer (test-pinned).

One stock in → one JSON-safe record out:

    snapshot (07-08, currency locked)  → route (00/09, chosen first)
    → share bridge (10) → normalised earnings (11) → quality (12)
    → three cases (15-16, the owner's Tata Tech sheet generalised)
    → justified range per route family (17) → verdict (18)
    + the 52 finance terms.

PURE: every outside input is a callable the caller injects (prices from
the store, index levels, the derived peer table) so a page render never
touches the network and a test can feed fixtures. What the chain does
not hold is DATA_NEEDED with its reason, never a quiet 0 — EXCEPT the
owner's P/E-bridge what-if rows (repo, CPI, GDP, Brent, institutional
change, delivery): the sheet treats those as optional inputs, so a
missing one counts 0 AND is listed on the record as `macro_data_needed`.

ASSUMED (printed on the Den): the base-year drivers are the latest FY's
own numbers (growth = last FY revenue growth, margin = last FY EBITDA
margin, PAT/EBITDA = last FY, payout = last FY); forward net cash =
base-year net cash; the currency effect applies to IT exporters only;
peers = the same route among the Nifty-200 statement set (a typed peer
list replaces them); mid-cycle MARGIN for the metals row only;
publication date = period end + 45 days (snapshot.py); case deltas
(scenarios.py); verdict bands (assess.py).

Batch 2 (owner choices 2026-10-21): the target starts from NORMALISED
PAT on every route (VAL-08); own P/E history on each year's normalised
PAT and own share count, odd years flagged (VAL-14a); one EV rule for the
stock and its peers (VAL-12a); lenders on filed net worth / tangible
book with ROE on the average book (VAL-02); power is not a lender
(VAL-12b); typed NAV / embedded value, holding-company and conglomerate
sum-of-parts (VAL-01 / VAL-14b).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from trading import valuation_context as _vc
from trading.valuation import dn, is_dn, num
from trading.valuation import assess as A
from trading.valuation import assumptions as ASM
from trading.valuation import normalise as N
from trading.valuation import route as R
from trading.valuation import scenarios as SC
from trading.valuation import shares as SH
from trading.valuation import snapshot as S
from trading.valuation import terms as T

log = logging.getLogger(__name__)

VERSION = "2026-10-21"
CR = 1e7                              # rupees → crore (the sheet's unit)

ASSUMED: Dict[str, dict] = {
    "driver_defaults": {
        "value": "revenue growth = last FY YoY; base EBITDA margin, PAT/EBITDA and "
                 "payout = the latest FY's own ratios; every cost/what-if driver 0; "
                 "forward net cash = base-year net cash",
        "source": "ASSUMED here 2026-10-14 — the owner's sheet types these per company"},
    "currency_effect_routes": {
        "value": [],
        "source": "owner choice 2026-10-21 (VALFIX batch 3): the IT-only rupee row E101 is "
                  "counted 0 in the live chain — the currency leg runs through EPS for "
                  "EVERY sector (trading/valuation/currency.py, shares by tier, USD + EUR); "
                  "the owner's Tata sheet keeps its own E101 rows cell for cell; the item-19 "
                  "exit record is built with e101_it_rupee=True — the rule the "
                  "pre-registered study measured (2026-10-21: E101 live for IT-services "
                  "routes, the batch-2 branch)"},
    "macro_counted_zero": {
        "value": ["repo", "cpi", "gdp", "brent", "institutional change", "delivery"],
        "source": "the sheet's what-if rows the app does not store — counted 0 and "
                  "listed DATA_NEEDED on every record"},
    "peer_definition": {
        "value": "median trailing multiple of the same route among the Nifty-200 "
                 "names with statements",
        "source": "ASSUMED here until the owner supplies peer groups (task book ask)"},
    "midcycle_ebitda": {
        "value": "metals row only: each case's EBITDA = E19 − E17 × (base-case margin E21 − "
                 "mean EBITDA margin of the stored FYs) ÷ 100, ÷ shares E28 (needs 3+ FYs); "
                 "every other EV row: the case's own EBITDA ÷ shares, no mid-cycle",
        "source": "owner choice 2026-10-21 (VAL-07): the sector table's 'EV/EBITDA at "
                  "MID-CYCLE margin', metals row only"},
    "min_history_for_own_band": {
        "value": 3, "source": "ASSUMED here: fewer FY-end multiples = no own band"},
    "ev_rule": {
        "value": "EV = price × shares + debt − cash − short-term investments + minorities, "
                 "for the stock AND its peers; equity per share = EBITDA per share × "
                 "multiple − (debt − cash − short-term investments) ÷ shares − minorities "
                 "÷ shares",
        "source": "owner choice 2026-10-21 (VAL-12a); 'Other Short Term Investments' counts "
                  "as cash; a line the feed leaves out counts 0"},
    "target_starts_from": {
        "value": "normalised PAT (stage 11) on every route when it is a number, else "
                 "reported PAT, labelled",
        "source": "owner choice 2026-10-21 (VAL-08), banks included"},
    "own_history_basis": {
        "value": "FY-end close ÷ (normalised PAT ÷ that FY's own period-end share count); "
                 "basic EPS when either is missing, labelled",
        "source": "owner choice 2026-10-21 (VAL-14a)"},
    "eps_spike_ratio": {
        "value": 1.5,
        "source": "owner choice 2026-10-21 (VAL-14a): an INTERIOR FY whose EPS is above "
                  "1.5× or below 1/1.5× BOTH neighbours is flagged and shown, never dropped "
                  "(end years are not judged) — ASSUMED"},
    "lender_book": {
        "value": "book = the bank's FILED net worth when the feed's equity differs from it "
                 "by more than 10%, else the feed's tangible book (same rule each year); "
                 "ROE = PAT ÷ average(book this FY, book last FY), year-end alone when "
                 "only one year is on file; BVPS = book ÷ shares; peer P/B on the same book",
        "source": "owner choice 2026-10-21 (VAL-02) — bank and NBFC routes only"},
    "lender_filing_tolerance_pct": {
        "value": 10.0, "source": "owner choice 2026-10-21 (VAL-02)"},
    "quality_warning_below": {
        "value": 50.0,
        "source": "ASSUMED here 2026-10-21 (VAL-08): a quality score below this prints a "
                  "warning beside the verdict — never a gate"},
    "segment_default_peers": {
        "value": {"O2C": ["IOC", "BPCL", "HINDPETRO"], "Oil & Gas": ["ONGC", "OIL"],
                  "Retail": ["DMART", "TRENT"], "Digital": ["BHARTIARTL"],
                  "Others": ["IOC", "BPCL", "HINDPETRO"]},
        "source": "owner choice 2026-10-21 (VAL-01): default segment multiple = median "
                  "EV/EBIT of these peers (HPCL trades as HINDPETRO); a typed segment "
                  "multiple, then a typed peer list, replaces it"},
}

#: Peer base layout version (VAL-12a / VAL-02 / VAL-01 added fields). The
#: Den's stored peer base is rebuilt when this or the code hash moves.
PEER_BASE_VERSION = 4          # 4 = + pat, sector, family, FY history (batch 3)

KEY_TERM_KIND: Dict[str, str] = {
    # how the Den prints each term's value: pct / x / inr / num
    "Revenue growth": "pct", "EBITDA / margin": "pct", "PAT margin / conversion": "pct",
    "Effective tax rate": "pct", "Normalised PAT": "inr_cr", "Basic EPS": "inr",
    "Diluted EPS": "inr", "Book value per share (BVPS)": "inr", "ROE": "pct",
    "ROA": "pct", "ROCE": "pct", "ROIC": "pct", "ROI (of a trade)": "pct",
    "Cost of equity (CAPM)": "pct", "WACC": "pct", "NPV": "inr_cr", "IRR": "pct",
    "DCF value": "inr", "FCF / FCF yield": "pct", "Cash conversion": "x",
    "Net debt / EBITDA": "x", "Debt / equity": "x", "Interest coverage": "x",
    "Working-capital days": "num", "Receivable days": "num", "Asset turnover": "x",
    "P/E (trailing, forward)": "x", "PEG": "x", "Earnings yield": "pct", "P/B": "x",
    "P/S, EV/Sales": "x", "EV": "inr_cr", "EV/EBITDA": "x",
    "Dividend yield / payout": "pct", "Dividend discount model": "inr",
    "Graham number": "inr", "Altman Z": "num", "Piotroski F": "num", "Beta": "num",
    "Sharpe-like momentum": "num", "Sector relative strength": "pct",
    "Institutional change (pp)": "num", "Delivery %": "pct",
    "Promoter holding / pledge": "pct", "Impact cost / capacity": "num",
    "Kelly fraction (measured)": "pct",
}


# ------------------------------------------------------------ small helpers

def _f(snap: dict, name: str) -> Optional[float]:
    return num((snap.get("fields") or {}).get(name))


def _hist(snap: dict, name: str) -> Dict[str, float]:
    h = (snap.get("history") or {}).get(name)
    return {k: float(v) for k, v in h.items()} if isinstance(h, dict) and not is_dn(h) else {}


def _cr(v: Optional[float]) -> Optional[float]:
    return None if v is None else v / CR


def _why(v: Any, default: str = "missing") -> str:
    return v.get("reason", default) if is_dn(v) else default


def statement_coverage() -> int:
    """How many names the committed statements zip covers (cached)."""
    try:
        from trading import register_v4 as rv
        import zipfile
        with zipfile.ZipFile(rv._STMT_ZIP) as z:
            return len({n.split("/")[1].rsplit("_", 1)[0]
                        for n in z.namelist() if n.endswith("_is.csv")})
    except Exception:                                   # noqa: BLE001
        return 0


def statement_symbols() -> List[str]:
    try:
        from trading import register_v4 as rv
        import zipfile
        with zipfile.ZipFile(rv._STMT_ZIP) as z:
            return sorted({n.split("/")[1].rsplit("_", 1)[0]
                           for n in z.namelist() if n.endswith("_is.csv")})
    except Exception:                                   # noqa: BLE001
        return []


def assumed_for_page() -> dict:
    """The handful of ASSUMED numbers the Den header prints."""
    cd = SC.CASE_DELTAS
    return {
        "publication_lag_days": int(S.ASSUMED["publication_lag_days"]),
        "case_growth_pp": abs(float(cd["upside"]["revenue_growth_pct"])),
        "case_margin_pp": abs(float(cd["upside"]["base_ebitda_margin_pct"])),
        "case_pe_pct": abs(float(cd["upside"]["pe_bridge_extra_pct"])),
        "attractive_discount_pct": float(A.ASSUMED["attractive_margin_pct"]["value"]),
        "usd_signature_pe": float(S.ASSUMED["usd_signature_pe"]),
    }


# ------------------------------------------------------------ the record

def failed_record(ticker: str, why: str) -> dict:
    """A template-safe record for a name whose chain crashed."""
    d = dn(why)
    return {
        "ticker": ticker, "version": VERSION, "price": None, "price_date": "",
        "asof": None, "drafted": False,
        "route": {"status": "UNDECLARED", "label": "", "label_source": "", "method": None,
                  "family": None, "sector": "", "basis": "", "per_share_metric": "",
                  "rule": why, "overlays": [], "sector_method": None,
                  "sector_family": None},
        "snapshot": {"status": "DATA_NEEDED", "why": why, "period_end": None,
                     "publication_date": None, "publication_assumed": None,
                     "currency": "", "converted": False, "fx": "", "basis": "",
                     "source": "", "lock_hash": "", "missing": 0},
        "shares": {"status": "DATA_NEEDED", "basic_now": None, "dilutive_pct": None,
                   "note": why},
        "earnings": {"status": "DATA_NEEDED", "why": why, "reported_pat": None,
                     "normalised_pat": None, "reported_eps": None, "normalised_eps": None,
                     "n_adjustments": 0, "matches_yahoo": None, "target_starts_from": ""},
        "quality": {"score": None, "why": why, "flags": [], "cash_conversion": None,
                    "net_debt_ebitda": None, "interest_cover": None},
        "scenarios": {"status": "DATA_NEEDED", "why": why, "eps": {}, "drivers": {},
                      "pe_bridge_pct": None, "macro_data_needed": []},
        "range": {"status": "DATA_NEEDED", "why": why, "low": None, "base": None,
                  "high": None, "method_text": "", "multiple_band": None,
                  "inverted": False, "inverted_note": "", "whatif": None,
                  "route_family": None, "single_route": None, "own_history": [],
                  "own_history_source": "computed"},
        "verdict": {"verdict": "insufficient", "upside_to_base_pct": None,
                    "reasons": [why], "warnings": []},
        "terms_rows": [], "data_needed": [why],
        "assumptions": {"drafted": [], "drafted_effective": [], "drafted_no_effect": [],
                        "drafted_values": {}, "live_inputs": {}, "no_effect_keys": []},
        "owner_inputs": None, "currency_base": None,
        "error": why, "range_flags": _range_flags("", {}, {}, {}, {}),
        "detail": {"error": d},
    }


# ------------------------------------------------------------ range facts

#: Route methods valued with the lender (bank) formula.
LENDER_METHODS = ("bank_pb_roe", "nbfc_pb")
#: The power / utilities route (VAL-12b reads it on the book family).
POWER_METHODS = ("power_regulated",)
#: How peer_rows_from_base builds a peer's EV — the SAME rule as the
#: stock's own equity bridge (VAL-12a, owner choice 2026-10-21).
PEER_EV_RULE = ("peer EV = price × shares + total debt − cash − short-term "
                "investments + minorities (the same rule as the stock)")


def _sti(snap: dict, period: Optional[str] = None) -> Optional[float]:
    """Short-term investments (Yahoo 'Other Short Term Investments', rupees)
    at `period` (default the snapshot's period end). The ONE definition the
    stock's net cash, its EV bridge, the peer EV and the quality check use
    (VAL-12a); None when the feed leaves the line out (counted 0)."""
    return N._extra(snap, "short_term_investments", period)


# ------------------------------------------------- own P/E history (VAL-14a)

def own_pe_rows(snap: dict, price_at: Callable[[Any], Optional[float]],
                ne: Optional[dict] = None) -> List[dict]:
    """The stock's own FY-end P/E readings, oldest first (owner choice
    2026-10-21, VAL-14a): FY-end close ÷ (normalised PAT ÷ THAT year's own
    period-end share count); basic EPS when either is missing (labelled).
    An interior year whose EPS is above eps_spike_ratio × or below
    1/eps_spike_ratio × BOTH neighbours is FLAGGED (shown, never dropped —
    the band's trim removes extremes); end years are never judged.
    Rows {period, eps, eps_basis, shares, price, pe, flag}. Pure."""
    ne = ne if isinstance(ne, dict) else N.normalised_earnings(snap)
    norm = ne.get("history") if isinstance(ne.get("history"), dict) else {}
    shares_h = _hist(snap, "shares_outstanding")
    eps_h = _hist(snap, "basic_eps")
    rows: List[dict] = []
    for p in sorted(set(norm) | set(eps_h)):
        n, sh = num(norm.get(p)), shares_h.get(p)
        if n is not None and sh:
            eps, basis = n / sh, "normalised PAT ÷ that FY's period-end shares"
        elif eps_h.get(p) is not None:
            eps = eps_h[p]
            basis = ("basic EPS — " + ("that FY's share count missing" if n is not None
                                       else "normalised PAT missing for that FY"))
        else:
            continue
        try:
            px = price_at(p)
        except Exception:                                 # noqa: BLE001
            px = None
        px = num(px)
        pe = px / eps if (px and eps and eps > 0) else None
        rows.append({"period": p, "eps": eps, "eps_basis": basis, "shares": sh,
                     "price": px, "pe": pe, "flag": None})
    k = float(ASSUMED["eps_spike_ratio"]["value"])
    for i in range(1, len(rows) - 1):
        e, a, b = rows[i]["eps"], rows[i - 1]["eps"], rows[i + 1]["eps"]
        if not (e > 0 and a > 0 and b > 0):
            continue
        if e > k * a and e > k * b:
            rows[i]["flag"] = (f"odd year: EPS {e:,.2f} is above {k:g}× both neighbours "
                               f"({a:,.2f}, {b:,.2f}) — shown, not dropped")
        elif e < a / k and e < b / k:
            rows[i]["flag"] = (f"odd year: EPS {e:,.2f} is below 1/{k:g} of both neighbours "
                               f"({a:,.2f}, {b:,.2f}) — shown, not dropped")
    return rows


# ------------------------------------------------- outside data (builder D)

def _filing_net_worth(sym: str, period: Optional[str]) -> Optional[tuple]:
    """(net worth ₹ crore, source) the lender FILED for `period`, from
    data/lender_networth_filings.csv via trading.valuation.filings; None
    when the module or the row is missing."""
    if not period:
        return None
    try:
        from trading.valuation import filings as _FL
        got = _FL.net_worth(sym, str(period)[:10])
    except Exception:                                     # noqa: BLE001
        return None
    if not got:
        return None
    try:
        cr, src = float(got[0]), str(got[1] if len(got) > 1 else "filing")
    except (TypeError, ValueError, IndexError):
        return None
    return (cr, src) if cr > 0 else None


def _lender_book(sym: str, snap: dict, period: Optional[str]) -> tuple:
    """(book ₹ crore or None, basis text) for a lender at one FY end
    (VAL-02, owner choice 2026-10-21): the FILED net worth when the feed's
    equity differs from it by more than 10%, else the feed's tangible book;
    a missing filing → tangible book, labelled; no tangible line → the
    feed's equity, labelled."""
    if not period:
        return None, "no period"
    feed = _hist(snap, "equity").get(period)
    if feed is None and period == snap.get("period_end"):
        feed = _f(snap, "equity")
    tb = N._extra(snap, "tangible_book", period)
    filing = _filing_net_worth(sym, period)
    tol = float(ASSUMED["lender_filing_tolerance_pct"]["value"])
    if filing is not None:
        f_cr, f_src = filing
        if feed is None:
            return f_cr, f"filed net worth ₹{f_cr:,.0f} cr ({f_src}); no feed equity"
        gap = abs(feed / CR - f_cr) / f_cr * 100
        if gap > tol:
            return f_cr, (f"filed net worth ₹{f_cr:,.0f} cr ({f_src}) — the feed's equity "
                          f"₹{feed / CR:,.0f} cr is {gap:.0f}% off it")
        if tb is not None and tb > 0:
            return tb / CR, (f"feed tangible book (the feed's equity is within {tol:g}% of "
                             f"the filing, {gap:.1f}%)")
        # reviewer defect 9 (2026-10-21): no tangible line, but the equity
        # WAS checked against the filing — say so (it is not unchecked)
        return feed / CR, (f"feed equity checked against the filing (within {tol:g}% of "
                           f"the filed net worth ₹{f_cr:,.0f} cr, {gap:.1f}% off; {f_src}) "
                           "— no tangible-book line")
    if tb is not None and tb > 0:
        return tb / CR, ("feed tangible book (Yahoo 'Tangible Book Value')" +
                         ("" if filing is not None else " — no filing on file"))
    if feed is not None:
        return feed / CR, "feed equity — no tangible-book line, no filing on file"
    return None, "no book on file"


def _segment_rows(sym: str) -> List[dict]:
    """Segment EBIT rows {segment, ebit_cr, fy_end, source} of a
    conglomerate (data/segment_ebit.csv via trading.valuation.segments);
    [] when the module or the rows are missing."""
    try:
        from trading.valuation import segments as _SG
        rows = _SG.segment_ebit(sym) or []
    except Exception:                                     # noqa: BLE001
        return []
    return [dict(r) for r in rows if isinstance(r, dict)]


def _holdco_rows(sym: str) -> List[dict]:
    """Listed-stake rows of a holding company (data/holdco_stakes.csv via
    trading.valuation.segments); [] when missing."""
    try:
        from trading.valuation import segments as _SG
        rows = _SG.holdco_stakes(sym) or []
    except Exception:                                     # noqa: BLE001
        return []
    return [dict(r) for r in rows if isinstance(r, dict)]


def _bare(t: Any) -> str:
    t = str(t or "").strip().upper()
    return t[:-3] if t.endswith((".NS", ".BO")) else t


def _peer_index(peer_rows: Any) -> Dict[str, dict]:
    """{SYMBOL: priced peer row} from what the caller injected: the priced
    table {method: [rows]} (peer_rows_from_base), a flat {ticker: row} or a
    list of rows."""
    try:
        tbl = peer_rows() if callable(peer_rows) else peer_rows
    except Exception:                                     # noqa: BLE001
        return {}
    out: Dict[str, dict] = {}
    if isinstance(tbl, dict):
        for k, v in tbl.items():
            if isinstance(v, list):
                for r in v:
                    if isinstance(r, dict) and r.get("ticker"):
                        out[_bare(r["ticker"])] = dict(r, method=r.get("method") or k)
            elif isinstance(v, dict):
                out[_bare(v.get("ticker") or k)] = dict(v)
    elif isinstance(tbl, list):
        for r in tbl:
            if isinstance(r, dict) and r.get("ticker"):
                out[_bare(r["ticker"])] = dict(r)
    return out


def _median_of(index: Dict[str, dict], tickers: List[str], key: str,
               exclude: str = "") -> tuple:
    """(median of `key` over the named tickers, [tickers used])."""
    used, xs = [], []
    for t in tickers:
        b = _bare(t)
        if not b or b == _bare(exclude):
            continue
        v = num((index.get(b) or {}).get(key))
        if v is not None and v > 0:
            xs.append(v)
            used.append(b)
    if not xs:
        return None, []
    xs.sort()
    n = len(xs)
    m = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0
    return m, used


def _segment_key(name: str) -> str:
    """A segment label → the default-peer group it belongs to."""
    s = str(name or "").upper().replace("&", " AND ")
    if "O2C" in s or "OIL TO CHEMICAL" in s or "REFIN" in s or "PETROCHEM" in s:
        return "O2C"
    if "OIL" in s and "GAS" in s or "EXPLORATION" in s or "UPSTREAM" in s:
        return "Oil & Gas"
    if "RETAIL" in s:
        return "Retail"
    if "DIGITAL" in s or "JIO" in s or "TELECOM" in s:
        return "Digital"
    return "Others"


def _case_values(family: str, rng: dict) -> Dict[str, Optional[float]]:
    """The three case targets as justified_range made them, BEFORE its
    low ≤ base ≤ high sort."""
    if family == "earnings":
        eps = (rng.get("bridge") or {}).get("eps") or {}
        band = rng.get("multiple_band")
        if isinstance(band, dict) and not is_dn(band):
            edge = {"downside": "low", "base": "mid", "upside": "high"}
            out: Dict[str, Optional[float]] = {}
            for c in SC.CASES:
                e, b = num(eps.get(c)), num(band.get(edge[c]))
                out[c] = SC.xround(e * b, 2) if (e is not None and b is not None) else None
            return out
    ps = rng.get("per_share") if isinstance(rng.get("per_share"), dict) else {}
    return {c: num(ps.get(c)) for c in SC.CASES}


def _range_flags(family: str, rt: dict, rng: dict, kw: dict, snap: dict,
                 facts: Optional[dict] = None) -> dict:
    """Plain facts the Den's known-fault lines read (VAL-01/02/07/12),
    so no rule has to parse note text. Facts only — the rules live in
    the Den. `facts` = what the pipeline decided for this row (lender
    book basis, typed peer list, sum-of-parts variant)."""
    rt = rt or {}
    rng = rng if isinstance(rng, dict) else {}
    kw = kw or {}
    snap = snap or {}
    facts = facts or {}
    vals = _case_values(family, rng)
    inverted = False
    printed: Dict[str, Optional[str]] = {"low": None, "base": None, "high": None}
    if all(vals[c] is not None for c in SC.CASES):
        inverted = not (vals["downside"] <= vals["base"] <= vals["upside"])
        if "inverted" in rng:
            # stage 17 never sorts (VAL-07, owner 2026-10-21): low IS the
            # downside case, high the upside case
            printed = {"low": "downside", "base": "base", "high": "upside"}
        else:
            order = sorted(SC.CASES, key=lambda c: vals[c])
            printed = {"low": order[0], "base": order[1], "high": order[2]}
    if "inverted" in rng:
        inverted = bool(rng.get("inverted")) or inverted
    method = rt.get("method")
    shares = _f(snap, "shares_outstanding")
    sti = _sti(snap)
    is_ev = family in ("ev_ebitda", "ev_sales")
    midcycle = bool(is_ev and (kw.get("per_share_overrides") or {}))
    return {
        "family": family or None, "method": method, "sector": rt.get("sector") or "",
        "case_values": vals, "inverted": inverted,
        "inverted_note": str(rng.get("inverted_note") or "") if inverted else "",
        "printed_case": printed, "printed_base_case": printed["base"],
        # kept name; since 2026-10-21 = the metals mid-cycle MARGIN on all three cases
        "midcycle_base": midcycle, "midcycle_margin": midcycle,
        "net_debt_per_share": num(kw.get("net_debt_per_share")) if is_ev else None,
        "minorities_per_share": num(kw.get("minorities_per_share")) if is_ev else None,
        "short_term_investments_per_share": (sti / shares if (is_ev and sti is not None
                                                              and shares) else None),
        "peer_ev_rule": PEER_EV_RULE if is_ev else "",
        "ev_rule_unified": bool(is_ev),
        "lender": method in LENDER_METHODS,
        "power_utility": method in POWER_METHODS,
        "book_route": family in ("book", "nav"),
        "lender_book_source": facts.get("lender_book_source"),
        "lender_book_basis": facts.get("lender_book_basis") or "",
        "peer_list": list(facts.get("peer_list") or []) or None,
        "sum_of_parts": facts.get("sum_of_parts"),
        "target_starts_from": facts.get("target_starts_from") or "",
    }


def _route_block(rt: dict) -> dict:
    overlays = []
    for o in rt.get("overlays") or []:
        if isinstance(o, dict):
            overlays.append(f"{o.get('sector') or o.get('method')} — "
                            f"{o.get('source') or o.get('rule') or 'overlay'}")
        else:
            overlays.append(str(o))
    return {"status": rt.get("status") or "UNDECLARED",
            "label": rt.get("label") or "", "label_source": rt.get("label_source") or "",
            "method": rt.get("method"), "family": rt.get("family"),
            "sector": rt.get("sector") or "", "basis": rt.get("basis") or "",
            "per_share_metric": rt.get("per_share_metric") or "",
            "rule": rt.get("rule") or "", "overlays": overlays,
            "nearest": rt.get("nearest"),
            # a conglomerate's sector route, kept beside its sum-of-parts
            "sector_method": rt.get("sector_method"),
            "sector_family": rt.get("sector_family")}


def _snapshot_block(snap: dict) -> dict:
    cur = snap.get("currency")
    if snap.get("source") == "none" or not snap.get("period_end"):
        why = _why(cur, "no statements")
        return {"status": "DATA_NEEDED", "why": why, "period_end": None,
                "publication_date": None, "publication_assumed": None,
                "currency": "", "converted": False, "fx": "", "basis": "",
                "source": snap.get("source") or "none",
                "lock_hash": snap.get("lock_hash") or "", "missing": 0}
    converted = isinstance(cur, str) and cur.startswith("USD")
    fx = ""
    if converted:
        p = snap.get("period_end")
        f = (snap.get("fx") or {}).get(p) or {}
        if f:
            fx = f"{f.get('rate'):.2f} ({f.get('fx_date')})"
    dn_fields = {k: v for k, v in (snap.get("data_needed") or {}).items()
                 if k not in ("basis",)}
    cur_text = cur if isinstance(cur, str) else f"DATA_NEEDED — {_why(cur)}"
    return {"status": "OK", "why": "", "period_end": snap.get("period_end"),
            "publication_date": snap.get("publication_date"),
            "publication_assumed": bool(snap.get("publication_assumed")),
            "currency": cur_text, "converted": converted, "fx": fx,
            "basis": snap.get("basis") or "unknown", "source": snap.get("source") or "",
            "lock_hash": snap.get("lock_hash") or "", "missing": len(dn_fields),
            "currency_check": snap.get("currency_check") or {}}


def _shares_block(b: dict) -> dict:
    st = b.get("status")
    status = st if isinstance(st, str) else "DATA_NEEDED"
    if status == "DATA_NEEDED" and is_dn(st):
        note = st.get("reason", "")
    else:
        note = b.get("note") or ""
    flags = b.get("flags") or []
    if flags and status != "DATA_NEEDED":
        note = (note + " · " if note else "") + "; ".join(str(x) for x in flags[:2])
    return {"status": status, "basic_now": num(b.get("basic_now")),
            "dilutive_pct": num(b.get("dilutive_pct")), "note": note[:200]}


def _earnings_block(ne: dict, snap: dict) -> dict:
    norm = ne.get("normalised_pat")
    rep = ne.get("reported_pat")
    if num(norm) is None or num(rep) is None:
        why = _why(norm if is_dn(norm) else rep, "normalised PAT missing")
        return {"status": "DATA_NEEDED", "why": why, "reported_pat": num(rep),
                "normalised_pat": None, "reported_eps": _f(snap, "basic_eps"),
                "normalised_eps": None, "n_adjustments": len(ne.get("adjustments") or []),
                "matches_yahoo": None, "adjustments": ne.get("adjustments") or []}
    my = ne.get("matches_yahoo")
    return {"status": "OK", "why": "", "reported_pat": float(rep),
            "normalised_pat": float(norm),
            "reported_eps": _f(snap, "basic_eps"),
            "normalised_eps": num(ne.get("normalised_eps")),
            "n_adjustments": len([a for a in (ne.get("adjustments") or []) if a.get("applied")]),
            "matches_yahoo": my if isinstance(my, bool) else None,
            "adjustments": ne.get("adjustments") or [],
            "eps_shares_basis": ne.get("eps_shares_basis")}


def _quality_block(q: dict) -> dict:
    score = q.get("score")
    return {"score": num(score), "why": _why(score, "") if num(score) is None else "",
            "flags": [str(x) for x in (q.get("flags") or [])],
            "cash_conversion": num(q.get("cash_conversion")),
            "net_debt_ebitda": num(q.get("net_debt_ebitda")),
            "interest_cover": num(q.get("interest_cover")),
            "debt_equity": num(q.get("debt_equity")),
            "fcf_margin": num(q.get("fcf_margin"))}


# ------------------------------------------------------------ scenarios

def target_pat(ne: Optional[dict], snap: dict) -> tuple[Optional[float], str]:
    """(PAT in rupees, basis) the target starts from (VAL-08, owner choice
    2026-10-21): normalised PAT on every route when it is a number, else
    reported PAT, labelled."""
    n = num((ne or {}).get("normalised_pat")) if isinstance(ne, dict) else None
    if n is not None:
        return n, "normalised"
    return _f(snap, "net_income_common"), "reported (normalised DATA_NEEDED)"


def _base_year(snap: dict, price_at: Callable[[Any], Optional[float]],
               ne: Optional[dict] = None) -> tuple[dict, List[str]]:
    """The sheet's column D from the snapshot, in ₹ crore / crore shares.
    PAT = normalised PAT when `ne` carries it, else reported (VAL-08)."""
    notes: List[str] = []
    rev = _f(snap, "revenue")
    ebitda = _f(snap, "ebitda")
    if ebitda is None:
        oi, da = _f(snap, "operating_income"), _f(snap, "d_and_a")
        if oi is not None and da is not None:
            ebitda = oi + da
            notes.append("EBITDA = operating income + D&A (no EBITDA line)")
    pat, pat_basis = target_pat(ne, snap)
    if pat_basis != "normalised":
        notes.append("target starts from REPORTED PAT — normalised PAT is DATA_NEEDED")
    tax = _f(snap, "tax")
    emp = _f(snap, "employee_cost")
    if emp is None:
        emp = 0.0
        notes.append("employee cost line not in statements — the wage/attrition "
                     "driver is disabled (counted 0)")
    shares = _f(snap, "shares_outstanding")
    cash = _f(snap, "cash")
    sti = _sti(snap)
    debt = _f(snap, "total_debt")
    equity = _f(snap, "equity")
    div_paid = _f(snap, "dividends_paid")
    dps = (abs(div_paid) / shares) if (div_paid is not None and shares) else 0.0
    if div_paid is None:
        notes.append("dividends line missing — dividend per share counted 0")
    px_fy = price_at(snap.get("period_end")) if snap.get("period_end") else None
    net_cash = None
    if cash is not None and debt is not None:
        net_cash = cash + (sti or 0.0) - debt
    prior = _hist(snap, "revenue")
    periods = sorted(prior)
    prior_rev = None
    if len(periods) >= 2 and snap.get("period_end") in prior:
        i = periods.index(snap["period_end"])
        if i >= 1:
            prior_rev = prior[periods[i - 1]]
    base = {
        "revenue": _cr(rev), "ebitda": _cr(ebitda), "pat": _cr(pat), "tax": _cr(tax),
        "employee_cost": _cr(emp), "shares": _cr(shares), "price_fy_end": px_fy,
        "net_cash": _cr(net_cash), "borrowings": _cr(debt), "equity": _cr(equity),
        "dividend_ps": dps, "prior_revenue": _cr(prior_rev),
    }
    return base, notes


def _drivers(base: dict, snap: dict, route_family: str) -> tuple[dict, List[str]]:
    notes: List[str] = []
    d = {k: 0.0 for k in SC.SHEET_DRIVERS}
    rev, prior = base.get("revenue"), base.get("prior_revenue")
    if rev and prior:
        g = (rev / prior - 1) * 100
        # A converted (ADR) name's rupee growth carries the rupee's own
        # move; the sheet's growth driver is the business's growth, so read
        # it in constant currency when both periods' rates are on record.
        fx = snap.get("fx") or {}
        periods = sorted(_hist(snap, "revenue"))
        pe = snap.get("period_end")
        if isinstance(snap.get("currency"), str) and snap["currency"].startswith("USD") \
                and pe in periods and periods.index(pe) >= 1:
            p0 = periods[periods.index(pe) - 1]
            r1, r0 = num((fx.get(pe) or {}).get("rate")), num((fx.get(p0) or {}).get("rate"))
            if r1 and r0:
                g = ((rev / r1) / (prior / r0) - 1) * 100
                notes.append(f"growth driver in constant currency (USD) for a converted "
                             f"name: {g:.1f}% vs {((rev / prior) - 1) * 100:.1f}% in rupees")
        d["revenue_growth_pct"] = SC.xround(g, 1)
    else:
        notes.append("no prior-year revenue — growth driver 0")
    if base.get("revenue") and base.get("ebitda") is not None:
        d["base_ebitda_margin_pct"] = SC.xround(base["ebitda"] / base["revenue"] * 100, 1)
    if base.get("ebitda") and base.get("pat") is not None:
        d["pat_pct_of_ebitda"] = SC.xround(base["pat"] / base["ebitda"] * 100, 1)
    eps0 = (base["pat"] / base["shares"]) if (base.get("pat") is not None and base.get("shares")) else None
    if eps0 and base.get("dividend_ps"):
        d["payout_pct"] = SC.xround(base["dividend_ps"] / eps0 * 100, 1)
    d["net_cash"] = base.get("net_cash") if base.get("net_cash") is not None else 0.0
    d["attrition_change_pp"] = 0.0
    return d, notes


def _market(macro: dict, snap: dict, route: dict, peer_pe: Optional[float],
            base: dict, sheet: Optional[dict] = None,
            e101_it_rupee: bool = False) -> tuple[dict, List[str]]:
    """The sheet's market inputs: stored levels where the app has them,
    neutral values (= zero effect) where it does not, each listed.

    `sheet` = the Den draft of the sheet's sensitivities. A missing CPI,
    GDP or delivery figure is filled with the SAME comfort / trend /
    benchmark its bridge row compares it with (draft or default), so the
    row really counts 0 under any draft (VAL-10, owner order 2026-10-21).

    `e101_it_rupee` (batch-3 review, 2026-10-21): True = the rupee row
    E101 reads USD/INR now vs the base year-end for IT-services routes —
    the batch-2 rule the item-19 pre-registered study measured, used for
    the exit RECORD only. False (default) = batch 3: E101 counted 0, the
    currency leg runs through EPS after the chain."""
    missing: List[str] = []
    av = SC._assumption_values(sheet or None)
    m: Dict[str, Any] = {}
    for a, b in (("sensex_start", "sensex_end"), ("nifty_start", "nifty_end")):
        s, e = num(macro.get(a)), num(macro.get(b))
        if s is None or e is None:
            s = e = 100.0
            missing.append(f"{a.split('_')[0]} level since the period end — counted 0")
        m[a], m[b] = s, e
    # repo: the rate at the base period end vs now (item 10, owner 2026-10-21 —
    # the two were set to the same number before, so the row always read 0)
    r0, r1 = num(macro.get("repo_start_pct")), num(macro.get("repo_end_pct"))
    if r1 is None:
        r1 = num(macro.get("repo_pct"))                  # older callers: today's rate
    if r0 is None or r1 is None:
        m["repo_start"] = m["repo_end"] = r1 if r1 is not None else (r0 or 0.0)
        why = str(macro.get("repo_start_why") or macro.get("repo_why") or "").strip()
        if r1 is None and r0 is None:
            missing.append("RBI repo rate — no rate on file; counted 0"
                           + (f" ({why})" if why else ""))
        else:
            side = "at the base period end" if r0 is None else "now"
            missing.append(f"RBI repo rate {side} — DATA_NEEDED"
                           + (f" ({why})" if why else "") + "; counted 0")
    else:
        m["repo_start"], m["repo_end"] = r0, r1
    m["cpi"] = num(macro.get("cpi_pct")) if num(macro.get("cpi_pct")) is not None else av["inflation_comfort_pct"]
    if num(macro.get("cpi_pct")) is None:
        missing.append("CPI — no feed wired (MOSPI / FRED); counted 0")
    m["gdp"] = num(macro.get("gdp_pct")) if num(macro.get("gdp_pct")) is not None else av["gdp_trend_pct"]
    if num(macro.get("gdp_pct")) is None:
        missing.append("GDP growth — no feed wired; counted 0")
    b0, b1 = num(macro.get("brent_start")), num(macro.get("brent_end"))
    if b0 is None or b1 is None:
        b0 = b1 = 100.0
        missing.append("Brent — no feed wired; counted 0")
    m["brent_start"], m["brent_end"] = b0, b1
    # rupee row E101: counted 0 in the live chain (owner choice 2026-10-21,
    # VALFIX batch 3) — the currency leg now runs through EPS for every
    # sector, after the chain (currency.py); the sheet keeps its own E101
    u_now, u_base = num(macro.get("usdinr_now")), num(macro.get("usdinr_base"))
    if e101_it_rupee:
        # the item-19 exit record (batch-3 review, 2026-10-21): the batch-2
        # branch VERBATIM — the rule the pre-registered study measured.
        # rupee leg: IT exporters only (ASSUMED)
        is_it = (route.get("sector") or "").startswith("IT services")
        if is_it and u_now and u_base:
            m["usdinr"], m["usdinr_base"] = u_now, u_base
        else:
            m["usdinr"] = m["usdinr_base"] = u_base or u_now or 1.0
            if is_it:
                missing.append("USDINR now/base — rupee leg counted 0")
    else:
        m["usdinr"] = m["usdinr_base"] = u_base or u_now or 1.0
    m["vix"] = num(macro.get("vix")) if num(macro.get("vix")) is not None else 0.0
    m["sentiment"] = 0.0
    i0, i1 = num(macro.get("inst_holding_start")), num(macro.get("inst_holding_end"))
    if i0 is None or i1 is None:
        i0 = i1 = 0.0
        why = str(macro.get("inst_holding_why") or "").strip()
        missing.append("institutional holding change — no FII + DII quarter on file for "
                       "both the base period end and now; counted 0"
                       + (f" ({why})" if why else ""))
    m["inst_holding_start"], m["inst_holding_end"] = i0, i1
    m["volume_ratio"] = 1.0
    if num(macro.get("delivery_pct")) is not None:
        m["delivery_pct"] = num(macro.get("delivery_pct"))
    else:
        m["delivery_pct"] = av["delivery_benchmark_pct"]
        missing.append("delivery % — frozen 2026-08-07 (laid off); counted 0")
    m["chart_score"] = 0.0
    m["event_pct"] = 0.0
    m["gap_close_pct"] = 0.0
    eps0 = (base["pat"] / base["shares"]) if (base.get("pat") is not None and base.get("shares")) else None
    own_pe = (base["price_fy_end"] / eps0) if (eps0 and base.get("price_fy_end")) else None
    if peer_pe and peer_pe > 0:
        m["peer_pe"] = peer_pe
    else:
        m["peer_pe"] = own_pe if own_pe and own_pe > 0 else 1.0
        missing.append("peer P/E — no same-route peers; premium-to-peer row reads 0")
    m["latest_price"] = num(macro.get("price_now"))
    return m, missing


def _scenario_block(cases: dict, drivers: dict, macro_missing: List[str],
                    notes: List[str]) -> dict:
    base = cases.get("base") or {}
    if base.get("status") != "OK" and num(base.get("eps")) is None:
        why = "; ".join(base.get("data_needed") or []) or "base case could not be computed"
        return {"status": "DATA_NEEDED", "why": why[:200], "eps": {}, "drivers": {},
                "pe_bridge_pct": None, "macro_data_needed": macro_missing, "notes": notes}
    eps = {c: num((cases.get(c) or {}).get("eps")) for c in SC.CASES}
    prices = {c: num((cases.get(c) or {}).get("model_price")) for c in SC.CASES}
    return {"status": "OK", "why": "", "eps": eps, "model_price": prices,
            "closing_pe": {c: num((cases.get(c) or {}).get("closing_pe")) for c in SC.CASES},
            "drivers": {"revenue_growth_pct": drivers.get("revenue_growth_pct"),
                        "base_margin_pct": drivers.get("base_ebitda_margin_pct"),
                        "pat_pct_of_ebitda": drivers.get("pat_pct_of_ebitda"),
                        "payout_pct": drivers.get("payout_pct")},
            "pe_bridge_pct": num(base.get("pe_total_impact_pct")) or 0.0,
            "pe_bridge": base.get("pe_bridge"),
            "macro_data_needed": macro_missing, "notes": notes,
            "case_deltas": {c: (cases.get(c) or {}).get("case_deltas") for c in SC.CASES}}


#: The multiple a typed peer list replaces, per route family (VAL-01).
_PEER_KEY = {"earnings": "pe", "book": "pb", "nav": "pb", "ev_ebitda": "ev_ebitda",
             "ev_sales": "ev_sales"}


def _midcycle_margin_overrides(snap: dict, cases: dict) -> tuple[Optional[dict], str]:
    """VAL-07 (owner choice 2026-10-21), metals row only: each case's EBITDA
    = E19 − E17 × (base-case margin E21 − mean EBITDA margin of the stored
    FYs) ÷ 100, ÷ shares E28. ({case: EBITDA per share}, note) or (None, why)."""
    he, hr = _hist(snap, "ebitda"), _hist(snap, "revenue")
    ms = [he[k] / hr[k] * 100 for k in he if hr.get(k)]
    if len(ms) < 3:
        return None, (f"mid-cycle margin needs 3+ FYs of EBITDA and revenue "
                      f"({len(ms)} on file) — the cases' own EBITDA used")
    cells = {c: ((cases.get(c) or {}).get("cells") or {}) for c in SC.CASES}
    m0 = num(cells["base"].get("E21"))
    if m0 is None:
        return None, "the base case carries no margin (E21) — the cases' own EBITDA used"
    mm = sum(ms) / len(ms)
    out: Dict[str, float] = {}
    for c in SC.CASES:
        e19, e17, e28 = (num(cells[c].get(k)) for k in ("E19", "E17", "E28"))
        if e19 is None or e17 is None or not e28:
            return None, f"{c} case lacks EBITDA / revenue / shares — the cases' own EBITDA used"
        out[c] = (e19 - e17 * (m0 - mm) / 100.0) / e28
    return out, (f"metals mid-cycle MARGIN {mm:.2f}% (mean of {len(ms)} FYs) in place of the "
                 f"base-year {m0:.1f}% on all three cases (owner choice 2026-10-21)")


def _prior_period(snap: dict, period: Optional[str]) -> Optional[str]:
    """The FY end before `period` that carries an equity or tangible-book line."""
    tb = (snap.get("extra") or {}).get("tangible_book")
    ps = set(_hist(snap, "equity")) | (set(tb) if isinstance(tb, dict) and not is_dn(tb)
                                       else set())
    ps = sorted(p for p in ps if period and p < period)
    return ps[-1] if ps else None


def _lender_inputs(sym: str, snap: dict, base: dict) -> tuple[Optional[float], Optional[float],
                                                              str, dict]:
    """(ROE %, BVPS, note, facts) for a lender on the VAL-02 book."""
    pe = snap.get("period_end")
    book, basis = _lender_book(sym, snap, pe)
    facts = {"lender_book_basis": basis,
             "lender_book_source": ("filing" if basis.startswith("filed") else
                                    "tangible" if basis.startswith("feed tangible") else
                                    "filing_checked" if basis.startswith("feed equity checked")
                                    else "feed_equity" if basis.startswith("feed equity")
                                    else None)}
    if book is None or not base.get("shares") or base.get("pat") is None:
        return None, None, f"lender book: {basis}", facts
    prev_p = _prior_period(snap, pe)
    prev, prev_basis = _lender_book(sym, snap, prev_p) if prev_p else (None, "")
    if prev is not None and prev > 0:
        roe = base["pat"] / ((book + prev) / 2.0) * 100
        note = (f"lender book {pe}: {basis}; {prev_p}: {prev_basis}; ROE = PAT ÷ average "
                f"book = {roe:.2f}% (owner choice 2026-10-21, VAL-02)")
    else:
        roe = base["pat"] / book * 100
        note = (f"lender book {pe}: {basis}; ROE = PAT ÷ YEAR-END book = {roe:.2f}% — only "
                "one year's book on file (VAL-02)")
    facts["lender_roe_pct"] = roe
    return roe, book / base["shares"], note, facts


def _range_inputs(family: str, snap: dict, base: dict, cases: dict, peers: dict,
                  own_pe_hist: List[Any], macro: dict,
                  ov: Optional[dict] = None, *, method: Optional[str] = None,
                  sym: str = "", owner: Optional[dict] = None,
                  peer_index: Optional[Dict[str, dict]] = None,
                  price_of: Optional[Callable[[str], Optional[float]]] = None,
                  segments: Optional[List[dict]] = None) -> dict:
    """The keyword arguments justified_range needs for this family. `ov` =
    the Den draft's range inputs (ERP, long-run g, ROE step, typed-value
    band) by name; `owner` = the symbol's row of the Den inputs table.
    Private keys (popped by evaluate): _midcycle_note, _growth_note,
    _peer_note, _facts."""
    kw: Dict[str, Any] = {}
    ov = ov or {}
    owner = owner if isinstance(owner, dict) else {}
    peer_index = peer_index or {}
    facts: Dict[str, Any] = {}
    shares = _f(snap, "shares_outstanding")
    if family == "earnings":
        kw["peer_multiple"] = peers.get("pe_median")
        usable = [h for h in (own_pe_hist or [])
                  if (num(h.get("pe")) if isinstance(h, dict) else num(h)) is not None]
        kw["own_multiple_history"] = usable if len(usable) >= int(
            ASSUMED["min_history_for_own_band"]["value"]) else None
    elif family in ("book", "nav"):
        lender = method in LENDER_METHODS
        lender_note = ""
        if lender:
            roe, bvps, lender_note, lf = _lender_inputs(sym, snap, base)
            facts.update(lf)
        else:
            roe = (base["pat"] / base["equity"] * 100) if (base.get("pat") is not None
                                                            and base.get("equity")) else None
            bvps = (base["equity"] / base["shares"]) if (base.get("equity") is not None
                                                          and base.get("shares")) else None
        kw["roe_pct"] = roe
        kw["bvps"] = bvps
        kw["rf_pct"] = num(macro.get("rf_pct"))
        kw["beta"] = num(macro.get("beta")) if num(macro.get("beta")) is not None else float(
            SC.SHEET_ASSUMPTIONS["beta"]["value"])
        kw["peer_multiple"] = peers.get("pb_median")
        # (ROE − g)/(COE − g) breaks down when g sits at or above ROE. The
        # long-run g (8%, valuation_context) is a market number; a lender
        # can only grow its book at the earnings it keeps, so g is capped
        # at the sustainable rate ROE × (1 − payout) — ASSUMED, printed.
        g_lr = float(ov.get("long_run_growth_pct", _vc.ASSUMED["long_run_growth_pct"]))
        if num(ov.get("equity_risk_premium_pct")) is not None:
            kw["erp_pct"] = float(ov["equity_risk_premium_pct"])
        if num(ov.get("book_roe_step_pp")) is not None:
            kw["roe_step_pp"] = float(ov["book_roe_step_pp"])
        if roe is not None:
            eps0 = (base["pat"] / base["shares"]) if base.get("shares") else None
            payout = (base.get("dividend_ps") or 0.0) / eps0 if eps0 else 0.0
            payout = min(max(payout, 0.0), 1.0)
            g_sus = roe * (1.0 - payout)
            kw["growth_pct"] = min(g_lr, g_sus)
            kw["_growth_note"] = (f"g = min(long-run {g_lr:g}%, sustainable ROE × (1 − payout) = "
                                  f"{g_sus:.1f}%) (ASSUMED here)")
        if lender_note:
            kw["_growth_note"] = (lender_note + " · " + kw.get("_growth_note", "")).strip(" ·")
        if family == "nav" and num(owner.get("nav_ps")) is None:
            # reviewer defect 3 (2026-10-21): with a typed NAV the range IS
            # the NAV — the fallback note belongs to the untyped case only
            kw["_growth_note"] = ("NAV not held — P/B fallback shown with " +
                                  kw.get("_growth_note", "the book-route inputs"))
    elif family in ("ev_ebitda", "ev_sales"):
        ebitda = _f(snap, "ebitda")
        if ebitda is None:
            oi, da = _f(snap, "operating_income"), _f(snap, "d_and_a")
            ebitda = (oi + da) if (oi is not None and da is not None) else None
        rev = _f(snap, "revenue")
        if shares:
            if family == "ev_ebitda" and ebitda is not None:
                # the three cases carry their own EBITDA (₹ cr) and shares
                # (cr) — justified_range reads EBITDA per share per case from
                # them, which is what gives the range its width; the latest
                # FY's figure is only the fallback when no case exists
                if not cases:
                    kw["ebitda_per_share"] = ebitda / shares
                elif method == "metals_midcycle":
                    ovr, note = _midcycle_margin_overrides(snap, cases)
                    if ovr:
                        kw["per_share_overrides"] = ovr
                    kw["_midcycle_note"] = note
            if family == "ev_sales" and rev is not None:
                kw["ebitda_per_share"] = rev / shares     # the module reads revenue/share here
        kw["peer_multiple"] = peers.get("ev_ebitda_median") if family == "ev_ebitda" else peers.get("ev_sales_median")
    if family in ("ev_ebitda", "ev_sales") or segments:
        # one EV rule (VAL-12a): net debt takes short-term investments off,
        # minorities are added back to the claim on the EV
        if shares:
            debt, cash = _f(snap, "total_debt"), _f(snap, "cash")
            sti = _sti(snap)
            if debt is not None and cash is not None:
                kw["net_debt_per_share"] = (debt - cash - (sti or 0.0)) / shares
            mi = _f(snap, "minority_interest_bs")
            kw["minorities_per_share"] = (mi / shares) if mi is not None else 0.0

    # a typed peer list replaces the route median (VAL-01, owner 2026-10-21)
    plist = [str(x) for x in (owner.get("peer_list") or []) if str(x).strip()]
    pkey = _PEER_KEY.get(family)
    if plist and pkey:
        m, used = _median_of(peer_index, plist, pkey, exclude=sym)
        if m is not None:
            kw["peer_multiple"] = m
            facts["peer_list"] = used
            if family in ("book", "nav"):
                # reviewer defect 4 (2026-10-21): the book route prices on
                # (ROE − g)/(COE − g) × BVPS; peer P/B only sits beside it
                kw["_peer_note"] = (f"peer P/B cross-check only — median {pkey} {m:.2f} of "
                                    f"YOUR peer list ({', '.join(used)}); the range does "
                                    "not use it")
            else:
                kw["_peer_note"] = (f"peer multiple = median {pkey} {m:.2f} of YOUR peer "
                                    f"list ({', '.join(used)}) in place of the route median")
        else:
            kw["_peer_note"] = (f"your peer list ({', '.join(plist)}) has no usable {pkey} in "
                                "the peer table — the route median stays")

    # owner inputs (typed NAV / EV / holding discount / segment multiples)
    if owner:
        kw["owner_inputs"] = dict(owner)
    if num(ov.get("typed_value_band_pct")) is not None:
        kw["typed_value_band_pct"] = float(ov["typed_value_band_pct"])

    # holding company: listed stakes priced like the peers (VAL-14b)
    if method == "holding_sotp":
        stakes = _holdco_rows(sym)
        if stakes:
            kw["holdco_stakes"] = stakes
            kw["subsidiary_prices"] = _subsidiary_prices(stakes, peer_index, price_of)
            kw["shares_outstanding"] = shares
            facts["sum_of_parts"] = "holding_company"

    # conglomerate: segment EBIT × segment EV/EBIT (VAL-01)
    if segments:
        kw["segments"] = segments
        kw["segment_multiples"] = _segment_multiples(segments, peer_index, owner, sym)
        kw["shares_outstanding"] = shares
        facts["sum_of_parts"] = "conglomerate"
    kw["_facts"] = facts
    return kw


def _subsidiary_prices(stakes: List[dict], peer_index: Dict[str, dict],
                       price_of: Optional[Callable[[str], Optional[float]]]) -> Dict[str, float]:
    """{SUBSIDIARY: price ₹} from the injected price source, else the peer
    table's price (the same source the peers are priced from)."""
    out: Dict[str, float] = {}
    for r in stakes:
        sub = _bare(r.get("subsidiary") or r.get("symbol") or r.get("ticker"))
        if not sub:
            continue
        px = None
        if price_of is not None:
            try:
                px = num(price_of(f"{sub}.NS"))
            except Exception:                             # noqa: BLE001
                px = None
        if px is None:
            px = num((peer_index.get(sub) or {}).get("price"))
        if px is not None and px > 0:
            out[sub] = px
    return out


def _segment_multiples(segments: List[dict], peer_index: Dict[str, dict],
                       owner: dict, sym: str) -> Dict[str, dict]:
    """{segment: {multiple, source, peers}} — median EV/EBIT of the
    segment's default peers, or of the owner's typed peer list. A typed
    segment multiple (owner_inputs.segment_multiples) wins inside
    scenarios.justified_range."""
    dflt = ASSUMED["segment_default_peers"]["value"]
    plist = [str(x) for x in (owner.get("peer_list") or []) if str(x).strip()]
    out: Dict[str, dict] = {}
    for r in segments:
        seg = str(r.get("segment") or "").strip()
        if not seg:
            continue
        if plist:
            peers_, src = plist, "median EV/EBIT of your typed peer list"
        else:
            key = _segment_key(seg)
            peers_, src = list(dflt.get(key) or dflt["Others"]), (
                f"median EV/EBIT of the default {key} peers (ASSUMED)")
        m, used = _median_of(peer_index, peers_, "ev_ebit", exclude=sym)
        # the group rides along even with no peer median, so a typed group
        # name can still reach this segment (reviewer defect 1, 2026-10-21)
        out[seg] = {"multiple": m, "group": _segment_key(seg),
                    "source": f"{src}: {', '.join(used)}" if m is not None else "",
                    "peers": used if m is not None else []}
    return out


def _fallback_block(fb: Any) -> Optional[dict]:
    """The nav route's P/B fallback (a book-route range) as plain numbers."""
    if not isinstance(fb, dict) or is_dn(fb):
        return None
    lo, ba, hi = num(fb.get("low")), num(fb.get("base")), num(fb.get("high"))
    if ba is None:
        return None
    return {"label": "P/B (book route)", "low": lo, "base": ba, "high": hi,
            "method_text": fb.get("method_text") or ""}


def _whatif_block(w: Any) -> Optional[dict]:
    """VAL-09: the owner's sheet price per case (EPS × opening P/E × (1 +
    bridge)) printed BESIDE the band range — never the range itself."""
    if not isinstance(w, dict) or is_dn(w):
        return None
    out = {c: num(w.get(c)) for c in SC.CASES}
    return out if any(v is not None for v in out.values()) else None


def _single_route_block(r: Any) -> Optional[dict]:
    """A conglomerate's single-route range, kept beside its sum-of-parts."""
    if not isinstance(r, dict) or is_dn(r):
        return None
    lo, ba, hi = num(r.get("low")), num(r.get("base")), num(r.get("high"))
    return {"family": r.get("family"), "low": lo, "base": ba, "high": hi,
            "status": "OK" if None not in (lo, ba, hi) else "DATA_NEEDED",
            "method_text": r.get("method_text") or ""}


def _band_out(band: Any) -> Optional[dict]:
    if isinstance(band, dict) and not is_dn(band) and num(band.get("low")) is not None:
        out = {"low": num(band.get("low")), "mid": num(band.get("mid")),
               "high": num(band.get("high")), "source": band.get("source") or ""}
        if isinstance(band.get("dropped"), dict):
            out["dropped"] = band["dropped"]          # VAL-03 trim: the two readings dropped
        return out
    return None


def _range_block(rng: dict, extra_note: str = "") -> dict:
    lo, ba, hi = num(rng.get("low")), num(rng.get("base")), num(rng.get("high"))
    band_out = _band_out(rng.get("multiple_band"))
    method = (rng.get("method_text") or "")
    if extra_note:
        method = f"{method} · {extra_note}"
    common = {"inverted": bool(rng.get("inverted")),
              "inverted_note": str(rng.get("inverted_note") or ""),
              "whatif": _whatif_block(rng.get("whatif")),
              "route_family": rng.get("route_family"),
              "single_route": _single_route_block(rng.get("single_route"))}
    if None in (lo, ba, hi):
        why_src = rng.get("base") if is_dn(rng.get("base")) else (
            rng.get("low") if is_dn(rng.get("low")) else rng.get("high"))
        why = _why(why_src, "; ".join(rng.get("data_needed") or []) or "range not computable")
        if is_dn(rng.get("status")):
            why = rng["status"].get("reason", why)
        return {"status": "DATA_NEEDED", "why": why[:220], "low": lo, "base": ba, "high": hi,
                "method_text": method, "multiple_band": band_out, "family": rng.get("family"),
                "fallback": _fallback_block(rng.get("fallback")),
                "notes": rng.get("notes") or [], "bridge": rng.get("bridge"), **common}
    return {"status": "OK", "why": "", "low": lo, "base": ba, "high": hi,
            "method_text": method, "multiple_band": band_out, "family": rng.get("family"),
            "vs_price_pct": rng.get("vs_price_pct"), "notes": rng.get("notes") or [],
            "sensitivity": rng.get("sensitivity"), "bridge": rng.get("bridge"), **common}


def _verdict_block(v: dict, warnings: Optional[List[str]] = None) -> dict:
    return {"verdict": v.get("verdict") or "insufficient",
            "upside_to_base_pct": num(v.get("upside_to_base_pct")),
            "discount_to_low_pct": num(v.get("discount_to_low_pct")),
            "range_width_pct": num(v.get("range_width_pct")),
            "reasons": [str(x) for x in (v.get("reasons") or [])],
            "warnings": [str(x) for x in (warnings or [])]}


def verdict_warnings(q: dict, bridge: dict) -> List[str]:
    """VAL-08 (owner choice 2026-10-21): the earnings-quality score and an
    unexplained share bridge print as WARNINGS beside the verdict — never
    gates."""
    out: List[str] = []
    th = float(ASSUMED["quality_warning_below"]["value"])
    sc = num((q or {}).get("score"))
    if sc is not None and sc < th:
        out.append(f"earnings quality {sc:.0f}/100 is below {th:g} (ASSUMED) — read the "
                   "quality flags before trusting this target")
    st = (bridge or {}).get("status")
    if st == "unexplained":
        pairs = [str(x) for x in ((bridge or {}).get("unexplained_pairs") or [])]
        what = f" ({', '.join(pairs[:2])})" if pairs else ""
        out.append("share count change not explained by a recorded split, bonus, buyback "
                   f"or issue{what} — a merger, IPO or QIP may sit behind it; EPS years "
                   "before it may not compare")
    return out


def _fmt(kind: str, v: Optional[float]) -> str:
    if v is None:
        return ""
    if kind == "pct":
        return f"{v * 100:.1f}%"
    if kind == "x":
        return f"{v:.2f}×"
    if kind == "inr":
        return f"₹{v:,.2f}"
    if kind == "inr_cr":
        return f"₹{v / CR:,.0f} cr"
    return f"{v:.2f}"


def _terms_rows(snap: dict, price: Optional[float], market: dict) -> List[dict]:
    rows = []
    try:
        details = T.detail_all(snap, price=price, market=market)
    except Exception as e:                                # noqa: BLE001
        log.warning("valuation chain: terms failed: %s", e)
        details = {}
    for name, entry in T.TERMS.items():
        d = details.get(name) or {}
        val = d.get("value")
        v = num(val)
        rows.append({"name": name, "formula": entry.get("formula") or "",
                     "stage": entry.get("stage") or "", "value": v,
                     "text": _fmt(KEY_TERM_KIND.get(name, "num"), v),
                     "why": _why(val, "") if v is None else "",
                     "parts": d.get("parts") or {}})
    return rows


# ------------------------------------------------------------------ peers

#: The fundamentals each peer row needs, stored by `peer_base` so the
#: multiples can be re-priced without re-opening 200 statements.
#: 2026-10-21 (PEER_BASE_VERSION 3): + minorities and short-term
#: investments (one EV rule, VAL-12a), EBIT (segment EV/EBIT, VAL-01) and
#: the lender book (VAL-02).
PEER_BASE_FIELDS = ("basic_eps", "shares_outstanding", "ebitda", "revenue",
                    "total_debt", "cash", "equity", "minority_interest_bs")


def peer_base(price_of: Callable[[str], Optional[float]], sector_map: dict,
              company_names: Optional[dict] = None) -> List[dict]:
    """The PRICE-FREE half of the peer table: one row per statement name
    {ticker, method, basic_eps, shares_outstanding, ebitda, revenue,
    total_debt, cash, equity, minority_interest_bs, short_term_investments,
    ebit, book, book_basis, version}. The price is passed to the snapshot only
    for the ADR currency check (a USD-filed name converts at the period-end
    USDINR, not at the price), so the stored figures stay valid while the
    statements do. Den fix 2026-10-15: this is the slow part (200 statement
    reads, ~9 s) and it is now built rarely and stored.

    A conglomerate stays in its SECTOR's peer group (route `sector_method`)
    so the other names' medians do not move when it gets a sum-of-parts."""
    out: List[dict] = []
    names = company_names or {}
    for sym in statement_symbols():
        t = f"{sym}.NS"
        try:
            px = price_of(t)
            snap = S.snapshot(sym, price=px)
            rt = R.route_for(t, sector_map, names.get(t, ""))
            method = rt.get("sector_method") or rt.get("method")
            if not method or snap.get("source") == "none":
                continue
            row = {"ticker": t, "method": method, "version": PEER_BASE_VERSION}
            for k in PEER_BASE_FIELDS:
                row[k] = _f(snap, k)
            row["short_term_investments"] = _sti(snap)
            ebit = _f(snap, "ebit")
            row["ebit"] = ebit if ebit is not None else _f(snap, "operating_income")
            if method in LENDER_METHODS:
                book, basis = _lender_book(sym, snap, snap.get("period_end"))
                row["book"] = book * CR if book is not None else None
                row["book_basis"] = basis
            # VALFIX batch 3 (§4 sector totals): latest PAT, the route group
            # and the last FY_HISTORY_YEARS FYs, read in this same pass
            row["pat"] = _f(snap, "net_income_common")
            row["sector"] = rt.get("sector") or ""
            row["family"] = rt.get("sector_family") or rt.get("family") or ""
            row["fy"] = _fy_history(sym, snap, method)
            out.append(row)
        except Exception as e:                            # noqa: BLE001
            log.warning("valuation chain: peer row failed for %s: %s", sym, e)
    return out


#: FYs of history a peer-base row keeps for the §4 sector totals (batch 3).
FY_HISTORY_YEARS = 4


def _fy_history(sym: str, snap: dict, method: Optional[str]) -> Dict[str, dict]:
    """{period_end: {shares, pat, ebitda, debt, cash, sti, mi, equity, book}}
    for the last FY_HISTORY_YEARS annual periods (rupees; a lender's book is
    its VAL-02 book for that year)."""
    hist = {k: _hist(snap, k) for k in ("shares_outstanding", "net_income_common", "ebitda",
                                         "total_debt", "cash", "short_term_investments",
                                         "minority_interest_bs", "equity")}
    periods = sorted(hist["shares_outstanding"])[-FY_HISTORY_YEARS:]
    out: Dict[str, dict] = {}
    for p in periods:
        row = {"shares": hist["shares_outstanding"].get(p), "pat": hist["net_income_common"].get(p),
               "ebitda": hist["ebitda"].get(p), "debt": hist["total_debt"].get(p),
               "cash": hist["cash"].get(p), "sti": hist["short_term_investments"].get(p),
               "mi": hist["minority_interest_bs"].get(p), "equity": hist["equity"].get(p)}
        if method in LENDER_METHODS:
            try:
                bk, _basis = _lender_book(sym, snap, p)
                row["book"] = bk * CR if bk is not None else None
            except Exception:                             # noqa: BLE001
                row["book"] = None
        out[p] = row
    return out


def peer_rows_from_base(base: List[dict], price_of: Callable[[str], Optional[float]]
                        ) -> Dict[str, List[dict]]:
    """{method: [{ticker, price, pe, ev_ebitda, ev_ebit, pb, ev_sales}]} — the
    base rows priced at `price_of(ticker)`. Pure arithmetic, no file or
    network. EV = price × shares + debt − cash − short-term investments +
    minorities (the stock's own rule, VAL-12a; a missing line counts 0). A
    lender's P/B reads its VAL-02 book (filed net worth or tangible book)."""
    out: Dict[str, List[dict]] = {}
    for b in base or []:
        t = b.get("ticker")
        method = b.get("method")
        if not t or not method:
            continue
        try:
            px = price_of(t)
        except Exception:                                 # noqa: BLE001
            px = None
        px = num(px)
        eps, sh = num(b.get("basic_eps")), num(b.get("shares_outstanding"))
        ebitda, rev = num(b.get("ebitda")), num(b.get("revenue"))
        debt, cash, eq = num(b.get("total_debt")), num(b.get("cash")), num(b.get("equity"))
        sti = num(b.get("short_term_investments")) or 0.0
        mi = num(b.get("minority_interest_bs")) or 0.0
        ebit = num(b.get("ebit"))
        book = num(b.get("book")) if b.get("book") is not None else eq
        row = {"ticker": t, "price": px, "pe": None, "ev_ebitda": None, "ev_ebit": None,
               "pb": None, "ev_sales": None}
        if px and eps and eps > 0:
            row["pe"] = px / eps
        if px and sh and book and book > 0:
            row["pb"] = px / (book / sh)
        if px and sh and debt is not None and cash is not None:
            ev = px * sh + debt - cash - sti + mi
            if ebitda and ebitda > 0:
                row["ev_ebitda"] = ev / ebitda
            if ebit and ebit > 0:
                row["ev_ebit"] = ev / ebit
            if rev and rev > 0:
                row["ev_sales"] = ev / rev
        out.setdefault(method, []).append(row)
    return out


def peer_table(price_of: Callable[[str], Optional[float]], sector_map: dict,
               company_names: Optional[dict] = None) -> Dict[str, List[dict]]:
    """{method: [{ticker, pe, ev_ebitda, pb, ev_sales}]} over every name in
    the statements zip — the ASSUMED peer definition. One snapshot per
    name; ADR names convert through their price. = peer_base priced by
    peer_rows_from_base (one definition, two steps)."""
    return peer_rows_from_base(peer_base(price_of, sector_map, company_names), price_of)


# --------------------------------------------------------------- evaluate

def _owner_row(owner_inputs: Any, sym: str) -> dict:
    """The symbol's row of the Den inputs table. Accepts the row itself, the
    table {SYMBOL: row} or the settings doc {"inputs": {SYMBOL: row}}."""
    if not isinstance(owner_inputs, dict) or not owner_inputs:
        return {}
    tbl = owner_inputs.get("inputs") if isinstance(owner_inputs.get("inputs"), dict) else None
    if tbl is None and any(isinstance(v, dict) and k.upper() == k and not k.islower()
                           for k, v in owner_inputs.items()):
        tbl = owner_inputs
    if tbl is not None:
        row = tbl.get(sym) or tbl.get(f"{sym}.NS") or {}
        return dict(row) if isinstance(row, dict) else {}
    return dict(owner_inputs)


#: The cost / capex / work-from-home / attrition drivers (all 0 in the app).
_COST_DRIVERS = ("sector_move_pct", "wfh_shift_pp", "other_capex", "electricity_tariff_pct",
                 "fuel_price_pct", "hardware_cost_pct", "rent_facility_pct", "wage_hike_pct",
                 "attrition_change_pp", "other_cost_inflation_pct", "order_wins_pct",
                 "bid_discount_pct", "tax_rate_change_pp", "one_off_costs")


def live_inputs(macro: dict, route: dict, drivers: dict, chain_ran: bool,
                e101_it_rupee: bool = False) -> Dict[str, bool]:
    """Which bridge / cost inputs THIS record really fed (item 9, owner
    2026-10-21) — assumptions.no_effect_keys(live=…) reads it.
    `e101_it_rupee` = the item-19 exit record's rule (see _market)."""
    macro = macro or {}

    def has(*ks: str) -> bool:
        return all(num(macro.get(k)) is not None for k in ks)

    if e101_it_rupee:
        # the batch-2 reading (the item-19 exit record, batch-3 review)
        is_it = str((route or {}).get("sector") or "").startswith("IT services")
        usdinr_fed = bool(is_it and has("usdinr_now", "usdinr_base"))
    else:
        # E101 is counted 0 in the live chain since 2026-10-21 (batch 3)
        usdinr_fed = False
    return {
        "market": has("nifty_start", "nifty_end") or has("sensex_start", "sensex_end"),
        "usdinr": usdinr_fed,
        "repo": has("repo_start_pct") and (has("repo_end_pct") or has("repo_pct")),
        "cpi": has("cpi_pct"), "gdp": has("gdp_pct"),
        "brent": has("brent_start", "brent_end"),
        "sentiment": False, "chart": False,
        "inst_holding": has("inst_holding_start", "inst_holding_end"),
        "delivery": has("delivery_pct"),
        "drivers": any((num((drivers or {}).get(k)) or 0.0) != 0.0 for k in _COST_DRIVERS),
        "chain": bool(chain_ran),
    }


def _row_safe(r: dict) -> dict:
    return {"period": r.get("period"), "eps": num(r.get("eps")),
            "eps_basis": r.get("eps_basis") or "", "shares": num(r.get("shares")),
            "price": num(r.get("price")), "pe": num(r.get("pe")), "flag": r.get("flag")}


def evaluate(ticker: str, *, price: Optional[float], price_date: str = "",
             sector_map: Optional[dict] = None, company_name: str = "",
             price_at: Optional[Callable[[Any], Optional[float]]] = None,
             own_pe_history: Optional[Callable[[dict], List[float]]] = None,
             peers: Optional[Callable[[str, str], dict]] = None,
             macro: Optional[Callable[[dict], dict]] = None,
             asof: Optional[str] = None,
             overrides: Optional[dict] = None,
             owner_inputs: Optional[dict] = None,
             peer_rows: Any = None,
             price_of: Optional[Callable[[str], Optional[float]]] = None,
             e101_it_rupee: bool = False) -> dict:
    """Stages 07-18 for one name. Every outside input is injected.

    `overrides` = the Den's assumption draft ({key: value} over
    assumptions.EDITABLE, owner order 2026-10-14). It is split by module
    and passed DOWN as arguments; no module default is mutated and the
    record says which keys were drafted (`assumptions.drafted`), which of
    them change a result (`assumptions.drafted_effective`; `drafted` is
    True only when there is one) and which change nothing yet
    (`assumptions.drafted_no_effect`, VAL-11 — per render: a sheet number
    whose input is not fed counts as no effect, item 9).

    Batch 2 (owner choices 2026-10-21):
      own P/E history — computed HERE (own_pe_rows: normalised PAT ÷ each
          year's own shares, odd years flagged); `own_pe_history` (a
          callable returning multiples) replaces it only when a caller
          passes one (tests, what-ifs) and the record says so;
      owner_inputs — the Den inputs table (settings doc
          `valuation_inputs`): the symbol's row, the {SYMBOL: row} table or
          the whole doc; typed NAV / EV / holding discount / segment
          multiples / peer_list;
      peer_rows — the priced peer table ({method: [rows]} from
          peer_rows_from_base, or a callable returning it): a typed
          peer_list and the conglomerate segment multiples read it;
      price_of — today's price of any ticker (holding-company subsidiaries);
          the peer table's price is the fallback.

    `e101_it_rupee` (batch-3 review, 2026-10-21): True = the rupee row E101
    is live for IT-services routes (the batch-2 branch) — what the item-19
    exit record is built with, because the pre-registered study measured
    that rule. False (default) = batch 3: E101 counted 0."""
    sm = sector_map or {}
    price_at = price_at or (lambda when: None)
    peers = peers or (lambda method, exclude: {})
    macro = macro or (lambda snap: {})
    t = ticker if ticker.endswith(".NS") else f"{ticker}.NS"
    sym = t[:-3]
    data_needed: List[str] = []
    notes: List[str] = []
    ov = ASM.split(overrides)

    snap = S.snapshot(sym, price=price, asof=asof,
                      publication_lag_days=ov["snapshot"].get("publication_lag_days"),
                      usd_signature_pe=ov["snapshot"].get("usd_signature_pe"))
    rt = R.route_for(t, sm, company_name)
    family = rt.get("family") or ""
    method = rt.get("method")
    # a conglomerate runs the chain on its sector's family; the segments
    # turn the range into a sum-of-parts (scenarios keeps the single route)
    segments = _segment_rows(sym) if method == R.CONGLOMERATE_METHOD else []
    chain_family = (rt.get("sector_family") or family) if segments else family
    if method == R.CONGLOMERATE_METHOD and not segments:
        chain_family = rt.get("sector_family") or family
        notes.append("conglomerate route but no segment rows on file — single-route range")
    bridge = SH.share_bridge(sym, snap, tol_pct=ov["shares"].get("reconcile_tol_pct"))
    ne = N.normalised_earnings(snap)
    q = N.quality(snap, chain_family, thresholds=ov["quality"] or None,
                  lender=method in LENDER_METHODS)
    owner = _owner_row(owner_inputs, sym)
    peer_index = _peer_index(peer_rows) if peer_rows is not None else {}

    rec: Dict[str, Any] = {
        "ticker": t, "version": VERSION, "price": price, "price_date": price_date,
        "asof": asof, "route": _route_block(rt), "snapshot": _snapshot_block(snap),
        "shares": _shares_block(bridge), "earnings": _earnings_block(ne, snap),
        "quality": _quality_block(q),
    }
    _pat, pat_basis = target_pat(ne, snap)
    rec["earnings"]["target_starts_from"] = pat_basis
    for k, why in (snap.get("data_needed") or {}).items():
        data_needed.append(f"snapshot.{k}: {why}")

    mac = {}
    try:
        mac = macro(snap) or {}
    except Exception as e:                                # noqa: BLE001
        log.warning("valuation chain: macro inputs failed for %s: %s", t, e)
    pr = {}
    peer_method = rt.get("sector_method") or method
    if peer_method:
        try:
            pr = peers(peer_method, t) or {}
        except Exception as e:                            # noqa: BLE001
            log.warning("valuation chain: peers failed for %s: %s", t, e)

    # ---- own P/E history (VAL-14a): computed here from the snapshot
    rows: List[dict] = []
    try:
        rows = own_pe_rows(snap, price_at, ne) if snap.get("period_end") else []
    except Exception as e:                                # noqa: BLE001
        log.warning("valuation chain: own P/E rows failed for %s: %s", t, e)
    own_hist: List[Any] = [r for r in rows if r.get("pe") is not None]
    own_src = "computed"
    if own_pe_history is not None:
        try:
            own_hist = list(own_pe_history(snap) or [])
            own_src = "caller"
        except Exception as e:                            # noqa: BLE001
            log.warning("valuation chain: own_pe_history failed for %s: %s", t, e)

    # ---- stages 15-17
    base, b_notes = _base_year(snap, price_at, ne)
    notes.extend(b_notes)
    drivers, d_notes = _drivers(base, snap, chain_family)
    notes.extend(d_notes)
    market, macro_missing = _market(mac, snap, rt, pr.get("pe_median"), base,
                                    sheet=ov["sheet"], e101_it_rupee=bool(e101_it_rupee))
    cases: dict = {}
    if rec["snapshot"]["status"] == "OK" and chain_family in ("earnings", "ev_ebitda", "ev_sales"):
        cases = SC.three_cases(base, drivers, market,
                               assumptions=ov["sheet"] or None,
                               deltas=ov["case_deltas"])
    elif rec["snapshot"]["status"] == "OK" and chain_family in ("book", "nav"):
        # the sheet's chain needs EBITDA; lenders and developers go straight
        # to the book/NAV route in justified_range (ROE and BVPS from the
        # snapshot). The cases dict stays empty and says so.
        notes.append(f"{chain_family} route: scenario EPS not run (no EBITDA chain for this "
                     "route); range from ROE / BVPS")
    rec["scenarios"] = (_scenario_block(cases, drivers, macro_missing, notes) if cases
                        else {"status": "DATA_NEEDED",
                              "why": (notes[-1] if notes and chain_family in ("book", "nav")
                                      else (rec["snapshot"]["why"] or
                                            f"{family or 'UNDECLARED'} route: no scenario chain")),
                              "eps": {}, "drivers": {}, "pe_bridge_pct": None,
                              "macro_data_needed": macro_missing, "notes": notes})

    rng: dict = {}
    kw: dict = {}
    facts: dict = {}
    if rt.get("status") != "declared":
        rng = {"status": dn(f"route UNDECLARED — {rt.get('rule') or 'no sector label'}"),
               "family": None}
        rec["range"] = _range_block(rng)
    elif rec["snapshot"]["status"] != "OK":
        rec["range"] = _range_block({"status": dn(rec["snapshot"]["why"]), "family": family})
    else:
        kw = _range_inputs(chain_family, snap, base, cases, pr, own_hist, mac,
                           ov=ov["range"], method=method, sym=sym, owner=owner,
                           peer_index=peer_index, price_of=price_of, segments=segments)
        facts = kw.pop("_facts", {}) or {}
        mid_note = " · ".join(x for x in (kw.pop("_midcycle_note", ""),
                                          kw.pop("_growth_note", ""),
                                          kw.pop("_peer_note", "")) if x)
        try:
            rng = SC.justified_range(chain_family, cases, price=price, **kw)
        except Exception as e:                            # noqa: BLE001
            log.exception("valuation chain: justified_range failed for %s", t)
            rng = {"status": dn(f"range failed: {type(e).__name__}"), "family": chain_family}
        rec["range"] = _range_block(rng, mid_note)
    rec["range"]["own_history"] = [_row_safe(r) for r in rows]
    rec["range"]["own_history_source"] = own_src
    facts["target_starts_from"] = pat_basis
    rec["range_flags"] = _range_flags(rng.get("family") or chain_family, rt, rng, kw, snap,
                                      facts)

    v = A.assess(price, {"low": rec["range"].get("low"), "base": rec["range"].get("base"),
                         "high": rec["range"].get("high"),
                         "family": rng.get("family") or chain_family},
                 bands=ov["verdict"] or None)
    rec["verdict"] = _verdict_block(v, verdict_warnings(q, bridge))

    # ---- the 52 terms
    t_market = {"rf_pct": num(mac.get("rf_pct")),
                "erp_pct": float(ov["range"].get("equity_risk_premium_pct",
                                                 _vc.ASSUMED["equity_risk_premium_pct"])),
                "growth_pct": float(ov["range"].get("long_run_growth_pct",
                                                    _vc.ASSUMED["long_run_growth_pct"])),
                "beta": num(mac.get("beta")),
                "forward_eps": (rec["scenarios"].get("eps") or {}).get("base")}
    if num(ov["terms"].get("dcf_growth_cap_pct")) is not None:
        t_market["dcf_growth_cap_pct"] = float(ov["terms"]["dcf_growth_cap_pct"])
    rec["terms_rows"] = _terms_rows(snap, price, t_market)

    for m in macro_missing:
        data_needed.append(f"bridge: {m}")
    rec["data_needed"] = data_needed
    rec["notes"] = notes
    live = live_inputs(mac, rt, drivers, bool(cases), e101_it_rupee=bool(e101_it_rupee))
    rec["assumptions"] = {"pipeline": {k: v_["value"] for k, v_ in ASSUMED.items()},
                          "snapshot": dict(S.ASSUMED), "case_deltas": ov["case_deltas"],
                          "verdict": {k: v_["value"] for k, v_ in A.ASSUMED.items()},
                          "drafted": list(ov["edited"]),
                          "drafted_effective": [],
                          "drafted_no_effect": [],
                          "drafted_values": dict(ov["values"]),
                          "live_inputs": live,
                          "no_effect_keys": ASM.no_effect_keys(live)}
    eff = ASM.effective_keys(ov["values"], asof=asof, live=live)
    rec["assumptions"]["drafted_effective"] = eff
    rec["assumptions"]["drafted_no_effect"] = [k for k in ov["edited"] if k not in eff]
    rec["drafted"] = bool(eff)
    rec["owner_inputs"] = owner or None
    # VALFIX batch 3: what the currency leg (through EPS) and the market legs
    # read — the base-year EPS and EBITDA margin, and the route group
    margin = None
    if num(base.get("revenue")) and num(base.get("ebitda")) is not None and base["revenue"] > 0:
        margin = base["ebitda"] / base["revenue"] * 100.0
    eps0 = (base["pat"] / base["shares"]) if (num(base.get("pat")) is not None
                                              and num(base.get("shares"))) else None
    rec["currency_base"] = {"eps": eps0, "ebitda_margin_pct": margin,
                            "margin_why": "" if margin is not None else
                            "no EBITDA or revenue line for the base year",
                            "sector": rt.get("sector") or "", "method": peer_method or "",
                            "family": chain_family}
    rec["detail"] = {"snapshot_fields": snap.get("fields"), "cases": cases,
                     "normalised": ne, "quality": q, "bridge": bridge}
    return rec
