"""Universe presets — hardcoded ticker lists for repeatability.

NIFTY_200 contains the top 200 stocks by market cap as of May 2026.
Refresh annually when NSE rebalances the Nifty 200 composition.

We keep the list hardcoded (not fetched live) so backtests are
deterministic and reproducible.
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
    "TATAMOTORS.NS", "TATACONSUM.NS", "ADANIPORTS.NS", "HDFCLIFE.NS",
    "BPCL.NS", "SBILIFE.NS", "APOLLOHOSP.NS", "NTPC.NS",
    "LTIM.NS", "HINDALCO.NS",

    # Mid + Large caps (Nifty 100/200 — illiquid alpha zone per academic studies)
    "ADANIGREEN.NS", "ADANIPOWER.NS", "ADANITRANS.NS",
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
    "OBEROIRLTY.NS", "OFSS.NS", "PAGEIND.NS", "PEL.NS",
    "PERSISTENT.NS", "PFC.NS", "PIDILITIND.NS", "PIIND.NS",
    "PNB.NS", "POLYCAB.NS", "POWERINDIA.NS", "RECLTD.NS",
    "SAIL.NS", "SBICARD.NS", "SHREECEM.NS", "SIEMENS.NS",
    "SRF.NS", "SUNDARMFIN.NS", "SUPREMEIND.NS", "TATAPOWER.NS",
    "TORNTPHARM.NS", "TORNTPOWER.NS", "TRENT.NS", "TVSMOTOR.NS",
    "UBL.NS", "UNITDSPR.NS", "VBL.NS", "VEDL.NS",
    "VOLTAS.NS", "WHIRLPOOL.NS", "ZYDUSLIFE.NS", "INDIANB.NS",
    "PETRONET.NS", "GMRINFRA.NS", "BHARATFORG.NS", "BANKINDIA.NS",

    # Midcap names where momentum alpha lives (per BacktestIndia 19yr study)
    "ABCAPITAL.NS", "AUBANK.NS", "BANDHANBNK.NS", "BHARATELE.NS",
    "BSOFT.NS", "CGCL.NS", "CHAMBLFERT.NS", "COFORGE.NS",
    "CROMPTON.NS", "CUMMINSIND.NS", "DALBHARAT.NS", "DEEPAKNTR.NS",
    "ESCORTS.NS", "EXIDEIND.NS", "FORTIS.NS", "GLAND.NS",
    "GUJGASLTD.NS", "HONAUT.NS", "IDEA.NS", "IDFC.NS",
    "IGL.NS", "IRCON.NS", "JKCEMENT.NS", "JSWENERGY.NS",
    "KPRMILL.NS", "L&TFH.NS", "LICHSGFIN.NS", "LUPIN.NS",
    "M&MFIN.NS", "MAHINDCIE.NS", "MANAPPURAM.NS", "MFSL.NS",
    "MINDTREE.NS", "MOTILALOFS.NS", "MRPL.NS", "NAM-INDIA.NS",
    "NATIONALUM.NS", "NHPC.NS", "NLCINDIA.NS", "OIL.NS",
    "PAYTM.NS", "PGHH.NS", "PIRAMALENT.NS", "POLICYBZR.NS",
    "RAMCOCEM.NS", "RBLBANK.NS", "RVNL.NS", "SCHAEFFLER.NS",
    "SOLARINDS.NS", "STAR.NS", "SUNTV.NS", "SYNGENE.NS",
    "TATACHEM.NS", "TATAELXSI.NS", "TATACOMM.NS", "TIINDIA.NS",
    "UCOBANK.NS", "UNIONBANK.NS", "UPL.NS", "VINATIORGA.NS",
    "WESTLIFE.NS", "ZEEL.NS", "ZOMATO.NS", "JIOFIN.NS",
]


# Default sectors for the expanded universe (Nifty 200 supplement).
# Best-effort mapping — missing entries default to "Unknown" sector.
NIFTY_200_SECTOR_MAP_EXTRAS = {
    "BAJAJ-AUTO.NS": "Auto", "EICHERMOT.NS": "Auto", "TATAMOTORS.NS": "Auto",
    "HEROMOTOCO.NS": "Auto", "TVSMOTOR.NS": "Auto", "ESCORTS.NS": "Auto",
    "BHARATFORG.NS": "Auto", "MOTHERSON.NS": "Auto", "MAHINDCIE.NS": "Auto",
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
    "M&MFIN.NS": "Financial", "L&TFH.NS": "Financial", "PEL.NS": "Financial",
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
    "PIRAMALENT.NS": "Pharma", "FORTIS.NS": "Pharma", "APOLLOHOSP.NS": "Pharma",
    "LTIM.NS": "IT", "MPHASIS.NS": "IT", "PERSISTENT.NS": "IT",
    "COFORGE.NS": "IT", "OFSS.NS": "IT", "MINDTREE.NS": "IT",
    "BSOFT.NS": "IT", "TATAELXSI.NS": "IT", "TATACOMM.NS": "IT",
    "ASIANPAINT.NS": "Consumer", "TITAN.NS": "Consumer",
    "BERGEPAINT.NS": "Consumer", "PIDILITIND.NS": "Consumer",
    "HAVELLS.NS": "Consumer", "CROMPTON.NS": "Consumer",
    "VOLTAS.NS": "Consumer", "WHIRLPOOL.NS": "Consumer", "DIXON.NS": "Consumer",
    "TRENT.NS": "Consumer", "DMART.NS": "Consumer", "PAGEIND.NS": "Consumer",
    "WESTLIFE.NS": "Consumer", "HONAUT.NS": "Consumer",
    "EXIDEIND.NS": "Consumer", "SUNTV.NS": "Media", "ZEEL.NS": "Media",
    "NAUKRI.NS": "IT", "ZOMATO.NS": "Consumer",
    "ULTRACEMCO.NS": "Construction", "GRASIM.NS": "Construction",
    "AMBUJACEM.NS": "Construction", "ACC.NS": "Construction",
    "SHREECEM.NS": "Construction", "DALBHARAT.NS": "Construction",
    "RAMCOCEM.NS": "Construction", "JKCEMENT.NS": "Construction",
    "DLF.NS": "Construction", "GODREJPROP.NS": "Construction",
    "OBEROIRLTY.NS": "Construction", "LT.NS": "Construction",
    "GMRINFRA.NS": "Construction", "IRCON.NS": "Construction",
    "RVNL.NS": "Construction", "CONCOR.NS": "Construction",
    "HAL.NS": "Defence", "BEL.NS": "Defence", "BHARATELE.NS": "Defence",
    "BHEL.NS": "Capital Goods", "SIEMENS.NS": "Capital Goods",
    "ABB.NS": "Capital Goods", "BOSCHLTD.NS": "Capital Goods",
    "CUMMINSIND.NS": "Capital Goods", "POLYCAB.NS": "Capital Goods",
    "POWERINDIA.NS": "Capital Goods", "SCHAEFFLER.NS": "Capital Goods",
    "TIINDIA.NS": "Capital Goods", "STAR.NS": "Capital Goods",
    "SOLARINDS.NS": "Capital Goods", "SUPREMEIND.NS": "Capital Goods",
    "NTPC.NS": "Utilities", "POWERGRID.NS": "Utilities",
    "TATAPOWER.NS": "Utilities", "TORNTPOWER.NS": "Utilities",
    "ADANIGREEN.NS": "Utilities", "ADANIPOWER.NS": "Utilities",
    "ADANITRANS.NS": "Utilities", "NHPC.NS": "Utilities",
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


def get_nifty_200_universe():
    """Returns (tickers_list, sector_map) for Nifty 200 preset."""
    sector_map = dict(NIFTY_200_SECTOR_MAP_EXTRAS)
    # Anything not in the map -> Unknown
    for t in NIFTY_200_TICKERS:
        sector_map.setdefault(t, "Unknown")
    return NIFTY_200_TICKERS, sector_map
