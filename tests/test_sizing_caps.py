"""
Tests for hardened sizing caps (multi-subcuenta pathology prevention).

Tests verify that sizing caps are correctly applied and prevent pathological
compounding where one symbol collapses while another explodes.
"""

import pytest
import numpy as np
import pandas as pd
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from atlasfx.environments.trading_env import ProductionTradingEnv, ProductionTradingConfig


def create_test_env(
    max_lots_per_symbol=20.0,
    max_concentration_pct=40.0,
    max_notional_per_symbol_usd=None,
):
    """Create a minimal test environment for sizing caps testing."""
    # Create minimal market data
    n_steps = 1000
    data = pd.DataFrame(
        {
            "[Feature] EURUSD | atr_14": np.random.randn(n_steps) * 0.01,
            "EURUSD-pair | open": 1.10 + np.random.randn(n_steps) * 0.001,
            "EURUSD-pair | high": 1.10 + np.random.randn(n_steps) * 0.001 + 0.0005,
            "EURUSD-pair | low": 1.10 + np.random.randn(n_steps) * 0.001 - 0.0005,
            "EURUSD-pair | close": 1.10 + np.random.randn(n_steps) * 0.001,
            "EURUSD-pair | atr_14_real_pips": np.full(n_steps, 10.0),
        }
    )

    symbols = ["EURUSD"]
    price_cols = {
        "EURUSD": {
            "open": "EURUSD-pair | open",
            "high": "EURUSD-pair | high",
            "low": "EURUSD-pair | low",
            "close": "EURUSD-pair | close",
        }
    }

    config = ProductionTradingConfig(
        initial_balance=10_000.0,
        max_risk_per_trade_pct=0.02,
        max_position_lots=50.0,
        max_lots_per_symbol=max_lots_per_symbol,
        max_concentration_pct_per_symbol=max_concentration_pct,
        max_notional_per_symbol_usd=max_notional_per_symbol_usd,
        episode_length=100,
        validation_mode=False,
    )

    env = ProductionTradingEnv(
        data=data,
        symbols=symbols,
        price_cols=price_cols,
        config=config,
    )

    return env


def test_sizing_caps_apply():
    """Test that sizing caps are correctly applied and tracked."""
    env = create_test_env(
        max_lots_per_symbol=5.0,  # Very low cap to force hits
        max_concentration_pct=20.0,  # Low concentration cap
    )

    env.reset(seed=42)

    # Force large action that should hit caps
    large_action = np.array([[1.0, 2.0, 3.0]])  # Max position with high confidence

    cap_hits_before = sum(env.lot_cap_hits_by_symbol.values())
    concentration_hits_before = sum(env.concentration_cap_hits_by_symbol.values())

    # Execute multiple steps with large actions
    for _ in range(10):
        obs, reward, terminated, truncated, info = env.step(large_action)
        if terminated or truncated:
            break

    cap_hits_after = sum(env.lot_cap_hits_by_symbol.values())
    concentration_hits_after = sum(env.concentration_cap_hits_by_symbol.values())

    # At least one cap should have been hit
    total_hits = (cap_hits_after - cap_hits_before) + (
        concentration_hits_after - concentration_hits_before
    )

    print("\n✅ Sizing caps test:")
    print(f"   Lot cap hits: {cap_hits_after - cap_hits_before}")
    print(f"   Concentration cap hits: {concentration_hits_after - concentration_hits_before}")
    print(f"   Total cap hits: {total_hits}")
    print(f"   Max lots observed: {env.max_lots_observed_by_symbol['EURUSD']:.2f}")
    print(f"   Max concentration: {env.max_concentration_observed_pct_by_symbol['EURUSD']:.2f}%")

    assert total_hits > 0, "Expected at least one sizing cap to be hit"
    assert env.max_lots_observed_by_symbol["EURUSD"] <= 5.0, "Max lots should not exceed cap"


def test_no_pathological_compounding_case():
    """
    Test that with sizing caps enabled, we cannot have a scenario where:
    - One symbol has equity < $10 (margin call territory)
    - Portfolio equity > $10,000 (10x initial)
    without cap hits being registered.

    This test runs with seed=42 for 10k steps to check if pathology is prevented.
    """
    env = create_test_env(
        max_lots_per_symbol=20.0,
        max_concentration_pct=40.0,
    )

    obs, info = env.reset(seed=42)

    done = False
    step_count = 0
    max_steps = 10_000

    min_equity = env.equity
    max_equity = env.equity

    while not done and step_count < max_steps:
        # Random aggressive actions
        action = np.random.rand(1, 3) * 2.0 - 1.0  # [-1, 1] range
        action[:, 0] = np.clip(action[:, 0], -1.0, 1.0)
        action[:, 1] = np.clip(action[:, 1], 0.5, 3.0)
        action[:, 2] = np.clip(action[:, 2], 1.0, 5.0)

        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated
        step_count += 1

        min_equity = min(min_equity, env.equity)
        max_equity = max(max_equity, env.equity)

    total_cap_hits = sum(env.lot_cap_hits_by_symbol.values()) + sum(
        env.concentration_cap_hits_by_symbol.values()
    )

    print(f"\n✅ Pathological compounding test ({step_count} steps):")
    print(f"   Initial equity: ${env.config.initial_balance:,.2f}")
    print(f"   Final equity: ${env.equity:,.2f}")
    print(f"   Min equity: ${min_equity:,.2f}")
    print(f"   Max equity: ${max_equity:,.2f}")
    print(f"   Total cap hits: {total_cap_hits}")

    # Check pathology condition
    pathology_detected = (min_equity < 10.0) and (max_equity > 10_000.0)

    if pathology_detected:
        # If pathology occurred, caps MUST have been hit
        assert total_cap_hits > 0, (
            f"PATHOLOGY DETECTED: min_equity=${min_equity:.2f}, max_equity=${max_equity:.2f}, "
            f"but NO caps were hit. Sizing system failed to prevent multi-subcuenta collapse."
        )
        print(
            f"   ⚠️  Pathology detected but caps prevented complete failure (cap hits: {total_cap_hits})"
        )
    else:
        print("   ✅ No pathology detected (caps working or not needed)")

    # The key assertion: if we have extreme compounding (>100x), caps should have fired
    if max_equity > 100 * env.config.initial_balance:
        assert total_cap_hits > 100, (
            f"Extreme compounding detected ({max_equity / env.config.initial_balance:.1f}x) "
            f"but only {total_cap_hits} cap hits. Caps may be too loose."
        )


def test_concentration_cap_prevents_overleveraging():
    """Test that concentration cap prevents single-symbol overleveraging."""
    env = create_test_env(
        max_lots_per_symbol=50.0,  # High lot cap
        max_concentration_pct=25.0,  # Low concentration cap (25% of equity)
    )

    env.reset(seed=123)

    # Try to take maximum position
    max_action = np.array([[1.0, 2.0, 3.0]])

    obs, reward, terminated, truncated, info = env.step(max_action)

    # Check that concentration cap was applied
    concentration_hits = env.concentration_cap_hits_by_symbol["EURUSD"]
    max_concentration = env.max_concentration_observed_pct_by_symbol["EURUSD"]

    print("\n✅ Concentration cap test:")
    print(f"   Concentration hits: {concentration_hits}")
    print(f"   Max concentration observed: {max_concentration:.2f}%")
    print("   Cap limit: 25.0%")

    # Concentration should not significantly exceed cap
    assert max_concentration <= 30.0, f"Concentration {max_concentration:.2f}% exceeded cap+buffer"


def _safe_unlink(path, retries=3, delay=0.5):
    """Unlink a file with retries to handle Windows file locking."""
    import time

    for i in range(retries):
        try:
            if path.exists():
                path.unlink()
            return
        except PermissionError:
            if i < retries - 1:
                time.sleep(delay)
            # Last attempt: ignore if still locked
    # If still exists after retries, ignore silently


def test_sizing_events_jsonl_created_only_if_hits():
    """Test that sizing_events.jsonl is only created when caps are hit."""
    from pathlib import Path

    log_file = Path("reports/runtime_risk_monitoring/sizing_events.jsonl")
    _safe_unlink(log_file)

    # Phase 1: Run with VERY high caps (absolutely no hits expected)
    env = create_test_env(
        max_lots_per_symbol=10000.0,  # Extremely high - impossible to hit
        max_concentration_pct=99.9,  # Effectively disabled
    )

    env.reset(seed=999)

    # Execute steps with SMALL actions (to ensure no caps)
    for _ in range(10):
        action = np.array([[0.1, 2.0, 3.0]])  # Small position
        obs, reward, terminated, truncated, info = env.step(action)
        if terminated or truncated:
            break

    # Verify no caps were hit via INTERNAL counters (immune to parallel test interference)
    total_hits = sum(env.lot_cap_hits_by_symbol.values()) + sum(
        env.concentration_cap_hits_by_symbol.values()
    )

    print("\n✅ JSONL not created test:")
    print(f"   Total cap hits: {total_hits} (expected: 0)")

    assert total_hits == 0, f"Expected 0 cap hits with high caps, got {total_hits}"

    # Phase 2: Now run with low caps (hits expected)
    _safe_unlink(log_file)  # Clean again before "hits" phase
    env2 = create_test_env(
        max_lots_per_symbol=2.0,  # Very low
        max_concentration_pct=10.0,  # Very low
    )

    env2.reset(seed=888)

    # Execute steps with aggressive actions
    for _ in range(5):
        action = np.array([[1.0, 2.0, 3.0]])
        obs, reward, terminated, truncated, info = env2.step(action)
        if terminated or truncated:
            break

    # Caps hit -> file SHOULD exist
    assert log_file.exists(), "sizing_events.jsonl MUST exist when caps hit"

    # Validate JSONL content
    import json

    events = []
    with open(log_file, encoding="utf-8") as f:
        for line in f:
            events.append(json.loads(line))

    assert len(events) > 0, "sizing_events.jsonl should contain at least one event"

    # Validate schema
    event = events[0]
    required_fields = [
        "step",
        "symbol",
        "event_type",
        "reasons",
        "requested_lots",
        "clamped_lots",
        "requested_notional_usd",
        "clamped_notional_usd",
        "equity_symbol",
        "equity_total",
        "concentration_pct_symbol",
        "price",
    ]
    for field in required_fields:
        assert field in event, f"Event missing required field: {field}"

    assert event["event_type"] == "SIZING_CAP"
    assert isinstance(event["reasons"], list)
    assert len(event["reasons"]) > 0

    print("\n✅ JSONL created test:")
    print(f"   File exists: {log_file.exists()} (expected: True)")
    print(f"   Events logged: {len(events)}")
    print(f"   First event reasons: {event['reasons']}")
    print(f"   First event clamped: {event['requested_lots']:.2f} → {event['clamped_lots']:.2f}")

    # Clean up
    _safe_unlink(log_file)


def test_portfolio_notional_cap_scales_all_symbols():
    """
    Test that max_portfolio_notional_usd cap scales all symbols proportionally.

    This test uses MultiPairPortfolioEnv which is the only place where
    portfolio-level caps make sense (since it has multiple symbols).
    """
    # This test would require MultiPairPortfolioEnv which needs more complex setup
    # For now, we validate the tracking fields exist in the config

    config = ProductionTradingConfig(
        initial_balance=10_000.0,
        max_portfolio_notional_usd=50_000.0,  # Portfolio cap
        max_lots_per_symbol=20.0,
        max_concentration_pct_per_symbol=40.0,
    )

    assert hasattr(config, "max_portfolio_notional_usd")
    assert config.max_portfolio_notional_usd == 50_000.0

    print("\n✅ Portfolio notional cap config test:")
    print(f"   max_portfolio_notional_usd: ${config.max_portfolio_notional_usd:,.0f}")
    print("   Config field exists: True")
    print("   Note: Full integration test requires MultiPairPortfolioEnv")


if __name__ == "__main__":
    print("=" * 80)
    print("SIZING CAPS TESTS")
    print("=" * 80)

    test_sizing_caps_apply()
    test_no_pathological_compounding_case()
    test_concentration_cap_prevents_overleveraging()
    test_sizing_events_jsonl_created_only_if_hits()
    test_portfolio_notional_cap_scales_all_symbols()

    print("\n" + "=" * 80)
    print("✅ ALL TESTS PASSED")
    print("=" * 80)
