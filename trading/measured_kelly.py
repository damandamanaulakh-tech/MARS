"""Kelly measured from the book's OWN closed trades — never from an assumption.

OWNER ORDER 2026-09-10, after reviewing the three repos: "Copy Ashstock ...
wire ash stocks good part - (f = p - (1-p)/b measured from ITS OWN closed
trades, applied = max(0, f) * 0.25 * min(1, trades/300))", with the fraction
later set to 0.29 ("prime number").

WHY THIS EXISTS. Every Kelly number this app has ever shown rested on
`kelly_ic = 0.05`, an ASSUMPTION nobody measured. Assumed-IC Kelly has no
error budget: if the true edge is a quarter of the assumed one, the bet is
2x-Kelly, where growth is zero and ruin is certain. AshStocks solved this
properly and it is the best-designed piece in any of the three repos —
measure the edge from the trades you actually closed, shrink it toward a coin
flip until you have enough of them, and let the answer only ever CUT a size.

THE FOUR DEFENCES, all from their design and all kept:

  1. Beta prior. `p` is smoothed toward 0.5 with `PRIOR_TRADES` pseudo-trades,
     so twelve lucky wins cannot read as a 100% win rate.
  2. Return clipping at +/- CLIP_PCT. One 60% winner must not set the payoff
     ratio for the whole book.
  3. Costs. AM07's ClosedTrade already records `costs`, so `net_pnl` is
     genuinely net — no reconstruction needed (this is better than the source
     design, which subtracts an assumed 0.08%).
  4. Confidence ramp. The size is multiplied by min(1, n / FULL_CONFIDENCE),
     so a thin sample sizes small even when it looks brilliant.

THE ONE THING ADDED HERE — THE REASON FILTER, and it is the important half.

The owner said "no cap on closed trades, i have already more than 100 in
that", and the count gate is duly off. But a count was never the real
question. AM07's closed book is dominated by exits that measure the OWNER and
a historical tagging bug rather than the strategy: 45 sales were SQUARE_OFF on
stale intraday tags, plus every OWNER_SELL, OWNER_CLOSE_ALL and
OWNER_SECTOR_EXIT, plus the portfolio-level KILL_SWITCH and DRAWDOWN_LADDER
liquidations, which are one decision applied to N names and not N verdicts.
Feeding those to a win-rate estimator is not a conservative choice or an
aggressive one — it is measuring the wrong thing. So they are excluded, the
breakdown is returned for the screen, and the owner sees the real usable count
before a rupee is sized on it.

THE CATEGORY FILTER (owner order 2026-10-05: "make it momentum only").
One pooled reading judged all five categories by each other's record — a
run of losing intraday or swing exits could stop momentum buying, and the
owner's 10-05 "why not buying" was most likely that. Now `measure(...,
category=)` counts only trades of ONE category, and the engine runs the
check only in that category's pass. `trade_category()` reads the trade's
own `mode` field (written at close since 2026-10-05); a legacy trade with
no field is classified from the record it carries — the reasoning text and
tags each entry path writes — never guessed. A legacy trade that cannot be
placed is "other" and left out of a category reading, counted on screen.

BOUNDARY: pure. Reads a list of ClosedTrade-shaped objects, returns numbers.
No network, no config mutation, no engine/risk/orders import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

# --- the estimator's constants, each with its reason -----------------------
# Beta prior: 25 wins + 25 losses of pseudo-evidence. AshStocks uses
# priorEquivalentTrades 50 at priorWinRate 0.5; same thing, stated as counts.
PRIOR_TRADES = 50.0
PRIOR_WIN_RATE = 0.5
# Symmetric clip on a single trade's net return, so one outlier cannot set b.
CLIP_PCT = 12.0
# Full confidence at 300 closed trades — the ramp below it is linear.
FULL_CONFIDENCE = 300

# Exit reasons that are a VERDICT ON THE TRADE. These feed the estimator.
STRATEGY_REASONS = frozenset({
    "STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP", "MAX_HOLD",
    "SCORE_DECAY", "ROTATION_EXIT",
    # item 19 (2026-10-21): the re-marked valuation target fell to or below
    # the month-end close — an exit rule's verdict, like TAKE_PROFIT
    "VAL_TARGET_DOWN",
})
# Prefix match too — the danger layer writes a described reason like
# "DANGER_EXIT_HALF (ATR Shock + Close Low ...)".
STRATEGY_PREFIXES = ("DANGER",)

# Exit reasons that are NOT a verdict on the trade, with the reason why.
EXCLUDED_REASONS = {
    "SQUARE_OFF": "intraday tag square-off, not a strategy exit",
    "SQUARE_OFF_HELD_NO_QUOTE": "square-off with no quote",
    "OWNER_SELL": "the owner sold it by hand",
    "OWNER_CLOSE_ALL": "the owner closed the book",
    "OWNER_SECTOR_EXIT": "the owner exited a whole sector",
    "KILL_SWITCH": "portfolio halt — one decision, not a per-name verdict",
    "DRAWDOWN_LADDER": "portfolio crash brake — same",
    "RETAG_EXITS": "mode retag, not a market outcome",
}


def classify(reason: Optional[str]) -> str:
    """'strategy' | 'excluded' | 'unknown' for one exit reason."""
    r = str(reason or "").strip().upper()
    if not r:
        return "unknown"
    head = r.split(" ")[0].split("(")[0].strip()
    if head in STRATEGY_REASONS:
        return "strategy"
    if head in EXCLUDED_REASONS:
        return "excluded"
    if any(head.startswith(p) for p in STRATEGY_PREFIXES):
        return "strategy"
    return "unknown"


MOMENTUM = "nifty200_momentum"
# Entry paths and the mark each leaves on a position's record (verified in
# engine.scan / _ocean_picks / scan_routes.owner_buy / backtest):
#   momentum scan   reasoning "MOM 72 + QUALITY 60 (blend 35%) = 68"
#   backtest        reasoning "NIFTY200_MOMENTUM: 6m+12m sharpe 68.0/100"
#   ocean           tag "ocean", reasoning "OCEAN: ..."
#   owner BUY/desk  tags owner_pick / buy_desk, reasoning "OWNER BUY CALL"
#                   (counted as momentum — the category_budget convention)
#   other four      reasoning_text() over the composite components
_MOMENTUM_PREFIXES = ("MOM ", "NIFTY200_MOMENTUM")
_OWNER_TAGS = ("owner_pick", "buy_desk")


def trade_category(trade: Any) -> str:
    """The category a closed trade belongs to: the mode recorded at close,
    else the category its own record proves. 'other' when it cannot be
    placed (a legacy swing/short/positional/intraday exit)."""
    mode = str(getattr(trade, "mode", "") or "").strip()
    tags = [str(x) for x in (getattr(trade, "tags", None) or [])]
    if "ocean" in tags:
        return "ocean"
    if mode:
        return mode
    why = str(getattr(trade, "reasoning", "") or "").strip()
    if why.upper().startswith("OCEAN"):
        return "ocean"
    if any(why.startswith(p) for p in _MOMENTUM_PREFIXES):
        return MOMENTUM
    if why.upper().startswith("OWNER BUY") or any(t in tags for t in _OWNER_TAGS):
        return MOMENTUM
    return "other"


def net_return(trade: Any) -> Optional[float]:
    """Net-of-cost return on the entry notional, as a fraction.

    AM07's ClosedTrade carries `costs` and `net_pnl`, so this is the real
    number and not a reconstruction. Returns None when the record cannot
    prove itself — a trade with no entry notional is not a 0% trade.
    """
    try:
        qty = float(getattr(trade, "qty", 0) or 0)
        entry = float(getattr(trade, "entry_price", 0) or 0)
        net = getattr(trade, "net_pnl", None)
        if net is None:
            gross = float(getattr(trade, "gross_pnl", 0) or 0)
            net = gross - float(getattr(trade, "costs", 0) or 0)
        notional = qty * entry
        if notional <= 0:
            return None
        return float(net) / notional
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class KellyReading:
    """Everything the screen needs. Never just a number."""
    status: str                     # NO_SAMPLE | NO_POSITIVE_EDGE | ACTIVE
    usable: int                     # trades that fed the estimate
    excluded: int
    unknown: int
    by_reason: dict                 # reason -> count, over ALL closed trades
    wins: int = 0
    losses: int = 0
    win_rate: Optional[float] = None          # Beta-smoothed
    raw_win_rate: Optional[float] = None      # what actually happened
    payoff_ratio: Optional[float] = None      # b = mean win / |mean loss|
    raw_kelly: Optional[float] = None         # f = p - (1-p)/b
    confidence: float = 0.0                   # min(1, n / FULL_CONFIDENCE)
    fraction: Optional[float] = None          # applied = max(0,f) * m * conf
    max_position_pct: Optional[float] = None  # the CAP this puts on a name
    block_new_entries: bool = False
    note: str = ""
    category: Optional[str] = None            # None = all categories pooled
    other_category: int = 0                   # trades left out for category

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["by_reason"] = dict(self.by_reason)
        return d


def measure(trades: Iterable[Any], fraction: float = 0.29,
            min_trades: int = 0,
            full_confidence: int = FULL_CONFIDENCE,
            clip_pct: Optional[float] = None,
            category: Optional[str] = None) -> KellyReading:
    """Measure Kelly from closed trades. Pure.

    `fraction` is the owner's m. `min_trades` 0 honours their "no cap on
    closed trades" — the sample gate is off, the reason filter is not.
    `clip_pct` overrides CLIP_PCT. AUDIT F-9 (2026-10-02): the engine passes
    the live momentum target (+20%), because a +20% TAKE_PROFIT is the
    design's normal win, not an outlier — clipped at 12% every target exit
    would read as +12%, understating the payoff ratio by 40% and pushing the
    check toward a wrongful "no edge" block.
    `category` (owner order 2026-10-05, "make it momentum only"): count only
    trades of that category (see trade_category); None pools all.
    """
    clip = float(CLIP_PCT if clip_pct is None else clip_pct) / 100.0
    by_reason: dict = {}
    usable: list = []
    excluded = unknown = other_cat = 0
    for t in trades or []:
        if category is not None and trade_category(t) != category:
            other_cat += 1
            continue
        raw = str(getattr(t, "reason", "") or "")
        head = raw.split(" ")[0].split("(")[0].strip().upper() or "(none)"
        by_reason[head] = by_reason.get(head, 0) + 1
        kind = classify(raw)
        if kind == "excluded":
            excluded += 1
            continue
        if kind == "unknown":
            unknown += 1
            continue
        r = net_return(t)
        if r is None:
            unknown += 1
            continue
        usable.append(max(-clip, min(clip, r)))

    n = len(usable)
    wins = [r for r in usable if r > 0]
    losses = [r for r in usable if r < 0]
    base = dict(usable=n, excluded=excluded, unknown=unknown,
                by_reason=by_reason, wins=len(wins), losses=len(losses),
                category=category, other_category=other_cat)

    if n < max(1, int(min_trades)) or not wins or not losses:
        return KellyReading(
            status="NO_SAMPLE", block_new_entries=False, **base,
            note=(f"{n} usable strategy exit(s) — need at least one win AND "
                  f"one loss to measure a payoff ratio. The size cap is OFF "
                  f"until then; the slab model sizes on its own."),
        )

    raw_wr = len(wins) / n
    p = (len(wins) + PRIOR_TRADES * PRIOR_WIN_RATE) / (n + PRIOR_TRADES)
    mean_win = sum(wins) / len(wins)
    mean_loss = abs(sum(losses) / len(losses))
    if mean_loss <= 0:
        return KellyReading(status="NO_SAMPLE", block_new_entries=False, **base,
                            note="every loss was exactly zero — no payoff ratio")
    b = mean_win / mean_loss
    f = p - (1 - p) / b
    conf = min(1.0, n / float(max(1, full_confidence)))
    applied = max(0.0, f) * float(fraction) * conf

    if f <= 0 or applied <= 0:
        return KellyReading(
            status="NO_POSITIVE_EDGE", win_rate=p, raw_win_rate=raw_wr,
            payoff_ratio=b, raw_kelly=f, confidence=conf, fraction=0.0,
            max_position_pct=0.0, block_new_entries=True, **base,
            note=(f"Measured on {n} real exits: win rate {raw_wr*100:.0f}%, "
                  f"payoff {b:.2f}. Kelly is {f:+.3f} — the book has NO "
                  f"measured edge after costs, so new entries are blocked."),
        )

    return KellyReading(
        status="ACTIVE", win_rate=p, raw_win_rate=raw_wr, payoff_ratio=b,
        raw_kelly=f, confidence=conf, fraction=applied,
        max_position_pct=applied * 100.0, block_new_entries=False, **base,
        note=(f"Measured on {n} real exits ({excluded} owner/portfolio exits "
              f"excluded): win rate {raw_wr*100:.0f}% (smoothed "
              f"{p*100:.0f}%), payoff {b:.2f}, Kelly {f:.3f}. At m="
              f"{fraction} and {conf*100:.0f}% confidence the cap is "
              f"{applied*100:.2f}% of equity per name."),
    )


def live_clip_pct() -> float:
    """The clip the live check uses: the larger of CLIP_PCT and the momentum
    preset's target (+20% since the owner's Node 12 pick). Reads the preset
    table only — no config mutation, no engine import."""
    try:
        from .modes import PRESETS
        tp = float(PRESETS["nifty200_momentum"].take_profit_pct) * 100.0
    except Exception:
        tp = 0.0
    return max(CLIP_PCT, tp)


def growth_vs_ruin(m: float) -> dict:
    """The owner's item 2, as numbers rather than a config comment.

    g(m) is proportional to m - m^2/2, so relative to full Kelly the growth
    kept is 2m - m^2. The probability of EVER halving the book under
    fractional Kelly m is 0.5 ** (2/m - 1). The error budget is 2/m: how many
    times worse the true edge can be than the assumed one before the bet is
    2x-Kelly, where growth is zero and ruin certain.
    """
    m = float(m)
    if m <= 0:
        return {"m": m, "growth_kept_pct": 0.0, "halving_risk_pct": 0.0,
                "error_budget_x": None}
    return {
        "m": round(m, 4),
        "growth_kept_pct": round((2 * m - m * m) * 100, 1),
        "halving_risk_pct": round(0.5 ** (2 / m - 1) * 100, 2),
        "error_budget_x": round(2.0 / m, 1),
    }


LADDER_FRACTIONS = (0.25, 0.29, 0.3333, 0.5, 0.75, 1.0)


def growth_ladder(live_m: float) -> list[dict]:
    """The table for the screen, with the live fraction marked."""
    out = []
    for m in sorted(set(LADDER_FRACTIONS) | {round(float(live_m), 4)}):
        row = growth_vs_ruin(m)
        row["live"] = abs(m - float(live_m)) < 1e-9
        out.append(row)
    return out
