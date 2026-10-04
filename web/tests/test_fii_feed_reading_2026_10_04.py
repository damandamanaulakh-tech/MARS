"""Owner order 2026-10-04: "fix the FII feed reading too".

The Upstox market FII/DII feed had been ARRIVING (the raw FII payload was on
/healthz `market_info_shape`) but never READ: the mapper looked for a string
date while Upstox sends `time_stamp` in milliseconds, so every row was
dropped; and the DII call left out its required data_type, so Upstox refused
it outright. The old tests passed because their fixtures used a payload shape
nobody sends — these use the REAL shape (live /healthz + the API docs).
"""
from __future__ import annotations

from trading import data_refresh as dr

# 2026-09-30 / 2026-10-01 as Upstox stamps them: IST midnight, in ms.
TS_0930 = 1790706600000          # 2026-09-29T18:30Z = 2026-09-30 00:00 IST
TS_1001 = 1790793000000          # 2026-10-01 00:00 IST


def _row(ts, long_=0, short=0, cl=0, pl=0, cs=0, ps=0):
    return {"time_stamp": ts, "buy_amount": 23109.75, "sell_amount": 24642.52,
            "buy_contracts": 353981, "sell_contracts": 384079,
            "oi_contracts": 7245154, "oi_amount": 452650.0,
            "total_long_contracts": long_, "total_short_contracts": short,
            "total_call_long_contracts": cl, "total_put_long_contracts": pl,
            "total_call_short_contracts": cs, "total_put_short_contracts": ps}


# Newest first, exactly as the docs say the list is ordered.
LIVE_SHAPE = {
    "NSE_FO|INDEX_FUTURES": [_row(TS_1001, 26129, 176187),
                             _row(TS_0930, 27655, 172773)],
    "NSE_FO|STOCK_FUTURES": [_row(TS_1001, 3518604, 2885070),
                             _row(TS_0930, 3520556, 2881392)],
    "NSE_FO|INDEX_OPTIONS": [_row(TS_1001, 1427242, 1225861,
                                  586358, 840884, 799389, 426472)],
    "NSE_FO|STOCK_OPTIONS": [_row(TS_1001, 476620, 479347,
                                  178516, 298104, 313571, 165776)],
}


def test_time_stamp_millis_reads_as_the_ist_trading_date():
    assert dr._row_date({"time_stamp": TS_1001}) == "2026-10-01"
    # the docs' own example: 2026-04-29T18:30Z must be the 30th, not the 29th
    assert dr._row_date({"time_stamp": 1777487400000}) == "2026-04-30"
    assert dr._row_date({"time_stamp": TS_1001 // 1000}) == "2026-10-01"
    assert dr._row_date({"time_stamp": None, "date": "2026-08-24"}) \
        == "2026-08-24"
    assert dr._row_date({"time_stamp": "junk"}) == ""


def test_the_live_payload_shape_now_produces_rows():
    rows = dr.map_market_fii(LIVE_SHAPE)
    assert [r["date"] for r in rows] == ["2026-09-30", "2026-10-01"]
    r = rows[-1]
    assert r["client_type"] == "FII" and r["source"] == "upstox_market_fii"
    assert (r["fut_idx_long"], r["fut_idx_short"]) == (26129, 176187)
    assert (r["fut_stk_long"], r["fut_stk_short"]) == (3518604, 2885070)
    assert (r["opt_idx_call_long"], r["opt_idx_put_long"],
            r["opt_idx_call_short"], r["opt_idx_put_short"]) == \
        (586358, 840884, 799389, 426472)
    assert r["opt_stk_put_short"] == 165776


def test_totals_match_nse_definition_and_are_never_partial():
    rows = dr.map_market_fii(LIVE_SHAPE)
    # the six legs of the committed 2026-08-07 FII row sum to NSE's totals
    assert rows[-1]["total_long"] == 5_448_595
    assert rows[-1]["total_short"] == 4_766_465
    # 09-30 has futures only (no option rows): no total, rather than a
    # sum that silently leaves out the option legs
    assert "total_long" not in rows[0] and "total_short" not in rows[0]
    assert rows[0]["fut_idx_long"] == 27655


def test_feed_rows_line_up_with_the_committed_panel_columns():
    import csv
    with open("data/fo_participant_oi_2024_2026.csv") as f:
        cols = set(next(csv.reader(f)))
    rows = dr.map_market_fii(LIVE_SHAPE)
    assert rows                    # an empty result must not pass vacuously
    for r in rows:
        extra = set(r) - cols - {"source"}
        assert not extra, extra


def test_dii_live_shape_reads_and_lands_in_crore():
    payload = {"NSE_EQ|CASH": [
        # the docs' example amounts (rupees): 852.35 Cr buy, 723.46 Cr sell
        {"time_stamp": TS_1001, "buy_amount": 8523456789.0,
         "sell_amount": 7234567890.5},
        # an amount already in crore stays as it is
        {"time_stamp": TS_0930, "buy_amount": 12621.89,
         "sell_amount": 11699.63},
        {"time_stamp": TS_0930 - 86400000, "buy_amount": 0,
         "sell_amount": 0},                                  # refused
    ]}
    rows = dr.map_market_dii(payload)
    assert [r["date"] for r in rows] == ["2026-09-30", "2026-10-01"]
    assert rows[0]["dii_net"] == 922.26
    assert rows[1]["dii_buy"] == 852.35 and rows[1]["dii_sell"] == 723.46
    assert rows[1]["dii_net"] == 128.89


def test_dii_call_sends_its_required_data_type(monkeypatch):
    from trading.brokers.upstox import UpstoxClient
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"NSE_EQ|CASH": []}}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen[url] = params
        return _Resp()

    monkeypatch.setattr("trading.brokers.upstox.httpx.get", fake_get)
    c = UpstoxClient(api_key="k", api_secret="s", redirect_uri="r",
                     access_token="tok")
    c.get_market_dii()
    params = seen["https://api.upstox.com/v2/market/dii"]
    assert params["data_type"] == "NSE_EQ|CASH"
    assert params["interval"] == "1D"
