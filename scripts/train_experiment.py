#!/usr/bin/env python3
"""
Train SAC agent on production environment with real Phase 2 data.

This script trains a Soft Actor-Critic (SAC) agent using the normalized
features from the Phase 2 data pipeline output.

Usage:
    python scripts/train_sac_production_env.py

Requirements:
    - data/1min_forex_data_train.parquet (from Phase 2 pipeline)
    - ProductionTradingEnv configured for single-pair trading
    - SAC agent with appropriate hyperparameters

Author: AtlasFX Team
Date: November 25, 2025
"""

from __future__ import annotations

import argparse
from pathlib import Path
import random

import numpy as np
import pandas as pd
import torch

from atlasfx.environments.trading_env import ProductionTradingConfig, ProductionTradingEnv
from atlasfx.models.sac import SAC, ReplayBuffer
from atlasfx.training.sac_trainer import SACTrainer
from atlasfx.utils.logging import get_logger


logger = get_logger(__name__)


def set_global_seed(seed: int) -> None:
    """Set random seed for reproducibility across all libraries."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def load_training_data(data_path: str | Path) -> pd.DataFrame:
    """
    Load and validate Phase 2 training data.

    Args:
        data_path: Path to training parquet file

    Returns:
        Training DataFrame with all features and OHLC columns

    Raises:
        FileNotFoundError: If data file doesn't exist
        ValueError: If required columns are missing
    """
    path = Path(data_path)
    if not path.exists():
        msg = f"Training data not found: {path}"
        logger.critical(msg)
        raise FileNotFoundError(msg)

    logger.info(f"Loading training data from {path}")
    df = pd.read_parquet(path)
    logger.info(f"  Loaded {len(df):,} rows × {len(df.columns)} columns")

    # Validate required columns exist (at least for one pair as MVP)
    required_patterns = ["open", "high", "low", "close", "[Feature]"]
    for pattern in required_patterns:
        if not any(pattern in col for col in df.columns):
            msg = f"Missing required column pattern: {pattern}"
            logger.critical(msg)
            raise ValueError(msg)

    logger.info("✅ Training data loaded successfully")
    return df


def _normalize_symbol(symbol: str) -> str:
    """Normalize a symbol string to the 'xxxyyy-pair' format used in parquet columns."""
    raw = symbol.strip()
    if " | " in raw:
        return raw.split(" | ")[0]
    sym_lower = raw.lower()
    return sym_lower if sym_lower.endswith("-pair") else f"{sym_lower}-pair"


def create_environment(
    df: pd.DataFrame,
    symbol: str | list[str] = "",
    use_vae_features: bool = False,
    loss_penalty_factor: float = 1.0,
    action_penalty: float = 0.0,
    position_dead_zone: float = 0.0,
    min_hold_period: int = 0,
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
    slippage_pips_mean: float = 0.0,
    slippage_pips_std: float = 0.0,
    allow_positive_slippage: bool = False,
    lambda_risk_penalty: float | None = None,
    lambda_clamp_penalty: float | None = None,
    lambda_turnover: float = 0.0,
    lambda_flip: float = 0.0,
    enter_threshold: float = 0.0,
    exit_threshold: float = 0.0,
    cooldown_bars: int = 0,
) -> ProductionTradingEnv:
    """
    Create ProductionTradingEnv from Phase 2 data.
    Args:
        df: Training DataFrame from Phase 2 pipeline.
        symbol: Currency pair symbol(s). Single string "eurusd" or list ["eurusd", "gbpusd"].
        use_vae_features: If True, use VAE latent features instead of raw features.

    Returns:
        Configured ProductionTradingEnv instance.
    """
    # Support both single symbol (str) and multi-pair (list)
    if isinstance(symbol, str):
        symbol_keys = [_normalize_symbol(symbol)]
    else:
        symbol_keys = [_normalize_symbol(s) for s in symbol]

    logger.info(f"Creating environment for {len(symbol_keys)} symbol(s): {symbol_keys}")

    price_cols = {}
    for sk in symbol_keys:
        price_cols[sk] = {
            "open": f"{sk} | open",
            "high": f"{sk} | high",
            "low": f"{sk} | low",
            "close": f"{sk} | close",
        }

    # Verificar que existan las columnas OHLC
    for sk in symbol_keys:
        for price_type, col_name in price_cols[sk].items():
            if col_name not in df.columns:
                msg = f"Missing price column: {col_name}"
                logger.critical(msg)
                raise ValueError(msg)

    # Config producción REALISTA:
    # - ATR real en pips (columna "... | atr_14_real_pips")
    # - pip_value_per_lot ~ 10 USD/pip/lot
    # - comisión 5 USD por lote por lado
    # - episode_length=500 para episodios más largos
    config = ProductionTradingConfig(
        initial_balance=10_000.0,
        max_risk_per_trade_pct=0.02,  # 2% risk per trade
        episode_length=500,  # 500 steps per episode (~8.3 hours at 1min)
        commission_per_lot=2.5,  # 2.5 USD per lot per side (5 USD round turn)
        pip_value_per_lot=10.0,  # 10 USD per pip per lot (majors)
        spread_pips=0.2,  # 0.2 pips spread
        action_penalty=action_penalty,  # Default: 0.0 (no penalty); override via --action-penalty
        position_dead_zone=position_dead_zone,  # Anti-overtrading: ignore tiny position changes
        min_hold_period=min_hold_period,  # Anti-overtrading: min bars before position change
        loss_penalty_factor=loss_penalty_factor,  # Asymmetric penalty for negative rewards
        use_vae_features=use_vae_features,  # Enable VAE features if requested
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
        # ── Position sizing caps ──
        # Hardening caps (concentration, lots) disabled to match E016.
        # max_leverage: configurable via CLI (None = uncapped, e.g. 20.0 = 20x)
        max_concentration_pct_per_symbol=10000.0,  # Effectively disabled (E016 had no cap)
        max_position_lots=None,  # No per-trade lot limit (E016 had none)
        max_lots_per_symbol=1000.0,  # Effectively disabled (E016 had no cap)
        max_leverage=max_leverage,  # Leverage cap (None=uncapped, 20.0=20x, etc.)
        # Risk/clamp penalty overrides (for multi-pair scaling)
        **(
            dict(lambda_risk_penalty=lambda_risk_penalty) if lambda_risk_penalty is not None else {}
        ),
        **(
            dict(lambda_clamp_penalty=lambda_clamp_penalty)
            if lambda_clamp_penalty is not None
            else {}
        ),
        # Slippage (pips-first)
        slippage_pips_mean=slippage_pips_mean,
        slippage_pips_std=slippage_pips_std,
        slippage_half_normal=not allow_positive_slippage,
        allow_positive_slippage=allow_positive_slippage,
        # ── Sniper-gate / cooldown / turnover / flip ──
        lambda_turnover=lambda_turnover,
        lambda_flip=lambda_flip,
        enter_threshold=enter_threshold,
        exit_threshold=exit_threshold,
        cooldown_bars=cooldown_bars,
    )

    env = ProductionTradingEnv(
        data=df,
        symbols=symbol_keys,
        price_cols=price_cols,
        config=config,
    )

    logger.info("✅ Environment created:")
    logger.info(f"   Symbols: {symbol_keys}")
    logger.info(f"   Num assets: {len(symbol_keys)}")
    logger.info(f"   State dim: {env.state_dim}")
    logger.info(f"   Action dim: {env.action_dim}")
    logger.info(f"   Episode length: {config.episode_length} steps")
    logger.info(f"   Action penalty: {config.action_penalty} (overtrading penalty - 5x)")
    logger.info(f"   Loss penalty factor: {config.loss_penalty_factor} (asymmetric penalty)")
    logger.info(
        f"   Max leverage: {config.max_leverage} ({'uncapped' if config.max_leverage is None else f'{config.max_leverage}x'})"
    )

    return env


def create_agent_and_buffer(
    state_dim: int,
    action_dim: int,
    hidden_dims: list[int],
    device: str,
    lr_actor: float = 3e-4,
    lr_critic: float = 3e-4,
    gamma: float = 0.99,
) -> tuple[SAC, ReplayBuffer]:
    """
    Create SAC agent and replay buffer.

    Args:
        state_dim: Observation space dimensionality
        action_dim: Action space dimensionality (should be 3)
        hidden_dims: Hidden layer dimensions for networks
        device: Device for training ("cuda" or "cpu")
        lr_actor: Learning rate for actor network
        lr_critic: Learning rate for critic networks
        gamma: Discount factor for future rewards

    Returns:
        Tuple of (SAC agent, ReplayBuffer)
    """
    logger.info("Creating SAC agent and replay buffer...")

    agent = SAC(
        state_dim=state_dim,
        action_dim=action_dim,
        hidden_dims=hidden_dims,
        device=device,
        lr_actor=lr_actor,
        lr_critic=lr_critic,
        gamma=gamma,
    )

    # La firma real del ReplayBuffer es:
    # ReplayBuffer(capacity: int, state_dim: int, action_dim: int)
    buffer = ReplayBuffer(
        capacity=1_000_000,
        state_dim=state_dim,
        action_dim=action_dim,
    )

    logger.info(f"✅ Agent created on device: {device}")
    logger.info(f"   Hidden dims: {hidden_dims}")
    logger.info(f"   LR actor/critic: {lr_actor}/{lr_critic}, gamma: {gamma}")
    logger.info("   Replay buffer capacity: 1,000,000")

    return agent, buffer


def main() -> None:
    """Main training loop for SAC on production environment."""
    # Parse arguments
    parser = argparse.ArgumentParser(description="Train SAC agent on production environment")
    parser.add_argument(
        "--data-path",
        type=str,
        default="data/1min_forex_data_train.parquet",
        help="Path to training parquet file",
    )
    parser.add_argument(
        "--num-episodes",
        type=int,
        default=1000,
        help="Number of training episodes (default: 1000 - E016 config)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=256,
        help="Batch size for agent updates",
    )
    parser.add_argument(
        "--warmup-steps",
        type=int,
        default=10_000,
        help="Random warmup steps before training (default: 10,000 - E016 config)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="eurusd",
        help="Currency pair symbol (default: eurusd). For single-pair mode.",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        nargs="+",
        default=None,
        help="Multiple currency pair symbols for multi-pair training (e.g., --symbols eurusd gbpusd usdjpy). Overrides --symbol.",
    )
    parser.add_argument(
        "--use-vae-features",
        action="store_true",
        help=(
            "If set, train SAC using VAE latent features from *_vae.parquet and "
            "enable use_vae_features in ProductionTradingConfig."
        ),
    )
    parser.add_argument(
        "--loss-penalty-factor",
        type=float,
        default=1.0,
        help="Multiplicador para penalizar recompensas negativas en el entorno (loss_penalty_factor).",
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
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed para numpy, random y torch (default: 42).",
    )
    parser.add_argument(
        "--hidden-dims",
        nargs="+",
        type=int,
        default=[256, 256],
        help="Hidden layer dimensions for actor/critic networks (default: [256, 256] - E016 baseline arch)",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=3e-4,
        help="Learning rate for actor and critic networks (default: 3e-4)",
    )
    parser.add_argument(
        "--gamma",
        type=float,
        default=0.99,
        help="Discount factor for future rewards (default: 0.99). Higher = more far-sighted.",
    )
    parser.add_argument(
        "--checkpoint",
        type=str,
        default=None,
        help="Path to pre-trained checkpoint for transfer learning (e.g., baselines/.../best_checkpoint.pt)",
    )
    parser.add_argument(
        "--action-penalty",
        type=float,
        default=0.0,
        help="Penalty per trade executed (default: 0.0 = no penalty). E047+: 0.0001.",
    )
    parser.add_argument(
        "--position-dead-zone",
        type=float,
        default=0.0,
        help="Min abs(target_pos_frac) to trigger a trade (default: 0.0 = disabled). E047+: 0.05.",
    )
    parser.add_argument(
        "--min-hold-period",
        type=int,
        default=0,
        help="Min bars to hold a position before allowing changes (default: 0 = disabled). E047+: 5.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Base output directory for models/results/logs (default: sac_baseline). Use unique name per experiment.",
    )
    parser.add_argument(
        "--max-leverage",
        type=float,
        default=None,
        help="Maximum leverage cap (e.g. 20.0 for 20x). None = uncapped (E034/E035 behavior).",
    )

    # Slippage (pips-first, preferred over legacy bps)
    parser.add_argument(
        "--slippage-pips-mean",
        type=float,
        default=0.0,
        help="Mean slippage per fill in pips (default: 0.0 = disabled). Typical retail: 0.1-0.3.",
    )
    parser.add_argument(
        "--slippage-pips-std",
        type=float,
        default=0.0,
        help="Std dev of slippage per fill in pips (default: 0.0). Suggested: 50%% of mean.",
    )
    parser.add_argument(
        "--allow-positive-slippage",
        action="store_true",
        help="Allow positive slippage (price improvement). Default: half-normal (always adverse).",
    )
    parser.add_argument(
        "--lambda-risk-penalty",
        type=float,
        default=None,
        help="Risk penalty coefficient (default: 0.05). For multi-pair, consider dividing by N (e.g. 0.025 for 2 pairs).",
    )
    parser.add_argument(
        "--lambda-clamp-penalty",
        type=float,
        default=None,
        help="Clamp penalty coefficient (default: 0.01). For multi-pair, consider dividing by N.",
    )

    # ── Sniper-gate / cooldown / turnover / flip ──
    parser.add_argument(
        "--lambda-turnover", type=float, default=0.0,
        help="Penalty per lot traded per step (soft anti-overtrading). 0=disabled.",
    )
    parser.add_argument(
        "--lambda-flip", type=float, default=0.0,
        help="One-off penalty per sign reversal (long↔short). 0=disabled.",
    )
    parser.add_argument(
        "--enter-threshold", type=float, default=0.0,
        help="Hysteresis: min |target_pos_frac| to open from flat. 0=disabled.",
    )
    parser.add_argument(
        "--exit-threshold", type=float, default=0.0,
        help="Hysteresis: max |target_pos_frac| to allow close. 0=disabled.",
    )
    parser.add_argument(
        "--cooldown-bars", type=int, default=0,
        help="Bars to block new entries after any close. 0=disabled.",
    )

    args = parser.parse_args()

    # Validate min_sl_pips
    if args.min_sl_pips < 0.0:
        parser.error("min-sl-pips debe ser >= 0.0")

    # Set global seed for reproducibility
    set_global_seed(args.seed)
    logger.info(f"Using global seed = {args.seed}")

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

    logger.info("=" * 60)
    logger.info("SAC Production Environment Training - Baseline")
    logger.info("=" * 60)

    # Log VAE mode
    if args.use_vae_features:
        logger.info("Training SAC with VAE latent features enabled.")
    else:
        logger.info("Training SAC with original raw feature set (no VAE).")

    # Device selection
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Using device: {device}")

    # Select data paths based on VAE flag
    if args.use_vae_features:
        # Use VAE-enhanced datasets
        train_path = args.data_path.replace(".parquet", "_vae.parquet")
        val_path = "data/1min_forex_data_val_vae.parquet"
        logger.info("Using VAE datasets: *_vae.parquet")
    else:
        # Use original datasets
        train_path = args.data_path
        val_path = "data/1min_forex_data_val.parquet"
        logger.info("Using raw feature datasets: *.parquet")

    # Load data
    df = load_training_data(train_path)

    # Resolve symbols: --symbols overrides --symbol
    symbols_list = args.symbols if args.symbols else [args.symbol]
    logger.info(f"Trading symbols: {symbols_list}")

    # Create environment
    env = create_environment(
        df,
        symbol=symbols_list if len(symbols_list) > 1 else symbols_list[0],
        use_vae_features=args.use_vae_features,
        loss_penalty_factor=args.loss_penalty_factor,
        action_penalty=args.action_penalty,
        position_dead_zone=args.position_dead_zone,
        min_hold_period=args.min_hold_period,
        lambda_risk_penalty=args.lambda_risk_penalty,
        lambda_clamp_penalty=args.lambda_clamp_penalty,
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
        slippage_pips_mean=args.slippage_pips_mean,
        slippage_pips_std=args.slippage_pips_std,
        allow_positive_slippage=args.allow_positive_slippage,
        lambda_turnover=args.lambda_turnover,
        lambda_flip=args.lambda_flip,
        enter_threshold=args.enter_threshold,
        exit_threshold=args.exit_threshold,
        cooldown_bars=args.cooldown_bars,
    )

    # Create agent and buffer
    agent, buffer = create_agent_and_buffer(
        state_dim=env.state_dim,
        action_dim=env.action_dim,
        hidden_dims=args.hidden_dims,
        device=device,
        lr_actor=args.lr,
        lr_critic=args.lr,
        gamma=args.gamma,
    )

    # Load checkpoint if provided (transfer learning)
    is_transfer_learning = False
    if args.checkpoint:
        checkpoint_path = Path(args.checkpoint)
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")

        logger.info("=" * 60)
        logger.info(f"Loading checkpoint for transfer learning: {checkpoint_path}")
        checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)

        # Load agent state dict
        if "agent_state_dict" in checkpoint:
            agent.load_state_dict(checkpoint["agent_state_dict"])
            logger.info(
                f"✅ Loaded pre-trained weights from episode {checkpoint.get('episode', 'unknown')}"
            )
            logger.info(f"   Best metric value: {checkpoint.get('best_metric_value', 'unknown')}")
        else:
            # Try direct load as fallback
            agent.load_state_dict(checkpoint)
            logger.warning("⚠️  Loaded checkpoint directly (no metadata found)")

        # CRITICAL FIX: Reset alpha for transfer learning
        # Pre-trained checkpoints have alpha ≈ 0 (exploration dead).
        # We need to reset it so the agent can explore and adapt to
        # the (potentially different) environment/reward structure.
        if hasattr(agent, "log_alpha") and agent.log_alpha is not None:
            old_alpha = agent.alpha.item()
            agent.log_alpha.data.fill_(0.0)  # alpha = exp(0) = 1.0
            agent.alpha_optimizer = torch.optim.Adam([agent.log_alpha], lr=3e-4)
            logger.info(
                f"🔄 Reset alpha: {old_alpha:.6f} → {agent.alpha.item():.6f} (fresh exploration)"
            )

        is_transfer_learning = True
        logger.info("🚀 Transfer learning: Starting from pre-trained weights")
        logger.info("   Warmup will use POLICY actions (not random)")
        logger.info("=" * 60)

    # Determine output directories
    output_base = args.output_dir or "sac_baseline"
    checkpoint_dir = f"models/{output_base}/"
    export_dir = f"results/{output_base}/"
    log_dir = f"logs/{output_base}/"
    progress_csv = f"results/{output_base}/training_progress.csv"

    # Create trainer (wandb disabled for baseline MVP)
    trainer = SACTrainer(
        agent=agent,
        env=env,
        replay_buffer=buffer,
        checkpoint_dir=checkpoint_dir,
        export_dir=export_dir,
        log_dir=log_dir,
        wandb_callback=None,  # Disable W&B for baseline
        save_every_n_episodes=25,  # Save checkpoint every 25 episodes
        export_every_n_episodes=100,  # Export episodes every 100 episodes
        eval_every_n_episodes=25,  # Evaluate every 25 episodes
        val_interval_episodes=25,  # Validate on fixed val set every 25 episodes
        val_data_path=val_path,  # Use VAE or raw validation data based on flag
        training_progress_csv=progress_csv,
    )

    logger.info("\n" + "=" * 60)
    logger.info(f"Starting Training (action_penalty={args.action_penalty})")
    logger.info("=" * 60)
    logger.info(f"Episodes: {args.num_episodes}")
    logger.info(f"Batch size: {args.batch_size}")
    logger.info(f"Warmup steps: {args.warmup_steps:,}")
    logger.info("=" * 60 + "\n")

    # Train
    episode_summaries = trainer.train(
        num_episodes=args.num_episodes,
        batch_size=args.batch_size,
        warmup_steps=args.warmup_steps,
        update_after=args.warmup_steps,  # Start updates after warmup
        update_every=1,  # Update every step (standard SAC)
        eval_episodes=5,  # 5 eval episodes for better statistics
        use_policy_warmup=is_transfer_learning,  # Use policy for warmup in transfer learning
    )

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("Training Complete")
    logger.info("=" * 60)

    if episode_summaries:
        # Compute average metrics
        avg_reward = np.mean([ep["episode_reward"] for ep in episode_summaries])
        avg_sharpe = np.mean([ep.get("sharpe_ratio", 0.0) for ep in episode_summaries])
        avg_return = np.mean([ep.get("total_return_pct", 0.0) for ep in episode_summaries])

        logger.info(f"Episodes completed: {len(episode_summaries)}")
        logger.info(f"Average reward: {avg_reward:.2f}")
        logger.info(f"Average Sharpe: {avg_sharpe:.3f}")
        logger.info(f"Average return: {avg_return:.2f}%")
        logger.info(f"Replay buffer size: {len(buffer):,}")
        logger.info(f"\nCheckpoints saved to: {checkpoint_dir}")
        logger.info(f"Results exported to: {export_dir}")
        logger.info(f"Logs saved to: {log_dir}")
    else:
        logger.warning("No episodes completed")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
