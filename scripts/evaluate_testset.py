#!/usr/bin/env python3
"""
Evaluate SAC Baseline on Full Test Set.

This script evaluates the trained SAC baseline model on the entire test set
using consecutive episodes. No training is performed - pure evaluation with
deterministic policy.

Author: AtlasFX MVP
Date: 2025-01
"""

import argparse
from datetime import datetime
import json
import logging
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
import pandas as pd
import torch
import yaml

from atlasfx.environments.trading_env import ProductionTradingConfig, ProductionTradingEnv
from atlasfx.models.sac import SAC


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Set up logging configuration."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(__name__)


def load_config(config_path: Path | None) -> dict[str, Any]:
    """Load configuration from YAML file."""
    if config_path is None or not config_path.exists():
        return {}

    with open(config_path) as f:
        return yaml.safe_load(f)


def _normalize_symbol(symbol: str) -> str:
    """Normalize a symbol string to the 'xxxyyy-pair' format used in parquet columns."""
    raw = symbol.strip()
    if " | " in raw:
        return raw.split(" | ")[0]
    sym_lower = raw.lower()
    return sym_lower if sym_lower.endswith("-pair") else f"{sym_lower}-pair"


def create_environment(
    df: pd.DataFrame,
    symbol: str | list[str],
    episode_length: int,
    use_vae_features: bool = False,
    min_tp_sl_ratio: float = 1.5,
    max_tp_sl_ratio: float = 3.0,
    min_sl_pips: float = 0.0,
    enable_break_even_stop: bool = False,
    break_even_trigger_r: float = 0.8,
    break_even_buffer_mode: str = "auto",
    break_even_buffer_pips: float = None,
    enable_trailing_stop: bool = False,
    trailing_start_r: float = 1.5,
    trailing_atr_multiple: float = 1.0,
    trailing_min_distance_pips: float = None,
    continuous_mode: bool = False,
    max_leverage: float | None = None,
    max_lots: float | None = 50.0,
    position_sizing_mode: str = "agent",
    fixed_lots: float = 1.0,
    disable_risk_scaling: bool = False,
    commission_per_lot: float = 2.5,
    spread_pips: float = 0.2,
    slippage_bps: float = 0.0,
    slippage_pips_mean: float = 0.0,
    slippage_pips_std: float = 0.0,
    allow_positive_slippage: bool = False,
    action_penalty: float = 0.0,
    position_dead_zone: float = 0.0,
    min_hold_period: int = 0,
) -> ProductionTradingEnv:
    """
    Create ProductionTradingEnv for evaluation.

    Args:
        df: Test data DataFrame
        symbol: Symbol key (e.g., 'eurusd-pair')
        episode_length: Number of bars per episode (ignored if continuous_mode=True)
        use_vae_features: If True, use VAE latent features instead of raw features
        continuous_mode: If True, set episode_length to full data length for continuous backtest
        max_leverage: Maximum leverage allowed (None = no cap)
        max_lots: Maximum position size in lots (None = no limit)
        position_sizing_mode: "agent" (default) or "fixed_lots" (size-invariant evaluation)
        fixed_lots: Fixed lot size when position_sizing_mode="fixed_lots"

    Returns:
        Configured ProductionTradingEnv
    """
    # For continuous mode, override episode_length to use full data
    effective_episode_length = len(df) if continuous_mode else episode_length

    config = ProductionTradingConfig(
        initial_balance=10_000.0,
        max_risk_per_trade_pct=0.02,
        episode_length=effective_episode_length,
        commission_per_lot=commission_per_lot,
        pip_value_per_lot=10.0,
        spread_pips=spread_pips,
        slippage_bps=slippage_bps,
        slippage_pips_mean=slippage_pips_mean,
        slippage_pips_std=slippage_pips_std,
        slippage_half_normal=True,
        allow_positive_slippage=allow_positive_slippage,
        use_vae_features=use_vae_features,
        # Anti-overtrading guards (must match training)
        action_penalty=action_penalty,
        position_dead_zone=position_dead_zone,
        min_hold_period=min_hold_period,
        # TP/SL ratio enforcement
        min_tp_sl_ratio=min_tp_sl_ratio,
        max_tp_sl_ratio=max_tp_sl_ratio,
        # Minimum SL constraint
        min_sl_pips=min_sl_pips,
        # Break-even stop configuration
        enable_break_even_stop=enable_break_even_stop,
        break_even_trigger_r=break_even_trigger_r,
        break_even_buffer_mode=break_even_buffer_mode,
        break_even_buffer_pips=break_even_buffer_pips,
        # Trailing stop configuration
        enable_trailing_stop=enable_trailing_stop,
        trailing_start_r=trailing_start_r,
        trailing_atr_multiple=trailing_atr_multiple,
        trailing_min_distance_pips=trailing_min_distance_pips,
        # Leverage cap
        max_leverage=max_leverage,
        # Position size cap
        max_position_lots=max_lots,
        # Position sizing mode
        position_sizing_mode=position_sizing_mode,
        fixed_lots=fixed_lots,
        # Risk scaling control
        disable_risk_scaling=disable_risk_scaling,
    )

    # Define price columns — support single or multi-pair
    if isinstance(symbol, str):
        symbol_keys = [symbol]
    else:
        symbol_keys = list(symbol)

    price_cols = {}
    for sk in symbol_keys:
        price_cols[sk] = {
            "open": f"{sk} | open",
            "high": f"{sk} | high",
            "low": f"{sk} | low",
            "close": f"{sk} | close",
        }

    env = ProductionTradingEnv(
        data=df,
        symbols=symbol_keys,
        price_cols=price_cols,
        config=config,
    )

    return env


def load_sac_agent(
    checkpoint_path: Path, state_dim: int, action_dim: int, device: torch.device,
    hidden_dims: list[int] | None = None,
) -> SAC:
    """
    Load trained SAC agent from checkpoint.

    Args:
        checkpoint_path: Path to checkpoint file
        state_dim: Environment state dimension
        action_dim: Environment action dimension
        device: Torch device (cuda/cpu)
        hidden_dims: Hidden layer dimensions (default: [256, 256])

    Returns:
        SAC agent with loaded weights
    """
    kwargs = dict(
        state_dim=state_dim,
        action_dim=action_dim,
        device=device,
    )
    if hidden_dims is not None:
        kwargs["hidden_dims"] = hidden_dims
    agent = SAC(**kwargs)

    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    agent.load_state_dict(checkpoint["agent_state_dict"])
    agent.eval()  # Set to evaluation mode

    return agent


def generate_episode_schedule(
    total_bars: int,
    episode_length: int,
    num_episodes: int,
    mode: str = "random",
    seed: int = 42,
    df: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Generate deterministic episode schedule.

    Args:
        total_bars: Total number of bars in test set
        episode_length: Number of bars per episode
        num_episodes: Number of episodes to generate
        mode: "random" (random windows with replacement) or "chronological" (sequential)
        seed: Random seed for deterministic generation
        df: Optional DataFrame with timestamp column for recording timestamps

    Returns:
        DataFrame with columns: episode_id, start_idx, length_steps, [start_ts, end_ts]
    """
    rng = np.random.default_rng(seed)
    schedule = []

    if mode == "random":
        # Random windows with replacement (matches current evaluation behavior)
        max_start = total_bars - episode_length
        if max_start < 0:
            raise ValueError(f"Episode length {episode_length} exceeds total bars {total_bars}")

        for ep_id in range(1, num_episodes + 1):
            start_idx = int(rng.integers(0, max(1, max_start)))
            schedule.append(
                {
                    "episode_id": ep_id,
                    "start_idx": start_idx,
                    "length_steps": episode_length,
                }
            )

    elif mode == "chronological":
        # Sequential non-overlapping windows
        for ep_id in range(1, num_episodes + 1):
            start_idx = (ep_id - 1) * episode_length
            if start_idx + episode_length > total_bars:
                break  # Stop if we run out of data
            schedule.append(
                {
                    "episode_id": ep_id,
                    "start_idx": start_idx,
                    "length_steps": episode_length,
                }
            )

    else:
        raise ValueError(f"Unknown schedule mode: {mode}. Use 'random' or 'chronological'.")

    schedule_df = pd.DataFrame(schedule)

    # Add timestamps if available
    if df is not None and "timestamp" in df.columns:
        schedule_df["start_ts"] = schedule_df["start_idx"].apply(
            lambda idx: df.iloc[idx]["timestamp"] if idx < len(df) else None
        )
        schedule_df["end_ts"] = schedule_df.apply(
            lambda row: df.iloc[min(row["start_idx"] + row["length_steps"] - 1, len(df) - 1)][
                "timestamp"
            ]
            if row["start_idx"] + row["length_steps"] <= len(df)
            else None,
            axis=1,
        )

    return schedule_df


def load_episode_schedule(schedule_path: Path) -> pd.DataFrame:
    """
    Load episode schedule from file (CSV or Parquet).

    Args:
        schedule_path: Path to schedule file

    Returns:
        DataFrame with episode schedule
    """
    if schedule_path.suffix == ".parquet":
        return pd.read_parquet(schedule_path)
    if schedule_path.suffix == ".csv":
        return pd.read_csv(schedule_path)
    raise ValueError(
        f"Unsupported schedule file format: {schedule_path.suffix}. Use .csv or .parquet"
    )


def run_evaluation_episode(
    env: ProductionTradingEnv,
    agent: SAC,
    episode_num: int,
    logger: logging.Logger,
    start_step: int | None = None,
) -> tuple[dict[str, Any], list]:
    """
    Run single evaluation episode with deterministic policy.

    Args:
        env: Trading environment
        agent: SAC agent
        episode_num: Episode number (for logging)
        logger: Logger instance
        start_step: Optional starting step index (for scheduled episodes)

    Returns:
        Tuple of (episode_summary dict, list of trades from env.trade_history)
    """
    reset_options = {"start_step": start_step} if start_step is not None else None
    obs, info = env.reset(options=reset_options)

    # DEBUG: Log tracker state after reset
    logger.debug(
        f"[EP{episode_num}] After reset: tracker_id={id(env.metrics_tracker)}, "
        f"equity_curve_len={len(env.metrics_tracker.equity_curve)}, "
        f"initial={env.metrics_tracker.equity_curve[0] if env.metrics_tracker.equity_curve else None}"
    )

    episode_reward = 0.0
    steps = 0
    done = False

    # Track episode start/end timestamps
    start_idx = env.current_step

    # Run episode with deterministic policy (no exploration)
    with torch.no_grad():
        while not done:
            # Select action deterministically (use mean of policy)
            action = agent.select_action(obs, deterministic=True)

            # Step environment
            next_obs, reward, terminated, truncated, step_info = env.step(action)
            done = terminated or truncated

            episode_reward += reward
            obs = next_obs
            steps += 1

    end_idx = env.current_step

    # DEBUG: Log tracker state before compute_all_metrics
    logger.debug(
        f"[EP{episode_num}] Before compute_metrics: equity_curve_len={len(env.metrics_tracker.equity_curve)}, "
        f"final={env.metrics_tracker.equity_curve[-1] if env.metrics_tracker.equity_curve else None}, "
        f"initial={env.metrics_tracker.initial_balance}"
    )

    # Get metrics from environment's tracker
    metrics = {}
    if hasattr(env, "metrics_tracker") and env.metrics_tracker is not None:
        metrics = env.metrics_tracker.compute_all_metrics()

    # DEBUG: Log computed return
    logger.debug(
        f"[EP{episode_num}] Computed total_return_pct={metrics.get('total_return_pct', 0.0):.4f}%"
    )

    # Get timestamp info
    start_time = env.data.iloc[start_idx]["timestamp"] if "timestamp" in env.data.columns else None
    end_time = env.data.iloc[end_idx - 1]["timestamp"] if "timestamp" in env.data.columns else None

    # Collect trades from this episode
    episode_trades = []
    if hasattr(env, "trade_history"):
        episode_trades = list(env.trade_history)

    # Build episode summary
    episode_summary = {
        "episode": episode_num,
        "episode_reward": episode_reward,
        "steps": steps,
        "start_idx": start_idx,
        "end_idx": end_idx,
        "start_time": start_time,
        "end_time": end_time,
        **metrics,  # Add all 20 performance metrics
    }

    logger.info(
        f"Episode {episode_num:4d} | "
        f"Reward: {episode_reward:8.2f} | "
        f"Sharpe: {metrics.get('sharpe_ratio', 0.0):6.3f} | "
        f"Return: {metrics.get('total_return_pct', 0.0):6.2f}% | "
        f"WinRate: {metrics.get('win_rate_pct', 0.0):5.1f}% | "
        f"Trades: {metrics.get('total_trades', 0):3.0f}"
    )

    return episode_summary, episode_trades


def run_continuous_evaluation(
    env: ProductionTradingEnv,
    agent: SAC,
    logger: logging.Logger,
    output_dir: Path,
    symbol: str | list[str],
    max_steps: int | None = None,
    commission_per_lot: float = 2.5,
    spread_pips: float = 0.2,
    slippage_bps: float = 0.0,
    slippage_pips_mean: float = 0.0,
    slippage_pips_std: float = 0.0,
    allow_positive_slippage: bool = False,
) -> None:
    """
    Run continuous backtest evaluation (no episodes, no resets).

    Args:
        env: Trading environment configured with full test data
        agent: SAC agent
        logger: Logger instance
        output_dir: Directory for output files
        symbol: Symbol being evaluated
        max_steps: Maximum number of steps to run (None = full dataset)
        commission_per_lot: Commission per lot (for metadata)
        spread_pips: Spread in pips (for metadata)
        slippage_bps: Slippage in basis points (for metadata, legacy)
        slippage_pips_mean: Slippage mean in pips (for metadata)
        slippage_pips_std: Slippage std in pips (for metadata)
        allow_positive_slippage: Whether positive slippage is allowed (for metadata)
    """
    logger.info("")
    logger.info("=" * 80)
    logger.info("CONTINUOUS BACKTEST MODE")
    logger.info("=" * 80)
    logger.info("Running single continuous evaluation over entire test set...")
    logger.info("No intermediate resets - tracking from start to finish")

    # Reset environment once at the start
    obs, info = env.reset()
    initial_balance = env.metrics_tracker.initial_balance

    logger.info(f"Initial balance: ${initial_balance:,.2f}")
    logger.info(f"Total bars to process: {len(env.data)}")

    # Track equity and returns at each timestep
    equity_history = [initial_balance]
    timestamps = []
    # Try both 'timestamp' and 'start_time' column names
    time_col = (
        "timestamp"
        if "timestamp" in env.data.columns
        else "start_time"
        if "start_time" in env.data.columns
        else None
    )
    if time_col:
        timestamps.append(env.data.iloc[0][time_col])

    total_reward = 0.0
    steps = 0
    done = False

    # Run until end of data (or max_steps if specified)
    with torch.no_grad():
        while not done:
            # Select action deterministically
            action = agent.select_action(obs, deterministic=True)

            # Step environment
            next_obs, reward, terminated, truncated, step_info = env.step(action)
            done = terminated or truncated

            # Check max_steps limit
            if max_steps is not None and steps >= max_steps:
                logger.info(f"✓ Reached max_steps limit: {max_steps:,} steps")
                done = True
                break

            # Record current equity
            current_equity = (
                env.metrics_tracker.equity_curve[-1]
                if env.metrics_tracker.equity_curve
                else initial_balance
            )
            equity_history.append(current_equity)

            # Record timestamp
            if time_col and env.current_step < len(env.data):
                timestamps.append(env.data.iloc[env.current_step][time_col])

            total_reward += reward
            obs = next_obs
            steps += 1

            # Progress logging
            if steps % 10000 == 0:
                logger.info(
                    f"  Step {steps:,} | Equity: ${current_equity:,.2f} | Reward: {total_reward:,.2f}"
                )

    logger.info(f"✓ Continuous evaluation complete: {steps:,} steps")

    # Get final metrics
    metrics = env.metrics_tracker.compute_all_metrics()
    final_equity = equity_history[-1]
    total_return_pct = ((final_equity - initial_balance) / initial_balance) * 100

    # Calculate and save returns series (needed for Sharpe calculation)
    equity_array = np.array(equity_history)
    returns_decimal = np.diff(equity_array) / equity_array[:-1]
    returns_pct = returns_decimal * 100

    # Calculate correct Sharpe ratio for continuous mode (1-minute data)
    # Annual factor for 1-minute data: 252 days * 24 hours * 60 minutes
    annual_factor_1min = 252 * 24 * 60
    mean_ret = returns_decimal.mean()
    std_ret = returns_decimal.std()
    sharpe_1min = (mean_ret / std_ret) * np.sqrt(annual_factor_1min) if std_ret > 0 else 0.0

    # Calculate Sharpe_daily from daily aggregated equity
    # Group equity by date and compute daily returns
    if timestamps and len(timestamps) >= len(equity_history):
        equity_df_for_daily = pd.DataFrame(
            {"timestamp": timestamps[: len(equity_history)], "equity": equity_history}
        )
        # FIX: Timestamps are in milliseconds, not nanoseconds
        equity_df_for_daily["date"] = pd.to_datetime(
            equity_df_for_daily["timestamp"], unit="ms"
        ).dt.date

        # Get unique dates and count
        unique_dates = equity_df_for_daily["date"].unique()
        num_days = len(unique_dates)
        start_date = str(unique_dates[0])
        end_date = str(unique_dates[-1])

        # Get last equity value for each day (closing equity)
        daily_equity = equity_df_for_daily.groupby("date")["equity"].last()

        # Calculate daily returns from closing equity
        daily_returns = daily_equity.pct_change().dropna()

        mean_daily_ret = daily_returns.mean()
        std_daily_ret = daily_returns.std()
        sharpe_daily = (mean_daily_ret / std_daily_ret) * np.sqrt(252) if std_daily_ret > 0 else 0.0
    else:
        # Fallback: If no timestamps, resample equity curve to daily
        # Assume 1440 steps per day (1-minute bars)
        equity_df_for_daily = pd.DataFrame({"equity": equity_history})
        equity_df_for_daily["day"] = equity_df_for_daily.index // 1440

        # Get start and end equity for each day
        daily_equity = (
            equity_df_for_daily.groupby("day")["equity"].agg(["first", "last"]).reset_index()
        )
        daily_returns = (daily_equity["last"] / daily_equity["first"] - 1).values

        mean_daily_ret = daily_returns.mean()
        std_daily_ret = daily_returns.std()
        sharpe_daily = (mean_daily_ret / std_daily_ret) * np.sqrt(252) if std_daily_ret > 0 else 0.0
        num_days = len(daily_returns)

    logger.info(f"Final equity: ${final_equity:,.2f}")
    logger.info(f"Total return: {total_return_pct:.2f}%")
    logger.info(f"Sharpe (1-min): {sharpe_1min:.3f}")
    logger.info(f"Sharpe (daily): {sharpe_daily:.3f} ({num_days} days)")
    logger.info(f"Total trades: {metrics.get('total_trades', 0):.0f}")

    # Log gross vs net metrics side by side
    logger.info("=" * 60)
    logger.info("  GROSS vs NET METRICS")
    logger.info("-" * 60)
    logger.info(
        f"  {'Metric':<25} {'Gross':>12} {'Net':>12}"
    )
    logger.info("-" * 60)
    logger.info(
        f"  {'Profit Factor':<25} {metrics.get('profit_factor', 0.0):>12.3f} "
        f"{metrics.get('profit_factor_net', 0.0):>12.3f}"
    )
    logger.info(
        f"  {'Win Rate %':<25} {metrics.get('win_rate_pct', 0.0):>12.1f} "
        f"{metrics.get('win_rate_net_pct', 0.0):>12.1f}"
    )
    logger.info(
        f"  {'EV / trade (USD)':<25} {metrics.get('expected_value_per_trade', 0.0):>12.4f} "
        f"{metrics.get('expected_value_net_per_trade', 0.0):>12.4f}"
    )
    logger.info(
        f"  {'Total PnL (USD)':<25} {metrics.get('total_pnl_gross_usd', 0.0):>12.2f} "
        f"{metrics.get('total_pnl_net_usd', 0.0):>12.2f}"
    )
    logger.info(
        f"  {'Total Costs (USD)':<25} {'':>12} "
        f"{metrics.get('total_costs_usd', 0.0):>12.2f}"
    )
    logger.info("=" * 60)

    # Calculate max drawdown from equity curve
    equity_array_mdd = np.array(equity_history)
    running_max = np.maximum.accumulate(equity_array_mdd)
    drawdown = (equity_array_mdd - running_max) / running_max
    max_drawdown_pct = abs(drawdown.min()) * 100 if len(drawdown) > 0 else 0.0

    # Sanitize metrics dict to avoid complex numbers in JSON
    # Round floats to 2 decimal places for cleaner output
    def sanitize_for_json(value):
        """Convert complex/numpy types to JSON-serializable Python types."""
        if isinstance(value, complex):
            return round(float(value.real), 2)
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return round(float(value), 2)
        if isinstance(value, float):
            return round(value, 2)
        if isinstance(value, np.ndarray):
            return [
                round(float(x), 2) if isinstance(x, (float, np.floating)) else x
                for x in value.tolist()
            ]
        return value

    sanitized_metrics = {k: sanitize_for_json(v) for k, v in metrics.items()}

    # P0 AUDIT: Extract emergency brake stats from final info (if available)
    emergency_brake_triggered = getattr(env, "emergency_brake_trigger_count", 0) > 0
    emergency_brake_count = getattr(env, "emergency_brake_trigger_count", 0)
    emergency_brake_first_step = getattr(env, "emergency_brake_first_trigger_step", None)
    emergency_brake_steps_after = getattr(env, "emergency_brake_total_steps_after", 0)
    emergency_brake_trades_after = getattr(env, "emergency_brake_trades_after", 0)

    # Save continuous summary (use corrected Sharpe metrics)
    summary = {
        "symbol": symbol,
        "initial_balance": round(initial_balance, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "sharpe_1min": round(
            float(sharpe_1min.real) if isinstance(sharpe_1min, complex) else float(sharpe_1min), 2
        ),
        "sharpe_daily": round(
            float(sharpe_daily.real) if isinstance(sharpe_daily, complex) else float(sharpe_daily),
            2,
        ),
        "num_days": num_days,
        "start_date": start_date if "start_date" in locals() else None,
        "end_date": end_date if "end_date" in locals() else None,
        "total_steps": steps,
        "total_reward": round(total_reward, 2),
        **sanitized_metrics,
        "sharpe_ratio": round(
            float(sharpe_1min.real) if isinstance(sharpe_1min, complex) else float(sharpe_1min), 2
        ),  # Keep for backwards compatibility
        # P0: Cost provenance - store effective runtime values
        "commission_per_lot": commission_per_lot,
        "spread_pips": spread_pips,
        "slippage_bps": slippage_bps,
        "slippage_pips_mean": slippage_pips_mean,
        "slippage_pips_std": slippage_pips_std,
        "allow_positive_slippage": allow_positive_slippage,
        "slippage_includes_spread": True,
        # P0 AUDIT: Emergency brake instrumentation
        "emergency_brake_triggered": emergency_brake_triggered,
        "emergency_brake_count": emergency_brake_count,
        "emergency_brake_first_step": emergency_brake_first_step,
        "emergency_brake_steps_after": emergency_brake_steps_after,
        "emergency_brake_trades_after": emergency_brake_trades_after,
    }

    summary_path = output_dir / "continuous_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    logger.info(f"✓ Saved summary to: {summary_path}")

    # Save equity curve
    equity_data = {"equity": equity_history}
    if timestamps and len(timestamps) == len(equity_history):
        equity_data["timestamp"] = timestamps
    elif timestamps and len(timestamps) > 0:
        # Align timestamps: equity_history has N+1 elements (initial + N steps)
        # timestamps has N or N+1 elements depending on collection
        if len(timestamps) == len(equity_history) - 1:
            # Add initial timestamp (same as first data point)
            equity_data["timestamp"] = [timestamps[0]] + timestamps
        else:
            equity_data["timestamp"] = timestamps[: len(equity_history)]

    equity_df = pd.DataFrame(equity_data)
    equity_path = output_dir / "equity_curve_continuous.parquet"
    equity_df.to_parquet(equity_path, index=False)
    logger.info(f"✓ Saved equity curve to: {equity_path} ({len(equity_df)} rows)")

    # Save returns series (already calculated above for Sharpe)
    returns_data = {
        "ret_decimal": returns_decimal,
        "ret_pct": returns_pct,
    }
    # Returns have len(steps), timestamps collected during loop also have len(steps)
    # BUT if we added initial timestamp, timestamps has len(steps)+1
    if timestamps:
        if len(timestamps) == len(returns_decimal) + 1:
            # Skip initial timestamp
            returns_data["timestamp"] = timestamps[1:]
        elif len(timestamps) == len(returns_decimal):
            # Already aligned
            returns_data["timestamp"] = timestamps
        else:
            # Mismatch - truncate to match
            returns_data["timestamp"] = timestamps[: len(returns_decimal)]

    returns_df = pd.DataFrame(returns_data)
    returns_path = output_dir / "returns_series_continuous.parquet"
    returns_df.to_parquet(returns_path, index=False)
    logger.info(f"✓ Saved returns series to: {returns_path} ({len(returns_df)} rows)")

    # Save trades
    if hasattr(env, "trade_history") and env.trade_history:
        from dataclasses import asdict

        trades_data = [asdict(trade) for trade in env.trade_history]
        trades_df = pd.DataFrame(trades_data)
        trades_path = output_dir / "trades_continuous.parquet"
        trades_df.to_parquet(trades_path, index=False)
        logger.info(f"✓ Saved trades to: {trades_path} ({len(trades_df)} trades)")

        # INTEGRITY AUDIT: Verify fixed_lots mode integrity
        if env.config.position_sizing_mode == "fixed_lots" and env.config.disable_risk_scaling:
            logger.info("Running fixed_lots integrity audit...")

            expected_units = int(env.config.fixed_lots * env.config.lot_size)

            # Check each trade for exact lot size
            exact_matches = (trades_df["units"].abs() == expected_units).sum()
            total_trades = len(trades_df)
            pct_exact = (exact_matches / total_trades * 100) if total_trades > 0 else 0.0
            pct_mismatch = 100.0 - pct_exact

            # Get mismatch examples
            mismatches = trades_df[trades_df["units"].abs() != expected_units]
            mismatch_examples = []
            for idx, row in mismatches.head(10).iterrows():
                mismatch_examples.append(
                    {
                        "trade_idx": int(idx),
                        "units": float(row["units"]),
                        "units_expected": expected_units,
                        "diff": float(abs(row["units"]) - expected_units),
                        "entry_time": int(row["entry_time"]),
                        "exit_time": int(row["exit_time"]),
                    }
                )

            # Check cap hits
            max_lots_hit_count = (
                trades_df["max_lots_hit"].sum() if "max_lots_hit" in trades_df.columns else 0
            )
            max_leverage_hit_count = (
                trades_df["max_leverage_hit"].sum()
                if "max_leverage_hit" in trades_df.columns
                else 0
            )

            integrity_report = {
                "position_sizing_mode": str(env.config.position_sizing_mode),
                "fixed_lots": float(env.config.fixed_lots),
                "disable_risk_scaling": bool(env.config.disable_risk_scaling),
                "expected_units": int(expected_units),
                "total_trades": int(total_trades),
                "exact_matches": int(exact_matches),
                "pct_exact_lots": float(pct_exact),
                "pct_mismatch_lots": float(pct_mismatch),
                "mismatch_examples": mismatch_examples,
                "max_lots_hit_count": int(max_lots_hit_count),
                "max_leverage_hit_count": int(max_leverage_hit_count),
                "final_equity": float(final_equity),
                "total_steps": int(steps),
                "passed": bool(pct_exact == 100.0),
            }

            integrity_path = output_dir / "integrity_report.json"
            with open(integrity_path, "w") as f:
                json.dump(integrity_report, f, indent=2)

            if pct_exact == 100.0:
                logger.info(f"✅ Integrity check PASSED: {pct_exact:.2f}% exact lots")
            else:
                logger.warning(
                    f"⚠️  Integrity check FAILED: {pct_exact:.2f}% exact lots, {len(mismatch_examples)} mismatches"
                )

                # Write INVALID marker
                invalid_path = output_dir / "INVALID_REASON.txt"
                with open(invalid_path, "w") as f:
                    f.write("INVALID: Fixed lots integrity violation\n")
                    f.write(f"Expected: {expected_units} units ({env.config.fixed_lots} lots)\n")
                    f.write(f"Actual: {pct_exact:.2f}% exact, {pct_mismatch:.2f}% mismatch\n")
                    f.write(f"Total trades: {total_trades}\n")
                    f.write(f"Exact matches: {exact_matches}\n")
                    f.write(f"Max lots hit: {max_lots_hit_count}\n")
                    f.write(f"Max leverage hit: {max_leverage_hit_count}\n\n")
                    f.write("First 10 mismatches:\n")
                    for ex in mismatch_examples:
                        f.write(
                            f"  Trade {ex['trade_idx']}: units={ex['units']:.0f} (expected {ex['units_expected']}), diff={ex['diff']:.0f}\n"
                        )

                logger.error(f"❌ Run marked INVALID: {invalid_path}")
    else:
        logger.warning("No trades recorded")

    # Calculate and save extended metrics
    try:
        from atlasfx.evaluation.extended_metrics import (
            calculate_extended_metrics,
            extended_metrics_to_dict_with_metadata,
        )

        extended_metrics = calculate_extended_metrics(
            equity_curve=equity_array,
            returns=returns_decimal,
            trades=list(env.trade_history) if hasattr(env, "trade_history") else [],
            annualized_return=total_return_pct,  # Use total return as proxy
            initial_balance=initial_balance,
            observed_sharpe=sharpe_1min,  # Use corrected 1-min Sharpe
            n_trials=1,
            skewness=float(pd.Series(returns_decimal).skew()) if len(returns_decimal) > 0 else 0.0,
            kurtosis=float(pd.Series(returns_decimal).kurtosis() + 3)
            if len(returns_decimal) > 0
            else 3.0,
        )

        metrics_dict = extended_metrics_to_dict_with_metadata(extended_metrics)
        meta = metrics_dict.pop("_meta")

        # Save extended metrics
        extended_df = pd.DataFrame([metrics_dict])
        extended_path = output_dir / "extended_metrics_continuous.csv"
        extended_df.to_csv(extended_path, index=False)
        logger.info(f"✓ Saved extended metrics to: {extended_path}")

        # Save metadata
        meta_info = {
            "schema_version": "3.0.0",
            "metrics": meta,
            "evaluation_mode": "continuous",
            "data_availability": {
                "has_timestamps": len(timestamps) > 0,
                "has_exit_reason": False,
                "has_mae_mfe": False,
                "has_position_tracking": False,
            },
            "notes": "Continuous backtest - no episode resets",
        }

        meta_path = output_dir / "extended_metrics_continuous_meta.json"
        with open(meta_path, "w") as f:
            json.dump(meta_info, f, indent=2)
        logger.info(f"✓ Saved metadata to: {meta_path}")

    except NotImplementedError as e:
        logger.warning(f"Extended metrics calculation not available for this configuration: {e}")
        logger.info("Continuing without extended metrics (all other outputs saved successfully)")
    except Exception as e:
        logger.error(f"Failed to calculate extended metrics: {e}")
        logger.info("Continuing without extended metrics (all other outputs saved successfully)")


def evaluate_full_testset(
    data_path: Path,
    checkpoint_path: Path,
    output_dir: Path,
    episode_length: int,
    symbol: str | list[str],
    use_vae_features: bool,
    logger: logging.Logger,
    eval_mode: str = "episode",
    min_tp_sl_ratio: float = 1.5,
    max_tp_sl_ratio: float = 3.0,
    min_sl_pips: float = 0.0,
    enable_break_even_stop: bool = False,
    break_even_trigger_r: float = 0.8,
    break_even_buffer_mode: str = "auto",
    break_even_buffer_pips: float = None,
    enable_trailing_stop: bool = False,
    trailing_start_r: float = 1.5,
    trailing_atr_multiple: float = 1.0,
    trailing_min_distance_pips: float = None,
    max_leverage: float | None = None,
    max_lots: float | None = 50.0,
    max_steps: int | None = None,
    position_sizing_mode: str = "agent",
    fixed_lots: float = 1.0,
    disable_risk_scaling: bool = False,
    commission_per_lot: float = 2.5,
    spread_pips: float = 0.2,
    slippage_bps: float = 0.0,
    slippage_pips_mean: float = 0.0,
    slippage_pips_std: float = 0.0,
    allow_positive_slippage: bool = False,
    action_penalty: float = 0.0,
    position_dead_zone: float = 0.0,
    min_hold_period: int = 0,
    hidden_dims: list[int] | None = None,
    save_episode_schedule: Path | None = None,
    episode_schedule_path: Path | None = None,
    schedule_seed: int = 42,
    schedule_mode: str = "random",
) -> None:
    """
    Evaluate SAC model on full test set.

    Args:
        data_path: Path to test data parquet file
        checkpoint_path: Path to SAC checkpoint
        output_dir: Directory for output files
        episode_length: Number of bars per episode
        symbol: Symbol to trade
        use_vae_features: If True, use VAE latent features
        logger: Logger instance
        eval_mode: "episode" or "continuous" - evaluation mode
        max_leverage: Maximum leverage allowed (None = no cap)
        position_sizing_mode: "agent" (default) or "fixed_lots"
        fixed_lots: Fixed lot size when position_sizing_mode="fixed_lots"
        hidden_dims: Hidden layer sizes for SAC network (default: [256, 256])
    """
    # Setup
    output_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Using device: {device}")
    logger.info(f"Evaluation mode: {eval_mode}")

    # Create run_config.json for provenance tracking
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parent.parent,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except:
        git_commit = None

    run_config = {
        "checkpoint_path": str(checkpoint_path.resolve()),
        "symbol": symbol,
        "episode_length": episode_length,
        "test_data_path": str(data_path.resolve()),
        "eval_mode": eval_mode,
        "schedule_mode": schedule_mode if eval_mode == "episode" else None,
        "schedule_seed": schedule_seed if eval_mode == "episode" else None,
        "schedule_path": str(episode_schedule_path.resolve())
        if episode_schedule_path and eval_mode == "episode"
        else None,
        "save_episode_schedule": str(save_episode_schedule.resolve())
        if save_episode_schedule and eval_mode == "episode"
        else None,
        "use_vae_features": use_vae_features,
        "min_tp_sl_ratio": min_tp_sl_ratio,
        "max_tp_sl_ratio": max_tp_sl_ratio,
        "min_sl_pips": min_sl_pips,
        "enable_break_even_stop": enable_break_even_stop,
        "break_even_trigger_r": break_even_trigger_r,
        "break_even_buffer_mode": break_even_buffer_mode,
        "break_even_buffer_pips": break_even_buffer_pips,
        "enable_trailing_stop": enable_trailing_stop,
        "trailing_start_r": trailing_start_r,
        "trailing_atr_multiple": trailing_atr_multiple,
        "trailing_min_distance_pips": trailing_min_distance_pips,
        "max_leverage": max_leverage,
        "max_lots": max_lots,
        "max_steps": max_steps if eval_mode == "continuous" else None,
        "position_sizing_mode": position_sizing_mode,
        "fixed_lots": fixed_lots if position_sizing_mode == "fixed_lots" else None,
        "disable_risk_scaling": disable_risk_scaling,
        # P0: Cost provenance - store effective runtime values
        "commission_per_lot": commission_per_lot,
        "spread_pips": spread_pips,
        "slippage_bps": slippage_bps,
        "slippage_pips_mean": slippage_pips_mean,
        "slippage_pips_std": slippage_pips_std,
        "allow_positive_slippage": allow_positive_slippage,
        "slippage_includes_spread": True,
        "action_penalty": action_penalty,
        "position_dead_zone": position_dead_zone,
        "min_hold_period": min_hold_period,
        "git_commit": git_commit,
        "timestamp": datetime.now().isoformat(),
        "script_version": "eval_sac_full_testset.py",
    }

    run_config_path = output_dir / "run_config.json"
    with open(run_config_path, "w") as f:
        json.dump(run_config, f, indent=2)
    logger.info(f"✓ Saved run configuration to: {run_config_path}")

    # Load test data
    logger.info(f"Loading test data from: {data_path}")
    df = pd.read_parquet(data_path)
    logger.info(f"Test data shape: {df.shape}")
    logger.info(f"Test data columns: {len(df.columns)}")

    # Normalize symbol name(s)
    if isinstance(symbol, str):
        symbol_keys = [_normalize_symbol(symbol)]
    else:
        symbol_keys = [_normalize_symbol(s) for s in symbol]
    logger.info(f"Using symbol key(s): {symbol_keys}")
    logger.info(f"Using VAE features: {use_vae_features}")

    # Create environment
    logger.info("Creating evaluation environment...")
    env = create_environment(
        df,
        symbol_keys if len(symbol_keys) > 1 else symbol_keys[0],
        episode_length,
        use_vae_features,
        min_tp_sl_ratio,
        max_tp_sl_ratio,
        min_sl_pips,
        enable_break_even_stop,
        break_even_trigger_r,
        break_even_buffer_mode,
        break_even_buffer_pips,
        enable_trailing_stop,
        trailing_start_r,
        trailing_atr_multiple,
        trailing_min_distance_pips,
        continuous_mode=(eval_mode == "continuous"),
        max_leverage=max_leverage,
        max_lots=max_lots,
        position_sizing_mode=position_sizing_mode,
        fixed_lots=fixed_lots,
        disable_risk_scaling=disable_risk_scaling,
        commission_per_lot=commission_per_lot,
        spread_pips=spread_pips,
        slippage_bps=slippage_bps,
        slippage_pips_mean=slippage_pips_mean,
        slippage_pips_std=slippage_pips_std,
        allow_positive_slippage=allow_positive_slippage,
        action_penalty=action_penalty,
        position_dead_zone=position_dead_zone,
        min_hold_period=min_hold_period,
    )
    logger.info(f"Environment state_dim: {env.state_dim}, action_dim: {env.action_dim}")

    # Load agent
    logger.info(f"Loading SAC agent from: {checkpoint_path}")
    agent = load_sac_agent(checkpoint_path, env.state_dim, env.action_dim, device, hidden_dims=hidden_dims)

    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    logger.info(f"Checkpoint from episode: {checkpoint.get('episode', 'unknown')}")
    logger.info(f"Checkpoint total steps: {checkpoint.get('total_steps', 'unknown')}")

    # Resolve symbol label for logging/provenance
    symbol_label = ",".join(symbol_keys) if len(symbol_keys) > 1 else symbol_keys[0]

    # Branch based on evaluation mode
    if eval_mode == "continuous":
        # CONTINUOUS MODE: Single run over entire test set
        run_continuous_evaluation(
            env=env,
            agent=agent,
            logger=logger,
            output_dir=output_dir,
            symbol=symbol_label,
            max_steps=max_steps,
            commission_per_lot=commission_per_lot,
            spread_pips=spread_pips,
            slippage_bps=slippage_bps,
            slippage_pips_mean=slippage_pips_mean,
            slippage_pips_std=slippage_pips_std,
            allow_positive_slippage=allow_positive_slippage,
        )
        logger.info("=" * 80)
        logger.info(f"Continuous evaluation complete. Results saved to: {output_dir}")
        logger.info("=" * 80)
        return  # Exit early - continuous mode complete

    # EPISODE MODE: Original behavior with scheduled episodes
    # Generate or load episode schedule
    total_bars = len(df)
    logger.info(f"Total bars: {total_bars}")
    logger.info(f"Episode length: {episode_length}")

    if episode_schedule_path is not None:
        # Load existing schedule
        logger.info(f"Loading episode schedule from: {episode_schedule_path}")
        schedule = load_episode_schedule(episode_schedule_path)
        logger.info(f"Loaded {len(schedule)} episodes from schedule")

        # Validate bounds
        for idx, row in schedule.iterrows():
            ep_id = int(row["episode_id"])
            start_idx = int(row["start_idx"])
            length = int(row["length_steps"])

            if start_idx < 0:
                raise ValueError(f"Episode {ep_id}: Invalid start_idx={start_idx} (must be >= 0)")
            if start_idx + length > total_bars:
                raise ValueError(
                    f"Episode {ep_id}: Window [{start_idx}, {start_idx + length}) "
                    f"exceeds data bounds [0, {total_bars}). "
                    f"start_idx={start_idx}, length_steps={length}, total_bars={total_bars}"
                )

        logger.info(f"✓ All {len(schedule)} episodes validated (within data bounds)")
    else:
        # Generate new schedule
        num_episodes = total_bars // episode_length
        logger.info(f"Generating {schedule_mode} episode schedule with seed={schedule_seed}")
        schedule = generate_episode_schedule(
            total_bars=total_bars,
            episode_length=episode_length,
            num_episodes=num_episodes,
            mode=schedule_mode,
            seed=schedule_seed,
            df=df,
        )
        logger.info(f"Generated {len(schedule)} episodes")

    # Save schedule if requested
    if save_episode_schedule is not None:
        save_episode_schedule.parent.mkdir(parents=True, exist_ok=True)
        if save_episode_schedule.suffix == ".parquet":
            schedule.to_parquet(save_episode_schedule, index=False)
        else:
            schedule.to_csv(save_episode_schedule, index=False)
        logger.info(f"Saved episode schedule to: {save_episode_schedule}")

    # Always save schedule to output dir for reproducibility
    schedule_output = output_dir / "episode_schedule.parquet"
    schedule.to_parquet(schedule_output, index=False)
    logger.info(f"Saved episode schedule to output dir: {schedule_output}")

    logger.info("")
    logger.info("=" * 80)
    logger.info("Starting full test set evaluation...")
    logger.info("=" * 80)

    # Run evaluation episodes using schedule
    episode_results = []
    all_trades = []  # Collect trades from all episodes

    for idx, row in schedule.iterrows():
        ep_id = int(row["episode_id"])
        start_idx = int(row["start_idx"])

        try:
            episode_summary, episode_trades = run_evaluation_episode(
                env, agent, ep_id, logger, start_step=start_idx
            )
            episode_results.append(episode_summary)
            all_trades.extend(episode_trades)
        except Exception as e:
            logger.error(f"Error in episode {ep_id}: {e}")
            # Try to continue with next episode
            try:
                env.reset()
            except Exception as reset_error:
                logger.error(f"Failed to reset environment: {reset_error}")
                break

    # Save results
    logger.info("")
    logger.info("=" * 80)
    logger.info("Saving results...")
    logger.info("=" * 80)

    results_df = pd.DataFrame(episode_results)
    # Add symbol column for provenance
    results_df["symbol"] = symbol_label
    csv_path = output_dir / "episode_metrics.csv"
    results_df.to_csv(csv_path, index=False)
    logger.info(f"Saved episode metrics to: {csv_path}")

    # Save all trades to parquet
    if all_trades:
        from dataclasses import asdict

        trades_data = [asdict(trade) for trade in all_trades]
        trades_df = pd.DataFrame(trades_data)
        trades_path = output_dir / "trades.parquet"
        trades_df.to_parquet(trades_path, index=False)
        logger.info(f"Saved {len(trades_df)} trades to: {trades_path}")
        logger.info(f"Trade columns: {list(trades_df.columns)}")
    else:
        logger.warning("No trades to save")

    # =========================================================================
    # Calculate and save extended metrics
    # =========================================================================
    logger.info("")
    logger.info("=" * 80)
    logger.info("CALCULATING EXTENDED METRICS")
    logger.info("=" * 80)

    try:
        import numpy as np

        from atlasfx.evaluation.extended_metrics import (
            calculate_extended_metrics,
            extended_metrics_to_dict_with_metadata,
        )

        # Extract equity curve and returns from episode results
        if "total_return_pct" in results_df.columns:
            # NOTE: Each episode is independent (starts at $10k, tracked separately)
            # For extended metrics, we treat them as sequential with COMPOUND GROWTH
            # This simulates a continuous equity curve where each episode's % return
            # is applied to the running balance (not fixed $10k)

            episode_returns = results_df["total_return_pct"].values / 100.0  # Convert to decimal

            # Build cumulative equity curve with COMPOUND GROWTH
            initial_balance = 10000.0
            equity_curve = [initial_balance]

            for i, ep_return in enumerate(episode_returns):
                # COMPOUND: Apply return to current balance
                # new_balance = current_balance * (1 + return_rate)
                new_balance = equity_curve[-1] * (1.0 + ep_return)
                equity_curve.append(new_balance)

            equity_curve = np.array(equity_curve)
            returns = np.diff(equity_curve) / equity_curve[:-1]

            # Save equity curve artifact
            equity_df = pd.DataFrame({"step_idx": range(len(equity_curve)), "equity": equity_curve})
            equity_path = output_dir / "equity_curve.parquet"
            equity_df.to_parquet(equity_path, index=False)
            logger.info(f"Saved equity curve to: {equity_path}")

            # Save returns series artifact
            returns_df = pd.DataFrame({"step_idx": range(len(returns)), "ret": returns})
            returns_path = output_dir / "returns_series.parquet"
            returns_df.to_parquet(returns_path, index=False)
            logger.info(f"Saved returns series to: {returns_path}")

            # Calculate extended metrics
            ann_return = (
                results_df["total_return_pct"].mean()
                if "total_return_pct" in results_df.columns
                else 0.0
            )
            sharpe = (
                results_df["sharpe_ratio"].mean() if "sharpe_ratio" in results_df.columns else 0.0
            )

            extended_metrics = calculate_extended_metrics(
                equity_curve=equity_curve,
                returns=returns,
                trades=all_trades,
                annualized_return=ann_return,
                initial_balance=10000.0,
                observed_sharpe=sharpe,
                n_trials=1,  # Single seed evaluation
                skewness=float(pd.Series(returns).skew()) if len(returns) > 0 else 0.0,
                kurtosis=float(pd.Series(returns).kurtosis() + 3) if len(returns) > 0 else 3.0,
            )

            # Convert to dict with metadata
            metrics_dict = extended_metrics_to_dict_with_metadata(extended_metrics)

            # Separate metrics and metadata
            meta = metrics_dict.pop("_meta")

            # Save extended metrics CSV
            extended_df = pd.DataFrame([metrics_dict])
            extended_path = output_dir / "extended_metrics.csv"
            extended_df.to_csv(extended_path, index=False)
            logger.info(f"Saved {len(metrics_dict)} extended metrics to: {extended_path}")

            # Save metadata JSON
            meta_info = {
                "schema_version": "3.0.0",
                "metrics": meta,
                "data_availability": {
                    "has_timestamps": False,  # Episode-based, no real timestamps
                    "has_exit_reason": False,  # Not tracked yet
                    "has_mae_mfe": False,  # Not tracked yet
                    "has_position_tracking": False,  # Not tracked yet
                },
                "quality_summary": {
                    "REAL": sum(1 for m in meta.values() if m["quality"] == "REAL"),
                    "APPROX": sum(1 for m in meta.values() if m["quality"] == "APPROX"),
                    "PROXY": sum(1 for m in meta.values() if m["quality"] == "PROXY"),
                    "PARTIAL": sum(1 for m in meta.values() if m["quality"] == "PARTIAL"),
                },
                "notes": {
                    "temporal_metrics": "PROXY - using episode windows, not calendar months",
                    "portfolio_metrics": "APPROX - using trade-level data, not real-time positions",
                    "mae_mfe": "PARTIAL - not tracked, returns 0.0",
                    "margin_util": "PARTIAL - no explicit margin model, returns 0.0",
                },
                "schedule_mode": schedule_mode if episode_schedule_path is None else "loaded",
                "schedule_seed": schedule_seed if episode_schedule_path is None else None,
                "schedule_path": str(episode_schedule_path)
                if episode_schedule_path is not None
                else None,
                "num_episodes": len(schedule),
            }

            meta_path = output_dir / "extended_metrics_meta.json"
            with open(meta_path, "w") as f:
                json.dump(meta_info, f, indent=2)
            logger.info(f"Saved metrics metadata to: {meta_path}")

            # Print quality summary
            logger.info("")
            logger.info("Extended Metrics Quality Summary:")
            for quality, count in meta_info["quality_summary"].items():
                logger.info(f"  {quality:8s}: {count:2d} metrics")

        else:
            logger.warning("Missing 'total_return_pct' column, skipping extended metrics")

    except Exception as e:
        logger.error(f"Failed to calculate extended metrics: {e}")
        logger.error("Continuing with standard metrics only...")

    # =========================================================================

    # Compute and print summary statistics
    logger.info("")
    logger.info("=" * 80)
    logger.info("EVALUATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total episodes completed: {len(episode_results)}")
    logger.info(f"Total bars evaluated: {len(episode_results) * episode_length}")
    logger.info("")

    # Key metrics to summarize
    key_metrics = [
        "episode_reward",
        "sharpe_ratio",
        "total_return_pct",
        "win_rate_pct",
        "max_drawdown_pct",
        "total_trades",
        "profit_factor",
    ]

    logger.info("Key Metrics Summary:")
    logger.info("-" * 80)

    for metric in key_metrics:
        if metric in results_df.columns:
            values = results_df[metric].dropna()
            if len(values) > 0:
                logger.info(
                    f"{metric:25s} | "
                    f"Mean: {values.mean():8.3f} | "
                    f"Median: {values.median():8.3f} | "
                    f"Std: {values.std():8.3f} | "
                    f"P25: {values.quantile(0.25):8.3f} | "
                    f"P75: {values.quantile(0.75):8.3f}"
                )

    logger.info("=" * 80)
    logger.info(f"Full evaluation complete. Results saved to: {output_dir}")
    logger.info("=" * 80)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Evaluate SAC baseline model on full test set",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to configuration YAML file (optional)",
    )

    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("models/sac_baseline/final_checkpoint.pt"),
        help="Path to SAC checkpoint file",
    )

    parser.add_argument(
        "--data-path",
        type=Path,
        default=Path("data/1min_forex_data_test.parquet"),
        help="Path to test data parquet file",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/sac_full_test_eval"),
        help="Output directory for results",
    )

    parser.add_argument(
        "--episode-length",
        type=int,
        default=500,
        help="Number of bars per episode",
    )

    parser.add_argument(
        "--symbol",
        type=str,
        default="eurusd",
        help="Symbol to trade (e.g., 'eurusd'). For single-pair mode.",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        nargs="+",
        default=None,
        help="Multiple symbols for multi-pair eval (e.g., --symbols eurusd gbpusd usdjpy). Overrides --symbol.",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    parser.add_argument(
        "--use-vae-features",
        action="store_true",
        help="If set, use VAE latent features from *_vae.parquet datasets",
    )

    parser.add_argument(
        "--eval-mode",
        type=str,
        choices=["episode", "continuous"],
        default="episode",
        help="Evaluation mode: 'episode' (with resets) or 'continuous' (single run, no resets)",
    )

    # TP/SL ratio enforcement arguments
    parser.add_argument(
        "--min-tp-sl-ratio",
        type=float,
        default=1.5,
        help="Minimum TP/SL ratio in pips (TP must be at least this many times SL)",
    )
    parser.add_argument(
        "--max-tp-sl-ratio",
        type=float,
        default=3.0,
        help="Maximum TP/SL ratio in pips (TP must be at most this many times SL)",
    )

    # Break-even stop arguments
    parser.add_argument(
        "--enable-break-even-stop",
        action="store_true",
        help="Enable break-even stop (moves SL to entry + buffer after reaching trigger_r)",
    )
    parser.add_argument(
        "--break-even-trigger-r",
        type=float,
        default=0.8,
        help="Trigger break-even stop when profit reaches this R multiple (e.g., 0.8 = 80%% of initial SL)",
    )
    parser.add_argument(
        "--break-even-buffer-mode",
        type=str,
        choices=["auto", "fixed"],
        default="auto",
        help="Break-even buffer mode: 'auto' = spread + commission, 'fixed' = use --break-even-buffer-pips",
    )
    parser.add_argument(
        "--break-even-buffer-pips",
        type=float,
        default=None,
        help="Fixed buffer in pips for break-even stop (only used if --break-even-buffer-mode=fixed)",
    )

    # Trailing stop arguments
    parser.add_argument(
        "--enable-trailing-stop",
        action="store_true",
        help="Enable trailing stop (follows price with ATR-based distance after reaching start_r)",
    )
    parser.add_argument(
        "--trailing-start-r",
        type=float,
        default=1.5,
        help="Start trailing stop when profit reaches this R multiple (e.g., 1.5 = 150%% of initial SL)",
    )
    parser.add_argument(
        "--trailing-atr-multiple",
        type=float,
        default=1.0,
        help="Trailing stop distance as multiple of ATR (e.g., 1.0 = 1x ATR behind high/low watermark)",
    )
    parser.add_argument(
        "--trailing-min-distance-pips",
        type=float,
        default=None,
        help="Minimum trailing distance in pips (overrides ATR if larger)",
    )
    parser.add_argument(
        "--min-sl-pips",
        type=float,
        default=0.0,
        help="Tamaño mínimo del stop loss en pips. 0.0 = desactivado (usa solo ATR floor).",
    )

    # Leverage cap argument
    parser.add_argument(
        "--max-leverage",
        type=float,
        default=None,
        help="Maximum leverage allowed (e.g., 20.0 for 20x). None = no cap (backward compatible).",
    )

    # Anti-overtrading guards (must match training env)
    parser.add_argument(
        "--action-penalty",
        type=float,
        default=0.0,
        help="Penalty per trade executed (default: 0.0 = no penalty). Must match training setting.",
    )
    parser.add_argument(
        "--position-dead-zone",
        type=float,
        default=0.0,
        help="Min abs(target_pos_frac) to trigger a trade (default: 0.0 = disabled). Must match training.",
    )
    parser.add_argument(
        "--min-hold-period",
        type=int,
        default=0,
        help="Min bars to hold position before allowing changes (default: 0 = disabled). Must match training.",
    )

    # Position size cap argument
    parser.add_argument(
        "--max-lots",
        type=float,
        default=50.0,
        help="Maximum position size in lots (e.g., 50.0). None = no limit.",
    )

    # Max steps for continuous mode (mini-runs)
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maximum steps for continuous eval (for mini-runs). None = full dataset.",
    )

    # Position sizing mode arguments (size-invariant evaluation)
    parser.add_argument(
        "--position-sizing-mode",
        type=str,
        choices=["agent", "fixed_lots"],
        default="agent",
        help="Position sizing mode: 'agent' (default, agent determines size) or 'fixed_lots' (agent only determines direction)",
    )
    parser.add_argument(
        "--fixed-lots",
        type=float,
        default=1.0,
        help="Fixed lot size when --position-sizing-mode=fixed_lots (default: 1.0)",
    )

    # Risk scaling control (for pure fixed lots experiments)
    parser.add_argument(
        "--disable-risk-scaling",
        action="store_true",
        default=False,
        help="Disable risk-based position scaling (max_total_risk clamp). USE WITH CAUTION - for pure fixed lots experiments only.",
    )

    # Cost parameters (STEP 7A: configurable costs)
    parser.add_argument(
        "--commission-per-lot",
        type=float,
        default=2.5,
        help="Commission per lot (USD per side). Default: 2.5",
    )
    parser.add_argument(
        "--spread-pips",
        type=float,
        default=0.2,
        help="Spread cost in pips (embedded in slippage_usd). Default: 0.2",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=0.0,
        help="[DEPRECATED] Stochastic slippage in basis points. Use --slippage-pips-mean instead.",
    )
    parser.add_argument(
        "--slippage-pips-mean",
        type=float,
        default=0.0,
        help="Mean slippage in pips (pips-first path). Default: 0.0 (disabled)",
    )
    parser.add_argument(
        "--slippage-pips-std",
        type=float,
        default=0.0,
        help="Std of stochastic slippage in pips (half-normal). Default: 0.0 (deterministic)",
    )
    parser.add_argument(
        "--allow-positive-slippage",
        action="store_true",
        default=False,
        help="Allow favorable slippage (full normal instead of half-normal). Default: False",
    )

    # Episode schedule arguments
    parser.add_argument(
        "--save-episode-schedule",
        type=Path,
        default=None,
        help="Save episode schedule to this path (for reuse across symbols)",
    )
    parser.add_argument(
        "--episode-schedule-path",
        type=Path,
        default=None,
        help="Load episode schedule from this path (use same windows as other symbols)",
    )
    parser.add_argument(
        "--schedule-seed",
        type=int,
        default=42,
        help="Random seed for episode schedule generation (default: 42)",
    )
    parser.add_argument(
        "--schedule-mode",
        type=str,
        choices=["random", "chronological"],
        default="random",
        help="Episode schedule mode: random (with replacement) or chronological (sequential)",
    )
    parser.add_argument(
        "--hidden-dims",
        type=int,
        nargs="+",
        default=None,
        help="Hidden layer sizes for SAC network (default: [256, 256]). Use e.g. --hidden-dims 128 128 for compact models.",
    )

    args = parser.parse_args()

    # Validate min_sl_pips
    if args.min_sl_pips < 0.0:
        raise ValueError("min-sl-pips debe ser >= 0.0")

    # Validate TP/SL ratio parameters
    if args.min_tp_sl_ratio < 1.0:
        raise ValueError("--min-tp-sl-ratio must be >= 1.0")
    if args.max_tp_sl_ratio < args.min_tp_sl_ratio:
        raise ValueError("--max-tp-sl-ratio must be >= --min-tp-sl-ratio")

    # Validate break-even parameters
    if args.break_even_trigger_r <= 0.0:
        raise ValueError("--break-even-trigger-r must be > 0.0")
    if args.break_even_buffer_mode == "fixed" and args.break_even_buffer_pips is None:
        raise ValueError("--break-even-buffer-pips is required when --break-even-buffer-mode=fixed")
    if args.break_even_buffer_pips is not None and args.break_even_buffer_pips < 0.0:
        raise ValueError("--break-even-buffer-pips must be >= 0.0")

    # Validate trailing stop parameters
    if args.trailing_start_r < args.break_even_trigger_r:
        raise ValueError(
            "--trailing-start-r should be >= --break-even-trigger-r (trailing starts after break-even)"
        )
    if args.trailing_atr_multiple <= 0.0:
        raise ValueError("--trailing-atr-multiple must be > 0.0")
    if args.trailing_min_distance_pips is not None and args.trailing_min_distance_pips < 0.0:
        raise ValueError("--trailing-min-distance-pips must be >= 0.0")

    # Setup logging
    logger = setup_logging(args.log_level)

    # Load config if provided
    config = load_config(args.config)

    # Override with CLI args
    data_path = args.data_path
    checkpoint_path = args.checkpoint
    output_dir = args.output_dir
    episode_length = args.episode_length
    # Resolve symbol(s) from CLI
    if args.symbols:
        symbol = [_normalize_symbol(s) for s in args.symbols]
    else:
        symbol = _normalize_symbol(args.symbol) if args.symbol else ""

    # Validate inputs
    if not data_path.exists():
        logger.error(f"Test data not found: {data_path}")
        return

    if not checkpoint_path.exists():
        logger.error(f"Checkpoint not found: {checkpoint_path}")
        return

    # Run evaluation
    logger.info("=" * 80)
    logger.info("SAC BASELINE - FULL TEST SET EVALUATION")
    logger.info("=" * 80)
    logger.info(f"Test data: {data_path}")
    logger.info(f"Checkpoint: {checkpoint_path}")
    logger.info(f"Output dir: {output_dir}")
    logger.info(f"Episode length: {episode_length}")
    logger.info(f"Symbol(s): {symbol}")
    logger.info("")

    evaluate_full_testset(
        data_path=data_path,
        checkpoint_path=checkpoint_path,
        output_dir=output_dir,
        episode_length=episode_length,
        symbol=symbol,
        use_vae_features=args.use_vae_features,
        logger=logger,
        eval_mode=args.eval_mode,
        min_tp_sl_ratio=args.min_tp_sl_ratio,
        max_tp_sl_ratio=args.max_tp_sl_ratio,
        min_sl_pips=args.min_sl_pips,
        enable_break_even_stop=args.enable_break_even_stop,
        break_even_trigger_r=args.break_even_trigger_r,
        break_even_buffer_mode=args.break_even_buffer_mode,
        break_even_buffer_pips=args.break_even_buffer_pips,
        enable_trailing_stop=args.enable_trailing_stop,
        trailing_start_r=args.trailing_start_r,
        trailing_atr_multiple=args.trailing_atr_multiple,
        trailing_min_distance_pips=args.trailing_min_distance_pips,
        max_leverage=args.max_leverage,
        max_lots=args.max_lots,
        max_steps=args.max_steps,
        position_sizing_mode=args.position_sizing_mode,
        fixed_lots=args.fixed_lots,
        disable_risk_scaling=args.disable_risk_scaling,
        commission_per_lot=args.commission_per_lot,
        spread_pips=args.spread_pips,
        slippage_bps=args.slippage_bps,
        slippage_pips_mean=args.slippage_pips_mean,
        slippage_pips_std=args.slippage_pips_std,
        allow_positive_slippage=args.allow_positive_slippage,
        action_penalty=args.action_penalty,
        position_dead_zone=args.position_dead_zone,
        min_hold_period=args.min_hold_period,
        save_episode_schedule=args.save_episode_schedule,
        episode_schedule_path=args.episode_schedule_path,
        schedule_seed=args.schedule_seed,
        schedule_mode=args.schedule_mode,
        hidden_dims=args.hidden_dims,
    )


if __name__ == "__main__":
    main()
