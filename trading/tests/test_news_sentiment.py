"""News sentiment tests — fully mocked, no network."""

from unittest.mock import MagicMock, patch

import pytest

from trading.signals.news import (
    NewsSentiment,
    _get_headlines_yfinance,
    _score_with_claude,
    fetch_sentiment,
)


def test_no_api_key_returns_neutral(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch(
        "trading.signals.news._get_headlines_yfinance",
        return_value=["Reliance announces ₹50,000 cr capex"],
    ):
        s = fetch_sentiment("RELIANCE.NS")
    assert s.score == 50.0
    assert "neutral" in s.reasoning.lower() or "unavailable" in s.reasoning.lower()


def test_no_headlines_returns_neutral():
    with patch("trading.signals.news._get_headlines_yfinance", return_value=[]):
        s = fetch_sentiment("UNKNOWN.NS")
    assert s.score == 50.0
    assert s.headlines_used == 0


def test_backtest_mode_returns_neutral():
    from trading import data
    data.set_backtest_mode(True)
    try:
        s = fetch_sentiment("RELIANCE.NS")
        assert s.score == 50.0
        assert "backtest" in s.reasoning.lower()
    finally:
        data.set_backtest_mode(False)


def test_score_with_claude_parses_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake_key")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "content": [{"text": "0.7\nStrong capex announcement is bullish."}]
    }
    with patch("trading.signals.news.httpx.post", return_value=fake_response):
        result = _score_with_claude("RELIANCE.NS", ["headline 1"])
    assert result is not None
    score, reasoning = result
    assert score == pytest.approx(0.7)
    assert "bullish" in reasoning.lower()


def test_score_with_claude_clamps_extreme_values(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake_key")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "content": [{"text": "5.0\nWildly bullish but clamped."}]
    }
    with patch("trading.signals.news.httpx.post", return_value=fake_response):
        result = _score_with_claude("X.NS", ["h"])
    assert result is not None
    score, _ = result
    assert score == 1.0   # clamped


def test_score_with_claude_handles_network_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake_key")
    import httpx
    with patch(
        "trading.signals.news.httpx.post",
        side_effect=httpx.ConnectError("offline"),
    ):
        assert _score_with_claude("X.NS", ["h"]) is None


def test_score_with_claude_handles_malformed_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake_key")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"foo": "bar"}   # no content
    with patch("trading.signals.news.httpx.post", return_value=fake_response):
        assert _score_with_claude("X.NS", ["h"]) is None


def test_fetch_sentiment_maps_compound_to_0_100(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake_key")
    with patch(
        "trading.signals.news._get_headlines_yfinance",
        return_value=["Sun Pharma USFDA approval for new drug"],
    ), patch(
        "trading.signals.news._score_with_claude",
        return_value=(0.8, "Major USFDA approval = strong positive"),
    ):
        s = fetch_sentiment("SUNPHARMA.NS")
    assert s.score == pytest.approx(90.0)   # 50 + 0.8*50
    assert s.raw_compound == 0.8
    assert s.headlines_used == 1


def test_fetch_sentiment_handles_negative_compound(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake_key")
    with patch(
        "trading.signals.news._get_headlines_yfinance",
        return_value=["Stock crashes on weak earnings"],
    ), patch(
        "trading.signals.news._score_with_claude",
        return_value=(-0.6, "Weak earnings, negative for stock"),
    ):
        s = fetch_sentiment("X.NS")
    assert s.score == pytest.approx(20.0)   # 50 + (-0.6)*50
