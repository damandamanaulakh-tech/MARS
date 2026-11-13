"""Owner-only reads of saved decisions. Rendering never recomputes a gate."""
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import auth, db, state
from ..templating import templates
from trading.quote_evidence import utc

router = APIRouter()


def _owner(request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("role") != "owner":
        return JSONResponse({"error": "owner_only"}, status_code=403)
    return None


def _valid(key):
    return bool(re.fullmatch(r"[0-9a-f]{32}", key or ""))


def _local_rows(p):
    path = Path(p.cfg.state_dir) / "journal_overflow.jsonl"
    try:
        with path.open() as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _text(value):
    if value is None:
        return "Unavailable"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:,.6g}"
    if isinstance(value, dict):
        return "; ".join(f"{k.replace('_', ' ')}: {_text(v)}" for k, v in value.items())
    if isinstance(value, (tuple, list)):
        return ", ".join(_text(v) for v in value) or "None"
    return str(value)


def _time(value):
    dt = utc(value) if value else None
    return dt.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y %H:%M:%S IST") if dt else "Unavailable"


def _card(decision, title):
    return {"title": title, "data": decision, "at": _time(decision.get("at")),
            "gates": [{"name": name.replace('_', ' ').capitalize(), **g,
                       "value_text": _text(g.get("value")), "threshold_text": _text(g.get("threshold"))}
                      for name, g in (decision.get("gates") or {}).items()]}


@router.get('/market-context')
def market_context(request: Request):
    denied = _owner(request)
    if denied is not None:
        return denied
    from trading import sector_identity as si
    p = state.portfolio()
    saved = [r.get('evidence') or {} for r in p.decision_journal]
    saved += [(getattr(pos, 'trade_evidence', {}) or {}).get('entry') or
              {'ticker': pos.ticker, 'mode': pos.mode, 'path': 'historical_entry'} for pos in p.positions.values()]
    latest = {}
    for d in sorted(saved, key=lambda d: d.get('at', '')):
        if d.get('ticker'):
            latest[(d['ticker'], d.get('mode', 'unknown'), d.get('path', 'unknown'))] = d
    rows = sorted(latest.values(), key=lambda d: d.get('at', ''), reverse=True)[:200]
    if request.query_params.get('format') == 'json':
        return JSONResponse({'scope': 'latest_current_ledger_and_journal_only', 'limit': 200,
                             'rows': [{k: d.get(k) for k in ('ticker','mode','path','id','at','market_context')} for d in rows]})
    registry = si.snapshot()
    return templates.TemplateResponse(request, 'market_context.html', {
        'user': auth.current_user(request), 'active': 'scan', 'rows': rows,
        'source_count': len(registry.get('sources', {})), 'source_total': len(si.FILES),
        'sources': [{'name': name, 'url': si.URLS[name],
                     'observation': (registry.get('sources') or {}).get(name),
                     'attempt': (registry.get('attempts') or {}).get(name)} for name in si.FILES]})


@router.get("/decisions")
def decisions(request: Request, ticker: str = "", mode: str = ""):
    denied = _owner(request)
    if denied is not None:
        return denied
    p = state.portfolio()
    # Recent archive tail plus current RAM; explicit limits on the page.
    source = list(db.search_decision_archive(ticker=ticker, mode=mode, limit=500)) + list(p.decision_journal)
    rows = {}
    for row in source:
        d = row.get("evidence") or {}
        if not d.get("id") or not d.get("gates"):
            continue
        if ticker and ticker.upper() not in str(d.get("ticker", "")).upper():
            continue
        if mode and d.get("mode") != mode:
            continue
        rows[d["id"]] = d
    ordered = sorted(rows.values(), key=lambda d: d.get("at", ""), reverse=True)[:200]
    return templates.TemplateResponse(request, "decisions.html", {
        "user": auth.current_user(request), "active": "scan", "rows": ordered,
        "ticker_filter": ticker, "mode_filter": mode, "time_text": _time})


@router.get("/decisions/{decision_id}")
def decision_view(request: Request, decision_id: str):
    denied = _owner(request)
    if denied is not None:
        return denied
    if not _valid(decision_id):
        return JSONResponse({"error": "invalid_id"}, status_code=400)
    p = state.portfolio()
    d = next((r.get("evidence") for r in reversed(p.decision_journal)
              if r.get("decision_id") == decision_id or (r.get("evidence") or {}).get("id") == decision_id), None)
    d = d or db.read_decision(decision_id)
    if d is None:
        d = next((r.get("evidence") for r in _local_rows(p)
                  if r.get("decision_id") == decision_id or (r.get("evidence") or {}).get("id") == decision_id), None)
    if not d:
        return templates.TemplateResponse(request, "decision_evidence.html", {
            "user": auth.current_user(request), "active": "scan", "cards": [],
            "amendments": [], "missing": "This saved decision is unavailable. No values have been reconstructed."}, status_code=404)
    if request.query_params.get("format") == "json":
        return JSONResponse(d)
    return templates.TemplateResponse(request, "decision_evidence.html", {
        "user": auth.current_user(request), "active": "scan", "cards": [_card(d, "Decision")],
        "amendments": [], "missing": None, "download": str(request.url.path) + "?format=json", "text_value": _text})


@router.get("/trade-evidence/{trade_id}")
def trade_view(request: Request, trade_id: str):
    denied = _owner(request)
    if denied is not None:
        return denied
    if not _valid(trade_id):
        return JSONResponse({"error": "invalid_id"}, status_code=400)
    p = state.portfolio()
    records = [obj.trade_evidence for obj in list(p.positions.values()) + list(p.trades)
               if (getattr(obj, "trade_evidence", {}) or {}).get("trade_id") == trade_id]
    records += db.read_trade_evidence(trade_id)
    # Count-based ledger overflow also carries its complete evidence.
    records += db.read_archived_trade_evidence(trade_id)
    full = [r for r in records if not r.get("archived")]
    if not full:
        return templates.TemplateResponse(request, "decision_evidence.html", {
            "user": auth.current_user(request), "active": "trades", "cards": [],
            "amendments": [], "missing": "Trade evidence is unavailable from storage. Historical gates remain unknown."}, status_code=404)
    entry = full[0].get("entry") or {"status": "unavailable"}
    exits = {r["exit"]["id"]: r["exit"] for r in full if r.get("exit")}
    amendments = {a["id"]: a for r in full for a in r.get("amendments", [])}
    for row in p.decision_journal:
        if row.get("trade_id") == trade_id and row.get("action") == "TRADE_AMENDMENT":
            a = row.get("evidence") or {}
            if a.get("id"):
                amendments[a["id"]] = a
    omitted = max(r.get("amendments_omitted", 0) for r in full)
    if omitted:
        for a in db.read_trade_amendments(trade_id):
            amendments[a["id"]] = a
        for row in _local_rows(p):
            if row.get("trade_id") == trade_id and row.get("action") == "TRADE_AMENDMENT":
                a = row.get("evidence") or {}
                if a.get("id"):
                    amendments[a["id"]] = a
    data = {"trade_id": trade_id, "entry": entry, "exits": list(exits.values()),
            "amendments": sorted(amendments.values(), key=lambda a: a["at"]),
            "archive_tail_limit": 200, "exit_archive_limit_per_store": 100, "older_amendments_recorded": omitted}
    if request.query_params.get("format") == "json":
        return JSONResponse(data)
    cards = [_card(entry, "Entry")] + [_card(d, "Exit / partial exit") for d in exits.values()]
    return templates.TemplateResponse(request, "decision_evidence.html", {
        "user": auth.current_user(request), "active": "trades", "cards": cards,
        "amendments": data["amendments"], "text_value": _text,
        "missing": "Original entry evidence was not recorded; current settings are not substituted." if entry.get("status") == "unavailable" else None,
        "archive_notice": f"This view includes the current ledger plus up to 100 exits per archive store and 200 archived amendments. {omitted} amendments left the position’s recent tail. Missing archive records remain unavailable.",
        "download": str(request.url.path) + "?format=json"})
