"""VIX size multiplier — owner formula decision 2026-11-26: the size of a new
automatic buy x1.15 / 1.00 / 0.80 / 0.65 / 0.50 by India VIX band.

Built behind its own switch, DEFAULT OFF: the test fixed in
research/vix_sizing_study/PLAN.md (committed before it was run) did not
support the ladder. The owner owns the switch; the measurement owns the
default.
"""
import ast
import json
import pathlib
from datetime import datetime

import pytest

from trading import sizing as SZ
from trading.config import Config

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "research" / "vix_sizing_study" / "run_2026-11-26" / "vix_sizing.json"


# ------------------------------------------------------------- the ladder

@pytest.mark.parametrize("vix,multiplier,band", [
    (9.5, 1.15, "under 12"), (11.99, 1.15, "under 12"),
    (12.0, 1.00, "12 to under 16"), (15.99, 1.00, "12 to under 16"),
    (16.0, 0.80, "16 to under 20"), (19.99, 0.80, "16 to under 20"),
    (20.0, 0.65, "20 to under 25"), (24.99, 0.65, "20 to under 25"),
    (25.0, 0.50, "25 and above"), (64.4, 0.50, "25 and above"),
])
def test_the_owners_five_bands(vix, multiplier, band):
    assert SZ.vix_size_multiplier(vix) == multiplier
    assert SZ.vix_band_label(vix) == band


@pytest.mark.parametrize("vix", [None, "", "n/a", 0, -3, float("nan"), float("inf")])
def test_an_unreadable_vix_is_unknown_never_assumed_calm(vix):
    assert SZ.vix_size_multiplier(vix) is None
    assert SZ.vix_band_label(vix) == "unknown"
    size, record = SZ.apply_vix_multiplier(300_000, vix, slab=150_000)
    assert size == 300_000 and record["applied"] is False
    assert record["note"] == "India VIX unavailable: size left unchanged"


def test_the_size_is_scaled_inside_the_minimum_and_the_cap():
    scale = lambda wanted, vix, cap=None: SZ.apply_vix_multiplier(wanted, vix, slab=150_000, cap=cap)  # noqa: E731
    assert scale(300_000, 18)[0] == 240_000
    assert scale(300_000, 22)[0] == 195_000
    assert scale(300_000, 10)[0] == pytest.approx(345_000)
    assert scale(300_000, 14) == (300_000, {"vix": 14.0, "band": "12 to under 16", "multiplier": 1.0,
                                            "wanted": 300_000.0, "applied": False, "size": 300_000.0})
    # never below the minimum ticket ...
    size, record = scale(180_000, 30)
    assert size == 150_000 and record["limited_by"] == "minimum ticket" and record["applied"] is True
    # ... but a size already under it is not lifted, and nothing becomes something
    assert scale(100_000, 30)[0] == 100_000 and scale(100_000, 30)[1]["applied"] is False
    assert scale(0, 30)[0] == 0 and scale(0, 10)[0] == 0
    # never above the cap
    size, record = scale(2_400_000, 10, cap=2_500_000)
    assert size == 2_500_000 and record["limited_by"] == "size cap"


def test_the_switch_is_off_by_default():
    assert Config().vix_sizing_enabled is False


# ----------------------------------------------------------- in the scan

def _engine(tmp_path, monkeypatch, vix):
    from trading.regime import RegimeReading
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)
    e.portfolio.positions.clear()
    e.portfolio.cash = float(e.cfg.starting_capital)

    def regime(*a, **k):
        if vix == "feed down":
            return None
        return RegimeReading(
            timestamp=datetime.utcnow(), nifty_close=100.0, nifty_200dma=90.0,
            pct_below_52w_high=2.0, vix=vix, fii_5d_net=100.0,
            regime="bull_extension", everest_active=True, ocean_active=False,
            sizing_multiplier_everest=1.0, sizing_multiplier_ocean=0.0,
            reasoning="test fixture")
    monkeypatch.setattr("trading.regime.fetch_regime", regime)
    return e


def _buy(e):
    result = e.scan()
    assert result.opened == ["UPP.NS"], result.skipped
    line = [r for r in e.portfolio.decision_journal
            if r.get("action") == "BUY" and r.get("ticker") == "UPP.NS"][-1]
    pos = e.portfolio.positions["UPP.NS"]
    return pos.qty * pos.entry_price, line["sizing"]


def test_off_changes_nothing_and_records_nothing(tmp_path, monkeypatch):
    spent, sizing = _buy(_engine(tmp_path, monkeypatch, vix=30.0))
    assert "vix" not in sizing
    assert spent == pytest.approx(sizing["notional"], rel=0.01)


@pytest.mark.parametrize("vix,multiplier", [(18.0, 0.80), (22.0, 0.65), (30.0, 0.50), (14.0, 1.00)])
def test_on_scales_the_buy_and_the_journal_says_by_how_much(tmp_path, monkeypatch, vix, multiplier):
    base, _ = _buy(_engine(tmp_path / "off", monkeypatch, vix=vix))
    e = _engine(tmp_path / "on", monkeypatch, vix=vix)
    e.cfg.vix_sizing_enabled = True
    spent, sizing = _buy(e)
    record = sizing["vix"]
    assert (record["vix"], record["multiplier"]) == (vix, multiplier)
    assert record["applied"] is (multiplier != 1.0)
    expected = max(record["wanted"] * multiplier, min(record["wanted"], e.cfg.position_slab_value))
    assert record["size"] == pytest.approx(expected, abs=1)
    # the position really is that size (to within one share), not just the record
    price = e.portfolio.positions["UPP.NS"].entry_price
    assert spent == pytest.approx(record["size"], abs=price)
    assert spent == pytest.approx(base * record["size"] / record["wanted"], abs=2 * price)


def test_on_at_a_calm_vix_never_passes_the_cap(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, vix=10.0)
    e.cfg.vix_sizing_enabled = True
    spent, sizing = _buy(e)
    cap = e.cfg.kelly_max_pct * e.cfg.starting_capital
    assert sizing["vix"]["multiplier"] == 1.15
    assert sizing["vix"]["size"] <= cap + 1 and spent <= cap + 1


def test_on_with_no_vix_reading_buys_the_unscaled_size_and_says_so(tmp_path, monkeypatch):
    base, _ = _buy(_engine(tmp_path / "off", monkeypatch, vix=14.0))
    e = _engine(tmp_path / "on", monkeypatch, vix=None)
    e.cfg.vix_sizing_enabled = True
    spent, sizing = _buy(e)
    assert sizing["vix"]["applied"] is False and sizing["vix"]["multiplier"] is None
    assert "unavailable" in sizing["vix"]["note"]
    assert spent == pytest.approx(base, rel=0.01)


# ------------------------------------------------------------- boundaries

def test_ocean_and_the_owners_own_buys_are_never_scaled():
    """The multiplier sits in the scan's score-sized branch only: not in the
    Ocean ticket branch, and not in any owner order path."""
    tree = ast.parse((ROOT / "trading" / "engine.py").read_text())
    branches = [n for n in ast.walk(tree) if isinstance(n, ast.If)
                and isinstance(n.test, ast.Call) and getattr(n.test.func, "attr", "") == "get"
                and n.test.args and getattr(n.test.args[0], "value", None) == "ocean"]
    uses = lambda nodes: any(isinstance(x, ast.Name) and x.id == "apply_vix_multiplier"      # noqa: E731
                             for node in nodes for x in ast.walk(node))
    sizing_branch = [b for b in branches if uses(b.orelse)]
    assert len(sizing_branch) == 1, "the VIX multiplier must sit in exactly one non-Ocean branch"
    assert not uses(sizing_branch[0].body), "the Ocean ticket must not be scaled"
    engine_uses = sum(1 for x in ast.walk(tree) if isinstance(x, ast.Name) and x.id == "apply_vix_multiplier")
    assert engine_uses == 1, "one call site only: the automatic scan"
    for path in ("trading/orders.py", "trading/risk.py", "trading/danger.py", "trading/exits.py",
                 "trading/backtest.py", "web/buy_desk.py", "web/routes/positions.py", "web/routes/scan_routes.py"):
        text = (ROOT / path).read_text()
        names = {n.id for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Attribute)}
        assert not ({"apply_vix_multiplier", "vix_size_multiplier", "vix_sizing_enabled"} & names), path


# ------------------------------------------------------------ the record

def test_the_committed_test_result_is_not_supported_and_says_why():
    run = json.loads(RUN.read_text())
    assert run["reading_rule"] == {
        "per_rupee_at_least_plain_first_half": True, "per_rupee_at_least_plain_second_half": False,
        "worst_6m_less_negative_first_half": True, "worst_6m_less_negative_second_half": False,
        "supported": False}
    whole = run["halves"]["whole period"]
    assert (whole["buys"], whole["mean_outcome_pct"], whole["per_rupee_with_multiplier_pct"]) == (11055, 3.431, 3.423)
    assert whole["average_multiplier"] == 0.9155
    by_band = {b["band"]: b for b in run["bands"]["whole period"]}
    assert [by_band[b]["multiplier"] for b in ("under 12", "12 to under 16", "16 to under 20",
                                               "20 to under 25", "25 and above")] == [1.15, 1.0, 0.8, 0.65, 0.5]
    # the band the ladder halves was the best one
    assert max(by_band, key=lambda b: by_band[b]["mean_outcome_pct"]) == "25 and above"
    # the plan exists and names the same ladder the code holds
    plan = (ROOT / "research" / "vix_sizing_study" / "PLAN.md").read_text()
    assert "written before any result was read" in plan
    for edge, multiplier in SZ.VIX_SIZE_BANDS:
        assert f"{multiplier:.2f}" in plan
    assert f"{SZ.VIX_SIZE_TOP:.2f}" in plan
