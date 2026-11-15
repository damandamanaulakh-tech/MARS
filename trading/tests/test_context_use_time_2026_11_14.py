"""SEQ-06: source validity belongs to the decision time, not batch startup."""
from copy import deepcopy
from datetime import timedelta

import pytest

from trading import context_inputs as ci, sector_identity as si
from trading.config import Config
from trading.tests.test_market_context_2026_11_13 import NOW, daily, source_snapshot


def prepared(monkeypatch, snapshot, *, isin=None):
    monkeypatch.setattr(si, 'snapshot', lambda: snapshot)
    monkeypatch.setattr('trading.universe_refresh.read_existing',
                        lambda path: {'AAA.NS': {'isin': isin}} if isin else {})
    monkeypatch.setattr(ci, '_sector_reader', lambda since: [
        {'date': str(day.date()), 'sector': 'IT', 'close': value}
        for day, value in daily()['Close'].items()])
    cfg = Config(momentum_lookback_days=2)
    return cfg, ci.prepare(['AAA.NS'], cfg, NOW)


def test_source_expiring_during_batch_is_unknown_at_actual_decision(monkeypatch):
    snapshot = source_snapshot(at=(NOW-timedelta(days=7)+timedelta(seconds=1)).isoformat())
    cfg, inputs = prepared(monkeypatch, snapshot)
    assert inputs['identities']['AAA.NS']['sector_status'] == 'verified'
    result = ci.observation(inputs, 'AAA.NS', daily(), daily(), cfg, NOW+timedelta(seconds=2))
    assert result['identity']['status'] == 'unknown'
    assert result['identity']['reason'] == 'source_stale'
    assert result['sector']['return_pct'] is None
    assert result['stock_vs_sector_pp'] is None
    assert result['stock_vs_market_pp'] == pytest.approx(0)


def test_expired_sector_does_not_discard_still_fresh_official_stock_identity(monkeypatch):
    snapshot = source_snapshot(('IT', 'Total Market'))
    snapshot['sources']['IT']['observed_at'] = (NOW-timedelta(days=7)+timedelta(seconds=1)).isoformat()
    cfg, inputs = prepared(monkeypatch, snapshot)
    result = ci.observation(inputs, 'AAA.NS', daily(), daily(), cfg, NOW+timedelta(seconds=2))
    assert result['identity']['status'] == 'verified'
    assert result['identity']['sector_status'] == 'unknown'
    assert result['identity']['sector_reason'] == 'no_verified_sector_index_membership'
    assert result['sector']['return_pct'] is None


def test_prepared_source_is_frozen_without_a_new_registry_or_master_read(monkeypatch):
    snapshot = source_snapshot()
    cfg, inputs = prepared(monkeypatch, snapshot, isin='INE000A01010')
    def forbidden(*args, **kwargs):
        raise AssertionError('decision reloaded mutable source or instrument master')
    monkeypatch.setattr(si, 'snapshot', forbidden)
    monkeypatch.setattr('trading.universe_refresh.read_existing', forbidden)
    snapshot['sources']['IT']['rows']['AAA.NS']['isin'] = 'INE999A01010'
    snapshot['sources']['IT']['observed_at'] = '2026-01-01T00:00:00+00:00'
    result = ci.observation(inputs, 'AAA.NS', daily(), daily(), cfg, NOW+timedelta(seconds=2))
    assert result['identity']['isin'] == 'INE000A01010'
    assert result['identity']['instrument_match'] == 'matched'
    assert result['status'] == 'ready'


def test_revalidation_preserves_instrument_conflict(monkeypatch):
    cfg, inputs = prepared(monkeypatch, source_snapshot(), isin='INE999A01010')
    result = ci.observation(inputs, 'AAA.NS', daily(), daily(), cfg, NOW+timedelta(seconds=2))
    assert result['identity']['reason'] == 'instrument_isin_conflict'
    assert result['identity']['status'] == 'unknown'
    assert result['sector']['return_pct'] is None


@pytest.mark.parametrize('at', ['2026-11-13T06:00:00', '2026-11-13', 'not-a-time'])
def test_unproved_source_timezone_or_invalid_time_is_unknown(at):
    result = si.resolve('AAA.NS', source_snapshot(at=at), NOW)
    assert result['status'] == 'unknown'
    assert result['reason'] == 'source_time_unavailable'


def test_exact_expiry_boundary_is_retained_but_next_instant_is_not(monkeypatch):
    at = NOW-timedelta(days=7)
    cfg, inputs = prepared(monkeypatch, source_snapshot(at=at.isoformat()))
    before = deepcopy(inputs['identities'])
    current = ci.observation(inputs, 'AAA.NS', daily(), daily(), cfg, NOW)
    later = ci.observation(inputs, 'AAA.NS', daily(), daily(), cfg, NOW+timedelta(microseconds=1))
    assert current['identity']['sector_status'] == 'verified'
    assert later['identity']['sector_status'] == 'unknown'
    assert inputs['identities'] == before
