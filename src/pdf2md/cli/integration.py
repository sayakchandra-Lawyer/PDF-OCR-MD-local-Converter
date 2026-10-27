"""`autostart`, `claude`, `doctor` and `mcp`."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from pdf2md.config import Config


def cmd_autostart(args, cfg: Config) -> int:
    from pdf2md.integrations import autostart

    print({"install": autostart.install, "uninstall": autostart.uninstall,
           "restart": autostart.restart, "status": autostart.status}[args.action]())
    return 0


def cmd_claude(args, cfg: Config) -> int:
    from pdf2md.integrations import claude as cs

    if args.action == "install-mcp":
        for p in cs.install_desktop_mcp():
            print(f"Claude Desktop: added 'pdf2md' MCP server to {p} (backup saved). Restart Claude Desktop.")
        print(cs.install_claude_code_mcp())
    elif args.action == "plugin-zip":
        out = Path(args.out).expanduser() if args.out else Path.cwd()
        print(f"Cowork plugin: {cs.build_plugin_zip(out)}")
        print("Upload it in Claude Desktop: Customize > Plugins > Upload.")
    elif args.action == "instructions":
        print(cs.GLOBAL_INSTRUCTIONS)
    return 0


def cmd_doctor(args, cfg: Config) -> int:
    from pdf2md.cli.doctor import run_doctor

    return run_doctor(cfg)


def cmd_mcp(args, cfg: Config) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)  # stdout belongs to the MCP protocol
    from pdf2md.integrations.mcp_server import run

    run(http=args.http, port=args.port)
    return 0
