"""Shared helpers. Credential-shaped test values are assembled at runtime so this repository's own
sources never contain a real-looking secret (DAT audits itself in CI)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from dat import engine
from dat.cli import main
from dat.policy import DEFAULT_POLICY, Policy

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "src" / "dat" / "schemas"


def fake(prefix: str, body: str) -> str:
    return prefix + body


AWS_KEY = fake("AKIA", "Q7ZK3M2N8P4R6T1V")
GITHUB_TOKEN = fake("ghp_", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8")
PRIVATE_KEY_HEADER = fake("-----BEGIN RSA ", "PRIVATE KEY-----")
STRIPE_KEY = fake("sk_live_", "4eC39HqLyjWDarjtT1zdp7dc")
STRONG_VALUE = fake("Zq8", "#vT2!mW9rL4pX7")


def write(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return root


def audit(root: Path, capability: str = "repo.audit", policy: Policy = DEFAULT_POLICY, **kw: Any) -> dict[str, Any]:
    return engine.run(capability, root, policy=policy, target_label="t", **kw)


def rules(report: dict[str, Any]) -> list[str]:
    return [f["rule_id"] for f in report["findings"]]


def by_rule(report: dict[str, Any], rule_id: str) -> list[dict[str, Any]]:
    return [f for f in report["findings"] if f["rule_id"] == rule_id]


def cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def cli_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code, out, _ = cli([*argv, "--format", "json"], capsys)
    assert out.count("\n") == 1, "stdout must hold exactly one JSON document"
    return code, json.loads(out)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def git_repo(root: Path, files: dict[str, str | bytes]) -> Path:
    write(root, files)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def dat_exe() -> str:
    local = Path(sys.executable).with_name("dat")
    found = str(local) if local.exists() else shutil.which("dat")
    if found is None:
        pytest.skip("dat console script not installed")
    return found


@pytest.fixture
def report_schema() -> Any:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SCHEMAS / "report.v1.schema.json").read_text())
    return jsonschema.Draft202012Validator(schema)
