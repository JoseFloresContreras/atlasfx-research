"""
Training module for AtlasFX.

This module provides trainers and utilities for training deep learning models,
including callbacks for experiment tracking with Weights & Biases.
"""

from atlasfx.training.callbacks import (
    WandbCallback,
    init_wandb_offline,
    init_wandb_online,
)
from atlasfx.training.sac_trainer import SACTrainer


__all__ = [
    "SACTrainer",
    "WandbCallback",
    "init_wandb_offline",
    "init_wandb_online",
]
