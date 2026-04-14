"""
Professional Logging Module for AtlasFX Trading System.

This module provides a production-ready logging infrastructure with:
    - Structured logging with multiple handlers
    - Unicode support for international market symbols
    - Timestamped log files with automatic rotation
    - Context-aware logging for trading operations
    - Performance-optimized for high-frequency trading

Author: AtlasFX Team
Version: 3.0.0
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from typing import Any, ClassVar


# Custom log level
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


class ColoredFormatter(logging.Formatter):
    """Formatter with color codes for console output."""

    COLORS: ClassVar[dict[str, str]] = {
        "TRACE": "\033[90m",
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
        formatted = super().format(record)
        record.levelname = levelname
        return formatted


class AtlasFXLogger:
    """Production-grade logger for AtlasFX trading system."""

    def __init__(
        self,
        name: str = "atlasfx",
        log_dir: str = "logs",
        level: int = logging.INFO,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        enable_console: bool = True,
        enable_file: bool = True,
        enable_rotation: bool = True,
    ) -> None:
        self.name = name
        self.log_dir = Path(log_dir)
        self.level = level
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.propagate = False
        self.logger.handlers.clear()

        self.log_filepath: str | None = None
        self._setup_handlers(enable_console, enable_file, enable_rotation, max_bytes, backup_count)

        if enable_file:
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            self.log_filepath = str(self.log_dir / f"{name}_{timestamp}.log")

    def _setup_handlers(
        self,
        enable_console: bool,
        enable_file: bool,
        enable_rotation: bool,
        max_bytes: int,
        backup_count: int,
    ) -> None:
        file_fmt = logging.Formatter(
            "%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_fmt = ColoredFormatter(
            "%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
            datefmt="%H:%M:%S",
        )

        if enable_console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.DEBUG)
            console_handler.setFormatter(console_fmt)
            self.logger.addHandler(console_handler)

        if enable_file:
            self.log_dir.mkdir(parents=True, exist_ok=True)

            if enable_rotation:
                timestamp = datetime.now(UTC).strftime("%Y%m%d")
                log_file = self.log_dir / f"{self.name}_{timestamp}.log"
                rotating_handler = RotatingFileHandler(
                    str(log_file),
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
                rotating_handler.setLevel(logging.DEBUG)
                rotating_handler.setFormatter(file_fmt)
                self.logger.addHandler(rotating_handler)
            else:
                timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
                log_file = self.log_dir / f"{self.name}_{timestamp}.log"
                file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(file_fmt)
                self.logger.addHandler(file_handler)

    def trace(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.log(TRACE, message, *args, **kwargs)

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.critical(message, *args, **kwargs)

    def exception(self, message: str, *args: Any, exc_info: bool = True, **kwargs: Any) -> None:
        """Log exception with stack trace."""
        self.logger.exception(message, *args, exc_info=exc_info, **kwargs)

    def set_level(self, level: int | str) -> None:
        if isinstance(level, str):
            level_map = {
                "TRACE": TRACE,
                "DEBUG": logging.DEBUG,
                "INFO": logging.INFO,
                "WARNING": logging.WARNING,
                "ERROR": logging.ERROR,
                "CRITICAL": logging.CRITICAL,
            }
            level = level_map.get(level.upper(), logging.INFO)
        self.level = level
        self.logger.setLevel(level)

    def get_log_filepath(self) -> str | None:
        """Get path to log file."""
        return self.log_filepath

    def log_performance(self, operation: str, duration_ms: float, **metrics: Any) -> None:
        metric_str = " | ".join(f"{k}={v}" for k, v in metrics.items())
        self.info(f"  {operation} completed in {duration_ms:.2f}ms | {metric_str}")

    def log_trade(
        self,
        action: str,
        symbol: str,
        quantity: float,
        price: float,
        **context: Any,
    ) -> None:
        context_str = " | ".join(f"{k}={v}" for k, v in context.items())
        self.info(f" {action} {quantity:.4f} {symbol} @ {price:.5f} | {context_str}")


# Global factory
_loggers: dict[str, AtlasFXLogger] = {}
_default_config: dict[str, Any] = {
    "log_dir": "logs",
    "level": logging.INFO,
    "enable_console": True,
    "enable_file": True,
    "enable_rotation": True,
}


def setup_logging(
    log_dir: str = "logs",
    level: int | str = logging.INFO,
    enable_console: bool = True,
    enable_file: bool = True,
    enable_rotation: bool = True,
) -> None:
    """Setup global logging configuration."""
    global _default_config  # noqa: PLW0603
    if isinstance(level, str):
        level_map = {
            "TRACE": TRACE,
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        level = level_map.get(level.upper(), logging.INFO)
    _default_config = {
        "log_dir": log_dir,
        "level": level,
        "enable_console": enable_console,
        "enable_file": enable_file,
        "enable_rotation": enable_rotation,
    }
    Path(log_dir).mkdir(parents=True, exist_ok=True)


def get_logger(name: str) -> AtlasFXLogger:
    if name not in _loggers:
        _loggers[name] = AtlasFXLogger(name=name, **_default_config)
    return _loggers[name]


# Backward compatibility
class CustomLogger:
    """Legacy logger for backward compatibility. Use get_logger() instead."""

    def __init__(self, log_directory: str = "logs") -> None:
        self._logger = AtlasFXLogger(
            name="atlasfx_pipeline",
            log_dir=log_directory,
            level=logging.DEBUG,
        )
        self.log_directory = log_directory
        self.level = logging.DEBUG
        self.logger = self._logger.logger
        self.log_filepath = self._logger.get_log_filepath() or ""

    def set_level(self, level: str) -> None:
        """Set log level."""
        self._logger.set_level(level)
        self.level = self._logger.level

    def debug(self, message: str, also_print: bool = False) -> None:  # noqa: ARG002
        """Log debug message (legacy interface)."""
        self._logger.debug(message)

    def info(self, message: str, also_print: bool = False) -> None:  # noqa: ARG002
        """Log info message (legacy interface)."""
        self._logger.info(message)

    def warning(self, message: str, also_print: bool = False) -> None:  # noqa: ARG002
        """Log warning message (legacy interface)."""
        self._logger.warning(message)

    def error(self, message: str, also_print: bool = False) -> None:  # noqa: ARG002
        """Log error message (legacy interface)."""
        self._logger.error(message)

    def critical(self, message: str, also_print: bool = False) -> None:  # noqa: ARG002
        """Log critical message (legacy interface)."""
        self._logger.critical(message)

    def get_log_filepath(self) -> str:
        return self.log_filepath


# Global instance
log = CustomLogger()
