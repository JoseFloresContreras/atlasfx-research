#!/usr/bin/env python3
"""
AtlasFX Pathing Utilities (v3.3)
--------------------------------
Centralized, cross-platform path resolver for the AtlasFX project.

Purpose:
    - Ensures all modules (loaders, validators, tests, scripts)
      can access configs, fixtures, and logs.
    - Provides consistent repo-root resolution
      across Windows, macOS, Linux, and CI.
    - Eliminates fragile relative path logic.

Key features:
    ✅ Auto-detects repo root via `.git` or `pyproject.toml`
    ✅ Cached repo root resolution (fast + stable)
    ✅ Cross-platform compatible
    ✅ Includes helpers for configs, logs, fixtures, data
    ✅ Safe to import in any context
"""

from functools import lru_cache
import logging
import os
from pathlib import Path


# ============================================================
# CORE PATH RESOLUTION
# ============================================================


@lru_cache(maxsize=1)
def resolve_repo_root() -> Path:
    """
    Detect and return the absolute path to the repository root.

    Priority:
        1. Directory containing `.git` or `pyproject.toml`
        2. Fallback: ascend 3 levels up from this file

    Returns
    -------
    Path
        Absolute path to the repository root
    """
    current = Path(__file__).resolve()

    for parent in current.parents:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            # Sanity check: must contain AtlasFX source
            if (parent / "src" / "atlasfx").exists():
                return parent

    # Fallback for packaged environments (e.g. PyInstaller, CI cache)
    fallback = current.parents[3]
    logging.warning(f"[pathing] ⚠️ Using fallback repo root: {fallback}")
    return fallback


def cd_repo_root(silent: bool = False) -> None:
    """
    Change current working directory to the repository root.

    Parameters
    ----------
    silent : bool, optional
        If True, suppress console output (default=False).
    """
    root = resolve_repo_root()
    os.chdir(root)
    if not silent:
        print(f"📂 Changed working directory to repo root: {root}")


def resolve_path(path: str | Path) -> Path:
    """
    Resolve any given path to an absolute path relative to the repository root.

    Parameters
    ----------
    path : str | Path
        Input path (absolute or relative)

    Returns
    -------
    Path
        Absolute path (existing or potential target)
    """
    p = Path(path)
    return p if p.is_absolute() else resolve_repo_root() / p


# ============================================================
# COMMON DIRECTORY HELPERS
# ============================================================


def get_configs_dir() -> Path:
    """Return the absolute path to configs/ directory."""
    return resolve_repo_root() / "configs"


def get_logs_dir() -> Path:
    """Return the absolute path to logs/ directory."""
    return resolve_repo_root() / "logs"


def get_tests_dir() -> Path:
    """Return the absolute path to tests/ directory."""
    return resolve_repo_root() / "tests"


def get_fixtures_dir() -> Path:
    """Return the absolute path to tests/fixtures/ directory."""
    return get_tests_dir() / "fixtures"


def get_scripts_dir() -> Path:
    """Return the absolute path to scripts/ directory."""
    return resolve_repo_root() / "scripts"


def get_data_dir(subdir: str | None = None) -> Path:
    """
    Return the absolute path to the project's data/ directory, optionally resolving a subfolder.

    Parameters
    ----------
    subdir : Optional[str]
        Subfolder name inside data/

    Returns
    -------
    Path
        Path to the data directory or its subfolder.
    """
    base = resolve_repo_root() / "data"
    return base / subdir if subdir else base


def get_tmp_dir(create: bool = True) -> Path:
    """
    Return the path to tests/tmp (temporary directory for pipelines).

    Parameters
    ----------
    create : bool
        Whether to create it if missing (default=True)
    """
    tmp = get_tests_dir() / "tmp"
    if create:
        os.makedirs(tmp, exist_ok=True)
    return tmp


# ============================================================
# VALIDATION AND DIAGNOSTICS
# ============================================================


def verify_structure() -> dict[str, bool]:
    """
    Check that all expected top-level directories exist.

    Returns
    -------
    dict[str, bool]
        Map of folder names → existence flag
    """
    expected = ["configs", "logs", "scripts", "tests", "src"]
    root = resolve_repo_root()
    return {d: (root / d).exists() for d in expected}


def print_diagnostics() -> None:
    """Print resolved directories and structure verification summary."""
    print("\n🔍 AtlasFX Pathing Diagnostics")
    print("=" * 60)
    print(f"Repo root:        {resolve_repo_root()}")
    print(f"Configs dir:      {get_configs_dir()}")
    print(f"Logs dir:         {get_logs_dir()}")
    print(f"Scripts dir:      {get_scripts_dir()}")
    print(f"Tests dir:        {get_tests_dir()}")
    print(f"Fixtures dir:     {get_fixtures_dir()}")
    print(f"Data dir:         {get_data_dir()}")
    print(f"Tmp dir:          {get_tmp_dir(create=False)}")
    print("=" * 60)
    print("Structure check:", verify_structure())


# ============================================================
# RUNTIME ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    print_diagnostics()
