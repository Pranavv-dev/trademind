# What we took from TauricResearch/TradingAgents

**Reviewed:** 2026-06-15 · their repo + recent commits (through June 2026)

[TradingAgents](https://github.com/TauricResearch/TradingAgents) is a LangGraph multi-agent framework where **every agent is an LLM call**, organized like a trading firm: 4 analysts → bull/bear debate → trader → aggressive/conservative/neutral risk debate → portfolio manager → execution, with a memory loop that reflects on realized returns.

## The core tension

TradingAgents is LLM-maximalist. TradeMind deliberately moved **away** from that — our forensic audit flagged "Gemini altering orders" as an anti-pattern and the research flagged the "Alpha Illusion" finding against end-to-end LLM trading agents. So we cherry-picked, we didn't port.

## What we incorporated ✅

### 1. The reflection / memory loop (SHIPPED 2026-06-15)
Their best idea: after a trade closes, record the outcome + a reflection, and feed recent lessons back into the next decision. We already had the data half (`signal_outcomes`); we built the feedback half — **deterministically, not via LLM**:
- `signal_outcomes.reflection` — a one-line post-mortem generated at close (`agents/memory.py::build_reflection`)
- `positions.entry_metadata` — snapshots the entry thesis so the reflection can recall *why* we entered
- `SignalMemory` (`agents/memory.py`) — turns recent per-symbol outcomes into a score multiplier (0.4–1.1): down-weights symbols that keep stopping us out, modestly rewards proven winners
- `ContextScorer.score_universe(memory_multipliers=…)` applies it; the orchestrator fetches it each scan
- Exposed at `GET /api/signal-performance/memory` and `/recent` (now includes `reflection`)

This is complementary to cooldown (intraday block) and max-holding-days (exit side): memory operates on a 10–30 day horizon — "has this symbol been good or bad for us lately?"

**Why deterministic, not LLM:** free, reproducible, and can't be hallucinated away. An LLM one-liner upgrade is possible later without schema changes.

## What we deliberately did NOT take ❌

| Their idea | Why skipped |
|---|---|
| Bull/Bear LLM debate (2 rounds) | LLM-expensive; research warns against LLM deciding trades |
| Aggressive/Conservative/Neutral LLM risk debate | Our deterministic 13-gate RiskManager is better for a money gate — reproducible, auditable, fail-closed |
| Every-agent-is-an-LLM + full LangGraph | Re-introduces the cost/hallucination problems we just removed |
| Multi-provider LLM registry (Bedrock/NIM/Groq…) | Over-engineering for a single-user system |

## Still worth taking later (folds into existing phases)

- **Market-data verification snapshot** (anti-hallucination guard for any LLM in the loop) → fold into the Gemini ReasoningAgent + the prod-readiness stale-data item
- **Deep-think vs quick-think LLM split** (cost discipline) → Phase F when LLM usage expands
- **News/macro analyst as a distinct confirmer** → validates the Phase C/D direction (FII/DII, India VIX, event blackout)
- **Stale-data rejection** (they added "reject stale prices rather than report wrong values") → independent validation of our pending Kite-WS staleness item
