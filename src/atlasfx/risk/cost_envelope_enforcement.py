"""
Cost Envelope Runtime Integration for Trading Environments

Provides drop-in enforcement of production cost limits during trading.

Integration Points:
- MultiPairPortfolioEnv.step() - Portfolio-level enforcement
- ProductionTradingEnv.step() - Per-symbol enforcement (future)

Usage:
    env = MultiPairPortfolioEnv(
        config=config,
        symbols=symbols,
        enable_cost_envelope=True,  # Enable runtime guardrail
        cost_envelope_config_path="config/production_cost_envelope.json",
    )

When enabled:
- Checks observed costs (commission, spread) before each trade
- If breach detected:
  - NO_TRADE: Forces action to HOLD (0.0), prevents new positions
  - FLATTEN: Closes all positions (action = 0.0, close existing)
- Logs all breaches to reports/runtime_cost_monitoring/cost_breaches.jsonl
"""

from datetime import datetime
from pathlib import Path

from atlasfx.risk.cost_envelope import (
    CostCheckResult,
    ObservedCosts,
    check_cost_envelope,
    load_cost_envelope,
    log_cost_breach,
)
from atlasfx.risk.cost_envelope_global_pause import GlobalPauseFallback
from atlasfx.utils.logging import get_logger


logger = get_logger(__name__)


class CostEnvelopeEnforcer:
    """Runtime cost envelope enforcement for trading environments.

    Wraps cost checking logic and breach actions for easy integration.

    Supports two enforcement modes:
    - per_symbol: Only zeroes actions for breaching symbols (preserves others)
    - global: Zeroes all actions when any symbol breaches (legacy behavior)
    """

    def __init__(
        self,
        config_path: Path | str = "config/production_cost_envelope.json",
        log_path: Path | str = "reports/runtime_cost_monitoring/cost_breaches.jsonl",
        enabled: bool = True,
        enforcement_mode: str = "per_symbol",  # "per_symbol" or "global"
        fallback_mode: str = "none",  # "none" or "global_pause"
        pause_window_steps: int = 500,
        pause_trigger_breach_rate: float = 0.08,
        pause_trigger_multi_breach: int = 2,
        pause_cooldown_steps: int = 1000,
    ):
        """Initialize cost envelope enforcer.

        Args:
            config_path: Path to production cost envelope config
            log_path: Path to breach log file (JSONL)
            enabled: Whether enforcement is active (False = monitoring only)
            enforcement_mode: "per_symbol" (default) or "global" (legacy)
            fallback_mode: "none" (default) or "global_pause"
            pause_window_steps: Rolling window for breach rate (global_pause)
            pause_trigger_breach_rate: Threshold for breach rate trigger (0.0-1.0)
            pause_trigger_multi_breach: Min symbols breaching simultaneously
            pause_cooldown_steps: Steps to pause after trigger
        """
        self.enabled = enabled
        self.log_path = Path(log_path)
        self.enforcement_mode = enforcement_mode
        self.fallback_mode = fallback_mode

        # Initialize global pause fallback if requested
        self.global_pause = None
        if fallback_mode == "global_pause":
            self.global_pause = GlobalPauseFallback(
                window_steps=pause_window_steps,
                trigger_breach_rate=pause_trigger_breach_rate,
                trigger_multi_breach=pause_trigger_multi_breach,
                cooldown_steps=pause_cooldown_steps,
                enabled=True,
            )
            logger.info(
                f"CostEnvelopeEnforcer: Global Pause Fallback enabled\n"
                f"  Window: {pause_window_steps} steps\n"
                f"  Trigger breach rate: {pause_trigger_breach_rate:.1%}\n"
                f"  Trigger multi-breach: {pause_trigger_multi_breach} symbols\n"
                f"  Cooldown: {pause_cooldown_steps} steps"
            )

        # Tracking metrics
        self.breaches_by_symbol = {}  # Dict[symbol, count]
        self.steps_breached_by_symbol = {}  # Dict[symbol, count]
        self.num_steps_any_breach = 0  # Steps where at least one symbol breached
        self.num_steps_all_breach = 0  # Steps where all symbols breached
        self.collateral_freeze_steps = (
            0  # Steps where non-breaching symbols were frozen (global mode only)
        )
        self.total_steps = 0

        if not enabled:
            logger.info("CostEnvelopeEnforcer: Disabled (monitoring only)")
            self.config = None
            return

        # Load configuration
        try:
            self.config = load_cost_envelope(config_path)

            # Log config info
            if self.config.per_symbol_limits:
                logger.info(
                    f"CostEnvelopeEnforcer: Enabled (mode={enforcement_mode})\n"
                    f"  Per-symbol limits loaded for {len(self.config.per_symbol_limits)} symbols\n"
                    f"  Default: commission=${self.config.default_limits.max_commission_per_lot_usd if self.config.default_limits else self.config.max_commission_per_lot_usd:.2f}/lot, "
                    f"spread={self.config.default_limits.max_spread_pips if self.config.default_limits else self.config.max_spread_pips:.2f} pips\n"
                    f"  Breach action: {self.config.breach_action}\n"
                    f"  Log path: {self.log_path}"
                )
            else:
                logger.info(
                    f"CostEnvelopeEnforcer: Enabled (mode={enforcement_mode})\n"
                    f"  Max commission: ${self.config.max_commission_per_lot_usd:.2f}/lot\n"
                    f"  Max spread: {self.config.max_spread_pips:.2f} pips\n"
                    f"  Breach action: {self.config.breach_action}\n"
                    f"  Log path: {self.log_path}"
                )
        except Exception as e:
            logger.error(f"Failed to load cost envelope config: {e}")
            logger.warning("CostEnvelopeEnforcer: Disabled due to config error")
            self.enabled = False
            self.config = None

    def check_and_enforce(
        self,
        commission_per_lot_usd: float,
        spread_pips: float,
        symbol: str | None = None,
        action: float = 0.0,
    ) -> tuple[float, bool, CostCheckResult | None]:
        """Check costs and enforce action if breach detected.

        Args:
            commission_per_lot_usd: Observed commission per lot in USD
            spread_pips: Observed spread in pips
            symbol: Symbol being traded (for logging)
            action: Proposed action (target position fraction)

        Returns:
            (enforced_action, breach_detected, check_result)

            - enforced_action: Modified action (0.0 if NO_TRADE breach)
            - breach_detected: True if costs exceed envelope limits
            - check_result: Full check result with metadata
        """
        if not self.enabled or self.config is None:
            # Monitoring only or disabled
            return action, False, None

        # Create observed costs
        observed = ObservedCosts(
            commission_per_lot_usd=commission_per_lot_usd,
            spread_pips=spread_pips,
            slippage_bps=0.0,
            symbol=symbol,
            timestamp=datetime.utcnow(),
        )

        # Check envelope
        result = check_cost_envelope(self.config, observed)

        # Log breach (or pass event for audit trail)
        if not result.within_limits or result.action != "ALLOW":
            log_cost_breach(result, self.log_path)

        # Enforce action
        if result.action == "NO_TRADE":
            # Force HOLD: don't open or increase positions
            logger.warning(
                f"CostEnvelopeEnforcer: NO_TRADE enforced for {symbol or 'portfolio'}\n"
                f"  Breaches: {', '.join(result.breaches)}\n"
                f"  Original action: {action:.3f} → Enforced: 0.0 (HOLD)"
            )
            return 0.0, True, result

        if result.action == "FLATTEN":
            # Close all positions (set action to 0.0 to close)
            logger.warning(
                f"CostEnvelopeEnforcer: FLATTEN enforced for {symbol or 'portfolio'}\n"
                f"  Breaches: {', '.join(result.breaches)}\n"
                f"  Original action: {action:.3f} → Enforced: 0.0 (FLATTEN)"
            )
            return 0.0, True, result

        # ALLOW: pass through original action
        return action, False, result

    def check_portfolio(
        self,
        costs_by_symbol: dict[str, tuple[float, float]],
    ) -> tuple[bool, list[str]]:
        """Check costs for all symbols in portfolio.

        Args:
            costs_by_symbol: Dict[symbol, (commission_per_lot_usd, spread_pips)]

        Returns:
            (any_breach_detected, symbols_breached)
        """
        if not self.enabled or self.config is None:
            return False, []

        breaches = []

        for symbol, (commission, spread) in costs_by_symbol.items():
            observed = ObservedCosts(
                commission_per_lot_usd=commission,
                spread_pips=spread,
                symbol=symbol,
            )

            result = check_cost_envelope(self.config, observed)

            if not result.within_limits:
                breaches.append(symbol)
                log_cost_breach(result, self.log_path)

        return len(breaches) > 0, breaches

    def compute_breaches_per_symbol(
        self,
        costs_by_symbol: dict[str, tuple[float, float]],
    ) -> dict[str, bool]:
        """Compute breach status for each symbol independently.

        Args:
            costs_by_symbol: Dict[symbol, (commission_per_lot_usd, spread_pips)]

        Returns:
            Dict[symbol, is_breaching] - True if symbol exceeds cost limits
        """
        if not self.enabled or self.config is None:
            return dict.fromkeys(costs_by_symbol, False)

        breaches_per_symbol = {}

        for symbol, (commission, spread) in costs_by_symbol.items():
            observed = ObservedCosts(
                commission_per_lot_usd=commission,
                spread_pips=spread,
                symbol=symbol,
            )

            result = check_cost_envelope(self.config, observed)
            breaches_per_symbol[symbol] = not result.within_limits

            # Log breach if detected
            if not result.within_limits:
                log_cost_breach(result, self.log_path)

                # Update tracking
                if symbol not in self.breaches_by_symbol:
                    self.breaches_by_symbol[symbol] = 0
                    self.steps_breached_by_symbol[symbol] = 0
                self.breaches_by_symbol[symbol] += 1
                self.steps_breached_by_symbol[symbol] += 1

        return breaches_per_symbol

    def apply_enforcement(
        self,
        actions: dict[str, float],
        breaches_per_symbol: dict[str, bool],
        symbols: list[str],
    ) -> tuple[dict[str, float], dict]:
        """Apply enforcement based on breaches and mode.

        Args:
            actions: Dict[symbol, action] - proposed actions
            breaches_per_symbol: Dict[symbol, is_breaching]
            symbols: List of all symbols (for global mode)

        Returns:
            (enforced_actions, metadata)
            - enforced_actions: Modified actions dict
            - metadata: Dict with enforcement stats
        """
        self.total_steps += 1

        enforced_actions = actions.copy()
        any_breach = any(breaches_per_symbol.values())
        all_breach = all(breaches_per_symbol.get(s, False) for s in symbols)

        # Update step-level tracking
        if any_breach:
            self.num_steps_any_breach += 1
        if all_breach:
            self.num_steps_all_breach += 1

        symbols_frozen = []
        collateral_frozen = []
        global_pause_active = False

        # Check global pause fallback first
        if self.global_pause is not None:
            global_pause_active = self.global_pause.check_and_update(
                breaches_per_symbol, self.total_steps
            )

            if global_pause_active:
                # GLOBAL PAUSE: Zero ALL actions
                for symbol in symbols:
                    enforced_actions[symbol] = 0.0
                    symbols_frozen.append(symbol)

                    # All symbols are frozen during global pause
                    if not breaches_per_symbol.get(symbol, False):
                        collateral_frozen.append(symbol)

                # Log transitions if any occurred
                if self.global_pause.transitions:
                    events_log = self.log_path.parent / "cost_envelope_events.jsonl"
                    self.global_pause.log_transitions(events_log)

                metadata = {
                    "enforcement_mode": "global_pause",
                    "any_breach": any_breach,
                    "all_breach": all_breach,
                    "symbols_frozen": symbols_frozen,
                    "collateral_frozen": collateral_frozen,
                    "num_collateral_frozen": len(collateral_frozen),
                    "global_pause_active": True,
                    "global_pause_mode": self.global_pause.mode,
                }

                return enforced_actions, metadata

        # Standard enforcement (if not in global pause)
        if self.enforcement_mode == "global":
            # Legacy: freeze ALL symbols if ANY breaches
            if any_breach:
                for symbol in symbols:
                    enforced_actions[symbol] = 0.0
                    symbols_frozen.append(symbol)

                    # Track collateral damage (non-breaching symbols frozen)
                    if not breaches_per_symbol.get(symbol, False):
                        collateral_frozen.append(symbol)

                if collateral_frozen:
                    self.collateral_freeze_steps += 1

        else:  # per_symbol mode (default)
            # Only freeze breaching symbols
            for symbol, is_breaching in breaches_per_symbol.items():
                if is_breaching:
                    enforced_actions[symbol] = 0.0
                    symbols_frozen.append(symbol)

        metadata = {
            "enforcement_mode": self.enforcement_mode,
            "any_breach": any_breach,
            "all_breach": all_breach,
            "symbols_frozen": symbols_frozen,
            "collateral_frozen": collateral_frozen,
            "num_collateral_frozen": len(collateral_frozen),
        }

        return enforced_actions, metadata

    def get_summary_stats(self) -> dict:
        """Get summary statistics for summary.json output.

        Returns:
            Dict with cost envelope tracking metrics
        """
        stats = {
            "ce_enabled": self.enabled,
            "ce_enforcement_mode": self.enforcement_mode,
            "ce_fallback_mode": self.fallback_mode,
            "ce_total_steps": self.total_steps,
            "ce_steps_any_breach": self.num_steps_any_breach,
            "ce_steps_all_breach": self.num_steps_all_breach,
            "ce_breach_rate_any": (
                self.num_steps_any_breach / self.total_steps if self.total_steps > 0 else 0.0
            ),
            "ce_breach_rate_all": (
                self.num_steps_all_breach / self.total_steps if self.total_steps > 0 else 0.0
            ),
            "ce_breaches_by_symbol": dict(self.breaches_by_symbol),
            "ce_collateral_freeze_steps": self.collateral_freeze_steps,
        }

        # Add global pause stats if enabled
        if self.global_pause is not None:
            pause_stats = self.global_pause.get_summary_stats()
            stats.update(pause_stats)

        return stats
