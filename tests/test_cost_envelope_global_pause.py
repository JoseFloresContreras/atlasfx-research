"""Unit tests for Global Pause Fallback mechanism."""

import pytest
from atlasfx.risk.cost_envelope_global_pause import GlobalPauseFallback


def test_global_pause_initialization():
    """Test basic initialization."""
    gp = GlobalPauseFallback(
        window_steps=100,
        trigger_breach_rate=0.10,
        trigger_multi_breach=2,
        cooldown_steps=500,
        enabled=True,
    )

    assert gp.mode == "NORMAL"
    assert gp.pause_entries == 0
    assert gp.pause_steps_total == 0
    assert gp.first_pause_step is None
    assert len(gp.breach_window) == 0
    assert len(gp.transitions) == 0


def test_normal_mode_no_breaches():
    """Test that NORMAL mode persists when no breaches."""
    gp = GlobalPauseFallback(window_steps=10, trigger_breach_rate=0.20)

    # Simulate 20 steps with no breaches
    for step in range(20):
        breaches = {"EURUSD": False, "GBPUSD": False}
        is_paused = gp.check_and_update(breaches, step)

        assert not is_paused
        assert gp.mode == "NORMAL"

    # No transitions should occur
    assert len(gp.transitions) == 0


def test_trigger_on_breach_rate():
    """Test pause trigger when breach rate exceeds threshold."""
    gp = GlobalPauseFallback(
        window_steps=10,
        trigger_breach_rate=0.20,  # 20% threshold
        trigger_multi_breach=99,  # Disable multi-breach trigger (large value)
        cooldown_steps=5,
    )

    # Simulate: 2 breaches at steps 0 and 1, then no more breaches
    for step in range(15):
        breaches = {
            "EURUSD": step in [0, 1],  # 2 breaches at start
            "GBPUSD": False,
        }
        is_paused = gp.check_and_update(breaches, step)

        # First breach triggers pause immediately (breach_rate=100% at step 0)
        if step == 0:
            assert is_paused, "Should pause on first breach (breach_rate=100%)"
            assert gp.mode == "PAUSED"
        elif step < 5:
            # Remains paused during cooldown (steps 1-4)
            assert is_paused
            assert gp.mode == "PAUSED"
        elif step == 5:
            # Cooldown expires at step 5, transitions to NORMAL, returns False
            # (Does NOT re-check trigger on same step)
            assert not is_paused, "Exits pause when cooldown expires"
            assert gp.mode == "NORMAL"
        elif step == 6:
            # Next step re-checks: breach_rate = 2/7 = 28.6% > 20%, re-triggers
            assert is_paused, "Re-triggers on next step after cooldown"
            assert gp.mode == "PAUSED"
        elif step < 11:
            # Second cooldown (steps 7-10)
            assert is_paused
            assert gp.mode == "PAUSED"
        elif step == 11:
            # Second cooldown expires
            assert not is_paused
            assert gp.mode == "NORMAL"
        else:
            # After step 11, breach_rate decays below 20%
            assert not is_paused

    # Verify transitions (NORMAL->PAUSED, PAUSED->NORMAL, NORMAL->PAUSED, PAUSED->NORMAL)
    assert len(gp.transitions) == 4, f"Expected 4 transitions, got {len(gp.transitions)}"


def test_trigger_on_multi_breach():
    """Test pause trigger when multiple symbols breach simultaneously."""
    # Use enabled=False temporarily to bypass validation, then manually enable
    # Actually, let's just use a very high but valid threshold
    gp = GlobalPauseFallback(
        window_steps=10,
        trigger_breach_rate=1.0,  # 100% threshold (only triggers if ALL steps breach)
        trigger_multi_breach=2,  # Trigger on 2+ symbols
        cooldown_steps=5,
    )

    # Step 0: Only 1 symbol breaches (no multi-breach trigger, but breach_rate=100%)
    # This will trigger on breach_rate! Need to avoid this edge case.
    # Solution: Pre-fill window with non-breach steps
    for i in range(5):
        gp.check_and_update({"EURUSD": False, "GBPUSD": False, "USDJPY": False}, i)

    # Now breach_rate starts low
    # Step 5: Only 1 symbol breaches (no trigger)
    breaches = {"EURUSD": True, "GBPUSD": False, "USDJPY": False}
    is_paused = gp.check_and_update(breaches, 5)
    assert not is_paused, (
        f"Should NOT pause with only 1 symbol breaching (rate={gp.get_rolling_breach_rate():.2f})"
    )
    assert gp.mode == "NORMAL"

    # Step 6: 2 symbols breach (trigger!)
    breaches = {"EURUSD": True, "GBPUSD": True, "USDJPY": False}
    is_paused = gp.check_and_update(breaches, 6)
    assert is_paused, "Should pause with 2 symbols breaching"
    assert gp.mode == "PAUSED"

    # Verify transition
    assert len(gp.transitions) == 1
    assert gp.transitions[0]["from_mode"] == "NORMAL"
    assert gp.transitions[0]["to_mode"] == "PAUSED"


def test_cooldown_behavior():
    """Test that pause lasts for cooldown_steps."""
    gp = GlobalPauseFallback(
        window_steps=5,
        trigger_breach_rate=0.20,
        trigger_multi_breach=99,
        cooldown_steps=10,
    )

    # Trigger pause at step 0
    breaches = {"EURUSD": True}
    gp.check_and_update(breaches, 0)
    assert gp.mode == "PAUSED"
    pause_start_step = 0

    # Verify pause lasts for cooldown_steps
    for step in range(1, 10):
        breaches = {"EURUSD": False}  # No more breaches
        is_paused = gp.check_and_update(breaches, step)

        steps_since_pause = step - pause_start_step
        if steps_since_pause < 10:
            assert is_paused
            assert gp.mode == "PAUSED"
        else:
            assert not is_paused
            assert gp.mode == "NORMAL"

    # Step 10: Should transition back to NORMAL
    is_paused = gp.check_and_update({"EURUSD": False}, 10)
    assert not is_paused
    assert gp.mode == "NORMAL"

    # Verify both transitions
    assert len(gp.transitions) == 2
    assert gp.transitions[0]["to_mode"] == "PAUSED"
    assert gp.transitions[1]["to_mode"] == "NORMAL"


def test_rolling_window_tracking():
    """Test rolling window maintains correct size."""
    gp = GlobalPauseFallback(window_steps=5, trigger_breach_rate=0.99)

    # Fill window
    for step in range(10):
        breaches = {"EURUSD": step % 2 == 0}  # Alternate breaches
        gp.check_and_update(breaches, step)

        # Window should cap at window_steps
        assert len(gp.breach_window) == min(step + 1, 5)


def test_summary_stats():
    """Test summary stats output."""
    gp = GlobalPauseFallback(
        window_steps=10,
        trigger_breach_rate=0.20,
        cooldown_steps=5,
    )

    # Trigger pause
    gp.check_and_update({"EURUSD": True}, 0)

    # Run through cooldown
    for step in range(1, 6):
        gp.check_and_update({"EURUSD": False}, step)

    stats = gp.get_summary_stats()

    assert stats["ce_fallback_mode"] == "global_pause"
    assert stats["ce_global_pause_triggered"] is True
    assert stats["ce_global_pause_entries"] == 1
    assert stats["ce_global_pause_steps_total"] >= 5
    assert stats["ce_global_pause_first_step"] == 0
    assert stats["ce_rolling_breach_rate_any_max"] >= 0.10


def test_disabled_mode():
    """Test that disabled mode never pauses."""
    gp = GlobalPauseFallback(
        window_steps=10,
        trigger_breach_rate=0.01,  # Very low threshold
        cooldown_steps=5,
        enabled=False,  # Disabled!
    )

    # Even with 100% breach rate, should not pause
    for step in range(20):
        breaches = {"EURUSD": True, "GBPUSD": True, "USDJPY": True}
        is_paused = gp.check_and_update(breaches, step)

        assert not is_paused
        assert gp.mode == "NORMAL"

    assert len(gp.transitions) == 0


def test_multiple_pause_cycles():
    """Test multiple PAUSE -> NORMAL -> PAUSE cycles."""
    gp = GlobalPauseFallback(
        window_steps=5,
        trigger_breach_rate=0.40,
        cooldown_steps=3,
    )

    # Cycle 1: Breach at step 0, recover at step 3
    gp.check_and_update({"EURUSD": True, "GBPUSD": True}, 0)  # Trigger
    assert gp.mode == "PAUSED"

    for step in range(1, 4):
        gp.check_and_update({"EURUSD": False}, step)

    assert gp.mode == "NORMAL"

    # Cycle 2: Breach at step 10, recover at step 13
    gp.check_and_update({"EURUSD": True, "GBPUSD": True}, 10)  # Trigger again
    assert gp.mode == "PAUSED"

    for step in range(11, 14):
        gp.check_and_update({"EURUSD": False}, step)

    assert gp.mode == "NORMAL"

    # Should have 4 transitions (NORMAL->PAUSED, PAUSED->NORMAL, NORMAL->PAUSED, PAUSED->NORMAL)
    assert len(gp.transitions) == 4
    assert gp.pause_entries == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
