"""Item 19, the web half (owner order 2026-10-21, "Wire A3" + "Exit as
tested"): the valuation records the exit target reads, the level / record
readers, the owner's Den button, the /positions chip, the Den status and
the Mongo mirror of PaperPosition.val_target.

Pins: the precompute stores the RECORD shape (the fields the study's
harness read) inside its budget; it skips — and says why — under the
rotation lock or above the memory ceiling; the boot catch-up only runs in
the evening (or on a holiday) when the records are older than a trading
day; the owner button runs it and a viewer gets 403; the chip renders for
a real PaperPosition in the live DICT; the readers return None / {}
without Mongo; val_target survives the Mongo hydrate AND the save mirror.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trading import valuation_exit as VE
from trading.paper import PaperPosition, Portfolio
from web import auth as auth_mod
from web import scheduler as S
from web import state
from web import valuation_chain_view as cv
from web import valuation_exit_feed as feed
from web.main import app

RECORD_KEYS = {"ticker", "asof", "verdict", "range_status", "base_target", "low",
               "high", "family", "method", "sector_index", "beta", "beta_n",
               "version", "drafted", "price", "note"}


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303
    return c


@pytest.fixture
def docs(monkeypatch):
    """Settings docs in memory; the in-process records copy cleared."""
    store: dict = {}
    monkeypatch.setattr(feed.db, "save_settings",
                        lambda d, doc_id="default": store.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(feed.db, "load_settings",
                        lambda doc_id="default": dict(store.get(doc_id, {})))
    feed.clear_cache()
    yield store
    feed.clear_cache()


def _series(n=300, start=100.0, seed=1, end="2026-10-20"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    return pd.Series(start * np.cumprod(1 + rng.normal(0, 0.01, n)), index=idx)


def _chain_row(ticker, verdict="attractive", base=4500.0, status="OK", price=4000.0):
    return {"ticker": ticker, "version": "2026-10-14", "price": price,
            "price_date": "2026-10-20", "drafted": False, "price_source": "store",
            "route": {"method": "pe", "family": "earnings"},
            "range": {"status": status, "low": base * 0.8, "base": base,
                      "high": base * 1.2, "family": "earnings"},
            "verdict": {"verdict": verdict}}


@pytest.fixture
def chain(monkeypatch):
    """The Den machinery the precompute reuses, with fixed inputs."""
    sensex = _series(seed=7)
    closes = {"TCS.NS": sensex * 0.05 + _series(seed=8) * 0.0 + 4000.0,
              "DLF.NS": _series(seed=9) * 7}
    calls = []

    def row_for(t, cfg, p, *, closes=None, price_source="", **k):
        calls.append(t)
        if t == "BAD.NS":
            raise RuntimeError("boom")
        return _chain_row(t, verdict="watch" if t == "DLF.NS" else "attractive",
                          base=800.0 if t == "DLF.NS" else 4500.0)
    monkeypatch.setattr(cv, "row_for", row_for)
    monkeypatch.setattr(cv, "_prefetch_closes",
                        lambda names: {t: closes[t] for t in names if t in closes})
    monkeypatch.setattr(cv, "_zip_close_series", lambda t: None)
    monkeypatch.setattr(cv, "_index_series",
                        lambda field: sensex.copy() if field == "sensex_close" else None)
    monkeypatch.setattr(feed, "precompute_scope",
                        lambda p, cap: (["TCS.NS", "DLF.NS", "BAD.NS"], []))
    return calls


def _own_entries(before: int, action: str) -> list:
    return [e for e in state.portfolio().decision_journal[before:]
            if e.get("action") == action]


# ------------------------------------------------------------ the job

def test_the_precompute_stores_the_record_shape(docs, chain):
    n0 = len(state.portfolio().decision_journal)
    res = S.run_valuation_precompute("test")
    assert res["status"] == "ok"
    doc = docs[feed.DOC_ID]
    rows = doc["rows"]
    assert set(rows) == {"TCS.NS", "DLF.NS"}                 # BAD.NS crashed
    r = rows["TCS.NS"]
    assert set(r) == RECORD_KEYS
    assert r["asof"] == "2026-10-20" and r["verdict"] == "attractive"
    assert r["range_status"] == "OK" and r["base_target"] == 4500.0
    assert r["low"] == 3600.0 and r["high"] == 5400.0
    assert r["family"] == "earnings" and r["method"] == "pe"
    assert r["sector_index"] == "IT"                          # cfg.sector_map → Nifty IT
    assert rows["DLF.NS"]["sector_index"] == "Realty"         # the realty override
    assert r["beta"] is not None and r["beta_n"] == 252       # 252 paired sessions
    assert r["drafted"] is False and r["version"] == "2026-10-14"
    assert doc["n"] == 2 and doc["n_judged"] == 2 and doc["asof"] == "2026-10-20"
    assert doc["skipped"] == {"budget": 0, "failed": 1, "cap": 0}
    assert feed.LAST["rows"]["TCS.NS"]["base_target"] == 4500.0
    j = _own_entries(n0, "VALUATION_PRECOMPUTE")
    assert len(j) == 1 and j[0]["n"] == 2 and j[0]["n_judged"] == 2
    assert j[0]["where"] == "test" and j[0]["elapsed_s"] >= 0
    assert j[0]["stored"] is True                             # read back
    # the engine's reader now serves it
    assert VE.records_for(["TCS.NS"])["TCS.NS"]["base_target"] == 4500.0


def test_names_past_the_budget_keep_their_previous_record(docs, chain, monkeypatch):
    feed.store({"rows": {"DLF.NS": {"ticker": "DLF.NS", "base_target": 1.0}},
                "built": "2026-10-19T11:20:00"})
    orig = cv.row_for

    def slow(t, *a, **k):
        time.sleep(0.15)
        return orig(t, *a, **k)
    monkeypatch.setattr(cv, "row_for", slow)
    monkeypatch.setenv("SB_VALUATION_PRECOMPUTE_BUDGET_S", "0.1")
    n0 = len(state.portfolio().decision_journal)
    res = S.run_valuation_precompute("test")
    assert res["stopped"] == "the 0.1 s budget"
    rows = docs[feed.DOC_ID]["rows"]
    assert rows["TCS.NS"]["base_target"] == 4500.0            # computed first
    assert rows["DLF.NS"] == {"ticker": "DLF.NS", "base_target": 1.0}   # kept
    assert "BAD.NS" not in rows                               # none before, none now
    assert docs[feed.DOC_ID]["skipped"]["budget"] == 2
    assert "STOPPED" in _own_entries(n0, "VALUATION_PRECOMPUTE")[0]["why"]


def test_it_skips_while_a_scan_rotation_holds_the_lock(docs, chain):
    n0 = len(state.portfolio().decision_journal)
    assert S._ROTATION_LOCK.acquire(timeout=5)
    try:
        res = S.run_valuation_precompute("test")
    finally:
        S._ROTATION_LOCK.release()
    assert res["status"] == "skipped" and res["reason"] == "busy"
    assert feed.DOC_ID not in docs and chain == []
    j = _own_entries(n0, "VALUATION_PRECOMPUTE_SKIPPED")
    assert len(j) == 1 and j[0]["reason"] == "busy"


def test_it_skips_above_the_memory_ceiling(docs, chain, monkeypatch):
    monkeypatch.setattr(cv, "rss_ceiling_mb", lambda: 380.0)
    import web.memory_relief as mr
    monkeypatch.setattr(mr, "rss_mb", lambda: 420.0)
    n0 = len(state.portfolio().decision_journal)
    res = S.run_valuation_precompute("test")
    assert res["status"] == "skipped" and res["reason"] == "memory"
    assert feed.DOC_ID not in docs and chain == []
    assert "420 MiB" in _own_entries(n0, "VALUATION_PRECOMPUTE_SKIPPED")[0]["why"]


def test_the_scope_is_held_then_shortlist_by_score_then_picks_then_targets(monkeypatch):
    class _P:
        positions = {"HELD.NS": object()}
        momentum_shortlist = {"score_floor": 62.0, "rows": {
            "LOW.NS": {"score": 61.0}, "B.NS": {"score": 70.0}, "A.NS": {"score": 80.0}}}
        last_scan_picks = [{"ticker": "PICK.NS"}, {"ticker": "A.NS"}]
    from web import valuation_view as vv
    monkeypatch.setattr(vv, "load_doc", lambda: {"targets": {"TGT.NS": {}}})
    names, cut = feed.precompute_scope(_P(), 120)
    assert names == ["HELD.NS", "A.NS", "B.NS", "PICK.NS", "TGT.NS"] and cut == []
    names, cut = feed.precompute_scope(_P(), 2)
    assert names == ["HELD.NS", "A.NS"] and cut == ["B.NS", "PICK.NS", "TGT.NS"]


# ---------------------------------------------------------- boot catch-up

class _IST:
    def __init__(self, y, m, d, hh, mm):
        self._d = datetime(y, m, d, hh, mm)

    def date(self):
        return self._d.date()

    @property
    def hour(self):
        return self._d.hour

    @property
    def minute(self):
        return self._d.minute


def test_the_boot_catchup_runs_only_in_the_evening_on_stale_records(docs, monkeypatch):
    monkeypatch.delenv("SB_DISABLE_CATCHUP", raising=False)
    ran = []
    monkeypatch.setattr(S, "run_valuation_precompute",
                        lambda where="manual": ran.append(where) or {"status": "ok"})
    # Friday 10-16's records on Wed 10-21 (Tue 10-20 is Dussehra): stale
    feed.store({"rows": {"X.NS": {}}, "built": "2026-10-16T11:20:00"})
    # 10:00 IST: the morning / afternoon chains get no new load
    assert S.valuation_catchup_if_stale("catchup", _IST(2026, 10, 21, 10, 0)) is False
    assert ran == []
    # 16:00 IST: runs
    assert S.valuation_catchup_if_stale("catchup", _IST(2026, 10, 21, 16, 0)) is True
    assert ran == ["catchup"]
    # Thu 16:00 with Wednesday's records: fresh, nothing runs
    feed.store({"rows": {"X.NS": {}}, "built": "2026-10-21T11:20:00"})
    assert S.valuation_catchup_if_stale("catchup", _IST(2026, 10, 22, 16, 0)) is False
    # Sunday morning with Thursday's records: a holiday, stale -> runs
    feed.store({"rows": {"X.NS": {}}, "built": "2026-10-22T11:20:00"})
    assert S.valuation_catchup_if_stale("catchup", _IST(2026, 10, 25, 9, 0)) is True
    assert ran == ["catchup", "catchup"]


def test_the_catchup_is_off_under_the_test_switch(docs, monkeypatch):
    monkeypatch.setenv("SB_DISABLE_CATCHUP", "1")
    assert S.valuation_catchup_if_stale("catchup", _IST(2026, 10, 21, 16, 0)) is False


def test_the_staleness_rule_counts_trading_days():
    # Dussehra 2026-10-20 is a holiday: on Wed 10-21 the last trading day is Mon 10-19
    assert feed.previous_trading_day(date(2026, 10, 21)) == date(2026, 10, 19)
    assert not feed.records_stale(date(2026, 10, 19), date(2026, 10, 21))
    assert feed.records_stale(date(2026, 10, 16), date(2026, 10, 21))
    assert feed.records_stale(None, date(2026, 10, 21))


def test_the_job_is_registered_after_the_register_precompute_and_in_the_week_block():
    import inspect
    src = inspect.getsource(S.start_if_enabled)
    i = src.index("run_register_precompute")
    j = src.index("_valuation_precompute_job")
    assert j > i and 'minute=50' in src[j - 200:j + 200]
    assert "valuation_catchup_if_stale" in src
    assert '("valuation precompute", lambda: run_valuation_precompute("post-topup"))' \
        in inspect.getsource(S._weekly_history_topup)


# ------------------------------------------------------------ readers

def test_the_readers_fall_back_to_none_without_mongo(docs, monkeypatch):
    import web.db as db
    monkeypatch.setattr(db, "_CONNECTED", False)
    assert feed.records_for(["TCS.NS"]) == {}
    assert feed.level("sector", "IT") is None
    assert feed.level("sector", None) is None
    assert feed.level("bogus") is None
    # the index legs read the committed file: a level, never invented for a
    # day the file has not reached
    s = feed.level("sensex")
    assert s is not None and s > 0
    assert feed.level("sensex", None, "2099-01-01") is None
    assert feed.level("sensex", None, "2026-01-02") > 0
    # the readers attached at boot are these functions
    state.cfg()
    assert VE._RECORD_READER is feed.records_for and VE._LEVEL_READER is feed.level


def test_an_empty_store_is_not_asked_again_on_every_pass(docs, monkeypatch):
    asked = []
    monkeypatch.setattr(feed.db, "load_settings",
                        lambda doc_id="default": asked.append(doc_id) or {})
    feed.records_for(["A.NS"])
    feed.records_for(["A.NS"])
    assert asked == [feed.DOC_ID]


# ------------------------------------------------------- persistence

def _val_pos(ticker="ZZVAL.NS"):
    pos = PaperPosition(ticker=ticker, qty=10, entry_price=1000.0,
                        entry_date=datetime(2026, 10, 21), sector="IT",
                        peak_price=1000.0, entry_score=70.0, reasoning="test",
                        mode="nifty200_momentum", stop_price=950.0,
                        target_price=1234.5)
    pos.val_target = {"source": "valuation", "base": 1234.5, "asof": "2026-10-20",
                      "verdict": "attractive", "family": "earnings", "t_entry": 1234.5,
                      "remarks": [{"month": "2026-10", "target": 1200.0}],
                      "last_remark_month": "2026-10", "exit_due": False}
    return pos


def test_val_target_survives_the_mongo_hydrate_and_the_save_mirror(tmp_path, monkeypatch):
    state.cfg()                                   # boot: save() is the mirror wrapper
    pos = _val_pos()
    doc = {"cash": 1.0, "positions": {pos.ticker: pos.to_dict()}}
    from trading.config import Config
    p2 = Portfolio.fresh(Config(state_dir=str(tmp_path)))
    state._hydrate_portfolio_from_mongo(p2, doc)
    assert p2.positions[pos.ticker].val_target == pos.val_target
    # a legacy Mongo doc without the field reads {}
    legacy = pos.to_dict()
    legacy.pop("val_target")
    state._hydrate_portfolio_from_mongo(p2, {"positions": {pos.ticker: legacy}})
    assert p2.positions[pos.ticker].val_target == {}
    # and the save wrapper writes it
    import web.db as db
    sent = {}
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "save_portfolio", lambda d, doc_id="default": sent.update(d))
    p2.positions[pos.ticker] = pos
    p2.save()
    assert sent["positions"][pos.ticker]["val_target"] == pos.val_target


# ------------------------------------------------------------ screens

def test_the_positions_chip_renders_for_a_real_position_in_the_live_dict():
    p = state.portfolio()
    vpos = _val_pos("ZZVAL.NS")
    kpos = _val_pos("ZZKATR.NS")
    kpos.val_target = {"source": "k_atr", "reason": "not_judged:unattractive"}
    lpos = PaperPosition.from_dict({
        "ticker": "ZZOLD.NS", "qty": 5, "entry_price": 500.0, "entry_date": "2026-09-01",
        "sector": "IT", "peak_price": 500.0, "entry_score": 0, "reasoning": "old",
        "mode": "", "stop_price": 475.0, "target_price": 600.0})
    added = {"ZZVAL.NS": vpos, "ZZKATR.NS": kpos, "ZZOLD.NS": lpos}
    p.positions.update(added)
    try:
        html = _owner().get("/positions").text
    finally:
        for t in added:
            p.positions.pop(t, None)
    assert "val_target_marker" in html
    assert 'data-val-target="valuation"' in html
    assert "VAL base ₹1,234.50 · asof 2026-10-20 · attractive" in html
    assert "re-marked 2026-10 → ₹1,200.00" in html
    assert 'data-val-target="k_atr"' in html and "not_judged:unattractive" in html
    assert 'data-val-target="legacy"' in html
    assert 'data-target-source-legend="1"' in html


def test_the_den_shows_the_records_and_stage_21(docs):
    from web.routes import den_routes as D
    feed.store({"rows": {"TCS.NS": {"verdict": "attractive"}}, "n_judged": 1,
                "built": "2026-10-20T11:20:00", "asof": "2026-10-20",
                "where": "16:50", "elapsed_s": 12.5, "skipped": {}})
    html = _owner().get("/den").text
    assert "den_valexit_marker" in html and "den_valexit_form_marker" in html
    assert "<strong>1</strong> name(s), <strong>1</strong> judged" in html
    rows = {r["code"]: r for r in D._sequence()["rows"]}
    assert rows["21"]["status"] == "exists" and D.SEQUENCE_STATUS_ASOF == "2026-10-21"
    assert 'name="valuation_exit_enabled"' in html


def test_the_owner_button_runs_it_and_a_viewer_gets_403(docs, monkeypatch):
    ran = []
    monkeypatch.setattr(S, "run_valuation_precompute",
                        lambda where="manual": ran.append(where) or {"status": "ok"})
    r = _owner().post("/den/valuation/precompute", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/den?vp=ok#chain"
    assert ran == ["owner-button"]
    assert "Valuation records rebuilt" in _owner().get("/den?vp=ok").text
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    c = TestClient(app)
    assert c.post("/login", data={"email": "viewer@sourceborn.in", "password": "v-test"},
                  follow_redirects=False).status_code == 303
    assert c.post("/den/valuation/precompute", follow_redirects=False).status_code == 403
    assert ran == ["owner-button"]


def test_the_switch_row_and_the_horizons_line():
    from web.routes import settings_routes as SR
    row = next(r for r in SR.EDITABLE_FIELDS if r[0] == "valuation_exit_enabled")
    assert row[2] == "Risk" and "VAL_TARGET_DOWN" in row[5] and "KEEP" in row[5]
    html = _owner().get("/").text
    assert 'data-horizon-valuation="1"' in html
    assert "momentum target = valuation base when judged (owner 2026-10-21), else k×ATR" in html


# ------------------------------------------- review fixes (2026-10-21)

def test_the_1650_job_waits_for_a_running_register_precompute(docs, chain):
    """The register precompute peaks ~350 MiB at 16:45-16:55 on the live
    box; the 16:50 job runs after it, never on top of it."""
    order: list = []
    started = threading.Event()

    def register():
        with S._PRECOMPUTE_LOCK:
            started.set()
            time.sleep(0.3)
            order.append("register done")
    th = threading.Thread(target=register)
    th.start()
    assert started.wait(5)
    res = S.run_valuation_precompute("16:50")
    order.append("valuation done")
    th.join()
    assert res["status"] == "ok" and chain == ["TCS.NS", "DLF.NS", "BAD.NS"]
    assert order == ["register done", "valuation done"]


def test_the_owner_button_never_waits_for_the_register_precompute(docs, chain):
    n0 = len(state.portfolio().decision_journal)
    assert S._PRECOMPUTE_LOCK.acquire(timeout=5)
    try:
        t0 = time.monotonic()
        res = S.run_valuation_precompute("owner-button")
        waited = time.monotonic() - t0
    finally:
        S._PRECOMPUTE_LOCK.release()
    assert waited < 2.0
    assert res["status"] == "skipped" and res["reason"] == "register"
    assert chain == [] and feed.DOC_ID not in docs
    assert _own_entries(n0, "VALUATION_PRECOMPUTE_SKIPPED")[0]["reason"] == "register"
    assert not S._VALUATION_LOCK.locked()


def test_the_register_precompute_holds_the_shared_lock(monkeypatch):
    seen: list = []
    monkeypatch.setattr(S, "_register_precompute_body",
                        lambda where="manual": seen.append(S._PRECOMPUTE_LOCK.locked()) or {})
    assert S.run_register_precompute("test") == {}
    assert seen == [True] and not S._PRECOMPUTE_LOCK.locked()


def test_a_hung_holder_never_stalls_the_register_precompute_for_ever(monkeypatch):
    monkeypatch.setattr(S, "REGISTER_WAIT_S", 0.1)
    ran: list = []
    monkeypatch.setattr(S, "_register_precompute_body",
                        lambda where="manual": ran.append(where) or {})
    assert S._PRECOMPUTE_LOCK.acquire(timeout=5)
    try:
        assert S.run_register_precompute("post-topup") == {}
        assert S._PRECOMPUTE_LOCK.locked()          # still the holder's
    finally:
        S._PRECOMPUTE_LOCK.release()
    assert ran == ["post-topup"]


def test_a_rotation_that_starts_mid_run_stops_it_at_the_next_chunk(docs, chain, monkeypatch):
    monkeypatch.setattr(S, "VALUATION_PRECOMPUTE_CHUNK", 1)
    feed.store({"rows": {"DLF.NS": {"ticker": "DLF.NS", "base_target": 1.0}},
                "built": "2026-10-19T11:20:00"})
    orig = cv.row_for

    def first_name_then_the_rotation_starts(t, *a, **k):
        out = orig(t, *a, **k)
        S._ROTATION_LOCK.acquire(blocking=False)
        return out
    monkeypatch.setattr(cv, "row_for", first_name_then_the_rotation_starts)
    try:
        res = S.run_valuation_precompute("test")
    finally:
        if S._ROTATION_LOCK.locked():
            S._ROTATION_LOCK.release()
    assert res["status"] == "ok" and chain == ["TCS.NS"]
    assert res["stopped"] == "a scan rotation is running"
    rows = docs[feed.DOC_ID]["rows"]
    assert rows["TCS.NS"]["base_target"] == 4500.0
    assert rows["DLF.NS"] == {"ticker": "DLF.NS", "base_target": 1.0}   # kept
    assert "BAD.NS" not in rows
    assert docs[feed.DOC_ID]["skipped"]["budget"] == 2


def test_memory_that_climbs_mid_run_stops_it_at_the_next_chunk(docs, chain, monkeypatch):
    monkeypatch.setattr(S, "VALUATION_PRECOMPUTE_CHUNK", 1)
    monkeypatch.setattr(cv, "rss_ceiling_mb", lambda: 380.0)
    import web.memory_relief as mr
    rss = {"mb": 300.0}
    monkeypatch.setattr(mr, "rss_mb", lambda: rss["mb"])
    orig = cv.row_for

    def climbs(t, *a, **k):
        rss["mb"] = 400.0
        return orig(t, *a, **k)
    monkeypatch.setattr(cv, "row_for", climbs)
    res = S.run_valuation_precompute("test")
    assert res["status"] == "ok" and chain == ["TCS.NS"]
    assert "400 MiB" in res["stopped"]


def test_it_waits_for_a_computing_den_open_then_skips(docs, chain, monkeypatch):
    monkeypatch.setattr(S, "VALUATION_WAIT_FOR_DEN_S", 0.1)
    assert cv._SECTION_LOCK.acquire(timeout=5)
    try:
        res = S.run_valuation_precompute("test")
    finally:
        cv._SECTION_LOCK.release()
    assert res["status"] == "skipped" and res["reason"] == "den"
    assert chain == [] and feed.DOC_ID not in docs
    assert not S._PRECOMPUTE_LOCK.locked() and not S._VALUATION_LOCK.locked()


def test_a_write_that_does_not_land_is_said(chain, monkeypatch):
    """db.save_settings logs a refused write and returns — the journal
    must not claim the records were stored."""
    monkeypatch.setattr(feed.db, "save_settings", lambda d, doc_id="default": None)
    monkeypatch.setattr(feed.db, "load_settings", lambda doc_id="default": {})
    feed.clear_cache()
    n0 = len(state.portfolio().decision_journal)
    try:
        res = S.run_valuation_precompute("test")
        assert res["status"] == "ok"
        j = _own_entries(n0, "VALUATION_PRECOMPUTE")[0]
        assert j["stored"] is False and "NOT saved to the database" in j["why"]
        # this process still serves them
        assert feed.LAST["rows"]["TCS.NS"]["base_target"] == 4500.0
    finally:
        feed.clear_cache()


# ------------------------------- skeptic review (2026-10-21): the switch saves

def test_the_switch_saves_while_the_live_universe_is_above_its_target():
    """The §9 form always posts the universe box as rendered. The live list
    has held 1,205 names since the 09-30 rebuild (cap 1,200), so every save
    was refused universe_too_big — the valuation-exit switch could not be
    turned off. An unchanged box is not an edit; the other fields save."""
    cfg = state.cfg()
    old_u, old_on = cfg.custom_universe, cfg.valuation_exit_enabled
    live = tuple(f"ZZLIVE{i:04d}.NS" for i in range(1205))   # not in the file either
    cfg.custom_universe = live
    cfg.valuation_exit_enabled = True
    try:
        box = ", ".join(t.replace(".NS", "") for t in live)
        r = _owner().post("/settings", data={"custom_universe": box,
                                             "valuation_exit_enabled": "0"},
                          follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/settings?saved=ok"
        assert state.cfg().valuation_exit_enabled is False
        assert tuple(state.cfg().custom_universe) == live
        # an EDIT is still checked: one name added past the cap is refused
        r = _owner().post("/settings", data={"custom_universe": box + ", TCS",
                                             "valuation_exit_enabled": "1"},
                          follow_redirects=False)
        assert "saved=universe_too_big" in r.headers["location"]
        assert state.cfg().valuation_exit_enabled is False      # nothing saved
    finally:
        state.cfg().custom_universe = old_u
        state.cfg().valuation_exit_enabled = old_on
