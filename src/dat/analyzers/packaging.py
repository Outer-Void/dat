"""Python packaging: pyproject.toml metadata and requirement pinning."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator
from typing import Any

from dat.analyzers.base import Analyzer, Rule, RunOptions
from dat.inventory import FileEntry, Inventory
from dat.model import Category, Confidence, Finding, Severity

P, S = Category.PACKAGE, Category.SECURITY
VCS = re.compile(r"(?:git|hg|svn|bzr)\+[a-z]+://\S+", re.I)
VCS_COMMIT = re.compile(r"@[0-9a-f]{40}(?:#|$)")
REQ_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]*\])?")


class PackagingAnalyzer(Analyzer):
    id = "packaging"
    version = "1.0.0"
    rules = (
        Rule("package.pyproject-invalid", P, Severity.HIGH, Confidence.HIGH, "pyproject.toml does not parse"),
        Rule("package.missing-name", P, Severity.HIGH, Confidence.HIGH, "Project has no name"),
        Rule("package.missing-version", P, Severity.MEDIUM, Confidence.HIGH, "Project version not declared"),
        Rule(
            "package.missing-license",
            P,
            Severity.MEDIUM,
            Confidence.HIGH,
            "No license metadata",
            "Declare `license` (SPDX expression) and `license-files`.",
        ),
        Rule("package.missing-requires-python", P, Severity.LOW, Confidence.HIGH, "requires-python not declared"),
        Rule(
            "package.unpinned-vcs-dependency",
            S,
            Severity.HIGH,
            Confidence.HIGH,
            "VCS dependency not pinned to a commit",
            "Pin to a full 40-character commit (@<sha>); branches and tags can move.",
        ),
        Rule("package.unversioned-requirement", P, Severity.INFO, Confidence.HIGH, "Requirement without a version"),
        Rule(
            "package.insecure-index",
            S,
            Severity.HIGH,
            Confidence.HIGH,
            "Package index over plain HTTP",
            "Use an https:// index.",
        ),
        Rule("package.trusted-host", S, Severity.MEDIUM, Confidence.HIGH, "pip --trusted-host disables TLS checks"),
    )

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        for entry in inv.matching("pyproject.toml"):
            yield from self._pyproject(inv, entry)
        for entry in inv.files:
            n = entry.name
            if n.endswith(".txt") and (n.startswith("requirements") or "/requirements/" in f"/{entry.rel}"):
                yield from self._requirements(inv, entry)

    def _pyproject(self, inv: Inventory, entry: FileEntry) -> Iterator[Finding]:
        text = inv.text(entry)
        if text is None:
            return
        try:
            doc = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            yield self.finding("package.pyproject-invalid", f"TOML error: {exc}.", path=entry.rel)
            return
        project = doc.get("project")
        if not isinstance(project, dict):
            return  # tool-only pyproject (e.g. just [tool.ruff]); not a package
        dynamic = set(project.get("dynamic") or [])
        rel = entry.rel
        if not project.get("name"):
            yield self.finding("package.missing-name", "[project] has no name.", path=rel)
        if "version" not in project and "version" not in dynamic:
            yield self.finding("package.missing-version", "[project] has no version.", path=rel)
        if not project.get("license") and not project.get("license-files") and "license" not in dynamic:
            yield self.finding("package.missing-license", "[project] declares no license.", path=rel)
        if not project.get("requires-python"):
            yield self.finding("package.missing-requires-python", "[project] has no requires-python.", path=rel)
        deps: list[Any] = list(project.get("dependencies") or [])
        for extra in sorted((project.get("optional-dependencies") or {}).items()):
            deps.extend(extra[1] or [])
        for group in sorted((doc.get("dependency-groups") or {}).items()):
            deps.extend(d for d in (group[1] or []) if isinstance(d, str))
        for dep in deps:
            if isinstance(dep, str):
                yield from self._requirement(rel, None, dep, pyproject=True)

    def _requirements(self, inv: Inventory, entry: FileEntry) -> Iterator[Finding]:
        text = inv.text(entry)
        if text is None:
            return
        for no, raw in enumerate(text.splitlines(), start=1):
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-i ", "--index-url", "--extra-index-url", "-f ", "--find-links")):
                if "http://" in line:
                    yield self.finding("package.insecure-index", "Index URL uses http://.", path=entry.rel, line=no)
                continue
            if line.startswith("--trusted-host"):
                yield self.finding("package.trusted-host", line, path=entry.rel, line=no)
                continue
            if line.startswith(("-r ", "-c ", "--requirement", "--constraint", "-e ", "--editable")) and not VCS.search(
                line
            ):
                continue
            yield from self._requirement(entry.rel, no, line, pyproject=False)

    def _requirement(self, rel: str, line: int | None, spec: str, *, pyproject: bool) -> Iterator[Finding]:
        vcs = VCS.search(spec)
        if vcs:
            if not VCS_COMMIT.search(vcs.group(0)):
                yield self.finding(
                    "package.unpinned-vcs-dependency",
                    "VCS requirement is not pinned to a commit.",
                    path=rel,
                    line=line,
                    evidence=vcs.group(0)[:160],
                )
            return
        if pyproject or spec.startswith(("http://", "https://", "file:", ".", "/")):
            return  # ranges are normal for libraries; only applications pin
        m = REQ_NAME.match(spec)
        rest = spec[m.end() :].split(";", 1)[0].strip() if m else spec
        if m and not rest:
            yield self.finding("package.unversioned-requirement", f"{m.group(0)} has no version.", path=rel, line=line)
