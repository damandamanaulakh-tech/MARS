# FINAL SET V1.2 — the live recipe as of 2026-10-05

*Owner order 2026-10-04: "also make these things as finale set V 1.1";
2026-10-05: "Go for A" (the ATR stop), which makes this V1.2.*

This page records **everything the app trades by today**, in one place. Every
number below was read from the code on 2026-10-04 (`trading/config.py`,
`trading/modes.py`, `trading/targets.py`, `trading/risk_v2.py`,
`trading/danger.py`, `web/scheduler.py`), not copied from older notes.
`web/tests/test_final_set_v1_2.py` checks the main numbers against the code on
every test run, so this page cannot quietly go out of date.

**V1.0** was the set approved on 2026-07-21: ₹50L capital, fixed ₹1.25L
trades, −5%/+12% exits, 50 names a day, Ocean parked, correction brake on.
Every line of V1.0 has since been changed by an order of yours. **V1.1**
(2026-10-04) brought all of those changes together and added three more
(PR #149, PR #150). **V1.2 changes exactly one line of V1.1**: the momentum
stop (section 3, and section 7 below).

Paper money only. Nothing here can place a real order.

---

## 1. Money

| Item | V1.2 value | Your order |
|---|---|---|
| Practice capital | **₹5,00,00,000** | 2026-09-10 |
| Trade size | **Slab rule**: the larger of ₹1,50,000 and 0.29 × Kelly × equity. Kelly is worked out from how far the score sits above 62 and how much the stock swings | 2026-09-10 ("150000 slab as minimum and rest on the score basis", m = 0.29) |
| Biggest single trade | 5% of the book (₹25 lakh) | 2026-09-10 |
| Smallest trade | **₹1,50,000**. A short trade is rounded up to ₹1.5L if there is room, otherwise skipped | 2026-10-04 ("no 25 k is needed") |
| Most positions at once | 222. This is a ceiling, not a target | 2026-09-10 |
| Cash share per category | **Momentum 40%**; intraday, short, swing and positional **15% each**. A share a category does not use stays as cash | 2026-10-04 |
| Ocean (dip-buying engine) trade | ₹1,68,750, up to 8 at once, outside the category shares | 2026-08-30 |
| Measured-Kelly check | On. If the app's own closed trades show no edge after costs, the scan opens nothing new. If they show an edge, each trade is capped at the size that record supports | 2026-10-02 ("F9- write it") |
| Cash kept back | 0% normally, 20% in a correction, 40% in a panic | 2026-07-27 |

## 2. What gets bought

| Item | V1.2 value |
|---|---|
| Stocks scanned | ~1,200, rebuilt every Friday by rupee turnover from the full NSE list. Your 172 hand-picked names, everything held and the watchlist always ride along. Blocklist: YESBANK |
| Momentum score | 50 + 25 × average(6-month return ÷ 6-month swing, 12-month return ÷ 12-month swing), blended 35% with quality |
| Categories, in scan order | momentum → intraday → short → swing → positional, at **09:20 and again at 14:30** |
| Buy floors | momentum **62** · intraday 72 · short 70 · swing 60 · positional 68 |
| Weights | Each of the four non-momentum categories scores on its own weights |
| Own trend | The stock's own 6-month return must be above zero (momentum) |
| Breadth band | New buys pause while 40%–65% of stocks are above their 200-day average; below 40% and above 65% are buyable. A VIX-crush re-entry overrides it |
| Correction brake | **Off** (Nifty under its 200-day average alone does not stop buying) |
| Candle confirmation | Off for momentum. On for the other four |
| Liquidity | At least ₹5 crore traded a day (rupees, not share count) |
| Sector limit | 12 held names per sector, 25% of the book per sector. Unmapped names are not counted |
| Three-formula view (AM07 / ASH08 68 / AshStocks 70) | Shown on /scan; the filter switch stays **off** |
| Market hours | Buys and sells only between 09:15 and 15:30 IST |

## 3. When it sells

| Category | Stop | Target | Trailing stop | Score exit | Longest hold |
|---|---|---|---|---|---|
| Momentum | −5%, or 2 × ATR if wider (cap −15%) | +20% (8 × ATR at the median stock) | −15% | below 40 | 200 days |
| Intraday | −1.5% | 1 × ATR | −1% | below 40 | same day — squared off at 15:25 |
| Short | −3% | 5 × ATR (~12.5%) | −4% | below 45 | 63 days |
| Swing | −5% | 4 × ATR (~10%) | −8% | below 45 | 20 days |
| Positional | −10% | 10 × ATR (~25%) | −12% | below 45 | 252 days |

**The momentum stop (V1.2, your order 2026-10-05 "Go for A"):** a new
momentum buy's stop is the wider of −5% and 2 × the stock's own 14-day
average true range, capped at −15%. Most stocks stay at −5%; a jumpy one
gets room (the study's average was −6.8%). Measured first, on the 12-year
panel with the settings chosen on 2015–2020 and judged on 2020–2026: it
beat the fixed −5% in both halves and in 5 of 6 windows, with 30% fewer
stop-outs (`research/stop_cooldown_study/summary.md`). Other categories keep
their fixed stops — only momentum was measured. Positions already held keep
the stop they were opened with; a stop is never loosened. The cost, stated
plainly: trade size is unchanged, so a losing trade on a jumpy stock can lose
more than 5% — up to 15% at the cap.

Each position is judged by its **own** category's rules. Your own buys are
never sold by the score exit; stop, target and trail still protect them.

**When the checks run:** every 30 minutes from 09:15 to 15:30. While the 09:20
or 14:30 scan is running, the scan runs the check itself between categories,
so no slot is skipped (2026-10-04). 15:35 only marks prices.

## 4. Safety rails

| Rail | V1.2 value |
|---|---|
| Daily loss stop (kill switch) | −2% of equity in one day: no new buys, and every holding with a live price is sold. It resets the next day |
| Drawdown ladder (from peak) | −5% → new trades 75% size · −8% → no new trades · −10% → cut half · −15% → mostly cash · −20% → close everything. Each level cuts once |
| Total risk at stop | At most 25% of equity, each held position counted at its own stop |
| Danger layer | On. 12 calibration-verified tripwires watch held names: 1 fire tightens the stop (never tighter than the position's own stop width at entry — the category's stop, or its wider ATR stop), 2 or more sell half |
| Rotation | On. A holding 90%+ of the way to its target can be sold to fund a fresh pick when cash, or that category's share, is short |
| Ocean | On, behind four rails (VIX knife, the panel's own damage line, reclaim bar, danger veto). Expect it to be rare |
| Macro halt | Budget and election-result days halt new buys. RBI policy days do **not** |

## 5. Not in the money path (display only)

The 112 trigger layer and its stage ladder, the news feed, the SEBI orders
card, the register, the lens, the sector event card, quarterly holdings and
the pre-fall framework. These are shown, never traded on.

## 6. What V1.2 has not proved yet

- The ATR stop has not run on a live market day yet; Tuesday 2026-10-06 is
  the first. Its evidence is the 12-year replay, which does not model the
  breadth gate, category shares, the danger layer or rotation.
- V1.1's three changes (category shares, scan-time exit checks, ₹1.5L
  minimum) ran live for the first time on Monday 2026-10-05.
- The 14:30 scan now does as much work as 09:20, whose peak memory measured
  291–326 MB on the 512 MB box. The first live reading decides it.
- The breadth band was measured for momentum only. Applying it to the other
  four is your reasoned order, not a measured result.
- The 60-day paper window is the test of the whole set.

## 7. What changed from V1.1

| Line | V1.1 | V1.2 | Your order |
|---|---|---|---|
| Momentum stop | −5% fixed | −5%, or 2 × ATR if wider, cap −15% (new buys only) | 2026-10-05 "Go for A" |
| Danger-layer leash | never tighter than the category's stop | never tighter than the position's own stop width at entry | follows from the above |
| Risk at stop (25% rail) | every position counted at the category's stop | each position counted at its own stop | follows from the above |

Settings has an "ATR Stop On" switch (1 by default); 0 restores the fixed
−5% for new buys. Nothing else moved.

Change any line of this page only on your word; the version then becomes V1.3.
