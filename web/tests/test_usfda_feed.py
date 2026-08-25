"""Owner ask 2026-08-25 ("did u wired the US FDA data and on going
senctions") — the real USFDA wire, replacing the fabricated calendar the
2026-08-09 audit emptied.

Pinned: name-token matching (query token, firm match, and that a wrong
firm never attaches silently — the firm name rides every row); the
recalls fetcher parses openFDA rows and refuses non-matching firms; the
inspections half without the owner's free FDA key reads DATA_NEEDED with
the unlock named; the weekly job scans pharma/chem/health universe names
plus every held name, stores and journals; Mongo-down journals a skip;
the news lexicon now tags USFDA and sanction headlines as regulatory
risk; and the money-path boundary holds — engine/risk/orders/danger
never import the feed, and no USFDA row gates anything.
"""
from __future__ import annotations

from pathlib import Path

from trading import usfda_feed
from web import state


def test_query_token_and_firm_match():
    assert usfda_feed.query_token("TORRENT PHARMACEUTICALS L") == "TORRENT"
    assert usfda_feed.query_token("IOL CHEM AND PHARMA LTD") == "IOL"
    assert usfda_feed.query_token("THE INDIA CO") == ""
    assert usfda_feed.firm_matches("Torrent Pharma Inc.", "TORRENT")
    assert not usfda_feed.firm_matches("Sun Pharmaceutical", "TORRENT")
    assert not usfda_feed.firm_matches("Anything", "")


def test_fetch_recalls_parses_and_refuses_wrong_firms(monkeypatch):
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [
                {"recalling_firm": "Torrent Pharma Inc.",
                 "recall_number": "D-1", "report_date": "20250611",
                 "classification": "Class II",
                 "reason_for_recall": "CGMP deviations", "status": "Ongoing"},
                {"recalling_firm": "Unrelated Labs LLC",
                 "recall_number": "D-2", "report_date": "20250101",
                 "classification": "Class I", "reason_for_recall": "x"},
            ]}

    monkeypatch.setattr(
        "trading.usfda_feed.httpx.get",
        lambda url, params=None, timeout=None: _Resp())
    rows = usfda_feed.fetch_recalls(
        {"TORNTPHARM.NS": "TORRENT PHARMACEUTICALS L"}, sleep_s=0)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "TORNTPHARM.NS" and r["event_id"] == "D-1"
    assert r["classification"] == "Class II"
    assert r["firm"] == "Torrent Pharma Inc."     # match always visible


def test_inspections_without_key_read_data_needed(monkeypatch):
    monkeypatch.delenv("FDA_DATADASHBOARD_USER", raising=False)
    monkeypatch.delenv("FDA_DATADASHBOARD_KEY", raising=False)
    rows, note = usfda_feed.fetch_inspections({"X.NS": "X LTD"})
    assert rows == []
    assert "DATA_NEEDED" in note and "FDA_DATADASHBOARD_USER" in note


def test_weekly_job_scans_pharma_and_held_and_journals(monkeypatch):
    from web import scheduler
    seen = {}
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr(
        "trading.usfda_feed.fetch_recalls",
        lambda nm, **k: seen.update(names=sorted(nm)) or [
            {"ticker": t, "event_id": "E1", "firm": "F", "date": "20250101",
             "classification": "Class II", "reason": "r", "status": "",
             "source": "openfda_enforcement"} for t in nm])
    monkeypatch.setattr("trading.usfda_feed.fetch_inspections",
                        lambda nm, **k: ([], "DATA_NEEDED — key missing"))
    monkeypatch.setattr("web.db.save_usfda_events",
                        lambda rows: seen.update(stored=rows) or len(rows))
    cfg = state.cfg()
    old_u = cfg.custom_universe
    cfg.custom_universe = ("IOLCP.NS", "HFCL.NS")   # pharma + telecom
    p = state.portfolio()
    n0 = len(p.decision_journal)
    try:
        scheduler._weekly_usfda_refresh()
    finally:
        cfg.custom_universe = old_u
    assert "IOLCP.NS" in seen["names"]           # pharma name scanned
    assert "HFCL.NS" not in seen["names"]        # non-pharma filtered out
    acts = [e.get("action") for e in p.decision_journal[n0:]]
    assert "DATA_REFRESH_USFDA" in acts
    why = next(e["why"] for e in p.decision_journal[n0:]
               if e.get("action") == "DATA_REFRESH_USFDA")
    assert "DATA_NEEDED" in why                  # inspections honesty


def test_weekly_job_mongo_down_journals_a_skip(monkeypatch):
    from web import scheduler
    monkeypatch.setattr("web.db.is_connected", lambda: False)
    p = state.portfolio()
    n0 = len(p.decision_journal)
    scheduler._weekly_usfda_refresh()
    assert "DATA_REFRESH_USFDA_SKIPPED" in [
        e.get("action") for e in p.decision_journal[n0:]]


def test_news_lexicon_tags_usfda_and_sanctions():
    from trading.news_feed import classify_headline
    for title in ("US FDA issues warning letter to XYZ Pharma unit",
                  "XYZ gets Form 483 with five observations",
                  "US sanctions supplier network tied to XYZ"):
        tags, side = classify_headline(title)[:2]
        assert "regulatory" in tags, title
        assert side == "RISK", title
    # the fraud-detection lookahead must survive the new patterns
    tags = classify_headline(
        "RBI lists 5 ways AI can transform fraud detection in banking")[0]
    assert "governance" not in tags and "regulatory" not in tags


def test_money_path_never_imports_the_feed():
    root = Path(__file__).resolve().parents[2]
    for mod in ("engine", "risk", "orders", "danger"):
        src = (root / "trading" / f"{mod}.py").read_text()
        src = "\n".join(ln for ln in src.splitlines()
                        if not ln.lstrip().startswith("#"))
        assert "usfda" not in src, mod
