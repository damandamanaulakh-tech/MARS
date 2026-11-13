from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from trading import fees
from trading.config import Config
from trading.paper import Portfolio, ClosedTrade
from web import cost_comparison as cc, db, state, auth, trade_audit
from web.main import app


def trade(**kw):
    d=dict(ticker='AAA.NS',qty=500,entry_date='2026-11-11T05:00:00',exit_date='2026-11-12T05:00:00',
           entry_price=100.,exit_price=101.,gross_pnl=500.,costs=130.,net_pnl=370.,mode='swing',
           reason='PARTIAL',sector='IT',entry_score=80.,reasoning='fixture')
    d.update(kw); return d


def test_comparison_groups_partial_buy_and_daily_dp_without_writes():
    rows=[trade(),trade(exit_date='2026-11-12T05:10:00')]; saved=deepcopy(rows)
    result=cc.compare(rows)
    a,b=result['rows']
    assert sum(r['details']['entry_allocated'] for r in (a,b))==142.34
    assert [r['details']['sell']['components']['dp'] for r in (a,b)]==[20.,0.]
    assert all(r['status']=='historical_estimate' and 'inferred' in r['note'] for r in (a,b))
    assert a['modeled_net']==500-a['modeled_costs']
    assert rows==saved
    assert result==cc.compare(rows)  # Stable comparison IDs, no invented execution identity.
    assert a['details']['entry_allocations'][0]['order_id'].startswith('comparison:')


@pytest.mark.parametrize('change', [{'mode':''},{'ticker':'ABC.BO'},{'ticker':'NIFTYBEES.NS'},
    {'entry_date':'2024-01-01T05:00:00'},{'ca_applied':['SPLIT']},{'qty':0},{'entry_price':float('nan')},
    {'exit_date':'2025-11-12T05:00:00'}])
def test_unknown_rows_remain_visible_and_not_zero(change):
    result=cc.compare([trade(**change)])
    assert result['summary']['unavailable']==1 and result['summary']['modeled']==0
    assert result['rows'][0]['modeled_costs'] is None
    assert result['rows'][0]['note']


def test_saved_original_quantity_includes_current_remaining_position():
    d=trade(trade_evidence={'trade_id':'one','entry':{'fill':{'qty':1000}}})
    p={**d,'qty':500}
    r=cc.compare([d],[p])['rows'][0]
    assert r['details']['entry_allocated']==71.17
    assert 'Saved original entry quantity' in r['note']


def test_archive_unavailable_is_explicit(monkeypatch):
    monkeypatch.setattr(db,'_CONNECTED',False)
    r=db.cost_comparison_archive()
    assert r['archive_status']=='unavailable' and r['archive_total'] is None


def test_archive_failure_and_cap_are_reported(monkeypatch):
    class Cursor:
        def sort(self,*a): return self
        def limit(self,*a): return self
        def max_time_ms(self,*a): return self
        def __iter__(self): return iter([trade()])
    class Collection:
        def count_documents(self,*a,**k): return 2
        def find(self, query, projection):
            assert projection['trade_evidence.entry.fill.qty']==1
            assert 'trade_evidence' not in projection  # Large gate snapshots excluded.
            assert projection['cost_evidence']==1
            return Cursor()
    monkeypatch.setattr(db,'_CONNECTED',True)
    monkeypatch.setattr(db,'_DB',SimpleNamespace(trades=Collection()))
    assert db.cost_comparison_archive(1)['archive_status']=='partial'
    monkeypatch.setattr(Collection,'find',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('offline')))
    assert db.cost_comparison_archive()['archive_status']=='unavailable'


def test_owner_page_json_csv_and_no_ledger_mutation(tmp_path,monkeypatch):
    p=Portfolio.fresh(Config(state_dir=str(tmp_path))); p.trades=[ClosedTrade.from_dict(trade())]
    saved=deepcopy(p.trades[0].to_dict()); cash=p.cash
    monkeypatch.setattr(state,'portfolio',lambda:p)
    monkeypatch.setattr(db,'cost_comparison_archive',lambda:{'rows':[trade()], 'archive_status':'complete','archive_total':1,'archive_loaded':1})
    monkeypatch.setattr(auth,'current_user',lambda r:{'role':'owner','name':'Owner'})
    monkeypatch.setattr(p,'save',lambda:pytest.fail('Comparison cannot save'))
    client=TestClient(app)
    for fmt in ('html','json','csv'):
        r=client.get('/trades/cost-comparison?format='+fmt)
        assert r.status_code==200
        assert 'counterfactual' in r.text
    assert client.get('/trades/cost-comparison?format=json').json()['summary']['rows']==1
    assert p.trades[0].to_dict()==saved and p.cash==cash
    monkeypatch.setattr(auth,'current_user',lambda r:{'role':'viewer'})
    assert client.get('/trades/cost-comparison').status_code==403
    assert client.post('/trades/cost-comparison').status_code==405


def test_price_audit_preserves_versioned_fee_snapshot(tmp_path):
    d=trade(cost_evidence={'model':fees.MODEL,'total':130.})
    t=ClosedTrade.from_dict(d)
    r=trade_audit.audit_trade(t,lambda *a:[],lambda *a:[],[],cfg=Config(state_dir=str(tmp_path)))
    assert r['corrected']['costs']==130. and r['corrected_force']['costs']==130.
    assert r['corrected']['net']==r['corrected']['gross']-130.
    assert r['cost_evidence']==d['cost_evidence']


def test_new_saved_fee_comparison_reuses_buy_allocation():
    sell=fees.charge(500,101.,'SELL','delivery','2026-11-12','AAA.NS',dp=True)
    ev={'model':fees.MODEL,'status':'paper_estimate','entry_allocated':71.17,'sell':sell}
    a=trade(cost_evidence=ev); b=trade(cost_evidence=ev,exit_date='2026-11-12T05:10:00')
    result=cc.compare([a,b]); rows=result['rows']
    assert sum(r['details']['entry_allocated'] for r in rows)==142.34
    assert [r['details']['sell']['components']['dp'] for r in rows]==[20.,0.]


def test_nonfinite_stored_amount_stays_unknown_in_json():
    import json
    r=cc.compare([trade(costs=float('nan'))])
    assert r['rows'][0]['stored_costs'] is None and r['rows'][0]['modeled_costs'] is None
    json.dumps(r,allow_nan=False)


def test_explicit_price_correction_changes_price_but_preserves_new_fees(tmp_path):
    p=Portfolio.fresh(Config(state_dir=str(tmp_path)))
    t=ClosedTrade.from_dict(trade(entry_price=200.,cost_evidence={'model':fees.MODEL,'total':130.}))
    p.trades=[t]
    daily=[{'date':'2026-11-11','open':100.,'high':101.,'low':99.,'close':100.},
           {'date':'2026-11-12','open':101.,'high':102.,'low':100.,'close':101.}]
    r=trade_audit.audit_trade(t,lambda *a:daily,lambda *a:[],[],cfg=p.cfg)
    before=p.cash
    result=trade_audit.apply_corrections(p,{'rows':[r]},[r['id']],cfg=p.cfg,force=True)
    assert result['done'] and t.costs==130.
    assert t.cost_evidence=={'model':fees.MODEL,'total':130.}
    assert t.net_pnl==t.gross_pnl-130.
    assert p.cash-before==pytest.approx(t.net_pnl-370.,rel=0,abs=1e-7)
