"""Market legs on the valuation target — "split legs, no double count"
(VALFIX batch 3, owner choices 2026-10-21; study S2, REPORT_market_fx.md).

    market-adjusted target = own target
        × (1 + β_mkt × Sensex move since the selection date)
        × (1 + c × (sector index move − Sensex move))     the sheet's E121 row
        × rates leg (10-year yield, batch 2's feed)
        × the currency factor through EPS (currency.py; 1 when DATA_NEEDED)

The sector leg is applied only when its window matches the Sensex move's
(start and end within ASSUMED sector_window_tol_days = 7 days, windows_match);
otherwise it is not applied and the note names both windows.

Two columns side by side, as §11 prints its sector leg:
    typed    : β_mkt = 1 (the sheet's B156) and c = 1 (1:1)
    measured : β_mkt = the stock's 252-session OLS slope on the Sensex,
               c = the slope on (sector − Sensex) in the same regression
               (stock ~ Sensex + (sector − Sensex), the study's own legs)

GLOBAL (S&P 500, Nasdaq for IT names, Dow) is PRINTED with its move since
the selection date and the part the Sensex already carries (δ × Sensex
move; δ measured: Nasdaq 0.332, S&P 0.286), and COUNTED 0 — the owner chose
split legs, not "+ global".

SELECTION DATE = the time of action: the date the valuation is made. Default
the results publication date (period end + 45 days, snapshot ASSUMED); a
date typed in the inputs table wins, then the §11 target's date (typed
there or pushed from §12). The own target's P/E bridge reads the market only
up to this date (bridge_end), so the market since then is counted once, here.

Both targets print on every row; the verdict is read for both, the
market-adjusted one (as of now) labelled "verdict now". Buy-below =
market-adjusted × (1 − the sentiment margin), as §11.

PURE: every series is handed in. Display only — no gate, stop or size
reads this (boundary test).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from trading import valuation_context as _vc
from trading.valuation import assess as A
from trading.valuation import num

MEASURED: Dict[str, dict] = {
    "global_delta": {
        "value": {"nasdaq": 0.332, "sp500": 0.286},
        "source": "REPORT_market_fx.md (study 1, 2026-10-20): slope of the US index's "
                  "previous-session daily return on the Sensex, 494 sessions "
                  "2024-10-22..2026-10-19 — δ × Sensex move = the part of the US move "
                  "already inside the Sensex"},
}
ASSUMED: Dict[str, dict] = {
    "typed_beta": {"value": 1.0,
                   "source": "the owner's sheet B156 (typed β = 1), shown beside the measured β"},
    "typed_sector_coef": {"value": 1.0,
                          "source": "the owner's 1:1 sector rule (§11), beside the measured "
                                    "coefficient"},
    "regression_window": {"value": 252,
                          "source": "valuation_context ASSUMED beta_window_days (batch 2)"},
    "regression_min_obs": {"value": 120,
                           "source": "valuation_context ASSUMED beta_min_obs (batch 2)"},
    "min_index_sessions": {"value": 252,
                           "source": "owner answer 2026-10-21 (Nifty Cement: β DATA_NEEDED "
                                     "until 252 sessions) — applied to every sector index"},
    "selection_default": {"value": "results publication date (period end + 45 days)",
                          "source": "owner 2026-10-21 ('the time of action'); snapshot "
                                    "ASSUMED publication_lag_days 45; a typed date wins, "
                                    "then the §11 target's date"},
    "market_fallback_sector_leg": {
        "value": 0.0,
        "source": "a name with no NSE sector index (Nifty 50 fallback) counts the "
                  "sector-vs-market leg 0: Nifty minus Sensex is no sector "
                  "(REPORT_market_fx.md, TATATECH on Nifty 50)"},
    "global_counted": {"value": 0.0,
                       "source": "owner 2026-10-21: split legs, not '+ global' — printed, "
                                 "counted 0"},
    "us_alignment": {"value": "the last US close BEFORE the Indian date",
                     "source": "REPORT_market_fx.md: an Indian day pairs with the previous "
                               "US session (a same-day pair would leak the future)"},
    "verdict_now_column": {"value": "measured when β and c are both measured, else typed",
                           "source": "ASSUMED here 2026-10-21 — the other column's verdict "
                                     "prints beside it"},
    "sector_window_tol_days": {
        "value": 7,
        "source": "VALFIX batch-3 review 2026-10-21 (B4): a sector refresh that failed leaves "
                  "the sector store days behind the index file; 7 calendar days = one missed "
                  "week. Sensex and sector windows (start or end) further apart than this = "
                  "the sector leg is not applied, both windows named"},
}

GLOBAL_LEGS = (("sp500", "S&P 500"), ("nasdaq", "Nasdaq"), ("dow", "Dow Jones"))


def _ts(x: Any) -> Optional[pd.Timestamp]:
    try:
        t = pd.Timestamp(str(x)[:10])
    except (TypeError, ValueError):
        return None
    return None if pd.isna(t) else t.normalize()


def _clean(s: Optional[pd.Series]) -> Optional[pd.Series]:
    return _vc._clean(s)


# ------------------------------------------------------------ selection date

def _iso(x: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(x or "").strip()[:10])
    except ValueError:
        return None


def selection_date(snapshot_block: Optional[dict], typed: Any = None,
                   today: Optional[date] = None, pushed: Optional[dict] = None) -> dict:
    """{date, source, dated_today, why, notes, publication_date, period_end}.

    Order (owner 2026-10-21, "the time of action"): a date typed in the
    inputs table → the §11 stored target's date for this stock (typed there
    or pushed from §12) → the results publication date (period end + the
    publication lag, or a supplied result date). A §11 date older than the
    period end of the statements now in use belonged to an older valuation
    and is not used (said). A typed date before the publication date values
    with figures not yet public — kept (the owner typed it) and said.
    None = DATA_NEEDED."""
    today = today or date.today()
    sb = snapshot_block or {}
    pub = _iso(sb.get("publication_date"))
    pe = _iso(sb.get("period_end"))
    notes: List[str] = []
    base = {"publication_date": pub.isoformat() if pub else None,
            "period_end": pe.isoformat() if pe else None, "notes": notes, "why": ""}

    def _done(d: date, src: str) -> dict:
        if pub is not None and d < pub:
            notes.append(f"{d.isoformat()} is before these statements were published "
                         f"({pub.isoformat()}) — valued with figures not yet public")
        return {**base, "date": d.isoformat(), "source": src, "dated_today": d >= today}

    t = _iso(typed)
    if t is not None:
        return _done(t, "typed in the inputs table")
    pd_ = _iso((pushed or {}).get("date"))
    if pd_ is not None:
        if pe is not None and pd_ < pe:
            notes.append(f"the §11 target's date {pd_.isoformat()} predates these statements "
                         f"(period end {pe.isoformat()}) — not used")
        else:
            basis = str((pushed or {}).get("basis") or "")
            how = "pushed from §12" if basis.startswith("computed") else "typed in §11"
            return _done(pd_, f"the §11 target's date ({how})")
    if pub is not None:
        if sb.get("publication_assumed"):
            gap = (pub - pe).days if pe is not None else None
            lag = f"period end + {gap} days" if gap is not None else "period end + the lag"
            src = f"results publication date — {lag} (ASSUMED)"
        else:
            src = "results publication date on record"
        return _done(pub, src)
    return {**base, "date": None, "source": "", "dated_today": False,
            "why": "no publication date (no statements) and none typed"}


def bridge_end(selection: Optional[dict]) -> Optional[str]:
    """The date the own target's P/E bridge reads the market to: the
    selection date, never before the period end (a selection on or before
    the period end leaves the bridge's market row at 0 — the market-adjusted
    leg carries the whole move). None = no selection date (the bridge reads
    the newest close, as before)."""
    sel = selection or {}
    d = _iso(sel.get("date"))
    if d is None:
        return None
    pe = _iso(sel.get("period_end"))
    if pe is not None and d < pe:
        d = pe
    return d.isoformat()


# ------------------------------------------------------------------ moves

def close_before(s: Optional[pd.Series], when: Any) -> Optional[tuple]:
    """(date_iso, value) of the last value STRICTLY before `when`."""
    s, ts = _clean(s), _ts(when)
    if s is None or ts is None:
        return None
    part = s[s.index < ts]
    return (part.index[-1].date().isoformat(), float(part.iloc[-1])) if len(part) else None


def us_move(s: Optional[pd.Series], india_from: Any, india_to: Any) -> dict:
    """A US index's move between the US sessions BEFORE two Indian dates."""
    a, b = close_before(s, india_from), close_before(s, india_to)
    if a is None:
        return {"status": "DATA_NEEDED", "pct": None,
                "why": "no US close stored before the selection date"}
    if b is None or a[1] <= 0:
        return {"status": "DATA_NEEDED", "pct": None, "why": "no recent US close stored"}
    return {"status": "OK", "start": a[1], "start_date": a[0], "end": b[1], "end_date": b[0],
            "pct": (b[1] / a[1] - 1.0) * 100.0}


def regress(stock: Optional[pd.Series], sensex: Optional[pd.Series],
            sector: Optional[pd.Series], end: Any = None,
            window: int = int(ASSUMED["regression_window"]["value"]),
            min_obs: int = int(ASSUMED["regression_min_obs"]["value"]),
            min_index_sessions: int = int(ASSUMED["min_index_sessions"]["value"])) -> dict:
    """β_mkt (stock on the Sensex alone) and c (the stock's slope on
    sector − Sensex with the Sensex in the same regression), over the last
    `window` paired daily returns ending at `end`."""
    out: dict = {"beta_mkt": None, "beta_n": 0, "beta_why": "", "rel_coef": None,
                 "rel_n": 0, "rel_why": "", "window": ""}
    b = _vc.beta(stock, sensex, window=window, min_obs=min_obs, end=end)
    out["beta_mkt"], out["beta_n"] = b.get("beta"), b.get("n") or 0
    out["beta_why"] = b.get("why") or ""
    if b.get("beta") is not None:
        out["window"] = f"{b['window_start']} → {b['window_end']}"
    st, mk, sc = _clean(stock), _clean(sensex), _clean(sector)
    if sc is None:
        out["rel_why"] = "no sector index series"
        return out
    ts = _ts(end)
    if ts is not None:
        sc = sc[sc.index <= ts]
    if len(sc) < int(min_index_sessions):
        first = sc.index[0].date().isoformat() if len(sc) else "—"
        out["rel_why"] = (f"the index has {len(sc)} sessions (from {first}) — the measured "
                          f"coefficient needs {int(min_index_sessions)}")
        return out
    if st is None or mk is None:
        out["rel_why"] = "no stock or Sensex series"
        return out
    df = pd.concat([st.rename("s"), mk.rename("m"), sc.rename("x")], axis=1, join="inner")
    if ts is not None:
        df = df[df.index <= ts]
    r = df.pct_change(fill_method=None).dropna()
    r = r[np.isfinite(r).all(axis=1)].tail(int(window))
    n = int(len(r))
    out["rel_n"] = n
    if n < int(min_obs):
        out["rel_why"] = f"{n} paired sessions — the coefficient needs {int(min_obs)}"
        return out
    X = np.column_stack([np.ones(n), r["m"].to_numpy(), (r["x"] - r["m"]).to_numpy()])
    try:
        coef, *_ = np.linalg.lstsq(X, r["s"].to_numpy(), rcond=None)
    except np.linalg.LinAlgError:
        out["rel_why"] = "regression did not solve"
        return out
    if not np.isfinite(coef[2]):
        out["rel_why"] = "regression did not solve"
        return out
    out["rel_coef"] = float(coef[2])
    return out


# ------------------------------------------------------------------- legs

def windows_match(sensex_move: Optional[dict], sector_move: Optional[dict],
                  tol_days: Optional[int] = None, *,
                  sector_name: Optional[str] = None) -> tuple:
    """(ok, why): do the Sensex and sector moves cover the same window?
    Compares the `start_date` / `end_date` that valuation_context.
    move_between puts on every OK move. Either end further apart than
    `tol_days` (ASSUMED sector_window_tol_days, 7) = not ok — subtracting a
    sector move that stops days before the Sensex move would book the gap as
    a sector effect. `why` names both windows. A move without its dates
    cannot be compared: ok, and `why` says so (no live path builds one)."""
    tol = int(ASSUMED["sector_window_tol_days"]["value"] if tol_days is None else tol_days)
    name = sector_name or "the sector index"
    s, x = sensex_move or {}, sector_move or {}
    sa, se = _iso(s.get("start_date")), _iso(s.get("end_date"))
    xa, xe = _iso(x.get("start_date")), _iso(x.get("end_date"))
    if None in (sa, se, xa, xe):
        return True, "window dates not on the moves — not compared"
    gap = max(abs((sa - xa).days), abs((se - xe).days))
    span = (f"Sensex {sa.isoformat()}→{se.isoformat()}, {name} "
            f"{xa.isoformat()}→{xe.isoformat()}: the windows differ by {gap} day(s)")
    if gap > tol:
        return False, f"{span} (more than {tol})"
    return True, (f"{span} (within {tol})" if gap else "")


def _column(own: float, s_pct: float, rel_pct: Optional[float], beta: float,
            coef: Optional[float], rates_mult: Optional[float], fx: float) -> dict:
    m = 1.0 + beta * s_pct / 100.0
    sf = 1.0 + (coef * rel_pct / 100.0) if (rel_pct is not None and coef is not None) else None
    total = m * (sf if sf is not None else 1.0) * (rates_mult if rates_mult is not None else 1.0) * fx
    return {"beta": beta, "coef": coef, "market_factor": m, "sector_factor": sf,
            "rates_factor": rates_mult, "fx_factor": fx, "total_factor": total,
            "target": own * total}


def market_adjust(own_target: Optional[float], *, sensex_move: dict, sector_move: Optional[dict],
                  sector_is_market: bool, reg: dict, rates: dict, fx_factor: float = 1.0,
                  dated_today: bool = False, sector_name: Optional[str] = None,
                  window_tol_days: Optional[int] = None) -> dict:
    """Both columns. Missing legs are named: the market leg missing = no
    market-adjusted target; a missing sector or rates leg is NOT applied
    (× 1 avoided in words — the note says so); FX is the EPS factor (1 when
    its own block says DATA_NEEDED). A sector move whose window differs from
    the Sensex move's by more than ASSUMED sector_window_tol_days is not
    applied either (`windows_match`; `sector_name` names the index in the
    note)."""
    out: Dict[str, Any] = {"status": "OK", "why": "", "notes": [], "typed": None,
                           "measured": None, "measured_why": "", "dated_today": dated_today}
    own = num(own_target)
    if own is None or own <= 0:
        out.update(status="DATA_NEEDED", why="no own target (the range has no base)")
        return out
    if dated_today:
        out.update(status="SAME_DAY", why="nothing to adjust yet — the selection date is today",
                   target_now=own)
        return out
    if (sensex_move or {}).get("status") != "OK":
        out.update(status="DATA_NEEDED",
                   why=f"Sensex move DATA_NEEDED — {(sensex_move or {}).get('why') or 'no series'}")
        return out
    s_pct = float(sensex_move["pct"])
    rel_pct: Optional[float] = None
    if sector_is_market:
        rel_pct = 0.0
        out["notes"].append("no NSE sector index for this name — the sector-vs-market leg "
                            "is counted 0 (the market leg carries it)")
    elif (sector_move or {}).get("status") == "OK":
        ok, wwhy = windows_match(sensex_move, sector_move, window_tol_days,
                                 sector_name=sector_name)
        out["sector_window"] = {"ok": ok, "why": wwhy}
        if ok:
            rel_pct = float(sector_move["pct"]) - s_pct
        else:
            out["notes"].append("sector leg not applied — " + wwhy)
    else:
        out["notes"].append("sector leg not applied — "
                            + ((sector_move or {}).get("why") or "no sector index move"))
    out["rel_pct"] = rel_pct
    rm = num((rates or {}).get("multiplier")) if (rates or {}).get("status") == "OK" else None
    if rm is None:
        out["notes"].append("rates leg not applied — "
                            + ((rates or {}).get("why") or "10-year yield DATA_NEEDED"))
    t_beta = float(ASSUMED["typed_beta"]["value"])
    t_coef = float(ASSUMED["typed_sector_coef"]["value"])
    out["typed"] = _column(own, s_pct, rel_pct, t_beta, t_coef if rel_pct is not None else None,
                           rm, fx_factor)
    b, c = num(reg.get("beta_mkt")), num(reg.get("rel_coef"))
    if sector_is_market:
        c = 0.0 if c is None else c
    if b is None:
        out["measured_why"] = f"β on the Sensex DATA_NEEDED — {reg.get('beta_why') or 'no series'}"
    elif rel_pct is not None and not sector_is_market and c is None:
        out["measured_why"] = f"sector coefficient DATA_NEEDED — {reg.get('rel_why') or 'no series'}"
    else:
        out["measured"] = _column(own, s_pct, rel_pct, b, c if rel_pct is not None else None,
                                  rm, fx_factor)
    return out


def global_lines(moves: Dict[str, dict], sensex_move: dict, is_it: bool) -> List[dict]:
    """The US legs, printed and counted 0: move since the selection date,
    δ × Sensex move already inside the Sensex, the residual. Nasdaq is shown
    for IT names, S&P and Dow for all."""
    s_ok = (sensex_move or {}).get("status") == "OK"
    s_pct = float(sensex_move["pct"]) if s_ok else None
    deltas = MEASURED["global_delta"]["value"]
    out = []
    for key, name in GLOBAL_LEGS:
        if key == "nasdaq" and not is_it:
            continue
        mv = (moves or {}).get(key) or {"status": "DATA_NEEDED", "why": "not stored", "pct": None}
        d = deltas.get(key)
        row = {"key": key, "name": name, "move": mv, "delta": d, "counted": 0.0,
               "inside_pct": None, "residual_pct": None,
               "delta_why": "" if d is not None else "δ not measured for this index"}
        if mv.get("status") == "OK" and d is not None and s_pct is not None:
            row["inside_pct"] = d * s_pct
            row["residual_pct"] = float(mv["pct"]) - d * s_pct
        out.append(row)
    return out


def verdicts(price: Any, rng: dict, out_legs: dict, bands: Optional[dict] = None) -> dict:
    """The verdict on the market-adjusted range for each column (the range
    scaled by the column's total factor), plus which one is "verdict now"."""
    res: Dict[str, Any] = {"typed": None, "measured": None, "now_column": None}
    lo, ba, hi = num(rng.get("low")), num(rng.get("base")), num(rng.get("high"))
    for col in ("typed", "measured"):
        c = out_legs.get(col)
        if not c or None in (lo, ba, hi):
            continue
        f = float(c["total_factor"])
        v = A.assess(price, {"low": lo * f, "base": ba * f, "high": hi * f,
                             "family": rng.get("family")}, bands=bands)
        res[col] = {"verdict": v.get("verdict"), "upside_to_base_pct": v.get("upside_to_base_pct"),
                    "reasons": list(v.get("reasons") or [])[:2],
                    "low": lo * f, "base": ba * f, "high": hi * f}
    res["now_column"] = "measured" if res["measured"] else ("typed" if res["typed"] else None)
    return res
