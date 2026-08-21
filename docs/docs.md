# Financial Structure

A short explanation of how money moves through my paper-trading system on Indian NSE.

---

## 1. The picture in one paragraph

System runs **multiple agents** on a shared capital pool. Each agent decides what to buy; a central risk manager sizes the position so every trade risks the same fixed fraction of capital — **0.5%** — regardless of the stock's price. Stop-loss is set automatically based on the stock's volatility (ATR-scaled). Both legs of brokerage and statutory charges are deducted from realized P&L, and paper-trade fills are adjusted for realistic slippage. A handful of portfolio-level caps prevent any single bad day from doing catastrophic damage.

---

## 2. Capital & risk budget

| Concept | Value | Meaning |
|---|---|---|
| Total capital (paper) | ₹500,000 (≈5 agents × ₹100k each) | Used for portfolio-level limits |
| Risk per trade | **0.5%** of capital | "1R" = ₹500 on a ₹100k pool |
| Risk-reward target | 2.5:1 | Win bigger than you lose |

If I take 10 consecutive losers in a row, drawdown is capped at 5% — fully recoverable.

---

## 3. Position sizing — the formula

```
quantity = (capital × 0.5%) / |entry − stop_loss|
```

Volatile stocks get smaller positions, calm stocks get larger ones — same rupee risk.

A notional ceiling caps any single position at **7% of capital** (safety floor for stocks with tiny SLs).

### Worked example

Capital ₹100,000, buying ADANIENT at ₹2,600:
- Stop-loss distance (4% based on ATR): ₹104
- Risk-budget per trade: ₹500
- Quantity: ₹500 / ₹104 = 4 shares (capped to 2 by the 7% notional ceiling)
- Position value: ₹5,200 (5.2% of capital)

---

## 4. Stop-loss & take-profit

| Trigger | Logic |
|---|---|
| **Stop-loss** | ATR-based — 1.2× to 2.0× the stock's 14-day ATR, clamped between 4% and 8% |
| **Take-profit** | 2.5× the stop-loss distance (so risking 4% targets 10% gain) |
| **Trailing stop** | After +1R profit, an additional 4% giveback from the peak kicks in. Ratchets up only. |
| **Time stop** | Position auto-closes if held too long: intraday 1 day, sentiment 15 days, proactive 21 days, technical 30 days |

---

## 5. Charges (Zerodha 2026 rates) — both legs deducted

| Charge | Delivery (CNC) | Intraday (MIS) |
|---|---|---|
| Brokerage | Free | min(0.03%, ₹20) |
| STT | 0.1% on buy + sell | 0.025% on sell only |
| Exchange txn | 0.00345% | same |
| GST | 18% of (brokerage + txn) | same |
| Stamp duty | 0.015% buy side | same |
| SEBI | ₹10 per crore | same |

**Round-trip cost example:** BUY 100 RELIANCE @ ₹1,000, SELL @ ₹1,050 → **~₹230 in charges**, so a ₹5,000 gross profit becomes a ₹4,770 net profit.

---

## 6. Slippage (paper-trade realism)

Paper fills are adjusted by a square-root market impact model so paper P&L tracks what live execution would actually deliver — typically 1–15 bps per leg for NIFTY 50 retail-scale orders.

```
slippage_bps = 0.5 × volatility × √(quantity / 20-day-avg-volume)
```

BUYs fill higher, SELLs fill lower.

---

## 7. P&L flow

```
Signal → Risk-sized quantity → Fill at (mid ± slippage)
                                            ↓
                              Position opens with entry_charges recorded
                                            ↓
                                  (time passes, SL/TP/trail/time-stop triggers)
                                            ↓
                          NET P&L = (exit − entry) × qty − entry_charges − exit_charges
```

The dashboard now shows honest net P&L. Before a fix on 2026-05-30 it overstated by ~₹100/trade (only the exit leg was being deducted).

---

## 8. Portfolio-level caps

| Cap | Limit | What it prevents |
|---|---|---|
| Per-position size | 7% of capital | One name blowing up the portfolio |
| Per-sector exposure | 30% of capital | Concentrated factor bets ("six 1% bets in Financials" = one 6% bet) |
| Gross deployment | 70% of capital | No 100% deployment — always keep dry powder |
| Per-agent open positions | 7 | Slot discipline |
| Daily loss limit | 3% of capital | Hard intraday stop — no more entries today |
| Max drawdown | 15% from peak | Strategic kill switch |
| Cooldown after close | Until next 9:15 IST | Stops the "buy → SL → re-buy → SL" death spiral |
| NIFTY regime filter | Halve sizes if NIFTY down >1%; block new BUYs if down >1.5% | Avoid catching falling knives |

---

## 9. Measuring edge — R-multiples, not rupees

Rupee P&L can hide an asymmetric loss distribution. The system tracks **R-multiples** instead:

- A win that closes at +2× the SL distance = **+2R**
- A stop-out = **−1R**
- A gap-down past SL = **−1.5R or worse**

**Expectancy** = `hit_rate × avg_winner_R − (1 − hit_rate) × |avg_loser_R|`

A strategy is **deployable** when expectancy ≥ **0.2R** over at least **30 trades**. Below that, slippage variance alone can eat the edge.

| Metric | Healthy | Suspicious |
|---|---|---|
| Hit rate | > 50% | < 40% (need payoff > 2 to compensate) |
| Payoff ratio (avg win / avg loss in R) | > 1.5 | < 1.0 |
| Profit factor (gross wins / gross losses) | > 1.5 | > 3.0 (probably overfit) |

---

## 10. A complete trade — end to end

Capital ₹100,000. ProactiveAgent fires BUY on ADANIENT at ₹2,600.

1. **Sizing**: SL = 4% → ₹2,496. Quantity = 2 shares (notional cap binds). Position ₹5,200 = 5.2% of capital.
2. **Fill**: Slippage 1 bps → fill price ₹2,600.26. Entry charges ₹6.20 stored on the position.
3. **Days 1–6**: Price runs to ₹2,730 (+5%). Trail activates at +1R (₹2,704). Effective SL ratchets to ₹2,620.80.
4. **Day 7**: Price retraces to ₹2,615 (below trail). Auto-closes.
5. **Exit fill**: ₹2,614.74 after slippage. Exit charges ₹5.44.
6. **Net P&L**: (₹2,614.74 − ₹2,600.26) × 2 = ₹28.96 gross, minus ₹11.64 total charges = **+₹17.32** (= +0.035R).

Without the trailing stop, that trade would have closed at the original ₹2,496 SL for a ₹−208 loss. The trail captured **+₹17** instead of bleeding ₹208.

---

That's the financial guts of it. Every percentage and threshold above is configurable; the values shown are the current production defaults.
