"""The universe rebuild must not sit behind the hour-long OHLCV pull.

Owner fault report 2026-09-11: "why system still stucking at 49 ... when its
decided that 1200 is the count".

THE EVIDENCE it never ran: the live /healthz reported history_tickers = 573
on 2026-08-29 and the SAME 573 twelve days later, while custom_universe was
still the hand-typed 49. `refresh_liquid_universe` sat AFTER a ~61-minute
pull — the exact position that cost five other feeds a week each and that
they were moved out of on 2026-08-29 — and unlike its two remaining
post-pull neighbours it has no daily counterpart, so a pull cut short by a
container restart meant the list was never rebuilt at all.
"""
from __future__ import annotations

import inspect

from web import scheduler


def _src() -> str:
    """Operative code only — comment lines stripped.

    This exact test shape has tripped on its own explanatory prose four
    times in this repo (the F1 test, the /positions hunt, the
    DAILY_SCHEDULER guard, the watchlist source-pin).
    """
    raw = inspect.getsource(scheduler._weekly_history_topup).splitlines()
    return "\n".join(ln.split("#")[0] for ln in raw
                     if not ln.strip().startswith("#"))


def test_the_rebuild_runs_before_the_ohlcv_pull():
    src = _src()
    first_rebuild = src.find("refresh_liquid_universe")
    pull = src.find("top_up_history")
    assert first_rebuild != -1 and pull != -1
    assert first_rebuild < pull, (
        "the universe rebuild must run BEFORE the hour-long pull — behind "
        "it, a restart means it never runs at all")


def test_it_also_runs_after_the_pull_to_pick_up_new_names():
    src = _src()
    assert src.count("refresh_liquid_universe") >= 2, (
        "the pre-pull run guarantees it happens; the post-pull run picks up "
        "whatever the pull just landed")


def test_the_pull_is_time_bounded():
    """Unbounded, it needs ~61 uninterrupted minutes and never completes."""
    assert "time_budget_s" in _src()


def test_the_block_stays_due_while_the_pull_still_owes_names(monkeypatch):
    """A bounded pull that stops early must NOT stamp 'done' and then wait
    seven days — that would be the same bug in a new costume."""
    from datetime import date

    from web import db
    monkeypatch.setattr(db, "load_settings", lambda doc_id=None: {
        scheduler.WEEKLY_BLOCK_KEY: date.today().isoformat(),
        scheduler.WEEKLY_BLOCK_LEFT_KEY: 900,
    })
    due, why = scheduler.weekly_block_due()
    assert due is True
    assert "900" in why


def test_a_finished_pull_restores_the_weekly_cadence(monkeypatch):
    from datetime import date

    from web import db
    monkeypatch.setattr(db, "load_settings", lambda doc_id=None: {
        scheduler.WEEKLY_BLOCK_KEY: date.today().isoformat(),
        scheduler.WEEKLY_BLOCK_LEFT_KEY: 0,
    })
    due, _ = scheduler.weekly_block_due(today=date.today())
    assert due is False, "nothing owed and stamped today — not due"


def test_the_remainder_is_recorded_with_the_completion(monkeypatch):
    saved = {}
    from web import db
    monkeypatch.setattr(db, "save_settings",
                        lambda d, doc_id=None: saved.update(d))
    scheduler._mark_weekly_block_done(1234)
    assert saved[scheduler.WEEKLY_BLOCK_LEFT_KEY] == 1234
    assert saved[scheduler.WEEKLY_BLOCK_KEY]


def test_the_scan_reads_the_store_reader_wired_at_boot():
    """trading/ must never import web.db — the reader is injected."""
    src = inspect.getsource(__import__("web.state", fromlist=["state"]))
    assert "set_history_store(db.history_docs)" in src
    # Comment lines stripped FIRST. The module's own docstring explains the
    # rule ("trading/ must not import web.db") and a naive grep matches that
    # prose — the fifth time this exact shape has bitten in this repo.
    from trading import data as tdata
    code = "\n".join(ln.split("#")[0] for ln in
                     inspect.getsource(tdata).splitlines()
                     if not ln.strip().startswith("#"))
    assert "import web" not in code
    assert "from web" not in code
