"""Full-store market breadth — the F3 fix (owner order 2026-08-20: "fix F3").

THE FAULT: the A3 breadth gate blocks new momentum entries when <=50% of the
panel is above its own 200DMA — but live breadth was measured on the scan's
OWN panel, the ~50-name rotating bucket. A 50-name sample of a ~2,400-name
universe carries ±7pp of sampling noise, and the market sat at 50.8% against
a <=50% line — so whether the owner's engine could buy on a given day was
decided by WHICH 50 NAMES the rotation happened to serve, not by the market.
The preregistered study (docs/breadth_gate_results.json) measured breadth on
the full 191-ticker panel; the live gate was reading a different, noisier
number than the one the evidence earned.

THE FIX: measure breadth over the FULL Mongo OHLCV store (~2,700 instruments,
fetched 2026-08-09, refreshed weekly — reliably so since the F1 fix made the
Friday top-up tokenless). One streamed Mongo query with a $slice tail
projection delivers each ticker's last ~200 closes; this module reduces that
stream to one number. Sampling noise at n≈2,700: ~±1pp. The engine's gate
prefers this stored reading while it is fresh and falls back to the bucket
sample only when it is missing or stale — behaviour then degrades to exactly
what it was before this fix, never to something new.

HONESTY RULES:
  * The reading carries `asof` (the newest close it saw) and the gate's
    journal prints it — store closes can be up to a week old between Friday
    top-ups, and a reading must say what day it describes.
  * Names whose own history has gone stale (no close within `dead_days` of
    the store's newest date) are EXCLUDED and counted — a name that stopped
    updating in 2024 would otherwise vote its 2024 state forever.
  * Too thin a sample returns None, never a guess.

This module is pure reduction: no Mongo import, no web import — the caller
(web/scheduler) streams the docs in. engine.py reads only the RESULT stored
on the portfolio, so the money path stays free of any new dependency.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

log = logging.getLogger(__name__)

#: A name with no close within this many days of the store's newest date is
#: excluded from the sample (delisted / dead symbol). ASSUMED.
DEAD_NAME_DAYS = 30


def _reduce(docs: Iterable[dict], dma: int,
            exclude: Iterable[str] = ()) -> tuple:
    """One streamed pass: each doc -> (ticker, end, above?, why). Only the
    verdict is kept, never the candles, so two readings can be taken from
    one pass of the store on the 512 MB box. Returns (rows, newest, funds)."""
    skip = set(exclude or ())
    funds = 0
    rows = []
    newest = None
    for d in docs:
        try:
            ticker = d.get("ticker")
            if skip and ticker in skip:
                # 2026-10-16: fund units are not stocks; they do not vote.
                funds += 1
                continue
            end = str(d.get("end") or "")
            candles = d.get("candles") or []
            if not end or len(candles) < dma:
                rows.append((ticker, None, None, "short"))
                continue
            closes = [float(c[4]) for c in candles[-dma:]]
            last = closes[-1]
            sma = sum(closes) / float(dma)
            if not (last > 0 and sma > 0):
                rows.append((ticker, None, None, "short"))
                continue
            rows.append((ticker, end, last > sma, None))
            if newest is None or end > newest:
                newest = end
        except Exception:                                   # noqa: BLE001
            rows.append((None, None, None, "short"))
    return rows, newest, funds


def _reading(rows: list, newest: Optional[str], dma: int, min_sample: int,
             funds: int = 0, include: Optional[set] = None,
             stale_days: int = DEAD_NAME_DAYS) -> Optional[dict]:
    from datetime import date, timedelta

    if newest is None:
        return None
    cutoff = (date.fromisoformat(newest)
              - timedelta(days=stale_days)).isoformat()
    above = sample = stale = short = 0
    for ticker, end, is_above, why in rows:
        if include is not None and ticker not in include:
            continue
        if why == "short":
            short += 1
            continue
        if end < cutoff:
            stale += 1                # a stale close is not today's vote
            continue
        sample += 1
        if is_above:
            above += 1
    if sample < min_sample:
        log.warning("store breadth: sample %d < %d — no reading", sample,
                    min_sample)
        return None
    return {
        "pct": round(above / sample, 4),
        "above": above,
        "sample": sample,
        "asof": newest,
        "dma": dma,
        "excluded_stale": stale,
        "excluded_short": short,
        "excluded_fund": funds,
    }


def store_breadth(docs: Iterable[dict], dma: int = 200,
                  min_sample: int = 1000,
                  exclude: Iterable[str] = ()) -> Optional[dict]:
    """Reduce streamed OHLCV tail-docs to one breadth reading over EVERY
    stored name (fund units in `exclude` left out).

    Each doc needs `ticker`, `end` (ISO date of its last close) and
    `candles` = the LAST `dma`(+) rows of [date, o, h, l, close, v]. Returns
    {pct, above, sample, asof, dma, excluded_stale, excluded_short,
    excluded_fund} or None when fewer than `min_sample` names are usable — a
    thin store must not wear a confident number.

    min_sample=1000 (REVIEW FIX, PR #68 — was 200): the live store
    held ~191 names, and the daily custom-universe top-up would have grown
    it to ~240 — at which point a "full store" reading would have crossed a
    200 bar carrying ~±6pp of sampling error, nearly the same noise the F3
    fix exists to remove. At 1000+ names the error is ~±3pp and shrinking;
    below the bar the gate keeps its honest bucket fallback. The weekly
    top-up now backfills the whole universe file, so the store reaches
    ~2,400 names within a few Fridays and the bar clears itself.

    Since 2026-10-16 this whole-store number is CONTEXT on the screens; the
    entry gate reads `gate_breadth` (the study panel) — see the note there.
    """
    rows, newest, funds = _reduce(docs, dma, exclude)
    return _reading(rows, newest, dma, min_sample, funds=funds)


# ---------------------------------------------------------------------------
# THE GATE READS THE POPULATION ITS BAND WAS MEASURED ON (2026-10-16).
#
# Owner: "new position are not added, its not using the all 5 trade
# segements as well" -> "relook n fix". The breadth gate blocks ALL FIVE
# categories inside its 40-65% band, and that band was FITTED
# (research/breadth_recut_2026-09-09, scripts/breadth_gate_recut.py) on the
# breadth of the committed 191-name study panel. The live gate compared those
# edges with a DIFFERENT number: the share of every stored name (~2,200 —
# small caps, mid caps, the liquid scan list and, until today, ETFs) above its
# 200-DMA. Measured over the 167 sessions 2026-02-11..10-15 with today's
# public candles, the populations sit at different levels (whole market
# -5.1pp vs the panel on average, the liquid scan list +6.9pp) and the gate
# decision agreed with the panel's on only 65-68% of sessions. Since 09-24
# the panel has read 24-32% (the band's "washed out, buyable" side) while the
# whole store sat on the 40% edge and the scan-list fallback read 50-53%
# (blocked), so the gate held every category back on a number its own
# evidence never measured.
#
# Not a loosening: over those same 167 sessions the panel reading sits in the
# band on 75% of them, the whole store on 53%. It is the definition the edges
# were measured with, nothing more.
# ---------------------------------------------------------------------------
STUDY_PANEL_ZIP = "history_upstox_export_2026-07-19.zip"
#: A panel reading on fewer names is a different population. ASSUMED: ~80%
#: of the 191-name panel.
PANEL_MIN_SAMPLE = 150
#: Panel names are topped up every trading day (scheduler adds them to the
#: daily top-up), so a close a week behind the newest is not today's vote.
#: ASSUMED: a long weekend plus a holiday is 4-5 calendar days.
PANEL_STALE_DAYS = 7
_PANEL_CACHE: dict = {}


def study_panel(path: Optional[str] = None) -> frozenset:
    """The 191 tickers the breadth band was measured on, read from the
    committed study zip's file names. Empty when the zip cannot be read —
    the callers then fall back to the pre-2026-10-16 readings."""
    import os
    import zipfile
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "..", "data",
                            STUDY_PANEL_ZIP)
    key = os.path.abspath(path)
    if key in _PANEL_CACHE:
        return _PANEL_CACHE[key]
    try:
        names = zipfile.ZipFile(path).namelist()
    except (OSError, zipfile.BadZipFile):
        return frozenset()
    out = frozenset(n[:-4] for n in names if n.endswith(".NS.csv"))
    _PANEL_CACHE[key] = out
    return out


def gate_breadth(docs: Iterable[dict], dma: int = 200,
                 exclude: Iterable[str] = (),
                 panel: Optional[Iterable[str]] = None,
                 full_min_sample: int = 1000) -> Optional[dict]:
    """The reading the entry gate reads, from ONE pass over the store.

    The study-panel reading when at least PANEL_MIN_SAMPLE panel names have a
    fresh close (`population` "study_panel"), with the whole-store numbers
    beside it as `full_*` context. When the panel is too thin it degrades to
    exactly the pre-2026-10-16 whole-store reading (`population`
    "full_store") — never to a guess. None when neither can be read."""
    rows, newest, funds = _reduce(docs, dma, exclude)
    full = _reading(rows, newest, dma, full_min_sample, funds=funds)
    panel = set(study_panel() if panel is None else panel)
    pr = (_reading(rows, newest, dma, PANEL_MIN_SAMPLE, include=panel,
                   stale_days=PANEL_STALE_DAYS) if panel else None)
    if pr is not None:
        pr["population"] = "study_panel"
        pr["panel_size"] = len(panel)
        if full is not None:
            pr["full_pct"] = full["pct"]
            pr["full_sample"] = full["sample"]
            pr["full_excluded_fund"] = full["excluded_fund"]
        return pr
    if full is not None:
        full["population"] = "full_store"
        full["panel_size"] = len(panel)
        return full
    return None


def population_label(reading: Optional[dict]) -> str:
    """Plain words for where a breadth reading came from."""
    pop = (reading or {}).get("population")
    n = (reading or {}).get("sample")
    if pop == "study_panel":
        return (f"study panel — {n} of the {reading.get('panel_size', 191)} "
                f"large caps the 40-65% band was measured on")
    if pop == "scan_panel":
        return (f"study panel names in today's scan ({n} names) — the "
                f"population the band was measured on")
    if pop == "full_store":
        return f"whole store ({n} names; the study panel was too thin)"
    if pop == "scan":
        return f"scan sample ({n} names; the study panel was too thin)"
    return f"whole store ({n} names)"


def is_fresh(reading: Optional[dict], today, max_age_days: int) -> bool:
    """Whether a stored reading is recent enough for the gate to trust.

    `today` is a date. Past `max_age_days` the gate falls back to the live
    bucket sample — stale-wide loses to fresh-narrow once the gap grows."""
    from datetime import date, timedelta
    if not reading or not reading.get("asof") or reading.get("pct") is None:
        return False
    try:
        asof = date.fromisoformat(str(reading["asof"])[:10])
    except ValueError:
        return False
    return (today - asof) <= timedelta(days=max_age_days)
