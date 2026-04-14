"""
Cost Envelope Global Pause Fallback

Implements a "RISK_OFF / GLOBAL_PAUSE" mechanism to prevent CE_v2 from executing
trades during extreme friction regimes where edge becomes negative.

Context:
- CE_v2 (per_symbol) validated under normal friction
- Sensitivity analysis showed catastrophic failure under high friction:
  sm=1.25 cm=1.5 sl=0.05 → CE_v2 ROI ≈ -27% (vs NONE ROI ≈ +893%)
- Root cause: Slippage amplifies losses 6.9x under high spread
- Mitigation: Pause ALL trading when friction regime becomes extreme

Trigger Conditions:
1. Rolling breach rate: (breach_steps_any / window) >= threshold
2. Multi-breach: >=N symbols breaching simultaneously

When triggered:
- GLOBAL_PAUSE mode: actions=0 for ALL symbols (not just breaching)
- Duration: cooldown_steps
- Logging: Only transitions (NORMAL→PAUSE, PAUSE→NORMAL)

Usage:
    fallback = GlobalPauseFallback(
        window_steps=500,
        trigger_breach_rate=0.08,  # 8% of window steps breaching
        trigger_multi_breach=2,    # >=2 symbols breaching same step
        cooldown_steps=1000,
    )

    # Each step:
    is_paused = fallback.check_and_update(breaches_per_symbol, current_step)

    if is_paused:
        actions = {symbol: 0.0 for symbol in actions}
"""

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class GlobalPauseFallback:
    """Global pause fallback mechanism for extreme friction regimes.

    Tracks rolling breach statistics and triggers a global trading pause
    when friction reaches extreme levels.

    Attributes:
        window_steps: Rolling window size for breach rate calculation
        trigger_breach_rate: Threshold for rolling breach rate (0.0-1.0)
        trigger_multi_breach: Min number of symbols breaching simultaneously
        cooldown_steps: Number of steps to pause after trigger
        enabled: Whether fallback is active
    """

    window_steps: int = 500
    trigger_breach_rate: float = 0.08  # 8% breach rate triggers pause
    trigger_multi_breach: int = 2  # >=2 symbols breaching
    cooldown_steps: int = 1000
    enabled: bool = True

    # State tracking
    mode: Literal["NORMAL", "PAUSED"] = field(default="NORMAL", init=False)
    current_step: int = field(default=0, init=False)
    pause_start_step: int | None = field(default=None, init=False)
    pause_entries: int = field(default=0, init=False)
    pause_steps_total: int = field(default=0, init=False)
    first_pause_step: int | None = field(default=None, init=False)

    # Rolling breach tracking
    breach_window: deque = field(default_factory=deque, init=False)  # deque of booleans
    rolling_breach_rate_max: float = field(default=0.0, init=False)

    # Transition logging
    last_mode: Literal["NORMAL", "PAUSED"] | None = field(default=None, init=False)
    transitions: list[dict] = field(default_factory=list, init=False)

    def __post_init__(self):
        """Initialize breach window."""
        if not self.enabled:
            return

        # Validate parameters
        if not (0.0 <= self.trigger_breach_rate <= 1.0):
            raise ValueError(
                f"trigger_breach_rate must be in [0.0, 1.0], got {self.trigger_breach_rate}"
            )
        if self.window_steps <= 0:
            raise ValueError(f"window_steps must be positive, got {self.window_steps}")
        if self.cooldown_steps <= 0:
            raise ValueError(f"cooldown_steps must be positive, got {self.cooldown_steps}")
        if self.trigger_multi_breach < 1:
            raise ValueError(f"trigger_multi_breach must be >= 1, got {self.trigger_multi_breach}")

    def check_and_update(
        self,
        breaches_per_symbol: dict[str, bool],
        step: int,
    ) -> bool:
        """Check if global pause should be active and update state.

        Args:
            breaches_per_symbol: Dict[symbol, is_breaching]
            step: Current environment step

        Returns:
            is_paused: True if trading should be paused (actions=0 for all)
        """
        if not self.enabled:
            return False

        self.current_step = step

        # Update rolling breach window
        any_breach_this_step = any(breaches_per_symbol.values())
        num_breaching = sum(1 for is_breaching in breaches_per_symbol.values() if is_breaching)

        self.breach_window.append(any_breach_this_step)
        if len(self.breach_window) > self.window_steps:
            self.breach_window.popleft()

        # Compute rolling breach rate
        if len(self.breach_window) > 0:
            rolling_breach_rate = sum(self.breach_window) / len(self.breach_window)
            self.rolling_breach_rate_max = max(self.rolling_breach_rate_max, rolling_breach_rate)
        else:
            rolling_breach_rate = 0.0

        # Check if currently in pause
        if self.mode == "PAUSED":
            # Check if cooldown expired
            steps_since_pause = self.current_step - self.pause_start_step
            if steps_since_pause >= self.cooldown_steps:
                # Exit pause
                self._transition_to("NORMAL")
                return False
            # Still paused
            self.pause_steps_total += 1
            return True

        # Check if should enter pause (NORMAL mode)
        should_pause = (
            rolling_breach_rate >= self.trigger_breach_rate
            or num_breaching >= self.trigger_multi_breach
        )

        if should_pause:
            self._transition_to("PAUSED")
            self.pause_start_step = self.current_step
            self.pause_entries += 1
            self.pause_steps_total += 1

            if self.first_pause_step is None:
                self.first_pause_step = self.current_step

            return True

        return False

    def _transition_to(self, new_mode: Literal["NORMAL", "PAUSED"]):
        """Record mode transition."""
        if self.mode != new_mode:
            transition = {
                "from_mode": self.mode,
                "to_mode": new_mode,
                "step": self.current_step,
                "rolling_breach_rate": self.get_rolling_breach_rate(),
            }
            self.transitions.append(transition)
            self.last_mode = self.mode
            self.mode = new_mode

    def get_rolling_breach_rate(self) -> float:
        """Get current rolling breach rate."""
        if len(self.breach_window) == 0:
            return 0.0
        return sum(self.breach_window) / len(self.breach_window)

    def get_summary_stats(self) -> dict:
        """Get summary statistics for logging."""
        return {
            "ce_fallback_mode": "global_pause" if self.enabled else "none",
            "ce_global_pause_triggered": self.pause_entries > 0,
            "ce_global_pause_entries": self.pause_entries,
            "ce_global_pause_steps_total": self.pause_steps_total,
            "ce_global_pause_first_step": self.first_pause_step,
            "ce_rolling_breach_rate_any_max": round(self.rolling_breach_rate_max, 6),
            "ce_pause_window_steps": self.window_steps,
            "ce_pause_trigger_breach_rate": self.trigger_breach_rate,
            "ce_pause_trigger_multi_breach": self.trigger_multi_breach,
            "ce_pause_cooldown_steps": self.cooldown_steps,
        }

    def get_transitions(self) -> list[dict]:
        """Get all mode transitions for logging."""
        return self.transitions.copy()

    def log_transitions(self, log_path: Path | str):
        """Log mode transitions to JSONL file.

        Only logs transitions, not per-step events.
        """
        if not self.transitions:
            return

        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        import json

        with open(log_path, "a") as f:
            for transition in self.transitions:
                f.write(json.dumps(transition) + "\n")

        # Clear logged transitions
        self.transitions.clear()
