"""
Trading Performance Metrics - Pure Functions for Financial Analysis

Comprehensive suite of trading performance metrics for backtesting and evaluation.
All functions are pure (no side effects) and fully type-annotated for reliability.

Metrics Categories:
    - Primary (10): Return-based and risk-adjusted performance metrics
    - Secondary (10): Trade-level and statistical metrics

IMPORTANT NOTES:
- Sharpe/Sortino should be calculated from EQUITY CURVE, not step rewards
- Step rewards create numerical instabilities (tiny std → exploding Sharpe)
- Always use equity-based returns for meaningful risk-adjusted metrics

Author: AtlasFX Team
Version: 1.1.0
Date: November 18, 2025 (Fixed Sharpe calculation)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray


if TYPE_CHECKING:
    from atlasfx.environments.trading_env import ProductionTrade


# ============================================================================
# CONSTANTS
# ============================================================================

TRADING_DAYS_PER_YEAR = 252
PERIODS_PER_YEAR_1MIN_FOREX = 252 * 24 * 60  # 362,880 — 1-minute bars, 24h forex market
MIN_RETURNS_FOR_RATIO = 2
MIN_STD_EPSILON = 1e-10
MIN_DRAWDOWN_EPSILON = 1e-8


# ============================================================================
# PRIMARY METRICS (10)
# ============================================================================


def calculate_total_return(initial_balance: float, final_equity: float) -> float:
    """
    Calculate total return percentage.

    Args:
        initial_balance: Starting capital
        final_equity: Ending equity (balance + unrealized PnL)

    Returns:
        Total return as decimal (e.g., 0.15 = 15%)

    Example:
        >>> calculate_total_return(10000, 11500)
        0.15
    """
    if initial_balance <= 0:
        return 0.0
    return (final_equity - initial_balance) / initial_balance


def calculate_annualized_return(
    total_return: float,
    num_periods: int,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """
    Calculate annualized return using compound interest formula.

    Args:
        total_return: Total return as decimal
        num_periods: Number of periods in the observation window
        periods_per_year: Periods per year (252 for daily, 362880 for 1-min forex)

    Returns:
        Annualized return as decimal

    Formula:
        (1 + total_return) ^ (periods_per_year / num_periods) - 1

    Example:
        >>> calculate_annualized_return(0.15, 126)  # 15% over ~6 months (daily)
        0.3225  # ~32% annualized
    """
    if num_periods <= 0:
        return 0.0

    years_fraction = num_periods / periods_per_year
    if years_fraction <= 0:
        return 0.0

    base = 1.0 + total_return
    if base <= 0:
        return -1.0  # Total or worse-than-total loss

    try:
        import math

        log_base = math.log(base)
        annualized_log = log_base / years_fraction
        # Clamp to prevent overflow: exp(50) ≈ 5e21, more than enough
        annualized_log = max(min(annualized_log, 50.0), -50.0)
        return math.exp(annualized_log) - 1.0
    except (OverflowError, ValueError):
        return 1e6 if total_return > 0 else -1.0


def calculate_annualized_volatility(
    returns: NDArray[np.float64],
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """
    Calculate annualized volatility (standard deviation of returns).

    Args:
        returns: Array of period returns (not cumulative)
        periods_per_year: Periods per year (252 for daily, 362880 for 1-min forex)

    Returns:
        Annualized volatility as decimal

    Formula:
        std(returns) * sqrt(periods_per_year)

    Example:
        >>> returns = np.array([0.01, -0.005, 0.02, 0.015])
        >>> calculate_annualized_volatility(returns)
        0.15  # ~15% annualized volatility
    """
    if len(returns) < MIN_RETURNS_FOR_RATIO:
        return 0.0

    std = float(np.std(returns, ddof=1))
    return std * np.sqrt(periods_per_year)


def calculate_sharpe_ratio(
    returns: NDArray[np.float64],
    risk_free_rate: float = 0.02,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """
    Calculate annualized Sharpe ratio from returns.

    IMPORTANT: This function should receive EQUITY-BASED returns (equity[t]/equity[t-1] - 1),
    NOT step-by-step reward signals. Using step rewards leads to numerical instabilities
    and meaningless Sharpe values (e.g., high Sharpe with negative returns).

    Args:
        returns: Array of period returns (equity-based, not reward-based)
        risk_free_rate: Annual risk-free rate (default 2%)
        periods_per_year: Periods per year (252 for daily, 362880 for 1-min forex)

    Returns:
        Annualized Sharpe ratio

    Formula:
        (mean(excess_returns) / std(excess_returns)) * sqrt(periods_per_year)

    Safeguards:
    - Returns 0.0 if insufficient data points (< 2)
    - Returns 0.0 if std is too small (< 1e-10)

    Example:
        >>> equity = np.array([10000, 10100, 10050, 10150])
        >>> returns = np.diff(equity) / equity[:-1]  # [0.01, -0.00495, 0.00995]
        >>> calculate_sharpe_ratio(returns, risk_free_rate=0.02)
        1.23
    """
    if len(returns) < MIN_RETURNS_FOR_RATIO:
        return 0.0

    # Convert annual risk-free rate to per-period
    period_rf = risk_free_rate / periods_per_year
    excess_returns = returns - period_rf

    mean_excess = float(np.mean(excess_returns))
    std_excess = float(np.std(excess_returns, ddof=1))

    # If std is tiny, Sharpe explodes numerically
    if std_excess < MIN_STD_EPSILON:
        return 0.0

    sharpe = (mean_excess / std_excess) * np.sqrt(periods_per_year)
    return float(sharpe)


def calculate_sharpe_from_equity(
    equity_curve: NDArray[np.float64],
    risk_free_rate: float = 0.02,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """
    Calculate Sharpe ratio from equity curve (RECOMMENDED METHOD).

    This is the CORRECT way to calculate Sharpe for trading strategies.
    Avoids numerical instabilities from using step rewards.

    Args:
        equity_curve: Array of equity values over time (e.g., [10000, 10100, 10050, ...])
        risk_free_rate: Annual risk-free rate (default 2%)
        periods_per_year: Periods per year (252 for daily, 362880 for 1-min forex)

    Returns:
        Annualized Sharpe ratio

    Example:
        >>> equity = np.array([10000, 10100, 10050, 10150])
        >>> calculate_sharpe_from_equity(equity)
        1.23
    """
    if len(equity_curve) < 2:
        return 0.0

    # Calculate returns from equity curve
    returns = np.diff(equity_curve) / equity_curve[:-1]

    # Use standard Sharpe calculation
    return calculate_sharpe_ratio(returns, risk_free_rate, periods_per_year)


def calculate_sortino_ratio(
    returns: NDArray[np.float64],
    risk_free_rate: float = 0.02,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """
    Calculate annualized Sortino ratio (downside deviation only).

    Args:
        returns: Array of period returns
        risk_free_rate: Annual risk-free rate (default 2%)
        periods_per_year: Periods per year (252 for daily, 362880 for 1-min forex)

    Returns:
        Annualized Sortino ratio

    Formula:
        (mean(excess_returns) / downside_std) * sqrt(periods_per_year)

    Note:
        Sortino focuses only on downside volatility, making it more
        appropriate for asymmetric return distributions.

    Example:
        >>> returns = np.array([0.01, 0.02, -0.005, 0.015])
        >>> calculate_sortino_ratio(returns)
        2.34
    """
    if len(returns) < MIN_RETURNS_FOR_RATIO:
        return 0.0

    period_rf = risk_free_rate / periods_per_year
    excess_returns = returns - period_rf

    mean_excess = float(np.mean(excess_returns))

    # Only consider negative returns for downside deviation
    downside_returns = excess_returns[excess_returns < 0]

    if len(downside_returns) == 0:
        return float("inf") if mean_excess > 0 else 0.0

    downside_std = float(np.std(downside_returns, ddof=1))

    if downside_std < MIN_STD_EPSILON:
        return 0.0

    sortino = (mean_excess / downside_std) * np.sqrt(periods_per_year)
    return float(sortino)


def calculate_max_drawdown(equity_curve: NDArray[np.float64]) -> float:
    """
    Calculate maximum drawdown from equity curve.

    Args:
        equity_curve: Array of equity values over time

    Returns:
        Maximum drawdown as positive decimal (e.g., 0.15 = 15% drawdown)

    Formula:
        max((running_max - equity) / running_max)

    Example:
        >>> equity = np.array([10000, 11000, 10500, 10000, 11500])
        >>> calculate_max_drawdown(equity)
        0.1304  # ~13% max drawdown
    """
    if len(equity_curve) == 0:
        return 0.0

    running_max = np.maximum.accumulate(equity_curve)
    drawdowns = (running_max - equity_curve) / np.maximum(running_max, MIN_STD_EPSILON)
    max_dd = float(np.max(drawdowns))

    return max_dd


def calculate_calmar_ratio(annualized_return: float, max_drawdown: float) -> float:
    """
    Calculate Calmar ratio (return / max drawdown).

    Args:
        annualized_return: Annualized return as decimal
        max_drawdown: Maximum drawdown as positive decimal

    Returns:
        Calmar ratio (higher is better)

    Note:
        Measures return per unit of downside risk.
        Values > 1.0 are considered good.

    Example:
        >>> calculate_calmar_ratio(0.25, 0.10)
        2.5
    """
    if max_drawdown < MIN_DRAWDOWN_EPSILON:
        return 0.0

    return annualized_return / max_drawdown


def calculate_omega_ratio(
    returns: NDArray[np.float64],
    threshold: float = 0.0,
) -> float:
    """
    Calculate Omega ratio (probability-weighted gains/losses).

    Args:
        returns: Array of period returns
        threshold: Minimum acceptable return (default 0%)

    Returns:
        Omega ratio (higher is better, >1 means positive expectancy)

    Formula:
        sum(returns > threshold) / sum(|returns < threshold|)

    Note:
        Omega ratio captures the entire return distribution shape,
        unlike Sharpe which assumes normality.

    Example:
        >>> returns = np.array([0.02, 0.01, -0.01, 0.03, -0.005])
        >>> calculate_omega_ratio(returns)
        2.4
    """
    if len(returns) == 0:
        return 0.0

    gains = returns[returns > threshold] - threshold
    losses = threshold - returns[returns < threshold]

    total_gains = float(np.sum(gains))
    total_losses = float(np.sum(losses))

    if total_losses < MIN_STD_EPSILON:
        return float("inf") if total_gains > 0 else 1.0

    return total_gains / total_losses


def calculate_tail_ratio(
    returns: NDArray[np.float64],
    quantile: float = 0.05,
) -> float:
    """
    Calculate tail ratio (95th percentile / 5th percentile).

    Args:
        returns: Array of period returns
        quantile: Quantile for tail analysis (default 5%)

    Returns:
        Tail ratio (higher is better, measures upside vs downside extremes)

    Formula:
        abs(percentile(95)) / abs(percentile(5))

    Note:
        Measures the ratio of extreme gains to extreme losses.
        Values > 1.0 indicate positive skew.

    Example:
        >>> returns = np.random.normal(0.001, 0.02, 1000)
        >>> calculate_tail_ratio(returns)
        1.15  # Slight positive skew
    """
    if len(returns) < 20:  # Need sufficient data for percentiles
        return 0.0

    try:
        # Use simple numpy quantile which is faster and more reliable
        sorted_returns = np.sort(returns)
        n = len(sorted_returns)

        lower_idx = int(quantile * n)
        upper_idx = int((1.0 - quantile) * n)

        # Ensure indices are within bounds
        lower_idx = max(0, min(lower_idx, n - 1))
        upper_idx = max(0, min(upper_idx, n - 1))

        lower_tail = float(sorted_returns[lower_idx])
        upper_tail = float(sorted_returns[upper_idx])

        if abs(lower_tail) < MIN_STD_EPSILON:
            return 0.0

        return abs(upper_tail) / abs(lower_tail)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        # Handle any errors gracefully
        return 0.0


def calculate_recovery_factor(total_return: float, max_drawdown: float) -> float:
    """
    Calculate recovery factor (total return / max drawdown).

    Args:
        total_return: Total return as decimal
        max_drawdown: Maximum drawdown as positive decimal

    Returns:
        Recovery factor (higher is better)

    Note:
        Similar to Calmar but uses total return instead of annualized.
        Measures ability to recover from losses.

    Example:
        >>> calculate_recovery_factor(0.50, 0.15)
        3.33  # Gained 3.33x the worst drawdown
    """
    if max_drawdown < MIN_DRAWDOWN_EPSILON:
        return 0.0

    return total_return / max_drawdown


# ============================================================================
# SECONDARY METRICS (10)
# ============================================================================


def calculate_win_rate(trades: list[ProductionTrade]) -> float:
    """
    Calculate percentage of winning trades.

    Args:
        trades: List of completed trades

    Returns:
        Win rate as decimal (e.g., 0.58 = 58%)

    Example:
        >>> trades = [Trade(pnl=100), Trade(pnl=-50), Trade(pnl=75)]
        >>> calculate_win_rate(trades)
        0.6667
    """
    if not trades:
        return 0.0

    winning_trades = sum(1 for t in trades if t.pnl_usd > 0)
    return winning_trades / len(trades)


def calculate_profit_factor(trades: list[ProductionTrade]) -> float:
    """
    Calculate profit factor (gross profit / gross loss).

    Args:
        trades: List of completed trades

    Returns:
        Profit factor (>1.0 is profitable)

    Formula:
        sum(winning_trades) / abs(sum(losing_trades))

    Example:
        >>> trades = [Trade(pnl=100), Trade(pnl=-50), Trade(pnl=75)]
        >>> calculate_profit_factor(trades)
        3.5  # $175 profit / $50 loss
    """
    if not trades:
        return 0.0

    gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_loss = abs(sum(t.pnl_usd for t in trades if t.pnl_usd < 0))

    if gross_loss < MIN_STD_EPSILON:
        return float("inf") if gross_profit > 0 else 0.0

    return gross_profit / gross_loss


def calculate_avg_trade_duration(trades: list[ProductionTrade]) -> float:
    """
    Calculate average trade duration in steps.

    Args:
        trades: List of completed trades

    Returns:
        Average holding period in steps

    Example:
        >>> trades = [
        ...     Trade(entry_time=0, exit_time=10),
        ...     Trade(entry_time=15, exit_time=30)
        ... ]
        >>> calculate_avg_trade_duration(trades)
        12.5
    """
    if not trades:
        return 0.0

    durations = [t.exit_time - t.entry_time for t in trades]
    return float(np.mean(durations))


def calculate_risk_reward_ratio(trades: list[ProductionTrade]) -> float:
    """
    Calculate average risk-reward ratio.

    Args:
        trades: List of completed trades

    Returns:
        Average reward/risk ratio

    Formula:
        avg(winning_trade) / avg(|losing_trade|)

    Example:
        >>> trades = [Trade(pnl=100), Trade(pnl=-50), Trade(pnl=150)]
        >>> calculate_risk_reward_ratio(trades)
        2.5  # Average win $125 / average loss $50
    """
    if not trades:
        return 0.0

    winning_pnls = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    losing_pnls = [abs(t.pnl_usd) for t in trades if t.pnl_usd < 0]

    if not winning_pnls or not losing_pnls:
        return 0.0

    avg_win = float(np.mean(winning_pnls))
    avg_loss = float(np.mean(losing_pnls))

    if avg_loss < MIN_STD_EPSILON:
        return 0.0

    return avg_win / avg_loss


def calculate_expected_value(trades: list[ProductionTrade]) -> float:
    """
    Calculate expected value per trade.

    Args:
        trades: List of completed trades

    Returns:
        Expected value in USD

    Formula:
        mean(trade_pnl)

    Example:
        >>> trades = [Trade(pnl=100), Trade(pnl=-50), Trade(pnl=75)]
        >>> calculate_expected_value(trades)
        41.67
    """
    if not trades:
        return 0.0

    pnls = [t.pnl_usd for t in trades]
    return float(np.mean(pnls))


# ============================================================================
# NET METRICS (gross metrics minus commission + slippage)
# ============================================================================


def _net_pnl(trade: ProductionTrade) -> float:
    """Return net PnL for a trade: gross pnl minus commission and slippage."""
    return trade.pnl_usd - trade.commission_usd - trade.slippage_usd


def calculate_win_rate_net(trades: list[ProductionTrade]) -> float:
    """Calculate win rate using net PnL (after costs)."""
    if not trades:
        return 0.0
    winning = sum(1 for t in trades if _net_pnl(t) > 0)
    return winning / len(trades)


def calculate_profit_factor_net(trades: list[ProductionTrade]) -> float:
    """Calculate profit factor using net PnL (after costs)."""
    if not trades:
        return 0.0
    net_pnls = [_net_pnl(t) for t in trades]
    gross_profit = sum(p for p in net_pnls if p > 0)
    gross_loss = abs(sum(p for p in net_pnls if p < 0))
    if gross_loss < MIN_STD_EPSILON:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def calculate_expected_value_net(trades: list[ProductionTrade]) -> float:
    """Calculate expected value per trade using net PnL (after costs)."""
    if not trades:
        return 0.0
    return float(np.mean([_net_pnl(t) for t in trades]))


def calculate_avg_capital_at_risk(trades: list[ProductionTrade]) -> float:
    """
    Calculate average capital at risk per trade.

    Args:
        trades: List of completed trades with risk_usd attribute

    Returns:
        Average risk in USD

    Note:
        Requires trades to have risk_usd field (SL distance * position size)

    Example:
        >>> trades = [Trade(risk_usd=100), Trade(risk_usd=150)]
        >>> calculate_avg_capital_at_risk(trades)
        125.0
    """
    if not trades:
        return 0.0

    risks = [getattr(t, "risk_usd", 0.0) for t in trades]
    return float(np.mean(risks))


def calculate_return_skewness(returns: NDArray[np.float64]) -> float:
    """
    Calculate skewness of return distribution.

    Args:
        returns: Array of period returns

    Returns:
        Skewness (0=symmetric, >0=positive skew, <0=negative skew)

    Note:
        Positive skew indicates more extreme positive returns.
        Negative skew indicates more extreme negative returns.

    Example:
        >>> returns = np.array([0.01, 0.02, -0.01, 0.05, 0.01])
        >>> calculate_return_skewness(returns)
        1.23  # Positive skew (large positive outlier)
    """
    if len(returns) < 3:
        return 0.0

    # Use numpy implementation to avoid scipy inspect issues
    n = len(returns)
    mean = np.mean(returns)
    std = np.std(returns, ddof=1)

    if std == 0:
        return 0.0

    # Unbiased skewness estimator
    m3 = np.mean((returns - mean) ** 3)
    skewness = (n * (n - 1)) ** 0.5 / (n - 2) * m3 / (std**3)

    return float(skewness)


def calculate_return_kurtosis(returns: NDArray[np.float64]) -> float:
    """
    Calculate excess kurtosis of return distribution.

    Args:
        returns: Array of period returns

    Returns:
        Excess kurtosis (0=normal, >0=fat tails, <0=thin tails)

    Note:
        High kurtosis indicates presence of extreme events (fat tails).
        Financial returns typically have positive excess kurtosis.

    Example:
        >>> returns = np.random.normal(0, 0.02, 1000)
        >>> calculate_return_kurtosis(returns)
        0.05  # Close to normal distribution
    """
    if len(returns) < 4:
        return 0.0

    # Use numpy implementation to avoid scipy inspect issues
    n = len(returns)
    mean = np.mean(returns)
    std = np.std(returns, ddof=1)

    if std == 0:
        return 0.0

    # Unbiased excess kurtosis estimator
    m4 = np.mean((returns - mean) ** 4)
    kurtosis = (n * (n + 1)) * m4 / ((n - 1) * (n - 2) * (n - 3) * std**4) - 3 * (n - 1) ** 2 / (
        (n - 2) * (n - 3)
    )

    return float(kurtosis)


def calculate_trade_efficiency_index(
    trades: list[ProductionTrade],
    market_returns: NDArray[np.float64] | None = None,
) -> float:
    """
    Calculate trade efficiency (realized returns / theoretical max).

    Args:
        trades: List of completed trades
        market_returns: Market returns during same period (optional)

    Returns:
        Efficiency ratio between 0 and 1

    Note:
        If market_returns not provided, uses MAE (Mean Absolute Excursion)
        approximation based on trade PnL variance.

    Example:
        >>> trades = [Trade(pnl=100), Trade(pnl=-50)]
        >>> calculate_trade_efficiency_index(trades)
        0.75
    """
    if not trades:
        return 0.0

    total_pnl = sum(t.pnl_usd for t in trades)

    if market_returns is not None and len(market_returns) > 0:
        # Compare against market performance
        market_pnl = float(np.sum(market_returns))
        if abs(market_pnl) < MIN_STD_EPSILON:
            return 0.0
        return min(1.0, max(0.0, total_pnl / abs(market_pnl)))
    # Use MAE approximation
    pnls = np.array([t.pnl_usd for t in trades])
    max_potential_pnl = float(np.sum(np.abs(pnls)))

    if max_potential_pnl < MIN_STD_EPSILON:
        return 0.0

    efficiency = total_pnl / max_potential_pnl
    return float(np.clip(efficiency, 0.0, 1.0))


def calculate_directional_accuracy(
    predictions: NDArray[np.float64],
    actuals: NDArray[np.float64],
) -> float:
    """
    Calculate directional accuracy (correct sign predictions).

    Args:
        predictions: Predicted returns or directions
        actuals: Actual returns or outcomes

    Returns:
        Accuracy as decimal (e.g., 0.65 = 65% correct)

    Note:
        Measures how often the model predicted the correct direction,
        regardless of magnitude.

    Example:
        >>> predictions = np.array([0.01, -0.02, 0.03])
        >>> actuals = np.array([0.005, -0.01, -0.001])
        >>> calculate_directional_accuracy(predictions, actuals)
        0.6667  # 2 out of 3 correct directions
    """
    if len(predictions) == 0 or len(predictions) != len(actuals):
        return 0.0

    pred_direction = np.sign(predictions)
    actual_direction = np.sign(actuals)

    correct = np.sum(pred_direction == actual_direction)
    return float(correct / len(predictions))


# ============================================================================
# AGGREGATE METRICS CALCULATOR
# ============================================================================


@dataclass
class PerformanceMetrics:
    """Complete performance metrics summary."""

    # Primary metrics (10)
    total_return_pct: float
    annualized_return_pct: float
    annualized_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    calmar_ratio: float
    omega_ratio: float
    tail_ratio: float
    recovery_factor: float

    # Secondary metrics (10)
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    directional_accuracy_pct: float
    avg_trade_duration: float
    risk_reward_ratio: float
    expected_value_per_trade: float
    avg_capital_at_risk: float
    return_skewness: float
    return_kurtosis: float

    # Net metrics (gross - costs: commission + slippage)
    win_rate_net_pct: float = 0.0
    profit_factor_net: float = 0.0
    expected_value_net_per_trade: float = 0.0
    total_costs_usd: float = 0.0
    total_pnl_gross_usd: float = 0.0
    total_pnl_net_usd: float = 0.0


def calculate_all_metrics(
    initial_balance: float,
    final_equity: float,
    equity_curve: NDArray[np.float64],
    trades: list[ProductionTrade],
    num_days: int | None = None,
    risk_free_rate: float = 0.02,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> PerformanceMetrics:
    """
    Calculate all 20 performance metrics at once.

    Args:
        initial_balance: Starting capital
        final_equity: Ending equity
        equity_curve: Array of equity values over time
        trades: List of completed trades
        num_days: Number of periods in the equity curve (auto-calculated if None)
        risk_free_rate: Annual risk-free rate (default 2%)
        periods_per_year: Periods per year (252 for daily, 362880 for 1-min forex)

    Returns:
        PerformanceMetrics dataclass with all metrics

    Example:
        >>> metrics = calculate_all_metrics(
        ...     initial_balance=10000,
        ...     final_equity=11500,
        ...     equity_curve=equity_array,
        ...     trades=trade_list
        ... )
        >>> print(f"Sharpe: {metrics.sharpe_ratio:.2f}")
        Sharpe: 1.85
    """
    # Calculate returns
    if len(equity_curve) > 1:
        returns = np.diff(equity_curve) / equity_curve[:-1]
    else:
        returns = np.array([])

    # Auto-calculate num_periods if not provided
    if num_days is None:
        num_days = len(equity_curve)

    # Primary metrics
    total_return = calculate_total_return(initial_balance, final_equity)
    annualized_return = calculate_annualized_return(total_return, num_days, periods_per_year)
    annualized_volatility = calculate_annualized_volatility(returns, periods_per_year)
    sharpe_ratio = calculate_sharpe_ratio(returns, risk_free_rate, periods_per_year)
    sortino_ratio = calculate_sortino_ratio(returns, risk_free_rate, periods_per_year)
    max_drawdown = calculate_max_drawdown(equity_curve)
    calmar_ratio = calculate_calmar_ratio(annualized_return, max_drawdown)
    omega_ratio = calculate_omega_ratio(returns)
    tail_ratio = calculate_tail_ratio(returns)
    recovery_factor = calculate_recovery_factor(total_return, max_drawdown)

    # Secondary metrics
    total_trades = len(trades)
    win_rate = calculate_win_rate(trades)
    profit_factor = calculate_profit_factor(trades)
    directional_accuracy = 0.0  # Requires predictions, set externally if available
    avg_trade_duration = calculate_avg_trade_duration(trades)
    risk_reward_ratio = calculate_risk_reward_ratio(trades)
    expected_value = calculate_expected_value(trades)
    avg_capital_at_risk = calculate_avg_capital_at_risk(trades)
    return_skewness = calculate_return_skewness(returns)
    return_kurtosis = calculate_return_kurtosis(returns)

    # Net metrics (after costs)
    win_rate_net = calculate_win_rate_net(trades)
    profit_factor_net = calculate_profit_factor_net(trades)
    expected_value_net = calculate_expected_value_net(trades)
    total_costs = sum(t.commission_usd + t.slippage_usd for t in trades)
    total_pnl_gross = sum(t.pnl_usd for t in trades)
    total_pnl_net = total_pnl_gross - total_costs

    return PerformanceMetrics(
        total_return_pct=total_return * 100,
        annualized_return_pct=annualized_return * 100,
        annualized_volatility=annualized_volatility,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        max_drawdown_pct=max_drawdown * 100,
        calmar_ratio=calmar_ratio,
        omega_ratio=omega_ratio,
        tail_ratio=tail_ratio,
        recovery_factor=recovery_factor,
        total_trades=total_trades,
        win_rate_pct=win_rate * 100,
        profit_factor=profit_factor,
        directional_accuracy_pct=directional_accuracy * 100,
        avg_trade_duration=avg_trade_duration,
        risk_reward_ratio=risk_reward_ratio,
        expected_value_per_trade=expected_value,
        avg_capital_at_risk=avg_capital_at_risk,
        return_skewness=return_skewness,
        return_kurtosis=return_kurtosis,
        # Net metrics
        win_rate_net_pct=win_rate_net * 100,
        profit_factor_net=profit_factor_net,
        expected_value_net_per_trade=expected_value_net,
        total_costs_usd=total_costs,
        total_pnl_gross_usd=total_pnl_gross,
        total_pnl_net_usd=total_pnl_net,
    )
