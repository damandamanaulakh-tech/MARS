"""XR-071 follow-up: prove an unchanged restored book can recover at startup."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from threading import Thread

import pytest

from trading.modes import MODE_LOCK
from web import db, scheduler, state
from web.tests.test_portfolio_save_guard_2026_11_20 import (
    NOW, engine, pick, storage,
)


def unconfirmed(monkeypatch, p):
    """Model a new process after successful hydration, without a current ack."""
    monkeypatch.setattr(db, 'LAST_PORTFOLIO_SAVE_AT', None)
    monkeypatch.setattr(db, 'LAST_WRITE_ERROR', None)
    monkeypatch.setattr(db, 'LAST_WRITE_ERROR_AT', None)
    p.save_error = None
    p.validation_pending = {}
    assert p.entry_block_reason() == 'portfolio_save_unconfirmed'


def financial(doc):
    return {key: deepcopy(doc[key]) for key in (
        'cash', 'positions', 'trades', 'capital_base', 'pending_orders',
        'risk_day', 'risk_day_equity', 'risk_day_killed',
        'loss_day', 'loss_count', 'loss_by_category',
    )}


def test_startup_confirms_restored_book_without_changing_financial_state(
        engine, storage, monkeypatch):
    p = engine.portfolio
    engine._open(pick('RESTORED.NS'), 10, 100., NOW)
    assert p.save() is True
    expected = financial(storage.doc)
    unconfirmed(monkeypatch, p)
    assert p._durable_book_loaded is True

    assert state._confirm_startup_portfolio_save(p) is True

    assert p.entry_block_reason() is None
    assert db.LAST_PORTFOLIO_SAVE_AT is not None
    assert financial(storage.doc) == expected


@pytest.mark.parametrize('failure', ['refused', 'unacknowledged'])
def test_startup_refusal_keeps_saved_book_and_entry_guard(
        engine, storage, monkeypatch, failure):
    p = engine.portfolio
    expected = deepcopy(storage.doc)
    unconfirmed(monkeypatch, p)
    if failure == 'refused':
        storage.error = RuntimeError('isolated storage refusal')
    else:
        storage.ack = False

    assert state._confirm_startup_portfolio_save(p) is False

    assert p.entry_block_reason() == 'portfolio_save_failed'
    assert db.LAST_PORTFOLIO_SAVE_AT is None
    assert storage.doc == expected


@pytest.mark.parametrize('connected', [True, False])
def test_startup_never_writes_an_unhydrated_fallback_book(
        engine, storage, monkeypatch, connected):
    p = engine.portfolio
    unconfirmed(monkeypatch, p)
    p._durable_book_loaded = False
    p.cash = 1.0
    expected = deepcopy(storage.doc)
    monkeypatch.setattr(db, 'is_connected', lambda: connected)
    calls = []
    monkeypatch.setattr(p, 'save', lambda: calls.append('save') or True)

    assert state._confirm_startup_portfolio_save(p) is False

    assert calls == []
    assert db.LAST_PORTFOLIO_SAVE_AT is None
    assert storage.doc == expected
    assert p.cash == 1.0


def test_local_only_startup_confirms_its_own_checkpoint(
        engine, monkeypatch):
    p = engine.portfolio
    p._durable_book_loaded = False
    monkeypatch.delenv('MONGO_URL')
    monkeypatch.setattr(db, '_CONNECTED', False)
    monkeypatch.setattr(db, '_DB', None)

    assert state._confirm_startup_portfolio_save(p) is True
    assert p.save_error is None
    assert p.entry_block_reason() is None


def test_unexpected_startup_save_exception_retains_entry_guard(
        engine, storage, monkeypatch):
    p = engine.portfolio
    unconfirmed(monkeypatch, p)
    expected = deepcopy(storage.doc)

    def fail():
        raise OSError('isolated local checkpoint failure')

    monkeypatch.setattr(p, 'save', fail)
    assert state._confirm_startup_portfolio_save(p) is False
    assert p.save_error
    assert p.entry_block_reason() == 'portfolio_save_failed'
    assert db.LAST_PORTFOLIO_SAVE_AT is None
    assert storage.doc == expected


def test_retry_recovers_unconfirmed_book_with_no_error_or_pending_events(
        engine, storage, monkeypatch):
    p = engine.portfolio
    unconfirmed(monkeypatch, p)
    expected = financial(storage.doc)

    scheduler._portfolio_save_retry()

    assert p.entry_block_reason() is None
    assert db.LAST_PORTFOLIO_SAVE_AT is not None
    assert financial(storage.doc) == expected


@pytest.mark.parametrize('previous_warning', [False, True])
def test_first_scan_gate_resumes_after_its_confirmed_save(
        engine, storage, monkeypatch, previous_warning):
    p = engine.portfolio
    unconfirmed(monkeypatch, p)
    if previous_warning:
        p.decision_journal.append({
            'action': 'MONGO_OFFLINE_HALT',
            'ts': datetime.utcnow().isoformat(),
            'why': 'Earlier process already reported the same-day pause.',
        })
    expected = financial(storage.doc)

    assert scheduler._degraded_halt('startup-test') is False

    assert p.entry_block_reason() is None
    assert financial(storage.doc) == expected


def test_existing_halt_warning_does_not_suppress_retries_after_refusal(
        engine, storage, monkeypatch):
    p = engine.portfolio
    unconfirmed(monkeypatch, p)
    p.decision_journal.append({
        'action': 'MONGO_OFFLINE_HALT',
        'ts': datetime.utcnow().isoformat(),
        'why': 'Earlier process warning.',
    })
    storage.ack = False

    assert scheduler._degraded_halt('first-refusal') is True
    assert p.entry_block_reason() == 'portfolio_save_failed'
    assert scheduler._degraded_halt('second-refusal') is True
    assert sum(row.get('action') == 'MONGO_OFFLINE_HALT'
               for row in p.decision_journal) == 1

    storage.ack = True
    assert scheduler._degraded_halt('recovered') is False
    assert p.entry_block_reason() is None


def test_unresolved_recovery_cannot_be_acknowledged_by_scan_gate(
        engine, storage, monkeypatch):
    p = engine.portfolio
    unconfirmed(monkeypatch, p)
    expected = deepcopy(storage.doc)
    monkeypatch.setattr(db, '_RECOVERED_PENDING', True)
    monkeypatch.setattr(state, 'heal_after_reconnect', lambda: None)
    saves = []
    monkeypatch.setattr(p, 'save', lambda: saves.append('save') or True)

    assert scheduler._degraded_halt('unresolved') is True

    assert saves == []
    assert db.recovered_pending() is True
    assert db.LAST_PORTFOLIO_SAVE_AT is None
    assert storage.doc == expected


def test_scan_gate_rechecks_guard_instead_of_trusting_save_return(
        engine, storage, monkeypatch):
    p = engine.portfolio
    unconfirmed(monkeypatch, p)
    expected = deepcopy(storage.doc)
    monkeypatch.setattr(p, 'save', lambda: True)

    assert scheduler._degraded_halt('no-real-ack') is True
    assert p.entry_block_reason() == 'portfolio_save_unconfirmed'
    assert db.LAST_PORTFOLIO_SAVE_AT is None
    assert storage.doc == expected


def test_scan_gate_defers_promptly_without_mutation_when_mode_lock_is_busy(
        engine, storage, monkeypatch):
    p = engine.portfolio
    unconfirmed(monkeypatch, p)
    before = deepcopy(p.decision_journal)
    expected = deepcopy(storage.doc)
    results = []
    with MODE_LOCK:
        worker = Thread(target=lambda: results.append(
            scheduler._degraded_halt('busy-test')))
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        assert results == [True]
        assert p.decision_journal == before
        assert storage.doc == expected
        assert db.LAST_PORTFOLIO_SAVE_AT is None

    assert scheduler._degraded_halt('after-lock') is False
    assert p.entry_block_reason() is None


def test_healthy_scan_gate_does_not_force_an_extra_save(
        engine, monkeypatch):
    saves = []
    monkeypatch.setattr(engine.portfolio, 'save',
                        lambda: saves.append('save') or True)

    assert scheduler._degraded_halt('already-healthy') is False
    assert saves == []


def test_save_maintenance_remains_registered_when_trading_schedulers_are_off(
        engine, monkeypatch):
    monkeypatch.setenv('DAILY_SCHEDULER', '0')
    monkeypatch.delenv('INTRADAY_SCHEDULER', raising=False)
    monkeypatch.setenv('SB_SCHEDULER_NO_START', '1')
    monkeypatch.setattr(scheduler, '_SCHED', None)

    scheduler.start_if_enabled()

    assert scheduler._SCHED is not None
    jobs = scheduler._SCHED.get_jobs()
    assert [job.id for job in jobs] == ['portfolio_save_retry']
    assert jobs[0].func is scheduler._portfolio_save_retry
    assert jobs[0].trigger.interval.total_seconds() == 60
    assert jobs[0].max_instances == 1


def isolate_bootstrap(monkeypatch, p):
    """Existing migrations are complete; exercise an ordinary unchanged boot."""
    from trading import exits, intraday_recovery
    migrations = {name: True for name in (
        'capital_order_2026_07_30_v2', 'fixed_size_order_2026_08_04',
        'exit_order_2026_08_24', 'ocean_order_2026_08_26',
        'breadth_band_2026_09_09', 'owner_batch_2026_09_10',
    )}
    overrides = {'state_dir': p.cfg.state_dir, 'mode': p.cfg.mode,
                 'starting_capital': p.cfg.starting_capital}
    monkeypatch.setattr(db, 'load_settings', lambda doc_id='default':
                        migrations if doc_id == 'migrations' else overrides)
    monkeypatch.setattr(state, '_migrate_slab_restore', lambda cfg: False)
    monkeypatch.setattr(state, '_migrate_segment_allocation', lambda *args: False)
    monkeypatch.setattr(state, '_migrate_archive_pre_mars', lambda p: {'done': False})
    monkeypatch.setattr(exits, 'retag_positions_per_mode', lambda p: 0)
    monkeypatch.setattr(intraday_recovery, 'ensure_book', lambda *args: False)
    for name in ('_CFG', '_PORTFOLIO', '_ENGINE'):
        monkeypatch.setattr(state, name, None)


@pytest.mark.parametrize('result', ['acknowledged', 'refused', 'read_unavailable'])
def test_actual_bootstrap_confirms_unchanged_book_before_broker_work(
        engine, storage, monkeypatch, result):
    """Exercise real hydration, engine construction and save-wrapper ordering."""
    p = engine.portfolio
    engine._open(pick('RESTORED.NS'), 10, 100., NOW)
    assert p.save() is True
    expected = financial(storage.doc)
    persisted = deepcopy(storage.doc)
    unconfirmed(monkeypatch, p)
    isolate_bootstrap(monkeypatch, p)

    def load(*, strict=False):
        if result == 'read_unavailable':
            if strict:
                raise RuntimeError('isolated startup read failure')
            return None
        return deepcopy(persisted)

    monkeypatch.setattr(db, 'load_portfolio', load)
    events = []
    original_replace = storage.replace_one

    def replace(*args, **kwargs):
        events.append('checkpoint')
        return original_replace(*args, **kwargs)

    def broker_attach():
        restored = state._PORTFOLIO
        assert restored is state._ENGINE.portfolio
        assert restored is not p
        events.append(('broker', restored.entry_block_reason()))

    monkeypatch.setattr(storage, 'replace_one', replace)
    monkeypatch.setattr(state, '_attach_broker_if_persisted', broker_attach)
    storage.ack = result != 'refused'

    state._bootstrap()

    if result == 'acknowledged':
        assert events == ['checkpoint', ('broker', None)]
        assert db.LAST_PORTFOLIO_SAVE_AT is not None
    elif result == 'refused':
        assert events == ['checkpoint', ('broker', 'portfolio_save_failed')]
        assert db.LAST_PORTFOLIO_SAVE_AT is None
    else:
        assert events == [('broker', 'portfolio_save_failed')]
        assert db.LAST_PORTFOLIO_SAVE_AT is None
    assert financial(storage.doc) == expected


@pytest.mark.parametrize('local_activity', [False, True])
def test_failed_startup_read_cannot_overwrite_durable_book_during_later_retries(
        engine, storage, monkeypatch, local_activity):
    p = engine.portfolio
    engine._open(pick('SAVED.NS'), 10, 100., NOW)
    assert p.save() is True
    durable = deepcopy(storage.doc)
    path = Path(p._state_path())
    if local_activity:
        local = deepcopy(durable)
        local['positions']['LOCAL.NS'] = local['positions'].pop('SAVED.NS')
        local['positions']['LOCAL.NS']['ticker'] = 'LOCAL.NS'
        local['cash'] = 1234.
        path.write_text(json.dumps(local))
    else:
        path.unlink()
    unconfirmed(monkeypatch, p)
    isolate_bootstrap(monkeypatch, p)
    phase = {'readable': False}

    def load(*, strict=False):
        if not phase['readable']:
            if strict:
                raise RuntimeError('isolated connected read failure')
            return None
        return deepcopy(durable)

    monkeypatch.setattr(db, 'load_portfolio', load)
    monkeypatch.setattr(state, '_attach_broker_if_persisted', lambda: None)
    state._bootstrap()
    # Follow the newly constructed process book, not the engine test fixture.
    monkeypatch.setattr(state, 'portfolio', lambda: state._PORTFOLIO)
    monkeypatch.setattr(state, 'engine', lambda: state._ENGINE)
    monkeypatch.setattr(state, 'cfg', lambda: state._CFG)
    restored = state.portfolio()

    for _ in range(2):
        scheduler._portfolio_save_retry()
        assert scheduler._degraded_halt('startup-read-still-failing') is True
        assert storage.doc == durable
        assert db.LAST_PORTFOLIO_SAVE_AT is None
        assert restored.entry_block_reason() is not None

    phase['readable'] = True
    scheduler._portfolio_save_retry()

    if local_activity:
        assert scheduler._degraded_halt('local-conflict') is True
        assert storage.doc == durable
        assert db.LAST_PORTFOLIO_SAVE_AT is None
        assert restored.cash == 1234.
        assert set(restored.positions) == {'LOCAL.NS'}
        assert db.recovered_pending() is True
    else:
        assert scheduler._degraded_halt('strict-read-recovered') is False
        assert financial(storage.doc) == financial(durable)
        assert restored.cash == durable['cash']
        assert set(restored.positions) == {'SAVED.NS'}
        assert db.LAST_PORTFOLIO_SAVE_AT is not None
        assert db.recovered_pending() is False


def test_failed_startup_read_followed_by_missing_book_stays_blocked(
        engine, storage, monkeypatch):
    p = engine.portfolio
    durable = deepcopy(storage.doc)
    Path(p._state_path()).unlink()
    unconfirmed(monkeypatch, p)
    isolate_bootstrap(monkeypatch, p)
    phase = {'missing': False}

    def load(*, strict=False):
        if strict and not phase['missing']:
            raise RuntimeError('isolated initial read failure')
        return None

    monkeypatch.setattr(db, 'load_portfolio', load)
    monkeypatch.setattr(state, '_attach_broker_if_persisted', lambda: None)
    state._bootstrap()
    monkeypatch.setattr(state, 'portfolio', lambda: state._PORTFOLIO)
    phase['missing'] = True

    scheduler._portfolio_save_retry()
    assert scheduler._degraded_halt('ambiguous-missing-book') is True
    assert storage.doc == durable
    assert db.LAST_PORTFOLIO_SAVE_AT is None
    assert db.recovered_pending() is True


def test_initial_confirmed_empty_store_can_initialize_a_new_book(
        engine, storage, monkeypatch):
    p = engine.portfolio
    Path(p._state_path()).unlink()
    storage.doc = None
    unconfirmed(monkeypatch, p)
    isolate_bootstrap(monkeypatch, p)
    reads = []

    def load(*, strict=False):
        reads.append(strict)
        return None

    monkeypatch.setattr(db, 'load_portfolio', load)
    monkeypatch.setattr(state, '_attach_broker_if_persisted', lambda: None)
    state._bootstrap()

    assert reads == [True]
    assert db.LAST_PORTFOLIO_SAVE_AT is not None
    assert state._PORTFOLIO.entry_block_reason() is None
    assert storage.doc['positions'] == {}
    assert storage.doc['trades'] == []
    assert storage.doc['cash'] == p.cfg.starting_capital


@pytest.mark.parametrize('failure', ['malformed_cash', 'partial_hydration'])
def test_incomplete_hydration_never_grants_write_authority(
        engine, storage, monkeypatch, failure):
    p = engine.portfolio
    durable = deepcopy(storage.doc)
    Path(p._state_path()).unlink()
    unconfirmed(monkeypatch, p)
    isolate_bootstrap(monkeypatch, p)
    unreadable = deepcopy(durable)
    if failure == 'malformed_cash':
        unreadable['cash'] = 'not a numeric balance'
    else:
        def partial_hydration(book, doc):
            book.cash = 1.0
            raise ValueError('isolated incomplete position hydration')
        monkeypatch.setattr(state, '_hydrate_portfolio_from_mongo', partial_hydration)
    monkeypatch.setattr(db, 'load_portfolio', lambda **kwargs: deepcopy(unreadable))
    monkeypatch.setattr(state, '_attach_broker_if_persisted', lambda: None)

    state._bootstrap()
    monkeypatch.setattr(state, 'portfolio', lambda: state._PORTFOLIO)
    restored = state.portfolio()

    assert getattr(restored, '_durable_book_read_confirmed', False) is False
    assert getattr(restored, '_durable_book_loaded', False) is False
    assert db.recovered_pending() is True
    assert restored.save() is False
    scheduler._portfolio_save_retry()
    assert scheduler._degraded_halt('incomplete-hydration') is True
    assert storage.doc == durable
    assert db.LAST_PORTFOLIO_SAVE_AT is None
    assert restored.entry_block_reason() is not None
