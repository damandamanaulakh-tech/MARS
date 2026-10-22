"""WHY NO BUY — owner 2026-10-21: "why there is no pop for gates or book,
how do i know its blocked".

Pins: the rotation records every category pass on portfolio.rotation_report
and journals ONE ROTATION_SUMMARY line (in the phone push set); the record
survives JSON and the Mongo mirror; the dashboard card renders the engine's
own words for a real dict-keyed book; a failed pass is named, never hidden.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from trading import category_budget as cb
from trading.config import Config
from trading.paper import PaperPosition, Portfolio
from web import auth as auth_mod, push_ntfy, state, why_no_buy as wnb
from web.clock import ist_today_iso
from web.main import app


def _client(email="owner@sourceborn.in", pwd=None):
    c = TestClient(app)
    r = c.post("/login", data={"email": email, "password": pwd or auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def _res(ranked=0, above=0, picks=0, opened=(), skipped=()):
    rows = [{"ticker": f"A{i}.NS", "score": 70.0 if i < above else 50.0} for i in range(ranked)]
    return SimpleNamespace(ranked=rows, picks=[{"ticker": r["ticker"]} for r in rows[:picks]],
                           opened=list(opened), skipped=[{"ticker": "X.NS", "reason": s} for s in skipped])


# ---------------------------------------------------------------- pure record

def test_pass_record_counts_the_engines_own_result():
    res = _res(ranked=1205, above=12, picks=3, opened=(), skipped=("candle_wait_no_pattern x", "already_held", "candle_wait_no_pattern"))
    journal = [{"action": "CORRECTION_BRAKE_OFF", "why": "info"},
               {"action": "CATEGORY_BUDGET_FULL", "category": "nifty200_momentum",
                "why": "Momentum has spent its 40% share"}]
    rec = wnb.pass_record("nifty200_momentum", res, journal, 62.0,
                          {"pct": 0.48, "population": "study_panel", "sample": 189})
    assert rec["scanned"] == 1205 and rec["above_floor"] == 12 and rec["picks"] == 3
    assert rec["opened"] == 0 and rec["skipped"][0] == ["candle_wait_no_pattern", 2]
    assert [b["action"] for b in rec["blocks"]] == ["CORRECTION_BRAKE_OFF", "CATEGORY_BUDGET_FULL"]
    assert rec["breadth"]["pct"] == 0.48 and rec["label"] == "Momentum"
    # the block names the stop; the brake-off info line never does
    # REPOINTED 2026-10-22 (B-6, owner card D4): the share is counted on what
    # is left after the owner's own buys and Ocean, and the words say so.
    assert wnb.stopped_by(rec).startswith(
        "share full (of what is left after your own buys and Ocean) — Momentum has spent")


def test_stopped_by_says_floor_picks_or_bought_in_plain_words():
    assert wnb.stopped_by(wnb.pass_record("intraday", _res(ranked=1205), [], 72.0, None)) == \
        "no name cleared the floor 72 (1205 scanned)"
    assert "3 above the floor 70 but none passed" in wnb.stopped_by(
        wnb.pass_record("intraday", _res(ranked=1205, above=3), [], 70.0, None))
    assert wnb.stopped_by(wnb.pass_record("short", _res(ranked=9, above=5, picks=5, skipped=("candle_wait_no_pattern", "candle_wait_no_pattern", "already_held")), [], 70.0, None)) == \
        "5 picks refused: waiting for a candle pattern 2, already held 1"
    assert wnb.stopped_by(wnb.pass_record("swing", _res(ranked=9, above=5, picks=5, opened=("A.NS", {"ticker": "B.NS"})), [], 60.0, None)) == "bought 2"
    assert wnb.stopped_by(wnb.failed_record("positional", "KeyError: x")) == "scan failed: KeyError: x"


def test_summary_text_is_one_sentence_per_category_and_names_no_buys():
    rot = {"where": "09:20", "order": list(cb.SHARE_FIELDS), "opened_total": 0, "passes": {
        "nifty200_momentum": wnb.pass_record("nifty200_momentum", _res(ranked=10, above=4, picks=4), [
            {"action": "MEASURED_KELLY_BLOCKED", "why": "no positive edge in 31 momentum trades"}], 62.0, None),
        "intraday": wnb.pass_record("intraday", _res(ranked=10), [], 72.0, None)}}
    t = wnb.summary_text(rot)
    assert t.startswith("09:20 rotation: NO BUYS. Momentum: Kelly check: no edge — no positive edge")
    assert "Intraday: no name cleared the floor 72" in t and "Short: not run" in t
    assert "ROTATION_SUMMARY" in push_ntfy.PUSH_ACTIONS


# ---------------------------------------------------------------- the rotation writes it

def test_rotation_records_every_pass_and_journals_one_summary(tmp_path):
    from web import scheduler
    cfg = Config(state_dir=str(tmp_path), mode="swing")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    calls = []

    def fake_scan():
        calls.append(cfg.mode)
        if cfg.mode == "short":
            raise RuntimeError("boom")
        if cfg.mode == "nifty200_momentum":
            p.decision_journal.append({"ts": datetime.utcnow().isoformat(),
                                       "action": "CATEGORY_BUDGET_FULL", "category": cfg.mode,
                                       "why": "Momentum has spent its 40% share"})
        return _res(ranked=50, above=5, picks=5, skipped=("candle_wait_no_pattern",))

    eng = SimpleNamespace(portfolio=p, scan=fake_scan, refresh=lambda: None)
    with patch.object(scheduler, "is_market_open", lambda: False, create=True):
        summary = scheduler._run_category_rotation(eng, cfg, "09:20")
    assert calls == list(scheduler._DAILY_MODE_ORDER)
    rots = p.rotation_report["rotations"]
    assert len(rots) == 1 and rots[0]["where"] == "09:20" and rots[0]["date"] == ist_today_iso()
    rec = rots[0]["passes"]
    assert rec["nifty200_momentum"]["blocks"][0]["action"] == "CATEGORY_BUDGET_FULL"
    assert rec["short"]["error"].startswith("RuntimeError")
    assert rots[0]["opened_total"] == 0
    lines = [e for e in p.decision_journal if e.get("action") == "ROTATION_SUMMARY"]
    assert len(lines) == 1 and lines[0]["why"].startswith("09:20 rotation: NO BUYS.")
    assert lines[0]["categories"]["short"].startswith("scan failed")
    assert cfg.mode == "swing"            # the owner's mode restored
    # a second rotation goes in front; the record is bounded
    with patch.object(scheduler, "is_market_open", lambda: False, create=True):
        for _ in range(5):
            scheduler._run_category_rotation(eng, cfg, "14:30")
    assert [r["where"] for r in p.rotation_report["rotations"]][:2] == ["14:30", "14:30"]
    assert len(p.rotation_report["rotations"]) == wnb.KEEP_ROTATIONS


def test_the_record_survives_json_and_the_mongo_mirror(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.rotation_report = {"rotations": [{"date": "2026-10-21", "where": "09:20", "passes": {}, "opened_total": 0}]}
    p.save()
    assert Portfolio.load_or_fresh(cfg).rotation_report == p.rotation_report
    # the real mirror: state's patched Portfolio.save builds the Mongo doc
    state._patch_portfolio_save_to_mirror_mongo()
    captured = {}
    with patch.object(state.db, "save_portfolio", lambda doc, **k: captured.update(doc) or True), \
         patch.object(state.db, "is_connected", lambda: True), \
         patch.object(state.db, "archive_journal", lambda rows: True):
        p.save()
    assert captured["rotation_report"] == p.rotation_report
    q = Portfolio.fresh(cfg)
    state._hydrate_portfolio_from_mongo(q, captured)
    assert q.rotation_report == p.rotation_report


# ---------------------------------------------------------------- the screen

def _seed(cfg, with_rotation=True):
    p = Portfolio.fresh(cfg)
    p.positions["TCS.NS"] = PaperPosition(
        ticker="TCS.NS", qty=10, entry_price=3000.0, entry_date=datetime(2026, 10, 1, 4, 0),
        sector="IT", peak_price=3000.0, entry_score=70.0, reasoning="fixture",
        mode="nifty200_momentum")
    p.breadth_today = {"pct": 0.48, "population": "study_panel", "sample": 189}
    if with_rotation:
        rot = {"date": ist_today_iso(), "where": "09:20", "order": list(cb.SHARE_FIELDS),
               "opened_total": 0, "passes": {
                   "nifty200_momentum": wnb.pass_record("nifty200_momentum", _res(ranked=1205, above=263, picks=263), [
                       {"action": "CATEGORY_BUDGET_FULL", "category": "nifty200_momentum",
                        "why": "Momentum has spent its 40% share (Rs 15,991,524 of Rs 16,000,000); 263 pick(s) waited."}], 62.0, p.breadth_today),
                   "intraday": wnb.pass_record("intraday", _res(ranked=1205), [], 72.0, p.breadth_today),
                   "short": wnb.failed_record("short", "RuntimeError: boom")}}
        p.rotation_report = wnb.add_rotation(None, rot)
    p.save()
    return p


def test_dashboard_shows_the_card_with_the_engines_words(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = _seed(cfg)
    with patch.object(state, "portfolio", lambda: p), patch.object(state, "cfg", lambda: cfg):
        r = _client().get("/")
    assert r.status_code == 200
    body = r.text
    assert "why_no_buy_marker" in body and "NO BUYS" in body
    assert 'data-why-no-buy-row="nifty200_momentum"' in body
    assert "share full" in body and "263 pick(s) waited" in body
    assert "no name cleared the floor 72 (1205 scanned)" in body
    assert "scan failed: RuntimeError: boom" in body
    # REPOINTED 2026-10-22 (B-2): this seed carries no `finished` stamp and
    # no running rotation, i.e. a process died after Short — the card now says
    # so instead of the generic line.
    assert "not run (process restarted)" in body             # swing / positional never ran
    assert "cut short after Short" in body
    assert "INSIDE the 40-65% band" in body


def test_dashboard_says_when_no_rotation_ran_today(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = _seed(cfg, with_rotation=False)
    with patch.object(state, "portfolio", lambda: p), patch.object(state, "cfg", lambda: cfg):
        r = _client().get("/")
    assert r.status_code == 200
    assert "why_no_buy_marker" in r.text and 'data-why-no-buy-none="1"' in r.text
    assert "No rotation recorded today" in r.text


def test_viewer_sees_the_card_too(tmp_path, monkeypatch):
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    cfg = Config(state_dir=str(tmp_path))
    p = _seed(cfg)
    with patch.object(state, "portfolio", lambda: p), patch.object(state, "cfg", lambda: cfg):
        c = TestClient(app)
        r = c.post("/login", data={"email": "viewer@sourceborn.in", "password": "v-test"})
        assert r.status_code == 200
        r = c.get("/")
    assert r.status_code == 200 and "why_no_buy_marker" in r.text
