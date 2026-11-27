"""NEW-03: the database calls that run while the engine holds a lock give up.

MongoClient is built with serverSelectionTimeoutMS and connectTimeoutMS only.
Those bound FINDING a server and OPENING a socket; pymongo computes
socket_timeout None, so a server that accepted a request and then stopped
answering was waited on for ever. The exit pass saves the book while holding
both the mode lock and the book lock, so one stalled request left every stop
and every target unchecked until the process restarted — and the scheduler
logged "rotation running - its own exit pass covers this slot" while it did.

OWNER DECISION 2026-11-27: bound the book-save and scan paths, not the whole
client, so the heavy unlocked reads cannot pause buying on a merely slow day.
"""
import threading
import time
from types import SimpleNamespace

import pytest

from web import db, scheduler


# --------------------------------------------------------------- the bound

def test_the_client_itself_is_deliberately_left_unbounded():
    """Option B, in the owner's words: not the whole client.

    If this ever becomes socketTimeoutMS on the client, that is option A and
    needs the owner's word, because it also bounds the statistics pass and the
    journal export.
    """
    import inspect
    src = inspect.getsource(db._try_connect)
    assert 'serverSelectionTimeoutMS' in src and 'connectTimeoutMS' in src
    assert 'socketTimeoutMS' not in src, "that is option A — ask the owner first"


def test_every_locked_path_call_is_inside_the_bound():
    """A new database call on a locked path must not be able to slip in."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(db.__file__).read_text())
    want = {
        'closed_history_policy': 'find_one',
        'save_portfolio': 'replace_one',
        'archive_portfolio_snapshot': 'replace_one',
        'archive_trades': 'bulk_write',
        'archive_journal': 'append',
        'history_docs': 'find',
    }
    for name, op in want.items():
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == name), None)
        assert fn is not None, name
        inside = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.With) and any(
                    isinstance(i.context_expr, ast.Call)
                    and getattr(i.context_expr.func, 'id', '') == 'bounded'
                    for i in node.items):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        inside.add(getattr(sub.func, 'attr', '') or getattr(sub.func, 'id', ''))
        assert op in inside, f"{name}: {op}(...) is not inside bounded(...)"


def test_the_bound_is_wide_enough_for_the_slowest_real_call():
    """The 13.04 s statistics figure is ~25-30 round trips, not one call."""
    assert db.LOCKED_PATH_TIMEOUT_S >= 10.0


def test_the_driver_really_has_a_deadline_during_a_book_save(monkeypatch):
    """Proof the bound is in force, read from pymongo's own state.

    The driver enforces this deadline inside its network layer, which no fake
    collection can exercise — so what is pinned here is that the deadline IS
    SET while the call runs, and absent outside it. Without the bounded(...)
    wrapper this reads None, which is the pre-repair world.
    """
    import pymongo._csot as csot
    seen = {}

    class Coll:
        def replace_one(self, where, doc, **kwargs):
            seen['timeout'] = csot.get_timeout()
            seen['deadline'] = csot.get_deadline()
            return SimpleNamespace(acknowledged=True)

    monkeypatch.setattr(db, '_CONNECTED', True)
    monkeypatch.setattr(db, '_DB', SimpleNamespace(portfolio=Coll()))
    monkeypatch.setattr(db, 'closed_history_policy', lambda: {})
    assert csot.get_timeout() is None
    assert db.save_portfolio({'cash': 1.0}) is True
    assert seen['timeout'] == db.LOCKED_PATH_TIMEOUT_S, seen
    assert seen['deadline'] not in (None, float('inf')), seen
    assert csot.get_timeout() is None, "the bound must not leak past the call"


@pytest.mark.parametrize('call', ['history_docs', 'archive_journal', 'archive_trades'])
def test_the_other_locked_paths_carry_the_same_deadline(monkeypatch, call):
    import pymongo._csot as csot
    seen = {}

    def spy(*a, **k):
        seen['timeout'] = csot.get_timeout()
        return [] if call == 'history_docs' else True

    class Coll:
        find = staticmethod(spy)
        bulk_write = staticmethod(spy)
        insert_many = staticmethod(spy)
    monkeypatch.setattr(db, '_CONNECTED', True)
    monkeypatch.setattr(db, '_DB', SimpleNamespace(ohlcv=Coll(), trades=Coll(),
                                                   journal_archive=Coll()))
    monkeypatch.setattr(db, 'closed_history_policy', lambda: {})
    if call == 'history_docs':
        db.history_docs(['TCS.NS'], rows=10)
    elif call == 'archive_trades':
        db.archive_trades([{'ticker': 'TCS.NS', 'entry_date': 'a', 'exit_date': 'b',
                            'qty': 1, 'reason': 'r'}])
    else:
        monkeypatch.setattr('web.journal_storage.append', spy)
        db.archive_journal([{'ts': 'x', 'action': 'Y'}])
    assert seen.get('timeout') == db.LOCKED_PATH_TIMEOUT_S, (call, seen)


def test_an_unlocked_heavy_read_keeps_its_old_behaviour():
    """Option B: the statistics pass and the journal export are NOT bounded."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(db.__file__).read_text())
    for name in ('storage_stats', '_cluster_sizes', 'list_journal_archive'):
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == name), None)
        if fn is None:
            continue
        assert not any(isinstance(n, ast.With) and any(
            isinstance(i.context_expr, ast.Call)
            and getattr(i.context_expr.func, 'id', '') == 'bounded'
            for i in n.items) for n in ast.walk(fn)), name


# --------------------------------------------------------------- the log

def test_a_skipped_exit_pass_says_so_when_no_rotation_holds_the_lock(monkeypatch, caplog):
    """The log line was the reason the freeze looked like nothing at all."""
    from trading.modes import ModeBusy
    monkeypatch.setattr('trading.modes.holding_mode_lock',
                        lambda **k: (_ for _ in ()).throw(ModeBusy('held')))
    monkeypatch.setattr(scheduler, '_check_pending_orders', lambda where: None)
    assert not scheduler._ROTATION_LOCK.locked()
    with caplog.at_level('INFO'):
        scheduler._session_refresh()
    text = caplog.text
    assert 'EXIT PASS SKIPPED' in text and 'NOT checked' in text
    assert 'its own exit pass covers this slot' not in text


def test_a_real_rotation_still_reports_the_slot_as_covered(monkeypatch, caplog):
    from trading.modes import ModeBusy
    monkeypatch.setattr('trading.modes.holding_mode_lock',
                        lambda **k: (_ for _ in ()).throw(ModeBusy('held')))
    monkeypatch.setattr(scheduler, '_check_pending_orders', lambda where: None)
    with scheduler._ROTATION_LOCK:
        with caplog.at_level('INFO'):
            scheduler._session_refresh()
    assert 'its own exit pass covers this slot' in caplog.text
    assert 'EXIT PASS SKIPPED' not in caplog.text
