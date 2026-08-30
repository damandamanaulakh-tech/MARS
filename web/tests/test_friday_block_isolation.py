"""The Friday 16:30 data block: order and isolation.

Owner ask 2026-08-29 ("is the Friday trigger captured?" -> "fix it").

THE FAULT THIS PINS: every weekly sub-job used to run sequentially
inside ONE try block, AFTER a ~2,400-name OHLCV top-up that takes the
better part of an hour. A crash — or a container restart mid-pull, which
no try block can catch — silently skipped every feed below it, and the
weekly block has no catch-up, so a miss cost a FULL WEEK. Three of the
have a daily counterpart and survived that (index tail, breadth, register
precompute); FOUR do not — FII tide, constituents, fundamentals, SEBI.
Measured on the live box 2026-08-28: the store held 573 of 2,428 names
after two full-scope Fridays, so the pull was not finishing and those four
were the ones paying for it.

Pinned here: the short store-independent feeds run BEFORE the pull;
one feed crashing cannot take out the others (and journals which one); a
crashing pull no longer costs them OR the two post-topup recomputes;
and DATA_REFRESH_WEEKLY_START marks that Friday fired even when the pull
later dies, which is exactly the evidence that was missing when the owner
asked.
"""
from __future__ import annotations

from web import scheduler, state

# The short, store-INDEPENDENT feeds, in the order they must run, and
# the two recomputes that read the store so can only follow the pull.
SHORT_FEEDS = ("_weekly_fii_refresh", "_weekly_index_refresh",
               "_monthly_constituents_snapshot", "_monthly_fundamentals_refresh",
               "_weekly_sebi_refresh")
POST_TOPUP = ("refresh_breadth_store", "run_register_precompute")


def _own_journal(p, n0):
    """Only this test's own appends — the state dir persists between runs."""
    return [e.get("action") for e in p.decision_journal[n0:]]


def _wire(monkeypatch, calls, *, topup_raises=False, feed_raises=None):
    """Stub every edge of the Friday block; record the call ORDER."""
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda d=None: True)
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.list_history_meta", lambda: [])
    monkeypatch.setattr("trading.data.history_broker", lambda: object())
    monkeypatch.setattr("trading.universes.load_full_universe",
                        lambda *a, **k: ["TCS.NS"])
    monkeypatch.setattr(
        "trading.brokers.upstox_instruments.to_instrument_keys",
        lambda names: {n: f"KEY|{n}" for n in names})

    def _topup(*a, **k):
        calls.append("top_up_history")
        if topup_raises:
            raise RuntimeError("container went away mid-pull")
        return {"ok": 1, "skipped_fresh": 0, "failed": 0, "total": 1}

    monkeypatch.setattr("trading.history_store.top_up_history", _topup)

    for name in SHORT_FEEDS + POST_TOPUP:
        def _mk(n):
            def _fn(*a, **k):
                calls.append(n)
                if feed_raises == n:
                    raise RuntimeError(f"{n} blew up")
            return _fn
        monkeypatch.setattr(scheduler, name, _mk(name))


def test_short_feeds_run_before_the_long_ohlcv_pull(monkeypatch):
    """The Friday-ONLY feeds must be finished before the hour-long pull
    can be interrupted. Order is the whole fix — isolation alone cannot
    survive a container restart."""
    calls = []
    _wire(monkeypatch, calls)
    scheduler._weekly_history_topup()

    pull = calls.index("top_up_history")
    for feed in SHORT_FEEDS:
        assert feed in calls, feed
        assert calls.index(feed) < pull, f"{feed} still runs after the pull"
    # the two store-derived recomputes genuinely need the fresh closes
    for name in POST_TOPUP:
        assert calls.index(name) > pull, name


def test_one_feed_crashing_does_not_take_out_the_others(monkeypatch):
    """Before the fix a single raise skipped every feed below it."""
    calls = []
    _wire(monkeypatch, calls, feed_raises="_weekly_fii_refresh")
    p = state.engine().portfolio
    n0 = len(p.decision_journal)

    scheduler._weekly_history_topup()

    for name in SHORT_FEEDS + POST_TOPUP:
        assert name in calls, f"{name} was skipped by an unrelated crash"
    assert "top_up_history" in calls
    acts = _own_journal(p, n0)
    assert "DATA_REFRESH_STEP_FAILED" in acts
    why = next(e["why"] for e in p.decision_journal[n0:]
               if e.get("action") == "DATA_REFRESH_STEP_FAILED")
    assert "FII tide" in why          # names WHICH feed, not just "a step"


def test_a_crashing_pull_no_longer_costs_the_weeks_feeds(monkeypatch):
    """The live failure mode: the OHLCV pull dies and the week's FII,
    constituents, fundamentals and SEBI data die with it."""
    calls = []
    _wire(monkeypatch, calls, topup_raises=True)
    p = state.engine().portfolio
    n0 = len(p.decision_journal)

    scheduler._weekly_history_topup()

    for name in SHORT_FEEDS:
        assert name in calls, f"{name} lost to a crashed pull"
    for name in POST_TOPUP:
        assert name in calls, f"{name} lost to a crashed pull"
    acts = _own_journal(p, n0)
    assert "DATA_REFRESH_FAILED" in acts       # the pull still reports itself


def test_friday_start_is_journaled_so_a_partial_run_is_visible(monkeypatch):
    """The evidence gap that prompted this fix: when the pull died there was
    no journal line at all saying Friday had fired."""
    calls = []
    _wire(monkeypatch, calls, topup_raises=True)
    p = state.engine().portfolio
    n0 = len(p.decision_journal)

    scheduler._weekly_history_topup()

    assert "DATA_REFRESH_WEEKLY_START" in _own_journal(p, n0)


def test_a_non_trading_day_still_does_nothing(monkeypatch):
    """AUDIT E gate must survive the reorder — no feed may run on a holiday."""
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda d=None: False)
    scheduler._weekly_history_topup()
    assert calls == []


def test_mongo_down_skips_before_any_feed_runs(monkeypatch):
    """Nowhere to store — the feeds must not burn their day guards."""
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr("web.db.is_connected", lambda: False)
    p = state.engine().portfolio
    n0 = len(p.decision_journal)

    scheduler._weekly_history_topup()

    assert calls == []
    assert "DATA_REFRESH_SKIPPED" in _own_journal(p, n0)
