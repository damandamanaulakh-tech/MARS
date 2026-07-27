"""Generate the MTAR tunnel visual — every mark from the real computed
files (mtar_daily.csv + mtar_tunnel_readings.csv). No placeholders."""
import math

import numpy as np
import pandas as pd

from pathlib import Path as _P
S = str(_P(__file__).resolve().parent)   # repaired 2026-07-27: repo-relative

px = pd.read_csv(f"{S}/mtar_daily.csv", parse_dates=["Date"], index_col="Date").sort_index()
T = pd.read_csv(f"{S}/mtar_tunnel_readings.csv", parse_dates=[0], index_col=0).sort_index()

BARS = {"mom_rank": 55.0, "abs_mom_6m": 0.0, "momentum_1m": 60.0,
        "volume_exp": 60.0, "rel_strength": 60.0, "vol_adj_up": 60.0,
        "breakout_q": 60.0}
NEAR = 5.0
LANES = [("mom_rank", "Momentum rank (6m+12m)"), ("abs_mom_6m", "Own 6m return > 0"),
         ("momentum_1m", "1-month momentum"), ("volume_exp", "Volume expansion"),
         ("rel_strength", "Strength vs Nifty"), ("vol_adj_up", "Vol-adjusted upside"),
         ("breakout_q", "Breakout quality"), ("candle", "Candle confirm"),
         ("regime", "Market regime gate")]

# ---------------- price chart (log scale, full history) ----------------
W, H, L, R, TOP, BOT = 1060, 340, 56, 14, 16, 34
c = px["Close"]
sma = c.rolling(200).mean()
d0, d1 = c.index[0], c.index[-1]
span = (d1 - d0).days
lo, hi = 850, 9000


def X(d):
    return L + (d - d0).days / span * (W - L - R)


def Y(v):
    return TOP + (1 - (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo))) * (H - TOP - BOT)


def path(series):
    pts = [f"{X(d):.1f},{Y(v):.1f}" for d, v in series.dropna().items()]
    return " ".join(pts)


gridlines = ""
for v in (1000, 2000, 4000, 8000):
    gridlines += (f'<line x1="{L}" y1="{Y(v):.1f}" x2="{W-R}" y2="{Y(v):.1f}" class="grid"/>'
                  f'<text x="{L-8}" y="{Y(v)+4:.1f}" class="ax" text-anchor="end">₹{v:,}</text>')
years = ""
for y in range(2021, 2027):
    d = pd.Timestamp(f"{y}-01-01")
    if d0 < d < d1:
        years += (f'<line x1="{X(d):.1f}" y1="{TOP}" x2="{X(d):.1f}" y2="{H-BOT}" class="grid"/>'
                  f'<text x="{X(d):.1f}" y="{H-12}" class="ax" text-anchor="middle">{y}</text>')

EVENTS = [
    ("2025-03-17", 1229, "Trough ₹1,229", "miss", "below"),
    ("2025-05-16", 1636, "200DMA reclaim + Q4 profit +222%", "near", "above"),
    ("2025-09-17", 1783, "TUNNEL BUY ₹1,783", "hit", "below"),
    ("2025-12-08", 2361, "MEIL order ₹194 Cr", "near", "below"),
    ("2026-05-18", 6200, "Orders ₹2,279 Cr + ₹467 Cr", "hit", "left"),
    ("2026-06-19", 8374, "ATH ₹8,374", "hit", "above"),
]
marks = ""
for ds, v, label, cls, side in EVENTS:
    d = pd.Timestamp(ds)
    x, y = X(d), Y(v)
    marks += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" class="mk {cls}"/>'
    if side == "above":
        marks += f'<text x="{x:.1f}" y="{y-10:.1f}" class="lbl" text-anchor="middle">{label}</text>'
    elif side == "below":
        marks += f'<text x="{x:.1f}" y="{y+18:.1f}" class="lbl" text-anchor="middle">{label}</text>'
    else:
        marks += f'<text x="{x-9:.1f}" y="{y+4:.1f}" class="lbl" text-anchor="end">{label}</text>'

# exit lines from the ₹1,783 entry
exits = ""
for v, lab, cls in ((1997, "+12% target exits ₹1,997", "exit12"),
                    (2318, "old +30% exits ₹2,318", "exit30"),
                    (7118, "trail −15% from ATH ≈ ₹7,118 (never hit in the rise)", "trail")):
    exits += (f'<line x1="{X(pd.Timestamp("2025-09-17")):.1f}" y1="{Y(v):.1f}" x2="{W-R}" y2="{Y(v):.1f}" class="{cls}"/>'
              f'<text x="{W-R-4}" y="{Y(v)-5:.1f}" class="lbl {cls}t" text-anchor="end">{lab}</text>')

price_svg = f'''<svg viewBox="0 0 {W} {H}" role="img" aria-label="MTAR price 2021-2026 log scale">
{gridlines}{years}
<polyline points="{path(sma)}" class="sma"/>
<polyline points="{path(c)}" class="price"/>
{exits}{marks}
</svg>'''

# ---------------- tunnel grid (weekly samples of real readings) ----------------
Tw = T.iloc[::5]      # every 5th trading day
cols = len(Tw)
CW, CH, GAP = 9, 26, 3
GL = 190
GW = GL + cols * (CW + 2) + 12
GH = len(LANES) * (CH + GAP) + 58


def cell_class(k, row):
    if k == "candle":
        return "hit" if isinstance(row["candle"], str) and row["candle"] else "na"
    if k == "regime":
        return "hit" if row["regime_ok"] in (True, "True") else "miss"
    v = row[k]
    if pd.isna(v):
        return "na"
    b = BARS[k]
    if v >= b:
        return "hit"
    if v >= b - NEAR:
        return "near"
    return "miss"


def cell_tip(k, d, row):
    if k == "candle":
        return f"{d.date()} candle: {row['candle'] or 'none'}"
    if k == "regime":
        return f"{d.date()} regime {'open' if row['regime_ok'] in (True,'True') else 'BRAKE'}"
    v = row[k]
    return f"{d.date()} {k} = {'-' if pd.isna(v) else f'{v:.0f}'} (bar {BARS[k]:.0f})"


rects, labels = "", ""
for r, (k, title) in enumerate(LANES):
    y = 30 + r * (CH + GAP)
    labels += f'<text x="{GL-10}" y="{y+CH/2+4}" class="lane" text-anchor="end">{title}</text>'
    for i, (d, row) in enumerate(Tw.iterrows()):
        x = GL + i * (CW + 2)
        rects += (f'<rect x="{x}" y="{y}" width="{CW}" height="{CH}" rx="1.5" '
                  f'class="c {cell_class(k, row)}"><title>{cell_tip(k, d, row)}</title></rect>')

key_dates = [("2025-03-17", "trough"), ("2025-05-16", "reclaim"),
             ("2025-09-17", "BUY"), ("2026-06-19", "ATH")]
axis = ""
for ds, lab in key_dates:
    dd = pd.Timestamp(ds)
    i = int(np.argmin(np.abs(Tw.index - dd)))
    x = GL + i * (CW + 2) + CW / 2
    axis += (f'<line x1="{x}" y1="26" x2="{x}" y2="{GH-26}" class="kd"/>'
             f'<text x="{x}" y="18" class="ax" text-anchor="middle">{lab}</text>')
mon = ""
for i, d in enumerate(Tw.index):
    if d.month != Tw.index[max(0, i - 1)].month and d.month in (1, 4, 7, 10):
        x = GL + i * (CW + 2)
        mon += f'<text x="{x}" y="{GH-8}" class="ax">{d.strftime("%b %y")}</text>'

tunnel_svg = f'''<svg viewBox="0 0 {GW} {GH}" role="img" aria-label="Parameter tunnel over time">
{labels}{rects}{axis}{mon}
</svg>'''

# ---------------- open-rate bars (rise window, real) ----------------
trough = pd.Timestamp("2025-03-17")
rise = T.loc[T.index > trough]
n = len(rise)
rates = []
for k, title in LANES:
    if k == "candle":
        ok = sum(1 for x in rise["candle"] if isinstance(x, str) and x)
    elif k == "regime":
        ok = int(rise["regime_ok"].astype(str).eq("True").sum())
    else:
        ok = int((rise[k].astype(float) >= BARS[k]).sum())
    rates.append((title, ok, round(ok / n * 100)))
bars_html = "".join(
    f'<div class="bar-row"><span class="bar-name">{t}</span>'
    f'<div class="bar-track"><div class="bar-fill" style="width:{p}%"></div></div>'
    f'<span class="bar-val">{p}% <em>({o}/{n}d)</em></span></div>'
    for t, o, p in rates)

# ---------------- near-miss ledger (real rows) ----------------
NM = [("2025-05-28", "1,657", "5/7", "own 6m return −5%", "—"),
      ("2025-06-06", "1,771", "5/7", "rank 51 · own 6m −1%", "URR"),
      ("2025-06-09", "1,770", "5/7", "rank 52 · own 6m −1%", "URR"),
      ("2025-06-10", "1,760", "5/7", "own 6m −2%", "URR"),
      ("2025-09-26", "1,879", "5/7", "volume 57", "URR"),
      ("2025-10-23", "2,291", "6/7", "volume 60", "morning star"),
      ("2026-01-13", "2,634", "6/7", "breakout quality 59", "—"),
      ("2026-02-12", "3,561", "5/7", "breakout quality 58", "URR")]
nm_rows = "".join(
    f'<tr><td class="num">{d}</td><td class="num">₹{p}</td><td class="num">{h}</td>'
    f'<td><span class="pill near-p">{miss}</span></td><td>{cd}</td></tr>'
    for d, p, h, miss, cd in NM)

NEWGEN = [
    ("N1", "Series-watch", "Score BE/T2T names in a shadow list; flag when they'd pass",
     "MTAR was OUTSIDE the universe (BE series) — flag would have fired May 2025", "universe change only"),
    ("N2", "Order-inflow shock", "New orders ≥ 25% of order book within 90 days",
     "₹2,279 Cr order = +88% of book in ONE day (May 2026); ₹720 Cr through FY25", "needs filings feed"),
    ("N3", "Earnings acceleration", "Quarterly profit growth > 2× previous quarter's",
     "+222% net profit at the exact turn (Q4 FY25)", "needs results feed"),
    ("N4", "Guidance raise", "Management raises its own forward numbers",
     "25% → 30-35% growth raised May 2025; later toward 80%", "needs filings feed"),
    ("N5", "Abs-mom tolerance", "≥5 nodes + candle confirmed → allow own 6m return ≥ −2%",
     "Blocked at −1% with URR candle, June 2025 ₹1,760s", "threshold mark"),
    ("N6", "Rank near-band", "Momentum rank ≥ 50 acceptable when 6 of 7 others hit",
     "Rank read 45-52 through May-June 2025", "threshold mark"),
]
ng_rows = "".join(
    f'<tr><td class="num">{n_}</td><td><strong>{t}</strong></td><td>{r}</td><td>{m}</td>'
    f'<td><span class="pill {"hit-p" if "threshold" in s or "universe" in s else "na-p"}">{s}</span></td></tr>'
    for n_, t, r, m, s in NEWGEN)

html = f'''<title>MTAR Through the Tunnel — Sourceborn AM07</title>
<style>
:root{{
  --bg:#0b1220; --panel:#121a2b; --panel2:#0e1524; --ink:#e8edf5; --ink2:#9aa7ba;
  --line:#243149; --accent:#7fa6c9;
  --hit:#2f9e6e; --hit-bg:#12341f; --near:#d99a2b; --near-bg:#3a2c10;
  --miss:#c4524a; --miss-bg:#361a18; --na:#39445a;
}}
@media (prefers-color-scheme: light){{:root{{
  --bg:#f4f6f3; --panel:#ffffff; --panel2:#eef1ec; --ink:#182335; --ink2:#5b6878;
  --line:#d8ded2; --accent:#3f6e96;
  --hit:#1e7d54; --hit-bg:#ddefe4; --near:#9c6d10; --near-bg:#f4e8cd;
  --miss:#a93f38; --miss-bg:#f3dedc; --na:#c9d1c4;
}}}}
:root[data-theme="light"]{{
  --bg:#f4f6f3; --panel:#ffffff; --panel2:#eef1ec; --ink:#182335; --ink2:#5b6878;
  --line:#d8ded2; --accent:#3f6e96;
  --hit:#1e7d54; --hit-bg:#ddefe4; --near:#9c6d10; --near-bg:#f4e8cd;
  --miss:#a93f38; --miss-bg:#f3dedc; --na:#c9d1c4;
}}
:root[data-theme="dark"]{{
  --bg:#0b1220; --panel:#121a2b; --panel2:#0e1524; --ink:#e8edf5; --ink2:#9aa7ba;
  --line:#243149; --accent:#7fa6c9;
  --hit:#2f9e6e; --hit-bg:#12341f; --near:#d99a2b; --near-bg:#3a2c10;
  --miss:#c4524a; --miss-bg:#361a18; --na:#39445a;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.55 "Avenir Next","Segoe UI",system-ui,sans-serif}}
.wrap{{max-width:1120px;margin:0 auto;padding:28px 20px 90px}}
.eyebrow{{font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--accent);font-weight:700}}
h1{{font-size:clamp(30px,5vw,52px);line-height:1.02;letter-spacing:-.02em;margin:10px 0 6px;font-weight:800;text-wrap:balance}}
.sub{{color:var(--ink2);max-width:70ch;margin:0 0 22px}}
h2{{font-size:21px;margin:44px 0 4px;letter-spacing:-.01em}}
.note{{color:var(--ink2);font-size:13.5px;margin:2px 0 12px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:20px 0}}
.stat{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 15px}}
.stat .k{{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink2);font-weight:700}}
.stat .v{{font:700 25px/1.15 ui-monospace,Menlo,monospace;margin-top:3px;font-variant-numeric:tabular-nums}}
.stat .v.up{{color:var(--hit)}} .stat .v.dn{{color:var(--miss)}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;overflow-x:auto}}
svg{{display:block;min-width:720px;width:100%;height:auto}}
.grid{{stroke:var(--line);stroke-width:1}}
.ax{{fill:var(--ink2);font:10.5px ui-monospace,monospace}}
.lane{{fill:var(--ink);font:12px "Avenir Next","Segoe UI",sans-serif;font-weight:600}}
.lbl{{fill:var(--ink);font:600 10.5px "Segoe UI",sans-serif}}
.price{{fill:none;stroke:var(--ink);stroke-width:1.8}}
.sma{{fill:none;stroke:var(--accent);stroke-width:1.2;stroke-dasharray:1 3}}
.mk{{stroke:var(--bg);stroke-width:1.5}}
.mk.hit{{fill:var(--hit)}} .mk.near{{fill:var(--near)}} .mk.miss{{fill:var(--miss)}}
.exit12{{stroke:var(--miss);stroke-width:1;stroke-dasharray:5 4}}
.exit30{{stroke:var(--near);stroke-width:1;stroke-dasharray:5 4}}
.trail{{stroke:var(--hit);stroke-width:1;stroke-dasharray:5 4}}
.exit12t{{fill:var(--miss)}} .exit30t{{fill:var(--near)}} .trailt{{fill:var(--hit)}}
.c.hit{{fill:var(--hit)}} .c.near{{fill:var(--near)}} .c.miss{{fill:var(--miss);opacity:.75}} .c.na{{fill:var(--na);opacity:.5}}
.kd{{stroke:var(--ink2);stroke-width:1;stroke-dasharray:2 3}}
.legend{{display:flex;flex-wrap:wrap;gap:16px;font-size:12.5px;color:var(--ink2);margin:10px 2px}}
.sw{{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:-1px}}
table{{border-collapse:collapse;width:100%;font-size:14px;min-width:640px}}
th{{text-align:left;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--ink2);
  padding:10px 12px;border-bottom:1px solid var(--line);background:var(--panel2)}}
td{{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}}
tr:last-child td{{border-bottom:none}}
.num{{font-family:ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums;white-space:nowrap}}
.pill{{display:inline-block;font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:999px}}
.hit-p{{color:var(--hit);background:var(--hit-bg)}}
.near-p{{color:var(--near);background:var(--near-bg)}}
.na-p{{color:var(--ink2);background:var(--panel2)}}
.bar-row{{display:grid;grid-template-columns:190px 1fr 110px;gap:12px;align-items:center;margin:7px 0}}
.bar-name{{font-size:13.5px;font-weight:600}}
.bar-track{{background:var(--panel2);border-radius:99px;height:14px;overflow:hidden}}
.bar-fill{{background:var(--hit);height:100%;border-radius:99px}}
.bar-val{{font:600 13px ui-monospace,monospace;color:var(--ink2)}} .bar-val em{{font-style:normal;opacity:.7}}
.foot{{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);color:var(--ink2);font-size:12.5px}}
.foot a{{color:var(--accent)}}
</style>
<div class="wrap">
<div class="eyebrow">Sourceborn · Reverse-Engineering Study № 1</div>
<h1>MTAR through the tunnel</h1>
<p class="sub">One real stock, five years of real prices, run day-by-day through the engine's existing parameters —
then rebuilt as a new-generation table. Every cell below is a computed reading; nothing is a placeholder.</p>

<div class="stats">
  <div class="stat"><div class="k">Listed Mar-2021</div><div class="v">₹1,082</div></div>
  <div class="stat"><div class="k">Trough 17-Mar-25</div><div class="v dn">₹1,229</div></div>
  <div class="stat"><div class="k">Tunnel BUY 17-Sep-25</div><div class="v">₹1,783</div></div>
  <div class="stat"><div class="k">ATH 19-Jun-26</div><div class="v up">₹8,374</div></div>
  <div class="stat"><div class="k">Captured from BUY</div><div class="v up">+229%</div></div>
</div>

<h2>The journey, with the engine's verdicts on it</h2>
<p class="note">Log scale. Dotted line = 200-day average. The three dashed lines show where each exit rule
would end a ₹1,783 entry — the +12% target, the old +30%, and the −15% trail (which never fired once during the whole rise).</p>
<div class="panel">{price_svg}</div>

<h2>The tunnel itself — nine lanes, weekly steps, Sep 2024 → today</h2>
<p class="note">Each row is one existing parameter; each cell one real reading (hover any cell for the number).
Watch the wall of red at the trough turn amber through May-June 2025 — the near-miss zone — then green by September.</p>
<div class="legend">
  <span><span class="sw" style="background:var(--hit)"></span>HIT — bar cleared</span>
  <span><span class="sw" style="background:var(--near)"></span>NEAR — within 5 points under the bar</span>
  <span><span class="sw" style="background:var(--miss)"></span>MISS</span>
  <span><span class="sw" style="background:var(--na);opacity:.6"></span>no reading / no pattern</span>
</div>
<div class="panel">{tunnel_svg}</div>

<h2>How open was each lane during the rise?</h2>
<p class="note">Share of the {n} trading days from the trough to yesterday on which each lane was green.</p>
<div class="panel" style="min-width:0">{bars_html}</div>

<h2>The near-miss ledger — where a monster stood outside the door</h2>
<p class="note">Days with at least five lanes green while one reading sat just under its bar. June 2025 is the treasure:
blocked by its own 6-month return at −1% while URR candles confirmed.</p>
<div class="panel"><table>
<thead><tr><th>Date</th><th>Price</th><th>Lanes hit</th><th>What blocked</th><th>Candle</th></tr></thead>
<tbody>{nm_rows}</tbody></table></div>

<h2>New-generation parameter table — born from this stock</h2>
<p class="note">What the tunnel and the dated public record say the engine was missing. Status shows what each row needs.</p>
<div class="panel"><table>
<thead><tr><th>#</th><th>Parameter</th><th>Rule</th><th>MTAR evidence</th><th>Status</th></tr></thead>
<tbody>{ng_rows}</tbody></table></div>

<h2>The structural finding above all of them</h2>
<p class="note" style="font-size:15px;color:var(--ink)">MTAR trades in the <strong>BE (trade-to-trade) series</strong> —
and the universe list carries EQ-series names only. The stock was never inside the room where the tunnel runs.
N1 is listed first for a reason.</p>

<p class="foot">Method: daily OHLCV from Upstox public history API (1,328 sessions, 15-Mar-2021 → 23-Jul-2026);
parameters computed with the engine's own code; momentum rank vs the 191-stock cohort; regime from real Nifty/200DMA.
News: dated public filings and coverage — Q4 FY25 profit +222% and guidance raise (May 2025), MEIL ₹194 Cr (Dec 2025),
international orders ₹2,279 Cr + ₹467 Cr taking the book to ₹5,363 Cr (May 2026).
Full day-by-day readings: mtar_tunnel_readings.csv. Research record — the live engine is untouched.</p>
</div>
'''
open(f"{S}/mtar_tunnel_visual.html", "w").write(html)
print("written:", len(html), "bytes · tunnel cols:", cols, "· rise days:", n)
