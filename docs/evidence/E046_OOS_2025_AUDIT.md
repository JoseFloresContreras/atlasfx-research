# E046 OOS 2025 Evaluation — Look-Ahead Bias Audit

**Date**: 2026-02-21  
**Status**: COMPLETE — 6/6 models evaluated  
**Verdict**: Look-ahead bias ELIMINATED. All models UNPROFITABLE.

---

## 1. Executive Summary

E046 retrained 6 models (3 pairs × 2 seeds) **from scratch** after fixing the
critical look-ahead bias discovered in the previous session. All code paths now
use `feature_data[T-1]` instead of `feature_data[T]`, ensuring the agent only
sees information available at the time of decision.

**Result**: All 6 models are catastrophically unprofitable on OOS 2025. The
E045 "edge" was **entirely** from look-ahead bias.

---

## 2. OOS 2025 Results

| Model | Sharpe (daily) | Return | MaxDD | Trades | Win Rate | Profit Factor |
|-------|---------------|--------|-------|--------|----------|--------------|
| A01_eurusd_s40 | -31.98 | -91.6% | 91.8% | 222,899 | 54.0% | 0.940 |
| A03_usdjpy_s40 | -84.16 | -95.8% | 95.8% | 250,521 | 51.6% | 0.930 |
| A07_usdchf_s40 | -47.44 | -98.2% | 98.2% | 213,389 | 48.7% | 0.870 |
| B01_eurusd_s50 | -35.25 | -92.7% | 92.7% | 220,396 | 49.9% | 0.930 |
| B03_usdjpy_s50 | -36.64 | -94.9% | 94.9% | 204,975 | 37.7% | 0.900 |
| B07_usdchf_s50 | -56.11 | -97.6% | 97.6% | 212,978 | 48.8% | 0.900 |
| **MEAN** | **-48.60** | **-95.1%** | **95.2%** | **220,860** | **48.4%** | **0.912** |

---

## 3. E045 vs E046 Comparison (Same Pairs)

| Pair | E045 Sharpe (look-ahead) | E046 Sharpe (causal) | Delta |
|------|--------------------------|---------------------|-------|
| EURUSD (s40) | +1.90 | -31.98 | -33.88 |
| USDJPY (s40) | +12.12 | -84.16 | -96.28 |
| USDCHF (s40) | +13.97 | -47.44 | -61.41 |
| EURUSD (s50) | +2.27 | -35.25 | -37.52 |
| USDJPY (s50) | +2.74 | -36.64 | -39.38 |
| USDCHF (s50) | +14.49 | -56.11 | -70.60 |

The performance collapse from positive to deeply negative is the **definitive
evidence** that look-ahead bias was the sole source of apparent profitability.

---

## 4. Code Audit — Causal Lag Verification

All 7 checks **PASS**:

| Check | Pattern | Status |
|-------|---------|--------|
| Feature lag | `feature_lag_idx = max(current_idx - 1, 0)` | PASS |
| Features use lag | `feature_data[feature_lag_idx]` | PASS |
| ATR lag in step() | `atr_lag_idx = max(current_idx - 1, 0)` | PASS |
| ATR in obs lagged | `atr_data[symbol][feature_lag_idx]` | PASS |
| Latent states lagged | `latent_states[feature_lag_idx]` | PASS |
| Forecasts lagged | `forecasts[feature_lag_idx]` | PASS |
| No unlagged access | No `feature_data[current_idx]` found | PASS |

---

## 5. Overtrading Analysis

All models exhibit extreme overtrading:

- **Trade frequency**: 55-67% of all bars have a trade
- **Average duration**: 1.5-1.8 bars (scalping noise)
- **Total trades**: 204K-250K per model over 373K bars
- **Win rate**: 37-54% (near random / slightly below)
- **Risk/Reward**: 0.77-1.45 (mostly < 1, losses bigger than wins)

The agent has learned to **trade every bar** but has **no directional edge**.
This is consistent with a model that never converged to a useful policy.

---

## 6. Diagnosis

### Why are all E046 models failing?

1. **E045's edge was fake**: The look-ahead bias gave the agent access to the
   current bar's close/high/low before deciding to trade at the open. This is
   equivalent to having a crystal ball — any model can be profitable with
   future information.

2. **No convergence**: Training Sharpe remained deeply negative throughout
   750 episodes. Best episode-level Sharpe peaked at 35-54 briefly (noise)
   but ended at -40 to -83. The models never found a profitable strategy.

3. **Feature inadequacy**: Current features (RSI, MACD, Bollinger, etc.
   computed on close prices) may not contain actionable predictive signal
   when properly lagged. The 1-bar lag means the agent sees yesterday's
   technical indicators, which may have zero predictive power for today's move.

4. **Cost death spiral**: With 200K+ trades and negative expectancy per trade,
   transaction costs (commission + spread + slippage) compound the losses.

---

## 7. Recommendation

**DO NOT run remaining E046 experiments.** Training 12 more models with the same
configuration will produce the same result. The problem is structural, not
statistical.

### Before resuming training, investigate:

| Priority | Issue | Potential Fix |
|----------|-------|---------------|
| P0 | Overtrading | Add trade frequency penalty to reward function |
| P0 | No signal | Verify features have predictive power when lagged |
| P1 | Reward design | Penalize per-bar trading; reward holding profitable positions |
| P1 | Action space | Add minimum hold period (e.g., force flat for N bars after action) |
| P2 | Training budget | May need >>750 episodes if learning from scratch |
| P2 | Architecture | Tune SAC hyperparameters for low-signal environments |

---

## 8. Files

- Audit script: `scripts/audit_e046_lookahead.py`
- OOS results: `results/oos_2025_e046/`
- Training logs: `logs/e046_*/`
- Environment fix: commit `43fd728` in `trading_env3.py`
