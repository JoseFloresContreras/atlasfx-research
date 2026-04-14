"""
Tests for exit commission charging.

Verifies that commission is charged on BOTH entry AND exit sides:
- Entry: lots × commission_per_lot (charged when position opens)
- Exit: lots × commission_per_lot (charged when position closes via SL/TP/reverse/agent_close)

Total round-turn commission per lot should be 2 × commission_per_lot = $5.00/lot.
"""

import numpy as np
import pandas as pd
import pytest

from atlasfx.environments.trading_env import (
    ProductionPosition,
    ProductionTradingConfig,
    ProductionTradingEnv,
)


def _create_test_env(
    n_bars: int = 50,
    symbol: str = "EURUSD",
    commission_per_lot: float = 2.5,
    ohlc_overrides: dict[int, dict[str, float]] | None = None,
) -> ProductionTradingEnv:
    """Create a minimal trading env with synthetic data."""
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
        commission_per_lot=commission_per_lot,
        spread_pips=0.0,           # Disable spread for clean commission test
        slippage_pips_mean=0.0,    # Disable slippage for clean commission test
        slippage_bps=0.0,
    )

    return ProductionTradingEnv(df, [symbol], price_cols, config)


def _inject_position(
    env: ProductionTradingEnv,
    symbol: str,
    entry_price: float,
    sl: float,
    tp: float,
    units: float = 100_000.0,
    entry_time: int = 0,
) -> None:
    """Inject a position and set its entry commission in position_costs."""
    pip_size = 0.0001
    initial_sl_pips = abs((entry_price - sl) / pip_size)
    initial_tp_pips = abs((tp - entry_price) / pip_size)
    initial_tp_sl_ratio = initial_tp_pips / max(initial_sl_pips, 1e-9)

    env.positions[symbol] = ProductionPosition(
        symbol=symbol,
        units=units,
        avg_entry=entry_price,
        sl=sl,
        tp=tp,
        entry_time=entry_time,
        initial_sl_pips=initial_sl_pips,
        initial_tp_pips=initial_tp_pips,
        initial_tp_sl_ratio=initial_tp_sl_ratio,
        moved_to_break_even=False,
        high_watermark_price=entry_price,
        low_watermark_price=entry_price,
        pos_mae_price=entry_price,
        pos_mfe_price=entry_price,
        pre_bar_mae=entry_price,
        pre_bar_mfe=entry_price,
    )

    # Set entry commission (what _calculate_costs would have produced)
    lots = abs(units) / env.config.lot_size
    entry_commission = lots * env.config.commission_per_lot
    leverage_info = {
        "notional_usd": abs(units) * entry_price,
        "leverage_before_cap": 0.0,
        "leverage_after_cap": 0.0,
        "cap_hit": False,
        "cap_scale": 1.0,
        "units_desired": units,
        "max_lots_hit": False,
        "max_leverage_hit": False,
        "equity_at_entry": env.balance,
    }
    env.position_costs[symbol] = (entry_commission, 0.0, leverage_info)


class TestExitCommission:
    """Test that exit commission is correctly charged."""

    def test_sl_exit_charges_round_turn_commission(self):
        """SL hit should charge commission on both entry and exit sides."""
        symbol = "EURUSD"
        # Bar 2: low drops to hit SL
        env = _create_test_env(
            n_bars=10,
            ohlc_overrides={2: {"low": 1.0950, "open": 1.1000, "high": 1.1001, "close": 1.0960}},
        )
        env.reset(seed=42)

        # Inject a long position: entry=1.1000, SL=1.0970, TP=1.1060
        _inject_position(env, symbol, 1.1000, sl=1.0970, tp=1.1060, units=100_000.0, entry_time=1)
        env.current_step = 2  # Jump to bar where SL triggers

        # Step — SL should fire (low=1.0950 < SL=1.0970)
        action = np.array([[0.0, 2.0, 3.0]])  # No new trade
        env.step(action)

        # Should have exactly 1 trade
        assert len(env.trade_history) == 1
        trade = env.trade_history[0]

        lots = abs(trade.units) / env.config.lot_size  # 1.0 lot
        expected_comm_rt = lots * env.config.commission_per_lot * 2  # $5.00

        assert trade.exit_reason == "sltp_sl"
        assert abs(trade.commission_usd - expected_comm_rt) < 0.001, (
            f"Commission should be ${expected_comm_rt:.2f} (RT), got ${trade.commission_usd:.2f}"
        )
        assert trade.slippage_usd == 0.0  # Spread/slippage disabled

    def test_tp_exit_charges_round_turn_commission(self):
        """TP hit should charge commission on both entry and exit sides."""
        symbol = "EURUSD"
        # Bar 2: high rises to hit TP
        env = _create_test_env(
            n_bars=10,
            ohlc_overrides={2: {"high": 1.1100, "open": 1.1000, "low": 1.0999, "close": 1.1050}},
        )
        env.reset(seed=42)

        # Inject long: entry=1.1000, SL=1.0970, TP=1.1060
        _inject_position(env, symbol, 1.1000, sl=1.0970, tp=1.1060, units=100_000.0, entry_time=1)
        env.current_step = 2

        action = np.array([[0.0, 2.0, 3.0]])
        env.step(action)

        assert len(env.trade_history) == 1
        trade = env.trade_history[0]

        lots = abs(trade.units) / env.config.lot_size
        expected_comm_rt = lots * env.config.commission_per_lot * 2

        assert trade.exit_reason == "sltp_tp"
        assert abs(trade.commission_usd - expected_comm_rt) < 0.001, (
            f"Commission should be ${expected_comm_rt:.2f} (RT), got ${trade.commission_usd:.2f}"
        )

    def test_reverse_exit_charges_round_turn_commission(self):
        """Reversal close should charge exit commission on the old position.

        Note: On reversal, step() calls _calculate_costs() for the NEW action BEFORE
        _execute_trade() closes the old position. This means the old position's trade
        record also includes the new position's entry commission (pre-existing behavior).
        We verify that at minimum, the exit commission component is present.
        """
        symbol = "EURUSD"
        env = _create_test_env(n_bars=20)
        env.reset(seed=42)

        # Inject a long position (entry_time=0 so min_hold_period=5 is satisfied at step 10)
        _inject_position(env, symbol, 1.1000, sl=1.0970, tp=1.1060, units=100_000.0, entry_time=0)
        env.current_step = 10

        # Record the injected entry commission
        injected_entry_comm = env.position_costs[symbol][0]  # $2.50

        # Send strong short signal to trigger reversal
        action = np.array([[-0.9, 2.0, 3.0]])
        env.step(action)

        # Find the closed trade
        closed_trades = [t for t in env.trade_history if t.exit_reason in ("reverse", "agent_close")]
        assert len(closed_trades) >= 1, "Should have at least 1 closed trade from reversal"

        trade = closed_trades[0]
        lots = abs(trade.units) / env.config.lot_size
        exit_commission = lots * env.config.commission_per_lot  # $2.50

        # The trade's commission must include at least: entry_comm + exit_comm
        min_expected = injected_entry_comm + exit_commission
        assert trade.commission_usd >= min_expected - 0.001, (
            f"Commission should include at least entry(${injected_entry_comm:.2f}) + "
            f"exit(${exit_commission:.2f}) = ${min_expected:.2f}, got ${trade.commission_usd:.2f}"
        )

    def test_commission_per_lot_is_exact_5_dollars(self):
        """Verify commission per lot is exactly $5.00 round-turn for a 1-lot position."""
        symbol = "EURUSD"
        env = _create_test_env(
            n_bars=10,
            ohlc_overrides={2: {"low": 1.0950}},
        )
        env.reset(seed=42)

        _inject_position(env, symbol, 1.1000, sl=1.0970, tp=1.1060, units=100_000.0, entry_time=1)
        env.current_step = 2

        action = np.array([[0.0, 2.0, 3.0]])
        env.step(action)

        trade = env.trade_history[0]
        assert trade.commission_usd == pytest.approx(5.0, abs=0.001), (
            f"1 standard lot RT commission should be $5.00, got ${trade.commission_usd:.4f}"
        )

    def test_fractional_lots_commission(self):
        """Verify commission scales correctly for fractional lots (e.g., 0.1 lots = 10K units)."""
        symbol = "EURUSD"
        env = _create_test_env(
            n_bars=10,
            ohlc_overrides={2: {"low": 1.0950}},
        )
        env.reset(seed=42)

        # 0.1 lots = 10,000 units
        _inject_position(env, symbol, 1.1000, sl=1.0970, tp=1.1060, units=10_000.0, entry_time=1)
        env.current_step = 2

        action = np.array([[0.0, 2.0, 3.0]])
        env.step(action)

        trade = env.trade_history[0]
        expected = 0.1 * 2.5 * 2  # 0.1 lots × $2.50/side × 2 sides = $0.50
        assert trade.commission_usd == pytest.approx(expected, abs=0.001), (
            f"0.1 lot RT commission should be ${expected:.2f}, got ${trade.commission_usd:.4f}"
        )

    def test_exit_commission_deducted_from_balance(self):
        """Verify that exit commission actually reduces the balance."""
        symbol = "EURUSD"
        env = _create_test_env(
            n_bars=10,
            ohlc_overrides={2: {"low": 1.0950, "open": 1.1000, "high": 1.1001, "close": 1.0960}},
        )
        env.reset(seed=42)

        _inject_position(env, symbol, 1.1000, sl=1.0970, tp=1.1060, units=100_000.0, entry_time=1)
        env.current_step = 2

        balance_before = env.balance
        action = np.array([[0.0, 2.0, 3.0]])
        env.step(action)
        balance_after = env.balance

        trade = env.trade_history[0]
        # Balance change should include: PnL from SL hit MINUS exit commission
        # PnL: (1.0970 - 1.1000) * 100,000 / 0.0001 * 0.0001 = -300 USD (approx)
        # The key check: balance should reflect the exit commission deduction
        pnl_usd = trade.pnl_usd
        exit_comm = abs(trade.units) / env.config.lot_size * env.config.commission_per_lot

        # balance_after should = balance_before + pnl_usd - exit_comm
        # (entry comm was already deducted when we set up position_costs)
        expected_balance = balance_before + pnl_usd - exit_comm
        assert abs(balance_after - expected_balance) < 0.01, (
            f"Balance should include exit commission deduction. "
            f"Expected {expected_balance:.2f}, got {balance_after:.2f}"
        )
