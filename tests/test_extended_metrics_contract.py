"""
Tests for Extended Metrics Contract & Schema Validation

Tests the metric schema, naming conventions, units, and basic calculations
with toy data to ensure contract stability.
"""

import numpy as np
import pytest
from dataclasses import asdict, dataclass

from atlasfx.evaluation.extended_metrics import (
    get_extended_metric_schema,
    list_metric_names,
    validate_metric_output,
    validate_units_convention,
    calculate_extended_metrics,
    ExtendedMetrics,
    extended_metrics_to_dict_with_metadata,
)


@dataclass
class MockProductionTrade:
    """Mock trade for testing - matches ProductionTrade structure."""

    trade_id: int = 0
    symbol: str = "EURUSD"
    entry_time: int = 0
    exit_time: int = 10
    entry_price: float = 1.1000
    exit_price: float = 1.1050
    units: float = 1000.0
    pnl_usd: float = 50.0
    commission_usd: float = 0.5
    slippage_usd: float = 0.3
    initial_sl_pips: float = 10.0
    initial_tp_pips: float = 20.0
    initial_tp_sl_ratio: float = 2.0
    notional_usd: float = 1100.0
    equity_at_entry: float = 10000.0
    mae_price: float = 1.0950  # 50 pips adverse
    mfe_price: float = 1.1100  # 100 pips favorable
    mae_usd: float = -50.0
    mfe_usd: float = 100.0
    exit_reason: str = "agent_close"


class TestMetricSchema:
    """Test metric schema and contract validation."""

    def test_schema_structure(self):
        """Schema has correct structure."""
        schema = get_extended_metric_schema()

        assert len(schema) == 61, f"Expected 61 metrics, got {len(schema)}"

        # Check first metric has required keys
        first = schema[0]
        assert "name" in first
        assert "category" in first
        assert "unit" in first
        assert "quality" in first
        assert "required_inputs" in first

        # Check categories
        categories = {m["category"] for m in schema}
        assert categories == {"A", "B", "C", "D", "E", "F", "G"}

        # Check quality tiers
        qualities = {m["quality"] for m in schema}
        assert qualities.issubset({"REAL", "APPROX", "PROXY", "PARTIAL"})

    def test_list_metric_names(self):
        """list_metric_names returns correct count and order."""
        names = list_metric_names()

        assert len(names) == 61
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)

        # Check no duplicates
        assert len(names) == len(set(names)), "Duplicate metric names found"

    def test_schema_matches_dataclass(self):
        """Schema names match ExtendedMetrics dataclass fields."""
        schema_names = set(list_metric_names())
        dataclass_names = set(ExtendedMetrics.__dataclass_fields__.keys())

        assert schema_names == dataclass_names, (
            f"Mismatch between schema and dataclass:\n"
            f"Missing in dataclass: {schema_names - dataclass_names}\n"
            f"Extra in dataclass: {dataclass_names - schema_names}"
        )


class TestUnitsConvention:
    """Test units convention validation."""

    def test_returns_as_decimals(self):
        """Returns should be decimals, not percentages."""
        # Valid: decimals
        returns_decimal = np.array([0.01, -0.02, 0.015, 0.005])
        validate_units_convention(returns=returns_decimal)  # Should pass

        # Invalid: percentages (large values)
        returns_pct = np.array([10.0, -20.0, 15.0, 5.0])  # In %, too large
        with pytest.raises(ValueError, match="Returns appear to be in percentage form"):
            validate_units_convention(returns=returns_pct)

    def test_percentage_metrics_range(self):
        """Percentage metrics should be 0-100 scale."""
        # Valid percentages
        valid_pct = {"win_rate": 65.5, "positive_month_rate": 75.0, "max_dd": 12.5}
        validate_units_convention(percentages=valid_pct)  # Should pass

        # Invalid: out of reasonable range
        invalid_pct = {"win_rate": 1500.0}  # Way too high
        with pytest.raises(ValueError, match="Expected range: -100 to 1000"):
            validate_units_convention(percentages=invalid_pct)


class TestToyCalculations:
    """Test calculations with toy data."""

    def create_toy_equity_curve(self) -> tuple[np.ndarray, np.ndarray]:
        """Create simple equity curve: up, down, up."""
        equity = np.array(
            [
                10000.0,  # Start
                10500.0,  # +5%
                11000.0,  # +4.76%
                10500.0,  # -4.55% (drawdown)
                10800.0,  # +2.86%
                11500.0,  # +6.48%
                12000.0,  # +4.35%
            ]
        )

        # Calculate returns
        returns = np.diff(equity) / equity[:-1]

        return equity, returns

    def create_toy_trades(self) -> list[MockProductionTrade]:
        """Create simple trade list."""
        return [
            MockProductionTrade(pnl_usd=500.0, units=1000.0),  # Win
            MockProductionTrade(pnl_usd=-200.0, units=1000.0),  # Loss
            MockProductionTrade(pnl_usd=300.0, units=1000.0),  # Win
            MockProductionTrade(pnl_usd=-150.0, units=1000.0),  # Loss
            MockProductionTrade(pnl_usd=700.0, units=1000.0),  # Big win
        ]

    def test_basic_calculation(self):
        """Test that calculate_extended_metrics runs without errors."""
        equity, returns = self.create_toy_equity_curve()
        trades = self.create_toy_trades()

        metrics = calculate_extended_metrics(
            equity_curve=equity,
            returns=returns,
            trades=trades,
            annualized_return=20.0,
            initial_balance=10000.0,
            observed_sharpe=1.5,
            n_trials=1,
            skewness=0.0,
            kurtosis=3.0,
        )

        # Should return valid ExtendedMetrics
        assert isinstance(metrics, ExtendedMetrics)
        assert len(asdict(metrics)) == 61

    def test_drawdown_metrics(self):
        """Test drawdown path metrics with known curve."""
        equity, returns = self.create_toy_equity_curve()
        trades = self.create_toy_trades()

        metrics = calculate_extended_metrics(
            equity_curve=equity,
            returns=returns,
            trades=trades,
            annualized_return=20.0,
            initial_balance=10000.0,
            observed_sharpe=1.5,
            n_trials=1,
        )

        # Ulcer index should be > 0 (had drawdown)
        assert metrics.ulcer_index > 0

        # Time under water should be 0-100%
        assert 0 <= metrics.time_under_water_pct <= 100

        # Pain ratio should be positive
        assert metrics.pain_ratio >= 0

    def test_tail_risk_metrics(self):
        """Test tail risk metrics consistency."""
        equity, returns = self.create_toy_equity_curve()
        trades = self.create_toy_trades()

        metrics = calculate_extended_metrics(
            equity_curve=equity,
            returns=returns,
            trades=trades,
            annualized_return=20.0,
            initial_balance=10000.0,
            observed_sharpe=1.5,
            n_trials=1,
        )

        # VaR 99% should be >= VaR 95% (99th percentile is more extreme, higher loss)
        assert metrics.var_99 >= metrics.var_95  # Both positive losses, 99 is higher

        # CVaR (conditional VaR) should be >= VaR (tail mean, more extreme loss)
        assert metrics.cvar_95 >= metrics.var_95  # CVaR higher (more extreme)
        assert metrics.cvar_99 >= metrics.var_99

        # Percentiles should be ordered
        assert metrics.ret_p01 <= metrics.ret_p05 <= metrics.ret_p99

    def test_trade_quality_metrics(self):
        """Test trade quality metrics."""
        equity, returns = self.create_toy_equity_curve()
        trades = self.create_toy_trades()

        metrics = calculate_extended_metrics(
            equity_curve=equity,
            returns=returns,
            trades=trades,
            annualized_return=20.0,
            initial_balance=10000.0,
            observed_sharpe=1.5,
            n_trials=1,
        )

        # Payoff ratio should be positive (more wins than losses)
        assert metrics.payoff_ratio > 0

        # EV should match total PnL / n_trades
        total_pnl = sum(t.pnl_usd for t in trades)
        expected_ev = total_pnl / len(trades)
        assert abs(metrics.ev_trade_usd - expected_ev) < 1.0

        # Consecutive streaks should be >= 1
        assert metrics.max_consec_wins >= 1
        assert metrics.max_consec_losses >= 1

    def test_psr_dsr_probabilities(self):
        """Test PSR/DSR are valid probabilities."""
        equity, returns = self.create_toy_equity_curve()
        trades = self.create_toy_trades()

        metrics = calculate_extended_metrics(
            equity_curve=equity,
            returns=returns,
            trades=trades,
            annualized_return=20.0,
            initial_balance=10000.0,
            observed_sharpe=2.0,  # Good Sharpe
            n_trials=5,
            skewness=0.2,
            kurtosis=3.5,
        )

        # PSR and DSR should be 0-1 probabilities
        assert 0 <= metrics.psr <= 1
        assert 0 <= metrics.dsr <= 1

        # DSR should be <= PSR (penalty for multiple testing)
        assert metrics.dsr <= metrics.psr


class TestMetadataIntegration:
    """Test metadata and output format."""

    def test_extended_metrics_to_dict_with_metadata(self):
        """Test conversion to dict with metadata."""
        equity = np.array([10000.0, 10500.0, 11000.0])
        returns = np.array([0.05, 0.0476])
        trades = [MockProductionTrade(pnl_usd=500.0)]

        metrics = calculate_extended_metrics(
            equity_curve=equity,
            returns=returns,
            trades=trades,
            annualized_return=20.0,
            initial_balance=10000.0,
            observed_sharpe=1.5,
            n_trials=1,
        )

        result = extended_metrics_to_dict_with_metadata(metrics)

        # Should have _meta section
        assert "_meta" in result

        # Meta should have info for each metric
        meta = result["_meta"]
        assert len(meta) == 61

        # Check structure of one meta entry
        assert "category" in meta["ulcer_index"]
        assert "unit" in meta["ulcer_index"]
        assert "quality" in meta["ulcer_index"]

        # Check values are present
        assert "ulcer_index" in result
        assert isinstance(result["ulcer_index"], (int, float))


class TestNaNHandling:
    """Test NaN handling for PARTIAL/PROXY metrics."""

    def test_partial_metrics_nan_allowed(self):
        """PARTIAL quality metrics can be NaN without validation error."""
        equity = np.array([10000.0, 10500.0, 11000.0])
        returns = np.array([0.05, 0.0476])
        trades = [MockProductionTrade(pnl_usd=500.0)]

        metrics = calculate_extended_metrics(
            equity_curve=equity,
            returns=returns,
            trades=trades,
            annualized_return=20.0,
            initial_balance=10000.0,
            observed_sharpe=1.5,
            n_trials=1,
        )

        # All REAL quality metrics should have non-NaN values
        assert not np.isnan(metrics.ulcer_index)
        assert not np.isnan(metrics.psr)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
