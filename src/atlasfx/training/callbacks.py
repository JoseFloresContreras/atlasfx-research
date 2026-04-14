"""
Weights & Biases Callback for Real-Time Training Monitoring

Provides W&B integration for tracking training metrics, visualizing performance,
and logging experiments in real-time or offline mode.

Author: AtlasFX Team
Version: 1.0.0
Date: November 4, 2025
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from atlasfx.environments.trading_env import ProductionTrade


try:
    import wandb

    WANDB_AVAILABLE = True
except ImportError:
    WANDB_AVAILABLE = False
    wandb = None  # type: ignore


class WandbCallback:
    """
    Weights & Biases callback for real-time training visualization.

    Logs training metrics, equity curves, and trade statistics to W&B dashboard.
    Supports both online and offline modes for experiment tracking.

    Features:
        - Step-level metrics logging (balance, equity, Sharpe, drawdown)
        - Episode-level summary statistics
        - Individual trade logging
        - Equity curve visualization
        - Distribution plots for returns
        - Configurable logging frequency

    Usage with W&B online mode:
        >>> import wandb
        >>> wandb.init(
        ...     project="atlasfx-mvp",
        ...     name="sac_training_run_001",
        ...     config={"env": "ProductionTradingEnv", "algorithm": "SAC"}
        ... )
        >>> callback = WandbCallback(log_frequency=10)
        >>> # In training loop:
        >>> for step in range(total_steps):
        ...     obs, reward, done, truncated, info = env.step(action)
        ...     callback.on_step_end(step, info)
        ...     if done:
        ...         callback.on_episode_end(episode, metrics)

    Usage with W&B offline mode (recommended for development):
        >>> import wandb
        >>> wandb.init(
        ...     project="atlasfx-mvp",
        ...     mode="offline",  # ← Offline mode
        ...     config=env_config
        ... )
        >>> callback = WandbCallback()
        >>> # ... training loop ...
        >>> wandb.finish()
        >>> # Later: wandb sync wandb/latest-run

    Args:
        log_frequency: Log step metrics every N steps (default: 10)
        log_trades: Whether to log individual trades (default: True)
        log_equity: Whether to log equity curve charts (default: True)
        log_distributions: Whether to log return distributions (default: False)
        enabled: Whether callback is active (auto-detects W&B availability)
    """

    def __init__(
        self,
        log_frequency: int = 10,
        log_trades: bool = True,
        log_equity: bool = True,
        log_distributions: bool = False,
        enabled: bool | None = None,
    ):
        """Initialize W&B callback."""
        # Auto-detect if W&B is available and initialized
        if enabled is None:
            enabled = WANDB_AVAILABLE and wandb is not None and wandb.run is not None

        self.enabled = enabled
        self.log_frequency = log_frequency
        self.log_trades = log_trades
        self.log_equity = log_equity
        self.log_distributions = log_distributions

        # Internal state
        self.step_count = 0
        self.episode_count = 0
        self.equity_buffer: list[tuple[int, float]] = []
        self.balance_buffer: list[tuple[int, float]] = []

        if not self.enabled:
            if not WANDB_AVAILABLE:
                print("⚠️ wandb not installed. Install with: pip install wandb")
            elif wandb is None or wandb.run is None:
                print("⚠️ wandb not initialized. Call wandb.init() first.")
            print("ℹ️ WandbCallback is disabled.")

    def on_step_end(self, step: int, info: dict[str, Any]) -> None:
        """
        Log step-level metrics to W&B.

        Called after each environment step to log real-time metrics.

        Args:
            step: Global step number
            info: Info dict from environment (contains all metrics)

        Logs:
            - train/balance, train/equity
            - train/sharpe_ratio, train/max_drawdown_pct
            - train/num_positions, train/total_capital_at_risk
        """
        if not self.enabled:
            return

        self.step_count += 1

        # Buffer equity for charting
        if "equity" in info and "balance" in info:
            self.equity_buffer.append((step, float(info["equity"])))
            self.balance_buffer.append((step, float(info["balance"])))

        # Log at specified frequency
        if self.step_count % self.log_frequency != 0:
            return

        # Prepare metrics
        metrics_to_log = {
            "train/step": step,
            "train/balance": info.get("balance", 0.0),
            "train/equity": info.get("equity", 0.0),
            "train/peak_equity": info.get("peak_equity", 0.0),
            "train/num_positions": info.get("num_positions", 0),
            "train/num_trades": info.get("num_trades", 0),
            "train/total_capital_at_risk_usd": info.get("total_capital_at_risk_usd", 0.0),
        }

        # Add performance metrics if available
        if "sharpe_ratio" in info:
            metrics_to_log["train/sharpe_ratio"] = info["sharpe_ratio"]
        if "sortino_ratio" in info:
            metrics_to_log["train/sortino_ratio"] = info["sortino_ratio"]
        if "max_drawdown_pct" in info:
            metrics_to_log["train/max_drawdown_pct"] = info["max_drawdown_pct"]
        if "win_rate_pct" in info:
            metrics_to_log["train/win_rate_pct"] = info["win_rate_pct"]

        # Log to W&B
        if wandb is not None:
            wandb.log(metrics_to_log, step=step)

    def on_episode_end(self, episode: int, metrics: dict[str, float]) -> None:
        """
        Log episode-level summary metrics to W&B.

        Called at the end of each episode to log comprehensive performance metrics.

        Args:
            episode: Episode number
            metrics: Dictionary with all 20 performance metrics

        Logs:
            - All primary metrics (returns, Sharpe, Sortino, drawdown, etc.)
            - All secondary metrics (win rate, profit factor, trade stats)
            - Equity curve chart (if enabled)
            - Summary table
        """
        if not self.enabled:
            return

        self.episode_count += 1

        # Log all 20 metrics with episode/ prefix
        episode_metrics = {f"episode/{k}": v for k, v in metrics.items()}

        # Add episode metadata
        episode_metrics["episode/episode_num"] = episode
        episode_metrics["episode/total_steps"] = self.step_count

        if wandb is not None:
            wandb.log(episode_metrics, step=episode)

            # Log equity curve chart
            if self.log_equity and self.equity_buffer:
                self._log_equity_curve(episode)

            # Log summary table
            self._log_summary_table(episode, metrics)

        # Clear buffers
        self.equity_buffer.clear()
        self.balance_buffer.clear()

    def on_trade_closed(self, trade: ProductionTrade) -> None:
        """
        Log individual trade to W&B.

        Args:
            trade: Completed trade object

        Logs:
            - trades/pnl_usd
            - trades/duration
            - trades/return_pct
        """
        if not self.enabled or not self.log_trades:
            return

        trade_metrics = {
            "trades/pnl_usd": trade.pnl_usd,
            "trades/duration": trade.exit_time - trade.entry_time,
            "trades/return_pct": trade.return_pct,
            "trades/commission_usd": trade.commission_usd,
            "trades/slippage_usd": trade.slippage_usd,
        }

        if wandb is not None:
            wandb.log(trade_metrics)

    def _log_equity_curve(self, episode: int) -> None:
        """Log equity curve as W&B line chart."""
        if wandb is None or not self.equity_buffer:
            return

        # Create W&B table
        steps, equities = zip(*self.equity_buffer, strict=False)
        _, balances = zip(*self.balance_buffer, strict=False)

        table = wandb.Table(
            data=[[s, e, b] for s, e, b in zip(steps, equities, balances, strict=False)],
            columns=["step", "equity", "balance"],
        )

        # Create line plot
        line_plot = wandb.plot.line(
            table, x="step", y="equity", title=f"Equity Curve - Episode {episode}"
        )

        wandb.log({f"charts/equity_curve_ep{episode}": line_plot}, step=episode)

    def _log_summary_table(self, episode: int, metrics: dict[str, float]) -> None:
        """Log episode summary as W&B table."""
        if wandb is None:
            return

        # Group metrics
        primary_metrics = [
            "total_return_pct",
            "annualized_return_pct",
            "annualized_volatility",
            "sharpe_ratio",
            "sortino_ratio",
            "max_drawdown_pct",
            "calmar_ratio",
            "omega_ratio",
            "tail_ratio",
            "recovery_factor",
        ]

        secondary_metrics = [
            "total_trades",
            "win_rate_pct",
            "profit_factor",
            "avg_trade_duration",
            "risk_reward_ratio",
            "expected_value_per_trade",
            "avg_capital_at_risk",
            "return_skewness",
            "return_kurtosis",
        ]

        # Create summary table
        table_data = []
        for metric in primary_metrics + secondary_metrics:
            if metric in metrics:
                table_data.append([metric, metrics[metric]])

        table = wandb.Table(data=table_data, columns=["Metric", "Value"])

        wandb.log({f"tables/summary_ep{episode}": table}, step=episode)

    def log_hyperparameters(self, config: dict[str, Any]) -> None:
        """
        Log hyperparameters to W&B config.

        Args:
            config: Dictionary with hyperparameters

        Example:
            >>> callback.log_hyperparameters({
            ...     "learning_rate": 3e-4,
            ...     "batch_size": 256,
            ...     "gamma": 0.99
            ... })
        """
        if not self.enabled or wandb is None:
            return

        wandb.config.update(config)

    def log_artifact(self, artifact_path: str, artifact_type: str, name: str) -> None:
        """
        Log file artifact to W&B.

        Args:
            artifact_path: Path to file or directory
            artifact_type: Type of artifact (e.g., "model", "dataset", "results")
            name: Artifact name

        Example:
            >>> callback.log_artifact(
            ...     "./results/episode_001",
            ...     artifact_type="episode_data",
            ...     name="episode_001_export"
            ... )
        """
        if not self.enabled or wandb is None:
            return

        artifact = wandb.Artifact(name=name, type=artifact_type)
        artifact.add_dir(artifact_path)
        wandb.log_artifact(artifact)

    def finish(self) -> None:
        """
        Finish W&B run and sync if in offline mode.

        Call at the end of training to ensure all data is saved.
        """
        if not self.enabled or wandb is None:
            return

        wandb.finish()


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def init_wandb_offline(
    project: str,
    name: str | None = None,
    config: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> bool:
    """
    Initialize W&B in offline mode.

    Convenience function for setting up W&B offline tracking.

    Args:
        project: W&B project name
        name: Run name (auto-generated if None)
        config: Configuration dictionary
        tags: List of tags for run organization

    Returns:
        True if initialization successful, False otherwise

    Example:
        >>> init_wandb_offline(
        ...     project="atlasfx-mvp",
        ...     name="sac_training_baseline",
        ...     config={"env": "ProductionTradingEnv", "algorithm": "SAC"},
        ...     tags=["sac", "baseline", "v1"]
        ... )
        True
    """
    if not WANDB_AVAILABLE or wandb is None:
        print("⚠️ wandb not available")
        return False

    try:
        wandb.init(
            project=project,
            name=name,
            mode="offline",
            config=config or {},
            tags=tags or [],
        )
        print(f"✅ W&B initialized in offline mode (project: {project})")
        return True
    except Exception as e:
        print(f"❌ Failed to initialize W&B: {e}")
        return False


def init_wandb_online(
    project: str,
    name: str | None = None,
    config: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    entity: str | None = None,
) -> bool:
    """
    Initialize W&B in online mode.

    Args:
        project: W&B project name
        name: Run name (auto-generated if None)
        config: Configuration dictionary
        tags: List of tags for run organization
        entity: W&B entity (username or team name)

    Returns:
        True if initialization successful, False otherwise

    Example:
        >>> init_wandb_online(
        ...     project="atlasfx-mvp",
        ...     entity="my-team",
        ...     config={"learning_rate": 3e-4}
        ... )
        True
    """
    if not WANDB_AVAILABLE or wandb is None:
        print("⚠️ wandb not available")
        return False

    try:
        wandb.init(
            project=project,
            name=name,
            mode="online",
            config=config or {},
            tags=tags or [],
            entity=entity,
        )
        print(f"✅ W&B initialized in online mode (project: {project})")
        return True
    except Exception as e:
        print(f"❌ Failed to initialize W&B: {e}")
        return False
