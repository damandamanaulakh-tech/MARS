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

# Cached per history depth: production keeps a short tail (RAM on the 512MB
# box), the calibration needs the full 12 years.
_panel_cache: dict[int, dict[str, pd.DataFrame]] = {}


def _sector_map() -> dict[str, str]:
    try:
        from .universes import NIFTY_200_SECTOR_MAP_EXTRAS
        return dict(NIFTY_200_SECTOR_MAP_EXTRAS)
    except Exception as e:                                  # pragma: no cover
        log.warning("external_panels: sector map unavailable: %s", e)
        return {}


def _load_panel(tail: int = _PANEL_TAIL) -> dict[str, pd.DataFrame]:
    """{ticker: DataFrame(close, volume)} for the committed 191-name panel.

    `tail` bounds bars per name. The default keeps the live layer inside the
    512MB box; pass 0 for the full history (the calibration does).
    """
    if tail in _panel_cache:
        return _panel_cache[tail]
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
                keep = keep.sort_index()
                out[ticker] = keep if tail <= 0 else keep.tail(tail)
    except OSError as e:
        log.warning("external_panels: panel zip unreadable: %s", e)
    _panel_cache[tail] = out
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


class PanelSet:
    """Every external panel as a FULL-HISTORY series, plus an as-of reader.

    Why full history: the trigger layer needs today's reading, but the
    calibration (BT01-BT08 for the external side) needs the reading as of any
    past bar. Both go through `externals_at()`, so the calibration can never
    measure something different from what the live layer computes — the same
    rule that kept the OHLCV calibration honest by calling the production
    trigger functions directly.

    All cross-sectional work is vectorised once here (191 columns x ~3k rows)
    rather than per stock per date.
    """

    def __init__(self, tickers: Optional[list[str]] = None,
                 tail: int = _PANEL_TAIL):
        self.panel = _load_panel(tail)
        self.smap = _sector_map()
        self.index_s = _load_index_close()
        self.fo = _load_fo_oi()
        self.deals = _load_deals()
        syms = {_base_symbol(t) for t in (tickers or list(self.panel))}
        self.delivery = _load_delivery(syms)

        names = sorted(self.panel)
        self.close = pd.DataFrame(
            {t: self.panel[t]["close"] for t in names}).sort_index()
        vol_cols = {t: self.panel[t]["volume"] for t in names
                    if "volume" in self.panel[t].columns}
        self.vol = (pd.DataFrame(vol_cols).reindex(self.close.index)
                    if vol_cols else None)

        c = self.close
        self.ret60 = c / c.shift(60) - 1.0
        self.ret10 = c / c.shift(10) - 1.0
        # Cross-sectional percentile ranks (0-100, higher = stronger). A row
        # with too few live names is left NaN, mirroring _pct_rank's min_n.
        live = c.notna().sum(axis=1)
        self.rs_rank = (self.ret60.rank(axis=1, pct=True) * 100.0)
        self.rs_rank = self.rs_rank.where(live >= 20)
        if self.vol is not None:
            turn = (c * self.vol).rolling(20).mean()
            self.turnover_rank = (turn.rank(axis=1, pct=True) * 100.0
                                  ).where(live >= 20)
        else:
            self.turnover_rank = None

        # Sector aggregates: equal-weight index, breadth, and 10d return rank.
        self.by_sector: dict[str, list[str]] = {}
        for t in names:
            sec = self.smap.get(t)
            if sec:
                self.by_sector.setdefault(sec, []).append(t)
        self.by_sector = {k: v for k, v in self.by_sector.items() if len(v) >= 2}

        self.sector_close: dict[str, pd.Series] = {}
        self.sector_breadth: dict[str, pd.Series] = {}
        ma20 = c.rolling(20).mean()
        above = c > ma20
        valid = ma20.notna()
        prior_low20 = c.shift(1).rolling(20).min()
        self.broke_low = c <= prior_low20
        pos20_rng = c.rolling(21).max() - c.rolling(21).min()
        self.prerise_flag = ((self.ret10 > 0) & (pos20_rng > 0) &
                             (((c - c.rolling(21).min()) /
                               pos20_rng.replace(0, np.nan)) >= 0.5))
        sec_ret10 = {}
        for sec, members in self.by_sector.items():
            wide = c[members]
            first = wide.apply(lambda col: col.dropna().iloc[0]
                               if col.notna().any() else np.nan)
            norm = wide.divide(first, axis=1)
            idx = norm.mean(axis=1, skipna=True)
            self.sector_close[sec] = idx
            vc = valid[members].sum(axis=1)
            self.sector_breadth[sec] = ((above[members].where(valid[members])
                                         .sum(axis=1) /
                                         vc.replace(0, np.nan)) * 100.0)
            sec_ret10[sec] = idx / idx.shift(10) - 1.0
        self.sector_ret10 = pd.DataFrame(sec_ret10)
        # RS line per ticker = close / its sector index, on the panel calendar.
        # Precomputed because externals_at() is called ~100k times by the
        # calibration and a per-call concat/join dominated the run.
        self.rs_lines: dict[str, pd.Series] = {}
        for sec, members in self.by_sector.items():
            rs = c[members].div(self.sector_close[sec], axis=0)
            for m in members:
                self.rs_lines[m] = rs[m]
        # >=8 sectors is the same floor prepare() used for sector-vs-sector.
        self.sector_rank = (self.sector_ret10.rank(axis=1, pct=True) * 100.0
                            ).where(self.sector_ret10.notna().sum(axis=1) >= 8)

        # Within-industry rank, peer breakdown counts, peer pre-rise flags —
        # each computed per sector then read back per ticker, with the stock
        # itself removed from its own peer group.
        self.own_sector_rank = pd.DataFrame(index=c.index, columns=names,
                                            dtype=float)
        self._sector_break_count: dict[str, pd.Series] = {}
        self._sector_flag_top5: dict[str, pd.Series] = {}
        self._sector_flag_rank6: dict[str, pd.Series] = {}
        self._sector_rank_desc: dict[str, pd.DataFrame] = {}
        for sec, members in self.by_sector.items():
            r = self.ret60[members]
            self.own_sector_rank[members] = (r.rank(axis=1, pct=True) * 100.0
                                             ).where(r.notna().sum(axis=1) >= 3)
            self._sector_break_count[sec] = self.broke_low[members].sum(axis=1)
            rank_desc = r.rank(axis=1, ascending=False, method="first")
            self._sector_rank_desc[sec] = rank_desc
            f = self.prerise_flag[members]
            self._sector_flag_top5[sec] = (f & (rank_desc <= 5)).sum(axis=1)
            self._sector_flag_rank6[sec] = (f & (rank_desc == 6)).sum(axis=1)

        self.sources = {
            "panel": bool(self.panel), "sector_map": bool(self.smap),
            "index": self.index_s is not None, "delivery": bool(self.delivery),
            "fo_oi": bool(self.fo), "deals": bool(self.deals),
        }
        panel_last = self.close.index[-1] if len(self.close) else None
        deals_last = max((g["date"].max() for g in self.deals.values()),
                         default=None)
        self.asof = {
            "panel": str(panel_last.date()) if panel_last is not None else "",
            "index": (str(self.index_s.index[-1].date())
                      if self.index_s is not None else ""),
            "deals": str(deals_last.date()) if deals_last is not None else "",
        }
        self._deals_last = deals_last

    # -- as-of reader ------------------------------------------------------

    def externals_at(self, ticker: str,
                     when: Optional[pd.Timestamp] = None) -> StockExternals:
        """Externals for `ticker` as of `when` (default: the panel's last bar).

        Every field stays None when its panel is absent or too short at that
        date, which keeps the trigger at DATA_NEEDED instead of inventing a
        quiet False.
        """
        sx = StockExternals(ticker=ticker)
        own = self.panel.get(ticker)
        if own is None or not len(own):
            sx.index_asof = self.asof.get("index", "")
            return self._attach_symbol_panels(sx, ticker, when)
        dates = own.index
        if when is None:
            pos = len(dates) - 1
        else:
            pos = int(dates.searchsorted(when, side="right")) - 1
            if pos < 0:
                return sx
        asof = dates[pos]
        sx.asof = str(asof.date())
        c = own["close"].to_numpy(dtype=float)[:pos + 1]

        sec = self.smap.get(ticker)
        if sec and sec in self.by_sector and pos >= _MIN_PANEL_BARS:
            members = [m for m in self.by_sector[sec] if m != ticker]
            sx.sector, sx.peers = sec, len(members)
            sx.ret_5d, sx.ret_10d = _ret(c, 5), _ret(c, 10)
            sidx = self.sector_close[sec]
            sa = sidx.to_numpy(dtype=float)[:sidx.index.searchsorted(
                asof, side="right")]
            sx.sector_ret_5d, sx.sector_ret_10d = _ret(sa, 5), _ret(sa, 10)
            rsl = self.rs_lines.get(ticker)
            if rsl is not None:
                seg = rsl.to_numpy(dtype=float)[
                    :self.close.index.searchsorted(asof, side="right")]
                seg = seg[np.isfinite(seg)]
                if len(seg) >= 30:
                    sx.rs_line = seg[-60:]
            br = self.sector_breadth[sec].iloc[:
                self.sector_breadth[sec].index.searchsorted(asof, side="right")]
            br = br.dropna()
            if len(br) >= 15:
                sx.peer_pct_above_ma20 = br.to_numpy(dtype=float)[-15:]
            sx.rs_rank_now = self._rank_at(self.rs_rank, ticker, asof, 0)
            sx.rs_rank_prev = self._rank_at(self.rs_rank, ticker, asof, 10)
            sx.own_sector_rank = self._rank_at(self.own_sector_rank, ticker,
                                               asof, 0)
            sx.sector_rank_now = self._rank_at(self.sector_rank, sec, asof, 0)
            sx.sector_rank_prev = self._rank_at(self.sector_rank, sec, asof, 10)
            if self.turnover_rank is not None:
                sx.turnover_rank_now = self._rank_at(self.turnover_rank,
                                                     ticker, asof, 0)
                sx.turnover_rank_prev = self._rank_at(self.turnover_rank,
                                                      ticker, asof, 10)
            ipos = self.close.index.searchsorted(asof, side="right") - 1
            if ipos >= 0:
                own_broke = bool(self.broke_low[ticker].iloc[ipos]) \
                    if ticker in self.broke_low.columns else False
                total = float(self._sector_break_count[sec].iloc[ipos])
                sx.peer_break_20d_low = int(max(0.0, total - (1 if own_broke
                                                              else 0)))
                # top-5 peers, self removed: drop self's own flag when it sits
                # inside the top 5 and promote the 6th-ranked member instead.
                top5 = float(self._sector_flag_top5[sec].iloc[ipos])
                rank_desc = self._sector_rank_desc[sec]
                own_rank = (float(rank_desc[ticker].iloc[ipos])
                            if ticker in rank_desc.columns else np.nan)
                if np.isfinite(own_rank):
                    own_flag = (bool(self.prerise_flag[ticker].iloc[ipos])
                                if ticker in self.prerise_flag.columns
                                else False)
                    if own_rank <= 5:
                        top5 -= 1.0 if own_flag else 0.0
                        top5 += float(self._sector_flag_rank6[sec].iloc[ipos])
                    sx.peer_prerise_flags = int(max(0.0, top5))

        if self.index_s is not None:
            ic = self.index_s.iloc[:self.index_s.index.searchsorted(
                asof, side="right")]
            if len(ic) >= 20:
                sx.index_close = ic.to_numpy(dtype=float)[-60:]
                sx.index_asof = str(ic.index[-1].date())

        return self._attach_symbol_panels(sx, ticker, asof)

    def _rank_at(self, frame: Optional[pd.DataFrame], col: str,
                 asof: pd.Timestamp, back: int) -> Optional[float]:
        if frame is None or col not in frame.columns:
            return None
        pos = int(frame.index.searchsorted(asof, side="right")) - 1 - back
        if pos < 0:
            return None
        v = frame[col].iloc[pos]
        return None if not np.isfinite(v) else round(float(v), 1)

    def _attach_symbol_panels(self, sx: StockExternals, ticker: str,
                              asof: Optional[pd.Timestamp]) -> StockExternals:
        """Delivery, F&O OI and deals — keyed on the bare symbol."""
        sym = _base_symbol(ticker)
        dv = self.delivery.get(sym)
        if dv is not None:
            seg = (dv if asof is None else
                   dv.iloc[:dv.index.searchsorted(asof, side="right")])
            if len(seg) >= 30:
                sx.delivery_ratio = seg.to_numpy(dtype=float)[-120:]
                sx.delivery_asof = str(seg.index[-1].date())

        oi = self.fo.get(sym)
        if oi is not None:
            seg = (oi if asof is None else
                   oi.iloc[:oi.index.searchsorted(asof, side="right")])
            if len(seg) >= 10:
                sx.fut_oi = seg["fut_oi"].to_numpy(dtype=float)[-30:]
                sx.ce_oi = seg["ce_oi"].to_numpy(dtype=float)[-30:]
                sx.pe_oi = seg["pe_oi"].to_numpy(dtype=float)[-30:]
                sx.fo_asof = str(seg.index[-1].date())

        if self.deals:
            anchor = asof if asof is not None else self._deals_last
            g = self.deals.get(sym)
            win = int(ASSUMED_EXTERNAL_CONSTANTS["deal_window_days"])
            if anchor is None:
                return sx
            if g is None:
                window = None
            else:
                window = g[(g["date"] <= anchor) &
                           (g["date"] >= anchor - pd.Timedelta(days=win * 2))]
            if window is not None and len(window):
                bulk = window[window["kind"] == "bulk"]
                bq = pd.to_numeric(bulk["qty"], errors="coerce").fillna(0.0)
                sx.bulk_rows = int(len(bulk))
                sx.block_rows = int((window["kind"] == "block").sum())
                sx.bulk_net_qty = float(bq[bulk["side"] == "BUY"].sum() -
                                        bq[bulk["side"] == "SELL"].sum())
                sx.deals_asof = str(pd.Timestamp(anchor).date())
            if not sx.bulk_rows:
                # No bulk row for this name in the window. Only "quiet" if the
                # months we looked at were COMPLETE; NSE capped months at 70
                # rows, and inside those absence proves nothing.
                months = {d.strftime("%Y-%m") for d in pd.date_range(
                    pd.Timestamp(anchor) - pd.Timedelta(days=win * 2),
                    pd.Timestamp(anchor), freq="D")}
                capped = bool(months & _bulk_capped_months)
                sx.deals_capped = capped
                sx.deal_buy_recent = None if capped else False
                sx.deal_sell_recent = None if capped else False
            else:
                sx.deal_buy_recent = bool(sx.bulk_net_qty > 0)
                sx.deal_sell_recent = bool(sx.bulk_net_qty < 0)
        return sx


_panelset_cache: Optional[PanelSet] = None


def panel_set(tickers: Optional[list[str]] = None, rebuild: bool = False,
              tail: int = _PANEL_TAIL) -> PanelSet:
    """The shared PanelSet (built once per process unless rebuild=True)."""
    global _panelset_cache
    if _panelset_cache is None or rebuild:
        _panelset_cache = PanelSet(tickers, tail=tail)
    return _panelset_cache


def prepare(tickers: list[str]) -> ExternalData:
    """Load every external panel once for this batch of tickers.

    Called at the start of a trigger-layer run (scheduler) or for a single
    stock (the /triggers page). Absent files simply leave fields None.
    """
    tickers = [t for t in dict.fromkeys(tickers) if t]
    ps = PanelSet(tickers)
    ed = ExternalData(sources=dict(ps.sources), asof=dict(ps.asof))
    for t in tickers:
        ed.per_ticker[t] = ps.externals_at(t)
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
    global _insider_cache, _fo_cache, _deals_cache, _panelset_cache
    _panel_cache.clear()
    _insider_cache = _fo_cache = _deals_cache = _panelset_cache = None
