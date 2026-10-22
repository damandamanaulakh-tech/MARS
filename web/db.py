"""MongoDB Atlas persistence.

If MONGO_URL env var is set, the portfolio + closed trades + settings
overrides + Upstox token are mirrored to Mongo on every save. If it's
missing or the connection fails, we silently fall back to on-disk JSON
so the app keeps working locally.

Collections in db `sourceborn`:
  - portfolio    : single doc keyed by _id="default" (or per-user later)
  - trades       : one doc per closed trade
  - settings     : single doc keyed by _id="default", Config overrides
  - upstox_token : single doc keyed by _id="default", access token + meta
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger(__name__)

_CLIENT = None
_DB = None
_CONNECTED = False


def _try_connect() -> None:
    global _CLIENT, _DB, _CONNECTED
    url = os.environ.get("MONGO_URL")
    if not url:
        log.info("MONGO_URL not set — using JSON fallback for persistence")
        return
    try:
        from pymongo import MongoClient
        from pymongo.errors import ConfigurationError, ConnectionFailure
    except ImportError:
        log.warning("pymongo not installed — using JSON fallback")
        return
    try:
        _CLIENT = MongoClient(
            url, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000,
        )
        # Force a server check to fail fast
        _CLIENT.admin.command("ping")
        _DB = _CLIENT[os.environ.get("MONGO_DB", "sourceborn")]
        _CONNECTED = True
        log.info("MongoDB connected: %s", os.environ.get("MONGO_DB", "sourceborn"))
    except (ConfigurationError, ConnectionFailure, Exception) as e:
        log.warning("MongoDB connection failed (%s) — JSON fallback active", e)
        _CLIENT = None
        _DB = None
        _CONNECTED = False


_try_connect()

# AUDIT FIX C1 (2026-08-09): the connection used to be attempted exactly ONCE,
# at import — a boot during a 30-second Atlas blip left the process on JSON-
# only persistence for its whole life, silently trading a fresh phantom book
# whose trades vanished at the next restart. Now every is_connected() check
# retries a dead connection at most once per minute, and the moment it
# recovers, web/state.heal_after_reconnect() replays what Mongo missed.
_RETRY_COOLDOWN_SEC = 60.0
_LAST_ATTEMPT = 0.0
_BOOT_DEGRADED = bool(os.environ.get("MONGO_URL")) and not _CONNECTED
_RECOVERED_PENDING = False


def mongo_expected() -> bool:
    """The owner configured Mongo — running without it is a DEGRADED state."""
    return bool(os.environ.get("MONGO_URL"))


def boot_was_degraded() -> bool:
    return _BOOT_DEGRADED


def recovered_pending() -> bool:
    """True once, after a dead->alive transition, until claimed by heal."""
    return _RECOVERED_PENDING


def claim_recovery() -> None:
    global _RECOVERED_PENDING
    _RECOVERED_PENDING = False


def ensure_connected() -> bool:
    """Reconnect a dead Mongo link, at most once per cooldown window."""
    global _LAST_ATTEMPT, _RECOVERED_PENDING
    if _CONNECTED:
        return True
    if not mongo_expected():
        return False
    import time as _time
    now = _time.time()
    if now - _LAST_ATTEMPT < _RETRY_COOLDOWN_SEC:
        return False
    _LAST_ATTEMPT = now
    _try_connect()
    if _CONNECTED:
        log.warning("MongoDB RECONNECTED after outage — heal pass pending")
        _RECOVERED_PENDING = True
    return _CONNECTED


def is_connected() -> bool:
    if not _CONNECTED:
        ensure_connected()
    return _CONNECTED


def status() -> dict:
    return {
        "connected": _CONNECTED,
        "db": os.environ.get("MONGO_DB", "sourceborn") if _CONNECTED else None,
        "url_set": bool(os.environ.get("MONGO_URL")),
        "degraded": mongo_expected() and not _CONNECTED,
        "boot_degraded": _BOOT_DEGRADED,
        # a connection that answers reads but refuses writes is NOT healthy:
        # the book is not being saved (2026-09-24, Atlas over quota)
        "write_error": LAST_WRITE_ERROR,
        "write_error_at": LAST_WRITE_ERROR_AT,
        "writes_landing": writes_landing(),
    }


def storage_stats(top: int = 12) -> Optional[dict]:
    """Atlas storage in MB — read-only, sizes only, never raises.

    OWNER ASK 2026-09-24 ("chk mongo / its hitting 512 +"): the free Atlas
    tier is capped at 512 MB and nothing in the app could say how close it
    was — a session cannot reach the owner's cluster, and estimating from
    code put the OHLCV store at ~70 MB with nothing else large, which does
    not explain a 512. So the number itself goes on /healthz, where both the
    owner and a session can read it without a login.

    `data_mb` + `index_mb` is what Atlas counts against the M0 limit;
    `storage_mb` is on-disk after compression and is shown for completeness.
    Collections are listed largest-first so the culprit, when there is one,
    is the first row. None when Mongo is offline or the command is refused.
    """
    if not _CONNECTED or _DB is None:
        return None
    try:
        scale = 1024 * 1024
        d = _DB.command("dbStats", scale=scale)
        out = {
            "data_mb": round(float(d.get("dataSize", 0)), 1),
            "index_mb": round(float(d.get("indexSize", 0)), 1),
            "storage_mb": round(float(d.get("storageSize", 0)), 1),
            "objects": int(d.get("objects", 0)),
            "collections": [],
        }
        rows = []
        for name in _DB.list_collection_names():
            try:
                c = _DB.command("collStats", name, scale=scale)
                rows.append({
                    "name": name,
                    "data_mb": round(float(c.get("size", 0)), 1),
                    "index_mb": round(float(c.get("totalIndexSize", 0)), 1),
                    "count": int(c.get("count", 0)),
                })
            except Exception:
                continue
        rows.sort(key=lambda r: -(r["data_mb"] + r["index_mb"]))
        out["collections"] = rows[:max(1, int(top))]
        # OWNER 2026-09-24 21:40 IST: "issue is at mongo 513 MB on 512 plan".
        # This db measures 68 MB, so the rest sits in ANOTHER database on the
        # same cluster (the free-tier cap counts the whole cluster). List
        # every database with its logical size so the hog is named here.
        out["cluster"] = _cluster_sizes(scale)
        out["last_write_error"] = LAST_WRITE_ERROR
        return out
    except Exception as e:
        log.warning("Mongo storage_stats failed: %s", e)
        return None


def _cluster_sizes(scale: int) -> Optional[dict]:
    """Logical size (data + indexes) of EVERY database on the cluster —
    what Atlas counts against the 512 MB free-tier cap. Read-only; None when
    the user lacks listDatabases (then only the app's own db is known)."""
    if _CLIENT is None:
        return None
    try:
        rows = []
        for name in _CLIENT.list_database_names():
            if name in ("admin", "local", "config"):
                continue
            try:
                s = _CLIENT[name].command("dbStats", scale=scale)
                rows.append({
                    "name": name,
                    "data_mb": round(float(s.get("dataSize", 0)), 1),
                    "index_mb": round(float(s.get("indexSize", 0)), 1),
                    "storage_mb": round(float(s.get("storageSize", 0)), 1),
                    "collections": int(s.get("collections", 0)),
                })
            except Exception:                                 # noqa: BLE001
                rows.append({"name": name, "data_mb": None, "index_mb": None,
                             "storage_mb": None, "collections": None,
                             "note": "dbStats refused"})
        rows.sort(key=lambda r: -((r["data_mb"] or 0) + (r["index_mb"] or 0)))
        total = round(sum((r["data_mb"] or 0) + (r["index_mb"] or 0) for r in rows), 1)
        return {"databases": rows, "total_mb": total,
                "note": "data_mb + index_mb per database is what the Atlas "
                        "free tier counts against 512 MB, cluster-wide."}
    except Exception as e:                                    # noqa: BLE001
        log.warning("Mongo cluster sizes failed: %s", e)
        return None


# ---------- portfolio ----------

#: The reason the LAST book save was refused, or None — set by
#: save_portfolio, cleared by the next success. 2026-09-24: Atlas refused
#: every write for hours ("over your space quota") and the only trace was a
#: log line; the dashboard's STORAGE chip stayed green and each restart
#: rolled the book back to the last copy that had landed. Read by /healthz
#: and db.status() so a refused write is visible from outside.
LAST_WRITE_ERROR: Optional[str] = None
LAST_WRITE_ERROR_AT: Optional[str] = None


def save_portfolio(doc: dict, doc_id: str = "default") -> None:
    global LAST_WRITE_ERROR, LAST_WRITE_ERROR_AT
    if not _CONNECTED or _DB is None:
        return
    try:
        _DB.portfolio.replace_one({"_id": doc_id}, {**doc, "_id": doc_id}, upsert=True)
        LAST_WRITE_ERROR = None
        LAST_WRITE_ERROR_AT = None
    except Exception as e:
        from datetime import datetime as _dt
        LAST_WRITE_ERROR = f"{type(e).__name__}: {str(e)[:200]}"
        LAST_WRITE_ERROR_AT = _dt.utcnow().isoformat()
        log.warning("Mongo save_portfolio failed: %s", e)


def writes_landing() -> bool:
    """False while Mongo is configured and REFUSED the last book save
    (LAST_WRITE_ERROR set) — the book is not being saved.

    XR-071 (entry-fix batch 2026-10-22): `_CONNECTED` is set only at connect
    time and a refused write never clears it, so `is_connected()` stayed
    True through the whole September quota episode and the scans kept buying
    into a book that vanished at the next restart. The scheduler's entry
    halt (`web/scheduler._degraded_halt`) reads THIS as well. The next save
    that lands clears LAST_WRITE_ERROR, so the halt lifts by itself.

    Deliberately NOT folded into is_connected(): every writer gates on that,
    and a False there would stop the very save that proves writes are back.
    Mongo not configured (JSON dev run) = True: nothing is expected to land.
    """
    if not mongo_expected():
        return True
    return LAST_WRITE_ERROR is None


def load_portfolio(doc_id: str = "default") -> Optional[dict]:
    if not _CONNECTED or _DB is None:
        return None
    try:
        doc = _DB.portfolio.find_one({"_id": doc_id})
        if doc:
            doc.pop("_id", None)
        return doc
    except Exception as e:
        log.warning("Mongo load_portfolio failed: %s", e)
        return None


# ---------- closed-trade archive ----------
#
# AUDIT M-4 + M-10 (owner: "go for next", 2026-10-02). The `trades`
# collection had writer and reader code with no caller, while every closed
# trade lived in the ONE portfolio document — the only uncapped list in it,
# heading for Mongo's 16 MB document limit. The collection is now the
# overflow archive: the save wrapper (web/state.py) moves trades beyond
# trading.paper.TRADES_DOC_CAP here and trims the book only after the
# archive write succeeded, so a closed trade is never lost.

def _trade_key(d: dict) -> str:
    """Deterministic id, so a save that dies between the archive write and
    the trim re-archives the same trades without duplicating them."""
    return (f"{d.get('ticker')}|{d.get('entry_date')}|{d.get('exit_date')}|"
            f"{d.get('qty')}|{d.get('reason')}")


def archive_trades(trade_docs: list) -> bool:
    """Upsert closed-trade dicts (ClosedTrade.to_dict()) into the archive.
    True when every row is stored (or there is nothing to store)."""
    if not trade_docs:
        return True
    if not _CONNECTED or _DB is None:
        return False
    try:
        from pymongo import UpdateOne
        ops = [UpdateOne({"_id": _trade_key(d)},
                         {"$setOnInsert": {**dict(d), "_id": _trade_key(d)}},
                         upsert=True)
               for d in trade_docs]
        _DB.trades.bulk_write(ops, ordered=False)
        return True
    except Exception as e:
        log.warning("Mongo archive_trades failed: %s", e)
        return False


# OWNER 2026-10-11, item 5: "remove the entries before 15 September". The
# journal is append-only (standing rule 3), so nothing is deleted: trades and
# journal lines closed/stamped before this cut are moved to the archive
# collections with `pre_mars: True` and left out of every live reader by
# default. `include_pre_mars=True` brings them back (the /trades "show
# archive" switch and research scripts).
PRE_MARS_CUT_ISO = "2026-09-15T00:00:00"
PRE_MARS_FLAG = "pre_mars"


def _not_pre_mars(include_pre_mars: bool) -> dict:
    return {} if include_pre_mars else {PRE_MARS_FLAG: {"$ne": True}}


def list_trades(limit: int = 0, include_pre_mars: bool = False,
                with_ids: bool = False) -> list:
    """Archived closed trades, OLDEST first (the order the CSV wants).
    `limit` > 0 keeps the newest N. [] when Mongo is down. Trades archived
    as pre-MARS (before 2026-09-15) are left out unless asked for.
    `with_ids` keeps each row's `_id` (the price audit corrects by it)."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        cur = _DB.trades.find(_not_pre_mars(include_pre_mars),
                              None if with_ids else {"_id": 0}).sort("exit_date", -1)
        if limit and limit > 0:
            cur = cur.limit(int(limit))
        return list(reversed(list(cur)))
    except Exception as e:
        log.warning("Mongo list_trades failed: %s", e)
        return []


def count_archived_trades() -> int:
    if not _CONNECTED or _DB is None:
        return 0
    try:
        return int(_DB.trades.count_documents({}, maxTimeMS=2000))
    except Exception as e:
        log.warning("Mongo count_archived_trades failed: %s", e)
        return 0


# Owner 2026-10-21 (trade price audit): the price fields of an ARCHIVED
# closed trade, rewritten on the owner's tap from /trades?audit=1. Live-book
# rows are rewritten in place and land through the normal save; only the
# archive needs this. The id (ticker|entry|exit|qty|reason) carries no price,
# so it is stable across a correction.
_TRADE_PRICE_FIELDS = ("entry_price", "exit_price", "gross_pnl", "costs", "net_pnl")
# qty moves only on a corporate-action restatement (a split between buy and
# sell: qty × ratio, entry ÷ ratio). The _id keeps the ORIGINAL qty — it is a
# key, not a figure; the row's own qty field carries the restated count.
_TRADE_INT_FIELDS = ("qty",)


def update_trade_prices(trade_id: str, fields: dict) -> bool:
    """UpdateOne by _id with the price fields (+ qty on a restatement) only.
    False when Mongo is down, the row is missing, or the write fails — the
    caller then refuses the correction instead of moving cash for a row that
    did not change."""
    if not _CONNECTED or _DB is None or not fields:
        return False
    doc = {k: float(v) for k, v in fields.items() if k in _TRADE_PRICE_FIELDS}
    doc.update({k: int(v) for k, v in fields.items() if k in _TRADE_INT_FIELDS})
    if isinstance(fields.get("ca_applied"), (list, tuple)):
        doc["ca_applied"] = [str(x) for x in fields["ca_applied"]]
    if not doc:
        return False
    try:
        res = _DB.trades.update_one({"_id": trade_id}, {"$set": doc})
        return int(getattr(res, "matched_count", 0) or 0) > 0
    except Exception as e:
        log.warning("Mongo update_trade_prices failed: %s", e)
        return False


# ---------- backtest archive ----------

def save_backtest(doc: dict) -> None:
    """Append a backtest summary row. Never overwrites — full history kept."""
    if not _CONNECTED or _DB is None:
        return
    try:
        _DB.backtests.insert_one(doc)
    except Exception as e:
        log.warning("Mongo save_backtest failed: %s", e)


def list_backtests(limit: int = 50) -> list:
    if not _CONNECTED or _DB is None:
        return []
    try:
        cur = _DB.backtests.find({}, {"_id": 0}).sort("ts", -1).limit(limit)
        return list(cur)
    except Exception as e:
        log.warning("Mongo list_backtests failed: %s", e)
        return []


# ---------- OHLCV history store (bulk Upstox download → durable) ----------

def save_history_doc(doc: dict) -> None:
    """Upsert one ticker's full daily history (keyed by _id=ticker)."""
    if not _CONNECTED or _DB is None:
        return
    try:
        _DB.ohlcv.replace_one({"_id": doc["_id"]}, doc, upsert=True)
    except Exception as e:
        log.warning("Mongo save_history_doc failed: %s", e)


def load_history_doc(ticker: str) -> Optional[dict]:
    if not _CONNECTED or _DB is None:
        return None
    try:
        return _DB.ohlcv.find_one({"_id": ticker})
    except Exception as e:
        log.warning("Mongo load_history_doc failed: %s", e)
        return None


def save_fii_rows(rows: list) -> int:
    """Upsert NSDL FPI daily rows (Node 3). rows = [(iso_date, buy, sell,
    net)]. Keyed by date so re-fetches overwrite, never duplicate. Returns
    how many were written (0 when Mongo is down — the caller journals)."""
    if not _CONNECTED or _DB is None or not rows:
        return 0
    n = 0
    try:
        for d, buy, sell, net in rows:
            _DB.fii_daily.replace_one(
                {"_id": str(d)},
                {"_id": str(d), "date": str(d), "fii_buy": float(buy),
                 "fii_sell": float(sell), "fii_net": float(net),
                 "source": "nsdl_settlement"},
                upsert=True)
            n += 1
    except Exception as e:
        log.warning("Mongo save_fii_rows failed: %s", e)
    return n


def load_fii_rows(since_iso: str = "") -> list:
    """FII daily rows from the store, ascending. [] when Mongo is down —
    flows.py treats an empty store as 'no store', never as zero flow."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        q = {"date": {"$gte": since_iso}} if since_iso else {}
        return list(_DB.fii_daily.find(q).sort("date", 1))
    except Exception as e:
        log.warning("Mongo load_fii_rows failed: %s", e)
        return []


def save_index_rows(rows: list) -> int:
    """Upsert index-daily rows (Node 3 index tail). rows = [{'date': iso,
    'nifty_open': ..., ...}] in the committed CSV's column names. Keyed by
    date so re-fetches overwrite, never duplicate."""
    if not _CONNECTED or _DB is None or not rows:
        return 0
    n = 0
    try:
        for r in rows:
            d = str(r.get("date") or "")
            if not d:
                continue
            _DB.index_daily.replace_one(
                {"_id": d}, {**{k: v for k, v in r.items()}, "_id": d,
                             "date": d}, upsert=True)
            n += 1
    except Exception as e:
        log.warning("Mongo save_index_rows failed: %s", e)
    return n


def save_sector_rows(rows: list) -> int:
    """Upsert sector-index daily rows (owner order 2026-09-09 "fix it").
    Keyed by date|sector so a re-fetch overwrites rather than duplicates."""
    if not _CONNECTED or _DB is None or not rows:
        return 0
    n = 0
    try:
        for r in rows:
            d, s = str(r.get("date") or ""), str(r.get("sector") or "")
            if not d or not s:
                continue
            _DB.sector_index.replace_one(
                {"_id": f"{d}|{s}"}, {**r, "_id": f"{d}|{s}"}, upsert=True)
            n += 1
    except Exception as e:
        log.warning("Mongo save_sector_rows failed: %s", e)
    return n


def load_sector_rows(since_iso: str = "") -> list:
    """Stored sector rows, ascending. [] when Mongo is down — the sector
    card then hides rather than showing a stale or invented move."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        q = {"date": {"$gte": since_iso}} if since_iso else {}
        return list(_DB.sector_index.find(q).sort("date", 1))
    except Exception as e:
        log.warning("Mongo load_sector_rows failed: %s", e)
        return []


def save_macro_rows(rows: list) -> int:
    """Upsert macro readings (VALFIX 2026-10-21: 10-year G-sec yield, RBI
    repo, RBI USD/INR; batch 3: FRED + NSE allIndices). Keyed series|date so
    a re-read overwrites its own day and never duplicates.

    Returns the count the store ACKNOWLEDGED (review 2026-10-21): one
    unordered bulk_write of ReplaceOne(upsert) per 500 rows;
    upserted + matched per chunk; a BulkWriteError counts what its details
    say landed; any other failure logs and returns what landed so far — the
    job journals the rows NOT stored instead of reading them as stored."""
    if not _CONNECTED or _DB is None or not rows:
        return 0
    from pymongo import ReplaceOne
    from pymongo.errors import BulkWriteError
    chunk_rows = 500          # the first FRED run upserts ~7k rows: 15 round trips, not 7k

    def _acked(res) -> int:
        # upserted + matched (a ReplaceOne on an existing _id is matched);
        # an unacknowledged write counts 0 — nothing proves it landed
        if res is None or not getattr(res, "acknowledged", True):
            return 0
        try:
            return int(res.upserted_count or 0) + int(res.matched_count or 0)
        except Exception:                                # noqa: BLE001
            return 0

    ops = []
    for r in rows:
        s, d = str(r.get("series") or ""), str(r.get("date") or "")
        if not s or not d:
            continue
        ops.append(ReplaceOne({"_id": f"{s}|{d}"}, {**r, "_id": f"{s}|{d}"}, upsert=True))
    n = 0
    for i in range(0, len(ops), chunk_rows):
        chunk = ops[i:i + chunk_rows]
        try:
            n += _acked(_DB.macro_daily.bulk_write(chunk, ordered=False))
        except BulkWriteError as e:
            det = e.details or {}
            got = int(det.get("nUpserted", 0) or 0) + int(det.get("nMatched", 0) or 0)
            n += got
            log.warning("Mongo save_macro_rows: %d of %d row(s) in a chunk refused (%s)",
                        len(chunk) - got, len(chunk),
                        ((det.get("writeErrors") or [{}])[0] or {}).get("errmsg", ""))
        except Exception as e:                           # noqa: BLE001
            log.warning("Mongo save_macro_rows failed after %d row(s): %s", n, e)
            return n
    return n


def load_macro_rows(series: str = None) -> list:
    """Stored macro readings, ascending by date (one series or all). []
    when Mongo is down — the valuation view then says the feed was never
    read, never an invented value."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        q = {"series": series} if series else {}
        return list(_DB.macro_daily.find(q).sort("date", 1))
    except Exception as e:
        log.warning("Mongo load_macro_rows failed: %s", e)
        return []


def load_index_rows(since_iso: str = "") -> list:
    """Stored index-daily rows, ascending. [] when Mongo is down — every
    consumer then behaves exactly as before Node 3 (committed CSV alone)."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        q = {"date": {"$gte": since_iso}} if since_iso else {}
        return list(_DB.index_daily.find(q).sort("date", 1))
    except Exception as e:
        log.warning("Mongo load_index_rows failed: %s", e)
        return []


def save_constituent_snapshot(month: str, name: str, rows: list) -> bool:
    """One dated constituents list (Node 3 forward-PIT record). Keyed by
    (month, list name); a re-run in the same month overwrites its own doc."""
    if not _CONNECTED or _DB is None or not rows:
        return False
    try:
        _DB.index_constituents.replace_one(
            {"_id": f"{month}:{name}"},
            {"_id": f"{month}:{name}", "month": month, "name": name,
             "fetched": datetime.utcnow().isoformat(),
             "rows": rows},
            upsert=True)
        return True
    except Exception as e:
        log.warning("Mongo save_constituent_snapshot failed: %s", e)
        return False


def save_market_info_raw(kind: str, day: str, payload) -> bool:
    """Archive one raw Market-Information payload (owner order 2026-08-24:
    Upstox FII/DII wire). Keyed kind:day so a re-run overwrites its own
    day. The raw copy exists so a mapper shape-miss loses nothing."""
    if not _CONNECTED or _DB is None or payload is None:
        return False
    try:
        _DB.market_info_raw.replace_one(
            {"_id": f"{kind}:{day}"},
            {"_id": f"{kind}:{day}", "kind": kind, "day": day,
             "fetched": datetime.utcnow().isoformat(), "payload": payload},
            upsert=True)
        return True
    except Exception as e:
        log.warning("Mongo save_market_info_raw failed: %s", e)
        return False


def load_market_info_raw(kind: str) -> Optional[dict]:
    """Newest archived market-info payload of one kind ("fii" / "dii").
    None when Mongo is down or nothing stored. Used by
    `market_info_shape` for the /healthz shape probe (audit M-3)."""
    if not _CONNECTED or _DB is None:
        return None
    try:
        return _DB.market_info_raw.find_one({"kind": kind},
                                            sort=[("day", -1)])
    except Exception as e:
        log.warning("Mongo load_market_info_raw failed: %s", e)
        return None


def _shape(v, depth: int = 0):
    """Structure only — key names, types, list lengths — never a value."""
    if depth > 5:
        return "..."
    if isinstance(v, dict):
        return {str(k): _shape(x, depth + 1) for k, x in list(v.items())[:40]}
    if isinstance(v, list):
        return {"list_len": len(v),
                "first": _shape(v[0], depth + 1) if v else None}
    return type(v).__name__


def market_info_shape(kind: str) -> Optional[dict]:
    """AUDIT M-3 (2026-10-02): the Upstox FII/DII mapper has never produced
    a row from the payloads stored in the live database, and a session
    cannot read that database. This exposes the STRUCTURE of the newest
    stored payload — key names, types, list lengths, no numbers — so the
    mapper can be fixed against what Upstox really sends."""
    doc = load_market_info_raw(kind)
    if not doc:
        return None
    return {"day": doc.get("day"), "shape": _shape(doc.get("payload"))}


def save_pwoi_rows(rows: list) -> int:
    """Upsert PWOI-shaped FII rows from the Upstox market/fii feed. Keyed
    date|client_type so re-fetches overwrite, never duplicate."""
    if not _CONNECTED or _DB is None or not rows:
        return 0
    n = 0
    try:
        for r in rows:
            d, ct = str(r.get("date") or ""), str(r.get("client_type") or "")
            if not d or not ct:
                continue
            _DB.pwoi_daily.replace_one(
                {"_id": f"{d}|{ct}"}, {**r, "_id": f"{d}|{ct}"}, upsert=True)
            n += 1
    except Exception as e:
        log.warning("Mongo save_pwoi_rows failed: %s", e)
    return n


def load_pwoi_rows() -> list:
    """Stored Upstox-fed PWOI rows, ascending by date. [] when Mongo is
    down — the register then reads the committed panel alone."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        return list(_DB.pwoi_daily.find({}).sort("date", 1))
    except Exception as e:
        log.warning("Mongo load_pwoi_rows failed: %s", e)
        return []


def update_dii_rows(rows: list) -> int:
    """Merge DII cash legs onto the fii_daily docs (keyed by date) — the
    first LIVE DII source since the NSDL DII archive proved OTP-walled."""
    if not _CONNECTED or _DB is None or not rows:
        return 0
    n = 0
    try:
        for r in rows:
            d = str(r.get("date") or "")
            if not d:
                continue
            _DB.fii_daily.update_one(
                {"_id": d},
                {"$set": {"date": d, "dii_buy": float(r["dii_buy"]),
                          "dii_sell": float(r["dii_sell"]),
                          "dii_net": float(r["dii_net"]),
                          "dii_source": "upstox_market_dii"}},
                upsert=True)
            n += 1
    except Exception as e:
        log.warning("Mongo update_dii_rows failed: %s", e)
    return n


def save_sebi_orders(rows: list) -> int:
    """Upsert SEBI enforcement-order matches (owner order 2026-08-25).
    Keyed ticker|order_id; returns how many were NEW so the journal can
    name fresh orders without re-announcing old ones."""
    if not _CONNECTED or _DB is None or not rows:
        return 0
    new = 0
    try:
        for r in rows:
            key = f"{r.get('ticker')}|{r.get('order_id')}"
            res = _DB.sebi_orders.replace_one(
                {"_id": key}, {**r, "_id": key}, upsert=True)
            if res.upserted_id is not None:
                new += 1
    except Exception as e:
        log.warning("Mongo save_sebi_orders failed: %s", e)
    return new


# Owner order 2026-10-21 (the BLSE split): corporate-action rows for HELD
# names, fetched daily from NSE's API by the 08:50 job and read by the
# engine's ex-date adjuster through trading.corporate_actions.set_store_reader.
def save_corporate_actions(rows: list) -> int:
    """Upsert raw NSE CA rows keyed symbol|ex_date|subject. Returns how many
    were NEW so the journal names fresh actions only."""
    if not _CONNECTED or _DB is None or not rows:
        return 0
    new = 0
    try:
        for r in rows:
            sym = str(r.get("symbol") or "").strip().upper()
            ex = str(r.get("exDate") or r.get("ex_date") or "").strip()
            subj = str(r.get("subject") or "").strip()
            if not sym or not ex or not subj:
                continue
            key = f"{sym}|{ex}|{subj[:80].lower()}"
            doc = {**r, "_id": key, "symbol": sym}
            res = _DB.corporate_actions.replace_one({"_id": key}, doc, upsert=True)
            if res.upserted_id is not None:
                new += 1
    except Exception as e:
        log.warning("Mongo save_corporate_actions failed: %s", e)
    return new


def load_corporate_actions(symbols=None) -> list:
    """Stored raw CA rows for the symbols (all when None). [] when Mongo is
    down — the reader then falls back to the committed file."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        q = {}
        if symbols:
            q = {"symbol": {"$in": [str(s).strip().upper() for s in symbols]}}
        return list(_DB.corporate_actions.find(q).limit(5000))
    except Exception as e:
        log.warning("Mongo load_corporate_actions failed: %s", e)
        return []


def load_sebi_orders(ticker: str = "") -> list:
    """Stored SEBI order matches, newest first. [] when Mongo is down."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        q = {"ticker": ticker} if ticker else {}
        return list(_DB.sebi_orders.find(q).sort("date", -1).limit(200))
    except Exception as e:
        log.warning("Mongo load_sebi_orders failed: %s", e)
        return []


def save_fundamentals_rows(rows: list) -> int:
    """Upsert fundamentals rows (Node 3 monthly refresh). rows =
    [{'symbol','field','as_of','value'}] — the committed CSV's own schema.
    Keyed by symbol|field|as_of so a re-fetch updates in place."""
    if not _CONNECTED or _DB is None or not rows:
        return 0
    n = 0
    try:
        for r in rows:
            key = f"{r['symbol']}|{r['field']}|{r['as_of']}"
            _DB.fundamentals.replace_one(
                {"_id": key}, {**r, "_id": key}, upsert=True)
            n += 1
    except Exception as e:
        log.warning("Mongo save_fundamentals_rows failed: %s", e)
    return n


def load_fundamentals_rows() -> list:
    """All stored fundamentals rows. [] when Mongo is down — register_v4
    then reads the committed CSV alone, the pre-Node-3 world."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        return list(_DB.fundamentals.find({}))
    except Exception as e:
        log.warning("Mongo load_fundamentals_rows failed: %s", e)
        return []


def history_tails(rows: int):
    """Stream every stored ticker's LAST `rows` candles (F3 breadth fix).

    One find() with a $slice projection — the server trims each doc's
    candles array to its tail, so ~2,700 names' worth of 200-close windows
    arrive in a single streamed query instead of 2,700 full-history loads
    (each full doc is ~12y of rows; the tail is ~7% of it). Yields raw docs
    {ticker, end, candles}; trading/breadth.py does the reduction. Empty
    iterator when Mongo is down — the caller treats that as "no reading",
    never as breadth of zero.
    """
    if not _CONNECTED or _DB is None:
        return
    try:
        cur = _DB.ohlcv.find(
            {}, {"ticker": 1, "end": 1, "candles": {"$slice": -int(rows)}})
        for doc in cur:
            yield doc
    except Exception as e:
        log.warning("Mongo history_tails failed: %s", e)
        return


def history_docs(tickers, rows: int = 0) -> dict:
    """{ticker: doc} for the NAMED tickers, in ONE query.

    Added 2026-09-11 for the scan's store-first read (owner: "why system
    still stucking at 49"). `history_tails` streams EVERY stored name, which
    is right for breadth but wrong here — the scan wants a named subset.
    One find() with an $in filter and, when `rows` is set, a $slice
    projection so the server trims each candles array to its tail.

    Mongo down or query failure returns {} — the caller then falls back to
    the network, exactly as it behaved before the store was consulted.
    """
    if not _CONNECTED or _DB is None:
        return {}
    names = [str(t) for t in (tickers or []) if t]
    if not names:
        return {}
    proj = {"ticker": 1, "end": 1, "start": 1, "candles": 1}
    if rows and int(rows) > 0:
        proj["candles"] = {"$slice": -int(rows)}
    out: dict = {}
    try:
        # AUDIT M-9 (2026-10-02): filter on `_id`, which Mongo always
        # indexes, not on the unindexed `ticker` field (a collection scan).
        # Every writer stores _id == ticker (history_store builds the doc).
        for doc in _DB.ohlcv.find({"_id": {"$in": names}}, proj):
            t = doc.get("ticker") or doc.get("_id")
            if t:
                out[t] = doc
    except Exception as e:
        log.warning("Mongo history_docs failed: %s", e)
        return {}
    return out


def list_history_meta() -> list:
    """Lightweight index of what's stored: ticker, n, start, end (no candles)."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        cur = _DB.ohlcv.find({}, {"candles": 0}).sort("_id", 1)
        return list(cur)
    except Exception as e:
        log.warning("Mongo list_history_meta failed: %s", e)
        return []


# ---------- decision-journal archive (append-only, unbounded) ----------

def archive_journal(entries: list) -> bool:
    """Append journal entries leaving the in-RAM cap to a durable, unbounded
    collection. The FULL audit trail lives here; RAM keeps only the recent
    tail — this is what stops the always-on process creeping past 512 MB."""
    if not entries:
        return True
    if not _CONNECTED or _DB is None:
        return False
    try:
        _DB.journal_archive.insert_many([dict(e) for e in entries], ordered=False)
        return True
    except Exception as e:
        log.warning("Mongo archive_journal failed: %s", e)
        return False


def list_journal_archive(limit: int = 20000,
                         include_pre_mars: bool = False) -> list:
    """Archived journal lines, OLDEST first, newest `limit` of them. Read by
    the /reports decision-journal CSV (audit M-5, 2026-10-02 — it had no
    caller, so lines that left the in-RAM cap could not be downloaded).
    Lines archived as pre-MARS (before 2026-09-15) are left out unless
    asked for."""
    if not _CONNECTED or _DB is None:
        return []
    try:
        cur = (_DB.journal_archive.find(_not_pre_mars(include_pre_mars),
                                        {"_id": 0})
               .sort("ts", -1).limit(limit))
        return list(reversed(list(cur)))
    except Exception as e:
        log.warning("Mongo list_journal_archive failed: %s", e)
        return []


def count_pre_mars(collection: str) -> int:
    """How many archived rows carry the pre-MARS flag in `trades` or
    `journal_archive`. -1 when Mongo is down or the count fails — the
    migration reads this back BEFORE trimming anything, and -1 must never
    look like "zero rows landed"."""
    if not _CONNECTED or _DB is None:
        return -1
    try:
        return int(_DB[collection].count_documents({PRE_MARS_FLAG: True},
                                                   maxTimeMS=4000))
    except Exception as e:
        log.warning("Mongo count_pre_mars(%s) failed: %s", collection, e)
        return -1


# ---------- settings overrides ----------

def save_settings(overrides: dict[str, Any], doc_id: str = "default") -> None:
    """MERGE the given keys into the settings doc — never replace it.

    REVIEW FIX (PR #68): this was replace_one, so any PARTIAL writer
    wiped every other stored override — the dashboard's mode quick-switch
    saves only {"mode": ...} and was silently deleting the owner's custom
    universe (and every other override) from Mongo; the in-memory values
    survived until the next restart, then bootstrap restored defaults over
    the owner's choices. Latent for every override since the beginning —
    the custom-universe list just made it visible. $set merges instead.
    """
    if not _CONNECTED or _DB is None:
        return
    try:
        _DB.settings.update_one(
            {"_id": doc_id},
            {"$set": dict(overrides)},
            upsert=True,
        )
    except Exception as e:
        log.warning("Mongo save_settings failed: %s", e)


def unset_settings_keys(keys: list, doc_id: str = "default") -> None:
    """Remove specific keys from a settings doc ($unset). save_settings is
    a $set merge by design (PR #68) and cannot delete — migrations that
    retire a stored override need this."""
    if not _CONNECTED or _DB is None or not keys:
        return
    try:
        _DB.settings.update_one(
            {"_id": doc_id},
            {"$unset": {str(k): "" for k in keys}},
        )
    except Exception as e:
        log.warning("Mongo unset_settings_keys failed: %s", e)


def load_settings(doc_id: str = "default") -> dict:
    if not _CONNECTED or _DB is None:
        return {}
    try:
        doc = _DB.settings.find_one({"_id": doc_id})
        if doc:
            doc.pop("_id", None)
            return doc
    except Exception as e:
        log.warning("Mongo load_settings failed: %s", e)
    return {}


# ---------- upstox token persistence ----------

#: The reason the LAST token save did not land, or None. Read by the OAuth
#: callback so a failed save is journaled and shown on the dashboard instead
#: of surfacing days later as "token EXPIRED" (owner 2026-09-24: "still red").
LAST_TOKEN_SAVE_ERROR: Optional[str] = None


def save_upstox_token(token_doc: dict, doc_id: str = "default") -> bool:
    """Persist Upstox access token + user metadata to Mongo so it survives
    Render restarts. Token still expires at 03:30 IST Upstox-side.

    Returns True only when the write landed. 2026-09-24: this used to return
    nothing and log a warning on failure, so the callback could not know the
    engine would re-read YESTERDAY's token a line later — the owner saw a
    green "Upstox: <name>" pill beside a red "token EXPIRED" bar after every
    successful login. The failure is now a value the caller must handle.
    """
    global LAST_TOKEN_SAVE_ERROR
    if not _CONNECTED or _DB is None:
        LAST_TOKEN_SAVE_ERROR = "mongo_disconnected"
        return False
    try:
        _DB.upstox_token.replace_one(
            {"_id": doc_id},
            {**token_doc, "_id": doc_id},
            upsert=True,
        )
        LAST_TOKEN_SAVE_ERROR = None
        return True
    except Exception as e:
        LAST_TOKEN_SAVE_ERROR = f"{type(e).__name__}: {str(e)[:160]}"
        log.warning("Mongo save_upstox_token failed: %s", e)
        return False


def load_upstox_token(doc_id: str = "default") -> Optional[dict]:
    if not _CONNECTED or _DB is None:
        return None
    try:
        doc = _DB.upstox_token.find_one({"_id": doc_id})
        if doc:
            doc.pop("_id", None)
            return doc
    except Exception as e:
        log.warning("Mongo load_upstox_token failed: %s", e)
    return None


def clear_upstox_token(doc_id: str = "default") -> None:
    if not _CONNECTED or _DB is None:
        return
    try:
        _DB.upstox_token.delete_one({"_id": doc_id})
    except Exception as e:
        log.warning("Mongo clear_upstox_token failed: %s", e)


# ---------- watchlist captures (owner item 28, 2026-10-12) ----------
#
# One document per capture of the day's scan watchlist, keyed by its IST
# minute (`_id` == `ts_key`, e.g. "2026-10-12T0935"). BOUNDED: the collection
# keeps the newest WATCHLIST_SNAPSHOT_KEEP documents and each document holds
# at most watchlist_capture.MAX_ROWS rows (300), so the collection can never
# exceed ~120 x ~60 KB = ~7 MB on the 512 MB free cluster. The trim runs only
# AFTER a successful insert — a refused write must not delete history.

WATCHLIST_SNAPSHOT_KEEP = 120


def save_watchlist_snapshot(doc: dict) -> bool:
    """Upsert one watchlist capture (keyed by its `ts_key`) and trim the
    collection to the newest WATCHLIST_SNAPSHOT_KEEP. True only when the
    write landed; never raises (the caller is the end of the 09:20 rotation
    and must not die for a report)."""
    if not _CONNECTED or _DB is None:
        return False
    key = str((doc or {}).get("ts_key") or "")
    if not key:
        return False
    try:
        _DB.watchlist_snapshots.replace_one(
            {"_id": key}, {**doc, "_id": key}, upsert=True)
    except Exception as e:
        log.warning("Mongo save_watchlist_snapshot failed: %s", e)
        return False
    # Retention: only after the insert landed, only the oldest beyond the cap.
    try:
        cur = (_DB.watchlist_snapshots.find({}, {"_id": 1})
               .sort("_id", -1).skip(WATCHLIST_SNAPSHOT_KEEP))
        old = [d["_id"] for d in cur]
        if old:
            _DB.watchlist_snapshots.delete_many({"_id": {"$in": old}})
    except Exception as e:
        log.warning("Mongo watchlist_snapshots trim failed: %s", e)
    return True


def list_watchlist_snapshots(limit: int = 60, fields=None) -> list:
    """Newest first, `_id` stripped. `fields` = the keys to return (a
    projection), None = whole documents. Rows are small when the caller
    asks for the summary keys only — the /reports card does."""
    if not _CONNECTED or _DB is None:
        return []
    proj = {"_id": 0}
    if fields:
        proj.update({str(f): 1 for f in fields})
    try:
        cur = _DB.watchlist_snapshots.find({}, proj).sort("_id", -1)
        if limit and limit > 0:
            cur = cur.limit(int(limit))
        out = []
        for d in cur:
            d = dict(d)
            d.pop("_id", None)      # the projection drops it; belt and braces
            out.append(d)
        return out
    except Exception as e:
        log.warning("Mongo list_watchlist_snapshots failed: %s", e)
        return []


def load_watchlist_snapshot(ts_key: str) -> Optional[dict]:
    """One capture by its key, `_id` stripped; None when absent or Mongo is
    down (the caller then falls back to the CSV on disk, or says so)."""
    if not _CONNECTED or _DB is None or not ts_key:
        return None
    try:
        doc = _DB.watchlist_snapshots.find_one({"_id": str(ts_key)})
        if doc:
            doc.pop("_id", None)
            return dict(doc)
    except Exception as e:
        log.warning("Mongo load_watchlist_snapshot failed: %s", e)
    return None
