"""Start the watcher automatically at login (macOS LaunchAgent, Windows Startup
folder, Linux systemd user service)."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from pdf2md.config import LOG_DIR

LABEL = "com.pdf2md.watcher"


def _python(windowless: bool = False) -> str:
    exe = Path(sys.executable)
    if windowless and os.name == "nt":
        pyw = exe.with_name("pythonw.exe")
        if pyw.exists():
            return str(pyw)
    return str(exe)


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _win_startup() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "Microsoft/Windows/Start Menu/Programs/Startup/pdf2md-watcher.vbs"


def _linux_unit() -> Path:
    return Path.home() / ".config/systemd/user/pdf2md.service"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def install() -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        plist = _mac_plist()
        plist.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "Label": LABEL,
            "ProgramArguments": [_python(), "-m", "pdf2md", "watch"],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "Nice": 5,
            "StandardOutPath": str(LOG_DIR / "watcher.out.log"),
            "StandardErrorPath": str(LOG_DIR / "watcher.err.log"),
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        }
        with open(plist, "wb") as f:
            plistlib.dump(data, f)
        uid = os.getuid()
        _run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"])
        r = _run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)])
        if r.returncode != 0:
            r = _run(["launchctl", "load", "-w", str(plist)])
        return f"LaunchAgent installed: {plist}" + (f" (launchctl: {r.stderr.strip()})" if r.returncode else "")
    if os.name == "nt":
        vbs = _win_startup()
        vbs.parent.mkdir(parents=True, exist_ok=True)
        cmd = f'""{_python(windowless=True)}"" -m pdf2md watch'
        vbs.write_text(
            'Set sh = CreateObject("WScript.Shell")\r\n'
            f'sh.Run "{cmd}", 0, False\r\n',
            encoding="utf-8",
        )
        subprocess.Popen(["wscript.exe", str(vbs)], close_fds=True)
        return f"Startup entry installed: {vbs} (watcher started)"
    unit = _linux_unit()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Unit]\nDescription=pdf2md PDF->Markdown watcher\n\n"
        f"[Service]\nExecStart={_python()} -m pdf2md watch\nRestart=on-failure\nNice=5\n\n"
        "[Install]\nWantedBy=default.target\n",
        encoding="utf-8",
    )
    _run(["systemctl", "--user", "daemon-reload"])
    r = _run(["systemctl", "--user", "enable", "--now", "pdf2md.service"])
    return f"systemd user service installed: {unit}" + (f" ({r.stderr.strip()})" if r.returncode else "")


def stop() -> None:
    from pdf2md.watcher import watcher_state

    if sys.platform == "darwin":
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        return
    if sys.platform.startswith("linux") and _linux_unit().exists():
        _run(["systemctl", "--user", "stop", "pdf2md.service"])
        return
    st = watcher_state()
    if st and st.get("alive") and st.get("pid"):
        if os.name == "nt":
            _run(["taskkill", "/PID", str(st["pid"]), "/F"])
        else:
            import signal

            os.kill(int(st["pid"]), signal.SIGTERM)


def unload_models() -> None:
    """Ask Ollama to free the vision model's memory now instead of after keep_alive."""
    from pdf2md.config import load_config
    from pdf2md.engine.ollama import OllamaClient, OllamaError

    cfg = load_config()
    client = OllamaClient(cfg.llm.ollama_url, timeout=15)
    if not client.available():
        return
    for model in {cfg.llm.vision_model, cfg.llm.page_model}:
        try:
            client._request("POST", "/api/generate", {"model": model, "keep_alive": 0}, timeout=15)
        except OllamaError:
            pass


def uninstall() -> str:
    stop()
    unload_models()
    removed = []
    for p in (_mac_plist(), _win_startup(), _linux_unit()):
        if p.exists():
            if p == _linux_unit():
                _run(["systemctl", "--user", "disable", "pdf2md.service"])
            p.unlink()
            removed.append(str(p))
    return "Removed: " + ", ".join(removed) if removed else "No autostart entry found."


def restart() -> str:
    stop()
    time.sleep(1)
    return install()


def status() -> str:
    from pdf2md.watcher import watcher_state

    installed = [str(p) for p in (_mac_plist(), _win_startup(), _linux_unit()) if p.exists()]
    st = watcher_state()
    running = bool(st and st.get("alive"))
    lines = [
        f"autostart: {'installed (' + ', '.join(installed) + ')' if installed else 'not installed'}",
        f"watcher: {'running, pid ' + str(st.get('pid')) if running else 'not running'}",
    ]
    if running:
        lines.append(f"current: {st.get('current') or 'idle'}; queue: {st.get('queue')}; "
                     f"converted: {st.get('converted')}; failed: {st.get('failed')}")
    lines.append(f"logs: {LOG_DIR}")
    return "\n".join(lines)
