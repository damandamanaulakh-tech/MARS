"""Owner request: realized win/loss figures and time frame for every segment."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from web.trade_performance import segment_performance


NOW = datetime(2026, 11, 3, 6, tzinfo=timezone.utc)


def trade(mode="nifty200_momentum", net=100, exit_date=None, **kw):
    return SimpleNamespace(
        mode=mode, net_pnl=net,
        exit_date=exit_date or datetime(2026, 11, 2, 6),
        entry_date=kw.pop("entry_date", datetime(2026, 10, 30, 6)),
        tags=kw.pop("tags", []), reasoning=kw.pop("reasoning", ""), **kw,
    )


def row(report, category):
    return next(r for r in report["rows"] if r["category"] == category)


def test_counts_every_realized_outcome_in_its_segment_and_reconciles():
    report = segment_performance([
        trade(net=100), trade(net=-60), trade(net=0),
        trade("swing", tags=["owner_pick"], net=20, reason="OWNER_SELL"),
        trade("short", net=-3), trade("positional", net=4),
        trade("intraday", net=-1), trade(tags=["ocean"], net=2),
    ], now=NOW)
    momentum = row(report, "nifty200_momentum")
    assert (momentum["total"], momentum["wins"], momentum["losses"], momentum["breakevens"]) == (3, 1, 1, 1)
    assert momentum["win_pct"] == pytest.approx(100 / 3)
    assert momentum["loss_pct"] == pytest.approx(100 / 3)
    assert momentum["breakeven_pct"] == pytest.approx(100 / 3)
    assert momentum["net_pnl"] == 40
    assert row(report, "swing")["wins"] == 1
    assert row(report, "swing")["win_pct"] == 100
    assert row(report, "ocean")["wins"] == 1
    assert report["total"] == sum(r["total"] for r in report["rows"]) == 8
    assert all(r["category"] != "owner" for r in report["rows"])


def test_legacy_evidence_is_used_but_unknown_history_is_not_guessed():
    report = segment_performance([
        trade(mode="", tags=["owner_pick"]),
        trade(mode="", reasoning="MOM 72 + QUALITY 60"),
        trade(mode="", reasoning="unplaced old composite trade", net=-9),
        trade(mode="invalid_mode"),
    ], now=NOW)
    assert row(report, "nifty200_momentum")["total"] == 2
    assert row(report, "unknown")["total"] == 2
    assert row(report, "unknown")["loss_pct"] == 50


def test_empty_segments_have_no_rate_or_invented_date():
    report = segment_performance([], now=NOW)
    assert len(report["rows"]) == 6
    for r in report["rows"]:
        assert r["total"] == 0
        assert r["win_pct"] is None and r["loss_pct"] is None
        assert r["first_exit"] is None and r["last_exit"] is None
        assert r["average_hold_days"] is None
    assert report["first_exit"] is None and report["last_exit"] is None


def test_time_window_is_inclusive_ist_calendar_days_and_future_exits_are_excluded():
    # Last 30 days includes 5 Oct–3 Nov IST. Both naive UTC and aware clocks
    # must place the 4 Oct 18:30 UTC fill on the first included date.
    report = segment_performance([
        trade(exit_date=datetime(2026, 10, 4, 18, 29, 59)),
        trade(exit_date=datetime(2026, 10, 4, 18, 30)),
        trade(exit_date=datetime(2026, 10, 4, 18, 30, tzinfo=timezone.utc)),
        trade(exit_date=datetime(2026, 11, 3, 6, 0, 1)),
    ], period="30", now=NOW)
    assert report["total"] == 2
    assert report["window_start"] == "2026-10-05"
    assert report["window_end"] == "2026-11-03"
    assert row(report, "nifty200_momentum")["first_exit"] == "2026-10-05"
    assert report["future_exits"] == 1


def test_elapsed_hold_and_sample_range_are_real_dates_not_preset_claims():
    report = segment_performance([
        trade("intraday", entry_date=datetime(2026, 11, 2, 5),
              exit_date=datetime(2026, 11, 2, 7)),
        trade("intraday", entry_date=datetime(2026, 11, 3, 4),
              exit_date=datetime(2026, 11, 3, 5)),
    ], now=NOW)
    r = row(report, "intraday")
    assert r["average_hold_days"] == pytest.approx(1.5 / 24)
    assert r["first_exit"] == "2026-11-02" and r["last_exit"] == "2026-11-03"


def test_invalid_performance_records_do_not_create_a_fake_loss_or_nan():
    report = segment_performance([
        trade(net=float("nan")), trade(net=float("inf")),
        trade(exit_date="not a date"),
        trade(entry_date=None),
    ], now=NOW)
    assert report["invalid_records"] == 3
    assert report["total"] == 1
    assert row(report, "nifty200_momentum")["average_hold_days"] is None


def test_unrecognized_period_falls_back_to_all_retained():
    report = segment_performance([trade()], period="9999999", now=NOW)
    assert report["period"] == "all"
    assert report["total"] == 1


def _closed(mode, net, exit_date, ticker="SEGMENT.NS", tags=None):
    from trading.paper import ClosedTrade
    return ClosedTrade(
        ticker=ticker, qty=10, entry_date=datetime(2026, 9, 1),
        exit_date=exit_date, entry_price=100, exit_price=100 + net / 10,
        gross_pnl=net, costs=0, net_pnl=net, reason="OWNER_SELL",
        sector="IT", entry_score=70, reasoning="test", mode=mode,
        tags=tags or [],
    )


def _page(monkeypatch, trades, query=""):
    from fastapi.testclient import TestClient
    from web import auth, db, state, trade_performance as performance
    from web.main import app
    monkeypatch.setattr(state.portfolio(), "trades", trades)
    monkeypatch.setattr(db, "count_pre_mars", lambda collection: 0)
    monkeypatch.setattr(performance, "segment_performance",
                        lambda trades, period="all": segment_performance(trades, period, now=NOW))
    client = TestClient(app)
    client.post("/login", data={"email": "owner@sourceborn.in", "password": auth._SEEDED_PWD})
    response = client.get("/trades" + query)
    assert response.status_code == 200
    return response.text


def _segment_html(body, category):
    import re
    match = re.search(r'<tr[^>]*data-performance-segment="' + category + r'"[^>]*>(.*?)</tr>', body, re.S)
    assert match is not None, category
    return match.group(1)


def test_trades_page_renders_segments_owner_outcomes_and_reporting_window(monkeypatch):
    body = _page(monkeypatch, [
        _closed("swing", 100, datetime(2026, 11, 2), tags=["owner_pick"]),
        _closed("swing", -50, datetime(2026, 11, 3)),
    ])
    assert "Win / loss by segment" in body
    swing = _segment_html(body, "swing")
    assert 'data-performance-count>2<' in swing
    assert swing.count("50.0%") == 2
    assert "2026-11-02" in swing and "2026-11-03" in swing
    assert 'data-performance-count>0<' in _segment_html(body, "nifty200_momentum")
    assert 'data-performance-segment="owner"' not in body
    assert "Open positions are excluded" in body
    assert "Each partial exit" in body
    assert "Average hold" in body and "All retained history" in body


def test_period_filter_changes_segment_table_and_keeps_existing_trade_ledger(monkeypatch):
    body = _page(monkeypatch, [
        _closed("swing", 100, datetime(2026, 9, 5), ticker="OLD.NS"),
        _closed("swing", -50, datetime(2026, 11, 3), ticker="RECENT.NS"),
    ], "?performance_period=30")
    swing = _segment_html(body, "swing")
    assert 'data-performance-count>1<' in swing and "100.0%" in swing
    assert "2026-10-05 to 2026-11-03" in body
    assert "OLD.NS" in body and "RECENT.NS" in body
    assert "filters this table only" in body


def test_archive_toggle_cannot_add_archived_records_to_segment_win_rate(monkeypatch):
    from fastapi.testclient import TestClient
    from web import auth, db, state, trade_performance as performance
    from web.main import app
    live = _closed("swing", -50, datetime(2026, 11, 3))
    archive = _closed("swing", 100, datetime(2026, 9, 5), "ARCHIVED.NS").to_dict()
    archive[db.PRE_MARS_FLAG] = True
    monkeypatch.setattr(state.portfolio(), "trades", [live])
    monkeypatch.setattr(db, "count_pre_mars", lambda collection: 1)
    monkeypatch.setattr(db, "list_trades", lambda **kwargs: [archive])
    monkeypatch.setattr(performance, "segment_performance",
                        lambda trades, period="all": segment_performance(trades, period, now=NOW))
    client = TestClient(app)
    client.post("/login", data={"email": "owner@sourceborn.in", "password": auth._SEEDED_PWD})
    response = client.get("/trades?archive=1")
    assert response.status_code == 200
    body = response.text
    assert "ARCHIVED.NS" in body
    swing = _segment_html(body, "swing")
    assert 'data-performance-count>1<' in swing and "100.0%" in swing
    assert "archived trades are excluded even when shown below" in body
