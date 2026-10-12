"""Owner item 28 (2026-10-12): "Report section: from now the first scan
after the new replacement of full 1200 share — watchlist will be captured
there with date n time and stock details (major parameters)".

web/watchlist_capture.py freezes the scan watchlist (bounded), saves it to
a bounded Mongo collection and a CSV, and /reports lists the captures with
a per-capture download and an on-page view.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from web import auth as auth_mod, db, state, watchlist_capture as wc
from web.main import app
from trading.reports import (list_watchlist_csvs, watchlist_csv_path,
                             write_watchlist_csv, WATCHLIST_CSV_KEEP)


# ----------------------------------------------------------- fixtures

def _cfg(**kw):
    base = dict(min_score_to_enter=62.0, sector_map={"A0.NS": "IT"},
                custom_universe=tuple(f"U{i}.NS" for i in range(1205)),
                state_dir="/tmp/sourceborn_paper")
    base.update(kw)
    return SimpleNamespace(**base)


def _portfolio(n_rows: int, floor: float = 62.0, above: int | None = None,
               mode: str = "nifty200_momentum", matrix_mode: str | None = None,
               with_matrix: bool = True):
    """n_rows tickers A0.. with scores descending from 90; `above` of them
    at/above the floor (default: all)."""
    above = n_rows if above is None else above
    rows, mx = {}, {}
    for i in range(n_rows):
        t = f"A{i}.NS"
        score = (90.0 - i * 0.01) if i < above else (floor - 1 - i * 0.01)
        rows[t] = {"score": round(score, 2), "abs_mom": True, "timing": i % 2 == 0,
                   "liq": True if i % 3 else None, "liq_why": "" if i % 3 else "not_judged",
                   "outcome": "" if i else "opened",
                   "c": {"momentum": 70.0 + i % 5, "quality": 60.0}}
        mx[t] = {"c": rows[t]["c"], "score": rows[t]["score"], "timing": rows[t]["timing"],
                 "abs_mom": True, "candle": "bullish_engulfing" if i % 2 else "",
                 "structures": {"hammer": bool(i % 2), "doji": False},
                 "multi": {"rows": {"am07": {"label": "PASS"},
                                    "ash08": {"label": "fail"},
                                    "ashstocks": {"label": "DATA_NEEDED"}}},
                 "outcome": ""}
    return SimpleNamespace(
        momentum_shortlist={"ts": "2026-10-12T04:05:00", "mode": mode,
                            "score_floor": floor,
                            "top": list(rows)[:50], "rows": rows},
        scan_matrix=({"ts": "2026-10-12T05:00:00", "mode": matrix_mode or mode,
                      "score_floor": floor, "rows": mx} if with_matrix else {}),
        last_scan_skipped=[{"ticker": "A1.NS", "reason": "candle_wait_no_pattern"}],
        last_scan_opened=["A0.NS"],
        positions={"A2.NS": object()},
        universe_batch={"ts": "2026-10-09T11:00:00"},
    )


NOW_UTC = datetime(2026, 10, 12, 4, 5, 12)          # 09:35:12 IST, naive = UTC


# ----------------------------------------------------------- build_snapshot

def test_clock_is_ist_and_the_key_is_the_ist_minute():
    s = wc.build_snapshot(_portfolio(3), _cfg(), NOW_UTC)
    assert s["ts"].startswith("2026-10-12T09:35:12+05:30")
    assert s["ts_key"] == "2026-10-12T0935"
    aware = datetime(2026, 10, 12, 4, 5, tzinfo=timezone.utc)
    assert wc.ts_key_for(wc.to_ist(aware)) == "2026-10-12T0935"


def test_bounds_301_rows_in_gives_300_out_all_above_floor():
    s = wc.build_snapshot(_portfolio(301), _cfg(), NOW_UTC)
    assert s["n_rows"] == 300 == len(s["rows"])
    assert all(r["score"] >= 62.0 for r in s["rows"])
    assert [r["rank"] for r in s["rows"]] == list(range(1, 301))
    assert s["rows"][0]["ticker"] == "A0.NS"          # best first


def test_every_row_at_or_above_the_floor_is_kept_below_the_cap():
    s = wc.build_snapshot(_portfolio(250, above=150), _cfg(), NOW_UTC)
    assert s["n_rows"] == 150
    assert s["n_above_floor"] == 150
    assert all(r["score"] >= 62.0 for r in s["rows"])


def test_top_100_by_score_is_kept_even_under_the_floor():
    s = wc.build_snapshot(_portfolio(400, above=20), _cfg(), NOW_UTC)
    assert s["n_rows"] == 100
    assert s["n_above_floor"] == 20
    assert sum(1 for r in s["rows"] if r["score"] < 62.0) == 80


def test_rows_carry_the_major_parameters():
    s = wc.build_snapshot(_portfolio(5), _cfg(), NOW_UTC)
    r0 = s["rows"][0]
    assert r0["ticker"] == "A0.NS" and r0["sector"] == "IT"
    assert r0["outcome"] == "opened" and r0["opened"] is True
    assert r0["components"] == {"momentum": 70.0, "quality": 60.0}
    assert r0["multi"] == {"am07": "PASS", "ash08": "fail", "ashstocks": "DATA_NEEDED"}
    assert r0["liquidity"] is None and r0["liquidity_why"] == "not_judged"
    r1 = s["rows"][1]
    assert r1["reason_if_skipped"] == "candle_wait_no_pattern"
    assert r1["candle"] == "bullish_engulfing" and r1["structures"] == {"hammer": True}
    assert r1["liquidity"] is True
    assert s["rows"][2]["held"] is True
    assert s["rows"][3]["sector"] == "Unknown"
    assert s["scan_mode"] == "nifty200_momentum" and s["floor"] == 62.0
    assert s["universe_count"] == 1205 and s["universe_rebuild"] == "2026-10-09T11:00:00"
    assert s["head"] == [f"A{i}.NS" for i in range(5)]
    assert s["source"] == "momentum_shortlist+scan_matrix"


def test_first_seen_is_carried_from_the_history_index():
    hist = {"A0.NS": "2026-10-06", "A1.NS": "2026-10-09", "A3.NS": "2027-01-01"}
    s = wc.build_snapshot(_portfolio(4), _cfg(), NOW_UTC, history_index=hist)
    by = {r["ticker"]: r["first_seen"] for r in s["rows"]}
    assert by["A0.NS"] == "2026-10-06"
    assert by["A1.NS"] == "2026-10-09"
    assert by["A2.NS"] == "2026-10-12"          # first time seen = today (IST)
    assert by["A3.NS"] == "2026-10-12"          # a future stamp is corrupt -> today


def test_history_index_takes_the_earliest_date_over_stored_captures():
    snaps = [
        {"ts": "2026-10-12T09:35:00+05:30",
         "rows": [{"ticker": "X.NS", "first_seen": "2026-10-08"},
                  {"ticker": "Y.NS"}]},
        {"ts": "2026-10-07T09:35:00+05:30",
         "rows": [{"ticker": "X.NS", "first_seen": "2026-10-06"},
                  {"ticker": "Z.NS", "first_seen": "2026-10-07"}]},
    ]
    assert wc.history_index_from(snaps) == {
        "X.NS": "2026-10-06", "Y.NS": "2026-10-12", "Z.NS": "2026-10-07"}


def test_a_different_pass_in_the_matrix_never_lends_its_scores():
    """After the full rotation scan_matrix holds the POSITIONAL pass. Its
    scores are a different formula; only the mode-independent extras
    (candle, structures, multi) may be read from it."""
    p = _portfolio(60, matrix_mode="positional")
    # give the positional matrix extra names the momentum shortlist never had
    for i in range(60, 200):
        p.scan_matrix["rows"][f"P{i}.NS"] = {"score": 99.0, "c": {}, "timing": True,
                                            "abs_mom": True, "candle": "", "structures": {},
                                            "multi": None, "outcome": ""}
    s = wc.build_snapshot(p, _cfg(), NOW_UTC)
    assert s["scan_mode"] == "nifty200_momentum"
    assert s["source"] == "momentum_shortlist+scan_matrix_extras"
    assert all(not r["ticker"].startswith("P") for r in s["rows"])
    assert s["n_rows"] == 60
    assert s["rows"][1]["candle"] == "bullish_engulfing"     # extras still ride
    # last_scan_skipped / last_scan_opened are written WITH the matrix
    # (engine.scan), so here they are the positional pass's — never borrowed.
    by = {r["ticker"]: r for r in s["rows"]}
    assert by["A1.NS"]["reason_if_skipped"] == ""            # not positional's candle_wait
    assert by["A0.NS"]["opened"] is False                    # not positional's opened list
    # the momentum pass's own skip reason (shortlist `outcome`) is used instead
    p.momentum_shortlist["rows"]["A3.NS"]["outcome"] = "breadth_band"
    s = wc.build_snapshot(p, _cfg(), NOW_UTC)
    assert {r["ticker"]: r for r in s["rows"]}["A3.NS"]["reason_if_skipped"] == "breadth_band"
    # and the missing below-floor fill is SAID, not silent
    assert "below-floor names are not in this capture" in s["note"]
    assert "positional" in s["note"]


def test_opened_is_read_off_the_book_when_the_matrix_is_another_pass():
    """A momentum buy made today, captured after the rotation: held, bought
    under momentum, entered today (naive UTC inside market hours)."""
    p = _portfolio(10, matrix_mode="positional")
    p.positions = {
        "A4.NS": SimpleNamespace(mode="nifty200_momentum",
                                 entry_date=datetime(2026, 10, 12, 3, 55)),
        "A5.NS": SimpleNamespace(mode="nifty200_momentum",
                                 entry_date=datetime(2026, 10, 9, 3, 55)),   # older
        "A6.NS": SimpleNamespace(mode="positional",
                                 entry_date=datetime(2026, 10, 12, 9, 0)),   # other category
    }
    by = {r["ticker"]: r for r in wc.build_snapshot(p, _cfg(), NOW_UTC)["rows"]}
    assert by["A4.NS"]["opened"] is True
    assert by["A5.NS"]["opened"] is False and by["A5.NS"]["held"] is True
    assert by["A6.NS"]["opened"] is False and by["A6.NS"]["held"] is True


def test_same_pass_capture_has_no_note():
    s = wc.build_snapshot(_portfolio(5), _cfg(), NOW_UTC)
    assert s["note"] == ""


def test_no_shortlist_falls_back_to_the_matrix_and_says_so():
    p = _portfolio(30, mode="positional")
    p.momentum_shortlist = {}
    s = wc.build_snapshot(p, _cfg(), NOW_UTC)
    assert s["source"] == "scan_matrix" and s["scan_mode"] == "positional"
    assert s["n_rows"] == 30
    assert s["rows"][0]["liquidity"] is None        # the matrix has no gate verdict


def test_nothing_to_capture_is_an_empty_honest_document():
    p = _portfolio(3)
    p.momentum_shortlist, p.scan_matrix = {}, {}
    s = wc.build_snapshot(p, _cfg(), NOW_UTC)
    assert s["n_rows"] == 0 and s["rows"] == [] and s["source"] == "none"


# ----------------------------------------------------------- CSV writer

def test_csv_is_one_file_per_capture_and_the_dir_keeps_sixty(tmp_path):
    s = wc.build_snapshot(_portfolio(5), _cfg(), NOW_UTC)
    path = write_watchlist_csv(s, str(tmp_path))
    assert path == watchlist_csv_path(str(tmp_path), "2026-10-12T0935")
    assert os.path.basename(path) == "watchlist_2026-10-12T0935.csv"
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 5
    assert rows[0]["ticker"] == "A0.NS" and rows[0]["scan_mode"] == "nifty200_momentum"
    assert rows[0]["snapshot_ts_ist"].startswith("2026-10-12T09:35")
    assert rows[1]["multi"] == "am07=PASS;ash08=fail;ashstocks=DATA_NEEDED"
    assert rows[1]["components"] == "momentum=71.0;quality=60.0"
    # retention on disk: 70 captures -> the newest 60 files remain
    for i in range(70):
        write_watchlist_csv({**s, "ts_key": f"2026-09-{(i % 28) + 1:02d}T{i:04d}"},
                            str(tmp_path))
    names = sorted(os.listdir(tmp_path / "watchlist"))
    assert len(names) == WATCHLIST_CSV_KEEP == 60
    assert "watchlist_2026-10-12T0935.csv" in names       # the newest key survives
    # a stray file in that dir is never touched
    (tmp_path / "watchlist" / "keep.me").write_text("x")
    write_watchlist_csv({**s, "ts_key": "2026-10-12T0936"}, str(tmp_path))
    assert (tmp_path / "watchlist" / "keep.me").exists()
    assert list_watchlist_csvs(str(tmp_path))[0]["ts_key"] == "2026-10-12T0936"


# ----------------------------------------------------------- capture()

class _FakeDB:
    """The three db functions the capture needs, as a plain object."""

    def __init__(self, fail: bool = False, prev=None):
        self.fail, self.saved, self.prev = fail, [], list(prev or [])

    def list_watchlist_snapshots(self, limit=60, fields=None):
        if self.fail:
            raise RuntimeError("mongo down")
        return self.prev[:limit]

    def save_watchlist_snapshot(self, doc):
        if self.fail:
            raise RuntimeError("quota exceeded")
        self.saved.append(doc)
        return True

    def load_watchlist_snapshot(self, ts_key):
        for d in self.saved:
            if d["ts_key"] == ts_key:
                return d
        return None


def test_capture_saves_to_mongo_and_writes_the_csv(tmp_path):
    fake = _FakeDB(prev=[{"ts": "2026-10-09T09:35:00+05:30",
                          "rows": [{"ticker": "A1.NS", "first_seen": "2026-10-01"}]}])
    out = wc.capture(_portfolio(5), _cfg(state_dir=str(tmp_path)), NOW_UTC,
                     db_module=fake)
    assert out["ok"] is True and out["mongo"] is True
    assert out["ts_key"] == "2026-10-12T0935" and out["n_rows"] == 5
    assert out["error"] is None
    assert os.path.exists(out["csv"])
    assert len(fake.saved) == 1
    by = {r["ticker"]: r["first_seen"] for r in fake.saved[0]["rows"]}
    assert by["A1.NS"] == "2026-10-01" and by["A0.NS"] == "2026-10-12"


def test_capture_returns_false_for_mongo_but_still_writes_the_csv_when_db_raises(tmp_path):
    out = wc.capture(_portfolio(5), _cfg(state_dir=str(tmp_path)), NOW_UTC,
                     db_module=_FakeDB(fail=True))
    assert out["mongo"] is False
    assert out["ok"] is True                       # the CSV landed
    assert out["csv"] and os.path.exists(out["csv"])
    assert "CSV only" in out["note"]
    assert "quota exceeded" in (out["error"] or "")
    assert "first_seen = today" in out["note"]      # history read failed, said so


def test_capture_never_raises_into_the_caller(tmp_path, monkeypatch):
    broken = SimpleNamespace(momentum_shortlist=object(), scan_matrix=None,
                             last_scan_skipped=None, last_scan_opened=None,
                             positions=None, universe_batch=None)
    out = wc.capture(broken, _cfg(state_dir=str(tmp_path)), NOW_UTC,
                     db_module=_FakeDB())
    assert out["ok"] is False and out["error"].startswith("build:")
    # and an empty scan record is a quiet no-op, not a failure
    p = _portfolio(3)
    p.momentum_shortlist, p.scan_matrix = {}, {}
    out = wc.capture(p, _cfg(state_dir=str(tmp_path)), NOW_UTC, db_module=_FakeDB())
    assert out["ok"] is False and out["n_rows"] == 0 and "nothing to capture" in out["note"]


def test_capture_signature_is_what_the_scheduler_calls():
    import inspect
    params = list(inspect.signature(wc.capture).parameters)
    assert params == ["portfolio", "cfg", "now", "db_module", "state_dir"]


# ----------------------------------------------------------- db retention

class _Cur(list):
    def sort(self, key, direction):
        return _Cur(sorted(self, key=lambda d: d.get(key) or "",
                           reverse=direction < 0))

    def skip(self, n):
        return _Cur(self[n:])

    def limit(self, n):
        return _Cur(self[:n])


class _Col:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def replace_one(self, flt, doc, upsert=False):
        self.docs[flt["_id"]] = dict(doc)

    def find(self, flt=None, proj=None):
        return _Cur([dict(d) for d in self.docs.values()])

    def find_one(self, flt):
        d = self.docs.get(flt["_id"])
        return dict(d) if d else None

    def delete_many(self, flt):
        for k in flt["_id"]["$in"]:
            self.docs.pop(k, None)


def test_mongo_retention_trims_to_the_newest_120(monkeypatch):
    col = _Col()
    monkeypatch.setattr(db, "_DB", SimpleNamespace(watchlist_snapshots=col))
    monkeypatch.setattr(db, "_CONNECTED", True)
    for i in range(125):
        day = i // 2 + 1
        key = f"2026-{(day - 1) // 28 + 1:02d}-{(day - 1) % 28 + 1:02d}T{i % 2:02d}35"
        assert db.save_watchlist_snapshot({"ts_key": key, "ts": key, "rows": []}) is True
    assert len(col.docs) == db.WATCHLIST_SNAPSHOT_KEEP == 120
    keys = sorted(col.docs)
    assert keys[-1] == max(keys)                     # newest kept
    assert db.list_watchlist_snapshots(limit=3)[0]["ts_key"] == keys[-1]
    assert db.load_watchlist_snapshot(keys[0])["ts_key"] == keys[0]
    assert db.load_watchlist_snapshot("nope") is None
    # keyed by ts: a second save in the same minute replaces, never duplicates
    db.save_watchlist_snapshot({"ts_key": keys[-1], "ts": keys[-1], "rows": [1]})
    assert len(col.docs) == 120
    assert "_id" not in db.list_watchlist_snapshots(limit=1)[0]


def test_db_functions_are_quiet_when_mongo_is_down(monkeypatch):
    monkeypatch.setattr(db, "_CONNECTED", False)
    assert db.save_watchlist_snapshot({"ts_key": "2026-10-12T0935"}) is False
    assert db.list_watchlist_snapshots() == []
    assert db.load_watchlist_snapshot("2026-10-12T0935") is None
    assert db.save_watchlist_snapshot({}) is False


# ----------------------------------------------------------- /reports

def _client() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code in (200, 303)
    return c


def test_reports_lists_captures_from_mongo_with_download_and_on_page_view(monkeypatch, tmp_path):
    snap = wc.build_snapshot(_portfolio(12), _cfg(), NOW_UTC)
    summary = {k: snap[k] for k in ("ts", "ts_key", "scan_mode", "floor", "n_rows",
                                    "n_above_floor", "head", "source", "universe_count")}
    monkeypatch.setattr(db, "list_watchlist_snapshots",
                        lambda limit=60, fields=None: [summary])
    monkeypatch.setattr(db, "load_watchlist_snapshot",
                        lambda ts_key: snap if ts_key == snap["ts_key"] else None)
    monkeypatch.setattr(state.cfg(), "state_dir", str(tmp_path))
    c = _client()
    html = c.get("/reports").text
    assert "watchlist_captures_marker" in html
    assert 'data-watchlist-key="2026-10-12T0935"' in html
    assert "2026-10-12 09:35 IST" in html
    assert "A0.NS, A1.NS, A2.NS, A3.NS, A4.NS, A5.NS, A6.NS, A7.NS" in html
    assert "/reports/watchlist/2026-10-12T0935.csv" in html
    assert "watchlist_show_marker" not in html            # collapsed by default
    # on-page expand
    html = c.get("/reports?show=2026-10-12T0935").text
    assert "watchlist_show_marker" in html
    assert html.count('data-watchlist-row="') == 12
    assert 'data-watchlist-row="A2.NS"' in html and "HELD" in html
    assert "candle_wait_no_pattern" in html
    assert "scan universe 1205 names" in html
    # download regenerates the CSV from Mongo when the file is gone
    assert not os.path.exists(watchlist_csv_path(str(tmp_path), "2026-10-12T0935"))
    r = c.get("/reports/watchlist/2026-10-12T0935.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "A11.NS" in r.text and "snapshot_ts_ist" in r.text
    assert os.path.exists(watchlist_csv_path(str(tmp_path), "2026-10-12T0935"))


def test_reports_shows_the_capture_note_on_the_expanded_view(monkeypatch, tmp_path):
    snap = wc.build_snapshot(_portfolio(8, matrix_mode="positional"), _cfg(), NOW_UTC)
    assert snap["note"]
    monkeypatch.setattr(db, "list_watchlist_snapshots",
                        lambda limit=60, fields=None: [{k: snap[k] for k in (
                            "ts", "ts_key", "scan_mode", "floor", "n_rows", "head")}])
    monkeypatch.setattr(db, "load_watchlist_snapshot",
                        lambda ts_key: snap if ts_key == snap["ts_key"] else None)
    monkeypatch.setattr(state.cfg(), "state_dir", str(tmp_path))
    html = _client().get("/reports?show=2026-10-12T0935").text
    assert "data-watchlist-note" in html
    assert "below-floor names are not in this capture" in html


def test_reports_falls_back_to_the_csv_on_disk_when_mongo_has_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "list_watchlist_snapshots", lambda limit=60, fields=None: [])
    monkeypatch.setattr(db, "load_watchlist_snapshot", lambda ts_key: None)
    monkeypatch.setattr(state.cfg(), "state_dir", str(tmp_path))
    snap = wc.build_snapshot(_portfolio(4), _cfg(), NOW_UTC)
    write_watchlist_csv(snap, str(tmp_path))
    c = _client()
    html = c.get("/reports?show=2026-10-12T0935").text
    assert 'data-watchlist-key="2026-10-12T0935"' in html and "(file)" in html
    assert html.count('data-watchlist-row="') == 4
    r = c.get("/reports/watchlist/2026-10-12T0935.csv")
    assert r.status_code == 200 and "A3.NS" in r.text


def test_reports_page_is_whole_with_no_capture_and_refuses_a_bad_key(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "list_watchlist_snapshots", lambda limit=60, fields=None: [])
    monkeypatch.setattr(db, "load_watchlist_snapshot", lambda ts_key: None)
    monkeypatch.setattr(state.cfg(), "state_dir", str(tmp_path))
    c = _client()
    r = c.get("/reports")
    assert r.status_code == 200
    assert "No capture yet" in r.text and "DATA_NEEDED" in r.text
    assert "Scan history" in r.text or "Reports" in r.text     # the old page survives
    r = c.get("/reports/watchlist/..%2F..%2Fetc%2Fpasswd.csv", follow_redirects=False)
    assert r.status_code in (303, 404)
    r = c.get("/reports/watchlist/2026-10-12T0935.csv", follow_redirects=False)
    assert r.status_code == 303 and "missing" in r.headers["location"]
