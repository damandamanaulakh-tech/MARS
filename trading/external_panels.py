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

Boundary (test-enforced): trading/engine.py, risk.py and orders.py must never
import this module directly.

SCOPE CHANGED 2026-08-13 — this is no longer display-only. Owner order "wire
0095 and 0094 both" put two panel-fed tripwires into the danger layer, so
`trading/danger.py` (the SELL side) imports this module and the engine reaches
it only through there. That is the owner's own F-006 rule: the risk side may
act on a calibrated signal, the entry side may not. No entry gate, sizing rule
or new-position decision reads anything here.
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
    "event_window_days": 10,           # T12W_0098 "within next/last 10D"
    "atr_expansion_mult": 1.3,         # T12W_0098 "volatility expansion"
    "analyst_upgrade_min_net": 1.0,    # T12W_0105 "upgrade_flag" (net revisions)
    "rs_lead_rank_min": 60.0,          # T12W_0105 "RS lead signal"
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
    # --- news-fed triggers (T12W_0097/0099/0103/0104/0106/0107/0108/0109/0110)
    # The owner's sheet names the flags but never the numbers behind them.
    # `news_event_bar` MIRRORS news_feed.TRIGGER_SCORE and a test pins the two
    # together — triggers.py must not import the news module, so the value is
    # duplicated here rather than allowed to drift.
    "news_event_bar": 2.0,             # "positive_news_score > threshold"
    "news_flat_return_3d": 0.03,       # THEIR number: "abs(return3) < 3%"
    "news_cluster_min": 2,             # "accumulation flags active" / "cluster"
    "news_sentiment_high": 0.5,        # "sentiment_score high" (-1..+1 scale)
    "news_weak_clv": 0.4,              # "negative CLV" at a new high
    # T12W_0102's deterioration carries NO numeric threshold — the owner
    # overruled the two ASSUMED numbers that briefly lived here (2026-08-24:
    # "there is no number, its just a pattern ... span of candle is 15
    # days"). The pattern read lives in options_chain.deterioration_pattern.
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
    if len(s) < 30:
        return None
    # Node 3 (2026-08-24): append the weekly stored tail so the T12W_0095
    # index overlay — a live danger tripwire — stops reading an index state
    # frozen at the CSV's commit date. No store / failure = the committed
    # series exactly as before.
    try:
        from trading import data_refresh as _dr
        s = _dr.extend_close_series(s, close_col)
    except Exception as e:                                    # noqa: BLE001
        log.warning("external_panels: index tail extend failed: %s", e)
    return s


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

    # Reference feeds fetched 2026-08-13 (earnings calendar, analyst revisions).
    # FORWARD SNAPSHOT: the earnings date is "next known at fetch time", so the
    # trigger that reads it informs today but can never be backtested — there
    # is no archive of past next-earnings-date values.
    earnings_days_away: Optional[int] = None
    earnings_is_estimate: Optional[bool] = None
    analyst_net_revisions: Optional[float] = None
    analyst_score: Optional[float] = None

    # Today's news reading for this stock, handed in by the caller as a plain
    # dict (this module never imports news_feed — the news verdict is data
    # here, not a dependency). Populated ONLY for a reading that actually
    # happened: a failed fetch leaves every field None, because "we could not
    # look" must stay DATA_NEEDED and never become a quiet False.
    #
    # TIMING, and it is a real one: the news is TODAY's, while every price
    # field above is as-of the stored panel's last bar. The nine triggers that
    # combine the two are therefore "today's news against the last stored
    # bar", which the page states rather than implying they are simultaneous.
    news_verdict: Optional[str] = None          # TRIGGER | PASS | NO_NEWS
    news_tags: Optional[tuple[str, ...]] = None
    news_risk: Optional[float] = None
    news_opp: Optional[float] = None
    news_sentiment: Optional[float] = None      # lexicon mean, -1..+1
    news_count: Optional[int] = None
    news_asof: str = ""

    # Node 8 (2026-08-23): today's option-chain snapshot, handed in by the
    # caller as plain dicts (this module never imports options_chain — the
    # chain is data here). Populated only when the snapshot is FRESH
    # (options_chain.MAX_AGE_DAYS); a login-less day leaves every field
    # None = DATA_NEEDED, never a stale chain wearing today's confidence.
    pcr_oi: Optional[float] = None              # put OI / call OI, nearest expiry
    iv_skew: Optional[float] = None             # ATM put IV − call IV, IV pts
    # The owner's 15-day pattern (2026-08-24: "no number, its just a pattern
    # ... span of candle is 15 days"): True = deterioration growing across
    # the stock's own recorded span, False = recorded and not growing,
    # None = fewer than MIN_PATTERN_DAYS recordings yet.
    chain_deterioration: Optional[bool] = None
    chain_days: int = 0                         # recordings held for this stock
    chain_asof: str = ""

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
        if self.earnings_days_away is None:
            out.append("earnings calendar")
        if self.analyst_net_revisions is None:
            out.append("analyst revisions")
        if self.deal_buy_recent is None:
            out.append("bulk deals (NSE 70-row/month cap — absence proves "
                       "nothing)" if self.deals_capped else "bulk/block deals")
        if self.news_risk is None:
            out.append("news reading (not fetched, or the fetch failed)")
        if self.pcr_oi is None:
            out.append("option chain (no snapshot — needs the daily "
                       "Upstox login, or this name has no options)")
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

    #: every heavy source this class can load
    ALL_SOURCES = ("panel", "index", "delivery", "fo_oi", "deals")

    def __init__(self, tickers: Optional[list[str]] = None,
                 tail: int = _PANEL_TAIL,
                 sources: tuple[str, ...] = ALL_SOURCES):
        # `sources` exists for the danger layer, which needs only the peer
        # panel and the index and runs inside engine.refresh — streaming the
        # 3.5M-row delivery file there would be unacceptable. Everything not
        # requested stays empty, which reads downstream as DATA_NEEDED rather
        # than as a quiet zero.
        self.sources_requested = tuple(sources)
        want = set(sources)
        self.panel = _load_panel(tail) if "panel" in want else {}
        self.smap = _sector_map() if "panel" in want else {}
        self.index_s = _load_index_close() if "index" in want else None
        self.fo = _load_fo_oi() if "fo_oi" in want else {}
        self.deals = _load_deals() if "deals" in want else {}
        if "delivery" in want:
            syms = {_base_symbol(t) for t in (tickers or list(self.panel))}
            self.delivery = _load_delivery(syms)
        else:
            self.delivery = {}

        names = sorted(self.panel)
        if not names:
            # Nothing to build cross-sectionally; every field stays empty and
            # externals_at() returns a StockExternals with only the symbol-side
            # panels filled (or none at all).
            self._init_empty()
            return
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

    def _init_empty(self) -> None:
        self.close = pd.DataFrame()
        self.vol = None
        self.ret60 = self.ret10 = pd.DataFrame()
        self.rs_rank = self.turnover_rank = None
        self.by_sector = {}
        self.sector_close = {}
        self.sector_breadth = {}
        self.sector_ret10 = pd.DataFrame()
        self.sector_rank = None
        self.own_sector_rank = pd.DataFrame()
        self.rs_lines = {}
        self.broke_low = pd.DataFrame()
        self.prerise_flag = pd.DataFrame()
        self._sector_break_count = {}
        self._sector_flag_top5 = {}
        self._sector_flag_rank6 = {}
        self._sector_rank_desc = {}
        self.sources = {"panel": False, "sector_map": False,
                        "index": self.index_s is not None,
                        "delivery": bool(self.delivery), "fo_oi": bool(self.fo),
                        "deals": bool(self.deals)}
        self.asof = {"panel": "", "index": "", "deals": ""}
        self._deals_last = None

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
        """Delivery, F&O OI, deals and the small reference feeds."""
        sym = _base_symbol(ticker)
        # Reference feeds are tiny CSVs, cached — cheap enough to attach on
        # every path, including the danger layer's light one (which does not
        # consume them).
        ew = earnings_window(ticker, str(asof.date()) if asof is not None
                             else None)
        if ew:
            sx.earnings_days_away = ew["days_away"]
            sx.earnings_is_estimate = ew["is_estimate"]
        rv = revisions_view(ticker)
        if rv:
            sx.analyst_net_revisions = rv["net_revisions_30d"]
        av = analyst_view(ticker)
        if av:
            sx.analyst_score = av["score"]
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


_panelset_cache: dict[tuple, PanelSet] = {}


def panel_set(tickers: Optional[list[str]] = None, rebuild: bool = False,
              tail: int = _PANEL_TAIL,
              sources: tuple[str, ...] = PanelSet.ALL_SOURCES) -> PanelSet:
    """The shared PanelSet, cached per (tail, sources) — the danger layer's
    light set and the research set are different objects, not one clobbering
    the other."""
    key = (tail, tuple(sources))
    if rebuild or key not in _panelset_cache:
        _panelset_cache[key] = PanelSet(tickers, tail=tail, sources=sources)
    return _panelset_cache[key]


def attach_news(sx: StockExternals, reading: Optional[dict]) -> StockExternals:
    """Copy one stored news reading onto a stock's externals.

    `reading` is a `news_feed.NewsReading.to_dict()` as persisted in
    `portfolio.news_watch` — a plain dict, so this module stays free of any
    news import and the news feed stays a display layer.

    A reading whose fetch FAILED (verdict UNKNOWN) is deliberately not copied:
    every field stays None, so the news triggers read DATA_NEEDED. "We could
    not look" and "we looked and it was quiet" are different facts, and only
    the second one may produce a quiet False.
    """
    if not isinstance(reading, dict):
        return sx
    verdict = str(reading.get("verdict") or "")
    if verdict not in ("TRIGGER", "PASS", "NO_NEWS"):
        return sx
    if reading.get("fetch_failed"):
        return sx
    sx.news_verdict = verdict
    sx.news_tags = tuple(reading.get("tags") or ())
    sx.news_risk = float(reading.get("risk_score") or 0.0)
    sx.news_opp = float(reading.get("opp_score") or 0.0)
    sx.news_sentiment = float(reading.get("sentiment") or 0.0)
    sx.news_count = len(reading.get("headlines") or ())
    sx.news_asof = str(reading.get("asof") or "")
    return sx


def attach_chain(sx: StockExternals, summary: Optional[dict],
                 history: Optional[list], asof: str) -> StockExternals:
    """Copy one stock's option-chain snapshot onto its externals (Node 8).

    `summary`/`history` come from portfolio.option_chain — plain dicts, so
    this module never imports options_chain. A snapshot that is not FRESH
    (older than options_chain.MAX_AGE_DAYS — a login-less day) attaches
    NOTHING: every consumer then reads DATA_NEEDED, because a stale chain
    wearing today's confidence is the exact fault the news UNKNOWN rule
    exists to prevent.
    """
    if not isinstance(summary, dict):
        return sx
    from datetime import date

    from .options_chain import deterioration_pattern, is_fresh
    if not is_fresh(asof, date.today()):
        return sx
    try:
        sx.pcr_oi = float(summary["pcr_oi"])
    except (KeyError, TypeError, ValueError):
        return sx
    skew = summary.get("iv_skew")
    sx.iv_skew = float(skew) if skew is not None else None
    sx.chain_deterioration = deterioration_pattern(history or [])
    sx.chain_days = len(history or [])
    sx.chain_asof = str(asof)
    return sx


#: What the PRODUCTION trigger pass and the /triggers page load — every
#: source EXCEPT the delivery file. MEASURED 2026-09-24 on the owner's report
#: that the box is "hitting 512+": `_load_delivery` streams the 3.5M-row
#: delivery_daily_2019_2026.csv.gz in 400k-row chunks and peaks at **+153 MB**
#: for one batch (boot is 114 MB; the whole 1,200-name scan is +72 MB; the
#: rest of the trigger panels together are +41 MB). It ran once a day inside
#: run_trigger_layer AND on every single /triggers/<stock> page tap.
#:
#: What it bought: the delivery ratio behind T12W_0060 — calibrated
#: **REJECTED** on 2026-08-13 (lift 0.992 over 59,195 windows, firing on 22%
#: of all days) — and register items that read a file frozen at 2026-08-07
#: (nsearchives blocks the cloud IP; Colab is the only refresh path), i.e. a
#: seven-week-old number wearing today's date. 153 MB for a rejected trigger
#: on stale data is the "unwanted" the owner asked to lay off.
#:
#: Consumers already handle the absence: an unloaded source reads
#: DATA_NEEDED, never a quiet False (test_missing_names_the_panels...). The
#: research scripts (calibration, sector-heat study) still pass tail=0 and the
#: full source set explicitly, so nothing measured is affected.
PROD_SOURCES: tuple[str, ...] = tuple(
    s for s in PanelSet.ALL_SOURCES if s != "delivery")


def prepare(tickers: list[str],
            news: Optional[dict[str, dict]] = None,
            chain: Optional[dict] = None,
            sources: tuple[str, ...] = PanelSet.ALL_SOURCES) -> ExternalData:
    """Load every external panel once for this batch of tickers.

    Called at the start of a trigger-layer run (scheduler) or for a single
    stock (the /triggers page). Absent files simply leave fields None.

    `news` is the stored `portfolio.news_watch["rows"]` — pass it and the nine
    news-fed triggers compute; omit it and they stay DATA_NEEDED, exactly as
    before the news module existed.

    `sources` narrows what is loaded; production callers pass PROD_SOURCES
    (see the note above it). The default stays ALL_SOURCES so research code
    that never named its sources is byte-identical.
    """
    tickers = [t for t in dict.fromkeys(tickers) if t]
    ps = PanelSet(tickers, sources=sources)
    ed = ExternalData(sources=dict(ps.sources), asof=dict(ps.asof))
    c_rows = (chain or {}).get("rows") or {}
    c_hist = (chain or {}).get("history") or {}
    c_asof = str((chain or {}).get("asof") or "")
    for t in tickers:
        sx = ps.externals_at(t)
        if news:
            attach_news(sx, news.get(t))
        if c_rows:
            attach_chain(sx, c_rows.get(t), c_hist.get(t), c_asof)
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


# --------------------------------------------------------------------------
# Reference panels fetched 2026-08-13 (owner: "API keys r there, fetch it").
# Per-stock lookups, cached whole because each file is small.
# --------------------------------------------------------------------------

_ref_cache: dict[str, dict] = {}


def _load_ref(name: str, key: str = "symbol") -> dict[str, list[dict]]:
    """{symbol: [rows]} from a small committed CSV. {} when absent."""
    if name in _ref_cache:
        return _ref_cache[name]
    out: dict[str, list[dict]] = {}
    try:
        with open(_DATA / f"{name}.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out.setdefault((row.get(key) or "").strip().upper(),
                               []).append(row)
    except OSError:
        log.warning("external_panels: %s.csv absent", name)
    _ref_cache[name] = out
    return out


def _f(row: dict, key: str) -> Optional[float]:
    v = (row or {}).get(key)
    try:
        return float(v) if v not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


def institutional_holdings(ticker: str) -> Optional[dict]:
    """FII / DII / mutual-fund / insurance holdings + holder counts + pledge.

    Parsed from the NSE shareholding XBRL (the file each committed JSON links
    to). Two quarters per symbol, so the QoQ deltas H-06 and X-07 need are
    real, not inferred. None = not fetched for this symbol.
    """
    rows = _load_ref("shareholding_institutional").get(_base_symbol(ticker))
    if not rows:
        return None
    # newest first: the fetch wrote latest-quarter rows first per symbol
    latest, prior = rows[0], (rows[1] if len(rows) > 1 else None)

    def d(field):
        a, b = _f(latest, field), _f(prior, field) if prior else None
        return None if a is None or b is None else round(a - b, 4)

    return {
        "quarter": latest.get("quarter"), "prior_quarter":
            (prior or {}).get("quarter"),
        "fii_pct": _f(latest, "fii_pct"), "fii_delta": d("fii_pct"),
        "dii_pct": _f(latest, "dii_pct"), "dii_delta": d("dii_pct"),
        "mf_pct": _f(latest, "mf_pct"), "mf_delta": d("mf_pct"),
        "insurance_pct": _f(latest, "insurance_pct"),
        "promoter_pct": _f(latest, "promoter_pct"),
        "promoter_pledged": (latest.get("promoter_pledged") or "").lower(),
        "fii_holders": _f(latest, "fii_holders"),
        "fii_holders_delta": d("fii_holders"),
        "dii_holders": _f(latest, "dii_holders"),
        "mf_holders": _f(latest, "mf_holders"),
        "mf_holders_delta": d("mf_holders"),
        "source": latest.get("source"),
    }


def analyst_view(ticker: str) -> Optional[dict]:
    """Broker ratings, consensus target and analyst count."""
    rows = _load_ref("analyst_ratings").get(_base_symbol(ticker))
    if not rows:
        return None
    r = rows[0]
    counts = {k: _f(r, k) or 0.0 for k in
              ("strong_buy", "buy", "hold", "sell", "strong_sell")}
    total = sum(counts.values())
    if total <= 0:
        return None
    # 0-100, higher = more bullish. Weights are the obvious linear ones and
    # ASSUMED — the register never defined a scoring formula for H-07.
    w = {"strong_buy": 100.0, "buy": 75.0, "hold": 50.0, "sell": 25.0,
         "strong_sell": 0.0}
    return {
        "score": round(sum(counts[k] * w[k] for k in counts) / total, 1),
        "counts": counts, "n_analysts": _f(r, "n_analysts"),
        "mean_rating": _f(r, "mean_rating"),
        "target_mean": _f(r, "target_mean"),
        "period": r.get("period"),
        "formula": "ASSUMED linear weights (100 strong-buy .. 0 strong-sell)",
    }


#: Yahoo's epsTrend history is not reliable: 16 of 189 symbols imply a >100%
#: estimate change in 30 days and 5 carry eps_30d_ago = 0 (a missing value
#: encoded as zero, which would read as an infinite revision). The COUNTS in
#: epsRevisions are a different, sound field. So the percentage is published
#: only inside a sane band and the counts carry the register item.
_MAX_CREDIBLE_EPS_CHANGE_PCT = 50.0


def revisions_view(ticker: str) -> Optional[dict]:
    """EPS revision counts (primary) and estimate drift (guarded).

    The register's momentum item reads the NET REVISION COUNT, not the percent
    change, because this source's historical estimate values are inconsistent
    — see _MAX_CREDIBLE_EPS_CHANGE_PCT. A percent change outside the credible
    band is returned as None with `pct_unreliable` set, never as a number.
    """
    rows = _load_ref("estimate_revisions").get(_base_symbol(ticker))
    if not rows:
        return None
    # the current fiscal year row is the one the register's momentum item wants
    cur = next((r for r in rows if (r.get("period") or "") == "0y"), rows[0])
    now, d7, d30 = (_f(cur, "eps_avg"), _f(cur, "eps_7d_ago"),
                    _f(cur, "eps_30d_ago"))
    up = _f(cur, "up_last30d") or 0.0
    down = _f(cur, "down_last30d") or 0.0
    out = {"period": cur.get("period"), "end_date": cur.get("end_date"),
           "eps_avg": now, "up_30d": up, "down_30d": down,
           "net_revisions_30d": up - down, "pct_change_30d": None,
           "pct_change_7d": None, "pct_unreliable": False}
    for key, base in (("pct_change_30d", d30), ("pct_change_7d", d7)):
        if now is None or base in (None, 0) or base <= 0:
            out["pct_unreliable"] = True
            continue
        chg = round(100.0 * (now / base - 1.0), 3)
        if abs(chg) > _MAX_CREDIBLE_EPS_CHANGE_PCT:
            out["pct_unreliable"] = True    # source inconsistency, not a move
            continue
        out[key] = chg
    return out


def shares_view(ticker: str) -> Optional[dict]:
    """Real shares outstanding and TRUE free float.

    This retires two proxies at once: the ASSUMED shares-from-NI/EPS estimate
    (W-011) and the public-shareholding-% stand-in for free float (X-06).
    """
    rows = _load_ref("shares_outstanding").get(_base_symbol(ticker))
    if not rows:
        return None
    r = rows[0]
    so, fl = _f(r, "shares_outstanding"), _f(r, "float_shares")
    return {
        "shares_outstanding": so, "float_shares": fl,
        "free_float_pct": (round(100.0 * fl / so, 2)
                           if so and fl and so > 0 else None),
        "held_pct_institutions": _f(r, "held_pct_institutions"),
    }


def earnings_window(ticker: str, asof: Optional[str] = None) -> Optional[dict]:
    """Days until the next reported earnings date.

    FORWARD-LOOKING SNAPSHOT: this is the next date as known at fetch time, so
    it can inform today but cannot be backtested — there is no archive of past
    'next earnings date' values. `is_estimate` says whether the exchange has
    confirmed the date or Yahoo guessed it.
    """
    rows = _load_ref("earnings_dates").get(_base_symbol(ticker))
    if not rows:
        return None
    r = rows[0]
    date = (r.get("earnings_date") or "").strip()
    if not date:
        return None
    try:
        import datetime as _dt
        d = _dt.date.fromisoformat(date)
        ref = (_dt.date.fromisoformat(asof[:10]) if asof
               else _dt.date.today())
        days = (d - ref).days
    except ValueError:
        return None
    return {"earnings_date": date, "days_away": days,
            "is_estimate": str(r.get("is_estimate", "")).lower() == "true"}


def usdinr_series() -> Optional[pd.Series]:
    """USDINR daily close (market-level, feeds W-025)."""
    try:
        df = pd.read_csv(_DATA / "usdinr_daily.csv", parse_dates=["date"])
    except OSError:
        return None
    s = pd.Series(pd.to_numeric(df["close"], errors="coerce").to_numpy(),
                  index=df["date"]).dropna().sort_index()
    return s if len(s) >= 30 else None


def reset_caches() -> None:
    """Drop cached panels (tests, and after a weekly data top-up)."""
    global _insider_cache, _fo_cache, _deals_cache
    _panel_cache.clear()
    _panelset_cache.clear()
    _ref_cache.clear()
    _insider_cache = _fo_cache = _deals_cache = None
