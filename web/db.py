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


def is_connected() -> bool:
    return _CONNECTED


def status() -> dict:
    return {
        "connected": _CONNECTED,
        "db": os.environ.get("MONGO_DB", "sourceborn") if _CONNECTED else None,
        "url_set": bool(os.environ.get("MONGO_URL")),
    }


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

def archive_journal(entries: list) -> None:
    """Append journal entries leaving the in-RAM cap to a durable, unbounded
    collection. The FULL audit trail lives here; RAM keeps only the recent
    tail — this is what stops the always-on process creeping past 512 MB."""
    if not _CONNECTED or _DB is None or not entries:
        return
    try:
        _DB.journal_archive.insert_many([dict(e) for e in entries], ordered=False)
    except Exception as e:
        log.warning("Mongo archive_journal failed: %s", e)


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
    if not _CONNECTED or _DB is None:
        return
    try:
        _DB.settings.replace_one(
            {"_id": doc_id},
            {**overrides, "_id": doc_id},
            upsert=True,
        )
    except Exception as e:
        log.warning("Mongo save_settings failed: %s", e)


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

def save_upstox_token(token_doc: dict, doc_id: str = "default") -> None:
    """Persist Upstox access token + user metadata to Mongo so it survives
    Render restarts. Token still expires at 03:30 IST Upstox-side."""
    if not _CONNECTED or _DB is None:
        return
    try:
        _DB.upstox_token.replace_one(
            {"_id": doc_id},
            {**token_doc, "_id": doc_id},
            upsert=True,
        )
    except Exception as e:
        log.warning("Mongo save_upstox_token failed: %s", e)


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
