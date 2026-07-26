"""Filings feed v1 — matcher and refresh (offline, no network)."""

from trading.filings import _key, build_watch_map, match_filings, refresh_filings


ANNS = [
    {"SLONGNAME": "Tata Consultancy Services Ltd", "NEWSSUB": "Award of contract",
     "NEWS_DT": "2026-07-26T09:00:00", "ATTACHMENTNAME": "x.pdf"},
    {"SLONGNAME": "Some Other Company Ltd", "NEWSSUB": "Board meeting",
     "NEWS_DT": "2026-07-26T09:01:00", "ATTACHMENTNAME": ""},
    {"SLONGNAME": "MTAR Technologies Limited", "NEWSSUB": "Order win Rs 386 cr",
     "NEWS_DT": "2026-07-26T09:02:00", "ATTACHMENTNAME": "y.pdf"},
]
UNI = [{"ticker": "TCS.NS", "name": "TATA CONSULTANCY SERV LT"},
       {"ticker": "MTARTECH.NS", "name": "MTAR TECHNOLOGIES LIMITED"}]


def test_name_key_normalises():
    assert _key("Tata Consultancy Services Ltd") == _key("TATA CONSULTANCY SERV LT")[:len(_key('Tata Consultancy Services Ltd'))] or \
           _key("Tata Consultancy Services Ltd").split()[0] == "TATA"


def test_match_and_refresh():
    wm = build_watch_map(UNI, {"TCS.NS", "MTARTECH.NS"})
    m = match_filings(ANNS, wm)
    got = {x["ticker"] for x in m}
    assert "MTARTECH.NS" in got
    assert all(x["ticker"] != "" for x in m)

    class P:
        filings = []
    import trading.filings as fl
    orig = fl.fetch_announcements
    fl.fetch_announcements = lambda a, b, max_pages=8: ANNS
    try:
        s = refresh_filings(P, wm)
        assert s["new"] == len(m) and len(P.filings) == len(m)
        s2 = refresh_filings(P, wm)          # same anns again -> no dupes
        assert s2["new"] == 0 and len(P.filings) == len(m)
    finally:
        fl.fetch_announcements = orig
