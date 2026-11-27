"""XR-041: a data outage no longer lifts a crash-damage throttle that is open.

`check_damage_cluster` returned active=False / sizing_multiplier=1.0 from its
thin-panel branch BEFORE the window logic ran, so a cluster that fired two days
ago — inside its own documented 22-day window — silently stopped halving new
buys for as long as the reference panel stayed thin. Three doors led there: a
thin panel (fewer than MIN_REFERENCE_NAMES of the 191 frozen names loaded), a
short market series, and a crash in the engine's own `except`, which left the
snapshot None and read as multiplier 1.0.

The saved dates were never lost, so the window resumed once the panel
recovered. The loss was confined to the passes during the outage — which is
exactly when a crowd-fear throttle matters.
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading import ifr


NOW = datetime(2026, 11, 27)


def ago(days):
    return (NOW - timedelta(days=days)).date().isoformat()


def real_panel(n_names, bars=90, shock=False):
    """n_names of the FROZEN reference list, with enough bars to be judged."""
    names = list((ifr.FROZEN or {}).get('tickers') or [])[:n_names]
    idx = pd.date_range('2026-06-01', periods=bars, freq='B')
    out = {}
    for i, t in enumerate(names):
        close = np.linspace(100.0, 60.0 if shock else 105.0, bars)
        out[t] = pd.DataFrame({'Open': close, 'High': close * 1.01,
                               'Low': close * 0.99, 'Close': close,
                               'Volume': np.full(bars, 1e6)}, index=idx)
    return out


def test_the_frozen_reference_panel_is_loaded():
    """Without it there is no thin-panel branch to test."""
    assert ifr.FROZEN is not None
    assert len(ifr.FROZEN['tickers']) >= ifr.MIN_REFERENCE_NAMES


@pytest.mark.parametrize('n_names', [0, 1, 29])
def test_a_thin_panel_keeps_an_open_window(n_names):
    snap = ifr.check_damage_cluster(real_panel(n_names), now=NOW,
                                    last_fire_date=ago(2))
    assert snap.active is True, n_names
    assert snap.sizing_multiplier == ifr.THROTTLE_MULTIPLIER
    assert snap.last_fire_date == ago(2), "the saved date must survive"
    assert 'window' in snap.reason and 'CONTINUES' in snap.reason


def test_a_thin_panel_does_not_invent_a_window_that_has_expired():
    snap = ifr.check_damage_cluster({}, now=NOW,
                                    last_fire_date=ago(ifr.ACTIVE_DAYS_AFTER_FIRE + 1))
    assert snap.active is False and snap.sizing_multiplier == 1.0


def test_the_boundary_is_the_documented_window():
    inside = ifr.check_damage_cluster({}, now=NOW,
                                      last_fire_date=ago(ifr.ACTIVE_DAYS_AFTER_FIRE))
    outside = ifr.check_damage_cluster({}, now=NOW,
                                       last_fire_date=ago(ifr.ACTIVE_DAYS_AFTER_FIRE + 1))
    assert inside.active is True and outside.active is False


def test_a_book_that_never_fired_is_untouched_by_a_thin_panel():
    snap = ifr.check_damage_cluster({}, now=NOW, last_fire_date=None)
    assert snap.active is False and snap.sizing_multiplier == 1.0
    assert snap.last_fire_date is None


def test_an_unreadable_saved_date_is_not_treated_as_active():
    snap = ifr.check_damage_cluster({}, now=NOW, last_fire_date='not-a-date')
    assert snap.active is False and snap.sizing_multiplier == 1.0


def test_the_ratio_signal_holds_its_own_window_independently():
    snap = ifr.check_damage_cluster({}, now=NOW, last_fire_date=None,
                                    ratio_last_fire_date=ago(3))
    assert snap.active is True and snap.ratio_active is True
    assert 'ratio' in snap.which_fired and 'cluster' not in snap.which_fired


def test_a_short_market_series_also_keeps_the_window():
    """The second door: a FULL panel with too few bars to judge."""
    snap = ifr.check_damage_cluster(real_panel(60, bars=35), now=NOW,
                                    last_fire_date=ago(2))
    assert snap.active is True and snap.sizing_multiplier == ifr.THROTTLE_MULTIPLIER
    assert 'insufficient history' in snap.reason


def test_a_crash_in_the_damage_check_keeps_the_window(monkeypatch):
    """The third door, in the engine's own except branch."""
    snap = ifr.held_throttle_snapshot(NOW, last_fire_date=ago(2),
                                      reason='damage check crashed')
    assert snap.active is True and snap.sizing_multiplier == ifr.THROTTLE_MULTIPLIER
    assert snap.fired_today is False
    assert snap.last_fire_date == ago(2)
    expired = ifr.held_throttle_snapshot(NOW, last_fire_date=ago(99))
    assert expired.active is False and expired.sizing_multiplier == 1.0


def test_the_engine_falls_back_to_the_saved_dates_when_the_check_raises():
    """Pinned on the engine's source: the except branch must not leave None."""
    import ast
    import pathlib
    from trading import engine as E
    tree = ast.parse(pathlib.Path(E.__file__).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == 'scan')
    names = {getattr(n.func, 'id', '') or getattr(n.func, 'attr', '')
             for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert 'held_throttle_snapshot' in names, \
        "a crashed damage check must fall back to the saved fire dates"


def test_both_paths_share_one_window_rule():
    """Thin-panel and normal paths must not disagree about 'still active'."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(ifr.__file__).read_text())
    helper = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == '_window_active'), None)
    assert helper is not None, "_window_active is the single rule"
    callers = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               for c in ast.walk(n)
               if isinstance(c, ast.Call) and getattr(c.func, 'id', '') == '_window_active'}
    assert {'check_damage_cluster', 'held_throttle_snapshot'} <= callers, callers


def test_a_real_fire_on_a_healthy_panel_still_fires():
    """The repair must not make the signal itself quieter."""
    snap = ifr.check_damage_cluster(real_panel(60, bars=120, shock=True), now=NOW,
                                    last_fire_date=None)
    assert snap.sizing_multiplier in (1.0, ifr.THROTTLE_MULTIPLIER)
    assert 'insufficient' not in snap.reason
