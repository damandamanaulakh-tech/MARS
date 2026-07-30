"""LENS V2 — the growth-first lens (owner ask 2026-07-30: "v1 for current,
v2 for what we r mixing", and "in Universe too u should focus on same").

v2 is READ-ONLY research maths. These tests pin the maths, the honesty
(DATA_NEEDED over guesses), the defensive trap detector, and — most
important — that the live engine can never be driven by it.
"""

import math

import pandas as pd
import pytest

from trading import lens_v2 as L


def _series(n=300, start=100.0, daily=0.001, vol=0.0):
    """Deterministic price path; vol adds a repeatable zig-zag."""
    idx = pd.bdate_range("2025-01-01", periods=n)
    px, out = start, []
    for i in range(n):
        px *= (1.0 + daily + (vol if i % 2 else -vol))
        out.append(px)
    return pd.Series(out, index=idx)


def _df(close: pd.Series, volume=1e6, high_mult=1.01, low_mult=0.99):
    return pd.DataFrame({
        "Open": close.shift(1).fillna(close.iloc[0]),
        "High": close * high_mult,
        "Low": close * low_mult,
        "Close": close,
        "Volume": pd.Series(volume, index=close.index),
    })


# ---- the maths -----------------------------------------------------------

def test_annual_vol_scales_daily_moves_by_root_252():
    c = _series(300, daily=0.0, vol=0.01)
    v = L.annual_vol(c)
    assert 0.10 < v < 0.30                      # ~1%/day zig-zag annualised
    assert L.annual_vol(_series(10)) == 0.0     # too short = 0, never a guess


def test_edge_estimate_is_grinold_ic_sigma_z():
    """alpha = IC * sigma * z, with z measured off the entry floor."""
    c = _series(300, daily=0.0, vol=0.01)
    sig = L.annual_vol(c)
    mu, sigma = L.edge_estimate(85.0, c, ic=0.05, floor=62.0)
    assert sigma == pytest.approx(sig)
    assert mu == pytest.approx(0.05 * sig * (85.0 - 62.0) / 30.0)
    # a score AT the floor carries no edge; below it, negative
    assert L.edge_estimate(62.0, c, floor=62.0)[0] == pytest.approx(0.0)
    assert L.edge_estimate(40.0, c, floor=62.0)[0] < 0


def test_kelly_is_mu_over_variance_fractional_and_capped():
    """f = fraction * mu / sigma^2, hard-capped as a share of equity."""
    # un-capped region: exactly fraction * mu / sigma^2
    assert L.kelly_fraction(0.02, 0.40, fraction=0.25, cap=1.0) == \
        pytest.approx(0.25 * 0.02 / 0.16)
    assert L.kelly_fraction(0.02, 0.40, fraction=0.5, cap=1.0) == \
        pytest.approx(0.5 * 0.02 / 0.16)
    # a big edge is clamped by the cap, not left to run
    assert L.kelly_fraction(0.10, 0.20, fraction=0.5, cap=0.05) == \
        pytest.approx(0.05)
    # no edge or no vol = no bet
    assert L.kelly_fraction(-0.05, 0.2) == 0.0
    assert L.kelly_fraction(0.05, 0.0) == 0.0


def test_shared_defaults_are_the_live_engines():
    """lens_v2 re-exports the ENGINE's maths (trading/sizing.py), so the
    displayed lens can never drift from the sizing that actually trades."""
    from trading import sizing
    assert L.annual_vol is sizing.annual_vol
    assert L.edge_estimate is sizing.edge_estimate
    assert L.kelly_fraction is sizing.kelly_fraction


def test_convexity_payoff_and_breakeven_hit_rate():
    """R = room-to-high / stop distance; breakeven win rate = 1/(1+R)."""
    c = _series(300, daily=0.002)                        # rising, so last == high
    d = _df(c)
    cx = L.convexity(d, stop_pct=0.05)
    assert cx["atr"] > 0
    assert cx["payoff_R"] == 0.0                         # at the high: no room
    assert cx["breakeven_hit"] == 1.0                    # so it needs 100% — refuse
    # pull price 10% off the high. The stop is measured off the CURRENT price,
    # so R = 0.10*hi / (0.90*hi * 0.05) = 2.22, breakeven 1/(1+2.22) = 0.31.
    c2 = pd.concat([c, pd.Series(
        [float(c.iloc[-1]) * 0.90], index=pd.bdate_range("2026-06-01", periods=1))])
    cx2 = L.convexity(_df(c2), stop_pct=0.05)
    assert cx2["payoff_R"] == pytest.approx(0.10 / (0.90 * 0.05), abs=0.05)
    assert cx2["breakeven_hit"] == pytest.approx(1 / (1 + 2.222), abs=0.02)


def test_impact_follows_the_square_root_law():
    """4x the size = 2x the impact. This is the wall that stops scaling."""
    d = _df(_series(300, daily=0.0, vol=0.01), volume=1e6)
    i1 = L.impact_bps(d, 10_00_000)
    i4 = L.impact_bps(d, 40_00_000)
    assert i1 > 0
    assert i4 / i1 == pytest.approx(2.0, abs=0.01)


def test_capacity_notional_inverts_the_impact_law():
    d = _df(_series(300, daily=0.0, vol=0.01), volume=1e6)
    q = L.capacity_notional(d, max_impact_bps=25.0)
    assert q > 0
    assert L.impact_bps(d, q) == pytest.approx(25.0, abs=0.5)


def test_v2_view_sizes_by_edge_and_reports_capacity_bind():
    d = _df(_series(300, daily=0.0015, vol=0.012), volume=1e6)
    v = L.v2_view("TEST.NS", d, v1_score=85.0, equity=50_00_000.0)
    assert v is not None
    assert v["ticker"] == "TEST.NS" and v["v1_score"] == 85.0
    assert v["mu_pct"] > 0 and v["sigma_pct"] > 0
    assert v["kelly_rupees"] > 0
    # size is Kelly cut to capacity, never above either
    assert v["v2_size_rupees"] <= v["kelly_rupees"] + 1
    if v["capacity_rupees"] > 0:
        assert v["v2_size_rupees"] <= v["capacity_rupees"] + 1
    # growth rate g = f*mu - f^2 sigma^2 / 2
    f, mu, sg = v["kelly_pct"] / 100, v["mu_pct"] / 100, v["sigma_pct"] / 100
    assert v["growth_rate_pct"] == pytest.approx((f * mu - 0.5 * f * f * sg * sg) * 100, abs=0.02)


def test_v2_view_says_data_needed_not_a_guess():
    thin = _df(_series(30))
    assert L.v2_view("THIN.NS", thin, 90.0, 50_00_000.0) is None
    assert L.v2_view("NONE.NS", None, 90.0, 50_00_000.0) is None


def test_rank_v2_orders_by_growth_not_score():
    rows = [{"ticker": "A", "v1_score": 95.0, "growth_rate_pct": 0.4},
            {"ticker": "B", "v1_score": 60.0, "growth_rate_pct": 2.1}]
    assert [r["ticker"] for r in L.rank_v2(rows)] == ["B", "A"]


# ---- the defensive detector ---------------------------------------------

def test_flags_parabolic_move_without_volume():
    c = _series(200, daily=0.0)
    ramp = [float(c.iloc[-1]) * (1.0 + 0.03) ** (i + 1) for i in range(21)]
    c2 = pd.concat([c, pd.Series(ramp, index=pd.bdate_range("2026-01-01", periods=21))])
    flags = L.manipulation_risk_flags(_df(c2, volume=1e6))
    assert "parabolic_without_volume" in flags
    assert L.is_trap(flags)                     # also trips no_down_days -> refuse


def test_flags_thin_book_ramp_on_tiny_turnover():
    c = _series(200, daily=0.0)
    ramp = [float(c.iloc[-1]) * (1.0 + 0.02) ** (i + 1) for i in range(21)]
    c2 = pd.concat([c, pd.Series(ramp, index=pd.bdate_range("2026-01-01", periods=21))])
    d = _df(c2, volume=100.0)                   # ~Rs 1e4/day turnover
    assert L.adv_rupees(d) < 1_00_00_000
    assert "thin_book_ramp" in L.manipulation_risk_flags(d)


def test_flags_volume_spike_with_price_stall():
    c = _series(200, daily=0.0, vol=0.001)
    vol = pd.Series(1e6, index=c.index)
    vol.iloc[-20:] = 5e6                        # 5x volume, price flat
    d = _df(c)
    d["Volume"] = vol
    assert "volume_spike_price_stall" in L.manipulation_risk_flags(d)


def test_clean_stock_has_no_flags_and_is_not_a_trap():
    d = _df(_series(300, daily=0.0008, vol=0.008))
    flags = L.manipulation_risk_flags(d)
    assert flags == []
    assert L.is_trap(flags) is False


def test_is_trap_needs_two_flags():
    assert L.is_trap([]) is False
    assert L.is_trap(["thin_book_ramp"]) is False           # warn only
    assert L.is_trap(["thin_book_ramp", "no_down_days"]) is True


# ---- the hard boundary: v2 can never trade ------------------------------

def test_engine_and_risk_never_import_lens_v2():
    """v2 is a viewer. If it ever reaches the entry path, this fails."""
    import inspect
    from trading import engine, orders, risk
    for mod in (engine, risk, orders):
        src = inspect.getsource(mod)
        assert "lens_v2" not in src, f"{mod.__name__} must not import lens_v2"


def test_lens_v2_has_no_order_or_config_mutation():
    import inspect
    src = inspect.getsource(L)
    for forbidden in ("place_order", "_open(", "portfolio.cash", "cfg.mode =",
                      "save(", "httpx", "requests"):
        assert forbidden not in src, f"lens_v2 must stay pure: found {forbidden}"
