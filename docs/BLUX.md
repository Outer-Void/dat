---
layout: page
title: BLUX
---

# DAT as a BLUX capability

DAT is `role: standalone` (BLUX ADR-0002). It works without BLUX, and deleting `.blux/` breaks nothing.
`.blux/` declares seven read-only capabilities:

| Capability | Input | Output |
|---|---|---|
| `repo.audit`, `repo.security`, `repo.quality`, `repo.release`, `package.audit`, `artifact.audit` | `target` (required), `policy`, `use_target_policy`, `fail_on`, `min_confidence` | `dat.report` v1 |
| `policy.verify` | `policy` (required), `report` | `dat.policy-verification` v1 |

Each is `provider.kind: subprocess` with entrypoint `dat blux-capability <id>` (BLUX ADR-0009). The adapter
(`src/dat/blux_adapter.py`) imports nothing from BLUX.

```sh
blux run repo.release --project path/to/dat --input '{"target": "."}' --system-store ./truth
blux-system graph --store ./truth
```

## Findings vs failures

| DAT result | Adapter | BLUX result | BLUX System status |
|---|---|---|---|
| Audit completed, gate pass | exit 0, report | `succeeded` | `SUCCESS` |
| Audit completed, **gate fail** | exit 0, report with `gate.verdict: fail` | `succeeded` | `SUCCESS` |
| Audit incomplete (analyzer failed) or error (missing target, bad policy) | exit 1, `{"error": …}` | `failed`, `EXECUTION_FAILED`, `details.provider_code` = DAT code | `FAILURE` |

A failed gate is a successful audit with a negative verdict. Callers read `output.gate.verdict`.
A BLUX `FAILURE` always means DAT could not audit, so the two can never be confused.

## Linkage

When invoked through BLUX, `report.linkage` carries the BLUX `operation_id`, `parent_operation_id`,
`session_id`, `project_id` and `capability_id`. The receipt BLUX System stores has
`output_sha256 = sha256(canonical JSON of the report)`. From the receipt you can therefore verify that a
given report is the one that operation produced, and from the report you can find its operation in BLUX System.

DAT does not accept plugins through BLUX, and the BLUX subprocess environment is minimal (PATH, HOME, locale,
TMPDIR). The caller's secrets are not passed to DAT.
