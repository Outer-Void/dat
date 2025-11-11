"""Policy driven severity rule evaluation with enhanced capabilities."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    """A single audit rule."""

    rule_id: str
    description: str
    patterns: Sequence[str]
    severity: str = "medium"
    file_patterns: Sequence[str] | None = None
    case_sensitive: bool = False
    regex_patterns: bool = False


@dataclass(frozen=True)
class RuleViolation:
    """Represents a match against a rule."""

    rule_id: str
    severity: str
    message: str
    path: str
    line_number: int | None = None
    matched_pattern: str | None = None
    context: str | None = None


@dataclass
class Policy:
    """Container for active audit rules."""

    rules: list[Rule]

    def should_scan_file(self, path: Path) -> bool:
        """Check if a file should be scanned based on file patterns."""
        if not self.rules:
            return True

        str_path = str(path)
        for rule in self.rules:
            if not rule.file_patterns:
                return True
            for pattern in rule.file_patterns:
                if pattern in str_path or re.search(pattern, str_path):
                    return True
        return False

    def evaluate(self, *, path: Path, lines: Iterable[str]) -> list[RuleViolation]:
        """Evaluate policy rules against a file stream."""
        violations: list[RuleViolation] = []
        file_content = list(lines)

        for rule in self.rules:
            # Skip if file patterns don't match
            if rule.file_patterns and not self._matches_file_pattern(
                path, rule.file_patterns
            ):
                continue

            rule_violations = self._evaluate_rule(rule, path, file_content)
            violations.extend(rule_violations)

        return violations

    def _matches_file_pattern(self, path: Path, file_patterns: Sequence[str]) -> bool:
        """Check if file matches any of the file patterns."""
        str_path = str(path)
        for pattern in file_patterns:
            if pattern in str_path or re.search(pattern, str_path):
                return True
        return False

    def _evaluate_rule(
        self, rule: Rule, path: Path, lines: list[str]
    ) -> list[RuleViolation]:
        """Evaluate a single rule against file content."""
        violations = []

        for line_number, line in enumerate(lines, start=1):
            if rule.case_sensitive:
                search_line = line
            else:
                search_line = line.lower()

            for pattern in rule.patterns:
                search_pattern = pattern if rule.case_sensitive else pattern.lower()

                if rule.regex_patterns:
                    if re.search(search_pattern, search_line):
                        context = self._get_context(lines, line_number)
                        violations.append(
                            RuleViolation(
                                rule_id=rule.rule_id,
                                severity=rule.severity,
                                message=f"Matched regex pattern '{pattern}'",
                                path=str(path),
                                line_number=line_number,
                                matched_pattern=pattern,
                                context=context,
                            )
                        )
                elif search_pattern in search_line.strip():
                    context = self._get_context(lines, line_number)
                    violations.append(
                        RuleViolation(
                            rule_id=rule.rule_id,
                            severity=rule.severity,
                            message=f"Matched pattern '{pattern}'",
                            path=str(path),
                            line_number=line_number,
                            matched_pattern=pattern,
                            context=context,
                        )
                    )
        return violations

    def _get_context(
        self, lines: list[str], line_number: int, context_lines: int = 2
    ) -> str:
        """Get context around the matched line."""
        start = max(0, line_number - context_lines - 1)
        end = min(len(lines), line_number + context_lines)

        context = []
        for i in range(start, end):
            prefix = "> " if i == line_number - 1 else "  "
            context.append(f"{prefix}{i + 1}: {lines[i].rstrip()}")

        return "\n".join(context)


DEFAULT_RULES = [
    Rule(
        rule_id="secrets.api_key",
        description="Potential API key exposure",
        patterns=("API_KEY", "SECRET_KEY", "x-api-key", "api-key"),
        severity="high",
        file_patterns=[r"\.(py|js|ts|java|cpp|c|h|hpp|rb|go|rs|php)$"],
    ),
    Rule(
        rule_id="credentials.password",
        description="Potential password in source",
        patterns=("password=", "pwd=", "PASS=", "PASSWORD="),
        severity="critical",
        file_patterns=[
            r"\.(py|js|ts|java|cpp|c|h|hpp|rb|go|rs|php|env|cfg|conf|config|ini|yml|yaml|json)$"
        ],
    ),
    Rule(
        rule_id="secrets.aws_key",
        description="AWS access key ID",
        patterns=[r"AWS[\\s\\S]*AKIA[0-9A-Z]{16}"],
        severity="critical",
        regex_patterns=True,
        file_patterns=[
            r"\.(py|js|ts|java|cpp|c|h|hpp|rb|go|rs|php|env|cfg|conf|config|ini|yml|yaml|json)$"
        ],
    ),
    Rule(
        rule_id="no.todo",
        description="TODO found in tracked files",
        patterns=("TODO", "FIXME", "XXX", "HACK"),
        severity="low",
        file_patterns=[r"\.(py|js|ts|java|cpp|c|h|hpp|rb|go|rs|php|md|rst|txt)$"],
    ),
    Rule(
        rule_id="security.debug",
        description="Debug statements in code",
        patterns=("console.log", "print(", "debugger", "var_dump", "dd("),
        severity="medium",
        file_patterns=[r"\.(py|js|ts|php|rb)$"],
    ),
]


def load_default_policy(extra_rules: Sequence[Rule] | None = None) -> Policy:
    """Return the default policy merged with *extra_rules*."""
    rules = list(DEFAULT_RULES)
    if extra_rules:
        rules.extend(extra_rules)
    return Policy(rules=rules)


def create_custom_rule(
    rule_id: str,
    description: str,
    patterns: Sequence[str],
    severity: str = "medium",
    file_patterns: Sequence[str] | None = None,
    case_sensitive: bool = False,
    regex_patterns: bool = False,
) -> Rule:
    """Convenience function to create custom rules."""
    return Rule(
        rule_id=rule_id,
        description=description,
        patterns=patterns,
        severity=severity,
        file_patterns=file_patterns,
        case_sensitive=case_sensitive,
        regex_patterns=regex_patterns,
    )
