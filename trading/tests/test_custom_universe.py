"""Owner's custom 50-name universe (order 2026-08-20: "reduce the universe
to 50 share only but all fresh ... do not change codes and just work on 50
count, rest remains same").

What these tests pin: the list is exactly 50 real, tradeable NSE names with
the owner's resolutions honoured; the scan reads it verbatim (held names
ride along, the blocklist still strips, no rotation); clearing it restores
the rotating bucket unchanged; and the daily-freshness knob on the top-up
exists and defaults to the old weekly behaviour.
"""
from __future__ import annotations

import csv
from pathlib import Path

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio
from trading.universes import OWNER_UNIVERSE_2026_08_20 as U50


ROOT = Path(__file__).resolve().parents[2]


def _universe_file() -> set:
    with open(ROOT / "data" / "upstox_nse_equities.csv", newline="",
              encoding="utf-8") as f:
        return {r["ticker"] for r in csv.DictReader(f)}


def test_every_name_is_unique_and_tradeable():
    """The count is the LESS important half, so it is no longer pinned.

    It was 50, then 49 when NSE moved HFCL to the BE (trade-to-trade)
    series, then 83 when the owner ordered defence, shipbuilding and pharma
    added (2026-09-16). A hardcoded number turns each of those ordinary
    events into a meaningless test failure — the same fault as the
    hardcoded colspan (2026-09-11) and the hardcoded universe counts
    (2026-09-09).

    What must never break: no duplicates, and every auto-scanned name
    resolves in the tradeable file. A name that silently stops resolving
    would be scanned, scored and possibly bought against an instrument the
    app's own T2T rule excludes."""
    assert len(set(U50)) == len(U50), "duplicate names in the scanned list"
    assert len(U50) >= 40, "the scanned list collapsed"
    known = _universe_file()
    missing = [t for t in U50 if t not in known]
    assert not missing, f"not in the tradeable universe file: {missing}"


def test_every_scanned_name_carries_a_sector():
    """PR #68's lesson, re-pinned as the list grows: risk.can_open counts
    every "Unknown" position as ONE sector for max_sector_positions, so a
    single unmapped name would jam the cap against unrelated companies."""
    cfg = Config()
    cfg.apply_mode_preset()
    unmapped = [t for t in U50 if cfg.sector_map.get(t, "Unknown") == "Unknown"]
    assert not unmapped, f"no sector mapping for: {unmapped}"


def test_the_owner_ordered_themes_are_actually_present():
    """Owner order 2026-09-16, after the BRICS review found the scanned list
    had zero names in any of the three. Pins one unmistakable name per
    theme rather than a count, so the list can grow or be pruned freely."""
    for name in ("HAL.NS", "BEL.NS",              # defence
                 "COCHINSHIP.NS", "MAZDOCK.NS",   # shipbuilding yards
                 "SUNPHARMA.NS", "CIPLA.NS",      # pharma
                 # 2026-09-30 "add stock getting impacted with this market
                 # RBI and other impacts": rate-sensitives, crude, rupee,
                 # defensives, gold, power/capex — one name per leg.
                 "SBIN.NS", "BAJFINANCE.NS", "DLF.NS", "ULTRACEMCO.NS",
                 "ONGC.NS", "IOC.NS", "INFY.NS", "HINDUNILVR.NS",
                 "TITAN.NS", "NTPC.NS"):
        assert name in U50, f"{name} missing from the scanned list"
    # Names the Upstox master does not carry were NOT added on 2026-09-30
    # (an invented instrument key is the fabricated-data class).
    for absent in ("TATAMOTORS.NS", "HPCL.NS", "ZOMATO.NS"):
        assert absent not in U50
    # SCI is a shipping LINE, not a builder — it stays Logistics.
    cfg = Config()
    cfg.apply_mode_preset()
    assert cfg.sector_map.get("SCI.NS") == "Logistics"
    assert cfg.sector_map.get("APOLLO.NS") == "Defence"   # Micro Systems


def test_the_t2t_names_stay_out_of_the_auto_scanned_list():
    """MTARTECH (2026-08-20) and HFCL (2026-09-09) both left for the same
    reason. Re-adding either without NSE moving it back to EQ would put a
    trade-to-trade name in front of the engine."""
    from trading.universes import CUSTOM_UNIVERSE_SECTOR_MAP
    for t2t in ("MTARTECH.NS", "HFCL.NS"):
        assert t2t not in U50
    # HFCL keeps its sector so a held or hand-bought position still maps
    assert CUSTOM_UNIVERSE_SECTOR_MAP.get("HFCL.NS") == "Telecom"


def test_the_owners_resolutions_are_honoured():
    """The mappings the owner confirmed on 2026-08-20 — a future edit must
    not quietly swap one company for another."""
    for t in ("IOLCP.NS", "HDFCBANK.NS", "ABCAPITAL.NS", "ICICIBANK.NS",
              "ELGIEQUIP.NS", "USHAMART.NS", "GVT&D.NS", "KIRLPNU.NS",
              "MASTEK.NS", "MAXHEALTH.NS", "ITCHOTELS.NS", "BHARTIARTL.NS"):
        assert t in U50, t


def test_mtartech_is_excluded_because_it_is_trade_to_trade():
    """NSE lists MTARTECH in the BE series; the tradeable universe filters
    T2T names by design — it stays on the series-watch card instead."""
    assert "MTARTECH.NS" not in U50
    assert "MTARTECH.NS" not in _universe_file()


def test_the_blocklist_name_can_never_be_in_the_list():
    assert "YESBANK.NS" not in U50


def test_auto_and_metal_fill_per_the_owners_direction():
    """'Automobile & Metal is growing add accordingly' — the 17 fills."""
    auto = {"M&M.NS", "MARUTI.NS", "EICHERMOT.NS", "TVSMOTOR.NS",
            "ASHOKLEY.NS", "BHARATFORG.NS", "MOTHERSON.NS", "SONACOMS.NS",
            "UNOMINDA.NS"}
    metal = {"TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "JINDALSTEL.NS",
             "VEDL.NS", "NATIONALUM.NS", "APLAPOLLO.NS", "JSL.NS"}
    assert auto <= set(U50) and metal <= set(U50)
    assert len(auto) + len(metal) == 17


def test_config_defaults_to_the_owners_list():
    cfg = Config(state_dir="/tmp/sb_cu_test")
    assert tuple(cfg.custom_universe) == U50


# ---------------------------------------------------------------------------
# scan_universe honours the list
# ---------------------------------------------------------------------------

def _engine(tmp_path, custom=None) -> TradingEngine:
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    if custom is not None:
        cfg.custom_universe = custom
    return TradingEngine(cfg, Portfolio.fresh(cfg))


def test_scan_reads_exactly_the_custom_list(tmp_path):
    eng = _engine(tmp_path)
    universe, period = eng.scan_universe()
    assert universe == list(U50)
    assert period == "2y"                    # momentum history need unchanged


def test_held_names_ride_along_even_off_the_list(tmp_path):
    """Exits and correlation need the held name's history whatever the
    week's list says."""
    from datetime import date

    from trading.paper import PaperPosition
    eng = _engine(tmp_path)
    eng.portfolio.positions["OFFLIST.NS"] = PaperPosition(
        ticker="OFFLIST.NS", qty=10, entry_price=100.0,
        entry_date=date(2026, 8, 1), sector="Test", peak_price=100.0,
        entry_score=70.0, reasoning="fixture")
    universe, _ = eng.scan_universe()
    # derived, not a literal: the list shrank 50 -> 49 on 2026-09-09 when
    # NSE moved HFCL to the BE series, and a hardcoded count turns a real
    # market event into a test failure that says nothing useful.
    assert universe[0] == "OFFLIST.NS" and len(universe) == len(U50) + 1


def test_blocklist_still_strips_from_the_custom_list(tmp_path):
    eng = _engine(tmp_path, custom=("YESBANK.NS", "TCS.NS"))
    universe, _ = eng.scan_universe()
    assert universe == ["TCS.NS"]


def test_empty_custom_list_restores_the_rotating_bucket(tmp_path):
    eng = _engine(tmp_path, custom=())
    universe, _ = eng.scan_universe()
    assert len(universe) >= 40               # a rotation slice, not the 50
    assert universe != list(U50)


def test_custom_list_governs_every_mode_not_only_momentum(tmp_path):
    """CODEX REVIEW FIX (PR #68): the 09:20 rotation runs short/swing/
    positional too, and each can OPEN positions — a momentum-only
    restriction would have let the other passes buy outside the 50."""
    for mode in ("swing", "short", "positional"):
        cfg = Config(state_dir=str(tmp_path), mode=mode)
        eng = TradingEngine(cfg, Portfolio.fresh(cfg))
        universe, _ = eng.scan_universe()
        assert universe == list(U50), mode


def test_other_modes_keep_their_own_universe_when_custom_is_off(tmp_path):
    cfg = Config(state_dir=str(tmp_path), mode="swing")
    cfg.custom_universe = ()
    eng = TradingEngine(cfg, Portfolio.fresh(cfg))
    universe, _ = eng.scan_universe()
    assert universe == cfg.universe


def test_every_custom_name_has_a_real_sector(tmp_path):
    """CODEX REVIEW FIX (PR #68): risk.can_open counts all Unknown names as
    ONE sector for max_sector_positions — a book of unmapped small-caps
    would have hit sector_cap_reached on unrelated companies."""
    cfg = Config(state_dir=str(tmp_path))
    unmapped = [t for t in U50 if cfg.sector_map.get(t, "Unknown") == "Unknown"]
    assert not unmapped, unmapped
    # and the fills really lean Auto + Metal, as ordered
    from collections import Counter
    c = Counter(cfg.sector_map[t] for t in U50)
    assert c["Metal"] >= 8 and c["Auto"] >= 9


# ---------------------------------------------------------------------------
# the "all fresh" knob
# ---------------------------------------------------------------------------

def test_topup_fresh_window_is_configurable_and_defaults_weekly():
    import inspect

    from trading.history_store import top_up_history
    sig = inspect.signature(top_up_history)
    assert sig.parameters["fresh_within_days"].default == 4


def test_daily_freshness_retops_a_doc_the_weekly_pass_would_skip():
    from datetime import date, timedelta

    from trading.history_store import top_up_history

    class _B:
        calls = 0

        def get_daily_candles(self, key, start, end):
            _B.calls += 1
            import pandas as pd
            idx = pd.to_datetime([date.today() - timedelta(days=1)])
            return pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0],
                                 "Close": [1.0], "Volume": [0.0]}, index=idx)

    today = date.today()
    two_days_old = {"_id": "X.NS", "ticker": "X.NS", "n": 1,
                    "candles": [[(today - timedelta(days=2)).isoformat(),
                                 1, 1, 1, 1, 0]],
                    "start": (today - timedelta(days=2)).isoformat(),
                    "end": (today - timedelta(days=2)).isoformat()}
    saved = []
    # weekly tolerance (4d): a 2-day-old doc reads fresh — skipped
    s1 = top_up_history(_B(), {"X.NS": "k"}, lambda t: dict(two_days_old),
                        saved.append, today)
    assert s1["skipped_fresh"] == 1 and not saved
    # daily tolerance (1d): the same doc is stale — refreshed
    s2 = top_up_history(_B(), {"X.NS": "k"}, lambda t: dict(two_days_old),
                        saved.append, today, fresh_within_days=1)
    assert s2["ok"] == 1 and saved
