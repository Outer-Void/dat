# DAT — Developer’s Audit Tool

<p align="center">
  <img src="docs/assets/dat-logo-space.png" alt="DAT logo" width="480">
</p>

**DAT** is a fast, local-first audit engine for codebases: secrets & policy checks, readable reports, and CI-ready outputs—no telemetry, no vendor lock-in.

**Author:** `Outer Void Team, Justadudeinspace`  
**Email:** `outervoid.blux@gmail.com`

---

## Why DAT

- **Readable by design** — Markdown and JSON/JSONL outputs that humans and CI both love.  
- **Local & reproducible** — runs entirely on your machine; deterministic reports.  
- **CI/CD and Docker friendly** — first-class snippets below.

---

## Features

- Secrets & credential patterns, policy rules, merge-marker detection  
- **Formats:** `md`, `json`, `jsonl`, optional `pdf` export  
- **Full-context Markdown** (optionally includes code blocks with masked secrets)  
- **LRC bridge** (Local Repo Compiler) — write `.lrc-audit.json` next to your build metadata for downstream packaging and provenance.

---

## Install

Requires Python 3.9+.

### Recommended: uv (fast, isolated)

```bash
uv tool install outervoid-dat
```

### pipx (isolated CLI install)

```bash
pipx install outervoid-dat
```

### curl/wget bootstrap (pipx → uv → venv/pip --user)

```bash
curl -fsSL https://raw.githubusercontent.com/Outer-Void/dat/v3.0.0/scripts/install.sh | bash
```

```bash
wget -qO- https://raw.githubusercontent.com/Outer-Void/dat/v3.0.0/scripts/install.sh | bash
```

> Need PDF output? Install with `outervoid-dat[pdf]` (e.g., `uv tool install "outervoid-dat[pdf]"`).

### From source (recommended while 3.x is in flux)

```bash
git clone https://github.com/Outer-Void/dat.git
cd dat
chmod +x scripts/install.sh
./scripts/install.sh --mode dev
./dat                  # default Markdown report in artifacts/report.md
```

```bash
# or run bootstrap direct
dat                    # default report.md generates audit report with main files code base print output (Entire project codebase located within a single document)
```

> Termux: the installer uses `pkg install python3` and avoids mixing prefixes.  
> proot-distro: use the proot shell and avoid Termux paths in `$PATH`.

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
RUN chmod +x scripts/install.sh
ENTRYPOINT ["./dat"]
```

Build/run:

```bash
docker build -t dat .
docker run -v "$PWD":/scan dat /scan --deep --json report.json
```

---

## Usage

Basic:

```bash
./dat                       # scan current repo → report.md
./dat --json report.json    # machine-readable
./dat --jsonl report.jsonl  # streaming-friendly lines
```

Signed/verbose runs and combined outputs:

```bash
./dat --deep --pdf audit.pdf --json scan.json --sign --verbose
```

Generate custom output location and doc type:

```bash
# From working Dir
dat -o /path/to/custom_label.md
```

```bash
# From any Dir
dat /path/to/project/ -o /path/to/custom_label.pdf
```

```bash
# From working Dir
dat -o /path/to/custom_label.json
```

### Options

- `--report <path>`/`--json <path>`/`--jsonl <path>` for output selection  
- Markdown can include **full code context with masking** when configured (default on).

---

## CI/CD Example

```yaml
- name: Install DAT
  run: |
    git clone https://github.com/Outer-Void/dat.git
    cd dat
    ./scripts/install.sh

- name: Security Scan
  run: |
    cd dat
    ./dat --safe --json security-report.json
```

---

## LRC Integration

**LRC is the Local Repo Compiler** — DAT can emit an audit next to your LRC build metadata for downstream tooling.

```bash
# Example: produce .lrc-audit.json with scan + findings + summary
./dat --from-lrc
```

Under the hood DAT loads `.lrc-build.json` and merges it with integration config, then writes `.lrc-audit.json` (metadata, scan, findings, summary, build context).

Repo link: **LRC — Local Repo Compiler** → [Outer-Void/lrc](https://github.com/Outer-Void/lrc)

> There was an older README line implying “License & Regulatory Compliance.” That was incorrect; this section corrects it.

---

## Output Formats

- **Markdown (`report.md`)** — human-readable, can include per-file code sections with masked secrets.  
- **JSON/JSONL** — structured for pipelines; validated in tests via `--report`.  
- **PDF** — printable report (requires `reportlab`).

---

## Troubleshooting

- **Permissions:** `chmod +x scripts/install.sh bootstrap.sh`  
- **Missing deps:** `pip install --force-reinstall -r requirements.txt` (Linux may need `libmagic`).  
- **Termux:** `termux-setup-storage` and clone into `~/storage/shared` if needed.

---

## Build & Publish

```bash
python -m build
twine check dist/*
```

TestPyPI:

```bash
twine upload --repository-url https://test.pypi.org/legacy/ dist/*
```

PyPI:

```bash
twine upload dist/*
```

---

## Security & Telemetry

- No outbound connections; deterministic local outputs.  
- Optional signing (`--sign`) and append-only audit logs are supported.

---

## Roadmap

- TUI explorer, richer rule packs, baseline diffs, repair suggestions.

(See [`docs/ROADMAP.md`](./docs/ROADMAP.md) for the living plan.)

---

## License

MIT — see [`LICENSE`](./LICENSE).
