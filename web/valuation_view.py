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

DEN §11 COST FIX 2026-10-15 (the §12 fault class — "Mars den always go
down nt opening"). Each open used to load the FULL twelve-year price
document of every stored target (load_ohlcv reads the whole doc, then kept
600 rows) and read the whole sector_index collection once per target:
25 targets = ~50 Atlas reads per open, during scans too. Now one render =
ONE batched store read for the targets' closes ($in + $slice, the tail
sized from the OLDEST valuation date, capped at PRICE_TAIL_MAX; a name the
store lacks alone falls back to load_ohlcv) and ONE sector read, kept
SERIES_TTL_S per process. Computed rows are kept ROW_TTL_S; a TIME BUDGET
stops new rows once spent; NO new row is computed (and nothing is read)
while a scan rotation runs or the process is above the Den's memory
ceiling — those rows print the reason. Same knobs as §12
(SB_DEN_CHAIN_BUDGET_S, SB_DEN_CHAIN_RSS_CEILING_MB).
"""
from __future__ import annotations

import logging
import re
import threading
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
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
    """Forget the cached index / bond / sector series and the kept §11 rows
    (tests — the web conftest calls this before every test — and a
    refreshed tail)."""
    _SERIES_CACHE.clear()
    with _SECTOR_LOCK:
        _SECTOR_CACHE.clear()
    clear_row_cache()


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


#: The sector index closes, read ONCE (2026-10-15: the old code read the
#: whole sector_index collection once PER TARGET on every open). One
#: db.load_sector_rows(since) per render, kept SERIES_TTL_S per process when
#: it returned rows (an empty read — store offline or not yet filled — is
#: asked again on the next render, never kept); `since` covers the β window
#: and the oldest valuation date, not the whole store. A render installs its
#: table in a thread-local so every target of that render reads the SAME
#: table even when the store answered nothing.
SECTOR_BETA_DAYS = 420          # calendar days ≥ the 252-session β window + margin
_SECTOR_CACHE: dict = {}
_SECTOR_LOCK = threading.Lock()
_TLS = threading.local()


def _default_sector_since() -> str:
    return (date.today() - timedelta(days=SECTOR_BETA_DAYS)).isoformat()


def _sector_table(since: Optional[str] = None) -> dict:
    """{sector name: close series}. `since` = the oldest date the caller
    needs (None = whatever is kept, else the β window's floor)."""
    now = _time.monotonic()
    with _SECTOR_LOCK:
        hit = _SECTOR_CACHE.get("t")
        if (hit is not None and now - hit["at"] < SERIES_TTL_S
                and (since is None or hit["since"] <= since)):
            return hit["table"]
    want = since if since is not None else _default_sector_since()
    try:
        rows = db.load_sector_rows(want)
    except Exception as e:                                     # noqa: BLE001
        log.warning("valuation: sector rows unavailable: %s", e)
        rows = []
    pts: dict = {}
    for r in rows or []:
        name, d, c = r.get("sector"), r.get("date"), r.get("close")
        if name and d and c is not None:
            pts.setdefault(name, ([], []))
            pts[name][0].append(d)
            pts[name][1].append(float(c))
    table = {}
    for name, (ds, cs) in pts.items():
        s = pd.Series(cs, index=pd.to_datetime(ds), dtype=float).sort_index()
        table[name] = s[~s.index.duplicated(keep="last")]
    if table:
        with _SECTOR_LOCK:
            _SECTOR_CACHE["t"] = {"since": want, "at": now, "table": table}
    return table


def _sector_series(name: str) -> Optional[pd.Series]:
    table = getattr(_TLS, "sector_table", None)
    if table is None:
        table = _sector_table()
    s = table.get(name)
    return None if s is None else s.copy()


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

#: Den §11 cost fix 2026-10-15 (see the module note).
#: The closes a render reads: the last N sessions of every target in ONE
#: batched store query. N covers the OLDEST valuation date among the rows
#: being computed (business days since that date + a margin — business
#: days over-count the sessions, so the slice reaches back past the date),
#: never fewer than PRICE_TAIL_MIN (the 252-session β + margin; the old
#: fixed tail) and never more than PRICE_TAIL_MAX (~10 years — the span one
#: public sector-candle request covers, so both legs reach the same dates).
PRICE_TAIL_MIN = 600
PRICE_TAIL_MAX = 2500
PRICE_TAIL_MARGIN = 10
#: Sector rows wanted before the valuation date (weekends, holidays).
SECTOR_MARGIN_DAYS = 15
#: One public daily-candle request spans ~10 years (verified by the fetch
#: kit: research/tools/fetch_kit/fetch_upstox_ohlcv.py CHUNK_DAYS 3600; a
#: 16-year span is refused) — the deepest backfill a typed date can ask for.
SECTOR_FEED_MAX_DAYS = 3600
#: Computed §11 rows kept this long per process (re-open = no store read).
ROW_TTL_S = 1800.0
ROW_CACHE_MAX = MAX_TARGETS * 4
_ROW_CACHE: dict = {}
_ROW_LOCK = threading.Lock()
#: One computing render at a time: a second open while §11 computes serves
#: the kept rows and marks the rest "not computed" (the §12 rule).
_SECTION_LOCK = threading.Lock()
DEFAULT_TIME_BUDGET_S = 20.0
DEFAULT_RSS_CEILING_MB = 380.0


def time_budget_s() -> float:
    """§11's time budget per render — the SAME knob as §12
    (SB_DEN_CHAIN_BUDGET_S; <= 0 = no budget)."""
    try:
        from . import valuation_chain_view as cv
        return float(cv.time_budget_s())
    except Exception:                                          # noqa: BLE001
        return DEFAULT_TIME_BUDGET_S


def _rss_ceiling_mb() -> float:
    try:
        from . import valuation_chain_view as cv
        return float(cv.rss_ceiling_mb())
    except Exception:                                          # noqa: BLE001
        return DEFAULT_RSS_CEILING_MB


def busy_reason() -> str:
    """Why §11 must not compute (or read) anything new right now ("" =
    free) — §12's rule (valuation_chain_view.busy_reason), worded for this
    section: a scan rotation holds the scheduler's rotation lock, or the
    process is above the Den's memory ceiling (SB_DEN_CHAIN_RSS_CEILING_MB;
    <= 0 = no ceiling)."""
    try:
        from . import scheduler
        lock = getattr(scheduler, "_ROTATION_LOCK", None)
        if lock is not None and lock.locked():
            return "a scan rotation is running on the box"
    except Exception:                                          # noqa: BLE001
        pass
    ceiling = _rss_ceiling_mb()
    if ceiling > 0:
        try:
            from .memory_relief import rss_mb
            r = rss_mb()
        except Exception:                                      # noqa: BLE001
            r = None
        if r is not None and r >= ceiling:
            return (f"the app is using {r:.0f} MiB of its 512 MiB "
                    f"(the Den computes below {ceiling:.0f})")
    return ""


def _as_date(v: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def price_tail_for(asofs) -> int:
    """Sessions of closes a render reads for valuation dates `asofs`."""
    today = date.today()
    need = 0
    for a in asofs:
        d = _as_date(a)
        if d is not None and d < today:
            need = max(need, int(np.busday_count(d, today)) + PRICE_TAIL_MARGIN)
    return int(min(PRICE_TAIL_MAX, max(PRICE_TAIL_MIN, need)))


def _sector_since_for(asofs) -> str:
    """The oldest sector date a render needs: the β window, or the oldest
    valuation date less a margin, whichever is earlier."""
    since = _default_sector_since()
    for a in asofs:
        d = _as_date(a)
        if d is not None:
            since = min(since, (d - timedelta(days=SECTOR_MARGIN_DAYS)).isoformat())
    return since


def _close_series(df) -> Optional[pd.Series]:
    if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
        return None
    s = df["Close"].dropna()
    return s if len(s) else None


def _prefetch_closes(tickers, rows: int) -> dict:
    """{ticker: (closes, store start date)} for the rows about to be
    computed, in ONE store query ($in + $slice). Names the store lacks are
    absent; the row loop then asks load_ohlcv for that name alone."""
    out: dict = {}
    if not tickers:
        return out
    try:
        if not db.is_connected():
            return out
        from trading.history_store import records_to_df
        docs = db.history_docs(list(tickers), rows=int(rows)) or {}
        for t, doc in docs.items():
            s = _close_series(records_to_df(doc))
            if s is not None:
                out[t] = (s, str(doc.get("start") or "")[:10])
    except Exception:                                          # noqa: BLE001
        log.warning("valuation: batched store read failed")
    return out


def _window_note(s: Optional[pd.Series], asof: Any, rows: int, start: str,
                 cut: bool) -> str:
    """When the page's own window (not the data) is why the valuation date
    has no close: say so. When the store itself starts after the date, name
    the STORE's first close (the window may start later than that). "" when
    the series reaches the date, or nothing better is known (the context's
    own message stands)."""
    d = _as_date(asof)
    if s is None or not len(s) or d is None:
        return ""
    first = s.index[0].date()
    if first <= d:
        return ""
    sd = _as_date(start)
    if sd is not None:
        if sd > d:                     # the data itself starts after the date
            return (f"series starts {sd.isoformat()}, after the valuation date"
                    if sd != first else "")
        held = f"; the store holds this name from {sd.isoformat()}"
    elif cut:
        held = ""
    else:
        return ""
    return (f"this page reads the last {rows} sessions (from {first.isoformat()}){held} "
            f"— a valuation dated {d.isoformat()} is older than the page's window")


def _sent_sig(sent: dict) -> tuple:
    vix = sent.get("vix")
    last = ((str(vix.index[-1])[:10], round(float(vix.iloc[-1]), 4))
            if vix is not None and len(vix) else None)
    return (last, sent.get("breadth_pct"), sent.get("breadth_low_pct"),
            sent.get("fii_5d_net_cr"), sent.get("damage_q90_on"))


def _row_key(ticker: str, entry: dict, a: dict, sent: dict, news: Optional[str],
             label: str) -> tuple:
    import json
    return (ticker, date.today().isoformat(),
            json.dumps({k: entry.get(k) for k in ("target", "asof", "basis", "ts")},
                       sort_keys=True, default=str),
            json.dumps(a, sort_keys=True, default=str),
            _sent_sig(sent), news or "", label)


def _row_cache_get(key: tuple) -> Optional[dict]:
    with _ROW_LOCK:
        hit = _ROW_CACHE.get(key)
        if hit is None or _time.monotonic() - hit[0] >= ROW_TTL_S:
            _ROW_CACHE.pop(key, None)
            return None
        return hit[1]


def _row_cache_put(key: tuple, rec: dict) -> None:
    now = _time.monotonic()
    with _ROW_LOCK:
        for k in [k for k, v in _ROW_CACHE.items() if now - v[0] >= ROW_TTL_S]:
            _ROW_CACHE.pop(k, None)
        while len(_ROW_CACHE) >= ROW_CACHE_MAX:
            oldest = min(_ROW_CACHE, key=lambda k: _ROW_CACHE[k][0])
            _ROW_CACHE.pop(oldest, None)
        _ROW_CACHE[key] = (now, rec)


def clear_row_cache() -> None:
    with _ROW_LOCK:
        _ROW_CACHE.clear()


def _forget_sector_reads() -> None:
    """A backfill stored rows: the kept sector table and the rows computed
    from it are stale."""
    with _SECTOR_LOCK:
        _SECTOR_CACHE.clear()
    clear_row_cache()


def _base_row(ticker: str, entry: dict, cfg) -> dict:
    return {"ticker": ticker, "target": float(entry.get("target") or 0),
            "asof": str(entry.get("asof") or ""), "legs": {},
            "basis": entry.get("basis") or "", "saved_ts": entry.get("ts") or "",
            "sector": (getattr(cfg, "sector_map", {}) or {}).get(ticker, "")}


def not_computed_row(ticker: str, entry: dict, cfg, why: str) -> dict:
    """A row §11 chose not to compute on this open (busy box, spent
    budget) — the target still prints, the reason in one line."""
    r = _base_row(ticker, entry, cfg)
    r.update(status="NOT_COMPUTED", not_computed=True, why=why)
    return r


def row_for(ticker: str, entry: dict, cfg, p, *, a: dict, bond, nifty, sent: dict,
            closes: Optional[pd.Series], window_note: str = "") -> dict:
    """One stored target's context reading (pure arithmetic in
    trading/valuation_context.py; the inputs are handed in)."""
    idx, label = bench_for(ticker, cfg)
    bench = _sector_series(idx) if idx else nifty
    bench_why = ""
    if idx and bench is None:
        bench_why = (f"no stored rows for Nifty {idx} yet — the 09:20 / "
                     f"15:35 sector refresh and the backfill on save fill them")
    elif not idx and bench is None:
        bench_why = "the committed index file is unreadable"
    r = vc.context(ticker, float(entry.get("target") or 0), entry.get("asof"),
                   closes, bench, label, bond=bond,
                   rate_now_pct=a.get("rate_now"), erp_pct=a["erp"],
                   growth_pct=a["growth"], inflation_shift_pp=a["infl_shift"],
                   sentiment=sent, news_verdict=_news_verdict(p, ticker),
                   bench_why=bench_why)
    if (window_note and r.get("status") != "OK"
            and str(r.get("why") or "").startswith("stock closes: series starts")):
        r["why"] = f"stock closes: {window_note}"
    r["basis"] = entry.get("basis") or ""
    r["saved_ts"] = entry.get("ts") or ""
    r["sector"] = (getattr(cfg, "sector_map", {}) or {}).get(ticker, "")
    return r


def _readings(cfg, p, doc: dict, *, a: dict, bond, sent: dict) -> tuple[list, dict]:
    """(rows, stats). Kept rows first; then, unless the box is busy, ONE
    batched store read for the closes, ONE sector read, and the rows in
    order until the time budget is spent."""
    from .candle_chart import load_ohlcv
    targets = sorted((doc.get("targets") or {}).items())
    budget = time_budget_s()
    t0 = _time.monotonic()
    stats = {"computed": 0, "cached": 0, "skipped_budget": 0, "skipped_busy": 0,
             "busy": "", "budget_s": budget, "price_tail": 0}
    keys = {t: _row_key(t, e, a, sent, _news_verdict(p, t), bench_for(t, cfg)[1])
            for t, e in targets}
    kept = {t: _row_cache_get(keys[t]) for t, _ in targets}
    todo = [(t, e) for t, e in targets if kept[t] is None]
    owns = _SECTION_LOCK.acquire(blocking=False) if todo else False
    busy = (busy_reason() if owns else
            ("another open of the Den is computing these rows right now" if todo else ""))
    rows: list = []
    try:
        pre: dict = {}
        rows_n = 0
        nifty = None
        if todo and not busy:
            asofs = [e.get("asof") for _, e in todo]
            rows_n = price_tail_for(asofs)
            stats["price_tail"] = rows_n
            pre = _prefetch_closes([t for t, _ in todo], rows_n)
            _TLS.sector_table = _sector_table(_sector_since_for(asofs))
            nifty = _index_field("nifty_close")
        for t, e in targets:
            if kept[t] is not None:
                rows.append(kept[t])
                stats["cached"] += 1
                continue
            if not busy and stats["computed"]:
                busy = busy_reason()              # a rotation that started mid-render
            if busy:
                rows.append(not_computed_row(
                    t, e, cfg, f"{busy}; open the Den again once it finishes"))
                stats["skipped_busy"] += 1
                continue
            if budget > 0 and _time.monotonic() - t0 >= budget:
                rows.append(not_computed_row(
                    t, e, cfg,
                    f"the §11 time budget ({budget:.0f} s) was spent on the rows above; "
                    f"open the Den again (computed rows are kept {int(ROW_TTL_S // 60)} min)"))
                stats["skipped_budget"] += 1
                continue
            try:
                if t in pre:
                    s, start = pre[t]
                    # a doc without a "start" stamp: a full slice means the
                    # page may have cut it
                    note = _window_note(s, e.get("asof"), rows_n, start,
                                        s is not None and len(s) >= rows_n)
                else:
                    df = load_ohlcv(t, tail=rows_n)
                    s = _close_series(df)
                    note = _window_note(s, e.get("asof"), rows_n, "",
                                        df is not None and len(df) >= rows_n)
                rec = row_for(t, e, cfg, p, a=a, bond=bond, nifty=nifty, sent=sent,
                              closes=s, window_note=note)
                _row_cache_put(keys[t], rec)
                stats["computed"] += 1
                rows.append(rec)
            except Exception:                                  # noqa: BLE001
                log.exception("valuation: context row failed for %s", t)
                r = _base_row(t, e, cfg)
                r.update(status="DATA_NEEDED",
                         why="the context could not be read for this name — see the log")
                rows.append(r)
    finally:
        _TLS.sector_table = None
        if owns:
            _SECTION_LOCK.release()
    if stats["computed"]:
        try:
            from .memory_relief import release
            release("den valuation")
        except Exception:                                      # noqa: BLE001
            pass
    stats["busy"] = busy
    stats["elapsed_s"] = round(_time.monotonic() - t0, 1)
    return rows, stats


def readings(cfg, p, doc: dict) -> list[dict]:
    """Every stored target's context reading (the guarded path section()
    uses — kept rows, one batched read, budget, busy box)."""
    a = assumptions_in_use(doc.get("assumptions") or {})
    sent = sentiment_inputs(cfg, p, _index_field("india_vix_close"))
    rows, _ = _readings(cfg, p, doc, a=a, bond=_bond_series(), sent=sent)
    return rows


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
        "computed": 0, "cached": 0, "skipped_budget": 0, "skipped_busy": 0,
        "busy": "", "budget_s": 0.0, "elapsed_s": 0.0, "price_tail": 0,
        "row_ttl_min": int(ROW_TTL_S // 60),
    }


def section(cfg, p, flag: Optional[str] = None, error: str = "") -> dict:
    out = empty_section(flag, error)
    doc = load_doc()
    a = assumptions_in_use(doc.get("assumptions") or {})
    bond = _bond_series()
    bond_last = ((bond.index[-1].date().isoformat(), float(bond.iloc[-1]))
                 if bond is not None and len(bond) else None)
    # the fear gauges as read today (same inputs every row sees) — read ONCE
    sent = sentiment_inputs(cfg, p, _index_field("india_vix_close"))
    rows, stats = _readings(cfg, p, doc, a=a, bond=bond, sent=sent)
    with_sent = [r for r in rows if (r.get("legs") or {}).get("sentiment")]
    sent_sample = (with_sent[0]["legs"]["sentiment"] if with_sent
                   else vc.sentiment_margin(**sent))
    # the "+1pp → −x%" sensitivity at today's assumptions, for the explainer
    r_now = (a.get("rate_now") if a.get("rate_now") is not None
             else (bond_last[1] if bond_last else None))
    sens = (vc.rates_leg(100.0, r_now, r_now, a["erp"], a["growth"], a["infl_shift"])
            if r_now is not None else {})
    out.update(stats)
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


def sector_days_for(asof_iso: str) -> int:
    """Calendar days of sector history the typed valuation date needs: the
    date itself less SECTOR_MARGIN_DAYS, never less than the scheduler's
    standing depth (the β window wants ~2 years), never more than one feed
    request spans (SECTOR_FEED_MAX_DAYS)."""
    try:
        from . import scheduler
        base = int(scheduler.SECTOR_HISTORY_DAYS)
    except Exception:                                          # noqa: BLE001
        base = 760
    d = _as_date(asof_iso)
    need = (date.today() - d).days + SECTOR_MARGIN_DAYS if d is not None else 0
    return int(min(SECTOR_FEED_MAX_DAYS, max(base, need)))


def ensure_sector_depth(asof_iso: str) -> dict:
    """Owner tap on the form: if the stored sector series does not reach
    the typed valuation date (or the β window), run the backfill now for
    the days THAT date needs (sector_days_for) instead of waiting for the
    next scheduled refresh. Tokenless; one attempt per process per day per
    depth (scheduler.backfill_sector_history's guard); no history client =
    nothing happens. BLOCKING (a store read, up to 16 network calls): the
    POST runs it in the threadpool, never on the event loop."""
    try:
        from . import scheduler
        days = sector_days_for(asof_iso)
        rep = dict(scheduler.backfill_sector_history(days=days) or {})
        rep["days"] = days
        if rep.get("stored"):
            _forget_sector_reads()
        return rep
    except Exception as e:                                     # noqa: BLE001
        log.warning("valuation: sector backfill unavailable: %s", e)
        return {"skipped": str(e)}
