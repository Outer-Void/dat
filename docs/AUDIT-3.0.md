---
layout: page
title: Stage 03 audit of DAT 3.0
---

# Audit of DAT 3.0.0 (`5fb9de9`), Stage 03

What was found before the 4.0 rebuild. Each row names its fix. Where a test guards the fix, it is in
`tests/test_regressions.py` (ids `FP-3.0-*` / `FN-3.0-*`) or the test named.

## Architecture

| Defect | Evidence | 4.0 |
|---|---|---|
| Circular import `scanner → integration → audit → report → scanner` | `import dat.scanner.core` failed whenever `dat/__init__` could not import the CLI first, e.g. without typer: `ImportError: cannot import name 'ScanResult' from partially initialized module 'dat.scanner'` | Layered modules with no cycles; `test_3_0_circular_import_is_gone` imports every module in a fresh interpreter |
| Import-time side effects | `dat/__init__` imported the whole CLI and ran init hooks; `utils` initialised colorama on import (escape codes on stdout) | `dat/__init__` only defines `__version__` |
| Two rule engines | `rules/rules.py` (whole-file substring, one finding per file) and `rules/engine.py` (per line) with different rule sets | One analyzer interface, one rule catalog |
| `dat/pdf.py` shadowed by `dat/pdf/` | Dead module | Removed (PDF output dropped; see CHANGELOG) |
| Version always `3.0.0` | `metadata.version("dat")` but the distribution is `outervoid-dat`, so the fallback literal always won | Single `__version__`; the release analyzer catches disagreeing declarations |
| `datetime.UTC` with `requires-python >=3.9` | Import fails on 3.9/3.10 | `requires-python >=3.11` |

## Behaviour

| Defect | 4.0 |
|---|---|
| Findings, crashes and "no findings" were indistinguishable: violations exited 0, any error exited 1 | Exit 0 / 1 (gate) / 2 (usage) / 3 (failure); `outcome` and `errors[]` in JSON |
| `main(argv=None)` dispatched to Typer whenever `argv` was falsy, so the documented argparse path ran only in tests | One argparse CLI |
| `--sign` without gpg wrote a bare SHA-256 into `<file>.asc`, which looks like a signature but proves nothing | gpg or exit 3; `test_sign_never_fakes_a_signature` |
| Every scan appended the OS user name to an encrypted log in `~/.config/dat/` | Removed. DAT writes only the files you name. |
| Policy-free substring rules: `print(` was a *security* finding; `PASS=` matched `bypass=`; `API_KEY` matched `os.environ["API_KEY"]` | Literal-value, entropy and placeholder guards; `print` is not a finding |
| AWS rule `AWS[\\s\\S]*AKIA…` in a raw string matched a literal backslash, so it could never fire; keys without "AWS" on the line were missed too | Exact key shape |
| `.md` files skipped by the old engine, so secrets and conflicts in docs were missed | All text files |
| Lowercased patterns against lowercased lines: case-sensitive shapes broken | Case-correct regexes |
| CI ran `black`/`isort`, which were not installed; pdf-smoke depended on a removed flag combination | New CI: ruff, mypy `--strict`, pytest, wheel check, self-audit |
| `sign-and-release.yml` and `.yaml` both triggered on `v*` tags | One `release.yml`, gated by `dat release --gate`; DAT now flags this pattern (`release.duplicate-workflow`) |
| Tests: 3 failing (CLI drift), async tests without `asyncio_mode`, collection needed reportlab | New suite with no optional dependencies |

## Found while auditing the organisation with 4.0 (fixed before release)

| False positive | Fix |
|---|---|
| Build outputs (`app/build/intermediates/…`) audited as source | Inventory is what git would commit |
| `yaml.load(…, Loader=_StrictLoader)` where `_StrictLoader(yaml.SafeLoader)` | Same-module SafeLoader subclasses are safe |
| `"com.onepassword.android": "onepassword"` flagged as a credential | A value that repeats its own key is not a secret |
| `"\nSECRET_KEY = …"` read as the name `nSECRET_KEY` | Escape-aware name boundary |
| `OPENAI_API_KEY="$var"` | Shell references are placeholders |
| Test or demo values (`password123`, `user:pass@`) | Still reported, with `low` confidence (below the default gate) |
