"""Walk-forward regression guard.

Asserts the backtest result-compiler produces correct numbers for a
hand-crafted trade set. If a future scoring/risk change accidentally
breaks the math, this test catches it.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from trading.backtest import _compile_result
from trading.config import Config
from trading.paper import ClosedTrade, Portfolio


def _trade(net_pnl: float, entry=100.0, exit=108.0, day=1, qty=100, sector="IT") -> ClosedTrade:
    return ClosedTrade(
        ticker=f"T{day}.NS", qty=qty,
        entry_date=datetime(2024, 1, day),
        exit_date=datetime(2024, 1, day + 7),
        entry_price=entry, exit_price=exit,
        gross_pnl=net_pnl + 50, costs=50, net_pnl=net_pnl,
        reason="TAKE_PROFIT" if net_pnl > 0 else "STOP_LOSS",
        sector=sector, entry_score=72.0, reasoning="x",
    )


def _portfolio(trades) -> Portfolio:
    cfg = Config(starting_capital=10_00_000)
    p = Portfolio.fresh(cfg)
    p.trades = list(trades)
    return p


def _bench() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=60, freq="D")
    return pd.DataFrame({
        "Close": pd.Series([100 + i * 0.1 for i in range(60)], index=idx),
    })


def test_all_winners_compile_to_positive_net_pnl():
    trades = [_trade(750.0, day=i + 1) for i in range(10)]
    p = _portfolio(trades)
    equity_curve = [("2024-01-01", 10_00_000), ("2024-02-10", 10_07_500)]

    r = _compile_result(
        cfg=p.cfg, start_date=date(2024, 1, 1), end_date=date(2024, 2, 10),
        universe_size=10, benchmark="^NSEI", portfolio=p,
        equity_curve=equity_curve, bench_hist=_bench(),
        time_in_market_days=40, total_days=40, warnings=[],
        final_equity=10_07_500,
    )
    assert r.num_trades == 10
    assert r.win_rate_pct == pytest.approx(100.0)
    assert r.final_equity == 10_07_500
    assert r.total_return_pct > 0
    assert r.max_drawdown_pct <= 0  # max DD is always 0 or negative


def test_all_losers_compile_to_negative_net_pnl():
    trades = [_trade(-550.0, day=i + 1) for i in range(5)]
    p = _portfolio(trades)
    equity_curve = [("2024-01-01", 10_00_000), ("2024-02-10", 9_97_250)]

    r = _compile_result(
        cfg=p.cfg, start_date=date(2024, 1, 1), end_date=date(2024, 2, 10),
        universe_size=5, benchmark="^NSEI", portfolio=p,
        equity_curve=equity_curve, bench_hist=_bench(),
        time_in_market_days=40, total_days=40, warnings=[],
        final_equity=9_97_250,
    )
    assert r.win_rate_pct == 0.0
    assert r.total_return_pct < 0
    assert r.num_trades == 5


def test_mixed_pnl_sector_bucketing_works():
    """Per-sector P&L bucketing must aggregate correctly."""
    trades = [
        _trade(1000.0, day=1, sector="IT"),
        _trade(1500.0, day=2, sector="IT"),
        _trade(-500.0, day=3, sector="Metal"),
        _trade(-300.0, day=4, sector="Metal"),
        _trade(2000.0, day=5, sector="Banks"),
    ]
    p = _portfolio(trades)
    equity_curve = [("2024-01-01", 10_00_000), ("2024-02-10", 10_03_700)]

    r = _compile_result(
        cfg=p.cfg, start_date=date(2024, 1, 1), end_date=date(2024, 2, 10),
        universe_size=10, benchmark="^NSEI", portfolio=p,
        equity_curve=equity_curve, bench_hist=_bench(),
        time_in_market_days=40, total_days=40, warnings=[],
        final_equity=10_03_700,
    )
    assert r.per_sector_pnl["IT"] == pytest.approx(2500.0)
    assert r.per_sector_pnl["Metal"] == pytest.approx(-800.0)
    assert r.per_sector_pnl["Banks"] == pytest.approx(2000.0)
    # win rate = 3 winners / 5 total = 60%
    assert r.win_rate_pct == pytest.approx(60.0)


def test_max_drawdown_computed_from_equity_curve():
    """Drawdown math: 10L -> 12L -> 9L should give -25% max DD."""
    trades = []
    p = _portfolio(trades)
    equity_curve = [
        ("2024-01-01", 10_00_000),
        ("2024-02-01", 12_00_000),
        ("2024-03-01", 9_00_000),
    ]

    r = _compile_result(
        cfg=p.cfg, start_date=date(2024, 1, 1), end_date=date(2024, 3, 1),
        universe_size=0, benchmark="^NSEI", portfolio=p,
        equity_curve=equity_curve, bench_hist=_bench(),
        time_in_market_days=0, total_days=60, warnings=[],
        final_equity=9_00_000,
    )
    assert r.max_drawdown_pct == pytest.approx(-25.0)
