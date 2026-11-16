"""Saved advisory explanations and owner controls, with no production writes."""
from copy import deepcopy

from trading import candles, decision_evidence as de
from trading.paper import Portfolio
from trading.tests.test_candles import _flat
from web import db, state
from web.tests.test_decision_evidence_2026_11_11 import book, NOW


def test_saved_advisory_keeps_owner_controls_through_json_mongo_and_read_page(book, monkeypatch):
    engine, client = book
    engine.cfg.candle_patterns = ('engulfing',)
    observation = candles.candle_assessment(_flat(), engine.cfg)
    d = de.new('AAA.NS', 'swing', 'scan', NOW)
    de.put(d, 'candle', observation, 'Advisory only', state=observation['state'], role='advisory',
           reason='No supporting pattern; does not block eligible buys')
    saved = de.journal_decision(engine.portfolio, d, 'opened', 'Entry executed')
    pos = engine.portfolio.positions['AAA.NS']
    pos.trade_evidence['entry'] = saved
    engine.portfolio.save()
    restored = Portfolio.load_or_fresh(engine.cfg)
    assert restored.positions['AAA.NS'].trade_evidence['entry'] == saved
    doc = {'cash':engine.portfolio.cash, 'positions':{'AAA.NS':pos.to_dict()}, 'trades':[]}
    fresh = Portfolio.fresh(engine.cfg)
    state._hydrate_portfolio_from_mongo(fresh, doc)
    assert fresh.positions['AAA.NS'].trade_evidence['entry'] == saved
    engine.cfg.candle_confirm_enabled = False
    engine.cfg.candle_patterns = ('breakout',)
    before = deepcopy(engine.portfolio.decision_journal)
    monkeypatch.setattr(candles, 'candle_assessment', lambda *a: (_ for _ in ()).throw(AssertionError('read recomputed evidence')))
    assert client.get('/decisions/'+d['id']+'?format=json').json() == saved
    page = client.get('/decisions/'+d['id'])
    assert page.status_code == 200
    assert 'advisory' in page.text and 'no_pattern' in page.text and 'engulfing' in page.text
    assert 'not that it blocked entry' in page.text
    assert engine.portfolio.decision_journal == before


def test_owner_can_disable_assistance_as_a_persisted_boolean(book, monkeypatch):
    engine, client = book
    saved = []
    monkeypatch.setattr(db, 'save_settings', lambda doc: saved.append(deepcopy(doc)))
    monkeypatch.setattr(state, 'reload_config', lambda: None)
    result = client.post('/settings', data={'candle_confirm_enabled':'0'}, follow_redirects=False)
    assert result.status_code == 303
    assert engine.cfg.candle_confirm_enabled is False
    assert saved[-1]['candle_confirm_enabled'] is False
    observation = candles.candle_assessment(_flat(), engine.cfg)
    assert observation['state'] == 'skipped'
    assert observation['controls']['enabled'] is False
    assert observation['entry_veto'] is observation['exit_veto'] is False


def test_scan_parameter_pages_distinguish_no_pattern_and_unavailable(book):
    engine, client = book
    engine.portfolio.scan_matrix = {'mode':'swing', 'candle_policy':'advisory', 'rows': {
        'AAA.NS': {'score':80., 'candle':'no_pattern', 'structures':{'breakout':False}, 'outcome':'opened'},
        'MISSING.NS': {'score':70., 'candle':'nonfinite_ohlcv', 'structures':{'breakout':None}, 'outcome':''}}}
    page = client.get('/scan/parameter/candle')
    assert page.status_code == 200
    assert 'No pattern does not block' in page.text and 'protective exits remain independent' in page.text.lower()
    assert 'AAA.NS' in page.text and 'MISSING.NS' in page.text
    structure = client.get('/scan/parameter/struct:breakout')
    assert structure.status_code == 200
    assert 'Unavailable' in structure.text


def test_no_pattern_is_not_rendered_as_a_failed_gate():
    from web.routes.scan_routes import _candle_cell, _build_piano
    cls, tip = _candle_cell('no_pattern', 'AAA.NS')
    assert cls != 'blocked' and 'does not block' in tip
    piano = _build_piano({'rows': {'AAA.NS': {'candle':'nonfinite_ohlcv', 'structures':{'breakout':None}}}})
    row = next(r for r in piano['sections'] if r['key'] == 'struct:breakout')
    assert 'unavailable' in row['cells'][0]['tip']
