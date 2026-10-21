"""In-memory paper portfolio. Loaded from / persisted to JSON on disk."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional

from .config import Config

log = logging.getLogger(__name__)

# RAM caps for the always-on process (Render 512MB). The FULL journal is kept
# durably in Mongo (journal_archive) by the web/state save wrapper; RAM holds
# only the recent tail so these lists can't grow unbounded for days.
JOURNAL_RAM_CAP = 5000
SCAN_HISTORY_RAM_CAP = 500
REGIME_HISTORY_RAM_CAP = 200
# AUDIT M-10 (2026-10-02): closed trades kept in the ONE Mongo portfolio
# document (16 MB limit). Older ones move to the `trades` archive collection
# (web/state.py save wrapper), and only after that write succeeds — a trade
# is never trimmed without a durable copy. ~400 bytes each, so 5,000 is ~2 MB.
TRADES_DOC_CAP = 5000
# Owner 2026-10-11 item 15: the per-name universe move ledger kept on the
# portfolio doc. ~1,200 names can move a few hundred at a time on a rebuild;
# 1,500 rows ≈ 6 weeks of rebuilds at ~150 KB — bounded for the 512 MB box.
UNIVERSE_LEDGER_CAP = 1500
EQUITY_HISTORY_RAM_CAP = 5000  # (ts, equity) points; appended every refresh


@dataclass
class PaperPosition:
    ticker: str
    qty: int
    entry_price: float          # post-slippage
    entry_date: datetime
    sector: str
    peak_price: float
    entry_score: float
    reasoning: str
    entry_notes: str = ""       # user-editable journal note (why I entered)
    tags: List[str] = field(default_factory=list)  # free-form tags ("earnings_play")
    mode: str = ""              # trade category at entry: intraday/short/swing/positional/nifty200_momentum
    stop_price: float = 0.0     # hard stop price, snapshot at entry (0 = fall back to live cfg)
    target_price: float = 0.0   # take-profit/target price, snapshot at entry (0 = fall back to live cfg)
    custom_levels: bool = False  # owner set stop/target by hand (order ticket).
                                 # retag_positions_per_mode must NOT overwrite
                                 # these on boot (fixed 2026-07-31).
    orig_qty: int = 0           # F15: qty at first entry (pyramid tranche base; 0 = use qty)
    pyramid_adds: int = 0       # F15: how many pyramid tranches added so far

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_date"] = self.entry_date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PaperPosition":
        d = dict(d)
        d["entry_date"] = datetime.fromisoformat(d["entry_date"])
        d.setdefault("entry_notes", "")
        d.setdefault("tags", [])
        d.setdefault("mode", "")
        d.setdefault("stop_price", 0.0)
        d.setdefault("target_price", 0.0)
        d.setdefault("custom_levels", False)
        d.setdefault("orig_qty", 0)
        d.setdefault("pyramid_adds", 0)
        return cls(**d)


@dataclass
class ClosedTrade:
    ticker: str
    qty: int
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    gross_pnl: float
    costs: float
    net_pnl: float
    reason: str
    sector: str
    entry_score: float
    reasoning: str
    entry_notes: str = ""
    tags: List[str] = field(default_factory=list)
    # 2026-10-05 ("make it momentum only"): the category the position was
    # opened under, copied from PaperPosition.mode at close. Blank on trades
    # closed before this field existed (measured_kelly.trade_category reads
    # the rest of the record for those).
    mode: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_date"] = self.entry_date.isoformat()
        d["exit_date"] = self.exit_date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ClosedTrade":
        d = dict(d)
        d["entry_date"] = datetime.fromisoformat(d["entry_date"])
        d["exit_date"] = datetime.fromisoformat(d["exit_date"])
        d.setdefault("entry_notes", "")
        d.setdefault("tags", [])
        return cls(**d)


@dataclass
class Portfolio:
    cfg: Config
    cash: float = 0.0
    positions: Dict[str, PaperPosition] = field(default_factory=dict)
    trades: List[ClosedTrade] = field(default_factory=list)
    equity_history: List[tuple] = field(default_factory=list)  # (iso_ts, equity)
    last_scan: Optional[str] = None
    # AUDIT S-5 (2026-10-02): stamped only by a scan that ran INSIDE market
    # hours (one that could buy). `last_scan` is stamped by every scan, the
    # look-only ones before 09:15 and after 15:30 included.
    last_scan_live: Optional[str] = None
    last_scan_picks: List[dict] = field(default_factory=list)
    last_scan_skipped: List[dict] = field(default_factory=list)
    last_scan_opened: List[str] = field(default_factory=list)
    last_regime: Optional[str] = None
    last_ifr_fire_date: Optional[str] = None        # IFR damage cluster fire (ISO date)
    last_ifr_ratio_fire_date: Optional[str] = None  # IFR tail-damage-ratio fire (Q2)
    regime_history: List[dict] = field(default_factory=list)  # [{ts, prev, new, reason}]
    scan_history: List[dict] = field(default_factory=list)  # archived scan summaries
    decision_journal: List[dict] = field(default_factory=list)  # append-only: why bought/sold/held
    virtual_book: Dict[str, dict] = field(default_factory=dict)  # F8: unbought YES picks, marked to market
    scan_matrix: dict = field(default_factory=dict)  # 🎹 full scan hit-map (overwritten each scan)
    momentum_shortlist: dict = field(default_factory=dict)  # momentum pass's own record (audit S-11/S-13)
    rotation_report: dict = field(default_factory=dict)  # why-no-buy: the last rotations' per-category record (owner 2026-10-21)
    trigger_watch: dict = field(default_factory=dict)  # ⚡ owner's trigger-layer daily result (display+watchlist only)
    news_watch: dict = field(default_factory=dict)     # 📰 news verdict per stock (display only, never blocks)
    universe_batch: dict = field(default_factory=dict)  # 📦 this week's liquidity batch: what came in/out (owner ask 2026-09-04)
    universe_ledger: List[dict] = field(default_factory=list)  # 📒 per-name moves {ts, ticker, move, turnover, rank, reason} — owner 2026-10-11 item 15; capped UNIVERSE_LEDGER_CAP
    breadth_store: dict = field(default_factory=dict)  # F3: full-store breadth reading {pct, sample, asof} the A3 gate prefers over the noisy bucket sample
    breadth_today: dict = field(default_factory=dict)  # the day's scan-bucket breadth, computed once by the first pass that CAN (momentum pulls 2y; the other four pull 6mo and cannot see a 200DMA) and reused by the rest — one market reading per day, not one per mode
    option_chain: dict = field(default_factory=dict)   # Node 8: daily chain snapshot {asof, rows, history} (display/triggers only; needs the daily login)
    register_market: dict = field(default_factory=dict)  # Node 10: nightly full-store register market view {asof, sample, rows} (/register reads, never computes)
    damage_store: dict = field(default_factory=dict)     # Node 9: canonical tail_down3_5d reading from the scan panel (FII-08/09 read it; IFR journals it beside its own)
    pending_orders: List[dict] = field(default_factory=list)  # owner limit orders (day validity)
    filings: List[dict] = field(default_factory=list)  # matched exchange announcements (held+watch)
    # Daily loss-halt baseline. Persisted so a mid-day Render restart cannot
    # silently re-baseline the kill switch (RiskState is rebuilt on every boot).
    risk_day: Optional[str] = None            # ISO date the baseline belongs to
    risk_day_equity: float = 0.0              # equity the -2% halt measures from
    risk_day_killed: bool = False             # switch tripped today? (survives restart)
    danger_alerts_start: Optional[str] = None  # danger layer mode-C start date (owner order 2026-08-11)
    # Owner order 2026-07-30: the starting_capital this book's cash has been
    # synced to. Changing Starting Capital in Settings now really adds/removes
    # cash (journaled CAPITAL_ADJUST) instead of doing nothing to a live book.
    capital_base: float = 0.0
    # AUDIT FIX C11 (2026-08-09): the 2-losses-in-a-day halt was memory-only —
    # a mid-day Render restart forgot it on exactly the day it exists for.
    loss_day: Optional[str] = None
    loss_count: int = 0
    # Owner order 2026-10-21: the day's losing strategy exits PER CATEGORY
    # ({category: count}), so the per-category halt survives a restart too.
    loss_by_category: dict = field(default_factory=dict)
    # Drawdown governor peak (2026-07-31). DrawdownGovernor was rebuilt on
    # every boot with peak_equity=0, so after any Render restart the measured
    # drawdown was 0 and the governor's throttles/halts were disabled until a
    # new peak formed. Persisting the peak makes the governor survive restarts.
    dd_peak_equity: float = 0.0
    # The drawdown level whose ladder cut already ran (owner order 2026-10-02):
    # each level cuts ONCE. Persisted, or a restart would cut the same level
    # again on its first pass.
    dd_cut_level: str = "normal"
    last_save: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def fresh(cls, cfg: Config) -> "Portfolio":
        p = cls(cfg=cfg, cash=cfg.starting_capital,
                capital_base=cfg.starting_capital)
        return p

    def sync_capital(self, cfg: Config) -> Optional[str]:
        """Make the book's cash follow cfg.starting_capital (owner order
        2026-07-30). Adds/removes the DELTA so the P&L history stays real —
        never a reset. Journaled CAPITAL_ADJUST; a withdrawal that would
        push cash below zero is refused and journaled CAPITAL_ADJUST_BLOCKED.
        Idempotent via capital_base. Returns a note when something changed."""
        target = float(cfg.starting_capital)
        delta = target - float(self.capital_base or 0.0)
        if abs(delta) < 0.5:
            return None
        if self.cash + delta < 0:
            self.decision_journal.append({
                "ts": datetime.utcnow().isoformat(),
                "action": "CAPITAL_ADJUST_BLOCKED",
                "detail": (f"Capital {self.capital_base:,.0f} -> {target:,.0f} "
                           f"needs {-delta:,.0f} out but free cash is only "
                           f"{self.cash:,.0f}. Sell first, then retry."),
            })
            return None
        self.cash += delta
        # The drawdown peak moves with the capital (owner order 2026-10-02):
        # a capital cut must not read as a drawdown (it could trip the
        # ladder and sell the book), and a top-up must not hide a real one.
        if self.dd_peak_equity > 0:
            self.dd_peak_equity = max(0.0, self.dd_peak_equity + delta)
        old = self.capital_base
        self.capital_base = target
        note = (f"Paper capital {old:,.0f} -> {target:,.0f} "
                f"({'+' if delta >= 0 else ''}{delta:,.0f} cash). ")
        self.decision_journal.append({
            "ts": datetime.utcnow().isoformat(),
            "action": "CAPITAL_ADJUST",
            "detail": note + f"Cash now {self.cash:,.0f}.",
            "cash_after": round(self.cash, 2),
        })
        return note

    def equity(self, prices: Dict[str, float]) -> float:
        eq = self.cash
        for p in self.positions.values():
            mark = prices.get(p.ticker, p.entry_price)
            eq += p.qty * mark
        return eq

    def deployed_value(self, prices: Dict[str, float]) -> float:
        return sum(
            p.qty * prices.get(p.ticker, p.entry_price)
            for p in self.positions.values()
        )

    # ---------- persistence ----------

    def _state_path(self) -> str:
        os.makedirs(self.cfg.state_dir, exist_ok=True)
        return os.path.join(self.cfg.state_dir, "portfolio.json")

    def save(self) -> None:
        with self._lock:
            # Bound RAM: keep only the recent tail. Overflow is archived to the
            # durable Mongo journal_archive by the web/state save wrapper BEFORE
            # this runs, so nothing is lost — the full trail lives in Mongo.
            if len(self.decision_journal) > JOURNAL_RAM_CAP:
                self.decision_journal = self.decision_journal[-JOURNAL_RAM_CAP:]
            self.scan_history = self.scan_history[-SCAN_HISTORY_RAM_CAP:]
            self.regime_history = self.regime_history[-REGIME_HISTORY_RAM_CAP:]
            self.equity_history = self.equity_history[-EQUITY_HISTORY_RAM_CAP:]
            data = {
                "cash": self.cash,
                "positions": {k: v.to_dict() for k, v in self.positions.items()},
                "trades": [t.to_dict() for t in self.trades],
                "equity_history": self.equity_history,
                "last_scan": self.last_scan,
                "last_scan_live": self.last_scan_live,
                "last_scan_picks": self.last_scan_picks,
                "last_scan_skipped": self.last_scan_skipped,
                "last_scan_opened": self.last_scan_opened,
                "last_regime": self.last_regime,
                "last_ifr_fire_date": self.last_ifr_fire_date,
                "last_ifr_ratio_fire_date": self.last_ifr_ratio_fire_date,
                "regime_history": self.regime_history[-200:],  # cap
                "scan_history": self.scan_history[-500:],      # cap
                "decision_journal": self.decision_journal,     # recent tail (full trail in Mongo journal_archive)
                "virtual_book": self.virtual_book,             # F8 shadow book of unbought YES picks
                "scan_matrix": self.scan_matrix,               # 🎹 one scan's hit-map, fixed size
                "momentum_shortlist": self.momentum_shortlist,  # momentum pass record (S-11/S-13)
                "rotation_report": self.rotation_report,       # why-no-buy record (owner 2026-10-21)
                "trigger_watch": self.trigger_watch,           # ⚡ trigger-layer daily result
                "news_watch": self.news_watch,                 # 📰 news verdict per stock
                "universe_batch": self.universe_batch,         # 📦 weekly batch details
                "universe_ledger": self.universe_ledger[-UNIVERSE_LEDGER_CAP:],  # 📒 per-name moves
                "breadth_store": self.breadth_store,           # F3 full-store breadth
                "breadth_today": self.breadth_today,           # the day's shared bucket reading
                "option_chain": self.option_chain,             # Node 8 chain snapshot
                "register_market": self.register_market,       # Node 10 precompute
                "damage_store": self.damage_store,             # Node 9 canonical damage
                "pending_orders": self.pending_orders[-200:],  # owner limit orders, capped
                "filings": self.filings[:100],                 # matched announcements, capped
                "risk_day": self.risk_day,                     # daily kill-switch baseline date
                "risk_day_equity": self.risk_day_equity,       # ...and the equity it measures from
                "risk_day_killed": self.risk_day_killed,       # tripped-today flag
                "danger_alerts_start": self.danger_alerts_start,  # danger layer C->B clock
                "capital_base": self.capital_base,   # capital the cash is synced to
                "dd_peak_equity": self.dd_peak_equity,  # drawdown-governor peak
                "dd_cut_level": self.dd_cut_level,      # ladder level already cut
                "loss_day": self.loss_day,               # 2-loss halt state
                "loss_count": self.loss_count,
                "loss_by_category": self.loss_by_category,  # per-category losers today
                "last_save": datetime.utcnow().isoformat(),
            }
            with open(self._state_path(), "w") as f:
                json.dump(data, f, indent=2)
            self.last_save = data["last_save"]

    @classmethod
    def load_or_fresh(cls, cfg: Config) -> "Portfolio":
        path = os.path.join(cfg.state_dir, "portfolio.json")
        if not os.path.exists(path):
            return cls.fresh(cfg)
        try:
            with open(path) as f:
                data = json.load(f)
            p = cls(cfg=cfg, cash=float(data.get("cash", cfg.starting_capital)))
            p.positions = {
                k: PaperPosition.from_dict(v) for k, v in data.get("positions", {}).items()
            }
            p.trades = [ClosedTrade.from_dict(t) for t in data.get("trades", [])]
            p.equity_history = list(data.get("equity_history", []))
            p.last_scan = data.get("last_scan")
            p.last_scan_live = data.get("last_scan_live")
            p.last_scan_picks = list(data.get("last_scan_picks", []))
            p.last_scan_skipped = list(data.get("last_scan_skipped", []))
            p.last_scan_opened = list(data.get("last_scan_opened", []))
            p.last_regime = data.get("last_regime")
            p.last_ifr_fire_date = data.get("last_ifr_fire_date")
            p.last_ifr_ratio_fire_date = data.get("last_ifr_ratio_fire_date")
            p.regime_history = list(data.get("regime_history", []))
            p.scan_history = list(data.get("scan_history", []))
            p.decision_journal = list(data.get("decision_journal", []))
            p.virtual_book = dict(data.get("virtual_book", {}))
            p.scan_matrix = dict(data.get("scan_matrix", {}))
            p.momentum_shortlist = dict(data.get("momentum_shortlist", {}) or {})
            p.rotation_report = dict(data.get("rotation_report", {}) or {})
            p.trigger_watch = dict(data.get("trigger_watch", {}))
            p.news_watch = dict(data.get("news_watch", {}))
            p.universe_batch = dict(data.get("universe_batch", {}) or {})
            p.universe_ledger = list(data.get("universe_ledger", []) or [])
            p.breadth_store = dict(data.get("breadth_store", {}))
            p.breadth_today = dict(data.get("breadth_today", {}) or {})
            p.option_chain = dict(data.get("option_chain", {}))
            p.register_market = dict(data.get("register_market", {}))
            p.damage_store = dict(data.get("damage_store", {}) or {})
            p.pending_orders = list(data.get("pending_orders", []))
            p.filings = list(data.get("filings", []))
            p.risk_day = data.get("risk_day")
            p.risk_day_equity = float(data.get("risk_day_equity", 0.0) or 0.0)
            p.risk_day_killed = bool(data.get("risk_day_killed", False))
            p.danger_alerts_start = data.get("danger_alerts_start")
            # Legacy books predate capital_base. Owner order 2026-08-30
            # ("remove the 25 Lacs thing from the entire history"): the
            # fallback is the CURRENT configured capital, so a file with no
            # capital_base is treated as already synced. That also stops the
            # old fallback from injecting a phantom capital delta into cash
            # on any book that never actually started at the superseded
            # figure — the migration this once served ran in July 2026.
            p.capital_base = float(data.get("capital_base", 0.0)
                                   or cfg.starting_capital)
            p.dd_peak_equity = float(data.get("dd_peak_equity", 0.0) or 0.0)
            p.dd_cut_level = str(data.get("dd_cut_level") or "normal")
            p.loss_day = data.get("loss_day")
            p.loss_count = int(data.get("loss_count", 0) or 0)
            p.loss_by_category = {str(k): int(v) for k, v in
                                  (data.get("loss_by_category") or {}).items()}
            p.last_save = data.get("last_save")
            return p
        except Exception as e:
            # AUDIT FIX C12 (2026-08-09): a corrupt/unparseable state file
            # used to SILENTLY return a fresh book — positions and trades
            # gone at the JSON layer with no log, no journal, no evidence.
            # Now: the corrupt file is quarantined next to the new one, the
            # reset is journaled (and pushed — CORRUPT_STATE_RESET is a push
            # action), and Mongo hydration can still repair the book after.
            log.error("portfolio state file corrupt (%s) — quarantining and "
                      "starting fresh; Mongo hydrate may still restore", e)
            try:
                from datetime import datetime as _dt
                stamp = _dt.utcnow().strftime("%Y%m%dT%H%M%S")
                os.replace(path, path + f".corrupt-{stamp}")
            except OSError:
                pass
            fresh = cls.fresh(cfg)
            fresh.decision_journal.append({
                "ts": datetime.utcnow().isoformat(),
                "action": "CORRUPT_STATE_RESET",
                "err": f"{type(e).__name__}: {str(e)[:120]}",
                "why": ("portfolio.json was unreadable — quarantined as "
                        ".corrupt-*, fresh book started. If Mongo is "
                        "connected the real book hydrates over this."),
            })
            return fresh

    def reset(self) -> None:
        # Journal the reset itself, then clear trading state.
        # decision_journal / regime_history / scan_history are NEVER cleared —
        # full audit trail survives resets (user rule: nothing deleted).
        self.decision_journal.append({
            "ts": datetime.utcnow().isoformat(),
            "action": "RESET",
            "detail": f"Portfolio reset. {len(self.positions)} open positions and "
                      f"{len(self.trades)} closed trades archived in journal before clear.",
        })
        self.cash = self.cfg.starting_capital
        self.capital_base = self.cfg.starting_capital
        self.positions = {}
        self.trades = []
        self.equity_history = []
        self.last_scan = None
        self.last_scan_live = None
        self.last_scan_picks = []
        self.last_scan_skipped = []
        self.last_scan_opened = []
        self.scan_matrix = {}
        self.momentum_shortlist = {}
        self.save()
