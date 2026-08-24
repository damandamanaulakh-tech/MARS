"""B-batch truth pass (owner: "fix B batch too") — every screen number the
2026-08-09 audit caught lying. Each test fails if the lie returns.
"""

import math
from pathlib import Path

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app


def _client(login: bool = True) -> TestClient:
    c = TestClient(app)
    if login:
        c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    return c


# B1 — the main header no longer prints the regime as a count
def test_dashboard_header_labels_the_regime_as_a_regime():
    src = Path("web/templates/dashboard.html").read_text()
    assert "exits due" not in src
    html = _client().get("/").text
    assert "exits due" not in html


# B2 — engine page tells the truth about Ocean and the reserve ladder
def test_engine_page_shows_parked_while_the_ocean_switch_is_off():
    cfg = state.cfg()
    assert cfg.ocean_enabled is False        # the shipped state
    src = Path("web/templates/engine.html").read_text()
    assert "PARKED (switch off)" in src
    assert "20% / 25% / 35% / 50%" not in src        # fictional ladder gone
    assert "0% / 0% / 20% / 40%" in src              # the real one
    assert "and FII flows are positive" not in src   # FII does not gate
    assert "Refreshes every 15 min automatically" not in src
    assert "regime_at" in src                        # timestamp rendered


# B3 — risk page truth pass + read-only governor
def test_risk_page_numbers_match_the_code():
    src = Path("web/templates/risk.html").read_text()
    assert "70% return correlation" not in src
    assert "85% return correlation" in src
    assert "Metal" not in src                        # fictional blacklist gone
    assert "18%, mid 12%, high 6%" not in src        # dead vol-tiers gone
    assert "as of the engine's last live pass" in src


def test_risk_page_view_never_mutates_the_governor():
    src = Path("web/routes/risk_routes.py").read_text()
    assert "drawdown_gov.update(" not in src, \
        "a page VIEW is mutating live risk state with entry marks"


# B4 — positions export carries real marks and says where each came from
def test_positions_csv_uses_live_marks_and_labels_the_source(tmp_path):
    from datetime import datetime
    from trading.config import Config
    from trading.paper import PaperPosition, Portfolio
    from trading.reports import write_positions_csv
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.positions["X.NS"] = PaperPosition(
        ticker="X.NS", qty=10, entry_price=100.0,
        entry_date=datetime(2026, 7, 1), sector="IT", peak_price=120.0,
        entry_score=70.0, reasoning="t")
    path = write_positions_csv(p, {"X.NS": 120.0},
                               price_source={"X.NS": "live"})
    body = Path(path).read_text()
    assert "price_source" in body
    assert "200.00" in body            # (120-100)*10 unrealised — not 0.00
    assert ",live," in body


def test_export_route_fetches_live_marks():
    src = Path("web/routes/reports_routes.py").read_text()
    assert "app_state.marks(" in src
    assert "price_source" in src


# B5 — the kill switch reaches the phone; the alerts page stops overselling
def test_kill_switch_is_a_push_action_now():
    from web.push_ntfy import PUSH_ACTIONS
    assert "KILL_SWITCH_SCAN" in PUSH_ACTIONS
    assert "EXIT_HELD_NO_QUOTE" in PUSH_ACTIONS


def test_alerts_page_admits_email_is_not_wired():
    src = Path("web/templates/alerts.html").read_text()
    assert "NOT WIRED" in src
    assert "no code sends email yet" in src.replace("\n", " ")


# B6 — yesterday's close is never served as a quote
def test_daily_close_quote_branch_is_gone():
    src = Path("trading/data.py").read_text()
    fq = src.split("def fetch_quotes(")[1].split("\ndef ")[0]
    assert "_fetch_daily_upstox" not in fq, \
        "fetch_quotes serves daily-candle closes as live quotes again"


# B7 — IFR badge respects the 22-day window
def test_ifr_badge_expires_after_the_window():
    from web.routes.dashboard import _ifr_window_active

    class _P:
        last_ifr_fire_date = "2020-01-01"          # ancient fire
        last_ifr_ratio_fire_date = None
    assert _ifr_window_active(_P()) is False

    from datetime import date, timedelta
    class _P2:
        last_ifr_fire_date = (date.today() - timedelta(days=3)).isoformat()
        last_ifr_ratio_fire_date = None
    assert _ifr_window_active(_P2()) is True


# B8 — marketing numbers that rotted are gone / dynamic
def test_settings_mode_picker_has_no_cagr_claim():
    src = Path("web/routes/settings_routes.py").read_text()
    assert "29.7" not in src


def test_order_ticket_placeholders_derive_from_the_preset():
    src = Path("web/templates/scan.html").read_text()
    assert 'placeholder="5 (preset)"' not in src
    assert "{{ preset_target }}" in src and "{{ preset_stop }}" in src
    html = _client().get("/scan").text
    assert "20" in html                # live momentum target +20 (Node 12)


def test_dashboard_dropped_the_dead_yfinance_promise():
    src = Path("web/templates/dashboard.html").read_text()
    assert "~15-min delayed" not in src and "~15 min delay" not in src
    assert "5-year default range" not in src


# B9 — score integrity: neutral means neutral
def test_zero_net_flow_scores_neutral_not_bearish():
    from trading.signals.flows import fii_flow_score
    assert fii_flow_score(0.0) == 50.0
    assert fii_flow_score(0.0, days=1) == 50.0


def test_csv_fallback_rejects_all_zero_rows(tmp_path):
    from trading.signals.flows import fetch_flows_from_csv
    f = tmp_path / "fii.csv"
    f.write_text("date,fii_cash_net,dii_cash_net\n"
                 "2026-08-01,0,0\n"
                 "2026-08-02,1500,\n")
    flows = fetch_flows_from_csv(str(f))
    assert flows is not None and len(flows) == 1
    assert flows[0].fii_net == 1500.0


def test_nan_input_scores_neutral_not_max_bearish():
    from trading.scoring import _clip
    assert _clip(math.nan) == 50.0
    assert _clip(120.0) == 100.0 and _clip(-5.0) == 0.0


def test_neutral_fallback_factors_are_not_sold_as_top_drivers():
    from trading.scoring import reasoning_text
    txt = reasoning_text({"momentum_1m": 80.0, "catalyst": 50.0,
                          "volume_expansion": 71.0, "short_interest": 50.0},
                         total=68.0)
    assert "catalyst" not in txt        # neutral fallback, drives nothing
    assert "momentum_1m" in txt


# B11 — a truncated benchmark is disclosed on the backtest result
def test_benchmark_truncation_is_disclosed():
    import pandas as pd
    from trading.backtest import _benchmark_staleness_warnings
    idx = pd.date_range("2026-01-01", "2026-06-05", freq="B")
    bench = pd.DataFrame({"Close": range(len(idx))}, index=idx)
    w = _benchmark_staleness_warnings(bench, "2026-08-09")
    assert w and "BENCHMARK TRUNCATED" in w[0]
    assert _benchmark_staleness_warnings(bench, "2026-06-06") == []


# B12 — the N200 benchmark is structurally possible now
def test_index_key_map_is_unified_and_carries_cnx200():
    from trading.brokers.upstox_instruments import INDEX_KEYS
    from trading.data import _INDEX_KEYS
    assert _INDEX_KEYS is INDEX_KEYS
    assert "^CNX200" in _INDEX_KEYS and "^BSESN" in _INDEX_KEYS


def test_preloaded_backtest_panel_asks_for_the_n200_benchmark():
    src = Path("web/routes/backtest_routes.py").read_text()
    assert "BENCHMARK2" in src
