---
layout: page
title: Policy
---

# Policies (`dat.policy` v1)

A policy is a JSON file. DAT uses `--policy FILE`, else `TARGET/.dat/policy.json` (or `TARGET/dat-policy.json`),
else the built-in default (gate at `high`, confidence `medium`). Schema:
[`policy.v1.schema.json`](../src/dat/schemas/policy.v1.schema.json).

```json
{
  "contract": "dat.policy",
  "version": 1,
  "policy_id": "my-repo",
  "gate": {"fail_on": "high", "min_confidence": "medium"},
  "analyzers": {"disable": ["git"]},
  "severity_overrides": {"quality.work-marker": "low"},
  "suppress": [
    {"rule_id": "secrets.*", "path": "tests/fixtures/*", "reason": "synthetic credentials for redaction tests"},
    {"fingerprint": "3f1c…", "reason": "accepted risk, see SEC-12"}
  ],
  "ignore_paths": ["vendor/", "*.min.js"],
  "limits": {"max_files": 50000, "max_file_bytes": 2097152}
}
```

| Key | Rules |
|---|---|
| `gate.fail_on` | Findings at or above this severity block the gate... |
| `gate.min_confidence` | ...if their confidence is at least this. |
| `analyzers.disable` | Analyzer ids to skip (reported as `disabled`). |
| `severity_overrides` | Rule id → severity, applied before the gate. |
| `suppress[]` | Needs a `reason` and at least one of `rule_id` (glob), `path` (glob), `fingerprint`. A bare `"*"` is rejected. Suppressed findings stay visible in `suppressed[]`. |
| `ignore_paths` | gitignore-style patterns (`dir/`, anchored `a/b`, base-name globs). Negation `!` is rejected. |
| `limits` | Exceeding `max_files` is an execution failure, not a silent truncation. Larger files are listed as skipped. |

Validation is strict. Unknown keys, unknown severities, unreasoned suppressions or a wrong `version` make the
policy invalid, and an audit with an invalid policy exits `3`.

`dat policy verify POLICY` reports every problem (exit `1` if invalid, `0` if valid). With `--report R`
it also applies the policy's suppressions, overrides and gate to a saved report. That lets you check a CI
report against a stricter release policy without re-running the audit.

A repository's own `.dat/policy.json` can suppress findings in that repository. Every suppression is
listed in the report together with the policy's SHA-256. When auditing code you don't control, pass
`--no-target-policy` or your own `--policy`.
