# RUN-20261126-02 — the live book as it stood before any restart

Owner screenshot of the dashboard, **27 November 2026, 01:07 IST**, with
`/healthz` read from this session at 01:18 IST on the same process. Recorded
because a restart reloads the last **saved** copy (26 Nov 14:23:56 IST), so
these are the figures to compare against afterwards.

## What the owner's screen showed

| Field | Value |
|---|---|
| Open positions | **51** |
| Equity (live marks) | ₹4.99 Cr |
| Capital | ₹5,00,00,000 |
| Allocated (entry cost of open positions) | ₹2,58,98,838 |
| Used now (live marks) | ₹2,61,68,240 |
| Cash | ₹2,37,00,394 |
| Reserved | ₹0 (regime reserve 0%, ladder 0% bull) |
| Unrealised P&L | +₹2,69,402 (+0.54% of capital) |
| Market state | BULL_EXTENSION |
| Upstox | connected, MANPREET KAUR; token expires 03:30 IST |

Cash + used = ₹4,98,68,634, which matches the equity shown. Against capital
that implies roughly **−₹4.0 lakh of booked losses** on the trades closed so
far; the fund summary that states this directly is on the unmerged branch.

## What the same process reported at 01:18 IST

| Reading | Value |
|---|---|
| `connected` | true — the stale flag, not a live check |
| `mongo` (storage statistics) | **null** |
| `history_tickers` | **0** |
| statistics refresh | **12.141 s** = four reads × the 3 s server-selection timeout |
| `write_error_at` | 2026-11-26T13:04:53 UTC — frozen for 6½ hours |
| `portfolio_save_confirmed_at` | 2026-11-26T08:53:56 UTC (14:23:56 IST) |
| `entry_block_reason` | `portfolio_save_failed` — new buys paused |
| Render memory | rss 503 MiB, **peak 749 MiB** |

## Two readings that matter and point in different directions

1. **Render's side of the owner's upgrade is live.** A peak of 749 MiB on a
   running process is above the old 512 MiB ceiling that used to kill this app,
   so the hosting plan did change.
2. **The error text still names a 512 MB limit, which is the Atlas FREE tier
   (M0) storage cap.** The owner states Mongo is paid. Both cannot describe the
   same cluster at the same moment, so either the paid tier was applied to a
   different cluster than the one `MONGO_URL` points at, or it has not taken
   effect on this one. This is checkable in the Atlas console in one look: the
   tier badge on the cluster that connection string names.

The banner's quota wording is the **last recorded error**, not the current
fault. Since 13:04:53 UTC every attempt has ended in a server-selection
timeout instead, which is why the storage statistics are null and the stored
stock count reads 0 — the process cannot reach the cluster at all. That is the
fault OPS-09 repairs on the unmerged branch (`db.note_link_failure` drops the
client so the next call rebuilds it); on the deployed build nothing clears
`_CONNECTED`, so only a restart gives it a fresh client.

## Order of operations, and why it matters

A restart taken before the storage problem is settled reconnects and then hits
the same refusal, losing everything since 14:23:56 IST for nothing.

1. Reports → download **Positions** and **Trades** while this process lives.
   (Trades leaves out the archived older rows; the Journal download reads the
   archive first and is expected to fail; the Positions file carries no stop,
   target or segment.)
2. In Atlas, on the cluster `MONGO_URL` names: confirm the tier, and confirm
   `sourceborn.portfolio` still exists. **If that collection is not there, do
   not restart.**
3. Free space or apply the paid tier to that cluster.
4. Restart the Render service — the same build, not a merge.
5. Compare the reloaded book against the two downloaded files and correct the
   difference (an owner SELL is gated to market hours).

Sells made after 14:23:56 IST are undone by the restart. Buys were paused from
the first refused save, but any made in the 14:30 pass before that refusal are
lost too.
