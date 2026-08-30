"""Sector-level event alerts — DISPLAY ONLY, the owner presses the button.

Owner ask 2026-08-29: "even if FDA announce anything, as trump announced the
sanctions again in mid week, how will app algorithm decide to exit the
affected stock/segment like IT, metals, healthcare". They then picked option
C-narrow from the options card.

WHY THIS SHAPE, and not an automatic news exit. Three reasons, all of them
the owner's own standards:

  1. News here can NEVER be calibrated. Google News serves about a week and
     keeps no archive, so unlike the 112 price triggers there is no way to
     measure whether a headline rule would have made or lost money. Wiring
     an unmeasurable rule to a sell order breaks the REQUIRES_15Y_BACKTEST
     rule in the owner's own sheets.
  2. The speed race is already lost. Algorithms move on a headline in
     milliseconds; a daily-bar app acting on the same headline is buying
     from whoever front-ran it.
  3. In April 2025 — the closest real analogue to the owner's example —
     professionals cut exposure over DAYS (2nd to 10th), not minutes.

So the app does the part a machine is actually good at: noticing that
several holdings in ONE sector are named in risk news on the same day, and
putting them in front of the owner in one place with a single button. The
decision stays human. Nothing here fires by itself.

Boundary: engine/risk/orders/danger never import this module — the same rule
that governs triggers, news_feed and register_v4, and it is test-enforced.
"""

from __future__ import annotations

# A news verdict only counts as a sector event when the module classified it
# as a genuine risk trigger. NO_NEWS is quiet, and UNKNOWN means the fetch
# failed — treating a failed fetch as an all-clear OR as an alert would both
# be lies, so it is reported separately and never grouped into a sector.
RISK_VERDICT = "TRIGGER"


def sector_alerts(positions, news_rows: dict, sector_map: dict) -> list[dict]:
    """Held names carrying a risk-news verdict today, grouped by sector.

    positions  — {ticker: position} the owner actually holds
    news_rows  — portfolio.news_watch["rows"], {ticker: reading.to_dict()}
    sector_map — cfg.sector_map

    Returns one row per affected sector, worst first, each carrying every
    held name in that sector so the owner sees the whole segment, not only
    the named stocks. Never raises on a partial or malformed reading — a
    display helper must not be able to take down the holdings page.
    """
    if not positions or not news_rows:
        return []

    held_by_sector: dict[str, list[str]] = {}
    for ticker in positions:
        sector = sector_map.get(ticker) or "Unknown"
        held_by_sector.setdefault(sector, []).append(ticker)

    out: list[dict] = []
    for sector, held in held_by_sector.items():
        flagged = []
        for ticker in sorted(held):
            row = news_rows.get(ticker) or {}
            if row.get("verdict") != RISK_VERDICT:
                continue
            heads = row.get("headlines") or []
            # the causing headline: the first one the classifier tagged as
            # risk, else the most recent. Shown so a wrong match is visible.
            cause = next((h for h in heads if h.get("side") == "RISK"),
                         heads[0] if heads else {})
            flagged.append({
                "ticker": ticker,
                "why": row.get("why") or "",
                "tags": row.get("tags") or [],
                "headline": cause.get("title") or "",
                "published": cause.get("published") or "",
                "source": cause.get("source") or "",
            })
        if not flagged:
            continue
        out.append({
            "sector": sector,
            "flagged": flagged,
            "n_flagged": len(flagged),
            "held": sorted(held),
            "n_held": len(held),
            # the unflagged rest of the segment — the owner asked about
            # exiting a SEGMENT, so the segment is shown whole.
            "quiet": sorted(set(held) - {f["ticker"] for f in flagged}),
        })

    out.sort(key=lambda r: (-r["n_flagged"], r["sector"]))
    return out


def unknown_feeds(positions, news_rows: dict) -> list[str]:
    """Held names whose news fetch FAILED today.

    Reported apart from the alerts on purpose: a broken fetch is not an
    all-clear. One Google News outage must never render as "no bad news" on
    every holding at once.
    """
    if not positions or not news_rows:
        return []
    return sorted(t for t in positions
                  if (news_rows.get(t) or {}).get("fetch_failed"))
