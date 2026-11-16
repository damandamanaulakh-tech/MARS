"""Goal10 exact initial-stop heat and measured net-loss regressions."""
from datetime import datetime
from types import SimpleNamespace as N
import math
import pytest
from trading import measured_kelly as mk
from trading.config import Config
from trading.risk import RiskState, can_open


def trade(net=-500, **kw):
    return N(**({'qty':100,'entry_price':100.,'net_pnl':net,'gross_pnl':None if net is None else net+30,
                'costs':30.,'reason':'STOP_LOSS','mode':'nifty200_momentum'}|kw))


def cfg():
    c=Config();c.cash_reserve_pct=0;c.category_budgets_enabled=False
    c.min_position_value=0;c.max_portfolio_heat_pct=.01
    return c


def gate(c, stop=85., **kw):
    return can_open(RiskState(c,1000000),1000000,1000000,{},'AAA.NS',100.,c,
                    proposed_stop_price=stop,when=datetime(2026,11,16),**kw)


def test_new_heat_uses_exact_15pct_stop():
    c=cfg()
    ok,why,q=gate(c,notional_override=100000)
    assert ok,why
    assert q==666 and q*15<=10000 and (q+1)*15>10000


def test_minimum_round_up_cannot_break_heat_even_if_first_qty_fits():
    c=cfg();c.max_portfolio_heat_pct=.022499;c.min_position_value=150000
    ok,why,q=gate(c,notional_override=150000)
    assert not ok and q==0 and why=='below_min_value_150000'


@pytest.mark.parametrize('stop',[float('nan'),float('inf'),0,-1,100,110])
def test_invalid_proposed_stop_cannot_open(stop):
    assert gate(cfg(),stop=stop)==(False,'invalid_proposed_stop',0)


def test_empty_is_unknown_but_all_net_losses_are_not_empty():
    empty=mk.measure([])
    assert empty.status=='NO_SAMPLE' and empty.fraction is None
    losses=mk.measure([trade() for _ in range(20)],category='nifty200_momentum')
    assert losses.status=='NO_POSITIVE_EDGE' and losses.block_new_entries
    assert losses.sample_state=='ALL_LOSS' and losses.fraction==0
    assert losses.payoff_ratio is None and losses.raw_kelly is None
    assert losses.raw_win_rate==0 and losses.losses==20


def test_costs_can_turn_gross_winner_into_measured_loss():
    t=trade(None,gross_pnl=10,costs=30)
    assert mk.net_return(t)==pytest.approx(-.002)
    assert mk.measure([t]).block_new_entries


@pytest.mark.parametrize('changes',[{'net_pnl':float('nan')},{'net_pnl':float('inf')},
    {'qty':float('nan')},{'entry_price':float('inf')},{'qty':-1,'entry_price':-1},
    {'net_pnl':None,'gross_pnl':None},{'net_pnl':None,'costs':None}])
def test_nonfinite_or_unproved_net_return_is_unknown(changes):
    t=trade(**changes)
    assert mk.net_return(t) is None
    assert mk.measure([t]).usable==0


def test_configured_sample_minimum_and_category_scope_are_preserved():
    assert mk.measure([trade()],min_trades=2).status=='NO_SAMPLE'
    assert mk.measure([trade(mode='swing')],category='nifty200_momentum').usable==0
    assert Config().measured_kelly_modes==('nifty200_momentum',)


def test_no_loss_and_all_flat_samples_never_invent_payoff():
    for trades,state in [([trade(10)],'NO_LOSS'),([trade(0)],'ALL_FLAT')]:
        r=mk.measure(trades)
        assert r.sample_state==state and r.status=='NO_SAMPLE'
        assert r.payoff_ratio is None and r.fraction is None


def test_economic_accounting_retains_excluded_losses_without_rewriting_ledger():
    rows=[trade(-100,reason='OWNER_SELL'),trade(-50,reason='SQUARE_OFF'),trade(200)]
    saved=[vars(t).copy() for t in rows]
    r=mk.measure(rows)
    assert r.excluded==2 and r.usable==1
    assert r.economic['known_exits']==3 and r.economic['net_losses']==2
    assert r.economic['observed_net_pnl']==50 and r.economic['observed_costs']==90
    assert r.economic['excluded_observed_net_pnl']==-150
    assert [vars(t) for t in rows]==saved


def test_all_loss_guard_is_wired_to_momentum_scan(tmp_path,monkeypatch):
    from trading.tests.test_measured_kelly_momentum_only_2026_10_05 import _fresh,_losers
    e=_fresh(tmp_path,monkeypatch,_losers(20,'nifty200_momentum'))
    cash=e.portfolio.cash
    e.scan()
    assert 'UPP.NS' not in e.portfolio.positions
    assert any(s['reason']=='measured_kelly_no_edge' for s in e.portfolio.last_scan_skipped)
    assert e.portfolio.cash==cash


@pytest.mark.parametrize('heat,should_open',[(.01,False),(.03,True)])
def test_scan_sizes_and_books_the_same_stop_without_second_computation(tmp_path,monkeypatch,heat,should_open):
    from trading.tests.test_measured_kelly_momentum_only_2026_10_05 import _fresh
    e=_fresh(tmp_path,monkeypatch,[])
    e.portfolio.cash=1000000
    e.cfg.category_budgets_enabled=False
    e.cfg.cash_reserve_pct=0
    e.cfg.max_portfolio_heat_pct=heat
    calls=[]
    def stop(pick,price,cfg=None):
        calls.append((pick['ticker'],price))
        pick['stop_diag']={'source':'fixture_15pct'}
        return round(price*.85,2)
    monkeypatch.setattr(e,'_stop_for',stop)
    e.scan()
    assert ('UPP.NS' in e.portfolio.positions)==should_open
    assert sum(t=='UPP.NS' for t,_ in calls)==1
    if should_open:
        p=e.portfolio.positions['UPP.NS']
        assert p.stop_price==round(p.entry_price*.85,2)
        assert p.qty*(p.entry_price-p.stop_price)<=1000000*heat
        assert p.trade_evidence['entry']['fill']['stop']==p.stop_price
    else:
        assert e.portfolio.cash==1000000
        assert not [j for j in e.portfolio.decision_journal if j.get('action')=='BUY']


def test_unknown_economic_accounting_is_not_zero():
    r=mk.measure([trade(net_pnl=float('nan'),costs=None)])
    assert r.economic['unknown_net_exits']==1
    assert r.economic['observed_net_pnl'] is None
    assert r.economic['observed_costs'] is None
