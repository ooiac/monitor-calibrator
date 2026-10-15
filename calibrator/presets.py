"""Looks you can load into the manual sliders: built-in ones plus your own.

A preset only sets sliders that exist on the current screen (no DDC/CI ->
no monitor preset, built-in panel -> brightness goes to the panel instead).

Keys: temperature (K), tint (±100, + = magenta), gamma, monitor_preset (name),
brightness (0-100, monitor or panel), contrast, red_gain/green_gain/blue_gain.
"""
from __future__ import annotations

import json

from . import backup

BUILTIN = [
    {"name": "Standard", "temperature": 6500, "tint": 0, "gamma": 2.2,
     "about": "6500 K, gamma 2.2. What the web, Windows and most content are made for."},
    {"name": "Photo editing", "temperature": 6500, "tint": 0, "gamma": 2.2, "monitor_preset": "sRGB",
     "about": "The monitor's own sRGB mode with neutral graphics card colors."},
    {"name": "Movies", "temperature": 6500, "tint": 0, "gamma": 2.4,
     "about": "Gamma 2.4. Deeper shadows, the way films are graded for dark rooms."},
    {"name": "Gaming", "temperature": 6500, "tint": 0, "gamma": 2.0,
     "about": "Gamma 2.0. Lifts dark areas so you can see into shadows."},
    {"name": "Cool daylight", "temperature": 7500, "tint": 0, "gamma": 2.2,
     "about": "7500 K. A crisper, bluer white for bright rooms."},
    {"name": "Warm", "temperature": 5500, "tint": 0, "gamma": 2.2,
     "about": "5500 K. A little warmer, like paper in daylight."},
    {"name": "Reading", "temperature": 4800, "tint": 0, "gamma": 2.2, "brightness": 50,
     "about": "4800 K and half brightness, easier on long reads."},
    {"name": "Evening", "temperature": 4000, "tint": 0, "gamma": 2.2,
     "about": "4000 K. About what Night Light does at medium."},
    {"name": "Night", "temperature": 3400, "tint": 0, "gamma": 2.2, "brightness": 30,
     "about": "3400 K, dim. Windows may refuse tints this strong (see the README)."},
]

KEYS = ("temperature", "tint", "gamma", "monitor_preset", "brightness", "contrast",
        "red_gain", "green_gain", "blue_gain")


def _path():
    return backup.DATA_DIR / "presets.json"


def user_presets() -> list[dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return [p for p in data if isinstance(p, dict) and p.get("name")]
    except (OSError, ValueError):
        return []


def all_presets() -> list[dict]:
    return [dict(p, user=False) for p in BUILTIN] + [dict(p, user=True) for p in user_presets()]


def save_user_preset(preset: dict) -> None:
    presets = [p for p in user_presets() if p["name"] != preset["name"]]
    presets.append({k: preset[k] for k in ("name", "about", *KEYS) if k in preset})
    backup.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(presets, indent=2), encoding="utf-8")


def delete_user_preset(name: str) -> None:
    presets = [p for p in user_presets() if p["name"] != name]
    _path().write_text(json.dumps(presets, indent=2), encoding="utf-8")
