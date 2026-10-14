"""MARS valuation chain — stage 10: SHARE CAPITAL + DILUTION BRIDGE.

Owner order 2026-10-14 ("lets hit the formulas work completion"); task book
stage 10 ("share count today, weighted, diluted; every change explained by
a corporate action or flagged"). Money impact: NONE — display only; the
engine never reads it (package boundary test).

What it does
------------
* `parse_corporate_action(subject)` — NSE corporate-action subject text →
  {type split|bonus|rights|buyback|dividend|other, ratio, detail,
  components}. A subject it cannot read is "other" with ratio None — a
  ratio is NEVER guessed from an unparseable string.
* `load_corporate_actions(symbol)` — that symbol's rows from the committed
  data/corporate_actions_2010_2026.csv (injectable).
* `share_bridge(ticker, snap, ca_rows, shares_csv_row)` — today's basic,
  weighted and diluted counts, the dilutive gap, the per-period counts, and
  a reconciliation of every consecutive period pair against the splits and
  bonuses whose ex-date falls between them.

Two honest readings the bridge allows, and says which it used
--------------------------------------------------------------
A split/bonus between two period ends either shows up as a jump of exactly
its factor ("unadjusted" source) or not at all, because the source restated
every earlier period onto today's share basis ("restated_by_source" —
Yahoo does this; HDFCBANK's 2025 1:1 bonus is invisible in its series). A
change that matches NEITHER is unexplained. Buybacks, issuance and rights
never get an invented factor: a non-zero cash-flow line is named in the
note, the pair stays unexplained if it is outside tolerance (a merger by
scheme of amalgamation is not a split — HDFCBANK FY23→FY24 is the case).

ASSUMED (printable, `ASSUMED` dict)
-----------------------------------
* reconcile_tol_pct 3.0 — a pair within ±3% of its explained factor is
  reconciled (task book; ESOP allotments and small buybacks live inside it);
* diluted_below_basic_tol_pct 1.0 — a diluted average below basic by more
  than this is flagged a source inconsistency;
* csv_check_tol_pct 2.0 — data/shares_outstanding.csv vs the balance-sheet
  count;
* actions_cap 40 — newest first.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from trading.valuation import dn, is_dn, num

_ROOT = Path(__file__).resolve().parents[2]
_CA_CSV = _ROOT / "data" / "corporate_actions_2010_2026.csv"
_SHARES_CSV = _ROOT / "data" / "shares_outstanding.csv"

ASSUMED: dict = {
    "reconcile_tol_pct": 3.0,
    "reconcile_tol_source": "task book stage 10 (ASSUMED): ESOP allotments and small buybacks fit inside ±3%",
    "diluted_below_basic_tol_pct": 1.0,
    "csv_check_tol_pct": 2.0,
    "actions_cap": 40,
    "factor_rule": ("split = old face value ÷ new face value; bonus a:b = 1 + a/b "
                    "(a new for every b held); rights/buyback/issuance carry NO factor"),
    "restated_basis": ("a split/bonus whose factor does not show in the series is "
                       "read as 'restated_by_source' when the raw change is within "
                       "tolerance of 1 (Yahoo restates history onto today's basis)"),
}

_NUM = r"(\d+(?:\.\d+)?)"


def _f(x: str) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _parse_split(s: str) -> Optional[float]:
    m = re.search(r"split(.*)$", s)
    if not m:
        return None
    tail = m.group(1)
    mm = re.search(_NUM + r"[^\d]*?\bto\b[^\d]*?" + _NUM, tail)
    if not mm:
        return None
    a, b = _f(mm.group(1)), _f(mm.group(2))
    if a is None or b is None or a <= b:
        return None
    return a / b


# Words allowed between the action word and its a:b ratio. A WHITELIST, so
# "Bonus Debentures 1:1", "Bonus NCRPS 1:10", "Bonus Preference Shares 21:1"
# and "Bonus 1 DVR : 10" never read as an equity bonus.
_FILLER = r"(?:\s*[-:]?\s*(?:issue|shares?|in the ratio of|at|eq))*"


def _parse_ab(word: str, s: str) -> Optional[float]:
    m = re.search(word + _FILLER + r"\s*[-:]?\s*" + _NUM + r"\s*:\s*" + _NUM, s)
    if not m:
        return None
    a, b = _f(m.group(1)), _f(m.group(2))
    if a is None or b is None:
        return None
    return a / b


def parse_corporate_action(subject: str) -> dict:
    """One NSE CA subject → {type, ratio, detail, components[, premium,
    amount]}. Primary type order: split > bonus > rights > buyback >
    dividend > other; `components` lists every action the text carries
    (a "Bonus 1:1 And Face Value Split" row moves the count by both)."""
    detail = re.sub(r"\s+", " ", str(subject or "")).strip()
    s = detail.lower()
    comps: list[dict] = []
    extra: dict = {}

    if "split" in s:
        r = _parse_split(s)
        comps.append({"type": "split" if r else "other", "ratio": r,
                      "note": None if r else "split text without a readable from/to face value"})
    if "bonus" in s:
        r = _parse_ab(r"bonus", s)
        comps.append({"type": "bonus" if r else "other", "ratio": r,
                      "note": None if r else "bonus text without a readable a:b"})
    if re.search(r"\brights?\b", s):
        r = _parse_ab(r"rights?", s)
        comps.append({"type": "rights" if r else "other", "ratio": r,
                      "note": None if r else "rights text without a readable a:b"})
        pm = re.search(r"premium\s*(?:of\s*)?(?:rs|re)?\.?\s*" + _NUM, s)
        if pm:
            extra["premium"] = float(pm.group(1))
    if re.search(r"buy\s*-?\s*back", s):
        comps.append({"type": "buyback", "ratio": None, "note": None})
    if re.search(r"\bdiv(?:idend)?s?\b", s):
        comps.append({"type": "dividend", "ratio": None, "note": None})
        am = re.search(r"\bdiv(?:idend)?s?\b[^\d]*?(?:rs|re)\.?\s*" + _NUM, s)
        if am:
            extra["amount"] = float(am.group(1))
    if re.search(r"consolidat|capital reduction|reduction of capital", s):
        comps.append({"type": "other", "ratio": None,
                      "note": "consolidation / capital reduction — share-count change "
                              "not parsed; read the scheme"})

    out = {"type": "other", "ratio": None, "detail": detail}
    for t in ("split", "bonus", "rights", "buyback", "dividend"):
        hit = next((c for c in comps if c["type"] == t), None)
        if hit is not None:
            out["type"], out["ratio"] = t, hit["ratio"]
            break
    out["components"] = [{"type": c["type"], "ratio": c["ratio"]} for c in comps
                         if c["type"] != "other" or c["note"]]
    notes = [c["note"] for c in comps if c["note"]]
    if notes:
        out["note"] = "; ".join(notes)
    out.update(extra)
    return out


def share_factor(parsed: dict) -> float:
    """The share-count multiplier a parsed action implies: split ratio,
    bonus 1 + a/b; everything else 1.0 (never invented)."""
    f = 1.0
    for c in parsed.get("components") or [parsed]:
        r = num(c.get("ratio"))
        if r is None:
            continue
        if c.get("type") == "split":
            f *= r
        elif c.get("type") == "bonus":
            f *= 1.0 + r
    return f


# ---------------------------------------------------------------------------
# Loaders (injectable)
# ---------------------------------------------------------------------------
def _bare(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    for suf in (".NS", ".BO"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t


def load_corporate_actions(symbol: str, path: Path = _CA_CSV) -> list[dict]:
    """That symbol's raw rows from the committed CA file (one streamed pass)."""
    sym = _bare(symbol)
    out: list[dict] = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("symbol") or "").strip().upper() == sym:
                    out.append(dict(r))
    except OSError:
        return []
    return out


def load_shares_row(symbol: str, path: Path = _SHARES_CSV) -> Optional[dict]:
    """That symbol's row of data/shares_outstanding.csv, or None."""
    sym = _bare(symbol)
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("symbol") or "").strip().upper() == sym:
                    return dict(r)
    except OSError:
        return None
    return None


def _iso(d: str) -> Optional[str]:
    d = (d or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _series(snap: dict, name: str) -> dict[str, float]:
    h = ((snap or {}).get("history") or {}).get(name) or {}
    out: dict[str, float] = {}
    if isinstance(h, dict):
        for k, v in h.items():
            x = num(v)
            if x is not None:
                out[str(k)[:10]] = x
    return out


def _field(snap: dict, name: str):
    return ((snap or {}).get("fields") or {}).get(name)


def _cr(x: float) -> str:
    return f"₹{x / 1e7:,.0f} Cr"


def share_bridge(ticker: str, snap: dict, ca_rows: Optional[list] = None,
                 shares_csv_row: Optional[dict] = None,
                 tol_pct: Optional[float] = None) -> dict:
    """Stage 10 — the share count today and every change since, explained or
    flagged. `ca_rows=None` loads the committed CA file; pass [] for none.
    `shares_csv_row=None` loads data/shares_outstanding.csv; pass {} to skip."""
    sym = _bare(ticker)
    snap = snap or {}
    # `tol_pct` = Den draft (owner order 2026-10-14); None = module default.
    tol = float(tol_pct) if num(tol_pct) is not None else ASSUMED["reconcile_tol_pct"]

    basic_now = _field(snap, "shares_outstanding")
    weighted = _field(snap, "basic_shares_avg")
    diluted = _field(snap, "diluted_shares_avg")
    bn = num(basic_now)
    wn, dn_ = num(weighted), num(diluted)
    flags: list[str] = []

    if wn is not None and dn_ is not None:
        dilutive = dn_ - wn
        if dilutive < -abs(wn) * ASSUMED["diluted_below_basic_tol_pct"] / 100:
            flags.append("latest diluted average sits BELOW basic — source inconsistency")
    else:
        miss = [n for n, v in (("diluted_shares_avg", dn_), ("basic_shares_avg", wn)) if v is None]
        dilutive = dn(f"needs {' and '.join(miss)}")

    # ---- corporate actions -------------------------------------------------
    if ca_rows is None:
        ca_rows = load_corporate_actions(sym)
    parsed_all: list[dict] = []
    for r in ca_rows or []:
        p = parse_corporate_action(r.get("subject", ""))
        p["ex_date"] = _iso(r.get("exDate") or r.get("ex_date") or "")
        parsed_all.append(p)
    parsed_all.sort(key=lambda p: p.get("ex_date") or "", reverse=True)
    actions = parsed_all[: ASSUMED["actions_cap"]]

    # ---- per-period counts -------------------------------------------------
    so_h = _series(snap, "shares_outstanding")
    ba_h = _series(snap, "basic_shares_avg")
    da_h = _series(snap, "diluted_shares_avg")
    periods = sorted(set(so_h) | set(ba_h) | set(da_h))
    by_period = [{"period_end": p, "shares_outstanding": so_h.get(p),
                  "basic_avg": ba_h.get(p), "diluted_avg": da_h.get(p)} for p in periods]
    for row in by_period:
        b, d = row["basic_avg"], row["diluted_avg"]
        if b is not None and d is not None and \
                d < b * (1 - ASSUMED["diluted_below_basic_tol_pct"] / 100):
            flags.append(f"{row['period_end']}: diluted average {d:,.0f} below basic "
                         f"{b:,.0f} — source inconsistency (diluted series not restated?)")

    series_used, series = "shares_outstanding", so_h
    if len(so_h) < 2 and len(ba_h) >= 2:
        series_used, series = "basic_shares_avg", ba_h

    out: dict = {
        "ticker": sym, "basic_now": basic_now if basic_now is not None else
        dn("shares_outstanding missing from the snapshot"),
        "weighted": weighted if weighted is not None else dn("basic_shares_avg missing"),
        "diluted": diluted if diluted is not None else dn("diluted_shares_avg missing"),
        "dilutive_instruments": dilutive,
        "dilutive_pct": (round(dilutive / wn * 100, 3)
                         if not is_dn(dilutive) and wn else dn("needs both averages")),
        "esop_pool": dn("ESOP/convertible pool not held; annual report"),
        "by_period": by_period, "series_used": series_used,
        "actions": actions, "explained": {}, "unexplained_change_pct": {},
        "pairs": [], "flags": flags, "assumed": dict(ASSUMED),
    }

    # ---- cross-check against data/shares_outstanding.csv ------------------
    if shares_csv_row is None:
        shares_csv_row = load_shares_row(sym) or {}
    csv_n = num((shares_csv_row or {}).get("shares_outstanding"))
    if csv_n is not None and bn:
        diff = (csv_n / bn - 1) * 100
        out["csv_check"] = {"csv_shares": csv_n, "diff_pct": round(diff, 3),
                            "float_shares": num((shares_csv_row or {}).get("float_shares")),
                            "agrees": abs(diff) <= ASSUMED["csv_check_tol_pct"]}
        if abs(diff) > ASSUMED["csv_check_tol_pct"]:
            flags.append(f"shares_outstanding.csv differs from the balance sheet by {diff:+.1f}%")
    else:
        out["csv_check"] = dn("no data/shares_outstanding.csv row or no balance-sheet count")

    # ---- reconciliation ----------------------------------------------------
    ps = sorted(series)
    if len(ps) < 2:
        out["status"] = dn("no share history to reconcile" if not ps
                           else "one period only — no pair to reconcile")
        out["note"] = ("Share history is missing — the bridge cannot say whether the "
                       "count changed or why.")
        return out

    buyb = _series(snap, "buybacks")
    issu = _series(snap, "issuance")
    unexplained: list[str] = []
    notes: list[str] = []
    for p0, p1 in zip(ps, ps[1:]):
        s0, s1 = series[p0], series[p1]
        key = f"{p0}→{p1}"
        ratio = s1 / s0 if s0 else None
        window = [a for a in parsed_all if a.get("ex_date") and p0 < a["ex_date"] <= p1]
        factor = 1.0
        for a in window:
            factor *= share_factor(a)
        out["explained"][p1] = round(factor, 6)
        pair = {"from": p0, "to": p1, "shares_from": s0, "shares_to": s1,
                "explained_factor": round(factor, 6),
                "actions_in_window": [f"{a['ex_date']}: {a['detail']}" for a in window
                                      if a["type"] in ("split", "bonus", "rights", "buyback")
                                      or a.get("note")]}
        if ratio is None:
            pair.update({"change_pct": None, "basis": None,
                         "unexplained_change_pct": dn("zero share count")})
            out["pairs"].append(pair)
            unexplained.append(key)
            continue
        pair["change_pct"] = round((ratio - 1) * 100, 4)
        u_unadj = abs(ratio / factor - 1) * 100
        basis, u = "unadjusted", u_unadj
        if factor != 1.0:
            u_rest = abs(ratio - 1) * 100
            if u_rest < u_unadj:
                basis, u = "restated_by_source", u_rest
        pair["basis"] = basis
        pair["unexplained_change_pct"] = round(u, 4)
        pair["within_tol"] = u <= tol
        out["unexplained_change_pct"][key] = round(u, 4)

        cf_bits = []
        b1, i1 = buyb.get(p1), issu.get(p1)
        if b1:
            cf_bits.append(f"buyback line {_cr(abs(b1))}")
        if i1:
            cf_bits.append(f"issuance line {_cr(abs(i1))}")
        pair["cash_flow_lines"] = {"buybacks": b1, "issuance": i1}
        rights = [a for a in window if a["type"] == "rights"]
        bbk_ca = [a for a in window if a["type"] == "buyback"]
        if u <= tol:
            bits = [f"{key}: {pair['change_pct']:+.2f}% — within ±{tol:g}%"]
            if basis == "restated_by_source":
                bits.append(f"a split/bonus (×{factor:g}) falls in the window but the "
                            f"series does not jump — the source restated earlier periods "
                            f"onto today's basis")
            elif factor != 1.0:
                bits.append(f"explained by split/bonus ×{factor:g}")
            if bbk_ca:
                bits.append("buyback corporate action " +
                            ", ".join(a["ex_date"] for a in bbk_ca))
            if cf_bits:
                bits.append(" and ".join(cf_bits) + " non-zero")
            if len(bits) > 1:
                notes.append("; ".join(bits))
        else:
            unexplained.append(key)
            bits = [f"{key}: shares {pair['change_pct']:+.2f}% and no parsed split/bonus "
                    f"explains it (explained factor ×{factor:g}; unexplained "
                    f"{u:.1f}%) — a merger/scheme, QIP, preferential issue or a source "
                    f"error; review the filings"]
            if cf_bits:
                bits.append(" and ".join(cf_bits) + " non-zero in that period — it may "
                            "explain part; no factor invented")
            if rights:
                bits.append("rights issue in the window (" +
                            ", ".join(a["detail"] for a in rights) +
                            ") — subscription not known, no factor invented")
            notes.append("; ".join(bits))
        out["pairs"].append(pair)

    if unexplained:
        out["status"] = "unexplained"
        out["unexplained_pairs"] = unexplained
        head = (f"UNEXPLAINED share-count change in {', '.join(unexplained)} — the "
                f"per-share figures across that pair are not like-for-like.")
    else:
        out["status"] = "reconciled"
        out["unexplained_pairs"] = []
        head = (f"Every period pair reconciles within ±{tol:g}% "
                f"({series_used}, {ps[0]} → {ps[-1]}).")
    out["note"] = " | ".join([head] + notes)
    return out


__all__ = ["ASSUMED", "parse_corporate_action", "share_factor",
           "load_corporate_actions", "load_shares_row", "share_bridge"]
