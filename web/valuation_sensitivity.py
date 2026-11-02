"""Read-only view model for /den/valuation/scenarios. No storage writes."""
from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo

from trading.valuation.sensitivity import calculate, number

DEFAULT_TICKERS = "COFORGE,ICICIBANK,DRREDDY,TVSMOTOR,BHARTIARTL"
MAX_STOCKS = 20
PRESETS = {
    "falling": {"market": -5, "fpi": -12000, "dii": 8000,
                "sectors": {"IT": -8, "Fin Service": -6, "Pharma": -2, "Auto": -7}},
    "unchanged": {"market": 0, "fpi": 0, "dii": 0, "sectors": {}},
    "rising": {"market": 5, "fpi": 8000, "dii": 4000,
               "sectors": {"IT": 8, "Fin Service": 6, "Pharma": 3, "Auto": 7}},
}
FIELDS = (
    ("market", "Market move (%)", -90, 200, None),
    ("fpi", "FPI net flow — 5 sessions (₹ crore)", -1e7, 1e7, None),
    ("dii", "DII net flow — 5 sessions (₹ crore)", -1e7, 1e7, None),
    ("coefficient", "Sector coefficient (assumed)", 0, 5, 1),
    ("margin", "Base entry discount (%)", 0, 90, 10),
    ("cap", "Maximum extra flow discount (percentage points)", 0, 50, 5),
    ("scale", "Flow scale (₹ crore)", 1, 1e7, 10000),
)


def build(params, document, today=None):
    today = today or datetime.now(ZoneInfo("Asia/Kolkata")).date()
    errors, fields, values = [], [], {}
    preset_name = params.get("preset", "falling")
    if preset_name not in PRESETS:
        errors.append("Unknown scenario preset")
        preset_name = "falling"
    preset = PRESETS[preset_name]
    for key, label, low, high, default in FIELDS:
        raw = params.get(key, preset.get(key, default))
        fields.append({"key": key, "label": label, "value": raw, "min": low, "max": high})
        try:
            values[key] = number(raw, label, low, high)
        except ValueError as e:
            errors.append(str(e))
    if not errors and values["margin"] + values["cap"] >= 100:
        errors.append("Base discount plus maximum extra discount must be below 100%")
    typed = params.get("tickers", DEFAULT_TICKERS)
    names = []
    for name in str(typed).upper().replace(" ", ",").split(","):
        if not name:
            continue
        if not name.endswith(".NS"):
            name += ".NS"
        if not re.fullmatch(r"[A-Z0-9&_.-]{1,30}\.NS", name):
            errors.append("Use NSE symbols separated by commas")
            break
        if name not in names:
            names.append(name)
    if not names or len(names) > MAX_STOCKS:
        errors.append(f"Choose between 1 and {MAX_STOCKS} stocks")
    names = names[:MAX_STOCKS]
    stored = document.get("rows") or {}
    rows = []
    for ticker in names:
        record = stored.get(ticker) or {}
        sector = record.get("sector_index") or "Nifty 50"
        fallback = sector == "Nifty 50"
        sector_key = "sector_" + ticker
        sector_input = params.get(sector_key, preset["sectors"].get(sector, preset["market"]))
        row = {"ticker": ticker, "sector": sector, "sector_key": sector_key,
               "sector_input": sector_input, "fallback": fallback, "asof": record.get("asof"),
               "base": record.get("base_target"), "beta": record.get("beta"),
               "beta_n": record.get("beta_n"), "result": None, "error": "", "notes": []}
        try:
            if not record or record.get("range_status") != "OK":
                raise ValueError("DATA NEEDED: no usable saved valuation range for this stock")
            asof = datetime.strptime(str(record.get("asof")), "%Y-%m-%d").date()
            age = (today - asof).days
            if age < 0:
                raise ValueError("DATA NEEDED: saved valuation date is in the future")
            if age > 7:
                row["notes"].append(f"Older baseline: {age} calendar days; results are anchored to that saved date.")
            if number(record.get("beta_n"), "Beta observations", 0, 1e6) < 120:
                raise ValueError("DATA NEEDED: market sensitivity needs at least 120 observations")
            if not fallback:
                sector_input = number(sector_input, f"{ticker} sector move (%)", -90, 200)
            if not fallback and sector not in preset["sectors"] and preset_name != "unchanged" and sector_key not in params:
                row["notes"].append("No sector-specific preset: assumed sector move equals the preset market move. Edit it below.")
            if not errors:
                row["result"] = calculate(base=record.get("base_target"), beta=record.get("beta"),
                    market_pct=values["market"], sector_pct=sector_input,
                    sector_coefficient=values["coefficient"], fpi_cr=values["fpi"], dii_cr=values["dii"],
                    base_margin_pct=values["margin"], max_extra_pp=values["cap"],
                    flow_scale_cr=values["scale"], sector_fallback=fallback)
        except (ValueError, TypeError) as e:
            row["error"] = str(e)
        rows.append(row)
    return {"preset": preset_name, "tickers": typed, "fields": fields, "errors": errors,
            "rows": rows, "built": document.get("built"), "max_stocks": MAX_STOCKS,
            "has_results": any(r["result"] for r in rows)}
