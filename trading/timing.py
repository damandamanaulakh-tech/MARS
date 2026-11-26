"""Entry timing overlay. AM 07 picks the stock; this decides when to enter.

Rule: enter only if the stock is also breaking out (within N% of 20-day high)
AND volume is confirming (5-day vol >= 1.3x 20-day vol).

Owner decision 2026-11-26 ("two volume gates shown separately"):
  Volume Gate 1 — the TREND: the mean of the last 5 bars against the mean of
    the last 20 (each segment's own mark; Momentum's is 0 = off). It has
    always been half of the timing check above; it now also has its own
    reading and its own switch (`volume_gate1_enabled`, default on = the
    behaviour before this change).
  Volume Gate 2 — the SPIKE: the latest bar against the mean of the 20 bars
    BEFORE it, 1.5x or more. New. It refuses a buy only in the segments named
    by `volume_gate2_block_modes` (Intraday); everywhere else it is a reading.
A "bar" is whatever the pass scores on: a 5-minute bar in Intraday, a daily
bar in the other four segments.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from .config import Config
from . import decision_evidence as de


def passes_breakout(close: pd.Series, cfg: Config) -> tuple[bool, float]:
    """True iff price is within `breakout_threshold_pct` of the N-day high."""
    if len(close) < cfg.breakout_lookback_days + 1:
        return False, 0.0
    window = close.iloc[-cfg.breakout_lookback_days:]
    hi = float(window.max())
    if hi <= 0:
        return False, 0.0
    last = float(close.iloc[-1])
    pct_from_high = (last - hi) / hi
    return pct_from_high >= -cfg.breakout_threshold_pct, pct_from_high


def passes_volume_confirm(volume: pd.Series, cfg: Config) -> tuple[bool, float]:
    """True iff 5-day avg volume >= ratio * 20-day avg volume."""
    if len(volume) < cfg.volume_long_days:
        return False, 0.0
    short = float(volume.iloc[-cfg.volume_short_days:].mean())
    long_ = float(volume.iloc[-cfg.volume_long_days:].mean())
    if long_ <= 0:
        return False, 0.0
    ratio = short / long_
    return ratio >= cfg.volume_confirm_ratio, ratio


def passes_volume_spike(volume: pd.Series, cfg: Config) -> tuple[Optional[bool], Optional[float]]:
    """Volume Gate 2. The latest bar's volume divided by the mean of the
    `volume_gate2_lookback` bars before it. (None, None) when there are too
    few bars or that mean is not positive: unknown, never a pass."""
    lookback = int(getattr(cfg, "volume_gate2_lookback", 20))
    if lookback <= 0 or len(volume) < lookback + 1:
        return None, None
    last = float(volume.iloc[-1])
    base = float(volume.iloc[-lookback - 1:-1].mean())
    if not (base > 0) or last != last:
        return None, None
    ratio = last / base
    return ratio >= float(getattr(cfg, "volume_gate2_ratio", 1.5)), ratio


def gate2_blocks(cfg: Config) -> bool:
    """Does Volume Gate 2 refuse a buy in the segment `cfg` is set to?"""
    return (bool(getattr(cfg, "volume_gate2_enabled", True))
            and getattr(cfg, "mode", "") in tuple(getattr(cfg, "volume_gate2_block_modes", ()) or ()))


def volume_gates(history: pd.DataFrame, cfg: Config) -> dict:
    """Both volume gates as separate readings. Pure: records no evidence and
    decides nothing. `ok` is None when the reading cannot be made."""
    volume = history["Volume"]
    ok1, ratio1 = passes_volume_confirm(volume, cfg)
    long_days = int(cfg.volume_long_days)
    known1 = len(volume) >= long_days and float(volume.iloc[-long_days:].mean()) > 0
    ok2, ratio2 = passes_volume_spike(volume, cfg)
    need1 = float(cfg.volume_confirm_ratio)
    return {
        "gate1": {"ok": bool(ok1) if known1 else None,
                  "ratio": round(float(ratio1), 3) if known1 else None,
                  "need": need1,
                  # a mark of 0 is the segment's own "timing off" (Momentum)
                  "applies": need1 > 0,
                  "blocks": need1 > 0 and bool(getattr(cfg, "volume_gate1_enabled", True))},
        "gate2": {"ok": ok2,
                  "ratio": round(float(ratio2), 3) if ratio2 is not None else None,
                  "need": float(getattr(cfg, "volume_gate2_ratio", 1.5)),
                  "blocks": gate2_blocks(cfg)},
    }


def timing_ok(history: pd.DataFrame, cfg: Config) -> tuple[bool, dict]:
    """Combine the two checks. Returns (pass, diagnostics)."""
    b_ok, b_val = passes_breakout(history["Close"], cfg)
    v_ok, v_val = passes_volume_confirm(history["Volume"], cfg)
    # Volume Gate 1's own switch. On (the default) = the volume leg is
    # required, exactly as before. Off = it is still measured and shown, and
    # the timing check rests on the breakout leg alone.
    gate1_on = bool(getattr(cfg, "volume_gate1_enabled", True))
    de.record("breakout", b_val, -cfg.breakout_threshold_pct, passed=b_ok,
              state="unavailable" if len(history["Close"]) < cfg.breakout_lookback_days + 1 else None,
              reason=f"Distance from {cfg.breakout_lookback_days}-bar high")
    v_unavailable = (len(history["Volume"]) < cfg.volume_long_days
                     or history["Volume"].iloc[-cfg.volume_long_days:].mean() <= 0)
    if gate1_on:
        de.record("volume", v_val, cfg.volume_confirm_ratio, passed=v_ok,
                  state="unavailable" if v_unavailable else None,
                  reason=f"{cfg.volume_short_days}/{cfg.volume_long_days}-bar mean ratio")
    else:
        de.record("volume", v_val, cfg.volume_confirm_ratio, passed=v_ok,
                  state="unavailable" if v_unavailable else "passed" if v_ok else "skipped",
                  role="advisory",
                  reason=(f"{cfg.volume_short_days}/{cfg.volume_long_days}-bar mean ratio; "
                          "Volume Gate 1 is switched off: shown, not required"))
    return b_ok and (v_ok or not gate1_on), {
        "breakout_ok": b_ok, "pct_from_high": b_val,
        "volume_ok": v_ok, "vol_ratio": v_val,
        "volume_gate1_on": gate1_on,
    }
