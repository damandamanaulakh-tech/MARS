"""Entry-path fix batch, lead seam pins (owner 2026-10-22): the two items
each builder left in another builder's file.

XR-027 — an all-missing volume tail passed the one liquidity gate
(`nan < 5` is False). XR-055 caller — engine.scan passes the FII window's
own day count to fii_flow_score, not `min(len(flows), 5)`.
"""
from __future__ import annotations

import inspect
import math

import numpy as np
import pandas as pd

from trading import engine as engine_mod
from trading.risk_v2 import passes_liquidity


def _series(n: int = 40):
    idx = pd.bdate_range("2026-08-01", periods=n)
    close = pd.Series(np.linspace(100.0, 110.0, n), index=idx)
    volume = pd.Series(np.full(n, 2_000_000.0), index=idx)   # Rs 20+ Cr/day
    return close, volume


def test_a_liquid_name_still_passes():
    close, volume = _series()
    ok, why = passes_liquidity(close, volume)
    assert ok and why == ""


def test_an_all_missing_volume_tail_is_refused_not_passed():
    """XR-027: the last five bars carry no volume at all. Old code: the
    rupee-volume mean is NaN, `nan < 5` is False, the name PASSED."""
    close, volume = _series()
    volume.iloc[-5:] = np.nan
    ok, why = passes_liquidity(close, volume)
    assert ok is False
    assert why.startswith("rupee_vol unknown")
    assert "no volume in the last 5 bars" in why


def test_a_partly_missing_tail_is_judged_on_the_bars_it_has():
    """Three of five bars present: the mean over those three decides, as
    before (dropna) — the refusal is only for an ALL-missing tail."""
    close, volume = _series()
    volume.iloc[-2:] = np.nan
    ok, why = passes_liquidity(close, volume)
    assert ok is True
    assert math.isfinite(float((close.iloc[-5:] * volume.iloc[-5:]).dropna().mean()))


def _operative(src: str) -> str:
    """Source with comment lines and docstrings stripped (the source-pin
    fault class: a pin must never trip on its own explanatory comment)."""
    out = []
    in_doc = False
    for line in src.splitlines():
        s = line.strip()
        if s.startswith('"""') or s.startswith("'''"):
            if s.count('"""') == 2 or s.count("'''") == 2:
                continue
            in_doc = not in_doc
            continue
        if in_doc or s.startswith("#"):
            continue
        out.append(line.split("#", 1)[0])
    return "\n".join(out)


def test_scan_reads_the_fii_window_day_count_not_the_row_count():
    """XR-055 caller: the scan's FII leg must take (sum, n_days) from
    fii_net_window and hand n_days to fii_flow_score — `min(len(flows), 5)`
    counted five ROWS that can span weeks as a five-day window."""
    src = _operative(inspect.getsource(engine_mod.TradingEngine.scan))
    assert "fii_net_window(flows, 5)" in src
    assert "fii_flow_score(fii_5d, days=n_days or 5)" in src
    assert "min(len(flows" not in src
    # (fii_net_last_n_days is still the regime fetch's override further
    # down the same method — a different consumer, left as it is)
