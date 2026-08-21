# TradeMind — Loss Diagnosis & Production Roadmap

**Generated:** 2026-05-30 (forensic audit, 8 parallel lens investigators, 43 confirmed findings)

This document answers two questions:

1. **Why did the system lose all the gains so far?** (forensic diagnosis)
2. **What needs to happen before it's safe with real money?** (production roadmap)

---

## TL;DR

You lost roughly **₹5,000–6,500** of cumulative realized P&L over April–May 2026 from three compounding leaks. Two of the three were architecturally fixed (May 12 cooldown, May 13 proactive-first restructure, May 20 max-holding-days). One is still leaking right now: **entry-leg brokerage charges are silently dropped from realized P&L** — every closed trade overstates net P&L by ~₹100–120, so your dashboard is lying to you by ~₹1,000/day.

The system is **architecturally correct as of May 20** but is **not net profitable** and is **not production-ready** for real money. Two distinct workstreams sit ahead:

- **Stop the bleed + fix the math** (≈32 hours, 10 changes) — the things that fix profitability
- **Production readiness** (≈114 hours, 15 changes, 4 blockers) — the things that fix safety

---

## 1. Diagnosis — Five Root Causes

Estimated share of cumulative realized losses, ranked.

### 🔴 1. Legacy sentiment positions with no max-hold and no cooldown — **70% of losses**

**Mechanism.** Pre-May 12, the SentimentAgent could open positions with a tight fixed 3% SL and zero time-based exit. Positions sat for weeks at unrealized losses, and a stop-loss exit was a *guaranteed* loss (4–6% drawdown from entry). Without per-agent cooldown, the moment SL closed a position, the same news headlines re-fired the BUY at a worse price — the death-spiral.

**Evidence.**
- WIPRO: opened Apr 10 @ ₹201.84, closed May 13 @ ₹189 → **−₹3,819** after 33 days
- TATASTEEL: opened Apr 28 @ ₹215.15, closed May 20 @ ₹204.17 → **−₹505** after 22 days
- BAJFINANCE: opened Apr 30 @ ₹946.05, closed May 13 @ ₹898 → **−₹483**
- NESTLEIND (proactive): opened May 13 @ ₹1,468.70, closed May 20 @ ₹1,409.30 → **−₹356**

**Status.** ✅ Fixed by May 12 cooldown + May 13 sentiment demotion + May 20 max-holding-days. The architecture cannot reproduce this pattern. But the rupees are gone — those are the gains you lost.

---

### 🟠 2. Entry-leg brokerage charges silently discarded from realized P&L — **10% of losses** *(ACTIVE BUG)*

**Mechanism.** `ExecutionEngine._update_position()` at `interface.py:122` computes:

```python
realized_pnl = (fill_price - avg_price) * close_qty
```

The opening-leg brokerage (~₹20 + STT 0.025% + GST + SEBI fees ≈ **₹100–120 per round trip on a typical NIFTY trade**) is computed and stored in the opening `Trade` row's `brokerage` field, but never retrieved at close. The Position model has no `entry_charges` column and no FK to the opening Trade, so the closing code has no way to recall it. The PaperBroker in-memory tracking has the same defect.

**Evidence.**
- `interface.py:96-103` — opens position, computes entry charges, stores in Trade
- `interface.py:122` — closes position, deducts ONLY exit-leg charges
- `interface.py:213-219` — no entry_charges retrieval
- `paper.py` — same defect, no entry-leg tracking on in-memory positions

**Quantified impact.** At 8 closes/day across active agents this is roughly **₹1,000/day of phantom profit**. Your dashboard's "+₹994 net portfolio" is closer to **₹0 or slightly negative** in reality.

**Status.** 🔴 Active bug. **Highest impact-to-effort fix in the system.**

---

### 🟠 3. Volatility-blind static stop-losses with no trailing and no conviction scaling — **10% of losses** *(ACTIVE)*

**Mechanism.** Proactive uses fixed 4% SL, Sentiment fixed 3% SL. Neither scales with the stock's ATR. Typical NIFTY large-cap daily ATR is 2.5–3.5%, so a 3–4% SL leaves almost no buffer above intraday noise — winners get whipsawed out. Compounding this:

- `trailing_stop()` exists in `stop_loss.py:21-36` but is **never called anywhere** in the codebase
- `Position` model has no `highest_price` column
- SL is set at entry and never updated → winners that run 5% and retrace 4% close at −0.8%
- Position sizing is flat 10% regardless of signal confidence (a 0.60 signal gets the same notional as a 0.95 signal)
- 10 positions × 10% each = **100% capital deployable** with no portfolio-level gross-exposure cap

**Evidence.** `proactive.py:37` (sl_pct 0.04), `sentiment.py:36` (sl_pct 0.03), `stop_loss.py:21-36` (trailing defined but unused), `position.py` (no highest_price), `risk/limits.py:11,15` (10% × 10 positions).

**Status.** 🔴 Active.

---

### 🟡 4. Sentiment over-tuning makes the agent functionally dead — **5% of losses**

**Mechanism.** Post-WIPRO panic, sentiment was tightened to `min_headlines=4` and `min_confidence=0.70`. But `_analyze_with_keywords()` caps confidence at 0.70:

```python
confidence = min(0.4 + bull_ratio * 0.3, 0.70)
```

To clear `min_confidence=0.70`, bull_ratio must equal **exactly 1.0** — every matched headline must be unanimously bullish with zero bearish words. Near-impossible on real news.

`sentiment.py:138` (schema default 0.55) contradicts `sentiment.py:35` (actual default 0.70) — likely an accidental misconfiguration during the May 13 tightening.

**Evidence.** `sentiment.py:35`, `:191`, `:195`, `:138`. Verified by arithmetic from two independent skeptics.

**Status.** 🔴 Active. Sentiment has gone from "noisy" to "silent confirmer" — useful information is being thrown away.

---

### 🟡 5. Architectural fragility — **5% of losses**

No trailing stops, no phased exits (50% off at 1× TP, ride the rest), no NIFTY-regime filter to scale down on risk-off days, no daily P&L target to stop trading after hitting goal, no `signal_outcome` table to learn which agents/score-tiers actually work. Every parameter decision is hindsight-driven — the May 13 tightening was a panic reaction to one ticker (WIPRO), not data-driven.

The system cannot tell you its own win rate per agent or per signal type. Every losing pattern is rediscovered manually, slowly, after capital damage.

---

## 2. Timeline — When did the rot start, when did it stop?

```
Apr 1–9    System operational, paper trading working, ~neutral P&L
Apr 10  ⚠️ WIPRO BUY (sentiment, 3% SL, no max-hold, no cooldown) — the rot begins
Apr 28  ⚠️ TATASTEEL BUY (sentiment) — same flaws
Apr 30  ⚠️ BAJFINANCE BUY, RELIANCE BUY (sentiment) — same
Apr-May    Old positions silently bleed; small wins from other agents mask the rot

May 12  ✅ Per-agent symbol cooldown deployed (death-spiral re-entry fix)
May 13  ✅ Proactive-first restructure: ContextScorer + ProactiveAgent now primary;
            sentiment + technical demoted to confirmers; sentiment tightened (→ dead)
May 13  ❌ Legacy positions come home: WIPRO −₹3,819, BAJFINANCE −₹483
            (this is the day your prior gains visibly evaporate on the dashboard)
May 13–19  New proactive picks (ADANIENT, ONGC, ...) show modest positive results
May 20  ✅ max_holding_days rule deployed
May 20  ❌ TATASTEEL force-close −₹505 (22d), NESTLEIND SL −₹356 (7d)
            BHARTIARTL TP +₹615; 8 closes net −₹149 realized
Final     +₹1,143 unrealized on 8 open positions
          +₹994 reported portfolio (but ~₹0 once entry charges are honestly accounted)
```

The bleed stopped. The gains never came back.

---

## 3. Immediate Fixes — Stop the Bleed (≈32 hours)

In **impact-per-hour** order. Do the top 4 this week.

### 3.1. 🚨 Fix entry-leg charges in realized P&L *(4h — HIGHEST IMPACT)*

Add `entry_charges` column to `Position`. Store opening Trade's brokerage on open. Deduct `(entry_charges + exit_charges)` at close. Fix PaperBroker in-memory tracking and the position_monitor auto-close path consistently.

- Files: `db/models/position.py`, `execution/interface.py`, `execution/paper.py`, `tasks/position_monitor.py`, `alembic/versions/`
- **Why first:** every P&L decision (daily loss limit, win-rate analysis, dashboards) is currently operating on inflated numbers. Until this is fixed, you are tuning the system based on lies. Pure arithmetic, no strategy risk.

### 3.2. ⚡ Sentiment confidence cap fix *(0.5h)*

`sentiment.py:35` — change `min_confidence: 0.70` → `0.55` (matches the schema default at line 138). The 0.70-cap-on-0.70-floor creates an unreachable deadzone.

- File: `agents/sentiment.py` (one line)
- **Why:** restores sentiment from <0.1% fire rate to ~5–10%. Brings back the confirmer signal layer.

### 3.3. 📏 Widen + ATR-base the stop-losses *(3h)*

Bump Proactive `sl_pct` to 5–6%, Sentiment to 5%, OR use 2.0×ATR from daily candles. Pass ATR via `signal.metadata` so `RiskManager.evaluate()` (already supports ATR override at manager.py:130-132) uses it.

- Files: `agents/proactive.py`, `agents/sentiment.py`, `risk/manager.py`
- **Why:** tight SL is the #1 cause of winners-becoming-losers and noise whipsaws. Infrastructure for ATR-based SL already exists; agents just need to populate the metadata field.

### 3.4. 📈 Implement trailing stop *(6h)*

Add `highest_price` column to `Position`. Each cycle: `highest_price = max(highest_price, current_price)`. Compute `trailing_sl = highest_price × (1 - trail_pct)` (default 4%). Close if `current_price < max(static_sl, trailing_sl)`. Ratchet only — never loosen.

- Files: `db/models/position.py`, `tasks/position_monitor.py`, `alembic/versions/`
- **Why:** `trailing_stop()` already exists in `stop_loss.py` but is unused. Schema migration + ~20 lines. Locks intermediate gains on winners. Estimated +15–25% to net realized P&L over a quarter.

### 3.5. 🔁 Patch the remaining cooldown leaks *(1h)*

(a) `interface.py:223` — change `if existing.quantity <= 0:` to `if close_qty > 0:` so partial closes also set cooldown. (b) `risk/manager.py:119-122` — currently fails open on Redis error; make it fail closed (default `in_cooldown=True` on exception, log CRITICAL).

- Files: `execution/interface.py`, `risk/manager.py`
- **Why:** two residual death-spiral leaks. Fail-closed is the correct posture for a money safety gate.

### 3.6. ⏰ Remove EOD-window restriction from max_holding_days *(1h)*

Currently max-hold only fires 15:00–15:25 IST. If that window is missed (Celery crash, Redis flake, deploy), +24h of bleed. Check at every monitor cycle. For intraday agents (max_days=1), force-close any position from prior day at first opportunity.

- File: `tasks/position_monitor.py`

### 3.7. 🌊 NIFTY market-regime filter on the scanner *(4h)*

Compute 5m NIFTY % vs prev close. If NIFTY down >1%, scale all new BUY position sizes ×0.5; if down >1.5%, pause new entries entirely. Inject as a new gate in `RiskManager.evaluate()`.

- Files: `tasks/scanner.py`, `risk/manager.py`
- **Why:** prevents cascaded SL hits on risk-off days. Stocks don't decouple from the index on bad days.

### 3.8. 🎯 Cap portfolio gross exposure *(2h)*

`max_position_size_pct` 10% → 7%; `max_open_positions` 10 → 7. Add explicit gross-exposure check: reject if total open exposure > 70% of capital.

- Files: `risk/limits.py`, `risk/manager.py`
- **Why:** currently 100% deployment is allowed. One bad day with 5+ simultaneous SLs ≈ 20% loss. Force dry powder.

### 3.9. 🧠 Signal-outcome learning table *(8h)*

New `signal_outcomes` table: `(close_reason, days_held, expected_pnl, actual_pnl, context_metrics, agent_id, signal_metadata)`. Write a row at every position close. Expose `/api/signal-performance` endpoint.

- Files: `db/models/`, `tasks/position_monitor.py`, `execution/interface.py`, `api/routes/`
- **Why:** right now you literally cannot answer "is my sentiment agent profitable?" or "do context_score ≥80 picks beat 60–70 picks?". Every tuning decision is hindsight-driven. This is the missing feedback loop.

### 3.10. 🚦 Fix proactive-veto position-state-blindness *(2h)*

In `orchestrator._apply_proactive_first`, before vetoing a proactive BUY on a technical SELL, check whether a position actually exists for that symbol. A SELL on a no-position symbol is nonsense and shouldn't kill a legitimate independent BUY.

- File: `agents/orchestrator.py` (~15 lines)

**Total for immediate fixes: ≈31.5 hours. After these, the system should be net positive on a typical week.**

---

## 4. Production Readiness — Safe for Real Money (≈114 hours)

Status before any of these: **NOT SAFE.** Four blockers must be cleared before live mode.

### 4.1. 🔴 Blockers (30 hours total — MUST clear before live)

| Gap | Effort | Fix |
|---|---:|---|
| **No API authentication** — all 29 endpoints (start/stop agents, modify risk config, place trades) are unauthenticated. CORS is not a security control. | 8h | JWT or API-key middleware on all `/api/*` except Kite OAuth callback. HTTPS in prod. Per-env keys. |
| **TRADING_MODE=live has zero safeguards** — env-var flip, no confirmation ritual, no kill switch. A typo deploys real money trades. | 12h | Separate `LIVE_MODE_ENABLED` flag with one-time dashboard confirmation token. Audit max_position_size_pct ≤ 5% before live. Add `POST /api/emergency-stop` cancelling all orders + pausing agents. Add staging mode (paper broker on live quotes). |
| **No database backups** — Postgres data in a Docker volume, no pg_dump, no S3 snapshots. Loss = entire trade history + agent configs + P&L records. | 6h | Celery task daily 16:00 IST: `pg_dump \| gzip \| aws s3 cp`. 30-day retention. Monthly restore-to-staging test. Long-term: managed RDS/Cloud SQL with automated snapshots. |
| **Redis has no persistence** — restart wipes cooldowns AND circuit breaker state mid-day. Death-spiral fix is defeated; circuit breaker resets allow unlimited further losses. | 4h | Mount redisdata volume + redis.conf with `save 900 1` and `appendonly yes`. Persist circuit_breaker key to DB as well. On startup, repopulate cooldowns from last-24h position closes. Long-term: managed ElastiCache/MemoryStore. |

### 4.2. 🟠 High-severity (23 hours)

| Gap | Effort |
|---|---:|
| Health check task returns hardcoded `True` — no real DB/Redis/Kite probe; no degraded-mode signal | 6h |
| Telegram alerts are optional — silent failure if env vars missing/typo | 4h |
| No Docker restart policy — worker crash = SL enforcement stops for hours | 1h |
| Kite WebSocket stale-data detection missing — disconnect >30m causes false intraday signals on stale bars | 6h |
| Cooldown sync race — cooldown set AFTER session.commit() with swallowed exception; failed set allows immediate re-buy | 6h |

### 4.3. 🟡 Medium-severity (57 hours)

- Order placement not idempotent (no UUID key) — double-spend risk on retry/crash *(4h)*
- No paper/live position reconciliation — drift on partial fills, rejects, missed WS events *(6h)*
- Timezone handling mixes UTC/IST/naive — fragile to deploy elsewhere *(4h)*
- Live-mode capital scaling — fixed 10% means ₹10L capital → ₹1L/order, near SEBI single-order limits *(3h)*
- Test coverage thin (~3.3k lines tests for ~15k backend) — no integration tests for scan → risk → execution *(40h)*

### 4.4. ⚪ Low-severity (4 hours)

- Alembic rollback strategy thin — single migration, skeletal downgrade

---

## 5. Things That Are Working — Do NOT Change

Confirmed by the audit. These pieces are correct and should be preserved.

- **Per-agent symbol cooldown** via Redis on full position close. Blocks re-buy until next 9:15 IST. This stopped the death-spiral and is the most important defensive layer.
- **Proactive-first orchestration with ContextScorer** (May 13 restructure). Sentiment/technical demoted to confirmers is the correct architecture.
- **max_holding_days enforcement** (15 sentiment / 21 proactive / 30 technical / 1 intraday). Stopped the 22-day TATASTEEL / 33-day WIPRO bleeds.
- **Zerodha charges computation** in `execution/charges.py` — formulas are correct per 2026 rates (STT, brokerage, GST, SEBI, stamp duty, exchange fees). The bug is in *applying* the entry leg, not computing.
- **Decimal arithmetic** in the charges/P&L path — avoids float rounding errors.
- **Duplicate-position check** in `risk_manager.evaluate()` — prevents same-direction stacking.
- **Daily loss limit (3%) and 15% drawdown circuit breaker** — gates exist and fire correctly when realized P&L is tracked accurately (currently undermined by root cause #2).
- **Celery beat schedule and IST timezone** on the worker — scheduling is correct; bugs are at the task level, not the scheduler.
- **ProactiveAgent's `LTP > today's open` intraday entry confirmation** — filters gap-down entries. Keep this gate.

---

## 6. Recommended Sequencing

```
WEEK 1 — STOP THE BLEED & FIX THE MATH (32h)
  Day 1-2 │ Fix #3.1 entry-leg charges               (4h)  ← unblock everything else
          │ Fix #3.2 sentiment cap                   (0.5h)
          │ Fix #3.5 cooldown leaks                  (1h)
          │ Fix #3.6 max-hold window                 (1h)
  Day 3-4 │ Fix #3.3 widen + ATR SL                  (3h)
          │ Fix #3.10 proactive-veto check           (2h)
  Day 5   │ Fix #3.8 gross exposure cap              (2h)
          │ Fix #3.7 NIFTY-regime filter             (4h)
  Day 6-7 │ Fix #3.4 trailing stop                   (6h)
          │ Fix #3.9 signal_outcomes table           (8h)

WEEK 2 — BLOCKERS (30h)
  Day 1-2 │ DB backups + Redis persistence            (10h)
  Day 3-4 │ API authentication                        (8h)
  Day 5-7 │ Live mode safeguards + emergency stop     (12h)

WEEK 3 — HIGH-SEVERITY (23h)
  Day 1-2 │ Real health checks + Telegram mandatory   (10h)
  Day 3   │ Docker restart policies                   (1h)
  Day 4-5 │ Kite WS staleness detection               (6h)
  Day 6-7 │ Cooldown sync race fix                    (6h)

WEEK 4+ — MEDIUM & TEST COVERAGE (57h)
  Idempotent orders, reconciliation, timezone cleanup, capital scaling, integration tests.
```

**Stop point for live trading:** after Week 2 + Week 3 (all blockers + highs cleared), with one week of paper trading showing positive P&L on the corrected accounting.

---

## 7. What the Numbers Should Look Like Afterward

Concrete success criteria after Week 1:

- ✅ Daily P&L on the dashboard equals what an offline reconciliation against `trades.brokerage` produces, within ₹5 (proves charge bug is fixed)
- ✅ Sentiment fires on ≥3% of scans (proves cap fix landed)
- ✅ At least 1 trailing-stop close visible in logs within 5 trading days (proves the trail ratchet is wired)
- ✅ `signal_outcomes` table populated with ≥20 rows after 1 week (proves the learning loop is open)
- ✅ Sharpe ratio of post-Week-1 trades > 0.5 over 2-week measurement window (proves overall strategy edge exists)

After Week 2+3:

- ✅ `pg_dump` runs daily and the latest dump is restorable (proves backup works)
- ✅ A redis restart does not reset cooldowns or the circuit breaker state (proves persistence works)
- ✅ Calling any API endpoint without `Authorization: Bearer ...` returns 401 (proves auth gate works)
- ✅ Setting `LIVE_MODE_ENABLED=true` requires dashboard confirmation token (proves the live-flip can't be silent)
- ✅ `POST /api/emergency-stop` cancels all open orders and pauses agents within 5 seconds (proves the kill switch works)

---

## Appendix A — Audit Methodology

Forensic investigation run as a parallel multi-agent workflow:
- **8 lens investigators** in Phase 1 (history, signal logic, risk/exit, execution/charges, cooldown/dedup, configuration, architecture, prod-readiness)
- **2 independent skeptics per finding** in Phase 2 (correctness skeptic + completeness skeptic), each tasked to refute
- **1 synthesis agent** in Phase 3, combining only findings confirmed by both skeptics
- **62 total findings → 48 high/critical → 43 confirmed → 5 root causes**

Full raw findings retained at the workflow task output. This document is the executive synthesis.
