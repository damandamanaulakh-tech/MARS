"""Dual-regime engines: Everest (momentum/breakout) and Ocean (deep reversion).

The master switch in trading.regime decides which engine is active.
Both engines share the same underlying scoring + risk modules but apply
fundamentally different selection logic:

  Everest: momentum, breakout, relative strength, FII accumulation
  Ocean:   deep value, capitulation volume, oversold RSI, tranche entry
"""
