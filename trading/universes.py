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
    # Custom-universe names get real sectors too (external review fix, PR #68)
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
# FUND UNITS ARE NOT STOCKS (owner 2026-10-16: "new position are not added,
# its not using the all 5 trade segements" -> "relook n fix").
#
# Upstox lists ETFs and liquid funds as NSE_EQ with instrument_type EQUITY,
# so the universe file carries them beside company shares (351 of 2,678 on
# 2026-10-15) and the 09-30 turnover rebuild put 56 into the scan list. The
# ISIN says which is which: INE = a company's share, INF = a fund unit.
#
# Measured on the 10-15 close with the real engine: a liquid fund moves
# ~0.2% a year with almost no volatility, so the vol-adjusted momentum score
# saturates (LIQUIDCASE 98.25, LIQUIDBETF 94.75 ...) and slab sizing hits the
# 5% cap on each one. Six liquid funds took Rs 1.5 Cr of momentum's Rs 2 Cr
# share before a single company was bought — and with a 10% target, a -5%
# stop and a score that never decays, neither exit can ever fire, so that
# cash sits idle for 200 days. They also always sit above their own 200-DMA,
# which lifts every breadth reading the entry gate reads.
#
# So fund units leave the SCAN (all five categories), the weekly turnover
# rebuild and both breadth readings. A fund unit already HELD stays in its
# own scan so its exits keep working; the owner's BUY ticket can still buy
# one by hand.
# ---------------------------------------------------------------------------
_FUND_CACHE: dict = {}


def fund_unit_tickers(path: str = None) -> frozenset:
    """Tickers whose ISIN marks a fund unit (INF...), read from the universe
    file (refreshed daily at 08:50). Cached per file version; an unreadable
    file returns an empty set — no name is dropped on a guess."""
    import csv
    import os
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "..", "data",
                            "upstox_nse_equities.csv")
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return frozenset()
    hit = _FUND_CACHE.get("key")
    if hit == key:
        return _FUND_CACHE["funds"]
    funds = set()
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                isin = (row.get("isin") or "").strip().upper()
                if not isin:
                    isin = str(row.get("instrument_key") or "").split("|")[-1]
                    isin = isin.strip().upper()
                t = (row.get("ticker") or "").strip()
                if t and isin.startswith("INF"):
                    funds.add(t if t.endswith(".NS") else t + ".NS")
    except OSError:
        return frozenset()
    out = frozenset(funds)
    _FUND_CACHE.update(key=key, funds=out)
    return out


def is_fund_unit(ticker: str, path: str = None) -> bool:
    t = str(ticker or "")
    return (t if t.endswith(".NS") else t + ".NS") in fund_unit_tickers(path)


def drop_fund_units(universe, held=(), path: str = None) -> list[str]:
    """`universe` without fund units; held names always stay."""
    funds = fund_unit_tickers(path)
    if not funds:
        return list(universe)
    held = set(held or ())
    return [t for t in universe if t in held or t not in funds]


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
# (REVIEW FIX, PR #68): unmapped names all read "Unknown", and
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
    # --- OWNER ORDER 2026-09-16 ("add defence, shipbuilding, pharma names
    # to the scan list"), after the BRICS-summit review showed the scanned
    # list had ZERO names in those three themes. Every one of these cleared
    # trading.risk_v2.passes_liquidity on LIVE Upstox candles before being
    # added — a name the entry gate can never pass is decoration on the
    # screen and nothing else (the THERMAX lesson, 2026-09-08).
    # "Defence" is a NEW sector label here; every other name keeps the
    # existing 19-sector vocabulary (Pharma = the healthcare bucket).
    "HAL.NS": "Defence",           # Hindustan Aeronautics
    "BEL.NS": "Defence",           # Bharat Electronics
    "BDL.NS": "Defence",           # Bharat Dynamics
    "SOLARINDS.NS": "Defence",     # Solar Industries — explosives/munitions
    "PARAS.NS": "Defence",         # Paras Defence and Space Technologies
    "DATAPATTNS.NS": "Defence",    # Data Patterns India
    "ZENTEC.NS": "Defence",        # Zen Technologies — simulators
    "BEML.NS": "Defence",
    "ASTRAMICRO.NS": "Defence",    # Astra Microwave
    "APOLLO.NS": "Defence",        # Apollo MICRO SYSTEMS (not Tyres/Hospitals)
    "CYIENTDLM.NS": "Defence",     # Cyient DLM — defence electronics mfg
    "UNIMECH.NS": "Defence",       # Unimech Aerospace
    "DYNAMATECH.NS": "Defence",    # Dynamatic Technologies — aero components
    "IDEAFORGE.NS": "Defence",     # ideaForge — drones
    # Shipbuilding sits in Capital Goods: the yards are heavy engineering and
    # the 19-sector vocabulary has no yard bucket. SCI (Shipping Corp) is
    # already in the list as Logistics and is NOT moved — it is a shipping
    # line, not a builder.
    "COCHINSHIP.NS": "Capital Goods",
    "GRSE.NS": "Capital Goods",    # Garden Reach Shipbuilders
    "MAZDOCK.NS": "Capital Goods",  # Mazagon Dock
    # Pharma (healthcare bucket, APOLLOHOSP/FORTIS precedent)
    "SUNPHARMA.NS": "Pharma", "DRREDDY.NS": "Pharma", "CIPLA.NS": "Pharma",
    "TORNTPHARM.NS": "Pharma", "LUPIN.NS": "Pharma", "AUROPHARMA.NS": "Pharma",
    "DIVISLAB.NS": "Pharma", "ZYDUSLIFE.NS": "Pharma", "ALKEM.NS": "Pharma",
    "MANKIND.NS": "Pharma", "GLENMARK.NS": "Pharma", "LAURUSLABS.NS": "Pharma",
    "BIOCON.NS": "Pharma", "GRANULES.NS": "Pharma", "PPLPHARMA.NS": "Pharma",
    "IPCALAB.NS": "Pharma", "NATCOPHARM.NS": "Pharma",
    # 2026-09-30 market-impact additions not covered by the Nifty-200 map
    "PRESTIGE.NS": "Construction", "PHOENIXLTD.NS": "Construction",
    "BRIGADE.NS": "Construction",
    "APOLLOTYRE.NS": "Auto", "BALKRISIND.NS": "Auto",
    "MGL.NS": "Energy",
    "LTTS.NS": "IT", "KPITTECH.NS": "IT",
    "KALYANKJIL.NS": "Consumer",
}

OWNER_UNIVERSE_2026_08_20: tuple = (
    # --- the owner's 33 (HFCL now 32 — see the T2T note below) ---
    # HFCL.NS REMOVED 2026-09-09, not by choice: the fresh Upstox master
    # lists it in the **BE (trade-to-trade) series**. Exactly the MTARTECH
    # case the owner already accepted on 2026-08-20 — the tradeable universe
    # filters T2T by design, so a BE name cannot be auto-scanned; it moves
    # to the series-watch card and stays buyable by hand through the ticket.
    # Its sector entry is KEPT below so a held or hand-bought position still
    # maps to Telecom. The list is 49 until the owner names a replacement.
    "IOLCP.NS", "SKIPPER.NS", "ELGIEQUIP.NS", "INDGN.NS",
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
    # --- defence (14) — OWNER ORDER 2026-09-16 ---
    # The BRICS-summit review (2026-09-14) found the scanned list held ZERO
    # defence, shipbuilding or pharma names while all three sat in the
    # universe file. Owner: "add defence, shipbuilding, pharma names to the
    # scan list". Each name below was screened on LIVE Upstox candles through
    # trading.risk_v2.passes_liquidity — the engine's own entry gate — and
    # every one cleared the Rs 5 Cr 5-day rupee-turnover bar. Screening
    # output: research/universe_add_2026-09-16.csv.
    "HAL.NS", "BEL.NS", "BDL.NS", "SOLARINDS.NS", "PARAS.NS",
    "DATAPATTNS.NS", "ZENTEC.NS", "BEML.NS", "ASTRAMICRO.NS", "APOLLO.NS",
    "CYIENTDLM.NS", "UNIMECH.NS", "DYNAMATECH.NS", "IDEAFORGE.NS",
    # --- shipbuilding (3) — the yards. SCI is already above as Logistics
    # (a shipping line, not a builder) and is deliberately not duplicated.
    "COCHINSHIP.NS", "GRSE.NS", "MAZDOCK.NS",
    # --- pharma (17) ---
    "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "TORNTPHARM.NS", "LUPIN.NS",
    "AUROPHARMA.NS", "DIVISLAB.NS", "ZYDUSLIFE.NS", "ALKEM.NS",
    "MANKIND.NS", "GLENMARK.NS", "LAURUSLABS.NS", "BIOCON.NS",
    "GRANULES.NS", "PPLPHARMA.NS", "IPCALAB.NS", "NATCOPHARM.NS",
    # --- market-impact names (90) — OWNER ORDER 2026-09-30 ---
    # "refresh the universe, add stock getting impacted with this market
    # RBI and other impacts in market". The September 2026 slide was driven
    # by an expected RBI hike, Brent above $100, a rupee past 96 and FII
    # selling, so the impacted sectors are: rate-sensitives (banks, NBFCs,
    # insurers, realty, cement, two-wheelers), crude producers and
    # consumers, rupee-sensitive IT exporters, defensives, gold/jewellery
    # and rate-sensitive power/capex PSUs. Every name screened on LIVE
    # Upstox candles through trading.risk_v2.passes_liquidity (the entry
    # gate) on 2026-09-30 — all cleared; the four candidates absent from
    # the Upstox master (TATAMOTORS, TATAMTRDVR, HPCL, ZOMATO) were NOT
    # added. Screening output: research/universe_add_2026-09-30.csv.
    # rate-sensitive: banks (11)
    "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS", "INDUSINDBK.NS",
    "BANKBARODA.NS", "PNB.NS", "CANBK.NS", "FEDERALBNK.NS", "IDFCFIRSTB.NS",
    "AUBANK.NS", "UNIONBANK.NS",
    # rate-sensitive: NBFC / insurance (15)
    "BAJFINANCE.NS", "BAJAJFINSV.NS", "SHRIRAMFIN.NS", "CHOLAFIN.NS",
    "LICHSGFIN.NS", "PFC.NS", "RECLTD.NS", "MUTHOOTFIN.NS", "MANAPPURAM.NS",
    "M&MFIN.NS", "SBILIFE.NS", "HDFCLIFE.NS", "ICICIGI.NS", "LICI.NS",
    "JIOFIN.NS",
    # rate-sensitive: realty / cement (11)
    "DLF.NS", "GODREJPROP.NS", "OBEROIRLTY.NS", "PRESTIGE.NS",
    "PHOENIXLTD.NS", "BRIGADE.NS", "ULTRACEMCO.NS", "SHREECEM.NS",
    "AMBUJACEM.NS", "ACC.NS", "DALBHARAT.NS",
    # rate-sensitive: two-wheelers (2)
    "BAJAJ-AUTO.NS", "HEROMOTOCO.NS",
    # crude: producers (3) and consumers (16)
    "ONGC.NS", "OIL.NS", "GAIL.NS",
    "IOC.NS", "BPCL.NS", "INDIGO.NS", "ASIANPAINT.NS", "BERGEPAINT.NS",
    "MRF.NS", "APOLLOTYRE.NS", "BALKRISIND.NS", "PIDILITIND.NS",
    "PETRONET.NS", "IGL.NS", "MGL.NS", "SRF.NS", "DEEPAKNTR.NS", "UPL.NS",
    # rupee: IT exporters (10)
    "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS", "PERSISTENT.NS",
    "COFORGE.NS", "MPHASIS.NS", "LTTS.NS", "KPITTECH.NS", "TATAELXSI.NS",
    # defensives (10)
    "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS",
    "MARICO.NS", "GODREJCP.NS", "COLPAL.NS", "TATACONSUM.NS", "VBL.NS",
    # gold / jewellery (2)
    "TITAN.NS", "KALYANKJIL.NS",
    # rate-sensitive power / capex PSUs (10)
    "NTPC.NS", "POWERGRID.NS", "TATAPOWER.NS", "COALINDIA.NS", "BHEL.NS",
    "LT.NS", "SIEMENS.NS", "ABB.NS", "ADANIPORTS.NS", "ADANIENT.NS",
)
