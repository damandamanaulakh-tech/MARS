"""Option-chain snapshots — Node 8 of the audit work plan (owner order
2026-08-23: "continue with node 8").

WHAT THIS CLOSES: TRG-102 / T12W_0102 "Put-Call Skew Risk" was the LAST
external trigger with no data path ("options chain data" — NSE serves us
nothing without cookie games). Upstox serves the chain through the owner's own
daily token at ₹0 — so a post-login job snapshots it once per trading day.
Also upgrades W-022 (true per-stock PCR from the live chain instead of the
committed panel) and W-007 (per-stock ATM implied vol, which only the index
had).

THE SHAPE OF THE FEED, honestly:
  * AUTH-DEPENDENT (audit F8): the option endpoints reject tokenless calls,
    so on a day the owner never logs in there is no snapshot — journaled
    OPTION_CHAIN_SKIPPED, and every consumer reads DATA_NEEDED, never a
    stale number dressed as live.
  * FORWARD ARCHIVE ONLY: no history of past chains exists anywhere, so
    T12W_0102's "deterioration" leg needs SNAPSHOT_WINDOW days of our own
    snapshots before it can compute — the trigger honestly reads
    DATA_NEEDED for its first days. It is UNCALIBRATED (no backward
    archive) and therefore display/watchlist only, excluded from the
    danger layer and from the calibration runner exactly like the news
    triggers.
  * The per-ticker history kept on the portfolio is bounded
    (HISTORY_KEEP rows) — the full daily archive accumulates in Mongo via
    the snapshots the scheduler stores.

This module is PURE (no network, no state): the scheduler owns the fetch
loop; pages and triggers read the stored result. engine/risk/orders never
import it (boundary-tested).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

log = logging.getLogger(__name__)

#: THE OWNER'S SPAN (their words, 2026-08-24: "there is no number, its just
#: a pattern like the other candle, but span of candle is 15 days ... the
#: algorithm only review the changes in that particular stock and record it
#: for 15 days"). The deterioration read is a PATTERN over up to 15 recorded
#: snapshots — not a numeric threshold.
SNAPSHOT_WINDOW = 15
#: minimum recorded days before any pattern is read at all — a shape needs
#: more than a couple of points. ASSUMED (the span is theirs; this floor
#: only stops a 2-day "pattern").
MIN_PATTERN_DAYS = 5
#: per-ticker (date, pcr, skew) rows kept on the portfolio = the 15-day
#: candle the owner described. Bounded RAM.
HISTORY_KEEP = 15
#: a stored snapshot older than this many days is not "today's chain".
MAX_AGE_DAYS = 2


def nearest_expiry(contracts: list, today: date) -> Optional[str]:
    """The nearest not-yet-past expiry among the underlying's contracts."""
    best = None
    for c in contracts or []:
        e = str(c.get("expiry") or "")[:10]
        try:
            d = date.fromisoformat(e)
        except ValueError:
            continue
        if d >= today and (best is None or d < best):
            best = d
    return best.isoformat() if best else None


def _row_leg(row: dict, leg: str) -> tuple[float, Optional[float]]:
    """(oi, iv) for one leg of a strike row; iv None when absent."""
    o = row.get(leg) or {}
    md = o.get("market_data") or {}
    gr = o.get("option_greeks") or {}
    try:
        oi = float(md.get("oi") or 0.0)
    except (TypeError, ValueError):
        oi = 0.0
    iv = gr.get("iv")
    try:
        iv = float(iv) if iv is not None and float(iv) > 0 else None
    except (TypeError, ValueError):
        iv = None
    return oi, iv


def summarize_chain(rows: list, spot: Optional[float] = None) -> Optional[dict]:
    """One expiry's chain -> {pcr_oi, ce_oi, pe_oi, atm_strike, iv_call,
    iv_put, iv_skew, n_strikes}. None when the rows carry no usable OI —
    an empty chain is DATA_NEEDED, never a zero PCR (which would read as
    maximally bullish).

    iv_skew = ATM put IV − ATM call IV, in IV points: puts pricier than
    calls = downside protection in demand. ATM = strike nearest the spot
    (the chain's own underlying_spot_price when the caller passes none).
    """
    ce = pe = 0.0
    strikes = []
    for row in rows or []:
        try:
            k = float(row.get("strike_price") or 0.0)
        except (TypeError, ValueError):
            continue
        c_oi, c_iv = _row_leg(row, "call_options")
        p_oi, p_iv = _row_leg(row, "put_options")
        ce += c_oi
        pe += p_oi
        if spot is None:
            s = row.get("underlying_spot_price")
            try:
                spot = float(s) if s else None
            except (TypeError, ValueError):
                pass
        strikes.append((k, c_iv, p_iv))
    if ce <= 0 or pe <= 0 or not strikes:
        return None
    atm_strike = iv_call = iv_put = None
    if spot:
        atm = min(strikes, key=lambda t: abs(t[0] - spot))
        atm_strike, iv_call, iv_put = atm
    skew = (round(iv_put - iv_call, 2)
            if iv_call is not None and iv_put is not None else None)
    return {
        "pcr_oi": round(pe / ce, 3),
        "ce_oi": ce, "pe_oi": pe,
        "atm_strike": atm_strike,
        "iv_call": iv_call, "iv_put": iv_put,
        "iv_skew": skew,
        "n_strikes": len(strikes),
    }


def deterioration_pattern(history: list,
                          span: int = SNAPSHOT_WINDOW) -> Optional[bool]:
    """The owner's 15-day pattern read (their words: "no number, its just a
    pattern like the other candle, but span of candle is 15 days").

    Reads the SHAPE of the recorded PCR/skew rows over the span, exactly the
    way a candle pattern is read — no numeric threshold:
      * NET RISING: the latest reading above the span's first, AND
      * PERSISTENT: more up-days than down-days across the recorded span
    on EITHER the PCR series or the skew series. Puts piling up (PCR) or put
    insurance getting pricier vs calls (skew), growing through the window,
    is the deterioration their sheet names.

    None until MIN_PATTERN_DAYS of the stock's own recordings exist — a
    two-point line is not a pattern, and no archive of past chains exists
    anywhere to borrow from.
    """
    def _series(idx: int) -> Optional[list]:
        rows = sorted((r[0], r[idx]) for r in (history or [])
                      if len(r) > idx and r[idx] is not None)
        return [v for _, v in rows[-span:]] if len(rows) >= MIN_PATTERN_DAYS             else None

    def _grows(vals: Optional[list]) -> Optional[bool]:
        if vals is None:
            return None
        ups = sum(1 for a, b in zip(vals, vals[1:]) if b > a)
        downs = sum(1 for a, b in zip(vals, vals[1:]) if b < a)
        return bool(vals[-1] > vals[0] and ups > downs)

    pcr, skew = _grows(_series(1)), _grows(_series(2))
    if pcr is None and skew is None:
        return None
    return bool(pcr) or bool(skew)


def is_fresh(asof: str, today: Optional[date] = None,
             max_age_days: int = MAX_AGE_DAYS) -> bool:
    try:
        d = date.fromisoformat(str(asof)[:10])
    except (ValueError, TypeError):
        return False
    return ((today or date.today()) - d).days <= max_age_days


def snapshot_universe(broker, tickers: list, key_of, today: date) -> dict:
    """Fetch + summarize the chain for every F&O name in `tickers` (plus the
    NIFTY index under '^NSEI'). Returns {"asof", "rows", "skipped_no_fo",
    "failed"} — the scheduler stores it; nothing here touches state.

    `key_of(ticker)` -> instrument key or None. Names without option
    contracts are recorded once as not-F&O (absence of an options market is
    not an error). A summarize miss counts as failed — visible, not padded.
    """
    rows: dict = {}
    no_fo: list = []
    failed: list = []
    targets = [("^NSEI", "NSE_INDEX|Nifty 50")] + [
        (t, key_of(t)) for t in tickers]
    for ticker, ik in targets:
        if not ik:
            no_fo.append(ticker)
            continue
        contracts = broker.get_option_contracts(ik)
        if not contracts:
            no_fo.append(ticker)
            continue
        expiry = nearest_expiry(contracts, today)
        if not expiry:
            failed.append(ticker)
            continue
        chain = broker.get_option_chain(ik, expiry)
        summ = summarize_chain(chain or [])
        if summ is None:
            failed.append(ticker)
            continue
        summ["expiry"] = expiry
        rows[ticker] = summ
    return {"asof": today.isoformat(), "ts": datetime.utcnow().isoformat(),
            "rows": rows, "skipped_no_fo": no_fo, "failed": failed}
