"""Option A — does SECTOR HEAT justify exiting the whole segment?

Owner ask 2026-08-29 ("how will app algorithm decide to exit the effected
stock/segement"), then "go for a and c". C shipped as a display + button.
A moves money, so it is measured first — the owner's own
REQUIRES_15Y_BACKTEST rule, the one under which 112 triggers were measured
and only 10 were wired into the danger layer.

THE QUESTION, stated so it can only be answered yes or no:

  T12W_0094 (sector contagion) is already VERIFIED per stock — lift 1.546
  over 6,982 fires in the 2026-08-13 external run. It fires on ONE stock at
  a time, tightening that stock's stop. The basket claim is different and
  has never been tested:

    when K names in one sector fire together, do the OTHER holdings in that
    sector — the ones news and triggers did NOT name — also fall?

  If yes, cutting the segment beats cutting the named names, and A earns a
  place. If no, the named-names exit the danger layer already does is
  enough, and A is a reshuffle dressed as risk control.

METHOD — deliberately identical to BT01-BT08 and the external run so the
numbers are comparable to the evidence already in this repo:
  * the committed 191-name x 12-year panel, PanelSet(tail=0)
  * the PRODUCTION trigger function `_e0094` against PRODUCTION panels via
    `externals_at(ticker, when)` — nothing is reimplemented, so this cannot
    measure something the app does not compute
  * evaluation every 5 trading days, 300 bars of own history required
  * forward window 20 trading days
  * TRAIN/TEST SPLIT at 2020-06-30. A conclusion is only reported if it
    holds on the test half. The threshold sweep of 2026-08-13 is why: three
    of six near-misses were pure curve fits that died out of sample.

Runtime note: _e0094 needs only the price context and the sector panel — no
family counts, no 79-trigger pass — so this run is far cheaper than the
external calibration. Checkpointed per ticker anyway; two earlier runs in
this repo were killed by container restarts.

Usage:  python scripts/sector_heat_study.py out_dir/
Writes: sector_heat.csv (per bucket, per half), rows.csv.gz, summary.md
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
from trading.triggers import _Ctx, _EXT_FNS, _EXT_MIN_BARS         # noqa: E402

STEP = 5                 # eval every 5 trading days (BT01-BT08's grid)
MIN_BARS = 300           # bars of own history before a stock is evaluated
FWD = 20                 # forward window
SPLIT = pd.Timestamp("2020-06-30")
CKPT_EVERY = 10

_ohlc_cache: dict = {}


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
    if len(_ohlc_cache) > 3:
        _ohlc_cache.clear()
    _ohlc_cache[ticker] = df
    return df


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ckpt_path = out / "checkpoint.json"

    print("building full-history panels...", flush=True)
    ps = XP.PanelSet(tail=0)
    panel = ps.panel
    sector_of = {}
    for sector, names in (ps.by_sector or {}).items():
        for n in names:
            sector_of[n] = sector
    print(f"panel: {len(panel)} tickers, {len(ps.close)} bars "
          f"{ps.close.index[0].date()} -> {ps.close.index[-1].date()}, "
          f"{len(ps.by_sector or {})} sectors", flush=True)

    fn0094 = _EXT_FNS["T12W_0094"]

    rows: list[tuple] = []          # (date, sector, ticker, fired, fwd20)
    done: set = set()
    if ckpt_path.exists():
        try:
            state = json.loads(ckpt_path.read_text())
            rows = [tuple(r) for r in state["rows"]]
            done = set(state["done"])
            print(f"resumed: {len(done)} tickers, {len(rows)} rows", flush=True)
        except Exception as e:
            print(f"checkpoint unreadable ({e}) — clean start", flush=True)

    def _flush():
        ckpt_path.write_text(json.dumps({"rows": rows, "done": sorted(done)}))

    todo = sorted(panel)
    for k, tick in enumerate(todo):
        if tick in done:
            continue
        sector = sector_of.get(tick)
        if not sector:
            done.add(tick)
            continue
        df = panel[tick]
        if len(df) < MIN_BARS + FWD + 50:
            done.add(tick)
            continue
        close = df["close"].to_numpy(dtype=float)
        n = len(df)
        fwd20 = np.full(n, np.nan)
        fwd20[:-FWD] = close[FWD:] / close[:-FWD] - 1
        ohlc = _ohlc_for(tick)
        if ohlc is None:
            done.add(tick)
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
            ext = ps.externals_at(tick, when)
            try:
                fired = fn0094(x, ext, {}, {})
            except Exception:
                fired = None
            if fired is None:
                continue                       # DATA_NEEDED, not a False
            r = fwd20[i]
            if not np.isfinite(r):
                continue
            rows.append((when.isoformat()[:10], sector, tick,
                         bool(fired), float(r)))
        done.add(tick)
        if (k + 1) % CKPT_EVERY == 0:
            _flush()
            print(f"  {len(done)}/{len(todo)} tickers, {len(rows)} rows",
                  flush=True)
    _flush()

    df = pd.DataFrame(rows, columns=["date", "sector", "ticker",
                                     "fired", "fwd20"])
    print(f"evaluated {len(df):,} stock-days", flush=True)
    df.to_csv(out / "rows.csv.gz", index=False, compression="gzip")

    # ---- the basket question -------------------------------------------
    # k = how many names in this sector fired on this date.
    heat = (df.groupby(["date", "sector"])["fired"].sum()
              .rename("k").reset_index())
    df = df.merge(heat, on=["date", "sector"], how="left")
    df["half"] = np.where(pd.to_datetime(df["date"]) <= SPLIT,
                          "train", "test")
    df["bucket"] = np.where(df["k"] >= 3, "k>=3",
                     np.where(df["k"] == 2, "k=2",
                       np.where(df["k"] == 1, "k=1", "k=0")))

    recs = []
    for half in ("train", "test"):
        h = df[df["half"] == half]
        for bucket in ("k=0", "k=1", "k=2", "k>=3"):
            b = h[h["bucket"] == bucket]
            for who, sel in (("fired", b[b["fired"]]),
                             ("not_fired", b[~b["fired"]])):
                if sel.empty:
                    continue
                recs.append({
                    "half": half, "bucket": bucket, "who": who,
                    "n": len(sel),
                    "mean_fwd20_pct": round(sel["fwd20"].mean() * 100, 3),
                    "median_fwd20_pct": round(sel["fwd20"].median() * 100, 3),
                    "p_fall10": round((sel["fwd20"] <= -0.10).mean(), 4),
                    "p_collapse20": round((sel["fwd20"] <= -0.20).mean(), 4),
                })
    res = pd.DataFrame(recs)
    res.to_csv(out / "sector_heat.csv", index=False)

    # ---- verdict --------------------------------------------------------
    def _get(half, bucket, who, col):
        m = res[(res.half == half) & (res.bucket == bucket) & (res.who == who)]
        return float(m[col].iloc[0]) if len(m) else float("nan")

    lines = [
        "# Sector heat — does clustering justify exiting the whole segment?",
        "",
        f"Panel {len(panel)} names, evaluated {len(df):,} stock-days, "
        f"split at {SPLIT.date()} (train <= split < test).",
        "",
        "`k` = how many names in the stock's own sector had T12W_0094 "
        "(sector contagion) fire on that date.",
        "",
        "## The claim being tested",
        "",
        "If cutting the whole segment beats cutting the named names, then "
        "the NOT-FIRED holdings in a high-k sector must fall too. If their "
        "forward return is no worse than at k=0, the basket exit sells "
        "healthy positions for nothing.",
        "",
        "## Not-fired names — the ones a basket exit would additionally sell",
        "",
        "| half | k | n | mean fwd20 % | P(fall>=10%) | P(collapse>=20%) |",
        "|---|---|---|---|---|---|",
    ]
    for half in ("train", "test"):
        for bucket in ("k=0", "k=1", "k=2", "k>=3"):
            m = res[(res.half == half) & (res.bucket == bucket)
                    & (res.who == "not_fired")]
            if not len(m):
                continue
            r = m.iloc[0]
            lines.append(f"| {half} | {bucket} | {int(r.n):,} | "
                         f"{r.mean_fwd20_pct:+.2f} | {r.p_fall10:.3f} | "
                         f"{r.p_collapse20:.3f} |")
    lines += ["", "## Fired names — the incumbent per-stock signal, for scale",
              "", "| half | k | n | mean fwd20 % | P(fall>=10%) |",
              "|---|---|---|---|---|"]
    for half in ("train", "test"):
        for bucket in ("k=1", "k=2", "k>=3"):
            m = res[(res.half == half) & (res.bucket == bucket)
                    & (res.who == "fired")]
            if not len(m):
                continue
            r = m.iloc[0]
            lines.append(f"| {half} | {bucket} | {int(r.n):,} | "
                         f"{r.mean_fwd20_pct:+.2f} | {r.p_fall10:.3f} |")

    base_tr = _get("train", "k=0", "not_fired", "mean_fwd20_pct")
    base_te = _get("test", "k=0", "not_fired", "mean_fwd20_pct")
    hot_tr = _get("train", "k>=3", "not_fired", "mean_fwd20_pct")
    hot_te = _get("test", "k>=3", "not_fired", "mean_fwd20_pct")
    lines += [
        "", "## Verdict", "",
        f"Not-fired names, k>=3 vs k=0: train {hot_tr:+.2f}% vs "
        f"{base_tr:+.2f}% (gap {hot_tr - base_tr:+.2f}pp), "
        f"test {hot_te:+.2f}% vs {base_te:+.2f}% (gap {hot_te - base_te:+.2f}pp).",
        "",
        "A basket exit is earned only if BOTH halves show the not-fired "
        "names materially worse in a hot sector. A gap that appears in "
        "train and vanishes in test is the curve fit this repo has already "
        "been bitten by (threshold sweep, 2026-08-13).",
    ]
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[-6:]), flush=True)
    print(f"\nwrote {out}/sector_heat.csv and summary.md", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "research/sector_heat_study")
