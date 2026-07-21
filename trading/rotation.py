"""F6 — rotation suggester.

When an open position is most of the way to its target, its remaining upside
is small; that capital may be better recycled into a fresh top-scored pick.
This module only SUGGESTS — it never sells. Paper-only discipline: the user
decides. Suggestions surface on the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RotationSuggestion:
    ticker: str
    progress_pct: float        # % of the way from entry to target
    value: float               # current market value of the position
    ltp: float
    target: float
    fund_ticker: str | None    # top watchlist pick to rotate into
    fund_score: float | None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "progress_pct": round(self.progress_pct, 1),
            "value": round(self.value, 2),
            "ltp": round(self.ltp, 2),
            "target": round(self.target, 2),
            "fund_ticker": self.fund_ticker,
            "fund_score": self.fund_score,
        }


def rotation_suggestions(positions: dict, prices: dict,
                         watchlist: list[dict], cfg,
                         min_progress: float = 0.90) -> list[dict]:
    """positions: {ticker: PaperPosition}; prices: {ticker: ltp};
    watchlist: [{ticker, score, ...}] top-scored not-held candidates.

    Returns suggestions for positions >= `min_progress` of the way to target,
    paired with the best available watchlist pick to recycle into."""
    # Best fresh pick to fund (highest score, not already held)
    fund = None
    for w in sorted(watchlist, key=lambda x: -(x.get("score") or 0)):
        if w.get("ticker") and w["ticker"] not in positions:
            fund = w
            break

    out: list[RotationSuggestion] = []
    for tkr, pos in positions.items():
        entry = float(getattr(pos, "entry_price", 0) or 0)
        ltp = float(prices.get(tkr, entry) or entry)
        target = float(getattr(pos, "target_price", 0) or 0)
        if target <= 0:
            target = entry * (1 + float(getattr(cfg, "take_profit_pct", 0.30)))
        denom = target - entry
        if denom <= 0:
            continue
        progress = (ltp - entry) / denom * 100
        if progress >= min_progress * 100:
            out.append(RotationSuggestion(
                ticker=tkr, progress_pct=progress,
                value=float(getattr(pos, "qty", 0)) * ltp, ltp=ltp,
                target=target,
                fund_ticker=(fund or {}).get("ticker") if fund else None,
                fund_score=(fund or {}).get("score") if fund else None,
            ))
    out.sort(key=lambda s: -s.progress_pct)
    return [s.to_dict() for s in out]
