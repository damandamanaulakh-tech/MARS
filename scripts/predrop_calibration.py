"""Market pre-drop / pre-rise calibration on the committed data.

Question: does any market-level signal fire BEFORE a Nifty fall (or rise)
with lead, and does it hold on years it was not fitted on?

Event: Nifty forward 20-session return <= -5% (DROP) / >= +5% (RISE).
Signals: computed daily from the 191x12y panel, the 15y index file, FII
cash, FII index-futures positioning, and the 10y yield. Binary fires.
Split: train < 2020-07-01, test >= 2020-07-01. Lift = P(event|fire) /
P(event). Episodes = fire starts (a signal on for 30 days is one warning).
Lead = sessions from fire-start to the first close <= -5% (median).
"""
import io, os, sys, zipfile, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (R-3)
import numpy as np, pandas as pd
from trading.config import Config

R = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")  # repo-relative (R-3)
OUT = os.path.dirname(os.path.abspath(__file__))
SPLIT = pd.Timestamp("2020-07-01")
H = 20

# ---------- panel ----------
z = zipfile.ZipFile(f"{R}/history_upstox_export_2026-07-19.zip")
closes, vols, highs, lows = {}, {}, {}, {}
for n in z.namelist():
    if not n.endswith(".csv"): continue
    df = pd.read_csv(io.BytesIO(z.read(n)), index_col=0, parse_dates=True)
    t = n[:-4]
    closes[t], vols[t], highs[t], lows[t] = df.Close, df.Volume, df.High, df.Low
C = pd.DataFrame(closes).sort_index(); V = pd.DataFrame(vols).reindex(C.index)
sector = Config().sector_map
print("panel", C.shape, C.index[0].date(), C.index[-1].date())

# ---------- index ----------
ix = pd.read_csv(f"{R}/index_daily_15y.csv")
ix["d"] = pd.to_datetime(ix.trade_date, dayfirst=True)
ix = ix.set_index("d").sort_index()
N = ix.nifty_close.astype(float); VIX = ix.india_vix_close.astype(float)
NH, NL = ix.nifty_high.astype(float), ix.nifty_low.astype(float)
idx = C.index.intersection(N.index)
C, V, N, VIX, NH, NL = C.loc[idx], V.loc[idx], N.loc[idx], VIX.loc[idx], NH.loc[idx], NL.loc[idx]

# ---------- FII cash (merged csv history) ----------
from trading.signals import flows
fl = flows.get_flows(max_age_days=0) or []
fii = pd.Series({pd.Timestamp(f.trade_date): f.fii_net for f in fl}).sort_index()
fii = fii.reindex(idx).fillna(0.0)
print("fii rows", len(fl), fii.index[0].date(), "->", fii[fii != 0].index.max().date())

# ---------- FII index futures net (PWOI long + wide) ----------
a = pd.read_csv(f"{R}/fo_participant_oi_2013_2023.csv")
a = a[(a.participant == "FII") & (a["product"] == "Future Index")]
fa = pd.Series(a.net_contracts.values, index=pd.to_datetime(a.trade_date))
b = pd.read_csv(f"{R}/fo_participant_oi_2024_2026.csv")
b = b[b.client_type == "FII"]
fb = pd.Series((b.fut_idx_long - b.fut_idx_short).values, index=pd.to_datetime(b.date))
fiifut = pd.concat([fa, fb]).sort_index()
fiifut = fiifut[~fiifut.index.duplicated()].reindex(idx).ffill()

# ---------- 10y yield ----------
y = pd.read_csv(f"{R}/india_10y_bond_yield.csv")
y.columns = [c.strip('"﻿') for c in y.columns]
# investing.com export = MM/DD/YYYY (confirmed: file ends 2026-06 per data README,
# and "05/29/2026" only parses month-first). A day-first read would silently
# scramble every date past the 12th.
ys = pd.Series(y.Price.astype(float).values,
               index=pd.to_datetime(y.Date, format="%m/%d/%Y")).sort_index()
ys = ys.reindex(idx).ffill()

# ---------- features ----------
# BUG FIX 2026-09-09: denominator = names that HAVE the moving average.
_m200, _m50 = C.rolling(200).mean(), C.rolling(50).mean()
_h200, _h50 = _m200.notna() & C.notna(), _m50.notna() & C.notna()
_n200 = _h200.sum(axis=1).replace(0, np.nan); _n50 = _h50.sum(axis=1).replace(0, np.nan)
above200 = ((C > _m200).where(_h200).sum(axis=1) / _n200).where(_n200 >= 100)
above50 = ((C > _m50).where(_h50).sum(axis=1) / _n50).where(_n50 >= 100)
_h20 = C.rolling(20).max().notna() & C.notna()
_n20 = _h20.sum(axis=1).replace(0, np.nan)
hi20 = (C >= C.rolling(20).max()).where(_h20).sum(axis=1) / _n20
lo20 = (C <= C.rolling(20).min()).where(_h20).sum(axis=1) / _n20
ew = C.pct_change().mean(axis=1)                      # equal-weight panel return
ew20 = (1 + ew).rolling(20).apply(np.prod, raw=True) - 1
n20 = N.pct_change(20)
r3 = C / C.shift(3) - 1
_h3 = r3.rolling(5).min().notna()
tail = (r3.rolling(5).min() <= -0.03).where(_h3).sum(axis=1) / _h3.sum(axis=1).replace(0, np.nan)
panelvol = V.sum(axis=1)
nret = N.pct_change()
dist_day = ((nret <= -0.002) & (panelvol > panelvol.shift(1))).astype(int)
dist25 = dist_day.rolling(25).sum()
vix_p90 = VIX.rolling(120).quantile(0.9)
rv10 = nret.rolling(10).std() * np.sqrt(252); rv60 = nret.rolling(60).std() * np.sqrt(252)
ma50, ma200 = N.rolling(50).mean(), N.rolling(200).mean()
tr = pd.concat([NH - NL, (NH - N.shift()).abs(), (NL - N.shift()).abs()], axis=1).max(axis=1)
atr5, atr60 = tr.rolling(5).mean(), tr.rolling(60).mean()
fii5 = fii.rolling(5).sum()
def sec_ret(names, n=20):
    cols = [t for t in C.columns if sector.get(t) in names]
    return (1 + C[cols].pct_change().mean(axis=1)).rolling(n).apply(np.prod, raw=True) - 1
defens = sec_ret({"FMCG", "Pharma"}); cycl = sec_ret({"Financial", "Auto", "Metal", "Capital Goods"})
vixpeak120 = VIX.rolling(120).max()

S = {
 # ---- pre-DROP candidates ----
 "breadth200 < 40%":            above200 < 0.40,
 "breadth50 < 30%":             above50 < 0.30,
 "breadth divergence (Nifty near 60d high, breadth50 -10pp/10d)":
     (N >= N.rolling(60).max() * 0.99) & ((above50 - above50.shift(10)) <= -0.10),
 "new lows - new highs > 20pp":  (lo20 - hi20) > 0.20,
 "distribution days >= 5 in 25": dist25 >= 5,
 "damage cluster tail_down3_5d >= 0.213 (owner's)": tail >= 0.213,
 "VIX > 120d p90 and rising":    (VIX > vix_p90) & (VIX > VIX.shift(5)),
 "VIX +15%/5d while Nifty flat": (VIX / VIX.shift(5) - 1 >= 0.15) & (N.pct_change(5).abs() < 0.01),
 "realised vol 10d > 1.5x 60d":  rv10 > 1.5 * rv60,
 "Nifty < 200DMA":               N < ma200,
 "death cross fresh (<=5d)":     ((ma50 < ma200) & (ma50.shift(5) >= ma200.shift(5))),
 "momentum-crash setup (2y ret<0 & vol high)":
     (N / N.shift(504) - 1 < 0) & (rv60 > rv60.rolling(500).quantile(0.6)),
 "FII cash 5d < -5000 Cr":       fii5 < -5000,
 "FII idx-fut net short & worsening 20d": (fiifut < 0) & (fiifut < fiifut.shift(20) * 1.2),
 "leadership rot (EW panel 20d - Nifty 20d < -3pp)": (ew20 - n20) < -0.03,
 "defensives lead cyclicals by 3pp/20d": (defens - cycl) > 0.03,
 "10y yield +25bp in 20d":       (ys - ys.shift(20)) >= 0.25,
 "Nifty ATR 5d > 1.5x 60d":      atr5 > 1.5 * atr60,
 # ---- pre-RISE candidates ----
 "[add] breadth thrust (breadth50 +20pp/10d)": (above50 - above50.shift(10)) >= 0.20,
 "[add] VIX crush (<= 75% of 120d peak, fresh)": (VIX <= 0.75 * vixpeak120) & (VIX.shift(1) > 0.75 * vixpeak120.shift(1)),
 "[add] Nifty reclaims 200DMA (fresh)": (N > ma200) & (N.shift(1) <= ma200.shift(1)),
 "[add] FII cash 5d > +5000 Cr":  fii5 > 5000,
 "[add] damage cluster clearing (>=0.213 -> <0.10 in 10d)": (tail < 0.10) & (tail.shift(10) >= 0.213),
 "[add] new highs share > 20%":   hi20 > 0.20,
 "[add] breadth200 < 25% (washout)": above200 < 0.25,
}

fwd = N.shift(-H) / N - 1
# first session within H where close <= -5% / >= +5% from today
def first_cross(th, sign):
    arr = N.values; out = np.full(len(arr), np.nan)
    for i in range(len(arr) - H):
        w = arr[i + 1:i + 1 + H] / arr[i] - 1
        hit = np.where(w <= th if sign < 0 else w >= th)[0]
        if len(hit): out[i] = hit[0] + 1
    return pd.Series(out, index=N.index)
lead_drop = first_cross(-0.05, -1); lead_rise = first_cross(0.05, +1)
DROP = fwd <= -0.05; RISE = fwd >= 0.05
valid = fwd.notna() & above200.notna() & (N.index >= pd.Timestamp("2015-06-01"))

rows = []
for name, sig in S.items():
    sig = sig.reindex(idx).fillna(False).astype(bool)
    starts = sig & ~sig.shift(1).fillna(False).astype(bool)
    for ev, evname, lead in ((DROP, "DROP", lead_drop), (RISE, "RISE", lead_rise)):
        r = {"signal": name, "event": evname}
        for half, mask in (("train", valid & (N.index < SPLIT)), ("test", valid & (N.index >= SPLIT))):
            base = ev[mask].mean()
            f = sig[mask]; st = starts[mask]
            n_fire = int(f.sum()); n_ep = int(st.sum())
            p = ev[mask & sig].mean() if n_fire else np.nan
            p_ep = ev[mask & starts].mean() if n_ep else np.nan
            r[f"{half}_base"] = round(base * 100, 1)
            r[f"{half}_hit"] = round(p * 100, 1) if n_fire else None
            r[f"{half}_lift"] = round(p / base, 2) if n_fire and base else None
            r[f"{half}_ep"] = n_ep
            r[f"{half}_ep_hit"] = round(p_ep * 100, 1) if n_ep else None
            r[f"{half}_ep_lift"] = round(p_ep / base, 2) if n_ep and base else None
            ld = lead[mask & starts & ev].median() if n_ep else np.nan
            r[f"{half}_lead"] = None if pd.isna(ld) else int(ld)
        rows.append(r)
res = pd.DataFrame(rows)
res.to_csv(f"{OUT}/predrop_calibration.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 60)
for ev in ("DROP", "RISE"):
    sub = res[res.event == ev].copy()
    sub["ok"] = (sub.train_ep_lift >= 1.2) & (sub.test_ep_lift >= 1.2)
    print(f"\n===== {ev} (base rate train {sub.train_base.iloc[0]}%, test {sub.test_base.iloc[0]}%) =====")
    print(sub[["signal", "train_ep", "train_ep_hit", "train_ep_lift", "train_lead", "test_ep", "test_ep_hit", "test_ep_lift", "test_lead", "ok"]]
          .sort_values("test_ep_lift", ascending=False).to_string(index=False))
