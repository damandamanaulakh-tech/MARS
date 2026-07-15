"""Backtester tests using synthetic OHLCV (no network)."""

from datetime import datetime
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from trading.backtest import _compile_result, _slice_to_date
from trading.config import Config
from trading.paper import ClosedTrade, Portfolio


def test_slice_to_date_returns_only_past_bars():
    idx = pd.date_range("2023-01-01", periods=10, freq="D")
    df = pd.DataFrame({"Close": range(10)}, index=idx)
    cutoff = pd.Timestamp("2023-01-05")
    sub = _slice_to_date(df, cutoff)
    assert len(sub) == 5
    assert sub.index[-1] == cutoff


def test_slice_to_date_handles_empty():
    assert _slice_to_date(None, "2023-01-01") is None
    assert _slice_to_date(pd.DataFrame(), "2023-01-01") is None


def test_data_backtest_mode_blocks_earnings_fetch():
    """fetch_earnings_growth returns None in backtest mode (no lookahead)."""
    from trading import data
    data.set_backtest_mode(True)
    try:
        # Should not even attempt yfinance call
        with patch("trading.data.yf.Ticker") as mock_ticker:
            result = data.fetch_earnings_growth("RELIANCE.NS")
        assert result is None
        mock_ticker.assert_not_called()
    finally:
        data.set_backtest_mode(False)


def test_data_normal_mode_calls_yfinance():
    """fetch_earnings_growth uses yfinance when NOT in backtest mode."""
    from trading import data
    data.set_backtest_mode(False)
    fake_info = {"earningsQuarterlyGrowth": 0.15}
    with patch("trading.data.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.info = fake_info
        result = data.fetch_earnings_growth("RELIANCE.NS")
    assert result == 15.0


def test_compile_result_zero_trades():
    """A backtest with no trades shouldn't crash."""
    cfg = Config()
    cfg.starting_capital = 1_000_000

    bench_idx = pd.date_range("2024-01-01", periods=5, freq="D")
    bench_hist = pd.DataFrame({"Close": [100, 101, 102, 103, 104]}, index=bench_idx)

    portfolio = Portfolio(cfg=cfg, cash=cfg.starting_capital)
    eq_curve = [("2024-01-01", 1_000_000), ("2024-01-02", 1_000_000)]

    result = _compile_result(
        cfg=cfg,
        start_date="2024-01-01", end_date="2024-01-05",
        universe_size=10, benchmark="^NSEI",
        portfolio=portfolio,
        equity_curve=eq_curve,
        bench_hist=bench_hist,
        time_in_market_days=0, total_days=5,
        warnings=[],
        final_equity=1_000_000,
    )
    assert result.num_trades == 0
    assert result.win_rate_pct == 0
    assert result.total_return_pct == 0
    assert result.benchmark_total_return_pct == pytest.approx(4.0)


def test_compile_result_with_winning_and_losing_trade():
    cfg = Config()
    cfg.starting_capital = 1_000_000

    bench_idx = pd.date_range("2024-01-01", periods=10, freq="D")
    bench_hist = pd.DataFrame({"Close": list(range(100, 110))}, index=bench_idx)

    portfolio = Portfolio(cfg=cfg, cash=1_050_000)  # net gain
    portfolio.trades = [
        ClosedTrade(
            ticker="A.NS", qty=10,
            entry_date=datetime(2024, 1, 2), exit_date=datetime(2024, 1, 5),
            entry_price=100, exit_price=110,
            gross_pnl=100, costs=10, net_pnl=90,
            reason="TAKE_PROFIT", sector="IT",
            entry_score=75, reasoning="x",
        ),
        ClosedTrade(
            ticker="B.NS", qty=10,
            entry_date=datetime(2024, 1, 3), exit_date=datetime(2024, 1, 6),
            entry_price=200, exit_price=190,
            gross_pnl=-100, costs=10, net_pnl=-110,
            reason="STOP_LOSS", sector="Pharma",
            entry_score=72, reasoning="y",
        ),
    ]
    eq_curve = [
        (f"2024-01-{d:02d}", 1_000_000 + d * 5000)
        for d in range(1, 11)
    ]

    result = _compile_result(
        cfg=cfg,
        start_date="2024-01-01", end_date="2024-01-10",
        universe_size=10, benchmark="^NSEI",
        portfolio=portfolio,
        equity_curve=eq_curve,
        bench_hist=bench_hist,
        time_in_market_days=8, total_days=10,
        warnings=["test warning"],
        final_equity=1_050_000,
    )
    assert result.num_trades == 2
    assert result.win_rate_pct == 50.0
    assert result.total_return_pct == pytest.approx(5.0)
    assert "test warning" in result.warnings
    assert "IT" in result.per_sector_pnl
    assert "TAKE_PROFIT" in result.per_reason_pnl


def test_backtest_result_summary_markdown_doesnt_crash():
    cfg = Config()
    bench_idx = pd.date_range("2024-01-01", periods=2, freq="D")
    bench_hist = pd.DataFrame({"Close": [100, 105]}, index=bench_idx)
    portfolio = Portfolio(cfg=cfg, cash=cfg.starting_capital)

    result = _compile_result(
        cfg=cfg,
        start_date="2024-01-01", end_date="2024-01-02",
        universe_size=5, benchmark="^NSEI",
        portfolio=portfolio,
        equity_curve=[("2024-01-01", cfg.starting_capital)],
        bench_hist=bench_hist,
        time_in_market_days=0, total_days=2,
        warnings=["caveat 1", "caveat 2"],
        final_equity=cfg.starting_capital,
    )
    md = result.summary_markdown()
    assert "Backtest Report" in md
    assert "Alpha" in md
    assert "caveat 1" in md
