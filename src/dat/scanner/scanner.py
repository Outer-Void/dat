"""Compatibility module exposing synchronous scanner APIs."""

from __future__ import annotations

from .sync import FileRecord, ScanResult, ScanStatistics, SkipEntry, scan_repository


__all__ = [
    "FileRecord",
    "ScanResult",
    "ScanStatistics",
    "SkipEntry",
    "scan_repository",
]
