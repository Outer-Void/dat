---
layout: default
title: DAT · Developer's Audit Tool
---

# DAT: Developer's Audit Tool
<p class="lead">Deterministic quality, security and release audits for repositories and build artifacts. Standalone, offline, with no AI judgment.</p>

<div class="card">
  <p><strong>Quickstart</strong></p>
  <pre><code>pip install "outervoid-dat @ git+https://github.com/Outer-Void/dat.git@v4.0.0"
dat audit .
dat release . --gate</code></pre>
  <p>
    <a class="btn" href="{{ site.repo_url }}">View on GitHub</a>
    <a class="btn ghost" href="{{ site.baseurl }}/USAGE">Usage</a>
  </p>
</div>

- [Usage and exit codes](USAGE.md)
- [Findings, report format and rule catalog](FINDINGS.md)
- [Policies and the release gate](POLICY.md)
- [Analyzer plugins](PLUGINS.md)
- [DAT as a BLUX capability](BLUX.md)
- [Audit of DAT 3.0](AUDIT-3.0.md)
