"""FII/DII flow tests — mocked NSE response, no real network."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading.signals.flows import (
    FlowReading, dii_net_last_n_days,
    fii_flow_score, fii_net_last_n_days, fetch_latest_flows,
)


def test_fii_flow_score_bullish_extreme():
    assert fii_flow_score(7000) == 90.0


def test_fii_flow_score_mild_bullish():
    s = fii_flow_score(2000)
    assert 60 <= s <= 75


def test_fii_flow_score_mild_bearish():
    s = fii_flow_score(-2000)
    assert 30 <= s <= 50


def test_fii_flow_score_extreme_bearish():
    assert fii_flow_score(-7000) == 10.0


def test_fii_flow_score_none_returns_neutral():
    assert fii_flow_score(None) == 50.0


def test_fii_net_last_n_days_sums():
    flows = [
        FlowReading(date(2026, 5, 30), 10_000, 5_000, 5_000, 8_000, 4_000, 4_000),
        FlowReading(date(2026, 5, 29), 8_000, 7_000, 1_000, 9_000, 5_000, 4_000),
        FlowReading(date(2026, 5, 28), 12_000, 6_000, 6_000, 7_000, 6_000, 1_000),
    ]
    assert fii_net_last_n_days(flows, n=3) == 12_000
    assert fii_net_last_n_days(flows, n=2) == 6_000


def test_dii_net_last_n_days_sums():
    flows = [
        FlowReading(date(2026, 5, 30), 10_000, 5_000, 5_000, 8_000, 4_000, 4_000),
        FlowReading(date(2026, 5, 29), 8_000, 7_000, 1_000, 9_000, 5_000, 4_000),
    ]
    assert dii_net_last_n_days(flows, n=2) == 8_000


def test_net_returns_none_for_empty():
    assert fii_net_last_n_days(None) is None
    assert fii_net_last_n_days([]) is None
    assert dii_net_last_n_days(None) is None


def test_fetch_latest_flows_handles_network_error():
    import httpx
    with patch("trading.signals.flows.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("blocked")
        assert fetch_latest_flows() is None


def test_fetch_latest_flows_parses_nse_payload():
    fake_payload = [
        {
            "date": "30-May-2026",
            "fiibyVal": 12000.0, "fiisellVal": 8000.0,
            "diibyVal": 9000.0, "diisellVal": 5000.0,
        },
        {
            "date": "29-May-2026",
            "fiibyVal": 10000.0, "fiisellVal": 11000.0,
            "diibyVal": 8000.0, "diisellVal": 4000.0,
        },
    ]
    fake_response = MagicMock()
    fake_response.json.return_value = fake_payload
    fake_response.raise_for_status = MagicMock()

    fake_get = MagicMock(return_value=fake_response)
    fake_client = MagicMock()
    fake_client.get = fake_get
    with patch("trading.signals.flows.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value = fake_client
        out = fetch_latest_flows()

    assert out is not None
    assert len(out) == 2
    assert out[0].trade_date == date(2026, 5, 30)
    assert out[0].fii_net == 4000.0
    assert out[0].dii_net == 4000.0
