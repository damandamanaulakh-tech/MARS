"""SEQ-04: the decision path must consume proved, completed intraday bars."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trading import quote_evidence as qe
from trading import intraday_bars as ib, intraday_data as idata, intraday_scoring as isc
from trading.config import Config
from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine

NOW = datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc)  # 10:30 IST


def bar_frame(now=NOW, minutes=5, count=90):
    """Actual regular-session grid, including overnight/holiday breaks."""
    indices = []
    for day in ib.session_days(now):
        opened, closed = ib.session_bounds(day)
        indices.extend(pd.date_range(opened, closed, freq=f'{minutes}min', inclusive='left'))
    idx = pd.DatetimeIndex([t for t in indices if t + pd.Timedelta(minutes=minutes) <= pd.Timestamp(now)])[-count:]
    close = 100. + np.arange(len(idx)) * .04 + np.sin(np.arange(len(idx))) * .015
    frame = pd.DataFrame({'Open':close-.01, 'High':close+.06, 'Low':close-.06,
                          'Close':close, 'Volume':np.full(len(idx), 10000.)}, index=idx)
    frame.attrs.update(interval=f'{minutes}m', source='upstox_v3')
    return frame


def freeze_engine(monkeypatch, now=NOW):
    clock = [ib.utc_now(now).replace(tzinfo=None)]
    class Clock(datetime):
        @classmethod
        def utcnow(cls): return clock[0]
    monkeypatch.setattr('trading.engine.datetime', Clock)
    return clock


@pytest.mark.parametrize('stamp,reason', [
    ('2026-11-12T10:35:00+05:30', 'future_bar'),
    ('2026-11-12T10:30:00+05:30', 'unfinished_bar'),
    ('2026-11-12T10:26:00+05:30', 'misaligned_interval'),
    ('2026-11-12T10:25:00', 'timestamp_timezone_unavailable'),
    ('2026-11-12T09:10:00+05:30', 'outside_regular_session'),
    ('2026-11-12T15:30:00+05:30', 'outside_regular_session'),
    ('2026-11-14T10:25:00+05:30', 'non_trading_session'),
    ('2026-11-10T10:25:00+05:30', 'non_trading_session'),
    ('2026-11-11T10:25:00+05:30', 'previous_session'),
    ('2026-11-12T10:15:00+05:30', 'stale_bar'),
])
def test_bad_session_or_time_has_explicit_reason(stamp, reason, monkeypatch):
    monkeypatch.setenv('SB_IGNORE_MARKET_HOURS', '1')
    assert ib.bar_status(stamp, 5, NOW) == reason


def test_bar_start_end_boundary_and_freshness_are_not_receipt_time():
    start = '2026-11-12T10:25:00+05:30'
    assert ib.bar_status(start, 5, NOW-timedelta(microseconds=1)) == 'unfinished_bar'
    assert ib.bar_status(start, 5, NOW) is None
    assert ib.bar_status(start, 5, NOW+timedelta(seconds=360)) is None
    assert ib.bar_status(start, 5, NOW+timedelta(seconds=361)) == 'stale_bar'
    assert ib.bar_status(start, 30, NOW) == 'wrong_interval'
    assert ib.bar_status('bad', 5, NOW) == 'invalid_timestamp'
    assert ib.bar_status('2027-01-04T10:25:00+05:30', 5,
        datetime.fromisoformat('2027-01-04T10:30:00+05:30')) == 'session_calendar_unverified'


def test_completed_window_spans_real_sessions_skipping_holiday():
    cfg = Config()
    frame = bar_frame(count=180)
    clean, d = ib.validate(frame.iloc[::-1], NOW, min_bars=ib.required_bars(cfg))
    assert d['status'] == 'ready' and d['missing_bars_in_window'] == 0
    assert d['last_bar_end'] == NOW.isoformat()
    assert clean.index.is_monotonic_increasing and str(clean.index.tz) == 'UTC'
    assert not any(t.date().isoformat() == '2026-11-10' for t in clean.index)
    assert d['required_bars'] == 64


@pytest.mark.parametrize('mutation,reason', [
    ('daily', 'wrong_or_unproved_interval'), ('no_interval', 'wrong_or_unproved_interval'),
    ('30m_disguised', 'missing_bars_in_window'), ('missing', 'missing_bars_in_window'),
    ('short', 'insufficient_completed_bars'), ('columns', 'missing_ohlcv_columns'),
    ('old', 'previous_session'), ('stale', 'stale_bar')])
def test_invalid_lookback_never_scores(mutation, reason):
    frame = bar_frame(count=375)
    min_bars = 64
    if mutation == 'daily': frame.attrs['interval'] = '1d'
    elif mutation == 'no_interval': frame.attrs.clear()
    elif mutation == '30m_disguised':
        frame = frame.iloc[::-6].sort_index()
        min_bars = 20
    elif mutation == 'missing': frame = frame.drop(frame.index[-20])
    elif mutation == 'short': frame = frame.tail(20)
    elif mutation == 'columns': frame = frame.drop(columns='Volume')
    elif mutation == 'old': frame = frame.loc[frame.index.date < NOW.date()]
    elif mutation == 'stale': frame = frame.iloc[:-3]
    clean, d = ib.validate(frame, NOW, min_bars=min_bars)
    assert d['status'] == 'unavailable' and d['reason'] == reason
    if mutation == 'missing': assert d['missing_bars_in_window'] == 1


@pytest.mark.parametrize('column,value', [('Close', float('nan')), ('Volume', -1),
    ('High', 1), ('Low', 1000), ('Open', 0), ('Close', float('inf'))])
def test_bad_ohlcv_creates_visible_gap_not_filled_prices(column, value):
    frame = bar_frame()
    frame.loc[frame.index[-10], column] = value
    clean, d = ib.validate(frame, NOW, min_bars=64)
    assert d['status'] == 'unavailable' and d['rejected']['invalid_ohlcv'] == 1
    assert frame.index[-10] not in clean.index


def test_identical_duplicates_deduplicate_conflicts_remove_both_and_future_is_excluded():
    frame = bar_frame()
    duplicate = frame.tail(1).copy()
    good = pd.concat([frame, duplicate])
    clean, d = ib.validate(good, NOW, min_bars=64)
    assert d['status'] == 'ready' and len(clean) == len(frame)
    duplicate['Volume'] += 1
    bad = pd.concat([frame, duplicate])
    clean, d = ib.validate(bad, NOW, min_bars=64)
    assert d['rejected']['conflicting_duplicates'] == 1 and frame.index[-1] not in clean.index
    future = frame.tail(2).copy()
    future.index = pd.DatetimeIndex([NOW, NOW + timedelta(minutes=5)])
    future['Close'] = 9000.
    clean, d = ib.validate(pd.concat([frame, future]), NOW, min_bars=64)
    assert d['status'] == 'ready' and clean['Close'].max() < 110.
    assert d['rejected'] == {'unfinished_bar': 1, 'future_bar': 1}


@pytest.fixture
def provider(monkeypatch):
    idata.clear_cache()
    monkeypatch.setattr(idata, 'to_instrument_keys', lambda names: {t:'NSE_EQ|'+t for t in names if t != 'NO_MAP'})
    frame = bar_frame()
    calls = []
    def get(key, **kw):
        calls.append((key, kw))
        today = frame.index.date == NOW.date()
        return frame.loc[~today if kw.get('from_date') else today].copy()
    client = SimpleNamespace(get_intraday_frame=get)
    yield client, calls
    idata.clear_cache()


def test_loader_combines_today_and_history_and_restart_refetches(provider):
    client, calls = provider
    frames, details = idata.fetch(['AAA'], Config(), client, NOW)
    assert details['AAA']['status'] == 'ready' and len(frames['AAA']) == 90
    assert len(calls) == 2 and all(c[1]['interval_minutes'] == 5 for c in calls)
    assert calls[1][1]['to_date'] == '2026-11-11'
    idata.fetch(['AAA'], Config(), client, NOW)
    assert len(calls) == 3  # only prior completed history is cached
    idata.clear_cache()
    idata.fetch(['AAA'], Config(), client, NOW)
    assert len(calls) == 5


def test_loader_keeps_missing_mapping_cap_and_rotation_explicit(provider):
    client, _ = provider
    names = ['BENCH', 'AAA', 'BBB', 'NO_MAP', 'CCC']
    frames, details = idata.fetch(names, Config(), client, NOW, cap=3, priority=['BENCH'])
    assert set(frames) == {'BENCH', 'AAA', 'BBB'} and set(details) == set(names)
    assert details['NO_MAP']['reason'] == 'intraday_fetch_cap'
    frames, details = idata.fetch(names, Config(), client, NOW, cap=3, offset=2, priority=['BENCH'])
    assert set(frames) == {'BENCH', 'CCC'}
    assert details['NO_MAP']['reason'] == 'instrument_mapping_unavailable'
    assert details['AAA']['reason'] == 'intraday_fetch_cap'


def test_loader_reports_missing_provider_and_wrong_interval(provider, monkeypatch):
    monkeypatch.setattr('trading.data.history_broker', lambda: None)
    assert idata.fetch(['AAA'], Config(), now=NOW)[1]['AAA']['reason'] == 'intraday_provider_unavailable'
    frame = bar_frame()
    frame.attrs['interval'] = '30m'
    wrong = SimpleNamespace(get_intraday_frame=lambda *a, **k: frame)
    frames, details = idata.fetch(['AAA'], Config(), wrong, NOW)
    assert not frames and details['AAA']['reason'] == 'wrong_or_unproved_interval'


@pytest.mark.parametrize('status', [401, 403, 429, 500])
def test_loader_reports_http_failures_without_fallback(provider, status):
    import httpx
    def failed(*a, **k):
        raise httpx.HTTPStatusError('fixture', request=httpx.Request('GET','https://example.test'), response=httpx.Response(status))
    frames, details = idata.fetch(['AAA'], Config(), SimpleNamespace(get_intraday_frame=failed), NOW)
    assert not frames and details['AAA']['reason'] == f'provider_http_{status}'


def test_loader_deadline_does_not_publish_late_data(provider):
    from threading import Event
    started, release = Event(), Event()
    def slow(*a, **k):
        started.set()
        assert release.wait(2)
        return bar_frame()
    try:
        frames, details = idata.fetch(['AAA'], Config(), SimpleNamespace(get_intraday_frame=slow), NOW, budget_seconds=.02)
        assert started.is_set() and not frames
        assert details['AAA']['reason'] == 'intraday_fetch_deadline'
    finally:
        release.set()
    assert not frames


def test_broker_v3_paths_keep_raw_timezone_and_requested_interval(monkeypatch):
    from trading.brokers.upstox import UpstoxClient
    calls = []
    def get(url, **kw):
        calls.append((url, kw))
        return SimpleNamespace(raise_for_status=lambda:None,
            json=lambda:{'data':{'candles':[['2026-11-12T10:25:00+05:30',100,102,99,101,10,0]]}})
    monkeypatch.setattr('trading.brokers.upstox.httpx.get', get)
    client = UpstoxClient(api_key='fixture', api_secret='fixture', redirect_uri='fixture')
    today = client.get_intraday_frame('NSE_EQ|AAA')
    past = client.get_intraday_frame('NSE_EQ|AAA', from_date='2026-11-05', to_date='2026-11-11')
    assert '/v3/historical-candle/intraday/NSE_EQ%7CAAA/minutes/5' in calls[0][0]
    assert calls[1][0].endswith('/minutes/5/2026-11-11/2026-11-05')
    assert today.attrs['interval'] == past.attrs['interval'] == '5m'
    assert today.index[0].endswith('+05:30')


def test_public_quote_uses_last_completed_bar_not_open_or_future(monkeypatch):
    from trading.brokers.upstox import UpstoxClient
    candles = [[t,100,102,99,c,10,0] for t,c in [
        ('2026-11-12T10:31:00+05:30',102), ('2026-11-12T10:30:00+05:30',102),
        ('2026-11-12T10:29:00+05:30',101)]]
    monkeypatch.setattr('trading.brokers.upstox.httpx.get', lambda *a, **k:
        SimpleNamespace(raise_for_status=lambda:None, json=lambda:{'data':{'candles':candles}}))
    client = UpstoxClient(api_key='fixture', api_secret='fixture', redirect_uri='fixture')
    assert client.get_intraday_last('AAA', now=NOW) == (101., '2026-11-12T04:59:00+00:00')
    assert client.get_intraday_last('AAA', now=NOW+timedelta(days=1)) is None


@pytest.fixture
def neutral_context(monkeypatch):
    from trading import scoring
    scoring.set_scoring_context()
    monkeypatch.setattr(scoring, 'score_earnings_growth', lambda t:50.)
    monkeypatch.setattr(scoring, '_short_interest_score', lambda *a:50.)
    monkeypatch.setattr(scoring, '_analyst_revision_score', lambda t:50.)
    monkeypatch.setattr('trading.signals.news.fetch_sentiment', lambda t:SimpleNamespace(score=50.))
    monkeypatch.setattr('trading.signals.policy.active_sector_boosts', lambda:{})
    yield
    scoring.set_scoring_context()


def test_intraday_score_uses_same_timestamp_windows_and_correct_volatility_units(neutral_context):
    from trading.scoring import weights_for
    cfg = Config(mode='intraday')
    cfg.sector_map = {'AAA':'IT', 'BBB':'IT'}
    frame = bar_frame()
    total, components, diag = isc.compute('AAA', frame, frame, {'AAA':frame, 'BBB':frame}, cfg, NOW)
    assert diag['status'] == 'ready' and diag['benchmark']['aligned']
    assert components['relative_strength'] == 50.
    ret = frame.Close.pct_change().dropna().tail(cfg.volatility_days)
    expected = np.clip(50 + (frame.Close.iloc[-1] / frame.Close.iloc[-22] - 1) * 100
                       / (ret.std()*np.sqrt(252*75)*100) * 50, 0, 100)
    assert components['vol_adj_upside'] == pytest.approx(expected)
    assert total == pytest.approx(sum(components[k]*w for k,w in weights_for('intraday').items()))
    assert diag['windows']['unit'] == 'completed_5m_bars'
    assert diag['context_interval']['hot_sector_bonus'] == 'daily_context'


@pytest.mark.parametrize('benchmark', ['missing', 'misaligned', 'daily'])
def test_unavailable_benchmark_is_neutral_and_explicit_not_measured(neutral_context, benchmark):
    frame = bar_frame()
    other = frame.copy()
    if benchmark == 'missing': other = None
    elif benchmark == 'misaligned': other = other.iloc[:-1]
    else: other.attrs['interval'] = '1d'
    total, components, diag = isc.compute('AAA', frame, other, {}, Config(), NOW)
    assert total is not None and components['relative_strength'] == 50.
    assert not diag['benchmark']['aligned']
    assert diag['component_coverage']['relative_strength'] == 'unavailable_neutral_50'


def test_daily_inputs_cannot_enter_intraday_scorer(neutral_context):
    frame = bar_frame()
    frame.attrs['interval'] = '1d'
    score, components, diag = isc.compute('AAA', frame, None, {}, Config(), NOW)
    assert score is None and not components and diag['status'] == 'unavailable'


def supply_intraday(monkeypatch, frame):
    def fetch(tickers, cfg, *a, **k):
        frames, details = {}, {}
        for t in tickers:
            clean, d = ib.validate(frame, a[1] if len(a) > 1 else NOW, min_bars=ib.required_bars(cfg))
            details[t] = d
            if d['status'] == 'ready': frames[t] = clean
        return frames, details
    monkeypatch.setattr('trading.engine.idata.fetch', fetch)


def intraday_engine(tmp_path, monkeypatch):
    e = _scan_engine(tmp_path, monkeypatch)
    e.cfg.mode = 'intraday'
    e.cfg.apply_mode_preset()
    e.cfg.category_budgets_enabled = False
    e.cfg.candle_confirm_enabled = False
    e.portfolio.positions.clear()
    e.portfolio.cash = e.cfg.starting_capital
    clock = freeze_engine(monkeypatch)
    frame = bar_frame()
    close = frame.Close.iloc[-2] * 1.015
    frame.iloc[-1] = [close-.01, close+.06, close-.06, close, 100000.]
    frame.loc[frame.index[-5:], 'Volume'] = 100000.
    supply_intraday(monkeypatch, frame)
    return e, frame, clock


def test_actual_scan_uses_5m_for_score_timing_candles_and_entry_target(tmp_path, monkeypatch, neutral_context):
    from trading import engine as module, scoring, candles
    e, frame, clock = intraday_engine(tmp_path, monkeypatch)
    seen = {'score':[], 'timing':[], 'candle':[], 'target':[]}
    def spy(name, fn):
        def call(*a, **k):
            h = a[1] if name == 'score' else a[0]['history'] if name == 'target' else a[0]
            seen[name].append(h.attrs.get('interval'))
            return fn(*a, **k)
        return call
    monkeypatch.setattr(scoring, 'compute_score', spy('score', scoring.compute_score))
    monkeypatch.setattr(module, 'timing_ok', spy('timing', module.timing_ok))
    monkeypatch.setattr(module, 'candle_confirmation', spy('candle', module.candle_confirmation))
    monkeypatch.setattr(e, '_target_for', spy('target', e._target_for))
    result = e.scan()
    assert result.opened == ['UPP.NS'], result.skipped
    assert all(seen[name] and set(seen[name]) == {'5m'} for name in seen), seen
    d = e.portfolio.positions['UPP.NS'].trade_evidence['entry']
    assert d['bar']['interval'] == '5m'
    assert d['daily_context']['interval'] == '1d'
    assert d['gates']['intraday_data_final']['state'] == 'passed'
    assert e.portfolio.last_scan_picks[0]['bar']['interval'] == '5m'
    assert d['fill']['target_method']['source'] == 'atr'
    assert e.portfolio.intraday_data_status['ready'] == 1


@pytest.mark.parametrize('path', ['scan', 'ocean'])
def test_data_expiring_during_target_work_cannot_book(tmp_path, monkeypatch, neutral_context, path):
    from trading import decision_evidence as de
    e, frame, clock = intraday_engine(tmp_path, monkeypatch)
    pick = {'ticker':'AAA', 'score':80., 'history':frame, 'decision':de.new('AAA','intraday',path,NOW)}
    before = e.portfolio.cash
    def slow(*a, **k):
        clock[0] += timedelta(minutes=7)
        return 110.
    monkeypatch.setattr(e, '_target_for', slow)
    with pytest.raises(ValueError, match='intraday_data_stale_bar'):
        e._open(pick, 10, 100., NOW.replace(tzinfo=None))
    assert e.portfolio.cash == before and not e.portfolio.positions
    assert not e.portfolio.decision_journal


def test_minute_quote_expires_before_signal_bars_and_cannot_fill(tmp_path, monkeypatch):
    from trading import decision_evidence as de
    e, frame, clock = intraday_engine(tmp_path, monkeypatch)
    pick = {'ticker':'AAA', 'score':80., 'history':frame, 'decision':de.new('AAA','intraday','scan',NOW),
            'quote_evidence':qe.make(100.,'upstox_1min_delayed',NOW,'2026-11-12T10:29:00+05:30','1m')}
    def slow(*a, **k):
        clock[0] += timedelta(minutes=3)
        return 110.
    monkeypatch.setattr(e,'_target_for',slow)
    before = e.portfolio.cash
    with pytest.raises(ValueError, match='intraday_quote_expired_before_booking'):
        e._open(pick,10,100.,NOW.replace(tzinfo=None))
    assert not e.portfolio.positions and e.portfolio.cash == before
    assert pick['decision']['gates']['intraday_data_final']['state'] == 'passed'
    assert pick['decision']['gates']['quote_final']['state'] == 'failed'


def test_valid_intraday_bars_with_missing_daily_context_do_not_crash_scan(tmp_path, monkeypatch, neutral_context):
    e, frame, _ = intraday_engine(tmp_path, monkeypatch)
    monkeypatch.setattr('trading.engine.fetch_daily',lambda *a,**k:{})
    result = e.scan()
    assert result.ranked and result.ranked[0]['ticker'] == 'UPP.NS'
    rows = [r['evidence'] for r in e.portfolio.decision_journal if r.get('action') == 'SCAN_DECISION']
    assert rows[-1]['daily_context']['status'] == 'unavailable'


def test_quote_expiring_while_score_runs_cannot_mutate_peak(tmp_path, monkeypatch, neutral_context):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _held_engine
    e, _ = _held_engine(tmp_path,monkeypatch)
    clock = freeze_engine(monkeypatch)
    pos = e.portfolio.positions['AAA.NS']
    pos.mode = 'intraday'
    pos.entry_date = NOW.replace(tzinfo=None)-timedelta(minutes=20)
    batch = qe.QuoteBatch({'AAA.NS':109.},evidence={'AAA.NS':qe.make(109.,'upstox_1min_delayed',NOW,'2026-11-12T10:29:00+05:30','1m')})
    monkeypatch.setattr('trading.engine.routed_fetch_quotes',lambda *a,**k:batch)
    monkeypatch.setattr(e,'_valuation_pass',lambda *a,**k:None)
    supply_intraday(monkeypatch,bar_frame())
    def slow(*a,**k):
        clock[0] += timedelta(minutes=3)
        return 80.,{}, {'interval':'5m','status':'ready'}
    monkeypatch.setattr('trading.engine.isc.compute',slow)
    e.refresh()
    assert pos.peak_price == 100. and not e.portfolio.trades
    assert any(r.get('why') == 'quote_expired_during_rescoring' for r in e.portfolio.decision_journal)


@pytest.mark.parametrize('price,trigger', [(100., None), (98., 'STOP_LOSS'), (105., 'TAKE_PROFIT')])
def test_refresh_missing_intraday_bars_does_not_sell_on_score_but_protective_exits_work(tmp_path, monkeypatch, price, trigger):
    from trading.engine import TradingEngine
    from trading.paper import Portfolio, PaperPosition
    cfg = Config(state_dir=str(tmp_path), mode='swing')
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    pos = PaperPosition('AAA', 10, 100., NOW.replace(tzinfo=None)-timedelta(minutes=30),
        'IT', 100., 20., 'fixture', mode='intraday', stop_price=98.5, target_price=104.)
    p.positions['AAA'] = pos
    e = TradingEngine(cfg, p)
    freeze_engine(monkeypatch)
    monkeypatch.setattr(e, '_absorb_corporate_actions', lambda *a:None)
    monkeypatch.setattr(e, '_danger_pass', lambda *a:None)
    monkeypatch.setattr(e, '_valuation_pass', lambda *a, **k:None)
    monkeypatch.setattr('trading.engine.routed_fetch_quotes', lambda *a, **k:{'AAA':price})
    monkeypatch.setattr('trading.engine.fetch_daily', lambda *a, **k:{'AAA':bar_frame()})
    monkeypatch.setattr(idata, 'fetch', lambda *a, **k:({}, {'AAA':ib.validate(None,NOW)[1]}))
    e.refresh()
    if trigger:
        assert p.trades[-1].reason == trigger and p.trades[-1].mode == 'intraday'
    else:
        assert 'AAA' in p.positions and not p.trades
        rows = [r['evidence'] for r in p.decision_journal if r.get('action') == 'EXIT_DECISION']
        assert rows[-1]['gates']['score_decay']['state'] == 'unavailable'
        assert rows[-1]['bar']['interval'] == '5m'


def test_missing_score_cannot_tighten_trail_or_suppress_real_trail():
    from trading import decision_evidence as de
    from trading.exits import check_exit
    from trading.paper import PaperPosition
    cfg = Config(mode='intraday')
    cfg.apply_mode_preset()
    pos = PaperPosition('AAA',10,100.,NOW.replace(tzinfo=None),'IT',110.,80.,'fixture',
                        mode='intraday',stop_price=90.,target_price=130.)
    d = {'score_available':False,'gates':{}}
    with de.capture(d):
        assert check_exit(pos,109.2,20.,cfg,NOW.replace(tzinfo=None)) == (False,'')
    assert d['gates']['trailing']['value']['tightened'] is False
    with de.capture({'score_available':False,'gates':{}}):
        assert check_exit(pos,108.,20.,cfg,NOW.replace(tzinfo=None)) == (True,'TRAILING_STOP')


def test_mixed_book_refresh_scores_each_segment_on_its_actual_timeframe(tmp_path, monkeypatch, neutral_context):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _held_engine
    from trading import engine as module
    e, _ = _held_engine(tmp_path, monkeypatch)
    freeze_engine(monkeypatch)
    pos = e.portfolio.positions['AAA.NS']
    pos.mode = 'intraday'
    pos.entry_date = NOW.replace(tzinfo=None)-timedelta(minutes=20)
    e.portfolio.positions['BBB.NS'].mode = 'swing'
    e.portfolio.positions['BBB.NS'].entry_date = pos.entry_date
    monkeypatch.setattr(module,'routed_fetch_quotes',lambda *a,**k:{'AAA.NS':100.,'BBB.NS':100.})
    supply_intraday(monkeypatch, bar_frame())
    seen = []
    real = module.score_for_mode
    def daily(mode, ticker, frame, *a, **k):
        seen.append((mode,ticker,frame.attrs.get('interval','1d')))
        return real(mode,ticker,frame,*a,**k)
    monkeypatch.setattr(module,'score_for_mode',daily)
    monkeypatch.setattr(e,'_valuation_pass',lambda *a,**k:None)
    e.refresh()
    assert seen == [('swing','BBB.NS','1d')]
    rows = [r['evidence'] for r in e.portfolio.decision_journal
            if r.get('action') == 'EXIT_DECISION' and r.get('ticker') == 'AAA.NS']
    assert rows[-1]['bar']['interval'] == '5m' and rows[-1]['score_available']


def test_invalid_delayed_quote_cannot_raise_peak_or_replace_retained_mark(tmp_path, monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _held_engine
    e, _ = _held_engine(tmp_path, monkeypatch)
    freeze_engine(monkeypatch)
    pos = e.portfolio.positions['AAA.NS']
    e._freshest_marks({'AAA.NS':105.}, {})
    before = pos.peak_price
    batch = qe.QuoteBatch({'AAA.NS':900.}, evidence={'AAA.NS':qe.make(900., 'upstox_1min_delayed',NOW,'2026-11-12T10:31:00+05:30','1m')})
    monkeypatch.setattr('trading.engine.routed_fetch_quotes',lambda *a,**k:batch)
    monkeypatch.setattr(e,'_valuation_pass',lambda *a,**k:None)
    e.refresh()
    assert pos.peak_price == before and pos.last_mark['price'] == 105.
    assert not e.portfolio.trades


@pytest.mark.parametrize('method', ['_close', '_exit_half'])
def test_last_exit_boundary_rejects_expired_candle_without_money_changes(tmp_path, monkeypatch, method):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _held_engine
    from trading import decision_evidence as de
    e, _ = _held_engine(tmp_path,monkeypatch)
    freeze_engine(monkeypatch)
    pos = e.portfolio.positions['AAA.NS']
    before = e.portfolio.cash, pos.qty
    quote = qe.make(100.,'upstox_1min_delayed',NOW,'2026-11-12T10:20:00+05:30','1m')
    with de.capture({'quote':quote}), pytest.raises(ValueError,match='intraday_quote_expired_before_exit'):
        getattr(e,method)('AAA.NS',100.,'fixture',NOW.replace(tzinfo=None))
    assert (e.portfolio.cash,pos.qty) == before and not e.portfolio.trades
    assert e.portfolio.positions['AAA.NS'] is pos


def test_danger_calculation_cannot_use_a_quote_that_expired_during_work(tmp_path, monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _held_engine, _frame
    from trading.engine import TradingEngine
    from trading import danger
    e, _ = _held_engine(tmp_path,monkeypatch)
    clock = freeze_engine(monkeypatch)
    e.portfolio.danger_alerts_start = '2026-10-01'
    batch = qe.QuoteBatch({'AAA.NS':100.},evidence={'AAA.NS':qe.make(100.,'upstox_1min_delayed',NOW,'2026-11-12T10:29:00+05:30','1m')})
    monkeypatch.setattr(danger,'externals_for',lambda *a: {})
    fired = list(danger.VERIFIED)[:2]
    assert danger.plan('AAA.NS', fired, True).action == 'EXIT_HALF'
    def slow(*a,**k):
        clock[0] += timedelta(minutes=3)
        return fired
    monkeypatch.setattr(danger,'evaluate_stock',slow)
    before = e.portfolio.positions['AAA.NS'].to_dict()
    TradingEngine._danger_pass(e,batch,{'AAA.NS':_frame()},NOW.replace(tzinfo=None))
    assert e.portfolio.positions['AAA.NS'].to_dict() == before and not e.portfolio.trades
    assert any(r.get('why') == 'quote_or_session_expired_during_danger_check' for r in e.portfolio.decision_journal)


@pytest.mark.parametrize('bar', [
    '2026-11-12T10:31:00+05:30',   # future
    '2026-11-12T10:30:00+05:30',   # open minute
    '2026-11-11T10:29:00+05:30',   # previous session
    '2026-11-12T10:20:00+05:30',   # stale during this session
    None,                         # receipt time cannot substitute for bar time
])
def test_unproved_delayed_quote_cannot_execute(bar):
    batch = qe.QuoteBatch({'AAA.NS': 100.}, evidence={
        'AAA.NS': qe.make(100., 'upstox_1min_delayed', NOW, bar, '1m')})
    assert not qe.executable(batch, 'AAA.NS', NOW)


def test_intraday_scan_never_substitutes_daily_history(tmp_path, monkeypatch):
    engine = _scan_engine(tmp_path, monkeypatch)
    engine.cfg.mode = 'intraday'
    engine.cfg.apply_mode_preset()
    monkeypatch.setattr(engine, '_trading_allowed', lambda now=None: False)
    # No intraday provider is attached. The daily fixture remains available.
    monkeypatch.setattr('trading.data.history_broker', lambda: None)
    used = []
    monkeypatch.setattr('trading.engine.compute_score',
        lambda ticker, frame, *a, **kw: (used.append(frame) or (90., {})))
    engine.scan()
    assert not used, 'daily bars reached intraday scoring despite missing intraday data'
    rows = [r['evidence'] for r in engine.portfolio.decision_journal
            if r.get('action') == 'SCAN_DECISION' and r.get('ticker') == 'UPP.NS']
    assert rows[-1]['gates']['history']['state'] == 'unavailable'
    assert rows[-1]['bar']['interval'] == '5m'
