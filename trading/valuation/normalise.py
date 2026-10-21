"""MARS valuation chain — stages 11-12: NORMALISED EARNINGS + EARNINGS QUALITY
and BALANCE-SHEET checks.

Owner order 2026-10-14 ("lets hit the formulas work completion"); task book
stages 11 ("strip one-offs from reported PAT, tax-effected") and 12
("cash conversion, working capital, leverage, coverage — flag weak quality
before any multiple is applied"). Money impact: NONE — display only; the
engine never reads it (package boundary test).

Formulas (published accounting identities, not assumptions)
-----------------------------------------------------------
normalised PAT = reported PAT − unusual items (pre-tax) + tax effect of the
unusual items. Sign convention VERIFIED on TCS FY2026 against Yahoo's own
"Normalized Income": 492,100 − (−36,660) + (−9,018.36) = 519,741.64 (₹ mn).
cash conversion = OCF ÷ PAT · FCF margin = FCF ÷ revenue · receivable days
= receivables ÷ revenue × 365 · working-capital days = (receivables +
inventory − payables) ÷ revenue × 365 · net debt ÷ EBITDA = (debt − cash −
short-term investments) ÷ EBITDA · interest cover = EBIT ÷ interest ·
debt/equity · asset turnover = revenue ÷ total assets.

ASSUMED (printable, `ASSUMED` dict)
-----------------------------------
* yahoo_match_tol_pct 0.5 — our normalised PAT "matches Yahoo" within this;
* tax effect estimated at the EFFECTIVE tax rate (tax ÷ pretax income) when
  the exact tax-effect line is absent; a rate outside 0-50% falls back to
  the snapshot's tax_rate field;
* a missing "Total Unusual Items" line = the source reported none →
  normalised = reported, labelled (cross-checked against Yahoo when present);
* thresholds: cash conversion 0.7, net debt/EBITDA 3, interest cover 2
  (task book notes); quality score weights + linear bands (ASSUMED here);
* lenders (route family "book"): leverage, coverage and working-capital
  ratios are not meaningful — DATA_NEEDED naming the bank ratios instead.
"""
from __future__ import annotations

from typing import Optional

from trading.valuation import dn, is_dn, num

ASSUMED: dict = {
    "yahoo_match_tol_pct": 0.5,
    "tax_rate_bounds": [0.0, 0.5],
    "tax_effect_estimate": "unusual items × effective tax rate (tax ÷ pretax income); "
                           "outside 0-50% → the snapshot's tax_rate field (ASSUMED)",
    "missing_unusual_means_none": ("Yahoo omits the 'Total Unusual Items' line when "
                                   "nothing was reported → normalised = reported "
                                   "(ASSUMED; cross-checked against Yahoo Normalized "
                                   "Income when present)"),
    "thresholds": {"cash_conversion_min": 0.7, "net_debt_ebitda_max": 3.0,
                   "interest_cover_min": 2.0},
    "thresholds_source": "task book notes (stage 12): cash conversion 0.7, "
                         "net debt/EBITDA 3, interest cover 2",
    # score: weight, and the linear band (value at score 0, value at score 100)
    "score_weights": {"cash_conversion": 30, "fcf_margin": 15, "net_debt_ebitda": 20,
                      "interest_cover": 15, "working_capital_days": 10,
                      "debt_equity": 10},
    "score_bands": {"cash_conversion": [0.4, 1.0], "fcf_margin": [0.0, 0.15],
                    "net_debt_ebitda": [4.0, 0.0], "interest_cover": [1.0, 8.0],
                    "working_capital_days": [180.0, 30.0], "debt_equity": [2.0, 0.25]},
    "score_rule": ("each component scored 0-100 linearly between its band ends "
                   "(clipped); the weighted mean over the components that computed; "
                   "fewer than half the weight available → DATA_NEEDED (ASSUMED here)"),
    "lender_note": ("not meaningful for a lender; bank ratios (NIM, GNPA, CAR) come "
                    "from results PDFs"),
}


# ---------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------
def _fields(snap: dict) -> dict:
    return (snap or {}).get("fields") or {}


def _hist(snap: dict, name: str) -> dict[str, float]:
    h = ((snap or {}).get("history") or {}).get(name) or {}
    out: dict[str, float] = {}
    if isinstance(h, dict):
        for k, v in h.items():
            x = num(v)
            if x is not None:
                out[str(k)[:10]] = x
    return out


def _extra(snap: dict, name: str, period: Optional[str] = None) -> Optional[float]:
    """snap["extra"][name] — a scalar (latest period) or {period_end: value}."""
    v = ((snap or {}).get("extra") or {}).get(name)
    if isinstance(v, dict) and not is_dn(v):
        if period is None:
            period = (snap or {}).get("period_end")
        if period is None:
            return None
        return num(v.get(str(period)[:10]))
    if period is not None and period != (snap or {}).get("period_end"):
        return None
    return num(v)


def _val(snap: dict, name: str, period: Optional[str] = None) -> Optional[float]:
    """Latest field, or the history value for `period`."""
    if period is None or period == (snap or {}).get("period_end"):
        x = num(_fields(snap).get(name))
        if x is not None:
            return x
        if period is None:
            return None
    return _hist(snap, name).get(str(period)[:10])


def _r(x: Optional[float], n: int = 6) -> Optional[float]:
    return None if x is None else round(float(x), n)


# ---------------------------------------------------------------------------
# Stage 11 — normalised earnings
# ---------------------------------------------------------------------------
def _tax_rate(snap: dict, period: Optional[str]) -> tuple[Optional[float], str]:
    tax, pre = _val(snap, "tax", period), _val(snap, "pretax_income", period)
    lo, hi = ASSUMED["tax_rate_bounds"]
    if tax is not None and pre and pre > 0:
        r = tax / pre
        if lo <= r <= hi:
            return r, "effective (tax ÷ pretax income)"
    tr = _val(snap, "tax_rate", period)
    if tr is not None and lo <= tr <= hi:
        return tr, "snapshot tax_rate field (effective rate out of range or missing)"
    return None, "no usable tax rate"


def _normalise_one(snap: dict, period: Optional[str]) -> dict:
    """{reported, unusual, tax_effect, tax_effect_basis, normalised, why}."""
    rep = _val(snap, "net_income_common", period)
    if rep is None:
        return {"normalised": dn("net_income_common (reported PAT) missing"),
                "reported": None}
    unusual = _val(snap, "unusual_items", period)
    if unusual is None:
        return {"reported": rep, "unusual": None, "tax_effect": None,
                "tax_effect_basis": "no unusual-items line (ASSUMED none)",
                "normalised": rep, "basis": "reported_as_normalised"}
    te = _extra(snap, "tax_effect_unusual", period)
    if te is not None:
        basis = "exact (source 'Tax Effect Of Unusual Items')"
    else:
        rate, why = _tax_rate(snap, period)
        if rate is None:
            return {"reported": rep, "unusual": unusual, "tax_effect": None,
                    "normalised": dn("tax effect of unusual items: no exact line and "
                                     "no usable tax rate"), "basis": None}
        te = unusual * rate
        basis = (f"estimated at the effective tax rate {rate:.4f} (ASSUMED; {why})")
    return {"reported": rep, "unusual": unusual, "tax_effect": te,
            "tax_effect_basis": basis, "normalised": rep - unusual + te,
            "basis": "computed"}


def normalised_earnings(snap: dict) -> dict:
    """Stage 11 — reported PAT with the one-offs stripped, tax-effected,
    cross-checked against Yahoo's own Normalized Income."""
    snap = snap or {}
    period = snap.get("period_end")
    one = _normalise_one(snap, None)
    out: dict = {"ticker": snap.get("ticker"), "period_end": period,
                 "reported_pat": one.get("reported") if one.get("reported") is not None
                 else dn("net_income_common (reported PAT) missing"),
                 "adjustments": [], "assumed": dict(ASSUMED)}

    unusual = one.get("unusual")
    if unusual is not None:
        out["adjustments"].append({
            "amount": unusual, "applied": True, "period_end": period,
            "reason": "one-offs the source groups as unusual (pre-tax) — subtracted",
            "source": "statements 'Total Unusual Items'"})
    if one.get("tax_effect") is not None:
        out["adjustments"].append({
            "amount": one["tax_effect"], "applied": True, "period_end": period,
            "reason": "tax on those one-offs — added back",
            "source": one.get("tax_effect_basis")})
    comp_note = ("component of Total Unusual Items — shown, NOT subtracted again")
    sic = num(_fields(snap).get("special_income_charges"))
    if sic is not None:
        out["adjustments"].append({"amount": sic, "applied": False, "period_end": period,
                                   "reason": comp_note,
                                   "source": "statements 'Special Income Charges'"})
    skipped = []
    for key, label in (("restructuring", "Restructuring And Mergern Acquisition"),
                       ("write_off", "Write Off")):
        v = _extra(snap, key)
        if v is None:
            skipped.append(key)
            continue
        out["adjustments"].append({"amount": v, "applied": False, "period_end": period,
                                   "reason": comp_note, "source": f"statements '{label}'"})
    if skipped:
        out["components_not_carried"] = (
            f"{', '.join(skipped)} not carried by the snapshot (not in SNAPSHOT_FIELDS; "
            f"read from snap['extra'] when present) — skipped, never assumed 0")

    norm = one.get("normalised")
    out["normalised_pat"] = norm
    out["tax_effect_basis"] = one.get("tax_effect_basis")
    ynorm = num(_fields(snap).get("normalized_income_yahoo"))
    out["yahoo_normalized"] = ynorm if ynorm is not None else \
        dn("normalized_income_yahoo missing from the snapshot")
    nn = num(norm)
    if nn is not None and ynorm:
        diff = (nn / ynorm - 1) * 100
        out["yahoo_diff_pct"] = round(diff, 4)
        out["matches_yahoo"] = abs(diff) <= ASSUMED["yahoo_match_tol_pct"]
    else:
        out["matches_yahoo"] = dn("needs both our normalised PAT and Yahoo's")

    if nn is not None:
        d = num(_fields(snap).get("diluted_shares_avg"))
        b = num(_fields(snap).get("basic_shares_avg"))
        if d:
            out["normalised_eps"], out["eps_shares_basis"] = nn / d, "diluted average shares"
        elif b:
            out["normalised_eps"], out["eps_shares_basis"] = nn / b, \
                "basic average shares (diluted missing)"
        else:
            out["normalised_eps"] = dn("no average share count in the snapshot")
            out["eps_shares_basis"] = None
    else:
        out["normalised_eps"] = dn("normalised PAT is DATA_NEEDED")

    hist: dict[str, float] = {}
    for p in sorted(_hist(snap, "net_income_common"), reverse=True):
        h = _normalise_one(snap, p)
        x = num(h.get("normalised"))
        if x is not None:
            hist[p] = x
    if nn is not None and period:
        hist[str(period)[:10]] = nn
    out["history"] = dict(sorted(hist.items(), reverse=True))

    if is_dn(norm):
        out["status"] = norm
    else:
        out["status"] = one.get("basis") or "computed"
    return out


# ---------------------------------------------------------------------------
# Stage 12 — earnings quality + balance sheet
# ---------------------------------------------------------------------------
def _ratio(a: Optional[float], b: Optional[float], need: str,
           mult: float = 1.0) -> object:
    if a is None or b is None:
        return dn(f"needs {need}")
    if b == 0:
        return dn(f"{need}: denominator is zero")
    return a / b * mult


def _band_score(v: float, zero_at: float, full_at: float) -> float:
    if full_at == zero_at:
        return 0.0
    s = (v - zero_at) / (full_at - zero_at) * 100
    return max(0.0, min(100.0, s))


def quality(snap: dict, route_family: str = "",
            thresholds: Optional[dict] = None, lender: Optional[bool] = None) -> dict:
    """Stage 12 — earnings quality and balance-sheet stress. For a lender
    the leverage/coverage/working-capital lines are DATA_NEEDED and so is
    the score.

    `lender` (VAL-12b, owner 2026-10-21): the caller says whether the stock
    is a lender (pipeline: route method bank / NBFC). None = the old rule,
    every route_family "book" name — which wrongly took power utilities in."""
    snap = snap or {}
    f = _fields(snap)
    g = lambda k: num(f.get(k))                                   # noqa: E731
    # `thresholds` = Den draft (owner order 2026-10-14) by name; the module
    # defaults never change.
    th = dict(ASSUMED["thresholds"])
    for k, v in (thresholds or {}).items():
        if k in th and num(v) is not None:
            th[k] = float(v)
    lender = (route_family == "book") if lender is None else bool(lender)
    flags: list[str] = []
    notes: list[str] = []

    pat, ocf, fcf, rev = g("net_income_common"), g("ocf"), g("fcf"), g("revenue")
    # ---- cash conversion (latest + 3-year list) --------------------------
    if pat is not None and pat <= 0:
        cc = dn("PAT not positive — cash conversion not meaningful")
    else:
        cc = _ratio(ocf, pat, "ocf and net_income_common")
    pat_h, ocf_h = _hist(snap, "net_income_common"), _hist(snap, "ocf")
    cc3 = []
    for p in sorted(set(pat_h) & set(ocf_h), reverse=True)[:3]:
        v = ocf_h[p] / pat_h[p] if pat_h[p] > 0 else None
        cc3.append({"period_end": p, "value": _r(v)})
    weak3 = len(cc3) == 3 and all(x["value"] is not None and
                                  x["value"] < th["cash_conversion_min"] for x in cc3)
    if weak3 and not lender:
        flags.append(f"cash conversion below {th['cash_conversion_min']} in each of the "
                     f"last 3 years")
    if lender:
        notes.append("lender: OCF swings with deposits and loans — cash conversion is "
                     "shown, not flagged")

    fcf_margin = _ratio(fcf, rev, "fcf and revenue")
    if fcf is not None and fcf < 0 and not lender:
        flags.append("free cash flow negative in the latest year")

    # ---- working capital ---------------------------------------------------
    recv, inv, pay = g("receivables"), g("inventory"), g("payables")
    if lender:
        rd = dn(ASSUMED["lender_note"])
        wcd = dn(ASSUMED["lender_note"])
        wc_basis = None
    else:
        rd = _ratio(recv, rev, "receivables and revenue", 365.0)
        if recv is None or pay is None or rev is None:
            wcd = dn("needs receivables, payables and revenue")
            wc_basis = None
        elif rev == 0:
            wcd = dn("revenue is zero")
            wc_basis = None
        else:
            wcd = (recv + (inv or 0.0) - pay) / rev * 365.0
            wc_basis = ("receivables + inventory − payables" if inv is not None else
                        "receivables − payables (no inventory line in the snapshot)")

    # ---- leverage + coverage ----------------------------------------------
    debt, cash, eq = g("total_debt"), g("cash"), g("equity")
    sti = _extra(snap, "short_term_investments")
    ebitda, ebit, intr = g("ebitda"), g("ebit"), g("interest_expense")
    if lender:
        nde = dn(ASSUMED["lender_note"])
        icov = dn(ASSUMED["lender_note"])
        de = dn(ASSUMED["lender_note"])
        net_debt = dn(ASSUMED["lender_note"])
        nd_basis = None
    else:
        if debt is None or cash is None:
            net_debt = dn("needs total_debt and cash")
            nd_basis = None
        else:
            net_debt = debt - cash - (sti or 0.0)
            nd_basis = ("debt − cash − short-term investments" if sti is not None else
                        "debt − cash (short-term investments not carried by the snapshot)")
        if is_dn(net_debt):
            nde = net_debt
        elif ebitda is None:
            nde = dn("needs ebitda")
        elif ebitda <= 0:
            nde = dn("EBITDA not positive — net debt/EBITDA not meaningful")
        else:
            nde = net_debt / ebitda
        if intr is not None and intr == 0:
            icov = dn("no interest expense — cover not meaningful (no debt service)")
        else:
            icov = _ratio(ebit, intr, "ebit and interest_expense")
        de = _ratio(debt, eq, "total_debt and equity")
        if num(nde) is not None and nde > th["net_debt_ebitda_max"]:
            flags.append(f"net debt/EBITDA {nde:.2f} above {th['net_debt_ebitda_max']:g}")
        if num(icov) is not None and icov < th["interest_cover_min"]:
            flags.append(f"interest cover {icov:.2f} below {th['interest_cover_min']:g}")
    at = _ratio(rev, g("total_assets"), "revenue and total_assets")

    comps = {"cash_conversion": cc, "fcf_margin": fcf_margin, "net_debt_ebitda": nde,
             "interest_cover": icov, "working_capital_days": wcd, "debt_equity": de}
    if lender:
        score = dn(ASSUMED["lender_note"])
        score_components = {}
    else:
        w = ASSUMED["score_weights"]
        bands = ASSUMED["score_bands"]
        tot_w = sum(w.values())
        got_w, acc = 0.0, 0.0
        score_components = {}
        for k, v in comps.items():
            x = num(v)
            if x is None:
                score_components[k] = None
                continue
            s = _band_score(x, *bands[k])
            score_components[k] = round(s, 2)
            got_w += w[k]
            acc += w[k] * s
        if got_w < tot_w / 2:
            score = dn("fewer than half the score weight computed — "
                       + ", ".join(k for k, v in score_components.items() if v is None))
        else:
            score = round(acc / got_w, 2)

    def _out(v):
        return v if is_dn(v) else _r(v)

    out = {"ticker": snap.get("ticker"), "period_end": snap.get("period_end"),
           "route_family": route_family or None,
           "cash_conversion": _out(cc), "cash_conversion_3y": cc3,
           "fcf_margin": _out(fcf_margin), "receivable_days": _out(rd),
           "working_capital_days": _out(wcd), "working_capital_basis": wc_basis,
           "net_debt": _out(net_debt), "net_debt_basis": nd_basis,
           "net_debt_ebitda": _out(nde), "interest_cover": _out(icov),
           "debt_equity": _out(de), "asset_turnover": _out(at),
           "flags": flags, "notes": notes, "score": score,
           "score_components": score_components,
           "score_weights": dict(ASSUMED["score_weights"]),
           "assumed": dict(ASSUMED)}
    values = [cc, fcf_margin, rd, wcd, nde, icov, de, at]
    if all(is_dn(v) for v in values):
        out["status"] = dn("no quality input in the snapshot")
    else:
        out["status"] = "computed"
    return out


__all__ = ["ASSUMED", "normalised_earnings", "quality"]
