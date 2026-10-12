# MARS — the live recipe, one record

*Owner order 2026-10-11: "Old V1, 1.1, 1.2 etc need to review and either
clubbed or removed if not in use", and the app's name is MARS from 2026-10-12
(Sourceborn MARS; the AM07 name stays only where it names the composite
formula and the repository).*

This page is the **index**. The live numbers themselves are still recorded in
[FINAL_SET_V1_3.md](FINAL_SET_V1_3.md), which `web/tests/test_final_set_v1_3.py`
checks against the code on every test run. When an owner order changes a
pinned number, that page is bumped and the pins move in the same commit, as
before. Nothing about the money path changed on 2026-10-12.

## Version table

| Version | Date | Owner order | Record |
|---|---|---|---|
| V1.0 | 2026-07-21 | the first final set (₹50L, fixed ₹1.25L, −5/+12) | history in CLAUDE.md |
| V1.1 | 2026-10-04 | "make these things as finale set V 1.1" (₹5 Cr, slab m=0.29, five categories, 40/15/15/15/15 shares) | [archive/FINAL_SET_V1_1.md](archive/FINAL_SET_V1_1.md) |
| V1.2 | 2026-10-05 | "Go for A" — momentum stop = max(5%, 2×ATR14), cap 15% | [archive/FINAL_SET_V1_2.md](archive/FINAL_SET_V1_2.md) |
| **V1.3 (live)** | 2026-10-05 | "make it momentum only" — measured-Kelly check reads momentum trades only | [FINAL_SET_V1_3.md](FINAL_SET_V1_3.md) |
| MARS | 2026-10-12 | name, screens and records; formulas unchanged pending the valuation sequence | this page |

## Evidence behind the choices (kept, not deleted)

| Program | Verdict | Where |
|---|---|---|
| Param search v1 (10 mega-caps) | 0/4 OOS — momentum has no edge narrow | [archive/PARAM_SEARCH_V1.md](archive/PARAM_SEARCH_V1.md) |
| Param search v2.1 (1,417-symbol panel) | 4/5 OOS, +9.3pp — the edge lives at breadth | [archive/PARAM_SEARCH_V2.md](archive/PARAM_SEARCH_V2.md) |
| Param search v3 / v3.1 (191 real tickers, PIT) | 3/4 and 2/4 — honest alpha −2..+9/yr | [PARAM_SEARCH_V3.md](PARAM_SEARCH_V3.md) — **its results JSON feeds the Evidence section of MARS Den**, so it stays here |
| Param search v4 (10 correction episodes) | VIX-crush +2.3 wired, dip-buying −2.2 rejected | [archive/PARAM_SEARCH_V4.md](archive/PARAM_SEARCH_V4.md) |
| Algorithm v1 vs v2 (growth lens) | lens is read-only | [ALGO_V1_V2.md](ALGO_V1_V2.md) |

The result files (`docs/param_search_*_results.json`, `*_progress.jsonl`) are
untouched: research scripts read them by path.

## What "MARS" changed on 2026-10-12 (screens and records only)

- App name and ribbon: MARS above, Sourceborn below; "Welcome to Sourceborn — <name>".
- Every on-screen clock prints IST (the dashboard date used to come from UTC).
- The Signal page is gone (zero utility, owner's words).
- Universe Lens, Trigger Layer, Selection Register, Strategy Weights and Engine
  Regime are reached through one left-bar entry, **MARS Den**, which also
  carries the algo controls that used to sit on Settings, the evidence
  panels, the piano and a sandbox that never touches the live formulas.
- Dashboard rebuilt around a fund summary (capital, allocated, reserved, used,
  P&L vs capital and vs used, per category).
- Closed trades and journal lines dated before 2026-09-15 are **archived**
  (Mongo `trades` / `journal_archive` with `pre_mars: true`), not deleted —
  standing rule 3 keeps the journal append-only; they leave the live screens,
  the CSVs and the measured-Kelly sample.
- A viewer login (VIEWER_PASSWORD) sees the dashboard, positions and trades only.
- The weekly universe rebuild keeps a per-name ledger (in/out, date, turnover, rank).
