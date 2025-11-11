#!/usr/bin/env bash
# Ultimate one-command fix for all DAT linting issues
# This script is safe, doesn't touch pyproject.toml, just fixes code

set -euo pipefail

echo "🔧 DAT Quick Lint Fix - Running..."
echo

# Fix all Python code issues in one go
python3 << 'PYFIX_EOF'
from pathlib import Path
import re

print("→ Fixing Python code issues...")

# Fix 1: src/dat/__init__.py - undefined 'e'
init_file = Path("src/dat/__init__.py")
if init_file.exists():
    content = init_file.read_text()
    content = content.replace(
        'print(f"Error: DAT CLI not available: {e}", file=sys.stderr)',
        'print("Error: DAT CLI not available", file=sys.stderr)'
    )
    init_file.write_text(content)
    print("  ✓ Fixed __init__.py")

# Fix 2: src/dat/scanner/sync.py - unused variable
sync_file = Path("src/dat/scanner/sync.py")
if sync_file.exists():
    content = sync_file.read_text()
    content = content.replace(
        'for dirpath, dirnames, filenames in os.walk',
        'for dirpath, _dirnames, filenames in os.walk'
    )
    sync_file.write_text(content)
    print("  ✓ Fixed sync.py")

# Fix 3: tests/test_cli.py - subprocess
test_cli = Path("tests/test_cli.py")
if test_cli.exists():
    content = test_cli.read_text()
    pattern = r'stdout=subprocess\.PIPE,\s*stderr=subprocess\.PIPE,'
    content = re.sub(pattern, 'capture_output=True,', content)
    test_cli.write_text(content)
    print("  ✓ Fixed test_cli.py")

# Fix 4: tests/test_core.py - exception
test_core = Path("tests/test_core.py")
if test_core.exists():
    content = test_core.read_text()
    content = content.replace(
        'with pytest.raises(Exception):',
        'with pytest.raises((ValueError, TypeError, RuntimeError)):'
    )
    test_core.write_text(content)
    print("  ✓ Fixed test_core.py")

print("✓ All Python fixes applied")
PYFIX_EOF

echo
echo "→ Running ruff auto-fixes..."

# Run ruff fixes (safe)
ruff check . --fix --quiet 2>&1 && echo "  ✓ Safe fixes applied" || echo "  ⚠ Some issues remain"

# Run ruff fixes (unsafe - for type annotations)
ruff check . --select UP --fix --unsafe-fixes --quiet 2>&1 && echo "  ✓ Type annotations modernized" || true

# Format
ruff format . --quiet 2>&1 && echo "  ✓ Code formatted" || true

echo
echo "→ Checking status..."
echo

# Final check
if ruff check . 2>&1 | grep -q "All checks passed"; then
    echo "✅ All checks passed!"
    exit 0
else
    remaining=$(ruff check . --statistics 2>&1 | grep "Found" | head -1 || echo "Some issues remain")
    echo "📊 Status: $remaining"
    echo
    echo "To see details: ruff check ."
    exit 0
fi
