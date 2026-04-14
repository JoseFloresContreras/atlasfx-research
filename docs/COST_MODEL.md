# Cost Model

## Overview

AtlasFX is a Soft Actor-Critic (SAC) reinforcement-learning agent trained to trade USDJPY on 1-minute bars. The agent generates genuine gross alpha — +$14,105 in gross PnL over 260 out-of-sample trading days — but transaction costs consume effectively all of it. Under the corrected canonical evaluation (CANON_EVAL_V2), the champion model (B01 V2b s42, checkpoint ep899) finishes approximately breakeven to slightly unprofitable.

This document explains the cost model, the evaluation correction that revealed the problem, and the sensitivity of net performance to cost assumptions.

---

## 1. Cost Component Definitions

### 1.1 Commission

| Parameter | Value |
|---|---|
| Rate | $2.50 per standard lot per side |
| Application | Deducted from account balance at trade open and trade close |

This is the commission assumption used throughout the project's baseline cost model.

### 1.2 Spread

| Parameter | Value |
|---|---|
| Size | 0.20 pips (fixed) |
| Application | Deducted as a cost at entry; does not adjust the fill price |

The spread cost is included in the `slippage_usd` accounting bucket alongside entry slippage. It is computed as:

$$\text{spread\_cost} = \text{spread\_pips} \times \text{pip\_value} \times \text{lot\_size}$$

### 1.3 Entry Slippage

| Parameter | Value |
|---|---|
| Distribution | Half-normal |
| Mean (μ) | 0.10 pips |
| Std dev (σ) | 0.05 pips |
| Application | Sampled per trade; deducted from balance as a cost (does not adjust fill price) |

Entry slippage is drawn from a half-normal distribution (always ≥ 0) and added to the `slippage_usd` bucket.

### 1.4 Exit Slippage

Exit slippage applies to stop-loss fills. In `price_only` mode, it adjusts the execution price rather than being recorded as a separate cost line. A diagnostic run (CANON_EVAL_V2, Task 2) tested all four exit-slippage modes on ep899 with `exit_sl_multiplier=1.5`:

| Mode | Trades | Gross PnL | Total Costs | **Net PnL** | PF net | Sharpe |
|---|---|---|---|---|---|---|
| **OFF** (no exit slip) | 41,767 | $14,105 | $14,817 | **−$712** | 0.986 | −0.87 |
| **price_only** | 41,792 | $9,872 | $12,906 | **−$3,034** | 0.935 | −4.33 |
| **cost_only** | 41,764 | $14,123 | $17,819 | **−$3,696** | 0.931 | −0.86 |
| **both** (old default) | 41,792 | $9,872 | $15,512 | **−$5,640** | 0.884 | −4.33 |

Impact of each channel versus OFF:

| Delta | Value |
|---|---|
| Δ(price_only − OFF) | −$2,322 — exit slip via worse fill price |
| Δ(cost_only − OFF) | −$2,985 — exit slip via separate cost deduction |
| Δ(both − OFF) | −$4,928 — both channels combined |
| Predicted if additive | −$5,306 |
| Actual (both − OFF) | −$4,928 (close; small gap from CRN drift) |

**Conclusion**: `mode=both` approximately double-counts exit slippage. All canonical evaluation runs use `mode=price_only`.

---

## 2. Corrected Baseline Results

All results below are from **CANON_EVAL_V2** (Task 3): a deterministic evaluation (seed=42) of checkpoint ep899 over the full OOS-2025 dataset (373,487 bars, ~260 trading days). The baseline configuration uses `mode=price_only` but disables exit slippage entirely; costs include commission, spread, and entry slippage only.

| Metric | Value |
|---|---|
| Gross PnL | +$14,105 |
| Total costs | $14,817 |
| **Net PnL** | **−$712** |
| Sharpe ratio | −0.87 |
| PF (net) | 0.986 |
| WR (net) | 45.4% |
| Trades | 41,767 |
| Avg trades/day | ~160 |

With exit slippage enabled (`price_only`, SL=1.5×), net PnL drops to −$3,034 (Sharpe −4.33), reflecting the additional −$2,322 impact quantified in §1.4.

The agent's high-frequency, narrow-edge style means that even small per-trade cost changes compound across ~41,800 trades to produce large swings in net performance.

---

## 3. Sensitivity Analysis

The slippage calibration grid (SLIPPAGE_SENSITIVITY_ANALYSIS, Task 2) evaluated the same checkpoint under alternative entry-slippage assumptions, with exit slippage disabled:

| Scenario | Entry μ (pips) | Entry σ (pips) | Net PnL | Sharpe | Total Costs |
|---|---|---|---|---|---|
| **Low-cost** | 0.05 | 0.03 | +$408 | +0.51 | $14,439 |
| **Baseline** | 0.10 | 0.05 | −$695 | −0.85 | $14,820 |
| Higher sigma | 0.10 | 0.10 | −$1,298 | −1.66 | $15,037 |
| Higher mean | 0.15 | 0.05 | −$1,444 | −1.87 | $15,082 |

The baseline result (−$695) is consistent with CANON_EVAL_V2's −$712 within $17, confirming reproducibility across independent evaluation runs.

Key observations:

- **Break-even slippage**: Interpolating between the low-cost and baseline scenarios, the strategy breaks even at approximately μ ≈ 0.07 pips entry slippage (all other costs held constant). This estimate is interpolated and has not been validated with a dedicated evaluation run.
- **Entry slippage sensitivity is moderate**: Doubling σ (0.05 → 0.10) costs ~$600 extra; raising μ (0.10 → 0.15) costs ~$750 extra.
- **Exit slippage dominates**: The −$2,322 impact of SL=1.5× exit slippage (§1.4) exceeds the impact of any entry-slippage variation tested.
- **Wider spread assumptions**: Given that the corrected baseline is already near breakeven, wider spread assumptions would push the strategy further into negative territory.
- **Gross alpha is real but narrow**: Gross PnL remains positive (+$14,105) across all scenarios. The margin is simply too thin to survive the baseline cost assumptions at this trade frequency.

---

## 4. Practical Conclusion

The cost model invalidates the strategy as currently configured. Two paths to viability exist in principle:

1. **Reduce trade frequency** — Fewer but higher-conviction trades would lower cumulative costs while preserving per-trade edge.
2. **Lower effective per-trade costs** — Achieving entry slippage below μ ≈ 0.07 pips would shift the strategy into positive territory, but this requires execution infrastructure improvements beyond the scope of the simulation.

A secondary checkpoint (ep774) finishes at net −$80 (Sharpe −0.07) under the same corrected evaluation, suggesting that the trained policy space includes near-breakeven configurations — but none that are robustly profitable after costs.

---

## 5. Remaining Caveats

1. **Evaluation discrepancy**: An earlier evaluation of the same checkpoint under the original (pre-correction) codebase produced materially different results. The gap (~$4,800 in net PnL) is attributed to code changes between evaluation runs (exit-slippage infrastructure, dead-zone logic, cooldown parameters). CANON_EVAL_V2 is treated as canonical because two independent post-correction evaluations agree within $17.

2. **Interpolated break-even**: The μ ≈ 0.07-pip break-even threshold is a linear interpolation between two evaluated scenarios, not a directly measured value.

3. **Missing cost decomposition**: CANON_EVAL_V2 reports aggregate costs ($14,817) but does not provide a per-component breakdown (commission vs. spread vs. slippage). The closest available decomposition is from an earlier, superseded evaluation and is not directly comparable.
