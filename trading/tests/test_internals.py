"""Node 9 steps 1-2 (owner: "node 9 go", 2026-08-24) — one canonical
damage series, three consumers, day-one behaviour unchanged.

Pinned: the module's formula is EXACTLY the register's inline one (one
computation site is the whole point); a broken tape fires the cluster and
a quiet one doesn't; thin panels return None, never a guess; engine.scan
stores the reading over its own panel; FII-08/09 prefer a fresh store and
degrade to the old page-panel recompute when it's stale; the reading
survives a save/load round trip; and the DANGER LAYER IS UNTOUCHED — the
design note promises it, so a test enforces it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading import internals
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio


def _frame(n=300, start=100.0, drift=0.15, seed=1, crash_last=0):
    rng = np.random.default_rng(seed)
    steps = 1 + drift / n + rng.normal(0, 0.004, n)
    if crash_last:
        steps[-crash_last:] = 0.96          # -4%/day tail — deep 3d damage
    close = start * np.cumprod(steps)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": np.full(n, 5e6),
    }, index=idx)


def test_formula_is_exactly_the_register_inline_expression():
    rng = np.random.default_rng(7)
    closes = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.02, (200, 12)), axis=0),
        index=pd.bdate_range(end="2026-08-21", periods=200),
        columns=[f"S{i}" for i in range(12)])
    ours = internals.tail_down3_5d(closes)
    register_inline = (closes.pct_change(3).rolling(5).min()
                       <= -0.03).mean(axis=1)
    pd.testing.assert_series_equal(ours, register_inline)


def test_damage_reading_fires_on_a_broken_tape_and_not_a_quiet_one():
    broken = {f"B{i}.NS": _frame(seed=i, crash_last=12)
              for i in range(12)}
    quiet = {f"Q{i}.NS": _frame(seed=100 + i, drift=0.3)
             for i in range(12)}
    r = internals.damage_reading(broken)
    assert r is not None and r["static_cluster"] and r["q90_cluster"]
    assert r["sample"] == 12 and r["value"] > 0.9
    q = internals.damage_reading(quiet)
    assert q is not None and not q["static_cluster"]


def test_thin_panels_return_none_never_a_guess():
    few = {f"T{i}.NS": _frame(seed=i) for i in range(5)}       # < MIN_MEMBERS
    assert internals.damage_reading(few) is None
    short = {f"S{i}.NS": _frame(n=30, seed=i) for i in range(12)}
    assert internals.damage_reading(short) is None
    assert internals.damage_reading({}) is None


def test_is_fresh_window():
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=10)).isoformat()
    assert internals.is_fresh({"asof": today})
    assert not internals.is_fresh({"asof": old})
    assert not internals.is_fresh({})
    assert not internals.is_fresh(None)


@pytest.fixture()
def eng(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    names = tuple(f"N{i}.NS" for i in range(12))
    cfg.custom_universe = names
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    frames = {t: _frame(seed=i, drift=0.4) for i, t in enumerate(names)}
    frames[cfg.benchmark] = _frame(seed=90, drift=0.05)
    frames["^INDIAVIX"] = _frame(start=12.0, drift=0.0, seed=91)

    def fake_fetch_daily(tickers, period="6mo"):
        if isinstance(tickers, str):
            tickers = [tickers]
        return {t: frames[t].copy() for t in tickers if t in frames}

    monkeypatch.setattr("trading.engine.fetch_daily", fake_fetch_daily)
    monkeypatch.setattr(
        "trading.engine.routed_fetch_quotes",
        lambda tickers, upstox_client=None: {
            t: float(frames[t]["Close"].iloc[-1])
            for t in tickers if t in frames})
    return e


def test_engine_scan_stores_the_canonical_reading(eng):
    eng.scan()
    store = eng.portfolio.damage_store
    assert store, "scan must store the canonical damage reading"
    assert store["sample"] >= 10
    assert 0.0 <= store["value"] <= 1.0
    assert "static_cluster" in store and "q90_cluster" in store
    assert store.get("computed")


def test_reading_survives_a_save_load_round_trip(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.damage_store = {"asof": "2026-08-24", "value": 0.31, "sample": 48,
                      "static_cluster": True, "q90_cluster": False}
    p.save()
    p2 = Portfolio.load_or_fresh(cfg)
    assert p2.damage_store == p.damage_store


def _mini_panel():
    return {f"P{i}.NS": _frame(seed=40 + i) for i in range(12)}


def test_fii_damage_prefers_a_fresh_store_and_says_so():
    import trading.register_v4 as rv
    fresh = {"asof": date.today().isoformat(), "value": 0.5, "sample": 48,
             "static_cluster": True, "q90_cluster": True}
    mv = rv.market_view(_mini_panel(), damage_store=fresh)
    note = mv["FII-08"].note
    assert "[canonical store" in note and "48 names" in note
    assert "damage[static 0.213]: YES" in note


def test_fii_damage_degrades_to_the_page_panel_when_the_store_is_stale():
    import trading.register_v4 as rv
    stale = {"asof": (date.today() - timedelta(days=30)).isoformat(),
             "value": 0.9, "sample": 48,
             "static_cluster": True, "q90_cluster": True}
    mv = rv.market_view(_mini_panel(), damage_store=stale)
    note = mv["FII-08"].note
    # the old computation ran (page panel), the stale store was refused
    assert "[canonical store" not in note
    assert "[page panel" in note


def test_danger_layer_is_untouched_by_node_9():
    """The design note promises the 12 calibrated tripwires stay welded to
    nothing — danger.py must not import internals, and internals must not
    import danger/engine/risk/orders (it is a pure reduction)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    danger_src = (root / "trading" / "danger.py").read_text()
    src = "\n".join(ln for ln in danger_src.splitlines()
                    if not ln.lstrip().startswith("#"))
    assert "internals" not in src
    itn_src = (root / "trading" / "internals.py").read_text()
    itn = "\n".join(ln for ln in itn_src.splitlines()
                    if not ln.lstrip().startswith("#"))
    for banned in ("danger", "engine", "risk", "orders", "httpx", "web."):
        assert f"import {banned}" not in itn and f"from .{banned}" not in itn
