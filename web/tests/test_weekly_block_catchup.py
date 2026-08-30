"""Self-healing weekly slot — the owner's Monday rule.

Owner, 2026-08-29, verbatim: "If Friday run is not done, it should run on
Monday not next Friday." Adopted alongside item 3 of the AshStocks review
(C-006: their continuous market-hours loop self-heals a missed slot; a cron
job simply misses it). The loop itself is NOT copied — a ~2,400-name pull
on a 512MB box cannot run every two minutes — so the job is registered
mon-fri at the same 16:30 slot and weekly_block_due() keeps it weekly.

Pinned here: Friday is still the slot; a completed Friday makes the rest of
the week a no-op; a MISSED Friday is picked up on the next trading day; the
cadence returns to Friday after a Monday catch-up instead of drifting to
Mondays for ever; a never-run book runs; the completion stamp is written
only AFTER the block's sequence (a crash must leave it unset, or the miss
becomes permanent); and the cron really is registered mon-fri.
"""
from __future__ import annotations

from datetime import date

from web import scheduler, state

FRI = date(2026, 8, 28)
MON = date(2026, 8, 31)
TUE = date(2026, 9, 1)
NEXT_FRI = date(2026, 9, 4)


def _ledger(monkeypatch, last_iso):
    """Pin the stored completion stamp."""
    monkeypatch.setattr(
        "web.db.load_settings",
        lambda doc_id="default": ({scheduler.WEEKLY_BLOCK_KEY: last_iso}
                                  if last_iso else {}))


def test_friday_runs_on_the_normal_weekly_cadence(monkeypatch):
    _ledger(monkeypatch, "2026-08-21")           # last Friday
    due, why = scheduler.weekly_block_due(FRI)
    assert due and "7 day" in why


def test_a_completed_friday_makes_the_rest_of_the_week_a_no_op(monkeypatch):
    _ledger(monkeypatch, FRI.isoformat())
    for day in (MON, TUE):
        due, _ = scheduler.weekly_block_due(day)
        assert not due, day


def test_a_missed_friday_is_picked_up_on_monday(monkeypatch):
    """THE OWNER'S RULE. Friday 28th never completed, so the stamp still
    reads the 21st — Monday must run it, not wait for the 4th."""
    _ledger(monkeypatch, "2026-08-21")
    due, why = scheduler.weekly_block_due(MON)
    assert due
    assert "did not complete" in why


def test_cadence_returns_to_friday_after_a_monday_catch_up(monkeypatch):
    """Without the 4-day Friday arm a single miss would move the block to
    Mondays permanently."""
    _ledger(monkeypatch, MON.isoformat())        # caught up on Monday
    assert not scheduler.weekly_block_due(TUE)[0]
    assert scheduler.weekly_block_due(NEXT_FRI)[0]


def test_a_book_that_has_never_run_is_due(monkeypatch):
    _ledger(monkeypatch, None)
    due, why = scheduler.weekly_block_due(MON)
    assert due and "no recorded completion" in why


def test_corrupt_or_future_stamps_fail_towards_running(monkeypatch):
    """A bad stamp must never silently freeze the weekly data."""
    _ledger(monkeypatch, "not-a-date")
    assert scheduler.weekly_block_due(MON)[0]
    _ledger(monkeypatch, "2027-01-01")           # clock skew
    due, why = scheduler.weekly_block_due(MON)
    assert due and "future" in why


def test_an_unreadable_ledger_does_not_raise(monkeypatch):
    def _boom(doc_id="default"):
        raise RuntimeError("mongo blip")
    monkeypatch.setattr("web.db.load_settings", _boom)
    assert scheduler.weekly_block_due(MON)[0]


def test_not_due_returns_before_doing_any_work(monkeypatch):
    """The whole point of running mon-fri: an ordinary Monday must cost one
    settings read, not a 2,400-name pull."""
    calls = []
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda d=None: True)
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("trading.data.history_broker", lambda: object())
    monkeypatch.setattr(scheduler, "weekly_block_due",
                        lambda *a, **k: (False, "already ran"))
    for name in ("_weekly_fii_refresh", "_weekly_sebi_refresh",
                 "refresh_breadth_store"):
        monkeypatch.setattr(scheduler, name,
                            lambda *a, _n=name, **k: calls.append(_n))
    monkeypatch.setattr("trading.history_store.top_up_history",
                        lambda *a, **k: calls.append("pull"))
    p = state.engine().portfolio
    n0 = len(p.decision_journal)

    scheduler._weekly_history_topup()

    assert calls == []
    acts = [e.get("action") for e in p.decision_journal[n0:]]
    assert "DATA_REFRESH_WEEKLY_START" not in acts


def test_completion_is_stamped_only_after_the_block_ran(monkeypatch):
    """A crash must leave no stamp, or one bad Friday becomes permanent."""
    import inspect
    src = inspect.getsource(scheduler._weekly_history_topup)
    body = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert body.count("_mark_weekly_block_done()") == 1
    # it must come after the post-topup recomputes, i.e. last in the function
    assert body.rstrip().endswith("_mark_weekly_block_done()")


def test_cron_is_registered_mon_fri_not_fri_only():
    """The registration is the other half of the fix — the due check alone
    cannot help if the job still only wakes on Fridays."""
    import inspect
    # Strip comments before asserting on source — a comment quoting the old
    # value would otherwise satisfy or break the pin (bitten twice: the F1
    # test and the /positions hunt).
    src = "\n".join(ln for ln in
                    inspect.getsource(scheduler.start_if_enabled).splitlines()
                    if not ln.lstrip().startswith("#"))
    # id= comes AFTER day_of_week= in the call, so anchor on the id and read
    # the registration that precedes it.
    registration = src.split('id="weekly_history_topup"')[0][-400:]
    assert 'day_of_week="mon-fri", hour=16, minute=30' in registration
    assert 'day_of_week="fri"' not in registration
