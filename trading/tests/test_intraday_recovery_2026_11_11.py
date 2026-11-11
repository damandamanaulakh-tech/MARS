"""SEQ-02: a missed deadline is unfinished work, never a different strategy."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.exits import retag_positions_per_mode
from trading.paper import PaperPosition, Portfolio

ENTRY = datetime(2026, 11, 11, 3, 50)  # 09:20 IST
DUE = datetime(2026, 11, 11, 9, 55)    # 15:25 IST


@pytest.fixture
def book(tmp_path, monkeypatch):
    clock = [DUE]

    class Clock(datetime):
        @classmethod
        def utcnow(cls):
            return clock[0]

    monkeypatch.setenv("SB_IGNORE_MARKET_HOURS", "0")
    monkeypatch.setattr("trading.engine.datetime", Clock)
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.disable_exits = True
    p = Portfolio.fresh(cfg)
    for t, mode in (("DAY.NS", "intraday"), ("HOLD.NS", "swing")):
        p.positions[t] = PaperPosition(ticker=t, qty=10, entry_price=100.,
            entry_date=ENTRY, sector="Test", peak_price=100., entry_score=80.,
            reasoning="fixture", mode=mode, stop_price=98.5, target_price=104.)
    e = TradingEngine(cfg, p)
    monkeypatch.setattr(e, "_absorb_corporate_actions", lambda *a: None)
    monkeypatch.setattr(e, "_danger_pass", lambda *a: None)
    monkeypatch.setattr(e, "_valuation_pass", lambda *a, **k: None)
    monkeypatch.setattr("trading.engine.fetch_daily", lambda *a, **k: {})
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", lambda *a, **k: {"DAY.NS": 101., "HOLD.NS": 100.})
    return e, clock


def test_refresh_recovers_a_missed_job_without_waiting_for_history(book, monkeypatch):
    e, _ = book
    def history(*a, **k):
        assert "DAY.NS" not in e.portfolio.positions
        return {}
    monkeypatch.setattr("trading.engine.fetch_daily", history)
    result = e.refresh()
    assert [t.reason for t in e.portfolio.trades] == ["SQUARE_OFF"]
    assert "HOLD.NS" in e.portfolio.positions
    assert "DAY.NS" in result["exits_this_refresh"]


def test_overnight_retag_preserves_mode_and_original_levels(book):
    e, _ = book
    pos = e.portfolio.positions["DAY.NS"]
    before = (pos.mode, pos.stop_price, pos.target_price)
    retag_positions_per_mode(e.portfolio, ENTRY + timedelta(days=1))
    assert (pos.mode, pos.stop_price, pos.target_price) == before
    assert not [r for r in e.portfolio.decision_journal
                if r.get("action") == "RETAG_EXITS" and r.get("ticker") == pos.ticker]


@pytest.mark.parametrize("now", [ENTRY, DUE-timedelta(seconds=1),
    datetime(2026, 11, 11, 10, 1), datetime(2026, 11, 11, 10, 0, 1),
    datetime(2026, 11, 12, 3, 40),
    datetime(2026, 11, 14, 6)])
def test_recovery_never_closes_before_deadline_or_outside_session(book, now):
    e, clock = book
    clock[0] = now
    assert e.square_off_intraday(prices={"DAY.NS": 101.}) == []
    assert "DAY.NS" in e.portfolio.positions
    assert not e.portfolio.trades


@pytest.mark.parametrize("quote", [None, 0., -1., float("nan"), float("inf"), True, "bad"])
def test_absent_or_invalid_quote_stays_pending_with_original_protection(book, quote):
    e, _ = book
    pos = e.portfolio.positions["DAY.NS"]
    assert e.square_off_intraday(prices={"DAY.NS": quote}) == []
    assert e.portfolio.positions["DAY.NS"] is pos
    assert (pos.mode, pos.stop_price, pos.target_price) == ("intraday", 98.5, 104.)
    assert pos.intraday_close["status"] == "waiting_quote"
    assert pos.intraday_close["deadline"] == "2026-11-11T09:55:00+00:00"
    assert not e.portfolio.trades


def test_quote_exception_is_saved_for_retry(book, monkeypatch):
    e, _ = book
    def failed(*a, **k):
        raise TimeoutError("quote fixture timeout")
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", failed)
    assert e.square_off_intraday() == []
    restored = Portfolio.load_or_fresh(e.cfg)
    pending = restored.positions["DAY.NS"].intraday_close
    assert pending["status"] == "waiting_quote"
    assert pending["last_error"] == "TimeoutError"
    assert pending["attempts"] == 1


def test_missing_quote_survives_restart_and_closes_once_when_quote_returns(book):
    e, clock = book
    e.square_off_intraday(prices={})
    restored = Portfolio.load_or_fresh(e.cfg)
    deadline = restored.positions["DAY.NS"].intraday_close["deadline"]
    e = TradingEngine(e.cfg, restored)
    clock[0] = datetime(2026, 11, 12, 3, 46)  # next session, 09:16 IST
    retag_positions_per_mode(restored, clock[0])
    assert e.square_off_intraday(prices={"DAY.NS": 97.}) == ["DAY.NS"]
    trade = restored.trades[-1]
    assert trade.mode == "intraday" and trade.reason == "SQUARE_OFF"
    assert trade.intraday_close["deadline"] == deadline
    assert trade.intraday_close["status"] == "closed"
    assert trade.intraday_close["attempts"] == 2
    snapshot = (restored.cash, len(restored.trades))
    assert e.square_off_intraday(prices={"DAY.NS": 97.}) == []
    assert (restored.cash, len(restored.trades)) == snapshot
    assert "HOLD.NS" in restored.positions


def test_quote_fetch_finishing_after_close_cannot_fill(book, monkeypatch):
    e, clock = book
    clock[0] = datetime(2026, 11, 11, 9, 59)
    def delayed(*a, **k):
        clock[0] = datetime(2026, 11, 11, 10, 1)
        return {"DAY.NS": 101.}
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", delayed)
    assert e.square_off_intraday() == []
    assert e.portfolio.positions["DAY.NS"].intraday_close["status"] == "waiting_market"


def test_entry_saves_deadline_and_late_entry_changes_nothing(book):
    e, clock = book
    cfg = e.mode_cfg("intraday")
    pick = {"ticker": "NEW.NS", "score": 80., "components": {}, "timing_pass": True,
            "reasoning": "fixture"}
    clock[0] = ENTRY
    e._open(deepcopy(pick), 10, 100., ENTRY, cfg=cfg)
    assert e.portfolio.positions["NEW.NS"].intraday_close["deadline"] == "2026-11-11T09:55:00+00:00"
    before = (e.portfolio.cash, len(e.portfolio.decision_journal), set(e.portfolio.positions))
    clock[0] = DUE
    pick["ticker"] = "LATE.NS"
    with pytest.raises(ValueError, match="intraday_entry_deadline"):
        e._open(pick, 10, 100., DUE, cfg=cfg)
    assert (e.portfolio.cash, len(e.portfolio.decision_journal), set(e.portfolio.positions)) == before


def test_scan_runs_deadline_recovery_before_history(book, monkeypatch):
    e, _ = book
    def history(*a, **k):
        assert "DAY.NS" not in e.portfolio.positions
        return {}
    monkeypatch.setattr("trading.engine.fetch_daily", history)
    e.scan()
    assert [t.reason for t in e.portfolio.trades] == ["SQUARE_OFF"]


def test_partial_quotes_checkpoint_success_and_keep_only_missing_work(book):
    e, _ = book
    p = e.portfolio
    p.positions["TWO.NS"] = deepcopy(p.positions["DAY.NS"])
    p.positions["TWO.NS"].ticker = "TWO.NS"
    assert e.square_off_intraday(prices={"DAY.NS": 101.}) == ["DAY.NS"]
    restored = Portfolio.load_or_fresh(e.cfg)
    assert "DAY.NS" not in restored.positions
    assert restored.positions["TWO.NS"].intraday_close["status"] == "waiting_quote"
    assert restored.trades[-1].intraday_close["status"] == "closed"


def test_one_failed_close_does_not_abandon_other_due_holdings(book, monkeypatch):
    e, _ = book
    e.portfolio.positions["TWO.NS"] = deepcopy(e.portfolio.positions["DAY.NS"])
    e.portfolio.positions["TWO.NS"].ticker = "TWO.NS"
    close = e._close
    def failed(t, *args):
        if t == "DAY.NS":
            raise RuntimeError("fixture close failed before mutation")
        return close(t, *args)
    monkeypatch.setattr(e, "_close", failed)
    assert e.square_off_intraday(prices={"DAY.NS": 101., "TWO.NS": 101.}) == ["TWO.NS"]
    assert e.portfolio.positions["DAY.NS"].intraday_close["status"] == "retry_pending"


def test_owner_levels_and_timezone_survive_deadline_enrollment(book):
    from zoneinfo import ZoneInfo
    from trading.intraday_recovery import ensure
    e, _ = book
    pos = e.portfolio.positions["DAY.NS"]
    pos.entry_date = datetime(2026, 11, 11, 9, 20, tzinfo=ZoneInfo("Asia/Kolkata"))
    pos.custom_levels, pos.stop_price, pos.target_price = True, 97.25, 106.5
    assert ensure(pos, ENTRY)
    record = deepcopy(pos.intraday_close)
    assert not ensure(pos, DUE)
    retag_positions_per_mode(e.portfolio, ENTRY+timedelta(days=1))
    assert pos.intraday_close == record
    assert (pos.stop_price, pos.target_price) == (97.25, 106.5)
    assert record["deadline"] == "2026-11-11T09:55:00+00:00"


def test_original_stop_still_executes_before_deadline(book):
    e, clock = book
    clock[0] = ENTRY + timedelta(hours=1)
    e.cfg.disable_exits = False
    # Only this quote crosses its hard stop; the deadline is still hours away.
    from unittest.mock import patch
    with patch("trading.engine.routed_fetch_quotes", return_value={"DAY.NS": 97.}):
        e.refresh()
    assert e.portfolio.trades[-1].reason == "STOP_LOSS"
    assert e.portfolio.trades[-1].intraday_close["exit_reason"] == "STOP_LOSS"


def test_scan_crossing_deadline_refuses_intraday_entry_before_rotation(tmp_path, monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)
    monkeypatch.setenv("SB_IGNORE_MARKET_HOURS", "0")
    e.cfg.mode = "intraday"
    e.cfg.apply_mode_preset()
    e.cfg.min_score_to_enter = 0.
    e.cfg.candle_confirm_enabled = False
    e.cfg.category_budgets_enabled = False
    e.portfolio.cash = 5_000_000.
    e.portfolio.positions.clear()
    clock = [ENTRY]
    class Clock(datetime):
        @classmethod
        def utcnow(cls): return clock[0]
    monkeypatch.setattr("trading.engine.datetime", Clock)
    real_history = __import__("trading.engine", fromlist=["fetch_daily"]).fetch_daily
    def slow_history(*a, **k):
        clock[0] = DUE + timedelta(seconds=30)
        return real_history(*a, **k)
    monkeypatch.setattr("trading.engine.fetch_daily", slow_history)
    monkeypatch.setattr("trading.engine.timing_ok", lambda *a, **k: (True, {}))
    monkeypatch.setattr("trading.engine.compute_score", lambda *a, **k: (90., {}))
    import pandas as pd
    monkeypatch.setattr("trading.signals.flows.get_flows", lambda: pd.DataFrame())
    seen = []
    monkeypatch.setattr(e, "_rotation_exits", lambda picks, *a, **k: seen.extend(picks) or [])
    result = e.scan()
    assert result.picks, "fixture must produce an otherwise selected candidate"
    assert not result.opened and not seen and not e.portfolio.positions
    assert any(s["reason"] == "intraday_entry_deadline" for s in result.skipped)


def test_stale_entry_clock_cannot_bypass_the_current_deadline(book):
    e, _ = book
    with pytest.raises(ValueError, match="intraday_entry_deadline"):
        e._open({"ticker": "LATE.NS"}, 10, 100., ENTRY, cfg=e.mode_cfg("intraday"))
    assert "LATE.NS" not in e.portfolio.positions


@pytest.mark.parametrize("late", [False, True])
def test_intraday_limit_fills_before_cutoff_but_waits_after_it(book, monkeypatch, late):
    from trading import orders
    import trading.engine as engine_module
    e, clock = book
    clock[0] = ENTRY
    monkeypatch.setattr(orders, "datetime", engine_module.datetime)
    order = orders.place_limit(e.portfolio, ticker="LIMIT.NS", limit_price=101.,
                               qty=10, mode="intraday", now=ENTRY)
    cash = e.portfolio.cash
    monkeypatch.setattr("trading.quote_router.fetch_quotes", lambda *a, **k: {"LIMIT.NS": 100.})
    clock[0] = DUE if late else ENTRY
    actions = orders.process_open_orders(e)
    if late:
        assert actions == [] and order["status"] == "open"
        assert "intraday_entry_deadline" in order["note"]
        assert "LIMIT.NS" not in e.portfolio.positions and e.portfolio.cash == cash
    else:
        assert actions == [order["id"]] and order["status"] == "filled"
        assert e.portfolio.positions["LIMIT.NS"].mode == "intraday"


def test_limit_quote_crossing_cutoff_cannot_open(book, monkeypatch):
    from trading import orders
    import trading.engine as engine_module
    e, clock = book
    clock[0] = DUE-timedelta(seconds=1)
    monkeypatch.setattr(orders, "datetime", engine_module.datetime)
    orders.place_limit(e.portfolio, ticker="LIMIT.NS", limit_price=101., qty=10,
                       mode="intraday", now=ENTRY)
    def delayed(*a, **k):
        clock[0] = DUE+timedelta(seconds=1)
        return {"LIMIT.NS": 100.}
    monkeypatch.setattr("trading.quote_router.fetch_quotes", delayed)
    assert orders.process_open_orders(e) == []
    assert "LIMIT.NS" not in e.portfolio.positions


def test_reopening_same_ticker_gets_a_new_deadline(book):
    e, clock = book
    e.square_off_intraday(prices={"DAY.NS": 101.})
    clock[0] = ENTRY+timedelta(days=1)
    e._open({"ticker": "DAY.NS", "reasoning": "new setup", "score": 80.}, 10, 100.,
            clock[0], cfg=e.mode_cfg("intraday"))
    record = e.portfolio.positions["DAY.NS"].intraday_close
    assert record["deadline"] == "2026-11-12T09:55:00+00:00"
    assert record["attempts"] == 0 and record["status"] == "scheduled"


def test_rescoring_crossing_market_close_does_not_execute_an_old_intraday_quote(book, monkeypatch):
    import pandas as pd
    e, clock = book
    clock[0] = DUE-timedelta(minutes=1)
    e.cfg.disable_exits = False
    frame = pd.DataFrame({"Close": [100., 97.]}, index=pd.to_datetime(["2026-11-10", "2026-11-11"]))
    monkeypatch.setattr("trading.engine.fetch_daily", lambda tickers, **k: {"DAY.NS": frame} if "DAY.NS" in tickers else {})
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", lambda *a, **k: {"DAY.NS": 97., "HOLD.NS": 100.})
    def delayed(*a, **k):
        clock[0] = datetime(2026, 11, 11, 10, 1)
        return 80., {}
    monkeypatch.setattr("trading.engine.score_for_mode", delayed)
    e.refresh()
    assert "DAY.NS" in e.portfolio.positions
    assert not e.portfolio.trades
    assert e.portfolio.positions["DAY.NS"].intraday_close["status"] == "waiting_market"


def test_pyramid_cannot_add_to_an_intraday_holding_waiting_to_close(book, monkeypatch):
    e, _ = book
    e.cfg.pyramid_enabled = True
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", lambda *a, **k: {"HOLD.NS": 100.})
    def plan(pos, *a, **k):
        return (1, 100.) if pos.mode == "intraday" else None
    monkeypatch.setattr("trading.pyramid.pyramid_plan", plan)
    e.refresh()
    assert e.portfolio.positions["DAY.NS"].qty == 10
    assert not [r for r in e.portfolio.decision_journal if r.get("action") == "PYRAMID_ADD"]


def test_stale_exit_clock_cannot_bypass_market_close(book):
    e, clock = book
    clock[0] = datetime(2026, 11, 11, 10, 1)
    cash = e.portfolio.cash
    with pytest.raises(ValueError, match="intraday_market_closed"):
        e._close("DAY.NS", 97., "STOP_LOSS", ENTRY)
    assert e.portfolio.cash == cash and not e.portfolio.trades
    assert "DAY.NS" in e.portfolio.positions


def test_deadline_quote_failure_cannot_fall_back_to_an_earlier_refresh_quote(book, monkeypatch):
    import pandas as pd
    e, clock = book
    clock[0] = DUE-timedelta(minutes=1)
    e.cfg.disable_exits = False
    e.portfolio.risk_day = DUE.date().isoformat()
    e.portfolio.risk_day_equity = e.portfolio.cash + 2000.
    e.portfolio.risk_day_killed = True
    quotes = iter([{"DAY.NS": 97., "HOLD.NS": 100.}, {}, {}, {}])
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", lambda *a, **k: next(quotes, {}))
    frame = pd.DataFrame({"Close": [100., 97.]}, index=pd.to_datetime(["2026-11-10", "2026-11-11"]))
    def delayed_history(tickers, **k):
        clock[0] = DUE+timedelta(seconds=1)
        return {"DAY.NS": frame} if "DAY.NS" in tickers else {}
    monkeypatch.setattr("trading.engine.fetch_daily", delayed_history)
    monkeypatch.setattr("trading.engine.score_for_mode", lambda *a, **k: (80., {}))
    e.refresh()
    assert "DAY.NS" in e.portfolio.positions
    assert not [t for t in e.portfolio.trades if t.ticker == "DAY.NS"]
    assert e.portfolio.positions["DAY.NS"].intraday_close["status"] == "waiting_quote"
