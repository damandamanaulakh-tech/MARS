"""Valuation chain stage 18 — financial-selection assessment (owner order
2026-10-14 "lets hit the formulas work completion"; money impact NONE).

Pins trading/valuation/assess.py:
  * each verdict (attractive / watch / unattractive / insufficient) is
    reached by a hand example on a 80 / 100 / 120 range;
  * insufficient on a DATA_NEEDED range, a 70%-wide range, a stated
    uncertainty above 60%, or a missing price;
  * reasons are never empty and not_a_trade_permission is ALWAYS True;
  * the attractive margin is the SAME base buy-discount the context stage
    uses (valuation_context.ASSUMED base_buy_discount_pct);
  * a thesis is recorded, never judged; its only effect is the documented
    above-base-but-within-high rule;
  * the module is pure.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from trading import valuation_context as vc
from trading.valuation import assess as av
from trading.valuation import dn
from trading.valuation import scenarios as sc

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "trading" / "valuation" / "assess.py"
RNG = {"low": 80.0, "base": 100.0, "high": 120.0, "family": "earnings"}
THESIS = {"text": "ER&D demand recovers with EU auto budgets", "horizon": "FY27",
          "falsifier": "two quarters of falling USD revenue"}


def _check_invariants(r):
    assert r["not_a_trade_permission"] is True
    assert r["reasons"], "reasons must never be empty"
    assert r["verdict"] in av.VERDICTS
    assert json.loads(json.dumps(r, allow_nan=False)) == r


def test_attractive_at_or_below_base_less_the_margin():
    r = av.assess(85.0, RNG)
    _check_invariants(r)
    assert r["verdict"] == "attractive"
    assert r["upside_to_base_pct"] == pytest.approx(17.65, abs=0.01)
    assert r["range_width_pct"] == 40.0
    assert r["discount_to_low_pct"] == pytest.approx(-6.25)
    assert av.assess(90.0, RNG)["verdict"] == "attractive"      # exactly base × 0.9


def test_attractive_below_the_low_is_noted():
    r = av.assess(70.0, RNG)
    _check_invariants(r)
    assert r["verdict"] == "attractive"
    assert r["discount_to_low_pct"] == 12.5
    assert any("below the range low" in x for x in r["reasons"])


@pytest.mark.parametrize("price", [95.0, 100.0, 105.0, 110.0])
def test_watch_within_ten_percent_of_base(price):
    r = av.assess(price, RNG)
    _check_invariants(r)
    assert r["verdict"] == "watch"


def test_unattractive_above_the_high():
    r = av.assess(125.0, RNG, thesis=THESIS)
    _check_invariants(r)
    assert r["verdict"] == "unattractive"
    assert r["premium_to_high_pct"] == pytest.approx(4.17, abs=0.01)


def test_unattractive_above_base_plus_ten_with_no_thesis():
    r = av.assess(115.0, RNG)
    _check_invariants(r)
    assert r["verdict"] == "unattractive"
    assert any("no thesis recorded" in x for x in r["reasons"])


def test_a_thesis_is_recorded_and_keeps_above_base_within_high_on_watch():
    r = av.assess(115.0, RNG, thesis=THESIS)
    _check_invariants(r)
    assert r["verdict"] == "watch"
    assert r["thesis"] == THESIS
    assert not any("no thesis" in x for x in r["reasons"])
    # the thesis never changes any other verdict
    for price in (85.0, 100.0, 125.0):
        assert av.assess(price, RNG, thesis=THESIS)["verdict"] == av.assess(price, RNG)["verdict"]


def test_insufficient_on_a_data_needed_range():
    rng = {"low": dn("no band"), "base": dn("no band"), "high": dn("no band")}
    r = av.assess(100.0, rng)
    _check_invariants(r)
    assert r["verdict"] == "insufficient"
    assert any("DATA_NEEDED (no band)" in x for x in r["reasons"])
    assert av.assess(100.0, {})["verdict"] == "insufficient"
    assert av.assess(100.0, None)["verdict"] == "insufficient"


def test_insufficient_on_a_seventy_percent_wide_range():
    r = av.assess(50.0, {"low": 65.0, "base": 100.0, "high": 135.0})
    _check_invariants(r)
    assert r["range_width_pct"] == 70.0
    assert r["verdict"] == "insufficient"          # even though 50 is far below base
    assert any("too uncertain" in x for x in r["reasons"])


def test_insufficient_on_stated_uncertainty_or_missing_price():
    r = av.assess(85.0, RNG, uncertainty_pct=70.0)
    _check_invariants(r)
    assert r["verdict"] == "insufficient" and r["uncertainty_pct"] == 70.0
    assert av.assess(85.0, RNG, uncertainty_pct=60.0)["verdict"] == "attractive"
    for p in (None, 0.0, "x", float("nan")):
        r = av.assess(p, RNG)
        _check_invariants(r)
        assert r["verdict"] == "insufficient"
        assert "price missing" in r["reasons"]


def test_an_unordered_range_is_insufficient():
    r = av.assess(100.0, {"low": 120.0, "base": 100.0, "high": 80.0})
    _check_invariants(r)
    assert r["verdict"] == "insufficient"


def test_attractive_margin_is_the_context_stage_buy_discount():
    assert av.ASSUMED["attractive_margin_pct"]["value"] == vc.ASSUMED["base_buy_discount_pct"]
    for k in ("watch_band_pct", "unattractive_premium_pct", "max_range_width_pct",
              "max_uncertainty_pct"):
        assert av.ASSUMED[k]["source"]


def test_assess_reads_a_stage17_range_directly():
    cases = sc.three_cases(sc.SHEET_BASE, sc.SHEET_DRIVERS, sc.SHEET_MARKET)
    rng = sc.justified_range("earnings", cases, price=715.85)
    r = av.assess(715.85, rng)
    _check_invariants(r)
    # 559.64 / 690.62 / 838.68 is 40.4% wide; 715.85 is +3.65% over base
    assert r["verdict"] == "watch" and r["family"] == "earnings"
    nav = sc.justified_range("nav", cases, price=715.85, rf_pct=7.0, beta=1.0)
    assert av.assess(715.85, nav)["verdict"] == "insufficient"


def _operative_source(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(getattr(body[0], "value", None), ast.Constant) and \
                    isinstance(body[0].value.value, str):
                spans.append((body[0].lineno, body[0].end_lineno or body[0].lineno))
    keep = []
    for i, line in enumerate(text.splitlines(), start=1):
        if any(a <= i <= b for a, b in spans) or line.strip().startswith("#"):
            continue
        keep.append(line.split("  #")[0])
    return "\n".join(keep)


def test_assess_module_is_pure():
    src = _operative_source(MODULE)
    assert not re.search(r"\bimport\s+(httpx|requests|urllib|yfinance|openpyxl)\b", src)
    assert not re.search(r"\bfrom\s+(httpx|requests|urllib|yfinance|openpyxl)\b", src)
    assert not re.search(r"^\s*(from|import)\s+web\b", src, re.M)
    assert not re.search(r"^\s*(from|import)\s+[\w.]*\b(engine|risk|orders|danger)\b",
                         src, re.M)
    assert not re.search(r"^\s*from\s+trading\s+import\s+.*\b(engine|risk|orders|danger)\b",
                         src, re.M)
