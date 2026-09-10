"""The weekly liquidity-universe job (owner order 2026-08-30).

"No automation for weekly new batch of stocks / batch size also reduce to
500 from 2300+". The hand-typed 50-name list of 2026-08-20 was the owner's
own weekly chore, and it going stale is exactly what they reported as
"system still use old stocks". The Friday block now rebuilds the universe
from the OHLCV store by median rupee turnover.

Pinned here: the job writes BOTH the live config and the stored override
(so a restart keeps the new batch); a Mongo outage or an empty result
leaves the previous list standing rather than emptying the universe; the
journal names what came in and went out; held names ride along; and it is
registered inside the Friday block, isolated from the OHLCV pull.
"""
from __future__ import annotations

from web import scheduler, state


def _doc(ticker, close, vol, bars=20):
    return {"ticker": ticker,
            "candles": [["2026-08-01", close, close, close, close, vol]
                        for _ in range(bars)]}


def _own(p, n0):
    return [e.get("action") for e in p.decision_journal[n0:]]


def test_job_rebuilds_the_universe_and_persists_it(monkeypatch):
    saved = {}
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.save_settings",
                        lambda d, doc_id="default": saved.update(d))
    monkeypatch.setattr("web.db.history_tails", lambda rows: [
        _doc("BIG.NS", 500.0, 1_000_000),
        _doc("MID.NS", 200.0, 1_000_000),
        _doc("DUST.NS", 2.0, 1_000),        # below the Rs 1 Cr floor
    ])
    cfg, p = state.cfg(), state.portfolio()
    old = cfg.custom_universe
    n0 = len(p.decision_journal)
    try:
        res = scheduler.refresh_liquid_universe("test")
        assert set(cfg.custom_universe) == {"BIG.NS", "MID.NS"}
        assert set(saved["custom_universe"]) == {"BIG.NS", "MID.NS"}
        assert res["skipped_illiquid"] == 1
        # Owner ask 2026-09-04 ("where is details for new batch"): the story
        # is kept on the portfolio for /scan, not only in the journal.
        b = p.universe_batch
        assert b["count"] == 2 and b["target"] == cfg.universe_target_size
        assert set(b["added"]) == {"BIG.NS", "MID.NS"}
        assert (b["short_by"] == cfg.universe_target_size - 2
                and b["skipped_illiquid"] == 1)
        assert "UNIVERSE_BATCH" in _own(p, n0)
        why = next(e["why"] for e in p.decision_journal[n0:]
                   if e.get("action") == "UNIVERSE_BATCH")
        assert "turnover" in why
        assert f"target {cfg.universe_target_size}" in why
    finally:
        cfg.custom_universe = old


def test_a_short_list_says_so_in_the_journal(monkeypatch):
    """The store is still filling — a 1-name universe must not read as full."""
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.save_settings", lambda d, doc_id="default": None)
    monkeypatch.setattr("web.db.history_tails", lambda rows: [
        _doc("BIG.NS", 500.0, 1_000_000)])
    cfg, p = state.cfg(), state.portfolio()
    old = cfg.custom_universe
    n0 = len(p.decision_journal)
    try:
        scheduler.refresh_liquid_universe("test")
        why = next(e["why"] for e in p.decision_journal[n0:]
                   if e.get("action") == "UNIVERSE_BATCH")
        assert f"SHORT BY {cfg.universe_target_size - 1}" in why
    finally:
        cfg.custom_universe = old


def test_an_empty_result_never_empties_the_universe(monkeypatch):
    """Emptying the universe would stop the engine trading entirely."""
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.save_settings", lambda d, doc_id="default": None)
    monkeypatch.setattr("web.db.history_tails", lambda rows: [])
    cfg, p = state.cfg(), state.portfolio()
    old = tuple(cfg.custom_universe)
    n0 = len(p.decision_journal)
    try:
        scheduler.refresh_liquid_universe("test")
        assert tuple(cfg.custom_universe) == old
        assert "UNIVERSE_BATCH_FAILED" in _own(p, n0)
    finally:
        cfg.custom_universe = old


def test_mongo_down_leaves_the_previous_list_standing(monkeypatch):
    monkeypatch.setattr("web.db.is_connected", lambda: False)
    cfg, p = state.cfg(), state.portfolio()
    old = tuple(cfg.custom_universe)
    n0 = len(p.decision_journal)
    scheduler.refresh_liquid_universe("test")
    assert tuple(cfg.custom_universe) == old
    assert "UNIVERSE_BATCH_SKIPPED" in _own(p, n0)


def test_the_job_is_registered_inside_the_friday_block():
    import inspect
    src = "\n".join(ln for ln in
                    inspect.getsource(scheduler._weekly_history_topup).splitlines()
                    if not ln.lstrip().startswith("#"))
    assert "refresh_liquid_universe" in src
