from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from trading import validation as v, forward_validation as fv
from trading.config import Config
from trading.paper import Portfolio, PaperPosition, ClosedTrade


def leg(key="a", pnl=-10, cost=2, qty=1):
    return dict(trade_id=key, net_pnl=pnl, costs=cost, qty=qty)


def test_empty_unknown_is_not_positive_edge():
    r = v.performance([], [], 100)
    assert r["net_expectancy"] is None and r["win_rate_pct"] is None
    assert r["acceptance"] == "not_established"


def test_partials_are_one_economic_trade_and_all_exit_reasons_count():
    r = v.performance([leg(pnl=10), leg(pnl=-30), leg("b", pnl=-5)], [], 100)
    assert r["closed_trades"] == 2
    assert r["net_expectancy"] == -12.5 and r["loss_rate_pct"] == 100
    assert v.performance([leg()], [], 100, ["a"])["net_expectancy"] is None


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), True, "unknown"])
def test_unknown_costs_or_pnl_exclude_whole_trade(bad):
    assert v.performance([leg(cost=bad)], [], 100)["unknown_trades"] == 1
    assert v.performance([leg(pnl=bad)], [], 100)["net_expectancy"] is None


def test_drawdown_includes_initial_capital_and_terminal_charges():
    r = v.performance([leg()], [("a", 99), ("b", 110), ("c", 90)], 100)
    assert r["max_drawdown_pct"] == pytest.approx(18.181818)
    assert r["final_equity"] == 90
    assert v.performance([], [("a", 95)], 100)["max_drawdown_pct"] == 5


def test_missing_marks_are_counted_not_zeroed():
    r = v.performance([], [("a", None), ("b", 100)], 100)
    assert r["missing_equity_marks"] == 1 and r["max_drawdown_pct"] == 0


def test_version_changes_with_settings_and_excludes_local_paths():
    a = Config(state_dir="/one"); b = deepcopy(a); b.state_dir = "/two"
    assert v.identity(a)["id"] == v.identity(b)["id"]
    b.stop_loss_pct -= .01
    assert v.identity(a)["id"] != v.identity(b)["id"]


def test_input_digest_changes_and_retains_actual_rows():
    frame = pd.DataFrame({"Close": [1, 2]}, index=pd.date_range("2026-11-16", periods=2))
    a = v.frame_data(frame); frame.iloc[-1, 0] = 3
    b = v.frame_data(frame)
    assert a["sha256"] != b["sha256"] and a["rows"][1][1] == 2
    assert v.frame_data(None)["status"] == "unknown"


def test_unversioned_cache_never_overwritten(tmp_path):
    out = tmp_path / "result.json"; out.write_text('{"historical":true}')
    with pytest.raises(ValueError, match="Unversioned"):
        v.guard_research_output(out, root=tmp_path)
    assert out.read_text() == '{"historical":true}'


@pytest.mark.parametrize("changed", ["code", "data", "parameters"])
def test_cache_rejects_each_identity_change(tmp_path, changed):
    (tmp_path / "trading").mkdir(); (tmp_path / "data").mkdir()
    code = tmp_path / "trading/a.py"; code.write_text("x=1")
    data = tmp_path / "data/a.csv"; data.write_text("a\n1")
    out = tmp_path / "result.json"
    v.guard_research_output(out, root=tmp_path)
    out.write_text("{}")
    v.guard_research_output(out, root=tmp_path)
    params = {}
    if changed == "code": code.write_text("x=2")
    if changed == "data": data.write_text("a\n2")
    if changed == "parameters": params = {"floor": 99}
    with pytest.raises(ValueError, match="identity mismatch"):
        v.guard_research_output(out, root=tmp_path, parameters=params)
    assert out.read_text() == "{}"


def test_forward_baseline_does_not_import_old_trades(tmp_path):
    p = Portfolio(cfg=Config(state_dir=str(tmp_path)), cash=100)
    fv.stage(p, datetime(2026, 11, 17, tzinfo=timezone.utc))
    events = list(p.validation_pending.values())
    assert [x["kind"] for x in events] == ["baseline", "book"]
    assert fv.report(events)["cohorts"][0]["closed_trades"] == 0


def test_forward_outbox_survives_failed_export_and_restart(tmp_path):
    cfg = Config(state_dir=str(tmp_path)); p = Portfolio(cfg=cfg, cash=100)
    fv.stage(p); before = deepcopy(p.validation_pending)
    assert p.save()
    fv.flush(p, lambda e: False)
    assert p.validation_pending == before
    restored = Portfolio.load_or_fresh(cfg)
    assert restored.validation_pending == before
    exported = []
    fv.flush(restored, lambda e: exported.append(e) or True)
    assert not restored.validation_pending and len(exported) == len(before)
    fv.stage(restored)
    assert not restored.validation_pending


def test_forward_separates_versions_and_partial_quantities():
    run = v.identity(Config())
    other = v.identity(Config(slippage_bps=30))
    events = [dict(kind="entry", path="scan", run=run, trade_id="a", position={"qty": 2}),
              dict(kind="exit", run=run, trade_id="a", trade=leg(qty=1), exit_code_sha256=run["code"]["sha256"]),
              dict(kind="baseline", run=other)]
    r = fv.report(events)
    assert len(r["cohorts"]) == 2
    assert r["cohorts"][0]["net_expectancy"] is None
    events.append(dict(kind="exit", run=run, trade_id="a", trade=leg(qty=1)))
    r = fv.report(events)
    assert r["cohorts"][0]["net_expectancy"] == -20
    assert r["cohorts"][0]["mixed_exit_code"] == 1
    assert r["cohorts"][0]["max_drawdown_pct"] is None


def test_actual_replay_inputs_reject_future_daily_and_unfinished_intraday():
    from research.combined_2026_11_17.replay import Inputs
    daily = pd.DataFrame({"Close": [100, 9999]}, index=pd.to_datetime(["2026-11-16", "2026-11-17"]))
    intra = pd.DataFrame({"Open": [100, 101, 9999], "High": [101, 102, 10000],
        "Low": [99, 100, 9998], "Close": [100, 101, 9999], "Volume": [100, 100, 100]},
        index=pd.to_datetime(["2026-11-17T09:15:00+05:30", "2026-11-17T09:20:00+05:30", "2026-11-17T09:25:00+05:30"]))
    x = Inputs({"A.NS": daily}, {"A.NS": intra})
    x.now = pd.Timestamp("2026-11-17T09:20:00+05:30")
    assert x.fetch_daily(["A.NS"])["A.NS"]["Close"].tolist() == [100]
    assert x.quotes(["A.NS"]) == {"A.NS": 101}
    frames, details = x.fetch_intraday(["A.NS"], Config())
    assert not frames and details["A.NS"]["rows"] == 1
    assert details["A.NS"]["last_bar"].startswith("2026-11-17T03:45")


def test_replay_schedule_matches_actual_daily_rotation_and_square_off():
    from research.combined_2026_11_17.replay import schedule
    events = [(d.strftime("%H:%M"), action) for d, action in schedule("2026-11-16")]
    assert [(ts, a) for ts, a in events if a == "scan"] == [("09:20", "scan"), ("14:30", "scan")]
    assert ("15:25", "square_off") in events
    assert ("09:00", "refresh") not in events


def test_actual_engine_runs_offline_with_production_scores_and_gates():
    from research.combined_2026_11_17.replay import run
    import numpy as np
    dates = pd.bdate_range("2025-04-01", "2026-11-13")
    prices = 100 * np.exp(np.arange(len(dates)) * .001)
    frame = pd.DataFrame({"Open": prices, "High": prices * 1.01, "Low": prices * .99,
                          "Close": prices, "Volume": 1e7}, index=dates)
    # No historical intraday provider => no executable prices, never fake buys.
    out = run({"ABB.NS": frame, "^NSEI": frame, "^INDIAVIX": frame}, {}, {},
              {"symbols": ["ABB.NS"], "start": "2026-11-16", "end": "2026-11-16"}, 5)
    assert len(out["decisions"]) == 10
    assert out["decisions"][0]["ranked"]
    assert not out["trades"] and not out["summary"]["open_positions"]
    assert out["summary"]["net_expectancy"] is None
    assert out["summary"]["coverage"].get("quotes_supplied", 0) == 0


def test_actual_engine_can_buy_and_exit_with_causal_execution_prices():
    from research.combined_2026_11_17.replay import run
    from trading.calendars import is_trading_day
    import numpy as np
    dates = pd.bdate_range("2025-04-01", "2026-11-16")
    prices = 100 * np.exp(np.arange(len(dates)) * .002)
    volume = np.ones(len(dates)) * 1e7
    volume[-6:] *= 5
    daily = pd.DataFrame({"Open": prices, "High": prices * 1.001, "Low": prices * .999,
                          "Close": prices, "Volume": volume}, index=dates)
    indices = [pd.Timestamp(str(day.date()) + " 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=5*i)
               for day in pd.date_range("2026-10-26", "2026-11-16") if is_trading_day(day.date()) for i in range(75)]
    last = float(daily.loc[:"2026-11-13", "Close"].iloc[-1])
    intraday = pd.DataFrame({"Open": last, "High": last*1.001, "Low": last*.999,
                             "Close": last, "Volume": 1e6}, index=pd.DatetimeIndex(indices).tz_convert("UTC"))
    # A large drop occurs only after the morning scan. It must be observed by
    # the real refresh/stop/cost path, not by a fabricated stop-price fill.
    intraday.loc[intraday.index >= pd.Timestamp("2026-11-16T05:00:00Z"), ["Open", "High", "Low", "Close"]] *= .80
    out = run({"ABB.NS": daily, "^NSEI": daily, "^INDIAVIX": daily*.05},
              {"ABB.NS": intraday, "^NSEI": intraday}, {"breadth_gate_enabled": False},
              {"symbols": ["ABB.NS"], "start": "2026-11-16", "end": "2026-11-16"}, 5)
    assert any(d["opened"] for d in out["decisions"])
    assert out["trades"]
    assert all(t["costs"] > 0 for t in out["trades"])
    assert out["summary"]["net_expectancy"] < 0


def test_forward_does_not_count_historical_or_future_equity_as_new_sessions():
    p = Portfolio(cfg=Config(), cash=100.)
    p.equity_history = [("2026-11-16T10:00:00", 100)]
    fv.stage(p, datetime(2026, 11, 17, 6, tzinfo=timezone.utc))
    assert not any(e["kind"] == "equity" for e in p.validation_pending.values())

    p.equity_history.append(("2026-11-18T10:00:00", 100))
    fv.stage(p, datetime(2026, 11, 17, 6, tzinfo=timezone.utc))
    assert not any(e["kind"] == "equity" for e in p.validation_pending.values())


def test_daily_backtest_routes_live_sizing_cap_and_category(monkeypatch):
    from trading import backtest as bt
    from trading.engine import TradingEngine
    cfg = Config(mode="nifty200_momentum")
    cfg.apply_mode_preset()
    dates = pd.bdate_range("2025-01-01", "2026-11-16")
    frame = pd.DataFrame({"Close": range(100, 100+len(dates)), "Open": 600.,
                          "High": 601., "Low": 599., "Volume": 1e7}, index=dates)
    p = Portfolio(cfg=cfg, cash=cfg.starting_capital)
    monkeypatch.setattr(bt, "_score_for_mode", lambda *a: (90, {}))
    monkeypatch.setattr(bt, "timing_ok", lambda *a: (True, {}))
    monkeypatch.setattr("trading.sizing.position_notional_for", lambda *a, **k: (600000, {"mode": "slab"}))
    monkeypatch.setattr(TradingEngine, "_measured_kelly", lambda *a: SimpleNamespace(status="ACTIVE", fraction=.01, block_new_entries=False))
    calls = []
    monkeypatch.setattr(bt, "can_open", lambda *a, **k: calls.append(k) or (False, "test", 0))
    from trading.risk import RiskState
    bt._try_open_positions(cfg, p, RiskState(cfg, cfg.starting_capital), {"ABB.NS": frame.iloc[:-1]},
                           None, {"ABB.NS": frame}, dates[-1])
    assert len(calls) == 1
    assert calls[0]["notional_override"] == 500000
    assert calls[0]["category"] == cfg.mode
    assert calls[0]["proposed_stop_price"] > 0


def test_daily_backtest_final_curve_includes_real_liquidation_fees(monkeypatch):
    from trading import backtest as bt, fees
    cfg = Config(custom_universe=("ABB.NS",), mode="swing")
    dates = pd.bdate_range("2026-01-01", "2026-11-16")
    frame = pd.DataFrame({"Open": 100., "High": 100., "Low": 100., "Close": 100., "Volume": 1e7}, index=dates)
    def one(c, p, risk, hist, bench, all_hist, next_date):
        if p.positions: return
        basis = fees.new_basis(100, 100., "swing", next_date, "ABB.NS")
        p.cash -= 10000 + basis["orders"][0]["fee"]["total"]
        p.positions["ABB.NS"] = PaperPosition("ABB.NS", 100, 100., next_date.to_pydatetime(),
            "Unknown", 100., 80., "test", mode="swing", cost_basis=basis)
    monkeypatch.setattr(bt, "_try_open_positions", one)
    monkeypatch.setattr(bt, "check_exit", lambda *a, **k: (False, ""))
    r = bt.run_backtest(cfg, "2026-11-13", "2026-11-16", universe=["ABB.NS"],
                        preloaded_history={"ABB.NS": frame, "^NSEI": frame})
    assert r.num_trades == 1
    assert r.equity_curve[-1][1] == r.final_equity < cfg.starting_capital
    assert r.max_drawdown_pct < 0
