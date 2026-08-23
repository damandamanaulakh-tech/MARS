"""BT01-BT08 for the EXTERNAL-DATA triggers — the ones wired 2026-08-13.

Owner order: "merge it and run the calibration".

The OHLCV side of the owner's trigger layer was calibrated on 2026-08-10
(scripts/bt_calibration_v1.py, research/bt_calibration/). The 21 external
triggers could not be calibrated then, because the sector panel, delivery
percentages, F&O open interest and deal files were not in the repo. They are
now, so this run puts those triggers through the SAME design: same event
labels, same evaluation grid, same lift definition, same proof tags — so a
number here is directly comparable to a number there.

Same anti-divergence rule as v1: this script calls the PRODUCTION trigger
functions (`trading.triggers` ext_fn) against PRODUCTION panels
(`trading.external_panels.PanelSet.externals_at`, the exact call the live
layer makes, only with a past as-of date). Nothing is reimplemented here, so
the calibration cannot measure something the app does not compute.

WHAT IS DIFFERENT FROM v1, and it matters when reading the output:
  * Each external panel has its own start date, so each trigger has its own
    usable window — the F&O open-interest triggers can only be judged from
    2024-07 (about 2 years), delivery from 2019-06, and the sector/RS family
    over the whole 12-year panel. Every row reports its own first and last
    evaluation date and its evaluation count; a small sample is labelled
    NOT_ENOUGH_EVIDENCE rather than dressed up as a verdict.
  * T12W_0111 / T12W_0112 (bulk-deal follow-through and institutional sell
    warning) are expected to record almost no evaluations. Every bulk-deal
    month in the committed file sits at NSE's 70-row cap, so the panel
    correctly reports DATA_NEEDED instead of a quiet False, and a trigger that
    cannot be evaluated cannot be calibrated. That is the honest result, not a
    bug — a complete paged pull would fill it in with no code change.

ASSUMED, inherited verbatim from v1 so the two runs stay comparable:
  event labels on forward adjusted closes (RISE fwd20 >= +10%, FALL <= -10%,
  COLLAPSE <= -20%, REVERSAL_UP fwd10 <= -5% and fwd20 >= +5%); evaluation
  every 5 trading days; a hit is a fire at t with the label set anywhere in
  (t, t+10]; lead time is days to the first labelled day, capped at 20; proof
  tags VERIFIED lift >= 1.5 and fires >= 30, SUPPORTED lift >= 1.1,
  NOT_ENOUGH_EVIDENCE fires < 30, else REJECTED.

Usage:  python scripts/bt_calibration_external_v1.py out_dir/
Writes: external_trigger_calibration.csv, summary.md
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading import external_panels as XP                          # noqa: E402
from trading.triggers import (CHAIN_FED, EXTERNAL_WIRED, NEWS_FED, _Ctx,  # noqa: E402
                             _EXT_MIN_BARS, family_counts, COMPUTABLE)

STEP = 5                # eval every 5 trading days (v1's grid)
MIN_BARS = 300          # bars of own history before a stock is evaluated
FWD = 20                # forward window for the labels
RISE_T, FALL_T, COLLAPSE_T = 0.10, -0.10, -0.20


def label_events(close: np.ndarray) -> dict[str, np.ndarray]:
    """BT01 labels, identical to v1."""
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

    print("building full-history panels...", flush=True)
    ps = XP.PanelSet(tail=0)          # 0 = full history, not the live tail
    panel = ps.panel
    print(f"panel: {len(panel)} tickers, {len(ps.close)} bars "
          f"{ps.close.index[0].date()} -> {ps.close.index[-1].date()}", flush=True)
    print(f"sectors: {len(ps.by_sector)} · delivery symbols: "
          f"{len(ps.delivery)} · F&O symbols: {len(ps.fo)} · "
          f"deal symbols: {len(ps.deals)}", flush=True)

    # Checkpointing: two earlier attempts at this run were killed mid-pass by
    # container restarts, losing everything. State is now flushed every
    # CKPT_EVERY tickers and reloaded on start, so a restart resumes instead of
    # starting over. Delete the file to force a clean run.
    ckpt_path = out / "checkpoint.json"

    sides = {t.id: t.side for t in EXTERNAL_WIRED}
    fires: dict[str, int] = defaultdict(int)
    evals: dict[str, int] = defaultdict(int)
    hits: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fp: dict[str, int] = defaultdict(int)
    leads: dict[str, list[int]] = defaultdict(list)
    first_eval: dict[str, str] = {}
    last_eval: dict[str, str] = {}
    base_windows: dict[str, int] = defaultdict(int)
    n_windows = 0
    done: set[str] = set()

    if ckpt_path.exists():
        try:
            ck = json.loads(ckpt_path.read_text())
            fires.update(ck["fires"]); evals.update(ck["evals"])
            fp.update(ck["fp"]); base_windows.update(ck["base_windows"])
            for tid, d in ck["hits"].items():
                hits[tid].update(d)
            for tid, v in ck["leads"].items():
                leads[tid].extend(v)
            first_eval.update(ck["first_eval"]); last_eval.update(ck["last_eval"])
            n_windows = ck["n_windows"]; done = set(ck["done"])
            print(f"resumed from checkpoint: {len(done)} tickers already done, "
                  f"{n_windows:,} windows", flush=True)
        except Exception as e:
            print(f"checkpoint unreadable ({e}) — starting clean", flush=True)

    def _flush() -> None:
        ckpt_path.write_text(json.dumps({
            "fires": dict(fires), "evals": dict(evals), "fp": dict(fp),
            "hits": {k2: dict(v2) for k2, v2 in hits.items()},
            "leads": {k2: list(v2) for k2, v2 in leads.items()},
            "base_windows": dict(base_windows), "n_windows": n_windows,
            "first_eval": first_eval, "last_eval": last_eval,
            "done": sorted(done),
        }))

    CKPT_EVERY = 10
    for k, tick in enumerate(sorted(panel)):
        if tick in done:
            continue
        df = panel[tick]
        if len(df) < MIN_BARS + FWD + 50:
            continue
        close = df["close"].to_numpy(dtype=float)
        ev = label_events(close)
        n = len(df)
        # The PanelSet carries close+volume; the trigger context also needs
        # High/Low (the base and support helpers use them), so read the full
        # OHLCV for this one ticker from the same committed zip.
        ohlc = _ohlc_for(tick)
        if ohlc is None:
            continue
        for i in range(MIN_BARS, n - FWD, STEP):
            when = df.index[i]
            pos = ohlc.index.searchsorted(when, side="right") - 1
            if pos < _EXT_MIN_BARS:
                continue
            window = ohlc.iloc[:pos + 1].tail(400)
            try:
                x = _Ctx(window)
            except (KeyError, ValueError):
                continue
            n_windows += 1
            future = {e: ev[e][i + 1:i + 11].any() for e in ev}
            future20 = {e: ev[e][i + 1:i + 21].any() for e in ev}
            for e, yes in future.items():
                if yes:
                    base_windows[e] += 1

            ext = ps.externals_at(tick, when)
            # The two deal composites are the only triggers needing the OHLCV
            # family counts, and they are DATA_NEEDED whenever the bulk source
            # is capped — so compute the 79-trigger pass only when they can
            # actually be evaluated.
            fam: dict = {}
            oh: dict = {}
            if (ext.deal_buy_recent is not None or
                    ext.deal_sell_recent is not None):
                oh = {}
                for ct in COMPUTABLE:
                    try:
                        oh[ct.id] = ct.fn(x)
                    except Exception:
                        oh[ct.id] = None
                fam = family_counts(oh)
            for t in EXTERNAL_WIRED:
                # The nine news-fed triggers are skipped on purpose, not by
                # omission: no archive of past headlines exists, so a past
                # window has no news to read and any "result" here would be
                # the price leg alone masquerading as a calibrated verdict.
                if t.id in NEWS_FED or t.id in CHAIN_FED:
                    # no backward archive exists for news or option chains —
                    # a past window has neither, so any "result" would be
                    # the price leg alone wearing a calibrated verdict.
                    continue
                try:
                    r = t.ext_fn(x, ext, fam, oh)
                except Exception:
                    r = None
                if r is None:
                    continue
                evals[t.id] += 1
                first_eval.setdefault(t.id, str(when.date()))
                last_eval[t.id] = str(when.date())
                if not r:
                    continue
                fires[t.id] += 1
                matched = False
                for e, yes in future.items():
                    if yes:
                        hits[t.id][e] += 1
                        matched = True
                if not matched and not any(future20.values()):
                    fp[t.id] += 1
                key = sides[t.id]
                seg = ev.get(key)
                if seg is not None:
                    s = seg[i + 1:i + 21]
                    if s.any():
                        leads[t.id].append(int(np.argmax(s)) + 1)
        done.add(tick)
        if len(done) % CKPT_EVERY == 0:
            _flush()
        if (k + 1) % 25 == 0:
            print(f"{k + 1}/{len(panel)} tickers done "
                  f"({n_windows:,} windows)", flush=True)

    _flush()
    base_rate = {e: (base_windows[e] / n_windows if n_windows else 0.0)
                 for e in base_windows}
    rows = []
    for t in EXTERNAL_WIRED:
        ev_n, f = evals[t.id], fires[t.id]
        key = t.side if t.side in ("RISE", "FALL", "COLLAPSE") else "REVERSAL_UP"
        own = hits[t.id].get(key, 0)
        hit_rate = own / f if f else 0.0
        lift = (hit_rate / base_rate[key]) if f and base_rate.get(key) else None
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
            "side": t.side, "evaluations": ev_n, "fires": f,
            "fire_rate_pct": round(100 * f / ev_n, 2) if ev_n else "",
            "own_side_hits": own,
            "hit_rate_pct": round(100 * hit_rate, 2),
            "baseline_rate_pct": round(100 * base_rate.get(key, 0), 2),
            "lift": round(lift, 3) if lift is not None else "",
            "false_positive_rate_pct":
                round(100 * fp[t.id] / f, 2) if f else "",
            "median_lead_days":
                float(np.median(leads[t.id])) if leads[t.id] else "",
            "proof_tag": tag,
            "first_eval": first_eval.get(t.id, ""),
            "last_eval": last_eval.get(t.id, ""),
            "data_note": _data_note(t.id, ev_n),
        })
    cal = pd.DataFrame(rows)
    cal["_sort"] = pd.to_numeric(cal["lift"], errors="coerce").fillna(-1)
    cal = cal.sort_values(["side", "_sort"], ascending=[True, False]) \
             .drop(columns="_sort")
    cal.to_csv(out / "external_trigger_calibration.csv", index=False)

    ver = cal[cal.proof_tag == "VERIFIED"]
    sup = cal[cal.proof_tag == "SUPPORTED"]
    rej = cal[cal.proof_tag == "REJECTED"]
    nee = cal[cal.proof_tag == "NOT_ENOUGH_EVIDENCE"]
    with open(out / "summary.md", "w") as fh:
        fh.write(f"""# External-trigger calibration (BT01-BT08) — run {pd.Timestamp.now().date()}

The 21 triggers wired on 2026-08-13, put through the SAME design as the
2026-08-10 OHLCV run so the numbers are comparable. Production code
throughout: `triggers.ext_fn` against `external_panels.PanelSet.externals_at`,
the live call with a past as-of date.

Panel: {len(panel)} tickers, {len(ps.close)} bars
{ps.close.index[0].date()} -> {ps.close.index[-1].date()}. Grid: every {STEP}
trading days, {n_windows:,} windows. Sectors: {len(ps.by_sector)}.

Event base rates per 10d window: """
                 + ", ".join(f"{e} {100*r:.1f}%" for e, r in base_rate.items())
                 + f"""

## Verdicts
- VERIFIED (lift>=1.5, fires>=30): {len(ver)}
- SUPPORTED (lift>=1.1):           {len(sup)}
- REJECTED (lift<1.1):             {len(rej)}
- NOT_ENOUGH_EVIDENCE (<30 fires): {len(nee)}

Each trigger's own usable window is in the CSV (`first_eval`, `last_eval`,
`evaluations`) because the panels start on different dates — F&O open
interest only exists from 2024-07, delivery from 2019-06, while the
sector/RS family spans the whole panel. Read a verdict against its sample.

## Ranked by measured lift
""")
        for _, r in cal.iterrows():
            lift = r["lift"] if r["lift"] != "" else "n/a"
            lead = (f", lead {r['median_lead_days']:.0f}d"
                    if r["median_lead_days"] != "" else "")
            fh.write(f"- **{r['trigger_id']}** {r['name']} [{r['side']}] — "
                     f"lift {lift}, {r['fires']} fires of {r['evaluations']} "
                     f"evals{lead} → {r['proof_tag']}"
                     + (f" ({r['data_note']})" if r["data_note"] else "") + "\n")
        fh.write("""
## What this does NOT license
Nothing here moves money. The owner's own rule (F-006, and the
REQUIRES_15Y_BACKTEST column in their sheet) keeps the entry side out of the
money path; the danger layer that went live on 2026-08-11 is sell-side only
and was earned by the OHLCV run's strict-label verdicts. A VERIFIED tag here
is a candidate for that same treatment, on the owner's word, not an automatic
promotion.
""")
    print("DONE", flush=True)


_ohlc_cache: dict[str, pd.DataFrame] = {}


def _ohlc_for(ticker: str):
    """Full OHLCV for one panel name (the trigger context needs High/Low)."""
    if ticker in _ohlc_cache:
        return _ohlc_cache[ticker]
    import io
    import zipfile
    zpath = ROOT / "data" / "history_upstox_export_2026-07-19.zip"
    try:
        with zipfile.ZipFile(zpath) as z:
            name = f"{ticker}.csv"
            if name not in set(z.namelist()):
                _ohlc_cache[ticker] = None
                return None
            df = pd.read_csv(io.BytesIO(z.read(name)), parse_dates=["Date"],
                             index_col="Date").sort_index()
    except OSError:
        return None
    if len(_ohlc_cache) > 3:           # keep RAM flat: one ticker at a time
        _ohlc_cache.clear()
    _ohlc_cache[ticker] = df
    return df


def _data_note(tid: str, evals_n: int) -> str:
    if tid in ("T12W_0111", "T12W_0112"):
        return ("bulk-deal source truncated at NSE's 70-row monthly cap — the "
                "panel reports DATA_NEEDED, so there is nothing to calibrate")
    if tid in ("T12W_0100", "T12W_0101"):
        return "F&O open interest only exists from 2024-07 in this repo"
    if tid == "T12W_0060":
        return "delivery percentages only exist from 2019-06 in this repo"
    return ""


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "research/bt_calibration_external")
