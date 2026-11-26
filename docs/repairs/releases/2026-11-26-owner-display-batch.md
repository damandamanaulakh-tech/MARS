# DISP-001 — owner display batch (26 November decisions)

Status: **all six items implemented and locally tested on the PR234 branch; NOT merged, NOT deployed.** The
deployment hold in [the intraday record](2026-11-26-intraday-pipeline.md)
applies to this batch too. Display only: nothing here changes a score, gate,
size, stop, target, cash balance or saved record.

## Owner request

26 November, on the formula table: "Liquidity ₹5 crore 5-day turnover — but
its not adding the margin released in the total, it should add the amount,
and must match with each total", plus a watch label, a 20-session rank column,
two separately shown volume gates, one pre-rise signal, a market-versus-stocks
gap measure and a stock page that shows targets and segment verdicts. Defaults
approved ("go with defaults"). Items land one at a time; each is listed here
only once it is implemented and tested.

The two volume gates are recorded separately as
[VOL-001](2026-11-26-volume-gates.md): the second gate refuses Intraday buys,
so it is not a display-only item and does not belong under this batch's
"display only" statement.

## Item 1 — fund summary adds closed trades and the totals match

Before: the dashboard fund block showed open positions only. A book with
closed trades read Capital, Allocated and Cash with nothing explaining why
they did not sum — the result of every closed trade was invisible.

Now (`web/market_panels.fund_summary`, `web/templates/dashboard.html`):

- Per segment, from the closed trades the live book lists: **Booked P&L**
  (net of every cost of both legs, with the count) and **Released today**
  (entry cost of positions closed today IST — money back in the segment's
  free room today).
- A **"Does it add up?"** block with two lines that end on the same rupee:
  `Capital ± booked ± open P&L = equity` and `Cash + used (now) = equity`.
  "Booked" on that line is derived (equity − capital − open P&L), so it
  always balances and covers every closed trade and every charge paid. The
  listed closed trades are printed beside it; any gap is named (older trades
  archived out of the live list, entry charges already paid on open
  positions) rather than hidden. Listed trades whose record names no segment
  are counted once, on their own.
- Capital is the book's funded base (`capital_base`), so a Settings change
  to the paper capital is not read as profit.
- Layout: the two percentage columns became one, the share is shown in
  lakh/crore with the exact rupee figure in its tooltip, and a loss prints
  as −₹5,600 (it printed ₹-5,600). Measured in a browser with a sample book:
  the table fits its box at 1366 and 1280 px wide; on a phone it scrolls
  inside its own box as before and the page itself does not.

Tests: `web/tests/test_fund_summary_reconcile_2026_11_26.py` (10) — segment
figures, exact balance, a book with no closed trade and no stored base, a
capital change, four damaged stored trades, the rendered page, an empty book.

Limitations: the block reads the trades the live book lists; it does not load
the archive. Charges paid on open positions are not itemised on this card.
Not observed on the live dashboard (a session cannot log in to it).

Full local suite with item 1 on commit `7d8c113`: **3,819 passed, 0 failed,
0 skipped** (junit report read back).

## Item 2 — WATCH label from 55

Owner decision: "Watch Floor - 55 and above". `Config.watch_floor = 55.0`,
display only — a test asserts no engine, risk, order, danger, sizing, exit,
scoring or timing module reads it. The buy marks (62 / 72 / 70 / 60 / 68) are
unchanged and still decide.

- `web/market_panels.watch_label(score, buy_mark, watch_floor)`: `cleared` at
  or above the buy mark, `watch` from the watch floor up to it, blank below or
  when the score is not a number. Each stock is judged against the buy mark
  of the pass that scored it (read from the stored scan record).
- Dashboard watchlist: now the not-held names scored 55 and above, each with
  a WATCH or CLEARED chip; names below 55 (or with no stored score) are
  counted in the header, not listed. A WATCH row with no other recorded
  reason reads "below the buy mark".
- Scan page: a WATCH chip beside the score. The page computes the label; the
  stored picks are not modified by a page view (test-pinned).

Tests: `web/tests/test_watch_label_2026_11_26.py` (14). All web tests pass
with items 1 and 2.

Behaviour to know: the dashboard watchlist used to list every scored name
not held, whatever its score. A name scored below 55 no longer appears there.

## Item 3 — the stock page shows the five segments, stops and targets

Owner request: "when u click the selected stock, it should show target
prices, and category of 5 momentum, intra etc". Every stock link already
opens `/chart/{ticker}`; that page now carries a card above the chart
(`web/stock_card.py`, `web/templates/chart.html`).

- One row per segment: its buy mark (the segment's own settings, owner edits
  included), its share of capital, the score, a CLEARED / WATCH / BELOW /
  NOT SCORED reading, and the stop and target a new buy at the last stored
  close would be given — computed by the engine's own `trading.targets`
  functions (a test compares them directly), with the source named
  (segment stop or 2 × ATR; k × ATR or the fixed target).
- Scores are never fetched. Momentum uses its stored momentum-pass score, or
  is computed on the page from stored bars (momentum and quality are pure
  price maths). The other four score on outside feeds, so they show a score
  only when the last stored scan pass was that segment's; otherwise the row
  says NOT SCORED. A test fails the card if it calls the composite scorer,
  the daily loader or the news reader.
- Intraday shows its rule, not a price: it is priced off 5-minute bars at
  the moment of entry and squared off at 15:25.
- A held stock shows its own segment, quantity, entry, stored stop and
  target, result at the last stored close, progress to target and days held.
- Valuation target: the stored record a new non-intraday buy would take as
  its target under the existing valuation-exit rule, or the plain reason none
  would be used.
- No stored history: the rules are still shown, prices are not, and the card
  says nothing is guessed. A fault in the card hides the card, never the page.

Tests: `web/tests/test_stock_card_2026_11_26.py` (10). Rendered in a browser
with a sample book at 1280 px (table fits) and 390 px (table scrolls inside
its own box; the page does not).

Limitation: stops and targets are worked from the last STORED close, not a
live quote, and the page says so; the real ones are set at the fill price.

## Item 4 — "Market vs stocks": the index against the typical stock

Owner: "now market goes quite high but not much movement in stocks, this
bubble I want to define".

- `trading/market_gap.py` (pure, no network): for 20 and 60 sessions, the
  benchmark's return, the MEDIAN return of the company shares in the scan
  list, the gap in percentage points, the share of stocks ahead of the index
  and the share that are up. A stock votes only with a full window of closes
  ending within 7 days of the index's last bar; fund units are left out;
  fewer than 100 voters gives no reading.
- The Momentum scan (which already holds two years of bars for the whole
  list) stores the reading on the book (`Portfolio.market_gap`, saved to JSON
  and Mongo and read back from both). A thin or failed reading never replaces
  a good one. The 5-minute side pass and the other categories do not write it.
- Dashboard card "Market vs stocks" under the index strip: one plain sentence
  for the longest window, both windows in a table, the as-of date, and an
  amber "days old" note when the reading is more than 5 days old.
- "Narrow rally" is worded when the index is up, the gap is 5 points or more
  and fewer than 40% of stocks are ahead. **Both cut-offs are ASSUMED** from
  the 25 November example and printed as assumptions; nothing was tested as a
  predictor and the card says it describes what happened, not a forecast.
- No trading rule reads it: a test walks the syntax tree of the engine and
  eleven rule modules — the engine only ever assigns the field.

Check against real data: on the public daily closes saved on 25 November
(253 company shares and the Nifty) the function returns, for 60 sessions,
Nifty +7.17%, median stock -2.95%, gap 10.12 points, 22% of stocks ahead; for
20 sessions +13.49% / +7.17% / 6.32 points / 26% ahead.

Tests: `trading/tests/test_market_gap_2026_11_26.py` (13) and
`web/tests/test_market_gap_card_2026_11_26.py` (10).

Limitation: the reading is as fresh as the last Momentum scan and the stored
daily bars; it appears on the live dashboard only after the first Momentum
pass on the deployed revision.

## Full-suite total with items 1-4

Full local suite on commit `4b08082` from a clean state directory:
**3,866 passed, 0 failed, 0 errors, 0 skipped** (junit report read back) —
3,809 after the intraday batch plus 10 + 14 + 10 + 23 new tests. CI on the
pull request is recorded separately once it completes.

## Item 5 — 20-session rank column

Owner decision: "20-session top-10 rank - Live Rank Rule"; approved default:
a column, not a buy rule.

- `multi_formula.rank_mom20`: each scanned stock's place by 20-session
  return (highest = 1) among the stocks the pass could rank, with the top 10
  marked. A stock with no 20-session return is unranked, never ranked last.
- The scan writes it onto the formula view each row already stores, so the
  saved book and both pages carry it with no new field. The 5-minute side
  pass does not compute it.
- Scan page: "★ 20d #1 of 812" under the three-formula chips, and a line in
  the formulas card stating the measured record of BUYING on it: −0.77
  (train) and −0.45 (test) against our own picks
  (`research/ash_formula_study/`). A formula is not shown without its record.
- No rule reads it: a syntax-tree test over the engine and eight rule
  modules, and the owner's switchable three-formula filter ignores it.

Tests: `trading/tests/test_rank20_column_2026_11_26.py` (5) and
`web/tests/test_rank20_scan_page_2026_11_26.py` (2).

## Full-suite total with items 1-5

Full local suite on commit `8fb48ed` from a clean state directory:
**3,873 passed, 0 failed, 0 errors, 0 skipped** (junit report read back).

## Item 6 — pre-rise label (compression + volume ignition as one signal)

Owner decision: "pre-rise = compression (T12W_0005) + volume ignition
(T12W_0057) as ONE signal"; approved default: the ignition within 5 trading
sessions of the compression.

**Measured before it was built** (`scripts/pre_rise_study.py`,
[summary](../../../research/pre_rise_study/summary.md)): 191 stocks x 12
years, every trading day, the BT01-BT08 labels and hit rule, split at
2020-06-30. The pair fired 373 times in 469,947 stock-days (332 separate
stock-months). A +10% label followed 29.49% of the time against 32.11% for
any day: lift 0.919 (0.996 to June 2020, 0.799 after). On the strict +15%
labels: 1.001 (1.099, then 0.853). It is not better than chance. The study's
own check: the compression half measured alone reproduces the committed
2026-08-09 lift exactly (1.063 / 1.146).

- `trading/pre_rise.py` (pure, display only): both halves come from the
  production trigger functions — no second copy of either formula. The label
  is on from the day an ignition bar has a compression bar on the same day or
  in the 5 sessions before it, and stays for 5 sessions (a display choice,
  stated on the screen; the study judges the day it fires). Too little
  history or a broken trigger reads "unknown", never "no signal".
- The measured record is loaded from the committed run file, so no screen can
  print a different figure; without the file the screens say "not measured".
- The daily trigger pass stores the reading with each stock's row
  (`trigger_watch`). A failed reading costs the label, never the pass.
- Screens: a Pre-rise column and a rule-plus-record note on the trigger
  table; the reading in plain words on the stock's trigger page and on the
  stock page card; a chip beside the stock on the Scan page, read from the
  stored pass and shown only while its bars are at most 7 days old; one line
  in the Den.
- No rule reads it: a syntax-tree test over the engine and eleven rule
  modules, and the module itself imports nothing but the trigger functions.
- On the real daily bars to 25 November (253 stocks): the label is on for 1
  stock; compression in the last 10 sessions for 3; ignition for 68.

Tests: `trading/tests/test_pre_rise_2026_11_26.py` (16) and
`web/tests/test_pre_rise_pages_2026_11_26.py` (9). The module is new, so
these files cannot be collected on the previous commit.

## Full-suite total with all six items

Full local suite on commit `2e5198d` from a clean state directory:
**3,951 passed, 0 failed, 0 errors, 0 skipped** (junit report read back).
That commit also carries the two volume gates (VOL-001).
