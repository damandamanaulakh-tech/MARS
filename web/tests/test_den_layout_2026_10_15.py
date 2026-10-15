"""MARS Den layout faults (2026-10-15 layout review, Chromium at 360-1920 px).
Money impact NONE — markup only; every number on these cards is unchanged.

What the review measured on the 10-15 main, and what these tests pin:

  §2 five-category table — 1,368-1,431 px wide: at 1366 px Max hold was cut
     and Candle gate / Kelly check / Cash share were off-screen; at 1024 px
     everything from "@ median ATR" on. Now the Category cell wraps, the
     multi-word headers and the Stop cell's ATR note may wrap, and Cash share
     sits right after Floor (measured: 1,044 px at 1366, every column shown).
  §2 sizing ladder — 11-43 px wider than its box at 360/390/1024, so the bold
     Size values read "₹1,50,0…". Cells use px-1 now (fits at 360/390/1024).
  §8 "Volatility and the stop rule" — the no-wrap "Live rule: 2.0×ATR, floor
     −5.0%, cap −15.0%" header made the table ~690-710 px in a 513 px card,
     hiding the very column the card exists for. The header wraps now.
  §8 walk-forward — at 1024 px the card was 342 px and Alpha / Worst DD were
     hidden; the two cards now sit side by side only from xl (1280 px), and
     the no-wrap "Out-of-sample window" header may wrap (at 1280 px the table
     was 506 px in a 470 px card).
  §10 sandbox knobs — a label wrapping to two lines pushed that field's input
     ~15-30 px below its neighbours; the grid aligns its items to the bottom.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from web import auth as auth_mod
from web.main import app

_CACHE: dict[str, str] = {}


def _atr_row() -> dict:
    # A display fixture for the §8 ATR card (it renders only when the book
    # holds a name with stored history, which the shared test state may not).
    from trading import targets as T
    return {"ticker": "TCS.NS", "asof": "17 Jul", "last": 2269.0,
            "atr": 69.84, "atr_pct": 3.08,
            "fixed_stop": 2155.55, "fixed_stop_pct": 5.0,
            "atr_stop": 2129.33, "atr_stop_pct": 6.16,
            "source": "atr", "widened": True,
            "k": T.ATR_STOP_K, "cap_pct": round(T.ATR_STOP_CAP * 100, 1)}


@pytest.fixture
def den_html(monkeypatch) -> str:
    """One owner render of /den for the whole file (function-scoped so the
    conftest's no-network fixtures are already in place when it runs)."""
    if "html" not in _CACHE:
        from web import market_panels as mp
        monkeypatch.setattr(mp, "atr_panel", lambda tickers, limit=4: [_atr_row()])
        c = TestClient(app)
        r = c.post("/login", data={"email": "owner@sourceborn.in",
                                   "password": auth_mod._SEEDED_PWD},
                   follow_redirects=False)
        assert r.status_code == 303, "owner login failed"
        page = c.get("/den")
        assert page.status_code == 200
        _CACHE["html"] = page.text
    return _CACHE["html"]


def _after(html: str, marker: str, end: str = "</table>") -> str:
    i = html.find(marker)
    assert i >= 0, f"{marker} did not render"
    j = html.find(end, i)
    assert j > i, f"no {end} after {marker}"
    return html[i:j]


def _text(fragment: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def _category_table(html: str):
    block = _after(html, "den_categories_marker")
    thead = block[block.find("<thead"):block.find("</thead>")]
    heads = [(cls, _text(inner)) for cls, inner in
             re.findall(r'<th class="([^"]*)">(.*?)</th>', thead, re.S)]
    rows = re.findall(r'<tr [^>]*data-den-category="([^"]+)">(.*?)</tr>', block, re.S)
    assert len(rows) == 5, "expected the five category rows"
    cells = {key: re.findall(r'<td class="([^"]*)"([^>]*)>(.*?)</td>', row, re.S)
             for key, row in rows}
    return heads, cells


# ---------------------------------------------------------------- §2 table

def test_cash_share_header_comes_right_after_floor(den_html):
    heads, cells = _category_table(den_html)
    names = [t for _, t in heads]
    assert names.count("Cash share") == 1
    assert names.index("Cash share") == names.index("Floor") + 1, names
    for key, tds in cells.items():
        assert len(tds) == len(heads), (key, "header and row column counts differ")
        floor_at = next(i for i, (_, attrs, _) in enumerate(tds) if "data-den-floor" in attrs)
        assert floor_at == names.index("Floor"), key
        share = _text(tds[floor_at + 1][2])
        assert re.fullmatch(r"\d+(\.\d+)?%|—", share), (key, share)


def test_category_cell_wraps(den_html):
    _, cells = _category_table(den_html)
    for key, tds in cells.items():
        cls = tds[0][0]
        assert "whitespace-nowrap" not in cls, (key, cls)
        assert "min-w-[9rem]" in cls, (key, cls)


def test_long_headers_and_the_stop_note_may_wrap(den_html):
    heads, cells = _category_table(den_html)
    for cls, text in heads:
        if " " in text:
            assert "whitespace-nowrap" not in cls, (text, cls)
    stop_at = [t for _, t in heads].index("Stop")
    for key, tds in cells.items():
        assert "whitespace-nowrap" not in tds[stop_at][0], (key, tds[stop_at][0])


# ------------------------------------------------------------ §2 sizing

def test_sizing_ladder_cells_use_px1(den_html):
    block = _after(den_html, "den_sizing_marker")
    table = block[block.find('<table class="w-full text-xs">'):]
    classes = re.findall(r'<t[hd] class="([^"]*)"', table)
    assert len(classes) > 5, "the ladder rows did not render"
    for cls in classes:
        assert "px-1" in cls.split() and "px-2" not in cls.split(), cls


# ----------------------------------------------------------------- §8

def test_atr_live_rule_header_has_no_nowrap(den_html):
    block = _after(den_html, "den_atr_marker")
    m = re.search(r'<th class="([^"]*)">Live rule:', block)
    assert m, "the live-rule header did not render"
    assert "whitespace-nowrap" not in m.group(1), m.group(1)


def test_walk_forward_grid_goes_two_columns_only_at_xl(den_html):
    m = re.search(r'<div class="([^"]*\bgrid\b[^"]*)">\s*'
                  r'<div class="border border-line bg-white"><!-- den_walk_forward_marker -->',
                  den_html)
    assert m, "the walk-forward card is no longer the grid's first item"
    cls = m.group(1).split()
    assert "xl:grid-cols-2" in cls and "lg:grid-cols-2" not in cls, cls


def test_walk_forward_window_header_may_wrap(den_html):
    block = _after(den_html, "den_walk_forward_marker")
    m = re.search(r'<th class="([^"]*)">Out-of-sample window</th>', block)
    assert m, "the window header did not render"
    assert "whitespace-nowrap" not in m.group(1), m.group(1)


# ---------------------------------------------------------------- §10

def test_sandbox_knob_grid_aligns_inputs_to_the_bottom(den_html):
    block = _after(den_html, "den_sandbox_form_marker", end="</form>")
    m = re.search(r'<div class="([^"]*\bgrid\b[^"]*)">', block)
    assert m, "the knob grid did not render"
    assert "items-end" in m.group(1).split(), m.group(1)
