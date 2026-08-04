"""Shared in-process trading state. Single Portfolio + Engine for the app.

Persistence: if MONGO_URL env var is set, settings overrides + closed trades
are mirrored to MongoDB Atlas. JSON-on-disk remains the source-of-truth for
the in-progress Portfolio so the app keeps working when Mongo is down.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import ClosedTrade, PaperPosition, Portfolio

from . import db


log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CFG: Optional[Config] = None
_PORTFOLIO: Optional[Portfolio] = None
_ENGINE: Optional[TradingEngine] = None
# Monkey-patched flag so trading/paper.py stays pure; web/state.py wraps save()
_PORTFOLIO_SAVE_PATCHED = False


def _hydrate_portfolio_from_mongo(p: Portfolio, doc: dict) -> None:
    """Replace JSON-loaded portfolio fields from a Mongo doc."""
    p.cash = float(doc.get("cash", p.cash))
    p.positions = {
        k: PaperPosition.from_dict(v) for k, v in (doc.get("positions") or {}).items()
    }
    p.trades = [ClosedTrade.from_dict(t) for t in (doc.get("trades") or [])]
    p.equity_history = list(doc.get("equity_history", []))
    p.last_scan = doc.get("last_scan")
    p.last_scan_picks = list(doc.get("last_scan_picks", []))
    p.last_scan_skipped = list(doc.get("last_scan_skipped", []))
    p.last_scan_opened = list(doc.get("last_scan_opened", []))
    p.scan_matrix = dict(doc.get("scan_matrix", {}))
    p.last_regime = doc.get("last_regime")
    p.last_ifr_fire_date = doc.get("last_ifr_fire_date")
    p.last_ifr_ratio_fire_date = doc.get("last_ifr_ratio_fire_date")
    p.regime_history = list(doc.get("regime_history", []))
    p.scan_history = list(doc.get("scan_history", []))
    p.decision_journal = list(doc.get("decision_journal", []))
    p.virtual_book = dict(doc.get("virtual_book", {}))
    p.pending_orders = list(doc.get("pending_orders", []))
    p.filings = list(doc.get("filings", []))
    # Daily kill-switch state — Mongo is the persistence that actually
    # survives a Render restart (/tmp JSON is wiped), so these MUST ride
    # in the mirror doc or the risk-day fix silently dies on redeploy.
    p.risk_day = doc.get("risk_day")
    p.risk_day_equity = float(doc.get("risk_day_equity", 0.0) or 0.0)
    p.risk_day_killed = bool(doc.get("risk_day_killed", False))
    # Legacy mirror docs predate capital_base; the live book started at 25L.
    p.capital_base = float(doc.get("capital_base", 25_00_000.0) or 25_00_000.0)
    p.dd_peak_equity = float(doc.get("dd_peak_equity", 0.0) or 0.0)
    p.last_save = doc.get("last_save")


def _patch_portfolio_save_to_mirror_mongo() -> None:
    """Wrap Portfolio.save() once so every save mirrors to Mongo (when connected)."""
    global _PORTFOLIO_SAVE_PATCHED
    if _PORTFOLIO_SAVE_PATCHED:
        return
    original_save = Portfolio.save

    def _save_with_mirror(self: Portfolio) -> None:
        # Durable-archive the journal overflow BEFORE original_save trims RAM,
        # so the full append-only trail survives in Mongo (journal_archive)
        # while RAM stays bounded on the always-on process.
        from trading.paper import JOURNAL_RAM_CAP
        if db.is_connected() and len(self.decision_journal) > JOURNAL_RAM_CAP:
            db.archive_journal(self.decision_journal[:-JOURNAL_RAM_CAP])
        original_save(self)
        if not db.is_connected():
            return
        doc = {
            "cash": self.cash,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "trades": [t.to_dict() for t in self.trades],
            "equity_history": self.equity_history,
            "last_scan": self.last_scan,
            "last_scan_picks": self.last_scan_picks,
            "last_scan_skipped": self.last_scan_skipped,
            "last_scan_opened": self.last_scan_opened,
            "last_regime": self.last_regime,
            "last_ifr_fire_date": self.last_ifr_fire_date,
            "last_ifr_ratio_fire_date": self.last_ifr_ratio_fire_date,
            "regime_history": self.regime_history[-200:],
            "scan_history": self.scan_history[-500:],
            "decision_journal": self.decision_journal,   # full, never truncated
            "virtual_book": self.virtual_book,
            "scan_matrix": self.scan_matrix,
            "pending_orders": self.pending_orders[-200:],
            "filings": self.filings[:100],
            "risk_day": self.risk_day,
            "risk_day_equity": self.risk_day_equity,
            "risk_day_killed": self.risk_day_killed,
            "capital_base": self.capital_base,
            "dd_peak_equity": self.dd_peak_equity,
            "last_save": self.last_save,
        }
        db.save_portfolio(doc)

    Portfolio.save = _save_with_mirror  # type: ignore[assignment]
    _PORTFOLIO_SAVE_PATCHED = True


def _coerce_override(cfg, key: str, value):
    """Heal type-poisoned Mongo overrides (recheck fix, 2026-07-27).

    Before the settings bool fix, 1/0 toggles were stored as the STRING "0"
    (truthy). Applying such a stored override raw re-poisons the config on
    every boot, and the settings-save bool branch can't heal it because
    isinstance(existing, bool) is False once the string is in place. Coerce
    strings back to the Config field's real type here, at the only door
    stored overrides enter through."""
    expected = getattr(cfg, key, None)
    if isinstance(expected, bool) and isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(expected, (int, float)) and not isinstance(expected, bool) \
            and isinstance(value, str):
        try:
            return type(expected)(float(value))
        except ValueError:
            return value
    return value


def _bootstrap() -> None:
    global _CFG, _PORTFOLIO, _ENGINE
    _CFG = Config()
    # Apply Mongo-persisted settings overrides (mode + tunables)
    overrides = db.load_settings()
    for key, value in overrides.items():
        if hasattr(_CFG, key):
            try:
                setattr(_CFG, key, _coerce_override(_CFG, key, value))
            except Exception:
                pass
    # Owner order 2026-07-30 ("change the capital to 50 lack, 80 stocks,
    # roughly 1-1.25 for each trade — change it now"; corrected same day:
    # "1.25 lac, 125000, not percentage"): applied ONCE over any stale saved
    # overrides, marker in the separate `migrations` settings doc (the
    # Settings form replaces the `default` doc, so a marker there would not
    # survive the next save). v2 supersedes the same-day v1 (which read the
    # size as 1.25%). Future Settings edits stay fully free.
    if not db.load_settings(doc_id="migrations").get("capital_order_2026_07_30_v2"):
        _CFG.starting_capital = 50_00_000.0
        _CFG.max_positions = 80
        _CFG.position_size_value = 125_000.0
        _CFG.max_position_pct = 0.025
        db.save_settings({**overrides, "starting_capital": 50_00_000.0,
                          "max_positions": 80, "position_size_value": 125_000.0,
                          "max_position_pct": 0.025})
        db.save_settings({"capital_order_2026_07_30": True,
                          "capital_order_2026_07_30_v2": True},
                         doc_id="migrations")

    # Apply mode preset (sets stops, target, etc. per chosen horizon)
    _CFG.apply_mode_preset()
    # Policy C (user-approved 2026-07-16): exits ON — pinned here so a stale
    # `disable_exits` in an old Mongo settings doc can never re-freeze exits.
    _CFG.disable_exits = False

    # Load portfolio: prefer Mongo (survives restarts), fall back to JSON
    _PORTFOLIO = Portfolio.load_or_fresh(_CFG)
    persisted = db.load_portfolio()
    if persisted:
        _hydrate_portfolio_from_mongo(_PORTFOLIO, persisted)

    # Wire save() to mirror to Mongo from now on
    _patch_portfolio_save_to_mirror_mongo()

    # Owner order 2026-07-30: cash follows starting_capital (delta, journaled).
    if _PORTFOLIO.sync_capital(_CFG):
        _PORTFOLIO.save()

    # Policy C migration: re-snapshot every open position's stop/target from
    # its own mode's table (stale intraday / blank tags -> momentum preset).
    # Journaled per position; idempotent, so later boots are no-ops.
    try:
        from trading.exits import retag_positions_per_mode
        if retag_positions_per_mode(_PORTFOLIO) > 0:
            _PORTFOLIO.save()
    except Exception:
        log.exception("Policy C retag failed — positions keep old stops")

    _ENGINE = TradingEngine(_CFG, _PORTFOLIO)

    # Attach Upstox broker if a token is persisted in Mongo
    _attach_broker_if_persisted()


def _attach_broker_if_persisted() -> None:
    """Build a UpstoxClient from the Mongo-persisted token and attach it
    to the engine so refresh() uses real-time quotes."""
    if _ENGINE is None:
        return
    try:
        from trading.brokers.upstox import UpstoxClient, UpstoxConfigError
    except ImportError:
        return
    token_doc = db.load_upstox_token()
    if not token_doc or not token_doc.get("access_token"):
        return
    try:
        client = UpstoxClient.from_env()
    except UpstoxConfigError:
        return
    client.access_token = token_doc.get("access_token")
    client.user_id = token_doc.get("user_id")
    client.user_name = token_doc.get("user_name")
    client.connected_at = token_doc.get("connected_at")
    _ENGINE.attach_broker(client)
    # Route ALL historical candles through Upstox too (Yahoo 429-blocks
    # cloud IPs; scans/backtests starve on yfinance alone).
    from trading import data as trading_data
    trading_data.set_history_broker(client)


def refresh_broker_attachment() -> None:
    """Public hook: re-attach broker after a fresh OAuth callback or disconnect."""
    with _LOCK:
        if _ENGINE is None:
            return
        _attach_broker_if_persisted()
        # If Mongo cleared the token, ensure engine forgets it too
        if db.load_upstox_token() is None:
            _ENGINE.attach_broker(None)
            from trading import data as trading_data
            trading_data.set_history_broker(None)


# {ticker: (price, fetched_at_epoch)} — per-ticker timestamps, because a
# single cache-wide timestamp let dead prices masquerade as live (see below).
_MARKS_CACHE: dict = {"prices": {}}
MARKS_MAX_AGE_SEC = 120.0        # older than this is NOT a live price


def marks(tickers: list[str], max_age: float = MARKS_MAX_AGE_SEC
          ) -> tuple[dict, bool]:
    """Latest prices for tickers. Returns (prices, live).

    FIXED 2026-07-31 — the owner caught the app showing TCS at a price that
    was not the market. The old cache kept one shared timestamp and, when a
    quote fetch FAILED, still returned every previously-cached price and
    reported live=True. A quote pulled hours (or a session) earlier was
    therefore displayed as real-time. That is how a wrong price reached the
    screen while the header claimed "Upstox real-time".

    Now: every price carries its own fetch time, anything older than
    `max_age` is DISCARDED rather than served, and `live` is True only for
    prices actually fresh. Callers fall back to entry marks — clearly
    labelled — instead of trusting a corpse.
    """
    import time
    if not tickers:
        return {}, False
    now = time.time()
    cache: dict = _MARKS_CACHE["prices"]

    # Drop anything past its age, everywhere — a stale entry must never be
    # reachable, not even for a ticker we are not asking about right now.
    for t in [k for k, (_p, ts) in cache.items() if now - ts > max_age]:
        cache.pop(t, None)

    need = [t for t in tickers if t not in cache]
    if need:
        try:
            from trading.quote_router import fetch_quotes as _rq
            got = _rq(need, upstox_client=engine().broker)
            for t, px in (got or {}).items():
                if px and px > 0:
                    cache[t] = (float(px), now)
        except Exception:
            pass

    out = {t: cache[t][0] for t in tickers if t in cache}
    return out, bool(out)


def marks_age(tickers: list[str]) -> float | None:
    """Seconds since the OLDEST live price among `tickers` was fetched.
    None when nothing live is cached. Lets the UI state the age instead of
    implying freshness."""
    import time
    cache = _MARKS_CACHE["prices"]
    ages = [time.time() - ts for t, (_p, ts) in cache.items() if t in set(tickers)]
    return max(ages) if ages else None


# {key: (rows, fetched_at)} — per-key timestamps. A single shared timestamp
# had the same defect as the old marks cache: any later fetch refreshed the
# clock for EVERY entry, so old candles kept looking fresh indefinitely.
_CANDLE_CACHE: dict = {"data": {}}
_CANDLE_TTL = 600.0


def recent_candles(ticker: str = "^NSEI", bars: int = 40) -> list[dict]:
    """F17: last `bars` daily OHLC for a candle chart. 10-min cached, Upstox-
    first via the history router. Returns [] if no data (chart hides itself).

    A FAILED fetch is never cached (2026-07-31): caching the empty result
    blanked the chart for the full TTL after one transient error, and made a
    failure indistinguishable from "this stock genuinely has no data"."""
    import time
    now = time.time()
    key = f"{ticker}:{bars}"
    hit = _CANDLE_CACHE["data"].get(key)
    if hit is not None and (now - hit[1]) < _CANDLE_TTL:
        return hit[0]
    out: list[dict] = []
    try:
        from trading import data as _data
        hist = _data.fetch_daily([ticker], period="4mo").get(ticker)
        if hist is not None and not hist.empty:
            tail = hist.dropna(subset=["Close"]).tail(bars)
            for ts, row in tail.iterrows():
                out.append({
                    "t": ts.strftime("%d-%b") if hasattr(ts, "strftime") else str(ts),
                    "o": round(float(row["Open"]), 2),
                    "h": round(float(row["High"]), 2),
                    "l": round(float(row["Low"]), 2),
                    "c": round(float(row["Close"]), 2),
                })
    except Exception:
        pass
    if out:                     # only cache a real result, never a failure
        _CANDLE_CACHE["data"][key] = (out, now)
    return out


def quote_source_status() -> dict:
    """Reports whether the engine is currently sourcing quotes from Upstox or yfinance."""
    from trading.quote_router import status as router_status
    if _ENGINE is None:
        return {"source": "yfinance", "reason": "engine_not_initialised"}
    return router_status(_ENGINE.broker)


def reload_config() -> None:
    """Reload config + apply mode preset + rebuild engine. Called after Settings save."""
    global _ENGINE
    with _LOCK:
        if _CFG is None or _PORTFOLIO is None:
            _bootstrap()
            return
        _CFG.apply_mode_preset()
        _ENGINE = TradingEngine(_CFG, _PORTFOLIO)


def db_status() -> dict:
    return db.status()


def cfg() -> Config:
    with _LOCK:
        if _CFG is None:
            _bootstrap()
        assert _CFG is not None
        return _CFG


def portfolio() -> Portfolio:
    with _LOCK:
        if _PORTFOLIO is None:
            _bootstrap()
        assert _PORTFOLIO is not None
        return _PORTFOLIO


def engine() -> TradingEngine:
    with _LOCK:
        if _ENGINE is None:
            _bootstrap()
        assert _ENGINE is not None
        # Lazy auto-attach: if the engine has no broker but Mongo has a
        # token, attach on demand. This handles the case where Upstox was
        # connected *after* the engine bootstrapped.
        if _ENGINE.broker is None:
            _attach_broker_if_persisted()
        return _ENGINE
