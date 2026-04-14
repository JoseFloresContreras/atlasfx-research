"""
Tests for trade ledger completeness, exit_reason tagging,
and max_concurrent_positions event-sweep calculation.
"""

import numpy as np
import pandas as pd
import pytest

from atlasfx.environments.trading_env import (
    ProductionPosition,
    ProductionTrade,
    ProductionTradingConfig,
    ProductionTradingEnv,
)
from atlasfx.evaluation.extended_metrics import calculate_max_concurrent_positions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_test_env(
    n_bars: int = 20,
    ohlc_overrides: dict[int, dict[str, float]] | None = None,
) -> ProductionTradingEnv:
    """Minimal env with synthetic EURUSD data."""
    symbol = "EURUSD"

    data = {
        f"{symbol}-pair | open": np.full(n_bars, 1.1000),
        f"{symbol}-pair | high": np.full(n_bars, 1.1001),
        f"{symbol}-pair | low": np.full(n_bars, 1.0999),
        f"{symbol}-pair | close": np.full(n_bars, 1.1000),
        f"[Feature] {symbol} | atr_14": np.full(n_bars, 0.001),
        f"{symbol} | atr_14_real_pips": np.full(n_bars, 10.0),
        "[Feature] dummy": np.zeros(n_bars),
    }

    if ohlc_overrides:
        for idx, overrides in ohlc_overrides.items():
            for key, value in overrides.items():
                col = f"{symbol}-pair | {key}"
                data[col][idx] = value

    df = pd.DataFrame(data)
    price_cols = {
        symbol: {
            "open": f"{symbol}-pair | open",
            "high": f"{symbol}-pair | high",
            "low": f"{symbol}-pair | low",
            "close": f"{symbol}-pair | close",
        }
    }

    config = ProductionTradingConfig(
        validation_mode=True,
        episode_length=n_bars - 1,
    )
    return ProductionTradingEnv(df, [symbol], price_cols, config)


def _inject_long(
    env: ProductionTradingEnv,
    entry_price: float = 1.1000,
    sl: float = 1.0950,
    tp: float = 1.1100,
    entry_time: int = 0,
    units: float = 100_000.0,
) -> None:
    """Inject a LONG position."""
    symbol = "EURUSD"
    pip_size = 0.0001
    sl_pips = abs((entry_price - sl) / pip_size)
    tp_pips = abs((tp - entry_price) / pip_size)
    env.positions[symbol] = ProductionPosition(
        symbol=symbol,
        units=units,
        avg_entry=entry_price,
        sl=sl,
        tp=tp,
        entry_time=entry_time,
        initial_sl_pips=sl_pips,
        initial_tp_pips=tp_pips,
        initial_tp_sl_ratio=tp_pips / max(sl_pips, 1e-9),
        moved_to_break_even=False,
        high_watermark_price=entry_price,
        low_watermark_price=entry_price,
        pos_mae_price=entry_price,
        pos_mfe_price=entry_price,
        pre_bar_mae=entry_price,
        pre_bar_mfe=entry_price,
    )
    env.position_costs[symbol] = (0.0, 0.0, {})


# ---------------------------------------------------------------------------
# exit_reason tests
# ---------------------------------------------------------------------------


class TestExitReasonSLTP:
    """exit_reason must be 'sltp_sl' on SL hit and 'sltp_tp' on TP hit."""

    def test_sl_hit_sets_sltp_sl(self):
        """SL hit → exit_reason == 'sltp_sl'."""
        # Bar 2 has low that triggers SL
        env = _create_test_env(
            n_bars=10,
            ohlc_overrides={
                2: {"open": 1.1000, "high": 1.1010, "low": 1.0940, "close": 1.0945},
            },
        )
        env.reset()
        env.current_step = 1
        _inject_long(env, entry_price=1.1000, sl=1.0950, tp=1.1100, entry_time=0)

        # Step to bar 2 → SL triggered (low 1.0940 < SL 1.0950)
        env.current_step = 2
        env._check_sl_tp_hits("EURUSD", high=1.1010, low=1.0940)

        assert len(env.trade_history) == 1
        trade = env.trade_history[0]
        assert trade.exit_reason == "sltp_sl"

    def test_tp_hit_sets_sltp_tp(self):
        """TP hit → exit_reason == 'sltp_tp'."""
        env = _create_test_env(
            n_bars=10,
            ohlc_overrides={
                2: {"open": 1.1000, "high": 1.1110, "low": 1.0990, "close": 1.1100},
            },
        )
        env.reset()
        env.current_step = 1
        _inject_long(env, entry_price=1.1000, sl=1.0950, tp=1.1100, entry_time=0)

        # Step to bar 2 → TP triggered (high 1.1110 > TP 1.1100)
        env.current_step = 2
        env._check_sl_tp_hits("EURUSD", high=1.1110, low=1.0990)

        assert len(env.trade_history) == 1
        trade = env.trade_history[0]
        assert trade.exit_reason == "sltp_tp"


class TestExitReasonAgentClose:
    """exit_reason must be 'agent_close' on voluntary flat."""

    def test_agent_close_sets_exit_reason(self):
        """Complete close via _execute_trade → exit_reason == 'agent_close'."""
        env = _create_test_env(n_bars=10)
        env.reset()
        env.current_step = 1
        _inject_long(env, entry_price=1.1000, sl=1.0950, tp=1.1100, entry_time=0)

        # Agent closes completely: trade_units negative of position
        env.current_step = 3
        env._execute_trade(
            symbol="EURUSD",
            trade_units=-100_000.0,
            price=1.1020,
            sl_price=0.0,
            tp_price=0.0,
        )

        assert len(env.trade_history) == 1
        trade = env.trade_history[0]
        assert trade.exit_reason == "agent_close"


class TestExitReasonReverse:
    """exit_reason must be 'reverse' when flipping direction."""

    def test_reverse_sets_exit_reason(self):
        """Reversal via _execute_trade → exit_reason == 'reverse'."""
        env = _create_test_env(n_bars=10)
        env.reset()
        env.current_step = 1
        _inject_long(env, entry_price=1.1000, sl=1.0950, tp=1.1100, entry_time=0)

        # Reverse: sell more than current long to flip short
        env.current_step = 3
        env._execute_trade(
            symbol="EURUSD",
            trade_units=-200_000.0,
            price=1.1020,
            sl_price=1.1070,
            tp_price=1.0970,
        )

        assert len(env.trade_history) == 1
        trade = env.trade_history[0]
        assert trade.exit_reason == "reverse"


# ---------------------------------------------------------------------------
# trade_id tests
# ---------------------------------------------------------------------------


class TestTradeId:
    """trade_id must be monotonically increasing from 0."""

    def test_trade_ids_sequential(self):
        """Multiple trades get sequential trade_id values."""
        env = _create_test_env(
            n_bars=20,
            ohlc_overrides={
                2: {"low": 1.0940},  # SL hit
                5: {"low": 1.0940},  # SL hit again
            },
        )
        env.reset()

        # Trade 0
        env.current_step = 1
        _inject_long(env, entry_price=1.1000, sl=1.0950, tp=1.1100, entry_time=0)
        env.current_step = 2
        env._check_sl_tp_hits("EURUSD", high=1.1001, low=1.0940)

        # Trade 1
        env.current_step = 4
        _inject_long(env, entry_price=1.1000, sl=1.0950, tp=1.1100, entry_time=3)
        env.current_step = 5
        env._check_sl_tp_hits("EURUSD", high=1.1001, low=1.0940)

        assert len(env.trade_history) == 2
        assert env.trade_history[0].trade_id == 0
        assert env.trade_history[1].trade_id == 1


# ---------------------------------------------------------------------------
# ProductionTrade field completeness
# ---------------------------------------------------------------------------


class TestTradeLedgerFields:
    """ProductionTrade must have all required ledger fields."""

    def test_required_fields_exist(self):
        """All ledger fields present on ProductionTrade."""
        required = [
            "trade_id",
            "symbol",
            "entry_time",
            "exit_time",
            "entry_price",
            "exit_price",
            "units",
            "notional_usd",
            "pnl_usd",
            "commission_usd",
            "slippage_usd",
            "exit_reason",
        ]
        fields = {f.name for f in ProductionTrade.__dataclass_fields__.values()}
        for name in required:
            assert name in fields, f"Missing field: {name}"

    def test_side_property(self):
        """side property returns 'long' or 'short'."""
        long_trade = ProductionTrade(
            symbol="EURUSD",
            entry_time=0,
            exit_time=1,
            entry_price=1.1,
            exit_price=1.1,
            units=100_000,
            pnl_usd=0,
            commission_usd=0,
            slippage_usd=0,
        )
        short_trade = ProductionTrade(
            symbol="EURUSD",
            entry_time=0,
            exit_time=1,
            entry_price=1.1,
            exit_price=1.1,
            units=-100_000,
            pnl_usd=0,
            commission_usd=0,
            slippage_usd=0,
        )
        assert long_trade.side == "long"
        assert short_trade.side == "short"

    def test_lots_property(self):
        """lots property returns abs(units)/100_000."""
        trade = ProductionTrade(
            symbol="EURUSD",
            entry_time=0,
            exit_time=1,
            entry_price=1.1,
            exit_price=1.1,
            units=-250_000,
            pnl_usd=0,
            commission_usd=0,
            slippage_usd=0,
        )
        assert trade.lots == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# max_concurrent_positions tests
# ---------------------------------------------------------------------------


class TestMaxConcurrentPositions:
    """Event-sweep max concurrent positions from trade intervals."""

    def test_no_trades(self):
        """No trades → 0."""
        assert calculate_max_concurrent_positions([]) == 0

    def test_single_trade(self):
        """One trade → max = 1."""
        t = ProductionTrade(
            symbol="EURUSD",
            entry_time=0,
            exit_time=10,
            entry_price=1.1,
            exit_price=1.1,
            units=1000,
            pnl_usd=0,
            commission_usd=0,
            slippage_usd=0,
        )
        assert calculate_max_concurrent_positions([t]) == 1

    def test_non_overlapping(self):
        """Two non-overlapping trades → max = 1."""
        t1 = ProductionTrade(
            symbol="EURUSD",
            entry_time=0,
            exit_time=10,
            entry_price=1.1,
            exit_price=1.1,
            units=1000,
            pnl_usd=0,
            commission_usd=0,
            slippage_usd=0,
        )
        t2 = ProductionTrade(
            symbol="GBPUSD",
            entry_time=10,
            exit_time=20,
            entry_price=1.3,
            exit_price=1.3,
            units=1000,
            pnl_usd=0,
            commission_usd=0,
            slippage_usd=0,
        )
        # t1 closes at 10, t2 opens at 10 → tie: close before open → max = 1
        assert calculate_max_concurrent_positions([t1, t2]) == 1

    def test_overlapping(self):
        """Two overlapping trades → max = 2."""
        t1 = ProductionTrade(
            symbol="EURUSD",
            entry_time=0,
            exit_time=15,
            entry_price=1.1,
            exit_price=1.1,
            units=1000,
            pnl_usd=0,
            commission_usd=0,
            slippage_usd=0,
        )
        t2 = ProductionTrade(
            symbol="GBPUSD",
            entry_time=5,
            exit_time=20,
            entry_price=1.3,
            exit_price=1.3,
            units=1000,
            pnl_usd=0,
            commission_usd=0,
            slippage_usd=0,
        )
        assert calculate_max_concurrent_positions([t1, t2]) == 2

    def test_three_way_overlap(self):
        """Three overlapping trades → max = 3."""
        trades = [
            ProductionTrade(
                symbol="EURUSD",
                entry_time=0,
                exit_time=30,
                entry_price=1.1,
                exit_price=1.1,
                units=1000,
                pnl_usd=0,
                commission_usd=0,
                slippage_usd=0,
            ),
            ProductionTrade(
                symbol="GBPUSD",
                entry_time=5,
                exit_time=25,
                entry_price=1.3,
                exit_price=1.3,
                units=1000,
                pnl_usd=0,
                commission_usd=0,
                slippage_usd=0,
            ),
            ProductionTrade(
                symbol="USDJPY",
                entry_time=10,
                exit_time=20,
                entry_price=150.0,
                exit_price=150.0,
                units=1000,
                pnl_usd=0,
                commission_usd=0,
                slippage_usd=0,
            ),
        ]
        assert calculate_max_concurrent_positions(trades) == 3
