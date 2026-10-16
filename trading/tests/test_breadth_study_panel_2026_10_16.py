"""The breadth gate reads the population its band was measured on (2026-10-16).

Owner: "new position are not added, its not using the all 5 trade
segements as well" -> "relook n fix". The gate blocks ALL FIVE categories
inside 40-65%, a band FITTED on the breadth of the committed 191-name study
panel; the live gate compared it with the whole store (and its fallback with
the ~1,200-name scan list), populations that sat 5-7pp away and agreed with
the panel's decision on only 65-68% of 2026's sessions. Since 09-24 the panel
read 24-32% (buyable) while the whole store hovered on the 40% line and the
scan-list fallback read 50-53% (blocked).

Pinned: the panel list is the study's own; the stored reading is the panel's
with the whole store beside it; stale or missing panel names never vote; a
thin panel degrades to the old whole-store reading; a reading taken before
this fix is not used; the scan's own reading is taken over the panel names;
and the gate follows the PANEL when the two populations disagree.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import inspect

from trading.breadth import (PANEL_MIN_SAMPLE, PANEL_STALE_DAYS, gate_breadth,
                             study_panel)
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio

NOW = datetime(2026, 10, 16, 4, 0)
PANEL = sorted(study_panel())


def _doc(t, up, end="2026-10-15"):
    closes = list(range(100, 300)) if up else list(range(300, 100, -1))
    return {"ticker": t, "end": end,
            "candles": [[end, c, c, c, c, 0.0] for c in closes]}


def _store(panel_up: int, panel_down: int, rest_up: int, rest_down: int):
    names = PANEL[:panel_up + panel_down]
    docs = [_doc(t, i < panel_up) for i, t in enumerate(names)]
    docs += [_doc(f"R{i}.NS", i < rest_up) for i in range(rest_up + rest_down)]
    return docs


def test_the_panel_is_the_studys_191_names():
    assert len(PANEL) == 191
    for t in ("RELIANCE.NS", "HDFCBANK.NS", "TCS.NS", "SBIN.NS"):
        assert t in PANEL
    assert not any("MANIFEST" in t for t in PANEL)


def test_the_stored_reading_is_the_panel_with_the_whole_store_beside_it():
    # panel 60 of 160 above (37.5%); whole store adds 1,500 names all above
    r = gate_breadth(_store(60, 100, 1500, 0))
    assert r["population"] == "study_panel"
    assert r["sample"] == 160 and r["pct"] == 0.375
    assert r["full_sample"] == 1660
    assert r["full_pct"] == round(1560 / 1660, 4)


def test_a_stale_panel_name_does_not_vote_and_a_thin_panel_degrades():
    docs = _store(60, 100, 1500, 0)
    old = (pd.Timestamp("2026-10-15")
           - pd.Timedelta(days=PANEL_STALE_DAYS + 3)).date().isoformat()
    docs.append(_doc(PANEL[170], True, end=old))
    assert gate_breadth(docs)["sample"] == 160          # the stale one left
    thin = gate_breadth(_store(10, 10, 1500, 0))        # 20 panel names
    assert PANEL_MIN_SAMPLE > 20
    assert thin["population"] == "full_store"           # the pre-fix reading
    assert thin["sample"] == 1520


def test_the_reading_is_dated_by_the_names_that_voted():
    """The store's newest close came from a name OUTSIDE the panel; the panel
    names all stopped at their last close. The reading must carry the panel's
    date, or a stale panel reading would pass the gate's freshness test."""
    docs = _store(60, 100, 0, 0)
    old = (pd.Timestamp("2026-10-15")
           - pd.Timedelta(days=PANEL_STALE_DAYS - 2)).date().isoformat()
    for d in docs:
        d["end"] = old
        d["candles"] = [[old] + c[1:] for c in d["candles"]]
    docs += [_doc(f"R{i}.NS", True) for i in range(1500)]   # end 2026-10-15
    r = gate_breadth(docs)
    assert r["population"] == "study_panel" and r["sample"] == 160
    assert r["asof"] == old
    assert r["full_sample"] == 1660


def _engine():
    cfg = Config(mode="nifty200_momentum")
    cfg.apply_mode_preset()
    cfg.breadth_gate_enabled = True
    return TradingEngine(cfg, Portfolio.fresh(cfg))


def test_the_gate_follows_the_panel_when_the_populations_disagree():
    """The live situation since 09-24: the whole store inside the band, the
    panel below it. The gate must read the panel — and the other way round
    it must block."""
    e = _engine()
    # panel 30% above, whole store 45% above
    e.portfolio.breadth_store = gate_breadth(_store(48, 112, 700, 600))
    assert e.portfolio.breadth_store["full_pct"] >= 0.40
    assert e._breadth_gate_blocks({}, NOW, n_picks=5) is False
    # panel 50% above, whole store 35% above
    e2 = _engine()
    e2.portfolio.breadth_store = gate_breadth(_store(80, 80, 400, 1100))
    assert e2.portfolio.breadth_store["full_pct"] < 0.40
    assert e2._breadth_gate_blocks({}, NOW, n_picks=5) is True
    why = e2.portfolio.decision_journal[-1]["why"]
    assert "study panel" in why and "measured on" in why


def _history(up_names, down_names):
    idx = pd.bdate_range("2025-10-01", periods=260)
    out = {}
    for t in up_names:
        c = pd.Series(range(100, 360), index=idx, dtype=float)
        out[t] = pd.DataFrame({"Close": c})
    for t in down_names:
        c = pd.Series(range(360, 100, -1), index=idx, dtype=float)
        out[t] = pd.DataFrame({"Close": c})
    return out


def test_a_reading_taken_before_the_fix_is_not_used():
    """The live box holds a whole-store reading with no `population` until
    its next 09:20 refresh; the first scan after the deploy must not decide
    on it."""
    e = _engine()
    e.portfolio.breadth_store = {"pct": 0.45, "sample": 2233,
                                 "asof": "2026-10-15", "dma": 200}
    # the scan holds 160 panel names, 30% above -> buyable
    hist = _history(PANEL[:48], PANEL[48:160])
    assert e._breadth_gate_blocks(hist, NOW, n_picks=5) is False
    assert e.portfolio.breadth_today["population"] == "scan_panel"


def test_the_scans_own_reading_is_taken_over_the_panel_names():
    e = _engine()
    # 160 panel names 30% above, 400 other names all above
    hist = _history(PANEL[:48] + [f"X{i}.NS" for i in range(400)],
                    PANEL[48:160])
    pct, n = e._cache_breadth_today(hist, NOW)
    assert n == 160 and abs(pct - 0.30) < 1e-9
    # too few panel names -> the whole scan, labelled as such
    small = _history(PANEL[:5] + [f"X{i}.NS" for i in range(30)], PANEL[5:10])
    pct2, n2 = e._cache_breadth_today(small, NOW)
    assert n2 == 40 and e.portfolio.breadth_today["population"] == "scan"


def test_the_panel_thresholds_are_the_stated_ones():
    """The PR states 150 names and 7 days; a silent change to either moves
    which population the money gate reads, so both are pinned by behaviour
    as well as by value."""
    assert PANEL_MIN_SAMPLE == 150 and PANEL_STALE_DAYS == 7
    assert gate_breadth(_store(150, 0, 1500, 0))["population"] == "study_panel"
    assert gate_breadth(_store(149, 0, 1500, 0))["population"] == "full_store"


def test_the_whole_store_fallback_still_needs_a_thousand_names():
    """With a thin panel the whole-store reading is STORED and the gate reads
    it for all five categories, so its sample bar (~±1pp at 1,000+ names) is
    on the money path. A 310-name store must not stand in for it."""
    assert inspect.signature(gate_breadth).parameters[
        "full_min_sample"].default == 1000
    assert gate_breadth(_store(10, 10, 290, 0)) is None
    assert gate_breadth(_store(10, 10, 990, 0))["population"] == "full_store"


def _swing():
    cfg = Config(mode="swing")
    cfg.apply_mode_preset()
    cfg.breadth_gate_enabled = True
    return TradingEngine(cfg, Portfolio.fresh(cfg))


def test_a_scan_reading_taken_before_the_fix_is_not_reused():
    """The 6-month passes reuse today's momentum reading. One written before
    the fix (no `population`) is a whole-scan-list number — the population
    the band was never measured on — so it is set aside like the stored
    one; a reading written by the fixed code is used."""
    e = _swing()
    e.portfolio.breadth_today = {"pct": 0.52, "sample": 1100,
                                 "asof": NOW.date().isoformat(),
                                 "mode": "nifty200_momentum"}
    assert e._breadth_gate_blocks({}, NOW, n_picks=5) is False
    e2 = _swing()
    e2.portfolio.breadth_today = {"pct": 0.52, "sample": 170,
                                  "asof": NOW.date().isoformat(),
                                  "mode": "nifty200_momentum",
                                  "population": "scan_panel"}
    assert e2._breadth_gate_blocks({}, NOW, n_picks=5) is True
    assert "study panel names in today's scan" in \
        e2.portfolio.decision_journal[-1]["why"]
