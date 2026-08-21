# TradeMind — Strategy Research & Adoption Roadmap

**Generated:** 2026-05-30 (web research, 8 parallel lens investigators, 200 sources, 71 patterns, 30 independently verified)

This document is the result of an **exhaustive forensic + web-research investigation** into what makes algorithmic equity-trading systems generate good outputs. It is the companion to `LOSS_DIAGNOSIS_AND_ROADMAP.md` (which tells you why TradeMind lost money) — this one tells you what the literature and successful retail/institutional systems do that TradeMind doesn't.

---

## TL;DR — Three deep principles

1. **Risk and exit management beat signal discovery for an unprofitable system.** Replacing fixed 3-4% SLs with ATR-scaled stops, R-multiple expectancy tracking, and conviction-scaled sizing produces a larger Sharpe lift than any new alpha signal. This is the highest-leverage workstream.

2. **Measurement integrity precedes optimization.** Without point-in-time NIFTY-membership backtests, purged k-fold CV, walk-forward analysis, R-multiple logging, and realistic NSE cost modeling, every other "improvement" is just curve-fitting noise. Build the measurement plumbing **first**, then iterate strategies.

3. **India-specific structural edges are orthogonal to imported US techniques** and should be prioritized because foreign capital hasn't priced them out. DELIV_PER (delivery %), FII/DII flows, India VIX percentile bucketing, 30-min ORB, RBI/INR/monsoon sector tilts — none of these have US analogs. They're freely available via NSE/Kite and currently invisible to TradeMind.

---

## The 15 Patterns Worth Adopting

Ranked by impact-per-effort for TradeMind specifically. Each is independently verified by a second skeptic agent.

### Tier 1 — Foundation (must do before anything else)

#### #1 — ATR-based R-multiple position sizing + expectancy logging *(14h)*

**Principle.** Normalize risk across NIFTY 50's 3-5× ATR% dispersion and expose true edge via R-multiples.

**Why now.** The current fixed 3-4% SL gives ADANIENT zero room (ATR ~3.5%) and HDFCBANK 2× too much (ATR ~1.5%). Without R-multiple logging, **you literally cannot tell if the system has edge** — every other change is guesswork.

**Code adoption plan:**
- Compute ATR(14) on D1 sync.
- Replace fixed SL with `stop = entry - k × ATR(14)`, k=1.5 default, k=1.2 in trending, k=2.0 in chop (ADX-bucketed).
- Position size = `(0.5% × equity) / (entry - stop)`, floor at 2% stop on low-vol names (NESTLEIND, HUL).
- Add columns to `trades`: `initial_stop_distance`, `R_realized`, `holding_days`, `regime_at_entry`.
- Nightly Celery job: rolling 30/60/90-day expectancy, profit factor, payoff ratio per agent. Surface in dashboard.

**Status:** ⚙️ **Partially implemented today.** The accounting fix (`entry_charges`) and `signal_outcomes` table I just landed are the foundation. R-multiple is captured per row. Still TODO: per-name ATR multiplier tuning + dashboard panel.

---

#### #2 — Chandelier / ATR trailing stop activated after +1R *(10h)*

**Principle.** Convert winners into runners; let volatility (not arbitrary %) set giveback tolerance.

**Why now.** Fixed 3-4% SL on a fixed-target system has guaranteed negative expectancy unless win rate >55%. The trailing ratchet is the only mechanism that captures the asymmetric right-tail returns that pay for the cluster of small losses.

**Code adoption plan:**
- Until +1R: hard SL from #1.
- After +1R: `trail = max(entry, HighestHigh(22) - 3.0 × ATR(22))` on D1; only ratchets up.
- Gate by trend filter: only activate Chandelier when `price > 50-DMA AND ADX(14) > 20`, else stay on fixed ATR stop (avoids whipsaw in chop).
- Add gap-protection: hard exit on pre-market gap below trail by `>0.5 × ATR` (Indian gap risk).

**Status:** ⚙️ **Basic version implemented today.** I added `highest_price` to Position and a 4% trail in `position_monitor.py` that activates after +1R. Still TODO: ATR-based trail distance, trend-filter gating, gap-protection.

---

#### #3 — Backtest hygiene: point-in-time NIFTY membership + Purged K-Fold CV + walk-forward *(24h)*

**Principle.** Measurement integrity precedes optimization — every other pattern is unverifiable without this.

**Why now.** NIFTY 50 today is not NIFTY 50 of 2018. Survivorship bias inflates returns ~9% annualized on Indian indices (arxiv 2603.19380). Your "learning loop" will train on garbage unless this exists.

**Code adoption plan:**
- (a) Ingest NIFTY 50 reconstitution history from NSE archives → `index_membership(symbol, from_date, to_date)`.
- (b) Gate signal computation through `as_of_date`.
- (c) `PurgedKFold` splitter with embargo = max label horizon (10 D1 bars for swing, 1 session for intraday).
- (d) Walk-forward harness: 12mo train / 3mo test, monthly step.
- (e) Reserve last 3 months as **untouched holdout**, evaluated ONCE per release.
- (f) Defer CPCV+DSR until feature set stabilizes — premature for a system without an established edge.

**Status:** ⚪ Not started. Current `backtest/` directory is a skeleton. Highest-priority deferred item.

---

### Tier 2 — Regime & exposure gating (cheap, large impact)

#### #4 — India VIX percentile-bucketed conviction sizing *(8h)*

**Principle.** Size, don't switch — modulate exposure by **percentile-ranked** vol regime, never go full cash.

**Why now.** Absolute VIX thresholds (15/20/30) are miscalibrated to India VIX distribution. Percentile-rank gating IS supported. India VIX is published by NSE via Kite Connect.

**Code adoption plan:**
- New `RegimeAgent` reads India VIX daily; computes trailing 1y percentile.
- Rules:
  - `pct < 70 AND breadth (% NIFTY 50 > 200-DMA) ≥ 40%` → full size.
  - `pct 70–90 OR breadth 25–40%` → 0.5× size, raise ContextScorer threshold.
  - `pct > 90 AND breadth < 25%` → 0.25× size (NOT zero — Cederburg out-of-sample risk + missed rebounds post-COVID).
- Multiply onto Pattern #1's R-budget.
- Add breadth gate independently — it's the more robust half.

**Status:** ⚙️ Partial. I added a simpler NIFTY-% scanner-side regime today (`market:nifty_bias` Redis key); this should be upgraded to percentile + breadth.

---

#### #5 — Portfolio heat cap + sector sub-cap *(6h)*

**Principle.** Six 1%-risk positions in Financials is one 6% bet on a single factor, not six independent bets.

**Why now.** NIFTY 50 Financials weight ~35%; Bank Nifty/Nifty correlation 0.88. Malhotra-Sinha 2024 documented Indian cross-sector correlations rising 3.12× in COVID; March-2020 Sensex −31.6% / 15 sessions across all sectors. Without this, ContextScorer mechanically piles into the same factor.

**Code adoption plan:**
- Pre-trade FastAPI handler: `portfolio_heat = sum(stop_distance × size / equity)`.
- Cap at 4% while unprofitable, 6% post-edge-confirmed.
- Sector cap: max 2 positions per NIFTY sector; combined sector heat ≤ 3%.
- Static sector mapping JSON (50 names).
- Log rejections with reason for weekly review.
- Feed `heat_headroom` back into ContextScorer so it down-ranks signals that would breach the cap, rather than generating-then-rejecting.

**Status:** ⚙️ Partial — I added a 70% gross-exposure cap today (`max_gross_exposure_pct=70`). Still TODO: stop-distance-weighted "heat" instead of notional gross, sector sub-cap, scorer-side heat awareness.

---

### Tier 3 — Specific alpha signals

#### #6 — 30-min Opening Range Breakout filtered by ContextScorer top-N *(14h)*

**Principle.** Daily filter is the alpha; the breakout is just the trigger. Zarattini-Barbon-Aziz 2024: in-play sleeve Sharpe 2.81.

**Why now.** Indian markets concentrate volatility 9:15–9:45 (no overnight session). AlgoTest/Streak/Intraday Lab: 30m ORB Sharpe 1.16, 8/9 years profitable. TradeMind already has IntradayTechnical 5m+15m bars AND ContextScorer top-N — they just aren't wired together this way.

**Code adoption plan:** new `OpeningRangeBreakout` agent or `intraday_orb` strategy_type that:
- 9:15 IST: compute ORB high/low from first 30 min.
- 9:45 IST: for each symbol in ContextScorer top-10, if price breaks above ORB high → BUY.
- SL = ORB low (or `entry - k × ATR`).
- TP = `entry + 2 × (entry - ORB_low)` or trail with Chandelier.
- Force EOD close (per `MAX_HOLD_DAYS_BY_STRATEGY["intraday_technical"] = 1`).

---

#### #7 — NSE Delivery Percentage (DELIV_PER) Z-score as ContextScorer feature *(10h)*

**Principle.** Use India-specific structural edges that have no US analog and aren't arbitraged by foreign capital.

**Why now.** DELIV_PER is SEBI-mandated daily disclosure in the Bhavcopy — uniquely Indian, freely available, no US equivalent. Highest-leverage NIFTY-specific feature TradeMind doesn't have.

**Code adoption plan:**
- New Celery task: download NSE Bhavcopy daily after market close, parse `DELIV_PER` per stock.
- Compute trailing 20d mean/std → Z-score.
- Add `delivery_zscore` as a 6th feature in `ContextScorer` (alongside RS, sector, 52w, volume Z, tightness).
- Weight ~0.15–0.20 in composite score.
- Watch correlation with existing `volume_zscore` — if >0.7, pick one (it's likely orthogonal because volume includes intraday churn while delivery is settled positions).

---

#### #8 — FII/DII 10/20-day rolling flow as portfolio-level conviction multiplier *(8h)*

**Principle.** FIIs are momentum traders (not "smart money") but their flow is the dominant marginal-buyer signal for NIFTY.

**Why now.** October 2024 stress test: FII sold record ₹1.14 lakh cr, DII absorbed 94%, but NIFTY still −8%. Daily noise is dominant; rolling 10–20d window is empirically robust (NSE working papers, ICRIER, Mukherjee-Chatterjee 2024). Cheap, public data; complements Pattern #4.

**Code adoption plan:**
- New Celery task: scrape/fetch FII+DII daily flows from NSE.
- Compute rolling 10d and 20d net flow.
- Multiplier on position size: positive trend = full size, negative trend = 0.7× size, two consecutive weeks of net selling = 0.5× size.
- Surface on dashboard.

---

### Tier 4 — Execution & cost discipline

#### #9 — Time stop / dead-money exit at N bars without +0.5R progress *(6h)*

**Principle.** Capital tied up in a stagnant trade is capital not deployed on the next fresh signal.

**Why now.** TradeMind generates from 50 names daily but lacks slot-recycling — fixed-SL trades hang indefinitely. Opportunity cost is high.

**Code adoption plan:** in `position_monitor.py`, in addition to `max_holding_days`, add a "dead-money" check: if `days_held ≥ time_stop_days` AND `max_unrealized_pnl_pct < 0.5R` ever achieved → close. Defaults: 5 D1 bars for swing, 3 hours for intraday.

**Status:** ⚙️ Adjacent to my max-holding-days fix. The new logic could subsume it.

---

#### #10 — NSE-realistic execution cost model *(10h)*

**Principle.** Most backtest-to-live blow-ups are cost misspecification, not signal failure.

**Why now.** Connors RSI-style 95bps-gross strategies get 15-30bps eaten by STT/brokerage/slippage. Build this into the backtest harness BEFORE evaluating any new signal, otherwise you'll deploy losers thinking they're winners.

**Code adoption plan:**
- Use existing `app/execution/charges.py` (already correct per Zerodha rates).
- Add a `slippage` model: `slippage_bps = c × sigma × sqrt(Q / ADV)` where `Q` is order quantity and `ADV` is 20d average daily volume.
- For NIFTY 50 with small order sizes, c=0.5–1.0 (calibrate from Kite tick replay).
- Apply to both backtest fills and to live-mode pre-trade impact estimates.

---

#### #11 — Order-type discipline + session-time gates *(8h)*

**Principle.** Indian intraday U-curve volatility makes naive market orders cost 10–30 bps in the first 15 min.

**Why now.** Cheapest behavioral fix (~2-6 engineering hours total). NSE pre-open auction + first-15-min spread widening is well-documented. Even on paper, codifying these gates means live P&L will track paper P&L instead of mysteriously trailing 30-50 bps.

**Code adoption plan:**
- Default order type → LIMIT at LTP for entries; MARKET only as final exit.
- Reject entry signals 9:15–9:30 IST (pre-ORB) unless `strategy_type` is `intraday_orb`.
- Reject entry signals 15:15–15:30 IST (avoid closing-auction chop).

---

### Tier 5 — Signal-quality refinement

#### #12 — Minervini SEPA Trend Template as **graded** eligibility gate *(6h)*

**Principle.** Hard pre-filter for Stage-2 eligibility, but graded on NIFTY 50 because strict 6/6 yields ~0 candidates in chop.

**Why now.** Components (52w-high proximity via George-Hwang 2004, 200-DMA rising via Faber) have strong independent academic support. NIFTY 50 universe is too narrow for strict 6/6 — implement as SCORED gate.

**Code adoption plan:** add a `sepa_score` (0-6) to `ContextScorer`. Components:
1. Price > 50-DMA
2. Price > 150-DMA
3. Price > 200-DMA
4. 50-DMA > 150-DMA > 200-DMA
5. 200-DMA rising for ≥1 month
6. Price within 25% of 52-week high

Use as a tiebreaker / amplifier rather than a hard gate. Stocks scoring 5–6 get a 1.2× weight; 0–2 get filtered out.

---

#### #13 — Triple-barrier labeling for the learning loop *(16h)*

**Principle.** Train-objective must match deploy-objective; without this every supervised model has train/serve skew.

**Why now.** Pranav wants a learning loop. Building it on next-day-return labels while deploying with ATR stops guarantees the model learns the wrong objective.

**Code adoption plan:** in the future ML labeling pipeline, label each historical bar with one of:
- `+1` (hit upper barrier = TP)
- `-1` (hit lower barrier = SL)
- `0` (hit time barrier = horizon expired)

Barriers are ATR-scaled, mirroring live exits. Use this label set to train any classifier that goes into ContextScorer.

---

#### #14 — FinBERT-as-feature replacing keyword SentimentAgent *(30h)*

**Principle.** LLM/sentiment is a weak-but-real factor; let a calibrator decide weight, never let it trigger orders.

**Why now.** Current SentimentAgent uses RSS keywords — the bottom of the financial NLP stack. FinBERT (ProsusAI/finbert) is a drop-in upgrade. Demoting Gemini ReasoningAgent to UI-only (not order-altering) addresses the "Alpha Illusion" failure mode for end-to-end LLM agents.

**Code adoption plan:**
- Add `finbert-feature` worker: run FinBERT on each headline, output `bullish_prob`, `bearish_prob`.
- Aggregate per stock per day → `sentiment_score_finbert` as a new ContextScorer feature with weight ~0.10.
- Keep SentimentAgent as a confirmer only (per current architecture).
- Demote Gemini ReasoningAgent to dashboard "narrative" — no order alteration.

---

#### #15 — Event blackout calendar *(12h)*

**Principle.** Information events have IV crush and adverse selection; not playing is the +EV move for a retail/paper system.

**Why now.** Cheap to add; prevents a class of tail losses no signal improvement can recoup.

**Code adoption plan:**
- New `event_calendar` Celery task: maintain table of upcoming RBI / FOMC / CPI / earnings / Budget dates.
- 15-min blackout window around each event in the scanner. Tag rejections with `reason=event_blackout`.
- F&O ban list check: query NSE F&O ban list daily; veto BUYs on banned names.

---

## Anti-Patterns Currently Present in TradeMind

The research flagged 14 anti-patterns. **Six are currently present in TradeMind** (the rest were checked and confirmed absent).

| # | Anti-pattern | Status | Mitigation |
|---:|---|---|---|
| 1 | Fixed 3-4% SL on multi-volatility universe | ✅ **fixed today** | ATR-aware SL in ProactiveAgent |
| 2 | Equal-rupee flat position sizing | 🟠 partial | NIFTY-regime scale done; conviction-scaled still TODO |
| 3 | No conviction-scaled sizing | 🟠 partial | needs Kelly fractional / R-budget; signal_outcomes is the data foundation |
| 4 | No trailing stop / no runner | ✅ **fixed today** | basic trail in `position_monitor`; needs Chandelier upgrade |
| 5 | PnL tracked in rupees, not R-multiples | ✅ **plumbing done today** | signal_outcomes captures `r_multiple`; UI still rupee-denominated |
| 6 | Gemini ReasoningAgent altering orders | 🟠 partial | only legacy path uses Gemini; new proactive-first bypasses it |
| 7 | Static weights on TechnicalAgent | 🟠 unchanged | regime-aware in `REGIME_WEIGHT_MODS` partially exists; needs auto-tuning |

**Anti-patterns research found, NOT present in TradeMind (do NOT introduce them):**

- Backtest on today's NIFTY 50 (survivorship bias) — TradeMind has no backtest yet, so this is a forward warning
- K-fold CV on path-dependent labels — same forward warning
- Pyramiding into winners before base system is profitable — explicitly do not do this
- Equity-curve self-filter (SMA-of-trades on/off switch) — counter-productive for momentum systems
- F&O ban release-pop / NIFTY inclusion arb as primary signal — crowded by faster players
- LightGBM next-day return prediction as standalone alpha — overfit; only use as feature
- Connors RSI-2 at US thresholds on NIFTY 50 — Indian costs eat the edge

---

## What Was Implemented in This Session

This session landed 10 fixes + 1 migration. The mapping to the research patterns:

| Fix | Research pattern reinforced |
|---|---|
| Entry-leg charges (interface.py + Position.entry_charges) | #10 NSE-realistic cost model — at last we deduct both legs |
| Sentiment confidence cap (0.70 → 0.55) | not in research; was a TradeMind-specific config bug |
| Cooldown fail-closed + partial-close | not in research; defensive plumbing |
| Max-holding-days fires every cycle | adjacent to #9 time stop / dead-money exit |
| Proactive ATR-aware SL (proactive.py) | #1 ATR R-multiple sizing — the entry half |
| Proactive-veto position-state-blindness | not in research; TradeMind-specific |
| Gross exposure cap (70%) + position limits 10→7 | #5 portfolio heat cap (notional version) |
| NIFTY market-regime filter | #4 VIX bucketing (% version, not VIX-percentile yet) |
| Trailing stop in position_monitor | #2 Chandelier trail — basic version |
| signal_outcomes learning table + /api/signal-performance | #1 R-multiple expectancy logging foundation |
| migration 9b2e4d1a3c5f | schema for above |

---

## Open Questions Requiring Experimentation

The research surfaced 14 open questions that **only backtesting can answer**:

1. What is TradeMind's actual R-multiple distribution? (Needed before fractional Kelly is meaningful.)
2. What is the empirically optimal ATR multiplier (k) per NIFTY-50 name?
3. What is the per-agent E-Ratio curve?
4. Does 5m ORB extend beyond 15–30 min on NIFTY 50, or fade per Bulkowski?
5. How often does strict 6/6 Minervini yield zero candidates?
6. What is the realistic Indian-market square-root impact coefficient?
7. Does FII/DII signal degrade once paper goes live (T+1 finalized vs T+0 provisional)?
8. Is Connors RSI(2)<5 appropriate for NIFTY single stocks vs <2/>98 on the index?
9. Does DELIV_PER Z-score collide with existing volume-Z (correlation >0.7)?
10. What's the right embargo length for purged-CV on IntradayTechnical 5m signals?
11. Does FinBERT outperform a fine-tuned variant on Moneycontrol/ET corpora?
12. What's the actual NIFTY 50 reconstitution rate per year?
13. How should Chandelier interact with overnight gaps?
14. Does 30m ORB need per-sector calibration on NIFTY 50?

**All of these are unanswerable without Pattern #3 (backtest hygiene).** That's why #3 is on the critical path even though it generates no direct P&L.

---

## Recommended Sequencing After This Session

Today's session has cleared the Tier-1 plumbing (accounting + signal outcomes + basic trail + ATR SL). The natural next phases:

| Phase | Patterns | Effort | Outcome |
|---|---|---:|---|
| **B (now)** | #3 backtest hygiene + #10 NSE cost model + #1 finish | 48h | Honest measurement of every other change |
| **C** | #4 VIX percentile + #5 stop-distance heat + #11 order discipline | 22h | Portfolio-level governors |
| **D** | #7 DELIV_PER feature + #8 FII/DII gate + #12 SEPA graded | 24h | India-specific alpha signals |
| **E** | #6 30m ORB strategy + #15 event blackout + #9 dead-money | 32h | Intraday strategy + tail-risk prevention |
| **F (later)** | #13 triple-barrier + #14 FinBERT | 46h | ML feature foundation |

**Critical path:** B → C → D → E → F.

Phase B is the gateway. Until backtest hygiene is in place, every later change is unverifiable.

---

## Sources (representative)

The 200 sources span:
- Academic: Jegadeesh-Titman (1993), Moskowitz-Ooi-Pedersen (2012), Asness et al. (2013), Bailey-Lopez de Prado (2014), George-Hwang (2004), Faber (2007), Zarattini-Barbon-Aziz (2024), Cederburg (2023), Arian et al. (2024), Mukherjee-Chatterjee (2024)
- Books: Lopez de Prado (Advances in Financial ML), Ernie Chan (Quantitative Trading), Robert Carver (Systematic Trading, Leveraged Trading), Andreas Clenow (Following the Trend, Stocks on the Move), Mark Minervini (Trade Like a Stock Market Wizard), Van Tharp (Trade Your Way to Financial Freedom)
- Open source: Freqtrade, Hummingbot, Lean, Backtrader, Zipline, vectorbt, jesse.trade, nautilus_trader, FinBERT
- India-specific: Zerodha Streak, AlgoTest, Tradetron, Smallcase factor portfolios, NSE working papers, ICRIER reports, SEBI PMS disclosures
- Industry: r/algotrading retrospectives, Quantpedia, Quantocracy, Robust Trader, AllAboutAlpha, Patrick Boyle

Full source list and unverified-pattern notes are in the workflow task output. Three forensic+research workflows have been run on this codebase in the past 24 hours: this is the second.
