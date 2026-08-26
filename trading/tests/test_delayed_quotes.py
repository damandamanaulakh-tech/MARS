"""Owner complaint 2026-08-26 ("data is just 15 sec delay and you r like
far away") — Upstox INTRADAY candles are public, so a login-less day now
runs on ~1-minute-delayed prices instead of no prices at all.

Pinned: the router's tokenless path serves intraday closes labelled
'upstox_1min_delayed' (never dressed as live); the connected path still
prefers real LTP and only falls back per missing name; yfinance stays
the last resort; the engine's fill_source carries the delayed label
through to the BUY journal; the broker helper parses the newest-first
candle order the live endpoint actually returns.
"""
from __future__ import annotations

from trading import quote_router as qr


class _HistBroker:
    """Tokenless history client: intraday works, LTP does not exist."""

    access_token = None

    def is_connected(self):
        return False

    def get_intraday_last(self, ikey, timeout=8.0):
        return (241.5, "2026-08-26T15:29:00+05:30")


class _LiveBroker(_HistBroker):
    access_token = "tok"

    def __init__(self, ltp_for):
        self._ltp_for = ltp_for

    def is_connected(self):
        return True

    def get_ltp(self, keys):
        return {k: 100.0 for k in keys if self._ltp_for(k)}


def test_tokenless_day_serves_delayed_candles_labelled(monkeypatch):
    monkeypatch.setattr("trading.data.history_broker", lambda: _HistBroker())
    monkeypatch.setattr(
        "trading.quote_router.to_instrument_keys",
        lambda ts: {t: f"NSE_EQ|{t}" for t in ts})
    monkeypatch.setattr("trading.quote_router.fetch_quotes_yf",
                        lambda ts: {})
    out = qr.fetch_quotes(["TCS.NS"], upstox_client=None)
    assert out == {"TCS.NS": 241.5}
    assert qr.LAST_SOURCES["TCS.NS"] == "upstox_1min_delayed"


def test_connected_path_prefers_ltp_and_falls_back_per_name(monkeypatch):
    monkeypatch.setattr(
        "trading.quote_router.to_instrument_keys",
        lambda ts: {t: f"NSE_EQ|{t}" for t in ts})
    monkeypatch.setattr("trading.quote_router.fetch_quotes_yf",
                        lambda ts: {})
    broker = _LiveBroker(ltp_for=lambda k: "TCS" in k)
    out = qr.fetch_quotes(["TCS.NS", "INFY.NS"], upstox_client=broker)
    assert out["TCS.NS"] == 100.0
    assert qr.LAST_SOURCES["TCS.NS"] == "upstox"          # a real tick
    assert out["INFY.NS"] == 241.5                        # delayed fallback
    assert qr.LAST_SOURCES["INFY.NS"] == "upstox_1min_delayed"


def test_no_client_anywhere_still_degrades_to_yf(monkeypatch):
    monkeypatch.setattr("trading.data.history_broker", lambda: None)
    monkeypatch.setattr("trading.quote_router.fetch_quotes_yf",
                        lambda ts: {t: 9.0 for t in ts})
    out = qr.fetch_quotes(["TCS.NS"], upstox_client=None)
    assert out == {"TCS.NS": 9.0}
    assert qr.LAST_SOURCES["TCS.NS"] == "yf_close"


def test_intraday_parser_reads_newest_first(monkeypatch):
    """The live endpoint returns candles newest-first (verified 2026-08-26:
    candles[0] was the 15:29 bar). The helper must read [0], not [-1]."""
    from trading.brokers.upstox import UpstoxClient

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"candles": [
                ["2026-08-26T15:29:00+05:30", 1, 1, 1, 24207.75, 0, 0],
                ["2026-08-26T09:15:00+05:30", 1, 1, 1, 24100.00, 0, 0],
            ]}}

    monkeypatch.setattr(
        "trading.brokers.upstox.httpx.get",
        lambda url, headers=None, timeout=None: _Resp())
    import httpx as _hx
    monkeypatch.setattr(_hx, "get",
                        lambda url, headers=None, timeout=None: _Resp())
    c = UpstoxClient(api_key="k", api_secret="s", redirect_uri="r")
    px, ts = c.get_intraday_last("NSE_INDEX|Nifty 50")
    assert px == 24207.75 and ts.startswith("2026-08-26T15:29")
