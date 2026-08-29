"""Quarterly institutional holdings on the scan row.

ADOPTED 2026-08-29 (owner order "go ... 2") from the external
AM07-vs-AshStocks review, finding C-014: AshStocks surfaces Upstox
quarterly shareholdings on its scan rows, while AM07 held the same data —
richer (FII/DII/MF/insurance, holder counts, pledge flag, two quarters,
193 symbols) — only inside /register. Nothing new is fetched; this is the
data moving to the screen the owner buys from.

Pinned here: the numbers reach the page; a symbol outside the fetched set
renders an em dash and never a zero; the page says QUARTERLY so a filing
can never be mistaken for today's flow; a pledged promoter is visible; the
slot divider still spans the widened table; and a failure in this display
lookup can never take down /scan.
"""
from __future__ import annotations

from pathlib import Path

from web.routes import scan_routes

_TPL = Path(__file__).resolve().parents[1] / "templates" / "scan.html"


def test_real_committed_holdings_reach_the_helper():
    """Reads the committed CSV — no fixture, so a schema change is caught."""
    out = scan_routes._institutional([{"ticker": "TCS.NS"}])
    assert "TCS.NS" in out, "TCS is in the fetched shareholding set"
    row = out["TCS.NS"]
    assert row["quarter"]
    assert row["fii_pct"] is not None or row["dii_pct"] is not None


def test_symbol_outside_the_fetched_set_is_absent_not_zero():
    """A missing filing must never render as 0% institutional ownership."""
    out = scan_routes._institutional([{"ticker": "NOTAREALTICKER.NS"}])
    assert out == {}


def test_helper_survives_bad_rows_and_never_raises():
    """/scan is the screen the owner buys from — a display lookup must not
    be able to 500 it (the _pick_detail lesson, 2026-08-21)."""
    assert scan_routes._institutional(None) == {}
    assert scan_routes._institutional([{}, {"ticker": ""}, None]) == {}


def test_page_labels_the_data_quarterly():
    """The app's standing rule: stale-by-nature data is labelled, always."""
    tpl = _TPL.read_text()
    assert "institutional_cell_marker" in tpl
    assert "quarterly" in tpl.lower()
    # the tooltip must name the filing quarter, not just say "quarterly"
    assert "h.quarter" in tpl


def test_missing_row_renders_an_em_dash_with_a_reason():
    tpl = _TPL.read_text()
    cell = tpl.split("institutional_cell_marker")[1].split("</td>")[0]
    assert "&mdash;" in cell or "—" in cell
    assert "Not a zero" in cell


def test_pledge_is_surfaced():
    cell = _TPL.read_text().split("institutional_cell_marker")[1].split("</td>")[0]
    assert "promoter_pledged" in cell and "pledged" in cell


def test_slot_divider_spans_the_widened_table():
    """The funnel divider must still cover every column, or the row breaks."""
    tpl = _TPL.read_text()
    seg = tpl.split("slot_divider_marker")[1].split("</tr>")[0]
    assert "colspan=\"{% if user and user.role == 'owner' %}8{% else %}7{% endif %}\"" in seg


def test_header_and_body_column_counts_agree():
    """Count <th> in the picks table against <td> in its row."""
    tpl = _TPL.read_text()
    block = tpl.split("💺")[-1] if "💺" in tpl else tpl
    head = block.split("<thead")[-1].split("</thead>")[0]
    body = block.split("<tbody")[-1].split("</tbody>")[0]
    row = body.split("slot_divider_marker")[-1].split("</tr>")[1]
    assert head.count("<th") == row.count("<td") == 8
