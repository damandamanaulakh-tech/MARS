"""Pure, exploratory market/sector/flow sensitivity; never an order input.

User-approved screen wiring of the 2026-11-02 five-stock prototype. Coefficients
and the institutional-flow discount are assumptions, not fitted predictions.
"""
from __future__ import annotations

import math


def number(value, label, low, high):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} must be a number") from None
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g}")
    return result


def calculate(*, base, beta, market_pct, sector_pct, sector_coefficient=1,
              fpi_cr=0, dii_cr=0, base_margin_pct=10, max_extra_pp=5,
              flow_scale_cr=10000, sector_fallback=False):
    base = number(base, "Saved valuation", 0.000001, 1e12)
    beta = number(beta, "Saved market sensitivity", -5, 5)
    market = number(market_pct, "Market move (%)", -90, 200)
    sector = market if sector_fallback else number(sector_pct, "Sector move (%)", -90, 200)
    coefficient = number(sector_coefficient, "Sector coefficient", 0, 5)
    fpi = number(fpi_cr, "FPI flow (crore)", -1e7, 1e7)
    dii = number(dii_cr, "DII flow (crore)", -1e7, 1e7)
    margin = number(base_margin_pct, "Base discount (%)", 0, 90)
    cap = number(max_extra_pp, "Maximum extra discount (points)", 0, 50)
    scale = number(flow_scale_cr, "Flow scale (crore)", 1, 1e7)
    if margin + cap >= 100:
        raise ValueError("Base discount plus maximum extra discount must be below 100%")
    market_factor = 1 + beta * market / 100
    relative = sector - market
    sector_factor = 1 + coefficient * relative / 100
    if min(market_factor, sector_factor) <= 0:
        raise ValueError("These assumptions produce a non-positive valuation factor; reduce the shock or coefficient")
    after_market = base * market_factor
    target = after_market * sector_factor
    extra = min(cap, max(0, -(fpi + dii) / scale * cap))
    total_margin = margin + extra
    entry = target * (1 - total_margin / 100)
    warnings = []
    if sector_fallback:
        warnings.append("No independent sector mapping: only the market adjustment is applied.")
    if (market < 0 and sector < 0 and target > base) or (market > 0 and sector > 0 and target < base):
        warnings.append("Calibration warning: the target moves against both market and sector. Review the saved beta and assumed sector coefficient together.")
    return {"base": base, "beta": beta, "market_pct": market, "sector_pct": sector,
            "sector_relative_pp": relative, "market_impact_rs": after_market - base,
            "sector_impact_rs": target - after_market, "adjusted_target": target,
            "target_change_pct": (target / base - 1) * 100,
            "net_flow_cr": fpi + dii, "extra_flow_margin_pp": extra,
            "total_margin_pct": total_margin, "illustrative_entry_ceiling": entry,
            "entry_margin_rs": target - entry, "warnings": warnings}
