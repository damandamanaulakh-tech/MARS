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
        return out
    except Exception as e:
        log.warning("Mongo storage_stats failed: %s", e)
        return None


# ---------- portfolio ----------

def save_portfolio(doc: dict, doc_id: str = "default") -> None:
    if not _CONNECTED or _DB is None:
        return
    try:
        _DB.portfolio.replace_one({"_id": doc_id}, {**doc, "_id": doc_id}, upsert=True)
    except Exception as e:
        log.warning("Mongo save_portfolio failed: %s", e)


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


# ---------- closed trades ----------

def append_trade(trade_doc: dict) -> None:
    if not _CONNECTED or _DB is None:
        return
    try:
        _DB.trades.insert_one(trade_doc)
    except Exception as e:
        log.warning("Mongo append_trade failed: %s", e)


def list_trades(limit: int = 1000) -> list:
    if not _CONNECTED or _DB is None:
        return []
    try:
        cur = _DB.trades.find({}, {"_id": 0}).sort("exit_date", -1).limit(limit)
        return list(cur)
    except Exception as e:
        log.warning("Mongo list_trades failed: %s", e)
        return []


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
    """Newest archived market-info payload of one kind (e.g. the FDA
    newsroom items). None when Mongo is down or nothing stored."""
    if not _CONNECTED or _DB is None:
        return None
    try:
        return _DB.market_info_raw.find_one({"kind": kind},
                                            sort=[("day", -1)])
    except Exception as e:
        log.warning("Mongo load_market_info_raw failed: %s", e)
        return None


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
        for doc in _DB.ohlcv.find({"ticker": {"$in": names}}, proj):
            t = doc.get("ticker")
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


def list_journal_archive(limit: int = 2000) -> list:
    if not _CONNECTED or _DB is None:
        return []
    try:
        cur = _DB.journal_archive.find({}, {"_id": 0}).sort("ts", -1).limit(limit)
        return list(cur)
    except Exception as e:
        log.warning("Mongo list_journal_archive failed: %s", e)
        return []


# ---------- settings overrides ----------

def save_settings(overrides: dict[str, Any], doc_id: str = "default") -> None:
    """MERGE the given keys into the settings doc — never replace it.

    CODEX REVIEW FIX (PR #68): this was replace_one, so any PARTIAL writer
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
