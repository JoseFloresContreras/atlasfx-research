"""
Tests for MAE/MFE intra-trade tracking in ProductionTradingEnv.

Tests verify that MAE/MFE is computed consistently with the simulator's
execution semantics (pessimistic SL/TP fills, OPEN-based entries/exits).

Key invariants:
- SL exit bar: MFE must NOT use bar's high (assume adverse-first)
- TP exit bar: MAE must NOT use bar's low (assume favorable-first)
- Voluntary close: neither high nor low of exit bar affect MAE/MFE
- Entry bar is excluded from MAE/MFE tracking
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
from atlasfx.evaluation.extended_metrics import calculate_mae_mfe_stats


def _create_test_env(
    n_bars: int = 10,
    ohlc_overrides: dict[int, dict[str, float]] | None = None,
) -> ProductionTradingEnv:
    """
    Create a minimal trading env with synthetic EURUSD data for testing.

    All bars default to O=H=C=1.1000, L=1.0999 with 10-pip ATR.
    Use ohlc_overrides to set specific bar OHLC values.
    """
    symbol = "EURUSD"

    # Default flat market
    data = {
        f"{symbol}-pair | open": np.full(n_bars, 1.1000),
        f"{symbol}-pair | high": np.full(n_bars, 1.1001),
        f"{symbol}-pair | low": np.full(n_bars, 1.0999),
        f"{symbol}-pair | close": np.full(n_bars, 1.1000),
        f"[Feature] {symbol} | atr_14": np.full(n_bars, 0.001),
        f"{symbol} | atr_14_real_pips": np.full(n_bars, 10.0),
        "[Feature] dummy": np.zeros(n_bars),
    }

    # Apply overrides
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


def _inject_long_position(
    env: ProductionTradingEnv,
    entry_price: float,
    sl: float,
    tp: float,
    entry_time: int = 0,
    units: float = 100_000.0,
) -> None:
    """Inject a LONG position into the env at a known state."""
    symbol = "EURUSD"
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
    env.position_costs[symbol] = (0.0, 0.0, {})


class TestMaeMfeSLExit:
    """Test (i): SL exit on bar with huge high → MFE must NOT use high."""

    def test_sl_exit_mfe_ignores_huge_high(self):
        """
        Scenario:
        - LONG EURUSD entry at 1.1000, SL=1.0950, TP=1.1100
        - Bar 1: normal bar H=1.1020, L=1.0980 → MAE=1.0980, MFE=1.1020
        - Bar 2: SL hit bar with HUGE high (H=1.2000, L=1.0940)
          - Both SL (L<=SL) and TP (H>=TP) triggered → SL wins ties
          - MFE must be 1.1020 (pre-bar), NOT 1.2000
          - MAE must be capped at SL=1.0950 (not low=1.0940)
        """
        ohlc = {
            # Bar 0: entry bar (flat)
            0: {"open": 1.1000, "high": 1.1001, "low": 1.0999, "close": 1.1000},
            # Bar 1: normal movement
            1: {"open": 1.1005, "high": 1.1020, "low": 1.0980, "close": 1.1010},
            # Bar 2: SL hit with enormous high
            2: {"open": 1.0960, "high": 1.2000, "low": 1.0940, "close": 1.0955},
        }

        env = _create_test_env(n_bars=5, ohlc_overrides=ohlc)
        env.reset(seed=42)

        symbol = "EURUSD"

        # Inject LONG position as if entered on bar 0
        _inject_long_position(env, entry_price=1.1000, sl=1.0950, tp=1.1100, entry_time=0)

        # --- Bar 1: position survives, MAE/MFE updated ---
        env.current_step = 1
        env._update_mae_mfe_pre_bar()

        pos = env.positions[symbol]
        assert pos.pos_mae_price == pytest.approx(1.0980)  # Low of bar 1
        assert pos.pos_mfe_price == pytest.approx(1.1020)  # High of bar 1

        # SL/TP check on bar 1: no trigger (L=1.0980 > SL=1.0950, H=1.1020 < TP=1.1100)
        high1 = float(env.ohlc_data[symbol]["high"][1])
        low1 = float(env.ohlc_data[symbol]["low"][1])
        pnl1 = env._check_sl_tp_hits(symbol, high1, low1)
        assert pnl1 == 0.0  # No exit

        # --- Bar 2: SL hit with huge high ---
        env.current_step = 2
        env._update_mae_mfe_pre_bar()

        # After full update, MFE would naively be 1.2000 — but _check_sl_tp_hits corrects
        high2 = float(env.ohlc_data[symbol]["high"][2])
        low2 = float(env.ohlc_data[symbol]["low"][2])
        pnl2 = env._check_sl_tp_hits(symbol, high2, low2)

        # Position should be closed via SL
        assert symbol not in env.positions
        assert len(env.trade_history) == 1

        trade = env.trade_history[0]

        # CRITICAL ASSERTIONS:
        # MFE must NOT be 1.2000 (the huge high). It's the pre-bar value.
        assert trade.mfe_price == pytest.approx(1.1020), (
            f"MFE should be pre-bar value 1.1020, not bar's huge high. Got {trade.mfe_price}"
        )

        # MAE must be capped at SL (1.0950), not the actual low (1.0940)
        assert trade.mae_price == pytest.approx(1.0950), (
            f"MAE should be capped at SL=1.0950, not bar's low. Got {trade.mae_price}"
        )

        # Exit price should be SL
        assert trade.exit_price == pytest.approx(1.0950)

        # USD values should be consistent
        assert not np.isnan(trade.mae_usd)
        assert not np.isnan(trade.mfe_usd)
        assert trade.mae_usd < 0  # Adverse = negative PnL
        assert trade.mfe_usd > 0  # Favorable = positive PnL


class TestMaeMfeTPExit:
    """Test (ii): TP exit on bar with huge low → MAE must NOT use low."""

    def test_tp_exit_mae_ignores_huge_low(self):
        """
        Scenario:
        - LONG EURUSD entry at 1.1000, SL=1.0500, TP=1.1050
        - Bar 1: normal bar H=1.1030, L=1.0990 → MAE=1.0990, MFE=1.1030
        - Bar 2: TP hit bar with huge LOW (H=1.1060, L=0.9000)
          - TP: H=1.1060 >= TP=1.1050 → triggered
          - SL: L=0.9000 <= SL=1.0500 → also triggered, but SL is at 1.0500
            Wait — L=0.9000 < SL=1.0500 → SL IS hit. SL wins ties.
            So we need SL far enough that L=0.9000 > SL.
            Use SL=0.8000 so L=0.9000 > SL=0.8000 → SL NOT hit.
          - Only TP triggers. Exit at TP=1.1050.
          - MAE must be 1.0990 (pre-bar), NOT 0.9000
          - MFE must be capped at TP=1.1050
        """
        ohlc = {
            # Bar 0: entry bar (flat)
            0: {"open": 1.1000, "high": 1.1001, "low": 1.0999, "close": 1.1000},
            # Bar 1: normal movement
            1: {"open": 1.1005, "high": 1.1030, "low": 1.0990, "close": 1.1020},
            # Bar 2: TP hit with enormous low
            2: {"open": 1.1040, "high": 1.1060, "low": 0.9000, "close": 1.1055},
        }

        env = _create_test_env(n_bars=5, ohlc_overrides=ohlc)
        env.reset(seed=42)

        symbol = "EURUSD"

        # Inject LONG position: SL far away so it doesn't trigger
        _inject_long_position(env, entry_price=1.1000, sl=0.8000, tp=1.1050, entry_time=0)

        # --- Bar 1: position survives ---
        env.current_step = 1
        env._update_mae_mfe_pre_bar()

        pos = env.positions[symbol]
        assert pos.pos_mae_price == pytest.approx(1.0990)
        assert pos.pos_mfe_price == pytest.approx(1.1030)

        high1 = float(env.ohlc_data[symbol]["high"][1])
        low1 = float(env.ohlc_data[symbol]["low"][1])
        pnl1 = env._check_sl_tp_hits(symbol, high1, low1)
        assert pnl1 == 0.0

        # --- Bar 2: TP hit with huge low ---
        env.current_step = 2
        env._update_mae_mfe_pre_bar()

        high2 = float(env.ohlc_data[symbol]["high"][2])
        low2 = float(env.ohlc_data[symbol]["low"][2])

        # Verify SL is NOT triggered: L=0.9000 > SL=0.8000? → 0.9 > 0.8 YES → SL not hit
        assert low2 > 0.8000, "Test setup: SL should NOT be triggered"

        pnl2 = env._check_sl_tp_hits(symbol, high2, low2)

        # Position should be closed via TP
        assert symbol not in env.positions
        assert len(env.trade_history) == 1

        trade = env.trade_history[0]

        # CRITICAL ASSERTIONS:
        # MAE must NOT be 0.9000 (the huge low). It's the pre-bar value.
        assert trade.mae_price == pytest.approx(1.0990), (
            f"MAE should be pre-bar value 1.0990, not bar's huge low. Got {trade.mae_price}"
        )

        # MFE must be capped at TP (1.1050), not the actual high (1.1060)
        assert trade.mfe_price == pytest.approx(1.1050), (
            f"MFE should be capped at TP=1.1050, not bar's high. Got {trade.mfe_price}"
        )

        # Exit price should be TP
        assert trade.exit_price == pytest.approx(1.1050)

        # USD values should be consistent
        assert not np.isnan(trade.mae_usd)
        assert not np.isnan(trade.mfe_usd)
        assert trade.mae_usd < 0  # Adverse = negative PnL
        assert trade.mfe_usd > 0  # Favorable = positive PnL


class TestMaeMfeAggregation:
    """Test MAE/MFE aggregation in extended metrics."""

    def test_aggregate_stats_with_valid_trades(self):
        """calculate_mae_mfe_stats returns correct pips stats."""
        trades = [
            MockTradeWithMAE(entry_price=1.1000, mae_price=1.0950, mfe_price=1.1080, units=1000.0),
            MockTradeWithMAE(entry_price=1.1000, mae_price=1.0970, mfe_price=1.1040, units=1000.0),
        ]
        result = calculate_mae_mfe_stats(trades)

        # Trade 1: MAE = 50 pips, MFE = 80 pips
        # Trade 2: MAE = 30 pips, MFE = 40 pips
        assert result["mae_avg"] == pytest.approx(40.0)  # (50+30)/2
        assert result["mfe_avg"] == pytest.approx(60.0)  # (80+40)/2

    def test_aggregate_stats_all_nan(self):
        """Returns NaN when all trades have NaN mae/mfe."""
        trades = [
            MockTradeWithMAE(mae_price=float("nan"), mfe_price=float("nan")),
        ]
        result = calculate_mae_mfe_stats(trades)
        assert np.isnan(result["mae_avg"])
        assert np.isnan(result["mfe_avg"])
        assert np.isnan(result["mae_p95"])
        assert np.isnan(result["mfe_p95"])

    def test_aggregate_stats_empty(self):
        """Returns NaN for empty trade list."""
        result = calculate_mae_mfe_stats([])
        assert np.isnan(result["mae_avg"])


class MockTradeWithMAE:
    """Minimal mock with MAE/MFE fields for aggregation tests."""

    def __init__(
        self,
        entry_price: float = 1.1000,
        mae_price: float = 1.0950,
        mfe_price: float = 1.1050,
        units: float = 1000.0,
        symbol: str = "EURUSD",
    ):
        self.symbol = symbol
        self.entry_price = entry_price
        self.mae_price = mae_price
        self.mfe_price = mfe_price
        self.units = units
        self.pnl_usd = 50.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
