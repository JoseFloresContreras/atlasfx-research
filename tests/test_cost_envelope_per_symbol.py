"""
Tests for Cost Envelope Per-Symbol Enforcement

Tests the hybrid fix for Cost Envelope:
- Per-symbol enforcement (only freeze breaching symbols)
- Per-symbol config (different thresholds per symbol)
- Global mode legacy (freeze all when any breaches)
- Config parsing (legacy vs new format)
"""

import json
import numpy as np
import pytest
from pathlib import Path
import tempfile

from atlasfx.risk.cost_envelope import (
    CostEnvelopeConfig,
    SymbolCostLimits,
    load_cost_envelope,
    check_cost_envelope,
    ObservedCosts,
)
from atlasfx.risk.cost_envelope_enforcement import CostEnvelopeEnforcer


# ============================================================================
# TEST 1: Per-Symbol Enforcement Only Affects Breaching Symbol
# ============================================================================
def test_per_symbol_enforcement_only_affects_breaching_symbol():
    """Test that per-symbol mode only zeros actions for breaching symbols."""

    # Create temp config with per-symbol limits
    config_data = {
        "envelope_limits": {
            "default": {"max_spread_pips": 0.5, "max_commission_per_lot_usd": 5.0},
            "symbols": {
                "USDJPY": {"max_spread_pips": 0.65},
                "EURUSD": {"max_spread_pips": 0.5},
                "GBPUSD": {"max_spread_pips": 0.5},
            },
        },
        "breach_actions": {"primary_action": "NO_TRADE"},
    }

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(config_data, f)
        config_path = f.name

    try:
        # Create enforcer in per_symbol mode
        enforcer = CostEnvelopeEnforcer(
            config_path=config_path, enabled=True, enforcement_mode="per_symbol"
        )

        symbols = ["EURUSD", "GBPUSD", "USDJPY"]

        # Simulate costs where only USDJPY breaches
        costs_by_symbol = {
            "EURUSD": (4.0, 0.4),  # Within limits
            "GBPUSD": (4.0, 0.4),  # Within limits
            "USDJPY": (4.0, 0.7),  # BREACH: spread 0.7 > 0.65
        }

        # Compute breaches
        breaches = enforcer.compute_breaches_per_symbol(costs_by_symbol)

        assert breaches["EURUSD"] == False, "EURUSD should not breach"
        assert breaches["GBPUSD"] == False, "GBPUSD should not breach"
        assert breaches["USDJPY"] == True, "USDJPY should breach"

        # Apply enforcement
        actions_dict = {"EURUSD": 0.5, "GBPUSD": 0.3, "USDJPY": 0.8}
        enforced_actions, metadata = enforcer.apply_enforcement(actions_dict, breaches, symbols)

        # Verify only USDJPY is frozen
        assert enforced_actions["EURUSD"] == 0.5, "EURUSD action should be preserved"
        assert enforced_actions["GBPUSD"] == 0.3, "GBPUSD action should be preserved"
        assert enforced_actions["USDJPY"] == 0.0, "USDJPY action should be zeroed"

        # Verify metadata
        assert metadata["enforcement_mode"] == "per_symbol"
        assert metadata["any_breach"] == True
        assert metadata["symbols_frozen"] == ["USDJPY"]
        assert metadata["collateral_frozen"] == []
        assert metadata["num_collateral_frozen"] == 0

        print("✅ Per-symbol enforcement test passed")

    finally:
        Path(config_path).unlink()


# ============================================================================
# TEST 2: Global Mode Still Freezes All Symbols
# ============================================================================
def test_global_mode_still_freezes_all_symbols():
    """Test that global mode (legacy) freezes ALL symbols when ANY breaches."""

    # Create temp config
    config_data = {
        "envelope_limits": {"default": {"max_spread_pips": 0.5, "max_commission_per_lot_usd": 5.0}},
        "breach_actions": {"primary_action": "NO_TRADE"},
    }

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(config_data, f)
        config_path = f.name

    try:
        # Create enforcer in GLOBAL mode
        enforcer = CostEnvelopeEnforcer(
            config_path=config_path, enabled=True, enforcement_mode="global"
        )

        symbols = ["EURUSD", "GBPUSD", "USDJPY"]

        # Simulate costs where only USDJPY breaches
        costs_by_symbol = {
            "EURUSD": (4.0, 0.4),  # Within limits
            "GBPUSD": (4.0, 0.4),  # Within limits
            "USDJPY": (4.0, 0.7),  # BREACH: spread 0.7 > 0.5
        }

        # Compute breaches
        breaches = enforcer.compute_breaches_per_symbol(costs_by_symbol)

        assert breaches["USDJPY"] == True

        # Apply enforcement
        actions_dict = {"EURUSD": 0.5, "GBPUSD": 0.3, "USDJPY": 0.8}
        enforced_actions, metadata = enforcer.apply_enforcement(actions_dict, breaches, symbols)

        # Verify ALL symbols are frozen
        assert enforced_actions["EURUSD"] == 0.0, "EURUSD should be frozen (collateral)"
        assert enforced_actions["GBPUSD"] == 0.0, "GBPUSD should be frozen (collateral)"
        assert enforced_actions["USDJPY"] == 0.0, "USDJPY should be frozen (breacher)"

        # Verify metadata
        assert metadata["enforcement_mode"] == "global"
        assert metadata["any_breach"] == True
        assert set(metadata["symbols_frozen"]) == set(symbols)
        assert set(metadata["collateral_frozen"]) == {"EURUSD", "GBPUSD"}
        assert metadata["num_collateral_frozen"] == 2

        # Verify collateral tracking
        assert enforcer.collateral_freeze_steps == 1

        print("✅ Global mode enforcement test passed")

    finally:
        Path(config_path).unlink()


# ============================================================================
# TEST 3: Config Parsing Per-Symbol and Fallback Default
# ============================================================================
def test_config_parsing_per_symbol_and_fallback_default():
    """Test that config parsing handles both formats correctly."""

    # Test 1: Legacy global format
    legacy_config = {
        "envelope_limits": {
            "max_commission_per_lot_usd": 5.0,
            "max_spread_pips": 0.5,
        },
        "breach_actions": {"primary_action": "NO_TRADE"},
    }

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(legacy_config, f)
        legacy_path = f.name

    try:
        config = load_cost_envelope(legacy_path)

        # Should use global limits
        assert config.max_spread_pips == 0.5
        assert config.max_commission_per_lot_usd == 5.0
        assert len(config.per_symbol_limits) == 0
        assert config.default_limits is None

        # get_limits_for_symbol should return legacy limits
        limits = config.get_limits_for_symbol("EURUSD")
        assert limits.max_spread_pips == 0.5

        print("✅ Legacy format parsing test passed")

    finally:
        Path(legacy_path).unlink()

    # Test 2: Per-symbol format with default
    per_symbol_config = {
        "envelope_limits": {
            "default": {"max_commission_per_lot_usd": 5.0, "max_spread_pips": 0.5},
            "symbols": {
                "USDJPY": {"max_spread_pips": 0.65},
                "EURUSD": {"max_spread_pips": 0.45},
            },
        },
        "breach_actions": {"primary_action": "NO_TRADE"},
    }

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(per_symbol_config, f)
        per_symbol_path = f.name

    try:
        config = load_cost_envelope(per_symbol_path)

        # Should have default limits
        assert config.default_limits is not None
        assert config.default_limits.max_spread_pips == 0.5

        # Should have per-symbol overrides
        assert len(config.per_symbol_limits) == 2

        # Test get_limits_for_symbol
        usdjpy_limits = config.get_limits_for_symbol("USDJPY")
        assert usdjpy_limits.max_spread_pips == 0.65, "USDJPY should use override"

        eurusd_limits = config.get_limits_for_symbol("EURUSD")
        assert eurusd_limits.max_spread_pips == 0.45, "EURUSD should use override"

        gbpusd_limits = config.get_limits_for_symbol("GBPUSD")
        assert gbpusd_limits.max_spread_pips == 0.5, "GBPUSD should use default fallback"

        print("✅ Per-symbol format parsing test passed")

    finally:
        Path(per_symbol_path).unlink()


# ============================================================================
# TEST 4: Summary Fields Present for CE Mode
# ============================================================================
def test_summary_fields_present_for_ce_mode():
    """Test that enforcer exposes correct tracking fields."""

    config_data = {
        "envelope_limits": {
            "default": {"max_spread_pips": 0.5, "max_commission_per_lot_usd": 5.0},
            "symbols": {"USDJPY": {"max_spread_pips": 0.65}},
        },
        "breach_actions": {"primary_action": "NO_TRADE"},
    }

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(config_data, f)
        config_path = f.name

    try:
        # Test per_symbol mode
        enforcer = CostEnvelopeEnforcer(
            config_path=config_path, enabled=True, enforcement_mode="per_symbol"
        )

        symbols = ["EURUSD", "USDJPY"]

        # Simulate multiple steps with different breach patterns
        # Step 1: USDJPY breaches
        costs1 = {"EURUSD": (4.0, 0.4), "USDJPY": (4.0, 0.7)}
        breaches1 = enforcer.compute_breaches_per_symbol(costs1)
        actions1 = {"EURUSD": 0.5, "USDJPY": 0.8}
        _, meta1 = enforcer.apply_enforcement(actions1, breaches1, symbols)

        # Step 2: Both breach
        costs2 = {"EURUSD": (4.0, 0.6), "USDJPY": (4.0, 0.7)}
        breaches2 = enforcer.compute_breaches_per_symbol(costs2)
        actions2 = {"EURUSD": 0.5, "USDJPY": 0.8}
        _, meta2 = enforcer.apply_enforcement(actions2, breaches2, symbols)

        # Step 3: No breach
        costs3 = {"EURUSD": (4.0, 0.4), "USDJPY": (4.0, 0.6)}
        breaches3 = enforcer.compute_breaches_per_symbol(costs3)
        actions3 = {"EURUSD": 0.5, "USDJPY": 0.8}
        _, meta3 = enforcer.apply_enforcement(actions3, breaches3, symbols)

        # Verify tracking fields exist
        assert hasattr(enforcer, "breaches_by_symbol")
        assert hasattr(enforcer, "steps_breached_by_symbol")
        assert hasattr(enforcer, "num_steps_any_breach")
        assert hasattr(enforcer, "num_steps_all_breach")
        assert hasattr(enforcer, "collateral_freeze_steps")
        assert hasattr(enforcer, "total_steps")

        # Verify counts
        assert enforcer.total_steps == 3
        assert enforcer.num_steps_any_breach == 2  # Steps 1 and 2
        assert enforcer.num_steps_all_breach == 1  # Step 2 only
        assert enforcer.collateral_freeze_steps == 0  # Per-symbol mode

        # Verify per-symbol tracking
        assert enforcer.steps_breached_by_symbol["USDJPY"] == 2  # Steps 1 and 2
        assert enforcer.steps_breached_by_symbol["EURUSD"] == 1  # Step 2 only

        print("✅ Summary fields test passed (per_symbol mode)")

        # Test global mode for collateral tracking
        enforcer_global = CostEnvelopeEnforcer(
            config_path=config_path, enabled=True, enforcement_mode="global"
        )

        # Step 1: USDJPY breaches (should freeze all)
        breaches1_g = enforcer_global.compute_breaches_per_symbol(costs1)
        actions1_g = {"EURUSD": 0.5, "USDJPY": 0.8}
        _, meta1_g = enforcer_global.apply_enforcement(actions1_g, breaches1_g, symbols)

        assert enforcer_global.collateral_freeze_steps == 1  # EURUSD was frozen

        print("✅ Summary fields test passed (global mode)")

    finally:
        Path(config_path).unlink()


if __name__ == "__main__":
    print("Running Cost Envelope Per-Symbol Tests...")
    print("=" * 70)

    test_per_symbol_enforcement_only_affects_breaching_symbol()
    test_global_mode_still_freezes_all_symbols()
    test_config_parsing_per_symbol_and_fallback_default()
    test_summary_fields_present_for_ce_mode()

    print("=" * 70)
    print("✅ All tests passed!")
