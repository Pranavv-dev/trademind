"""Download initial NIFTY 50 historical data (2 years of daily candles)."""

import asyncio
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.data.downloader import DataDownloader  # noqa: E402
from app.data.universe import NIFTY50  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402


async def main():
    print(f"Seeding historical data for {len(NIFTY50)} NIFTY 50 symbols...")
    async with async_session_factory() as session:
        downloader = DataDownloader(session)
        results = await downloader.download_universe(NIFTY50)

    total = sum(results.values())
    succeeded = sum(1 for v in results.values() if v > 0)
    print(f"\nDone! Downloaded {total} candles for {succeeded}/{len(NIFTY50)} symbols.")

    for symbol, count in sorted(results.items()):
        status = f"{count} candles" if count > 0 else "FAILED"
        print(f"  {symbol:>15}: {status}")


if __name__ == "__main__":
    asyncio.run(main())
