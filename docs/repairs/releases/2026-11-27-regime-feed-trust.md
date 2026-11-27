# XR-050 — a dead market-state feed no longer reads as a calm market

**Owner's choice, 2026-11-27, from an options card: keep yesterday's reading
until the feed answers.** (The alternative offered — always take the defensive
side while the feed is down — was declined, because a brief glitch on an
ordinary day would stop buying.) With nothing stored at all, a fresh book
carries on exactly as before, which is the condition that forced the choice.

## What was wrong

`fetch_regime` wraps only the fetch, and its `except` **returns**
`_neutral_fallback(...)` — regime "neutral", everest on, sizing multiplier 1.0
— rather than raising. The C7 sticky brake added on 2026-08-09 lives in
`engine.scan`'s `except`. So on the **common** failure, control took the `else`
and `regime_feed_down_brake` was set to **False**. C7 was the right idea placed
in the one branch the real failure never reaches.

**There is a second route that raises nothing at all.** `data.fetch_daily`
returns its dict whatever happens, so an empty answer reaches `classify()` with
every input None and falls through to its own terminal
`("neutral", True, False, 1.0, 0.0, "NEUTRAL (insufficient data)")`. Either
way the reading is permissive and is **not** None.

Three consequences, measured with a reproduction that fed in FII net
−25,000 Cr so a real classifier would have flagged it:

* `cfg.cash_reserve_pct = dynamic_cash_reserve_pct("neutral")` = **0.0** — the
  book loses the protective cash it is meant to hold on exactly the worst days:
  20% (Rs 1 crore) in a true correction, 40% (Rs 2 crore) in a true panic.
* `everest_ok = regime.everest_active` = **True**, so in a true panic the entry
  block that reading would have applied is lifted.
* **The permissive reading was written to `portfolio.last_regime`** — a field
  persisted both ways. One dead-feed day overwrote a stored "panic" with
  "neutral", so on the *next* dead-feed day C7 read `last_known = "neutral"`
  and could not arm either. **This compounding half is not in the backlog row.**

Narrower than the row implies, and said so rather than inflated: the entry
block it lifts is **panic-only**, because the owner switched the correction
brake off in August. Ocean is unaffected — `_ocean_picks` never reads
`regime.ocean_active`; its four rails gate it.

## The test that let it survive three audits

`trading/tests/test_silent_death_fixes_c_2026_08_09.py` monkeypatched
**`fetch_regime` itself** to raise — a path production never takes. So the
tested branch was the `except`, while the real failure took the `else`. **The
test passed on the broken code.** It now fails the *fetch*, which is what
really happens, and additionally asserts that the stored regime is not
overwritten. Verified: it fails on the pre-repair code, where it used to pass.

## What changed

* `RegimeReading.feed_ok: bool = True` — does this verdict have evidence behind
  it? `_neutral_fallback` sets False; `fetch_regime` sets
  `feed_ok = nifty_close is not None and "insufficient data" not in why`, which
  closes the second route. The default is True, so no other caller or stored
  reading changes behaviour.
* `engine._sticky_regime_brake(how)` — the C7 body extracted so **both** routes
  take the same rule, with `how` ("failed" / "untrusted") in the journal so the
  line says which one happened. A source test asserts there are exactly two
  call sites, because the fault *was* two routes to one state with only one
  guarded.
* `engine.scan` now computes `regime_trusted` and treats an untrusted reading
  exactly as it treats no reading at all: the sticky brake runs, the write to
  `last_regime` is skipped, `everest_ok` falls back to the brake, and the
  correction-brake-off override requires a reading it can trust.
* The cash reserve reads `regime.regime` when trusted, otherwise
  `portfolio.last_regime`, otherwise is left alone.

## Money

**Whole-book, entry side and deployment.** Exits never read the regime, so
nothing already held is touched. Before: a dead-feed day deployed the full six
segment shares with 0% reserve and, in a true panic, no entry block. After: the
last regime actually measured keeps its brake and its reserve until the feed
answers. **Expect fewer buys on a broken-feed day following a correction or
panic** — that is the point.

## Tests

`trading/tests/test_regime_feed_trust_2026_11_27.py` — 12 tests, **11 verified
to fail on the pre-repair code** (the twelfth pins that the flag defaults to
True, which is true either way by design). Both routes are marked
untrustworthy; a real reading is trusted; the engine keeps yesterday's brake
*and* reserve for both stored regimes × both routes, and does not overwrite the
stored regime; the journal line appears once a day and names the route; a fresh
book with nothing stored carries on; a trusted reading still persists a real
switch and still sets a 0% reserve; and one rule decides the brake on both
routes.
