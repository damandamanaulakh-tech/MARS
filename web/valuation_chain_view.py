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

BATCH 2 (owner choices 2026-10-21): the macro inputs read the stored feeds
(trading.macro_feeds — the Upstox 10-year G-sec yield, the RBI repo rate
and USD/INR, FII + DII holding per stock), each dated and marked STALE when
old; the owner's inputs table (settings doc `valuation_inputs`: typed NAV,
embedded value, holding discount, segment multiples, peer list) is passed
to the chain; the priced peer table is handed in so a typed peer list and
the sum-of-parts segment multiples can read it; known-fault lines whose fix
landed are gone; the "no effect yet" labels follow what each render feeds.
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
#: 3 = 2026-10-21: + short-term investments, minorities, EBIT and the lender
#: book (VAL-12a / VAL-01 / VAL-02). The pipeline's own number wins when higher.
#: 4 = 2026-10-21 (VALFIX batch 3): + PAT, route sector / family and the last
#: four FYs (the §4 sector totals read them).
PEER_BASE_VERSION = 4


def _peer_base_version() -> int:
    try:
        from trading.valuation import pipeline
        return max(PEER_BASE_VERSION, int(getattr(pipeline, "PEER_BASE_VERSION", 0) or 0))
    except Exception:                                   # noqa: BLE001
        return PEER_BASE_VERSION
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
        self.targets: Optional[dict] = None   # §11 stored targets, read once per render

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


def _index_end(end_date: Optional[str]) -> Optional[str]:
    """The selection date when it is before the newest stored Nifty close
    (else None = read to the newest close, as before)."""
    if not end_date:
        return None
    s = _index_series("nifty_close")
    if s is None or not len(s.dropna()):
        return None
    last = s.dropna().index[-1].date().isoformat()
    return str(end_date)[:10] if str(end_date)[:10] < last else None


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


# The stock's own P/E history is the pipeline's (pipeline.own_pe_rows, VAL-14a,
# owner choice 2026-10-21: FY-end close ÷ normalised PAT ÷ that FY's shares,
# odd years flagged); the old basic-EPS reader that lived here is gone.


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


def _target_entries() -> dict:
    """The §11 stored targets {ticker: {target, asof, basis, ts}}, read ONCE
    per render through its gate; once it is closed, the in-process copy
    valuation_view keeps."""
    r = _render()
    if r is not None and r.targets is not None:
        return r.targets
    g = _gate()
    doc = None
    if not g.down:
        doc = g.call("§11 targets read", vv.load_doc, default=None)
    if doc is None:
        doc = {"targets": dict((getattr(vv, "LAST", {}) or {}).get("targets") or {})}
    out = {str(k): dict(v) for k, v in (doc.get("targets") or {}).items() if isinstance(v, dict)}
    if r is not None:
        r.targets = out
    return out


def _target_names() -> list[str]:
    """The §11 stored targets' tickers."""
    return list(_target_entries().keys())


def pushed_entry(ticker: str) -> dict:
    """{date, basis} of this stock's §11 stored target ({} = none) — the
    "pushed" tier of the selection date (VALFIX batch 3)."""
    e = _target_entries().get(str(ticker or "")) or {}
    return {"date": str(e.get("asof") or "")[:10], "basis": str(e.get("basis") or "")} \
        if e.get("asof") else {}


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


# ------------------------------------------------- owner inputs table

#: VAL-01 / VAL-14b (owner choice 2026-10-21): the numbers the chain cannot
#: read from any file — typed NAV, embedded value, holding discount, segment
#: multiples and a peer list per stock. Settings doc + in-process copy (the
#: assumption-draft pattern). Display only: the engine never reads it.
INPUTS_DOC_ID = "valuation_inputs"
MAX_INPUT_SYMBOLS = 40
MAX_INPUT_PEERS = 20
MAX_INPUT_SEGMENTS = 12
LAST_INPUTS: dict = {}
INPUT_NUMBER_FIELDS = ("nav_ps", "ev_ps", "vnb_ps")
INPUT_DATE_FIELDS = ("nav_asof", "ev_asof", "selection_date")
#: VALFIX batch 3 (owner 2026-10-21): the company's revenue share billed in
#: USD / EUR (tier (a) of the currency leg; negative = an importer).
INPUT_SHARE_FIELDS = ("usd_share_pct", "eur_share_pct")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9&/.,() \-]{1,40}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _bare_symbol(t: Any) -> str:
    s = str(t or "").strip().upper()
    return s[:-3] if s.endswith((".NS", ".BO")) else s


READ_FAILED = ("the stored inputs table could not be read (database) — nothing changed; "
               "try again when the store answers")


def _read_inputs() -> tuple[dict, bool]:
    """({SYMBOL: row}, ok) straight from the settings doc. ok is False when
    the read did not happen or failed (this render's store gate was, or
    became, down) — reviewer defect 5 (2026-10-21): a failed read used to
    come back as an EMPTY table, and the save that followed wrote that
    emptiness over every stored row."""
    from . import db
    g = _gate()
    if g.down:
        return {}, False
    doc = g.call("inputs table read", db.load_settings, doc_id=INPUTS_DOC_ID, default=None)
    if g.down or doc is None:
        return {}, False
    table = dict(doc.get("inputs") or {}) if isinstance(doc, dict) else {}
    return {str(k).upper(): dict(v) for k, v in table.items() if isinstance(v, dict)}, True


def load_inputs() -> dict:
    """{SYMBOL: row} of the inputs table — the Mongo doc first, the
    in-process copy when the doc cannot be read (display only; a SAVE never
    builds on this fallback, see save_input_row)."""
    table, ok = _read_inputs()
    if not ok:
        log.warning("valuation chain: inputs table unreadable — using the in-process copy")
    if not table and LAST_INPUTS:
        table = {str(k).upper(): dict(v) for k, v in LAST_INPUTS.items()
                 if isinstance(v, dict)}
    return table


def _store_inputs(table: dict) -> bool:
    from datetime import datetime
    from . import db
    LAST_INPUTS.clear()
    LAST_INPUTS.update(table)
    try:
        db.save_settings({"inputs": dict(table), "ts": datetime.utcnow().isoformat()},
                         doc_id=INPUTS_DOC_ID)
        return True
    except Exception:                                   # noqa: BLE001
        log.exception("valuation chain: inputs table save failed (in-memory copy kept)")
        return False


def save_input_row(symbol: str, row: dict) -> tuple[bool, str]:
    """Add or replace ONE symbol's row. (ok, error). Refused — nothing
    written, nothing changed — when the stored table could not be read."""
    from datetime import datetime
    sym = _bare_symbol(symbol)
    table, ok = _read_inputs()
    if not ok:
        return False, READ_FAILED
    if sym not in table and len(table) >= MAX_INPUT_SYMBOLS:
        return False, f"the inputs table holds at most {MAX_INPUT_SYMBOLS} symbols — remove one first"
    table[sym] = {**row, "ts": datetime.utcnow().isoformat(timespec="seconds")}
    _store_inputs(table)
    clear_row_cache()
    return True, ""


def remove_input_row(symbol: str) -> bool:
    """Drop one symbol's row; False when it is not there OR the stored
    table could not be read (a failed read must never write)."""
    sym = _bare_symbol(symbol)
    table, ok = _read_inputs()
    if not ok or sym not in table:
        return False
    table.pop(sym, None)
    _store_inputs(table)
    clear_row_cache()
    return True


def _pos_num(raw: Any, name: str, errors: list, *, lo: float = 0.0, hi: float = 1e7,
             allow_zero: bool = False) -> Optional[float]:
    import math
    s = str(raw or "").strip().replace(",", "")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        errors.append(f"{name}: '{s[:20]}' is not a number")
        return None
    if not math.isfinite(v) or v > hi or v < lo or (v == lo and not allow_zero):
        errors.append(f"{name}: {s[:20]} is outside {lo:g}–{hi:g}")
        return None
    return v


def parse_inputs_form(form) -> tuple[str, dict, list]:
    """Form → (SYMBOL, row, errors). Every field is optional, but at least
    one number, a peer list or segment multiples must be typed. A single bad
    field refuses the whole row (nothing stored)."""
    errors: list = []
    full = vv.normalise_symbol(str(form.get("ticker") or ""))
    sym = _bare_symbol(full)
    if not sym:
        return "", {}, ["the symbol is missing or not an NSE symbol"]
    row: dict = {}
    for k in INPUT_NUMBER_FIELDS:
        v = _pos_num(form.get(k), k, errors)
        if v is not None:
            row[k] = v
    d = _pos_num(form.get("holding_discount_pct"), "holding_discount_pct", errors,
                 lo=0.0, hi=95.0, allow_zero=True)
    if d is not None:
        row["holding_discount_pct"] = d
    from trading.valuation import currency as CUR
    lo_s, hi_s = CUR.SHARE_BOUNDS
    for k in INPUT_SHARE_FIELDS:
        v = _share_num(form.get(k), k, errors, lo_s, hi_s)
        if v is not None:
            row[k] = v
    if (row.get("usd_share_pct") or 0.0) + (row.get("eur_share_pct") or 0.0) > 100.0:
        errors.append("USD + EUR share above 100%")
    for k in INPUT_DATE_FIELDS:
        s = str(form.get(k) or "").strip()
        if not s:
            continue
        try:
            if not _ISO_RE.match(s):
                raise ValueError
            d = date.fromisoformat(s)
        except ValueError:
            errors.append(f"{k}: '{s[:20]}' is not a date (YYYY-MM-DD)")
            continue
        if k == "selection_date" and d > date.today():
            # the time of action cannot be in the future (§11's own rule)
            errors.append(f"selection date {s} is in the future")
            continue
        row[k] = s
    segs: dict = {}
    raw_segs = str(form.get("segment_multiples") or "").strip()
    for part in [x for x in re.split(r"[;\n]+", raw_segs) if x.strip()]:
        if "=" not in part:
            errors.append(f"segment multiples: '{part.strip()[:30]}' needs SEGMENT=multiple")
            continue
        name, val = (x.strip() for x in part.split("=", 1))
        if not _SEGMENT_RE.match(name):
            errors.append(f"segment multiples: '{name[:30]}' is not a segment name")
            continue
        v = _pos_num(val, f"segment multiple {name}", errors, hi=500.0)
        if v is not None:
            segs[name] = v
    if len(segs) > MAX_INPUT_SEGMENTS:
        errors.append(f"segment multiples: at most {MAX_INPUT_SEGMENTS}")
    if segs:
        row["segment_multiples"] = segs
    peers: list = []
    for raw_t in re.split(r"[,\s]+", str(form.get("peer_list") or "")):
        if not raw_t.strip():
            continue
        t = _bare_symbol(vv.normalise_symbol(raw_t))
        if not t:
            errors.append(f"peer list: '{raw_t.strip()[:20]}' is not an NSE symbol")
        elif t != sym and t not in peers:
            peers.append(t)
    if len(peers) > MAX_INPUT_PEERS:
        errors.append(f"peer list: at most {MAX_INPUT_PEERS} names")
    if peers:
        row["peer_list"] = peers
    note = str(form.get("note") or "").strip()
    if note:
        row["note"] = note[:200]
    if not errors and not (set(row) - {"note", "nav_asof", "ev_asof"}):
        errors.append("nothing typed — enter a NAV, embedded value, holding discount, "
                      "segment multiples, a peer list, a USD / EUR share or a selection "
                      "date (remove deletes a row)")
    return sym, row, errors


def _share_num(raw: Any, name: str, errors: list, lo: float, hi: float) -> Optional[float]:
    """A revenue share in % — 0 and negatives (an importer) are legitimate."""
    import math
    s = str(raw or "").strip().replace(",", "").rstrip("%")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        errors.append(f"{name}: '{s[:20]}' is not a number")
        return None
    if not math.isfinite(v) or v < lo or v > hi:
        errors.append(f"{name}: {s[:20]} is outside {lo:g}–{hi:g}")
        return None
    return v


def segments_on_file() -> list:
    """[{symbol, names}] — the stocks with segment rows in
    data/segment_ebit.csv and their segment names AS PRINTED, so the owner
    can type a segment multiple against the real name (reviewer defect 1)."""
    try:
        from trading.valuation import segments as SG
        return [{"symbol": sym, "names": [r["segment"] for r in SG.segment_ebit(sym)]}
                for sym in SG.symbols_with_segments()]
    except Exception:                                   # noqa: BLE001
        return []


def inputs_section(flag: str = "", error: str = "") -> dict:
    """The inputs table as the Den prints it (rows A-Z)."""
    table = load_inputs()
    rows = []
    for sym in sorted(table):
        r = table[sym]
        rows.append({"symbol": sym, **r,
                     "segments_text": "; ".join(f"{k}={v:g}" for k, v in
                                                (r.get("segment_multiples") or {}).items()),
                     "peers_text": " ".join(r.get("peer_list") or [])})
    return {"flag": flag or "", "error": error, "rows": rows, "n": len(rows),
            "max": MAX_INPUT_SYMBOLS, "doc_id": INPUTS_DOC_ID,
            "segments_on_file": segments_on_file()}


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
    # kept for the day: a holding company's subsidiaries are priced from it
    _PEER_CACHE["store_closes"] = dict(store)
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
            and doc.get("version") == _peer_base_version()):
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
                "version": _peer_base_version(),
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


def _ensure_peer_table() -> bool:
    """Build the priced peer table once per process per day (False = not
    built because this render's time budget was already spent)."""
    from trading.valuation import pipeline
    today = date.today().isoformat()
    if not _peer_table_stale(today):
        return True
    r = _render()
    if r is not None and r.budget_spent():
        # Review 2026-10-15: the budget was checked only before a row,
        # so the first row ran on into a ~9 s peer build. A build that
        # would start after the budget is spent is not started; row_for
        # sees the flag and prints the row NOT COMPUTED (an empty peer
        # table here would otherwise be cached as the row's answer).
        r.peer_build_skipped = True
        return False
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
            except Exception:                           # noqa: BLE001
                log.exception("valuation chain: peer table failed")
            _PEER_CACHE["by_method"] = by_method
            _PEER_CACHE["day"] = today
            # Priced from the repo copy because THIS render's database
            # gate closed: rebuilt by a later open whose database answers.
            _PEER_CACHE["degraded"] = _gate().down
    return True


def peer_table_rows() -> dict:
    """The priced peer table {method: [rows]} — handed to the chain so a
    typed peer list and the sum-of-parts segment multiples can read any
    statement name's multiples (VAL-01). {} when it could not be built."""
    if not _ensure_peer_table():
        return {}
    return dict(_PEER_CACHE.get("by_method") or {})


#: At most this many subsidiaries of one holding company are read from the
#: repo zip when the store has no close for them.
SUBSIDIARY_ZIP_MAX = 12


def subsidiary_price(ticker: str) -> Optional[float]:
    """Today's close of a holding company's subsidiary (VAL-14b), from the
    SAME place the peers are priced: the day's one-query store closes, the
    priced peer table, then the repo zip."""
    t = str(ticker or "").upper()
    if not t:
        return None
    if not t.endswith((".NS", ".BO")):
        t += ".NS"
    px = (_PEER_CACHE.get("store_closes") or {}).get(t)
    if px:
        return float(px)
    for rows in (_PEER_CACHE.get("by_method") or {}).values():
        for r in rows:
            if r.get("ticker") == t and V.num(r.get("price")):
                return float(r["price"])
    seen = _PEER_CACHE.setdefault("zip_subsidiaries", {})
    if t in seen:
        return seen[t]
    if len(seen) >= SUBSIDIARY_ZIP_MAX:
        return None
    s = _zip_close_series(t)
    seen[t] = float(s.iloc[-1]) if s is not None and len(s) else None
    return seen[t]


def peer_multiples(method: str, exclude: str = "") -> dict:
    """Median trailing multiples (P/E, P/B, EV/EBITDA, EV/Sales) of the
    names on the same route among the Nifty-200 statement set. ASSUMED
    peer definition — the owner has not supplied peer groups. Priced once
    per process per day from ONE store query; the price-free base comes
    from peer_base (stored, rebuilt weekly)."""
    if not _ensure_peer_table():
        return {"n": 0, "pe_median": None, "pb_median": None,
                "ev_ebitda_median": None, "ev_sales_median": None,
                "source": "peer table not built — the §12 time budget was spent"}
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

#: Item 10 (owner choices 2026-10-21): a stored daily feed older than this
#: prints STALE beside its value (same rule as the USD-INR row, VAL-13a).
FEED_STALE_DAYS = 10
#: Feed readings are kept this long per process (they move once a day).
FEED_TTL_S = 600.0
_FEED_CACHE: dict = {}


def _macro_feeds():
    """trading.macro_feeds (builder D) or None while it is absent."""
    try:
        from trading import macro_feeds as MF
        return MF
    except Exception:                                   # noqa: BLE001
        return None


def _reading(x: Any) -> Optional[tuple]:
    """A feed answer → (value, date_iso, source) or None. Accepts the
    contract's tuple, a {value, date, source} dict or a bare number."""
    if x is None:
        return None
    if isinstance(x, dict):
        v = V.num(x.get("value"))
        return (v, str(x.get("date") or "")[:10], str(x.get("source") or "")) if v is not None else None
    if isinstance(x, (tuple, list)):
        v = V.num(x[0]) if x else None
        if v is None:
            return None
        d = str(x[1])[:10] if len(x) > 1 and x[1] else ""
        s = str(x[2]) if len(x) > 2 and x[2] else ""
        return v, d, s
    v = V.num(x)
    return (v, "", "") if v is not None else None


def _feed(fn_name: str, *args, store: bool = True) -> Any:
    """One trading.macro_feeds call, kept FEED_TTL_S per process. `store`
    readings go through this render's database gate (the module's reader
    asks Mongo); a call made while the gate is closed is skipped and not
    kept, so a later open asks again."""
    import time as _time
    mf = _macro_feeds()
    fn = getattr(mf, fn_name, None) if mf is not None else None
    if fn is None:
        return None
    key = (fn_name,) + tuple(str(a) for a in args)
    now = _time.monotonic()
    hit = _FEED_CACHE.get(key)
    if hit is not None and now - hit[0] < FEED_TTL_S:
        return hit[1]
    if store:
        g = _gate()
        if g.down:
            return None
        out = g.call(f"macro feed read ({fn_name} {args[0] if args else ''})", fn, *args,
                     default=None)
        if g.down:
            return out
    else:
        try:
            out = fn(*args)
        except Exception:                               # noqa: BLE001
            log.warning("valuation chain: macro feed %s failed", fn_name)
            return None
    if len(_FEED_CACHE) > 256:
        _FEED_CACHE.clear()
    _FEED_CACHE[key] = (now, out)
    return out


def clear_feed_cache() -> None:
    _FEED_CACHE.clear()


def _stale_days() -> int:
    """macro_feeds.STALE_DAYS when the module is there (one rule), else ours."""
    mf = _macro_feeds()
    try:
        return int(getattr(mf, "STALE_DAYS", FEED_STALE_DAYS)) if mf is not None else FEED_STALE_DAYS
    except (TypeError, ValueError):
        return FEED_STALE_DAYS


def _age_days(d: Any, today: Optional[date] = None) -> Optional[int]:
    try:
        return ((today or date.today()) - date.fromisoformat(str(d)[:10])).days
    except (TypeError, ValueError):
        return None


_BOND_NAME_RE = re.compile(r"(\d+(?:\.\d+)?%\s*GS\s*\d{4})")


def _bond_name(row: Any, source: str = "") -> str:
    """The G-sec's name from a stored row's `bond` field, else from its
    source text ("… · 7.54% GS 2036 (IN…) close …")."""
    if isinstance(row, dict) and str(row.get("bond") or "").strip():
        return str(row["bond"]).strip()
    m = _BOND_NAME_RE.search(str(source or ""))
    return m.group(1) if m else ""


def _rbi_benchmark() -> dict:
    """RBI's own 10-year benchmark yield from the home page's Government
    Securities Market box (series rbi_gsec10y, stored with the RBI leg) —
    printed BESIDE the Upstox yield, never used: the owner chose Upstox as
    the source (lead's note (a), 2026-10-21)."""
    got = _reading(_feed("latest", "rbi_gsec10y"))
    if got is None:
        return {"rbi_benchmark_pct": None, "rbi_benchmark_date": None,
                "rbi_benchmark_bond": ""}
    v, d, src = got
    return {"rbi_benchmark_pct": v, "rbi_benchmark_date": d or None,
            "rbi_benchmark_bond": _bond_name(_feed("latest_row", "rbi_gsec10y"), src)}


def _rf_inputs(today: Optional[date] = None) -> dict:
    """The 10-year yield (owner chose Upstox only): the latest stored
    Upstox G-sec yield, dated, STALE past FEED_STALE_DAYS; none ever stored
    → the committed June file, labelled stale. `rf_bond` names the paper
    the yield was read from (reviewer defect 8); the RBI benchmark rides
    beside it as display only."""
    got = _reading(_feed("latest", "gsec10y_yield"))
    if got is not None:
        v, d, src = got
        age = _age_days(d, today)
        return {"rf_pct": v, "rf_date": d or None,
                "rf_source": src or "Upstox 10-year G-sec (stored daily)",
                "rf_bond": _bond_name(_feed("latest_row", "gsec10y_yield"), src),
                "rf_stale": bool(age is None or age > _stale_days()), "rf_age_days": age,
                "rf_feed": True, **_rbi_benchmark()}
    bond = vv._bond_series()
    if bond is not None and len(bond.dropna()):
        b = bond.dropna()
        d = b.index[-1].date().isoformat()
        return {"rf_pct": float(b.iloc[-1]), "rf_date": d,
                "rf_source": "June file (stale) — the committed 10-year yield file; "
                             "no Upstox G-sec yield stored yet",
                "rf_bond": "", "rf_stale": True, "rf_age_days": _age_days(d, today),
                "rf_feed": False, **_rbi_benchmark()}
    return {"rf_pct": None, "rf_date": None, "rf_source": "", "rf_bond": "",
            "rf_stale": False, "rf_age_days": None, "rf_feed": False, **_rbi_benchmark()}


def _repo_inputs(period_end: Any, today: Optional[date] = None) -> dict:
    """RBI repo rate at the base period end (start) and now (end). The
    start reads only RBI-verified history; unverifiable = DATA_NEEDED with
    the reason, and the bridge row counts 0 (item 10)."""
    out: dict = {"repo_start_pct": None, "repo_start_date": None, "repo_end_pct": None,
                 "repo_end_date": None, "repo_source": "", "repo_end_stale": False}
    if _macro_feeds() is None:
        out["repo_start_why"] = "the RBI repo feed is not wired in this build"
        return out
    end = _reading(_feed("latest", "rbi_repo"))
    if end is not None:
        out["repo_end_pct"], out["repo_end_date"], out["repo_source"] = end
        age = _age_days(end[1], today)
        out["repo_end_stale"] = bool(age is None or age > _stale_days())
    start = _reading(_feed("at", "rbi_repo", str(period_end)[:10])) if period_end else None
    if start is not None:
        out["repo_start_pct"], out["repo_start_date"] = start[0], start[1] or str(period_end)[:10]
    elif period_end:
        out["repo_start_why"] = (f"no RBI-verified repo rate on file for {str(period_end)[:10]} "
                                 "(data/rbi_repo_history.csv holds only rates read from RBI pages)")
    if end is None:
        out["repo_why"] = "no RBI repo rate stored yet (the RBI home page is read daily)"
    return out


def _usdinr_now(today: Optional[date] = None) -> tuple:
    """(rate, date, source) for USD/INR now: the latest RBI reference rate
    stored, else the committed usdinr_daily.csv's last row."""
    from trading.valuation import snapshot as S
    got = _reading(_feed("latest", "rbi_usdinr"))
    if got is not None:
        return got[0], got[1] or None, got[2] or "RBI reference rate (stored daily)"
    fx = S.usdinr_at((today or date.today()).isoformat())
    if fx:
        return fx[0], fx[1], "the committed USD-INR file (nothing refreshes it)"
    return None, None, ""


def _inst_inputs(ticker: str, period_end: Any) -> dict:
    """FII + DII holding % at the quarter at/just before the base period end
    (start) and the latest quarter (end), per stock, dated."""
    sym = str(ticker or "").upper().replace(".NS", "").replace(".BO", "")
    out: dict = {"inst_holding_start": None, "inst_holding_end": None,
                 "inst_holding_start_date": None, "inst_holding_end_date": None}
    if _macro_feeds() is None or not period_end:
        out["inst_holding_why"] = ("the shareholding feed is not wired in this build"
                                   if period_end else "no base period end")
        return out
    got = _feed("inst_holding", sym, str(period_end)[:10], store=False)
    if isinstance(got, (tuple, list)) and len(got) >= 2 and V.num(got[0]) is not None \
            and V.num(got[1]) is not None:
        out["inst_holding_start"], out["inst_holding_end"] = float(got[0]), float(got[1])
        out["inst_holding_start_date"] = str(got[2])[:10] if len(got) > 2 and got[2] else None
        out["inst_holding_end_date"] = str(got[3])[:10] if len(got) > 3 and got[3] else None
    else:
        out["inst_holding_why"] = (f"no FII + DII quarter on file for {sym} at/before "
                                   f"{str(period_end)[:10]} and a later one "
                                   "(data/shareholding_institutional.csv)")
    return out


def _macro_inputs(snap: dict, closes: Optional[pd.Series] = None,
                  ticker: str = "", end_date: Optional[str] = None) -> dict:
    """The owner's P/E-bridge market inputs from STORED state: Sensex /
    Nifty levels at the period end and now, VIX, the 10-year yield (rf,
    the Upstox G-sec feed), the stock's measured β on Nifty 50, the RBI
    repo rate at the period end and now, USD/INR now (RBI feed) and at the
    period end (the committed file), FII + DII holding then and now. CPI /
    GDP / Brent are not fed (owner chose none) — the pipeline counts them 0
    and lists them DATA_NEEDED. Each fed value carries its date.

    `end_date` (VALFIX batch 3) = the selection date: the Sensex / Nifty
    levels are read to it, not to today, so the own target is the valuation
    as of the time of action and the market since then is counted once —
    in the market-adjusted leg (owner: "split legs, no double count")."""
    from trading.valuation import snapshot as S
    period_end = snap.get("period_end")
    end = _index_end(end_date)
    nifty = index_move("nifty_close", period_end, end)
    sensex = index_move("sensex_close", period_end, end)
    vix = _index_series("india_vix_close")
    out: dict = {
        "sensex_start": sensex.get("start"), "sensex_end": sensex.get("end"),
        "nifty_start": nifty.get("start"), "nifty_end": nifty.get("end"),
        "index_end_date": nifty.get("end_date"),
        "index_end_is_selection": end is not None,
        "vix": float(vix.dropna().iloc[-1]) if vix is not None and len(vix.dropna()) else None,
        "beta": None, "usdinr_now": None, "usdinr_base": None,
        "price_now": price_now(closes)[0] if closes is not None else None,
        "period_end": period_end,
    }
    try:
        out.update(_rf_inputs())
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: 10-year yield read failed")
        out.setdefault("rf_pct", None)
    try:
        out.update(_repo_inputs(period_end))
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: repo read failed")
    try:
        out.update(_inst_inputs(ticker, period_end))
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: institutional holding read failed")
    out["usdinr_now_date"] = out["usdinr_base_date"] = None
    out["usdinr_now_source"] = ""
    try:
        now_v, now_d, now_src = _usdinr_now()
        fx_base = S.usdinr_at(period_end) if period_end else None
        out["usdinr_now"] = now_v
        out["usdinr_base"] = fx_base[0] if fx_base else None
        # the dates the two rates were read on (VAL-13a / VAL-15)
        out["usdinr_now_date"] = now_d
        out["usdinr_now_source"] = now_src
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


def _evaluate_kwargs() -> Any:
    """The keyword arguments pipeline.evaluate accepts (builders work in
    parallel: a seam not there yet is simply not passed). A `**kwargs`
    parameter accepts everything."""
    import inspect
    from trading.valuation import pipeline
    try:
        params = inspect.signature(pipeline.evaluate).parameters
    except (TypeError, ValueError):
        return set()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return _ALL
    return set(params)


class _AllNames:
    def __contains__(self, _name: Any) -> bool:
        return True


_ALL = _AllNames()


def row_for(ticker: str, cfg, p, *, show_terms: bool = False,
            overrides: Optional[dict] = None,
            closes: Any = _UNSET, price_source: str = "",
            owner_inputs: Any = _UNSET) -> dict:
    """One chain record. `closes` handed in (a series, or None = the store
    and the repo zip both lack the name) is used as is; not handed in, it
    is read here. `price_source` ("store" / "repo copy") is printed beside
    the price. `owner_inputs` = the Den inputs table {SYMBOL: row}; not
    handed in, it is read here. The own P/E history is the pipeline's own
    (own_pe_rows, VAL-14a) — this module no longer passes one."""
    from trading.valuation import pipeline
    if closes is _UNSET:
        closes = _closes(ticker)
    if owner_inputs is _UNSET:
        owner_inputs = load_inputs()
    px, px_date = price_now(closes)
    mac_box: dict = {}
    from trading.valuation import market_legs as ML
    owner_row = _owner_row_of(owner_inputs if isinstance(owner_inputs, dict) else {}, ticker)
    pushed = pushed_entry(ticker)

    def _macro(snap):
        # VALFIX batch 3: the selection date (typed → §11 → publication) is
        # read here, once, and the bridge's market rows end at it
        sel = ML.selection_date(snap, owner_row.get("selection_date"), pushed=pushed)
        mac_box["sel"] = sel
        m = _macro_inputs(snap, closes, ticker, end_date=ML.bridge_end(sel))
        mac_box["m"] = m
        return m

    kwargs = dict(
        price=px, price_date=px_date,
        sector_map=dict(getattr(cfg, "sector_map", {}) or {}),
        company_name=_company_names().get(ticker, ""),
        price_at=lambda when: price_at(closes, when),
        peers=peer_multiples,
        macro=_macro,
        overrides=overrides,
    )
    accepted = _evaluate_kwargs()
    extra = {"owner_inputs": owner_inputs or None, "peer_rows": peer_table_rows,
             "price_of": subsidiary_price}
    kwargs.update({k: v for k, v in extra.items() if k in accepted})
    rec = pipeline.evaluate(ticker, **kwargs)
    r = _render()
    if r is not None and r.peer_build_skipped:
        # computed without the peer table — never shown or kept as an answer
        r.peer_build_skipped = False
        raise _BudgetSpent("the peer table was not built")
    rec["key_terms"] = [t for t in rec.get("terms_rows", []) if t["name"] in KEY_TERMS]
    rec["all_terms"] = rec.get("terms_rows", []) if show_terms else []
    rec["n_terms"] = len(rec.get("terms_rows", []))
    rec["price_source"] = price_source if px is not None else ""
    # the inputs this row was fed, with their dates (the walk and the
    # row's inputs line print them)
    rec["macro_inputs"] = {k: v for k, v in (mac_box.get("m") or {}).items()
                           if isinstance(v, (int, float, str, bool)) or v is None}
    # VALFIX batch 3 (owner 2026-10-21): the IT-only rupee row E101 is counted
    # 0 in the live chain — the currency leg runs through EPS for every
    # sector inside the market block below (fx_block stays for the sheet's
    # own reading)
    rec["fx"] = {"applies": False, "e101_replaced": True}
    from . import valuation_market_view as mv
    try:
        rec["market"] = mv.market_block(ticker, rec, closes, cfg, p, mac_box.get("m") or {},
                                        overrides, owner_row, mv.sector_fx_for_render(),
                                        selection=mac_box.get("sel"), pushed=pushed)
    except Exception:                                   # noqa: BLE001
        log.exception("valuation chain: market legs failed for %s", ticker)
        rec["market"] = {"status": "DATA_NEEDED",
                         "why": "the market legs could not be read — see the log",
                         "selection": {}}
    try:
        idx = (rec.get("market") or {}).get("index") or vc.index_for(
            ticker, dict(getattr(cfg, "sector_map", {}) or {}))
        rec["sector_total"] = mv.sector_block(rec, idx)
    except Exception:                                   # noqa: BLE001
        log.exception("valuation chain: sector totals failed for %s", ticker)
        rec["sector_total"] = {"status": "DATA_NEEDED",
                               "why": "the sector totals could not be read — see the log",
                               "nse": {}}
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
    src = str(mac.get("usdinr_now_source") or "")
    return {"applies": True, "counted": counted,
            "now": now if counted else V.num(mac.get("usdinr_now")),
            "now_date": now_date, "age_days": age,
            # item 10: the RBI feed when stored, else the committed file
            "now_source": src, "now_from_feed": src.startswith("RBI"),
            "stale": bool(age is not None and age > FX_STALE_DAYS),
            "stale_days": FX_STALE_DAYS,
            "base": base if counted else V.num(mac.get("usdinr_base")),
            "base_date": base_date, "pct": pct if counted else None,
            "formula": formula}


# ------------------------------------------------------ known faults

#: VAL-01: groups whose businesses span sectors. Since 2026-10-21 a name
#: with segment rows on file (sum-of-parts) or a typed peer list carries no
#: warning; the others still do (owner order: keep VAL-01 for them).
CONGLOMERATES = ("RELIANCE", "ADANIENT", "BAJAJFINSV", "ITC", "GRASIM", "LT",
                 "M&M", "TATAMOTORS")


def known_faults(rec: dict) -> list:
    """Still-open flaws that bend THIS row's printed target (owner order
    2026-10-21: show a red '⚠ known fault' line). Every rule lives here, one
    block per flaw, so a flag is deleted by deleting its block when the fix
    lands. Facts come from the record's `range_flags` (pipeline); a record
    without them (built before that field existed) falls back to the route
    and says less rather than guess.

    Batch 2 (2026-10-21) removed the blocks whose fix landed: VAL-07 (the
    cases are never sorted now — an inverted row carries the red "cases
    inverted" tag instead), VAL-12a (one EV rule for the stock and its
    peers) and VAL-12b (power keeps the book method but its debt checks are
    back). VAL-02 stays only for a lender whose book could use neither the
    filed net worth nor the tangible book (an equity line CHECKED against a
    filing within 10% counts as checked — reviewer defect 9); VAL-01 only
    for a group with no sum-of-parts and no typed peer list."""
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

    # VAL-01 — a conglomerate on one sector's peers (no segments, no typed peers)
    if sym in CONGLOMERATES and not fl.get("sum_of_parts") and not fl.get("peer_list") \
            and family != "sotp":
        sec = route.get("sector") or fl.get("sector") or "one route"
        out.append({"code": "VAL-01",
                    "text": f"a group with several businesses valued on the peers of one "
                            f"route ({sec}); its other businesses are not valued — no "
                            f"segment rows on file; a peer list typed in the Den inputs "
                            f"table replaces the route median"})

    # VAL-02 — a lender's book that is neither the filing nor the tangible book
    lender = fl.get("lender") if fl else method in ("bank_pb_roe", "nbfc_pb")
    if lender and family == "book" and fl.get("lender_book_source") not in (
            "filing", "tangible", "filing_checked"):
        basis = fl.get("lender_book_basis") or "the feed's equity line"
        out.append({"code": "VAL-02",
                    "text": f"book = {basis}: no filed net worth and no tangible-book line on "
                            f"file, so the equity line is unchecked; an inflated equity line "
                            f"(as after a merger) lowers the ROE and pulls this target down"})
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


def effective_draft_keys(values: Optional[dict], live: Optional[dict] = None) -> list:
    """The drafted keys that really change a §12 result (the banner, its
    count and the per-row tag read these; VAL-11). §12 passes no as-of
    date, so the publication lag never counts here. `live` = which inputs
    this render feeds (item 9): a sheet number whose input is not fed
    changes nothing."""
    from trading.valuation import assumptions as ASM
    fn = getattr(ASM, "effective_keys", None)
    if fn is not None:
        try:
            return list(fn(values, live=live))
        except TypeError:                               # an older assumptions module
            return list(fn(values))
    return sorted(k for k in (values or {}) if k not in _NO_EFFECT_FALLBACK)


def no_effect_reason(key: str, live: Optional[dict] = None) -> str:
    from trading.valuation import assumptions as ASM
    fn = getattr(ASM, "no_effect_reason", None)
    if fn is not None:
        try:
            return fn(key, live) or ""
        except TypeError:
            pass
    reasons = getattr(ASM, "NO_EFFECT_REASONS", None) or {}
    if key in reasons:
        return reasons[key]
    return "changes no result yet" if key in _NO_EFFECT_FALLBACK else ""


def _feeds_live() -> dict:
    """Which bridge inputs the app can feed at all right now, with no
    stock in hand (§13 on a render with no §12 row): the index store, the
    stored RBI repo rate with a verified start at the last March year end,
    the FII + DII file. Everything else stays at assumptions.DEFAULT_LIVE."""
    out: dict = {}
    try:
        s = _index_series("nifty_close")
        out["market"] = bool(s is not None and len(s.dropna()))
    except Exception:                                   # noqa: BLE001
        pass
    if _macro_feeds() is not None:
        today = date.today()
        fy = date(today.year if today.month > 3 else today.year - 1, 3, 31).isoformat()
        try:
            out["repo"] = bool(_reading(_feed("latest", "rbi_repo"))
                               and _reading(_feed("at", "rbi_repo", fy)))
        except Exception:                               # noqa: BLE001
            out["repo"] = False
        try:
            got = _feed("inst_holding", "RELIANCE", fy, store=False)
            out["inst_holding"] = bool(isinstance(got, (tuple, list)) and len(got) >= 2
                                       and V.num(got[0]) is not None)
        except Exception:                               # noqa: BLE001
            out["inst_holding"] = False
    return out


def page_live(rows: Optional[list] = None) -> dict:
    """The inputs fed on THIS render (item 9, owner 2026-10-21): an input
    counts as live when ANY computed §12 row fed it (each record carries
    its own `assumptions.live_inputs`); with no computed row, what the app
    can feed at all (_feeds_live). A no-effect label disappears once its
    input is live."""
    from trading.valuation import assumptions as ASM
    maps = [((r.get("assumptions") or {}).get("live_inputs") or {}) for r in (rows or [])
            if r.get("row_kind", row_kind(r)) == "full"]
    maps = [m for m in maps if m]
    base = dict(getattr(ASM, "DEFAULT_LIVE", {}) or {})
    if maps:
        out = {k: False for k in set(base) | {k for m in maps for k in m}}
        for m in maps:
            for k, v in m.items():
                out[k] = bool(out.get(k)) or bool(v)
        return out
    base.update(_feeds_live())
    return base


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


def _row_key(ticker: str, overrides: Optional[dict], show_terms: bool,
             owner_row: Optional[dict] = None) -> tuple:
    """Kept-row key: the ticker, the day, the draft, terms on/off and the
    stock's row of the inputs table (a saved input recomputes the row)."""
    import json
    return (ticker, date.today().isoformat(),
            json.dumps(overrides or {}, sort_keys=True, default=str), bool(show_terms),
            json.dumps(owner_row or {}, sort_keys=True, default=str))


def _owner_row_of(table: Optional[dict], ticker: str) -> dict:
    t = table or {}
    return dict(t.get(_bare_symbol(ticker)) or {})


def _key_row(table: Optional[dict], ticker: str) -> dict:
    """The kept-row key's input part: the stock's inputs-table row plus its
    §11 date (VALFIX batch 3 — a push or a typed §11 date moves the
    selection date, so it must recompute the row)."""
    row = _owner_row_of(table, ticker)
    pe = pushed_entry(ticker)
    if pe:
        row["__s11"] = pe.get("date")
    return row


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
    """Forget the kept rows and the kept feed readings (the web conftest
    calls this before every test; an inputs-table save calls it too)."""
    with _ROW_LOCK:
        _ROW_CACHE.clear()
    _FEED_CACHE.clear()


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
            "live": {}, "inputs": {"n": 0, "symbols": []},
            "budget_s": time_budget_s(), "elapsed_s": 0.0,
            "computed": 0, "cached": 0, "skipped_budget": 0,
            "skipped_busy": 0, "busy": "", "busy_kind": "", "failed": 0,
            "n_cut": 0, "cut_names": [], "store_down": "", "store_wait_s": 0.0,
            "degraded_ttl_min": int(DEGRADED_ROW_TTL_S // 60),
            "row_ttl_min": int(ROW_TTL_S // 60),
            "remapped": {"n": 0, "rows": []}}


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
    # VALFIX batch 3: every name the 2026-10-21 index mapping moved (listed,
    # collapsible, on §12)
    try:
        from . import valuation_market_view as mv
        out["remapped"] = mv.remapped_names(dict(getattr(cfg, "sector_map", {}) or {}))
    except Exception:                                   # noqa: BLE001
        log.warning("valuation chain: remap list failed")
    overrides = load_assumptions()
    inputs = load_inputs()
    names = names_to_show(p, typed_full)
    cut = list(getattr(names, "cut", None) or [])
    out.update({"n_cut": len(cut), "cut_names": cut})
    todo = [t for t in names
            if _row_cache_get(_row_key(t, overrides, out["show_terms"] and t == typed_full,
                                       _key_row(inputs, t))) is None]
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
                            ctx, busy_kind, busy, pre, inputs)
    finally:
        if owns:
            _SECTION_LOCK.release()
    if res["computed"]:
        try:
            from .memory_relief import release
            release("den chain")
        except Exception:                               # noqa: BLE001
            pass
    # VAL-11 / item 9: a drafted number counts when it changes ANY row shown
    # (each record says which of its drafted keys changed it, by what that
    # row was fed); with no computed row, by what the app feeds at all
    full = [r for r in res["rows"] if r.get("row_kind") == "full"]
    if full:
        eff = sorted({k for r in full
                      for k in ((r.get("assumptions") or {}).get("drafted_effective") or [])})
    else:
        eff = effective_draft_keys(overrides, live=page_live([]))
    out.update({"drafted": bool(eff), "n_edited": len(eff), "drafted_keys": eff,
                "no_effect_keys": sorted(k for k in overrides if k not in eff),
                "live": page_live(res["rows"]),
                "inputs": {"n": len(inputs), "symbols": sorted(inputs)}})
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


def walk_record(cfg, p, typed: str) -> dict:
    """The ONE stock the Den §1 walk shows (owner order 2026-10-21: "?walk=
    TCS"). Same guards as §12: a kept row is reused; a new one is computed
    only when no scan rotation holds the lock, the process is under the
    memory ceiling, no other open is computing, and inside the time budget.
    {typed, ticker, rec, status, why, elapsed_s, store_down}; status is
    "" (nothing typed) / error / cached / computed / busy / budget / failed."""
    import time as _time
    out: dict = {"typed": typed or "", "ticker": "", "rec": None, "status": "", "why": "",
                 "elapsed_s": 0.0, "store_down": ""}
    if not typed:
        return out
    full = vv.normalise_symbol(typed)
    if not full:
        out.update(status="error", why=f"'{typed}' is not a symbol")
        return out
    out["ticker"] = full
    ctx = _Render(time_budget_s(), _time.monotonic())
    prev = getattr(_RENDER, "ctx", None)
    _RENDER.ctx = ctx
    computed = False
    try:
        overrides = load_assumptions()
        inputs = load_inputs()
        key = _row_key(full, overrides, False, _key_row(inputs, full))
        hit = _row_cache_get(key)
        if hit is not None:
            out.update(rec=_marked(hit), status="cached")
            return out
        if not _SECTION_LOCK.acquire(blocking=False):
            out.update(status="busy", why=f"{OTHER_TAB_TEXT}; {BUSY_REMEDY['other_tab']}")
            return out
        try:
            kind, why = busy_status()
            if why:
                out.update(status="busy",
                           why=f"{why}; {BUSY_REMEDY.get(kind) or BUSY_REMEDY['rotation']}")
                return out
            pre = _prefetch_closes([full])
            if full in pre:
                closes, src = pre[full], "store"
            else:
                closes, src = _zip_close_series(full), "repo copy"
            try:
                rec = row_for(full, cfg, p, overrides=overrides or None, closes=closes,
                              price_source=src, owner_inputs=inputs)
            except _BudgetSpent:
                out.update(status="budget", why=_budget_row_text(ctx.budget, ctx.gate))
                return out
            except Exception as e:                      # noqa: BLE001
                log.exception("valuation chain: walk row failed for %s", full)
                out.update(status="failed", why=f"{type(e).__name__}: {e}"[:160])
                return out
            _row_cache_put(key, _marked(rec), DEGRADED_ROW_TTL_S if ctx.gate.down else None)
            computed = True
            out.update(rec=rec, status="computed")
        finally:
            _SECTION_LOCK.release()
    finally:
        _RENDER.ctx = prev
        out["elapsed_s"] = round(_time.monotonic() - ctx.t0, 1)
        out["store_down"] = ctx.gate.why if ctx.gate.down else ""
        if computed:
            try:
                from .memory_relief import release
                release("den walk")
            except Exception:                           # noqa: BLE001
                pass
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
                  busy_kind, busy, pre, inputs: Optional[dict] = None) -> dict:
    """The row loop. The busy reading is re-taken before every NEW row, so a
    scan rotation that starts mid-render stops it. A name the prefetch did
    not find in the store is read from the repo zip here (not asked again)."""
    rows = []
    computed = cached = skipped_budget = skipped_busy = failed = 0
    inputs = inputs or {}
    for t in names:
        terms = show_terms and t == typed_full
        key = _row_key(t, overrides, terms, _key_row(inputs, t))
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
                          closes=closes, price_source=src, owner_inputs=inputs)
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

#: Used only if scenarios carries no FAMILY_RULE_TEXT (the owner's
#: 2026-10-21 choices, worded as scenarios words them).
_FAMILY_RULES_FALLBACK = [
    ("earnings", "value per share = scenario EPS × band [min(peer P/E, own low), mean(peer, own median), "
                 "max(peer, own high)]; own history of 4+ readings drops its single lowest and highest first; "
                 "no own history → the three cases' own closing P/E; with a band the P/E bridge moves only "
                 "the what-if price"),
    ("book", "justified P/B = (ROE − g) / (COE − g), COE = rf + β × ERP; value = P/B × BVPS; "
             "downside / upside = ROE ∓ step; g = min(long-run g, ROE × (1 − payout))"),
    ("ev_ebitda", "EV = multiple × EBITDA per share; equity per share = EV − net debt (debt − cash − "
                  "short-term investments) − minorities; each case on its own EBITDA, the metals row on a "
                  "mid-cycle EBITDA margin"),
    ("ev_sales", "EV = multiple × sales per share; equity per share = EV − net debt − minorities"),
    ("nav", "typed NAV per share × (1 − b, 1, 1 + b); nothing typed → no target, P/B shown as a fallback"),
    ("embedded_value", "typed embedded value per share × (1 − b, 1, 1 + b); nothing typed → no target"),
    ("sotp", "holding company: listed stakes at market less a holding discount; conglomerate: Σ segment "
             "EBIT × segment EV/EBIT less net debt and minorities"),
]


def family_rules() -> list:
    """(family, rule text) for §13 B2 and the walk — scenarios' own text
    (FAMILY_RULE_TEXT, builder A) so the page cannot drift from the code."""
    try:
        from trading.valuation import scenarios as SC
        txt = getattr(SC, "FAMILY_RULE_TEXT", None)
    except Exception:                                   # noqa: BLE001
        txt = None
    if not isinstance(txt, dict) or not txt:
        return list(_FAMILY_RULES_FALLBACK)
    out = [(k, str(v)) for k, v in txt.items() if k != "all"]
    if txt.get("all"):
        out.append(("all routes", str(txt["all"])))
    return out


FAMILY_RULES = family_rules()

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
                          "rate, bounded 0-50%); checked against the audited Normalized Income line when present; "
                          "the target starts from normalised PAT on EVERY route when it is a number, else reported "
                          "PAT, labelled (owner choice 2026-10-21, banks included)"),
    ("12 quality", "cash conversion = OCF ÷ PAT, net debt ÷ EBITDA, interest cover = EBIT ÷ interest, each flagged at "
                   "the thresholds in Part A; lenders' leverage lines DATA_NEEDED, power utilities keep them; "
                   "score = ASSUMED weights; a low score and an unexplained share bridge print as warnings beside "
                   "the verdict, never gates"),
    ("15-16 cases", "the chain in Part B3 run three times with the case deltas (Part A) added to revenue growth, "
                    "EBITDA margin and the P/E bridge total; constant-currency growth for converted names; with a "
                    "P/E band the bridge moves only the what-if price printed beside the range"),
    ("17 range", "per family as listed under B2; own P/E history = FY-end close ÷ (normalised PAT ÷ that year's "
                 "shares), odd years flagged not dropped; 3×3 grid growth {−1,0,+1}pp × margin {−1,0,+1}pp × the "
                 "band mid and a multiple row {−10,0,+10} points; the three cases are never sorted"),
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
            "terms": [], "routes": [], "chain_cells": [], "live": {},
            "family_rules": family_rules(), "stage_rules": STAGE_RULES}


def formulas_section(flag: Optional[str] = None, error: str = "",
                     live: Optional[dict] = None) -> dict:
    """Den §13: the editable assumption rows (with the stored draft) and
    every formula registry the chain runs. Stored inputs only. `live` = the
    inputs fed on this render (page_live of §12's rows); a sheet number
    whose input is not fed is labelled "no effect yet" with the reason, and
    the label goes when its input is live (item 9)."""
    from trading.valuation import assumptions as ASM
    from trading.valuation import route as R
    from trading.valuation import scenarios as SC
    from trading.valuation import terms as T
    values = load_assumptions()
    if live is None:
        live = page_live([])
    eff = effective_draft_keys(values, live=live)
    try:
        described = ASM.describe(values, live=live)
    except TypeError:                                   # an older assumptions module
        described = ASM.describe(values)
    rows = []
    for r in described:
        r = dict(r)
        # VAL-11: editable, but changes no result on this render — labelled
        r["no_effect"] = no_effect_reason(r["key"], live) or ""
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
            "chain_cols": chain_cols, "live": dict(live or {}),
            "family_rules": family_rules(), "stage_rules": STAGE_RULES}
