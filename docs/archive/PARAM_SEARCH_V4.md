# Search v4 — The Correction Playbook (2026-07-21)

The user's challenge: "an app built for the market cannot answer a
correction with nothing." Fair. This test asked what the app SHOULD do in
corrections — with preregistered, parameter-FIXED rules (no tuning: with
only ten episodes, any grid search would be self-deception; every episode
is out-of-sample by construction).

Method: correction = the engine's own rule (Nifty < 200-DMA, real index
data) → 10 episodes 2015-2026 incl. the CURRENT one (2026-02-27→, 93 days
— the live regime call is correct). Weekly rebalance inside episodes,
costs 0.15%/side, forced liquidation at episode end, liquidity floor
₹1 Cr. Script `scripts/param_search_v4_correction.py`; raw results
`docs/param_search_v4_results.json`.

## Verdict vs holding cash (the incumbent)

| Rule | Avg/episode | Beats cash | Read |
|---|---|---|---|
| NIFTY dip-buying | −2.2% | 4/10 | REJECTED — buying corrections blindly loses; the brake's premise re-proven |
| Half-size momentum | −2.5% | 2/10 | REJECTED — momentum stays OFF in corrections (double-proven now) |
| OCEAN deep reversion | +0.4% | 6/10 | Most consistent, BUT −18%/−41%DD catching knives in the 2020 crash — unsafe without a crash guard |
| **VIX-CRUSH re-entry** | **+2.3%** | 3/10 | **ADOPTED** — cash through mild corrections (signal simply never fires), then harvests V-recoveries: **+24.0% COVID**, +7.3% 2022, **+2.0% in the current episode**; one loss (2025, −6.3%) |

## What was wired into the engine (same day)

`cfg.correction_vix_reentry = True`: in a correction, momentum entries
re-enable only once **India VIX ≤ 75% of its recent (120d) spike peak** —
`regime.vix_crush_ok`, fed by live ^INDIAVIX via Upstox. Journaled as
CORRECTION_VIX_REENTRY whenever it fires. In mild corrections the signal
stays silent and cash remains the stance — exactly what the evidence
ordered. Momentum's hard brake otherwise unchanged; Ocean remains dormant
pending a crash-guarded redesign (its knife-catching failure mode must be
fixed before it earns a live role — the OCEAN+VIX-guard combo is a
data-suggested hypothesis, NOT yet evidence, and is parked as such).

## Honest limits

- Ten episodes is a small sample; VIX_CRUSH's average is driven by the two
  crash-recoveries. Preregistration protects against tuning, not against
  small-N luck. The live 60-day run (currently inside a real correction,
  with the signal at the ready) is the confirmation instrument.
- Survivorship tag rides as ever (191 current members).
