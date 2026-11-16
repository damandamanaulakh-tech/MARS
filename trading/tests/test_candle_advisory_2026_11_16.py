"""SEQ-07 owner policy: patterns assist, eligibility and exits stay independent."""
from copy import deepcopy
from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading import candles
from trading.config import Config
from trading.paper import Portfolio
from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
from trading.tests.test_candles import _flat
from trading.tests.test_completed_intraday_2026_11_12 import intraday_engine, neutral_context

MODES = ('intraday', 'short', 'swing', 'positional', 'nifty200_momentum')


@pytest.mark.parametrize('mode', MODES)
def test_no_segment_requires_a_candle_pattern(mode):
    assert candles.candle_gate_applies(Config(mode=mode)) is False


@pytest.mark.parametrize('column,value', [
    ('High', np.nan), ('Low', np.nan), ('Volume', np.nan),
    ('High', np.inf), ('Low', -1), ('Volume', -1), ('Open', 200),
])
def test_invalid_breakout_input_never_confirms(column, value):
    frame = _flat()
    frame.iloc[-2, frame.columns.get_loc('Volume')] = 3000
    frame.iloc[-1] = [101, 106, 100.5, 105.5, 2500]
    frame.iloc[-1, frame.columns.get_loc(column)] = value
    assert not candles.breakout_candle(frame)
    assert candles.candle_confirmation(frame, Config(candle_patterns=('breakout',)))[0] is False


@pytest.mark.parametrize('mode', MODES)
def test_actual_scan_buys_without_a_pattern_and_saves_advisory(tmp_path, monkeypatch, neutral_context, mode):
    if mode == 'intraday':
        engine, frame, _ = intraday_engine(tmp_path, monkeypatch)
    else:
        engine = _scan_engine(tmp_path, monkeypatch)
        engine.cfg.mode = mode
        engine.cfg.apply_mode_preset()
        engine.cfg.min_score_to_enter = 0.
        engine.cfg.breakout_threshold_pct = 1.
        engine.cfg.volume_confirm_ratio = 0.
        engine.portfolio.positions.clear()
        engine.portfolio.cash = engine.cfg.starting_capital
    engine.cfg.candle_confirm_enabled = True
    engine.cfg.candle_patterns = ('engulfing',)  # actual bars do not engulf
    result = engine.scan()
    assert result.opened == ['UPP.NS'], result.skipped
    assert not any(r['reason'].startswith('candle_wait') for r in result.skipped)
    evidence = engine.portfolio.positions['UPP.NS'].trade_evidence['entry']
    observed = evidence['gates']['candle']
    assert observed['role'] == 'advisory'
    assert observed['state'] == 'failed'  # pattern absent, not an eligibility failure
    assert observed['value']['reason'] == 'no_pattern'
    assert observed['value']['policy'] == 'advisory'
    assert observed['value']['controls']['patterns'] == ['engulfing']
    assert observed['value']['bar']['interval'] == ('5m' if mode == 'intraday' else '1d')
    saved = deepcopy(evidence)
    engine.cfg.candle_patterns = ('breakout',)
    engine.portfolio.save()
    restored = Portfolio.load_or_fresh(engine.cfg)
    assert restored.positions['UPP.NS'].trade_evidence['entry'] == saved


def test_backtest_does_not_refuse_an_eligible_no_pattern_pick(tmp_path, monkeypatch):
    from trading import backtest as bt
    from trading.risk import RiskState
    cfg = Config(mode='swing', state_dir=str(tmp_path), candle_patterns=('engulfing',))
    cfg.apply_mode_preset()
    cfg.category_budgets_enabled = False
    hist = _flat()
    next_day = date(2026, 11, 17)
    all_hist = pd.concat([hist, pd.DataFrame([hist.iloc[-1]], index=[pd.Timestamp(next_day)])])
    monkeypatch.setattr(bt, '_score_for_mode', lambda *a: (80., {}))
    monkeypatch.setattr(bt, 'timing_ok', lambda *a: (True, 'eligible fixture'))
    monkeypatch.setattr('trading.calendars.is_system_event_within', lambda **k: None)
    book = Portfolio.fresh(cfg)
    bt._try_open_positions(cfg, book, RiskState(cfg, cfg.starting_capital),
        {'AAA.NS': hist}, None, {'AAA.NS': all_hist}, next_day)
    assert 'AAA.NS' in book.positions
    expected = deepcopy(book.positions['AAA.NS'].trade_evidence)
    assert expected['entry']['gates']['candle']['value']['reason'] == 'no_pattern'
    cfg.candle_confirm_enabled = False
    bt._close_position(book, 'AAA.NS', 101., 'BACKTEST_END', next_day)
    assert book.trades[-1].trade_evidence == expected


@pytest.mark.parametrize('column', ['Open', 'High', 'Low', 'Close', 'Volume'])
@pytest.mark.parametrize('offset', [-1, -10])
def test_missing_window_data_is_unknown_not_false_support(column, offset):
    frame = _flat()
    frame.iloc[-1] = [101, 106, 100.5, 105.5, 2500]
    frame.iloc[offset, frame.columns.get_loc(column)] = np.nan
    observed = candles.candle_assessment(frame, Config())
    assert observed['state'] == 'unavailable'
    assert observed['pattern'] is None
    assert observed['entry_veto'] is observed['exit_veto'] is False
    assert candles.scan_all_patterns(frame)['breakout'] is None


def test_valid_support_and_owner_disable_are_distinct_and_detached():
    frame = _flat()
    frame.iloc[-1] = [101, 106, 100.5, 105.5, 2500]
    cfg = Config()
    support = candles.candle_assessment(frame, cfg)
    assert support['state'] == 'passed' and support['pattern'] == 'breakout'
    cfg.candle_confirm_enabled = False
    disabled = candles.candle_assessment(frame, cfg)
    assert disabled['state'] == 'skipped' and disabled['pattern'] is None
    assert support['controls']['enabled'] is True
    assert disabled['controls']['enabled'] is False
    assert support['score_adjustment'] == disabled['score_adjustment'] == 0


@pytest.mark.parametrize('patterns', [None, 42, 'breakout', ('not_a_pattern',), ({'bad':'value'},)])
def test_malformed_saved_pattern_setting_cannot_raise_or_restore_a_veto(patterns):
    observation = candles.candle_assessment(_flat(), Config(candle_patterns=patterns))
    assert observation['state'] == 'unavailable'
    assert observation['entry_veto'] is observation['exit_veto'] is False


@pytest.mark.parametrize('damage', ['short', 'missing_column', 'duplicate', 'reversed'])
def test_unavailable_pattern_reasons_are_explicit(damage):
    frame = _flat()
    if damage == 'short': frame = frame.tail(2)
    elif damage == 'missing_column': frame = frame.drop(columns='Volume')
    elif damage == 'duplicate': frame.index = [frame.index[-1]] * len(frame)
    else: frame = frame.iloc[::-1]
    observed = candles.candle_assessment(frame, Config())
    assert observed['state'] == 'unavailable'
    assert observed['reason'] != 'no_pattern'


@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('price,reason', [(94., 'STOP_LOSS'), (121., 'TAKE_PROFIT'), (105., 'TRAILING_STOP')])
def test_refresh_protective_exits_never_consult_candles(tmp_path, monkeypatch, enabled, price, reason):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _held_engine
    from trading import engine as module
    e, _ = _held_engine(tmp_path, monkeypatch)
    e.portfolio.positions.pop('BBB.NS')
    pos = e.portfolio.positions['AAA.NS']
    pos.stop_price, pos.target_price, pos.peak_price = 95., 120., 118.
    pos.custom_levels = True
    e.cfg.candle_confirm_enabled = enabled
    monkeypatch.setattr(module, 'routed_fetch_quotes', lambda *a, **k: {'AAA.NS':price})
    def forbidden(*a, **k):
        raise AssertionError('A candle check must never delay a protective exit')
    monkeypatch.setattr(module, 'candle_assessment', forbidden)
    monkeypatch.setattr(candles, 'candle_confirmation', forbidden)
    e.refresh()
    assert 'AAA.NS' not in e.portfolio.positions
    trade = e.portfolio.trades[-1]
    assert trade.reason == reason
    assert trade.trade_evidence['exit']['trigger'] == reason


@pytest.mark.parametrize('blocker', ['quote', 'risk'])
def test_pattern_support_cannot_override_other_entry_rules(tmp_path, monkeypatch, blocker):
    from trading import quote_evidence as qe
    e = _scan_engine(tmp_path, monkeypatch)
    e.portfolio.positions.clear()
    e.portfolio.cash = e.cfg.starting_capital
    e.cfg.candle_confirm_enabled = True
    monkeypatch.setattr(candles, 'candle_confirmation', lambda *a: (True, 'breakout'))
    if blocker == 'risk': e.cfg.max_positions = 0
    else:
        monkeypatch.setattr('trading.engine.routed_fetch_quotes', lambda *a, **k:
            qe.QuoteBatch({'UPP.NS':120.}, evidence={'UPP.NS':qe.make(120., 'yf_close')}))
    e.scan()
    assert not e.portfolio.positions
    assert any(r['reason'].startswith('quote_' if blocker == 'quote' else 'max_positions')
               for r in e.portfolio.last_scan_skipped)
    saved = [r['evidence'] for r in e.portfolio.decision_journal
             if r.get('action') == 'SCAN_DECISION' and r.get('ticker') == 'UPP.NS'][-1]
    assert saved['gates']['candle']['state'] == 'passed'
