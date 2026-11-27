"""OWNER DECISION 2026-11-27, from an options card: "Cap only, never block".

The owner read the live reading on their own Risk page and sent the
screenshot: NO_POSITIVE_EDGE on NINE usable exits (win rate 11%, payoff
0.27, Kelly -1.645), with the momentum engine switched off because of it.
The confidence ramp needs 300 closed trades to carry full weight, yet the
veto fired at nine — so the book could not earn the record that would have
cleared it. Their option, verbatim: "The check shrinks position size when
the edge is weak but never refuses a buy outright."

What that means in code, and what these tests pin:
  * cfg.measured_kelly_block_enabled defaults to False;
  * a NO_POSITIVE_EDGE reading CAPS every new scan entry at the owner's
    minimum ticket instead of refusing it — a cap of zero would be a veto
    wearing a cap's clothes, so the floor is the slab;
  * the OBSERVATION stays FAILED either way. The owner's standing rule is
    that a loss is never rewritten as an edge, so the decision record still
    says the edge test did not pass; only the reason says what was done;
  * the journal action names the policy that ran. A line called
    MEASURED_KELLY_BLOCKED on a day nothing was blocked is the
    lying-screen class, so cap-only writes MEASURED_KELLY_NO_EDGE;
  * setting the switch to 1 restores the 2026-10-02 veto exactly.

Every test here fails on the code before this change, where the refusal was
unconditional.
"""
from __future__ import annotations

from datetime import datetime

from trading.config import Config
from trading.engine import TradingEngine
from trading.measured_kelly import measure
from trading.paper import ClosedTrade
from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine

MOM = "nifty200_momentum"


def _trade(i, ret, reason="STOP_LOSS"):
    entry = 100.0
    exit_ = entry * (1 + ret)
    return ClosedTrade(
        ticker=f"K{i}.NS", qty=100, entry_date=datetime(2026, 1, 1),
        exit_date=datetime(2026, 2, 1), entry_price=entry, exit_price=exit_,
        gross_pnl=(exit_ - entry) * 100, costs=0.0,
        net_pnl=(exit_ - entry) * 100, reason=reason, sector="IT",
        entry_score=70.0, reasoning="fixture", tags=[], mode=MOM)


def _no_edge():
    """10 small wins against 30 full stops — Kelly negative."""
    return ([_trade(i, 0.02, "TAKE_PROFIT") for i in range(10)]
            + [_trade(100 + i, -0.05) for i in range(30)])


def _nine_like_the_owners_book():
    """The live shape of 2026-11-27: 9 usable exits, 1 win, payoff well
    under 1. The owner's page read win rate 11% and payoff 0.27."""
    return [_trade(0, 0.02, "TAKE_PROFIT")] + [_trade(1 + i, -0.07)
                                               for i in range(8)]


def _fresh(tmp_path, monkeypatch, trades, veto=False):
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.portfolio.cash = 5_00_00_000.0
    e.portfolio.positions.clear()
    e.portfolio.trades = list(trades)
    e.cfg.measured_kelly_block_enabled = bool(veto)
    return e


# ------------------------------------------------------------- the switch

def test_the_veto_is_off_by_default():
    """The owner's decision is the default, not an opt-in."""
    assert Config().measured_kelly_block_enabled is False
    # the check itself stays on — it still caps
    assert Config().measured_kelly_enabled is True


# ------------------------------------------------------- the scan behaviour

def test_no_measured_edge_no_longer_refuses_the_entry(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    e.scan()
    reasons = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert reasons.get("UPP.NS") != "measured_kelly_no_edge"
    assert "UPP.NS" in e.portfolio.positions, (
        "cap-only must still open the position; reasons=%r" % (reasons,))


def test_the_nine_trade_reading_that_stopped_the_live_book_now_buys(
        tmp_path, monkeypatch):
    """The exact shape of the owner's 27 Nov screen."""
    trades = _nine_like_the_owners_book()
    r = measure(trades, fraction=0.29, category=MOM)
    assert r.status == "NO_POSITIVE_EDGE" and r.usable == 9
    e = _fresh(tmp_path, monkeypatch, trades)
    e.scan()
    assert "UPP.NS" in e.portfolio.positions


def test_the_capped_entry_opens_at_the_minimum_ticket(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    e.scan()
    pos = e.portfolio.positions["UPP.NS"]
    notional = pos.qty * pos.entry_price
    slab = e.cfg.position_slab_value
    # one whole share of slack either side: the slab is rounded to shares
    assert slab - pos.entry_price <= notional <= slab + pos.entry_price, notional


def test_the_veto_switched_on_refuses_exactly_as_before(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge(), veto=True)
    e.scan()
    assert "UPP.NS" not in e.portfolio.positions
    reasons = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert reasons.get("UPP.NS") == "measured_kelly_no_edge"


# ----------------------------------------------------------- the honesty

def test_the_edge_test_still_reads_as_failed_when_it_caps(tmp_path, monkeypatch):
    """A cap is not a pass. The owner's rule: never rewrite a loss as an
    edge — so the recorded observation must still say the gate failed."""
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    e.scan()
    buys = [x for x in e.portfolio.decision_journal if x.get("action") == "BUY"]
    assert buys, "expected the capped buy to be journaled"
    ev = buys[-1]["evidence"]
    mk = ev["gates"]["measured_kelly"]
    # the observation stands: the edge test did not pass
    assert mk["state"] == "failed", mk
    # ... and the trade still happened. Both facts in one record.
    assert ev["outcome"] == "bought", ev["outcome"]
    assert "capped" in mk["reason"].lower(), mk["reason"]
    assert "not refused" in mk["reason"].lower(), mk["reason"]

    # with the veto on, the same failed gate blocks instead
    v = _fresh(tmp_path, monkeypatch, _no_edge(), veto=True)
    v.scan()
    rows = [x for x in v.portfolio.decision_journal
            if x.get("evidence", {}).get("ticker") == "UPP.NS"]
    vev = rows[-1]["evidence"]
    assert vev["gates"]["measured_kelly"]["state"] == "failed"
    assert vev["outcome"] == "blocked", vev["outcome"]
    assert "refused" in vev["gates"]["measured_kelly"]["reason"].lower()


def test_the_journal_action_names_the_policy_that_ran(tmp_path, monkeypatch):
    cap = _fresh(tmp_path, monkeypatch, _no_edge())
    cap.scan()
    actions = [x.get("action") for x in cap.portfolio.decision_journal]
    assert "MEASURED_KELLY_NO_EDGE" in actions
    assert "MEASURED_KELLY_BLOCKED" not in actions, (
        "nothing was blocked, so no line may say BLOCKED")
    line = [x for x in cap.portfolio.decision_journal
            if x.get("action") == "MEASURED_KELLY_NO_EDGE"][-1]
    assert line["policy"] == "cap_only"
    assert "NOT refused" in line["why"]

    veto = _fresh(tmp_path, monkeypatch, _no_edge(), veto=True)
    veto.scan()
    vactions = [x.get("action") for x in veto.portfolio.decision_journal]
    assert "MEASURED_KELLY_BLOCKED" in vactions
    assert "MEASURED_KELLY_NO_EDGE" not in vactions
    vline = [x for x in veto.portfolio.decision_journal
             if x.get("action") == "MEASURED_KELLY_BLOCKED"][-1]
    assert vline["policy"] == "block"


def test_the_reading_itself_never_claims_the_entry_was_blocked(tmp_path,
                                                               monkeypatch):
    """measure() is pure and cannot see cfg.measured_kelly_block_enabled, so
    its note must not assert a policy. It said "so new entries are blocked",
    which renders on BOTH the /risk card and the Den engines screen — on a
    cap-only day that sentence is false on the owner's own screen. The
    estimator reports the measurement; the screen reports what was done."""
    r = measure(_no_edge(), fraction=0.29, category=MOM)
    assert r.status == "NO_POSITIVE_EDGE"
    assert "blocked" not in r.note.lower(), r.note
    assert "no measured edge" in r.note.lower() or "NO measured edge" in r.note
    # the machine-readable flag still says the edge is absent
    assert r.block_new_entries is True

    allloss = measure([_trade(i, -0.05) for i in range(12)],
                      fraction=0.29, category=MOM)
    assert "blocked" not in allloss.note.lower(), allloss.note


# --------------------------------------------------------------- the cap

def test_the_cap_is_the_floor_when_there_is_no_edge():
    cfg = Config(state_dir="/tmp/sb_mk_caponly", mode=MOM)
    cfg.apply_mode_preset()
    cfg.measured_kelly_block_enabled = False
    r = measure(_no_edge(), fraction=0.29, category=MOM)
    assert r.status == "NO_POSITIVE_EDGE" and r.fraction == 0.0
    cap = TradingEngine._measured_kelly_cap(r, cfg, 5_00_00_000.0)
    assert cap == cfg.position_slab_value, cap
    # a cap of zero would be a veto wearing a cap's clothes
    assert cap > 0


def test_the_cap_is_unused_when_the_veto_refuses_the_pick():
    cfg = Config(state_dir="/tmp/sb_mk_veto", mode=MOM)
    cfg.apply_mode_preset()
    cfg.measured_kelly_block_enabled = True
    r = measure(_no_edge(), fraction=0.29, category=MOM)
    assert TradingEngine._measured_kelly_cap(r, cfg, 5_00_00_000.0) is None


def test_a_measured_edge_still_caps_on_its_own_fraction():
    """Unchanged behaviour: this is the half the owner kept."""
    cfg = Config(state_dir="/tmp/sb_mk_active", mode=MOM)
    cfg.apply_mode_preset()
    strong = measure([_trade(i, 0.20, "TAKE_PROFIT") for i in range(250)]
                     + [_trade(500 + i, -0.05) for i in range(50)],
                     fraction=0.29, category=MOM)
    assert strong.status == "ACTIVE"
    cap = TradingEngine._measured_kelly_cap(strong, cfg, 5_00_00_000.0)
    assert cap == max(strong.fraction * 5_00_00_000.0,
                      cfg.position_slab_value)
    assert TradingEngine._measured_kelly_cap(None, cfg, 1e8) is None


def test_a_broken_estimator_still_never_stops_buying(tmp_path, monkeypatch):
    """Unchanged: a crash reads as no reading, and no reading never cuts."""
    e = _fresh(tmp_path, monkeypatch, _no_edge())

    def boom(*a, **k):
        raise RuntimeError("estimator down")

    monkeypatch.setattr("trading.measured_kelly.measure", boom)
    assert e._measured_kelly(datetime(2026, 11, 27, 4, 0)) is None
