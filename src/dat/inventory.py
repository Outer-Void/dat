"""Deterministic file inventory of an audit target.

Walks in sorted order, never follows symlinks, honours `.datignore` and policy ignores, and reads file
contents lazily with a size cap. Unreadable files are recorded as skipped, not raised.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import stat
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from dat.errors import DatError, ErrorCode

ALWAYS_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".gradle",
    }
)
DEFAULT_MAX_FILES = 50_000
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
BINARY_SNIFF = 8192


@dataclass(frozen=True)
class FileEntry:
    rel: str  # POSIX path relative to the target root
    size: int
    mode: int

    @property
    def name(self) -> str:
        return self.rel.rsplit("/", 1)[-1]

    @property
    def suffix(self) -> str:
        name = self.name
        return name[name.rfind(".") :].lower() if "." in name[1:] else ""


@dataclass(frozen=True)
class Skipped:
    rel: str
    reason: str


def _compile(patterns: Iterable[str]) -> list[tuple[str, bool, bool]]:
    """gitignore-lite: `dir/` matches directories, a pattern with `/` is anchored at the root, otherwise
    it matches the base name at any depth. Negation is not supported (and rejected by policy validation)."""
    out = []
    for raw in patterns:
        p = raw.strip()
        if not p or p.startswith("#"):
            continue
        dir_only = p.endswith("/")
        p = p.rstrip("/")
        anchored = "/" in p
        out.append((p.lstrip("/"), dir_only, anchored))
    return out


def _ignored(rel: str, is_dir: bool, rules: list[tuple[str, bool, bool]]) -> bool:
    base = rel.rsplit("/", 1)[-1]
    for pat, dir_only, anchored in rules:
        if dir_only and not is_dir:
            continue
        if fnmatch.fnmatchcase(rel if anchored else base, pat):
            return True
    return False


@dataclass
class Inventory:
    root: Path
    files: list[FileEntry] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    single_file: bool = False
    source: str = "walk"  # "git" when the file list came from `git ls-files`
    _text_cache: dict[str, str | None] = field(default_factory=dict, repr=False)

    @classmethod
    def build(
        cls,
        target: Path,
        *,
        ignore: Iterable[str] = (),
        max_files: int = DEFAULT_MAX_FILES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        use_git: bool = True,
    ) -> Inventory:
        """With use_git and a git work tree, audit what git would commit: tracked plus untracked files that
        are not gitignored. Otherwise (or if git is unavailable) walk the directory."""
        try:
            st = target.lstat()
        except FileNotFoundError:
            raise DatError(
                ErrorCode.TARGET_NOT_FOUND, "Target does not exist.", details={"target": str(target)}
            ) from None
        except OSError as exc:
            raise DatError(
                ErrorCode.TARGET_UNREADABLE, "Target cannot be read.", details={"errno": exc.errno}
            ) from None
        if stat.S_ISLNK(st.st_mode):
            raise DatError(ErrorCode.INVALID_TARGET, "Target is a symbolic link; pass the real path.")
        if stat.S_ISREG(st.st_mode):
            inv = cls(root=target.parent, max_file_bytes=max_file_bytes, single_file=True)
            inv.files.append(FileEntry(target.name, st.st_size, st.st_mode))
            return inv
        if not stat.S_ISDIR(st.st_mode):
            raise DatError(ErrorCode.INVALID_TARGET, "Target is neither a file nor a directory.")

        patterns = list(ignore)
        datignore = target / ".datignore"
        if datignore.is_file() and not datignore.is_symlink():
            patterns.extend(datignore.read_text(encoding="utf-8", errors="replace").splitlines())
        rules = _compile(patterns)
        inv = cls(root=target, max_file_bytes=max_file_bytes)
        listed = _git_files(target) if use_git and (target / ".git").exists() else None
        if listed is not None:
            inv.source = "git"
        entries = inv._from_list(target, listed, rules) if listed is not None else inv._walk(target, "", rules)
        for entry in entries:
            inv.files.append(entry)
            if len(inv.files) > max_files:
                raise DatError(
                    ErrorCode.LIMIT_EXCEEDED,
                    "Target has more files than the configured limit.",
                    details={"max_files": max_files},
                )
        return inv

    def _walk(self, base: Path, prefix: str, rules: list[tuple[str, bool, bool]]) -> Iterator[FileEntry]:
        try:
            names = sorted(os.listdir(base))
        except OSError as exc:
            self.skipped.append(Skipped(prefix or ".", f"unreadable_directory:{exc.errno}"))
            return
        for name in names:
            rel = f"{prefix}{name}"
            path = base / name
            try:
                st = path.lstat()
            except OSError as exc:
                self.skipped.append(Skipped(rel, f"unreadable:{exc.errno}"))
                continue
            if stat.S_ISLNK(st.st_mode):
                self.skipped.append(Skipped(rel, "symlink"))
                continue
            if stat.S_ISDIR(st.st_mode):
                if name in ALWAYS_IGNORED_DIRS or _ignored(rel, True, rules):
                    continue
                yield from self._walk(path, f"{rel}/", rules)
            elif stat.S_ISREG(st.st_mode):
                if _ignored(rel, False, rules):
                    continue
                yield FileEntry(rel, st.st_size, st.st_mode)
            else:
                self.skipped.append(Skipped(rel, "special_file"))

    def _from_list(self, base: Path, rels: list[str], rules: list[tuple[str, bool, bool]]) -> Iterator[FileEntry]:
        for rel in sorted(set(rels)):
            parts = rel.split("/")
            if any(p in ALWAYS_IGNORED_DIRS for p in parts[:-1]) or _ignored(rel, False, rules):
                continue
            if any(_ignored("/".join(parts[: i + 1]), True, rules) for i in range(len(parts) - 1)):
                continue
            try:
                st = (base / rel).lstat()
            except FileNotFoundError:
                continue  # tracked but deleted in the work tree
            except OSError as exc:
                self.skipped.append(Skipped(rel, f"unreadable:{exc.errno}"))
                continue
            if stat.S_ISLNK(st.st_mode):
                self.skipped.append(Skipped(rel, "symlink"))
            elif stat.S_ISREG(st.st_mode):
                yield FileEntry(rel, st.st_size, st.st_mode)
            elif not stat.S_ISDIR(st.st_mode):  # submodules show up as directories
                self.skipped.append(Skipped(rel, "special_file"))

    def path(self, entry: FileEntry | str) -> Path:
        return self.root / (entry.rel if isinstance(entry, FileEntry) else entry)

    def get(self, rel: str) -> FileEntry | None:
        for f in self.files:
            if f.rel == rel:
                return f
        return None

    def exists(self, rel: str) -> bool:
        return self.get(rel) is not None

    def read_bytes(self, entry: FileEntry) -> bytes | None:
        try:
            return self.path(entry).read_bytes()
        except OSError:
            return None

    def text(self, entry: FileEntry) -> str | None:
        """Decoded content, or None for binary, oversized or unreadable files (recorded as skipped once)."""
        if entry.rel in self._text_cache:
            return self._text_cache[entry.rel]
        value: str | None = None
        if entry.size > self.max_file_bytes:
            self.skipped.append(Skipped(entry.rel, "too_large"))
        else:
            data = self.read_bytes(entry)
            if data is None:
                self.skipped.append(Skipped(entry.rel, "unreadable"))
            elif b"\x00" in data[:BINARY_SNIFF]:
                value = None
            else:
                value = data.decode("utf-8", errors="replace")
        self._text_cache[entry.rel] = value
        return value

    def matching(self, *names: str, suffixes: Iterable[str] = ()) -> list[FileEntry]:
        sfx = tuple(suffixes)
        return [f for f in self.files if f.name in names or (sfx and f.suffix in sfx)]

    def skipped_sorted(self) -> list[Skipped]:
        return sorted(set(self.skipped), key=lambda s: (s.rel, s.reason))


def _git_files(root: Path) -> list[str] | None:
    exe = shutil.which("git")
    if exe is None:
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"})
    try:
        proc = subprocess.run(
            [
                exe,
                "-C",
                str(root),
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.quotepath=false",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=60,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if p]
