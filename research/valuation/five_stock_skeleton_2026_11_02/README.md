# Five-stock valuation skeleton — 2 November 2026

Status: RESEARCH PROTOTYPE RUN; not calibrated, not a production valuation upgrade.
User request: make a skeleton with rough figures, run on five stocks, share results.

Subsequent user approval: "ok, get it wire". PR #191 wires this arithmetic to
the owner-only `/den/valuation/scenarios` screen, linked from Den section 12.
The dated fixtures below remain the reproduction baseline; the screen reads
the latest available saved valuation records. Scenario values remain exploratory
and do not replace automated valuation targets or exit rules.

## Evidence and scope

Saved MARS valuation records were read from settings.valuation_records. Build:
2026-11-02T11:45:56.722999 UTC; all five records' price as-of: 2026-10-30.
These are existing model estimates, not independently audited intrinsic values.
Each saved market beta uses 252 observations. The stock sample is purposive:
IT, banking, pharma, auto and telecom; it is not a performance backtest.
No current quote or today's actual FII/DII flow is claimed.
No credentials, holdings, account quantities, cash or personal P/L are included.

The runner imports only Python standard-library modules, reads this folder's
inputs.json and writes results.json/results.csv. No live engine, exit rule,
configuration, order, database or network path is called.

## Calculation skeleton

All percentages below are entered as percentages (divide by 100 in arithmetic).

    V_market = saved_base * (1 + saved_beta * scenario_market / 100)
    relative_sector = scenario_sector - scenario_market
    V_adjusted = V_market * (1 + assumed_sector_coefficient * relative_sector / 100)
    net_institutional_flow = assumed_FPI_5d + assumed_DII_5d
    extra_margin_pp = min(5, max(0, -net_institutional_flow / 10000 * 5))
    total_margin_pct = 10 + extra_margin_pp
    illustrative_entry_ceiling = V_adjusted * (1 - total_margin_pct / 100)

Sector coefficient = 1 is ASSUMED, not fitted. Saved beta is univariate:
this is an approximate sensitivity illustration, not a jointly fitted causal
model or an exact replay of the production display. All market/sector moves
cover the same hypothetical window. For Bharti, the saved sector is Nifty 50:
relative_sector=0; no independent telecom input is invented.

Foreign/domestic flows change the safety margin only. The Rs 10,000 crore scale,
5 percentage-point cap and 10% base margin are uncalibrated prototype choices.
Positive institutional buying does not reduce the margin below 10%.
This net-flow arithmetic is not proof that DII buying offsets FPI price impact.
No sector FPI allocation or company foreign ownership data was supplied.

There is no incremental earnings, FX, interest-rate or peer-multiple shock in
this first version. Those mechanisms need method-specific calculation and
overlap checks. For example, a 5% EBITDA change does not necessarily imply a
5% equity-value change after net debt. Sector multiple compression must not be
added again if it is already represented in the sector price shock.

## Assumed scenarios

| Input | Falling market | Unchanged | Rising market |
|---|---:|---:|---:|
| Broad market | -5% | 0% | +5% |
| IT sector | -8% | 0% | +8% |
| Financial sector | -6% | 0% | +6% |
| Pharma sector | -2% | 0% | +3% |
| Auto sector | -7% | 0% | +7% |
| Bharti sector | Market fallback | Market fallback | Market fallback |
| FPI, five sessions (Rs crore) | -12,000 | 0 | +8,000 |
| DII, five sessions (Rs crore) | +8,000 | 0 | +4,000 |
| Net flow (Rs crore) | -4,000 | 0 | +12,000 |
| Total illustrative entry margin | 12% | 10% | 10% |

These are hypothetical shocks from the saved baseline, not observed returns,
predictions, or an assertion about current foreign-fund activity.

## Five-stock results

Amounts are rupees per share. Entry ceiling is an arithmetic scenario output,
not a buy instruction. The saved reference quote is dated 30 October.

| Stock | Dated reference price | Saved base | Beta | Falling target | Falling entry ceiling | Unchanged target | Rising target | Rising entry ceiling |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| COFORGE.NS | 2,045.00 | 1,585.00 | 1.066 | 1,455.53 | 1,280.86 | 1,585.00 | 1,719.54 | 1,547.59 |
| ICICIBANK.NS | 1,398.00 | 1,143.69 | 0.925 | 1,079.88 | 950.29 | 1,143.69 | 1,208.56 | 1,087.70 |
| DRREDDY.NS | 1,352.00 | 2,105.16 | 0.396 | 2,125.37 | 1,870.32 | 2,105.16 | 2,103.92 | 1,893.53 |
| TVSMOTOR.NS | 4,195.20 | 2,514.90 | 1.105 | 2,328.45 | 2,049.03 | 2,514.90 | 2,706.91 | 2,436.22 |
| BHARTIARTL.NS | 1,973.70 | 866.00 | 0.657 | 837.56 | 737.05 | 866.00 | 894.44 | 805.00 |

## Attribution of the falling-market result

| Stock | Starting base | Market change (Rs) | Sector-relative change (Rs) | Adjusted target | Target change | Margin deduction (Rs) | Entry ceiling |
|---|---:|---:|---:|---:|---:|---:|---:|
| COFORGE.NS | 1,585.00 | -84.46 | -45.02 | 1,455.53 | -8.17% | 174.66 | 1,280.86 |
| ICICIBANK.NS | 1,143.69 | -52.90 | -10.91 | 1,079.88 | -5.58% | 129.59 | 950.29 |
| DRREDDY.NS | 2,105.16 | -41.70 | 61.90 | 2,125.37 | 0.96% | 255.04 | 1,870.32 |
| TVSMOTOR.NS | 2,514.90 | -138.93 | -47.52 | 2,328.45 | -7.41% | 279.41 | 2,049.03 |
| BHARTIARTL.NS | 866.00 | -28.44 | 0.00 | 837.56 | -3.28% | 100.51 | 737.05 |

## What the run exposed

1. COFORGE and TVSMOTOR are the most sensitive to this particular assumed
   market/sector path. That is conditional on the chosen shocks and coefficients.
2. DRREDDY rises 0.96% in the falling case: its market leg removes Rs 41.70,
   but the assumed sector-relative leg adds Rs 61.90. It also marginally falls
   in the rising case. This is a MODEL-CALIBRATION FLAG, not evidence of an
   investment opportunity. Low univariate beta plus sector coefficient 1 can
   overweight relative sector performance. A joint fit, stability checks and
   unseen-period validation are needed before using this result operationally.
3. BHARTIARTL's separate sector effect is missing because the stored tested-rule
   mapping uses Nifty 50. Its result measures only market sensitivity.
4. DII offset is visible: holding the valuation fixed, FPI -12,000 with DII
   zero produces a 15% margin; DII +8,000 produces 12%; DII +12,000 produces 10%.
   This demonstrates the chosen arithmetic, not empirically estimated impact.
5. The saved bases themselves need checking. Large discrepancies from dated
   market prices must trigger review of statements, share counts, net debt,
   sector method and corporate actions; they must not become automatic BUY labels.
6. Existing low/high valuation endpoints are multiplied by the same factors in
   results.json/CSV. They are conditional bands, not statistical confidence intervals,
   and contain no extra estimated macro uncertainty.

## Reproduction and validation

Run from the repository root:

    python3 research/valuation/five_stock_skeleton_2026_11_02/run.py

Executed successfully: 15 stock/scenario combinations. Built-in checks verify
unchanged-baseline identity; rupee attribution reconciliation; no duplicate
sector leg under market fallback; an independent 100 -> 92.15 -> 81.092 hand
calculation; and that changing only DII changes the margin, not adjusted value.
This is arithmetic verification, not forecast accuracy or strategy validation.

Inputs: [inputs.json](inputs.json). All 15 outputs: [CSV](results.csv) and
[JSON](results.json). Executable calculation: [run.py](run.py).

Source methods:
- Existing MARS market/sector decomposition: trading/valuation/market_legs.py.
- Saved-record construction: web/valuation_exit_feed.py.
- Fundamental value requires cash-flow/growth/risk assumptions:
  [Damodaran introduction](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/background/valintro.htm).
- Future live flow ingestion should retain the published scope and dates:
  [NSE FII/FPI and DII reports](https://www.nseindia.com/reports/fii-dii).

Next decision: review the assumptions and calibrate market/sector coefficients.
Do not connect this prototype to live valuation-based exits or trading gates.

