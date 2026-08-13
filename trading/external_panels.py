"""External data panels — the feeds the owner's trigger layer was waiting for.

Owner order 2026-08-13: "why u r waiting for 28 which u can wire, do that".
Until the Colab v2 / asset intake landed, 33 of the owner's 112 triggers
returned DATA_NEEDED because the app held no sector panel, no delivery
percentages, no F&O open interest and no bulk/block deals. Those files are
now committed, so 21 of the 33 can be computed from data in this repo.

What each panel is built from (nothing here reaches the network):
  * sector / peer / RS  — the committed 191x12y OHLCV panel
    (data/history_upstox_export_2026-07-19.zip) grouped by
    universes.NIFTY_200_SECTOR_MAP_EXTRAS (191 names, 19 sectors)
  * index overlay       — data/index_daily_15y.csv (Nifty 50 close)
  * delivery            — data/delivery_daily_2019_2026.csv.gz
  * F&O open interest   — data/fo_oi_daily_2024_2026.csv
  * bulk / block deals  — data/{bulk,block}_deals_2019_2026.csv

Honesty rules, same as the rest of the app:
  * A missing file, a symbol absent from a panel, or too little history
    returns None. None means DATA_NEEDED at the trigger layer — it NEVER
    degrades into a guessed False, which would read as "computed quiet".
  * `prepare()` makes ONE pass over the big delivery file for the whole
    batch of symbols (3.5M rows, 512MB box) instead of one pass per stock.
  * The deals panels are known-truncated at the source: NSE's endpoint
    capped busy months at 70 rows, so a quiet deal flag is "no deal in the
    slice we have", not "no deal happened". `DEALS_TRUNCATED` says so and
    the page prints it.

Boundary (test-enforced, same as triggers.py and lens_v2.py): trading/
engine.py, risk.py and orders.py must never import this module. Display and
watchlist only.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parents[1] / "data"
_PANEL_ZIP = _DATA / "history_upstox_export_2026-07-19.zip"
_INDEX_CSV = _DATA / "index_daily_15y.csv"
_DELIVERY_GZ = _DATA / "delivery_daily_2019_2026.csv.gz"
_FO_OI_CSV = _DATA / "fo_oi_daily_2024_2026.csv"
_BULK_CSV = _DATA / "bulk_deals_2019_2026.csv"
_BLOCK_CSV = _DATA / "block_deals_2019_2026.csv"

# The deals files are top-of-window slices, not full history — NSE's endpoint
# capped each month at 70 rows (all 92 months for bulk, 39 of 91 for block).
DEALS_TRUNCATED = (
    "NSE returned at most 70 rows per month: every bulk-deal month and 39 of "
    "91 block-deal months are truncated. A quiet flag means 'no deal in the "
    "rows we hold', not 'no deal occurred'.")

# Constants the owner's sheet names only in words ("threshold", "improves").
# Collected here for their calibration run to replace with measured numbers,
# exactly like triggers.ASSUMED_CONSTANTS.
ASSUMED_EXTERNAL_CONSTANTS = {
    "rs_outperformance_10d": 0.03,     # "> threshold" for RS lead (3pp / 10d)
    "rs_underperformance_10d": 0.03,   # "< -threshold" for RS lag
    "rank_jump_points": 20.0,          # "improves by >=20 points" (their number)
    "turnover_rank_jump_points": 25.0,  # "improves >=25 percentile" (their number)
    "top_decile_pct": 10.0,            # "industry_relative_rank <= 10%"
    "delivery_pctile": 80.0,           # "above 80th percentile" (their number)
    "peer_breakdown_frac": 0.30,       # ">=30% of industry" (their number)
    "peer_break_min_count": 3,         # ">=3 peers break 20D low"
    "peer_flag_min_count": 3,          # "at least 3 of top 5 peers"
    "near_support_frac": 0.03,         # "near support" = within 3% of 20D low
    "deal_window_days": 10,            # "bulk_deal_buy_flag" recency window
    "rs_slope_flat": 0.0,              # "RS slope flatten" = slope <= 0
}

_PANEL_TAIL = 420          # bars kept per panel name (RAM on the 512MB box)
_MIN_PANEL_BARS = 60


# --------------------------------------------------------------------------
# Panel loading (cached at module level — the layer runs once a day)
# --------------------------------------------------------------------------

_panel_cache: Optional[dict[str, pd.DataFrame]] = None


def _sector_map() -> dict[str, str]:
    try:
        from .universes import NIFTY_200_SECTOR_MAP_EXTRAS
        return dict(NIFTY_200_SECTOR_MAP_EXTRAS)
    except Exception as e:                                  # pragma: no cover
        log.warning("external_panels: sector map unavailable: %s", e)
        return {}


def _load_panel() -> dict[str, pd.DataFrame]:
    """{ticker: DataFrame(close, volume)} for the committed 191-name panel."""
    global _panel_cache
    if _panel_cache is not None:
        return _panel_cache
    out: dict[str, pd.DataFrame] = {}
    try:
        with zipfile.ZipFile(_PANEL_ZIP) as z:
            for name in z.namelist():
                if not name.endswith(".csv"):
                    continue
                ticker = name[:-4]
                try:
                    df = pd.read_csv(io.BytesIO(z.read(name)),
                                     parse_dates=["Date"], index_col="Date")
                except Exception:
                    continue
                cols = {c.lower(): c for c in df.columns}
                if "close" not in cols:
                    continue
                keep = pd.DataFrame({"close": df[cols["close"]].astype(float)})
                if "volume" in cols:
                    keep["volume"] = df[cols["volume"]].astype(float)
                out[ticker] = keep.sort_index().tail(_PANEL_TAIL)
    except OSError as e:
        log.warning("external_panels: panel zip unreadable: %s", e)
    _panel_cache = out
    return out


def _load_index_close() -> Optional[pd.Series]:
    """Nifty 50 daily close from the committed index file."""
    try:
        df = pd.read_csv(_INDEX_CSV)
    except OSError:
        return None
    date_col = next((c for c in df.columns if c.lower() in
                     ("trade_date", "date", "dt", "timestamp")), None)
    close_col = next((c for c in df.columns
                      if c.lower().endswith("close") and "nifty" in c.lower()
                      and "bank" not in c.lower() and "200" not in c.lower()),
                     None)
    if date_col is None or close_col is None:
        log.warning("external_panels: index file lacks a Nifty close column")
        return None
    s = pd.Series(pd.to_numeric(df[close_col], errors="coerce").to_numpy(),
                  index=pd.to_datetime(df[date_col], errors="coerce",
                                       dayfirst=True))
    s = s[s.index.notna()].dropna().sort_index()
    return s if len(s) >= 30 else None


def _load_delivery(symbols: set[str]) -> dict[str, pd.Series]:
    """delivery_qty / traded_qty per date, for this batch of symbols only.

    ONE streamed pass over the 3.5M-row file (chunked so peak RAM stays
    small), keeping only EQ/BE/BZ rows for the symbols asked for.
    """
    if not symbols:
        return {}
    frames: dict[str, list[tuple]] = {s: [] for s in symbols}
    try:
        with gzip.open(_DELIVERY_GZ, "rt", newline="") as f:
            for chunk in pd.read_csv(f, chunksize=400_000,
                                     usecols=["date", "symbol",
                                              "ttl_trd_qnty", "deliv_qty"]):
                hit = chunk[chunk["symbol"].isin(symbols)]
                for sym, g in hit.groupby("symbol", sort=False):
                    frames[sym].append(
                        (g["date"].to_numpy(), g["ttl_trd_qnty"].to_numpy(),
                         g["deliv_qty"].to_numpy()))
    except OSError as e:
        log.warning("external_panels: delivery file unreadable: %s", e)
        return {}
    out: dict[str, pd.Series] = {}
    for sym, parts in frames.items():
        if not parts:
            continue
        dates = np.concatenate([p[0] for p in parts])
        ttl = pd.to_numeric(np.concatenate([p[1] for p in parts]),
                            errors="coerce")
        dlv = pd.to_numeric(np.concatenate([p[2] for p in parts]),
                            errors="coerce")
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = np.where(ttl > 0, dlv / ttl, np.nan)
        s = pd.Series(ratio, index=pd.to_datetime(dates, errors="coerce"))
        s = s[s.index.notna()].dropna().sort_index()
        if len(s) >= 30:
            out[sym] = s
    return out


_fo_cache: Optional[dict[str, pd.DataFrame]] = None
_deals_cache: Optional[dict[str, pd.DataFrame]] = None
# Months where NSE returned its 70-row maximum: inside those months an absent
# symbol is NOT evidence of "no deal", so the deal triggers must report
# DATA_NEEDED rather than a quiet False. Filled by _load_deals().
_bulk_capped_months: set[str] = set()
_NSE_DEAL_ROW_CAP = 70


def _load_fo_oi() -> dict[str, pd.DataFrame]:
    """{symbol: DataFrame(fut_oi, ce_oi, pe_oi)} indexed by date."""
    global _fo_cache
    if _fo_cache is not None:
        return _fo_cache
    try:
        df = pd.read_csv(_FO_OI_CSV, parse_dates=["date"])
    except OSError:
        _fo_cache = {}
        return _fo_cache
    except Exception as e:                                  # pragma: no cover
        log.warning("external_panels: F&O OI unreadable: %s", e)
        _fo_cache = {}
        return _fo_cache
    out: dict[str, pd.DataFrame] = {}
    for sym, g in df.groupby("symbol", sort=False):
        g = g.sort_values("date")
        # One row per date: sum across expiries so the series is the symbol's
        # whole book, not an arbitrary contract.
        agg = g.groupby("date")[["fut_oi", "ce_oi", "pe_oi"]].sum()
        if len(agg) >= 10:
            out[str(sym)] = agg
    _fo_cache = out
    return out


def _load_deals() -> dict[str, pd.DataFrame]:
    """{symbol: DataFrame(date, side, qty)} from bulk + block deals."""
    global _deals_cache
    if _deals_cache is not None:
        return _deals_cache
    rows: list[tuple] = []
    for path, kind in ((_BULK_CSV, "bulk"), (_BLOCK_CSV, "block")):
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    rows.append((r.get("BD_DT_DATE", ""),
                                 (r.get("BD_SYMBOL") or "").strip().upper(),
                                 (r.get("BD_BUY_SELL") or "").strip().upper(),
                                 r.get("BD_QTY_TRD") or "", kind))
        except OSError:
            continue
    if not rows:
        _deals_cache = {}
        return _deals_cache
    df = pd.DataFrame(rows, columns=["date", "symbol", "side", "qty", "kind"])
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df = df[df["date"].notna() & df["symbol"].ne("")]
    bulk = df[df["kind"] == "bulk"]
    if len(bulk):
        per_month = bulk.groupby(bulk["date"].dt.strftime("%Y-%m")).size()
        _bulk_capped_months.clear()
        _bulk_capped_months.update(
            str(m) for m, n in per_month.items() if n >= _NSE_DEAL_ROW_CAP)
    _deals_cache = {str(s): g.sort_values("date")
                    for s, g in df.groupby("symbol", sort=False)}
    return _deals_cache


# --------------------------------------------------------------------------
# Per-stock view handed to the trigger functions
# --------------------------------------------------------------------------

@dataclass
class StockExternals:
    """Precomputed external readings for ONE stock, as of the panel's last bar.

    Every field is Optional. None = DATA_NEEDED for whatever trigger needs it.
    """
    ticker: str
    sector: Optional[str] = None
    peers: int = 0
    # As-of dates, per panel. The stored panels are NOT all current: the
    # committed OHLCV zip ends before today, so every RS/peer reading is
    # as-of `asof`, and the page prints it rather than implying "live".
    asof: str = ""
    index_asof: str = ""
    delivery_asof: str = ""
    fo_asof: str = ""
    deals_asof: str = ""

    # Relative strength vs the stock's own sector (equal-weight peer index)
    ret_5d: Optional[float] = None
    ret_10d: Optional[float] = None
    sector_ret_5d: Optional[float] = None
    sector_ret_10d: Optional[float] = None
    rs_line: Optional[np.ndarray] = None          # stock / sector, last 60

    # Cross-sectional ranks within the panel (0-100 percentile, higher better)
    rs_rank_now: Optional[float] = None
    rs_rank_prev: Optional[float] = None          # 10 sessions earlier
    own_sector_rank: Optional[float] = None       # percentile inside own sector
    sector_rank_now: Optional[float] = None
    sector_rank_prev: Optional[float] = None
    turnover_rank_now: Optional[float] = None
    turnover_rank_prev: Optional[float] = None

    # Peer breadth
    peer_pct_above_ma20: Optional[np.ndarray] = None   # last 15 sessions
    peer_break_20d_low: Optional[int] = None           # peers breaking today
    peer_prerise_flags: Optional[int] = None           # of the top-5 peers

    # Market index
    index_close: Optional[np.ndarray] = None           # last 60 sessions

    # Delivery, F&O, deals
    delivery_ratio: Optional[np.ndarray] = None        # last 120 sessions
    fut_oi: Optional[np.ndarray] = None                # last 30 sessions
    ce_oi: Optional[np.ndarray] = None
    pe_oi: Optional[np.ndarray] = None
    # Deal semantics, learned from the data itself:
    #   BLOCK deals report BOTH counterparties, so their buy and sell rows
    #   cancel exactly — presence is meaningful, direction is not.
    #   BULK deals are per-client disclosures (>0.5% of shares), so their NET
    #   quantity is a real direction. The owner's formula names the bulk flag,
    #   so the side flags below come from bulk rows only; block deals are
    #   carried separately as "a large negotiated trade happened".
    bulk_net_qty: Optional[float] = None
    bulk_rows: int = 0
    block_rows: int = 0
    deals_capped: bool = False        # window fell inside a 70-row-capped month
    deal_buy_recent: Optional[bool] = None
    deal_sell_recent: Optional[bool] = None

    def missing(self) -> list[str]:
        """Which panels came back empty for this stock (shown on the page)."""
        out = []
        if self.sector is None or self.peers < 2:
            out.append("sector/peer panel")
        if self.index_close is None:
            out.append("index close")
        if self.delivery_ratio is None:
            out.append("delivery")
        if self.fut_oi is None:
            out.append("F&O OI")
        if self.deal_buy_recent is None:
            out.append("bulk deals (NSE 70-row/month cap — absence proves "
                       "nothing)" if self.deals_capped else "bulk/block deals")
        return out


@dataclass
class ExternalData:
    """One batch load, shared by every stock in a trigger-layer run."""
    per_ticker: dict[str, StockExternals] = field(default_factory=dict)
    sources: dict[str, bool] = field(default_factory=dict)
    asof: dict[str, str] = field(default_factory=dict)   # panel -> last date

    def for_ticker(self, ticker: str) -> Optional[StockExternals]:
        return self.per_ticker.get(ticker)


def _base_symbol(ticker: str) -> str:
    """'TCS.NS' → 'TCS' (delivery/F&O/deals files key on the bare symbol)."""
    return ticker.split(".")[0].upper()


def _pct_rank(value: float, population: list[float],
              min_n: int = 20) -> Optional[float]:
    """Percentile of `value` within `population` (0-100, higher = stronger).

    `min_n` guards against ranking inside a population too small to mean
    anything; the sector-vs-sector ranks pass a lower floor because there are
    only 19 sectors in the map."""
    pop = [p for p in population if p is not None and np.isfinite(p)]
    if len(pop) < min_n or value is None or not np.isfinite(value):
        return None
    return round(100.0 * sum(1 for p in pop if p <= value) / len(pop), 1)


def _ret(arr: np.ndarray, days: int) -> Optional[float]:
    if arr is None or len(arr) < days + 1:
        return None
    a, b = float(arr[-1]), float(arr[-1 - days])
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return None
    return a / b - 1.0


def prepare(tickers: list[str]) -> ExternalData:
    """Load every external panel once for this batch of tickers.

    Called at the start of a trigger-layer run (scheduler) or for a single
    stock (the /triggers page). Absent files simply leave fields None.
    """
    tickers = [t for t in dict.fromkeys(tickers) if t]
    panel = _load_panel()
    smap = _sector_map()
    index_s = _load_index_close()
    fo = _load_fo_oi()
    deals = _load_deals()
    delivery = _load_delivery({_base_symbol(t) for t in tickers})

    ed = ExternalData(sources={
        "panel": bool(panel), "sector_map": bool(smap),
        "index": index_s is not None, "delivery": bool(delivery),
        "fo_oi": bool(fo), "deals": bool(deals),
    })

    # --- sector aggregates from the panel (equal-weight peer index) --------
    by_sector: dict[str, list[str]] = {}
    for t in panel:
        sec = smap.get(t)
        if sec:
            by_sector.setdefault(sec, []).append(t)

    sector_close: dict[str, pd.Series] = {}
    sector_breadth: dict[str, pd.Series] = {}
    for sec, members in by_sector.items():
        closes = [panel[m]["close"] for m in members if len(panel[m]) >=
                  _MIN_PANEL_BARS]
        if len(closes) < 2:
            continue
        wide = pd.concat(closes, axis=1).sort_index().dropna(how="all")
        norm = wide.divide(wide.iloc[0], axis=1)          # equal-weight index
        sector_close[sec] = norm.mean(axis=1, skipna=True).dropna()
        ma20 = wide.rolling(20).mean()
        above = (wide > ma20)
        valid = ma20.notna()
        with np.errstate(invalid="ignore"):
            sector_breadth[sec] = (above.where(valid).sum(axis=1) /
                                   valid.sum(axis=1).replace(0, np.nan))

    # --- cross-sectional rank populations ---------------------------------
    rs_now, rs_prev, tv_now, tv_prev = {}, {}, {}, {}
    for t, df in panel.items():
        c = df["close"].to_numpy(dtype=float)
        rs_now[t] = _ret(c, 60)
        rs_prev[t] = _ret(c[:-10], 60) if len(c) > 70 else None
        if "volume" in df.columns:
            v = df["volume"].to_numpy(dtype=float)
            turn = c * v
            tv_now[t] = (float(np.nanmean(turn[-20:]))
                         if len(turn) >= 20 else None)
            tv_prev[t] = (float(np.nanmean(turn[-30:-10]))
                          if len(turn) >= 30 else None)
    sec_ret_now = {s: _ret(v.to_numpy(dtype=float), 10)
                   for s, v in sector_close.items()}
    sec_ret_prev = {s: _ret(v.to_numpy(dtype=float)[:-10], 10)
                    for s, v in sector_close.items() if len(v) > 20}

    index_arr = (index_s.to_numpy(dtype=float)[-60:]
                 if index_s is not None else None)
    index_asof = (str(index_s.index[-1].date()) if index_s is not None else "")
    panel_last = max((df.index[-1] for df in panel.values() if len(df)),
                     default=None)
    deals_last = max((g["date"].max() for g in deals.values()), default=None)
    ed.asof = {
        "panel": str(panel_last.date()) if panel_last is not None else "",
        "index": index_asof,
        "deals": str(deals_last.date()) if deals_last is not None else "",
    }

    for t in tickers:
        sx = StockExternals(ticker=t)
        sym = _base_symbol(t)
        sec = smap.get(t)
        own = panel.get(t)

        if sec and own is not None and len(own) >= _MIN_PANEL_BARS:
            members = [m for m in by_sector.get(sec, []) if m != t]
            sx.sector, sx.peers = sec, len(members)
            c = own["close"].to_numpy(dtype=float)
            sx.ret_5d, sx.ret_10d = _ret(c, 5), _ret(c, 10)
            sc = sector_close.get(sec)
            if sc is not None:
                sa = sc.to_numpy(dtype=float)
                sx.sector_ret_5d, sx.sector_ret_10d = _ret(sa, 5), _ret(sa, 10)
                joined = pd.concat(
                    [own["close"].rename("s"), sc.rename("x")],
                    axis=1, join="inner").dropna()
                if len(joined) >= 30:
                    line = (joined["s"] / joined["x"]).to_numpy(dtype=float)
                    sx.rs_line = line[-60:]
            br = sector_breadth.get(sec)
            if br is not None and br.notna().sum() >= 15:
                sx.peer_pct_above_ma20 = (br.dropna().to_numpy(dtype=float)
                                          * 100.0)[-15:]
            broke = 0
            for m in members:
                mc = panel[m]
                if len(mc) < 25:
                    continue
                low = mc["close"].to_numpy(dtype=float)
                if float(low[-1]) <= float(np.nanmin(low[-21:-1])):
                    broke += 1
            sx.peer_break_20d_low = broke
            # "3 of top 5 peers have pre-rise flags": top 5 by 60d return,
            # flag = 10d return positive while inside its own 20d range top.
            top5 = sorted(members, key=lambda m: (rs_now.get(m) if
                          rs_now.get(m) is not None else -9e9), reverse=True)[:5]
            flags = 0
            for m in top5:
                mc = panel[m]["close"].to_numpy(dtype=float)
                r10 = _ret(mc, 10)
                if r10 is not None and r10 > 0 and len(mc) >= 21:
                    hi, lo = float(np.nanmax(mc[-21:])), float(np.nanmin(mc[-21:]))
                    if hi > lo and (mc[-1] - lo) / (hi - lo) >= 0.5:
                        flags += 1
            sx.peer_prerise_flags = flags if top5 else None
            sx.rs_rank_now = _pct_rank(rs_now.get(t), list(rs_now.values()))
            # Rank inside the stock's OWN industry (their "industry_relative_
            # rank"): percentile of 60d return among sector members. Small
            # sectors have few members, so _pct_rank's 20-name floor is
            # relaxed here — a 6-name sector still ranks honestly.
            sec_pop = [rs_now.get(m) for m in by_sector.get(sec, [])
                       if rs_now.get(m) is not None]
            mine = rs_now.get(t)
            if mine is not None and len(sec_pop) >= 3:
                sx.own_sector_rank = round(
                    100.0 * sum(1 for p in sec_pop if p <= mine) /
                    len(sec_pop), 1)
            sx.rs_rank_prev = _pct_rank(rs_prev.get(t), list(rs_prev.values()))
            sx.sector_rank_now = _pct_rank(sec_ret_now.get(sec),
                                           list(sec_ret_now.values()), min_n=8)
            sx.sector_rank_prev = _pct_rank(sec_ret_prev.get(sec),
                                            list(sec_ret_prev.values()),
                                            min_n=8)
            sx.turnover_rank_now = _pct_rank(tv_now.get(t),
                                             list(tv_now.values()))
            sx.turnover_rank_prev = _pct_rank(tv_prev.get(t),
                                              list(tv_prev.values()))

        sx.index_close = index_arr
        sx.index_asof = index_asof
        if own is not None and len(own):
            sx.asof = str(own.index[-1].date())

        dv = delivery.get(sym)
        if dv is not None and len(dv) >= 30:
            sx.delivery_ratio = dv.to_numpy(dtype=float)[-120:]
            sx.delivery_asof = str(dv.index[-1].date())

        oi = fo.get(sym)
        if oi is not None and len(oi) >= 10:
            sx.fut_oi = oi["fut_oi"].to_numpy(dtype=float)[-30:]
            sx.ce_oi = oi["ce_oi"].to_numpy(dtype=float)[-30:]
            sx.pe_oi = oi["pe_oi"].to_numpy(dtype=float)[-30:]
            sx.fo_asof = str(oi.index[-1].date())

        if deals:
            g = deals.get(sym)
            win = int(ASSUMED_EXTERNAL_CONSTANTS["deal_window_days"])
            # Anchor on THIS stock's as-of bar (calendar days ~ 2x sessions).
            # Anchoring on the symbol's own last deal would mark a name with
            # any deal in its history as "recent deal" forever.
            anchor = (own.index[-1] if own is not None and len(own)
                      else (deals_last if deals_last is not None else None))
            if g is None or anchor is None:
                sx.deal_buy_recent = sx.deal_sell_recent = (
                    False if anchor is not None else None)
            else:
                window = g[(g["date"] <= anchor) &
                           (g["date"] >= anchor - pd.Timedelta(days=win * 2))]
                bulk = window[window["kind"] == "bulk"]
                bq = pd.to_numeric(bulk["qty"], errors="coerce").fillna(0.0)
                buys = float(bq[bulk["side"] == "BUY"].sum())
                sells = float(bq[bulk["side"] == "SELL"].sum())
                sx.bulk_net_qty = buys - sells
                sx.bulk_rows = int(len(bulk))
                sx.block_rows = int((window["kind"] == "block").sum())
                if sx.bulk_rows:
                    sx.deal_buy_recent = bool(sx.bulk_net_qty > 0)
                    sx.deal_sell_recent = bool(sx.bulk_net_qty < 0)
                else:
                    # No bulk row for this name in the window. Only call that
                    # "quiet" if the months we looked at were COMPLETE; if NSE
                    # capped them at 70 rows, absence proves nothing and the
                    # trigger stays DATA_NEEDED.
                    months = {d.strftime("%Y-%m") for d in pd.date_range(
                        anchor - pd.Timedelta(days=win * 2), anchor, freq="D")}
                    capped = bool(months & _bulk_capped_months)
                    sx.deal_buy_recent = None if capped else False
                    sx.deal_sell_recent = None if capped else False
                    sx.deals_capped = capped
                sx.deals_asof = str(anchor.date())

        ed.per_ticker[t] = sx
    return ed


# --------------------------------------------------------------------------
# Register-side panels (holdings, insider) — per-stock, cheap, on demand
# --------------------------------------------------------------------------

_SHAREHOLDING_DIR = _DATA / "shareholding"


def shareholding_quarters(ticker: str) -> Optional[list[dict]]:
    """Promoter % and public % by quarter, newest first. None = not fetched.

    Source: the NSE shareholding-pattern JSONs (Colab v2 intake). These carry
    ONLY the promoter/public split — the FII/DII/mutual-fund breakdown and
    pledge % live in the XBRL file each record links to, which this app cannot
    fetch (nsearchives blocks the cloud IP). So H-01 computes; H-02..H-06 and
    X-07 stay DATA_NEEDED with a known free path (Colab).
    """
    import json
    path = _SHAREHOLDING_DIR / f"{_base_symbol(ticker)}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, list):
        return None
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        pr = pd.to_numeric(r.get("pr_and_prgrp"), errors="coerce")
        pub = pd.to_numeric(r.get("public_val"), errors="coerce")
        if not np.isfinite(pr):
            continue
        out.append({
            "date": str(r.get("date") or ""),
            "promoter_pct": round(float(pr), 2),
            "public_pct": (round(float(pub), 2) if np.isfinite(pub) else None),
            "submitted": str(r.get("submissionDate") or ""),
        })
    return out or None


_insider_cache: Optional[dict[str, pd.DataFrame]] = None
_INSIDER_CSV = _DATA / "insider_pit.csv"


def insider_events(ticker: str, days: int = 180) -> Optional[dict]:
    """SEBI PIT insider buy/sell in the last `days`. None = file absent.

    Counts promoter/insider disclosures, not market trades. Dates come from
    the disclosure timestamp ('date'); a handful of source rows carry
    impossible transaction dates (NSE filing typos) and are ignored here.
    """
    global _insider_cache
    if _insider_cache is None:
        try:
            df = pd.read_csv(_INSIDER_CSV, low_memory=False,
                             usecols=["symbol", "date", "buyQuantity",
                                      "sellquantity", "personCategory",
                                      "acqMode"])
            df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y %H:%M",
                                        errors="coerce")
            df = df[df["date"].notna()]
            df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
            _insider_cache = {str(s): g.sort_values("date")
                              for s, g in df.groupby("symbol", sort=False)}
        except (OSError, ValueError) as e:
            log.warning("external_panels: insider file unreadable: %s", e)
            _insider_cache = {}
    if not _insider_cache:
        return None
    g = _insider_cache.get(_base_symbol(ticker))
    if g is None:
        return {"buys": 0, "sells": 0, "net_qty": 0.0, "last": "",
                "window_days": days}
    cutoff = g["date"].max() - pd.Timedelta(days=days)
    r = g[g["date"] >= cutoff]
    buy = pd.to_numeric(r["buyQuantity"], errors="coerce").fillna(0.0)
    sell = pd.to_numeric(r["sellquantity"], errors="coerce").fillna(0.0)
    return {
        "buys": int((buy > 0).sum()),
        "sells": int((sell > 0).sum()),
        "net_qty": float(buy.sum() - sell.sum()),
        "last": str(r["date"].max().date()) if len(r) else "",
        "window_days": days,
    }


def deal_summary(ticker: str, days: int = 20) -> Optional[dict]:
    """Bulk/block deal rows for one symbol in the last `days` (register H-20).

    None = the deal files are absent. Note the caller must treat a zero-row
    result as PARTIAL, not "no deal": every bulk month in the committed file
    hit NSE's 70-row cap. Block-deal rows report both counterparties, so only
    the bulk rows carry a direction.
    """
    deals = _load_deals()
    if not deals:
        return None
    sym = _base_symbol(ticker)
    g = deals.get(sym)
    anchor = max((d["date"].max() for d in deals.values()), default=None)
    if anchor is None:
        return None
    if g is None:
        return {"bulk_rows": 0, "block_rows": 0, "bulk_net_qty": 0.0,
                "window_days": days, "asof": str(anchor.date())}
    w = g[(g["date"] <= anchor) &
          (g["date"] >= anchor - pd.Timedelta(days=days))]
    bulk = w[w["kind"] == "bulk"]
    bq = pd.to_numeric(bulk["qty"], errors="coerce").fillna(0.0)
    return {
        "bulk_rows": int(len(bulk)),
        "block_rows": int((w["kind"] == "block").sum()),
        "bulk_net_qty": float(bq[bulk["side"] == "BUY"].sum() -
                              bq[bulk["side"] == "SELL"].sum()),
        "window_days": days,
        "asof": str(anchor.date()),
    }


def pcr_oi(ticker: str) -> Optional[dict]:
    """Put/call ratio on OPEN INTEREST for one symbol (register W-022).

    The register asked for volume-based PCR; the committed panel carries OI,
    so this is an OI-basis PCR and is labelled as such wherever shown.
    """
    oi = _load_fo_oi().get(_base_symbol(ticker))
    if oi is None or len(oi) < 10:
        return None
    ce = float(oi["ce_oi"].iloc[-1])
    pe = float(oi["pe_oi"].iloc[-1])
    if ce <= 0:
        return None
    hist = (oi["pe_oi"] / oi["ce_oi"].replace(0, np.nan)).dropna()
    return {
        "pcr_oi": round(pe / ce, 3),
        "basis": "open interest (not volume — volume PCR needs the chain)",
        "pctile_1y": (round(100.0 * float((hist <= pe / ce).mean()), 1)
                      if len(hist) >= 60 else None),
        "asof": str(oi.index[-1].date()),
    }


def reset_caches() -> None:
    """Drop cached panels (tests, and after a weekly data top-up)."""
    global _panel_cache, _insider_cache, _fo_cache, _deals_cache
    _panel_cache = _insider_cache = _fo_cache = _deals_cache = None
