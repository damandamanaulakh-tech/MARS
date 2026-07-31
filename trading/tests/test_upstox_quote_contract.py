"""The Upstox quote call, pinned to the documented API contract.

Owner 2026-07-31: "not live price yet, VSSL is 304, ultrachemo is 11892".

ROOT CAUSE: get_quote() sent params={"symbol": instrument_key}. The Upstox v2
market-quote endpoints take **instrument_key**. Every request was therefore
malformed, every quote failed, and the router fell back to a yfinance path
that Render blocks — so the app could never show a live price, no matter how
many times the owner logged in.

Docs: https://upstox.com/developer/api-documentation/ltp/
      https://upstox.com/developer/api-documentation/get-full-market-quote/
"""

from unittest.mock import MagicMock, patch

import pytest

import trading.brokers.upstox as U
from trading.brokers.upstox import LTP_BATCH, UpstoxClient


def _client():
    c = UpstoxClient.__new__(UpstoxClient)
    c._auth_headers = lambda: {"Authorization": "Bearer test"}
    return c


def _resp(data: dict):
    r = MagicMock()
    r.raise_for_status = lambda: None
    r.json = lambda: {"status": "success", "data": data}
    return r


def test_quote_uses_instrument_key_not_symbol():
    """THE bug. `symbol` is not a parameter Upstox accepts."""
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.update(params or {})
        return _resp({"NSE_EQ:RELIANCE": {"last_price": 1327.2}})

    with patch.object(U.httpx, "get", fake_get):
        _client().get_quote("NSE_EQ|INE002A01018")
    assert "instrument_key" in seen, "must send instrument_key"
    assert "symbol" not in seen, "the old broken parameter is back"
    assert seen["instrument_key"] == "NSE_EQ|INE002A01018"


def test_ltp_is_batched_into_one_call():
    """43 positions used to mean 43 sequential HTTP calls."""
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(params["instrument_key"])
        return _resp({})

    keys = [f"NSE_EQ|K{i}" for i in range(40)]
    with patch.object(U.httpx, "get", fake_get):
        _client().get_ltp(keys)
    assert len(calls) == 1, f"expected 1 batched call, made {len(calls)}"
    assert calls[0].count(",") == 39


def test_ltp_chunks_above_the_batch_limit():
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(params["instrument_key"].count(",") + 1)
        return _resp({})

    keys = [f"NSE_EQ|K{i}" for i in range(LTP_BATCH + 10)]
    with patch.object(U.httpx, "get", fake_get):
        _client().get_ltp(keys)
    assert calls == [LTP_BATCH, 10]


def test_prices_map_back_by_instrument_token():
    """The response is keyed SEGMENT:TRADINGSYMBOL, which does NOT match the
    instrument_key we sent — results must map back via instrument_token."""
    data = {
        "NSE_EQ:VSSL": {"last_price": 304.0,
                        "instrument_token": "NSE_EQ|INE050A01025"},
        "NSE_EQ:ULTRACEMCO": {"last_price": 11892.0,
                              "instrument_token": "NSE_EQ|INE481G01011"},
    }
    with patch.object(U.httpx, "get",
                      lambda *a, **k: _resp(data)):
        got = _client().get_ltp(["NSE_EQ|INE050A01025", "NSE_EQ|INE481G01011"])
    assert got == {"NSE_EQ|INE050A01025": 304.0,
                   "NSE_EQ|INE481G01011": 11892.0}


def test_index_keys_work_through_the_same_path():
    data = {"NSE_INDEX:Nifty 50": {"last_price": 24400.0,
                                   "instrument_token": "NSE_INDEX|Nifty 50"}}
    with patch.object(U.httpx, "get", lambda *a, **k: _resp(data)):
        got = _client().get_ltp(["NSE_INDEX|Nifty 50"])
    assert got == {"NSE_INDEX|Nifty 50": 24400.0}


def test_a_failed_batch_returns_nothing_rather_than_guessing():
    import httpx as _h

    def boom(*a, **k):
        raise _h.HTTPError("network down")

    with patch.object(U.httpx, "get", boom):
        assert _client().get_ltp(["NSE_EQ|X"]) == {}


def test_partial_success_is_honest_success():
    """One resolved, one not: return the one, do not fabricate the other."""
    data = {"NSE_EQ:VSSL": {"last_price": 304.0,
                            "instrument_token": "NSE_EQ|INE050A01025"}}
    with patch.object(U.httpx, "get", lambda *a, **k: _resp(data)):
        got = _client().get_ltp(["NSE_EQ|INE050A01025", "NSE_EQ|MISSING"])
    assert got == {"NSE_EQ|INE050A01025": 304.0}


def test_router_turns_batched_prices_into_ticker_prices():
    from trading import quote_router as qr

    class FakeBroker:
        def is_connected(self):
            return True

        def get_ltp(self, keys):
            table = {"NSE_EQ|INE050A01025": 304.0,
                     "NSE_EQ|INE481G01011": 11892.0}
            return {k: table[k] for k in keys if k in table}

    with patch.object(qr, "to_instrument_keys", return_value={
            "VSSL.NS": "NSE_EQ|INE050A01025",
            "ULTRACEMCO.NS": "NSE_EQ|INE481G01011"}), \
         patch.object(qr, "fetch_quotes_yf", return_value={}):
        px = qr.fetch_quotes(["VSSL.NS", "ULTRACEMCO.NS"],
                             upstox_client=FakeBroker())
    assert px == {"VSSL.NS": 304.0, "ULTRACEMCO.NS": 11892.0}


def test_router_calls_the_broker_once_not_per_ticker():
    from trading import quote_router as qr

    class CountingBroker:
        def __init__(self):
            self.calls = 0

        def is_connected(self):
            return True

        def get_ltp(self, keys):
            self.calls += 1
            return {}

    b = CountingBroker()
    keymap = {f"T{i}.NS": f"NSE_EQ|K{i}" for i in range(30)}
    with patch.object(qr, "to_instrument_keys", return_value=keymap), \
         patch.object(qr, "fetch_quotes_yf", return_value={}):
        qr.fetch_quotes(list(keymap), upstox_client=b)
    assert b.calls == 1, f"broker hit {b.calls} times for 30 tickers"
