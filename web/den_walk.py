"""Den §1 — the selection sequence as a WALK TREE (owner order 2026-10-21:
"walk tree + stock picker + owner branches").

Each of the 26 stages (00..24, 17b) opens into the formula branches the code
really runs, built from the code's own registries — the route methods
(stage 09), the share bridge (10), normalised earnings (11), the quality
lines (12), the market / rates / holding inputs (13), the scenario chain's
cells grouped (15/16), the range rule per family with the band and the own
history (17), the context legs (17b), the verdict rules (18) — plus the 52
finance terms, each under the stage its registry names. With ?walk=TCS the
tree carries THAT stock's number on every node, or DATA_NEEDED with the
reason; the one stock is computed by web.valuation_chain_view.walk_record
under §12's guards (time budget, rotation lock, memory ceiling, kept rows).

Owner branches: settings doc `sequence_branches` = {branches: [{id, parent,
name, formula, note, ts}]}, at most MAX_BRANCHES; the parent is any node id
(code ids are stable: "s15", "s15.revenue_growth", …) or another owner
branch. They print tagged "your branch · not yet computed" — nothing
computes them. Display only: no gate, stop or size reads any of this.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger(__name__)

BRANCHES_DOC_ID = "sequence_branches"
MAX_BRANCHES = 500
MAX_NAME = 80
MAX_TEXT = 300
#: In-process copy (the Mongo doc is the durable one; the Den works while
#: Mongo is offline — the formula_drafts pattern).
LAST_BRANCHES: list = []
#: Stages opened by default when a stock is walked (the chain stages).
_OPEN_WITH_STOCK = {"s07", "s09", "s11", "s12", "s13", "s15", "s16", "s17", "s18"}
_ID_RE = re.compile(r"^[a-z0-9_.\-]{1,120}$")


# ------------------------------------------------------------ formatting

def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _f2(v: Any) -> str:
    f = _num(v)
    if f is None:
        return ""
    return f"{f:,.2f}"


def _inr(v: Any) -> str:
    f = _num(v)
    return "" if f is None else f"₹{f:,.2f}"


def _cr(v: Any) -> str:
    """Rupees → ₹ crore."""
    f = _num(v)
    return "" if f is None else f"₹{f / 1e7:,.0f} cr"


def _pct(v: Any, digits: int = 2) -> str:
    f = _num(v)
    return "" if f is None else f"{f:.{digits}f}%"


def _slug(s: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")
    return out[:60] or "x"


def _node(nid: str, name: str, formula: str = "", *, info: bool = False,
          children: Optional[list] = None, kind: str = "code") -> dict:
    """info=True: a rule / structure node with no per-stock number."""
    return {"id": nid, "name": name, "formula": formula, "value": "", "why": "",
            "info": info, "kind": kind, "children": list(children or []),
            "note": "", "ts": "", "open": False, "current": False}


def _set(n: dict, text: str, why: str, stock: bool) -> dict:
    """The stock's number on a node, or DATA_NEEDED + reason (stock walked)."""
    if not stock or n["info"]:
        return n
    if text:
        n["value"] = text
    else:
        n["why"] = why or "not computed for this stock"
    return n


class _Ids:
    """Unique node ids within one tree."""

    def __init__(self) -> None:
        self.seen: set = set()

    def __call__(self, nid: str) -> str:
        base, i = nid, 2
        while nid in self.seen:
            nid = f"{base}_{i}"
            i += 1
        self.seen.add(nid)
        return nid


# ---------------------------------------------------------- the stages

def _stage_code_of(term_stage: str) -> str:
    """A finance term's registry stage ('12, 17', 'timing (03)', 'FF') →
    the stage node it hangs under."""
    s = str(term_stage or "")
    m = re.search(r"\((\d{2})\)", s)
    if m:
        return m.group(1)
    m = re.match(r"\s*(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):02d}"
    return "22"                                         # 'FF' — measured on paper trades


def _terms_nodes(rec: Optional[dict], ids: _Ids, stock: bool) -> dict:
    """{stage code: [term nodes]} — the 52 finance terms by their stage."""
    from trading.valuation import terms as T
    rows = {r.get("name"): r for r in ((rec or {}).get("terms_rows") or [])}
    out: dict = {}
    for name, e in T.TERMS.items():
        code = _stage_code_of(e.get("stage") or "")
        n = _node(ids(f"s{code}.{_slug(name)}"), name,
                  f"{e.get('formula') or ''}  [terms registry stage {e.get('stage') or '—'}]")
        r = rows.get(name) or {}
        _set(n, r.get("text") or "" if r.get("value") is not None else "",
             r.get("why") or ("" if r else "term not computed for this stock"), stock)
        if stock and isinstance(r.get("parts"), dict):
            for k, v in list(r["parts"].items())[:12]:
                c = _node(ids(f"{n['id']}.{_slug(k)}"), str(k), "")
                if isinstance(v, dict):
                    c["value"] = " · ".join(f"{a}: {_short(b)}" for a, b in list(v.items())[:8])
                else:
                    c["value"] = _short(v) or "—"
                n["children"].append(c)
        out.setdefault(code, []).append(n)
    return out


def _short(v: Any) -> str:
    f = _num(v)
    if f is not None and not isinstance(v, bool):
        return f"{f:,.4g}" if abs(f) < 1e6 else f"{f:,.0f}"
    if v is None:
        return ""
    return str(v)[:80]


def _route_nodes(rec, ids, stock) -> list:
    from trading.valuation import route as R
    rt = (rec or {}).get("route") or {}
    this = _node(ids("s09.this_stock"), "This stock's route",
                 "label = cfg.sector_map → Nifty-500 industry file → unmapped; keyword rules for "
                 "the coarse labels; PSU / holding overlays; anything else UNDECLARED, never forced")
    if rt.get("status") == "declared":
        txt = (f"{rt.get('sector')} · method {rt.get('method')} · family {rt.get('family')} "
               f"(label '{rt.get('label') or '—'}' via {rt.get('label_source') or '—'})")
        _set(this, txt, "", stock)
    else:
        _set(this, "", f"UNDECLARED — {rt.get('rule') or 'no sector label'}", stock)
    out = [this]
    for r in R.SECTOR_METHODS:
        m = r.get("method") or ""
        n = _node(ids(f"s09.{_slug(m)}"), r.get("sector") or m,
                  f"method {m} · family {r.get('family')} · basis {r.get('primary_basis') or '—'} · "
                  f"per share {r.get('per_share_metric') or '—'}", info=True)
        if stock and m and m == rt.get("method"):
            n["current"] = True
            n["value"] = "← this stock's route"
        for k, label in (("key_drivers", "Key drivers"), ("bad_time_behaviour", "In a bad year"),
                         ("data_missing", "Data still missing")):
            if r.get(k):
                n["children"].append(_node(ids(f"{n['id']}.{k}"), label, str(r[k]), info=True))
        out.append(n)
    return out


def _shares_nodes(rec, ids, stock) -> list:
    from trading.valuation import shares as SH
    sh = (rec or {}).get("shares") or {}
    tol = (SH.ASSUMED or {}).get("reconcile_tol_pct")
    basic = _node(ids("s10.basic_now"), "Basic shares now",
                  "today's basic share count (the snapshot's shares outstanding)")
    _set(basic, f"{_num(sh.get('basic_now')) / 1e7:,.2f} cr shares" if _num(sh.get("basic_now")) else "",
         sh.get("note") or "", stock)
    dil = _node(ids("s10.dilution"), "Dilution", "diluted ÷ basic − 1 (ESOP pool DATA_NEEDED)")
    _set(dil, _pct(sh.get("dilutive_pct")), "no diluted count on file", stock)
    rec_n = _node(ids("s10.reconcile"), "Period-pair reconciliation",
                  f"each period pair reconciles within ±{tol}% after split / bonus factors, else "
                  f"'unexplained' (printed as a warning beside the verdict)")
    _set(rec_n, f"{sh.get('status')}" + (f" — {sh.get('note')}" if sh.get("note") else "")
         if sh.get("status") and sh.get("status") != "DATA_NEEDED" else "",
         sh.get("note") or "share bridge DATA_NEEDED", stock)
    rule = _node(ids("s10.factor_rule"), "Corporate-action factors",
                 str((SH.ASSUMED or {}).get("factor_rule") or "split = old face ÷ new face; "
                                                              "bonus a:b = 1 + a/b"), info=True)
    return [basic, dil, rec_n, rule]


def _earnings_nodes(rec, ids, stock) -> list:
    from trading.valuation import pipeline as P
    e = (rec or {}).get("earnings") or {}
    why = e.get("why") or "normalised PAT DATA_NEEDED"
    rep = _node(ids("s11.reported_pat"), "Reported PAT", "net income to common, the locked FY")
    _set(rep, _cr(e.get("reported_pat")), why, stock)
    adj = _node(ids("s11.adjustments"), "One-off adjustments",
                "unusual items (pre-tax) subtracted, their tax effect added back; components "
                "shown, never subtracted twice")
    _set(adj, f"{e.get('n_adjustments') or 0} applied" if e.get("status") == "OK" else "", why, stock)
    if stock:
        for i, a in enumerate((e.get("adjustments") or [])[:10]):
            c = _node(ids(f"s11.adjustments.{i + 1}"), str(a.get("source") or a.get("reason") or "line"),
                      str(a.get("reason") or ""))
            c["value"] = (_cr(a.get("amount")) + (" (applied)" if a.get("applied") else " (shown only)"))
            adj["children"].append(c)
    norm = _node(ids("s11.normalised_pat"), "Normalised PAT",
                 "reported − unusual items + their tax effect (checked against the audited "
                 "Normalized Income line)")
    _set(norm, _cr(e.get("normalised_pat")), why, stock)
    eps = _node(ids("s11.normalised_eps"), "Normalised EPS", "normalised PAT ÷ shares")
    _set(eps, _inr(e.get("normalised_eps")), why, stock)
    src = (P.ASSUMED.get("target_starts_from") or {}).get("value") or (
        "normalised PAT on every route when it is a number, else reported PAT, labelled")
    start = _node(ids("s11.target_starts_from"), "The target starts from", str(src))
    _set(start, str(e.get("target_starts_from") or ""), "not recorded on this row", stock)
    return [rep, adj, norm, eps, start]


def _quality_nodes(rec, ids, stock) -> list:
    from trading.valuation import normalise as N
    q = (rec or {}).get("quality") or {}
    rule = (N.ASSUMED or {}).get("score_rule") or "score = ASSUMED weights over the lines below"
    out = []
    score = _node(ids("s12.score"), "Quality score", str(rule))
    _set(score, f"{_num(q.get('score')):.0f}/100" if _num(q.get("score")) is not None else "",
         q.get("why") or "score DATA_NEEDED", stock)
    out.append(score)
    for k, name, formula, fmt in (
            ("cash_conversion", "Cash conversion", "OCF ÷ PAT (3 FYs)", _f2),
            ("net_debt_ebitda", "Net debt / EBITDA", "(debt − cash − short-term investments) ÷ EBITDA", _f2),
            ("interest_cover", "Interest cover", "EBIT ÷ interest", _f2),
            ("debt_equity", "Debt / equity", "total debt ÷ equity", _f2),
            ("fcf_margin", "FCF margin", "(OCF − capex) ÷ revenue", lambda v: _pct(_num(v) * 100 if _num(v) is not None else None, 1))):
        n = _node(ids(f"s12.{k}"), name, formula)
        _set(n, fmt(q.get(k)), "not on file for this stock (lenders: DATA_NEEDED by design)", stock)
        out.append(n)
    flags = _node(ids("s12.flags"), "Flags", "each line flagged at the thresholds in §13 Part A")
    _set(flags, "; ".join(q.get("flags") or []) or ("none" if q else ""), "", stock)
    out.append(flags)
    return out


def _macro_nodes(rec, ids, stock) -> list:
    m = (rec or {}).get("macro_inputs") or {}
    out = []
    mk = _node(ids("s13.market"), "Market: Nifty 50 / Sensex",
               "level at the base period end vs the latest stored close (index store)")
    if _num(m.get("nifty_start")) and _num(m.get("nifty_end")):
        txt = (f"Nifty {_f2(m.get('nifty_start'))} → {_f2(m.get('nifty_end'))}"
               + (f" (to {m.get('index_end_date')})" if m.get("index_end_date") else "")
               + (f" · Sensex {_f2(m.get('sensex_start'))} → {_f2(m.get('sensex_end'))}"
                  if _num(m.get("sensex_end")) else ""))
    else:
        txt = ""
    _set(mk, txt, "no index level since the base period end — the market row counts 0", stock)
    out.append(mk)
    vix = _node(ids("s13.vix"), "India VIX", "latest stored close")
    _set(vix, _f2(m.get("vix")), "no VIX on file", stock)
    out.append(vix)
    rf = _node(ids("s13.rf"), "10-year G-sec yield (rf)",
               "the latest stored Upstox 10-year G-sec yield (bond maths on the most-traded "
               "10-year paper), STALE past 10 days; none stored → the committed June file")
    if _num(m.get("rf_pct")) is not None:
        txt = (f"{_pct(m.get('rf_pct'))} on {m.get('rf_date') or '—'} · {m.get('rf_source') or ''}"
               + (" · STALE" if m.get("rf_stale") else ""))
        if _num(m.get("rbi_benchmark_pct")) is not None:
            # lead's note (a): RBI's own benchmark beside the Upstox read,
            # display only — the owner chose Upstox as the source
            txt += (f" · RBI benchmark {_pct(m.get('rbi_benchmark_pct'))} "
                    f"({m.get('rbi_benchmark_date') or '—'}"
                    + (f", {m.get('rbi_benchmark_bond')}" if m.get("rbi_benchmark_bond") else "")
                    + ") — display only, not used")
    else:
        txt = ""
    _set(rf, txt, "no 10-year yield on file", stock)
    out.append(rf)
    repo = _node(ids("s13.repo"), "RBI repo rate, base period end → now",
                 "start = RBI-verified history at the base period end; end = the latest RBI "
                 "home-page reading stored; a start that cannot be verified counts 0")
    if _num(m.get("repo_start_pct")) is not None and _num(m.get("repo_end_pct")) is not None:
        txt = (f"{_pct(m.get('repo_start_pct'))} ({m.get('repo_start_date') or '—'}) → "
               f"{_pct(m.get('repo_end_pct'))} ({m.get('repo_end_date') or '—'})")
    else:
        txt = ""
    why = m.get("repo_start_why") or m.get("repo_why") or "no repo rate on file"
    if not m.get("repo_start_why") and _num(m.get("repo_start_pct")) is not None:
        why = (f"start {_pct(m.get('repo_start_pct'))} ({m.get('repo_start_date') or '—'}) "
               f"verified; {why}")
    _set(repo, txt, why + " — the rates row counts 0", stock)
    out.append(repo)
    fx = _node(ids("s13.usdinr"), "USD/INR now vs base",
               "now = the latest RBI reference rate stored, else the committed file; base = the "
               "committed file at the base period end (IT exporters' rupee row only)")
    if _num(m.get("usdinr_now")) is not None:
        txt = (f"{_f2(m.get('usdinr_now'))} on {m.get('usdinr_now_date') or '—'} "
               f"({m.get('usdinr_now_source') or '—'}) vs {_f2(m.get('usdinr_base'))} on "
               f"{m.get('usdinr_base_date') or '—'}")
    else:
        txt = ""
    _set(fx, txt, "no USD/INR rate on file", stock)
    out.append(fx)
    inst = _node(ids("s13.inst_holding"), "FII + DII holding, then → now",
                 "FII + DII % at the quarter at/just before the base period end vs the latest "
                 "quarter (NSE shareholding filings)")
    if _num(m.get("inst_holding_start")) is not None and _num(m.get("inst_holding_end")) is not None:
        txt = (f"{_pct(m.get('inst_holding_start'))} ({m.get('inst_holding_start_date') or '—'}) → "
               f"{_pct(m.get('inst_holding_end'))} ({m.get('inst_holding_end_date') or '—'})")
    else:
        txt = ""
    _set(inst, txt, (m.get("inst_holding_why") or "no holding on file")
         + " — the institutional row counts 0", stock)
    out.append(inst)
    beta = _node(ids("s13.beta"), "β on Nifty 50", "OLS of daily returns over 252 sessions (min 120)")
    _set(beta, _f2(m.get("beta")), "fewer than 120 overlapping sessions", stock)
    out.append(beta)
    nf = _node(ids("s13.not_fed"), "Not fed (counted 0)",
               "CPI, GDP and Brent (owner chose no feed), sentiment and chart scores, delivery % "
               "(frozen 2026-08-07): each row of the P/E bridge counts 0")
    if stock:
        miss = ((rec or {}).get("scenarios") or {}).get("macro_data_needed") or []
        nf["value"] = f"{len(miss)} bridge input(s) counted 0" if miss else "none"
        for i, t in enumerate(miss[:12]):
            c = _node(ids(f"s13.not_fed.{i + 1}"), str(t)[:80], "", info=True)
            nf["children"].append(c)
    out.append(nf)
    return out


#: The scenario chain's cells, grouped as the sheet lays them out.
_CELL_GROUPS = (
    ("s15", "base_year", "Base year (column D)", lambda c: c.startswith("D")),
    ("s15", "revenue_costs", "Revenue and the cost / capex drivers",
     lambda c: c == "E17" or c in {f"E{i}" for i in range(101, 119)}),
    ("s15", "forecast", "Forecast EBITDA, tax and PAT",
     lambda c: c in {"E19", "E20", "E22", "E25", "E26", "E27"}),
    ("s16", "eps", "Scenario EPS, book value and leverage",
     lambda c: c in {"E28", "E29", "E30", "E32", "E33", "E34", "E35", "E37", "E47"}),
    ("s16", "pe_bridge", "P/E bridge (section H) — opening → closing P/E",
     lambda c: c in {"E74", "E82", "", "None"} or c in {f"E{i}" for i in range(120, 135)}),
    ("s16", "price", "Model price and the market check", lambda c: True),
)


def _cell_nodes(rec, ids, stock) -> dict:
    """{stage code: [group nodes]} — the 82 computed cells of the owner's
    sheet, each with the stock's base-case value (downside / upside beside)."""
    from trading.valuation import scenarios as SC
    cases = (((rec or {}).get("detail") or {}).get("cases") or {})
    cell_of = {c: ((cases.get(c) or {}).get("cells") or {}) for c in ("downside", "base", "upside")}
    groups: dict = {}
    for g_stage, g_key, g_name, _fn in _CELL_GROUPS:
        groups[(g_stage, g_key)] = _node(ids(f"{g_stage}.{g_key}"), g_name,
                                         "the owner's sheet, cell for cell", info=True)
    for c in SC.chain_formulas():
        cell = str(c.get("cell") or "")
        for g_stage, g_key, _n, fn in _CELL_GROUPS:
            if fn(cell):
                break
        key = cell or str(c.get("key") or "sum")
        n = _node(ids(f"{g_stage}.{g_key}.{_slug(key)}"), f"{cell or '—'} {c.get('label') or ''}",
                  str(c.get("formula") or ""))
        look = cell or c.get("key")
        b = _num(cell_of["base"].get(look))
        if b is not None:
            d, u = _num(cell_of["downside"].get(look)), _num(cell_of["upside"].get(look))
            txt = f"{b:,.2f}" + (f" (down {d:,.2f} / up {u:,.2f})" if d is not None and u is not None else "")
        else:
            txt = ""
        _set(n, txt, "this stock's route runs no scenario chain" if not cases
             else "cell not computed for this stock", stock)
        groups[(g_stage, g_key)]["children"].append(n)
    out: dict = {}
    for (g_stage, _k), node in groups.items():
        if node["children"]:
            out.setdefault(g_stage[1:], []).append(node)
    return out


def _cases_nodes(rec, ids, stock) -> tuple:
    from trading.valuation import scenarios as SC
    sc = (rec or {}).get("scenarios") or {}
    dl = SC.CASE_DELTAS
    up = dl.get("upside") or {}
    deltas = _node(ids("s15.case_deltas"), "Case deltas",
                   f"downside / upside = the base chain with ∓{abs(_num(up.get('revenue_growth_pct')) or 0):g}pp "
                   f"revenue growth, ∓{abs(_num(up.get('base_ebitda_margin_pct')) or 0):g}pp EBITDA margin "
                   f"and ∓{abs(_num(up.get('pe_bridge_extra_pct')) or 0):g} points on the P/E bridge "
                   "(ASSUMED, editable in §13)", info=True)
    drv = sc.get("drivers") or {}
    drivers = _node(ids("s15.drivers"), "Base-year drivers",
                    "growth = revenue_t ÷ revenue_t−1 − 1 (constant currency for converted names); "
                    "margin = EBITDA ÷ revenue; PAT as % of EBITDA; payout")
    _set(drivers, (f"growth {_pct(drv.get('revenue_growth_pct'), 1)} · margin "
                   f"{_pct(drv.get('base_margin_pct'), 1)} · PAT/EBITDA {_pct(drv.get('pat_pct_of_ebitda'), 1)}")
         if sc.get("status") == "OK" else "", sc.get("why") or "", stock)
    eps = sc.get("eps") or {}
    e = _node(ids("s16.case_eps"), "Scenario EPS (down / base / up)", "E29 of each case")
    _set(e, " / ".join(_f2(eps.get(c)) for c in ("downside", "base", "upside"))
         if sc.get("status") == "OK" else "", sc.get("why") or "", stock)
    br = _node(ids("s16.pe_bridge_total"), "P/E bridge total (base case)",
               "E132 = sum of the factor rows + the case points; moves the what-if price, "
               "never the range when a P/E band exists")
    _set(br, _pct(sc.get("pe_bridge_pct")) if sc.get("status") == "OK" else "",
         sc.get("why") or "", stock)
    return [deltas, drivers], [e, br]


def _range_nodes(rec, ids, stock) -> list:
    from . import valuation_chain_view as cv
    rng = (rec or {}).get("range") or {}
    fam = rng.get("family") or ((rec or {}).get("route") or {}).get("family") or ""
    rules = dict(cv.family_rules())
    fr = _node(ids("s17.family_rule"), "This stock's range rule",
               rules.get(fam, "") if stock else "per family — see the families below")
    _set(fr, f"family {fam}" if fam else "", "route UNDECLARED — no family", stock)
    fams = _node(ids("s17.families"), "Range rule per family", "", info=True)
    for f, txt in cv.family_rules():
        fams["children"].append(_node(ids(f"s17.families.{_slug(f)}"), f, txt, info=True))
    out = [fr, fams]
    rg = _node(ids("s17.range"), "Justified range (low / base / high)",
               "the three cases kept in their own order — never sorted; out of order = CASES "
               "INVERTED and no verdict")
    if rng.get("status") == "OK":
        txt = f"₹{_f2(rng.get('low'))} / ₹{_f2(rng.get('base'))} / ₹{_f2(rng.get('high'))}"
        if rng.get("inverted"):
            txt += " · CASES INVERTED"
    else:
        txt = ""
    _set(rg, txt, rng.get("why") or "range DATA_NEEDED", stock)
    out.append(rg)
    band = rng.get("multiple_band") or {}
    bn = _node(ids("s17.band"), "Multiple band",
               "low = min(peer, own low), mid = (peer + own median) ÷ 2, high = max(peer, own "
               "high); 4+ own readings: the single lowest and highest dropped first")
    _set(bn, (f"{_f2(band.get('low'))} / {_f2(band.get('mid'))} / {_f2(band.get('high'))}× — "
              f"{band.get('source') or ''}") if band else "",
         "no multiple band on this route" if rng.get("status") == "OK" else (rng.get("why") or ""),
         stock)
    out.append(bn)
    hist = rng.get("own_history") or []
    oh = _node(ids("s17.own_history"), "Own P/E history",
               "FY-end close ÷ (normalised PAT ÷ that FY's own period-end shares); basic EPS "
               "when either is missing; an interior odd year is flagged, never dropped")
    _set(oh, f"{sum(1 for h in hist if _num(h.get('pe')) is not None)} reading(s)" if hist else "",
         "no FY-end readings (no statements or no prices)", stock)
    if stock:
        for h in hist[:12]:
            c = _node(ids(f"s17.own_history.{_slug(h.get('period'))}"), str(h.get("period") or ""),
                      str(h.get("eps_basis") or ""))
            if _num(h.get("pe")) is not None:
                c["value"] = (f"{_f2(h.get('pe'))}× (price {_f2(h.get('price'))} ÷ EPS "
                              f"{_f2(h.get('eps'))})")
            else:
                c["why"] = "no FY-end price or a loss year — no P/E"
            if h.get("flag"):
                c["value"] = (c["value"] + " · " if c["value"] else "") + f"⚑ {h['flag']}"
            oh["children"].append(c)
    out.append(oh)
    wi = rng.get("whatif") or {}
    wn = _node(ids("s17.whatif"), "What-if price (your sheet)",
               "each case's model price = EPS × opening P/E × (1 + P/E bridge); moves with the "
               "bridge, not the range")
    _set(wn, " / ".join(f"₹{_f2(wi.get(c))}" for c in ("downside", "base", "upside")) if wi else "",
         "no what-if on this route (no P/E band)", stock)
    out.append(wn)
    return out


def _context_nodes(rec, ids, stock, target: Optional[dict]) -> list:
    import trading.valuation_context as vc
    a = vc.ASSUMED
    out = []
    why = ("needs a §11 target for this stock — the context legs verify a stored target "
           "(see §11)") if not target else ""
    for k, name, formula in (
            ("sector_leg", "Sector leg", "target × (sector index now ÷ then), at β = 1 and at the "
                                         "measured β, side by side (Indian NSE sector indices)"),
            ("rates_leg", "Rates leg", f"(r_then − g) ÷ (r_now + shift − g), r = 10y + ERP "
                                       f"{a.get('equity_risk_premium_pct')}%, g "
                                       f"{a.get('long_run_growth_pct')}%"),
            ("sentiment", "Sentiment margin", f"buy below target × (1 − (base "
                                              f"{a.get('base_buy_discount_pct')}% + "
                                              f"{a.get('fear_step_pp')}pp per fear gauge ON))"),
            ("decompose", "Move split", f"sector part vs stock residual; residual ≥ ±"
                                        f"{a.get('rerun_residual_pct')}% = RE-RUN flag")):
        n = _node(ids(f"s17b.{k}"), name, formula)
        _set(n, (f"§11 target ₹{_f2(target.get('target'))} on {target.get('asof') or '—'} — "
                 "the leg is printed on §11") if target else "", why, stock)
        out.append(n)
    return out


def _verdict_nodes(rec, ids, stock) -> list:
    from trading.valuation import assess as A
    b = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in A.ASSUMED.items()}
    v = (rec or {}).get("verdict") or {}
    out = []
    vn = _node(ids("s18.verdict"), "Verdict", "attractive · watch · unattractive · insufficient — "
                                              "never a trade permission")
    _set(vn, (str(v.get("verdict") or "").upper()
              + (f" · {v.get('upside_to_base_pct'):+.1f}% to base"
                 if _num(v.get("upside_to_base_pct")) is not None else "")) if v else "",
         "no verdict", stock)
    if stock:
        for i, r in enumerate((v.get("reasons") or [])[:6]):
            vn["children"].append(_node(ids(f"s18.verdict.{i + 1}"), str(r)[:120], "", info=True))
    out.append(vn)
    rules = _node(ids("s18.rules"), "The rules, in order", "", info=True)
    for k, name, txt in (
            ("insufficient", "INSUFFICIENT first",
             f"a range value missing or the cases out of order; or (high − low) ÷ base above "
             f"{b.get('max_range_width_pct')}%; or the stated uncertainty above "
             f"{b.get('max_uncertainty_pct')}%"),
            ("attractive", "ATTRACTIVE", f"price ≤ base × (1 − {b.get('attractive_margin_pct')}%)"),
            ("unattractive", "UNATTRACTIVE",
             f"price above the range high, or above base × (1 + {b.get('unattractive_premium_pct')}%) "
             f"with no thesis recorded"),
            ("watch", "WATCH", f"otherwise (watch band ±{b.get('watch_band_pct')}%)")):
        rules["children"].append(_node(ids(f"s18.rules.{k}"), name, txt, info=True))
    out.append(rules)
    wn = _node(ids("s18.warnings"), "Warnings beside the verdict",
               "earnings quality below 50 (ASSUMED) and an unexplained share bridge — never gates")
    w = v.get("warnings") or []
    _set(wn, "; ".join(w) if w else ("none" if v else ""), "no verdict", stock)
    out.append(wn)
    return out


def build_tree(rec: Optional[dict] = None, seq_rows: Optional[list] = None,
               branches: Optional[list] = None, target: Optional[dict] = None) -> dict:
    """{nodes, ids, n_nodes, n_owner, orphans} — the 26 stages with their
    code branches (and the stock's numbers when `rec` is given), then the
    owner's branches hung under their parents."""
    if seq_rows is None:
        from .routes import den_routes as D
        seq_rows = D._sequence()["rows"]
    stock = rec is not None
    ids = _Ids()
    stages: list = []
    by_code: dict = {}
    for r in seq_rows:
        code = str(r.get("code"))
        n = _node(ids(f"s{code}"), r.get("name") or "", r.get("where") or "", kind="stage")
        n["status"] = r.get("status") or ""
        n["code"] = code
        n["open"] = stock and n["id"] in _OPEN_WITH_STOCK
        stages.append(n)
        by_code[code] = n

    def add(code: str, nodes: list) -> None:
        if code in by_code:
            by_code[code]["children"].extend(nodes)

    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception:                               # noqa: BLE001
            log.exception("den walk: %s failed", getattr(fn, "__name__", fn))
            return []

    rt = (rec or {}).get("route") or {}
    s00 = _node(ids("s00.route"), "This stock's valuation basis",
                "the sector-method table (stage 09) decides the family the range uses")
    _set(s00, (f"{rt.get('sector')} → family {rt.get('family')}" if rt.get("status") == "declared"
               else ""), f"UNDECLARED — {rt.get('rule') or 'no sector label'}", stock)
    add("00", [s00])
    snap = (rec or {}).get("snapshot") or {}
    sw = snap.get("why") or "no statements"
    s7 = []
    for k, name, formula, val in (
            ("period_end", "Locked FY", "latest audited FY in the statements zip (+ monthly refresh)",
             snap.get("period_end")),
            ("publication", "Publication date", "period end + the ASSUMED lag unless an NSE result "
                                                "date is given (point-in-time)",
             (str(snap.get("publication_date") or "") + (" (assumed)" if snap.get("publication_assumed")
                                                         else "")) if snap.get("publication_date") else ""),
            ("source", "Source", "the committed statements zip / fundamentals file", snap.get("source")),
            ("missing", "Line items missing", "fields the snapshot could not fill",
             str(snap.get("missing")) if snap.get("status") == "OK" else "")):
        n = _node(ids(f"s07.{k}"), name, formula)
        _set(n, str(val or ""), sw, stock)
        s7.append(n)
    add("07", s7)
    s8 = []
    for k, name, formula, val in (
            ("currency", "Currency lock", "an ADR name converts at the period-end USD-INR only when "
                                          "it is on the suspect list AND price ÷ EPS exceeds the USD "
                                          "signature; list alone never converts",
             (str(snap.get("currency") or "") + (f" · fx {snap.get('fx')}" if snap.get("fx") else ""))),
            ("basis", "Consolidated / standalone", "DATA_NEEDED unless the source says (never guessed)",
             snap.get("basis")),
            ("lock_hash", "Lock hash", "sha256 over the locked fields",
             (snap.get("lock_hash") or "")[:12])):
        n = _node(ids(f"s08.{k}"), name, formula)
        _set(n, str(val or ""), sw, stock)
        s8.append(n)
    add("08", s8)
    add("09", safe(_route_nodes, rec, ids, stock))
    add("10", safe(_shares_nodes, rec, ids, stock))
    add("11", safe(_earnings_nodes, rec, ids, stock))
    add("12", safe(_quality_nodes, rec, ids, stock))
    add("13", safe(_macro_nodes, rec, ids, stock))
    c15, c16 = safe(_cases_nodes, rec, ids, stock) or ([], [])
    add("15", c15)
    add("16", c16)
    for code, nodes in (safe(_cell_nodes, rec, ids, stock) or {}).items():
        add(code, nodes)
    add("17", safe(_range_nodes, rec, ids, stock))
    add("17b", safe(_context_nodes, rec, ids, stock, target))
    add("18", safe(_verdict_nodes, rec, ids, stock))
    for code, nodes in (safe(_terms_nodes, rec, ids, stock) or {}).items():
        add(code, nodes)
    code_ids = set(ids.seen)
    n_code = len(code_ids)
    orphans = _attach_branches(stages, branches or [], code_ids)
    return {"nodes": stages, "ids": sorted(code_ids), "n_nodes": n_code,
            "n_owner": len(branches or []), "orphans": orphans}


def _index(nodes: list, out: Optional[dict] = None) -> dict:
    out = {} if out is None else out
    for n in nodes:
        out[n["id"]] = n
        _index(n["children"], out)
    return out


def _attach_branches(stages: list, branches: list, code_ids: set) -> list:
    """Hang each owner branch under its parent (code node or owner branch);
    a branch whose parent is gone is returned as an orphan (printed apart)."""
    idx = _index(stages)
    own: dict = {}
    for b in branches:
        n = _node(str(b.get("id")), str(b.get("name") or ""), str(b.get("formula") or ""),
                  kind="owner", info=True)
        n["note"], n["ts"], n["parent"] = str(b.get("note") or ""), str(b.get("ts") or ""), \
            str(b.get("parent") or "")
        own[n["id"]] = n
    orphans = []
    for nid, n in own.items():
        parent = n["parent"]
        host = own.get(parent) or idx.get(parent)
        if host is None or parent == nid:
            orphans.append(n)
        else:
            host["children"].append(n)
    return orphans


# ---------------------------------------------------- owner branch store

READ_FAILED = ("the stored branches could not be read (database) — nothing changed; "
               "try again when the store answers")


def _read_branches() -> tuple[list, bool]:
    """([{id, parent, name, formula, note, ts}], ok) — this process's copy
    first (the one the owner just saved; db.save_settings swallows a failed
    write, so Mongo can hold an older list — the sandbox draft's rule), then
    the Mongo doc (survives a restart). ok is False when the Mongo read
    RAISED — reviewer defect 5 (2026-10-21): that used to read as an empty
    list, and the save that followed wrote it over every stored branch."""
    from . import db
    rows: list = list(LAST_BRANCHES)
    ok = True
    if not rows:
        try:
            doc = db.load_settings(doc_id=BRANCHES_DOC_ID) or {}
            rows = list(doc.get("branches") or [])
        except Exception:                               # noqa: BLE001
            log.warning("den walk: branches unreadable")
            ok = False
    return [dict(r) for r in rows if isinstance(r, dict) and r.get("id")], ok


def load_branches() -> list:
    """The branches for DISPLAY (a failed read prints none); a save or a
    remove never builds on this — they use _read_branches and refuse."""
    return _read_branches()[0]


def _store(rows: list) -> None:
    from . import db
    LAST_BRANCHES.clear()
    LAST_BRANCHES.extend(rows)
    try:
        db.save_settings({"branches": list(rows), "ts": datetime.utcnow().isoformat()},
                         doc_id=BRANCHES_DOC_ID)
    except Exception:                                   # noqa: BLE001
        log.exception("den walk: branches save failed (in-memory copy kept)")


def _descendants(rows: list, root: str) -> set:
    kids: dict = {}
    for r in rows:
        kids.setdefault(str(r.get("parent")), []).append(str(r.get("id")))
    out, todo = set(), [root]
    while todo:
        x = todo.pop()
        for k in kids.get(x, []):
            if k not in out:
                out.add(k)
                todo.append(k)
    return out


def save_branch(form, code_ids: Optional[set] = None) -> tuple[bool, str, str]:
    """Add a branch, or edit one when the form names an existing `id`.
    (ok, error, id). The parent must be a code node or an owner branch.
    Refused, nothing written, when the stored branches could not be read."""
    rows, ok = _read_branches()
    if not ok:
        return False, READ_FAILED, ""
    if code_ids is None:
        code_ids = set(build_tree(None, branches=[])["ids"])
    owner_ids = {str(r["id"]) for r in rows}
    nid = str(form.get("id") or "").strip()
    parent = str(form.get("parent") or "").strip()
    name = " ".join(str(form.get("name") or "").split())
    formula = str(form.get("formula") or "").strip()
    note = str(form.get("note") or "").strip()
    if not name:
        return False, "a branch needs a name", ""
    if len(name) > MAX_NAME:
        return False, f"name: at most {MAX_NAME} characters", ""
    if len(formula) > MAX_TEXT or len(note) > MAX_TEXT:
        return False, f"formula and note: at most {MAX_TEXT} characters each", ""
    if not parent or not _ID_RE.match(parent):
        return False, "pick a parent node id (for example s15 or s15.revenue_growth)", ""
    if parent not in code_ids and parent not in owner_ids:
        return False, f"no node '{parent}' in the tree", ""
    now = datetime.utcnow().isoformat(timespec="seconds")
    if nid:
        if nid not in owner_ids:
            return False, f"no branch of yours with id '{nid}'", ""
        if parent == nid or parent in _descendants(rows, nid):
            return False, "a branch cannot hang under itself or its own sub-branch", ""
        for r in rows:
            if r["id"] == nid:
                r.update({"parent": parent, "name": name, "formula": formula, "note": note,
                          "ts": now})
        _store(rows)
        return True, "", nid
    if len(rows) >= MAX_BRANCHES:
        return False, f"at most {MAX_BRANCHES} branches — remove some first", ""
    n = 1 + max([int(r["id"][2:]) for r in rows
                 if str(r["id"]).startswith("ob") and str(r["id"])[2:].isdigit()] or [0])
    nid = f"ob{n}"
    rows.append({"id": nid, "parent": parent, "name": name, "formula": formula, "note": note,
                 "ts": now})
    _store(rows)
    return True, "", nid


def remove_branch(nid: str) -> int:
    """Remove one owner branch and its whole subtree; the count removed —
    −1 when the stored branches could not be read (nothing written)."""
    rows, ok = _read_branches()
    if not ok:
        return -1
    nid = str(nid or "").strip()
    if nid not in {str(r["id"]) for r in rows}:
        return 0
    gone = {nid} | _descendants(rows, nid)
    keep = [r for r in rows if str(r["id"]) not in gone]
    _store(keep)
    return len(gone)


# ---------------------------------------------------------- the section

def section(cfg, p, typed: str = "", flag: str = "", error: str = "") -> dict:
    """The §1 walk for one page render."""
    from . import valuation_chain_view as cv
    from . import valuation_view as vv
    walk = cv.walk_record(cfg, p, typed) if typed else {
        "typed": "", "ticker": "", "rec": None, "status": "", "why": "", "elapsed_s": 0.0,
        "store_down": ""}
    rec = walk.get("rec")
    target = None
    if rec is not None:
        try:
            target = (vv.load_doc().get("targets") or {}).get(walk["ticker"])
        except Exception:                               # noqa: BLE001
            target = None
    branches = load_branches()
    tree = build_tree(rec, branches=branches, target=target)
    return {"typed": walk.get("typed") or "", "ticker": walk.get("ticker") or "",
            "status": walk.get("status") or "", "why": walk.get("why") or "",
            "elapsed_s": walk.get("elapsed_s") or 0.0, "store_down": walk.get("store_down") or "",
            "has_stock": rec is not None,
            "price": (rec or {}).get("price"), "price_date": (rec or {}).get("price_date"),
            "nodes": tree["nodes"], "ids": tree["ids"], "n_nodes": tree["n_nodes"],
            "n_owner": tree["n_owner"], "orphans": tree["orphans"],
            "branches": branches, "max_branches": MAX_BRANCHES,
            "flag": flag or "", "error": error or "", "doc_id": BRANCHES_DOC_ID}


def empty_section(error: str = "") -> dict:
    return {"typed": "", "ticker": "", "status": "", "why": "", "elapsed_s": 0.0,
            "store_down": "", "has_stock": False, "price": None, "price_date": None,
            "nodes": [], "ids": [], "n_nodes": 0, "n_owner": 0, "orphans": [], "branches": [],
            "max_branches": MAX_BRANCHES, "flag": "invalid" if error else "", "error": error,
            "doc_id": BRANCHES_DOC_ID}
