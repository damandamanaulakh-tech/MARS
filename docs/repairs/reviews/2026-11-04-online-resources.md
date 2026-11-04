# Online resources and DATA_NEEDED inventory — 4 November 2026

Baseline reviewed: `6de43c694b0b573cf414a1f839f2a4125aec809d`.

Owner request: connect online resources where information is needed, including
stock/company news, global markets, NSE, BSE and SEBI, and make those resources
visible in the app. This is an input/coverage review and the implementation
inventory for the current batch. It is not a claim that every missing field has
been filled or that every provider is reachable from the deployed host.

## Coverage of this review

Read the actual consumers and scheduler paths in `trading/register_v4.py`,
`trading/external_panels.py`, `trading/news_feed.py`, `trading/filings.py`,
`trading/sebi_feed.py`, `trading/macro_feeds.py`, `trading/options_chain.py`,
`trading/valuation/pipeline.py`, `web/scheduler.py`, `web/state.py`,
`web/routes/register_routes.py`, `web/routes/trigger_routes.py`, the valuation
view modules and their templates. This was not a new full read of every asset
in the repository. No production credentials were inspected and no production
trades or database records were changed by this audit.

The source links below identify configured providers. A provider link is not
proof that its fetch succeeded, that a particular company is covered, or that
its output is used by a calculation. The Sources page distinguishes source
observation/publication dates from refresh attempts and successful refreshes.

## Existing source-to-consumer map

Review IDs in this table identify this inventory only; original repair backlog
IDs are preserved separately.

| ID | Input / source | Actual storage and consumers | Existing refresh / scope | Why data can still be missing | Next action and effect |
|---|---|---|---|---|---|
| SRC-01 | Company news — [Google News](https://news.google.com/) RSS | `news_feed.run` → `portfolio.news_watch.rows` → trigger detail, nine news-fed triggers and Register W-019 | `run_news_feed` before the post-scan trigger pass; default limit 60; held names then Momentum shortlist/other scan names | Not in bounded scope; request failure; no matching headlines; old pass; ticker normalization mismatch | Preserve article link, source and publication time; distinguish UNKNOWN, NO_NEWS and stale. Research only; no independent buy/exit permission. |
| SRC-02 | Company announcements — [BSE](https://www.bseindia.com/) public announcement endpoint | `filings.refresh_filings` → `portfolio.filings` → Scan/Watchlist filings panel | 09:05 IST weekdays; held/watchlist name matching; two-day window; up to eight listing pages; 100 retained matches | HTTP/IP block; empty first page; pagination cap; company-name mismatch; old retained records | Show fetch failure and retained-data date; add issuer identity checks and pagination coverage before claiming exhaustive coverage. Notice links are not parsed financial statements. |
| SRC-03 | Enforcement orders — [SEBI](https://www.sebi.gov.in/) listing | `sebi_feed.fetch_orders/match_orders` → Mongo SEBI orders → `/triggers/{ticker}` | Weekly data block at 16:30 IST, with catch-up logic; up to three listing pages; custom universe + held company names | No parsed listing; partial fetch; title identity mismatch; source older than latest refresh; unlisted person/order outside company scope | Keep listing fetch outcome, scanned count and match scope. “No matching title” must not mean “no enforcement risk.” Display only. |
| SRC-04 | NSE/BSE prices and daily candles — [Upstox](https://upstox.com/developer/api-documentation/) | Quote router, stored OHLCV and history overlays → positions, scans, charts, Register, valuations | 08:30 history top-up; 09:20 and 14:30 scans; configured in-session refresh; weekly history continuation | Broker token for quotes; provider failure; symbol/instrument mapping; newly listed/insufficient history; stale stored bars | Expose quote session status separately from stored-history coverage. New source connectivity must preserve stale-data and price-quality checks. |
| SRC-05 | Option chains — Upstox | `run_option_chain_snapshot` → `portfolio.option_chain` → T12W_0102, Register W-007/W-022, trigger detail | Daily snapshot and login/read-now paths; bounded to 120 names; held/picked/watched scope | Login required; stock not F&O; no usable OI; failed request; missing sessions for the 15-day pattern | Distinguish AUTH_REQUIRED, NOT_APPLICABLE, FETCH_FAILED, STALE and INSUFFICIENT_HISTORY. Keep fresh chain above historical proxies. |
| SRC-06 | Nifty/Sensex/VIX and sector history — Upstox + committed base | `data_refresh` stored index/sector overlays → market panels, Register market inputs, valuation beta/sector legs, trigger panels | Morning/close index refresh plus weekly block; history merged with committed CSVs | Broker/provider gap; no symbol mapping; missing benchmark dates; fewer than 252 common sessions for measured beta | Backfill dated history where provider supports it. Never substitute a latest value for a required historical observation. |
| SRC-07 | Global S&P 500, Nasdaq, Dow — [FRED](https://fred.stlouisfed.org/) | `macro_feeds.FRED_SERIES` → `macro_daily` → valuation market view; current batch adds a visible global-market panel | Macro refresh after 09:20 rotation and after 15:35 close; stored daily observations | Source outage, no first seed, old observation, closed US market/date lag | Show observation date and source lag. Existing valuation decomposition counts overlapping US movement as context, not another unreviewed price multiplier. |
| SRC-08 | USD/INR and USD per EUR — FRED; RBI/FBIL reference | FRED series in `macro_daily` → currency-through-EPS leg; RBI reference stored alongside; older `usdinr_daily.csv` still serves some Register paths | FRED H.10 currency observations have weekly-release lag; RBI home refresh uses separately dated readings | Selection-date history absent; stale series; fixed CSV consumer not overlaid; wrong currency conversion/units | Maintain distinct source/date/units. EUR/INR needs both compatible FX legs. A currency move still requires a documented company exposure. |
| SRC-09 | 10-year government yield — Upstox G-sec candles; [RBI](https://www.rbi.org.in/) instrument list | `macro_feeds.fetch_gsec10y` calculates/stores `gsec10y_yield` → valuation rate leg; RBI benchmark is display-only | Macro job; chosen bond and observation date stored | Candle failure; unavailable bond identity; no historical yield near selection date; stale fallback file | Keep named bond, coupon/maturity and source date. Do not silently switch the owner's yield source or equate a repo rate with a 10-year yield. |
| SRC-10 | Repo rate and RBI benchmark/reference observations — RBI | `rbi_repo`, `rbi_usdinr`, `rbi_gsec10y` → valuation context; verified MPC history supplies historical repo anchor | Macro job; date/source stored | RBI page parsing failure; no verified historical decision covering requested date | Preserve verified history; latest rate cannot fill an unavailable past observation. |
| SRC-11 | NSE sector/index P/E and P/B — [NSE indices](https://www.nseindia.com/market-data/live-market-indices) / `allIndices` | `macro_daily` series `nse_pe:*` / `nse_pb:*` → valuation sector comparison | Intraday refresh plus closing replacement; timestamp marks intraday/closed state | Cloud/API block; P/E not published for sector; sector absent; short stored history | Keep per-sector coverage, data date and intraday/close flag. A fresh maximum date across sectors does not prove every sector is fresh. |
| SRC-12 | FII/DII daily market activity — Upstox; NSDL tide | `run_market_fii_snapshot`, weekly FII refresh and stored overlays → Register FII items / flow context | Daily token-dependent snapshot and weekly tide refresh | Token absent; exchange segment not supported; stale committed rows; cumulative-vs-daily series mismatch | Keep source/segment/basis explicit. Market flows are not a company's quarterly institutional ownership. |
| SRC-13 | Quarterly promoter/FII/DII/MF holdings and pledge — NSE shareholding XBRL intake | `shareholding_institutional.csv` and related intake JSONs → `external_panels.institutional_holdings` → H-01..H-06/X-07 and valuation ownership inputs | Current consumer reads committed reference files; not every company covered | Missing company/quarter; only one quarter; XBRL schema/identity; pledge flag is not pledge percentage | A bounded incremental filing ingestion and revision history can extend coverage. Needs issuer, quarter, filing date, units and provenance; generic RSS cannot fill it. |
| SRC-14 | Annual/quarterly financial statements — committed Yahoo statement ZIP + long-form monthly fields | `register_v4.load_statements` reads ZIP; `load_fundamentals` overlays Mongo rows; valuation pipeline coverage enumerates statement ZIP | Monthly budgeted long-form refresh over custom universe + held names, resumed through weekly block | Full statements not in ZIP; required line item missing; fiscal date/unit/currency ambiguity; partial refresh | Distinguish full-statement coverage from a few updated scalar fields. New online statement ingestion needs reconciliation and provenance before valuation use. |
| SRC-15 | Analyst ratings, revisions, shares/free float, earnings dates — fetched reference CSVs | `_load_ref` → H-07, W-011/W-020, X-06/X-11 and external trigger context | Current lookups read cached committed CSVs, not a network call | Uncovered issuer; old snapshot; analyst estimate not actual; revision drift inconsistent | Add source observation/fetch dates and conservative freshness. Keep invalid estimate drift PARTIAL; do not invent forward history from one current snapshot. |
| SRC-16 | Delivery percentage — committed large daily delivery file | `_load_delivery` supports research, but `PROD_SOURCES` excludes it | Explicitly disabled for production on the 512 MiB deployment | +153 MB measured peak for the 3.5-million-row source; stale file; rejected calibration | Do not reenable the old heavy reader. A future daily, indexed, per-symbol store needs source access and bounded memory proof. |
| SRC-17 | F&O OI, bulk/block deals, insider disclosures — committed panels | `external_panels` → trigger and Register inputs, including H-20/H-25/W-022 | Committed panel reads; historical deals known truncated at 70 rows/month in intake | Frozen panel; incomplete month; absent ticker; old “quiet” not current absence | Add incremental exchange disclosures with coverage metadata. Preserve PARTIAL for truncated history; don't turn absence into a clean bill of health. |
| SRC-18 | Register market breadth and research metrics — stored OHLCV | 16:45 `run_register_precompute` → `portfolio.register_market`; page falls back to a 50-member panel | Precompute requires a sufficiently populated store; accepted when closes within five days | Thin store; slow database; failed/stale precompute; no history for selected symbol | Show panel size, observation date and fallback scope. This is a calculation/store issue, not necessarily a missing website. |
| SRC-19 | Valuation “NOT COMPUTED” | `valuation_chain_view` / `valuation_view` budget, memory, database and concurrency guards | 16:50 precompute; guarded/cached Den reads; boot catch-up | Scan active; shared lock; time/RSS budget; database deadline | Preserve reason and retry/catch-up state. Connecting feeds must not remove the safeguards that protect the trading process. |
| SRC-20 | Valuation filing-only fields | Snapshot/share bridge/earnings/FX geography/route inputs | Filed or owner-supplied structured inputs | ESOP pool, exceptional items, segment earnings, geographic exposure, consolidation scope absent | Read and verify the filing; keep source document, page/line, period and units. A news headline is context, not accounting evidence. |

## Register DATA_NEEDED categories

The fallback dictionaries contain 25 per-stock entries and four market entries.
They are fallback definitions, not a live count of failed metrics; many are
superseded when a valid reading exists. Count each stock/date's actual states.

| Category | Register examples | Appropriate remedy |
|---|---|---|
| Fetch/coverage that can be extended | H-01..H-07, H-20, H-25, W-011, W-019, W-020, X-06, X-07, X-11 | Ingest the relevant source with issuer/date/units, then wire the stored consumer and its freshness handling. |
| Credential or listing dependent | W-007 and W-022; T12W_0102 | Secure Upstox login, an F&O-listed instrument, a usable chain and enough dated snapshots. |
| Filing extraction / accounting interpretation | H-09 R&D; X-05 durability; valuation ESOP/one-offs/geographic split | Obtain the correct filing and produce an auditable structured observation; do not infer an amount from a headline. |
| Undefined or licensed source/model | H-10 ESG; W-021 true short interest; H-19 liquidity; W-026 short-rate leg; W-027 credit spread | Define geography, tenor, units, source rights and formula before adding an adapter. True short interest and futures OI are different measures. |
| Additional historical observations | H-06 quarter-on-quarter changes; price/beta/rank lookbacks; option-pattern history | Backfill compatible observations or wait for sufficient history. Preserve minimum sample requirements. |
| Engineering or research work | H-27 trade attribution; H-28 bootstrap confidence intervals; X-19 exact replication | Implement/test the requested analysis. These are not missing data URLs. |

## Concrete consumer faults identified in this audit

| Finding | Baseline behavior | Required regression proof |
|---|---|---|
| Register company-news symbol mismatch | `_wire_news_item` looks up a bare symbol, while `run_news_feed` stores ticker keys such as `TCS.NS`; fetched news can still appear missing in W-019. | Bare and `.NS` inputs reach the same stored row; missing/failed/stale rows remain explicit. |
| Fresh PCR overwritten by historical proxy | `_wire_intake_items` calls `_wire_chain_items`, then assigns W-022 from `external_panels.pcr_oi` using the committed F&O panel. The later assignment can erase a fresh actual chain result. | Fresh valid chain wins; absent/stale/malformed chain leaves the clearly dated proxy or missing state. |
| Source status must use actual stored schema | News pass writes top-level `ts` and row-level `asof`, not top-level `asof`. A status card using the wrong field would always report missing. | Healthy, partial, failed and stale fixtures read the real keys and distinguish observation age from fetch time. |
| Page rendering must not refresh credentials | `state.quote_source_status()` can call `_heal_stale_broker`; a read-only source page must not use that healing side effect. | The page reads current session metadata without attaching a broker or calling an upstream service. |

These findings belong to a bounded input/consumer batch. Their inclusion here is
not proof of implementation, merge, deployment or live behavior. Record those
states and exact evidence in the batch/release ledger.

## Sources page and disclosure requirements

The current batch adds `/resources` under owner access, a sidebar entry, and
contextual links from Dashboard, Register and the valuation chain. The page
reads saved data only. No page-open scan, bulk backfill or order is triggered.
The valuation coverage copy now distinguishes full statements from monthly
summary fields. Missing statements require a batch fetch and validation; the
page no longer attributes that missing implementation to a pending owner
approval from an older instruction.

Each source record exposes `key`, `name`, official `url`, `kind`, `status`,
`as_of`, `last_attempt`, `last_success`, `error`, `reason` and `remedy`.
Notice records expose their source, headline, source URL and publication time.
Unsafe URL schemes and credential-bearing links are not rendered as links;
external text is HTML-escaped. No API token or connection string belongs in a
status/error field.

Required distinctions:

- A successful fetch is not necessarily a new observation or complete history.
- A valid empty response differs from a fetch/parse failure.
- Retained items keep their publication dates after a failed or empty refresh.
- A database write refusal is visible as not stored; it is not a persisted success.
- A connected broker session is not proof every endpoint, instrument or quote is valid.
- Official notices, company news and numerical market observations are separate.
- `DATA_NEEDED`, `STALE`, `PARTIAL`, `AUTH_REQUIRED`, `NOT_APPLICABLE`,
  `INSUFFICIENT_HISTORY` and computation deferral have different remedies.
- Existing data requirements, trading thresholds, exit behavior and source
  authority are not bypassed to replace missing values with zero.

## Validation and rollout record

Focused UI validation: 38 tests passed, zero skipped, covering the new route,
existing Register page, valuation-chain clarity and valuation-chain page tests.
The seven new page tests cover authentication before
reads, viewer restrictions, independent dates, visible source failure, empty
state honesty, retained notices carrying their failed-feed status, untrusted
text/URL handling, cached-report immutability and no mutation route. Broader
integration/CI/production source probes are recorded by
the parent batch; they are not claimed by this review document.

Source probes must record provider, endpoint, observation/publish date,
fetch timestamp, HTTP/parse outcome and host/environment. A successful laptop
probe does not establish Render connectivity. Keep unavailable/blocked providers
visible and preserve good prior observations. Do not place trades to validate
resource ingestion.

## Implemented catalog in this batch

The Sources page has 18 status entries: four new bulletin feeds; five FRED series; three RBI/Upstox macro readings; NSE sector valuations; broker-session status; company news; existing BSE filings; and independently dated FPI/DII cash observations. It does not claim 18 independent providers. Global dashboard cards use five of those stored series.

Four new bulletin endpoints are NSE `Online_announcements.xml` and `Corporate_action.xml` under its official RSS archive, SEBI `sebirss.xml`, and Federal Reserve `press_monetary.xml`. The unverified generic BSE RSS endpoint was excluded; existing BSE company announcements reach the page instead. All actual deployment outcomes belong in the release ledger.

Further feed defects remain separate follow-up work: discarded intraday quote timestamps; a daily-close fallback that can receive a live Upstox label; and a shared index-history watermark that can miss gaps in one of four index legs. They require dedicated freshness and partial-history regressions. Existing thresholds are unchanged by this batch.
