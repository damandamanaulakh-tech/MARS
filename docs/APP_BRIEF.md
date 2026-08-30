# SOURCEBORN AM07 — The Owner's Brief (simple language)

*Read this one page and you know your app. Updated 2026-08-09 (audit truth pass — six money facts had gone stale).*

## What this app is

A robot stock-picker that trades **paper money only** (₹50L practice capital —
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
| 14:30 | Top-up scan if the book is under-invested |
| 15:25 | Force-sells every intraday position — no exceptions, ever |
| 15:35 | Marks all positions at live prices and fires the exit rules |
| always | Journals every buy, sell, hold, failure and reason |

If a scan fails you SEE it (journal shows SCAN_FAILED). Silent dead mornings
are impossible now.

## The strategy, in plain words (the "final set" — earned by testing, not opinion)

1. **Buy strength:** rank stocks by how strongly they've risen over 6 and 12
   months, adjusted for how wildly they swing, and buy above score 62 (your
   floor, 2026-07-30). Each new trade is a FIXED **₹1.25 lakh** (your order:
   "1.25 lac, 125000, not percentage"), up to 80 slots as the book grows.
   Each stock must also be rising on its own (not just "least bad").
   **What a trade is worth, all three cases:** a normal (Everest) entry is
   **₹1,25,000**; an **Ocean** dip-engine entry is **₹1,68,750 — 35% MORE than a
   normal trade** (your order 2026-08-30, replacing the half size you
   approved on 2026-08-26; max 8 Ocean slots, so at most ₹13.5L in the
   dip engine at once); and **nothing below ₹25,000 ever
   opens** — that is a dust guard from 2026-07-11, and a trade that small
   is skipped outright, not shrunk to fit. So "at least ₹1 lakh" describes
   Everest only, not every trade the book can make.
2. **Sell by rails, not feelings:** every position carries **−5% stop /
   +20% target** from day one (your calls: stop 2026-07-26; target raised
   +12→+20 on 2026-08-24 — you picked "Switch to D" after both exit
   studies showed it winning all four test windows), a **−15% trailing
   stop that is now genuinely live** (above +17.6% it protects the gain;
   at the old +12 it could never fire), a score-decay exit that dumps dead
   leaders (never your own manual picks — those only you sell, or the
   stop/target does), and a 200-day maximum hold.
3. **Bad weather = brake — SWITCHED OFF at your order (2026-08-09,
   "i want to take risk on 200 DMA"):** a correction (Nifty below its
   200-day average) no longer blocks buying by itself. Still standing:
   panic blocks, the breadth gate, the score floor, the −5% stop, the
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
  measured, documented edge estimate (`docs/PARAM_SEARCH_V1..V4.md`).
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
