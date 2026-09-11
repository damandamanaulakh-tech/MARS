"""Owner order 2026-09-11 ("go with these formula" / "ash08 68") — the
three-formula view, and the filter switch behind it.

Pinned here, because each one is a claim the owner reads off a screen:
the ASH08 formula is theirs verbatim; its floor of 68 really does mean
"6-month return above +0.385%" (the arithmetic that makes it a near-copy
of our own absolute-momentum gate); the AshStocks column is OUR score by
construction, so a future re-implementation cannot make two things out of
one; nothing unjudgeable is ever rendered or gated as a FAIL; the filter
DEFAULTS OFF, because the measurement and not the enthusiasm sets the
default; and with it off the engine's buying is byte-identical to before.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from trading import multi_formula as mf
from trading.config import Config


def _close(n=400, total_return=0.30, seed=3):
    """A clean series with a known end-to-end return."""
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    step = (1 + total_return) ** (1 / (n - 1))
    return pd.Series(100.0 * step ** np.arange(n), index=idx)


def test_ash08_is_their_formula_verbatim():
    c = _close()
    got = mf.ash08_score(c)
    # Recompute their way: 182 CALENDAR days, 0.65 momentum + 0.35 coverage.
    w = c[c.index >= c.index[-1] - pd.Timedelta(days=182)]
    mom6 = float(w.iloc[-1]) / float(w.iloc[0]) - 1.0
    mom_s = max(0.0, min(100.0, 50.0 + mom6 * 200.0))
    cover = max(0.0, min(100.0, 100.0 * len(c.dropna()) / 126.0))
    assert got == pytest.approx(round(0.65 * mom_s + 0.35 * cover, 1))


def test_the_68_floor_really_means_six_month_return_above_0_385pct():
    """The finding that makes ASH08's floor a near-copy of a gate we run.

    Their coverage leg saturates at 100 for any real history, so
    68 = 0.65*(50 + mom6*200) + 35  =>  mom6 = 0.00385.
    """
    implied = ((mf.ASH08_FLOOR - 35.0) / 0.65 - 50.0) / 200.0
    assert implied == pytest.approx(0.00385, abs=1e-5)

    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
    for ret, should_pass in [(0.02, True), (-0.02, False)]:
        c = pd.Series(np.linspace(100.0, 100.0 * (1 + ret), 300), index=idx)
        assert (mf.ash08_score(c) >= mf.ASH08_FLOOR) is should_pass


def test_the_ashstocks_column_is_our_own_score_by_construction():
    """Spearman +1.000 was measured; this makes drift impossible.

    AshStocks' python brain is AM07's arithmetic, so its column reads our
    score against their higher floor. If someone ever re-implements it,
    this test fails and the summary's claim stops being true silently.
    """
    v = mf.evaluate(_close(), am07_score=64.0, am07_floor=62.0)
    assert v["rows"]["ashstocks"]["score"] == v["rows"]["am07"]["score"] == 64.0
    assert v["rows"]["am07"]["pass"] is True        # 64 >= our 62
    assert v["rows"]["ashstocks"]["pass"] is False  # 64 <  their 70
    assert v["rows"]["ashstocks"]["floor"] == 70.0


def test_the_live_floor_is_read_not_hardcoded():
    """Our own bar comes from the engine, so this module can never disagree
    with it — the doc-vs-code error of 2026-07-30 in miniature."""
    v = mf.evaluate(_close(), am07_score=64.0, am07_floor=70.0)
    assert v["rows"]["am07"]["floor"] == 70.0
    assert v["rows"]["am07"]["pass"] is False


def test_unjudgeable_is_data_needed_never_a_fail():
    short = _close(n=40)
    v = mf.evaluate(short, am07_score=None, am07_floor=62.0)
    assert v["rows"]["ash08"]["score"] is None
    assert v["rows"]["ash08"]["pass"] is None
    assert v["rows"]["ash08"]["label"] == "DATA_NEEDED"
    assert v["rows"]["am07"]["label"] == "DATA_NEEDED"
    assert mf.mom20(_close(n=5)) is None


def test_the_gate_fails_open_on_data_it_cannot_judge():
    """A short history is not evidence against a stock. A gate that blocked
    on DATA_NEEDED would quietly shrink the money universe."""
    v = mf.evaluate(_close(n=40), am07_score=80.0, am07_floor=62.0)
    assert mf.gate_blocks(v) is None
    assert mf.gate_blocks(None) is None
    assert mf.gate_blocks({}) is None


def test_the_gate_blocks_and_names_the_formula_that_refused():
    v = mf.evaluate(_close(total_return=-0.20), am07_score=64.0, am07_floor=62.0)
    why = mf.gate_blocks(v)
    assert why and "ash08" in why and "ashstocks" in why
    # Our own floor is the engine's job; the gate speaks only for the others.
    assert "am07" not in why


def test_agreement_counts_only_what_was_actually_judged():
    v = mf.evaluate(_close(n=40), am07_score=None, am07_floor=62.0)
    assert v["judged"] == 0 and v["agree"] == 0
    assert v["all_pass"] is False      # nothing judged is not "everything passed"


def test_the_filter_defaults_off_because_the_measurement_says_so():
    """research/ash_formula_study/: ASH08's floor removes 3% of candidates
    and AshStocks' live rule scored -0.77 train / -0.45 test. The owner owns
    the switch; the evidence owns the default."""
    assert Config().multi_formula_gate is False


def test_the_module_stays_pure():
    """Same contract as trading/internals.py — engine may import it, so it
    must not reach for the money path, the network, or config mutation."""
    import inspect
    src = inspect.getsource(mf)
    # Strip comments and the docstring FIRST: this file's own prose names
    # every module below, and a source pin that matches its own explanation
    # is the fault class this repo has hit five times.
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    code = code.split('"""')[0] + '"""'.join(code.split('"""')[2:])
    for banned in ("from .engine", "from .risk", "from .orders",
                   "from .danger", "import requests", "import httpx",
                   "yfinance", "web.db"):
        assert banned not in code, f"multi_formula must not reference {banned}"
