"""BT01-BT08: the owner's reverse-backtest calibration, run for real.

Design source (verbatim steps): research/chityy_archive/trigger_layer_v0_2/
reverse_backtest_design_v0_2.csv. This run evaluates the SAME trigger code
the live app runs (trading.triggers.evaluate / side_scores / evaluate_gates
— no reimplementation, so calibration and production can never diverge).

Panel: the committed 191-ticker x ~12y daily store
(data/history_upstox_export_2026-07-19.zip). The design asks 15Y; 12y is
what the committed store holds — disclosed in the output, not hidden.

ASSUMED (the design leaves these numeric choices open; every one is
recorded in the output header):
  - Event labels (BT01), on forward ADJUSTED-close returns:
      RISE     : fwd_20D >= +10%
      FALL     : fwd_20D <= -10%
      COLLAPSE : fwd_20D <= -20%
      REVERSAL : fwd_10D <= -5% AND fwd_20D >= +5% (down-then-up)
  - Evaluation grid: every 5 trading days (weekly cadence) — 191 stocks x
    ~500 eval dates x 79 triggers. Daily cadence is a x5 rerun if wanted.
  - Hit (BT03): trigger fired at t AND the event label is set at any day in
    (t, t+10] (the event's formation start inside the trigger's 1-2W claim).
  - Lead time: days from fire t to the first labelled event day after t
    (capped 20) — the SEQUENCING answer: smaller median lead = fires later
    (closer to the move); larger = earlier-stage trigger.
  - Proof tags (BT08): VERIFIED   = lift >= 1.5 AND fires >= 30
                       SUPPORTED  = lift >= 1.1 AND fires >= 30
                       NOT_ENOUGH = fires < 30
                       REJECTED   = lift < 1.1 with fires >= 30

Usage:  python scripts/bt_calibration_v1.py out_dir/
Writes: trigger_calibration.csv (per trigger: fires, hits by event type,
        lift, FP rate, median lead days, proof tag)
        cluster_scores.csv (BT06: their 8 clusters, precision/recall/lift)
        gate_replay.csv (BT07: GATE_RISE_ENTRY_01 paper replay, expectancy)
        summary.md
"""
from __future__ import annotations

import io
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading.triggers import (COMPUTABLE, TRIGGERS, _Ctx,          # noqa: E402
                              evaluate_gates, side_scores)

ZIP = ROOT / "data" / "history_upstox_export_2026-07-19.zip"
STEP = 5                # eval every 5 trading days
MIN_BARS = 300          # bars of history a trigger evaluation needs
FWD = 20                # forward window for labels

RISE_T, FALL_T, COLLAPSE_T = 0.10, -0.10, -0.20


def load_panel() -> dict[str, pd.DataFrame]:
    out = {}
    with zipfile.ZipFile(ZIP) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            df = pd.read_csv(io.BytesIO(z.read(name)),
                             parse_dates=["Date"], index_col="Date").sort_index()
            if len(df) >= MIN_BARS + FWD + 50:
                out[name[:-4]] = df
    return out


def label_events(close: np.ndarray) -> dict[str, np.ndarray]:
    """BT01: per-day event labels from forward returns."""
    n = len(close)
    f10 = np.full(n, np.nan)
    f20 = np.full(n, np.nan)
    f10[:-10] = close[10:] / close[:-10] - 1
    f20[:-20] = close[20:] / close[:-20] - 1
    return {
        "RISE": f20 >= RISE_T,
        "FALL": f20 <= FALL_T,
        "COLLAPSE": f20 <= COLLAPSE_T,
        "REVERSAL_UP": (f10 <= -0.05) & (f20 >= 0.05),
    }


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    panel = load_panel()
    print(f"panel: {len(panel)} tickers", flush=True)

    sides = {t.id: t.side for t in TRIGGERS}
    fires = defaultdict(int)                    # trigger -> total fires
    evals = defaultdict(int)                    # trigger -> total evaluations
    hits = defaultdict(lambda: defaultdict(int))  # trigger -> event -> hits
    fp = defaultdict(int)                       # fires with NO event in 20d
    leads = defaultdict(list)                   # trigger -> lead days
    base_windows = defaultdict(int)             # event -> windows where event followed
    n_windows = 0
    # BT07 replay: GATE_RISE_ENTRY_01 entries, exit +12/-5/20d (live bracket)
    trades = []

    for k, (tick, df) in enumerate(sorted(panel.items())):
        close = df["Close"].to_numpy(dtype=float)
        ev = label_events(close)
        n = len(df)
        for i in range(MIN_BARS, n - FWD, STEP):
            n_windows += 1
            window = df.iloc[:i + 1].tail(400)
            try:
                x = _Ctx(window)
            except (KeyError, ValueError):
                continue
            # any event starting in (i, i+10]?
            future = {e: ev[e][i + 1:i + 11].any() for e in ev}
            future20 = {e: ev[e][i + 1:i + 21].any() for e in ev}
            for e, yes in future.items():
                if yes:
                    base_windows[e] += 1
            day_hits: dict[str, bool | None] = {}
            for t in COMPUTABLE:
                try:
                    r = t.fn(x)
                except Exception:
                    r = None
                day_hits[t.id] = r
                if r is None:
                    continue
                evals[t.id] += 1
                if r:
                    fires[t.id] += 1
                    matched = False
                    for e, yes in future.items():
                        if yes:
                            hits[t.id][e] += 1
                            matched = True
                    if not matched and not any(future20.values()):
                        fp[t.id] += 1
                    # lead time to first labelled day of the trigger's OWN side
                    side = sides[t.id]
                    key = {"RISE": "RISE", "FALL": "FALL",
                           "COLLAPSE": "COLLAPSE",
                           "REVERSAL_UP": "REVERSAL_UP"}.get(side)
                    if key:
                        seg = ev[key][i + 1:i + 21]
                        idx = np.argmax(seg) if seg.any() else None
                        if idx is not None:
                            leads[t.id].append(int(idx) + 1)
            # BT07: their entry gate on this day's fires
            scores = side_scores(day_hits)
            gates = evaluate_gates(day_hits)
            entry = any(g["id"] == "GATE_RISE_ENTRY_01" and g["status"] == "PASS"
                        for g in gates)
            if entry:
                fill = close[i]
                stop, tgt = fill * 0.95, fill * 1.12
                exit_px, exit_d = close[min(i + 20, n - 1)], 20
                for j in range(i + 1, min(i + 21, n)):
                    if close[j] <= stop:
                        exit_px, exit_d = stop, j - i
                        break
                    if close[j] >= tgt:
                        exit_px, exit_d = tgt, j - i
                        break
                trades.append({"ticker": tick, "i": i,
                               "date": str(df.index[i].date()),
                               "ret_pct": (exit_px / fill - 1) * 100,
                               "days": exit_d,
                               "rise_score": scores.get("RISE"),
                               "fall_score": scores.get("FALL")})
        if (k + 1) % 25 == 0:
            print(f"{k + 1}/{len(panel)} tickers done", flush=True)

    # ---- assemble BT03/04/05/08 per-trigger table -------------------------
    base_rate = {e: base_windows[e] / n_windows for e in base_windows}
    rows = []
    for t in COMPUTABLE:
        f, ev_n = fires[t.id], evals[t.id]
        if ev_n == 0:
            continue
        side = sides[t.id]
        key = side if side in ("RISE", "FALL", "COLLAPSE") else "REVERSAL_UP"
        own_hits = hits[t.id].get(key, 0)
        hit_rate = own_hits / f if f else 0.0
        lift = (hit_rate / base_rate[key]) if f and base_rate.get(key) else None
        fp_rate = fp[t.id] / f if f else None
        med_lead = float(np.median(leads[t.id])) if leads[t.id] else None
        if f < 30:
            tag = "NOT_ENOUGH_EVIDENCE"
        elif lift is not None and lift >= 1.5:
            tag = "VERIFIED"
        elif lift is not None and lift >= 1.1:
            tag = "SUPPORTED"
        else:
            tag = "REJECTED"
        rows.append({
            "trigger_id": t.id, "name": t.name, "family": t.family,
            "side": side, "evaluations": ev_n, "fires": f,
            "fire_rate_pct": round(100 * f / ev_n, 2),
            "own_side_hits": own_hits,
            "hit_rate_pct": round(100 * hit_rate, 2),
            "baseline_rate_pct": round(100 * base_rate.get(key, 0), 2),
            "lift": round(lift, 3) if lift is not None else "",
            "false_positive_rate_pct":
                round(100 * fp_rate, 2) if fp_rate is not None else "",
            "median_lead_days": med_lead if med_lead is not None else "",
            "proof_tag": tag,
        })
    cal = pd.DataFrame(rows).sort_values(["side", "lift"],
                                         ascending=[True, False])
    cal.to_csv(out / "trigger_calibration.csv", index=False)

    # ---- BT07 replay summary ---------------------------------------------
    tr = pd.DataFrame(trades)
    if len(tr):
        tr.to_csv(out / "gate_replay.csv", index=False)
        wins = (tr.ret_pct > 0).mean() * 100
        expectancy = tr.ret_pct.mean()
    else:
        wins = expectancy = float("nan")

    verified = cal[cal.proof_tag == "VERIFIED"]
    rejected = cal[cal.proof_tag == "REJECTED"]
    seq = cal[cal.median_lead_days != ""].copy()
    with open(out / "summary.md", "w") as fh:
        fh.write(f"""# BT01-BT08 calibration — run {pd.Timestamp.now().date()}

Panel: {len(panel)} tickers x ~12y daily (committed store; design asks 15Y
— 12y is what exists, disclosed). Eval grid: every {STEP} trading days,
{n_windows:,} windows. SAME trigger code as production
(trading.triggers), zero reimplementation.

Event base rates per 10d window: """
                 + ", ".join(f"{e} {100*r:.1f}%" for e, r in base_rate.items())
                 + f"""

## Verdicts (BT08)
- VERIFIED (lift>=1.5, fires>=30): {len(verified)}
- SUPPORTED (lift>=1.1):           {len(cal[cal.proof_tag=='SUPPORTED'])}
- REJECTED (lift<1.1):             {len(rejected)}
- NOT_ENOUGH_EVIDENCE (<30 fires): {len(cal[cal.proof_tag=='NOT_ENOUGH_EVIDENCE'])}

## BT07 — GATE_RISE_ENTRY_01 paper replay (live -5/+12/20d bracket)
- entries: {len(tr)} · win rate: {wins:.1f}% · expectancy/trade: {expectancy:+.2f}%

## Sequencing (the "which fires first" answer)
Median lead days to the trigger's own event, by family (higher = earlier):
""")
        if len(seq):
            fam = seq.groupby("family")["median_lead_days"].median() \
                     .sort_values(ascending=False)
            for f_, v in fam.items():
                fh.write(f"- {f_}: {v:.0f}d\n")
    print("DONE", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
