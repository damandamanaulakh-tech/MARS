"""News sentiment via Claude API.

Pulls recent headlines per ticker, asks Claude (Haiku) to score each on
a -1.0 to +1.0 sentiment scale, returns a 0-100 catalyst score for the
engine.

Cost expectation (Haiku at $0.25 / 1M input tokens):
  - ~5 headlines per stock per day
  - ~150 input tokens per call (system + headlines)
  - 30 stocks * 5 headlines = ~22k tokens/day = $0.005/day = ~₹0.50/day
  - Monthly: ~₹15

Falls back gracefully:
  - No ANTHROPIC_API_KEY -> returns neutral 50, logs once
  - API error -> returns neutral 50
  - No headlines -> returns neutral 50
  - In backtest mode -> returns neutral 50 (no historical timestamps)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

from ..data import is_backtest_mode

log = logging.getLogger(__name__)


CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"   # cheapest, fast
_WARNED_NO_KEY = False


@dataclass
class NewsSentiment:
    ticker: str
    score: float                      # 0..100
    headlines_used: int
    raw_compound: float               # -1.0 .. +1.0 average
    reasoning: str


def _get_headlines_yfinance(ticker: str, lookback_days: int = 7) -> list[str]:
    """Pull recent headlines for the ticker. Returns list of titles only."""
    try:
        import yfinance as yf
        items = yf.Ticker(ticker).news or []
    except Exception as e:
        log.debug("yfinance news failed for %s: %s", ticker, e)
        return []
    titles: list[str] = []
    for item in items[:10]:  # cap at 10
        # yfinance schema varies; try both flat + content.title
        title = item.get("title")
        if not title and isinstance(item.get("content"), dict):
            title = item["content"].get("title")
        if title:
            titles.append(title.strip())
    return titles[:5]   # top-5 to keep token cost low


def _score_with_claude(ticker: str, headlines: list[str]) -> Optional[tuple[float, str]]:
    """Returns (compound_score_-1_to_1, reasoning) or None on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    global _WARNED_NO_KEY
    if not api_key:
        if not _WARNED_NO_KEY:
            log.info("ANTHROPIC_API_KEY not set; news sentiment will return neutral")
            _WARNED_NO_KEY = True
        return None
    if not headlines:
        return None

    bullet_lines = "\n".join(f"- {h}" for h in headlines)
    prompt = (
        f"Score the sentiment of these recent headlines for NSE-listed stock "
        f"{ticker} as a single number from -1.0 (very negative for the stock) "
        f"to +1.0 (very positive). Consider Indian market context. Only the "
        f"number, then one short reason on a new line.\n\n"
        f"HEADLINES:\n{bullet_lines}"
    )

    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 120,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        r = httpx.post(CLAUDE_API_URL, json=body, headers=headers, timeout=15.0)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Claude sentiment call failed for %s: %s", ticker, e)
        return None

    try:
        text = data["content"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        return None

    # Parse: first line should be the number, rest is reasoning
    lines = text.split("\n", 1)
    try:
        score_raw = float(lines[0].strip().split()[0].rstrip(","))
        score = max(-1.0, min(1.0, score_raw))
    except (ValueError, IndexError):
        return None
    reasoning = lines[1].strip() if len(lines) > 1 else ""
    return score, reasoning


def fetch_sentiment(ticker: str, lookback_days: int = 7) -> NewsSentiment:
    """Returns sentiment reading. Always returns a NewsSentiment object —
    score is 50 (neutral) on any failure or in backtest mode."""
    if is_backtest_mode():
        return NewsSentiment(
            ticker=ticker, score=50.0, headlines_used=0,
            raw_compound=0.0,
            reasoning="backtest-mode: no historical headline timestamps",
        )

    headlines = _get_headlines_yfinance(ticker, lookback_days=lookback_days)
    if not headlines:
        return NewsSentiment(
            ticker=ticker, score=50.0, headlines_used=0,
            raw_compound=0.0, reasoning="no headlines available",
        )

    result = _score_with_claude(ticker, headlines)
    if result is None:
        return NewsSentiment(
            ticker=ticker, score=50.0, headlines_used=len(headlines),
            raw_compound=0.0,
            reasoning="Claude API unavailable; defaulted to neutral",
        )

    compound, reasoning = result
    # Map -1..+1 -> 0..100
    score = 50.0 + compound * 50.0
    return NewsSentiment(
        ticker=ticker, score=score, headlines_used=len(headlines),
        raw_compound=compound, reasoning=reasoning,
    )
