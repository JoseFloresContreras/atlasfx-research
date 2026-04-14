# Evidence Map — Audit Trail & Research Rigor

This index links to the strongest evidence that AtlasFX was a serious, self-critical research program. Each item represents a specific audit, test, or analysis that demonstrates methodological discipline beyond standard backtesting.

---

## Leakage Detection

- [E046 OOS 2025 Audit](evidence/E046_OOS_2025_AUDIT.md) — Controlled before/after proof that prior profitable results came from look-ahead bias; 6 models retrained from scratch after fixing T→T-1 lag all become catastrophically unprofitable.
- `scripts/audit_e046_lookahead.py` — Automated leakage detector using multiple independent verification methods: entry price vs bar open, feature lag checks, trade direction vs same-bar return correlation.
- `scripts/run_tick_feature_leakage_checks.py` — Perturbation-based feature leakage test: removes future ticks and verifies features remain identical, with temporal split boundary checks and normalization leakage detection.

## Cost Rigor

- [Canon Eval V2](evidence/CANON_EVAL_V2.md) — Discovered and corrected a double-counting bug in exit slippage that overstated cost impact by ~2×; re-ran the full cost grid to produce corrected canonical numbers.
- [Slippage Sensitivity Analysis](evidence/SLIPPAGE_SENSITIVITY_ANALYSIS.md) — Parametric cost-sensitivity sweep across 7 configurations and 2 checkpoints with post-hoc hour filters; identifies the break-even point between low-cost and baseline scenarios.
- `scripts/run_slippage_checkpoint_eval.py` — Evaluates multiple models across slippage configurations on OOS 2025 data with structured experiment grids.
- `scripts/loss_decomposition.py` — Decomposes losses into gross PnL vs commission vs slippage with per-trade averages and exit reason analysis.
- `scripts/audits/audit_cost_integration.py` — Audits whether the equity curve correctly includes costs by reconstructing it via both gross and net PnL paths.
- `scripts/audits/audit_sharpe_daily.py` — Double-recomputes the headline Sharpe ratio via two independent methods; values must match within 1e-6 tolerance.

## Alpha Existence Testing

- `scripts/alpha_existence_test.py` — Tests for signal existence using LogReg, LightGBM, and regression on 5m/15m USDJPY features with AUC/IC metrics and toy backtest under canonical costs.
- `scripts/alpha_existence_test_1m.py` — Same alpha existence methodology adapted for 1-minute bars with k={1,3,5} forward horizons.
- `scripts/analyze_worse_than_random.py` — Investigates why post-bias-fix models lose more than random trading: estimates random-trade cost, checks for systematic directional bias, and tests anti-predictivity.

## Execution Realism

- `scripts/audit_toy_vs_env_equivalence.py` — Cross-validates toy backtest against real environment execution using three execution models with explicit leakage checks and cost accounting reconciliation.
- `scripts/latency_execution_survival_map.py` — Sweeps latency (0/0.5/1/2 bars) × execution model (taker/maker-ideal/maker-adverse) to test whether alpha survives real-world infrastructure constraints.
- `scripts/run_tick_survival_map.py` — Multi-dimensional tick-level parameter sweep (Δt × delay × TTL × fill_prob × adverse_pips × hold_ms) with viability gate requiring PF≥1.05 in both time-split halves.
- [Cost Envelope A/B Testing](evidence/COST_ENVELOPE_AB_TESTING.md) — Deterministic record/replay A/B framework with byte-exact validation (assert_frame_equal with rtol=0, atol=0), eliminating CUDA non-determinism from evaluation.

## External Review

- [Expert Review Report](evidence/EXPERT_REVIEW_REPORT.md) — Full technical dossier prepared for external expert scrutiny; includes the central diagnosis that the agent learned to be a scalper with ~$0.41 edge on ~$0.31 costs per trade.

## Bug Forensics

- [Technical Fixes Master](evidence/TECHNICAL_FIXES_MASTER.md) — Consolidated record of critical bugs found and fixed, including a 48× position amplification bug (normalized ATR used instead of real), reward function errors, and data normalization leakage.
- [Hardening Matrix Causal](evidence/HARDENING_MATRIX_CAUSAL.md) — Root-cause analysis of Cost Envelope ROI degradation using controlled A/B experiments; identifies USDJPY as responsible for 96.6% of global freezes with precise PnL attribution.

## Negative Results

- [E052 Sniper V2 Report](evidence/E052_SNIPER_V2_REPORT.md) — Systematic multi-seed negative result: 2 configurations × 5 seeds × 2 exit slippage levels, all failing all performance gates. Documented with full transparency.
- [Baselines](evidence/BASELINES.md) — Official baseline definitions (v1/v2/v3) with reproducible configurations and analytical weakness identification showing poor TP/SL ratios in early architectures.

## Accounting Integrity

- `scripts/sanity_check_accounting.py` — Validates the fundamental equity identity (equity[t+1] = equity[t] × (1+r[t])), recalculates max drawdown independently, and checks for NaN/Inf contamination.
