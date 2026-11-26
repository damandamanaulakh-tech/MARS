"""One stock against the five segments — the card on /chart/{ticker}.

Owner order 2026-11-26: "when u click the selected stock, it should show
target prices, and category of 5 momentum, intra etc".

DISPLAY ONLY. Built from what the app already holds: the stored daily bars,
the book, the last scan's stored record and the stored valuation record. A
page view never fetches a quote, runs a scan, or scores a stock through a
network feed. Every figure the card cannot know is said to be unknown, with
the reason — never filled with a guess.

What each row is:
  buy mark   the segment's own score mark (engine.mode_cfg, owner edits
             included) — the number the scan refuses below;
  score      Momentum: its stored momentum-pass score, else computed here
             from the stored bars (momentum + quality are pure price maths).
             The other four score on outside inputs too, so they are shown
             only when the LAST stored scan pass was that segment's;
  verdict    cleared / watch / below against the marks (market_panels.
             watch_label) — a reading of the score, never a buy signal;
  stop,      what a NEW buy at the last stored close would be given by the
  target     same functions the engine calls (trading.targets). Intraday is
             priced off 5-minute bars at the moment of entry, so the card
             states its rule and no price.
  volume     the two volume gates (trading.timing) on the stored daily bars,
             with each daily segment's own Gate 1 mark. Intraday reads its
             5-minute bars at its own scan and is not shown.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

SEGMENTS = ("nifty200_momentum", "intraday", "short", "swing", "positional")
MOMENTUM = "nifty200_momentum"

# verdict -> (label, tone) for the template
VERDICTS = {
    "cleared": ("CLEARED", "good"),
    "watch": ("WATCH", "warn"),
    "below": ("BELOW", "muted"),
    "unknown": ("NOT SCORED", "muted"),
}


def _num(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _stored_scan(portfolio, ticker: str) -> dict:
    """The last scan pass's own record for this stock, if it has one."""
    matrix = getattr(portfolio, "scan_matrix", None)
    matrix = matrix if isinstance(matrix, dict) else {}
    row = (matrix.get("rows") or {}).get(ticker)
    if not isinstance(row, dict):
        return {}
    return {"mode": str(matrix.get("mode") or ""), "at": matrix.get("ts"),
            "score": _num(row.get("score")),
            "floor": _num(matrix.get("score_floor")),
            "outcome": str(row.get("outcome") or ""),
            "timing": row.get("timing")}


def _stored_momentum(portfolio, ticker: str) -> dict:
    short = getattr(portfolio, "momentum_shortlist", None)
    short = short if isinstance(short, dict) else {}
    row = (short.get("rows") or {}).get(ticker)
    if not isinstance(row, dict):
        return {}
    return {"score": _num(row.get("score")), "at": short.get("ts"),
            "outcome": str(row.get("outcome") or "")}


def _valuation(ticker: str, last_close: float, today, cfg) -> dict:
    """The stored valuation target a new non-intraday buy would take, by the
    engine's own rule (trading.valuation_exit) — or the reason it would not."""
    try:
        from trading import valuation_exit as VE
        if not bool(getattr(cfg, "valuation_exit_enabled", True)):
            return {"target": None, "reason": "disabled"}
        if not VE.ready():
            return {"target": None, "reason": "no_reader"}
        rec = VE.records_for([ticker]).get(ticker)
        target, diag = VE.target_from_record(rec, last_close, today)
        return {"target": target, "reason": str((diag or {}).get("reason") or ""),
                "asof": (rec or {}).get("asof") if isinstance(rec, dict) else None,
                "verdict": (rec or {}).get("verdict") if isinstance(rec, dict) else None}
    except Exception as exc:                                   # noqa: BLE001
        return {"target": None, "reason": f"unavailable: {type(exc).__name__}"}


_VALUATION_WHY = {
    "disabled": "valuation targets are switched off in Settings",
    "no_reader": "no stored valuation record is available here",
    "no_record": "no valuation record is stored for this stock",
    "below_margin": "the valuation target is not at least 5% above the price",
}


def valuation_text(v: dict) -> str:
    reason = str(v.get("reason") or "")
    if reason.startswith("stale"):
        return "the stored valuation record is too old"
    if reason.startswith("not_judged"):
        return "the valuation chain could not judge this stock (" + reason.split(":", 1)[-1] + ")"
    return _VALUATION_WHY.get(reason, reason.replace("_", " ") or "not available")


def _volume_gates(df, mode_cfg: Callable) -> Optional[dict]:
    """Both volume gates on the stored daily bars, with each daily segment's
    own Gate 1 mark. None when the bars carry no volume or nothing can be read."""
    from trading import category_budget as cb
    try:
        from trading import timing as TM
        if "Volume" not in df:
            return None
        rows, trend, spike = [], None, None
        for mode in SEGMENTS:
            if mode == "intraday":
                continue
            gates = TM.volume_gates(df, mode_cfg(mode))
            g1, spike = gates["gate1"], gates["gate2"]
            trend = g1.get("ratio") if trend is None else trend
            rows.append({"label": cb.LABELS.get(mode, mode), "need": g1.get("need"),
                         "applies": bool(g1.get("applies")), "ok": g1.get("ok"),
                         "blocks": bool(g1.get("blocks"))})
        if spike is None or (trend is None and spike.get("ratio") is None):
            return None
        return {"trend_ratio": trend, "segments": rows,
                "spike_ratio": spike.get("ratio"), "spike_ok": spike.get("ok"),
                "spike_need": spike.get("need")}
    except Exception:                                          # noqa: BLE001
        return None


def build(ticker: str, df, portfolio, cfg, mode_cfg: Callable, today=None) -> dict:
    """The card for one stock. `df` = stored daily OHLCV (None = no data).
    `mode_cfg(mode)` = the segment's own settings (engine.mode_cfg)."""
    from trading import category_budget as cb
    from trading import targets as T
    from . import market_panels as mp

    today = today or datetime.utcnow().date()
    out: dict = {"ticker": ticker, "has_data": False, "segments": [], "held": None,
                 "last_scan": None, "valuation": None, "notes": []}
    closes = None
    if df is not None and not getattr(df, "empty", True) and "Close" in df:
        closes = df["Close"].dropna()
    if closes is None or closes.empty:
        out["notes"].append("No stored daily history for this stock — stops and targets "
                            "cannot be worked out, and nothing is guessed.")
        last = None
    else:
        last = float(closes.iloc[-1])
        stamp = closes.index[-1]
        atr = T.atr(df)
        out.update(has_data=True, last_close=last,
                   asof=stamp.strftime("%d %b %Y") if hasattr(stamp, "strftime") else str(stamp),
                   bars=int(len(closes)), atr=atr,
                   atr_pct=(atr / last * 100) if atr and last else None)

    watch_floor = float(getattr(cfg, "watch_floor", 55.0) or 0.0)
    scan = _stored_scan(portfolio, ticker)
    mom = _stored_momentum(portfolio, ticker)
    if scan:
        out["last_scan"] = {**scan, "segment": cb.LABELS.get(scan["mode"], scan["mode"] or "—")}

    for mode in SEGMENTS:
        try:
            mcfg = mode_cfg(mode)
        except Exception:                                      # noqa: BLE001
            continue
        buy_mark = float(mcfg.min_score_to_enter)
        row = {"mode": mode, "label": cb.LABELS.get(mode, mode), "buy_mark": buy_mark,
               "share": cb.share(cfg, mode), "max_hold_days": int(mcfg.max_hold_days),
               "score": None, "score_source": "", "stop_pct": None, "stop_price": None,
               "target_pct": None, "target_price": None, "target_source": "",
               "stop_source": "", "rule": ""}

        # ---- score: stored first, pure price maths second, never a feed
        if mode == MOMENTUM and mom.get("score") is not None:
            row["score"], row["score_source"] = mom["score"], "stored by the last Momentum pass"
        elif scan and scan["mode"] == mode and scan.get("score") is not None:
            row["score"], row["score_source"] = scan["score"], "stored by the last scan pass"
        elif mode == MOMENTUM and df is not None and closes is not None and len(closes):
            try:
                from trading.scoring import score_for_mode
                value, _ = score_for_mode(MOMENTUM, ticker, df, None, {}, mcfg, missing=None)
                if value is not None:
                    row["score"] = float(value)
                    row["score_source"] = "computed now from the stored daily bars"
                else:
                    row["score_source"] = "needs about a year of stored bars"
            except Exception:                                  # noqa: BLE001
                row["score_source"] = "could not be computed from the stored bars"
        else:
            # this segment's score uses outside feeds, which a page view never calls
            row["score_source"] = "not scored in the last stored pass"
        label = mp.watch_label(row["score"], buy_mark, watch_floor)
        row["verdict"] = label or ("below" if row["score"] is not None else "unknown")
        row["verdict_label"], row["tone"] = VERDICTS[row["verdict"]]

        # ---- what a NEW buy at the last stored close would be given
        if mode == "intraday":
            lo, hi = T.TARGET_BAND["intraday"]
            row["rule"] = (f"Priced off 5-minute bars at the moment of entry: stop "
                           f"{abs(float(mcfg.stop_loss_pct)) * 100:g}% below the fill, target "
                           f"{T.ATR_TARGET_K['intraday']:g} × the 5-minute ATR (kept between "
                           f"+{lo * 100:g}% and +{hi * 100:g}%), squared off 15:25 the same day.")
        elif last:
            stop_pct, sdiag = T.stop_pct_for(mode, last, df, float(mcfg.stop_loss_pct),
                                             enabled=bool(getattr(mcfg, "atr_stop_enabled", True)))
            tgt_pct, tdiag = T.target_pct_for(mode, last, df, float(mcfg.take_profit_pct))
            row.update(stop_pct=stop_pct * 100, stop_price=round(last * (1 + stop_pct), 2),
                       target_pct=tgt_pct * 100, target_price=round(last * (1 + tgt_pct), 2),
                       stop_source=("2 × ATR" if str(sdiag.get("source", "")).startswith("atr")
                                    and sdiag.get("source") != "atr_floor" else "segment stop"),
                       target_source=(f"{tdiag.get('k'):g} × ATR" if tdiag.get("source") == "atr"
                                      else "segment fixed target"))
        out["segments"].append(row)

    # ---- the two volume gates on the stored DAILY bars (owner decision
    # 2026-11-26). One reading of each; the mark Gate 1 needs differs by
    # segment. Intraday reads 5-minute bars at its own scan, not these.
    out["volume_gates"] = _volume_gates(df, mode_cfg) if out["has_data"] else None

    # ---- pre-rise (owner decision 2026-11-26): compression + volume
    # ignition as one label, with its measured record. Display only.
    out["pre_rise"] = None
    if out["has_data"]:
        try:
            from trading import pre_rise as PR
            got = PR.reading(df)
            out["pre_rise"] = {"on": bool(got and got.get("on")), "text": PR.describe(got),
                               "record": PR.record_text()}
        except Exception:                                      # noqa: BLE001
            out["pre_rise"] = None

    # ---- the book
    pos = getattr(portfolio, "positions", {}).get(ticker)
    if pos is not None:
        entry = _num(getattr(pos, "entry_price", None)) or 0.0
        seg = cb.category_of(pos)
        stop = _num(getattr(pos, "stop_price", None)) or None
        target = _num(getattr(pos, "target_price", None)) or None
        held = {"segment": cb.LABELS.get(seg, seg.replace("_", " ").title()),
                "qty": getattr(pos, "qty", None), "entry": entry, "stop": stop, "target": target,
                "owner_pick": "owner_pick" in (getattr(pos, "tags", None) or [])}
        try:
            held["days"] = (today - pos.entry_date.date()).days
        except Exception:                                      # noqa: BLE001
            held["days"] = None
        if last and entry:
            held["pnl_pct"] = (last / entry - 1) * 100
            if target and target > entry:
                held["to_target_pct"] = max(0.0, min(100.0, (last - entry) / (target - entry) * 100))
        out["held"] = held

    # ---- the valuation target a new non-intraday buy would take
    if last:
        v = _valuation(ticker, last, today, cfg)
        v["text"] = valuation_text(v) if v.get("target") is None else ""
        if v.get("target") is not None:
            v["upside_pct"] = (float(v["target"]) / last - 1) * 100
        out["valuation"] = v
    return out
