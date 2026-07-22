"""Sourceborn AUTO — automated daily pipeline for the Nifty200 momentum engine.

DESIGN LAW (SB-URR): full autonomy is EARNED through gates, never assumed.
The ladder below is enforced in code. Any gate failure downgrades the mode,
journals the reason, and never trades blind.

    DRY_RUN      full pipeline, orders only logged
    PAPER        orders go to the paper portfolio
    LIVE_CONFIRM real orders, each held for one human tap
    LIVE_AUTO    real orders placed automatically  <- must pass ALL gates

GATES (checked at every run):
  G1 PIT_INTEGRITY   latest backtest tagged POINT-IN-TIME (the P1 fix) — an
                     auto system trading a survivorship-biased edge is auto-
                     mating a lie; the code refuses.
  G2 PAPER_SEASONING >= cfg.min_paper_days successful PAPER days recorded.
  G3 KILL_SWITCH     data/KILL absent. Touch the file -> everything halts.
  G4 TOKEN_FRESH     Upstox access token valid (expires ~3:30 AM IST daily).
                     Invalid -> halt + alert. Never trade on a dead session.
  G5 SEBI_ALGO_ID    cfg.algo_id non-empty. Since 1 Apr 2026 every algo order
                     must carry the exchange Algo-ID or it is rejected.
  G6 ORDER_BUDGET    per-run order count + notional caps + price sanity band.
  G7 DRAWDOWN_BRAKE  live equity drawdown > cfg.max_dd_pct -> no NEW entries
                     (exits still allowed), alert raised.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

MODES = ["DRY_RUN", "PAPER", "LIVE_CONFIRM", "LIVE_AUTO"]
PIT_CLEAN = "POINT-IN-TIME"


@dataclass
class AutoConfig:
    mode: str = "DRY_RUN"
    algo_id: str = ""                  # exchange-assigned, via Upstox registration
    min_paper_days: int = 30
    max_orders_per_run: int = 12
    max_notional_pct: float = 0.25     # of portfolio equity per run
    price_band_pct: float = 0.10       # reject orders >10% from prev close
    max_dd_pct: float = 0.12           # drawdown brake
    kill_file: str = "data/KILL"
    journal_path: str = "data/auto_journal.jsonl"
    state_path: str = "data/auto_state.json"


@dataclass
class RunContext:
    today: date
    token_valid: bool
    latest_backtest_universe_tags: list[str]
    paper_days_completed: int
    equity: float
    peak_equity: float
    prev_close: dict[str, float] = field(default_factory=dict)


@dataclass
class Order:
    ticker: str
    side: str          # BUY / SELL
    qty: int
    ref_price: float
    is_exit: bool = False


class GateResult:
    def __init__(self):
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    @property
    def ok(self) -> bool:
        return not self.failed


def journal(cfg: AutoConfig, event: str, **kw):
    os.makedirs(os.path.dirname(cfg.journal_path) or ".", exist_ok=True)
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **kw}
    with open(cfg.journal_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def check_gates(cfg: AutoConfig, ctx: RunContext, orders: list[Order]) -> GateResult:
    g = GateResult()

    def gate(name, ok, why_fail):
        (g.passed.append(name) if ok else g.failed.append((name, why_fail)))

    gate("G3_KILL_SWITCH", not os.path.exists(cfg.kill_file),
         f"kill file present: {cfg.kill_file}")
    gate("G4_TOKEN_FRESH", ctx.token_valid,
         "Upstox token invalid/expired — re-authorize before any run")

    if cfg.mode in ("LIVE_CONFIRM", "LIVE_AUTO"):
        gate("G5_SEBI_ALGO_ID", bool(cfg.algo_id.strip()),
             "no exchange Algo-ID configured — SEBI framework (mandatory since "
             "2026-04-01) rejects untagged algo orders")

    if cfg.mode == "LIVE_AUTO":
        gate("G1_PIT_INTEGRITY",
             ctx.latest_backtest_universe_tags == [PIT_CLEAN],
             f"backtest universe tags = {ctx.latest_backtest_universe_tags}; "
             "LIVE_AUTO requires a clean POINT-IN-TIME validated backtest")
        gate("G2_PAPER_SEASONING", ctx.paper_days_completed >= cfg.min_paper_days,
             f"only {ctx.paper_days_completed}/{cfg.min_paper_days} paper days")

    gate("G6_ORDER_COUNT", len(orders) <= cfg.max_orders_per_run,
         f"{len(orders)} orders > cap {cfg.max_orders_per_run}")
    notional = sum(o.qty * o.ref_price for o in orders if o.side == "BUY")
    gate("G6_NOTIONAL", notional <= cfg.max_notional_pct * ctx.equity,
         f"buy notional {notional:.0f} > {cfg.max_notional_pct:.0%} of equity")
    for o in orders:
        pc = ctx.prev_close.get(o.ticker)
        if pc and abs(o.ref_price - pc) / pc > cfg.price_band_pct:
            gate("G6_PRICE_BAND", False,
                 f"{o.ticker} ref {o.ref_price} vs prev close {pc} outside "
                 f"±{cfg.price_band_pct:.0%}")
            break
    else:
        g.passed.append("G6_PRICE_BAND")

    dd = 0.0 if ctx.peak_equity <= 0 else (ctx.peak_equity - ctx.equity) / ctx.peak_equity
    entries = [o for o in orders if not o.is_exit and o.side == "BUY"]
    gate("G7_DRAWDOWN_BRAKE", not (dd > cfg.max_dd_pct and entries),
         f"drawdown {dd:.1%} > {cfg.max_dd_pct:.0%} — new entries blocked "
         "(exits remain allowed)")
    return g


def effective_mode(cfg: AutoConfig, gates: GateResult) -> str:
    """LIVE_AUTO downgrades to LIVE_CONFIRM, LIVE_* to DRY_RUN on failure."""
    if gates.ok:
        return cfg.mode
    hard = {n for n, _ in gates.failed}
    if {"G3_KILL_SWITCH", "G4_TOKEN_FRESH"} & hard:
        return "HALT"
    if cfg.mode == "LIVE_AUTO":
        return "LIVE_CONFIRM"
    if cfg.mode in ("LIVE_CONFIRM", "PAPER"):
        return "DRY_RUN"
    return cfg.mode


def run_daily(cfg: AutoConfig, ctx: RunContext,
              build_orders: Callable[[], list[Order]],
              place_live: Optional[Callable[[Order, str], dict]] = None,
              place_paper: Optional[Callable[[Order], dict]] = None) -> dict:
    """One scheduled run. Returns a summary dict; everything journaled."""
    orders = build_orders()
    gates = check_gates(cfg, ctx, orders)
    mode = effective_mode(cfg, gates)
    journal(cfg, "gate_check", requested=cfg.mode, effective=mode,
            passed=gates.passed, failed=gates.failed, n_orders=len(orders))

    placed, held = [], []
    if mode == "HALT":
        journal(cfg, "halt", reason=[f for f in gates.failed])
        return {"mode": mode, "placed": [], "held": [o.ticker for o in orders]}

    for o in orders:
        if mode == "DRY_RUN":
            journal(cfg, "dry_order", **o.__dict__)
        elif mode == "PAPER":
            (place_paper or (lambda x: {}))(o)
            journal(cfg, "paper_order", **o.__dict__)
        elif mode == "LIVE_CONFIRM":
            held.append(o.ticker)
            journal(cfg, "order_held_for_confirm", **o.__dict__)
        elif mode == "LIVE_AUTO":
            resp = (place_live or (lambda x, aid: {}))(o, cfg.algo_id)
            placed.append(o.ticker)
            journal(cfg, "live_order", algo_id=cfg.algo_id,
                    broker_resp=str(resp)[:200], **o.__dict__)
    return {"mode": mode, "placed": placed, "held": held,
            "gates_failed": gates.failed}
