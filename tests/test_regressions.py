"""Regression fixtures for defects found in the Stage 03 audit of DAT <= 3.0 and while auditing the org.

Each case names the defect it guards: FP = false positive the old code produced, FN = false negative.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import AWS_KEY, STRONG_VALUE, audit, fake, rules, write

CASES: list[tuple[str, dict[str, str], set[str], set[str]]] = [
    # (id, files, rules that must appear, rules that must not appear)
    (
        "FP-3.0-env-reference-is-not-a-secret",
        {"app.py": 'import os\nAPI_KEY = os.environ["API_KEY"]\nSECRET_KEY = os.getenv("SECRET_KEY")\n'},
        set(),
        {"secrets.hardcoded-credential"},
    ),
    (
        "FP-3.0-password-parameter-is-not-a-secret",
        {"auth.py": "def login(user, password=None):\n    return password\n"},
        set(),
        {"secrets.hardcoded-credential"},
    ),
    (
        "FP-3.0-bypass-is-not-pass",
        {"cfg.py": 'bypass="enabled-for-all-users"\ncompass = "north-north-east"\n'},
        set(),
        {"secrets.hardcoded-credential"},
    ),
    (
        "FP-3.0-print-is-not-a-security-finding",
        {"cli.py": 'print("hello")\nconsole = None\n'},
        set(),
        {"python.debugger", "python.eval"},
    ),
    (
        "FP-3.0-todo-in-identifier-is-not-a-marker",
        {"t.py": "TODO_LIST = []\nmy_todo_items = 1\n"},
        set(),
        {"quality.work-marker"},
    ),
    (
        "FP-3.0-markdown-rule-is-not-a-conflict",
        {"README.md": "Title\n=======\n\ntext\n"},
        set(),
        {"quality.merge-conflict"},
    ),
    (
        "FP-lone-conflict-start-marker-is-not-a-conflict",
        {"git-notes.txt": "Conflicts look like this:\n<<<<<<< HEAD\n(and continue below)\n"},
        set(),
        {"quality.merge-conflict"},
    ),
    (
        "FN-3.0-aws-key-without-AWS-prefix",
        {"settings.py": f'KEY_ID = "{AWS_KEY}"\n'},
        {"secrets.aws-access-key-id"},
        set(),
    ),
    (
        "FN-3.0-secret-in-markdown-was-skipped",
        {"NOTES.md": f"deploy with {AWS_KEY}\n"},
        {"secrets.aws-access-key-id"},
        set(),
    ),
    (
        "FN-3.0-conflict-in-markdown-was-skipped",
        {"doc.md": "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> branch\n"},
        {"quality.merge-conflict"},
        set(),
    ),
    (
        "FP-aws-documentation-example-key",
        {"doc.py": f'k = "{fake("AKIA", "IOSFODNN7EXAMPLE")}"\n'},
        set(),
        {"secrets.aws-access-key-id"},
    ),
    (
        "FP-shell-variable-reference",
        {"run.sh": 'OPENAI_API_KEY="$api_key_from_vault"\n'},
        set(),
        {"secrets.hardcoded-credential"},
    ),
    (
        "FP-json-key-repeating-its-name",
        {"map.json": '{"com.onepassword.android": "onepassword"}\n'},
        set(),
        {"secrets.hardcoded-credential"},
    ),
    (
        "FP-escaped-newline-glues-name",
        {"t.py": f"x = \"line\\nSECRET_KEY = '{STRONG_VALUE}'\"\n"},
        {"secrets.hardcoded-credential"},
        set(),
    ),
    (
        "FP-yaml-safe-loader-subclass",
        {
            "s.py": "import yaml\nclass Strict(yaml.SafeLoader):\n    pass\nclass Stricter(Strict):\n    pass\n"
            "yaml.load(t, Loader=Stricter)\n"
        },
        set(),
        {"python.yaml-unsafe-load"},
    ),
    (
        "FN-from-import-subprocess-shell",
        {"s.py": "from subprocess import run\nrun(cmd, shell=True)\n"},
        {"python.shell-injection"},
        set(),
    ),
    (
        "FN-3.0-version-fallback-disagrees-with-pyproject",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "3.0.0"\n',
            "src/x/__init__.py": 'try:\n    __version__ = v("x")\nexcept E:\n    __version__ = "2.9.9"\n',
        },
        {"release.version-inconsistent"},
        set(),
    ),
    (
        "FN-3.0-duplicate-release-workflows",
        {
            ".github/workflows/sign-and-release.yml": "on: push\n",
            ".github/workflows/sign-and-release.yaml": "on: push\n",
        },
        {"release.duplicate-workflow"},
        set(),
    ),
]


@pytest.mark.parametrize(("case_id", "files", "must", "must_not"), CASES, ids=[c[0] for c in CASES])
def test_regression(tmp_path: Path, case_id: str, files: dict[str, str], must: set[str], must_not: set[str]) -> None:
    report = audit(write(tmp_path, files))
    found = set(rules(report))
    assert report["outcome"] == "completed"
    assert must <= found, f"{case_id}: missing {must - found}"
    assert not (must_not & found), f"{case_id}: unexpected {must_not & found}"


def test_example_like_values_are_reported_below_gate_confidence(tmp_path: Path) -> None:
    write(tmp_path, {"t.py": 'SECRET_KEY = "very-secret-key-123"\n', "README.md": f'api_key = "{STRONG_VALUE}"\n'})
    found = {f["location"]["path"]: f for f in audit(tmp_path)["findings"] if f["rule_id"].startswith("secrets.")}
    assert found["t.py"]["confidence"] == "low"  # demo-looking value
    assert found["README.md"]["confidence"] == "low"  # documentation
    write(tmp_path, {"t.py": f'SECRET_KEY = "{STRONG_VALUE}"\n'})
    found = {f["location"]["path"]: f for f in audit(tmp_path)["findings"] if f["rule_id"].startswith("secrets.")}
    assert found["t.py"]["confidence"] == "medium"


def test_3_0_circular_import_is_gone() -> None:
    """DAT <= 3.0: importing dat.scanner without typer installed raised ImportError (scanner -> integration ->
    report -> scanner). Every module must now import on its own, in a fresh interpreter."""
    import subprocess
    import sys

    import dat

    pkg = Path(dat.__file__).parent
    modules = sorted(
        "dat." + str(p.relative_to(pkg).with_suffix("")).replace(os.sep, ".").removesuffix(".__init__")
        for p in pkg.rglob("*.py")
        if p.name != "__main__.py"
    )
    for mod in modules:
        proc = subprocess.run([sys.executable, "-c", f"import {mod}"], capture_output=True, text=True)
        assert proc.returncode == 0, f"{mod}: {proc.stderr}"
