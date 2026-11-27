"""XR-052: rotation no longer sells a winner for a buy that cannot happen.

The entry decision runs in two passes — a gate pass that fills `cleared`, then
rotation, then the sizing/opening pass whose only `can_open` call applies the
kill switch, the sector caps, the minimum ticket and the rest. So rotation
decided BEFORE those gates ran, and on some days it sold a near-target winner,
booked a round trip of costs and slippage, gave up the last stretch to target,
and the pick was then refused for a reason cash cannot fix.

Reproduced three ways by the verification pass: a kill-switch day (the day the
book has already lost 2%, and the day the engine would sell a winner for
nothing), a full sector, and a ticket under the minimum.

THE ORDER IS NOT CHANGED. trading/tests/test_rotation_exec.py pins rotation
BEFORE can_open deliberately, because the freed cash has to exist when can_open
counts it. This is the other half: a read-only test of the gates no amount of
freed cash changes.

Owner's choice 2026-11-27, from an options card: "Only sell when the new buy can
actually go through."
"""
import pytest

from trading import risk as R
from trading.config import Config
from trading.paper import PaperPosition
from datetime import datetime


NOW = datetime(2026, 11, 27, 10, 30)


def cfg_of(tmp_path, **kw):
    c = Config(state_dir=str(tmp_path), mode='nifty200_momentum')
    c.apply_mode_preset()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def pos(ticker, price=1000.0, qty=10):
    return PaperPosition(ticker=ticker, qty=qty, entry_price=price,
                         entry_date=NOW, sector='IT', peak_price=price,
                         entry_score=80., reasoning='fixture')


def state(cfg, killed=False):
    s = R.RiskState(cfg, cfg.starting_capital)
    s.kill_switch = killed
    return s


# ------------------------------------------- what cash cannot fix

def test_a_kill_switch_day_is_named(tmp_path):
    cfg = cfg_of(tmp_path)
    assert R.blocked_regardless_of_cash(
        cfg, state(cfg, killed=True), {}, 'TCS.NS', 1000.0) == 'kill_switch_active'


def test_a_zero_sized_pick_is_named(tmp_path):
    cfg = cfg_of(tmp_path)
    assert R.blocked_regardless_of_cash(
        cfg, state(cfg), {}, 'TCS.NS', 1000.0,
        notional_override=0.0) == 'no_estimated_edge'


def test_a_share_dearer_than_the_whole_ticket_is_named(tmp_path):
    cfg = cfg_of(tmp_path)
    assert R.blocked_regardless_of_cash(
        cfg, state(cfg), {}, 'TCS.NS', 1e12) == 'qty_zero'


def test_a_full_sector_is_named(tmp_path):
    cfg = cfg_of(tmp_path, max_sector_positions=3)
    cfg.sector_map = {f'IT{i}.NS': 'IT' for i in range(3)}
    cfg.sector_map['NEW.NS'] = 'IT'
    held = {f'IT{i}.NS': pos(f'IT{i}.NS') for i in range(3)}
    assert R.blocked_regardless_of_cash(
        cfg, state(cfg), held, 'NEW.NS', 1000.0,
        current_equity=5_00_00_000) == 'sector_cap_reached'


def test_a_clean_pick_is_not_blocked(tmp_path):
    cfg = cfg_of(tmp_path)
    cfg.sector_map = {'NEW.NS': 'IT'}
    assert R.blocked_regardless_of_cash(
        cfg, state(cfg), {}, 'NEW.NS', 1000.0, current_equity=5_00_00_000) is None


# -------------------------------- selling a same-sector name DOES help

def test_a_sector_slot_freed_by_the_sale_is_counted(tmp_path):
    """Without this the helper would stand rotation down in the one case
    where selling genuinely helps."""
    cfg = cfg_of(tmp_path, max_sector_positions=3)
    cfg.sector_map = {f'IT{i}.NS': 'IT' for i in range(3)}
    cfg.sector_map['NEW.NS'] = 'IT'
    held = {f'IT{i}.NS': pos(f'IT{i}.NS') for i in range(3)}
    # without the sale: blocked
    assert R.blocked_regardless_of_cash(
        cfg, state(cfg), held, 'NEW.NS', 1000.0,
        current_equity=5_00_00_000) == 'sector_cap_reached'
    # with one of them up for sale: not blocked
    assert R.blocked_regardless_of_cash(
        cfg, state(cfg), held, 'NEW.NS', 1000.0,
        current_equity=5_00_00_000, freeing=('IT0.NS',)) is None


def test_selling_another_sector_does_not_free_this_one(tmp_path):
    cfg = cfg_of(tmp_path, max_sector_positions=3)
    cfg.sector_map = {f'IT{i}.NS': 'IT' for i in range(3)}
    cfg.sector_map.update({'PHARMA.NS': 'Pharma', 'NEW.NS': 'IT'})
    held = {f'IT{i}.NS': pos(f'IT{i}.NS') for i in range(3)}
    held['PHARMA.NS'] = pos('PHARMA.NS')
    assert R.blocked_regardless_of_cash(
        cfg, state(cfg), held, 'NEW.NS', 1000.0, current_equity=5_00_00_000,
        freeing=('PHARMA.NS',)) == 'sector_cap_reached'


# --------------------------------- what rotation IS allowed to fix

@pytest.mark.parametrize('why', ['no cash at all', 'ceiling reached'])
def test_the_gates_rotation_exists_to_fix_are_not_named(tmp_path, why):
    """Naming these would stop rotation firing at all — they are its purpose."""
    cfg = cfg_of(tmp_path, max_positions=2)
    cfg.sector_map = {'NEW.NS': 'IT', 'A.NS': 'IT', 'B.NS': 'Pharma'}
    held = {'A.NS': pos('A.NS'), 'B.NS': pos('B.NS')} if why == 'ceiling reached' else {}
    assert R.blocked_regardless_of_cash(
        cfg, state(cfg), held, 'NEW.NS', 1000.0, current_equity=5_00_00_000) is None


def test_the_helper_never_reads_cash_or_quantity():
    """It must be a test of the gates cash cannot fix, nothing more."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(R.blocked_regardless_of_cash))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert 'cash' not in names
    args = {a.arg for a in tree.body[0].args.args}
    assert 'cash' not in args


# ------------------------------------------------- the engine stands down

def test_rotation_sells_nothing_when_no_pick_could_open(tmp_path, monkeypatch):
    """The whole point, driven through the real engine."""
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)
    e.cfg.category_budgets_enabled = False
    e.risk.kill_switch = True                      # cash cannot fix this
    before = dict(e.portfolio.positions)
    pick = {'ticker': 'NEW.NS', 'score': 85.0, 'close': 1000.0,
            'reasoning': 'isolated pick'}
    assert e._rotation_exits([pick], NOW) == []
    assert dict(e.portfolio.positions) == before, "nothing may be sold"
    assert not e.portfolio.trades, "no round trip of costs may be booked"
    rows = [r for r in e.portfolio.decision_journal
            if r.get('action') == 'ROTATION_STOOD_DOWN']
    assert rows, "the owner must be able to see why nothing was sold"
    assert 'kill_switch_active' in str(rows[-1])


def test_a_read_failure_never_stops_a_rotation(tmp_path, monkeypatch):
    """A guard that breaks the feature it guards is worse than no guard."""
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)
    monkeypatch.setattr('trading.engine.blocked_regardless_of_cash',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    pick = {'ticker': 'NEW.NS', 'score': 85.0, 'close': 1000.0,
            'reasoning': 'isolated pick'}
    e._rotation_exits([pick], NOW)                 # must not raise
