"""Owner order 2026-10-12: "add a time budget on the fundamentals step".

The monthly fundamentals refresh (Node 3) runs inside the weekly block
BEFORE the universe rebuild and the OHLCV pull, over ~1,205 names with one
20 s-timeout Yahoo GET each and, until now, no total bound. When Yahoo hung
the block sat in it for 89 minutes (Fri 10-09) and again Mon 10-12, and
everything sequenced after it never ran.

Pinned here: the fetcher stops on its budget and names what it did not
reach; a partial run saves what it got, records the names attempted, and
leaves the weekly block due the next trading day; the next run resumes from
the ledger and stamps the month only when every name has been attempted; a
run Yahoo refuses entirely counts nothing as done and does NOT ask for a
next-day rerun; the month guard still short-circuits; the budget comes from
the env with a 600 s default; and the scheduler calls the budgeted fetcher,
never the unbounded one.
"""
from __future__ import annotations

import inspect
import re
from datetime import date

from trading import data_refresh as dr
from web import scheduler, state


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _payload(value=1.0):
    return {"timeseries": {"result": [
        {"meta": {"type": ["annualNetIncome"]},
         "annualNetIncome": [{"asOfDate": "2026-03-31",
                              "reportedValue": {"raw": value}}]}]}}


# ------------------------------------------------------------ the fetcher

def test_the_fetcher_stops_on_its_time_budget_and_names_the_rest(monkeypatch):
    clock = {"t": 0.0}
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        clock["t"] += 1.0                      # every request "takes" 1 s
        return _Resp(_payload())

    monkeypatch.setattr(dr.httpx, "get", fake_get)
    monkeypatch.setattr(dr.time, "monotonic", lambda: clock["t"])
    syms = [f"S{i:02d}" for i in range(10)]
    res = dr.fetch_fundamentals_budgeted(syms, time_budget_s=2.5, sleep_s=0)
    # before S00 t=0, S01 t=1, S02 t=2 (all < 2.5); before S03 t=3 -> stop
    assert res["attempted"] == syms[:3]
    assert res["remaining"] == syms[3:]
    assert res["stopped"] == "time_budget"
    assert res["n_ok"] == 3 and len(res["rows"]) == 3
    assert len(calls) == 3                     # nothing fetched past the budget
    assert res["elapsed_s"] == 3.0
    # the order is kept, so the caller can resume from `remaining`
    assert res["attempted"] + res["remaining"] == syms


def test_no_budget_means_the_old_unbounded_run(monkeypatch):
    clock = {"t": 0.0}

    def fake_get(url, headers=None, timeout=None):
        clock["t"] += 1.0
        return _Resp(_payload())

    monkeypatch.setattr(dr.httpx, "get", fake_get)
    monkeypatch.setattr(dr.time, "monotonic", lambda: clock["t"])
    syms = [f"S{i:02d}" for i in range(6)]
    for budget in (None, 0, -5):
        clock["t"] = 0.0
        res = dr.fetch_fundamentals_budgeted(syms, time_budget_s=budget,
                                             sleep_s=0)
        assert res["attempted"] == syms and res["remaining"] == []
        assert res["stopped"] is None
    # the unbounded wrapper keeps its (rows, n_ok) contract for the kit
    clock["t"] = 0.0
    rows, n_ok = dr.fetch_fundamentals(syms, sleep_s=0)
    assert n_ok == 6 and len(rows) == 6


def test_a_failed_request_counts_as_attempted_not_answered(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        if "S01" in url:
            raise RuntimeError("429")
        return _Resp(_payload())

    monkeypatch.setattr(dr.httpx, "get", fake_get)
    res = dr.fetch_fundamentals_budgeted(["S00", "S01", "S02"],
                                         time_budget_s=None, sleep_s=0)
    assert res["attempted"] == ["S00", "S01", "S02"]
    assert res["n_ok"] == 2 and len(res["rows"]) == 2


# ------------------------------------------------------ the weekly step

def _ledger_store(monkeypatch):
    """An in-memory settings store with the real merge/unset semantics."""
    store: dict = {"data_refresh": {}}
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.load_settings",
                        lambda doc_id="default": dict(store.get(doc_id, {})))
    monkeypatch.setattr("web.db.save_settings",
                        lambda d, doc_id="default":
                        store.setdefault(doc_id, {}).update(d))

    def unset(keys, doc_id="default"):
        for k in keys:
            store.get(doc_id, {}).pop(k, None)
    monkeypatch.setattr("web.db.unset_settings_keys", unset)
    saved: list = []
    monkeypatch.setattr("web.db.save_fundamentals_rows",
                        lambda rows: saved.append(len(rows)) or len(rows))
    import trading.register_v4 as rv
    monkeypatch.setattr(rv, "clear_fundamentals_cache", lambda: None)
    return store, saved


def _expected_symbols():
    cfg = state.cfg()
    p = state.portfolio()
    return sorted({t.replace(".NS", "")
                   for t in (tuple(cfg.custom_universe or ())
                             + tuple((p.positions or {}).keys()))})


def _own(p, n0):
    return [e for e in p.decision_journal[n0:]]


def test_a_partial_run_keeps_progress_and_the_next_run_finishes_the_month(monkeypatch):
    store, saved = _ledger_store(monkeypatch)
    month = date.today().isoformat()[:7]
    cfg = state.cfg()
    old_universe = cfg.custom_universe
    cfg.custom_universe = tuple(f"ZB{i:02d}.NS" for i in range(5))
    try:
        syms = _expected_symbols()
        assert len(syms) >= 5
        seen: list = []

        def partial(todo, time_budget_s=None, **k):
            seen.append((list(todo), time_budget_s))
            done = list(todo[:2])              # the budget allowed two names
            return {"rows": [{"symbol": s, "field": "annualNetIncome",
                              "as_of": "2026-03-31", "value": 1.0} for s in done],
                    "n_ok": len(done), "attempted": done,
                    "remaining": list(todo[2:]), "elapsed_s": 600.2,
                    "stopped": "time_budget"}
        monkeypatch.setattr("trading.data_refresh.fetch_fundamentals_budgeted",
                            partial)
        monkeypatch.setenv("SB_FUNDAMENTALS_BUDGET_S", "600")
        p = state.portfolio()
        n0 = len(p.decision_journal)
        scheduler._monthly_fundamentals_refresh()

        led = store["data_refresh"]
        assert seen[0][0] == syms and seen[0][1] == 600.0
        assert led[scheduler.FUNDAMENTALS_DONE_KEY] == {
            "month": month, "symbols": sorted(syms[:2])}
        assert led[scheduler.FUNDAMENTALS_LEFT_KEY] == len(syms) - 2
        assert "fundamentals_month" not in led          # NOT stamped yet
        assert saved == [2]
        line = [e for e in _own(p, n0)
                if e.get("action") == "DATA_REFRESH_FUNDAMENTALS"][-1]["why"]
        assert "STOPPED on the 600 s time budget" in line
        assert f"{len(syms) - 2} name(s) left" in line
        assert "next trading day" in line
        # the block comes back tomorrow for it
        due, why = scheduler.weekly_block_due()
        assert due and "fundamentals" in why

        # --- next run: the ledger says what is left; the fetcher gets ONLY
        # the names not yet attempted, finishes, and the month is stamped
        def rest(todo, time_budget_s=None, **k):
            seen.append((list(todo), time_budget_s))
            return {"rows": [{"symbol": s, "field": "annualNetIncome",
                              "as_of": "2026-03-31", "value": 1.0} for s in todo],
                    "n_ok": len(todo), "attempted": list(todo),
                    "remaining": [], "elapsed_s": 40.0, "stopped": None}
        monkeypatch.setattr("trading.data_refresh.fetch_fundamentals_budgeted",
                            rest)
        n1 = len(p.decision_journal)
        scheduler._monthly_fundamentals_refresh()
        led = store["data_refresh"]
        assert seen[1][0] == syms[2:]
        assert led["fundamentals_month"] == month
        assert led[scheduler.FUNDAMENTALS_LEFT_KEY] == 0
        assert scheduler.FUNDAMENTALS_DONE_KEY not in led
        assert saved == [2, len(syms) - 2]
        line = [e for e in _own(p, n1)
                if e.get("action") == "DATA_REFRESH_FUNDAMENTALS"][-1]["why"]
        assert f"Complete: {len(syms)}/{len(syms)} names" in line

        # --- a third call inside the month does nothing (the guard)
        def boom(*a, **k):
            raise AssertionError("must not refetch inside the month")
        monkeypatch.setattr("trading.data_refresh.fetch_fundamentals_budgeted",
                            boom)
        scheduler._monthly_fundamentals_refresh()
    finally:
        cfg.custom_universe = old_universe


def test_a_run_yahoo_refuses_entirely_counts_nothing_done_and_waits_a_week(monkeypatch):
    store, saved = _ledger_store(monkeypatch)
    month = date.today().isoformat()[:7]
    # progress from an earlier run this month must survive the bad run
    store["data_refresh"] = {scheduler.FUNDAMENTALS_DONE_KEY:
                             {"month": month, "symbols": ["AAA"]},
                             scheduler.FUNDAMENTALS_LEFT_KEY: 7}
    cfg = state.cfg()
    old_universe = cfg.custom_universe
    cfg.custom_universe = ("AAA.NS", "BBB.NS", "CCC.NS")
    try:
        monkeypatch.setattr(
            "trading.data_refresh.fetch_fundamentals_budgeted",
            lambda todo, time_budget_s=None, **k: {
                "rows": [], "n_ok": 0, "attempted": list(todo[:1]),
                "remaining": list(todo[1:]), "elapsed_s": 20.4,
                "stopped": "time_budget"})
        p = state.portfolio()
        n0 = len(p.decision_journal)
        scheduler._monthly_fundamentals_refresh()
        led = store["data_refresh"]
        assert saved == []
        assert led[scheduler.FUNDAMENTALS_DONE_KEY]["symbols"] == ["AAA"]
        assert led[scheduler.FUNDAMENTALS_LEFT_KEY] == 0     # no daily hang-loop
        assert "fundamentals_month" not in led
        acts = [e.get("action") for e in _own(p, n0)]
        assert "DATA_REFRESH_FUNDAMENTALS_FAILED" in acts
        assert "DATA_REFRESH_FUNDAMENTALS" not in acts
    finally:
        cfg.custom_universe = old_universe


def test_the_budget_comes_from_the_env_with_a_600s_default(monkeypatch):
    monkeypatch.delenv("SB_FUNDAMENTALS_BUDGET_S", raising=False)
    assert scheduler.fundamentals_time_budget_s() == 600.0
    monkeypatch.setenv("SB_FUNDAMENTALS_BUDGET_S", "90")
    assert scheduler.fundamentals_time_budget_s() == 90.0
    monkeypatch.setenv("SB_FUNDAMENTALS_BUDGET_S", "0")
    assert scheduler.fundamentals_time_budget_s() is None   # explicit unbounded
    monkeypatch.setenv("SB_FUNDAMENTALS_BUDGET_S", "abc")
    assert scheduler.fundamentals_time_budget_s() == 600.0


def test_weekly_block_due_ignores_a_zero_fundamentals_balance(monkeypatch):
    """The new arm must not make the block due every day on its own."""
    today = date.today()
    monkeypatch.setattr("web.db.load_settings",
                        lambda doc_id="default": {
                            scheduler.WEEKLY_BLOCK_KEY: today.isoformat(),
                            scheduler.WEEKLY_BLOCK_LEFT_KEY: 0,
                            scheduler.FUNDAMENTALS_LEFT_KEY: 0})
    due, why = scheduler.weekly_block_due(today)
    assert not due


def test_the_step_calls_the_budgeted_fetcher_with_a_budget():
    """Source pin: the weekly step passes a time budget and never falls back
    to the unbounded fetcher. Comments and the docstring are stripped before
    asserting (the fault class recorded five times in CLAUDE.md)."""
    src = inspect.getsource(scheduler._monthly_fundamentals_refresh)
    src = re.sub(r'"""[\s\S]*?"""', "", src, count=1)
    src = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "fetch_fundamentals_budgeted(" in src
    assert "time_budget_s=" in src
    assert "fetch_fundamentals(" not in src
