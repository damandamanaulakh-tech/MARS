import os, sys, tempfile
from datetime import date
from trading.auto import (AutoConfig, RunContext, Order, check_gates, effective_mode,
                  run_daily, PIT_CLEAN)


def _cfg(**kw):
    d = tempfile.mkdtemp()
    base = dict(kill_file=os.path.join(d, "KILL"),
                journal_path=os.path.join(d, "j.jsonl"),
                state_path=os.path.join(d, "s.json"))
    base.update(kw); return AutoConfig(**base)


def _ctx(**kw):
    base = dict(today=date(2026, 7, 16), token_valid=True,
                latest_backtest_universe_tags=[PIT_CLEAN],
                paper_days_completed=45, equity=1_000_000,
                peak_equity=1_000_000, prev_close={"TCS.NS": 4000})
    base.update(kw); return RunContext(**base)


def test_live_auto_refuses_survivorship_biased_backtest():
    cfg = _cfg(mode="LIVE_AUTO", algo_id="ALGO_X_001")
    ctx = _ctx(latest_backtest_universe_tags=["SURVIVORSHIP-BIASED (...)"])
    g = check_gates(cfg, ctx, [])
    assert ("G1_PIT_INTEGRITY" in [n for n, _ in g.failed])
    assert effective_mode(cfg, g) == "LIVE_CONFIRM"   # downgrade, not halt


def test_live_auto_requires_paper_seasoning():
    cfg = _cfg(mode="LIVE_AUTO", algo_id="A1")
    g = check_gates(cfg, _ctx(paper_days_completed=3), [])
    assert "G2_PAPER_SEASONING" in [n for n, _ in g.failed]


def test_sebi_algo_id_required_for_any_live_mode():
    for m in ("LIVE_CONFIRM", "LIVE_AUTO"):
        cfg = _cfg(mode=m, algo_id="")
        g = check_gates(cfg, _ctx(), [])
        assert "G5_SEBI_ALGO_ID" in [n for n, _ in g.failed]


def test_kill_switch_halts_everything():
    cfg = _cfg(mode="LIVE_AUTO", algo_id="A1")
    open(cfg.kill_file, "w").write("stop")
    out = run_daily(cfg, _ctx(), lambda: [Order("TCS.NS", "BUY", 10, 4000)])
    assert out["mode"] == "HALT" and out["placed"] == []


def test_dead_token_never_trades_blind():
    cfg = _cfg(mode="LIVE_AUTO", algo_id="A1")
    g = check_gates(cfg, _ctx(token_valid=False), [])
    assert effective_mode(cfg, g) == "HALT"


def test_order_budget_and_price_band():
    cfg = _cfg(mode="PAPER", max_orders_per_run=2)
    orders = [Order("TCS.NS", "BUY", 10, 4000)] * 3
    g = check_gates(cfg, _ctx(), orders)
    assert "G6_ORDER_COUNT" in [n for n, _ in g.failed]
    wild = [Order("TCS.NS", "BUY", 10, 5000)]        # +25% vs prev close 4000
    g2 = check_gates(cfg, _ctx(), wild)
    assert "G6_PRICE_BAND" in [n for n, _ in g2.failed]


def test_drawdown_brake_blocks_entries_allows_exits():
    cfg = _cfg(mode="PAPER")
    ctx = _ctx(equity=850_000, peak_equity=1_000_000)   # 15% dd > 12% cap
    entry = [Order("TCS.NS", "BUY", 10, 4000)]
    exit_ = [Order("TCS.NS", "SELL", 10, 4000, is_exit=True)]
    assert not check_gates(cfg, ctx, entry).ok
    assert check_gates(cfg, ctx, exit_).ok


def test_clean_live_auto_places_with_algo_id():
    cfg = _cfg(mode="LIVE_AUTO", algo_id="ALGO_UP_007")
    seen = []
    out = run_daily(cfg, _ctx(),
                    lambda: [Order("TCS.NS", "BUY", 5, 4000)],
                    place_live=lambda o, aid: seen.append((o.ticker, aid)) or {"ok": 1})
    assert out["mode"] == "LIVE_AUTO" and out["placed"] == ["TCS.NS"]
    assert seen == [("TCS.NS", "ALGO_UP_007")]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
