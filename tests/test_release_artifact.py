"""repo.release (files, versions, git state) and artifact.audit (archives, RECORD, checksums)."""

from __future__ import annotations

import base64
import hashlib
import io
import stat
import tarfile
import zipfile
from pathlib import Path

from tests.conftest import AWS_KEY, audit, by_rule, git, git_repo, needs_git, rules, write

RELEASABLE = {
    "LICENSE": "MIT\n",
    "README.md": "# x\n",
    "CHANGELOG.md": "## 1.2.0\n- first\n",
    "pyproject.toml": '[project]\nname = "x"\nversion = "1.2.0"\nlicense = "MIT"\nrequires-python = ">=3.11"\n',
    "src/x/__init__.py": '__version__ = "1.2.0"\n',
}


def test_release_files_and_versions(tmp_path: Path) -> None:
    write(
        tmp_path,
        {"pyproject.toml": '[project]\nname = "x"\nversion = "1.0.0"\n', "package.json": '{"version": "1.0.1"}'},
    )
    found = set(rules(audit(tmp_path, "repo.release")))
    assert {
        "release.missing-license",
        "release.missing-readme",
        "release.missing-changelog",
        "release.version-inconsistent",
    } <= found


def test_release_changelog_must_mention_version(tmp_path: Path) -> None:
    write(tmp_path, {**RELEASABLE, "CHANGELOG.md": "## 1.1.0\n## 1.2.00\n"})
    assert rules(audit(tmp_path, "repo.release")) == ["release.changelog-missing-version"]


@needs_git
def test_git_clean_tagged_release_passes(tmp_path: Path) -> None:
    git_repo(tmp_path, RELEASABLE)
    git(tmp_path, "tag", "v1.2.0")
    report = audit(tmp_path, "repo.release")
    assert report["findings"] == []
    assert report["gate"]["verdict"] == "pass"
    assert {a["id"]: a["status"] for a in report["analyzers"]}["git"] == "ok"


@needs_git
def test_git_dirty_untagged_and_mismatched(tmp_path: Path) -> None:
    git_repo(tmp_path, RELEASABLE)
    assert rules(audit(tmp_path, "repo.release")) == ["release.untagged-version"]
    git(tmp_path, "tag", "v1.2.0")
    write(tmp_path, {"README.md": "# changed\n", "new.txt": "x\n"})
    (dirty,) = by_rule(audit(tmp_path, "repo.release"), "release.dirty-worktree")
    assert dirty["message"].startswith("2 uncommitted change(s)")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "more")
    assert rules(audit(tmp_path, "repo.release")) == ["release.tag-mismatch"]


@needs_git
def test_git_inventory_honours_gitignore(tmp_path: Path) -> None:
    git_repo(tmp_path, {".gitignore": "build/\n", "a.py": "x = 1\n"})
    write(tmp_path, {"build/out.py": f'k = "{AWS_KEY}"\n', "untracked.py": "y = 2\n"})
    report = audit(tmp_path, "repo.security")
    assert report["target"]["inventory"] == "git"
    assert report["target"]["files"] == 3  # .gitignore, a.py, untracked.py
    assert report["findings"] == []


def test_non_git_directory_skips_git_checks(tmp_path: Path) -> None:
    write(tmp_path, RELEASABLE)
    report = audit(tmp_path, "repo.release")
    git_run = next(a for a in report["analyzers"] if a["id"] == "git")
    assert git_run == {
        "id": "git",
        "version": "1.0.0",
        "origin": "builtin",
        "status": "skipped",
        "reason": "not a git repository",
        "findings": 0,
    }
    assert report["outcome"] == "completed"


# -- artifacts ----------------------------------------------------------------------------------------------


def _zip(path: Path, members: dict[str, bytes], modes: dict[str, int] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if modes and name in modes:
                info.external_attr = modes[name] << 16
            zf.writestr(info, data)
    return path


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


def _wheel(path: Path, files: dict[str, bytes], *, tamper: str | None = None) -> Path:
    record = "\n".join([_record_line(n, d) for n, d in files.items()] + ["x-1.0.dist-info/RECORD,,"]) + "\n"
    if tamper:
        files = {**files, tamper: b"tampered"}
    return _zip(path, {**files, "x-1.0.dist-info/RECORD": record.encode()})


def test_good_wheel_and_checksums(tmp_path: Path) -> None:
    files = {"x/__init__.py": b"x = 1\n", "x-1.0.dist-info/METADATA": b"Name: x\n"}
    whl = _wheel(tmp_path / "x-1.0-py3-none-any.whl", files)
    digest = hashlib.sha256(whl.read_bytes()).hexdigest()
    (tmp_path / "SHA256SUMS").write_text(f"{digest}  {whl.name}\n")
    report = audit(tmp_path, "artifact.audit")
    assert report["findings"] == [] and report["outcome"] == "completed"
    art = next(a for a in report["target"]["artifacts"] if a["path"] == whl.name)
    assert art == {"path": whl.name, "size": whl.stat().st_size, "sha256": digest, "archive": "zip"}


def test_tampered_wheel_and_bad_checksum(tmp_path: Path) -> None:
    whl = _wheel(tmp_path / "x-1.0-py3-none-any.whl", {"x/__init__.py": b"x = 1\n"}, tamper="x/__init__.py")
    (tmp_path / "x.whl.sha256").write_text("0" * 64 + "\n")
    (tmp_path / "SHA256SUMS").write_text(f"{'1' * 64}  {whl.name}\n{'2' * 64}  missing.tar.gz\n")
    report = audit(tmp_path, "artifact.audit")
    assert by_rule(report, "artifact.record-mismatch")[0]["location"]["member"] == "x/__init__.py"
    assert len(by_rule(report, "artifact.checksum-mismatch")) == 1
    assert len(by_rule(report, "artifact.checksum-target-missing")) == 2  # missing.tar.gz and x.whl


def test_hostile_zip(tmp_path: Path) -> None:
    _zip(
        tmp_path / "evil.zip",
        {
            "../../etc/cron.d/x": b"* * * * * root sh\n",
            "ok/link": b"/etc/passwd",
            "bin/su": b"\x7fELF",
            "cfg/settings.py": f'k = "{AWS_KEY}"\n'.encode(),
        },
        modes={"ok/link": stat.S_IFLNK | 0o777, "bin/su": stat.S_IFREG | 0o4755},
    )
    report = audit(tmp_path / "evil.zip", "artifact.audit")
    got = {(f["rule_id"], f["location"]["member"]) for f in report["findings"]}
    assert ("artifact.path-traversal", "../../etc/cron.d/x") in got
    assert ("artifact.unsafe-link", "ok/link") in got
    assert ("artifact.special-member", "bin/su") in got
    assert ("artifact.embedded-secret", "cfg/settings.py") in got
    assert AWS_KEY not in str(report)


def test_hostile_tar(tmp_path: Path) -> None:
    path = tmp_path / "evil.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        for name, kind, target in (
            ("a/../../x", tarfile.REGTYPE, ""),
            ("l", tarfile.SYMTYPE, "../../outside"),
            ("inside", tarfile.SYMTYPE, "a/b"),
            ("dev", tarfile.CHRTYPE, ""),
        ):
            ti = tarfile.TarInfo(name)
            ti.type, ti.linkname = kind, target
            tf.addfile(ti, io.BytesIO(b"") if kind == tarfile.REGTYPE else None)
    got = {(f["rule_id"], f["location"]["member"]) for f in audit(path, "artifact.audit")["findings"]}
    assert got == {
        ("artifact.path-traversal", "a/../../x"),
        ("artifact.unsafe-link", "l"),
        ("artifact.special-member", "dev"),
    }


def test_corrupt_archive_is_a_finding(tmp_path: Path) -> None:
    (tmp_path / "broken.whl").write_bytes(b"PK\x03\x04 not really")
    report = audit(tmp_path, "artifact.audit")
    assert report["outcome"] == "completed"
    assert rules(report) == ["artifact.corrupt-archive"]


def test_decompression_bomb_is_flagged_without_reading_it(tmp_path: Path) -> None:
    _zip(tmp_path / "bomb.zip", {"zeros.bin": b"\0" * (12 * 1024 * 1024)})
    assert "artifact.resource-limit" in rules(audit(tmp_path, "artifact.audit"))
