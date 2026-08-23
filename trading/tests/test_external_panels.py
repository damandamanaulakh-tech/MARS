"""The external-data panels and the 21 trigger implementations they unlock.

Owner order 2026-08-13: "why u r waiting for 28 which u can wire, do that".
These tests pin the two properties that matter for money-safety:

  1. Without panels, NOTHING changed — every external trigger still reads
     DATA_NEEDED, so the previously-shipped behaviour is untouched.
  2. A missing or truncated panel produces None (DATA_NEEDED), never a quiet
     False. A quiet False would read on screen as "computed, nothing there",
     which is the fake-data failure the owner has banned.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading import external_panels as XP
from trading import triggers as T


def _df(n: int = 200, start: float = 100.0, drift: float = 0.001) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=n)
    c = start * np.cumprod(1 + np.full(n, drift))
    return pd.DataFrame({
        "Open": c * 0.995, "High": c * 1.01, "Low": c * 0.99,
        "Close": c, "Volume": np.full(n, 1_000_000.0),
    }, index=idx)


# ---------------------------------------------------------------------------
# 1. The no-panel path is unchanged
# ---------------------------------------------------------------------------

def test_without_panels_every_external_trigger_is_data_needed():
    hits = T.evaluate(_df())
    for t in T.EXTERNAL:
        assert hits[t.id] is None, f"{t.id} produced a value with no panel"


def test_registry_split_is_the_owners_sheet_plus_new_implementations():
    assert len(T.TRIGGERS) == 112
    assert len(T.COMPUTABLE) == 79            # OHLCV side, unchanged
    assert len(T.EXTERNAL) == 33              # the sheet's external count
    # 23 after the 2026-08-13 feed intake, 32 with the news-fed nine
    # (2026-08-14), 33 with the chain-fed T12W_0102 (Node 8, 2026-08-23):
    # EVERY external trigger now has a data path. 0102 computes only on
    # days the owner's login produced a chain snapshot — wired is not the
    # same as always-computable, and the page says which.
    assert len(T.EXTERNAL_WIRED) == 33
    assert T.EXTERNAL_PENDING == []
    assert len(T.EXTERNAL_WIRED) + len(T.EXTERNAL_PENDING) == len(T.EXTERNAL)
    for t in T.EXTERNAL_PENDING:
        assert t.ext_fn is None
        assert t.needs, f"{t.id} must still say what data would unlock it"


def test_ohlcv_side_keeps_its_own_implementations():
    # Guard against an external impl being wired onto an OHLCV-ready trigger.
    for t in T.COMPUTABLE:
        assert t.fn is not None and t.ext_fn is None


# ---------------------------------------------------------------------------
# 2. Empty panels degrade to DATA_NEEDED, never to a quiet False
# ---------------------------------------------------------------------------

def test_empty_externals_give_none_not_false():
    empty = XP.StockExternals(ticker="X.NS")
    hits = T.evaluate(_df(), ext=empty)
    for t in T.EXTERNAL_WIRED:
        assert hits[t.id] is None, (
            f"{t.id} returned {hits[t.id]!r} from an empty panel — a quiet "
            "False would read as 'computed, nothing there'")


def test_truncated_bulk_month_reads_data_needed_not_quiet():
    """NSE capped every bulk-deal month at 70 rows, so 'no row for this name'
    is not evidence of 'no deal'. The panel must say so."""
    capped = XP.StockExternals(ticker="X.NS", deal_buy_recent=None,
                               deal_sell_recent=None, deals_capped=True)
    hits = T.evaluate(_df(), ext=capped)
    assert hits["T12W_0111"] is None
    assert hits["T12W_0112"] is None
    assert any("70-row" in m for m in capped.missing())


def test_complete_month_absence_is_allowed_to_be_quiet():
    """If a month were NOT capped, absence would be real information."""
    complete = XP.StockExternals(ticker="X.NS", deal_buy_recent=False,
                                 deal_sell_recent=False, deals_capped=False)
    hits = T.evaluate(_df(), ext=complete)
    assert hits["T12W_0111"] is False
    assert hits["T12W_0112"] is False


# ---------------------------------------------------------------------------
# 3. Each wired trigger fires on data built to satisfy the owner's formula
# ---------------------------------------------------------------------------

def test_rs_lead_fires_when_stock_beats_sector_inside_a_base():
    base = _df(drift=0.0)                          # flat = inside a base
    e = XP.StockExternals(ticker="X.NS", sector="Metal", peers=8,
                          ret_10d=0.08, sector_ret_10d=0.01)
    assert T.evaluate(base, ext=e)["T12W_0045"] is True
    e.ret_10d = 0.011                              # spread under the threshold
    assert T.evaluate(base, ext=e)["T12W_0045"] is False


def test_rs_lag_fires_when_stock_trails_sector():
    e = XP.StockExternals(ticker="X.NS", ret_10d=-0.06, sector_ret_10d=0.01)
    assert T.evaluate(_df(), ext=e)["T12W_0051"] is True


def test_rank_jump_uses_the_owners_20_point_rule():
    e = XP.StockExternals(ticker="X.NS", rs_rank_now=70.0, rs_rank_prev=45.0)
    assert T.evaluate(_df(), ext=e)["T12W_0046"] is True
    e.rs_rank_now = 60.0                           # +15 points, under the rule
    assert T.evaluate(_df(), ext=e)["T12W_0046"] is False


def test_peer_breadth_rise_and_fall():
    up = XP.StockExternals(ticker="X.NS",
                           peer_pct_above_ma20=np.linspace(20, 80, 15))
    assert T.evaluate(_df(), ext=up)["T12W_0048"] is True
    down = XP.StockExternals(ticker="X.NS",
                            peer_pct_above_ma20=np.linspace(80, 20, 15))
    assert T.evaluate(_df(), ext=down)["T12W_0048"] is False


def test_holds_while_sector_falls():
    e = XP.StockExternals(ticker="X.NS", ret_5d=0.01, sector_ret_5d=-0.03)
    assert T.evaluate(_df(), ext=e)["T12W_0049"] is True


def test_delivery_spike_uses_the_80th_percentile():
    hot = np.concatenate([np.full(100, 0.30), [0.85]])
    e = XP.StockExternals(ticker="X.NS", delivery_ratio=hot)
    assert T.evaluate(_df(), ext=e)["T12W_0060"] is True
    cold = np.concatenate([np.full(100, 0.30), [0.10]])
    e2 = XP.StockExternals(ticker="X.NS", delivery_ratio=cold)
    assert T.evaluate(_df(), ext=e2)["T12W_0060"] is False


def test_oi_short_build_needs_price_down_and_oi_up():
    down = _df(drift=-0.004)
    rising_oi = np.linspace(1_000, 2_000, 30)
    e = XP.StockExternals(ticker="X.NS", fut_oi=rising_oi)
    assert T.evaluate(down, ext=e)["T12W_0100"] is True
    e2 = XP.StockExternals(ticker="X.NS", fut_oi=np.linspace(2_000, 1_000, 30))
    assert T.evaluate(down, ext=e2)["T12W_0100"] is False       # OI falling
    up = _df(drift=0.004)
    assert T.evaluate(up, ext=e)["T12W_0100"] is False          # price rising


def _breakdown_df(n: int = 200) -> pd.DataFrame:
    """A base, then a bar that closes below the prior 20 sessions' lowest low.

    A monotone decline never does this (each bar's Low sits under its Close by
    construction), so a real support break has to be built explicitly.
    """
    d = _df(n, drift=0.0)
    d.iloc[-1, d.columns.get_loc("Low")] = d["Close"].iloc[-2] * 0.93
    d.iloc[-1, d.columns.get_loc("Close")] = d["Low"].iloc[:-1].min() * 0.98
    return d


def test_index_overlay_needs_weak_index_and_a_broken_support():
    breaking = _breakdown_df()
    weak_index = np.linspace(100, 80, 60)
    e = XP.StockExternals(ticker="X.NS", index_close=weak_index)
    assert T.evaluate(breaking, ext=e)["T12W_0095"] is True
    strong_index = np.linspace(80, 100, 60)
    e2 = XP.StockExternals(ticker="X.NS", index_close=strong_index)
    assert T.evaluate(breaking, ext=e2)["T12W_0095"] is False
    # ...and a weak index alone, with support intact, is not a tripwire
    assert T.evaluate(_df(), ext=e)["T12W_0095"] is False


def test_peer_breakdown_count_and_industry_share():
    e = XP.StockExternals(ticker="X.NS", peers=10, peer_break_20d_low=4)
    hits = T.evaluate(_df(), ext=e)                # rising price = not broken
    assert hits["T12W_0052"] is True               # >=3 peers broke
    assert hits["T12W_0094"] is True               # >=30% of the industry
    e2 = XP.StockExternals(ticker="X.NS", peers=10, peer_break_20d_low=1)
    hits2 = T.evaluate(_df(), ext=e2)
    assert hits2["T12W_0052"] is False and hits2["T12W_0094"] is False


def test_top_decile_inside_industry():
    e = XP.StockExternals(ticker="X.NS", own_sector_rank=95.0)
    assert T.evaluate(_df(), ext=e)["T12W_0050"] is True
    e.own_sector_rank = 55.0
    assert T.evaluate(_df(), ext=e)["T12W_0050"] is False


def test_turnover_rank_jump_uses_25_points():
    e = XP.StockExternals(ticker="X.NS", turnover_rank_now=80.0,
                          turnover_rank_prev=50.0)
    assert T.evaluate(_df(), ext=e)["T12W_0059"] is True
    e.turnover_rank_now = 65.0
    assert T.evaluate(_df(), ext=e)["T12W_0059"] is False


def test_industry_rank_deterioration():
    e = XP.StockExternals(ticker="X.NS", sector_rank_now=30.0,
                          sector_rank_prev=80.0)
    assert T.evaluate(_df(), ext=e)["T12W_0055"] is True


def test_rs_line_triggers_need_a_real_rs_line():
    # RS breaking up BEFORE price: rs line at a 10d high while price sits
    # below its own 10d high (a flat series is AT its high, so pull it back).
    pulled_back = _df(drift=0.0)
    pulled_back.iloc[-1, pulled_back.columns.get_loc("Close")] *= 0.97
    e = XP.StockExternals(ticker="X.NS", rs_line=np.linspace(1.0, 1.3, 60))
    hits = T.evaluate(pulled_back, ext=e)
    assert hits["T12W_0039"] is True               # RS at a 10d high...
    falling_rs = XP.StockExternals(ticker="X.NS",
                                   rs_line=np.linspace(1.3, 1.0, 60))
    assert T.evaluate(pulled_back, ext=falling_rs)["T12W_0039"] is False
    short = XP.StockExternals(ticker="X.NS", rs_line=np.array([1.0, 1.1]))
    assert T.evaluate(_df(), ext=short)["T12W_0039"] is None   # thin = unknown


# ---------------------------------------------------------------------------
# 4. Loaders and the real committed files
# ---------------------------------------------------------------------------

def test_prepare_reads_the_committed_panels():
    ed = XP.prepare(["TCS.NS"])
    assert ed.sources["panel"] and ed.sources["sector_map"]
    x = ed.for_ticker("TCS.NS")
    assert x is not None and x.sector, "TCS must land in a sector"
    assert x.asof, "every panel reading must carry its as-of date"
    assert x.ret_10d is not None and x.sector_ret_10d is not None


def test_shareholding_reads_promoter_and_public_only():
    q = XP.shareholding_quarters("TCS.NS")
    if q is None:
        pytest.skip("TCS shareholding json not fetched in this checkout")
    assert 0 < q[0]["promoter_pct"] < 100
    assert q[0]["public_pct"] is not None
    # The FII/DII/MF split is NOT in these files — it lives in the XBRL each
    # record links to. Anything claiming otherwise would be invented.
    assert "fii_pct" not in q[0] and "dii_pct" not in q[0]


def test_pcr_is_labelled_as_open_interest_not_volume():
    p = XP.pcr_oi("TATASTEEL.NS")
    if p is None:
        pytest.skip("F&O OI panel absent")
    assert p["pcr_oi"] > 0
    assert "open interest" in p["basis"] and "not volume" in p["basis"]


def test_missing_names_the_panels_that_came_back_empty():
    x = XP.StockExternals(ticker="NOSUCH.NS")
    missing = x.missing()
    assert "sector/peer panel" in missing and "delivery" in missing
    assert "F&O OI" in missing


# ---------------------------------------------------------------------------
# 5. Boundary: this is display/research only, like triggers and lens_v2
# ---------------------------------------------------------------------------

def test_money_path_never_imports_the_panels():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in ("engine.py", "risk.py", "orders.py"):
        src = (root / name).read_text()
        assert "external_panels" not in src, (
            f"{name} must not import external_panels — display/research only "
            "(the owner's own F-006 rule: risk side may act, entry side may "
            "not)")


def test_panels_module_places_no_orders_and_touches_no_network():
    from pathlib import Path
    src = (Path(XP.__file__)).read_text()
    for forbidden in ("requests.", "urllib", "http", "place_order",
                      "buy(", "sell("):
        assert forbidden not in src, f"external_panels must not contain {forbidden}"


# ---------------------------------------------------------------------------
# Feeds fetched 2026-08-13 (owner: "fetch it" then "wire the 13")
# ---------------------------------------------------------------------------

def test_institutional_holdings_read_with_deltas():
    h = XP.institutional_holdings("TATASTEEL.NS")
    if h is None:
        pytest.skip("holdings not fetched in this checkout")
    for k in ("fii_pct", "dii_pct", "mf_pct"):
        assert h[k] is not None and 0 <= h[k] <= 100
    # two quarters were fetched, so the QoQ deltas must be real numbers
    assert h["prior_quarter"] and h["fii_delta"] is not None
    assert h["promoter_pledged"] in ("true", "false")
    assert h["fii_holders"] and h["fii_holders"] > 0     # X-07's input


def test_unreliable_eps_history_is_withheld_not_published():
    """Yahoo's epsTrend history is inconsistent: 16 of 189 symbols imply a
    >100% 30-day estimate change and 5 carry eps_30d_ago = 0. The counts are
    sound; the percentage must be suppressed rather than published wrong."""
    r = XP.revisions_view("RELIANCE.NS")
    if r is None:
        pytest.skip("revisions not fetched in this checkout")
    assert r["pct_change_30d"] is None and r["pct_unreliable"] is True
    # ...while the revision COUNTS still come through
    assert r["up_30d"] is not None and r["down_30d"] is not None
    assert r["net_revisions_30d"] == r["up_30d"] - r["down_30d"]


def test_true_free_float_replaces_the_proxy():
    sh = XP.shares_view("TATASTEEL.NS")
    if sh is None:
        pytest.skip("key stats not fetched in this checkout")
    assert sh["shares_outstanding"] > 0
    assert 0 < sh["free_float_pct"] <= 100
    assert sh["float_shares"] <= sh["shares_outstanding"]


def test_earnings_window_is_point_in_time():
    """days_away must be measured from the as-of date, not from today, or a
    backtest would read tomorrow's calendar."""
    a = XP.earnings_window("TATASTEEL.NS", asof="2026-07-17")
    b = XP.earnings_window("TATASTEEL.NS", asof="2026-07-27")
    if a is None or b is None:
        pytest.skip("earnings dates not fetched in this checkout")
    assert a["days_away"] - b["days_away"] == 10


def test_the_two_new_triggers_are_display_only_and_uncalibrated():
    from trading import danger
    for tid in ("T12W_0098", "T12W_0105"):
        assert tid in {t.id for t in T.EXTERNAL_WIRED}
        assert tid not in danger.VERIFIED, (
            f"{tid} is uncalibrated — it must not reach the money path")


def test_earnings_trigger_needs_the_window_and_volatility():
    base = _df(drift=0.0)
    far = XP.StockExternals(ticker="X.NS", earnings_days_away=40)
    assert T.evaluate(base, ext=far)["T12W_0098"] is False
    no_cal = XP.StockExternals(ticker="X.NS")
    assert T.evaluate(base, ext=no_cal)["T12W_0098"] is None    # DATA_NEEDED


def test_broker_upgrade_trigger_needs_both_legs():
    up_and_strong = XP.StockExternals(ticker="X.NS", analyst_net_revisions=3.0,
                                     own_sector_rank=90.0)
    assert T.evaluate(_df(), ext=up_and_strong)["T12W_0105"] is True
    up_but_weak = XP.StockExternals(ticker="X.NS", analyst_net_revisions=3.0,
                                    own_sector_rank=20.0)
    assert T.evaluate(_df(), ext=up_but_weak)["T12W_0105"] is False
    downgrades = XP.StockExternals(ticker="X.NS", analyst_net_revisions=-5.0,
                                   own_sector_rank=90.0)
    assert T.evaluate(_df(), ext=downgrades)["T12W_0105"] is False
    assert T.evaluate(_df(), ext=XP.StockExternals(ticker="X.NS")
                      )["T12W_0105"] is None
