"""Built-in analyzers and plugin loading.

Plugins are opt-in per run (`--plugin`) and never loaded implicitly, so a default DAT run executes only
the reviewed built-ins. A plugin is either an installed entry point in group `dat.analyzers` or an
explicit `module:Class` reference.
"""

from __future__ import annotations

import importlib
from importlib import metadata

from dat.analyzers.android import AndroidAnalyzer
from dat.analyzers.artifact import ArtifactAnalyzer
from dat.analyzers.base import Analyzer, Rule, RunOptions, Skip
from dat.analyzers.container import ContainerAnalyzer
from dat.analyzers.javascript import JavaScriptAnalyzer
from dat.analyzers.packaging import PackagingAnalyzer
from dat.analyzers.python_code import PythonAnalyzer
from dat.analyzers.quality import QualityAnalyzer
from dat.analyzers.release import GitStateAnalyzer, ReleaseAnalyzer
from dat.analyzers.secrets import SecretsAnalyzer
from dat.errors import DatError, ErrorCode
from dat.model import RULE_ID

ENTRY_POINT_GROUP = "dat.analyzers"

BUILTIN: tuple[type[Analyzer], ...] = (
    SecretsAnalyzer,
    QualityAnalyzer,
    PythonAnalyzer,
    JavaScriptAnalyzer,
    AndroidAnalyzer,
    ContainerAnalyzer,
    PackagingAnalyzer,
    ReleaseAnalyzer,
    GitStateAnalyzer,
    ArtifactAnalyzer,
)


def builtin_ids() -> list[str]:
    return [a.id for a in BUILTIN]


def _validate_class(obj: object, ref: str) -> type[Analyzer]:
    if not (isinstance(obj, type) and issubclass(obj, Analyzer)):
        raise DatError(ErrorCode.PLUGIN_LOAD_FAILED, "Plugin is not a dat Analyzer subclass.", details={"plugin": ref})
    for attr in ("id", "version"):
        if not isinstance(getattr(obj, attr, None), str):
            raise DatError(ErrorCode.PLUGIN_LOAD_FAILED, f"Plugin lacks a string {attr}.", details={"plugin": ref})
    if obj.id in builtin_ids():
        raise DatError(ErrorCode.PLUGIN_LOAD_FAILED, "Plugin id shadows a built-in.", details={"plugin": ref})
    for r in obj.rules:
        if not RULE_ID.match(r.rule_id):
            raise DatError(ErrorCode.PLUGIN_LOAD_FAILED, "Plugin rule id is invalid.", details={"plugin": ref})
    return obj


def load_plugin(ref: str) -> type[Analyzer]:
    try:
        if ":" in ref:
            module, _, attr = ref.partition(":")
            obj: object = getattr(importlib.import_module(module), attr)
        else:
            eps = [ep for ep in metadata.entry_points(group=ENTRY_POINT_GROUP) if ep.name == ref]
            if not eps:
                raise DatError(ErrorCode.PLUGIN_LOAD_FAILED, "No such plugin entry point.", details={"plugin": ref})
            obj = eps[0].load()
    except DatError:
        raise
    except Exception as exc:  # noqa: BLE001 - any import failure is a load failure
        raise DatError(
            ErrorCode.PLUGIN_LOAD_FAILED,
            "Plugin could not be imported.",
            details={"plugin": ref, "exception_type": type(exc).__name__},
        ) from None
    return _validate_class(obj, ref)


def installed_plugins() -> list[str]:
    return sorted(ep.name for ep in metadata.entry_points(group=ENTRY_POINT_GROUP))


__all__ = ["BUILTIN", "ENTRY_POINT_GROUP", "Analyzer", "Rule", "RunOptions", "Skip", "load_plugin"]
