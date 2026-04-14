"""
AtlasFX – Portfolio Drawdown Circuit Breaker

Tracks portfolio-level drawdown and applies risk controls:
- NORMAL: No restrictions
- WARN: Log warnings only
- REDUCE: Scale actions by configurable factor (default 0.5)
- KILL: Force all positions flat (action = 0.0)

Includes hysteresis to prevent rapid mode oscillation.
"""

from __future__ import annotations

from enum import Enum

from atlasfx.utils.logging import get_logger


logger = get_logger(__name__)


class DrawdownMode(Enum):
    """Drawdown circuit breaker modes."""

    NORMAL = "NORMAL"
    WARN = "WARN"
    REDUCE = "REDUCE"
    KILL = "KILL"


class DrawdownCircuitBreaker:
    """
    Portfolio-level drawdown circuit breaker with hysteresis.

    Tracks peak equity and current equity to compute drawdown ratio.
    Applies progressive risk controls based on drawdown thresholds.

    Modes:
        NORMAL: dd < warn_dd (scale_factor=1.0)
        WARN: warn_dd <= dd < reduce_dd (scale_factor=1.0, log warnings)
        REDUCE: reduce_dd <= dd < kill_dd (scale_factor=reduce_factor)
        KILL: dd >= kill_dd (force_flat=True, action=0.0)

    Hysteresis:
        When in REDUCE or KILL, recovery requires dd <= recover_dd for
        consecutive_recovery_steps before returning to NORMAL.

    Args:
        warn_dd: Warning threshold (default: 0.15 = -15%)
        reduce_dd: Reduction threshold (default: 0.20 = -20%)
        kill_dd: Kill threshold (default: 0.25 = -25%)
        recover_dd: Recovery threshold (default: 0.18 = -18%)
        reduce_factor: Action scaling factor in REDUCE mode (default: 0.5)
        consecutive_recovery_steps: Steps below recover_dd for recovery (default: 5000)
        enabled: Enable/disable breaker (default: True)
    """

    def __init__(
        self,
        warn_dd: float = 0.15,
        reduce_dd: float = 0.20,
        kill_dd: float = 0.25,
        recover_dd: float = 0.18,
        reduce_factor: float = 0.5,
        consecutive_recovery_steps: int = 5000,
        enabled: bool = True,
    ) -> None:
        # Validate thresholds
        if not (0.0 < warn_dd < reduce_dd < kill_dd <= 1.0):
            raise ValueError(
                f"Invalid thresholds: 0 < warn_dd ({warn_dd}) < reduce_dd ({reduce_dd}) "
                f"< kill_dd ({kill_dd}) <= 1.0"
            )
        if not (0.0 <= recover_dd < reduce_dd):
            raise ValueError(
                f"Invalid recovery: recover_dd ({recover_dd}) must be < reduce_dd ({reduce_dd})"
            )
        if not (0.0 < reduce_factor <= 1.0):
            raise ValueError(f"reduce_factor ({reduce_factor}) must be in (0, 1]")

        self.warn_dd = warn_dd
        self.reduce_dd = reduce_dd
        self.kill_dd = kill_dd
        self.recover_dd = recover_dd
        self.reduce_factor = reduce_factor
        self.consecutive_recovery_steps = consecutive_recovery_steps
        self.enabled = enabled

        # State
        self.peak_equity = 0.0
        self.current_equity = 0.0
        self.current_dd = 0.0
        self.mode = DrawdownMode.NORMAL
        self.steps_below_recover = 0
        self.current_step = 0

        # Transition tracking
        self.last_mode = DrawdownMode.NORMAL
        self.mode_entry_step = 0

        # Previous enforcement state (for change detection)
        self.prev_mode = DrawdownMode.NORMAL
        self.prev_scale_factor = 1.0
        self.prev_force_flat = False

        # Detailed counters
        self.steps_in_normal = 0
        self.steps_in_warn = 0
        self.steps_in_reduce = 0
        self.steps_in_kill = 0
        self.first_warn_step = None
        self.first_reduce_step = None
        self.first_kill_step = None
        self.dd_at_first_kill = None
        self.equity_at_first_kill = None
        self.current_consecutive_kill_steps = 0
        self.max_consecutive_kill_steps = 0

        logger.info(
            f"DrawdownCircuitBreaker initialized:\n"
            f"  Enabled: {enabled}\n"
            f"  Thresholds: warn={warn_dd:.1%}, reduce={reduce_dd:.1%}, "
            f"kill={kill_dd:.1%}, recover={recover_dd:.1%}\n"
            f"  Reduce factor: {reduce_factor}\n"
            f"  Recovery steps: {consecutive_recovery_steps}"
        )

    def update(self, step: int, equity: float) -> dict:
        """
        Update breaker state with current equity.

        Args:
            step: Current step number
            equity: Current portfolio equity

        Returns:
            State dict with:
                - mode: Current DrawdownMode
                - dd: Current drawdown ratio
                - peak_equity: All-time peak equity
                - current_equity: Current equity
                - scale_factor: Action scaling factor
                - force_flat: Whether to force flat positions
                - recovered: Whether recovery condition was just met
                - steps_below_recover: Consecutive steps below recovery threshold
        """
        if not self.enabled:
            return {
                "mode": DrawdownMode.NORMAL.value,
                "dd": 0.0,
                "peak_equity": equity,
                "current_equity": equity,
                "scale_factor": 1.0,
                "force_flat": False,
                "recovered": False,
                "steps_below_recover": 0,
            }

        self.current_step = step
        self.current_equity = equity

        # Update peak
        peak_updated = False
        if equity > self.peak_equity:
            self.peak_equity = equity
            peak_updated = True

        # Calculate drawdown
        if self.peak_equity > 0:
            self.current_dd = 1.0 - (equity / self.peak_equity)
        else:
            self.current_dd = 0.0

        # Track recovery progress
        recovered = False
        if self.current_dd <= self.recover_dd:
            self.steps_below_recover += 1
        else:
            self.steps_below_recover = 0

        # Determine mode
        self.last_mode = self.mode
        old_mode = self.mode

        if self.mode in (DrawdownMode.REDUCE, DrawdownMode.KILL):
            # In restricted modes, require sustained recovery
            if self.steps_below_recover >= self.consecutive_recovery_steps:
                self.mode = DrawdownMode.NORMAL
                recovered = True
                logger.info(
                    f"🟢 RECOVERY: Drawdown recovered to {self.current_dd:.2%} "
                    f"(held below {self.recover_dd:.1%} for {self.consecutive_recovery_steps} steps). "
                    f"Returning to NORMAL mode at step {step}"
                )
            elif self.current_dd >= self.kill_dd:
                self.mode = DrawdownMode.KILL
            elif self.current_dd >= self.reduce_dd:
                self.mode = DrawdownMode.REDUCE
            # Otherwise stay in current mode
        # In NORMAL or WARN, apply standard thresholds
        elif self.current_dd >= self.kill_dd:
            self.mode = DrawdownMode.KILL
        elif self.current_dd >= self.reduce_dd:
            self.mode = DrawdownMode.REDUCE
        elif self.current_dd >= self.warn_dd:
            self.mode = DrawdownMode.WARN
        else:
            self.mode = DrawdownMode.NORMAL

        # Log mode transitions
        if self.mode != old_mode:
            self.mode_entry_step = step
            self._log_mode_transition(old_mode, self.mode, step)

        # Determine actions
        scale_factor = 1.0
        force_flat = False

        if self.mode == DrawdownMode.REDUCE:
            scale_factor = self.reduce_factor
        elif self.mode == DrawdownMode.KILL:
            force_flat = True
            scale_factor = 0.0

        # Update counters
        if self.mode == DrawdownMode.NORMAL:
            self.steps_in_normal += 1
        elif self.mode == DrawdownMode.WARN:
            self.steps_in_warn += 1
            if self.first_warn_step is None:
                self.first_warn_step = step
        elif self.mode == DrawdownMode.REDUCE:
            self.steps_in_reduce += 1
            if self.first_reduce_step is None:
                self.first_reduce_step = step
        elif self.mode == DrawdownMode.KILL:
            self.steps_in_kill += 1
            if self.first_kill_step is None:
                self.first_kill_step = step
                self.dd_at_first_kill = self.current_dd
                self.equity_at_first_kill = self.current_equity
            self.current_consecutive_kill_steps += 1
            self.max_consecutive_kill_steps = max(
                self.max_consecutive_kill_steps, self.current_consecutive_kill_steps
            )

        # Reset consecutive kill counter if exiting KILL mode
        if old_mode == DrawdownMode.KILL and self.mode != DrawdownMode.KILL:
            self.current_consecutive_kill_steps = 0

        # Determine event type for logging (only log when enforcement state changes)
        event_type = None
        if step == 1:
            event_type = "INIT"
        elif self.mode != old_mode:
            # Mode changed - always log
            if self.mode == DrawdownMode.NORMAL and recovered:
                event_type = "RECOVERY"
            else:
                event_type = "MODE_CHANGE"
        elif (
            self.mode != self.prev_mode
            or scale_factor != self.prev_scale_factor
            or force_flat != self.prev_force_flat
        ):
            # Enforcement state changed (shouldn't happen without mode change, but safety check)
            if force_flat or scale_factor != 1.0:
                event_type = "ENFORCEMENT"
        # else: no change in enforcement state - don't log

        # Update previous state for next iteration
        self.prev_mode = self.mode
        self.prev_scale_factor = scale_factor
        self.prev_force_flat = force_flat

        return {
            "mode": self.mode.value,
            "dd": self.current_dd,
            "peak_equity": self.peak_equity,
            "current_equity": self.current_equity,
            "scale_factor": scale_factor,
            "force_flat": force_flat,
            "recovered": recovered,
            "steps_below_recover": self.steps_below_recover,
            "event_type": event_type,
            "peak_updated": peak_updated,
        }

    def _log_mode_transition(
        self, old_mode: DrawdownMode, new_mode: DrawdownMode, step: int
    ) -> None:
        """Log mode transitions with appropriate urgency."""
        equity_pct = (self.current_equity / self.peak_equity) if self.peak_equity > 0 else 1.0
        equity_pct_str = f"{equity_pct:.2%}"

        if new_mode == DrawdownMode.NORMAL:
            logger.info(
                f"🟢 NORMAL: Drawdown {self.current_dd:.2%} < {self.warn_dd:.1%} "
                f"(equity at {equity_pct_str} of peak ${self.peak_equity:,.2f}) at step {step}"
            )
        elif new_mode == DrawdownMode.WARN:
            logger.warning(
                f"🟡 WARN: Drawdown {self.current_dd:.2%} reached {self.warn_dd:.1%} threshold "
                f"(equity at {equity_pct_str} of peak ${self.peak_equity:,.2f}) at step {step}"
            )
        elif new_mode == DrawdownMode.REDUCE:
            logger.warning(
                f"🟠 REDUCE: Drawdown {self.current_dd:.2%} reached {self.reduce_dd:.1%} threshold. "
                f"Scaling actions by {self.reduce_factor} "
                f"(equity at {equity_pct_str} of peak ${self.peak_equity:,.2f}) at step {step}"
            )
        elif new_mode == DrawdownMode.KILL:
            logger.error(
                f"🔴 KILL: Drawdown {self.current_dd:.2%} exceeded {self.kill_dd:.1%} threshold. "
                f"FORCING ALL POSITIONS FLAT "
                f"(equity at {equity_pct_str} of peak ${self.peak_equity:,.2f}) at step {step}"
            )

    def get_stats(self) -> dict:
        """Get breaker statistics."""
        return {
            "enabled": self.enabled,
            "mode": self.mode.value,
            "current_dd": self.current_dd,
            "peak_equity": self.peak_equity,
            "current_equity": self.current_equity,
            "steps_below_recover": self.steps_below_recover,
            "current_step": self.current_step,
            # Detailed counters
            "steps_in_normal": self.steps_in_normal,
            "steps_in_warn": self.steps_in_warn,
            "steps_in_reduce": self.steps_in_reduce,
            "steps_in_kill": self.steps_in_kill,
            "first_warn_step": self.first_warn_step,
            "first_reduce_step": self.first_reduce_step,
            "first_kill_step": self.first_kill_step,
            "dd_at_first_kill": self.dd_at_first_kill,
            "equity_at_first_kill": self.equity_at_first_kill,
            "max_consecutive_kill_steps": self.max_consecutive_kill_steps,
        }

    def reset(self) -> None:
        """Reset breaker state."""
        self.peak_equity = 0.0
        self.current_equity = 0.0
        self.current_dd = 0.0
        self.mode = DrawdownMode.NORMAL
        self.steps_below_recover = 0
        self.current_step = 0
        self.last_mode = DrawdownMode.NORMAL
        self.mode_entry_step = 0

        # Reset previous state
        self.prev_mode = DrawdownMode.NORMAL
        self.prev_scale_factor = 1.0
        self.prev_force_flat = False

        # Reset counters
        self.steps_in_normal = 0
        self.steps_in_warn = 0
        self.steps_in_reduce = 0
        self.steps_in_kill = 0
        self.first_warn_step = None
        self.first_reduce_step = None
        self.first_kill_step = None
        self.dd_at_first_kill = None
        self.equity_at_first_kill = None
        self.current_consecutive_kill_steps = 0
        self.max_consecutive_kill_steps = 0
