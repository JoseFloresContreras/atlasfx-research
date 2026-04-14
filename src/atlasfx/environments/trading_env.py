"""
AtlasFX – ProductionTradingEnv (v1.0)

═══════════════════════════════════════════════════════════════════════════════
This is the CANONICAL PRODUCTION environment for RL trading experiments.
All new experiments MUST use ProductionTradingEnv unless there is a very specific
reason to use a legacy or archived environment.
═══════════════════════════════════════════════════════════════════════════════

Level 4 (Production) - Full-featured environment for final evaluation and deployment.

Features:
  - ATR-based position sizing with real pips
  - USD-centric risk management
  - Multi-asset support
  - Stop loss / Take profit management
  - Transaction costs and slippage
  - Comprehensive metrics tracking
  - Episode export capabilities

Other environments have been moved to atlasfx.environments.archive.

Author: AtlasFX Team
Version: 4.1.0 (Production + Metrics + Export)
Date: November 4, 2025
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import json
import logging
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd

from atlasfx.evaluation.trading_metrics import TradingMetricsTracker
from atlasfx.utils.logging import get_logger


# Import forex notional helper for leverage cap
scripts_dir = Path(__file__).resolve().parents[3] / "scripts" / "audits"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

try:
    from forex_notional import notional_usd as calculate_notional_usd
except ImportError:
    # Fallback if forex_notional not available
    def calculate_notional_usd(symbol: str, units: float, entry_price: float) -> float:
        """Fallback notional calculation."""
        symbol_upper = symbol.upper().replace("-PAIR", "")
        if symbol_upper.startswith("USD"):
            # USDXXX: USD is base, units are already in USD
            return abs(units)
        # XXXUSD: non-USD base, convert via price
        return abs(units * entry_price)


if TYPE_CHECKING:
    from numpy.typing import NDArray


logger = get_logger(__name__)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _get_git_commit() -> str | None:
    """Get current git commit hash for reproducibility."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


# ============================================================================
# ENUMS & DATACLASSES
# ============================================================================


class ActionType(Enum):
    """Discrete action types (not primary interface)."""

    HOLD = 0
    BUY = 1
    SELL = -1
    CLOSE = 2


@dataclass
class ProductionTradingConfig:
    """
    Configuration for AtlasFX Production Trading Environment.

    Key features:
    - USD-centric risk management
    - ATR-based SL/TP distances
    - Realistic transaction costs (commission + spread + slippage)
    - Multi-asset support ready
    """

    # Capital & risk
    initial_balance: float = 10_000.0
    max_risk_per_trade_pct: float = 0.02  # 2% of balance per trade
    max_capital_at_risk_pct_total: float = 0.15  # 15% total portfolio risk

    # Transaction costs (USD) - REAL FX UNITS
    # These values are in real-world units (not scaled).
    # Typical for EURUSD majors: ~0.2 pips spread, 10 USD/pip/lot, 5 USD round-turn commission.
    commission_per_lot: float = 2.5  # USD per lot per side (5 USD round turn)
    spread_pips: float = 0.2  # Average bid-ask spread in pips
    slippage_bps: float = 0.0  # DEPRECATED — use slippage_pips_mean instead.
    # BUG: slippage_bps computes trade_value = units × close_price which gives
    # QUOTE-currency notional (e.g. JPY for USDJPY), then treats it as USD.
    # This inflates cost ~150× for JPY pairs and ~1× for XXX/USD pairs.
    # Also 5 bps ≈ 5-7 pips, which is extreme (real slippage ≈ 0.1-0.3 pips).
    # Kept for backward compatibility only.

    # ── Pips-first slippage (preferred, correct for all pairs) ──
    slippage_pips_mean: float = 0.0  # Mean slippage per fill in pips (0 = disabled)
    slippage_pips_std: float = 0.0  # Std dev of slippage in pips
    slippage_half_normal: bool = True  # True = half-normal (always adverse); False = normal
    allow_positive_slippage: bool = False  # If True + half_normal=False, slippage can improve fills

    # ── Exit slippage (applied on close: SL, TP, reverse, agent_close) ──
    exit_slippage_enabled: bool = False  # Master switch for exit slippage
    exit_slippage_mult_sl: float = 1.5   # Multiplier vs entry slippage for SL exits (fast moves)
    exit_slippage_mult_tp: float = 1.0   # Multiplier vs entry slippage for TP exits
    exit_slippage_mult_reverse: float = 1.0  # Multiplier for reversal exits
    exit_slippage_mult_agent_close: float = 1.0  # Multiplier for voluntary closes
    exit_slippage_mode: str = "both"  # "both" (default), "price_only", "cost_only"
    # price_only: slippage adjusts exit price (affects pnl_usd), cost=0
    # cost_only: exit price unchanged, slippage recorded as cost only
    # both: adjusts price AND records cost (WARNING: double-counts in net_pnl)

    # Market microstructure
    pip_value_per_lot: float = 10.0  # USD value per pip per lot (real unitsf)
    lot_size: float = 100_000.0  # Standard lot size (units)
    max_position_lots: float | None = (
        50.0  # Maximum position size in lots (safety limit), None = no limit
    )

    # HARDENED SIZING CAPS (per-symbol)
    max_lots_per_symbol: float = 20.0  # Max lots per symbol (default: 20)
    max_notional_per_symbol_usd: float | None = (
        None  # Max notional USD per symbol (None = no limit)
    )
    max_portfolio_notional_usd: float | None = (
        None  # Max total portfolio notional USD (None = no limit)
    )
    max_concentration_pct_per_symbol: float = (
        40.0  # Max % of portfolio equity per symbol (default: 40%)
    )

    # ATR column naming convention
    atr_column_template: str = (
        "[Feature] {symbol} | atr_14"  # Normalized ATR for observations (z-scored)
    )
    atr_real_column_template: str = (
        "{symbol} | atr_14_real_pips"  # RAW ATR in pips for position sizing (NOT normalized)
    )
    atr_fallback_pips: float = 10.0  # Fallback ATR value in pips if column missing
    atr_floor_pips: float = (
        0.5  # Minimum ATR floor for SL calculation (prevents position explosion)
    )
    sl_atr_multiple: float = 2.0  # Stop loss distance as multiple of ATR
    min_sl_pips: float = 0.0
    """
    Mínimo tamaño de stop loss EN PIPS aplicado después de la lógica normal de ATR.
    - 0.0 → desactivado (comportamiento actual).
    - >0.0 → sl_dist_pips nunca será menor que este valor.
    """
    min_tp_sl_ratio: float = 1.5  # Minimum TP/SL ratio in pips
    max_tp_sl_ratio: float = 3.0  # Maximum TP/SL ratio in pips
    """
    Ratio mínimo y máximo TP/SL en PIPS.
    - min_tp_sl_ratio: TP en pips será al menos este múltiplo del SL.
    - max_tp_sl_ratio: TP en pips será como máximo este múltiplo del SL.
    Ejemplo: si SL = 10 pips y min_tp_sl_ratio=1.5, TP mínimo = 15 pips.
    """

    # Break-even stop configuration
    enable_break_even_stop: bool = False  # Enable break-even stop adjustment
    break_even_trigger_r: float = 0.8  # Trigger break-even when profit >= R × initial_sl_pips
    break_even_buffer_mode: str = "auto"  # 'auto' (spread-based) or 'fixed'
    break_even_buffer_pips: float | None = None  # Fixed buffer in pips (if mode='fixed')

    # Trailing stop configuration
    enable_trailing_stop: bool = False  # Enable trailing stop adjustment
    trailing_start_r: float = 1.5  # Start trailing when profit >= R × initial_sl_pips
    trailing_atr_multiple: float = 1.0  # Trailing distance as multiple of ATR
    trailing_min_distance_pips: float | None = None  # Minimum trailing distance in pips

    # Episode configuration
    episode_length: int = 1000
    validation_mode: bool = False

    # Reward configuration
    reward_type: Literal["pnl_normalized", "pnl_sharpe_mix"] = "pnl_normalized"
    lambda_clamp_penalty: float = 0.01  # Penalty for risk clamping
    lambda_risk_penalty: float = 0.05  # Penalty proportional to capital at risk
    lambda_trade_incentive: float = 0.0  # DEPRECATED: DO NOT USE (causes overtrading)
    # Note: Trade incentive should always be 0.0. Any positive value incentivizes
    # overtrading which destroys profitability through transaction costs.

    # Action penalty (for discouraging overtrading)
    action_penalty: float = 0.0  # Penalty per trade executed (default: 0.0 = no penalty)

    # Dead zone: if abs(target_pos_frac) < this threshold, treat as HOLD (no trade)
    # Prevents SAC Gaussian policy noise from triggering micro-trades every bar.
    position_dead_zone: float = 0.0

    # Minimum hold period: after opening/changing a position, prevent further
    # changes for this many bars. Prevents compulsive flip-flopping.
    min_hold_period: int = 0

    # ── Turnover penalty (soft anti-overtrading via reward) ──
    lambda_turnover: float = 0.0
    """
    Penalty per lot traded per step.
    reward -= lambda_turnover * lots_traded_step
    0.0 = disabled (default, backward compatible).
    """

    # ── Flip penalty (penalise sign reversals) ──
    lambda_flip: float = 0.0
    """
    One-off penalty applied when position sign changes (long→short or short→long).
    reward -= lambda_flip  (once per flip event)
    0.0 = disabled (default).
    """

    # ── Anti-instant-reverse (E052b) ──
    disallow_instant_reverse: bool = False
    """
    When True and agent holds a position: if the agent requests a
    target of the OPPOSITE sign, the action is converted to
    'close to flat' (target_pos=0).  The agent must wait for
    cooldown_bars before re-entering on the opposite side.
    False = disabled (default, backward compatible).
    """
    flip_penalty_on_attempt: bool = True
    """
    When True, lambda_flip penalty fires even when a reverse is
    blocked by disallow_instant_reverse.  The *attempt* itself is
    penalised so the policy learns not to propose reversals.
    Only relevant when disallow_instant_reverse=True and lambda_flip>0.
    """

    # ── Hysteresis entry/exit ("sniper gate") ──
    enter_threshold: float = 0.0
    """
    Minimum |target_pos_frac| to open a NEW position from flat.
    0.0 = disabled (uses position_dead_zone only).
    Typical: 0.25–0.30.
    """
    exit_threshold: float = 0.0
    """
    While in a position, allow close only if |target_pos_frac| <= exit_threshold.
    Between exit_threshold and enter_threshold the position is HELD unchanged.
    0.0 = disabled.
    Typical: 0.10–0.15.
    """

    # ── Cooldown post-close ──
    cooldown_bars: int = 0
    """
    After any position close (SL/TP/reverse/agent_close), block new entries
    for this many bars.  0 = disabled (default).
    Typical: 10–20.  Moderate guardrail, not main selectivity mechanism.
    """

    # Loss penalty (asymmetric penalty for negative rewards)
    loss_penalty_factor: float = 1.0
    """
    Multiplicador aplicado a recompensas negativas.
    - Si == 1.0 → comportamiento actual (sin cambio).
    - Si  > 1.0 → las recompensas negativas se vuelven más negativas.
    Ejemplo: loss_penalty_factor=1.5 convierte reward=-0.01 en reward=-0.015
    """

    # Numerical guards
    min_balance_eps: float = 1.0  # Minimum balance for normalization
    min_position_change: float = 1e-6  # Minimum trade size to execute

    # Metrics configuration
    risk_free_rate: float = 0.02  # 2% annual risk-free rate for Sharpe/Sortino
    periods_per_year: int = 362_880  # 252 days * 24h * 60min = 1-min forex bars per year

    # VAE feature configuration
    use_vae_features: bool = False  # Use VAE latent features instead of raw technical features
    vae_feature_prefix: str = "[VAE]"  # Prefix for VAE feature columns

    # Leverage cap
    max_leverage: float | None = (
        None  # Maximum leverage allowed (None = no cap, backward compatible)
    )
    """
    Hard maximum leverage cap applied before opening positions.
    If leverage > max_leverage, position size is scaled down proportionally.
    - None: No cap (backward compatible)
    - float: Cap value (e.g., 20.0 for 20x leverage)

    Leverage is calculated as: notional_usd / equity_current
    Where notional_usd accounts for currency pair conventions:
    - USDJPY: notional_usd = abs(units) (USD is base)
    - EURUSD/GBPUSD: notional_usd = abs(units * entry_price)
    """

    # Position sizing mode (for size-invariant evaluation)
    position_sizing_mode: Literal["agent", "fixed_lots"] = "agent"
    """
    Position sizing mode:
    - "agent": Agent determines position size via risk management (default)
    - "fixed_lots": Agent only determines direction/entry/exit, size is fixed
    """

    fixed_lots: float = 1.0
    """
    Fixed lot size when position_sizing_mode == "fixed_lots".
    Agent's conviction is ignored - all positions are this size (respecting sign).
    Used for size-invariant evaluation to isolate timing edge from sizing edge.
    """

    disable_risk_scaling: bool = False
    """
    If True, disables ALL risk-based position scaling (max_total_risk clamp, etc.).
    ONLY use for "pure fixed lots" experiments to isolate timing edge.
    WARNING: Can result in very high risk per trade if equity is low.
    - False (default): Apply all risk management safeguards
    - True: Skip max_total_risk clamp, execute exact fixed_lots regardless of equity
    """


@dataclass
class ProductionPosition:
    """Represents an open trading position."""

    symbol: str
    units: float  # Base currency units (can be negative for short)
    avg_entry: float  # Average entry price
    sl: float  # Stop loss price level
    tp: float  # Take profit price level
    entry_time: int  # Step index when position opened
    unrealized_pnl_usd: float = 0.0  # Current unrealized PnL in USD

    # Break-even and trailing stop tracking
    initial_sl_pips: float = 0.0  # Original SL distance in pips (never changes after opening)
    initial_tp_pips: float = 0.0  # Original TP distance in pips (never changes after opening)
    initial_tp_sl_ratio: float = 0.0  # Original TP/SL ratio (never changes after opening)
    moved_to_break_even: bool = False  # Flag indicating if SL was moved to break-even
    high_watermark_price: float = 0.0  # Highest price reached (for longs) or entry for shorts
    low_watermark_price: float = 0.0  # Lowest price reached (for shorts) or entry for longs

    # MAE/MFE intra-trade tracking
    pos_mae_price: float = 0.0  # Worst adverse price during position life
    pos_mfe_price: float = 0.0  # Best favorable price during position life
    pre_bar_mae: float = 0.0  # Saved before bar update (for exit corrections)
    pre_bar_mfe: float = 0.0  # Saved before bar update (for exit corrections)

    @property
    def is_long(self) -> bool:
        return self.units > 0

    @property
    def is_short(self) -> bool:
        return self.units < 0

    @property
    def notional_value(self) -> float:
        """Position notional value in quote currency."""
        return abs(self.units * self.avg_entry)


@dataclass
class ProductionTrade:
    """Represents a completed trade (for history tracking)."""

    symbol: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    units: float
    pnl_usd: float  # Realized PnL in USD
    commission_usd: float
    slippage_usd: float
    initial_sl_pips: float = 0.0  # Initial SL distance in pips
    initial_tp_pips: float = 0.0  # Initial TP distance in pips
    initial_tp_sl_ratio: float = 0.0  # Initial TP/SL ratio

    # Trade identification
    trade_id: int = -1  # Auto-assigned sequential ID per episode
    exit_reason: str = "other"  # sltp_sl | sltp_tp | agent_close | reverse | other

    # Leverage cap tracking
    notional_usd: float = 0.0  # Notional value in USD
    leverage_before_cap: float = 0.0  # Leverage before cap applied
    leverage_after_cap: float = 0.0  # Leverage after cap applied
    cap_hit: bool = False  # Whether leverage cap was hit
    cap_scale: float = 1.0  # Scaling factor applied (1.0 = no scaling)

    # Position sizing cap tracking
    units_desired: float = 0.0  # Desired units before any caps
    max_lots_hit: bool = False  # Whether max_lots cap was hit
    max_leverage_hit: bool = False  # Whether max_leverage cap was hit
    equity_at_entry: float = 0.0  # Equity at trade entry for leverage calculations

    # MAE/MFE (Maximum Adverse/Favorable Excursion)
    mae_price: float = float("nan")  # Worst adverse price during trade life
    mfe_price: float = float("nan")  # Best favorable price during trade life
    mae_usd: float = float("nan")  # PnL in USD at MAE point
    mfe_usd: float = float("nan")  # PnL in USD at MFE point

    @property
    def side(self) -> str:
        """Trade direction: 'long' or 'short'."""
        return "long" if self.units > 0 else "short"

    @property
    def lots(self) -> float:
        """Absolute position size in standard lots (100k units)."""
        return abs(self.units) / 100_000

    @property
    def return_pct(self) -> float:
        """Return percentage (PnL / entry value)."""
        entry_value = abs(self.units * self.entry_price)
        if entry_value == 0:
            return 0.0
        return (self.pnl_usd / entry_value) * 100

    @property
    def duration(self) -> int:
        """Trade duration in steps."""
        return self.exit_time - self.entry_time


@dataclass
class EpisodeMetadata:
    """
    Episode metadata for reproducibility and auditing.

    Contains all information needed to reproduce an episode exactly,
    including environment configuration, random seed, and system state.
    """

    episode_id: str
    seed: int | None
    timestamp: str
    git_commit: str | None
    config: ProductionTradingConfig
    python_version: str
    environment_version: str = "4.1.0"

    # Additional runtime info
    num_steps: int = 0
    num_trades: int = 0
    final_balance: float = 0.0
    final_equity: float = 0.0


# ============================================================================
# MAIN ENVIRONMENT CLASS
# ============================================================================


class ProductionTradingEnv:
    """
    AtlasFX Production Trading Environment - Level 4 (Production).

    Key Features:
    - USD-centric risk management with ATR-based SL/TP
    - Realistic transaction costs (commission, spread, slippage)
    - Pessimistic fill logic (SL wins ties)
    - Multi-asset support ready
    - Gymnasium-compatible API
    - Comprehensive logging and diagnostics

    Action Space:
        Continuous action per asset: [target_pos_frac, sl_dist_ATR, tp_dist_ATR]
        - target_pos_frac: Desired position fraction ∈ [-1, 1]
        - sl_dist_ATR: Stop loss distance in ATR multiples (e.g., 2.0 = 2×ATR)
        - tp_dist_ATR: Take profit distance in ATR multiples (e.g., 3.0 = 3×ATR)

    Observation Space:
        Concatenated vector:
        - Market features (from DataFrame columns starting with "[Feature]")
        - Agent state per asset: [pos_frac, sl_norm_atr, tp_norm_atr, executed_fraction,
                                   sl_ATR, tp_ATR, last_clamped_flag, position_open_flag]
        - Optional: latent_states, forecasts (if provided)

    Reward:
        reward = (pnl_usd_step - costs) / balance - clamp_penalty - risk_penalty
        where risk_penalty = λ × (capital_at_risk / balance)
    """

    def __init__(
        self,
        data: pd.DataFrame,
        symbols: list[str],
        price_cols: dict[str, dict[str, str]],
        config: ProductionTradingConfig | None = None,
        latent_states: NDArray[np.float64] | None = None,
        forecasts: NDArray[np.float64] | None = None,
    ) -> None:
        """
        Initialize the trading environment.

        Args:
            data: DataFrame with OHLC and feature columns
            symbols: List of trading symbols (e.g., ["EURUSD", "GBPUSD"])
            price_cols: Mapping of symbols to OHLC column names
                       e.g., {"EURUSD": {"open": "EURUSD-pair | open", ...}}
            config: Trading configuration (or None for defaults)
            latent_states: Optional latent representations (T × latent_dim)
            forecasts: Optional forecast vectors (T × forecast_dim)
        """
        self.data = data
        self.symbols = symbols
        self.price_cols = price_cols
        self.config = config or ProductionTradingConfig()
        self.latent_states = latent_states
        self.forecasts = forecasts

        self.num_assets = len(symbols)
        self.num_steps = len(data)

        # Action penalty for discouraging overtrading
        self.action_penalty = self.config.action_penalty

        # Loss penalty factor for asymmetric penalty on negative rewards
        self.loss_penalty_factor = self.config.loss_penalty_factor

        # Validate data
        self._validate_data()

        # Extract price arrays and ATR values per symbol
        self._load_market_data()

        # Observation/action space dimensions
        self.action_dim = 3 * self.num_assets  # [pos_frac, sl_ATR, tp_ATR] per asset
        self.state_dim = self._calculate_state_dim()

        # Episode state (initialized in reset)
        self.current_step: int = 0
        self.start_step: int = 0
        self.balance: float = 0.0
        self.equity: float = 0.0
        self.peak_equity: float = 0.0

        # Positions and trades
        self.positions: dict[str, ProductionPosition] = {}
        self.trade_history: list[ProductionTrade] = []

        # Track costs per position (entry + accumulated costs)
        self.position_costs: dict[str, tuple[float, float]] = {}  # symbol -> (commission, slippage)

        # Agent state per asset
        self.last_executed_actions: dict[str, NDArray[np.float64]] = {
            sym: np.zeros(3, dtype=np.float64) for sym in symbols
        }
        self.last_clamped_flags: dict[str, float] = dict.fromkeys(symbols, 0.0)

        # Risk tracking
        self.total_capital_at_risk_usd: float = 0.0

        # Metrics tracking
        self.metrics_tracker = TradingMetricsTracker(
            initial_balance=self.config.initial_balance,
            risk_free_rate=self.config.risk_free_rate,
            periods_per_year=self.config.periods_per_year,
        )

        # Seed tracking for reproducibility
        self.last_seed: int | None = None

        # RNG (set in reset with seed)
        self.rng: np.random.Generator = np.random.default_rng()

        logger.info(
            "ProductionTradingEnv initialized: %d assets, %d steps, state_dim=%d",
            self.num_assets,
            self.num_steps,
            self.state_dim,
        )

    # ========================================================================
    # DATA VALIDATION & LOADING
    # ========================================================================

    def _validate_data(self) -> None:
        """Validate that required columns exist in data."""
        for symbol in self.symbols:
            if symbol not in self.price_cols:
                raise ValueError(f"Symbol {symbol} not found in price_cols mapping.")

            for price_type in ["open", "high", "low", "close"]:
                col = self.price_cols[symbol].get(price_type)
                if not col or col not in self.data.columns:
                    raise ValueError(
                        f"Required column '{col}' for {symbol}/{price_type} not found in data."
                    )

            # Check ATR column (normalized, for features)
            atr_col = self.config.atr_column_template.format(symbol=symbol)
            if atr_col not in self.data.columns:
                raise ValueError(
                    f"ATR column '{atr_col}' not found. ATR in price units is required."
                )

            # Check ATR real column (for risk management) - OPTIONAL with fallback
            atr_real_col = self.config.atr_real_column_template.format(symbol=symbol)
            if atr_real_col not in self.data.columns:
                logger.warning(
                    f"⚠️  ATR real column '{atr_real_col}' not found for {symbol}. "
                    f"Will use fallback: {self.config.atr_fallback_pips} pips. "
                    f"⚠️  Add 'atr_real_pips' featurizer to data pipeline for optimal position sizing."
                )

    def _load_market_data(self) -> None:
        """Load OHLC and ATR data for all symbols."""
        self.ohlc_data: dict[str, dict[str, NDArray[np.float64]]] = {}
        self.atr_data: dict[str, NDArray[np.float64]] = {}
        self.atr_data_real: dict[str, NDArray[np.float64]] = {}

        for symbol in self.symbols:
            cols = self.price_cols[symbol]
            self.ohlc_data[symbol] = {
                "open": self.data[cols["open"]].values.astype(np.float64),
                "high": self.data[cols["high"]].values.astype(np.float64),
                "low": self.data[cols["low"]].values.astype(np.float64),
                "close": self.data[cols["close"]].values.astype(np.float64),
            }

            # Normalized ATR (for observations)
            atr_col = self.config.atr_column_template.format(symbol=symbol)
            self.atr_data[symbol] = self.data[atr_col].values.astype(np.float64)

            # Real ATR in pips (for risk management position sizing)
            atr_real_col = self.config.atr_real_column_template.format(symbol=symbol)
            if atr_real_col in self.data.columns:
                self.atr_data_real[symbol] = self.data[atr_real_col].values.astype(np.float64)
                logger.info(f"✅ Using ATR_REAL for {symbol}: {atr_real_col}")
            else:
                # Use fallback fixed ATR in pips
                logger.warning(
                    f"⚠️  {symbol}: ATR_REAL column not found, using fallback "
                    f"{self.config.atr_fallback_pips} pips"
                )
                self.atr_data_real[symbol] = np.full(
                    self.num_steps, self.config.atr_fallback_pips, dtype=np.float64
                )

        # Detect VAE feature columns
        self.vae_feature_columns = [
            c for c in self.data.columns if c.startswith(self.config.vae_feature_prefix)
        ]

        if self.vae_feature_columns:
            logger.info(
                "Detected %d VAE feature columns with prefix='%s'",
                len(self.vae_feature_columns),
                self.config.vae_feature_prefix,
            )
        else:
            logger.info(
                "No VAE feature columns found with prefix='%s'",
                self.config.vae_feature_prefix,
            )

        # Extract feature columns based on configuration
        if self.config.use_vae_features:
            # Use VAE features if enabled
            if not self.vae_feature_columns:
                raise ValueError(
                    f"use_vae_features=True but no VAE columns found with prefix '{self.config.vae_feature_prefix}'. "
                    f"Ensure the data contains VAE embeddings or set use_vae_features=False."
                )
            feature_cols = self.vae_feature_columns
            logger.info("Using VAE features for observations (%d dimensions)", len(feature_cols))
        else:
            # Use traditional [Feature] columns
            feature_cols = [c for c in self.data.columns if c.startswith("[Feature]")]
            if not feature_cols:
                logger.warning("No feature columns found (columns starting with '[Feature]').")

        if not feature_cols:
            self.feature_data = np.zeros((self.num_steps, 0), dtype=np.float64)
        else:
            self.feature_data = self.data[feature_cols].values.astype(np.float64)

        logger.info(
            "Market data loaded: %d steps, %d feature columns", self.num_steps, len(feature_cols)
        )

    def _calculate_state_dim(self) -> int:
        """Calculate observation space dimension."""
        # Market features
        dim = self.feature_data.shape[1]

        # Agent state per asset: 8 features
        # [pos_frac, sl_norm_atr, tp_norm_atr, executed_fraction, sl_ATR, tp_ATR, clamped_flag, pos_open_flag]
        dim += 8 * self.num_assets

        # Optional: latent states
        if self.latent_states is not None:
            dim += self.latent_states.shape[1]

        # Optional: forecasts
        if self.forecasts is not None:
            if self.forecasts.ndim == 1:
                dim += 1
            else:
                dim += self.forecasts.shape[1]

        return dim

    # ========================================================================
    # GYM API - RESET
    # ========================================================================

    def reset(
        self,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.float64], dict[str, Any]]:
        """
        Reset environment for a new episode.

        Args:
            seed: Random seed for reproducibility
            options: Optional configuration overrides

        Returns:
            observation: Initial observation vector
            info: Diagnostic information
        """
        # Store seed for reproducibility tracking
        self.last_seed = seed

        if seed is not None:
            self.rng = np.random.default_rng(seed)

        # Determine episode start
        if self.config.validation_mode:
            self.start_step = 0
        elif options and "start_step" in options:
            self.start_step = int(options["start_step"])
        else:
            max_start = self.num_steps - self.config.episode_length - 1
            self.start_step = int(self.rng.integers(0, max(1, max_start)))

        self.current_step = self.start_step

        # Reset financial state
        self.balance = self.config.initial_balance
        self.equity = self.config.initial_balance
        self.peak_equity = self.config.initial_balance

        # Clear positions and trades
        self.positions = {}
        self.trade_history = []
        self.position_costs = {}
        self._next_trade_id = 0  # Auto-incrementing trade ID

        # Reset agent state
        self.last_executed_actions = {sym: np.zeros(3, dtype=np.float64) for sym in self.symbols}
        self.last_clamped_flags = dict.fromkeys(self.symbols, 0.0)
        self.total_capital_at_risk_usd = 0.0

        # ── Sniper-gate / cooldown / turnover state ──────────────────────
        self.cooldown_remaining = dict.fromkeys(self.symbols, 0)  # bars left
        self.episode_turnover_lots = 0.0   # cumulative lots traded
        self.episode_flips = 0             # sign-change count
        self.prev_position_sign = dict.fromkeys(self.symbols, 0)  # -1/0/+1
        self.reverse_attempts_blocked = 0  # E052b counter
        self.cooldown_blocks = 0           # E052b counter

        # Emergency brake instrumentation (P0 AUDIT)
        self.emergency_brake_trigger_count = 0
        self.emergency_brake_first_trigger_step = None
        self.emergency_brake_total_steps_after = 0
        self.emergency_brake_trades_after = 0

        # Emergency brake instrumentation (P0 AUDIT)
        self.emergency_brake_trigger_count = 0
        self.emergency_brake_first_trigger_step = None
        self.emergency_brake_total_steps_after = 0
        self.emergency_brake_trades_after = 0

        # Reset counters for diagnostics
        self.atr_floor_hits = 0  # Counts when ATR floor is applied
        self.position_cap_hits = 0  # Counts when position cap is applied

        # HARDENED SIZING CAPS - per-symbol tracking
        self.lot_cap_hits_by_symbol = dict.fromkeys(self.symbols, 0)
        self.notional_cap_hits_by_symbol = dict.fromkeys(self.symbols, 0)
        self.concentration_cap_hits_by_symbol = dict.fromkeys(self.symbols, 0)
        self.max_lots_observed_by_symbol = dict.fromkeys(self.symbols, 0.0)
        self.max_notional_observed_by_symbol = dict.fromkeys(self.symbols, 0.0)
        self.max_concentration_observed_pct_by_symbol = dict.fromkeys(self.symbols, 0.0)

        # Reset metrics tracker
        self.metrics_tracker = TradingMetricsTracker(
            initial_balance=self.config.initial_balance,
            risk_free_rate=self.config.risk_free_rate,
            periods_per_year=self.config.periods_per_year,
        )

        # Record initial state
        self.metrics_tracker.record_step(
            step=self.current_step,
            balance=self.balance,
            equity=self.equity,
        )

        obs = self._get_observation()
        info = {
            "step": self.current_step,
            "balance": self.balance,
            "equity": self.equity,
            "positions": len(self.positions),
        }

        logger.debug("Environment reset at step %d (balance=%.2f)", self.start_step, self.balance)
        return obs, info

    # ========================================================================
    # GYM API - STEP
    # ========================================================================

    def step(
        self, action: NDArray[np.float64]
    ) -> tuple[NDArray[np.float64], float, bool, bool, dict[str, Any]]:
        """
        Execute one environment step.

        Args:
            action: Action vector (3 × num_assets): [target_pos_frac, sl_ATR, tp_ATR] per asset

        Returns:
            observation: Next observation
            reward: Step reward
            terminated: Whether episode ended naturally
            truncated: Whether episode was truncated
            info: Diagnostic information
        """
        # Validate and reshape action
        action = np.asarray(action, dtype=np.float64).reshape(-1, 3)
        if action.shape[0] != self.num_assets:
            raise ValueError(
                f"Action shape mismatch: expected ({self.num_assets}, 3), got {action.shape}"
            )

        # Clip action to valid ranges
        action[:, 0] = np.clip(action[:, 0], -1.0, 1.0)  # target_pos_frac
        action[:, 1] = np.clip(action[:, 1], 0.1, 5.0)  # sl_dist_ATR
        action[:, 2] = np.clip(action[:, 2], 0.1, 10.0)  # tp_dist_ATR

        # Get current market data
        current_idx = self.current_step
        if current_idx >= self.num_steps:
            raise IndexError(f"Step index {current_idx} exceeds data length {self.num_steps}")

        # Lagged ATR index for causal correctness: ATR[T] uses high[T]/low[T],
        # unknown at open[T]. Use ATR[T-1] for sizing and protective-stop updates.
        # Impact is minimal (1 bar out of 14-period average ≈ 7% change).
        atr_lag_idx = max(current_idx - 1, 0)

        # ── Tick cooldown counters ───────────────────────────────────────
        if self.config.cooldown_bars > 0:
            for sym in self.symbols:
                if self.cooldown_remaining[sym] > 0:
                    self.cooldown_remaining[sym] -= 1

        info: dict[str, Any] = {"proposed_actions": action.copy()}

        # 1) Update protective stops (break-even and trailing) for existing positions
        for symbol in list(self.positions.keys()):
            current_price = float(self.ohlc_data[symbol]["open"][current_idx])
            current_atr_pips = self._get_atr_real_pips(symbol, atr_lag_idx)
            self._maybe_update_protective_stops(symbol, current_price, current_atr_pips)

        # 1.5) Update MAE/MFE tracking for existing positions before SL/TP check
        self._update_mae_mfe_pre_bar()

        # 2) Check SL/TP hits on existing positions (pessimistic)
        total_realized_pnl_usd = 0.0
        for symbol in list(self.positions.keys()):  # Copy keys to allow deletion
            high = float(self.ohlc_data[symbol]["high"][current_idx])
            low = float(self.ohlc_data[symbol]["low"][current_idx])
            realized_pnl_usd = self._check_sl_tp_hits(symbol, high, low)
            total_realized_pnl_usd += realized_pnl_usd

        self.balance += total_realized_pnl_usd
        info["pnl_realized_sltp_usd"] = float(total_realized_pnl_usd)

        # 3) Execute trades for each asset
        total_trade_costs = 0.0
        total_exec_pnl_usd = 0.0
        trades_before = len(self.trade_history)  # Track trades for incentive
        step_turnover_lots = 0.0  # lots traded this step (for turnover penalty)
        step_flips = 0            # sign-change events this step

        for asset_idx, symbol in enumerate(self.symbols):
            asset_action = action[asset_idx]
            current_open = float(self.ohlc_data[symbol]["open"][current_idx])

            # Get ATR in REAL PIPS for position sizing (lagged for causal correctness)
            current_atr_pips = self._get_atr_real_pips(symbol, atr_lag_idx)

            # Risk management: calculate trade size, SL/TP levels (using REAL ATR in pips)
            trade_units, sl_price, tp_price, rm_info = self._apply_risk_management(
                symbol, asset_action, current_open, current_atr_pips
            )

            # Update agent state
            self.last_executed_actions[symbol] = rm_info["executed_action"]
            self.last_clamped_flags[symbol] = 1.0 if rm_info["is_clamped"] else 0.0

            # Calculate costs (commission, slippage, total)
            commission, slippage, costs = self._calculate_costs(symbol, trade_units)
            total_trade_costs += costs

            # Store costs for this trade (will be used when position closes)
            if symbol not in self.position_costs:
                self.position_costs[symbol] = (
                    0.0,
                    0.0,
                    {},
                )  # (commission, slippage, leverage_info)
            old_comm, old_slip, _ = self.position_costs[symbol]

            # Extract leverage info from rm_info
            leverage_info = {
                "notional_usd": rm_info.get("leverage_before_cap", 0.0)
                * (
                    self.metrics_tracker.equity_curve[-1]
                    if len(self.metrics_tracker.equity_curve) > 0
                    else self.config.initial_balance
                ),
                "leverage_before_cap": rm_info.get("leverage_before_cap", 0.0),
                "leverage_after_cap": rm_info.get("leverage_after_cap", 0.0),
                "cap_hit": rm_info.get("cap_hit", False),
                "cap_scale": rm_info.get("cap_scale", 1.0),
                # NEW: Position sizing details
                "units_desired": rm_info.get("units_desired", 0.0),
                "max_lots_hit": rm_info.get("max_lots_hit", False),
                "max_leverage_hit": rm_info.get("max_leverage_hit", False),
                "equity_at_entry": rm_info.get("equity_at_entry", 0.0),
            }

            self.position_costs[symbol] = (
                old_comm + commission,
                old_slip + slippage,
                leverage_info,
            )

            # Execute trade (pass leverage info for logging)
            exec_pnl_usd = self._execute_trade(
                symbol, trade_units, current_open, sl_price, tp_price, leverage_info
            )
            total_exec_pnl_usd += exec_pnl_usd

            # ── Turnover & flip tracking ─────────────────────────────────
            lots_delta = abs(trade_units) / self.config.lot_size
            step_turnover_lots += lots_delta
            self.episode_turnover_lots += lots_delta

            # Detect sign flip (long↔short)
            new_sign = 0
            if symbol in self.positions:
                new_sign = int(np.sign(self.positions[symbol].units))
            old_sign = self.prev_position_sign.get(symbol, 0)
            if old_sign != 0 and new_sign != 0 and old_sign != new_sign:
                step_flips += 1
                self.episode_flips += 1
            # Count blocked reverse attempts as flips for penalty
            elif (
                self.config.flip_penalty_on_attempt
                and rm_info.get("reverse_attempt_blocked", False)
            ):
                step_flips += 1
                self.episode_flips += 1
            self.prev_position_sign[symbol] = new_sign

            # Track blocked attempts (E052b diagnostics)
            if rm_info.get("reverse_attempt_blocked", False):
                self.reverse_attempts_blocked += 1
            if rm_info.get("cooldown_skip", False):
                self.cooldown_blocks += 1

            # Store per-asset info
            info[f"{symbol}_rm"] = rm_info
            info[f"{symbol}_costs"] = float(costs)
            info[f"{symbol}_exec_pnl_usd"] = float(exec_pnl_usd)

        # Deduct costs from balance
        self.balance -= total_trade_costs
        self.balance += total_exec_pnl_usd

        # 4) Update equity and risk metrics
        self._update_equity()
        self.peak_equity = max(self.peak_equity, self.equity)

        # 5) Calculate reward (include trade count for incentive)
        trades_executed = len(self.trade_history) - trades_before
        total_pnl_usd = total_realized_pnl_usd + total_exec_pnl_usd
        reward = self._calculate_reward(
            total_pnl_usd, total_trade_costs, info, trades_executed,
            turnover_lots=step_turnover_lots, flips=step_flips,
        )

        # 6) Advance time
        self.current_step += 1

        # P0 AUDIT: Track steps/trades after brake
        if self.emergency_brake_first_trigger_step is not None:
            self.emergency_brake_total_steps_after = (
                self.current_step - self.emergency_brake_first_trigger_step
            )
            self.emergency_brake_trades_after = len(self.trade_history)

        # 7) Record step in metrics tracker
        self.metrics_tracker.record_step(
            step=self.current_step,
            balance=self.balance,
            equity=self.equity,
        )

        # 8) Check termination
        terminated = self.current_step >= self.start_step + self.config.episode_length
        truncated = False

        # Margin call
        if self.balance < self.config.min_balance_eps:
            terminated = True
            reward -= 100.0
            logger.warning("Margin call at step %d (balance=%.2f)", self.current_step, self.balance)

        # 9) Get observation and finalize info
        obs = (
            self._get_observation()
            if not terminated
            else np.zeros(self.state_dim, dtype=np.float64)
        )
        info.update(self._get_info())
        info["reward"] = float(reward)
        info["total_costs"] = float(total_trade_costs)
        info["total_pnl_usd"] = float(total_pnl_usd)

        return obs, float(reward), bool(terminated), bool(truncated), info

    # ========================================================================
    # PROTECTIVE STOPS (BREAK-EVEN & TRAILING)
    # ========================================================================

    def _maybe_update_protective_stops(
        self,
        symbol: str,
        current_price: float,
        atr_pips: float,
    ) -> None:
        """
        Update protective stops (break-even and/or trailing) for an open position.

        This method implements two types of protective stops:
        1. Break-even stop: Moves SL to entry + buffer when profit >= trigger_r × initial_sl_pips
        2. Trailing stop: Adjusts SL based on price watermarks and ATR distance

        Args:
            symbol: Asset symbol
            current_price: Current market price (open of current bar)
            atr_pips: Current ATR value in real pips

        Notes:
            - Only updates SL if it improves (moves in favorable direction)
            - Respects enable_break_even_stop and enable_trailing_stop flags
            - Uses initial_sl_pips (set at position opening) for R calculations
            - Does nothing if initial_sl_pips <= 0 (safety check)
        """
        # Early return if no protective stops enabled
        if not self.config.enable_break_even_stop and not self.config.enable_trailing_stop:
            return

        # Position must exist
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]

        # Safety check: avoid division by zero
        if pos.initial_sl_pips <= 0:
            return

        # Determine pip size for this symbol
        pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001

        # Calculate buffer for break-even (spread + commission round-turn)
        commission_pips_round_turn = (
            2.0 * self.config.commission_per_lot
        ) / self.config.pip_value_per_lot

        if (
            self.config.break_even_buffer_mode == "fixed"
            and self.config.break_even_buffer_pips is not None
        ):
            buffer_pips = self.config.break_even_buffer_pips
        else:
            # Auto mode: spread + commission
            buffer_pips = self.config.spread_pips + commission_pips_round_turn

        # Calculate unrealized profit in pips (signed correctly for long/short)
        if pos.units > 0:  # Long position
            unrealized_pips = (current_price - pos.avg_entry) / pip_size
        else:  # Short position
            unrealized_pips = (pos.avg_entry - current_price) / pip_size

        # Calculate R (multiples of initial risk)
        R = unrealized_pips / pos.initial_sl_pips

        # 1) Break-even stop logic
        if (
            self.config.enable_break_even_stop
            and self.config.break_even_trigger_r <= R
            and not pos.moved_to_break_even
        ):
            buffer_price = buffer_pips * pip_size

            if pos.units > 0:  # Long
                new_sl = pos.avg_entry + buffer_price
            else:  # Short
                new_sl = pos.avg_entry - buffer_price

            # Apply only if it improves the SL
            should_update = False
            if pos.units > 0:  # Long: new SL should be higher
                should_update = new_sl > pos.sl
            else:  # Short: new SL should be lower
                should_update = new_sl < pos.sl

            if should_update:
                pos.sl = new_sl
                pos.moved_to_break_even = True
                logger.debug(
                    f"Break-even applied: {symbol} at R={R:.2f}, new SL={new_sl:.5f} (entry={pos.avg_entry:.5f} + buffer={buffer_pips:.2f} pips)"
                )

        # 2) Trailing stop logic
        if self.config.enable_trailing_stop and self.config.trailing_start_r <= R:
            # Update watermarks
            if pos.units > 0:  # Long
                pos.high_watermark_price = max(pos.high_watermark_price, current_price)
            else:  # Short
                pos.low_watermark_price = min(pos.low_watermark_price, current_price)

            # Calculate trailing distance in pips
            base_trail_pips = self.config.trailing_atr_multiple * atr_pips
            min_trail_pips = self.config.trailing_min_distance_pips or buffer_pips
            trail_dist_pips = max(base_trail_pips, min_trail_pips)
            trail_dist_price = trail_dist_pips * pip_size

            # Calculate new SL based on watermark
            if pos.units > 0:  # Long
                new_sl = pos.high_watermark_price - trail_dist_price
            else:  # Short
                new_sl = pos.low_watermark_price + trail_dist_price

            # Apply only if it improves the SL
            should_update = False
            if pos.units > 0:  # Long: new SL should be higher
                should_update = new_sl > pos.sl
            else:  # Short: new SL should be lower
                should_update = new_sl < pos.sl

            if should_update:
                old_sl = pos.sl
                pos.sl = new_sl
                logger.debug(
                    f"Trailing stop updated: {symbol} at R={R:.2f}, SL {old_sl:.5f} -> {new_sl:.5f} (trail={trail_dist_pips:.2f} pips)"
                )

    # ========================================================================
    # MAE/MFE INTRA-TRADE TRACKING
    # ========================================================================

    def _update_mae_mfe_pre_bar(self) -> None:
        """
        Update MAE/MFE for existing positions with current bar's high/low.

        Called BEFORE _check_sl_tp_hits() in step().
        Only updates positions opened BEFORE the current bar (entry_time < current_step).
        Saves pre-bar values for exit correction in _check_sl_tp_hits / _execute_trade.
        """
        current_idx = self.current_step
        for symbol, pos in self.positions.items():
            # Skip positions opened THIS bar (no intra-bar tracking for entry bar)
            if pos.entry_time >= current_idx:
                continue

            high = float(self.ohlc_data[symbol]["high"][current_idx])
            low = float(self.ohlc_data[symbol]["low"][current_idx])

            # Save pre-bar values for exit correction
            pos.pre_bar_mae = pos.pos_mae_price
            pos.pre_bar_mfe = pos.pos_mfe_price

            # Update with this bar's extremes
            if pos.is_long:
                pos.pos_mae_price = min(pos.pos_mae_price, low)
                pos.pos_mfe_price = max(pos.pos_mfe_price, high)
            else:
                pos.pos_mae_price = max(pos.pos_mae_price, high)
                pos.pos_mfe_price = min(pos.pos_mfe_price, low)

    def _finalize_mae_mfe_voluntary(
        self, symbol: str, pos: ProductionPosition
    ) -> tuple[float, float, float, float]:
        """
        Finalize MAE/MFE for a voluntary close (agent action at OPEN).

        For voluntary exits, we do NOT include the exit bar's high/low.
        Returns pre-bar MAE/MFE values and corresponding USD amounts.

        Args:
            symbol: Trading symbol
            pos: Position being closed

        Returns:
            (mae_price, mfe_price, mae_usd, mfe_usd)
        """
        if pos.entry_time < self.current_step:
            final_mae = pos.pre_bar_mae
            final_mfe = pos.pre_bar_mfe
        else:
            # Position opened and closed same bar — no intra-bar tracking possible
            final_mae = float("nan")
            final_mfe = float("nan")

        if not np.isnan(final_mae):
            mae_usd = self._calculate_pnl_usd(symbol, pos.units, pos.avg_entry, final_mae)
        else:
            mae_usd = float("nan")

        if not np.isnan(final_mfe):
            mfe_usd = self._calculate_pnl_usd(symbol, pos.units, pos.avg_entry, final_mfe)
        else:
            mfe_usd = float("nan")

        return final_mae, final_mfe, mae_usd, mfe_usd

    # ========================================================================
    # SL/TP CHECKING (PESSIMISTIC FILL)
    # ========================================================================

    def _check_sl_tp_hits(self, symbol: str, high: float, low: float) -> float:
        """
        Check if SL or TP was hit during the candle (pessimistic: SL wins ties).

        Args:
            symbol: Asset symbol
            high: Candle high price
            low: Candle low price

        Returns:
            realized_pnl_usd: Realized PnL in USD (0 if no hit)
        """
        if symbol not in self.positions:
            return 0.0

        pos = self.positions[symbol]
        exit_price = 0.0
        is_long = pos.is_long

        sl_hit = (is_long and low <= pos.sl and pos.sl > 0) or (
            not is_long and high >= pos.sl and pos.sl > 0
        )
        tp_hit = (is_long and high >= pos.tp and pos.tp > 0) or (
            not is_long and low <= pos.tp and pos.tp > 0
        )

        # Pessimistic: SL wins ties
        if sl_hit:
            exit_price = pos.sl
        elif tp_hit:
            exit_price = pos.tp

        if exit_price > 0:
            # Exit slippage: adjust exit price adversely before PnL calc
            exit_lots = abs(pos.units) / self.config.lot_size
            exit_slip_mult = (
                self.config.exit_slippage_mult_sl if sl_hit
                else self.config.exit_slippage_mult_tp
            )
            exit_price, exit_slip_usd = self._calculate_exit_slippage(
                symbol, exit_price, is_long, exit_lots, exit_slip_mult
            )

            pnl_usd = self._calculate_pnl_usd(symbol, pos.units, pos.avg_entry, exit_price)

            # Finalize MAE/MFE with SL/TP-specific corrections
            # SL exit: don't use high for MFE (assume adverse first); cap MAE at SL
            # TP exit: don't use low for MAE (assume favorable first); cap MFE at TP
            if sl_hit:
                if is_long:
                    final_mae = min(pos.pre_bar_mae, pos.sl)
                    final_mfe = pos.pre_bar_mfe
                else:
                    final_mae = max(pos.pre_bar_mae, pos.sl)
                    final_mfe = pos.pre_bar_mfe
            elif is_long:
                final_mae = pos.pre_bar_mae
                final_mfe = max(pos.pre_bar_mfe, pos.tp)
            else:
                final_mae = pos.pre_bar_mae
                final_mfe = min(pos.pre_bar_mfe, pos.tp)

            mae_usd = self._calculate_pnl_usd(symbol, pos.units, pos.avg_entry, final_mae)
            mfe_usd = self._calculate_pnl_usd(symbol, pos.units, pos.avg_entry, final_mfe)

            # Get accumulated costs and leverage info for this position (entry + any additions)
            commission_total = 0.0
            slippage_total = 0.0
            leverage_info = {}
            if symbol in self.position_costs:
                if len(self.position_costs[symbol]) == 3:
                    commission_total, slippage_total, leverage_info = self.position_costs[symbol]
                else:  # Backward compatibility
                    commission_total, slippage_total = self.position_costs[symbol]
                    leverage_info = {}

            # Add exit slippage to accumulated slippage total
            slippage_total += exit_slip_usd

            # Exit commission: charge $commission_per_lot on the closing side
            exit_commission = exit_lots * self.config.commission_per_lot
            commission_total += exit_commission

            # Record trade with actual costs and leverage
            _tid = self._next_trade_id
            self._next_trade_id += 1
            trade = ProductionTrade(
                trade_id=_tid,
                symbol=symbol,
                entry_time=pos.entry_time,
                exit_time=self.current_step,
                entry_price=pos.avg_entry,
                exit_price=exit_price,
                units=pos.units,
                pnl_usd=pnl_usd,
                commission_usd=commission_total,
                slippage_usd=slippage_total,
                initial_sl_pips=pos.initial_sl_pips,
                initial_tp_pips=pos.initial_tp_pips,
                initial_tp_sl_ratio=pos.initial_tp_sl_ratio,
                notional_usd=leverage_info.get("notional_usd", 0.0),
                leverage_before_cap=leverage_info.get("leverage_before_cap", 0.0),
                leverage_after_cap=leverage_info.get("leverage_after_cap", 0.0),
                cap_hit=leverage_info.get("cap_hit", False),
                cap_scale=leverage_info.get("cap_scale", 1.0),
                # Position sizing details
                units_desired=leverage_info.get("units_desired", pos.units),
                max_lots_hit=leverage_info.get("max_lots_hit", False),
                max_leverage_hit=leverage_info.get("max_leverage_hit", False),
                equity_at_entry=leverage_info.get("equity_at_entry", 0.0),
                # MAE/MFE
                mae_price=final_mae,
                mfe_price=final_mfe,
                mae_usd=mae_usd,
                mfe_usd=mfe_usd,
                # Exit reason
                exit_reason="sltp_sl" if sl_hit else "sltp_tp",
            )
            self.trade_history.append(trade)
            self.metrics_tracker.record_trade(trade)  # Track in metrics

            # Close position and clear costs
            del self.positions[symbol]
            if symbol in self.position_costs:
                del self.position_costs[symbol]
            # Activate cooldown after close
            if self.config.cooldown_bars > 0:
                self.cooldown_remaining[symbol] = self.config.cooldown_bars
            self._update_total_risk()

            logger.debug(
                "SL/TP hit for %s at %.5f (PnL=%.2f USD, exit_comm=%.4f, exit_slip=%.4f USD)",
                symbol,
                exit_price,
                pnl_usd,
                exit_commission,
                exit_slip_usd,
            )
            # Return PnL minus exit commission (slippage already in exit_price → in pnl_usd)
            return pnl_usd - exit_commission

        return 0.0

    # ========================================================================
    # RISK MANAGEMENT
    # ========================================================================

    def _log_sizing_event(
        self,
        symbol: str,
        step: int,
        reasons: list[str],
        requested_lots: float,
        clamped_lots: float,
        price: float,
    ) -> None:
        """
        Log sizing cap event to JSONL file.

        Only called when at least one cap was triggered.
        File created on first event.
        """
        import json
        from pathlib import Path

        log_dir = Path("reports/runtime_risk_monitoring")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "sizing_events.jsonl"

        # Calculate notionals
        requested_notional = abs(requested_lots * self.config.lot_size * price)
        clamped_notional = abs(clamped_lots * self.config.lot_size * price)
        if "JPY" in symbol.upper():
            requested_notional = abs(requested_lots * self.config.lot_size)
            clamped_notional = abs(clamped_lots * self.config.lot_size)

        # Calculate concentration %
        concentration_pct = (clamped_notional / self.equity * 100) if self.equity > 0 else 0.0

        event = {
            "step": step,
            "symbol": symbol,
            "event_type": "SIZING_CAP",
            "reasons": reasons,
            "requested_lots": float(requested_lots),
            "clamped_lots": float(clamped_lots),
            "requested_notional_usd": float(requested_notional),
            "clamped_notional_usd": float(clamped_notional),
            "equity_symbol": float(self.equity),  # This is sub-env equity
            "equity_total": float(self.equity),  # Same in sub-env (portfolio tracked elsewhere)
            "concentration_pct_symbol": float(concentration_pct),
            "price": float(price),
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def _get_atr_real_pips(self, symbol: str, step: int) -> float:
        """
        Get ATR in real pips for position sizing.

        Args:
            symbol: Trading symbol
            step: Current step index

        Returns:
            ATR value in pips (e.g., 10.0 for 10 pips)

        Notes:
            - Uses ATR_REAL column if available
            - Falls back to config.atr_fallback_pips if not present
            - Always logs warning on fallback (ONCE per symbol)
        """
        if symbol in self.atr_data_real:
            atr_pips = float(self.atr_data_real[symbol][step])

            # Sanity check: ATR should be positive
            if atr_pips <= 0:
                logger.warning(
                    f"⚠️  {symbol}: ATR_REAL is {atr_pips:.4f} at step {step}, "
                    f"using fallback {self.config.atr_fallback_pips} pips"
                )
                return self.config.atr_fallback_pips

            return atr_pips
        # Should not happen if _load_market_data works correctly
        logger.warning(
            f"⚠️  {symbol}: ATR_REAL not loaded, using fallback {self.config.atr_fallback_pips} pips"
        )
        return self.config.atr_fallback_pips

    def _apply_risk_management(
        self,
        symbol: str,
        action: NDArray[np.float64],
        price: float,
        atr_pips: float,
    ) -> tuple[float, float, float, dict[str, Any]]:
        """
        Apply USD-centric risk management to determine trade size and SL/TP levels.

        ⚠️  CRITICAL: This function now uses ATR_REAL (in pips) for position sizing,
        not normalized ATR. This fixes the 244x position amplification bug.

        Args:
            symbol: Asset symbol
            action: [target_pos_frac, sl_dist_ATR, tp_dist_ATR]
            price: Current market price
            atr_pips: ATR value in REAL PIPS (e.g., 10.0 for 10 pips)

        Returns:
            trade_units: Units to trade (delta from current position)
            sl_price: Stop loss price level
            tp_price: Take profit price level
            info: Risk management diagnostics
        """
        target_pos_frac, sl_dist_atr, tp_dist_atr = (
            float(action[0]),
            float(action[1]),
            float(action[2]),
        )
        conviction = abs(target_pos_frac)

        info: dict[str, Any] = {
            "proposed_action": action.copy(),
            "is_clamped": False,
            "clamp_reason": "none",
        }

        is_flat = symbol not in self.positions

        # ── Cooldown guard (flat only) ───────────────────────────────────
        # After any close, block new entries for cooldown_bars.
        if is_flat and self.config.cooldown_bars > 0:
            cd = self.cooldown_remaining.get(symbol, 0)
            if cd > 0:
                info["cooldown_skip"] = True
                info["cooldown_remaining"] = cd
                info["executed_action"] = np.array(
                    [0.0, sl_dist_atr, tp_dist_atr], dtype=np.float64
                )
                return 0.0, 0.0, 0.0, info

        # ── Anti-instant-reverse guard (E052b) ────────────────────────
        # If enabled and agent holds a position, convert opposite-sign
        # targets to "close to flat" instead of allowing a reversal.
        if self.config.disallow_instant_reverse and not is_flat:
            current_units = self.positions[symbol].units
            current_sign = np.sign(current_units)
            target_sign = np.sign(target_pos_frac)
            if target_sign != 0 and target_sign != current_sign:
                # Reverse attempt!  Convert to close-to-flat.
                info["reverse_attempt_blocked"] = True
                trade_units = -current_units
                info["executed_action"] = np.array(
                    [0.0, sl_dist_atr, tp_dist_atr], dtype=np.float64
                )
                return float(trade_units), 0.0, 0.0, info

        # ── Hysteresis gate ("sniper gate") ──────────────────────────────
        # When active (enter_threshold > 0):
        #   Flat  → open only if conviction >= enter_threshold
        #   In pos → HOLD unless conviction <= exit_threshold (→ force close)
        # Force-close bypasses dead-zone so the position actually closes.
        hysteresis_active = self.config.enter_threshold > 0.0
        hysteresis_force_close = False

        if hysteresis_active:
            if is_flat:
                if conviction < self.config.enter_threshold:
                    info["hysteresis_skip"] = True
                    info["hysteresis_reason"] = "flat_below_enter_th"
                    info["conviction"] = float(conviction)
                    info["executed_action"] = np.array(
                        [0.0, sl_dist_atr, tp_dist_atr], dtype=np.float64
                    )
                    return 0.0, 0.0, 0.0, info
            else:
                # In position
                if conviction <= self.config.exit_threshold:
                    # Agent signals low conviction → FORCE CLOSE full position
                    hysteresis_force_close = True
                    info["hysteresis_force_close"] = True
                    current_units = self.positions[symbol].units
                    # Return trade_units = -current_units → full close
                    # SL/TP irrelevant for close; set to 0
                    info["executed_action"] = np.array(
                        [0.0, sl_dist_atr, tp_dist_atr], dtype=np.float64
                    )
                    return float(-current_units), 0.0, 0.0, info
                else:
                    # conviction > exit_th → HOLD position unchanged
                    info["hysteresis_hold"] = True
                    info["hysteresis_reason"] = "in_pos_above_exit_th"
                    info["conviction"] = float(conviction)
                    info["executed_action"] = np.array(
                        [0.0, sl_dist_atr, tp_dist_atr], dtype=np.float64
                    )
                    return 0.0, 0.0, 0.0, info

        # ── Anti-overtrading guard A: Dead zone ──────────────────────────
        # If conviction is below the dead zone threshold, skip the trade.
        # This prevents SAC Gaussian noise from triggering a trade every bar.
        if conviction < self.config.position_dead_zone:
            info["dead_zone_skip"] = True
            info["conviction"] = float(conviction)
            info["executed_action"] = np.array(
                [0.0, sl_dist_atr, tp_dist_atr], dtype=np.float64
            )
            return 0.0, 0.0, 0.0, info

        # ── Anti-overtrading guard C: Min hold period ────────────────────
        # If we have an existing position and haven't held it long enough,
        # block any change (additions, reductions, reversals).
        if self.config.min_hold_period > 0 and symbol in self.positions:
            bars_held = self.current_step - self.positions[symbol].entry_time
            if bars_held < self.config.min_hold_period:
                info["hold_period_active"] = True
                info["bars_held"] = int(bars_held)
                info["min_hold_required"] = self.config.min_hold_period
                info["executed_action"] = np.array(
                    [0.0, sl_dist_atr, tp_dist_atr], dtype=np.float64
                )
                return 0.0, 0.0, 0.0, info

        # 1) Allowed risk in USD (conviction-scaled)
        allowed_risk_usd = float(self.balance * self.config.max_risk_per_trade_pct * conviction)
        info["allowed_risk_usd"] = allowed_risk_usd

        # 2) Calculate SL/TP distances in PIPS with ATR floor protection
        # Agent provides multiples of ATR (e.g., 2.0 = 2x ATR)
        # Apply numerical safety and ATR floor to prevent position explosion
        atr_pips_safe = max(atr_pips, 1e-6)  # Basic numerical safety
        sl_dist_pips = atr_pips_safe * self.config.sl_atr_multiple

        # Apply ATR floor - if SL distance falls below floor, use floor instead
        atr_floor_applied = False
        if sl_dist_pips < self.config.atr_floor_pips:
            sl_dist_pips = self.config.atr_floor_pips
            atr_floor_applied = True
            self.atr_floor_hits += 1

        # Apply min_sl_pips floor - additional constraint on top of ATR floor
        min_sl_floor_applied = False
        if self.config.min_sl_pips > 0.0 and sl_dist_pips < self.config.min_sl_pips:
            sl_dist_pips = self.config.min_sl_pips
            min_sl_floor_applied = True

        # 2b) TP en pips propuesto por el agente
        raw_tp_dist_pips = tp_dist_atr * atr_pips_safe

        # 2c) TP mínimo y máximo en función del SL
        min_tp_pips = sl_dist_pips * self.config.min_tp_sl_ratio
        max_tp_pips = sl_dist_pips * self.config.max_tp_sl_ratio

        # 2d) Aplicar clip:
        # - si raw_tp_dist_pips <= 0 → usar directamente el mínimo (para evitar TP degenerado)
        # - si > 0 → clip entre [min_tp_pips, max_tp_pips]
        if raw_tp_dist_pips <= 0.0:
            tp_dist_pips = float(min_tp_pips)
        else:
            tp_dist_pips = float(np.clip(raw_tp_dist_pips, min_tp_pips, max_tp_pips))

        info["atr_floor_applied"] = atr_floor_applied
        info["min_sl_floor_applied"] = min_sl_floor_applied
        info["sl_dist_pips"] = float(sl_dist_pips)
        info["tp_dist_pips"] = float(tp_dist_pips)
        info["tp_sl_ratio"] = float(tp_dist_pips / sl_dist_pips) if sl_dist_pips > 0 else 0.0
        info["tp_dist_pips_raw"] = float(raw_tp_dist_pips)

        # Guard against degenerate SL
        if sl_dist_pips < 1.0:
            info["executed_action"] = np.array([0.0, sl_dist_atr, tp_dist_atr], dtype=np.float64)
            return 0.0, 0.0, 0.0, info

        # 3) Calculate position size based on risk formula
        # Formula: risk_usd = lots × sl_pips × pip_value_per_lot
        # => lots = risk_usd / (sl_pips × pip_value_per_lot)
        # => units = lots × lot_size
        #
        # ⚠️  CRITICAL FIX: Now uses atr_pips (real pips, e.g., 10.0)
        #    instead of normalized ATR (e.g., 0.0001)
        #    This fixes the 244x amplification bug.
        max_lots_allowed = allowed_risk_usd / (sl_dist_pips * self.config.pip_value_per_lot)

        # Apply maximum position size limit (emergency cap only)
        position_cap_applied = False
        if (
            self.config.max_position_lots is not None
            and max_lots_allowed > self.config.max_position_lots
        ):
            # Rate-limit warnings: only log every 1000 cap hits to prevent spam
            if self.position_cap_hits % 1000 == 0:
                logger.warning(
                    f"⚠️  {symbol}: Risk-based lots exceeds max cap "
                    f"({self.config.max_position_lots:.1f}). Cap hits: {self.position_cap_hits + 1}"
                )
            max_lots_allowed = self.config.max_position_lots
            position_cap_applied = True
            self.position_cap_hits += 1

        info["position_cap_applied"] = position_cap_applied

        # Position sizing mode: agent-based or fixed
        if self.config.position_sizing_mode == "fixed_lots":
            # Fixed lots mode: agent only determines direction, size is fixed
            # Use sign of target_pos_frac to determine direction, fixed_lots for size
            target_lots = self.config.fixed_lots
            target_pos_units = np.sign(target_pos_frac) * target_lots * self.config.lot_size
            info["sizing_mode"] = "fixed_lots"
            info["fixed_lots_used"] = float(target_lots)
        else:
            # Agent-based sizing mode (default): use risk management
            target_lots = max_lots_allowed * conviction  # Scale by conviction
            target_pos_units = np.sign(target_pos_frac) * target_lots * self.config.lot_size
            info["sizing_mode"] = "agent"

        # =================================================================
        # HARDENED SIZING CAPS (Multi-subcuenta pathology prevention)
        # =================================================================
        requested_lots = abs(target_lots)
        clamped_lots = requested_lots
        cap_reasons = []  # Collect all triggered cap reasons

        # CAP 1: max_lots_per_symbol
        if requested_lots > self.config.max_lots_per_symbol:
            clamped_lots = self.config.max_lots_per_symbol
            cap_reasons.append("max_lots_per_symbol")
            self.lot_cap_hits_by_symbol[symbol] += 1

        # CAP 2: max_notional_per_symbol_usd
        if self.config.max_notional_per_symbol_usd is not None:
            notional_usd = abs(clamped_lots * self.config.lot_size * price)
            if "JPY" in symbol.upper():
                notional_usd = abs(clamped_lots * self.config.lot_size)  # USD is base

            if notional_usd > self.config.max_notional_per_symbol_usd:
                # Clamp lots to meet notional limit
                max_lots_from_notional = (
                    self.config.max_notional_per_symbol_usd / self.config.lot_size
                )
                if "JPY" not in symbol.upper():
                    max_lots_from_notional /= price
                clamped_lots = min(clamped_lots, max_lots_from_notional)
                cap_reasons.append("max_notional_per_symbol_usd")
                self.notional_cap_hits_by_symbol[symbol] += 1

        # CAP 3: max_concentration_pct_per_symbol
        if self.equity > 0:
            notional_usd = abs(clamped_lots * self.config.lot_size * price)
            if "JPY" in symbol.upper():
                notional_usd = abs(clamped_lots * self.config.lot_size)

            concentration_pct = (notional_usd / self.equity) * 100
            max_concentration = self.config.max_concentration_pct_per_symbol

            if concentration_pct > max_concentration:
                # Clamp lots to meet concentration limit
                max_notional_allowed = self.equity * max_concentration / 100.0
                max_lots_from_concentration = max_notional_allowed / self.config.lot_size
                if "JPY" not in symbol.upper():
                    max_lots_from_concentration /= price
                clamped_lots = min(clamped_lots, max_lots_from_concentration)
                cap_reasons.append("max_concentration_pct_per_symbol")
                self.concentration_cap_hits_by_symbol[symbol] += 1

        # Apply clamped lots and log if any cap was hit
        if clamped_lots < requested_lots:
            target_lots = clamped_lots
            target_pos_units = np.sign(target_pos_frac) * target_lots * self.config.lot_size
            info["hardened_cap_applied"] = True
            info["hardened_cap_reasons"] = cap_reasons
            info["requested_lots"] = float(requested_lots)
            info["clamped_lots"] = float(clamped_lots)

            # Log to JSONL (only if caps were hit)
            self._log_sizing_event(
                symbol=symbol,
                step=self.current_step,
                reasons=cap_reasons,
                requested_lots=requested_lots,
                clamped_lots=clamped_lots,
                price=price,
            )
        else:
            info["hardened_cap_applied"] = False

        # Track max observed metrics
        self.max_lots_observed_by_symbol[symbol] = max(
            self.max_lots_observed_by_symbol[symbol], abs(target_lots)
        )

        notional_usd = abs(target_lots * self.config.lot_size * price)
        if "JPY" in symbol.upper():
            notional_usd = abs(target_lots * self.config.lot_size)
        self.max_notional_observed_by_symbol[symbol] = max(
            self.max_notional_observed_by_symbol[symbol], notional_usd
        )

        if self.equity > 0:
            concentration_pct = (notional_usd / self.equity) * 100
            self.max_concentration_observed_pct_by_symbol[symbol] = max(
                self.max_concentration_observed_pct_by_symbol[symbol], concentration_pct
            )
        # =================================================================
        # END HARDENED SIZING CAPS
        # =================================================================

        # 4) Determine pip size for price calculations (separate from pip_value)
        pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001

        # 5) Convert pip distances to price distances
        sl_dist_price = sl_dist_pips * pip_size
        tp_dist_price = tp_dist_pips * pip_size

        # 6) Calculate new position risk (USD)
        new_pos_risk_usd = self._units_to_usd_loss(target_pos_units, sl_dist_price, pip_size)

        # 7) Clamp by max total capital at risk
        # If disable_risk_scaling=True: Apply only emergency brake (risk > 150% equity)
        # If disable_risk_scaling=False: Apply normal risk management (80% equity)
        if self.config.disable_risk_scaling:
            # Emergency brake: only prevent catastrophic margin calls
            emergency_max_risk = (
                self.balance * 1.5
            )  # Allow up to 150% risk (trades can exceed equity)
            if new_pos_risk_usd > emergency_max_risk > 0:
                scaling = emergency_max_risk / new_pos_risk_usd if new_pos_risk_usd > 0 else 0.0
                target_pos_units *= scaling
                new_pos_risk_usd = self._units_to_usd_loss(
                    target_pos_units, sl_dist_price, pip_size
                )
                info["is_clamped"] = True
                info["clamp_reason"] = "emergency_brake_150pct"
                # P0 AUDIT instrumentation
                self.emergency_brake_trigger_count += 1
                if self.emergency_brake_first_trigger_step is None:
                    self.emergency_brake_first_trigger_step = self.current_step
                # P0 AUDIT instrumentation
                self.emergency_brake_trigger_count += 1
                if self.emergency_brake_first_trigger_step is None:
                    self.emergency_brake_first_trigger_step = self.current_step
        else:
            # Normal risk management
            max_total_risk_usd = self.balance * self.config.max_capital_at_risk_pct_total
            if new_pos_risk_usd > max_total_risk_usd >= 0:
                scaling = max_total_risk_usd / new_pos_risk_usd if new_pos_risk_usd > 0 else 0.0
                target_pos_units *= scaling
                new_pos_risk_usd = self._units_to_usd_loss(
                    target_pos_units, sl_dist_price, pip_size
                )
                info["is_clamped"] = True
                info["clamp_reason"] = "max_total_risk"

        # 8) Trade delta
        current_units = self.positions[symbol].units if symbol in self.positions else 0.0
        trade_units = target_pos_units - current_units

        # 9) LEVERAGE CAP (Hard cap before position opening)
        # Calculate leverage for the NEW POSITION (not trade delta)
        leverage_before_cap = 0.0
        leverage_after_cap = 0.0
        cap_hit = False
        cap_scale = 1.0

        if (
            self.config.max_leverage is not None
            and abs(target_pos_units) > self.config.min_position_change
        ):
            # Get current equity from metrics tracker
            equity_current = (
                self.metrics_tracker.equity_curve[-1]
                if len(self.metrics_tracker.equity_curve) > 0
                else self.config.initial_balance
            )
            equity_current = max(equity_current, 1e-9)  # Prevent division by zero

            # Calculate notional using forex_notional helper
            notional_usd = calculate_notional_usd(symbol, target_pos_units, price)
            leverage_before_cap = notional_usd / equity_current

            # Apply cap if needed
            if leverage_before_cap > self.config.max_leverage:
                cap_hit = True
                cap_scale = self.config.max_leverage / leverage_before_cap

                # Scale target position
                target_pos_units_scaled = int(np.trunc(target_pos_units * cap_scale))

                # If scaled units become zero, skip trade entirely
                if target_pos_units_scaled == 0:
                    logger.debug(
                        f"Leverage cap: {symbol} trade skipped (leverage {leverage_before_cap:.2f}x > {self.config.max_leverage}x, "
                        f"scaled units -> 0)"
                    )
                    info["leverage_cap_skipped"] = True
                    info["leverage_before_cap"] = float(leverage_before_cap)
                    info["cap_scale"] = float(cap_scale)
                    return 0.0, float(sl_price), float(tp_price), info

                # Update target and trade units
                target_pos_units = float(target_pos_units_scaled)
                trade_units = target_pos_units - current_units

                # Recalculate notional and leverage after scaling
                notional_usd_after = calculate_notional_usd(symbol, target_pos_units, price)
                leverage_after_cap = notional_usd_after / equity_current

                # Assert leverage is within cap (with small tolerance for rounding)
                assert leverage_after_cap <= self.config.max_leverage + 1e-6, (
                    f"Leverage cap assertion failed: {symbol} leverage_after={leverage_after_cap:.6f} > "
                    f"cap={self.config.max_leverage} + tolerance"
                )

                logger.debug(
                    f"Leverage cap applied: {symbol} leverage {leverage_before_cap:.2f}x -> {leverage_after_cap:.2f}x "
                    f"(scale={cap_scale:.4f}, units scaled)"
                )
            else:
                leverage_after_cap = leverage_before_cap

        # Store leverage info for logging
        info["leverage_before_cap"] = float(leverage_before_cap)
        info["leverage_after_cap"] = float(leverage_after_cap)
        info["cap_hit"] = cap_hit
        info["cap_scale"] = float(cap_scale)

        # 10) Executed trade risk
        executed_trade_risk_usd = self._units_to_usd_loss(trade_units, sl_dist_price, pip_size)
        info["executed_trade_risk_usd"] = float(executed_trade_risk_usd)
        info["new_pos_risk_usd"] = float(new_pos_risk_usd)

        # 11) Executed fraction
        executed_fraction = 0.0
        if allowed_risk_usd > 0:
            executed_fraction = np.clip(executed_trade_risk_usd / allowed_risk_usd, 0.0, 1.0)

        info["executed_action"] = np.array(
            [executed_fraction, sl_dist_atr, tp_dist_atr], dtype=np.float64
        )
        info["atr_pips"] = float(atr_pips)  # Log for debugging
        info["sl_dist_pips"] = float(sl_dist_pips)
        info["tp_dist_pips"] = float(tp_dist_pips)
        info["max_lots_allowed"] = float(max_lots_allowed)
        info["atr_floor_hits"] = self.atr_floor_hits
        info["position_cap_hits"] = self.position_cap_hits

        # NEW: Track position sizing details for audit
        current_units = self.positions[symbol].units if symbol in self.positions else 0.0
        units_desired_before_caps = trade_units + current_units  # What we wanted before any caps
        equity_at_entry = (
            self.metrics_tracker.equity_curve[-1]
            if len(self.metrics_tracker.equity_curve) > 0
            else self.config.initial_balance
        )

        info["units_desired"] = float(units_desired_before_caps)
        info["max_lots_hit"] = position_cap_applied
        info["max_leverage_hit"] = cap_hit
        info["equity_at_entry"] = float(equity_at_entry)

        # 12) SL/TP price levels
        is_long = target_pos_frac >= 0
        sl_price = price - sl_dist_price if is_long else price + sl_dist_price
        tp_price = price + tp_dist_price if is_long else price - tp_dist_price

        # FAIL-FAST: Verify fixed_lots mode produces exact lot sizes
        if self.config.position_sizing_mode == "fixed_lots" and self.config.disable_risk_scaling:
            # Calculate executed lots
            executed_lots = abs(target_pos_units) / self.config.lot_size
            expected_lots = self.config.fixed_lots

            # Allow 1e-9 tolerance for floating point precision
            if abs(executed_lots - expected_lots) > 1e-9:
                error_msg = (
                    f"FAIL-FAST: Fixed lots integrity violation!\n"
                    f"  Symbol: {symbol}\n"
                    f"  Step: {self.current_step}\n"
                    f"  Expected lots: {expected_lots:.9f}\n"
                    f"  Executed lots: {executed_lots:.9f}\n"
                    f"  Diff: {abs(executed_lots - expected_lots):.9e}\n"
                    f"  target_pos_units: {target_pos_units}\n"
                    f"  current_units: {current_units}\n"
                    f"  trade_units: {trade_units}\n"
                    f"  Equity: ${equity_at_entry:.2f}\n"
                    f"  Clamp reason: {info.get('clamp_reason', 'none')}\n"
                    f"  Cap hit: {cap_hit}\n"
                    f"  Max lots hit: {position_cap_applied}\n"
                )
                raise AssertionError(error_msg)

        return float(trade_units), float(sl_price), float(tp_price), info

    def _units_to_usd_loss(self, units: float, sl_dist_price: float, pip_size: float) -> float:
        """
        Convert units and stop distance to estimated USD loss.

        Args:
            units: Position size in base currency units
            sl_dist_price: Stop loss distance in price units
            pip_size: Pip size for this pair

        Returns:
            Estimated loss in USD if stop is hit
        """
        if pip_size <= 0:
            return 0.0

        lots = abs(units) / self.config.lot_size
        loss_pips = sl_dist_price / pip_size
        loss_usd = lots * loss_pips * self.config.pip_value_per_lot
        return float(loss_usd)

    def _update_total_risk(self) -> None:
        """Recalculate total capital at risk from all open positions."""
        total_risk = 0.0
        for symbol, pos in self.positions.items():
            pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
            sl_dist = abs(pos.avg_entry - pos.sl)
            risk_usd = self._units_to_usd_loss(pos.units, sl_dist, pip_size)
            total_risk += risk_usd

        self.total_capital_at_risk_usd = total_risk

    # ========================================================================
    # COSTS & EXECUTION
    # ========================================================================

    def _calculate_exit_slippage(
        self, symbol: str, exit_price: float, is_long: bool, lots: float, multiplier: float
    ) -> tuple[float, float]:
        """
        Sample exit slippage and return adjusted exit price + USD cost.

        Args:
            symbol: Asset symbol
            exit_price: Original exit price (SL/TP level or open[T])
            is_long: Whether the position being closed is long
            lots: Position size in lots
            multiplier: Multiplier vs base slippage (e.g. 1.5 for SL)

        Returns:
            (adjusted_exit_price, slippage_cost_usd)
        """
        if (
            not self.config.exit_slippage_enabled
            or self.config.slippage_pips_mean <= 0
        ):
            return exit_price, 0.0

        pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001

        # Sample slippage using same distribution as entry
        if self.config.slippage_half_normal:
            raw = abs(self.rng.normal(0.0, 1.0))
            slip_pips = self.config.slippage_pips_mean + raw * self.config.slippage_pips_std
        elif self.config.allow_positive_slippage:
            slip_pips = self.rng.normal(
                self.config.slippage_pips_mean, self.config.slippage_pips_std
            )
        else:
            slip_pips = abs(
                self.rng.normal(self.config.slippage_pips_mean, self.config.slippage_pips_std)
            )
        slip_pips = max(slip_pips, 0.0) * multiplier

        # Adverse direction: longs get worse (lower) exit; shorts get worse (higher) exit
        if is_long:
            adjusted_price = exit_price - slip_pips * pip_size
        else:
            adjusted_price = exit_price + slip_pips * pip_size

        slip_cost_usd = lots * slip_pips * self.config.pip_value_per_lot

        logger.debug(
            "EXIT_SLIPPAGE %s: %.4f pips (mult=%.1f) → price %.5f→%.5f ($%.4f) mode=%s",
            symbol, slip_pips, multiplier, exit_price, adjusted_price, slip_cost_usd,
            self.config.exit_slippage_mode,
        )

        mode = self.config.exit_slippage_mode
        if mode == "price_only":
            return adjusted_price, 0.0
        elif mode == "cost_only":
            return exit_price, slip_cost_usd  # Original price, cost recorded separately
        else:  # "both" (default / current behavior)
            return adjusted_price, slip_cost_usd

    def _calculate_costs(self, symbol: str, trade_units: float) -> tuple[float, float, float]:
        """
        Calculate transaction costs (commission + spread + slippage) in USD.

        Args:
            symbol: Asset symbol
            trade_units: Units to trade

        Returns:
            Tuple of (commission_usd, slippage_usd, total_cost_usd)
            Note: Spread cost is included in slippage_usd for simplicity
        """
        if abs(trade_units) < self.config.min_position_change:
            return 0.0, 0.0, 0.0

        lots_traded = abs(trade_units) / self.config.lot_size

        # Commission (per lot per side)
        commission_cost = lots_traded * self.config.commission_per_lot

        # Spread cost (treated as slippage for accounting)
        spread_cost = lots_traded * self.config.spread_pips * self.config.pip_value_per_lot

        # ── Slippage ──
        slippage_cost = spread_cost  # Start with spread

        # Option A: pips-first (preferred, correct for all pairs)
        if self.config.slippage_pips_mean > 0:
            pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
            if self.config.slippage_half_normal:
                # Half-normal: always adverse (abs)
                raw = abs(self.rng.normal(0.0, 1.0))
                slip_pips = self.config.slippage_pips_mean + raw * self.config.slippage_pips_std
            elif self.config.allow_positive_slippage:
                # Full normal: can improve or worsen fills
                slip_pips = self.rng.normal(
                    self.config.slippage_pips_mean, self.config.slippage_pips_std
                )
            else:
                # Normal but clamped to adverse only
                slip_pips = abs(
                    self.rng.normal(self.config.slippage_pips_mean, self.config.slippage_pips_std)
                )
            slip_pips = max(slip_pips, 0.0)  # Safety: never negative cost
            slip_cost_usd = lots_traded * slip_pips * self.config.pip_value_per_lot
            slippage_cost += slip_cost_usd

            if logger.level <= logging.DEBUG:
                close_price = self.ohlc_data[symbol]["close"][self.current_step]
                slip_abs_price = slip_pips * pip_size
                logger.debug(
                    "SLIPPAGE [pips-first] %s: mean=%.3f std=%.3f → slip=%.4f pips "
                    "(%.6f price, $%.4f) | lots=%.4f close=%.5f",
                    symbol,
                    self.config.slippage_pips_mean,
                    self.config.slippage_pips_std,
                    slip_pips,
                    slip_abs_price,
                    slip_cost_usd,
                    lots_traded,
                    close_price,
                )

        # Option B: legacy bps (DEPRECATED — currency bug for JPY pairs)
        elif self.config.slippage_bps > 0:
            trade_value = abs(trade_units) * self.ohlc_data[symbol]["close"][self.current_step]
            slippage_pct = self.rng.normal(
                self.config.slippage_bps / 10000, self.config.slippage_bps / 20000
            )
            bps_slip_usd = abs(trade_value * slippage_pct)
            slippage_cost += bps_slip_usd

            if logger.level <= logging.DEBUG:
                pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
                close_price = self.ohlc_data[symbol]["close"][self.current_step]
                slip_abs_price = close_price * abs(slippage_pct)
                slip_pips_equiv = slip_abs_price / pip_size
                logger.debug(
                    "SLIPPAGE [bps-DEPRECATED] %s: bps=%.1f → pct=%.6f → "
                    "slip_abs=%.6f (%.2f pips equiv) $%.4f | "
                    "trade_value=%.2f (QUOTE CCY!) lots=%.4f close=%.5f",
                    symbol,
                    self.config.slippage_bps,
                    slippage_pct,
                    slip_abs_price,
                    slip_pips_equiv,
                    bps_slip_usd,
                    trade_value,
                    lots_traded,
                    close_price,
                )

        total_cost = commission_cost + slippage_cost
        return float(commission_cost), float(slippage_cost), float(total_cost)

    def _execute_trade(
        self,
        symbol: str,
        trade_units: float,
        price: float,
        sl_price: float,
        tp_price: float,
        leverage_info: dict | None = None,
    ) -> float:
        """
        Execute trade: update or create position, handle reversals.

        Args:
            symbol: Asset symbol
            trade_units: Units to trade (delta)
            price: Execution price
            sl_price: New stop loss level
            tp_price: New take profit level
            leverage_info: Dict with leverage tracking (notional_usd, leverage_before_cap, etc.)

        Returns:
            realized_pnl_usd: PnL from closing portion (if any)
        """
        if leverage_info is None:
            leverage_info = {
                "notional_usd": 0.0,
                "leverage_before_cap": 0.0,
                "leverage_after_cap": 0.0,
                "cap_hit": False,
                "cap_scale": 1.0,
            }

        if abs(trade_units) < self.config.min_position_change:
            return 0.0

        realized_pnl_usd = 0.0
        current_units = self.positions[symbol].units if symbol in self.positions else 0.0

        # Reversal: close existing position and open new one
        if current_units != 0 and np.sign(trade_units) != np.sign(current_units):
            pos = self.positions[symbol]
            is_long_closing = pos.is_long

            # Exit slippage: compute adjusted exit price (don't mutate 'price' — reused for new entry)
            exit_lots = abs(pos.units) / self.config.lot_size
            rev_exit_price, rev_exit_slip_usd = self._calculate_exit_slippage(
                symbol, price, is_long_closing, exit_lots,
                self.config.exit_slippage_mult_reverse,
            )

            pnl_usd = self._calculate_pnl_usd(symbol, pos.units, pos.avg_entry, rev_exit_price)
            realized_pnl_usd = pnl_usd

            # Voluntary close MAE/MFE: use pre-bar values (don't include exit bar high/low)
            v_mae, v_mfe, v_mae_usd, v_mfe_usd = self._finalize_mae_mfe_voluntary(symbol, pos)

            # Get accumulated costs and leverage info for this position
            commission_total = 0.0
            slippage_total = 0.0
            pos_leverage_info = {}
            if symbol in self.position_costs:
                if len(self.position_costs[symbol]) == 3:
                    commission_total, slippage_total, pos_leverage_info = self.position_costs[
                        symbol
                    ]
                else:  # Backward compatibility
                    commission_total, slippage_total = self.position_costs[symbol]
                    pos_leverage_info = {}

            # Add exit slippage to accumulated slippage total
            slippage_total += rev_exit_slip_usd

            # Exit commission: charge $commission_per_lot on the closing side
            exit_commission = exit_lots * self.config.commission_per_lot
            commission_total += exit_commission
            # Deduct exit commission from realized PnL so balance is correctly reduced
            realized_pnl_usd -= exit_commission

            # Record trade with actual costs and leverage
            _tid = self._next_trade_id
            self._next_trade_id += 1
            trade = ProductionTrade(
                trade_id=_tid,
                symbol=symbol,
                entry_time=pos.entry_time,
                exit_time=self.current_step,
                entry_price=pos.avg_entry,
                exit_price=rev_exit_price,
                units=pos.units,
                pnl_usd=pnl_usd,
                commission_usd=commission_total,
                slippage_usd=slippage_total,
                initial_sl_pips=pos.initial_sl_pips,
                initial_tp_pips=pos.initial_tp_pips,
                initial_tp_sl_ratio=pos.initial_tp_sl_ratio,
                notional_usd=pos_leverage_info.get("notional_usd", 0.0),
                leverage_before_cap=pos_leverage_info.get("leverage_before_cap", 0.0),
                leverage_after_cap=pos_leverage_info.get("leverage_after_cap", 0.0),
                cap_hit=pos_leverage_info.get("cap_hit", False),
                cap_scale=pos_leverage_info.get("cap_scale", 1.0),
                # Position sizing details
                units_desired=pos_leverage_info.get("units_desired", pos.units),
                max_lots_hit=pos_leverage_info.get("max_lots_hit", False),
                max_leverage_hit=pos_leverage_info.get("max_leverage_hit", False),
                equity_at_entry=pos_leverage_info.get("equity_at_entry", 0.0),
                # MAE/MFE
                mae_price=v_mae,
                mfe_price=v_mfe,
                mae_usd=v_mae_usd,
                mfe_usd=v_mfe_usd,
                # Exit reason: "reverse" only if a new opposite position will open
                exit_reason=(
                    "reverse"
                    if abs(trade_units + current_units) >= self.config.min_position_change
                    else "agent_close"
                ),
            )
            self.trade_history.append(trade)
            self.metrics_tracker.record_trade(trade)  # Track in metrics

            # Close position and clear costs
            del self.positions[symbol]
            if symbol in self.position_costs:
                del self.position_costs[symbol]
            # Activate cooldown after close (reversal may re-open, but cooldown only
            # matters if the reversal side ends up empty)
            if self.config.cooldown_bars > 0:
                self.cooldown_remaining[symbol] = self.config.cooldown_bars

            # Remaining units to apply
            remaining_units = trade_units + current_units
            total_units = remaining_units

            # Open new position if remaining
            if abs(total_units) >= self.config.min_position_change:
                # Calculate pip_size and initial metrics
                pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
                initial_sl_pips = max(abs((price - sl_price) / pip_size), 1e-9)
                initial_tp_pips = max(abs((tp_price - price) / pip_size), 1e-9)
                initial_tp_sl_ratio = initial_tp_pips / initial_sl_pips

                self.positions[symbol] = ProductionPosition(
                    symbol=symbol,
                    units=total_units,
                    avg_entry=price,
                    sl=sl_price,
                    tp=tp_price,
                    entry_time=self.current_step,
                    initial_sl_pips=initial_sl_pips,
                    initial_tp_pips=initial_tp_pips,
                    initial_tp_sl_ratio=initial_tp_sl_ratio,
                    moved_to_break_even=False,
                    high_watermark_price=price,
                    low_watermark_price=price,
                    pos_mae_price=price,
                    pos_mfe_price=price,
                    pre_bar_mae=price,
                    pre_bar_mfe=price,
                )

            self._update_total_risk()
            return realized_pnl_usd

        # Normal case: open, increase, or reduce position
        total_units = current_units + trade_units

        if abs(current_units) < self.config.min_position_change:
            # Opening new position
            if abs(total_units) >= self.config.min_position_change:
                # Calculate pip_size and initial metrics
                pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
                initial_sl_pips = max(abs((price - sl_price) / pip_size), 1e-9)
                initial_tp_pips = max(abs((tp_price - price) / pip_size), 1e-9)
                initial_tp_sl_ratio = initial_tp_pips / initial_sl_pips

                self.positions[symbol] = ProductionPosition(
                    symbol=symbol,
                    units=total_units,
                    avg_entry=price,
                    sl=sl_price,
                    tp=tp_price,
                    entry_time=self.current_step,
                    initial_sl_pips=initial_sl_pips,
                    initial_tp_pips=initial_tp_pips,
                    initial_tp_sl_ratio=initial_tp_sl_ratio,
                    moved_to_break_even=False,
                    high_watermark_price=price,
                    low_watermark_price=price,
                    pos_mae_price=price,
                    pos_mfe_price=price,
                    pre_bar_mae=price,
                    pre_bar_mfe=price,
                )
        elif abs(total_units) > abs(current_units):
            # Increasing position: weighted average entry
            pos = self.positions[symbol]
            old_value = current_units * pos.avg_entry
            new_value = trade_units * price
            denom = total_units if abs(total_units) > 1e-9 else 1e-9
            pos.avg_entry = (old_value + new_value) / denom
            pos.units = total_units
            pos.sl = sl_price
            pos.tp = tp_price
            # Note: initial_sl_pips, moved_to_break_even, and watermarks are NOT recalculated
            # They remain from the original position opening
        elif abs(total_units) >= self.config.min_position_change:
            # Reducing position: keep entry, update units
            pos = self.positions[symbol]
            pos.units = total_units
            pos.sl = sl_price
            pos.tp = tp_price
        # Close position completely
        elif symbol in self.positions:
            pos = self.positions[symbol]
            is_long_closing = pos.is_long

            # Exit slippage: compute adjusted exit price
            exit_lots = abs(pos.units) / self.config.lot_size
            agent_exit_price, agent_exit_slip_usd = self._calculate_exit_slippage(
                symbol, price, is_long_closing, exit_lots,
                self.config.exit_slippage_mult_agent_close,
            )

            pnl_usd = self._calculate_pnl_usd(symbol, pos.units, pos.avg_entry, agent_exit_price)
            realized_pnl_usd = pnl_usd

            # Voluntary close MAE/MFE: use pre-bar values (don't include exit bar high/low)
            v_mae, v_mfe, v_mae_usd, v_mfe_usd = self._finalize_mae_mfe_voluntary(symbol, pos)

            # Get accumulated costs and leverage info for this position
            commission_total = 0.0
            slippage_total = 0.0
            pos_leverage_info = {}
            if symbol in self.position_costs:
                if len(self.position_costs[symbol]) == 3:
                    commission_total, slippage_total, pos_leverage_info = self.position_costs[
                        symbol
                    ]
                else:  # Backward compatibility
                    commission_total, slippage_total = self.position_costs[symbol]
                    pos_leverage_info = {}

            # Add exit slippage to accumulated slippage total
            slippage_total += agent_exit_slip_usd

            # Exit commission: charge $commission_per_lot on the closing side
            exit_commission = exit_lots * self.config.commission_per_lot
            commission_total += exit_commission
            # Deduct exit commission from realized PnL so balance is correctly reduced
            realized_pnl_usd -= exit_commission

            _tid = self._next_trade_id
            self._next_trade_id += 1
            trade = ProductionTrade(
                trade_id=_tid,
                symbol=symbol,
                entry_time=pos.entry_time,
                exit_time=self.current_step,
                entry_price=pos.avg_entry,
                exit_price=agent_exit_price,
                units=pos.units,
                pnl_usd=pnl_usd,
                commission_usd=commission_total,
                slippage_usd=slippage_total,
                initial_sl_pips=pos.initial_sl_pips,
                initial_tp_pips=pos.initial_tp_pips,
                initial_tp_sl_ratio=pos.initial_tp_sl_ratio,
                notional_usd=pos_leverage_info.get("notional_usd", 0.0),
                leverage_before_cap=pos_leverage_info.get("leverage_before_cap", 0.0),
                leverage_after_cap=pos_leverage_info.get("leverage_after_cap", 0.0),
                cap_hit=pos_leverage_info.get("cap_hit", False),
                cap_scale=pos_leverage_info.get("cap_scale", 1.0),
                # Position sizing details
                units_desired=pos_leverage_info.get("units_desired", pos.units),
                max_lots_hit=pos_leverage_info.get("max_lots_hit", False),
                max_leverage_hit=pos_leverage_info.get("max_leverage_hit", False),
                equity_at_entry=pos_leverage_info.get("equity_at_entry", 0.0),
                # MAE/MFE
                mae_price=v_mae,
                mfe_price=v_mfe,
                mae_usd=v_mae_usd,
                mfe_usd=v_mfe_usd,
                # Exit reason
                exit_reason="agent_close",
            )
            self.trade_history.append(trade)
            self.metrics_tracker.record_trade(trade)  # Track in metrics
            del self.positions[symbol]
            if symbol in self.position_costs:
                del self.position_costs[symbol]
            # Activate cooldown after agent_close
            if self.config.cooldown_bars > 0:
                self.cooldown_remaining[symbol] = self.config.cooldown_bars

        self._update_total_risk()
        return realized_pnl_usd

    def _calculate_pnl_usd(
        self, symbol: str, units: float, entry_price: float, exit_price: float
    ) -> float:
        """
        Calculate PnL in USD for a position.

        Args:
            symbol: Asset symbol
            units: Position size (signed)
            entry_price: Entry price
            exit_price: Exit price

        Returns:
            PnL in USD
        """
        pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
        price_diff = exit_price - entry_price  # Signed
        lots = abs(units) / self.config.lot_size
        price_diff_pips = price_diff / pip_size if pip_size > 0 else 0.0

        # Adjust sign based on position direction
        if units < 0:  # Short position
            price_diff_pips = -price_diff_pips

        pnl_usd = lots * price_diff_pips * self.config.pip_value_per_lot
        return float(pnl_usd)

    # ========================================================================
    # EQUITY & REWARD
    # ========================================================================

    def _update_equity(self) -> None:
        """Update equity by calculating unrealized PnL for all positions."""
        unrealized_pnl_usd = 0.0
        for symbol, pos in self.positions.items():
            current_price = float(self.ohlc_data[symbol]["close"][self.current_step])
            pnl = self._calculate_pnl_usd(symbol, pos.units, pos.avg_entry, current_price)
            pos.unrealized_pnl_usd = pnl
            unrealized_pnl_usd += pnl

        self.equity = self.balance + unrealized_pnl_usd

    def _calculate_reward(
        self, pnl_usd: float, costs_usd: float, info: dict[str, Any], trades_executed: int = 0,
        *, turnover_lots: float = 0.0, flips: int = 0,
    ) -> float:
        """
        Calculate step reward based on risk-adjusted returns.

        Reward formula:
            reward = (pnl_usd - costs_usd) / balance
                     - clamp_penalty (if clamped)
                     - risk_penalty × (capital_at_risk / balance)
                     - action_penalty × trades_executed
                     - lambda_turnover × turnover_lots
                     - lambda_flip × flips

        Key principles:
        1. Reward is based ONLY on net PnL (profit minus costs)
        2. Costs are ALREADY DEDUCTED from PnL (natural penalty for overtrading)
        3. No trade incentive (any positive value causes overtrading)
        4. Risk penalty discourages excessive position sizes
        5. Action penalty directly penalizes each trade (discourage overtrading)

        Args:
            pnl_usd: Realized PnL in USD (before costs)
            costs_usd: Transaction costs in USD (commission + spread + slippage)
            info: Step info dict (for logging reward components)
            trades_executed: Number of trades executed in this step

        Returns:
            Scalar reward normalized by balance
        """
        denom = max(self.balance, self.config.min_balance_eps)

        # Base reward: net PnL normalized by balance
        # Costs are subtracted here - this is the ONLY penalty for trading
        reward = (pnl_usd - costs_usd) / denom

        # Clamping penalty (if any asset was clamped to max position)
        is_clamped = any(self.last_clamped_flags.values())
        clamp_penalty_value = 0.0
        if is_clamped:
            clamp_penalty_value = self.config.lambda_clamp_penalty
            reward -= clamp_penalty_value

        # Risk penalty (proportional to capital at risk)
        risk_penalty = 0.0
        if denom > 0 and self.total_capital_at_risk_usd > 0:
            risk_penalty = self.config.lambda_risk_penalty * (
                self.total_capital_at_risk_usd / denom
            )
            reward -= risk_penalty

        # Action penalty (penalize each trade to discourage overtrading)
        action_penalty_value = 0.0
        if trades_executed > 0 and self.action_penalty > 0.0:
            action_penalty_value = self.action_penalty * trades_executed
            reward -= action_penalty_value

            # Debug log when penalty is applied
            if trades_executed > 0:
                logger.debug(
                    "Action penalty applied: %d trades × %.4f = -%.4f",
                    trades_executed,
                    self.action_penalty,
                    action_penalty_value,
                )

        # IMPORTANT: NO trade incentive
        # Trade incentive (lambda_trade_incentive) should ALWAYS be 0.0
        # Any positive value creates perverse incentives to overtrade
        # which destroys profitability through transaction costs

        # ── Turnover penalty ─────────────────────────────────────────────
        turnover_penalty_value = 0.0
        if turnover_lots > 0.0 and self.config.lambda_turnover > 0.0:
            turnover_penalty_value = self.config.lambda_turnover * turnover_lots
            reward -= turnover_penalty_value

        # ── Flip penalty ─────────────────────────────────────────────────
        flip_penalty_value = 0.0
        if flips > 0 and self.config.lambda_flip > 0.0:
            flip_penalty_value = self.config.lambda_flip * flips
            reward -= flip_penalty_value

        # Apply asymmetric loss penalty (penalize negative rewards more heavily)
        loss_penalty_applied = 0.0
        if reward < 0.0 and self.loss_penalty_factor > 1.0:
            # Save original reward for logging
            original_reward = reward
            reward = reward * self.loss_penalty_factor
            loss_penalty_applied = reward - original_reward  # Will be negative

            logger.debug(
                "Loss penalty applied: original_reward=%.6f, factor=%.3f, new_reward=%.6f",
                original_reward,
                self.loss_penalty_factor,
                reward,
            )

        # Log reward components for analysis
        info["reward_components"] = {
            "pnl_gross": float(pnl_usd / denom),  # PnL before costs
            "costs_normalized": float(costs_usd / denom),  # Costs normalized
            "pnl_net": float((pnl_usd - costs_usd) / denom),  # Net PnL (what we reward)
            "clamp_penalty": float(clamp_penalty_value),
            "risk_penalty": float(risk_penalty),
            "action_penalty": float(action_penalty_value),
            "turnover_penalty": float(turnover_penalty_value),
            "flip_penalty": float(flip_penalty_value),
            "loss_penalty": float(loss_penalty_applied),
            "total_reward": float(reward),
            "trades_executed": int(trades_executed),
            "turnover_lots": float(turnover_lots),
            "flips": int(flips),
        }

        return float(reward)

    # ========================================================================
    # OBSERVATION
    # ========================================================================

    def _get_observation(self) -> NDArray[np.float64]:
        """
        Build observation vector.

        Structure:
            [market_features, agent_state_per_asset, latent_states?, forecasts?]

        Agent state per asset (8 features):
            - pos_frac: Position notional / balance
            - sl_norm_atr: |entry - SL| / ATR
            - tp_norm_atr: |TP - entry| / ATR
            - executed_fraction: Last executed action[0]
            - sl_ATR: Last executed action[1]
            - tp_ATR: Last executed action[2]
            - last_clamped_flag: 1 if clamped, 0 otherwise
            - position_open_flag: 1 if position open, 0 otherwise

        Returns:
            Observation vector (1D array)
        """
        current_idx = min(self.current_step, self.num_steps - 1)

        # CAUSAL LAG: Use features from the PREVIOUS bar to avoid look-ahead bias.
        # Features at row T are computed from bar T's OHLC (incl. close[T], high[T],
        # low[T]), but the agent trades at open[T]. At open[T] we only know data
        # through bar T-1, so we use feature_data[T-1].
        # After step() increments current_step to T+1, the next obs uses
        # feature_data[T] — which is causally correct since close[T] is known
        # at open[T+1].
        # Time/session features (timestamp-based, no look-ahead) are also shifted
        # by 1 minute for simplicity — negligible impact on 1-min bars.
        feature_lag_idx = max(current_idx - 1, 0)

        # Market features (lagged to avoid look-ahead)
        market_features = self.feature_data[feature_lag_idx]

        # Agent state per asset
        agent_states = []
        for symbol in self.symbols:
            # ATR also lagged: ATR[T] uses high[T]/low[T], unknown at open[T]
            current_atr = float(self.atr_data[symbol][feature_lag_idx])
            pos = self.positions.get(symbol)

            if pos:
                pos_frac = (pos.units * pos.avg_entry) / self.balance if self.balance > 0 else 0.0
                sl_norm_atr = abs(pos.avg_entry - pos.sl) / current_atr if current_atr > 0 else 0.0
                tp_norm_atr = abs(pos.tp - pos.avg_entry) / current_atr if current_atr > 0 else 0.0
                position_open_flag = 1.0
            else:
                pos_frac = 0.0
                sl_norm_atr = 0.0
                tp_norm_atr = 0.0
                position_open_flag = 0.0

            executed_action = self.last_executed_actions[symbol]
            clamped_flag = self.last_clamped_flags[symbol]

            agent_state = np.array(
                [
                    pos_frac,
                    sl_norm_atr,
                    tp_norm_atr,
                    executed_action[0],  # executed_fraction
                    executed_action[1],  # sl_ATR
                    executed_action[2],  # tp_ATR
                    clamped_flag,
                    position_open_flag,
                ],
                dtype=np.float64,
            )
            agent_states.append(agent_state)

        agent_states_concat = np.concatenate(agent_states)

        # Optional: latent states (also lagged for causal consistency)
        latent_vec = (
            self.latent_states[feature_lag_idx]
            if self.latent_states is not None
            else np.array([], dtype=np.float64)
        )

        # Optional: forecasts (also lagged for causal consistency)
        forecast_vec = np.array([], dtype=np.float64)
        if self.forecasts is not None:
            if self.forecasts.ndim == 1:
                forecast_vec = np.array([self.forecasts[feature_lag_idx]], dtype=np.float64)
            else:
                forecast_vec = self.forecasts[feature_lag_idx]

        # Concatenate all components
        obs = np.concatenate([market_features, agent_states_concat, latent_vec, forecast_vec])
        return obs.astype(np.float64)

    def _get_info(self) -> dict[str, Any]:
        """Get diagnostic information with all performance metrics."""
        # Basic info
        basic_info = {
            "step": self.current_step,
            "balance": float(self.balance),
            "equity": float(self.equity),
            "peak_equity": float(self.peak_equity),
            "total_capital_at_risk_usd": float(self.total_capital_at_risk_usd),
            "num_positions": len(self.positions),
            "num_trades": len(self.trade_history),
            "positions": {symbol: pos.units for symbol, pos in self.positions.items()},
            # P0 AUDIT: Emergency brake instrumentation
            "emergency_brake_trigger_count": self.emergency_brake_trigger_count,
            "emergency_brake_first_trigger_step": self.emergency_brake_first_trigger_step,
            "emergency_brake_total_steps_after": self.emergency_brake_total_steps_after,
            "emergency_brake_trades_after": self.emergency_brake_trades_after,
        }

        # Compute all 20 performance metrics (expensive, so do it only every 100 steps)
        # Use relative step check for episode end (current_step is absolute in data)
        is_episode_end = self.current_step >= self.start_step + self.config.episode_length
        if self.current_step % 100 == 0 or is_episode_end:
            all_metrics = self.metrics_tracker.compute_all_metrics()
            # Merge basic info with metrics
            return {**basic_info, **all_metrics}
        # Return only basic info for intermediate steps
        return basic_info

    # ========================================================================
    # GYM API - RENDER & CLOSE
    # ========================================================================

    def render(self, mode: Literal["human", "rgb_array"] = "human") -> Any | None:
        """
        Render environment state.

        Args:
            mode: Render mode ("human" or "rgb_array")

        Returns:
            None for human mode, RGB array for rgb_array mode
        """
        if mode == "human":
            print(
                f"Step: {self.current_step}, Balance: ${self.balance:.2f}, Equity: ${self.equity:.2f}"
            )
            print(f"Positions: {len(self.positions)}, Trades: {len(self.trade_history)}")
            print(f"Capital at Risk: ${self.total_capital_at_risk_usd:.2f}")
            for symbol, pos in self.positions.items():
                print(
                    f"  {symbol}: {pos.units:.2f} units @ {pos.avg_entry:.5f} (PnL: ${pos.unrealized_pnl_usd:.2f})"
                )
            return None
        return None

    def export_history(
        self,
        output_dir: str | Path,
        episode_id: str | None = None,
        format: Literal["parquet", "feather"] = "parquet",
        compress: bool = True,
    ) -> dict[str, Path | None]:
        """
        Export episode history for auditing, reproducibility, and analysis.

        Exports:
            - trades.parquet: All completed trades with metadata
            - equity.parquet: Equity curve (step, balance, equity)
            - metadata.json: Episode metadata (seed, config, timestamp, git commit)
            - config.json: Full environment configuration
            - metrics.json: All 20 performance metrics

        Args:
            output_dir: Directory to save export files
            episode_id: Optional episode identifier (auto-generated if None)
            format: Export format ("parquet" or "feather")
            compress: Whether to compress parquet files (snappy compression)

        Returns:
            Dictionary mapping file type to Path:
                {'trades': Path, 'equity': Path, 'metadata': Path, 'config': Path, 'metrics': Path}

        Example:
            >>> env.export_history(
            ...     output_dir="./results/episode_001",
            ...     episode_id="sac_train_ep001",
            ...     format="parquet",
            ...     compress=True
            ... )
            {'trades': Path('...'), 'equity': Path('...'), ...}
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        exported_files: dict[str, Path | None] = {}

        # 1. Export trades history
        if self.trade_history:
            trades_df = pd.DataFrame([asdict(t) for t in self.trade_history])

            if format == "parquet":
                trades_file = output_path / "trades.parquet"
                compression = "snappy" if compress else None
                trades_df.to_parquet(trades_file, compression=compression, index=False)
            else:
                trades_file = output_path / "trades.feather"
                trades_df.to_feather(trades_file)

            exported_files["trades"] = trades_file
            logger.info(f"Exported {len(self.trade_history)} trades to {trades_file}")
        else:
            exported_files["trades"] = None
            logger.info("No trades to export")

        # 2. Export equity curve
        if self.metrics_tracker.equity_curve:
            equity_df = pd.DataFrame(
                {
                    "step": self.metrics_tracker.timestamps,
                    "balance": self.metrics_tracker.balance_curve,
                    "equity": self.metrics_tracker.equity_curve,
                }
            )

            if format == "parquet":
                equity_file = output_path / "equity.parquet"
                compression = "snappy" if compress else None
                equity_df.to_parquet(equity_file, compression=compression, index=False)
            else:
                equity_file = output_path / "equity.feather"
                equity_df.to_feather(equity_file)

            exported_files["equity"] = equity_file
            logger.info(f"Exported equity curve ({len(equity_df)} points) to {equity_file}")
        else:
            exported_files["equity"] = None

        # 3. Export episode metadata
        metadata = EpisodeMetadata(
            episode_id=episode_id or f"ep_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            seed=self.last_seed,
            timestamp=datetime.now().isoformat(),
            git_commit=_get_git_commit(),
            config=self.config,
            python_version=sys.version,
            environment_version="4.1.0",
            num_steps=len(self.metrics_tracker.equity_curve),
            num_trades=len(self.trade_history),
            final_balance=self.balance,
            final_equity=self.equity,
        )

        metadata_file = output_path / "metadata.json"
        with open(metadata_file, "w", encoding="utf-8") as f:
            # Convert dataclass to dict, then serialize
            metadata_dict = asdict(metadata)
            # Convert config to dict as well
            metadata_dict["config"] = asdict(metadata.config)
            json.dump(metadata_dict, f, indent=2, default=str)

        exported_files["metadata"] = metadata_file
        logger.info(f"Exported metadata to {metadata_file}")

        # 4. Export configuration
        config_file = output_path / "config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(asdict(self.config), f, indent=2, default=str)

        exported_files["config"] = config_file
        logger.info(f"Exported config to {config_file}")

        # 5. Export computed metrics
        metrics = self.metrics_tracker.compute_all_metrics()

        # Convert non-serializable types (complex, nan, inf) to serializable
        # and round floats to 2 decimal places for cleaner output
        def sanitize_metrics(obj):
            if isinstance(obj, dict):
                return {k: sanitize_metrics(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [sanitize_metrics(item) for item in obj]
            if isinstance(obj, complex):
                return round(float(obj.real), 2)
            if isinstance(obj, float):
                if np.isnan(obj) or np.isinf(obj):
                    return None
                return round(obj, 2)
            if isinstance(obj, np.floating):
                val = float(obj)
                if np.isnan(val) or np.isinf(val):
                    return None
                return round(val, 2)
            return obj

        metrics = sanitize_metrics(metrics)
        metrics_file = output_path / "metrics.json"
        with open(metrics_file, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)

        exported_files["metrics"] = metrics_file
        logger.info(f"Exported metrics to {metrics_file}")

        logger.info(f"✅ Episode history exported successfully to {output_path}")
        return exported_files

    def close(self) -> None:
        """Clean up environment resources."""
        logger.debug("Environment closed.")


# ============================================================================
# SMOKE TEST
# ============================================================================


if __name__ == "__main__":
    """
    Minimal smoke test: create environment with synthetic data and run 10 steps.
    """
    print("=" * 60)
    print("AtlasFX Trading Environment v3 - Smoke Test")
    print("=" * 60)

    # Generate synthetic data
    num_steps = 1500
    symbol = "EURUSD"

    np.random.seed(42)
    prices = 1.1000 + np.cumsum(np.random.randn(num_steps) * 0.0001)
    atr_values = np.abs(np.random.randn(num_steps) * 0.0002 + 0.0010)

    df = pd.DataFrame(
        {
            f"{symbol}-pair | open": prices + np.random.randn(num_steps) * 0.00005,
            f"{symbol}-pair | high": prices + np.abs(np.random.randn(num_steps)) * 0.0001,
            f"{symbol}-pair | low": prices - np.abs(np.random.randn(num_steps)) * 0.0001,
            f"{symbol}-pair | close": prices,
            f"[Feature] {symbol} | atr_14_price": atr_values,
            "[Feature] dummy_feature": np.random.randn(num_steps),
        }
    )

    price_cols = {
        symbol: {
            "open": f"{symbol}-pair | open",
            "high": f"{symbol}-pair | high",
            "low": f"{symbol}-pair | low",
            "close": f"{symbol}-pair | close",
        }
    }

    config = ProductionTradingConfig(
        initial_balance=10_000.0,
        max_risk_per_trade_pct=0.02,
        episode_length=100,
    )

    env = ProductionTradingEnv(
        data=df,
        symbols=[symbol],
        price_cols=price_cols,
        config=config,
    )

    # Reset environment
    obs, info = env.reset(seed=123)
    print("\n✅ Reset successful:")
    print(f"   Observation shape: {obs.shape}")
    print(f"   State dim: {env.state_dim}")
    print(f"   Action dim: {env.action_dim}")
    print(f"   Info: {info}")

    # Run 10 steps with random actions
    print("\n🔄 Running 10 random steps...")
    for i in range(10):
        # Random action: [target_pos_frac, sl_dist_ATR, tp_dist_ATR]
        action = np.array(
            [
                np.random.uniform(-0.5, 0.5),  # target_pos_frac
                np.random.uniform(1.5, 3.0),  # sl_dist_ATR
                np.random.uniform(2.0, 4.0),  # tp_dist_ATR
            ]
        )

        obs, reward, terminated, truncated, info = env.step(action)

        print(f"\nStep {i + 1}:")
        print(f"  Action: {action}")
        print(f"  Reward: {reward:.6f}")
        print(f"  Balance: ${info['balance']:.2f}")
        print(f"  Equity: ${info['equity']:.2f}")
        print(f"  Positions: {info['num_positions']}")
        print(f"  Risk: ${info['total_capital_at_risk_usd']:.2f}")

        if terminated or truncated:
            print(f"\n⚠️ Episode ended at step {i + 1}")
            break

    print("\n✅ Smoke test completed successfully!")
    print(f"   Total trades: {len(env.trade_history)}")
    print(f"   Final balance: ${env.balance:.2f}")
    print(f"   Final equity: ${env.equity:.2f}")
    print("=" * 60)
