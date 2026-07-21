import os, sys, tempfile
from datetime import date, timedelta
from trading.autoswap import SwapConfig, Position, Candidate, plan_swaps, run_autoswap
from trading.auto import AutoConfig, RunContext, PIT_CLEAN

TODAY = date(2026, 7, 16)
def C(t,s,sec="IT",px=100.0): return Candidate(t,sec,s,px)
def P(t,s,sec="IT"): return Position(t,sec,s)
def _cfg(**kw):
    d=tempfile.mkdtemp()
    b=dict(kill_file=os.path.join(d,"K"),journal_path=os.path.join(d,"j.jsonl"),state_path=os.path.join(d,"s.json"))
    b.update(kw); return AutoConfig(**b)
def _ctx(**kw):
    b=dict(today=TODAY,token_valid=True,latest_backtest_universe_tags=[PIT_CLEAN],
           paper_days_completed=60,equity=1_000_000,peak_equity=1_000_000,prev_close={})
    b.update(kw); return RunContext(**b)

def test_fills_empty_slots_to_target_count():
    s=SwapConfig(target_count=3,max_swaps_per_run=5,must_beat_exited=False)
    p=plan_swaps(s,[P("A.NS",70)],[C("B.NS",80),C("C.NS",75,"BANK")],{},TODAY,300_000)
    assert [o.ticker for o in p.orders]==["B.NS","C.NS"]

def test_full_book_swaps_nothing():
    s=SwapConfig(target_count=2)
    p=plan_swaps(s,[P("A.NS",70),P("B.NS",71,"BANK")],[C("C.NS",99)],{},TODAY,1e6)
    assert p.orders==[] and "book full" in p.skipped[0]

def test_replacement_must_beat_the_name_that_left():
    s=SwapConfig(target_count=2,must_beat_exited=True)
    p=plan_swaps(s,[P("A.NS",70)],[C("B.NS",65)],{},TODAY,1e6,exited_scores=[72.0])
    assert p.orders==[] and "does not beat exited" in p.skipped[0]

def test_score_floor_blocks_weak_refill():
    s=SwapConfig(target_count=2,min_score=60,must_beat_exited=False)
    p=plan_swaps(s,[],[C("B.NS",41)],{},TODAY,1e6)
    assert p.orders==[] and "< floor" in p.skipped[0]

def test_cooldown_blocks_immediate_rebuy():
    s=SwapConfig(target_count=2,cooldown_days=5,must_beat_exited=False)
    p=plan_swaps(s,[],[C("B.NS",90)],{"B.NS":TODAY-timedelta(days=2)},TODAY,1e6)
    assert p.orders==[] and "cooldown" in p.skipped[0]
    assert plan_swaps(s,[],[C("B.NS",90)],{"B.NS":TODAY-timedelta(days=9)},TODAY,1e6).orders

def test_sector_cap_prevents_silent_concentration():
    s=SwapConfig(target_count=6,max_per_sector=2,must_beat_exited=False)
    held=[P("A.NS",70,"IT"),P("B.NS",70,"IT")]
    p=plan_swaps(s,held,[C("C.NS",95,"IT"),C("D.NS",80,"BANK")],{},TODAY,1e6)
    assert [o.ticker for o in p.orders]==["D.NS"]
    assert any("sector IT at cap" in x for x in p.skipped)

def test_churn_brake_caps_swaps_per_run():
    s=SwapConfig(target_count=10,max_swaps_per_run=2,must_beat_exited=False)
    r=[C(f"T{i}.NS",90-i,"IT" if i%2 else "BANK") for i in range(6)]
    assert len(plan_swaps(s,[],r,{},TODAY,1e6).orders)==2

def test_swap_obeys_full_gate_ladder_not_a_shortcut():
    cfg=_cfg(mode="LIVE_AUTO",algo_id="A1")
    ctx=_ctx(latest_backtest_universe_tags=["SURVIVORSHIP-BIASED (...)"])
    out=run_autoswap(cfg,SwapConfig(target_count=2,must_beat_exited=False),ctx,[],[C("B.NS",90)],{})
    assert out["mode"]=="LIVE_CONFIRM" and out["placed"]==[]

def test_kill_switch_halts_swaps_too():
    cfg=_cfg(mode="LIVE_AUTO",algo_id="A1"); open(cfg.kill_file,"w").write("x")
    out=run_autoswap(cfg,SwapConfig(target_count=2,must_beat_exited=False),_ctx(),[],[C("B.NS",90)],{})
    assert out["mode"]=="HALT"
