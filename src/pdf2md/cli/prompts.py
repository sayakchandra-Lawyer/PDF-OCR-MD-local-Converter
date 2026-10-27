"""Small interactive helpers for the setup wizard."""

from __future__ import annotations


def ask(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{question}{suffix}: ").strip()
    except EOFError:
        ans = ""
    return ans or default


def yes(question: str, default: bool = True, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    ans = ask(question + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not ans else ans.startswith("y")
