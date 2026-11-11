"""SEQ-03 consumer/storage evidence, isolated books and no production writes."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import json

import pytest
from fastapi.testclient import TestClient
from trading import decision_evidence as de, quote_evidence as qe
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio
from web import auth, db, state
from web.main import app

NOW = datetime(2026, 11, 11, 5)


@pytest.fixture
def book(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path), mode='swing')
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    e = TradingEngine(cfg, p)
    monkeypatch.setattr(state, 'portfolio', lambda: p)
    monkeypatch.setattr(state, 'cfg', lambda: cfg)
    monkeypatch.setattr(state, 'engine', lambda: e)
    monkeypatch.setattr(auth, 'current_user', lambda r: {'role':'owner', 'email':'owner@test'})
    monkeypatch.setattr(db, 'is_connected', lambda: False)
    monkeypatch.setattr(db, 'list_journal_archive', lambda **k: [])
    monkeypatch.setattr(db, 'search_decision_archive', lambda **k: [])
    monkeypatch.setattr(db, 'read_decision', lambda key: None)
    monkeypatch.setattr(db, 'read_trade_evidence', lambda key: [])
    monkeypatch.setattr(db, 'read_trade_amendments', lambda key: [])
    monkeypatch.setattr(db, 'read_archived_trade_evidence', lambda key: [])
    e._open({'ticker':'AAA.NS', 'score':80., 'reasoning':'fixture'}, 10, 100., NOW)
    return e, TestClient(app)


def test_owner_can_render_and_export_exact_decision_without_recalculation(book):
    e, client = book
    d = de.new('AAA.NS', 'swing', 'scan', NOW)
    de.put(d, 'score', 47., 60., passed=False)
    de.put(d, 'spread', state='unavailable', reason='Provider did not supply spread')
    expected = de.journal_decision(e.portfolio, d, 'blocked', 'score')
    before = deepcopy(e.portfolio.decision_journal)
    e.cfg.min_score_to_enter = 99.
    page = client.get('/decisions/'+d['id'])
    assert page.status_code == 200
    assert '47' in page.text and '60' in page.text and 'data-gate-state="unavailable"' in page.text
    assert client.get('/decisions/'+d['id']+'?format=json').json() == expected
    assert d['id'] in client.get('/decisions').text
    assert e.portfolio.decision_journal == before


def test_trade_page_reunites_entry_partial_exits_and_amendments(book):
    e, client = book
    pos = e.portfolio.positions['AAA.NS']
    trade_id = pos.trade_evidence['trade_id']
    before = de.protection(pos)
    pos.stop_price = 90.
    de.amendment(pos, before, 'OWNER_LEVELS', NOW, portfolio=e.portfolio)
    e._exit_half('AAA.NS', 105., 'DANGER_EXIT_HALF', NOW)
    e._close('AAA.NS', 110., 'OWNER_SELL', NOW)
    body = client.get('/trade-evidence/'+trade_id+'?format=json').json()
    assert len(body['exits']) == 2 and len(body['amendments']) == 1
    assert body['entry']['fill']['price'] == 100.
    assert client.get('/trade-evidence/'+trade_id).status_code == 200
    assert 'OWNER_LEVELS' in client.get('/trade-evidence/'+trade_id).text


@pytest.mark.parametrize('role', [None, 'viewer'])
def test_evidence_is_owner_only(book, monkeypatch, role):
    _, client = book
    monkeypatch.setattr(auth, 'current_user', lambda r: {'role':role} if role else None)
    for path in ['/decisions', '/decisions/'+'a'*32, '/trade-evidence/'+'a'*32]:
        assert client.get(path, follow_redirects=False).status_code in (303, 403)


def test_legacy_trade_and_missing_decision_show_unknown(book):
    e, client = book
    pos = e.portfolio.positions['AAA.NS']
    pos.trade_evidence = {}
    e._close('AAA.NS', 100., 'OWNER_SELL', NOW)
    trade_id = e.portfolio.trades[-1].trade_evidence['trade_id']
    assert 'Original entry evidence was not recorded' in client.get('/trade-evidence/'+trade_id).text
    assert client.get('/decisions/'+'a'*32).status_code == 404
    assert client.get('/decisions/not-an-id').status_code == 400


def test_archived_and_local_spilled_decisions_are_readable(book, monkeypatch):
    e, client = book
    d = de.finish(de.new('ARCHIVE.NS','swing','scan',NOW), 'blocked', 'fixture')
    monkeypatch.setattr(db, 'read_decision', lambda key: deepcopy(d) if key == d['id'] else None)
    assert client.get('/decisions/'+d['id']+'?format=json').json() == d
    monkeypatch.setattr(db, 'read_decision', lambda key: None)
    from trading.paper import spill_journal
    assert spill_journal(e.portfolio, [{'decision_id':d['id'], 'evidence':d}])
    assert client.get('/decisions/'+d['id']+'?format=json').json() == d


@pytest.mark.parametrize('acknowledged', [True, False])
def test_evidence_archive_preserves_ledger_numbers_and_retains_unacknowledged_data(book, monkeypatch, acknowledged):
    e, _ = book
    e._close('AAA.NS', 105., 'OWNER_SELL', NOW)
    before = e.portfolio.trades[-1].to_dict()
    captured = []
    monkeypatch.setattr('trading.paper.TRADES_BYTES_CAP', 1)
    monkeypatch.setattr(db, 'is_connected', lambda: True)
    monkeypatch.setattr(db, 'archive_trade_evidence', lambda ev: captured.append(deepcopy(ev)) or acknowledged)
    monkeypatch.setattr(db, 'save_portfolio', lambda doc: True)
    assert e.portfolio.save()
    assert len(e.portfolio.trades) == 1
    after = e.portfolio.trades[0].to_dict()
    assert {k:v for k,v in before.items() if k != 'trade_evidence'} == {k:v for k,v in after.items() if k != 'trade_evidence'}
    assert captured == [before['trade_evidence']]
    if acknowledged:
        assert after['trade_evidence']['archived']
    else:
        assert after == before


@pytest.mark.parametrize('archive_ok', [False, True])
def test_journal_byte_cap_waits_for_durable_archive(book, monkeypatch, archive_ok):
    e, _ = book
    p = e.portfolio
    p.decision_journal = [{'ts':str(i), 'payload':'x'*1000} for i in range(10)]
    before = deepcopy(p.decision_journal)
    monkeypatch.setattr('trading.paper.JOURNAL_BYTES_CAP', 2500)
    captured = []
    monkeypatch.setattr(db, 'archive_journal', lambda rows: captured.extend(deepcopy(rows)) or archive_ok)
    monkeypatch.setattr(state, '_spill_journal_overflow', lambda *a: False)
    monkeypatch.setattr('trading.paper.spill_journal', lambda *a: False)
    p.save()
    if archive_ok:
        assert captured + p.decision_journal == before
        assert len(json.dumps(p.decision_journal).encode()) <= 2500
    else:
        assert p.decision_journal == before


def test_mongo_round_trip_retains_trade_evidence(book):
    e, _ = book
    e._exit_half('AAA.NS', 105., 'DANGER_EXIT_HALF', NOW)
    doc = {'cash': e.portfolio.cash, 'positions':{t:p.to_dict() for t,p in e.portfolio.positions.items()},
           'trades':[t.to_dict() for t in e.portfolio.trades]}
    fresh = Portfolio.fresh(e.cfg)
    state._hydrate_portfolio_from_mongo(fresh, doc)
    assert fresh.positions['AAA.NS'].trade_evidence == e.portfolio.positions['AAA.NS'].trade_evidence
    assert fresh.trades[0].trade_evidence == e.portfolio.trades[0].trade_evidence


def test_cached_quote_keeps_its_own_source_when_another_fetch_changes_global(book, monkeypatch):
    from trading import quote_router as qr
    monkeypatch.setattr(state, '_MARKS_CACHE', {'prices':{}})
    batch = qe.QuoteBatch({'AAA.NS':101.}, evidence={'AAA.NS':qe.make(101., 'upstox')})
    monkeypatch.setattr(qr, 'fetch_quotes', lambda *a, **k: batch)
    first, live = state.marks(['AAA.NS'])
    assert live
    monkeypatch.setitem(qr.LAST_SOURCES, 'AAA.NS', 'yf_close')
    second, live = state.marks(['AAA.NS'])
    assert second == first and live
    assert second.evidence['AAA.NS']['source'] == 'upstox'
    assert state.marks_sources(['AAA.NS']) == {'AAA.NS':'upstox'}


def test_delayed_quote_is_displayable_but_not_labelled_live(book, monkeypatch):
    from trading import quote_router as qr
    monkeypatch.setattr(state, '_MARKS_CACHE', {'prices':{}})
    batch = qe.QuoteBatch({'AAA.NS':101.}, evidence={'AAA.NS':qe.make(101., 'upstox_1min_delayed')})
    monkeypatch.setattr(qr, 'fetch_quotes', lambda *a, **k: batch)
    prices, live = state.marks(['AAA.NS'])
    assert prices['AAA.NS'] == 101. and not live


def test_newer_saved_broker_login_wins_old_browser_session(book, monkeypatch):
    from web.routes import upstox
    request = SimpleNamespace(session={'upstox_token':'old-fixture', 'upstox_connected_at':'2026-11-10T05:00:00'})
    monkeypatch.setattr(db, 'load_upstox_token', lambda: {'access_token':'new-fixture', 'connected_at':'2026-11-11T05:00:00'})
    monkeypatch.setattr(upstox.UpstoxClient, 'from_env', lambda: SimpleNamespace())
    client = upstox._client_from_session(request)
    assert client.access_token == 'new-fixture'
    assert request.session['upstox_connected_at'] == '2026-11-11T05:00:00'


@pytest.mark.parametrize('hours,connected,reason', [(-1.,False,'token_expired'), (None,False,'expiry_unknown'), (1.,True,'token_current')])
def test_status_never_calls_expired_or_unknown_token_connected(book, monkeypatch, hours, connected, reason):
    from web.routes import upstox
    fake = SimpleNamespace(is_connected=lambda:True, hours_until_expiry=lambda:hours,
        user_id='fixture', user_name='Fixture', connected_at='2026-11-11T05:00:00')
    monkeypatch.setattr(upstox.UpstoxClient, 'from_env', lambda: fake)
    monkeypatch.setattr(upstox, '_client_from_session', lambda request: fake)
    response = book[1].get('/upstox/status')
    assert response.json()['connected'] is connected
    assert response.json()['reason'] == reason


@pytest.mark.parametrize("source", ["upstox_1min_delayed", "yf_close"])
def test_blank_owner_sell_does_not_claim_fallback_is_live(book, monkeypatch, source):
    e, client = book
    batch = qe.QuoteBatch({'AAA.NS':101.}, evidence={'AAA.NS':qe.make(101., source)})
    monkeypatch.setattr(state, 'marks', lambda tickers: (batch, False))
    before = e.portfolio.cash
    reply = client.post('/positions/AAA.NS/sell', data={'price':''}, follow_redirects=False)
    assert reply.status_code == 303 and 'need_price' in reply.headers['location']
    assert 'AAA.NS' in e.portfolio.positions and e.portfolio.cash == before
    assert not e.portfolio.trades


def test_explicit_owner_price_retains_its_source_in_exit(book):
    e, client = book
    reply = client.post('/positions/AAA.NS/sell', data={'price':'103.5'}, follow_redirects=False)
    assert reply.status_code == 303
    ev = e.portfolio.trades[-1].trade_evidence['exit']
    assert ev['quote']['source'] == 'owner_typed'
    assert ev['quote']['provider_at'] is None
    assert ev['gates']['owner']['state'] == 'passed'


def test_byte_archived_trade_is_still_readable_beside_a_live_partial(book, monkeypatch):
    e, client = book
    pos = e.portfolio.positions['AAA.NS']
    trade_id = pos.trade_evidence['trade_id']
    e._exit_half('AAA.NS', 105., 'DANGER_EXIT_HALF', NOW)
    archived = deepcopy(e.portfolio.trades[0].trade_evidence)
    e.portfolio.trades[0].trade_evidence = {'trade_id':trade_id, 'archived':True, 'exit_id':archived['exit']['id']}
    monkeypatch.setattr(db, 'read_trade_evidence', lambda key: [archived] if key == trade_id else [])
    body = client.get('/trade-evidence/'+trade_id+'?format=json').json()
    assert len(body['exits']) == 1 and body['exits'][0]['fill']['qty'] == 5
    assert body['entry'] == pos.trade_evidence['entry']


@pytest.mark.parametrize("ack", [True, False])
def test_journal_archive_requires_write_acknowledgement(monkeypatch, ack):
    fake = SimpleNamespace(journal_archive=SimpleNamespace(insert_many=lambda *a, **k: SimpleNamespace(acknowledged=ack)))
    monkeypatch.setattr(db, '_DB', fake)
    monkeypatch.setattr(db, '_CONNECTED', True)
    assert db.archive_journal([{'ts':'fixture'}]) is ack



def test_each_sale_from_recent_decisions_has_a_working_detail_link(book):
    e, client = book
    e._exit_half('AAA.NS', 105., 'DANGER_EXIT_HALF', NOW)
    e._close('AAA.NS', 106., 'OWNER_SELL', NOW)
    for trade in e.portfolio.trades:
        d = trade.trade_evidence['exit']
        assert client.get('/decisions/'+d['id']+'?format=json').json() == d



def test_symbol_filter_searches_archived_matches_before_recent_limit(book, monkeypatch):
    d = de.finish(de.new('OLD.NS', 'intraday', 'scan', NOW), 'blocked', 'fixture')
    calls = []
    monkeypatch.setattr(db, 'search_decision_archive', lambda **k: calls.append(k) or [{'evidence':d}])
    page = book[1].get('/decisions?ticker=OLD&mode=intraday')
    assert page.status_code == 200 and d['id'] in page.text
    assert calls == [{'ticker':'OLD', 'mode':'intraday', 'limit':500}]
