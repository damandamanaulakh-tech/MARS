# Previous scan audit — 3 November 2026

Read-only production queries at approximately 11:07–11:13 IST. Portfolio snapshot last_save: 2026-11-03T05:37:49.677494 UTC (11:07:49 IST).
Live deployment checked: ddaca63cf393b7454a0f32d46e136a5d79cdcb38; Render deployment dep-dbkb2rmq1p3s73bmegqg.
No scan, order, settings change or deployment was triggered by this audit.

## Latest saved scan

Intraday, stored scan timestamp 10:06:16 IST (2026-11-03T04:36:16.771037 UTC).
This is the engine's saved scan/decision timestamp, not an independently timed completion event.
1,141 ranked; 52 score >= 72; 31 timing=false; 21 selected; 10 opened; 11 refused.
Render logs record POST /scan/run at 10:00:55 IST, consistent with a manually requested scan.

Ten automatic additions: LAURUSLABS, PPLPHARMA, ATHERENERG, AGARWALEYE, SAREGAMA, NAVINFLUOR, IIFLCAPS, CHOICEIN, TATACONSUM, JISLJALEQS.
One additional owner BUY: ALIVUS at 11:02:50 IST, stored mode nifty200_momentum and tag owner_pick. This is not an automatic Momentum addition.
Today's BUY events: Intraday 10; owner 1; automatic Momentum/Short/Swing/Positional/Ocean 0 recorded.
No matching today's BUY events in journal_archive. In-document journal has 1,811 entries extending back to 15 September.
Current snapshot open-position buckets: Intraday 10, Swing 7, Owner 27, automatic Momentum/Short/Positional/Ocean 0; total 44.
The 27 owner positions have Momentum mode, but owner tags put them outside the automatic Momentum budget.

## Recorded refusals from latest scan

| Symbol | Score | Recorded gate | Observation |
|---|---:|---|---|
| ALIVUS.NS | 86.5 | candle_wait_no_pattern | Owner bought later at 11:02:50 IST |
| AARTIDRUGS.NS | 86.3 | candle_wait_no_pattern | Still unheld in observed snapshot |
| BOROLTD.NS | 86.2 | liquidity_spread 0.113% > 0.10% | Still unheld in observed snapshot |
| LIQGRWBEES.NS | 83.8 | already_held | Already held |
| SONACOMS.NS | 83.6 | already_held | Already held |
| CIPLA.NS | 81.1 | candle_wait_no_pattern | Still unheld in observed snapshot |
| FDC.NS | 81 | already_held | Already held |
| POONAWALLA.NS | 80.8 | candle_wait_no_pattern | Still unheld in observed snapshot |
| SANOFI.NS | 80.1 | already_held | Already held |
| EASEMYTRIP.NS | 79.9 | liquidity_spread 0.165% > 0.10% | Still unheld in observed snapshot |
| AFFLE.NS | 74.9 | candle_wait_no_pattern | Still unheld in observed snapshot |

Of the 31 timing=false rows, 23 are unheld and eight already held. Timing is an inferred initial selection rejection from the saved false verdict and source selection condition; those rows have blank outcome, and detailed failed subconditions were not saved.
Later gates are not established to have passed after any earlier refusal.
Total above-floor Intraday rows still unheld: 29 = 23 timing failures + 4 candle waits + 2 spread failures.

## Momentum freshness and Buy Desk reconstruction

Saved Momentum timestamp: 2 November 16:11:24 IST. Its completed pass record is 16:18:08 IST. Floor 62.
197 above-floor rows: 185 recorded market_after_close; 12 abs_mom=false with blank outcome.
17 of the 185 also have a separately computed liquidity failure; do not call liquidity the first automatic stop.
Current holdings plus saved floor/absolute-momentum/liquidity filters give 138 unheld candidates, of which only the first 60 fit the Buy Desk display limit.
Default YESBANK blocklist excludes none of these. Current runtime fund metadata, live quote/budget sizing, and the authenticated rendered Buy Desk were not independently fetched; 138 is a saved-field reconstruction, not a funded recommendation count.
The eight identifiable liquid-fund rows are already held and excluded.
All of these market_after_close reasons belong to yesterday's scan; none establishes today's market-hours gate result.

First 60 reconstructed rows:
1. KRISHANA.NS — 91.2
2. SBC.NS — 90
3. ANANDRATHI.NS — 88.7
4. MBAPL.NS — 88.6
5. DIVISLAB.NS — 88.5
6. CUPID.NS — 85.5
7. SAILIFE.NS — 85.4
8. GLAND.NS — 85.3
9. SYRMA.NS — 85.1
10. HFCL.NS — 84.9
11. CPPLUS.NS — 83.8
12. FEDERALBNK.NS — 83.8
13. RATNAVEER.NS — 83.7
14. ACUTAAS.NS — 82.5
15. RUBICON.NS — 82.3
16. SETL.NS — 82.3
17. AETHER.NS — 82.2
18. SHILPAMED.NS — 82.2
19. AEROFLEX.NS — 81.9
20. KMEW.NS — 81.8
21. SANSERA.NS — 81.6
22. MOREPENLAB.NS — 80.8
23. AUROPHARMA.NS — 80.3
24. RAYMOND.NS — 80.2
25. RPTECH.NS — 80.1
26. HONASA.NS — 79.8
27. NAZARA.NS — 79.7
28. NYKAA.NS — 79.6
29. ZYDUSLIFE.NS — 78.8
30. NITINSPIN.NS — 78.5
31. MARKSANS.NS — 78
32. GRWRHITECH.NS — 77.7
33. AJANTPHARM.NS — 77.6
34. INDSWFTLAB.NS — 77.2
35. MOTHERSON.NS — 77
36. AHCL.NS — 76.5
37. IOLCP.NS — 76.5
38. KTKBANK.NS — 76
39. GRANULES.NS — 75.8
40. GANDHAR.NS — 75.2
41. KARURVYSYA.NS — 75
42. MUFIN.NS — 74.8
43. CEMPRO.NS — 74.7
44. FCL.NS — 74.5
45. MCX.NS — 74.4
46. ONEPOINT.NS — 74.4
47. IBULLSLTD.NS — 74.3
48. BHEL.NS — 74.1
49. INDHOTEL.NS — 73.8
50. SATIN.NS — 73.7
51. IPCALAB.NS — 73.6
52. NRBBEARING.NS — 73.6
53. AKUMS.NS — 73.4
54. SAKAR.NS — 73.2
55. BIRLACABLE.NS — 72.8
56. EBGNG.NS — 72.7
57. FILATEX.NS — 72.7
58. ACMESOLAR.NS — 72.4
59. LALPATHLAB.NS — 72.3
60. RPEL.NS — 72.1

## Rotation evidence gap

rotation_report.active/daily still identifies 2 November, phase look_only, status running, finished null, only Momentum completed live=false.
No complete five-stream rotation for 3 November is recorded in the queried snapshot. Do not equate zero recorded purchases with a successfully completed scan that rejected every candidate.
No current per-stock refusal list for Short, Swing or Positional can be established from this snapshot.

## Sources and complete table

Production Mongo sourceborn.portfolio: last_scan, scan_matrix, last_scan_opened, last_scan_skipped, scan_history, decision_journal, positions, momentum_shortlist, rotation_report.
Production sourceborn.journal_archive queried for today's BUY/owner-summary/rotation events.
Render Research Engine read-only logs and deployment metadata.
Interpretation: trading/engine.py:519,1053,1077,1148,1550; web/buy_desk.py:54; trading/category_budget.py:70; web/rotation_progress.py.
[Full 249-row CSV](2026-11-03-previous-scan.csv): all 52 above-floor Intraday rows plus all 197 saved Momentum rows.
This report and CSV preserve read-only observations. Publishing the audit does not change the application or trading policy.
