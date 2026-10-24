"""Release readiness: required files, version consistency, workflow duplication, and git state."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Iterator

from dat.analyzers.base import Analyzer, Rule, RunOptions, Skip
from dat.inventory import Inventory
from dat.model import Category, Confidence, Finding, Severity

R = Category.RELEASE
VERSION_ASSIGN = re.compile(r"""^\s*__version__\s*(?::\s*str\s*)?=\s*["']([^"']+)["']""", re.M)
LICENSE_NAMES = re.compile(r"^(LICEN[CS]E|COPYING)(\.[A-Za-z0-9]+)?$", re.I)
README_NAMES = re.compile(r"^README(\.[A-Za-z0-9]+)?$", re.I)
CHANGELOG_NAMES = re.compile(r"^(CHANGELOG|CHANGES|HISTORY|NEWS)(\.[A-Za-z0-9]+)?$", re.I)
GIT_TIMEOUT_S = 30


def declared_versions(inv: Inventory) -> dict[str, str]:
    """Version strings declared at the top of the target: source -> version."""
    out: dict[str, str] = {}
    py = inv.get("pyproject.toml")
    if py and (text := inv.text(py)):
        try:
            v = (tomllib.loads(text).get("project") or {}).get("version")
            if isinstance(v, str):
                out["pyproject.toml"] = v
        except tomllib.TOMLDecodeError:
            pass
    pkg = inv.get("package.json")
    if pkg and (text := inv.text(pkg)):
        try:
            doc = json.loads(text)
            if isinstance(doc, dict) and isinstance(doc.get("version"), str):
                out["package.json"] = doc["version"]
        except json.JSONDecodeError:
            pass
    for f in inv.files:
        parts = f.rel.split("/")
        top_level_pkg = (len(parts) == 3 and parts[0] == "src") or len(parts) == 2
        if top_level_pkg and f.name in ("__init__.py", "_version.py", "__version__.py") and (text := inv.text(f)):
            for m in VERSION_ASSIGN.finditer(text):
                out.setdefault(f"{f.rel}:{text.count(chr(10), 0, m.start()) + 1}", m.group(1))
    return out


class ReleaseAnalyzer(Analyzer):
    id = "release"
    version = "1.0.0"
    rules = (
        Rule("release.missing-license", R, Severity.HIGH, Confidence.HIGH, "No LICENSE file"),
        Rule("release.missing-readme", R, Severity.MEDIUM, Confidence.HIGH, "No README"),
        Rule("release.missing-changelog", R, Severity.LOW, Confidence.HIGH, "No changelog"),
        Rule("release.version-undeclared", R, Severity.MEDIUM, Confidence.HIGH, "No version declared"),
        Rule(
            "release.version-inconsistent",
            R,
            Severity.HIGH,
            Confidence.HIGH,
            "Declared versions disagree",
            "Use one source of truth for the version (e.g. importlib.metadata) and bump all declarations.",
        ),
        Rule("release.changelog-missing-version", R, Severity.LOW, Confidence.MEDIUM, "Changelog lacks the version"),
        Rule(
            "release.duplicate-workflow",
            R,
            Severity.MEDIUM,
            Confidence.HIGH,
            "Duplicate workflow (.yml and .yaml)",
            "Both files run. Keep one.",
        ),
    )

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        if inv.single_file:
            raise Skip("release checks need a repository directory")
        top = [f for f in inv.files if "/" not in f.rel]
        if not any(LICENSE_NAMES.match(f.name) for f in top):
            yield self.finding("release.missing-license", "No LICENSE/COPYING file at the root.")
        if not any(README_NAMES.match(f.name) for f in top):
            yield self.finding("release.missing-readme", "No README at the root.")
        changelogs = [f for f in inv.files if f.rel.count("/") <= 1 and CHANGELOG_NAMES.match(f.name)]
        if not changelogs:
            yield self.finding("release.missing-changelog", "No CHANGELOG at the root or one level down.")

        versions = declared_versions(inv)
        if not versions:
            yield self.finding(
                "release.version-undeclared", "No version in pyproject.toml, package.json or __version__."
            )
        elif len(set(versions.values())) > 1:
            listing = ", ".join(f"{src}={v}" for src, v in sorted(versions.items()))
            yield self.finding("release.version-inconsistent", f"Versions differ: {listing}.", evidence=listing)
        elif changelogs:
            version = next(iter(versions.values()))
            texts = [inv.text(c) or "" for c in changelogs]
            if not any(re.search(rf"(?<![\w.]){re.escape(version)}(?![\w.])", t) for t in texts):
                yield self.finding(
                    "release.changelog-missing-version",
                    f"No changelog entry mentions {version}.",
                    path=changelogs[0].rel,
                )

        stems: dict[str, list[str]] = {}
        for f in inv.files:
            if f.rel.startswith(".github/workflows/") and f.suffix in (".yml", ".yaml"):
                stems.setdefault(f.rel[: -len(f.suffix)], []).append(f.rel)
        for paths in stems.values():
            if len(paths) > 1:
                yield self.finding(
                    "release.duplicate-workflow", f"{' and '.join(sorted(paths))} both exist.", path=sorted(paths)[0]
                )


def _git(root: str, *args: str) -> str:
    exe = shutil.which("git")
    if exe is None:
        raise RuntimeError("git executable not found")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1"})
    proc = subprocess.run(
        [exe, "-C", root, "-c", "core.fsmonitor=false", *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed with exit {proc.returncode}")
    return proc.stdout


class GitStateAnalyzer(Analyzer):
    """Reads git state through the git CLI. A target without .git is skipped; a broken git is a failure."""

    id = "git"
    version = "1.0.0"
    rules = (
        Rule(
            "release.dirty-worktree",
            R,
            Severity.HIGH,
            Confidence.HIGH,
            "Uncommitted changes",
            "Release from a clean, committed tree.",
        ),
        Rule("release.untagged-version", R, Severity.MEDIUM, Confidence.HIGH, "HEAD is not tagged with the version"),
        Rule(
            "release.tag-mismatch",
            R,
            Severity.HIGH,
            Confidence.HIGH,
            "Version tag points at another commit",
            "This version was already tagged elsewhere; bump the version.",
        ),
    )

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        if inv.single_file or not (inv.root / ".git").exists():
            raise Skip("not a git repository")
        root = str(inv.root)
        status = _git(root, "status", "--porcelain=v1", "--untracked-files=normal").splitlines()
        if status:
            shown = ", ".join(sorted(s[3:] for s in status)[:5])
            yield self.finding("release.dirty-worktree", f"{len(status)} uncommitted change(s): {shown}.")
        versions = set(declared_versions(inv).values())
        if len(versions) != 1:
            return  # undeclared or inconsistent: reported by the release analyzer
        version = versions.pop()
        candidates = (f"v{version}", version)
        head = _git(root, "rev-parse", "HEAD").strip()
        at_head = set(_git(root, "tag", "--points-at", "HEAD").split())
        if at_head.intersection(candidates):
            return
        for tag in candidates:
            try:
                target = _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}").strip()
            except RuntimeError:
                continue
            if target and target != head:
                yield self.finding(
                    "release.tag-mismatch", f"Tag {tag} points at {target[:12]}, not HEAD {head[:12]}.", evidence=tag
                )
                return
        yield self.finding("release.untagged-version", f"No tag v{version} or {version} at HEAD.")
