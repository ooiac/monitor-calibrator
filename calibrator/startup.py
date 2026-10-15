"""Shortcuts: the optional sign-in re-apply, and the Start menu entry the installer offers.

Windows forgets GPU color curves at every reboot. The sign-in shortcut runs
`calibrate.py --apply-saved` (no window) to put a kept calibration back. Turning
either off deletes its shortcut and nothing else.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "calibrate.py"
PROGRAMS_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
STARTUP_DIR = PROGRAMS_DIR / "Startup"
LINK = STARTUP_DIR / "Monitor Calibrator - re-apply.lnk"
MENU_LINK = PROGRAMS_DIR / "Monitor Calibrator.lnk"
CREATE_NO_WINDOW = 0x08000000


def _pythonw() -> str:
    windowless = Path(sys.executable).with_name("pythonw.exe")
    return str(windowless if windowless.exists() else sys.executable)


def make_shortcut(link: Path, arguments: str, description: str) -> None:
    # Paths travel as environment variables, so spaces and quotes in them can't break the script.
    env = dict(os.environ, MC_LINK=str(link), MC_TARGET=_pythonw(), MC_ARGS=arguments,
               MC_DIR=str(SCRIPT.parent), MC_DESC=description)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MC_LINK); "
        "$s.TargetPath = $env:MC_TARGET; $s.Arguments = $env:MC_ARGS; $s.WorkingDirectory = $env:MC_DIR; "
        "$s.Description = $env:MC_DESC; $s.Save()"
    )
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env, check=True, capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW,
    )


def is_enabled() -> bool:
    return LINK.exists()


def enable() -> None:
    make_shortcut(LINK, f'"{SCRIPT}" --apply-saved --delay 10', "Re-applies your kept monitor calibration")


def disable() -> None:
    LINK.unlink(missing_ok=True)


def has_menu_shortcut() -> bool:
    return MENU_LINK.exists()


def add_menu_shortcut() -> None:
    make_shortcut(MENU_LINK, f'"{SCRIPT}"', "Tune and match your screens")


def remove_shortcuts() -> list[str]:
    removed = []
    for link in (MENU_LINK, LINK):
        if link.exists():
            link.unlink()
            removed.append(link.name)
    return removed
