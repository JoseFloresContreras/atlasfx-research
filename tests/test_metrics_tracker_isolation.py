"""
Test: Metrics Tracker Isolation Between Episodes

This test ensures that TradingMetricsTracker correctly resets state between episodes,
preventing data leakage or accumulation bugs.

Requirements:
- Tracker must create new instance on env.reset()
- equity_curve must not accumulate across episodes
- Returns must be calculated correctly per episode
- No state from previous episode should affect next episode

Author: AtlasFX Forensics
Date: 2025-12-30
"""

import numpy as np
import pandas as pd
import pytest
import torch

from atlasfx.environments.trading_env import ProductionTradingConfig, ProductionTradingEnv
from atlasfx.models.sac import SAC


@pytest.fixture
def test_data():
    """Generate synthetic test data with predictable behavior."""
    n_bars = 2000  # Enough for 2+ episodes

    data = pd.DataFrame(
        {
            "timestamp": range(n_bars),
            "symbol": ["eurusd"] * n_bars,
            # Price columns (eurusd-pair)
            "eurusd-pair | open": np.linspace(1.1000, 1.1200, n_bars),
            "eurusd-pair | high": np.linspace(1.1020, 1.1220, n_bars),
            "eurusd-pair | low": np.linspace(1.0980, 1.1180, n_bars),
            "eurusd-pair | close": np.linspace(1.1010, 1.1210, n_bars),
            # ATR in PRICE units (required by ProductionTradingEnv)
            "[Feature] eurusd-pair | atr_14": [0.0015] * n_bars,  # ~15 pips
            # Feature columns (minimal set for state construction)
            "eurusd-pair | ema_10": np.linspace(1.1000, 1.1200, n_bars),
            "eurusd-pair | ema_30": np.linspace(1.1000, 1.1200, n_bars),
            "eurusd-pair | rsi_14": [50.0] * n_bars,
        }
    )

    return data


@pytest.fixture
def env(test_data):
    """Create ProductionTradingEnv with test data."""
    config = ProductionTradingConfig(
        initial_balance=10_000.0,
        max_risk_per_trade_pct=0.02,
        episode_length=500,
        commission_per_lot=2.5,
        pip_value_per_lot=10.0,
        spread_pips=0.2,
        use_vae_features=False,
        # Disable anti-overtrading so untrained SAC agent can trade freely
        position_dead_zone=0.0,
        min_hold_period=0,
    )

    symbols = ["eurusd-pair"]
    price_cols = {
        "eurusd-pair": {
            "open": "eurusd-pair | open",
            "high": "eurusd-pair | high",
            "low": "eurusd-pair | low",
            "close": "eurusd-pair | close",
        }
    }

    env = ProductionTradingEnv(
        data=test_data,
        symbols=symbols,
        price_cols=price_cols,
        config=config,
    )

    return env


@pytest.fixture
def agent(env):
    """Create dummy SAC agent for testing."""
    agent = SAC(
        state_dim=env.state_dim,
        action_dim=3,
        device="cpu",
    )
    agent.eval()
    return agent


def run_episode(env, agent, episode_num: int) -> dict:
    """
    Run single episode and collect metrics.

    Returns:
        dict with tracker_id, equity_curve_len, initial, final, computed_return
    """
    obs, info = env.reset()

    # Capture tracker state after reset
    tracker_id_after_reset = id(env.metrics_tracker)
    equity_len_after_reset = len(env.metrics_tracker.equity_curve)
    initial_equity = (
        env.metrics_tracker.equity_curve[0] if env.metrics_tracker.equity_curve else None
    )

    # Run episode
    done = False
    steps = 0
    with torch.no_grad():
        while not done and steps < 500:
            action = agent.select_action(obs, deterministic=True)
            obs, reward, terminated, truncated, step_info = env.step(action)
            done = terminated or truncated
            steps += 1

    # Capture tracker state before compute_metrics
    equity_len_before_compute = len(env.metrics_tracker.equity_curve)
    final_equity = (
        env.metrics_tracker.equity_curve[-1] if env.metrics_tracker.equity_curve else None
    )

    # Compute metrics
    metrics = env.metrics_tracker.compute_all_metrics()
    computed_return = metrics.get("total_return_pct", 0.0)

    return {
        "episode": episode_num,
        "tracker_id_after_reset": tracker_id_after_reset,
        "equity_len_after_reset": equity_len_after_reset,
        "initial_equity": initial_equity,
        "equity_len_before_compute": equity_len_before_compute,
        "final_equity": final_equity,
        "computed_return": computed_return,
        "steps": steps,
    }


def test_tracker_resets_between_episodes(env, agent):
    """
    Test that TradingMetricsTracker correctly resets state between episodes.

    Critical checks:
    1. tracker_id changes (new instance created)
    2. equity_curve length is 1 after reset (only initial value)
    3. equity_curve length matches episode steps + 1
    4. initial equity is always $10,000
    5. Returns are calculated correctly per episode
    6. No accumulation across episodes
    """
    # Run 2 consecutive episodes
    ep1_data = run_episode(env, agent, episode_num=1)
    ep2_data = run_episode(env, agent, episode_num=2)

    print("\n" + "=" * 80)
    print("EPISODE 1 STATE:")
    print("=" * 80)
    for k, v in ep1_data.items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 80)
    print("EPISODE 2 STATE:")
    print("=" * 80)
    for k, v in ep2_data.items():
        print(f"  {k}: {v}")

    # ========================================================================
    # CRITICAL ASSERTIONS
    # ========================================================================

    # 1. Tracker ID must change (new instance created)
    assert ep1_data["tracker_id_after_reset"] != ep2_data["tracker_id_after_reset"], (
        "Tracker ID did not change between episodes! "
        "This means the same tracker instance is being reused."
    )

    # 2. Equity curve length after reset must be 1 (only initial value)
    assert ep1_data["equity_len_after_reset"] == 1, (
        f"Episode 1: equity_curve_len after reset is {ep1_data['equity_len_after_reset']}, expected 1"
    )
    assert ep2_data["equity_len_after_reset"] == 1, (
        f"Episode 2: equity_curve_len after reset is {ep2_data['equity_len_after_reset']}, expected 1. "
        f"This indicates data accumulation from previous episode!"
    )

    # 3. Equity curve length before compute must be steps + 1
    expected_len_ep1 = ep1_data["steps"] + 1
    expected_len_ep2 = ep2_data["steps"] + 1

    assert ep1_data["equity_len_before_compute"] == expected_len_ep1, (
        f"Episode 1: equity_curve_len is {ep1_data['equity_len_before_compute']}, "
        f"expected {expected_len_ep1} (steps={ep1_data['steps']} + 1)"
    )
    assert ep2_data["equity_len_before_compute"] == expected_len_ep2, (
        f"Episode 2: equity_curve_len is {ep2_data['equity_len_before_compute']}, "
        f"expected {expected_len_ep2} (steps={ep2_data['steps']} + 1). "
        f"Possible accumulation from episode 1!"
    )

    # 4. Initial equity must always be $10,000
    assert ep1_data["initial_equity"] == 10_000.0, (
        f"Episode 1: initial_equity is {ep1_data['initial_equity']}, expected 10000.0"
    )
    assert ep2_data["initial_equity"] == 10_000.0, (
        f"Episode 2: initial_equity is {ep2_data['initial_equity']}, expected 10000.0. "
        f"This may indicate state leakage from episode 1!"
    )

    # 5. Verify return calculation is correct
    # Return should be: (final - initial) / initial * 100
    expected_return_ep1 = (
        (ep1_data["final_equity"] - ep1_data["initial_equity"]) / ep1_data["initial_equity"]
    ) * 100
    expected_return_ep2 = (
        (ep2_data["final_equity"] - ep2_data["initial_equity"]) / ep2_data["initial_equity"]
    ) * 100

    assert abs(ep1_data["computed_return"] - expected_return_ep1) < 1e-6, (
        f"Episode 1: computed return is {ep1_data['computed_return']:.6f}%, "
        f"expected {expected_return_ep1:.6f}%"
    )
    assert abs(ep2_data["computed_return"] - expected_return_ep2) < 1e-6, (
        f"Episode 2: computed return is {ep2_data['computed_return']:.6f}%, "
        f"expected {expected_return_ep2:.6f}%"
    )

    # 6. Episode 2 should NOT be affected by Episode 1's performance
    # This is implicitly tested by checks 1-5, but we add explicit verification:
    # If there was accumulation, equity_len_after_reset for ep2 would be > 1
    # If there was ID reuse, tracker_id would match
    # If initial wasn't reset, it would equal ep1's final

    assert ep2_data["initial_equity"] != ep1_data["final_equity"], (
        f"Episode 2's initial equity ({ep2_data['initial_equity']}) "
        f"equals Episode 1's final equity ({ep1_data['final_equity']}). "
        f"This indicates the tracker is NOT resetting properly!"
    )

    print("\n" + "=" * 80)
    print("✅ ALL CHECKS PASSED - Tracker isolation confirmed!")
    print("=" * 80)
    print(
        f"  ✓ Tracker ID changes: {ep1_data['tracker_id_after_reset']} → {ep2_data['tracker_id_after_reset']}"
    )
    print(
        f"  ✓ Equity curve resets: {ep1_data['equity_len_after_reset']} → {ep2_data['equity_len_after_reset']} (both = 1)"
    )
    print(
        f"  ✓ Length matches steps: Ep1={ep1_data['equity_len_before_compute']} (steps+1), Ep2={ep2_data['equity_len_before_compute']} (steps+1)"
    )
    print(
        f"  ✓ Initial always $10k: Ep1={ep1_data['initial_equity']}, Ep2={ep2_data['initial_equity']}"
    )
    print(
        f"  ✓ Returns calculated correctly: Ep1={ep1_data['computed_return']:.4f}%, Ep2={ep2_data['computed_return']:.4f}%"
    )
    print("  ✓ No state leakage: Ep2 initial ≠ Ep1 final")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
