"""What the app needs, and installing what's missing.

Needed: Windows 10 (1703) or newer, Python 3.10+, Tkinter.
Extras: Pillow and qrcode for the phone camera; the Bahnschrift font for the intended look.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import re
import site
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# import name -> (pip requirement, minimum version, what it's for, distribution, label).
# Keep in step with requirements.txt.
PACKAGES = {
    "PIL": ("pillow>=11.0", (11, 0), "the phone camera", "pillow", "Pillow"),
    "qrcode": ("qrcode>=7.4", (7, 4), "the QR code that connects your phone", "qrcode", "qrcode"),
}
CREATE_NO_WINDOW = 0x08000000


@dataclass
class Item:
    name: str
    ok: bool
    required: bool
    detail: str
    package: str | None = None  # pip requirement that fixes it, if pip can


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:2])


def _package(module: str, spec: str, minimum: tuple[int, int], purpose: str, dist: str, label: str) -> Item:
    name = f"{label} (for {purpose})"
    if importlib.util.find_spec(module) is None:
        return Item(name, False, False, "Not installed.", spec)
    try:
        version = importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return Item(name, True, False, "Installed.")
    if _version(version) < minimum:
        return Item(name, False, False, f"Version {version} is too old.", spec)
    return Item(name, True, False, f"Version {version}.")


def _tkinter() -> Item:
    name = "Tkinter (draws the window)"
    try:
        import tkinter

        version = tkinter.TclVersion
    except Exception:  # missing from some Python builds
        return Item(name, False, True, "Missing. Reinstall Python from python.org with \"tcl/tk and IDLE\" ticked.")
    return Item(name, True, True, f"Tk {version}.")


def scan() -> list[Item]:
    items = []
    build = sys.getwindowsversion().build if sys.platform == "win32" else 0
    label = "Windows 11" if build >= 22000 else "Windows 10" if build >= 10240 else "Windows"
    items.append(Item("Windows 10 or 11", build >= 15063, True,
                      f"{label}, build {build}." if build else "This isn't Windows."))
    v = sys.version_info
    items.append(Item("Python 3.10 or newer", v >= (3, 10), True, f"Python {v.major}.{v.minor}.{v.micro}."))
    items.append(_tkinter())
    has_pip = importlib.util.find_spec("pip") is not None
    items.append(Item("pip (installs the extras)", has_pip, False,
                      "Available." if has_pip else "Missing. Fix it with: python -m ensurepip --upgrade"))
    for module, details in PACKAGES.items():
        items.append(_package(module, *details))
    font = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "bahnschrift.ttf"
    items.append(Item("Bahnschrift font (looks only)", font.exists(), False,
                      "Installed." if font.exists() else "Not found. The app uses Segoe UI instead."))
    return items


def missing_packages(items: list[Item] | None = None) -> list[str]:
    return [item.package for item in (items if items is not None else scan()) if not item.ok and item.package]


def pip_command(specs: list[str]) -> list[str]:
    """The pip call for this Python: the console python.exe, and a per-user install outside virtual envs."""
    python = Path(sys.executable)
    console = python.with_name("python.exe")
    if python.name.lower() == "pythonw.exe" and console.exists():
        python = console
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):  # not inside a virtual env
        command.append("--user")
    return command + list(specs)


def install(specs: list[str]) -> tuple[bool, str]:
    """Install with pip. Returns (worked, the last lines pip printed)."""
    try:
        result = subprocess.run(pip_command(specs), capture_output=True, text=True, timeout=900,
                                creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode == 0:
        _refresh_paths()
    lines = (result.stdout + result.stderr).strip().splitlines()
    return result.returncode == 0, "\n".join(lines[-6:])


def _refresh_paths() -> None:
    """Make freshly installed packages importable right away (the per-user folder may be brand new)."""
    user_site = site.getusersitepackages()
    if os.path.isdir(user_site) and user_site not in sys.path:
        site.addsitedir(user_site)
    importlib.invalidate_caches()
