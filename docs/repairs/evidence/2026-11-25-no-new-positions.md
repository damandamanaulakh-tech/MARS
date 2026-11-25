# New-position investigation — 25 November 2026

Read-only observation during 10:51–11:10 IST. No production scan, buy, sale,
threshold change or broker request was induced. Live revision remained
`8e894dd39ae393d4589be4103bf226f8ef75445c`, deployment
`dep-dc08ac6q1p3s73dfd4d0` (20 November). The six-segment allocation draft
was not deployed at observation time.

## Latest intraday result

Stored scan timestamp: 2026-11-25T05:19:37.682057 UTC (10:49:37 IST).
Universe: 1,129 stocks; ranked 0; picks 0; opened 0.

| Stage | Stock count | Evidence and interpretation |
|---|---:|---|
| Not requested under fetch cap | 1,010 | `intraday_fetch_cap`; cap is 120 requests including one benchmark, at most 119 stocks per pass |
| Reported deadline/missing fetch | 82 | `intraday_fetch_deadline`; 90-second budget, four workers. Code can relabel completed empty responses when another request times out, so these are reported deadline outcomes, not proof of 82 individual timeouts |
| Data initially ready, stale at scoring | 37 | All 37 archived decisions were decompressed and their raw BSON SHA-256 verified. Every score gate records `stale_bar`. Valid completed bars existed at initial fetch; slow work before scoring aged them out |
| Total reconciled | 1,129 | 1,010 + 82 + 37; no candidate reached buying |

The initial data snapshot was 05:01:35.977935 UTC (10:31:35 IST), with
37 stock frames and the benchmark ready. Last completed bars ended at
05:00 UTC. LAURUSLABS scoring revalidated at 05:18:56.475676 UTC, age
1,136.476 seconds. MANAKCOAT at 05:19:26.376211 UTC was 1,166.376 seconds
old. The completed-five-minute-bar rule allows 300 seconds plus 60 seconds
publication grace. The freshness rejection is correct; the delayed scan
pipeline is the defect. Do not backdate scoring or relax freshness to buy.

Examples initially ready but stale: LATENTVIEW, LAURUSLABS, LENSKART,
LEMONTREE, LICI, LT, LUPIN, M&M. Cap examples: SASKEN, DRREDDY, ALIVUS,
RELIANCE, SYNGENE. Deadline-label examples: MANALIPETC, MANAPPURAM,
MANCREDIT, MANGLMCEM, MANINDS.

## Other segments and infrastructure

- Latest retained Momentum shortlist: 20 November 09:10:14 UTC; 1,026 ranked,
  254 picks, all 254 `measured_kelly_no_edge`. Journal: eight eligible closed
  exits, about 12% wins, payoff 0.27, Kelly -1.627. This is historical evidence,
  not a fresh 25-November Momentum pass or a market-wide win rate.
- Latest complete multi-segment rotation record remains 11 November.
  Active/daily records are 20 November with empty passes. These are stale
  checkpoints, not proof a rotation is still running. Current Short, Swing,
  Positional and Ocean gate results cannot be inferred from those old records.
- Open holdings: Momentum 3, Intraday 0, Short 6, Swing 6, Positional 10,
  Ocean 0; 25 total. Closed ledger: 88 rows. Eight exits on 23 November
  explain the decline from 33 open/80 closed on 21 November. This proves
  recorded exits occurred; it is not proof all current protection will work.
- Cash: INR 41,900,955.482066736. Entry cost deployed: approximately
  INR 7.48 million. No observed current intraday refusal was a budget refusal.
- Health read: ready true, DB connected, degraded false, write_error null,
  entry_block_reason null. Current book save acknowledged at
  2026-11-25T05:21:15.270066 UTC. Persisted risk_day_killed false;
  loss_count 0. Cluster reported approximately 355 MiB / 512 MiB.
- Upstox returned quote HTTP 401 at 09:21 IST, then stored a new connection
  at 09:25:01 IST. The later validated intraday frames demonstrate subsequent
  data access. Do not present the earlier 401 as a current universal outage.
- App calendar treats 24 November as a holiday. This explains that day's
  planned absence only; it does not explain 23 or 25 November gaps.

## Required follow-up (not implemented by this investigation)

1. Move/refresh bounded completed-bar batches immediately before scoring;
   keep data fresh through final booking and preserve all session/exit checks.
2. Retain unfinished fetch work or advance the cursor by actual work, with
   correct distinctions between queued, timed out, empty and failed results.
3. Reproduce rotation interruption/recovery and persist current per-segment
   completion or explicit failure; never label an old checkpoint as running now.
4. Complete the independently approved allocation release; changing budgets
   alone cannot fix these data problems or remove Momentum's measured-edge gate.

Sources: authenticated read-only Mongo projections of `portfolio`, `settings`,
`upstox_token`, and `journal_archive`; Render service/deploy/application logs;
public `/healthz`; deployed source at the SHA above. Private raw BSON and
account session data are excluded from Git. No research setting is promoted.
