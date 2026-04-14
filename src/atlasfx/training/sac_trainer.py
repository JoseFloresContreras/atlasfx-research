"""
SAC Trainer with integrated metrics, logging, and episode export.

This trainer provides a high-level interface for training SAC agents with:
- Automatic W&B logging via WandbCallback
- Episode export with all 20 performance metrics
- Replay buffer management
- Checkpointing and model persistence
- Early stopping based on performance metrics
- Comprehensive logging and progress tracking

Author: AtlasFX Team
Date: November 4, 2025
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import torch
from tqdm import tqdm

from atlasfx.training.callbacks import WandbCallback


if TYPE_CHECKING:
    from atlasfx.environments.trading_env import ProductionTradingEnv
    from atlasfx.models.sac import SAC, ReplayBuffer


class SACTrainer:
    """
    High-level trainer for SAC agents with automatic logging and export.

    Features:
    - Integrated W&B logging via WandbCallback
    - Automatic episode export with 20 performance metrics
    - Replay buffer management with warmup
    - Model checkpointing based on best performance
    - Early stopping to prevent overtraining
    - Comprehensive progress tracking
    - Support for deterministic evaluation episodes

    Example:
        >>> from atlasfx.models import SAC, ReplayBuffer
        >>> from atlasfx.environments import ProductionTradingEnv
        >>> from atlasfx.training import SACTrainer, init_wandb_offline
        >>>
        >>> # Initialize components
        >>> env = ProductionTradingEnv(...)
        >>> agent = SAC(state_dim=env.state_dim, action_dim=env.action_dim)
        >>> buffer = ReplayBuffer(capacity=1_000_000, state_dim=env.state_dim, action_dim=env.action_dim)
        >>>
        >>> # Initialize W&B
        >>> init_wandb_offline(project="atlasfx-sac")
        >>>
        >>> # Create trainer
        >>> trainer = SACTrainer(
        ...     agent=agent,
        ...     env=env,
        ...     replay_buffer=buffer,
        ...     checkpoint_dir="models/sac",
        ...     export_dir="results/sac",
        ... )
        >>>
        >>> # Train
        >>> trainer.train(
        ...     num_episodes=1000,
        ...     batch_size=256,
        ...     warmup_steps=10000,
        ... )
    """

    def __init__(
        self,
        agent: SAC,
        env: ProductionTradingEnv,
        replay_buffer: ReplayBuffer,
        checkpoint_dir: str | Path = "models/sac/",
        export_dir: str | Path = "results/sac/",
        log_dir: str | Path = "logs/sac/",
        wandb_callback: WandbCallback | None = None,
        save_every_n_episodes: int = 10,
        export_every_n_episodes: int = 10,
        eval_every_n_episodes: int = 5,
        early_stopping_metric: str = "sharpe_ratio",
        early_stopping_patience: int = 50,
        early_stopping_min_delta: float = 0.01,
        val_interval_episodes: int = 50,
        val_data_path: str | Path | None = None,
        num_val_episodes: int = 20,
        training_progress_csv: str | Path | None = None,
    ) -> None:
        """
        Initialize SAC trainer.

        Args:
            agent: SAC agent to train
            env: Trading environment (uses training data)
            replay_buffer: Replay buffer for experience storage
            checkpoint_dir: Directory to save model checkpoints
            export_dir: Directory to export episode histories
            log_dir: Directory to save training logs
            wandb_callback: Optional WandbCallback for logging (creates default if None)
            save_every_n_episodes: Save checkpoint every N episodes
            export_every_n_episodes: Export episode history every N episodes
            eval_every_n_episodes: Run deterministic evaluation every N episodes
            early_stopping_metric: Metric to use for early stopping (e.g., "sharpe_ratio", "total_return_pct")
            early_stopping_patience: Number of episodes without improvement before stopping
            early_stopping_min_delta: Minimum improvement to reset patience counter
            val_interval_episodes: Evaluate on validation set every N episodes (0 to disable)
            val_data_path: Path to validation parquet file (e.g., data/1min_forex_data_val.parquet)
            num_val_episodes: Number of uniformly-spaced episodes to run across val set (default 20)
            training_progress_csv: Path to save training progress metrics (e.g., results/sac_training_progress.csv)
        """
        self.agent = agent
        self.env = env
        self.replay_buffer = replay_buffer

        # Setup directories
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Setup W&B callback
        self.callback = wandb_callback or WandbCallback(
            log_frequency=1,  # Log every step for RL
            log_trades=True,
            log_equity=True,
            log_distributions=True,
        )

        # Training configuration
        self.save_every_n_episodes = save_every_n_episodes
        self.export_every_n_episodes = export_every_n_episodes
        self.eval_every_n_episodes = eval_every_n_episodes

        # Early stopping
        self.early_stopping_metric = early_stopping_metric
        self.early_stopping_patience = early_stopping_patience
        self.early_stopping_min_delta = early_stopping_min_delta
        self.best_metric_value = float("-inf")
        self.patience_counter = 0

        # Best validation checkpoint tracking
        # Score formula: CAGR / (MaxDD + epsilon)^alpha
        # Higher is better; negative returns produce negative score
        self.best_val_score: float = float("-inf")
        self.best_checkpoint_path: Path | None = None
        self._val_score_alpha: float = 1.0
        self._val_score_epsilon: float = 1.0  # Avoid div-by-zero when MaxDD ≈ 0

        # Setup logging (must be before _init_best_checkpoints_csv which uses self.logger)
        self.logger = logging.getLogger(__name__)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.log_dir / "training.log")
            handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

        # Training state
        self.current_episode = 0
        self.total_steps = 0
        self.total_updates = 0

        # Episode history
        self.episode_rewards: list[float] = []
        self.episode_metrics: list[dict[str, float]] = []

        # Validation configuration
        self.val_interval_episodes = val_interval_episodes
        self.val_data_path = Path(val_data_path) if val_data_path else None
        self.num_val_episodes = num_val_episodes
        self.training_progress_csv = Path(training_progress_csv) if training_progress_csv else None
        self.val_env = None  # Lazy initialization on first validation

        # Initialize training progress CSV if provided
        if self.training_progress_csv:
            self.training_progress_csv.parent.mkdir(parents=True, exist_ok=True)
            # Create CSV header if file doesn't exist
            if not self.training_progress_csv.exists():
                with open(self.training_progress_csv, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            "train_episode",
                            "val_return_pct",
                            "val_sharpe",
                            "val_n_trades",
                            "val_win_rate",
                            "val_max_drawdown",
                            "val_score",
                            "timestamp",
                        ]
                    )
                self.logger.info(f"Created training progress CSV: {self.training_progress_csv}")

        # Initialize training metrics CSV (per-episode logging)
        self.training_metrics_csv = self.checkpoint_dir / "training_metrics.csv"
        self._init_training_metrics_writer()

        # Best checkpoints metrics CSV (tracks each time a new best is found)
        # Stored in export_dir (results/) alongside experiment outputs
        self.best_checkpoints_csv = self.export_dir / "best_checkpoints_metrics.csv"
        self._init_best_checkpoints_csv()

        self.logger.info("SACTrainer initialized")
        self.logger.info(f"Agent: {agent.__class__.__name__}")
        self.logger.info(f"Device: {agent.device}")
        self.logger.info(f"Environment: {env.__class__.__name__}")
        self.logger.info(f"Replay buffer capacity: {replay_buffer.capacity:,}")
        self.logger.info(f"Checkpoint dir: {checkpoint_dir}")
        self.logger.info(f"Export dir: {export_dir}")
        self.logger.info(f"Early stopping metric: {early_stopping_metric}")
        self.logger.info(f"Early stopping patience: {early_stopping_patience}")
        if self.val_interval_episodes > 0 and self.val_data_path:
            self.logger.info(f"Validation interval: every {val_interval_episodes} episodes")
            self.logger.info(f"Validation episodes per eval: {num_val_episodes}")
            self.logger.info(f"Validation data: {self.val_data_path}")
            self.logger.info(f"Training progress CSV: {self.training_progress_csv}")

    def train_episode(
        self,
        batch_size: int = 256,
        update_after: int = 1000,
        update_every: int = 1,
        deterministic: bool = False,
        log_every_n_steps: int = 100,  # NEW: Log progress every N steps
    ) -> dict[str, Any]:
        """
        Train for one episode.

        Args:
            batch_size: Batch size for agent updates
            update_after: Start updating agent after this many steps
            update_every: Update agent every N steps
            deterministic: If True, use deterministic policy (for evaluation)
            log_every_n_steps: Print progress every N steps (0 = no logging)

        Returns:
            Dictionary with episode metrics including:
            - episode_reward: Total reward
            - episode_length: Number of steps
            - num_updates: Number of agent updates
            - all 20 performance metrics from TradingMetricsTracker
            - average agent losses (critic, actor, alpha)
        """
        # Reset environment
        obs, info = self.env.reset()
        episode_reward = 0.0
        episode_length = 0
        num_updates = 0

        # Agent metrics accumulation
        critic_losses = []
        actor_losses = []
        alpha_values = []

        # Episode loop
        while True:
            # Select action
            action = self.agent.select_action(obs, deterministic=deterministic)

            # Environment step
            next_obs, reward, terminated, truncated, step_info = self.env.step(action)
            done = terminated or truncated

            # Store transition (only if training, not evaluation)
            if not deterministic:
                self.replay_buffer.add(obs, action, reward, next_obs, done)

            # Update agent (only if training and enough experience)
            if (
                not deterministic
                and self.total_steps >= update_after
                and self.total_steps % update_every == 0
                and len(self.replay_buffer) >= batch_size
            ):
                # Sample batch and update (ensure device matches agent)
                batch = self.replay_buffer.sample(batch_size, device=self.agent.device)
                agent_metrics = self.agent.update(*batch)

                # Accumulate metrics
                critic_losses.append(agent_metrics["critic_loss"])
                actor_losses.append(agent_metrics["actor_loss"])
                alpha_values.append(agent_metrics["alpha"])

                num_updates += 1
                self.total_updates += 1

            # NEW: Log progress during episode
            if (
                log_every_n_steps > 0
                and episode_length > 0
                and episode_length % log_every_n_steps == 0
            ):
                mode = "Eval" if deterministic else "Train"
                avg_reward = episode_reward / episode_length
                print(
                    f"   [{mode}] Step {episode_length}: avg_reward={avg_reward:.4f}, total_reward={episode_reward:.2f}, updates={num_updates}"
                )

            # Log step to W&B
            if self.callback.enabled:
                self.callback.on_step_end(step=self.total_steps, info=step_info)

            # Update state
            obs = next_obs
            episode_reward += reward
            episode_length += 1
            self.total_steps += 1

            # Check if episode ended
            if done:
                break

        # Compute final metrics from environment
        # Check if environment has metrics_tracker (ProductionTradingEnv) or get_episode_metrics (custom env)
        if hasattr(self.env, "metrics_tracker"):
            final_metrics = self.env.metrics_tracker.compute_all_metrics()
        elif hasattr(self.env, "get_episode_metrics"):
            final_metrics = self.env.get_episode_metrics()
        else:
            # Fallback: minimal metrics
            final_metrics = {
                "sharpe_ratio": 0.0,
                "total_return_pct": 0.0,
                "max_drawdown_pct": 0.0,
            }

        # Build episode summary
        episode_summary = {
            "episode": self.current_episode,
            "episode_reward": episode_reward,
            "episode_length": episode_length,
            "num_updates": num_updates,
            "total_steps": self.total_steps,
            "total_updates": self.total_updates,
            "buffer_size": len(self.replay_buffer),
            "deterministic": deterministic,
            **final_metrics,  # Add all 20 performance metrics
        }

        # Add agent metrics (if any updates happened)
        if num_updates > 0:
            episode_summary.update(
                {
                    "avg_critic_loss": np.mean(critic_losses),
                    "avg_actor_loss": np.mean(actor_losses),
                    "avg_alpha": np.mean(alpha_values),
                }
            )

        # Log episode to W&B
        if self.callback.enabled:
            self.callback.on_episode_end(episode=self.current_episode, metrics=episode_summary)

        # Log to file
        mode = "Eval" if deterministic else "Train"
        self.logger.info(
            f"Episode {self.current_episode} [{mode}] - "
            f"Reward: {episode_reward:.2f}, "
            f"Length: {episode_length}, "
            f"Sharpe: {final_metrics['sharpe_ratio']:.3f}, "
            f"Return: {final_metrics['total_return_pct']:.2f}%, "
            f"Updates: {num_updates}"
        )

        return episode_summary

    def train(
        self,
        num_episodes: int,
        batch_size: int = 256,
        warmup_steps: int = 10000,
        update_after: int = 1000,
        update_every: int = 1,
        eval_episodes: int = 1,
        use_policy_warmup: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Train SAC agent for multiple episodes.

        Training loop includes:
        1. Warmup phase (random or policy-based experience collection)
        2. Training phase (collect experience + update agent)
        3. Periodic deterministic evaluation
        4. Checkpointing based on best performance
        5. Episode export for reproducibility
        6. Early stopping if no improvement

        Args:
            num_episodes: Total number of training episodes
            batch_size: Batch size for agent updates
            warmup_steps: Steps before training starts (random or policy-based)
            update_after: Start agent updates after this many total steps
            update_every: Update agent every N steps
            eval_episodes: Number of deterministic evaluation episodes to run
            use_policy_warmup: If True, use agent's policy for warmup instead of random
                             (critical for transfer learning to avoid destroying weights)

        Returns:
            List of episode summaries (one dict per episode)
        """
        self.logger.info(f"Starting training for {num_episodes} episodes")
        self.logger.info(f"Warmup steps: {warmup_steps:,}")
        self.logger.info(
            f"Warmup mode: {'policy (transfer learning)' if use_policy_warmup else 'random (from scratch)'}"
        )
        self.logger.info(f"Batch size: {batch_size}")
        self.logger.info(f"Update after: {update_after:,} steps")
        self.logger.info(f"Update every: {update_every} steps")

        all_episode_summaries = []

        # Warmup phase
        if warmup_steps > 0 and self.total_steps < warmup_steps:
            if use_policy_warmup:
                self.logger.info("Starting warmup phase with PRE-TRAINED POLICY actions...")
            else:
                self.logger.info("Starting warmup phase with random actions...")
            self._warmup(warmup_steps, use_policy=use_policy_warmup)
            self.logger.info(f"Warmup complete. Buffer size: {len(self.replay_buffer):,}")

        # Training loop
        pbar = tqdm(range(num_episodes), desc="Training SAC")
        for episode in pbar:
            self.current_episode = episode

            # Train episode
            episode_summary = self.train_episode(
                batch_size=batch_size,
                update_after=update_after,
                update_every=update_every,
                deterministic=False,
            )

            # Store metrics
            self.episode_rewards.append(episode_summary["episode_reward"])
            self.episode_metrics.append(episode_summary)
            all_episode_summaries.append(episode_summary)

            # Log training metrics to CSV
            self._log_training_metrics(
                episode_idx=episode + 1,  # 1-indexed for readability
                total_reward=episode_summary["episode_reward"],
                info=episode_summary,
            )

            # Update progress bar
            pbar.set_postfix(
                {
                    "reward": f"{episode_summary['episode_reward']:.2f}",
                    "sharpe": f"{episode_summary['sharpe_ratio']:.3f}",
                    "buffer": f"{len(self.replay_buffer):,}",
                }
            )

            # Periodic evaluation
            if (episode + 1) % self.eval_every_n_episodes == 0:
                self.logger.info(f"Running {eval_episodes} evaluation episode(s)...")
                eval_metrics = self._evaluate(eval_episodes)
                all_episode_summaries.extend(eval_metrics)

                # Check for early stopping (based on eval performance)
                if self._check_early_stopping(eval_metrics[-1]):
                    self.logger.info("Early stopping triggered. Training complete.")
                    break

            # Periodic checkpointing
            if (episode + 1) % self.save_every_n_episodes == 0:
                self._save_checkpoint(episode_summary)

            # Periodic episode export
            if (episode + 1) % self.export_every_n_episodes == 0:
                self._export_episode(episode)

            # Periodic validation on fixed validation set
            if (
                self.val_interval_episodes > 0
                and self.val_data_path
                and (episode + 1) % self.val_interval_episodes == 0
            ):
                self.logger.info("Running validation on fixed validation set...")
                val_metrics = self._evaluate_on_validation()
                self._log_validation_to_csv(episode, val_metrics)

                # Save best checkpoint if composite validation score improved
                # Score = CAGR / (MaxDD + epsilon)^alpha
                val_score = self._compute_val_score(val_metrics)
                if val_score > self.best_val_score:
                    prev_best = self.best_val_score
                    self.best_val_score = val_score
                    best_path = self.checkpoint_dir / "best_checkpoint.pt"
                    self._save_checkpoint(episode_summary, best_path=best_path)
                    self.best_checkpoint_path = best_path
                    self._log_best_checkpoint_metrics(episode, val_metrics, prev_best)
                    self.logger.info(
                        f"New best checkpoint saved at {best_path} "
                        f"(score={val_score:.4f}, "
                        f"return={val_metrics['val_return_pct']:.2f}%, "
                        f"maxdd={val_metrics['val_max_drawdown']:.2f}%)"
                    )

        # Final checkpoint and export
        self.logger.info("Training loop completed. Saving final checkpoint...")
        self._save_checkpoint(episode_summary, is_final=True)
        self._export_episode(self.current_episode, is_final=True)

        # Finish W&B logging
        if self.callback.enabled:
            self.callback.finish()

        self.logger.info("Training complete!")
        self.logger.info(f"Total episodes: {len(all_episode_summaries)}")
        self.logger.info(f"Total steps: {self.total_steps:,}")
        self.logger.info(f"Total updates: {self.total_updates:,}")

        return all_episode_summaries

    def _init_training_metrics_writer(self) -> None:
        """
        Initialize training metrics CSV file.
        Creates the file with headers if it doesn't exist.
        """
        try:
            if not self.training_metrics_csv.exists():
                with open(self.training_metrics_csv, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            "episode",
                            "total_reward",
                            "episode_return_pct",
                            "episode_max_drawdown_pct",
                            "episode_num_trades",
                            "episode_win_rate",
                        ]
                    )
                self.logger.info(f"Created training metrics CSV: {self.training_metrics_csv}")
        except Exception as e:
            self.logger.warning(f"Failed to initialize training metrics CSV: {e}")

    def _compute_val_score(self, val_metrics: dict[str, float]) -> float:
        """
        Compute composite validation score for checkpoint selection.

        Formula: score = CAGR / (MaxDD + epsilon)^alpha

        Where:
        - CAGR = val_return_pct (mean return % across validation episodes)
        - MaxDD = val_max_drawdown (mean max drawdown % across validation episodes)
        - epsilon = 1.0 (prevents division by zero when MaxDD is near 0)
        - alpha = 1.0 (controls penalty severity for drawdown)

        Properties:
        - Positive returns + low drawdown → high positive score
        - Negative returns → negative score (losing model never beats winner)
        - Zero drawdown → score = CAGR / epsilon^alpha = CAGR
        - Higher drawdown → lower score (penalized denominator)

        Args:
            val_metrics: Validation metrics dictionary with val_return_pct and val_max_drawdown

        Returns:
            Composite score (higher is better)
        """
        cagr = val_metrics.get("val_return_pct", 0.0)
        max_dd = abs(val_metrics.get("val_max_drawdown", 0.0))  # Ensure positive

        denominator = (max_dd + self._val_score_epsilon) ** self._val_score_alpha
        score = cagr / denominator if denominator > 0 else 0.0

        return score

    def _init_best_checkpoints_csv(self) -> None:
        """
        Initialize best_checkpoints_metrics.csv file (transposed format).
        Rows = metric names, Columns = checkpoints.
        Tracks every time a new best checkpoint is saved with full metrics
        (20 standard + 56 extended when available).
        """
        self._best_checkpoints_data: list[dict[str, object]] = []

        # If file already exists from a resumed run, load existing data
        if self.best_checkpoints_csv.exists():
            import csv as csv_mod

            try:
                with open(self.best_checkpoints_csv, newline="") as f:
                    reader = csv_mod.reader(f)
                    rows = list(reader)
                if rows and len(rows[0]) > 1:
                    # Transposed format: first col = metric name, rest = checkpoints
                    metric_names = [row[0] for row in rows]
                    n_checkpoints = len(rows[0]) - 1
                    for cp_idx in range(n_checkpoints):
                        entry: dict[str, object] = {}
                        for row in rows:
                            key = row[0]
                            val = row[cp_idx + 1]
                            # Try to parse as number
                            try:
                                entry[key] = int(val)
                            except ValueError:
                                try:
                                    entry[key] = float(val)
                                except ValueError:
                                    entry[key] = val
                        self._best_checkpoints_data.append(entry)
                    self.logger.info(f"Loaded {n_checkpoints} existing checkpoints from CSV")
            except Exception as e:
                self.logger.warning(f"Could not load existing CSV: {e}")

        self.logger.info(f"Best checkpoints CSV target: {self.best_checkpoints_csv}")

    def _compute_extended_metrics_from_val_env(self) -> dict[str, float]:
        """
        Compute extended metrics from the validation environment's last episode data.

        Returns:
            Dictionary with extended metric names and values.
            Empty dict if val_env is unavailable or computation fails.
        """
        if self.val_env is None or not hasattr(self.val_env, "metrics_tracker"):
            return {}

        tracker = self.val_env.metrics_tracker
        if not tracker.equity_curve or len(tracker.equity_curve) < 2:
            return {}

        try:
            from atlasfx.evaluation.extended_metrics import calculate_extended_metrics

            equity_curve = tracker.get_equity_curve_array()
            returns = tracker.get_returns()
            trades = tracker.trades
            standard_metrics = tracker.compute_all_metrics()

            ext = calculate_extended_metrics(
                equity_curve=equity_curve,
                returns=returns,
                trades=trades,
                annualized_return=standard_metrics.get("annualized_return_pct", 0.0),
                initial_balance=tracker.initial_balance,
                observed_sharpe=standard_metrics.get("sharpe_ratio", 0.0),
                n_trials=1,
                skewness=standard_metrics.get("return_skewness", 0.0),
                kurtosis=standard_metrics.get("return_kurtosis", 3.0),
            )

            # Convert dataclass to dict with "ext_" prefix to avoid collisions
            ext_dict = {}
            for name, value in ext.__dict__.items():
                ext_dict[f"ext_{name}"] = value

            self.logger.info(f"Computed {len(ext_dict)} extended metrics for best checkpoint")
            return ext_dict

        except Exception as e:
            self.logger.warning(f"Failed to compute extended metrics: {e}")
            return {}

    def _log_best_checkpoint_metrics(
        self, episode: int, val_metrics: dict[str, float], prev_best_score: float
    ) -> None:
        """
        Log comprehensive metrics when a new best checkpoint is saved.
        Writes CSV in TRANSPOSED format: rows = metric names, columns = checkpoints.

        Collects:
        - Metadata: episode, prev_best_score, improvement, timestamp
        - Aggregated validation metrics (return, sharpe, maxdd, score, etc.)
        - 20 standard metrics from validation environment's last episode
        - 56 extended metrics (VaR, CVaR, ulcer index, etc.) when available

        Args:
            episode: Current training episode number
            val_metrics: Validation metrics dictionary (aggregated across N episodes)
            prev_best_score: Previous best composite score (before this improvement)
        """
        try:
            from datetime import datetime

            current_score = val_metrics.get("val_score", self._compute_val_score(val_metrics))
            improvement = (
                current_score - prev_best_score
                if prev_best_score != float("-inf")
                else current_score
            )

            # Start with metadata
            row: dict[str, object] = {
                "train_episode": episode,
                "prev_best_score": (
                    round(prev_best_score, 4) if prev_best_score != float("-inf") else "N/A"
                ),
                "improvement": round(improvement, 4),
                "timestamp": datetime.now().isoformat(),
            }

            # Add aggregated validation metrics (averaged across N episodes)
            for k, v in val_metrics.items():
                if isinstance(v, float):
                    row[k] = round(v, 2)
                else:
                    row[k] = v

            # Add full 20 standard metrics from last validation episode
            if (
                self.val_env is not None
                and hasattr(self.val_env, "metrics_tracker")
                and self.val_env.metrics_tracker.equity_curve
            ):
                standard = self.val_env.metrics_tracker.compute_all_metrics()
                for k, v in standard.items():
                    row[f"std_{k}"] = round(v, 2) if isinstance(v, float) else v

            # Add 56 extended metrics
            ext_metrics = self._compute_extended_metrics_from_val_env()
            row.update(ext_metrics)

            # Round all float values to 2 decimals
            for k, v in row.items():
                if isinstance(v, float):
                    row[k] = round(v, 2)

            # Append to in-memory list and rewrite entire CSV in transposed format
            self._best_checkpoints_data.append(row)
            self._write_transposed_csv()

            n_metrics = len(row) - 4  # Exclude metadata columns
            self.logger.info(
                f"Best checkpoint metrics logged (episode {episode}, "
                f"score={current_score:.4f}, improvement={improvement:.4f}, "
                f"metrics_count={n_metrics})"
            )
        except Exception as e:
            self.logger.warning(f"Failed to log best checkpoint metrics: {e}")

    def _write_transposed_csv(self) -> None:
        """
        Write best_checkpoints_metrics.csv in transposed format.

        Format:
            metric,ep_024,ep_049,...
            train_episode,24,49,...
            val_return_pct,10.5,15.3,...
        """
        import csv as csv_mod

        if not self._best_checkpoints_data:
            return

        # Collect all metric names (union of all checkpoints, preserve order)
        all_keys: list[str] = []
        seen: set[str] = set()
        for entry in self._best_checkpoints_data:
            for k in entry:
                if k not in seen:
                    all_keys.append(k)
                    seen.add(k)

        # Build column headers from episode numbers
        col_headers = []
        for entry in self._best_checkpoints_data:
            ep = entry.get("train_episode", "?")
            col_headers.append(f"ep_{int(ep):05d}" if isinstance(ep, (int, float)) else str(ep))

        with open(self.best_checkpoints_csv, "w", newline="") as f:
            writer = csv_mod.writer(f)
            # Header row
            writer.writerow(["metric"] + col_headers)
            # One row per metric
            for key in all_keys:
                values = [entry.get(key, "") for entry in self._best_checkpoints_data]
                writer.writerow([key] + values)

    def _log_training_metrics(
        self, episode_idx: int, total_reward: float, info: dict[str, Any]
    ) -> None:
        """
        Log training metrics for one episode to CSV.

        This method extracts performance metrics from the episode_summary dict and logs them
        to training_metrics.csv. It uses robust field extraction with multiple fallbacks to
        handle both ProductionTradingEnv and MultiPairPortfolioEnv.

        Field Mapping:
        - episode_return_pct: Tries "episode_return_pct" then "total_return_pct"
        - episode_max_drawdown_pct: Tries "episode_max_drawdown_pct" then "max_drawdown_pct"
        - episode_num_trades: Tries "episode_num_trades" then "total_trades"
        - episode_win_rate: Tries "episode_win_rate" then "win_rate_pct"

        Args:
            episode_idx: Episode number (1-indexed for readability)
            total_reward: Total episode reward (from episode_summary["episode_reward"])
            info: Episode summary dictionary (contains final_metrics from environment)
        """
        try:
            # Extract metrics with fallback to empty string if not present
            # This handles both single-symbol and multi-pair environments robustly
            return_pct = info.get("episode_return_pct", info.get("total_return_pct", ""))
            max_dd_pct = info.get("episode_max_drawdown_pct", info.get("max_drawdown_pct", ""))
            num_trades = info.get("episode_num_trades", info.get("total_trades", ""))
            win_rate = info.get("episode_win_rate", info.get("win_rate_pct", ""))

            # Append row to CSV
            with open(self.training_metrics_csv, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        episode_idx,
                        total_reward,
                        return_pct,
                        max_dd_pct,
                        num_trades,
                        win_rate,
                    ]
                )
        except Exception as e:
            self.logger.warning(f"Failed to log training metrics for episode {episode_idx}: {e}")

    def _warmup(self, warmup_steps: int, use_policy: bool = False) -> None:
        """
        Warmup phase to populate replay buffer.

        When use_policy=False (default, training from scratch):
            Uses random actions to explore the action space uniformly.
        When use_policy=True (transfer learning):
            Uses the pre-trained policy (stochastic) to collect on-policy
            transitions. This preserves the quality of the loaded weights
            by ensuring the replay buffer contains relevant experiences.

        Args:
            warmup_steps: Number of steps to take
            use_policy: If True, use the agent's policy instead of random actions
        """
        desc = "Warmup (policy)" if use_policy else "Warmup (random)"
        obs, _ = self.env.reset()
        steps_taken = 0

        pbar = tqdm(total=warmup_steps, desc=desc)
        while steps_taken < warmup_steps:
            if use_policy:
                # Use the pre-trained policy (stochastic for exploration)
                action = self.agent.select_action(obs, deterministic=False)
            else:
                # Random action (uniform in [-1, 1] for each action dimension)
                action = np.random.uniform(-1, 1, size=self.env.action_dim)

            # Environment step
            next_obs, reward, terminated, truncated, _ = self.env.step(action)
            done = terminated or truncated

            # Store transition
            self.replay_buffer.add(obs, action, reward, next_obs, done)

            obs = next_obs
            steps_taken += 1
            self.total_steps += 1
            pbar.update(1)

            # Reset if episode ended
            if done:
                obs, _ = self.env.reset()

        pbar.close()

    def _evaluate(self, num_episodes: int = 1) -> list[dict[str, Any]]:
        """
        Run deterministic evaluation episodes.

        Args:
            num_episodes: Number of evaluation episodes

        Returns:
            List of evaluation episode summaries
        """
        eval_summaries = []

        for i in range(num_episodes):
            episode_summary = self.train_episode(
                batch_size=0,  # No updates during evaluation
                update_after=float("inf"),  # No updates
                deterministic=True,
            )
            eval_summaries.append(episode_summary)

            self.logger.info(
                f"Eval Episode {i + 1}/{num_episodes} - "
                f"Reward: {episode_summary['episode_reward']:.2f}, "
                f"Sharpe: {episode_summary['sharpe_ratio']:.3f}, "
                f"Return: {episode_summary['total_return_pct']:.2f}%"
            )

        return eval_summaries

    def _check_early_stopping(self, episode_metrics: dict[str, Any]) -> bool:
        """
        Check if early stopping criteria is met.

        Args:
            episode_metrics: Metrics from evaluation episode

        Returns:
            True if training should stop, False otherwise
        """
        current_metric = episode_metrics.get(self.early_stopping_metric, float("-inf"))

        # Check for improvement
        if current_metric > self.best_metric_value + self.early_stopping_min_delta:
            improvement = current_metric - self.best_metric_value
            self.best_metric_value = current_metric
            self.patience_counter = 0
            self.logger.info(
                f"New best {self.early_stopping_metric}: {current_metric:.4f} "
                f"(improved by {improvement:.4f})"
            )
            return False
        self.patience_counter += 1
        self.logger.info(
            f"No improvement in {self.early_stopping_metric}. "
            f"Patience: {self.patience_counter}/{self.early_stopping_patience}"
        )
        return self.patience_counter >= self.early_stopping_patience

    def _save_checkpoint(
        self, episode_metrics: dict[str, Any], is_final: bool = False, best_path: Path | None = None
    ) -> None:
        """
        Save model checkpoint.

        Args:
            episode_metrics: Metrics from current episode
            is_final: Whether this is the final checkpoint
            best_path: Optional path for best checkpoint (overrides default naming)
        """
        if best_path is not None:
            checkpoint_path = best_path
        else:
            checkpoint_name = (
                "final_checkpoint.pt" if is_final else f"checkpoint_ep{self.current_episode:05d}.pt"
            )
            checkpoint_path = self.checkpoint_dir / checkpoint_name

        # Save agent state
        checkpoint = {
            "episode": self.current_episode,
            "total_steps": self.total_steps,
            "total_updates": self.total_updates,
            "agent_state_dict": self.agent.state_dict(),
            "best_metric_value": self.best_metric_value,
            "episode_metrics": episode_metrics,
        }

        torch.save(checkpoint, checkpoint_path)
        self.logger.info(f"Checkpoint saved: {checkpoint_path}")

        # Also save best model if this is the best so far
        if episode_metrics.get(self.early_stopping_metric, float("-inf")) >= self.best_metric_value:
            best_path = self.checkpoint_dir / "best_model.pt"
            torch.save(checkpoint, best_path)
            self.logger.info(f"Best model updated: {best_path}")

    def _export_episode(self, episode: int, is_final: bool = False) -> None:
        """
        Export episode history.

        Args:
            episode: Episode number
            is_final: Whether this is the final export
        """
        episode_id = "final" if is_final else f"ep{episode:05d}"
        episode_dir = self.export_dir / f"episode_{episode_id}"

        exported = self.env.export_history(
            output_dir=episode_dir,
            episode_id=episode_id,
            format="parquet",
            compress=True,
        )

        self.logger.info(f"Episode {episode_id} exported to: {episode_dir}")
        for file_type, path in exported.items():
            # Skip nested structures (e.g., by_symbol dict in multipair envs)
            if isinstance(path, dict):
                continue
            if path:
                self.logger.debug(f"  - {file_type}: {path.name}")

    def _evaluate_on_validation(self) -> dict[str, float]:
        """
        Evaluate current policy on fixed validation set using multiple episodes.

        Runs N uniformly-spaced, non-overlapping episodes across the full validation
        set to prevent overfitting to a small window. Returns aggregated metrics
        (mean across episodes).

        Episode spacing: If val set has M total episodes and we run N eval episodes,
        we pick N evenly-spaced start points across [0, M). This ensures coverage
        of the entire validation period (e.g., all 7 months), not just the first window.

        Note: Validation is skipped for MultiPairPortfolioEnv (not compatible with
        single-pair validation data).

        Returns:
            Dictionary with validation metrics:
            - val_return_pct: Mean return percentage across episodes
            - val_sharpe: Mean Sharpe ratio across episodes
            - val_n_trades: Total trades across all episodes
            - val_win_rate: Mean win rate percentage
            - val_max_drawdown: Mean max drawdown percentage
            - val_n_episodes: Number of episodes evaluated
        """
        # Check if environment is MultiPairPortfolioEnv (skip validation)
        from atlasfx.environments.trading_env_multipair import MultiPairPortfolioEnv

        if isinstance(self.env, MultiPairPortfolioEnv):
            self.logger.debug(
                "Skipping validation (MultiPairPortfolioEnv not compatible with single-pair val data)"
            )
            return {
                "val_return_pct": 0.0,
                "val_sharpe": 0.0,
                "val_n_trades": 0,
                "val_win_rate": 0.0,
                "val_max_drawdown": 0.0,
                "val_n_episodes": 0,
            }

        # Lazy initialization of validation environment
        if self.val_env is None:
            self.logger.info(f"Initializing validation environment from {self.val_data_path}")
            import pandas as pd

            from atlasfx.environments.trading_env import (
                ProductionTradingEnv,
            )

            # Load validation data
            val_df = pd.read_parquet(self.val_data_path)
            self.logger.info(f"  Loaded {len(val_df):,} validation bars")

            # Create validation environment with same config as training env
            # Use same symbols and config as training (supports multi-pair)
            val_symbols = list(self.env.symbols)
            price_cols = self.env.price_cols

            self.val_env = ProductionTradingEnv(
                data=val_df,
                symbols=val_symbols,
                price_cols=price_cols,
                config=self.env.config,  # Use same config as training
            )
            self.logger.info(
                f"  Validation env created: {len(val_df)} bars, {self.val_env.state_dim} state dims"
            )

        # Calculate episode layout across val set
        episode_length = self.val_env.config.episode_length
        total_bars = self.val_env.num_steps
        max_possible_episodes = total_bars // episode_length
        num_val_episodes = min(self.num_val_episodes, max_possible_episodes)

        if num_val_episodes <= 0:
            self.logger.warning(
                f"Val set too short for even 1 episode ({total_bars} bars < {episode_length})"
            )
            return {
                "val_return_pct": 0.0,
                "val_sharpe": 0.0,
                "val_n_trades": 0,
                "val_win_rate": 0.0,
                "val_max_drawdown": 0.0,
                "val_n_episodes": 0,
            }

        # Pick N uniformly-spaced start points across the val set
        # This ensures coverage of the entire validation period
        if num_val_episodes == max_possible_episodes:
            # Use all episodes sequentially
            start_steps = [i * episode_length for i in range(num_val_episodes)]
        else:
            # Uniformly space N episodes across the available range
            spacing = max_possible_episodes / num_val_episodes
            start_steps = [int(i * spacing) * episode_length for i in range(num_val_episodes)]

        # Run multi-episode evaluation
        all_returns = []
        all_sharpes = []
        all_trades = []
        all_win_rates = []
        all_max_dds = []

        with torch.no_grad():
            for start_step in start_steps:
                # Reset to specific position in val set
                val_obs, _ = self.val_env.reset(options={"start_step": start_step})
                val_steps = 0

                while True:
                    val_action = self.agent.select_action(val_obs, deterministic=True)
                    val_obs, val_reward, terminated, truncated, _ = self.val_env.step(val_action)
                    val_steps += 1
                    if terminated or truncated:
                        break

                # Extract episode metrics
                if hasattr(self.val_env, "metrics_tracker"):
                    ep_metrics = self.val_env.metrics_tracker.compute_all_metrics()
                else:
                    ep_metrics = {}

                all_returns.append(ep_metrics.get("total_return_pct", 0.0))
                all_sharpes.append(ep_metrics.get("sharpe_ratio", 0.0))
                all_trades.append(ep_metrics.get("total_trades", 0))
                all_win_rates.append(ep_metrics.get("win_rate_pct", 0.0))
                all_max_dds.append(ep_metrics.get("max_drawdown_pct", 0.0))

        # Aggregate across episodes (filter NaN/inf Sharpes)
        sharpes_arr = np.array(all_sharpes)
        valid_sharpes = sharpes_arr[np.isfinite(sharpes_arr)]

        val_metrics = {
            "val_return_pct": float(np.mean(all_returns)),
            "val_sharpe": float(np.mean(valid_sharpes)) if len(valid_sharpes) > 0 else 0.0,
            "val_n_trades": int(np.sum(all_trades)),
            "val_win_rate": float(np.mean(all_win_rates)),
            "val_max_drawdown": float(np.mean(all_max_dds)),
            "val_n_episodes": num_val_episodes,
        }

        # Compute composite score for logging
        val_score = self._compute_val_score(val_metrics)
        val_metrics["val_score"] = val_score

        self.logger.info(
            f"Validation - Episode {self.current_episode}: "
            f"Return={val_metrics['val_return_pct']:.2f}%, "
            f"Sharpe={val_metrics['val_sharpe']:.3f}, "
            f"MaxDD={val_metrics['val_max_drawdown']:.2f}%, "
            f"Score={val_score:.4f}, "
            f"Trades={val_metrics['val_n_trades']}, "
            f"WinRate={val_metrics['val_win_rate']:.1f}%, "
            f"Episodes={num_val_episodes}/{max_possible_episodes}"
        )

        return val_metrics

    def _log_validation_to_csv(self, episode: int, val_metrics: dict[str, float]) -> None:
        """
        Append validation metrics to training progress CSV.

        Args:
            episode: Current training episode number
            val_metrics: Validation metrics dictionary
        """
        if not self.training_progress_csv:
            return

        import csv
        from datetime import datetime

        # Append metrics to CSV
        with open(self.training_progress_csv, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    episode,
                    f"{val_metrics['val_return_pct']:.4f}",
                    f"{val_metrics['val_sharpe']:.6f}",
                    int(val_metrics["val_n_trades"]),
                    f"{val_metrics['val_win_rate']:.4f}",
                    f"{val_metrics['val_max_drawdown']:.4f}",
                    f"{val_metrics.get('val_score', 0.0):.4f}",
                    datetime.now().isoformat(),
                ]
            )

        self.logger.debug(f"Validation metrics logged to {self.training_progress_csv}")

    def load_checkpoint(self, checkpoint_path: str | Path) -> dict[str, Any]:
        """
        Load checkpoint and restore training state.

        Args:
            checkpoint_path: Path to checkpoint file

        Returns:
            Checkpoint dictionary with training state
        """
        checkpoint = torch.load(checkpoint_path, map_location=self.agent.actor.device)

        # Restore agent
        self.agent.load_state_dict(checkpoint["agent_state_dict"])

        # Restore training state
        self.current_episode = checkpoint["episode"]
        self.total_steps = checkpoint["total_steps"]
        self.total_updates = checkpoint["total_updates"]
        self.best_metric_value = checkpoint["best_metric_value"]

        self.logger.info(f"Checkpoint loaded from: {checkpoint_path}")
        self.logger.info(f"Resuming from episode {self.current_episode}")
        self.logger.info(f"Total steps: {self.total_steps:,}")
        self.logger.info(f"Best metric: {self.best_metric_value:.4f}")

        return checkpoint
