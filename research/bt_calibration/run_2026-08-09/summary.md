# BT01-BT08 calibration — run 2026-08-10

Panel: 191 tickers x ~12y daily (committed store; design asks 15Y
— 12y is what exists, disclosed). Eval grid: every 5 trading days,
94,479 windows. SAME trigger code as production
(trading.triggers), zero reimplementation.

Event base rates per 10d window: RISE 32.1%, FALL 19.5%, REVERSAL_UP 3.8%, COLLAPSE 3.4%

## Verdicts (BT08)
- VERIFIED (lift>=1.5, fires>=30): 5
- SUPPORTED (lift>=1.1):           9
- REJECTED (lift<1.1):             64
- NOT_ENOUGH_EVIDENCE (<30 fires): 1

## BT07 — GATE_RISE_ENTRY_01 paper replay (live -5/+12/20d bracket)
- entries: 1 · win rate: 0.0% · expectancy/trade: -5.00%

## Sequencing (the "which fires first" answer)
Median lead days to the trigger's own event, by family (higher = earlier):
- Failed Breakdown / Reclaim: 9d
- Collapse / Risk Tripwire: 6d
- Distribution / Pre-Fall: 5d
- Liquidity / Volume Transition: 4d
- Compression / Base Before Move: 4d
- Stealth Accumulation / Absorption: 4d
