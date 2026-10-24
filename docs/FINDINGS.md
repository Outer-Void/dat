---
layout: page
title: Findings
---

# Findings and reports (`dat.report` v1)

A **finding** is something DAT observed about the target. An **error** is DAT failing to observe. They never
mix: findings live in `findings[]`, errors in `errors[]`, and the exit code tells them apart
([USAGE.md](USAGE.md#exit-codes)).

Schemas: [`src/dat/schemas/report.v1.schema.json`](../src/dat/schemas/report.v1.schema.json) (report and finding),
[`policy.v1.schema.json`](../src/dat/schemas/policy.v1.schema.json). Both ship in the wheel.

## Finding

| Field | Meaning |
|---|---|
| `rule_id` | Stable dotted id (`secrets.aws-access-key-id`). Never renamed; retired ids are not reused. |
| `analyzer` | Analyzer that produced it (`secrets`, or a plugin id). |
| `category` | `security`, `quality`, `release`, `package` or `artifact`. |
| `severity` | `info` < `low` < `medium` < `high` < `critical`: how bad it is if real. |
| `confidence` | `low` < `medium` < `high`: how sure the rule is that the match is real. |
| `title`, `message` | Rule title; message specific to this occurrence. |
| `location` | `path` (POSIX, relative to the target), `line`, `column`, `member` (entry inside an archive). |
| `evidence` | Short, **redacted** excerpt (`AKIA…[REDACTED len=20]`). Secrets never appear in reports. |
| `remediation` | What to do, when the rule knows. |
| `fingerprint` | SHA-256 of rule, path, member and evidence. Stable when lines move; repeated identical matches get distinct fingerprints. Use it to suppress one occurrence. |

## Report

| Field | Meaning |
|---|---|
| `outcome` | `completed` (authoritative), `incomplete` (an analyzer failed; its findings were dropped), `error` (nothing audited). |
| `gate` | `verdict` `pass`/`fail` when completed, `not_evaluated` otherwise; `blocking` count; thresholds; `enforced` (whether the exit code used it). |
| `target` | `kind`, `path` as given, `files`, `inventory` (`git` = what git would commit, `walk` = directory walk), `skipped[]`, and for artifacts `artifacts[]` with SHA-256s. |
| `summary` | Counts by severity and category; suppressed count. |
| `suppressed[]` | Each suppressed finding with `source` (`inline`/`policy`) and the reason. Nothing is silently dropped. |
| `analyzers[]` | Every analyzer with `status` `ok`, `skipped` (with reason, e.g. not a git repository), `disabled` or `failed`. |
| `errors[]` | `code`, `message`, `details`; never exception text. |
| `policy` | Policy id and SHA-256 of the policy document used. |
| `linkage` | BLUX operation ids when run through BLUX ([BLUX.md](BLUX.md)), otherwise `null`. |

Reports are deterministic: no timestamps, durations, host names or absolute paths unless you passed one.
The same input produces byte-identical JSON.

## Determinism and authority

Every rule is a fixed regular expression, AST check, manifest parse or archive inspection. DAT never calls a
network service or a model, and never asks an AI whether something is a problem. Confidence comes from how
specific the rule is (a full AWS key shape is `high`, a generic `password = "..."` assignment is `medium`, a
demo-looking value or one in documentation is `low`), not from judgment at run time.

## Rule catalog

| Rule | Analyzer | Category | Severity | Confidence | Title |
|---|---|---|---|---|---|
| `secrets.private-key` | secrets | security | critical | high | Private key |
| `secrets.aws-access-key-id` | secrets | security | high | high | AWS access key ID |
| `secrets.github-token` | secrets | security | critical | high | GitHub token |
| `secrets.anthropic-key` | secrets | security | critical | high | Anthropic API key |
| `secrets.openai-key` | secrets | security | critical | medium | OpenAI API key |
| `secrets.slack-token` | secrets | security | high | high | Slack token |
| `secrets.slack-webhook` | secrets | security | high | high | Slack incoming webhook |
| `secrets.stripe-key` | secrets | security | critical | high | Stripe live key |
| `secrets.google-api-key` | secrets | security | high | high | Google API key |
| `secrets.telegram-bot-token` | secrets | security | high | high | Telegram bot token |
| `secrets.jwt` | secrets | security | medium | medium | JSON Web Token |
| `secrets.url-credentials` | secrets | security | high | medium | Credentials in URL |
| `secrets.hardcoded-credential` | secrets | security | high | medium | Hard-coded credential |
| `quality.merge-conflict` | quality | quality | high | high | Unresolved merge conflict |
| `quality.work-marker` | quality | quality | info | medium | Work marker comment |
| `quality.large-file` | quality | quality | low | high | Large file in repository |
| `quality.mixed-line-endings` | quality | quality | low | high | Mixed line endings |
| `python.syntax-error` | python | quality | high | high | Python file does not parse |
| `python.shell-injection` | python | security | high | high | Subprocess with shell=True |
| `python.os-system` | python | security | medium | high | os.system / os.popen |
| `python.eval` | python | security | medium | medium | eval/exec of dynamic code |
| `python.unsafe-deserialization` | python | security | medium | medium | Unsafe deserialization (pickle/marshal) |
| `python.yaml-unsafe-load` | python | security | high | high | yaml.load without a safe loader |
| `python.tls-verify-disabled` | python | security | high | medium | TLS verification disabled |
| `python.insecure-tempfile` | python | security | medium | high | tempfile.mktemp is race-prone |
| `python.debugger` | python | quality | medium | high | Debugger breakpoint left in code |
| `python.bare-except` | python | quality | low | high | Bare except |
| `js.manifest-invalid` | javascript | package | high | high | package.json is not valid JSON |
| `js.install-script` | javascript | security | medium | high | Install-time lifecycle script |
| `js.unpinned-dependency` | javascript | package | medium | high | Unpinned dependency |
| `js.missing-lockfile` | javascript | package | medium | high | Dependencies without a lockfile |
| `js.dynamic-code` | javascript | security | medium | low | eval / new Function |
| `android.manifest-unparseable` | android | quality | medium | high | AndroidManifest.xml not parsed |
| `android.debuggable` | android | security | high | high | android:debuggable=true |
| `android.allow-backup` | android | security | low | high | android:allowBackup=true |
| `android.cleartext-traffic` | android | security | medium | high | Cleartext HTTP allowed |
| `android.exported-component` | android | security | medium | medium | Exported component without permission |
| `android.gradle-signing-secret` | android | security | high | high | Signing password in Gradle file |
| `container.unpinned-base-image` | container | security | medium | high | Base image not pinned |
| `container.runs-as-root` | container | security | medium | medium | Container runs as root |
| `container.remote-add` | container | security | medium | high | ADD from a URL |
| `container.pipe-to-shell` | container | security | high | high | Download piped to a shell |
| `container.secret-in-env` | container | security | high | medium | Secret baked into image |
| `package.pyproject-invalid` | packaging | package | high | high | pyproject.toml does not parse |
| `package.missing-name` | packaging | package | high | high | Project has no name |
| `package.missing-version` | packaging | package | medium | high | Project version not declared |
| `package.missing-license` | packaging | package | medium | high | No license metadata |
| `package.missing-requires-python` | packaging | package | low | high | requires-python not declared |
| `package.unpinned-vcs-dependency` | packaging | security | high | high | VCS dependency not pinned to a commit |
| `package.unversioned-requirement` | packaging | package | info | high | Requirement without a version |
| `package.insecure-index` | packaging | security | high | high | Package index over plain HTTP |
| `package.trusted-host` | packaging | security | medium | high | pip --trusted-host disables TLS checks |
| `release.missing-license` | release | release | high | high | No LICENSE file |
| `release.missing-readme` | release | release | medium | high | No README |
| `release.missing-changelog` | release | release | low | high | No changelog |
| `release.version-undeclared` | release | release | medium | high | No version declared |
| `release.version-inconsistent` | release | release | high | high | Declared versions disagree |
| `release.changelog-missing-version` | release | release | low | medium | Changelog lacks the version |
| `release.duplicate-workflow` | release | release | medium | high | Duplicate workflow (.yml and .yaml) |
| `release.dirty-worktree` | git | release | high | high | Uncommitted changes |
| `release.untagged-version` | git | release | medium | high | HEAD is not tagged with the version |
| `release.tag-mismatch` | git | release | high | high | Version tag points at another commit |
| `artifact.corrupt-archive` | artifact | artifact | high | high | Archive cannot be read |
| `artifact.path-traversal` | artifact | security | critical | high | Archive member escapes the extraction directory |
| `artifact.unsafe-link` | artifact | security | high | high | Link member points outside the archive |
| `artifact.special-member` | artifact | security | high | high | Device, FIFO or setuid/setgid member |
| `artifact.resource-limit` | artifact | security | high | medium | Archive exceeds safe expansion limits |
| `artifact.embedded-secret` | artifact | security | critical | high | Credential inside artifact |
| `artifact.record-missing` | artifact | artifact | high | high | Wheel has no RECORD |
| `artifact.record-mismatch` | artifact | artifact | high | high | Wheel RECORD does not match contents |
| `artifact.checksum-mismatch` | artifact | artifact | critical | high | Checksum does not match |
| `artifact.checksum-target-missing` | artifact | artifact | medium | high | Checksum lists a missing file |

Inline suppression: append `dat:ignore` to the line (all rules) or `dat:ignore[rule.id,other.id]`.
Suppressions are reported, not hidden.
