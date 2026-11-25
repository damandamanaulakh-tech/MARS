# New-position investigation — 25 November 2026

Read-only observation during 10:51–11:10 IST. No production scan, buy, sale,
threshold change or broker request was induced. Live revision remained
`8e894dd39ae393d4589be4103bf226f8ef75445c`, deployment
`dep-dc08ac6q1p3s73dfd4d0` (20 November). The six-segment allocation draft
was not deployed at observation time.

## First inspected intraday result

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

## Later scan: the same defect reproduced in normal activity

Read-only follow-up at approximately 11:35 IST found completion at
2026-11-25T05:44:48.379750 UTC (11:14:48 IST): **1,133 stocks, zero ranked,
zero picks and zero opened**. The exact breakdown is 1,014 fetch-capped,
82 deadline-labelled, one `instrument_mapping_unavailable` (NOVARTIND.NS),
and 36 initially ready stocks whose scoring records all report `stale_bar`.
These counts sum to 1,133; the unavailable fetch total is 1,097.

The initial validation at 05:26:39.470092 UTC (10:56:39 IST) accepted 36
stock frames and the benchmark, with bars ending at 05:25 UTC (10:55 IST).
Scoring ran from NCC at 05:44:16.298529 UTC to OFSS at 05:44:38.484690 UTC;
bar age was 1,156.30–1,178.48 seconds against the 360-second limit. All 72
archived records for the 36 ready tickers were decompressed and their BSON
checksums/envelopes verified. Exactly 36 score records cover all 36 tickers;
the other records describe the same stocks and are not additional candidates.

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

### Later portfolio observation

The persisted book at 06:08:47.980153 UTC (11:38:47 IST) has 21 open
positions and 92 closed records, cash INR 43,762,074.39798673, and the daily
loss halt remains false. Current segment counts are Momentum 3, Short 3,
Swing 5, Positional 10, Intraday 0 and Ocean 0. Four normal exits at
approximately 11:30–11:31 IST account for the difference from the earlier
25-open/88-closed observation: MINDACORP, PNB and BANKBARODA closed on
`TRAILING_STOP`; KPRMILL closed on `MAX_HOLD`. This is observed exit activity,
not a claim that every possible protective failure case has occurred live.
Health also confirms an acknowledged save at 06:08:07.893703 UTC with no
entry-block reason. No diagnostic scan or trade was induced.

## Scheduler evidence and its limits

The authenticated dashboard at approximately 11:28 IST reports no rotation
today and a latest single-mode scan at 11:14 IST. It does not show the
`DAILY_SCHEDULER=0` warning. Source inspection confirms this warning reads
`daily_enabled()`: the setting is enabled, but this does not prove the
scheduler is running or that all jobs are registered.

Render logs confirm `[09:20] another multi-mode scan is already running —
skipped` at 20 November 09:05:54.557604987 UTC and `[14:30] a category
rotation is still running — skipped` at 23 November 09:00:00.001826883 UTC.
The instances differ from the current 25 November instance; an old in-memory
lock cannot itself persist across those restarts. These are verified skipped
overlaps, not proof of which stage held the lock.

The deployed scheduler acquires `_ROTATION_LOCK` before corporate actions,
daily history top-up, breadth and market/sector refresh, and only then creates
the category checkpoint. History top-up waits on `_TOPUP_LOCK` without a
timeout and calls `top_up_history()` without its optional fetch/time limits.
A long preparation stage can therefore leave an old checkpoint and block the
next scheduled rotation while manual scans remain possible. This mechanism
is supported by source, but the specific stage responsible for the observed
stalls remains unknown without stage timing or a live thread stack. Likewise,
the save retry acknowledges the book but does not itself invoke daily catch-up.
Absent INFO logs are not treated as proof that a job never ran.

### Observed service shutdown and startup protection

The Render service API confirms that the actual MARS web service has compute
plan `free` and one instance, despite the repository blueprint's Starter
declaration. Logs show the old process shut down at 06:23:16 UTC (11:53:16 IST),
with startup of a new instance completed at 06:30:09 UTC (12:00:09 IST), on
the same 20 November functional revision. No deployment/restart was requested
by this investigation. Render's exact shutdown cause was not returned.
[Render documents](https://render.com/docs/free#spinning-down-on-idle) that free
web services sleep after 15 minutes without inbound traffic and restart when
traffic returns. The observed gap is consistent with that limitation; the
in-process scheduler cannot execute while the web process is stopped.

At startup, health correctly reported `portfolio_save_unconfirmed`, with no
acknowledged save for the new process. The normal save retry acknowledged
the book at 06:31:12.157462 UTC (12:01:12 IST), clearing the entry block and
degraded state. This temporary startup guard is distinct from the earlier
completed scans' stale-data failures. No compute-plan change or charge was
authorized or performed. Reliable unattended scheduling requires a deployment
that remains running; recovery after a restart does not execute jobs during
the stopped interval.

Render metrics for 05:00–05:50 UTC also report a CPU limit of **0.15 CPU**.
During the two scan windows, several five-minute usage measurements are
0.140–0.150 CPU, including exactly 0.15 at 05:10 and 05:40 UTC. Thus the
service was near or at its available CPU capacity while bars were ageing.
This supports capacity pressure as a contributor to delay; it does not isolate
the cost of individual pipeline stages. The bounded event-history query
returned no platform reason for the shutdown, so that cause remains unproved.

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
