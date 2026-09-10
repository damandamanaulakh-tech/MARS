"""Pick the tradeable universe by real rupee liquidity — pure reduction.

OWNER ORDER 2026-08-30: "No automation for weekly new batch of stocks /
batch size also reduce to 500 from 2300+", and from the options card: "Top
500 by liquidity/turnover, weekly, replaces the 50".

WHY TURNOVER AND NOT TRADED QUANTITY. The committed delivery file carries
`ttl_trd_qnty` for every NSE name, which is tempting because it covers the
whole market — but it is a SHARE COUNT. Ranking on it puts penny stocks at
the top: ten million shares at Rs 3 is Rs 3 crore, while fifty thousand
shares at Rs 4,000 is Rs 20 crore. So the ranking uses close x volume, the
same rupee-turnover measure `risk.passes_liquidity` already gates entries
with. One measure of liquidity in the app, not two.

MEDIAN, not mean: one block deal or one delivery day must not lift a
normally-thin stock into the tradeable set.

HONEST LIMIT, and it is why `select_liquid_universe` returns the sample
size beside the list: this ranks the names the OHLCV store actually holds.
The store grows every Friday, so the ranking converges on a true top-500 of
the market as it fills. Until it does, the picked list is the top N of what
is stored — the caller states that on the page and in the journal rather
than shipping a short list as though it were a full 500.

Pure: no network, no config mutation, no engine import.
"""

from __future__ import annotations

from typing import Iterable, Optional

# Owner order 2026-08-30: 500, down from the ~2,300-name full-NSE list.
DEFAULT_TARGET = 500
# Bars of history a name needs before its liquidity is judged. A fortnight
# of trading; fewer and one quiet week decides the rank.
MIN_BARS = 10
# Rupee-turnover floor, matched to the live entry gate (Rs 1 crore median).
# A name below this could never pass risk.passes_liquidity anyway, so
# carrying it in the universe would only waste a scan slot.
MIN_TURNOVER = 1_00_00_000.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def median_turnover(candles: Iterable, lookback: int = 20) -> Optional[float]:
    """Median daily close x volume over the last `lookback` bars.

    `candles` are the stored rows [date, o, h, l, c, v]. None when the name
    has too little history to judge — never 0.0, which would read as "known
    to be illiquid" rather than "not enough data".
    """
    rows = [c for c in (candles or []) if c and len(c) >= 6]
    if len(rows) < MIN_BARS:
        return None
    tail = rows[-int(lookback):] if lookback else rows
    vals = []
    for c in tail:
        try:
            close, vol = float(c[4]), float(c[5])
        except (TypeError, ValueError):
            continue
        if close > 0 and vol > 0:
            vals.append(close * vol)
    if len(vals) < MIN_BARS:
        return None
    return _median(vals)


def select_liquid_universe(docs: Iterable[dict],
                           target: int = DEFAULT_TARGET,
                           lookback: int = 20,
                           blocklist: Iterable[str] = (),
                           always_include: Iterable[str] = ()) -> dict:
    """Rank stored names by median rupee turnover and take the top `target`.

    docs            — raw store docs {ticker, candles}, e.g. db.history_tails
    always_include  — names that ride along whatever their rank. HELD names,
                      so a position can never fall out of its own scan
                      universe (the same rule engine.scan_universe applies),
                      and since 2026-09-10 also WATCHLIST names, on the
                      owner's order: "keep the entire watch list in new
                      download on friday ... some current work done on watch
                      list must be kept". A watched name is watched precisely
                      because it is not a position yet, so dropping it on a
                      turnover cut loses the reasoning the owner accumulated.
                      (The return key stays `held_added` for compatibility;
                      it means "carried in regardless of rank".)

    Returns {tickers, ranked, sample, judged, skipped_thin, skipped_illiquid,
    short_by} — `short_by` is how many names the target is missing, so the
    caller can say so instead of presenting a short list as a full one.
    """
    banned = {t.upper() for t in (blocklist or ())}
    scored: list[tuple[float, str]] = []
    thin = illiquid = 0
    for doc in docs or []:
        ticker = (doc or {}).get("ticker") or ""
        if not ticker or ticker.upper() in banned:
            continue
        turnover = median_turnover(doc.get("candles"), lookback)
        if turnover is None:
            thin += 1
            continue
        if turnover < MIN_TURNOVER:
            illiquid += 1
            continue
        scored.append((turnover, ticker))

    scored.sort(key=lambda kv: (-kv[0], kv[1]))
    picked = [t for _v, t in scored[:int(target)]]

    held = [t for t in (always_include or ())
            if t and t not in picked and t.upper() not in banned]
    tickers = sorted(set(picked) | set(held))

    return {
        "tickers": tickers,
        "ranked": [{"ticker": t, "turnover": round(v, 2)}
                   for v, t in scored[:int(target)]],
        "sample": len(scored) + thin + illiquid,
        "judged": len(scored),
        "skipped_thin": thin,
        "skipped_illiquid": illiquid,
        "held_added": sorted(held),
        "short_by": max(0, int(target) - len(picked)),
    }
