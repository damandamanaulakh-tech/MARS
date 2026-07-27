"""Owner-approved adoptions from the external review (2026-07-27):
1. Full NSE 2026 holiday calendar wired into is_trading_day.
2. Nifty 200 (^CNX200) as secondary backtest benchmark, fail-visible.
3. Truthful marketing copy (tested in web/tests/test_truthful_copy.py).
"""

from datetime import date

import pandas as pd

from trading.calendars import NSE_FIXED_HOLIDAYS, is_trading_day


# ---- 1. holiday calendar ----

def test_2026_has_the_full_16_holiday_list():
    y2026 = [d for d in NSE_FIXED_HOLIDAYS if d.year == 2026]
    assert len(y2026) == 16
    assert date(2026, 3, 3) in y2026      # Holi — lunar, was missing before
    assert date(2026, 11, 10) in y2026    # Diwali Balipratipada
    assert date(2026, 1, 15) in y2026     # Maharashtra election holiday


def test_scheduler_gate_skips_diwali_but_trades_next_day():
    assert not is_trading_day(date(2026, 11, 10))   # Diwali — closed
    assert is_trading_day(date(2026, 11, 11))       # Wednesday — open
    assert not is_trading_day(date(2026, 11, 14))   # Saturday — weekend


def test_2027_keeps_failsafe_fixed_dates_only():
    """Lunar 2027 dates aren't published yet — only the 4 fixed ones, and a
    missed holiday fails safe via the stale-data guard (no fresh candle)."""
    y2027 = [d for d in NSE_FIXED_HOLIDAYS if d.year == 2027]
    assert y2027 == [date(2027, 1, 26), date(2027, 8, 15),
                     date(2027, 10, 2), date(2027, 12, 25)]


# ---- 2. Nifty 200 secondary benchmark ----

def _frame(closes, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame({
        "Open": closes, "High": closes, "Low": closes,
        "Close": closes, "Volume": [1e6] * len(closes),
    }, index=idx)


def _compile(bench2_hist):
    from trading.backtest import _compile_result
    from trading.config import Config
    from trading.paper import Portfolio

    cfg = Config()
    return _compile_result(
        cfg=cfg, start_date="2024-01-01", end_date="2024-01-10",
        universe_size=1, benchmark="^NSEI",
        portfolio=Portfolio(cfg=cfg, cash=cfg.starting_capital),
        equity_curve=[("2024-01-01", cfg.starting_capital)],
        bench_hist=_frame([100.0] * 10),
        bench2_hist=bench2_hist,
        time_in_market_days=0, total_days=10,
        warnings=[], final_equity=cfg.starting_capital,
    )


def test_nifty200_benchmark_computed_when_series_present():
    r = _compile(_frame([100.0, 101, 102, 103, 104, 105, 106, 107, 108, 110]))
    assert r.benchmark2 == "^CNX200"
    assert abs(r.benchmark2_total_return_pct - 10.0) < 1e-6
    assert abs(r.alpha2_pct - (r.total_return_pct - 10.0)) < 1e-6
    assert "Alpha vs Nifty 200" in r.summary_markdown()


def test_nifty200_gap_is_visible_not_silent():
    """No series -> None + an explicit DATA_NEEDED warning, never a quiet drop."""
    r = _compile(None)
    assert r.benchmark2_total_return_pct is None
    assert r.alpha2_pct is None
    assert any("DATA_NEEDED" in w and "^CNX200" in w for w in r.warnings)
    assert "Alpha vs Nifty 200" not in r.summary_markdown()
