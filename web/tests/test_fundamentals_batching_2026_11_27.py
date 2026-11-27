"""NEW-01 / NEW-02: the monthly company figures are saved in batches, and
progress is written down after every one.

NEW-01: the save sent ONE database round trip per row, in a plain loop, after
the fetch's own time budget had already expired. A first-of-the-month run
fetches ~929 symbols at ~60 rows each — about 55,700 round trips with the
weekly universe rebuild and the OHLCV pull queued behind them.

NEW-02: the progress ledger was written ONCE, strictly after that save, so
anything that ended the process during it wrote neither the done list nor the
remaining count. The next run started again from the top of the alphabet.
"""
from datetime import date
from types import SimpleNamespace

import pytest

from web import db, scheduler, state
from web.tests.test_fundamentals_budget_2026_10_12 import (
    _expected_symbols, _ledger_store, _own,
)


class Collection:
    """Counts what a save really asks of the database."""

    def __init__(self):
        self.replace_calls = 0
        self.bulk_calls = 0
        self.ops = 0
        self.stored = {}

    def replace_one(self, where, doc, **kwargs):
        self.replace_calls += 1
        self.stored[where['_id']] = doc
        return SimpleNamespace(acknowledged=True, upserted_count=1, matched_count=0)

    def bulk_write(self, ops, ordered=True):
        self.bulk_calls += 1
        self.ops += len(ops)
        for op in ops:
            self.stored[op._filter['_id']] = op._doc
        return SimpleNamespace(acknowledged=True, upserted_count=len(ops), matched_count=0)


@pytest.fixture
def collection(monkeypatch):
    c = Collection()
    monkeypatch.setattr(db, '_CONNECTED', True)
    monkeypatch.setattr(db, '_DB', SimpleNamespace(fundamentals=c))
    return c


def rows(n_symbols, per_symbol=60):
    return [{'symbol': f'S{i:04d}', 'field': f'f{j:02d}', 'as_of': '2026-03-31',
             'value': float(j)}
            for i in range(n_symbols) for j in range(per_symbol)]


# --------------------------------------------------------------- NEW-01

def test_a_months_worth_of_figures_costs_round_trips_in_the_hundreds(collection):
    """929 symbols x 60 fields: the shape of a real first-of-month run."""
    many = rows(929)
    assert len(many) == 55_740
    stored = db.save_fundamentals_rows(many)
    assert stored == 55_740
    assert len(collection.stored) == 55_740          # every row landed
    assert collection.replace_calls == 0             # not one at a time
    assert collection.bulk_calls == 112              # ceil(55740 / 500)
    assert collection.ops == 55_740


def test_the_keys_and_rows_are_unchanged_by_the_batching(collection):
    db.save_fundamentals_rows([
        {'symbol': 'TCS', 'field': 'annualNetIncome', 'as_of': '2026-03-31', 'value': 7.0},
        {'symbol': 'M&M', 'field': 'annualTotalRevenue', 'as_of': '2026-03-31', 'value': 9.0},
    ])
    assert set(collection.stored) == {'TCS|annualNetIncome|2026-03-31',
                                      'M&M|annualTotalRevenue|2026-03-31'}
    doc = collection.stored['TCS|annualNetIncome|2026-03-31']
    assert doc['symbol'] == 'TCS' and doc['value'] == 7.0
    assert doc['_id'] == 'TCS|annualNetIncome|2026-03-31'


def test_a_row_missing_its_key_is_skipped_rather_than_stored_under_a_blank_id(collection):
    stored = db.save_fundamentals_rows([
        {'symbol': 'TCS', 'field': 'annualNetIncome', 'as_of': '2026-03-31', 'value': 1.0},
        {'symbol': '', 'field': 'annualNetIncome', 'as_of': '2026-03-31', 'value': 2.0},
        {'symbol': 'TCS', 'field': '', 'as_of': '2026-03-31', 'value': 3.0},
        {'symbol': 'TCS', 'field': 'annualNetIncome', 'as_of': '', 'value': 4.0},
    ])
    assert stored == 1 and len(collection.stored) == 1


def test_only_what_the_store_acknowledged_is_counted(collection, monkeypatch):
    """A refused write must never be journalled as a stored datapoint."""
    monkeypatch.setattr(collection, 'bulk_write',
                        lambda ops, ordered=True: SimpleNamespace(acknowledged=False))
    assert db.save_fundamentals_rows(rows(2)) == 0


def test_a_partly_refused_chunk_counts_only_the_part_that_landed(collection, monkeypatch):
    from pymongo.errors import BulkWriteError

    def refuse(ops, ordered=True):
        raise BulkWriteError({'nUpserted': 300, 'nMatched': 20,
                              'writeErrors': [{'errmsg': 'space quota exceeded'}]})
    monkeypatch.setattr(collection, 'bulk_write', refuse)
    one_chunk = rows(8)                      # 480 rows = a single chunk of 500
    assert len(one_chunk) < 500
    assert db.save_fundamentals_rows(one_chunk) == 320


# --------------------------------------------------------------- NEW-02

def _run(monkeypatch, batches, budget='600'):
    """Drive the real job with a stubbed fetcher; return (ledger, calls)."""
    store, saved = _ledger_store(monkeypatch)
    calls = []

    def fetch(todo, time_budget_s=None, **k):
        calls.append(list(todo))
        answer = batches.pop(0) if batches else {}
        n = answer.get('ok', len(todo))
        got = list(todo[:n]) if n else []
        return {'rows': [{'symbol': s, 'field': 'annualNetIncome',
                          'as_of': '2026-03-31', 'value': 1.0} for s in got],
                'n_ok': len(got), 'attempted': list(todo[:answer.get('tried', n)]),
                'remaining': list(todo[answer.get('tried', n):]),
                'elapsed_s': 1.0, 'stopped': answer.get('stop')}
    monkeypatch.setattr('trading.data_refresh.fetch_fundamentals_budgeted', fetch)
    monkeypatch.setenv('SB_FUNDAMENTALS_BUDGET_S', budget)
    monkeypatch.setattr(db, 'save_fundamentals_rows', lambda r: len(r))
    scheduler._monthly_fundamentals_refresh()
    return store.get('data_refresh', {}), calls


def test_the_names_are_fetched_in_batches_not_all_at_once(monkeypatch):
    cfg = state.cfg()
    old = cfg.custom_universe
    cfg.custom_universe = tuple(f"ZB{i:03d}.NS" for i in range(120))
    try:
        led, calls = _run(monkeypatch, [])
        assert len(calls) >= 3, [len(c) for c in calls]
        assert all(len(c) <= scheduler.FUNDAMENTALS_BATCH for c in calls)
        assert led.get('fundamentals_month') == date.today().isoformat()[:7]
    finally:
        cfg.custom_universe = old


def test_progress_is_written_after_every_batch_so_a_restart_resumes(monkeypatch):
    """THE FAULT: the ledger used to be written once, after everything."""
    cfg = state.cfg()
    old = cfg.custom_universe
    cfg.custom_universe = tuple(f"ZB{i:03d}.NS" for i in range(120))
    try:
        store, saved = _ledger_store(monkeypatch)
        writes = []
        real_save = db.save_settings

        def spy(doc, doc_id='default'):
            if scheduler.FUNDAMENTALS_DONE_KEY in doc:
                writes.append(len(doc[scheduler.FUNDAMENTALS_DONE_KEY]['symbols']))
            return real_save(doc, doc_id=doc_id)
        monkeypatch.setattr(db, 'save_settings', spy)

        def fetch(todo, time_budget_s=None, **k):
            got = list(todo)
            return {'rows': [{'symbol': s, 'field': 'f', 'as_of': 'd', 'value': 1.}
                             for s in got],
                    'n_ok': len(got), 'attempted': got, 'remaining': [],
                    'elapsed_s': 1.0, 'stopped': None}
        monkeypatch.setattr('trading.data_refresh.fetch_fundamentals_budgeted', fetch)
        monkeypatch.setenv('SB_FUNDAMENTALS_BUDGET_S', '600')
        monkeypatch.setattr(db, 'save_fundamentals_rows', lambda r: len(r))
        scheduler._monthly_fundamentals_refresh()
        # one ledger write per batch, each one carrying MORE names than the last
        assert len(writes) >= 3, writes
        assert writes == sorted(writes) and writes[0] < writes[-1], writes
    finally:
        cfg.custom_universe = old


def test_a_run_that_dies_mid_save_still_leaves_the_earlier_batches_recorded(monkeypatch):
    """The exact NEW-02 scenario: the process ends inside a later save."""
    cfg = state.cfg()
    old = cfg.custom_universe
    cfg.custom_universe = tuple(f"ZB{i:03d}.NS" for i in range(120))
    try:
        store, saved = _ledger_store(monkeypatch)
        calls = {'n': 0}

        def fetch(todo, time_budget_s=None, **k):
            got = list(todo)
            return {'rows': [{'symbol': s, 'field': 'f', 'as_of': 'd', 'value': 1.}
                             for s in got],
                    'n_ok': len(got), 'attempted': got, 'remaining': [],
                    'elapsed_s': 1.0, 'stopped': None}

        def dying_save(r):
            calls['n'] += 1
            if calls['n'] >= 3:
                raise RuntimeError('process ended inside the save')
            return len(r)
        monkeypatch.setattr('trading.data_refresh.fetch_fundamentals_budgeted', fetch)
        monkeypatch.setenv('SB_FUNDAMENTALS_BUDGET_S', '600')
        monkeypatch.setattr(db, 'save_fundamentals_rows', dying_save)
        scheduler._monthly_fundamentals_refresh()
        led = store.get('data_refresh', {})
        recorded = led.get(scheduler.FUNDAMENTALS_DONE_KEY, {}).get('symbols') or []
        # the first two batches survived the crash, and the month is NOT stamped
        assert len(recorded) >= scheduler.FUNDAMENTALS_BATCH, len(recorded)
        assert 'fundamentals_month' not in led
        assert led.get(scheduler.FUNDAMENTALS_LEFT_KEY, 0) > 0
        # so the block comes back tomorrow and resumes from where it stopped
        due, why = scheduler.weekly_block_due()
        assert due and 'fundamentals' in why
    finally:
        cfg.custom_universe = old


def test_a_run_yahoo_refuses_entirely_still_records_no_progress(monkeypatch):
    """Unchanged behaviour: nothing came back, so nothing counts as attempted."""
    cfg = state.cfg()
    old = cfg.custom_universe
    cfg.custom_universe = tuple(f"ZB{i:03d}.NS" for i in range(60))
    try:
        store, saved = _ledger_store(monkeypatch)

        def nothing(todo, time_budget_s=None, **k):
            return {'rows': [], 'n_ok': 0, 'attempted': list(todo),
                    'remaining': [], 'elapsed_s': 1.0, 'stopped': None}
        monkeypatch.setattr('trading.data_refresh.fetch_fundamentals_budgeted', nothing)
        monkeypatch.setenv('SB_FUNDAMENTALS_BUDGET_S', '600')
        p = state.portfolio()
        n0 = len(p.decision_journal)
        scheduler._monthly_fundamentals_refresh()
        led = store.get('data_refresh', {})
        assert scheduler.FUNDAMENTALS_DONE_KEY not in led
        assert led.get(scheduler.FUNDAMENTALS_LEFT_KEY) == 0
        assert 'fundamentals_month' not in led
        assert [e for e in _own(p, n0)
                if e.get('action') == 'DATA_REFRESH_FUNDAMENTALS_FAILED']
    finally:
        cfg.custom_universe = old
