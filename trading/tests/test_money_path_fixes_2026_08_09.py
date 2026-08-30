"""The five money-path fixes from the 2026-08-09 full-corner audit
(owner: "fix all 5 money path first"). Each test fails if the flaw returns.

A1  strength ladder cut the owner's fixed Rs 1.25L to Rs 87.5k (score 62-74.9)
A2  a single missing quote fired exits / force-closes at entry-price marks
A3  the -2% daily kill switch was never evaluated at scan time
A4  owner limit fills carried score 0 -> score-decay auto-sold their pick
A5  fabricated USFDA/earnings dates blocked real entries
"""

from datetime import date, datetime, timedelta

import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.exits import check_exit
from trading.paper import PaperPosition, Portfolio


def _eng(tmp_path, cash=50_00_000.0):
    cfg = Config(state_dir=str(tmp_path))
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.cash = cash
    return TradingEngine(cfg, p), cfg, p


def _pos(ticker="TCS.NS", entry=1000.0, qty=10, peak=None, tags=None,
         score=70.0, mode="nifty200_momentum"):
    return PaperPosition(
        ticker=ticker, qty=qty, entry_price=entry,
        entry_date=datetime.utcnow() - timedelta(days=5), sector="IT",
        peak_price=peak if peak is not None else entry, entry_score=score,
        reasoning="t", mode=mode, tags=list(tags or []),
        stop_price=entry * 0.95, target_price=entry * 1.12,
    )


# ------------------------------------------------------- A1: the ladder is dead

def test_a_62_to_74_score_gets_the_full_fixed_size():
    """Owner order: Rs 1.25 lakh EACH. The 0.7x cut for scores 60-74.9 is gone
    — the size is the owner's number, not a suggestion."""
    from pathlib import Path
    src = Path("trading/engine.py").read_text()
    assert "strength_mult" not in src, "the strength ladder is back"
    assert "0.7 if s >= 60" not in src


# --------------------------------------- A2: no exits on entry-price fallbacks

def test_missing_quote_holds_the_exit_instead_of_faking_it(tmp_path, monkeypatch):
    """A winner peaked +25%: with its quote missing, the old fallback marked
    it at ENTRY (-20% from peak) and fired the trail, closing the trade at
    entry price. Now: held, journaled EXIT_HELD_NO_QUOTE, still open."""
    eng, cfg, p = _eng(tmp_path)
    p.positions["WIN.NS"] = _pos("WIN.NS", entry=100.0, peak=125.0)
    p.positions["OK.NS"] = _pos("OK.NS", entry=200.0, peak=200.0)
    monkeypatch.setattr(
        "trading.engine.routed_fetch_quotes",
        lambda tickers, upstox_client=None: {"OK.NS": 201.0},  # WIN.NS missing
    )
    monkeypatch.setattr("trading.engine.fetch_daily",
                        lambda *a, **k: {})
    eng.refresh()
    assert "WIN.NS" in p.positions, "position was closed on a missing quote"
    assert not [t for t in p.trades if t.ticker == "WIN.NS"]
    held = [e for e in p.decision_journal
            if e.get("action") == "EXIT_HELD_NO_QUOTE"
            and e.get("ticker") == "WIN.NS"]
    assert held, "the hold must be visible in the journal"


def test_kill_switch_liquidation_skips_unpriced_names(tmp_path, monkeypatch):
    eng, cfg, p = _eng(tmp_path)
    p.positions["A.NS"] = _pos("A.NS", entry=100.0)
    p.positions["B.NS"] = _pos("B.NS", entry=100.0)
    # arm the halt the persistent way, so refresh()'s day-roll restores it
    p.risk_day = datetime.utcnow().date().isoformat()
    p.risk_day_equity = 1.0
    p.risk_day_killed = True
    eng.risk.kill_switch = True
    monkeypatch.setattr(
        "trading.engine.routed_fetch_quotes",
        lambda tickers, upstox_client=None: {"A.NS": 99.0},   # B.NS missing
    )
    monkeypatch.setattr("trading.engine.fetch_daily", lambda *a, **k: {})
    eng.refresh()
    assert "A.NS" not in p.positions          # closed at a REAL price
    assert "B.NS" in p.positions              # never filled at entry price
    a_trades = [t for t in p.trades if t.ticker == "A.NS"]
    assert a_trades and a_trades[-1].reason == "KILL_SWITCH"


def test_square_off_never_fills_at_a_made_up_price(tmp_path, monkeypatch):
    eng, cfg, p = _eng(tmp_path)
    p.positions["INTRA.NS"] = _pos("INTRA.NS", entry=50.0, mode="intraday")
    monkeypatch.setattr(
        "trading.engine.routed_fetch_quotes",
        lambda tickers, upstox_client=None: {},
    )
    closed = eng.square_off_intraday()
    assert closed == []
    assert "INTRA.NS" in p.positions
    assert any(e.get("action") == "SQUARE_OFF_HELD_NO_QUOTE"
               for e in p.decision_journal)


# ------------------------------------------------ A3: kill switch at scan time

def test_scan_blocks_new_entries_on_a_crash_day(tmp_path, monkeypatch):
    """Book down >2% intraday at 09:20: the halt must be live BEFORE the scan
    buys, not discovered at 15:35."""
    eng, cfg, p = _eng(tmp_path, cash=0.0)
    p.positions["HELD.NS"] = _pos("HELD.NS", entry=100.0, qty=50_000)  # 50L book
    # yesterday's baseline: equity 50L
    p.risk_day = datetime.utcnow().date().isoformat()
    p.risk_day_equity = 50_00_000.0
    eng.risk.day_start_equity = 50_00_000.0
    # live quotes show -3% on the whole book
    monkeypatch.setattr(
        "trading.engine.routed_fetch_quotes",
        lambda tickers, upstox_client=None: {"HELD.NS": 97.0},
    )
    from trading.risk import can_open
    # scan's new check must trip the switch...
    held_px = {"HELD.NS": 97.0}
    assert eng.risk.check_kill_switch(p.equity(held_px)) is True
    # ...and can_open must then refuse every entry
    ok, reason, qty = can_open(eng.risk, cash=10_00_000.0,
                               current_equity=p.equity(held_px),
                               open_positions=p.positions, ticker="NEW.NS",
                               fill_price=100.0, cfg=cfg)
    assert ok is False and reason == "kill_switch_active"


def test_scan_source_evaluates_the_switch():
    """scan() itself must contain the evaluation (not only refresh)."""
    from pathlib import Path
    src = Path("trading/engine.py").read_text()
    scan_body = src.split("def scan(")[1].split("def square_off_intraday")[0]
    assert "check_kill_switch" in scan_body, \
        "the 09:20/14:30 scans never evaluate the daily halt"


# ------------------------------------- A4: owner picks are not decay-sellable

def test_owner_pick_is_never_auto_sold_by_score_decay():
    cfg = Config()
    cfg.apply_mode_preset()
    pos = _pos(tags=["owner_pick"], score=0.0)
    should, reason = check_exit(pos, current_price=1010.0, current_score=10.0,
                                cfg=cfg, now=datetime.utcnow())
    assert not (should and reason == "SCORE_DECAY"), \
        "score decay auto-sold the owner's deliberate pick"


def test_engine_picks_still_decay_normally():
    cfg = Config()
    cfg.apply_mode_preset()
    pos = _pos(tags=[], score=70.0)
    should, reason = check_exit(pos, current_price=1010.0, current_score=10.0,
                                cfg=cfg, now=datetime.utcnow())
    assert should and reason == "SCORE_DECAY"


def test_owner_pick_stop_and_target_still_protect():
    """Only the score judgement is the owner's — the hard stop still fires."""
    cfg = Config()
    cfg.apply_mode_preset()
    pos = _pos(tags=["owner_pick"])
    should, reason = check_exit(pos, current_price=940.0, current_score=80.0,
                                cfg=cfg, now=datetime.utcnow())
    assert should and reason == "STOP_LOSS"
    pos2 = _pos(tags=["owner_pick"])
    should2, reason2 = check_exit(pos2, current_price=1130.0, current_score=80.0,
                                  cfg=cfg, now=datetime.utcnow())
    assert should2 and reason2 == "TAKE_PROFIT"


def test_limit_fill_carries_scan_score_and_the_tag(tmp_path, monkeypatch):
    from trading import orders
    eng, cfg, p = _eng(tmp_path)
    p.scan_matrix = {"rows": {"PICK.NS": {"score": 68.4, "c": {"momentum": 70.0}}}}
    o = orders.place_limit(p, ticker="PICK.NS", limit_price=100.0, qty=10)
    orders._fill(eng, o, 99.5, datetime.utcnow())
    pos = p.positions["PICK.NS"]
    assert "owner_pick" in pos.tags
    assert pos.entry_score == pytest.approx(68.4)


# --------------------------------------------- A5: no fabricated event blocks

def test_the_invented_usfda_calendar_is_gone():
    """A5 held, and then strengthened. The fabricated USFDA decisions were
    emptied on 2026-08-09; on 2026-08-30 the owner ordered FDA dropped
    entirely, so the constant and the event type no longer exist at all.
    DRREDDY was blackout-blocked ~Aug 19-22 on a made-up "Rituximab
    biosimilar" decision. Never again."""
    from trading import calendars as cal
    assert not hasattr(cal, "USFDA_DECISIONS_2026")
    for d in (date(2026, 8, 19), date(2026, 8, 20), date(2026, 8, 22)):
        ev = cal.is_ticker_event_within("DRREDDY.NS", days=3, today=d)
        assert ev is None or ev.event_type != "usfda", \
            f"fabricated USFDA event still blocks DRREDDY at {d}"


def test_no_guessed_future_earnings_dates_remain():
    """Companies announce dates ~2-4 weeks ahead; anything further out in the
    committed calendar is a guess, and guesses may not gate money."""
    from trading.calendars import INDIA_EARNINGS_DATES_2026
    horizon = date(2026, 9, 1)
    future = [(t, d) for t, ds in INDIA_EARNINGS_DATES_2026.items()
              for d in ds if d >= horizon]
    assert future == [], f"guessed earnings dates back in the money path: {future}"
