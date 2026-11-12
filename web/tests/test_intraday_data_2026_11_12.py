"""SEQ-04 saved data coverage survives reload and the owner can read it."""
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest
from trading.config import Config
from trading.paper import Portfolio
from trading.engine import TradingEngine
from trading import intraday_bars as ib
from trading.tests.test_completed_intraday_2026_11_12 import NOW, bar_frame
from web import auth, db, state
from web.main import app


def coverage():
    ready = ib.validate(bar_frame(), NOW, min_bars=64)[1]
    missing = ib.validate(None, NOW, min_bars=64)[1]
    missing['reason'] = 'intraday_fetch_cap'
    return {'at':NOW.isoformat(), 'interval':'5m', 'asked':2, 'ready':1,
            'windows':ib.window_description(Config()), 'benchmark':ready,
            'rows':{'AAA.NS':ready, 'MISSING.NS':missing}, 'next_offset':119}


def test_json_and_mongo_restart_preserve_coverage_and_next_batch(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.intraday_data_status = coverage()
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(db, 'is_connected', lambda:True)
    stored = {}
    monkeypatch.setattr(db, 'save_portfolio', lambda d:stored.update(deepcopy(d)) or True)
    assert p.save()
    assert Portfolio.load_or_fresh(cfg).intraday_data_status == p.intraday_data_status
    restored = Portfolio.fresh(cfg)
    state._hydrate_portfolio_from_mongo(restored, stored)
    assert restored.intraday_data_status == p.intraday_data_status
    state._hydrate_portfolio_from_mongo(restored, {})
    assert restored.intraday_data_status == {}


def test_scan_page_shows_every_name_and_missing_data_without_fetching(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.intraday_data_status = coverage()
    before = deepcopy(p.intraday_data_status)
    e = TradingEngine(cfg,p)
    monkeypatch.setattr(state,'cfg',lambda:cfg)
    monkeypatch.setattr(state,'portfolio',lambda:p)
    monkeypatch.setattr(state,'engine',lambda:e)
    monkeypatch.setattr(auth,'current_user',lambda r:{'role':'owner','email':'fixture'})
    def forbidden(*a, **k):
        raise AssertionError('view must not fetch or trigger a scan')
    monkeypatch.setattr('trading.intraday_data.fetch',forbidden)
    monkeypatch.setattr(e,'scan',forbidden)
    body = TestClient(app).get('/scan')
    assert body.status_code == 200
    assert '1 / 2 stocks' in body.text and '5-minute bars' in body.text
    assert 'AAA.NS' in body.text and 'MISSING.NS' in body.text
    assert 'intraday fetch cap' in body.text and 'data-intraday-status="unavailable"' in body.text
    assert '09:15–15:30 IST' in body.text and 'saved observation' in body.text
    assert p.intraday_data_status == before and not p.decision_journal
    p.intraday_data_status = {}
    assert 'no intraday data check has been recorded yet' in TestClient(app).get('/scan').text


def test_den_declares_intraday_timeframe_and_does_not_claim_daily_median(tmp_path):
    from web.routes.den_routes import _category_rows
    # Exercise the actual presenter using its regular cfg/portfolio inputs.
    cfg = Config(state_dir=str(tmp_path))
    rows = _category_rows(cfg)
    by_mode = {r['key']:r for r in rows}
    assert by_mode['intraday']['bar'] == 'completed 5-minute'
    assert '5 regular sessions' in by_mode['intraday']['history']
    assert by_mode['intraday']['target_at_median_pct'] is None
    assert all(r['bar'] == 'daily' for m,r in by_mode.items() if m != 'intraday')


@pytest.mark.parametrize('path', ['market', 'limit', 'buy_desk'])
def test_owner_paths_report_quote_expiry_without_debit_or_filled_order(tmp_path, monkeypatch, path):
    from trading import quote_evidence as qe, orders
    from web import buy_desk
    cfg = Config(state_dir=str(tmp_path),mode='nifty200_momentum')
    p = Portfolio.fresh(cfg)
    e = TradingEngine(cfg,p)
    monkeypatch.setattr(state,'cfg',lambda:cfg)
    monkeypatch.setattr(state,'portfolio',lambda:p)
    monkeypatch.setattr(state,'engine',lambda:e)
    monkeypatch.setattr(auth,'current_user',lambda r:{'role':'owner','email':'fixture'})
    batch=qe.QuoteBatch({'AAA.NS':100.},evidence={'AAA.NS':qe.make(100.,'upstox',NOW)})
    monkeypatch.setattr('trading.quote_router.fetch_quotes',lambda *a,**k:batch)
    monkeypatch.setattr(e,'_trading_allowed',lambda *a:True)
    def expired(*a,**k):
        raise ValueError('intraday_quote_expired_before_booking')
    monkeypatch.setattr(e,'_open',expired)
    before=p.cash
    if path == 'market':
        client=TestClient(app)
        response=client.post('/scan/buy',data={'ticker':'AAA.NS','qty':'10'},follow_redirects=False)
        assert response.status_code == 303 and 'intraday_quote_expired_before_booking' in response.headers['location']
        assert 'no position was opened' in client.get(response.headers['location']).text
    elif path == 'limit':
        order=orders.place_limit(p,ticker='AAA.NS',limit_price=101.,qty=10,now=NOW.replace(tzinfo=None))
        assert orders.process_open_orders(e,now=NOW.replace(tzinfo=None)) == []
        assert order['status'] == 'open' and order['note'] == 'intraday_quote_expired_before_booking'
    else:
        result=buy_desk.execute(e,p,cfg,[{'ticker':'AAA.NS','recommended':True,'price':100.,'qty':10,'score':80.}],
            NOW.replace(tzinfo=None),budget_rs=10000.,market_hours_bypassed=False,quotes=batch)
        assert not result['opened'] and result['skipped'][0]['reason'] == 'intraday_quote_expired_before_booking'
    assert p.cash == before and not p.positions and not p.trades
    assert any(r.get('why') == 'intraday_quote_expired_before_booking' for r in p.decision_journal)
