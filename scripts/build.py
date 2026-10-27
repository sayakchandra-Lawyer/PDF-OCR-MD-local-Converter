"""Build the release artefacts into dist/.

    python scripts/build.py                     check versions, then build everything
    python scripts/build.py --check             only check that all version numbers agree (CI)
    python scripts/build.py --set-version 0.3.0 bump the version everywhere, then stop

Artefacts (all versioned):
    pdf_ocr_md_local_converter-<v>-py3-none-any.whl   Python package (what installers install)
    pdf_ocr_md_local_converter-<v>.tar.gz             source package
    pdf-markdown-first-<v>.zip                         Claude Cowork plugin (Customize > Plugins > Upload)
    SHA256SUMS.txt
The Windows installer (packaging/windows) is built from these in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "src/pdf2md/__init__.py"
PLUGIN_JSON = ROOT / "src/pdf2md/claude_plugin/.claude-plugin/plugin.json"
MARKETPLACE_JSON = ROOT / ".claude-plugin/marketplace.json"
DIST = ROOT / "dist"
VERSION_RE = re.compile(r'^__version__ = "([^"]+)"', re.M)
REQUIRED_IN_WHEEL = [
    "pdf2md/claude_plugin/.claude-plugin/plugin.json",
    "pdf2md/claude_plugin/hooks/hooks.json",
    "pdf2md/claude_plugin/hooks/pdf_read_guard.py",
    "pdf2md/claude_plugin/skills/pdf-markdown-first/SKILL.md",
]


def package_version() -> str:
    return VERSION_RE.search(INIT.read_text(encoding="utf-8")).group(1)


def manifest_versions() -> dict[str, str]:
    plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    market = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
    return {
        "src/pdf2md/__init__.py": package_version(),
        "plugin.json": plugin["version"],
        "marketplace.json": market["plugins"][0]["version"],
    }


def check_versions() -> str:
    versions = manifest_versions()
    if len(set(versions.values())) != 1:
        sys.exit(f"Version mismatch: {versions}. Fix with: python scripts/build.py --set-version X.Y.Z")
    return versions["src/pdf2md/__init__.py"]


def set_version(new: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        sys.exit("Version must look like 1.2.3")
    INIT.write_text(VERSION_RE.sub(f'__version__ = "{new}"', INIT.read_text(encoding="utf-8")), encoding="utf-8")
    for path, setter in (
        (PLUGIN_JSON, lambda d: d.__setitem__("version", new)),
        (MARKETPLACE_JSON, lambda d: d["plugins"][0].__setitem__("version", new)),
    ):
        data = json.loads(path.read_text(encoding="utf-8"))
        setter(data)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Version set to {new}. Commit, then tag:  git tag v{new} && git push origin v{new}")


def build_python_packages() -> None:
    try:
        import build  # noqa: F401

        cmd = [sys.executable, "-m", "build", "--outdir", str(DIST), str(ROOT)]
    except ImportError:
        uv = shutil.which("uv")
        if not uv:
            sys.exit("Install the 'build' package (pip install build) or uv to build the wheel.")
        cmd = [uv, "build", "--out-dir", str(DIST), str(ROOT)]
    subprocess.run(cmd, check=True)


def check_wheel(version: str) -> Path:
    wheels = list(DIST.glob(f"pdf_ocr_md_local_converter-{version}-*.whl"))
    if len(wheels) != 1:
        sys.exit(f"Expected one wheel for {version} in {DIST}, found {wheels}")
    names = set(zipfile.ZipFile(wheels[0]).namelist())
    missing = [n for n in REQUIRED_IN_WHEEL if n not in names]
    if missing:
        sys.exit(f"Wheel is missing plugin files: {missing}")
    return wheels[0]


def build_plugin_zip() -> Path:
    sys.path.insert(0, str(ROOT / "src"))
    from pdf2md.integrations.claude import build_plugin_zip as build_zip

    return build_zip(DIST)


def write_checksums() -> Path:
    out = DIST / "SHA256SUMS.txt"
    lines = []
    for f in sorted(DIST.iterdir()):
        if f.is_file() and f.name != out.name:
            lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--set-version")
    args = ap.parse_args()
    if args.set_version:
        set_version(args.set_version)
        return
    version = check_versions()
    if args.check:
        print(f"Versions consistent: {version}")
        return
    shutil.rmtree(DIST, ignore_errors=True)
    DIST.mkdir()
    build_python_packages()
    wheel = check_wheel(version)
    plugin = build_plugin_zip()
    sums = write_checksums()
    print(f"\nBuilt pdf2md {version}:")
    for f in (wheel, *DIST.glob("*.tar.gz"), plugin, sums):
        print(f"  {f.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
