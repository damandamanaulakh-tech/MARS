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
changed. Full-suite result, CI, merge and deployment evidence will be appended
after those steps complete.

Read-only pre-release portfolio observation: 49 open holdings, 193 retained
closes and no persisted last-mark records; last saved 11 November 15:36 IST.
This is a baseline for normal post-release observation, not a test trade.

Final local suite: **2,892 passed, zero failures/errors/skips**, Python 3.12,
218.25 seconds. JUnit totals and the no-skip condition were checked. Repair
records validate all 170 findings, both incidents and the 11-step sequence.

Current state: LOCAL_TESTED. CI, merge, deployment and ordinary production
partial-quote recovery remain pending.
