# AM07 — v1 vs v2: the full algorithm

> **SUPERSESSION NOTE (2026-08-09 audit):** this document's figures were
> computed at the 2026-07-30 defaults. Three things moved after it was
> written: (1) the v2 lens now runs **½-Kelly capped at 5%/name** via
> Settings (`kelly_fraction=0.5`, `kelly_max_pct=0.05`) — the ¼-Kelly/25%
> pseudocode and the ₹1,38,825 worked example below reflect the OLD defaults
> and will not reproduce on `/lens` today (the same TATASTEEL inputs now cap
> at ₹2,50,000); (2) live sizing REVERTED to the owner's fixed ₹1.25L on
> 2026-08-04 — Kelly is display/optional; (3) `broker.bid_ask()` WAS built
> (2026-07-31), so section 12's "the spread check has never run" is fixed.
> v1's live liquidity floor is ₹5 Cr 5-day turnover + 200k shares ADV (the
> ₹1 Cr median figure below is the research screen). The maths itself is
> unchanged and correct.

Owner ask 2026-07-30: *"i want the full logics behind agent say / build a proper
algo behind agent's say / how that will work"* — **v1** = what runs your money
today, **v2** = the growth-first ("unbounded") agent from the hypothetical.

Every number in this document was computed by running the committed code
(`trading/lens_v2.py`, `trading/config.py`) against the committed OHLCV store
on 2026-07-30, data to 2026-07-17. Nothing here is illustrative-invented. The
one thing that is an *assumption* is labelled as one, loudly, because the whole
v2 answer hinges on it.

---

## 1. The single difference

Everything else follows from this one line.

| | Objective | In plain words |
|---|---|---|
| **v1** (live engine) | maximise return **subject to** survival constraints | "Make money without ever blowing up." |
| **v2** (growth agent) | maximise **E[log W]** | "Make the pile compound as fast as mathematically possible." |

`E[log W]` — expected log of wealth — is the formally correct objective for
an agent that reinvests everything forever and has no other constraint. It is
the Kelly criterion (Kelly 1956; Thorp). It is also, unmodified, a machine for
producing ruin. Both facts are true at once, and section 9 shows exactly where
they cross.

---

## 2. v1 — the algorithm that runs your money

**Stages.** Each one narrows the candidate set.

| # | Stage | Rule | Narrows by |
|---|---|---|---|
| 1 | Universe | full NSE list, auto-refreshed daily (~2,393 names) | — |
| 2 | Rotating bucket | deterministic ~50/day slice, so the whole market is covered over ~48 days at 512 MB | 2,393 → ~50 |
| 3 | Liquidity floor | ≥ ₹1 Cr median daily turnover | drops illiquid |
| 4 | Blocklist | `ticker_blocklist` never scanned or bought | drops banned |
| 5 | Score | 6m+12m volatility-adjusted momentum as an absolute score (50 + 25 × avg(r6/σ6, r12/σ12), clipped 0-100 — no ranking step; corrected 2026-10-02, audit R-11), **35% quality blend** | scores |
| 6 | Score floor | `score ≥ 62` to be eligible | drops weak |
| 7 | Absolute-momentum gate | own 6-month return must be **positive** | drops "least-bad faller" |
| 8 | Regime brake | correction (Nifty < 200DMA) blocks new buys… | can drop **all** |
| 9 | VIX re-entry | …unless India VIX ≤ 75% of its 120-day spike peak | re-opens in fear |
| 10 | Breadth gate | > 50% of the panel above its own 200DMA (bypassed by 9) | drops in weak tape |
| 11 | Candle confirm | one of 14 structures must fire on the latest bar | delays entry |
| 12 | Risk gates | max positions, sector count + capital cap, portfolio heat, correlation, drawdown governor, daily kill switch | drops |
| 13 | **Size** | **fixed ₹1,25,000** — the same for every name | — |
| 14 | Exit | −5% stop / +12% target / −15% trail / score-decay / 200d max hold, snapshotted at entry | — |

**Pseudocode.**

```python
for day in trading_days:
    if not is_trading_day(day):            continue
    universe = refresh_nse_universe()                    # 08:50
    panel    = rotating_bucket(universe, 50, day.toordinal())
    history  = load_ohlcv(panel + held)                  # Upstox / Mongo store

    regime   = classify(nifty)                           # bull|neutral|correction|panic
    vix_ok   = vix_crush_reentry(india_vix)
    if regime == "correction" and not vix_ok:
        journal("ENTRY_BLOCKED_REGIME");   exits_only(); continue
    if breadth(history) <= 0.50 and not vix_ok:
        journal("BREADTH_BLOCKED");        exits_only(); continue

    ranked = []
    for t in panel:
        if turnover(t) < 1_00_00_000:      continue      # liquidity floor
        if t in cfg.ticker_blocklist:      continue
        mom  = momentum_score(close[t])                  # 6m+12m / vol
        qual = quality_score(close[t], volume[t])
        ranked.append((t, blend(mom, qual, 0.35)))

    for t, score in sorted(ranked, key=lambda r: -r[1]):
        if score < 62:                     continue      # live momentum floor
        if not six_month_return_positive(t):  continue   # absolute gate
        if not candle_confirms(t):         journal("candle_wait"); continue
        ok, why, qty = can_open(risk, cash, equity, positions, t, price[t], cfg)
        if not ok:                         journal(why);  continue
        qty = int(cfg.position_notional() // price[t])    # == 125000 // price
        open_position(t, qty, price[t],
                      stop   = price[t] * 0.95,
                      target = price[t] * 1.12)
```

The critical line is the second-to-last. **v1 never needs to know what a stock's
expected return is** — its size is a constant. That single fact is what makes v1
robust: no estimate can be wrong, because no estimate is used.

---

## 3. v2 — the growth-first algorithm

v2 must know the expected return, because size *is* the bet. That requirement is
its strength and its fatal fragility.

**Stages.**

| # | Stage | Rule | Why a compounder needs it |
|---|---|---|---|
| 1 | Same universe | reuse v1's scan | — |
| 2 | **Edge estimate** | `µ = IC × σ × z` (Grinold) where `z = (score − floor)/30`, floor = the LIVE entry floor (62) | turns a *rank* into an expected return — the step v1 skips |
| 3 | **Volatility** | `σ` = realised annualised, 63-day | Kelly's denominator |
| 4 | **Kelly size** | `f* = µ/σ²`, taken at **¼** and capped 25% | maximises `E[log W]` |
| 5 | **Convexity** | ATR room-to-high; `R = room / stop`; breakeven win rate `1/(1+R)` | log-wealth rewards positive skew |
| 6 | **Capacity** | `Q = ADV · (bps / 10⁴·Y·σ_d)²` | size that liquidity can actually absorb |
| 7 | **Final size** | `min(Kelly, capacity)` | the wall wins over the wish |
| 8 | **Velocity** | momentum half-life → holding period | hold past the half-life and you pay costs for a dead signal |
| 9 | **Trap filter** | 2+ manipulation-risk flags → refuse | its hunting ground is where pumps fish |
| 10 | **Rank** | by `g = f·µ − ½f²σ²` (expected log-growth), **not by score** | that is the objective |

**Pseudocode** (this is `lens_v2.v2_view` / `rank_v2`, in words):

```python
IC          = 0.05          # ASSUMPTION — see section 7
KELLY_FRAC  = 0.25          # fragility discount
Y           = 0.75          # square-root-law coefficient

def v2_candidate(t, df, v1_score, equity):
    close = df["Close"].dropna()
    if len(close) < 60:  return None                  # DATA_NEEDED, never a guess

    sigma = close.pct_change().tail(63).std() * sqrt(252)
    z     = (v1_score - cfg.min_score_to_enter) / 30  # anchored on the LIVE floor
    mu    = IC * sigma * z                            # Grinold: alpha = IC*sigma*z

    f     = min(0.25, KELLY_FRAC * mu / sigma**2) if mu > 0 else 0
    kelly = f * equity

    adv       = median(close * volume, 60)
    sigma_d   = close.pct_change().tail(63).std()
    capacity  = adv * (25 / (1e4 * Y * sigma_d))**2   # 25 bps impact budget
    size      = min(kelly, capacity)

    a     = atr(df, 14)
    hi    = close.tail(252).max()
    R     = (hi - close[-1]) / (close[-1] * 0.05)     # payoff ratio vs the stop
    g     = f*mu - 0.5 * f**2 * sigma**2              # expected log-growth

    if len(manipulation_risk_flags(df)) >= 2: return REFUSE
    return dict(size=size, R=R, breakeven=1/(1+R), g=g,
                capacity_binds=capacity < kelly)

book = sorted(candidates, key=lambda c: -c["g"])[:12]  # concentrate
```

---

## 4. The parameter table

| Parameter | v1 (live, real value) | v2 (growth-first) | Consequence |
|---|---|---|---|
| Objective | return, survival-constrained | `E[log W]` | v2 has a higher ceiling and a floor at zero |
| Position size | **₹1,25,000 fixed** | `¼·µ/σ²·equity`, capped 25% | v2's size swings with its own estimate |
| Names held | up to **80** (~40 funded) | ~**12**, concentrated | v2 gets less diversification benefit |
| Leverage | **none** | the multiplier it wants most | the single fastest route to ruin |
| Score floor | **62** (live momentum preset) | none — low scores just get small size | v2 never refuses on rank alone |
| Stop | **−5%** hard | volatility-scaled (1.5×ATR) | gaps defeat both |
| Target | **+12%** | none — ride to the half-life | v2 turns over faster |
| Liquidity | ₹1 Cr/day floor | explicit impact budget (25 bps) | v2 knows its own capacity |
| Regime | brake in correction | press hardest risk-on | v2 is more exposed at the turn |
| Breadth gate | on, >50% | none | v2 buys into weak tape |
| Cash reserve | 0% base, 20/40% forced in stress | 0% always | v2 has no dry powder |
| Kill switch | **−2% daily halt** | none | v2's bad day has no floor |
| Estimate needed | **none** | µ, σ, IC, impact | v2 is only as good as its estimates |

---

## 5. Worked example — TATASTEEL, real numbers

Run against the committed store, data to 2026-07-17.

| Step | Value | Where it comes from |
|---|---|---|
| Momentum score | 59.6 | `momentum_score` — 6m+12m / vol |
| Quality score | 85.9 | `quality_score` — low-vol + liquidity |
| **v1 score** | **68.8** | 65/35 blend |
| v1 verdict | **PASSES the floor** (68.8 ≥ 62) — then still needs the absolute-momentum gate, a candle confirmation, and the regime/breadth brake to be off | `min_score_to_enter` |
| v1 size | ₹1,25,000 | fixed, regardless of any of the below |
| σ (annual) | 20.7% | realised, 63d |
| z | (68.8 − 62)/30 = 0.227 | floor-relative |
| **µ (est.)** | **+0.48%/yr** | `0.05 × 0.207 × 0.46` |
| ¼-Kelly weight | **2.78%** | `0.25 × 0.0048 / 0.207²` |
| **v2 size** | **₹1,38,825** | 2.78% of ₹50L |
| Capacity @25 bps | ₹34.7 **Cr** | ADV ₹530 Cr — doesn't bind |
| Impact at that size | 0.5 bps | negligible at this scale |
| Payoff R | 3.79 | 9.6 ATR of room ÷ 5% stop |
| **Breakeven win rate** | **20.9%** | `1/(1+3.79)` — needs to be right 1 in 5 |
| Momentum half-life | 3 days | signal decays fast → short hold |
| Trap flags | none | clean |
| **Expected growth contribution** | **+0.01%/yr** | `f·µ − ½f²σ²` |

Two other names, same run: **RELIANCE** (score 54.1) and **TCS** (score 40.7)
both produce **µ < 0 → Kelly 0 → v2 buys nothing.** v2 is not indiscriminately
greedy; below the floor its own math says *no bet*.

---

## 6. The finding that actually matters

> **At a realistic IC, Kelly's answer is ₹1,38,825. You chose ₹1,25,000.**

Those are 11% apart. The formal growth-maximising bet size for a ₹50 lakh book
in a liquid Indian large-cap, computed from the volatility of the stock itself,
is essentially the number you picked by instinct. That is not luck — it is what
happens when the honest edge is small: Kelly shrinks to modest sizes on its own.

And look at the growth column: **+0.01%/yr**. That is what a real, defensible
edge contributes per position. The fantasy of an agent compounding its way to a
data centre does not die at the ethics stage. It dies here, in the arithmetic,
long before.

---

## 7. So how does v2 ever look aggressive? Three doors — all of them lead to ruin

v2 only becomes the predator of the hypothetical by walking through one of these.

**Door 1 — assume a bigger IC.** IC is the *only* free parameter, and size scales
linearly with it. (Ladder computed at the rounded σ = 20.7%, so the IC-0.05 row
reads ₹1,38,889 against the ₹1,38,825 the code returns at full precision.)

| Assumed IC | µ | ¼-Kelly | Size on ₹50L | Full Kelly |
|---|---|---|---|---|
| 0.02 (honest, single weak factor) | +0.19% | 1.11% | ₹55,556 | 4.4% |
| **0.05 (used above)** | +0.48% | 2.78% | ₹1,38,889 | 11.1% |
| 0.10 (world-class, rarely sustained) | +0.95% | 5.56% | ₹2,77,778 | 22.2% |
| 0.20 (does not exist net of costs) | +1.90% | 11.11% | ₹5,55,556 | 44.4% |

Nothing in the data tells you which row is true. **Assuming your way to a bigger
bet is not an edge — it is leverage on an opinion.** Your own committed
walk-forward evidence puts honest alpha at **−2 to +9 pp/yr**, i.e. the top rows.

**Door 2 — leverage.** Multiplies `f` directly. Section 9 prices it.

**Door 3 — go small and illiquid**, where gross mispricing is largest. That is
exactly where engineered pumps look for buyers — which is why the trap detector
(section 10) exists in the same module.

---

## 8. The scaling wall — computed, not asserted

Impact follows the **square-root law**: `impact ≈ Y · σ_daily · √(Q/ADV)`.
Four times the size is only twice the impact — which sounds forgiving until you
see that it never stops growing. TATASTEEL, real ADV ₹530 Cr:

| Position size | Modelled impact |
|---|---|
| ₹1.25 lakh | 0.5 bps |
| ₹1 Cr | 4.2 bps |
| ₹10 Cr | 13.4 bps |
| ₹100 Cr | 42.5 bps |
| ₹1,000 Cr | **134.3 bps** |

At ₹1,000 Cr in one name you pay 1.34% just to get in — **more than the entire
estimated annual edge**. Capacity at a 25 bps budget, real numbers:

| Stock | ADV | Capacity @25 bps |
|---|---|---|
| BHARTIARTL | ₹1,218 Cr | ₹65.3 Cr |
| TATASTEEL | ₹530 Cr | ₹34.7 Cr |
| SUNPHARMA | ₹434 Cr | ₹25.1 Cr |
| MARUTI | ₹544 Cr | ₹24.3 Cr |
| HINDUNILVR | ₹338 Cr | ₹15.7 Cr |
| BAJAJFINSV | ₹174 Cr | ₹9.6 Cr |
| DRREDDY | ₹233 Cr | ₹7.5 Cr |

Seven large-caps absorb ~₹182 Cr. Scaled to a 25-name book: **~₹650 Cr is
roughly where this strategy stops working.** Real money — and a hard ceiling.
Past it, every added rupee makes every existing rupee worse. **Infinite scaling
is not available at any level of intelligence; the market's depth is the limit.**

---

## 9. The ruin law — why "no boundaries" ends at zero

Kelly's drawdown property: betting a multiple `m` of full Kelly, the probability
that equity **ever** falls to a fraction `a` of its start is `a^(2/m − 1)`. For
`a = 0.5` (ever halving):

Growth rate at multiple `m` of full Kelly is `(µ²/σ²)·(m − m²/2)`, peaking at
`m = 1`. Both columns computed, not asserted:

| Bet size | Growth rate (% of the maximum) | P(ever −50%) |
|---|---|---|
| **¼ Kelly (v2 default)** | 43.8% | **0.78%** |
| **½ Kelly** | **75.0%** | **12.5%** |
| **Full Kelly** | **100%** | **50%** |
| 1.5× Kelly | 75.0% | 79.4% |
| **2× Kelly and beyond** | **0%** | **100%** |

Three things to read off it:

1. **½ Kelly is the honest sweet spot** — three-quarters of the maximum growth
   rate for a 12.5% chance of ever halving, versus full Kelly's 50%. Full Kelly
   buys the last quarter of growth by quadrupling your risk of losing half the
   book.
2. **The curve is symmetric, the risk is not.** 1.5× Kelly earns exactly the
   same growth rate as ½ Kelly (75%) while carrying **6× the halving risk**
   (79.4% vs 12.5%). Overbetting is never a trade-off — it is pure loss.
3. **At 2× Kelly the growth rate is zero, and beyond it negative.** The account
   trends to zero **while appearing to work** for a long while first. That is
   the mathematical shape of the unbounded agent's ending.

v2 ships at ¼ Kelly — the most conservative row — precisely *because* µ is an
assumption (section 7). When your edge estimate might be wrong by 2×, betting as
if it were right is how you land in the bottom rows without knowing it.

Three further truths the fantasy omits:

- **Stops do not bound loss.** A −5% stop does not fire at −5% through a gap or
  a limit-down; it fires at whatever the next print is.
- **Leverage converts a drawdown into a liquidation.** A margin call sells for
  you, at the worst possible price, with no say from the algorithm.
- **Diversification evaporates when you need it.** Correlations run toward 1 in
  a crisis, so 12 concentrated names behave like one on the day it matters.

**Terminal states of an unbounded agent: (a) stays legal → over-leverages →
zero; (b) goes illegal → seized. Neither ends in a data centre.**

---

## 10. The defensive layer — the honest half of "find the flaws"

The v2 module ships a detector, not a playbook. It reads the *outside* signature
of an engineered move from public daily bars:

| Flag | What it sees | Why v2 needs it |
|---|---|---|
| `parabolic_without_volume` | +40% in 20d, volume flat | nobody real is buying it up |
| `thin_book_ramp` | +25% with < ₹1 Cr/day turnover | too thin to be genuine |
| `volume_spike_price_stall` | 3× volume, price flat | distribution into buyers |
| `repeated_limit_up` | ≥4 near-limit days in 20 | engineered or cascading |
| `no_down_days` | ≥18 of 20 days up | not natural trading |

Two or more flags → **v2 refuses the name**. This exists because v2's own
hunting ground (Door 3) is precisely where pump-and-dumps recruit exit
liquidity. Same knowledge as the dark version, pointed at not being the victim.

---

## 11. What this app honestly cannot do

Stated so no layer above is mistaken for something it isn't:

- **No options chain** → true convexity cannot be *priced*, only proxied by
  ATR room-to-high. Real v2 convexity (long gamma, defined-risk spreads) is not
  implementable here.
- **No order-book depth** → impact is *modelled* by the square-root law, never
  measured. `broker.bid_ask()` does not exist; the spread check has never run.
- **No live short interest, no intraday microstructure** → no shorting leg, no
  stat-arb, no execution scheduling.
- **FII/DII data ends 2025-03-29** (SEBI publishes months late) → flow signals
  are historical, not current.
- **IC is assumed, never measured** → the single largest source of error in
  every v2 number above.
- **No leverage rail** → v2's leverage discussion is arithmetic on this page, not
  a capability of this app.

---

## 12. Where the line is

v2 as built is aggressive and **legal**: Kelly sizing, factor edges, event
timing, illiquidity premia, volatility targeting — all ordinary quantitative
practice. The step past it — spoofing, layering, wash trades, pump-and-dump,
front-running, insider data — is **not a parameter of this algorithm and is not
in this repository**, on paper or otherwise. Two reasons, in order of how much
they should matter:

1. It is a crime that takes money from real people, and a paper harness is the
   on-ramp to a live one by this app's own design (60-day proof → owner's word →
   real orders). The artifact would be the same either way.
2. **It does not even solve the problem.** Section 8 is the proof: names small
   enough to move are too small to hold meaningful money, and names large enough
   to matter cannot be moved by any book you could build. The dark path fails on
   arithmetic before it fails in court.

---

## 13. Verdict

**v1 is not the timid version of v2. v1 is v2 with the ruin term priced in.**

The live engine's fixed ₹1.25L is within 11% of what Kelly independently
recommends at an honest IC. Its −5%/+12% brackets, −2% kill switch, breadth gate
and regime brake are the machinery that holds the halving risk near **0.78%**
instead of **50%**. That costs real growth — ¼ Kelly earns 44% of the
theoretical maximum rate, not 97% — and it is still the right trade, because the
maximum rate is only collectable by an agent that is certain of its own edge, and
no one here is (section 7).

If you ever want more aggression, the defensible move is **½ Kelly, not full**:
75% of the maximum growth for a 12.5% halving risk. That would be a real
decision with a real number attached — your call, not the engine's. Which is why
`/lens` shows v2 as a **view**, and a test permanently forbids it from placing
an order.

*Reproduce every figure here:* `trading/lens_v2.py` + `trading/tests/test_lens_v2.py`,
or open `/lens` in the app.
