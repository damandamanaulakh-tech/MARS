# DISP-001 — owner display batch (26 November decisions)

Status: **in progress on the PR234 branch; NOT merged, NOT deployed.** The
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

Tests: `web/tests/test_watch_label_2026_11_26.py` (15). All 1,560 web tests
pass with items 1 and 2.

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
