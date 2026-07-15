"""In-memory paper portfolio. Loaded from / persisted to JSON on disk."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional

from .config import Config


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
    regime_history: List[dict] = field(default_factory=list)  # [{ts, prev, new, reason}]
    scan_history: List[dict] = field(default_factory=list)  # archived scan summaries
    last_save: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def fresh(cls, cfg: Config) -> "Portfolio":
        p = cls(cfg=cfg, cash=cfg.starting_capital)
        return p

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
                "regime_history": self.regime_history[-200:],  # cap
                "scan_history": self.scan_history[-500:],      # cap
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
            p.regime_history = list(data.get("regime_history", []))
            p.scan_history = list(data.get("scan_history", []))
            p.last_save = data.get("last_save")
            return p
        except Exception:
            return cls.fresh(cfg)

    def reset(self) -> None:
        self.cash = self.cfg.starting_capital
        self.positions = {}
        self.trades = []
        self.equity_history = []
        self.last_scan = None
        self.last_scan_picks = []
        self.last_scan_skipped = []
        self.last_scan_opened = []
        self.save()
