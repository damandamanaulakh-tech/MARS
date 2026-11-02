> Historical audit snapshot, 30 October 2026. Findings and line numbers describe that baseline. See [current repair status](../README.md) for subsequent fixes.

# Validation record — MARS review, 30 October 2026

Main source: `bdc82ec7039ccad151f45674ae253c6db6672751`.
Draft source: `d79f6a2d9c8f9a92f8f5b57a70f36f14b5283262`.

| Check | Result | Scope / interpretation |
|---|---|---|
| Full main tests, host IST | 2,581 passed; 2 failed; 3 skipped; 201.41s | Failures mixed local Oct30 with UTC Oct29 near midnight |
| Full main tests, TZ=UTC | **2,583 passed; 3 skipped; 212.81s** | Complete trading/tests + web/tests suite; 2,788 warnings |
| Skipped workbook checks | **Resolved by installing openpyxl3.1.5 in temporary environment; valuation-scenarios module 29/29 passed, 0 skipped, 0.33s** | Repository requirements and CI installation omit openpyxl; importorskip otherwise omits three workbook-input/cache checks |
| Draft entry-fix regression modules | **98 passed; 4.35s** | Six new test_entryfix modules; not a full local draft-suite run |
| Python parsing | 403/403 files AST-parsed | Syntax/structure check, not complete runtime coverage |
| Tracked-file integrity | All 929 SHA-256 values unchanged after the full suite and review | No tracked source/data changes |
| CSV/gzip outside archives | 170 files, 4,076,215 data rows parsed | Row/header profiling, not blanket financial-data validation |
| ZIP CRC integrity | 8 ZIP-format containers, 3,425 members passed | Includes4ZIP,3XLSX,1NPZ |
| Archive CSVs | 3,385 CSV members, 3,719,150 data rows parsed | Per-stock OHLC invariants additionally checked |
| GitHub Actions | Latest observed main and draft test checks successful | Remote CI evidence on configured Python3.11; no deployment proof |
| Public health | HTTP200; self-reported ready/connected/no write error | No deployment SHA; no authenticated account inspection |

**Runtime.** Python3.12.14; repository direct requirements installed exactly; pytest9.2.0. The final environment additionally contains openpyxl3.1.5 for the skipped workbook comparisons. The dependency snapshot is in `review-environment-freeze.txt`. This differs from CI/Render Python3.11, so local success does not replace environment-specific deployment testing.

**Reproduction commands.** In the temporary review checkout, the full command was:

```text
TZ=UTC SB_DISABLE_CATCHUP=1 SB_SCHEDULER_NO_START=1 \
  SB_SKIP_UPSTOX_PREFLIGHT=1 SB_CHAIN_PACE_S=0 \
  python -m pytest \
  trading/tests/ web/tests/ -q --tb=short --disable-warnings
```

The process environment was rebuilt from a small OS-variable allowlist; production credentials were not passed. The sandbox denied network. Existing test fixtures disable startup scheduler/catch-up, and many external fetches are mocked. Some trading tests still attempted live reads and logged DNS failures; this is a test isolation limitation, not evidence of validated external integration.

The workbook module was run separately after adding openpyxl:

```text
python -m pytest trading/tests/test_valuation_scenarios_2026_10_14.py \
  -q -rs --tb=short --disable-warnings
```

Its29passing cases include the3previously skipped workbook comparisons. Do not describe this as one complete 2,586-test run with the additional dependency; the actual full-run and follow-up results are stated separately above.

**Draft modules checked:** trading test_entryfix_gates, test_entryfix_seams, test_entryfix_signals; web test_entryfix_scheduler, test_entryfix_seams_web, test_entryfix_web, all dated2026_10_22. The draft was materialized as a separate source archive with no changes to main.

**Targeted correctness probes.** `engine-reproductions.py` and `engine-reproductions.txt` capture danger booleans, dead regime, NaN liquidity/Ocean survival, false partial-quote halt, heat sizing, replay/live sizing, correlation, unfinished full-halt liquidation and IFR state loss. The web review records isolated negative-quantity, missing-OAuth-state and DII-field-loss probes. The research review records isolated intake and duplicate-cleaner probes. They used disposable data and mocked external/persistence boundaries, never the hosted portfolio.

**Not verified:** exact deployed commit, private live settings/positions/journals, successful whole-day scheduler execution, recovered backups, real broker execution, live strategy profitability, rendered browser behavior/accessibility, all transitive dependency vulnerabilities, or the contents of the240release assets. No permission to change or deploy the trading system was inferred from the request to review it.
