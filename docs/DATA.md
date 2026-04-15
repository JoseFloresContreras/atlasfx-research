# Data

## Overview

This document describes the data pipeline used in the AtlasFX project: how raw tick data is transformed into normalized feature matrices consumed by the RL training environment, and how out-of-sample evaluation data is processed using frozen statistics to prevent information leakage.

The public repository contains the core source code (environments, models, training, evaluation, risk management) and documentation describing the pipeline methodology. Raw tick datasets, processed Parquet files, pipeline YAML configs, and frozen normalization artifacts are not redistributed — they are either large or were part of the working research environment. The pipeline design is documented here for transparency and reproducibility.

---

## 1. Scope and Purpose

The pipeline converts irregularly-spaced Level 1 tick data (bid, ask, volume) into fixed-interval klines with engineered features, ready for consumption by a Gymnasium-compatible RL environment. It is designed around three principles:

1. **Causal correctness** — No feature at time $T$ uses information from $T$ or later.
2. **No leakage between splits** — All fitting statistics (normalization, winsorization) are computed from training data only and frozen for reuse.
3. **Reproducibility** — YAML-driven configuration, deterministic ordering, and saved artifacts allow full reconstruction of any processed dataset.


---

## 2. Instruments, Timeframe, and Resolution

| Parameter | Value |
|---|---|
| Instruments | 7 major forex pairs: AUDUSD, EURUSD, GBPUSD, USDJPY, USDCAD, USDCHF, NZDUSD |
| Training period | 2021-01-04 to 2024-12-31 (4 years) |
| OOS evaluation period | 2025 calendar year (separate raw data, never mixed with training) |
| Raw format | Tick-by-tick: timestamp (ms), bid, ask, bid volume, ask volume |
| Primary resolution | 1-minute klines; 5-minute also generated for selected experiments |
| Approximate raw data size | Several tens of GB across all pairs and years |

The RL agent that produced the main results (B01 V2b s42) was trained and evaluated on USDJPY at 1-minute resolution.


---

## 3. Raw Inputs and Derived Datasets

### 3.1 Raw Tick Data

Each trading day produces one CSV file per pair with columns: `timestamp` (Unix ms), `askPrice`, `bidPrice`, `askVolume`, `bidVolume`. Tick frequency is irregular, ranging from approximately 20 ms during London/NY overlap to several hundred milliseconds during low-liquidity sessions.

### 3.2 Aggregated Klines

Phase 1 of the pipeline (merge → clean → aggregate) produces a single Parquet file per resolution. Per-symbol aggregators include: OHLC, volume, VWAP, spread, volatility, tick count, order flow imbalance (OFI), and micro price.

### 3.3 Feature-Engineered Outputs

Phase 2 adds engineered features (prefixed with `[Feature]`). The final outputs are Parquet files for each split (train, validation, test). The exact column count depends on the featurizer configuration and any downstream feature-selection steps.


---

## 4. Feature Pipeline

Features fall into eight categories:

| Category | Examples | Typical Window |
|---|---|---|
| Price-based | Log returns, mid price | 1 bar |
| Volume-based | VWAP, VWAP deviation | Aggregation window |
| Microstructure | Spread, OFI, micro price, tick count | Aggregation window |
| Technical indicators | RSI (14), Bollinger width (20) | 14–20 bars |
| Volatility | ATR (14), realized vol, bipower variation (30), volatility ratio (14/60) | 14–60 bars |
| Session flags | Sydney, Tokyo, London, New York | Timestamp-derived |
| Temporal | Minute-of-day (sin/cos), day-of-week | Timestamp-derived |
| Cross-asset | Pair correlations (30), cross-sectional imbalance | 14–30 bars |

All rolling-window features use only past data. Session flags and temporal features are derived deterministically from the timestamp.

A column-naming convention controls normalization: columns prefixed with `[Feature]` are normalized; unprefixed columns (e.g., raw ATR in pips) are preserved as-is for use by the trading environment's position-sizing logic.


---

## 5. Train / Validation / Test / OOS Splits

| Split | Description | Purpose |
|---|---|---|
| Train | Earlier training-era data | Model training, normalization fitting |
| Validation | Intermediate period | Checkpoint selection during training |
| Test (2024) | Most recent in-sample-era holdout | In-sample-era test |
| OOS 2025 | 2025 calendar year (separate raw data) | Truly unseen out-of-sample evaluation |

The split is chronological — data is sorted by timestamp and divided by row index with no shuffling. Temporal ordering is enforced by assertion (`train.max_time < val.min_time < test.min_time`). Split ratios are configured in YAML.

**OOS 2025** is processed from a separate raw data folder through the same pipeline steps but with frozen normalization and winsorization statistics. There is no train/val/test subdivision within OOS 2025 — the entire year serves as a single evaluation set.

**Note on OOS 2024 integrity:** The test-2024 split was used iteratively during architecture and hyperparameter search across many experiments. Only OOS 2025 was introduced after all configurations were locked and provides a clean out-of-sample evaluation.


---

## 6. Leakage Prevention and Causal Alignment

### 6.1 Feature Causality

All market features observed by the agent at decision time $T$ are computed from data strictly before $T$. An internal audit discovered a look-ahead bug that exposed same-bar price information (`close[T]`, `high[T]`, `low[T]`) at decision time $T$. The fix lags all features by one bar — the agent observes `features[T−1]` and executes at `open[T]`. Pre-fix results were invalidated.

### 6.2 Split Integrity

An early pipeline version produced randomized (non-chronological) splits. This was identified and corrected via a dedicated pipeline configuration that regenerated all splits in strict chronological order with temporal assertions.

### 6.3 Train-Only Statistics

- **Winsorization bounds** are computed from the training split only and applied identically to validation, test, and OOS 2025 data.
- **Normalization statistics** (mean, std for z-score) are computed from the training split only. All other splits are normalized using these frozen statistics.
- Both sets of statistics are persisted as artifacts and are never recomputed when processing new data.

### 6.4 Frozen Artifact Reuse

The OOS 2025 preparation script replicates the exact same processing order as the training pipeline but loads frozen winsorization bounds and normalization scalers rather than fitting new ones. In the working research environment, a dedicated test suite validated that:

1. Frozen statistics are loaded, not recomputed.
2. Output matches manual z-score using the frozen mean/std.
3. The frozen artifact files are not modified by the process.


---

## 7. Normalization and Artifacts

### 7.1 Strategy

Normalization is applied only to `[Feature]`-prefixed columns. The primary method is z-score normalization with clipping at ±4σ. The pipeline supports multiple normalization strategies assigned per feature type — for example, identity (no transform) for features already expressed as returns, log transforms for heavy-tailed distributions like volume, and sin/cos encoding for temporal features. The specific strategy per feature is recorded in the saved artifacts.

### 7.2 Saved Artifacts

Three frozen artifact files were used in the research pipeline:

| File | Contents |
|---|---|
| `methods.json` | Normalization strategy assigned to each feature |
| `scalers.pkl` | Fitted scaler objects (from training data only) |
| `winsor_limits.json` | Winsorization bounds (from training data only) |

These artifacts are sufficient to normalize new data without access to the original training set. They are not included in the public repository but are described here for reproducibility.


---

## 8. What Is and Is Not Included in the Public Repo

### Included

- Core source code (`src/atlasfx/`): environments, models, training, evaluation, risk management
- Production cost envelope configurations (`config/`)
- Research documentation and evidence trail (`docs/`)

### Not Included

- **Raw tick data** (several tens of GB). Historical tick-by-tick forex data can be obtained from providers of publicly available historical forex tick feeds. The pipeline expects one CSV per trading day per pair, in the `timestamp, askPrice, bidPrice, askVolume, bidVolume` CSV format.
- **Processed Parquet files** (train/val/test splits). These are deterministically reproducible from raw data using the pipeline code and YAML configs used in the research environment.
- **Large model checkpoints.** See the training documentation for checkpoint format and storage.

The repository is structured so that a user with access to equivalent raw tick data can reproduce the entire pipeline end-to-end.


---

## 9. Reproducibility Notes

- **Deterministic pipeline**: Fixed processing order, no random sampling in aggregation or featurization. The only stochastic element is the RL training seed, not the data pipeline.
- **YAML-driven configuration**: All pipeline parameters (symbols, time windows, split ratios, winsorization percentiles, normalization strategies, featurizer windows) were specified in version-controlled YAML files in the working research environment.
- **Schema validation**: Input data was validated against a schema that enforces column types, value ranges, and cross-column constraints (e.g., ask ≥ bid) at each pipeline stage.
- **Frozen artifacts for forward application**: New data (e.g., OOS 2025) is processed using saved training-period statistics, ensuring exact reproducibility of the normalization step without access to the original training split.
- **Execution**: Phase 1 (ticks → klines) and Phase 2 (klines → features) are run as separate pipeline commands with YAML configuration.


---

## 10. Practical Limitations

1. **Single-pair focus in main results.** Although the pipeline processes 7 pairs, the champion model was trained and evaluated on USDJPY only. Cross-pair features are included in the feature matrix but their contribution to single-pair performance has not been isolated.

2. **OOS 2024 contamination.** The test-2024 split was used iteratively during architecture search. Only OOS 2025 provides a clean out-of-sample evaluation.

3. **Volume data quality.** Tick-level volume fields represent quoted liquidity (order book depth) rather than executed transaction volume. The distinction matters for microstructure features like OFI.

4. **Feature validation status.** Most features are marked as lookahead-safe in the feature audit. A small number of features (related to cross-sectional alignment and order-book timing) carry a "Needs Review" flag. These flags reflect an honest audit status rather than confirmed leakage.

5. **Raw data not redistributed.** Reproducing the pipeline from scratch requires obtaining equivalent tick data independently.


---

## 11. Implications for Research

The data pipeline demonstrates several practices relevant to applied ML research on financial time series:

- **Strict temporal splitting** with chronological assertions, corrected after discovering an early randomized-split bug.
- **Frozen normalization** that decouples train-time fitting from inference-time application, validated with dedicated unit tests.
- **Causal feature engineering** enforced by a 1-bar lag after a look-ahead bug was discovered, audited, and fixed — with pre-fix results publicly invalidated.
- **Transparent audit trail** documenting what went wrong (randomized splits, look-ahead bias) and how it was corrected, rather than presenting only the final clean version.

These are standard practices in rigorous ML experimentation, but they are frequently omitted or under-documented in trading-system research. The pipeline code and documentation are provided as a reference implementation.
