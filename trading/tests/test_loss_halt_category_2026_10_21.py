"""Owner order 2026-10-21 (choice card): "Halt only the category that lost.
Two losing momentum exits halt momentum for the day; the other four keep
buying. Same max(2, 10%) count, per category."

Before: risk_v2.ConsecutiveLossTracker counted the whole book and the engine
refused every category's new entries for the rest of the day.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio
from trading.risk_v2 import ConsecutiveLossTracker
from trading.tests.test_audit_s4_s9_2026_10_02 import _near_target, _scan_engine
from trading.tests.test_category_budgets_2026_10_04 import _open_swing_gates

MOM = "nifty200_momentum"
D = date(2026, 10, 21)


# ---------------------------------------------------------------- tracker

def test_two_losing_momentum_exits_halt_momentum_only():
    t = ConsecutiveLossTracker()
    t.record_close(D, -1000.0, reason="STOP_LOSS", category=MOM)
    t.record_close(D, -1000.0, reason="STOP_LOSS", category=MOM)
    assert t.halted(today=D, category=MOM, open_in_category=3, scope="category")
    for other in ("intraday", "short", "swing", "positional", "ocean"):
        assert not t.halted(today=D, category=other, open_in_category=3,
                            scope="category"), other
    # book scope = the old rule: everyone halted
    assert t.halted(today=D, open_positions=5, scope="book")
    assert t.halted(today=D, category="swing", scope="book")


def test_the_ten_percent_rule_is_per_category():
    """max(2, ceil(10% x (open in that category + its losers today)))."""
    t = ConsecutiveLossTracker()
    t.record_close(D, -1.0, reason="STOP_LOSS", category=MOM)
    t.record_close(D, -1.0, reason="STOP_LOSS", category=MOM)
    # momentum holds 48 names: limit 5, two losers do not halt it
    assert t.category_limit(MOM, 48) == 5
    assert not t.halted(today=D, category=MOM, open_in_category=48,
                        scope="category")
    # the book-wide count would read the same two against the BOOK's limit
    for _ in range(3):
        t.record_close(D, -1.0, reason="STOP_LOSS", category=MOM)
    assert t.halted(today=D, category=MOM, open_in_category=45, scope="category")
    # swing with three names and two losers: limit 2, halted
    t.record_close(D, -1.0, reason="DECAY", category="swing")
    t.record_close(D, -1.0, reason="DECAY", category="swing")
    assert t.halted(today=D, category="swing", open_in_category=3, scope="category")
    assert t.by_category == {MOM: 5, "swing": 2}
    assert t.losses_today == 7


def test_excluded_closes_and_a_new_day_reset_per_category_too():
    t = ConsecutiveLossTracker()
    for why in ("OWNER_SELL", "KILL_SWITCH", "SQUARE_OFF"):
        t.record_close(D, -1000.0, reason=why, category=MOM)
    assert t.by_category == {}
    t.record_close(D, -1000.0, reason="STOP_LOSS", category="owner")
    t.record_close(D, -1000.0, reason="STOP_LOSS", category="owner")
    assert t.halted(today=D, category="owner", open_in_category=1, scope="category")
    assert not t.halted(today=D, category=MOM, open_in_category=1, scope="category")
    t.record_close(date(2026, 10, 22), -1.0, reason="STOP_LOSS", category=MOM)
    assert t.by_category == {MOM: 1} and t.losses_today == 1


def test_the_snapshot_names_the_halted_categories():
    t = ConsecutiveLossTracker()
    t.record_close(D, -1.0, reason="STOP_LOSS", category=MOM)
    t.record_close(D, -1.0, reason="STOP_LOSS", category=MOM)
    s = t.snapshot(open_positions=4, open_by_category={MOM: 2, "swing": 2},
                   scope="category")
    assert s["halted_categories"] == [MOM] and s["halted"] is True
    rows = {r["category"]: r for r in s["categories"]}
    assert rows[MOM]["losses"] == 2 and rows[MOM]["max_allowed"] == 2
    assert rows["swing"]["losses"] == 0 and rows["swing"]["halted"] is False
    s2 = t.snapshot(open_positions=4, scope="book")
    assert s2["halted"] is True and s2["scope"] == "book"


def test_old_callers_keep_the_book_wide_meaning():
    """halted() with no category/scope = the pre-order book rule (the
    hydrate test in test_silent_death_fixes_c relies on it)."""
    t = ConsecutiveLossTracker()
    t.record_close(D, -1.0)
    t.record_close(D, -1.0)
    assert t.halted(today=D) is True
    assert t.by_category == {}


# ----------------------------------------------------------------- engine

def _two_losing_momentum_exits(e, now):
    """Close two momentum positions at a loss through the engine's own
    _close so the tracker is fed the way the live exit pass feeds it."""
    old2 = _near_target("OLD2.NS")
    e.portfolio.positions["OLD2.NS"] = old2
    e._close("OLD.NS", 90.0, "STOP_LOSS", now)
    e._close("OLD2.NS", 90.0, "STOP_LOSS", now)
    assert e.loss_tracker.by_category == {MOM: 2}
    assert e.portfolio.loss_by_category == {MOM: 2}
    assert e.portfolio.loss_count == 2
    e.portfolio.cash = 5_00_00_000.0
    e.cfg.measured_kelly_enabled = False       # isolate the loss halt


def test_a_swing_entry_still_opens_after_two_losing_momentum_exits(tmp_path,
                                                                   monkeypatch):
    """The order itself. On the old code the book-wide halt refused the
    swing pick too (reason consecutive_loss_halt)."""
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    now = datetime.utcnow()
    _two_losing_momentum_exits(e, now)
    assert e.cfg.loss_halt_scope == "category"      # the default
    # momentum is halted: its pick waits, and the journal says which category
    n0 = len(e.portfolio.decision_journal)
    e.scan()
    assert "UPP.NS" not in e.portfolio.positions
    sk = {s["ticker"]: s for s in e.portfolio.last_scan_skipped}
    assert sk["UPP.NS"]["reason"] == "consecutive_loss_halt"
    assert sk["UPP.NS"]["category"] == MOM
    lines = [x for x in e.portfolio.decision_journal[n0:]
             if x.get("action") == "CONSECUTIVE_LOSS_HALT"]
    assert len(lines) == 1
    assert lines[0]["category"] == MOM and lines[0]["scope"] == "category"
    assert lines[0]["losses"] == 2 and lines[0]["max_allowed"] == 2
    assert lines[0]["by_category"] == {MOM: 2}
    assert lines[0]["refused"] == ["UPP.NS"]
    # a second momentum pass today does not repeat the line
    e.scan()
    assert len([x for x in e.portfolio.decision_journal[n0:]
                if x.get("action") == "CONSECUTIVE_LOSS_HALT"]) == 1
    # swing keeps buying
    _open_swing_gates(e, monkeypatch)
    e.scan()
    assert "UPP.NS" in e.portfolio.positions
    assert e.portfolio.positions["UPP.NS"].mode == "swing"


def test_scope_book_reproduces_the_old_behaviour(tmp_path, monkeypatch):
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    _two_losing_momentum_exits(e, datetime.utcnow())
    e.cfg.loss_halt_scope = "book"
    _open_swing_gates(e, monkeypatch)
    n0 = len(e.portfolio.decision_journal)
    e.scan()
    assert "UPP.NS" not in e.portfolio.positions
    sk = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert sk["UPP.NS"] == "consecutive_loss_halt"
    line = [x for x in e.portfolio.decision_journal[n0:]
            if x.get("action") == "CONSECUTIVE_LOSS_HALT"]
    assert line and line[0]["scope"] == "book" and line[0]["losses"] == 2


def test_an_ocean_pick_asks_the_ocean_category():
    cfg = Config()
    assert TradingEngine._loss_halt_category({"ticker": "X", "ocean": True}, cfg) == "ocean"
    cfg.mode = "swing"
    assert TradingEngine._loss_halt_category({"ticker": "X"}, cfg) == "swing"
    cfg.mode = ""
    assert TradingEngine._loss_halt_category({"ticker": "X"}, cfg) == MOM
    cfg.loss_halt_scope = "nonsense"
    assert TradingEngine._loss_halt_scope(cfg) == "category"


def test_the_engine_counts_open_positions_per_category(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)

    def _pos(t, mode, tags=()):
        return PaperPosition(ticker=t, qty=1, entry_price=100.0,
                             entry_date=datetime(2026, 10, 1), sector="IT",
                             peak_price=100.0, entry_score=70.0,
                             reasoning="fixture", mode=mode, tags=list(tags))
    p.positions = {"A.NS": _pos("A.NS", MOM), "B.NS": _pos("B.NS", MOM),
                   "C.NS": _pos("C.NS", "swing"),
                   "D.NS": _pos("D.NS", MOM, tags=["owner_pick"]),
                   "E.NS": _pos("E.NS", MOM, tags=["ocean"])}
    e = TradingEngine(cfg, p)
    assert e._open_in_category(MOM) == 2
    assert e._open_by_category() == {MOM: 2, "swing": 1, "owner": 1, "ocean": 1}


# ------------------------------------------------------------ persistence

def test_per_category_counts_round_trip_through_json_and_the_engine(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.loss_day = date.today().isoformat()
    p.loss_count = 2
    p.loss_by_category = {MOM: 2}
    p.save()
    back = Portfolio.load_or_fresh(cfg)
    assert back.loss_by_category == {MOM: 2}
    eng = TradingEngine(cfg, back)
    assert eng.loss_tracker.by_category == {MOM: 2}
    assert eng.loss_tracker.halted(today=date.today(), category=MOM,
                                   open_in_category=0, scope="category")
    assert not eng.loss_tracker.halted(today=date.today(), category="swing",
                                       open_in_category=0, scope="category")


def test_per_category_counts_ride_the_mongo_mirror_both_ways(tmp_path):
    """The risk_day lesson: a field missing from the mirror dies on every
    Render restart. Hydrate from a modelled Mongo doc, and the save doc
    carries the field."""
    from web import state as app_state
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    doc = {"cash": 1.0, "positions": {}, "trades": [],
           "loss_day": date.today().isoformat(), "loss_count": 3,
           "loss_by_category": {MOM: 2, "swing": 1}}
    app_state._hydrate_portfolio_from_mongo(p, doc)
    assert p.loss_by_category == {MOM: 2, "swing": 1}
    eng = TradingEngine(cfg, p)
    assert eng.loss_tracker.by_category == {MOM: 2, "swing": 1}
    src = Path("web/state.py").read_text()
    hydrate = src.split("def _hydrate_portfolio_from_mongo")[1].split("\ndef ")[0]
    mirror = src.split("def _save_with_mirror")[1].split("db.save_portfolio(doc)")[0]
    assert "loss_by_category" in hydrate and '"loss_by_category"' in mirror


def test_the_backtest_runs_no_loss_halt_at_all():
    """Convention note for the owner: the backtest never ran this halt
    (no ConsecutiveLossTracker in trading/backtest.py), so scope "book"
    vs "category" changes nothing there — nothing to switch."""
    src = Path("trading/backtest.py").read_text()
    assert "ConsecutiveLossTracker" not in src and "loss_tracker" not in src


# ------------------------------------------------- review fixes 2026-10-21
# Each test below failed on the first build of this order.

def test_owner_losers_are_shown_but_never_read_as_a_halt():
    """Owner buys are never gated by the loss halt, so two losing owner
    exits must not print "HALT ACTIVE for owner" — the count is shown, the
    row says it gates nothing."""
    t = ConsecutiveLossTracker()
    t.record_close(D, -1.0, reason="STOP_LOSS", category="owner")
    t.record_close(D, -1.0, reason="STOP_LOSS", category="owner")
    s = t.snapshot(open_positions=1, open_by_category={"owner": 1},
                   scope="category", today=D)
    assert s["halted"] is False and s["halted_categories"] == []
    row = {r["category"]: r for r in s["categories"]}["owner"]
    assert row["losses"] == 2 and row["gated"] is False
    assert row["halted"] is False
    mom = t.snapshot(open_by_category={MOM: 1}, scope="category", today=D)
    assert {r["category"]: r for r in mom["categories"]}[MOM]["gated"] is True
    # the ungated label is the owner bucket's own name
    from trading import category_budget as cb
    from trading import risk_v2
    assert risk_v2.LOSS_HALT_UNGATED == (cb.OWNER,)


def test_yesterdays_counts_never_read_as_halted_today():
    """The gate reads nothing halted on a new day (the counts reset at the
    day's first close). The Risk card must agree instead of printing
    yesterday's halt as active until then."""
    t = ConsecutiveLossTracker()
    t.record_close(D, -1.0, reason="STOP_LOSS", category=MOM)
    t.record_close(D, -1.0, reason="STOP_LOSS", category=MOM)
    nxt = date(2026, 10, 22)
    assert not t.halted(today=nxt, category=MOM, open_in_category=0,
                        scope="category")                       # the gate
    s = t.snapshot(open_positions=0, open_by_category={}, scope="category",
                   today=nxt)
    assert s["halted"] is False and s["halted_categories"] == []
    assert s["losses_today"] == 0
    assert all(r["losses"] == 0 for r in s["categories"])
    assert t.snapshot(open_positions=0, scope="book", today=nxt)["halted"] is False
    # the same day still reads halted
    same = t.snapshot(open_positions=0, open_by_category={}, scope="category",
                      today=D)
    assert same["halted_categories"] == [MOM]


def test_a_category_that_emptied_reads_zero_open():
    """The open counts the Risk page passes are the WHOLE book; a category
    whose positions all closed must read 0 open, not its last count."""
    t = ConsecutiveLossTracker()
    t.snapshot(open_positions=27, open_by_category={MOM: 2, "intraday": 25},
               scope="category")
    s = t.snapshot(open_positions=2, open_by_category={MOM: 2},
                   scope="category")
    rows = {r["category"]: r for r in s["categories"]}
    assert rows.get("intraday", {"open": 0})["open"] == 0
    assert rows[MOM]["open"] == 2


def test_a_restart_onto_a_pre_category_record_keeps_todays_halt(tmp_path):
    """The deploy itself is a restart: a book saved by the old code carries
    today's losers with no category. Until today's record is categorised the
    whole-book rule stands, so the restart never forgets a halt that was
    already on; the next day clears it like any other count."""
    from datetime import timedelta
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    today = datetime.utcnow().date()
    p.loss_day = today.isoformat()
    p.loss_count = 2                    # old shape: no loss_by_category
    eng = TradingEngine(cfg, p)
    t = eng.loss_tracker
    assert t.by_category == {}
    for cat in (MOM, "swing", "ocean"):
        assert t.halted(today=today, open_positions=0, category=cat,
                        open_in_category=0, scope="category"), cat
    s = t.snapshot(open_positions=0, open_by_category={}, scope="category",
                   today=today)
    assert s["halted"] is True and s["uncategorised"] == 2
    nxt = today + timedelta(days=1)
    t.record_close(nxt, -1.0, reason="STOP_LOSS", category=MOM)
    assert not t.halted(today=nxt, category="swing", open_in_category=0,
                        scope="category")
    assert t.uncategorised() == 0


def test_the_halt_line_is_written_once_per_category_per_day(tmp_path,
                                                            monkeypatch):
    """"Once per category per day" must hold on a busy day too: a look-back
    of the last 60 journal lines let a second line through once the exit
    passes, buys and other categories' lines between two momentum passes
    pushed the first one out of the window."""
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    now = datetime.utcnow()
    _two_losing_momentum_exits(e, now)
    n0 = len(e.portfolio.decision_journal)
    e.scan()
    for i in range(80):                       # a busy day between the passes
        e.portfolio.decision_journal.append(
            {"ts": datetime.utcnow().isoformat(), "action": "HOLD",
             "ticker": f"F{i}.NS"})
    e.scan()
    lines = [x for x in e.portfolio.decision_journal[n0:]
             if x.get("action") == "CONSECUTIVE_LOSS_HALT"]
    assert len(lines) == 1, len(lines)


def test_the_legacy_halt_line_says_why(tmp_path, monkeypatch):
    """A restart onto a pre-category record holds every category for the
    rest of the day — the journal line must say so, not "lost 0"."""
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    t = e.loss_tracker
    t.today = datetime.utcnow().date()
    t.losses_today = 2
    t.by_category = {}
    e.portfolio.cash = 5_00_00_000.0
    e.cfg.measured_kelly_enabled = False
    n0 = len(e.portfolio.decision_journal)
    e.scan()
    sk = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert sk["UPP.NS"] == "consecutive_loss_halt"
    line = [x for x in e.portfolio.decision_journal[n0:]
            if x.get("action") == "CONSECUTIVE_LOSS_HALT"][0]
    assert line["uncategorised"] == 2
    assert "before per-category counting" in line["why"]
