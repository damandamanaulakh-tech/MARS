"""Guard tests for the Selection Register v4 layer (display-only)."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading import register_v4 as rv

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Boundary: the money path must never touch this module, and this module
# must never touch orders / config / network.
# ---------------------------------------------------------------------------

def test_money_path_never_imports_register():
    for name in ("engine.py", "risk.py", "orders.py"):
        src = (ROOT / "trading" / name).read_text()
        assert "register_v4" not in src, f"{name} imports the register layer"


def test_register_source_has_no_forbidden_calls():
    src = (ROOT / "trading" / "register_v4.py").read_text()
    for banned in ("requests.", "httpx", "urllib", "place_order", "buy(",
                   "sell(", "cfg.mode =", "apply_mode_preset"):
        assert banned not in src, f"forbidden reference in register_v4: {banned}"


# ---------------------------------------------------------------------------
# Registry: parsed from the owner's sheet, complete and consistent
# ---------------------------------------------------------------------------

def test_registry_has_279_unique_ids():
    assert len(rv.REGISTRY) == 279
    assert len(rv.REGISTRY_BY_ID) == 279


def test_registry_matches_committed_csv():
    with open(ROOT / "research" / "external" /
              "selection_register_v4_279.csv", encoding="utf-8") as f:
        ids = [r["ID"] for r in csv.DictReader(f) if r.get("ID")]
    assert ids == [r["ID"] for r in rv.REGISTRY]


def test_every_referenced_id_exists_in_sheet():
    for rid in list(rv.AM07_HAVE) + rv._STOCK_IDS + \
            list(rv._MARKET_DATA_NEEDED) + list(rv.U_S_MAPPING):
        assert rid in rv.REGISTRY_BY_ID, f"{rid} not in the owner's sheet"


def test_no_id_both_have_and_computed():
    overlap = set(rv.AM07_HAVE) & set(rv._STOCK_IDS)
    assert not overlap, f"ids marked HAVE but also re-computed: {overlap}"


# ---------------------------------------------------------------------------
# Per-stock computation on production-shaped OHLCV (capitalized columns —
# the trigger-layer lesson)
# ---------------------------------------------------------------------------

def _frame(n=1400, seed=7, trap_last=False):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0006, 0.015, n)
    close = 100 * np.cumprod(1 + rets)
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    vol = rng.integers(50_000, 500_000, n).astype(float)
    if trap_last:
        prior_high = high[-21:-1].max()
        high[-1] = prior_high * 1.03
        close[-1] = prior_high * 0.985
        open_[-1] = prior_high * 0.99
        low[-1] = min(low[-1], close[-1] * 0.995)
    idx = pd.bdate_range(end="2026-08-07", periods=n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def test_stock_view_covers_every_stock_id():
    view = rv.stock_view(_frame(), "NOSUCH")
    for rid in rv._STOCK_IDS:
        assert rid in view, f"{rid} missing from stock_view"
        assert view[rid].state in ("VALUE", "FIRED", "QUIET", "NA",
                                   "PARTIAL", "DATA_NEEDED")


def test_trap_tag_fires_on_constructed_trap_bar():
    view = rv.stock_view(_frame(trap_last=True), "NOSUCH")
    assert view["E-04"].state == "FIRED"


def test_cagr_matches_hand_calc():
    df = _frame()
    view = rv.stock_view(df, "NOSUCH")
    c = df["Close"].to_numpy()
    expect = ((c[-1] / c[-756]) ** (1 / 3) - 1) * 100
    assert view["H-11"].state == "VALUE"
    assert abs(view["H-11"].value - expect) < 1e-9


def test_skip_month_momentum_matches_hand_calc():
    df = _frame()
    view = rv.stock_view(df, "NOSUCH")
    c = df["Close"].to_numpy()
    assert abs(view["X-01"].value - (c[-22] / c[-253] - 1) * 100) < 1e-9


def test_rvol_matches_hand_calc():
    df = _frame()
    view = rv.stock_view(df, "NOSUCH")
    v = df["Volume"]
    assert abs(view["W-009"].value -
               v.iloc[-1] / v.rolling(20).mean().iloc[-1]) < 1e-9


def test_short_history_yields_na_not_numbers():
    view = rv.stock_view(_frame(n=30), "NOSUCH")
    assert view["H-11"].state == "NA"
    assert view["X-01"].state == "NA"


def test_fundamental_items_data_needed_without_symbol_data():
    view = rv.stock_view(_frame(), "NOSUCHSYMBOL")
    for rid in ("W-014", "X-12", "X-16"):
        assert view[rid].state == "DATA_NEEDED"


def test_roe_from_real_committed_fundamentals():
    if not (ROOT / "data" / "fundamentals_n200_2026-08-09.csv").exists():
        pytest.skip("fundamentals CSV not present")
    view = rv.stock_view(_frame(), "RELIANCE")
    assert view["W-014"].state == "VALUE"
    fund = rv.load_fundamentals("RELIANCE")
    ni = rv._latest(fund["annualNetIncome"])[1]
    eq = rv._latest(fund["annualStockholdersEquity"])[1]
    assert abs(view["W-014"].value - ni / eq) < 1e-12


# ---------------------------------------------------------------------------
# Market-level items
# ---------------------------------------------------------------------------

def _panel(k=15, n=300):
    return {f"S{i}": _frame(n=n, seed=i) for i in range(k)}


def _flows(neg_last10=8):
    dates = pd.bdate_range(end="2026-08-07", periods=120)
    vals = np.abs(np.random.default_rng(1).normal(1500, 800, 120))
    vals[-neg_last10:] = -np.abs(vals[-neg_last10:])
    return pd.DataFrame({"date": dates.strftime("%Y-%m-%d"),
                         "fii_cash_net": vals, "dii_cash_net": ""})


def test_fii_sell_cluster_fires_on_8_of_10_negative():
    out = rv.market_view({}, flows=_flows(neg_last10=8))
    assert out["FII-02"].state == "FIRED"
    out2 = rv.market_view({}, flows=_flows(neg_last10=2))
    assert out2["FII-02"].state == "QUIET"


def test_fii3_data_needed_when_dii_blank():
    out = rv.market_view({}, flows=_flows())
    assert out["FII-03"].state == "DATA_NEEDED"


def test_px_dbi_items_stay_data_needed():
    out = rv.market_view(_panel(), index_close=_frame(n=300)["Close"])
    assert out["PX-01"].state == "DATA_NEEDED"
    assert out["PX-05"].state == "DATA_NEEDED"
    assert "dbi" in out["PX-01"].note


def test_px_computable_items_evaluate_with_panel():
    idx = _frame(n=300, seed=99)["Close"]
    out = rv.market_view(_panel(), index_close=idx)
    assert out["PX-02"].state in ("FIRED", "QUIET")
    assert out["PX-03"].state in ("FIRED", "QUIET")


def test_pwoi_items_from_real_committed_panel():
    if not (ROOT / "data" / "fo_participant_oi_2024_2026.csv").exists():
        pytest.skip("PWOI panel not present")
    pwoi = pd.read_csv(ROOT / "data" / "fo_participant_oi_2024_2026.csv")
    out = rv.market_view({}, pwoi=pwoi)
    assert out["FII-04"].state in ("FIRED", "QUIET")
    assert out["FII-05"].state in ("FIRED", "QUIET")
    assert out["FII-07"].state in ("FIRED", "QUIET")


def test_damage_stack_items_wait_for_owner_pick():
    out = rv.market_view({})
    assert out["FII-08"].state == "DATA_NEEDED"
    assert "pick one" in out["FII-08"].note


def test_market_inputs_load_from_committed_files():
    inputs = rv.load_market_inputs()
    assert "index" in inputs and len(inputs["index"]) > 1000
    assert "vix" in inputs and len(inputs["vix"]) > 1000
    assert "flows" in inputs and len(inputs["flows"]) > 500
    assert "pwoi" in inputs and len(inputs["pwoi"]) > 3000
    assert "bond" in inputs and len(inputs["bond"]) > 1000


# ---------------------------------------------------------------------------
# Scan-quality telemetry (S-12/13/14)
# ---------------------------------------------------------------------------

def test_effective_score_discounts_never_inflates():
    full = {k: 1.0 for k in rv._REQUIRED_FEATURES}
    out = rv.scan_quality(full, 70.0)
    assert out["S-13"].value == 100.0
    assert out["S-14"].value == 70.0
    partial = dict(full, correlation=None, quality=None)
    out2 = rv.scan_quality(partial, 70.0)
    assert out2["S-14"].value < 70.0
    assert "UNKNOWN" in out2["S-12"].note


def test_scan_quality_handles_missing_score():
    out = rv.scan_quality({}, None)
    assert out["S-14"].state == "NA"
