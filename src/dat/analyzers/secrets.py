"""Credential detection by exact token shape, plus a guarded generic assignment rule.

Shape rules (cloud keys, tokens, private keys) are high confidence. The generic rule only fires on a
quoted literal assigned to a credential-like name, never on references such as os.environ["API_KEY"],
and ignores placeholders and low-entropy values.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass

from dat.analyzers.base import Analyzer, Rule, RunOptions, line_of
from dat.inventory import Inventory
from dat.model import Category, Confidence, Finding, Severity, redact

C, H, M, L = Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW
HC, MC, LC = Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW
SEC = Category.SECURITY
ROTATE = "Remove it from the source, rotate the credential, and load it from the environment or a secret store."


@dataclass(frozen=True)
class Shape:
    rule_id: str
    title: str
    pattern: re.Pattern[str]
    severity: Severity
    confidence: Confidence
    group: int = 0


SHAPES: tuple[Shape, ...] = (
    Shape(
        "secrets.private-key",
        "Private key",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"),
        C,
        HC,
    ),
    Shape("secrets.aws-access-key-id", "AWS access key ID", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), H, HC),
    Shape(
        "secrets.github-token",
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"),
        C,
        HC,
    ),
    Shape(
        "secrets.anthropic-key",
        "Anthropic API key",
        re.compile(r"\bsk-ant-(?:api|admin)\d{2}-[A-Za-z0-9_-]{20,}"),
        C,
        HC,
    ),
    Shape(
        "secrets.openai-key",
        "OpenAI API key",
        re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?(?!ant-)[A-Za-z0-9_-]{32,}"),
        C,
        MC,
    ),
    Shape("secrets.slack-token", "Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), H, HC),
    Shape(
        "secrets.slack-webhook",
        "Slack incoming webhook",
        re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]{16,}"),
        H,
        HC,
    ),
    Shape("secrets.stripe-key", "Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}"), C, HC),
    Shape("secrets.google-api-key", "Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), H, HC),
    Shape(
        "secrets.telegram-bot-token",
        "Telegram bot token",
        re.compile(r"\b\d{8,10}:AA[0-9A-Za-z_-]{33}\b"),
        H,
        HC,
    ),
    Shape(
        "secrets.jwt",
        "JSON Web Token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        M,
        MC,
    ),
    Shape(
        "secrets.url-credentials",
        "Credentials in URL",
        re.compile(r"\b[a-z][a-z0-9+.-]{1,15}://([^/\s:@'\"]{1,64}):([^/\s:@'\"]{3,128})@[\w.-]+"),
        H,
        MC,
        2,
    ),
)

# name = "literal" / name: 'literal' / "name": "literal"
GENERIC = re.compile(
    r"""(?ix)
    (?:(?<=\\[nrt])|(?<![a-z0-9_]))
    (?P<name>[a-z0-9_.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key|client[_-]?secret)[a-z0-9_.-]*)
    ["']?\s*(?::|=|:=)\s*
    (?P<q>["'])(?P<value>[^"'\s]{8,256})(?P=q)
    """
)
PLACEHOLDER = re.compile(
    r"(?i)(^\$[a-z_({]|^\{[a-z_][\w.]*\}$|\$\{|\{\{|<[a-z_-]+>|^\*+$|x{4,}|changeme|change_me|example|placeholder|dummy|your[_-]|redacted|"
    r"^test|fake|sample|^\.\.\.|replace|todo|<|%\(|%s)"
)
# Integrity hashes and lock data are high-entropy by design.
GENERIC_SKIP_NAMES = frozenset(
    {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "go.sum"}
)
AWS_DOC_EXAMPLE = re.compile(r"EXAMPLE$")
# Values that look like demo data are still reported, but below the default gate confidence.
EXAMPLE_LIKE = re.compile(r"(?i)(12345|abcdef|password|secret|qwerty|letmein|admin|foobar|hunter2)")
DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
WEAK_URL_PASSWORDS = frozenset({"pass", "password", "passwd", "pwd", "secret", "changeme", "user"})


def entropy(value: str) -> float:
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def scan_text(text: str) -> Iterator[tuple[str, int, int, str, Confidence | None]]:
    """Yield (rule_id, line, column, redacted evidence, confidence override). Shared with artifact.audit."""
    for shape in SHAPES:
        for m in shape.pattern.finditer(text):
            secret = m.group(shape.group)
            if shape.rule_id == "secrets.aws-access-key-id" and AWS_DOC_EXAMPLE.search(secret):
                continue
            override: Confidence | None = None
            if shape.rule_id == "secrets.url-credentials":
                if PLACEHOLDER.search(secret):
                    continue
                if secret.lower() in WEAK_URL_PASSWORDS or EXAMPLE_LIKE.search(secret):
                    override = LC
            start = m.start()
            col = start - text.rfind("\n", 0, start)
            evidence = "-----BEGIN … PRIVATE KEY-----" if shape.rule_id == "secrets.private-key" else redact(secret)
            yield shape.rule_id, line_of(text, start), col, evidence, override


def scan_generic(text: str) -> Iterator[tuple[int, int, str, str, bool]]:
    """Yield (line, column, name, redacted evidence, looks_like_example)."""
    for m in GENERIC.finditer(text):
        value = m.group("value")
        if PLACEHOLDER.search(value) or entropy(value) < 3.0 or value.lower() in m.group("name").lower():
            continue  # placeholder, low entropy, or a key that merely repeats its own name
        if any(s.pattern.search(value) for s in SHAPES):
            continue  # already reported by its shape rule
        start = m.start("value")
        col = start - text.rfind("\n", 0, start)
        yield line_of(text, start), col, m.group("name"), redact(value, keep=2), bool(EXAMPLE_LIKE.search(value))


RULES = (
    *(Rule(s.rule_id, SEC, s.severity, s.confidence, s.title, ROTATE) for s in SHAPES),
    Rule(
        "secrets.hardcoded-credential",
        SEC,
        H,
        MC,
        "Hard-coded credential",
        "Load the value from the environment or a secret store instead of committing it.",
    ),
)
_BY_ID = {s.rule_id: s for s in SHAPES}


class SecretsAnalyzer(Analyzer):
    id = "secrets"
    version = "1.0.0"
    rules = RULES

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        for entry in inv.files:
            text = inv.text(entry)
            if text is None:
                continue
            for rule_id, line, col, evidence, confidence in scan_text(text):
                yield self.finding(
                    rule_id,
                    f"{_BY_ID[rule_id].title} in {entry.rel}.",
                    path=entry.rel,
                    line=line,
                    column=col,
                    evidence=evidence,
                    confidence=confidence,
                )
            if entry.name in GENERIC_SKIP_NAMES:
                continue
            for line, col, name, evidence, example in scan_generic(text):
                low = example or entry.suffix in DOC_SUFFIXES
                yield self.finding(
                    "secrets.hardcoded-credential",
                    f"Literal value assigned to {name!r}.",
                    path=entry.rel,
                    line=line,
                    column=col,
                    evidence=evidence,
                    confidence=LC if low else None,
                )
