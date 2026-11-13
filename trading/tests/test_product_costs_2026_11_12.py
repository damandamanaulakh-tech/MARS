"""SEQ-05: dated fees, cash identities and entry allocation across real exits."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio, PaperPosition

NOW = datetime(2026, 11, 12, 5, 0)


def engine(tmp_path, monkeypatch, mode='intraday'):
    class Clock(datetime):
        @classmethod
        def utcnow(cls): return NOW
    monkeypatch.setattr('trading.engine.datetime', Clock)
    cfg=Config(state_dir=str(tmp_path), mode=mode, slippage_bps=0)
    p=Portfolio.fresh(cfg); p.cash=1_000_000.
    e=TradingEngine(cfg,p)
    monkeypatch.setattr(e,'_target_for',lambda *a,**k:120.)
    monkeypatch.setattr(e,'_stop_for',lambda *a,**k:95.)
    return e


def open_position(e, qty=1000, when=NOW, mode=None):
    if mode: e.cfg.mode=mode
    e._open({'ticker':'AAA.NS','score':80.,'reasoning':'fee fixture'},qty,100.,when)
    return e.portfolio.positions['AAA.NS']


def test_buy_charges_are_booked_once_at_entry(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch)
    pos=open_position(e)
    assert e.portfolio.cash == pytest.approx(899969.66,rel=0,abs=1e-8)
    assert pos.cost_basis['orders'][0]['fee']['total'] == 30.34
    assert pos.cost_basis['orders'][0]['cash_paid'] is True


def test_intraday_roundtrip_and_cash_use_the_held_product(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch)
    open_position(e)
    e.cfg.mode='nifty200_momentum'
    e._close('AAA.NS',101.,'OWNER_SELL',NOW+timedelta(minutes=10))
    t=e.portfolio.trades[-1]
    assert t.costs == pytest.approx(82.97,rel=0,abs=1e-8)
    assert t.net_pnl == pytest.approx(917.03,rel=0,abs=1e-8)
    assert e.portfolio.cash == pytest.approx(1_000_917.03,rel=0,abs=1e-8)
    assert t.cost_evidence['sell']['components']['dp'] == 0


def test_delivery_partial_exits_charge_dp_once_per_day(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch,'swing')
    open_position(e,when=NOW-timedelta(days=1))
    e._exit_half('AAA.NS',101.,'DANGER_HALF_EXIT',NOW)
    e._close('AAA.NS',101.,'OWNER_SELL',NOW+timedelta(minutes=10))
    a,b=e.portfolio.trades
    assert a.cost_evidence['sell']['components']['dp'] == 20.
    assert b.cost_evidence['sell']['components']['dp'] == 0.
    assert a.cost_evidence['entry_allocated']+b.cost_evidence['entry_allocated'] == pytest.approx(142.34,rel=0,abs=1e-8)
    assert e.portfolio.cash == pytest.approx(1_000_000+a.net_pnl+b.net_pnl,rel=0,abs=1e-8)


def test_fee_inclusive_cash_refusal_changes_nothing(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch); e.portfolio.cash=100000.
    before=(e.portfolio.cash,deepcopy(e.portfolio.positions),deepcopy(e.portfolio.decision_journal))
    with pytest.raises(ValueError,match='costs_cash_short'):
        open_position(e)
    assert (e.portfolio.cash,e.portfolio.positions,e.portfolio.decision_journal)==before


def test_nse_march_revision_does_not_double_count_ipft():
    from trading.fees import charge
    old=charge(100000,100.,'BUY','intraday',datetime(2026,2,27),'AAA.NS')
    new=charge(100000,100.,'BUY','intraday',datetime(2026,3,2),'AAA.NS')
    assert old['components']['exchange'] == 297.
    assert old['components']['ipft'] == 10.
    assert new['components']['exchange'] == 306.99
    assert new['components']['ipft'] == .01
    assert old['total'] == new['total']


@pytest.mark.parametrize('qty,price,side,product,brokerage,stt,stamp,dp',[
    (1000,100.,'BUY','intraday',20.,0.,3.,0.),
    (1000,100.,'SELL','intraday',20.,25.,0.,0.),
    (1000,100.,'BUY','delivery',20.,100.,15.,0.),
    (1000,100.,'SELL','delivery',20.,100.,0.,20.),
    (1,100.,'BUY','delivery',2.50,.10,.02,0.),
    (1,100.,'BUY','intraday',.10,0.,0.,0.),
])
def test_basic_order_caps_and_side_taxes(qty,price,side,product,brokerage,stt,stamp,dp):
    from trading.fees import charge
    f=charge(qty,price,side,product,NOW,'AAA.NS',dp=True)
    c=f['components']
    assert (c['brokerage'],c['stt'],c['stamp'],c['dp'])==(brokerage,stt,stamp,dp)
    assert f['total'] == pytest.approx(sum(c.values()),abs=.001,rel=0)


@pytest.mark.parametrize('qty,price',[(0,100),(-1,100),(1.5,100),(1,float('nan')),(1,float('inf')),(1,0)])
def test_invalid_fee_input_cannot_become_a_zero_charge(qty,price):
    from trading.fees import charge
    with pytest.raises(ValueError,match='costs_invalid'):
        charge(qty,price,'BUY','intraday',NOW,'AAA.NS')


def test_partial_buy_allocation_survives_json_and_mongo_reload(tmp_path,monkeypatch):
    from web.state import _hydrate_portfolio_from_mongo
    e=engine(tmp_path,monkeypatch,'swing')
    open_position(e,qty=1001,when=NOW-timedelta(days=1))
    e._exit_half('AAA.NS',101.,'DANGER_HALF_EXIT',NOW)
    p=e.portfolio; p.save()
    reloaded=Portfolio.load_or_fresh(e.cfg)
    assert reloaded.cost_dp_days==p.cost_dp_days
    assert reloaded.positions['AAA.NS'].cost_basis==p.positions['AAA.NS'].cost_basis
    assert reloaded.trades[0].cost_evidence==p.trades[0].cost_evidence
    mirrored=Portfolio.fresh(e.cfg)
    _hydrate_portfolio_from_mongo(mirrored,{'cash':reloaded.cash,
        'positions':{k:v.to_dict() for k,v in reloaded.positions.items()},
        'trades':[t.to_dict() for t in reloaded.trades], 'cost_dp_days':reloaded.cost_dp_days})
    e.portfolio=mirrored
    e._close('AAA.NS',101.,'OWNER_SELL',NOW+timedelta(minutes=20))
    assert mirrored.trades[-1].cost_evidence['sell']['components']['dp']==0
    assert mirrored.cash==pytest.approx(1_000_000+sum(t.net_pnl for t in mirrored.trades),abs=1e-8,rel=0)


def test_same_day_delivery_exit_rebates_buy_tax_without_rewriting_entry(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch,'swing'); pos=open_position(e)
    entry_evidence=deepcopy(pos.trade_evidence['entry'])
    assert e.portfolio.cash==pytest.approx(899857.66,abs=1e-8,rel=0)
    e._close('AAA.NS',101.,'OWNER_SELL',NOW+timedelta(minutes=20))
    t=e.portfolio.trades[-1]
    assert t.costs==pytest.approx(82.97,abs=.001,rel=0)
    assert t.cost_evidence['entry_refund']==112.
    assert t.trade_evidence['entry']==entry_evidence
    assert e.portfolio.cash==pytest.approx(1_000_917.03,abs=1e-8,rel=0)


def test_partial_delivery_same_day_then_next_day_allocates_each_leg(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch,'swing'); open_position(e)
    e._exit_half('AAA.NS',101.,'OWNER_SELL',NOW)
    e._close('AAA.NS',101.,'OWNER_SELL',NOW+timedelta(days=1))
    a,b=e.portfolio.trades
    assert a.cost_evidence['sell']['product']=='intraday'
    assert a.cost_evidence['entry_refund']==56.
    assert b.cost_evidence['sell']['components']['dp']==20.
    assert b.cost_evidence['entry_refund']==0
    assert e.portfolio.cash==pytest.approx(1_000_000+a.net_pnl+b.net_pnl,abs=1e-8,rel=0)


def test_dp_is_recharged_for_a_new_day_but_not_same_day_reentry(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch,'swing')
    for exit_day in (NOW,NOW+timedelta(hours=1),NOW+timedelta(days=1)):
        open_position(e,when=NOW-timedelta(days=3))
        e._close('AAA.NS',101.,'OWNER_SELL',exit_day)
    assert [t.cost_evidence['sell']['components']['dp'] for t in e.portfolio.trades]==[20.,0.,20.]


def test_legacy_transition_preserves_past_ledger_and_only_defers_remaining_buy(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch)
    pos=PaperPosition('AAA.NS',500,100.,NOW,'IT',100.,80.,'old',mode='intraday')
    pos.trade_evidence={'trade_id':'old-entry','entry':{'fill':{'qty':1000}},'amendments':[]}
    e.portfolio.positions[pos.ticker]=pos; e.portfolio.cash-=50000
    before=deepcopy(e.portfolio.trades)
    e._close(pos.ticker,101.,'OWNER_SELL',NOW)
    t=e.portfolio.trades[-1]
    assert e.portfolio.trades[:-1]==before
    assert t.cost_evidence['status']=='legacy_entry_estimate'
    assert t.cost_evidence['deferred_entry_charge']==15.17
    assert t.cost_evidence['entry_refund']==0
    assert e.portfolio.cash==pytest.approx(1_000_000+t.net_pnl,abs=1e-8,rel=0)


def test_overnight_intraday_product_is_explicitly_unverified(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch)
    pos=open_position(e); pos.entry_date=NOW-timedelta(days=1)
    pos.cost_basis['orders'][0]['date_ist']='2026-11-11'
    e._close(pos.ticker,101.,'RECOVERY',NOW)
    ev=e.portfolio.trades[-1].cost_evidence
    assert ev['status']=='overnight_intraday_estimate'
    assert 'conversion' in ev['note']


def test_split_preserves_total_entry_fees_and_does_not_double_charge(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch,'swing'); pos=open_position(e,when=NOW-timedelta(days=1))
    pos.qty*=2; pos.entry_price/=2
    e._exit_half(pos.ticker,51.,'OWNER_SELL',NOW)
    e._close(pos.ticker,51.,'OWNER_SELL',NOW)
    assert sum(t.cost_evidence['entry_allocated'] for t in e.portfolio.trades)==142.34
    assert e.portfolio.cash==pytest.approx(1_000_000+sum(t.net_pnl for t in e.portfolio.trades),abs=1e-8,rel=0)


def test_fees_and_slippage_are_separate_not_charged_twice(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch); e.cfg.slippage_bps=5
    e._open({'ticker':'AAA.NS','score':80.,'reasoning':'slip'},1000,100.05,NOW)
    e._close('AAA.NS',101.,'OWNER_SELL',NOW)
    t=e.portfolio.trades[-1]
    assert t.entry_price==100.05 and t.exit_price==pytest.approx(100.9495)
    assert e.portfolio.cash==pytest.approx(1_000_000+t.net_pnl,abs=1e-8,rel=0)


@pytest.mark.parametrize('ticker,sector', [('ABC.BO','IT'),('AAPL.US','IT'),('^NSEI','Index'),('SBINEQWETF.NS','ETF'),('NIFTYBEES.NS','ETF'),('FUND.NS','ETF')])
def test_unsupported_asset_is_refused_before_entry_cash_changes(tmp_path,monkeypatch,ticker,sector):
    e=engine(tmp_path,monkeypatch,'swing'); e.cfg.sector_map={ticker:sector}
    before=e.portfolio.cash
    with pytest.raises(ValueError,match='costs_.*schedule_unavailable'):
        e._open({'ticker':ticker,'score':80},10,100.,NOW)
    assert e.portfolio.cash==before and not e.portfolio.positions


def test_unsupported_legacy_asset_can_still_protectively_exit(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch,'swing')
    p=PaperPosition('ABC.BO',10,100.,NOW,'IT',100.,80.,'legacy',mode='swing')
    e.portfolio.positions[p.ticker]=p; e.portfolio.cash-=1000
    e._close(p.ticker,99.,'STOP',NOW)
    assert not e.portfolio.positions
    t=e.portfolio.trades[-1]
    assert t.cost_evidence['status']=='unavailable'
    assert t.cost_evidence['reason']=='costs_exchange_schedule_unavailable'
    assert e.portfolio.cash==pytest.approx(1_000_000+t.net_pnl,rel=0,abs=1e-8)


def test_missing_future_schedule_never_debits_saved_entry_twice(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch); p=open_position(e)
    monkeypatch.setattr('trading.fees.charge',lambda *a,**k: (_ for _ in ()).throw(ValueError('costs_schedule_unavailable')))
    e._exit_half(p.ticker,101.,'STOP',NOW)
    e._close(p.ticker,101.,'STOP',NOW)
    assert e.portfolio.cash==pytest.approx(1_000_000+sum(t.net_pnl for t in e.portfolio.trades),rel=0,abs=1e-8)
    assert sum(t.cost_evidence['previously_paid_entry_allocation'] for t in e.portfolio.trades)==30.34


def test_buy_order_caps_and_fifo_allocation_survive_pyramid(tmp_path,monkeypatch):
    from trading import fees
    from trading.pyramid import apply_pyramid
    e=engine(tmp_path,monkeypatch,'swing'); p=open_position(e,when=NOW-timedelta(days=1))
    basis=fees.add_basis(p,500,102.,NOW)
    extra=basis['orders'][-1]['fee']['total']
    e.portfolio.cash-=51000+extra; p.cost_basis=basis
    apply_pyramid(p,500,102.)
    e._exit_half(p.ticker,104.,'PARTIAL',NOW)
    e._close(p.ticker,104.,'STOP',NOW+timedelta(days=1))
    assert sum(t.cost_evidence['entry_allocated'] for t in e.portfolio.trades)==pytest.approx(142.34+extra,rel=0,abs=1e-8)
    assert e.portfolio.cash==pytest.approx(1_000_000+sum(t.net_pnl for t in e.portfolio.trades),rel=0,abs=1e-8)
    assert len(e.portfolio.trades[-1].cost_evidence['entry_allocations'])==2


def test_dp_grouping_uses_ist_calendar_not_utc_day():
    from trading.fees import day
    assert day(datetime(2026,11,11,18,31))==day(datetime(2026,11,12,5))
    assert day(datetime(2026,11,11,18,29))!=day(datetime(2026,11,12,5))


def test_backtest_new_model_books_costs_and_legacy_is_explicit(tmp_path,monkeypatch):
    from trading.backtest import _close_position
    from trading.costs import trade_costs
    e=engine(tmp_path,monkeypatch,'swing'); open_position(e,when=NOW-timedelta(days=1))
    _close_position(e.portfolio,'AAA.NS',101.,'TARGET',NOW.date())
    assert e.portfolio.trades[-1].cost_evidence['model'].startswith('upstox-basic')
    assert e.portfolio.cash==pytest.approx(1_000_000+e.portfolio.trades[-1].net_pnl,rel=0,abs=1e-8)
    e=engine(tmp_path,monkeypatch,'swing'); e.cfg.backtest_cost_model='legacy_delivery'
    p=PaperPosition('AAA.NS',1000,100.,NOW,'IT',100.,80.,'old',mode='swing')
    e.portfolio.positions[p.ticker]=p; e.portfolio.cash-=100000
    _close_position(e.portfolio,p.ticker,101.,'TARGET',NOW.date())
    assert e.portfolio.trades[-1].costs==trade_costs(1000,100.,101.,e.cfg)
    assert e.portfolio.trades[-1].cost_evidence['model']=='legacy_delivery'


def test_backtest_unverified_date_refused_before_fetch(tmp_path,monkeypatch):
    from trading.backtest import run_backtest
    cfg=Config(state_dir=str(tmp_path))
    with pytest.raises(ValueError,match='costs_date_before_verified'):
        run_backtest(cfg,'2024-01-01','2024-12-31',preloaded_history={})


def test_risk_affordability_includes_entry_fee(tmp_path):
    from trading.risk import can_open, RiskState
    from trading.fees import entry_total
    cfg=Config(state_dir=str(tmp_path), mode='swing',cash_reserve_pct=0,position_size_value=100000,position_slab_value=0,
               min_position_value=0,max_sector_exposure_pct=None,max_portfolio_heat_pct=None)
    ok,why,qty=can_open(RiskState(cfg,100000),100000,100000,{},'AAA.NS',100.,cfg,when=NOW)
    assert ok and qty==998 and entry_total(qty,100.,'swing',NOW,'AAA.NS')<=100000
    assert entry_total(qty+1,100.,'swing',NOW,'AAA.NS')>100000


def test_split_then_pyramid_does_not_rescale_the_new_buy_order(tmp_path,monkeypatch):
    from trading import fees
    from trading.pyramid import apply_pyramid
    e=engine(tmp_path,monkeypatch,'swing'); p=open_position(e,when=NOW-timedelta(days=1))
    p.qty*=2; p.entry_price/=2
    basis=fees.add_basis(p,500,51.,NOW); extra=basis['orders'][-1]['fee']['total']
    assert [o['remaining_qty'] for o in basis['orders']]==[2000,500]
    e.portfolio.cash-=25500+extra; p.cost_basis=basis; apply_pyramid(p,500,51.)
    e._exit_half(p.ticker,52.,'PARTIAL',NOW)
    a=e.portfolio.trades[-1]
    assert a.cost_evidence['entry_allocated']==88.96
    assert a.cost_evidence['sell']['delivery_qty']==1250
    e._close(p.ticker,52.,'STOP',NOW)
    assert e.portfolio.trades[-1].cost_evidence['sell']['delivery_qty']==750
    assert e.portfolio.cash==pytest.approx(1_000_000+sum(t.net_pnl for t in e.portfolio.trades),rel=0,abs=1e-8)


def test_actual_mongo_mirror_persists_cost_allocation_and_dp(tmp_path,monkeypatch):
    from web import state,db
    state._patch_portfolio_save_to_mirror_mongo()
    stored={}
    monkeypatch.setattr(db,'is_connected',lambda:True)
    monkeypatch.setattr(db,'save_portfolio',lambda doc:stored.update(deepcopy(doc)) or True)
    e=engine(tmp_path,monkeypatch,'swing'); open_position(e,when=NOW-timedelta(days=1))
    e._exit_half('AAA.NS',101.,'PARTIAL',NOW)
    assert e.portfolio.save() is True
    restored=Portfolio.fresh(e.cfg); state._hydrate_portfolio_from_mongo(restored,stored)
    assert restored.cost_dp_days==e.portfolio.cost_dp_days
    assert restored.positions['AAA.NS'].cost_basis==e.portfolio.positions['AAA.NS'].cost_basis
    assert restored.trades[-1].cost_evidence==e.portfolio.trades[-1].cost_evidence


def test_backtest_export_retains_fee_model_and_components(tmp_path,monkeypatch):
    import pandas as pd
    from trading.backtest import _compile_result
    from trading import fees
    e=engine(tmp_path,monkeypatch,'swing'); e.cfg.starting_capital=1_000_000
    open_position(e,when=NOW-timedelta(days=1)); e._close('AAA.NS',101.,'STOP',NOW)
    result=_compile_result(e.cfg,'2026-11-11','2026-11-12',1,'^NSEI',e.portfolio,
        [('2026-11-11',1_000_000),('2026-11-12',e.portfolio.cash)],pd.DataFrame({'Close':[100.,101.]}, index=pd.date_range('2026-11-11',periods=2)),1,2,[],e.portfolio.cash)
    row=result.to_dict()['trades'][0]
    assert row['cost_evidence']==e.portfolio.trades[0].cost_evidence
    assert row['gross_pnl']-row['costs']==row['net_pnl']
    assert result.cfg_snapshot['fee_model_version']==fees.MODEL
