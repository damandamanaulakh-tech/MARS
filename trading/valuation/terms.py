"""MARS valuation chain — the 52 finance terms of the task book (sheet
"Finance terms", 2026-10-12), each a plain formula over the locked
snapshot (stages 07-08, snapshot.py). DISPLAY ONLY.

Owner order 2026-10-14 ("lets hit the formulas work completion"). Money
impact: NONE — no gate, stop, size or exit reads this module (package
boundary, test-pinned). The terms feed the Den's valuation stages
(11, 12, 15, 16, 17, 18, 20 — the "stage" column of each entry is copied
from the task book).

CONTRACT
--------
* `TERMS[name]` = {"formula", "stage", "note", "fn"} for exactly the 52
  names of the sheet, spelled as the sheet spells them.
* `compute(term, snap, price=, market=)` → float or DATA_NEEDED (`dn`),
  never raises. `detail(...)` → the same value plus its parts (both legs of
  a two-part term, the basis used, flags). `compute_all` / `detail_all`
  run every term.
* `snap` is a snapshot.snapshot() record (fields / history / quarterly /
  ttm / extras); `price` is rupees per share; `market` is an optional dict
  of outside inputs. Rates come IN as percent (`rf_pct`, `erp_pct`,
  `growth_pct`, `coe_pct`, `roe_pct`, `wacc_pct`, `rate_pct`,
  `eps_growth_pct`, `kd_pct`) and go OUT as decimals (ROE 0.487 = 48.7%).
  Other market keys: `beta`, `forward_eps`, `dps`, `trade` {entry, exit,
  costs}, `cash_flows` [CF_1..CF_n], `outlay`, `fcf_forecast`
  [FCF_1..FCF_n], `stock_series` / `sector_series` / `index_series`
  (closes, oldest first) or `stock_1m_pct` / `sector_1m_pct` /
  `market_1m_pct`, `inst_pct_now` / `inst_pct_prev`, `promoter_pct`,
  `pledge_pct`.
* Every formula is a plain function over floats; any missing input is
  `dn` naming it (and the snapshot's own reason when the snapshot said
  why). Nothing is defaulted to 0.

PUBLISHED identities (not choices): Graham 22.5 (= 15 × P/E · 1.5 × P/B),
Altman Z weights 1.2 / 1.4 / 3.3 / 0.6 / 1.0, Piotroski's nine tests.
ASSUMED (printed with their source): DCF growth cap 10% (nominal GDP) ·
cash-conversion flag 0.7 · net debt/EBITDA flag 3 · interest-cover flag 2
(the three flags from the task book notes) · prior-year window 300-430
days · 1 month = 21 sessions.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from trading.valuation import dn, is_dn, num

log = logging.getLogger(__name__)

VERSION = "2026-10-14"

PUBLISHED: Dict[str, Any] = {
    "graham_multiplier": 22.5,
    "altman_weights": (1.2, 1.4, 3.3, 0.6, 1.0),
    "piotroski_tests": 9,
}

ASSUMED: Dict[str, float] = {
    "dcf_growth_cap_pct": 10.0,
    "cash_conversion_flag": 0.7,
    "net_debt_ebitda_flag": 3.0,
    "interest_cover_flag": 2.0,
    "prior_year_min_days": 300,
    "prior_year_max_days": 430,
    "sessions_per_month": 21,
}
ASSUMED_SOURCE: Dict[str, str] = {
    "dcf_growth_cap_pct": "task book 2026-10-12 'g ≤ nominal GDP'; 10% ASSUMED here",
    "cash_conversion_flag": "task book 2026-10-12 note '<0.7 three years = flag'",
    "net_debt_ebitda_flag": "task book 2026-10-12 note '>3 = stress'",
    "interest_cover_flag": "task book 2026-10-12 note '<2 = flag'",
    "prior_year_min_days": "ASSUMED here — the 'previous year' must be ~1 year back",
    "prior_year_max_days": "ASSUMED here — the 'previous year' must be ~1 year back",
    "sessions_per_month": "ASSUMED here — 1M return over 21 trading sessions",
}

DAYS_PER_YEAR = 365.0


# ---------------------------------------------------------------- helpers
def _need(**inputs: Any) -> Tuple[Optional[Dict[str, float]], Optional[dict]]:
    """Floats for every input, or a DATA_NEEDED naming each missing one."""
    vals: Dict[str, float] = {}
    miss: List[str] = []
    names: List[str] = []
    for k, v in inputs.items():
        f = num(v)
        if f is None:
            names.append(k)
            miss.append(f"{k} ({v['reason']})" if is_dn(v) else k)
        else:
            vals[k] = f
    if miss:
        return None, dn("missing input: " + "; ".join(miss), missing=names)
    return vals, None


def _div(a: float, b: float, what: str) -> Any:
    if b == 0:
        return dn(f"{what}: denominator is zero")
    return float(a) / float(b)


def _fields(snap: Optional[dict]) -> dict:
    return (snap or {}).get("fields") or {}


def _f(snap: Optional[dict], name: str) -> Any:
    fields = _fields(snap)
    if not fields:
        return dn("empty snapshot (no fields)")
    v = fields.get(name)
    if v is None:
        return dn(f"{name} not in snapshot")
    return v


def _extra(snap: Optional[dict], name: str) -> Any:
    v = ((snap or {}).get("extras") or {}).get(name)
    return dn(f"{name} not in snapshot extras") if v is None else v


def _series(snap: Optional[dict], block: str, name: str) -> Dict[str, float]:
    s = ((snap or {}).get(block) or {}).get(name)
    if not isinstance(s, dict) or is_dn(s):
        return {}
    return {k: float(v) for k, v in s.items() if num(v) is not None}


def _days(a: str, b: str) -> Optional[int]:
    from datetime import date
    try:
        return (date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days
    except (TypeError, ValueError):
        return None


def _prior_period(period: Optional[str], periods: List[str]) -> Optional[str]:
    """The newest period ~one year before `period` (window ASSUMED)."""
    if not period:
        return None
    lo, hi = ASSUMED["prior_year_min_days"], ASSUMED["prior_year_max_days"]
    for p in sorted(periods, reverse=True):
        d = _days(period, p)
        if d is not None and lo <= d <= hi:
            return p
    return None


def _prev(snap: Optional[dict], name: str) -> Any:
    """The field one fiscal year before the snapshot's period."""
    period = (snap or {}).get("period_end")
    hist = _series(snap, "history", name)
    p = _prior_period(period, list(hist))
    if p is None:
        return dn(f"prior-year {name} not in history")
    return hist[p]


def _pct(market: Optional[dict], key: str) -> Any:
    v = (market or {}).get(key)
    f = num(v)
    if f is None:
        return dn(f"market input {key} not given")
    return f / 100.0


def _mkt(market: Optional[dict], key: str) -> Any:
    v = (market or {}).get(key)
    f = num(v)
    return dn(f"market input {key} not given") if f is None else f


def _price(price: Any) -> Any:
    f = num(price)
    if f is None or f <= 0:
        return dn("price not given")
    return f


def _mcap(snap: Optional[dict], price: Any) -> Any:
    v, bad = _need(price=_price(price), shares_outstanding=_f(snap, "shares_outstanding"))
    if bad:
        return bad
    return v["price"] * v["shares_outstanding"]


def _ebitda(snap: Optional[dict]) -> Tuple[Any, str]:
    """EBITDA line when Yahoo carries it, else operating income + D&A."""
    line = _f(snap, "ebitda")
    if num(line) is not None:
        return float(line), "EBITDA line"
    v, bad = _need(operating_income=_f(snap, "operating_income"),
                   d_and_a=_f(snap, "d_and_a"))
    if bad:
        reason = ("no EBITDA line and " + bad["reason"]
                  if is_dn(line) else bad["reason"])
        return dn(reason), "none"
    return v["operating_income"] + v["d_and_a"], "operating_income + d_and_a"


def _eps_for_pe(snap: Optional[dict]) -> Tuple[Any, str]:
    ttm = ((snap or {}).get("ttm") or {}).get("basic_eps")
    if num(ttm) is not None:
        return float(ttm), "TTM basic EPS (4 consecutive quarters)"
    rep = _f(snap, "basic_eps")
    if num(rep) is not None:
        return float(rep), "reported annual basic EPS"
    return rep, "none"


def _coe(snap: Optional[dict], market: Optional[dict]) -> Tuple[Any, str]:
    c = _pct(market, "coe_pct")
    if num(c) is not None:
        return c, "market coe_pct"
    k = capm(_pct(market, "rf_pct"), _mkt(market, "beta"), _pct(market, "erp_pct"))
    if num(k) is not None:
        return k, "CAPM rf + β × ERP"
    return dn("cost of equity needs coe_pct, or rf_pct + beta + erp_pct"), "none"


def _financial_layout(snap: Optional[dict]) -> bool:
    """True when the statements have NO EBIT and NO operating-income line —
    Yahoo's bank / NBFC / insurer layout, where interest expense includes
    deposits and 'debt' is the business, not leverage."""
    for name in ("ebit", "operating_income"):
        v = _f(snap, name)
        if not (is_dn(v) and "not in statements" in str(v.get("reason", ""))):
            return False
    return True


FINANCIAL_LAYOUT_REASON = ("financial-statement layout (no EBIT / operating-"
                           "income line — bank, NBFC or insurer): deposits "
                           "and borrowings are the business")


def _bvps(snap: Optional[dict]) -> Any:
    v, bad = _need(equity=_f(snap, "equity"),
                   shares_outstanding=_f(snap, "shares_outstanding"))
    if bad:
        return bad
    return _div(v["equity"], v["shares_outstanding"], "BVPS")


def _r1m(series: Any) -> Any:
    """1-month return from a close series, oldest first."""
    if series is None:
        return dn("series not given")
    vals = list(series.tolist() if hasattr(series, "tolist") else series)
    vals = [x for x in (num(v) for v in vals) if x is not None]
    n = int(ASSUMED["sessions_per_month"])
    if len(vals) <= n:
        return dn(f"series shorter than {n + 1} closes")
    if vals[-1 - n] <= 0:
        return dn("non-positive close in series")
    return vals[-1] / vals[-1 - n] - 1.0


# ------------------------------------------------------- plain formulas
def growth(cur: Any, prev: Any) -> Any:
    """cur / prev − 1; undefined on a non-positive base."""
    v, bad = _need(current=cur, previous=prev)
    if bad:
        return bad
    if v["previous"] <= 0:
        return dn("growth undefined on a non-positive base")
    return v["current"] / v["previous"] - 1.0


def ratio(a: Any, b: Any, a_name: str = "numerator", b_name: str = "denominator") -> Any:
    v, bad = _need(**{a_name: a, b_name: b})
    if bad:
        return bad
    return _div(v[a_name], v[b_name], f"{a_name}/{b_name}")


def avg_return(income: Any, closing: Any, opening: Any, what: str) -> Any:
    """income / average(closing, opening) — ROE, ROA."""
    v, bad = _need(income=income, **{f"{what}_t": closing, f"{what}_t-1": opening})
    if bad:
        return bad
    avg = (v[f"{what}_t"] + v[f"{what}_t-1"]) / 2.0
    if avg <= 0:
        return dn(f"average {what} is not positive")
    return v["income"] / avg


def roce(ebit: Any, equity: Any, debt: Any, cash: Any) -> Any:
    v, bad = _need(ebit=ebit, equity=equity, total_debt=debt, cash=cash)
    if bad:
        return bad
    ce = v["equity"] + v["total_debt"] - v["cash"]
    if ce <= 0:
        return dn("capital employed (equity + debt − cash) is not positive")
    return v["ebit"] / ce


def roic(ebit: Any, tax_rate: Any, invested_capital: Any) -> Any:
    v, bad = _need(ebit=ebit, tax_rate=tax_rate, invested_capital=invested_capital)
    if bad:
        return bad
    if v["invested_capital"] <= 0:
        return dn("invested capital is not positive")
    return v["ebit"] * (1.0 - v["tax_rate"]) / v["invested_capital"]


def trade_roi(entry: Any, exit_: Any, costs: Any) -> Any:
    """(exit − entry − costs) / entry — all three in the same unit."""
    v, bad = _need(entry=entry, exit=exit_, costs=costs)
    if bad:
        return bad
    if v["entry"] <= 0:
        return dn("entry is not positive")
    return (v["exit"] - v["entry"] - v["costs"]) / v["entry"]


def capm(rf: Any, beta: Any, erp: Any) -> Any:
    """rf + β × ERP (decimals)."""
    v, bad = _need(rf=rf, beta=beta, erp=erp)
    if bad:
        return bad
    return v["rf"] + v["beta"] * v["erp"]


def wacc(e: Any, d: Any, ke: Any, kd: Any, t: Any) -> Any:
    """E/V × Ke + D/V × Kd × (1 − t). With no debt, WACC = Ke."""
    v, bad = _need(equity_value=e, debt=d, ke=ke, tax_rate=t)
    if bad:
        return bad
    if v["debt"] == 0:
        return v["ke"]
    kdv, bad = _need(kd=kd)
    if bad:
        return bad
    V = v["equity_value"] + v["debt"]
    if V <= 0:
        return dn("E + D is not positive")
    return (v["equity_value"] / V * v["ke"]
            + v["debt"] / V * kdv["kd"] * (1.0 - v["tax_rate"]))


def npv(rate: Any, cash_flows: Any, outlay: Any) -> Any:
    """Σ CF_t / (1 + r)^t − outlay, t = 1..n."""
    v, bad = _need(rate=rate, outlay=outlay)
    if bad:
        return bad
    cfs = [num(x) for x in (cash_flows or [])]
    if not cfs:
        return dn("missing input: cash_flows")
    if any(x is None for x in cfs):
        return dn("cash_flows contains a missing value")
    if v["rate"] <= -1:
        return dn("rate must be above −100%")
    return sum(cf / (1.0 + v["rate"]) ** (t + 1) for t, cf in enumerate(cfs)) - v["outlay"]


def irr(cash_flows: Any, outlay: Any, lo: float = -0.99, hi: float = 10.0,
        tol: float = 1e-10, max_iter: int = 300) -> Any:
    """The r where NPV(r) = 0, by bisection on [lo, hi]."""
    v, bad = _need(outlay=outlay)
    if bad:
        return bad
    f_lo, f_hi = npv(lo, cash_flows, outlay), npv(hi, cash_flows, outlay)
    if is_dn(f_lo):
        return f_lo
    if is_dn(f_hi):
        return f_hi
    if f_lo == 0:
        return float(lo)
    if f_lo * f_hi > 0:
        return dn("no sign change of NPV on [−99%, 1000%] — IRR undefined")
    a, b = lo, hi
    for _ in range(max_iter):
        m = (a + b) / 2.0
        fm = npv(m, cash_flows, outlay)
        if abs(fm) < tol or (b - a) / 2.0 < tol:
            return float(m)
        if (fm > 0) == (f_lo > 0):
            a, f_lo = m, fm
        else:
            b = m
    return float((a + b) / 2.0)


def dcf_value(fcfs: Any, wacc_: Any, g: Any, cap_pct: Any = None) -> Any:
    """Σ FCF_t/(1+WACC)^t + TV/(1+WACC)^n, TV = FCF_n × (1+g)/(WACC−g).

    The terminal value is discounted to today like every other flow. g is
    capped at nominal GDP (ASSUMED cap) — above it the value is refused,
    never clamped silently."""
    v, bad = _need(wacc=wacc_, g=g)
    if bad:
        return bad
    flows = [num(x) for x in (fcfs or [])]
    if not flows:
        return dn("missing input: fcf_forecast (scenario FCF, stages 15-16)")
    if any(x is None for x in flows):
        return dn("fcf_forecast contains a missing value")
    cap = (float(cap_pct) if num(cap_pct) is not None
           else ASSUMED["dcf_growth_cap_pct"]) / 100.0
    if v["g"] > cap:
        return dn(f"g {v['g']:.2%} above the {cap:.0%} nominal-GDP cap")
    if v["wacc"] <= v["g"]:
        return dn("WACC must exceed g")
    w = v["wacc"]
    pv = sum(cf / (1.0 + w) ** (t + 1) for t, cf in enumerate(flows))
    n = len(flows)
    tv = flows[-1] * (1.0 + v["g"]) / (w - v["g"])
    return pv + tv / (1.0 + w) ** n


def justified_pb(roe_: Any, g: Any, coe: Any) -> Any:
    """(ROE − g) / (COE − g)."""
    v, bad = _need(roe=roe_, g=g, coe=coe)
    if bad:
        return bad
    if v["coe"] <= v["g"]:
        return dn("COE must exceed g")
    return (v["roe"] - v["g"]) / (v["coe"] - v["g"])


def ddm(dps: Any, g: Any, ke: Any) -> Any:
    """DPS × (1 + g) / (Ke − g)."""
    v, bad = _need(dps=dps, g=g, ke=ke)
    if bad:
        return bad
    if v["ke"] <= v["g"]:
        return dn("Ke must exceed g")
    return v["dps"] * (1.0 + v["g"]) / (v["ke"] - v["g"])


def graham_number(eps: Any, bvps: Any) -> Any:
    """√(22.5 × EPS × BVPS); undefined when EPS or BVPS ≤ 0."""
    v, bad = _need(eps=eps, bvps=bvps)
    if bad:
        return bad
    if v["eps"] <= 0 or v["bvps"] <= 0:
        return dn("Graham number undefined when EPS or BVPS ≤ 0")
    return math.sqrt(PUBLISHED["graham_multiplier"] * v["eps"] * v["bvps"])


def altman_z(a: Any, b: Any, c: Any, d: Any, e: Any) -> Any:
    v, bad = _need(A=a, B=b, C=c, D=d, E=e)
    if bad:
        return bad
    w = PUBLISHED["altman_weights"]
    return (w[0] * v["A"] + w[1] * v["B"] + w[2] * v["C"]
            + w[3] * v["D"] + w[4] * v["E"])


def peg(pe: Any, growth_pct: Any) -> Any:
    v, bad = _need(pe=pe, eps_growth_pct=growth_pct)
    if bad:
        return bad
    if v["eps_growth_pct"] <= 0:
        return dn("PEG undefined for non-positive EPS growth")
    if v["pe"] <= 0:
        return dn("PEG undefined for a non-positive P/E")
    return v["pe"] / v["eps_growth_pct"]


# ------------------------------------------------------------ term bodies
def _t_revenue_growth(snap, price, market):
    cur, prev = _f(snap, "revenue"), _prev(snap, "revenue")
    hist = _series(snap, "history", "revenue")
    yoy = {}
    for p in sorted(hist, reverse=True):
        q = _prior_period(p, list(hist))
        if q is not None and hist[q] > 0:
            yoy[p] = hist[p] / hist[q] - 1.0
    parts: Dict[str, Any] = {"yoy_by_year": yoy}
    qs = _series(snap, "quarterly", "revenue")
    if qs:
        last = max(qs)
        parts["run_rate_annualised"] = qs[last] * 4.0
        parts["run_rate_quarter"] = last
        same = [q for q in qs if 360 <= (_days(last, q) or 0) <= 370]
        if same and qs[same[0]] > 0:
            parts["quarter_yoy"] = qs[last] / qs[same[0]] - 1.0
    return {"value": growth(cur, prev), **parts}


def _t_ebitda_margin(snap, price, market):
    e, src = _ebitda(snap)
    return {"value": ratio(e, _f(snap, "revenue"), "ebitda", "revenue"),
            "ebitda": e, "ebitda_source": src}


def _t_pat_margin(snap, price, market):
    e, src = _ebitda(snap)
    return {"value": ratio(_f(snap, "net_income"), _f(snap, "revenue"),
                           "net_income", "revenue"),
            "conversion_pat_over_ebitda": ratio(_f(snap, "net_income"), e,
                                                "net_income", "ebitda"),
            "ebitda_source": src}


def _t_tax_rate(snap, price, market):
    v, bad = _need(tax=_f(snap, "tax"), pretax_income=_f(snap, "pretax_income"))
    if bad:
        val = bad
    elif v["pretax_income"] <= 0:
        val = dn("effective tax rate undefined on a pre-tax loss")
    else:
        val = v["tax"] / v["pretax_income"]
    return {"value": val, "yahoo_tax_rate_for_calcs": _f(snap, "tax_rate")}


def _t_normalised_pat(snap, price, market):
    norm = _f(snap, "normalized_income_yahoo")
    rep = _f(snap, "net_income")
    gap = (num(norm) - num(rep)) if (num(norm) is not None and num(rep) is not None) \
        else dn("needs both reported and normalised PAT")
    return {"value": norm, "reported_pat": rep, "gap": gap,
            "unusual_items": _f(snap, "unusual_items"),
            "basis": "Yahoo 'Normalized Income'; the adjustments table "
                     "with reasons is normalise.py's"}


def _t_basic_eps(snap, price, market):
    comp = ratio(_f(snap, "net_income_common"), _f(snap, "basic_shares_avg"),
                 "net_income_common", "basic_shares_avg")
    rep = _f(snap, "basic_eps")
    gap = (num(comp) / num(rep) - 1.0) if (num(comp) is not None and num(rep))\
        else dn("needs computed and reported EPS")
    return {"value": comp, "reported_basic_eps": rep, "gap_vs_reported": gap}


def _t_diluted_eps(snap, price, market):
    comp = ratio(_f(snap, "net_income_common"), _f(snap, "diluted_shares_avg"),
                 "net_income_common", "diluted_shares_avg")
    rep = _f(snap, "diluted_eps")
    gap = (num(comp) / num(rep) - 1.0) if (num(comp) is not None and num(rep))\
        else dn("needs computed and reported diluted EPS")
    return {"value": comp, "reported_diluted_eps": rep, "gap_vs_reported": gap,
            "basis": "Yahoo's diluted average shares; ESOP/convertible "
                     "detail itself not held"}


def _t_bvps(snap, price, market):
    return _bvps(snap)


def _t_roe(snap, price, market):
    return {"value": avg_return(_f(snap, "net_income"), _f(snap, "equity"),
                                _prev(snap, "equity"), "equity"),
            "roe_on_closing_equity": ratio(_f(snap, "net_income"),
                                           _f(snap, "equity"),
                                           "net_income", "equity")}


def _t_roa(snap, price, market):
    return avg_return(_f(snap, "net_income"), _f(snap, "total_assets"),
                      _prev(snap, "total_assets"), "total_assets")


def _t_roce(snap, price, market):
    return roce(_f(snap, "ebit"), _f(snap, "equity"), _f(snap, "total_debt"),
                _f(snap, "cash"))


def _t_roic(snap, price, market):
    ic = _f(snap, "invested_capital")
    src = "Invested Capital line"
    if num(ic) is None:
        v, bad = _need(equity=_f(snap, "equity"), total_debt=_f(snap, "total_debt"),
                       cash=_f(snap, "cash"))
        if bad:
            ic = dn("no Invested Capital line and " + bad["reason"])
            src = "none"
        else:
            ic = v["equity"] + v["total_debt"] - v["cash"]
            src = "equity + debt − cash (fallback)"
    return {"value": roic(_f(snap, "ebit"), _f(snap, "tax_rate"), ic),
            "invested_capital_source": src}


def _t_trade_roi(snap, price, market):
    t = (market or {}).get("trade")
    if not isinstance(t, dict):
        return dn("needs a trade {entry, exit, costs} (paper book)")
    return trade_roi(t.get("entry"), t.get("exit"), t.get("costs"))


def _t_capm(snap, price, market):
    return capm(_pct(market, "rf_pct"), _mkt(market, "beta"), _pct(market, "erp_pct"))


def _wacc_parts(snap, price, market):
    if _financial_layout(snap):
        return dn(FINANCIAL_LAYOUT_REASON + "; interest expense includes "
                  "deposits, so WACC is not meaningful")
    ke, ke_src = _coe(snap, market)
    kd = _pct(market, "kd_pct")
    kd_src = "market kd_pct"
    if num(kd) is None:
        kd = ratio(_f(snap, "interest_expense"), _f(snap, "total_debt"),
                   "interest_expense", "total_debt")
        kd_src = "interest_expense / total_debt"
    e = _mcap(snap, price)
    val = wacc(e, _f(snap, "total_debt"), ke, kd, _f(snap, "tax_rate"))
    return {"value": val, "ke": ke, "ke_source": ke_src, "kd": kd,
            "kd_source": kd_src, "equity_value": e,
            "note": "banks: interest expense includes deposits — not a WACC input"}


def _t_npv(snap, price, market):
    m = market or {}
    outlay = m.get("outlay", price)
    return npv(_pct(market, "rate_pct"), m.get("cash_flows"),
               num(outlay) if outlay is not None else dn("outlay (or price) not given"))


def _t_irr(snap, price, market):
    m = market or {}
    outlay = m.get("outlay", price)
    if not m.get("cash_flows"):
        return dn("missing input: cash_flows (scenarios, stage 16)")
    return irr(m.get("cash_flows"),
               num(outlay) if outlay is not None else dn("outlay (or price) not given"))


def _t_dcf(snap, price, market):
    w = _pct(market, "wacc_pct")
    w_src = "market wacc_pct"
    if num(w) is None:
        wp = _wacc_parts(snap, price, market)
        w = wp if is_dn(wp) else wp["value"]
        w_src = "WACC term"
    g = _pct(market, "growth_pct")
    cap_pct = (market or {}).get("dcf_growth_cap_pct")       # Den draft, else default
    val = dcf_value((market or {}).get("fcf_forecast"), w, g, cap_pct=cap_pct)
    parts: Dict[str, Any] = {"value": val, "wacc": w, "wacc_source": w_src,
                             "g": g, "g_cap": (float(cap_pct) if num(cap_pct) is not None
                                               else ASSUMED["dcf_growth_cap_pct"]) / 100.0}
    if num(val) is not None:
        v, bad = _need(total_debt=_f(snap, "total_debt"), cash=_f(snap, "cash"),
                       minority_interest_bs=_f(snap, "minority_interest_bs"),
                       shares_outstanding=_f(snap, "shares_outstanding"))
        if bad:
            parts["equity_value"] = bad
            parts["per_share"] = bad
        else:
            eqv = val - v["total_debt"] + v["cash"] - v["minority_interest_bs"]
            parts["equity_value"] = eqv
            parts["per_share"] = _div(eqv, v["shares_outstanding"], "per share")
    return parts


def _t_fcf_yield(snap, price, market):
    fcf = _f(snap, "fcf")
    chk = dn("needs ocf and capex")
    v, bad = _need(ocf=_f(snap, "ocf"), capex=_f(snap, "capex"))
    if not bad:
        chk = v["ocf"] + v["capex"]          # capex is negative as reported
    return {"value": ratio(fcf, _mcap(snap, price), "fcf", "market_cap"),
            "fcf": fcf, "ocf_plus_capex": chk}


def _t_cash_conversion(snap, price, market):
    ocf_h = _series(snap, "history", "ocf")
    ni_h = _series(snap, "history", "net_income")
    by_year = {p: ocf_h[p] / ni_h[p] for p in sorted(ocf_h, reverse=True)
               if p in ni_h and ni_h[p] > 0}
    last3 = list(by_year.values())[:3]
    flag: Any = (all(x < ASSUMED["cash_conversion_flag"] for x in last3)
                 if len(last3) == 3 else dn("needs 3 years of OCF and PAT"))
    return {"value": ratio(_f(snap, "ocf"), _f(snap, "net_income"),
                           "ocf", "net_income"),
            "by_year": by_year, "flag_below_0_7_three_years": flag}


def _t_net_debt_ebitda(snap, price, market):
    e, src = _ebitda(snap)
    v, bad = _need(total_debt=_f(snap, "total_debt"), cash=_f(snap, "cash"),
                   ebitda=e)
    if bad:
        return {"value": bad, "ebitda_source": src}
    if v["ebitda"] <= 0:
        return {"value": dn("net debt / EBITDA undefined on non-positive EBITDA"),
                "ebitda_source": src}
    val = (v["total_debt"] - v["cash"]) / v["ebitda"]
    return {"value": val, "ebitda_source": src,
            "flag_above_3": val > ASSUMED["net_debt_ebitda_flag"]}


def _t_debt_equity(snap, price, market):
    if _financial_layout(snap):
        return dn(FINANCIAL_LAYOUT_REASON + "; debt/equity blank for banks "
                  "(task book)")
    return {"value": ratio(_f(snap, "total_debt"), _f(snap, "equity"),
                           "total_debt", "equity"),
            "note": "blank for banks in the task book — deposits are not debt"}


def _t_interest_cover(snap, price, market):
    v, bad = _need(ebit=_f(snap, "ebit"), interest_expense=_f(snap, "interest_expense"))
    if bad:
        return bad
    if v["interest_expense"] == 0:
        return dn("no interest expense — coverage unbounded")
    val = v["ebit"] / v["interest_expense"]
    return {"value": val, "flag_below_2": val < ASSUMED["interest_cover_flag"]}


def _t_wc_days(snap, price, market):
    v, bad = _need(receivables=_f(snap, "receivables"), inventory=_f(snap, "inventory"),
                   payables=_f(snap, "payables"), revenue=_f(snap, "revenue"))
    if bad:
        return bad
    return _div((v["receivables"] + v["inventory"] - v["payables"]) * DAYS_PER_YEAR,
                v["revenue"], "working-capital days")


def _t_receivable_days(snap, price, market):
    v, bad = _need(receivables=_f(snap, "receivables"), revenue=_f(snap, "revenue"))
    if bad:
        return bad
    return _div(v["receivables"] * DAYS_PER_YEAR, v["revenue"], "receivable days")


def _t_asset_turnover(snap, price, market):
    return {"value": ratio(_f(snap, "revenue"), _f(snap, "total_assets"),
                           "revenue", "total_assets"),
            "basis": "closing total assets"}


def _pe(snap, price):
    eps, basis = _eps_for_pe(snap)
    v, bad = _need(price=_price(price), eps=eps)
    if bad:
        return bad, basis
    if v["eps"] <= 0:
        return dn("P/E undefined on a loss (EPS ≤ 0)"), basis
    return v["price"] / v["eps"], basis


def _t_pe(snap, price, market):
    pe, basis = _pe(snap, price)
    fwd = ratio(_price(price), _mkt(market, "forward_eps"), "price", "forward_eps")
    if num(fwd) is not None and fwd <= 0:
        fwd = dn("forward P/E undefined on a forward loss")
    rep = _f(snap, "basic_eps")
    pe_rep = (num(price) / num(rep)) if (num(price) and num(rep) and num(rep) > 0) \
        else dn("needs price and a positive reported EPS")
    return {"value": pe, "eps_basis": basis, "forward_pe": fwd,
            "pe_on_reported_annual_eps": pe_rep}


def _t_peg(snap, price, market):
    pe, basis = _pe(snap, price)
    g = _mkt(market, "eps_growth_pct")
    g_src = "market eps_growth_pct"
    if num(g) is None:
        gr = growth(_f(snap, "basic_eps"), _prev(snap, "basic_eps"))
        g = gr * 100.0 if num(gr) is not None else gr
        g_src = "reported basic EPS, latest FY vs prior FY"
    return {"value": peg(pe, g), "eps_growth_pct": g, "growth_source": g_src,
            "eps_basis": basis}


def _t_earnings_yield(snap, price, market):
    eps, basis = _eps_for_pe(snap)
    return {"value": ratio(eps, _price(price), "eps", "price"), "eps_basis": basis}


def _t_pb(snap, price, market):
    bvps = _bvps(snap)
    roe_in = _pct(market, "roe_pct")
    roe_src = "market roe_pct"
    if num(roe_in) is None:
        roe_in = _t_roe(snap, price, market)["value"]
        roe_src = "ROE term (average equity)"
    coe, coe_src = _coe(snap, market)
    return {"value": ratio(_price(price), bvps, "price", "bvps"),
            "bvps": bvps,
            "justified_pb": justified_pb(roe_in, _pct(market, "growth_pct"), coe),
            "roe": roe_in, "roe_source": roe_src, "coe": coe, "coe_source": coe_src}


def _sti(snap) -> float:
    """Short-term investments (Yahoo 'Other Short Term Investments', the
    extras line pipeline._sti reads); absent = 0, the pipeline's convention."""
    v = _extra(snap, "short_term_investments")
    return float(v) if num(v) is not None else 0.0


def _ev(snap, price):
    """EV = market cap + debt − cash − short-term investments + minorities:
    the ONE EV rule (VAL-12a, owner 2026-10-21) the peer table, the stock's
    EV bridge and these terms all use — reviewer defect 2 (2026-10-21) found
    the terms still on the old mcap + debt − cash + minorities."""
    mc = _mcap(snap, price)
    v, bad = _need(market_cap=mc, total_debt=_f(snap, "total_debt"),
                   cash=_f(snap, "cash"))
    if bad:
        return bad
    core = v["market_cap"] + v["total_debt"] - v["cash"] - _sti(snap)
    mi = _f(snap, "minority_interest_bs")
    if num(mi) is None:
        return dn("missing input: minority_interest_bs"
                  + (f" ({mi['reason']})" if is_dn(mi) else ""),
                  ev_without_minorities=core)
    return core + float(mi)


def _t_ps(snap, price, market):
    ev = _ev(snap, price)
    return {"value": ratio(_mcap(snap, price), _f(snap, "revenue"),
                           "market_cap", "revenue"),
            "ev_sales": ratio(ev, _f(snap, "revenue"), "ev", "revenue")}


def _t_ev(snap, price, market):
    return {"value": _ev(snap, price), "short_term_investments": _sti(snap),
            "rule": "mcap + debt − cash − short-term investments + minorities (VAL-12a)"}


def _t_ev_ebitda(snap, price, market):
    e, src = _ebitda(snap)
    v, bad = _need(ev=_ev(snap, price), ebitda=e)
    if bad:
        return {"value": bad, "ebitda_source": src}
    if v["ebitda"] <= 0:
        return {"value": dn("EV/EBITDA undefined on non-positive EBITDA"),
                "ebitda_source": src}
    return {"value": v["ev"] / v["ebitda"], "ebitda_source": src}


def _dps(snap) -> Any:
    v, bad = _need(dividends_paid=_f(snap, "dividends_paid"),
                   shares_outstanding=_f(snap, "shares_outstanding"))
    if bad:
        return bad
    return _div(abs(v["dividends_paid"]), v["shares_outstanding"], "DPS")


def _t_dividend(snap, price, market):
    dps = _dps(snap)
    payout = dn("needs dividends_paid and a positive net income")
    v, bad = _need(dividends_paid=_f(snap, "dividends_paid"),
                   net_income=_f(snap, "net_income"))
    if not bad and v["net_income"] > 0:
        payout = abs(v["dividends_paid"]) / v["net_income"]
    return {"value": ratio(dps, _price(price), "dps", "price"), "dps": dps,
            "payout": payout,
            "basis": "cash dividends PAID in the year (timing differs from declared DPS)"}


def _t_ddm(snap, price, market):
    dps = _mkt(market, "dps")
    src = "market dps"
    if num(dps) is None:
        dps = _dps(snap)
        src = "|dividends_paid| / shares"
    ke, ke_src = _coe(snap, market)
    return {"value": ddm(dps, _pct(market, "growth_pct"), ke), "dps": dps,
            "dps_source": src, "ke": ke, "ke_source": ke_src}


def _t_graham(snap, price, market):
    return {"value": graham_number(_f(snap, "basic_eps"), _bvps(snap)),
            "eps_basis": "reported annual basic EPS"}


def _t_altman(snap, price, market):
    v, bad = _need(total_assets=_f(snap, "total_assets"))
    if bad:
        return bad
    ta = v["total_assets"]
    if ta <= 0:
        return dn("total assets not positive")
    a = ratio(_f(snap, "working_capital"), ta, "working_capital", "total_assets")
    re_ = _extra(snap, "retained_earnings")
    b_src = "Retained Earnings line"
    if num(re_) is None:
        p, bad2 = _need(equity=_f(snap, "equity"),
                        capital_stock=_extra(snap, "capital_stock"))
        if bad2:
            re_ = dn("no retained-earnings line and no proxy: " + bad2["reason"])
            b_src = "none"
        else:
            re_ = p["equity"] - p["capital_stock"]
            b_src = "PROXY equity − capital stock (no retained-earnings line)"
    b = ratio(re_, ta, "retained_earnings", "total_assets")
    c = ratio(_f(snap, "ebit"), ta, "ebit", "total_assets")
    liab = dn("needs equity")
    eq = _f(snap, "equity")
    if num(eq) is not None:
        liab = ta - float(eq)
        if liab <= 0:
            liab = dn("total liabilities (assets − equity) not positive")
    d = ratio(_mcap(snap, price), liab, "market_cap", "total_liabilities")
    e = ratio(_f(snap, "revenue"), ta, "revenue", "total_assets")
    return {"value": altman_z(a, b, c, d, e), "A": a, "B": b, "C": c, "D": d,
            "E": e, "B_source": b_src,
            "basis": "liabilities = total assets − equity; zones <1.8 distress, >3 safe"}


def _t_piotroski(snap, price, market):
    period = (snap or {}).get("period_end")
    legs: Dict[str, Any] = {}

    def h(name):
        return _series(snap, "history", name)

    def pair(name):
        s = h(name)
        if period not in s:
            return None
        p = _prior_period(period, list(s))
        return (s[period], s[p]) if p else None

    ni, ta, cfo = pair("net_income"), pair("total_assets"), h("ocf").get(period)
    debt, ca, cl = pair("total_debt"), pair("current_assets"), pair("current_liabilities")
    sh, gp, rev = pair("shares_outstanding"), pair("gross_profit"), pair("revenue")
    if ni and ta and ta[0] > 0 and ta[1] > 0:
        legs["roa_positive"] = ni[0] / ta[0] > 0
        legs["roa_rising"] = ni[0] / ta[0] > ni[1] / ta[1]
    if cfo is not None:
        legs["cfo_positive"] = cfo > 0
        if ni:
            legs["cfo_above_pat"] = cfo > ni[0]
    if debt and ta and ta[0] > 0 and ta[1] > 0:
        legs["leverage_falling"] = debt[0] / ta[0] < debt[1] / ta[1]
    if ca and cl and cl[0] > 0 and cl[1] > 0:
        legs["current_ratio_rising"] = ca[0] / cl[0] > ca[1] / cl[1]
    if sh:
        legs["no_new_shares"] = sh[0] <= sh[1]
    if gp and rev and rev[0] > 0 and rev[1] > 0:
        legs["gross_margin_rising"] = gp[0] / rev[0] > gp[1] / rev[1]
    if rev and ta and ta[0] > 0 and ta[1] > 0:
        legs["asset_turnover_rising"] = rev[0] / ta[0] > rev[1] / ta[1]
    want = ["roa_positive", "roa_rising", "cfo_positive", "cfo_above_pat",
            "leverage_falling", "current_ratio_rising", "no_new_shares",
            "gross_margin_rising", "asset_turnover_rising"]
    missing = [k for k in want if k not in legs]
    score = float(sum(1 for v in legs.values() if v))
    if missing:
        return {"value": dn("Piotroski F needs all 9 tests over two years; "
                            "missing: " + ", ".join(missing),
                            partial=score, of=len(legs)),
                "legs": legs}
    return {"value": score, "legs": legs,
            "basis": "ROA on closing assets; leverage = total debt / assets"}


def _t_beta(snap, price, market):
    return _mkt(market, "beta")


def _t_sector_rs(snap, price, market):
    m = market or {}
    s = _pct(m, "stock_1m_pct")
    if num(s) is None and m.get("stock_series") is not None:
        s = _r1m(m.get("stock_series"))
    sec = _pct(m, "sector_1m_pct")
    if num(sec) is None and m.get("sector_series") is not None:
        sec = _r1m(m.get("sector_series"))
    mk = _pct(m, "market_1m_pct")
    if num(mk) is None and m.get("index_series") is not None:
        mk = _r1m(m.get("index_series"))
    vs_sec = dn("needs stock and sector 1M (series or *_1m_pct)")
    vs_mkt = dn("needs stock and market 1M (series or *_1m_pct)")
    if num(s) is not None and num(sec) is not None:
        vs_sec = s - sec
    if num(s) is not None and num(mk) is not None:
        vs_mkt = s - mk
    return {"value": vs_sec, "stock_minus_market_1m": vs_mkt,
            "stock_1m": s, "sector_1m": sec, "market_1m": mk}


def _t_inst_change(snap, price, market):
    v, bad = _need(inst_pct_now=_mkt(market, "inst_pct_now"),
                   inst_pct_prev=_mkt(market, "inst_pct_prev"))
    if bad:
        return bad
    return v["inst_pct_now"] - v["inst_pct_prev"]


def _t_promoter(snap, price, market):
    return {"value": _mkt(market, "promoter_pct"),
            "pledge_pct": _mkt(market, "pledge_pct")}


def _blocked(reason: str) -> Callable:
    def fn(snap, price, market):
        return dn(reason)
    return fn


# ----------------------------------------------------------------- registry
def _term(formula: str, stage: str, note: str, fn: Optional[Callable]) -> dict:
    return {"formula": formula, "stage": stage, "note": note, "fn": fn}


TERMS: Dict[str, dict] = {
    "Revenue growth": _term("Rev_t / Rev_t-1 − 1", "15",
                            "YoY and run-rate", _t_revenue_growth),
    "EBITDA / margin": _term("EBITDA (line, else op income + D&A); EBITDA / revenue",
                             "11, 15", "Base margin before drivers", _t_ebitda_margin),
    "PAT margin / conversion": _term("PAT / revenue; PAT / EBITDA", "11, 16",
                                     "Conversion = after D&A, interest, tax",
                                     _t_pat_margin),
    "Effective tax rate": _term("Tax / PBT", "16", "Policy sensitivity", _t_tax_rate),
    "Normalised PAT": _term("Reported PAT ± exceptional ± tax one-offs", "11",
                            "Yahoo Normalized Income read here; the adjustment "
                            "table with reasons is normalise.py's",
                            _t_normalised_pat),
    "Basic EPS": _term("PAT to equity holders / weighted avg shares", "16",
                       "computed beside the reported figure, gap shown",
                       _t_basic_eps),
    "Diluted EPS": _term("PAT / (weighted shares + dilutive instruments)", "16",
                         "Blocks if unreconciled", _t_diluted_eps),
    "Book value per share (BVPS)": _term("Equity / shares", "16 (banks, realty)",
                                         "", _t_bvps),
    "ROE": _term("PAT / avg equity", "12, 17", "Bank route core", _t_roe),
    "ROA": _term("PAT / avg total assets", "12", "Banks/NBFC", _t_roa),
    "ROCE": _term("EBIT / (equity + debt − cash)", "12",
                  "Capital-intensive sectors", _t_roce),
    "ROIC": _term("NOPAT / invested capital = EBIT × (1 − t) / IC", "12, 17",
                  "vs WACC = value creation", _t_roic),
    "ROI (of a trade)": _term("(exit − entry − costs) / entry", "FF",
                              "Per-trade record", _t_trade_roi),
    "Cost of equity (CAPM)": _term("Rf + β × ERP", "17", "Rf needs the live 10y",
                                   _t_capm),
    "WACC": _term("E/V×Ke + D/V×Kd×(1−t)", "17", "DCF discount rate",
                  _wacc_parts),
    "NPV": _term("Σ CF_t/(1+r)^t − outlay", "17", "Scenario value", _t_npv),
    "IRR": _term("r where NPV = 0", "18", "Expected return at today's price",
                 _t_irr),
    "DCF value": _term("Σ FCF_t/(1+WACC)^t + TV; TV = FCF×(1+g)/(WACC−g)", "17",
                       "g ≤ nominal GDP (10% cap ASSUMED); TV discounted",
                       _t_dcf),
    "FCF / FCF yield": _term("OCF − capex; FCF / market cap", "12, 17", "",
                             _t_fcf_yield),
    "Cash conversion": _term("OCF / PAT", "12", "<0.7 three years = flag",
                             _t_cash_conversion),
    "Net debt / EBITDA": _term("(debt − cash) / EBITDA", "12", ">3 = stress",
                               _t_net_debt_ebitda),
    "Debt / equity": _term("Total debt / equity", "12", "Blank for banks",
                           _t_debt_equity),
    "Interest coverage": _term("EBIT / interest", "12", "<2 = flag",
                               _t_interest_cover),
    "Working-capital days": _term(
        "(receivables + inventory − payables) / revenue × 365", "12", "",
        _t_wc_days),
    "Receivable days": _term("Receivables / revenue × 365", "12", "Quality",
                             _t_receivable_days),
    "Asset turnover": _term("Revenue / assets", "12", "", _t_asset_turnover),
    "P/E (trailing, forward)": _term("Price / EPS", "17, 18",
                                     "TTM EPS when 4 consecutive quarters "
                                     "exist, else reported annual", _t_pe),
    "PEG": _term("P/E / EPS growth %", "17", "", _t_peg),
    "Earnings yield": _term("EPS / price", "18", "vs 10y yield",
                            _t_earnings_yield),
    "P/B": _term("Price / BVPS", "17",
                 "Banks: justified P/B = (ROE−g)/(COE−g)", _t_pb),
    "P/S, EV/Sales": _term("Mcap / revenue; EV / revenue", "17",
                           "Loss-making / early", _t_ps),
    "EV": _term("Mcap + debt − cash − short-term investments + minorities", "17",
                "EV→equity bridge (one EV rule, VAL-12a)", _t_ev),
    "EV/EBITDA": _term("EV / EBITDA", "17", "Mid-cycle for cyclicals",
                       _t_ev_ebitda),
    "Dividend yield / payout": _term("DPS / price; DPS / EPS", "17", "PSU floor",
                                     _t_dividend),
    "Dividend discount model": _term("DPS×(1+g)/(Ke−g)", "17", "Utilities, PSU",
                                     _t_ddm),
    "Graham number": _term("√(22.5 × EPS × BVPS)", "17", "Sanity floor",
                           _t_graham),
    "Altman Z": _term("1.2A+1.4B+3.3C+0.6D+1.0E", "12",
                      "A WC/TA · B RE/TA · C EBIT/TA · D Mcap/TL · E Sales/TA",
                      _t_altman),
    "Piotroski F": _term("9 binary tests", "12", "needs two fiscal years",
                         _t_piotroski),
    "Beta": _term("cov(r_s, r_m)/var(r_m)", "13, 17",
                  "Resilience screen uses <0.8 / >1.1; computed in "
                  "trading.valuation_context.beta — passed in here", _t_beta),
    "Sharpe-like momentum": _term("r6/σ6, r12/σ12 avg", "timing (03)",
                                  "Today's base; becomes context",
                                  _blocked("lives in trading.strategies "
                                           "(the live momentum score)")),
    "NIM / CASA / GNPA / NNPA / PCR / CAR / credit cost": _term(
        "Bank ratios", "12, 17 (banks)", "From results PDFs / filings",
        _blocked("bank ratios not in statements; results PDFs / NSE filings")),
    "AUM / spread / ALM": _term(
        "NBFC", "12, 17", "",
        _blocked("NBFC ratios not in statements; results / NSE filings")),
    "Embedded value / VNB": _term(
        "Insurers", "17", "",
        _blocked("insurer disclosures not in statements; results / NSE filings")),
    "NAV per share": _term(
        "Realty: Σ project cash flows discounted + land − net debt",
        "16-17 (realty)", "", _blocked("project cash flows not held")),
    "Order book / book-to-bill": _term(
        "Order book / TTM revenue; inflow / revenue", "13, 15 (capex)",
        "From results", _blocked("from results; news order_win only")),
    "ARPU / SSSG / occupancy / EBITDA per room": _term(
        "Sector operating KPIs", "13, 15", "",
        _blocked("sector operating KPIs not held; results / investor "
                 "presentations")),
    "Sector relative strength": _term("Stock 1M − sector 1M; − market 1M",
                                      "13, timing", "Resilience screen X/Y",
                                      _t_sector_rs),
    "Institutional change (pp)": _term("FII+DII % QoQ", "13",
                                       "Resilience screen P", _t_inst_change),
    "Delivery %": _term("Delivered / traded", "13", "Needs Colab refresh",
                        _blocked("frozen 2026-08-07, laid off")),
    "Promoter holding / pledge": _term("%", "13", "Resilience screen N",
                                       _t_promoter),
    "Impact cost / capacity": _term("Square-root law (lens)", "20",
                                    "Execution limit",
                                    _blocked("lives in trading.lens_v2")),
    "Kelly fraction (measured)": _term("p − (1−p)/b from closed trades", "20",
                                       "Momentum only today",
                                       _blocked("lives in trading.measured_kelly")),
}
assert len(TERMS) == 52, f"task book lists 52 terms, registry has {len(TERMS)}"


# ------------------------------------------------------------------- API
def _clean(v: Any) -> Any:
    if is_dn(v):
        return v
    if isinstance(v, bool):
        return v
    f = num(v)
    if f is not None:
        return f
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if v is None or isinstance(v, str):
        return v
    return dn(f"non-numeric result {type(v).__name__}")


def detail(term: str, snap: Optional[dict], *, price: Any = None,
           market: Optional[dict] = None) -> dict:
    """{term, value, parts, formula, stage, note} — never raises."""
    entry = TERMS.get(term)
    if entry is None:
        return {"term": term, "value": dn(f"unknown term {term!r}"), "parts": {}}
    try:
        out = entry["fn"](snap or {}, price, market or {})
    except Exception as e:                                     # noqa: BLE001
        log.warning("valuation.terms %s failed: %s", term, e)
        out = dn(f"could not compute: {type(e).__name__}: {e}")
    if isinstance(out, dict) and not is_dn(out) and "value" in out:
        value = out["value"]
        parts = {k: v for k, v in out.items() if k != "value"}
    else:
        value, parts = out, {}
    value = _clean(value)
    if not is_dn(value) and num(value) is None:
        value = dn("result is not a finite number")
    return {"term": term, "value": value, "parts": _clean(parts),
            "formula": entry["formula"], "stage": entry["stage"],
            "note": entry["note"]}


def compute(term: str, snap: Optional[dict], *, price: Any = None,
            market: Optional[dict] = None) -> Any:
    """The term's primary value: a float, or DATA_NEEDED. Never raises."""
    return detail(term, snap, price=price, market=market)["value"]


def compute_all(snap: Optional[dict], price: Any = None,
                market: Optional[dict] = None) -> Dict[str, Any]:
    return {t: compute(t, snap, price=price, market=market) for t in TERMS}


def detail_all(snap: Optional[dict], price: Any = None,
               market: Optional[dict] = None) -> Dict[str, dict]:
    return {t: detail(t, snap, price=price, market=market) for t in TERMS}


__all__ = ["TERMS", "compute", "compute_all", "detail", "detail_all",
           "ASSUMED", "ASSUMED_SOURCE", "PUBLISHED", "VERSION",
           "growth", "ratio", "avg_return", "roce", "roic", "trade_roi",
           "capm", "wacc", "npv", "irr", "dcf_value", "justified_pb", "ddm",
           "graham_number", "altman_z", "peg"]
