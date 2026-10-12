# Valuation context stage — market · sector · rates · sentiment on a standalone target

*Owner order 2026-10-12: "when we are evaluating the price with connection
of the market as well as the stock sector, let's suppose if IT sector
globally is down by 2%, it means that the entire IT company's evaluation is
dropped by 2%, that also must be considered on a current stock price as
well as inflation rate at that time because of dip in sensation or some
other things also needs to be added to verify and to provide a target for
any kind of stock" → picks: **both** sector columns, **Indian sector
indices only**, inflation **through the discount rate** → "go".*

**Money impact: none.** This is a target-price layer. No gate, stop, size
or exit reads it (`trading/tests/test_valuation_context_2026_10_12.py`
pins that engine/risk/orders/danger never import it).

## Where it sits

Stage **17b** of the selection sequence on the MARS Den (§1): after the
justified valuation range (17), before the financial-selection assessment
(18). The standalone target — normalised earnings × justified P/E, dated —
is **typed on the Den (§11)** until stages 09–17 produce it. Each stored
target is read against today's context every time the page opens.

## The four legs

| Leg | Rule | Inputs on the box | When it reads DATA_NEEDED |
|---|---|---|---|
| **Sector** (both columns, side by side) | `1:1 = target × (1 + sector move since the valuation date)` — the owner's rule. `measured = target × (1 + β × sector move)` — β = OLS slope of the stock's daily returns on its sector index over the last 252 sessions (at least 120 pairs). | 16 real NSE indices in Mongo `sector_index`, refreshed 09:20 / 15:35 / Friday, plus a one-time backfill to ~2 years (`scheduler.backfill_sector_history`, also run on a Den save). Mapping `valuation_context.SECTOR_INDEX_FOR`: Auto, Financial→Fin Service, IT, Pharma, FMCG, Metal, Energy, Utilities→Energy, Media, Construction→Infra; the seven realty names→Realty. Any other sector reads **Nifty 50** and the page says so. | Valuation date earlier than the stored index (the page prints the store's first date); fewer than 120 paired sessions for β (the count is printed). |
| **Rates / inflation** | Through the discount rate, never a flat cut: `P/E ∝ 1/(r − g)`, `r = 10-year yield + equity risk premium`, `multiplier = (r_then − g) / (r_now + inflation shift − g)`. At 10y 7.2 + ERP 5 − g 8 (r−g 4.2 points) one extra point is **−19%**; the page prints the sensitivity from today's numbers with every reading. | `data/india_10y_bond_yield.csv` (ends 2026-06-08). "Today's 10-year yield" is a form field — the file's last row is used, and dated, when blank. ERP and growth are form fields with ASSUMED defaults; "inflation shift" is a what-if in points on today's rate. | No yield at the valuation date; growth at or above the discount rate (the formula has no answer). |
| **Sentiment margin** | A **wider buy discount, never a forecast**: `buy below = context target × (1 − discount)`, `discount = 10% + 2.5 points per gauge ON`. Gauges: VIX above the 90th percentile of its prior 120 sessions (Ocean rail R1's definition), breadth below the band's 40% edge, FII 5-day net ≤ −₹5,000 Cr (the flows module's own "very bearish"), the damage cluster on the panel's own q90. | VIX from the index file + stored tail; breadth from `portfolio.breadth_store` / `breadth_today`; FII from the committed CSV merged with the Mongo tail (45-day guard, like the engine); damage from `portfolio.damage_store`. | Each gauge reads ON / off / DATA_NEEDED on its own; an unknown gauge is counted, never read as fear or calm. |
| **Verify** | `stock move = β × sector move + residual`. A residual beyond ±10% flags the standalone valuation for a **RE-RUN** (the owner's event loop); the stock's news verdict is printed beside it (TRIGGER = news on record; PASS / NO_NEWS = nothing explains it). β missing → the 1:1 split, labelled "assumed 1". | Stock closes from the Mongo store (repo zip fallback), the same series as the sector leg. | A missing move on either side. |

Context target = sector column × rates multiplier; when the sector leg is
DATA_NEEDED the page prints the rates-only figure and says so.

## ASSUMED, not measured

`valuation_context.ASSUMED`: ERP 5%, growth 8%, inflation shift 0, base
discount 10%, fear step 2.5 points, re-run band ±10%, β window 252 / min
120, VIX window 120 / quantile 0.90, FII fear −₹5,000 Cr. The 09-09 and
09-29 studies (`research/breadth_recut_2026-09-09/`,
`docs/PREFALL_FRAMEWORK_2026-09-29.md`) measured every sentiment gauge as a
predictor of the next move and all failed the holdout — that is why the
sentiment leg widens a margin and never moves the target. The owner's 1:1
rule is their order, shown beside the measured β so both can be read; the
leg's edge is not claimed.

## What is NOT here

- **Global sector indices** — no free feed reaches Render (checked
  2026-10-12); the owner chose Indian indices only.
- **A CPI feed** — inflation enters through the yield (and the what-if
  shift); MOSPI / FRED monthly series could be wired later (~2-month lag).
- **A live 10-year yield** — the file ends June 2026; the owner types
  today's rate. No daily series reachable from the box was found
  (2026-09-29 check).
- **The standalone target itself** — Phase 1 (fundamentals currency and
  unit lock) and stages 09–17 still wait on the owner's formula review and
  the companion sheet.

## Code

`trading/valuation_context.py` (pure: `beta`, `sector_leg`, `rates_leg`,
`sentiment_margin`, `decompose`, `context`), `web/valuation_view.py`
(inputs from stored state, the owner's targets in settings doc
`valuation_targets` with an in-process copy), `web/templates/_den_valuation.html`
(Den §11), `web/routes/den_routes.py` (`POST /den/valuation`,
`/den/valuation/clear`, stage 17b), `trading/data_refresh.fetch_sector_history`
+ `web/scheduler.backfill_sector_history` (the one-time depth). Tests:
`trading/tests/test_valuation_context_2026_10_12.py`,
`web/tests/test_valuation_den_2026_10_12.py`.
