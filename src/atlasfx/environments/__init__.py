"""
Trading environments module for AtlasFX.

ProductionTradingEnv: Full-featured production environment with ATR-based
position sizing, multi-asset support, and USD-centric risk management.
"""

from atlasfx.environments.trading_env import (
    ActionType as ProductionActionType,
    EpisodeMetadata,
    ProductionPosition,
    ProductionTrade,
    ProductionTradingConfig,
    ProductionTradingEnv,
)

__all__ = [
    "ProductionActionType",
    "EpisodeMetadata",
    "ProductionPosition",
    "ProductionTrade",
    "ProductionTradingConfig",
    "ProductionTradingEnv",
]
