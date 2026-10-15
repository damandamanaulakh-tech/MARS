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
peers = the same route among the Nifty-200 statement set; mid-cycle
EBITDA for metals = the 5-year mean; publication date = period end +
45 days (snapshot.py); case deltas (scenarios.py); verdict bands
(assess.py).
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

VERSION = "2026-10-14"
CR = 1e7                              # rupees → crore (the sheet's unit)

ASSUMED: Dict[str, dict] = {
    "driver_defaults": {
        "value": "revenue growth = last FY YoY; base EBITDA margin, PAT/EBITDA and "
                 "payout = the latest FY's own ratios; every cost/what-if driver 0; "
                 "forward net cash = base-year net cash",
        "source": "ASSUMED here 2026-10-14 — the owner's sheet types these per company"},
    "currency_effect_routes": {
        "value": ["earnings"],
        "source": "ASSUMED here: the sheet's +0.25pp margin per 1% rupee fall is an "
                  "IT-exporter rule of thumb; applied only when the route's sector "
                  "is IT services / ER&D, else the rupee leg is 0"},
    "macro_counted_zero": {
        "value": ["repo", "cpi", "gdp", "brent", "institutional change", "delivery"],
        "source": "the sheet's what-if rows the app does not store — counted 0 and "
                  "listed DATA_NEEDED on every record"},
    "peer_definition": {
        "value": "median trailing multiple of the same route among the Nifty-200 "
                 "names with statements",
        "source": "ASSUMED here until the owner supplies peer groups (task book ask)"},
    "midcycle_ebitda": {
        "value": "mean EBITDA per share over the available FYs (≥3) for metals",
        "source": "ASSUMED here — task book: 'EV/EBITDA at MID-CYCLE margin'"},
    "min_history_for_own_band": {
        "value": 3, "source": "ASSUMED here: fewer FY-end multiples = no own band"},
}

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
                  "rule": why, "overlays": []},
        "snapshot": {"status": "DATA_NEEDED", "why": why, "period_end": None,
                     "publication_date": None, "publication_assumed": None,
                     "currency": "", "converted": False, "fx": "", "basis": "",
                     "source": "", "lock_hash": "", "missing": 0},
        "shares": {"status": "DATA_NEEDED", "basic_now": None, "dilutive_pct": None,
                   "note": why},
        "earnings": {"status": "DATA_NEEDED", "why": why, "reported_pat": None,
                     "normalised_pat": None, "reported_eps": None, "normalised_eps": None,
                     "n_adjustments": 0, "matches_yahoo": None},
        "quality": {"score": None, "why": why, "flags": [], "cash_conversion": None,
                    "net_debt_ebitda": None, "interest_cover": None},
        "scenarios": {"status": "DATA_NEEDED", "why": why, "eps": {}, "drivers": {},
                      "pe_bridge_pct": None, "macro_data_needed": []},
        "range": {"status": "DATA_NEEDED", "why": why, "low": None, "base": None,
                  "high": None, "method_text": "", "multiple_band": None},
        "verdict": {"verdict": "insufficient", "upside_to_base_pct": None,
                    "reasons": [why]},
        "terms_rows": [], "data_needed": [why], "assumptions": {}, "error": why,
        "detail": {"error": d},
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
            "nearest": rt.get("nearest")}


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

def _base_year(snap: dict, price_at: Callable[[Any], Optional[float]]) -> tuple[dict, List[str]]:
    """The sheet's column D from the snapshot, in ₹ crore / crore shares."""
    notes: List[str] = []
    rev = _f(snap, "revenue")
    ebitda = _f(snap, "ebitda")
    if ebitda is None:
        oi, da = _f(snap, "operating_income"), _f(snap, "d_and_a")
        if oi is not None and da is not None:
            ebitda = oi + da
            notes.append("EBITDA = operating income + D&A (no EBITDA line)")
    pat = _f(snap, "net_income_common")
    tax = _f(snap, "tax")
    emp = _f(snap, "employee_cost")
    if emp is None:
        emp = 0.0
        notes.append("employee cost line not in statements — the wage/attrition "
                     "driver is disabled (counted 0)")
    shares = _f(snap, "shares_outstanding")
    cash = _f(snap, "cash")
    sti = num(((snap.get("extra") or {}).get("short_term_investments") or {}).get(
        snap.get("period_end"))) if isinstance((snap.get("extra") or {}).get(
            "short_term_investments"), dict) else None
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
            base: dict) -> tuple[dict, List[str]]:
    """The sheet's market inputs: stored levels where the app has them,
    neutral values (= zero effect) where it does not, each listed."""
    missing: List[str] = []
    av = {k: v["value"] for k, v in SC.SHEET_ASSUMPTIONS.items()}
    m: Dict[str, Any] = {}
    for a, b in (("sensex_start", "sensex_end"), ("nifty_start", "nifty_end")):
        s, e = num(macro.get(a)), num(macro.get(b))
        if s is None or e is None:
            s = e = 100.0
            missing.append(f"{a.split('_')[0]} level since the period end — counted 0")
        m[a], m[b] = s, e
    for k in ("repo", "cpi", "gdp", "brent"):
        pass
    m["repo_start"] = m["repo_end"] = num(macro.get("repo_pct")) or 0.0
    if num(macro.get("repo_pct")) is None:
        missing.append("RBI repo rate — no feed wired; counted 0")
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
    # rupee leg: IT exporters only (ASSUMED)
    u_now, u_base = num(macro.get("usdinr_now")), num(macro.get("usdinr_base"))
    is_it = (route.get("sector") or "").startswith("IT services")
    if is_it and u_now and u_base:
        m["usdinr"], m["usdinr_base"] = u_now, u_base
    else:
        m["usdinr"] = m["usdinr_base"] = u_base or u_now or 1.0
        if is_it:
            missing.append("USDINR now/base — rupee leg counted 0")
    m["vix"] = num(macro.get("vix")) if num(macro.get("vix")) is not None else 0.0
    m["sentiment"] = 0.0
    i0, i1 = num(macro.get("inst_holding_start")), num(macro.get("inst_holding_end"))
    if i0 is None or i1 is None:
        i0 = i1 = 0.0
        missing.append("institutional holding change — not wired into the chain; counted 0")
    m["inst_holding_start"], m["inst_holding_end"] = i0, i1
    m["volume_ratio"] = 1.0
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


def _range_inputs(family: str, snap: dict, base: dict, cases: dict, peers: dict,
                  own_pe_hist: List[float], macro: dict,
                  ov: Optional[dict] = None) -> dict:
    """The keyword arguments justified_range needs for this family. `ov` =
    the Den draft's range inputs (ERP, long-run g, ROE step) by name."""
    kw: Dict[str, Any] = {}
    ov = ov or {}
    shares = _f(snap, "shares_outstanding")
    if family == "earnings":
        kw["peer_multiple"] = peers.get("pe_median")
        kw["own_multiple_history"] = own_pe_hist if len(own_pe_hist) >= int(
            ASSUMED["min_history_for_own_band"]["value"]) else None
    elif family in ("book", "nav"):
        roe = (base["pat"] / base["equity"] * 100) if (base.get("pat") is not None and base.get("equity")) else None
        kw["roe_pct"] = roe
        kw["bvps"] = (base["equity"] / base["shares"]) if (base.get("equity") is not None and base.get("shares")) else None
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
        if family == "nav":
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
                hist = _hist(snap, "ebitda")
                if len(hist) >= 3:
                    # base case = mid-cycle EBITDA per share; the downside /
                    # upside keep the scenario chain's own EBITDA so the range
                    # has a width (a point band would read as false precision)
                    mid = sum(hist.values()) / len(hist) / shares
                    kw["per_share_overrides"] = {"base": mid}
                    kw["_midcycle_note"] = (f"base = mid-cycle EBITDA/share, mean of {len(hist)} FYs "
                                            f"({ASSUMED['midcycle_ebitda']['source']})")
            if family == "ev_sales" and rev is not None:
                kw["ebitda_per_share"] = rev / shares     # the module reads revenue/share here
            debt, cash = _f(snap, "total_debt"), _f(snap, "cash")
            sti_h = (snap.get("extra") or {}).get("short_term_investments")
            sti = num(sti_h.get(snap.get("period_end"))) if isinstance(sti_h, dict) else None
            if debt is not None and cash is not None:
                kw["net_debt_per_share"] = (debt - cash - (sti or 0.0)) / shares
            mi = _f(snap, "minority_interest_bs")
            kw["minorities_per_share"] = (mi / shares) if mi is not None else 0.0
        kw["peer_multiple"] = peers.get("ev_ebitda_median") if family == "ev_ebitda" else peers.get("ev_sales_median")
    return kw


def _fallback_block(fb: Any) -> Optional[dict]:
    """The nav route's P/B fallback (a book-route range) as plain numbers."""
    if not isinstance(fb, dict) or is_dn(fb):
        return None
    lo, ba, hi = num(fb.get("low")), num(fb.get("base")), num(fb.get("high"))
    if ba is None:
        return None
    return {"label": "P/B (book route)", "low": lo, "base": ba, "high": hi,
            "method_text": fb.get("method_text") or ""}


def _range_block(rng: dict, extra_note: str = "") -> dict:
    lo, ba, hi = num(rng.get("low")), num(rng.get("base")), num(rng.get("high"))
    band = rng.get("multiple_band")
    band_out = None
    if isinstance(band, dict) and not is_dn(band) and num(band.get("low")) is not None:
        band_out = {"low": num(band.get("low")), "mid": num(band.get("mid")),
                    "high": num(band.get("high")), "source": band.get("source") or ""}
    method = (rng.get("method_text") or "")
    if extra_note:
        method = f"{method} · {extra_note}"
    if None in (lo, ba, hi):
        why_src = rng.get("base") if is_dn(rng.get("base")) else (
            rng.get("low") if is_dn(rng.get("low")) else rng.get("high"))
        why = _why(why_src, "; ".join(rng.get("data_needed") or []) or "range not computable")
        if is_dn(rng.get("status")):
            why = rng["status"].get("reason", why)
        return {"status": "DATA_NEEDED", "why": why[:220], "low": lo, "base": ba, "high": hi,
                "method_text": method, "multiple_band": band_out, "family": rng.get("family"),
                "fallback": _fallback_block(rng.get("fallback")),
                "notes": rng.get("notes") or []}
    return {"status": "OK", "why": "", "low": lo, "base": ba, "high": hi,
            "method_text": method, "multiple_band": band_out, "family": rng.get("family"),
            "vs_price_pct": rng.get("vs_price_pct"), "notes": rng.get("notes") or [],
            "sensitivity": rng.get("sensitivity"), "bridge": rng.get("bridge")}


def _verdict_block(v: dict) -> dict:
    return {"verdict": v.get("verdict") or "insufficient",
            "upside_to_base_pct": num(v.get("upside_to_base_pct")),
            "discount_to_low_pct": num(v.get("discount_to_low_pct")),
            "range_width_pct": num(v.get("range_width_pct")),
            "reasons": [str(x) for x in (v.get("reasons") or [])]}


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
PEER_BASE_FIELDS = ("basic_eps", "shares_outstanding", "ebitda", "revenue",
                    "total_debt", "cash", "equity")


def peer_base(price_of: Callable[[str], Optional[float]], sector_map: dict,
              company_names: Optional[dict] = None) -> List[dict]:
    """The PRICE-FREE half of the peer table: one row per statement name
    {ticker, method, basic_eps, shares_outstanding, ebitda, revenue,
    total_debt, cash, equity}. The price is passed to the snapshot only for
    the ADR currency check (a USD-filed name converts at the period-end
    USDINR, not at the price), so the stored figures stay valid while the
    statements do. Den fix 2026-10-15: this is the slow part (200 statement
    reads, ~9 s) and it is now built rarely and stored."""
    out: List[dict] = []
    names = company_names or {}
    for sym in statement_symbols():
        t = f"{sym}.NS"
        try:
            px = price_of(t)
            snap = S.snapshot(sym, price=px)
            rt = R.route_for(t, sector_map, names.get(t, ""))
            method = rt.get("method")
            if not method or snap.get("source") == "none":
                continue
            row = {"ticker": t, "method": method}
            for k in PEER_BASE_FIELDS:
                row[k] = _f(snap, k)
            out.append(row)
        except Exception as e:                            # noqa: BLE001
            log.warning("valuation chain: peer row failed for %s: %s", sym, e)
    return out


def peer_rows_from_base(base: List[dict], price_of: Callable[[str], Optional[float]]
                        ) -> Dict[str, List[dict]]:
    """{method: [{ticker, pe, ev_ebitda, pb, ev_sales}]} — the base rows
    priced at `price_of(ticker)`. Pure arithmetic, no file or network."""
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
        eps, sh = num(b.get("basic_eps")), num(b.get("shares_outstanding"))
        ebitda, rev = num(b.get("ebitda")), num(b.get("revenue"))
        debt, cash, eq = num(b.get("total_debt")), num(b.get("cash")), num(b.get("equity"))
        row = {"ticker": t, "pe": None, "ev_ebitda": None, "pb": None, "ev_sales": None}
        if px and eps and eps > 0:
            row["pe"] = px / eps
        if px and sh and eq and eq > 0:
            row["pb"] = px / (eq / sh)
        if px and sh and debt is not None and cash is not None:
            ev = px * sh + debt - cash
            if ebitda and ebitda > 0:
                row["ev_ebitda"] = ev / ebitda
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

def evaluate(ticker: str, *, price: Optional[float], price_date: str = "",
             sector_map: Optional[dict] = None, company_name: str = "",
             price_at: Optional[Callable[[Any], Optional[float]]] = None,
             own_pe_history: Optional[Callable[[dict], List[float]]] = None,
             peers: Optional[Callable[[str, str], dict]] = None,
             macro: Optional[Callable[[dict], dict]] = None,
             asof: Optional[str] = None,
             overrides: Optional[dict] = None) -> dict:
    """Stages 07-18 for one name. Every outside input is injected.

    `overrides` = the Den's assumption draft ({key: value} over
    assumptions.EDITABLE, owner order 2026-10-14). It is split by module
    and passed DOWN as arguments; no module default is mutated and the
    record says which keys were drafted (`drafted`, `assumptions.drafted`)."""
    sm = sector_map or {}
    ov = ASM.split(overrides)
    price_at = price_at or (lambda when: None)
    own_pe_history = own_pe_history or (lambda snap: [])
    peers = peers or (lambda method, exclude: {})
    macro = macro or (lambda snap: {})
    t = ticker if ticker.endswith(".NS") else f"{ticker}.NS"
    sym = t[:-3]
    data_needed: List[str] = []
    notes: List[str] = []

    snap = S.snapshot(sym, price=price, asof=asof,
                      publication_lag_days=ov["snapshot"].get("publication_lag_days"),
                      usd_signature_pe=ov["snapshot"].get("usd_signature_pe"))
    rt = R.route_for(t, sm, company_name)
    family = rt.get("family") or ""
    bridge = SH.share_bridge(sym, snap, tol_pct=ov["shares"].get("reconcile_tol_pct"))
    ne = N.normalised_earnings(snap)
    q = N.quality(snap, family, thresholds=ov["quality"] or None)

    rec: Dict[str, Any] = {
        "ticker": t, "version": VERSION, "price": price, "price_date": price_date,
        "asof": asof, "route": _route_block(rt), "snapshot": _snapshot_block(snap),
        "shares": _shares_block(bridge), "earnings": _earnings_block(ne, snap),
        "quality": _quality_block(q),
    }
    for k, why in (snap.get("data_needed") or {}).items():
        data_needed.append(f"snapshot.{k}: {why}")

    mac = {}
    try:
        mac = macro(snap) or {}
    except Exception as e:                                # noqa: BLE001
        log.warning("valuation chain: macro inputs failed for %s: %s", t, e)
    pr = {}
    if rt.get("method"):
        try:
            pr = peers(rt["method"], t) or {}
        except Exception as e:                            # noqa: BLE001
            log.warning("valuation chain: peers failed for %s: %s", t, e)

    # ---- stages 15-17
    base, b_notes = _base_year(snap, price_at)
    notes.extend(b_notes)
    drivers, d_notes = _drivers(base, snap, family)
    notes.extend(d_notes)
    market, macro_missing = _market(mac, snap, rt, pr.get("pe_median"), base)
    cases: dict = {}
    if rec["snapshot"]["status"] == "OK" and family in ("earnings", "ev_ebitda", "ev_sales"):
        cases = SC.three_cases(base, drivers, market,
                               assumptions=ov["sheet"] or None,
                               deltas=ov["case_deltas"])
    elif rec["snapshot"]["status"] == "OK" and family in ("book", "nav"):
        # the sheet's chain needs EBITDA; lenders and developers go straight
        # to the book/NAV route in justified_range (ROE and BVPS from the
        # snapshot). The cases dict stays empty and says so.
        notes.append(f"{family} route: scenario EPS not run (no EBITDA chain for this "
                     "route); range from ROE / BVPS")
    rec["scenarios"] = (_scenario_block(cases, drivers, macro_missing, notes) if cases
                        else {"status": "DATA_NEEDED",
                              "why": (notes[-1] if notes and family in ("book", "nav")
                                      else (rec["snapshot"]["why"] or
                                            f"{family or 'UNDECLARED'} route: no scenario chain")),
                              "eps": {}, "drivers": {}, "pe_bridge_pct": None,
                              "macro_data_needed": macro_missing, "notes": notes})

    if rt.get("status") != "declared":
        rng = {"status": dn(f"route UNDECLARED — {rt.get('rule') or 'no sector label'}"),
               "family": None}
        rec["range"] = _range_block(rng)
    elif rec["snapshot"]["status"] != "OK":
        rec["range"] = _range_block({"status": dn(rec["snapshot"]["why"]), "family": family})
    else:
        kw = _range_inputs(family, snap, base, cases, pr, own_pe_history(snap), mac,
                           ov=ov["range"])
        mid_note = " · ".join(x for x in (kw.pop("_midcycle_note", ""),
                                          kw.pop("_growth_note", "")) if x)
        try:
            rng = SC.justified_range(family, cases, price=price, **kw)
        except Exception as e:                            # noqa: BLE001
            log.exception("valuation chain: justified_range failed for %s", t)
            rng = {"status": dn(f"range failed: {type(e).__name__}"), "family": family}
        rec["range"] = _range_block(rng, mid_note)

    v = A.assess(price, {"low": rec["range"].get("low"), "base": rec["range"].get("base"),
                         "high": rec["range"].get("high"), "family": family},
                 bands=ov["verdict"] or None)
    rec["verdict"] = _verdict_block(v)

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
    rec["assumptions"] = {"pipeline": {k: v_["value"] for k, v_ in ASSUMED.items()},
                          "snapshot": dict(S.ASSUMED), "case_deltas": ov["case_deltas"],
                          "verdict": {k: v_["value"] for k, v_ in A.ASSUMED.items()},
                          "drafted": list(ov["edited"]),
                          "drafted_values": dict(ov["values"])}
    rec["drafted"] = bool(ov["edited"])
    rec["detail"] = {"snapshot_fields": snap.get("fields"), "cases": cases,
                     "normalised": ne, "quality": q, "bridge": bridge}
    return rec
