"""One place that decides whether a web route may trade right now.

OWNER ORDER 2026-09-10: "trade are prohibited from now after market hours"
-> asked whether their own buttons should be blocked too -> "After Hours yes
all block, but i can run the advisery to review buy, watch and block list,
for trade hours".

So: ALL of it, the owner's own buttons included. That overrides the standing
lesson from 2026-08-04 ("guard rails that block the owner from acting on
their own book are the wrong default") because they were asked the question
directly and answered it in writing. It is recorded here so a future session
does not quietly restore the old default on the strength of that lesson.

What is NOT gated, deliberately: every read-only screen. Scanning, scoring,
the picks table, the piano pages, /triggers, /register, /lens, news, the
advisory — all of it runs at any hour, which is exactly what they asked to
keep ("i can run the advisery to review buy, watch and block list").
Cancelling a resting limit order is also not gated: cancelling is the
absence of a trade, and refusing it would trap an order overnight.
"""

from __future__ import annotations

from typing import Optional

from fastapi.responses import RedirectResponse

from trading.calendars import is_market_open, market_state


def trading_blocked(cfg=None) -> Optional[str]:
    """None when trading is allowed; otherwise the market state that blocks it.

    Reads the same `cfg.market_hours_only` switch the engine reads, so a
    route and the engine can never disagree about whether the book is open.
    """
    if cfg is not None and not bool(getattr(cfg, "market_hours_only", True)):
        return None
    if is_market_open():
        return None
    return market_state()


_HUMAN = {
    "pre_open": "the market has not opened yet (opens 09:15 IST)",
    "after_close": "the market has closed (closed 15:30 IST)",
    "closed_holiday": "the market is closed today",
}


def reason_text(state: str) -> str:
    return _HUMAN.get(state, "the market is closed")


def blocked_redirect(page: str, state: str) -> RedirectResponse:
    """Send the owner back with a message that says WHY, never a bare error."""
    return RedirectResponse(f"{page}?error=market_closed&state={state}",
                            status_code=303)
