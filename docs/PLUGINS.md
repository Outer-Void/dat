---
layout: page
title: Plugins
---

# Analyzer plugins

Built-in analyzers and plugins implement the same class, `dat.analyzers.Analyzer`:

```python
from collections.abc import Iterator
from dat.analyzers import Analyzer, Rule, RunOptions, Skip
from dat.inventory import Inventory
from dat.model import Category, Confidence, Finding, Severity


class LicenseHeader(Analyzer):
    id = "license-header"  # unique; may not shadow a built-in
    version = "1.0.0"
    target_kinds = frozenset({"repository"})  # or {"artifact"}
    rules = (Rule("acme.missing-header", Category.QUALITY, Severity.LOW, Confidence.HIGH, "Missing licence header"),)

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        for f in inv.files:
            if f.suffix == ".py" and not (inv.text(f) or "").startswith("# SPDX-"):
                yield self.finding("acme.missing-header", "No SPDX header.", path=f.rel, line=1)
```

Load it per run: `dat audit . --plugin mypkg.checks:LicenseHeader`. You can also register an entry point in
group `dat.analyzers` and pass its name (`--plugin license-header`). DAT never loads plugins implicitly. They
cannot be requested through BLUX, so remote input never chooses code to run.

## Contract

- **Deterministic.** Same inventory, same findings. No network, no models, no wall-clock decisions.
- **Read-only.** Use `inv.text(entry)` / `inv.read_bytes(entry)`; paths come from the inventory, never from content.
- **Redact.** Put at most a redacted excerpt in `evidence` (`dat.model.redact`).
- **Fail honestly.** Raise `Skip("reason")` when the analyzer does not apply, which is reported as `skipped`. Any other
  exception fails *that analyzer only*. Its partial findings are discarded, the report becomes
  `incomplete` and `dat` exits `3`. Yielding anything other than a `Finding` is an
  `ANALYZER_CONTRACT_VIOLATION`.
- Rule ids match `^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9-]*)+$`. Use your own namespace.
