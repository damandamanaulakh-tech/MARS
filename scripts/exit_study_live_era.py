"""Node 12 evidence refresh (owner: "node 12", 2026-08-24) — does the exit
study's verdict survive the LIVE entry floor?

The committed exit_variant_study_results.json was generated 2026-07-27 at
entry floor 55 (era-pinned so it regenerates). The live preset has been 62
since the owner's 2026-07-30 order — a higher floor means fewer, stronger
entries, and a verdict about exit brackets could in principle flip. Before
the owner decides Node 12, this runs the SAME machinery (same script's
loader/config/wrapper, same 191-ticker panel, same 4 windows, same real
backtest engine) with ONLY the era pin removed — floor = the live preset —
for the three variants that matter to the decision:

    A   stop -5 / target +12 / trail -15   (live today; the trail is dead)
    D   stop -5 / target +20 / trail -15   (the committed study's winner)
    D15 stop -5 / target +15 / trail -12   (the runner-up)

NOT varied (disclosed, not hidden): the panel stays the 191-ticker
committed zip — the owner's 50-name custom universe is a weekly hand-picked
list whose small-caps are not in the committed offline store, so it cannot
be replayed honestly; the floor is the only era delta under test.

Usage: python scripts/exit_study_live_era.py <VARIANT>   (A | D | D15)
Writes research/exit_study_live_era/<VARIANT>.json (one file per variant so
three processes can run in parallel; each window appended as it finishes —
a killed run resumes at the next window).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from exit_variant_study import (VARIANTS, WINDOWS, load_history,  # noqa: E402
                                make_cfg)
import trading.backtest as bt                                     # noqa: E402

RUN_VARIANTS = {
    "A": "A_baseline_-5_+12_t15",
    "D": "D_target+20_t15",
    "D15": "D15_target+15_t12",
}
OUT_DIR = ROOT / "research" / "exit_study_live_era"


def main(which: str) -> None:
    vname = RUN_VARIANTS[which]
    stop, target, trail, be = VARIANTS[vname]
    assert be is None, "A/D/D15 carry no breakeven wrapper"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{which}.json"
    done: dict = {}
    if out_path.exists():
        done = json.loads(out_path.read_text())
    hist = load_history()
    tickers = sorted(t for t in hist if t != "^NSEI")
    for wname, start, end in WINDOWS:
        if wname in done:
            print(f"{which} {wname}: cached", flush=True)
            continue
        cfg = make_cfg(tickers, stop, target, trail)
        cfg.state_dir = f"/tmp/exit_study_live_era_{which}"
        # THE LIVE ERA: undo the study's floor-55 pin — apply_mode_preset()
        # in make_cfg already set the live preset value; the pin overwrote
        # it. Re-apply the preset so the floor is whatever production runs.
        cfg.apply_mode_preset()
        cfg.stop_loss_pct = stop
        cfg.take_profit_pct = target
        cfg.trailing_stop_pct = trail
        floor = cfg.min_score_to_enter
        t0 = time.time()
        r = bt.run_backtest(cfg, start, end, universe=tickers,
                            preloaded_history=hist)
        reasons: dict = {}
        for t in r.trades:
            k = t.get("reason", "?")
            reasons[k] = reasons.get(k, 0) + 1
        row = {
            "variant": vname, "window": wname,
            "entry_floor": floor,
            "return_pct": round(r.total_return_pct, 2),
            "alpha_pct": round(r.alpha_pct, 2),
            "bench_pct": round(r.benchmark_total_return_pct, 2),
            "max_dd_pct": round(r.max_drawdown_pct, 2),
            "trades": r.num_trades,
            "win_rate_pct": round(r.win_rate_pct, 1),
            "sharpe": round(r.sharpe_ratio, 2),
            "exit_reasons": reasons,
            "secs": round(time.time() - t0, 1),
        }
        done[wname] = row
        out_path.write_text(json.dumps(done, indent=1))
        print(f"{which} {wname}: {row['return_pct']:+.2f}% "
              f"({row['trades']} trades, floor {floor})", flush=True)
    print(f"{which} DONE", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
