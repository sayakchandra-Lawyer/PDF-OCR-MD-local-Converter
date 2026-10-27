"""`pdf2md update`: move to the newest GitHub release (or `git pull` for a clone)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from pdf2md import GITHUB_REPO, __version__

API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"


def git_checkout() -> Path | None:
    """Repository root when pdf2md runs from a git clone (editable install)."""
    root = Path(__file__).resolve().parents[3]
    return root if (root / ".git").exists() and (root / "pyproject.toml").exists() else None


def parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split(".")[:3] if x.isdigit())


def latest_release() -> dict:
    req = urllib.request.Request(API_LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": "pdf2md"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}
    pick = lambda pred: next((url for name, url in assets.items() if pred(name)), None)  # noqa: E731
    return {
        "version": data.get("tag_name", "").lstrip("v"),
        "page": data.get("html_url", RELEASES_PAGE),
        "wheel": pick(lambda n: n.endswith(".whl")),
        "installer": pick(lambda n: n.lower().endswith(".exe")),
        "plugin": pick(lambda n: n.startswith("pdf-markdown-first-") and n.endswith(".zip")),
    }


def find_uv() -> str | None:
    candidates = [shutil.which("uv")]
    home = Path.home()
    candidates += [str(home / ".local/bin" / ("uv.exe" if os.name == "nt" else "uv"))]
    if os.name == "nt":
        candidates.append(str(Path(os.environ.get("LOCALAPPDATA", home)) / "Programs/pdf2md/uv.exe"))
    return next((c for c in candidates if c and Path(c).exists()), None)


def install_command(target: str, editable: bool = False) -> list[str]:
    uv = find_uv()
    spec = ["-e", target] if editable else ["--upgrade", target]
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, *spec]
    return [sys.executable, "-m", "pip", "install", *spec]


def _download(url: str, folder: Path) -> Path:
    out = folder / url.rsplit("/", 1)[-1]
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "pdf2md"}), timeout=300) as r:
        out.write_bytes(r.read())
    return out


def _finish_on_windows(steps: list[list[str]]) -> None:
    """Windows locks pdf2md.exe while it runs, so finish the install from a
    separate console window after this process has exited."""
    script = Path(tempfile.gettempdir()) / "pdf2md-update.cmd"
    lines = ["@echo off", "echo Updating pdf2md...", "timeout /t 3 /nobreak >nul"]
    for cmd in steps:
        lines.append(subprocess.list2cmdline(cmd) + " || goto :failed")
    lines += [
        "echo.", "echo pdf2md updated. You can close this window.", "pause", "exit /b 0",
        ":failed", "echo.", "echo Update failed - see the messages above.", "pause", "exit /b 1",
    ]
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    subprocess.Popen(["cmd.exe", "/c", "start", "pdf2md update", "cmd.exe", "/c", str(script)], close_fds=True)


def run_update(check_only: bool = False) -> int:
    from pdf2md.integrations import autostart

    pdf2md_cmd = [sys.executable, "-m", "pdf2md"]
    repo = git_checkout()
    if repo:
        print(f"pdf2md {__version__} runs from a git clone: {repo}")
        if check_only:
            subprocess.run(["git", "-C", str(repo), "fetch", "-q"], check=False)
            subprocess.run(["git", "-C", str(repo), "status", "-sb"], check=False)
            return 0
        r = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"])
        if r.returncode != 0:
            print("git pull failed; resolve it and run pdf2md update again.")
            return 1
        install = install_command(str(repo), editable=True)
    else:
        try:
            rel = latest_release()
        except Exception as e:  # noqa: BLE001 - offline, rate limit, ...
            print(f"Could not check for updates ({e}). Latest release: {RELEASES_PAGE}")
            return 1
        print(f"Installed: {__version__}   Latest release: {rel['version'] or '?'}")
        if not rel["version"] or parse_version(rel["version"]) <= parse_version(__version__):
            print("You are up to date.")
            return 0
        if check_only:
            print(f"Update available: {rel['page']}")
            return 0
        if not rel["wheel"]:
            print(f"The release has no package to install; download the installer from {rel['page']}")
            return 1
        print("Downloading", rel["wheel"])
        wheel = _download(rel["wheel"], Path(tempfile.mkdtemp(prefix="pdf2md-")))
        install = install_command(str(wheel))

    had_autostart = "not installed" not in autostart.status().splitlines()[0]
    autostart.stop()
    after = [pdf2md_cmd + ["autostart", "install"]] if had_autostart else []
    after.append(pdf2md_cmd + ["claude", "plugin-zip", "--out", str(Path.home() / "Desktop")])
    if os.name == "nt":
        _finish_on_windows([install, *after])
        print("The update continues in a new window.")
        return 0
    if subprocess.run(install).returncode != 0:
        print("Installation failed.")
        return 1
    for cmd in after:
        subprocess.run(cmd)
    print("pdf2md updated. If the plugin changed, re-upload the zip on your Desktop in Claude Desktop "
          "(Customize > Plugins).")
    return 0
