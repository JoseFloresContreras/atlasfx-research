"""
Evaluation module for AtlasFX.

This module provides metrics and backtesting tools
for evaluating trading strategies.

Submodules:
- metrics: Pure functions for calculating trading performance metrics
- trading_metrics: Stateful metrics tracker for episode-level tracking
"""

from atlasfx.evaluation import metrics, trading_metrics
from atlasfx.evaluation.metrics import (
    PERIODS_PER_YEAR_1MIN_FOREX,
    PerformanceMetrics,
    calculate_all_metrics,
)
from atlasfx.evaluation.trading_metrics import TradingMetricsTracker


__all__ = [
    "PERIODS_PER_YEAR_1MIN_FOREX",
    "PerformanceMetrics",
    "TradingMetricsTracker",
    "calculate_all_metrics",
    "metrics",
    "trading_metrics",
]
