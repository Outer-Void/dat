"""Dockerfile / Containerfile checks."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from dat.analyzers.base import Analyzer, Rule, RunOptions
from dat.inventory import FileEntry, Inventory
from dat.model import Category, Confidence, Finding, Severity

S = Category.SECURITY
PIPE_TO_SHELL = re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b")
SECRET_NAME = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|private[_-]?key)")


@dataclass(frozen=True)
class Instruction:
    line: int
    op: str
    args: str


def _is_containerfile(entry: FileEntry) -> bool:
    n = entry.name
    return (
        n in ("Dockerfile", "Containerfile")
        or n.startswith(("Dockerfile.", "Containerfile."))
        or n.endswith(".dockerfile")
    )


def parse(text: str) -> list[Instruction]:
    out: list[Instruction] = []
    buf: list[str] = []
    start = 0
    for no, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue
        if not buf:
            start = no
        if stripped.endswith("\\"):
            buf.append(stripped[:-1])
            continue
        buf.append(stripped)
        joined = " ".join(p for p in buf if p and not p.startswith("#"))
        buf = []
        op, _, args = joined.partition(" ")
        out.append(Instruction(start, op.upper(), args.strip()))
    return out


class ContainerAnalyzer(Analyzer):
    id = "container"
    version = "1.0.0"
    rules = (
        Rule(
            "container.unpinned-base-image",
            S,
            Severity.MEDIUM,
            Confidence.HIGH,
            "Base image not pinned",
            "Pin a version tag, ideally with an @sha256 digest.",
        ),
        Rule(
            "container.runs-as-root",
            S,
            Severity.MEDIUM,
            Confidence.MEDIUM,
            "Container runs as root",
            "Add a USER instruction with a non-root user.",
        ),
        Rule("container.remote-add", S, Severity.MEDIUM, Confidence.HIGH, "ADD from a URL", "Use curl + checksum."),
        Rule("container.pipe-to-shell", S, Severity.HIGH, Confidence.HIGH, "Download piped to a shell"),
        Rule("container.secret-in-env", S, Severity.HIGH, Confidence.MEDIUM, "Secret baked into image"),
    )

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        for entry in inv.files:
            if not _is_containerfile(entry):
                continue
            text = inv.text(entry)
            if text is not None:
                yield from self._check(entry.rel, parse(text))

    def _check(self, rel: str, instrs: list[Instruction]) -> Iterator[Finding]:
        stages: set[str] = set()
        last_user: str | None = None
        final_from: Instruction | None = None
        for ins in instrs:
            if ins.op == "FROM":
                final_from, last_user = ins, None
                words = [w for w in ins.args.split() if not w.startswith("--")]
                image = words[0] if words else ""
                if len(words) >= 3 and words[1].lower() == "as":
                    stages.add(words[2].lower())
                if image.lower() in stages or image == "scratch" or "$" in image or "@sha256:" in image:
                    continue
                tag = image.rsplit("/", 1)[-1].partition(":")[2]
                if not tag or tag == "latest":
                    yield self.finding(
                        "container.unpinned-base-image", f"FROM {image}.", path=rel, line=ins.line, evidence=image
                    )
            elif ins.op == "USER":
                last_user = ins.args.split(":")[0].strip()
            elif ins.op == "ADD" and re.search(r"(?:^|\s)https?://", ins.args):
                yield self.finding("container.remote-add", "ADD fetches a URL.", path=rel, line=ins.line)
            elif ins.op == "RUN" and PIPE_TO_SHELL.search(ins.args):
                yield self.finding(
                    "container.pipe-to-shell", "Remote script piped to a shell.", path=rel, line=ins.line
                )
            elif ins.op in ("ENV", "ARG"):
                yield from self._env(rel, ins)
        if final_from is not None and (last_user is None or last_user in ("root", "0")):
            yield self.finding(
                "container.runs-as-root", "Final stage has no non-root USER.", path=rel, line=final_from.line
            )

    def _env(self, rel: str, ins: Instruction) -> Iterator[Finding]:
        for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)(?:=|\s+)(\"[^\"]*\"|'[^']*'|\S+)?", ins.args):
            name, value = m.group(1), (m.group(2) or "").strip("\"'")
            if SECRET_NAME.search(name) and value and not value.startswith("$"):
                yield self.finding(
                    "container.secret-in-env", f"{ins.op} {name} has a literal value.", path=rel, line=ins.line
                )
