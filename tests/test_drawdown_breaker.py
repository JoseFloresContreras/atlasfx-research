"""
Tests for DrawdownCircuitBreaker

Validates:
- Mode transitions with synthetic equity sequences
- Hysteresis (recovery requires N consecutive steps below recover_dd)
- KILL mode persistence until recovery
"""

import pytest

from atlasfx.risk.drawdown_breaker import DrawdownCircuitBreaker, DrawdownMode


class TestDrawdownCircuitBreaker:
    """Test suite for DrawdownCircuitBreaker."""

    def test_initialization(self):
        """Test breaker initialization with default and custom thresholds."""
        # Default thresholds
        breaker = DrawdownCircuitBreaker()
        assert breaker.warn_dd == 0.15
        assert breaker.reduce_dd == 0.20
        assert breaker.kill_dd == 0.25
        assert breaker.recover_dd == 0.18
        assert breaker.reduce_factor == 0.5
        assert breaker.consecutive_recovery_steps == 5000
        assert breaker.enabled is True
        assert breaker.mode == DrawdownMode.NORMAL

        # Custom thresholds
        breaker = DrawdownCircuitBreaker(
            warn_dd=0.10,
            reduce_dd=0.15,
            kill_dd=0.20,
            recover_dd=0.12,
            reduce_factor=0.3,
            consecutive_recovery_steps=1000,
        )
        assert breaker.warn_dd == 0.10
        assert breaker.reduce_dd == 0.15
        assert breaker.kill_dd == 0.20
        assert breaker.recover_dd == 0.12
        assert breaker.reduce_factor == 0.3
        assert breaker.consecutive_recovery_steps == 1000

    def test_invalid_thresholds(self):
        """Test that invalid thresholds raise ValueError."""
        # warn_dd >= reduce_dd
        with pytest.raises(ValueError, match="Invalid thresholds"):
            DrawdownCircuitBreaker(warn_dd=0.20, reduce_dd=0.15, kill_dd=0.25)

        # reduce_dd >= kill_dd
        with pytest.raises(ValueError, match="Invalid thresholds"):
            DrawdownCircuitBreaker(warn_dd=0.10, reduce_dd=0.25, kill_dd=0.20)

        # recover_dd >= reduce_dd (no hysteresis)
        with pytest.raises(ValueError, match="Invalid recovery"):
            DrawdownCircuitBreaker(warn_dd=0.10, reduce_dd=0.15, kill_dd=0.20, recover_dd=0.16)

        # Invalid reduce_factor
        with pytest.raises(ValueError, match="reduce_factor"):
            DrawdownCircuitBreaker(reduce_factor=1.5)

    def test_disabled_breaker(self):
        """Test that disabled breaker always returns NORMAL mode."""
        breaker = DrawdownCircuitBreaker(enabled=False)

        # Even with severe drawdown, should stay NORMAL
        state = breaker.update(step=1, equity=1000.0)
        assert state["mode"] == DrawdownMode.NORMAL.value
        assert state["scale_factor"] == 1.0
        assert state["force_flat"] is False

        state = breaker.update(step=2, equity=500.0)  # -50% DD
        assert state["mode"] == DrawdownMode.NORMAL.value
        assert state["scale_factor"] == 1.0
        assert state["force_flat"] is False

    def test_normal_mode(self):
        """Test NORMAL mode (dd < warn_dd)."""
        breaker = DrawdownCircuitBreaker(warn_dd=0.15, reduce_dd=0.20, kill_dd=0.25)

        # Start with peak
        state = breaker.update(step=1, equity=10000.0)
        assert state["mode"] == DrawdownMode.NORMAL.value
        assert state["dd"] == 0.0
        assert state["scale_factor"] == 1.0
        assert state["force_flat"] is False

        # Small drawdown (10%)
        state = breaker.update(step=2, equity=9000.0)
        assert state["mode"] == DrawdownMode.NORMAL.value
        assert pytest.approx(state["dd"], abs=1e-6) == 0.10
        assert state["scale_factor"] == 1.0
        assert state["force_flat"] is False

    def test_warn_mode(self):
        """Test WARN mode (warn_dd <= dd < reduce_dd)."""
        breaker = DrawdownCircuitBreaker(warn_dd=0.15, reduce_dd=0.20, kill_dd=0.25)

        # Set peak
        breaker.update(step=1, equity=10000.0)

        # Trigger WARN (16% DD)
        state = breaker.update(step=2, equity=8400.0)
        assert state["mode"] == DrawdownMode.WARN.value
        assert pytest.approx(state["dd"], abs=1e-6) == 0.16
        assert state["scale_factor"] == 1.0  # No action scaling in WARN
        assert state["force_flat"] is False

    def test_reduce_mode(self):
        """Test REDUCE mode (reduce_dd <= dd < kill_dd)."""
        breaker = DrawdownCircuitBreaker(
            warn_dd=0.15, reduce_dd=0.20, kill_dd=0.25, reduce_factor=0.5
        )

        # Set peak
        breaker.update(step=1, equity=10000.0)

        # Trigger REDUCE (22% DD)
        state = breaker.update(step=2, equity=7800.0)
        assert state["mode"] == DrawdownMode.REDUCE.value
        assert pytest.approx(state["dd"], abs=1e-6) == 0.22
        assert state["scale_factor"] == 0.5
        assert state["force_flat"] is False

    def test_kill_mode(self):
        """Test KILL mode (dd >= kill_dd)."""
        breaker = DrawdownCircuitBreaker(warn_dd=0.15, reduce_dd=0.20, kill_dd=0.25)

        # Set peak
        breaker.update(step=1, equity=10000.0)

        # Trigger KILL (30% DD)
        state = breaker.update(step=2, equity=7000.0)
        assert state["mode"] == DrawdownMode.KILL.value
        assert pytest.approx(state["dd"], abs=1e-6) == 0.30
        assert state["scale_factor"] == 0.0
        assert state["force_flat"] is True

    def test_hysteresis_from_reduce(self):
        """Test hysteresis when recovering from REDUCE mode."""
        breaker = DrawdownCircuitBreaker(
            warn_dd=0.15,
            reduce_dd=0.20,
            kill_dd=0.25,
            recover_dd=0.18,
            consecutive_recovery_steps=3,  # Short for testing
        )

        # Set peak and enter REDUCE
        breaker.update(step=1, equity=10000.0)
        state = breaker.update(step=2, equity=7800.0)  # 22% DD
        assert state["mode"] == DrawdownMode.REDUCE.value

        # Recover below recover_dd but not sustained
        state = breaker.update(step=3, equity=8300.0)  # 17% DD (below 18%)
        assert state["mode"] == DrawdownMode.REDUCE.value  # Still in REDUCE
        assert state["steps_below_recover"] == 1

        # Fluctuate above recover_dd (resets counter)
        state = breaker.update(step=4, equity=8100.0)  # 19% DD (above 18%)
        assert state["mode"] == DrawdownMode.REDUCE.value
        assert state["steps_below_recover"] == 0

        # Sustained recovery for 3 steps
        state = breaker.update(step=5, equity=8300.0)  # 17% DD
        assert state["mode"] == DrawdownMode.REDUCE.value
        assert state["steps_below_recover"] == 1

        state = breaker.update(step=6, equity=8400.0)  # 16% DD
        assert state["mode"] == DrawdownMode.REDUCE.value
        assert state["steps_below_recover"] == 2

        state = breaker.update(step=7, equity=8500.0)  # 15% DD
        assert state["mode"] == DrawdownMode.NORMAL.value  # Recovered!
        assert state["recovered"] is True
        assert state["scale_factor"] == 1.0

    def test_hysteresis_from_kill(self):
        """Test hysteresis when recovering from KILL mode."""
        breaker = DrawdownCircuitBreaker(
            warn_dd=0.15,
            reduce_dd=0.20,
            kill_dd=0.25,
            recover_dd=0.18,
            consecutive_recovery_steps=2,  # Short for testing
        )

        # Set peak and enter KILL
        breaker.update(step=1, equity=10000.0)
        state = breaker.update(step=2, equity=7000.0)  # 30% DD
        assert state["mode"] == DrawdownMode.KILL.value

        # Partial recovery (still in KILL)
        state = breaker.update(step=3, equity=7500.0)  # 25% DD (still >= kill_dd)
        assert state["mode"] == DrawdownMode.KILL.value

        # Drop below kill_dd (25%) but above reduce_dd (20%)
        # Should transition to REDUCE since dd is in [reduce_dd, kill_dd) range
        state = breaker.update(step=4, equity=7700.0)  # 23% DD (below kill_dd, above reduce_dd)
        assert state["mode"] == DrawdownMode.REDUCE.value  # Now in REDUCE

        # Continue recovery in REDUCE range
        state = breaker.update(step=5, equity=8100.0)  # 19% DD (still in reduce range)
        assert state["mode"] == DrawdownMode.REDUCE.value  # Still in REDUCE

        # Sustained recovery below recover_dd
        state = breaker.update(step=6, equity=8300.0)  # 17% DD
        assert state["mode"] == DrawdownMode.REDUCE.value
        assert state["steps_below_recover"] == 1

        state = breaker.update(step=7, equity=8400.0)  # 16% DD
        assert state["mode"] == DrawdownMode.NORMAL.value  # Recovered!
        assert state["recovered"] is True

    def test_kill_mode_persistence(self):
        """Test that KILL mode persists until sustained recovery."""
        breaker = DrawdownCircuitBreaker(
            warn_dd=0.15,
            reduce_dd=0.20,
            kill_dd=0.25,
            recover_dd=0.18,
            consecutive_recovery_steps=3,
        )

        # Enter KILL
        breaker.update(step=1, equity=10000.0)
        breaker.update(step=2, equity=7000.0)  # 30% DD

        # Fluctuate but stay in restrictive modes
        for i in range(10):
            equity = 7000.0 + (i * 100.0)  # Slowly recover
            state = breaker.update(step=3 + i, equity=equity)
            if state["dd"] >= 0.25:
                assert state["mode"] == DrawdownMode.KILL.value
            elif state["dd"] >= 0.20:
                assert state["mode"] in (DrawdownMode.REDUCE.value, DrawdownMode.KILL.value)

    def test_peak_tracking(self):
        """Test that peak equity is tracked correctly."""
        breaker = DrawdownCircuitBreaker()

        # Set initial peak
        state = breaker.update(step=1, equity=10000.0)
        assert state["peak_equity"] == 10000.0

        # New peak
        state = breaker.update(step=2, equity=12000.0)
        assert state["peak_equity"] == 12000.0

        # Drawdown (peak unchanged)
        state = breaker.update(step=3, equity=10000.0)
        assert state["peak_equity"] == 12000.0
        assert pytest.approx(state["dd"], abs=1e-6) == (1.0 - 10000.0 / 12000.0)

        # New all-time peak
        state = breaker.update(step=4, equity=15000.0)
        assert state["peak_equity"] == 15000.0
        assert state["dd"] == 0.0

    def test_reset(self):
        """Test breaker reset."""
        breaker = DrawdownCircuitBreaker()

        # Build up state
        breaker.update(step=1, equity=10000.0)
        breaker.update(step=2, equity=7000.0)  # Enter KILL
        breaker.update(step=3, equity=8000.0)

        assert breaker.peak_equity == 10000.0
        assert breaker.current_equity == 8000.0
        assert breaker.mode != DrawdownMode.NORMAL

        # Reset
        breaker.reset()

        assert breaker.peak_equity == 0.0
        assert breaker.current_equity == 0.0
        assert breaker.current_dd == 0.0
        assert breaker.mode == DrawdownMode.NORMAL
        assert breaker.steps_below_recover == 0
        assert breaker.current_step == 0

    def test_mode_transitions_sequence(self):
        """Test complete sequence of mode transitions."""
        breaker = DrawdownCircuitBreaker(
            warn_dd=0.15,
            reduce_dd=0.20,
            kill_dd=0.25,
            recover_dd=0.18,
            consecutive_recovery_steps=2,
        )

        # NORMAL -> WARN
        breaker.update(step=1, equity=10000.0)
        state = breaker.update(step=2, equity=8400.0)  # 16% DD
        assert state["mode"] == DrawdownMode.WARN.value

        # WARN -> REDUCE
        state = breaker.update(step=3, equity=7800.0)  # 22% DD
        assert state["mode"] == DrawdownMode.REDUCE.value

        # REDUCE -> KILL
        state = breaker.update(step=4, equity=7000.0)  # 30% DD
        assert state["mode"] == DrawdownMode.KILL.value

        # KILL -> REDUCE (drop below kill_dd)
        state = breaker.update(step=5, equity=7800.0)  # 22% DD
        assert state["mode"] == DrawdownMode.REDUCE.value

        # REDUCE -> NORMAL (sustained recovery)
        state = breaker.update(step=6, equity=8300.0)  # 17% DD
        assert state["mode"] == DrawdownMode.REDUCE.value
        assert state["steps_below_recover"] == 1

        state = breaker.update(step=7, equity=8400.0)  # 16% DD
        assert state["mode"] == DrawdownMode.NORMAL.value
        assert state["recovered"] is True

    def test_get_stats(self):
        """Test get_stats method."""
        breaker = DrawdownCircuitBreaker()

        breaker.update(step=1, equity=10000.0)
        breaker.update(step=2, equity=7900.0)  # 21% DD (in REDUCE range)

        stats = breaker.get_stats()

        assert stats["enabled"] is True
        assert stats["mode"] == DrawdownMode.REDUCE.value
        assert stats["peak_equity"] == 10000.0
        assert stats["current_equity"] == 7900.0
        assert pytest.approx(stats["current_dd"], abs=1e-6) == 0.21
        assert stats["current_step"] == 2
