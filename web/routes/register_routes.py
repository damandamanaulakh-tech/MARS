"""📇 /register — the owner's Selection Register v4 (279 items), on screen.

Wired 2026-08-09 on the owner's order ("focus rest from 279 … discuss and
wire the rest"). Scope: DISPLAY / RESEARCH ONLY — the register's own
REQUIRES_15Y_BACKTEST rule applies, nothing here opens, closes, sizes or
blocks a trade. ~151 items AM07 already had are labelled with where they
live; the rest compute from committed data or say DATA_NEEDED with exactly
what unlocks them. Assumptions are on the page, not buried
(register_v4.ASSUMED_CONSTANTS).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth
from ..templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

_PANEL_CAP = 50          # members loaded for market-level breadth items
_PANEL_TAIL = 320        # bars per member (512MB box)


def _universe_names() -> list[str]:
    try:
        from trading.universes import load_full_universe
        return sorted(load_full_universe())
    except Exception:
        return []


def _load_panel() -> tuple[dict, dict]:
    """{ticker: OHLCV} + sector map from stored history (repo zip / Mongo)."""
    from trading.universes import get_nifty_200_universe
    from web.candle_chart import load_ohlcv
    tickers, sector_map = get_nifty_200_universe()
    panel = {}
    for t in tickers:
        if len(panel) >= _PANEL_CAP:
            break
        df = load_ohlcv(t, tail=_PANEL_TAIL)
        if df is not None and len(df) >= 60:
            panel[t] = df
    return panel, sector_map


def _group_of(rid: str) -> str:
    from trading.register_v4 import REGISTRY_BY_ID
    row = REGISTRY_BY_ID.get(rid)
    return (row or {}).get("Group", "")


def _rows_for(readings: dict) -> list[dict]:
    from trading.register_v4 import REGISTRY_BY_ID
    out = []
    for rid, r in readings.items():
        meta = REGISTRY_BY_ID.get(rid, {})
        out.append({
            "id": rid, "group": meta.get("Group", ""),
            "param": meta.get("Parameter", ""),
            "state": r.state,
            "value": None if r.value is None else round(r.value, 3),
            "note": r.note,
        })
    order = {"FIRED": 0, "VALUE": 1, "PARTIAL": 2, "QUIET": 3,
             "NA": 4, "DATA_NEEDED": 5}
    out.sort(key=lambda x: (x["group"], order.get(x["state"], 9), x["id"]))
    return out


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, ticker: str = ""):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    import trading.register_v4 as rv

    market_rows, stock_rows, stock_err = [], [], ""
    inputs = {}
    precomputed = None
    try:
        inputs = rv.load_market_inputs()
        # Node 10 (owner order 2026-08-24): prefer the nightly FULL-STORE
        # precompute (~2,700 names) over the render-time 50-member sample.
        # Fresh = closes within 5 days; stale or absent degrades to exactly
        # the old capped live compute, never to something new.
        panel = {}
        try:
            from datetime import date, timedelta
            from .. import state as app_state
            stored = app_state.portfolio().register_market or {}
            asof = str(stored.get("asof") or "")[:10]
            if (stored.get("rows") and asof
                    and date.today() - date.fromisoformat(asof)
                    <= timedelta(days=5)):
                precomputed = stored
        except Exception:
            log.exception("register: precompute read failed")
        if precomputed:
            mv = {rid: rv.Reading(rid, r.get("state", "NA"),
                                  r.get("value"), r.get("note", ""))
                  for rid, r in precomputed["rows"].items()}
        else:
            panel, sector_map = _load_panel()
            dmg = {}
            try:
                from .. import state as _st
                dmg = _st.portfolio().damage_store or {}
            except Exception:
                log.exception("register: damage store read failed")
            mv = rv.market_view(
                panel,
                index_close=inputs.get("index"),
                vix_close=inputs.get("vix"),
                bond_yield=inputs.get("bond"),
                flows=inputs.get("flows"),
                pwoi=inputs.get("pwoi"),
                sectors=sector_map,
                damage_store=dmg,
            )
        # W-028..031: portfolio-construction rules against the LIVE book
        try:
            from .. import state
            p = state.portfolio()
            # Fixed 2026-08-21 (found while hunting the /positions 500): this
            # called p.equity() without the prices argument and read a
            # pos.avg_price field that does not exist — both raised, the
            # except below swallowed it, and W-028..031 have NEVER computed
            # on a live book. Entry-price equity is the honest offline base
            # (this page fetches no quotes) and entry_price is the real field.
            deployed = sum(pos.qty * pos.entry_price
                           for pos in (p.positions or {}).values())
            equity = max(float(p.cash) + deployed, 1.0)
            weights = {t: pos.qty * pos.entry_price / equity
                       for t, pos in (p.positions or {}).items()}
            # On the precompute path `panel` is empty — load just the HELD
            # names' closes for the risk-parity vols (<=80 names, cheap),
            # or W-029..031 would silently lose their inputs.
            if not panel and p.positions:
                from web.candle_chart import load_ohlcv
                for ht in p.positions:
                    hdf = load_ohlcv(ht, tail=_PANEL_TAIL)
                    if hdf is not None and len(hdf) >= 60:
                        panel[ht] = hdf
            vols = {}
            for t, df in panel.items():
                r = df["Close"].pct_change().dropna() if "Close" in df else None
                if r is not None and len(r) >= 60:
                    vols[t] = float(r.std())
            mv.update(rv.portfolio_items(weights, vols))
        except Exception:
            log.exception("register: portfolio items failed")
            mv.update(rv.portfolio_items({}, {}))
        market_rows = _rows_for(mv)
    except Exception as e:                                    # noqa: BLE001
        log.exception("register: market view failed")
        stock_err = f"market view failed: {e}"

    t = (ticker or "").strip().upper()
    if t:
        try:
            from web.candle_chart import load_ohlcv
            sym = t.replace(".NS", "")
            df = load_ohlcv(t if t.endswith(".NS") else t + ".NS", tail=2600)
            if df is None:
                df = load_ohlcv(sym, tail=2600)
            if df is None or len(df) < 30:
                stock_err = (f"DATA_NEEDED — no stored history for {t}; "
                             "chart/scan pages fetch nothing silently and "
                             "neither does this one")
            else:
                sv = rv.stock_view(df, sym,
                                   bench_close=inputs.get("index"))
                stock_rows = _rows_for(sv)
        except Exception as e:                                # noqa: BLE001
            log.exception("register: stock view failed")
            stock_err = f"stock view failed: {e}"

    counts = {"have": len(rv.AM07_HAVE)}
    states = {}
    for r in market_rows + stock_rows:
        states[r["state"]] = states.get(r["state"], 0) + 1

    return templates.TemplateResponse(
        request, "register.html",
        {
            "user": user, "active": "register",
            "precomputed": precomputed,
            "market_rows": market_rows,
            "stock_rows": stock_rows,
            "ticker": t, "stock_err": stock_err,
            "have": sorted(rv.AM07_HAVE.items()),
            "us_map": sorted(rv.U_S_MAPPING.items()) + sorted(rv.BT_MAPPING.items()),
            "assumed": sorted(rv.ASSUMED_CONSTANTS.items()),
            "counts": counts, "states": states,
            "n_registry": len(rv.REGISTRY),
            "universe_names": _universe_names(),
        })
