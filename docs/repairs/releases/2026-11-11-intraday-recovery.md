# SEQ-02 — intraday closing and recovery

Owner instruction: start the next goal, recover missed closing jobs, restart,
missing quotes and late scans, preserve original strategy/stop/target and retain
unfinished work. Finding INTRA-002; goal G13; related OPS-04.
[Programme](../EXECUTION_PLAN.md), [original audit](../reviews/2026-11-11-intraday-trade-audit.md).

Baseline main: `031f79130495b5245e3e09b2c3ca9753b3040ed8`.
Baseline live functional revision: `499fcd550a881fcfca1a6e33d4db646811f83140`.

## Proven failure and repair

The historical audit records seven 29 October positions surviving overnight.
Their stops/targets widened to Momentum on the next boot while mode stayed
intraday. The historical cause of the missed original slot remains unproven.
Current code has a single square-off slot, no per-holding saved deadline or
retry, and no market-hours/deadline guard inside square_off_intraday itself.
Nineteen initial regression cases failed against the unmodified baseline.

- Each intraday holding now stores its 15:25 IST deadline, status, retry count,
  last attempt/error and creation time. Legacy holdings derive the deadline
  from their own entry day; a restart cannot postpone it. JSON and the Mongo
  mirror carry this record. Completed trades and SELL evidence retain it.
- Startup enrolls legacy holdings without trading; a separate startup recovery
  job runs after 30 seconds. A one-minute in-session retry is registered on
  either enabled trading scheduler track, independently of scan catch-up.
- Scans and refreshes recover overdue work before slow history/scoring. A
  refresh rechecks after history retrieval and scans recheck on completion.
- Valid quotes close only due intraday holdings. Missing/invalid quotes and
  quote errors remain pending. Market-closed work waits for the next session;
  the clock is rechecked after quote retrieval, including seconds at 15:30.
  Retained valuation marks never become execution prices.
- Recovery uses the shared mode lock without waiting behind a scan. A busy
  job defers; the durable deadline, scan recovery and next retry preserve work.
  Each completed holding is saved separately; one failed close does not stop
  the remaining names. Repeated passes do not close the same holding twice.
- Overdue intraday holdings retain their mode and original stop/target at boot.
  Custom levels remain protected. Existing valid stop exits still work before
  the deadline; successful deadline exits continue to use SQUARE_OFF.
- Automatic candidates, rotation funding, final fills, owner market buys and
  intraday limit fills reject entry at/after 15:25. A late resting instruction
  remains unfilled. The normal selected segment and its funds are preserved.
- Open Positions shows the deadline, overdue status and retry count without
  mutating the book when viewed. Owner buys receive a readable cutoff reason.

The prior test that explicitly expected overnight Momentum widening was
updated to assert the owner's newly requested original intraday mode/levels;
its assertions remain active. The existing test-only market-clock override
continues to support replay; the new boundary tests turn it off and pin UTC
and IST clocks. Production configuration does not enable that override.

## Validation and evidence boundaries

42 new cases and 104 focused checks pass. Coverage includes the actual scan/refresh paths,
restart JSON/Mongo round trips, all/partial quote failures, invalid numerical
prices, quote exceptions, late responses, seconds after the market close,
pre-deadline stops, custom levels, lock contention, one-name close failure,
reopened symbols, delayed scans, owner/limit orders, scheduler registration
and read-only page rendering. The first full suite passed 2,930 tests with zero
skips in 213.01 seconds. Review then reproduced two additional failures: a
rescore crossing market close could execute on the earlier clock, and optional
pyramiding could add to a holding awaiting its deadline close. Both now have
passing regressions. Intraday close execution rechecks the session, due work
cannot be pyramided, and later risk liquidation cannot reuse an earlier quote
when deadline recovery has just failed to obtain a new one. The final full
suite and release results follow.

Read-only baseline at 16:14 UTC / 21:44 IST on 11 November: 49 open holdings,
zero intraday holdings, 193 closes; last saved 10:06:25 UTC / 15:36:25 IST.
No production trade, scan or outage was induced. A normal deadline recovery
cannot be claimed from a healthy deployment or an empty intraday book.

Provider observation time/fallback age remains SEQ-03. Existing quote-router
responses still have that source limitation; this batch validates the numeric
price and session, not exchange tick freshness. Repository-wide persistence
acknowledgement/recovery faults remain G5, and broader owner-mutation concurrency
remains G6. The scheduler still respects an explicit disabling of both trading
tracks. Historical P&L, fee assumptions and strategy thresholds are preserved;
this batch changes execution recovery and the owner-approved overdue handling.

Final local suite: **2,934 passed, zero failures/errors/skips**, Python 3.12,
212.33 seconds. JUnit totals and the no-skip condition were independently
checked. All changed Python sources parse, whitespace checks pass and the
repair validator retains all 170 findings, both incidents and the sequence.

## Shipped evidence — 11 November 2026

| Stage | Verified evidence |
|---|---|
| Reviewed PR | [PR #199](https://github.com/damandamanaulakh-tech/MARS/pull/199), head `49295f300f243c731ed727c1f8d8c5523bd864b8`; all 22 published files byte-match the tested local files |
| PR CI | [Run 43058041445](https://github.com/damandamanaulakh-tech/MARS/actions/runs/43058041445): **2,934 passed**, zero skipped, 3 warnings, Python 3.11, 354.00 seconds; repair-records and the no-skip guard passed |
| Merge | `a9919f6446690a62b76914fb9178009aaacf55e4`, 16:31:49 UTC / 22:01:49 IST; merged tree `012024026f9ee7468f72bc0ad0f9605fd392a591` matches the reviewed tree |
| Deployment | Render `dep-dbq9gu59el5s73e0va70`, automatic deployment of that exact merge; **live** at 16:33:44 UTC / 22:03:44 IST |
| Health | At 16:35 UTC / 22:05 IST: `/healthz` reports `ok=true`, production `ready=true`, DB connected, degraded/boot-degraded false, no write error. No error-level logs returned for the deployment window through 16:35:25 UTC |
| Application startup | Render logged application startup complete at 16:33:36 UTC. This is application startup evidence, not proof that the recovery job ran |
| Recovery-job observation | The filtered log query through 16:35:25 UTC returned no recovery-job execution row. Registration/callback/lock behavior is covered in isolated tests; deployed execution remains unconfirmed |
| Read-only portfolio | At 16:35 UTC, 49 open holdings, **zero intraday holdings**, 193 closes; last save still 10:06:25 UTC, unchanged from the pre-release baseline |
| Normal deadline outcome | Pending. There was no intraday holding available to observe recovering. No production trade, scan or outage was induced |

Current state: **DEPLOYED_HEALTH_VERIFIED_RUNTIME_PENDING**. The engineering
repair is shipped. Keep normal recovery-job and deadline behavior open until
observed in ordinary operation. SEQ-03 is next queued and has not begun
implementation; its quote/gate provenance scope remains separate.
