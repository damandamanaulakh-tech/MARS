"""Sourceborn AUTOSWAP — hold the target position count automatically.

The gap this closes: when a position exits mid-cycle the slot sits empty until
the next rebalance. AUTOSWAP refills it from the ranked queue the scan already
produced — so the book stays at target count without a human watching.

SB-URR discipline (this is the whole point — a swap is a TRADE, not a tidy-up):
  * every swap runs the SAME gates as a fresh entry (auto.check_gates)
  * a replacement must clear the score floor AND beat the exited name's score
  * sector caps still bind — no silent concentration
  * cooldown: a name that just exited cannot be re-bought for N days
  * every swap writes an entry to the journal with the reason
  * DRY_RUN/PAPER/LIVE ladder is inherited, never bypassed
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .auto import AutoConfig, RunContext, Order, check_gates, effective_mode, journal


@dataclass
class SwapConfig:
    target_count: int = 10          # how many positions the book should hold
    min_score: float = 60.0         # replacement must clear this
    must_beat_exited: bool = True   # and must outrank what left
    max_per_sector: int = 3
    cooldown_days: int = 5          # no re-buying a name you just exited
    max_swaps_per_run: int = 3      # churn brake


@dataclass
class Position:
    ticker: str
    sector: str
    entry_score: float


@dataclass
class Candidate:
    ticker: str
    sector: str
    score: float
    last_close: float


@dataclass
class SwapPlan:
    orders: list[Order] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def plan_swaps(scfg: SwapConfig,
               open_positions: list[Position],
               ranked: list[Candidate],
               recent_exits: dict[str, date],
               today: date,
               equity: float,
               exited_scores: Optional[list[float]] = None) -> SwapPlan:
    """Decide which names refill empty slots. Pure function — no side effects."""
    plan = SwapPlan()
    open_n = len(open_positions)
    slots = scfg.target_count - open_n
    if slots <= 0:
        plan.skipped.append(f"book full: {open_n}/{scfg.target_count}")
        return plan

    held = {p.ticker for p in open_positions}
    sector_n: dict[str, int] = {}
    for p in open_positions:
        sector_n[p.sector] = sector_n.get(p.sector, 0) + 1

    bar = max(exited_scores) if (scfg.must_beat_exited and exited_scores) else None

    for c in sorted(ranked, key=lambda r: -r.score):
        if len(plan.orders) >= min(slots, scfg.max_swaps_per_run):
            break
        if c.ticker in held:
            continue
        if c.score < scfg.min_score:
            plan.skipped.append(f"{c.ticker}: score {c.score:.1f} < floor {scfg.min_score}")
            continue
        if bar is not None and c.score <= bar:
            plan.skipped.append(
                f"{c.ticker}: score {c.score:.1f} does not beat exited {bar:.1f}")
            continue
        gone = recent_exits.get(c.ticker)
        if gone and (today - gone).days < scfg.cooldown_days:
            plan.skipped.append(
                f"{c.ticker}: cooldown, exited {(today - gone).days}d ago")
            continue
        if sector_n.get(c.sector, 0) >= scfg.max_per_sector:
            plan.skipped.append(f"{c.ticker}: sector {c.sector} at cap")
            continue

        qty = max(1, int((equity / scfg.target_count) / max(c.last_close, 0.01)))
        plan.orders.append(Order(c.ticker, "BUY", qty, c.last_close))
        plan.reasons.append(
            f"SWAP-IN {c.ticker} score {c.score:.1f} -> fills slot "
            f"{open_n + len(plan.orders)}/{scfg.target_count}"
            + (f", beats exited {bar:.1f}" if bar is not None else ""))
        sector_n[c.sector] = sector_n.get(c.sector, 0) + 1
        held.add(c.ticker)
    return plan


def run_autoswap(cfg: AutoConfig, scfg: SwapConfig, ctx: RunContext,
                 open_positions: list[Position], ranked: list[Candidate],
                 recent_exits: dict[str, date],
                 exited_scores: Optional[list[float]] = None,
                 place_live=None, place_paper=None) -> dict:
    """Plan swaps, then run them through the FULL gate ladder. No shortcuts."""
    plan = plan_swaps(scfg, open_positions, ranked, recent_exits,
                      ctx.today, ctx.equity, exited_scores)
    if not plan.orders:
        journal(cfg, "autoswap_none", skipped=plan.skipped[:8])
        return {"mode": cfg.mode, "placed": [], "skipped": plan.skipped}

    gates = check_gates(cfg, ctx, plan.orders)
    mode = effective_mode(cfg, gates)
    journal(cfg, "autoswap_gate", requested=cfg.mode, effective=mode,
            failed=gates.failed, reasons=plan.reasons)
    if mode == "HALT":
        return {"mode": "HALT", "placed": [], "skipped": plan.skipped}

    placed = []
    for o, why in zip(plan.orders, plan.reasons):
        if mode == "DRY_RUN":
            journal(cfg, "autoswap_dry", why=why, **o.__dict__)
        elif mode == "PAPER":
            (place_paper or (lambda x: None))(o)
            journal(cfg, "autoswap_paper", why=why, **o.__dict__)
        elif mode == "LIVE_CONFIRM":
            journal(cfg, "autoswap_held_for_confirm", why=why, **o.__dict__)
        elif mode == "LIVE_AUTO":
            (place_live or (lambda x, a: None))(o, cfg.algo_id)
            placed.append(o.ticker)
            journal(cfg, "autoswap_live", why=why, algo_id=cfg.algo_id, **o.__dict__)
    return {"mode": mode, "placed": placed, "reasons": plan.reasons,
            "skipped": plan.skipped}
