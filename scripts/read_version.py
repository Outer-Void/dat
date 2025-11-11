#!/usr/bin/env python3
"""Read package name and version from pyproject.toml"""

from pathlib import Path


def main():
    pyproject = Path("pyproject.toml")

    if not pyproject.exists():
        print("dat|0.0.0")
        return

    try:
        # Python 3.11+
        import tomllib
    except ImportError:
        try:
            # Python 3.6-3.10 with tomli
            import tomli as tomllib
        except ImportError:
            # Fallback
            print("dat|0.0.0")
            return

    try:
        data = tomllib.loads(pyproject.read_text())
        project = data.get("project", {})
        name = project.get("name", "dat")
        version = project.get("version", "0.0.0")
        print(f"{name}|{version}")
    except Exception:
        print("dat|0.0.0")


if __name__ == "__main__":
    main()
