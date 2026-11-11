"""AUD-002: missing quotes must not erase previously observed market value."""
from copy import deepcopy
from datetime import datetime, timedelta

import pandas as pd
import pytest

from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio
from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine


NOW = datetime(2026, 11, 11, 6)


@pytest.fixture
def book(tmp_path, monkeypatch):
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.cfg.min_score_to_enter = 101
    e.portfolio.positions.clear()
    e.portfolio.cash = 3_900_000.
    for ticker, qty in (("AAA.NS", 10_000), ("BBB.NS", 1_000)):
        e.portfolio.positions[ticker] = PaperPosition(
            ticker=ticker, qty=qty, entry_price=100.,
            entry_date=NOW-timedelta(days=2), sector="Test", peak_price=100.,
            entry_score=75., reasoning="fixture", mode="nifty200_momentum",
            stop_price=50., target_price=1000.)
    p = e.portfolio
    p.risk_day = NOW.date().isoformat()
    p.risk_day_equity = p.dd_peak_equity = 5_200_000.
    p.equity_history = [((NOW-timedelta(days=1)).isoformat(), 5_200_000.)]

    class Clock(datetime):
        @classmethod
        def utcnow(cls):
            return NOW

    monkeypatch.setattr("trading.engine.datetime", Clock)
    monkeypatch.setattr(e, "_absorb_corporate_actions", lambda *a: None)
    monkeypatch.setattr(e, "_danger_pass", lambda *a: None)
    monkeypatch.setattr(e, "_valuation_pass", lambda *a, **k: None)
    monkeypatch.setattr("trading.signals.flows.get_flows", lambda: pd.DataFrame())
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda *a, **k: {"AAA.NS": 100.})
    return e


def remember_full_book(e):
    return e._freshest_marks({"AAA.NS": 100., "BBB.NS": 300.}, {})


def test_scan_partial_quote_keeps_52_lakh_and_does_not_halt(book):
    remember_full_book(book)
    book.scan()
    assert book.portfolio.equity_history[-1][1] == pytest.approx(5_200_000.)
    assert book.risk.kill_switch is False
    assert book.portfolio.risk_day_killed is False
    assert book.drawdown_gov.last_equity == pytest.approx(5_200_000.)


def test_refresh_partial_quote_keeps_winner_and_never_fills_on_carried_mark(book):
    remember_full_book(book)
    # A carried price above the target must still never become an exit quote.
    book.portfolio.positions["BBB.NS"].target_price = 200.
    result = book.refresh()
    assert result["equity"] == pytest.approx(5_200_000.)
    assert not book.risk.kill_switch
    assert "BBB.NS" in book.portfolio.positions
    assert not book.portfolio.trades


def test_real_loss_in_quoted_holding_still_halts_with_other_mark_carried(book, monkeypatch):
    remember_full_book(book)
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda *a, **k: {"AAA.NS": 80.})
    book.refresh()
    assert book.risk.kill_switch
    assert book.portfolio.risk_day_killed
    assert [t.ticker for t in book.portfolio.trades] == ["AAA.NS"]
    assert "BBB.NS" in book.portfolio.positions


def test_older_daily_close_cannot_replace_a_remembered_quote(book):
    remember_full_book(book)
    history = {"BBB.NS": pd.DataFrame({"Close": [150.]},
               index=pd.DatetimeIndex([NOW-timedelta(days=1)]))}
    marks = book._freshest_marks({"AAA.NS": 100.}, history)
    assert marks["BBB.NS"] == 300.


@pytest.mark.parametrize("bad", [0., -1., float("nan"), float("inf"), "bad", True])
def test_invalid_quote_preserves_last_good_value(book, bad):
    remember_full_book(book)
    marks = book._freshest_marks({"BBB.NS": bad}, {})
    assert marks["BBB.NS"] == 300.
    assert book.portfolio.equity({"AAA.NS": 100., "BBB.NS": bad}) == 5_200_000.


def test_complete_quote_outage_returns_last_known_value(book, monkeypatch):
    remember_full_book(book)
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", lambda *a, **k: {})
    assert book.refresh()["equity"] == 5_200_000.
    assert not book.portfolio.trades


def test_marks_survive_json_restart_and_keep_original_observation_time(book):
    remember_full_book(book)
    saved = deepcopy(book.portfolio.positions["BBB.NS"].last_mark)
    book.portfolio.save()
    restored = Portfolio.load_or_fresh(book.cfg)
    fresh = TradingEngine(book.cfg, restored)
    fresh._freshest_marks({"AAA.NS": 100.}, {})
    assert restored.equity({}) == 5_200_000.
    assert restored.positions["BBB.NS"].last_mark == saved
    assert saved["source"] == "quote_response"
    assert saved["provider_time"] is None
    assert saved["received_at"]


def test_unknown_legacy_mark_does_not_trip_loss_or_publish_false_equity(book):
    before = list(book.portfolio.equity_history)
    book.scan()
    assert not book.risk.kill_switch
    assert not book.portfolio.risk_day_killed
    assert book.portfolio.equity_history == before
    assert any(j["action"] == "PORTFOLIO_MARKS_INCOMPLETE"
               for j in book.portfolio.decision_journal)


def test_unknown_book_value_blocks_new_scan_entries_with_an_explicit_reason(book):
    book.cfg.min_score_to_enter = 62
    book.scan()
    assert not book.portfolio.last_scan_opened
    assert any(s["reason"] == "portfolio_marks_incomplete"
               for s in book.portfolio.last_scan_skipped)


def test_complete_recovered_quotes_remove_the_data_gap_but_not_an_existing_halt(book, monkeypatch):
    book.portfolio.risk_day_killed = True
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda *a, **k: {"AAA.NS": 100., "BBB.NS": 300.})
    book.scan()
    assert book.risk.kill_switch
    assert book.portfolio.equity_history[-1][1] == 5_200_000.


def test_reopened_ticker_does_not_inherit_old_mark(book):
    remember_full_book(book)
    old = book.portfolio.positions.pop("BBB.NS")
    book.portfolio.positions["BBB.NS"] = PaperPosition(
        ticker=old.ticker, qty=1000, entry_price=90., entry_date=NOW,
        sector="Test", peak_price=90., entry_score=75., reasoning="new holding")
    assert book.portfolio.equity({"AAA.NS": 100.}) == 4_990_000.


def test_unknown_mark_does_not_disable_a_quoted_stop(book, monkeypatch):
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda *a, **k: {"AAA.NS": 40.})
    result = book.refresh()
    assert not book.risk.kill_switch
    assert [(t.ticker, t.reason) for t in book.portfolio.trades] == [("AAA.NS", "STOP_LOSS")]
    assert result["valuation_complete"] is False
    assert result["valuation_missing"] == ["BBB.NS"]


def test_missing_marks_never_rearm_a_completed_drawdown_cut(book):
    book.portfolio.dd_cut_level = "cut_half"
    book._update_drawdown({"AAA.NS": 100.}, NOW, cut_allowed=True)
    assert book.portfolio.dd_cut_level == "cut_half"
    assert not book.portfolio.trades
    assert not book.portfolio.dd_cut_pending


def test_previously_authorized_cut_still_uses_available_quotes_during_a_gap(book):
    p = book.portfolio
    p.dd_cut_pending = {
        "level": "cut_half", "cut_fraction": .5, "started_at": NOW.isoformat(),
        "remaining_count": 1,
        "eligible": {t: pos.entry_date.isoformat() for t, pos in p.positions.items()},
    }
    book._update_drawdown({"AAA.NS": 100.}, NOW, cut_allowed=True)
    assert [t.ticker for t in p.trades] == ["AAA.NS"]
    assert "BBB.NS" in p.positions
    assert not p.dd_cut_pending
    assert p.dd_cut_level == "cut_half"


def test_newer_daily_session_replaces_older_quote_without_inventing_quote_time(book):
    remember_full_book(book)
    record = book.portfolio.positions["BBB.NS"].last_mark
    record["received_at"] = "2026-11-10T06:00:00+00:00"
    frame = pd.DataFrame({"Close": [280.]}, index=pd.DatetimeIndex([NOW.date()]))
    marks = book._freshest_marks({}, {"BBB.NS": frame})
    assert marks["BBB.NS"] == 280.
    record = book.portfolio.positions["BBB.NS"].last_mark
    assert record["asof"] == "2026-11-11"
    assert record["source"] == "stored_close"
    assert record["provider_time"] is None


def test_split_keeps_retained_market_value_and_original_receipt(book):
    from trading.corporate_actions import apply_to_position
    remember_full_book(book)
    pos = book.portfolio.positions["BBB.NS"]
    received = pos.last_mark["received_at"]
    apply_to_position(pos, {"symbol": "BBB", "ex_date": "2026-11-11",
                            "ratio": 2., "kind": "split", "subject": "split fixture"})
    assert pos.qty == 2000
    assert pos.last_mark["price"] == 150.
    assert pos.last_mark["received_at"] == received
    assert book.portfolio.equity({}) == 5_200_000.


def test_pre_split_history_cannot_restore_the_unadjusted_price(book):
    from trading.corporate_actions import apply_to_position
    remember_full_book(book)
    pos = book.portfolio.positions["BBB.NS"]
    pos.last_mark.update(source="stored_close", asof="2026-11-10")
    apply_to_position(pos, {"symbol": "BBB", "ex_date": "2026-11-11",
                            "ratio": 2., "kind": "split", "subject": "split fixture"})
    old = pd.DataFrame({"Close": [300.]}, index=pd.DatetimeIndex(["2026-11-10"]))
    book._freshest_marks({}, {"BBB.NS": old})
    assert pos.last_mark["price"] == 150.
    assert book.portfolio.equity({}) == 5_200_000.


@pytest.mark.parametrize("bad", [None, [], "bad", {"price": float("nan")}, {"price": -1}])
def test_malformed_saved_mark_does_not_corrupt_the_holding(book, bad):
    raw = book.portfolio.positions["BBB.NS"].to_dict()
    raw["last_mark"] = bad
    pos = PaperPosition.from_dict(raw)
    from trading.portfolio_marks import valuation_price
    assert valuation_price(pos) == 100.
