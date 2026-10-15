"""Valuation chain on the MARS Den (§12) — the web half of sequence stages
07-18 (owner order 2026-10-14: "lets hit the formulas work completion").

trading/valuation/* holds the arithmetic (pure). This module gathers every
input from STORED state only — no network on a page render:

  * closes: the Mongo store's last PRICE_TAIL rows in one $slice query
    (db.history_docs), else web.candle_chart.load_ohlcv (repo zip) —
    today's price, the price at each FY end (own P/E history), the price
    at the snapshot's period end;
  * index levels: the committed index file + the stored tail
    (web.valuation_view._index_field) for the Sensex / Nifty legs of the
    owner's P/E bridge;
  * peers: the median trailing P/E of the SAME ROUTE among the Nifty-200
    names with statements — ASSUMED until the owner gives peer groups.
    The price-free half (200 statement reads, ~9 s) is built at most once
    every PEER_BASE_MAX_AGE_DAYS and STORED in settings doc
    `valuation_peers`, so a fresh process after a sleep reads it back
    instead of rebuilding; today's multiples = that base x the last stored
    close of every name, fetched in ONE query (db.history_tails(1));
  * which names to show: the typed one, the §11 stored targets, held names.

Macro rows of the P/E bridge the app does not store (repo, CPI, GDP,
Brent) are passed as 0 and listed as DATA_NEEDED on the row — the sheet
treats them as what-if inputs, so a missing one must not blank the price.
Nothing here is read by the money path.

DEN FIX 2026-10-15 (owner: "Mars den always go down nt opening"): this
section used to load ~200 twelve-year price documents (the peer table) and
re-read three committed files per row on EVERY open — on the 512 MB box,
on top of a scan, that is what took the app down. Now: the peer base is
stored, prices come from one query, the file loaders cache per process,
computed rows are kept ROW_TTL_S, a TIME BUDGET stops new rows once spent,
and NO new row is computed while a scan rotation runs or the process is
above RSS_CEILING_MB — those rows print the reason instead.
"""
from __future__ import annotations

import logging
import statistics
import threading
from datetime import date
from typing import Any, Optional

import pandas as pd

from trading import valuation as V
from trading import valuation_context as vc

from . import valuation_view as vv

log = logging.getLogger(__name__)

MAX_ROWS = 25
PRICE_TAIL = 1500                        # ~6 years of daily closes
#: Owner order 2026-10-14 ("need editable"): the assumption DRAFT lives in
#: this settings doc (+ an in-process copy so the Den works while Mongo is
#: offline, the formula_drafts pattern). §12 reads it on the page render;
#: the live Config and the engine never do.
ASSUMPTIONS_DOC_ID = "valuation_assumptions"
LAST_ASSUMPTIONS: dict = {}
KEY_TERMS = ("P/E (trailing, forward)", "P/B", "EV/EBITDA", "ROE", "ROCE",
             "FCF / FCF yield", "Cash conversion", "Net debt / EBITDA",
             "Interest coverage", "Dividend yield / payout", "Graham number",
             "Revenue growth")

_PEER_CACHE: dict = {"day": None, "by_method": {}, "base": None,
                     "built": None, "sig": None, "source": ""}

#: Settings doc that holds the price-free peer base (see the module note).
PEERS_DOC_ID = "valuation_peers"
#: The stored base is valid for the calendar day it was built (the old
#: code rebuilt per process per day; this keeps that freshness — a monthly
#: fundamentals refresh or a new FY passing its publication lag is picked
#: up the next day — while a process woken after a sleep reads it back).
PEER_BASE_MAX_AGE_DAYS = 1
#: Bump when the stored base's row shape changes, so an old doc is rebuilt.
#: The signature also hashes the valuation code, so a deploy that changes
#: routing or the snapshot rebuilds it without a hand bump.
PEER_BASE_VERSION = 2
#: A base priced on fewer statement names than this share is used for the
#: day in this process but NOT stored (a half-filled store must not fix a
#: thin peer table — or an ADR name with no price — into the stored copy).
PEER_PRICE_COVERAGE = 0.9
_ZIP = vv._ROOT / "data" / "history_upstox_export_2026-07-19.zip"
_PEER_LOCK = threading.Lock()
_ROW_LOCK = threading.Lock()
#: One computing render at a time: a second open while §12 is computing
#: serves the kept rows and marks the rest "not computed" instead of
#: doubling the memory on the 512 MB box.
_SECTION_LOCK = threading.Lock()

#: Computed §12 rows kept this long per process (re-open = instant).
ROW_TTL_S = 1800.0
ROW_CACHE_MAX = 80
_ROW_CACHE: dict = {}
#: Den fix 2026-10-15 defaults; both read from the environment at call
#: time (SB_DEN_CHAIN_BUDGET_S, SB_DEN_CHAIN_RSS_CEILING_MB; <= 0 = off).
DEFAULT_TIME_BUDGET_S = 20.0
#: ASSUMED from the measured box: idle after a scan 330-370 MiB, a scan
#: rotation 400-495, the 09:20 trigger layer 500-525 = the OOM kills.
DEFAULT_RSS_CEILING_MB = 380.0


# ------------------------------------------------------------- inputs

def _close_series(df) -> Optional[pd.Series]:
    if df is None or df.empty or "Close" not in df.columns:
        return None
    s = df["Close"].dropna().astype(float)
    s.index = pd.to_datetime(s.index)
    return s if len(s) else None


def _closes(ticker: str) -> Optional[pd.Series]:
    """The last PRICE_TAIL closes: one $slice query on the store (the old
    path loaded the whole twelve-year document and dropped most of it),
    else the chart loader's repo-zip fallback."""
    from . import db
    try:
        if db.is_connected():
            from trading.history_store import records_to_df
            doc = (db.history_docs([ticker], rows=PRICE_TAIL) or {}).get(ticker)
            s = _close_series(records_to_df(doc)) if doc else None
            if s is not None:
                return s
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: store read failed for %s", ticker)
    # Straight to the repo zip — load_ohlcv would ask Mongo AGAIN (a second
    # timeout when the store is unreachable).
    return _zip_close_series(ticker)


def _zip_frames(tickers) -> dict:
    """{ticker: OHLCV frame} from the committed repo zip, ONE zip open."""
    import io
    import zipfile
    out: dict = {}
    want = [t for t in (tickers or []) if t]
    if not want:
        return out
    try:
        with zipfile.ZipFile(_ZIP) as z:
            names = set(z.namelist())
            for t in want:
                n = f"{t}.csv"
                if n not in names:
                    continue
                try:
                    df = pd.read_csv(io.BytesIO(z.read(n)), parse_dates=["Date"],
                                     index_col="Date").sort_index()
                    out[t] = df
                except Exception:                       # noqa: BLE001
                    continue
    except (OSError, Exception):                        # noqa: BLE001
        log.warning("valuation chain: repo zip unreadable")
    return out


def _zip_close_series(ticker: str) -> Optional[pd.Series]:
    df = _zip_frames([ticker]).get(ticker)
    return _close_series(df.tail(PRICE_TAIL)) if df is not None else None


def price_now(closes: Optional[pd.Series]) -> tuple[Optional[float], str]:
    if closes is None or not len(closes):
        return None, ""
    return float(closes.iloc[-1]), closes.index[-1].date().isoformat()


def price_at(closes: Optional[pd.Series], when: Any) -> Optional[float]:
    """Close on or before `when` (None when the series starts later)."""
    if closes is None or not len(closes) or not when:
        return None
    try:
        ts = pd.Timestamp(when)
    except (TypeError, ValueError):
        return None
    s = closes[closes.index <= ts]
    return float(s.iloc[-1]) if len(s) else None


def index_move(field: str, start: Any, end: Any = None) -> dict:
    """{start, end, start_date, end_date} levels of one index column."""
    s = vv._index_field(field)
    if s is None or not len(s):
        return {"start": None, "end": None}
    s = s.dropna()
    a = price_at(s, start)
    b = float(s.iloc[-1]) if end is None else price_at(s, end)
    return {"start": a, "end": b,
            "end_date": s.index[-1].date().isoformat() if end is None else str(end)[:10]}


def own_pe_history(closes: Optional[pd.Series], snap: dict) -> list[float]:
    """FY-end close ÷ that FY's basic EPS, for every FY in the snapshot
    history — the stock's own multiple range (needs the long tail)."""
    out: list[float] = []
    eps_hist = ((snap.get("history") or {}).get("basic_eps") or {})
    for period_end, eps in sorted(eps_hist.items()):
        e = V.num(eps)
        px = price_at(closes, period_end)
        if e and e > 0 and px:
            out.append(px / e)
    return out


def _held_names(p) -> list[str]:
    """Held tickers. `Portfolio.positions` is a dict keyed by ticker; a
    list of position objects is accepted too (fixtures)."""
    held = getattr(p, "positions", None) or {}
    try:
        if isinstance(held, dict):
            return [str(k) for k in held.keys()]
        return [getattr(x, "ticker", str(x)) for x in held]
    except Exception:                                   # noqa: BLE001
        return []


def names_to_show(p, typed: str = "") -> list[str]:
    """Typed first, then the §11 stored targets, then held names; unique;
    capped at MAX_ROWS."""
    out: list[str] = []
    if typed:
        out.append(typed)
    try:
        doc = vv.load_doc()
        for t in sorted((doc.get("targets") or {}).keys()):
            if t not in out:
                out.append(t)
    except Exception:                                   # noqa: BLE001
        pass
    for t in _held_names(p):
        if t not in out:
            out.append(t)
    return out[:MAX_ROWS]


# ------------------------------------------------------ assumption draft

def load_assumptions() -> dict:
    """{key: value} of the stored draft — Mongo doc first, the in-process
    copy when the doc cannot be read. Unknown keys are dropped."""
    from trading.valuation import assumptions as ASM
    from . import db
    values: dict = {}
    try:
        doc = db.load_settings(doc_id=ASSUMPTIONS_DOC_ID) or {}
        values = dict(doc.get("values") or {})
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: assumption draft unreadable — using the in-process copy")
    if not values and LAST_ASSUMPTIONS:
        values = dict(LAST_ASSUMPTIONS)
    return ASM.clean(values)


def save_assumptions(values: dict) -> bool:
    """Store the draft (differences from the defaults only). Returns
    whether the Mongo write landed; the in-process copy is kept either way."""
    from datetime import datetime
    from trading.valuation import assumptions as ASM
    from . import db
    clean = ASM.clean(values)
    LAST_ASSUMPTIONS.clear()
    LAST_ASSUMPTIONS.update(clean)
    try:
        db.save_settings({"values": clean, "ts": datetime.utcnow().isoformat(),
                          "version": ASM.VERSION}, doc_id=ASSUMPTIONS_DOC_ID)
        return True
    except Exception:                                   # noqa: BLE001
        log.exception("valuation chain: assumption draft save failed (in-memory copy kept)")
        return False


def clear_assumptions() -> None:
    from datetime import datetime
    from . import db
    LAST_ASSUMPTIONS.clear()
    try:
        db.save_settings({"values": {}, "ts": datetime.utcnow().isoformat()},
                         doc_id=ASSUMPTIONS_DOC_ID)
    except Exception:                                   # noqa: BLE001
        log.exception("valuation chain: assumption draft clear failed (in-memory copy cleared)")


def parse_assumptions_form(form) -> tuple[dict, list]:
    """Form → (values, errors) through assumptions.coerce; a non-owner
    field or an unknown name is ignored, a bad number is an error."""
    from trading.valuation import assumptions as ASM
    raw = {k: form.get(k) for k in ASM.BY_KEY}
    return ASM.coerce(raw)


# --------------------------------------------------------------- peers

def _store_closes() -> dict:
    """{ticker: last stored close} for EVERY stored name in one query
    (db.history_tails(1) — the server trims each document to its last
    candle). {} when Mongo is down; the caller then reads names one by one
    from the repo zip, as before."""
    from . import db
    out: dict = {}
    try:
        for doc in db.history_tails(1) or ():
            t = doc.get("ticker") or doc.get("_id")
            c = doc.get("candles") or []
            if t and c:
                try:
                    out[str(t)] = float(c[-1][4])
                except (TypeError, ValueError, IndexError):
                    continue
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: store closes unreadable")
    return out


def _peer_prices() -> dict:
    """{ticker: last close} for the statement names: the store's last
    candles in ONE query, names the store lacks (or all of them when Mongo
    is down) filled from the repo zip in ONE zip open — never name-by-name
    store reads (review 2026-10-15: with Atlas unreachable those were two
    timeouts per name, twice)."""
    from trading.valuation import pipeline
    want = [f"{sym}.NS" for sym in pipeline.statement_symbols()]
    store = _store_closes()
    prices = {t: store[t] for t in want if store.get(t)}
    missing = [t for t in want if t not in prices]
    for t, df in _zip_frames(missing).items():
        try:
            c = pd.to_numeric(df["Close"], errors="coerce").dropna()
            if len(c):
                prices[t] = float(c.iloc[-1])
        except Exception:                               # noqa: BLE001
            continue
    return prices


def _peer_sig(sector_map: dict) -> str:
    """Short hash of the sector labels of the statement names — the route
    reads them, so a changed label rebuilds the stored base."""
    import hashlib
    import json
    from trading.valuation import pipeline
    try:
        names = [f"{s}.NS" for s in pipeline.statement_symbols()]
    except Exception:                                   # noqa: BLE001
        names = []
    part = {t: sector_map.get(t) for t in names}
    h = hashlib.sha1(json.dumps(part, sort_keys=True, default=str).encode())
    h.update(str(getattr(pipeline, "VERSION", "")).encode())
    for mod in ("route.py", "snapshot.py", "pipeline.py"):
        try:
            h.update((vv._ROOT / "trading" / "valuation" / mod).read_bytes())
        except OSError:
            h.update(mod.encode())
    return h.hexdigest()[:12]


def _base_fresh(built: Any) -> bool:
    try:
        age = (date.today() - date.fromisoformat(str(built)[:10])).days
    except (TypeError, ValueError):
        return False
    return 0 <= age < PEER_BASE_MAX_AGE_DAYS


def peer_base(prices: Optional[dict] = None) -> tuple[list, str, str]:
    """(base rows, built date, source) — in-process copy, else the stored
    settings doc, else built now from the statements and stored."""
    from datetime import datetime
    from trading.valuation import pipeline
    from . import db
    smap = _sector_map()
    sig = _peer_sig(smap)
    if (_PEER_CACHE.get("base") is not None and _PEER_CACHE.get("sig") == sig
            and _base_fresh(_PEER_CACHE.get("built"))):
        return _PEER_CACHE["base"], _PEER_CACHE["built"], _PEER_CACHE.get("source") or "process"
    try:
        doc = db.load_settings(doc_id=PEERS_DOC_ID) or {}
    except Exception:                                   # noqa: BLE001
        doc = {}
    if (doc.get("base") and doc.get("sig") == sig and _base_fresh(doc.get("built"))
            and doc.get("version") == PEER_BASE_VERSION):
        _PEER_CACHE.update({"base": list(doc["base"]), "built": str(doc["built"])[:10],
                            "sig": sig, "source": "stored"})
        return _PEER_CACHE["base"], _PEER_CACHE["built"], "stored"
    if prices is None:
        prices = _peer_prices()
    base = pipeline.peer_base(price_of=prices.get, sector_map=smap,
                              company_names=_company_names())
    built = date.today().isoformat()
    try:
        n_names = len(pipeline.statement_symbols())
    except Exception:                                   # noqa: BLE001
        n_names = 0
    n_priced = sum(1 for v in prices.values() if v)
    coverage = (n_priced / n_names) if n_names else 0.0
    store_it = coverage >= PEER_PRICE_COVERAGE
    src = "built" if store_it else "built_thin"
    _PEER_CACHE.update({"base": base, "built": built, "sig": sig, "source": src,
                        "coverage": round(coverage, 3)})
    if not store_it:
        log.warning("valuation chain: peer base priced on %d of %d names — "
                    "used today, not stored", n_priced, n_names)
        return base, built, src
    try:
        db.save_settings({"base": base, "built": built, "sig": sig, "n": len(base),
                          "n_priced": n_priced, "coverage": round(coverage, 3),
                          "version": PEER_BASE_VERSION,
                          "ts": datetime.utcnow().isoformat()}, doc_id=PEERS_DOC_ID)
    except Exception:                                   # noqa: BLE001
        log.exception("valuation chain: peer base save failed (in-process copy kept)")
    return base, built, src


def peer_multiples(method: str, exclude: str = "") -> dict:
    """Median trailing multiples (P/E, P/B, EV/EBITDA, EV/Sales) of the
    names on the same route among the Nifty-200 statement set. ASSUMED
    peer definition — the owner has not supplied peer groups. Priced once
    per process per day from ONE store query; the price-free base comes
    from peer_base (stored, rebuilt weekly)."""
    from trading.valuation import pipeline
    today = date.today().isoformat()
    if _PEER_CACHE["day"] != today:
        # One build per day per process, under a lock: a second render that
        # arrives mid-build WAITS for the table instead of reading an empty
        # one (and caching peerless rows for 30 min — review 2026-10-15).
        with _PEER_LOCK:
            if _PEER_CACHE["day"] != today:
                by_method: dict = {}
                try:
                    prices = _peer_prices()
                    base, _built, _src = peer_base(prices)
                    by_method = pipeline.peer_rows_from_base(base, prices.get)
                except Exception:                       # noqa: BLE001
                    log.exception("valuation chain: peer table failed")
                _PEER_CACHE["by_method"] = by_method
                _PEER_CACHE["day"] = today
    rows = [r for r in (_PEER_CACHE["by_method"].get(method) or [])
            if r.get("ticker") != exclude]

    def _med(key: str) -> Optional[float]:
        xs = [r[key] for r in rows if V.num(r.get(key)) and r[key] > 0]
        return statistics.median(xs) if xs else None

    built = _PEER_CACHE.get("built")
    return {"n": len(rows), "pe_median": _med("pe"), "pb_median": _med("pb"),
            "ev_ebitda_median": _med("ev_ebitda"), "ev_sales_median": _med("ev_sales"),
            "source": (f"median of {len(rows)} same-route Nifty-200 names (ASSUMED peers"
                       + (f"; statements base built {built}" if built else "") + ")")}


def _sector_map() -> dict:
    try:
        from . import state
        return dict(getattr(state.cfg(), "sector_map", {}) or {})
    except Exception:                                   # noqa: BLE001
        return {}


_NAMES_CACHE: dict = {}


def _company_names() -> dict:
    """ticker → company name from the committed universe file
    (data/upstox_nse_equities.csv: ticker,trading_symbol,name,…) — the
    route's keyword rules read the name. Loaded once per process."""
    if _NAMES_CACHE:
        return _NAMES_CACHE
    import csv
    try:
        with open(vv._ROOT / "data" / "upstox_nse_equities.csv", newline="",
                  encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = (r.get("ticker") or "").strip().upper()
                if t:
                    _NAMES_CACHE[t] = (r.get("name") or "").strip()
    except OSError:
        log.warning("valuation chain: universe file unreadable (names)")
    return _NAMES_CACHE


# ---------------------------------------------------------------- rows

def _macro_inputs(snap: dict, closes: Optional[pd.Series] = None) -> dict:
    """The owner's P/E-bridge market inputs from STORED state: Sensex /
    Nifty levels at the period end and now, VIX, the 10-year yield (rf),
    the stock's measured β on Nifty 50, USDINR now and at the period end.
    Repo / CPI / GDP / Brent are not stored — the pipeline counts them 0
    and lists them DATA_NEEDED."""
    from trading.valuation import snapshot as S
    period_end = snap.get("period_end")
    nifty = index_move("nifty_close", period_end)
    sensex = index_move("sensex_close", period_end)
    vix = vv._index_field("india_vix_close")
    bond = vv._bond_series()
    out: dict = {
        "sensex_start": sensex.get("start"), "sensex_end": sensex.get("end"),
        "nifty_start": nifty.get("start"), "nifty_end": nifty.get("end"),
        "vix": float(vix.dropna().iloc[-1]) if vix is not None and len(vix.dropna()) else None,
        "rf_pct": float(bond.dropna().iloc[-1]) if bond is not None and len(bond.dropna()) else None,
        "beta": None, "usdinr_now": None, "usdinr_base": None,
        "price_now": price_now(closes)[0] if closes is not None else None,
    }
    try:
        fx_now = S.usdinr_at(date.today().isoformat())
        fx_base = S.usdinr_at(period_end) if period_end else None
        out["usdinr_now"] = fx_now[0] if fx_now else None
        out["usdinr_base"] = fx_base[0] if fx_base else None
    except Exception:                                   # noqa: BLE001
        pass
    try:
        nifty_s = vv._index_field("nifty_close")
        if closes is not None and nifty_s is not None:
            b = vc.beta(closes, nifty_s)
            out["beta"] = b.get("beta") if isinstance(b, dict) else V.num(b)
    except Exception:                                   # noqa: BLE001
        pass
    return out


def _prefetch_closes(tickers: list) -> dict:
    """{ticker: close series} for the rows about to be computed, in ONE
    store query ($in + $slice). Names the store lacks are absent; row_for
    then falls back to _closes (repo zip)."""
    from . import db
    out: dict = {}
    if not tickers:
        return out
    try:
        if not db.is_connected():
            return out
        from trading.history_store import records_to_df
        docs = db.history_docs(list(tickers), rows=PRICE_TAIL) or {}
        for t, doc in docs.items():
            s = _close_series(records_to_df(doc))
            if s is not None:
                out[t] = s
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: batched store read failed")
    return out


def row_for(ticker: str, cfg, p, *, show_terms: bool = False,
            overrides: Optional[dict] = None,
            closes: Optional[pd.Series] = None) -> dict:
    from trading.valuation import pipeline
    if closes is None:
        closes = _closes(ticker)
    px, px_date = price_now(closes)
    rec = pipeline.evaluate(
        ticker, price=px, price_date=px_date,
        sector_map=dict(getattr(cfg, "sector_map", {}) or {}),
        company_name=_company_names().get(ticker, ""),
        price_at=lambda when: price_at(closes, when),
        own_pe_history=lambda snap: own_pe_history(closes, snap),
        peers=peer_multiples,
        macro=lambda snap: _macro_inputs(snap, closes),
        overrides=overrides,
    )
    rec["key_terms"] = [t for t in rec.get("terms_rows", []) if t["name"] in KEY_TERMS]
    rec["all_terms"] = rec.get("terms_rows", []) if show_terms else []
    rec["n_terms"] = len(rec.get("terms_rows", []))
    return rec


def time_budget_s() -> float:
    """§12's time budget per render, seconds (env SB_DEN_CHAIN_BUDGET_S;
    <= 0 = no budget)."""
    import os
    try:
        return float(os.environ.get("SB_DEN_CHAIN_BUDGET_S", DEFAULT_TIME_BUDGET_S))
    except ValueError:
        return DEFAULT_TIME_BUDGET_S


def rss_ceiling_mb() -> float:
    """Above this resident memory §12 computes no new row (env
    SB_DEN_CHAIN_RSS_CEILING_MB; <= 0 = no ceiling)."""
    import os
    try:
        return float(os.environ.get("SB_DEN_CHAIN_RSS_CEILING_MB", DEFAULT_RSS_CEILING_MB))
    except ValueError:
        return DEFAULT_RSS_CEILING_MB


def busy_reason() -> str:
    """Why §12 must not compute right now ("" = free to compute). A scan
    rotation holds the scheduler's rotation lock; the 09:20 trigger layer
    runs after it is released, so the memory reading covers that one."""
    try:
        from . import scheduler
        lock = getattr(scheduler, "_ROTATION_LOCK", None)
        if lock is not None and lock.locked():
            return "a scan rotation is running on the box"
    except Exception:                                   # noqa: BLE001
        pass
    ceiling = rss_ceiling_mb()
    if ceiling > 0:
        try:
            from .memory_relief import rss_mb
            r = rss_mb()
        except Exception:                               # noqa: BLE001
            r = None
        if r is not None and r >= ceiling:
            return (f"the app is using {r:.0f} MiB of its 512 MiB "
                    f"(§12 computes below {ceiling:.0f})")
    return ""


def _row_key(ticker: str, overrides: Optional[dict], show_terms: bool) -> tuple:
    import json
    return (ticker, date.today().isoformat(),
            json.dumps(overrides or {}, sort_keys=True, default=str), bool(show_terms))


def _row_cache_get(key: tuple) -> Optional[dict]:
    import time as _time
    with _ROW_LOCK:
        hit = _ROW_CACHE.get(key)
        if hit is None or _time.monotonic() - hit[0] >= ROW_TTL_S:
            _ROW_CACHE.pop(key, None)
            return None
        return hit[1]


def _row_cache_put(key: tuple, rec: dict) -> None:
    import time as _time
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


def not_computed_record(ticker: str, why: str) -> dict:
    """failed_record's shape, flagged `not_computed` — a row §12 chose not
    to compute on this render (time budget or a busy box), never a crash."""
    from trading.valuation import pipeline
    rec = pipeline.failed_record(ticker, why)
    rec["not_computed"] = True
    return rec


def empty_section(error: str = "", typed: str = "") -> dict:
    from trading.valuation import pipeline
    return {"rows": [], "n": 0, "max_rows": MAX_ROWS, "typed": typed,
            "typed_full": vv.normalise_symbol(typed) if typed else "",
            "show_terms": False, "error": error,
            "n_statements": pipeline.statement_coverage(),
            "assumed": pipeline.assumed_for_page(),
            "today": date.today().isoformat(),
            "drafted": False, "n_edited": 0, "drafted_keys": [],
            "budget_s": time_budget_s(), "elapsed_s": 0.0,
            "computed": 0, "cached": 0, "skipped_budget": 0,
            "skipped_busy": 0, "busy": "", "row_ttl_min": int(ROW_TTL_S // 60)}


def section(cfg, p, typed: str = "", show_terms: bool = False) -> dict:
    import time as _time
    out = empty_section("", typed)
    typed_full = ""
    if typed:
        typed_full = vv.normalise_symbol(typed)
        if not typed_full:
            out["error"] = f"'{typed}' is not a symbol"
    out["typed_full"] = typed_full
    out["show_terms"] = bool(show_terms and typed_full)
    overrides = load_assumptions()
    out.update({"drafted": bool(overrides), "n_edited": len(overrides),
                "drafted_keys": sorted(overrides)})
    budget = time_budget_s()
    t0 = _time.monotonic()
    rows = []
    computed = cached = skipped_budget = skipped_busy = 0
    names = names_to_show(p, typed_full)
    todo = [t for t in names
            if _row_cache_get(_row_key(t, overrides, out["show_terms"] and t == typed_full)) is None]
    owns = _SECTION_LOCK.acquire(blocking=False) if todo else False
    busy = busy_reason() if owns else ("another open of the Den is computing these rows right now"
                                       if todo else "")
    try:
        pre = _prefetch_closes(todo) if (todo and not busy) else {}
        rows, computed, cached, skipped_budget, skipped_busy, busy = _section_rows(
            cfg, p, names, typed_full, out["show_terms"], overrides, budget, t0, busy, pre)
    finally:
        if owns:
            _SECTION_LOCK.release()
    if computed:
        try:
            from .memory_relief import release
            release("den chain")
        except Exception:                               # noqa: BLE001
            pass
    out.update({"rows": rows, "n": len(rows), "busy": busy,
                "elapsed_s": round(_time.monotonic() - t0, 1),
                "computed": computed, "cached": cached,
                "skipped_budget": skipped_budget, "skipped_busy": skipped_busy})
    return out


def _section_rows(cfg, p, names, typed_full, show_terms, overrides, budget, t0,
                  busy, pre):
    """The row loop. The busy reading is re-taken before every NEW row, so a
    scan rotation that starts mid-render stops it."""
    import time as _time
    rows = []
    computed = cached = skipped_budget = skipped_busy = 0
    for t in names:
        terms = show_terms and t == typed_full
        key = _row_key(t, overrides, terms)
        hit = _row_cache_get(key)
        if hit is not None:
            rows.append(hit)
            cached += 1
            continue
        if not busy and computed:
            busy = busy_reason()
        if busy:
            rows.append(not_computed_record(
                t, f"not computed — {busy}; open the Den again once it finishes"))
            skipped_busy += 1
            continue
        if budget > 0 and _time.monotonic() - t0 >= budget:
            rows.append(not_computed_record(
                t, f"not computed — the §12 time budget ({budget:.0f} s) was spent on "
                   f"the rows above; open the Den again (computed rows are kept "
                   f"{int(ROW_TTL_S // 60)} min) or type this name in the box"))
            skipped_budget += 1
            continue
        try:
            rec = row_for(t, cfg, p, show_terms=terms, overrides=overrides or None,
                          closes=pre.get(t))
            _row_cache_put(key, rec)
            computed += 1
            rows.append(rec)
        except Exception as e:                          # noqa: BLE001
            log.exception("valuation chain: row failed for %s", t)
            from trading.valuation import pipeline
            rows.append(pipeline.failed_record(t, str(e)[:120]))
    return rows, computed, cached, skipped_budget, skipped_busy, busy


# ------------------------------------------------------ §13 formulas page

FAMILY_RULES = [
    ("earnings", "value per share = scenario EPS × band [min(peer P/E, own low), mean(peer, own median), max(peer, own high)]; "
                 "no own history → the three cases' own closing P/E"),
    ("book", "justified P/B = (ROE − g) / (COE − g), COE = rf + β × ERP; value = P/B × BVPS; "
             "downside / upside = ROE ∓ step; g = min(long-run g, ROE × (1 − payout))"),
    ("ev_ebitda", "EV = multiple × EBITDA per share; equity per share = EV − net debt − minorities; "
                  "metals use the mid-cycle (5-FY mean) EBITDA"),
    ("ev_sales", "EV = multiple × sales per share; equity per share = EV − net debt − minorities"),
    ("nav", "NAV per share = DATA_NEEDED (no project NAV stored); the P/B route is shown as a labelled fallback"),
    ("embedded_value", "EV/VNB = DATA_NEEDED (no embedded-value disclosure stored)"),
    ("sotp", "sum of parts = DATA_NEEDED (no segment values stored)"),
]

STAGE_RULES = [
    ("07-08 snapshot lock", "latest audited FY from the statements zip (+ monthly refresh); publication date = "
                            "period end + lag (ASSUMED) unless an NSE result date is given; currency: an ADR name "
                            "converts at the period-end USDINR only when it is on the suspect list AND price ÷ EPS "
                            "exceeds the USD signature — list alone never converts, signature alone = DATA_NEEDED; "
                            "sha256 lock hash over the fields"),
    ("09 route", "label = cfg.sector_map → Nifty-500 industry file → unmapped; keyword rules for the coarse labels "
                 "(bank / NBFC / insurer / AMC, hospital, realty / cement / EPC, retail / hotel / airline); PSU and "
                 "holding overlays; anything else UNDECLARED with a nearest suggestion, never forced"),
    ("10 shares", "today's count vs the prior period: split = old face ÷ new face, bonus a:b = 1 + a/b; a period pair "
                  "reconciles within ± tolerance (Part A) else 'unexplained'; rights / buyback / issuance carry no factor; "
                  "ESOP pool = DATA_NEEDED"),
    ("11 normalised PAT", "normalised = reported − unusual items + their tax effect (tax effect = unusual × effective "
                          "rate, bounded 0-50%); checked against the audited Normalized Income line when present"),
    ("12 quality", "cash conversion = OCF ÷ PAT, net debt ÷ EBITDA, interest cover = EBIT ÷ interest, each flagged at "
                   "the thresholds in Part A; lenders' leverage lines DATA_NEEDED; score = ASSUMED weights"),
    ("15-16 cases", "the chain in Part B3 run three times with the case deltas (Part A) added to revenue growth, "
                    "EBITDA margin and the P/E bridge total; constant-currency growth for converted names"),
    ("17 range", "per family as listed under B2; 3×3 grid growth {−1,0,+1}pp × margin {−1,0,+1}pp and a multiple row "
                 "{−10,0,+10} points on the bridge"),
    ("17b context", "sector leg (1:1 and measured β), rates leg (r_then − g) / (r_now + shift − g), sentiment margin "
                    "= base discount + steps per gauge ON — §11 above"),
    ("18 verdict", "attractive when base ≥ price × (1 + margin); watch within ± band of base; unattractive above base "
                   "× (1 + premium); insufficient when (high − low) / base or the stated uncertainty exceeds the width "
                   "cap — bands in Part A; never a trade permission"),
]


def empty_formulas_section(flag: str = "", error: str = "") -> dict:
    return {"flag": flag, "error": error, "groups": [], "rows": [], "n_editable": 0,
            "n_edited": 0, "terms": [], "routes": [], "chain_cells": [],
            "family_rules": FAMILY_RULES, "stage_rules": STAGE_RULES}


def formulas_section(flag: Optional[str] = None, error: str = "") -> dict:
    """Den §13: the editable assumption rows (with the stored draft) and
    every formula registry the chain runs. Stored inputs only."""
    from trading.valuation import assumptions as ASM
    from trading.valuation import route as R
    from trading.valuation import scenarios as SC
    from trading.valuation import terms as T
    values = load_assumptions()
    rows = ASM.describe(values)
    terms = [{"name": name, "formula": e.get("formula") or "", "stage": e.get("stage") or ""}
             for name, e in T.TERMS.items()]
    routes = []
    for r in R.SECTOR_METHODS:
        routes.append({"sector": r.get("sector") or r.get("label") or "",
                       "method": r.get("method") or "", "family": r.get("family") or "",
                       "basis": r.get("primary_basis") or "",
                       "per_share": r.get("per_share_metric") or "",
                       "rule": r.get("key_drivers") or "",
                       "bad_time": r.get("bad_time_behaviour") or ""})
    return {"flag": flag or "", "error": error, "groups": ASM.GROUPS, "rows": rows,
            "n_editable": len(rows), "n_edited": len(values),
            "terms": terms, "routes": routes, "chain_cells": SC.chain_formulas(),
            "family_rules": FAMILY_RULES, "stage_rules": STAGE_RULES}
