# SOURCEBORN AM07 — The Owner's Brief (simple language)

*Read this one page and you know your app. Updated 2026-10-02 (sizing truth pass — the capital and trade size had gone stale since 2026-09-10).*

## What this app is

A robot stock-picker that trades **paper money only** (₹5 crore practice capital
since 2026-09-10, ₹50L before that —
a hard lock in the code means it can never place a real order without your
explicit approval and a rebuild). It watches the whole NSE market, buys the
strongest stocks by a tested recipe, sells them by fixed rules, and writes
down every single decision it makes — nothing is ever hidden or deleted.

## What happens every trading day — by itself

| Time (IST) | What the app does alone |
|---|---|
| 03:30 | Upstox kills yesterday's token (their rule, not ours) |
| **your login** | The ONE thing only you can do. Any time of day — the moment you connect, a missed morning scan runs itself |
| 08:50 | Refreshes the full NSE list from Upstox (auto — ~2,400+ stocks, new listings enter same day) |
| 09:20 | Scans the market: momentum first (a fresh ~50-stock slice of the whole NSE list daily, spread across the alphabet, plus everything held), then intraday, short, swing, positional — all five categories execute trades (your order 2026-08-30) |
| every 30 min, 09:15-15:30 | Checks every stop and target at live prices (your order 2026-09-10). While a scan is running, the scan itself runs this check between categories, so no 30-minute slot is skipped (your order 2026-10-04) |
| 14:30 | A second scan of all five categories, each within its own cash share (your order 2026-10-04 — it used to scan one category and stop at 60% invested) |
| 15:25 | Force-sells every intraday position — no exceptions, ever |
| 15:35 | Marks all positions at the closing prices (no buying or selling after 15:30) |
| always | Journals every buy, sell, hold, failure and reason |

If a scan fails you SEE it (journal shows SCAN_FAILED). Silent dead mornings
are impossible now.

## The strategy, in plain words (the "final set" — earned by testing, not opinion)

**This is Final Set V1.3 (your orders 2026-10-04 and 2026-10-05).** The full
one-page record, every number checked against the code, is
`docs/FINAL_SET_V1_3.md`.

1. **Buy strength:** rank stocks by how strongly they've risen over 6 and 12
   months, adjusted for how wildly they swing, and buy above score 62 (your
   floor, 2026-07-30). **Trade size = the slab rule (your order 2026-09-10,
   "150000 slab as minimum and rest on the score basis", m = 0.29):**
   size = the larger of **₹1,50,000** and 0.29 × Kelly × equity, where Kelly
   comes from the score above 62 and the stock's own swing; a score AT 62
   is skipped; no trade above 5% of the book (₹25 lakh). Up to 222 slots.
   On ₹5 crore at a typical swing: 63 → ₹1.5L · 65 → ₹3.9L · 68 → ₹7.8L ·
   72 → ₹13.1L · 80 → ₹23.5L. (This is NOT ½-Kelly — ½-Kelly ran only
   2026-07-30 → 08-04; the old fixed ₹1,25,000 ran 08-04 → 09-10.)
   **Measured-Kelly check (your order 2026-10-02, "write it"; momentum only
   since your order 2026-10-05, "make it momentum only"):** before the
   momentum pass the app reads its OWN closed MOMENTUM trades (stops,
   targets, trails, decay, max-hold, rotation, danger — never your own
   sells, square-offs, the kill switch or the drawdown ladder, and never
   the other four categories' or Ocean's exits). If they show NO edge after
   costs, the momentum scan opens nothing new; if they show an edge, each
   new momentum trade is capped at the size that record supports (never
   below ₹1,50,000, never raised). Intraday, short, swing, positional and
   Ocean are neither counted nor blocked. The reading is on the Risk page;
   Settings switches it off. Your own BUY button and the Buy Desk are never
   blocked by it.
   **Cash share per category (your order 2026-10-04, "fix it so all 5
   categories get cash"):** momentum may spend **40%** of the book (₹2 crore
   on ₹5 crore), intraday, short, swing and positional **15% each** (₹75
   lakh). Before this, momentum is scanned first at 09:20 and could spend
   all the cash before the other four were looked at. A share a category
   does not use stays as cash — it is never lent to another. In a correction
   the 20% safety reserve shrinks all five shares alike (momentum ₹1.6 crore,
   the others ₹60 lakh). Ocean sits outside the shares (its own 8 slots).
   Your BUY button and the Buy Desk are never refused, but what they buy
   counts as momentum. The Risk page shows each share, spent and left;
   Settings can change the five numbers or switch the shares off.
   **Momentum buys no longer wait for a candle pattern** (your order
   2026-10-04, "off for momentum" — 12 years measured, the wait added
   nothing). Intraday, short, swing and positional still wait, and each of
   those four now scores with its OWN weights (same order; before, all four
   used the swing weights).
   Each stock must also be rising on its own (not just "least bad").
   **What a trade is worth, all three cases:** a normal (Everest) entry is
   the slab ladder above — **at least ₹1,50,000** (₹1,25,000 under the old
   fixed rule, still selectable in Settings); an **Ocean** dip-engine entry is **₹1,68,750 — 35% MORE than a
   normal trade** (your order 2026-08-30, replacing the half size you
   approved on 2026-08-26; max 8 Ocean slots, so at most ₹13.5L in the
   dip engine at once — and Ocean only buys behind its four safety rails,
   whose damage rule now uses the panel's own top-10% line (your order
   2026-10-04; the fixed 0.213 was ON 84% of days, so Ocean had bought
   nothing in 12 years of data); and **nothing below ₹1,50,000 ever
   opens from a scan** (your order 2026-10-04, "no 25 k is needed" — the
   old ₹25,000 dust guard is gone). When cash, a category's share, the
   sector limit or the risk limit leaves less than ₹1.5 lakh, the trade is
   rounded up to ₹1.5 lakh if there is room, otherwise it is skipped —
   never opened small.
2. **Sell by rails, not feelings:** every momentum position carries a
   **stop of −5%, or 2 × its own 14-day range if that is wider, capped at
   −15%** (stop −5% your call 2026-07-26; the ATR widening your call
   2026-10-05, "Go for A", after it beat the fixed stop on the 12-year
   panel in both the choosing half and the judging half —
   `research/stop_cooldown_study`; other categories keep their fixed
   stops) and a **+20% target** from day one (target raised +12→+20 on
   2026-08-24 — you picked "Switch to D" after both exit studies showed
   it winning all four test windows), a **−15% trailing
   stop that is now genuinely live** (above +17.6% it protects the gain;
   at the old +12 it could never fire), a score-decay exit that dumps dead
   leaders (never your own manual picks — those only you sell, or the
   stop/target does), and a 200-day maximum hold.
3. **Bad weather = brake — SWITCHED OFF at your order (2026-08-09,
   "i want to take risk on 200 DMA"):** a correction (Nifty below its
   200-day average) no longer blocks buying by itself. Still standing:
   panic blocks, the breadth gate, the score floor, the stop, the
   kill switch, and the crash cash-reserve ladder (20% correction / 40%
   panic). The Settings toggle restores the brake.
4. **The green light inside corrections (new):** if the fear index (India
   VIX) falls back to 75% of its panic peak, the app starts buying again
   early — the only correction tactic that beat holding cash in 10 real
   correction episodes (it caught +24% of the COVID rebound).
5. **Banned list:** stocks you've blacklisted (YESBANK) are never touched.

## Your buttons

- **🟢 BUY buttons — everywhere a stock is listed:** every row of the
  Scan page's top-50 list, the dashboard Watchlist, and Today's Picks has
  its own 🟢 BUY button. One tap opens that stock properly — live price,
  right size, stops set, journaled as YOUR call. Your five calls —
  TORNTPHARM, CONCOR, PNB, DIVISLAB, FEDERALBNK — await these taps.
- **🟢 Order ticket (Scan page):** like a broker order form. Type the
  ticker, then fill only what YOU want to control — qty (blank = app
  sizes it), Market or Limit (limit waits for your price, valid for the
  day, expires 15:25), your own stop %, your own target % (blank =
  preset). Resting limit orders show in their own list with a CANCEL
  button; every placement/fill/cancel/expiry is journaled.
- **🎹 Tap any piano note:** every cell/name in the Parameter Piano
  opens that parameter's own page — what it means in plain words, the
  exact pass rule, WHERE in the code to change it, and every stock it
  passed, weakened, blocked or skipped in the last scan.
- **RUN SCAN NOW:** manual scan in the current mode.
- **MODE:** keep it on **N200 MOMENTUM** (the tested strategy).
- **Backtest page:** Download History (refresh the data store) and
  Export Stored ZIP (hand the data to research).

## What is proven, and what is not — the honest line

- Across four research campaigns on real data, the recipe beat buy-and-hold
  in most unseen test windows — roughly **+9%/year before survivorship
  doubts, somewhere between −2% and +9% after them**. Not a guarantee; a
  measured, documented edge estimate (`docs/PARAM_SEARCH_V3.md` (live walk-forward evidence) and `docs/archive/PARAM_SEARCH_V1..V4.md`).
- **Right now the market is in a correction and this factor is in a
  measured cold stretch** — your flat year was the season, not a broken app.
- The one test that settles it: **60 days of live paper trading** starting
  when you switch mode + log in. Pass/fail was defined in advance: the app
  should track its expected behavior vs Nifty; far-below-both means stop
  and investigate.

## If something looks wrong

1. Open the journal / ⚡ card — SCAN_FAILED or DATA_STALE tells you what and why.
2. Nine times out of ten the cause is the Upstox login — connect, and the
   catch-up scan fixes the day automatically.
3. An empty book during CORRECTION with no VIX green-light = the app working
   correctly, not failing.

## Where everything lives (feed nothing twice)

Repo `damandamanaulakh-tech/am07` — **branch `main` is live** (Render
auto-deploys it). The old sourceborn-engine repo is retired; nothing lives
only there. All data is committed: 12y of your Upstox candles,
15y of Nifty/VIX/Sensex/BankNifty, the 1991-2024 close panel, the 10y clean
set + its rebuild script, the 5 point-in-time membership lists, every search
script and result. `CLAUDE.md` is the machine's memory — any future session
reads it first and knows everything without you re-feeding a single file.
