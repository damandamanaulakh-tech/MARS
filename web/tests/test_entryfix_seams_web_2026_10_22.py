"""Entry-path fix batch, lead seam pins on the screens (owner 2026-10-22):
the dashboard fund card prints the SAME category limit the gate refuses on
(share of what is left after the owner bucket and Ocean), and the two
screens that still described the spread check / the XR-060 bug tell the
truth after the batch.
"""
from __future__ import annotations

from datetime import datetime

from trading import category_budget as cb
from trading.config import Config
from trading.paper import PaperPosition, Portfolio
from web import market_panels as mp
from web.routes import settings_routes


def _pos(t, qty, px, mode="nifty200_momentum", tags=None):
    return PaperPosition(
        ticker=t, qty=qty, entry_price=px, entry_date=datetime(2026, 10, 20),
        sector="IT", peak_price=px, entry_score=70.0, reasoning="fixture",
        mode=mode, stop_price=px * 0.95, target_price=px * 1.2,
        tags=list(tags or []))


def test_fund_card_limit_is_the_share_of_what_is_left_after_owner_and_ocean(tmp_path):
    """Owner card 2026-10-22 ("Shares from what is left after owner +
    Ocean"): Rs 5 Cr book, Rs 1 Cr of owner buys, Rs 10 L of Ocean ->
    momentum's limit on the card = 40% of Rs 3.9 Cr, exactly what
    risk.can_open refuses on — not 40% of the whole Rs 5 Cr (old card)."""
    cfg = Config(state_dir=str(tmp_path))
    cfg.cash_reserve_pct = 0.0
    p = Portfolio.fresh(cfg)
    p.positions = {
        "OWN.NS": _pos("OWN.NS", 1000, 10_000.0, mode="", tags=["owner_pick"]),
        "OCN.NS": _pos("OCN.NS", 100, 10_000.0, tags=["ocean"]),
    }
    prices = {"OWN.NS": 10_000.0, "OCN.NS": 10_000.0}
    f = mp.fund_summary(p, cfg, prices, live_count=2)
    rows = {r["category"]: r for r in f["rows"]}
    equity = p.equity(prices)
    expected = cb.limit(cfg, cb.MOMENTUM, equity, positions=p.positions)
    assert rows[cb.MOMENTUM]["limit"] == expected
    base = cb.share_base(cfg, p.positions, equity)
    assert base == cb.deployable(cfg, equity) - 1_00_00_000.0 - 10_00_000.0
    assert rows[cb.MOMENTUM]["limit"] == cb.share(cfg, cb.MOMENTUM) * base
    # and NOT the old reading (share of the whole deployable amount)
    assert rows[cb.MOMENTUM]["limit"] < cb.share(cfg, cb.MOMENTUM) * cb.deployable(cfg, equity)


def test_fund_card_without_owner_or_ocean_holdings_is_unchanged(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    cfg.cash_reserve_pct = 0.0
    p = Portfolio.fresh(cfg)
    p.positions = {"SWG.NS": _pos("SWG.NS", 10, 100.0, mode="swing")}
    f = mp.fund_summary(p, cfg, {"SWG.NS": 100.0}, live_count=1)
    rows = {r["category"]: r for r in f["rows"]}
    equity = p.equity({"SWG.NS": 100.0})
    assert rows[cb.MOMENTUM]["limit"] == cb.share(cfg, cb.MOMENTUM) * cb.deployable(cfg, equity)


def test_settings_danger_hint_no_longer_calls_xr060_a_known_bug():
    rows = [r for r in settings_routes.FIELDS if r[0] == "danger_layer_enabled"] \
        if hasattr(settings_routes, "FIELDS") else []
    src = open(settings_routes.__file__, encoding="utf-8").read()
    line = [l for l in src.splitlines() if '"danger_layer_enabled"' in l][0]
    assert "KNOWN BUG" not in line
    assert "only 2 of the 10" not in line
    assert "All 10 price tripwires" in line
    assert "XR-060 fixed 2026-10-22" in line


def test_den_engines_page_says_the_spread_check_is_off():
    import pathlib
    tpl = pathlib.Path(mp.__file__).resolve().parent / "templates" / "_den_engines.html"
    text = tpl.read_text(encoding="utf-8")
    assert "spread ≤" not in text
    assert "bid-ask spread NOT checked" in text
    assert "switched off, owner card 2026-10-22" in text
