"""Snapshots of a display's settings so every change can be undone.

Per display (folder named after the monitor's id and serial):
  original.json   first snapshot ever taken, never overwritten (the "factory" undo)
  previous.json   state right before the most recent change
  pending.json    exists only while a change awaits "keep?"; if the app dies,
                  the next start restores it automatically
  confirmed.json  the calibration you chose to keep (re-applied after reboots)
  history.log     what happened, when
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .profiles import Calibration

DATA_DIR = Path(
    os.environ.get("MONITOR_CALIBRATOR_HOME")
    or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MonitorCalibrator"
)


@dataclass
class Snapshot:
    display_key: str
    display_name: str
    taken_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    ramp: list[list[int]] | None = None
    ddc: dict[int, int] = field(default_factory=dict)
    brightness: int | None = None

    def to_json(self) -> dict:
        return {
            "display_key": self.display_key,
            "display_name": self.display_name,
            "taken_at": self.taken_at,
            "ramp": self.ramp,
            "ddc": {f"0x{code:02X}": value for code, value in self.ddc.items()},
            "brightness": self.brightness,
        }

    @classmethod
    def from_json(cls, data: dict) -> Snapshot:
        return cls(
            display_key=data["display_key"],
            display_name=data.get("display_name", ""),
            taken_at=data.get("taken_at", ""),
            ramp=data.get("ramp"),
            ddc={int(code, 16): value for code, value in (data.get("ddc") or {}).items()},
            brightness=data.get("brightness"),
        )


def migrate_folders(displays) -> list[str]:
    """Move backups saved under the first version's folder names, once and only when it's unambiguous."""
    root = DATA_DIR / "displays"
    moved = []
    for display in displays:
        old, new = display.legacy_key, display.key
        if old == new or sum(d.legacy_key == old for d in displays) != 1:
            continue  # nothing to do, or two connected monitors claim the same old folder
        source, target = root / old, root / new
        if source.is_dir() and not target.exists():
            try:
                source.rename(target)
                moved.append(f"Moved the backups of {display.name} to their new folder.")
            except OSError:
                pass
    return moved


def display_dir(key: str) -> Path:
    path = DATA_DIR / "displays" / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written backup


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(snapshot: Snapshot, kind: str) -> None:
    _write(display_dir(snapshot.display_key) / f"{kind}.json", snapshot.to_json())


def load(key: str, kind: str) -> Snapshot | None:
    data = _read(display_dir(key) / f"{kind}.json")
    return Snapshot.from_json(data) if data else None


def save_original_if_missing(snapshot: Snapshot) -> bool:
    if (display_dir(snapshot.display_key) / "original.json").exists():
        return False
    save(snapshot, "original")
    return True


def set_pending(snapshot: Snapshot) -> None:
    save(snapshot, "pending")


def clear_pending(key: str) -> None:
    (display_dir(key) / "pending.json").unlink(missing_ok=True)


def pending_snapshots() -> list[Snapshot]:
    root = DATA_DIR / "displays"
    if not root.exists():
        return []
    found = []
    for path in root.glob("*/pending.json"):
        data = _read(path)
        if data:
            found.append(Snapshot.from_json(data))
    return found


def save_confirmed(key: str, calibration: Calibration) -> None:
    _write(display_dir(key) / "confirmed.json", calibration.to_json())


def load_confirmed(key: str) -> Calibration | None:
    data = _read(display_dir(key) / "confirmed.json")
    return Calibration.from_json(data) if data else None


def clear_confirmed(key: str) -> None:
    (display_dir(key) / "confirmed.json").unlink(missing_ok=True)


def declined(key: str) -> list[str]:
    """Calibrations you went back from. They are not applied again by themselves."""
    return (_read(display_dir(key) / "declined.json") or {}).get("ids", [])


def set_declined(key: str, calibration_id: str, is_declined: bool) -> None:
    ids = [i for i in declined(key) if i != calibration_id]
    if is_declined:
        ids.append(calibration_id)
    _write(display_dir(key) / "declined.json", {"ids": ids})


def log(key: str, message: str) -> None:
    with open(display_dir(key) / "history.log", "a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")


def load_settings() -> dict:
    return _read(DATA_DIR / "settings.json") or {}


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write(DATA_DIR / "settings.json", settings)
