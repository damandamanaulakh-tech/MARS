"""The owner's 2026-09-16 fault report: "how all positons got exist / no new
position are getting added".

Three causes were found and fixed. Each is pinned here, and each of these
tests was checked to FAIL against the pre-fix engine before being kept.

  1. The breadth gate guarded MOMENTUM ONLY. The 09:20 job runs five passes,
     so in the dead band the one pass that holds for 200 days was blocked
     while swing (floor 60 — LOWER than momentum's 62), short and intraday
     kept buying. Intraday is force-closed at 15:25 the same day by design and
     swing sells at 20 days, so the book filled with names that could not stay
     and emptied again.
  2. Rotation SOLD when the engine had refused to buy — it runs after the
     breadth and market-hours gates have emptied the pick list, then falls
     back to the watchlist and sells a near-target winner to chase a name that
     will not be bought. It also never honoured the owner's "no trades outside
     market hours" order, so a boot-time catch-up scan at 21:00 could sell.
  3. The danger layer put stops on a 2.5% leash off the live price — an
     ASSUMED number, unlike the tripwires themselves — marked them
     custom_levels so they never returned to the owner's -5%, and since
     2026-09-10 that leash is checked ~13 times a day instead of ~2.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio


def _engine(tmp_path, mode="swing") -> TradingEngine:
    cfg = Config(state_dir=str(tmp_path), mode=mode)
    cfg.apply_mode_preset()
    return TradingEngine(cfg, Portfolio.fresh(cfg))


def _pos(ticker="TCS.NS", entry=100.0, qty=10, **kw) -> PaperPosition:
    kw.setdefault("sector", "IT")
    kw.setdefault("peak_price", entry)
    kw.setdefault("entry_score", 70.0)
    kw.setdefault("reasoning", "fixture")
    return PaperPosition(ticker=ticker, qty=qty, entry_price=entry,
                         entry_date=datetime(2026, 8, 1), **kw)


def _frame(closes) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                         "Close": closes, "Volume": [1e6] * len(closes)},
                        index=idx)


# ---------------------------------------------------------------------------
# 1. the breadth gate covers every pass
# ---------------------------------------------------------------------------

def _dead_band_history(n_above: int, n_total: int) -> dict:
    """Frames long enough for a 200DMA, with a known fraction above it."""
    hist = {}
    for i in range(n_total):
        if i < n_above:
            closes = [100.0] * 250 + [200.0] * 50      # well above its 200DMA
        else:
            closes = [200.0] * 250 + [100.0] * 50      # well below
        hist[f"T{i}.NS"] = _frame(closes)
    return hist


@pytest.mark.parametrize("mode", ["nifty200_momentum", "intraday", "short",
                                  "swing", "positional"])
def test_breadth_gate_blocks_every_pass_not_only_momentum(tmp_path, mode):
    """THE FAULT: this returned False for every mode but momentum, so the
    passes that buy short-horizon positions were never gated at all."""
    eng = _engine(tmp_path, mode=mode)
    # 50% above their 200DMA — inside the measured dead band (40-65%)
    hist = _dead_band_history(25, 50)
    assert eng._breadth_gate_blocks(hist, datetime.utcnow(), n_picks=3) is True


def test_a_washed_out_tape_still_buys_in_every_pass(tmp_path):
    """The 2026-09-09 re-cut must survive: BELOW 40% measured as one of the
    best states to buy, so extending the gate must not turn it back into a
    'weak market' block."""
    for mode in ("nifty200_momentum", "swing"):
        eng = _engine(tmp_path, mode=mode)
        hist = _dead_band_history(5, 50)          # 10% — washed out
        assert eng._breadth_gate_blocks(hist, datetime.utcnow()) is False


def test_short_history_passes_reuse_the_days_reading(tmp_path):
    """Every mode but momentum pulls 6mo, and compute_breadth drops any ticker
    with under 200 bars — so their own read is (None, 0) and the gate would
    have stayed a no-op for exactly the passes it now has to cover.

    Momentum runs first in the rotation, caches the reading, and the rest read
    it. Market breadth is a property of the market, not of the pass.
    """
    now = datetime.utcnow()
    mom = _engine(tmp_path, mode="nifty200_momentum")
    assert mom._breadth_gate_blocks(_dead_band_history(25, 50), now) is True
    cached = mom.portfolio.breadth_today
    assert cached["asof"] == now.date().isoformat()
    assert 0.40 <= cached["pct"] < 0.65

    swing = _engine(tmp_path, mode="swing")
    swing.portfolio.breadth_today = cached
    short_hist = {"A.NS": _frame([100.0] * 126)}       # 6mo — no 200DMA
    assert swing._breadth_gate_blocks(short_hist, now) is True


def test_a_missed_reading_never_overwrites_a_good_one(tmp_path):
    """A pass that cannot compute breadth must not blank the day's cache —
    that would silently re-open the gate for every pass after it."""
    now = datetime.utcnow()
    eng = _engine(tmp_path, mode="swing")
    good = {"pct": 0.50, "sample": 50, "asof": now.date().isoformat(),
            "mode": "nifty200_momentum", "ts": now.isoformat()}
    eng.portfolio.breadth_today = dict(good)
    eng._breadth_gate_blocks({"A.NS": _frame([100.0] * 126)}, now)
    assert eng.portfolio.breadth_today == good


def test_yesterdays_reading_is_never_reused(tmp_path):
    """Stale breadth must fail OPEN, not block on an old number."""
    now = datetime.utcnow()
    eng = _engine(tmp_path, mode="swing")
    eng.portfolio.breadth_today = {"pct": 0.50, "sample": 50,
                                   "asof": "2020-01-01", "mode": "x",
                                   "ts": "2020-01-01T00:00:00"}
    assert eng._breadth_gate_blocks({"A.NS": _frame([100.0] * 126), }, now) is False


def test_the_vix_bypass_still_wins_in_every_pass(tmp_path):
    eng = _engine(tmp_path, mode="swing")
    assert eng._breadth_gate_blocks(_dead_band_history(25, 50),
                                    datetime.utcnow(), bypass=True) is False


def test_breadth_today_survives_a_restart(tmp_path):
    """risk_day lesson: a reading that lives only in memory is gone on the
    next Render restart, and the passes after it fail open."""
    eng = _engine(tmp_path, mode="nifty200_momentum")
    eng._breadth_gate_blocks(_dead_band_history(25, 50), datetime.utcnow())
    eng.portfolio.save()
    again = Portfolio.load_or_fresh(eng.cfg)
    assert again.breadth_today.get("sample") == 50


# ---------------------------------------------------------------------------
# 0. the engine's clock — the root cause, found while testing the three above
# ---------------------------------------------------------------------------

def test_the_engine_reads_the_session_on_the_real_ist_clock(tmp_path,
                                                            monkeypatch):
    """THE ROOT CAUSE of the owner's whole report.

    Every `now` in the engine is datetime.utcnow() — naive UTC — but
    calendars treats a naive datetime as ALREADY IST. IST is UTC+5:30, so the
    session was read 5h30m late: the 09:20 scan believed the market was shut
    and bought nothing, morning exits were all deferred and fired together
    after 14:45, and the post-close passes traded freely.

    The old guard test only asserted that the string "is_market_open" appears
    in _trading_allowed's source. A name in the source is not a behaviour —
    the fault-mask class. This walks a real trading day instead.
    """
    monkeypatch.delenv("SB_IGNORE_MARKET_HOURS", raising=False)
    eng = _engine(tmp_path, mode="nifty200_momentum")
    ist_to_utc = lambda h, m: datetime(2026, 9, 16, h, m) - timedelta(
        hours=5, minutes=30)

    for h, m in [(9, 20), (10, 0), (12, 0), (14, 30), (15, 25)]:
        assert eng._trading_allowed(ist_to_utc(h, m)) is True, \
            f"{h:02d}:{m:02d} IST is inside the NSE session"
        assert eng._market_state(ist_to_utc(h, m)) == "open"

    for h, m in [(8, 0), (9, 14), (15, 35), (18, 0), (21, 0)]:
        assert eng._trading_allowed(ist_to_utc(h, m)) is False, \
            f"{h:02d}:{m:02d} IST is outside the NSE session"
    assert eng._market_state(ist_to_utc(21, 0)) == "after_close"
    assert eng._market_state(ist_to_utc(8, 0)) == "pre_open"


def test_the_0920_scan_is_inside_the_session(tmp_path, monkeypatch):
    """Stated on its own because it is the whole "no new position are getting
    added" half: the daily scan job runs at 09:20 IST and MUST be allowed to
    open a position."""
    monkeypatch.delenv("SB_IGNORE_MARKET_HOURS", raising=False)
    eng = _engine(tmp_path, mode="nifty200_momentum")
    scan_utc = datetime(2026, 9, 16, 9, 20) - timedelta(hours=5, minutes=30)
    assert eng._trading_allowed(scan_utc) is True


def test_a_weekend_is_still_shut_at_every_hour(tmp_path, monkeypatch):
    monkeypatch.delenv("SB_IGNORE_MARKET_HOURS", raising=False)
    eng = _engine(tmp_path, mode="nifty200_momentum")
    for h in (6, 10, 14, 20):
        sat = datetime(2026, 9, 12, h, 0) - timedelta(hours=5, minutes=30)
        assert eng._trading_allowed(sat) is False


# ---------------------------------------------------------------------------
# 2. rotation cannot sell into a refusal, or outside market hours
# ---------------------------------------------------------------------------

def test_rotation_stands_down_when_the_breadth_gate_blocked_entries(tmp_path):
    """THE DRAIN: no pick survived the gate, so there is nothing to fund — but
    the watchlist fallback would still sell a near-target winner."""
    eng = _engine(tmp_path, mode="nifty200_momentum")
    eng.portfolio.positions["TCS.NS"] = _pos()
    eng.portfolio.cash = 0.0
    eng.portfolio.scan_matrix = {"rows": {"INFY.NS": {"score": 80.0}}}
    now = datetime(2026, 9, 16, 5, 0)          # naive UTC = 10:30 IST, open
    assert eng._rotation_exits([], now, entries_blocked=True) == []
    assert "TCS.NS" in eng.portfolio.positions
    assert any(e.get("action") == "ROTATION_STOOD_DOWN"
               for e in eng.portfolio.decision_journal)


def test_rotation_never_sells_outside_market_hours(tmp_path, monkeypatch):
    """Owner order 2026-09-10 "yes all block". refresh() honoured it via
    marking_only; this branch sat after the gate in scan() and never did, so a
    boot-time catch-up at 21:00 could sell.

    The conftest sets SB_IGNORE_MARKET_HOURS=1 for the whole suite (tests run
    at whatever hour CI wakes up), so this test must drop it or it would
    assert nothing — the fault-mask class.
    """
    monkeypatch.delenv("SB_IGNORE_MARKET_HOURS", raising=False)
    eng = _engine(tmp_path, mode="nifty200_momentum")
    eng.portfolio.positions["TCS.NS"] = _pos()
    eng.portfolio.cash = 0.0
    eng.portfolio.scan_matrix = {"rows": {"INFY.NS": {"score": 80.0}}}
    night = datetime(2026, 9, 16, 15, 30)      # naive UTC = 21:00 IST
    assert eng._rotation_exits([], night) == []
    assert "TCS.NS" in eng.portfolio.positions
    assert any(e.get("action") == "ROTATION_MARKET_CLOSED"
               for e in eng.portfolio.decision_journal)


def test_a_real_funded_pick_still_rotates(tmp_path, monkeypatch):
    """The fix must stay narrow. An ocean pick is exempt from the breadth gate,
    so one reaching rotation IS a buy that will happen and may be funded."""
    import trading.engine as eng_mod

    eng = _engine(tmp_path, mode="nifty200_momentum")
    monkeypatch.setattr(eng_mod, "routed_fetch_quotes",
                        lambda *a, **k: {"TCS.NS": 119.0})
    pos = _pos(entry=100.0, qty=10)
    pos.target_price = 120.0
    eng.portfolio.positions["TCS.NS"] = pos
    eng.portfolio.cash = 0.0
    real_pick = [{"ticker": "INFY.NS", "score": 80.0, "ocean": True}]
    out = eng._rotation_exits(real_pick, datetime(2026, 9, 16, 5, 0),
                              entries_blocked=True)
    assert out == ["TCS.NS"]


# ---------------------------------------------------------------------------
# 3. the danger leash can never be tighter than the owner's own stop
# ---------------------------------------------------------------------------

def test_the_danger_leash_is_floored_at_the_owners_stop():
    """TIGHTEN_PCT is -2.5% and ASSUMED; the owner's stop is -5% and decided.
    The assumed number must not override the decided one."""
    from trading import danger

    cfg = Config(mode="nifty200_momentum")
    cfg.apply_mode_preset()
    leash = max(abs(danger.TIGHTEN_PCT), abs(cfg.stop_loss_pct))
    assert leash == abs(cfg.stop_loss_pct) == 0.05
    assert leash > abs(danger.TIGHTEN_PCT), "the -2.5% leash must be widened"


def test_a_tighter_owner_stop_would_tighten_the_leash_with_it():
    """The floor reads cfg, so it follows the owner rather than pinning 5%."""
    from trading import danger

    cfg = Config(mode="nifty200_momentum")
    cfg.apply_mode_preset()
    cfg.stop_loss_pct = -0.02
    leash = max(abs(danger.TIGHTEN_PCT), abs(cfg.stop_loss_pct))
    assert leash == abs(danger.TIGHTEN_PCT) == 0.025


def test_the_alert_text_names_the_leash_that_was_actually_set():
    """Lying-screen class: the message hardcoded TIGHTEN_PCT, so after the
    floor it would have named a stop that was never applied."""
    from trading import danger

    reading = danger.DangerReading(
        ticker="TCS.NS", fired=list(danger.VERIFIED)[:1],
        names=[danger.VERIFIED[list(danger.VERIFIED)[0]]["name"]],
        action="TIGHTEN")
    assert "-5.0%" in danger.describe(reading, True, leash_pct=0.05)
    assert "-2.5%" not in danger.describe(reading, True, leash_pct=0.05)
    # callers that apply no floor still read the module's own number
    assert "-2.5%" in danger.describe(reading, True)
