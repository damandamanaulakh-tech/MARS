# SEQ-01 — partial-quote portfolio equity

Owner authorization: run the repair sequence one batch at a time and track it
in the repository. Goal G4; finding AUD-002. [Programme](../EXECUTION_PLAN.md).

Baseline main: `dabadae49f9fb412df1fca8936525032dbd72373`.

## Reproduction

The scan evaluates its daily loss halt on the raw partial quote dictionary
before assembling the fuller valuation marks. Missing holdings revert to entry
prices. Refresh can similarly replace a more recent observation with an older
stored close, and an all-quote outage returns entry-valued equity.

An unchanged test book has INR 39 lakh cash, 10,000 AAA shares at 100 and 1,000
BBB shares last observed at 300 (entry 100): INR 52 lakh. When only AAA is
quoted, the old scan measures INR 50 lakh and trips its 2% daily halt. The
initial new regression file produced **13 failures and 3 passes** on the
unmodified baseline. Existing genuine-loss behavior was included as a control.

## Repair boundaries

- Persist each holding's last positive finite observed price, source, receipt
  time and source date where available. A carried observation keeps its clock.
- Use those observations before daily-loss and drawdown checks, sizing and
  equity-history recording. An older daily close cannot erase a newer quote.
- When no observation exists for a legacy holding, label the entry-only
  estimate, defer new aggregate loss/drawdown decisions and equity points, and
  refuse new automatic scan entries with `portfolio_marks_incomplete`.
- Quoted protective exits and previously authorized pending liquidation remain
  active. Retained valuation prices are never copied into executable quotes.
- Reject invalid numerical quotes; preserve the original observation on gaps.
- Save observations through JSON/Mongo, keep split/bonus per-share units
  consistent, and prevent a reopened ticker inheriting a closed holding's mark.
- Risk shows the retained prices and their source/time without updating the
  governor or fetching data when the page is opened.

Receipt time is **not** a provider observation timestamp. The quote router's
underlying fallback-age and live-label defects remain tracked separately for
SEQ-03. Retained prices are estimates, not proof that the missing stock's
market price has stayed unchanged. Thresholds and historical trades are not
changed or restated by this repair. Other screens still have their separately
tracked quote-label/value-consumer work.

## Validation and release

Focused failures reproduced and repaired. The final focused group passed
102 tests. JSON/Mongo, legacy data, invalid quotes, actual scan/refresh paths,
existing halt preservation, authorized cuts, stock splits and read-only Risk
rendering are covered. Review found that a pre-split stored close could undo
an adjusted retained mark; a failing-before regression now prevents it.

The first full run passed 2,885 and failed six category-allocation cases. Those
fixtures gave old holdings neither a quote nor an observed price, so the new
explicit data-gap refusal correctly preceded their intended budget checks.
Their fixtures now provide a retained observation while preserving the lack
of an executable quote. All original assertions remain; the budget-cap case
also explicitly requires its expected new purchase. No allocation formula was
changed. Final local, PR CI, merge and deployment evidence follow.

Read-only pre-release portfolio observation: 49 open holdings, 193 retained
closes and no persisted last-mark records; last saved 11 November 15:36 IST.
This is a baseline for normal post-release observation, not a test trade.

Final local suite: **2,892 passed, zero failures/errors/skips**, Python 3.12,
218.25 seconds. JUnit totals and the no-skip condition were checked. Repair
records validate all 170 findings, both incidents and the 11-step sequence.

## Shipped evidence — 11 November 2026

| Stage | Verified evidence |
|---|---|
| Reviewed PR | [PR #197](https://github.com/damandamanaulakh-tech/MARS/pull/197); head `e3a269dd268a363c57e82e0a28f2deb9c1701326`; all 19 published files byte-matched the tested local files |
| PR CI | [Run 43049842383](https://github.com/damandamanaulakh-tech/MARS/actions/runs/43049842383): **2,892 passed**, zero skipped, 3 warnings, Python 3.11, 255.45 seconds; repair-records and the no-skip guard passed |
| Merge | `499fcd550a881fcfca1a6e33d4db646811f83140`; merged 15:41:09 UTC / 21:11:09 IST; merged tree `570c9f5a701be265750e5dd4ff05ae02997256b6` matches the reviewed publication |
| Deployment | Render `dep-dbq8p5t9el5s73dvhbu0`, automatic new-commit deployment of that exact merge; **live** at 15:42:55 UTC / 21:12:55 IST |
| Health | At 15:43 UTC / 21:13 IST, `/healthz`: `ok=true`, production readiness `ready=true`, DB connected, degraded/boot-degraded false, no write error. No error-level Render logs returned for the deployment window through 15:43:11 UTC |
| Read-only portfolio observation | At 15:43 UTC, 49 open positions and 193 closed trades, unchanged from baseline; zero persisted retained marks and no mark-snapshot timestamp. Last save still 10:06:25 UTC / 15:36:25 IST, before this release |
| Runtime boundary | No normal post-release mark save or production quote-gap recovery has yet been observed. No production scan, trade or quote outage was induced. Deployment health is not evidence that this scenario occurred |

Current state: **DEPLOYED_HEALTH_VERIFIED_RUNTIME_PENDING**. The engineering
repair is shipped; retain the normal quote-gap observation as an open runtime
check. SEQ-02 is next in the queue and has not begun implementation. Provider
quote-age validation and other consumers remain in SEQ-03; this batch does not
claim those separate source issues are resolved.
