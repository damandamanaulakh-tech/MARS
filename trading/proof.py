"""Proof-gap ledger (C3) — ported from the Chityy engine's honesty layer.

Turns strategy-proof status from a hidden risk into an explicit, machine-
readable record, surfaced read-only on the scan routes. Truth-passed
2026-07-27 (owner-approved): the original four gaps were written before the
v1-v4 evidence program and had gone stale in the PESSIMISTIC direction —
the app was still telling visitors its data was missing and its edge
untested long after both stopped being true. An honesty layer that
understates the evidence is as wrong as marketing that overstates it.
"""

from __future__ import annotations


def proof_gap_ledger() -> list[dict]:
    return [
        {
            "id": "G001", "type": "DATA_HALT",
            "gap": "Real long-horizon OHLCV history was missing.",
            "impact": "Long-horizon walk-forward proof could not run.",
            "status": "CLOSED_2026_07",
            "next": ("Holdings: 191 tickers x 12y real Upstox OHLCV "
                     "(data/history_upstox_export_2026-07-19.zip), 15y index "
                     "daily (index_daily_15y.csv), 1,417-symbol close panel "
                     "back to 1991, 2,392-stock 7.5y research universe. "
                     "Kept fresh by the Friday weekly top-up job."),
        },
        {
            "id": "G002", "type": "PROOF_DEBT",
            "gap": "Backtests used TODAY's Nifty-200 membership (survivorship bias).",
            "impact": "Reported alpha was optimistic; not point-in-time.",
            "status": "CLOSED_2026_07",
            "next": ("PIT universe wired (trading/universes_pit.py, 5 NSE "
                     "snapshots 2017-2023); results tagged POINT-IN-TIME vs "
                     "SURVIVORSHIP-BIASED; v3.1 PIT-constrained run published "
                     "(honest alpha bounds -2..+9%/yr)."),
        },
        {
            "id": "G003", "type": "EXECUTION_FIREWALL",
            "gap": "No live broker order path exists.",
            "impact": "Paper-only boundary enforced by the PAPER_ONLY lock.",
            "status": "LOCKED",
            "next": "Explicit PAPER_ONLY=0 + manual approval before any live connector.",
        },
        {
            "id": "G004", "type": "EDGE_UNPROVEN",
            "gap": "Live (not backtest) edge not yet demonstrated.",
            "impact": ("Backtest evidence exists — v2.1: 4/5 OOS windows "
                       "positive at breadth; v3: 3/4; v3.1 PIT bounds "
                       "-2..+9%/yr. What remains unproven is LIVE paper "
                       "performance."),
            "status": "IN_PROGRESS",
            "next": ("60-day live paper window running (started 2026-07-21 "
                     "era). Pass = paper return within/above the walk-forward "
                     "band vs Nifty 200, scans verifiably daily. Verdict at "
                     "day 60; no engine changes mid-window without owner ask."),
        },
    ]
