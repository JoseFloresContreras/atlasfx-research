"""
Cost Envelope Runtime Guardrail System

Enforces production cost limits during trading to prevent degraded performance
under unfavorable broker conditions (high commissions, wide spreads).

Key Features:
- Load cost envelope configuration from JSON
- Check observed costs against envelope limits
- Trigger breach actions (NO_TRADE, FLATTEN)
- Log structured breach events for monitoring

Convention:
- Slippage is NOT considered (always 0.0 bps)
- Focuses on commission_per_lot_usd and spread_pips only
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class SymbolCostLimits:
    """Cost limits for a specific symbol."""

    max_commission_per_lot_usd: float
    max_spread_pips: float
    max_slippage_bps: float = 0.0


@dataclass(frozen=True)
class CostEnvelopeConfig:
    """Production cost envelope configuration.

    Defines maximum acceptable trading costs and breach response actions.

    Supports two formats:
    1. Global (legacy): Single thresholds for all symbols
    2. Per-symbol: Different thresholds per symbol with fallback to default
    """

    # Legacy global limits (used if per_symbol_limits is empty)
    max_commission_per_lot_usd: float
    max_spread_pips: float
    max_slippage_bps: float  # Always 0.0 - not enforced but kept for schema compatibility

    breach_action: Literal["NO_TRADE", "FLATTEN"]
    secondary_action: Literal["FLATTEN", "NO_TRADE"] | None = None
    log_level: str = "ERROR"

    description: str = ""
    version: str = "1.0"

    # Per-symbol limits (new format)
    per_symbol_limits: dict[str, SymbolCostLimits] = field(default_factory=dict)
    default_limits: SymbolCostLimits | None = None

    def get_limits_for_symbol(self, symbol: str) -> SymbolCostLimits:
        """Get cost limits for a specific symbol with fallback logic.

        Priority:
        1. per_symbol_limits[symbol] if exists
        2. default_limits if exists
        3. Legacy global limits
        """
        if symbol in self.per_symbol_limits:
            return self.per_symbol_limits[symbol]

        if self.default_limits is not None:
            return self.default_limits

        # Fallback to legacy global limits
        return SymbolCostLimits(
            max_commission_per_lot_usd=self.max_commission_per_lot_usd,
            max_spread_pips=self.max_spread_pips,
            max_slippage_bps=self.max_slippage_bps,
        )


@dataclass
class ObservedCosts:
    """Observed trading costs from broker/environment."""

    commission_per_lot_usd: float
    spread_pips: float
    slippage_bps: float = 0.0  # Not used in checks, kept for compatibility

    symbol: str | None = None
    timestamp: datetime | None = None


@dataclass
class CostCheckResult:
    """Result of cost envelope check.

    Indicates whether observed costs are within limits and what action to take.
    """

    within_limits: bool
    breaches: list[str] = field(default_factory=list)
    action: Literal["ALLOW", "NO_TRADE", "FLATTEN"] = "ALLOW"

    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        """Populate metadata if not provided."""
        if not self.metadata:
            self.metadata = {
                "breaches": self.breaches,
                "action_taken": self.action,
            }


def load_cost_envelope(config_path: Path | str) -> CostEnvelopeConfig:
    """Load cost envelope configuration from JSON file.

    Supports two formats:
    1. Legacy global format:
       {
         "envelope_limits": {"max_commission_per_lot_usd": 5.0, "max_spread_pips": 0.5},
         "breach_actions": {"primary_action": "NO_TRADE"}
       }

    2. Per-symbol format:
       {
         "envelope_limits": {
           "default": {"max_commission_per_lot_usd": 5.0, "max_spread_pips": 0.5},
           "symbols": {
             "USDJPY": {"max_spread_pips": 0.65},
             "EURUSD": {"max_spread_pips": 0.5}
           }
         },
         "breach_actions": {"primary_action": "NO_TRADE"}
       }

    Args:
        config_path: Path to production_cost_envelope.json

    Returns:
        CostEnvelopeConfig dataclass

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid
    """
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Cost envelope config not found: {config_path}")

    with open(config_path) as f:
        data = json.load(f)

    # Extract envelope limits
    limits = data.get("envelope_limits", {})
    breach_actions = data.get("breach_actions", {})

    if not limits:
        raise ValueError("Missing 'envelope_limits' in config")

    # Detect format: legacy global or per-symbol
    has_default = "default" in limits
    has_symbols = "symbols" in limits

    per_symbol_limits = {}
    default_limits = None

    if has_default or has_symbols:
        # New per-symbol format
        # Parse default limits
        if has_default:
            default_data = limits["default"]
            default_limits = SymbolCostLimits(
                max_commission_per_lot_usd=default_data.get("max_commission_per_lot_usd", 5.0),
                max_spread_pips=default_data.get("max_spread_pips", 0.5),
                max_slippage_bps=default_data.get("max_slippage_bps", 0.0),
            )

        # Parse per-symbol overrides
        if has_symbols:
            symbols_data = limits["symbols"]
            for symbol, symbol_limits in symbols_data.items():
                # Use default as base, override with symbol-specific values
                base_commission = (
                    default_limits.max_commission_per_lot_usd if default_limits else 5.0
                )
                base_spread = default_limits.max_spread_pips if default_limits else 0.5
                base_slippage = default_limits.max_slippage_bps if default_limits else 0.0

                per_symbol_limits[symbol] = SymbolCostLimits(
                    max_commission_per_lot_usd=symbol_limits.get(
                        "max_commission_per_lot_usd", base_commission
                    ),
                    max_spread_pips=symbol_limits.get("max_spread_pips", base_spread),
                    max_slippage_bps=symbol_limits.get("max_slippage_bps", base_slippage),
                )

        # Use default as global fallback, or first symbol if no default
        if default_limits:
            global_commission = default_limits.max_commission_per_lot_usd
            global_spread = default_limits.max_spread_pips
            global_slippage = default_limits.max_slippage_bps
        else:
            global_commission = 5.0
            global_spread = 0.5
            global_slippage = 0.0
    else:
        # Legacy global format
        global_commission = limits["max_commission_per_lot_usd"]
        global_spread = limits["max_spread_pips"]
        global_slippage = limits.get("max_slippage_bps", 0.0)

    return CostEnvelopeConfig(
        max_commission_per_lot_usd=global_commission,
        max_spread_pips=global_spread,
        max_slippage_bps=global_slippage,
        breach_action=breach_actions.get("primary_action", "NO_TRADE"),
        secondary_action=breach_actions.get("secondary_action"),
        log_level=breach_actions.get("log_level", "ERROR"),
        description=data.get("description", ""),
        version=data.get("version", "1.0"),
        per_symbol_limits=per_symbol_limits,
        default_limits=default_limits,
    )


def check_cost_envelope(
    envelope: CostEnvelopeConfig,
    observed_costs: ObservedCosts,
) -> CostCheckResult:
    """Check if observed costs are within envelope limits.

    Args:
        envelope: Cost envelope configuration
        observed_costs: Current observed costs from broker

    Returns:
        CostCheckResult with breach information and recommended action

    Notes:
        - Slippage is NOT checked (always assumed 0.0 bps)
        - Commission and spread are checked independently
        - Action is determined by envelope.breach_action
        - Uses per-symbol limits if symbol is provided, otherwise global limits
    """
    # Get limits for this symbol (with fallback logic)
    symbol = observed_costs.symbol or "UNKNOWN"
    limits = envelope.get_limits_for_symbol(symbol)

    breaches = []

    # Check commission
    if observed_costs.commission_per_lot_usd > limits.max_commission_per_lot_usd:
        breaches.append(
            f"Commission ${observed_costs.commission_per_lot_usd:.2f} "
            f"exceeds limit ${limits.max_commission_per_lot_usd:.2f}"
        )

    # Check spread
    if observed_costs.spread_pips > limits.max_spread_pips:
        breaches.append(
            f"Spread {observed_costs.spread_pips:.2f} pips "
            f"exceeds limit {limits.max_spread_pips:.2f} pips"
        )

    # Slippage NOT checked (kept at 0.0 bps, not enforced)

    within_limits = len(breaches) == 0

    if within_limits:
        action = "ALLOW"
    else:
        action = envelope.breach_action

    metadata = {
        "commission_per_lot_usd": observed_costs.commission_per_lot_usd,
        "spread_pips": observed_costs.spread_pips,
        "slippage_bps": 0.0,  # Not checked
        "max_commission_per_lot_usd": limits.max_commission_per_lot_usd,
        "max_spread_pips": limits.max_spread_pips,
        "breaches": breaches,
        "action_taken": action,
        "symbol": observed_costs.symbol,
        "timestamp": observed_costs.timestamp.isoformat() if observed_costs.timestamp else None,
    }

    return CostCheckResult(
        within_limits=within_limits,
        breaches=breaches,
        action=action,
        metadata=metadata,
    )


def log_cost_breach(
    check_result: CostCheckResult,
    log_path: Path | str = "reports/runtime_cost_monitoring/cost_breaches.jsonl",
) -> None:
    """Log cost breach event to structured JSONL file.

    Args:
        check_result: Result from check_cost_envelope
        log_path: Path to JSONL log file (appends if exists)

    Notes:
        - Creates parent directories if needed
        - Appends one JSON line per breach event
        - Logs even if within_limits=True (for audit trail)
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Build log entry
    log_entry = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "within_limits": check_result.within_limits,
        "breaches": check_result.breaches,
        "action": check_result.action,
        **check_result.metadata,  # Includes costs, thresholds, symbol
    }

    # Append to JSONL
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")
