"""Stock universe definitions for Indian markets."""

NIFTY50 = [
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "INFY",
    "ICICIBANK",
    "HINDUNILVR",
    "SBIN",
    "BHARTIARTL",
    "ITC",
    "KOTAKBANK",
    "LT",
    "HCLTECH",
    "AXISBANK",
    "ASIANPAINT",
    "MARUTI",
    "SUNPHARMA",
    "TITAN",
    "BAJFINANCE",
    "DMART",
    "NTPC",
    "TATAMOTORS",
    "ULTRACEMCO",
    "ONGC",
    "WIPRO",
    "JSWSTEEL",
    "POWERGRID",
    "M&M",
    "ADANIENT",
    "TATASTEEL",
    "NESTLEIND",
    "TECHM",
    "HDFCLIFE",
    "BAJAJFINSV",
    "INDUSINDBK",
    "GRASIM",
    "CIPLA",
    "APOLLOHOSP",
    "DRREDDY",
    "COALINDIA",
    "BPCL",
    "BRITANNIA",
    "SBILIFE",
    "EICHERMOT",
    "DIVISLAB",
    "TATACONSUM",
    "HEROMOTOCO",
    "BAJAJ-AUTO",
    "HINDALCO",
    "LTIM",
    "ADANIPORTS",
]

# ~40 liquid NSE midcaps (NIFTY Midcap 100 members) — a LESS-efficient universe
# than NIFTY-50, where mean-reversion / cross-sectional edge is more likely to survive.
MIDCAP = [
    "AUBANK",
    "ASHOKLEY",
    "AUROPHARMA",
    "BALKRISIND",
    "BHARATFORG",
    "BIOCON",
    "CANBK",
    "COFORGE",
    "CONCOR",
    "CUMMINSIND",
    "DIXON",
    "ESCORTS",
    "FEDERALBNK",
    "GODREJPROP",
    "HINDPETRO",
    "IDFCFIRSTB",
    "INDHOTEL",
    "JUBLFOOD",
    "LICHSGFIN",
    "LUPIN",
    "MFSL",
    "MPHASIS",
    "MRF",
    "NMDC",
    "OBEROIRLTY",
    "PAGEIND",
    "PERSISTENT",
    "PETRONET",
    "PIIND",
    "POLYCAB",
    "SAIL",
    "SRF",
    "TATACOMM",
    "TORNTPHARM",
    "TRENT",
    "TVSMOTOR",
    "UBL",
    "VOLTAS",
    "ABCAPITAL",
    "BANKINDIA",
]

BANKNIFTY = [
    "HDFCBANK",
    "ICICIBANK",
    "KOTAKBANK",
    "AXISBANK",
    "SBIN",
    "INDUSINDBK",
    "BANDHANBNK",
    "FEDERALBNK",
    "IDFCFIRSTB",
    "PNB",
    "AUBANK",
    "BANKBARODA",
]

# Sector mappings for risk management (sector exposure limits)
SECTORS = {
    "IT": ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM", "LTIM"],
    "BANKING": [
        "HDFCBANK",
        "ICICIBANK",
        "KOTAKBANK",
        "AXISBANK",
        "SBIN",
        "INDUSINDBK",
        "BANDHANBNK",
        "FEDERALBNK",
        "PNB",
        "AUBANK",
        "BANKBARODA",
        "IDFCFIRSTB",
    ],
    "FINANCE": ["BAJFINANCE", "BAJAJFINSV", "HDFCLIFE", "SBILIFE"],
    "ENERGY": ["RELIANCE", "ONGC", "NTPC", "POWERGRID", "BPCL", "COALINDIA"],
    "AUTO": ["TATAMOTORS", "MARUTI", "M&M", "EICHERMOT", "HEROMOTOCO", "BAJAJ-AUTO"],
    "PHARMA": ["SUNPHARMA", "CIPLA", "DRREDDY", "APOLLOHOSP", "DIVISLAB"],
    "METALS": ["TATASTEEL", "JSWSTEEL", "HINDALCO"],
    "FMCG": ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "TATACONSUM", "DMART"],
    "INFRA": ["LT", "ULTRACEMCO", "GRASIM", "ADANIENT", "ADANIPORTS"],
    "TELECOM": ["BHARTIARTL"],
    "CONSUMER": ["TITAN", "ASIANPAINT"],
}

UNIVERSES = {
    "NIFTY50": NIFTY50,
    "BANKNIFTY": BANKNIFTY,
    "MIDCAP": MIDCAP,
}


def get_universe(name: str) -> list[str]:
    return UNIVERSES.get(name.upper(), [])


def get_sector(symbol: str) -> str | None:
    for sector, symbols in SECTORS.items():
        if symbol in symbols:
            return sector
    return None


def get_all_symbols() -> list[str]:
    """Return deduplicated list of all tracked symbols."""
    all_syms = set()
    for symbols in UNIVERSES.values():
        all_syms.update(symbols)
    return sorted(all_syms)
