"""Item 5 (owner order 2026-09-10) — do the ASH08 / AshStocks formulas earn
a place in AM07's selection, as EXTRA FILTERS or as a combined score?

The owner's framing, verbatim: "adopt all 3 formula n show me here how it
could with there 3 and your own ... more formula means for filters - wire
it in last after discussion and confirmation from me".

So the question is not "whose score looks prettiest today". It is:
  (a) do the formulas actually disagree with ours, out of sample?
  (b) among the names AM07 would already buy, does a second formula's
      approval improve what happens next?
  (c) could one of theirs replace or blend into ours?

DESIGN — identical discipline to every other decision on this repo:
  * committed 191x12y panel (data/history_upstox_export_2026-07-19.zip)
  * PRODUCTION functions for AM07's score (the anti-divergence rule that
    kept BT01-BT08 honest) — momentum_score + quality_score + blend 0.35
  * monthly observation points, trailing window only (no lookahead)
  * TRAIN to 2020-06-30, TEST after; anything chosen is chosen on train
  * outcome measured under the LIVE bracket (-5% stop / +20% target /
    200-day max hold, close basis, first touch) — i.e. the money, not a
    raw forward return

SURVIVORSHIP: the panel is today's survivors, as in every study here. The
comparison is fair because both sides of every filter run on the same panel.

Run:  python scripts/ash_formula_study.py
Out:  research/ash_formula_study/{observations.csv.gz,summary.md,*.csv}
"""
from __future__ import annotations

import io
import os
import sys
import zipfile

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from trading.quality import blend_scores, quality_score            # noqa: E402
from trading.strategies.nifty200_momentum import momentum_score    # noqa: E402

ZIP = os.path.join(ROOT, "data", "history_upstox_export_2026-07-19.zip")
OUT = os.path.join(ROOT, "research", "ash_formula_study")

SPLIT = pd.Timestamp("2020-06-30")
FLOOR = 62.0          # live momentum floor (owner order 2026-07-30)
QUAL_W = 0.35         # live quality blend
STOP, TARGET, MAXHOLD = -0.05, 0.20, 200   # live bracket (Node 12, 2026-08-24)
WIN = 300             # trailing bars handed to the scorers


# ---------------------------------------------------------------- formulas
def ash08_score(close_win: pd.Series):
    """ASH08 advisory.py, read from that repo's own source.

    score = 0.65 * clip(50 + mom6 * 200) + 0.35 * quality
    where their `quality` is 100 * n_bars / 126 capped at 100 — DATA
    COVERAGE, not company quality. In their own committed snapshot all 191
    names carry quality = 100.0, one distinct value. Their momentum window
    is 182 CALENDAR days with a 120-day minimum span.
    """
    c = close_win.dropna()
    if len(c) < 2:
        return None
    last_d = c.index[-1]
    w = c[c.index >= last_d - pd.Timedelta(days=182)]
    if w.empty or (last_d - w.index[0]).days < 120 or float(w.iloc[0]) <= 0:
        return None
    mom6 = float(w.iloc[-1]) / float(w.iloc[0]) - 1.0
    mom_s = max(0.0, min(100.0, 50.0 + mom6 * 200.0))
    cover = max(0.0, min(100.0, 100.0 * len(c) / 126.0))
    return 0.65 * mom_s + 0.35 * cover


# AshStocks' python brain is NOT implemented separately here: read from
# lib/, its momentum is 50 + avg(r6/v6, r12/v12) * 25 and its quality is
# (low-vol + liquidity) / 2 — the SAME arithmetic as AM07's momentum_score
# and quality_score. Spearman rho measured +1.000 over the panel. A formula
# that ranks identically cannot filter anything, so what is tested here is
# AshStocks' LIVE rule instead: SEL_MOMENTUM_20_TOP10, a 20-session
# momentum rank with no floor.


def bracket_outcome(close_fwd: np.ndarray, entry: float):
    """First touch under the live bracket, close basis -> (return, reason).
    Unresolved at the panel edge returns (None, 'unresolved') and is dropped
    — an unfinished trade is not a result."""
    hi, lo = entry * (1 + TARGET), entry * (1 + STOP)
    for i in range(min(len(close_fwd), MAXHOLD)):
        px = close_fwd[i]
        if px <= lo:
            return STOP, "stop"
        if px >= hi:
            return TARGET, "target"
    if len(close_fwd) >= MAXHOLD:
        return float(close_fwd[MAXHOLD - 1] / entry - 1.0), "maxhold"
    return None, "unresolved"


def spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman = Pearson on ranks (scipy is not a dependency here)."""
    return float(a.rank().corr(b.rank()))


# ---------------------------------------------------------------- build
def load_panel() -> dict:
    z = zipfile.ZipFile(ZIP)
    frames = {}
    for n in z.namelist():
        if not n.lower().endswith(".csv"):
            continue
        d = pd.read_csv(io.BytesIO(z.read(n)), parse_dates=["Date"])
        d = d.set_index("Date").sort_index()
        if len(d) >= 600:
            frames[n[:-4]] = d
    return frames


def build() -> pd.DataFrame:
    frames = load_panel()
    print(f"panel: {len(frames)} tickers", file=sys.stderr)

    all_idx = pd.DatetimeIndex(sorted(set().union(*[set(d.index) for d in frames.values()])))
    obs = pd.DatetimeIndex(
        pd.Series(all_idx, index=all_idx).resample("ME").last().dropna().values)
    print(f"observation months: {len(obs)} "
          f"({obs[0].date()} -> {obs[-1].date()})", file=sys.stderr)

    rows = []
    for t, d in frames.items():
        close, vol = d["Close"], d["Volume"]
        arr = close.to_numpy(dtype=float)
        pos = {ts: i for i, ts in enumerate(close.index)}
        for ts in obs:
            i = pos.get(ts)
            if i is None or i < 260:
                continue
            cw, vw = close.iloc[max(0, i - WIN):i + 1], vol.iloc[max(0, i - WIN):i + 1]
            mom = momentum_score(cw)
            entry = arr[i]
            if mom is None or entry <= 0:
                continue
            fwd = arr[i + 1:]
            br, why = bracket_outcome(fwd, entry)
            rows.append(dict(
                date=ts, ticker=t,
                am07=blend_scores(mom, quality_score(cw, vw), QUAL_W),
                am07_mom=mom, ash08=ash08_score(cw),
                # AM07's own absolute-momentum gate: close > close 127 bars ago
                abs_ok=bool(arr[i] > arr[i - 127]),
                m20=arr[i] / arr[i - 21] - 1.0,
                bracket=br if br is not None else np.nan, exit_why=why,
            ))
    R = pd.DataFrame(rows)
    R["m20_pct"] = R.groupby("date")["m20"].rank(pct=True)
    print(f"observations: {len(R):,}", file=sys.stderr)
    return R


# ---------------------------------------------------------------- read
FILTERS = {
    "(none) AM07 alone": lambda x: x.index == x.index,
    "+ ASH08 >= 68 (their floor)": lambda x: x.ash08 >= 68,
    "+ ASH08 >= 75": lambda x: x.ash08 >= 75,
    "+ ASH08 >= 80": lambda x: x.ash08 >= 80,
    "+ 20d mom top 10% (AshStocks live rule)": lambda x: x.m20_pct >= 0.90,
    "+ 20d mom top 25%": lambda x: x.m20_pct >= 0.75,
    "+ 20d mom top 50%": lambda x: x.m20_pct >= 0.50,
    "+ 20d mom BOTTOM 50%": lambda x: x.m20_pct <= 0.50,
    "+ 20d mom BOTTOM 25%": lambda x: x.m20_pct <= 0.25,
    "+ ASH08>=75 AND 20d top 25%": lambda x: (x.ash08 >= 75) & (x.m20_pct >= 0.75),
}


def filter_table(cand: pd.DataFrame) -> pd.DataFrame:
    tr, te = cand[cand.date <= SPLIT], cand[cand.date > SPLIT]
    out = []
    for label, fn in FILTERS.items():
        a, b = tr[fn(tr)], te[fn(te)]
        out.append(dict(
            filter=label, n_train=len(a), win_train=100 * (a.exit_why == "target").mean(),
            ret_train=100 * a.bracket.mean(), n_test=len(b),
            win_test=100 * (b.exit_why == "target").mean(),
            ret_test=100 * b.bracket.mean()))
    t = pd.DataFrame(out)
    t["edge_train"] = t.ret_train - t.ret_train.iloc[0]
    t["edge_test"] = t.ret_test - t.ret_test.iloc[0]
    return t


def combo_table(R: pd.DataFrame) -> pd.DataFrame:
    d = R.dropna(subset=["am07", "ash08", "m20", "bracket"]).copy()
    g = d.groupby("date")
    d["r_am"], d["r_a8"], d["r_m20"] = (g["am07"].rank(pct=True),
                                        g["ash08"].rank(pct=True),
                                        g["m20"].rank(pct=True))
    combos = {
        "AM07 alone (live)": d.r_am,
        "AM07 + ASH08 (50/50)": (d.r_am + d.r_a8) / 2,
        "AM07 + AshStocks-20d (50/50)": (d.r_am + d.r_m20) / 2,
        "all three equal (1/3 each)": (d.r_am + d.r_a8 + d.r_m20) / 3,
        "AM07 60 / ASH08 20 / 20d 20": 0.6 * d.r_am + 0.2 * d.r_a8 + 0.2 * d.r_m20,
        "AM07 + INVERSE 20d (50/50)": (d.r_am + (1 - d.r_m20)) / 2,
    }
    out = []
    for name, s in combos.items():
        d["_s"] = s
        top = d[d.groupby("date")["_s"].rank(ascending=False, method="first") <= 30]
        out.append(dict(combination=name,
                        ret_train=100 * top[top.date <= SPLIT].bracket.mean(),
                        ret_test=100 * top[top.date > SPLIT].bracket.mean()))
    t = pd.DataFrame(out)
    t["edge_test"] = t.ret_test - t.ret_test.iloc[0]
    return t


def robustness(cand: pd.DataFrame) -> tuple:
    """The bar that killed sector-heat and FII-S5: does it hold per YEAR and
    per MONTH, or is it a pooled average carried by a few stretches?"""
    cand = cand.copy()
    cand["yr"] = cand.date.dt.year
    yr_rows = []
    for yr, d in cand.groupby("yr"):
        f = d[d.m20_pct <= 0.25]
        if len(f) < 20:
            continue
        yr_rows.append(dict(
            year=int(yr), half="train" if pd.Timestamp(f"{yr}-07-01") <= SPLIT else "test",
            n_base=len(d), ret_base=100 * d.bracket.mean(),
            n_filtered=len(f), ret_filtered=100 * f.bracket.mean(),
            edge=100 * (f.bracket.mean() - d.bracket.mean())))
    yr = pd.DataFrame(yr_rows)

    mon_rows = []
    for lab, d in [("train", cand[cand.date <= SPLIT]), ("test", cand[cand.date > SPLIT])]:
        bm = d.groupby("date").bracket.mean()
        fm = d[d.m20_pct <= 0.25].groupby("date").bracket.mean()
        j = pd.concat([bm.rename("base"), fm.rename("filt")], axis=1).dropna()
        diff = 100 * (j.filt - j.base)
        mon_rows.append(dict(half=lab, months=len(j), mean_edge=diff.mean(),
                             median_edge=diff.median(),
                             pct_months_positive=100 * (diff > 0).mean()))
    return yr, pd.DataFrame(mon_rows)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    R = build()
    R.to_csv(os.path.join(OUT, "observations.csv.gz"), index=False)

    sub = R.dropna(subset=["am07", "ash08", "m20"])
    corr = pd.DataFrame([
        dict(pair="AM07 score vs ASH08 score", spearman=spearman(sub.am07, sub.ash08)),
        dict(pair="AM07 momentum leg vs ASH08", spearman=spearman(sub.am07_mom, sub.ash08)),
        dict(pair="AM07 score vs 20-day momentum", spearman=spearman(sub.am07, sub.m20)),
    ])

    cand = R[(R.am07 >= FLOOR) & (R.abs_ok)].dropna(subset=["bracket"]).copy()
    ft, ct = filter_table(cand), combo_table(R)
    yr, mon = robustness(cand)

    for name, t in [("correlations", corr), ("filters", ft), ("combinations", ct),
                    ("robust_by_year", yr), ("robust_by_month", mon)]:
        t.to_csv(os.path.join(OUT, f"{name}.csv"), index=False)
        print(f"\n=== {name} ===")
        print(t.to_string(index=False))

    keep = cand[cand.m20_pct <= 0.25]
    print(f"\ncandidates per month: AM07 alone median "
          f"{cand.groupby('date').size().median():.0f}, "
          f"with bottom-25% filter median {keep.groupby('date').size().median():.0f}")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
