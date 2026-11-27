"""Owner 2026-11-27: "Data is needed" — delivery and participant OI catch up.

Both files were frozen at 2026-08-07 because every note in this repo said
nsearchives.nseindia.com blocks this host and the owner's own Colab notebook
was the only refresh path. Checked with real requests on 2026-11-27 across
seven dates, including a Saturday that correctly returned 404: it answers.

Nothing here touches the network. The live shapes were read from the real files
first and are reproduced as fixtures, so a change at NSE shows up as a failing
parse rather than as silently skewed data.
"""
from datetime import date
from types import SimpleNamespace

import pytest

from trading import data_refresh as dr
from trading import external_panels as ep
from web import db, scheduler


# The real header, read from sec_bhavdata_full_26112026.csv on 2026-11-27.
DELIVERY_CSV = (
    "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, "
    "LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, "
    "NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
    "TCS, EQ, 26-Nov-2026, 3000, 3010, 3020, 2990, 3005, 3005, 3005, "
    "5534593, 1000, 100, 2516164, 45.46\n"
    "THINBE, BE, 26-Nov-2026, 100, 100, 100, 100, 100, 100, 100, "
    "1000, 1, 1, 400, 40.00\n"
    "AGILT, GS, 26-Nov-2026, 100, 100, 100, 100, 100, 100, 100, "
    "50, 1, 1, 50, 100.00\n"
    "NODLV, SM, 26-Nov-2026, 100, 100, 100, 100, 100, 100, 100, "
    "500, 1, 1, -, -\n")

# The real participant-OI shape, read from fao_participant_oi_26112026.csv.
PWOI_CSV = (
    '""Participant wise Open Interest (no. of contracts) in Equity '
    'Derivatives as on Nov 26, 2026"",,,,,,,,,,,,,,\n'
    "Client Type,Future Index Long,Future Index Short,Future Stock Long,"
    "Future Stock Short,Option Index Call Long,Option Index Put Long,"
    "Option Index Call Short,Option Index Put Short,Option Stock Call Long,"
    "Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,"
    "Total Long Contracts,Total Short Contracts\n"
    "Client,150618,77370,3029623,226442,2929777,2759780,2878071,3208984,"
    "1345799,567778,783852,702087,10783374,7876806\n"
    "FII,37660,126129,3424190,2540254,535348,774403,644850,558824,"
    "152614,163563,176658,132752,5087777,4179466\n")

HTML_INSTEAD = "<html><head><title>404</title></head><body>Not Found</body></html>"


def serve(text):
    return lambda url, timeout=20.0: text


# ------------------------------------------------------------ delivery parse

def test_a_real_day_parses_into_one_document(monkeypatch):
    monkeypatch.setattr(dr, '_archive_get', serve(DELIVERY_CSV))
    doc = dr.fetch_delivery_day(date(2026, 11, 26))
    assert doc['date'] == '2026-11-26'
    assert doc['rows']['TCS'] == [5534593.0, 2516164.0]
    assert doc['rows']['THINBE'] == [1000.0, 400.0]          # BE is kept


def test_series_outside_the_panels_own_set_are_dropped(monkeypatch):
    """The committed panel has always kept EQ/BE/BZ only."""
    monkeypatch.setattr(dr, '_archive_get', serve(DELIVERY_CSV))
    doc = dr.fetch_delivery_day(date(2026, 11, 26))
    assert 'AGILT' not in doc['rows'], "a government security is not an equity"
    assert set(doc['rows']) == {'TCS', 'THINBE'}


def test_a_row_with_no_delivery_figure_is_skipped(monkeypatch):
    """DELIV_QTY is '-' on series that do not report delivery."""
    monkeypatch.setattr(dr, '_archive_get', serve(DELIVERY_CSV))
    doc = dr.fetch_delivery_day(date(2026, 11, 26))
    assert 'NODLV' not in doc['rows']


def test_an_html_page_with_a_200_is_not_data(monkeypatch):
    """The trap this repo knows: a missing date answers 200 with HTML."""
    monkeypatch.setattr(dr, '_archive_get', serve(HTML_INSTEAD))
    assert dr.fetch_delivery_day(date(2026, 11, 21)) is None


def test_a_missing_column_refuses_the_day(monkeypatch):
    """Better no day than a day stored under the wrong names."""
    stripped = DELIVERY_CSV.replace(' DELIV_QTY,', ' SOMETHING_ELSE,')
    monkeypatch.setattr(dr, '_archive_get', serve(stripped))
    assert dr.fetch_delivery_day(date(2026, 11, 26)) is None


def test_nothing_fetched_is_not_an_empty_day(monkeypatch):
    monkeypatch.setattr(dr, '_archive_get', serve(None))
    assert dr.fetch_delivery_day(date(2026, 11, 26)) is None


# -------------------------------------------------------- participant OI parse

def test_participant_oi_maps_every_column_by_name(monkeypatch):
    monkeypatch.setattr(dr, '_archive_get', serve(PWOI_CSV))
    rows = dr.fetch_participant_oi_day(date(2026, 11, 26))
    assert [r['client_type'] for r in rows] == ['Client', 'FII']
    fii = rows[1]
    assert fii['date'] == '2026-11-26'
    assert fii['fut_idx_long'] == 37660 and fii['fut_idx_short'] == 126129
    assert fii['total_long'] == 5087777 and fii['total_short'] == 4179466
    # every column the committed panel stores is present
    import csv
    import pathlib
    head = pathlib.Path('data/fo_participant_oi_2024_2026.csv').read_text(
        encoding='utf-8').splitlines()[0].split(',')
    assert set(head) == set(fii), (set(head) ^ set(fii))


def test_a_reordered_participant_header_is_still_read_correctly(monkeypatch):
    """Mapped by NAME, so a reorder cannot scramble the data silently."""
    lines = PWOI_CSV.splitlines()
    head = lines[1].split(',')
    order = [0, 2, 1] + list(range(3, len(head)))           # swap two columns
    lines[1] = ','.join(head[i] for i in order)
    for i in (2, 3):
        cells = lines[i].split(',')
        lines[i] = ','.join(cells[j] for j in order)
    monkeypatch.setattr(dr, '_archive_get', serve('\n'.join(lines) + '\n'))
    rows = dr.fetch_participant_oi_day(date(2026, 11, 26))
    fii = next(r for r in rows if r['client_type'] == 'FII')
    assert fii['fut_idx_long'] == 37660 and fii['fut_idx_short'] == 126129


def test_a_missing_participant_column_refuses_the_day(monkeypatch):
    monkeypatch.setattr(dr, '_archive_get',
                        serve(PWOI_CSV.replace('Total Short Contracts', 'X')))
    assert dr.fetch_participant_oi_day(date(2026, 11, 26)) == []


# ----------------------------------------------------------- which days, and resume

def test_only_trading_days_after_the_last_stored_one_are_fetched():
    days = scheduler._archive_days_to_fetch('2026-08-07', today=date(2026, 8, 15))
    iso = [d.isoformat() for d in days]
    assert iso[0] == '2026-08-10', iso          # the 8th and 9th are a weekend
    assert '2026-08-08' not in iso and '2026-08-09' not in iso
    assert all(d > '2026-08-07' for d in iso)
    assert all(d < '2026-08-15' for d in iso)  # today is never fetched


def test_the_run_is_capped_so_the_block_keeps_moving():
    capped = scheduler._archive_days_to_fetch('2026-08-07', today=date(2026, 11, 27),
                                              cap=5)
    assert len(capped) == 5
    whole = scheduler._archive_days_to_fetch('2026-08-07', today=date(2026, 11, 27))
    assert len(whole) > 60, len(whole)          # the real gap, ~74 days


def test_an_unreadable_last_date_fetches_nothing_rather_than_everything():
    assert scheduler._archive_days_to_fetch('not-a-date') == []
    assert scheduler._archive_days_to_fetch('') == []


def test_the_committed_last_dates_match_the_files():
    """A constant that must match a file is a drift risk — so pin it."""
    import gzip
    last = ''
    with gzip.open('data/delivery_daily_2019_2026.csv.gz', 'rt') as f:
        f.readline()
        for line in f:
            d = line.split(',', 1)[0]
            if d > last:
                last = d
    assert last == scheduler.DELIVERY_COMMITTED_THROUGH == ep.DELIVERY_BASE_THROUGH
    import pathlib
    pwoi_last = max(ln.split(',', 1)[0] for ln in
                    pathlib.Path('data/fo_participant_oi_2024_2026.csv')
                    .read_text(encoding='utf-8').splitlines()[1:] if ln)
    assert pwoi_last == scheduler.PWOI_COMMITTED_THROUGH


# ------------------------------------------------------------ the read-time overlay

def _stored(n, symbol='TCS', traded=1000.0, delivered=500.0, start='2026-09-01'):
    import pandas as pd
    days = pd.bdate_range(start, periods=n).strftime('%Y-%m-%d')
    return [{'date': d, 'rows': {symbol: [traded, delivered]}} for d in days]


def test_stored_days_alone_can_build_the_series(monkeypatch):
    """The committed 50 MB file is never rewritten; fresh days are layered on.

    The base is stubbed away entirely here, so what is proven is that the
    stored half really reaches the panel rather than being decoration.
    """
    import gzip
    monkeypatch.setattr(ep, '_stored_delivery_days', lambda: _stored(35))
    monkeypatch.setattr(gzip, 'open',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('no file')))
    out = ep._load_delivery({'TCS'})
    assert 'TCS' in out, "stored days must reach the panel"
    assert len(out['TCS']) == 35
    assert abs(float(out['TCS'].iloc[-1]) - 0.5) < 1e-9     # 500 of 1000


def test_too_few_days_is_still_not_judged(monkeypatch):
    """The panel's own 30-bar minimum is unchanged by the overlay."""
    import gzip
    monkeypatch.setattr(ep, '_stored_delivery_days', lambda: _stored(29))
    monkeypatch.setattr(gzip, 'open',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('no file')))
    assert ep._load_delivery({'TCS'}) == {}


def test_a_later_reading_of_the_same_day_wins(monkeypatch):
    """One reading per date — a correction must land, not be ignored.

    The same rule covers a stored day that also exists in the committed file,
    because the file's arrays are concatenated first and the overlay last.
    """
    import gzip
    days = _stored(35)
    days.append({'date': days[-1]['date'], 'rows': {'TCS': [1000.0, 900.0]}})
    monkeypatch.setattr(ep, '_stored_delivery_days', lambda: days)
    monkeypatch.setattr(gzip, 'open',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('no file')))
    out = ep._load_delivery({'TCS'})
    assert abs(float(out['TCS'].iloc[-1]) - 0.9) < 1e-9, out['TCS'].tail(2)


def test_a_symbol_the_batch_did_not_ask_for_is_not_loaded(monkeypatch):
    import gzip
    monkeypatch.setattr(ep, '_stored_delivery_days',
                        lambda: _stored(35, symbol='OTHER'))
    monkeypatch.setattr(gzip, 'open',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('no file')))
    assert ep._load_delivery({'TCS'}) == {}


def test_a_malformed_stored_row_is_skipped_not_fatal(monkeypatch):
    import gzip
    days = _stored(35)
    days += [{'date': '2026-10-20', 'rows': {'TCS': ['x', None]}},
             {'date': '2026-10-21', 'rows': {'TCS': [1000.0]}},
             {'date': '', 'rows': {'TCS': [1.0, 1.0]}}]
    monkeypatch.setattr(ep, '_stored_delivery_days', lambda: days)
    monkeypatch.setattr(gzip, 'open',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('no file')))
    out = ep._load_delivery({'TCS'})
    assert len(out['TCS']) == 35


def test_delivery_is_read_in_production_again():
    """Switched off in September for the 512 MB box; the owner has since said
    there is room, and a 749 MB peak was measured on the live process."""
    assert 'delivery' in ep.PROD_SOURCES
    assert set(ep.PROD_SOURCES) == set(ep.PanelSet.ALL_SOURCES)


def test_no_reader_means_exactly_the_old_behaviour(monkeypatch):
    monkeypatch.setattr(ep, '_delivery_reader', None)
    assert ep._stored_delivery_days() == []


def test_a_broken_reader_never_breaks_the_panel(monkeypatch):
    monkeypatch.setattr(ep, '_delivery_reader',
                        lambda: (_ for _ in ()).throw(RuntimeError('store down')))
    assert ep._stored_delivery_days() == []


# -------------------------------------------------------------- the weekly step

def test_the_step_is_registered_in_the_weekly_block():
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(scheduler.__file__).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == '_weekly_history_topup')
    body = ast.unparse(fn)
    assert '_weekly_nse_archives_refresh' in body
    assert 'NSE archives' in body
    # and BEFORE the hour-long price pull, like every other short feed
    # before the hour-long price pull, like every other short feed. Anchor
    # on the step CALL, not the bare phrase: the skip messages near the top
    # of the function mention the OHLCV top-up too.
    pull_at = body.index("_run_weekly_step('OHLCV top-up'")
    assert body.index("'NSE archives'") < pull_at


def test_the_step_skips_cleanly_with_no_database(monkeypatch):
    monkeypatch.setattr(db, 'is_connected', lambda: False)
    rows = []
    monkeypatch.setattr(scheduler, '_refresh_journal',
                        lambda a, w: rows.append((a, w)))
    scheduler._weekly_nse_archives_refresh()
    assert rows and rows[0][0] == 'DATA_REFRESH_ARCHIVES_SKIPPED'


def test_the_step_stores_what_it_fetched_and_says_what_is_left(monkeypatch):
    saved = {}
    monkeypatch.setattr(db, 'is_connected', lambda: True)
    monkeypatch.setattr(db, 'latest_delivery_day', lambda: '2026-11-24')
    monkeypatch.setattr(db, 'latest_pwoi_day', lambda: '2026-11-24')
    monkeypatch.setattr(db, 'save_delivery_days',
                        lambda docs: saved.setdefault('dlv', docs) or len(docs))
    monkeypatch.setattr(db, 'save_pwoi_rows',
                        lambda rows: saved.setdefault('pwoi', rows) or len(rows))
    monkeypatch.setattr(dr, 'fetch_delivery_day',
                        lambda d: {'date': d.isoformat(), 'rows': {'TCS': [1.0, 1.0]}})
    monkeypatch.setattr(dr, 'fetch_participant_oi_day',
                        lambda d: [{'date': d.isoformat(), 'client_type': 'FII'}])
    lines = []
    monkeypatch.setattr(scheduler, '_refresh_journal',
                        lambda a, w: lines.append((a, w)))
    scheduler._weekly_nse_archives_refresh()
    assert lines and lines[-1][0] == 'DATA_REFRESH_ARCHIVES'
    assert saved.get('dlv'), "a fetched day must be stored"
    assert 'delivery' in lines[-1][1] and 'participant OI' in lines[-1][1]
