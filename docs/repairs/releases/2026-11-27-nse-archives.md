# "Data is needed" — the stalest files catch themselves up

**Owner, 2026-11-27:** *"render have enough storage to expand / so no worry /
Data is needed, / its there on repo assist too, u can let me know i will feed
u more and accordingly"*

## Nothing had been removed

The owner believed heavy data files had been deleted. They had not. All 21 are
committed and `git status` on `data/` is clean — delivery 49.8 MB, insider 17.3
MB, F&O participant OI 7.6 MB, F&O open interest 4.7 MB, the whole FII/DII
family. **The problem was staleness, not absence:** most stop on
7–13 August 2026.

One thing *was* switched off: on 24 September the 52 MB delivery file stopped
being **read** in production (one line, `PROD_SOURCES`) to survive the 512 MB
box. The file never left the repository.

## The correction that made this cheap

Every note in this repo — `research/tools/fetch_kit/README_FETCH_GUIDE.md` and
CLAUDE.md in several places — said **nsearchives.nseindia.com blocks this host
and the owner's own Colab notebook was the only refresh path.**

**That is no longer true.** Verified with real requests on 2026-11-27, by two
independent readers and then by hand:

```
sec_bhavdata_full_26112026.csv    200   403,413 bytes   real CSV
fao_participant_oi_26112026.csv   200   real, 5 client rows
BhavCopy_NSE_FO_...20261125...zip 200   1.19 MB real zip
```

Checked across seven dates including a Saturday that correctly returned **404**,
so it is not a fluke. One day's delivery file arrives in **under half a
second**, and the gap from 10 August is **74 trading days** — a minute or two of
fetching, not a manual export.

## What changed

* `data_refresh.fetch_delivery_day(day)` and `fetch_participant_oi_day(day)`.
  Columns were **read from the live files, not guessed**, and are mapped **by
  name**, so a reorder at NSE cannot scramble the data silently — there is a
  test that reorders the header and still reads the right numbers. The guards
  are the ones this repo learned the hard way: a missing date answers **200
  with an HTML page**, so the body is validated and never the status code
  alone; a day missing any column we store is **refused** rather than written
  skewed; `DELIV_QTY` is `-` on series that do not report delivery, and those
  rows are skipped; only EQ/BE/BZ are kept, as the committed panel always did.
* `db.save_delivery_days` stores **one document per day** —
  `{_id: "2026-11-26", rows: {SYMBOL: [traded, delivered]}}`, ~2,800 symbols,
  well inside Mongo's limit. One per symbol-day would be ~150,000 documents for
  the same information.
* **No committed CSV is ever rewritten.** Fresh rows go to Mongo and are
  layered on at read time in `external_panels._load_delivery` — the pattern FII
  and the index tail already use — so the repository does not grow. That
  matters: `data/` is already 177 MB and **Render clones all of it on every
  deploy**, so every megabyte added is paid again on every deploy. Participant
  OI needed no new merge at all: `extend_pwoi_df` and `save_pwoi_rows` already
  existed and nothing had ever written to them.
* `scheduler._weekly_nse_archives_refresh`, registered as a weekly step
  **before** the hour-long price pull like every other short feed — so it
  inherits OPS-03's wall-clock cap from the same day. It resumes from the last
  stored day, 40 days a run, and journals what is left.
* A delivery day that is unreadable in the base file no longer discards the
  stored half: the stream failure logs and carries on to the overlay.

## The two the owner's upgrade unblocked

* **The delivery source is read in production again.** On their word ("enough
  storage to expand so no worry") and on measurements, not on the old comment:
  the load costs **+93 MB at the peak** and +46 MB resident, in 3.2 s, at the
  production scope of 80 names (+96 MB at 120 — the cost is the file, not the
  number of stocks, so it does not grow with the universe), against a live
  process **measured at a 749 MB peak**, which no 512 MB plan allows.
* **The Den's 380 MB ceiling is raised to 900.** That number was tuned for a
  box that no longer exists, and the only thing it was still costing was two
  working screens: the process sits above 380 MB for most of a trading day, so
  the valuation sections said *"not computed — memory"* all day with plenty to
  spare. The guard itself stays, and a test now asserts it is never zero.

## Money, honestly

**None, either way.** What the delivery file feeds is `T12W_0060`, which is
**calibrated REJECTED** (lift 0.992 over 59,195 windows, firing on 22% of all
days), plus register and display items. **Fresh data on a rejected trigger is
still a rejected trigger** — the gain is that the next time anyone measures it,
they will be measuring this year rather than a seven-week-old number wearing
today's date. Participant OI feeds register items FII-04/05 and the two OI
tripwires, also display. Nothing here reads or writes a position, a stop or a
target.

## Tests

`web/tests/test_nse_archives_2026_11_27.py` — 24 tests, **all 24 verified to
fail on the pre-repair code.** The live shapes are reproduced as fixtures so a
change at NSE shows up as a failing parse rather than skewed data; nothing in
the file touches the network. Coverage: a real day parses; non-EQ/BE/BZ series
and no-delivery rows are dropped; an HTML page with a 200 is not data; a
missing column refuses the day; participant OI maps every column the committed
panel stores (checked against that file's own header) and survives a reordered
header; only trading days after the last stored one are fetched and today never
is; the run is capped and the real gap is >60 days; an unreadable last date
fetches nothing rather than everything; the committed last dates are pinned
against the files themselves, because a constant that must match a file is a
drift risk; stored days alone reach the panel, the 30-bar minimum is unchanged,
a later reading of the same day wins, a symbol not asked for is not loaded, a
malformed row is skipped; delivery is in `PROD_SOURCES` again; the step is
registered before the pull, skips cleanly with no database, and reports what is
left.

## Still the owner's to supply

* **Shareholding detail** — the FII/DII/mutual-fund split and pledge %, behind
  register items H-02…H-06 and X-07. This is the one with real value still
  locked behind DATA_NEEDED.
* **Total Return Index** — needs a POST this session was not permitted to make;
  genuinely unverified rather than known-blocked.
* **Insider dealings** (stops 2026-03-16) and **bulk/block deals** — and NSE
  caps the deal files at 70 rows a month, so those can never be complete.
