"""
Pytest configuration and shared fixtures for AtlasFX MVP tests.
Ensures `src` is visible for both pytest and subprocess calls (CLI validators, pipelines).
"""

import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest


# === Ensure src/ is visible globally ===
project_root = Path(__file__).resolve().parents[1]
src_path = project_root / "src"

# Add to sys.path for pytest imports
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Export PYTHONPATH so subprocesses inherit visibility (e.g., `python -m atlasfx.data.validators`)
os.environ["PYTHONPATH"] = str(src_path)


# === Shared fixtures ===
@pytest.fixture
def sample_tick_data():
    """Create sample tick data for testing."""
    n_rows = 100
    start_time = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")

    timestamps = [start_time + pd.Timedelta(seconds=i) for i in range(n_rows)]
    bid_prices = np.random.uniform(1.0, 1.1, n_rows)
    ask_prices = bid_prices + np.random.uniform(0.0001, 0.001, n_rows)

    return pd.DataFrame(
        {
            "timestamp": [int(ts.timestamp() * 1000) for ts in timestamps],
            "askPrice": ask_prices,
            "bidPrice": bid_prices,
            "askVolume": np.random.uniform(100, 1000, n_rows),
            "bidVolume": np.random.uniform(100, 1000, n_rows),
        }
    )


@pytest.fixture
def sample_aggregated_data():
    """Create sample aggregated data for testing."""
    n_rows = 50
    start_time = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
    timestamps = [start_time + pd.Timedelta(minutes=5 * i) for i in range(n_rows)]

    data = pd.DataFrame(
        {
            "start_time": timestamps,
            "tick_count": np.random.randint(10, 100, n_rows),
            "high": np.random.uniform(1.05, 1.15, n_rows),
            "low": np.random.uniform(0.95, 1.05, n_rows),
            "close": np.random.uniform(1.0, 1.1, n_rows),
            "volume": np.random.uniform(1000, 10000, n_rows),
            "vwap": np.random.uniform(1.0, 1.1, n_rows),
            "ofi": np.random.uniform(-0.1, 0.1, n_rows),
            "micro_price": np.random.uniform(1.0, 1.1, n_rows),
        }
    )

    return data.set_index("start_time")


@pytest.fixture
def sample_config():
    """Create sample configuration dictionary for testing."""
    return {
        "time_window": "5min",
        "output_directory": "data",
        "split": {"train": 0.7, "val": 0.15, "test": 0.15},
    }
