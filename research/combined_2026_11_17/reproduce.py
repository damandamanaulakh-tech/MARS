"""Verify against archived source, not whatever strategy main runs next month.

Independent slippage scenarios run in fresh processes. Input acceleration removes
rows older than intraday_bars.session_days would discard anyway, before the
original validator loops over them. Accepted inputs and all numerical results
must exactly match the original sequential, unaccelerated run. No score,
threshold or selected candidate changes.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent


def _scenario(daily, intraday, saved, protocol, bps, corrected):
    """Each scenario has a fresh interpreter, clock, caches and paper book."""
    from . import replay
    if corrected:
        from .attribution import corrected_run
        run = corrected_run()
    else:
        run = replay.run
    with bounded_input_window():
        return run(daily, intraday, saved, protocol, bps)


@contextmanager
def parallel_scenarios(corrected=False):
    """Independent sensitivity scenarios; full original-output equality required."""
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    from . import study
    futures = {}
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        def run(daily, intraday, saved, protocol, bps):
            if not futures:
                for value in protocol["slippage_bps_each_side"]:
                    futures[value] = pool.submit(_scenario, daily, intraday, saved, protocol, value, corrected)
            return futures[bps].result()
        with patch.object(study, "run", run):
            yield


@contextmanager
def bounded_input_window():
    from .replay import Inputs
    from trading import intraday_bars as ib
    import pandas as pd
    original = Inputs.fetch_intraday
    def fetch(self, tickers, cfg, client=None, now=None, **kwargs):
        frames = self.intraday
        first = ib.session_days(self.now.to_pydatetime())[0]
        cutoff = pd.Timestamp(first, tz=ib.IST).tz_convert("UTC")
        self.intraday = {t: f.loc[f.index >= cutoff] for t, f in frames.items() if t in tickers}
        try:
            return original(self, tickers, cfg, client, now, **kwargs)
        finally:
            self.intraday = frames
    with patch.object(Inputs, "fetch_intraday", fetch):
        yield


def check_runtime(seal):
    from importlib.metadata import version
    if platform.python_version() != seal["runtime"]["python"]:
        raise ValueError("Use the frozen Python runtime")
    for name, expected in seal["runtime"]["packages"].items():
        if version(name) != expected:
            raise ValueError("Frozen dependency mismatch: " + name)


def main():
    if "--child" in sys.argv:
        from . import study
        check_runtime(json.loads((HERE / "seal.json").read_text()))
        if "--attribution" in sys.argv:
            from . import attribution
            attribution.main(record="--record" in sys.argv)
        else:
            with parallel_scenarios():
                sys.argv = [sys.argv[0], "verify"]
                study.main()
        return
    from trading.validation import file_hash
    seal = json.loads((HERE / "seal.json").read_text())
    check_runtime(seal)
    if file_hash(HERE / "source.zip") != seal["source_archive_sha256"]:
        raise ValueError("Archived strategy code hash mismatch")
    with tempfile.TemporaryDirectory(prefix="mars-seq11-frozen-") as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(HERE / "source.zip") as archive:
            if set(archive.namelist()) != set(seal["files"]):
                raise ValueError("Unexpected source archive membership")
            for name in archive.namelist():
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise ValueError("Unsafe archive path")
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
                if file_hash(target) != seal["files"][name]:
                    raise ValueError("Source member hash mismatch: " + name)
        dest = root / "research/combined_2026_11_17"
        # Supplement missing non-Python assets from the already committed
        # pre-acquisition revision. Never replace the original source seal.
        assets = json.loads((HERE / "runtime-assets.json").read_text())
        if assets["source_commit"] != seal["source_commit"]:
            raise ValueError("Runtime asset revision differs from sealed strategy")
        checkout = HERE.parents[1]
        for name, expected in assets["files"].items():
            if Path(name).is_absolute() or ".." in Path(name).parts or name in seal["files"]:
                raise ValueError("Unsafe or conflicting supplemental asset path")
            source = checkout / name
            if file_hash(source) != expected["sha256"]:
                raise ValueError("Restore the exact pre-acquisition asset from Git: " + name)
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        names = ["reproduce.py", "seal.json", "opened.json", "manifest.json", "raw_responses.json.gz", "results.json.gz"]
        if "--attribution" in sys.argv:
            names += ["attribution.py", "attribution-amendment.json"]
            if "--record" not in sys.argv:
                names.append("attribution.json.gz")
        for name in names:
            shutil.copy2(HERE / name, dest / name)
        env = {**os.environ, "PYTHONPATH": str(root), "RENDER_GIT_COMMIT": seal["source_commit"]}
        subprocess.run([sys.executable, "-m", "research.combined_2026_11_17.reproduce", "--child", *sys.argv[1:]],
                       cwd=root, env=env, check=True)
        if "--attribution" in sys.argv and "--record" in sys.argv:
            with (HERE / "attribution.json.gz").open("xb") as handle:
                handle.write((dest / "attribution.json.gz").read_bytes())


if __name__ == "__main__":
    main()
