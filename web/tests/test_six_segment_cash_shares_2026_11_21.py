"""Owner's six cash shares: reporting counts Ocean once and settings retain it."""
from datetime import datetime
import re
from types import SimpleNamespace

import pytest

from trading import category_budget as cb
from trading.config import Config
from trading.paper import PaperPosition, Portfolio
from web import market_panels as mp, state
from web.routes import settings_routes


SHARES = {
    cb.MOMENTUM: 0.30, "intraday": 0.10, "swing": 0.24,
    "short": 0.24, "positional": 0.10, cb.OCEAN: 0.02,
}


def _position(ticker, *, mode, tags=()):
    return PaperPosition(
        ticker=ticker, qty=100, entry_price=100.0,
        entry_date=datetime(2026, 11, 20), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode=mode, tags=list(tags),
        stop_price=95.0, target_price=120.0,
    )


@pytest.mark.parametrize("reserve", [0.0, 0.20, 0.40])
def test_fund_summary_counts_ocean_once_with_the_same_deployable_base(tmp_path, reserve):
    cfg = Config(state_dir=str(tmp_path), cash_reserve_pct=reserve)
    p = Portfolio.fresh(cfg)
    p.positions = {
        "MOM.NS": _position("MOM.NS", mode=cb.MOMENTUM, tags=["owner_pick"]),
        "OCN.NS": _position("OCN.NS", mode=cb.MOMENTUM, tags=["ocean"]),
    }
    p.cash = cfg.starting_capital - 20_000.0
    prices = {"MOM.NS": 110.0, "OCN.NS": 90.0}

    summary = mp.fund_summary(p, cfg, prices, live_count=2)
    rows = {row["category"]: row for row in summary["rows"]}
    assert len(summary["rows"]) == len(rows) == 6
    assert set(rows) == set(SHARES)
    assert summary["allocated"] == 20_000.0
    assert summary["now"] == 20_000.0
    assert summary["pnl"] == 0.0
    assert sum(row["open"] for row in rows.values()) == 2
    assert summary["share_total"] == pytest.approx(1.0)
    assert sum(row["limit"] for row in rows.values()) == pytest.approx(summary["deployable"])
    for segment, share in SHARES.items():
        assert rows[segment]["share"] == share
        assert rows[segment]["limit"] == pytest.approx(summary["equity"] * (1 - reserve) * share)
    assert rows[cb.OCEAN]["pnl_vs_share"] == pytest.approx(-1000 / rows[cb.OCEAN]["limit"] * 100)
    assert rows[cb.MOMENTUM]["allocated"] == 10_000.0


def test_disabling_shares_does_not_restore_a_separate_ocean_bucket(tmp_path):
    cfg = Config(state_dir=str(tmp_path), category_budgets_enabled=False)
    p = Portfolio.fresh(cfg)
    p.positions = {"OCN.NS": _position("OCN.NS", mode=cb.MOMENTUM, tags=["ocean"])}
    summary = mp.fund_summary(p, cfg, {"OCN.NS": 90.0}, live_count=1)
    ocean = [row for row in summary["rows"] if row["category"] == cb.OCEAN]
    assert len(ocean) == 1
    assert ocean[0]["limit"] is None
    assert ocean[0]["share"] is None
    assert summary["allocated"] == 10_000.0
    assert summary["now"] == 9_000.0
    assert summary["share_total"] is None


def test_settings_display_and_save_all_six_percentages(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    saved = {}
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(state, "reload_config", lambda: None)
    monkeypatch.setattr(settings_routes.db, "save_settings", lambda data: saved.update(data))
    fields = {field["name"]: field for field in settings_routes._grouped()["Capital"]}
    submitted = {}
    for segment, expected in SHARES.items():
        name = cb.SHARE_FIELDS[segment]
        assert fields[name]["value"] == expected * 100
        assert fields[name]["is_pct"] is True
        submitted[name] = str(expected * 100)
    response = settings_routes._settings_save_body(submitted)
    assert response.status_code == 303
    for segment, expected in SHARES.items():
        name = cb.SHARE_FIELDS[segment]
        assert getattr(cfg, name) == expected
        assert saved[name] == expected


@pytest.mark.parametrize("entered, expected", [("-2", 0.0), ("120", 1.0)])
def test_ocean_share_has_the_same_settings_bounds_as_other_segments(tmp_path, monkeypatch, entered, expected):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    saved = {}
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(state, "reload_config", lambda: None)
    monkeypatch.setattr(settings_routes.db, "save_settings", lambda data: saved.update(data))
    settings_routes._settings_save_body({"budget_share_ocean": entered})
    assert cfg.budget_share_ocean == expected
    assert saved["budget_share_ocean"] == expected


@pytest.mark.parametrize("path", ["/scan", "/positions"])
def test_ocean_budget_does_not_add_an_unsupported_manual_segment(tmp_path, monkeypatch, path):
    from fastapi.testclient import TestClient
    from trading.engine import TradingEngine
    from web import auth
    from web.main import app

    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    eng = TradingEngine(cfg, portfolio=p)
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(state, "engine", lambda: eng)
    monkeypatch.setattr(auth, "current_user", lambda request: {"role": "owner"})
    response = TestClient(app).get(path)
    assert response.status_code == 200
    select = re.search(r'<select\b[^>]*name="mode"[^>]*>(.*?)</select>', response.text, re.S)
    assert select is not None
    values = re.findall(r'<option\b[^>]*value="([^"]+)"', select.group(1))
    assert set(values) == set(SHARES) - {cb.OCEAN}


def test_rotation_fallback_keeps_five_passes_and_labels_real_ocean_refusal(tmp_path):
    from web import why_no_buy as wnb

    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    res = SimpleNamespace(ranked=[], picks=[{"ticker": "OCN.NS", "ocean": True}],
                          opened=[], skipped=[{"ticker": "OCN.NS", "reason": "category_budget_full"}])
    rec = wnb.pass_record(cb.MOMENTUM, res, [{
        "action": "CATEGORY_BUDGET_FULL", "category": cb.OCEAN,
        "why": "Ocean has spent its 2% share",
    }], 62.0, None)
    rot = {"date": "2026-11-21", "where": "09:20", "status": "complete",
           "opened_total": 0, "passes": {cb.MOMENTUM: rec}}
    p.rotation_report = {"rotations": [rot]}
    card = wnb.card(p, cfg, "2026-11-21")
    assert [row["mode"] for row in card["rows"]] == list(cb.OWNER_SEGMENTS)
    assert card["rows"][0]["blocks"][0]["category"] == cb.OCEAN
    assert card["rows"][0]["stopped_by"].startswith("Ocean: share full")
    text = wnb.summary_text(rot)
    assert "Ocean: share full" in text
    assert "Ocean: not run" not in text


def test_ocean_budget_refusal_is_reported_under_ocean():
    rows = mp.why_not_today([{
        "ts": "2026-11-21T05:00:00", "action": "CATEGORY_BUDGET_FULL",
        "category": cb.OCEAN, "why": "Ocean has spent its 2% share",
    }], "2026-11-21")
    ocean = [row for row in rows if row["category"] == cb.OCEAN]
    assert len(ocean) == 1
    assert ocean[0]["why"] == "Ocean has spent its 2% share"
