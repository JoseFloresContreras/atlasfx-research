"""
Forex Notional Calculation Module

Provides standard functions for calculating notional values and exposure
in USD for forex pairs. Handles currency conversion logic properly for
major pairs.

Author: AtlasFX Team
Date: December 30, 2025
"""

from __future__ import annotations


def notional_usd(symbol: str, units: float, price: float) -> float:
    """
    Calculate notional value in USD for a forex position.

    Rules for major pairs:
    - XXXUSD (e.g., EURUSD, GBPUSD): units are in base currency (XXX)
      -> notional_usd = abs(units) * price
    - USDXXX (e.g., USDJPY, USDCAD): units are ALREADY in USD
      -> notional_usd = abs(units)
    - Cross pairs: Not implemented (raise NotImplementedError)

    Args:
        symbol: Trading pair symbol (e.g., "EURUSD", "USDJPY")
        units: Position size in base currency units (signed)
        price: Current market price

    Returns:
        Notional value in USD

    Raises:
        NotImplementedError: If symbol is not a major pair (XXX/USD or USD/XXX)

    Examples:
        >>> # EURUSD: 100,000 EUR at 1.1000 = $110,000 notional
        >>> notional_usd("EURUSD", 100_000, 1.1000)
        110000.0

        >>> # USDJPY: 100,000 units are already in USD = $100,000 notional
        >>> notional_usd("USDJPY", 100_000, 150.00)
        100000.0

        >>> # Negative units (short position) - same notional
        >>> notional_usd("EURUSD", -100_000, 1.1000)
        110000.0
    """
    symbol_upper = symbol.upper().replace("-PAIR", "")

    # XXXUSD pairs: units in base currency, convert to USD
    if symbol_upper.endswith("USD"):
        return abs(units) * price

    # USDXXX pairs: units already in USD
    if symbol_upper.startswith("USD"):
        return abs(units)

    # Cross pairs not supported
    raise NotImplementedError(
        f"Notional calculation for cross pair '{symbol}' not implemented. "
        f"Only major pairs (XXX/USD or USD/XXX) are supported."
    )


def lots_from_units(units: float, lot_size: float = 100_000.0) -> float:
    """
    Convert position units to standard lots.

    Args:
        units: Position size in base currency units (signed)
        lot_size: Standard lot size (default: 100,000)

    Returns:
        Position size in lots (always positive)

    Examples:
        >>> lots_from_units(100_000)
        1.0

        >>> lots_from_units(50_000)
        0.5

        >>> lots_from_units(-250_000)
        2.5
    """
    return abs(units) / lot_size


def exposure_usd(
    positions: dict[str, tuple[float, float]],
) -> tuple[float, float, float]:
    """
    Calculate USD exposure for a portfolio of positions.

    Args:
        positions: Dict mapping symbol -> (units, price)

    Returns:
        Tuple of (gross_exposure_usd, long_exposure_usd, short_exposure_usd)

    Examples:
        >>> positions = {
        ...     "EURUSD": (100_000, 1.1000),   # Long 1 lot EUR
        ...     "USDJPY": (-100_000, 150.00),  # Short 1 lot USD
        ... }
        >>> gross, long_exp, short_exp = exposure_usd(positions)
        >>> gross
        210000.0
        >>> long_exp
        110000.0
        >>> short_exp
        100000.0
    """
    gross = 0.0
    long_exp = 0.0
    short_exp = 0.0

    for symbol, (units, price) in positions.items():
        notional = notional_usd(symbol, units, price)
        gross += notional

        if units > 0:
            long_exp += notional
        else:
            short_exp += notional

    return gross, long_exp, short_exp
