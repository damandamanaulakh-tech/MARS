"""Node 3 (owner order 2026-08-23): the FII tide stays populated between
repo commits — NSDL weekly tail into Mongo, merged into get_flows.

What these tests pin: the parse handles NSDL's quirks (parenthesised
negatives, the cumulative-table trap), the report→trading-date shift
matches the backfill script's rule, the three-source merge keeps its
precedence (live NSE beats the store beats the CSV, per date), and every
failure path reads as 'no data', never as zero flow.
"""
from __future__ import annotations

from datetime import date

import pytest

from trading.signals import fii_nsdl as N
from trading.signals import flows as FL


# ---------------------------------------------------------------------------
# fii_nsdl parsing + shift
# ---------------------------------------------------------------------------

def test_num_reads_parenthesised_negatives():
    assert N._num("(6,984.73)") == -6984.73
    assert N._num("1,234.50") == 1234.5


_HTML = """
<table>
<tr><td>07-Aug-2026</td></tr>
<tr><td>Equity</td></tr>
<tr><td>Stock Exchange</td><td>10,000.00</td><td>9,582.00</td><td>418.00</td></tr>
<tr><td>Debt</td></tr>
<tr><td>Stock Exchange</td><td>1.00</td><td>2.00</td><td>(1.00)</td></tr>
<tr><td>07-Aug-2026</td></tr>
<tr><td>Equity</td></tr>
<tr><td>Stock Exchange</td><td>99.00</td><td>2,79,099.00</td><td>(2,79,000.00)</td></tr>
</table>
"""


def test_parse_takes_the_first_equity_row_never_the_cumulative():
    """The 2026-08-09 trap: the page repeats each date in a cumulative
    table; last-match once wrote -2.79 lakh Cr over a +418 Cr day."""
    out = N._parse(_HTML)
    assert out[date(2026, 8, 7)] == (10000.0, 9582.0, 418.0)


def test_shift_moves_each_value_to_the_prior_report_date():
    rows = [("2026-08-05", 1.0, 2.0, -1.0),
            ("2026-08-06", 3.0, 1.0, 2.0),
            ("2026-08-07", 5.0, 1.0, 4.0)]
    s = N.shift_to_trading_dates(rows)
    # value reported on the 6th belongs to the 5th, on the 7th to the 6th
    assert s[1] == ("2026-08-05", 3.0, 1.0, 2.0)
    assert s[2] == ("2026-08-06", 5.0, 1.0, 4.0)
    # the window's first value falls back to the previous weekday
    assert s[0][0] == "2026-08-04"


def test_shift_first_row_skips_weekends():
    s = N.shift_to_trading_dates([("2026-08-10", 1.0, 1.0, 0.5)])  # a Monday
    assert s[0][0] == "2026-08-07"                                 # Friday


# ---------------------------------------------------------------------------
# flows merge
# ---------------------------------------------------------------------------

@pytest.fixture()
def _sources(monkeypatch):
    def fr(d, net, buy=0.0, sell=0.0, dii=0.0):
        return FL.FlowReading(trade_date=d, fii_buy=buy, fii_sell=sell,
                              fii_net=net, dii_buy=0.0, dii_sell=0.0,
                              dii_net=dii)
    csv_rows = [fr(date(2026, 8, 18), 100.0), fr(date(2026, 8, 19), 200.0)]
    store_rows = [fr(date(2026, 8, 19), 250.0), fr(date(2026, 8, 20), 300.0)]
    live_rows = [fr(date(2026, 8, 21), -50.0, buy=9000.0, sell=9050.0,
                    dii=400.0)]
    monkeypatch.setattr(FL, "fetch_flows_from_csv", lambda: list(csv_rows))
    monkeypatch.setattr(FL, "fetch_flows_from_store", lambda: list(store_rows))
    monkeypatch.setattr(FL, "fetch_latest_flows", lambda: list(live_rows))
    return csv_rows, store_rows, live_rows


def test_merge_unions_all_three_sources_by_date(_sources, monkeypatch):
    monkeypatch.setattr(FL, "flows_age_days", lambda f: 1)
    flows = FL.get_flows()
    by = {f.trade_date: f for f in flows}
    assert len(flows) == 4                       # 18, 19, 20, 21
    assert by[date(2026, 8, 19)].fii_net == 250.0   # store beats CSV
    assert by[date(2026, 8, 21)].fii_net == -50.0   # live tops the stack
    # newest first — the 5-day sum reads a real window again
    assert FL.fii_net_last_n_days(flows, 3) == 250.0 + 300.0 - 50.0


def test_staleness_guard_still_rules_the_merged_series(_sources, monkeypatch):
    monkeypatch.setattr(FL, "flows_age_days", lambda f: 90)
    assert FL.get_flows() is None                # old data = no signal


def test_absent_store_changes_nothing(monkeypatch, _sources):
    monkeypatch.setattr(FL, "fetch_flows_from_store", lambda: None)
    monkeypatch.setattr(FL, "flows_age_days", lambda f: 1)
    flows = FL.get_flows()
    by = {f.trade_date: f for f in flows}
    assert by[date(2026, 8, 19)].fii_net == 200.0   # CSV value stands


def test_store_reader_zero_rows_and_holes_never_become_flow():
    FL.set_store_reader(lambda since: [
        {"date": "2026-08-19", "fii_buy": 0.0, "fii_sell": 0.0, "fii_net": 0.0},
        {"date": "bad-date", "fii_net": 5.0},
        {"date": "2026-08-20", "fii_buy": 9000.0, "fii_sell": 8700.0,
         "fii_net": 300.0},
    ])
    try:
        out = FL.fetch_flows_from_store()
        assert len(out) == 1 and out[0].fii_net == 300.0
    finally:
        FL.set_store_reader(None)


def test_no_reader_attached_reads_as_no_store():
    FL.set_store_reader(None)
    assert FL.fetch_flows_from_store() is None


def test_weekly_job_journals_success_and_failure(monkeypatch):
    from web import db, scheduler, state
    p = state.portfolio()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "save_fii_rows", lambda rows: len(rows))
    monkeypatch.setattr("trading.signals.fii_nsdl.recent_rows",
                        lambda months=2: [("2026-08-20", 1.0, 2.0, -1.0),
                                          ("2026-08-21", 3.0, 1.0, 2.0)])
    scheduler._weekly_fii_refresh()
    assert any(e.get("action") == "DATA_REFRESH_FII"
               for e in p.decision_journal[-3:])
    monkeypatch.setattr("trading.signals.fii_nsdl.recent_rows",
                        lambda months=2: None)
    scheduler._weekly_fii_refresh()
    assert any(e.get("action") == "DATA_REFRESH_FII_FAILED"
               for e in p.decision_journal[-3:])
