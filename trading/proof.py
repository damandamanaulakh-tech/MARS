"""Proof-gap ledger (C3) — ported from the Chityy engine's honesty layer.

Turns "this strategy is unproven" from a hidden risk into an explicit, machine-
readable status. Surfaced read-only at GET /proof. No claim of edge is made
until these gaps close.
"""

from __future__ import annotations


def proof_gap_ledger() -> list[dict]:
    return [
        {
            "id": "G001", "type": "DATA_HALT",
            "gap": "Real ~15-year OHLCV history is missing.",
            "impact": "Long-horizon reverse-pattern / walk-forward proof cannot run.",
            "status": "WAITING_FOR_DATA",
            "next": "Upload 15Y Upstox/NSE OHLCV (Google-Drive export was not readable).",
        },
        {
            "id": "G002", "type": "PROOF_DEBT",
            "gap": "Backtest uses TODAY's Nifty-200 membership (survivorship bias).",
            "impact": "Reported alpha is optimistic; not point-in-time.",
            "status": "OPEN",
            "next": "Point-in-time index constituents per rebalance.",
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
            "gap": "Momentum + quality blend not validated out-of-sample.",
            "impact": "No confirmed positive edge yet.",
            "status": "OPEN",
            "next": "Run walk_forward() per-year report once G001 data lands.",
        },
    ]
