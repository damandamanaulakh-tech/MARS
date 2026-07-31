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
    """Now BATCHED: one get_ltp call for all resolved tickers (2026-07-31)."""
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    client.access_token = "T"

    with patch(
        "trading.quote_router.to_instrument_keys",
        return_value={"RELIANCE.NS": "NSE_EQ|INE002A01018"},
    ), patch.object(client, "get_ltp") as mock_ltp:
        mock_ltp.return_value = {"NSE_EQ|INE002A01018": 2475.5}
        out = fetch_quotes(["RELIANCE.NS"], upstox_client=client)
    assert out["RELIANCE.NS"] == 2475.5
    mock_ltp.assert_called_once_with(["NSE_EQ|INE002A01018"])


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
    ), patch.object(client, "get_ltp") as mock_ltp, patch(
        "trading.quote_router.fetch_quotes_yf"
    ) as mock_yf:
        # the batch resolves Reliance only -> TCS goes to the fallback
        mock_ltp.return_value = {"NSE_EQ|INE002A01018": 2475.5}
        mock_yf.return_value = {"TCS.NS": 3300.0}
        out = fetch_quotes(["RELIANCE.NS", "TCS.NS"], upstox_client=client)

    assert out["RELIANCE.NS"] == 2475.5
    assert out["TCS.NS"] == 3300.0
    mock_yf.assert_called_once_with(["TCS.NS"])


def test_no_last_price_is_not_substituted_with_a_previous_close():
    """DELIBERATE CHANGE 2026-07-31. The old full-quote path filled a missing
    last_price with ohlc.close — i.e. it showed YESTERDAY'S CLOSE as the live
    price. That silent substitution is exactly the fault the owner caught, so
    the LTP path does not do it: no last price means no price, and the UI shows
    a dash or an ENTRY tag instead of a number that looks current."""
    client = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    client.access_token = "T"

    with patch(
        "trading.quote_router.to_instrument_keys",
        return_value={"RELIANCE.NS": "NSE_EQ|INE002A01018"},
    ), patch.object(client, "get_ltp", return_value={}), patch(
        "trading.quote_router.fetch_quotes_yf", return_value={}
    ):
        out = fetch_quotes(["RELIANCE.NS"], upstox_client=client)
    assert out == {}, "a stale close was substituted for a live price"


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
    ), patch.object(client, "get_ltp", side_effect=RuntimeError("network")), patch(
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
