"""Independent lifecycle checks for explicit, bounded, non-replayed paper batches."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import threading
from types import SimpleNamespace
from uuid import UUID

import pytest

from trading.config import Config
from trading.paper import Portfolio
from web import scheduler, strategy_batch as batch

DAY = '2026-11-26'
ACTOR = 'owner@example.invalid'
MODES = ('nifty200_momentum', 'short', 'swing', 'positional')


def request_id(n=1, day=DAY):
    return f'{day}:{UUID(int=n, version=4)}'


@pytest.fixture
def rig(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path), mode='intraday')
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    calls, refreshes, workers, snapshots, authorities = [], [], [], [], []
    active = []
    @contextmanager
    def authority(**kwargs):
        assert not active
        active.append(kwargs)
        authorities.append(deepcopy(kwargs))
        try:
            yield
        finally:
            active.clear()
    def refresh():
        assert active
        refreshes.append(cfg.mode)
    def scan():
        assert active and cfg.mode in MODES
        calls.append(cfg.mode)
        opened = [f'{cfg.mode}_{i}.NS' for i in range(7)]
        for ticker in opened:
            p.positions[ticker] = SimpleNamespace(mode=cfg.mode, tags=[])
        if cfg.mode == MODES[0]:
            opened.append('OCEAN.NS')
            p.positions['OCEAN.NS'] = SimpleNamespace(mode=cfg.mode, tags=['ocean'])
        return SimpleNamespace(ranked=[{}] * 12, picks=[{}] * 9,
                               opened=opened, skipped=[
                                   {'ticker': 'NO_FUNDS.NS', 'reason': 'category_budget_full'},
                                   {'ticker': 'NO_PRICE.NS', 'reason': 'quote_unavailable'},
                                   {'ticker': 'NO_FUNDS2.NS', 'reason': 'category_budget_full'},
                               ])
    def forbidden(*args, **kwargs):
        raise AssertionError('A strategy batch must never process pending owner orders')
    eng = SimpleNamespace(cfg=cfg, portfolio=p, scan=scan, refresh=refresh,
                          strategy_batch_request=authority, process_orders=forbidden,
                          process_pending_orders=forbidden)
    def save():
        snapshots.append({'ledger': deepcopy(p.strategy_batch), 'cfg': deepcopy(vars(cfg)),
                          'scans': list(calls), 'refreshes': list(refreshes)})
        return True
    monkeypatch.setattr(p, 'save', save)
    monkeypatch.setattr(batch, '_launch', workers.append)
    monkeypatch.setattr(batch, '_LOCK', threading.Lock())
    monkeypatch.setattr(batch, '_ACTIVE', None)
    monkeypatch.setattr(batch, 'MODE_LOCK', threading.RLock())
    monkeypatch.setattr(batch, '_today_ist', lambda: DAY)
    monkeypatch.setattr(batch, '_utc_now', lambda: datetime(2026, 11, 26, 6, tzinfo=timezone.utc))
    monkeypatch.setattr(batch, '_session_open', lambda: True)
    monkeypatch.setattr(scheduler, '_ROTATION_LOCK', threading.Lock())
    monkeypatch.setattr('web.memory_relief.release', lambda *a: None)
    return SimpleNamespace(cfg=cfg, p=p, e=eng, calls=calls, refreshes=refreshes,
                           workers=workers, snapshots=snapshots, active=active,
                           authorities=authorities)


def start(rig, n=1, **kwargs):
    return batch.start(rig.e, requested_by=ACTOR, request_id=request_id(n), **kwargs)


def run(rig):
    assert len(rig.workers) == 1
    rig.workers[0]()
    return batch.status(rig.p)


def test_all_eligible_results_are_retained_per_segment_with_no_three_or_five_cap(rig):
    cfg_before = deepcopy(vars(rig.cfg))
    accepted = start(rig, override_measured_performance=True)
    assert accepted['status'] == 'accepted' and rig.calls == rig.refreshes == []
    assert accepted['modes'] == list(MODES) and accepted['include_ocean'] is True
    report = run(rig)
    assert report['status'] == 'complete'
    assert rig.calls == rig.refreshes == list(MODES)
    assert report['opened_total'] == 29
    assert all(len(report['opened_segments'][mode]) == 7 for mode in MODES)
    assert report['opened_segments']['ocean'] == ['OCEAN.NS']
    assert 'intraday' not in report['opened_segments']
    assert all(summary['gate_counts'] == {'category_budget_full': 2, 'quote_unavailable': 1}
               for summary in report['passes'].values())
    assert rig.authorities == [{'batch_id': request_id(), 'actor': ACTOR,
                               'authorized_date_ist': DAY,
                               'override_measured_performance': True}]
    assert vars(rig.cfg) == cfg_before and rig.active == []
    assert rig.snapshots[0]['scans'] == rig.snapshots[0]['refreshes'] == []
    assert rig.snapshots[-1]['cfg'] == cfg_before
    assert rig.snapshots[-1]['ledger']['requests'][request_id()]['status'] == 'complete'
    assert batch._ACTIVE is None and not scheduler._ROTATION_LOCK.locked()


def test_duplicate_request_reports_same_work_without_scans_or_another_worker(rig):
    start(rig)
    duplicate = batch.start(rig.e, requested_by='different-owner@example.invalid',
                            request_id=request_id(), override_measured_performance=True)
    assert duplicate['status'] == 'accepted'
    assert duplicate['requested_by'] == ACTOR
    assert duplicate['override_measured_performance'] is False
    assert len(rig.workers) == 1
    finished = run(rig)
    snapshots = len(rig.snapshots)
    assert start(rig) == finished
    assert len(rig.workers) == 1 and len(rig.snapshots) == snapshots
    assert rig.calls == list(MODES)


@pytest.mark.parametrize('failure_at,completed_scans', [(1, 0), (2, 0), (3, 1), (4, 1), (10, 4)])
def test_each_unacknowledged_checkpoint_stops_further_entries(rig, monkeypatch, failure_at, completed_scans):
    original_save = rig.p.save
    count = 0
    def save():
        nonlocal count
        count += 1
        if count == failure_at:
            return False
        return original_save()
    monkeypatch.setattr(rig.p, 'save', save)
    before = deepcopy(vars(rig.cfg))
    start(rig)
    report = run(rig)
    assert report['status'] == 'save_failed'
    assert len(rig.calls) == completed_scans
    assert vars(rig.cfg) == before and rig.active == []
    assert batch._ACTIVE is None and not scheduler._ROTATION_LOCK.locked()
    calls_before = list(rig.calls)
    start(rig)
    assert len(rig.workers) == 1 and rig.calls == calls_before


def test_save_exception_cannot_launch_scan_or_leave_locks_held(rig, monkeypatch):
    def fail():
        raise OSError('isolated disk error')
    monkeypatch.setattr(rig.p, 'save', fail)
    start(rig)
    assert run(rig)['status'] == 'save_failed'
    assert rig.calls == rig.refreshes == []
    assert batch._ACTIVE is None and not scheduler._ROTATION_LOCK.locked()
    assert batch.MODE_LOCK.acquire(blocking=False)
    batch.MODE_LOCK.release()


def test_existing_unconfirmed_save_blocks_request_before_worker_creation(rig):
    rig.p.save_error = 'isolated save refusal'
    before = deepcopy(rig.p.strategy_batch)
    assert start(rig)['status'] == 'save_failed'
    assert rig.workers == [] and rig.p.strategy_batch == before


def test_protective_refresh_save_failure_prevents_the_following_entry_scan(rig, monkeypatch):
    normal_refresh = rig.e.refresh
    def refresh():
        normal_refresh()
        rig.p.save_error = 'Protective exit was not acknowledged'
    monkeypatch.setattr(rig.e, 'refresh', refresh)
    start(rig)
    assert run(rig)['status'] == 'save_failed'
    assert rig.refreshes == [MODES[0]] and rig.calls == []
    assert batch._ACTIVE is None and not scheduler._ROTATION_LOCK.locked()


@pytest.mark.parametrize('fail_in', ['refresh', 'scan'])
def test_exception_restores_exact_preset_and_is_not_retried(rig, monkeypatch, fail_in):
    before = deepcopy(vars(rig.cfg))
    normal = getattr(rig.e, fail_in)
    def fail():
        if rig.cfg.mode == 'short':
            raise RuntimeError('isolated engine interruption')
        return normal()
    monkeypatch.setattr(rig.e, fail_in, fail)
    start(rig)
    report = run(rig)
    assert report['status'] == 'failed'
    assert rig.calls == [MODES[0]]
    assert report['passes'][MODES[0]]['opened_count'] == 8
    assert vars(rig.cfg) == before and rig.active == []
    assert batch._ACTIVE is None and not scheduler._ROTATION_LOCK.locked()
    assert start(rig)['status'] == 'failed'
    assert len(rig.workers) == 1


def test_midscan_failure_reports_partial_buy_count_unknown_not_zero(rig, monkeypatch):
    normal_scan = rig.e.scan
    def scan():
        if rig.cfg.mode != 'short':
            return normal_scan()
        evidence = {'path': 'owner_strategy_batch', 'strategy_batch': {
            'batch_id': request_id(), 'original_path': 'scan'}}
        rig.p.positions['PARTIAL.NS'] = SimpleNamespace(
            mode='short', tags=[], trade_evidence={'entry': evidence})
        rig.p.cash -= 100_000
        rig.p.decision_journal.append({'action': 'BUY', 'ticker': 'PARTIAL.NS',
                                       'mode': 'short', 'evidence': evidence})
        raise RuntimeError('later candidate failed after a booked entry')
    monkeypatch.setattr(rig.e, 'scan', scan)
    start(rig)
    report = run(rig)
    assert report['status'] == 'failed'
    assert 'PARTIAL.NS' in rig.p.positions
    assert report['opened_total'] is None
    assert report['passes']['short']['opened_count'] is None
    assert report['passes'][MODES[0]]['opened_count'] == 8
    assert len(report['opened']) == 8  # acknowledged completed-pass entries remain known
    assert start(rig)['status'] == 'failed' and len(rig.workers) == 1


def test_process_interruption_keeps_completed_pass_and_never_restarts_implicitly(rig, monkeypatch):
    original = rig.e.scan
    before = deepcopy(vars(rig.cfg))
    def interrupt():
        if rig.cfg.mode == 'short':
            raise KeyboardInterrupt('isolated process interruption')
        return original()
    monkeypatch.setattr(rig.e, 'scan', interrupt)
    start(rig)
    with pytest.raises(KeyboardInterrupt):
        run(rig)
    assert vars(rig.cfg) == before and rig.active == []
    report = batch.status(rig.p)
    assert report['status'] == 'interrupted'
    assert report['opened_total'] is None
    assert report['passes'][MODES[0]]['opened_count'] == 8
    assert start(rig)['status'] == 'interrupted' and len(rig.workers) == 1
    assert batch._ACTIVE is None and not scheduler._ROTATION_LOCK.locked()


def test_session_expiry_between_passes_retains_completed_work_without_next_scan(rig, monkeypatch):
    normal_scan = rig.e.scan
    def scan():
        result = normal_scan()
        monkeypatch.setattr(batch, '_session_open', lambda: False)
        return result
    monkeypatch.setattr(rig.e, 'scan', scan)
    start(rig)
    report = run(rig)
    assert report['status'] == 'expired'
    assert rig.calls == [MODES[0]] and rig.refreshes == [MODES[0]]
    assert report['opened_total'] == 8
    assert report['passes'][MODES[0]]['status'] == 'complete'
    assert start(rig)['status'] == 'expired' and len(rig.workers) == 1


def test_midnight_before_worker_never_executes_yesterday_request(rig, monkeypatch):
    start(rig)
    monkeypatch.setattr(batch, '_today_ist', lambda: '2026-11-27')
    assert run(rig)['status'] == 'expired'
    assert rig.calls == rig.refreshes == []


@pytest.mark.parametrize('kind', ['rotation', 'active_batch'])
def test_parallel_activity_returns_busy_without_queuing(rig, monkeypatch, kind):
    if kind == 'rotation':
        scheduler._ROTATION_LOCK.acquire()
    else:
        monkeypatch.setattr(batch, '_ACTIVE', {'portfolio': 1, 'request_id': 'elsewhere'})
    try:
        assert start(rig)['status'] == 'busy'
        assert rig.workers == [] and rig.p.strategy_batch == {}
    finally:
        if kind == 'rotation':
            scheduler._ROTATION_LOCK.release()


def test_worker_owns_mode_rlock_on_a_real_background_thread(rig):
    start(rig)
    worker = threading.Thread(target=rig.workers[0])
    worker.start()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert batch.status(rig.p)['status'] == 'complete'
    assert rig.calls == list(MODES)
    assert batch.MODE_LOCK.acquire(blocking=False)
    batch.MODE_LOCK.release()
    assert batch._ACTIVE is None and not scheduler._ROTATION_LOCK.locked()


def test_busy_mode_lock_is_nonblocking_and_cannot_transfer_rlock_ownership(rig):
    # Hold the RLock in this thread while the actual worker tries from another.
    batch.MODE_LOCK.acquire()
    try:
        start(rig)
        worker = threading.Thread(target=rig.workers[0])
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive(), 'A busy mode lock must not queue an expired batch'
        assert batch.status(rig.p)['status'] == 'busy'
        assert rig.calls == rig.refreshes == []
        assert batch._ACTIVE is None and not scheduler._ROTATION_LOCK.locked()
    finally:
        batch.MODE_LOCK.release()


def test_failed_worker_launch_releases_reservation_and_duplicate_never_retries(rig, monkeypatch):
    def fail(worker):
        raise RuntimeError('isolated thread start failure')
    monkeypatch.setattr(batch, '_launch', fail)
    assert start(rig)['status'] == 'failed'
    assert batch._ACTIVE is None and not scheduler._ROTATION_LOCK.locked()
    monkeypatch.setattr(batch, '_launch', rig.workers.append)
    assert start(rig)['status'] == 'failed' and rig.workers == []


@pytest.mark.parametrize('raw', [None, '', '2026-11-26', '2026-11-26:not-a-uuid',
                                  '2026-11-26:11111111-1111-1111-8111-111111111111'])
def test_malformed_or_non_uuid4_request_never_reserves_work(rig, raw):
    answer = batch.start(rig.e, ACTOR, raw)
    assert answer['status'] == 'rejected'
    assert rig.workers == [] and rig.p.strategy_batch == {}


@pytest.mark.parametrize('field,value', [('requested_by', ''), ('requested_by', 'x' * 201),
                                        ('override_measured_performance', 'true')])
def test_invalid_authority_never_reserves_work(rig, field, value):
    args = {'requested_by': ACTOR, 'request_id': request_id()}
    args[field] = value
    assert batch.start(rig.e, **args)['status'] == 'rejected'
    assert rig.workers == [] and rig.p.strategy_batch == {}


def test_old_or_afterhours_requests_are_not_queued(rig, monkeypatch):
    assert batch.start(rig.e, ACTOR, request_id(day='2026-11-25'))['status'] == 'expired'
    monkeypatch.setattr(batch, '_session_open', lambda: False)
    assert start(rig)['status'] == 'rejected'
    assert rig.workers == [] and rig.p.strategy_batch == {}


def test_sixteen_ids_are_retained_and_overflow_rejected_without_eviction(rig):
    for n in range(1, 17):
        start(rig, n)
        rig.workers[-1]()
    ledger = deepcopy(rig.p.strategy_batch)
    assert len(ledger['requests']) == 16
    assert start(rig, 17)['status'] == 'rejected'
    assert rig.p.strategy_batch == ledger and len(rig.workers) == 16
    assert start(rig, 1)['status'] == 'complete'
    assert rig.p.strategy_batch == ledger and len(rig.workers) == 16
    assert not scheduler._ROTATION_LOCK.locked()


def test_status_of_orphan_is_readonly_and_duplicate_never_replays(rig, monkeypatch):
    record = {'status': 'running', 'request_id': request_id(), 'passes': {MODES[0]: {
        'status': 'complete', 'opened_count': 2}}, 'opened_total': 2}
    rig.p.strategy_batch = {'date': DAY, 'requests': {request_id(): record},
                            'latest_request_id': request_id()}
    before = deepcopy(rig.p.strategy_batch)
    def forbidden():
        raise AssertionError('Reading orphan status must never save')
    monkeypatch.setattr(rig.p, 'save', forbidden)
    assert batch.status(rig.p)['status'] == 'interrupted'
    assert start(rig)['status'] == 'interrupted'
    assert rig.p.strategy_batch == before and rig.workers == []
    assert rig.calls == rig.refreshes == []
