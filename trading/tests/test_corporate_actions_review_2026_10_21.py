"""Review of the ex-date adjuster (owner order 2026-10-21, the BLSE split).

Each test pins a defect the review found in the first build:
- one split worded two ways (NSE API vs the committed file) was applied
  twice — qty × 4 on a 2-for-1;
- a bonus and a split on one ex-date, once as a combined row and once as
  separate rows, multiplied three times;
- the adjuster ran only from scheduler call sites, so an exit pass started
  any other way (the 14:30 rotation's in-loop refresh on a freshly woken
  process, the owner's Refresh button) compared a halved quote with the
  unadjusted entry — the BLSE stop-out itself;
- two threads could both apply one action;
- a bare-date entry crashed the pending check, so that position was never
  adjusted;
- a rights issue on a second held name the same day was never journaled.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading import corporate_actions as ca
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio

NSE_SPLIT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"
FILE_SPLIT = "Face Value Split From Rs.10/- To Rs.5/-"


def _pos(ticker="BLSE.NS", qty=101, entry=320.16, when=datetime(2026, 10, 1, 4, 0), **kw):
    d = dict(ticker=ticker, qty=qty, entry_price=entry, entry_date=when, sector="IT",
             peak_price=330.0, entry_score=70.0, reasoning="fixture",
             stop_price=304.15, target_price=384.19, mode="nifty200_momentum")
    d.update(kw)
    return PaperPosition(**d)


@pytest.fixture()
def eng(tmp_path):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.cash = 1_000_000.0
    return TradingEngine(cfg, p)


# ------------------------------------------------------------ one action --
def test_one_split_worded_two_ways_is_one_action():
    rows = [{"symbol": "BLSE", "exDate": "06-Oct-2026", "subject": NSE_SPLIT, "source": "nse_api"},
            {"symbol": "BLSE", "exDate": "06-Oct-2026", "subject": FILE_SPLIT,
             "source": "committed_file"}]
    acts = ca.parse_rows(rows)
    assert len(acts) == 1
    assert acts[0]["ratio"] == 2.0 and acts[0]["source"] == "nse_api"


def test_the_engine_doubles_qty_once_when_both_sources_carry_the_split(eng):
    p = eng.portfolio
    pos = _pos()
    p.positions = {pos.ticker: pos}
    raw = [{"symbol": "BLSE", "exDate": "06-Oct-2026", "subject": NSE_SPLIT, "source": "nse_api"},
           {"symbol": "BLSE", "exDate": "06-Oct-2026", "subject": FILE_SPLIT,
            "source": "committed_file"}]
    applied = eng.apply_corporate_actions(today=date(2026, 10, 6),
                                          actions_for=lambda s: ca.parse_rows(raw))
    assert len(applied) == 1
    assert pos.qty == 202 and abs(pos.entry_price - 160.08) < 1e-6      # not 404 / 80.04
    assert sum(1 for e in p.decision_journal if e["action"] == "CORPORATE_ACTION_APPLIED") == 1


def test_pending_never_lists_one_ex_date_twice():
    a = {"symbol": "BLSE", "ex_date": "2026-10-06", "kind": "split", "ratio": 2.0,
         "subject": NSE_SPLIT, "isin": "", "source": "nse_api"}
    b = {**a, "subject": FILE_SPLIT, "source": "committed_file"}
    assert len(ca.pending_for_position(_pos(), [a, b], date(2026, 10, 6))) == 1


def test_bonus_and_split_on_one_day_multiply_once_whatever_the_rows():
    combined = {"symbol": "Y", "exDate": "06-Oct-2026", "source": "nse_api",
                "subject": "Bonus 1:1 And Face Value Split From Rs 10 To Rs 5"}
    bonus = {"symbol": "Y", "exDate": "06-Oct-2026", "source": "committed_file",
             "subject": "Bonus 1:1"}
    split = {"symbol": "Y", "exDate": "06-Oct-2026", "source": "committed_file",
             "subject": "Face Value Split From Rs 10 To Rs 5"}
    for rows in ([combined, bonus, split], [bonus, split], [combined]):
        acts = [a for a in ca.parse_rows(rows) if a["kind"] != "rights"]
        assert len(acts) == 1
        assert acts[0]["ratio"] == 4.0 and acts[0]["kind"] == "split+bonus"


# ---------------------------------------------- the exit pass absorbs it --
def _earlier_session_ist() -> date:
    """A session inside the 5-session window but never today (on the ex-date
    itself the restatement waits for 09:15 IST, and CI runs at any hour)."""
    today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    return date.fromisoformat(ca.recent_sessions(today, 2)[1])


def test_refresh_itself_absorbs_the_split_before_its_exit_check(eng, monkeypatch):
    """No scheduler call site in front of it: a refresh() started any way
    (the 14:30 rotation on a freshly woken process, the Refresh button) must
    still see a halved quote against a halved entry — a HOLD, not the −49%
    stop-out the BLSE book took."""
    ex = _earlier_session_ist()
    pos = _pos(when=datetime.combine(ex - timedelta(days=7), datetime.min.time()).replace(hour=4))
    eng.portfolio.positions = {pos.ticker: pos}
    ca.set_store_reader(lambda syms: [{"symbol": "BLSE", "exDate": ex.strftime("%d-%b-%Y"),
                                       "subject": NSE_SPLIT, "source": "nse_api"}])

    def frame():
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
        close = np.linspace(150.0, 158.0, 300)
        return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                             "Close": close, "Volume": np.full(300, 5e6)}, index=idx)
    monkeypatch.setattr("trading.engine.fetch_daily",
                        lambda tickers, period="6mo": {t: frame() for t in
                                                       ([tickers] if isinstance(tickers, str)
                                                        else tickers)})
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda tickers, upstox_client=None: {t: 158.0 for t in tickers})
    try:
        eng.refresh()
    finally:
        ca.set_store_reader(None)
    p = eng.portfolio
    assert "BLSE.NS" in p.positions and p.trades == []
    assert pos.qty == 202 and abs(pos.entry_price - 160.08) < 1e-6
    assert not any(e["action"] == "SELL" for e in p.decision_journal)
    assert any(e["action"] == "CORPORATE_ACTION_APPLIED" for e in p.decision_journal)


def test_scan_absorbs_before_its_kill_switch_reads_live_marks():
    from pathlib import Path
    src = Path(__file__).resolve().parents[2].joinpath("trading", "engine.py").read_text()
    body = src[src.index("    def scan("):src.index("    def refresh(")]
    assert body.index('self._absorb_corporate_actions("scan")') < body.index("check_kill_switch(")


def test_on_the_ex_date_the_restatement_waits_for_the_open(eng):
    """Before 09:15 IST the quote is yesterday's unadjusted last trade: a
    doubled qty marked at it would inflate equity and the persisted
    drawdown peak for a pre-open scan. From the open it applies."""
    pos = _pos()
    eng.portfolio.positions = {pos.ticker: pos}
    acts = [{"symbol": "BLSE", "ex_date": "2026-10-06", "kind": "split", "ratio": 2.0,
             "subject": NSE_SPLIT, "isin": "", "source": "nse_api"}]
    pre = datetime(2026, 10, 6, 3, 0)               # 08:30 IST
    assert eng.apply_corporate_actions(today=date(2026, 10, 6), actions_for=lambda s: acts,
                                       now=pre) == []
    assert pos.qty == 101
    opened = datetime(2026, 10, 6, 3, 46)           # 09:16 IST
    assert len(eng.apply_corporate_actions(today=date(2026, 10, 6), actions_for=lambda s: acts,
                                           now=opened)) == 1
    assert pos.qty == 202
    # a later day's pre-open run applies a missed ex-date at once
    pos2 = _pos(ticker="LATE.NS")
    eng.portfolio.positions = {pos2.ticker: pos2}
    later = datetime(2026, 10, 8, 2, 0)             # 07:30 IST two sessions on
    assert len(eng.apply_corporate_actions(today=date(2026, 10, 8),
                                           actions_for=lambda s: [{**acts[0], "symbol": "LATE"}],
                                           now=later)) == 1


def test_two_threads_never_double_one_position(eng, monkeypatch):
    """The scheduler's exit pass and a web refresh can run together. Both
    reaching the restatement before either has recorded it would double the
    position twice — the lock lets only one in at a time."""
    pos = _pos()
    eng.portfolio.positions = {pos.ticker: pos}
    acts = [{"symbol": "BLSE", "ex_date": "2026-10-06", "kind": "split", "ratio": 2.0,
             "subject": NSE_SPLIT, "isin": "", "source": "nse_api"}]
    gate = threading.Barrier(2, timeout=1.0)
    real = ca.apply_to_position

    def slow_apply(p, c):
        try:
            gate.wait()                 # without the lock both threads meet here
        except threading.BrokenBarrierError:
            pass
        return real(p, c)
    monkeypatch.setattr(ca, "apply_to_position", slow_apply)
    ts = [threading.Thread(target=eng.apply_corporate_actions,
                           kwargs={"today": date(2026, 10, 6), "actions_for": lambda s: acts})
          for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert pos.qty == 202 and abs(pos.entry_price - 160.08) < 1e-6
    assert sum(1 for e in eng.portfolio.decision_journal
               if e["action"] == "CORPORATE_ACTION_APPLIED") == 1


def test_a_bare_date_entry_is_adjusted(eng):
    pos = _pos(when=date(2026, 10, 1))
    eng.portfolio.positions = {pos.ticker: pos}
    acts = [{"symbol": "BLSE", "ex_date": "2026-10-06", "kind": "split", "ratio": 2.0,
             "subject": NSE_SPLIT, "isin": "", "source": "nse_api"}]
    applied = eng.apply_corporate_actions(today=date(2026, 10, 6), actions_for=lambda s: acts)
    assert len(applied) == 1 and pos.qty == 202


def test_a_rights_issue_is_named_for_every_held_name(eng):
    a, b = _pos(), _pos(ticker="OTHER.NS")
    eng.portfolio.positions = {a.ticker: a, b.ticker: b}

    def rights(sym):
        return [{"symbol": sym, "ex_date": "2026-10-06", "kind": "rights", "ratio": None,
                 "subject": "Rights 1:4", "isin": "", "source": "nse_api"}]
    eng.apply_corporate_actions(today=date(2026, 10, 6), actions_for=rights)
    eng.apply_corporate_actions(today=date(2026, 10, 6), actions_for=rights)  # same day again
    lines = [e for e in eng.portfolio.decision_journal
             if e["action"] == "CORPORATE_ACTION_UNAPPLIED"]
    assert sorted(e["ticker"] for e in lines) == ["BLSE.NS", "OTHER.NS"]
    assert a.qty == 101 and b.qty == 101


def test_rows_remembered_in_process_reach_the_adjuster():
    """The daily fetch keeps its rows in process too — a Mongo outage that
    refuses the save must not hide the split from the ex-date adjuster."""
    ca.clear_remembered()
    try:
        ca.remember_rows("BLSE", [{"symbol": "BLSE", "exDate": "06-Oct-2026",
                                   "subject": NSE_SPLIT, "source": "nse_api"}])
        assert any(a["ratio"] == 2.0 for a in ca.actions_for("BLSE.NS"))
        assert any(a["ratio"] == 2.0 for a in ca.actions_for_many(["BLSE.NS"])["BLSE"])
    finally:
        ca.clear_remembered()


def test_actions_for_many_reads_the_store_once():
    calls = []
    ca.set_store_reader(lambda syms: (calls.append(list(syms)), [
        {"symbol": "BLSE", "exDate": "06-Oct-2026", "subject": NSE_SPLIT, "source": "nse_api"}])[1])
    try:
        out = ca.actions_for_many(["BLSE.NS", "TCS.NS", "INFY.NS"])
    finally:
        ca.set_store_reader(None)
    assert len(calls) == 1 and sorted(calls[0]) == ["BLSE", "INFY", "TCS"]
    assert out["BLSE"][0]["ratio"] == 2.0
