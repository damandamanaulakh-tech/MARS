# Repository work instructions

The user's current instruction (2 November 2026) is to update the repository
and workflow, not Desktop folders.

- Use `docs/repairs/` as the canonical repair backlog, goal order and release
  ledger. Update these files in the same PR as each repair. Do not create or
  refresh Desktop tracking copies unless the user explicitly requests them.
- Read `docs/repairs/WORKFLOW.md` and the relevant finding before starting a
  repair. Preserve original IDs, source evidence and owner decisions. Record
  additional incidents separately; do not silently drop unresolved records.
- Keep implementation, tests, merge, deployment health and observed runtime
  behavior distinct. Record the SHA, PR/CI links and observation time behind
  each claim. Unknown runtime outcomes stay open.
- Run applicable checks and the repository's CI. The CI no-skip guard must
  remain enabled. Resolve missing test prerequisites; never remove or weaken
  an assertion to produce a green result.
- Keep paper-only trading controls and existing thresholds unless the user
  explicitly approves a policy change. Use isolated portfolios for tests;
  do not induce production trades to verify a repair.

Earlier historical notes in `CLAUDE.md` are context, not current deployment
proof. Current user instructions take precedence over older work directions.
