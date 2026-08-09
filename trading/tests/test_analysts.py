"""analyst_revision live scorer — formula, fallbacks, breaker. No network."""

import sys
import types

import trading.signals.analysts as A


def _reset():
    A._CACHE.clear()
    A._FAIL_STREAK = 0
    A._BREAKER_UNTIL = 0.0


class _FakeTicker:
    def __init__(self, info):
        self._info = info

    @property
    def info(self):
        if isinstance(self._info, Exception):
            raise self._info
        return self._info


def _fake_yf(info):
    m = types.ModuleType("yfinance")
    m.Ticker = lambda t: _FakeTicker(info)
    return m


def test_formula_matches_tested_tonight_cgcl(monkeypatch):
    """CGCL: recommendationMean 4.0 -> 25.0 (the live bearish flag)."""
    _reset()
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf(
        {"recommendationMean": 4.0, "numberOfAnalystOpinions": 5}))
    a = A.fetch_analyst_score("CGCL.NS")
    assert a is not None and a.score == 25.0 and a.analysts == 5


def test_strong_buy_scores_high_reliance(monkeypatch):
    """RELIANCE: mean 1.156 -> 96.1 (TESTED_TONIGHT row)."""
    _reset()
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf(
        {"recommendationMean": 1.156, "numberOfAnalystOpinions": 42}))
    a = A.fetch_analyst_score("RELIANCE.NS")
    assert a is not None and abs(a.score - 96.1) < 0.05


def test_no_coverage_returns_none(monkeypatch):
    _reset()
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf({}))
    assert A.fetch_analyst_score("NOCOV.NS") is None


def test_backtest_mode_returns_none():
    _reset()
    from trading import data
    # thread-local since audit fix A8 — set it the real way for THIS thread
    data.set_backtest_mode(True)
    try:
        assert A.fetch_analyst_score("RELIANCE.NS") is None
    finally:
        data.set_backtest_mode(False)


def test_error_never_raises_and_breaker_opens(monkeypatch):
    _reset()
    monkeypatch.setitem(sys.modules, "yfinance",
                        _fake_yf(RuntimeError("429 Too Many Requests")))
    for _ in range(A._BREAKER_AFTER):
        assert A.fetch_analyst_score("BLOCKED.NS") is None
    assert A._BREAKER_UNTIL > 0, "breaker should open after repeated failures"
    _reset()


def test_cache_prevents_refetch(monkeypatch):
    _reset()
    calls = []
    m = types.ModuleType("yfinance")

    def _t(t):
        calls.append(t)
        return _FakeTicker({"recommendationMean": 2.0,
                            "numberOfAnalystOpinions": 10})
    m.Ticker = _t
    monkeypatch.setitem(sys.modules, "yfinance", m)
    a1 = A.fetch_analyst_score("Z.NS")
    a2 = A.fetch_analyst_score("Z.NS")
    assert a1.score == a2.score == 75.0
    assert len(calls) == 1


def test_scoring_uses_live_value(monkeypatch):
    from trading import scoring
    import trading.signals.analysts as mod
    monkeypatch.setattr(mod, "fetch_analyst_score",
                        lambda t: A.AnalystScore(25.0, 5, 4.0))
    assert scoring._analyst_revision_score("CGCL.NS") == 25.0


def test_scoring_falls_back_to_neutral(monkeypatch):
    from trading import scoring
    import trading.signals.analysts as mod
    monkeypatch.setattr(mod, "fetch_analyst_score", lambda t: None)
    assert scoring._analyst_revision_score("X.NS") == 50.0
