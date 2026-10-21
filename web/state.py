"""Shared in-process trading state. Single Portfolio + Engine for the app.

Persistence: if MONGO_URL env var is set, settings overrides + closed trades
are mirrored to MongoDB Atlas. JSON-on-disk remains the source-of-truth for
the in-progress Portfolio so the app keeps working when Mongo is down.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import ClosedTrade, PaperPosition, Portfolio

from . import db


log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CFG: Optional[Config] = None
_PORTFOLIO: Optional[Portfolio] = None
_ENGINE: Optional[TradingEngine] = None
# Monkey-patched flag so trading/paper.py stays pure; web/state.py wraps save()
_PORTFOLIO_SAVE_PATCHED = False


def _hydrate_portfolio_from_mongo(p: Portfolio, doc: dict) -> None:
    """Replace JSON-loaded portfolio fields from a Mongo doc."""
    p.cash = float(doc.get("cash", p.cash))
    p.positions = {
        k: PaperPosition.from_dict(v) for k, v in (doc.get("positions") or {}).items()
    }
    p.trades = [ClosedTrade.from_dict(t) for t in (doc.get("trades") or [])]
    p.equity_history = list(doc.get("equity_history", []))
    p.last_scan = doc.get("last_scan")
    p.last_scan_live = doc.get("last_scan_live")
    p.last_scan_picks = list(doc.get("last_scan_picks", []))
    p.last_scan_skipped = list(doc.get("last_scan_skipped", []))
    p.last_scan_opened = list(doc.get("last_scan_opened", []))
    p.scan_matrix = dict(doc.get("scan_matrix", {}))
    p.momentum_shortlist = dict(doc.get("momentum_shortlist", {}) or {})
    p.rotation_report = dict(doc.get("rotation_report", {}) or {})
    p.last_regime = doc.get("last_regime")
    p.last_ifr_fire_date = doc.get("last_ifr_fire_date")
    p.last_ifr_ratio_fire_date = doc.get("last_ifr_ratio_fire_date")
    p.regime_history = list(doc.get("regime_history", []))
    p.scan_history = list(doc.get("scan_history", []))
    p.decision_journal = list(doc.get("decision_journal", []))
    p.virtual_book = dict(doc.get("virtual_book", {}))
    p.pending_orders = list(doc.get("pending_orders", []))
    p.filings = list(doc.get("filings", []))
    # Daily kill-switch state — Mongo is the persistence that actually
    # survives a Render restart (/tmp JSON is wiped), so these MUST ride
    # in the mirror doc or the risk-day fix silently dies on redeploy.
    p.risk_day = doc.get("risk_day")
    p.risk_day_equity = float(doc.get("risk_day_equity", 0.0) or 0.0)
    p.risk_day_killed = bool(doc.get("risk_day_killed", False))
    # Legacy mirror docs predate capital_base. Owner order 2026-08-30
    # ("remove the 25 Lacs thing from the entire history"): the fallback is
    # the LIVE capital, so a pre-capital_base doc can no longer resurrect a
    # superseded book size on a restart.
    #
    # FIXED 2026-09-10: this was HARDCODED to Rs 50,00,000, which was the live
    # capital when the line was written and stopped being it the moment the
    # owner raised the book to Rs 5 crore. A legacy doc would then have read
    # as "base 50L vs configured 5Cr" and sync_capital would have injected a
    # phantom +4.5 crore of cash. Read the live config — the same fault the
    # 25L fallback caused, one book size later.
    _live_base = float(getattr(p.cfg, "starting_capital", 0.0) or 0.0)
    p.capital_base = float(doc.get("capital_base") or _live_base)
    p.dd_peak_equity = float(doc.get("dd_peak_equity", 0.0) or 0.0)
    p.dd_cut_level = str(doc.get("dd_cut_level") or "normal")
    p.loss_day = doc.get("loss_day")
    p.loss_count = int(doc.get("loss_count", 0) or 0)
    # owner order 2026-10-21: per-category losers ride the mirror BOTH ways
    p.loss_by_category = {str(k): int(v) for k, v in
                          (doc.get("loss_by_category") or {}).items()}
    p.trigger_watch = dict(doc.get("trigger_watch", {}) or {})
    # Mirror BOTH ways or a Render restart silently loses it — the risk_day
    # lesson from 2026-07-27.
    p.news_watch = dict(doc.get("news_watch", {}) or {})
    p.universe_batch = dict(doc.get("universe_batch", {}) or {})
    p.universe_ledger = list(doc.get("universe_ledger", []) or [])
    p.breadth_store = dict(doc.get("breadth_store", {}) or {})
    p.breadth_today = dict(doc.get("breadth_today", {}) or {})
    p.option_chain = dict(doc.get("option_chain", {}) or {})
    p.register_market = dict(doc.get("register_market", {}) or {})
    p.damage_store = dict(doc.get("damage_store", {}) or {})
    p.danger_alerts_start = doc.get("danger_alerts_start")
    p.last_save = doc.get("last_save")


def _patch_portfolio_save_to_mirror_mongo() -> None:
    """Wrap Portfolio.save() once so every save mirrors to Mongo (when connected)."""
    global _PORTFOLIO_SAVE_PATCHED
    if _PORTFOLIO_SAVE_PATCHED:
        return
    original_save = Portfolio.save

    def _archive_trade_overflow(self: Portfolio) -> None:
        """AUDIT M-10: move closed trades beyond TRADES_DOC_CAP to the
        `trades` archive, and trim the book ONLY when that write succeeded.
        Mongo down = keep everything in the book; the doc grows a little
        until the next save that can archive. A trade is never lost."""
        from trading.paper import TRADES_DOC_CAP
        if len(self.trades) <= TRADES_DOC_CAP or not db.is_connected():
            return
        overflow = self.trades[:-TRADES_DOC_CAP]
        if db.archive_trades([t.to_dict() for t in overflow]):
            self.trades = self.trades[-TRADES_DOC_CAP:]

    def _save_with_mirror(self: Portfolio) -> None:
        # Durable-archive the journal overflow BEFORE original_save trims RAM,
        # so the full append-only trail survives in Mongo (journal_archive)
        # while RAM stays bounded on the always-on process.
        from trading.paper import JOURNAL_RAM_CAP
        if len(self.decision_journal) > JOURNAL_RAM_CAP:
            overflow = self.decision_journal[:-JOURNAL_RAM_CAP]
            # AUDIT FIX C2 (2026-08-09): if Mongo dies mid-session the archive
            # used to fail with a log line while original_save trimmed the RAM
            # copy — append-only journal entries were PERMANENTLY lost. Failed
            # archives now spill to a local JSONL and replay on reconnect.
            if not db.archive_journal(overflow):
                _spill_journal_overflow(self, overflow)
        _archive_trade_overflow(self)
        original_save(self)
        if not db.is_connected():
            return
        doc = {
            "cash": self.cash,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "trades": [t.to_dict() for t in self.trades],
            "equity_history": self.equity_history,
            "last_scan": self.last_scan,
            "last_scan_live": getattr(self, "last_scan_live", None),
            "last_scan_picks": self.last_scan_picks,
            "last_scan_skipped": self.last_scan_skipped,
            "last_scan_opened": self.last_scan_opened,
            "last_regime": self.last_regime,
            "last_ifr_fire_date": self.last_ifr_fire_date,
            "last_ifr_ratio_fire_date": self.last_ifr_ratio_fire_date,
            "regime_history": self.regime_history[-200:],
            "scan_history": self.scan_history[-500:],
            # the recent tail: save() trims RAM to JOURNAL_RAM_CAP (5,000)
            # lines after archiving the overflow to journal_archive above
            # (audit S-14: this said "full, never truncated")
            "decision_journal": self.decision_journal,
            "virtual_book": self.virtual_book,
            "scan_matrix": self.scan_matrix,
            "momentum_shortlist": getattr(self, "momentum_shortlist", {}) or {},
            "rotation_report": getattr(self, "rotation_report", {}) or {},
            "pending_orders": self.pending_orders[-200:],
            "filings": self.filings[:100],
            "risk_day": self.risk_day,
            "risk_day_equity": self.risk_day_equity,
            "risk_day_killed": self.risk_day_killed,
            "capital_base": self.capital_base,
            "dd_peak_equity": self.dd_peak_equity,
            "dd_cut_level": getattr(self, "dd_cut_level", "normal"),
            "loss_day": self.loss_day,
            "loss_count": self.loss_count,
            "loss_by_category": getattr(self, "loss_by_category", {}) or {},
            "trigger_watch": self.trigger_watch,
            "news_watch": self.news_watch,
            "universe_batch": self.universe_batch,
            "universe_ledger": self.universe_ledger[-1500:],
            "breadth_store": self.breadth_store,
            "breadth_today": self.breadth_today,
            "option_chain": self.option_chain,
            "register_market": self.register_market,
            "damage_store": self.damage_store,
            "danger_alerts_start": self.danger_alerts_start,
            "last_save": self.last_save,
        }
        db.save_portfolio(doc)

    Portfolio.save = _save_with_mirror  # type: ignore[assignment]
    _PORTFOLIO_SAVE_PATCHED = True


def _overflow_path(p: Portfolio) -> str:
    import os
    return os.path.join(p.cfg.state_dir, "journal_overflow.jsonl")


def _spill_journal_overflow(p: Portfolio, entries: list) -> None:
    """Durable-ish local backup for journal entries the Mongo archive missed."""
    import json as _json
    try:
        with open(_overflow_path(p), "a") as f:
            for e in entries:
                f.write(_json.dumps(e, default=str) + "\n")
        log.warning("journal overflow spilled to disk (%d entries) — will "
                    "replay to Mongo on reconnect", len(entries))
    except OSError as e:
        log.error("journal overflow spill FAILED (%d entries at risk): %s",
                  len(entries), e)


def mongo_degraded() -> bool:
    """The owner configured Mongo and it is currently unreachable."""
    return db.mongo_expected() and not db.is_connected()


def heal_after_reconnect() -> None:
    """AUDIT FIX C1/C2 (2026-08-09): one-shot repair after Mongo comes back.

    (1) Replays any spilled journal overflow into the durable archive.
    (2) If the BOOT happened during the outage (phantom fresh book) and the
        local book is still virgin — no positions, no trades — hydrate the
        real book from Mongo instead of overwriting it at the next save.
        A local book that already has activity is NEVER clobbered; the
        conflict is journaled for the owner instead.
    """
    if not db.recovered_pending():
        return
    db.claim_recovery()
    p = portfolio()
    import json as _json
    import os
    path = _overflow_path(p)
    if os.path.exists(path):
        try:
            with open(path) as f:
                entries = [_json.loads(line) for line in f if line.strip()]
            if not entries or db.archive_journal(entries):
                os.remove(path)
                log.info("replayed %d spilled journal entries to Mongo",
                         len(entries))
        except (OSError, ValueError) as e:
            log.error("overflow replay failed (kept on disk): %s", e)

    from datetime import datetime as _dt
    if db.boot_was_degraded():
        virgin = not p.positions and not p.trades
        persisted = db.load_portfolio()
        if virgin and persisted:
            _hydrate_portfolio_from_mongo(p, persisted)
            p.decision_journal.append({
                "ts": _dt.utcnow().isoformat(), "action": "MONGO_RECOVERED",
                "why": ("Boot happened during a Mongo outage; the real book "
                        "has been restored from Mongo (local book was empty)."),
            })
        elif persisted:
            p.decision_journal.append({
                "ts": _dt.utcnow().isoformat(),
                "action": "MONGO_RECOVERED_CONFLICT",
                "why": ("Mongo is back but this session already traded on a "
                        "fresh book started during the outage. NOTHING was "
                        "overwritten — owner must reconcile (the Mongo book "
                        "is untouched until the next save)."),
            })
    p.save()


def _coerce_override(cfg, key: str, value):
    """Heal type-poisoned Mongo overrides (recheck fix, 2026-07-27).

    Before the settings bool fix, 1/0 toggles were stored as the STRING "0"
    (truthy). Applying such a stored override raw re-poisons the config on
    every boot, and the settings-save bool branch can't heal it because
    isinstance(existing, bool) is False once the string is in place. Coerce
    strings back to the Config field's real type here, at the only door
    stored overrides enter through."""
    expected = getattr(cfg, key, None)
    if isinstance(expected, bool) and isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(expected, (int, float)) and not isinstance(expected, bool) \
            and isinstance(value, str):
        try:
            return type(expected)(float(value))
        except ValueError:
            return value
    # custom_universe (2026-08-20): stored as a JSON list, the Config field
    # is a tuple — coerce so `if custom:` and equality checks behave.
    if isinstance(expected, tuple) and isinstance(value, list):
        return tuple(value)
    return value


def _migrate_slab_restore(cfg) -> bool:
    """One-time: clear a stored sizing_mode "fixed" left by the Settings-save
    bug (fixed 2026-10-04 — every save rewrote "slab" to "fixed"). Returns
    True when it restored slab. Marker in the migrations doc."""
    if db.load_settings(doc_id="migrations").get("slab_restore_2026_10_04"):
        return False
    restored = False
    if str(db.load_settings().get("sizing_mode", "")).lower() == "fixed":
        db.unset_settings_keys(["sizing_mode"])
        cfg.sizing_mode = "slab"
        restored = True
        log.warning("sizing_mode: stored 'fixed' (Settings-save bug) "
                    "cleared — slab sizing restored")
    db.save_settings({"slab_restore_2026_10_04": True}, doc_id="migrations")
    return restored


PRE_MARS_MARKER = "archive_pre_mars_2026_09_15"


def _migrate_archive_pre_mars(p: Portfolio) -> dict:
    """OWNER ORDER 2026-10-11, item 5: "remove the entries before 15
    September, any file u need for back or data, keep rest needs to rest".

    ARCHIVE, NEVER DELETE — standing rule 3 says the journal is append-only,
    and the closed trades are the evidence behind every measured number.
    Closed trades whose exit is before 2026-09-15 00:00 IST and journal lines
    stamped before it move to the Mongo archive collections (`trades`,
    `journal_archive`) flagged `pre_mars: True`, which every live reader
    skips by default (web/db.py). Only AFTER the archive write is read back
    with at least as many flagged rows as were sent does the live book drop
    them. Mongo down, write refused, or a short read-back = nothing trimmed,
    no marker, retried on the next boot. Git history is untouched.

    Returns {trades, journal, done} for the journal line and the tests."""
    from datetime import datetime as _dt, timedelta as _td

    out = {"trades": 0, "journal": 0, "done": False}
    if db.load_settings(doc_id="migrations").get(PRE_MARS_MARKER):
        return out
    if not db.is_connected():
        return out
    # 2026-09-15 00:00 IST = 2026-09-14 18:30 UTC; stamps are naive UTC.
    cut_utc = _dt(2026, 9, 15) - _td(hours=5, minutes=30)
    old_trades = [t for t in p.trades
                  if getattr(t, "exit_date", None) is not None
                  and t.exit_date < cut_utc]
    old_journal = [e for e in p.decision_journal
                   if str(e.get("ts", ""))[:19] < cut_utc.isoformat()[:19]]
    t_docs = [{**t.to_dict(), db.PRE_MARS_FLAG: True} for t in old_trades]
    j_docs = [{**dict(e), db.PRE_MARS_FLAG: True} for e in old_journal]
    if t_docs and not db.archive_trades(t_docs):
        log.warning("pre-MARS archive: trade write failed — nothing trimmed")
        return out
    if j_docs and not db.archive_journal(j_docs):
        log.warning("pre-MARS archive: journal write failed — nothing trimmed")
        return out
    # Read back before trimming (the save-then-verify rule of the sequence).
    if t_docs and db.count_pre_mars("trades") < len(t_docs):
        log.warning("pre-MARS archive: trade read-back short — nothing trimmed")
        return out
    if j_docs and db.count_pre_mars("journal_archive") < len(j_docs):
        log.warning("pre-MARS archive: journal read-back short — nothing trimmed")
        return out
    keep_t = [t for t in p.trades if t not in old_trades]
    keep_j = [e for e in p.decision_journal if e not in old_journal]
    p.trades = keep_t
    p.decision_journal = keep_j
    out.update({"trades": len(t_docs), "journal": len(j_docs), "done": True})
    p.decision_journal.append({
        "ts": _dt.utcnow().isoformat(), "action": "MARS_ARCHIVE_PRE_2026_09_15",
        "why": (f"Owner order 2026-10-11 item 5: {len(t_docs)} closed trades "
                f"and {len(j_docs)} journal lines dated before 2026-09-15 IST "
                "moved to the Mongo archive (pre_mars flag), read back, then "
                "left the live book. Nothing deleted; '?archive=1' on /trades "
                "shows them.")})
    db.save_settings({PRE_MARS_MARKER: True}, doc_id="migrations")
    return out


def _bootstrap() -> None:
    global _CFG, _PORTFOLIO, _ENGINE
    _CFG = Config()
    # Apply Mongo-persisted settings overrides (mode + tunables)
    overrides = db.load_settings()
    for key, value in overrides.items():
        if hasattr(_CFG, key):
            try:
                setattr(_CFG, key, _coerce_override(_CFG, key, value))
            except Exception:
                pass
    # Owner order 2026-07-30 ("change the capital to 50 lack, 80 stocks,
    # roughly 1-1.25 for each trade — change it now"; corrected same day:
    # "1.25 lac, 125000, not percentage"): applied ONCE over any stale saved
    # overrides, marker in the separate `migrations` settings doc (the
    # Settings form replaces the `default` doc, so a marker there would not
    # survive the next save). v2 supersedes the same-day v1 (which read the
    # size as 1.25%). Future Settings edits stay fully free.
    if not db.load_settings(doc_id="migrations").get("capital_order_2026_07_30_v2"):
        _CFG.starting_capital = 50_00_000.0
        _CFG.max_positions = 80
        _CFG.position_size_value = 125_000.0
        _CFG.max_position_pct = 0.025
        db.save_settings({**overrides, "starting_capital": 50_00_000.0,
                          "max_positions": 80, "position_size_value": 125_000.0,
                          "max_position_pct": 0.025})
        db.save_settings({"capital_order_2026_07_30": True,
                          "capital_order_2026_07_30_v2": True},
                         doc_id="migrations")

    # Owner order 2026-08-04 ("still u r making 30000 positions"): back to the
    # FIXED Rs 1.25 lakh per trade they actually asked for on 2026-07-30
    # ("1.25 lac, 125000, not percentage"). Half-Kelly was adopted from MY
    # question, and at scores just over the 62 floor it sizes Rs 20k-60k
    # (measured: score 63 -> Rs 20,719 · 65 -> Rs 62,158) — which is the
    # "30000 positions" they are seeing. Their written number wins over my
    # maths. Kelly stays available in Settings (sizing_mode=half_kelly).
    if not db.load_settings(doc_id="migrations").get("fixed_size_order_2026_08_04"):
        _CFG.sizing_mode = "fixed"
        _CFG.position_size_value = 125_000.0
        db.save_settings({**db.load_settings(), "sizing_mode": "fixed",
                          "position_size_value": 125_000.0})
        db.save_settings({"fixed_size_order_2026_08_04": True},
                         doc_id="migrations")

    # Owner order 2026-08-24 (Node 12): exit bracket -5/+20/trail-15 — the
    # owner picked "Switch to D" after both exit studies (floor-55 committed
    # + floor-62 live-era rerun) showed it beating +12 in all four windows.
    # The preset now carries +20; this one-time migration clears any STORED
    # exit override (top-level settings or per-mode preset_overrides) so a
    # pre-order Settings edit can't silently pin the superseded +12.
    if not db.load_settings(doc_id="migrations").get("exit_order_2026_08_24"):
        db.unset_settings_keys(["take_profit_pct", "trailing_stop_pct"])
        po = dict(db.load_settings().get("preset_overrides") or {})
        mo = {k: v for k, v in dict(po.get("nifty200_momentum") or {}).items()
              if k not in ("take_profit_pct", "trailing_stop_pct")}
        if mo:
            po["nifty200_momentum"] = mo
        else:
            po.pop("nifty200_momentum", None)
        db.save_settings({"preset_overrides": po})
        _CFG.preset_overrides = po
        db.save_settings({"exit_order_2026_08_24": True}, doc_id="migrations")

    # Owner approval 2026-08-26 ("Approve as specced"): Ocean un-parked
    # behind the crash guard. The park era stored ocean_enabled=False in
    # Mongo; this one-time migration clears that override so the new
    # default (ON) rules. The Settings toggle still works afterwards.
    if not db.load_settings(doc_id="migrations").get("ocean_order_2026_08_26"):
        db.unset_settings_keys(["ocean_enabled"])
        _CFG.ocean_enabled = True
        db.save_settings({"ocean_order_2026_08_26": True},
                         doc_id="migrations")

    # Owner order 2026-09-09 ("fix it"): the breadth gate is a measured DEAD
    # BAND (40-65%), not a "block below 50%" rule. Any stored breadth_min_pct
    # from the old shape is dead config now; clear it so a Settings edit made
    # under the superseded rule cannot linger, and clear any stored band
    # values so the measured defaults rule on the first boot after the change.
    if not db.load_settings(doc_id="migrations").get("breadth_band_2026_09_09"):
        db.unset_settings_keys(["breadth_min_pct", "breadth_block_low",
                                "breadth_block_high"])
        _CFG.breadth_block_low = 0.40
        _CFG.breadth_block_high = 0.65
        db.save_settings({"breadth_band_2026_09_09": True},
                         doc_id="migrations")

    # OWNER BATCH 2026-09-10. Capital Rs 50L -> Rs 5 crore, ceiling 80 -> 222,
    # and the SLAB sizing model (Rs 1.5 lakh floor, score-driven above it, at
    # m = 0.29). Stored overrides from the fixed-size era would otherwise pin
    # the old numbers on the live box, so they are unset rather than left to
    # win — save_settings is a $set merge and cannot delete on its own.
    #
    # The cash move itself is NOT done here: _PORTFOLIO does not exist yet at
    # this point in the boot (it loads further down), and the existing
    # sync_capital call after it already moves cash by the delta and journals
    # CAPITAL_ADJUST. This migration only has to make sure the CONFIG says
    # Rs 5 crore before that call runs, and that no stored override from the
    # fixed-size era survives to overwrite it.
    if not db.load_settings(doc_id="migrations").get("owner_batch_2026_09_10"):
        db.unset_settings_keys([
            "starting_capital", "max_positions", "sizing_mode",
            "kelly_fraction", "position_slab_value", "position_size_value",
            "universe_target_size", "market_hours_only",
            "intraday_refresh_minutes", "rotation_cash_trigger_pct",
        ])
        _CFG.starting_capital = 5_00_00_000.0
        _CFG.max_positions = 222
        _CFG.sizing_mode = "slab"
        _CFG.position_slab_value = 150_000.0
        _CFG.kelly_fraction = 0.29
        _CFG.universe_target_size = 1200
        _CFG.market_hours_only = True
        _CFG.intraday_refresh_minutes = 30
        _CFG.rotation_cash_trigger_pct = 0.05
        db.save_settings({"owner_batch_2026_09_10": True},
                         doc_id="migrations")

    # FIX 2026-10-04: the Settings save rewrote sizing_mode "slab" -> "fixed"
    # on EVERY save (its validation list never learned "slab"), so any save
    # since 2026-09-10 left a stored "fixed" pinning flat Rs 1.25 lakh trades
    # against the owner's written order ("no flat size to follow"). The owner
    # never ordered fixed after that date, so a stored "fixed" is the bug's
    # footprint: cleared once, and slab rules again. The Settings field still
    # accepts "fixed" for a deliberate choice made after this boot.
    _migrate_slab_restore(_CFG)

    # Apply mode preset (sets stops, target, etc. per chosen horizon)
    _CFG.apply_mode_preset()
    # Policy C (user-approved 2026-07-16): exits ON — pinned here so a stale
    # `disable_exits` in an old Mongo settings doc can never re-freeze exits.
    _CFG.disable_exits = False

    # Node 3 (2026-08-23): hand flows.py its Mongo FII-store reader — the
    # broker-attach pattern, so the trading module never imports web.db.
    try:
        from trading.signals import flows as _flows
        _flows.set_store_reader(db.load_fii_rows)
    except Exception:
        log.exception("FII store reader attach failed")

    # Node 3 rest (2026-08-24): same pattern for the index-daily tail and
    # the fundamentals overlay — trading modules never import web.db.
    try:
        from trading import data_refresh as _dr
        _dr.set_index_reader(db.load_index_rows)
        _dr.set_fundamentals_reader(db.load_fundamentals_rows)
        _dr.set_pwoi_reader(db.load_pwoi_rows)
    except Exception:
        log.exception("data_refresh reader attach failed")

    # VALFIX 2026-10-21: the valuation chain's macro feeds (10-year yield,
    # RBI repo, RBI USD/INR) read their stored rows through this reader.
    try:
        from trading import macro_feeds as _mf
        _mf.set_reader(db.load_macro_rows)
    except Exception:
        log.exception("macro_feeds reader attach failed")
    # Owner order 2026-10-21 (the BLSE split): the engine's ex-date adjuster
    # reads the daily NSE corporate-action fetch from Mongo through this
    # reader; unattached or down = the committed CA file alone.
    try:
        from trading import corporate_actions as _tca
        _tca.set_store_reader(db.load_corporate_actions)
    except Exception:
        log.exception("corporate-actions store reader attach failed")

    # 2026-09-11 (owner: "why system still stucking at 49"): the scan reads
    # the OHLCV store FIRST and only falls back to the network for what the
    # store lacks or holds stale. Without this reader every name was fetched
    # one request at a time — ~3 hours for a 1,200-name universe, which is
    # why the universe could never grow. Same injection pattern as above.
    try:
        from trading import data as _tdata
        _tdata.set_history_store(db.history_docs)
    except Exception:
        log.exception("history store reader attach failed")

    # Load portfolio: prefer Mongo (survives restarts), fall back to JSON
    _PORTFOLIO = Portfolio.load_or_fresh(_CFG)
    persisted = db.load_portfolio()
    if persisted:
        _hydrate_portfolio_from_mongo(_PORTFOLIO, persisted)

    # Wire save() to mirror to Mongo from now on
    _patch_portfolio_save_to_mirror_mongo()

    # Owner order 2026-10-11 item 5: pre-15-September trades and journal
    # lines leave the live book for the archive (never deleted). One-time,
    # marker in the migrations doc, retried on the next boot if Mongo was
    # down or the read-back came up short.
    try:
        if _migrate_archive_pre_mars(_PORTFOLIO).get("done"):
            _PORTFOLIO.save()
    except Exception:
        log.exception("pre-MARS archive migration failed — nothing trimmed")

    # Owner order 2026-07-30: cash follows starting_capital (delta, journaled).
    if _PORTFOLIO.sync_capital(_CFG):
        _PORTFOLIO.save()

    # Policy C migration: re-snapshot every open position's stop/target from
    # its own mode's table (stale intraday / blank tags -> momentum preset).
    # Journaled per position; idempotent, so later boots are no-ops.
    try:
        from trading.exits import retag_positions_per_mode
        if retag_positions_per_mode(_PORTFOLIO) > 0:
            _PORTFOLIO.save()
    except Exception:
        log.exception("Policy C retag failed — positions keep old stops")

    _ENGINE = TradingEngine(_CFG, _PORTFOLIO)

    # Attach Upstox broker if a token is persisted in Mongo
    _attach_broker_if_persisted()


def _attach_broker_if_persisted() -> None:
    """Build a UpstoxClient from the Mongo-persisted token and attach it
    to the engine so refresh() uses real-time quotes."""
    if _ENGINE is None:
        return
    try:
        from trading.brokers.upstox import UpstoxClient, UpstoxConfigError
    except ImportError:
        return
    try:
        client = UpstoxClient.from_env()
    except UpstoxConfigError:
        return
    token_doc = db.load_upstox_token()
    if token_doc and token_doc.get("access_token"):
        client.access_token = token_doc.get("access_token")
        client.user_id = token_doc.get("user_id")
        client.user_name = token_doc.get("user_name")
        client.connected_at = token_doc.get("connected_at")
        _ENGINE.attach_broker(client)
    # Route ALL historical candles through Upstox too (Yahoo 429-blocks
    # cloud IPs; scans/backtests starve on yfinance alone).
    #
    # ROOT CAUSE OF "prices are still not live" (owner, 2026-08-20): this
    # attach used to RETURN EARLY when no token was stored — so before the
    # day's login (the token dies at 03:30 IST) there was NO client at all,
    # every historical fetch fell to Yahoo's 429 wall, and scans/charts
    # starved even though Upstox HISTORICAL candles are public and work
    # TOKENLESS (F1 fix). The history client is now attached with or
    # without a token; only LIVE quotes still wait for the daily login —
    # that is an Upstox rule, not ours.
    from trading import data as trading_data
    trading_data.set_history_broker(client)


def refresh_broker_attachment() -> None:
    """Public hook: re-attach broker after a fresh OAuth callback or disconnect."""
    with _LOCK:
        if _ENGINE is None:
            return
        _attach_broker_if_persisted()
        # If Mongo cleared the token, the engine forgets it (live quotes are
        # honestly absent until the next login) — but the HISTORY client
        # stays: the candle endpoint is public, so a logout must not send
        # history fetching back to Yahoo's 429 wall.
        if db.load_upstox_token() is None:
            _ENGINE.attach_broker(None)


# {ticker: (price, fetched_at_epoch)} — per-ticker timestamps, because a
# single cache-wide timestamp let dead prices masquerade as live (see below).
_MARKS_LOCK = threading.Lock()   # AUDIT E: the cache was mutated lock-free across scheduler+request threads
_MARKS_CACHE: dict = {"prices": {}}
MARKS_MAX_AGE_SEC = 120.0        # older than this is NOT a live price


def marks(tickers: list[str], max_age: float = MARKS_MAX_AGE_SEC
          ) -> tuple[dict, bool]:
    """Latest prices for tickers. Returns (prices, live).

    FIXED 2026-07-31 — the owner caught the app showing TCS at a price that
    was not the market. The old cache kept one shared timestamp and, when a
    quote fetch FAILED, still returned every previously-cached price and
    reported live=True. A quote pulled hours (or a session) earlier was
    therefore displayed as real-time. That is how a wrong price reached the
    screen while the header claimed "Upstox real-time".

    Now: every price carries its own fetch time, anything older than
    `max_age` is DISCARDED rather than served, and `live` is True only for
    prices actually fresh. Callers fall back to entry marks — clearly
    labelled — instead of trusting a corpse.
    """
    import time
    if not tickers:
        return {}, False
    now = time.time()
    cache: dict = _MARKS_CACHE["prices"]

    # AUDIT E (2026-08-09): all cache reads/writes are under _MARKS_LOCK now —
    # the old lock-free dict was mutated by scheduler threads while a request
    # iterated it, which could 500 the positions page with "dict changed size
    # during iteration".
    with _MARKS_LOCK:
        for t in [k for k, (_p, ts) in list(cache.items()) if now - ts > max_age]:
            cache.pop(t, None)
        need = [t for t in tickers if t not in cache]

    if need:
        try:
            from trading.quote_router import fetch_quotes as _rq
            got = _rq(need, upstox_client=engine().broker)
        except Exception:
            got = {}
        with _MARKS_LOCK:
            for t, px in (got or {}).items():
                if px and px > 0:
                    cache[t] = (float(px), now)

    with _MARKS_LOCK:
        out = {t: cache[t][0] for t in tickers if t in cache}
    return out, bool(out)


def marks_sources(tickers) -> dict:
    """{ticker: 'upstox'|'yf_close'} for the tickers we have prices for —
    External review on PR#40: an export must know whether a mark was a real
    live tick or a delayed daily close."""
    try:
        from trading.quote_router import LAST_SOURCES
        return {t: LAST_SOURCES.get(t) for t in tickers if t in LAST_SOURCES}
    except ImportError:
        return {}


def marks_age(tickers: list[str]) -> float | None:
    """Seconds since the OLDEST live price among `tickers` was fetched.
    None when nothing live is cached. Lets the UI state the age instead of
    implying freshness."""
    import time
    cache = _MARKS_CACHE["prices"]
    ages = [time.time() - ts for t, (_p, ts) in cache.items() if t in set(tickers)]
    return max(ages) if ages else None


# {key: (rows, fetched_at)} — per-key timestamps. A single shared timestamp
# had the same defect as the old marks cache: any later fetch refreshed the
# clock for EVERY entry, so old candles kept looking fresh indefinitely.
_CANDLE_CACHE: dict = {"data": {}}
_CANDLE_TTL = 600.0


def recent_candles(ticker: str = "^NSEI", bars: int = 40) -> list[dict]:
    """F17: last `bars` daily OHLC for a candle chart. 10-min cached, Upstox-
    first via the history router. Returns [] if no data (chart hides itself).

    A FAILED fetch is never cached (2026-07-31): caching the empty result
    blanked the chart for the full TTL after one transient error, and made a
    failure indistinguishable from "this stock genuinely has no data"."""
    import time
    now = time.time()
    key = f"{ticker}:{bars}"
    hit = _CANDLE_CACHE["data"].get(key)
    if hit is not None and (now - hit[1]) < _CANDLE_TTL:
        return hit[0]
    out: list[dict] = []
    try:
        from trading import data as _data
        hist = _data.fetch_daily([ticker], period="4mo").get(ticker)
        if hist is not None and not hist.empty:
            tail = hist.dropna(subset=["Close"]).tail(bars)
            for ts, row in tail.iterrows():
                out.append({
                    "t": ts.strftime("%d-%b") if hasattr(ts, "strftime") else str(ts),
                    "o": round(float(row["Open"]), 2),
                    "h": round(float(row["High"]), 2),
                    "l": round(float(row["Low"]), 2),
                    "c": round(float(row["Close"]), 2),
                })
    except Exception:
        pass
    if out:                     # only cache a real result, never a failure
        _CANDLE_CACHE["data"][key] = (out, now)
    return out


def attach_broker_client(client) -> None:
    """Hand the engine a client the OAuth callback just built — the token
    goes to the engine IN MEMORY, never via a Mongo round trip.

    OWNER FAULT 2026-09-24 ("upstox is not getting connected ... still
    red"): the callback used to save the token to Mongo and then have the
    engine READ IT BACK. On the live box that read returned yesterday's
    document after every fresh login (the write was not landing), so the
    engine ran on a dead token — red "token EXPIRED" bar, "no live quote"
    on every index card, a Reconnect link that repeated the whole loop —
    while the browser cookie proudly showed the owner's name. Mongo is for
    surviving a restart; the running process gets the token directly.
    """
    with _LOCK:
        if _ENGINE is None:
            _bootstrap()
        assert _ENGINE is not None
        _ENGINE.attach_broker(client)
        try:
            from trading import data as trading_data
            trading_data.set_history_broker(client)
        except Exception:                                     # noqa: BLE001
            log.exception("history broker attach failed")


def _broker_is_stale(broker) -> bool:
    """None, or a token whose 03:30 IST expiry has passed."""
    if broker is None:
        return True
    try:
        hours = broker.hours_until_expiry()
    except Exception:                                         # noqa: BLE001
        return False
    return hours is not None and hours <= 0


def _heal_stale_broker() -> None:
    """If the engine's token is missing or expired but Mongo holds a FRESHER
    one, attach that. One cheap find_one, and only while stale.

    Covers the case the in-memory attach cannot: a Render redeploy overlaps
    two processes for a moment, the login lands on the old one, and the new
    one booted with the previous day's document. Before this the new process
    kept the dead token for its whole life (the lazy attach in engine() only
    ever fired when the broker was None, never when it was expired).
    Caller holds _LOCK.
    """
    if _ENGINE is None or not _broker_is_stale(_ENGINE.broker):
        return
    doc = db.load_upstox_token()
    if not doc or not doc.get("access_token"):
        return
    current = getattr(_ENGINE.broker, "connected_at", None)
    if _ENGINE.broker is not None and doc.get("connected_at") == current:
        return                                  # same dead token, nothing fresher
    _attach_broker_if_persisted()


def quote_source_status() -> dict:
    """Reports whether the engine is currently sourcing quotes from Upstox or yfinance."""
    from trading.quote_router import status as router_status
    if _ENGINE is None:
        return {"source": "yfinance", "reason": "engine_not_initialised"}
    with _LOCK:
        _heal_stale_broker()
        broker = _ENGINE.broker
    return router_status(broker)


def reload_config() -> None:
    """Reload config + apply mode preset + rebuild engine. Called after Settings save."""
    global _ENGINE
    with _LOCK:
        if _CFG is None or _PORTFOLIO is None:
            _bootstrap()
            return
        _CFG.apply_mode_preset()
        # 2026-09-26 (owner: "still upstox not connecting"): the rebuilt
        # engine used to start with NO broker, so the token the login had
        # just handed the engine in memory was dropped by any Settings save
        # or the dashboard mode switch; the next read healed from Mongo,
        # which holds a stale document while the Atlas quota refuses writes
        # — red "token EXPIRED" minutes after a green login. The broker is
        # the owner's session, not configuration: carry it across.
        broker = _ENGINE.broker if _ENGINE is not None else None
        _ENGINE = TradingEngine(_CFG, _PORTFOLIO)
        if broker is not None:
            _ENGINE.attach_broker(broker)


def db_status() -> dict:
    return db.status()


def cfg() -> Config:
    with _LOCK:
        if _CFG is None:
            _bootstrap()
        assert _CFG is not None
        return _CFG


def portfolio() -> Portfolio:
    with _LOCK:
        if _PORTFOLIO is None:
            _bootstrap()
        assert _PORTFOLIO is not None
        return _PORTFOLIO


def engine() -> TradingEngine:
    with _LOCK:
        if _ENGINE is None:
            _bootstrap()
        assert _ENGINE is not None
        # Lazy auto-attach: if the engine has no broker but Mongo has a
        # token, attach on demand. This handles the case where Upstox was
        # connected *after* the engine bootstrapped. 2026-09-24: also when
        # the broker it holds is EXPIRED and Mongo has a fresher one.
        _heal_stale_broker()
        return _ENGINE
