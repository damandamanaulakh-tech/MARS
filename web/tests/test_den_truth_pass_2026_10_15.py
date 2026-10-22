"""MARS Den truth pass (2026-10-15, after the owner's "merge now n show me
the den"). Money impact NONE — every change is display text. An audit read
every number and rule the Den prints against the code that decides it; these
pin the corrections so the page cannot drift back.

  * §4/§2 danger layer: the page says which price tripwires the layer can
    actually COUNT. `danger.evaluate_stock` counts only the Python value True;
    a tripwire that returns a NumPy bool is never counted (upload review
    XR-060, fix awaits the owner). The Den measures this from the tripwires
    themselves, so the warning disappears on its own when the fix lands.
  * the danger leash is the WIDER of 2.5%, the position's category stop and
    its own entry-stop width (engine.refresh), not the live mode's stop.
  * the five-category table prints the history the rotation really pulls
    (daily bars; momentum 2y, the rest TradingEngine.scan's default), not the
    presets' bar_interval/history_period fields, which nothing reads.
  * the liquidity line no longer claims a market-cap check the scan never
    runs (engine passes mcap None).
  * §13's verdict rule and §12's bridge/ordering text match assess.py,
    scenarios.py and names_to_show.
"""
from __future__ import annotations

import inspect
import re

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from trading import danger
from trading.engine import TradingEngine
from web import auth as auth_mod
from web.main import app
from web.routes import den_routes as D


def _den() -> str:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303, "owner login failed"
    page = c.get("/den")
    assert page.status_code == 200
    assert "den_danger_marker" in page.text and "den_risk_marker" in page.text
    return page.text


def _frame(n: int = 320) -> pd.DataFrame:
    i = np.arange(n, dtype=float)
    c = 100.0 * np.exp(0.0004 * i)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
                         "Volume": np.full(n, 2e5)},
                        index=pd.bdate_range(end="2026-10-14", periods=n))


def test_blind_reading_matches_what_the_layer_really_counts(monkeypatch):
    # REPOINTED 2026-10-22 (owner card D8, XR-060 "Ship it"). This pin used
    # to assert the BUG: a NumPy True was not counted, so evaluate_stock
    # returned only the Python-True tripwire. The layer now counts both, and
    # the LIVE tripwires answer Python booleans (danger._python_bool), so the
    # Den's type check reads none of them blind and its red warning goes.
    fake = {"T_NUMPY": lambda x: np.True_, "T_PYTHON": lambda x: True}
    monkeypatch.setattr(danger, "_FNS", fake)
    monkeypatch.setattr(danger, "_EXT_FNS", {})
    assert danger.evaluate_stock(_frame()) == ["T_NUMPY", "T_PYTHON"]
    monkeypatch.undo()
    monkeypatch.setattr(D, "_BLIND", None)
    blind = D._danger_blind()
    assert blind["blind"] == []


def test_blind_warning_disappears_once_every_tripwire_returns_a_python_bool(monkeypatch):
    monkeypatch.setattr(danger, "_FNS", {"T_A": lambda x: bool(np.True_),
                                         "T_B": lambda x: False})
    monkeypatch.setattr(D, "_BLIND", None)
    try:
        html = _den()
        assert "den_danger_blind_marker" not in html
        assert "data-den-danger-blind" not in html
    finally:
        D._BLIND = None


def test_the_den_names_todays_blind_tripwires_while_the_bug_stands(monkeypatch):
    monkeypatch.setattr(D, "_BLIND", None)
    blind = D._danger_blind()
    html = _den()
    if blind["blind"]:
        assert "den_danger_blind_marker" in html
        for tid in blind["blind"] + blind["countable"]:
            assert tid in html
    else:                                               # the fix has landed
        assert "den_danger_blind_marker" not in html


def test_danger_leash_text_matches_the_engine():
    html = " ".join(_den().split())
    assert "the WIDER of" in html and "entry-stop width" in html
    src = inspect.getsource(TradingEngine)
    assert "entry_width" in src and "abs(float(danger.TIGHTEN_PCT))" in src


def test_category_rows_print_the_history_the_rotation_really_pulls():
    html = _den()
    other = inspect.signature(TradingEngine.scan).parameters["history_period"].default
    rows = re.findall(r'data-den-category="([^"]+)">(.*?)</tr>', html, re.S)
    assert len(rows) == 5
    for key, row in rows:
        want = "2y" if key == "nifty200_momentum" else other
        assert f"daily bars · {want} history" in row, (key, row[:300])
        assert "5m bars" not in row and "1wk bars" not in row


def test_liquidity_line_admits_market_cap_is_not_checked():
    html = " ".join(_den().split())
    assert "market cap NOT checked" in html
    src = "\n".join(l for l in inspect.getsource(TradingEngine.scan).splitlines()
                    if not l.strip().startswith("#"))
    assert "mcap_cr = None" in src, "the scan now passes a market cap — update the Den line"


def test_section_13_verdict_rule_matches_assess():
    from trading.valuation import assess, assumptions
    from web import valuation_chain_view as V
    rule = dict(V.STAGE_RULES)["18 verdict"]
    assert "price ≤ base × (1 − margin)" in rule
    assert "above the range high" in rule
    src = inspect.getsource(assess)
    assert "p <= ba * (1 - m)" in src and "elif p > hi:" in src
    assert "price is at least this % below base" in inspect.getsource(assumptions)


def test_section_13_cell_count_adds_up():
    from web import valuation_chain_view as V
    f = V.formulas_section()
    total = sum(int(n) for n in re.findall(r"(\d+) (?:in column|without)", f["chain_cols"]))
    assert total == len(f["chain_cells"])
    html = _den()
    assert f["chain_cols"] in html
