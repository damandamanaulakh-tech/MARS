"""§12 market legs, currency leg through EPS and sector totals — the web half
of VALFIX batch 3 (owner choices 2026-10-21). The arithmetic is pure, in
trading/valuation/market_legs.py, currency.py and sector_total.py; this
module gathers every input from STORED state only (no network on a render):

  * Sensex / Nifty / VIX: the committed index file + the stored tail
    (valuation_chain_view._index_series, read once per render);
  * the stock's NSE sector index: the stored sector rows (16 + the six
    indices added 2026-10-21), mapped by valuation_context.index_for —
    strict industry rule A plus the owner's six;
  * FRED S&P 500 / Nasdaq / Dow / USD-INR / USD-per-EUR and NSE's sector
    P/E / P/B: Mongo macro_daily, through the render's database gate, each
    dated (a series never stored reads DATA_NEEDED, never an old value as
    fresh);
  * the 10-year yield: the committed June file joined to the stored
    Upstox G-sec yields after it (both labelled);
  * the owner's tables: the inputs table (valuation_inputs: typed USD / EUR
    share, typed selection date) and the per-sector currency table
    (settings doc valuation_sector_fx);
  * the sector totals' FY history and 52-week high: built at most once a
    day from the repo price zip + ONE batched store read, STORED in
    settings doc valuation_sector_totals.

Display only — no gate, stop or size reads any of it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from trading import valuation_context as vc
from trading.valuation import num

from . import valuation_chain_view as cv
from . import valuation_view as vv

log = logging.getLogger(__name__)

SECTOR_FX_DOC_ID = "valuation_sector_fx"
LAST_SECTOR_FX: dict = {}
TOTALS_DOC_ID = "valuation_sector_totals"
TOTALS_VERSION = 1
_TOTALS: dict = {}
_TOTALS_LOCK = threading.Lock()
#: Days of stored closes the 52-week high reads (252 sessions + margin).
HIGH_ROWS = 262
#: Names per zip read while collecting FY-end closes (memory cap; measured
#: 2026-10-21: the whole build 1.3 s, no rise in peak memory).
ZIP_CHUNK = 25
#: ASSUMED: an NSE P/E reading older than this many days prints STALE — the
#: same 10 days batch 2's feeds use (macro_feeds.STALE_DAYS).
NSE_STALE_DAYS = 10
READ_FAILED = ("the stored sector currency table could not be read (database) — nothing "
               "changed; reload the Den and save again when the store answers")


# ------------------------------------------------------- render-scoped cache

def _rcache() -> dict:
    r = cv._render()
    if r is None:
        return {}
    c = getattr(r, "b3", None)
    if c is None:
        c = {}
        r.b3 = c
    return c


def _points_series(name: str) -> Optional[pd.Series]:
    """A stored macro series as a Series (date index), through the feed
    cache and this render's database gate; None = never stored / no feed."""
    pts = cv._feed("series_points", name)
    if not pts:
        return None
    try:
        s = pd.Series([float(v) for _d, v in pts], index=pd.to_datetime([d for d, _v in pts]))
    except (TypeError, ValueError):
        return None
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s if len(s) else None


def _sector_series(index_name: str, since: str) -> Optional[pd.Series]:
    """The stored closes of one NSE sector index, the table read once per
    render (through the gate) from `since`."""
    rc = _rcache()
    key = ("sector_table", since)
    if key not in rc:
        g = cv._gate()
        rc[key] = (g.call("sector index read", vv._sector_table, since, default={})
                   if not g.down else {}) or {}
    s = rc[key].get(index_name)
    return None if s is None else s.copy()


def _bond_series() -> tuple[Optional[pd.Series], str]:
    """The committed 10-year file (ends 2026-06) joined to the stored Upstox
    G-sec yields after its last date. (series, source text)."""
    base = vv._bond_series()
    feed = _points_series("gsec10y_yield")
    parts = []
    if base is not None and len(base.dropna()):
        base = base.dropna()
        parts.append(f"committed 10-year file to {base.index[-1].date().isoformat()}")
    else:
        base = None
    if feed is not None:
        if base is not None:
            feed = feed[feed.index > base.index[-1]]
        if len(feed):
            parts.append(f"Upstox G-sec yields stored {feed.index[0].date().isoformat()} → "
                         f"{feed.index[-1].date().isoformat()}")
    pieces = [x for x in (base, feed) if x is not None and len(x)]
    if not pieces:
        return None, ""
    s = pd.concat(pieces).sort_index()
    return s[~s.index.duplicated(keep="last")], " + ".join(parts)


def _sentiment(cfg, p) -> dict:
    rc = _rcache()
    if "sentiment" not in rc:
        try:
            vix = cv._index_series("india_vix_close")
            si = vv.sentiment_inputs(cfg, p, vix)
            rc["sentiment"] = vc.sentiment_margin(si.get("vix"), si.get("breadth_pct"),
                                                  float(si.get("breadth_low_pct", 40.0)),
                                                  si.get("fii_5d_net_cr"),
                                                  si.get("damage_q90_on"))
        except Exception:                                   # noqa: BLE001
            log.warning("valuation market legs: sentiment inputs failed")
            rc["sentiment"] = vc.sentiment_margin()
    return rc["sentiment"]


# ------------------------------------------------- sector currency table

def sector_names() -> list[str]:
    from trading.valuation import route as R
    return [str(r.get("sector") or "") for r in R.SECTOR_METHODS if r.get("sector")]


def _read_sector_fx() -> tuple[dict, bool]:
    """({sector: row}, ok). ok False = the read did not happen or failed — a
    save never builds on that (the inputs-table rule, reviewer defect 5)."""
    from . import db
    g = cv._gate()
    if g.down:
        return {}, False
    doc = g.call("sector currency table read", db.load_settings, doc_id=SECTOR_FX_DOC_ID,
                 default=None)
    if g.down or doc is None:
        return {}, False
    tbl = doc.get("sectors") if isinstance(doc, dict) else None
    return ({str(k): dict(v) for k, v in (tbl or {}).items() if isinstance(v, dict)}, True)


def load_sector_fx() -> dict:
    table, ok = _read_sector_fx()
    if not ok and LAST_SECTOR_FX:
        return {k: dict(v) for k, v in LAST_SECTOR_FX.items()}
    return table


def save_sector_fx(table: dict) -> tuple[bool, str]:
    from datetime import datetime
    from . import db
    _old, ok = _read_sector_fx()
    if not ok:
        return False, READ_FAILED
    LAST_SECTOR_FX.clear()
    LAST_SECTOR_FX.update(table)
    try:
        db.save_settings({"sectors": dict(table), "ts": datetime.utcnow().isoformat()},
                         doc_id=SECTOR_FX_DOC_ID)
    except Exception:                                       # noqa: BLE001
        log.exception("valuation: sector currency table save failed (in-memory copy kept)")
        return False, "the database refused the write — the table is kept in memory only"
    cv.clear_row_cache()
    return True, ""


def parse_sector_fx_form(form) -> tuple[dict, list]:
    """The Den form (fx_usd_<i>, fx_eur_<i>, fx_pp_<i> per route sector) →
    (table, errors). `loaded` must be 1 — a form printed while the table
    could not be read would save blanks over the owner's numbers."""
    from trading.valuation import currency as CUR
    if str(form.get("loaded") or "") != "1":
        return {}, ["the table could not be read when this page was opened — reload the "
                    "Den before saving (nothing was changed)"]
    raw = {}
    for i, sec in enumerate(sector_names()):
        raw[sec] = {"usd_share_pct": form.get(f"fx_usd_{i}"),
                    "eur_share_pct": form.get(f"fx_eur_{i}"),
                    "pass_through_pp": form.get(f"fx_pp_{i}")}
    return CUR.parse_sector_table(raw)


def sector_fx_section(flag: str = "", error: str = "") -> dict:
    from trading.valuation import currency as CUR
    table, ok = _read_sector_fx()
    if not ok and LAST_SECTOR_FX:
        table, ok = {k: dict(v) for k, v in LAST_SECTOR_FX.items()}, False
    rows = []
    for i, sec in enumerate(sector_names()):
        r = table.get(sec) or {}
        rows.append({"i": i, "sector": sec, "usd_share_pct": r.get("usd_share_pct"),
                     "eur_share_pct": r.get("eur_share_pct"),
                     "pass_through_pp": r.get("pass_through_pp")})
    return {"flag": flag or "", "error": error, "rows": rows, "loaded": bool(ok),
            "doc_id": SECTOR_FX_DOC_ID, "n_set": sum(1 for r in rows if any(
                r[k] is not None for k in ("usd_share_pct", "eur_share_pct", "pass_through_pp"))),
            "pp_default": CUR.ASSUMED["pass_through_pp"]["value"],
            "share_bounds": CUR.SHARE_BOUNDS, "pp_bounds": CUR.PASS_THROUGH_BOUNDS}


def empty_sector_fx_section(flag: str = "", error: str = "") -> dict:
    return {"flag": flag, "error": error, "rows": [], "loaded": False,
            "doc_id": SECTOR_FX_DOC_ID, "n_set": 0, "pp_default": 0.25,
            "share_bounds": (-100.0, 100.0), "pp_bounds": (0.0, 5.0)}


# ----------------------------------------------------------- remap list

_REMAP_CACHE: dict = {}


def remapped_names(sector_map: Optional[dict]) -> dict:
    """Every name whose NSE sector index changed on 2026-10-21 (strict
    industry rule A + the owner's six indices + the app's Chemicals label):
    {n, rows: [{ticker, company, industry, old, new, rule}]}, over the
    Nifty-500 industry file and every labelled name in cfg.sector_map."""
    from trading.valuation import route as R
    sm = dict(sector_map or {})
    sig = hashlib.sha1(json.dumps(sm, sort_keys=True, default=str).encode()).hexdigest()
    if _REMAP_CACHE.get("sig") == sig:
        return _REMAP_CACHE["out"]
    rows = []
    try:
        look = R.load_nifty500_industry()
    except Exception:                                       # noqa: BLE001
        look = {}
    names = dict(look)
    for t in sm:                              # labelled names outside the file too
        b = cv._bare_symbol(t)
        names.setdefault(b, {"company": "", "industry": ""})
    for sym, info in sorted(names.items()):
        t = f"{sym}.NS"
        new = vc.index_for(t, sm)
        old = vc.label_only_index(t, sm)
        if new["index"] != old:
            rows.append({"ticker": sym, "company": (info or {}).get("company") or "",
                         "industry": (info or {}).get("industry") or "",
                         "old": f"Nifty {old}" if old else vc.MARKET_INDEX_LABEL,
                         "new": new["label"], "rule": new["rule"]})
    rows.sort(key=lambda r: (r["new"], r["ticker"]))
    out = {"n": len(rows), "rows": rows}
    _REMAP_CACHE.update(sig=sig, out=out)
    return out


# ------------------------------------------------------------- the legs

def _verdict_bands(overrides: Optional[dict]) -> Optional[dict]:
    try:
        from trading.valuation import assumptions as ASM
        return ASM.split(overrides)["verdict"] or None
    except Exception:                                       # noqa: BLE001
        return None


def _range_draft(overrides: Optional[dict]) -> dict:
    try:
        from trading.valuation import assumptions as ASM
        return ASM.split(overrides)["range"] or {}
    except Exception:                                       # noqa: BLE001
        return {}


def sector_fx_for_render() -> dict:
    """The sector currency table, read once per render."""
    rc = _rcache()
    if "sector_fx" not in rc:
        rc["sector_fx"] = load_sector_fx()
    return rc["sector_fx"]


def _fred_stale(move: dict, series: str, asof: Optional[str] = None) -> dict:
    """Mark a FRED move whose newest reading is older than the feed's stale
    rule (macro_feeds.FRED_STALE_DAYS: 7 days daily, 14 days weekly),
    counted back from the row's own "now" (its price date — the move ends
    there on purpose), else from today."""
    from trading import macro_feeds as mf
    if (move or {}).get("status") != "OK":
        return move
    try:
        ref = date.fromisoformat(str(asof)[:10]) if asof else None
    except ValueError:
        ref = None
    age = cv._age_days(move.get("end_date"), ref)
    lim = mf.FRED_STALE_DAYS[mf.fred_kind(series)]
    return {**move, "age_days": age, "stale": bool(age is None or age > lim), "stale_days": lim}


def market_block(ticker: str, rec: dict, closes: Optional[pd.Series], cfg, p,
                 mac: dict, overrides: Optional[dict], owner_row: Optional[dict],
                 sector_fx: Optional[dict], selection: Optional[dict] = None,
                 pushed: Optional[dict] = None) -> dict:
    """§12's market-adjusted target with every leg, the currency leg and
    the global lines (counted 0), for one computed record. `selection` =
    the date the row's own target was valued at (row_for reads it once, so
    the P/E bridge and these legs share it); not handed in, it is read
    here the same way."""
    from trading.valuation import currency as CUR
    from trading.valuation import market_legs as ML
    owner_row = owner_row or {}
    sym = cv._bare_symbol(ticker)
    sel = selection or ML.selection_date(rec.get("snapshot"), owner_row.get("selection_date"),
                                         pushed=pushed)
    out: dict = {"status": "OK", "why": "", "selection": sel,
                 "bridge_end": (mac or {}).get("index_end_date")
                 if (mac or {}).get("index_end_is_selection") else None}
    if not sel.get("date"):
        out.update(status="DATA_NEEDED", why=sel.get("why") or "no selection date")
        return out
    sd = sel["date"]
    now_d = rec.get("price_date") or None
    smap = dict(getattr(cfg, "sector_map", {}) or {})
    idx = vc.index_for(ticker, smap)
    out["index"] = idx

    # --- Sensex leg
    sensex = cv._index_series("sensex_close")
    s_move = vc.move_between(sensex, sd, now_d)
    out["sensex"] = s_move

    # --- sector leg
    since = min(vv._default_sector_since(),
                (date.fromisoformat(sd) - timedelta(days=vv.SECTOR_MARGIN_DAYS)).isoformat())
    sector = _sector_series(idx["index"], since) if idx.get("index") else None
    if idx.get("index"):
        sec_move = vc.move_between(sector, sd, now_d) if sector is not None else {
            "status": "DATA_NEEDED", "pct": None,
            "why": (f"no stored rows for {idx['label']} yet — the 09:20 / 15:35 sector "
                    "refresh and its backfill fill them")}
    else:
        sec_move = {"status": "MARKET", "pct": None, "why": idx.get("why") or ""}
    out["sector"] = sec_move
    reg = ML.regress(closes, sensex, sector if idx.get("index") else None, end=now_d)
    out["reg"] = reg

    # --- rates leg (the selection date's 10-year yield → now)
    own = num((rec.get("range") or {}).get("base"))
    bond, bond_src = _bond_series()
    r_then = vc.close_at(bond, sd)
    r_now = num(mac.get("rf_pct"))
    now_src = (f"{'Upstox G-sec' if mac.get('rf_feed') else 'June file'} "
               f"{mac.get('rf_date') or ''}").strip()
    if r_now is None and bond is not None and len(bond):
        r_now, now_src = float(bond.iloc[-1]), f"{bond.index[-1].date().isoformat()}"
    rd = _range_draft(overrides)
    erp = float(rd.get("equity_risk_premium_pct", vc.ASSUMED["equity_risk_premium_pct"]))
    growth = float(rd.get("long_run_growth_pct", vc.ASSUMED["long_run_growth_pct"]))
    rl = vc.rates_leg(own, r_then[1] if r_then else None, r_now, erp, growth)
    rl["rate_then_date"] = r_then[0].isoformat() if r_then else None
    rl["rate_now_source"] = now_src if r_now is not None else ""
    rl["rate_now_stale"] = bool(mac.get("rf_stale"))
    rl["series_source"] = bond_src
    if r_then is None:
        rl["why"] = (f"no 10-year yield on file at {sd}" if bond is not None
                     else "no 10-year yield series")
    out["rates"] = rl

    # --- currency through EPS (every sector)
    cb = rec.get("currency_base") or {}
    sector_name = cb.get("sector") or (rec.get("route") or {}).get("sector") or ""
    shares = CUR.resolve_shares(sym, owner_row, sector_name, sector_fx)
    pp, pp_src = CUR.pass_through(sector_name, sector_fx)
    moves = CUR.fx_moves(_points_series("fred_usdinr"), _points_series("fred_usdeur"), sd,
                         end=now_d)
    moves = {"usd": _fred_stale(moves.get("usd") or {}, "fred_usdinr", now_d),
             "eur": _fred_stale(moves.get("eur") or {}, "fred_usdeur", now_d)}
    fx = CUR.fx_eps(cb.get("eps"), cb.get("ebitda_margin_pct"), pp, shares.get("usd_pct"),
                    shares.get("eur_pct"), (moves.get("usd") or {}).get("pct"),
                    (moves.get("eur") or {}).get("pct"))
    if cb.get("margin_why") and fx.get("status") != "OK":
        fx["margin_why"] = cb["margin_why"]
    # batch 2's RBI reference rate, printed beside — the leg reads FRED
    rbi = {"rate": num(mac.get("usdinr_now")), "date": mac.get("usdinr_now_date"),
           "source": str(mac.get("usdinr_now_source") or "")}
    out["fx"] = {**fx, "shares": shares, "pass_through_pp": pp, "pass_through_source": pp_src,
                 "moves": moves, "sector": sector_name,
                 "margin_pct": cb.get("ebitda_margin_pct"),
                 "rbi": rbi if rbi["rate"] is not None and rbi["source"].startswith("RBI")
                 else None,
                 "lag_note": "Fed H.10 weekly — 3 to 10 days behind, New York noon rates"}

    # --- the targets. A price dated before the selection date leaves no
    # move to apply (the legs would run backwards) — the own target stands.
    price_before_sel = bool(now_d and str(now_d)[:10] < sd)
    if price_before_sel:
        out["price_before_selection"] = (f"the latest price ({str(now_d)[:10]}) is older than "
                                         f"the selection date ({sd}) — nothing to adjust yet")
    # a factor of exactly 0 is a real (if extreme) answer — never read as "missing"
    fxf = num(fx.get("factor"))
    legs = ML.market_adjust(own, sensex_move=s_move, sector_move=sec_move,
                            sector_is_market=not idx.get("index"), reg=reg, rates=rl,
                            fx_factor=fxf if fxf is not None else 1.0,
                            dated_today=bool(sel.get("dated_today")) or price_before_sel)
    out["legs"] = legs
    rng = dict(rec.get("range") or {})
    rng["family"] = rng.get("family") or (rec.get("route") or {}).get("family")
    vd = ML.verdicts(rec.get("price"), rng, legs, bands=_verdict_bands(overrides))
    out["verdicts"] = vd
    col = vd.get("now_column")
    if legs.get("status") == "SAME_DAY":
        out["target_now"] = own
        out["verdict_now"] = (rec.get("verdict") or {}).get("verdict")
        out["now_column"] = "own"
    elif col:
        out["target_now"] = legs[col]["target"]
        out["verdict_now"] = vd[col]["verdict"]
        out["now_column"] = col
    else:
        out["target_now"] = None
        out["verdict_now"] = None
        out["now_column"] = None
    sm = _sentiment(cfg, p)
    out["sentiment"] = {"discount_pct": sm.get("discount_pct"), "n_on": sm.get("n_on"),
                        "n_unknown": sm.get("n_unknown"),
                        "gauges": [{"name": g["name"], "state": g["state"]}
                                   for g in sm.get("gauges") or []]}
    tn = num(out.get("target_now"))
    out["buy_below"] = (tn * (1.0 - float(sm["discount_pct"]) / 100.0)
                        if tn is not None and tn > 0 else None)
    if tn is not None and tn > 0 and num(rec.get("price")):
        out["target_now_vs_price_pct"] = (tn / float(rec["price"]) - 1.0) * 100.0

    # --- the US legs, printed and counted 0
    is_it = str(sector_name).startswith("IT services") or idx.get("index") == "IT"
    gm = {k: _fred_stale(ML.us_move(_points_series(f"fred_{k}"), sd,
                                    now_d or date.today().isoformat()), f"fred_{k}", now_d)
          for k in ("sp500", "nasdaq", "dow")}
    out["us_lines"] = ML.global_lines(gm, s_move, is_it)
    return out


# --------------------------------------------------------- sector totals

def _closes_at_periods(s: pd.Series, periods: list) -> dict:
    out = {}
    for per in periods:
        try:
            ts = pd.Timestamp(per)
        except (TypeError, ValueError):
            continue
        part = s[s.index <= ts]
        # within 10 days of the period end, never a stale older close
        if len(part) and (ts - part.index[-1]).days <= 10:
            out[per] = float(part.iloc[-1])
    return out


def _build_totals(base: list) -> tuple[dict, bool]:
    """({method: {"fy": fy_history (no 'now'), "high": high_52w}}, store_read)
    for the day. FY-end closes from the repo zip (one chunk at a time), the
    names the zip lacks and the 52-week window from ONE batched store read
    each, through the render's gate (a closed gate = those parts
    DATA_NEEDED). store_read False = the 52-week window was not read."""
    from trading.history_store import records_to_df
    from trading.valuation import sector_total as ST
    from . import db
    names = [b["ticker"] for b in base if b.get("ticker")]
    periods = {b["ticker"]: sorted((b.get("fy") or {}).keys()) for b in base}
    fy_closes: dict = {}
    for i in range(0, len(names), ZIP_CHUNK):
        chunk = names[i:i + ZIP_CHUNK]
        for t, df in cv._zip_frames(chunk).items():
            try:
                s = pd.to_numeric(df["Close"], errors="coerce").dropna()
                s.index = pd.to_datetime(s.index)
                fy_closes[t] = _closes_at_periods(s, periods.get(t) or [])
            except Exception:                               # noqa: BLE001
                continue
    g = cv._gate()
    missing = [t for t in names if not fy_closes.get(t)]
    oldest = min((min(v) for v in periods.values() if v), default=None)
    if missing and oldest and not g.down and g.call("database check", db.is_connected,
                                                     default=False):
        rows = int(np.busday_count(date.fromisoformat(oldest[:10]), date.today())) + 15
        docs = g.call("FY-end price read (sector totals)", db.history_docs, missing,
                      rows=rows, default={}, heavy=True) or {}
        for t, doc in docs.items():
            s = cv._close_series(records_to_df(doc))
            if s is not None:
                fy_closes[t] = _closes_at_periods(s, periods.get(t) or [])
        del docs
    win: dict = {}
    high_why = ""
    store_read = False
    if not g.down and g.call("database check", db.is_connected, default=False):
        store_read = True
        docs = g.call("52-week price read (sector totals)", db.history_docs, names,
                      rows=HIGH_ROWS, default={}, heavy=True) or {}
        for t, doc in docs.items():
            s = cv._close_series(records_to_df(doc))
            if s is not None:
                win[t] = s
        del docs
    else:
        high_why = "the database did not answer — the 52-week window needs the stored closes"
    groups: dict = {}
    by_method: dict = {}
    for b in base:
        by_method.setdefault(b.get("method"), []).append(b)
    for method, rows in by_method.items():
        fy = ST.fy_history(rows, fy_closes, {})
        fy.pop("now", None)
        if win:
            hi = ST.high_52w({b["ticker"]: win.get(b["ticker"]) for b in rows},
                             {b["ticker"]: b.get("shares_outstanding") for b in rows})
        else:
            hi = {"high_cr": None, "high_date": None,
                  "why": high_why or "no stored closes for the group"}
        groups[str(method)] = {"fy": fy, "high": hi}
    return groups, store_read and not g.down


def totals_built(base: list) -> tuple[dict, str]:
    """(groups, source) — the in-process copy, else the stored doc for
    today, else built now (unless this render's budget is spent) and
    stored. A build that read nothing from the store is used for the day
    but not stored."""
    from datetime import datetime
    from . import db
    today = date.today().isoformat()
    rc = _rcache()
    if "totals" in rc:                      # one build at most per render
        return rc["totals"]
    if _TOTALS.get("day") == today and _TOTALS.get("groups") is not None:
        return _TOTALS["groups"], _TOTALS.get("source") or "process"
    with _TOTALS_LOCK:
        if _TOTALS.get("day") == today and _TOTALS.get("groups") is not None:
            return _TOTALS["groups"], _TOTALS.get("source") or "process"
        g = cv._gate()
        doc = g.call("stored sector totals read", db.load_settings, doc_id=TOTALS_DOC_ID,
                     default={}) if not g.down else {}
        if (doc or {}).get("day") == today and doc.get("version") == TOTALS_VERSION \
                and isinstance(doc.get("groups"), dict):
            _TOTALS.update(day=today, groups=doc["groups"], source="stored")
            return doc["groups"], "stored"
        r = cv._render()
        if r is not None and r.budget_spent():
            rc["totals"] = ({}, "not built on this open — the §12 time budget was spent")
            return rc["totals"]
        groups, complete = _build_totals(base)
        _TOTALS.update(day=today, groups=groups, source="built" if complete else "built_degraded")
        if complete:
            try:
                g.call("sector totals save", db.save_settings,
                       {"day": today, "version": TOTALS_VERSION, "groups": groups,
                        "ts": datetime.utcnow().isoformat()}, doc_id=TOTALS_DOC_ID)
            except Exception:                               # noqa: BLE001
                log.exception("valuation: sector totals save failed (in-process copy kept)")
        else:
            # rebuilt by a later open whose database answers
            _TOTALS["day"] = None
        rc["totals"] = (groups, _TOTALS["source"])
        return groups, _TOTALS["source"]


def _nse_reading(index_label: Optional[str]) -> dict:
    """NSE's own P/E / P/B for the stock's index, dated by NSE's session."""
    from trading import data_refresh as dr
    name = dr.SECTOR_NSE_NAME.get(index_label or "Nifty 50")
    out = {"index": name, "pe": None, "pb": None, "date": None, "stale": False,
           "history_from": None, "n_days": 0, "why": ""}
    if not name:
        out["why"] = "no NSE name for this index"
        return out
    pe_row = cv._feed("latest_row", f"nse_pe:{name}")
    pb_row = cv._feed("latest_row", f"nse_pb:{name}")
    if not isinstance(pe_row, dict) and not isinstance(pb_row, dict):
        out["why"] = ("NSE allIndices not stored yet — the 09:20 / 15:35 market feed "
                      "reads it daily")
        return out
    row = pe_row if isinstance(pe_row, dict) else pb_row
    out["pe"] = num((pe_row or {}).get("value")) if isinstance(pe_row, dict) else None
    out["pb"] = num((pb_row or {}).get("value")) if isinstance(pb_row, dict) else None
    out["date"] = str(row.get("date") or "")[:10] or None
    out["timestamp"] = row.get("timestamp") or ""
    out["closed"] = bool(row.get("session_closed"))
    age = cv._age_days(out["date"])
    out["stale"] = bool(age is None or age > NSE_STALE_DAYS)
    out["age_days"] = age
    pts = cv._feed("series_points", f"nse_pe:{name}") or []
    if pts:
        out["history_from"], out["n_days"] = pts[0][0], len(pts)
    return out


def _index_below_high(series: Optional[pd.Series]) -> Optional[dict]:
    if series is None or not len(series):
        return None
    s = series.dropna().tail(252)
    if not len(s):
        return None
    hi_d = s.idxmax()
    return {"below_pct": (float(s.iloc[-1]) / float(s.max()) - 1.0) * 100.0,
            "high": float(s.max()), "high_date": hi_d.date().isoformat(),
            "last_date": s.index[-1].date().isoformat(), "n": int(len(s))}


def sector_block(rec: dict, idx: Optional[dict] = None) -> dict:
    """§4: the route group's total beside the target, with NSE's own P/E."""
    from trading.valuation import sector_total as ST
    cb = rec.get("currency_base") or {}
    method = cb.get("method") or ""
    out: dict = {"status": "OK", "why": ""}
    base = cv._PEER_CACHE.get("base") or []
    by = cv._PEER_CACHE.get("by_method") or {}
    if not method or not base:
        out.update(status="DATA_NEEDED",
                   why="no route group — the statements peer base is not built on this open"
                   if method else "no route for this name")
    else:
        rows = [b for b in base if b.get("method") == method]
        prices = {r["ticker"]: r["price"] for r in by.get(method) or []
                  if r.get("ticker") and num(r.get("price"))}
        groups, src = totals_built(base)
        blk = ST.block(rows, prices, cb.get("family") or "", groups.get(method),
                       cb.get("sector") or "")
        blk["built_source"] = src
        out.update(blk)
        if not groups and "budget" in src:
            out["fy_why"] = out["high_why"] = src
    idx = idx or {}
    nse_key = idx.get("index") or "Nifty 50"
    out["nse"] = _nse_reading(nse_key)
    since = vv._default_sector_since()
    ser = _sector_series(idx["index"], since) if idx.get("index") else cv._index_series(
        "nifty_close")
    out["index_high"] = _index_below_high(ser)
    out["index_label"] = idx.get("label") or vc.MARKET_INDEX_LABEL
    return out


def clear_caches() -> None:
    """Tests: forget the day's sector totals and the remap list."""
    _TOTALS.clear()
    _REMAP_CACHE.clear()
