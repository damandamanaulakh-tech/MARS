"""FII/DII flows must never present a placeholder or a stale row as today's
flow (owner: 'FII 5-day net — why its not wired yet?', 2026-07-26)."""

from datetime import date, timedelta

from trading.signals import flows as fl


def _row(d, fii=0.0, dii=0.0, buy=0.0, sell=0.0):
    return fl.FlowReading(trade_date=d, fii_buy=buy, fii_sell=sell,
                          fii_net=fii, dii_buy=buy, dii_sell=sell, dii_net=dii)


def test_all_zero_live_rows_are_dropped(monkeypatch):
    """NSE answers with 0.0 placeholder rows before publishing — those must
    not look like a real flat market, or they hide the CSV fallback."""
    import httpx

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return [{"date": "07-Aug-2026", "fiibyVal": 0, "fiisellVal": 0,
                     "diibyVal": 0, "diisellVal": 0}]

    class FakeClient:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    assert fl.fetch_latest_flows() is None       # placeholder != data


def test_stale_flows_are_data_needed(monkeypatch):
    old = date.today() - timedelta(days=700)
    monkeypatch.setattr(fl, "fetch_latest_flows", lambda: None)
    monkeypatch.setattr(fl, "fetch_flows_from_csv", lambda path=None: [_row(old, fii=-500)])
    assert fl.get_flows() is None                # never used for decisions
    st = fl.flow_status()
    assert st["state"] == "STALE" and st["fii_5d_net"] is None
    assert st["age_days"] >= 699


def test_fresh_flows_are_used(monkeypatch):
    today = date.today()
    rows = [_row(today - timedelta(days=i), fii=-100.0 * (i + 1)) for i in range(5)]
    monkeypatch.setattr(fl, "fetch_latest_flows", lambda: rows)
    assert fl.get_flows() is not None
    st = fl.flow_status()
    assert st["state"] == "LIVE"
    assert st["fii_5d_net"] == -1500.0            # 100+200+300+400+500
