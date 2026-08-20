"""Universe presets — hardcoded ticker lists for repeatability.

NIFTY_200 contains the top 200 stocks by market cap as of May 2026.
Refresh annually when NSE rebalances the Nifty 200 composition.

We keep the list hardcoded (not fetched live) so backtests are
deterministic and reproducible.

2026-06 cleanup: removed 12 symbols Yahoo no longer serves (merged/renamed/
delisted). 6 renamed to their current symbol, 6 dropped (successor already
listed, or no clean Yahoo symbol):
  ZOMATO->ETERNAL, ADANITRANS->ADANIENSOL, GMRINFRA->GMRAIRPORT,
  L&TFH->LTF, MAHINDCIE->CIEINDIA, TATAMOTORS->TMPV (post-demerger);
  dropped BHARATELE/IDFC (BEL/IDFCFIRSTB already listed),
  LTIM/MINDTREE/PIRAMALENT/PEL (no working Yahoo symbol).
"""

# Nifty 200 (top 200 NSE stocks by mcap, May 2026 composition).
# This is a representative subset. NSE publishes the full list at:
#   https://www.niftyindices.com/indices/equity/broad-based-indices/NIFTY-200
NIFTY_200_TICKERS = [
    # Nifty 50 large caps
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS", "LT.NS",
    "AXISBANK.NS", "MARUTI.NS", "TITAN.NS", "HCLTECH.NS", "BAJFINANCE.NS",
    "SUNPHARMA.NS", "TECHM.NS", "POWERGRID.NS", "ONGC.NS", "COALINDIA.NS",
    "ASIANPAINT.NS", "WIPRO.NS", "NESTLEIND.NS", "ULTRACEMCO.NS",
    "ADANIENT.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "GRASIM.NS",
    "BAJAJFINSV.NS", "M&M.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS",
    "HINDUNILVR.NS", "INDUSINDBK.NS", "DIVISLAB.NS", "DRREDDY.NS",
    "CIPLA.NS", "BRITANNIA.NS", "HEROMOTOCO.NS", "SHRIRAMFIN.NS",
    "TMPV.NS", "TATACONSUM.NS", "ADANIPORTS.NS", "HDFCLIFE.NS",
    "BPCL.NS", "SBILIFE.NS", "APOLLOHOSP.NS", "NTPC.NS",
    "HINDALCO.NS",

    # Mid + Large caps (Nifty 100/200 — illiquid alpha zone per academic studies)
    "ADANIGREEN.NS", "ADANIPOWER.NS", "ADANIENSOL.NS",
    "AMBUJACEM.NS", "ABB.NS", "ABBOTINDIA.NS", "ACC.NS",
    "ALKEM.NS", "AUROPHARMA.NS", "BAJAJHLDNG.NS", "BANKBARODA.NS",
    "BEL.NS", "BERGEPAINT.NS", "BHEL.NS", "BIOCON.NS", "BOSCHLTD.NS",
    "CANBK.NS", "CHOLAFIN.NS", "COLPAL.NS", "CONCOR.NS",
    "DABUR.NS", "DLF.NS", "DMART.NS", "DIXON.NS",
    "FEDERALBNK.NS", "GAIL.NS", "GODREJCP.NS", "GODREJPROP.NS",
    "HAL.NS", "HAVELLS.NS", "HDFCAMC.NS", "ICICIGI.NS",
    "ICICIPRULI.NS", "IDFCFIRSTB.NS", "INDUSTOWER.NS", "INDIGO.NS",
    "IOC.NS", "IRCTC.NS", "JINDALSTEL.NS", "JUBLFOOD.NS",
    "LICI.NS", "MARICO.NS", "MOTHERSON.NS", "MPHASIS.NS",
    "MRF.NS", "MUTHOOTFIN.NS", "NAUKRI.NS", "NMDC.NS",
    "OBEROIRLTY.NS", "OFSS.NS", "PAGEIND.NS",
    "PERSISTENT.NS", "PFC.NS", "PIDILITIND.NS", "PIIND.NS",
    "PNB.NS", "POLYCAB.NS", "POWERINDIA.NS", "RECLTD.NS",
    "SAIL.NS", "SBICARD.NS", "SHREECEM.NS", "SIEMENS.NS",
    "SRF.NS", "SUNDARMFIN.NS", "SUPREMEIND.NS", "TATAPOWER.NS",
    "TORNTPHARM.NS", "TORNTPOWER.NS", "TRENT.NS", "TVSMOTOR.NS",
    "UBL.NS", "UNITDSPR.NS", "VBL.NS", "VEDL.NS",
    "VOLTAS.NS", "WHIRLPOOL.NS", "ZYDUSLIFE.NS", "INDIANB.NS",
    "PETRONET.NS", "GMRAIRPORT.NS", "BHARATFORG.NS", "BANKINDIA.NS",

    # Midcap names where momentum alpha lives (per BacktestIndia 19yr study)
    "ABCAPITAL.NS", "AUBANK.NS", "BANDHANBNK.NS",
    "BSOFT.NS", "CGCL.NS", "CHAMBLFERT.NS", "COFORGE.NS",
    "CROMPTON.NS", "CUMMINSIND.NS", "DALBHARAT.NS", "DEEPAKNTR.NS",
    "ESCORTS.NS", "EXIDEIND.NS", "FORTIS.NS", "GLAND.NS",
    "GUJGASLTD.NS", "HONAUT.NS", "IDEA.NS",
    "IGL.NS", "IRCON.NS", "JKCEMENT.NS", "JSWENERGY.NS",
    "KPRMILL.NS", "LTF.NS", "LICHSGFIN.NS", "LUPIN.NS",
    "M&MFIN.NS", "CIEINDIA.NS", "MANAPPURAM.NS", "MFSL.NS",
    "MOTILALOFS.NS", "MRPL.NS", "NAM-INDIA.NS",
    "NATIONALUM.NS", "NHPC.NS", "NLCINDIA.NS", "OIL.NS",
    "PAYTM.NS", "PGHH.NS", "POLICYBZR.NS",
    "RAMCOCEM.NS", "RBLBANK.NS", "RVNL.NS", "SCHAEFFLER.NS",
    "SOLARINDS.NS", "STAR.NS", "SUNTV.NS", "SYNGENE.NS",
    "TATACHEM.NS", "TATAELXSI.NS", "TATACOMM.NS", "TIINDIA.NS",
    "UCOBANK.NS", "UNIONBANK.NS", "UPL.NS", "VINATIORGA.NS",
    "WESTLIFE.NS", "ZEEL.NS", "ETERNAL.NS", "JIOFIN.NS",
]


# Default sectors for the expanded universe (Nifty 200 supplement).
# Best-effort mapping — missing entries default to "Unknown" sector.
NIFTY_200_SECTOR_MAP_EXTRAS = {
    "BAJAJ-AUTO.NS": "Auto", "EICHERMOT.NS": "Auto", "TMPV.NS": "Auto",
    "HEROMOTOCO.NS": "Auto", "TVSMOTOR.NS": "Auto", "ESCORTS.NS": "Auto",
    "BHARATFORG.NS": "Auto", "MOTHERSON.NS": "Auto", "CIEINDIA.NS": "Auto",
    "HINDUNILVR.NS": "FMCG", "BRITANNIA.NS": "FMCG", "TATACONSUM.NS": "FMCG",
    "DABUR.NS": "FMCG", "MARICO.NS": "FMCG", "COLPAL.NS": "FMCG",
    "GODREJCP.NS": "FMCG", "UBL.NS": "FMCG", "UNITDSPR.NS": "FMCG",
    "VBL.NS": "FMCG", "PGHH.NS": "FMCG", "JUBLFOOD.NS": "FMCG",
    "INDUSINDBK.NS": "Financial", "SHRIRAMFIN.NS": "Financial",
    "BAJAJFINSV.NS": "Financial", "HDFCLIFE.NS": "Financial",
    "SBILIFE.NS": "Financial", "ICICIPRULI.NS": "Financial",
    "ICICIGI.NS": "Financial", "BANKBARODA.NS": "Financial",
    "CANBK.NS": "Financial", "PNB.NS": "Financial", "FEDERALBNK.NS": "Financial",
    "IDFCFIRSTB.NS": "Financial", "RBLBANK.NS": "Financial",
    "AUBANK.NS": "Financial", "BANDHANBNK.NS": "Financial",
    "HDFCAMC.NS": "Financial", "CHOLAFIN.NS": "Financial",
    "MUTHOOTFIN.NS": "Financial", "MANAPPURAM.NS": "Financial",
    "M&MFIN.NS": "Financial", "LTF.NS": "Financial",
    "LICI.NS": "Financial", "LICHSGFIN.NS": "Financial",
    "SBICARD.NS": "Financial", "JIOFIN.NS": "Financial",
    "MFSL.NS": "Financial", "MOTILALOFS.NS": "Financial",
    "ABCAPITAL.NS": "Financial", "PFC.NS": "Financial", "RECLTD.NS": "Financial",
    "PAYTM.NS": "Financial", "POLICYBZR.NS": "Financial",
    "INDIANB.NS": "Financial", "BANKINDIA.NS": "Financial",
    "UCOBANK.NS": "Financial", "UNIONBANK.NS": "Financial",
    "DIVISLAB.NS": "Pharma", "DRREDDY.NS": "Pharma", "CIPLA.NS": "Pharma",
    "AUROPHARMA.NS": "Pharma", "BIOCON.NS": "Pharma", "ALKEM.NS": "Pharma",
    "TORNTPHARM.NS": "Pharma", "GLAND.NS": "Pharma", "ZYDUSLIFE.NS": "Pharma",
    "LUPIN.NS": "Pharma", "SYNGENE.NS": "Pharma", "ABBOTINDIA.NS": "Pharma",
    "FORTIS.NS": "Pharma", "APOLLOHOSP.NS": "Pharma",
    "MPHASIS.NS": "IT", "PERSISTENT.NS": "IT",
    "COFORGE.NS": "IT", "OFSS.NS": "IT",
    "BSOFT.NS": "IT", "TATAELXSI.NS": "IT", "TATACOMM.NS": "IT",
    "ASIANPAINT.NS": "Consumer", "TITAN.NS": "Consumer",
    "BERGEPAINT.NS": "Consumer", "PIDILITIND.NS": "Consumer",
    "HAVELLS.NS": "Consumer", "CROMPTON.NS": "Consumer",
    "VOLTAS.NS": "Consumer", "WHIRLPOOL.NS": "Consumer", "DIXON.NS": "Consumer",
    "TRENT.NS": "Consumer", "DMART.NS": "Consumer", "PAGEIND.NS": "Consumer",
    "WESTLIFE.NS": "Consumer", "HONAUT.NS": "Consumer",
    "EXIDEIND.NS": "Consumer", "SUNTV.NS": "Media", "ZEEL.NS": "Media",
    "NAUKRI.NS": "IT", "ETERNAL.NS": "Consumer",
    "ULTRACEMCO.NS": "Construction", "GRASIM.NS": "Construction",
    "AMBUJACEM.NS": "Construction", "ACC.NS": "Construction",
    "SHREECEM.NS": "Construction", "DALBHARAT.NS": "Construction",
    "RAMCOCEM.NS": "Construction", "JKCEMENT.NS": "Construction",
    "DLF.NS": "Construction", "GODREJPROP.NS": "Construction",
    "OBEROIRLTY.NS": "Construction", "LT.NS": "Construction",
    "GMRAIRPORT.NS": "Construction", "IRCON.NS": "Construction",
    "RVNL.NS": "Construction", "CONCOR.NS": "Construction",
    "HAL.NS": "Defence", "BEL.NS": "Defence",
    "BHEL.NS": "Capital Goods", "SIEMENS.NS": "Capital Goods",
    "ABB.NS": "Capital Goods", "BOSCHLTD.NS": "Capital Goods",
    "CUMMINSIND.NS": "Capital Goods", "POLYCAB.NS": "Capital Goods",
    "POWERINDIA.NS": "Capital Goods", "SCHAEFFLER.NS": "Capital Goods",
    "TIINDIA.NS": "Capital Goods", "STAR.NS": "Capital Goods",
    "SOLARINDS.NS": "Capital Goods", "SUPREMEIND.NS": "Capital Goods",
    "NTPC.NS": "Utilities", "POWERGRID.NS": "Utilities",
    "TATAPOWER.NS": "Utilities", "TORNTPOWER.NS": "Utilities",
    "ADANIGREEN.NS": "Utilities", "ADANIPOWER.NS": "Utilities",
    "ADANIENSOL.NS": "Utilities", "NHPC.NS": "Utilities",
    "NLCINDIA.NS": "Utilities", "JSWENERGY.NS": "Utilities",
    "RELIANCE.NS": "Energy", "ONGC.NS": "Energy", "BPCL.NS": "Energy",
    "IOC.NS": "Energy", "GAIL.NS": "Energy", "PETRONET.NS": "Energy",
    "OIL.NS": "Energy", "MRPL.NS": "Energy", "IGL.NS": "Energy",
    "GUJGASLTD.NS": "Energy",
    "TATASTEEL.NS": "Metal", "JSWSTEEL.NS": "Metal", "COALINDIA.NS": "Metal",
    "HINDALCO.NS": "Metal", "NMDC.NS": "Metal", "SAIL.NS": "Metal",
    "VEDL.NS": "Metal", "NATIONALUM.NS": "Metal", "JINDALSTEL.NS": "Metal",
    "INDIGO.NS": "Aviation",
    "BHARTIARTL.NS": "Telecom", "IDEA.NS": "Telecom",
    "INDUSTOWER.NS": "Telecom",
    "ADANIPORTS.NS": "Logistics", "IRCTC.NS": "Logistics",
    "DEEPAKNTR.NS": "Chemicals", "SRF.NS": "Chemicals",
    "VINATIORGA.NS": "Chemicals", "TATACHEM.NS": "Chemicals",
    "PIIND.NS": "Chemicals", "UPL.NS": "Chemicals",
    "CHAMBLFERT.NS": "Chemicals",
    "MRF.NS": "Auto", "ADANIENT.NS": "Diversified",
    "BAJAJHLDNG.NS": "Diversified", "CGCL.NS": "Diversified",
    "KPRMILL.NS": "Textiles", "NAM-INDIA.NS": "Financial",
    "MARUTI.NS": "Auto", "M&M.NS": "Auto", "HCLTECH.NS": "IT",
    "TCS.NS": "IT", "INFY.NS": "IT", "WIPRO.NS": "IT", "TECHM.NS": "IT",
    "HDFCBANK.NS": "Financial", "ICICIBANK.NS": "Financial",
    "SBIN.NS": "Financial", "KOTAKBANK.NS": "Financial",
    "AXISBANK.NS": "Financial", "BAJFINANCE.NS": "Financial",
    "SUNPHARMA.NS": "Pharma", "NESTLEIND.NS": "FMCG", "ITC.NS": "FMCG",
}


def load_universe_from_csv(path: str, ticker_col: str = "ticker",
                           suffix: str = ".NS", limit: int | None = 200) -> list[str]:
    """C4: refresh the tradable universe from a maintained CSV (e.g. a slice of
    the 30k master). The SCANNED list stays small (default cap 200) so Render's
    512 MB budget holds — the 30k master is a SOURCE, never the scan universe.
    Returns cleaned, de-duplicated NSE tickers."""
    import csv
    out: list[str] = []
    seen: set[str] = set()
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            t = (row.get(ticker_col) or row.get("symbol") or row.get("Symbol") or "").strip().upper()
            if not t:
                continue
            if not (t.endswith(".NS") or t.endswith(".BO")):
                t += suffix
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
            if limit and len(out) >= limit:
                break
    return out


def load_full_universe(path: str = None) -> list[str]:
    """F8: the FULL committed NSE cash-equity universe (2,376) as a source
    list — never scanned whole (512 MB). Feed into rotating_bucket()."""
    import os
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "..", "data",
                            "upstox_nse_equities.csv")
    try:
        return load_universe_from_csv(path, limit=None)
    except OSError:
        return list(NIFTY_200_TICKERS)


def rotating_bucket(tickers: list[str], size: int, day_index: int) -> list[str]:
    """F8: deterministic ~`size`/day rotating slice so a big universe is fully
    covered over ceil(len/size) days, repeating on a fixed cycle. day_index is
    a caller-supplied integer (e.g. date.toordinal()) so it's reproducible and
    backtestable — no Math.random / Date.now in the engine.

    FIXED 2026-08-04 (owner: "ur new universe is alphbet wise"). This used to
    take a CONTIGUOUS slice `tickers[start:start+size]` of a list that
    universe_refresh sorts ALPHABETICALLY. So every single day's scan was one
    alphabet block — day 1 was 20MICRONS..ASHOKLEY, and the Z names waited
    ~48 days. Worse, alphabet blocks cluster by group name (all the ADANI /
    BAJAJ / TATA names land together), so a day's candidates were accidentally
    concentrated instead of spread across the market.

    A STRIDE fixes it with no loss of the properties that matter: `tickers[b::k]`
    is still deterministic, still reproducible from day_index alone, and still
    covers the whole universe over the same cycle — but each day's sample is
    spread across the entire alphabet, so every scan sees the real market."""
    n = len(tickers)
    if n == 0 or size <= 0:
        return []
    if size >= n:
        return list(tickers)
    n_buckets = (n + size - 1) // size
    b = day_index % n_buckets
    return tickers[b::n_buckets]


def get_nifty_200_universe():
    """Returns (tickers_list, sector_map) for Nifty 200 preset."""
    sector_map = dict(NIFTY_200_SECTOR_MAP_EXTRAS)
    # Custom-universe names get real sectors too (Codex review fix, PR #68)
    # — defined next to OWNER_UNIVERSE_2026_08_20 below.
    try:
        sector_map.update(CUSTOM_UNIVERSE_SECTOR_MAP)
    except NameError:                                   # pragma: no cover
        pass
    # Anything not in the map -> Unknown
    for t in NIFTY_200_TICKERS:
        sector_map.setdefault(t, "Unknown")
    return NIFTY_200_TICKERS, sector_map


def apply_blocklist(universe: list[str], held, blocklist) -> list[str]:
    """Owner's never-trade list: drop blocked names from a scan universe.
    Held tickers are exempt so refresh/exits keep their history."""
    blocked = set(blocklist or ())
    if not blocked:
        return list(universe)
    held = set(held or ())
    return [t for t in universe if t in held or t not in blocked]


# ---------------------------------------------------------------------------
# OWNER'S CUSTOM 50 (order 2026-08-20: "reduce the universe to 50 share only
# but all fresh ... do not change codes and just work on 50 count, rest
# remains same ... ARE SOME FOR THIS WEEK, ADD MORE AND MAKE IT 50").
#
# 33 are the owner's own picks, resolved against the live universe file —
# the exact mapping from their message (ambiguous ones they resolved
# themselves on 2026-08-20): IOCP→IOLCP · ELGIE→ELGIEQUIP · USHAMRT→USHAMART
# · GVTD→GVT&D · KIRPLU→KIRLPNU · AGL→AGL (Allcargo Global, exact symbol) ·
# HDGC→HDFCBANK · ICICI→ICICIBANK · BHARTIAIRTEL→BHARTIARTL · MASTK→MASTEK ·
# MAXHEALTHCARE→MAXHEALTH · ADITYABIRLA→ABCAPITAL · ITCHOTEL→ITCHOTELS.
# MTARTECH is EXCLUDED: NSE lists it in the BE (trade-to-trade) series,
# which the tradeable universe deliberately filters out — it stays on the
# series-watch card; the owner can buy it manually via the ticket.
#
# 17 fill the count to 50 per their "Automobile & Metal is growing add
# accordingly": 9 automobile/ancillary + 8 metal, all liquid, all present
# in the committed universe file (verified 2026-08-20).
#
# The list is EDITABLE in Settings (Custom Universe) — the owner refreshes
# it weekly ("ARE SOME FOR THIS WEEK"); blank restores the full-NSE
# rotating bucket. The blocklist still applies on top, and every other
# gate/score/exit is untouched — their words: "rest remains same".
# ---------------------------------------------------------------------------

# Sector map for the custom-universe names the Nifty-200 map doesn't cover
# (CODEX REVIEW FIX, PR #68): unmapped names all read "Unknown", and
# risk.can_open counts every Unknown position as ONE sector for
# max_sector_positions — so a book of unmapped small-caps would have hit
# sector_cap_reached on completely unrelated companies. Same 19-sector
# vocabulary as NIFTY_200_SECTOR_MAP_EXTRAS (Pharma = the healthcare
# bucket, per APOLLOHOSP/FORTIS precedent). Merged into the default
# cfg.sector_map below.
CUSTOM_UNIVERSE_SECTOR_MAP: dict = {
    "HFCL.NS": "Telecom",          # telecom gear + optical fibre
    "IOLCP.NS": "Pharma",          # IOL Chemicals & Pharma
    "SKIPPER.NS": "Capital Goods",  # T&D towers
    "ELGIEQUIP.NS": "Capital Goods",
    "INDGN.NS": "Pharma",          # healthcare-tech services
    "ACUTAAS.NS": "Chemicals",
    "USHAMART.NS": "Metal",        # steel wire ropes
    "PGEL.NS": "Consumer",         # electronics manufacturing (durables)
    "THERMAX.NS": "Capital Goods",
    "GVT&D.NS": "Capital Goods",   # power T&D equipment
    "KIRLPNU.NS": "Capital Goods",
    "SYRMA.NS": "Capital Goods",   # electronics manufacturing services
    "RAIN.NS": "Chemicals",        # carbon products / chemicals
    "AGL.NS": "Logistics",         # Allcargo Global
    "MASTEK.NS": "IT",
    "ANGELONE.NS": "Financial",
    "VTL.NS": "Textiles",
    "LODHA.NS": "Construction",
    "MAXHEALTH.NS": "Pharma",      # hospitals (healthcare bucket)
    "IMFA.NS": "Metal",            # ferro alloys
    "POCL.NS": "Metal",            # lead/zinc oxides
    "ITCHOTELS.NS": "Consumer",    # hotels
    "SCI.NS": "Logistics",         # shipping
    "VPRPL.NS": "Construction",
    "IGIL.NS": "Consumer",         # gem certification services
    "ONEPOINT.NS": "IT",           # BPO/tech services
    "ASHOKLEY.NS": "Auto",
    "SONACOMS.NS": "Auto",
    "UNOMINDA.NS": "Auto",
    "APLAPOLLO.NS": "Metal",       # steel tubes
    "JSL.NS": "Metal",             # stainless steel
}

OWNER_UNIVERSE_2026_08_20: tuple = (
    # --- the owner's 33 ---
    "HFCL.NS", "IOLCP.NS", "SKIPPER.NS", "ELGIEQUIP.NS", "INDGN.NS",
    "ACUTAAS.NS", "USHAMART.NS", "PGEL.NS", "THERMAX.NS", "GVT&D.NS",
    "KIRLPNU.NS", "SYRMA.NS", "RAIN.NS", "TCS.NS", "AGL.NS",
    "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS", "MASTEK.NS",
    "ANGELONE.NS", "VTL.NS", "LODHA.NS", "MAXHEALTH.NS", "NMDC.NS",
    "IMFA.NS", "ABCAPITAL.NS", "RELIANCE.NS", "POCL.NS", "ITCHOTELS.NS",
    "SCI.NS", "VPRPL.NS", "IGIL.NS", "ONEPOINT.NS",
    # --- automobile (9) — "Automobile ... is growing" ---
    "M&M.NS", "MARUTI.NS", "EICHERMOT.NS", "TVSMOTOR.NS", "ASHOKLEY.NS",
    "BHARATFORG.NS", "MOTHERSON.NS", "SONACOMS.NS", "UNOMINDA.NS",
    # --- metal (8) — "... & Metal is growing" ---
    "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "JINDALSTEL.NS",
    "VEDL.NS", "NATIONALUM.NS", "APLAPOLLO.NS", "JSL.NS",
)
