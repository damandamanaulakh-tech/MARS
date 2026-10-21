"""Currency leg THROUGH EPS for every sector's shares (VALFIX batch 3, owner
order 2026-10-21: "through EPS, USD + EUR" … "its just not about tcs or one
share, it will be used on all sector shares").

    EPS × (1 + s_USD × ΔUSD/INR% × pt + s_EUR × ΔEUR/INR% × pt)

    ΔFX = the rupee's move since the selection date (FRED DEXINUS; EUR/INR =
          DEXINUS × DEXUSEU on the dates both carry a value); + = rupee weaker
    pt  = pass-through margin points ÷ EBITDA margin %, read as the EPS %
          one 1% rupee move gives per 100% of revenue in that currency
          (100 × pp ÷ m: TCS 0.25 ÷ 27.07 = 0.924%; PAT is a fixed share of
          EBITDA in the owner's sheet, E23, so EPS moves by the EBITDA %)
    s   = the revenue share in that currency, from the FIRST tier that has it:
          (a) the owner's typed share (Den inputs table: usd_share_pct /
              eur_share_pct)
          (b) the company's filed geographic split (data/geography_splits.csv,
              NSE results XBRL; Americas → USD, Europe → EUR, UK → GBP shown
              but not used, India → INR, other → USD by the owner's rule)
          (c) BRSR "exports as % of turnover" (data/geography_brsr_exports.csv)
              counted as USD, the EUR part from the sector default's EUR:USD
              ratio — exports from India ONLY, said on every row
          (d) the owner's SECTOR default (Den table, settings doc
              valuation_sector_fx; a negative share = an importer)
          (e) DATA_NEEDED — the leg is counted 0 and the row says so.
          Never a built-in default share.

The target moves with EPS at an unchanged multiple (target × the EPS
factor), the study's S2 (REPORT_market_fx.md). FX never multiplies the
PRICE: once the market and sector legs are in, prices show no FX effect.
The owner's Tata sheet keeps its own E101 rows cell for cell (scenarios.py);
this leg replaces the IT-only E101 rupee row in the LIVE chain only.

PURE: no network, no web import; the two committed files are read by small
cached loaders a test can point elsewhere. Display only — no gate, stop or
size reads this (boundary test).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from trading.valuation import num

_DATA = Path(__file__).resolve().parents[2] / "data"
GEO_CSV = _DATA / "geography_splits.csv"
BRSR_CSV = _DATA / "geography_brsr_exports.csv"

ASSUMED: Dict[str, dict] = {
    "pass_through_pp": {
        "value": 0.25,
        "source": "the owner's Tata Tech sheet B152 (+0.25 points of EBITDA margin per 1% "
                  "rupee move) — owner 2026-10-21: one per sector, editable in the Den, "
                  "starting here for every sector"},
    "pt_rule": {
        "value": "pt (EPS % per 1% move per 100% share) = 100 × pass-through margin points "
                 "÷ EBITDA margin %; EPS % = share × ΔFX% × pt",
        "source": "REPORT_market_fx.md (study 1, 2026-10-20), structure S2: FX_EPS"},
    "other_counts_as_usd": {
        "value": True,
        "source": "owner rule 2026-10-21: a filed 'other' / 'outside India' line counts as USD"},
    "gbp_not_used": {
        "value": True,
        "source": "owner 2026-10-21: a UK line is reported, not used (no GBP leg)"},
    "brsr_counts_as_usd": {
        "value": "exports % → USD; EUR part = exports × the sector default's EUR ÷ (USD + EUR) "
                 "when the sector default holds both shares ≥ 0, else all USD",
        "source": "owner answer 2026-10-21 (FX share tiers)"},
    "target_scaling": {
        "value": "target × the EPS factor (the route's multiple held fixed)",
        "source": "REPORT_market_fx.md S2: FX enters through EPS, never the price"},
}

#: KPITTECH's XBRL regions are gross of ₹1,869 cr unallocated inter-segment
#: revenue (22.5%), so its filed shares come out wrong; the contract uses its
#: own FY26 investor deck (filed on NSE) instead — read 2026-10-21.
MEASURED: Dict[str, dict] = {
    "KPITTECH": {
        "fy_end": "2026-03-31", "basis": "investor deck (FY26 revenue, USD mn)",
        "lines": [("US", "USD", 27.4), ("Europe", "EUR", 48.0), ("Asia", "OTHER", 24.6)],
        "source_url": ("https://nsearchives.nseindia.com/corporate/MeghanaPhadnis_"
                       "06052026134642_CovletInvestorPresentationMar26_Signed.pdf"),
        "note": "KPIT investor presentation Mar-26: US 198.81 / Europe 348.04 / Asia 177.95 "
                "USD mn — the XBRL regions are gross of unallocated inter-segment revenue",
    },
}

#: Input guards (ASSUMED): a typed or sector-default share must sit in this
#: range (negative = an importer's net cost in that currency); a pass-through
#: above 5 margin points per 1% move (20× the sheet's B152) is refused as a typo.
SHARE_BOUNDS = (-100.0, 100.0)
PASS_THROUGH_BOUNDS = (0.0, 5.0)

TIER_TEXT = {
    "typed": "your typed share (inputs table)",
    "filed": "the company's filed geographic split (NSE results XBRL)",
    "brsr": "BRSR exports as % of turnover — exports from India only",
    "sector": "your sector default (Den table)",
    "none": "DATA_NEEDED — no typed share, no filed split, no BRSR export figure, "
            "no sector default",
}
BRSR_CAVEAT = "exports from India only"

_CACHE: dict = {}


def _file_key(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), 0, 0)


def _read(path: Path) -> list:
    key = _file_key(path)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    rows: list = []
    try:
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        rows = []
    _CACHE[key] = rows
    return rows


def _bare(sym: Any) -> str:
    s = str(sym or "").strip().upper()
    return s[:-3] if s.endswith((".NS", ".BO")) else s


def _split_from_lines(lines: List[tuple]) -> dict:
    b = {"USD": 0.0, "EUR": 0.0, "GBP": 0.0, "INR": 0.0, "OTHER": 0.0}
    for _label, bucket, pct in lines:
        if bucket in b and pct is not None:
            b[bucket] += float(pct)
    return {"usd_pct": round(b["USD"] + b["OTHER"], 2), "eur_pct": round(b["EUR"], 2),
            "gbp_pct": round(b["GBP"], 2), "inr_pct": round(b["INR"], 2),
            "usd_filed_pct": round(b["USD"], 2), "other_pct": round(b["OTHER"], 2)}


def filed_split(sym: Any, path: Optional[Path] = None) -> Optional[dict]:
    """The company's filed split, or None. A table that mixes business lines
    with regions (any BUSINESS_LINE row) gives no currency shares — its
    regions are shares of gross segment revenue with business lines left
    unassigned (fetch_geography.py's MIXED_BUSINESS_GEO status)."""
    s = _bare(sym)
    if s in MEASURED and path is None:
        m = MEASURED[s]
        return {**_split_from_lines(m["lines"]), "fy_end": m["fy_end"], "basis": m["basis"],
                "source_url": m["source_url"], "lines": [list(x) for x in m["lines"]],
                "note": m["note"]}
    rows = [r for r in _read(path or GEO_CSV) if _bare(r.get("symbol")) == s]
    if not rows or any(str(r.get("bucket")) == "BUSINESS_LINE" for r in rows):
        return None
    lines = [(r.get("region_label_as_filed") or "", str(r.get("bucket") or ""),
              num(r.get("share_pct_of_revenue"))) for r in rows]
    notes = sorted({str(r.get("mapping_note") or "").strip() for r in rows} - {""})
    return {**_split_from_lines(lines), "fy_end": rows[0].get("fy_end") or "",
            "basis": rows[0].get("basis") or "", "source_url": rows[0].get("source_url") or "",
            "lines": [list(x) for x in lines], "note": " | ".join(notes)[:300]}


def brsr_exports(sym: Any, path: Optional[Path] = None) -> Optional[dict]:
    """BRSR "exports as % of turnover" (status FOUND only), or None. A filed
    0 is a real 0 (a bank exporting nothing), not a missing value."""
    s = _bare(sym)
    for r in _read(path or BRSR_CSV):
        if _bare(r.get("symbol")) != s or str(r.get("status")) != "FOUND":
            continue
        v = num(r.get("export_pct_of_turnover"))
        if v is None:
            return None
        return {"export_pct": v, "fy": r.get("fy") or "",
                "boundary": r.get("reporting_boundary") or "",
                "source_url": r.get("source_url") or ""}
    return None


def _sector_row(sector: str, table: Optional[dict]) -> dict:
    row = (table or {}).get(sector) if sector else None
    return row if isinstance(row, dict) else {}


def pass_through(sector: str, table: Optional[dict]) -> tuple[float, str]:
    """(pass-through margin points, source) — the sector's own Den value, else
    the sheet's B152."""
    v = num(_sector_row(sector, table).get("pass_through_pp"))
    if v is not None:
        return float(v), f"your sector table ({sector})"
    return float(ASSUMED["pass_through_pp"]["value"]), "the sheet's B152 (0.25), sector default"


def resolve_shares(sym: Any, typed: Optional[dict], sector: str,
                   sector_table: Optional[dict]) -> dict:
    """{tier, usd_pct, eur_pct, source, caveat, detail, lines} — the FIRST
    tier that holds a share. usd_pct / eur_pct are None only on tier
    'none' (DATA_NEEDED)."""
    typed = typed if isinstance(typed, dict) else {}
    t_usd, t_eur = num(typed.get("usd_share_pct")), num(typed.get("eur_share_pct"))
    if t_usd is not None or t_eur is not None:
        miss = [c for c, v in (("USD", t_usd), ("EUR", t_eur)) if v is None]
        return {"tier": "typed", "usd_pct": t_usd if t_usd is not None else 0.0,
                "eur_pct": t_eur if t_eur is not None else 0.0,
                "source": TIER_TEXT["typed"], "caveat": "",
                "detail": (f"{' and '.join(miss)} not typed — counted 0" if miss else ""),
                "lines": []}
    fs = filed_split(sym)
    if fs is not None:
        parts = [f"USD {fs['usd_filed_pct']:g}%"] if fs["usd_filed_pct"] else []
        if fs["other_pct"]:
            parts.append(f"other {fs['other_pct']:g}% (counted as USD)")
        if fs["eur_pct"]:
            parts.append(f"EUR {fs['eur_pct']:g}%")
        if fs["gbp_pct"]:
            parts.append(f"GBP {fs['gbp_pct']:g}% reported, not used")
        if fs["inr_pct"]:
            parts.append(f"India {fs['inr_pct']:g}%")
        return {"tier": "filed", "usd_pct": fs["usd_pct"], "eur_pct": fs["eur_pct"],
                "source": f"{TIER_TEXT['filed']}, FY to {fs['fy_end']} ({fs['basis']})",
                "caveat": "", "detail": " · ".join(parts), "lines": fs["lines"],
                "url": fs["source_url"], "note": fs.get("note") or ""}
    sd = _sector_row(sector, sector_table)
    s_usd, s_eur = num(sd.get("usd_share_pct")), num(sd.get("eur_share_pct"))
    br = brsr_exports(sym)
    if br is not None:
        e = float(br["export_pct"])
        if s_usd is not None and s_eur is not None and s_usd >= 0 and s_eur >= 0 \
                and (s_usd + s_eur) > 0:
            eur = e * s_eur / (s_usd + s_eur)
            ratio = f"EUR part by your {sector} default EUR {s_eur:g} : USD {s_usd:g}"
        else:
            eur = 0.0
            ratio = "no sector default ratio — all counted as USD"
        if (s_usd is not None and s_usd < 0) or (s_eur is not None and s_eur < 0):
            # your order puts BRSR first; an importer's net cost is not an export
            ratio += (f"; your {sector} default is an importer's (USD {s_usd if s_usd is not None else 0:g} "
                      f"/ EUR {s_eur if s_eur is not None else 0:g}) — not used, BRSR comes first "
                      "in your tier order")
        eur = round(eur, 2)                 # the two parts add back to the filed figure
        return {"tier": "brsr", "usd_pct": round(e - eur, 2), "eur_pct": eur,
                "source": f"{TIER_TEXT['brsr']}, FY {br['fy']} ({br['boundary']})",
                "caveat": BRSR_CAVEAT,
                "detail": f"exports {e:g}% of turnover; {ratio}", "lines": [],
                "url": br["source_url"]}
    if s_usd is not None or s_eur is not None:
        return {"tier": "sector", "usd_pct": s_usd if s_usd is not None else 0.0,
                "eur_pct": s_eur if s_eur is not None else 0.0,
                "source": f"{TIER_TEXT['sector']}: {sector}", "caveat": "",
                "detail": "", "lines": []}
    return {"tier": "none", "usd_pct": None, "eur_pct": None, "source": TIER_TEXT["none"],
            "caveat": "", "detail": "", "lines": []}


# ------------------------------------------------------------------ moves

def _clean(s: Optional[pd.Series]) -> Optional[pd.Series]:
    if s is None or len(s) == 0:
        return None
    try:
        out = pd.Series(pd.to_numeric(s, errors="coerce").to_numpy(),
                        index=pd.to_datetime(s.index, errors="coerce"))
    except (TypeError, ValueError):
        return None
    out = out[out.index.notna()].dropna().sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out if len(out) else None


def _move(s: Optional[pd.Series], start: Any, name: str, end: Any = None) -> dict:
    s = _clean(s)
    if s is None:
        return {"status": "DATA_NEEDED", "why": f"no {name} series stored", "pct": None}
    try:
        ts = pd.Timestamp(str(start)[:10])
    except (TypeError, ValueError):
        return {"status": "DATA_NEEDED", "why": "selection date unreadable", "pct": None}
    if end:
        try:
            s = s[s.index <= pd.Timestamp(str(end)[:10])]
        except (TypeError, ValueError):
            pass
        if not len(s):
            return {"status": "DATA_NEEDED", "pct": None, "why": f"no {name} rate by {end}"}
    part = s[s.index <= ts]
    if part.empty:
        return {"status": "DATA_NEEDED", "pct": None,
                "why": f"{name} series starts {s.index[0].date().isoformat()}, after the "
                       f"selection date"}
    a_d, a = part.index[-1].date().isoformat(), float(part.iloc[-1])
    b_d, b = s.index[-1].date().isoformat(), float(s.iloc[-1])
    if a <= 0:
        return {"status": "DATA_NEEDED", "why": f"{name}: non-positive rate", "pct": None}
    return {"status": "OK", "start": a, "start_date": a_d, "end": b, "end_date": b_d,
            "pct": (b / a - 1.0) * 100.0}


def fx_moves(usdinr: Optional[pd.Series], usdeur: Optional[pd.Series],
             selection: Any, end: Any = None) -> dict:
    """{usd, eur}: the rupee's move against each since the selection date
    (the rate on or before it → the newest stored rate on or before `end`,
    the row's own "now"; each dated). EUR/INR = USD/INR × USD-per-EUR on the
    dates both carry a value."""
    out = {"usd": _move(usdinr, selection, "USD/INR (FRED DEXINUS)", end)}
    a, b = _clean(usdinr), _clean(usdeur)
    if a is None or b is None:
        out["eur"] = {"status": "DATA_NEEDED", "pct": None,
                      "why": "no USD/INR or USD-per-EUR series stored (FRED DEXINUS × DEXUSEU)"}
    else:
        both = pd.concat([a.rename("u"), b.rename("e")], axis=1, join="inner").dropna()
        eurinr = both["u"] * both["e"] if len(both) else None
        out["eur"] = _move(eurinr, selection, "EUR/INR (FRED DEXINUS × DEXUSEU)", end)
    return out


def fx_eps(eps: Optional[float], margin_pct: Optional[float], pass_through_pp: float,
           usd_pct: Optional[float], eur_pct: Optional[float],
           d_usd_pct: Optional[float], d_eur_pct: Optional[float]) -> dict:
    """The EPS factor. Every missing input counts its part 0 and is named —
    the leg is never silently 1.0."""
    out: Dict[str, Any] = {"status": "OK", "factor": 1.0, "counted": True, "why": [],
                           "pt": None, "usd_part_pct": 0.0, "eur_part_pct": 0.0,
                           "eps": num(eps), "eps_adjusted": num(eps)}
    if usd_pct is None and eur_pct is None:
        out.update(status="DATA_NEEDED", counted=False,
                   why=["no currency share on any tier — the leg is counted 0"])
        return out
    if (num(usd_pct) or 0.0) == 0.0 and (num(eur_pct) or 0.0) == 0.0:
        # a 0% share (a bank's BRSR 0) is a real 0: no currency effect, counted
        out["note"] = "0% of revenue in USD or EUR on this tier — no currency effect"
        out["total_pct"] = 0.0
        return out
    m = num(margin_pct)
    if m is None or m <= 0:
        out.update(status="DATA_NEEDED", counted=False,
                   why=["EBITDA margin missing or not positive — pt cannot be read; "
                        "the leg is counted 0"])
        return out
    # pt in EPS % per 1% rupee move per 100% share: +pp margin points on a
    # margin of m% lifts EBITDA (so EPS, E23) by pp / m as a FRACTION, i.e.
    # 100 × pp / m percent — TCS: 100 × 0.25 / 27.07 = 0.924 (study S2)
    pt = 100.0 * float(pass_through_pp) / m
    out["pt"] = pt
    why: List[str] = []
    for cur, share, move, key in (("USD", usd_pct, d_usd_pct, "usd_part_pct"),
                                  ("EUR", eur_pct, d_eur_pct, "eur_part_pct")):
        sh = num(share) or 0.0
        if sh == 0.0:
            continue
        mv = num(move)
        if mv is None:
            why.append(f"{cur}/INR move DATA_NEEDED — the {cur} part is counted 0")
            continue
        out[key] = sh / 100.0 * mv * pt
    total = out["usd_part_pct"] + out["eur_part_pct"]
    out["factor"] = 1.0 + total / 100.0
    out["total_pct"] = total
    if out["eps"] is not None:
        out["eps_adjusted"] = out["eps"] * out["factor"]
    out["why"] = why
    if why and total == 0.0:
        out.update(status="DATA_NEEDED", counted=False)
    return out


def parse_sector_table(form_rows: Dict[str, dict]) -> tuple[dict, list]:
    """{sector: {usd_share_pct, eur_share_pct, pass_through_pp}} from raw
    form strings → (clean table, errors). Blank = not set. A bad value
    refuses the whole table (the caller stores nothing)."""
    import math
    clean: dict = {}
    errors: list = []
    for sector, raw in (form_rows or {}).items():
        row: dict = {}
        for key, lo_hi in (("usd_share_pct", SHARE_BOUNDS), ("eur_share_pct", SHARE_BOUNDS),
                           ("pass_through_pp", PASS_THROUGH_BOUNDS)):
            txt = str((raw or {}).get(key) or "").strip().replace(",", "")
            if not txt:
                continue
            try:
                v = float(txt)
            except ValueError:
                errors.append(f"{sector} {key}: '{txt[:20]}' is not a number")
                continue
            lo, hi = lo_hi
            if not math.isfinite(v) or v < lo or v > hi:
                errors.append(f"{sector} {key}: {txt[:20]} is outside {lo:g}–{hi:g}")
                continue
            row[key] = v
        u, e = row.get("usd_share_pct"), row.get("eur_share_pct")
        if (u or 0.0) + (e or 0.0) > 100.0:
            errors.append(f"{sector}: USD + EUR share above 100%")
        if row:
            clean[sector] = row
    return clean, errors
