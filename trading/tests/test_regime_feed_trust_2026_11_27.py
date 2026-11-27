"""XR-050: a dead market-state feed no longer reads as a calm market.

`fetch_regime` catches its OWN fetch failure and RETURNS a permissive neutral
reading — everest on, 0% cash reserve — instead of raising. The C7 sticky brake
lives in `engine.scan`'s `except`, so on the COMMON failure control took the
`else` and the brake was switched OFF.

Worse than the backlog row says: the permissive reading was also WRITTEN to
`portfolio.last_regime`, so one dead-feed day overwrote a stored "panic" and
the brake could not arm on the next dead-feed day either.

And there is a SECOND route that raises nothing at all: `data.fetch_daily`
returns its dict whatever happens, so an empty answer reaches `classify()` with
every input None and falls through to its own "NEUTRAL (insufficient data)".

Owner's choice 2026-11-27, from an options card: keep yesterday's reading until
the feed answers; with nothing stored, carry on as before.
"""
import pandas as pd
import pytest

from trading import regime as R


# ------------------------------------------------- the reading tells the truth

def test_a_failed_fetch_is_marked_untrustworthy(monkeypatch):
    monkeypatch.setattr('trading.data.fetch_daily',
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError('connection reset by peer')))
    r = R.fetch_regime()
    assert r.feed_ok is False
    assert r.regime == 'neutral' and r.everest_active is True   # unchanged shape
    assert 'fetch_failed' in r.reasoning


def test_an_empty_answer_is_marked_untrustworthy(monkeypatch):
    """The route that raises nothing: fetch_daily returns {} and classify
    falls through to its own 'insufficient data'."""
    monkeypatch.setattr('trading.data.fetch_daily', lambda *a, **k: {})
    r = R.fetch_regime()
    assert r.feed_ok is False
    assert 'insufficient data' in r.reasoning


def test_a_real_reading_is_trusted(monkeypatch):
    import numpy as np
    idx = pd.date_range('2025-01-01', periods=300, freq='B')
    nifty = pd.Series(np.linspace(20000, 23000, 300), index=idx)
    vix = pd.Series(np.full(300, 13.0), index=idx)
    monkeypatch.setattr('trading.data.fetch_daily', lambda *a, **k: {
        R.NIFTY_50: pd.DataFrame({'Close': nifty}),
        R.INDIA_VIX: pd.DataFrame({'Close': vix})})
    r = R.fetch_regime()
    assert r.feed_ok is True
    assert r.nifty_close is not None and r.nifty_200dma is not None


def test_the_flag_defaults_to_trusted_so_old_callers_are_unaffected():
    from datetime import datetime
    r = R.RegimeReading(
        timestamp=datetime(2026, 11, 27), nifty_close=1.0, nifty_200dma=1.0,
        pct_below_52w_high=0.0, vix=13.0, fii_5d_net=None, regime='neutral',
        everest_active=True, ocean_active=False,
        sizing_multiplier_everest=1.0, sizing_multiplier_ocean=0.0,
        reasoning='built by hand')
    assert r.feed_ok is True
    assert r.to_dict()['feed_ok'] is True


# --------------------------------------------------- the engine acts on it

def _engine(tmp_path, monkeypatch, stored_regime):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)
    e.portfolio.last_regime = stored_regime
    return e


@pytest.mark.parametrize('stored', ['panic', 'correction'])
@pytest.mark.parametrize('route', ['raises', 'untrusted'])
def test_a_dead_feed_keeps_yesterdays_brake_and_reserve(tmp_path, monkeypatch,
                                                        stored, route):
    e = _engine(tmp_path, monkeypatch, stored)
    if route == 'raises':
        monkeypatch.setattr('trading.regime.fetch_regime',
                            lambda **k: (_ for _ in ()).throw(RuntimeError('down')))
    else:
        monkeypatch.setattr('trading.regime.fetch_regime',
                            lambda **k: R._neutral_fallback('fetch_failed: reset'))
    e.scan()
    from trading.risk_v2 import dynamic_cash_reserve_pct
    want = dynamic_cash_reserve_pct(stored, floor=0.0)
    assert e.cfg.cash_reserve_pct == want, (stored, route, e.cfg.cash_reserve_pct)
    assert want > 0, "a correction or panic must hold protective cash"
    # and the stored regime is NOT overwritten with the fallback's "neutral"
    assert e.portfolio.last_regime == stored
    rows = [r for r in e.portfolio.decision_journal
            if r.get('action') == 'REGIME_FEED_DOWN']
    assert rows, "a dead feed must say so in the journal"
    assert rows[-1].get('route') in ('failed', 'untrusted')


def test_an_untrusted_reading_is_journaled_only_once_a_day(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, 'panic')
    monkeypatch.setattr('trading.regime.fetch_regime',
                        lambda **k: R._neutral_fallback('fetch_failed: reset'))
    e.scan()
    e.scan()
    rows = [r for r in e.portfolio.decision_journal
            if r.get('action') == 'REGIME_FEED_DOWN']
    assert len(rows) == 1, len(rows)


def test_a_fresh_book_with_nothing_stored_carries_on(tmp_path, monkeypatch):
    """The owner's own condition: nothing stored means nothing to be sticky
    about, so the day proceeds exactly as it did before this repair."""
    e = _engine(tmp_path, monkeypatch, None)
    monkeypatch.setattr('trading.regime.fetch_regime',
                        lambda **k: R._neutral_fallback('fetch_failed: reset'))
    e.scan()
    assert not [r for r in e.portfolio.decision_journal
                if r.get('action') == 'REGIME_FEED_DOWN']


def test_a_trusted_reading_still_drives_everything(tmp_path, monkeypatch):
    """The repair must not make a working feed behave like a broken one."""
    e = _engine(tmp_path, monkeypatch, 'panic')
    good = R.RegimeReading(
        timestamp=__import__('datetime').datetime(2026, 11, 27),
        nifty_close=23000.0, nifty_200dma=22000.0, pct_below_52w_high=-2.0,
        vix=13.0, fii_5d_net=500.0, regime='neutral', everest_active=True,
        ocean_active=False, sizing_multiplier_everest=1.0,
        sizing_multiplier_ocean=0.0, reasoning='NEUTRAL: real', feed_ok=True)
    monkeypatch.setattr('trading.regime.fetch_regime', lambda **k: good)
    e.scan()
    assert e.portfolio.last_regime == 'neutral', "a real switch must persist"
    assert e.cfg.cash_reserve_pct == 0.0
    assert not [r for r in e.portfolio.decision_journal
                if r.get('action') == 'REGIME_FEED_DOWN']


def test_one_rule_decides_the_brake_on_both_routes():
    """The fault was two routes to the same state with only one guarded."""
    import ast
    import pathlib
    from trading import engine as E
    tree = ast.parse(pathlib.Path(E.__file__).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == 'scan')
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and getattr(n.func, 'attr', '') == '_sticky_regime_brake']
    assert len(calls) == 2, f"both routes must take the same brake, found {len(calls)}"
