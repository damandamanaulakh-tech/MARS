"""In-memory paper portfolio. Loaded from / persisted to JSON on disk."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional

from .config import Config

# RAM caps for the always-on process (Render 512MB). The FULL journal is kept
# durably in Mongo (journal_archive) by the web/state save wrapper; RAM holds
# only the recent tail so these lists can't grow unbounded for days.
JOURNAL_RAM_CAP = 5000
SCAN_HISTORY_RAM_CAP = 500
REGIME_HISTORY_RAM_CAP = 200
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
    pending_orders: List[dict] = field(default_factory=list)  # owner limit orders (day validity)
    filings: List[dict] = field(default_factory=list)  # matched exchange announcements (held+watch)
    # Daily loss-halt baseline. Persisted so a mid-day Render restart cannot
    # silently re-baseline the kill switch (RiskState is rebuilt on every boot).
    risk_day: Optional[str] = None            # ISO date the baseline belongs to
    risk_day_equity: float = 0.0              # equity the -2% halt measures from
    risk_day_killed: bool = False             # switch tripped today? (survives restart)
    # Owner order 2026-07-30: the starting_capital this book's cash has been
    # synced to. Changing Starting Capital in Settings now really adds/removes
    # cash (journaled CAPITAL_ADJUST) instead of doing nothing to a live book.
    capital_base: float = 0.0
    # Drawdown governor peak (2026-07-31). DrawdownGovernor was rebuilt on
    # every boot with peak_equity=0, so after any Render restart the measured
    # drawdown was 0 and the governor's throttles/halts were disabled until a
    # new peak formed. Persisting the peak makes the governor survive restarts.
    dd_peak_equity: float = 0.0
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
                "pending_orders": self.pending_orders[-200:],  # owner limit orders, capped
                "filings": self.filings[:100],                 # matched announcements, capped
                "risk_day": self.risk_day,                     # daily kill-switch baseline date
                "risk_day_equity": self.risk_day_equity,       # ...and the equity it measures from
                "risk_day_killed": self.risk_day_killed,       # tripped-today flag
                "capital_base": self.capital_base,   # capital the cash is synced to
                "dd_peak_equity": self.dd_peak_equity,  # drawdown-governor peak
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
            p.pending_orders = list(data.get("pending_orders", []))
            p.filings = list(data.get("filings", []))
            p.risk_day = data.get("risk_day")
            p.risk_day_equity = float(data.get("risk_day_equity", 0.0) or 0.0)
            p.risk_day_killed = bool(data.get("risk_day_killed", False))
            # Legacy books predate capital_base; every live book started at
            # the 2026 spec's 25L, so that is the honest baseline to sync from.
            p.capital_base = float(data.get("capital_base", 25_00_000.0) or 25_00_000.0)
            p.dd_peak_equity = float(data.get("dd_peak_equity", 0.0) or 0.0)
            p.last_save = data.get("last_save")
            return p
        except Exception:
            return cls.fresh(cfg)

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
        self.last_scan_picks = []
        self.last_scan_skipped = []
        self.last_scan_opened = []
        self.scan_matrix = {}
        self.save()
