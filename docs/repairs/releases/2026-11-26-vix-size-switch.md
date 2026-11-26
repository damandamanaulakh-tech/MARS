# VIX-001 — VIX size multiplier: tested first, switch built, default OFF

Status: **implemented and locally tested on the PR234 branch; NOT merged, NOT
deployed.** The deployment hold in
[the intraday record](2026-11-26-intraday-pipeline.md) applies. With the
switch off (the default) no size and no stored record changes.

## Owner request

26 November formula table: "VIX sizing — size x 1.15 / 1 / 0.8 / 0.65 / 0.5 by
VIX band", then "VIX, few other are not in default, add that too". Approved
defaults: on top of the regime reserve; never below the Rs 1.5 lakh minimum;
not on the owner's own BUY button. Agreed way of working: test first, behind
its own switch, result shown before anything is switched on.

## Test (before the switch was built)

[Plan](../../../research/vix_sizing_study/PLAN.md) committed first
(`51554aa`), then the run (`9248374`):
[result](../../../research/vix_sizing_study/summary.md). The rule has no free
number, so nothing was chosen on either half.

- Population: 11,055 Momentum picks (score at least 62, own 6-month return
  positive) at month-end points, July 2015 to June 2026, from the committed
  observation table; India VIX from the committed index file.
- **Reading rule fixed in the plan: NOT SUPPORTED.** Per rupee deployed the
  multiplier gives +3.423% against +3.431% plain over the whole period
  (+0.145 points to June 2020, -0.071 after). The worst six-month run per
  unit deployed is better in the first half and worse in the second.
- Mean outcome of a buy by band: under 12 +5.74%; 12-16 +2.46%; 16-20
  +2.71%; 20-25 +4.22%; 25 and above **+7.07%** — the band the ladder halves
  was the best one.
- The worst six months of the record (July to December 2024) had VIX at
  12.8 to 15.6: the multiplier was 1.00 throughout.
- Over the whole period the ladder deploys 8.5% less and earns 8.7% less.

Limits: month-end entry points; the stored table's fixed -5% / +20% bracket;
Momentum only; no cash limit, segment share, minimum or cap modelled; a
survivor panel.

## Implemented behavior

- `sizing.vix_size_multiplier` / `VIX_SIZE_BANDS`: the owner's ladder, one
  function used by the test and by the scan. A missing, zero or non-numeric
  VIX is unknown, never assumed calm.
- `sizing.apply_vix_multiplier(wanted, vix, slab, cap)`: scales the size a
  buy wants; never below the minimum ticket (and a size already under it is
  not lifted; zero stays zero); never above the cap; an unknown VIX leaves
  the size alone and says so.
- `cfg.vix_sizing_enabled` (default **False**). When on, the scan applies it
  to an automatic score-sized buy after the measured-Kelly cap, using the VIX
  of the scan's regime reading, with the cap = the lower of 5% of equity and
  the measured-Kelly cap. Every later check (cash, reserve, segment share,
  sector, heat, minimum value) still applies to the result. The BUY journal's
  sizing record carries the VIX, band, multiplier, wanted size and final size.
- Not scaled: Ocean's fixed ticket; the owner's BUY button, order ticket and
  Buy Desk; the backtest.
- Screens: a Settings switch whose hint states the test result; a Den line
  with the ladder, on/off, and the result read from the committed run file.

## Validation

- `trading/tests/test_vix_sizing_2026_11_26.py` (28): every band edge; the
  unknown cases; minimum and cap; default off; in a real scan, off changes
  and records nothing, on scales the position at four VIX levels and the
  journal says by how much, the cap holds at a calm VIX, a missing reading
  buys the unscaled size; a syntax-tree check that the one call site is the
  scan's non-Ocean branch and that no order, risk, exit, backtest or owner
  path names the multiplier; the committed result and plan.
- `web/tests/test_vix_sizing_page_2026_11_26.py` (3): the Den line off and
  on; the result read from the run file; the Settings switch through a save.
- One existing test was repaired, not weakened. On the switch commit
  `ab6fc59` the full suite gave 3,982 passed, 1 failed:
  `test_the_den_states_both_gates_from_the_live_settings` read
  "Momentum 1.2x". Cause, reproduced in order: an earlier test leaves the
  shared settings in Swing, the new VIX Settings test then saved the algo
  form as it was shown and stored Swing's 1.2 as a Momentum per-segment
  override, and the Den test ran next. **Not an app fault** — the rotation's
  own restore and the 5-minute side pass were both checked. Fixed in
  `8f74b78` with a `clean_settings()` context manager shared by the two
  Settings-reading test files; the Den test now also asserts that an owner
  per-segment edit is what that line prints. CI on `ab6fc59` failed on that
  one test and nothing else, and CI on `8f74b78` reported nothing failed.
- Full local suite on commit `8f74b78` from a clean state directory:
  **3,983 passed, 0 failed, 0 errors, 0 skipped** (655 s; junit report read
  back).

## Recommendation and open items

- Recommendation: leave the switch off. The owner decides.
- Not tested: the multiplier in the other four score-sized segments; the
  daily (not month-end) entry flow; interaction with cash limits.
- If the owner switches it on, runtime acceptance is a normal session whose
  BUY records show the VIX reading and multiplier; no trade will be induced.
