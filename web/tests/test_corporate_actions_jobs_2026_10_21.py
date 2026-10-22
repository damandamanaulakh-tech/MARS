"""The prevention wiring for the BLSE split (owner order 2026-10-21):
the 08:50 NSE corporate-action fetch for held names, ISIN-change detection
on the universe refresh, the apply-then-refetch job, and the call sites
that run it BEFORE any exit pass.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from trading import corporate_actions as tca
from trading.paper import PaperPosition
from web import db, scheduler, state

ROOT = Path(__file__).resolve().parents[2]
SPLIT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"
API_ROW = {"symbol": "BLSE", "exDate": "06-Oct-2026", "recDate": "06-Oct-2026",
           "isin": "INE0NLT01010", "subject": SPLIT, "series": "EQ", "faceVal": "5"}


def _pos(ticker="BLSE.NS", qty=100, entry=320.16, when=datetime(2026, 10, 1, 4, 0)):
    return PaperPosition(ticker=ticker, qty=qty, entry_price=entry, entry_date=when, sector="IT",
                         peak_price=330.0, entry_score=70.0, reasoning="f", stop_price=304.15,
                         target_price=384.19, mode="nifty200_momentum")


@pytest.fixture()
def held(monkeypatch):
    p = state.portfolio()
    saved = (dict(p.positions), list(p.trades), p.cash, list(p.decision_journal))
    # The shared singleton's journal carries lines from earlier tests in the
    # same process, and _refetch_history's once-a-day guard reads it —
    # a FAILED line left by another test silenced this one in CI.
    p.decision_journal = []
    pos = _pos()
    p.positions = {pos.ticker: pos}
    p.trades = []
    p.cash = 1_000_000.0
    monkeypatch.setattr(db, "is_connected", lambda: True)
    scheduler._HISTORY_REFETCH_DUE.clear()
    # the engine's re-fetch queue is module-global: names other tests'
    # engines adjusted must not ride into this one
    from trading import engine as eng_mod
    eng_mod.CA_REFETCH_DUE.clear()
    try:
        yield p, pos
    finally:
        p.positions, p.trades, p.cash, p.decision_journal = saved
        scheduler._HISTORY_REFETCH_DUE.clear()
        eng_mod.CA_REFETCH_DUE.clear()


def test_the_0850_fetch_stores_rows_for_held_names_and_journals_new_actions(held, monkeypatch):
    p, pos = held
    stored = []
    monkeypatch.setattr(db, "save_corporate_actions", lambda rows: (stored.extend(rows), 1)[1])
    seen = []

    def fake_fetch(sym):
        seen.append(sym)
        return [API_ROW, {**API_ROW, "exDate": "08-Sep-2026", "subject": "Dividend - Re 0.50 Per Share"}]
    out = scheduler._corporate_actions_fetch_job("test", fetch=fake_fetch)
    assert seen == ["BLSE"] and out["fetched"] == 1 and out["new"] == 1
    assert any(r["subject"] == SPLIT for r in stored)
    line = [e for e in p.decision_journal if e["action"] == "CORPORATE_ACTIONS_FETCHED"]
    assert len(line) == 1 and line[0]["new"][0]["ratio"] == 2.0
    # a failed fetch is named, never read as "no actions"
    p.decision_journal.clear()
    out2 = scheduler._corporate_actions_fetch_job("test", fetch=lambda s: None)
    assert out2["failed"] == ["BLSE"]
    assert "FAILED for BLSE" in p.decision_journal[-1]["why"]


def test_no_held_names_means_no_fetch(held):
    p, _ = held
    p.positions = {}
    out = scheduler._corporate_actions_fetch_job("test", fetch=lambda s: pytest.fail("fetched"))
    assert out["fetched"] == 0


def test_an_isin_change_on_a_held_name_is_journaled_preferred_and_queued(held, monkeypatch):
    p, pos = held
    import trading.brokers.upstox_instruments as ui
    before = {"BLSE.NS": "NSE_EQ|INE0NLT01010"}
    monkeypatch.setattr(scheduler, "_held_instrument_keys", lambda p: {"BLSE.NS": "NSE_EQ|INE0NLT01028"})
    prefs = {}
    monkeypatch.setattr(ui, "prefer_keys", lambda m: prefs.update(m))
    changed = scheduler._note_isin_changes(p, before)
    assert changed == ["BLSE.NS"]
    assert prefs == {"BLSE.NS": "NSE_EQ|INE0NLT01028"}
    assert "BLSE.NS" in scheduler._HISTORY_REFETCH_DUE
    line = [e for e in p.decision_journal if e["action"] == "INSTRUMENT_ISIN_CHANGED"]
    assert len(line) == 1 and line[0]["old_key"].endswith("1010") and line[0]["new_key"].endswith("1028")
    # same key = nothing
    assert scheduler._note_isin_changes(p, {"BLSE.NS": "NSE_EQ|INE0NLT01028"}) == []


def test_prefer_keys_wins_in_every_lookup_path(monkeypatch):
    import trading.brokers.upstox_instruments as ui
    monkeypatch.setattr(ui, "_OVERRIDES", {})
    monkeypatch.setattr(ui, "_LOOKUP", {"BLSE.NS": "NSE_EQ|OLD", "TCS.NS": "NSE_EQ|T"})
    monkeypatch.setattr(ui, "_LOOKUP_BUILT_AT", 10 ** 12)      # fresh, no rebuild
    ui.prefer_keys({"BLSE.NS": "NSE_EQ|NEW"})
    assert ui.to_instrument_key("BLSE.NS") == "NSE_EQ|NEW"
    assert ui.to_instrument_keys(["BLSE.NS", "TCS.NS"]) == {"BLSE.NS": "NSE_EQ|NEW", "TCS.NS": "NSE_EQ|T"}
    assert ui.get_lookup()["BLSE.NS"] == "NSE_EQ|NEW"
    from trading.data import _instrument_key
    assert _instrument_key("BLSE.NS") == "NSE_EQ|NEW"
    assert ui.status()["preferred_keys"] == 1


def test_the_universe_refresh_job_detects_the_change_and_fetches_actions(held, monkeypatch):
    p, pos = held
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
    fetched = []
    monkeypatch.setattr(scheduler, "_corporate_actions_fetch_job",
                        lambda where, force=None, fetch=None, symbols=None: (fetched.append(force), {})[1])
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda where: None)
    scheduler._universe_refresh_job()
    assert fetched == [["BLSE.NS"]]
    assert any(e["action"] == "INSTRUMENT_ISIN_CHANGED" for e in p.decision_journal)


def test_apply_job_restates_the_position_and_replaces_its_history(held, monkeypatch):
    p, pos = held
    tca.set_store_reader(lambda syms: [API_ROW] if "BLSE" in syms else [])
    saved = []
    monkeypatch.setattr(db, "save_history_doc", lambda doc: saved.append(doc))
    import trading.brokers.upstox_instruments as ui
    monkeypatch.setattr(ui, "to_instrument_key", lambda t: "NSE_EQ|INE0NLT01028")

    class _Broker:
        calls = 0

        def get_daily_candles(self, key, start, end):
            _Broker.calls += 1
            if _Broker.calls > 2:
                return pd.DataFrame()
            idx = pd.bdate_range(end=pd.Timestamp(end), periods=50)
            return pd.DataFrame({"Open": 160.0, "High": 161.0, "Low": 159.0, "Close": 160.5,
                                 "Volume": 1e6}, index=idx)
    eng = state.engine()
    monkeypatch.setattr(eng, "broker", _Broker())
    try:
        applied = scheduler.apply_corporate_actions_job("test", today=date(2026, 10, 6))
    finally:
        tca.set_store_reader(None)
    assert len(applied) == 1 and pos.qty == 200 and abs(pos.entry_price - 160.08) < 1e-6
    assert p.trades == []
    assert saved and saved[0]["_id"] == "BLSE.NS" and saved[0]["n"] >= 50
    acts = [e["action"] for e in p.decision_journal]
    assert "CORPORATE_ACTION_APPLIED" in acts and "HISTORY_REFETCHED" in acts
    assert "BLSE.NS" not in scheduler._HISTORY_REFETCH_DUE
    # a queued ISIN change alone also re-fetches; a dead broker journals the failure
    scheduler._HISTORY_REFETCH_DUE.add("BLSE.NS")
    monkeypatch.setattr(eng, "broker", None)
    monkeypatch.setattr("trading.data.history_broker", lambda: None)
    scheduler.apply_corporate_actions_job("test", today=date(2026, 10, 6))
    assert p.decision_journal[-1]["action"] == "HISTORY_REFETCH_FAILED"
    assert "BLSE.NS" in scheduler._HISTORY_REFETCH_DUE       # retried next run


def test_apply_job_never_raises(held, monkeypatch):
    eng = state.engine()
    monkeypatch.setattr(eng, "apply_corporate_actions",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert scheduler.apply_corporate_actions_job("test") == []


def _code(path: Path) -> str:
    return "\n".join(ln for ln in path.read_text().splitlines() if not ln.strip().startswith("#"))


def test_the_adjuster_runs_before_every_exit_pass():
    src = _code(ROOT / "web" / "scheduler.py")
    # 09:20 job: before the top-up and the rotation
    # REPOINTED 2026-10-22 (entry-fix B-2): the job gained a `resume=`
    # parameter (a cut-short rotation's missing categories), so the
    # signature is matched up to its open bracket only.
    m = re.search(r"def _daily_multi_mode_scan\(.*?def _daily_universe_topup", src, re.S)
    body = m.group(0)
    assert body.index('apply_corporate_actions_job("09:20")') < body.index('_topup_once_today("09:20")')
    assert body.index('apply_corporate_actions_job("09:20")') < body.index("_run_category_rotation(")
    # boot catch-up: before the scan
    m = re.search(r"def _catchup_run\(\):.*?_daily_multi_mode_scan\(\)", src, re.S)
    assert 'apply_corporate_actions_job("catchup")' in m.group(0)
    # in-session pass: before engine.refresh()
    m = re.search(r"def _session_refresh\(\).*?_mark_exit_pass\(\)", src, re.S)
    body = m.group(0)
    assert body.index('apply_corporate_actions_job("session")') < body.index("state.engine().refresh()")
    # boot attaches the store reader
    st = _code(ROOT / "web" / "state.py")
    assert "set_store_reader(db.load_corporate_actions)" in st


def test_db_corporate_actions_helpers_key_by_symbol_and_ex_date(monkeypatch):
    class _Res:
        def __init__(self, up):
            self.upserted_id = up

    class _Coll:
        docs = {}

        def replace_one(self, q, doc, upsert=False):
            new = q["_id"] not in _Coll.docs
            _Coll.docs[q["_id"]] = doc
            return _Res("x" if new else None)

        def find(self, q):
            syms = (q.get("symbol") or {}).get("$in")
            rows = [d for d in _Coll.docs.values() if not syms or d["symbol"] in syms]

            class _Cur(list):
                def limit(self, n):
                    return self
            return _Cur(rows)

    class _DB:
        corporate_actions = _Coll()
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", _DB())
    assert db.save_corporate_actions([API_ROW, API_ROW]) == 1
    assert db.save_corporate_actions([API_ROW]) == 0
    rows = db.load_corporate_actions(["BLSE"])
    assert len(rows) == 1 and rows[0]["_id"].startswith("BLSE|06-Oct-2026|")
    assert db.load_corporate_actions(["TCS"]) == []
    monkeypatch.setattr(db, "_CONNECTED", False)
    assert db.load_corporate_actions(["BLSE"]) == [] and db.save_corporate_actions([API_ROW]) == 0
