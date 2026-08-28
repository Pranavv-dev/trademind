"""Cross-sectional, cost-aware backtest of the LIVE ProactiveAgent strategy.

The existing single-symbol BacktestEngine cannot test the real strategy: our live
signals come from ProactiveAgent, which is driven by a daily watchlist that
ContextScorer computes ACROSS the whole universe (relative strength vs a synthetic
NIFTY proxy, sector momentum, etc.). A per-symbol engine never builds that watchlist.

This module closes that gap. For each simulated trading day it:
  1. Scores the whole universe with ContextScorer, using ONLY data up to that day
     (point-in-time, via AsOfCandleRepo — no look-ahead).
  2. Builds the watchlist and injects it into ProactiveAgent.
  3. Runs ProactiveAgent.analyze() per symbol to originate BUY signals.
  4. Sizes with R-based sizing and the live risk caps (max positions, notional cap).
  5. Manages exits exactly like position_monitor: stop-loss, take-profit, trailing
     stop (after +1R), and max-holding-days.
  6. Applies REAL costs on both legs: Zerodha charges (charges.py) + square-root
     slippage (slippage.py). This is the honesty the old simulator lacked.

Headline output is expectancy in R after costs — the system's own deploy gate is
>= 0.2R over >= 30 trades. Run:

    docker compose exec backend python -m app.backtest.proactive_backtest \
        --start 2025-05-01 --end 2026-06-20
"""

from __future__ import annotations

import argparse
import asyncio
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import structlog

from app.agents.proactive import ProactiveAgent
from app.agents.signals import MarketSnapshot
from app.data.context_scorer import ContextScore, ContextScorer
from app.data.universe import NIFTY50
from app.execution.charges import calculate_charges
from app.execution.slippage import SlippageInputs, apply_slippage, compute_slippage_bps

log = structlog.get_logger()


# ──────────────────────────────────────────────────────────────────────────
# Point-in-time candle repo
# ──────────────────────────────────────────────────────────────────────────
class AsOfCandleRepo:
    """Wraps CandleRepository to serve candles only up to a movable `as_of` date.

    Preloads each symbol's daily candles once into memory (newest-first, like the
    real repo) and slices by `as_of` on each call, so ContextScorer can be run for
    every simulated day without per-day DB round-trips or look-ahead leakage.
    """

    def __init__(self, rows_by_symbol: dict[str, list]):
        # rows_by_symbol[symbol] = list of ORM candle rows, NEWEST-FIRST.
        self._rows = rows_by_symbol
        self.as_of: date | None = None

    @classmethod
    async def preload(cls, candle_repo, symbols, start: date, end: date) -> "AsOfCandleRepo":
        # Pull enough history before `start` for the 252-day (52w) lookbacks.
        history_start = datetime.combine(start - timedelta(days=420), datetime.min.time())
        end_dt = datetime.combine(end, datetime.max.time())
        rows_by_symbol: dict[str, list] = {}
        for symbol in symbols:
            rows = await candle_repo.get_candles(
                symbol=symbol,
                exchange="NSE",
                timeframe="1d",
                start=history_start,
                end=end_dt,
                limit=5000,
            )
            if rows:
                # Normalize to newest-first (real repo returns newest-first).
                rows_sorted = sorted(rows, key=lambda c: c.time, reverse=True)
                rows_by_symbol[symbol] = rows_sorted
        return cls(rows_by_symbol)

    async def get_candles(
        self, symbol, exchange="NSE", timeframe="1d", start=None, end=None, limit=252
    ):
        rows = self._rows.get(symbol, [])
        if self.as_of is not None:
            rows = [c for c in rows if c.time.date() <= self.as_of]
        return rows[:limit]

    def trading_dates(self, start: date, end: date) -> list[date]:
        """Union of candle dates in [start, end] across all symbols, ascending."""
        days: set[date] = set()
        for rows in self._rows.values():
            for c in rows:
                d = c.time.date()
                if start <= d <= end:
                    days.add(d)
        return sorted(days)

    def sma(self, symbol: str, d: date, n: int) -> float | None:
        """SMA of the last n closes with time <= d (None if insufficient history)."""
        closes = [float(c.close) for c in self._rows.get(symbol, []) if c.time.date() <= d]
        # rows are newest-first → first n are the most recent
        if len(closes) < n:
            return None
        return sum(closes[:n]) / n

    def bar_on(self, symbol: str, d: date) -> dict | None:
        """The OHLCV bar for `symbol` on date `d`, or None."""
        for c in self._rows.get(symbol, []):
            if c.time.date() == d:
                return {
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                    "volume": int(c.volume),
                }
            if c.time.date() < d:
                break  # newest-first; passed the date
        return None


# ──────────────────────────────────────────────────────────────────────────
# Portfolio with live-equivalent sizing, exits, and real costs
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class BTPosition:
    symbol: str
    quantity: int
    entry_fill: float  # cost-adjusted entry price
    stop_loss: float
    take_profit: float
    entry_date: date
    highest_price: float  # for trailing stop
    risk_rupees: float  # |entry_fill - stop| * qty  (1R in ₹)
    entry_charges: float
    realized_vol_pct: float
    adv_shares: int
    atr: float = 0.0  # absolute ATR at entry (for Chandelier trail)
    trailing_active: bool = False


@dataclass
class BTTrade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_fill: float
    exit_fill: float
    quantity: int
    net_pnl: float  # after both legs' charges
    r_multiple: float
    exit_reason: str
    days_held: int


@dataclass
class BTConfig:
    initial_capital: float = 200_000.0
    risk_per_trade_pct: float = 0.5
    max_notional_pct: float = 7.0
    max_open_positions: int = 7
    max_gross_exposure_pct: float = 70.0
    min_confidence: float = 0.60
    signal_threshold: float = 65.0
    watchlist_threshold: float = 60.0
    max_watchlist: int = 10
    max_holding_days: int = 21  # proactive default
    trailing_pct: float = 0.04  # 4% trail, activates after +1R
    apply_costs: bool = True
    # ---- experiment toggles (all default OFF = current live behavior) ----
    trend_ma: int = 0  # >0: only enter if close > SMA(trend_ma)
    require_market_uptrend: bool = False  # only enter if NIFTY proxy 20d return > 0
    trail_after_r: float = 1.0  # arm trailing stop after this many R
    atr_trail_mult: float = 0.0  # >0: trail = high - mult*ATR (Chandelier) instead of trailing_pct
    # ---- inverted (mean-reversion) entry: buy the most OVERSOLD names ----
    invert: bool = False  # rank by LOW proximity_52w, buy oversold + green-day bounce
    mr_tp_ratio: float = 2.0  # mean-reversion target as multiple of stop distance
    mr_sl_pct: float = 0.05  # fallback SL if ATR unavailable


class Portfolio:
    def __init__(self, cfg: BTConfig):
        self.cfg = cfg
        self.cash = cfg.initial_capital
        self.positions: dict[str, BTPosition] = {}
        self.trades: list[BTTrade] = []
        self.equity_curve: list[dict] = []

    # ----- sizing -----
    def _r_based_qty(self, entry: float, stop: float) -> int:
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0 or entry <= 0:
            return 0
        equity = self.cash + sum(p.quantity * p.entry_fill for p in self.positions.values())
        risk_budget = equity * self.cfg.risk_per_trade_pct / 100.0
        qty = int(risk_budget / risk_per_share)
        notional_cap_qty = int(equity * self.cfg.max_notional_pct / 100.0 / entry)
        return max(0, min(qty, notional_cap_qty))

    def _gross_exposure(self) -> float:
        return sum(p.quantity * p.entry_fill for p in self.positions.values())

    # ----- entries -----
    def try_open(self, symbol: str, signal, score: ContextScore, d: date) -> bool:
        if symbol in self.positions:
            return False
        if len(self.positions) >= self.cfg.max_open_positions:
            return False
        if signal.confidence < self.cfg.min_confidence:
            return False

        raw_entry = float(signal.entry_price)
        stop = float(signal.stop_loss)
        qty = self._r_based_qty(raw_entry, stop)
        if qty <= 0:
            return False

        # Slippage on entry (BUY fills higher), then charges.
        entry_fill = raw_entry
        buy_charges = 0.0
        if self.cfg.apply_costs:
            bps = compute_slippage_bps(
                SlippageInputs(
                    price=raw_entry,
                    quantity=qty,
                    realized_vol_pct=score.realized_vol_pct,
                    adv_shares=score.adv_20,
                )
            )
            entry_fill = apply_slippage(raw_entry, "BUY", bps)
            buy_charges = float(
                calculate_charges(Decimal(str(entry_fill)), qty, "BUY", "CNC", "NSE")["total"]
            )

        cost = qty * entry_fill + buy_charges
        equity = self.cash + self._gross_exposure()
        if cost > self.cash:
            return False
        if (
            self._gross_exposure() + qty * entry_fill
        ) > equity * self.cfg.max_gross_exposure_pct / 100.0:
            return False

        self.cash -= cost
        self.positions[symbol] = BTPosition(
            symbol=symbol,
            quantity=qty,
            entry_fill=entry_fill,
            stop_loss=stop,
            take_profit=float(signal.take_profit),
            entry_date=d,
            highest_price=raw_entry,
            risk_rupees=abs(entry_fill - stop) * qty,
            entry_charges=buy_charges,
            realized_vol_pct=score.realized_vol_pct,
            adv_shares=score.adv_20,
            atr=(score.tightness * raw_entry) if score.tightness > 0 else 0.0,
        )
        return True

    # ----- exits (mirror position_monitor order: SL, TP, trailing, max-hold) -----
    def check_exits(self, symbol: str, bar: dict, d: date) -> None:
        pos = self.positions.get(symbol)
        if pos is None:
            return
        high, low, close = bar["high"], bar["low"], bar["close"]

        # ratchet trailing reference + activation after +1R
        if high > pos.highest_price:
            pos.highest_price = high
        arm_price = pos.entry_fill + self.cfg.trail_after_r * pos.risk_rupees / pos.quantity
        if not pos.trailing_active and high >= arm_price:
            pos.trailing_active = True

        exit_price = None
        reason = None
        # 1. stop-loss (conservative first)
        if pos.stop_loss > 0 and low <= pos.stop_loss:
            exit_price, reason = pos.stop_loss, "stop_loss"
        # 2. take-profit
        elif pos.take_profit > 0 and high >= pos.take_profit:
            exit_price, reason = pos.take_profit, "take_profit"
        # 3. trailing stop (Chandelier if atr_trail_mult set, else fixed %)
        elif pos.trailing_active:
            if self.cfg.atr_trail_mult > 0 and pos.atr > 0:
                trail = pos.highest_price - self.cfg.atr_trail_mult * pos.atr
            else:
                trail = pos.highest_price * (1 - self.cfg.trailing_pct)
            if low <= trail:
                exit_price, reason = trail, "trailing_stop"
        # 4. max-holding-days
        if exit_price is None and (d - pos.entry_date).days >= self.cfg.max_holding_days:
            exit_price, reason = close, "max_holding_days"

        if exit_price is not None:
            self._close(symbol, exit_price, d, reason)

    def _close(self, symbol: str, raw_exit: float, d: date, reason: str) -> None:
        pos = self.positions.pop(symbol)
        exit_fill = raw_exit
        sell_charges = 0.0
        if self.cfg.apply_costs:
            bps = compute_slippage_bps(
                SlippageInputs(
                    price=raw_exit,
                    quantity=pos.quantity,
                    realized_vol_pct=pos.realized_vol_pct,
                    adv_shares=pos.adv_shares,
                )
            )
            exit_fill = apply_slippage(raw_exit, "SELL", bps)
            sell_charges = float(
                calculate_charges(Decimal(str(exit_fill)), pos.quantity, "SELL", "CNC", "NSE")[
                    "total"
                ]
            )

        self.cash += pos.quantity * exit_fill - sell_charges
        gross = (exit_fill - pos.entry_fill) * pos.quantity
        net = gross - pos.entry_charges - sell_charges
        r_mult = net / pos.risk_rupees if pos.risk_rupees > 0 else 0.0
        self.trades.append(
            BTTrade(
                symbol=symbol,
                entry_date=pos.entry_date,
                exit_date=d,
                entry_fill=round(pos.entry_fill, 2),
                exit_fill=round(exit_fill, 2),
                quantity=pos.quantity,
                net_pnl=round(net, 2),
                r_multiple=round(r_mult, 3),
                exit_reason=reason,
                days_held=(d - pos.entry_date).days,
            )
        )

    def mark(self, d: date, closes: dict[str, float]) -> None:
        pv = sum(p.quantity * closes.get(s, p.entry_fill) for s, p in self.positions.items())
        self.equity_curve.append({"date": str(d), "value": round(self.cash + pv, 2)})

    def force_close_all(self, closes: dict[str, float], d: date) -> None:
        for s in list(self.positions.keys()):
            self._close(s, closes.get(s, self.positions[s].entry_fill), d, "backtest_end")


# ──────────────────────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────────────────────
def summarize(trades: list[BTTrade], equity_curve: list[dict], initial: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "note": "no trades generated"}
    rs = [t.r_multiple for t in trades]
    pnls = [t.net_pnl for t in trades]
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    expectancy_r = sum(rs) / n

    # Sharpe from daily equity returns (annualized, 252d)
    sharpe = 0.0
    if len(equity_curve) > 2:
        vals = [e["value"] for e in equity_curve]
        rets = [(vals[i] / vals[i - 1] - 1) for i in range(1, len(vals)) if vals[i - 1] > 0]
        if rets:
            m = sum(rets) / len(rets)
            var = sum((r - m) ** 2 for r in rets) / len(rets)
            sd = var**0.5
            if sd > 0:
                sharpe = (m / sd) * math.sqrt(252)

    # Max drawdown
    peak = -1e18
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e["value"])
        if peak > 0:
            max_dd = max(max_dd, (peak - e["value"]) / peak)

    return {
        "trades": n,
        "win_rate_pct": round(100 * len(wins) / n, 1),
        "expectancy_R": round(expectancy_r, 3),
        "avg_win_R": round(sum(t.r_multiple for t in wins) / len(wins), 3) if wins else 0.0,
        "avg_loss_R": round(sum(t.r_multiple for t in losses) / len(losses), 3) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "total_net_pnl": round(sum(pnls), 2),
        "return_pct": round(100 * sum(pnls) / initial, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(100 * max_dd, 1),
        "deploy_gate_pass": bool(expectancy_r >= 0.2 and n >= 30),
    }


# ──────────────────────────────────────────────────────────────────────────
# Universe resolution
# ──────────────────────────────────────────────────────────────────────────
INDEX_NAME = "NIFTY50"


def _fixed_universe(symbols: list[str]):
    """A universe that never changes — explicit symbols, or the fallback roster."""
    frozen = list(symbols)
    return lambda _d: frozen


def _membership_universe(tenures: list[tuple[str, date, date | None]]):
    """Resolve point-in-time membership in memory from overlapping tenures.

    `tenures` is (symbol, from_date, to_date) with to_date=None meaning "still a
    member". Results are memoised per date: a daily backtest asks for the same
    roster ~250 times a year and membership only moves at reconstitution.
    """
    cache: dict[date, list[str]] = {}

    def universe_on(d: date) -> list[str]:
        hit = cache.get(d)
        if hit is None:
            hit = sorted({sym for sym, frm, to in tenures if frm <= d and (to is None or to >= d)})
            cache[d] = hit
        return hit

    return universe_on


# ──────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────
async def run_proactive_backtest(
    session,
    start: date,
    end: date,
    cfg: BTConfig | None = None,
    symbols: list[str] | None = None,
) -> dict:
    from app.db.repositories.candle_repo import CandleRepository
    from app.db.repositories.index_membership_repo import IndexMembershipRepository

    cfg = cfg or BTConfig()
    candle_repo = CandleRepository(session)

    # ── Universe: point-in-time membership, or today's roster as a fallback ──
    #
    # Scoring every simulated day against TODAY's NIFTY 50 is survivorship bias:
    # every current member survived, and the names that were dropped for
    # underperformance are invisible. When index_membership is seeded we resolve
    # the roster that actually existed on each bar; otherwise we fall back to the
    # static list and say so loudly, because the resulting numbers are inflated.
    explicit_symbols = symbols is not None
    coverage_start: date | None = None
    membership_repo = IndexMembershipRepository(session)
    has_membership = (not explicit_symbols) and await membership_repo.has_any(INDEX_NAME)

    if explicit_symbols:
        universe_method = "explicit_symbols"
        universe_on = _fixed_universe(list(symbols))
        preload_symbols = list(symbols)
    elif has_membership:
        universe_method = "point_in_time"
        tenures = await membership_repo.rows_in_range(INDEX_NAME, start, end)
        universe_on = _membership_universe(tenures)
        # Preload candles for every name that was a member at ANY point in the
        # window — the roster changes underneath us as the simulation advances.
        preload_symbols = sorted({sym for sym, _, _ in tenures})

        # Seeded membership that begins after `start` leaves the early part of the
        # window with an EMPTY roster: it trades nothing and still reports the full
        # period, which reads as a valid result. Refuse to be quiet about it.
        coverage_start = min((frm for _s, frm, _t in tenures), default=None)
        if coverage_start is not None and coverage_start > start:
            uncovered_days = (coverage_start - start).days
            log.warning(
                "bt_universe_coverage_gap",
                requested_start=str(start),
                coverage_start=str(coverage_start),
                uncovered_days=uncovered_days,
                reason=(
                    "index_membership has no rows before coverage_start, so the "
                    "universe is empty until then and those bars trade nothing. "
                    "Extend RECONSTITUTIONS in app/db/seed_membership.py, or start "
                    "the backtest at coverage_start."
                ),
            )
    else:
        universe_method = "current_only_fallback"
        log.warning(
            "bt_universe_fallback",
            reason=(
                "index_membership table empty — scoring against today's NIFTY 50. "
                "Results carry survivorship bias; seed it with "
                "`python -m app.db.seed_membership`."
            ),
        )
        universe_on = _fixed_universe(list(NIFTY50))
        preload_symbols = list(NIFTY50)

    asof = await AsOfCandleRepo.preload(candle_repo, preload_symbols, start, end)
    scorer = ContextScorer(candle_repo=asof)  # scorer reads point-in-time via wrapper
    agent = ProactiveAgent(
        agent_id="bt-proactive",
        name="BT Proactive",
        config={
            "signal_threshold": cfg.signal_threshold,
            "watchlist_threshold": cfg.watchlist_threshold,
            "max_watchlist": cfg.max_watchlist,
        },
    )
    pf = Portfolio(cfg)

    dates = asof.trading_dates(start, end)
    log.info(
        "bt_start",
        start=str(start),
        end=str(end),
        n_days=len(dates),
        n_symbols=len(asof._rows),
        universe_method=universe_method,
        costs=cfg.apply_costs,
    )

    for d in dates:
        asof.as_of = d

        # 1. exits first, using today's bar
        for sym in list(pf.positions.keys()):
            bar = asof.bar_on(sym, d)
            if bar:
                pf.check_exits(sym, bar, d)

        # 2. score universe point-in-time → watchlist
        scores = await scorer.score_universe(symbols=universe_on(d))
        if cfg.invert:
            # Mean-reversion: rank by LOWEST 52w proximity (most oversold), take N.
            ranked = sorted(scores.items(), key=lambda kv: kv[1].proximity_52w)[: cfg.max_watchlist]
            watchlist = dict(ranked)
        else:
            watchlist = {
                s: sc for s, sc in scores.items() if sc.total_score >= cfg.watchlist_threshold
            }
            if len(watchlist) > cfg.max_watchlist:
                top = sorted(watchlist.items(), key=lambda kv: kv[1].total_score, reverse=True)[
                    : cfg.max_watchlist
                ]
                watchlist = dict(top)
        agent.set_watchlist(watchlist)

        # Market-regime gate: is the synthetic NIFTY proxy rising?
        market_up = True
        if cfg.require_market_uptrend and scores:
            any_score = next(iter(scores.values()))
            nifty_20d = any_score.components.get("nifty_proxy_20d_return_pct", 0.0)
            market_up = nifty_20d > 0

        # 3. originate + open
        for sym, score in watchlist.items():
            if sym in pf.positions:
                continue
            if cfg.require_market_uptrend and not market_up:
                continue
            # Trend gate: only buy above the trend MA
            if cfg.trend_ma > 0:
                ma = asof.sma(sym, d, cfg.trend_ma)
                bar0 = asof.bar_on(sym, d)
                if ma is None or bar0 is None or bar0["close"] <= ma:
                    continue
            bar = asof.bar_on(sym, d)
            if not bar:
                continue

            if cfg.invert:
                # Mean-reversion entry: buy the oversold name on a green-day bounce.
                if bar["close"] <= bar["open"] or bar["open"] <= 0:
                    continue
                entry = bar["close"]
                atr_mult = score.atr_multiplier if score.atr_multiplier > 0 else 1.5
                sl_pct = (
                    max(0.04, min(0.08, atr_mult * score.tightness))
                    if score.tightness > 0
                    else cfg.mr_sl_pct
                )
                signal = SimpleNamespace(
                    action="BUY",
                    confidence=0.65,
                    entry_price=entry,
                    stop_loss=round(entry * (1 - sl_pct), 2),
                    take_profit=round(entry * (1 + sl_pct * cfg.mr_tp_ratio), 2),
                )
                pf.try_open(sym, signal, score, d)
            else:
                snap = MarketSnapshot(
                    symbol=sym,
                    exchange="NSE",
                    ltp=bar["close"],
                    open=bar["open"],
                    high=bar["high"],
                    low=bar["low"],
                    close=bar["close"],
                    volume=bar["volume"],
                    candles_1d=[],
                    timestamp=datetime.now(timezone.utc),
                )
                signal = await agent.analyze(snap)
                if signal and signal.action == "BUY":
                    pf.try_open(sym, signal, score, d)

        # 4. mark to market
        closes = {
            s: (asof.bar_on(s, d) or {}).get("close", pf.positions[s].entry_fill)
            for s in pf.positions
        }
        pf.mark(d, closes)

    # close out
    if dates:
        last = dates[-1]
        closes = {
            s: (asof.bar_on(s, last) or {}).get("close", p.entry_fill)
            for s, p in pf.positions.items()
        }
        pf.force_close_all(closes, last)

    result = summarize(pf.trades, pf.equity_curve, cfg.initial_capital)
    result["period"] = f"{start} → {end}"
    result["costs_applied"] = cfg.apply_costs
    result["universe_method"] = universe_method
    if coverage_start is not None and coverage_start > start:
        # Carried on the result so a caller writing up the numbers can't miss it.
        result["universe_coverage_start"] = str(coverage_start)
        result["universe_warning"] = (
            f"membership data starts {coverage_start}; bars from {start} to that date "
            f"had an empty universe and traded nothing"
        )
    return result


async def _main(args):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.config import settings

    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cfg = BTConfig(
        apply_costs=not args.no_costs,
        initial_capital=args.capital,
        trend_ma=args.trend_ma,
        require_market_uptrend=args.market_uptrend,
        trail_after_r=args.trail_after_r,
        atr_trail_mult=args.atr_trail_mult,
        invert=args.invert,
        mr_tp_ratio=args.mr_tp_ratio,
    )
    try:
        async with factory() as session:
            result = await run_proactive_backtest(
                session,
                date.fromisoformat(args.start),
                date.fromisoformat(args.end),
                cfg=cfg,
            )
    finally:
        await engine.dispose()

    print("\n=== ProactiveAgent backtest (cross-sectional, point-in-time) ===")
    for k, v in result.items():
        print(f"  {k:>20}: {v}")
    # Compare cost on/off if requested
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--capital", type=float, default=200_000.0)
    p.add_argument(
        "--no-costs", action="store_true", help="disable charges+slippage (to measure cost drag)"
    )
    p.add_argument(
        "--trend-ma",
        type=int,
        default=0,
        dest="trend_ma",
        help="only enter if close > SMA(N); 0=off",
    )
    p.add_argument(
        "--market-uptrend",
        action="store_true",
        dest="market_uptrend",
        help="only enter if NIFTY proxy 20d return > 0",
    )
    p.add_argument(
        "--trail-after-r",
        type=float,
        default=1.0,
        dest="trail_after_r",
        help="arm trailing stop after this many R",
    )
    p.add_argument(
        "--atr-trail-mult",
        type=float,
        default=0.0,
        dest="atr_trail_mult",
        help=">0: Chandelier trail = high - mult*ATR",
    )
    p.add_argument(
        "--invert",
        action="store_true",
        help="mean-reversion: buy most-oversold (low 52w proximity) names",
    )
    p.add_argument(
        "--mr-tp-ratio",
        type=float,
        default=2.0,
        dest="mr_tp_ratio",
        help="mean-reversion target as multiple of stop distance",
    )
    asyncio.run(_main(p.parse_args()))
