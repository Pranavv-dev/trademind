# TradeMind — Architecture & Implementation Status

**Last updated:** 2026-05-30 (Phase B landed)

This document describes how TradeMind is currently put together, which pieces are **shipped**, which are **partially done**, and which are **planned but not yet built**. The goal is to give a single accurate reference for the system's state.

For the **forensic loss diagnosis** see `LOSS_DIAGNOSIS_AND_ROADMAP.md`. For the **strategy research and adoption plan** see `STRATEGY_RESEARCH.md`. For the **file-by-file navigation map** see `CODE_MAP.md`.

---

## 1. System overview

TradeMind is an AI-powered paper-trading system for Indian markets (NSE/BSE) running on top of Zerodha Kite Connect. It is built around an **agent model** — each agent generates trade signals from a specific data source/strategy, signals are filtered through a risk manager, and trades are executed via a paper broker (or live Kite broker).

### High-level data flow

```
                      ┌─────────────────────────────────────────────┐
                      │  EXTERNAL SOURCES                            │
                      │  • Kite Connect REST (quotes, instruments)   │
                      │  • Kite Connect WebSocket (live ticks)       │
                      │  • NSE RSS / Indian financial news feeds     │
                      │  • Gemini 2.5 Flash Lite (LLM reasoning)     │
                      └─────────────────────────────────────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              ▼                          ▼                          ▼
        ┌──────────┐             ┌───────────────┐         ┌─────────────────┐
        │  REDIS   │             │  POSTGRES     │         │  CELERY BEAT    │
        │  (cache) │             │  (state)      │         │  (scheduler)    │
        │  quotes  │             │  agents       │         │  market-scan    │
        │  cooldown│             │  positions    │         │  intraday-scan  │
        │  tokens  │             │  trades       │         │  position-mon   │
        │  signals │             │  candles      │         │  data-sync      │
        └──────────┘             └───────────────┘         └─────────────────┘
                                         │
                                         ▼
                          ┌──────────────────────────────┐
                          │  AGENTS (signal generation)  │
                          ├──────────────────────────────┤
                          │  Proactive   (primary)       │
                          │  Technical   (confirmer)     │
                          │  Sentiment   (confirmer)     │
                          │  Reasoning   (LLM validator) │
                          │  Intraday-Tech (own pipeline)│
                          └──────────────────────────────┘
                                         │
                                         ▼
                          ┌──────────────────────────────┐
                          │  ORCHESTRATOR                │
                          │  • Build watchlist           │
                          │  • Apply confirm/veto        │
                          │  • Combine signals           │
                          └──────────────────────────────┘
                                         │
                                         ▼
                          ┌──────────────────────────────┐
                          │  RISK MANAGER                │
                          │  • min confidence            │
                          │  • duplicate positions       │
                          │  • position size %           │
                          │  • sector exposure 30%       │
                          │  • daily loss limit 3%       │
                          │  • cooldown (until 9:15 IST) │
                          │  • drawdown circuit breaker  │
                          │  • order rate (SEBI 10/sec)  │
                          └──────────────────────────────┘
                                         │
                                         ▼
                          ┌──────────────────────────────┐
                          │  EXECUTION ENGINE            │
                          │  • Paper broker (default)    │
                          │  • Kite broker (live mode)   │
                          │  • Order + position tracking │
                          │  • Charges simulation        │
                          └──────────────────────────────┘
                                         │
                                         ▼
                                   ┌─────────┐
                                   │ TRADES  │
                                   │  (DB)   │
                                   └─────────┘
```

### Process layout (Docker)

| Container | Role |
|---|---|
| `backend` | FastAPI server, Kite WebSocket ticker, tick aggregator, API routes |
| `frontend` | Next.js dashboard, served at localhost:3000 |
| `celery-beat` | Cron-style scheduler for periodic tasks |
| `celery-worker` | Executes scheduled and manually-triggered tasks |
| `db` | PostgreSQL 17 |
| `redis` | Redis 7 — cache + Celery broker |

---

## 2. Agents

Five strategy types exist today. The first three originated as independent signal generators; the architecture was recently restructured so **`proactive` is the primary originator** and `technical` + `sentiment` act as confirmers.

### 2.1 ProactiveAgent (`strategy_type: proactive`) ✅ shipped

**Role:** Primary signal originator.
**Inputs (via `ContextScorer`):**

| Signal | Source | Predictive of |
|---|---|---|
| Relative Strength (20d) | D1 candles vs synthetic NIFTY mean | Continuing outperformance |
| Sector momentum | Avg 20d return per sector vs NIFTY | Sector rotation legs |
| 52-week proximity | D1 high/low | Breakout setups |
| Volume Z-score | (today vol − 20d mean) / std | Institutional accumulation |
| Tightness (ATR/price) | 14-day ATR | Coiled-spring consolidation |

Each stock gets a composite score 0–100. Watchlist threshold = 60. Signal threshold = 65. Entry requires `LTP > today's open` (intraday confirmation).

**File:** `backend/app/agents/proactive.py`, `backend/app/data/context_scorer.py`

### 2.2 TechnicalAgent (`strategy_type: technical`) ✅ shipped, now confirmer-only

**Role:** Indicator-based voting; in the new pipeline its signals **confirm or veto** Proactive signals on the same symbol.

**Indicators (each casts +1/-1/0 vote with weights):**

| Indicator | Weight | Description |
|---|---:|---|
| MACD (12/26/9) | 2.0 | Trend momentum, crossover-aware |
| SMA 50/200 cross | 2.0 | Golden / death cross |
| SMA 20/50 cross | 1.5 | Short-term trend |
| RSI-14 | 1.5 | Oversold <40, overbought >60 |
| Bollinger Bands (20, 2σ) | 1.0 | Mean reversion at band touches |
| OBV divergence | 1.0 | Volume-price divergence |
| 52w proximity | 0.5 | Breakout/bounce candidate |
| VWAP | 0.5 | ⚠️ broken on daily candles (always votes 0) |

**Regime-aware weighting (ADX + ATR + SMA alignment):**

| Regime | Weight adjustments |
|---|---|
| trending_up | MACD/SMA boosted, RSI/BB reduced |
| trending_down | Same, plus suppress SELL (long-only); BUY requires 1+ oversold indicator |
| ranging | RSI/BB doubled, SMA zeroed |
| volatile | All halved |

**File:** `backend/app/agents/technical.py`

### 2.3 SentimentAgent (`strategy_type: sentiment`) ✅ shipped, very strict

**Role:** Keyword-based news sentiment; in the new pipeline confirms/vetoes Proactive signals.

**Sources (RSS):**
- Economic Times Markets
- Moneycontrol Market Reports
- LiveMint Markets
- NDTV Profit Latest

**Logic:** 64 headlines/cycle. For each stock, find matching headlines via the `COMPANY_ALIASES` dict. Count bullish vs bearish keywords. Fire BUY/SELL if `>65%` one-sided.

**Current config (tightened 2026-05-13 after the WIPRO/SBIN losses):**
- `min_headlines: 4` (was 2)
- `min_confidence: 0.70` (was 0.55) — note: keyword-only confidence is capped at 0.70, so this essentially requires unanimously bullish/bearish coverage
- `sl_pct: 0.03`, `tp_ratio: 2.0`

Gemini integration was previously inside this agent and was removed for cost reasons.

**File:** `backend/app/agents/sentiment.py`

### 2.4 ReasoningAgent (`strategy_type: reasoning`) ✅ shipped, legacy path only

**Role:** Gemini-based second opinion on borderline signals. Currently only used in the **legacy code path** (when no proactive agent is active). In the proactive-first pipeline, this agent is bypassed.

**Model:** `gemini-2.5-flash-lite`

**Confidence gate (in orchestrator `_validate_with_reasoning`):**
- Signals with confidence ≥ 0.60 AND `votes` in metadata (technical-backed) → skip Gemini
- Signals with confidence ≥ 0.65 AND headlines_count ≥ 2 → skip Gemini
- Signals with confidence < 0.50 → drop
- Else → send to Gemini

**File:** `backend/app/agents/reasoning.py`

### 2.5 IntradayTechnicalAgent (`strategy_type: intraday_technical`) ✅ shipped, runs separately

**Role:** Multi-timeframe (15m regime / 5m votes) version of TechnicalAgent. Runs in its **own scan pipeline** every 5 minutes — completely isolated from the daily proactive pipeline. No ensemble, no reasoning validation.

**Data dependency:** 5m + 15m candles built live from Kite WebSocket tick stream by `TickBarAggregator` (no historical intraday API available on user's plan).

**Warmup:** 50× 5m bars (≈4 hours session time) required minimum. 15m regime is used once 50× 15m bars accumulate (~Day 2 afternoon); before that falls back to 5m-derived regime.

**Files:**
- `backend/app/agents/intraday_technical.py`
- `backend/app/data/aggregator.py`
- `backend/app/tasks/intraday_scanner.py`

---

## 3. Pipeline modes

The orchestrator runs in **one of two modes** depending on whether a Proactive agent is active.

### 3.1 Proactive-first mode (default since 2026-05-13)

```
1. Build ContextScorer watchlist (D1 candles → composite scores)
2. Inject watchlist into each ProactiveAgent
3. Run all agents concurrently on snapshots
4. Filter raw signals:
   • Keep only proactive originators
   • For each, look up technical/sentiment signals on same symbol:
     - Same action (BUY confirms BUY) → boost confidence (×1.15 tech, ×1.10 sent)
     - Opposite action (SELL vetoes BUY) → drop signal
     - No signal → neutral
5. Pass final signals through risk manager → execution
```

Log marker: `scan_cycle_proactive raw_signals=N final_signals=M`

### 3.2 Legacy ensemble mode

Used only if no `proactive` agent is registered (e.g. paused or removed).

```
1. Run all agents
2. Combine via EnsembleAgent (weighted average per symbol)
3. Validate borderline signals via Gemini
4. Pass to risk manager
```

Log marker: `scan_cycle_legacy raw_signals=N final_signals=M`

---

## 4. Risk manager

Every signal — whether from proactive, intraday, or legacy ensemble — flows through `RiskManager.evaluate()` which applies **all** of the following gates (a single failed check rejects the trade):

| Check | Limit |
|---|---|
| Minimum confidence | 0.60 |
| Stop-loss required | yes |
| Circuit breaker (max drawdown) | 15% from equity peak |
| NIFTY regime filter (NEW 2026-05-30) | risk_off_strong → block BUYs; risk_off → 0.5× size |
| Duplicate position (cross-agent) | only one OPEN BUY position per symbol across all agents |
| Per-agent symbol cooldown (fail-closed) | After any close, the agent cannot re-buy that symbol until next 9:15 IST |
| Position size | ≤ **7%** of agent capital (tightened from 10%) |
| Portfolio gross exposure (NEW 2026-05-30) | ≤ **70%** of total capital across all open positions |
| Single order value | ≤ ₹200,000 |
| Daily loss limit | ≤ 3% of total capital |
| Max open positions per agent | **7** (tightened from 10) |
| Sector exposure | ≤ 30% of total capital |
| Order rate | ≤ 10/sec (SEBI) |

**Position sizing (Phase B):**

The risk manager now uses **R-based position sizing** when the signal carries a usable SL distance: `quantity = (capital × risk_per_trade_pct / 100) / |entry − stop|`, default `risk_per_trade_pct = 0.5%`. This automatically gives volatile names smaller positions and calm names larger ones at the same dollar risk, fixing the "equal-rupee flat sizing" anti-pattern. A notional ceiling (`max_position_size_pct = 7%`) still applies as a safety floor for stocks with tiny SLs. Falls back to fixed-percentage sizing when SL is unusable.

**Notes:**
- **Cooldown** (added 2026-05-12) is now **fail-closed** (2026-05-30): if Redis is unreachable, treats as in-cooldown rather than silently bypassing.
- **Cooldown set on ANY close** (2026-05-30): partial closes also trigger cooldown; set BEFORE commit with rollback on Redis failure.
- **Duplicate check** is cross-agent (not per-agent): if any agent holds RELIANCE, no other agent can open RELIANCE.

**File:** `backend/app/risk/manager.py`, `backend/app/risk/limits.py`, `backend/app/risk/position_sizer.py`

---

## 5. Execution

`ExecutionEngine.execute(proposal)`:

1. Create order record (status `pending`)
2. Submit to broker (`PaperBroker` in paper mode, `KiteBroker` in live)
3. On fill: update order, create/update position
4. **Capture opening-leg charges** on the position itself (`positions.entry_charges` column, NEW 2026-05-30)
5. **Initialize `highest_price`** for the trailing-stop ratchet (NEW 2026-05-30)
6. On close: deduct BOTH entry+exit charges (pro-rated for partial closes) → honest realized P&L
7. On full close: set `closed_at`, write per-agent cooldown to Redis, write `signal_outcomes` row
8. Charges (STT, brokerage, GST, exchange, stamp duty, SEBI) calculated by `charges.py`

**Realistic slippage (NEW 2026-05-30):** PaperBroker now applies a square-root market-impact slippage model `slippage_bps = c × σ × √(Q / ADV)` (`c=0.5`, `σ` and ADV from ContextScorer). Without this, paper P&L mysteriously tracked mid-price exactly while live would trail by 10–30 bps/trade. Disable via `PAPER_SLIPPAGE_DISABLED=1` for tests.

**Files:** `backend/app/execution/interface.py`, `backend/app/execution/paper.py`, `backend/app/execution/charges.py`, `backend/app/execution/slippage.py`

---

## 6. Schedulers

All scheduled tasks run via Celery Beat. Crontab times are IST.

| Task | Schedule | Purpose |
|---|---|---|
| `pre-market` | 8:30 AM, Mon–Fri | Auth check, Telegram alert if Kite token expired |
| `market-scan` | every 15 min, 9–15 IST, Mon–Fri | Main scan cycle (proactive-first) — now writes `market:nifty_bias` to Redis |
| `intraday-scan` | every 5 min, 9–15 IST, Mon–Fri | Intraday-only agent pipeline |
| `position-monitor` | every 5 min, 9–15 IST, Mon–Fri | SL/TP/trailing/max-hold exit enforcement |
| `eod-report` | 3:45 PM, Mon–Fri | End-of-day summary (Telegram) |
| **`expectancy-snapshot`** *(NEW)* | 4:00 PM, Mon–Fri | Roll up signal_outcomes into 30/60/90-day R-multiple stats → Redis |
| `data-sync` | 4:30 PM, Mon–Fri | Sync D1 candles from Kite for next day's signals |
| `health-check` | every 10 min | Check DB/Redis/broker connectivity |

**File:** `backend/app/tasks/celery_app.py`

---

## 7. Database schema (key tables)

| Table | Purpose |
|---|---|
| `agents` | Agent definitions: name, strategy_type, config, status, market, universe, capital |
| `positions` | Open and closed positions (cross-agent, keyed by id). Unique partial index on `(agent_id, symbol, exchange, side) WHERE closed_at IS NULL` |
| `trades` | Order history (every BUY/SELL with fill price, qty, charges, etc.) |
| `candles` | OHLCV bars, composite PK `(symbol, exchange, timeframe, time)`. Stores `1d` (synced EOD), `5m` and `15m` (built live from ticks) |
| `signals` | Generated signal log (optional, for backtesting analysis) |

---

## 8. WebSocket / live data

The Kite ticker is connected on backend startup if a valid token is in Redis, OR after a user completes Kite auth via `/api/auth/kite/callback`.

**On connect:** subscribes to all NIFTY 50 instrument tokens in `MODE_FULL`.

**Tick callback (`TickBarAggregator.on_ticks`):**
1. Resolves symbol via instrument master
2. Per (symbol, timeframe), maintains in-memory bar state (OHLCV + cumulative volume baseline)
3. On bar boundary (every 5m / 15m, IST-aligned to 9:15 open), flushes completed bar to `candles` table

**File:** `backend/app/data/feeds/kite_ws.py`, `backend/app/data/aggregator.py`

---

## 9. Implementation status

### ✅ Shipped

| Feature | Notes |
|---|---|
| Agent framework + lifecycle (start/stop, scan loop) | |
| TechnicalAgent with 8 weighted indicators + regime awareness | |
| SentimentAgent (RSS + keyword scoring) | Cap fix 2026-05-30 (was deadzone) |
| ReasoningAgent (Gemini integration) — legacy path only | |
| Proactive-first pipeline + ContextScorer + ProactiveAgent | NEW: 2026-05-13 |
| **ATR-aware SL on ProactiveAgent** (k × tightness, floor 4%, cap 8%) | **NEW: 2026-05-30** |
| **R-based position sizing** (0.5% capital risk per trade, max-notional safety floor) | **NEW: 2026-05-30** |
| **Trailing stop with `Position.highest_price` ratchet** (4% trail, activates after +1R) | **NEW: 2026-05-30** |
| **Entry-leg charges in realized P&L** (`Position.entry_charges` column) | **NEW: 2026-05-30** |
| **NSE-realistic slippage in PaperBroker** (√-impact model) | **NEW: 2026-05-30** |
| **`signal_outcomes` learning table + `/api/signal-performance`** | **NEW: 2026-05-30** |
| **Walk-forward backtest harness + PurgedKFold CV + walk_forward_windows** | **NEW: 2026-05-30** |
| **Point-in-time `index_membership` table** (NIFTY 50 historical roster) | **NEW: 2026-05-30** |
| **Nightly expectancy snapshot Celery job** (30/60/90-day rolling R-multiples) | **NEW: 2026-05-30** |
| **NIFTY market-regime filter** (`market:nifty_bias` Redis → risk manager) | **NEW: 2026-05-30** |
| **Portfolio gross-exposure cap** (70%) + tightened per-position 7% × 7 positions | **NEW: 2026-05-30** |
| IntradayTechnicalAgent + tick-to-bar aggregator (5m + 15m) | NEW: 2026-04-19 |
| Risk manager with **13** gates | Expanded 2026-05-30 |
| Per-agent symbol cooldown (now fail-closed, set on any close) | Hardened 2026-05-30 |
| Cross-agent duplicate position check | Fixed: 2026-04-10 |
| Partial unique index on positions (`closed_at IS NULL`) | Fixed: 2026-04-10 |
| ExecutionEngine: paper + Kite brokers, charges simulation | |
| Position monitor (5-min SL/TP/trailing/max-hold enforcement) | Max-hold extended 2026-05-30 |
| Daily candle sync via Kite Historical API (D1 only) | |
| Celery scheduler for all periodic tasks | + expectancy-snapshot 2026-05-30 |
| Dashboard (Next.js): agents, trades, dashboard, settings, backtest | |
| Telegram bot notifications (auth alerts, trade events, EOD) | If configured |
| Dynamic NSE holiday detection (API + Redis cache + hardcoded fallback) | Fixed: 2026-04-10 |
| Kite token TTL (auto-expires at 11:55 PM IST) | Fixed: 2026-04-10 |

### 🟡 Partial / known issues

| Item | Status |
|---|---|
| VWAP indicator on daily candles | Silently votes 0 because `ta.vwap` requires DatetimeIndex; works on intraday once we set the index — not yet wired |
| Cross-agent dedup vs intraday strategy | Intraday agent cannot trade a symbol that any other agent holds (by-design but reduces intraday opportunity set) |
| Stock split / corporate action handling | None. A split would make old SL look reasonable while price has halved → false SL hits |
| Tick-out-of-order handling in aggregator | Only forward-rolls bars; replayed ticks could mutate current bar with stale close |
| First-bar volume after backend restart | Partial — only counts ticks observed after startup |
| Aggregator opens a new DB session per bar flush | ~50 short-lived sessions at 5-min boundaries; wasteful but not broken |

### 🔴 Planned but not yet built (post-Phase-B roadmap)

See `STRATEGY_RESEARCH.md` for the verified, ranked adoption plan (15 patterns, 200 research sources).

**Phase C — Regime & exposure refinement (~22h):**
- India VIX percentile-bucketed conviction sizing (upgrade from %-change to percentile + breadth) [Pattern #4]
- Portfolio "heat" cap (stop-distance weighted, not just notional) + sector sub-cap (max 2 per sector) [Pattern #5]
- Order-type discipline: LIMIT-first entries, MARKET only on exits + session-time gates (no entries 9:15-9:30 or 15:15-15:30) [Pattern #11]

**Phase D — India-specific alpha signals (~24h):**
- NSE bhavcopy CSV daily download → DELIV_PER Z-score as 6th ContextScorer feature [Pattern #7]
- NSE FII/DII rolling-flow as portfolio-level conviction multiplier [Pattern #8]
- Minervini SEPA Trend Template as graded eligibility gate (not hard 6/6) [Pattern #12]

**Phase E — Intraday strategy + tail-risk (~32h):**
- 30-min Opening Range Breakout filtered by ContextScorer top-N [Pattern #6]
- Event blackout calendar (RBI / FOMC / CPI / earnings) + F&O ban veto [Pattern #15]
- Dead-money exit at N bars without +0.5R progress [Pattern #9]
- Fix VWAP for intraday agent (set time as DatetimeIndex)

**Phase F — ML foundation (~46h):**
- FinBERT-as-feature replacing keyword SentimentAgent (NOT as primary signal) [Pattern #14]
- Triple-barrier labeling for the learning loop [Pattern #13]
- Demote Gemini ReasoningAgent to UI-only (no order alteration)

**Production-readiness blockers (parallel track, ~30h, see LOSS_DIAGNOSIS_AND_ROADMAP.md §4.1):**
- API authentication (all 29 endpoints currently unauthenticated)
- TRADING_MODE=live safeguards (one-time confirmation token, emergency-stop endpoint)
- Database backups (daily pg_dump → S3)
- Redis persistence (RDB + AOF; otherwise cooldowns and circuit-breaker state are wiped on restart)

**Operational/correctness (~57h):**
- Real health checks (currently a stub returning hardcoded `true`)
- Telegram alerts mandatory in live mode
- Docker restart policies
- Kite WebSocket staleness detection
- Order placement idempotency (UUID idempotency keys for live retry safety)
- Position reconciliation (DB vs Kite holdings)
- Integration test suite

**Known partial / pre-existing issues:**
- VWAP indicator on daily candles (still votes 0; intraday fix in Phase E)
- Stock split / corporate action handling — none. A split would make old SL look reasonable while price has halved → false SL hits
- Tick-out-of-order handling in aggregator
- First-bar volume after backend restart (only counts ticks observed after startup)

---

## 10. Active agents (as of 2026-05-13 16:00 IST)

| Name | Strategy | Status | Capital | Role |
|---|---|---|---:|---|
| Proactive-NIFTY50 | proactive | active | ₹100,000 | Primary originator |
| Test-tech | technical | active | (per DB) | Confirmer only |
| Sentiment-NIFTY50 | sentiment | active | (per DB) | Confirmer only (strict filter) |
| Gemini-Reasoning | reasoning | (per DB) | — | Legacy-path validator |
| Intraday-Tech-NIFTY50 | intraday_technical | active | ₹100,000 | Independent intraday pipeline |

---

## 11. Configuration reference

### Risk limits (`app/risk/limits.py`) — Phase B values
```
min_confidence:           0.60
max_position_size_pct:    7.0          # tightened from 10.0 (2026-05-30)
max_open_positions:       7            # tightened from 10
max_gross_exposure_pct:   70.0         # NEW (2026-05-30) — portfolio-level cap
max_daily_loss_pct:       3.0
max_drawdown_pct:         15.0
max_sector_exposure_pct:  30.0
max_order_rate_per_sec:   10           # SEBI
max_single_order_value:   ₹200,000
require_stop_loss:        true
```

### ProactiveAgent defaults (`app/agents/proactive.py`) — Phase B values
```
watchlist_threshold:       60.0
signal_threshold:          65.0
max_watchlist:             10
# SL is ATR-aware: k × tightness clamped to [floor, cap]
atr_sl_multiplier:         2.0          # used when ContextScorer doesn't override k
sl_floor_pct:              0.04         # never tighter than 4%
sl_cap_pct:                0.08         # never wider than 8%
sl_pct:                    0.05         # fixed fallback when tightness missing
tp_ratio:                  2.5
risk_per_trade_pct:        0.5          # NEW (2026-05-30) — for R-based sizing
require_open_confirmation: true
```

### SentimentAgent defaults (`app/agents/sentiment.py`)
```
min_headlines:    4
min_confidence:   0.55          # FIXED (2026-05-30) — was 0.70 which was a deadzone
sl_pct:           0.05          # widened from 0.03 on 2026-05-30
tp_ratio:         2.0
sources:          all RSS feeds
```

### IntradayTechnicalAgent defaults (`app/agents/intraday_technical.py`)
```
signal_threshold:    0.20
atr_sl_multiplier:   2.0
tp_ratio:            2.0
long_only:           true
weights:             same as daily TechnicalAgent
MIN_BARS_5M:         50    # ~4hr of session data
MIN_BARS_15M_FOR_REGIME: 50  # falls back to 5m-derived regime below this
```

### Slippage model (`app/execution/slippage.py`) — NEW Phase B
```
DEFAULT_IMPACT_C = 0.5        # √-impact coefficient
MIN_SLIPPAGE_BPS = 1.0        # half-spread floor
MAX_SLIPPAGE_BPS = 100.0      # safety cap
PaperBroker default σ:   2.0% daily
PaperBroker default ADV: 5,000,000 shares
PAPER_SLIPPAGE_DISABLED env var: set to "1" to disable in tests
```

### Backtest harness (`app/backtest/cv.py`, `walk_forward.py`) — NEW Phase B
```
PurgedKFold:
  n_splits:           5
  label_horizon_days: 10        # ≥ max strategy holding period
  embargo_days:       5         # extra purge after each fold

walk_forward_windows defaults:
  train_months: 12
  test_months:  3
  step_months:  1
  → ~52 windows over a 5-year backtest
```

---

## 12. Key recent fixes (incident log)

| Date | Fix |
|---|---|
| 2026-04-10 | Cross-agent dedup added (stacking bug producing 4× repeat buys) |
| 2026-04-10 | Partial unique index on positions allows closed positions to be re-opened |
| 2026-04-10 | Dynamic NSE holiday detection (was hardcoded 2026 list missing entries) |
| 2026-04-10 | Kite token TTL = until 11:55 PM IST (prevents stale-token next-day issues) |
| 2026-04-19 | Intraday strategy + tick-to-bar aggregator |
| 2026-04-19 | Fix `await ticker.subscribe()` race in auth callback (was silent no-op) |
| 2026-05-12 | Per-agent symbol cooldown after position close (fixes death-spiral) |
| 2026-05-13 | Proactive-first restructure: ContextScorer + ProactiveAgent primary, others confirm |
| 2026-05-13 | Sentiment tightened to 4 headlines / 0.70 confidence (later revealed mathematical deadzone) |
| 2026-05-20 | max_holding_days enforcement at EOD (sentiment 15d, proactive 21d, technical 30d, intraday 1d) |
| **2026-05-30** | **Phase A — 10 Week-1 fixes (LOSS_DIAGNOSIS_AND_ROADMAP.md):** entry-leg charges in realized P&L, sentiment cap fix (0.70 → 0.55), ATR-aware SL on ProactiveAgent, trailing stop with `Position.highest_price` ratchet, cooldown leaks patched (partial closes + fail-closed), max-hold every cycle, NIFTY-regime filter (`market:nifty_bias`), gross-exposure cap 70% + 7% × 7 positions, `signal_outcomes` learning table + `/api/signal-performance`, proactive-veto position-state-blindness fix |
| **2026-05-30** | **Phase B — Backtest hygiene + cost realism + R-multiple sizing (STRATEGY_RESEARCH.md):** R-based position sizing in RiskManager (0.5% capital per trade); NSE-realistic √-impact slippage model in PaperBroker; ContextScorer emits realized_vol + ADV + regime-bucketed ATR multiplier; `index_membership` table for point-in-time NIFTY 50 universe; PurgedKFold + walk-forward CV utilities (`backtest/cv.py`); walk-forward backtest orchestrator (`backtest/walk_forward.py`); expectancy + R-multiple metrics (`backtest/expectancy.py`); nightly expectancy-snapshot Celery job → Redis; `/api/backtest/walk-forward` endpoint; `/api/signal-performance/expectancy` endpoint; NIFTY 50 reconstitution seed script (`scripts/seed_nifty50_membership.py`) |

---

## 13. Phase B components — Backtest hygiene & R-multiple foundation (2026-05-30)

The plumbing that lets every future strategy change be data-driven rather than guesswork.

### 13.1 R-based position sizing

`risk/manager.py` now prefers `r_based_size()` whenever a usable SL is set on the signal:

```
quantity = (capital × risk_per_trade_pct / 100) / |entry − stop|
```

with a notional ceiling (`max_position_size_pct = 7%`) as a safety floor for stocks with tiny stops. ContextScorer attaches a per-symbol regime-bucketed `atr_multiplier` (k=1.2 in trends, 1.5 default, 2.0 in chop) which feeds into the SL distance, so position size auto-adjusts to volatility. NIFTY-regime scaling (`risk_off` → 0.5×) applies multiplicatively.

**File:** `backend/app/risk/position_sizer.py::r_based_size`

### 13.2 Slippage model

`execution/slippage.py` implements the square-root market impact model:

```
slippage_bps = c × σ × sqrt(Q / ADV)
```

PaperBroker applies this on every fill — BUYs fill higher, SELLs fill lower — so paper P&L tracks what live execution would actually deliver. ContextScorer outputs realized_vol_pct (daily) and adv_20 (20-day average daily volume) which ExecutionEngine forwards to the broker via signal metadata.

**Files:** `backend/app/execution/slippage.py`, `backend/app/execution/paper.py`, `backend/app/data/context_scorer.py`

### 13.3 Point-in-time index membership

`index_membership` table captures `(index_name, symbol, from_date, to_date)`. The walk-forward backtest queries it with an `as_of_date` filter so historical signals see the universe that existed at that time — not today's already-survived-the-cull roster. Without this, NIFTY backtests overstate returns by ~9% annualized.

Seed: `scripts/seed_nifty50_membership.py` — populates known reconstitutions since 2023.

**Files:** `backend/app/db/models/index_membership.py`, `backend/app/db/repositories/index_membership_repo.py`, `backend/alembic/versions/a3f5c8d9b2e1_phase_b_index_membership.py`

### 13.4 Backtest CV primitives

`backtest/cv.py` provides:

- `purged_kfold(dates, n_splits, label_horizon_days, embargo_days)` — Lopez de Prado's purged k-fold for path-dependent labels (default n=5, horizon=10, embargo=5)
- `walk_forward_windows(start, end, train_months, test_months, step_months)` — Pardo-style walk-forward (defaults 12mo train / 3mo test / 1mo step)
- `deflated_sharpe_threshold(n_strategies_tested, confidence)` — Bailey-Lopez de Prado multiple-testing correction

### 13.5 Walk-forward orchestrator

`backtest/walk_forward.py::WalkForwardBacktest` runs the existing single-symbol `BacktestEngine` across (windows × point-in-time universe) and aggregates results. Exposed at `POST /api/backtest/walk-forward`.

The result includes per-window metrics so users can spot performance instability across regimes — the key safeguard against curve-fitting.

### 13.6 Expectancy + R-multiple metrics

`backtest/expectancy.py` computes per-agent (and system-wide) statistics from the `signal_outcomes` table:

```
hit_rate         = wins / trades
avg_win_r        = mean(R-multiples of winners)
avg_loss_r       = mean(R-multiples of losers)
payoff_ratio     = avg_win_r / |avg_loss_r|
profit_factor    = sum(net_pnl > 0) / |sum(net_pnl < 0)|
expectancy_r     = hit_rate × avg_win_r + (1 − hit_rate) × avg_loss_r
deployable       = expectancy_r ≥ 0.2  AND  trades ≥ 30
```

These are the only numbers that honestly say whether the system has edge. A rolling 30/60/90-day snapshot is computed by `expectancy-snapshot` Celery task at 4:00 PM IST and cached to Redis (`expectancy:snapshot` key). The dashboard reads from cache, falling back to live query if missing.

Exposed at `GET /api/signal-performance/expectancy?days={30,60,90}&use_cache={true,false}`.

**File:** `backend/app/backtest/expectancy.py`, `backend/app/tasks/expectancy_job.py`, `backend/app/api/routes/signal_performance.py`
