# Key Findings

AtlasFX is a research project that explored whether a Soft Actor-Critic (SAC)
reinforcement learning agent could profitably trade forex at 1-minute resolution
with realistic transaction costs. Over more than 50 experiments and approximately
280 training runs spanning six months, the original hypothesis was systematically
tested and ultimately invalidated. This document distills the most important
findings from that process — covering look-ahead bias detection, cost modeling,
training stability, and the methodological pitfalls of applied RL in finance.

The complete experiment record is preserved in
[docs/SAC_EXPERIMENT_LOG.md](SAC_EXPERIMENT_LOG.md).

---

## 1. Look-Ahead Bias Was the Sole Source of Apparent Profitability

Experiment E045 produced models with daily Sharpe ratios as high as +14.49 on
unseen 2025 data — results that appeared to confirm a genuine trading edge. A
subsequent code audit discovered that the observation function used
`feature_data[T]` instead of `feature_data[T-1]`, giving the agent access to the
current bar's close, high, and low before deciding to trade at the open. Empirical
verification showed RSI–intrabar-move correlations of 0.75–0.80, confirming
information leakage.

After fixing the lag (E046), all 6 retrained models were catastrophically
unprofitable: mean daily Sharpe fell to **−48.60**, with mean returns of −95.1%.
The E045 "edge" was entirely artificial.

**Why it matters:** Look-ahead bias can hide behind a single missing index offset
and still produce convincingly realistic-looking backtest results. Independent
code audits of the observation pipeline are essential in any RL-for-finance
project.

**Sources:** E046_OOS_2025_AUDIT.md, OOS_2025_AUDIT.md, OOS_2025_RESULTS.md,
docs/SAC_EXPERIMENT_LOG.md (E045, E046)

---

## 2. Transaction Costs Consume Over 75% of Gross Alpha at 1-Minute Scale

The best surviving model (B01 V2b s42, USDJPY 1-min) generated $17,183 in gross
PnL over 259 trading days on a $10,000 account, but $13,028 (75.8%) was consumed
by transaction costs — commissions accounting for 42.3% and entry slippage for
57.7% of total costs. Net expected value per trade was $0.10 against $0.31 in
per-trade costs, across approximately 41,700 trades.

Under re-evaluation with different stochastic slippage draws, the same model's
net PnL shifted from +$4,155 to −$695, illustrating that net performance sits
within the noise band of the cost assumptions.

**Why it matters:** At 1-minute trading frequency (~160 trades/day), even a
moderate gross edge is overwhelmed by realistic execution costs. This is a
structural constraint, not a tuning problem.

**Sources:** BASELINE_B01_V2b_s42.md, SLIPPAGE_SENSITIVITY_ANALYSIS.md

---

## 3. Exit Slippage Was Double-Counted, Requiring Correction of Prior Analyses

A deterministic canonical evaluation discovered that the
`exit_slippage_mode=both` setting charged exit slippage through two independent
channels: a degraded fill price (reducing gross PnL by $2,322) and a separate
cost line item (adding $2,985 in costs). The combined impact (−$4,928) was nearly
the sum of the two individual channels applied independently. After correction to
`mode=price_only`, exit slippage impact at SL×1.5 was −$2,322 — roughly half the
previously reported figure.

This required retroactive correction of results in the slippage sensitivity
analysis, where exit slippage impact had been overstated by approximately 2×.

**Why it matters:** Cost model accounting bugs can propagate silently through an
analysis pipeline. Canonical evaluation frameworks with explicit mode isolation
are necessary to catch them.

**Sources:** CANON_EVAL_V2.md

---

## 4. Break-Even Slippage Falls Below Institutional Execution Thresholds

A slippage calibration grid showed the model requires entry slippage below
approximately μ=0.07 pips to achieve positive net returns — below what most
institutional ECN venues typically deliver. At the baseline assumption (μ=0.10,
σ=0.05 pips), the model loses −$695. Only at an optimistic low-cost scenario
(μ=0.05, σ=0.03) does it turn marginally profitable (+$408 over 259 days on
$10,000).

The full sensitivity range spans from +$408 (low cost) to −$7,975 (aggressive
worst-case), confirming that the model's edge is narrower than the uncertainty
band of the cost assumptions.

**Why it matters:** A trading strategy whose profitability depends on slippage
being at the extreme low end of realistic estimates is not deployable. This
finding sets a concrete, falsifiable execution threshold.

**Sources:** SLIPPAGE_SENSITIVITY_ANALYSIS.md, BASELINE_B01_V2b_s42.md

---

## 5. Seed Fragility Produces 70-Point Sharpe Swings

In experiment E044, the same 3-pair SAC configuration trained on the same data
with different random seeds produced daily Sharpe ratios of +26.79 (seed 314),
−43.23 (seed 42), and −41.15 (seed 2024) — a span of 70 points. Ten out of
eleven E044 sub-experiments failed; only one converged successfully. A prior
"breakthrough" result (E043, Sharpe +26.79) was revealed to be a favorable seed
draw rather than a robust policy.

Across the later V2-family configs on USDJPY (post look-ahead fix), only 2 out
of 6 seed–config combinations showed positive Sharpe on held-out 2025 data.

**Why it matters:** Single-seed RL results in finance are unreliable. Reporting
best-seed performance without multi-seed statistics overstates the true
distribution and should be treated as anecdotal, not as robust evidence.

**Sources:** docs/SAC_EXPERIMENT_LOG.md (E044),
docs/SECTION_D_RL_TRAINING_STABILITY.md

---

## 6. Iterative Config Search Created Measurable Selection Bias

The progression from V1 → V2 → V2a → V2b involved four sequential hyperparameter
refinements, each validated on OOS 2024 test data. This constitutes backtest
overfitting via iterative search: OOS 2024 was used as a de facto validation set
for approximately 280 training runs, eroding its test-set integrity.

The 2025 OOS evaluation partially confirmed this concern: only 2 out of 6 USDJPY
seed–config combinations showed positive Sharpe on the truly unseen 2025 data.
One seed achieved Sharpe +6.34 on OOS 2024 and collapsed to −3.25 on OOS 2025.

**Why it matters:** When hyperparameters are iteratively tuned against a test set,
that set becomes a validation set. Genuine out-of-sample evaluation requires data
that was never consulted during development. This project introduced OOS 2025
after all configs were locked to partially mitigate the issue, but the sample was
too small for definitive conclusions.

**Sources:** docs/SECTION_D_RL_TRAINING_STABILITY.md, docs/SAC_EXPERIMENT_LOG.md

---

## 7. Hour-of-Day Filtering Is an Overfitting Artifact

In-sample hour filtering transformed a losing model into a profitable one:
restricting trades to the top 5 expected-value hours turned a −$3,034 net loss
into a +$543 gain. However, out-of-sample validation showed that hourly EV
patterns do not persist: sign agreement between 2024-selected and 2025-evaluated
hours was 12 out of 24 (50% — a coin flip). Within 2025, H1-to-H2 sign agreement
was 7 out of 24 (29% — worse than random).

The top-5 hour selection from 2024 produced a −$209 loss on 2025, underperforming
the unfiltered baseline of −$80.

**Why it matters:** Post-hoc time-of-day filters are a common source of
overfitting in trading systems. This finding provides concrete OOS evidence that
hourly EV structure in forex is non-stationary and cannot be relied upon for trade
selection.

**Sources:** CANON_EVAL_V2.md (Tasks 3–4)

---

## 8. Seven Consecutive Failures Traced to Seven Independent Root Causes

Experiments E027 through E033 — seven consecutive attempts — failed to reproduce
earlier successful multi-pair results. A systematic diagnosis in E034 traced the
failures to seven independent root causes: network architecture drift
([256,256] → [512,512,256]), loss penalty factor drift, incorrect training mode
(from-scratch instead of transfer learning), destructive random warmup, a
hardcoded action penalty, shared output directories causing data corruption, and
production safety caps applied during training.

Fixing all seven restored reproducibility: E034 achieved 87.3% return with
Sharpe 3.56.

**Why it matters:** RL training pipelines have numerous sources of hidden
non-determinism and parameter drift. A single uncontrolled change can make results
irreproducible, and multiple simultaneous drifts make root-cause isolation
extremely difficult. Systematic cataloging before continuing experimentation
prevented an indefinite cycle of unexplainable failures.

**Sources:** docs/SAC_EXPERIMENT_LOG.md (E027–E034)

---

## 9. Agent Action Magnitude Does Not Predict Trade Quality

A conviction-filtering analysis used the agent's requested position size as a
proxy for trade conviction. Counter to expectation, high-conviction trades
(above the 75th percentile of `units_desired`) had negative net PnL across every
time-filter combination tested. The best time filter (top 5 EV hours) degraded
from Sharpe +3.73 to +0.04 when combined with a high-conviction gate.

Over 50% of the agent's trades had a raw `units_desired` of zero (dead-zone
entries), revealing that the policy's edge came from position *continuation* and
timing rather than from confident new-entry signals.

**Why it matters:** In continuous-action RL for trading, action magnitude is not a
reliable confidence signal. The agent's alpha was embedded in *when* it traded,
not in *how much* it wanted to trade. This has implications for any RL system
where action scale is used as a proxy for model certainty.

**Sources:** SLIPPAGE_SENSITIVITY_ANALYSIS.md (Task 4)

---

## 10. Final Experiment Closed the Research Program

The last experiment in the series (E052) applied sniper-style cooldown gates and
turnover/flip penalties across 5 random seeds and 2 configurations. Every
combination failed every quality gate: median net PnL ranged from −$4,688 to
−$5,703, median daily Sharpe from −19.39 to −19.74, and median max drawdown
from 47% to 57%.

This result, combined with the broader body of experiments in the project,
closed the research program. No further hyperparameter or gating modification was
expected to overcome the structural cost barrier at 1-minute resolution.

**Why it matters:** The decision to stop experimenting was itself a research
outcome. Recognizing when a hypothesis has been adequately tested — and accepting
a negative result — is as important as any positive finding.

**Sources:** e052_1m_sniper_v2_report.md, docs/SAC_EXPERIMENT_LOG.md

---

## Conclusion

AtlasFX documents a six-month applied research effort that systematically tested
whether deep reinforcement learning can profitably trade forex at 1-minute
resolution under realistic transaction costs. The answer, supported by more than
50 experiments and approximately 280 training runs across 7 currency pairs, is
**no** — not with the current SAC architecture, feature set, and cost assumptions.

The project's value lies not in the trading model but in the rigor of the process:
the detection and correction of look-ahead bias, the discovery of a cost-model
double-counting bug, the quantification of seed fragility and selection bias, and
the resolution of a seven-experiment reproducibility crisis. Each of these is a
known hazard in applied ML for finance. This project provides concrete, quantified
examples of all of them.
