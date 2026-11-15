"""Real stored-data adapters, identity resolver and comparison on engine paths."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trading import context_inputs as ci, data, market_context as mc, sector_identity as si
from trading.history_store import df_to_records
from trading.paper import Portfolio
from trading.tests.test_audit_s4_s9_2026_10_02 import _held_engine, _scan_engine
from trading.tests.test_completed_intraday_2026_11_12 import freeze_engine
from trading.tests.test_market_context_2026_11_13 import NOW


@pytest.mark.parametrize('path', ['scan', 'refresh'])
@pytest.mark.parametrize('source_case', ['fresh', 'expires_during_read', 'missing_sector_prices'])
def test_real_adapters_compute_and_save_same_window_on_engine_paths(
        tmp_path, monkeypatch, path, source_case):
    clock = freeze_engine(monkeypatch, NOW)
    if path == 'scan':
        engine = _scan_engine(tmp_path, monkeypatch)
        engine.cfg.custom_universe = ('TCS.NS',)
        engine.portfolio.positions.clear()
    else:
        engine, _ = _held_engine(tmp_path, monkeypatch)
        pos = engine.portfolio.positions.pop('AAA.NS')
        engine.portfolio.positions.clear()
        pos.ticker = 'TCS.NS'
        pos.stop_price = 140.0  # live 130 must still execute this protective exit
        engine.portfolio.positions['TCS.NS'] = pos
    cfg = engine.cfg
    cfg.momentum_lookback_days = 21
    cfg.valuation_exit_enabled = False
    monkeypatch.setattr('trading.engine.routed_fetch_quotes',
                        lambda *a, **k: {'TCS.NS': 130.0})

    # Use the real official-file parser and store reader; only clock and I/O
    # boundaries are fixtures. No fabricated precomputed context is injected.
    raw = (Path(__file__).resolve().parents[2] / 'docs/repairs/evidence/'
           '2026-11-13-sector-sources/ind_niftyitlist.csv').read_bytes()
    observed = NOW-timedelta(days=7)+timedelta(seconds=1) if source_case == 'expires_during_read' else NOW
    source = si.parse(raw, 'IT', observed)
    registry = {'schema': 1, 'model': si.MODEL, 'sources': {'IT': source}}
    reads = {'identity': 0, 'prices': [], 'sector': 0}
    def registry_reader():
        reads['identity'] += 1
        return deepcopy(registry)
    monkeypatch.setattr(si, '_reader', registry_reader)
    monkeypatch.setattr('trading.universe_refresh.read_existing',
                        lambda *a: {'TCS.NS': {'isin': source['rows']['TCS.NS']['isin']}})

    grid = mc.daily_grid(NOW, 22)
    dates = grid.tz_convert('Asia/Kolkata').tz_localize(None).normalize()
    def frame(start, end):
        closes = np.linspace(start, end, len(dates))
        return pd.DataFrame({'Open': closes, 'High': closes+1, 'Low': closes-1,
                             'Close': closes, 'Volume': 1_000_000.0}, index=dates)
    docs = {ticker: df_to_records(ticker, frame(a, b)) for ticker, a, b in
            [('TCS.NS', 100, 130), (cfg.benchmark, 200, 210), ('^INDIAVIX', 12, 12)]}
    def history_reader(tickers, rows):
        reads['prices'].append(tuple(tickers))
        return {t: deepcopy(docs[t]) for t in tickers if t in docs}
    monkeypatch.setattr(data, '_HISTORY_STORE', history_reader)
    class Timestamp:
        def __new__(cls, *a, **k):
            return pd.Timestamp(*a, **k)
        @staticmethod
        def today():
            return pd.Timestamp(NOW).tz_localize(None)
    monkeypatch.setattr(data, 'pd', SimpleNamespace(Timestamp=Timestamp, Timedelta=pd.Timedelta))
    monkeypatch.setattr('trading.engine.fetch_daily', data.fetch_daily)
    def forbidden(*a, **k):
        raise AssertionError('unexpected history network call')
    monkeypatch.setattr(data, '_fetch_daily_upstox', forbidden)
    monkeypatch.setattr(data.yf, 'download', forbidden)
    def sector_reader(since):
        reads['sector'] += 1
        if source_case == 'expires_during_read':
            clock[0] += timedelta(seconds=2)
        return [] if source_case == 'missing_sector_prices' else [
            {'date': day.date().isoformat(), 'sector': 'IT', 'close': float(value)}
            for day, value in zip(dates, np.linspace(300, 330, len(dates)))]
    monkeypatch.setattr(ci, '_sector_reader', sector_reader)

    getattr(engine, path)()
    action = 'SCAN_DECISION' if path == 'scan' else 'SELL'
    decisions = [r['evidence'] for r in engine.portfolio.decision_journal
                 if r.get('action') == action and r.get('ticker') == 'TCS.NS']
    assert decisions, 'actual engine path must persist the computed context'
    context = decisions[-1]['market_context']
    assert context['window']['start'] == grid[0].isoformat()
    assert context['window']['end'] == grid[-1].isoformat()
    assert context['stock']['source'] == context['market']['source'] == 'ohlcv_store'
    assert context['stock']['return_pct'] == pytest.approx(30)
    assert context['market']['return_pct'] == pytest.approx(5)
    assert context['stock_vs_market_pp'] == pytest.approx(25)
    assert context['identity']['instrument_isin'] == source['rows']['TCS.NS']['isin']
    assert context['identity']['evaluated_at'] == context['observed_at']
    assert reads['identity'] == reads['sector'] == 1
    assert ('TCS.NS',) in reads['prices'] and (cfg.benchmark,) in reads['prices']
    if source_case == 'fresh':
        assert context['status'] == 'ready'
        assert context['sector']['return_pct'] == pytest.approx(10)
        assert context['stock_vs_sector_pp'] == pytest.approx(20)
        assert context['sector_vs_market_pp'] == pytest.approx(5)
    else:
        assert context['status'] == 'partial'
        assert context['sector']['return_pct'] is None
        assert context['stock_vs_sector_pp'] is None
        assert context['sector']['reason'] == ('source_stale' if source_case == 'expires_during_read' else 'missing_data')
    if path == 'refresh':
        trade = next(t for t in engine.portfolio.trades if t.ticker == 'TCS.NS')
        assert trade.reason == 'STOP_LOSS'
        assert trade.trade_evidence['exit']['market_context'] == context
    else:
        assert not engine.portfolio.trades and not engine.portfolio.positions
    # Reopen the saved book under a different registry: historical evidence
    # remains exactly the observation made on the decision's clock.
    monkeypatch.setattr(si, '_reader', lambda: {})
    assert engine.portfolio.save()
    restored = Portfolio.load_or_fresh(cfg)
    retained = [r['evidence']['market_context'] for r in restored.decision_journal
                if r.get('action') == action and r.get('ticker') == 'TCS.NS']
    assert retained[-1] == context
