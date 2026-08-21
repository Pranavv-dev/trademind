import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.schemas import QuoteResponse, StockSearchResult
from app.dependencies import get_redis

router = APIRouter()

# NIFTY 50 universe (hardcoded for now, will be dynamic later)
NIFTY50_SYMBOLS = [
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

BANKNIFTY_SYMBOLS = [
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

UNIVERSES = {
    "NIFTY50": NIFTY50_SYMBOLS,
    "BANKNIFTY": BANKNIFTY_SYMBOLS,
}


@router.get("/quote/{symbol}", response_model=QuoteResponse)
async def get_quote(
    symbol: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    # Try to get cached price data from Redis
    import json

    data = await redis.get(f"price:NSE:{symbol.upper()}")
    if not data:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    quote = json.loads(data)
    return QuoteResponse(**quote)


@router.get("/search", response_model=list[StockSearchResult])
async def search_stocks(q: str = Query(..., min_length=1)):
    from app.data.feeds.instruments import get_instrument_master

    instrument_master = get_instrument_master()

    # Use instrument master if loaded, else fallback to hardcoded list
    if instrument_master.loaded:
        instruments = instrument_master.search(q, exchange="NSE", limit=20)
        return [
            StockSearchResult(
                symbol=inst["symbol"],
                name=inst.get("name", inst["symbol"]),
                exchange=inst.get("exchange", "NSE"),
            )
            for inst in instruments
        ]

    query = q.upper()
    results = []
    for symbol in NIFTY50_SYMBOLS + BANKNIFTY_SYMBOLS:
        if query in symbol:
            results.append(StockSearchResult(symbol=symbol, name=symbol, exchange="NSE"))
    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        if r.symbol not in seen:
            seen.add(r.symbol)
            unique.append(r)
    return unique[:20]


@router.post("/sync-candles")
async def sync_candles(symbols: list[str] | None = Query(None), days: int = Query(365)):
    """Trigger historical candle download for given symbols (or agent universe)."""
    from datetime import datetime, timedelta

    from app.data.downloader import DataDownloader
    from app.db.session import async_session_factory

    if not symbols:
        from app.data.universe import get_all_symbols

        symbols = get_all_symbols()
        if not symbols:
            symbols = ["RELIANCE", "TCS", "INFY"]

    start = datetime.now() - timedelta(days=days)
    async with async_session_factory() as session:
        dl = DataDownloader(session)
        results = await dl.download_universe(symbols, start=start)
    return {"results": results, "total_candles": sum(results.values())}


@router.get("/universe/{name}", response_model=list[str])
async def get_universe(name: str):
    symbols = UNIVERSES.get(name.upper())
    if symbols is None:
        raise HTTPException(status_code=404, detail=f"Universe '{name}' not found")
    return symbols
