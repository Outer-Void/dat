"""Build artifact audit: archive safety, wheel RECORD integrity, checksum files, embedded secrets.

Archives are inspected, never extracted. Reads are bounded so a hostile archive (zip bomb, millions of
members) cannot exhaust memory; hitting a bound is itself a finding.
"""

from __future__ import annotations

import base64
import hashlib
import re
import stat
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from dat.analyzers.base import Analyzer, Rule, RunOptions
from dat.analyzers.secrets import SHAPES, scan_text
from dat.inventory import FileEntry, Inventory
from dat.model import Category, Confidence, Finding, Severity

A, S = Category.ARTIFACT, Category.SECURITY
ZIP_SUFFIXES = (".zip", ".whl", ".jar", ".apk", ".aar", ".egg")
TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
MAX_MEMBERS = 100_000
MAX_DECLARED_BYTES = 2 * 1024**3
MAX_RATIO = 200
MAX_TEXT_MEMBER = 1024 * 1024
READ_BUDGET = 64 * 1024 * 1024
CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+)$")
_TITLES = {s.rule_id: s.title for s in SHAPES}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_kind(name: str) -> str | None:
    low = name.lower()
    if low.endswith(TAR_SUFFIXES):
        return "tar"
    if low.endswith(ZIP_SUFFIXES):
        return "zip"
    return None


@dataclass
class Member:
    name: str
    size: int
    compressed: int | None
    mode: int | None
    kind: str  # file, dir, symlink, hardlink, special
    link: str | None = None


Opener = Callable[[Member], bytes]


def _unsafe_name(name: str) -> bool:
    n = name.replace("\\", "/")
    return n.startswith("/") or re.match(r"^[A-Za-z]:", n) is not None or ".." in n.split("/")


class ArtifactAnalyzer(Analyzer):
    id = "artifact"
    version = "1.0.0"
    target_kinds = frozenset({"artifact"})
    rules = (
        Rule("artifact.corrupt-archive", A, Severity.HIGH, Confidence.HIGH, "Archive cannot be read"),
        Rule(
            "artifact.path-traversal",
            S,
            Severity.CRITICAL,
            Confidence.HIGH,
            "Archive member escapes the extraction directory",
        ),
        Rule("artifact.unsafe-link", S, Severity.HIGH, Confidence.HIGH, "Link member points outside the archive"),
        Rule("artifact.special-member", S, Severity.HIGH, Confidence.HIGH, "Device, FIFO or setuid/setgid member"),
        Rule("artifact.resource-limit", S, Severity.HIGH, Confidence.MEDIUM, "Archive exceeds safe expansion limits"),
        Rule("artifact.embedded-secret", S, Severity.CRITICAL, Confidence.HIGH, "Credential inside artifact"),
        Rule("artifact.record-missing", A, Severity.HIGH, Confidence.HIGH, "Wheel has no RECORD"),
        Rule("artifact.record-mismatch", A, Severity.HIGH, Confidence.HIGH, "Wheel RECORD does not match contents"),
        Rule("artifact.checksum-mismatch", A, Severity.CRITICAL, Confidence.HIGH, "Checksum does not match"),
        Rule("artifact.checksum-target-missing", A, Severity.MEDIUM, Confidence.HIGH, "Checksum lists a missing file"),
    )

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        self._budget = READ_BUDGET
        for entry in inv.files:
            kind = archive_kind(entry.name)
            if kind is not None:
                yield from self._archive(inv, entry, kind)
            elif entry.name in ("SHA256SUMS", "SHA256SUMS.txt", "sha256sums.txt") or entry.name.endswith(".sha256"):
                yield from self._checksums(inv, entry)

    # -- archives ---------------------------------------------------------------------------------------
    def _archive(self, inv: Inventory, entry: FileEntry, kind: str) -> Iterator[Finding]:
        path = inv.path(entry)
        try:
            if kind == "zip":
                with zipfile.ZipFile(path) as zf:
                    yield from self._zip(entry.rel, zf)
            else:
                with tarfile.open(path, mode="r:*") as tf:
                    yield from self._tar(entry.rel, tf)
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, ValueError) as exc:
            yield self.finding(
                "artifact.corrupt-archive", f"{type(exc).__name__} while reading {kind} archive.", path=entry.rel
            )

    def _zip(self, rel: str, zf: zipfile.ZipFile) -> Iterator[Finding]:
        infos = zf.infolist()
        members = []
        for i in infos[: MAX_MEMBERS + 1]:
            mode = (i.external_attr >> 16) & 0xFFFF or None
            kind = "dir" if i.is_dir() else "symlink" if mode and stat.S_ISLNK(mode) else "file"
            link = zf.read(i).decode("utf-8", "replace") if kind == "symlink" and i.file_size < 4096 else None
            members.append(Member(i.filename, i.file_size, i.compress_size, mode, kind, link))
        yield from self._structure(rel, members)

        def opener(m: Member) -> bytes:
            return zf.read(m.name)

        yield from self._contents(rel, members, opener)
        if rel.lower().endswith(".whl"):
            yield from self._wheel_record(rel, members, opener)

    def _tar(self, rel: str, tf: tarfile.TarFile) -> Iterator[Finding]:
        members: list[Member] = []
        tar_infos: dict[str, tarfile.TarInfo] = {}
        for i, ti in enumerate(tf):
            if i > MAX_MEMBERS:
                break
            if ti.isreg():
                kind = "file"
            elif ti.isdir():
                kind = "dir"
            elif ti.issym():
                kind = "symlink"
            elif ti.islnk():
                kind = "hardlink"
            else:
                kind = "special"
            members.append(Member(ti.name, ti.size, None, ti.mode, kind, ti.linkname or None))
            tar_infos[ti.name] = ti
        yield from self._structure(rel, members)

        def opener(m: Member) -> bytes:
            fh = tf.extractfile(tar_infos[m.name])
            return fh.read() if fh is not None else b""

        yield from self._contents(rel, members, opener)

    def _structure(self, rel: str, members: list[Member]) -> Iterator[Finding]:
        if len(members) > MAX_MEMBERS:
            yield self.finding("artifact.resource-limit", f"More than {MAX_MEMBERS} members.", path=rel)
        declared = sum(m.size for m in members)
        compressed = sum(m.compressed or 0 for m in members)
        if declared > MAX_DECLARED_BYTES or (
            compressed and declared > 10 * 1024**2 and declared / compressed > MAX_RATIO
        ):
            yield self.finding(
                "artifact.resource-limit", f"Expands to {declared} bytes (possible decompression bomb).", path=rel
            )
        for m in members:
            if _unsafe_name(m.name):
                yield self.finding(
                    "artifact.path-traversal", "Member name escapes the archive root.", path=rel, member=m.name
                )
            if m.kind in ("symlink", "hardlink") and m.link is not None:
                base = m.name.rsplit("/", 1)[0] if "/" in m.name and m.kind == "symlink" else ""
                resolved = f"{base}/{m.link}" if base else m.link
                absolute = m.link.startswith("/") or re.match(r"^[A-Za-z]:", m.link) is not None
                if absolute or _escapes(resolved):
                    yield self.finding("artifact.unsafe-link", f"{m.kind} -> {m.link}.", path=rel, member=m.name)
            if m.kind == "special" or (m.mode is not None and m.mode & (stat.S_ISUID | stat.S_ISGID)):
                yield self.finding(
                    "artifact.special-member", "Special file or setuid/setgid bit.", path=rel, member=m.name
                )

    def _contents(self, rel: str, members: list[Member], opener: Opener) -> Iterator[Finding]:
        for m in members:
            if m.kind != "file" or m.size > MAX_TEXT_MEMBER or _unsafe_name(m.name):
                continue
            if self._budget <= 0:
                yield self.finding(
                    "artifact.resource-limit",
                    "Content read budget exhausted; scan truncated.",
                    path=rel,
                    confidence=Confidence.HIGH,
                    severity=Severity.MEDIUM,
                )
                return
            data = opener(m)[:MAX_TEXT_MEMBER]
            self._budget -= len(data)
            if b"\x00" in data[:8192]:
                continue
            for rule_id, line, _col, evidence, confidence in scan_text(data.decode("utf-8", "replace")):
                yield self.finding(
                    "artifact.embedded-secret",
                    f"{_TITLES[rule_id]} ({rule_id}) inside {m.name}.",
                    path=rel,
                    member=m.name,
                    line=line,
                    evidence=evidence,
                    confidence=confidence,
                )

    def _wheel_record(self, rel: str, members: list[Member], opener: Opener) -> Iterator[Finding]:
        records = [m for m in members if re.match(r"^[^/]+\.dist-info/RECORD$", m.name)]
        if len(records) != 1:
            yield self.finding("artifact.record-missing", "No single *.dist-info/RECORD.", path=rel)
            return
        record = records[0]
        listed: dict[str, tuple[str, str]] = {}
        for row in opener(record).decode("utf-8", "replace").splitlines():
            parts = row.rsplit(",", 2)
            if len(parts) == 3:
                listed[parts[0]] = (parts[1], parts[2])
        files = {m.name: m for m in members if m.kind == "file"}
        for name in sorted(files):
            if name == record.name or name.endswith((".dist-info/RECORD.jws", ".dist-info/RECORD.p7s")):
                continue
            if name not in listed:
                yield self.finding("artifact.record-mismatch", "File not listed in RECORD.", path=rel, member=name)
                continue
            digest, size = listed.pop(name)
            algo, _, expected = digest.partition("=")
            if algo != "sha256" or not expected:
                yield self.finding(
                    "artifact.record-mismatch", "RECORD entry lacks a sha256 hash.", path=rel, member=name
                )
                continue
            data = opener(files[name])
            actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            if actual != expected or (size and size != str(len(data))):
                yield self.finding(
                    "artifact.record-mismatch", "Hash or size differs from RECORD.", path=rel, member=name
                )
        for name in sorted(listed):
            if name != record.name:
                yield self.finding("artifact.record-mismatch", "RECORD lists a missing file.", path=rel, member=name)

    # -- checksum files -----------------------------------------------------------------------------------
    def _checksums(self, inv: Inventory, entry: FileEntry) -> Iterator[Finding]:
        text = inv.text(entry) or ""
        directory = entry.rel.rsplit("/", 1)[0] + "/" if "/" in entry.rel else ""
        for no, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            m = CHECKSUM_LINE.match(line)
            if m:
                expected, name = m.group(1).lower(), m.group(2).strip()
            elif re.fullmatch(r"[0-9a-fA-F]{64}", line) and entry.name.endswith(".sha256"):
                expected, name = line.lower(), entry.name[: -len(".sha256")]
            else:
                continue
            if _unsafe_name(name):
                yield self.finding(
                    "artifact.checksum-target-missing", f"Unsafe path {name!r}.", path=entry.rel, line=no
                )
                continue
            target = inv.path(f"{directory}{name}")
            if not target.is_file() or target.is_symlink():
                yield self.finding("artifact.checksum-target-missing", f"{name} not found.", path=entry.rel, line=no)
            elif sha256_file(target) != expected:
                yield self.finding(
                    "artifact.checksum-mismatch", f"{name} does not match.", path=entry.rel, line=no, evidence=name
                )


def _escapes(path: str) -> bool:
    depth = 0
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        depth += -1 if part == ".." else 1
        if depth < 0:
            return True
    return False
