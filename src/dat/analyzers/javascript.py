"""JavaScript / npm: install-time scripts, unpinned dependencies, missing lockfiles, dynamic code."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from dat.analyzers.base import Analyzer, Rule, RunOptions
from dat.inventory import FileEntry, Inventory
from dat.model import Category, Confidence, Finding, Severity

P, S = Category.PACKAGE, Category.SECURITY
LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb")
INSTALL_HOOKS = ("preinstall", "install", "postinstall", "prepare")
DEP_FIELDS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
JS_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
DYNAMIC_CODE = re.compile(r"(?<![\w.$])(?:eval\s*\(|new\s+Function\s*\()")
COMMIT = re.compile(r"#[0-9a-f]{40}$")


def _unpinned(spec: str) -> str | None:
    s = spec.strip()
    if s in ("", "*", "latest", "x", "next"):
        return "floating version"
    if s.startswith(("git+", "git:", "github:", "http:", "https:")) or re.match(r"^[\w.-]+/[\w.-]+(#.*)?$", s):
        return None if COMMIT.search(s) else "VCS or URL dependency without a pinned commit"
    return None


class JavaScriptAnalyzer(Analyzer):
    id = "javascript"
    version = "1.0.0"
    rules = (
        Rule("js.manifest-invalid", P, Severity.HIGH, Confidence.HIGH, "package.json is not valid JSON"),
        Rule(
            "js.install-script",
            S,
            Severity.MEDIUM,
            Confidence.HIGH,
            "Install-time lifecycle script",
            "Install scripts run with the installing user's rights. Avoid them or document why they are needed.",
        ),
        Rule("js.unpinned-dependency", P, Severity.MEDIUM, Confidence.HIGH, "Unpinned dependency"),
        Rule(
            "js.missing-lockfile",
            P,
            Severity.MEDIUM,
            Confidence.HIGH,
            "Dependencies without a lockfile",
            "Commit package-lock.json (or the lockfile of your package manager) for reproducible installs.",
        ),
        Rule("js.dynamic-code", S, Severity.MEDIUM, Confidence.LOW, "eval / new Function"),
    )

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        names = {f.rel for f in inv.files}
        for entry in inv.matching("package.json"):
            yield from self._manifest(inv, entry, names)
        for entry in inv.files:
            if entry.suffix in JS_SUFFIXES and ".min." not in entry.name:
                text = inv.text(entry)
                if text is None:
                    continue
                for m in DYNAMIC_CODE.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    yield self.finding("js.dynamic-code", "Dynamic code evaluation.", path=entry.rel, line=line)

    def _manifest(self, inv: Inventory, entry: FileEntry, names: set[str]) -> Iterator[Finding]:
        text = inv.text(entry)
        if text is None:
            return
        try:
            doc: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            yield self.finding("js.manifest-invalid", f"JSON error: {exc.msg}.", path=entry.rel, line=exc.lineno)
            return
        if not isinstance(doc, dict):
            yield self.finding("js.manifest-invalid", "package.json is not an object.", path=entry.rel)
            return
        raw_scripts = doc.get("scripts")
        scripts: dict[str, Any] = raw_scripts if isinstance(raw_scripts, dict) else {}
        for hook in INSTALL_HOOKS:
            if isinstance(scripts.get(hook), str):
                yield self.finding(
                    "js.install-script", f"scripts.{hook} runs on install.", path=entry.rel, evidence=scripts[hook]
                )
        has_deps = False
        for fld in DEP_FIELDS:
            deps = doc.get(fld)
            if not isinstance(deps, dict):
                continue
            has_deps = has_deps or (fld != "peerDependencies" and bool(deps))
            for name in sorted(deps):
                spec = deps[name]
                reason = _unpinned(spec) if isinstance(spec, str) else "non-string version"
                if reason:
                    yield self.finding(
                        "js.unpinned-dependency", f"{fld}.{name}: {reason}.", path=entry.rel, evidence=str(spec)
                    )
        directory = entry.rel.rsplit("/", 1)[0] + "/" if "/" in entry.rel else ""
        # Lockfiles are often in .datignore (*.lock), so the filesystem is checked too.
        locked = any(f"{directory}{lock}" in names or inv.path(f"{directory}{lock}").is_file() for lock in LOCKFILES)
        if has_deps and not locked and not doc.get("workspaces"):
            yield self.finding("js.missing-lockfile", "No lockfile next to package.json.", path=entry.rel)
