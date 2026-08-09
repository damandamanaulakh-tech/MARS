# 5 GENERAL-year backtests (non-event) — real NSE data (2026-07-10)

> **NOTE (2026-08-09 audit):** the −8.5pp figure referenced here is superseded by the v1–v4 program — do not quote it.


Companion to BACKTEST_EVENTS_5.md. Same engine/config (momentum mode,
exits ON, 15-name real dataset, equal-weight self-basket benchmark).
Windows chosen to avoid COVID/wars/elections.

| Test | Strategy | Basket | Alpha | Strat DD | Basket DD | Win% | Trades | Sharpe |
|---|---|---|---|---|---|---|---|---|
| GEN 2018 (IL&FS chop) | +1.9% | +2.3% | −0.4 | −10.1% | −16.7% | 38 | 42 | 0.27 |
| GEN 2019 H2 (drift) | −0.2% | +5.2% | −5.4 | −5.1% | −11.5% | 52 | 21 | 0.04 |
| GEN 2021 H2 (bull top) | +3.5% | +6.6% | −3.1 | −7.2% | −12.7% | 37 | 19 | 0.87 |
| GEN 2024 H2 (FII selloff) | −1.1% | −5.2% | **+4.2** | −8.8% | −12.5% | 42 | 19 | −0.15 |
| GEN 2025-26 (recent 12m) | −2.8% | −1.1% | −1.7 | −7.7% | −11.7% | 30 | 27 | −0.39 |

## Parameter hit-map: GENERAL vs EVENT (all 10 tests)
- GENERAL years: churn exits dominate. Trailing 40 fires net −₹3.9L,
  stops 23 fires −₹5.3L, targets +₹4.6L → net exit drag ≈ −₹4.5L
  (≈ −3.6%/yr on ₹25L). Alpha positive in only 1/7 general tests — and
  that one (2024 H2) was a FALLING market.
- EVENT years: same stops/ladder become armour — DD cut to ~half of
  market every time (COVID −15.5% vs −36%).
- MAX_HOLD exits were profit-positive in EVERY test they fired (10/10).
- Pattern: defensive momentum system — earns its keep in falls/shocks,
  pays an insurance premium in calm/up years. Full-cycle net ≈ the
  −8.5pp 7-yr alpha measured earlier.

Caveats: same as events report (16 mega-caps, self-basket benchmark,
2021 data hole, no STT/brokerage).
