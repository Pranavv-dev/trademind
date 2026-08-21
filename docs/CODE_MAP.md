# TradeMind — Code Map & Architecture Diagrams

**Last updated:** 2026-05-20

This document is the **navigation map** for the codebase: where every file lives, what it does, and how the pieces wire together. For implementation status and roadmap, see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## 1. Top-level layout

```
trademind/
├── backend/             FastAPI app + Celery workers + Python tests
│   ├── app/             Application code (see §3)
│   ├── alembic/         DB migrations (Alembic for Postgres)
│   ├── tests/           pytest suite mirroring app/ structure
│   ├── Dockerfile       Backend container image (also used for celery)
│   └── pyproject.toml   Python deps (uv-managed)
├── frontend/            Next.js 14 dashboard
│   ├── src/app/         App-router pages
│   ├── src/components/  Shared + dashboard components
│   ├── src/hooks/       React hooks (useApi, useWebSocket)
│   └── src/lib/         API client, util helpers
├── docs/                Documentation
│   ├── ARCHITECTURE.md  Implementation status + roadmap
│   └── CODE_MAP.md      (this file)
├── scripts/             One-off ops scripts
├── docker-compose.yml   Multi-service stack (backend, frontend, db, redis, celery×2)
├── docker-compose.dev.yml  Dev overrides
└── Makefile             Common commands (up, restart, logs, etc.)
```

---

## 2. System architecture (services & data flow)

```
                           ┌───────────────────────────────────────────┐
                           │          EXTERNAL SOURCES                  │
                           │  Kite REST · Kite WebSocket · RSS · Gemini │
                           └───────────────────────────────────────────┘
                                              │
        ┌─────────────────────────────────────┴─────────────────────────────────┐
        ▼                                                                       ▼
┌───────────────┐   HTTP    ┌──────────────────────┐   tasks    ┌─────────────────────┐
│   FRONTEND    │◄─────────►│       BACKEND        │───────────►│   CELERY BEAT       │
│  (Next.js)    │   WS      │  (FastAPI + ticker   │            │  (cron scheduler)   │
│  localhost:   │◄─────────►│   + aggregator)      │            └──────────┬──────────┘
│     3000      │           └──────────┬───────────┘                       │
└───────────────┘                      │                                   ▼
                                       │                       ┌─────────────────────┐
                                       │                       │   CELERY WORKER     │
                                       │                       │  (task executor)    │
                                       │                       └──────────┬──────────┘
                                       │                                  │
                                       ▼                                  ▼
                              ┌────────────────┐                ┌─────────────────────┐
                              │     REDIS      │                │     POSTGRES        │
                              │   • quotes     │                │   • agents          │
                              │   • cooldown   │◄──────────────►│   • positions       │
                              │   • token      │                │   • trades          │
                              │   • signals    │                │   • candles         │
                              │   • broker     │                │   • daily_snapshots │
                              └────────────────┘                └─────────────────────┘
```

**Six docker services** (`docker-compose.yml`):

| Service | Image | Purpose |
|---|---|---|
| `backend` | trademind-backend | FastAPI (`uvicorn`), runs `app/main.py`. Hosts API, ticker, aggregator. |
| `frontend` | trademind-frontend | Next.js dev server on port 3000 |
| `celery-beat` | trademind-celery-beat | Cron-style scheduler — fires periodic tasks |
| `celery-worker` | trademind-celery-worker | Executes Celery tasks |
| `db` | postgres:17 | Application state |
| `redis` | redis:7 | Cache + Celery broker |

---

## 3. Backend layout (`backend/app/`)

```
app/
├── main.py                      FastAPI entrypoint, lifespan, ticker connect, aggregator wire
├── config.py                    Pydantic settings — env vars, defaults
├── dependencies.py              FastAPI DI: redis, db session
│
├── agents/                      Signal-generating agents (§4)
│   ├── base.py                  BaseAgent abstract class
│   ├── signals.py               Signal, MarketSnapshot, TradeProposal dataclasses
│   ├── proactive.py             ProactiveAgent — primary originator
│   ├── technical.py             TechnicalAgent — 8 indicators, regime-aware (now confirmer)
│   ├── sentiment.py             SentimentAgent — RSS+keywords (now confirmer, strict)
│   ├── intraday_technical.py    IntradayTechnicalAgent — multi-timeframe (5m+15m)
│   ├── reasoning.py             ReasoningAgent — Gemini second-opinion (legacy path)
│   ├── ensemble.py              EnsembleAgent — weighted vote (legacy path)
│   └── orchestrator.py          AgentOrchestrator — pipeline glue (proactive-first / legacy)
│
├── data/                        Market data & analytics
│   ├── universe.py              NIFTY50/BANKNIFTY symbols, SECTORS map
│   ├── indicators.py            RSI/MACD/BB/SMA/ADX/OBV/VWAP + regime classifier
│   ├── context_scorer.py        Proactive scoring (RS, sector, 52w, volume, tightness)
│   ├── aggregator.py            Tick → 5m/15m bar builder (writes to candles table)
│   ├── downloader.py            Kite historical_data wrapper (D1 only)
│   ├── cache.py                 PriceCache — Redis ops (quotes, cooldown, tokens, P&L)
│   └── feeds/
│       ├── kite_ws.py           KiteTickerManager — WebSocket tick stream
│       ├── instruments.py       InstrumentMaster — symbol↔token map
│       ├── news.py              NewsScraper — 4 RSS feeds + symbol matching
│       └── nse_api.py           NSE REST fallback for quotes/holidays
│
├── risk/                        Risk gates applied to every signal
│   ├── manager.py               RiskManager.evaluate — 11 gates incl. cooldown
│   ├── limits.py                RiskLimits dataclass + defaults
│   ├── position_sizer.py        fixed_percentage_size — qty calculation
│   ├── stop_loss.py             atr_stop, fixed_percentage_stop
│   └── drawdown.py              DrawdownMonitor — circuit breaker
│
├── execution/                   Order placement & position tracking
│   ├── interface.py             ExecutionEngine — proposal → fill → DB write
│   ├── order_manager.py         Order lifecycle records
│   ├── paper.py                 PaperBroker — simulates fills at cached LTP
│   ├── live.py                  Live broker dispatcher
│   ├── charges.py               STT, brokerage, GST simulation
│   └── broker/
│       ├── base.py              BaseBroker interface
│       └── kite.py              KiteBroker — Zerodha Kite client wrapper
│
├── db/                          Database layer
│   ├── session.py               async_session_factory, engine
│   ├── models/
│   │   ├── base.py              Declarative base, UUIDMixin, TimestampMixin
│   │   ├── agent.py             Agent (name, strategy_type, config, status, capital…)
│   │   ├── position.py          Position (open/closed, SL/TP, P&L)
│   │   ├── trade.py             Trade (order/fill ledger)
│   │   ├── candle.py            Candle (OHLCV, composite PK, multi-timeframe)
│   │   └── snapshot.py          DailySnapshot — EOD equity tracking
│   └── repositories/
│       ├── agent_repo.py        AgentRepository — CRUD + get_active
│       ├── candle_repo.py       CandleRepository — get/upsert candles
│       └── trade_repo.py        TradeRepository — query trades
│
├── api/                         FastAPI routes
│   ├── router.py                Top-level router aggregator
│   ├── schemas.py               Pydantic request/response models
│   ├── websocket.py             /ws/live — broadcast hub
│   └── routes/
│       ├── agents.py            /api/agents — CRUD, start/stop
│       ├── auth.py              /api/auth/kite — Kite OAuth callback + ticker connect
│       ├── trades.py            /api/trades — history
│       ├── dashboard.py         /api/dashboard — summary stats
│       ├── market.py            /api/market — quotes, instruments
│       ├── risk.py              /api/risk — limits inspection
│       └── backtest.py          /api/backtest — run + results
│
├── tasks/                       Celery tasks (scheduled work)
│   ├── celery_app.py            Celery instance + beat_schedule
│   ├── scanner.py               run_scan_cycle (15min) + run_pre_market (8:30 AM)
│   ├── intraday_scanner.py      run_intraday_scan (5min) — own pipeline
│   ├── position_monitor.py      check_positions (5min) — SL/TP/max-hold enforcement
│   ├── data_sync.py             sync_daily_candles (4:30 PM) — Kite D1 download
│   ├── eod.py                   run_eod_report (3:45 PM) — daily summary
│   ├── health.py                check_system_health (10min)
│   └── holidays.py              is_market_holiday + NSE holiday API fetch
│
├── notifications/               Outbound user messaging
│   ├── telegram.py              get_bot, notify_* helpers
│   └── commands.py              /commands handler (status, positions, etc.)
│
└── backtest/                    Strategy backtesting
    ├── engine.py                Backtest orchestrator
    ├── simulator.py             Replay engine
    └── metrics.py               Sharpe, max DD, win rate, etc.
```

---

## 4. Agent pipeline (proactive-first)

When a `proactive` agent is registered & active, the orchestrator runs in this mode. Otherwise it falls back to the legacy ensemble path.

```
┌────────────────────────────────────────────────────────────────────┐
│                    SCAN CYCLE (every 15 min)                       │
│                  app/tasks/scanner.py::run_scan_cycle               │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────┐
        │   1. PRE-FLIGHT                          │
        │   • Holiday check (NSE API + cache)      │
        │   • Live market check (NIFTY LTP)        │
        │   • Kite auth check (Redis token)        │
        └─────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────┐
        │   2. BUILD WATCHLIST                             │
        │   app/data/context_scorer.py                     │
        │   For each NIFTY50 symbol, compute:              │
        │     • RS score (vs synthetic NIFTY)              │
        │     • Sector momentum                            │
        │     • 52w high proximity                         │
        │     • Volume Z-score                             │
        │     • Tightness (ATR/price)                      │
        │   → context_score (0-100); ≥60 = watchlist        │
        └─────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────┐
        │   3. FAN-OUT (asyncio.gather)                    │
        │   For each active agent × each symbol:           │
        │     agent.analyze(snapshot) → Signal | None      │
        │                                                   │
        │   ProactiveAgent   — fires from watchlist        │
        │   TechnicalAgent   — indicator vote              │
        │   SentimentAgent   — keyword sentiment           │
        └─────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────┐
        │   4. APPLY PROACTIVE-FIRST FILTER                │
        │   orchestrator._apply_proactive_first            │
        │   Per proactive signal:                          │
        │     • Look up TechnicalAgent signal on symbol    │
        │       └ same action → ×1.15 confidence boost     │
        │       └ opposite action → VETO (drop signal)     │
        │     • Look up SentimentAgent signal on symbol    │
        │       └ same action → ×1.10 confidence boost     │
        │       └ opposite action → VETO                   │
        │   Non-proactive originators → discarded          │
        └─────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────┐
        │   5. RISK MANAGER (per signal)                   │
        │   app/risk/manager.py::RiskManager.evaluate      │
        │   11 gates; ANY failure = trade rejected         │
        │     • min_confidence ≥ 0.60                      │
        │     • cooldown not active (until 9:15 IST)       │
        │     • no duplicate position (cross-agent)        │
        │     • position size ≤ 10%                        │
        │     • sector exposure ≤ 30%                      │
        │     • daily loss ≤ 3% of capital                 │
        │     • max 10 open positions per agent            │
        │     • order rate ≤ 10/sec                        │
        │     • single order value ≤ ₹200k                 │
        │     • circuit breaker (drawdown < 15%)           │
        │     • stop-loss must be present                  │
        └─────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────┐
        │   6. EXECUTION                                   │
        │   app/execution/interface.py::ExecutionEngine    │
        │   → PaperBroker (default) or KiteBroker (live)   │
        │   → Update positions, write trades, set cooldown │
        │   → Telegram notification                        │
        └─────────────────────────────────────────────────┘
```

---

## 5. Intraday pipeline (parallel, every 5 min)

```
┌──────────────────────────────────────────────────────────────┐
│              app/tasks/intraday_scanner.py                    │
│              run_intraday_scan (every 5 min)                  │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────┐
    │ Live tick stream → TickBarAggregator             │
    │ (running inside backend FastAPI process)         │
    │ Builds 5m + 15m bars from KiteTicker callback    │
    │ Persists to `candles` table at bar close         │
    └─────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────┐
    │ Load intraday agents (strategy_type=             │
    │ "intraday_technical") + their NIFTY50 universe   │
    └─────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────┐
    │ For each symbol fetch:                           │
    │   • Quote from Redis cache                       │
    │   • 75 × 5m candles from DB                       │
    │   • 30 × 15m candles from DB                      │
    │ Build MarketSnapshot                             │
    └─────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────┐
    │ IntradayTechnicalAgent.analyze(snapshot)         │
    │ Uses SAME indicators + weights as TechnicalAgent │
    │ • 15m for regime (or 5m fallback while warming)  │
    │ • 5m for entry voting                            │
    │ Requires 50× 5m bars minimum (warmup ≈4 hrs)     │
    └─────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────┐
    │ Direct to RiskManager (no ensemble, no Gemini)   │
    │ → ExecutionEngine                                │
    └─────────────────────────────────────────────────┘
```

---

## 6. Position exit triggers

```
┌──────────────────────────────────────────────────────────────┐
│        app/tasks/position_monitor.py (every 5 min)            │
└──────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌──────────────┐    ┌──────────────────┐    ┌────────────────────────┐
│ Stop Loss    │    │ Take Profit       │    │ Max Holding Days       │
│ price ≤ SL   │    │ price ≥ TP        │    │ (EOD 15:00-15:25 IST   │
│ (any time)   │    │ (any time)        │    │  window only)          │
│              │    │                   │    │                        │
│              │    │                   │    │ sentiment: 15 days     │
│              │    │                   │    │ proactive: 21 days     │
│              │    │                   │    │ technical: 30 days     │
│              │    │                   │    │ intraday:   1 day      │
└──────────────┘    └──────────────────┘    └────────────────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
                  Create SELL signal (close)
                  → RiskManager (closes bypass cooldown)
                  → ExecutionEngine
                  → Set cooldown (until next 9:15 IST)
                  → Telegram notify (🔴 SL / 🟢 TP / ⏰ max-hold)
```

---

## 7. Celery beat schedule (`celery_app.py`)

All times are IST.

| Cron name | Schedule | Task | Purpose |
|---|---|---|---|
| `pre-market` | 8:30 AM Mon-Fri | `scanner.run_pre_market` | Auth check + Telegram alert |
| `market-scan` | every 15 min, 9-15 Mon-Fri | `scanner.run_scan_cycle` | Main proactive scan |
| `intraday-scan` | every 5 min, 9-15 Mon-Fri | `intraday_scanner.run_intraday_scan` | Intraday-only agents |
| `position-monitor` | every 5 min, 9-15 Mon-Fri | `position_monitor.check_positions` | SL/TP/max-hold exits |
| `eod-report` | 3:45 PM Mon-Fri | `eod.run_eod_report` | Daily summary |
| `data-sync` | 4:30 PM Mon-Fri | `data_sync.sync_daily_candles` | Kite D1 download |
| `health-check` | every 10 min | `health.check_system_health` | DB/Redis/broker checks |

---

## 8. Database schema (key tables)

```
┌─────────────────────┐         ┌─────────────────────┐
│       agents        │1───────∞│      positions      │
├─────────────────────┤         ├─────────────────────┤
│ id (PK)              │         │ id (PK)              │
│ name (unique)        │         │ agent_id (FK)        │
│ strategy_type        │         │ symbol               │
│ config (JSONB)       │         │ exchange             │
│ status               │         │ side                 │
│ market               │         │ quantity             │
│ universe (JSONB)     │         │ avg_price            │
│ risk_params (JSONB)  │         │ current_price        │
│ capital_allocated    │         │ unrealized_pnl       │
└─────────────────────┘         │ realized_pnl         │
         │                       │ stop_loss            │
         │1                      │ take_profit          │
         │                       │ is_paper             │
         │∞                      │ opened_at            │
┌─────────────────────┐          │ closed_at            │
│       trades        │          │ uq_open_position*    │
├─────────────────────┤          └─────────────────────┘
│ id (PK)              │
│ agent_id (FK)        │          ┌─────────────────────┐
│ order_id             │          │      candles        │
│ symbol, side, qty    │          ├─────────────────────┤
│ price, fill_price    │          │ symbol  (PK)         │
│ status               │          │ exchange (PK)        │
│ pnl                  │          │ timeframe (PK)       │
│ charges              │          │ time (PK)            │
│ created_at           │          │ open/high/low/close  │
│ filled_at            │          │ volume               │
└─────────────────────┘          └─────────────────────┘

* uq_open_position is a PARTIAL UNIQUE INDEX:
  (agent_id, symbol, exchange, side) WHERE closed_at IS NULL
```

`daily_snapshots` table tracks per-agent EOD equity for charting.

---

## 9. Frontend layout (`frontend/src/`)

```
src/
├── app/                         Next.js 14 app-router pages
│   ├── layout.tsx               Root layout (sidebar + content)
│   ├── page.tsx                 / — Dashboard
│   ├── trades/page.tsx          /trades — Trade history table
│   ├── agents/page.tsx          /agents — Agent list (CRUD)
│   ├── agents/[id]/page.tsx     /agents/{id} — Agent detail
│   ├── backtest/page.tsx        /backtest — Run backtests
│   └── settings/page.tsx        /settings — Risk config, Kite auth
│
├── components/
│   ├── dashboard/
│   │   ├── stat-cards.tsx       Total trades / P&L / Win rate
│   │   ├── equity-chart.tsx     P&L over time
│   │   ├── agent-table.tsx      Per-agent performance grid
│   │   ├── recent-trades.tsx    Last N trades feed
│   │   └── risk-panel.tsx       Risk limits + exposure bars
│   └── shared/
│       ├── sidebar.tsx          Left-nav (Dashboard/Agents/Trades/…)
│       ├── card.tsx, badge.tsx  Generic UI primitives
│       └── status-dot.tsx       Active/paused indicator
│
├── hooks/
│   ├── use-api.ts               Typed fetch wrapper around /api/*
│   └── use-websocket.ts         Subscribe to /ws/live for tick events
│
└── lib/
    ├── api.ts                   API client (typed routes)
    └── utils.ts                 cn(), formatters, etc.
```

---

## 10. File-by-file quick reference

### Agents (`backend/app/agents/`)

| File | Role | Originates trades? |
|---|---|---|
| `base.py` | `BaseAgent` ABC: `analyze()`, `get_config_schema()`, lifecycle | — |
| `signals.py` | Dataclasses: `Signal`, `MarketSnapshot`, `TradeProposal` | — |
| `proactive.py` | **Primary originator** — fires from ContextScorer watchlist | ✅ |
| `technical.py` | 8 weighted indicators + regime; now confirmer-only | confirmer |
| `sentiment.py` | RSS + keyword scoring; very strict (4 headlines, 0.70 conf) | confirmer |
| `intraday_technical.py` | Same indicators on 5m+15m timeframes; own pipeline | ✅ (intraday-scan only) |
| `reasoning.py` | Gemini second-opinion (legacy ensemble path only) | validator |
| `ensemble.py` | Weighted-vote combiner (legacy path) | combiner |
| `orchestrator.py` | Glue: builds watchlist, fans out, applies confirm/veto | — |

### Data (`backend/app/data/`)

| File | Role |
|---|---|
| `universe.py` | NIFTY50/BANKNIFTY symbol lists, SECTORS dict, helpers |
| `indicators.py` | Pure functions: RSI/MACD/BB/SMA/ATR/ADX/OBV/VWAP + `classify_regime` + `get_*_signal` voters |
| `context_scorer.py` | `ContextScorer.score_universe()` → per-stock 0-100 composite |
| `aggregator.py` | `TickBarAggregator` — kite tick stream → 5m+15m bars to DB |
| `downloader.py` | `DataDownloader` — Kite REST historical_data (D1 only) |
| `cache.py` | `PriceCache` — Redis ops for quotes, tokens, cooldown, P&L, signals |
| `feeds/kite_ws.py` | `KiteTickerManager` — WebSocket connection lifecycle |
| `feeds/instruments.py` | `InstrumentMaster` — symbol↔token bidirectional lookup |
| `feeds/news.py` | `NewsScraper` — 4 RSS feeds + alias-based symbol mention matcher |
| `feeds/nse_api.py` | `nse_client` — fallback quote/holiday APIs |

### Risk (`backend/app/risk/`)

| File | Role |
|---|---|
| `manager.py` | `RiskManager.evaluate(signal, capital, total)` — 11 gates |
| `limits.py` | `RiskLimits` dataclass with all default thresholds |
| `position_sizer.py` | `fixed_percentage_size(capital, price, pct)` |
| `stop_loss.py` | `atr_stop`, `fixed_percentage_stop` |
| `drawdown.py` | `DrawdownMonitor` — equity peak tracking, circuit breaker |

### Execution (`backend/app/execution/`)

| File | Role |
|---|---|
| `interface.py` | `ExecutionEngine.execute(proposal)` — full order lifecycle + cooldown set |
| `order_manager.py` | `OrderManager` — order record CRUD |
| `paper.py` | `PaperBroker` — simulated fills at cached LTP |
| `live.py` | Wires `KiteBroker` for live mode |
| `charges.py` | `calculate_charges` — STT, brokerage, GST, SEBI |
| `broker/base.py` | `BaseBroker` interface |
| `broker/kite.py` | `get_kite_broker()` — Zerodha KiteConnect singleton |

### DB (`backend/app/db/`)

| File | Role |
|---|---|
| `session.py` | `async_session_factory`, shared async engine (pool 20+10) |
| `models/base.py` | Declarative `Base`, `UUIDMixin`, `TimestampMixin` |
| `models/agent.py` | `Agent` ORM model |
| `models/position.py` | `Position` ORM with partial unique index |
| `models/trade.py` | `Trade` ORM model |
| `models/candle.py` | `Candle` ORM, composite PK |
| `models/snapshot.py` | `DailySnapshot` ORM |
| `repositories/agent_repo.py` | `AgentRepository.get_active`, etc. |
| `repositories/candle_repo.py` | `CandleRepository.get_candles`, `upsert_candles` |
| `repositories/trade_repo.py` | Trade queries |

### API (`backend/app/api/`)

| File | Routes |
|---|---|
| `router.py` | Combines all sub-routers under `/api` |
| `schemas.py` | Pydantic models incl. strategy_type regex |
| `websocket.py` | `/ws/live` — broadcasts tick events to UI |
| `routes/agents.py` | CRUD + start/stop endpoints |
| `routes/auth.py` | `/kite/login`, `/kite/callback`, `/kite/subscribe`, `/kite/status` |
| `routes/trades.py` | Trade history, today's summary |
| `routes/dashboard.py` | Equity, P&L, win rate aggregates |
| `routes/market.py` | Quote endpoints, instrument search |
| `routes/risk.py` | Inspect current risk gate state |
| `routes/backtest.py` | Run backtest, fetch results |

### Tasks (`backend/app/tasks/`)

| File | Tasks |
|---|---|
| `celery_app.py` | Celery instance, beat_schedule, module imports |
| `scanner.py` | `run_scan_cycle` (15m), `run_pre_market` (8:30 AM) |
| `intraday_scanner.py` | `run_intraday_scan` (5m) |
| `position_monitor.py` | `check_positions` (5m) — SL/TP/max-hold |
| `data_sync.py` | `sync_daily_candles` (4:30 PM EOD) |
| `eod.py` | `run_eod_report` (3:45 PM) |
| `health.py` | `check_system_health` (10m) |
| `holidays.py` | `is_market_holiday`, `is_market_holiday_async` (NSE API + cache + fallback) |

### Notifications (`backend/app/notifications/`)

| File | Role |
|---|---|
| `telegram.py` | `get_bot()` singleton, `notify_*` helpers, polling loop |
| `commands.py` | `/status`, `/positions`, etc. Telegram command handlers |

### Backtest (`backend/app/backtest/`)

| File | Role |
|---|---|
| `engine.py` | High-level backtest orchestration |
| `simulator.py` | Replays candles through agents |
| `metrics.py` | Sharpe, Sortino, max drawdown, win rate |

### Other root files

| File | Role |
|---|---|
| `app/main.py` | FastAPI app factory, lifespan (ticker connect, aggregator wire, telegram start) |
| `app/config.py` | `Settings` from env (database_url, redis_url, kite keys, gemini key, etc.) |
| `app/dependencies.py` | FastAPI DI: `get_redis`, `get_session`, `init_redis`, `close_redis` |
| `alembic/env.py` + `versions/` | DB migrations |
| `Dockerfile` | Builds backend image used by `backend`, `celery-worker`, `celery-beat` |
| `pyproject.toml` | `uv` deps incl. fastapi, sqlalchemy, kiteconnect, pandas-ta, celery, structlog, google-generativeai |

---

## 11. Key call paths (read these to understand the system)

| Scenario | Trace |
|---|---|
| **Daily scan creates a trade** | `celery_app.beat → scanner.run_scan_cycle → orchestrator.run_scan_cycle → context_scorer.score_universe → orchestrator._apply_proactive_first → risk.manager.evaluate → execution.interface.execute → paper.PaperBroker.place_order → db.Position` |
| **Tick arrives and a 5m bar closes** | `kite_ws.KiteTicker._on_ticks → aggregator.on_ticks (via run_coroutine_threadsafe) → bar rollover → candle_repo.upsert_candles` |
| **Intraday agent fires** | `celery_app.beat → intraday_scanner.run_intraday_scan → candle_repo.get_candles (5m+15m) → intraday_technical.analyze → risk.manager.evaluate → execution.interface.execute` |
| **Position SL triggers** | `celery_app.beat → position_monitor.check_positions → broker.kite.ltp → reason='stop_loss' → execution.interface.execute (SELL signal) → position.closed_at set + cooldown to Redis + telegram` |
| **User auths Kite** | `frontend → /api/auth/kite/login → broker.get_login_url → Zerodha redirect → /api/auth/kite/callback → broker.generate_session → cache.set_kite_token → instrument_master.load → ticker.connect → ticker.subscribe(NIFTY50 tokens)` |

---

## 12. Where to start if you want to change…

| Goal | File(s) |
|---|---|
| Tune risk limits | `app/risk/limits.py` |
| Add a new indicator | `app/data/indicators.py` + `app/agents/technical.py` (vote) |
| Add a new scoring signal for ProactiveAgent | `app/data/context_scorer.py` |
| Change scan frequency | `app/tasks/celery_app.py` |
| Add a new agent type | new file in `app/agents/`, register in `orchestrator.py::AGENT_CLASSES`, update `api/schemas.py` regex |
| Change SL/TP method per strategy | the agent's `analyze()` — see `proactive.py` lines around `calculate_atr_stop` |
| Change max-holding-days | `app/tasks/position_monitor.py::MAX_HOLD_DAYS_BY_STRATEGY` |
| Adjust ensemble weights (legacy path) | `app/agents/ensemble.py` |
| Add a new notification | `app/notifications/telegram.py` |
| Add a new dashboard widget | `frontend/src/components/dashboard/` + page in `frontend/src/app/` |
| Add a new API endpoint | `backend/app/api/routes/` + register in `router.py` |
| Add a new DB column | new migration in `backend/alembic/versions/` + update model in `app/db/models/` |
