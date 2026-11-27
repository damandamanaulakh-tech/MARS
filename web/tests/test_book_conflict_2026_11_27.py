"""XR-070: a restart that finds two books keeps the newer one and files both.

OWNER DECISION 2026-11-27, from an options card: "Keep the newer one, file the
other". Before this, a conflict refused every save for the life of the process.
Nothing was overwritten — the old journal line was true — but the book the
session was actually trading on never reached the database, died at the next
restart, and buying stayed blocked the whole time.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio
from web import db, state

NOW = datetime(2026, 11, 27, 5)


class Store:
    """The two collections the healer touches, each with its own answers."""

    def __init__(self):
        self.book = None
        self.archive = {}
        self.ack = True
        self.archive_error = None

    # portfolio collection
    def replace_one(self, where, doc, **kwargs):
        if self.ack is True:
            self.book = deepcopy(doc)
        return SimpleNamespace(acknowledged=self.ack)


class Archive:
    def __init__(self, parent):
        self.parent = parent

    def replace_one(self, where, doc, **kwargs):
        if self.parent.archive_error:
            raise self.parent.archive_error
        if self.parent.ack is not True:
            return SimpleNamespace(acknowledged=False)
        self.parent.archive[where['_id']] = deepcopy(doc)
        return SimpleNamespace(acknowledged=True)


def position(ticker, price=100.0):
    return PaperPosition(ticker=ticker, qty=10, entry_price=price, entry_date=NOW,
                         sector='Test', peak_price=price, entry_score=80.,
                         reasoning='isolated conflict fixture')


@pytest.fixture
def store(monkeypatch):
    s = Store()
    monkeypatch.setenv('MONGO_URL', 'mongodb://isolated.invalid')
    monkeypatch.setattr(db, '_CONNECTED', True)
    monkeypatch.setattr(db, '_DB', SimpleNamespace(portfolio=s, portfolio_archive=Archive(s)))
    monkeypatch.setattr(db, 'LAST_WRITE_ERROR', None)
    monkeypatch.setattr(db, 'LAST_WRITE_ERROR_AT', None)
    monkeypatch.setattr(db, 'LAST_PORTFOLIO_SAVE_AT', None, raising=False)
    monkeypatch.setattr(db, 'LAST_BOOK_CONFLICT', None, raising=False)
    monkeypatch.setattr(db, 'LAST_BOOK_CONFLICT_AT', None, raising=False)
    monkeypatch.setattr(db, '_BOOT_DEGRADED', True)
    monkeypatch.setattr(db, '_RECOVERED_PENDING', True)
    monkeypatch.setattr(db, 'closed_history_policy', lambda: {})
    monkeypatch.setattr(db, 'archive_journal', lambda rows: False)
    state._patch_portfolio_save_to_mirror_mongo()
    return s


def two_books(tmp_path, monkeypatch, store, *, session_newer=True):
    """A session trading on its own book, and an older/newer durable copy."""
    cfg = Config(state_dir=str(tmp_path), mode='swing', starting_capital=1_000_000)
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    e = TradingEngine(cfg, p)
    monkeypatch.setattr(state, 'portfolio', lambda: p)
    monkeypatch.setattr(state, 'engine', lambda: e)

    # the durable book: two positions, last action at a known time
    durable = Portfolio.fresh(cfg)
    durable.positions = {'OLD1.NS': position('OLD1.NS'), 'OLD2.NS': position('OLD2.NS')}
    durable.cash = 111_111.0
    stored_at = NOW - (timedelta(hours=6) if session_newer else timedelta(0))
    durable.decision_journal = [{'ts': stored_at.isoformat(), 'action': 'BUY', 'why': 'durable'}]
    persisted = durable.snapshot()

    # the running book: one position opened during the outage, newer or older
    p.positions = {'OUTAGE.NS': position('OUTAGE.NS', 250.0)}
    p.cash = 222_222.0
    local_at = NOW - (timedelta(0) if session_newer else timedelta(hours=6))
    p.decision_journal = [{'ts': local_at.isoformat(), 'action': 'BUY', 'why': 'outage session'}]
    p._durable_book_loaded = False

    monkeypatch.setattr(db, 'load_portfolio', lambda **kw: deepcopy(persisted))
    return p, persisted


def test_the_outage_book_is_kept_when_it_acted_last(tmp_path, monkeypatch, store):
    p, persisted = two_books(tmp_path, monkeypatch, store, session_newer=True)
    state.heal_after_reconnect()
    # the session's own book continues, and reaches the database
    assert set(p.positions) == {'OUTAGE.NS'}
    assert p.cash == 222_222.0
    assert set(store.book['positions']) == {'OUTAGE.NS'}
    # writes resume: the recovery is claimed and nothing blocks entries
    assert db.recovered_pending() is False
    assert db.LAST_WRITE_ERROR is None
    assert db.portfolio_entry_block_reason(reconnect=False) is None


def test_the_durable_book_is_kept_when_it_acted_last(tmp_path, monkeypatch, store):
    p, persisted = two_books(tmp_path, monkeypatch, store, session_newer=False)
    state.heal_after_reconnect()
    assert set(p.positions) == {'OLD1.NS', 'OLD2.NS'}
    assert p.cash == 111_111.0
    assert db.recovered_pending() is False


def test_both_books_are_filed_away_before_either_is_chosen(tmp_path, monkeypatch, store):
    p, persisted = two_books(tmp_path, monkeypatch, store, session_newer=True)
    state.heal_after_reconnect()
    labels = {doc['archive_label'] for doc in store.archive.values()}
    assert labels == {'conflict-database', 'conflict-session'}
    filed = {frozenset(doc['positions']) for doc in store.archive.values()}
    assert filed == {frozenset({'OLD1.NS', 'OLD2.NS'}), frozenset({'OUTAGE.NS'})}
    # the archive is verbatim: the cash of each book is the cash it had
    by_label = {doc['archive_label']: doc for doc in store.archive.values()}
    assert by_label['conflict-database']['cash'] == 111_111.0
    assert by_label['conflict-session']['cash'] == 222_222.0


def test_the_owner_is_told_in_the_journal_and_on_the_screen(tmp_path, monkeypatch, store):
    p, persisted = two_books(tmp_path, monkeypatch, store, session_newer=True)
    state.heal_after_reconnect()
    rows = [r for r in p.decision_journal if r.get('action') == 'MONGO_RECOVERED_CONFLICT_RESOLVED']
    assert len(rows) == 1
    row = rows[0]
    assert row['kept'] == 'session'
    assert row['session_positions'] == 1 and row['database_positions'] == 2
    assert row['database_archive_id'] in store.archive
    assert row['session_archive_id'] in store.archive
    assert db.status()['book_conflict'] and db.status()['book_conflict_at']
    assert row['database_archive_id'] in db.status()['book_conflict']


def test_a_book_that_cannot_be_filed_away_is_never_discarded(tmp_path, monkeypatch, store):
    """The old refuse-and-journal path still guards a failing archive."""
    p, persisted = two_books(tmp_path, monkeypatch, store, session_newer=False)
    store.archive_error = RuntimeError('space quota exceeded')
    state.heal_after_reconnect()
    assert set(p.positions) == {'OUTAGE.NS'}          # nothing hydrated over it
    assert store.book is None                          # and nothing written
    assert db.recovered_pending() is True
    assert db.LAST_WRITE_ERROR == 'recovery book conflict; reconciliation required'
    assert any(r.get('action') == 'MONGO_RECOVERED_CONFLICT' for r in p.decision_journal)


def test_books_with_no_readable_age_are_not_guessed_between(tmp_path, monkeypatch, store):
    p, persisted = two_books(tmp_path, monkeypatch, store, session_newer=True)
    persisted['decision_journal'] = [{'action': 'BUY', 'why': 'no stamp at all'}]
    monkeypatch.setattr(db, 'load_portfolio', lambda **kw: deepcopy(persisted))
    state.heal_after_reconnect()
    assert set(p.positions) == {'OUTAGE.NS'}
    assert store.book is None
    assert db.recovered_pending() is True


def test_an_empty_local_book_still_takes_the_durable_one(tmp_path, monkeypatch, store):
    """The virgin path is untouched — a conflict needs local activity."""
    p, persisted = two_books(tmp_path, monkeypatch, store, session_newer=True)
    p.positions, p.trades = {}, []
    state.heal_after_reconnect()
    assert set(p.positions) == {'OLD1.NS', 'OLD2.NS'}
    assert not store.archive              # no conflict, so nothing was filed
    assert db.recovered_pending() is False


def test_the_dashboard_shows_the_reconciliation_and_hides_it_otherwise(monkeypatch):
    """A journal line the owner never downloads is not a notice."""
    from fastapi.testclient import TestClient
    from web import auth
    from web.main import app
    monkeypatch.setattr(auth, 'current_user', lambda request: {'role': 'owner', 'username': 'owner'})
    client = TestClient(app)
    real_status = db.status

    monkeypatch.setattr(db, 'status', lambda: {**real_status(), 'book_conflict': None,
                                               'book_conflict_at': None})
    quiet = client.get('/')
    assert quiet.status_code == 200 and 'data-book-conflict' not in quiet.text

    note = 'Kept the running session copy (1 positions); filed the other away unchanged.'
    monkeypatch.setattr(db, 'status', lambda: {**real_status(), 'book_conflict': note,
                                               'book_conflict_at': '2026-11-27T05:00:00'})
    shown = client.get('/')
    assert shown.status_code == 200
    assert 'data-book-conflict' in shown.text and note in shown.text
