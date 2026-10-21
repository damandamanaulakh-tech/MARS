"""Review of the corporate-action jobs (owner order 2026-10-21, BLSE).

Each test pins a defect the review found in the first build:
- a blocked/hanging NSE was asked for every held name (≤ 60 × 15 s);
- rows fetched while Mongo refused the save were lost (the docstring said
  "kept in process"), so the adjuster never saw the split;
- the 08:50 slot is often slept through and nothing else fetched the held
  names' actions before the adjuster ran;
- a restart puts the repo's universe file (old ISIN) back, so every next
  08:50 journaled INSTRUMENT_ISIN_CHANGED and replaced the history again;
- HISTORY_REFETCHED was journaled while Mongo was offline (the save is
  silent then) and a failing re-fetch journaled on every 30-minute pass;
- names the engine adjusted on its own (refresh()/scan()) never had their
  history replaced; the 14:30 rotation never ran the adjuster first.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pytest

from trading import corporate_actions as tca
from trading import engine as eng_mod
from trading.paper import PaperPosition
from web import db, scheduler, state

ROOT = Path(__file__).resolve().parents[2]
SPLIT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"
API_ROW = {"symbol": "BLSE", "exDate": "06-Oct-2026", "isin": "INE0NLT01010", "subject": SPLIT}


def _pos(ticker="BLSE.NS", qty=100, entry=320.16, when=datetime(2026, 10, 1, 4, 0)):
    return PaperPosition(ticker=ticker, qty=qty, entry_price=entry, entry_date=when, sector="IT",
                         peak_price=330.0, entry_score=70.0, reasoning="f", stop_price=304.15,
                         target_price=384.19, mode="nifty200_momentum")


@pytest.fixture()
def held(monkeypatch):
    p = state.portfolio()
    saved = (dict(p.positions), list(p.trades), p.cash, list(p.decision_journal))
    pos = _pos()
    p.positions = {pos.ticker: pos}
    p.trades = []
    p.cash = 1_000_000.0
    monkeypatch.setattr(db, "is_connected", lambda: True)
    scheduler._HISTORY_REFETCH_DUE.clear()
    eng_mod.CA_REFETCH_DUE.clear()
    tca.clear_remembered()
    monkeypatch.setattr(scheduler, "_CA_FETCH_DAY", "")
    try:
        yield p, pos
    finally:
        p.positions, p.trades, p.cash, p.decision_journal = saved
        scheduler._HISTORY_REFETCH_DUE.clear()
        eng_mod.CA_REFETCH_DUE.clear()
        tca.clear_remembered()


def _mine(p, n0, action):
    return [e for e in p.decision_journal[n0:] if e.get("action") == action]


def test_the_fetch_stops_asking_after_three_failures_in_a_row(held, monkeypatch):
    p, _ = held
    for t in ("A.NS", "B.NS", "C.NS", "D.NS", "E.NS"):
        p.positions[t] = _pos(ticker=t)
    asked = []
    n0 = len(p.decision_journal)
    out = scheduler._corporate_actions_fetch_job("test", fetch=lambda s: (asked.append(s), None)[1])
    assert len(asked) == 3 and len(out["skipped"]) == 3
    line = _mine(p, n0, "CORPORATE_ACTIONS_FETCHED")[-1]
    assert "NOT ASKED after 3 failures" in line["why"]


def test_rows_fetched_while_mongo_refuses_still_reach_the_adjuster(held, monkeypatch):
    p, pos = held
    monkeypatch.setattr(db, "is_connected", lambda: False)
    scheduler._corporate_actions_fetch_job("test", fetch=lambda s: [API_ROW])
    applied = state.engine().apply_corporate_actions(today=date(2026, 10, 6))
    assert len(applied) == 1 and pos.qty == 200


def test_the_adjuster_job_fetches_the_held_names_first_once_a_day(held, monkeypatch):
    monkeypatch.setenv("SB_CA_FETCH_ON_APPLY", "1")
    calls = []

    def fake_fetch(where, force=None, fetch=None, symbols=None):
        calls.append(where)
        scheduler._CA_FETCH_DAY = scheduler._ist_today_iso()
        return {}
    monkeypatch.setattr(scheduler, "_corporate_actions_fetch_job", fake_fetch)
    monkeypatch.setattr(scheduler, "_refetch_history", lambda *a, **k: True)
    scheduler.apply_corporate_actions_job("session", today=date(2026, 10, 5))
    scheduler.apply_corporate_actions_job("session", today=date(2026, 10, 5))
    assert calls == ["session"]


def test_the_production_default_fetches_before_the_adjuster():
    src = (ROOT / "web" / "scheduler.py").read_text()
    assert 'os.environ.get("SB_CA_FETCH_ON_APPLY", "1") != "0"' in src
    assert "SB_CA_FETCH_ON_APPLY" not in (ROOT / "render.yaml").read_text()
    assert "SB_CA_FETCH_PACE_S" not in (ROOT / "render.yaml").read_text()


def test_an_isin_change_is_journaled_once_per_new_key(held, monkeypatch):
    p, _ = held
    import trading.brokers.upstox_instruments as ui
    monkeypatch.setattr(ui, "prefer_keys", lambda m: None)
    monkeypatch.setattr(scheduler, "_held_instrument_keys",
                        lambda p: {"BLSE.NS": "NSE_EQ|INE0NLT01028"})
    n0 = len(p.decision_journal)
    before = {"BLSE.NS": "NSE_EQ|INE0NLT01010"}
    assert scheduler._note_isin_changes(p, before) == ["BLSE.NS"]
    scheduler._HISTORY_REFETCH_DUE.clear()
    # the next boot's 08:50 sees the repo file's old key again
    assert scheduler._note_isin_changes(p, before) == []
    assert len(_mine(p, n0, "INSTRUMENT_ISIN_CHANGED")) == 1
    # no re-fetch has landed on the new key yet (a restart came between):
    # still queued, never lost to the dedupe
    assert "BLSE.NS" in scheduler._HISTORY_REFETCH_DUE
    scheduler._HISTORY_REFETCH_DUE.clear()
    p.decision_journal.append({"ts": "2026-10-07T03:30:00", "action": "HISTORY_REFETCHED",
                               "ticker": "BLSE.NS", "key": "NSE_EQ|INE0NLT01028"})
    assert scheduler._note_isin_changes(p, before) == []
    assert "BLSE.NS" not in scheduler._HISTORY_REFETCH_DUE


def test_no_history_replacement_is_claimed_while_mongo_is_offline(held, monkeypatch):
    p, _ = held
    monkeypatch.setattr(db, "is_connected", lambda: False)
    import trading.brokers.upstox_instruments as ui
    monkeypatch.setattr(ui, "to_instrument_key", lambda t: "NSE_EQ|INE0NLT01028")
    n0 = len(p.decision_journal)
    assert scheduler._refetch_history("BLSE.NS", "test", broker=object()) is False
    assert not _mine(p, n0, "HISTORY_REFETCHED")
    assert "Mongo offline" in _mine(p, n0, "HISTORY_REFETCH_FAILED")[0]["err"]


def test_a_failing_refetch_is_journaled_once_a_day(held, monkeypatch):
    p, _ = held
    monkeypatch.setattr("trading.data.history_broker", lambda: None)
    monkeypatch.setattr(state.engine(), "broker", None)
    n0 = len(p.decision_journal)
    for _ in range(3):
        scheduler._refetch_history("BLSE.NS", "session")
    assert len(_mine(p, n0, "HISTORY_REFETCH_FAILED")) == 1


def test_names_the_engine_adjusted_itself_get_their_history_replaced(held, monkeypatch):
    p, pos = held
    eng_mod.CA_REFETCH_DUE.add("BLSE.NS")
    got = []
    monkeypatch.setattr(scheduler, "_refetch_history",
                        lambda t, where, broker=None, today=None: (got.append(t), True)[1])
    monkeypatch.setattr(state.engine(), "apply_corporate_actions", lambda today=None: [])
    scheduler.apply_corporate_actions_job("session")
    assert got == ["BLSE.NS"] and not eng_mod.CA_REFETCH_DUE


def test_the_engine_queues_what_it_adjusted_for_the_refetch(held):
    p, pos = held
    state.engine().apply_corporate_actions(
        today=date(2026, 10, 6),
        actions_for=lambda s: tca.parse_rows([API_ROW]) if s == "BLSE" else [])
    assert "BLSE.NS" in eng_mod.CA_REFETCH_DUE


def test_the_1430_rotation_runs_the_adjuster_first():
    src = "\n".join(ln for ln in (ROOT / "web" / "scheduler.py").read_text().splitlines()
                    if not ln.strip().startswith("#"))
    body = re.search(r"def _deployment_topup_scan\(\).*?_check_pending_orders\(\"top-up\"\)",
                     src, re.S).group(0)
    assert body.index('apply_corporate_actions_job("14:30")') < body.index("_run_category_rotation(")


def test_ca_applied_survives_the_mongo_mirror_and_hydrate(held, monkeypatch):
    """Render wipes the JSON on every restart: the field must ride the Mongo
    mirror both ways (the risk_day lesson), on positions AND closed trades."""
    from trading.paper import ClosedTrade, Portfolio
    p, pos = held
    pos.ca_applied = ["BLSE|2026-10-06"]
    p.trades = [ClosedTrade(ticker="X.NS", qty=2, entry_date=datetime(2026, 10, 1, 4, 0),
                            exit_date=datetime(2026, 10, 8, 5, 0), entry_price=50.0,
                            exit_price=51.0, gross_pnl=2.0, costs=0.1, net_pnl=1.9,
                            reason="TARGET", sector="IT", entry_score=70.0, reasoning="f",
                            ca_applied=["X|2026-10-06"])]
    docs = []
    monkeypatch.setattr(db, "save_portfolio", lambda doc: docs.append(doc))
    p.save()
    assert docs, "the mirror did not run"
    back = Portfolio.fresh(state.cfg())
    state._hydrate_portfolio_from_mongo(back, docs[-1])
    assert back.positions["BLSE.NS"].ca_applied == ["BLSE|2026-10-06"]
    assert back.trades[0].ca_applied == ["X|2026-10-06"]
