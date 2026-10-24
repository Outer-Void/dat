"""Repository hygiene: merge-conflict markers, work markers, oversized files, mixed line endings."""

from __future__ import annotations

import re
from collections.abc import Iterator

from dat.analyzers.base import Analyzer, Rule, RunOptions
from dat.inventory import Inventory
from dat.model import Category, Confidence, Finding, Severity

Q = Category.QUALITY
CONFLICT_START = re.compile(r"^<{7}(?: |$)", re.M)
CONFLICT_MID = re.compile(r"^={7}$", re.M)
CONFLICT_END = re.compile(r"^>{7}(?: |$)", re.M)
# Only in a comment, and only as a whole word: TODO_LIST or "todo" in prose does not count.
WORK_MARKER = re.compile(r"(?:#|//|/\*|<!--|--|;)[^\n]*?\b(TODO|FIXME|XXX|HACK)\b")  # dat:ignore[quality.work-marker]
LARGE_TEXT = 1024 * 1024
LARGE_BINARY = 10 * 1024 * 1024


class QualityAnalyzer(Analyzer):
    id = "quality"
    version = "1.0.0"
    rules = (
        Rule(
            "quality.merge-conflict",
            Q,
            Severity.HIGH,
            Confidence.HIGH,
            "Unresolved merge conflict",
            "Resolve the conflict and remove the markers.",
        ),
        Rule("quality.work-marker", Q, Severity.INFO, Confidence.MEDIUM, "Work marker comment"),
        Rule(
            "quality.large-file",
            Q,
            Severity.LOW,
            Confidence.HIGH,
            "Large file in repository",
            "Keep large or generated files out of version control (release assets, LFS, or .datignore).",
        ),
        Rule("quality.mixed-line-endings", Q, Severity.LOW, Confidence.HIGH, "Mixed line endings"),
    )

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        for entry in inv.files:
            text = inv.text(entry)
            if text is None:
                if entry.size > LARGE_BINARY:
                    yield self.finding("quality.large-file", f"{entry.size} byte binary file.", path=entry.rel)
                continue
            if entry.size > LARGE_TEXT:
                yield self.finding("quality.large-file", f"{entry.size} byte text file.", path=entry.rel)
            yield from self._conflicts(entry.rel, text)
            for m in WORK_MARKER.finditer(text):
                line = text.count("\n", 0, m.start(1)) + 1
                yield self.finding("quality.work-marker", f"{m.group(1)} comment.", path=entry.rel, line=line)
            crlf = text.count("\r\n")
            if crlf and text.count("\n") > crlf:
                yield self.finding("quality.mixed-line-endings", "File mixes CRLF and LF line endings.", path=entry.rel)

    def _conflicts(self, rel: str, text: str) -> Iterator[Finding]:
        # A real conflict has all three markers in order. A lone "=======" is a Markdown/RST heading rule.
        for start in CONFLICT_START.finditer(text):
            mid = CONFLICT_MID.search(text, start.end())
            end = CONFLICT_END.search(text, mid.end()) if mid else None
            if mid and end:
                line = text.count("\n", 0, start.start()) + 1
                yield self.finding("quality.merge-conflict", "Merge conflict markers.", path=rel, line=line)
