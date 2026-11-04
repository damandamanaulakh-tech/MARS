"""FII refresh must preserve the independently observed DII leg."""
from types import SimpleNamespace

import pytest

from trading.signals import flows
from web import db


class _Cursor(list):
    def sort(self, field, direction):
        return _Cursor(sorted(self, key=lambda r: r.get(field, ""), reverse=direction < 0))


class _Collection:
    def __init__(self):
        self.rows = {}
        self.latest_calls = []
        self.fail_fii = False

    def update_one(self, query, update, upsert=False):
        assert upsert
        assert set(update) == {"$set"}
        doc = self.rows.setdefault(query["_id"], {"_id": query["_id"]})
        doc.update(update["$set"])
        return SimpleNamespace(acknowledged=True)

    def find(self, query):
        return _Cursor(dict(r) for r in self.rows.values())

    def find_one(self, query, projection, sort):
        self.latest_calls.append((query, projection, sort))
        field = next(k for k in query if k.endswith("_net"))
        if field == "fii_net" and self.fail_fii:
            raise RuntimeError("fixture read unavailable")
        assert query[field] == {"$type": "number"}
        assert query["date"] == {"$exists": True}
        assert sort == [("date", -1)]
        rows = [r for r in self.rows.values() if isinstance(r.get(field), (int, float))]
        if not rows:
            return None
        row = max(rows, key=lambda r: r["date"])
        return {k: row[k] for k, show in projection.items() if show and k in row}


@pytest.fixture
def collection(monkeypatch):
    coll = _Collection()
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", SimpleNamespace(fii_daily=coll))
    monkeypatch.setattr(flows, "_STORE_READER", db.load_fii_rows)
    return coll


def test_nsdl_refresh_preserves_dii_and_both_legs_reach_consumer(collection, monkeypatch):
    assert db.update_dii_rows([{"date": "2026-11-03", "dii_buy": 12000,
                               "dii_sell": 11000, "dii_net": 1000}]) == 1
    assert db.save_fii_rows([("2026-11-03", 8000, 8500, -500)]) == 1
    # A later NSDL correction also preserves independently stored DII.
    assert db.save_fii_rows([("2026-11-03", 8000, 8400, -400)]) == 1
    stored = collection.rows["2026-11-03"]
    assert stored["dii_net"] == 1000 and stored["dii_source"] == "upstox_market_dii"
    assert stored["fii_net"] == -400 and stored["source"] == "nsdl_settlement"
    monkeypatch.setattr(flows, "fetch_flows_from_csv", lambda: None)
    monkeypatch.setattr(flows, "fetch_latest_flows", lambda: None)
    rows = flows.get_flows(max_age_days=0)
    assert len(rows) == 1
    assert (rows[0].dii_buy, rows[0].dii_sell, rows[0].dii_net) == (12000, 11000, 1000)
    assert flows.dii_net_last_n_days(rows) == 1000
    assert flows.fii_net_last_n_days(rows) == -400
    assert flows.fii_flow_score(flows.fii_net_last_n_days(rows)) == flows.fii_flow_score(-400)


def test_latest_fii_and_dii_have_independent_dates_without_fii_invention(collection):
    db.save_fii_rows([("2026-11-02", 8000, 8400, -400)])
    db.update_dii_rows([{"date": "2026-11-03", "dii_buy": 12000,
                         "dii_sell": 11000, "dii_net": 1000}])
    latest = db.latest_cash_flow_resources()
    assert latest["fii"]["date"] == "2026-11-02"
    assert latest["dii"]["date"] == "2026-11-03"
    assert len(collection.latest_calls) == 2
    assert "_id" not in latest["fii"] and "fii_net" not in latest["dii"]
    # DII-only dates are shown by their own source card, never inserted as
    # zero-flow observations into the FII strategy's sample.
    rows = flows.fetch_flows_from_store()
    assert len(rows) == 1 and rows[0].trade_date.isoformat() == "2026-11-02"
    assert rows[0].fii_net == -400
    assert rows[0].dii_net is None


def test_failed_latest_read_does_not_claim_data_or_hide_other_leg(collection):
    db.update_dii_rows([{"date": "2026-11-03", "dii_buy": 12000,
                         "dii_sell": 11000, "dii_net": 1000}])
    collection.fail_fii = True
    latest = db.latest_cash_flow_resources()
    assert latest["fii"] == {} and latest["dii"]["dii_net"] == 1000


def test_offline_latest_read_is_unavailable(monkeypatch):
    monkeypatch.setattr(db, "_CONNECTED", False)
    assert db.latest_cash_flow_resources() == {"fii": {}, "dii": {}}


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "not a number"])
def test_missing_or_invalid_dii_is_unknown_without_changing_fii(collection, value):
    db.save_fii_rows([("2026-11-03", 8000, 8400, -400)])
    collection.rows["2026-11-03"]["dii_net"] = value
    rows = flows.fetch_flows_from_store()
    assert len(rows) == 1 and rows[0].fii_net == -400
    assert rows[0].dii_net is None
    assert flows.dii_net_last_n_days(rows) is None


def test_observed_zero_dii_is_kept_and_partial_window_is_not_reported_as_complete(collection):
    db.save_fii_rows([("2026-11-03", 8000, 8400, -400), ("2026-11-02", 9000, 8500, 500)])
    db.update_dii_rows([{"date": "2026-11-03", "dii_buy": 12000,
                         "dii_sell": 12000, "dii_net": 0}])
    rows = flows.fetch_flows_from_store()
    assert flows.dii_net_last_n_days(rows, 1) == 0
    assert flows.dii_net_last_n_days(rows, 2) is None
    assert flows.fii_net_last_n_days(rows, 2) == 100
