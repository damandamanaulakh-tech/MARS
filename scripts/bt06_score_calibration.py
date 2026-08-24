"""BT06 (Node 11, owner order 2026-08-24: "node 11") — fit the side-score
weights on MEASURED per-trigger lifts, so the score formula stops being
ASSUMED, and judge the result on a holdout.

Their design row (reverse_backtest_design_v0_2.csv): "BT06 | Cluster score
test | combine triggers into rise/fall/collapse scores | proof metric:
precision, recall, lift". Their sheets set the GATE thresholds (entry =
rise>=65 & fall<35 & dq>=80) but never the score formula — the formula in
production is weighted-%-fired with the sheet's own per-trigger weights,
labelled ASSUMED since 2026-08-04. This run replaces those weights with
measured ones and keeps everything else identical.

METHOD (every choice recorded, none hidden):
  * SAME trigger code as production (trading.triggers), same panel
    (191 x ~12y committed zip), same labels as the 2026-08-09 main run
    (RISE fwd20>=+10%, FALL <=-10%, COLLAPSE <=-20%, REVERSAL_UP), same
    weekly eval grid.
  * TRAIN/TEST split at 2020-06-30 (the threshold-sweep precedent:
    "never sweep a threshold without a holdout"). Weights are FIT on
    train only; every judgement is read on test only.
  * Weight mapping (an ASSUMED choice, stated): for each trigger,
    train-half lift on its OWN side; weight = max(0, lift - 1), and 0
    when train fires < 30 (their own BT08 evidence bar). Inverted
    triggers get 0, never negative — subtracting would be a second
    invented rule.
  * Score SHAPE unchanged: 100 x sum(w of fired) / sum(w of computable)
    per side — only the weights change, which is exactly what Node 11
    names ("fit the side-score weights").
  * Judged on test: precision / recall / lift per side at a threshold
    grid for BOTH formulas, plus a paper replay of GATE_RISE_ENTRY_01
    (rise>=65 & fall<35 & dq>=80, live -5/+12/20d bracket) under each.

Usage:  python scripts/bt06_score_calibration.py research/bt06/run_DATE/
Writes: side_score_weights.csv    (per trigger: train lift, fires, weight)
        score_calibration.csv     (side x formula x threshold: P/R/lift, test)
        gate_replay_compare.csv   (entry gate under each formula, test half)
        summary.md
Checkpoint: window matrix saved to windows.npz after the panel pass; a
re-run with the file present skips straight to the fit (container-restart
lesson from the external run).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from bt_calibration_v1 import (FWD, MIN_BARS, STEP,                # noqa: E402
                               label_events, load_panel)
from trading.triggers import (COMPUTABLE, TRIGGERS, _Ctx,          # noqa: E402
                              data_quality_score)

SPLIT = pd.Timestamp("2020-06-30")
MIN_TRAIN_FIRES = 30                 # their BT08 evidence bar
EVENTS = ("RISE", "FALL", "COLLAPSE", "REVERSAL_UP")
THRESHOLDS = list(range(30, 95, 5))
SHEET_W = {t.id: float(t.weight) for t in TRIGGERS}


def _side_key(side: str) -> str:
    return side if side in ("RISE", "FALL", "COLLAPSE") else "REVERSAL_UP"


def collect_windows(out: Path) -> dict:
    """One pass over the panel: per window, per-trigger hit state (int8:
    -1 not computable / 0 quiet / 1 fired), the 4 event flags, date,
    ticker index and bar index (for the replay)."""
    npz = out / "windows.npz"
    if npz.exists():
        d = dict(np.load(npz, allow_pickle=True))
        print(f"checkpoint: {len(d['dates'])} windows loaded", flush=True)
        return d
    panel = load_panel()
    print(f"panel: {len(panel)} tickers", flush=True)
    tickers = sorted(panel)
    hits_rows, ev_rows, dates, tick_idx, bar_idx = [], [], [], [], []
    for k, tick in enumerate(tickers):
        df = panel[tick]
        close = df["Close"].to_numpy(dtype=float)
        ev = label_events(close)
        n = len(df)
        for i in range(MIN_BARS, n - FWD, STEP):
            window = df.iloc[:i + 1].tail(400)
            try:
                x = _Ctx(window)
            except (KeyError, ValueError):
                continue
            row = np.full(len(COMPUTABLE), -1, dtype=np.int8)
            for j, t in enumerate(COMPUTABLE):
                try:
                    r = t.fn(x)
                except Exception:
                    r = None
                if r is not None:
                    row[j] = 1 if r else 0
            hits_rows.append(row)
            ev_rows.append([bool(ev[e][i + 1:i + 11].any()) for e in EVENTS])
            dates.append(df.index[i].value)
            tick_idx.append(k)
            bar_idx.append(i)
        if (k + 1) % 25 == 0:
            print(f"{k + 1}/{len(tickers)} tickers", flush=True)
    d = {
        "hits": np.array(hits_rows, dtype=np.int8),
        "events": np.array(ev_rows, dtype=bool),
        "dates": np.array(dates, dtype=np.int64),
        "tick_idx": np.array(tick_idx, dtype=np.int32),
        "bar_idx": np.array(bar_idx, dtype=np.int32),
        "tickers": np.array(tickers, dtype=object),
    }
    np.savez_compressed(npz, **d)
    print(f"{len(d['dates'])} windows collected + checkpointed", flush=True)
    return d


def scores_matrix(hits: np.ndarray, weights: dict[str, float]) -> dict:
    """Per-side score per window under a weight set — the production
    formula vectorised: 100 * sum(w fired)/sum(w computable); NaN when a
    side has no computable weight."""
    out = {}
    for side in EVENTS:
        cols = [j for j, t in enumerate(COMPUTABLE)
                if _side_key(t.side) == side]
        if not cols:
            continue
        w = np.array([weights.get(COMPUTABLE[j].id, 0.0) for j in cols])
        h = hits[:, cols]
        comp = (h >= 0).astype(float) * w
        fired = (h == 1).astype(float) * w
        tot = comp.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[side] = np.where(tot > 0, 100.0 * fired.sum(axis=1) / tot,
                                 np.nan)
    return out


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    d = collect_windows(out)
    hits, events = d["hits"], d["events"]
    dates = pd.DatetimeIndex(d["dates"])
    is_train = np.asarray(dates <= SPLIT)
    is_test = ~is_train
    n_tr, n_te = int(is_train.sum()), int(is_test.sum())
    print(f"windows: {len(dates)} (train {n_tr}, test {n_te})", flush=True)

    # ---- fit: per-trigger lift on TRAIN, own side only --------------------
    ev_col = {e: k for k, e in enumerate(EVENTS)}
    base_tr = {e: events[is_train, ev_col[e]].mean() for e in EVENTS}
    wrows, measured_w = [], {}
    for j, t in enumerate(COMPUTABLE):
        key = _side_key(t.side)
        col = hits[is_train, j]
        fires = int((col == 1).sum())
        if fires >= MIN_TRAIN_FIRES and base_tr[key] > 0:
            hit_rate = float(events[is_train][:, ev_col[key]][
                hits[is_train, j] == 1].mean())
            lift = hit_rate / base_tr[key]
        else:
            lift = None
        w = max(0.0, (lift - 1.0)) if lift is not None else 0.0
        measured_w[t.id] = w
        wrows.append({"trigger_id": t.id, "name": t.name, "side": key,
                      "train_fires": fires,
                      "train_lift": round(lift, 3) if lift is not None else "",
                      "sheet_weight": SHEET_W[t.id],
                      "measured_weight": round(w, 4)})
    pd.DataFrame(wrows).to_csv(out / "side_score_weights.csv", index=False)
    live = {k: v for k, v in measured_w.items() if v > 0}
    print(f"weights fit: {len(live)}/{len(COMPUTABLE)} triggers carry "
          f"weight > 0", flush=True)

    # ---- score both formulas everywhere, judge on TEST --------------------
    s_assumed = scores_matrix(hits, SHEET_W)
    s_measured = scores_matrix(hits, measured_w)
    rows = []
    for side in EVENTS:
        y = events[is_test, ev_col[side]]
        base = float(y.mean())
        for label, sm in (("assumed", s_assumed), ("measured", s_measured)):
            s = sm.get(side)
            if s is None:
                continue
            s = s[is_test]
            ok = ~np.isnan(s)
            for thr in THRESHOLDS:
                sel = ok & (s >= thr)
                n_sel = int(sel.sum())
                prec = float(y[sel].mean()) if n_sel else None
                rec = (float((y & sel).sum() / y.sum())
                       if y.sum() else None)
                rows.append({
                    "side": side, "formula": label, "threshold": thr,
                    "windows_selected": n_sel,
                    "precision_pct":
                        round(100 * prec, 2) if prec is not None else "",
                    "recall_pct":
                        round(100 * rec, 2) if rec is not None else "",
                    "base_rate_pct": round(100 * base, 2),
                    "lift": round(prec / base, 3)
                            if prec is not None and base else "",
                })
    sc = pd.DataFrame(rows)
    sc.to_csv(out / "score_calibration.csv", index=False)

    # ---- entry-gate replay under each formula (TEST half) -----------------
    dq = np.array([
        data_quality_score({COMPUTABLE[j].id: (None if hits[r, j] < 0
                                               else bool(hits[r, j]))
                            for j in range(len(COMPUTABLE))})
        for r in range(hits.shape[0])])
    panel = load_panel()
    tickers = d["tickers"]
    grows = []
    for label, sm in (("assumed", s_assumed), ("measured", s_measured)):
        rise, fall = sm.get("RISE"), sm.get("FALL")
        entry = (is_test & ~np.isnan(rise) & ~np.isnan(fall)
                 & (rise >= 65) & (fall < 35) & (dq >= 80))
        rets = []
        for r in np.where(entry)[0]:
            df = panel[str(tickers[d['tick_idx'][r]])]
            close = df["Close"].to_numpy(dtype=float)
            i, n = int(d["bar_idx"][r]), len(df)
            fill = close[i]
            stop, tgt = fill * 0.95, fill * 1.12
            exit_px = close[min(i + 20, n - 1)]
            for j in range(i + 1, min(i + 21, n)):
                if close[j] <= stop:
                    exit_px = stop
                    break
                if close[j] >= tgt:
                    exit_px = tgt
                    break
            rets.append((exit_px / fill - 1) * 100)
        rets = np.array(rets)
        grows.append({
            "formula": label, "entries_test": len(rets),
            "win_rate_pct":
                round(float((rets > 0).mean() * 100), 1) if len(rets) else "",
            "expectancy_pct":
                round(float(rets.mean()), 2) if len(rets) else "",
            "worst_pct": round(float(rets.min()), 2) if len(rets) else "",
        })
    gr = pd.DataFrame(grows)
    gr.to_csv(out / "gate_replay_compare.csv", index=False)

    with open(out / "summary.md", "w") as fh:
        fh.write(f"""# BT06 side-score recalibration — run {pd.Timestamp.now().date()}

Windows: {len(dates):,} (train {n_tr:,} to {SPLIT.date()}, test {n_te:,}
after). Labels/panel/grid identical to the 2026-08-09 main run; SAME
production trigger code. Weight mapping: max(0, train_lift - 1), zero
under {MIN_TRAIN_FIRES} train fires (their BT08 bar); score shape
unchanged (weighted % of computable fired).

Weights: {len(live)} of {len(COMPUTABLE)} triggers carry measured weight
> 0 (the rest measured at or below chance on train — weight 0).

## GATE_RISE_ENTRY_01 replay on TEST (rise>=65 & fall<35 & dq>=80,
-5/+12/20d bracket)

{gr.to_string(index=False)}

Read score_calibration.csv for the full precision/recall/lift grid.
""")
    print("DONE", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
