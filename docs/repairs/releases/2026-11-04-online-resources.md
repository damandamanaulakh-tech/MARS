# Online resource connections — 4 November 2026

Baseline main: `6de43c694b0b573cf414a1f839f2a4125aec809d`.
Owner request: wire online resources wherever required information appears,
including news, stocks, global markets, NSE, BSE and SEBI.

## Scope and evidence

The [20-source inventory](../reviews/2026-11-04-online-resources.md) traces
providers, stored data, consumers, refresh schedules and unresolved prerequisites.
This is a bounded connectivity and consumer repair, not a new financial model or
claim that every missing fundamental can be filled from a public website.
The 165 original findings and two incidents remain retained; NET-001 records the
new work. XR-069 receives only its FII/DII completion subclaim. AUD-004 receives the independently stored DII preservation repair.

Read-only production evidence taken during this task:

- `macro_daily` held four series: gsec10y_yield and rbi_gsec10y through
  2 November, rbi_repo and rbi_usdinr through 3 November. No stored FRED or
  NSE P/E series were returned by the grouped query.
- Render logs show FRED SP500, Nasdaq and Dow failing in the 3 November
  close refresh at 10:05:55, 10:06:26 and 10:06:56 UTC. Three roughly
  30-second requests consumed the shared 90-second budget before NSE.
- The latest saved company-news pass was 3 November 09:05:27 UTC,
  60 stocks, with no recorded fetch failures. This proves the saved pass,
  not complete coverage of every stock or article.
- The legacy market_fii_day completion marker was 3 November. The marker
  alone did not prove successful mapping or persistence of both legs.

## Resulting behavior

- Four new official bulletin adapters: NSE announcements, NSE corporate actions, SEBI circulars/releases/orders and US Federal Reserve monetary policy. Each stores source/article URL and publication time, rejects malformed responses and unsafe links, and caps reads at 2 MB / 40 items.
- Owner Sources page at `/resources`, linked from the sidebar, Dashboard,
  Register and valuation, serves cached source status and official notices.
  Observation date, attempt time, successful refresh, error and next action
  are distinct. Unsafe links are omitted and external text is escaped.
- Public notices have a separate bounded job at 09:10, 12:10 and 16:10 IST
  and one startup attempt. This job does not enter the portfolio lock, scan,
  change risk limits or place orders. Failed refreshes retain last-good items;
  refused writes are visibly NOT STORED rather than persisted success.
- Five global dashboard cards consume stored FRED S&P 500, Nasdaq, Dow, USD/INR and USD-per-EUR observations. No remote fetch occurs when opening those cards. Missing/invalid/future data stays missing and older observations are labeled stale.
- Company-news article URLs reach the trigger detail; Register news finds the stored exchange ticker and a fresh options-chain PCR now overrides the dated historical proxy.
- Domestic NSE valuation refresh runs before global requests, so FRED outages
  cannot consume its turn. Per-source attempts are saved for the Sources page. FRED/NSE requests use eight-second timeouts; a 200 response containing HTML or the wrong FRED CSV schema fails explicitly.
- FII and DII complete independently only after nonempty mapped rows are
  acknowledged by storage. Unmapped or partially saved legs retry on the next
  existing trigger pass. A completed leg is not fetched again that day.
  The old combined marker cannot suppress the first verified attempt.
- NSDL FII updates merge only FII fields, preserving DII values and provenance. The stored flow reader returns actual DII values, with unknown/nonfinite DII unavailable. FII eligibility, values and score are unchanged. Independently dated FPI and DII source cards show the latest stored cash net in crore INR. DII-only days do not become fabricated FII observations.
- Source freshness follows the existing valuation thresholds. Company-news
  status uses the real saved schema and distinguishes partial/all-failed passes.
  Mixed NSE coverage remains PARTIAL. Latest macro status reads one observation
  per series, rather than loading years of history into the page.

## Reproduction and validation

Two FII/DII regression cases were run against the baseline function extracted
from the pinned Git revision in an isolated test process. Both failed because
the baseline returned success for an unmapped leg or trusted the old completion
marker. The repaired function passes both plus the missing-payload case.

Focused checks: 72 news/adapter/Register tests; 132 global/macro/dashboard tests; 57 FII/DII storage/reader tests; 38 Resources/Register/valuation-copy tests, all zero skipped. Independent review found an unreadable-cache replacement defect; it was fixed and regression-tested before release. A preliminary broad run had 2,839 passes and one fixture-related failure while that fixture was being relocated; the final frozen-tree run supersedes it. Final frozen-tree local suite: **2,852 passed, zero failures/errors/skips**, Python 3.12, 215.31 seconds. Repair-record validation passes with 166 records (all 165 original IDs retained) and two separate incidents; diff whitespace check passes. Independent review found no remaining blocking issues in the source and scheduler scope. Endpoint-probe, CI, merge and deployment evidence will be recorded here when observed. No production trade was induced.

## Release state

LOCAL_TESTED. Implementation and full local validation are complete. No merge, deployment or runtime ingestion is claimed by this local record. Public endpoint reachability from a development
machine must not be represented as reachability from Render.

## Remaining boundaries

Public headlines/notices are context, not extracted accounting facts. Company
geography, R&D, ESOP pools, exceptional items and other filing-specific inputs
still require verified documents. Licensed ESG/true short-interest datasets and
undefined research formulas remain explicit gaps. Broker authentication and
minimum historical sample requirements remain in force. Existing SEBI title
matching and BSE pagination do not establish exhaustive regulatory coverage.
The broader compound XR-069 jobs, risk,
valuation weights and candle/exit policies remain outside this repair.

## Official source references and access limits

- [NSE RSS programme](https://www.nseindia.com/static/rss-feed).
- [SEBI RSS feed specification](https://www.sebi.gov.in/rss.html).
- [Federal Reserve feeds](https://www.federalreserve.gov/feeds/feeds.htm).
- [FRED S&P 500](https://fred.stlouisfed.org/series/SP500) and [USD/INR](https://fred.stlouisfed.org/series/DEXINUS).
- Existing BSE company filings are connected to the Sources page with their retained dates and last journaled refresh result. The unverified BSE generic-notices RSS candidate was excluded.
- Current [Upstox intraday documentation](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/) specifies bearer authentication. Historical tokenless behaviour is not a guarantee; the existing secure login remains available.
- Development CSV network probes were blocked by sandbox DNS; escalated probes were aborted while awaiting permission. No successful direct CSV response is claimed. Ordinary deployed jobs and read-only stored/log evidence determine runtime reachability.
