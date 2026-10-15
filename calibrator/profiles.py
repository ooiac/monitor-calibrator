"""Search for calibrations that fit a display, best first.

Sources, in the order the auto-calibrator trusts them:
  1. profiles/*.json: your own saved calibrations (or ones you write by hand)
  2. ICC profiles with calibration curves ('vcgt'): the one Windows has set for
     this display, ICC files in profiles/, and matching files in Windows' color folder
  3. the monitor's factory sRGB / 6500 K preset, switched over DDC/CI
  4. a white-point / gamma correction computed from the EDID the monitor reports
"""
from __future__ import annotations

import json
import re
import winreg
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from urllib.parse import quote_plus

from . import colormath, ddc, gamma, icc
from .logs import log

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"


@dataclass
class Calibration:
    id: str
    title: str
    source: str
    confidence: str = "low"
    detail: str = ""
    score: int = 0
    auto: bool = True  # may the auto-calibrator pick it on its own?
    ramp: list[list[int]] | None = None  # GPU gamma ramp
    ddc: dict[int, int] = field(default_factory=dict)  # monitor VCP code -> value
    brightness: int | None = None  # built-in panel brightness (WMI)
    settings: dict = field(default_factory=dict)  # human-level knobs, for "Save as profile"

    def to_json(self) -> dict:
        data = asdict(self)
        data["ddc"] = {f"0x{code:02X}": value for code, value in self.ddc.items()}
        return data

    @classmethod
    def from_json(cls, data: dict) -> Calibration:
        data = dict(data)
        data["ddc"] = {int(code, 16): value for code, value in (data.get("ddc") or {}).items()}
        if data.get("confidence") == "—":  # placeholder used by the first version
            data["confidence"] = ""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def changes(self) -> str:
        """What this touches, in plain words: 'colors; monitor brightness, color mode'."""
        parts = []
        if self.ramp is not None:
            parts.append("colors")
        if self.ddc:
            parts.append("monitor " + ", ".join(ddc.LABELS.get(c, hex(c)) for c in self.ddc))
        if self.brightness is not None:
            parts.append("screen brightness")
        return "; ".join(parts) or "nothing"


def system_model() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as key:
            value, _ = winreg.QueryValueEx(key, "SystemProductName")
            return str(value).split("_")[0].strip()
    except OSError:
        return ""


def _model_tokens(display) -> list[str]:
    """Distinctive words to look for in ICC file names: PnP id and model numbers like 'u2720q'."""
    names = [display.name, display.edid.name if display.edid else ""]
    if display.internal:
        names.append(system_model())
    tokens = {display.pnp_id.lower()} if display.pnp_id else set()
    for name in names:
        for token in re.findall(r"[a-z0-9]+", name.lower()):
            if len(token) >= 4 and any(ch.isdigit() for ch in token):
                tokens.add(token)
    return sorted(tokens)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _matches(match: dict, display) -> bool:
    pnp, name, serial = match.get("pnp_id"), match.get("name"), match.get("serial")
    if not (pnp or name):
        return False
    if pnp and pnp.upper() != display.pnp_id.upper():
        return False
    if name and _normalize(name) not in _normalize(display.name):
        return False
    if serial and (not display.edid or str(serial) != display.edid.serial):
        return False
    return True


def _json_profiles():
    for path in sorted(PROFILE_DIR.glob("*.json")):
        if path.name.startswith("_"):  # examples / disabled
            continue
        try:
            yield path, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue


def _from_profile(path: Path, data: dict, state) -> Calibration | None:
    notes = []
    ramp = None
    gpu = data.get("gpu") or {}
    if data.get("icc"):
        try:
            ramp = icc.read(path.parent / data["icc"]).vcgt
        except (OSError, ValueError) as exc:
            notes.append(str(exc))
        if ramp is None:
            notes.append(f"{data['icc']} has no calibration curves")
    elif gpu:
        ramp = gamma.from_settings(gpu)

    values = {}
    for name, value in (data.get("ddc") or {}).items():
        try:
            code = ddc.code_for(name)
            values[code] = ddc.preset_value(value) if code == ddc.COLOR_PRESET else int(value)
        except ValueError as exc:
            notes.append(str(exc))
    unsupported = [ddc.NAMES.get(c, hex(c)) for c in values if c not in state.ddc_values]
    if unsupported:
        notes.append("monitor did not answer for " + ", ".join(unsupported) + ", skipped")
    ordered = {c: values[c] for c in ddc.APPLY_ORDER if c in values and c in state.ddc_values}
    brightness = data.get("laptop_brightness")

    if ramp is None and not ordered and brightness is None:
        return None
    detail = data.get("notes", "") or f"Saved profile {path.name}."
    if notes:
        detail += " (" + "; ".join(notes) + ")"
    return Calibration(
        id=f"profile:{path.name.lower()}",
        title=data.get("name") or path.stem,
        source=f"profiles/{path.name}",
        confidence="high",
        detail=detail,
        score=100,
        ramp=ramp,
        ddc=ordered,
        brightness=brightness,
        settings={k: data[k] for k in ("gpu", "ddc", "icc") if k in data},
    )


def _icc_candidates(display) -> list[Calibration]:
    default = icc.display_default_profile(display.adapter_luid, display.source_id)
    tokens = _model_tokens(display)
    results = []
    for folder, base_score, where in ((PROFILE_DIR, 85, "profiles folder"), (icc.COLOR_DIR, 75, "Windows color folder")):
        for path in sorted(folder.glob("*.ic[cm]")):
            associated = folder == icc.COLOR_DIR and default is not None and path.name.lower() == default.lower()
            try:
                profile = icc.read(path)
            except (OSError, ValueError):
                continue
            if not (associated or any(t in profile.searchable_text for t in tokens)):
                continue
            if profile.vcgt is None or gamma.is_identity(profile.vcgt):
                continue  # describes the screen for color-managed apps, but carries no calibration
            results.append(
                Calibration(
                    id=f"icc:{path.name.lower()}",
                    title=f"ICC profile “{profile.description}”",
                    source="Windows default for this display" if associated else where,
                    confidence="high" if associated else "medium",
                    detail=f"Loads the calibration curves (vcgt) from {path}. "
                    f"Curves: {gamma.describe(profile.vcgt)}. Measured profiles assume the monitor "
                    "is on the same on-screen settings it had when it was measured.",
                    score=90 if associated else base_score,
                    ramp=profile.vcgt,
                    settings={"icc": str(path)},
                )
            )
    return results


def _preset_candidates(state) -> list[Calibration]:
    caps = state.ddc_caps
    if caps is None or ddc.COLOR_PRESET not in state.ddc_values:
        return []  # can't read the current preset back, so we couldn't undo it
    options = (
        (ddc.SRGB, "Monitor's factory sRGB mode", 60,
         "Switches the monitor's own color mode to sRGB (D65 white, sRGB colors and gamma). It's usually the "
         "most accurate mode the factory tuned. Some monitors lock brightness in this mode."),
        (ddc.K6500, "Monitor's 6500 K (D65) preset", 50,
         "Switches the monitor's color temperature preset to 6500 K, the standard white for PCs and the web."),
    )
    return [
        Calibration(
            id=f"ddc-preset:{value}",
            title=title,
            source="monitor (DDC/CI)",
            confidence="medium",
            detail=detail,
            score=score,
            ddc={ddc.COLOR_PRESET: value},
            settings={"ddc": {"color_preset": ddc.PRESETS[value]}},
        )
        for value, title, score, detail in options
        if value in caps.presets()
    ]


def _edid_candidates(display) -> list[Calibration]:
    e = display.edid
    if not e or not all(0 < v < 1 for xy in (e.red, e.green, e.blue, e.white) for v in xy):
        return []
    try:
        gains = colormath.whitepoint_gains(e.red, e.green, e.blue, e.white)
    except (ValueError, ZeroDivisionError):
        return []
    exponent = gamma.DISPLAY_GAMMA / e.gamma if e.gamma else 1.0
    if min(gains) < 0.6 or not 0.8 < exponent < 1.25:
        return []  # implausible data, don't trust it
    if min(gains) > 0.985 and abs(exponent - 1) < 0.02:
        return []  # EDID says the panel is already standard
    native = colormath.xy_to_cct(*e.white)
    return [
        Calibration(
            id=f"edid:{display.pnp_id}",
            title=f"EDID correction: white {native:.0f} K → 6500 K, gamma {e.gamma or 2.2:.2f} → 2.20",
            source="monitor's EDID data",
            confidence="low",
            detail="Computed from the primaries / white point / gamma the monitor reports about itself. "
            "Those are often typical values for the model rather than measurements of your unit.",
            score=30,
            ramp=gamma.build(gains, (exponent,) * 3),
        )
    ]


def _reset_candidate(state) -> list[Calibration]:
    if state.ramp is None or gamma.is_identity(state.ramp):
        return []
    return [
        Calibration(
            id="reset-ramp",
            title="Reset the graphics card colors to neutral",
            source="built-in",
            confidence="",
            detail=f"The graphics card's colors are currently {gamma.describe(state.ramp)}. Something set that: "
            "Night Light, a color app from your laptop or graphics card maker, f.lux, or a game. "
            "This puts them back to neutral.",
            score=10,
            auto=False,
            ramp=gamma.identity(),
        )
    ]


def find(display, state) -> list[Calibration]:
    found = []
    for path, data in _json_profiles():
        try:
            if not isinstance(data, dict) or not _matches(data.get("match") or {}, display):
                continue
            calibration = _from_profile(path, data, state)
        except (ValueError, TypeError, AttributeError) as exc:  # a typo in a hand-written file
            log.warning("skipping %s: %s", path.name, exc)
            continue
        if calibration:
            found.append(calibration)
    found += _icc_candidates(display)
    found += _preset_candidates(state)
    found += _edid_candidates(display)
    found += _reset_candidate(state)
    found.sort(key=lambda c: -c.score)
    return found


def best(candidates: list[Calibration]) -> Calibration | None:
    return next((c for c in candidates if c.auto), None)


def from_icc_file(path: str) -> Calibration:
    profile = icc.read(Path(path))
    if profile.vcgt is None:
        raise ValueError(
            "This ICC profile has no calibration curves (vcgt). It only describes the screen for "
            "color-managed apps; install it from Windows' Color Management panel instead."
        )
    return Calibration(
        id=f"icc:{Path(path).name.lower()}",
        title=f"ICC profile “{profile.description}”",
        source="file you picked",
        confidence="medium",
        detail=f"Calibration curves from {path}: {gamma.describe(profile.vcgt)}.",
        score=0,
        auto=False,
        ramp=profile.vcgt,
        settings={"icc": str(path)},
    )


def save_profile(display, settings: dict) -> Path:
    """Write a profiles/<PnP id>.json the auto-calibrator will pick up next time."""
    PROFILE_DIR.mkdir(exist_ok=True)
    data = {
        "name": f"My calibration for {display.name}",
        "match": {"pnp_id": display.pnp_id},
        "notes": "Saved from the calibrator's manual adjustments.",
        **settings,
    }
    path = PROFILE_DIR / f"{display.pnp_id or display.key}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def search_urls(display) -> list[tuple[str, str]]:
    google = "https://www.google.com/search?q="
    name = display.edid.name if display.edid and display.edid.name else display.name
    if display.internal:
        model = system_model() or "laptop"
        return [
            (f"Notebookcheck review of {model} (often includes an ICC file)",
             google + quote_plus(f"notebookcheck {model} review")),
            (f"ICC profile for panel {display.pnp_id}", google + quote_plus(f'"{display.pnp_id}" icc profile')),
        ]
    return [
        (f"ICC profile for {name}", google + quote_plus(f'"{name}" icc profile download')),
        (f"Calibration settings for {name}", google + quote_plus(f"{name} calibration settings review")),
        (f"Manufacturer driver / ICC for {name}", google + quote_plus(f"{name} monitor driver icc")),
    ]
