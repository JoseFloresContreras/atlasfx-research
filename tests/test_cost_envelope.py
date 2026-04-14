"""
Tests for Cost Envelope Runtime Guardrail System

Validates:
- Configuration loading
- Cost checking logic (PASS, BREACH commission, BREACH spread)
- Action selection (NO_TRADE, FLATTEN)
- Breach logging
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, UTC

from atlasfx.risk.cost_envelope import (
    CostEnvelopeConfig,
    ObservedCosts,
    CostCheckResult,
    load_cost_envelope,
    check_cost_envelope,
    log_cost_breach,
)


@pytest.fixture
def mock_envelope_config(tmp_path):
    """Create a mock cost envelope config file."""
    config = {
        "description": "Test cost envelope",
        "version": "1.0",
        "baseline_costs": {
            "commission_per_lot_usd": 2.5,
            "spread_pips_typical": 0.2,
            "slippage_bps": 0.0,
        },
        "envelope_limits": {
            "max_commission_per_lot_usd": 5.0,
            "max_spread_pips": 0.5,
            "max_slippage_bps": 0.0,
        },
        "breach_actions": {
            "primary_action": "NO_TRADE",
            "secondary_action": "FLATTEN",
            "log_level": "ERROR",
        },
    }

    config_path = tmp_path / "test_envelope.json"
    with open(config_path, "w") as f:
        json.dump(config, f)

    return config_path


@pytest.fixture
def envelope():
    """Create a test envelope config."""
    return CostEnvelopeConfig(
        max_commission_per_lot_usd=5.0,
        max_spread_pips=0.5,
        max_slippage_bps=0.0,
        breach_action="NO_TRADE",
        secondary_action="FLATTEN",
    )


class TestCostEnvelopeLoading:
    """Test configuration loading."""

    def test_load_valid_config(self, mock_envelope_config):
        """Test loading valid config file."""
        envelope = load_cost_envelope(mock_envelope_config)

        assert envelope.max_commission_per_lot_usd == 5.0
        assert envelope.max_spread_pips == 0.5
        assert envelope.max_slippage_bps == 0.0
        assert envelope.breach_action == "NO_TRADE"
        assert envelope.secondary_action == "FLATTEN"

    def test_load_missing_file(self, tmp_path):
        """Test loading non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            load_cost_envelope(tmp_path / "nonexistent.json")

    def test_load_invalid_config(self, tmp_path):
        """Test loading invalid config raises error."""
        bad_config = tmp_path / "bad.json"
        with open(bad_config, "w") as f:
            json.dump({"invalid": "config"}, f)

        with pytest.raises(ValueError):
            load_cost_envelope(bad_config)


class TestCostEnvelopeChecking:
    """Test cost checking logic."""

    def test_pass_within_limits(self, envelope):
        """Test PASS: commission=2.5, spread=0.2 (both within limits)."""
        observed = ObservedCosts(
            commission_per_lot_usd=2.5,
            spread_pips=0.2,
            symbol="EURUSD",
            timestamp=datetime.now(UTC),
        )

        result = check_cost_envelope(envelope, observed)

        assert result.within_limits is True
        assert len(result.breaches) == 0
        assert result.action == "ALLOW"
        assert result.metadata["commission_per_lot_usd"] == 2.5
        assert result.metadata["spread_pips"] == 0.2

    def test_breach_spread_only(self, envelope):
        """Test BREACH: commission=2.5 (OK), spread=1.0 (exceeds 0.5)."""
        observed = ObservedCosts(
            commission_per_lot_usd=2.5,
            spread_pips=1.0,
            symbol="GBPUSD",
            timestamp=datetime.now(UTC),
        )

        result = check_cost_envelope(envelope, observed)

        assert result.within_limits is False
        assert len(result.breaches) == 1
        assert "Spread 1.00 pips exceeds limit 0.50 pips" in result.breaches[0]
        assert result.action == "NO_TRADE"  # Primary action

    def test_breach_commission_only(self, envelope):
        """Test BREACH: commission=12.5 (exceeds 5.0), spread=0.2 (OK)."""
        observed = ObservedCosts(
            commission_per_lot_usd=12.5,
            spread_pips=0.2,
            symbol="USDJPY",
            timestamp=datetime.now(UTC),
        )

        result = check_cost_envelope(envelope, observed)

        assert result.within_limits is False
        assert len(result.breaches) == 1
        assert "Commission $12.50 exceeds limit $5.00" in result.breaches[0]
        assert result.action == "NO_TRADE"

    def test_breach_both_costs(self, envelope):
        """Test BREACH: both commission and spread exceed limits."""
        observed = ObservedCosts(
            commission_per_lot_usd=12.5,
            spread_pips=1.0,
            symbol="EURUSD",
            timestamp=datetime.now(UTC),
        )

        result = check_cost_envelope(envelope, observed)

        assert result.within_limits is False
        assert len(result.breaches) == 2
        assert result.action == "NO_TRADE"

    def test_at_limit_boundary(self, envelope):
        """Test costs exactly at limits (should PASS)."""
        observed = ObservedCosts(
            commission_per_lot_usd=5.0,  # Exactly at limit
            spread_pips=0.5,  # Exactly at limit
            symbol="EURUSD",
            timestamp=datetime.now(UTC),
        )

        result = check_cost_envelope(envelope, observed)

        assert result.within_limits is True
        assert len(result.breaches) == 0
        assert result.action == "ALLOW"

    def test_just_over_limit(self, envelope):
        """Test costs just over limits (should BREACH)."""
        observed = ObservedCosts(
            commission_per_lot_usd=5.01,
            spread_pips=0.51,
            symbol="EURUSD",
            timestamp=datetime.now(UTC),
        )

        result = check_cost_envelope(envelope, observed)

        assert result.within_limits is False
        assert len(result.breaches) == 2


class TestBreachActions:
    """Test breach action selection."""

    def test_no_trade_action(self):
        """Test NO_TRADE breach action."""
        envelope = CostEnvelopeConfig(
            max_commission_per_lot_usd=5.0,
            max_spread_pips=0.5,
            max_slippage_bps=0.0,
            breach_action="NO_TRADE",
        )

        observed = ObservedCosts(
            commission_per_lot_usd=12.5,
            spread_pips=0.2,
        )

        result = check_cost_envelope(envelope, observed)

        assert result.action == "NO_TRADE"

    def test_flatten_action(self):
        """Test FLATTEN breach action."""
        envelope = CostEnvelopeConfig(
            max_commission_per_lot_usd=5.0,
            max_spread_pips=0.5,
            max_slippage_bps=0.0,
            breach_action="FLATTEN",
        )

        observed = ObservedCosts(
            commission_per_lot_usd=2.5,
            spread_pips=1.0,
        )

        result = check_cost_envelope(envelope, observed)

        assert result.action == "FLATTEN"


class TestBreachLogging:
    """Test breach event logging."""

    def test_log_breach_event(self, tmp_path, envelope):
        """Test logging breach to JSONL."""
        log_path = tmp_path / "cost_breaches.jsonl"

        observed = ObservedCosts(
            commission_per_lot_usd=12.5,
            spread_pips=0.2,
            symbol="EURUSD",
            timestamp=datetime(2026, 1, 8, 12, 0, 0),
        )

        result = check_cost_envelope(envelope, observed)
        log_cost_breach(result, log_path)

        # Verify file created
        assert log_path.exists()

        # Parse JSONL
        with open(log_path) as f:
            lines = f.readlines()

        assert len(lines) == 1

        log_entry = json.loads(lines[0])
        assert log_entry["within_limits"] is False
        assert log_entry["action"] == "NO_TRADE"
        assert log_entry["commission_per_lot_usd"] == 12.5
        assert log_entry["spread_pips"] == 0.2
        assert log_entry["symbol"] == "EURUSD"
        assert len(log_entry["breaches"]) == 1

    def test_log_multiple_breaches(self, tmp_path, envelope):
        """Test logging multiple breach events."""
        log_path = tmp_path / "cost_breaches.jsonl"

        # First breach
        result1 = check_cost_envelope(
            envelope, ObservedCosts(commission_per_lot_usd=12.5, spread_pips=0.2, symbol="EURUSD")
        )
        log_cost_breach(result1, log_path)

        # Second breach
        result2 = check_cost_envelope(
            envelope, ObservedCosts(commission_per_lot_usd=2.5, spread_pips=1.0, symbol="GBPUSD")
        )
        log_cost_breach(result2, log_path)

        # Verify both logged
        with open(log_path) as f:
            lines = f.readlines()

        assert len(lines) == 2

        entry1 = json.loads(lines[0])
        entry2 = json.loads(lines[1])

        assert entry1["symbol"] == "EURUSD"
        assert entry2["symbol"] == "GBPUSD"

    def test_log_pass_event(self, tmp_path, envelope):
        """Test logging PASS event (for audit trail)."""
        log_path = tmp_path / "cost_breaches.jsonl"

        observed = ObservedCosts(
            commission_per_lot_usd=2.5,
            spread_pips=0.2,
            symbol="USDJPY",
        )

        result = check_cost_envelope(envelope, observed)
        log_cost_breach(result, log_path)

        with open(log_path) as f:
            log_entry = json.loads(f.readline())

        assert log_entry["within_limits"] is True
        assert log_entry["action"] == "ALLOW"
        assert len(log_entry["breaches"]) == 0


class TestSlippageIgnored:
    """Test that slippage is NOT enforced (always 0.0 bps)."""

    def test_slippage_not_checked(self, envelope):
        """Test slippage value doesn't affect checks."""
        observed = ObservedCosts(
            commission_per_lot_usd=2.5,
            spread_pips=0.2,
            slippage_bps=100.0,  # High slippage, but should be ignored
        )

        result = check_cost_envelope(envelope, observed)

        assert result.within_limits is True  # Should still pass
        assert len(result.breaches) == 0
        assert result.metadata["slippage_bps"] == 0.0  # Always 0 in metadata


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
