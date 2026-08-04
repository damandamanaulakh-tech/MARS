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


def _mock_nse(payload):
    """Run fetch_latest_flows() against a canned NSE response."""
    fake_response = MagicMock()
    fake_response.json.return_value = payload
    fake_response.raise_for_status = MagicMock()
    fake_client = MagicMock()
    fake_client.get = MagicMock(return_value=fake_response)
    with patch("trading.signals.flows.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value = fake_client
        return fetch_latest_flows()


def test_fetch_latest_flows_parses_the_real_nse_payload():
    """FIXED 2026-07-31. The endpoint returns ONE ROW PER CATEGORY with
    string values under buyValue/sellValue — not the fiibyVal/diibyVal keys
    the parser used to look for. This is the shape the live feed actually
    returns (captured from api.nseindia.com/api/fiidiiTradeReact)."""
    out = _mock_nse([
        {"category": "DII", "date": "30-May-2026",
         "buyValue": "9,000.00", "sellValue": "5000.00", "netValue": "4000.00"},
        {"category": "FII/FPI", "date": "30-May-2026",
         "buyValue": "12000.00", "sellValue": "8000.00", "netValue": "4000.00"},
        {"category": "FII/FPI", "date": "29-May-2026",
         "buyValue": "10000.00", "sellValue": "11000.00", "netValue": "-1000.00"},
        {"category": "DII", "date": "29-May-2026",
         "buyValue": "8000.00", "sellValue": "4000.00", "netValue": "4000.00"},
    ])
    assert out is not None
    assert len(out) == 2
    assert out[0].trade_date == date(2026, 5, 30)      # newest first
    assert out[0].fii_net == 4000.0
    assert out[0].dii_net == 4000.0
    assert out[1].fii_net == -1000.0


def test_all_zero_rows_are_rejected_not_scored():
    """THE BUG: every key lookup missed, so every value defaulted to 0 and the
    parser emitted zero-net readings DATED TODAY. They passed every freshness
    check and scored a fabricated 40 (mildly bearish) every single day."""
    out = _mock_nse([
        {"category": "FII/FPI", "date": "30-May-2026",
         "buyValue": "0", "sellValue": "0", "netValue": "0"},
        {"category": "DII", "date": "30-May-2026",
         "buyValue": "0", "sellValue": "0", "netValue": "0"},
    ])
    assert out is None, "a zero row is a parse miss, not a flat market"


def test_unparseable_rows_are_dropped_and_good_ones_kept():
    out = _mock_nse([
        {"category": "FII/FPI", "date": "not-a-date",
         "buyValue": "1", "sellValue": "2"},
        {"category": "FII/FPI", "date": "30-May-2026", "buyValue": "x",
         "sellValue": "2"},
        {"category": "FII/FPI", "date": "28-May-2026",
         "buyValue": "6000", "sellValue": "1000"},
        "junk",
    ])
    assert out is not None and len(out) == 1
    assert out[0].trade_date == date(2026, 5, 28) and out[0].fii_net == 5000.0


def test_a_fii_only_day_is_kept_with_dii_zero():
    """The live endpoint sometimes publishes FII before DII. Keep the real
    half rather than dropping a genuine reading."""
    out = _mock_nse([{"category": "FII/FPI", "date": "30-May-2026",
                      "buyValue": "12000", "sellValue": "8000"}])
    assert out is not None and out[0].fii_net == 4000.0
    assert out[0].dii_net == 0.0


def test_flows_older_than_the_limit_are_no_signal():
    """Staleness guard. The committed CSV ends 2025-03-29 and SEBI publishes
    months late, so without this the engine scored today's market on flows
    over a year old and called it a live factor."""
    from trading.signals.flows import MAX_FLOW_AGE_DAYS, flows_age_days, get_flows
    stale = [FlowReading(date(2025, 3, 29), 1.0, 0.0, 1.0, 0.0, 0.0, 0.0)]
    assert flows_age_days(stale) > MAX_FLOW_AGE_DAYS
    with patch("trading.signals.flows.fetch_latest_flows", return_value=None), \
         patch("trading.signals.flows.fetch_flows_from_csv", return_value=stale):
        assert get_flows() is None                    # goes quiet, not bearish
        assert get_flows(max_age_days=0) == stale     # research opt-out


def test_fresh_flows_pass_the_staleness_guard():
    from trading.signals.flows import get_flows
    from datetime import timedelta
    fresh = [FlowReading(date.today() - timedelta(days=1),
                         12_000.0, 8_000.0, 4_000.0, 0.0, 0.0, 0.0)]
    with patch("trading.signals.flows.fetch_latest_flows", return_value=fresh):
        assert get_flows() == fresh


def test_flows_age_days_is_none_without_data():
    from trading.signals.flows import flows_age_days
    assert flows_age_days(None) is None and flows_age_days([]) is None


def test_score_normalises_a_single_day_to_a_five_day_equivalent():
    """The live endpoint returns ONE day. Scoring +6,000 Cr in a single day
    against a 5-day threshold read a record buy day as an ordinary week."""
    assert fii_flow_score(6000.0, days=5) == 90.0
    assert fii_flow_score(1200.0, days=1) == 90.0     # 1,200/day = 6,000/5d
    assert fii_flow_score(1200.0, days=5) < 90.0
    assert fii_flow_score(-1200.0, days=1) == 10.0
    assert fii_flow_score(None, days=1) == 50.0


def test_committed_csv_extends_to_march_2025_with_blank_dii():
    """F6 refresh (owner 2026-07-29: the monthly release zips ARE FII/DII).
    Rows after 2024-12 derive from the SEBI FII disclosure archive via
    scripts/build_fii_cash_from_disclosures.py — FII only, DII left blank
    (read as 0.0), never invented. The old 2024 rows are untouched."""
    from trading.signals.flows import fetch_flows_from_csv
    flows = fetch_flows_from_csv()
    assert flows is not None
    newest = flows[0]
    assert newest.trade_date >= date(2025, 3, 1)      # was stuck at 2024-09-06
    assert newest.dii_net == 0.0                       # blank parses, row kept
    # the record Jan-2025 FPI exodus must be visible in the data
    jan = [f.fii_net for f in flows
           if f.trade_date.year == 2025 and f.trade_date.month == 1]
    assert len(jan) >= 20 and sum(jan) < -70_000
    # original hand-fed rows survive the merge untouched
    sep6 = [f for f in flows if f.trade_date == date(2024, 9, 6)]
    assert sep6 and abs(sep6[0].fii_net - (-772.94)) < 0.01
    assert abs(sep6[0].dii_net - (-90.28)) < 0.01
