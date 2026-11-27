"""RUN-20261125-01 — the intraday pipeline (owner order 2026-11-26).

25 Nov, live: 37 stocks came back with valid 5-minute bars, the scan then
spent about 19 minutes on market and book work, and every bar was past its
6-minute validity when scoring began. 0 of 1,133 stocks scored, twice.

What these tests pin:
  * the 5-minute bars are fetched with scoring as the very next step;
  * the pass reads the ~100 most-traded names, all of them, every pass —
    while Ocean, sector heat and the structural flags still see the whole
    scan list;
  * a request that answered with nothing keeps its own reason when another
    request runs out of time;
  * the owner's entry rules (defaults approved): last automatic buy 14:45,
    at most 10 intraday positions, no same-day re-buy after a stop;
  * the 5-minute side pass buys like any intraday pass but leaves the Scan
    page record, Ocean, rotation selling and the bulk of the journal alone.
"""
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trading import intraday_bars as ib, intraday_data as idata, intraday_recovery as ir
from trading.config import Config
from trading.paper import ClosedTrade, PaperPosition
from trading.tests.test_completed_intraday_2026_11_12 import (  # noqa: F401
    NOW, bar_frame, intraday_engine, neutral_context)

IST = timezone(timedelta(hours=5, minutes=30))


def _strong(frame):
    """The last completed bar breaks out on volume, as in intraday_engine."""
    close = frame.Close.iloc[-2] * 1.015
    frame.iloc[-1] = [close - .01, close + .06, close - .06, close, 100000.]
    frame.loc[frame.index[-5:], 'Volume'] = 100000.
    return frame


def _flat(frame):
    frame = frame.copy()
    frame[['Open', 'High', 'Low', 'Close']] = 100.
    return frame


def supply_fresh_at_fetch(monkeypatch, seen=None):
    """A provider whose bars are fresh AT THE MOMENT IT IS ASKED — which is
    what the live feed is. `FLAT*` names get a dead-flat frame."""
    def fetch(tickers, cfg, *a, **k):
        when = ib.utc_now(a[1])
        if seen is not None:
            seen.append(a[1])
        frames, details = {}, {}
        for t in tickers:
            frame = bar_frame(now=when)
            frame = _flat(frame) if t.startswith('FLAT') else _strong(frame)
            clean, d = ib.validate(frame, when, min_bars=ib.required_bars(cfg))
            details[t] = d
            if d['status'] == 'ready':
                frames[t] = clean
        return frames, details
    monkeypatch.setattr('trading.engine.idata.fetch', fetch)


def slow_market_work(monkeypatch, clock, minutes):
    """Everything the scan does between loading daily history and scoring
    (regime, risk marks, breadth, flows ...) took ~19 minutes on 25 Nov."""
    import trading.regime as regime_module
    original = regime_module.fetch_regime

    def slow(*a, **k):
        clock[0] += timedelta(minutes=minutes)
        return original(*a, **k)
    monkeypatch.setattr('trading.regime.fetch_regime', slow)


# ------------------------------------------------------- fetch, then score

def test_bars_are_fetched_after_the_slow_work_and_scored_while_fresh(
        tmp_path, monkeypatch, neutral_context):
    e, _, clock = intraday_engine(tmp_path, monkeypatch)
    started = clock[0]
    fetched_at = []
    supply_fresh_at_fetch(monkeypatch, fetched_at)
    slow_market_work(monkeypatch, clock, 20)

    result = e.scan()

    assert fetched_at and fetched_at[0] >= started + timedelta(minutes=20), (
        "the 5-minute bars were fetched before the slow market work")
    assert [r['ticker'] for r in result.ranked] == ['UPP.NS'], (
        "bars fetched before the slow work expire before scoring: 0 scored")
    assert result.opened == ['UPP.NS'], result.skipped
    timing = e.portfolio.intraday_data_status['timing']
    assert timing['scored'] == 1 and timing['scored_after_fetch_s'] < 60


def test_the_decision_record_is_built_from_the_bars_that_are_scored(
        tmp_path, monkeypatch, neutral_context):
    e, _, clock = intraday_engine(tmp_path, monkeypatch)
    supply_fresh_at_fetch(monkeypatch)
    slow_market_work(monkeypatch, clock, 20)
    e.scan()
    entry = e.portfolio.positions['UPP.NS'].trade_evidence['entry']
    # 10:30 IST + 20 minutes of work -> the bar that ended 10:50 IST
    assert entry['bar']['interval'] == '5m'
    assert entry['gates']['intraday_data_final']['state'] == 'passed'
    asof = ib.utc_now(datetime.fromisoformat(e.portfolio.intraday_data_status['at']))
    assert asof >= NOW + timedelta(minutes=20)


# ------------------------------------------------- the ~100 most-traded list

def _daily(turnover_cr, bars=40):
    idx = pd.bdate_range('2026-09-01', periods=bars)
    close = np.full(bars, 100.)
    return pd.DataFrame({'Open': close, 'High': close, 'Low': close, 'Close': close,
                         'Volume': np.full(bars, turnover_cr * 1e7 / 100.)}, index=idx)


def test_the_intraday_list_is_the_most_traded_names(tmp_path, monkeypatch):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    pool = [f'N{i:03d}.NS' for i in range(150)]
    history = {t: _daily(10 + i) for i, t in enumerate(pool)}  # N149 trades most
    names, size = e._intraday_universe(pool, history)
    assert size == 150 and len(names) == 100
    assert names[0] == 'N149.NS' and set(names) == set(pool[50:])


def test_held_names_take_no_slot_and_unranked_names_only_fill_spare_ones(tmp_path, monkeypatch):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    e.cfg.intraday_universe_size = 3
    pool = ['A.NS', 'B.NS', 'C.NS', 'HELD.NS', 'NOHIST.NS', 'D.NS']
    history = {'A.NS': _daily(5), 'B.NS': _daily(50), 'C.NS': _daily(20),
               'HELD.NS': _daily(500), 'D.NS': _daily(1)}
    e.portfolio.positions['HELD.NS'] = SimpleNamespace(mode='swing', tags=[])
    assert e._intraday_universe(pool, history) == (['B.NS', 'C.NS', 'A.NS'], 6)
    # two ranked names only -> the name with no daily file fills the third slot
    del history['A.NS'], history['D.NS']
    names, _ = e._intraday_universe(pool, history)
    assert names == ['B.NS', 'C.NS', 'A.NS']
    assert e._intraday_universe(pool, {})[0] == ['A.NS', 'B.NS', 'C.NS']


@pytest.mark.parametrize('size', [0, -5, None, 'x'])
def test_size_zero_or_unreadable_keeps_the_whole_list(tmp_path, monkeypatch, size):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    e.cfg.intraday_universe_size = size
    pool = [f'N{i}.NS' for i in range(130)]
    assert e._intraday_universe(pool, {}) == (pool, 130)


def test_the_list_never_outgrows_the_fetch_cap_and_a_short_list_is_unchanged(tmp_path, monkeypatch):
    from trading.data import MAX_NETWORK_FALLBACK
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    e.cfg.intraday_universe_size = 5000
    pool = [f'N{i}.NS' for i in range(400)]
    names, _ = e._intraday_universe(pool, {t: _daily(10) for t in pool})
    assert len(names) == MAX_NETWORK_FALLBACK - 1   # + the benchmark = the cap
    short = pool[:40]
    assert e._intraday_universe(short, {})[0] == short


def test_only_the_scored_names_are_narrowed_ocean_still_sees_the_whole_list(
        tmp_path, monkeypatch, neutral_context):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    supply_fresh_at_fetch(monkeypatch)
    e.cfg.custom_universe = ('UPP.NS', 'FLAT1.NS', 'FLAT2.NS')
    e.cfg.intraday_universe_size = 1
    ocean_saw = []
    monkeypatch.setattr(e, '_ocean_picks',
                        lambda regime, history, universe, now: ocean_saw.append(list(universe)) or [])
    result = e.scan()
    status = e.portfolio.intraday_data_status
    assert status['asked'] == 1 and status['pool'] == 3
    assert status['list_rule'].startswith('top 1 of 3 by median rupee turnover')
    assert [r['ticker'] for r in result.ranked] == ['UPP.NS']
    assert sorted(ocean_saw[0]) == ['FLAT1.NS', 'FLAT2.NS', 'UPP.NS']


# ------------------------------------------------------ honest fetch reasons

def test_an_answered_empty_request_is_not_relabelled_a_deadline(monkeypatch):
    idata.clear_cache()
    monkeypatch.setattr(idata, 'to_instrument_keys', lambda names: {t: 'NSE_EQ|' + t for t in names})
    release = Event()

    def get(key, **kw):
        if key.endswith('SLOW'):
            assert release.wait(3)
        return pd.DataFrame()
    try:
        frames, details = idata.fetch(['EMPTY', 'SLOW'], Config(),
                                      SimpleNamespace(get_intraday_frame=get), NOW,
                                      budget_seconds=.4)
    finally:
        release.set()
        idata.clear_cache()
    assert not frames
    assert details['SLOW']['reason'] == 'intraday_fetch_deadline'
    assert details['EMPTY']['reason'] != 'intraday_fetch_deadline', (
        "a request that answered with nothing was reported as a deadline")


# ------------------------------------------------- last automatic buy: 14:45

def _ist(hour, minute):
    return datetime(2026, 11, 12, hour, minute, tzinfo=IST).astimezone(timezone.utc)


def test_automatic_intraday_buys_stop_at_the_owners_time(monkeypatch):
    monkeypatch.delenv('SB_IGNORE_MARKET_HOURS', raising=False)
    cfg = Config()
    assert cfg.intraday_last_entry_time == '14:45'
    assert ir.auto_entry_allowed('intraday', _ist(14, 44), cfg)
    assert not ir.auto_entry_allowed('intraday', _ist(14, 45), cfg)
    assert not ir.auto_entry_allowed('intraday', _ist(15, 0), cfg)
    # the owner's own order keeps the square-off time, as before
    assert ir.entry_allowed('intraday', _ist(15, 0))
    assert not ir.entry_allowed('intraday', _ist(15, 25))
    # the other categories never had, and do not get, a time of day
    for mode in ('nifty200_momentum', 'short', 'swing', 'positional'):
        assert ir.auto_entry_allowed(mode, _ist(15, 20), cfg)


@pytest.mark.parametrize('raw', ['', None, 'soon', '25:99'])
def test_a_blank_or_unreadable_time_falls_back_to_the_square_off_rule(monkeypatch, raw):
    monkeypatch.delenv('SB_IGNORE_MARKET_HOURS', raising=False)
    cfg = Config()
    cfg.intraday_last_entry_time = raw
    assert ir.auto_entry_allowed('intraday', _ist(15, 20), cfg)
    assert not ir.auto_entry_allowed('intraday', _ist(15, 25), cfg)


@pytest.mark.parametrize('hour,minute,buys', [(14, 40, True), (14, 50, False)])
def test_the_scan_refuses_a_cleared_pick_after_the_time(
        tmp_path, monkeypatch, neutral_context, hour, minute, buys):
    e, _, clock = intraday_engine(tmp_path, monkeypatch)
    monkeypatch.delenv('SB_IGNORE_MARKET_HOURS', raising=False)
    clock[0] = _ist(hour, minute).replace(tzinfo=None)
    supply_fresh_at_fetch(monkeypatch)
    result = e.scan()
    assert [r['ticker'] for r in result.ranked] == ['UPP.NS']
    if buys:
        assert result.opened == ['UPP.NS'], result.skipped
    else:
        assert not result.opened and not e.portfolio.positions
        assert {'ticker': 'UPP.NS', 'reason': 'intraday_entry_deadline'} in result.skipped


# ------------------------------------- at most 10, and no re-buy after a stop

def _position(ticker, mode='intraday'):
    return PaperPosition(ticker=ticker, qty=10, entry_price=100., entry_date=datetime(2026, 11, 12, 4, 0),
                         stop_price=97.5, target_price=101.5, sector='Unknown', peak_price=100.,
                         entry_score=75., reasoning='fixture', mode=mode)


def _pick(ticker='NEW.NS'):
    from trading import decision_evidence as de
    return {'ticker': ticker, 'score': 80., 'decision': de.new(ticker, 'intraday', 'scan', NOW)}


def test_the_eleventh_intraday_position_is_refused(tmp_path, monkeypatch):
    e, _, clock = intraday_engine(tmp_path, monkeypatch)
    assert e.cfg.intraday_max_positions == 10
    for i in range(9):
        e.portfolio.positions[f'I{i}.NS'] = _position(f'I{i}.NS')
    # positions of the other categories do not count against the intraday cap
    for i in range(5):
        e.portfolio.positions[f'S{i}.NS'] = _position(f'S{i}.NS', mode='swing')
    pick = _pick()
    assert e._intraday_entry_rule(pick, pick['decision'], clock[0]) is None
    assert pick['decision']['gates']['intraday_position_cap']['state'] == 'passed'
    e.portfolio.positions['I9.NS'] = _position('I9.NS')
    pick = _pick()
    assert e._intraday_entry_rule(pick, pick['decision'], clock[0]) == 'intraday_position_cap'
    assert pick['decision']['gates']['intraday_position_cap']['state'] == 'failed'
    e.cfg.intraday_max_positions = 0            # 0 = no cap
    assert e._intraday_entry_rule(_pick(), None, clock[0]) is None


def test_the_cap_holds_inside_one_pass_too(tmp_path, monkeypatch, neutral_context):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    supply_fresh_at_fetch(monkeypatch)
    e.cfg.custom_universe = ('UPP.NS', 'UP2.NS', 'UP3.NS')
    e.cfg.intraday_max_positions = 2
    quotes = {'UPP.NS': 101., 'UP2.NS': 101., 'UP3.NS': 101.}
    monkeypatch.setattr('trading.engine.routed_fetch_quotes',
                        lambda tickers, upstox_client=None: {t: quotes[t] for t in tickers if t in quotes})
    result = e.scan()
    assert len(result.ranked) == 3
    assert len(result.opened) == 2, (result.opened, result.skipped)
    assert [s['reason'] for s in result.skipped].count('intraday_position_cap') == 1


def _closed(ticker, reason, when, mode='intraday'):
    return ClosedTrade(ticker=ticker, qty=10, entry_date=when - timedelta(hours=1), exit_date=when,
                       entry_price=100., exit_price=97.5, gross_pnl=-25., costs=0., net_pnl=-25.,
                       reason=reason, sector='Unknown', entry_score=75., reasoning='fixture', mode=mode)


def test_no_intraday_rebuy_of_a_stock_stopped_out_today(tmp_path, monkeypatch):
    e, _, clock = intraday_engine(tmp_path, monkeypatch)
    now = clock[0]
    assert e.cfg.intraday_rebuy_after_stop is False
    e.portfolio.trades += [
        _closed('STOP.NS', 'STOP_LOSS', now - timedelta(minutes=30)),
        _closed('TRAIL.NS', 'TRAILING_STOP', now - timedelta(minutes=20)),
        _closed('WIN.NS', 'TAKE_PROFIT', now - timedelta(minutes=10)),
        _closed('OLD.NS', 'STOP_LOSS', now - timedelta(days=1)),
        _closed('SWING.NS', 'STOP_LOSS', now - timedelta(minutes=5), mode='swing'),
    ]
    refused = {t: e._intraday_entry_rule(_pick(t), None, now)
               for t in ('STOP.NS', 'TRAIL.NS', 'WIN.NS', 'OLD.NS', 'SWING.NS', 'FRESH.NS')}
    assert refused == {'STOP.NS': 'intraday_rebuy_after_stop',
                       'TRAIL.NS': 'intraday_rebuy_after_stop',
                       'WIN.NS': None, 'OLD.NS': None, 'SWING.NS': None, 'FRESH.NS': None}
    pick = _pick('STOP.NS')
    e._intraday_entry_rule(pick, pick['decision'], now)
    assert pick['decision']['gates']['intraday_rebuy_after_stop']['state'] == 'failed'
    e.cfg.intraday_rebuy_after_stop = True     # the owner's switch
    assert e._intraday_entry_rule(_pick('STOP.NS'), None, now) is None


def test_the_two_rules_touch_only_the_intraday_scan(tmp_path, monkeypatch):
    e, _, clock = intraday_engine(tmp_path, monkeypatch)
    for i in range(12):
        e.portfolio.positions[f'I{i}.NS'] = _position(f'I{i}.NS')
    ocean = dict(_pick(), ocean=True)
    assert e._intraday_entry_rule(ocean, None, clock[0]) is None
    e.cfg.mode = 'swing'
    assert e._intraday_entry_rule(_pick(), None, clock[0]) is None


# --------------------------------------------------- the 5-minute side pass

def _side_ready(tmp_path, monkeypatch):
    e, _, clock = intraday_engine(tmp_path, monkeypatch)
    supply_fresh_at_fetch(monkeypatch)
    e.cfg.custom_universe = ('UPP.NS', 'FLAT1.NS', 'FLAT2.NS')
    p = e.portfolio
    p.last_scan, p.last_scan_live = 'LAST', 'LIVE'
    p.last_scan_picks = [{'ticker': 'KEEP.NS'}]
    p.last_scan_skipped = [{'ticker': 'K.NS', 'reason': 'kept'}]
    p.last_scan_opened = ['KEPT.NS']
    p.scan_matrix = {'mode': 'positional', 'rows': {'KEEP.NS': {}}}
    p.scan_history = [{'ts': 'kept'}]
    p.virtual_book = {'KEEP.NS': {'first_seen': 'kept'}}
    return e, clock


def _screens(p):
    return (p.last_scan, p.last_scan_live, list(p.last_scan_picks), list(p.last_scan_skipped),
            list(p.last_scan_opened), dict(p.scan_matrix), list(p.scan_history), dict(p.virtual_book))


def _rows(p, action):
    return [r for r in p.decision_journal if r.get('action') == action]


def test_the_side_pass_buys_and_leaves_every_shared_screen_alone(
        tmp_path, monkeypatch, neutral_context):
    e, _ = _side_ready(tmp_path, monkeypatch)
    before = _screens(e.portfolio)
    refuse = lambda *a, **k: pytest.fail('not part of the 5-minute pass')
    monkeypatch.setattr(e, '_ocean_picks', refuse)
    monkeypatch.setattr(e, '_rotation_exits', refuse)

    result = e.scan(side_pass=True)

    assert result.opened == ['UPP.NS'], result.skipped
    assert e.portfolio.positions['UPP.NS'].mode == 'intraday'
    assert _screens(e.portfolio) == before
    summary = _rows(e.portfolio, 'INTRADAY_PASS')
    assert len(summary) == 1
    assert summary[0]['opened'] == ['UPP.NS'] and summary[0]['scored'] == 3
    assert summary[0]['asked'] == 3 and summary[0]['ready'] == 3
    assert 'bought: UPP.NS' in summary[0]['why']
    last = e.portfolio.intraday_data_status['last_pass']
    assert last['opened'] == ['UPP.NS'] and last['timing']['scored'] == 3
    # one full record for the stock it bought; the two it did not select are
    # counted in the summary, not written out at ~2 KB each every 5 minutes
    decisions = _rows(e.portfolio, 'SCAN_DECISION')
    assert [d['ticker'] for d in decisions] == ['UPP.NS']
    assert decisions[0]['evidence']['outcome'] == 'bought'
    assert sum(r['stocks'] for r in summary[0]['not_bought']) == 2


def test_an_ordinary_pass_still_records_every_stock_and_the_screens(
        tmp_path, monkeypatch, neutral_context):
    e, _ = _side_ready(tmp_path, monkeypatch)
    result = e.scan()
    assert result.opened == ['UPP.NS']
    scan_rows = [d for d in _rows(e.portfolio, 'SCAN_DECISION') if d['evidence']['path'] == 'scan']
    assert sorted(d['ticker'] for d in scan_rows) == ['FLAT1.NS', 'FLAT2.NS', 'UPP.NS']
    # Ocean still looks at the list in an ordinary pass, as it always did
    assert any(d['evidence']['path'] == 'ocean' for d in _rows(e.portfolio, 'SCAN_DECISION'))
    assert not _rows(e.portfolio, 'INTRADAY_PASS')
    assert e.portfolio.last_scan not in ('LAST', None)
    assert e.portfolio.scan_matrix['mode'] == 'intraday'
    assert [p['ticker'] for p in e.portfolio.last_scan_picks][0] == 'UPP.NS'


def test_the_same_refusal_is_recorded_once_a_day_not_every_five_minutes(
        tmp_path, monkeypatch, neutral_context):
    e, clock = _side_ready(tmp_path, monkeypatch)
    for i in range(10):                         # the cap of 10 is already full
        e.portfolio.positions[f'I{i}.NS'] = _position(f'I{i}.NS')
    monkeypatch.setattr(e, 'square_off_intraday', lambda *a, **k: [])
    for _ in range(3):
        result = e.scan(side_pass=True)
        assert not result.opened
        clock[0] += timedelta(minutes=5)
    assert len(_rows(e.portfolio, 'INTRADAY_PASS')) == 3
    refused = _rows(e.portfolio, 'SCAN_DECISION')
    assert [d['why'] for d in refused if d['ticker'] == 'UPP.NS'] == ['intraday_position_cap']
    # every stock, held ones included, has at most ONE record after three passes
    assert len(refused) == len({d['ticker'] for d in refused})
    # a DIFFERENT reason for the same stock is new information: recorded
    e.cfg.intraday_max_positions = 0
    e.portfolio.trades.append(_closed('UPP.NS', 'STOP_LOSS', clock[0] - timedelta(minutes=1)))
    e.scan(side_pass=True)
    assert [d['why'] for d in _rows(e.portfolio, 'SCAN_DECISION') if d['ticker'] == 'UPP.NS'] == [
        'intraday_position_cap', 'intraday_rebuy_after_stop']


def test_the_side_pass_exists_for_the_intraday_category_only(tmp_path, monkeypatch):
    e, _ = _side_ready(tmp_path, monkeypatch)
    e.cfg.mode = 'swing'
    e.cfg.apply_mode_preset()
    journal = list(e.portfolio.decision_journal)
    with pytest.raises(ValueError, match='intraday 5-minute pass only'):
        e.scan(side_pass=True)
    assert e.portfolio.decision_journal == journal and not e.portfolio.positions


def _correction_regime(monkeypatch):
    from trading.regime import RegimeReading
    monkeypatch.setattr('trading.regime.fetch_regime', lambda *a, **k: RegimeReading(
        timestamp=datetime.utcnow(), nifty_close=90.0, nifty_200dma=100.0,
        pct_below_52w_high=14.0, vix=18.0, fii_5d_net=-100.0,
        regime='correction', everest_active=False, ocean_active=True,
        sizing_multiplier_everest=0.0, sizing_multiplier_ocean=1.0,
        reasoning='test fixture'))


@pytest.mark.parametrize('side_pass,lines', [(False, 1), (True, 0)])
def test_market_context_lines_are_not_repeated_every_five_minutes(
        tmp_path, monkeypatch, neutral_context, side_pass, lines):
    """Festival window and brake-off are written by EVERY pass while they
    hold. Twice a day from the rotations is the record; 66 more copies a day
    from the side pass would be noise (and one such line goes to the phone)."""
    e, _ = _side_ready(tmp_path, monkeypatch)
    monkeypatch.setattr('trading.calendars.festival_context', lambda days=5: 'Fixture Festival')
    _correction_regime(monkeypatch)
    e.cfg.correction_brake_enabled = False
    e.cfg.correction_vix_reentry = False
    result = e.scan(side_pass=side_pass)
    assert len(_rows(e.portfolio, 'CONTEXT')) == lines
    assert len(_rows(e.portfolio, 'CORRECTION_BRAKE_OFF')) == lines
    # the brake-off itself still applies to the pass: the pick is bought
    assert result.opened == ['UPP.NS'], result.skipped


def test_nothing_can_be_scored_before_the_first_bar_closes(monkeypatch):
    monkeypatch.delenv('SB_IGNORE_MARKET_HOURS', raising=False)
    assert not ir.first_bar_complete(_ist(9, 15))
    assert not ir.first_bar_complete(_ist(9, 19))
    assert ir.first_bar_complete(_ist(9, 20))
    assert ir.first_bar_complete(_ist(14, 0))


# ------------------------------- the side pass does not reload daily inputs

def _count_daily_reads(monkeypatch):
    """Count every read of an input that is one-figure-a-day."""
    import trading.engine as engine_module
    import trading.regime as regime_module
    reads = {'daily': [], 'flows': 0, 'regime': 0}
    load_daily, read_regime = engine_module.fetch_daily, regime_module.fetch_regime

    def daily(tickers, period='6mo'):
        reads['daily'].append(tuple(tickers) if not isinstance(tickers, str) else (tickers,))
        return load_daily(tickers, period=period)

    def flows(*a, **k):
        reads['flows'] += 1
        return None

    def regime(*a, **k):
        reads['regime'] += 1
        return read_regime(*a, **k)
    monkeypatch.setattr('trading.engine.fetch_daily', daily)
    monkeypatch.setattr('trading.signals.flows.get_flows', flows)
    monkeypatch.setattr('trading.regime.fetch_regime', regime)
    return reads


def test_the_side_pass_loads_the_days_daily_inputs_once_and_reuses_them(
        tmp_path, monkeypatch, neutral_context):
    e, clock = _side_ready(tmp_path, monkeypatch)
    e.cfg.intraday_max_positions = 0
    reads = _count_daily_reads(monkeypatch)

    e.scan(side_pass=True)
    first = (len(reads['daily']), reads['flows'], reads['regime'])
    assert first == (2, 1, 1), first            # the list + the benchmark; flows; regime
    for _ in range(3):
        clock[0] += timedelta(minutes=5)
        e.scan(side_pass=True)
    assert (len(reads['daily']), reads['flows'], reads['regime']) == first, (
        "the 5-minute pass reloaded inputs that change once a day")
    # ...and it still scored fresh 5-minute bars on every one of those passes
    assert len(_rows(e.portfolio, 'INTRADAY_PASS')) == 4
    assert all(r['scored'] == 3 for r in _rows(e.portfolio, 'INTRADAY_PASS'))


def test_an_ordinary_pass_never_reads_from_the_side_pass_copy(
        tmp_path, monkeypatch, neutral_context):
    e, clock = _side_ready(tmp_path, monkeypatch)
    reads = _count_daily_reads(monkeypatch)
    e.scan(side_pass=True)
    before = (len(reads['daily']), reads['flows'], reads['regime'])
    clock[0] += timedelta(minutes=5)
    e.scan()
    after = (len(reads['daily']), reads['flows'], reads['regime'])
    assert after[0] >= before[0] + 2 and after[1] == before[1] + 2 and after[2] == before[2] + 1


def test_the_copy_expires_and_a_changed_list_is_loaded_again(
        tmp_path, monkeypatch, neutral_context):
    e, clock = _side_ready(tmp_path, monkeypatch)
    reads = _count_daily_reads(monkeypatch)
    e.scan(side_pass=True)
    assert len(reads['daily']) == 2
    e.cfg.custom_universe = ('UPP.NS', 'FLAT1.NS')          # the owner edits the list
    e.scan(side_pass=True)
    assert len(reads['daily']) == 4 and reads['flows'] == 1
    monkeypatch.setattr(type(e), 'SIDE_DAILY_REUSE_S', -1.0)  # thirty minutes later
    e.scan(side_pass=True)
    # an expired copy is read again: the two flow reads of one pass, and the regime
    assert len(reads['daily']) == 6 and reads['flows'] == 3 and reads['regime'] == 2


def test_a_failed_reading_is_not_kept(tmp_path, monkeypatch):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    calls = []

    def load():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('feed down')
        return 'reading'
    with pytest.raises(RuntimeError):
        e._side_reuse('regime', load)
    assert e._side_reuse('regime', load) == 'reading'
    assert e._side_reuse('regime', load) == 'reading' and len(calls) == 2
