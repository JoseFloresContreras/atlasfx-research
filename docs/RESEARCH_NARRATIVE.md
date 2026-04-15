# Research Narrative

This document traces the arc of the AtlasFX research program in chronological order: what was tried, what was learned, and why the project concluded with a negative result. It is intended as a readable companion to the distilled findings in [KEY_FINDINGS.md](KEY_FINDINGS.md) and the raw experiment record in [SAC_EXPERIMENT_LOG.md](SAC_EXPERIMENT_LOG.md).

---

## Phase 1: Baselines and Early Exploration (Nov–Dec 2025)

The project began with the question of whether a Soft Actor-Critic agent could learn a profitable short-term forex trading policy. Early experiments (E001–E012) explored basic SAC configurations on USDJPY 1-minute bars. Most failed outright — poor convergence, unstable rewards, catastrophic drawdowns. A handful of "MEGA" configurations appeared promising during training but collapsed under extended evaluation, revealing that training-set performance was not predictive.

By mid-December, a simpler baseline approach (E013–E014) produced stable positive returns on validation data: +16.7% and +38.6% mean return. These became the v1/v2 baselines. Attempts to improve them through trailing stops and TP/SL modifications (E015) degraded performance, and a subsequent analysis (A001–A002) revealed the agent was using ultra-tight stop-losses (2.3-pip median) with only a 1.5× TP/SL ratio — a scalping style with narrow per-trade edge.

## Phase 2: Multi-Pair Expansion (Dec 2025–Jan 2026)

Success on USDJPY prompted expansion to multiple currency pairs. E016 achieved +38.0% portfolio return (Sharpe 4.84) across 3 pairs, and E020 extended to 6 symbols. A USDCHF investigation (A004) identified pair-specific failure modes, leading to its exclusion in the v3 baseline.

A portfolio-level agent (E018) failed severely — −17.9% with 0% win rate on test data — demonstrating that single-pair success did not generalize to multi-pair joint training.

## Phase 3: The Reproducibility Crisis (Jan–Feb 2026)

Experiments E027 through E033 — seven consecutive attempts — failed to reproduce the earlier multi-pair results. Each attempt tried a different fix; each failed for a different reason. A systematic diagnosis in E034 traced the failures to seven independent root causes operating simultaneously: network architecture drift, loss penalty factor drift, incorrect training mode, destructive random warmup, a hardcoded action penalty, shared output directory corruption, and production safety caps applied during training.

Fixing all seven restored reproducibility: E034 achieved 87.3% return with Sharpe 3.56. The episode is documented as Finding #8 in [KEY_FINDINGS.md](KEY_FINDINGS.md).

## Phase 4: Apparent Breakthroughs (Feb 2026)

With reproducibility restored, the project entered its most productive phase. Multi-symbol validation (E035–E037) showed cross-seed consistency. Test-set evaluation (E038) confirmed positive Sharpe on held-out data. Seven-pair exploration (E041) found all 7 pairs profitable. Multi-pair agents (E042–E043) produced the highest results in the project's history: Sharpe 26.79 on 3-pair training.

These results appeared to confirm a genuine trading edge.

## Phase 5: Look-Ahead Bias Discovery (Feb 2026)

A code audit of the observation function revealed that `_get_observation()` was using `feature_data[T]` instead of `feature_data[T-1]`, giving the agent access to the current bar's close, high, and low at decision time. This is a textbook look-ahead bias — and it explained the anomalously strong results.

After fixing the lag, experiment E046 retrained 6 models from scratch. All were catastrophically unprofitable: mean daily Sharpe fell to −48.60, with mean returns of −95.1%. The Phase 4 edge was entirely artificial. The audit trail for this finding is preserved in [evidence/E046_OOS_2025_AUDIT.md](evidence/E046_OOS_2025_AUDIT.md).

## Phase 6: Post-Fix Evaluation and Cost Analysis (Feb 2026)

With the look-ahead fix in place, the best surviving model (B01 V2b s42, USDJPY) was re-evaluated on truly out-of-sample 2025 data. A canonical evaluation framework (CANON_EVAL_V2) was built to ensure deterministic, reproducible results.

The champion checkpoint generated +$14,105 in gross PnL over 260 trading days — evidence that the agent learned a real, if narrow, trading signal. But transaction costs ($14,817) consumed all of it, leaving net PnL at −$712 (Sharpe −0.87). The full cost analysis is in [COST_MODEL.md](COST_MODEL.md).

During this evaluation, a double-counting bug in exit slippage was discovered: the `mode=both` setting charged slippage through two independent channels simultaneously, overstating cost impact by approximately 2×. After correction to `mode=price_only`, the canonical numbers were re-established. See [evidence/CANON_EVAL_V2.md](evidence/CANON_EVAL_V2.md).

A slippage sensitivity analysis showed the model requires entry slippage below approximately μ=0.07 pips to break even — below typical institutional execution thresholds.

## Phase 7: Final Attempts and Stop Decision (Feb 2026)

Two categories of recovery were attempted:

1. **Hour-of-day filtering**: Restricting trades to historically positive-EV hours turned losses into gains in-sample, but out-of-sample validation showed hourly EV patterns did not persist (50% sign agreement — equivalent to a coin flip).

2. **Anti-overtrading controls (E052)**: Sniper-style cooldown gates and turnover penalties were tested across 5 seeds × 2 configurations. Every combination failed every quality gate.

These results closed the research program. The stop decision was evidence-based: no further hyperparameter or gating modification was expected to overcome the structural cost barrier at 1-minute trading frequency.

## Conclusion

AtlasFX answered its research question: a SAC agent cannot profitably trade 1-minute USDJPY under realistic transaction costs with the architecture and feature set used. The agent learned a genuine gross signal, but the margin was too narrow to survive execution costs at the required trade frequency (~160 trades/day).

The project's contribution is methodological. It provides concrete, quantified examples of look-ahead bias, cost-model accounting errors, seed fragility, selection bias, and reproducibility failure — each a known hazard in applied ML for finance, each documented with audit trails and corrective evidence.

For the distilled findings, see [KEY_FINDINGS.md](KEY_FINDINGS.md). For the complete raw experiment record, see [SAC_EXPERIMENT_LOG.md](SAC_EXPERIMENT_LOG.md).
