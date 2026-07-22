# 5-Event Backtest Report — REAL NSE data (2026-07-10)

Engine: nifty200_momentum mode, quality blend 0.35, floor 55, exits ON
(stop −12% / target +30% / trailing / max-hold — measured deliberately;
live app currently runs buy-and-hold). Sizing 6.67% × 15 slots (15-name
universe needs full deployability). All other live params untouched.

Data: real NSE daily bhavcopy, 16 mega-caps, 2016-02 → 2026-06 (user's
Drive "15 stock 10 years trades"), corporate actions back-adjusted
(9 actions detected incl. TATASTEEL 10:1 2022, RELIANCE/TCS/INFY/HDFCBANK
bonuses, DRREDDY 1:5). Cleaned dataset committed: `data/history_15stock/`.
Benchmark = equal-weight basket of the same names (real Nifty history not
available cloud-side; basket alpha = pure stock-picking + timing skill).

| Test | Strategy | Basket | Alpha | Strat DD | Basket DD | Win% | Sharpe | Trades |
|---|---|---|---|---|---|---|---|---|
| GENERAL BULL 2017 | +20.5% | +29.7% | −9.3 | −3.3% | −5.0% | 74 | 3.14 | 27 |
| COVID 2020 | +9.8% | +21.2% | −11.5 | **−15.5%** | **−36.0%** | 45 | 0.86 | 42 |
| WAR (Ukraine) 2022 | −1.0% | +4.5% | −5.6 | **−6.3%** | **−16.1%** | 46 | −0.05 | 26 |
| GENERAL CHOPPY 2023 | +8.9% | +12.2% | −3.3 | −5.8% | −8.8% | 70 | 1.26 | 33 |
| ELECTION 2024 | +13.9% | +12.0% | **+1.9** | −7.7% | −12.5% | 51 | 1.41 | 37 |

## Which parameters HIT (worked as designed)
- **Drawdown ladder + stops + regime = crash armour.** Every stress test cut
  drawdown to ~40-60% of the market's (COVID −15.5% vs −36%).
- **Election shock handling: only positive-alpha test.** Jun-4-2024 −6% day →
  stops cut fast, re-entered, targets banked (+₹3.7L TAKE_PROFIT PnL).
- **STOP_LOSS −12%**: expensive (−₹8.0L net over 5 tests) but it IS the
  insurance premium that bought the small drawdowns. Keep.

## Which parameters NEED TWEAK (evidence)
1. **TAKE_PROFIT +30% cuts momentum winners.** 2017: banked +₹3.7L but the
   sold winners kept running → alpha −9.3. Momentum edge = ride the tail.
   → Proposal: momentum mode target OFF (or +50%), let trailing manage.
2. **TRAILING stop too tight in high-vol.** War-2022: 11 trailing fires,
   −₹1.1L churn. → Proposal: volatility-scaled trail (wider when VIX high).
3. **Slow re-entry after crash-stops.** COVID: only 74.6% time-in-market —
   missed part of the V-recovery. → Proposal: regime-flip re-arm (immediate
   rescan when regime returns to bull).
4. **MAX_HOLD time-outs were PROFITABLE exits (+₹3.9L)** = it kept cutting
   live winners. → Proposal: momentum mode holds while score > decay floor,
   no fixed day limit.
5. Buy-and-hold basket beat the exit-managed strategy 4/5 times but with
   2-3× the drawdown — the user's hold instinct is half-right: hold more,
   but keep the crash brakes.

## Caveats (honest)
16 mega-caps only (not Nifty 200) · fixed still-alive list = mild
survivorship flavour · benchmark is self-basket, not Nifty TRI ·
data hole 02→06/2021 stretches early-2022 momentum lookbacks ·
BAJAJFINSV 2020-03-23 circuit day partially mis-adjusted (−9.5% gap read
as corp action; softens its COVID crash slightly) · engine slippage model
as configured; no STT/brokerage modelled.
