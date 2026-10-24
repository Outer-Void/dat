"""Python source checks on the AST. Nothing is executed or imported from the target."""

from __future__ import annotations

import ast
import warnings
from collections.abc import Iterator

from dat.analyzers.base import Analyzer, Rule, RunOptions
from dat.inventory import Inventory
from dat.model import Category, Confidence, Finding, Severity

S, Q = Category.SECURITY, Category.QUALITY
H, M, L = Severity.HIGH, Severity.MEDIUM, Severity.LOW
HC, MC = Confidence.HIGH, Confidence.MEDIUM

SAFE_YAML_LOADERS = frozenset({"SafeLoader", "CSafeLoader", "BaseLoader"})
SUBPROCESS_NAMES = frozenset({"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"})
HTTP_CALLS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "request", "Session", "Client", "AsyncClient"}
)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_const(node: ast.expr | None, value: object) -> bool:
    return isinstance(node, ast.Constant) and node.value is value


def _safe_loader_classes(tree: ast.Module) -> frozenset[str]:
    """SafeLoader and module-level classes that (transitively) subclass it are safe yaml.load Loaders."""
    safe = set(SAFE_YAML_LOADERS)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    changed = True
    while changed:
        changed = False
        for cls in classes:
            if cls.name not in safe and any(_dotted(b).rsplit(".", 1)[-1] in safe for b in cls.bases):
                safe.add(cls.name)
                changed = True
    return frozenset(safe)


class PythonAnalyzer(Analyzer):
    id = "python"
    version = "1.0.0"
    rules = (
        Rule("python.syntax-error", Q, H, HC, "Python file does not parse"),
        Rule(
            "python.shell-injection",
            S,
            H,
            HC,
            "Subprocess with shell=True",
            "Pass an argument list without shell=True.",
        ),
        Rule("python.os-system", S, M, HC, "os.system / os.popen", "Use subprocess.run with an argument list."),
        Rule("python.eval", S, M, MC, "eval/exec of dynamic code"),
        Rule("python.unsafe-deserialization", S, M, MC, "Unsafe deserialization (pickle/marshal)"),
        Rule(
            "python.yaml-unsafe-load",
            S,
            H,
            HC,
            "yaml.load without a safe loader",
            "Use yaml.safe_load or Loader=yaml.SafeLoader.",
        ),
        Rule("python.tls-verify-disabled", S, H, MC, "TLS verification disabled"),
        Rule("python.insecure-tempfile", S, M, HC, "tempfile.mktemp is race-prone", "Use tempfile.mkstemp."),
        Rule("python.debugger", Q, M, HC, "Debugger breakpoint left in code"),
        Rule("python.bare-except", Q, L, HC, "Bare except", "Catch specific exceptions."),
    )

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        for entry in inv.files:
            if entry.suffix not in (".py", ".pyw"):
                continue
            text = inv.text(entry)
            if text is None:
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    tree = ast.parse(text, filename=entry.rel)
            except SyntaxError as exc:
                yield self.finding(
                    "python.syntax-error", f"SyntaxError: {exc.msg}.", path=entry.rel, line=exc.lineno or None
                )
                continue
            except ValueError:
                yield self.finding("python.syntax-error", "Source contains null bytes.", path=entry.rel)
                continue
            self._safe_loaders = _safe_loader_classes(tree)
            yield from self._visit(entry.rel, tree)

    def _visit(self, rel: str, tree: ast.AST) -> Iterator[Finding]:
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                yield self.finding("python.bare-except", "Bare `except:` clause.", path=rel, line=node.lineno)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                if any(n in ("pdb", "ipdb", "pudb") for n in names):
                    yield self.finding("python.debugger", "Debugger module imported.", path=rel, line=node.lineno)
            elif isinstance(node, ast.Call):
                yield from self._call(rel, node)

    def _call(self, rel: str, call: ast.Call) -> Iterator[Finding]:
        name = _dotted(call.func)
        line = call.lineno
        tail = name.rsplit(".", 1)[-1]
        if (name.startswith("subprocess.") or name in SUBPROCESS_NAMES) and _is_const(_kw(call, "shell"), True):
            yield self.finding("python.shell-injection", f"{name}(..., shell=True).", path=rel, line=line)
        elif name in ("os.system", "os.popen"):
            yield self.finding("python.os-system", f"{name}() runs a shell.", path=rel, line=line)
        elif name in ("eval", "exec") and not (call.args and isinstance(call.args[0], ast.Constant)):
            yield self.finding("python.eval", f"{name}() on a non-literal.", path=rel, line=line)
        elif name in ("pickle.load", "pickle.loads", "marshal.load", "marshal.loads", "cPickle.loads"):
            yield self.finding("python.unsafe-deserialization", f"{name}().", path=rel, line=line)
        elif name in ("yaml.unsafe_load", "yaml.full_load"):
            yield self.finding("python.yaml-unsafe-load", f"{name}().", path=rel, line=line)
        elif name == "yaml.load":
            loader = _kw(call, "Loader") or (call.args[1] if len(call.args) > 1 else None)
            if loader is None or _dotted(loader).rsplit(".", 1)[-1] not in self._safe_loaders:
                yield self.finding("python.yaml-unsafe-load", "yaml.load() without a safe Loader.", path=rel, line=line)
        elif name == "tempfile.mktemp":
            yield self.finding("python.insecure-tempfile", "tempfile.mktemp().", path=rel, line=line)
        elif name in ("breakpoint", "pdb.set_trace", "ipdb.set_trace"):
            yield self.finding("python.debugger", f"{name}() call.", path=rel, line=line)
        if _is_const(_kw(call, "verify"), False) and tail in HTTP_CALLS:
            yield self.finding("python.tls-verify-disabled", f"{name}(..., verify=False).", path=rel, line=line)
