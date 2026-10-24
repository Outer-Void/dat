#!/usr/bin/env bash
# Build the wheel, install it into a clean venv outside the source tree, and run it there.
set -euo pipefail
PYTHON="${PYTHON:-python3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

"$PYTHON" -m build --wheel --outdir "$WORK/dist" "$ROOT" >/dev/null
"$PYTHON" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q "$WORK"/dist/*.whl
cd "$WORK"
"$WORK/venv/bin/python" - <<'PY'
import importlib.resources as r, json
for name in ("report.v1.schema.json", "policy.v1.schema.json"):
    json.loads(r.files("dat").joinpath("schemas", name).read_text())
PY
mkdir -p "$WORK/t" && echo 'eval(x)' > "$WORK/t/a.py"
"$WORK/venv/bin/dat" security "$WORK/t" --format json >"$WORK/out.json"
"$WORK/venv/bin/python" -c "import json,sys; r=json.load(open(sys.argv[1])); assert r['outcome']=='completed', r" "$WORK/out.json"
set +e; "$WORK/venv/bin/dat" audit "$WORK/missing" --quiet; code=$?; set -e
[ "$code" -eq 3 ] || { echo "expected exit 3 for a missing target, got $code" >&2; exit 1; }
"$WORK/venv/bin/dat" analyzers --format json | "$WORK/venv/bin/python" -m json.tool >/dev/null
echo "verify_wheel: OK"
