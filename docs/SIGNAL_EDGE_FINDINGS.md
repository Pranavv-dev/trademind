# Signal Edge Findings — why TradeMind wasn't making money

_Investigation date: 2026-07-01. Tooling: `backend/app/backtest/proactive_backtest.py`
(cross-sectional, cost-aware backtest of the real ProactiveAgent/ContextScorer signal)
and `backend/app/backtest/factor_study.py` (factor information-coefficient study)._

## TL;DR

The live momentum strategy has **negative expectancy, even before costs** — and the
factor study shows **it is pointed the wrong way**. NIFTY-50 large caps mean-revert;
the system buys breakouts/strength, i.e. it systematically buys the side that fades.
Inverting the entry (buy oversold, not breakouts) improves expectancy ~10× to roughly
breakeven. **Not yet deployable. Needs 5y validation + tuning before any live use.**

## Evidence

### 1. The real strategy, backtested honestly for the first time (12 months, ~144 trades)
| | Win rate | Avg win | Avg loss | Expectancy | PF |
|---|---|---|---|---|---|
| Gross (no costs) | 43.8% | +0.77R | −0.80R | **−0.115R** | 0.79 |
| Net (real costs) | 42.7% | +0.73R | −0.83R | **−0.165R** | 0.69 |

No exit/filter configuration (trend gate, market regime, let-winners-run, Chandelier)
made it positive. Best was "let winners run" (trail arms at +2R not +1R): −0.042R gross.

### 2. Factor IC study (6,279 obs, weekly samples, Spearman vs forward return)
| factor | 5d | 10d | 21d |
|---|---|---|---|
| total_score | −0.046 | −0.082 | −0.078 |
| rs_score | −0.031 | −0.052 | −0.061 |
| sector_momentum | −0.033 | −0.043 | −0.030 |
| **proximity_52w** | −0.070 | −0.097 | **−0.127** |
| volume_zscore | −0.009 | −0.015 | +0.059 |
| tightness | +0.033 | +0.053 | −0.012 |

Negative IC across nearly all factors, strengthening with horizon. `proximity_52w`
IC = −0.127 @ 21d is a *large* effect: stocks near 52w highs (which we buy) underperform;
oversold names outperform. **The edge is mean-reversion; the strategy is momentum.**

### 3. Inverting the entry (net, with costs)
| Variant | trades | win% | Expectancy | PF |
|---|---|---|---|---|
| Momentum (current live) | 143 | 42.7 | −0.165R | 0.69 |
| Inverted (buy oversold) | 168 | 47.0 | −0.034R | 0.92 |
| Inverted + let winners run (2R) | 160 | 43.8 | **−0.016R** | 0.98 |

10× expectancy improvement, sign-consistent with the IC study.

## Why strong IC only buys breakeven trades
- Long-only on NIFTY-50 can't short the "high proximity underperforms" half → harvests
  only the oversold-rebound side.
- Mean-reversion trades more often → cost-sensitive (~0.05R/trade drag).
- Crude trigger ("lowest 52w proximity + green day"); no RSI/distance-from-MA; hold not
  tuned to the ~21d reversion horizon.

## Hard caveats
- **One ~12-month regime** (2025-06 → 2026-06). Mean-reversion is regime-dependent and
  blows up in trends/crises. NOT validated out-of-sample.
- Data: only ~15 months of daily candles on the VM; `index_membership` not seeded →
  current-universe (mild survivorship bias); 52w proximity biased early.
- Daily entry proxy (close>open) stands in for live intraday confirmation.

## 5-YEAR VALIDATION (2021–2026, ~900 trades) — added 2026-07-01
Backfilled ~5y daily candles via yfinance (`backend/app/data/backfill_yf.py`, 48/50
symbols, 77k rows). Re-ran across bull/bear/sideways regimes:

| Variant (net, costs) | win% | Expectancy | PF | Return | Sharpe | MaxDD |
|---|---|---|---|---|---|---|
| Momentum (current live) | 49.6 | −0.09R | 0.80 | −21.2% | −0.77 | 26.3% |
| Inverted (mean-reversion) | 53.5 | +0.074R | 1.20 | +24.4% | 0.68 | 10.2% |
| **Inverted + let winners run (2R)** | 49.0 | **+0.143R** | 1.31 | **+43.2%** | **1.06** | 9.7% |
| _(gross)_ | 50.5 | +0.20R | 1.47 | +66.9% | 1.48 | 9.2% |

**The mean-reversion edge SURVIVES 5 years / multiple regimes** — the direction is real,
not a 12-month artifact. Momentum (live) is a structural loser (−21%, Sharpe −0.77).

5y factor IC (37.5k obs): proximity_52w −0.050, total_score −0.037 (still inverted but
weaker than the −0.127/−0.078 of the recent window); **volume_zscore +0.034 @ 21d — the
only positive, horizon-strengthening factor → a promising momentum-of-participation lead.**

### CAVEAT — survivorship bias (important)

> **Update (2026-08-28):** `proactive_backtest.py` previously read the static
> `NIFTY50` list and never consulted `index_membership` at all, so seeding the table
> would not have changed these numbers. It now resolves the roster per simulated day
> when the table is seeded, falls back with a warning when it isn't, and refuses to
> silently truncate a window that starts before the seeded coverage. The figures below
> were produced *before* that change and are unrevised — re-running them against a
> membership history extended back to 2021 is the open task.

The 5y test used TODAY's NIFTY-50 (`index_membership` still not seeded). Backtesting
today's index members over 2021–26 flatters mean-reversion (today's members all survived /
recovered). **Treat the +43% magnitude as optimistic; the SIGN and direction are solid.**
De-bias by seeding point-in-time membership before trusting the magnitude.

## Recommended next steps (in order)
1. **Lock in** the let-winners-run exit fix (trail arms at +2R) regardless — strict improvement.
2. **Backfill ~5y daily candles** (Kite historical / yfinance `.NS`) + seed `index_membership`.
3. **Re-run the IC study + inverted backtest across 5y / multiple regimes.** Only trust the
   mean-reversion edge if it survives.
4. If it survives: tune the oversold trigger (RSI<30 / % below 20-DMA), reduce trade
   frequency to cut cost drag, tune hold to ~21d, target +0.2R net over ≥30 trades.
5. **Do not deploy live until a variant clears +0.2R after costs out-of-sample.** It's paper —
   no real money at risk — so there is no rush to go live, only a need to find real edge.
