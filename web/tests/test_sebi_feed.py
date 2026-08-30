"""Owner order 2026-08-25 ("wire the SEBI enforcement orders feed too").

Pinned: the listing parser reads SEBI's real row markup (fixture copied
from a live response); title matching is EXACT-word (stricter than the
a prefix rule — SEBI titles are free prose over every sector) and
the matched token rides every row; the weekly job scans ALL universe +
held names, stores and journals with the scanned count (absence = dated
evidence); Mongo-down journals a skip; the money-path boundary holds.
"""
from __future__ import annotations

from pathlib import Path

from trading import sebi_feed
from web import state

# verbatim shape from the live endpoint (2026-08-25 probe)
_ROW_HTML = (
    "<table class='table'><thead><tr role='row'><th>Date</th><th>Title</th>"
    "</tr></thead>"
    "<tr role='row' class='odd'><td>Aug 18, 2026</td><td>"
    "<a href='https://www.sebi.gov.in/enforcement/orders/aug-2026/"
    "final-order-in-the-matter-of-rain-industries_103720.html' "
    "target=\"_blank\" title=\"x\" class='points'>"
    "Final Order in the matter of Rain Industries Limited</a></td></tr>"
    "<tr role='row' class='odd'><td>Aug 14, 2026</td><td>"
    "<a href='https://www.sebi.gov.in/enforcement/orders/aug-2026/"
    "appeal-no-6992_103665.html' target=\"_blank\" class='points'>"
    "Appeal No. 6992 of 2026 filed by Rakesh Mohan Rai</a></td></tr>"
    "</table>#@#<li>breadcrumb</li>")


def test_parser_reads_the_real_row_markup(monkeypatch):
    class _Resp:
        text = _ROW_HTML

        def raise_for_status(self):
            pass

    monkeypatch.setattr("trading.sebi_feed.httpx.post",
                        lambda url, data=None, headers=None, timeout=None:
                        _Resp())
    orders = sebi_feed.fetch_orders(pages=1, sleep_s=0)
    assert len(orders) == 2
    assert orders[0] == {
        "order_id": "103720", "date": "2026-08-18",
        "title": "Final Order in the matter of Rain Industries Limited",
        "url": "https://www.sebi.gov.in/enforcement/orders/aug-2026/"
               "final-order-in-the-matter-of-rain-industries_103720.html"}


def test_title_matching_is_exact_word_and_token_is_visible():
    assert sebi_feed.title_matches(
        "Final Order in the matter of Rain Industries Limited", "RAIN")
    # prefix must NOT match — "Rainbow" is not "Rain"
    assert not sebi_feed.title_matches(
        "Order in the matter of Rainbow Foundations", "RAIN")
    assert not sebi_feed.title_matches("Anything", "")
    matched = sebi_feed.match_orders(
        [{"order_id": "1", "date": "2026-08-18", "url": "u",
          "title": "Final Order in the matter of Rain Industries Limited"}],
        {"RAIN.NS": "RAIN INDUSTRIES LIMITED",
         "HFCL.NS": "HFCL LIMITED"})
    assert len(matched) == 1
    assert matched[0]["ticker"] == "RAIN.NS"
    assert matched[0]["token"] == "RAIN"          # wrong matches stay visible


def test_weekly_job_scans_all_names_and_journals(monkeypatch):
    from web import scheduler
    seen = {}
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr(
        "trading.sebi_feed.fetch_orders",
        lambda **k: [{"order_id": "9", "date": "2026-08-18", "url": "u",
                      "title": "Final Order in the matter of IOL Chem"}])
    monkeypatch.setattr("web.db.save_sebi_orders",
                        lambda rows: seen.update(stored=rows) or len(rows))
    cfg = state.cfg()
    old_u = cfg.custom_universe
    cfg.custom_universe = ("IOLCP.NS", "HFCL.NS")
    p = state.portfolio()
    n0 = len(p.decision_journal)
    try:
        scheduler._weekly_sebi_refresh()
    finally:
        cfg.custom_universe = old_u
    acts = [e.get("action") for e in p.decision_journal[n0:]]
    assert "DATA_REFRESH_SEBI" in acts
    why = next(e["why"] for e in p.decision_journal[n0:]
               if e.get("action") == "DATA_REFRESH_SEBI")
    assert "1 order(s) scanned" in why            # absence = dated evidence
    assert seen["stored"][0]["ticker"] == "IOLCP.NS"


def test_weekly_job_mongo_down_journals_a_skip(monkeypatch):
    from web import scheduler
    monkeypatch.setattr("web.db.is_connected", lambda: False)
    p = state.portfolio()
    n0 = len(p.decision_journal)
    scheduler._weekly_sebi_refresh()
    assert "DATA_REFRESH_SEBI_SKIPPED" in [
        e.get("action") for e in p.decision_journal[n0:]]


def test_money_path_never_imports_the_feed():
    root = Path(__file__).resolve().parents[2]
    for mod in ("engine", "risk", "orders", "danger"):
        src = (root / "trading" / f"{mod}.py").read_text()
        src = "\n".join(ln for ln in src.splitlines()
                        if not ln.lstrip().startswith("#"))
        assert "sebi_feed" not in src, mod
