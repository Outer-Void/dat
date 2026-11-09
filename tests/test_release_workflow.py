"""OV-63 (F140): the tag workflow must never publish an unsigned or self-certified release.

Before OV-63, `.github/workflows/release.yml` built DAT, gated it only with DAT's own audit (no tests), signed
only `if: env.GPG_PRIVATE_KEY != ''` and then published a GitHub release. DAT has no such secret, so a version tag
would have published unsigned artifacts as a release. Owner decision 2026-11-09: releases are signed offline with
the shared release tool; the workflow only verifies.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"


def test_release_workflow_cannot_publish() -> None:
    text = WORKFLOW.read_text()
    assert "action-gh-release" not in text
    assert "gh release" not in text
    assert "contents: write" not in text
    assert "contents: read" in text


def test_release_workflow_never_skips_signing_silently() -> None:
    text = WORKFLOW.read_text()
    assert "secrets.GPG" not in text
    assert "if: env." not in text


def test_release_workflow_runs_the_tests_and_checks_the_signed_record() -> None:
    text = WORKFLOW.read_text()
    assert "pytest" in text
    assert "release_pipeline.py verify" in text and "--rebuild" in text
