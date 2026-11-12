# SEQ-04 — completed intraday data

Owner instruction, 12 November 2026: use completed intraday bars, check timeframe
and session, reject future or unfinished bars, show missing data, test and update
the repository and workflow without skipping. Baseline main:
`0090a4c2ee5f9d0006b8e772f3363d25109cfa91`; baseline functional release:
`5bee374a6c9de71b2bb15203434f90fa0a7eb7c1`.
Scope: INTRA-001 and ROOT-AM07-04. [Programme](../EXECUTION_PLAN.md).

## Failure reproduced

Six new cases failed against baseline: a nominal intraday scan consumed daily
history when intraday data was absent, and delayed quotes with future, unfinished,
previous-session, stale or missing bar timestamps remained executable. The same
cases pass after this repair. Historical fills and the 74-trade audit ledger are
preserved; their missing original observations are not reconstructed.

## Connected data and decision paths

| Requirement | Implementation and proof |
|---|---|
| Actual five-minute input | Upstox V3 `minutes/5` current-session and historical endpoints are called separately, with original timezone-bearing bar-start timestamps. The loader joins five regular trading sessions and never substitutes daily candles. Adapter tests assert the actual paths, interval and date order. |
| Completed bars | A candle is eligible only after start + interval. Future and unfinished rows are removed before any calculation; valid earlier completed rows may still be used. Delayed quote selection similarly chooses the newest valid completed minute, not array element zero. |
| Session and alignment | Regular NSE equity session 09:15–15:30 IST, five-minute grid from 09:15, known holidays and weekends excluded. Naive provider timestamps, outside-session bars and wrong/unproved intervals are rejected. Engine naive clocks are explicitly UTC. |
| Missing and invalid data | Missing OHLCV, non-finite/non-positive prices, impossible high/low, negative volume, short lookbacks and gaps stay unavailable. Identical duplicates deduplicate; conflicting duplicates are removed. No forward fill or invented zero-volume bar. Actual zero-volume index candles remain valid. |
| Freshness | Latest completed bar must be from the current regular session. Its close may be at most one interval + 60 seconds old: 360 seconds for 5m signals, 120 seconds for 1m delayed quotes. This is an explicit operational freshness allowance, not an optimized profit parameter. Equality is accepted; one instant beyond the boundary is rejected. |
| Same-period signals | Intraday scoring, timing, candle evaluation, pick charts and new ATR targets consume validated 5m frames. Mixed-book refresh selects by each holding's saved segment. Benchmark and sector-peer return windows require matching timestamps. Missing benchmark remains the existing neutral 50 and is explicitly labelled unavailable. |
| No silent strategy rewrite | Existing weights, window counts, floors, stop/target bands and timing thresholds remain. Counts on the intraday path are now completed five-minute bars: default momentum 21, relative strength 63, breakout 20, volume 5/20, volatility 21, minimum complete window 64. Volatility uses 75 five-minute bars per normal session. There is no new ORB/VWAP implementation or claim. |
| Daily context stays labelled | Liquidity, daily volatility sizing, correlation, regime/breadth/damage, FII/hot-sector overlays, the optional daily multi-formula check and Ocean's 200DMA/weekly selection keep their original daily inputs. An Ocean pick allocated to intraday also needs valid 5m execution data. No daily median ATR is advertised as a measured intraday median. |
| Late work | Data is revalidated before rotation eligibility, before opening, and after target computation immediately before booking. Minute quotes are checked again at booking, after rescoring, before danger action and at partial/final exit booking, so expiry cannot manufacture a fill, stop amendment or trailing peak. Owner market/Buy Desk calls report the refusal and resting limits remain open with the reason saved. Scheduled closing retains SEQ-02's independent deadline/retry behavior. |
| Missing bars and protection | Missing current score is unavailable, not evidence of score decay or a reason to tighten a trail. With a valid quote, hard stop, target, ordinary trailing stop, max hold and deadline recovery remain active. Invalid delayed quotes cannot replace retained valuation marks. |
| Visible and durable coverage | Scan has a saved coverage count plus a row for every requested stock, source, session, last bar end, rejected rows, missing bars and reason. Decision pages show the actual interval/window and quote verdict. JSON and Mongo preserve this observation and the next fetch offset; page reads do not fetch or trade. |

## Data contract and limits

The loader has a 120-name cap including the benchmark, four workers, eight-second
per-request timeout and 90-second batch deadline. Historical completed data is
cached per instrument/day in a bounded 1,600-entry memory cache; current-session
data is fetched again. A restart refetches history. Queued work is cancelled at
the deadline and late completions cannot enter the returned decision data.
The next scan rotates through remaining stocks and persists its offset. Every
unfetched, unmapped, timed-out, unauthorized, rate-limited or empty response is
shown explicitly. A finite request budget is not full-universe coverage; the
saved ready count is an observation at its timestamp, not a current guarantee.

The verified schedule covers **regular 2026 NSE equity sessions**. Special
sessions (including Muhurat) require a separately verified schedule. The current
shared calendar has only partial 2027 holidays; intraday validation returns
`session_calendar_unverified` for that year until the official calendar is added.
This must be maintained before the year changes. No future schedule is guessed.

Provider contracts and calendar evidence checked for this batch:

- [Upstox V3 intraday candles](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/): timestamp identifies candle start; five-minute interval is explicitly requested.
- [Upstox V3 historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/): prior-session minute history and inclusive date parameters.
- [NSE regular market timings](https://www.nseindia.com/static/market-data/market-timings): regular equity session.
- [NSE 2026 equity holiday circular CMTR71775](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf) and [NSE holiday calendar including 15 January](https://www.nseindia.com/resources/exchange-communication-holidays?apiversion=v2): current regular-session dates.
- [NSE clearing circular referring to CMTR72260](https://nsearchives.nseindia.com/content/circulars/CMPT72278.pdf): additional 15 January 2026 election holiday.

The LTP endpoint still does not provide an exchange trade timestamp. Receipt
time is not relabelled as provider time. Owner-reported holdings and explicit
manual entries keep their distinct authority; they do not claim automatic scan
gates. Existing legacy injected quote adapters remain labelled unknown. The
broader provider, persistence and concurrency findings remain open.

## Validation and shipping ledger

Read-only public provider probe on **12 November 2026 09:48:35 IST**: both V3
endpoints returned HTTP 200 without credentials. Nifty 50 supplied 321 input
rows; the production validator retained 306, rejected one unfinished candle and
14 non-regular-session rows, and found zero missing bars in the required
64-bar window. Last accepted bar: 09:40–09:45 IST. This proves development-host
endpoint parsing and validation on observed data, not Render ingestion or
stock-universe coverage. [Exact probe evidence](../evidence/2026-11-12-intraday-provider-probe.json).

LOCAL_TESTED. Six baseline failures reproduced; **69 new cases** and **150 focused/related checks** pass. Final local full suite: **3,063 passed, zero failures, errors or skips**, Python 3.12, 192.22s. CI totals, exact PR/head, merge, deployment and health observations will be appended once verified. The final review added expiry checks after slow work, at partial/final exit booking and before danger stop changes, plus missing daily-context coverage.
No test is skipped and the existing CI no-skip guard remains enabled.

Normal production five-minute scan coverage, missing-feed handling and subsequent
trade evidence are separate runtime observations. No production scan, trade or
outage is induced to obtain them. INTRA-001 retains its research subclaim:
daily-history backtests cannot establish this corrected intraday path's returns.
Costs, slippage, intrabar uncertainty, out-of-sample and forward paper validation
remain in SEQ-05 and SEQ-08–11. This data repair makes no profitability claim.
