"""CLI exit codes and outputs, legacy invocations (lrc), signing, BLUX adapter protocol, independence."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from dat import blux_adapter, engine
from dat.errors import EXIT_FAILURE, EXIT_GATE_FAILED, EXIT_OK, EXIT_USAGE
from tests.conftest import AWS_KEY, ROOT, STRONG_VALUE, cli, cli_json, dat_exe, write

POLICY = {"contract": "dat.policy", "version": 1, "policy_id": "strict", "gate": {"fail_on": "low"}}


def test_exit_codes_distinguish_findings_from_failures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, {"a.py": f'k = "{AWS_KEY}"\n'})
    clean = write(tmp_path / "clean", {"x.txt": "hello\n"})
    assert cli_json(["security", str(tmp_path)], capsys)[0] == EXIT_OK  # findings, gate advisory
    code, report = cli_json(["security", str(tmp_path), "--gate"], capsys)
    assert code == EXIT_GATE_FAILED and report["gate"]["verdict"] == "fail"
    assert cli_json(["security", str(clean), "--gate"], capsys)[0] == EXIT_OK
    code, report = cli_json(["security", str(tmp_path / "missing"), "--gate"], capsys)
    assert code == EXIT_FAILURE and report["outcome"] == "error"
    assert cli(["security", "--bogus"], capsys)[0] == EXIT_USAGE
    assert cli([], capsys)[0] == EXIT_USAGE
    assert cli(["security", str(clean), "--fail-on", "info"], capsys)[0] == EXIT_OK


def test_plugin_failure_exits_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "boomplug.py").write_text(
        "from dat.analyzers import Analyzer\nclass B(Analyzer):\n    id = 'b'\n    version = '1'\n"
        "    def run(self, inv, o):\n        raise ValueError\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    code, report = cli_json(["audit", str(tmp_path), "--plugin", "boomplug:B"], capsys)
    assert code == EXIT_FAILURE and report["outcome"] == "incomplete"
    code, report = cli_json(["audit", str(tmp_path), "--plugin", "boomplug:Missing"], capsys)
    assert code == EXIT_FAILURE and report["errors"][0]["code"] == "PLUGIN_LOAD_FAILED"
    sys.modules.pop("boomplug", None)


def test_outputs_and_formats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, {"src/a.py": "eval(x)\n"})
    out = tmp_path / "out"
    code, text, _ = cli(
        [
            "audit",
            str(tmp_path / "src"),
            "--json",
            str(out / "r.json"),
            "--md",
            str(out / "r.md"),
            "--sarif",
            str(out / "r.sarif"),
            "-o",
            str(out / "r.txt"),
        ],
        capsys,
    )
    assert code == EXIT_OK and "python.eval" in text and "Outcome: COMPLETED" in text
    assert json.loads((out / "r.json").read_text())["contract"] == "dat.report"
    assert "| medium | medium | `python.eval`" in (out / "r.md").read_text()
    sarif = json.loads((out / "r.sarif").read_text())
    assert sarif["version"] == "2.1.0" and "python.eval" in {r["ruleId"] for r in sarif["runs"][0]["results"]}
    assert (out / "r.txt").read_text().startswith("DAT ")
    assert cli(["audit", str(tmp_path / "src"), "--quiet"], capsys)[1] == ""


def test_text_report_separates_errors_from_findings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, text, _ = cli(["audit", str(tmp_path / "missing")], capsys)
    assert code == EXIT_FAILURE
    assert "Outcome: ERROR" in text and "they are not findings" in text and "TARGET_NOT_FOUND" in text


def test_lrc_invocations_still_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """lrc runs `dat audit --report ${BUILD_DIR}` (cwd=BUILD_DIR) and `dat --from-lrc DIR [args]`."""
    build = write(tmp_path / "build", {"main.py": "x = 1\n"})
    monkeypatch.chdir(build)
    code, _, _ = cli(["audit", "--report", str(build)], capsys)
    assert code == EXIT_OK
    assert json.loads((build / "dat-report.json").read_text())["target"]["path"] == "."
    code, out, err = cli(["--from-lrc", str(build), "--no-sign", "--format", "json"], capsys)
    assert code == EXIT_OK and json.loads(out)["target"]["path"] == str(build)
    assert "ignored since 4.0" in err
    code, out, _ = cli([str(build), "--format", "json"], capsys)  # `dat PATH`
    assert code == EXIT_OK and json.loads(out)["capability"] == "repo.audit"


def test_sign_never_fakes_a_signature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    write(tmp_path, {"a.txt": "x\n"})
    code, _, err = cli(["audit", str(tmp_path), "--json", str(tmp_path / "r.json"), "--sign"], capsys)
    assert code == EXIT_FAILURE and "SIGNING_FAILED" in err
    assert not (tmp_path / "r.json.asc").exists()


def test_policy_verify_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "p.json").write_text(json.dumps(POLICY))
    (tmp_path / "bad.json").write_text('{"contract": "dat.policy", "version": 1, "gate": {"fail_on": "x"}}')
    (tmp_path / "broken.json").write_text("{nope")
    write(tmp_path / "repo", {"a.py": "try:\n    x()\nexcept:\n    pass\n"})
    cli(["quality", str(tmp_path / "repo"), "--json", str(tmp_path / "r.json"), "--quiet"], capsys)

    code, doc = cli_json(["policy", "verify", str(tmp_path / "p.json")], capsys)
    assert (code, doc["verdict"], doc["policy"]["valid"]) == (EXIT_OK, "pass", True)
    code, doc = cli_json(["policy", "verify", str(tmp_path / "p.json"), "--report", str(tmp_path / "r.json")], capsys)
    assert (code, doc["verdict"], doc["report"]["blocking"]) == (EXIT_GATE_FAILED, "fail", 1)
    code, doc = cli_json(["policy", "verify", str(tmp_path / "bad.json")], capsys)
    assert code == EXIT_GATE_FAILED and doc["policy"]["valid"] is False and doc["outcome"] == "completed"
    code, doc = cli_json(["policy", "verify", str(tmp_path / "broken.json")], capsys)
    assert code == EXIT_GATE_FAILED and doc["policy"]["valid"] is False
    code, doc = cli_json(["policy", "verify", str(tmp_path / "absent.json")], capsys)
    assert code == EXIT_FAILURE and doc["errors"][0]["code"] == "POLICY_NOT_FOUND"
    code, doc = cli_json(["policy", "verify", str(tmp_path / "p.json"), "--report", str(tmp_path / "p.json")], capsys)
    assert code == EXIT_FAILURE and doc["errors"][0]["code"] == "INVALID_REPORT"


def test_target_policy_is_used_unless_disabled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, {".dat/policy.json": json.dumps(POLICY), "a.py": "x = 1  # TODO\n"})
    assert cli(["quality", str(tmp_path), "--gate"], capsys)[0] == EXIT_OK  # info < low
    write(tmp_path, {"a.py": "try:\n    x()\nexcept:\n    pass\n"})
    assert cli(["quality", str(tmp_path), "--gate"], capsys)[0] == EXIT_GATE_FAILED  # low >= low (target policy)
    assert cli(["quality", str(tmp_path), "--gate", "--no-target-policy"], capsys)[0] == EXIT_OK
    write(tmp_path, {".dat/policy.json": "{"})
    assert cli(["quality", str(tmp_path)], capsys)[0] == EXIT_FAILURE  # invalid policy: cannot audit


def test_analyzers_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = cli(["analyzers", "--format", "json"], capsys)
    doc = json.loads(out)
    assert code == 0 and {a["id"] for a in doc["analyzers"]} >= {"secrets", "artifact", "git"}
    assert set(doc["capabilities"]) == set(engine.PROFILES)
    assert json.loads(cli(["version"], capsys)[1]) == {"name": "dat", "version": "4.0.0"}


def test_bundle_masks_secrets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, {"a.py": f'k = "{AWS_KEY}"\nPASSWORD = "{STRONG_VALUE}"\n', "Makefile": "all:\n\techo\n"})
    out = tmp_path / "out" / "B.md"
    assert cli(["cmd", str(tmp_path), "--out", str(out)], capsys)[0] == EXIT_OK
    text = out.read_text()
    assert "## `a.py`" in text and "## `Makefile`" in text
    assert AWS_KEY not in text and STRONG_VALUE not in text and text.count("***REDACTED***") == 2


# -- BLUX adapter -------------------------------------------------------------------------------------------


def _call(cap: str, payload: Any) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    code = blux_adapter.main(cap, stdin=io.BytesIO(raw), stdout=out)
    assert out.getvalue().count("\n") == 1
    return code, json.loads(out.getvalue())


CTX = {
    "operation_id": "op_01J0000000000000000000000A",
    "parent_operation_id": None,
    "session_id": None,
    "project_id": "dat",
    "capability_id": "repo.security",
    "actor": {"type": "user", "id": "u"},
}


def test_adapter_success_carries_linkage_and_gate_failure_is_still_success(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": f'k = "{AWS_KEY}"\n'})
    code, doc = _call(
        "repo.security", {"capability_id": "repo.security", "input": {"target": str(tmp_path)}, "context": CTX}
    )
    assert code == 0 and doc["outcome"] == "completed" and doc["gate"]["verdict"] == "fail"
    assert doc["linkage"] == {
        "operation_id": CTX["operation_id"],
        "parent_operation_id": None,
        "session_id": None,
        "project_id": "dat",
        "capability_id": "repo.security",
        "system": "blux",
    }


def test_adapter_failures(tmp_path: Path) -> None:
    code, doc = _call("repo.audit", {"input": {"target": str(tmp_path / "missing")}, "context": CTX})
    assert code == 1 and doc["error"]["code"] == "TARGET_NOT_FOUND"
    assert doc["error"]["details"]["outcome"] == "error"
    assert _call("repo.audit", b"not json")[1]["error"]["code"] == "INVALID_INPUT"
    assert _call("repo.audit", {"input": {"target": ".", "plugin": "x:Y"}})[1]["error"]["code"] == "INVALID_INPUT"
    assert _call("repo.audit", {"capability_id": "repo.quality", "input": {"target": "."}})[0] == 1
    assert _call("repo.audit", {"input": {"target": ".", "fail_on": "nope"}})[1]["error"]["code"] == "INVALID_INPUT"


def test_adapter_policy_verify(tmp_path: Path) -> None:
    (tmp_path / "p.json").write_text(json.dumps(POLICY))
    code, doc = _call("policy.verify", {"input": {"policy": str(tmp_path / "p.json")}, "context": CTX})
    assert code == 0 and doc["verdict"] == "pass" and doc["linkage"]["operation_id"] == CTX["operation_id"]
    code, doc = _call("policy.verify", {"input": {"policy": str(tmp_path / "none.json")}})
    assert code == 1 and doc["error"]["code"] == "POLICY_NOT_FOUND"


def _sdk_dot_blux() -> Any:
    """blux-sdk is only a test dependency. CI sets DAT_REQUIRE_BLUX_SDK=1 so these never silently skip."""
    if os.environ.get("DAT_REQUIRE_BLUX_SDK") == "1":
        import blux_sdk.dot_blux

        return blux_sdk.dot_blux
    return pytest.importorskip("blux_sdk.dot_blux")


def test_dot_blux_matches_the_engine() -> None:
    dot_blux = _sdk_dot_blux()
    project = dot_blux.load(ROOT)
    caps = {c.capability_id: c for c in project.capabilities()}
    assert set(caps) == set(blux_adapter.CAPABILITIES)
    assert project.manifest["role"] == "standalone"
    for cap in caps.values():
        assert cap.side_effects == "read"
        assert cap.provider["entrypoint"] == f"dat blux-capability {cap.capability_id}"


def test_dot_blux_output_schema_accepts_real_reports(tmp_path: Path) -> None:
    dot_blux = _sdk_dot_blux()
    caps = {c.capability_id: c for c in dot_blux.load(ROOT).capabilities()}
    write(tmp_path, {"a.py": "eval(x)\n"})
    (tmp_path / "p.json").write_text(json.dumps(POLICY))
    for cap in engine.PROFILES:
        code, doc = _call(cap, {"input": {"target": str(tmp_path)}, "context": {**CTX, "capability_id": cap}})
        assert code == 0
        caps[cap].validate_output(doc)
        caps[cap].validate_input({"target": "x", "fail_on": "low"})
    code, doc = _call("policy.verify", {"input": {"policy": str(tmp_path / "p.json")}, "context": CTX})
    caps["policy.verify"].validate_output(doc)


# -- standalone / independence --------------------------------------------------------------------------------


def test_dat_imports_no_blux_ai_or_network_modules() -> None:
    code = (
        "import sys, dat.cli, dat.engine, dat.blux_adapter, dat.verify, dat.bundle; "
        "print('\\n'.join(sorted(sys.modules)))"
    )
    loaded = set(
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    )
    forbidden = {
        m
        for m in loaded
        if m.split(".")[0]
        in {
            "blux_sdk",
            "blux_runtime",
            "blux_cli",
            "blux_system",
            "blux_ca",
            "blux_coga",
            "coga",
            "ca",
            "openai",
            "anthropic",
            "requests",
            "httpx",
            "urllib3",
            "http",
            "socket",
            "ssl",
        }
    }
    assert not forbidden, forbidden


def test_console_script_end_to_end(tmp_path: Path) -> None:
    exe = dat_exe()
    write(tmp_path, {"a.py": f'k = "{AWS_KEY}"\n'})
    env = {**os.environ, "PYTHONPATH": ""}
    proc = subprocess.run(
        [exe, "release", str(tmp_path), "--gate", "--format", "json"], capture_output=True, env=env, text=True
    )
    assert proc.returncode == EXIT_GATE_FAILED and proc.stdout.count("\n") == 1
    assert json.loads(proc.stdout)["capability"] == "repo.release"
    proc = subprocess.run([exe, "blux-capability", "repo.audit"], input=b"[]", capture_output=True, env=env)
    assert proc.returncode == 1 and json.loads(proc.stdout)["error"]["code"] == "INVALID_INPUT"
