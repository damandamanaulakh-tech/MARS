"""Entry-path fix batch, builder A1 — money gates (owner order 2026-10-22:
"review and find flaws, fix them for app not buying or gates which are
halting"; owner cards D1, D3, D4 and the review's confirmed findings).

A1-1  The scan's daily-loss check valued held names with no quote at their
      ENTRY price against a baseline marked at the latest close — a false
      -2% halt stopped all five categories. It reads the freshest marks now.
A1-2  D1 "Block only on 30+ usable trades": a negative measured-Kelly reading
      on a thin record CAPS each momentum buy at the slab, never blocks.
A1-3  D3 "Switch the spread check off": only the Rs 5 Cr turnover rule gates
      liquidity; the scan no longer reads the bid-ask at all.
A1-4  D4 "Shares from what is left after owner + Ocean".
A1-5  CATEGORY_BUDGET_FULL prints the limit the gate refused on.
A1-6  XR-026 heat gate counts a new entry at its own stop; XR-051 the
      correlation gate sees the names cleared earlier in the same pass.

Every behavioural test here fails on main bdc82ec (checked against an
exported copy of that commit).
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from trading import category_budget as cb
from trading import measured_kelly as MK
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import ClosedTrade, PaperPosition, Portfolio
from trading.risk import RiskState, can_open
from trading.risk_v2 import passes_liquidity

MOM = "nifty200_momentum"
CR = 1_00_00_000.0
LAKH = 1_00_000.0


# ------------------------------------------------------------------ fixtures

def _frame(n=300, start=100.0, drift=0.15, seed=1, end=None, flat=None):
    rng = np.random.default_rng(seed)
    if flat is not None:
        close = np.full(n, float(flat))
    else:
        close = start * np.cumprod(1 + drift / n + rng.normal(0, 0.004, n))
    idx = pd.bdate_range(end=end or pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01,
                         "Low": close * 0.99, "Close": close,
                         "Volume": rng.integers(5e6, 1e7, n).astype(float)},
                        index=idx)


def _yday():
    return pd.Timestamp.today().normalize() - pd.offsets.BDay(1)


def _pos(ticker, qty, entry=100.0, mode=MOM, tags=None, stop=None, target=None):
    return PaperPosition(
        ticker=ticker, qty=int(qty), entry_price=float(entry),
        entry_date=datetime(2026, 8, 1), sector="IT", peak_price=float(entry),
        entry_score=70.0, reasoning="fixture", mode=mode,
        stop_price=float(stop if stop is not None else entry * 0.95),
        target_price=float(target if target is not None else entry * 10),
        tags=list(tags or []))


def _engine(tmp_path, monkeypatch, *, picks=("UPP.NS",), frames=None,
            quotes=None, positions=None, cash=5 * CR, trades=()):
    """A momentum-pass engine with the network and the clock stubbed. Each
    pick gets a strong rising frame unless `frames` names its own."""
    cfg = Config(state_dir=str(tmp_path), mode=MOM)
    cfg.apply_mode_preset()
    cfg.candle_confirm_enabled = False
    cfg.custom_universe = tuple(picks)
    p = Portfolio.fresh(cfg)
    p.cash = float(cash)
    p.positions.clear()
    p.positions.update(positions or {})
    p.trades = list(trades)
    e = TradingEngine(cfg, p)
    fr = {t: _frame(drift=0.60, seed=2) for t in picks}
    fr.update({cfg.benchmark: _frame(drift=0.05, seed=4),
               "^INDIAVIX": _frame(start=12.0, drift=0.0, seed=5)})
    fr.update(frames or {})

    def fake_fetch_daily(tickers, period="6mo"):
        if isinstance(tickers, str):
            tickers = [tickers]
        return {t: fr[t].copy() for t in tickers if t in fr}

    from trading.regime import RegimeReading

    def fake_regime(*a, **k):
        return RegimeReading(
            timestamp=datetime.utcnow(), nifty_close=100.0, nifty_200dma=90.0,
            pct_below_52w_high=2.0, vix=12.0, fii_5d_net=100.0,
            regime="bull_extension", everest_active=True, ocean_active=False,
            sizing_multiplier_everest=1.0, sizing_multiplier_ocean=0.0,
            reasoning="test fixture")
    monkeypatch.setattr("trading.regime.fetch_regime", fake_regime)
    monkeypatch.setattr("trading.calendars.is_system_event_within",
                        lambda *a, **k: None)
    monkeypatch.setattr("trading.engine.fetch_daily", fake_fetch_daily)
    q = {t: float(f["Close"].iloc[-1]) for t, f in fr.items()}
    if quotes is not None:
        # held names answer only what the test says; picks always answer
        for t in (positions or {}):
            q.pop(t, None)
        q.update({k: v for k, v in quotes.items() if v is not None})
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda tickers, upstox_client=None:
                        {t: q[t] for t in tickers if t in q})
    monkeypatch.setattr(e, "_trading_allowed", lambda now=None: True)
    monkeypatch.setattr(e, "_breadth_gate_blocks", lambda *a, **k: False)
    return e


def _journal(e, action):
    return [x for x in e.portfolio.decision_journal if x.get("action") == action]


def _reasons(e):
    return {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}


def _trade(i, ret, reason="STOP_LOSS"):
    entry = 100.0
    exit_ = entry * (1 + ret)
    return ClosedTrade(
        ticker=f"T{i}.NS", qty=100, entry_date=datetime(2026, 9, 20),
        exit_date=datetime(2026, 10, 1), entry_price=entry, exit_price=exit_,
        gross_pnl=(exit_ - entry) * 100, costs=0.0,
        net_pnl=(exit_ - entry) * 100, reason=reason, sector="IT",
        entry_score=70.0, reasoning="fixture", mode=MOM)


def _record(n_pairs):
    """n_pairs x (one +3% early win, one -10% stop): a negative Kelly."""
    out = []
    for i in range(n_pairs):
        out.append(_trade(2 * i, 0.03, "TAKE_PROFIT"))
        out.append(_trade(2 * i + 1, -0.10))
    return out


# ============================================================ A1-1 kill switch

def _halt_book(tmp_path, monkeypatch, lose_quote):
    """Two winners with NO quote (stored close 130, entry 100) and one
    quoted loser. Yesterday's equity was marked at the stored closes."""
    positions = {"WIN1.NS": _pos("WIN1.NS", 1000),
                 "WIN2.NS": _pos("WIN2.NS", 1000),
                 "LOSE.NS": _pos("LOSE.NS", 1000)}
    frames = {"WIN1.NS": _frame(flat=130.0, end=_yday()),
              "WIN2.NS": _frame(flat=130.0, end=_yday()),
              "LOSE.NS": _frame(flat=100.0, end=_yday())}
    e = _engine(tmp_path, monkeypatch, picks=("UPP.NS",), frames=frames,
                quotes={"LOSE.NS": lose_quote}, positions=positions,
                cash=10_000.0)
    e.cfg.custom_universe = ("UPP.NS",)
    # yesterday's last mark: cash + 2 x 1000 x 130 + 1000 x 100
    e.portfolio.equity_history = [("2026-10-20T10:00:00", 370_000.0)]
    return e


def test_unquoted_winners_do_not_trip_a_false_daily_halt(tmp_path, monkeypatch):
    """Old code: 10,000 + 2 x 100,000 (entry) + 99,000 = 309,000 against a
    370,000 baseline = -16.5% -> KILL_SWITCH_SCAN, every category stopped.
    On the freshest marks it is 369,000 = -0.3%: no halt."""
    e = _halt_book(tmp_path, monkeypatch, lose_quote=99.0)
    e.scan()
    assert not e.portfolio.risk_day_killed
    assert not e.risk.kill_switch
    assert not _journal(e, "KILL_SWITCH_SCAN")
    # Rs 10,000 cash buys nothing either way — but not for a halt
    assert _reasons(e).get("UPP.NS") != "kill_switch_active"


def test_a_real_loss_on_fresh_marks_still_trips_the_halt(tmp_path, monkeypatch):
    """The quoted loser falls 40%: 10,000 + 260,000 + 60,000 = 330,000 =
    -10.8% on the same freshest marks -> the halt trips as it should."""
    e = _halt_book(tmp_path, monkeypatch, lose_quote=60.0)
    e.scan()
    assert e.portfolio.risk_day_killed
    assert _journal(e, "KILL_SWITCH_SCAN")
    assert "UPP.NS" not in e.portfolio.positions
    assert _reasons(e).get("UPP.NS") == "kill_switch_active"


def test_no_quotes_at_all_still_leaves_the_check_to_refresh(tmp_path, monkeypatch):
    e = _halt_book(tmp_path, monkeypatch, lose_quote=None)
    e.portfolio.equity_history = [("2026-10-20T10:00:00", 10_000_000.0)]
    e.scan()
    assert not _journal(e, "KILL_SWITCH_SCAN")


# ============================================================== A1-2 Kelly

def test_a_two_trade_negative_record_caps_and_does_not_block():
    """One +3% win, one -10% stop: f is negative. Owner card 2026-10-22 —
    under 30 usable exits that CAPS at the slab, it does not block."""
    r = MK.measure(_record(1), fraction=0.29, category=MOM, clip_pct=20.0)
    assert r.usable == 2 and r.raw_kelly < 0
    assert r.status == "NO_POSITIVE_EDGE"
    assert r.block_new_entries is False
    assert r.thin_record is True
    assert r.min_block_trades == 30
    assert "capped at the slab, not blocked" in r.note
    assert "2 of 30" in r.note


def test_forty_trades_with_the_same_ratios_still_block():
    r = MK.measure(_record(20), fraction=0.29, category=MOM, clip_pct=20.0)
    assert r.usable == 40 and r.status == "NO_POSITIVE_EDGE"
    assert r.block_new_entries is True
    assert r.thin_record is False
    assert "blocked" in r.note


def test_the_block_line_is_exactly_thirty():
    r29 = MK.measure(_record(14) + [_trade(99, -0.10)], category=MOM)
    r30 = MK.measure(_record(15), category=MOM)
    assert r29.usable == 29 and r29.thin_record and not r29.block_new_entries
    assert r30.usable == 30 and r30.block_new_entries and not r30.thin_record


def test_thin_record_is_only_a_negative_reading():
    """A positive thin reading is ACTIVE (the confidence ramp shrinks it),
    never 'thin' — the flag means 'would have blocked, caps instead'."""
    good = [_trade(i, 0.20, "TAKE_PROFIT") for i in range(3)] + [
        _trade(10 + i, -0.05) for i in range(2)]
    r = MK.measure(good, category=MOM)
    assert r.status == "ACTIVE" and r.thin_record is False


def test_the_minimum_is_a_recorded_assumption():
    a = MK.ASSUMED["MIN_BLOCK_TRADES"]
    assert a["value"] == MK.MIN_BLOCK_TRADES == 30
    assert "2026-10-22" in a["source"]


def test_a_thin_record_still_buys_at_the_slab(tmp_path, monkeypatch):
    """Old code refused UPP.NS 'measured_kelly_no_edge' on two trades."""
    e = _engine(tmp_path, monkeypatch, trades=_record(1))
    e.scan()
    assert "UPP.NS" in e.portfolio.positions, _reasons(e)
    pos = e.portfolio.positions["UPP.NS"]
    slab = e.cfg.position_slab_value
    spent = pos.qty * pos.entry_price
    assert slab <= spent < slab + pos.entry_price * 1.01
    buy = _journal(e, "BUY")[-1]
    mk = buy["sizing"]["measured_kelly"]
    assert mk["thin_record"] is True and mk["cap"] == pytest.approx(slab)
    assert mk["status"] == "NO_POSITIVE_EDGE" and mk["capped"] is True
    assert not _journal(e, "MEASURED_KELLY_BLOCKED")
    thin = _journal(e, "MEASURED_KELLY_THIN_CAP")
    assert len(thin) == 1 and thin[0]["usable"] == 2
    assert thin[0]["cap"] == pytest.approx(slab)


def test_forty_losing_trades_still_block_the_momentum_scan(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, trades=_record(20))
    e.scan()
    assert "UPP.NS" not in e.portfolio.positions
    assert _reasons(e).get("UPP.NS") == "measured_kelly_no_edge"
    assert _journal(e, "MEASURED_KELLY_BLOCKED")
    assert not _journal(e, "MEASURED_KELLY_THIN_CAP")


def test_the_thin_cap_is_the_slab_and_nothing_else():
    cfg = Config(state_dir="/tmp/sb_a1_cap", mode=MOM)
    cfg.apply_mode_preset()
    thin = MK.measure(_record(1), category=MOM)
    assert TradingEngine._measured_kelly_cap(thin, cfg, 5 * CR) == \
        cfg.position_slab_value
    full = MK.measure(_record(20), category=MOM)
    assert TradingEngine._measured_kelly_cap(full, cfg, 5 * CR) is None


# ============================================================ A1-3 spread off

def test_a_wide_spread_no_longer_fails_liquidity():
    df = _frame()
    for spread in (0.0008, 0.005, 0.05):
        ok, reason = passes_liquidity(df["Close"], df["Volume"],
                                      spread_pct=spread)
        assert ok is True and "spread" not in reason


def test_the_scan_never_reads_the_bid_ask(tmp_path, monkeypatch):
    """Logged in, a 5% spread: old code refused 'liquidity_spread ...'."""
    class Broker:
        calls = 0

        def bid_ask(self, ticker):
            Broker.calls += 1
            return 95.0, 105.0
    e = _engine(tmp_path, monkeypatch)
    e.broker = Broker()
    e.scan()
    assert Broker.calls == 0
    assert "UPP.NS" in e.portfolio.positions, _reasons(e)
    assert not any("spread" in str(s.get("reason", ""))
                   for s in e.portfolio.last_scan_skipped)


# ======================================================== A1-4 share base

def _cfg0():
    cfg = Config(state_dir="/tmp/sb_a1_share", mode=MOM)
    cfg.apply_mode_preset()
    cfg.cash_reserve_pct = 0.0
    return cfg


def test_shares_are_cut_from_what_is_left_after_owner_and_ocean():
    cfg = _cfg0()
    book = 5 * CR
    owner = {"OWN.NS": _pos("OWN.NS", CR / 100, tags=["owner_pick", "buy_desk"])}
    ocean = {"OCN.NS": _pos("OCN.NS", CR / 100, tags=["ocean"])}
    # owner Rs 1 Cr: momentum = 40% of Rs 4 Cr
    assert cb.share_base(cfg, owner, book) == pytest.approx(4 * CR)
    assert cb.limit(cfg, MOM, book, positions=owner) == pytest.approx(0.40 * 4 * CR)
    assert cb.room(cfg, owner, MOM, book) == pytest.approx(0.40 * 4 * CR)
    # owner Rs 1 Cr + Ocean Rs 1 Cr: momentum = 40% of Rs 3 Cr
    both = {**owner, **ocean}
    assert cb.room(cfg, both, MOM, book) == pytest.approx(0.40 * 3 * CR)
    assert cb.room(cfg, both, "swing", book) == pytest.approx(0.15 * 3 * CR)
    parts = cb.share_base_parts(cfg, both, book)
    assert parts == pytest.approx({"deployable": 5 * CR, "owner": CR,
                                   "ocean": CR, "base": 3 * CR})
    rows = {r["category"]: r for r in cb.usage(cfg, both, book)}
    assert rows[MOM]["limit"] == pytest.approx(0.40 * 3 * CR)
    assert rows[MOM]["share_base"] == pytest.approx(3 * CR)
    # the five shares now add up to exactly what is left
    assert sum(r["limit"] for r in rows.values()) == pytest.approx(3 * CR)


def test_with_no_owner_or_ocean_holdings_the_old_numbers_hold():
    cfg = _cfg0()
    book = 5 * CR
    scan = {"S1.NS": _pos("S1.NS", 50 * LAKH / 100),
            "S2.NS": _pos("S2.NS", 20 * LAKH / 100, mode="swing")}
    assert cb.share_base(cfg, scan, book) == pytest.approx(cb.deployable(cfg, book))
    for cat in cb.SHARE_FIELDS:
        assert cb.limit(cfg, cat, book, positions=scan) == pytest.approx(
            cb.limit(cfg, cat, book))
    cfg.cash_reserve_pct = 0.20
    assert cb.room(cfg, scan, MOM, book) == pytest.approx(
        0.40 * 0.80 * book - 50 * LAKH)


def test_the_gate_refuses_on_the_share_of_what_is_left():
    """Owner bucket Rs 3 Cr, momentum already holds Rs 90 L. Old gate: 40%
    of Rs 5 Cr = Rs 2 Cr, room Rs 1.1 Cr -> bought. Now: 40% of the Rs 2 Cr
    left = Rs 80 L, already over -> category_budget_full."""
    cfg = _cfg0()
    book = 5 * CR
    held = {"OWN.NS": _pos("OWN.NS", 3 * CR / 100, tags=["owner_pick"]),
            "MOM.NS": _pos("MOM.NS", 90 * LAKH / 100)}
    risk = RiskState(cfg=cfg, starting_capital=book)
    ok, why, _ = can_open(risk, book - 3 * CR - 90 * LAKH, book, held,
                          "NEW.NS", 100.0, cfg, notional_override=1.5 * LAKH,
                          category=MOM)
    assert not ok and why == "category_budget_full"
    # swing still has room: 15% of Rs 2 Cr = Rs 30 L
    ok, why, qty = can_open(risk, book - 3 * CR - 90 * LAKH, book, held,
                            "NEW.NS", 100.0, cfg, notional_override=1.5 * LAKH,
                            category="swing")
    assert ok, why


def _code(fn) -> str:
    src = inspect.getsource(fn)
    return "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))


def test_rotation_reads_the_same_share_base_as_the_gate():
    assert "cb.limit(cfg, cat, eq, positions=held)" in _code(
        TradingEngine._rotation_exits)


# ===================================================== A1-5 journal limit

def _full_momentum(tmp_path, monkeypatch, owner_rs=0.0):
    """Momentum holds Rs 18 L at entry, quoted +50% (Rs 27 L marked); cash
    Rs 20 L; optional owner bucket. The share is spent either way."""
    positions = {"H1.NS": _pos("H1.NS", 18_000, entry=100.0)}
    quotes = {"H1.NS": 150.0}
    if owner_rs:
        positions["OWN.NS"] = _pos("OWN.NS", owner_rs / 100, tags=["owner_pick"])
        quotes["OWN.NS"] = 100.0
    e = _engine(tmp_path, monkeypatch, positions=positions, quotes=quotes,
                cash=20 * LAKH)
    e.cfg.cash_reserve_pct = 0.0
    return e


def test_the_budget_full_line_prints_the_gates_own_limit(tmp_path, monkeypatch):
    """Marked equity 47 L -> the gate's limit 40% = 18.8 L (room 0.8 L).
    Old journal recomputed on cash + ENTRY cost (38 L) and printed 15.2 L."""
    e = _full_momentum(tmp_path, monkeypatch)
    e.scan()
    assert _reasons(e).get("UPP.NS") == "category_budget_full"
    line = _journal(e, "CATEGORY_BUDGET_FULL")[-1]
    assert line["limit"] == pytest.approx(0.40 * 47 * LAKH, abs=1)
    assert line["used"] == pytest.approx(18 * LAKH, abs=1)
    assert "Rs 1,880,000" in line["why"]


def test_the_budget_full_line_names_the_owner_bucket(tmp_path, monkeypatch):
    e = _full_momentum(tmp_path, monkeypatch, owner_rs=10 * LAKH)
    e.scan()
    line = _journal(e, "CATEGORY_BUDGET_FULL")[-1]
    # marked equity 57 L, owner 10 L -> base 47 L, limit 18.8 L
    assert line["share_base"] == pytest.approx(47 * LAKH, abs=1)
    assert line["owner_bucket"] == pytest.approx(10 * LAKH, abs=1)
    assert line["limit"] == pytest.approx(0.40 * 47 * LAKH, abs=1)
    assert "left after your own buys" in line["why"]


# ================================================= A1-6 heat + correlation

def test_heat_counts_a_new_entry_at_its_own_stop():
    """Cap 5% of Rs 1 Cr = Rs 5 L at stop. A Rs 50 L entry at a 15% ATR stop
    is Rs 7.5 L of heat -> trimmed to Rs 5 L / 15% = Rs 33.3 L. At the flat
    5% it read Rs 2.5 L and went through whole."""
    cfg = _cfg0()
    cfg.max_portfolio_heat_pct = 0.05
    cfg.max_position_pct = 1.0
    risk = RiskState(cfg=cfg, starting_capital=CR)
    flat = can_open(risk, CR, CR, {}, "NEW.NS", 100.0, cfg,
                    notional_override=50 * LAKH)
    own = can_open(risk, CR, CR, {}, "NEW.NS", 100.0, cfg,
                   notional_override=50 * LAKH, stop_frac=0.15)
    assert flat[0] and flat[2] * 100.0 == pytest.approx(50 * LAKH)
    assert own[0] and own[2] * 100.0 == pytest.approx(5 * LAKH / 0.15, abs=100)


def test_the_engine_hands_the_gate_the_stop_it_will_set(tmp_path, monkeypatch):
    from trading import engine as E
    seen = []
    real = E.can_open

    def spy(*a, **k):
        seen.append(k.get("stop_frac"))
        return real(*a, **k)
    monkeypatch.setattr(E, "can_open", spy)
    e = _engine(tmp_path, monkeypatch)
    e.scan()
    pos = e.portfolio.positions["UPP.NS"]
    assert seen and seen[-1] is not None
    assert seen[-1] == pytest.approx(
        (pos.entry_price - pos.stop_price) / pos.entry_price, abs=1e-3)


def test_the_correlation_gate_sees_names_cleared_in_the_same_pass(tmp_path,
                                                                 monkeypatch):
    """Two picks moving identically: the old set was built once from the
    book held before the pass, so both opened."""
    twin = _frame(drift=0.60, seed=2)
    e = _engine(tmp_path, monkeypatch, picks=("UPP.NS", "TWIN.NS"),
                frames={"UPP.NS": twin, "TWIN.NS": twin.copy()})
    e.scan()
    opened = [t for t in ("UPP.NS", "TWIN.NS") if t in e.portfolio.positions]
    assert len(opened) == 1, opened
    other = ({"UPP.NS", "TWIN.NS"} - set(opened)).pop()
    assert _reasons(e)[other].startswith(f"correlated_{opened[0]}_")


def test_two_uncorrelated_picks_both_still_open(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, picks=("UPP.NS", "OTH.NS"),
                frames={"UPP.NS": _frame(drift=0.60, seed=2),
                        "OTH.NS": _frame(drift=0.60, seed=11)})
    e.scan()
    assert {"UPP.NS", "OTH.NS"} <= set(e.portfolio.positions), _reasons(e)
