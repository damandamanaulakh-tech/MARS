"""Review fixes to the corporate-action jobs (owner order 2026-10-21, BLSE).

- the history re-fetch owed after an adjustment lived only in process
  memory: a restart between the adjustment and the next job lost it, and
  the stored series kept old raw bars under new adjusted ones (a fake -50%
  bar for the momentum score and the danger tripwires). The journal now
  carries the debt across a restart;
- an ISIN change on a held name with no split/bonus record reachable (NSE
  refusing the live box, the committed file older) was silent — the exact
  BLSE case. It is journaled loudly; nothing is sold.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from trading import corporate_actions as tca
from trading import engine as eng_mod
from trading.paper import PaperPosition
from web import db, scheduler, state

SPLIT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"


def _pos(ticker="BLSE.NS"):
    return PaperPosition(ticker=ticker, qty=100, entry_price=320.16,
                         entry_date=datetime(2026, 10, 1, 4, 0), sector="IT", peak_price=330.0,
                         entry_score=70.0, reasoning="f", stop_price=304.15, target_price=384.19,
                         mode="nifty200_momentum")


@pytest.fixture()
def held(monkeypatch):
    p = state.portfolio()
    saved = (dict(p.positions), list(p.trades), p.cash, list(p.decision_journal))
    pos = _pos()
    p.positions = {pos.ticker: pos}
    p.trades = []
    p.decision_journal = []
    monkeypatch.setattr(db, "is_connected", lambda: True)
    scheduler._HISTORY_REFETCH_DUE.clear()
    eng_mod.CA_REFETCH_DUE.clear()
    tca.clear_remembered()
    reader = tca._STORE_READER
    tca.set_store_reader(None)
    try:
        yield p, pos
    finally:
        p.positions, p.trades, p.cash, p.decision_journal = saved
        scheduler._HISTORY_REFETCH_DUE.clear()
        eng_mod.CA_REFETCH_DUE.clear()
        tca.clear_remembered()
        tca.set_store_reader(reader)


def test_an_adjustment_made_before_a_restart_still_gets_its_history_replaced(held, monkeypatch):
    p, _ = held
    # the adjuster ran, then the process died: both in-process queues are empty
    p.decision_journal.append({"ts": datetime.utcnow().isoformat(),
                               "action": "CORPORATE_ACTION_APPLIED", "ticker": "BLSE.NS"})
    got = []
    monkeypatch.setattr(scheduler, "_refetch_history",
                        lambda t, where, broker=None, today=None: (got.append(t), False)[1])
    monkeypatch.setattr(state.engine(), "apply_corporate_actions", lambda today=None: [])
    scheduler.apply_corporate_actions_job("session")
    assert got == ["BLSE.NS"]
    # once a re-fetch has landed after it, nothing more is owed
    p.decision_journal.append({"ts": (datetime.utcnow() + timedelta(seconds=1)).isoformat(),
                               "action": "HISTORY_REFETCHED", "ticker": "BLSE.NS"})
    got.clear()
    scheduler._HISTORY_REFETCH_DUE.clear()
    scheduler.apply_corporate_actions_job("session")
    assert got == []


def test_an_old_adjustment_is_not_owed_for_ever(held):
    p, _ = held
    old = (datetime.utcnow() - timedelta(days=scheduler.REFETCH_OWED_DAYS + 2)).isoformat()
    p.decision_journal.append({"ts": old, "action": "CORPORATE_ACTION_APPLIED",
                               "ticker": "BLSE.NS"})
    assert scheduler._refetch_owed_from_journal(p) == set()


def test_an_isin_change_with_no_split_record_is_journaled_loudly(held):
    p, _ = held
    # the committed file carries no October BLSE split, NSE did not answer
    assert scheduler._warn_isin_change_without_record(p, ["BLSE.NS"]) == ["BLSE.NS"]
    lines = [e for e in p.decision_journal if e["action"] == "CORPORATE_ACTION_RECORD_MISSING"]
    assert len(lines) == 1 and lines[0]["ticker"] == "BLSE.NS" and "by hand" in lines[0]["why"]
    # with the record in hand there is nothing to warn about
    ex = (date.today() - timedelta(days=3)).strftime("%d-%b-%Y")
    tca.remember_rows("BLSE", [{"symbol": "BLSE", "exDate": ex, "subject": SPLIT,
                                "source": "nse_api"}])
    assert scheduler._warn_isin_change_without_record(p, ["BLSE.NS"]) == []


def test_the_0850_job_warns_after_its_forced_fetch(held, monkeypatch):
    p, _ = held
    keys = iter([{"BLSE.NS": "NSE_EQ|INE0NLT01010"}, {"BLSE.NS": "NSE_EQ|INE0NLT01028"}])
    monkeypatch.setattr(scheduler, "_held_instrument_keys", lambda p: next(keys))
    monkeypatch.setattr("trading.universe_refresh.refresh_universe",
                        lambda: {"total": 2600, "added": 0, "removed": 0, "added_names": [],
                                 "removed_names": [], "was": 2600})
    monkeypatch.setattr("trading.universe_refresh.refresh_series_watch",
                        lambda: {"total": 0, "scored": 0})
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a, **k: True)
    import trading.brokers.upstox_instruments as ui
    monkeypatch.setattr(ui, "prefer_keys", lambda m: None)
    monkeypatch.setattr(scheduler, "_corporate_actions_fetch_job",
                        lambda where, force=None, fetch=None, symbols=None: {})
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda where: None)
    scheduler._universe_refresh_job()
    acts = [e["action"] for e in p.decision_journal]
    assert "INSTRUMENT_ISIN_CHANGED" in acts and "CORPORATE_ACTION_RECORD_MISSING" in acts
