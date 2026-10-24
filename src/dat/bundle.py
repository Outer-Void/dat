"""`dat bundle` (alias `dat cmd`): concatenate main source files into one Markdown document.

Secrets are masked with the same detectors the secrets analyzer uses. Not an audit capability.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from dat.analyzers.secrets import GENERIC, PLACEHOLDER, SHAPES
from dat.errors import EXIT_FAILURE, EXIT_OK, DatError
from dat.inventory import Inventory

DEFAULT_EXTS = (
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".js",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".pl",
    ".lua",
    ".scala",
    ".kt",
)
DEFAULT_NAMES = ("Makefile", "GNUmakefile", "Dockerfile")
DEFAULT_IGNORES = ("dist", "build", "artifacts", "coverage", "*.egg-info", ".idea", ".vscode")
LANG = {
    ".py": "python",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".ps1": "powershell",
    ".bat": "bat",
    ".cmd": "bat",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".pl": "perl",
    ".lua": "lua",
    ".scala": "scala",
    ".kt": "kotlin",
}
MASK = "***REDACTED***"


def mask_secrets(text: str) -> str:
    for shape in SHAPES:
        text = shape.pattern.sub(MASK, text)

    def generic(m: re.Match[str]) -> str:
        value = m.group("value")
        if PLACEHOLDER.search(value):
            return m.group(0)
        return m.group(0).replace(value, MASK)

    return GENERIC.sub(generic, text)


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--out", "-o", default="artifacts/CODEBASE.md", help="output Markdown file")
    p.add_argument("--exts", default=",".join(DEFAULT_EXTS), help="comma-separated extensions")
    p.add_argument("--names", default=",".join(DEFAULT_NAMES), help="comma-separated base names")
    p.add_argument("--ignore", default=",".join(DEFAULT_IGNORES), help="comma-separated dirs/globs to skip")


def render(inv: Inventory, exts: tuple[str, ...], names: tuple[str, ...]) -> tuple[str, int]:
    chosen = [f for f in inv.files if f.name in names or f.suffix in exts]
    lines = ["# DAT bundle: main code and scripts", "", f"- **Files**: {len(chosen)}", ""]
    for f in chosen:
        text = inv.text(f)
        if text is None:
            continue
        lang = (
            "make"
            if f.name in ("Makefile", "GNUmakefile")
            else "dockerfile"
            if f.name == "Dockerfile"
            else (LANG.get(f.suffix, ""))
        )
        fence = "````" if "```" in text else "```"
        lines += [f"## `{f.rel}`", "", f"{fence}{lang}", mask_secrets(text).rstrip("\n"), fence, ""]
    return "\n".join(lines) + "\n", len(chosen)


def run(args: argparse.Namespace) -> int:
    exts = tuple(s.strip().lower() for s in args.exts.split(",") if s.strip())
    names = tuple(s.strip() for s in args.names.split(",") if s.strip())
    ignores = [s.strip() for s in args.ignore.split(",") if s.strip()]
    out = Path(args.out)
    try:
        inv = Inventory.build(Path(args.path), ignore=[*ignores, out.as_posix()])
    except DatError as exc:
        print(f"dat: {exc.code.value}: {exc.message}", file=sys.stderr)
        return EXIT_FAILURE
    text, count = render(inv, exts, names)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"dat: OUTPUT_FAILED: {exc.strerror}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"dat: wrote {out} ({count} files)", file=sys.stderr)
    return EXIT_OK
