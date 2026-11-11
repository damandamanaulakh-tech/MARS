"""SEQ-03: decisions retain what was actually observed, never later guesses."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio, PaperPosition
from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine, _Event

NOW = datetime(2026, 11, 11, 5, 0)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path), mode="swing")
    cfg.apply_mode_preset()
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    monkeypatch.setattr(e, "_target_for", lambda *a, **k: 120.)
    monkeypatch.setattr(e, "_stop_for", lambda *a, **k: 95.)
    return e


def _open(e):
    pick = {"ticker": "AAA.NS", "score": 80., "components": {"momentum": 80.},
            "timing_pass": True, "reasoning": "fixture"}
    e._open(pick, 10, 100., NOW)
    return e.portfolio.positions["AAA.NS"]


def test_entry_snapshot_survives_settings_change_and_restart(engine):
    pos = _open(engine)
    evidence = deepcopy(getattr(pos, "trade_evidence", {}))
    assert evidence["entry"]["fill"]["stop"] == 95.
    engine.cfg.stop_loss_pct = -.5
    engine.portfolio.save()
    restored = Portfolio.load_or_fresh(engine.cfg).positions[pos.ticker]
    assert restored.trade_evidence == evidence
    assert evidence["entry"]["quote"]["source"] == "unknown"


def test_full_and_partial_exits_keep_one_trade_identity(engine):
    pos = _open(engine)
    engine._exit_half(pos.ticker, 110., "DANGER_EXIT_HALF", NOW)
    engine._close(pos.ticker, 105., "OWNER_SELL", NOW)
    first, second = engine.portfolio.trades
    a, b = getattr(first, "trade_evidence", {}), getattr(second, "trade_evidence", {})
    assert a["trade_id"] == b["trade_id"]
    assert a["exit"]["id"] != b["exit"]["id"]
    assert a["exit"]["trigger"] == "DANGER_EXIT_HALF"
    assert b["exit"]["fill"]["qty"] == 5
    assert a["entry"] == b["entry"]


def test_owner_level_change_retains_before_and_after(engine):
    from trading.orders import apply_custom_levels
    pos = _open(engine)
    apply_custom_levels(pos, 8., 25.)
    amendments = getattr(pos, "trade_evidence", {}).get("amendments", [])
    assert amendments[-1]["before"]["stop"] == 95.
    assert amendments[-1]["after"]["stop"] == 92.
    assert amendments[-1]["after"]["target"] == 125.


def test_blocked_candidate_keeps_gate_value_and_short_circuit(tmp_path, monkeypatch):
    e = _scan_engine(tmp_path, monkeypatch, event=_Event())
    e.scan()
    rows = [j for j in e.portfolio.decision_journal
            if j.get("action") == "SCAN_DECISION" and j.get("ticker") == "UPP.NS"]
    assert rows
    d = rows[-1]["evidence"]
    assert d["gates"]["system_event"]["state"] == "failed"
    assert d["gates"]["system_event"]["value"]["event_type"] == "budget"
    assert d["gates"]["candle"]["state"] == "skipped"
    assert d["gates"]["score"]["threshold"] == e.cfg.min_score_to_enter
    assert d["bar"]["interval"] == "1d"


def test_unscored_name_is_recorded_as_unavailable(tmp_path, monkeypatch):
    e = _scan_engine(tmp_path, monkeypatch)
    e.cfg.custom_universe = ("MISSING.NS",)
    e.scan()
    rows = [j for j in e.portfolio.decision_journal
            if j.get("action") == "SCAN_DECISION" and j.get("ticker") == "MISSING.NS"]
    assert rows[-1]["evidence"]["gates"]["history"]["state"] == "unavailable"


def test_quote_candle_timestamp_is_kept_on_the_returned_batch(monkeypatch):
    from trading import quote_router as qr
    class Broker:
        def get_intraday_last(self, key):
            return (101., "2026-11-11T10:29:00+05:30")
    monkeypatch.setattr("trading.data.history_broker", lambda: Broker())
    monkeypatch.setattr(qr, "to_instrument_keys", lambda ts: {t: t for t in ts})
    monkeypatch.setattr(qr, "fetch_quotes_yf", lambda ts: {})
    batch = qr.fetch_quotes(["AAA.NS"])
    ev = getattr(batch, "evidence", {})["AAA.NS"]
    assert ev["provider_at"] == "2026-11-11T04:59:00+00:00"
    assert ev["interval"] == "1m" and ev["source"] == "upstox_1min_delayed"
    assert ev["received_at"] != ev["provider_at"]


def test_legacy_close_does_not_invent_an_entry_decision(engine):
    pos = PaperPosition("OLD.NS", 2, 100., NOW, "Unknown", 100., 50., "legacy", mode="swing")
    engine.portfolio.positions[pos.ticker] = pos
    engine._close(pos.ticker, 99., "OWNER_SELL", NOW)
    ev = getattr(engine.portfolio.trades[-1], "trade_evidence", {})
    assert ev["entry"]["status"] == "unavailable"
    assert "gates" not in ev["entry"]

@pytest.mark.parametrize('source,live,allowed', [
    ('upstox', True, True), ('upstox_1min_delayed', False, True),
    ('yf_close', False, False), ('prev_close', False, False), ('unknown', False, False)])
def test_source_classification_does_not_relabel_delayed_or_retained_prices(source, live, allowed):
    from trading import quote_evidence as qe
    batch = qe.QuoteBatch({'A': 100}, evidence={'A': qe.make(100, source, NOW)})
    assert qe.executable(batch, 'A', NOW) is allowed
    assert qe.executable(batch, 'A', NOW, live_only=True) is live


@pytest.mark.parametrize('bad', [None, 0, -1, True, float('nan'), float('inf'), 'bad'])
def test_invalid_quotes_never_execute(bad):
    from trading import quote_evidence as qe
    batch = qe.QuoteBatch({'A': bad}, evidence={'A': qe.make(bad, 'upstox', NOW)})
    assert not qe.executable(batch, 'A', NOW)


def test_quote_age_is_observed_without_unapproved_candle_age_policy():
    from trading import quote_evidence as qe
    batch = qe.QuoteBatch({'A': 100}, evidence={'A': qe.make(100, 'upstox_1min_delayed', NOW,
        '2026-11-10T10:29:00+05:30', '1m')})
    observed = qe.observation(batch, 'A', NOW)
    assert observed['provider_age_seconds'] == 86460
    assert observed['receipt_age_seconds'] == 0
    assert qe.executable(batch, 'A', NOW)  # Step 4 must separately establish candle-age policy.
    observed['source'] = 'wrong'
    assert batch.evidence['A']['source'] == 'upstox_1min_delayed'


def test_concurrent_contexts_cannot_share_gate_results():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from trading import decision_evidence as de
    barrier = Barrier(2)
    def run(t):
        d = de.new(t, 'swing', 'scan', NOW)
        with de.capture(d):
            barrier.wait(timeout=5)
            de.record('score', t, 60, passed=True)
        return d
    with ThreadPoolExecutor(2) as pool:
        first, second = list(pool.map(run, ['AAA', 'BBB']))
    assert first['gates']['score']['value'] == 'AAA'
    assert second['gates']['score']['value'] == 'BBB'
    assert de.current() is None


@pytest.mark.parametrize('price,score,days,reason,gate', [
    (94., 80., 0, 'STOP_LOSS', 'stop'), (121., 80., 0, 'TAKE_PROFIT', 'target'),
    (105., 80., 0, 'TRAILING_STOP', 'trailing'), (115., 1., 0, 'SCORE_DECAY', 'score_decay'),
    (115., 80., 50, 'MAX_HOLD', 'max_hold')])
def test_each_standard_exit_records_the_actual_trigger_and_short_circuit(engine, price, score, days, reason, gate):
    from datetime import timedelta
    from trading import decision_evidence as de
    from trading.exits import check_exit
    pos = _open(engine)
    pos.peak_price = 118.
    engine.cfg.trailing_stop_pct = -.10
    engine.cfg.max_hold_days = 40
    trace = de.new(pos.ticker, pos.mode, 'exit', NOW)
    with de.capture(trace):
        hit, why = check_exit(pos, price, score, engine.cfg, NOW+timedelta(days=days))
        assert hit and why == reason
        engine._close(pos.ticker, price, why, NOW+timedelta(days=days))
    ev = engine.portfolio.trades[-1].trade_evidence['exit']
    assert ev['gates'][gate]['state'] == 'passed'
    assert ev['gates'][gate]['threshold'] is not None
    assert ev['accounting']['net_pnl'] == engine.portfolio.trades[-1].net_pnl
    if gate == 'stop':
        assert ev['gates']['target']['state'] == 'skipped'


def test_daily_close_cannot_fill_scan_or_fund_rotation(tmp_path, monkeypatch):
    from trading import quote_evidence as qe
    e = _scan_engine(tmp_path, monkeypatch)
    batch = qe.QuoteBatch({'UPP.NS': 120., 'OLD.NS': 119.}, evidence={
        t: qe.make(px, 'yf_close', NOW, interval='1d') for t, px in [('UPP.NS',120.),('OLD.NS',119.)]})
    monkeypatch.setattr('trading.engine.routed_fetch_quotes', lambda *a, **k: batch)
    e.scan()
    assert not e.portfolio.trades and 'UPP.NS' not in e.portfolio.positions
    assert e.portfolio.last_scan_skipped[-1]['reason'] == 'quote_unavailable_or_non_executable'
    d = [j['evidence'] for j in e.portfolio.decision_journal if j.get('action') == 'SCAN_DECISION'][-1]
    assert d['gates']['quote']['state'] == 'failed'
    assert d['weights'] == {'momentum': 1-e.cfg.quality_blend_pct, 'quality': e.cfg.quality_blend_pct}


def test_missing_refresh_quote_is_recorded_without_exit(engine, monkeypatch):
    from trading import quote_evidence as qe
    pos = _open(engine)
    monkeypatch.setattr('trading.engine.routed_fetch_quotes', lambda *a, **k: qe.QuoteBatch())
    engine.refresh()
    d = [j['evidence'] for j in engine.portfolio.decision_journal if j.get('action') == 'EXIT_DECISION'][-1]
    assert d['gates']['quote']['state'] == 'unavailable'
    assert d['gates']['stop']['state'] == 'skipped'
    assert pos.ticker in engine.portfolio.positions and not engine.portfolio.trades


def test_ocean_rejection_records_own_rules_not_momentum(tmp_path, monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _frame
    e = _scan_engine(tmp_path, monkeypatch)
    e.cfg.ocean_enabled = True
    e._ocean_picks(None, {'UPP.NS': _frame()}, ['UPP.NS'], NOW)
    d = [j['evidence'] for j in e.portfolio.decision_journal if j.get('action') == 'SCAN_DECISION'][-1]
    assert d['path'] == 'ocean'
    assert d['gates']['ocean_discount']['state'] == 'failed'
    assert d['gates']['score']['state'] == 'skipped'


def test_ocean_missing_fundamentals_are_unknown(monkeypatch):
    from trading.engines import ocean
    from trading import decision_evidence as de
    monkeypatch.setattr(ocean, 'is_backtest_mode', lambda: False)
    monkeypatch.setattr(ocean, '_fundamentals', lambda t: {})
    d = de.new('AAA', 'swing', 'ocean', NOW)
    with de.capture(d):
        assert not ocean.survival_check('AAA')[0]
    assert d['gates']['ocean_fundamentals']['state'] == 'unavailable'


def test_long_amendment_history_remains_in_append_only_journal(engine):
    from trading import decision_evidence as de
    pos = _open(engine)
    for i in range(40):
        before = de.protection(pos)
        pos.stop_price += 0.1
        de.amendment(pos, before, 'fixture', NOW, portfolio=engine.portfolio)
    import json
    retained = pos.trade_evidence['amendments']
    assert 0 < len(retained) <= de.AMENDMENT_TAIL
    assert len(json.dumps(retained).encode()) <= de.AMENDMENT_BYTES_CAP
    assert pos.trade_evidence['amendments_omitted'] == 40-len(retained)
    rows = [j for j in engine.portfolio.decision_journal if j['action'] == 'TRADE_AMENDMENT']
    assert len(rows) == 40 and rows[0]['evidence']['before']['stop'] == 95.


def test_limit_wait_quote_observation_survives_restart(engine, monkeypatch):
    from trading import orders, quote_evidence as qe
    o = orders.place_limit(engine.portfolio, ticker='AAA.NS', limit_price=90., qty=1)
    batch = qe.QuoteBatch({'AAA.NS': 100}, evidence={'AAA.NS': qe.make(100, 'upstox', NOW)})
    monkeypatch.setattr('trading.quote_router.fetch_quotes', lambda *a, **k: batch)
    monkeypatch.setattr(engine, '_trading_allowed', lambda *a: True)
    assert orders.process_open_orders(engine, NOW) == []
    saved = Portfolio.load_or_fresh(engine.cfg).pending_orders[-1]
    assert saved['last_decision']['gates']['limit_price']['state'] == 'failed'
    assert saved['last_quote']['source'] == 'upstox'



def test_danger_records_pass_fail_and_missing_panel_separately(monkeypatch):
    from trading import danger, decision_evidence as de
    from trading.tests.test_audit_s4_s9_2026_10_02 import _frame
    monkeypatch.setattr(danger, '_FNS', {'hit':lambda x:True, 'clear':lambda x:False, 'absent':lambda x:None})
    monkeypatch.setattr(danger, '_EXT_FNS', {'panel':lambda *a:True})
    d = de.new('AAA','swing','exit',NOW)
    with de.capture(d):
        assert danger.evaluate_stock(_frame()) == ['hit']
    assert {k:g['state'] for k,g in d['gates'].items()} == {
        'danger:hit':'passed', 'danger:clear':'failed', 'danger:absent':'unavailable', 'danger:panel':'unavailable'}



def test_router_missing_price_is_unavailable_not_failed(engine):
    from trading import quote_evidence as qe, decision_evidence as de
    pos = _open(engine)
    batch = qe.QuoteBatch(evidence={pos.ticker:qe.make(None,'unavailable',NOW)})
    d = de.exit_decision(pos, batch, NOW)
    assert d['gates']['quote']['state'] == 'unavailable'
    assert d['quote']['price'] is None


def test_large_position_amendments_have_byte_bound_and_full_journal_copy(engine):
    import json
    from trading import decision_evidence as de
    pos = _open(engine)
    for i in range(20):
        before = de.protection(pos)
        pos.stop_price += .1
        de.amendment(pos, before, 'DANGER_TIGHTEN', NOW, portfolio=engine.portfolio,
                     details={'observations':'x'*4000})
    assert len(json.dumps(pos.trade_evidence['amendments']).encode()) <= 8192
    rows = [r for r in engine.portfolio.decision_journal if r.get('action') == 'TRADE_AMENDMENT']
    assert len(rows) == 20
    assert all(len(r['evidence']['details']['observations']) == 4000 for r in rows)
