"""MARS valuation chain — stages 00 / 09: the VALUATION BASIS, chosen FIRST.

Owner order 2026-10-14 ("lets hit the formulas work completion"), built on
the 2026-10-12 task book sheet "Sector methods" (21 rows, verbatim below)
and the owner's docx addition "valuation basis FIRST". Money impact: NONE —
display only; engine / risk / orders / danger never read this module (the
package boundary test pins it).

What it does
------------
* `SECTOR_METHODS` — the owner's 21 sector rows, each with a machine
  `method` code and a valuation `family` (earnings / book / ev_ebitda / nav
  / ev_sales / embedded_value / sotp) that the scenario stage switches on.
  The family is the FIRST valuation multiple the row's primary basis names
  (rule stated here, not hidden).
* `label_for(ticker, sector_map, industry_lookup)` — the app's own sector
  label (cfg.sector_map) first, else the Nifty-500 constituents file's
  "Industry" column, else unmapped.
* `route_for(ticker, sector_map, company_name, industry_lookup)` — label →
  method row, with keyword rules on the company name / ticker where a label
  is too coarse (Financial → bank / NBFC / insurance / AMC). An unmapped or
  coarse-only label (Media, Textiles, Diversified, Unknown, none) is
  UNDECLARED — never forced into a method (task book rule: "unmapped names
  → route UNDECLARED → review"). Overlays: PSU (name/ticker keyword) and
  holding company (name keyword) — both ASSUMED.

ASSUMED (printable, `ASSUMED` dict)
-----------------------------------
* label_rules — every keyword rule mapping a coarse label to a row;
* keyword_matching — tokens of <= 4 characters match whole words, longer
  ones match a word prefix; the ticker counts when it equals the keyword;
* psu_keywords (+ full-name forms, + exclusions) — the honest source
  (promoter = President of India in the shareholding XBRL) is NOT parsed;
* holding_rule — HOLDING, or INVESTMENT without FINANCE;
* nearest_for_undeclared — a suggestion only; status stays UNDECLARED;
* label "Unknown" in cfg.sector_map is treated as no label (falls through
  to the Nifty-500 file).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
_N500 = _ROOT / "data" / "index_constituents" / "2026-08-09_ind_nifty500list.csv"

FAMILIES: tuple[str, ...] = ("earnings", "book", "ev_ebitda", "nav",
                             "ev_sales", "embedded_value", "sotp")

# ---------------------------------------------------------------------------
# The owner's 21 rows (task book sheet "Sector methods"), verbatim.
# ---------------------------------------------------------------------------
_ROWS: tuple[tuple, ...] = (
    (1, "IT services / ER&D",
     "Earnings (P/E, PEG), EV/EBITDA cross-check", "Diluted EPS",
     "USD revenue growth, deal TCV, margin (wage, utilisation, attrition), rupee",
     "Buybacks/dividends, cost cuts, bench reduction; multiples de-rate before earnings fall",
     "US/EU tech budgets, H-1B/visa policy, USDINR (+1% ≈ +0.25pp margin), US rates",
     "Statements (USD trap), USDINR, attrition from filings text",
     "Deal TCV, utilisation, segment mix",
     "earnings", "earnings"),
    (2, "Banks (private/PSU)",
     "P/B × ROE (justified P/B = (ROE−g)/(COE−g)); P/E secondary",
     "Book value per share, EPS",
     "Loan growth, NIM, CASA, credit cost, GNPA/NNPA, PCR, CAR",
     "Provision buffers, slower growth, capital raise (dilution); valuation floors at ~1× book for quality",
     "RBI repo, liquidity, priority-sector rules, deposit insurance, global rate cycle",
     "Annual statements (bank format partly), shareholding",
     "NIM, GNPA, CASA, CAR (from results PDFs / NSE filings)",
     "bank_pb_roe", "book"),
    (3, "NBFC / HFC",
     "P/B × ROA/ROE, AUM growth", "BVPS",
     "AUM growth, spread, cost of funds, GNPA, leverage",
     "Funding squeeze first (2018 IL&FS pattern); liquidity cover matters more than P/E",
     "RBI scale-based rules, bond yields, bank funding",
     "Statements", "AUM, spread, ALM",
     "nbfc_pb", "book"),
    (4, "Insurance",
     "Embedded value (P/EV), VNB margin", "EV per share",
     "APE growth, VNB margin, persistency, solvency",
     "Long-duration book cushions; equity-market falls hit ULIP income",
     "IRDAI, tax on policies, bond yields",
     "Statements", "EV, VNB, persistency",
     "insurance_ev", "embedded_value"),
    (5, "AMC / broking / exchanges",
     "P/E on AUM-linked earnings, EV/Sales", "EPS",
     "AUM, yield, flows, volumes",
     "Earnings fall with market levels; cost base fixed",
     "SEBI fee caps, STT",
     "Statements", "AUM, flows",
     "amc_pe", "earnings"),
    (6, "Pharma (formulations/API)",
     "EV/EBITDA, P/E; pipeline optionality", "EPS",
     "US generics pricing, ANDA approvals, India chronic growth, USFDA status, API prices",
     "Defensive demand; FDA actions are the tail risk; rupee helps exporters",
     "USFDA, US drug pricing policy, USDINR, China API supply",
     "Statements, news regulatory family", "Pipeline, plant status",
     "pharma_ev_ebitda", "ev_ebitda"),
    (7, "Hospitals / diagnostics",
     "EV/EBITDA, EV per bed", "EBITDA per share",
     "Occupancy, ARPOB, bed adds, payer mix",
     "Defensive; capex-heavy growth slows",
     "Price caps, insurance rules",
     "Statements", "Beds, ARPOB",
     "hospital_ev_ebitda", "ev_ebitda"),
    (8, "Metals (steel/aluminium/zinc)",
     "EV/EBITDA at MID-CYCLE margin; P/B at trough", "EBITDA per share, BVPS",
     "LME/China prices, spreads, volumes, net debt",
     "Trough earnings mislead P/E; use mid-cycle EBITDA and net debt/EBITDA; dividends cut",
     "Export duty, safeguard duty, China stimulus, USD, carbon rules",
     "Statements, sector index", "Realisation, spread, volumes",
     "metals_midcycle", "ev_ebitda"),
    (9, "Oil & gas / OMC",
     "EV/EBITDA, P/B (OMC), EV/reserves (upstream)", "EBITDA per share",
     "Crude, GRM, marketing margin, subsidy/under-recovery",
     "Policy-driven margins; crude spikes crush OMC margins",
     "Excise, LPG subsidy, windfall tax, OPEC",
     "Statements; Brent NOT stored", "Brent, GRM",
     "oil_gas", "ev_ebitda"),
    (10, "Power / utilities",
     "Regulated equity × allowed ROE; P/B; EV/EBITDA for merchant", "BVPS, EPS",
     "Regulated base growth, PLF, tariff orders, receivables from discoms",
     "Stable; discom dues and fuel pass-through are the risks",
     "CERC/SERC tariffs, coal policy, RE targets",
     "Statements", "Regulated equity, PLF",
     "power_regulated", "book"),
    (11, "Capital goods / defence / shipyards",
     "Order book ÷ revenue, EV/EBITDA, P/E on execution", "EPS",
     "Order inflow, book-to-bill, execution, working capital, margins",
     "Order cancellations/delays; working-capital stress; government capex continuity",
     "Defence budget, DAP, export clearances, PSU dividends",
     "Statements, news order_win family", "Order book (from results/filings)",
     "capital_goods", "ev_ebitda"),
    (12, "Auto / auto components",
     "EV/EBITDA, P/E; EV/Sales at trough", "EPS",
     "Volumes, mix, commodity costs (steel, aluminium, rubber), EV transition, exports",
     "Volume cycles; inventory; fixed-cost leverage",
     "GST on vehicles, PLI, emission norms, EU/US auto R&D budgets",
     "Statements, sector index", "Monthly volumes",
     "auto", "ev_ebitda"),
    (13, "Real estate",
     "NAV per share (discounted land/project value), pre-sales, net debt",
     "NAV per share",
     "Pre-sales, collections, launches, net debt, interest rates",
     "Rates up → NAV discount widens; leverage kills; collections vs sales",
     "RERA, stamp duty, home-loan rates",
     "Statements, shareholding", "Pre-sales, NAV",
     "realty_nav", "nav"),
    (14, "Cement / building materials",
     "EV/tonne, EV/EBITDA", "EBITDA per tonne",
     "Volumes, realisation, fuel/power cost, capacity adds",
     "Price discipline; fuel cost swings",
     "Coal/pet-coke prices, infra spend",
     "Statements", "Capacity, EBITDA/t",
     "cement_ev_tonne", "ev_ebitda"),
    (15, "FMCG / consumer",
     "P/E with growth, EV/EBITDA; DCF", "EPS",
     "Volume growth, pricing, gross margin (palm oil, crude derivatives), rural demand",
     "Defensive; multiples stay high; input-cost cycles",
     "GST, agri policy, monsoon",
     "Statements", "Volume growth",
     "fmcg_pe", "earnings"),
    (16, "Retail / hotels / airlines",
     "EV/EBITDA, same-store growth, EBITDA per room, EV/seat", "EBITDA per share",
     "SSSG, occupancy, ARR, fuel, lease costs",
     "Operating leverage both ways; fuel and leases",
     "ATF tax, tourism policy",
     "Statements", "SSSG, occupancy",
     "retail_ev_ebitda", "ev_ebitda"),
    (17, "Telecom",
     "EV/EBITDA, ARPU", "EBITDA per share",
     "ARPU, subscriber adds, capex, spectrum debt",
     "Tariff hikes repair; leverage is the risk",
     "AGR dues, spectrum policy",
     "Statements", "ARPU",
     "telecom_ev_ebitda", "ev_ebitda"),
    (18, "Chemicals / agro",
     "EV/EBITDA, P/E", "EPS",
     "Spreads, China supply, capex cycle",
     "Spread compression; inventory losses",
     "Anti-dumping, China capacity",
     "Statements", "Spreads",
     "chemicals", "ev_ebitda"),
    (19, "Infra / EPC / logistics",
     "EV/EBITDA, P/E, order book", "EPS",
     "Order book, execution, working capital, toll/volume",
     "Working-capital stress; claims",
     "Government capex, NHAI, GST e-way",
     "Statements", "Order book",
     "infra_epc", "ev_ebitda"),
    (20, "PSU (any sector)",
     "Sector method + dividend yield floor + OFS supply watch", "per route",
     "Dividend policy, government stake sales, capex mandates",
     "OFS supply caps rallies; dividend yield supports",
     "Disinvestment calendar",
     "Shareholding (GoI %), CA (dividends)", "OFS calendar",
     # Only ever an OVERLAY on the sector route; its family is the
     # dividend-yield floor's (earnings-based) and is never switched on.
     "psu_overlay", "earnings"),
    (21, "Holding companies",
     "Sum-of-parts with holding discount (40-60%)", "NAV per share",
     "Listed stake values, unlisted assets",
     "Discount widens in stress",
     "Tax on dividends",
     "Shareholding", "Stake map (item 11)",
     "holding_sotp", "sotp"),
)

_KEYS = ("row", "sector", "primary_basis", "per_share_metric", "key_drivers",
         "bad_time_behaviour", "policy_links", "data_have", "data_missing",
         "method", "family")

SECTOR_METHODS: list[dict] = [dict(zip(_KEYS, r)) for r in _ROWS]
SECTOR_METHODS[19]["overlay_only"] = True          # row 20 (PSU)

_BY_METHOD: dict[str, dict] = {r["method"]: r for r in SECTOR_METHODS}


def method_row(method: str) -> Optional[dict]:
    """The SECTOR_METHODS row for a method code (a copy), or None."""
    r = _BY_METHOD.get(method)
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# ASSUMED rules (printable on the Den)
# ---------------------------------------------------------------------------
_FIN_BANK = ["BANK"]
_FIN_AMC = ["AMC", "ASSET MANAGEMENT", "BSE", "MCX", "CDSL", "CAMS", "ANGEL",
            "BROKING", "SECURITIES", "EXCHANGE", "DEPOSITORY"]
_FIN_INS = ["LIFE", "INSURANCE", "GENERAL INS", "ASSURANCE"]
_FIN_NBFC = ["FINANCE", "FINSERV", "FINCORP", "CAPITAL", "HOUSING", "HFC",
             "CREDIT", "LEASING"]
_HOSPITAL = ["HOSPITAL", "HEALTH", "DIAGNOS", "PATH LAB", "PATHLAB", "MEDICAL",
             "CLINIC", "HRUDAYALAYA", "QUALITY CARE"]
_REALTY = ["REALTY", "ESTATE", "DLF", "LODHA", "OBEROI", "PRESTIGE", "BRIGADE",
           "PHOENIX", "SOBHA", "GODREJ PROP", "SUNTECK", "MAHINDRA LIFE"]
_CEMENT = ["CEMENT", "ULTRATECH", "AMBUJA", "ACC", "SHREE", "DALMIA",
           "JK LAKSHMI", "RAMCO", "BIRLA CORP"]
_RETAIL = ["RETAIL", "TRENT", "DMART", "AVENUE", "HOTEL", "INDHOTEL", "LEMON",
           "CHALET", "AIRLINE", "INDIGO", "INTERGLOBE", "SPICEJET", "RESTAURANT",
           "JUBILANT FOOD", "DEVYANI", "WESTLIFE"]
_AIRLINE = ["AIRLINE", "INDIGO", "INTERGLOBE", "SPICEJET", "AVIATION"]

# One rule set per label: (app labels, Nifty-500 industries, ordered
# [(keywords, method, why)], default method or None = UNDECLARED, why).
_LABEL_RULES: tuple[dict, ...] = (
    {"labels": ["Financial"], "industries": ["Financial Services"],
     "keywords": [(_FIN_BANK, "bank_pb_roe", "name says BANK"),
                  (_FIN_AMC, "amc_pe", "name/ticker says AMC/broking/exchange/depository"),
                  (_FIN_INS, "insurance_ev", "name says LIFE/INSURANCE/ASSURANCE"),
                  (_FIN_NBFC, "nbfc_pb", "name says FINANCE/CAPITAL/HOUSING/CREDIT")],
     "default": "nbfc_pb", "default_why": "financial default (ASSUMED)"},
    {"labels": ["IT"], "industries": ["Information Technology"],
     "keywords": [], "default": "earnings", "default_why": "IT label"},
    {"labels": ["Pharma"], "industries": ["Healthcare"],
     "keywords": [(_HOSPITAL, "hospital_ev_ebitda",
                   "name says HOSPITAL/HEALTH/DIAGNOSTIC/PATH LAB/MEDICAL")],
     "default": "pharma_ev_ebitda", "default_why": "pharma/healthcare default"},
    {"labels": ["Metal"], "industries": ["Metals & Mining"],
     "keywords": [], "default": "metals_midcycle", "default_why": "metal label"},
    {"labels": ["Energy"], "industries": ["Oil Gas & Consumable Fuels"],
     "keywords": [], "default": "oil_gas", "default_why": "energy label"},
    {"labels": ["Utilities"], "industries": ["Power"],
     "keywords": [], "default": "power_regulated", "default_why": "utilities/power label"},
    {"labels": ["Capital Goods", "Defence"], "industries": ["Capital Goods"],
     "keywords": [], "default": "capital_goods", "default_why": "capital goods/defence label"},
    {"labels": ["Auto"], "industries": ["Automobile and Auto Components"],
     "keywords": [], "default": "auto", "default_why": "auto label"},
    {"labels": ["Construction"], "industries": ["Realty", "Construction"],
     "keywords": [(_REALTY, "realty_nav", "name/ticker says REALTY/ESTATE or a named developer"),
                  (_CEMENT, "cement_ev_tonne", "name/ticker says CEMENT or a named cement maker")],
     "default": "infra_epc", "default_why": "construction default = infra/EPC (ASSUMED)"},
    {"labels": [], "industries": ["Construction Materials"],
     "keywords": [(_REALTY, "realty_nav", "name/ticker says REALTY/ESTATE")],
     "default": "cement_ev_tonne",
     "default_why": "Nifty 'Construction Materials' = cement / building materials (ASSUMED)"},
    {"labels": ["Consumer"], "industries": ["Consumer Services", "Consumer Durables"],
     "keywords": [(_RETAIL, "retail_ev_ebitda",
                   "name/ticker says RETAIL/HOTEL/AIRLINE/RESTAURANT or a named chain")],
     "default": "fmcg_pe", "default_why": "consumer default = FMCG/consumer (ASSUMED)"},
    {"labels": ["FMCG"], "industries": ["Fast Moving Consumer Goods"],
     "keywords": [], "default": "fmcg_pe", "default_why": "FMCG label"},
    {"labels": ["Chemicals"], "industries": ["Chemicals"],
     "keywords": [], "default": "chemicals", "default_why": "chemicals label"},
    {"labels": ["Telecom"], "industries": ["Telecommunication"],
     "keywords": [], "default": "telecom_ev_ebitda", "default_why": "telecom label"},
    {"labels": ["Logistics"], "industries": ["Services"],
     "keywords": [(_AIRLINE, "retail_ev_ebitda", "name/ticker says AIRLINE/AVIATION")],
     "default": "infra_epc", "default_why": "logistics/services default = infra/EPC/logistics"},
    {"labels": ["Aviation"], "industries": [],
     "keywords": [], "default": "retail_ev_ebitda", "default_why": "aviation label → airlines row"},
    {"labels": ["Media", "Textiles", "Diversified", "Unknown"],
     "industries": ["Media Entertainment & Publication", "Textiles", "Diversified"],
     "keywords": [], "default": None,
     "default_why": "label too coarse for a method — UNDECLARED, review (task book rule)"},
)

_PSU_KEYWORDS = ["BHARAT", "HINDUSTAN", "INDIAN OIL", "OIL INDIA", "ONGC", "NTPC",
                 "POWER GRID", "COAL INDIA", "SAIL", "NMDC", "BHEL", "BEL", "HAL",
                 "GAIL", "NHPC", "SJVN", "IRFC", "RVNL", "IRCTC", "CONCOR", "NBCC",
                 "NALCO", "RECLTD", "PFC", "SBIN", "BANK OF", "PNB", "CANARA",
                 "UNION BANK", "INDIAN BANK", "BANK OF BARODA", "LIC", "NEW INDIA",
                 "GIC", "MAZAGON", "GRSE", "COCHIN SHIPYARD", "BDL", "BEML",
                 "MIDHANI", "ENGINEERS INDIA", "HUDCO", "IREDA", "NLC", "MOIL", "KIOCL"]
# Full-name forms of the abbreviations above (so a NAME match finds the same
# companies the ticker list means) + PSU banks/insurers the list misses.
_PSU_FULL_NAMES = ["STEEL AUTHORITY", "NATIONAL ALUMINIUM", "OIL AND NATURAL GAS",
                   "RAIL VIKAS", "INDIAN RAILWAY", "CONTAINER CORPORATION",
                   "REC LTD", "REC LIMITED", "POWER FINANCE CORPORATION",
                   "STATE BANK OF INDIA", "PUNJAB NATIONAL BANK",
                   "INDIAN OVERSEAS BANK", "UCO BANK", "CENTRAL BANK OF INDIA",
                   "LIFE INSURANCE CORPORATION", "GENERAL INSURANCE CORPORATION",
                   "HOUSING AND URBAN DEVELOPMENT", "NLC INDIA",
                   "INDIAN RENEWABLE ENERGY DEVELOPMENT", "MISHRA DHATU",
                   "GARDEN REACH", "BHARAT DYNAMICS", "HINDUSTAN AERONAUTICS"]
# Private companies the keyword list would catch by name alone.
_PSU_EXCLUSIONS = ["HINDUSTAN UNILEVER", "HINDUSTAN ZINC", "BHARAT FORGE",
                   "BHARAT BIJLEE", "HINDUSTAN MEDIA", "HINDUSTAN FOODS",
                   "BHARAT RASAYAN", "BHARAT WIRE", "PNB HOUSING",
                   "DALMIA BHARAT", "CITY UNION BANK"]

# The holding-company rule only overrides these labels (or an UNDECLARED
# route): an operating-sector label keeps its method — "ACME Solar Holdings"
# is a power producer, "Tube Investments of India" a manufacturer.
_HOLDING_LABELS = ("Financial", "Financial Services", "Diversified")

_CORPORATE_FORM = r"\b(LIMITED|LTD|BANK|CORPORATION|ASSURANCE)\b"

_NEAREST: dict[str, str] = {"Media": "fmcg_pe", "Textiles": "chemicals",
                            "Diversified": "holding_sotp",
                            "Media Entertainment & Publication": "fmcg_pe"}

ASSUMED: dict = {
    "label_rules": [
        {"labels": r["labels"], "industries": r["industries"],
         "keyword_rules": [{"keywords": list(k), "method": m, "why": w}
                           for (k, m, w) in r["keywords"]],
         "default": r["default"], "default_why": r["default_why"]}
        for r in _LABEL_RULES],
    "keyword_matching": ("upper-cased name, punctuation → space, & → AND; a keyword "
                         "of <= 4 characters matches a whole word, a longer one a "
                         "word prefix; the ticker counts when it equals the keyword "
                         "with spaces removed (or starts with it, keyword > 4 chars). "
                         "PSU keywords match whole words and the exact ticker only"),
    "financial_order": ("BANK → AMC/broking/exchange → insurance → NBFC → default "
                        "NBFC. AMC is tested before insurance so 'Nippon Life India "
                        "Asset Management' reads AMC, not insurance (ASSUMED here)"),
    "hospital_keywords_note": ("plain 'LAB' is NOT a hospital keyword — it would route "
                               "Dr. Reddy's / Divi's / Laurus Laboratories to hospitals; "
                               "'PATH LAB' is used instead; 'HOSP' is spelled 'HOSPITAL' so the "
                               "<= 4-character whole-word rule cannot drop 'Hospitals' "
                               "(ASSUMED here)"),
    "psu_keywords": list(_PSU_KEYWORDS),
    "psu_full_names": list(_PSU_FULL_NAMES),
    "psu_exclusions": list(_PSU_EXCLUSIONS),
    "psu_rule": ("a PSU keyword equals the ticker, OR the company name carries a "
                 "corporate-form word (LIMITED/LTD; nationalised banks and statutory "
                 "corporations carry none, so BANK/CORPORATION/ASSURANCE also count) "
                 "AND a PSU keyword matches the name; no exclusion may match"),
    "psu_source": "name keyword (ASSUMED); shareholding promoter name not parsed",
    "holding_rule": ("name says HOLDING, or INVESTMENT without FINANCE "
                     "(Cholamandalam Investment and Finance stays an NBFC); applied "
                     "only over a Financial / Financial Services / Diversified label "
                     "or an UNDECLARED route — an operating-sector label keeps its "
                     "method (ACME Solar Holdings, Tube Investments of India)"),
    "nearest_for_undeclared": dict(_NEAREST),
    "unknown_label": "cfg.sector_map 'Unknown' counts as no label (falls to the Nifty-500 file)",
    "family_rule": "a row's family = the FIRST valuation multiple its primary basis names",
}


# ---------------------------------------------------------------------------
# Loaders (injectable)
# ---------------------------------------------------------------------------
_N500_CACHE: dict[str, dict] = {}


def load_nifty500_industry(path: Path = _N500) -> dict[str, dict]:
    """{SYMBOL: {"industry": str, "company": str}} from the committed Nifty-500
    constituents file. Missing file → {} (every name then reads 'unmapped')."""
    if path == _N500 and _N500_CACHE:
        return _N500_CACHE
    out: dict[str, dict] = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                sym = (r.get("Symbol") or "").strip().upper()
                if sym:
                    out[sym] = {"industry": (r.get("Industry") or "").strip(),
                                "company": (r.get("Company Name") or "").strip()}
    except OSError:
        return {}
    if path == _N500:
        _N500_CACHE.update(out)
    return out


def _bare(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    for suf in (".NS", ".BO"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t


def _lookup_row(sym: str, industry_lookup: Optional[dict]) -> dict:
    look = industry_lookup if industry_lookup is not None else load_nifty500_industry()
    row = look.get(sym) or look.get(sym + ".NS")
    if row is None:
        return {}
    if isinstance(row, str):
        return {"industry": row, "company": ""}
    return {"industry": str(row.get("industry") or row.get("Industry") or ""),
            "company": str(row.get("company") or row.get("Company Name") or "")}


def label_for(ticker: str, sector_map: dict,
              industry_lookup: Optional[dict] = None) -> tuple[str, str]:
    """(label, source): cfg.sector_map first ("sector_map"), else the Nifty-500
    file's Industry ("nifty500_industry"), else ("", "unmapped")."""
    sym = _bare(ticker)
    sm = sector_map or {}
    lab = sm.get(sym + ".NS") or sm.get(sym) or sm.get(ticker)
    if lab and str(lab).strip() and str(lab).strip() != "Unknown":
        return str(lab).strip(), "sector_map"
    row = _lookup_row(sym, industry_lookup)
    if row.get("industry"):
        return row["industry"], "nifty500_industry"
    return "", "unmapped"


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------
def _norm_name(name: str) -> str:
    s = (name or "").upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _kw_hit(kw: str, name_n: str, sym: str, whole: bool = False) -> bool:
    """`whole=True` (the PSU list): whole words on the name, exact ticker only."""
    k = _norm_name(kw)
    if not k:
        return False
    compact = k.replace(" ", "")
    if sym and (sym == compact or
                (not whole and len(compact) > 4 and sym.startswith(compact))):
        return True
    if not name_n:
        return False
    if whole or len(compact) <= 4:
        return re.search(r"\b" + re.escape(k) + r"\b", name_n) is not None
    return re.search(r"\b" + re.escape(k), name_n) is not None


def _first_hit(kws, name_n: str, sym: str, whole: bool = False) -> Optional[str]:
    for k in kws:
        if _kw_hit(k, name_n, sym, whole):
            return k
    return None


def _rule_for_label(label: str, source: str) -> Optional[dict]:
    for r in _LABEL_RULES:
        pool = r["labels"] if source == "sector_map" else r["industries"]
        if label in pool:
            return r
    # a Nifty industry that equals an app label (e.g. "Chemicals") — try both
    for r in _LABEL_RULES:
        if label in r["labels"] or label in r["industries"]:
            return r
    return None


def _psu_overlay(name_n: str, sym: str) -> Optional[dict]:
    for ex in _PSU_EXCLUSIONS:
        if _kw_hit(ex, name_n, ""):
            return None
    corporate_form = re.search(_CORPORATE_FORM, name_n) is not None
    hit = _first_hit(_PSU_KEYWORDS + _PSU_FULL_NAMES,
                     name_n if corporate_form else "", sym, whole=True)
    if hit is None:
        return None
    row = _BY_METHOD["psu_overlay"]
    return {"method": "psu_overlay", "row": row["row"], "sector": row["sector"],
            "basis": row["primary_basis"], "drivers": row["key_drivers"],
            "defended_by": row["bad_time_behaviour"], "matched": hit,
            "source": ASSUMED["psu_source"]}


def _holding_hit(name_n: str) -> Optional[str]:
    if re.search(r"\bHOLDING", name_n):
        return "HOLDING"
    if re.search(r"\bINVESTMENT", name_n) and not re.search(r"\bFINANCE", name_n):
        return "INVESTMENT"
    return None


def route_for(ticker: str, sector_map: dict, company_name: str = "",
              industry_lookup: Optional[dict] = None) -> dict:
    """Stage 00/09 — the valuation basis for one stock, chosen FIRST.

    Returns {ticker, label, label_source, status "declared"|"UNDECLARED",
    method, family, basis, per_share_metric, drivers, defended_by,
    policy_links, data_have, data_missing, rule, overlays, nearest?}."""
    sym = _bare(ticker)
    label, source = label_for(ticker, sector_map, industry_lookup)
    if not company_name:
        company_name = _lookup_row(sym, industry_lookup).get("company", "")
    name_n = _norm_name(company_name)

    method: Optional[str] = None
    rule = ""
    nearest: Optional[str] = None
    if source == "unmapped":
        rule = ("no sector label in cfg.sector_map and the name is not in the "
                "Nifty-500 file — UNDECLARED, review")
    else:
        lr = _rule_for_label(label, source)
        if lr is None:
            rule = f"label '{label}' ({source}) has no method rule — UNDECLARED, review"
        else:
            for kws, m, why in lr["keywords"]:
                hit = _first_hit(kws, name_n, sym)
                if hit is not None:
                    method = m
                    rule = f"{label} + keyword '{hit}': {why} (ASSUMED)"
                    break
            if method is None:
                method = lr["default"]
                rule = f"{label}: {lr['default_why']}"
            if method is None:
                nearest = _NEAREST.get(label)

    holding = _holding_hit(name_n)
    if holding is not None and (method is None or label in _HOLDING_LABELS):
        method = "holding_sotp"
        rule = (f"name says '{holding}' → holding company, sum-of-parts "
                f"(ASSUMED; label '{label or 'none'}')")
        nearest = None

    overlays: list[dict] = []
    psu = _psu_overlay(name_n, sym)
    if psu is not None:
        overlays.append(psu)

    out: dict = {"ticker": sym, "company": company_name, "label": label,
                 "label_source": source, "rule": rule, "overlays": overlays}
    if method is None:
        out.update({"status": "UNDECLARED", "method": None, "family": None,
                    "basis": None, "per_share_metric": None, "drivers": None,
                    "defended_by": None, "policy_links": None, "data_have": None,
                    "data_missing": None})
        if nearest:
            nr = _BY_METHOD[nearest]
            out["nearest"] = {"method": nearest, "sector": nr["sector"],
                              "note": "suggestion only — status stays UNDECLARED "
                                      "until the owner declares a route (ASSUMED)"}
        return out
    row = _BY_METHOD[method]
    out.update({"status": "declared", "method": method, "family": row["family"],
                "row": row["row"], "sector": row["sector"],
                "basis": row["primary_basis"],
                "per_share_metric": row["per_share_metric"],
                "drivers": row["key_drivers"],
                "defended_by": row["bad_time_behaviour"],
                "policy_links": row["policy_links"],
                "data_have": row["data_have"], "data_missing": row["data_missing"]})
    return out


__all__ = ["SECTOR_METHODS", "FAMILIES", "ASSUMED", "method_row",
           "load_nifty500_industry", "label_for", "route_for"]
