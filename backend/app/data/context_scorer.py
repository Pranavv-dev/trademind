"""ContextScorer — computes proactive (leading) signals per stock from daily candle data.

Outputs a daily watchlist of high-conviction stocks BEFORE intraday price action. Used by
the ProactiveAgent as its primary candidate generator.

Signals (all derived from D1 candles already in DB — no external data needed):
- Relative Strength (RS): stock's 20d return vs synthetic NIFTY 20d return
- Sector Momentum: average 20d return of stock's sector vs NIFTY
- 52-week proximity + consolidation tightness (coiled-spring breakout candidates)
- Volume Z-score: today's volume vs 20d mean (institutional surge detection)

A higher context_score → stronger proactive conviction. Top quartile = watchlist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from app.data.universe import NIFTY50, SECTORS, get_sector
from app.db.repositories.candle_repo import CandleRepository

log = structlog.get_logger()


@dataclass
class ContextScore:
    symbol: str
    rs_score: float  # >1 = outperforming index
    sector_momentum: float  # +ve = sector outperforming
    proximity_52w: float  # 0-1, closer to 1 = near 52w high
    volume_zscore: float  # how many SDs above mean
    tightness: float  # ATR/price (smaller = more consolidating)
    total_score: float  # 0-100
    # New fields for Phase B — realistic-cost & ATR R-multiple wiring:
    realized_vol_pct: float = 0.0  # daily realized vol in PERCENT
    adv_20: int = 0  # 20-day average daily volume in SHARES
    atr_multiplier: float = 1.5  # k for SL = entry - k×ATR (regime-bucketed)
    components: dict[str, float] = field(default_factory=dict)

    @property
    def watchlist_eligible(self) -> bool:
        return self.total_score >= 60


class ContextScorer:
    """Computes per-stock context scores from D1 candles."""

    def __init__(self, candle_repo: CandleRepository):
        self.candle_repo = candle_repo

    async def score_universe(
        self,
        symbols: list[str] | None = None,
        memory_multipliers: dict[str, float] | None = None,
    ) -> dict[str, ContextScore]:
        """Compute context scores for the full NIFTY 50 universe (or a subset).

        memory_multipliers: optional per-symbol multiplier (from SignalMemory) applied
        to the final score. Encodes the learning-loop lesson — down-weight symbols that
        have been stopping us out, lean into symbols that have worked. 1.0 = neutral.
        """
        symbols = symbols or NIFTY50
        memory_multipliers = memory_multipliers or {}

        # Step 1: load last 252 D1 candles for each symbol
        returns_by_symbol: dict[str, dict] = {}
        for symbol in symbols:
            data = await self._compute_basics(symbol)
            if data is not None:
                returns_by_symbol[symbol] = data

        if not returns_by_symbol:
            log.warning("context_scorer_no_data")
            return {}

        # Step 2: build synthetic NIFTY 20d return (equal-weighted mean)
        valid_20d_returns = [
            d["return_20d"] for d in returns_by_symbol.values() if d["return_20d"] is not None
        ]
        nifty_proxy_20d = (
            sum(valid_20d_returns) / len(valid_20d_returns) if valid_20d_returns else 0.0
        )

        # Step 3: sector momentum (each sector's avg 20d return vs NIFTY proxy)
        sector_momentum: dict[str, float] = {}
        for sector_name, sector_symbols in SECTORS.items():
            sector_returns = [
                returns_by_symbol[s]["return_20d"]
                for s in sector_symbols
                if s in returns_by_symbol and returns_by_symbol[s]["return_20d"] is not None
            ]
            if sector_returns:
                avg = sum(sector_returns) / len(sector_returns)
                sector_momentum[sector_name] = avg - nifty_proxy_20d

        # Step 4: compute per-stock composite score
        scores: dict[str, ContextScore] = {}
        for symbol, data in returns_by_symbol.items():
            rs_score = self._compute_rs(data["return_20d"], nifty_proxy_20d)
            sector = get_sector(symbol)
            sec_mom = sector_momentum.get(sector, 0.0) if sector else 0.0

            # Subscores normalized to 0-100
            rs_component = max(min((rs_score - 0.95) * 500, 100), 0)  # 1.0 RS = 25, 1.2 RS = 100
            sec_component = max(min(sec_mom * 1000 + 50, 100), 0)  # 0 sector_mom = 50, +5% = 100
            prox_component = data["proximity_52w"] * 100  # 0-1 → 0-100
            vol_component = max(
                min((data["volume_zscore"] + 1) * 25, 100), 0
            )  # z=-1→0, z=1→50, z=3→100
            tight_component = max(
                min((1 - data["tightness"] * 50) * 100, 100), 0
            )  # smaller ATR/price = higher

            total = (
                rs_component * 0.25
                + sec_component * 0.20
                + prox_component * 0.25
                + vol_component * 0.20
                + tight_component * 0.10
            )

            # Apply the learning-loop memory multiplier (1.0 = neutral). Down-weights
            # symbols that have been stopping us out; modestly rewards proven winners.
            mem_mult = memory_multipliers.get(symbol, 1.0)
            total = total * mem_mult

            # ATR multiplier (k) by regime: tighter in trends, wider in chop.
            # Simple proxy for regime: |sector_momentum| > 0.03 (3%/20d) = trending.
            # Could later be replaced with proper ADX-bucketed classification.
            if abs(sec_mom) > 0.03:
                k = 1.2  # trending
            elif data["tightness"] > 0.025:
                k = 2.0  # chop / volatile
            else:
                k = 1.5  # neutral default

            scores[symbol] = ContextScore(
                symbol=symbol,
                rs_score=rs_score,
                sector_momentum=sec_mom,
                proximity_52w=data["proximity_52w"],
                volume_zscore=data["volume_zscore"],
                tightness=data["tightness"],
                realized_vol_pct=data.get("realized_vol_pct", 0.0),
                adv_20=data.get("adv_20", 0),
                atr_multiplier=k,
                total_score=round(total, 2),
                components={
                    "rs": round(rs_component, 1),
                    "sector": round(sec_component, 1),
                    "proximity": round(prox_component, 1),
                    "volume": round(vol_component, 1),
                    "tightness": round(tight_component, 1),
                    "atr_multiplier": k,
                    "memory_mult": round(mem_mult, 3),
                    "nifty_proxy_20d_return_pct": round(nifty_proxy_20d * 100, 2),
                },
            )

        return scores

    async def _compute_basics(self, symbol: str) -> dict | None:
        """Compute per-symbol metrics from last 252 D1 candles."""
        candles = await self.candle_repo.get_candles(
            symbol=symbol,
            exchange="NSE",
            timeframe="1d",
            limit=252,
        )
        if len(candles) < 30:  # need at least ~30 days
            return None

        # Order oldest → newest
        candles = list(reversed(candles))
        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        volumes = [int(c.volume) for c in candles]

        # 20d return
        if len(closes) >= 21 and closes[-21] > 0:
            return_20d = (closes[-1] / closes[-21]) - 1.0
        else:
            return_20d = None

        # 52w (252d) proximity — 0 = at 52w low, 1 = at 52w high
        high_52w = max(highs[-252:]) if len(highs) >= 1 else max(highs)
        low_52w = min(lows[-252:]) if len(lows) >= 1 else min(lows)
        rng = high_52w - low_52w
        proximity_52w = ((closes[-1] - low_52w) / rng) if rng > 0 else 0.5

        # Volume Z-score (today vs last 20d mean/std)
        vol_zscore = 0.0
        if len(volumes) >= 21:
            recent20 = volumes[-21:-1]  # excluding today
            mean = sum(recent20) / len(recent20)
            if mean > 0:
                var = sum((v - mean) ** 2 for v in recent20) / len(recent20)
                std = var**0.5
                if std > 0:
                    vol_zscore = (volumes[-1] - mean) / std

        # Tightness: 14-day true range avg / current price (smaller = consolidating)
        tr_values = []
        for i in range(max(1, len(closes) - 14), len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr_values.append(tr)
        atr14 = sum(tr_values) / len(tr_values) if tr_values else 0
        tightness = (atr14 / closes[-1]) if closes[-1] > 0 else 0.05

        # Realized vol — 20-day standard deviation of daily log returns,
        # annualized? No — we want daily-scale for the slippage model.
        # Output in PERCENT for downstream consumers (e.g. 1.6 = 1.6%/day).
        realized_vol_pct = 0.0
        if len(closes) >= 21:
            import math

            recent = closes[-21:]
            rets = []
            for i in range(1, len(recent)):
                if recent[i - 1] > 0:
                    rets.append(math.log(recent[i] / recent[i - 1]))
            if rets:
                mean = sum(rets) / len(rets)
                var = sum((r - mean) ** 2 for r in rets) / len(rets)
                realized_vol_pct = (var**0.5) * 100.0

        # 20-day average daily volume (shares). Used by the slippage model.
        adv_20 = 0
        if len(volumes) >= 20:
            adv_20 = int(sum(volumes[-20:]) / 20)

        return {
            "return_20d": return_20d,
            "proximity_52w": proximity_52w,
            "volume_zscore": vol_zscore,
            "tightness": tightness,
            "realized_vol_pct": realized_vol_pct,
            "adv_20": adv_20,
        }

    @staticmethod
    def _compute_rs(stock_20d: float | None, nifty_20d: float) -> float:
        """Relative strength = (1 + stock_return) / (1 + nifty_return). >1 = outperforming."""
        if stock_20d is None:
            return 1.0
        denom = 1 + nifty_20d
        if abs(denom) < 1e-9:
            return 1.0
        return (1 + stock_20d) / denom
