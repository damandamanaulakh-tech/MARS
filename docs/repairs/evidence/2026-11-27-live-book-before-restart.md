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

---

## RECOVERED — 27 November, read at 02:17 IST (20:46 UTC)

The fault is over. `/healthz` on the live box:

| Reading | Before (01:18 IST) | Now (02:17 IST) |
|---|---|---|
| Storage statistics | null | **full** (sourceborn 321.7 MB) |
| Stored stocks | 0 | **2,233** |
| Statistics refresh | 12.14 s (four timeouts) | **9.10 s** (real reads) |
| `degraded` | true | **false** |
| `write_error` | the quota text, frozen 13:04:53 | **None** |
| Last confirmed save | 26 Nov 08:53:56 UTC | **26 Nov 20:35:01 UTC** |
| `entry_block_reason` | `portfolio_save_failed` | **None** — buys unpaused |
| Forward-validation pending exports | 35 | **0**, last acknowledged 20:34:59 |

**The cluster is no longer on the free tier.** It now totals **518 MB** —
above the 512 MB free cap — and a write landed at 20:35:01 all the same. On
M0 that write would have been refused, so the paid tier the owner described
is now in effect on the cluster `MONGO_URL` names.

**The process restarted, and the book rolled back with it.** Two readings
prove it: peak memory fell from **749 MiB to 333 MiB** (a peak can only fall
in a new process), and `scan_universe` moved **backwards** — 847 names with
`last_rebuild 2026-11-26` before, 1,205 names with `last_rebuild
2026-09-30` now. A reload of an older stored document is the only thing that
moves that stamp back. So the book now live is the one saved at **26 Nov
14:23:56 IST**, and the 02:05 IST save wrote that copy back.

What that means for the record, exactly as set out above: positions SOLD
after 14:23:56 IST are open again; anything BOUGHT in the 14:30 pass before
the first refused save is gone. The 01:07 IST screenshot in this file — 51
open positions, ₹4.99 Cr equity, ₹2.37 Cr cash, +₹2,69,402 unrealised — is
the only record of the state that was discarded, beside whatever the owner
downloaded from Reports.

**A correction to the inference in this file.** It said only a restart could
recover the link, because the deployed build never rebuilds its client while
`_CONNECTED` is true. That flag does govern our own rebuild, but pymongo's
own client re-discovers servers by itself, so a restart was not the only
possible route; what happened here was a restart regardless. OPS-09 on the
PR234 branch is still the right repair — it drops the client on a link
failure so the heal pass can save the in-memory book instead of losing it.

**The next limit is our own journal.** Of the 321.7 MB this app holds,
`journal_archive` is **236 MB of data plus 11 MB of indexes across 120,696
rows** — 77% of the database, growing about 25 MB a day, 94% of it
scan-decision rows (RUN-20261126-01). The database grew from ~251 MB on
25 November to 321.7 MB now. Retention is not implemented and remains an
owner decision against the standing append-only rule.
