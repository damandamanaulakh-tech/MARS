"""News sentiment via Claude API (or OpenAI, whichever key is present).

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
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"                 # cheapest, fast
_WARNED_NO_KEY = False


def _sentiment_prompt(ticker: str, headlines: list[str]) -> str:
    bullet_lines = "\n".join(f"- {h}" for h in headlines)
    return (
        f"Score the sentiment of these recent headlines for NSE-listed stock "
        f"{ticker} as a single number from -1.0 (very negative for the stock) "
        f"to +1.0 (very positive). Consider Indian market context. Only the "
        f"number, then one short reason on a new line.\n\n"
        f"HEADLINES:\n{bullet_lines}"
    )


def _parse_score_text(text: str):
    lines = text.strip().split("\n", 1)
    try:
        score_raw = float(lines[0].strip().split()[0].rstrip(","))
    except (ValueError, IndexError):
        return None
    score = max(-1.0, min(1.0, score_raw))
    reasoning = lines[1].strip() if len(lines) > 1 else ""
    return score, reasoning


def _score_with_openai(ticker: str, headlines: list[str]):
    """OpenAI fallback — the user's Render env carries OPENAI_API_KEY."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not headlines:
        return None
    body = {
        "model": OPENAI_MODEL,
        "max_tokens": 120,
        "messages": [{"role": "user",
                      "content": _sentiment_prompt(ticker, headlines)}],
    }
    headers = {"Authorization": f"Bearer {api_key}",
               "content-type": "application/json"}
    try:
        r = httpx.post(OPENAI_API_URL, json=body, headers=headers, timeout=15.0)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        log.warning("OpenAI sentiment call failed for %s: %s", ticker, e)
        return None
    return _parse_score_text(text)


@dataclass
class NewsSentiment:
    ticker: str
    score: float                      # 0..100
    headlines_used: int
    raw_compound: float               # -1.0 .. +1.0 average
    reasoning: str


# 2026-10-22 entry-path review (owner: "fix all"): the headline fetch was the
# one Yahoo call in the composite with NO time limit and NO breaker — the
# earnings and analyst lookups already had both (AUDIT S-15). Every
# intraday / short / swing / positional score calls it, so a blocked or hung
# Yahoo cost one full request per name per pass while the rotation held the
# mode lock. Same pattern as data.fetch_earnings_growth now: each call bounded
# by data.run_with_timeout (8 s), and three failures in a row stop the fetch
# for 30 minutes. A blocked fetch still reads "no headlines" = neutral 50.
#: Every number the headline breaker chooses, with where it comes from.
ASSUMED = {
    # Both copy the earnings-growth breaker in data.py (AUDIT S-15,
    # 2026-10-02: _EG_BREAKER_AFTER 3 / _EG_BREAKER_SECS 1800) so the three
    # Yahoo lookups in the composite give up the same way.
    "breaker_after_fails": 3,
    "breaker_secs": 1800.0,
}
_NEWS_FAILS = 0
_NEWS_BREAKER_UNTIL = 0.0
_NEWS_BREAKER_AFTER = ASSUMED["breaker_after_fails"]
_NEWS_BREAKER_SECS = ASSUMED["breaker_secs"]
#: None = data.YAHOO_INFO_TIMEOUT_S (8 s), read at call time.
NEWS_TIMEOUT_S = None


def _get_headlines_yfinance(ticker: str, lookback_days: int = 7) -> list[str]:
    """Pull recent headlines for the ticker. Returns list of titles only."""
    global _NEWS_FAILS, _NEWS_BREAKER_UNTIL
    import time as _time
    now = _time.time()
    if now < _NEWS_BREAKER_UNTIL:
        return []
    try:
        import yfinance as yf
        from .. import data as _data
        timeout = (NEWS_TIMEOUT_S if NEWS_TIMEOUT_S is not None
                   else _data.YAHOO_INFO_TIMEOUT_S)
        items = _data.run_with_timeout(lambda: yf.Ticker(ticker).news,
                                       timeout=timeout) or []
        _NEWS_FAILS = 0
    except Exception as e:
        _NEWS_FAILS += 1
        if _NEWS_FAILS >= _NEWS_BREAKER_AFTER:
            _NEWS_BREAKER_UNTIL = now + _NEWS_BREAKER_SECS
            _NEWS_FAILS = 0
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
    if not api_key or not headlines:
        return None

    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 120,
        "messages": [{"role": "user",
                      "content": _sentiment_prompt(ticker, headlines)}],
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
    return _parse_score_text(text)


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

    # Provider: Claude key first, OpenAI key second (user's Render env).
    global _WARNED_NO_KEY
    result = _score_with_claude(ticker, headlines)
    if result is None:
        result = _score_with_openai(ticker, headlines)
    if result is None:
        if not (os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("OPENAI_API_KEY")) and not _WARNED_NO_KEY:
            log.info("No ANTHROPIC_API_KEY / OPENAI_API_KEY; news sentiment neutral")
            _WARNED_NO_KEY = True
        return NewsSentiment(
            ticker=ticker, score=50.0, headlines_used=len(headlines),
            raw_compound=0.0,
            reasoning="LLM API unavailable; defaulted to neutral",
        )

    compound, reasoning = result
    # Map -1..+1 -> 0..100
    score = 50.0 + compound * 50.0
    return NewsSentiment(
        ticker=ticker, score=score, headlines_used=len(headlines),
        raw_compound=compound, reasoning=reasoning,
    )
