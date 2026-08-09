"""Selection Register v4 — the owner's 279-item register, wired READ-ONLY.

Source of truth: research/external/selection_register_v4_279.csv (extracted
verbatim from the owner's Selection_Register_v4_279_OwnBuild.xlsx, uploaded
2026-08-09). The registry metadata is parsed FROM that CSV at import so code
can never drift from the sheet — same discipline as trading/triggers.py.

SCOPE (owner order 2026-08-09 "discuss and wire the rest"): this module
computes the ~128 register items AM07 did not already have, as a DISPLAY /
RESEARCH layer only. The register's own rules apply: REQUIRES_15Y_BACKTEST
before anything touches money, aggressive variants never production, fail
closed on missing data. HARD BOUNDARY: engine.py / risk.py / orders.py must
never import this module (test-enforced), and this module must never place
orders, mutate config, or call the network.

Honesty rules carried from the trigger layer:
- A formula is implemented ONLY as written in the register. Where the
  register leaves a constant or input undefined, the value used here is in
  ASSUMED_CONSTANTS and the reading is labelled.
- Insufficient data -> state NA or DATA_NEEDED with the reason, never a
  silent default.
- Items the register defines against external data we don't hold stay
  DATA_NEEDED with what unlocks them.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from trading.triggers import _Ctx, _normalize_ohlcv  # display-layer sibling

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_REG_CSV = _ROOT / "research" / "external" / "selection_register_v4_279.csv"
_FUND_CSV = _ROOT / "data" / "fundamentals_n200_2026-08-09.csv"
_STMT_ZIP = _ROOT / "data" / "fundamentals_yf_statements_2026-08-09.zip"
_PWOI_CSV = _ROOT / "data" / "fo_participant_oi_2024_2026.csv"
_FII_CSV = _ROOT / "data" / "fii_dii_cash.csv"
_INDEX_CSV = _ROOT / "data" / "index_daily_15y.csv"
_BOND_CSV = _ROOT / "data" / "india_10y_bond_yield.csv"

# ---------------------------------------------------------------------------
# Registry: parsed from the owner's sheet, never hand-copied
# ---------------------------------------------------------------------------


def load_registry() -> list[dict]:
    out = []
    with open(_REG_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("ID"):
                out.append(row)
    return out


REGISTRY: list[dict] = load_registry()
REGISTRY_BY_ID: dict[str, dict] = {r["ID"]: r for r in REGISTRY}

# Where the ~151 already-present items live in AM07. Shown on the page so a
# register row is never re-implemented twice. Kept as data, not behaviour.
AM07_HAVE: dict[str, str] = {
    **{f"TRG-{i:03d}": "trading/triggers.py (79 compute, 33 DATA_NEEDED)"
       for i in range(1, 113)},
    **{f"TG-0{i}": "trading/triggers.py gates" for i in range(1, 9)},
    **{f"TC-0{i}": "trading/triggers.py clusters" for i in range(1, 9)},
    "A-01": "momentum/returns core", "A-02": "candles.py gap logic",
    "A-03": "lens_v2.atr / candles", "A-04": "candles.py body logic",
    "A-05": "candles.py wick logic", "A-06": "candles.py wick logic",
    "B-01": "signals/technicals.py sma + ma_stack_score",
    "B-02": "signals/technicals.py ema",
    "B-04": "signals/technicals.py rsi",
    "B-05": "signals/technicals.py macd",
    "B-06": "signals/technicals.py bollinger/52w-high proximity (partial)",
    "C-01": "volume-expansion lane / candles volume ratio",
    "C-02": "lens_v2.atr + market_panels.atr_panel",
    "D-01": "risk.py sector caps",
    "F-01": "AM07 composite (exit ruler)",
    "H-08": "signals/technicals.py beta_vs_benchmark",
    "H-13": "signals/technicals.py adx",
    "H-23": "engines Everest/Ocean (Ocean parked)",
    "H-24": "modes.PRESETS nifty200_momentum (LIVE)",
    "H-26": "cfg.blacklisted_sectors mechanism (EMPTY — register's "
            "\"['Metal'] live\" claim is stale, audit removed it)",
    "W-008": "signals/technicals.py beta_vs_benchmark",
    "W-023": "regime.py India VIX level",
    "W-024": "regime.py vix_crush (120d spike vs current)",
    "VIX-01": "regime panic/correction entry block (variant)",
}

# Every constant or input the register does NOT pin down. On-page, labelled.
ASSUMED_CONSTANTS: dict[str, str] = {
    "E-tags volume ratio": "volume ratio = volume / SMA20(volume) (W-009 form)",
    "B-07 percentile": "percentile computed within the loaded panel, not the "
                       "full eligible universe",
    "H-18 band": "S/R touch = high or low within ±1.5% of level; levels from "
                 "swing highs/lows (wing 2), recency weight = 0.5^(age/125d)",
    "H-21 proxy": "register wants SECTOR INDEX z-score; no sector index "
                  "series in repo — per-stock 24M z vs own 10Y shown as "
                  "PROXY, clearly labelled",
    "H-22 window": "uses full stored history (~12y), not exactly 10Y",
    "PX tails": "tail_down3 = share of panel members whose worst 3-day "
                "return inside the window <= -3% (register never defines "
                "the cut; -3% ASSUMED). tail_up3 mirror at +3%",
    "PX percentile refs": "p10/p25/p75/p90 legs computed vs the loaded "
                          "panel-history window (~250d), not 10Y",
    "W-011/W-015/W-016/X-13/X-16 shares": "shares outstanding approximated "
        "as annualNetIncome / annualBasicEPS from the committed "
        "fundamentals (no shares field in repo); market cap = shares x "
        "last close",
    "W-018 EBITDA": "EBITDA row from statements zip when present, else "
                    "operating income as a labelled approximation",
    "FII-07 PCR": "PE_OI/CE_OI from the PWOI TOTAL row (puts long+short / "
                  "calls long+short), index options",
    "X-15 sigma leg": "needs 8 quarters of surprises; repo holds ~5 "
                      "quarters — raw YoY qEPS change shown, sigma leg "
                      "DATA_NEEDED",
}

# S/U rows: the register audits the ASH08 build. AM07's equivalents, for the
# page — documentation, not computation.
U_S_MAPPING: dict[str, str] = {
    "U-01": "AM07 has no CORE_MIN concept; universe = full NSE auto-refresh "
            "(2,393), scan = rotating ~50/day",
    "U-02": "AM07 cap = max_positions 80 (book), scan bucket ~50",
    "U-03": "AM07 discovery = whole universe via rotation, no 5000 cap needed",
    "U-04": "AM07 liquidity gate fails closed post-audit (A-batch); "
            "turnover floor Rs 1Cr+ median",
    "U-05": "AM07 uses median daily turnover, not 5-day sum — same intent",
    "U-06": "AM07 eligibility = liquidity + blocklist + score floor + "
            "abs-momentum + regime gates, all fail-closed",
    "U-07": "AM07 ranks by momentum score, never alphabetical (the "
            "alphabetical-bucket fault was found and fixed 2026-08-04)",
    "S-01": "AM07 momentum = 6m+12m vol-adjusted rank blend (v2.1/v3 "
            "tested) — already rank-based, the register's canonical form",
    "S-02": "AM07 quality blend 35% exists; missing-inputs handling differs",
    "S-03": "AM07 blend 65/35 equivalent lives in strategy config",
    "S-04": "AM07 ADV-equivalent gate fails closed (audit A-batch)",
    "S-05": "AM07 turnover gate fails closed",
    "S-06": "AM07 staleness guard: DATA_STALE + HOLD (stale-data guard)",
    "S-07": "AM07 absolute-momentum gate ON (own 6m return > 0)",
    "S-08": "AM07 computes REAL pairwise correlation, cap 0.85 (risk_v2)",
    "S-09": "AM07 hard pass = all gates AND; kill switch on top",
    "S-10": "AM07 momentum floor = 62 (owner order 2026-07-30); ASH08 "
            "ruling 67 applies to ASH08's different score scale",
    "S-11": "AM07 has no WATCH band; trigger layer provides watchlist",
    "S-12": "AM07 states: scan pick / no-pick / blocked (journaled); "
            "UNKNOWN-vs-REJECT split adopted here as page telemetry",
    "S-13": "coverage telemetry — computed on this page (see scan_quality)",
    "S-14": "effective score telemetry — computed on this page",
}


@dataclass
class Reading:
    """One register item evaluated. state: VALUE | FIRED | QUIET | NA |
    PARTIAL | DATA_NEEDED | HAVE."""
    id: str
    state: str
    value: Optional[float] = None
    note: str = ""


def _na(rid: str, why: str = "insufficient history") -> Reading:
    return Reading(rid, "NA", None, why)


def _dn(rid: str, why: str) -> Reading:
    return Reading(rid, "DATA_NEEDED", None, why)


# ---------------------------------------------------------------------------
# Fundamentals: curated CSV (fast) + full statements zip (line items)
# ---------------------------------------------------------------------------

_fund_cache: dict[str, dict[str, dict[str, float]]] = {}
_stmt_cache: dict[str, dict[str, pd.DataFrame]] = {}


def load_fundamentals(symbol: str) -> dict[str, dict[str, float]]:
    """{field: {as_of: value}} for one symbol from the curated CSV."""
    global _fund_cache
    if not _fund_cache:
        try:
            with open(_FUND_CSV) as f:
                for r in csv.DictReader(f):
                    _fund_cache.setdefault(r["symbol"], {}).setdefault(
                        r["field"], {})[r["as_of"]] = float(r["value"])
        except OSError:
            log.warning("register_v4: fundamentals CSV missing")
    return _fund_cache.get(symbol, {})


def load_statements(symbol: str) -> dict[str, pd.DataFrame]:
    """Full yfinance statements for one symbol from the committed zip.
    {'bs'|'is'|'cf'|'qis': DataFrame(index=line item, columns=dates)}."""
    if symbol in _stmt_cache:
        return _stmt_cache[symbol]
    out: dict[str, pd.DataFrame] = {}
    try:
        with zipfile.ZipFile(_STMT_ZIP) as z:
            names = set(z.namelist())
            for kind in ("bs", "is", "cf", "qis"):
                m = f"fundamentals/{symbol}_{kind}.csv"
                if m in names:
                    df = pd.read_csv(io.BytesIO(z.read(m)), index_col=0)
                    out[kind] = df
    except OSError:
        pass
    _stmt_cache[symbol] = out
    if len(_stmt_cache) > 64:          # bound RAM on the 512MB box
        _stmt_cache.pop(next(iter(_stmt_cache)))
    return out


def _latest(series: dict[str, float]) -> Optional[tuple[str, float]]:
    if not series:
        return None
    d = max(series)
    return d, series[d]


def _annual_pair(series: dict[str, float]) -> Optional[tuple[float, float]]:
    """(latest, previous) annual values, or None."""
    if len(series) < 2:
        return None
    ds = sorted(series)
    return series[ds[-1]], series[ds[-2]]


def _stmt_row(stm: dict[str, pd.DataFrame], kind: str, *labels: str
              ) -> Optional[pd.Series]:
    df = stm.get(kind)
    if df is None:
        return None
    for lab in labels:
        if lab in df.index:
            s = pd.to_numeric(df.loc[lab], errors="coerce").dropna()
            if len(s):
                return s
    return None


def _shares_estimate(fund: dict) -> Optional[float]:
    """ASSUMED: shares ~= net income / basic EPS (no shares field in repo)."""
    ni = _latest(fund.get("annualNetIncome", {}))
    eps = _latest(fund.get("annualBasicEPS", {}))
    if not ni or not eps or not eps[1]:
        return None
    return ni[1] / eps[1]


# ---------------------------------------------------------------------------
# Per-stock items
# ---------------------------------------------------------------------------

def stock_view(df: pd.DataFrame, symbol: str = "",
               bench_close: Optional[pd.Series] = None) -> dict[str, Reading]:
    """Evaluate every per-stock register item AM07 lacked. df = daily OHLCV
    (either column case). bench_close = Nifty (or N200) adjusted closes for
    RS items. Fundamentals load from the committed files by symbol."""
    out: dict[str, Reading] = {}
    try:
        x = _Ctx(df)
    except (KeyError, ValueError) as e:
        why = f"bad OHLCV: {e}"
        for rid in _STOCK_IDS:
            out[rid] = _na(rid, why)
        return out

    c, n = x.c, x.n
    vol_ratio = x.v[-1] / x.vol20[-1] if n >= 21 and x.vol20[-1] > 0 else np.nan
    body = (c - x.o)
    rng = np.where(x.rng > 0, x.rng, np.nan)
    prev_c = x.prev_c

    def fired(rid, cond, note=""):
        out[rid] = Reading(rid, "FIRED" if cond else "QUIET", None, note)

    def value(rid, val, note=""):
        if val is None or (isinstance(val, float) and not np.isfinite(val)):
            out[rid] = _na(rid)
        else:
            out[rid] = Reading(rid, "VALUE", float(val), note)

    # ---- E group: pattern tags (register's PROPOSED v1 formulas) ----------
    if n >= 22:
        uw_pct = 100 * (x.h[-1] - max(x.o[-1], c[-1])) / prev_c[-1]
        lw = min(x.o[-1], c[-1]) - x.l[-1]
        uw = x.h[-1] - max(x.o[-1], c[-1])
        body_abs = abs(c[-1] - x.o[-1])
        close_pos = (c[-1] - x.l[-1]) / rng[-1] if np.isfinite(rng[-1]) else 0.5
        fired("E-01", uw_pct >= 2.0 and uw >= 3 * body_abs
              and close_pos <= 0.40 and vol_ratio >= 1.5)
        body_frac = body_abs / rng[-1] if np.isfinite(rng[-1]) else 0.0
        fired("E-02", vol_ratio >= 2.5 and body_frac <= 0.25 and lw >= uw)
        low20 = np.min(x.l[-21:-1])
        body_pct = 100 * (c[-1] - x.o[-1]) / prev_c[-1]
        fired("E-03", c[-1] < low20 and body_pct <= -1.5 and vol_ratio >= 1.5)
        high20 = np.max(x.h[-21:-1])
        fired("E-04", x.h[-1] > high20 and c[-1] < high20 and uw_pct >= 1.5,
              "trap = broke prior-20 high, closed back inside")
        # E-05 needs RS percentile -> bench required
        if bench_close is not None and len(bench_close) >= 64 and n >= 64:
            rs3 = x.ret(63) - (bench_close.iloc[-1] / bench_close.iloc[-64] - 1)
            sma20 = x.ma20[-1]
            pullback = 1 - c[-1] / np.max(x.h[-21:])
            # percentile leg needs a panel; single-stock uses raw RS > 0 as
            # the >=80th-pct stand-in, labelled
            fired("E-05", rs3 > 0 and c[-1] > sma20 and pullback <= 0.05,
                  "panel percentile leg approximated by RS>0 (single-stock)")
        else:
            out["E-05"] = _na("E-05", "needs benchmark closes")
        if n >= 30:
            sma50v = x.ma50[-1] if n >= 50 else np.nan
            downtrend = np.isfinite(sma50v) and c[-1] < sma50v and \
                x.slope(x.ma20, 10) < 0
            ups = 0
            for k in range(1, 5):
                if c[-k] > c[-k - 1]:
                    ups += 1
                else:
                    break
            weak = False
            if 2 <= ups <= 4 and n >= 21 + ups:
                bounce = c[-1] / c[-1 - ups] - 1
                peak_before = np.max(c[-21 - ups:-ups])
                decline = c[-1 - ups] / peak_before - 1
                weak = (downtrend and decline < 0
                        and bounce < 0.5 * abs(decline)
                        and (vol_ratio < 1 if np.isfinite(vol_ratio) else False))
            fired("E-06", bool(weak))
        else:
            out["E-06"] = _na("E-06")
    else:
        for rid in ("E-01", "E-02", "E-03", "E-04", "E-05", "E-06"):
            out[rid] = _na(rid)

    # ---- B-07 relative strength vs benchmark ------------------------------
    if bench_close is not None and n >= 127 and len(bench_close) >= 127:
        rs = {}
        for label, days in (("1M", 21), ("3M", 63), ("6M", 126)):
            rs[label] = x.ret(days) - (bench_close.iloc[-1] /
                                       bench_close.iloc[-1 - days] - 1)
        value("B-07", rs["6M"] * 100,
              f"RS 1M {rs['1M']*100:+.1f}pp / 3M {rs['3M']*100:+.1f}pp / "
              f"6M {rs['6M']*100:+.1f}pp; universe percentile needs panel")
    else:
        out["B-07"] = _na("B-07", "needs benchmark closes + 6M history")

    # ---- H group -----------------------------------------------------------
    for rid, yrs in (("H-11", 3), ("H-11b", 5)):
        pass
    if n >= 756:
        cagr3 = (c[-1] / c[-756]) ** (1 / 3) - 1
        cagr5 = (c[-1] / c[-1260]) ** (1 / 5) - 1 if n >= 1260 else None
        note = f"3Y {cagr3*100:.1f}%" + (f" / 5Y {cagr5*100:.1f}%" if cagr5 else " / 5Y NA")
        value("H-11", cagr3 * 100, note)
        seg = c[-756:]
        peak = np.maximum.accumulate(seg)
        value("H-12", float(np.min(seg / peak - 1)) * 100, "3Y max drawdown %")
    else:
        out["H-11"] = _na("H-11", "needs 3y bars")
        out["H-12"] = _na("H-12", "needs 3y bars")
    if n >= 200:
        sma100 = pd.Series(c).rolling(100).mean().to_numpy()
        state = "above" if c[-1] > sma100[-1] else "below"
        fired("H-14", c[-1] > sma100[-1], f"close {state} SMA100")
    else:
        out["H-14"] = _na("H-14")
    if bench_close is not None and n >= 253 and len(bench_close) >= 253:
        value("H-15", (x.ret(252) - (bench_close.iloc[-1] /
              bench_close.iloc[-253] - 1)) * 100,
              "12M RS pp; percentile needs panel")
    else:
        out["H-15"] = _na("H-15", "needs benchmark + 12M history")
    if n >= 75:
        gaps = 100 * np.abs(x.o[1:] / c[:-1] - 1)
        atrp = 100 * x.atr14 / np.where(prev_c > 0, prev_c, np.nan)
        recent_g, recent_a = gaps[-60:], atrp[-60:]
        ok = np.isfinite(recent_a)
        value("H-16", float(np.sum(recent_g[ok] > recent_a[ok]) / 60),
              "share of last 60 sessions gapping > 1x ATR14%")
    else:
        out["H-16"] = _na("H-16")
    if n >= 111:
        b = t = 0
        for i in range(n - 90, n):
            h20 = np.max(x.h[i - 20:i])
            if x.h[i] > h20:
                if c[i] < h20:
                    t += 1
                else:
                    b += 1
        rate = t / (b + t) if (b + t) else None
        out["H-17"] = Reading("H-17", "VALUE", rate,
                              f"breakouts {b}, traps {t} in 90d"
                              if rate is not None else "no events in 90d")
    else:
        out["H-17"] = _na("H-17")
    if n >= 250:
        piv = [x.l[i] for i in x.swing_lows(250, 2)] + \
              [x.h[i] for i in x.swing_highs(250, 2)]
        level_hits = 0.0
        px = c[-1]
        idx_l, idx_h = x.swing_lows(250, 2), x.swing_highs(250, 2)
        for i in idx_l + idx_h:
            lvl = x.l[i] if i in idx_l else x.h[i]
            if abs(lvl / px - 1) <= 0.015:
                age = n - 1 - i
                level_hits += 0.5 ** (age / 125)
        value("H-18", level_hits,
              "touch-count x recency within +/-1.5% of price (ASSUMED decay)")
    else:
        out["H-18"] = _na("H-18")
    if n >= 2400:
        r24 = pd.Series(c).pct_change(504).dropna()
        z = (r24.iloc[-1] - r24.mean()) / r24.std() if r24.std() else np.nan
        state = "BOOM" if z > 2 else ("BUST" if z < -2 else "neutral")
        value("H-21", float(z), f"{state} — PER-STOCK PROXY, register wants "
                                "sector index (not in repo)")
    else:
        out["H-21"] = _na("H-21", "needs ~10y bars")
    if n >= 1500:
        s = pd.Series(c, index=pd.to_datetime(df.index[-n:]) if not
                      isinstance(df.index, pd.DatetimeIndex) else df.index[-n:])
        mret = s.resample("ME").last().pct_change().dropna()
        if len(mret) >= 60:
            m = mret.index[-1].month
            month_m = mret[mret.index.month == m]
            rest = mret[mret.index.month != m]
            if len(month_m) >= 5 and month_m.std():
                t = (month_m.mean() - rest.mean()) / \
                    (month_m.std() / np.sqrt(len(month_m)))
                sig = abs(t) >= 2
                value("H-22", float(month_m.mean() * 100),
                      f"month-{m} mean vs rest, t={t:.2f} "
                      f"({'significant' if sig else 'NO_EFFECT'})")
            else:
                out["H-22"] = _na("H-22")
        else:
            out["H-22"] = _na("H-22")
    else:
        out["H-22"] = _na("H-22", "needs ~6y+ bars")

    # ---- W group (price/volume) -------------------------------------------
    if n >= 253:
        value("W-003", (x.ret(126) - (c[-64] / c[-190] - 1)) * 100,
              "Mom(6M now) - Mom(6M, 3M ago), pp")
        rets = pd.Series(c).pct_change().dropna()
        neg = rets[rets < 0]
        value("W-006", float(neg.tail(252).std() * np.sqrt(252) * 100),
              "annualized downside vol %")
        value("X-01", (c[-22] / c[-253] - 1) * 100, "12-1 skip-month return %")
        vol252 = float(rets.tail(252).std() * np.sqrt(252) * 100)
        value("X-04", vol252, "annualized vol % (low preferred)")
        lo52 = np.min(x.l[-252:])
        value("X-09", (c[-1] / lo52 - 1) * 100,
              "% above 52w low (rule wants >= 30)")
        sma200 = pd.Series(c).rolling(200).mean().to_numpy()
        run = 0
        for k in range(1, min(n - 200, 120)):
            if sma200[-k] > sma200[-k - 1]:
                run += 1
            else:
                break
        out["X-10"] = Reading("X-10", "FIRED" if run >= 20 else "QUIET",
                              float(run), f"200DMA rising {run} sessions")
    else:
        for rid in ("W-003", "W-006", "X-01", "X-04", "X-09", "X-10"):
            out[rid] = _na(rid, "needs 12M bars")
    if bench_close is not None and n >= 253 and len(bench_close) >= 253:
        sret = pd.Series(c).pct_change().dropna().tail(252).to_numpy()
        bret = bench_close.pct_change().dropna().tail(252).to_numpy()
        m = min(len(sret), len(bret))
        sret, bret = sret[-m:], bret[-m:]
        if m >= 100 and np.var(bret) > 0:
            beta = float(np.cov(sret, bret)[0, 1] / np.var(bret))
            resid = sret - beta * bret
            value("W-004", float(np.sum(resid[-126:]) * 100),
                  "6M residual momentum pp (market regression; sector leg "
                  "not available)")
            value("W-005", float(np.std(resid) * np.sqrt(252) * 100),
                  "idiosyncratic vol % (market factor removed)")
            rf = 0.065 / 252
            alpha = float((np.mean(sret) - rf - beta *
                          (np.mean(bret) - rf)) * 252 * 100)
            value("X-02", alpha, f"Jensen alpha %/yr, beta {beta:.2f} "
                                 "(Rf ASSUMED 6.5%)")
            out["X-20"] = Reading("X-20",
                                  "FIRED" if 0.6 <= beta <= 1.0 else "QUIET",
                                  beta, "preferred band 0.6-1.0")
            fired("W-002", x.ret(126) > 0 and
                  x.ret(126) > (bench_close.iloc[-1] / bench_close.iloc[-127] - 1),
                  "abs mom > 0 AND rel mom > benchmark")
            value("W-001", (x.ret(126) - (bench_close.iloc[-1] /
                  bench_close.iloc[-127] - 1)) * 100,
                  "6M return vs benchmark pp; cross-sectional rank needs panel")
        else:
            for rid in ("W-004", "W-005", "X-02", "X-20", "W-002", "W-001"):
                out[rid] = _na(rid, "aligned return overlap too short")
    else:
        for rid in ("W-004", "W-005", "X-02", "X-20", "W-002", "W-001"):
            out[rid] = _na(rid, "needs benchmark closes")
    if n >= 21:
        value("W-009", vol_ratio, "volume / SMA20(volume)")
        r1 = np.abs(np.diff(c) / c[:-1])
        v1 = x.v[1:]
        ok = v1 > 0
        if ok[-60:].sum() >= 30:
            value("W-010", float(np.mean((r1[-60:][ok[-60:]] /
                  v1[-60:][ok[-60:]])) * 1e9),
                  "Amihud |ret|/volume x1e9, 60d")
        else:
            out["W-010"] = _na("W-010", "volume zero/missing in window")
        mfm = ((c - x.l) - (x.h - c)) / np.where(x.rng > 0, x.rng, np.nan)
        mfv = np.nan_to_num(mfm) * x.v
        vsum = x.v[-20:].sum()
        value("W-012", float(mfv[-20:].sum() / vsum) if vsum > 0 else None,
              "Chaikin Money Flow 20d")
    else:
        for rid in ("W-009", "W-010", "W-012"):
            out[rid] = _na(rid)
    if n >= 12:
        atr10 = pd.Series(x.tr).rolling(10).mean().to_numpy()
        mid = (x.h + x.l) / 2
        upper, lower = mid + 3 * atr10, mid - 3 * atr10
        st_state = "up" if c[-1] > lower[-2] and c[-1] > upper[-2] else \
                   ("down" if c[-1] < lower[-2] else "up" if c[-1] > upper[-2] else "flat")
        # canonical supertrend needs stateful bands; simplified flip shown
        above = c[-1] > upper[-2]
        below = c[-1] < lower[-2]
        out["X-18"] = Reading(
            "X-18", "FIRED" if (above or below) else "QUIET", None,
            f"close vs ATR(10)x3 bands: {'above' if above else 'below' if below else 'inside'}"
            " (simplified band, not stateful supertrend)")
    else:
        out["X-18"] = _na("X-18")

    # ---- fundamentals-based (curated CSV + statements zip) ----------------
    fund = load_fundamentals(symbol) if symbol else {}
    stm = load_statements(symbol) if symbol else {}
    px_last = float(c[-1])
    shares = _shares_estimate(fund)
    mcap = shares * px_last if shares else None

    def frac(rid, num, den, note=""):
        if num is None or den in (None, 0):
            out[rid] = _dn(rid, "fundamentals field missing")
        else:
            value(rid, num / den, note)

    gp = _latest(fund.get("annualGrossProfit", {}))
    ta = _latest(fund.get("annualTotalAssets", {}))
    frac("W-013", gp and gp[1], ta and ta[1], "gross profit / total assets")
    ni = _latest(fund.get("annualNetIncome", {}))
    eq = _latest(fund.get("annualStockholdersEquity", {}))
    frac("W-014", ni and ni[1], eq and eq[1], "ROE")
    eps = _latest(fund.get("annualBasicEPS", {}))
    frac("W-015", eps and eps[1], px_last, "EPS / price")
    fcf = _latest(fund.get("annualFreeCashFlow", {}))
    if fcf and mcap:
        value("W-016", fcf[1] / mcap, "FCF / mcap (shares ASSUMED NI/EPS)")
    else:
        out["W-016"] = _dn("W-016", "needs FCF + shares estimate")
    wc = _stmt_row(stm, "bs", "Working Capital")
    if wc is not None and len(wc) >= 2 and ta:
        value("W-017", float((wc.iloc[0] - wc.iloc[1]) / ta[1]),
              "d(working capital)/assets from statements zip")
    else:
        out["W-017"] = _dn("W-017", "Working Capital rows not in statements")
    debt = _latest(fund.get("annualTotalDebt", {}))
    ebitda = _stmt_row(stm, "is", "EBITDA", "Normalized EBITDA")
    opi = _latest(fund.get("annualOperatingIncome", {}))
    if debt and ebitda is not None and len(ebitda) and ebitda.iloc[0]:
        value("W-018", debt[1] / float(ebitda.iloc[0]), "debt / EBITDA")
    elif debt and opi and opi[1]:
        value("W-018", debt[1] / opi[1],
              "debt / OPERATING INCOME (EBITDA row missing — labelled approx)")
    else:
        out["W-018"] = _dn("W-018", "needs debt + EBITDA")

    epss = fund.get("annualBasicEPS", {})
    if len(epss) >= 3:
        ds = sorted(epss)
        growth = [epss[ds[i + 1]] / epss[ds[i]] - 1
                  for i in range(len(ds) - 1) if epss[ds[i]]]
        if len(growth) >= 2:
            out["X-03"] = Reading("X-03", "PARTIAL", float(np.std(growth)),
                                  f"sigma over {len(growth)} growth points "
                                  "(register wants 5Y; repo holds ~4 years)")
        else:
            out["X-03"] = _dn("X-03", "EPS history too short")
    else:
        out["X-03"] = _dn("X-03", "needs >=3 annual EPS points")

    # Piotroski
    legs, miss = [], []
    ni2 = _annual_pair(fund.get("annualNetIncome", {}))
    ta2 = _annual_pair(fund.get("annualTotalAssets", {}))
    cfo = _latest(fund.get("annualOperatingCashFlow", {}))
    debt2 = _annual_pair(fund.get("annualTotalDebt", {}))
    gp2 = _annual_pair(fund.get("annualGrossProfit", {}))
    rev2 = _annual_pair(fund.get("annualTotalRevenue", {}))
    if ni2 and ta2:
        legs.append(ni2[0] / ta2[0] > 0)                       # ROA>0
        legs.append(ni2[0] / ta2[0] > ni2[1] / ta2[1])         # dROA>0
    else:
        miss += ["ROA", "dROA"]
    if cfo:
        legs.append(cfo[1] > 0)                                # CFO>0
        if ni2:
            legs.append(cfo[1] > ni2[0])                       # CFO>NI
        else:
            miss.append("CFO>NI")
    else:
        miss += ["CFO", "CFO>NI"]
    if debt2 and ta2:
        legs.append(debt2[0] / ta2[0] < debt2[1] / ta2[1])     # dLev<0
    else:
        miss.append("dLeverage")
    ca = _stmt_row(stm, "bs", "Current Assets")
    cl = _stmt_row(stm, "bs", "Current Liabilities")
    if ca is not None and cl is not None and len(ca) >= 2 and len(cl) >= 2 \
            and cl.iloc[0] and cl.iloc[1]:
        legs.append(ca.iloc[0] / cl.iloc[0] > ca.iloc[1] / cl.iloc[1])
    else:
        miss.append("dCurrentRatio")
    sh = _stmt_row(stm, "bs", "Ordinary Shares Number", "Share Issued")
    if sh is not None and len(sh) >= 2:
        legs.append(sh.iloc[0] <= sh.iloc[1] * 1.02)           # no new shares
    else:
        miss.append("shareIssuance")
    if gp2 and rev2 and rev2[0] and rev2[1]:
        legs.append(gp2[0] / rev2[0] > gp2[1] / rev2[1])       # dMargin>0
        if ta2:
            legs.append(rev2[0] / ta2[0] > rev2[1] / ta2[1])   # dTurnover>0
        else:
            miss.append("dTurnover")
    else:
        miss += ["dMargin", "dTurnover"]
    if legs:
        state = "VALUE" if not miss else "PARTIAL"
        out["X-12"] = Reading("X-12", state, float(sum(legs)),
                              f"{sum(legs)}/{len(legs)} legs true"
                              + (f"; missing: {','.join(miss)}" if miss else ""))
    else:
        out["X-12"] = _dn("X-12", "no fundamentals for symbol")

    # Altman Z
    re_ = _stmt_row(stm, "bs", "Retained Earnings")
    tl = None
    if ta and eq:
        tl = ta[1] - eq[1]
    if wc is not None and len(wc) and ta and re_ is not None and len(re_) \
            and opi and mcap and tl:
        z = (1.2 * float(wc.iloc[0]) / ta[1] + 1.4 * float(re_.iloc[0]) / ta[1]
             + 3.3 * opi[1] / ta[1] + 0.6 * mcap / tl
             + 1.0 * (rev2[0] if rev2 else 0) / ta[1])
        out["X-13"] = Reading("X-13", "PARTIAL" if not rev2 else "VALUE",
                              float(z),
                              ("Z; exclude <1.8; MVE uses ASSUMED shares; "
                               "EBIT=operating income"))
    else:
        out["X-13"] = _dn("X-13", "needs WC/RE/EBIT/mcap/liabilities")

    # Beneish — needs many line items; compute what exists, list the rest
    ben_missing = []
    recv = _stmt_row(stm, "bs", "Accounts Receivable", "Receivables")
    if recv is None or len(recv) < 2:
        ben_missing.append("receivables")
    sga = _stmt_row(stm, "is", "Selling General And Administration")
    if sga is None or len(sga) < 2:
        ben_missing.append("SGA")
    dep = _stmt_row(stm, "cf", "Depreciation And Amortization",
                    "Depreciation Amortization Depletion")
    if dep is None or len(dep) < 2:
        ben_missing.append("depreciation")
    if ben_missing:
        out["X-14"] = _dn("X-14", "statement rows missing: "
                          + ",".join(ben_missing))
    else:
        out["X-14"] = Reading("X-14", "PARTIAL", None,
                              "all 8 ratios need 2y aligned rows — "
                              "full M-score deferred to research script")

    qeps = fund.get("quarterlyBasicEPS", {})
    if len(qeps) >= 5:
        ds = sorted(qeps)
        yoy = qeps[ds[-1]] - qeps[ds[-5]]
        out["X-15"] = Reading("X-15", "PARTIAL", float(yoy),
                              "qEPS YoY change; SUE sigma leg needs 8 "
                              "quarters of surprises (repo holds ~5)")
    else:
        out["X-15"] = _dn("X-15", "needs 5+ quarters of EPS")
    if eq and mcap:
        value("X-16", eq[1] / mcap, "book/market (shares ASSUMED NI/EPS)")
    else:
        out["X-16"] = _dn("X-16", "needs equity + shares estimate")
    ta_pair = _annual_pair(fund.get("annualTotalAssets", {}))
    if ta_pair and ta_pair[1]:
        value("X-17", ta_pair[0] / ta_pair[1] - 1,
              "YoY total-asset growth (low preferred)")
    else:
        out["X-17"] = _dn("X-17", "needs 2 annual asset points")

    # items whose data simply is not in the repo
    for rid, why in _STOCK_DATA_NEEDED.items():
        out.setdefault(rid, _dn(rid, why))
    return out


_STOCK_DATA_NEEDED = {
    "H-01": "promoter holding % — shareholding pattern feed not in repo",
    "H-02": "promoter pledge % — not in repo",
    "H-03": "FII holding % per stock — not in repo",
    "H-04": "DII holding % per stock — not in repo",
    "H-05": "MF holding % per stock — not in repo",
    "H-06": "institutional activity delta — needs H-03..05",
    "H-07": "analyst ratings — no feed",
    "H-09": "R&D intensity — line item not in statements coverage",
    "H-10": "ESG score — no feed",
    "H-19": "macro liquidity — M2/liquidity series not in repo",
    "H-20": "block/bulk deal feed not in repo",
    "H-25": "insider (SAST/PIT) filings feed not in repo",
    "H-27": "infra item: per-trade attribution — build order, not a metric",
    "H-28": "infra item: bootstrap CI — build order, not a metric",
    "W-007": "per-stock implied vol needs options chain (index-level VRP "
             "on market page)",
    "W-011": "true shares outstanding not in repo (NI/EPS estimate used "
             "elsewhere, too rough for a turnover ratio)",
    "W-019": "news sentiment — no NLP feed",
    "W-020": "analyst estimate revisions — no feed",
    "W-021": "short interest — NSE feed not free (register agrees)",
    "W-022": "per-stock put/call volume — options chain not in repo",
    "X-05": "durability composite — filings-based components not in repo",
    "X-06": "free-float % — not in repo",
    "X-07": "institutional holder count — not in repo",
    "X-11": "estimate revisions — no feed",
    "X-19": "documented on page (H-24 preset live); exact replication is a "
            "research script order",
}

_STOCK_IDS = [
    "E-01", "E-02", "E-03", "E-04", "E-05", "E-06", "B-07",
    "H-11", "H-12", "H-14", "H-15", "H-16", "H-17", "H-18", "H-21", "H-22",
    "W-001", "W-002", "W-003", "W-004", "W-005", "W-006", "W-009", "W-010",
    "W-012", "W-013", "W-014", "W-015", "W-016", "W-017", "W-018",
    "X-01", "X-02", "X-03", "X-04", "X-09", "X-10", "X-12", "X-13", "X-14",
    "X-15", "X-16", "X-17", "X-18", "X-20",
] + list(_STOCK_DATA_NEEDED)


# ---------------------------------------------------------------------------
# Market-level items: PX panic signatures, FII stack, VIX-02, C-03
# ---------------------------------------------------------------------------

def _panel_series(panel: dict[str, pd.DataFrame], tail: int = 280
                  ) -> Optional[pd.DataFrame]:
    """Aligned close-price frame (dates x tickers) from per-stock OHLCV."""
    cols = {}
    for t, df in panel.items():
        try:
            nd = _normalize_ohlcv(df)
        except (KeyError, ValueError):
            continue
        s = nd["close"]
        if not isinstance(s.index, pd.DatetimeIndex):
            continue
        cols[t] = s.tail(tail)
    if len(cols) < 10:
        return None
    return pd.DataFrame(cols).dropna(how="all")


def market_view(panel: dict[str, pd.DataFrame],
                index_close: Optional[pd.Series] = None,
                vix_close: Optional[pd.Series] = None,
                bond_yield: Optional[pd.Series] = None,
                flows: Optional[pd.DataFrame] = None,
                pwoi: Optional[pd.DataFrame] = None,
                sectors: Optional[dict[str, str]] = None
                ) -> dict[str, Reading]:
    """PX-01..12, FII-01..09, VIX-02, C-03, W-007 market VRP.
    panel = {ticker: OHLCV df} (the scan bucket / stored-history sample)."""
    out: dict[str, Reading] = {}

    prices = _panel_series(panel)
    if prices is not None and len(prices) >= 40:
        rets = prices.pct_change()
        # daily breadth / dispersion / tail series over the loaded window
        up_pct5 = (prices.pct_change(5) > 0).mean(axis=1) * 100
        up_pct10 = (prices.pct_change(10) > 0).mean(axis=1) * 100
        disp5 = prices.pct_change(5).std(axis=1) * 100
        disp10 = prices.pct_change(10).std(axis=1) * 100
        r3 = prices.pct_change(3)
        tail_dn3_5 = (r3.rolling(5).min() <= -0.03).mean(axis=1) * 100
        tail_up3_3 = (r3.rolling(3).max() >= 0.03).mean(axis=1) * 100
        tail_dn3_3 = (r3.rolling(3).min() <= -0.03).mean(axis=1) * 100
        ew_ret5 = rets.mean(axis=1).rolling(5).sum() * 100

        def pref(series, q):
            s = series.dropna()
            return float(np.percentile(s, q)) if len(s) >= 60 else None

        ic = index_close.reindex(prices.index).ffill() if index_close is not None else None
        iret5 = (ic.iloc[-1] / ic.iloc[-6] - 1) * 100 if ic is not None and len(ic) > 6 else None
        iret10 = (ic.iloc[-1] / ic.iloc[-11] - 1) * 100 if ic is not None and len(ic) > 11 else None
        iret3 = (ic.iloc[-1] / ic.iloc[-4] - 1) * 100 if ic is not None and len(ic) > 4 else None
        ychg5 = None
        if bond_yield is not None and len(bond_yield) > 6:
            ychg5 = float(bond_yield.iloc[-1] - bond_yield.iloc[-6])

        A = "ASSUMED tail cut -3%/+3%, window-relative percentiles — see ASSUMED_CONSTANTS"
        out["PX-01"] = _dn("PX-01", "dbi (down-breadth index) never defined "
                                    "in the provided files")
        out["PX-05"] = _dn("PX-05", "dbi never defined in the provided files")
        if iret5 is not None:
            out["PX-02"] = Reading("PX-02", "FIRED" if
                                   tail_dn3_5.iloc[-1] >= 17.79 and iret5 > -2
                                   else "QUIET", float(tail_dn3_5.iloc[-1]), A)
            out["PX-04"] = Reading(
                "PX-04", "FIRED" if disp10.iloc[-6] and
                disp5.iloc[-1] / disp10.iloc[-6] >= 1.30 and abs(iret5) <= 1.25
                else "QUIET", float(disp5.iloc[-1]), A)
        if iret10 is not None:
            out["PX-03"] = Reading(
                "PX-03", "FIRED" if (up_pct10.iloc[-11] - up_pct10.iloc[-1])
                >= 15 and iret10 > -1 else "QUIET",
                float(up_pct10.iloc[-1]), A)
        if ychg5 is not None and iret5 is not None:
            out["PX-06"] = Reading(
                "PX-06", "FIRED" if ychg5 >= 0.095 and up_pct5.iloc[-1] < 45
                and iret5 < 0 else "QUIET", ychg5, A)
        p75d = pref(disp5, 75)
        if iret5 is not None and p75d is not None:
            out["PX-07"] = Reading(
                "PX-07", "FIRED" if up_pct5.iloc[-1] >= 58 and
                0 <= iret5 <= 1.25 and disp5.iloc[-1] < p75d else "QUIET",
                float(up_pct5.iloc[-1]), A)
        p90t = pref(tail_dn3_5, 90)
        if p90t is not None:
            out["PX-08"] = Reading(
                "PX-08", "FIRED" if tail_dn3_5.iloc[-6] >= p90t and
                up_pct5.iloc[-1] >= 68 and ew_ret5.iloc[-1] >= 1.25
                else "QUIET", float(ew_ret5.iloc[-1]), A)
            p25r = pref(pd.Series(prices.pct_change(10).mean(axis=1) * 100), 25)
            asym = tail_up3_3 - tail_dn3_3
            if iret10 is not None and p25r is not None:
                out["PX-09"] = Reading(
                    "PX-09", "FIRED" if asym.iloc[-2] < 0 <= asym.iloc[-1]
                    and iret10 < p25r else "QUIET", float(asym.iloc[-1]), A)
            out["PX-11"] = Reading(
                "PX-11", "FIRED" if tail_dn3_5.iloc[-1] >= p90t and
                up_pct5.iloc[-1] <= 35 and iret10 is not None and
                iret10 <= (p25r or -1e9) else "QUIET",
                float(tail_dn3_5.iloc[-1]), A)
        p25disp = pref(disp10, 25)
        if p25disp is not None and iret3 is not None:
            out["PX-10"] = Reading(
                "PX-10", "FIRED" if disp10.iloc[-1] <= p25disp and
                up_pct5.iloc[-1] >= 60 and iret3 > 0.75 else "QUIET",
                float(disp10.iloc[-1]), A)
        if ychg5 is not None and bond_yield is not None:
            ych_series = bond_yield.diff(5).dropna()
            p10y = pref(ych_series, 10)
            if p10y is not None:
                out["PX-12"] = Reading(
                    "PX-12", "FIRED" if ychg5 <= p10y and
                    (up_pct5.iloc[-1] - up_pct5.iloc[-6]) >= 12 else "QUIET",
                    float(up_pct5.iloc[-1] - up_pct5.iloc[-6]), A)
        # C-03 industry breadth
        if sectors:
            per = {}
            for t in prices.columns:
                sec = sectors.get(t)
                if not sec or len(prices[t].dropna()) < 200:
                    continue
                above = prices[t].iloc[-1] > prices[t].rolling(200).mean().iloc[-1]
                per.setdefault(sec, []).append(bool(above))
            rows = {s: sum(v) / len(v) for s, v in per.items() if len(v) >= 3}
            if rows:
                worst = min(rows, key=rows.get)
                best = max(rows, key=rows.get)
                out["C-03"] = Reading(
                    "C-03", "VALUE", float(np.mean(list(rows.values())) * 100),
                    f"{len(rows)} industries; best {best} "
                    f"{rows[best]*100:.0f}%, worst {worst} "
                    f"{rows[worst]*100:.0f}% (min 3 members each)")
            else:
                out["C-03"] = _na("C-03", "no industry with >=3 covered members")
        else:
            out["C-03"] = _na("C-03", "no sector map supplied")
    else:
        for rid in [f"PX-{i:02d}" for i in range(1, 13)] + ["C-03"]:
            out[rid] = _na(rid, "panel too small (<10 members / <40 bars)")

    # VIX items
    if vix_close is not None and len(vix_close) > 6:
        v = vix_close.dropna()
        spike1d = v.iloc[-1] / v.iloc[-2] - 1 if len(v) >= 2 else np.nan
        rise3d = v.iloc[-1] / v.iloc[-4] - 1 if len(v) >= 4 else np.nan
        frozen = spike1d > 0.20 or (len(v) >= 3 and v.iloc[-2] / v.iloc[-3] - 1 > 0.20)
        out["VIX-02"] = Reading(
            "VIX-02", "FIRED" if frozen else "QUIET", float(spike1d * 100),
            f"1d {spike1d*100:+.1f}% / 3d {rise3d*100:+.1f}%; freeze = 1d "
            ">20% (today or yesterday)")
        if prices is not None and len(prices) >= 40:
            realized = float(prices.pct_change().mean(axis=1)
                             .tail(21).std() * np.sqrt(252) * 100)
            value_ = float(v.iloc[-1] - realized)
            out["W-007"] = Reading(
                "W-007", "VALUE", value_,
                f"index VRP: VIX {v.iloc[-1]:.1f} - realized {realized:.1f} "
                "(panel EW realized vol; per-stock IV not in repo)")
    else:
        out["VIX-02"] = _na("VIX-02", "no VIX series")
        out.setdefault("W-007", _dn("W-007", "needs VIX series"))

    # FII stack
    if flows is not None and len(flows) >= 30:
        fii = pd.to_numeric(flows["fii_cash_net"], errors="coerce").dropna()
        s5 = fii.rolling(5).sum().dropna()
        if len(s5) >= 60:
            q10 = float(np.percentile(s5, 10))
            out["FII-01"] = Reading(
                "FII-01", "FIRED" if s5.iloc[-1] <= q10 else "QUIET",
                float(s5.iloc[-1]), f"5d sum vs q10 {q10:.0f} Cr "
                                    "(window = committed history)")
        neg10 = int((fii.tail(10) < 0).sum())
        out["FII-02"] = Reading("FII-02", "FIRED" if neg10 >= 7 else "QUIET",
                                float(neg10), "negative days in last 10")
        dii = pd.to_numeric(flows.get("dii_cash_net"), errors="coerce")
        dii5 = dii.dropna().tail(5)
        if len(dii5) == 5:
            out["FII-03"] = Reading(
                "FII-03", "FIRED" if fii.tail(5).sum() < 0 < dii5.sum()
                else "QUIET", float(dii5.sum()), "FII 5d < 0 AND DII 5d > 0")
        else:
            out["FII-03"] = _dn("FII-03", "DII empty after 2025-03 (NSDL DII "
                                          "archive is OTP-walled)")
    else:
        for rid in ("FII-01", "FII-02", "FII-03"):
            out[rid] = _na(rid, "flow history missing")

    if pwoi is not None and len(pwoi) >= 60:
        fii_rows = pwoi[pwoi["client_type"] == "FII"].set_index("date")
        cli_rows = pwoi[pwoi["client_type"] == "Client"].set_index("date")
        tot_rows = pwoi[pwoi["client_type"] == "TOTAL"].set_index("date")
        net_if = (fii_rows["fut_idx_long"].astype(float)
                  - fii_rows["fut_idx_short"].astype(float))
        q10 = float(np.percentile(net_if, 10))
        out["FII-04"] = Reading(
            "FII-04", "FIRED" if net_if.iloc[-1] < q10 else "QUIET",
            float(net_if.iloc[-1]),
            f"FII idx-fut net contracts vs rolling q10 {q10:.0f}")
        tot_if = (fii_rows["fut_idx_long"].astype(float)
                  + fii_rows["fut_idx_short"].astype(float))
        short_share = fii_rows["fut_idx_short"].astype(float) / tot_if
        q80 = float(np.percentile(short_share.dropna(), 80))
        out["FII-05"] = Reading(
            "FII-05", "FIRED" if short_share.iloc[-1] > q80 else "QUIET",
            float(short_share.iloc[-1] * 100),
            f"short share % vs q80 {q80*100:.1f}%")
        cli_tot = (cli_rows["fut_idx_long"].astype(float)
                   + cli_rows["fut_idx_short"].astype(float))
        cli_share = cli_rows["fut_idx_long"].astype(float) / cli_tot
        if len(cli_share) >= 6 and len(net_if) >= 6:
            crowd = (cli_share.iloc[-1] > cli_share.iloc[-6]
                     and net_if.iloc[-1] < net_if.iloc[-6] < 0)
            out["FII-06"] = Reading(
                "FII-06", "FIRED" if crowd else "QUIET",
                float(cli_share.iloc[-1] * 100),
                "client long share rising while FII net short deepens (5d)")
        pcr = ((tot_rows["opt_idx_put_long"].astype(float)
                + tot_rows["opt_idx_put_short"].astype(float))
               / (tot_rows["opt_idx_call_long"].astype(float)
                  + tot_rows["opt_idx_call_short"].astype(float)))
        if len(pcr) >= 21:
            avg20 = pcr.rolling(20).mean()
            jump = pcr.iloc[-1] / avg20.iloc[-2] - 1
            out["FII-07"] = Reading(
                "FII-07", "FIRED" if jump > 0.30 else "QUIET",
                float(pcr.iloc[-1]),
                f"PCR-OI {pcr.iloc[-1]:.2f}, {jump*100:+.1f}% vs 20d avg "
                "(TOTAL row, ASSUMED per register gap)")
    else:
        for rid in ("FII-04", "FII-05", "FII-06", "FII-07"):
            out[rid] = _dn(rid, "PWOI panel missing")
    # FII-08/09: owner ruled 2026-08-09 "why not both 0.213 and rolling q90"
    # — BOTH damage variants computed, side by side. Damage day per
    # trading/ifr.py: market tail_down3_5d (share of members with a <=-3%
    # 3-day move inside the last 5 days) above threshold; cluster = >=5 such
    # days in the last 10. Static variant: > 21.3% (IFR v0.4). Rolling
    # variant: > q90 of the loaded window (~250d page panel, not the
    # engine's full history — labelled).
    dmg_static = dmg_q90 = None
    if prices is not None and len(prices) >= 40:
        tail = (prices.pct_change(3).rolling(5).min() <= -0.03).mean(axis=1) * 100
        tail = tail.dropna()
        if len(tail) >= 60:
            q90 = float(np.percentile(tail, 90))
            dmg_static = int((tail.tail(10) > 21.3).sum()) >= 5
            dmg_q90 = int((tail.tail(10) > q90).sum()) >= 5
    if dmg_static is None:
        out["FII-08"] = _na("FII-08", "panel too small for the damage series")
        out["FII-09"] = _na("FII-09", "panel too small for the damage series")
    else:
        both = (f"damage[static 0.213]: {'YES' if dmg_static else 'no'} · "
                f"damage[rolling q90]: {'YES' if dmg_q90 else 'no'} "
                "(owner: both variants shown)")
        f01 = out.get("FII-01")
        if f01 is not None and f01.state in ("FIRED", "QUIET"):
            s8 = (dmg_static or dmg_q90) and f01.state == "FIRED"
            out["FII-08"] = Reading("FII-08", "FIRED" if s8 else "QUIET",
                                    None, both + " AND FII-01="
                                    + f01.state)
        else:
            out["FII-08"] = _na("FII-08", both + "; FII-01 unavailable")
        f04, f05 = out.get("FII-04"), out.get("FII-05")
        if f04 is not None and f05 is not None and \
                f04.state in ("FIRED", "QUIET") and f05.state in ("FIRED", "QUIET"):
            s9 = ((dmg_static or dmg_q90) and f04.state == "FIRED"
                  and f05.state == "FIRED")
            out["FII-09"] = Reading("FII-09", "FIRED" if s9 else "QUIET",
                                    None, both + f" AND FII-04={f04.state}"
                                    f" AND FII-05={f05.state}")
        else:
            out["FII-09"] = _na("FII-09", both + "; PWOI legs unavailable")

    for rid, why in _MARKET_DATA_NEEDED.items():
        out.setdefault(rid, _dn(rid, why))
    return out


_MARKET_DATA_NEEDED = {
    "W-025": "USDINR series not in repo",
    "W-026": "short-rate leg (1Y/3M) not in repo; 10Y alone is not a slope",
    "W-027": "credit-spread inputs not in repo",
    "H-19": "macro liquidity series not in repo",
}

# W-028..031 are portfolio-construction rules, not signals: evaluated on the
# page against the CURRENT book weights when supplied.


def portfolio_items(weights: dict[str, float],
                    vols: dict[str, float]) -> dict[str, Reading]:
    out: dict[str, Reading] = {}
    if weights and vols:
        common = [t for t in weights if t in vols and vols[t] > 0]
        if common:
            inv = {t: 1 / vols[t] for t in common}
            tot = sum(inv.values())
            rp = {t: inv[t] / tot for t in common}
            drift = sum(abs(rp[t] - weights.get(t, 0)) for t in common) / 2
            out["W-028"] = Reading("W-028", "VALUE", float(drift * 100),
                                   "distance of book from risk-parity "
                                   "weights, % (0 = at parity)")
        else:
            out["W-028"] = _na("W-028", "no vol data for held names")
    else:
        out["W-028"] = _na("W-028", "no book/vol supplied")
    out["W-029"] = _dn("W-029", "needs covariance matrix — research script "
                                "order, not a page metric")
    out["W-030"] = Reading("W-030", "QUIET", None,
                           "rule documented: sum|dw|/2 <= threshold; AM07 "
                           "has no rebalance-turnover cap today")
    out["W-031"] = Reading("W-031", "QUIET", None,
                           "AM07 sector caps = partial neutralization; "
                           "full-neutral not implemented")
    return out


# ---------------------------------------------------------------------------
# S-12/13/14: scan-quality telemetry over one pick row
# ---------------------------------------------------------------------------

_REQUIRED_FEATURES = ("momentum", "quality", "adv", "turnover",
                      "staleness", "correlation")


def scan_quality(components: dict[str, Optional[float]],
                 raw_score: Optional[float]) -> dict[str, Reading]:
    """S-13 coverage, S-14 effective score, S-12 decision state — computed
    from whichever component values the scan actually had. Display only."""
    present = sum(1 for k in _REQUIRED_FEATURES
                  if components.get(k) is not None)
    coverage = present / len(_REQUIRED_FEATURES)
    out = {"S-13": Reading("S-13", "VALUE", coverage * 100,
                           f"{present}/{len(_REQUIRED_FEATURES)} required "
                           "features observed")}
    if raw_score is None:
        out["S-14"] = _na("S-14", "no raw score")
        out["S-12"] = Reading("S-12", "VALUE", None, "UNKNOWN (incomplete)")
    else:
        eff = raw_score * coverage
        out["S-14"] = Reading("S-14", "VALUE", eff,
                              f"raw {raw_score:.1f} x coverage "
                              f"{coverage:.2f} — discounted, never inflated")
        state = ("UNKNOWN" if coverage < 1.0 else
                 "SELECT" if eff >= 62 else "REJECT")
        out["S-12"] = Reading("S-12", "VALUE", None,
                              f"{state} (SELECT floor = live cfg 62)")
    return out


# ---------------------------------------------------------------------------
# Loaders for the page (committed files only — zero network)
# ---------------------------------------------------------------------------

def load_market_inputs() -> dict:
    """Index/VIX/bond/flows/PWOI from the committed CSVs."""
    out: dict = {}
    try:
        idx = pd.read_csv(_INDEX_CSV, usecols=["trade_date", "nifty_close",
                                               "india_vix_close"])
        idx["trade_date"] = pd.to_datetime(idx["trade_date"], dayfirst=True,
                                           format="mixed")
        idx = idx.set_index("trade_date").sort_index()
        out["index"] = idx["nifty_close"].dropna()
        out["vix"] = idx["india_vix_close"].dropna()
    except Exception as e:                                    # noqa: BLE001
        log.warning("register_v4: index csv load failed: %s", e)
    try:
        bond = pd.read_csv(_BOND_CSV)
        bond.columns = [c.strip().strip('"').strip("﻿") for c in bond.columns]
        # investing.com export: US-format MM/DD/YYYY dates, newest first
        bond["Date"] = pd.to_datetime(bond["Date"], format="%m/%d/%Y",
                                      errors="coerce")
        out["bond"] = (bond.dropna(subset=["Date"]).set_index("Date")["Price"]
                       .astype(float).sort_index())
    except Exception as e:                                    # noqa: BLE001
        log.warning("register_v4: bond csv load failed: %s", e)
    try:
        out["flows"] = pd.read_csv(_FII_CSV)
    except OSError:
        pass
    try:
        out["pwoi"] = pd.read_csv(_PWOI_CSV)
    except OSError:
        pass
    return out
