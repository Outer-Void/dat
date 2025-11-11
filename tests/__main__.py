"""DAT Test Suite Initialization.

This module configures the testing environment for the DAT package.
It ensures consistent imports, logging setup, and compatibility across
Python versions (3.9–3.13).
"""

from __future__ import annotations

import pathlib
import sys
import warnings


# Ensure the src/ directory is on the import path for local testing
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Optional: Suppress known irrelevant warnings during tests
warnings.filterwarnings("ignore", category=DeprecationWarning, module="dat")

# Basic smoke indicator for pytest collection
__all__ = ["ROOT", "SRC"]

# Optional: minimal test-time logger setup
try:
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("dat").info("Initialized DAT test environment.")
except Exception:
    pass
