"""RUN-20261126-02 — the app must reconnect to its database by itself.

Live, 26 November: the Atlas cluster's plan was changed at about 18:35 IST.
A plan change moves the cluster to new hosts. The running app's database
client kept the old hosts and never found a server again — but the app only
ever recorded "connected" at connect time, so it kept saying connected, every
read and save timed out, nothing reconnected, and the dashboard went on
showing the earlier storage-quota message (stamped 18:34 IST) for hours.

These tests pin: a link failure drops the client; the next check builds a
new one; the in-memory book is then saved; a REFUSED operation (quota) is
not a link failure; and the message on screen is the current one.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pymongo.errors import (AutoReconnect, NetworkTimeout, OperationFailure,
                            ServerSelectionTimeoutError)

from web import db, scheduler, state
from web.tests.test_portfolio_save_guard_2026_11_20 import (  # noqa: F401
    BookStore, engine, storage)

QUOTA = "you are over your space quota, using 513 MB of 512 MB. Writes are blocked on your cluster."


class Client:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.mark.parametrize("exc", [ServerSelectionTimeoutError("No replica set members found"),
                                 NetworkTimeout("timed out"), AutoReconnect("connection closed")])
def test_a_link_failure_drops_the_client_so_a_fresh_one_can_be_built(storage, monkeypatch, exc):
    client = Client()
    monkeypatch.setattr(db, "_CLIENT", client)
    assert db.note_link_failure(exc, "test") is True
    assert db._CONNECTED is False and db._DB is None and db._CLIENT is None and client.closed
    assert "database unreachable" in db.LAST_WRITE_ERROR and type(exc).__name__ in db.LAST_WRITE_ERROR
    assert db.status()["connected"] is False and db.status()["degraded"] is True
    assert db.portfolio_entry_block_reason(reconnect=False) == "portfolio_save_unavailable"


@pytest.mark.parametrize("exc", [OperationFailure(QUOTA), RuntimeError("anything else"), ValueError("bad policy")])
def test_a_refused_operation_is_not_a_link_failure(storage, monkeypatch, exc):
    client = Client()
    monkeypatch.setattr(db, "_CLIENT", client)
    assert db.note_link_failure(exc, "test") is False
    assert db._CONNECTED is True and db._DB is not None and not client.closed


def test_a_quota_refusal_keeps_the_connection_and_its_own_message(engine, storage):
    storage.error = OperationFailure(QUOTA)
    assert engine.portfolio.save() is False
    assert db._CONNECTED is True
    assert "513 MB of 512 MB" in db.LAST_WRITE_ERROR


def _dead_link(monkeypatch):
    """Every read and write now fails the way a moved cluster fails."""
    def gone(*a, **k):
        raise ServerSelectionTimeoutError("No replica set members found yet")
    dead = SimpleNamespace(portfolio=SimpleNamespace(replace_one=gone),
                           settings=SimpleNamespace(find_one=gone))
    monkeypatch.setattr(db, "_DB", dead)
    monkeypatch.setattr(db, "_CLIENT", Client())


def test_the_stale_quota_message_is_replaced_by_what_is_failing_now(engine, storage, monkeypatch):
    storage.error = OperationFailure(QUOTA)
    assert engine.portfolio.save() is False and "513 MB" in db.LAST_WRITE_ERROR
    stamp = db.LAST_WRITE_ERROR_AT
    _dead_link(monkeypatch)
    # the real policy reader now runs against the dead link, as it did live
    monkeypatch.setattr(db, "closed_history_policy", REAL_POLICY)
    assert engine.portfolio.save() is False
    assert "513 MB" not in db.LAST_WRITE_ERROR and "database unreachable" in db.LAST_WRITE_ERROR
    assert db.LAST_WRITE_ERROR_AT != stamp and db._CONNECTED is False


REAL_POLICY = db.closed_history_policy


def test_after_the_cluster_moves_the_app_reconnects_and_saves_the_book_it_holds(
        engine, storage, monkeypatch):
    p = engine.portfolio
    saved_before = deepcopy(storage.doc)
    monkeypatch.setattr(db, "closed_history_policy", REAL_POLICY)
    _dead_link(monkeypatch)

    # the outage: the book keeps changing in memory, nothing can be saved
    p.cash -= 12_345.0
    assert p.save() is False
    assert db._CONNECTED is False and state.mongo_degraded() is True
    assert storage.doc == saved_before

    # the cluster is back on new hosts; a FRESH client can reach it
    attempts = []

    def fresh_connection():
        attempts.append(1)
        db._CLIENT = Client()
        db._DB = SimpleNamespace(portfolio=storage,
                                 settings=SimpleNamespace(find_one=lambda q: None))
        db._CONNECTED = True
    monkeypatch.setattr(db, "_try_connect", fresh_connection)
    monkeypatch.setattr(db, "_LAST_ATTEMPT", 0.0)

    scheduler._portfolio_save_retry()                 # the one-minute job, unchanged

    assert attempts == [1]
    assert db._CONNECTED is True and db.LAST_WRITE_ERROR is None
    assert state.mongo_degraded() is False and db.recovered_pending() is False
    assert storage.doc["cash"] == p.cash              # the book in MEMORY is what was saved
    assert p.save_error is None


def test_while_the_cluster_is_still_away_it_retries_once_a_minute_not_in_a_loop(engine, storage, monkeypatch):
    monkeypatch.setattr(db, "closed_history_policy", REAL_POLICY)
    _dead_link(monkeypatch)
    assert engine.portfolio.save() is False
    attempts = []
    monkeypatch.setattr(db, "_try_connect", lambda: attempts.append(1))      # still unreachable
    monkeypatch.setattr(db, "_LAST_ATTEMPT", 0.0)
    for _ in range(5):
        scheduler._portfolio_save_retry()
        assert db.is_connected() is False
    assert attempts == [1]                            # the 60-second cooldown holds
    assert db.LAST_WRITE_ERROR and state.mongo_degraded() is True


def test_the_health_thread_notices_a_dead_link_even_when_nothing_is_saving(storage, monkeypatch):
    def gone(*a, **k):
        raise ServerSelectionTimeoutError("No replica set members found yet")
    monkeypatch.setattr(db, "_DB", SimpleNamespace(command=gone))
    monkeypatch.setattr(db, "_CLIENT", Client())
    assert db.storage_stats() is None
    assert db._CONNECTED is False and "database unreachable" in db.LAST_WRITE_ERROR


def test_the_dashboard_says_unreachable_and_that_no_restart_is_needed(monkeypatch):
    import re
    from web.tests.test_dashboard_mars_2026_10_12 import _Book, _client
    from unittest.mock import patch
    status = {"connected": False, "db": None, "url_set": True, "degraded": True, "boot_degraded": False,
              "write_error": "database unreachable (ServerSelectionTimeoutError) — reconnecting with a fresh connection",
              "write_error_at": "2026-11-26T13:04:53.396884",
              "portfolio_save_confirmed_at": "2026-11-26T08:53:56.078377",
              "entry_block_reason": "portfolio_save_unavailable"}
    with _Book(), patch("web.db.status", return_value=status), \
            patch("web.db.is_connected", return_value=False), \
            patch.object(state, "marks", return_value=({}, False)):
        page = _client().get("/").text
    text = " ".join(re.sub(r"<[^>]+>", " ", page).split())
    assert 'data-db-unreachable="1"' in page and 'data-db-write-error="1"' not in page
    assert "Database CANNOT BE REACHED" in text and "no restart is needed" in text
    assert "recorded 2026-11-26 18:34 IST" in text
    assert "database unreachable (ServerSelectionTimeoutError)" in text
