"""Android: source AndroidManifest.xml flags and Gradle signing secrets.

The manifest parser refuses DTDs and entities, so a hostile manifest cannot expand or fetch anything.
Compiled (binary XML) manifests inside APKs are out of scope; artifact.audit covers APK archives.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator

from dat.analyzers.base import Analyzer, Rule, RunOptions
from dat.inventory import Inventory
from dat.model import Category, Confidence, Finding, Severity

S, Q = Category.SECURITY, Category.QUALITY
NS = "{http://schemas.android.com/apk/res/android}"
COMPONENTS = ("activity", "activity-alias", "service", "receiver", "provider")
GRADLE_SECRET = re.compile(r"""\b(storePassword|keyPassword)\b\s*(?:=\s*|\(\s*|\s+)(["'])([^"'$]{1,128})\2""")


class AndroidAnalyzer(Analyzer):
    id = "android"
    version = "1.0.0"
    rules = (
        Rule("android.manifest-unparseable", Q, Severity.MEDIUM, Confidence.HIGH, "AndroidManifest.xml not parsed"),
        Rule(
            "android.debuggable",
            S,
            Severity.HIGH,
            Confidence.HIGH,
            "android:debuggable=true",
            "Remove the attribute; build types control debuggability.",
        ),
        Rule("android.allow-backup", S, Severity.LOW, Confidence.HIGH, "android:allowBackup=true"),
        Rule(
            "android.cleartext-traffic",
            S,
            Severity.MEDIUM,
            Confidence.HIGH,
            "Cleartext HTTP allowed",
            "Set usesCleartextTraffic=false or scope it with a network security config.",
        ),
        Rule(
            "android.exported-component",
            S,
            Severity.MEDIUM,
            Confidence.MEDIUM,
            "Exported component without permission",
            "Set android:exported=false or require a permission.",
        ),
        Rule("android.gradle-signing-secret", S, Severity.HIGH, Confidence.HIGH, "Signing password in Gradle file"),
    )

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        for entry in inv.matching("AndroidManifest.xml"):
            text = inv.text(entry)
            if text is None:
                continue  # binary XML
            yield from self._manifest(entry.rel, text)
        for entry in inv.matching("build.gradle", "build.gradle.kts"):
            text = inv.text(entry)
            if text is None:
                continue
            for m in GRADLE_SECRET.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                yield self.finding(
                    "android.gradle-signing-secret", f"{m.group(1)} is a literal.", path=entry.rel, line=line
                )

    def _manifest(self, rel: str, text: str) -> Iterator[Finding]:
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            yield self.finding("android.manifest-unparseable", "DTD/entities are not allowed.", path=rel)
            return
        try:
            root = ET.fromstring(text)  # noqa: S314 - DTDs rejected above; no entity expansion possible
        except ET.ParseError as exc:
            yield self.finding("android.manifest-unparseable", f"XML error: {exc}.", path=rel)
            return
        app = root.find("application")
        if app is None:
            return
        flags = (
            ("debuggable", "android.debuggable", "Application is debuggable."),
            ("allowBackup", "android.allow-backup", "App data can be extracted with adb backup."),
            ("usesCleartextTraffic", "android.cleartext-traffic", "Cleartext HTTP is allowed."),
        )
        for attr, rule_id, message in flags:
            if app.get(f"{NS}{attr}", "").lower() == "true":
                yield self.finding(rule_id, message, path=rel)
        for tag in COMPONENTS:
            for comp in app.iter(tag):
                if comp.get(f"{NS}exported", "").lower() != "true" or comp.get(f"{NS}permission"):
                    continue
                if tag == "provider" and (comp.get(f"{NS}readPermission") or comp.get(f"{NS}writePermission")):
                    continue
                if _is_launcher(comp):
                    continue
                name = comp.get(f"{NS}name", "?")
                yield self.finding("android.exported-component", f"{tag} {name} is exported.", path=rel)


def _is_launcher(comp: ET.Element) -> bool:
    for f in comp.iter("intent-filter"):
        actions = {a.get(f"{NS}name") for a in f.iter("action")}
        cats = {c.get(f"{NS}name") for c in f.iter("category")}
        if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in cats:
            return True
    return False
