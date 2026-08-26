"""The 5 review fixes (2026-07-12): FII wiring, login health, stale guard,
festival-now bug, cost transparency."""

import pandas as pd
from datetime import date
from trading.config import Config
from trading.signals.flows import fetch_flows_from_csv, get_flows, fii_net_last_n_days


def test_fii_csv_fallback_loads_committed_data():
    flows = fetch_flows_from_csv()
    assert flows and len(flows) > 100
    assert flows[0].trade_date > flows[-1].trade_date        # newest first
    net5 = fii_net_last_n_days(flows, n=5)
    assert net5 is not None and isinstance(net5, float)


def test_get_flows_never_none_with_committed_csv():
    # live NSE 403s from cloud -> must still return the CSV
    assert get_flows() is not None


def test_regime_accepts_fii_and_flags_selling():
    from trading.regime import classify
    # below 200-DMA + heavy FII selling -> correction with FII flag in reason
    r = classify(nifty_close=95, nifty_200dma=100, pct_below_52w_high=-8,
                 vix=16, fii_5d_net=-5000.0)
    assert r[0] == "correction"
    assert "FII" in r[5]


def test_quote_status_health_flag():
    from trading.quote_router import status
    s = status(None)
    assert s["source"] == "yfinance" and s["healthy"] is False
    # 2026-08-26 wire: a tokenless day now runs on delayed public candles;
    # the message names the degradation and the way back to real-time.
    assert "delayed" in s["message"] and "Log in" in s["message"]

    class _Tok:
        def is_connected(self): return True
        def hours_until_expiry(self): return 0.0
    s2 = status(_Tok())
    assert s2["healthy"] is False and s2["reason"] == "token_expired"

    class _Ok:
        def is_connected(self): return True
        def hours_until_expiry(self): return 6.0
    s3 = status(_Ok())
    assert s3["healthy"] is True and s3["hours_left"] == 6.0


def test_festival_now_bug_fixed():
    # engine.scan defines `now` before the festival block now; import-level
    # sanity that festival_context works standalone (the block's dependency)
    from trading.calendars import festival_context
    assert festival_context(days=5, today=date(2026, 11, 6))  # Dhanteras window
