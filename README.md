# AtlasFX

**Algorithmic Forex Trading Research — A Negative-Result Case Study in Deep Reinforcement Learning**

## Summary

AtlasFX is a multi-month applied research project that investigated whether a Soft Actor-Critic (SAC) reinforcement learning agent could profitably trade short-term forex under realistic execution costs.

The final conclusion is **no**. Under the corrected evaluation framework, the project found signs of gross trading signal, but not a trading edge that survives commissions, spread, and slippage robustly enough to support a profitability claim. The repository is therefore presented not as a trading product, but as a documented research program in hypothesis testing, leakage prevention, reproducibility, and cost-aware evaluation.

Across more than 50 experiments and approximately 280 training runs, AtlasFX evolved from early baseline building into a broader investigation of RL fragility in financial markets: look-ahead bias, configuration drift, seed sensitivity, selection bias, and the gap between gross and net performance.

## Research Question

> Can a deep reinforcement learning agent learn a 1-minute FX trading policy that remains profitable after realistic transaction costs on truly out-of-sample data?

## Final Conclusion

The hypothesis was invalidated.

AtlasFX found evidence that the agent can learn a repeatable **gross** trading signal under this setup. However, under the corrected canonical evaluation, baseline execution costs consume essentially all of that margin, leaving the strategy approximately breakeven to slightly unprofitable. Under more adverse cost assumptions, the system becomes clearly unprofitable.

The value of the project is therefore methodological rather than commercial: it documents how a serious ML research program can become more rigorous over time and still arrive at a negative result.

## Key Findings

- **Look-ahead bias was the decisive failure mode in the strongest early results.** A temporal indexing bug exposed same-bar information at decision time; once corrected, the apparent edge collapsed.
- **Transaction costs were the binding constraint.** The best surviving configuration generated gross alpha, but not enough to survive realistic commissions, spread, and slippage at the trading frequency used.
- **Seed fragility was extreme.** The same SAC configuration could move from strongly positive to deeply negative performance across seeds.
- **Reproducibility had to be earned.** A multi-experiment reproducibility crisis was traced to multiple simultaneous sources of configuration drift and pipeline inconsistency.
- **Iterative tuning created selection pressure on holdout data.** The project explicitly documents this rather than treating later holdout-era gains as clean evidence.
- **The stop decision was evidence-based.** Final attempts to recover the edge through filtering and anti-overtrading controls failed to produce a robust net-profitable result.

For the full distilled findings, see [docs/KEY_FINDINGS.md](docs/KEY_FINDINGS.md).

## Architecture Overview

AtlasFX combines a multi-stage research pipeline:

- **Data pipeline:** tick-level FX data → fixed-interval bars → engineered features → normalized splits
- **Feature engineering:** price, volume, microstructure, technical, volatility, session, temporal, and cross-asset features
- **RL agent:** Soft Actor-Critic with continuous actions for trading behavior
- **Trading environment:** cost-aware simulated execution with commissions, spread, slippage, stop-loss logic, and risk controls
- **Evaluation stack:** deterministic canonical evaluation, sensitivity analysis, and post-audit re-evaluation

For technical architecture details, see the module docstrings and `docs/` folder.

## Data and Evaluation

- **Universe:** 7 major FX pairs
- **Primary research focus:** USDJPY at 1-minute resolution
- **Training-era data:** 2021–2024
- **Final out-of-sample evaluation:** 2025, processed separately using frozen training-period artifacts
- **Pipeline discipline:** chronological splits, train-only fitted statistics, frozen normalization, causal feature alignment after audit corrections

Raw tick data is **not** included in the public repository. The repo focuses on code, configuration, artifacts, and documentation needed to reproduce the workflow from equivalent raw inputs.

For details, see [docs/DATA.md](docs/DATA.md).

## Why This Project Matters

AtlasFX is useful as a portfolio project because it demonstrates more than model training:

- rigorous applied ML experimentation under uncertainty
- detection and correction of leakage in time-series systems
- debugging of reproducibility failures in RL pipelines
- separation of gross and net performance under realistic cost assumptions
- evidence-based invalidation of a plausible but unsupported hypothesis

This is not a polished success story. It is a technically serious research record.

## Repository Structure

```text
atlasfx-research/
├── src/atlasfx/              # Core package: environments, models, evaluation, training, risk
├── config/                   # Production cost envelope configs
├── docs/                     # Research documentation, evidence trail, and experiment log
├── scripts/                  # Training, evaluation, and canonical eval scripts
├── tests/                    # Unit and integration tests
├── pyproject.toml
├── requirements.txt
└── README.md