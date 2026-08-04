"""The 2026-07-31 "check and fix all" batch — guards for flaws that had been
listed as known-and-deferred for weeks. Each test fails if the flaw returns.

1. broker.bid_ask() did not exist, so the spread half of the liquidity gate
   never ran once (AttributeError swallowed by the caller's try/except).
2. Portfolio.dd_peak_equity was not persisted, so the drawdown governor
   restarted with peak 0 and measured no drawdown after every Render restart.
3. The -15% trailing stop cannot fire under -5%/+12% and the app said nothing.
4. retag_positions_per_mode() overwrote owner-set stops/targets on boot.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from trading.config import Config
from trading.exits import retag_positions_per_mode
from trading.paper import PaperPosition, Portfolio


# ---------------------------------------------------------------- 1. bid_ask

class _FakeUpstox:
    """Only the bits bid_ask() touches."""
    def __init__(self, quote):
        self._quote = quote
        self.asked = []

    def get_quote(self, key):
        self.asked.append(key)
        return self._quote


def _bid_ask(quote, ticker="TCS.NS"):
    from trading.brokers.upstox import UpstoxClient
    fake = _FakeUpstox(quote)
    return UpstoxClient.bid_ask(fake, ticker), fake


def test_bid_ask_exists_on_the_real_client():
    """engine.scan() calls broker.bid_ask(); it must be a real method."""
    from trading.brokers.upstox import UpstoxClient
    assert callable(getattr(UpstoxClient, "bid_ask", None))


def test_bid_ask_reads_the_top_of_the_order_book():
    quote = {"depth": {"buy": [{"price": 3990.0, "quantity": 50},
                               {"price": 3989.0, "quantity": 10}],
                       "sell": [{"price": 3992.0, "quantity": 40},
                                {"price": 3993.5, "quantity": 5}]}}
    (bid, ask), _ = _bid_ask(quote, "NSE_EQ|INE467B01029")
    assert bid == 3990.0 and ask == 3992.0
    spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
    assert 0.0 < spread_pct < 0.1


def test_bid_ask_returns_none_when_depth_is_missing():
    """No depth = skip the spread test explicitly. Never fabricate a spread."""
    for quote in ({}, {"depth": None}, {"depth": {}},
                  {"depth": {"buy": [], "sell": []}}, None):
        (bid, ask), _ = _bid_ask(quote, "NSE_EQ|INE467B01029")
        assert bid is None and ask is None, quote


def test_bid_ask_rejects_zero_prices():
    quote = {"depth": {"buy": [{"price": 0}], "sell": [{"price": 3992.0}]}}
    (bid, ask), _ = _bid_ask(quote, "NSE_EQ|INE467B01029")
    assert bid is None and ask is None


def test_bid_ask_passes_a_raw_instrument_key_straight_through():
    quote = {"depth": {"buy": [{"price": 10.0}], "sell": [{"price": 10.2}]}}
    (bid, ask), fake = _bid_ask(quote, "NSE_EQ|INE467B01029")
    assert fake.asked == ["NSE_EQ|INE467B01029"]
    assert (bid, ask) == (10.0, 10.2)


def test_bid_ask_maps_a_yfinance_ticker_to_an_instrument_key(monkeypatch):
    monkeypatch.setattr(
        "trading.brokers.upstox_instruments.to_instrument_keys",
        lambda tickers: {"TCS.NS": "NSE_EQ|INE467B01029"},
    )
    quote = {"depth": {"buy": [{"price": 10.0}], "sell": [{"price": 10.2}]}}
    (bid, ask), fake = _bid_ask(quote, "TCS.NS")
    assert fake.asked == ["NSE_EQ|INE467B01029"]
    assert (bid, ask) == (10.0, 10.2)


def test_bid_ask_gives_up_when_the_ticker_cannot_be_resolved(monkeypatch):
    monkeypatch.setattr(
        "trading.brokers.upstox_instruments.to_instrument_keys",
        lambda tickers: {},
    )
    (bid, ask), fake = _bid_ask({"depth": {"buy": [{"price": 1.0}],
                                           "sell": [{"price": 1.1}]}}, "NOPE.NS")
    assert (bid, ask) == (None, None)
    assert fake.asked == [], "must not query Upstox with an empty key"


# --------------------------------------------------- 2. drawdown peak persists

def test_dd_peak_equity_survives_a_save_load_round_trip(tmp_path):
    cfg = Config(state_dir=str(tmp_path), starting_capital=50_00_000.0)
    p = Portfolio.fresh(cfg)
    p.dd_peak_equity = 52_34_567.0
    p.save()
    back = Portfolio.load_or_fresh(cfg)
    assert back.dd_peak_equity == pytest.approx(52_34_567.0)


def test_legacy_book_without_the_field_loads_as_zero(tmp_path):
    import json
    cfg = Config(state_dir=str(tmp_path), starting_capital=100.0)
    with open(tmp_path / "portfolio.json", "w") as f:
        json.dump({"cash": 100.0, "positions": {}, "trades": []}, f)
    back = Portfolio.load_or_fresh(cfg)
    assert back.dd_peak_equity == 0.0


def test_mongo_mirror_carries_the_peak_both_ways():
    """web/state.py mirrors to Mongo — a field missing there dies on restart
    anyway, because Render wipes the JSON in /tmp."""
    src = Path("web/state.py").read_text()
    hydrate = src.split("def _hydrate_portfolio_from_mongo")[1].split("\ndef ")[0]
    mirror = src.split("def _patch_portfolio_save_to_mirror_mongo")[1].split("\ndef ")[0]
    assert "dd_peak_equity" in hydrate, "restart loses the drawdown peak"
    assert "dd_peak_equity" in mirror, "the peak is never written to Mongo"


def test_engine_restores_the_peak_before_updating_the_governor():
    src = Path("trading/engine.py").read_text()
    update = src.find("self.drawdown_gov.update(")
    assert update != -1
    before, after = src[:update], src[update:]
    assert "dd_peak_equity" in before[-1200:], \
        "the persisted peak must be restored BEFORE update(), not after"
    assert "dd_peak_equity" in after[:600], "the new peak is never saved back"


# ------------------------------------------------------- 3. trail reachability

def test_the_live_bracket_reports_its_trail_as_unreachable():
    """Owner's live numbers: stop -5%, target +12%, trail -15%. A -15% trail
    only clears the -5% stop once the peak passes +11.76% — and +12% closes
    the trade. Window: 0.24pp. Practically dead."""
    cfg = Config(stop_loss_pct=-0.05, take_profit_pct=0.12,
                 trailing_stop_pct=-0.15)
    live, need = cfg.trail_is_reachable()
    assert live is False
    assert need == pytest.approx(0.1176, abs=0.0005)


def test_the_old_bracket_had_a_working_trail():
    """Before the -5% stop order it was -12%: a 3.5pp window, and the study
    showed the trail firing 6-41x per window. The stop change killed it."""
    cfg = Config(stop_loss_pct=-0.12, take_profit_pct=0.12,
                 trailing_stop_pct=-0.15)
    live, need = cfg.trail_is_reachable()
    assert live is True
    assert need == pytest.approx(0.0353, abs=0.0005)


def test_a_wider_target_revives_the_trail():
    """Variant D from the exit study (+20% target) — reported, not wired."""
    cfg = Config(stop_loss_pct=-0.05, take_profit_pct=0.20,
                 trailing_stop_pct=-0.15)
    assert cfg.trail_is_reachable()[0] is True


def test_a_nonsense_trail_is_never_called_reachable():
    for trail in (0.0, 0.1, -1.0, -1.5):
        cfg = Config(stop_loss_pct=-0.05, take_profit_pct=0.12,
                     trailing_stop_pct=trail)
        assert cfg.trail_is_reachable()[0] is False, trail


# ---------------------------------------------------- 4. owner levels survive

def _pos(**kw) -> PaperPosition:
    base = dict(ticker="TCS.NS", qty=10, entry_price=1000.0,
                entry_date=datetime.utcnow() - timedelta(days=3),
                sector="IT", peak_price=1000.0, entry_score=70.0,
                reasoning="t", mode="nifty200_momentum",
                stop_price=950.0, target_price=1120.0)
    base.update(kw)
    return PaperPosition(**base)


def test_owner_custom_levels_survive_the_boot_retag(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.positions["TCS.NS"] = _pos(stop_price=900.0, target_price=1300.0,
                                 custom_levels=True, mode="swing")
    changed = retag_positions_per_mode(p)
    pos = p.positions["TCS.NS"]
    assert changed == 0
    assert pos.stop_price == 900.0, "owner's stop was overwritten by the preset"
    assert pos.target_price == 1300.0
    assert not [e for e in p.decision_journal if e.get("action") == "RETAG_EXITS"]


def test_engine_set_levels_are_still_retagged(tmp_path):
    """The fix must not disable the retag for ordinary engine positions."""
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.positions["TCS.NS"] = _pos(mode="", stop_price=0.0, target_price=0.0)
    assert retag_positions_per_mode(p) == 1
    assert p.positions["TCS.NS"].stop_price > 0


def test_custom_levels_flag_round_trips_through_storage(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.positions["TCS.NS"] = _pos(custom_levels=True)
    p.save()
    back = Portfolio.load_or_fresh(cfg)
    assert back.positions["TCS.NS"].custom_levels is True


def test_legacy_positions_default_to_not_custom():
    pos = PaperPosition.from_dict({
        "ticker": "TCS.NS", "qty": 1, "entry_price": 1.0,
        "entry_date": datetime.utcnow().isoformat(), "sector": "IT",
        "peak_price": 1.0, "entry_score": 1.0, "reasoning": "legacy",
    })
    assert pos.custom_levels is False


def test_order_ticket_marks_levels_as_custom():
    from trading.orders import apply_custom_levels
    pos = _pos(custom_levels=False)
    note = apply_custom_levels(pos, stop_pct=8, target_pct=25)
    assert pos.custom_levels is True
    assert pos.stop_price == 920.0 and pos.target_price == 1250.0
    assert "stop" in note and "target" in note


def test_order_ticket_without_levels_leaves_the_flag_off():
    from trading.orders import apply_custom_levels
    pos = _pos(custom_levels=False)
    apply_custom_levels(pos, stop_pct=None, target_pct=None)
    assert pos.custom_levels is False
