"""C-batch guards (owner: "fix C and D too") — silent-death risks from the
2026-08-09 audit. Each test fails if a failure becomes invisible again.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from trading.config import Config
from trading.paper import Portfolio


# ------------------------------------------------ C1: Mongo retries + heals

def test_db_retries_a_dead_connection_with_cooldown(monkeypatch):
    from web import db
    monkeypatch.setenv("MONGO_URL", "mongodb://nowhere.invalid:27017")
    monkeypatch.setattr(db, "_CONNECTED", False)
    monkeypatch.setattr(db, "_LAST_ATTEMPT", 0.0)
    calls = []
    monkeypatch.setattr(db, "_try_connect", lambda: calls.append(1))
    db.ensure_connected()
    db.ensure_connected()          # inside cooldown — must NOT retry again
    assert len(calls) == 1, "reconnect ignored the cooldown (would hammer Atlas)"
    monkeypatch.setattr(db, "_LAST_ATTEMPT", 0.0)
    db.ensure_connected()
    assert len(calls) == 2, "reconnect never retries — the C1 outage bug is back"


def test_degraded_status_is_reported(monkeypatch):
    from web import db
    monkeypatch.setenv("MONGO_URL", "mongodb://nowhere.invalid:27017")
    monkeypatch.setattr(db, "_CONNECTED", False)
    monkeypatch.setattr(db, "_LAST_ATTEMPT", 9e18)   # suppress retry in test
    st = db.status()
    assert st["degraded"] is True


def test_scans_halt_while_mongo_is_expected_but_down(monkeypatch):
    """The phantom-book scenario: boot during an Atlas blip must not TRADE."""
    from web import db, scheduler, state
    monkeypatch.setenv("MONGO_URL", "mongodb://nowhere.invalid:27017")
    monkeypatch.setattr(db, "_CONNECTED", False)
    monkeypatch.setattr(db, "_LAST_ATTEMPT", 9e18)
    assert state.mongo_degraded() is True
    assert scheduler._degraded_halt("test") is True
    p = state.portfolio()
    assert any(e.get("action") == "MONGO_OFFLINE_HALT"
               for e in p.decision_journal[-5:])


# ------------------------------------------- C2: journal overflow never lost

def test_failed_archive_spills_to_disk_and_replays(tmp_path, monkeypatch):
    from web import db, state
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    entries = [{"ts": "2026-08-09T00:00:00", "action": "TEST", "i": i}
               for i in range(5)]
    monkeypatch.setattr(db, "archive_journal", lambda e: False)   # Mongo dead
    state._spill_journal_overflow(p, entries)
    spill = Path(state._overflow_path(p))
    assert spill.exists()
    lines = [json.loads(x) for x in spill.read_text().splitlines()]
    assert len(lines) == 5 and lines[3]["i"] == 3


def test_save_wrapper_spills_when_archive_fails():
    src = Path("web/state.py").read_text()
    body = src.split("def _save_with_mirror")[1].split("\n    Portfolio.save")[0]
    assert "_spill_journal_overflow" in body, \
        "journal overflow is silently dropped again when Mongo dies"


# --------------------------------------------------- C3: rotation concurrency

def test_two_rotations_cannot_run_at_once():
    src = Path("web/scheduler.py").read_text()
    assert "_ROTATION_LOCK.acquire(blocking=False)" in src
    body = src.split("def _daily_multi_mode_scan")[1]
    assert "finally:" in body and "_ROTATION_LOCK.release()" in body
    after_loop = body.split("for m in _DAILY_MODE_ORDER")[1]
    # the restore sits in the loop's outer finally (nested since audit S-6,
    # whose own finally releases the mode lock held across the rotation)
    outer = after_loop.split("    finally:\n        # AUDIT FIX C3")[1]
    assert "cfg.mode = original_mode" in outer, \
        "the mode restore is not in a finally — an escape leaves cfg poisoned"
    assert "MODE_LOCK.release()" in outer.split("finally:")[-1]


def test_mode_lock_exists_and_rotation_uses_it():
    from trading.modes import MODE_LOCK
    assert MODE_LOCK is not None
    src = Path("web/scheduler.py").read_text()
    assert "with MODE_LOCK:" in src


# --------------------------------------------------- C4/C5: pinned + present

def test_every_requirement_is_pinned():
    for line in Path("requirements.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "==" in line, f"unpinned dependency: {line}"


def test_feedparser_is_a_dependency_and_importable():
    assert "feedparser==" in Path("requirements.txt").read_text()
    import feedparser  # noqa: F401  (the policy boost can finally run)


def test_pli_matches_the_word_not_compliance():
    from trading.signals import policy
    import re
    src = Path("trading/signals/policy.py").read_text()
    assert r"\bPLI\b" in src, "the substring match (comPLIance -> boost) is back"


# --------------------------------------------------------- C6: gitignore traps

def test_code_referenced_artifacts_are_never_gitignored():
    import subprocess
    candidates = [
        "docs/example_future_artifact.csv",
        "research/tunnel_studies/new_reading.csv",
        "scripts/new_study_output.csv",
        "docs/new_progress.jsonl",
    ]
    out = subprocess.run(["git", "check-ignore", *candidates],
                         capture_output=True, text=True, cwd=".")
    assert out.stdout.strip() == "", \
        f"these would be silently untracked (the trigger-CSV bug class): {out.stdout}"


# ----------------------------------------------------- C7: regime fails STICKY

def test_regime_feed_failure_keeps_the_correction_brake(monkeypatch, tmp_path):
    from trading.engine import TradingEngine
    cfg = Config(state_dir=str(tmp_path))
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.last_regime = "correction"
    eng = TradingEngine(cfg, p)
    monkeypatch.setattr("trading.engine.fetch_daily", lambda *a, **k: {})
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda *a, **k: {})
    import trading.regime as regime_mod
    monkeypatch.setattr(regime_mod, "fetch_regime",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("feed down")))
    res = eng.scan()
    assert res.opened == []
    assert any(e.get("action") == "REGIME_FEED_DOWN"
               for e in p.decision_journal), \
        "a dead regime feed no longer announces itself"


# -------------------------------------------- C8: truncated history must raise

def test_history_window_failure_raises_after_retry():
    class _Broker:
        calls = 0
        def get_daily_candles(self, key, s, e):
            self.calls += 1
            raise ConnectionError("blip")
    from trading.history_store import download_ticker_history
    with pytest.raises(ConnectionError):
        download_ticker_history(_Broker(), "NSE_EQ|X", years=3,
                                today=date(2026, 8, 9))


# ------------------------------------------ C9: blocked filings feed is a FAIL

def test_blocked_bse_feed_raises_instead_of_reporting_success(monkeypatch):
    import trading.filings as fl

    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"Table": []}          # cloud-blocked shape

    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): return _R()

    monkeypatch.setattr("httpx.Client", lambda **k: _C())
    with pytest.raises(RuntimeError):
        fl.fetch_announcements(date(2026, 8, 8), date(2026, 8, 9))


def test_failed_job_actions_reach_the_phone():
    from web.push_ntfy import PUSH_ACTIONS
    for action in ("UNIVERSE_REFRESH_FAILED", "FILINGS_FAILED",
                   "DATA_REFRESH_FAILED", "TRIGGER_LAYER_FAILED",
                   "MONGO_OFFLINE_HALT", "CORRUPT_STATE_RESET"):
        assert action in PUSH_ACTIONS, f"{action} fails silently again"


# --------------------------------------- C10: no dead protection in the client

def test_the_dead_isin_symbol_fallback_is_gone():
    src = Path("trading/brokers/upstox.py").read_text()
    assert 'k.split("|")[-1].strip().upper() == sym' not in src


def test_history_instrument_map_rides_the_loud_shared_lookup():
    src = Path("trading/data.py").read_text()
    body = src.split("def _instrument_key")[1].split("\ndef ")[0]
    assert "get_lookup" in body


# ------------------------------------------------ C11: halts survive restarts

def test_loss_halt_state_round_trips_through_storage(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.loss_day = "2026-08-09"
    p.loss_count = 2
    p.save()
    back = Portfolio.load_or_fresh(cfg)
    assert back.loss_day == "2026-08-09" and back.loss_count == 2


def test_engine_hydrates_the_loss_tracker(tmp_path):
    from trading.engine import TradingEngine
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.loss_day = date.today().isoformat()
    p.loss_count = 2
    eng = TradingEngine(cfg, p)
    assert eng.loss_tracker.halted(today=date.today()) is True, \
        "a restart forgot the 2-loss halt on exactly the day it exists for"


def test_loss_fields_ride_the_mongo_mirror():
    src = Path("web/state.py").read_text()
    hydrate = src.split("def _hydrate_portfolio_from_mongo")[1].split("\ndef ")[0]
    assert "loss_day" in hydrate and "loss_count" in hydrate
    mirror = src.split("def _patch_portfolio_save_to_mirror_mongo")[1]
    assert '"loss_day"' in mirror and '"loss_count"' in mirror


# --------------------------------------------- C12: corrupt state is loud

def test_corrupt_state_file_is_quarantined_and_journaled(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    bad = Path(tmp_path) / "portfolio.json"
    bad.write_text("{ this is not json !!!")
    p = Portfolio.load_or_fresh(cfg)
    assert any(e.get("action") == "CORRUPT_STATE_RESET"
               for e in p.decision_journal), "silent book reset is back"
    quarantined = list(Path(tmp_path).glob("portfolio.json.corrupt-*"))
    assert quarantined, "the corrupt file evidence was destroyed"
