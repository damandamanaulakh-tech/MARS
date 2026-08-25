"""Owner order 2026-08-24 ("with upstox key u can wire the FII data") —
the daily Upstox market FII/DII pull.

Pinned: the broker methods call the real endpoints with bearer auth (and
accept UPSTOX_ANALYTICS_TOKEN when no login token exists); the mappers
are defensive — a row enters the store only when it proves a date plus
the index-futures legs (FII) or non-zero buy/sell (DII); the job journals
a skip without auth and a snapshot with counts, once per day; the PWOI
frame extension appends only past the committed panel and degrades to it
on any failure; FII-04/05 notes name the Upstox source when it decided,
and FII-06/07 name their stale legs on a mixed frame.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from trading import data_refresh as dr
from web import state


FII_PAYLOAD = {
    "NSE_FO|INDEX_FUTURES": [
        {"date": "2026-08-22", "long_contracts": 60_000,
         "short_contracts": 540_000, "buy_amount": 1.0, "sell_amount": 2.0},
        {"date": "2026-08-24", "long_contracts": 55_000,
         "short_contracts": 500_000},
    ],
    "NSE_FO|STOCK_FUTURES": [
        {"date": "2026-08-24", "long_contracts": 1_500_000,
         "short_contracts": 300_000},
    ],
}
DII_PAYLOAD = [
    {"date": "2026-08-24", "buy_amount": 12621.89, "sell_amount": 11699.63},
    {"date": "2026-08-23", "buy_amount": 0, "sell_amount": 0},   # refused
]


def test_map_market_fii_builds_pwoi_shaped_rows():
    rows = dr.map_market_fii(FII_PAYLOAD)
    assert [r["date"] for r in rows] == ["2026-08-22", "2026-08-24"]
    r = rows[-1]
    assert r["client_type"] == "FII"
    assert r["fut_idx_long"] == 55_000 and r["fut_idx_short"] == 500_000
    assert r["fut_stk_long"] == 1_500_000
    assert r["source"] == "upstox_market_fii"
    # a payload missing the index-futures legs yields nothing
    assert dr.map_market_fii({"NSE_FO|STOCK_FUTURES":
                              FII_PAYLOAD["NSE_FO|STOCK_FUTURES"]}) == []
    assert dr.map_market_fii({"x": [{"long_contracts": 1}]}) == []


def test_map_market_dii_refuses_all_zero_rows():
    rows = dr.map_market_dii(DII_PAYLOAD)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-08-24"
    assert round(rows[0]["dii_net"], 2) == 922.26


def test_broker_methods_send_bearer_and_params(monkeypatch):
    from trading.brokers.upstox import UpstoxClient
    calls = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"ok": True}}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls[url] = (params, headers)
        return _Resp()

    monkeypatch.setattr("trading.brokers.upstox.httpx.get", fake_get)
    c = UpstoxClient(api_key="k", api_secret="s", redirect_uri="r",
                     access_token="tok123")
    assert c.get_market_fii(["NSE_FO|INDEX_FUTURES"]) == {"ok": True}
    params, headers = calls["https://api.upstox.com/v2/market/fii"]
    assert ("data_type", "NSE_FO|INDEX_FUTURES") in params
    assert ("interval", "1D") in params
    assert "tok123" in headers.get("Authorization", "")
    assert c.get_market_dii() == {"ok": True}
    assert "https://api.upstox.com/v2/market/dii" in calls


def test_analytics_token_env_works_without_login(monkeypatch):
    from trading.brokers.upstox import UpstoxClient
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "analytics-abc")
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": []}

    monkeypatch.setattr(
        "trading.brokers.upstox.httpx.get",
        lambda url, params=None, headers=None, timeout=None:
        seen.update(h=headers) or _Resp())
    c = UpstoxClient(api_key="k", api_secret="s", redirect_uri="r")
    c.get_market_fii(["NSE_FO|INDEX_FUTURES"])
    assert "analytics-abc" in seen["h"]["Authorization"]


def test_snapshot_job_stores_and_journals(monkeypatch):
    from web import scheduler
    stored = {}
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.load_settings", lambda doc_id="default": {})
    monkeypatch.setattr("web.db.save_settings",
                        lambda d, doc_id="default":
                        stored.setdefault("marks", []).append(d))
    monkeypatch.setattr("web.db.save_market_info_raw",
                        lambda k, day, p: stored.update({f"raw_{k}": p}))
    monkeypatch.setattr("web.db.save_pwoi_rows",
                        lambda rows: stored.update(pwoi=rows) or len(rows))
    monkeypatch.setattr("web.db.update_dii_rows",
                        lambda rows: stored.update(dii=rows) or len(rows))

    class _Broker:
        access_token = "tok"

        def is_connected(self):
            return True

        def get_market_fii(self, dts, interval="1D"):
            return FII_PAYLOAD

        def get_market_dii(self, interval="1D"):
            return DII_PAYLOAD

    monkeypatch.setattr(state.engine(), "broker", _Broker(), raising=False)
    p = state.portfolio()
    n0 = len(p.decision_journal)
    assert scheduler.run_market_fii_snapshot("test")
    acts = [e.get("action") for e in p.decision_journal[n0:]]
    assert "MARKET_FII_SNAPSHOT" in acts
    assert stored["pwoi"] and stored["dii"]
    assert any("market_fii_day" in m for m in stored["marks"])


def test_snapshot_job_without_auth_journals_a_skip(monkeypatch):
    from web import scheduler
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.load_settings", lambda doc_id="default": {})
    marks = []
    monkeypatch.setattr("web.db.save_settings",
                        lambda d, doc_id="default": marks.append(d))
    monkeypatch.setattr(state.engine(), "broker", None, raising=False)
    monkeypatch.setattr("trading.data.history_broker", lambda: None)
    p = state.portfolio()
    n0 = len(p.decision_journal)
    assert not scheduler.run_market_fii_snapshot("test")
    acts = [e.get("action") for e in p.decision_journal[n0:]]
    assert "MARKET_FII_SKIPPED" in acts


def test_extend_pwoi_appends_only_past_committed():
    df = pd.DataFrame([
        {"date": "2026-08-06", "client_type": "FII",
         "fut_idx_long": 1.0, "fut_idx_short": 2.0},
        {"date": "2026-08-07", "client_type": "Client",
         "fut_idx_long": 3.0, "fut_idx_short": 4.0},
    ])
    old = dr._pwoi_reader
    dr.set_pwoi_reader(lambda: [
        {"date": "2026-08-07", "client_type": "FII",
         "fut_idx_long": 9.0, "fut_idx_short": 9.0},      # dup date: refused
        {"date": "2026-08-24", "client_type": "FII",
         "fut_idx_long": 55_000.0, "fut_idx_short": 500_000.0},
    ])
    try:
        out, n = dr.extend_pwoi_df(df)
        assert n == 1 and len(out) == 3
        assert str(out["date"].max()) == "2026-08-24"
    finally:
        dr._pwoi_reader = old


def test_register_notes_name_the_upstox_source_and_the_stale_legs():
    import trading.register_v4 as rv
    rows = []
    for i in range(70):
        d = (pd.Timestamp("2026-04-01") + pd.Timedelta(days=i)).date().isoformat()
        for ct in ("FII", "Client", "TOTAL"):
            rows.append({"date": d, "client_type": ct,
                         "fut_idx_long": 100.0 + i, "fut_idx_short": 90.0,
                         "opt_idx_call_long": 10.0, "opt_idx_put_long": 9.0,
                         "opt_idx_call_short": 8.0, "opt_idx_put_short": 7.0})
    pwoi = pd.DataFrame(rows)
    ext = pd.DataFrame([{"date": "2026-08-24", "client_type": "FII",
                         "fut_idx_long": 50.0, "fut_idx_short": 500.0,
                         "source": "upstox_market_fii"}])
    pwoi = pd.concat([pwoi, ext], ignore_index=True).sort_values("date")
    mv = rv.market_view({}, pwoi=pwoi)
    assert "[upstox market/fii to 2026-08-24]" in mv["FII-04"].note
    assert "client/total legs end" in mv["FII-06"].note
