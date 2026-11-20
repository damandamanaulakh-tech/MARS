"""Storage boundaries, exact cleanup selection and retry/readback invariants."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import json
import pytest
from bson import BSON

from trading import history_retention as retention
from trading.config import Config
from trading.paper import Portfolio, ClosedTrade, PaperPosition
from web import db, state, journal_storage as storage, journal_archive_codec as codec


def trade(**kw):
    return {'ticker':'AAA.NS', 'qty':2, 'entry_date':'2026-10-01T04:00:00',
            'exit_date':'2026-11-17T09:00:00', 'entry_price':100., 'exit_price':90.,
            'gross_pnl':-20.,'costs':2.,'net_pnl':-22.,'reason':'STOP_LOSS',
            'sector':'IT','entry_score':70.,'reasoning':'fixture','mode':'intraday',**kw}


@pytest.mark.parametrize('entry,exit,mode,expected',[
    ('2026-09-30T18:29:59','2026-11-19T04:00:00','swing','entry_through_2026_09_30'),
    ('2026-09-30T18:30:00','2026-11-17T09:00:00','swing',None),
    ('2026-10-01T04:00:00','2026-11-17T18:29:59','intraday','closed_intraday_through_2026_11_17'),
    ('2026-10-01T04:00:00','2026-11-17T18:30:00','intraday',None),
    ('2026-10-01T04:00:00','2026-11-19T04:00:00','intraday',None),
    ('2026-10-01T04:00:00','2026-11-17T09:00:00','',None),
    ('2026-10-01T04:00:00',None,'intraday','unknown_dates'),
    ('bad','2026-11-17T09:00:00','intraday','unknown_dates'),
    ('2026-10-01T04:00:00','2026-09-17T09:00:00','intraday','unknown_dates'),
    ('2026-10-01T00:00:00+05:30','2026-11-19T04:00:00Z','swing',None),
])
def test_cutoffs_use_entry_ist_and_closed_intraday_ist(entry,exit,mode,expected):
    assert retention.selection_reason(trade(entry_date=entry,exit_date=exit,mode=mode),
        entry_before=datetime(2026,10,1,tzinfo=retention.IST),
        intraday_closed_before=datetime(2026,11,18,tzinfo=retention.IST)) == expected


def test_exact_manifest_does_not_become_a_rolling_purge_or_touch_later_partial_exit(tmp_path):
    old=ClosedTrade.from_dict(trade()); later=ClosedTrade.from_dict(trade(exit_date='2026-11-18T04:00:00'))
    p=Portfolio.fresh(Config(state_dir=str(tmp_path)));p.cash=98765.;p.capital_base=123456.;p.dd_peak_equity=120000.
    p.positions['OPEN.NS']=PaperPosition.from_dict({'ticker':'OPEN.NS','qty':7,
        'entry_date':'2026-09-15T05:00:00','entry_price':100.,'peak_price':110.,
        'entry_score':70.,'sector':'IT','reasoning':'kept','mode':'intraday',
        'stop_price':95.,'target_price':120.})
    p.trades=[old,later];p.closed_history_policy={'version':1,'removed_fingerprints':[retention.fingerprint(old)]}
    before={k:deepcopy(getattr(p,k)) for k in ('cash','capital_base','dd_peak_equity','positions')}
    p.save();q=Portfolio.load_or_fresh(p.cfg)
    assert q.trades==[later]
    assert q.cash==before['cash'] and q.capital_base==before['capital_base'] and q.dd_peak_equity==before['dd_peak_equity']
    assert q.positions==before['positions']
    assert q.closed_history_policy==p.closed_history_policy


@pytest.mark.parametrize('bad',[{'version':2,'removed_fingerprints':[]},{'version':1,'removed_fingerprints':['*']},
                                {'version':1,'removed_fingerprints':'all'}])
def test_bad_policy_does_not_erase_book_or_issue_success(tmp_path,monkeypatch,bad):
    p=Portfolio.fresh(Config(state_dir=str(tmp_path)));p.cash=1234.;p.trades=[ClosedTrade.from_dict(trade())]
    p.closed_history_policy=bad;p.save()
    q=Portfolio.load_or_fresh(p.cfg)
    assert q.cash==1234. and len(q.trades)==1 and q.closed_history_policy==bad
    monkeypatch.setattr(db,'_CONNECTED',True)
    monkeypatch.setattr(db,'_DB',SimpleNamespace(settings=SimpleNamespace(find_one=lambda *a: {'policy':bad})))
    assert db.save_portfolio({'cash':1234.,'trades':[trade()]}) is False


def test_policy_refused_read_cannot_overwrite_live_history(monkeypatch):
    writes=[]
    def unavailable(*a):raise RuntimeError('offline')
    monkeypatch.setattr(db,'_CONNECTED',True)
    monkeypatch.setattr(db,'_DB',SimpleNamespace(settings=SimpleNamespace(find_one=unavailable),
        portfolio=SimpleNamespace(replace_one=lambda *a,**k:writes.append(a))))
    assert db.save_portfolio({'trades':[trade()]}) is False
    assert not writes


def event(i=0):
    return {'ts':f'2026-11-17T04:00:{i:02}', 'action':'SCAN_DECISION','decision_id':str(i),
            'evidence':{'ticker':'AAA.NS','mode':'intraday','gates':{'signal':{'value':'unchanged'*1000}}}}


class Collection:
    def __init__(self,ack=True,partial=False):self.rows={};self.ack=ack;self.partial=partial
    def bulk_write(self,ops,**kwargs):
        for op in ops[:1] if self.partial else ops:
            self.rows.setdefault(op._filter['_id'],deepcopy(op._doc['$setOnInsert']))
        return SimpleNamespace(acknowledged=self.ack)
    def find(self,query):return [v for k,v in self.rows.items() if k in query['_id']['$in']]


def test_journal_retry_preserves_exact_evidence_and_does_not_grow_collection():
    c=Collection();entries=[event(1),event(2)];before=deepcopy(entries)
    assert storage.append(c,entries) and len(c.rows)==2
    assert storage.append(c,[{k:e[k] for k in reversed(e)} for e in entries])
    assert len(c.rows)==2 and [{k:v for k,v in e.items() if k!='_id'} for e in entries]==before
    assert all(codec.MARKER in row for row in c.rows.values())
    assert [storage.decoded(v) for v in c.rows.values()]==before


def test_distinct_identical_events_are_preserved_and_spill_retry_keeps_ids():
    c=Collection();entries=[event(1),event(1)]
    assert storage.append(c,entries) and len(c.rows)==2
    replay=json.loads(json.dumps(entries))
    assert storage.append(c,replay) and len(c.rows)==2


def test_partial_acknowledgement_and_conflicting_identity_are_not_success():
    c=Collection(partial=True)
    entries=[event(1),event(2)]
    with pytest.raises(codec.ArchiveFormatError,match='readback'):storage.append(c,entries)
    c.partial=False;assert storage.append(c,entries) and len(c.rows)==2
    c=Collection(ack=False);assert storage.append(c,[event(1)]) is False
    c=Collection();a={**event(1),'_id':'same'};assert storage.append(c,[a])
    with pytest.raises(codec.ArchiveFormatError):storage.append(c,[{**a,'action':'changed'}])


def test_mixed_legacy_packed_corruption_and_query_fields():
    legacy={'_id':'legacy',**event(1)};packed=BSON(codec.pack_raw(BSON.encode({'_id':'packed',**event(2)}))).decode()
    assert storage.decoded(legacy)==event(1) and storage.decoded(packed)==event(2)
    damaged=deepcopy(packed);damaged[codec.MARKER]['sha256']='0'*64
    with pytest.raises(codec.ArchiveFormatError):storage.decoded(damaged)
    query=storage.decision_query('AAA.NS','intraday')
    assert query['$or'][0]['evidence.mode']=='intraday'
    assert query['$or'][1]['_mars_journal_query.mode']=='intraday'
    assert query['$or'][1]['_mars_journal_query.ticker']['$regex']==r'AAA\.NS'


def test_report_consumes_archive_lazily_and_keeps_each_row(tmp_path):
    from trading.reports import write_journal_csv
    import tracemalloc
    p=Portfolio.fresh(Config(state_dir=str(tmp_path)));seen=[]
    def rows():
        for i in range(1000):
            seen.append(i)
            yield {'ts':str(i),'action':'HOLD','evidence':{'snapshot':str(i).zfill(4)*5000}}
    tracemalloc.start()
    try:
        path=write_journal_csv(p,archived=rows())
        _,peak=tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4_000_000  # materializing the snapshots would exceed 20 MB
    assert len(seen)==1000 and len(open(path).readlines())==1001


@pytest.mark.parametrize('index_refused', [False, True])
def test_connect_prepares_bounded_export_without_losing_book_on_index_refusal(monkeypatch, index_refused):
    import pymongo
    calls=[]
    def create_index(keys, **kwargs):
        calls.append((keys, kwargs))
        if index_refused:
            raise RuntimeError('index temporarily unavailable')
        return kwargs['name']
    database=SimpleNamespace(journal_archive=SimpleNamespace(create_index=create_index))
    class Client:
        admin=SimpleNamespace(command=lambda *_: {'ok':1})
        def __getitem__(self, name):
            assert name=='sourceborn'
            return database
    monkeypatch.setenv('MONGO_URL','mongodb://isolated-fixture.invalid')
    monkeypatch.setenv('MONGO_DB','sourceborn')
    monkeypatch.setattr(pymongo,'MongoClient',lambda *a,**kw:Client())
    monkeypatch.setattr(db,'_CLIENT',None)
    monkeypatch.setattr(db,'_DB',None)
    monkeypatch.setattr(db,'_CONNECTED',False)
    db._try_connect()
    assert db._CONNECTED and db._DB is database
    assert calls==[([('ts',-1),('_id',-1)],{'name':'journal_recent_ts_id'})]


@pytest.mark.parametrize('name', ['decision_journal', 'trades'])
def test_history_download_remains_available_when_live_quotes_are_unavailable(monkeypatch, tmp_path, name):
    from fastapi.testclient import TestClient
    from web.main import app
    from web import auth
    p=Portfolio.fresh(Config(state_dir=str(tmp_path)))
    p.positions['AAA.NS']=SimpleNamespace(ticker='AAA.NS',entry_price=100.)
    p.trades=[ClosedTrade.from_dict(trade())]
    p.decision_journal=[{'ts':'2026-11-20T04:00:00','action':'HOLD'}]
    monkeypatch.setattr(state,'portfolio',lambda:p)
    monkeypatch.setattr(auth,'current_user',lambda request:{'role':'owner'})
    def unrelated(*args,**kwargs):
        raise AssertionError('History export called an unrelated quote provider or archive')
    monkeypatch.setattr(state,'marks',unrelated)
    monkeypatch.setattr(state,'marks_sources',unrelated)
    monkeypatch.setattr(db,'list_trades',(lambda:[]) if name=='trades' else unrelated)
    monkeypatch.setattr(db,'iter_journal_archive',
        (lambda:iter([{'ts':'2026-11-19T04:00:00','action':'SCAN_DECISION'}])) if name=='decision_journal' else unrelated)
    response=TestClient(app).get('/reports/download/'+name,follow_redirects=False)
    assert response.status_code==200 and 'text/csv' in response.headers['content-type']
    if name=='decision_journal':
        assert 'SCAN_DECISION' in response.text and 'HOLD' in response.text
    else:
        assert 'STOP_LOSS' in response.text and 'net_pnl' in response.text
