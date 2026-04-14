"""
Extended Trading Performance Metrics - Additional Deep Analysis Metrics

56 extended metrics in 7 categories for deep performance analysis:
    A) Drawdown Path (6)  - Ulcer Index, DD Duration, Time Under Water, Pain
    B) Tail Risk (9)      - VaR, CVaR, Return Percentiles
    C) Trade Quality (19) - Win/Loss, Expectancy, Long/Short, Best/Worst, AHPR/GHPR
    D) Execution Cost (6) - Slippage, Commission Impact, Breakeven
    E) Portfolio (6)      - Exposure, Leverage, Net USD
    F) Temporal (6)       - Monthly Performance Statistics
    G) Robustness (4)     - Z-score, Risk of Ruin, PSR, DSR

These metrics complement the standard 20 metrics in metrics.py
and are calculated on-demand for detailed analysis.

Author: AtlasFX Team
Version: 4.0.0
Date: January 2025
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray
from scipy import stats

from atlasfx.evaluation.forex_notional import notional_usd


if TYPE_CHECKING:
    from atlasfx.environments.trading_env import ProductionTrade


# ============================================================================
# METRIC SCHEMA & CONTRACT (SOURCE OF TRUTH)
# ============================================================================

EXTENDED_METRICS_SCHEMA = [
    # A) Drawdown Path Metrics (6)
    {
        "name": "ulcer_index",
        "category": "A",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["equity_curve"],
    },
    {
        "name": "dd_duration_avg",
        "category": "A",
        "unit": "steps",
        "quality": "REAL",
        "required_inputs": ["equity_curve"],
    },
    {
        "name": "dd_duration_max",
        "category": "A",
        "unit": "steps",
        "quality": "REAL",
        "required_inputs": ["equity_curve"],
    },
    {
        "name": "time_under_water_pct",
        "category": "A",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["equity_curve"],
    },
    {
        "name": "pain_index",
        "category": "A",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["equity_curve"],
    },
    {
        "name": "pain_ratio",
        "category": "A",
        "unit": "ratio",
        "quality": "REAL",
        "required_inputs": ["equity_curve", "annualized_return"],
    },
    # B) Tail Risk Metrics (9)
    # NOTE: VaR/CVaR use LOSS-POSITIVE convention (positive = bad, always >= 0)
    # Example: VaR_95=5.0 means "95% confident loss won't exceed 5%"
    {
        "name": "var_95",
        "category": "B",
        "unit": "pct_loss",
        "quality": "REAL",
        "required_inputs": ["returns"],
    },
    {
        "name": "var_99",
        "category": "B",
        "unit": "pct_loss",
        "quality": "REAL",
        "required_inputs": ["returns"],
    },
    {
        "name": "cvar_95",
        "category": "B",
        "unit": "pct_loss",
        "quality": "REAL",
        "required_inputs": ["returns"],
    },
    {
        "name": "cvar_99",
        "category": "B",
        "unit": "pct_loss",
        "quality": "REAL",
        "required_inputs": ["returns"],
    },
    {
        "name": "ret_p01",
        "category": "B",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["returns"],
    },
    {
        "name": "ret_p05",
        "category": "B",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["returns"],
    },
    {
        "name": "ret_p95",
        "category": "B",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["returns"],
    },
    {
        "name": "ret_p99",
        "category": "B",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["returns"],
    },
    {
        "name": "worst_return",
        "category": "B",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["returns"],
    },
    # C) Trade Quality Metrics (19)
    {
        "name": "avg_win",
        "category": "C",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "avg_loss",
        "category": "C",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "payoff_ratio",
        "category": "C",
        "unit": "ratio",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "ev_trade_usd",
        "category": "C",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "ev_trade_r",
        "category": "C",
        "unit": "r_units",
        "quality": "REAL",
        "required_inputs": ["trades", "initial_balance"],
    },
    {
        "name": "max_consec_wins",
        "category": "C",
        "unit": "count",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "max_consec_losses",
        "category": "C",
        "unit": "count",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "longs_won_pct",
        "category": "C",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "shorts_won_pct",
        "category": "C",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "longs_pnl_usd",
        "category": "C",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "shorts_pnl_usd",
        "category": "C",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "best_trade_usd",
        "category": "C",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "worst_trade_usd",
        "category": "C",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "best_trade_pips",
        "category": "C",
        "unit": "pips",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "worst_trade_pips",
        "category": "C",
        "unit": "pips",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "total_lots",
        "category": "C",
        "unit": "lots",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "turnover_usd",
        "category": "C",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "ahpr_pct",
        "category": "C",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "ghpr_pct",
        "category": "C",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["trades", "equity_curve"],
    },
    {
        "name": "mae_avg",
        "category": "C",
        "unit": "pips",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "mfe_avg",
        "category": "C",
        "unit": "pips",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "mae_p95",
        "category": "C",
        "unit": "pips",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "mfe_p95",
        "category": "C",
        "unit": "pips",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    # D) Execution Cost Metrics (6)
    {
        "name": "cost_total_usd",
        "category": "D",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "cost_per_trade",
        "category": "D",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "commission_total",
        "category": "D",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "slippage_total",
        "category": "D",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "cost_pct_of_gross_pnl",
        "category": "D",
        "unit": "pct",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "breakeven_cost_est",
        "category": "D",
        "unit": "usd",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    # E) Portfolio/Margin Metrics (7)
    # NOTE: All notional/exposure values are in USD using proper currency conversion
    {
        "name": "max_concurrent_positions",
        "category": "E",
        "unit": "count",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "gross_exposure_avg",
        "category": "E",
        "unit": "pct_usd",
        "quality": "APPROX",
        "required_inputs": ["trades", "initial_balance"],
    },
    {
        "name": "gross_exposure_max",
        "category": "E",
        "unit": "pct_usd",
        "quality": "APPROX",
        "required_inputs": ["trades", "initial_balance"],
    },
    {
        "name": "leverage_avg",
        "category": "E",
        "unit": "ratio",
        "quality": "APPROX",
        "required_inputs": ["trades", "initial_balance"],
    },
    {
        "name": "leverage_max",
        "category": "E",
        "unit": "ratio",
        "quality": "APPROX",
        "required_inputs": ["trades", "initial_balance"],
    },
    {
        "name": "net_usd_exposure_avg",
        "category": "E",
        "unit": "pct",
        "quality": "APPROX",
        "required_inputs": ["trades"],
    },
    {
        "name": "net_usd_exposure_max",
        "category": "E",
        "unit": "pct",
        "quality": "APPROX",
        "required_inputs": ["trades"],
    },
    # F) Temporal Metrics (6)
    {
        "name": "positive_month_rate",
        "category": "F",
        "unit": "pct",
        "quality": "PROXY",
        "required_inputs": ["equity_curve", "timestamps"],
    },
    {
        "name": "n_months",
        "category": "F",
        "unit": "count",
        "quality": "PROXY",
        "required_inputs": ["equity_curve", "timestamps"],
    },
    {
        "name": "month_ret_mean",
        "category": "F",
        "unit": "pct",
        "quality": "PROXY",
        "required_inputs": ["equity_curve", "timestamps"],
    },
    {
        "name": "month_ret_median",
        "category": "F",
        "unit": "pct",
        "quality": "PROXY",
        "required_inputs": ["equity_curve", "timestamps"],
    },
    {
        "name": "month_ret_p05",
        "category": "F",
        "unit": "pct",
        "quality": "PROXY",
        "required_inputs": ["equity_curve", "timestamps"],
    },
    {
        "name": "month_ret_p95",
        "category": "F",
        "unit": "pct",
        "quality": "PROXY",
        "required_inputs": ["equity_curve", "timestamps"],
    },
    # G) Robustness Metrics (4)
    {
        "name": "z_score",
        "category": "G",
        "unit": "ratio",
        "quality": "REAL",
        "required_inputs": ["returns", "observed_sharpe", "n_observations"],
    },
    {
        "name": "risk_of_ruin",
        "category": "G",
        "unit": "probability",
        "quality": "REAL",
        "required_inputs": ["trades"],
    },
    {
        "name": "psr",
        "category": "G",
        "unit": "probability",
        "quality": "REAL",
        "required_inputs": ["returns", "observed_sharpe", "n_observations"],
    },
    {
        "name": "dsr",
        "category": "G",
        "unit": "probability",
        "quality": "REAL",
        "required_inputs": ["returns", "observed_sharpe", "n_observations", "n_trials"],
    },
]


def get_extended_metric_schema() -> list[dict]:
    """
    Return the canonical schema for all extended metrics.

    This is the SOURCE OF TRUTH for:
    - Metric names (exact)
    - Categories (A-G)
    - Units (pct, ratio, usd, count, etc.)
    - Quality tiers (REAL, APPROX, PROXY, PARTIAL)
    - Required inputs

    Quality Tiers:
    - REAL: Calculated from available data with no approximations
    - APPROX: Uses simplified approximations (e.g., no real-time position tracking)
    - PROXY: Uses substitute data (e.g., episode returns instead of calendar months)
    - PARTIAL: Missing required inputs (e.g., no MAE/MFE tracking), returns NaN

    Unit Conventions:
    - pct: Percentage values 0-100 (e.g., 25.5 = 25.5%)
    - ratio: Dimensionless ratios (e.g., 2.5 = 2.5× leverage)
    - usd: Dollar amounts (e.g., 150.00 = $150)
    - count: Integer counts (e.g., 3 = 3 positions)
    - steps: Time steps/periods (e.g., 50 = 50 steps)
    - r_units: Risk units (e.g., 1.5R = 1.5× initial risk)
    - probability: 0-1 probability (e.g., 0.95 = 95%)

    Returns:
        List of dicts with keys: name, category, unit, quality, required_inputs
    """
    return EXTENDED_METRICS_SCHEMA.copy()


def list_metric_names() -> list[str]:
    """
    List all canonical metric names.

    Returns:
        List of 57 metric names in schema order
    """
    return [m["name"] for m in EXTENDED_METRICS_SCHEMA]


def validate_metric_output(metrics: ExtendedMetrics) -> None:
    """
    Validate that metrics output matches schema contract.

    Checks:
    1. No missing metrics
    2. No extra metrics
    3. NaN values only allowed for non-REAL quality

    Args:
        metrics: ExtendedMetrics dataclass instance

    Raises:
        ValueError: If contract violated
    """
    schema_names = set(list_metric_names())
    output_names = set(metrics.__dataclass_fields__.keys())

    # Check for missing/extra metrics
    missing = schema_names - output_names
    extra = output_names - schema_names

    if missing:
        raise ValueError(f"Missing metrics in output: {sorted(missing)}")
    if extra:
        raise ValueError(f"Extra metrics in output: {sorted(extra)}")

    # Check NaN values for REAL quality metrics
    schema_dict = {m["name"]: m for m in EXTENDED_METRICS_SCHEMA}

    # MAE/MFE metrics can legitimately be NaN when trades lack intra-trade tracking
    _REAL_NAN_ALLOWED = {"mae_avg", "mfe_avg", "mae_p95", "mfe_p95"}

    for name, value in metrics.__dict__.items():
        metric_info = schema_dict[name]

        # Allow NaN for non-REAL quality or explicitly allowed REAL metrics
        if metric_info["quality"] == "REAL" and name not in _REAL_NAN_ALLOWED:
            if isinstance(value, float) and np.isnan(value):
                raise ValueError(
                    f"Metric '{name}' has quality=REAL but value is NaN. "
                    f"Either fix calculation or change quality flag."
                )


def validate_units_convention(
    returns: NDArray[np.float64] | None = None, percentages: dict[str, float] | None = None
) -> None:
    """
    Validate that units follow convention.

    Convention:
    - Returns: decimals (0.01 = 1%)
    - Percentages in metrics: 0-100 scale (25.5 = 25.5%)
    - Ratios: dimensionless (2.5 = 2.5×)

    Args:
        returns: Return series (should be decimals)
        percentages: Dict of percentage metrics (should be 0-100)

    Raises:
        ValueError: If convention violated
    """
    if returns is not None:
        # Returns should be decimals, typically |ret| < 1.0
        max_abs_ret = np.max(np.abs(returns))
        if max_abs_ret > 10.0:
            raise ValueError(
                f"Returns appear to be in percentage form (max={max_abs_ret:.1f}). "
                f"Expected decimals (0.01 = 1%). Convert before passing."
            )

    if percentages is not None:
        for name, value in percentages.items():
            if not np.isnan(value) and (value < -100 or value > 1000):
                raise ValueError(
                    f"Percentage metric '{name}' has value {value:.2f}. "
                    f"Expected range: -100 to 1000 (in %). Check calculation."
                )


# ============================================================================
# A) DRAWDOWN PATH METRICS
# ============================================================================


def calculate_ulcer_index(equity_curve: NDArray[np.float64]) -> float:
    """
    Calculate Ulcer Index - measures depth and duration of drawdowns.

    Formula: UI = sqrt(mean(DD(t)^2)) where DD(t) is % drawdown from peak

    Lower is better. UI < 5% is excellent, > 15% is concerning.

    Args:
        equity_curve: Array of equity values over time

    Returns:
        Ulcer Index as percentage

    Example:
        >>> equity = np.array([10000, 10500, 10200, 10800, 11000])
        >>> calculate_ulcer_index(equity)
        1.23  # Low UI = smooth equity curve
    """
    if len(equity_curve) < 2:
        return 0.0

    # Calculate running maximum (peak)
    running_max = np.maximum.accumulate(equity_curve)

    # Calculate drawdown % from peak at each point
    drawdowns = (equity_curve - running_max) / running_max * 100

    # Ulcer Index = sqrt(mean(DD^2))
    ulcer_index = np.sqrt(np.mean(drawdowns**2))

    return abs(ulcer_index)


def calculate_drawdown_duration(equity_curve: NDArray[np.float64]) -> tuple[float, int]:
    """
    Calculate drawdown duration statistics.

    Returns average and maximum duration (in timesteps) where equity < previous peak.

    Args:
        equity_curve: Array of equity values over time

    Returns:
        Tuple of (avg_duration, max_duration) in timesteps

    Example:
        >>> equity = np.array([100, 110, 105, 108, 115, 110, 112, 120])
        >>> calculate_drawdown_duration(equity)
        (2.5, 3)  # Average 2.5 steps, max 3 steps in drawdown
    """
    if len(equity_curve) < 2:
        return 0.0, 0

    running_max = np.maximum.accumulate(equity_curve)
    in_drawdown = equity_curve < running_max

    # Find drawdown periods
    durations = []
    current_duration = 0

    for is_dd in in_drawdown:
        if is_dd:
            current_duration += 1
        elif current_duration > 0:
            durations.append(current_duration)
            current_duration = 0

    # Add final duration if still in drawdown
    if current_duration > 0:
        durations.append(current_duration)

    if not durations:
        return 0.0, 0

    avg_duration = np.mean(durations)
    max_duration = max(durations)

    return float(avg_duration), int(max_duration)


def calculate_time_under_water(equity_curve: NDArray[np.float64]) -> float:
    """
    Calculate percentage of time spent below previous peak (underwater).

    Args:
        equity_curve: Array of equity values over time

    Returns:
        Percentage of time underwater (0-100%)

    Example:
        >>> equity = np.array([100, 110, 105, 108, 115, 110])
        >>> calculate_time_under_water(equity)
        50.0  # 50% of time below peak
    """
    if len(equity_curve) < 2:
        return 0.0

    running_max = np.maximum.accumulate(equity_curve)
    underwater = equity_curve < running_max

    time_under_water_pct = (np.sum(underwater) / len(equity_curve)) * 100

    return float(time_under_water_pct)


def calculate_pain_index_and_ratio(
    equity_curve: NDArray[np.float64], annualized_return: float
) -> tuple[float, float]:
    """
    Calculate Pain Index and Pain Ratio.

    Pain Index: Average drawdown magnitude over time
    Pain Ratio: Annualized return / Pain Index (higher is better)

    Args:
        equity_curve: Array of equity values over time
        annualized_return: Annualized return as percentage

    Returns:
        Tuple of (pain_index, pain_ratio)

    Example:
        >>> equity = np.array([100, 110, 105, 115])
        >>> calculate_pain_index_and_ratio(equity, 25.0)
        (1.5, 16.67)  # Low pain, good ratio
    """
    if len(equity_curve) < 2:
        return 0.0, 0.0

    running_max = np.maximum.accumulate(equity_curve)
    drawdowns = (equity_curve - running_max) / running_max * 100

    # Pain Index = average absolute drawdown
    pain_index = np.mean(np.abs(drawdowns))

    # Pain Ratio = return / pain
    if pain_index > 1e-8:
        pain_ratio = annualized_return / pain_index
    else:
        pain_ratio = 0.0

    return float(pain_index), float(pain_ratio)


# ============================================================================
# B) TAIL RISK METRICS
# ============================================================================


def calculate_var_cvar(
    returns: NDArray[np.float64], confidence_levels: list[float] = [0.95, 0.99]
) -> dict[str, float]:
    """
    Calculate Value at Risk (VaR) and Conditional VaR (CVaR) using LOSS-POSITIVE convention.

    CONVENTION: Loss-positive (positive = bad, always >= 0)
    - VaR_95 = 5.0 means "95% confident loss won't exceed 5%"
    - CVaR_95 >= VaR_95 (always, by definition)

    VaR: Maximum loss not exceeded with given confidence
    CVaR/ES: Expected loss given loss exceeds VaR (tail mean)

    Args:
        returns: Array of returns (as decimals, e.g., 0.01 = 1%)
        confidence_levels: List of confidence levels (e.g., [0.95, 0.99])

    Returns:
        Dictionary with var_XX and cvar_XX keys (loss-positive, >= 0)

    Example:
        >>> returns = np.array([0.01, -0.02, 0.03, -0.05, 0.02])
        >>> calculate_var_cvar(returns)
        {'var_95': 4.5, 'cvar_95': 5.0, 'var_99': 5.0, 'cvar_99': 5.0}
        # Note: Positive values = losses, CVaR >= VaR
    """
    if len(returns) == 0:
        return {f"var_{int(cl * 100)}": 0.0 for cl in confidence_levels} | {
            f"cvar_{int(cl * 100)}": 0.0 for cl in confidence_levels
        }

    results = {}

    for cl in confidence_levels:
        alpha = 1 - cl
        var_percentile = int(cl * 100)

        # VaR: alpha-percentile of returns (e.g., 5th percentile for 95% confidence)
        var_return = np.percentile(returns, alpha * 100)

        # Convert to loss-positive: VaR_loss = max(0, -return_percentile)
        var_loss = max(0.0, -var_return)
        results[f"var_{var_percentile}"] = float(var_loss * 100)  # Convert to percentage

        # CVaR: mean of returns in the tail (returns <= var_return)
        tail_returns = returns[returns <= var_return]
        if len(tail_returns) > 0:
            cvar_return = np.mean(tail_returns)
        else:
            cvar_return = var_return

        # Convert to loss-positive: CVaR_loss = max(0, -tail_mean)
        cvar_loss = max(0.0, -cvar_return)
        results[f"cvar_{var_percentile}"] = float(cvar_loss * 100)

    # Sanity check: CVaR must be >= VaR by definition
    for cl in confidence_levels:
        var_percentile = int(cl * 100)
        var_key = f"var_{var_percentile}"
        cvar_key = f"cvar_{var_percentile}"
        if results[cvar_key] < results[var_key]:
            # This should never happen with correct implementation
            results[cvar_key] = results[var_key]

    return results


def calculate_return_percentiles(returns: NDArray[np.float64]) -> dict[str, float]:
    """
    Calculate return percentiles (p1, p5, p50, p95, p99).

    Args:
        returns: Array of returns (as decimals or percentages)

    Returns:
        Dictionary with ret_pXX keys

    Example:
        >>> returns = np.random.normal(0.01, 0.02, 1000)
        >>> calculate_return_percentiles(returns)
        {'ret_p01': -3.5, 'ret_p05': -1.8, ..., 'ret_p99': 5.2}
    """
    if len(returns) == 0:
        return {f"ret_p{p:02d}": 0.0 for p in [1, 5, 50, 95, 99]}

    percentiles = [1, 5, 50, 95, 99]
    results = {}

    for p in percentiles:
        value = np.percentile(returns, p)
        results[f"ret_p{p:02d}"] = float(value * 100)  # Convert to percentage

    return results


def calculate_worst_periods(
    returns: NDArray[np.float64], timestamps: NDArray | None = None
) -> dict[str, float]:
    """
    Calculate worst return in a single period.

    Note: Without timestamps, we can only calculate worst_single_return.
    With timestamps, could calculate worst_day, worst_week, worst_month.

    Args:
        returns: Array of returns
        timestamps: Optional timestamps for period grouping

    Returns:
        Dictionary with worst_* keys
    """
    if len(returns) == 0:
        return {"worst_return": 0.0}

    worst_return = np.min(returns)

    results = {"worst_return": float(worst_return * 100)}

    # TODO: Add worst_day, worst_week, worst_month if timestamps available
    # This requires grouping by date/week/month and summing returns per period

    return results


# ============================================================================
# C) TRADE QUALITY METRICS
# ============================================================================


def calculate_avg_win_loss(trades: list[ProductionTrade]) -> tuple[float, float, float]:
    """
    Calculate average win, average loss, and payoff ratio.

    Args:
        trades: List of completed trades

    Returns:
        Tuple of (avg_win, avg_loss, payoff_ratio)
        avg_win and avg_loss in dollars
        payoff_ratio = avg_win / |avg_loss|

    Example:
        >>> trades = [...]  # Some trades
        >>> calculate_avg_win_loss(trades)
        (150.0, -50.0, 3.0)  # Win $150 avg, lose $50 avg, 3:1 ratio
    """
    if not trades:
        return 0.0, 0.0, 0.0

    winning_pnls = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    losing_pnls = [t.pnl_usd for t in trades if t.pnl_usd < 0]

    avg_win = np.mean(winning_pnls) if winning_pnls else 0.0
    avg_loss = np.mean(losing_pnls) if losing_pnls else 0.0

    # Payoff ratio = avg_win / |avg_loss|
    if avg_loss < 0:
        payoff_ratio = avg_win / abs(avg_loss)
    else:
        payoff_ratio = 0.0

    return float(avg_win), float(avg_loss), float(payoff_ratio)


def calculate_expectancy(
    trades: list[ProductionTrade], initial_balance: float = 10000.0
) -> tuple[float, float]:
    """
    Calculate expectancy (expected value) per trade in $ and R-multiples.

    EV$ = WR * AvgWin + (1-WR) * AvgLoss
    EVR = EV$ / initial_risk (where initial_risk = initial SL distance * position_size)

    Args:
        trades: List of completed trades
        initial_balance: Initial capital for R-multiple calculation

    Returns:
        Tuple of (ev_trade_usd, ev_trade_r)

    Example:
        >>> trades = [...]
        >>> calculate_expectancy(trades, 10000)
        (25.50, 0.51)  # Expect $25.50 per trade, 0.51R
    """
    if not trades:
        return 0.0, 0.0

    # Calculate win rate
    wins = sum(1 for t in trades if t.pnl_usd > 0)
    win_rate = wins / len(trades)

    # Get avg win and loss
    avg_win, avg_loss, _ = calculate_avg_win_loss(trades)

    # Expectancy in dollars
    ev_trade_usd = win_rate * avg_win + (1 - win_rate) * avg_loss

    # Expectancy in R-multiples (simplified: using initial_sl_pips as proxy for risk)
    # Proper R calculation would need actual risk per trade
    # For now, use as percentage of balance
    ev_trade_r = (ev_trade_usd / initial_balance) * 100 if initial_balance > 0 else 0.0

    return float(ev_trade_usd), float(ev_trade_r)


def calculate_consecutive_streaks(trades: list[ProductionTrade]) -> tuple[int, int]:
    """
    Calculate maximum consecutive wins and losses.

    Args:
        trades: List of completed trades (must be in chronological order)

    Returns:
        Tuple of (max_consec_wins, max_consec_losses)

    Example:
        >>> trades = [...]  # W, W, W, L, L, W, L, W, W, W, W
        >>> calculate_consecutive_streaks(trades)
        (4, 2)  # Max 4 wins in a row, max 2 losses in a row
    """
    if not trades:
        return 0, 0

    max_wins = 0
    max_losses = 0
    current_wins = 0
    current_losses = 0

    for trade in trades:
        if trade.pnl_usd > 0:
            current_wins += 1
            current_losses = 0
            max_wins = max(max_wins, current_wins)
        elif trade.pnl_usd < 0:
            current_losses += 1
            current_wins = 0
            max_losses = max(max_losses, current_losses)
        # Skip breakeven trades (pnl_usd == 0)

    return int(max_wins), int(max_losses)


def _pip_size(symbol: str) -> float:
    """Return pip size for a currency pair (0.01 for JPY pairs, 0.0001 for others)."""
    return 0.01 if "JPY" in symbol.upper() else 0.0001


def _trade_pips(trade: ProductionTrade) -> float:
    """Convert trade to pips gained (signed: positive = profitable direction)."""
    direction = 1.0 if trade.units > 0 else -1.0
    price_diff = trade.exit_price - trade.entry_price
    return direction * price_diff / _pip_size(trade.symbol)


def calculate_long_short_stats(trades: list[ProductionTrade]) -> dict[str, float]:
    """
    Calculate win rate and PnL breakdown by trade direction.

    Long = units > 0 (buy), Short = units < 0 (sell).

    Args:
        trades: List of completed trades

    Returns:
        Dictionary with longs_won_pct, shorts_won_pct, longs_pnl_usd, shorts_pnl_usd

    Example:
        >>> calculate_long_short_stats(trades)
        {'longs_won_pct': 68.5, 'shorts_won_pct': 62.0,
         'longs_pnl_usd': 5000.0, 'shorts_pnl_usd': 3000.0}
    """
    if not trades:
        return {
            "longs_won_pct": 0.0,
            "shorts_won_pct": 0.0,
            "longs_pnl_usd": 0.0,
            "shorts_pnl_usd": 0.0,
        }

    longs = [t for t in trades if t.units > 0]
    shorts = [t for t in trades if t.units < 0]

    longs_won = sum(1 for t in longs if t.pnl_usd > 0)
    shorts_won = sum(1 for t in shorts if t.pnl_usd > 0)

    return {
        "longs_won_pct": float((longs_won / len(longs) * 100) if longs else 0.0),
        "shorts_won_pct": float((shorts_won / len(shorts) * 100) if shorts else 0.0),
        "longs_pnl_usd": float(sum(t.pnl_usd for t in longs)),
        "shorts_pnl_usd": float(sum(t.pnl_usd for t in shorts)),
    }


def calculate_best_worst_trade(trades: list[ProductionTrade]) -> dict[str, float]:
    """
    Calculate best and worst trade in USD and pips.

    For pips: JPY pairs use pip=0.01, all others use pip=0.0001.

    Args:
        trades: List of completed trades

    Returns:
        Dictionary with best_trade_usd, worst_trade_usd, best_trade_pips, worst_trade_pips

    Example:
        >>> calculate_best_worst_trade(trades)
        {'best_trade_usd': 500.0, 'worst_trade_usd': -200.0,
         'best_trade_pips': 50.0, 'worst_trade_pips': -30.0}
    """
    if not trades:
        return {
            "best_trade_usd": 0.0,
            "worst_trade_usd": 0.0,
            "best_trade_pips": 0.0,
            "worst_trade_pips": 0.0,
        }

    pnls = [t.pnl_usd for t in trades]
    pips = [_trade_pips(t) for t in trades]

    return {
        "best_trade_usd": float(max(pnls)),
        "worst_trade_usd": float(min(pnls)),
        "best_trade_pips": float(max(pips)),
        "worst_trade_pips": float(min(pips)),
    }


def calculate_lots_turnover(trades: list[ProductionTrade]) -> dict[str, float]:
    """
    Calculate total lots traded and USD turnover.

    1 standard lot = 100,000 units of base currency.

    Args:
        trades: List of completed trades

    Returns:
        Dictionary with total_lots and turnover_usd

    Example:
        >>> calculate_lots_turnover(trades)
        {'total_lots': 150.5, 'turnover_usd': 15050000.0}
    """
    if not trades:
        return {"total_lots": 0.0, "turnover_usd": 0.0}

    total_lots = sum(abs(t.units) / 100_000 for t in trades)
    turnover = sum(t.notional_usd for t in trades)

    return {
        "total_lots": float(total_lots),
        "turnover_usd": float(turnover),
    }


def calculate_ahpr_ghpr(
    trades: list[ProductionTrade],
    equity_curve: NDArray[np.float64],
) -> dict[str, float]:
    """
    Calculate Average and Geometric Holding Period Return.

    AHPR: Arithmetic mean of per-trade return percentages.
    GHPR: Geometric mean = (final_equity / initial_equity)^(1/n) - 1.

    Args:
        trades: List of completed trades
        equity_curve: Equity curve array

    Returns:
        Dictionary with ahpr_pct and ghpr_pct (as percentages)

    Example:
        >>> calculate_ahpr_ghpr(trades, equity)
        {'ahpr_pct': 0.15, 'ghpr_pct': 0.12}
    """
    if not trades or len(equity_curve) < 2:
        return {"ahpr_pct": 0.0, "ghpr_pct": 0.0}

    # AHPR: arithmetic mean of per-trade return %
    trade_returns = []
    for t in trades:
        if t.equity_at_entry > 0:
            trade_returns.append((t.pnl_usd / t.equity_at_entry) * 100)

    ahpr = float(np.mean(trade_returns)) if trade_returns else 0.0

    # GHPR: geometric mean from equity curve
    initial_eq = equity_curve[0]
    final_eq = equity_curve[-1]
    n = len(trades)

    if initial_eq > 0 and final_eq > 0 and n > 0:
        ghpr = ((final_eq / initial_eq) ** (1.0 / n) - 1.0) * 100
    else:
        ghpr = 0.0

    return {"ahpr_pct": float(ahpr), "ghpr_pct": float(ghpr)}


def calculate_mae_mfe_stats(trades: list[ProductionTrade]) -> dict[str, float]:
    """
    Calculate MAE/MFE aggregate statistics in pips.

    MAE (Maximum Adverse Excursion): how far against the trade price moved (in pips).
    MFE (Maximum Favorable Excursion): how far in favor the trade price moved (in pips).

    Both are expressed as positive values (distance from entry).
    Trades with NaN mae_price/mfe_price are excluded.

    Args:
        trades: List of completed trades (must have mae_price, mfe_price attributes)

    Returns:
        Dictionary with mae_avg, mfe_avg, mae_p95, mfe_p95 (all in pips)

    Example:
        >>> calculate_mae_mfe_stats(trades)
        {'mae_avg': 15.3, 'mfe_avg': 22.7, 'mae_p95': 42.1, 'mfe_p95': 58.9}
    """
    nan_result = {
        "mae_avg": float("nan"),
        "mfe_avg": float("nan"),
        "mae_p95": float("nan"),
        "mfe_p95": float("nan"),
    }

    if not trades:
        return nan_result

    mae_pips_list: list[float] = []
    mfe_pips_list: list[float] = []

    for t in trades:
        mae_p = getattr(t, "mae_price", None)
        mfe_p = getattr(t, "mfe_price", None)

        # Skip if missing or NaN
        if mae_p is None or mfe_p is None:
            continue
        if np.isnan(mae_p) or np.isnan(mfe_p):
            continue

        pip_size = _pip_size(t.symbol)
        direction = 1.0 if t.units > 0 else -1.0

        # MAE in pips (positive = adverse distance from entry)
        mae_val = direction * (t.entry_price - mae_p) / pip_size
        # MFE in pips (positive = favorable distance from entry)
        mfe_val = direction * (mfe_p - t.entry_price) / pip_size

        mae_pips_list.append(mae_val)
        mfe_pips_list.append(mfe_val)

    if not mae_pips_list:
        return nan_result

    return {
        "mae_avg": float(np.mean(mae_pips_list)),
        "mfe_avg": float(np.mean(mfe_pips_list)),
        "mae_p95": float(np.percentile(mae_pips_list, 95)),
        "mfe_p95": float(np.percentile(mfe_pips_list, 95)),
    }


def calculate_z_score_sharpe(
    observed_sharpe: float,
    n_observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    Calculate z-score of the Sharpe ratio (significance test).

    Uses Bailey & López de Prado (2012) variance adjustment
    for non-normal returns.

    Higher z-score → more statistically significant Sharpe.
    z > 1.96 ≈ 95% confidence, z > 2.58 ≈ 99% confidence.

    Args:
        observed_sharpe: Observed Sharpe ratio
        n_observations: Number of return observations
        skewness: Return skewness
        kurtosis: Return kurtosis (excess kurtosis + 3)

    Returns:
        Z-score (dimensionless)

    Example:
        >>> calculate_z_score_sharpe(2.0, 252, 0.5, 4.0)
        5.8  # Highly significant
    """
    if n_observations < 2:
        return 0.0

    variance_term = 1 - skewness * observed_sharpe + ((kurtosis - 1) / 4) * (observed_sharpe**2)

    if variance_term <= 0:
        variance_term = 1.0

    sharpe_std = np.sqrt(variance_term / (n_observations - 1))

    if sharpe_std <= 0:
        return 0.0

    return float(observed_sharpe / sharpe_std)


def calculate_risk_of_ruin(
    trades: list[ProductionTrade],
    equity: float = 10000.0,
    ruin_threshold: float = 0.5,
) -> float:
    """
    Calculate probability of losing ruin_threshold fraction of capital.

    Uses the classic formula: RoR = (q/p)^N
    where p = win_rate, q = 1-p, N = equity / avg_loss.

    Args:
        trades: List of completed trades
        equity: Current equity for N calculation
        ruin_threshold: Fraction of equity that defines ruin (default 50%)

    Returns:
        Probability of ruin (0-1). Lower is better.

    Example:
        >>> calculate_risk_of_ruin(trades, 10000.0, 0.5)
        0.001  # 0.1% chance of losing 50% of capital
    """
    if not trades:
        return 1.0

    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd < 0]

    if not losses:
        return 0.0  # No losses ever → zero ruin
    if not wins:
        return 1.0  # No wins ever → certain ruin

    win_rate = len(wins) / len(trades)
    avg_loss = abs(np.mean([t.pnl_usd for t in losses]))

    if win_rate <= 0.5:
        return 1.0  # Negative or zero edge

    # Number of avg-loss units that ruin_threshold represents
    ruin_capital = equity * ruin_threshold
    n_units = ruin_capital / avg_loss if avg_loss > 0 else float("inf")

    # RoR = (q/p)^N
    q = 1 - win_rate
    p = win_rate
    ratio = q / p  # < 1 when win_rate > 0.5

    ror = ratio**n_units
    return float(max(0.0, min(1.0, ror)))


# ============================================================================
# D) EXECUTION COST METRICS
# ============================================================================


def calculate_cost_impact(trades: list[ProductionTrade]) -> dict[str, float]:
    """
    Calculate total cost impact from commissions and slippage.

    Args:
        trades: List of completed trades

    Returns:
        Dictionary with cost metrics

    Example:
        >>> trades = [...]
        >>> calculate_cost_impact(trades)
        {
            'cost_total_usd': 500.0,
            'cost_per_trade': 2.5,
            'commission_total': 300.0,
            'slippage_total': 200.0,
            'cost_pct_of_gross_pnl': 5.0
        }
    """
    if not trades:
        return {
            "cost_total_usd": 0.0,
            "cost_per_trade": 0.0,
            "commission_total": 0.0,
            "slippage_total": 0.0,
            "cost_pct_of_gross_pnl": 0.0,
        }

    total_commission = sum(abs(t.commission_usd) for t in trades)
    total_slippage = sum(abs(t.slippage_usd) for t in trades)
    cost_total = total_commission + total_slippage
    cost_per_trade = cost_total / len(trades)

    # Gross PnL = Net PnL + Costs
    net_pnl = sum(t.pnl_usd for t in trades)
    gross_pnl = net_pnl + cost_total

    if gross_pnl > 0:
        cost_pct = (cost_total / gross_pnl) * 100
    else:
        cost_pct = 0.0

    return {
        "cost_total_usd": float(cost_total),
        "cost_per_trade": float(cost_per_trade),
        "commission_total": float(total_commission),
        "slippage_total": float(total_slippage),
        "cost_pct_of_gross_pnl": float(cost_pct),
    }


def calculate_breakeven_cost(trades: list[ProductionTrade]) -> float:
    """
    Calculate breakeven cost threshold.

    This is the maximum average cost per trade before EV → 0.

    Formula: breakeven_cost = current_ev_per_trade

    Args:
        trades: List of completed trades

    Returns:
        Breakeven cost in dollars per trade

    Example:
        >>> trades = [...]
        >>> calculate_breakeven_cost(trades)
        25.50  # Can tolerate up to $25.50 cost per trade before breaking even
    """
    if not trades:
        return 0.0

    # Current EV per trade
    ev_trade_usd, _ = calculate_expectancy(trades)

    # Breakeven cost = current EV (if costs exceed this, EV → 0)
    return float(ev_trade_usd)


# ============================================================================
# E) PORTFOLIO / MARGIN / SHARED CAPITAL METRICS
# ============================================================================


def calculate_gross_exposure(
    trades: list[ProductionTrade], initial_balance: float = 10000.0
) -> tuple[float, float]:
    """
    Calculate gross exposure as leverage ratio (notional / equity at entry).

    Uses trade.equity_at_entry for per-trade leverage calculation.
    Falls back to initial_balance if equity_at_entry is not available.

    Gross Exposure = notional_usd / equity_at_entry  (as ratio, e.g. 5.0 = 5x)

    Args:
        trades: List of completed trades
        initial_balance: Fallback equity if equity_at_entry unavailable

    Returns:
        Tuple of (gross_exposure_avg, gross_exposure_max) as leverage ratios
    """
    if not trades:
        return 0.0, 0.0

    # Calculate leverage ratio per trade using equity at time of entry
    exposures = []
    for trade in trades:
        notional = notional_usd(trade.symbol, trade.units, trade.entry_price)
        # Use equity_at_entry if available (> 0), otherwise fall back to initial_balance
        equity = trade.equity_at_entry if trade.equity_at_entry > 0 else initial_balance
        leverage_ratio = notional / max(equity, 1e-9)
        exposures.append(leverage_ratio)

    avg_exposure = np.mean(exposures)
    max_exposure = np.max(exposures)

    return float(avg_exposure), float(max_exposure)


def calculate_leverage(
    trades: list[ProductionTrade], initial_balance: float = 10000.0
) -> tuple[float, float]:
    """
    Calculate leverage ratio per trade (notional / equity_at_entry).

    Uses trade.equity_at_entry for accurate per-trade leverage.
    Returns ratio values (e.g. 5.0 = 5x leverage, 20.0 = 20x leverage).

    Args:
        trades: List of completed trades
        initial_balance: Fallback equity if equity_at_entry unavailable

    Returns:
        Tuple of (leverage_avg, leverage_max) as ratios
    """
    # For forex with no explicit margin model, leverage = gross exposure
    return calculate_gross_exposure(trades, initial_balance)


def calculate_net_usd_exposure(trades: list[ProductionTrade]) -> tuple[float, float]:
    """
    Calculate net USD exposure (aggregate directional bias).

    FIXED: Now uses proper notional_usd() for currency conversion.

    Net Exposure = Σ signed_notional_usd / equity
    Positive = net long position, Negative = net short position

    Args:
        trades: List of completed trades

    Returns:
        Tuple of (net_usd_exposure_avg, net_usd_exposure_max)

    Example:
        >>> trades = [...]
        >>> calculate_net_usd_exposure(trades)
        (10.0, 25.0)  # Net exposure 10% avg, 25% max
    """
    if not trades:
        return 0.0, 0.0

    # Net exposure considers direction (long vs short)
    net_exposures = []
    for trade in trades:
        # Use proper notional_usd() with sign preserved
        notional = notional_usd(trade.symbol, trade.units, trade.entry_price)
        # Preserve direction: positive units = long, negative = short
        signed_notional = notional if trade.units > 0 else -notional
        net_exposures.append(signed_notional)

    # Average and max absolute net exposure
    avg_net = np.mean(np.abs(net_exposures))
    max_net = np.max(np.abs(net_exposures))

    return float(avg_net), float(max_net)


def calculate_max_concurrent_positions(trades: list[ProductionTrade]) -> int:
    """
    Calculate the maximum number of simultaneously open positions.

    Uses an event-sweep algorithm over trade [entry_time, exit_time] intervals.
    At each event boundary, tracks the running count of open positions.

    Args:
        trades: List of completed trades (must have entry_time, exit_time)

    Returns:
        Maximum concurrent position count (0 if no trades)

    Example:
        >>> # Trade A: steps 0-10, Trade B: steps 5-15 -> max_concurrent = 2
    """
    if not trades:
        return 0

    events: list[tuple[int, int]] = []  # (time, +1 open / -1 close)
    for t in trades:
        events.append((t.entry_time, 1))
        events.append((t.exit_time, -1))

    # Sort by time; on tie, closes (-1) before opens (+1) to avoid over-count
    events.sort(key=lambda e: (e[0], e[1]))

    max_conc = 0
    current = 0
    for _, delta in events:
        current += delta
        max_conc = max(max_conc, current)
    return max_conc


# ============================================================================
# F) TEMPORAL METRICS
# ============================================================================


def calculate_monthly_performance(
    equity_curve: NDArray[np.float64], timestamps: NDArray | None = None
) -> dict[str, float]:
    """
    Calculate monthly return statistics.

    Requires: equity curve with timestamps or assumes sequential monthly data.

    Args:
        equity_curve: Array of equity values
        timestamps: Optional timestamps for grouping

    Returns:
        Dictionary with monthly stats

    Example:
        >>> equity = np.array([10000, 10500, 11000, ...])
        >>> calculate_monthly_performance(equity)
        {
            'positive_month_rate': 75.0,
            'n_months': 12,
            'month_ret_mean': 5.5,
            'month_ret_median': 5.0,
            'month_ret_p05': -2.0,
            'month_ret_p95': 15.0
        }
    """
    if len(equity_curve) < 2:
        return {
            "positive_month_rate": 0.0,
            "n_months": 0,
            "month_ret_mean": 0.0,
            "month_ret_median": 0.0,
            "month_ret_p05": 0.0,
            "month_ret_p95": 0.0,
        }

    # Simplified: treat as continuous returns
    # Proper implementation would group by month if timestamps available

    # For episode-based evaluation: calculate overall stats
    # If this was a single backtest, we'd group by month

    # Placeholder: use episode returns as proxy for "monthly" returns
    # This is not ideal but workable without timestamp grouping

    returns = np.diff(equity_curve) / equity_curve[:-1] * 100

    positive_count = np.sum(returns > 0)
    positive_rate = (positive_count / len(returns)) * 100 if len(returns) > 0 else 0.0

    return {
        "positive_month_rate": float(positive_rate),
        "n_months": len(returns),  # Proxy: number of periods
        "month_ret_mean": float(np.mean(returns)),
        "month_ret_median": float(np.median(returns)),
        "month_ret_p05": float(np.percentile(returns, 5)),
        "month_ret_p95": float(np.percentile(returns, 95)),
    }


def calculate_temporal_performance(trades: list[ProductionTrade]) -> dict[str, float]:
    """
    Calculate performance by hour of day and day of week.

    Note: Requires timestamp data in trades. Placeholder implementation.

    Args:
        trades: List of completed trades with timestamps

    Returns:
        Dictionary with temporal statistics

    Example:
        >>> trades = [...]
        >>> calculate_temporal_performance(trades)
        {'ret_by_hour_mean': {...}, 'ret_by_dow_mean': {...}}
    """
    # Placeholder - requires entry_time/exit_time datetime parsing
    # Would need: extract hour-of-day, day-of-week, group PnL

    return {
        "ret_by_hour_mean": 0.0,  # Would be dict: {0: 0.5, 1: -0.2, ...}
        "ret_by_dow_mean": 0.0,  # Would be dict: {0: 1.0, 1: 0.5, ...}
    }


# ============================================================================
# G) ROBUSTNESS METRICS (ACROSS SEEDS)
# ============================================================================


def calculate_robustness_stats(results_across_seeds: list[dict[str, float]]) -> dict[str, float]:
    """
    Calculate robustness statistics across multiple seeds/runs.

    Measures consistency of performance across random seeds.

    Args:
        results_across_seeds: List of result dictionaries from different seeds
                             Each dict should have: 'return', 'sharpe', 'max_dd'

    Returns:
        Dictionary with robustness statistics

    Example:
        >>> seed_results = [
        ...     {'return': 25.0, 'sharpe': 2.0, 'max_dd': 5.0},
        ...     {'return': 28.0, 'sharpe': 2.2, 'max_dd': 6.0},
        ...     {'return': 22.0, 'sharpe': 1.8, 'max_dd': 7.0}
        ... ]
        >>> calculate_robustness_stats(seed_results)
        {
            'return_median': 25.0,
            'return_p25': 23.5,
            'return_p75': 26.5,
            'return_min': 22.0,
            'return_max': 28.0,
            'sharpe_median': 2.0,
            ...
        }
    """
    if not results_across_seeds:
        return {}

    # Extract arrays for each metric
    returns = np.array([r.get("return", 0) for r in results_across_seeds])
    sharpes = np.array([r.get("sharpe", 0) for r in results_across_seeds])
    max_dds = np.array([r.get("max_dd", 0) for r in results_across_seeds])

    stats = {}

    # Return statistics
    if len(returns) > 0:
        stats["return_median"] = float(np.median(returns))
        stats["return_p25"] = float(np.percentile(returns, 25))
        stats["return_p75"] = float(np.percentile(returns, 75))
        stats["return_min"] = float(np.min(returns))
        stats["return_max"] = float(np.max(returns))
        stats["return_iqr"] = stats["return_p75"] - stats["return_p25"]

    # Sharpe statistics
    if len(sharpes) > 0:
        stats["sharpe_median"] = float(np.median(sharpes))
        stats["sharpe_p25"] = float(np.percentile(sharpes, 25))
        stats["sharpe_p75"] = float(np.percentile(sharpes, 75))
        stats["sharpe_min"] = float(np.min(sharpes))
        stats["sharpe_max"] = float(np.max(sharpes))
        stats["sharpe_iqr"] = stats["sharpe_p75"] - stats["sharpe_p25"]

    # MaxDD statistics
    if len(max_dds) > 0:
        stats["max_dd_median"] = float(np.median(max_dds))
        stats["max_dd_p25"] = float(np.percentile(max_dds, 25))
        stats["max_dd_p75"] = float(np.percentile(max_dds, 75))
        stats["max_dd_min"] = float(np.min(max_dds))
        stats["max_dd_max"] = float(np.max(max_dds))
        stats["max_dd_iqr"] = stats["max_dd_p75"] - stats["max_dd_p25"]

    return stats


def calculate_probabilistic_sharpe_ratio(
    observed_sharpe: float,
    n_observations: int,
    sharpe_ref: float = 0.0,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    Calculate Probabilistic Sharpe Ratio (PSR).

    PSR = probability that observed Sharpe > reference Sharpe
    Accounts for track record length, skewness, and kurtosis.

    Formula from Bailey & López de Prado (2012)

    Args:
        observed_sharpe: Observed Sharpe ratio
        n_observations: Number of observations (e.g., episodes)
        sharpe_ref: Reference Sharpe (usually 0 for "is it positive?")
        skewness: Return skewness
        kurtosis: Return kurtosis (excess kurtosis + 3)

    Returns:
        PSR as probability (0-1)

    Example:
        >>> calculate_probabilistic_sharpe_ratio(
        ...     observed_sharpe=2.0,
        ...     n_observations=100,
        ...     skewness=0.5,
        ...     kurtosis=4.0
        ... )
        0.95  # 95% confident Sharpe > 0
    """
    if n_observations < 2:
        return 0.0

    # Adjustment for skewness and kurtosis
    # σ_SR = √[(1 - γ₃·SR + (γ₄-1)/4·SR²) / (N-1)]
    # where γ₃ = skewness, γ₄ = kurtosis

    variance_term = 1 - skewness * observed_sharpe + ((kurtosis - 1) / 4) * (observed_sharpe**2)

    if variance_term <= 0:
        variance_term = 1.0  # Fallback

    sharpe_std = np.sqrt(variance_term / (n_observations - 1))

    if sharpe_std <= 0:
        return 1.0 if observed_sharpe > sharpe_ref else 0.0

    # Z-score: (SR_obs - SR_ref) / σ_SR
    z_score = (observed_sharpe - sharpe_ref) / sharpe_std

    # PSR = Φ(z), where Φ is standard normal CDF
    psr = float(stats.norm.cdf(z_score))

    return psr


def calculate_deflated_sharpe_ratio(
    observed_sharpe: float,
    n_observations: int,
    n_trials: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    Calculate Deflated Sharpe Ratio (DSR).

    DSR adjusts for multiple testing (trying many strategies/params).
    Answers: "What's the probability this Sharpe is real after trying N strategies?"

    Formula from Bailey & López de Prado (2014)

    Args:
        observed_sharpe: Observed Sharpe ratio
        n_observations: Number of observations
        n_trials: Number of strategies/parameters tested
        skewness: Return skewness
        kurtosis: Return kurtosis

    Returns:
        DSR as probability (0-1)

    Example:
        >>> calculate_deflated_sharpe_ratio(
        ...     observed_sharpe=2.0,
        ...     n_observations=100,
        ...     n_trials=10,  # Tested 10 parameter sets
        ...     skewness=0.5,
        ...     kurtosis=4.0
        ... )
        0.85  # 85% confident after accounting for multiple tests
    """
    if n_observations < 2 or n_trials < 1:
        return 0.0

    # Expected maximum Sharpe under null (from trying N trials)
    # SR_max ≈ √(2·ln(N))
    expected_max_sharpe = np.sqrt(2 * np.log(n_trials))

    # Use PSR with adjusted reference Sharpe
    dsr = calculate_probabilistic_sharpe_ratio(
        observed_sharpe=observed_sharpe,
        n_observations=n_observations,
        sharpe_ref=expected_max_sharpe,
        skewness=skewness,
        kurtosis=kurtosis,
    )

    return dsr


# ============================================================================
# COMPREHENSIVE METRICS CALCULATOR
# ============================================================================


@dataclass
class ExtendedMetrics:
    """Extended metrics beyond standard 20. Total: 61 comprehensive metrics."""

    # A) Drawdown Path (6)
    ulcer_index: float
    dd_duration_avg: float
    dd_duration_max: int
    time_under_water_pct: float
    pain_index: float
    pain_ratio: float

    # B) Tail Risk (9)
    var_95: float
    var_99: float
    cvar_95: float
    cvar_99: float
    ret_p01: float
    ret_p05: float
    ret_p95: float
    ret_p99: float
    worst_return: float

    # C) Trade Quality (23)
    avg_win: float
    avg_loss: float
    payoff_ratio: float
    ev_trade_usd: float
    ev_trade_r: float
    max_consec_wins: int
    max_consec_losses: int
    longs_won_pct: float
    shorts_won_pct: float
    longs_pnl_usd: float
    shorts_pnl_usd: float
    best_trade_usd: float
    worst_trade_usd: float
    best_trade_pips: float
    worst_trade_pips: float
    total_lots: float
    turnover_usd: float
    ahpr_pct: float
    ghpr_pct: float
    mae_avg: float
    mfe_avg: float
    mae_p95: float
    mfe_p95: float

    # D) Execution Costs (6)
    cost_total_usd: float
    cost_per_trade: float
    commission_total: float
    slippage_total: float
    cost_pct_of_gross_pnl: float
    breakeven_cost_est: float

    # E) Portfolio / Margin (7)
    max_concurrent_positions: int
    gross_exposure_avg: float
    gross_exposure_max: float
    leverage_avg: float
    leverage_max: float
    net_usd_exposure_avg: float
    net_usd_exposure_max: float

    # F) Temporal (6)
    positive_month_rate: float
    n_months: int
    month_ret_mean: float
    month_ret_median: float
    month_ret_p05: float
    month_ret_p95: float

    # G) Robustness (4)
    z_score: float = 0.0
    risk_of_ruin: float = 0.0
    psr: float = 0.0  # Probabilistic Sharpe Ratio
    dsr: float = 0.0  # Deflated Sharpe Ratio


def calculate_extended_metrics(
    equity_curve: NDArray[np.float64],
    returns: NDArray[np.float64],
    trades: list[ProductionTrade],
    annualized_return: float,
    initial_balance: float = 10000.0,
    observed_sharpe: float = 0.0,
    n_trials: int = 1,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> ExtendedMetrics:
    """
    Calculate all 61 extended metrics at once.

    Args:
        equity_curve: Array of equity values over time
        returns: Array of returns (as decimals)
        trades: List of completed trades
        annualized_return: Annualized return percentage
        initial_balance: Initial capital
        observed_sharpe: Sharpe ratio for PSR/DSR calculation
        n_trials: Number of parameter sets tested (for DSR)
        skewness: Return skewness
        kurtosis: Return kurtosis

    Returns:
        ExtendedMetrics dataclass with all 60 additional metrics
    """
    # A) Drawdown Path
    ulcer_index = calculate_ulcer_index(equity_curve)
    dd_duration_avg, dd_duration_max = calculate_drawdown_duration(equity_curve)
    time_under_water_pct = calculate_time_under_water(equity_curve)
    pain_index, pain_ratio = calculate_pain_index_and_ratio(equity_curve, annualized_return)

    # B) Tail Risk
    var_cvar = calculate_var_cvar(returns, [0.95, 0.99])
    percentiles = calculate_return_percentiles(returns)
    worst = calculate_worst_periods(returns)

    # C) Trade Quality
    avg_win, avg_loss, payoff_ratio = calculate_avg_win_loss(trades)
    ev_trade_usd, ev_trade_r = calculate_expectancy(trades, initial_balance)
    max_consec_wins, max_consec_losses = calculate_consecutive_streaks(trades)
    long_short = calculate_long_short_stats(trades)
    best_worst = calculate_best_worst_trade(trades)
    lots_turn = calculate_lots_turnover(trades)
    ahpr_ghpr = calculate_ahpr_ghpr(trades, equity_curve)
    mae_mfe = calculate_mae_mfe_stats(trades)

    # D) Execution Costs
    costs = calculate_cost_impact(trades)
    breakeven_cost = calculate_breakeven_cost(trades)

    # E) Portfolio / Margin
    max_conc_pos = calculate_max_concurrent_positions(trades)
    gross_exp_avg, gross_exp_max = calculate_gross_exposure(trades, initial_balance)
    lev_avg, lev_max = calculate_leverage(trades, initial_balance)
    net_usd_avg, net_usd_max = calculate_net_usd_exposure(trades)

    # F) Temporal
    monthly_perf = calculate_monthly_performance(equity_curve)

    # G) Robustness
    z_score = calculate_z_score_sharpe(
        observed_sharpe=observed_sharpe,
        n_observations=len(returns),
        skewness=skewness,
        kurtosis=kurtosis,
    )
    ror = calculate_risk_of_ruin(trades, equity=initial_balance)
    psr = calculate_probabilistic_sharpe_ratio(
        observed_sharpe=observed_sharpe,
        n_observations=len(returns),
        skewness=skewness,
        kurtosis=kurtosis,
    )
    dsr = calculate_deflated_sharpe_ratio(
        observed_sharpe=observed_sharpe,
        n_observations=len(returns),
        n_trials=n_trials,
        skewness=skewness,
        kurtosis=kurtosis,
    )

    result = ExtendedMetrics(
        # A) Drawdown Path
        ulcer_index=ulcer_index,
        dd_duration_avg=dd_duration_avg,
        dd_duration_max=dd_duration_max,
        time_under_water_pct=time_under_water_pct,
        pain_index=pain_index,
        pain_ratio=pain_ratio,
        # B) Tail Risk
        var_95=var_cvar["var_95"],
        var_99=var_cvar["var_99"],
        cvar_95=var_cvar["cvar_95"],
        cvar_99=var_cvar["cvar_99"],
        ret_p01=percentiles["ret_p01"],
        ret_p05=percentiles["ret_p05"],
        ret_p95=percentiles["ret_p95"],
        ret_p99=percentiles["ret_p99"],
        worst_return=worst["worst_return"],
        # C) Trade Quality
        avg_win=avg_win,
        avg_loss=avg_loss,
        payoff_ratio=payoff_ratio,
        ev_trade_usd=ev_trade_usd,
        ev_trade_r=ev_trade_r,
        max_consec_wins=max_consec_wins,
        max_consec_losses=max_consec_losses,
        longs_won_pct=long_short["longs_won_pct"],
        shorts_won_pct=long_short["shorts_won_pct"],
        longs_pnl_usd=long_short["longs_pnl_usd"],
        shorts_pnl_usd=long_short["shorts_pnl_usd"],
        best_trade_usd=best_worst["best_trade_usd"],
        worst_trade_usd=best_worst["worst_trade_usd"],
        best_trade_pips=best_worst["best_trade_pips"],
        worst_trade_pips=best_worst["worst_trade_pips"],
        total_lots=lots_turn["total_lots"],
        turnover_usd=lots_turn["turnover_usd"],
        ahpr_pct=ahpr_ghpr["ahpr_pct"],
        ghpr_pct=ahpr_ghpr["ghpr_pct"],
        mae_avg=mae_mfe["mae_avg"],
        mfe_avg=mae_mfe["mfe_avg"],
        mae_p95=mae_mfe["mae_p95"],
        mfe_p95=mae_mfe["mfe_p95"],
        # D) Execution Costs
        cost_total_usd=costs["cost_total_usd"],
        cost_per_trade=costs["cost_per_trade"],
        commission_total=costs["commission_total"],
        slippage_total=costs["slippage_total"],
        cost_pct_of_gross_pnl=costs["cost_pct_of_gross_pnl"],
        breakeven_cost_est=breakeven_cost,
        # E) Portfolio / Margin
        max_concurrent_positions=max_conc_pos,
        gross_exposure_avg=gross_exp_avg,
        gross_exposure_max=gross_exp_max,
        leverage_avg=lev_avg,
        leverage_max=lev_max,
        net_usd_exposure_avg=net_usd_avg,
        net_usd_exposure_max=net_usd_max,
        # F) Temporal
        positive_month_rate=monthly_perf["positive_month_rate"],
        n_months=monthly_perf["n_months"],
        month_ret_mean=monthly_perf["month_ret_mean"],
        month_ret_median=monthly_perf["month_ret_median"],
        month_ret_p05=monthly_perf["month_ret_p05"],
        month_ret_p95=monthly_perf["month_ret_p95"],
        # G) Robustness
        z_score=z_score,
        risk_of_ruin=ror,
        psr=psr,
        dsr=dsr,
    )

    # Validate units convention
    validate_units_convention(returns=returns)

    # Validate output matches schema
    validate_metric_output(result)

    return result


def extended_metrics_to_dict_with_metadata(metrics: ExtendedMetrics) -> dict:
    """
    Convert ExtendedMetrics to dict with metadata annotations.

    Returns dict with:
    - metric values
    - _meta dict with schema info per metric

    Args:
        metrics: ExtendedMetrics instance

    Returns:
        Dict with metric values + _meta section
    """
    schema_dict = {m["name"]: m for m in EXTENDED_METRICS_SCHEMA}

    result = {}
    meta = {}

    for name, value in metrics.__dict__.items():
        result[name] = value
        meta[name] = {
            "category": schema_dict[name]["category"],
            "unit": schema_dict[name]["unit"],
            "quality": schema_dict[name]["quality"],
        }

    result["_meta"] = meta
    return result
