"""Sector TOTAL valuation beside the target (VALFIX batch 3, owner order
2026-10-21: "show beside the target"; study 2, REPORT_sector_val.md, V3).

Per route group of the statement set (the names on the same valuation
route — the peer group the range already uses):

    total market cap  = Σ price × latest-FY shares
    aggregate P/E     = Σ mcap ÷ Σ latest-FY PAT (net income to common;
                        loss-makers included, so a group with a big loss
                        reads high or "n/m"; a name with no PAT on file or
                        a newest FY older than the group's is left out of
                        every multiple and still counted in the market cap)
    EV/EBITDA         = Σ (mcap + debt − cash − short-term investments +
                        minorities) ÷ Σ EBITDA       (EV routes)
    P/B               = Σ mcap ÷ Σ book (a lender's VAL-02 book, else
                        equity)                       (book routes)
    4-yr FY-end average = the mean of the four latest FY-end aggregates on a
                        CONSTANT panel (names with every one of the four
                        FYs), each = Σ(FY-end close × that FY's shares) ÷
                        Σ that FY's PAT (the app's own_pe_history
                        convention); "now" is recomputed on that same panel
    % below 52-week high = the group total now against the highest daily
                        Σ close × latest shares of the last 252 sessions

and NSE's own published P/E / P/B for the stock's sector index (stored
daily, dated by NSE's session). Moves NO target — display only.

PURE: base rows, prices and closes are handed in (the web layer builds and
stores them once a day). Boundary test: nothing in the money path reads it.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from trading.valuation import num

CR = 1e7

ASSUMED: Dict[str, dict] = {
    "group": {"value": "the names on the same valuation route (route method) among the "
                       "Nifty-200 statement set",
              "source": "owner 2026-10-21 ('Σ price × shares over the route group from the "
                        "statement set')"},
    "fy_label": {"value": "a period ending Jan-Mar counts as that year's FY, Apr-Dec as the "
                          "next year's",
                 "source": "ASSUMED here 2026-10-21 so a December-year company joins the "
                           "same FY column as the March-year names"},
    "panel": {"value": "constant panel: names with all four FYs priced",
              "source": "REPORT_sector_val.md (study 2) method"},
    "fy_years": {"value": 4, "source": "owner 2026-10-21 ('4-yr FY-end average')"},
    "high_window": {"value": 252,
                    "source": "52 weeks of sessions; a name enters the high only when its "
                              "closes cover the window (gaps carried forward)"},
}

FAMILY_MULTIPLE = {"earnings": "pe", "ev_ebitda": "ev_ebitda", "ev_sales": "ev_ebitda",
                   "book": "pb", "nav": "pb", "sotp": "pe", "embedded_value": "pb"}
MULTIPLE_LABEL = {"pe": "P/E", "ev_ebitda": "EV/EBITDA", "pb": "P/B"}


def fy_label(period_end: Any) -> Optional[str]:
    try:
        d = date.fromisoformat(str(period_end)[:10])
    except (TypeError, ValueError):
        return None
    return f"FY{(d.year if d.month <= 3 else d.year + 1) % 100:02d}"


def _book(row: dict) -> Optional[float]:
    b = num(row.get("book"))
    return b if b is not None else num(row.get("equity"))


def _ev(px: float, sh: float, row: dict) -> Optional[float]:
    debt, cash = num(row.get("total_debt")), num(row.get("cash"))
    if debt is None or cash is None:
        return None
    sti = num(row.get("short_term_investments")) or 0.0
    mi = num(row.get("minority_interest_bs")) or 0.0
    return px * sh + debt - cash - sti + mi


def _ratio(a: float, b: float) -> Optional[float]:
    return a / b if b and b > 0 else None


def _latest_fy(row: dict) -> Optional[str]:
    """The FY label of a base row's newest annual period (None = no history)."""
    periods = [p for p in (row.get("fy") or {}) if fy_label(p)]
    return fy_label(max(periods)) if periods else None


def _group_fy(rows: List[dict]) -> Optional[str]:
    """The group's current FY = the most common newest FY (ties → the newer)."""
    labels = [x for x in (_latest_fy(r) for r in rows) if x]
    if not labels:
        return None
    return max(set(labels), key=lambda x: (labels.count(x), x))


def totals(rows: Iterable[dict], prices: Dict[str, float]) -> dict:
    """Group totals NOW from the base rows' latest FY and today's prices.

    Every priced name adds to the market cap. A multiple pairs only the names
    that carry both sides (a P/E only names with a PAT on file — a missing
    PAT would inflate it), and a name whose newest FY is older than the
    group's (its statements are a year behind) counts in the market cap but
    in no multiple (study 2 rule; listed as `stale_fy`)."""
    rows = list(rows)
    cur_fy = _group_fy(rows)
    n = n_priced = 0
    mcap = 0.0
    pe_m = pat = 0.0
    n_pe = 0
    ev = ebitda = 0.0
    n_ev = 0
    book_m = book = 0.0
    n_book = 0
    missing: List[str] = []
    stale: List[str] = []
    no_pat: List[str] = []
    for r in rows:
        n += 1
        t = r.get("ticker")
        px, sh = num(prices.get(t)), num(r.get("shares_outstanding"))
        if px is None or not sh:
            missing.append(str(t))
            continue
        n_priced += 1
        m = px * sh
        mcap += m
        lf = _latest_fy(r)
        if cur_fy and lf and lf < cur_fy:
            stale.append(str(t))
            continue
        p = num(r.get("pat"))
        if p is not None:
            pe_m += m
            pat += p
            n_pe += 1
        else:
            no_pat.append(str(t))
        e, eb = _ev(px, sh, r), num(r.get("ebitda"))
        if e is not None and eb is not None:
            ev += e
            ebitda += eb
            n_ev += 1
        bk = _book(r)
        if bk is not None:
            book_m += m
            book += bk
            n_book += 1
    return {"n": n, "n_priced": n_priced, "mcap_cr": mcap / CR if n_priced else None,
            "pat_cr": pat / CR if n_pe else None,
            "pe": _ratio(pe_m, pat) if n_pe else None,
            "ev_ebitda": _ratio(ev, ebitda) if n_ev else None,
            "pb": _ratio(book_m, book) if n_book else None,
            "n_pe": n_pe, "n_ev": n_ev, "n_book": n_book, "fy": cur_fy,
            "unpriced": missing[:20], "stale_fy": stale[:20], "no_pat": no_pat[:20]}


def _fy_rows(row: dict) -> Dict[str, dict]:
    """{FY label: that FY's figures} from a base row's `fy` history."""
    out: Dict[str, dict] = {}
    for period, f in (row.get("fy") or {}).items():
        lab = fy_label(period)
        if lab and isinstance(f, dict):
            out[lab] = {**f, "period": period}
    return out


def fy_history(rows: Iterable[dict], fy_closes: Dict[str, Dict[str, float]],
               prices: Dict[str, float], years: int = int(ASSUMED["fy_years"]["value"])) -> dict:
    """{labels, panel, by_fy: {label: {pe, ev_ebitda, pb}}, avg, now} on a
    constant panel. `fy_closes` = {ticker: {period_end: close on/before it}}."""
    rows = list(rows)
    per: Dict[str, Dict[str, dict]] = {}
    labels: set = set()
    for r in rows:
        t = r.get("ticker")
        fr = _fy_rows(r)
        closes = fy_closes.get(t) or {}
        ok = {}
        for lab, f in fr.items():
            c = num(closes.get(f["period"]))
            if c is not None and num(f.get("shares")):
                ok[lab] = {**f, "close": c}
        if ok:
            per[t] = ok
            labels |= set(ok)
    want = sorted(labels)[-years:]
    out: Dict[str, Any] = {"labels": want, "panel": [], "by_fy": {}, "avg": {}, "now": {},
                           "why": ""}
    if len(want) < years:
        out["why"] = (f"only {len(want)} FY-end(s) priced for this group — the "
                      f"{years}-yr average needs {years}")
        return out
    panel = sorted(t for t, ok in per.items() if all(lab in ok for lab in want))
    out["panel"] = panel
    if not panel:
        out["why"] = "no name in the group has all four FY-ends priced"
        return out
    for lab in want:
        m = pat = ev = eb = bm = bk = 0.0
        n_ev = n_bk = n_pe = 0
        for t in panel:
            f = per[t][lab]
            sh, c = float(f["shares"]), float(f["close"])
            mm = c * sh
            p = num(f.get("pat"))
            if p is not None:              # a year with no PAT on file stays out of that P/E
                m += mm
                pat += p
                n_pe += 1
            e = _ev(c, sh, {"total_debt": f.get("debt"), "cash": f.get("cash"),
                            "short_term_investments": f.get("sti"),
                            "minority_interest_bs": f.get("mi")})
            if e is not None and num(f.get("ebitda")) is not None:
                ev += e
                eb += float(f["ebitda"])
                n_ev += 1
            b = num(f.get("book")) if num(f.get("book")) is not None else num(f.get("equity"))
            if b is not None:
                bm += mm
                bk += b
                n_bk += 1
        out["by_fy"][lab] = {"pe": _ratio(m, pat) if n_pe else None,
                             "ev_ebitda": _ratio(ev, eb) if n_ev else None,
                             "pb": _ratio(bm, bk) if n_bk else None}
    for k in ("pe", "ev_ebitda", "pb"):
        xs = [out["by_fy"][lab][k] for lab in want]
        out["avg"][k] = (sum(xs) / len(xs)) if all(x is not None and x > 0 for x in xs) else None
    out["now"] = totals([r for r in rows if r.get("ticker") in set(panel)], prices)
    return out


def high_52w(closes: Dict[str, pd.Series], shares: Dict[str, float],
             window: int = int(ASSUMED["high_window"]["value"])) -> dict:
    """{high_cr, high_date, last_cr, last_date, n_in, n_out}: the group's
    daily Σ close × latest shares over the last `window` sessions. A name
    whose closes start after the window's first day is left out (counted)."""
    frames = {}
    n_out = 0
    for t, s in (closes or {}).items():
        sh = num(shares.get(t))
        if s is None or not len(s) or not sh:
            n_out += 1
            continue
        frames[t] = (pd.Series(s).astype(float).sort_index(), float(sh))
    if not frames:
        return {"high_cr": None, "high_date": None, "n_in": 0, "n_out": n_out,
                "why": "no stored closes for the group"}
    idx = sorted(set().union(*[set(s.index) for s, _ in frames.values()]))[-int(window):]
    if not idx:
        return {"high_cr": None, "high_date": None, "n_in": 0, "n_out": n_out,
                "why": "no stored closes for the group"}
    start = idx[0]
    total = pd.Series(0.0, index=pd.DatetimeIndex(idx))
    n_in = 0
    for t, (s, sh) in frames.items():
        if s.index[0] > start:
            n_out += 1
            continue
        x = s.reindex(s.index.union(total.index)).ffill().reindex(total.index)
        total = total + x.fillna(0.0) * sh
        n_in += 1
    if not n_in:
        return {"high_cr": None, "high_date": None, "n_in": 0, "n_out": n_out,
                "why": "no group name covers the 52-week window"}
    hi_d = total.idxmax()
    return {"high_cr": float(total.max()) / CR, "high_date": hi_d.date().isoformat(),
            "last_cr": float(total.iloc[-1]) / CR, "last_date": total.index[-1].date().isoformat(),
            "n_in": n_in, "n_out": n_out, "why": "", "members": sorted(
                t for t, (s, _sh) in frames.items() if s.index[0] <= start)}


def multiple_key(family: str) -> str:
    return FAMILY_MULTIPLE.get(str(family or ""), "pe")


def block(rows: List[dict], prices: Dict[str, float], family: str, built: Optional[dict],
          sector_label: str = "") -> dict:
    """One row's §4 line. `built` = the stored day's {fy, high} for this
    group (None = not built yet → those two parts DATA_NEEDED)."""
    now = totals(rows, prices)
    key = multiple_key(family)
    out: Dict[str, Any] = {"status": "OK", "sector": sector_label, "n": now["n"],
                           "n_priced": now["n_priced"], "mcap_cr": now["mcap_cr"],
                           "group_fy": now["fy"], "stale_fy": now["stale_fy"], "no_pat": now["no_pat"],
                           "pe_now": now["pe"], "multiple": key,
                           "multiple_label": MULTIPLE_LABEL[key], "multiple_now": now.get(key),
                           "panel_n": 0, "avg": None, "panel_now": None, "vs_avg_pct": None,
                           "pe_avg": None, "pe_panel_now": None, "pe_vs_avg_pct": None,
                           "labels": [], "fy_why": "", "high_cr": None, "high_date": None,
                           "below_high_pct": None, "high_why": "", "unpriced": now["unpriced"]}
    if not now["n_priced"]:
        out.update(status="DATA_NEEDED", fy_why="no group name is priced")
        return out
    fy = (built or {}).get("fy")
    if not fy:
        out["fy_why"] = (built or {}).get("fy_why") or "the group's FY history is not built yet"
    else:
        out["labels"] = fy.get("labels") or []
        out["panel_n"] = len(fy.get("panel") or [])
        if fy.get("why"):
            out["fy_why"] = fy["why"]
        else:
            panel_rows = [r for r in rows if r.get("ticker") in set(fy.get("panel") or [])]
            pn = totals(panel_rows, prices)
            out["avg"] = (fy.get("avg") or {}).get(key)
            out["panel_now"] = pn.get(key)
            if out["avg"] and out["panel_now"]:
                out["vs_avg_pct"] = (out["panel_now"] / out["avg"] - 1.0) * 100.0
            out["pe_avg"] = (fy.get("avg") or {}).get("pe")
            out["pe_panel_now"] = pn.get("pe")
            if out["pe_avg"] and out["pe_panel_now"]:
                out["pe_vs_avg_pct"] = (out["pe_panel_now"] / out["pe_avg"] - 1.0) * 100.0
            out["by_fy"] = fy.get("by_fy") or {}
    hi = (built or {}).get("high")
    if not hi or hi.get("high_cr") is None:
        out["high_why"] = (hi or {}).get("why") or "the group's 52-week high is not built yet"
    else:
        members = set(hi.get("members") or [])
        mem_now = totals([r for r in rows if r.get("ticker") in members], prices)
        out["high_cr"], out["high_date"] = hi["high_cr"], hi["high_date"]
        cur = mem_now["mcap_cr"]
        if cur is not None and hi["high_cr"]:
            out["below_high_pct"] = min(0.0, (cur / hi["high_cr"] - 1.0) * 100.0)
            out["high_members"] = len(members)
    return out
