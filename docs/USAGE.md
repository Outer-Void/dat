---
layout: page
title: Usage
---

# Using `dat`

```sh
pip install "outervoid-dat @ git+https://github.com/Outer-Void/dat.git@v4.0.0"   # or: pip install -e .
dat audit .                        # full repository audit, human report on stdout
dat release . --gate               # release gate: exit 1 if anything blocks
dat security . --format json       # one JSON document on stdout
dat artifact dist/ --sarif dat.sarif
dat policy verify .dat/policy.json --report dat-report.json
```

Python 3.11+. No runtime dependencies, no network, no BLUX required. `git` is used when present (inventory
and release checks); `gpg` only for `--sign`.

## Commands

| Command | Capability | Audits |
|---|---|---|
| `dat audit [TARGET]` | `repo.audit` | Everything below for a repository |
| `dat security [TARGET]` | `repo.security` | Secrets, risky Python/JS, Android manifest, containers, package security |
| `dat quality [TARGET]` | `repo.quality` | Merge conflicts, Python syntax/bare except/debuggers, work markers, large files, line endings |
| `dat release [TARGET]` | `repo.release` | LICENSE/README/CHANGELOG, version consistency, duplicate workflows, git cleanliness and tag, secrets, conflicts |
| `dat package [TARGET]` | `package.audit` | `pyproject.toml`, `requirements*.txt`, `package.json`: metadata, pinning, lockfiles, install scripts |
| `dat artifact TARGET` | `artifact.audit` | Wheels, zips, tars, APK/JAR: traversal, links, setuid, bombs, RECORD, `SHA256SUMS`, embedded secrets |
| `dat policy verify POLICY [--report R]` | `policy.verify` | Validate a policy; optionally re-gate a saved report |
| `dat analyzers` | | List analyzers, rules, capabilities, installed plugins |
| `dat blux-capability CAP` | | BLUX subprocess adapter ([BLUX.md](BLUX.md)) |
| `dat bundle PATH -o OUT.md` | | Concatenate source files into Markdown with secrets masked (`dat cmd` is an alias) |

`TARGET` defaults to `.`. In a git work tree DAT audits what git would commit (tracked plus untracked,
not gitignored). Otherwise it walks the directory. It never follows symlinks and always honours `.datignore`.

## Options (audit commands)

| Option | Effect |
|---|---|
| `--format text\|json\|markdown\|sarif` | stdout format. `json` is exactly one document. |
| `--json F`, `--md F`, `--sarif F`, `--text F`, `-o F` | Also write files (`-o` picks the format from the extension). Writes are atomic. |
| `--report PATH` | JSON report; a directory gets `PATH/dat-report.json`. |
| `--policy F` | Use this policy instead of `TARGET/.dat/policy.json`. |
| `--no-target-policy` | Ignore the target's own policy (useful when auditing third-party code). |
| `--gate` | Release-gate mode: exit 1 when blocking findings exist. |
| `--fail-on SEV`, `--min-confidence C` | Gate thresholds (default `high` / `medium`); `--fail-on` implies `--gate`. |
| `--plugin REF` | Load an analyzer plugin (entry point name or `module:Class`). Never implicit. |
| `--sign [--sign-key K]` | gpg detached-sign every written file. Fails (exit 3) if it cannot; never fakes a signature. |
| `--quiet` | Nothing on stdout. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Audit completed. Gate passed, or not enforced. Findings may exist. |
| `1` | Audit completed and the enforced gate failed (blocking findings). |
| `2` | Usage error (bad flags). |
| `3` | DAT could not complete the audit: missing/unreadable target, invalid policy, analyzer or plugin failure, limit exceeded, output or signing failure. |

Exit `3` always wins over `1`: an incomplete audit is never reported as a gate result. In JSON the same
distinction is `outcome` (`completed` / `incomplete` / `error`) and `gate.verdict`.

## Legacy invocations (pre-4.0)

`dat PATH [--json F]` runs `dat audit PATH`. `dat --from-lrc DIR` runs `dat audit DIR` (used by lrc).
`dat audit --report BUILD_DIR` (lrc's default command) writes `BUILD_DIR/dat-report.json`. `--no-sign`,
`--deep`, `--fast`, `--audit` and `--all` are accepted and ignored with a note on stderr.
