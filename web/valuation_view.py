"""Valuation context on the MARS Den — the web half of the stage (owner
order 2026-10-12: "both, indian sector only, discount rate - go").

trading/valuation_context.py holds the arithmetic (pure). This module
gathers every input from STORED state only — no network on a page render:

  * the stock's closes: web.candle_chart.load_ohlcv (Mongo store, repo zip);
  * the sector index closes: Mongo `sector_index` (db.load_sector_rows),
    the series the 09:20 / 15:35 refresh keeps and the one-time backfill
    deepens; a name with no mapped NSE index reads Nifty 50 instead;
  * the 10-year yield: data/india_10y_bond_yield.csv (ends 2026-06 — the
    owner types today's yield on the form; the page prints the file date);
  * the sentiment gauges: VIX from the index file + stored tail, breadth
    from the stored full-store / bucket reading, FII from the committed CSV
    merged with the Mongo tail (45-day guard, like the engine), the damage
    cluster from the scan's canonical store, the news verdict from the
    news watch.

The owner's standalone targets live in settings doc "valuation_targets"
({targets: {ticker: {target, asof, basis, ts}}, assumptions: {...}}) with
an in-process copy so the card still works when Mongo is offline. They are
typed by hand until the valuation sequence's stages 09-17 produce them.
Nothing here is read by the money path.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from trading import valuation_context as vc

from . import db

log = logging.getLogger(__name__)

DOC_ID = "valuation_targets"
MAX_TARGETS = 25
_ROOT = Path(__file__).resolve().parents[1]
_INDEX_CSV = _ROOT / "data" / "index_daily_15y.csv"
_BOND_CSV = _ROOT / "data" / "india_10y_bond_yield.csv"
_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}(\.NS|\.BO)?$")
FII_MAX_AGE_DAYS = 45                     # the engine's own staleness guard

#: In-process copy of the stored doc (Mongo is the durable copy).
LAST: dict = {"targets": {}, "assumptions": {}}

ASSUMPTION_FIELDS = (
    # form name, label, ASSUMED default key (None = blank means "file")
    ("rate_now", "Today's 10-year yield % (blank = the committed file's last row)", None),
    ("erp", "Equity risk premium %", "equity_risk_premium_pct"),
    ("growth", "Long-run growth % (g)", "long_run_growth_pct"),
    ("infl_shift", "Inflation shift, points on today's rate (what-if)", "inflation_shift_pp"),
)


# ------------------------------------------------------------- stored doc

def _merge_doc(stored: dict) -> dict:
    out = {"targets": dict((stored or {}).get("targets") or {}),
           "assumptions": dict((stored or {}).get("assumptions") or {})}
    # The in-process copy wins when Mongo could not be read (offline):
    # a saved target must not vanish from the card on a reconnect blip.
    if not out["targets"] and LAST.get("targets"):
        out["targets"] = dict(LAST["targets"])
    if not out["assumptions"] and LAST.get("assumptions"):
        out["assumptions"] = dict(LAST["assumptions"])
    return out


def load_doc() -> dict:
    try:
        stored = db.load_settings(doc_id=DOC_ID) or {}
    except Exception as e:                                     # noqa: BLE001
        log.warning("valuation doc load failed: %s", e)
        stored = {}
    return _merge_doc(stored)


def _store(doc: dict) -> None:
    LAST["targets"] = dict(doc.get("targets") or {})
    LAST["assumptions"] = dict(doc.get("assumptions") or {})
    try:
        db.save_settings({"targets": LAST["targets"],
                          "assumptions": LAST["assumptions"],
                          "ts": datetime.utcnow().isoformat()}, doc_id=DOC_ID)
    except Exception:
        log.exception("valuation doc save failed (in-memory copy kept)")


def normalise_symbol(raw: str) -> str:
    s = (raw or "").strip().upper().replace(" ", "")
    if s and not s.endswith((".NS", ".BO")):
        s += ".NS"
    return s if _SYMBOL_RE.match(s) else ""


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_form(form) -> tuple[Optional[dict], dict, str]:
    """(target entry or None, assumptions, error). The entry is None when
    the ticker/target/date are not usable — nothing is stored then."""
    err = ""
    assumptions = {}
    for name, _label, key in ASSUMPTION_FIELDS:
        v = _f(form.get(name))
        if v is not None:
            assumptions[name] = v
    ticker = normalise_symbol(str(form.get("ticker") or ""))
    target = _f(form.get("target"))
    asof_raw = str(form.get("asof") or "").strip()
    if not ticker:
        err = "symbol missing or not NSE-shaped"
    elif target is None or target <= 0:
        err = "standalone target must be a number above zero"
    else:
        try:
            asof = date.fromisoformat(asof_raw) if asof_raw else date.today()
        except ValueError:
            asof, err = None, "valuation date must be YYYY-MM-DD"
        if asof and asof > date.today():
            err = "valuation date cannot be in the future"
    if err:
        return None, assumptions, err
    basis = str(form.get("basis") or "").strip()[:200]
    return ({"ticker": ticker, "target": float(target),
             "asof": asof.isoformat(), "basis": basis,
             "ts": datetime.utcnow().isoformat()}, assumptions, "")


def save_target(entry: dict, assumptions: dict) -> tuple[bool, str]:
    doc = load_doc()
    targets = dict(doc["targets"])
    if entry["ticker"] not in targets and len(targets) >= MAX_TARGETS:
        return False, f"at most {MAX_TARGETS} stored targets — clear one first"
    targets[entry["ticker"]] = entry
    doc["targets"] = targets
    if assumptions:
        doc["assumptions"] = {**doc["assumptions"], **assumptions}
    _store(doc)
    return True, ""


def clear_target(ticker: str) -> None:
    doc = load_doc()
    doc["targets"].pop(ticker, None)
    _store(doc)


# ----------------------------------------------------------------- inputs

#: Den fix 2026-10-15 (owner: "Mars den always go down nt opening"): the
#: Den's §12 read these two committed files on EVERY call — four index
#: reads plus one bond read per row, ~125 file parses for 25 rows, each
#: index read also asking Mongo for the stored tail. Now one parse per
#: process per file (keyed by the file's mtime and size, so a replaced file
#: is re-read) and the Mongo tail re-asked at most every SERIES_TTL_S.
#: Callers get a copy; the cached series is never handed out.
SERIES_TTL_S = 600.0
_SERIES_CACHE: dict = {}


def _series_key(path) -> tuple:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (None, None)


def _cached_series(name: str, path, build) -> Optional[pd.Series]:
    """`build()` returns (series, cacheable). A reading that should be
    retried (e.g. the Mongo tail failed to extend it) is returned but not
    kept, so the next call asks again — the old per-call behaviour."""
    import time as _time
    key = _series_key(path)
    hit = _SERIES_CACHE.get(name)
    now = _time.monotonic()
    if hit is not None and hit[0] == key and now - hit[1] < SERIES_TTL_S:
        return None if hit[2] is None else hit[2].copy()
    s, cacheable = build()
    if cacheable:
        _SERIES_CACHE[name] = (key, now, s)
    return None if s is None else s.copy()


def clear_series_cache() -> None:
    """Forget the cached index / bond series (tests, a refreshed tail)."""
    _SERIES_CACHE.clear()


def _bond_series() -> Optional[pd.Series]:
    return _cached_series("bond", _BOND_CSV, lambda: (_read_bond_series(), True))


def _read_bond_series() -> Optional[pd.Series]:
    try:
        bond = pd.read_csv(_BOND_CSV)
        bond.columns = [c.strip().strip('"').strip("\ufeff") for c in bond.columns]
        bond["Date"] = pd.to_datetime(bond["Date"], format="%m/%d/%Y",
                                      errors="coerce")
        return (bond.dropna(subset=["Date"]).set_index("Date")["Price"]
                .astype(float).sort_index())
    except Exception as e:                                     # noqa: BLE001
        log.warning("valuation: bond csv unreadable: %s", e)
        return None


def _index_field(field: str) -> Optional[pd.Series]:
    """One close column of the committed index file + the stored tail."""
    return _cached_series(f"index:{field}", _INDEX_CSV,
                          lambda: _read_index_field(field))


def _read_index_field(field: str) -> tuple:
    """(series, cacheable). Cacheable when the stored tail extended the
    committed file, or when no Mongo is configured (the file IS the
    answer); a connected store that added nothing may be a failed read,
    so that reading is not kept (review 2026-10-15)."""
    try:
        df = pd.read_csv(_INDEX_CSV, usecols=["trade_date", field])
        s = pd.Series(pd.to_numeric(df[field], errors="coerce").to_numpy(),
                      index=pd.to_datetime(df["trade_date"], dayfirst=True,
                                           errors="coerce"))
        s = s[s.index.notna()].dropna().sort_index()
    except Exception as e:                                     # noqa: BLE001
        log.warning("valuation: index csv unreadable (%s): %s", field, e)
        return None, False
    last_file = s.index.max() if len(s) else None
    try:
        from trading import data_refresh as dr
        s = dr.extend_close_series(s, field)
    except Exception as e:                                     # noqa: BLE001
        log.warning("valuation: index tail extend failed: %s", e)
    extended = bool(len(s)) and last_file is not None and s.index.max() > last_file
    try:
        connected = bool(db.is_connected())
    except Exception:                                          # noqa: BLE001
        connected = False
    return s, (extended or not connected)


def _sector_series(name: str) -> Optional[pd.Series]:
    try:
        rows = db.load_sector_rows()
    except Exception as e:                                     # noqa: BLE001
        log.warning("valuation: sector rows unavailable: %s", e)
        return None
    pts = [(r.get("date"), r.get("close")) for r in rows or []
           if r.get("sector") == name and r.get("close") is not None]
    if not pts:
        return None
    s = pd.Series([float(v) for _, v in pts],
                  index=pd.to_datetime([d for d, _ in pts]))
    return s.sort_index()


def bench_for(ticker: str, cfg) -> tuple[Optional[str], str]:
    """(NSE sector index name or None, label printed on the page)."""
    override = vc.TICKER_INDEX_OVERRIDE.get(ticker)
    if override:
        return override, f"Nifty {override}"
    sector = (getattr(cfg, "sector_map", {}) or {}).get(ticker, "")
    idx = vc.SECTOR_INDEX_FOR.get(sector)
    if idx:
        return idx, f"Nifty {idx}"
    return None, (f"{vc.MARKET_INDEX_LABEL} — no NSE sector index wired for "
                  f"'{sector or 'unmapped'}'")


def _fii_5d() -> Optional[float]:
    try:
        from trading.signals import flows
        by: dict = {}
        for src in (flows.fetch_flows_from_csv(), flows.fetch_flows_from_store()):
            for f in (src or []):
                by[f.trade_date] = f
        rows = sorted(by.values(), key=lambda r: r.trade_date, reverse=True)
        if not rows:
            return None
        if (date.today() - rows[0].trade_date).days > FII_MAX_AGE_DAYS:
            return None
        return flows.fii_net_last_n_days(rows, 5)
    except Exception as e:                                     # noqa: BLE001
        log.warning("valuation: FII reading failed: %s", e)
        return None


def _breadth_pct(p) -> Optional[float]:
    for store in (getattr(p, "breadth_store", None), getattr(p, "breadth_today", None)):
        if store and store.get("pct") is not None:
            try:
                v = float(store["pct"])
                return v * 100.0 if v <= 1.0 else v
            except (TypeError, ValueError):
                continue
    return None


def _damage_q90(p) -> Optional[bool]:
    d = getattr(p, "damage_store", None) or {}
    if "q90_cluster" not in d:
        return None
    return bool(d.get("q90_cluster"))


def _news_verdict(p, ticker: str) -> Optional[str]:
    rows = ((getattr(p, "news_watch", None) or {}).get("rows") or {})
    r = rows.get(ticker) or {}
    return r.get("verdict")


def sentiment_inputs(cfg, p, vix: Optional[pd.Series]) -> dict:
    return {"vix": vix, "breadth_pct": _breadth_pct(p),
            "breadth_low_pct": float(getattr(cfg, "breadth_block_low", 0.40)) * 100.0,
            "fii_5d_net_cr": _fii_5d(), "damage_q90_on": _damage_q90(p)}


def assumptions_in_use(doc_assumptions: dict) -> dict:
    out = {}
    for name, _label, key in ASSUMPTION_FIELDS:
        v = doc_assumptions.get(name)
        out[name] = float(v) if v is not None else (
            float(vc.ASSUMED[key]) if key else None)
    return out


# ------------------------------------------------------------------ rows

def readings(cfg, p, doc: dict) -> list[dict]:
    from .candle_chart import load_ohlcv
    a = assumptions_in_use(doc.get("assumptions") or {})
    bond = _bond_series()
    vix = _index_field("india_vix_close")
    nifty = _index_field("nifty_close")
    sent = sentiment_inputs(cfg, p, vix)
    out = []
    for ticker, entry in sorted((doc.get("targets") or {}).items()):
        df = load_ohlcv(ticker, tail=600)
        stock = df["Close"].dropna() if (df is not None and not df.empty
                                        and "Close" in df.columns) else None
        idx, label = bench_for(ticker, cfg)
        bench = _sector_series(idx) if idx else nifty
        bench_why = ""
        if idx and bench is None:
            bench_why = (f"no stored rows for Nifty {idx} yet — the 09:20 / "
                         f"15:35 sector refresh and the backfill on save fill them")
        elif not idx and bench is None:
            bench_why = "the committed index file is unreadable"
        r = vc.context(ticker, float(entry.get("target") or 0), entry.get("asof"),
                       stock, bench, label, bond=bond,
                       rate_now_pct=a.get("rate_now"), erp_pct=a["erp"],
                       growth_pct=a["growth"], inflation_shift_pp=a["infl_shift"],
                       sentiment=sent, news_verdict=_news_verdict(p, ticker),
                       bench_why=bench_why)
        r["basis"] = entry.get("basis") or ""
        r["saved_ts"] = entry.get("ts") or ""
        r["sector"] = (getattr(cfg, "sector_map", {}) or {}).get(ticker, "")
        out.append(r)
    return out


def empty_section(flag: Optional[str] = None, error: str = "") -> dict:
    """The template-safe shape with no rows — also the fallback the Den
    renders when the real section crashes (every key the template reads
    is present, so a data fault can never take the page down)."""
    a = assumptions_in_use({})
    return {
        "doc_id": DOC_ID, "max_targets": MAX_TARGETS,
        "flag": flag or "", "error": error,
        "rows": [], "n": 0,
        "assumptions": a, "assumption_fields": [
            {"name": n, "label": l, "value": a.get(n), "assumed": vc.ASSUMED.get(k) if k else None}
            for n, l, k in ASSUMPTION_FIELDS],
        "assumed": dict(vc.ASSUMED),
        "bond_last": None, "sentiment": None,
        "sensitivity_per_pp": None, "rate_now_used": None,
        "sector_index_map": dict(vc.SECTOR_INDEX_FOR),
        "overrides": dict(vc.TICKER_INDEX_OVERRIDE),
        "today": date.today().isoformat(),
    }


def section(cfg, p, flag: Optional[str] = None, error: str = "") -> dict:
    out = empty_section(flag, error)
    doc = load_doc()
    a = assumptions_in_use(doc.get("assumptions") or {})
    bond = _bond_series()
    bond_last = ((bond.index[-1].date().isoformat(), float(bond.iloc[-1]))
                 if bond is not None and len(bond) else None)
    rows = readings(cfg, p, doc)
    # the fear gauges as read today (same inputs every row sees)
    with_sent = [r for r in rows if (r.get("legs") or {}).get("sentiment")]
    sent_sample = (with_sent[0]["legs"]["sentiment"] if with_sent
                   else vc.sentiment_margin(
                       **sentiment_inputs(cfg, p, _index_field("india_vix_close"))))
    # the "+1pp → −x%" sensitivity at today's assumptions, for the explainer
    r_now = (a.get("rate_now") if a.get("rate_now") is not None
             else (bond_last[1] if bond_last else None))
    sens = (vc.rates_leg(100.0, r_now, r_now, a["erp"], a["growth"], a["infl_shift"])
            if r_now is not None else {})
    out.update({
        "rows": rows, "n": len(rows),
        "assumptions": a, "assumption_fields": [
            {"name": n, "label": l, "value": a.get(n), "assumed": vc.ASSUMED.get(k) if k else None}
            for n, l, k in ASSUMPTION_FIELDS],
        "bond_last": bond_last,
        "sentiment": sent_sample,
        "sensitivity_per_pp": sens.get("per_pp_pct"),
        "rate_now_used": r_now,
    })
    return out


def ensure_sector_depth(asof_iso: str) -> dict:
    """Owner tap on the form: if the stored sector series does not reach
    the typed valuation date (or the β window), run the one-time backfill
    now instead of waiting for the next scheduled refresh. Tokenless; one
    attempt per process per day; no history client = nothing happens."""
    try:
        from . import scheduler
        rep = scheduler.backfill_sector_history()
        return rep
    except Exception as e:                                     # noqa: BLE001
        log.warning("valuation: sector backfill unavailable: %s", e)
        return {"skipped": str(e)}
