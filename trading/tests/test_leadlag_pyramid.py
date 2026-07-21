"""F3 lead-lag risk-off + F15 pyramiding (no network)."""

import numpy as np
import pandas as pd

from trading.config import Config
from trading.paper import PaperPosition
from trading.pyramid import pyramid_plan, apply_pyramid
from trading.regime import classify, lead_lag_risk_off
from datetime import datetime


def test_lead_lag_detects_cascade():
    # sharp 1-day drop + VIX spike -> risk-off fires
    nifty = pd.Series([100, 100, 100, 100, 100, 97.5])   # -2.5% last day
    vix = pd.Series([12, 12, 12, 12, 12, 15])            # +25% in 5d
    fired, why = lead_lag_risk_off(nifty, vix)
    assert fired and "1-day" in why
    # a >3.5% single-day fall alone fires without VIX
    nifty2 = pd.Series([100, 100, 96.0])
    assert lead_lag_risk_off(nifty2, None)[0]
    # calm tape -> no fire
    assert not lead_lag_risk_off(pd.Series([100, 100.5, 101]), pd.Series([12]*6))[0]


def test_classify_leadlag_escalation():
    # otherwise-bull tape but risk-off cascade -> defensive correction early
    r = classify(nifty_close=110, nifty_200dma=100, pct_below_52w_high=-3,
                 vix=14, fii_5d_net=None, global_risk_off=True)
    assert r[0] == "correction" and not r[1]      # everest off
    # deep off high + risk-off -> panic escalation
    r2 = classify(nifty_close=88, nifty_200dma=100, pct_below_52w_high=-11,
                  vix=20, fii_5d_net=None, global_risk_off=True)
    assert r2[0] == "panic"
    # same inputs WITHOUT risk-off stay correction (not panic)
    r3 = classify(nifty_close=88, nifty_200dma=100, pct_below_52w_high=-11,
                  vix=20, fii_5d_net=None, global_risk_off=False)
    assert r3[0] == "correction"


def _pos(entry=100.0, qty=100):
    return PaperPosition(ticker="P.NS", qty=qty, entry_price=entry,
                         entry_date=datetime(2026, 6, 1), sector="IT",
                         peak_price=entry, entry_score=70.0, reasoning="t",
                         orig_qty=qty)


def test_pyramid_plan_and_apply():
    cfg = Config()
    cfg.pyramid_enabled = True
    cfg.pyramid_trigger_pct = 0.08
    cfg.pyramid_add_pct = 0.5
    cfg.max_pyramid_adds = 2
    pos = _pos(entry=100.0, qty=100)
    # up 5% -> below first trigger (8%)
    assert pyramid_plan(pos, 105.0, 1e9, cfg) is None
    # up 9% -> first tranche of 50 shares
    plan = pyramid_plan(pos, 109.0, 1e9, cfg)
    assert plan == (50, 50 * 109.0)
    apply_pyramid(pos, 50, 109.0)
    assert pos.qty == 150 and pos.pyramid_adds == 1
    # weighted avg entry: (100*100 + 109*50)/150
    assert abs(pos.entry_price - (100*100 + 109*50)/150) < 1e-6
    # second tranche needs +16% from ORIGINAL entry; at 109 avg entry ~103,
    # need price >= 116 for step 2 (0.08*2 from entry_price now 103) -> ~111.2
    assert pyramid_plan(pos, 111.0, 1e9, cfg) is None
    plan2 = pyramid_plan(pos, 130.0, 1e9, cfg)
    assert plan2 is not None
    apply_pyramid(pos, plan2[0], 130.0)
    assert pos.pyramid_adds == 2
    # maxed out
    assert pyramid_plan(pos, 200.0, 1e9, cfg) is None


def test_pyramid_disabled_by_default():
    cfg = Config()
    assert cfg.pyramid_enabled is False
    assert pyramid_plan(_pos(), 200.0, 1e9, cfg) is None


def test_pyramid_respects_cash():
    cfg = Config(); cfg.pyramid_enabled = True
    pos = _pos(entry=100.0, qty=100)
    # only enough cash for 10 shares
    plan = pyramid_plan(pos, 110.0, 1100.0, cfg)
    assert plan is not None and plan[0] == 10
