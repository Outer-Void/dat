"""Engine guarantees: failure isolation, plugin contract, suppression, determinism, policy validation."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dat import engine
from dat.analyzers import Analyzer, Rule, RunOptions, load_plugin
from dat.errors import DatError, ErrorCode
from dat.inventory import Inventory
from dat.model import Category, Confidence, Finding, Severity
from dat.policy import DEFAULT_POLICY, from_document, validate
from dat.render import to_json
from tests.conftest import AWS_KEY, audit, by_rule, rules, write


class Exploding(Analyzer):
    id = "exploding"
    version = "0.0.1"
    rules = (Rule("plugin.boom", Category.QUALITY, Severity.LOW, Confidence.HIGH, "boom"),)

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        yield self.finding("plugin.boom", "partial result that must be discarded")
        raise RuntimeError("secret detail sk-should-not-leak")


class NotAFinding(Analyzer):
    id = "liar"
    version = "0.0.1"

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        yield {"rule_id": "x.y"}  # type: ignore[misc]


class Good(Analyzer):
    id = "good"
    version = "1.0.0"
    rules = (Rule("plugin.files", Category.QUALITY, Severity.INFO, Confidence.HIGH, "File count"),)

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        yield self.finding("plugin.files", f"{len(inv.files)} files")


def test_failing_analyzer_is_isolated_and_reported(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": f'k = "{AWS_KEY}"\n'})
    report = audit(tmp_path, plugins=[("t:Exploding", Exploding), ("t:Good", Good)])
    assert report["outcome"] == "incomplete"
    assert report["gate"]["verdict"] == "not_evaluated"
    status = {a["id"]: a["status"] for a in report["analyzers"]}
    assert status["exploding"] == "failed" and status["good"] == "ok" and status["secrets"] == "ok"
    assert "plugin.boom" not in rules(report)  # partial output of a failed analyzer is dropped
    assert {"secrets.aws-access-key-id", "plugin.files"} <= set(rules(report))  # the others still ran
    assert report["errors"] == [
        {
            "code": "ANALYZER_FAILED",
            "message": "Analyzer exploding failed.",
            "details": {"exception_type": "RuntimeError"},
            "analyzer": "exploding",
        }
    ]
    assert "should-not-leak" not in json.dumps(report)


def test_contract_violation_is_a_failure(tmp_path: Path) -> None:
    report = audit(write(tmp_path, {"a": "x"}), plugins=[("t:NotAFinding", NotAFinding)])
    assert report["outcome"] == "incomplete"
    assert report["errors"][0]["code"] == "ANALYZER_CONTRACT_VIOLATION"


def test_plugin_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "myplug.py").write_text(
        "from dat.analyzers import Analyzer\n"
        "class P(Analyzer):\n    id = 'p'\n    version = '1'\n    def run(self, inv, o):\n        return iter(())\n"
        "class Shadow(P):\n    id = 'secrets'\n"
        "NotAClass = 3\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert load_plugin("myplug:P").id == "p"
    for ref, why in (
        ("myplug:Shadow", "shadows"),
        ("myplug:NotAClass", "not a dat"),
        ("nosuch:X", "imported"),
        ("no-entry-point", "entry point"),
    ):
        with pytest.raises(DatError) as exc:
            load_plugin(ref)
        assert exc.value.code is ErrorCode.PLUGIN_LOAD_FAILED and why in exc.value.message
    sys.modules.pop("myplug", None)


def test_profiles_filter_categories(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": f'k = "{AWS_KEY}"  # TODO\ntry:\n    x()\nexcept:\n    pass\n'})
    sec, qual = audit(tmp_path, "repo.security"), audit(tmp_path, "repo.quality")
    assert {f["category"] for f in sec["findings"]} == {"security"}
    assert {f["category"] for f in qual["findings"]} == {"quality"}
    assert "secrets.aws-access-key-id" in rules(sec) and "python.bare-except" in rules(qual)


def test_inline_and_policy_suppression(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "a.py": f'k = "{AWS_KEY}"  # dat:ignore[secrets.aws-access-key-id]\n'
            f'j = "{AWS_KEY}"  # dat:ignore[other.rule]\n',
            "tests/fixture.py": f'k = "{AWS_KEY}"\n',
        },
    )
    policy = from_document(
        {
            "contract": "dat.policy",
            "version": 1,
            "policy_id": "p",
            "suppress": [{"rule_id": "secrets.*", "path": "tests/*", "reason": "test fixtures"}],
        }
    )
    report = audit(tmp_path, "repo.security", policy=policy)
    assert [(f["location"]["path"], f["location"]["line"]) for f in report["findings"]] == [("a.py", 2)]
    assert sorted((s["source"], s["location"]["path"]) for s in report["suppressed"]) == [
        ("inline", "a.py"),
        ("policy", "tests/fixture.py"),
    ]
    assert report["summary"]["suppressed"] == 2


def test_severity_override_and_gate_threshold(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": "import os\nos.system(c)\n"})
    assert audit(tmp_path, "repo.security")["gate"]["verdict"] == "pass"  # medium < high
    policy = from_document({"contract": "dat.policy", "version": 1, "gate": {"fail_on": "medium"}})
    assert audit(tmp_path, "repo.security", policy=policy)["gate"] == {
        "enforced": False,
        "fail_on": "medium",
        "min_confidence": "medium",
        "blocking": 1,
        "verdict": "fail",
    }
    policy = from_document(
        {"contract": "dat.policy", "version": 1, "severity_overrides": {"python.os-system": "critical"}}
    )
    assert by_rule(audit(tmp_path, "repo.security", policy=policy), "python.os-system")[0]["severity"] == "critical"


def test_disabled_analyzer(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": f'k = "{AWS_KEY}"\n'})
    policy = from_document({"contract": "dat.policy", "version": 1, "analyzers": {"disable": ["secrets"]}})
    report = audit(tmp_path, "repo.security", policy=policy)
    assert report["findings"] == []
    assert {a["id"]: a["status"] for a in report["analyzers"]}["secrets"] == "disabled"


def test_reports_are_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, {"b.py": f'k = "{AWS_KEY}"\n', "a.py": "eval(x)\neval(x)\n", "Dockerfile": "FROM x\n"})
    first = to_json(audit(tmp_path))
    monkeypatch.chdir(tmp_path)
    second = to_json(engine.run("repo.audit", Path("."), policy=DEFAULT_POLICY, target_label="t"))
    assert first == second
    fps = [f["fingerprint"] for f in json.loads(first)["findings"]]
    assert len(fps) == len(set(fps)), "repeated identical matches still get distinct fingerprints"


def test_fingerprint_survives_line_moves(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": f'k = "{AWS_KEY}"\n'})
    before = by_rule(audit(tmp_path), "secrets.aws-access-key-id")[0]
    write(tmp_path, {"a.py": f'\n\n# moved\nk = "{AWS_KEY}"\n'})
    after = by_rule(audit(tmp_path), "secrets.aws-access-key-id")[0]
    assert before["fingerprint"] == after["fingerprint"] and after["location"]["line"] == 4


def test_inventory_limits_and_symlinks(tmp_path: Path) -> None:
    write(tmp_path, {"a": "1", "b": "2", "c": "3", ".datignore": "ignored/\n*.log\n", "ignored/x": "", "y.log": ""})
    os.symlink("/etc/passwd", tmp_path / "link")
    report = audit(tmp_path)
    assert report["target"]["files"] == 4  # a b c .datignore
    assert {"path": "link", "reason": "symlink"} in report["target"]["skipped"]
    small = from_document({"contract": "dat.policy", "version": 1, "limits": {"max_files": 2}})
    report = audit(tmp_path, policy=small)
    assert report["outcome"] == "error" and report["errors"][0]["code"] == "LIMIT_EXCEEDED"


def test_missing_target_is_an_error_not_a_clean_report(tmp_path: Path) -> None:
    report = audit(tmp_path / "nope")
    assert report["outcome"] == "error"
    assert report["findings"] == [] and report["gate"]["verdict"] == "not_evaluated"
    assert report["errors"][0]["code"] == "TARGET_NOT_FOUND"


def test_report_schema(tmp_path: Path, report_schema: Any) -> None:
    write(tmp_path, {"a.py": f'k = "{AWS_KEY}"  # dat:ignore\neval(x)\n'})
    for report in (
        audit(tmp_path),
        audit(tmp_path / "missing"),
        audit(tmp_path, plugins=[("x:E", Exploding)]),
        audit(tmp_path, "artifact.audit"),
    ):
        errors = sorted(report_schema.iter_errors(report), key=str)
        assert not errors, errors[0]


# -- policy documents ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc", "problem"),
    [
        ([], "policy must be a JSON object"),
        ({"contract": "x", "version": 1}, "contract must be 'dat.policy'"),
        ({"contract": "dat.policy", "version": 2}, "unsupported policy version 2"),
        ({"contract": "dat.policy", "version": True}, "version must be an integer"),
        ({"contract": "dat.policy", "version": 1, "extra": 1}, "unknown key 'extra'"),
        ({"contract": "dat.policy", "version": 1, "gate": {"fail_on": "severe"}}, "gate.fail_on must be one of"),
        ({"contract": "dat.policy", "version": 1, "suppress": [{"rule_id": "a.b"}]}, "reason is required"),
        ({"contract": "dat.policy", "version": 1, "suppress": [{"reason": "because"}]}, "must narrow"),
        (
            {"contract": "dat.policy", "version": 1, "suppress": [{"rule_id": "*", "reason": "all of it"}]},
            "every finding",
        ),
        ({"contract": "dat.policy", "version": 1, "ignore_paths": ["!keep"]}, "negation"),
        ({"contract": "dat.policy", "version": 1, "limits": {"max_files": 0}}, "positive integer"),
        ({"contract": "dat.policy", "version": 1, "severity_overrides": {"Bad Rule": "low"}}, "invalid rule id"),
    ],
)
def test_policy_validation(doc: Any, problem: str) -> None:
    problems = validate(doc)
    assert any(problem in p for p in problems), problems


def test_policy_schema_agrees_with_validator() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    from tests.conftest import SCHEMAS

    schema = jsonschema.Draft202012Validator(json.loads((SCHEMAS / "policy.v1.schema.json").read_text()))
    good = {
        "contract": "dat.policy",
        "version": 1,
        "policy_id": "p",
        "gate": {"fail_on": "medium"},
        "suppress": [{"rule_id": "secrets.*", "path": "tests/*", "reason": "fixtures"}],
        "ignore_paths": ["vendor/"],
        "limits": {"max_files": 10},
        "analyzers": {"disable": ["git"]},
        "severity_overrides": {"quality.work-marker": "low"},
    }
    assert validate(good) == [] and schema.is_valid(good)
    assert validate(DEFAULT_POLICY.source or {"contract": "dat.policy", "version": 1}) == []
