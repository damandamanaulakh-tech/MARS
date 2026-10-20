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
  * which names to show: the typed one, held names, then the §11 stored
    targets A-Z (cap MAX_ROWS; the cut is printed).

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

CLARITY FIX 2026-10-15 (reviewers on the live Den): with Atlas unreachable
while db._CONNECTED stays True (XR-071) every store call waited ~3 s and a
§12 open made ~9 of them (modelled: first row 31.8 s). Within ONE render the
first store call that fails or is slow is now the last one — the rest of
that open reads the committed repo files only and the page says the
database was the cause (`_StoreGate`). Busy rows name their own remedy
(scan / memory / another tab); held names come before §11 targets and a cut
by the 25-row cap is printed; a crashed row, a name with no statements and
a range that could not be built each print one honest line instead of an
INSUFFICIENT verdict; the "use as §11 target" button shows only on a judged
verdict.
"""
from __future__ import annotations

import logging
import re
import statistics
import threading
from collections import Counter
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

#: The snapshot source of a name with FULL statements (the committed zip);
#: anything else (the summary-only fundamentals file, or nothing) prints
#: one NO STATEMENTS line instead of a row of DATA_NEEDED cells.
FULL_STATEMENTS_SOURCE = "yahoo_statements_zip"
#: The verdicts that JUDGED the price against a range — only these may be
#: pushed into §11 as a target (an INSUFFICIENT range is too uncertain).
JUDGED_VERDICTS = ("attractive", "watch", "unattractive")


# ------------------------------------------- render-scoped store gate

#: Review 2026-10-15 (XR-071 on §12): web/db.py swallows pymongo's 3 s
#: server-selection timeout and answers {} / [] — so a dead Atlas looks like
#: a slow empty answer. Within one §12 render the first store call that
#: raises, waits SLOW_STORE_S and brings nothing back, or is slow at all
#: (settings / index reads over SLOW_STORE_S, the two price reads over
#: HEAVY_SLOW_STORE_S — they legitimately move megabytes) is the LAST store
#: call of that render. ASSUMED thresholds: the timeout is 3 s.
SLOW_STORE_S = 2.0
HEAVY_SLOW_STORE_S = 8.0
#: Rows computed after the gate closed were priced from the repo copy; they
#: are kept this long (not ROW_TTL_S) so a recovered database replaces them.
DEGRADED_ROW_TTL_S = 300.0


class _StoreGate:
    """Store calls of ONE §12 render. Once `down`, every later call returns
    its default at once (the callers then read the committed files)."""

    def __init__(self) -> None:
        self.down = False
        self.why = ""
        self.calls = 0
        self.spent_s = 0.0

    def call(self, label: str, fn, *a, default=None, heavy: bool = False, **k):
        import time as _time
        if self.down:
            return default
        self.calls += 1
        t = _time.monotonic()
        try:
            out = fn(*a, **k)
        except Exception as e:                          # noqa: BLE001
            dt = _time.monotonic() - t
            self.spent_s += dt
            self._trip(f"the {label} failed ({type(e).__name__}) after {dt:.1f} s")
            return default
        dt = _time.monotonic() - t
        self.spent_s += dt
        empty = out is None or (hasattr(out, "__len__") and len(out) == 0)
        limit = HEAVY_SLOW_STORE_S if heavy else SLOW_STORE_S
        if empty and dt >= SLOW_STORE_S:
            self._trip(f"the {label} waited {dt:.1f} s and got no answer")
        elif dt >= limit:
            self._trip(f"the {label} took {dt:.1f} s")
        return out

    def _trip(self, why: str) -> None:
        self.down = True
        self.why = why
        log.warning("valuation chain: %s — no more database reads on this open", why)


class _Render:
    """Per-render state, carried thread-locally through the row helpers."""

    def __init__(self, budget: float, t0: float) -> None:
        self.gate = _StoreGate()
        self.budget = budget
        self.t0 = t0
        self.index: dict = {}               # field → series, read once per render
        self.peer_build_skipped = False

    def budget_spent(self) -> bool:
        import time as _time
        return self.budget > 0 and _time.monotonic() - self.t0 >= self.budget


_RENDER = threading.local()


def _render() -> Optional[_Render]:
    return getattr(_RENDER, "ctx", None)


def _gate() -> _StoreGate:
    """The current render's gate; outside a render a fresh (open) one."""
    r = _render()
    return r.gate if r is not None else _StoreGate()


class _BudgetSpent(Exception):
    """row_for could not build the peer table inside the time budget."""


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
    g = _gate()
    try:
        if g.call("database check", db.is_connected, default=False):
            from trading.history_store import records_to_df
            doc = (g.call("price read", db.history_docs, [ticker], rows=PRICE_TAIL,
                          default={}, heavy=True) or {}).get(ticker)
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
    try:
        return _close_series(df.tail(PRICE_TAIL)) if df is not None else None
    except Exception:                                   # noqa: BLE001
        return None


_FILE_INDEX: dict = {}


def _file_index_field(field: str) -> Optional[pd.Series]:
    """One close column of the COMMITTED index file only — no stored tail.
    Read when this render's database gate is closed (the stored tail is
    asked through valuation_view, which would wait on the dead store
    again). Same parse as valuation_view._read_index_field."""
    path = getattr(vv, "_INDEX_CSV", vv._ROOT / "data" / "index_daily_15y.csv")
    try:
        st = path.stat()
        key = (field, st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    hit = _FILE_INDEX.get(key)
    if hit is None:
        try:
            df = pd.read_csv(path, usecols=["trade_date", field])
            s = pd.Series(pd.to_numeric(df[field], errors="coerce").to_numpy(),
                          index=pd.to_datetime(df["trade_date"], dayfirst=True,
                                               errors="coerce"))
            hit = s[s.index.notna()].dropna().sort_index()
        except Exception as e:                          # noqa: BLE001
            log.warning("valuation chain: index csv unreadable (%s): %s", field, e)
            return None
        if len(_FILE_INDEX) > 16:
            _FILE_INDEX.clear()
        _FILE_INDEX[key] = hit
    return hit.copy()


def _index_series(field: str) -> Optional[pd.Series]:
    """An index close column for this render: read ONCE per render (the
    old code asked four times per row), through valuation_view's cached
    reader (committed file + stored tail) while the database answers, the
    committed file alone once this render's gate is closed."""
    r = _render()
    if r is not None and field in r.index:
        s = r.index[field]
        return None if s is None else s.copy()
    g = _gate()
    if g.down:
        s = _file_index_field(field)
    else:
        s = g.call("index tail read", vv._index_field, field, default=None)
        if s is None and g.down:
            s = _file_index_field(field)
    if r is not None:
        r.index[field] = s
    return None if s is None else s.copy()


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
    s = _index_series(field)
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


class ShownNames(list):
    """names_to_show's answer: the names shown, plus `.cut` = the names the
    MAX_ROWS cap left out (printed on the page, never dropped silently)."""

    def __init__(self, names=(), cut=()) -> None:
        super().__init__(names)
        self.cut = list(cut)


def _target_names() -> list[str]:
    """The §11 stored targets' tickers. Read through this render's gate;
    once it is closed, the in-process copy valuation_view keeps."""
    g = _gate()
    doc = None
    if not g.down:
        doc = g.call("§11 targets read", vv.load_doc, default=None)
    if doc is None:
        doc = {"targets": dict((getattr(vv, "LAST", {}) or {}).get("targets") or {})}
    return list((doc.get("targets") or {}).keys())


def names_to_show(p, typed: str = "") -> "ShownNames":
    """Typed first, then HELD names, then the §11 stored targets A-Z;
    unique; capped at MAX_ROWS. Review 2026-10-15: targets used to come
    before held names, so 25 stored targets pushed every held name off the
    chain without a word — the book's own names now come first and `.cut`
    names whatever the cap left out."""
    out: list[str] = []
    if typed:
        out.append(typed)
    for t in _held_names(p):
        if t not in out:
            out.append(t)
    try:
        for t in sorted(_target_names()):
            if t not in out:
                out.append(t)
    except Exception:                                   # noqa: BLE001
        pass
    return ShownNames(out[:MAX_ROWS], out[MAX_ROWS:])


# ------------------------------------------------------ assumption draft

def load_assumptions() -> dict:
    """{key: value} of the stored draft — Mongo doc first, the in-process
    copy when the doc cannot be read. Unknown keys are dropped."""
    from trading.valuation import assumptions as ASM
    from . import db
    values: dict = {}
    try:
        doc = _gate().call("§13 assumption draft read", db.load_settings,
                           doc_id=ASSUMPTIONS_DOC_ID, default={}) or {}
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
        docs = _gate().call("last-close read of every stored name",
                            lambda: list(db.history_tails(1) or ()),
                            default=[], heavy=True) or []
        for doc in docs:
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
    g = _gate()
    try:
        doc = g.call("stored peer base read", db.load_settings,
                     doc_id=PEERS_DOC_ID, default={}) or {}
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
        g.call("peer base save", db.save_settings,
               {"base": base, "built": built, "sig": sig, "n": len(base),
                "n_priced": n_priced, "coverage": round(coverage, 3),
                "version": PEER_BASE_VERSION,
                "ts": datetime.utcnow().isoformat()}, doc_id=PEERS_DOC_ID)
    except Exception:                                   # noqa: BLE001
        log.exception("valuation chain: peer base save failed (in-process copy kept)")
    return base, built, src


def _peer_table_stale(today: str) -> bool:
    """The priced peer table needs (re)building: another day, or it was
    priced from the repo copy while a render's database gate was closed and
    the current render's database still answers."""
    if _PEER_CACHE.get("day") != today:
        return True
    return bool(_PEER_CACHE.get("degraded")) and not _gate().down


def peer_multiples(method: str, exclude: str = "") -> dict:
    """Median trailing multiples (P/E, P/B, EV/EBITDA, EV/Sales) of the
    names on the same route among the Nifty-200 statement set. ASSUMED
    peer definition — the owner has not supplied peer groups. Priced once
    per process per day from ONE store query; the price-free base comes
    from peer_base (stored, rebuilt weekly)."""
    from trading.valuation import pipeline
    today = date.today().isoformat()
    if _peer_table_stale(today):
        r = _render()
        if r is not None and r.budget_spent():
            # Review 2026-10-15: the budget was checked only before a row,
            # so the first row ran on into a ~9 s peer build. A build that
            # would start after the budget is spent is not started; row_for
            # sees the flag and prints the row NOT COMPUTED (an empty peer
            # table here would otherwise be cached as the row's answer).
            r.peer_build_skipped = True
            return {"n": 0, "pe_median": None, "pb_median": None,
                    "ev_ebitda_median": None, "ev_sales_median": None,
                    "source": "peer table not built — the §12 time budget was spent"}
        # One build per day per process, under a lock: a second render that
        # arrives mid-build WAITS for the table instead of reading an empty
        # one (and caching peerless rows for 30 min — review 2026-10-15).
        with _PEER_LOCK:
            if _peer_table_stale(today):
                by_method: dict = {}
                try:
                    prices = _peer_prices()
                    base, _built, _src = peer_base(prices)
                    by_method = pipeline.peer_rows_from_base(base, prices.get)
                except Exception:                       # noqa: BLE001
                    log.exception("valuation chain: peer table failed")
                _PEER_CACHE["by_method"] = by_method
                _PEER_CACHE["day"] = today
                # Priced from the repo copy because THIS render's database
                # gate closed: rebuilt by a later open whose database answers.
                _PEER_CACHE["degraded"] = _gate().down
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
    vix = _index_series("india_vix_close")
    bond = vv._bond_series()
    out: dict = {
        "sensex_start": sensex.get("start"), "sensex_end": sensex.get("end"),
        "nifty_start": nifty.get("start"), "nifty_end": nifty.get("end"),
        "vix": float(vix.dropna().iloc[-1]) if vix is not None and len(vix.dropna()) else None,
        "rf_pct": float(bond.dropna().iloc[-1]) if bond is not None and len(bond.dropna()) else None,
        "beta": None, "usdinr_now": None, "usdinr_base": None,
        "price_now": price_now(closes)[0] if closes is not None else None,
    }
    out["usdinr_now_date"] = out["usdinr_base_date"] = None
    try:
        fx_now = S.usdinr_at(date.today().isoformat())
        fx_base = S.usdinr_at(period_end) if period_end else None
        out["usdinr_now"] = fx_now[0] if fx_now else None
        out["usdinr_base"] = fx_base[0] if fx_base else None
        # the dates the two rates were read on (VAL-13a / VAL-15)
        out["usdinr_now_date"] = fx_now[1] if fx_now else None
        out["usdinr_base_date"] = fx_base[1] if fx_base else None
    except Exception:                                   # noqa: BLE001
        pass
    try:
        nifty_s = _index_series("nifty_close")
        if closes is not None and nifty_s is not None:
            b = vc.beta(closes, nifty_s)
            out["beta"] = b.get("beta") if isinstance(b, dict) else V.num(b)
    except Exception:                                   # noqa: BLE001
        pass
    return out


def _prefetch_closes(tickers: list) -> dict:
    """{ticker: close series} for the rows about to be computed, in ONE
    store query ($in + $slice), through this render's database gate. Names
    the store lacks are absent; the row loop reads THOSE straight from the
    repo zip — never a second store read for a name this query already
    asked about (review 2026-10-15)."""
    from . import db
    out: dict = {}
    if not tickers:
        return out
    g = _gate()
    try:
        if not g.call("database check", db.is_connected, default=False):
            return out
        from trading.history_store import records_to_df
        docs = g.call("price read", db.history_docs, list(tickers), rows=PRICE_TAIL,
                      default={}, heavy=True) or {}
        for t, doc in docs.items():
            s = _close_series(records_to_df(doc))
            if s is not None:
                out[t] = s
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: batched store read failed")
    return out


#: row_for's "closes not handed in" marker (None = handed in, no price).
_UNSET: Any = object()


def row_for(ticker: str, cfg, p, *, show_terms: bool = False,
            overrides: Optional[dict] = None,
            closes: Any = _UNSET, price_source: str = "") -> dict:
    """One chain record. `closes` handed in (a series, or None = the store
    and the repo zip both lack the name) is used as is; not handed in, it
    is read here. `price_source` ("store" / "repo copy") is printed beside
    the price."""
    from trading.valuation import pipeline
    if closes is _UNSET:
        closes = _closes(ticker)
    px, px_date = price_now(closes)
    mac_box: dict = {}

    def _macro(snap):
        m = _macro_inputs(snap, closes)
        mac_box["m"] = m
        return m

    rec = pipeline.evaluate(
        ticker, price=px, price_date=px_date,
        sector_map=dict(getattr(cfg, "sector_map", {}) or {}),
        company_name=_company_names().get(ticker, ""),
        price_at=lambda when: price_at(closes, when),
        own_pe_history=lambda snap: own_pe_history(closes, snap),
        peers=peer_multiples,
        macro=_macro,
        overrides=overrides,
    )
    r = _render()
    if r is not None and r.peer_build_skipped:
        # computed without the peer table — never shown or kept as an answer
        r.peer_build_skipped = False
        raise _BudgetSpent("the peer table was not built")
    rec["key_terms"] = [t for t in rec.get("terms_rows", []) if t["name"] in KEY_TERMS]
    rec["all_terms"] = rec.get("terms_rows", []) if show_terms else []
    rec["n_terms"] = len(rec.get("terms_rows", []))
    rec["price_source"] = price_source if px is not None else ""
    try:
        rec["fx"] = fx_block(rec, mac_box.get("m") or {})
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: rupee row block failed for %s", ticker)
        rec["fx"] = {"applies": False}
    return rec


# ------------------------------------------------- rupee row (E101) line

#: VAL-13a: the USD-INR "now" rate is read from data/usdinr_daily.csv,
#: which nothing refreshes; past this many days the row says it is stale.
FX_STALE_DAYS = 10


def fx_block(rec: dict, mac: dict, today: Optional[date] = None) -> dict:
    """The IT rupee row (E101) as §12 prints it: the 'now' rate and its
    date (stale past FX_STALE_DAYS, VAL-13a), the base rate the chain
    actually divided by and its date, and E101's formula with that base
    (VAL-15). Only IT-services routes carry the rupee leg; other rows get
    {"applies": False}."""
    sector = (rec.get("route") or {}).get("sector") or ""
    if not str(sector).startswith("IT services"):
        return {"applies": False}
    today = today or date.today()
    base_case = (((rec.get("detail") or {}).get("cases") or {}).get("base") or {})
    used = ((base_case.get("inputs") or {}).get("market") or {})
    now = V.num(used.get("usdinr"))
    base = V.num(used.get("usdinr_base"))
    if now is None:
        now = V.num(mac.get("usdinr_now"))
    if base is None:
        base = V.num(mac.get("usdinr_base"))
    now_date = mac.get("usdinr_now_date")
    base_date = mac.get("usdinr_base_date")
    age = None
    if now_date:
        try:
            age = (today - date.fromisoformat(str(now_date)[:10])).days
        except ValueError:
            age = None
    # the pipeline sets now = base (row counted 0) when either rate is missing
    counted = (V.num(mac.get("usdinr_now")) is not None
               and V.num(mac.get("usdinr_base")) is not None)
    pct = V.num((base_case.get("cells") or {}).get("E101"))
    try:
        from trading.valuation import scenarios as SC
        fmt = getattr(SC, "e101_formula", None)
        formula = fmt(base if counted else None, base_date if counted else None) if fmt \
            else "ROUND((E78 / USD-INR at the base year-end date - 1)*100,2)"
    except Exception:                                   # noqa: BLE001
        formula = "ROUND((E78 / USD-INR at the base year-end date - 1)*100,2)"
    return {"applies": True, "counted": counted,
            "now": now if counted else V.num(mac.get("usdinr_now")),
            "now_date": now_date, "age_days": age,
            "stale": bool(age is not None and age > FX_STALE_DAYS),
            "stale_days": FX_STALE_DAYS,
            "base": base if counted else V.num(mac.get("usdinr_base")),
            "base_date": base_date, "pct": pct if counted else None,
            "formula": formula}


# ------------------------------------------------------ known faults

#: VAL-01: groups whose businesses span sectors; the chain values each on
#: the peers of ONE route (no sum-of-parts yet). Delete when VAL-01 lands.
CONGLOMERATES = ("RELIANCE", "ADANIENT", "BAJAJFINSV", "ITC", "GRASIM", "LT",
                 "M&M", "TATAMOTORS")


def _ps(v: Optional[float]) -> str:
    return f"₹{v:,.2f}/share"


def known_faults(rec: dict) -> list:
    """Still-open flaws that bend THIS row's printed target (owner order
    2026-10-21: show a red '⚠ known fault' line). Every rule lives here, one
    block per flaw, so a flag is deleted by deleting its block when the fix
    lands. Facts come from the record's `range_flags` (pipeline); a record
    without them (built before that field existed) falls back to the route
    and the range notes, and says less rather than guess."""
    out: list = []
    if row_kind(rec) != "full":
        return out
    rng = rec.get("range") or {}
    if rng.get("status") != "OK":
        return out                                      # no target printed
    fl = rec.get("range_flags") or {}
    route = rec.get("route") or {}
    family = fl.get("family") or rng.get("family") or route.get("family") or ""
    method = fl.get("method") or route.get("method") or ""
    sym = str(rec.get("ticker") or "").upper().replace(".NS", "").replace(".BO", "")

    # VAL-07 — EV/EBITDA cases inverted, then sorted silently
    if family not in ("ev_ebitda", "ev_sales"):
        pass
    elif fl:
        if fl.get("inverted"):
            printed = fl.get("printed_case") or {}
            pc = fl.get("printed_base_case") or printed.get("base")
            if pc and pc != "base":
                txt = (f"the three cases came out inverted and were sorted, so the "
                       f"printed target is the {pc} case, not the base case")
            else:
                txt = (f"the three cases came out inverted and were sorted: the printed "
                       f"low is the {printed.get('low') or '?'} case and the high the "
                       f"{printed.get('high') or '?'} case")
            if fl.get("midcycle_base"):
                txt += (" (the base uses mid-cycle EBITDA — the average of past years — "
                        "while downside and upside use next year's forecast)")
            out.append({"code": "VAL-07", "text": txt})
    else:
        notes = " ".join(str(n) for n in (rng.get("notes") or []))
        if "inverted" in notes:
            out.append({"code": "VAL-07",
                        "text": "the three cases came out inverted and were sorted, so the "
                                "printed target may be another case than the base"})

    # VAL-01 — a conglomerate on one sector's peers
    if sym in CONGLOMERATES:
        sec = route.get("sector") or fl.get("sector") or "one route"
        out.append({"code": "VAL-01",
                    "text": f"a group with several businesses valued on the peers of one "
                            f"route ({sec}); its other businesses are not valued "
                            f"(no sum-of-parts yet)"})

    # VAL-12a — the stock's EV takes minorities / short-term investments, the peers' do not
    if family in ("ev_ebitda", "ev_sales"):
        mi = V.num(fl.get("minorities_per_share"))
        sti = V.num(fl.get("short_term_investments_per_share"))
        parts = []
        if mi is not None and abs(mi) >= 0.005:
            parts.append(f"takes off {_ps(mi)} for minority interest")
        if sti is not None and abs(sti) >= 0.005:
            parts.append(f"credits {_ps(sti)} of short-term investments")
        if parts:
            out.append({"code": "VAL-12a",
                        "text": "this stock's value " + " and ".join(parts) +
                                ", but the peers' EV multiple counts neither "
                                f"({fl.get('peer_ev_rule') or 'peer EV = price × shares + debt − cash'}), "
                                "so the two sides are not measured the same way"})

    # VAL-02 — lender's equity line unchecked
    lender = fl.get("lender") if fl else method in ("bank_pb_roe", "nbfc_pb")
    if lender and family == "book":
        out.append({"code": "VAL-02",
                    "text": "equity line from the feed not checked against the bank's "
                            "reported net worth; an inflated equity line (as after a merger) "
                            "lowers the ROE and pulls this target down"})

    # VAL-12b — power utilities on the lender formula
    power = fl.get("power_utility") if fl else method == "power_regulated"
    if power and family == "book":
        out.append({"code": "VAL-12b",
                    "text": "valued with the bank formula; debt checks off — a power "
                            "company carries heavy debt that this route never tests"})
    return out


def row_kind(rec: dict) -> str:
    """How §12 prints a record: not_computed / failed / no_statements (one
    honest line each) or full (the whole row)."""
    if rec.get("not_computed"):
        return "not_computed"
    if rec.get("failed"):
        return "failed"
    if (rec.get("snapshot") or {}).get("source") != FULL_STATEMENTS_SOURCE:
        return "no_statements"
    return "full"


def _marked(rec: dict) -> dict:
    rec["row_kind"] = row_kind(rec)
    asm = rec.get("assumptions")
    if isinstance(asm, dict) and "drafted_effective" not in asm:
        # a record built without the VAL-11 split: derive it here
        eff = effective_draft_keys(asm.get("drafted_values") or {})
        asm["drafted_effective"] = eff
        rec["drafted"] = bool(eff)
    try:
        rec["known_faults"] = known_faults(rec)
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: known-fault rules failed for %s", rec.get("ticker"))
        rec["known_faults"] = []
    return rec


# ------------------------------------------------- draft keys in effect

#: VAL-11: drafted numbers that change no result yet. Mirrors
#: assumptions.NO_EFFECT_REASONS; used only if that registry is absent.
_NO_EFFECT_FALLBACK = ("verdict.max_uncertainty_pct", "verdict.watch_band_pct",
                       "terms.dcf_growth_cap_pct", "snapshot.publication_lag_days")


def effective_draft_keys(values: Optional[dict]) -> list:
    """The drafted keys that really change a §12 result (the banner, its
    count and the per-row tag read these; VAL-11). §12 passes no as-of
    date, so the publication lag never counts here."""
    from trading.valuation import assumptions as ASM
    fn = getattr(ASM, "effective_keys", None)
    if fn is not None:
        return list(fn(values))
    return sorted(k for k in (values or {}) if k not in _NO_EFFECT_FALLBACK)


def no_effect_reason(key: str) -> str:
    from trading.valuation import assumptions as ASM
    reasons = getattr(ASM, "NO_EFFECT_REASONS", None) or {}
    if key in reasons:
        return reasons[key]
    return "changes no result yet" if key in _NO_EFFECT_FALLBACK else ""


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


#: What the owner can do about each kind of busy box (review 2026-10-15:
#: one banner told a memory-held box to "open the Den again once it
#: finishes" — nothing finishes; and "stay away 15 minutes" in market
#: hours would let the free plan sleep through the 30-minute exit checks).
BUSY_REMEDY = {
    "rotation": "open the Den again when the scan finishes",
    "memory": ("the box keeps the memory a scan used until the app restarts, "
               "so this row fills in after the next restart"),
    "other_tab": "reload in a few seconds",
}
OTHER_TAB_TEXT = "another open of the Den is computing these rows right now"


def busy_status() -> tuple[str, str]:
    """(kind, why) §12 must not compute right now; ("", "") = free.
    kind: "rotation" (a scan holds the scheduler's rotation lock) or
    "memory" (the process is above the ceiling — the 09:20 trigger layer
    runs after the rotation lock is released, so this reading covers it).
    The third kind, "other_tab", is decided by section() (the render lock)."""
    try:
        from . import scheduler
        lock = getattr(scheduler, "_ROTATION_LOCK", None)
        if lock is not None and lock.locked():
            return "rotation", "a scan rotation is running on the box"
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
            return "memory", (f"the app is using {r:.0f} MiB of its 512 MiB "
                              f"(§12 computes below {ceiling:.0f})")
    return "", ""


def busy_reason() -> str:
    """busy_status()'s text only ("" = free to compute)."""
    return busy_status()[1]


def _row_key(ticker: str, overrides: Optional[dict], show_terms: bool) -> tuple:
    import json
    return (ticker, date.today().isoformat(),
            json.dumps(overrides or {}, sort_keys=True, default=str), bool(show_terms))


def _ttl_of(entry: tuple) -> float:
    return entry[2] if len(entry) > 2 and entry[2] else ROW_TTL_S


def _row_cache_get(key: tuple) -> Optional[dict]:
    import time as _time
    with _ROW_LOCK:
        hit = _ROW_CACHE.get(key)
        if hit is None or _time.monotonic() - hit[0] >= _ttl_of(hit):
            _ROW_CACHE.pop(key, None)
            return None
        return hit[1]


def _row_cache_put(key: tuple, rec: dict, ttl: Optional[float] = None) -> None:
    """Keep a computed row ROW_TTL_S (or `ttl` — DEGRADED_ROW_TTL_S for a
    row priced from the repo copy because the database did not answer)."""
    import time as _time
    now = _time.monotonic()
    with _ROW_LOCK:
        for k in [k for k, v in _ROW_CACHE.items() if now - v[0] >= _ttl_of(v)]:
            _ROW_CACHE.pop(k, None)
        while len(_ROW_CACHE) >= ROW_CACHE_MAX:
            oldest = min(_ROW_CACHE, key=lambda k: _ROW_CACHE[k][0])
            _ROW_CACHE.pop(oldest, None)
        _ROW_CACHE[key] = (now, rec, ttl)


def clear_row_cache() -> None:
    with _ROW_LOCK:
        _ROW_CACHE.clear()


def not_computed_record(ticker: str, why: str) -> dict:
    """failed_record's shape, flagged `not_computed` — a row §12 chose not
    to compute on this render (time budget or a busy box), never a crash."""
    from trading.valuation import pipeline
    rec = pipeline.failed_record(ticker, why)
    rec["not_computed"] = True
    rec["row_kind"] = "not_computed"
    return rec


def crashed_record(ticker: str, error: str) -> dict:
    """failed_record's shape, flagged `failed` — the chain RAISED for this
    name on this open. Printed as one FAILED line (never as an UNDECLARED
    route with an INSUFFICIENT verdict) and counted on the "This open" line;
    not kept, so the next open tries again."""
    from trading.valuation import pipeline
    rec = pipeline.failed_record(ticker, error)
    rec["failed"] = True
    rec["row_kind"] = "failed"
    return rec


def empty_section(error: str = "", typed: str = "") -> dict:
    from trading.valuation import pipeline
    return {"rows": [], "n": 0, "max_rows": MAX_ROWS, "typed": typed,
            "typed_full": vv.normalise_symbol(typed) if typed else "",
            "show_terms": False, "error": error,
            "n_statements": pipeline.statement_coverage(),
            "assumed": pipeline.assumed_for_page(),
            "today": date.today().isoformat(),
            "drafted": False, "n_edited": 0, "drafted_keys": [], "no_effect_keys": [],
            "budget_s": time_budget_s(), "elapsed_s": 0.0,
            "computed": 0, "cached": 0, "skipped_budget": 0,
            "skipped_busy": 0, "busy": "", "busy_kind": "", "failed": 0,
            "n_cut": 0, "cut_names": [], "store_down": "", "store_wait_s": 0.0,
            "degraded_ttl_min": int(DEGRADED_ROW_TTL_S // 60),
            "row_ttl_min": int(ROW_TTL_S // 60)}


def section(cfg, p, typed: str = "", show_terms: bool = False) -> dict:
    """§12 for one page render. Every store call made while building it
    goes through ONE render-scoped database gate (see _StoreGate); the time
    budget's clock starts here, so it includes reading the draft, the
    targets and the rows' prices."""
    import time as _time
    t0 = _time.monotonic()
    ctx = _Render(time_budget_s(), t0)
    prev = getattr(_RENDER, "ctx", None)
    _RENDER.ctx = ctx
    try:
        return _section(cfg, p, typed, show_terms, ctx)
    finally:
        _RENDER.ctx = prev


def _section(cfg, p, typed: str, show_terms: bool, ctx: "_Render") -> dict:
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
    # VAL-11: only the drafted numbers that change a result switch the
    # banner on and count; the no-effect ones are listed apart
    eff = effective_draft_keys(overrides)
    out.update({"drafted": bool(eff), "n_edited": len(eff), "drafted_keys": eff,
                "no_effect_keys": sorted(k for k in overrides if k not in eff)})
    names = names_to_show(p, typed_full)
    cut = list(getattr(names, "cut", None) or [])
    out.update({"n_cut": len(cut), "cut_names": cut})
    todo = [t for t in names
            if _row_cache_get(_row_key(t, overrides, out["show_terms"] and t == typed_full)) is None]
    owns = _SECTION_LOCK.acquire(blocking=False) if todo else False
    if owns:
        busy_kind, busy = busy_status()
    elif todo:
        busy_kind, busy = "other_tab", OTHER_TAB_TEXT
    else:
        busy_kind, busy = "", ""
    try:
        pre = _prefetch_closes(todo) if (todo and not busy) else {}
        res = _section_rows(cfg, p, names, typed_full, out["show_terms"], overrides,
                            ctx, busy_kind, busy, pre)
    finally:
        if owns:
            _SECTION_LOCK.release()
    if res["computed"]:
        try:
            from .memory_relief import release
            release("den chain")
        except Exception:                               # noqa: BLE001
            pass
    g = ctx.gate
    out.update({"rows": res["rows"], "n": len(res["rows"]),
                "busy": res["busy"], "busy_kind": res["busy_kind"],
                "elapsed_s": round(_time.monotonic() - ctx.t0, 1),
                "computed": res["computed"], "cached": res["cached"],
                "failed": res["failed"],
                "skipped_budget": res["skipped_budget"],
                "skipped_busy": res["skipped_busy"],
                "store_down": g.why if g.down else "",
                "store_wait_s": round(g.spent_s, 1)})
    return out


def _budget_row_text(budget: float, gate: "_StoreGate") -> str:
    keep = f"computed rows are kept {int(ROW_TTL_S // 60)} min"
    if gate.down:
        return (f"not computed — the database did not answer in time on this open "
                f"({gate.why}) and the §12 time budget ({budget:.0f} s) ran out; "
                f"open the Den again ({keep}) or type this name in the box")
    return (f"not computed — the §12 time budget ({budget:.0f} s, which includes "
            f"loading the rows' prices) ran out before this row; open the Den "
            f"again ({keep}) or type this name in the box")


def _section_rows(cfg, p, names, typed_full, show_terms, overrides, ctx,
                  busy_kind, busy, pre) -> dict:
    """The row loop. The busy reading is re-taken before every NEW row, so a
    scan rotation that starts mid-render stops it. A name the prefetch did
    not find in the store is read from the repo zip here (not asked again)."""
    rows = []
    computed = cached = skipped_budget = skipped_busy = failed = 0
    for t in names:
        terms = show_terms and t == typed_full
        key = _row_key(t, overrides, terms)
        hit = _row_cache_get(key)
        if hit is not None:
            rows.append(_marked(hit))
            cached += 1
            continue
        if not busy and computed:
            busy_kind, busy = busy_status()
        if busy:
            rows.append(not_computed_record(
                t, f"not computed — {busy}; {BUSY_REMEDY.get(busy_kind) or BUSY_REMEDY['rotation']}"))
            skipped_busy += 1
            continue
        if ctx.budget_spent():
            rows.append(not_computed_record(t, _budget_row_text(ctx.budget, ctx.gate)))
            skipped_budget += 1
            continue
        if t in pre:
            closes, src = pre[t], "store"
        else:
            closes = _zip_close_series(t)
            src = "repo copy"
        try:
            rec = row_for(t, cfg, p, show_terms=terms, overrides=overrides or None,
                          closes=closes, price_source=src)
        except _BudgetSpent:
            rows.append(not_computed_record(t, _budget_row_text(ctx.budget, ctx.gate)))
            skipped_budget += 1
            continue
        except Exception as e:                          # noqa: BLE001
            log.exception("valuation chain: row failed for %s", t)
            rows.append(crashed_record(t, f"{type(e).__name__}: {e}"[:160]))
            failed += 1
            continue
        _row_cache_put(key, _marked(rec), DEGRADED_ROW_TTL_S if ctx.gate.down else None)
        computed += 1
        rows.append(rec)
    return {"rows": rows, "computed": computed, "cached": cached,
            "skipped_budget": skipped_budget, "skipped_busy": skipped_busy,
            "failed": failed, "busy": busy, "busy_kind": busy_kind}


# ------------------------------------------------------ §13 formulas page

FAMILY_RULES = [
    ("earnings", "value per share = scenario EPS × band [min(peer P/E, own low), mean(peer, own median), max(peer, own high)]; "
                 "no own history → the three cases' own closing P/E"),
    ("book", "justified P/B = (ROE − g) / (COE − g), COE = rf + β × ERP; value = P/B × BVPS; "
             "downside / upside = ROE ∓ step; g = min(long-run g, ROE × (1 − payout))"),
    ("ev_ebitda", "EV = multiple × EBITDA per share; equity per share = EV − net debt − minorities; "
                  "the BASE case uses mid-cycle EBITDA per share (the mean of every stored FY, when 3 or more "
                  "exist — any EV/EBITDA route, not metals only); downside / upside keep their own case EBITDA"),
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
                    "= base discount + steps per gauge ON — §11 above; a target dated today has nothing to adjust "
                    "yet (no context target printed); a missing 10-year yield is not applied — the context target "
                    "is the sector leg only, labelled — and with the sector leg missing too there is none"),
    ("18 verdict", "insufficient first, when a range value is missing or unordered, (high − low) / base exceeds the "
                   "width cap or the stated uncertainty its cap; then attractive when price ≤ base × (1 − margin); "
                   "unattractive when price is above the range high, or above base × (1 + premium) with no thesis "
                   "recorded; otherwise watch — bands in Part A; never a trade permission"),
]


def empty_formulas_section(flag: str = "", error: str = "") -> dict:
    return {"flag": flag, "error": error, "groups": [], "rows": [], "n_editable": 0,
            "n_edited": 0, "n_stored": 0, "no_effect_keys": [], "n_no_effect_rows": 0,
            "terms": [], "routes": [], "chain_cells": [],
            "family_rules": FAMILY_RULES, "stage_rules": STAGE_RULES}


def formulas_section(flag: Optional[str] = None, error: str = "") -> dict:
    """Den §13: the editable assumption rows (with the stored draft) and
    every formula registry the chain runs. Stored inputs only."""
    from trading.valuation import assumptions as ASM
    from trading.valuation import route as R
    from trading.valuation import scenarios as SC
    from trading.valuation import terms as T
    values = load_assumptions()
    eff = effective_draft_keys(values)
    rows = []
    for r in ASM.describe(values):
        r = dict(r)
        # VAL-11: editable, but changes no result yet — labelled, not counted
        r["no_effect"] = r.get("no_effect") or no_effect_reason(r["key"])
        rows.append(r)
    terms = [{"name": name, "formula": e.get("formula") or "", "stage": e.get("stage") or ""}
             for name, e in T.TERMS.items()]
    cells = SC.chain_formulas()
    cols = Counter(re.match(r"[A-Z]*", str(c.get("cell") or "")).group(0) or "no cell"
                   for c in cells)
    chain_cols = " · ".join(f"{n} in column {k}" if k != "no cell" else f"{n} without a cell"
                            for k, n in sorted(cols.items(), key=lambda kv: -kv[1]))
    routes = []
    for r in R.SECTOR_METHODS:
        routes.append({"sector": r.get("sector") or r.get("label") or "",
                       "method": r.get("method") or "", "family": r.get("family") or "",
                       "basis": r.get("primary_basis") or "",
                       "per_share": r.get("per_share_metric") or "",
                       "rule": r.get("key_drivers") or "",
                       "bad_time": r.get("bad_time_behaviour") or ""})
    return {"flag": flag or "", "error": error, "groups": ASM.GROUPS, "rows": rows,
            "n_editable": len(rows), "n_edited": len(eff), "n_stored": len(values),
            "no_effect_keys": sorted(k for k in values if k not in eff),
            "n_no_effect_rows": sum(1 for r in rows if r.get("no_effect")),
            "terms": terms, "routes": routes, "chain_cells": cells,
            "chain_cols": chain_cols,
            "family_rules": FAMILY_RULES, "stage_rules": STAGE_RULES}
