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
