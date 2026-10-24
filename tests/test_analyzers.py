"""Per-analyzer behaviour: true positives, the guard rails that keep false positives down, and redaction."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import (
    AWS_KEY,
    GITHUB_TOKEN,
    PRIVATE_KEY_HEADER,
    STRIPE_KEY,
    STRONG_VALUE,
    audit,
    by_rule,
    fake,
    rules,
    write,
)

# -- secrets ----------------------------------------------------------------------------------------------


def test_shape_rules_detect_and_redact(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "a.py": f'k = "{AWS_KEY}"\nt = "{GITHUB_TOKEN}"\ns = "{STRIPE_KEY}"\n',
            "id_rsa": f"{PRIVATE_KEY_HEADER}\nMIIE...\n",
            "db.env": f"DATABASE_URL=postgres://svc:{STRONG_VALUE}@db.internal/app\n",
        },
    )
    report = audit(tmp_path, "repo.security")
    found = set(rules(report))
    assert {
        "secrets.aws-access-key-id",
        "secrets.github-token",
        "secrets.stripe-key",
        "secrets.private-key",
        "secrets.url-credentials",
    } <= found
    blob = json.dumps(report)
    for secret in (AWS_KEY, GITHUB_TOKEN, STRIPE_KEY, STRONG_VALUE):
        assert secret not in blob, "evidence must be redacted"
    aws = by_rule(report, "secrets.aws-access-key-id")[0]
    assert aws["location"] == {"path": "a.py", "line": 1, "column": 6, "member": None}
    assert aws["severity"] == "high" and aws["confidence"] == "high"


def test_generic_rule_needs_literal_entropy_and_no_placeholder(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "c.py": (
                f'DB_PASSWORD = "{STRONG_VALUE}"\n'
                'API_KEY = "changeme-please"\n'
                'TOKEN = "${VAULT_TOKEN}"\n'
                'password = "aaaaaaaaaaaa"\n'
            ),
            "package-lock.json": f'{{"integrity_api_key": "{STRONG_VALUE}"}}\n',
        },
    )
    hits = by_rule(audit(tmp_path), "secrets.hardcoded-credential")
    assert [(h["location"]["path"], h["location"]["line"]) for h in hits] == [("c.py", 1)]


def test_url_with_weak_example_password_is_low_confidence(tmp_path: Path) -> None:
    write(tmp_path, {"e.sh": "export URL=postgresql://user:pass@host/db\n"})
    (hit,) = by_rule(audit(tmp_path), "secrets.url-credentials")
    assert hit["confidence"] == "low"


# -- python -------------------------------------------------------------------------------------------------


def test_python_ast_rules(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "m.py": "\n".join(
                [
                    "import subprocess, os, pickle, yaml, tempfile, requests",
                    "subprocess.run(cmd, shell=True)",  # 2
                    "subprocess.run(['ls'], shell=False)",
                    "os.system(cmd)",  # 4
                    "eval(user_input)",  # 5
                    "eval('1 + 1')",
                    "pickle.loads(blob)",  # 7
                    "yaml.load(text)",  # 8
                    "yaml.load(text, Loader=yaml.SafeLoader)",
                    "yaml.safe_load(text)",
                    "requests.get(url, verify=False)",  # 11
                    "tempfile.mktemp()",  # 12
                    "try:\n    pass\nexcept:\n    pass",  # 15
                    "breakpoint()",  # 17
                    "",
                ]
            )
        },
    )
    got = sorted(
        (f["rule_id"], f["location"]["line"]) for f in audit(tmp_path)["findings"] if f["rule_id"].startswith("python.")
    )
    assert got == [
        ("python.bare-except", 15),
        ("python.debugger", 17),
        ("python.eval", 5),
        ("python.insecure-tempfile", 12),
        ("python.os-system", 4),
        ("python.shell-injection", 2),
        ("python.tls-verify-disabled", 11),
        ("python.unsafe-deserialization", 7),
        ("python.yaml-unsafe-load", 8),
    ]


def test_syntax_error_is_a_finding_not_a_failure(tmp_path: Path) -> None:
    write(tmp_path, {"bad.py": "def f(:\n"})
    report = audit(tmp_path, "repo.quality")
    assert report["outcome"] == "completed"
    assert by_rule(report, "python.syntax-error")[0]["location"]["line"] == 1


# -- quality ------------------------------------------------------------------------------------------------


def test_quality_rules(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "c.txt": "a\n<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs\n",
            "w.py": "x = 1  # TODO: tidy\n# FIXME later\nlabel = 'TODO'\n",
            "crlf.txt": "a\r\nb\nc\r\n",
            "big.txt": "x" * (1024 * 1024 + 1),
        },
    )
    report = audit(tmp_path, "repo.quality")
    assert [f["location"]["line"] for f in by_rule(report, "quality.merge-conflict")] == [2]
    assert [f["location"]["line"] for f in by_rule(report, "quality.work-marker")] == [1, 2]
    assert [f["location"]["path"] for f in by_rule(report, "quality.mixed-line-endings")] == ["crlf.txt"]
    assert [f["location"]["path"] for f in by_rule(report, "quality.large-file")] == ["big.txt"]


# -- javascript ---------------------------------------------------------------------------------------------


def test_npm_manifest_rules(tmp_path: Path) -> None:
    pkg = {
        "name": "x",
        "version": "1.0.0",
        "scripts": {"postinstall": "node setup.js", "test": "jest"},
        "dependencies": {
            "left-pad": "*",
            "lib": "github:org/lib",
            "ok": "^1.2.3",
            "pinned": "git+https://g.example/r.git#" + "a" * 40,
        },
    }
    write(
        tmp_path,
        {
            "package.json": json.dumps(pkg),
            "x.js": "const f = eval(code);\nconst g = new Function('a');\n",
            "x.min.js": "eval(a)",
        },
    )
    report = audit(tmp_path)
    assert by_rule(report, "js.install-script")[0]["message"] == "scripts.postinstall runs on install."
    assert sorted(f["evidence"] for f in by_rule(report, "js.unpinned-dependency")) == ["*", "github:org/lib"]
    assert len(by_rule(report, "js.missing-lockfile")) == 1
    assert [f["location"]["line"] for f in by_rule(report, "js.dynamic-code")] == [1, 2]
    write(tmp_path, {"package-lock.json": "{}"})
    assert not by_rule(audit(tmp_path), "js.missing-lockfile")


def test_invalid_package_json(tmp_path: Path) -> None:
    write(tmp_path, {"package.json": "{,}"})
    assert rules(audit(tmp_path, "package.audit")) == ["js.manifest-invalid"]


# -- android ------------------------------------------------------------------------------------------------

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="x">
  <application android:debuggable="true" android:allowBackup="true" android:usesCleartextTraffic="true">
    <activity android:name=".Main" android:exported="true">
      <intent-filter>
        <action android:name="android.intent.action.MAIN"/>
        <category android:name="android.intent.category.LAUNCHER"/>
      </intent-filter>
    </activity>
    <service android:name=".Open" android:exported="true"/>
    <service android:name=".Guarded" android:exported="true" android:permission="x.P"/>
    <receiver android:name=".Private" android:exported="false"/>
  </application>
</manifest>
"""


def test_android_manifest_and_gradle(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "app/src/main/AndroidManifest.xml": MANIFEST,
            "app/build.gradle": 'signingConfigs { release { storePassword "' + STRONG_VALUE + '"\n'
            'keyPassword System.getenv("KP") } }\n',
        },
    )
    report = audit(tmp_path, "repo.security")
    assert {"android.debuggable", "android.allow-backup", "android.cleartext-traffic"} <= set(rules(report))
    assert [f["message"] for f in by_rule(report, "android.exported-component")] == ["service .Open is exported."]
    assert len(by_rule(report, "android.gradle-signing-secret")) == 1


def test_android_manifest_with_entities_is_not_parsed(tmp_path: Path) -> None:
    bomb = '<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "aaaa">]><manifest>&a;</manifest>'
    write(tmp_path, {"AndroidManifest.xml": bomb})
    assert rules(audit(tmp_path)).count("android.manifest-unparseable") == 1


# -- containers ---------------------------------------------------------------------------------------------


def test_dockerfile_rules(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "Dockerfile": "\n".join(
                [
                    "FROM python AS build",  # 1 unpinned
                    "RUN curl -fsSL https://x.example/i.sh \\",  # 2 pipe to shell (continued)
                    "    | sh",
                    "FROM build AS test",  # stage ref: fine
                    "FROM alpine:3.20@sha256:" + "0" * 64,  # pinned
                    "ADD https://x.example/a.tgz /a",  # 6
                    "ENV API_TOKEN=" + STRONG_VALUE + " MODE=prod",  # 7
                    "ARG GITHUB_TOKEN",  # no value: fine
                    "",
                ]
            ),
            "svc/Dockerfile.prod": "FROM debian:12\nUSER app\n",
        },
    )
    report = audit(tmp_path, "repo.security")
    got = sorted(
        (f["rule_id"], f["location"]["path"], f["location"]["line"])
        for f in report["findings"]
        if f["rule_id"].startswith("container.")
    )
    assert got == [
        ("container.pipe-to-shell", "Dockerfile", 2),
        ("container.remote-add", "Dockerfile", 6),
        ("container.runs-as-root", "Dockerfile", 5),
        ("container.secret-in-env", "Dockerfile", 7),
        ("container.unpinned-base-image", "Dockerfile", 1),
    ]


# -- packaging ----------------------------------------------------------------------------------------------


def test_pyproject_and_requirements(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "x"\ndependencies = ["a>=1", '
            '"b @ git+https://g.example/b.git@main", '
            '"c @ git+https://g.example/c.git@' + "f" * 40 + '"]\n',
            "requirements.txt": "--index-url http://pypi.internal/simple\n--trusted-host pypi.internal\n"
            "requests\nflask==3.0.0\n-e git+https://g.example/d.git#egg=d\n",
            "tools/pyproject.toml": "[tool.ruff]\nline-length = 100\n",
        },
    )
    report = audit(tmp_path, "package.audit")
    got = sorted((f["rule_id"], f["location"]["path"]) for f in report["findings"])
    assert got == [
        ("package.insecure-index", "requirements.txt"),
        ("package.missing-license", "pyproject.toml"),
        ("package.missing-requires-python", "pyproject.toml"),
        ("package.missing-version", "pyproject.toml"),
        ("package.trusted-host", "requirements.txt"),
        ("package.unpinned-vcs-dependency", "pyproject.toml"),
        ("package.unpinned-vcs-dependency", "requirements.txt"),
        ("package.unversioned-requirement", "requirements.txt"),
    ]


def test_invalid_pyproject(tmp_path: Path) -> None:
    write(tmp_path, {"pyproject.toml": "[project\n"})
    assert rules(audit(tmp_path, "package.audit")) == ["package.pyproject-invalid"]


def test_fake_helper_keeps_sources_clean() -> None:
    assert fake("a", "b") == "ab"
