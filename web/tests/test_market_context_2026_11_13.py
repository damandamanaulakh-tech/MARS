from copy import deepcopy
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from trading import decision_evidence as de, market_context as mc, sector_identity as si
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio
from trading.tests.test_market_context_2026_11_13 import NOW, daily, identity, source_snapshot
from web import auth, db, state, sector_context
from web.main import app


@pytest.fixture
def book(tmp_path,monkeypatch):
    cfg=Config(state_dir=str(tmp_path));p=Portfolio.fresh(cfg);e=TradingEngine(cfg,p)
    monkeypatch.setattr(state,'portfolio',lambda:p)
    monkeypatch.setattr(state,'cfg',lambda:cfg)
    monkeypatch.setattr(state,'engine',lambda:e)
    monkeypatch.setattr(auth,'current_user',lambda r:{'role':'owner','email':'fixture'})
    monkeypatch.setattr(db,'read_decision',lambda k:None)
    monkeypatch.setattr(db,'is_connected',lambda:False)
    return e,TestClient(app)


def saved_context(p):
    d=de.new('TCS.NS','intraday','scan',NOW)
    d['market_context']=mc.compare('TCS.NS',daily(),None,daily(),identity(),NOW,lookback=2)
    de.journal_decision(p,d,'blocked','fixture')
    return d


def test_owner_table_and_download_show_same_saved_window_and_unknowns_without_fetch(book,monkeypatch):
    e,client=book;d=saved_context(e.portfolio)
    before=deepcopy(e.portfolio.decision_journal)
    def forbidden(*a,**k):raise AssertionError('read page fetched or traded')
    monkeypatch.setattr(e,'scan',forbidden)
    monkeypatch.setattr('trading.intraday_data.fetch',forbidden)
    monkeypatch.setattr(si,'refresh',forbidden)
    html=client.get('/market-context')
    assert html.status_code==200 and 'TCS.NS' in html.text
    assert 'Unknown' in html.text and '2026-11-12T10:00:00+00:00' in html.text
    assert 'data-context-status="partial"' in html.text
    data=client.get('/market-context?format=json').json()
    assert data['rows'][0]['market_context']==d['market_context']
    assert 'Stock minus sector: Unknown' in client.get('/decisions/'+d['id']).text
    assert e.portfolio.decision_journal==before


@pytest.mark.parametrize('role',[None,'viewer'])
def test_comparison_is_owner_only(book,monkeypatch,role):
    _,client=book
    monkeypatch.setattr(auth,'current_user',lambda r:{'role':role} if role else None)
    assert client.get('/market-context?format=json',follow_redirects=False).status_code in (303,403)


def test_context_survives_buy_partial_final_exit_json_and_mongo_restart(book,monkeypatch):
    e,_=book;d=saved_context(e.portfolio)
    d['ticker']='AAA.NS'
    e._open({'ticker':'AAA.NS','score':80.,'decision':d,'reasoning':'fixture'},10,100.,NOW.replace(tzinfo=None))
    p=e.portfolio
    original=deepcopy(p.positions['AAA.NS'].trade_evidence['entry']['market_context'])
    # A new registry cannot rewrite a saved decision.
    monkeypatch.setattr(si,'snapshot',lambda:{})
    stored={}
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(db,'is_connected',lambda:True)
    monkeypatch.setattr(db,'save_portfolio',lambda doc:stored.update(deepcopy(doc)) or True)
    assert p.save()
    for restored in (Portfolio.load_or_fresh(e.cfg),Portfolio.fresh(e.cfg)):
        if not restored.positions:state._hydrate_portfolio_from_mongo(restored,stored)
        assert restored.positions['AAA.NS'].trade_evidence['entry']['market_context']==original
    e._exit_half('AAA.NS',105.,'OWNER_SELL',NOW.replace(tzinfo=None))
    e._close('AAA.NS',110.,'OWNER_SELL',NOW.replace(tzinfo=None))
    for trade in p.trades:
        assert trade.trade_evidence['entry']['market_context']==original
        assert trade.trade_evidence['exit']['market_context']['status']=='unknown'


@pytest.mark.parametrize('failure',['offline','read','write'])
def test_feed_failed_store_never_claims_observed_or_overwrites_prior(monkeypatch,failure):
    previous=source_snapshot();before=deepcopy(previous);writes=[]
    monkeypatch.setattr(db,'is_connected',lambda:failure!='offline')
    def load():
        if failure=='read':raise RuntimeError('unreadable')
        return previous
    monkeypatch.setattr(db,'load_sector_identity',load)
    monkeypatch.setattr(si,'refresh',lambda *a,**k:previous)
    monkeypatch.setattr(db,'save_sector_identity',lambda doc:writes.append(doc) or False)
    out=sector_context.refresh(NOW)
    assert out['status'] in ('store_unavailable','failed_retained','write_unacknowledged')
    assert previous==before
    assert len(writes)==(1 if failure=='write' else 0)


def test_snapshot_store_acknowledgement_and_validation(monkeypatch):
    stored={};ack=[True]
    class Settings:
        def replace_one(self,key,doc,upsert):stored.update(deepcopy(doc));return SimpleNamespace(acknowledged=ack[0])
        def find_one(self,key):return deepcopy(stored) or None
    monkeypatch.setattr(db,'_CONNECTED',True)
    monkeypatch.setattr(db,'_DB',SimpleNamespace(settings=Settings()))
    snap=source_snapshot()
    assert db.save_sector_identity(snap) is True
    assert db.load_sector_identity()==snap
    ack[0]=False
    assert db.save_sector_identity(snap) is False
    stored['schema']=0
    with pytest.raises(ValueError):db.load_sector_identity()


def test_sector_store_reader_keeps_duplicate_conflicts_unknown(monkeypatch):
    from trading import context_inputs as ci
    rows=[{'date':str(d.date()),'sector':'IT','close':v} for d,v in daily()['Close'].items()]
    rows.append({**rows[-1],'close':999})
    monkeypatch.setattr(ci,'_sector_reader',lambda since:rows)
    out=ci.daily_sectors(NOW)
    c=mc.compare('TCS.NS',daily(),out['IT'],daily(),identity(),NOW,lookback=2)
    assert c['sector']['return_pct'] is None
    assert c['sector']['rejected']['conflicting_duplicates']==1


def test_sector_intraday_loader_passes_verified_keys_and_explicit_budget(monkeypatch):
    from trading import context_inputs as ci
    from trading.data_refresh import SECTOR_INDEX_KEYS
    from trading.tests.test_completed_intraday_2026_11_12 import bar_frame
    monkeypatch.setattr(ci,'identities',lambda *a,**k:{'TCS.NS':identity()})
    seen=[]
    def fetch(tickers,cfg,client,now,**kwargs):seen.append((tickers,kwargs));return {'IT':bar_frame(now=NOW)},{}
    monkeypatch.setattr(ci.intraday_data,'fetch',fetch)
    data=ci.prepare(['TCS.NS'],Config(mode='intraday'),NOW,client=object())
    assert data['interval']=='5m'
    assert seen==[(['IT'],{'instrument_keys':{'IT':SECTOR_INDEX_KEYS['IT']},'cap':1,'budget_seconds':20})]


def test_production_scan_saves_context_on_actual_decision_path(tmp_path,monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    from trading import context_inputs as ci
    from trading.tests.test_completed_intraday_2026_11_12 import freeze_engine
    freeze_engine(monkeypatch,NOW)
    e=_scan_engine(tmp_path,monkeypatch)
    marker={'schema':1,'role':'context_only','status':'unknown','reason':'fixture_missing_sector'}
    calls=[]
    monkeypatch.setattr(ci,'prepare',lambda *a,**k:{})
    def observe(inputs,ticker,stock,market,cfg,now):calls.append(ticker);return deepcopy(marker)
    monkeypatch.setattr(ci,'observation',observe)
    e.scan()
    assert 'UPP.NS' in calls
    rows=[r['evidence'] for r in e.portfolio.decision_journal if r.get('action')=='SCAN_DECISION']
    assert any(r['ticker']=='UPP.NS' and r['market_context']==marker for r in rows)


def test_actual_refresh_stores_context_without_changing_stop_exit(tmp_path,monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _held_engine
    from trading import context_inputs as ci
    from trading.tests.test_completed_intraday_2026_11_12 import freeze_engine
    freeze_engine(monkeypatch,NOW)
    e,_=_held_engine(tmp_path,monkeypatch)
    e.portfolio.positions['AAA.NS'].stop_price=110
    marker={'schema':1,'role':'context_only','status':'unknown','reason':'offline'}
    monkeypatch.setattr(ci,'prepare',lambda *a,**k:{})
    monkeypatch.setattr(ci,'observation',lambda *a,**k:deepcopy(marker))
    e.refresh()
    trade=next(t for t in e.portfolio.trades if t.ticker=='AAA.NS')
    assert trade.reason=='STOP_LOSS' and trade.trade_evidence['exit']['market_context']==marker

@pytest.mark.parametrize('code,message,reason', [
    (8000, 'you are over your space quota, using 513 MB of 512 MB', 'store_quota_exceeded'),
    (13, 'not authorized mongodb+srv://user:secret@host', 'store_permission_denied'),
    (8000, 'unrelated Atlas failure secret', 'store_write_failed'),
])
def test_sector_write_error_is_classified_without_leaking_server_text(monkeypatch, code, message, reason):
    from pymongo.errors import OperationFailure
    class Settings:
        def replace_one(self, *args, **kwargs): raise OperationFailure(message, code)
    monkeypatch.setattr(db, '_CONNECTED', True)
    monkeypatch.setattr(db, '_DB', SimpleNamespace(settings=Settings()))
    assert db.save_sector_identity(source_snapshot()) is False
    diagnostic = db.sector_identity_write_status()
    assert diagnostic['reason'] == reason and diagnostic['code'] == code
    assert diagnostic['status'] == 'failed' and diagnostic['at']
    assert message not in str(diagnostic) and 'secret' not in str(diagnostic)


def test_sector_write_recovery_requires_ack_and_keeps_portfolio_failure(monkeypatch):
    ack = [False]
    class Settings:
        def replace_one(self, *a, **kw): return SimpleNamespace(acknowledged=ack[0])
    monkeypatch.setattr(db, '_CONNECTED', True)
    monkeypatch.setattr(db, '_DB', SimpleNamespace(settings=Settings()))
    monkeypatch.setattr(db, 'LAST_WRITE_ERROR', 'separate portfolio failure')
    assert db.save_sector_identity(source_snapshot()) is False
    assert db.sector_identity_write_status()['reason'] == 'write_unacknowledged'
    ack[0] = True
    assert db.save_sector_identity(source_snapshot()) is True
    assert db.sector_identity_write_status()['status'] == 'acknowledged'
    assert db.sector_identity_write_status()['reason'] is None
    assert db.LAST_WRITE_ERROR == 'separate portfolio failure'


def test_quota_failure_visible_on_owner_export_and_health_without_any_io(book, monkeypatch):
    from pymongo.errors import OperationFailure
    previous = source_snapshot(); before = deepcopy(previous)
    class Settings:
        def replace_one(self, *a, **kw): raise OperationFailure('over your space quota', 8000)
    monkeypatch.setattr(db, '_CONNECTED', True)
    monkeypatch.setattr(db, '_DB', SimpleNamespace(settings=Settings()))
    monkeypatch.setattr(db, 'is_connected', lambda: True)
    monkeypatch.setattr(db, 'load_sector_identity', lambda: previous)
    monkeypatch.setattr(si, 'refresh', lambda *a, **kw: deepcopy(previous))
    result = sector_context.refresh(NOW)
    assert result['status'] == 'write_unacknowledged'
    assert result['write']['reason'] == 'store_quota_exceeded'
    assert previous == before
    def forbidden(*a, **kw): raise AssertionError('health or owner view performed IO')
    monkeypatch.setattr(si, 'refresh', forbidden)
    monkeypatch.setattr(db, 'storage_stats', forbidden)
    monkeypatch.setattr(si, 'snapshot', lambda: previous)
    _, client = book
    body = client.get('/market-context?format=json').json()
    assert body['refresh']['last_attempt'] == result
    assert body['refresh']['scope'] == 'current_process'
    html = client.get('/market-context').text
    assert 'not saved' in html and 'store_quota_exceeded' in html
    health = client.get('/healthz').json()
    assert health['sector_context']['last_attempt'] == result
    assert 'secret' not in str(health['sector_context'])


def test_process_restart_does_not_claim_source_refresh_success(monkeypatch):
    monkeypatch.setattr(sector_context, '_last_result', None)
    out = sector_context.status()
    assert out['last_attempt'] is None and out['scope'] == 'current_process'
    assert out['durability'] == 'unknown'


def test_fresh_seed_is_not_mistaken_for_durable_snapshot(tmp_path, monkeypatch):
    import json
    seed = source_snapshot()
    seed['attempts'] = {name: {'status': 'observed', 'at': NOW.isoformat()} for name in si.FILES}
    path = tmp_path / 'seed.json'; path.write_text(json.dumps(seed))
    monkeypatch.setattr(si, 'SEED', path)
    monkeypatch.setattr(db, 'is_connected', lambda: True)
    monkeypatch.setattr(db, 'load_sector_identity', lambda: None)
    monkeypatch.setattr(si, 'refresh', lambda *a, **kw: deepcopy(seed))
    saved = []
    monkeypatch.setattr(db, 'save_sector_identity', lambda doc: saved.append(doc) or True)
    assert sector_context.refresh(NOW)['status'] == 'stored'
    assert saved == [seed]
