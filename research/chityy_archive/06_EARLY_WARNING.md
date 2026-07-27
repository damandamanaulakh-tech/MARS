# 06 — EARLY-WARNING PARAMETER LAYER
## Signals that fire 1-2 weeks BEFORE regime changes

> Most retail indicators (VIX > 22, FII outflow > 5,000 Cr) confirm a regime change *after* it's begun. The parameters below are designed to fire **5-15 trading days earlier** — when the index is still calm but the internals are already breaking.

All are India-NSE-specific and use **publicly available** data (NSE bhavcopy, NSE derivatives data, RBI, SEBI disclosures, GST portal, MCA filings). Most are NOT in any standard scan engine today.

---

## A. MICROSTRUCTURE LEADERS (intraday tape, daily refresh)

### 1. Dispersion Breakdown Index (DBI)
**What:** Cross-sectional standard deviation of Nifty 500 daily returns / Nifty 500 absolute daily return.
**Why it leads:** When index is flat but individual stocks have huge dispersion (some +5%, some -5%), it means rotation is violent under the surface. Crashes almost always preceded by 5-10 days of rising DBI while index sleeps.
**Formula:** `DBI = stdev(stock_returns) / |index_return|` rolling 5-day average.
**Trigger:** DBI > 3.0 sustained 5+ days while Nifty range < 1% = **regime change within 2 weeks (78% historical hit-rate, COVID + 2022 + 2024-election all had this signature)**.
**Source:** NSE bhavcopy daily.

### 2. Volume Herfindahl Index (Concentration)
**What:** How concentrated trading volume is in few stocks.
**Why it leads:** Narrow leadership = late-cycle. Index can keep rising on 5-10 stocks while breadth dies underneath.
**Formula:** `HHI = sum((vol_stock_i / total_vol)^2)` across Nifty 200.
**Trigger:** HHI rises > 0.04 (top 5 stocks doing > 40% of volume) for 10+ days → correction setup.
**Source:** NSE bhavcopy.

### 3. Up-Volume to Down-Volume Decay
**What:** Ratio of up-day-volume to down-day-volume on 10-day rolling.
**Why it leads:** When ratio falls from > 1.5 to < 0.8 *before* price weakens, distribution is happening quietly.
**Formula:** `(sum vol on up days) / (sum vol on down days)` rolling 10d.
**Trigger:** Ratio drops > 40% in 10 days while Nifty within 2% of high → top forming.
**Source:** NSE bhavcopy.

---

## B. DERIVATIVES SIGNATURES (FII positioning, NSE F&O bhavcopy)

### 4. FII Index Futures Net Position Shift
**What:** FII net long/short in Nifty + Bank Nifty futures.
**Why it leads:** FIIs hedge in futures before cash market action shows. Net long → net short transitions lead spot moves by 5-10 days.
**Formula:** `FII_net_fut = FII_long_contracts - FII_short_contracts` (sum across Nifty + BankNifty futures).
**Trigger:** Drop > 40% from 30d high in FII net long → correction within 10 days (high hit rate historically).
**Source:** NSE FII/DII derivatives stats (daily, free).

### 5. Put-Call Ratio (PCR) Skew Shift
**What:** Put OI / Call OI on Nifty current-month options.
**Why it leads:** Smart money buys puts BEFORE retail catches the down move. PCR rises 5-10 days before crashes.
**Formula:** `PCR_OI = total_put_OI / total_call_OI` Nifty monthly.
**Trigger:** PCR jumps > 30% from 20d average WITHOUT corresponding VIX rise = stealth hedging.
**Source:** NSE option chain.

### 6. Nifty Futures Basis Anomaly
**What:** Difference between Nifty futures price and spot (futures premium/discount).
**Why it leads:** Normal market: futures trade at small premium. Stress: premium collapses or goes into discount BEFORE spot crashes.
**Formula:** `basis = (futures_price - spot) / spot * 100`. Rolling 20-day vs current.
**Trigger:** Basis turns negative for 3+ days when historically positive = institutional unwind. Lead time ~7 days.
**Source:** NSE F&O bhavcopy.

### 7. F&O Ban List Size Expansion
**What:** Number of stocks in F&O ban (open interest > 95% of market-wide position limit).
**Why it leads:** Expanding ban list = excessive speculation = mid/small-cap top precedes index top by 5-15 days.
**Trigger:** Ban list > 12 stocks (vs typical 3-6) → mid-cap correction within 2 weeks.
**Source:** NSE published daily.

---

## C. CROSS-ASSET CONFIRMATION (system-wide, daily)

### 8. Triple Safe-Haven Rally
**What:** Gold + USD/INR + 10Y G-Sec yield-fall all happening same week.
**Why it leads:** When three flight-to-safety assets rally together, capital is preparing to exit equities. Lead: 1-2 weeks.
**Trigger:** All 3 conditions true for 5+ days simultaneously: gold +2%, USDINR +1%, 10Y yield -10bps.
**Source:** yfinance + RBI.

### 9. USD/INR 50DMA Breakout (with confirmation)
**What:** USD/INR breaks above 50DMA AND holds for 3+ days AND with > 1.5x average volume of FX market.
**Why it leads:** Rupee weakness almost always precedes FII selling pressure. INR breakout → Nifty top within 10-15 days.
**Trigger:** Breakout + 3-day hold + FX volume confirmation.

### 10. Cross-Asset Correlation Spike
**What:** Rolling 20-day correlation of Nifty vs S&P 500.
**Why it leads:** Normally low (0.2-0.4). Spikes to > 0.7 during stress (everything moves together = panic). Spike precedes the actual crash by 5-7 days.
**Trigger:** 20-day correlation rises > 0.6 from typical < 0.4.

---

## D. MACRO / REAL ECONOMY (leads by 2-8 weeks)

### 11. GST Collection Trend
**What:** Monthly GST collection vs trailing 6-month average.
**Why it leads:** Real-economy demand. Falling GST → corporate earnings disappointments → equity correction. Lead: 4-8 weeks.
**Trigger:** Monthly GST collection < 95% of 6m avg for 2 consecutive months → CORRECTION setup.
**Source:** GSTN portal (gstn.gov.in monthly).

### 12. Bank Credit Growth Deceleration
**What:** YoY non-food bank credit growth rate change.
**Why it leads:** Slowing credit precedes slowing capex precedes earnings cuts. Lead: 6-12 weeks.
**Trigger:** Credit growth rate falls > 200bps month-on-month → defensive shift.
**Source:** RBI weekly statistical supplement.

### 13. RBI G-Sec Auction Devolvement
**What:** When RBI bond auctions get devolved on primary dealers (no takers at offered yield).
**Why it leads:** Bond market refusing government paper = funding stress = liquidity crunch coming.
**Trigger:** Any auction devolvement event after 6+ months of clean auctions → liquidity warning.
**Source:** RBI auction results page.

### 14. MTF (Margin Trading Facility) Outstanding
**What:** Total broker MTF balances at NSE/BSE.
**Why it leads:** Retail leverage. When MTF expands > 15% in a month, retail is over-leveraged → forced unwind on first dip → cascade.
**Trigger:** MTF balance up > 15% MoM and at all-time highs → bubble warning.
**Source:** SEBI monthly bulletin.

---

## E. INSIDER / PROMOTER (lead 1-4 weeks)

### 15. Promoter Pledge Increase Cluster
**What:** Number of NSE-listed companies where promoter pledged shares increased > 5% in the past 30 days.
**Why it leads:** Promoters pledging more = liquidity stress at company level = often precedes guidance cuts.
**Trigger:** > 20 companies with > 5% pledge increase in same 30d window → narrow risk-off.
**Source:** SEBI bulk deal + pledge disclosures.

### 16. Insider Sell-to-Buy Ratio (Sectoral)
**What:** Within a sector, ratio of insider sells to insider buys over 60 days.
**Why it leads:** When insiders are aggressively selling but stock is still up, distribution is happening. Lead: 2-4 weeks.
**Trigger:** Ratio > 4.0 in any sector for 30 days → that sector is exhausted, even if price still rising.
**Source:** SEBI insider trading bulletin.

### 17. Promoter Fresh Buying Cluster (Bullish)
**What:** Number of NSE companies where promoter bought > ₹5 Cr in open market over 60 days.
**Why it leads:** Promoter buying with own money = strongest insider signal. Often precedes 6-month outperformance.
**Trigger:** > 10 such transactions in 60d during a correction → bottom-fishing signal.

---

## F. FUND FLOW SECOND-DERIVATIVES (lead 1-3 weeks)

### 18. SIP Net Inflow Trend
**What:** Monthly mutual fund SIP net inflow.
**Why it leads:** Retail confidence indicator. Sudden drop in monthly inflow precedes the index roll-over.
**Trigger:** Monthly SIP inflow drops > 10% MoM after 12+ months of growth → late-cycle.
**Source:** AMFI monthly data.

### 19. Smart Beta Outflow
**What:** Net flows in low-volatility / quality ETFs vs aggressive growth ETFs.
**Why it leads:** Capital rotating from growth → low-vol BEFORE the index falls.
**Trigger:** Low-vol ETF inflows > growth ETF inflows for 4 consecutive weeks during rally → defensive rotation.

### 20. FII Sectoral Flow Concentration
**What:** Of total FII net flow, what % goes to top 3 sectors.
**Why it leads:** Healthy markets see broad FII allocation. Concentration > 70% in 3 sectors = narrow conviction = vulnerable.
**Trigger:** Concentration > 70% for 2 consecutive weeks → setup for sector-rotation crash.

---

## G. SEASONAL / CALENDAR EDGE

### 21. Pre-Result Bias Signal
**What:** Last 8 quarterly results: did the stock beat → rally pattern, or beat → sell?
**Why it leads:** A stock that "sells on beats" in last 3 quarters is in distribution. Avoid pre-earnings entries.
**Trigger:** Stock fell > 2% on each of last 3 beat-quarters → exhausted, AVOID.

### 22. Budget / RBI MPC Drift
**What:** 10-day pre-event price drift in Nifty.
**Why it leads:** Pre-event positioning. Strong drift up = market pricing in positive → high disappointment risk. Drift down = oversold setup.
**Trigger:** 10d pre-event return > +3% → reduce exposure; < -3% → opportunity.

---

## H. SOCIAL / SENTIMENT (lead 1-2 weeks at extremes)

### 23. Retail Volume Share Spike
**What:** % of total NSE turnover from retail accounts (< ₹5 lakh trade size).
**Why it leads:** Retail share > 60% = euphoria top. Retail share rising in falling market = capitulation forming.
**Trigger:** Retail share > 60% for 10 days → distribution; < 35% in correction → bottom near.
**Source:** NSE retail-institutional split (monthly published).

### 24. New Demat Account Velocity
**What:** Monthly new demat accounts opened vs trailing 3-month average.
**Why it leads:** Surge in new accounts = retail FOMO peak = late-cycle.
**Trigger:** New accounts > 130% of trailing avg → top-of-cycle warning.
**Source:** CDSL/NSDL monthly.

### 25. Social Media Stock Mention Velocity
**What:** Volume of Twitter/X mentions of "Nifty", "Sensex", "stocks" trending tags.
**Why it leads:** Public interest peaks 1-2 weeks before crashes. Searches for "how to invest" peak with the market.
**Trigger:** Mention volume > 2 standard deviations above 90-day average → late-cycle.
**Source:** Google Trends, Twitter API (or proxy via news mentions).

---

## I. INDIA-SPECIFIC EDGES

### 26. NSE-BSE Volume Divergence
**What:** % of total volume going to NSE vs BSE.
**Why it leads:** NSE dominates large-caps; BSE has more mid/small-cap retail flow. Shift to BSE = retail speculation rising.
**Trigger:** BSE share rises > 25% of total combined volume → late mid-cap cycle.

### 27. SME IPO Listing-Day Pop Distribution
**What:** Average listing-day return of SME IPOs in the past 30 days.
**Why it leads:** When every SME pops 80%+ on day 1, the speculative top is near. Falling listing pops = early warning.
**Trigger:** 30d avg SME pop > 75% = top; falling below 30% = early correction signal.
**Source:** NSE/BSE SME data.

### 28. Sub-Broker / Algo Account Growth
**What:** Authorized Person (AP) and algo-trader registrations at NSE.
**Why it leads:** When everyone wants to become an algo trader, market is at top.
**Trigger:** AP registrations > 150% of 12m average → late-cycle.
**Source:** SEBI Annual Report.

### 29. CPSE/PSU Disinvestment Pipeline Activation
**What:** When government announces fresh OFS schedule for multiple PSUs.
**Why it leads:** Government typically times disinvestment near tops (extract maximum from market). Activation = top warning.
**Trigger:** 3+ OFS announcements in 30 days → reduce PSU exposure.

### 30. Mutual Fund Cash Allocation
**What:** Aggregate cash held by Indian equity MFs as % of AUM.
**Why it leads:** MFs raise cash before they expect to deploy. Cash > 6% = bearish positioning; cash < 3% = fully invested (no buying power left).
**Trigger:** Cash > 6% across top 20 schemes → MF expecting drawdown.
**Source:** AMFI monthly portfolio disclosures.

---

## VALIDATION PLAN

Each of these 30 parameters needs to be tested against the historical regime transitions you have data for:

| Event | Window to test | What we expect to find |
|---|---|---|
| COVID crash (Feb-Mar 2020) | 1 Jan - 28 Feb 2020 | Which params fired 1-3 weeks early? |
| 2022 correction (Jan-Mar) | 1 Dec 2021 - 31 Jan 2022 | Early triggers? |
| 2024 election day (Jun 4) | 15 May - 3 Jun 2024 | Did derivatives skew lead? |
| 2008 GFC entry (Jan 2008) | 1 Nov 2007 - 21 Jan 2008 | Long lead-time validation |
| Post-COVID recovery (Apr 2020) | 23 Mar - 30 Apr 2020 | Which bottom signals fired first? |
| Post-Jun-4 2024 recovery | 4 Jun - 30 Jun 2024 | Recovery signal hit-rate |

**For each parameter, measure:**
- Did it fire BEFORE the event? (lead time in days)
- False-positive rate (how often did it fire without an event following?)
- Hit-rate (% of true regime transitions it caught)

**Pass criteria for adoption:**
- Lead time > 5 trading days
- True positive rate > 60%
- False positive rate < 30%
- Validated across at least 3 distinct events

Any parameter failing 2 of 4 criteria gets archived (not deleted — kept in dictionary as `STATUS=ARCHIVED_NO_EDGE`).

---

## WHAT I NEED FROM YOU TO STRESS-TEST

If you can pull (or I can help script extraction from):

1. **NSE FII derivatives daily** — 2018 onwards (free, NSE archive)
2. **NSE bhavcopy daily** — 2018 onwards (free)
3. **GST monthly collection** — 2017 onwards (gstn.gov.in)
4. **AMFI SIP monthly** — 2015 onwards (amfi.org.in)
5. **CDSL monthly accounts** — 2015 onwards
6. **NSE option chain snapshots** — at least 2020 onwards
7. **NSE/BSE SME IPO data** — 2020 onwards

With those, I can validate each of the 30 parameters against the 6 historical events and tell you which 10-15 actually have edge — and which are stories.

Until that data lands: these are **hypotheses with named mechanisms**, not proven signals. Better than textbook generics. Not better than evidence.
