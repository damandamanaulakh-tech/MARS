"""Trade price audit against Upstox candles (owner order 2026-10-21).

Owner: "there is one positon in closed trade of BELS, it never go beyond
200, u r showing 49% loss, fix this with buy and sell real price at that
time, and do check all other on there buy and sell in closed trades".

A session cannot see the live book, so this runs ON THE LIVE BOX from
/trades?audit=1: every closed trade (live + archived) and every open
position is checked against the PUBLIC Upstox candles for its fill day —
the day's low/high, and the close of the 1-minute candle at the fill minute.
A stored price outside the day's range is flagged; the corrected trade is
recomputed through the engine's OWN fill rule (5 bps slippage) and cost
model (trading/costs.trade_costs), so a correction is what the engine would
have booked with a right quote.

Nothing here changes a number by itself. `apply_corrections` runs only from
the owner's tap on POST /trades/correct, journals TRADE_PRICE_CORRECTED once
per trade (journal append-only) and moves cash by exactly the net delta.

Pure functions take injected fetchers so tests need no network; the real
fetchers (`upstox_fetchers`) use the v3 candle endpoints, tokenless.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Flags a side (BUY or SELL) can carry.
OK = "OK"
OUT_OF_RANGE = "OUT_OF_RANGE"
CA_UNADJUSTED = "CA_UNADJUSTED"
NO_CANDLE = "NO_CANDLE"
OLD_ISIN = "OLD_ISIN"
NOT_CHECKED = "NOT_CHECKED"
FLAGGED = {OUT_OF_RANGE, CA_UNADJUSTED, OLD_ISIN}

# Tolerance around the day's low-high: the engine's own 5 bps slippage plus
# 0.5% ASSUMED (a fill a few ticks past the printed extreme is not a fault).
SLIPPAGE_BPS_DEFAULT = 5
EXTRA_TOL_PCT_ASSUMED = 0.5
TOL_PCT_DEFAULT = 0.6
TOL_NOTE = ("tolerance = 5 bps slippage + 0.5% ASSUMED on top of the day's "
            "low-high (0.6% total)")

# Journal lines are matched to a fill by ticker + timestamp within this.
JOURNAL_MATCH_S = 120
JOURNAL_ACTIONS = ("BUY", "SELL", "OWNER_SELL", "OWNER_BATCH_BUY",
                   "OWNER_SECTOR_EXIT", "OWNER_ORDER_FILLED")
# The owner BUY button opens through engine._open with the scan pick's (or
# the default) fill_source, never the quote it really used — its line is
# recognised by this reasoning prefix (web/routes/scan_routes.owner_buy).
OWNER_BUY_PREFIX = "OWNER BUY CALL"

CACHE_TTL_S = 1800            # candles + result kept 30 min in-process
V3_URL = "https://api.upstox.com/v3/historical-candle"
HTTP_TIMEOUT_S = 15.0


def budget_s() -> float:
    """SB_TRADE_AUDIT_BUDGET_S (default 60 s; <= 0 = unbounded)."""
    try:
        v = float(os.environ.get("SB_TRADE_AUDIT_BUDGET_S", "60") or 60)
    except ValueError:
        v = 60.0
    return v


# ---------------------------------------------------------------------------
# ids, time, corporate actions
# ---------------------------------------------------------------------------
def _iso(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def trade_id(t) -> str:
    """The M-4 archive id (web/db._trade_key) — same for live and archived
    rows: ticker|entry_iso|exit_iso|qty|reason. (Built from the attributes,
    not to_dict(): the signature below runs it over up to 5,000 trades.)"""
    if isinstance(t, dict):
        return (f"{t.get('ticker')}|{t.get('entry_date')}|{t.get('exit_date')}|"
                f"{t.get('qty')}|{t.get('reason')}")
    return (f"{t.ticker}|{_iso(t.entry_date)}|{_iso(t.exit_date)}|"
            f"{t.qty}|{t.reason}")


def position_id(pos) -> str:
    return f"POS|{pos.ticker}|{_iso(pos.entry_date)}|{pos.qty}"


def _as_dt(dt) -> datetime:
    """A legacy date-only value (a bare `date`) reads as that day 00:00."""
    if isinstance(dt, datetime):
        return dt
    return datetime(dt.year, dt.month, dt.day)


def to_ist(dt) -> datetime:
    """Engine stamps are naive UTC (utcnow). An aware stamp converts; a
    naive one is read as UTC."""
    dt = _as_dt(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def _date_only(dt) -> bool:
    """A manual position carries a date with no time (00:00:00)."""
    if not isinstance(dt, datetime):
        return True
    return dt.hour == 0 and dt.minute == 0 and dt.second == 0


def bare(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    for suf in (".NS", ".BO"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t


def parse_ca_rows(rows: list) -> list[dict]:
    """Raw CA rows (NSE API / committed file / Mongo store — one shape) →
    [{ex_date, subject, factor, kind, isin, source}] ascending. ONE parser
    for the whole app: trading.corporate_actions (the engine's ex-date
    adjuster reads the same ratios). `factor` = the share factor (split
    10→5 = 2, bonus 1:1 = 2); a rights row carries factor None — reported,
    never used as a ratio (its price effect needs the issue price)."""
    from trading.corporate_actions import parse_rows
    out = []
    for c in parse_rows(rows or []):
        out.append({"ex_date": c["ex_date"], "subject": c["subject"],
                    "factor": (float(c["ratio"]) if c.get("ratio") else None),
                    "kind": c["kind"], "isin": c.get("isin", ""),
                    "source": c.get("source", "")})
    return out


def _iso_ca_date(s: str) -> Optional[str]:
    from trading.corporate_actions import iso_date
    return iso_date(s)


def _with_factor(ca: list[dict]) -> list[dict]:
    return [c for c in ca if c.get("factor") and abs(c["factor"] - 1.0) > 1e-9]


# Split ratios a stored price is tested against when NO corporate-action
# record is reachable (NSE fetch failed, committed file too old). A fit is a
# HINT printed with DATA_NEEDED — never a ratio the correction moves cash on.
COMMON_RATIOS = (2.0, 5.0, 10.0, 2.5, 4.0, 1.5, 3.0, 1.25, 20.0)


def ca_after(ca: list[dict], date_iso: str) -> list[dict]:
    """Split/bonus rows with ex-date AFTER this fill date: Upstox candles are
    back-adjusted for every one of them, so the fill's own-day price is
    candle × their combined factor."""
    return [c for c in _with_factor(ca) if c["ex_date"] > date_iso]


def ca_between(ca: list[dict], d1: str, d2: str) -> list[dict]:
    """Every CA row (rights included) with ex-date in (d1, d2]."""
    return [c for c in ca if d1 < c["ex_date"] <= d2]


def ca_label(c: dict) -> str:
    return f"{c['subject'].strip()} ex {c['ex_date']}"


def restated_trade(qty: int, entry: float, exit_: float, ratio: float, cfg) -> dict:
    """THE OWNER'S CORRECTION RULE for a split/bonus BETWEEN buy and sell
    (2026-10-21, "real price at that time"): keep the sell as booked (it IS
    the real post-split price); restate the buy on the post-split basis —
    entry ÷ ratio, qty × ratio; gross/costs/net through trade_costs on the
    restated legs. A fractional remainder (a 1:2 bonus on an odd qty) is
    returned at its cost, exactly as the ex-date adjuster does for a held
    name — so the cash change is new net − old net and nothing else
    (`frac_cash` is that returned cost, shown, already inside the delta)."""
    import math
    exact = qty * ratio
    new_qty = int(math.floor(exact + 1e-9))
    frac = max(0.0, exact - new_qty)
    new_entry = entry / ratio
    corr = corrected_trade(new_qty, new_entry, exit_, cfg)
    corr["qty"] = new_qty
    corr["frac_shares"] = round(frac, 6)
    corr["frac_cash"] = frac * new_entry
    corr["ratio"] = ratio
    return corr


# ---------------------------------------------------------------------------
# candle picking (pure)
# ---------------------------------------------------------------------------
def _daily_by_date(daily: list) -> dict:
    out = {}
    for c in daily or []:
        d = c.get("date")
        if d:
            out[d] = c
    return out


def day_candle(daily: list, date_iso: str) -> tuple[Optional[dict], str]:
    """The daily bar for the date, else the nearest PRIOR session's (a fill
    stamped on a holiday/weekend by a catch-up pass). ('', bar) / note."""
    by = _daily_by_date(daily)
    if date_iso in by:
        return by[date_iso], ""
    prior = sorted(d for d in by if d < date_iso)
    if prior:
        return by[prior[-1]], f"no bar on {date_iso}; prior session {prior[-1]} used"
    return None, f"no daily bar on or before {date_iso}"


class _Bars:
    """One day's intraday candles kept compact for the 30-minute cache
    (epoch seconds + open + close in arrays, ~4.5 KB a day instead of ~150 KB
    of dicts): a long book can fetch hundreds of fill days, and the 512 MB
    box already peaks near its ceiling during the rotations."""
    __slots__ = ("t", "o", "c")

    def __init__(self, candles):
        from array import array
        rows = []
        for c in candles or []:
            ts = c.get("ts")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    continue
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            try:
                rows.append((int(ts.timestamp()), float(c["open"]), float(c["close"])))
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort()
        self.t = array("q", (r[0] for r in rows))
        self.o = array("d", (r[1] for r in rows))
        self.c = array("d", (r[2] for r in rows))

    def __len__(self):
        return len(self.t)


def _compact(candles):
    return None if candles is None else _Bars(candles)


def _minute_price_bars(b: _Bars, fill_ist: datetime, interval_min: int):
    import bisect
    if not len(b):
        return None, ""
    ft = fill_ist.timestamp()
    if ft < b.t[0]:
        return float(b.o[0]), f"{interval_min}-min first bar open (pre-open stamp)"
    if ft >= b.t[-1] + interval_min * 60:
        return float(b.c[-1]), f"{interval_min}-min last bar close (after-hours stamp)"
    i = bisect.bisect_right(b.t, ft) - 1
    hhmm = datetime.fromtimestamp(b.t[i], IST).strftime("%H:%M")
    return float(b.c[i]), f"{interval_min}-min candle {hhmm} IST close"


def minute_price(minute, fill_ist: datetime, interval_min: int) -> tuple[Optional[float], str]:
    """Close of the candle at/just before the fill minute. Before the first
    bar = the day's open (pre-open stamp); after the last bar = the last
    close (after-hours stamp). Candle ts = bar start, IST."""
    if isinstance(minute, _Bars):
        return _minute_price_bars(minute, fill_ist, interval_min)
    bars = []
    for c in minute or []:
        ts = c.get("ts")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                continue
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        bars.append((ts.astimezone(IST), c))
    if not bars:
        return None, ""
    bars.sort(key=lambda x: x[0])
    if fill_ist < bars[0][0]:
        return float(bars[0][1]["open"]), f"{interval_min}-min first bar open (pre-open stamp)"
    last_ts = bars[-1][0] + timedelta(minutes=interval_min)
    if fill_ist >= last_ts:
        return float(bars[-1][1]["close"]), f"{interval_min}-min last bar close (after-hours stamp)"
    pick = bars[0]
    for b in bars:
        if b[0] <= fill_ist:
            pick = b
        else:
            break
    return float(pick[1]["close"]), f"{interval_min}-min candle {pick[0].strftime('%H:%M')} IST close"


def in_range(price: float, low: float, high: float, tol_pct: float) -> bool:
    t = tol_pct / 100.0
    return low * (1 - t) <= price <= high * (1 + t)


def ratio_hint(stored: float, low: float, high: float, ca: list[dict],
               tol_pct: float) -> Optional[float]:
    """A CA ratio r (from the symbol's split/bonus rows) such that stored × r
    sits in the day's range. Only ratios a CA record carries — nothing
    invented. None when none fits."""
    cands: list[float] = []
    prod = 1.0
    for c in _with_factor(ca):
        f = float(c["factor"])
        prod *= f
        cands += [f, 1.0 / f]
    if abs(prod - 1.0) > 1e-9:
        cands = [prod, 1.0 / prod] + cands
    for r in cands:
        if in_range(stored * r, low, high, tol_pct):
            return r
    return None


def common_ratio_hint(stored: float, low: float, high: float,
                      tol_pct: float) -> Optional[float]:
    """stored ÷ r in range for a common split ratio — a HINT that a split
    with no reachable record sits between the fill and today (DATA_NEEDED).
    Never a correction basis."""
    for r in COMMON_RATIOS:
        if in_range(stored / r, low, high, tol_pct):
            return r
    return None


# ---------------------------------------------------------------------------
# one side
# ---------------------------------------------------------------------------
def audit_side(side: str, stored: float, when: datetime, ticker: str,
               daily: Optional[list], minute_get: Callable, ca: list[dict],
               tol_pct: float, slippage_bps: float, isin_note: str = "",
               ca_complete: bool = True) -> dict:
    """One fill (BUY or SELL) against its day's candles. `minute_get(date_iso,
    interval)` returns candles or None. `daily` None = fetch failed.
    `ca_complete` False = the live NSE corporate-action fetch did not answer
    for this name, so a split may be missing from `ca`: an out-of-range
    price that fits a common split ratio is then a DATA_NEEDED hint and the
    row is not corrected on that guess. True = NSE answered (or the caller
    vouches for the rows): an out-of-range price is simply a wrong fill."""
    ist = to_ist(when)
    date_iso = ist.date().isoformat()
    row = {
        "side": side, "stored": float(stored), "date_ist": date_iso,
        "time_ist": ("date only" if _date_only(when) else ist.strftime("%H:%M")),
        "day_low": None, "day_high": None, "day_close": None,
        "minute_price": None, "real_source": "", "fill": None,
        "in_range": None, "ratio_hint": None, "flag": NO_CANDLE, "note": "",
        "ca_after": [ca_label(c) for c in ca_after(ca, date_iso)],
        "factor": 1.0, "needs_ca_record": False, "intraday": False,
    }
    bar, note = day_candle(daily or [], date_iso) if daily is not None else (None, "candle fetch failed")
    if bar is None:
        row["note"] = note or "no candle"
        return row
    low, high, close = float(bar["low"]), float(bar["high"]), float(bar["close"])
    row["day_low"], row["day_high"], row["day_close"] = low, high, close
    after = ca_after(ca, date_iso)
    factor = 1.0
    for c in after:
        factor *= float(c["factor"])
    row["factor"] = factor
    notes = [note] if note else []

    # the real price at that minute (raw candle basis)
    mp, src = None, ""
    if _date_only(when):
        mp, src = close, "daily close (date-only stamp, time unknown)"
    else:
        bar_date = bar.get("date") or date_iso
        for interval in (1, 30):
            cands = minute_get(bar_date, interval)
            if cands:
                mp, src = minute_price(cands, ist, interval)
                if mp is not None:
                    row["intraday"] = True       # timed to the fill, not the close
                    break
        if mp is None:
            mp, src = close, "daily close (no minute candles served)"
    # Upstox back-adjusts for every later split/bonus: the price actually
    # traded that day is candle × factor.
    real = mp * factor
    row["minute_price"] = real
    row["real_source"] = src + (f" × {factor:g} (CA after fill)" if abs(factor - 1) > 1e-9 else "")
    from trading.costs import slippage_price

    class _C:  # minimal cfg for the slippage rule
        pass
    _c = _C()
    _c.slippage_bps = slippage_bps
    row["fill"] = slippage_price(real, side, _c)

    raw_ok = in_range(stored, low, high, tol_pct)
    real_ok = in_range(stored, low * factor, high * factor, tol_pct)
    row["in_range"] = raw_ok if abs(factor - 1) < 1e-9 else real_ok
    if abs(factor - 1) < 1e-9:
        if raw_ok:
            row["flag"] = OK
        else:
            row["flag"] = OUT_OF_RANGE
            row["ratio_hint"] = ratio_hint(stored, low, high, ca, tol_pct)
            if row["ratio_hint"] is None:
                r = None if ca_complete else common_ratio_hint(stored, low, high, tol_pct)
                if r is not None:
                    row["ratio_hint"] = 1.0 / r
                    row["needs_ca_record"] = True
                    notes.append(f"stored ÷ {r:g} fits the day's (back-adjusted) range — "
                                 f"looks like a {r:g}-for-1 split after this fill with NO "
                                 f"corporate-action record reachable (DATA_NEEDED: the "
                                 f"08:50 NSE fetch or a later tap). Not corrected on a guess.")
                elif isin_note:
                    row["flag"] = OLD_ISIN
                    notes.append(isin_note)
    else:
        subj = "; ".join(ca_label(c) for c in after)
        if real_ok:
            # the fill was right on its day; the candles are the adjusted ones
            row["flag"] = CA_UNADJUSTED
            row["ratio_hint"] = 1.0 / factor
            notes.append(f"corporate action {subj} after this fill: the stored price is "
                         f"pre-adjustment; adjusted = price × {1.0 / factor:g}. The position "
                         f"itself was never adjusted (qty × {factor:g}) — see the root-cause note.")
        elif raw_ok:
            row["flag"] = OUT_OF_RANGE
            row["ratio_hint"] = factor
            notes.append(f"stored price matches the back-ADJUSTED candle, not the price traded "
                         f"on {date_iso} (× {factor:g} for {subj}) — the fill read an adjusted series")
        else:
            row["flag"] = OUT_OF_RANGE
            row["ratio_hint"] = ratio_hint(stored, low, high, ca, tol_pct)
    row["note"] = "; ".join(n for n in notes if n)
    return row


# ---------------------------------------------------------------------------
# whole trade / position
# ---------------------------------------------------------------------------
def _cfg_costs(cfg):
    if cfg is not None:
        return cfg
    from trading.config import Config
    return Config()


def corrected_trade(qty: int, entry: float, exit_: float, cfg) -> dict:
    from trading.costs import trade_costs
    gross = (exit_ - entry) * qty
    costs = trade_costs(qty, entry, exit_, cfg)
    return {"entry": entry, "exit": exit_, "gross": gross, "costs": costs,
            "net": gross - costs}


def _side_price(row: dict, stored: float, rewrite_ok: bool) -> float:
    """The price one side is corrected to. Outside its own-day range (or on
    a retired key) → the real price at the fill minute (± slippage). Inside
    the range → the stored price stands, unless `rewrite_ok` (the trade is
    being rewritten — contract item 3, both sides) AND the real price is
    timed to the fill (a 1- or 30-minute candle; a bare daily close is no
    better than a fill that was already inside the day's range). A side
    with no candle, or one right on its pre-split day, keeps its stored
    price."""
    fill = row.get("fill")
    if fill is None:
        return float(stored)
    if row["flag"] in (OUT_OF_RANGE, OLD_ISIN):
        return float(fill)
    if row["flag"] == OK and rewrite_ok and row.get("intraday"):
        return float(fill)
    return float(stored)


def audit_trade(trade, fetch_daily: Callable, fetch_minute: Callable,
                ca_rows: list, tol_pct: float = TOL_PCT_DEFAULT, cfg=None,
                isin_note: str = "", daily: Optional[list] = "unset",
                ca_complete: bool = True) -> dict:
    """One ClosedTrade against its candles. `fetch_daily(ticker, from_iso,
    to_iso)` → [{date, open, high, low, close}] or None; `fetch_minute(ticker,
    date_iso, interval_min)` → [{ts, open, high, low, close}] or None.
    `ca_rows` = the symbol's raw CA file rows (parsed here)."""
    cfg = _cfg_costs(cfg)
    t = trade
    from trading.corporate_actions import bare as _bare
    sym = _bare(t.ticker)
    # an action the record is already stated on (absorbed while held, or
    # restated by an earlier tap) is not pending for it
    applied = set(getattr(t, "ca_applied", None) or [])
    ca = [c for c in parse_ca_rows(ca_rows) if f"{sym}|{c['ex_date']}" not in applied]
    e_ist, x_ist = to_ist(t.entry_date), to_ist(t.exit_date)
    if daily == "unset":
        daily = fetch_daily(t.ticker, (e_ist.date() - timedelta(days=7)).isoformat(),
                            (x_ist.date() + timedelta(days=1)).isoformat())
    mget = lambda d, i: fetch_minute(t.ticker, d, i)  # noqa: E731
    slip = float(getattr(cfg, "slippage_bps", SLIPPAGE_BPS_DEFAULT))
    buy = audit_side("BUY", t.entry_price, t.entry_date, t.ticker, daily, mget, ca,
                     tol_pct, slip, isin_note, ca_complete)
    sell = audit_side("SELL", t.exit_price, t.exit_date, t.ticker, daily, mget, ca,
                      tol_pct, slip, isin_note, ca_complete)
    between = ca_between(ca, e_ist.date().isoformat(), x_ist.date().isoformat())
    ratio = 1.0
    for c in _with_factor(between):
        ratio *= float(c["factor"])
    if abs(ratio - 1.0) < 1e-9:
        # a split AFTER the trade closed: both legs sit on the same pre-split
        # basis, right on their own days — nothing to restate, not a fault
        for side_row in (buy, sell):
            if side_row["flag"] == CA_UNADJUSTED:
                side_row["flag"] = OK
                side_row["note"] = ("pre-split price, right on its own day; the split "
                                    "came after this trade closed, so both legs are on "
                                    "one basis")
    side_flagged = bool({buy["flag"], sell["flag"]} & FLAGGED)
    restate = None
    if abs(ratio - 1.0) > 1e-9:
        # the owner's rule: sell as booked, buy restated on the post-split
        # basis; a side outside its own-day range still takes the real price
        entry_c = _side_price(buy, t.entry_price, rewrite_ok=False)
        exit_c = _side_price(sell, t.exit_price, rewrite_ok=False)
        corr = restated_trade(t.qty, entry_c, exit_c, ratio, cfg)
        restate = {"ratio": ratio, "qty": corr["qty"], "entry": corr["entry"],
                   "exit": corr["exit"], "frac_cash": corr["frac_cash"],
                   "frac_shares": corr["frac_shares"],
                   "actions": [ca_label(c) for c in _with_factor(between)],
                   "keys": [f"{sym}|{c['ex_date']}" for c in _with_factor(between)],
                   "why": (f"corporate action between buy and sell: entry ÷ {ratio:g}, "
                           f"qty × {ratio:g} (= {corr['qty']}), sell kept as booked")}
        corr_force = corr
    else:
        # contract item 3: a flagged trade is rewritten on BOTH sides to the
        # real price at the fill minute; an unflagged trade stays as stored
        # (force=1 rewrites it the same way)
        if side_flagged:
            corr = corrected_trade(t.qty, _side_price(buy, t.entry_price, True),
                                   _side_price(sell, t.exit_price, True), cfg)
        else:
            # nothing proposed: the record as stored (a cost-model change since
            # the close must not show as a "change" on a right trade)
            corr = {"entry": float(t.entry_price), "exit": float(t.exit_price),
                    "gross": float(t.gross_pnl), "costs": float(t.costs),
                    "net": float(t.net_pnl)}
        corr["qty"] = t.qty
        corr["frac_cash"] = 0.0
        corr_force = corrected_trade(t.qty, _side_price(buy, t.entry_price, True),
                                     _side_price(sell, t.exit_price, True), cfg)
        corr_force["qty"] = t.qty
        corr_force["frac_cash"] = 0.0
    # the fractional share (if any) is returned at cost inside the restated
    # legs, so the book's cash moves by exactly new net − old net
    delta = corr["net"] - float(t.net_pnl)
    flags = sorted({buy["flag"], sell["flag"]} & FLAGGED)
    if restate:
        flags.append("CA_BETWEEN")
    rights = [ca_label(c) for c in between if c.get("kind") == "rights"]
    needs_record = bool(buy.get("needs_ca_record") or sell.get("needs_ca_record"))
    return {
        "id": trade_id(t), "kind": "closed", "ticker": t.ticker, "qty": t.qty,
        "reason": t.reason, "mode": getattr(t, "mode", "") or "",
        "ca_applied_before": sorted(applied),
        "buy": buy, "sell": sell,
        "flags": flags, "flagged": bool(flags),
        "no_candle": NO_CANDLE in (buy["flag"], sell["flag"]),
        "ca_between": [ca_label(c) for c in between],
        "rights_between": rights,
        "restate": restate,
        "needs_ca_record": needs_record,
        "stored_net": float(t.net_pnl), "stored_entry": float(t.entry_price),
        "stored_exit": float(t.exit_price),
        # a fit on a common split ratio with no CA record: shown, never corrected
        "corrected": None if needs_record else corr,
        "corrected_force": None if needs_record else corr_force,
        "delta_net": 0.0 if needs_record else delta,
        "delta_cash": 0.0 if needs_record else delta,
        "checked": True,
    }


def audit_position(pos, fetch_daily: Callable, fetch_minute: Callable,
                   ca_rows: list, tol_pct: float = TOL_PCT_DEFAULT, cfg=None,
                   isin_note: str = "", daily: Optional[list] = "unset",
                   ca_complete: bool = True) -> dict:
    """An open position's entry. Cash was debited qty × entry at the open,
    so a corrected entry moves cash by qty × (stored − real)."""
    cfg = _cfg_costs(cfg)
    from trading.corporate_actions import bare as _bare
    applied = set(getattr(pos, "ca_applied", None) or [])
    sym = _bare(pos.ticker)
    # an action the ex-date adjuster already absorbed is not pending: the
    # stored entry is on the post-split basis, like the candles
    ca = [c for c in parse_ca_rows(ca_rows) if f"{sym}|{c['ex_date']}" not in applied]
    e_ist = to_ist(pos.entry_date)
    if daily == "unset":
        daily = fetch_daily(pos.ticker, (e_ist.date() - timedelta(days=7)).isoformat(),
                            (e_ist.date() + timedelta(days=1)).isoformat())
    mget = lambda d, i: fetch_minute(pos.ticker, d, i)  # noqa: E731
    slip = float(getattr(cfg, "slippage_bps", SLIPPAGE_BPS_DEFAULT))
    buy = audit_side("BUY", pos.entry_price, pos.entry_date, pos.ticker, daily, mget, ca,
                     tol_pct, slip, isin_note, ca_complete)
    entry_c = _side_price(buy, pos.entry_price, rewrite_ok=False)
    after = ca_after(ca, e_ist.date().isoformat())
    ratio = 1.0
    for c in after:
        ratio *= float(c["factor"])
    restate = None
    flags = sorted({buy["flag"]} & FLAGGED)
    delta_cash = pos.qty * (float(pos.entry_price) - entry_c)
    corrected = {"entry": entry_c, "exit": None, "gross": None, "costs": None, "net": None,
                 "qty": pos.qty}
    if abs(ratio - 1.0) > 1e-9:
        import math
        exact = pos.qty * ratio
        new_qty = int(math.floor(exact + 1e-9))
        new_entry = entry_c / ratio
        frac_cash = (exact - new_qty) * new_entry
        restate = {"ratio": ratio, "qty": new_qty, "entry": new_entry, "exit": None,
                   "frac_cash": frac_cash, "actions": [ca_label(c) for c in after],
                   "keys": [f"{sym}|{c['ex_date']}" for c in after],
                   "why": (f"split/bonus after the buy, position never adjusted: "
                           f"qty × {ratio:g} (= {new_qty}), entry ÷ {ratio:g}, "
                           f"stop/target/peak ÷ {ratio:g} — the same restatement the "
                           f"09:20 ex-date adjuster applies")}
        corrected = {"entry": new_entry, "exit": None, "gross": None, "costs": None,
                     "net": None, "qty": new_qty}
        delta_cash += frac_cash
        flags.append("CA_BETWEEN")
    corrected_force = corrected
    if restate is None:
        corrected_force = {**corrected, "entry": _side_price(buy, pos.entry_price, True)}
    needs_record = bool(buy.get("needs_ca_record"))
    return {
        "id": position_id(pos), "kind": "open", "ticker": pos.ticker, "qty": pos.qty,
        "reason": "OPEN", "mode": getattr(pos, "mode", "") or "",
        "buy": buy, "sell": None,
        "flags": flags, "flagged": bool(flags),
        "no_candle": buy["flag"] == NO_CANDLE,
        "ca_between": [ca_label(c) for c in after],
        "rights_between": [ca_label(c) for c in ca if c.get("kind") == "rights"
                           and c["ex_date"] > e_ist.date().isoformat()],
        "restate": restate,
        "needs_ca_record": needs_record,
        "stored_net": 0.0, "stored_entry": float(pos.entry_price), "stored_exit": None,
        "corrected": None if needs_record else corrected,
        "corrected_force": None if needs_record else corrected_force,
        "delta_net": 0.0,
        "delta_cash": 0.0 if needs_record else delta_cash,
        "checked": True,
    }


def unchecked_row(obj, kind: str) -> dict:
    """A row past the time budget: printed, never guessed."""
    if kind == "closed":
        rid, qty, reason = trade_id(obj), obj.qty, obj.reason
    else:
        rid, qty, reason = position_id(obj), obj.qty, "OPEN"
    return {"id": rid, "kind": kind, "ticker": obj.ticker, "qty": qty, "reason": reason,
            "mode": getattr(obj, "mode", "") or "", "buy": None, "sell": None,
            "flags": [], "flagged": False, "no_candle": False, "ca_between": [],
            "rights_between": [], "restate": None, "needs_ca_record": False,
            "stored_net": float(getattr(obj, "net_pnl", 0.0) or 0.0),
            "stored_entry": float(obj.entry_price),
            "stored_exit": float(getattr(obj, "exit_price", 0.0)) if kind == "closed" else None,
            "corrected": None, "corrected_force": None, "delta_net": 0.0, "delta_cash": 0.0,
            "checked": False, "note": "not checked this open — tap again"}


# ---------------------------------------------------------------------------
# journal source for a fill
# ---------------------------------------------------------------------------
def journal_source(journal: list, ticker: str, when: datetime) -> str:
    """'action: fill_source' of the journal line(s) within ±2 min of the fill
    for this ticker; '' when the RAM journal has none (older lines live in
    the Mongo archive)."""
    when = _as_dt(when)
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    found = []
    for e in journal or []:
        act = e.get("action")
        if act not in JOURNAL_ACTIONS:
            continue
        ts = e.get("ts")
        try:
            ets = datetime.fromisoformat(str(ts))
        except (TypeError, ValueError):
            continue
        if ets.tzinfo is not None:
            ets = ets.astimezone(timezone.utc).replace(tzinfo=None)
        if abs((ets - when).total_seconds()) > JOURNAL_MATCH_S:
            continue
        tick = e.get("ticker")
        if act == "OWNER_BATCH_BUY":
            opened = [o.get("ticker") for o in (e.get("opened") or [])]
            if ticker not in opened:
                continue
        elif act == "OWNER_SECTOR_EXIT":
            if ticker not in (e.get("closed") or []):
                continue
        elif tick != ticker:
            continue
        src = e.get("fill_source") or e.get("price_source") or ""
        if act == "BUY" and str(e.get("why") or "").startswith(OWNER_BUY_PREFIX):
            # the button's line carries a borrowed label, not its own source
            label = ("OWNER BUY button: bare quote, no slippage — quote source not "
                     f"journaled (the line's '{src or 'prev_close'}' is not this fill's)")
            found.append((1, label))
            continue
        if act == "OWNER_ORDER_FILLED":
            src = "limit order"
        if src:
            label = f"{act}: {src}"
        elif act in ("OWNER_BATCH_BUY", "OWNER_SECTOR_EXIT"):
            label = act                         # a batch line: context only
        else:
            label = f"{act}: quote source not journaled"
        found.append((1 if src else 0, label))
    if not found:
        return ""
    found.sort(key=lambda x: -x[0])
    # the line that names a source wins; the rest are listed once each
    seen, out = set(), []
    for _, s in found:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return " · ".join(out)


# ---------------------------------------------------------------------------
# the whole book, with a time budget and caches
# ---------------------------------------------------------------------------
_CANDLES: dict = {}          # (kind, ticker, a, b) -> (ts, candles)
_RESULT: dict = {}           # sig -> (ts, result) — the latest book only
_CA_FETCHES: dict = {}       # symbol -> (ts, rows | None) — NSE answers AND failures
_LOCK = threading.Lock()
# One correction at a time: the audit read, the rewrite and the save happen
# under this lock, so a double tap can never apply the same trade twice.
APPLY_LOCK = threading.Lock()
CANDLE_CACHE_MAX = 4000      # entries; expired ones are dropped first
CA_FAIL_TTL_S = 300          # a failed NSE call is not retried for 5 min


def clear_caches() -> None:
    _CANDLES.clear()
    _RESULT.clear()
    _CA_FETCHES.clear()
    _NSE_BREAKER.update(fails=0, open_until=0.0)


def _purge(now: float) -> None:
    if len(_CANDLES) < CANDLE_CACHE_MAX:
        return
    for k in [k for k, v in _CANDLES.items() if now - v[0] >= CACHE_TTL_S]:
        _CANDLES.pop(k, None)
    if len(_CANDLES) >= CANDLE_CACHE_MAX:
        for k in sorted(_CANDLES, key=lambda k: _CANDLES[k][0])[: len(_CANDLES) // 2]:
            _CANDLES.pop(k, None)


def _cached(key, fn):
    now = time.monotonic()
    hit = _CANDLES.get(key)
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    val = fn()
    if val is not None:                      # a failed fetch is retried next tap
        _purge(now)
        _CANDLES[key] = (now, val)
    return val


def book_signature(portfolio, archived_rows: list) -> tuple:
    """What the cached result was computed on: counts, cash AND every
    stored price / qty / applied action, so a correction or an ex-date
    adjustment that leaves the counts and cash unchanged still re-checks."""
    import hashlib
    h = hashlib.sha1()
    for t in portfolio.trades:
        h.update(f"{trade_id(t)}|{t.entry_price:.6f}|{t.exit_price:.6f}|{t.net_pnl:.4f}|"
                 f"{','.join(getattr(t, 'ca_applied', None) or [])};".encode())
    for pos in portfolio.positions.values():
        h.update(f"{position_id(pos)}|{pos.entry_price:.6f}|"
                 f"{','.join(getattr(pos, 'ca_applied', None) or [])};".encode())
    for d in archived_rows or []:
        h.update(f"{trade_id(d)}|{d.get('entry_price')}|{d.get('exit_price')}|"
                 f"{d.get('net_pnl')}|{d.get('ca_applied')};".encode())
    return (len(portfolio.trades), len(portfolio.positions), len(archived_rows or []),
            round(float(portfolio.cash), 2), h.hexdigest())


def audit_all(portfolio, archived_rows: list, fetch_daily: Callable,
              fetch_minute: Callable, ca_loader: Callable, cfg=None,
              budget: Optional[float] = None, tol_pct: float = TOL_PCT_DEFAULT,
              isin_check: Optional[Callable] = None, now: Optional[datetime] = None,
              ca_complete: Optional[Callable] = None) -> dict:
    """Every closed trade (live + archived, newest first) and every open
    position. One daily fetch per ticker over its span, one minute fetch per
    fill date, all cached 30 min; rows past the time budget are printed as
    NOT CHECKED. `ca_loader(symbol)` → raw CA rows; `isin_check(ticker)` →
    a note when the CA file's ISIN differs from the current one;
    `ca_complete(symbol)` → False when the live NSE fetch did not answer
    (None = every caller-supplied row set is taken as complete)."""
    from trading.paper import ClosedTrade
    cfg = _cfg_costs(cfg)
    budget = budget_s() if budget is None else float(budget)
    t0 = time.monotonic()
    started = (now or datetime.utcnow()).isoformat()

    closed: list = []                       # (trade, pre_mars, archived, row id)
    live_ids = set()
    for t in list(portfolio.trades):
        closed.append((t, False, False, trade_id(t)))
        live_ids.add(trade_id(t))
    for d in archived_rows or []:
        try:
            dd = {k: v for k, v in d.items() if k not in ("pre_mars", "_id")}
            ct = ClosedTrade.from_dict(dd)
        except Exception:                       # malformed archive doc stays out
            continue
        # the archive's own _id is the row id: it keeps the ORIGINAL qty, so a
        # restated row (qty × ratio) is still found by update_trade_prices
        rid = str(d.get("_id") or trade_id(ct))
        if trade_id(ct) in live_ids or rid in live_ids:
            continue                            # archived AND still in the book
        closed.append((ct, bool(d.get("pre_mars")), True, rid))
    closed.sort(key=lambda x: _as_dt(x[0].exit_date), reverse=True)
    opens = sorted(portfolio.positions.values(), key=lambda p: _as_dt(p.entry_date),
                   reverse=True)

    # span per ticker → one daily fetch each
    span: dict = {}
    for t, _, _, _ in closed:
        a, b = to_ist(t.entry_date).date(), to_ist(t.exit_date).date()
        lo, hi = span.get(t.ticker, (a, b))
        span[t.ticker] = (min(lo, a), max(hi, b))
    for p in opens:
        a = to_ist(p.entry_date).date()
        lo, hi = span.get(p.ticker, (a, a))
        span[p.ticker] = (min(lo, a), max(hi, a))

    daily_for: dict = {}
    ca_for: dict = {}
    isin_for: dict = {}
    complete_for: dict = {}

    def _prep(ticker: str) -> bool:
        if ticker in daily_for:
            return True
        if budget > 0 and time.monotonic() - t0 > budget:
            return False
        lo, hi = span[ticker]
        a, b = (lo - timedelta(days=7)).isoformat(), (hi + timedelta(days=1)).isoformat()
        daily_for[ticker] = _cached(("d", ticker, a, b), lambda: fetch_daily(ticker, a, b))
        try:
            ca_for[ticker] = ca_loader(bare(ticker)) or []
        except Exception as e:                  # noqa: BLE001
            log.warning("CA rows for %s unreadable: %s", ticker, e)
            ca_for[ticker] = []
        isin_for[ticker] = (isin_check(ticker) if isin_check else "") or ""
        try:
            complete_for[ticker] = True if ca_complete is None else bool(ca_complete(bare(ticker)))
        except Exception:                       # noqa: BLE001
            complete_for[ticker] = False
        return True

    cut = [False]                           # the budget skipped a minute fetch

    def _mget(ticker, date_iso, interval):
        """fetch_minute(ticker, date, interval) with the budget + cache. Past
        the budget nothing is fetched and the row is marked NOT CHECKED (a
        daily close standing in for a minute price never asked for would
        read as 'no minute candles served' — and price a correction)."""
        key = ("m", ticker, date_iso, interval)
        hit = _CANDLES.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1]
        if budget > 0 and time.monotonic() - t0 > budget:
            cut[0] = True
            return None
        return _cached(key, lambda: _compact(fetch_minute(ticker, date_iso, interval)))

    journal = list(getattr(portfolio, "decision_journal", []) or [])
    rows: list = []
    skipped = 0
    for t, pre, arch, rid in closed:
        cut[0] = False
        if not _prep(t.ticker):
            r = unchecked_row(t, "closed")
            skipped += 1
        else:
            r = audit_trade(t, fetch_daily, _mget, ca_for[t.ticker], tol_pct, cfg,
                            isin_for[t.ticker], daily=daily_for[t.ticker],
                            ca_complete=complete_for[t.ticker])
            if cut[0]:
                r = unchecked_row(t, "closed")
                skipped += 1
            else:
                r["buy"]["source"] = journal_source(journal, t.ticker, t.entry_date)
                r["sell"]["source"] = journal_source(journal, t.ticker, t.exit_date)
        r["id"] = rid
        r["pre_mars"] = pre
        r["archived"] = arch
        r["entry_date"] = to_ist(t.entry_date).strftime("%Y-%m-%d")
        r["exit_date"] = to_ist(t.exit_date).strftime("%Y-%m-%d")
        rows.append(r)
    for p in opens:
        cut[0] = False
        if not _prep(p.ticker):
            r = unchecked_row(p, "open")
            skipped += 1
        else:
            r = audit_position(p, fetch_daily, _mget, ca_for[p.ticker], tol_pct, cfg,
                               isin_for[p.ticker], daily=daily_for[p.ticker],
                               ca_complete=complete_for[p.ticker])
            if cut[0]:
                r = unchecked_row(p, "open")
                skipped += 1
            else:
                r["buy"]["source"] = journal_source(journal, p.ticker, p.entry_date)
        r["pre_mars"] = False
        r["archived"] = False
        r["entry_date"] = to_ist(p.entry_date).strftime("%Y-%m-%d")
        r["exit_date"] = ""
        rows.append(r)

    fills = sum((1 if r["kind"] == "open" else 2) for r in rows if r["checked"])
    outside = 0
    for r in rows:
        if not r["checked"]:
            continue
        for side in (r["buy"], r["sell"]):
            if side and side["flag"] in FLAGGED:
                outside += 1
    with_ca = sum(1 for r in rows if r["ca_between"])
    no_candle = sum(1 for r in rows if r["no_candle"])
    needs_record = sum(1 for r in rows if r.get("needs_ca_record"))
    cash_delta = sum(r["delta_cash"] for r in rows if r["flagged"] and r.get("corrected"))
    return {
        "rows": rows,
        "summary": {"fills": fills, "outside": outside, "with_ca": with_ca,
                    "no_candle": no_candle, "cash_delta": cash_delta,
                    "needs_ca_record": needs_record,
                    "flagged_rows": sum(1 for r in rows if r["flagged"]),
                    "unchecked": skipped, "checked": len(rows) - skipped, "total": len(rows)},
        "budget_s": budget, "elapsed_s": round(time.monotonic() - t0, 2),
        "tol_pct": tol_pct, "tol_note": TOL_NOTE, "asof": started,
        "stopped": skipped > 0,
    }


# ---------------------------------------------------------------------------
# real fetchers (Upstox v3, tokenless) + CA / ISIN readers
# ---------------------------------------------------------------------------
_IKEYS: Optional[dict] = None


def _ikeys() -> dict:
    global _IKEYS
    if _IKEYS is None:
        try:
            from trading.brokers.upstox_instruments import committed_lookup
            _IKEYS = committed_lookup()
        except Exception as e:                  # noqa: BLE001
            log.warning("instrument lookup unreadable: %s", e)
            _IKEYS = {}
    return _IKEYS


def _nsify(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not (t.endswith(".NS") or t.endswith(".BO")):
        t += ".NS"
    return t


def instrument_keys(ticker: str) -> list:
    """Keys to try, best first: the live master's (daily-refreshed, with the
    08:50 refresh's preferred keys — a split that changed the ISIN lands
    here: BLSE INE0NLT01010 → INE0NLT01028), then the committed file's when
    it differs. Upstox refuses a retired ISIN ("Invalid Instrument key")."""
    t = _nsify(ticker)
    out = []
    try:
        from trading.brokers.upstox_instruments import to_instrument_key
        k = to_instrument_key(t)
        if k:
            out.append(k)
    except Exception as e:                      # noqa: BLE001
        log.info("live instrument lookup unavailable: %s", e)
    k2 = _ikeys().get(t)
    if k2 and k2 not in out:
        out.append(k2)
    return out


def instrument_key(ticker: str) -> Optional[str]:
    keys = instrument_keys(ticker)
    return keys[0] if keys else None


def _get_json(url: str) -> Optional[dict]:
    import httpx
    try:
        r = httpx.get(url, headers={"accept": "application/json"}, timeout=HTTP_TIMEOUT_S)
        if r.status_code != 200:
            log.info("upstox candle %s -> %s %s", url, r.status_code, r.text[:120])
            return None
        return r.json()
    except Exception as e:                      # noqa: BLE001
        log.info("upstox candle fetch failed %s: %s", url, e)
        return None


def _first_answer(ticker: str, path: str) -> Optional[dict]:
    """GET the candle path on each candidate key until one answers."""
    from urllib.parse import quote
    for ik in instrument_keys(ticker):
        d = _get_json(f"{V3_URL}/{quote(ik, safe='')}/{path}")
        if d:
            return d
    return None


def fetch_daily_upstox(ticker: str, from_iso: str, to_iso: str) -> Optional[list]:
    """[{date, open, high, low, close}] ascending; None on failure / unknown
    key. Dates are the candle's IST day."""
    d = _first_answer(ticker, f"days/1/{to_iso}/{from_iso}")
    if not d:
        return None
    out = []
    for c in (d.get("data") or {}).get("candles") or []:
        try:
            out.append({"date": str(c[0])[:10], "open": float(c[1]), "high": float(c[2]),
                        "low": float(c[3]), "close": float(c[4])})
        except (IndexError, TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["date"])
    return out


def fetch_minute_upstox(ticker: str, date_iso: str, interval: int = 1) -> Optional[list]:
    """[{ts (aware IST), open, high, low, close}] for one day; None on
    failure. Upstox serves 1-minute bars for roughly two months back."""
    d = _first_answer(ticker, f"minutes/{int(interval)}/{date_iso}/{date_iso}")
    if not d:
        return None
    out = []
    for c in (d.get("data") or {}).get("candles") or []:
        try:
            out.append({"ts": datetime.fromisoformat(str(c[0])), "open": float(c[1]),
                        "high": float(c[2]), "low": float(c[3]), "close": float(c[4])})
        except (IndexError, TypeError, ValueError):
            continue
    return out


def ca_rows_for(symbol: str) -> list:
    """Raw CA rows from every source: the Mongo store (the 08:50 fetch for
    held names), the committed file, and a LIVE NSE fetch for this symbol
    (cached 30 min; a closed trade's name is no longer held, so the store
    may not carry it — BLSE's split is in neither the file nor the store
    until fetched). A failed live fetch is simply absent, never 'no CA'."""
    from trading import corporate_actions as tca
    sym = tca.bare(symbol)
    live = _nse_rows(sym)
    return (list(tca.store_rows([sym])) + tca.remembered_rows(sym)
            + tca.committed_rows(sym) + list(live or []))


# NSE's own API is a courtesy from a cloud box: after this many failures in a
# row it is not asked again for CA_FAIL_TTL_S, so a blocked or hanging NSE
# cannot eat the audit's time budget one name at a time (≤ 15 s each). The
# rows then come from the store + committed file and the row says DATA_NEEDED
# where a split might be missing (ca_complete False).
NSE_MAX_FAILS = 3
NSE_AUDIT_TIMEOUT_S = 6.0
_NSE_BREAKER = {"fails": 0, "open_until": 0.0}


def _nse_rows(sym: str):
    """NSE's rows for the symbol, cached 30 min; a FAILED call is cached
    too (5 min), so the rows reader and the completeness check never wait
    on two timeouts for one name."""
    from trading import corporate_actions as tca
    now = time.monotonic()
    hit = _CA_FETCHES.get(sym)
    if hit is not None:
        ttl = CACHE_TTL_S if hit[1] is not None else CA_FAIL_TTL_S
        if now - hit[0] < ttl:
            return hit[1]
    if now < _NSE_BREAKER["open_until"]:
        return None                             # NSE not answering: not asked
    rows = tca.fetch_nse_actions(sym, timeout=NSE_AUDIT_TIMEOUT_S)
    _CA_FETCHES[sym] = (now, rows)
    if rows is None:
        _NSE_BREAKER["fails"] += 1
        if _NSE_BREAKER["fails"] >= NSE_MAX_FAILS:
            _NSE_BREAKER["open_until"] = now + CA_FAIL_TTL_S
            _NSE_BREAKER["fails"] = 0
    else:
        _NSE_BREAKER["fails"] = 0
    return rows


def isin_note_for(ticker: str) -> str:
    """When the universe FILE's key and the live master's key differ, or the
    CA rows carry another ISIN, a face-value change re-listed the share:
    an old key is refused by Upstox and an old fill is on the old basis."""
    keys = instrument_keys(ticker)
    if len(keys) > 1:
        return (f"ISIN changed: Upstox master lists {keys[0].split('|')[-1]}, the universe "
                f"file {keys[1].split('|')[-1]} — a face-value change re-listed the share")
    cur = keys[0].split("|")[-1] if keys and "|" in keys[0] else ""
    try:
        from trading import corporate_actions as tca
        rows = tca.committed_rows(bare(ticker))
    except Exception:                           # noqa: BLE001
        return ""
    isins = [(r.get("exDate") or "", (r.get("isin") or "").strip()) for r in rows if r.get("isin")]
    if not isins or not cur:
        return ""
    newest = sorted(isins, key=lambda x: _iso_ca_date(x[0]) or "")[-1][1]
    if newest and newest != cur:
        return (f"ISIN changed: CA file carries {newest}, Upstox now lists {cur} — a face-value "
                f"change after the CA file's last row may explain the gap (DATA_NEEDED)")
    return ""


def ca_complete_for(symbol: str) -> bool:
    """True when NSE's corporate-action API answered for the symbol in this
    audit (cached with the rows), so 'no split row' means no split."""
    from trading import corporate_actions as tca
    return _nse_rows(tca.bare(symbol)) is not None


def upstox_fetchers() -> tuple:
    return (fetch_daily_upstox, fetch_minute_upstox, ca_rows_for, isin_note_for,
            ca_complete_for)


BUSY_NOTE = ("A scan rotation is running right now. The audit fetches the candles "
             "of every trade, and the 512 MB box cannot carry that on top of a "
             "rotation — open the audit again once the rotation has finished "
             "(nothing was checked or changed).")


def busy_result(cfg=None) -> dict:
    return {"rows": [], "summary": None, "error": BUSY_NOTE, "busy": True,
            "budget_s": budget_s(), "elapsed_s": 0, "stopped": False,
            "tol_pct": TOL_PCT_DEFAULT, "tol_note": TOL_NOTE, "asof": ""}


def run_audit(portfolio, archived_rows: list, cfg=None, fetchers=None,
              budget: Optional[float] = None, force: bool = False,
              busy: Optional[Callable] = None) -> dict:
    """The page entry point: cached 30 min per book signature; an incomplete
    (budget-stopped) result is recomputed on the next tap, the candle cache
    carrying what was already fetched. `busy()` True (a rotation holds the
    box) = a cached result or a "come back" note, never a fresh fetch."""
    sig = book_signature(portfolio, archived_rows)
    now = time.monotonic()
    hit = _RESULT.get(sig)
    if hit and not force and now - hit[0] < CACHE_TTL_S and not hit[1].get("stopped"):
        return hit[1]
    try:
        if busy is not None and busy():
            return busy_result(cfg)
    except Exception:                           # noqa: BLE001
        pass
    fs = tuple(fetchers or upstox_fetchers())
    fd, fm, ca, isin = fs[:4]
    complete = fs[4] if len(fs) > 4 else None      # a 4-tuple vouches for its rows
    with _LOCK:
        hit = _RESULT.get(sig)
        if hit and not force and now - hit[0] < CACHE_TTL_S and not hit[1].get("stopped"):
            return hit[1]
        res = audit_all(portfolio, archived_rows, fd, fm, ca, cfg, budget, isin_check=isin,
                        ca_complete=complete)
        _RESULT.clear()                         # one book, one result
        _RESULT[sig] = (time.monotonic(), res)
    try:
        from .memory_relief import release
        release("trade_audit")
    except Exception:                           # noqa: BLE001
        pass
    return res


def _pending_actions(pos, row: dict) -> list:
    """The split/bonus rows the audit row restated, in the shape
    corporate_actions.apply_to_position takes (keys ca_key uses)."""
    from trading.corporate_actions import bare as _bare
    sym = _bare(pos.ticker)
    rs = row.get("restate") or {}
    out = []
    for label, key in zip(rs.get("actions") or [], rs.get("keys") or []):
        subj, _, _ex = label.rpartition(" ex ")
        out.append({"symbol": sym, "ex_date": key.split("|", 1)[1], "subject": subj,
                    "kind": "split", "ratio": 1.0, "source": "audit"})
    # the ratio lives on the whole restatement; apply it once, on the first
    # row, and record every action key so the adjuster skips all of them
    if out:
        out[0]["ratio"] = float(row["restate"]["ratio"])
        for o in out[1:]:
            o["ratio"] = 1.0
    return out


def find_row(result: dict, rid: str) -> Optional[dict]:
    for r in result.get("rows") or []:
        if r["id"] == rid:
            return r
    return None


# ---------------------------------------------------------------------------
# the owner-tapped correction
# ---------------------------------------------------------------------------
def shift_baselines(portfolio, delta: float, now: datetime) -> dict:
    """A correction restates PAST fills, so today's loss-halt baseline and
    the drawdown peak move with the cash it moves — a bookkeeping tap must
    never read as a trading loss (the kill switch and the drawdown ladder
    both SELL). The sync_capital rule for a capital change (2026-10-02),
    applied to a price correction. Returns what moved, for the journal."""
    moved = {}
    if not delta:
        return moved
    day = now.date().isoformat()                # risk_day is the engine's UTC date
    rde = float(getattr(portfolio, "risk_day_equity", 0.0) or 0.0)
    if getattr(portfolio, "risk_day", None) == day and rde > 0:
        portfolio.risk_day_equity = max(0.0, rde + delta)
        moved["risk_day_equity"] = round(portfolio.risk_day_equity, 2)
    peak = float(getattr(portfolio, "dd_peak_equity", 0.0) or 0.0)
    if peak > 0:
        portfolio.dd_peak_equity = max(0.0, peak + delta)
        moved["dd_peak_equity"] = round(portfolio.dd_peak_equity, 2)
    return moved


def apply_corrections(portfolio, result: dict, ids: list, cfg=None, side: str = "",
                      force: bool = False, update_archived: Optional[Callable] = None,
                      now: Optional[datetime] = None) -> dict:
    """Rewrite the fills of the given rows to the real minute prices (both
    sides unless `side` limits it), recompute gross/costs/net with
    trade_costs, move cash by the net delta (closed) or qty × entry delta
    (open), journal TRADE_PRICE_CORRECTED once per row. Refused: an id not
    in the audit, an unflagged id (unless force), an id the journal already
    names as corrected (unless force), a row whose correction would change
    nothing. force=1 recomputes from the CURRENT stored prices (the audit
    the caller just ran) — an already-restated trade carries its applied
    action in ca_applied, so its entry is never divided twice.
    `update_archived(id, fields)` → bool updates a Mongo-archived row.
    Caller saves the book (and holds APPLY_LOCK around audit + apply + save)."""
    cfg = _cfg_costs(cfg)
    now = now or datetime.utcnow()
    side = (side or "").upper()
    done, refused = [], []
    live_by_id = {trade_id(t): t for t in portfolio.trades}
    pos_by_id = {position_id(p): p for p in portfolio.positions.values()}
    corrected_before = set()
    for e in portfolio.decision_journal or []:
        if e.get("action") == "TRADE_PRICE_CORRECTED":
            # a restated trade's id moves with its qty: both ids count
            corrected_before.update(x for x in (e.get("id"), e.get("new_id")) if x)
    for rid in dict.fromkeys(ids):          # a repeated id in one POST counts once
        r = find_row(result, rid)
        if r is None:
            refused.append({"id": rid, "why": "not in the audit — open /trades?audit=1 first"})
            continue
        if not r["checked"] or r["buy"] is None:
            refused.append({"id": rid, "why": "not checked yet"})
            continue
        if rid in corrected_before and not force:
            refused.append({"id": rid, "why": "already corrected (journal TRADE_PRICE_CORRECTED)"})
            continue
        if not r["flagged"] and not force:
            refused.append({"id": rid, "why": "not flagged (force=1 to override)"})
            continue
        if r.get("needs_ca_record") or r.get("corrected") is None:
            refused.append({"id": rid, "why": "needs the corporate-action record first "
                                              "(never corrected on a guessed split)"})
            continue
        corr0 = r["corrected"] if r["flagged"] else (r.get("corrected_force") or r["corrected"])
        if r["kind"] == "open":
            pos = pos_by_id.get(rid)
            if pos is None:
                moved = any(q.ticker == r["ticker"] for q in portfolio.positions.values())
                refused.append({"id": rid, "why": (
                    "position changed since the audit read (qty moved — the ex-date "
                    "adjuster?) — reopen the audit" if moved else "position no longer open")})
                continue
            if side == "SELL":
                refused.append({"id": rid, "why": "an open position has no sell side"})
                continue
            rs = r.get("restate")
            # the row must still describe this position: the ex-date adjuster
            # (another thread) may have restated it after the audit read
            done_keys = set(getattr(pos, "ca_applied", None) or [])
            if (abs(float(pos.entry_price) - float(r["stored_entry"])) > 1e-9
                    or (rs and done_keys & set(rs.get("keys") or []))):
                refused.append({"id": rid, "why": "position changed since the audit read "
                                                  "(already restated?) — reopen the audit"})
                continue
            old = float(pos.entry_price)
            old_qty = int(pos.qty)
            if rs:
                # the owner's restatement — the ex-date adjuster's own maths,
                # so the 09:20 job cannot apply the same action twice
                from trading.corporate_actions import apply_to_position
                base = float(corr0["entry"]) * float(rs["ratio"])  # own-day basis
                delta_cash = pos.qty * (old - base)
                pos.entry_price = base
                total_credit = 0.0
                for ca in _pending_actions(pos, r):
                    body = apply_to_position(pos, ca)
                    total_credit += float(body["cash_credit"])
                delta_cash += total_credit
                new = float(pos.entry_price)
            else:
                new = float(corr0["entry"])
                if abs(new - old) < 1e-9:
                    refused.append({"id": rid, "why": "nothing to change (the stored entry "
                                                      "is the real price)"})
                    continue
                delta_cash = pos.qty * (old - new)
                pos.entry_price = new
                if pos.peak_price < new:
                    pos.peak_price = new
            portfolio.cash += delta_cash
            moved = shift_baselines(portfolio, delta_cash, now)
            line = {
                "ts": now.isoformat(), "action": "TRADE_PRICE_CORRECTED", "id": rid,
                "baselines_moved": moved,
                "ticker": pos.ticker, "kind": "open", "qty": pos.qty,
                "before": {"entry": round(old, 2), "exit": None, "net": None, "qty": old_qty},
                "after": {"entry": round(new, 2), "exit": None, "net": None, "qty": int(pos.qty)},
                "corporate_action": (rs["actions"] if rs else None),
                "ratio": (rs["ratio"] if rs else None),
                "sources": {"buy": r["buy"].get("real_source", ""),
                            "buy_journal": r["buy"].get("source", "")},
                "candles": {"buy": {"date": r["buy"]["date_ist"], "low": r["buy"]["day_low"],
                                    "high": r["buy"]["day_high"],
                                    "minute": r["buy"]["minute_price"]}},
                "delta_cash": round(delta_cash, 2), "owner": True, "forced": bool(force),
                "why": ((f"Owner restated the open position for {'; '.join(rs['actions'])}: "
                         f"qty × {rs['ratio']:g}, entry ÷ {rs['ratio']:g}, stop/target/peak ÷ "
                         f"{rs['ratio']:g} — the ex-date adjuster's own maths; nothing sold.")
                        if rs else
                        ("Owner corrected the entry to the real Upstox candle price at the fill "
                         "minute (+5 bps slippage, the engine's own fill rule). Stop/target "
                         "levels untouched. Cash moved by qty × (old − new).")),
                "cash_after": round(portfolio.cash, 2),
            }
            portfolio.decision_journal.append(line)
            done.append({"id": rid, "ticker": pos.ticker, "delta_cash": delta_cash})
            continue

        # closed trade (live or archived)
        t = live_by_id.get(rid)
        if t is None and not r.get("archived"):
            refused.append({"id": rid, "why": "trade no longer in the book"})
            continue
        buy, sell = r["buy"], r["sell"]
        old_entry, old_exit, old_net = r["stored_entry"], r["stored_exit"], r["stored_net"]
        if t is not None and (abs(float(t.entry_price) - float(old_entry)) > 1e-9
                              or abs(float(t.exit_price) - float(old_exit)) > 1e-9):
            refused.append({"id": rid, "why": "trade changed since the audit read — "
                                              "reopen the audit"})
            continue
        rs = r.get("restate")
        new_entry = old_entry
        new_exit = old_exit
        # the audit's own-day prices: stored when the side was right on its
        # day, the real minute price otherwise
        own_entry = float(corr0["entry"]) * (float(rs["ratio"]) if rs else 1.0)
        own_exit = float(corr0["exit"])
        if side in ("", "BUY"):
            new_entry = own_entry
        if side in ("", "SELL"):
            new_exit = own_exit
        new_qty = int(r["qty"])
        frac_cash = 0.0
        if rs:
            # the restatement is the whole trade (qty moves both legs)
            corr = restated_trade(int(r["qty"]), new_entry, new_exit, float(rs["ratio"]), cfg)
            new_entry, new_qty, frac_cash = corr["entry"], corr["qty"], corr["frac_cash"]
        else:
            if abs(new_entry - old_entry) < 1e-9 and abs(new_exit - old_exit) < 1e-9:
                refused.append({"id": rid, "why": "nothing to change (the stored fills are "
                                                  "the real prices)"})
                continue
            corr = corrected_trade(new_qty, new_entry, new_exit, cfg)
        # a fractional share is returned at cost inside the restated legs:
        # cash moves by exactly new net − old net
        delta_cash = corr["net"] - float(old_net)
        fields = {"entry_price": new_entry, "exit_price": new_exit, "qty": new_qty,
                  "gross_pnl": corr["gross"], "costs": corr["costs"], "net_pnl": corr["net"]}
        if rs:
            prior = (getattr(t, "ca_applied", None) if t is not None
                     else r.get("ca_applied_before")) or []
            fields["ca_applied"] = sorted(set(prior) | set(rs.get("keys") or []))
        new_id = rid
        if t is not None:
            t.entry_price, t.exit_price, t.qty = new_entry, new_exit, new_qty
            t.gross_pnl, t.costs, t.net_pnl = corr["gross"], corr["costs"], corr["net"]
            if rs:
                t.ca_applied = fields["ca_applied"]
            new_id = trade_id(t)            # qty × ratio moves a live trade's id
            where = "book"
        else:
            ok = bool(update_archived(rid, fields)) if update_archived else False
            if not ok:
                refused.append({"id": rid, "why": "archived row: Mongo update refused"})
                continue
            where = "archive"
        portfolio.cash += delta_cash
        moved = shift_baselines(portfolio, delta_cash, now)
        line = {
            "ts": now.isoformat(), "action": "TRADE_PRICE_CORRECTED", "id": rid,
            "new_id": new_id, "baselines_moved": moved,
            "ticker": r["ticker"], "kind": "closed", "where": where, "qty": r["qty"],
            "before": {"entry": round(old_entry, 2), "exit": round(old_exit, 2),
                       "net": round(float(old_net), 2), "qty": int(r["qty"])},
            "after": {"entry": round(new_entry, 2), "exit": round(new_exit, 2),
                      "net": round(corr["net"], 2), "qty": new_qty},
            "corporate_action": (rs["actions"] if rs else None),
            "ratio": (rs["ratio"] if rs else None),
            "fractional_share_cost_returned": round(frac_cash, 2),
            "sources": {"buy": buy.get("real_source", "") if buy else "",
                        "sell": sell.get("real_source", "") if sell else "",
                        "buy_journal": buy.get("source", "") if buy else "",
                        "sell_journal": sell.get("source", "") if sell else ""},
            "candles": {
                "buy": ({"date": buy["date_ist"], "low": buy["day_low"], "high": buy["day_high"],
                         "minute": buy["minute_price"]} if buy else None),
                "sell": ({"date": sell["date_ist"], "low": sell["day_low"],
                          "high": sell["day_high"], "minute": sell["minute_price"]} if sell else None),
            },
            "delta_cash": round(delta_cash, 2), "owner": True, "side": side or "BOTH",
            "forced": bool(force),
            "why": ((f"Owner restated the trade for {'; '.join(rs['actions'])}: sell kept as "
                     f"booked, buy on the post-action basis (entry ÷ {rs['ratio']:g}, qty × "
                     f"{rs['ratio']:g}); gross/costs/net recomputed with trade_costs; cash moved "
                     f"by (new net − old net).") if rs else
                    ("Owner corrected the fills to the real Upstox candle prices at the fill "
                     "minutes (± 5 bps slippage, the engine's own fill rule); gross/costs/net "
                     "recomputed with trade_costs; cash moved by (new net − old net).")),
            "cash_after": round(portfolio.cash, 2),
        }
        portfolio.decision_journal.append(line)
        done.append({"id": rid, "ticker": r["ticker"], "delta_cash": delta_cash})
    if done:
        _RESULT.clear()
    return {"done": done, "refused": refused,
            "delta_cash": sum(d["delta_cash"] for d in done)}
