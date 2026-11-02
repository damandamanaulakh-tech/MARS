# research/valuation

## Five-stock sensitivity skeleton

[2 November 2026 prototype](five_stock_skeleton_2026_11_02/README.md): five saved MARS baselines, three hypothetical market/sector/FPI-DII scenarios, all inputs, executable runner and 15 outputs. Research only; coefficients and flow margins are uncalibrated. No live valuation or exit rule changed.

`Tata_Technologies_IPO_valuation_model_2026-10-09.xlsx` is the owner's own example model of 2026-10-09 (data as of 9 Oct 2026 close): Tata Technologies IPO-to-date actuals and the FY27E P/E-driver scenario, committed unchanged.
It is the reproduction fixture for `trading/tests/test_valuation_scenarios_2026_10_14.py`: `trading/valuation/scenarios.fy_scenario` must match its cached FY27E cells.
Section H (rows 120-131, the P/E bridge) is the owner's additive what-if; the 29 sensitivities in rows 140-168 are all typed "Assumption" in the sheet.
Display/research only — nothing in the trading engine reads this file or the valuation package.
