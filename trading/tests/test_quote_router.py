"""Tests for the quote router (Upstox vs yfinance fallback)."""

from unittest.mock import MagicMock, patch

import pytest

from trading.brokers.upstox import UpstoxClient
from trading.quote_router import fetch_quotes, status


# ---- router source selection ----

def test_router_uses_yfinance_when_no_broker():
    with patch("trading.quote_router.fetch_quotes_yf") as mock_yf:
        mock_yf.return_value = {"RELIANCE.NS": 2450.0}
        out = fetch_quotes(["RELIANCE.NS"], upstox_client=None)
    mock_yf.assert_called_once_with(["RELIANCE.NS"])
    assert out["RELIANCE.NS"] == 2450.0


def test_router_uses_yfinance_when_broker_disconnected():
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    # No access_token set -> disconnected
    with patch("trading.quote_router.fetch_quotes_yf") as mock_yf:
        mock_yf.return_value = {"RELIANCE.NS": 2440.0}
        out = fetch_quotes(["RELIANCE.NS"], upstox_client=client)
    mock_yf.assert_called_once()
    assert out["RELIANCE.NS"] == 2440.0


def test_router_uses_upstox_when_connected_and_instrument_known():
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    client.access_token = "T"

    with patch(
        "trading.quote_router.to_instrument_keys",
        return_value={"RELIANCE.NS": "NSE_EQ|INE002A01018"},
    ), patch.object(client, "get_quote") as mock_get:
        mock_get.return_value = {"last_price": 2475.5}
        out = fetch_quotes(["RELIANCE.NS"], upstox_client=client)
    assert out["RELIANCE.NS"] == 2475.5
    mock_get.assert_called_once_with("NSE_EQ|INE002A01018")


def test_router_falls_back_to_yfinance_for_unknown_instrument():
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    client.access_token = "T"

    with patch(
        "trading.quote_router.to_instrument_keys", return_value={}
    ), patch("trading.quote_router.fetch_quotes_yf") as mock_yf:
        mock_yf.return_value = {"WEIRD.NS": 100.0}
        out = fetch_quotes(["WEIRD.NS"], upstox_client=client)
    mock_yf.assert_called_once_with(["WEIRD.NS"])
    assert out["WEIRD.NS"] == 100.0


def test_router_falls_back_for_individual_failure():
    """Mix: one ticker resolves via Upstox; one fails -> yfinance picks up the gap."""
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    client.access_token = "T"

    with patch(
        "trading.quote_router.to_instrument_keys",
        return_value={"RELIANCE.NS": "NSE_EQ|INE002A01018", "TCS.NS": "NSE_EQ|INE467B01029"},
    ), patch.object(client, "get_quote") as mock_get, patch(
        "trading.quote_router.fetch_quotes_yf"
    ) as mock_yf:
        # Reliance returns a quote, TCS returns None -> falls back
        def side_effect(key):
            return {"last_price": 2475.5} if "INE002A01018" in key else None
        mock_get.side_effect = side_effect
        mock_yf.return_value = {"TCS.NS": 3300.0}
        out = fetch_quotes(["RELIANCE.NS", "TCS.NS"], upstox_client=client)

    assert out["RELIANCE.NS"] == 2475.5
    assert out["TCS.NS"] == 3300.0
    mock_yf.assert_called_once_with(["TCS.NS"])


def test_router_reads_ohlc_close_when_last_price_missing():
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    client.access_token = "T"

    with patch(
        "trading.quote_router.to_instrument_keys",
        return_value={"RELIANCE.NS": "NSE_EQ|INE002A01018"},
    ), patch.object(client, "get_quote") as mock_get:
        mock_get.return_value = {"ohlc": {"close": 2440.0}}
        out = fetch_quotes(["RELIANCE.NS"], upstox_client=client)
    assert out["RELIANCE.NS"] == 2440.0


def test_router_empty_input_returns_empty():
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    client.access_token = "T"
    assert fetch_quotes([], upstox_client=client) == {}


def test_router_handles_upstox_exception_per_ticker():
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    client.access_token = "T"

    with patch(
        "trading.quote_router.to_instrument_keys",
        return_value={"RELIANCE.NS": "NSE_EQ|INE002A01018"},
    ), patch.object(client, "get_quote", side_effect=RuntimeError("network")), patch(
        "trading.quote_router.fetch_quotes_yf"
    ) as mock_yf:
        mock_yf.return_value = {"RELIANCE.NS": 2440.0}
        out = fetch_quotes(["RELIANCE.NS"], upstox_client=client)
    assert out["RELIANCE.NS"] == 2440.0


# ---- status helper ----

def test_status_reports_yfinance_when_no_broker():
    s = status(None)
    assert s["source"] == "yfinance"


def test_status_reports_yfinance_when_broker_disconnected():
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    s = status(client)
    assert s["source"] == "yfinance"


def test_status_reports_upstox_when_connected():
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    client.access_token = "T"
    s = status(client)
    assert s["source"] == "upstox"
