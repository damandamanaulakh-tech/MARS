# SEQ-05 — dated product costs and preserved historical ledger

Status: **LOCAL_TESTED — 3,128 tests passed with zero skips; CI/shipment pending**.
Baseline: `6a659080c97c25a180f5ad62e3a2b1577febc14e` (main after PR204).
Finding: **XR-059**, original wording and all original backlog IDs retained.
The owner explicitly selected **Upstox Basic, published standard tariff**.
No production trade or scan is induced for this repair.

## Problem and resulting behavior

The baseline charged every category the delivery formula, collected both buy
and sell fees on each partial exit, assumed zero brokerage and applied a full
DP amount repeatedly. Entry cash omitted fees. Five isolated tests reproduced
these defects; [failure output](../evidence/2026-11-12-cost-baseline-failures.txt).
For a 1,000-share intraday buy at INR100 and sell at INR101, with no slippage,
the old formula charged INR240.34989. The selected tariff models INR82.97:
INR30.34 at buy and INR52.63 at sell; net is INR917.03 on INR1,000 gross.

| Requirement | Implemented behavior and proof |
|---|---|
| Dated verified charges | Versioned `upstox-basic-equity-2026-11-12-v1`; effective exchange schedules from 1 Oct 2024 and 1 Mar 2026. Broker tariff checked 12 Nov 2026; no invented historical effective date. [Source/rate artifact](../evidence/2026-11-12-cost-sources.json). |
| Correct side/product | Basic per-order cap; delivery versus intraday STT and buy-only stamp; separate exchange, IPFT, SEBI, GST and DP. Held segment controls costs, not whichever scan config is active. |
| Buy accounting | Automatic scan, Ocean/final engine guard, owner market, limit, Buy Desk and pyramids book each buy fee once. Fee-inclusive sizing/refusal prevents unfunded orders; strategy thresholds remain unchanged. |
| Partial/final exits | Saved original order allocations, FIFO across pyramids, residual paise assigned exactly once. One partial sale is one sell order. Split/bonus quantity reconciliation retains original rupee fees, including split-before-pyramid. |
| Same-day delivery | Same IST-day sales use non-delivery tax treatment, allocate the saved intraday buy alternative and refund paid excess. Remaining delivery lots retain their allocations. Original entry evidence remains unchanged. |
| Daily DP | INR20 plus GST once per exact scrip/IST sell day, across partial exits and re-entry, persisted in JSON and Mongo. A different sell day charges again. Canonical ticker identity is required. |
| Legacy transition | Existing remaining holdings receive a labeled entry estimate, with old entry fees deferred only where they were never paid. No prior closed record or cash history is rewritten. Unsupported legacy exits use an explicitly unavailable legacy estimate so protection can proceed. |
| Evidence and comparison | Entry/exit evidence records component amounts, tariff version, exchange schedule, product, date, allocation/refund/deferred amount and cash movement. Owner `/trades/cost-comparison` shows stored versus modeled costs/net, reasons, HTML/CSV/JSON and bounded archive coverage. No apply/write endpoint. |
| Price audit and research | Explicit price correction keeps recorded versioned fees. New backtests use the selected dated model; earlier-than-Oct-2024 periods require an explicit legacy comparison. Each saved run identifies its model; existing runs remain unchanged. Daily backtests remain daily, not evidence of five-minute strategy performance. |

## Primary-source reconciliation

- [Upstox Basic versus Plus](https://upstox.com/help-center/t-264072/): Basic delivery min(INR20, 2.5% of turnover), intraday min(INR20, 0.1%) per executed order. No Plus or expired 2025 API discount applied.
- [NSE FA64232](https://nsearchives.nseindia.com/content/circulars/FA64232.pdf), 27 Sep 2024, effective 1 Oct 2024: cash exchange INR297/crore/side. [NSE FA73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf), 27 Feb 2026, effective 1 Mar 2026: exchange/IPFT split changes from INR297 + INR10 to INR306.99 + INR0.01 per crore. Combined INR307 stays the same; the old IPFT is not added to the new combined rate.
- [NSE statutory levies](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies): cash equity shares, delivery STT 0.1% both sides, non-delivery 0.025% sell; stamp 0.015% delivery buy and 0.003% intraday buy; SEBI INR10/crore. [NSE stamp schedule](https://www.nseindia.com/static/invest/first-time-investor-stamp-duty-charges-taxes) effective 1 Jul 2020.
- [Upstox tariff](https://upstox.com/brokerage-charges/): standard DP INR20 plus GST per scrip/sell day, no intraday DP. [Broker calculator](https://upstox.com/calculator/brokerage-calculator/) explicitly includes SEBI in the GST base; the main tariff additionally names IPFT/DP. The model applies 18% to brokerage, exchange, IPFT, SEBI and DP.

Rounding is half-up to INR0.01 per component, with GST on rounded service
components. Contract-note/day tax rounding may differ. Each app partial exit
is a separate order, not a partial broker fill under one order ID. Broker
conversion, interest, auction and forced-square-off penalties are not invented.
A paper scheduled exit is not proof of a broker forced-square-off charge.

Support is ordinary NSE cash shares. Known ETF/fund identifiers and configured
fund/REIT/InvIT sectors, non-NSE exchanges and unverified pre-Oct-2024 dates are
explicitly unsupported for new entries. Other NSE names carry the explicit
ordinary-share assumption; the app does not yet have a verified instrument tax
master. BSE needs a dated scrip-group schedule; fund taxes must be separately
verified. Historical comparisons retain unsupported rows as unavailable.

## Historical comparison, separate from the original ledger

The [preserved 74-trade audit](../reviews/2026-11-11-intraday-trades.csv) has SHA256
`6d50ab7bfbb6563634805ae64fb0e3f235dae2b5df47388bd3abab402e59107c`.
The new [comparison CSV](../reviews/2026-11-12-intraday-cost-comparison.csv) and
[detailed JSON](../reviews/2026-11-12-intraday-cost-comparison.json) use exactly
those rows, recorded dates and prices, without changing their original data.

| Measure | Original stored ledger | Selected-tariff counterfactual |
|---|---:|---:|
| Confirmed closes | 74 | 74 |
| Costs | INR70,160.03 | INR14,430.89 |
| Net P&L | INR−223,874.74 | INR−168,145.55 |
| Positive net closes | 22 | 26 |

Seven held-overnight intraday rows are explicitly unverified product estimates.
Missing broker order identities are grouped only as an explained inference;
this is not an account reconciliation. Gross P&L remains negative. Lower modeled
fees do not establish a profitable strategy or authorize threshold changes.

## Validation and shipment

Five baseline failures reproduced; **60 new cases**, **93 focused checks** and **3,128 full-suite tests** pass with zero failures, errors or skips on 13 November 2026 (Python 3.12, 194.50s). [Validation evidence](../evidence/2026-11-13-cost-validation.json). The archive query projects only required fee inputs; large gate snapshots are not loaded for this page. The older 2023 walk-forward fixture explicitly selects the preserved legacy cost comparison while retaining all its window assertions. CI, PR, matching deploy and health remain pending; no shipment claimed yet.
The existing no-skip workflow remains enabled. Natural post-release entry,
partial/final exit and DP allocation are separate observations, pending until
normal activity supplies evidence. No historical ledger rewrite is required.

### CI count reconciliation

PR205 initial head `06897c9a00c5a3f55c232d5c9ff4e0c470fd5caf` passed [CI run 43375188760](https://github.com/damandamanaulakh-tech/MARS/actions/runs/43375188760): **3,127 repository tests**, zero skips, 330.49s, with repair-records and the no-skip guard successful. Local 3,128 includes one additional existing syntax-test parameter for an unrelated untracked duplicate script, `check_repair_records 2.py`. The 3,127 canonical tests all ran; no test was skipped or removed. The batch adds **60 new cases** (41 engine/cost, 17 comparison, 2 owner-order). This count-only record correction changes no application code; its CI is checked again before merge.
