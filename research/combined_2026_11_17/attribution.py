"""Reporting-only correction, after the holdout was consumed; never a new strategy.

Retain the original sealed results. Replace exactly one accounting predicate
so open and closed segment P&L both use the stored holding mode. Verify that
all actual decisions, fills, costs, trade counts and shared-book metrics match.
"""
from copy import deepcopy
import gzip
import inspect
import json
from pathlib import Path

from trading.validation import canonical, file_hash
from . import replay, study

HERE = Path(__file__).resolve().parent


def corrected_run():
    source = inspect.getsource(replay.run)
    before = "held = [p for p in portfolio.positions.values() if category_of(p) == mode]"
    after = "held = [p for p in portfolio.positions.values() if p.mode == mode]"
    if source.count(before) != 1:
        raise ValueError("Frozen attribution source no longer matches the declared correction")
    namespace = dict(replay.__dict__)
    exec(compile(source.replace(before, after), "<declared-segment-attribution-correction>", "exec"), namespace)
    return namespace["run"]


def strip_attribution(result):
    out = deepcopy(result)
    for outcome in out["outcomes"].values():
        outcome.pop("mode_equity")
        for metrics in outcome["summary"]["modes"].values():
            for key in ("max_drawdown_pct", "missing_equity_marks", "final_equity"):
                metrics.pop(key)
    return out


def evaluate():
    original = json.loads(gzip.decompress((HERE / "results.json.gz").read_bytes()))
    from .reproduce import parallel_scenarios
    with parallel_scenarios(corrected=True):
        corrected = study.evaluate()
    if canonical(strip_attribution(original)) != canonical(strip_attribution(corrected)):
        raise ValueError("Accounting correction changed strategy decisions or shared-book results")
    return {"schema": 1, "original_results_sha256": file_hash(HERE / "results.json.gz"),
        "amendment_sha256": file_hash(HERE / "attribution-amendment.json"),
        "reporting_source_sha256": file_hash(Path(__file__)),
        "decision_fill_cost_and_shared_book_parity": True,
        "outcomes": {key: {"mode_equity": r["mode_equity"], "modes": r["summary"]["modes"]}
                     for key, r in corrected["outcomes"].items()}}


def main(record=False):
    raw = canonical(evaluate())
    path = HERE / "attribution.json.gz"
    if record:
        with path.open("xb") as handle:
            handle.write(gzip.compress(raw, mtime=0))
        print("Segment attribution recorded; every decision/fill/cost/shared-book result unchanged.")
    else:
        if gzip.decompress(path.read_bytes()) != raw:
            raise ValueError("Segment attribution correction differs")
        print("Corrected segment attribution exactly reproduced; all strategy outcomes unchanged.")
