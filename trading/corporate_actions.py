"""Corporate actions on HELD names — the BLSE lesson (owner order 2026-10-21).

What happened: BLS E-Services split 10 → 5 on 2026-10-06 (ISIN
INE0NLT01010 → INE0NLT01028). The book held it from before the ex-date at
~Rs 320; nothing adjusted the position, the quote halved to ~Rs 158, the
−5% stop fired and a −49% loss was booked on a name that had moved about
−1%. The owner: "Yes — adjust held positions on the ex-date".

This module is PURE (no network client, no config mutation, nothing from
web/): it parses corporate-action rows — the NSE API's and the committed
data/corporate_actions_2010_2026.csv share one shape — into
{symbol, ex_date, kind, ratio}, says which of them a held position still
has to absorb, and restates the position (qty × ratio, entry ÷ ratio,
stop/target/peak ÷ ratio) so the next exit pass compares like with like.
The engine calls `TradingEngine.apply_corporate_actions`; the store rows
reach this module through an injected reader (the flows.set_store_reader
pattern — trading/ never imports web.db). The network fetch lives here as
a plain function the scheduler calls with a throwaway client; nothing in
the money path calls it.

Ratios (share factor, what ONE old share becomes):
  split  "Face Value Split From Rs 10 To Rs 5"      → 10/5 = 2
  consolidation "… From Re 1 To Rs 10"               → 1/10 = 0.1
  bonus  "Bonus 1:1" (a new for every b held)        → 1 + a/b = 2
  rights → ratio None: the price effect depends on the issue price, so a
           rights row is reported (DATA_NEEDED), never applied.
A subject the parser cannot read is "other" with ratio None — never a
guessed number (the fabricated-data rule).
"""
from __future__ import annotations

import csv
import logging
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

log = logging.getLogger(__name__)

NSE_CA_URL = "https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol={symbol}"
NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
NSE_TIMEOUT_S = 15.0
# How many past sessions a missed ex-date is still applied for (the app
# sleeps; a boot days later must still catch an ex-date it slept through).
LOOKBACK_SESSIONS = 5
APPLIED_KINDS = ("split", "consolidation", "bonus")

_CA_CSV = os.path.join(os.path.dirname(__file__), "..", "data",
                       "corporate_actions_2010_2026.csv")
_NUM = r"(\d+(?:\.\d+)?)"


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _from_to(tail: str) -> Optional[tuple]:
    m = re.search(_NUM + r"[^\d]*?\bto\b[^\d]*?" + _NUM, tail)
    if not m:
        return None
    a, b = _f(m.group(1)), _f(m.group(2))
    if a is None or b is None:
        return None
    return a, b


# Words allowed between "bonus"/"rights" and the a:b (a whitelist, so "Bonus
# Debentures 1:1" or "Bonus NCRPS 1:10" never read as an equity bonus).
_FILLER = r"(?:\s*[-:]?\s*(?:issue|shares?|in the ratio of|at|eq))*"


def _ab(word: str, s: str) -> Optional[tuple]:
    m = re.search(word + _FILLER + r"\s*[-:]?\s*" + _NUM + r"\s*:\s*" + _NUM, s)
    if not m:
        return None
    a, b = _f(m.group(1)), _f(m.group(2))
    if a is None or b is None:
        return None
    return a, b


def parse_subject(subject: str) -> dict:
    """NSE subject text → {kind, ratio, detail}. kind ∈ split | consolidation
    | bonus | rights | other; ratio = the share factor (None for rights /
    other). A row carrying both a bonus and a split multiplies them."""
    detail = re.sub(r"\s+", " ", str(subject or "")).strip()
    s = detail.lower()
    kinds: list = []
    ratio = 1.0
    if "split" in s or "sub-division" in s or "subdivision" in s:
        m = re.search(r"(?:split|sub-division|subdivision)(.*)$", s)
        ft = _from_to(m.group(1)) if m else None
        if ft and ft[0] > ft[1]:
            kinds.append("split")
            ratio *= ft[0] / ft[1]
        else:
            kinds.append("other")
    if "consolidat" in s:
        m = re.search(r"consolidat\w*(.*)$", s)
        ft = _from_to(m.group(1)) if m else None
        if ft and ft[0] < ft[1]:
            kinds.append("consolidation")
            ratio *= ft[0] / ft[1]
        else:
            kinds.append("other")
    if "bonus" in s:
        ab = _ab(r"bonus", s)
        if ab:
            kinds.append("bonus")
            ratio *= 1.0 + ab[0] / ab[1]
        else:
            kinds.append("other")
    if re.search(r"\brights?\b", s):
        kinds.append("rights")
    applied = [k for k in kinds if k in APPLIED_KINDS]
    if applied:
        kind = applied[0] if len(applied) == 1 else "+".join(applied)
        return {"kind": kind, "ratio": ratio, "detail": detail}
    if "rights" in kinds:
        return {"kind": "rights", "ratio": None, "detail": detail}
    return {"kind": "other", "ratio": None, "detail": detail}


def iso_date(s) -> Optional[str]:
    """'06-Oct-2026' / '2026-10-06' / '06-10-2026' → '2026-10-06'; None otherwise."""
    s = str(s or "").strip()
    if not s or s == "-":
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# Which copy of one action wins when several sources carry it: NSE's own
# API first (the store holds NSE rows), the committed file last.
_SOURCE_RANK = {"nse_api": 0, "store": 1, "audit": 2, "committed_file": 3}


def _rank(src: str) -> int:
    return _SOURCE_RANK.get(src or "", 2)


def parse_rows(rows, source: str = "") -> list:
    """Raw CA rows (NSE API or the committed file — same columns) → the
    split/consolidation/bonus/rights ones: [{symbol, ex_date, kind, ratio,
    subject, isin, source}], ascending by ex-date. Dividends, AGMs and
    unreadable subjects are left out.

    ONE share action per symbol|ex_date, deduped per KIND. The NSE API and
    the committed file word the same split differently ("Face Value Split
    (Sub-Division) - From Rs 10/- …" vs "Fv Split Rs.10 To Rs.5"); a dedupe
    on the subject text would keep both and the adjuster would double the
    qty twice. So per symbol|ex_date each kind (split / consolidation /
    bonus) is taken once, from the best source that carries it (NSE first,
    the committed file last); kinds from different rows are multiplied into
    one action, and a row whose kinds another row already covers is
    dropped. A kind only a lower source lists still counts (NSE carrying
    the split, the file the same-day bonus). Rights rows stay separate
    (reported, never applied)."""
    parsed: list = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        subj = str(r.get("subject") or "").strip()
        ex = iso_date(r.get("exDate") or r.get("ex_date"))
        sym = str(r.get("symbol") or "").strip().upper()
        if not subj or not ex or not sym:
            continue
        p = parse_subject(subj)
        if p["kind"] == "other":
            continue
        if p["ratio"] is not None and abs(p["ratio"] - 1.0) < 1e-9:
            continue
        parsed.append({
            "symbol": sym, "ex_date": ex, "kind": p["kind"], "ratio": p["ratio"],
            "subject": p["detail"], "isin": str(r.get("isin") or "").strip(),
            "source": str(r.get("source") or source or ""),
        })
    out: list = []
    rights: dict = {}
    groups: dict = {}
    for row in parsed:
        if row["kind"] == "rights":
            k = (row["symbol"], row["ex_date"])
            if k not in rights or _rank(row["source"]) < _rank(rights[k]["source"]):
                rights[k] = row
            continue
        groups.setdefault((row["symbol"], row["ex_date"]), []).append(row)
    for (sym, ex), rs in groups.items():
        # best source first; inside one source the combined row first
        rs = sorted(rs, key=lambda r: (_rank(r["source"]), -len(r["kind"].split("+"))))
        covered: set = set()
        merged = None
        for r in rs:
            kinds = set(r["kind"].split("+"))
            if kinds & covered:
                continue                    # the same action again, or part of one
            covered |= kinds
            if merged is None:
                merged = dict(r)
            else:
                merged["ratio"] = float(merged["ratio"]) * float(r["ratio"])
                merged["subject"] = f"{merged['subject']} + {r['subject']}"
        merged["kind"] = "+".join(sorted(covered, key=lambda k: APPLIED_KINDS.index(k)))
        out.append(merged)
    out.extend(rights.values())
    return sorted(out, key=lambda x: (x["ex_date"], x["symbol"], x["kind"]))


def ca_key(ca: dict) -> str:
    return f"{ca['symbol']}|{ca['ex_date']}"


# ---------------------------------------------------------------------------
# sources: committed file (indexed once per process), store reader, NSE API
# ---------------------------------------------------------------------------
_FILE_INDEX: Optional[tuple] = None     # (file key, {SYMBOL: [raw rows]})
_KEEP = ("split", "bonus", "sub-division", "subdivision", "rights", "consolidat")


def _file_index(path: str = _CA_CSV) -> dict:
    """{SYMBOL: [raw split/bonus/rights rows]} from the committed file,
    read once per process (the dividend/AGM rows — most of the 34,792 —
    are dropped at read time, so the index is small)."""
    global _FILE_INDEX
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _FILE_INDEX is not None and _FILE_INDEX[0] == key:
        return _FILE_INDEX[1]
    idx: dict = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                subj = (row.get("subject") or "").lower()
                if not any(k in subj for k in _KEEP):
                    continue
                sym = (row.get("symbol") or "").strip().upper()
                if sym:
                    idx.setdefault(sym, []).append({**row, "source": "committed_file"})
    except OSError as e:
        log.warning("corporate-actions file unreadable: %s", e)
        return {}
    _FILE_INDEX = (key, idx)
    return idx


def clear_file_index() -> None:
    global _FILE_INDEX
    _FILE_INDEX = None


def committed_rows(symbol: str, path: str = _CA_CSV) -> list:
    return list(_file_index(path).get(bare(symbol), []))


_STORE_READER: Optional[Callable] = None


def set_store_reader(fn) -> None:
    """fn(symbols: list[str]) -> raw CA rows stored by the daily fetch
    (Mongo `corporate_actions`). Attached by web/state at boot."""
    global _STORE_READER
    _STORE_READER = fn


def store_rows(symbols) -> list:
    if _STORE_READER is None:
        return []
    try:
        return list(_STORE_READER(list(symbols)) or [])
    except Exception as e:                      # noqa: BLE001
        log.warning("corporate-actions store read failed: %s", e)
        return []


def _nse_get(url: str, timeout: float = NSE_TIMEOUT_S):
    """The one network call of this module (tests replace it). JSON or None."""
    import httpx
    r = httpx.get(url, headers=NSE_HEADERS, timeout=timeout, follow_redirects=True)
    if r.status_code != 200:
        log.info("NSE CA %s -> %s", url, r.status_code)
        return None
    return r.json()


def fetch_nse_actions(symbol: str, get: Optional[Callable] = None,
                      timeout: float = NSE_TIMEOUT_S) -> Optional[list]:
    """The symbol's corporate actions from NSE's own API (browser headers;
    answered 200 from the sandbox 2026-10-21). Raw rows, or None when the
    call fails — None is "unknown", never "no actions"."""
    sym = bare(symbol)
    url = NSE_CA_URL.format(symbol=sym)
    try:
        if get is None:
            data = _nse_get(url, timeout)
        else:
            data = get(url)
    except Exception as e:                      # noqa: BLE001
        log.info("NSE CA fetch failed for %s: %s", sym, e)
        return None
    if not isinstance(data, list):
        return None
    out = []
    for row in data:
        if isinstance(row, dict):
            out.append({**row, "source": "nse_api"})
    return out


def bare(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    for suf in (".NS", ".BO"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t


# Rows the daily NSE fetch returned in THIS process, kept so a Mongo outage
# (save refused) never hides a split the fetch did see. ≤ ~60 held names.
_FETCHED: dict = {}


def remember_rows(symbol: str, rows) -> None:
    """Keep the NSE rows fetched for a symbol in process (latest fetch wins)."""
    sym = bare(symbol)
    if sym and rows is not None:
        _FETCHED[sym] = [dict(r) for r in rows if isinstance(r, dict)]


def remembered_rows(symbol: str) -> list:
    return list(_FETCHED.get(bare(symbol), []))


def clear_remembered() -> None:
    _FETCHED.clear()


def actions_for(symbol: str, extra_rows=None) -> list:
    """Every parsed split/consolidation/bonus/rights row known for the
    symbol: the store (daily NSE fetch) + this process's own fetch + the
    committed file (+ rows the caller fetched itself), merged and deduped."""
    sym = bare(symbol)
    raw = (list(store_rows([sym])) + remembered_rows(sym) + committed_rows(sym)
           + list(extra_rows or []))
    return [c for c in parse_rows(raw) if c["symbol"] == sym]


def actions_for_many(symbols) -> dict:
    """{SYMBOL: parsed rows} for several symbols with ONE store read (the
    engine calls this on every exit pass; a query per held name would be
    ~60 round trips each time)."""
    syms = sorted({bare(s) for s in symbols or [] if s})
    if not syms:
        return {}
    stored: dict = {}
    for r in store_rows(syms):
        if isinstance(r, dict):
            stored.setdefault(str(r.get("symbol") or "").strip().upper(), []).append(r)
    out = {}
    for sym in syms:
        raw = stored.get(sym, []) + remembered_rows(sym) + committed_rows(sym)
        out[sym] = [c for c in parse_rows(raw) if c["symbol"] == sym]
    return out


# ---------------------------------------------------------------------------
# which actions a held position still has to absorb
# ---------------------------------------------------------------------------
def _ist_date(dt) -> date:
    """Engine stamps are naive UTC; the ex-date is an IST calendar day. A
    bare date (a legacy date-only entry) is already a calendar day."""
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta(hours=5, minutes=30)).date()


def recent_sessions(today: date, n: int = LOOKBACK_SESSIONS) -> list:
    """The last n trading days ending today (today included when it trades)."""
    from .calendars import is_trading_day
    out, d, guard = [], today, 0
    while len(out) < n and guard < 40:
        if is_trading_day(d):
            out.append(d.isoformat())
        d -= timedelta(days=1)
        guard += 1
    return out


# On the ex-date itself the restatement waits for the market open: before
# 09:15 IST the quote is still yesterday's unadjusted last trade, and a
# doubled qty marked at it would inflate equity (and the persisted drawdown
# peak) for a pre-open scan. From the open the quote is the adjusted one.
OPEN_IST = (9, 15)


def pending_for_position(pos, actions: list, today: date,
                         lookback: int = LOOKBACK_SESSIONS,
                         now_ist: Optional[datetime] = None) -> list:
    """The split/consolidation/bonus rows this position must still absorb:
    ex-date after the entry day (a buy on or after the ex-date already paid
    the adjusted price), on or before today, within the last `lookback`
    sessions, with a ratio, and not in pos.ca_applied. `now_ist` (the real
    IST clock) holds back an action whose ex-date is that day until 09:15."""
    entry_day = _ist_date(pos.entry_date).isoformat()
    window = set(recent_sessions(today, lookback))
    applied = set(getattr(pos, "ca_applied", None) or [])
    preopen_day = None
    if now_ist is not None and (now_ist.hour, now_ist.minute) < OPEN_IST:
        preopen_day = now_ist.date().isoformat()
    out, seen = [], set()
    for ca in actions:
        if ca["kind"].split("+")[0] not in APPLIED_KINDS or not ca.get("ratio"):
            continue
        if ca["ex_date"] <= entry_day or ca["ex_date"] > today.isoformat():
            continue
        if ca["ex_date"] == preopen_day:
            continue                            # restated at the open, not before
        if ca["ex_date"] not in window:
            continue
        # one application per SYMBOL|ex_date, however many rows describe it
        if ca_key(ca) in applied or ca_key(ca) in seen:
            continue
        seen.add(ca_key(ca))
        out.append(ca)
    return out


def leaves_whole_share(pos, ca: dict) -> bool:
    """False when the action would leave less than one whole share (a
    consolidation on a tiny holding). Applying it would book a qty-0
    position, so the engine names it for a hand settlement instead."""
    try:
        return math.floor(int(pos.qty) * float(ca["ratio"]) + 1e-9) >= 1
    except (TypeError, ValueError, KeyError):
        return False


def apply_to_position(pos, ca: dict) -> dict:
    """Restate ONE held position for one action, in place. qty × ratio
    (floored to whole shares; the fractional remainder is credited to cash
    at the ADJUSTED entry price so the cost basis is unchanged), entry ÷
    ratio, stop/target/peak ÷ ratio (0 = unset, left alone), custom_levels
    untouched, the action recorded in pos.ca_applied. Returns the journal
    body {before, after, cash_credit, remainder_shares}. Never sells."""
    ratio = float(ca["ratio"])
    before = {"qty": int(pos.qty), "entry_price": round(float(pos.entry_price), 4),
              "stop_price": round(float(getattr(pos, "stop_price", 0.0) or 0.0), 4),
              "target_price": round(float(getattr(pos, "target_price", 0.0) or 0.0), 4),
              "peak_price": round(float(getattr(pos, "peak_price", 0.0) or 0.0), 4)}
    exact = pos.qty * ratio
    new_qty = int(math.floor(exact + 1e-9))
    remainder = max(0.0, exact - new_qty)
    new_entry = float(pos.entry_price) / ratio
    credit = remainder * new_entry
    pos.qty = new_qty
    pos.entry_price = new_entry
    for fld in ("stop_price", "target_price", "peak_price"):
        v = float(getattr(pos, fld, 0.0) or 0.0)
        if v > 0:
            setattr(pos, fld, v / ratio)
    if getattr(pos, "orig_qty", 0):
        pos.orig_qty = int(math.floor(pos.orig_qty * ratio + 1e-9))
    applied = list(getattr(pos, "ca_applied", None) or [])
    applied.append(ca_key(ca))
    pos.ca_applied = applied
    after = {"qty": int(pos.qty), "entry_price": round(pos.entry_price, 4),
             "stop_price": round(float(getattr(pos, "stop_price", 0.0) or 0.0), 4),
             "target_price": round(float(getattr(pos, "target_price", 0.0) or 0.0), 4),
             "peak_price": round(float(getattr(pos, "peak_price", 0.0) or 0.0), 4)}
    return {"ticker": pos.ticker, "subject": ca["subject"], "ex_date": ca["ex_date"],
            "kind": ca["kind"], "ratio": ratio, "before": before, "after": after,
            "remainder_shares": round(remainder, 6), "cash_credit": round(credit, 2),
            "source": ca.get("source", "")}
