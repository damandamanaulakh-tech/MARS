"""Visual candle chart /chart/{ticker} (owner ask 2026-07-29): a real
candlestick SVG drawn server-side from stored data — display only, never
fake, structure markers on the exact bars where they fired."""

from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from web import auth as auth_mod
from web.candle_chart import load_ohlcv, per_bar_structures, render_svg
from web.main import app


def _df(n=120, base=100.0):
    idx = pd.bdate_range("2026-01-01", periods=n)
    px = pd.Series([base + i * 0.5 for i in range(n)], index=idx)
    return pd.DataFrame({"Open": px, "High": px + 1, "Low": px - 1,
                         "Close": px + 0.5, "Volume": 1e6})


def test_svg_draws_every_candle_with_volume_and_tooltips():
    df = _df(120)
    svg = render_svg(df, bars=90)
    assert svg.startswith("<svg")
    assert svg.count("<rect") == 180          # 90 bodies + 90 volume bars
    assert svg.count("<title>") == 90         # every bar hoverable
    assert "₹" in svg                          # price axis labelled


def test_svg_marks_structures_on_the_right_bar():
    df = _df(120)
    hits = {89: [("kicker_bull", "bull")], 10: [("evening_star", "bear")]}
    svg = render_svg(df, hits=hits, bars=90)
    assert "kicker bull" in svg and "▲" in svg
    assert "evening star (bearish warning)" in svg and "▼" in svg


def test_svg_draws_position_levels():
    df = _df(120)
    svg = render_svg(df, levels=[("entry", 120.0, "#334155"),
                                 ("stop", 114.0, "#dc2626"),
                                 ("target", 134.4, "#059669")], bars=90)
    assert "entry ₹120.00" in svg
    assert "stop ₹114.00" in svg and "target ₹134.40" in svg
    assert svg.count("stroke-dasharray") == 3


def test_per_bar_structures_finds_a_seeded_pattern_on_its_bar():
    df = _df(120)
    # carve a kicker on the LAST bar: red bar then gap-up green
    df.iloc[-2] = [200.0, 201.0, 194.0, 195.0, 1e6]
    df.iloc[-1] = [207.0, 213.0, 206.0, 212.0, 1e6]
    hits = per_bar_structures(df, bars=90)
    assert any(nm == "kicker_bull" for nm, _f in hits.get(89, []))


def test_load_ohlcv_falls_back_to_repo_zip_without_network():
    with patch("web.db.is_connected", return_value=False):
        df = load_ohlcv("RELIANCE.NS")
        assert df is not None and len(df) > 0
        assert load_ohlcv("NOSUCH.NS") is None   # honest None, no fetch


def test_chart_page_owner_sees_svg_anon_redirected():
    anon = TestClient(app)
    r = anon.get("/chart/RELIANCE.NS", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]

    owner = TestClient(app)
    owner.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    with patch("web.db.is_connected", return_value=False):
        page = owner.get("/chart/RELIANCE.NS").text
    assert "<svg" in page and "Daily Candles" in page


def test_chart_page_shows_data_needed_never_fakes():
    owner = TestClient(app)
    owner.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    with patch("web.candle_chart.load_ohlcv", return_value=None), \
         patch("web.routes.chart_routes.load_ohlcv", return_value=None):
        page = owner.get("/chart/NOSUCH.NS").text
    assert "DATA_NEEDED" in page and "<svg" not in page


def test_dashboard_chip_styling_derives_from_registry():
    """The 18->34 expansion made the dashboard's hardcoded bearish list
    stale (new bearish structures would have shown green). Styling must
    come from the registry now."""
    src = open("web/templates/dashboard.html").read()
    assert "bearish_names" in src
    assert "'bearish_engulfing','evening_star'" not in src


def test_stock_rows_link_to_the_chart():
    for tpl in ("scan.html", "dashboard.html", "positions.html"):
        src = open(f"web/templates/{tpl}").read()
        assert "/chart/" in src, tpl
