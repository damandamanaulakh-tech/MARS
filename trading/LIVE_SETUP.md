# How to use the trading dashboard

This is the **paper-trading** module. It picks NSE swing-trade candidates
using AM 07 scoring, simulates buying them with virtual money, tracks the
positions over multiple days, and shows you the results. **No real money
is touched.**

## What "paper trading" means here

- Capital is virtual (default Rs 25,00,000)
- Prices are real (delayed ~15 minutes from NSE via yfinance)
- Orders are not sent anywhere — they're recorded in a JSON file
- You see exactly what would have happened if the trades were real

When you're comfortable with what the system does — typically after a few
weeks of paper-trading — we can wire it to your Upstox account for real
orders. That is a separate step and will only happen with your explicit
go-ahead.

## Using it from iPad (or any browser)

You don't install anything. The whole thing runs as a tab inside the
existing `sourceborn-engine` Hugging Face Space.

1. Open your HF Space URL in any browser (iPad Safari is fine)
2. Scroll past the chat
3. Find the **Trading: Summary** tab
4. Click **Run pre-market scan now**
5. Wait ~30 seconds (it's pulling 6 months of data for ~30 stocks)
6. Look at the results. Top picks appear in **Trading: Picks**.

Open the Space at least once every 2 days so it doesn't go to sleep on
the HF free tier.

## What each tab shows

| Tab | What's there |
|---|---|
| Trading: Summary | One-glance status: equity, cash, deployed, today's picks, open positions, kill-switch status |
| Trading: Picks | Top-20 AM 07 ranking from the last scan, with score and reasoning |
| Trading: Trades | Every closed trade with entry, exit, P&L, reason for closing |
| Trading: Equity | Equity curve over time |
| Trading: Reports | Click to generate fresh CSV exports. Then click each file to download. |
| Trading: Settings | All risk rules and AM 07 weights, plus the **Reset** option |

## The rules the system follows

**Capital rules**
- Always keeps 20% of capital as cash reserve (max 80% deployed)
- Maximum 10% per single share
- Maximum 10 open positions
- Maximum 3 positions in any one sector

**Per-trade rules**
- Stop loss: -5% from entry
- Take profit: +10% from entry (R:R 1:2)
- Trailing stop: -8% from peak (kicks in once profitable)
- Score-decay exit: closes if AM 07 score drops below 50
- Maximum holding: 60 trading days

**Portfolio rules**
- Daily loss kill switch at -2% (halts all new entries for the day)

**Entry filters (must pass all)**
- AM 07 score >= 70
- Price within 2% of 20-day high
- 5-day volume >= 1.3x 20-day volume

## What's not real yet (honest list)

Three of AM 07's ten scoring components don't have free NSE data sources,
so they are stubbed at neutral 50:

- `catalyst` (10% weight) — needs a news API
- `short_interest` (5% weight) — no free NSE source
- `analyst_revision` (5% weight) — paid feeds only

So you're getting 80% of the AM 07 signal from real data. The remaining
20% becomes useful when we add real feeds later — no engine change needed.

Also:
- Data is delayed ~15 min (yfinance). Fine for swing (multi-day holds).
  For intraday this gets replaced with Upstox real-time later.
- HF Spaces free tier sleeps after 48 hr inactivity. Opening the dashboard
  wakes it up. Paid tier or a small VPS keeps it always-on.

## Resetting

If you want to start over: **Trading: Settings** tab -> type `RESET` ->
click the red button. Wipes positions, trades, history. Cash restored.

## Running the smoke tests (optional)

```
pip install -r requirements.txt pytest
PYTHONPATH=. pytest trading/tests -q
```

12 tests cover the risk gates and exit logic. They pass without any
network access in under a second.
