"""
Trading Metrics Tracker - Stateful Metrics Collection

Tracks trading performance metrics throughout episode lifetime and computes
comprehensive statistics at the end. Integrates with metrics.py for calculations.

Author: AtlasFX Team
Version: 1.0.0
Date: November 4, 2025
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from atlasfx.evaluation.metrics import (
    TRADING_DAYS_PER_YEAR,
    PerformanceMetrics,
    calculate_all_metrics,
)


if TYPE_CHECKING:
    from atlasfx.environments.trading_env import ProductionTrade


@dataclass
class TradingMetricsTracker:
    """
    Tracks and calculates trading metrics over episode lifetime.

    This class maintains state during an episode and provides comprehensive
    metrics calculation at the end. It's designed to be reset for each new episode.

    Usage:
        tracker = TradingMetricsTracker(initial_balance=10000)

        # During episode:
        for step in range(num_steps):
            tracker.record_step(step, balance, equity)
            if trade_closed:
                tracker.record_trade(trade)

        # At episode end:
        metrics = tracker.compute_all_metrics()
        print(f"Sharpe Ratio: {metrics.sharpe_ratio:.2f}")

    Attributes:
        initial_balance: Starting capital
        risk_free_rate: Annual risk-free rate for Sharpe/Sortino calculations
        equity_curve: List of equity snapshots at each step
        balance_curve: List of balance snapshots at each step
        trades: List of completed trades
        timestamps: List of step indices
    """

    initial_balance: float
    risk_free_rate: float = 0.02
    periods_per_year: int = TRADING_DAYS_PER_YEAR

    # Internal state (auto-initialized)
    equity_curve: list[float] = field(default_factory=list)
    balance_curve: list[float] = field(default_factory=list)
    trades: list[ProductionTrade] = field(default_factory=list)
    timestamps: list[int] = field(default_factory=list)

    def record_step(self, step: int, balance: float, equity: float) -> None:
        """
        Record snapshot at current step.

        Args:
            step: Current step index
            balance: Current balance (realized PnL)
            equity: Current equity (balance + unrealized PnL)

        Example:
            >>> tracker.record_step(100, 10500.0, 10750.0)
        """
        self.timestamps.append(step)
        self.balance_curve.append(balance)
        self.equity_curve.append(equity)

    def record_trade(self, trade: ProductionTrade) -> None:
        """
        Add completed trade to history.

        Args:
            trade: Completed trade object

        Example:
            >>> tracker.record_trade(completed_trade)
        """
        self.trades.append(trade)

    def compute_all_metrics(self) -> dict[str, float]:
        """
        Calculate all 20 performance metrics.

        Returns:
            Dictionary with all metrics:
                - Primary (10): return-based and risk-adjusted
                - Secondary (10): trade-level and statistical

        Note:
            Returns zeros if insufficient data (e.g., no equity curve).

        Example:
            >>> metrics = tracker.compute_all_metrics()
            >>> print(metrics['sharpe_ratio'])
            1.85

        Raises:
            AssertionError: If equity_curve state is invalid (prevents data leakage bugs)
        """
        # Handle edge case: no data
        if not self.equity_curve:
            return self._empty_metrics()

        # FAIL-FAST ASSERTIONS: Catch tracker state bugs early
        # These prevent data leakage between episodes or accumulation bugs
        assert self.equity_curve[0] == self.initial_balance, (
            f"Equity curve initial value mismatch: "
            f"equity_curve[0]={self.equity_curve[0]:.2f} != "
            f"initial_balance={self.initial_balance:.2f}. "
            f"This indicates the tracker was not properly reset."
        )

        # Convert to numpy arrays
        equity_array = np.array(self.equity_curve, dtype=np.float64)
        final_equity = self.equity_curve[-1] if self.equity_curve else self.initial_balance
        num_days = len(self.equity_curve)

        # Calculate all metrics using centralized function
        metrics: PerformanceMetrics = calculate_all_metrics(
            initial_balance=self.initial_balance,
            final_equity=final_equity,
            equity_curve=equity_array,
            trades=self.trades,
            num_days=num_days,
            risk_free_rate=self.risk_free_rate,
            periods_per_year=self.periods_per_year,
        )

        # Convert to dictionary
        return {
            # Primary metrics (10)
            "total_return_pct": metrics.total_return_pct,
            "annualized_return_pct": metrics.annualized_return_pct,
            "annualized_volatility": metrics.annualized_volatility,
            "sharpe_ratio": metrics.sharpe_ratio,
            "sortino_ratio": metrics.sortino_ratio,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "calmar_ratio": metrics.calmar_ratio,
            "omega_ratio": metrics.omega_ratio,
            "tail_ratio": metrics.tail_ratio,
            "recovery_factor": metrics.recovery_factor,
            # Secondary metrics (10) — GROSS
            "total_trades": metrics.total_trades,
            "win_rate_pct": metrics.win_rate_pct,
            "profit_factor": metrics.profit_factor,
            "directional_accuracy_pct": metrics.directional_accuracy_pct,
            "avg_trade_duration": metrics.avg_trade_duration,
            "risk_reward_ratio": metrics.risk_reward_ratio,
            "expected_value_per_trade": metrics.expected_value_per_trade,
            "avg_capital_at_risk": metrics.avg_capital_at_risk,
            "return_skewness": metrics.return_skewness,
            "return_kurtosis": metrics.return_kurtosis,
            # Net metrics (after commission + slippage)
            "win_rate_net_pct": metrics.win_rate_net_pct,
            "profit_factor_net": metrics.profit_factor_net,
            "expected_value_net_per_trade": metrics.expected_value_net_per_trade,
            "total_costs_usd": metrics.total_costs_usd,
            "total_pnl_gross_usd": metrics.total_pnl_gross_usd,
            "total_pnl_net_usd": metrics.total_pnl_net_usd,
        }

    def _empty_metrics(self) -> dict[str, float]:
        """Return zero-filled metrics dictionary."""
        return {
            # Primary
            "total_return_pct": 0.0,
            "annualized_return_pct": 0.0,
            "annualized_volatility": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "calmar_ratio": 0.0,
            "omega_ratio": 0.0,
            "tail_ratio": 0.0,
            "recovery_factor": 0.0,
            # Secondary
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "directional_accuracy_pct": 0.0,
            "avg_trade_duration": 0.0,
            "risk_reward_ratio": 0.0,
            "expected_value_per_trade": 0.0,
            "avg_capital_at_risk": 0.0,
            "return_skewness": 0.0,
            "return_kurtosis": 0.0,
            # Net
            "win_rate_net_pct": 0.0,
            "profit_factor_net": 0.0,
            "expected_value_net_per_trade": 0.0,
            "total_costs_usd": 0.0,
            "total_pnl_gross_usd": 0.0,
            "total_pnl_net_usd": 0.0,
        }

    def get_equity_curve_array(self) -> NDArray[np.float64]:
        """Get equity curve as numpy array."""
        return np.array(self.equity_curve, dtype=np.float64)

    def get_balance_curve_array(self) -> NDArray[np.float64]:
        """Get balance curve as numpy array."""
        return np.array(self.balance_curve, dtype=np.float64)

    def get_returns(self) -> NDArray[np.float64]:
        """
        Calculate period returns from equity curve.

        Returns:
            Array of returns (equity[t] - equity[t-1]) / equity[t-1]
        """
        if len(self.equity_curve) < 2:
            return np.array([], dtype=np.float64)

        equity = np.array(self.equity_curve, dtype=np.float64)
        returns = np.diff(equity) / equity[:-1]
        return returns

    def get_summary_stats(self) -> dict[str, float]:
        """
        Get quick summary statistics without full metric calculation.

        Returns:
            Dictionary with basic stats:
                - num_steps, final_balance, final_equity, num_trades

        Example:
            >>> stats = tracker.get_summary_stats()
            >>> print(f"Final equity: ${stats['final_equity']:.2f}")
        """
        return {
            "num_steps": len(self.equity_curve),
            "final_balance": self.balance_curve[-1] if self.balance_curve else self.initial_balance,
            "final_equity": self.equity_curve[-1] if self.equity_curve else self.initial_balance,
            "num_trades": len(self.trades),
        }

    def reset(self) -> None:
        """
        Reset tracker for new episode.

        Clears all accumulated data while preserving configuration.

        Example:
            >>> tracker.reset()
            >>> assert len(tracker.equity_curve) == 0
        """
        self.equity_curve.clear()
        self.balance_curve.clear()
        self.trades.clear()
        self.timestamps.clear()


# ============================================================================
# TRANSACTION COST ANALYSIS
# ============================================================================


def apply_costs_and_recompute_metrics(
    trades: list[ProductionTrade],
    initial_balance: float,
    commission_per_lot_side: float,
    slippage_pips: float,
    pip_value: float,
    risk_free_rate: float = 0.02,
) -> dict[str, float]:
    """
    Apply transaction costs (commission + slippage) to trades and recompute metrics.

    This function takes a list of trades with gross PnL and applies realistic
    transaction costs to calculate net PnL and corresponding performance metrics.

    Args:
        trades: List of completed trades with gross PnL
        initial_balance: Starting capital for return calculations
        commission_per_lot_side: Fixed commission per lot per side (USD)
        slippage_pips: Average slippage in pips per trade
        pip_value: Value per pip per lot (USD/pip/lot)
        risk_free_rate: Annual risk-free rate for Sharpe/Sortino (default 2%)

    Returns:
        Dictionary containing:
            - return_pct: Net return percentage
            - sharpe: Sharpe ratio from net returns
            - max_drawdown: Maximum drawdown from net equity
            - profit_factor: Gross profit / gross loss (net)
            - win_rate: Percentage of profitable trades (net)
            - n_trades: Total number of trades
            - gross_pnl: Total gross PnL (USD)
            - net_pnl: Total net PnL after costs (USD)
            - total_costs_usd: Total transaction costs (USD)
            - avg_cost_per_trade: Average cost per trade (USD)

    Example:
        >>> trades = [
        ...     ProductionTrade(
        ...         symbol="EURUSD",
        ...         entry_time=0,
        ...         exit_time=10,
        ...         entry_price=1.1000,
        ...         exit_price=1.1010,
        ...         units=10000,
        ...         pnl_usd=100.0,
        ...         commission_usd=0.0,
        ...         slippage_usd=0.0,
        ...     )
        ... ]
        >>> metrics = apply_costs_and_recompute_metrics(
        ...     trades=trades,
        ...     initial_balance=10000.0,
        ...     commission_per_lot_side=2.5,
        ...     slippage_pips=0.5,
        ...     pip_value=10.0,
        ... )
        >>> print(f"Net return: {metrics['return_pct']:.2f}%")
        Net return: 0.80%  # After $20 in costs
    """
    import pandas as pd

    from atlasfx.evaluation.metrics import (
        calculate_max_drawdown,
        calculate_profit_factor,
        calculate_sharpe_from_equity,
        calculate_win_rate,
    )

    if not trades:
        return {
            "return_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": 0.0,
            "win_rate": 0.0,
            "n_trades": 0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "total_costs_usd": 0.0,
            "avg_cost_per_trade": 0.0,
        }

    # Create DataFrame for easier manipulation
    trades_data = []
    for trade in trades:
        # Calculate position size in lots (assuming 1 lot = 100,000 units)
        position_size_lots = abs(trade.units) / 100_000.0

        # Calculate costs
        cost_commission = 2.0 * commission_per_lot_side * position_size_lots  # Round-trip
        cost_slippage = slippage_pips * pip_value * position_size_lots
        cost_total = cost_commission + cost_slippage

        # Calculate net PnL
        pnl_gross = trade.pnl_usd
        pnl_net = pnl_gross - cost_total

        trades_data.append(
            {
                "pnl_gross_usd": pnl_gross,
                "pnl_net_usd": pnl_net,
                "cost_usd": cost_total,
                "position_size_lots": position_size_lots,
            }
        )

    df = pd.DataFrame(trades_data)

    # Calculate aggregate metrics
    gross_pnl = float(df["pnl_gross_usd"].sum())
    net_pnl = float(df["pnl_net_usd"].sum())
    total_costs = float(df["cost_usd"].sum())
    avg_cost = total_costs / len(trades)

    # Calculate return percentage
    final_balance = initial_balance + net_pnl
    return_pct = (net_pnl / initial_balance) * 100.0 if initial_balance > 0 else 0.0

    # Build equity curve from net PnL
    equity_curve = _build_equity_curve_from_pnl(
        initial_balance=initial_balance,
        pnl_series=df["pnl_net_usd"].values,
    )

    # Calculate Sharpe ratio from equity curve
    sharpe = calculate_sharpe_from_equity(equity_curve, risk_free_rate)

    # Calculate max drawdown
    max_dd = calculate_max_drawdown(equity_curve)

    # Create modified trades with net PnL for profit factor and win rate
    net_trades = _create_net_trades(trades, df["pnl_net_usd"].values)

    # Calculate trade-level metrics using existing functions
    profit_factor = calculate_profit_factor(net_trades)
    win_rate = calculate_win_rate(net_trades) * 100.0  # Convert to percentage

    return {
        "return_pct": return_pct,
        "sharpe": sharpe,
        "max_drawdown": max_dd * 100.0,  # Convert to percentage
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "n_trades": len(trades),
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "total_costs_usd": total_costs,
        "avg_cost_per_trade": avg_cost,
    }


def _build_equity_curve_from_pnl(
    initial_balance: float,
    pnl_series: NDArray[np.float64],
) -> NDArray[np.float64]:
    """
    Build equity curve from series of PnL values.

    Args:
        initial_balance: Starting capital
        pnl_series: Array of PnL values per trade

    Returns:
        Equity curve array (length = n_trades + 1)
    """
    equity = np.zeros(len(pnl_series) + 1, dtype=np.float64)
    equity[0] = initial_balance

    for i, pnl in enumerate(pnl_series):
        equity[i + 1] = equity[i] + pnl

    return equity


def _create_net_trades(
    original_trades: list[ProductionTrade],
    net_pnl_values: NDArray[np.float64],
) -> list[ProductionTrade]:
    """
    Create modified trades with net PnL for metric calculations.

    Args:
        original_trades: Original trades with gross PnL
        net_pnl_values: Net PnL values after costs

    Returns:
        List of ProductionTrade objects with updated pnl_usd
    """
    from dataclasses import replace

    net_trades = []
    for trade, net_pnl in zip(original_trades, net_pnl_values):
        # Create a copy with updated PnL
        net_trade = replace(trade, pnl_usd=net_pnl)
        net_trades.append(net_trade)

    return net_trades
