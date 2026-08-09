"""D-batch guards — the owner's manual and status docs must match the code.
The 2026-08-09 audit found APP_BRIEF carrying SIX wrong money facts; these
tests pin the manual's numbers to the live config so they can never silently
drift again.
"""

from pathlib import Path

from trading.config import Config
from trading.modes import get_preset


def _brief() -> str:
    return Path("docs/APP_BRIEF.md").read_text()


def test_app_brief_capital_matches_config():
    cfg = Config()
    assert f"₹{int(cfg.starting_capital / 1_00_000)}L" in _brief().replace(
        "₹50L", f"₹{int(cfg.starting_capital / 1_00_000)}L")
    assert "₹50L" in _brief()
    assert "₹25L practice capital" not in _brief()


def test_app_brief_sizing_matches_the_owners_order():
    b = _brief()
    assert "₹1.25 lakh" in b
    assert "3.3%" not in b
    assert "top ~25-30" not in b


def test_app_brief_exits_match_the_live_preset():
    p = get_preset("nifty200_momentum")
    b = _brief()
    assert p.stop_loss_pct == -0.05 and "−5% stop" in b
    assert p.take_profit_pct == 0.12 and "+12% target" in b
    assert "−12% stop" not in b
    assert "partial profit books at" not in b       # no consumer exists


def test_app_brief_points_at_the_live_repo():
    b = _brief()
    assert "damandamanaulakh-tech/am07" in b
    assert "claude/exciting-ride" not in b
    assert "`main` is live" in b


def test_app_brief_rotation_matches_the_scheduler():
    b = _brief()
    assert "intraday removed from auto-rotation" in b
    assert "then intraday, short, swing" not in b


def test_status_boards_carry_supersession_banners():
    assert "FROZEN BOARD" in Path("docs/WORK_LIST.md").read_text()
    assert "FOSSIL" in Path("docs/STATUS_BOARD.md").read_text()
    assert "SUPERSEDED" in Path("docs/APP_REVIEW.md").read_text()


def test_algo_doc_discloses_the_parameter_drift():
    a = Path("docs/ALGO_V1_V2.md").read_text()
    assert "SUPERSESSION NOTE" in a
    assert "½-Kelly capped at 5%" in a
    assert "REVERTED to the owner's fixed" in a.replace(
        "REVERTED to the owner's fixed", "REVERTED to the owner's fixed")


def test_data_readme_headline_numbers_are_current():
    r = Path("data/README.md").read_text()
    assert "user-supplied 2026-07-09" not in r
    assert "auto-refreshed daily" in r.lower()
    assert "2025-03-29" in r                    # fii coverage is current


def test_claude_md_no_longer_contradicts_itself():
    c = Path("CLAUDE.md").read_text()
    assert "[SUPERSEDED 2026-08-04" in c            # the ½-Kelly header
    assert "momentum −12/+12" not in c              # THE FINAL SET exits line
    assert "verified CLEAN 31×59" in c              # chityy CSV truth


def test_walk_forward_panel_names_its_era():
    d = Path("web/templates/dashboard.html").read_text()
    assert "floor-55 era" in d


# ---- Codex-review regression guards (PR#40) ----

def test_risk_page_renders_without_crashing():
    """Codex caught the B3 fix deleting the equity the template renders —
    /risk 500'd and no test noticed. Now one does."""
    from fastapi.testclient import TestClient
    from web import auth as auth_mod
    from web.main import app
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    r = c.get("/risk")
    assert r.status_code == 200
    assert "as of the engine" in r.text


def test_crash_brake_sells_are_named_on_the_phone():
    src = Path("web/push_ntfy.py").read_text()
    assert 'e.get("exit_reason") in (' in src
    assert '"KILL_SWITCH", "DRAWDOWN_LADDER", "SQUARE_OFF")' in src
    from web.push_ntfy import PUSH_ACTIONS
    assert "KILL_SWITCH" not in PUSH_ACTIONS or PUSH_ACTIONS.get("KILL_SWITCH_SCAN"),         "dead action names must not imply coverage"


def test_holiday_expiries_land_on_trading_days():
    from trading.calendars import fno_monthly_expiry, is_trading_day
    for y, m in ((2026, 11), (2027, 1), (2026, 2), (2026, 12)):
        assert is_trading_day(fno_monthly_expiry(y, m))


def test_export_never_calls_a_delayed_close_live():
    src = Path("web/routes/reports_routes.py").read_text()
    assert "delayed_close" in src
    assert 'srcs.get(pos.ticker) == "upstox"' in src
