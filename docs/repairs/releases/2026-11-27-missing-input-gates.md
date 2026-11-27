# XR-027 and XR-041 — a missing input is no longer read as a benign one

Two repairs in one change because they are one fault shape, named by the
verification pass: *a MISSING input read as a BENIGN reading.* `NaN < 5 crore`
is False; an empty reference panel is "no throttle". This repo already states
the opposite convention twice — `universe_select.median_turnover` ("never 0.0,
which would read as known-to-be-illiquid") and `timing.passes_volume_spike`
("unknown, never a pass"). These are the two places it was not applied.

---

## XR-027 — a stock whose recent volume is missing passed the liquidity gate

**Owner's choice, 2026-11-27, from an options card: all 5 of the last 5 days
must carry a real volume.** (3 of 5 was offered, matching the backlog row's own
proposal, and declined.)

`rupee_vol = (close.iloc[-5:] * volume.iloc[-5:]).dropna().mean()` over an
all-blank window is NaN; `NaN < 5.0` is False, so the refusal was skipped and
control fell through to `return True, ""`. The Rs 5 crore gate — the one thing
standing between the book and a stock nobody trades — passed it.

**The partly-blank case is worse, and it is the one that reaches the automatic
09:20 / 14:30 scan.** pandas `.mean()` skips NaN, so blanks *raise* the average.
Measured on one name: true 5-day turnover Rs 4.08 crore, correctly refused; with
four of those days blank it read **Rs 20.00 crore and was accepted**, and the
timing volume ratio was inflated from 3.7 to 14.9 at the same time.

The all-blank case was separately reachable through the owner's own Buy Desk:
`engine._store_momentum_shortlist` runs `passes_liquidity` for every row at or
above the floor with no timing precondition, stores `liq: True`, and
`web/buy_desk.py` accepts exactly that — so one tap could place the whole
Rs 2.5 crore budget off a shortlist whose liquidity column was wrong.

Fixed in `trading/risk_v2.passes_liquidity`: the last-5 close x volume pairs are
built explicitly, only finite positive ones are kept, and fewer than
`LiquidityRules.min_real_volume_days` (5) returns
`False, "volume_missing N/5 real day(s) < 5 required"`. A non-finite average is
refused too. `inf` volume, zero-and-blank mixes and negative volume all refuse —
`inf` was not named in the backlog row and also passed before.

**Exposure in committed data is zero** (0 of 191 panel names carry a blank or
non-finite volume anywhere), so this is a property of the live fetch/store path:
`history_store.records_to_df` turns a stored null into NaN, and
`data._parse_yf_multi` drops a row only on a missing Close, never a missing
Volume. Both producers exist. A control measurement: of 60 committed names made
genuinely illiquid and then blanked for five days, **60 of 60 flipped from
refused to accepted** on the old gate.

**Money:** per-name, entry side. Each wrongly admitted name is one ticket
between the Rs 1.5 lakh slab and the Rs 25 lakh cap, in a stock that cannot be
sold at that size without moving it.

---

## XR-041 — a data outage lifted a crash-damage throttle that was still running

`check_damage_cluster` returned `active=False, sizing_multiplier=1.0` from its
thin-panel branch **before** the window logic ran. So a cluster that fired two
days ago, inside its own documented 22-day window, silently stopped halving new
buys for as long as the reference panel stayed thin.

Three doors led to that return, and the backlog row named only the first:

1. a thin panel — fewer than `MIN_REFERENCE_NAMES` (30) of the 191 frozen
   reference names loaded;
2. **a full panel with too few bars** (`len(market_series) < CLUSTER_WINDOW + 30`)
   — 60 real names at 35 bars read `active=False`, at 60 bars `active=True`;
3. **a crash**: `engine.py`'s own `except Exception: pass` left the snapshot
   `None`, which `_throttled_qty` reads as multiplier 1.0.

Narrower than the row implies, and worth stating so nobody over-scopes it: the
saved date **survives** all three, and the engine writes it back unchanged, so
the window resumes once the panel recovers. The loss is confined to the passes
during the outage — which is exactly when a crowd-fear throttle matters.

The 2026-10-02 F-5 work **created** this branch (it introduced the frozen panel
and `MIN_REFERENCE_NAMES`) and also capped its cost (`engine._throttled_qty`
floors a throttled ticket at the slab). It neither fixed nor worsened the item.

Fixed in `trading/ifr.py`: `_window_active(prev_iso, now)` is now a
module-level helper — one rule, so the thin-panel path and the normal path
cannot disagree about what "still active" means — and the early return computes
`active`, `sizing_multiplier` and `which_fired` from the saved dates with
`fired_today=False`, keeping its own reason text and appending that the window
is still open. `held_throttle_snapshot(now, ...)` does the same with no panel at
all, and `engine.scan`'s `except` branch now uses it instead of leaving `None`.
An unreadable saved date is still not active.

**Money:** sizing only, entry side. An intended Rs 25,00,000 ticket went out at
Rs 25,00,000 instead of Rs 12,50,000; a Rs 6.7 lakh ticket at Rs 6.7 lakh
instead of Rs 3.35 lakh. Tickets already between Rs 1.5 and 3 lakh were
unaffected because the slab floor catches them.

---

## Tests

`trading/tests/test_liquidity_missing_volume_2026_11_27.py` — 11 tests, **8
verified to fail on the old gate**: a genuinely liquid name still passes and a
genuinely thin one is still refused on turnover (so the repair did not just make
the gate stricter everywhere); all-blank, last-five-blank, infinite,
zero-and-blank and negative windows refuse; the measured Rs 4.08-vs-Rs 20.00
case; the requirement is a rule on `LiquidityRules`, not a hardcoded five, and
3-of-5 is reachable if the owner moves; the two liquidity definitions agree in
kind; and `insufficient_history` keeps its own distinct reason.

`trading/tests/test_ifr_window_held_2026_11_27.py` — 14 tests, **9 verified to
fail on the old code**: the frozen panel is loaded at all (without it there is
no branch to test); 0, 1 and 29 names keep an open window; an expired window is
not revived; the boundary is exactly `ACTIVE_DAYS_AFTER_FIRE` on both sides; a
book that never fired is untouched; an unreadable date is not active; the ratio
signal holds its own window independently; the short-history door; the crash
door, including a syntax-tree pin that `engine.scan` calls
`held_throttle_snapshot`; a pin that both paths call the one `_window_active`
helper; and a real fire on a healthy panel still fires.
