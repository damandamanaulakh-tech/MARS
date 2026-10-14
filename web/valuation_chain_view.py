"""Valuation chain on the MARS Den (§12) — the web half of sequence stages
07-18 (owner order 2026-10-14: "lets hit the formulas work completion").

trading/valuation/* holds the arithmetic (pure). This module gathers every
input from STORED state only — no network on a page render:

  * closes: web.candle_chart.load_ohlcv (Mongo store, repo zip) — today's
    price, the price at each FY end (own P/E history), the price at the
    snapshot's period end;
  * index levels: the committed index file + the stored tail
    (web.valuation_view._index_field) for the Sensex / Nifty legs of the
    owner's P/E bridge;
  * peers: the median trailing P/E of the SAME ROUTE among the Nifty-200
    names with statements — ASSUMED until the owner gives peer groups;
    computed once per process per day (200 zip reads);
  * which names to show: the typed one, the §11 stored targets, held names.

Macro rows of the P/E bridge the app does not store (repo, CPI, GDP,
Brent) are passed as 0 and listed as DATA_NEEDED on the row — the sheet
treats them as what-if inputs, so a missing one must not blank the price.
Nothing here is read by the money path.
"""
from __future__ import annotations

import logging
import statistics
from datetime import date
from typing import Any, Optional

import pandas as pd

from trading import valuation as V
from trading import valuation_context as vc

from . import valuation_view as vv

log = logging.getLogger(__name__)

MAX_ROWS = 25
PRICE_TAIL = 1500                        # ~6 years of daily closes
KEY_TERMS = ("P/E (trailing, forward)", "P/B", "EV/EBITDA", "ROE", "ROCE",
             "FCF / FCF yield", "Cash conversion", "Net debt / EBITDA",
             "Interest coverage", "Dividend yield / payout", "Graham number",
             "Revenue growth")

_PEER_CACHE: dict = {"day": None, "by_method": {}}


# ------------------------------------------------------------- inputs

def _closes(ticker: str) -> Optional[pd.Series]:
    from .candle_chart import load_ohlcv
    df = load_ohlcv(ticker, tail=PRICE_TAIL)
    if df is None or df.empty or "Close" not in df.columns:
        return None
    s = df["Close"].dropna()
    s.index = pd.to_datetime(s.index)
    return s if len(s) else None


def price_now(closes: Optional[pd.Series]) -> tuple[Optional[float], str]:
    if closes is None or not len(closes):
        return None, ""
    return float(closes.iloc[-1]), closes.index[-1].date().isoformat()


def price_at(closes: Optional[pd.Series], when: Any) -> Optional[float]:
    """Close on or before `when` (None when the series starts later)."""
    if closes is None or not len(closes) or not when:
        return None
    try:
        ts = pd.Timestamp(when)
    except (TypeError, ValueError):
        return None
    s = closes[closes.index <= ts]
    return float(s.iloc[-1]) if len(s) else None


def index_move(field: str, start: Any, end: Any = None) -> dict:
    """{start, end, start_date, end_date} levels of one index column."""
    s = vv._index_field(field)
    if s is None or not len(s):
        return {"start": None, "end": None}
    s = s.dropna()
    a = price_at(s, start)
    b = float(s.iloc[-1]) if end is None else price_at(s, end)
    return {"start": a, "end": b,
            "end_date": s.index[-1].date().isoformat() if end is None else str(end)[:10]}


def own_pe_history(closes: Optional[pd.Series], snap: dict) -> list[float]:
    """FY-end close ÷ that FY's basic EPS, for every FY in the snapshot
    history — the stock's own multiple range (needs the long tail)."""
    out: list[float] = []
    eps_hist = ((snap.get("history") or {}).get("basic_eps") or {})
    for period_end, eps in sorted(eps_hist.items()):
        e = V.num(eps)
        px = price_at(closes, period_end)
        if e and e > 0 and px:
            out.append(px / e)
    return out


def _held_names(p) -> list[str]:
    """Held tickers. `Portfolio.positions` is a dict keyed by ticker; a
    list of position objects is accepted too (fixtures)."""
    held = getattr(p, "positions", None) or {}
    try:
        if isinstance(held, dict):
            return [str(k) for k in held.keys()]
        return [getattr(x, "ticker", str(x)) for x in held]
    except Exception:                                   # noqa: BLE001
        return []


def names_to_show(p, typed: str = "") -> list[str]:
    """Typed first, then the §11 stored targets, then held names; unique;
    capped at MAX_ROWS."""
    out: list[str] = []
    if typed:
        out.append(typed)
    try:
        doc = vv.load_doc()
        for t in sorted((doc.get("targets") or {}).keys()):
            if t not in out:
                out.append(t)
    except Exception:                                   # noqa: BLE001
        pass
    for t in _held_names(p):
        if t not in out:
            out.append(t)
    return out[:MAX_ROWS]


# --------------------------------------------------------------- peers

def peer_multiples(method: str, exclude: str = "") -> dict:
    """Median trailing multiples (P/E, P/B, EV/EBITDA, EV/Sales) of the
    names on the same route among the Nifty-200 statement set. ASSUMED
    peer definition — the owner has not supplied peer groups. One pass per
    process per day; every name's price from the store."""
    from trading.valuation import pipeline
    today = date.today().isoformat()
    if _PEER_CACHE["day"] != today:
        _PEER_CACHE["day"] = today
        _PEER_CACHE["by_method"] = {}
        try:
            _PEER_CACHE["by_method"] = pipeline.peer_table(
                price_of=lambda t: price_now(_closes(t))[0],
                sector_map=_sector_map(), company_names=_company_names())
        except Exception:                               # noqa: BLE001
            log.exception("valuation chain: peer table failed")
    rows = [r for r in (_PEER_CACHE["by_method"].get(method) or [])
            if r.get("ticker") != exclude]

    def _med(key: str) -> Optional[float]:
        xs = [r[key] for r in rows if V.num(r.get(key)) and r[key] > 0]
        return statistics.median(xs) if xs else None

    return {"n": len(rows), "pe_median": _med("pe"), "pb_median": _med("pb"),
            "ev_ebitda_median": _med("ev_ebitda"), "ev_sales_median": _med("ev_sales"),
            "source": f"median of {len(rows)} same-route Nifty-200 names (ASSUMED peers)"}


def _sector_map() -> dict:
    try:
        from . import state
        return dict(getattr(state.cfg(), "sector_map", {}) or {})
    except Exception:                                   # noqa: BLE001
        return {}


_NAMES_CACHE: dict = {}


def _company_names() -> dict:
    """ticker → company name from the committed universe file
    (data/upstox_nse_equities.csv: ticker,trading_symbol,name,…) — the
    route's keyword rules read the name. Loaded once per process."""
    if _NAMES_CACHE:
        return _NAMES_CACHE
    import csv
    try:
        with open(vv._ROOT / "data" / "upstox_nse_equities.csv", newline="",
                  encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = (r.get("ticker") or "").strip().upper()
                if t:
                    _NAMES_CACHE[t] = (r.get("name") or "").strip()
    except OSError:
        log.warning("valuation chain: universe file unreadable (names)")
    return _NAMES_CACHE


# ---------------------------------------------------------------- rows

def _macro_inputs(snap: dict, closes: Optional[pd.Series] = None) -> dict:
    """The owner's P/E-bridge market inputs from STORED state: Sensex /
    Nifty levels at the period end and now, VIX, the 10-year yield (rf),
    the stock's measured β on Nifty 50, USDINR now and at the period end.
    Repo / CPI / GDP / Brent are not stored — the pipeline counts them 0
    and lists them DATA_NEEDED."""
    from trading.valuation import snapshot as S
    period_end = snap.get("period_end")
    nifty = index_move("nifty_close", period_end)
    sensex = index_move("sensex_close", period_end)
    vix = vv._index_field("india_vix_close")
    bond = vv._bond_series()
    out: dict = {
        "sensex_start": sensex.get("start"), "sensex_end": sensex.get("end"),
        "nifty_start": nifty.get("start"), "nifty_end": nifty.get("end"),
        "vix": float(vix.dropna().iloc[-1]) if vix is not None and len(vix.dropna()) else None,
        "rf_pct": float(bond.dropna().iloc[-1]) if bond is not None and len(bond.dropna()) else None,
        "beta": None, "usdinr_now": None, "usdinr_base": None,
        "price_now": price_now(closes)[0] if closes is not None else None,
    }
    try:
        fx_now = S.usdinr_at(date.today().isoformat())
        fx_base = S.usdinr_at(period_end) if period_end else None
        out["usdinr_now"] = fx_now[0] if fx_now else None
        out["usdinr_base"] = fx_base[0] if fx_base else None
    except Exception:                                   # noqa: BLE001
        pass
    try:
        nifty_s = vv._index_field("nifty_close")
        if closes is not None and nifty_s is not None:
            b = vc.beta(closes, nifty_s)
            out["beta"] = b.get("beta") if isinstance(b, dict) else V.num(b)
    except Exception:                                   # noqa: BLE001
        pass
    return out


def row_for(ticker: str, cfg, p, *, show_terms: bool = False) -> dict:
    from trading.valuation import pipeline
    closes = _closes(ticker)
    px, px_date = price_now(closes)
    rec = pipeline.evaluate(
        ticker, price=px, price_date=px_date,
        sector_map=dict(getattr(cfg, "sector_map", {}) or {}),
        company_name=_company_names().get(ticker, ""),
        price_at=lambda when: price_at(closes, when),
        own_pe_history=lambda snap: own_pe_history(closes, snap),
        peers=peer_multiples,
        macro=lambda snap: _macro_inputs(snap, closes),
    )
    rec["key_terms"] = [t for t in rec.get("terms_rows", []) if t["name"] in KEY_TERMS]
    rec["all_terms"] = rec.get("terms_rows", []) if show_terms else []
    rec["n_terms"] = len(rec.get("terms_rows", []))
    return rec


def empty_section(error: str = "", typed: str = "") -> dict:
    from trading.valuation import pipeline
    return {"rows": [], "n": 0, "max_rows": MAX_ROWS, "typed": typed,
            "typed_full": vv.normalise_symbol(typed) if typed else "",
            "show_terms": False, "error": error,
            "n_statements": pipeline.statement_coverage(),
            "assumed": pipeline.assumed_for_page(),
            "today": date.today().isoformat()}


def section(cfg, p, typed: str = "", show_terms: bool = False) -> dict:
    out = empty_section("", typed)
    typed_full = ""
    if typed:
        typed_full = vv.normalise_symbol(typed)
        if not typed_full:
            out["error"] = f"'{typed}' is not a symbol"
    out["typed_full"] = typed_full
    out["show_terms"] = bool(show_terms and typed_full)
    rows = []
    for t in names_to_show(p, typed_full):
        try:
            rows.append(row_for(t, cfg, p, show_terms=(out["show_terms"] and t == typed_full)))
        except Exception as e:                          # noqa: BLE001
            log.exception("valuation chain: row failed for %s", t)
            from trading.valuation import pipeline
            rows.append(pipeline.failed_record(t, str(e)[:120]))
    out.update({"rows": rows, "n": len(rows)})
    return out
