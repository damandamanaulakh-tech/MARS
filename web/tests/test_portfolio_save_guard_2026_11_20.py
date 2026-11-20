"""XR-071: connected is not durable; entry authority follows book saves."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio
from web import auth, db, scheduler, state
from web.main import app
from trading.tests.test_completed_intraday_2026_11_12 import neutral_context
from trading.tests.test_intraday_recovery_2026_11_11 import book

NOW = datetime(2026, 11, 20, 5)


class BookStore:
    def __init__(self):
        self.ack = True
        self.error = None
        self.doc = None

    def replace_one(self, where, doc, **kwargs):
        if self.error:
            raise self.error
        if self.ack is True:
            self.doc = deepcopy(doc)
        return SimpleNamespace(acknowledged=self.ack)


@pytest.fixture
def storage(monkeypatch):
    store = BookStore()
    monkeypatch.setenv('MONGO_URL', 'mongodb://isolated.invalid')
    monkeypatch.setattr(db, '_CONNECTED', True)
    monkeypatch.setattr(db, '_DB', SimpleNamespace(portfolio=store))
    monkeypatch.setattr(db, 'LAST_WRITE_ERROR', None)
    monkeypatch.setattr(db, 'LAST_WRITE_ERROR_AT', None)
    monkeypatch.setattr(db, 'LAST_PORTFOLIO_SAVE_AT', None, raising=False)
    monkeypatch.setattr(db, '_RECOVERED_PENDING', False)
    monkeypatch.setattr(db, '_BOOT_DEGRADED', False)
    monkeypatch.setattr(db, 'closed_history_policy', lambda: {})
    monkeypatch.setattr(db, 'archive_journal', lambda rows: False)
    monkeypatch.setattr(db, 'save_validation_event', lambda row: False)
    state._patch_portfolio_save_to_mirror_mongo()
    return store


@pytest.fixture
def engine(tmp_path, monkeypatch, storage):
    cfg = Config(state_dir=str(tmp_path), mode='swing', starting_capital=1_000_000)
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    e = TradingEngine(cfg, p)
    monkeypatch.setattr(state, 'portfolio', lambda: p)
    monkeypatch.setattr(state, 'engine', lambda: e)
    monkeypatch.setattr(state, 'cfg', lambda: cfg)
    monkeypatch.setattr(auth, 'current_user', lambda request: {'role': 'owner'})
    assert p.save() is True
    return e


def pick(ticker='NEW.NS'):
    return {'ticker': ticker, 'score': 80., 'reasoning': 'isolated eligible pick'}


@pytest.mark.parametrize('failure', ['quota', 'unacknowledged'])
def test_connected_refusal_blocks_booking_and_is_visible(engine, storage, failure):
    if failure == 'quota':
        storage.error = RuntimeError('space quota exceeded')
    else:
        storage.ack = False
    p = engine.portfolio
    assert p.save() is False
    before = p.cash
    assert db.is_connected() is True
    assert state.mongo_degraded() is True
    assert db.status()['degraded'] is True
    with pytest.raises(ValueError, match='portfolio_save_'):
        engine._open(pick(), 10, 100., NOW)
    assert not p.positions and p.cash == before


def test_policy_read_failure_is_also_a_failed_checkpoint(engine, monkeypatch):
    def refuse():
        raise RuntimeError('retention policy unavailable')
    monkeypatch.setattr(db, 'closed_history_policy', refuse)
    assert engine.portfolio.save() is False
    assert state.mongo_degraded() is True
    with pytest.raises(ValueError, match='portfolio_save_'):
        engine._open(pick(), 10, 100., NOW)


def test_only_confirmed_current_book_save_reopens_entries(engine, storage, monkeypatch):
    p = engine.portfolio
    storage.ack = False
    assert p.save() is False
    storage.ack = True
    # A successful write to another portfolio identity cannot clear this failure.
    assert db.save_portfolio({'cash': 1}, doc_id='unrelated') is True
    assert state.mongo_degraded() is True
    with pytest.raises(ValueError, match='portfolio_save_'):
        engine._open(pick(), 10, 100., NOW)
    assert p.save() is True
    assert state.mongo_degraded() is False
    engine._open(pick(), 10, 100., NOW)
    assert 'NEW.NS' in p.positions


def test_failure_during_slow_entry_work_is_rechecked_before_debit(engine, storage, monkeypatch):
    before = engine.portfolio.cash
    def target(*args, **kwargs):
        storage.ack = False
        assert engine.portfolio.save() is False
        return 120.
    monkeypatch.setattr(engine, '_target_for', target)
    with pytest.raises(ValueError, match='portfolio_save_'):
        engine._open(pick(), 10, 100., NOW)
    assert engine.portfolio.cash == before and not engine.portfolio.positions


def test_owner_buy_and_resting_limit_wait_without_changing_cash(engine, storage, monkeypatch):
    from trading import orders
    p = engine.portfolio
    order = orders.place_limit(p, ticker='LIMIT.NS', limit_price=101, qty=10, mode='short')
    storage.ack = False
    assert p.save() is False
    before = p.cash
    monkeypatch.setattr('trading.quote_router.fetch_quotes', lambda *a, **k: {'NEW.NS': 100.})
    response = TestClient(app).post('/scan/buy', data={'ticker':'NEW', 'qty':'10', 'mode':'swing'}, follow_redirects=False)
    assert 'portfolio_save_' in response.headers['location']
    orders._fill(engine, order, 100., NOW)
    assert order['status'] == 'open' and 'portfolio_save_' in order['note']
    assert p.cash == before and not p.positions
    storage.ack = True
    assert p.save() is True
    orders._fill(engine, order, 100., NOW)
    assert order['status'] == 'filled'


def test_backup_route_cannot_report_success_for_false(engine, storage):
    storage.ack = False
    response = TestClient(app).post('/reports/backup', follow_redirects=False)
    assert response.headers['location'] == '/reports?generated=backup_err_unacknowledged'


def test_save_failure_does_not_flush_forward_retry_work(engine, storage, monkeypatch):
    p = engine.portfolio
    storage.ack = False
    calls = []
    monkeypatch.setattr(db, 'save_validation_event', lambda row: calls.append(row) or True)
    assert p.save() is False
    pending = deepcopy(p.validation_pending)
    assert pending and not calls
    assert Portfolio.load_or_fresh(p.cfg).validation_pending == pending
    storage.ack = True
    assert p.save() is True
    assert set(pending) <= {row['_id'] for row in calls}
    assert not p.validation_pending


def test_retry_keeps_connected_quota_spill_until_archive_ack(engine, storage, monkeypatch):
    p = engine.portfolio
    entries = [{'_id':'xr071:one', 'ts':NOW.isoformat(), 'action':'TEST'}]
    assert state._spill_journal_overflow(p, entries)
    path = Path(state._overflow_path(p))
    storage.ack = False
    assert p.save() is False
    # Connection never went down. Recovery must not depend on a reconnect event.
    scheduler._portfolio_save_retry()
    assert path.exists() and state.mongo_degraded()
    storage.ack = True
    scheduler._portfolio_save_retry()
    assert path.exists() and not state.mongo_degraded()
    observed = []
    monkeypatch.setattr(db, 'archive_journal', lambda rows: observed.extend(deepcopy(rows)) or True)
    scheduler._portfolio_save_retry()
    assert not path.exists() and observed == entries


def test_local_write_exception_becomes_retryable_without_losing_book(engine, monkeypatch):
    p = engine.portfolio
    path = p._state_path
    monkeypatch.setattr(p, '_state_path', lambda: (_ for _ in ()).throw(OSError('disk full')))
    assert p.save() is False
    with pytest.raises(ValueError, match='portfolio_save_'):
        engine._open(pick(), 10, 100., NOW)
    monkeypatch.setattr(p, '_state_path', path)
    assert p.save() is True
    engine._open(pick(), 10, 100., NOW)
    assert 'NEW.NS' in p.positions


@pytest.mark.parametrize('mode', ['intraday', 'short', 'swing', 'positional', 'nifty200_momentum'])
def test_actual_scan_blocks_each_segment_before_funding_sales(tmp_path, monkeypatch, storage, neutral_context, mode):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    from trading.tests.test_completed_intraday_2026_11_12 import intraday_engine
    if mode == 'intraday':
        e, _, _ = intraday_engine(tmp_path, monkeypatch)
    else:
        e = _scan_engine(tmp_path, monkeypatch)
        e.cfg.mode = mode
        e.cfg.apply_mode_preset()
        e.cfg.min_score_to_enter = 0.
        e.cfg.breakout_threshold_pct = 1.
        e.cfg.volume_confirm_ratio = 0.
    e.portfolio.positions.clear()
    e.portfolio.cash = e.cfg.starting_capital
    storage.ack = False
    assert e.portfolio.save() is False
    before = e.portfolio.cash
    result = e.scan()
    assert not result.opened and not e.portfolio.positions
    assert e.portfolio.cash == before
    assert any(r['reason'] == 'portfolio_save_failed' for r in result.skipped)
    storage.ack = True
    assert e.portfolio.save() is True
    assert e.scan().opened == ['UPP.NS']


@pytest.mark.parametrize('price,reason', [(94., 'STOP_LOSS'), (121., 'TAKE_PROFIT'), (105., 'TRAILING_STOP')])
def test_refresh_keeps_protective_exits_and_unsaved_trades(tmp_path, monkeypatch, storage, price, reason):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _held_engine
    e, _ = _held_engine(tmp_path, monkeypatch)
    p = e.portfolio
    p.positions.pop('BBB.NS')
    pos = p.positions['AAA.NS']
    pos.stop_price, pos.target_price, pos.peak_price = 95., 120., 118.
    pos.custom_levels = True
    monkeypatch.setattr('trading.engine.routed_fetch_quotes', lambda *a, **k: {'AAA.NS': price})
    storage.ack = False
    assert p.save() is False
    e.refresh()
    assert not p.positions and p.trades[-1].reason == reason
    assert p.entry_block_reason() == 'portfolio_save_failed'
    saved_trade = deepcopy(p.trades[-1].to_dict())
    storage.ack = True
    assert p.save() is True
    assert storage.doc['trades'][-1] == saved_trade


def test_intraday_missing_quote_and_close_retry_survive_failed_saves(book, storage):
    e, _ = book
    p = e.portfolio
    storage.ack = False
    assert p.save() is False
    assert e.square_off_intraday(prices={}) == []
    pos = p.positions['DAY.NS']
    assert pos.intraday_close['status'] == 'waiting_quote'
    assert (pos.mode, pos.stop_price, pos.target_price) == ('intraday', 98.5, 104.)
    assert e.square_off_intraday(prices={'DAY.NS': 101.}) == ['DAY.NS']
    trade = deepcopy(p.trades[-1].to_dict())
    assert trade['reason'] == 'SQUARE_OFF' and p.save_error
    storage.ack = True
    assert p.save() is True
    assert storage.doc['trades'][-1] == trade
    assert 'HOLD.NS' in storage.doc['positions']


def test_pyramid_is_a_buy_and_does_not_bypass_save_guard(tmp_path, monkeypatch, storage):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _held_engine
    e, _ = _held_engine(tmp_path, monkeypatch)
    p = e.portfolio
    p.positions.pop('BBB.NS')
    pos = p.positions['AAA.NS']
    pos.tags.append('owner_pick')  # skip unrelated score-decay exit
    pos.entry_date = NOW
    e.cfg.pyramid_enabled = True
    e.cfg.pyramid_trigger_pct = .01
    e.cfg.pyramid_add_pct = .5
    storage.ack = False
    assert p.save() is False
    before = pos.qty
    e.refresh()
    assert pos.qty == before and pos.pyramid_adds == 0
    storage.ack = True
    assert p.save() is True
    e.refresh()
    assert pos.qty > before and pos.pyramid_adds == 1


def test_owner_batch_rechecks_after_an_intervening_failed_save(engine, storage, monkeypatch):
    from web import buy_desk
    p = engine.portfolio
    cfg = engine.mode_cfg(buy_desk.DESK_MODE)
    real = engine._open
    def fail_after_first(*args, **kwargs):
        result = real(*args, **kwargs)
        storage.ack = False
        assert p.save() is False
        return result
    monkeypatch.setattr(engine, '_open', fail_after_first)
    rows = [{'ticker': t, 'score':80., 'qty':10, 'price':100., 'recommended':True} for t in ('ONE.NS', 'TWO.NS')]
    result = buy_desk.execute(engine, p, cfg, rows, NOW, budget_rs=100_000, market_hours_bypassed=False)
    assert list(p.positions) == ['ONE.NS']
    assert result['skipped'] == [{'ticker':'TWO.NS', 'reason':'portfolio_save_failed'}]


def test_fresh_process_needs_current_save_even_with_a_connected_database(engine, storage, monkeypatch):
    p = Portfolio.load_or_fresh(engine.cfg)
    state._hydrate_portfolio_from_mongo(p, storage.doc)
    p._entry_probe = db.portfolio_entry_block_reason
    monkeypatch.setattr(db, 'LAST_PORTFOLIO_SAVE_AT', None)
    monkeypatch.setattr(db, 'LAST_WRITE_ERROR', None)
    e = TradingEngine(engine.cfg, p)
    with pytest.raises(ValueError, match='portfolio_save_unconfirmed'):
        e._open(pick(), 10, 100., NOW)
    assert p.save() is True
    e._open(pick(), 10, 100., NOW)
    assert 'NEW.NS' in p.positions


def test_retry_is_registered_without_intraday_and_recovers_empty_book(engine, storage, monkeypatch):
    monkeypatch.setenv('DAILY_SCHEDULER', '1')
    monkeypatch.setenv('INTRADAY_SCHEDULER', '0')
    monkeypatch.setattr(scheduler, '_SCHED', None)
    scheduler.start_if_enabled()
    job = scheduler._SCHED.get_job('portfolio_save_retry')
    assert job.func is scheduler._portfolio_save_retry
    assert job.trigger.interval.total_seconds() == 60
    assert job.max_instances == 1
    storage.ack = False
    assert engine.portfolio.save() is False
    scheduler._portfolio_save_retry()
    assert state.mongo_degraded()
    storage.ack = True
    scheduler._portfolio_save_retry()
    assert not engine.portfolio.positions and not state.mongo_degraded()


def test_retry_defers_instead_of_holding_up_protective_work(engine, storage, monkeypatch):
    from threading import Thread
    from trading.modes import MODE_LOCK
    storage.ack = False
    assert engine.portfolio.save() is False
    storage.ack = True
    with MODE_LOCK:
        thread = Thread(target=scheduler._portfolio_save_retry)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
        assert engine.portfolio.save_error
    scheduler._portfolio_save_retry()
    assert not engine.portfolio.save_error


def test_spill_retry_is_bounded_and_keeps_identities_after_partial_ack(engine, monkeypatch):
    import json
    p = engine.portfolio
    path = Path(state._overflow_path(p))
    # Legacy spill rows have no IDs. Rewriting acknowledged prefixes must not
    # change the identity of later rows on retries.
    path.write_text(''.join(json.dumps({'ts':NOW.isoformat(), 'i':i})+'\n' for i in range(205)))
    batches = []
    monkeypatch.setattr(db, 'archive_journal', lambda rows: batches.append(deepcopy(rows)) or True)
    assert state.replay_journal_spill(p) is False
    assert len(batches) == 1 and len(batches[0]) == 100
    tail = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(tail) == 105 and all('_id' in row for row in tail)
    monkeypatch.setattr(db, 'archive_journal', lambda rows: False)
    assert state.replay_journal_spill(p) is False
    assert [json.loads(line) for line in path.read_text().splitlines()] == tail
    monkeypatch.setattr(db, 'archive_journal', lambda rows: batches.append(deepcopy(rows)) or True)
    assert state.replay_journal_spill(p) is False
    assert state.replay_journal_spill(p) is True
    assert not path.exists() and [len(b) for b in batches] == [100, 100, 5]
    assert batches[1] + batches[2] == tail


def test_entry_waits_for_inflight_checkpoint_and_observes_its_failure(engine, storage, monkeypatch):
    from threading import Event, Thread
    started, release, entered = Event(), Event(), Event()
    results = []
    def replace(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return SimpleNamespace(acknowledged=False)
    monkeypatch.setattr(storage, 'replace_one', replace)
    saver = Thread(target=lambda: results.append(engine.portfolio.save()))
    def buy():
        entered.set()
        try:
            engine._open(pick(), 10, 100., NOW)
        except ValueError as exc:
            results.append(str(exc))
    saver.start()
    assert started.wait(2)
    buyer = Thread(target=buy)
    buyer.start()
    assert entered.wait(2)
    assert not engine.portfolio.positions
    release.set()
    saver.join(3)
    buyer.join(3)
    assert not saver.is_alive() and not buyer.is_alive()
    assert results == [False, 'portfolio_save_failed']
    assert not engine.portfolio.positions


def test_auxiliary_export_failure_keeps_retrying_after_book_recovers(engine, storage, monkeypatch):
    assert engine.portfolio.validation_pending
    assert not engine.portfolio.entry_block_reason()
    exported = []
    monkeypatch.setattr(db, 'save_validation_event', lambda row: exported.append(row) or True)
    scheduler._portfolio_save_retry()
    assert exported and not engine.portfolio.validation_pending


def test_existing_holding_is_not_rotated_to_fund_a_blocked_buy(tmp_path, monkeypatch, storage):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)
    e.cfg.category_budgets_enabled = False
    storage.ack = False
    assert e.portfolio.save() is False
    assert e._rotation_exits([pick()], NOW) == []
    assert 'OLD.NS' in e.portfolio.positions and not e.portfolio.trades


def test_reconnect_event_is_not_consumed_before_book_ack(engine, storage, monkeypatch):
    monkeypatch.setattr(db, '_RECOVERED_PENDING', True)
    storage.ack = False
    state.heal_after_reconnect()
    assert db.recovered_pending() and state.mongo_degraded()
    storage.ack = True
    state.heal_after_reconnect()
    assert not db.recovered_pending() and not state.mongo_degraded()


@pytest.mark.parametrize('read_state', ['missing', 'refused', 'conflict'])
def test_recovery_retry_cannot_acknowledge_a_phantom_or_conflicting_book(engine, storage, monkeypatch, read_state):
    from trading.paper import PaperPosition
    p = engine.portfolio
    p._durable_book_loaded = False
    monkeypatch.setattr(db, '_BOOT_DEGRADED', True)
    monkeypatch.setattr(db, '_RECOVERED_PENDING', True)
    before = deepcopy(storage.doc)
    def load(**kwargs):
        assert kwargs == {'strict':True}
        if read_state == 'refused':
            raise RuntimeError('read refused')
        return None if read_state == 'missing' else deepcopy(before)
    monkeypatch.setattr(db, 'load_portfolio', load)
    if read_state == 'conflict':
        p.positions['PHANTOM.NS'] = PaperPosition(ticker='PHANTOM.NS', qty=1, entry_price=100., entry_date=NOW, sector='Test', peak_price=100., entry_score=80., reasoning='isolated conflict')
    scheduler._portfolio_save_retry()
    assert db.recovered_pending() and state.mongo_degraded()
    assert storage.doc == before
    assert p.save() is False and storage.doc == before


def test_first_reconnect_hydrates_before_any_retry_save(engine, storage, monkeypatch):
    p = engine.portfolio
    original = deepcopy(storage.doc)
    p._durable_book_loaded = False
    p.cash = 1.
    monkeypatch.setattr(db, '_BOOT_DEGRADED', True)
    monkeypatch.setattr(db, '_CONNECTED', False)
    monkeypatch.setattr(db, '_LAST_ATTEMPT', 0.)
    monkeypatch.setattr(db, '_try_connect', lambda: setattr(db, '_CONNECTED', True))
    monkeypatch.setattr(db, 'load_portfolio', lambda **kwargs: deepcopy(original))
    scheduler._portfolio_save_retry()
    assert p.cash == original['cash'] == storage.doc['cash']
    assert not db.recovered_pending() and not state.mongo_degraded()


