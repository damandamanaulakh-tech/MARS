"""⚡ The owner's trigger layer — guards that the code IS the sheet.

The registry is parsed from research/chityy_archive/trigger_layer_v0_2/
trigger_params_v0_2.csv at import; these tests re-read that CSV independently
and cross-check every id, family, side, weight and readiness flag, then probe
the trigger maths on engineered fixtures. Display + watchlist only — the
boundary test pins that the engine can never grow a dependency on this.
"""

import csv

import numpy as np
import pandas as pd
import pytest

from trading import triggers as T


# --------------------------------------------------------------- the registry

def _sheet_rows():
    with open(T.PARAMS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_registry_matches_the_owners_sheet_exactly():
    sheet = _sheet_rows()
    assert len(T.TRIGGERS) == len(sheet) == 112
    by_id = {t.id: t for t in T.TRIGGERS}
    for row in sheet:
        t = by_id[row["parameter_id"]]
        assert t.name == row["parameter_name"].strip()
        assert t.family == row["trigger_family"].strip()
        assert t.side == row["rise_fall_side"].strip()
        assert t.weight == float(row["default_weight"])
        assert t.ohlcv_ready == (row["can_be_calculated_from_ohlcv"] == "YES")


def test_every_ohlcv_ready_trigger_has_an_implementation():
    assert len(T.COMPUTABLE) == 79
    assert all(t.fn is not None for t in T.COMPUTABLE)


def test_external_triggers_have_no_fn_and_name_their_data():
    assert len(T.EXTERNAL) == 33
    for t in T.EXTERNAL:
        assert t.fn is None
        assert t.needs, f"{t.id} must say what data unlocks it"


def test_engine_and_risk_never_import_the_trigger_layer():
    """Display + watchlist only — the owner's own REQUIRES_15Y_BACKTEST rule.
    The money path may not silently grow a dependency on unproven triggers."""
    import re
    from pathlib import Path
    pat = re.compile(r"^\s*(from\s+\S*triggers\s+import|import\s+\S*triggers)",
                     re.MULTILINE)
    for mod in ("trading/engine.py", "trading/risk.py", "trading/orders.py"):
        src = Path(mod).read_text()
        assert not pat.search(src), \
            f"{mod} imports the trigger layer — that needs an owner order"


def test_trigger_module_places_no_orders():
    from pathlib import Path
    src = Path("trading/triggers.py").read_text()
    for forbidden in ("_open(", "_close(", "place_limit", "httpx", "requests"):
        assert forbidden not in src, f"triggers.py contains {forbidden}"


# ------------------------------------------------------------------- fixtures

def _df(c, h=None, l=None, o=None, v=None):
    """PRODUCTION-SHAPED frame: capitalized columns, exactly what
    history_store/candle_chart.load_ohlcv return. The 2026-08-09 audit found
    the whole layer crashed on real data (KeyError 'open') while these tests
    passed on hand-built lowercase frames — fixtures must match production."""
    c = np.asarray(c, dtype=float)
    n = len(c)
    h = np.asarray(h, dtype=float) if h is not None else c * 1.01
    l = np.asarray(l, dtype=float) if l is not None else c * 0.99
    o = np.asarray(o, dtype=float) if o is not None else (h + l) / 2
    v = np.asarray(v, dtype=float) if v is not None else np.full(n, 100_000.0)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v})


def _base(n=300, seed=7, vol=0.012, drift=0.0003):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    h = c * (1 + np.abs(rng.normal(0, 0.005, n)))
    l = c * (1 - np.abs(rng.normal(0, 0.005, n)))
    o = l + (h - l) * rng.random(n)
    v = rng.integers(80_000, 120_000, n).astype(float)
    return c, h, l, o, v


# ------------------------------------------------------------- trigger maths

def test_nr7_fires_on_an_engineered_narrowest_day():
    c, h, l, o, v = _base()
    hi10, lo10 = np.max(h[-10:-1]), np.min(l[-10:-1])
    c[-1] = (hi10 + lo10) / 2       # dead centre of the 10D base...
    h[-1] = c[-1] * 1.00005         # ...on a range far narrower than any
    l[-1] = c[-1] * 0.99995         # of the prior 7 sessions
    o[-1] = c[-1]
    hits = T.evaluate(_df(c, h, l, o, v))
    assert hits["T12W_0001"] is True


def test_volume_wakeup_fires_after_a_dry_base():
    c, h, l, o, v = _base()
    v[-6:-1] = 50_000               # dry: well under vol20
    v[-1] = 400_000                 # wakeup: > 1.5x vol20
    hits = T.evaluate(_df(c, h, l, o, v))
    assert hits["T12W_0057"] is True


def test_support_break_with_volume_spike_fires():
    c, h, l, o, v = _base()
    c[-1] = np.min(l[-20:-1]) * 0.97
    l[-1] = c[-1] * 0.995
    h[-1] = c[-2]
    o[-1] = c[-2]
    v[-1] = 500_000
    hits = T.evaluate(_df(c, h, l, o, v))
    assert hits["T12W_0089"] is True


def test_gap_down_reclaim_fires():
    c, h, l, o, v = _base()
    o[-1] = l[-2] * 0.97            # opens below yesterday's low
    c[-1] = c[-2] * 1.001           # closes back above yesterday's close
    h[-1] = c[-1] * 1.005
    l[-1] = o[-1] * 0.995
    hits = T.evaluate(_df(c, h, l, o, v))
    assert hits["T12W_0037"] is True


def test_new_high_with_weak_close_fires_distribution():
    c, h, l, o, v = _base()
    h[-1] = np.max(h[-20:]) * 1.02  # new 20D high...
    l[-1] = c[-2] * 0.97
    c[-1] = l[-1] + 0.2 * (h[-1] - l[-1])   # ...but closes near the low
    hits = T.evaluate(_df(c, h, l, o, v))
    assert hits["T12W_0071"] is True


def test_gates_always_agree_with_the_owner_logic_strings():
    """Property check on random tapes: every gate verdict must be exactly the
    owner's boolean over the family counts and scores — no drift possible."""
    for seed in (1, 2, 3, 11, 42):
        c, h, l, o, v = _base(seed=seed)
        hits = T.evaluate(_df(c, h, l, o, v))
        fam = T.family_counts(hits)
        sc = T.side_scores(hits)
        gates = {g["id"]: g["status"] for g in T.evaluate_gates(hits)}
        assert gates["GATE_FALL_AVOID_01"] == (
            "PASS" if (fam.get(T.F_DISTRIBUTION, 0) >= 3
                       or sc.get("FALL", 0) >= 55) else "no")
        assert gates["GATE_COLLAPSE_BLOCK_01"] == (
            "PASS" if (sc.get("COLLAPSE", 0) >= 60
                       or hits.get("T12W_0089") is True) else "no")
        assert gates["GATE_RISE_WATCH_01"] == (
            "PASS" if (fam.get(T.F_COMPRESSION, 0) >= 2
                       and fam.get(T.F_ACCUMULATION, 0) >= 2
                       and fam.get(T.F_DISTRIBUTION, 0) == 0) else "no")


def test_external_triggers_are_always_none_never_guessed():
    c, h, l, o, v = _base()
    hits = T.evaluate(_df(c, h, l, o, v))
    for t in T.EXTERNAL:
        assert hits[t.id] is None, f"{t.id} produced a value without its data"


def test_short_history_returns_none_not_false():
    c, h, l, o, v = _base(n=40)
    hits = T.evaluate(_df(c, h, l, o, v))
    assert hits["T12W_0004"] is None            # needs 252 bars
    assert hits["T12W_0003"] is None            # needs 130 bars
    dq = T.data_quality_score(hits)
    assert dq < 100.0


def test_no_history_at_all_is_all_none():
    hits = T.evaluate(None)
    assert all(h is None for h in hits.values())
    assert T.data_quality_score(hits) == 0.0


# ----------------------------------------------------------- scores and gates

def _hits_where(true_ids):
    """All computable triggers False except the named ones; externals None."""
    hits = {t.id: (None if not t.ohlcv_ready else t.id in true_ids)
            for t in T.TRIGGERS}
    return hits


def test_side_scores_are_weighted_percent():
    rise = [t for t in T.COMPUTABLE if t.side == "RISE"]
    hits = _hits_where({rise[0].id})
    s = T.side_scores(hits)
    expect = 100.0 * rise[0].weight / sum(t.weight for t in rise)
    assert s["RISE"] == pytest.approx(expect, abs=0.11)
    assert s["FALL"] == 0.0


def test_watch_gate_needs_owner_exact_conditions():
    comp = [t.id for t in T.COMPUTABLE if t.family == T.F_COMPRESSION][:2]
    accu = [t.id for t in T.COMPUTABLE if t.family == T.F_ACCUMULATION][:2]
    dist = [t.id for t in T.COMPUTABLE if t.family == T.F_DISTRIBUTION][:1]

    def gate(hits):
        return {g["id"]: g["status"] for g in T.evaluate_gates(hits)}

    assert gate(_hits_where(set(comp + accu)))["GATE_RISE_WATCH_01"] == "PASS"
    # one distribution trigger firing kills the watch (distribution_count=0)
    assert gate(_hits_where(set(comp + accu + dist)))["GATE_RISE_WATCH_01"] == "no"
    # 1 compression is not 2
    assert gate(_hits_where(set(comp[:1] + accu)))["GATE_RISE_WATCH_01"] == "no"


def test_fall_avoid_gate_fires_on_three_distribution_triggers():
    dist = [t.id for t in T.COMPUTABLE if t.family == T.F_DISTRIBUTION][:3]
    gates = {g["id"]: g["status"] for g in T.evaluate_gates(_hits_where(set(dist)))}
    assert gates["GATE_FALL_AVOID_01"] == "PASS"


def test_collapse_block_fires_on_support_break_trigger_alone():
    gates = {g["id"]: g["status"]
             for g in T.evaluate_gates(_hits_where({"T12W_0089"}))}
    assert gates["GATE_COLLAPSE_BLOCK_01"] == "PASS"


def test_data_needed_gates_stay_data_needed():
    gates = {g["id"]: g["status"] for g in T.evaluate_gates(_hits_where(set()))}
    assert gates["GATE_SECTOR_CONFIRM_01"] == "DATA_NEEDED"
    assert gates["GATE_EVENT_OVERLAY_01"] == "DATA_NEEDED"


def test_stock_view_watch_flag_matches_the_gate():
    c, h, l, o, v = _base()
    view = T.stock_view(_df(c, h, l, o, v))
    gate = next(g for g in view["gates"] if g["id"] == "GATE_RISE_WATCH_01")
    assert view["watch"] == (gate["status"] == "PASS")


# ------------------------------------------------- production-shape guards

def test_both_column_cases_evaluate_identically():
    c, h, l, o, v = _base()
    cap = _df(c, h, l, o, v)
    low = cap.rename(columns=str.lower)
    assert T.evaluate(cap) == T.evaluate(low)


def test_the_real_loader_output_feeds_the_trigger_layer():
    """END-TO-END: the exact chain the daily pass runs — candle_chart.
    load_ohlcv (repo zip, no network) into stock_view. This is the test whose
    absence let a KeyError ship: the layer had never touched real data."""
    from web.candle_chart import load_ohlcv
    df = load_ohlcv("TCS.NS", tail=420)
    assert df is not None and len(df) > 250, "repo zip must supply TCS history"
    view = T.stock_view(df, asof="test")
    assert view["computable"] == view["total_computable"] == 79
    assert set(view["scores"]) >= {"RISE", "FALL"}
    assert len(view["gates"]) == 8


def test_a_frame_missing_columns_raises_loudly():
    with pytest.raises(KeyError):
        T._Ctx(pd.DataFrame({"Close": [1.0] * 30}))
