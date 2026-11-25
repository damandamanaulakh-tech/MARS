"""The owner's batch grants one narrow override, not a different trade engine."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from freezegun import freeze_time

from trading import quote_evidence as qe
from trading.paper import PaperPosition
from trading.portfolio_marks import remember
from trading.tests.test_audit_s4_s9_2026_10_02 import _Event, _scan_engine
from trading.tests.test_measured_kelly_wired_2026_10_02 import (
    _buys, _edge, _fresh as _base_engine, _no_edge,
)
from trading.tests.test_ocean_guard import eng as ocean_engine

DAY = '2026-11-26'
BATCH_ID = DAY + ':11111111-1111-4111-8111-111111111111'
NOW = datetime(2026, 11, 26, 6)


@pytest.fixture(autouse=True)
def batch_clock():
    with freeze_time(NOW) as clock:
        yield clock


def request(e, *, override=True, day=DAY):
    return e.strategy_batch_request(
        batch_id=BATCH_ID, actor='owner@example.invalid',
        authorized_date_ist=day, override_measured_performance=override)


def _fresh(tmp_path, monkeypatch, trades):
    """Exercise the real scanner with timestamped, executable fixture quotes."""
    e = _base_engine(tmp_path, monkeypatch, trades)
    _timestamp_quotes(monkeypatch)
    return e


def _timestamp_quotes(monkeypatch):
    from trading import engine
    prices_for = engine.routed_fetch_quotes
    def quotes(*args, **kwargs):
        prices = prices_for(*args, **kwargs)
        now = datetime.now(timezone.utc)
        return qe.QuoteBatch(prices, evidence={
            ticker: qe.make(price, 'upstox', now, provider_at=now)
            for ticker, price in prices.items()})
    monkeypatch.setattr(engine, 'routed_fetch_quotes', quotes)


def test_negative_measured_edge_is_overridden_once_with_failed_gate_preserved(
        tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    cfg_before = deepcopy(vars(e.cfg))
    with request(e):
        e.scan()
    pos = e.portfolio.positions['UPP.NS']
    evidence = pos.trade_evidence['entry']
    assert pos.mode == 'nifty200_momentum'
    assert not ({'owner_pick', 'buy_desk'} & set(pos.tags))
    assert 0 < pos.stop_price < pos.entry_price < pos.target_price
    assert evidence['path'] == 'owner_strategy_batch'
    assert evidence['gates']['measured_kelly']['state'] == 'failed'
    assert evidence['gates']['measured_kelly']['value']['usable'] == 40
    assert evidence['gates']['measured_kelly']['value']['raw_kelly'] < 0
    authority = evidence['strategy_batch']
    assert authority['batch_id'] == BATCH_ID
    assert authority['actor'] == 'owner@example.invalid'
    assert authority['authorized_date_ist'] == DAY
    assert authority['original_path'] == 'scan'
    assert authority['execution'] == 'paper'
    assert authority['override'] == {
        'scope': 'measured_performance', 'authorized': True, 'applied': True,
    }
    assert e.cfg.measured_kelly_enabled is cfg_before['measured_kelly_enabled']

    # The request has ended. A later ordinary scan sees the same losing
    # historical sample and is blocked again, without a persistent toggle.
    e.portfolio.positions.clear()
    e.portfolio.cash = 50_000_000.
    e.scan()
    assert not e.portfolio.positions
    assert {'ticker': 'UPP.NS', 'reason': 'measured_kelly_no_edge'} in e.portfolio.last_scan_skipped


def test_without_explicit_override_the_same_batch_remains_blocked(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    with request(e, override=False):
        e.scan()
    assert not e.portfolio.positions
    assert not _buys(e)
    assert {'ticker': 'UPP.NS', 'reason': 'measured_kelly_no_edge'} in e.portfolio.last_scan_skipped


def test_positive_performance_size_cap_survives_override_authority(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _edge())
    with request(e):
        e.scan()
    pos = e.portfolio.positions['UPP.NS']
    mk = _buys(e)[-1]['sizing']['measured_kelly']
    assert mk['status'] == 'ACTIVE' and mk['usable'] == 60
    assert mk['capped'] is True and mk['wanted'] > mk['cap']
    assert pos.qty * pos.entry_price <= mk['cap'] * 1.001
    authority = pos.trade_evidence['entry']['strategy_batch']['override']
    assert authority['authorized'] is True and authority['applied'] is False


@pytest.mark.parametrize('danger', [False, True])
def test_existing_ocean_rails_and_segment_survive_batch_authority(ocean_engine, monkeypatch, danger):
    e = ocean_engine
    e.portfolio.trades = _no_edge()
    _timestamp_quotes(monkeypatch)
    if danger:
        monkeypatch.setattr('trading.danger.evaluate_stock',
                            lambda *a, **k: ['T12W_0027'])
    with request(e):
        e.scan()
    if danger:
        assert 'CRASH.NS' not in e.portfolio.positions
        blocked = [row for row in e.portfolio.decision_journal
                   if row.get('action') == 'OCEAN_STOCK_BLOCKED']
        assert blocked and 'danger_T12W_0027' in blocked[0]['why']
    else:
        pos = e.portfolio.positions['CRASH.NS']
        assert 'ocean' in pos.tags and 'owner_pick' not in pos.tags
        authority = pos.trade_evidence['entry']['strategy_batch']
        assert authority['original_path'] == 'ocean'
        assert authority['override']['applied'] is False
        assert pos.trade_evidence['entry']['gates']['measured_kelly']['state'] == 'skipped'
        assert pos.qty * pos.entry_price <= e.cfg.ocean_position_value


@pytest.mark.parametrize('gate', ['macro_event', 'cash', 'segment_budget', 'persistence', 'stop_risk'])
def test_override_cannot_bypass_other_entry_checks(tmp_path, monkeypatch, gate):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    e.cfg.rotation_auto_enabled = False
    if gate == 'macro_event':
        monkeypatch.setattr('trading.calendars.is_system_event_within',
                            lambda *a, **k: _Event())
    elif gate == 'cash':
        e.portfolio.cash = 1000.
    elif gate == 'segment_budget':
        pos = PaperPosition(ticker='HELD.NS', qty=150000, entry_price=100.,
            entry_date=NOW, sector='Test', peak_price=100., entry_score=80.,
            reasoning='isolated full segment', mode='nifty200_momentum',
            stop_price=90., target_price=120.)
        remember(pos, 100., 'quote_response', NOW)
        e.portfolio.positions[pos.ticker] = pos
        e.portfolio.cash = 35_000_000.
        e.cfg.cash_reserve_pct = 0.
    elif gate == 'persistence':
        e.portfolio.save_error = 'isolated write refusal'
        monkeypatch.setattr(e.portfolio, 'save', lambda: False)
    else:
        monkeypatch.setattr(e, '_stop_for', lambda pick, price, **kwargs: price)
    before = (e.portfolio.cash, deepcopy(e.portfolio.positions))
    with request(e):
        e.scan()
    assert 'UPP.NS' not in e.portfolio.positions
    assert not _buys(e)
    assert e.portfolio.cash == before[0]
    assert set(e.portfolio.positions) == set(before[1])
    reasons = [row['reason'] for row in e.portfolio.last_scan_skipped
               if row['ticker'] == 'UPP.NS']
    assert reasons and all(reason != 'measured_kelly_no_edge' for reason in reasons)
    if gate == 'macro_event':
        assert any(reason.startswith('halt_pre_budget') for reason in reasons)
    elif gate == 'segment_budget':
        assert 'category_budget_full' in reasons
    elif gate == 'persistence':
        assert 'portfolio_save_failed' in reasons
    elif gate == 'stop_risk':
        assert 'invalid_proposed_stop' in reasons


@pytest.mark.parametrize('mode', ['short', 'swing', 'positional', 'intraday'])
def test_final_booking_uses_actual_day_and_rejects_intraday(
        tmp_path, monkeypatch, mode):
    e = _fresh(tmp_path, monkeypatch, [])
    e.cfg.mode = mode
    e.cfg.apply_mode_preset()
    before = (e.portfolio.cash, deepcopy(e.portfolio.positions),
              deepcopy(e.portfolio.decision_journal))
    day = DAY if mode == 'intraday' else '2026-11-25'
    with request(e, day=day):
        reason = ('strategy_batch_segment_not_authorized' if mode == 'intraday'
                  else 'strategy_batch_expired')
        with pytest.raises(ValueError, match=reason):
            e._open({'ticker': 'NEW.NS', 'score': 80.}, 100, 100.,
                    datetime(2026, 11, 25, 6))
    assert (e.portfolio.cash, e.portfolio.positions, e.portfolio.decision_journal) == before


def test_slow_target_calculation_cannot_fill_after_actual_market_close(
        tmp_path, monkeypatch, batch_clock):
    e = _fresh(tmp_path, monkeypatch, [])
    monkeypatch.delenv('SB_IGNORE_MARKET_HOURS', raising=False)
    batch_clock.move_to('2026-11-26T09:59:59Z')
    saved_scan_clock = datetime.utcnow()
    calculated = []
    def slow_target(*args, **kwargs):
        calculated.append(True)
        batch_clock.move_to('2026-11-26T10:00:01Z')
        return 120.
    monkeypatch.setattr(e, '_target_for', slow_target)
    before = (e.portfolio.cash, deepcopy(e.portfolio.positions),
              deepcopy(e.portfolio.decision_journal))
    with request(e):
        with pytest.raises(ValueError, match='strategy_batch_market_closed'):
            e._open({'ticker': 'NEW.NS', 'score': 80.,
                     'quote_evidence': qe.make(100., 'upstox', saved_scan_clock,
                         provider_at=saved_scan_clock.replace(tzinfo=timezone.utc))},
                    100, 100., saved_scan_clock)
    assert calculated == [True]
    assert (e.portfolio.cash, e.portfolio.positions, e.portfolio.decision_journal) == before


def test_context_is_released_after_exception(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    with pytest.raises(RuntimeError, match='isolated failure'):
        with request(e):
            raise RuntimeError('isolated failure')
    e.scan()
    assert not e.portfolio.positions
    assert {'ticker': 'UPP.NS', 'reason': 'measured_kelly_no_edge'} in e.portfolio.last_scan_skipped


@pytest.mark.parametrize('source,received,provider', [
    ('unknown', NOW, NOW), ('prev_close', NOW, NOW),
    ('upstox', datetime(2026, 11, 25, 6), datetime(2026, 11, 25, 6)),
    ('upstox', datetime(2026, 11, 26, 7), NOW),
    ('upstox', NOW, datetime(2026, 11, 26, 7)),
])
def test_batch_never_books_missing_daily_previous_day_or_future_quotes(
        tmp_path, monkeypatch, source, received, provider):
    e = _fresh(tmp_path, monkeypatch, [])
    before = (e.portfolio.cash, deepcopy(e.portfolio.positions),
              deepcopy(e.portfolio.decision_journal))
    quote = qe.make(100., source, received,
                    provider_at=provider.replace(tzinfo=timezone.utc))
    with request(e):
        with pytest.raises(ValueError, match='strategy_batch_quote_unavailable_or_stale'):
            e._open({'ticker': 'NEW.NS', 'score': 80., 'quote_evidence': quote},
                    100, 100., NOW)
    assert (e.portfolio.cash, e.portfolio.positions, e.portfolio.decision_journal) == before


def test_batch_authority_cannot_leak_to_another_thread(tmp_path, monkeypatch):
    import threading
    e = _fresh(tmp_path, monkeypatch, [])
    observed = []
    with request(e):
        worker = threading.Thread(target=lambda: observed.append(e._active_strategy_batch()))
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert e._active_strategy_batch()['batch_id'] == BATCH_ID
    assert observed == [None]
    assert e._active_strategy_batch() is None


@pytest.mark.parametrize('expire_during_fetch', [False, True])
def test_expired_batch_cannot_sell_existing_holdings_to_fund_new_entries(
        tmp_path, monkeypatch, batch_clock, expire_during_fetch):
    e = _scan_engine(tmp_path, monkeypatch)
    e.cfg.category_budgets_enabled = False
    e.cfg.cash_reserve_pct = 0.
    e.portfolio.positions['OLD.NS'].qty = 10000
    fetches = []
    def quotes(*args, **kwargs):
        fetches.append(True)
        batch_clock.move_to('2026-11-27T06:00:00Z')
        return qe.QuoteBatch({'OLD.NS': 119.}, evidence={
            'OLD.NS': qe.make(119., 'upstox', NOW,
                              provider_at=NOW.replace(tzinfo=timezone.utc))})
    monkeypatch.setattr('trading.engine.routed_fetch_quotes', quotes)
    before = e.portfolio.cash
    with request(e, day=DAY if expire_during_fetch else '2026-11-25'):
        result = e._rotation_exits([{'ticker': 'UPP.NS', 'score': 90.}], NOW)
    assert result == []
    assert bool(fetches) is expire_during_fetch
    assert 'OLD.NS' in e.portfolio.positions and e.portfolio.cash == before
    assert not e.portfolio.trades


def test_batch_position_exits_automatically_and_its_net_loss_stays_in_risk_sample(
        tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    before = e._measured_kelly(NOW)
    with request(e):
        e.scan()
    pos = e.portfolio.positions['UPP.NS']
    qty, entry, original_stop = pos.qty, pos.entry_price, pos.stop_price
    assert qty * (entry - original_stop) <= (
        e.portfolio.equity({}) * e.cfg.max_portfolio_heat_pct)
    quote = pos.stop_price * .99
    prices = qe.QuoteBatch({'UPP.NS': quote}, evidence={
        'UPP.NS': qe.make(quote, 'upstox', NOW,
                          provider_at=NOW.replace(tzinfo=timezone.utc))})
    monkeypatch.setattr(e, '_absorb_corporate_actions', lambda *a: None)
    monkeypatch.setattr(e, '_danger_pass', lambda *a: None)
    monkeypatch.setattr(e, '_valuation_pass', lambda *a, **k: None)
    monkeypatch.setattr('trading.engine.fetch_daily', lambda *a, **k: {})
    monkeypatch.setattr('trading.engine.routed_fetch_quotes', lambda *a, **k: prices)
    # The authorization has ended; ordinary automatic protection owns this exit.
    e.refresh()
    assert 'UPP.NS' not in e.portfolio.positions
    trade = e.portfolio.trades[-1]
    assert trade.reason == 'STOP_LOSS' and trade.mode == 'nifty200_momentum'
    assert trade.costs > 0 and trade.net_pnl < trade.gross_pnl < 0
    assert trade.trade_evidence['entry']['path'] == 'owner_strategy_batch'
    after = e._measured_kelly(NOW)
    assert after.usable == before.usable + 1
    assert after.losses == before.losses + 1
    assert after.economic['observed_net_pnl'] == pytest.approx(
        before.economic['observed_net_pnl'] + trade.net_pnl)
    assert after.block_new_entries is True


@pytest.mark.parametrize('held_mode', ['intraday', 'nifty200_momentum'])
def test_batch_refresh_suppresses_pyramids_but_keeps_valid_quote_stop_exit(
        tmp_path, monkeypatch, held_mode):
    e = _fresh(tmp_path, monkeypatch, [])
    e.cfg.pyramid_enabled = True
    e.cfg.category_budgets_enabled = False
    e.cfg.cash_reserve_pct = 0.
    for ticker, stop, price in [('WIN.NS', 80., 110.), ('STOP.NS', 95., 94.)]:
        e.portfolio.positions[ticker] = PaperPosition(ticker=ticker, qty=100,
            entry_price=100., entry_date=NOW, sector='Test', peak_price=100.,
            entry_score=80., reasoning='isolated held position', mode=held_mode,
            stop_price=stop, target_price=150., custom_levels=True)
    monkeypatch.setattr(e, '_absorb_corporate_actions', lambda *a: None)
    monkeypatch.setattr(e, '_danger_pass', lambda *a: None)
    monkeypatch.setattr(e, '_valuation_pass', lambda *a, **k: None)
    monkeypatch.setattr('trading.engine.fetch_daily', lambda *a, **k: {})
    prices = {'WIN.NS': 110., 'STOP.NS': 94.}
    quotes = qe.QuoteBatch(prices, evidence={
        ticker: qe.make(price, 'upstox', NOW) for ticker, price in prices.items()})
    monkeypatch.setattr('trading.engine.routed_fetch_quotes', lambda *a, **k: quotes)
    with request(e):
        e.refresh()
    assert e.portfolio.positions['WIN.NS'].qty == 100
    assert e.portfolio.positions['WIN.NS'].pyramid_adds == 0
    assert 'STOP.NS' not in e.portfolio.positions
    assert e.portfolio.trades[-1].reason == 'STOP_LOSS'
    assert e.cfg.pyramid_enabled is True
    assert not [row for row in e.portfolio.decision_journal
                if row.get('action') == 'PYRAMID_BUY']


def _old_scan_then_execution_quote(monkeypatch, batch_clock, outcome='fresh'):
    """The scan quote is 5.5 hours old before the execution request starts."""
    morning = datetime(2026, 11, 26, 3, 45, tzinfo=timezone.utc)
    afternoon = datetime(2026, 11, 26, 9, 15, tzinfo=timezone.utc)
    batch_clock.move_to(morning)
    calls = []
    def quotes(tickers, upstox_client=None):
        calls.append((list(tickers), datetime.now(timezone.utc)))
        assert list(tickers) == ['UPP.NS']
        if len(calls) == 1:
            old = qe.QuoteBatch({'UPP.NS': 200.}, evidence={
                'UPP.NS': qe.make(200., 'upstox', morning, provider_at=morning)})
            batch_clock.move_to(afternoon)
            return old
        assert len(calls) == 2, 'A failed execution quote must not retry or fall back'
        if outcome == 'exception':
            raise OSError('isolated execution quote failure')
        if outcome == 'empty':
            return qe.QuoteBatch()
        if outcome == 'expiry':
            batch_clock.move_to('2026-11-26T10:00:01Z')
        received = morning if outcome == 'cached' else datetime.now(timezone.utc)
        return qe.QuoteBatch({'UPP.NS': 400.}, evidence={
            'UPP.NS': qe.make(400., 'upstox', received, provider_at=received)})
    monkeypatch.setattr('trading.engine.routed_fetch_quotes', quotes)
    return calls, morning, afternoon


def test_execution_request_reprices_quantity_stop_costs_and_segment_budget(
        tmp_path, monkeypatch, batch_clock):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    e.cfg.rotation_auto_enabled = False
    e.cfg.sizing_mode = 'fixed'
    e.cfg.position_size_value = 400_000.
    e.cfg.budget_share_momentum = .006  # isolated 300,000 share of 50,000,000
    e.cfg.cash_reserve_pct = 0.
    e.cfg.slippage_bps = 10.
    calls, morning, afternoon = _old_scan_then_execution_quote(monkeypatch, batch_clock)
    stops, targets = [], []
    original_stop, original_target = e._stop_for, e._target_for
    def stop(pick, price, **kwargs):
        stops.append(price)
        return original_stop(pick, price, **kwargs)
    def target(pick, price, **kwargs):
        targets.append(price)
        return original_target(pick, price, **kwargs)
    monkeypatch.setattr(e, '_stop_for', stop)
    monkeypatch.setattr(e, '_target_for', target)
    cash_before = e.portfolio.cash
    with request(e):
        e.scan()
    assert calls == [(['UPP.NS'], morning), (['UPP.NS'], afternoon)]
    pos = e.portfolio.positions['UPP.NS']
    assert pos.entry_price == pytest.approx(400.4)
    assert pos.qty == 749  # 300,000 segment room at the fresh price including slippage
    assert stops == targets == [pos.entry_price]
    assert pos.qty * pos.entry_price <= 300_000 < (pos.qty + 1) * pos.entry_price
    evidence = pos.trade_evidence['entry']
    assert evidence['quote']['price'] == 400.
    assert qe.utc(evidence['quote']['received_at']) == afternoon
    assert qe.utc(evidence['quote']['execution_request_started_at']) == afternoon
    assert evidence['fill']['stop'] == pos.stop_price
    assert evidence['fill']['target'] == pos.target_price
    assert evidence['gates']['category_budget_final']['value']['notional'] == pytest.approx(
        pos.qty * pos.entry_price)
    assert evidence['gates']['category_budget_final']['value']['room'] == pytest.approx(300_000)
    order = pos.cost_basis['orders'][0]
    assert order['price'] == pos.entry_price and order['qty'] == pos.qty
    assert order['fee']['turnover'] == pytest.approx(round(pos.qty * pos.entry_price, 2))
    assert order['fee']['total'] > 0
    assert e.portfolio.cash == pytest.approx(
        cash_before - pos.qty * pos.entry_price - order['fee']['total'])


@pytest.mark.parametrize('outcome', ['exception', 'empty', 'cached'])
def test_failed_or_cached_execution_request_never_falls_back_to_old_scan_quote(
        tmp_path, monkeypatch, batch_clock, outcome):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    e.cfg.rotation_auto_enabled = False
    calls, _, afternoon = _old_scan_then_execution_quote(monkeypatch, batch_clock, outcome)
    before = e.portfolio.cash
    with request(e):
        e.scan()
    assert len(calls) == 2 and calls[-1][1] == afternoon
    assert not e.portfolio.positions and not _buys(e)
    assert e.portfolio.cash == before
    reasons = [row['reason'] for row in e.portfolio.last_scan_skipped
               if row['ticker'] == 'UPP.NS']
    assert reasons and any(reason in {
        'strategy_batch_quote_refresh_failed', 'strategy_batch_quote_unavailable_or_stale',
    } for reason in reasons)


def test_authorization_expiry_during_execution_quote_prevents_booking(
        tmp_path, monkeypatch, batch_clock):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    e.cfg.rotation_auto_enabled = False
    calls, _, _ = _old_scan_then_execution_quote(monkeypatch, batch_clock, 'expiry')
    before = e.portfolio.cash
    with request(e):
        e.scan()
    assert len(calls) == 2
    assert e.portfolio.cash == before and not e.portfolio.positions and not _buys(e)
    assert any(row['reason'] == 'strategy_batch_market_closed'
               for row in e.portfolio.last_scan_skipped if row['ticker'] == 'UPP.NS')
