"""News scraper for Indian financial news sources."""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

import feedparser
import httpx
import structlog

log = structlog.get_logger()

RSS_FEEDS = {
    "et_markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "moneycontrol": "https://www.moneycontrol.com/rss/marketreports.xml",
    "livemint": "https://www.livemint.com/rss/markets",
    "ndtv_profit": "https://feeds.feedburner.com/ndtvprofit-latest",
}

# Common company name -> symbol mapping for better headline matching
COMPANY_ALIASES = {
    "RELIANCE": ["RELIANCE", "RIL", "MUKESH AMBANI", "JIO"],
    "TCS": ["TCS", "TATA CONSULTANCY"],
    "HDFCBANK": ["HDFC BANK", "HDFCBANK"],
    "INFY": ["INFOSYS", "INFY"],
    "ICICIBANK": ["ICICI BANK", "ICICIBANK"],
    "SBIN": ["SBI", "STATE BANK"],
    "BHARTIARTL": ["AIRTEL", "BHARTI AIRTEL"],
    "ITC": ["ITC"],
    "KOTAKBANK": ["KOTAK", "KOTAK MAHINDRA"],
    "LT": ["L&T", "LARSEN"],
    "HCLTECH": ["HCL TECH", "HCLTECH"],
    "AXISBANK": ["AXIS BANK"],
    "MARUTI": ["MARUTI", "MARUTI SUZUKI"],
    "SUNPHARMA": ["SUN PHARMA"],
    "TITAN": ["TITAN"],
    "BAJFINANCE": ["BAJAJ FINANCE"],
    "TATAMOTORS": ["TATA MOTORS"],
    "WIPRO": ["WIPRO"],
    "ADANIENT": ["ADANI ENT", "ADANI"],
    "TATASTEEL": ["TATA STEEL"],
    "HINDUNILVR": ["HUL", "HINDUSTAN UNILEVER"],
    "BAJAJFINSV": ["BAJAJ FINSERV"],
    "NTPC": ["NTPC"],
    "ONGC": ["ONGC"],
    "COALINDIA": ["COAL INDIA"],
    "POWERGRID": ["POWER GRID"],
    "JSWSTEEL": ["JSW STEEL"],
}


@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published: datetime | None = None
    symbols: list[str] = field(default_factory=list)


class NewsScraper:
    """Fetches and parses financial news from RSS feeds."""

    _cache: list["NewsItem"] = []
    _cache_time: float = 0.0
    _cache_ttl: float = 300.0  # 5 minutes
    _fetching: bool = False

    async def fetch_headlines(self, sources: list[str] | None = None) -> list[NewsItem]:
        """Fetch recent news headlines from configured RSS feeds."""
        if sources is None:
            sources = list(RSS_FEEDS.keys())

        # Return cached results if fresh (avoids 200+ HTTP requests per scan)
        if (
            NewsScraper._cache
            and (time.monotonic() - NewsScraper._cache_time) < NewsScraper._cache_ttl
        ):
            return NewsScraper._cache

        # Simple guard: if another coroutine is already fetching, return stale cache
        if NewsScraper._fetching:
            return NewsScraper._cache

        NewsScraper._fetching = True
        try:
            all_items: list[NewsItem] = []
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=15),
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; TradeMind/1.0)"},
            ) as client:
                tasks = []
                for source in sources:
                    url = RSS_FEEDS.get(source)
                    if url:
                        tasks.append(self._fetch_feed(client, source, url))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, list):
                        all_items.extend(result)

            fetched = sorted(all_items, key=lambda x: x.published or datetime.min, reverse=True)
            NewsScraper._cache = fetched
            NewsScraper._cache_time = time.monotonic()
            log.info("news_fetched", total_headlines=len(fetched))
            return fetched
        finally:
            NewsScraper._fetching = False

    async def _fetch_feed(self, client: httpx.AsyncClient, source: str, url: str) -> list[NewsItem]:
        """Fetch and parse a single RSS feed."""
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.warning("news_feed_failed", source=source, status=resp.status_code)
                return []

            feed = feedparser.parse(resp.text)
            items = []
            for entry in feed.entries[:20]:  # Last 20 headlines
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6])

                items.append(
                    NewsItem(
                        title=entry.get("title", ""),
                        source=source,
                        url=entry.get("link", ""),
                        published=published,
                    )
                )
            log.debug("news_feed_ok", source=source, headlines=len(items))
            return items
        except Exception as e:
            log.warning("news_feed_error", source=source, error=str(e)[:100])
            return []

    def find_symbol_mentions(
        self, items: list[NewsItem], symbols: list[str]
    ) -> dict[str, list[NewsItem]]:
        """Map symbols to news items that mention them.

        Uses company aliases for better matching (e.g. 'Infosys' matches INFY).
        """
        mentions: dict[str, list[NewsItem]] = {s: [] for s in symbols}
        for item in items:
            title_upper = item.title.upper()
            for symbol in symbols:
                # Check exact symbol match
                matched = symbol in title_upper
                # Check aliases
                if not matched and symbol in COMPANY_ALIASES:
                    for alias in COMPANY_ALIASES[symbol]:
                        if alias in title_upper:
                            matched = True
                            break
                if matched:
                    item.symbols.append(symbol)
                    mentions[symbol].append(item)
        return mentions


news_scraper = NewsScraper()
