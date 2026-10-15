"""Read a display's state, apply calibrations safely, and undo them."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from . import backup, ddc, gamma, laptop, nightlight, profiles
from .displays import Display, list_displays
from .profiles import Calibration


@dataclass
class DisplayState:
    display: Display
    ramp: list[list[int]] | None = None
    ramp_error: str | None = None
    ddc_caps: ddc.Capabilities | None = None
    ddc_values: dict[int, tuple[int, int]] = field(default_factory=dict)  # code -> (current, max)
    ddc_error: str | None = None
    brightness: int | None = None  # built-in panel, via WMI
    night_light: bool | None = None

    def ddc_current(self, code: int) -> int | None:
        value = self.ddc_values.get(code)
        return value[0] if value else None


def _capabilities(display: Display, monitor: ddc.Monitor) -> ddc.Capabilities | None:
    # Asking for the capability string takes seconds on many monitors, and it never changes.
    cache = backup.display_dir(display.key) / "capabilities.txt"
    try:
        caps = ddc.parse_capabilities(cache.read_text(encoding="ascii"))
        if caps.vcp:
            return caps
    except OSError:
        pass
    try:
        caps = monitor.capabilities()
    except ddc.DdcError:
        return None
    cache.write_text(caps.raw, encoding="ascii")
    return caps


def _probe_ddc(display: Display, state: DisplayState) -> None:
    try:
        with ddc.open_monitor(display.hmonitor) as monitor:
            if display.internal:
                # Built-in panels practically never answer; one quick try avoids long timeouts.
                try:
                    state.ddc_values[ddc.BRIGHTNESS] = monitor.get(ddc.BRIGHTNESS, retries=1)
                except ddc.DdcError:
                    state.ddc_error = "built-in panels don't support DDC/CI"
                    return
            caps = state.ddc_caps = _capabilities(display, monitor)
            for code in ddc.MANAGED:
                listed = caps is None or caps.supports(code)
                # Black levels are often left out of a monitor's capability list but still work,
                # so they get one quick try anyway.
                if code in state.ddc_values or not (listed or code in ddc.BLACK_LEVELS):
                    continue
                try:
                    state.ddc_values[code] = monitor.get(code, retries=3 if (caps and listed) else 1)
                except ddc.DdcError:
                    if caps is None and not state.ddc_values:
                        break  # nothing answers: stop before every code times out
    except ddc.DdcError as exc:
        state.ddc_error = str(exc)
        return
    if not state.ddc_values:
        state.ddc_error = "the monitor did not answer DDC/CI (turn on “DDC/CI” in its on-screen menu)"


def probe(display: Display) -> DisplayState:
    state = DisplayState(display)
    try:
        state.ramp = gamma.read(display.gdi_name)
    except gamma.GammaError as exc:
        state.ramp_error = str(exc)
    _probe_ddc(display, state)
    if display.internal:
        state.brightness = laptop.get_brightness(display.device_path)
    state.night_light = nightlight.is_enabled()
    return state


def snapshot_of(state: DisplayState) -> backup.Snapshot:
    return backup.Snapshot(
        display_key=state.display.key,
        display_name=state.display.name,
        ramp=state.ramp,
        ddc={code: value[0] for code, value in state.ddc_values.items()},
        brightness=state.brightness,
    )


def apply(display: Display, calibration: Calibration) -> None:
    if calibration.ddc:
        with ddc.open_monitor(display.hmonitor) as monitor:
            for code in ddc.APPLY_ORDER:
                if code in calibration.ddc:
                    monitor.set(code, calibration.ddc[code])
    if calibration.brightness is not None and not laptop.set_brightness(display.device_path, calibration.brightness):
        raise RuntimeError("Windows refused the panel brightness change")
    if calibration.ramp is not None:
        gamma.write(display.gdi_name, calibration.ramp)


def restore(display: Display, snapshot: backup.Snapshot) -> list[str]:
    """Put a snapshot back. Returns a list of problems (empty = fully restored)."""
    problems = []
    if snapshot.ddc:
        try:
            with ddc.open_monitor(display.hmonitor) as monitor:
                for code in ddc.RESTORE_ORDER:
                    if code not in snapshot.ddc:
                        continue
                    try:
                        current, _ = monitor.get(code)
                    except ddc.DdcError:
                        current = None  # couldn't read: write it anyway, restoring is the safe direction
                    try:
                        if current != snapshot.ddc[code]:
                            monitor.set(code, snapshot.ddc[code])
                    except ddc.DdcError as exc:
                        problems.append(str(exc))
        except ddc.DdcError as exc:
            problems.append(str(exc))
    if snapshot.brightness is not None:
        if laptop.get_brightness(display.device_path) != snapshot.brightness:
            if not laptop.set_brightness(display.device_path, snapshot.brightness):
                problems.append("could not restore the panel brightness")
    if snapshot.ramp is not None:
        try:
            gamma.write(display.gdi_name, snapshot.ramp)
        except gamma.GammaError:
            try:
                gamma.write(display.gdi_name, gamma.identity())
                problems.append("the old gamma ramp was refused, so it was reset to neutral instead")
            except gamma.GammaError as exc:
                problems.append(str(exc))
    return problems


def begin(display: Display, calibration: Calibration) -> backup.Snapshot:
    """Back up, mark the change as pending, apply. Always follow with finish()."""
    state = probe(display)
    unreadable = [ddc.NAMES.get(c, hex(c)) for c in calibration.ddc if c not in state.ddc_values]
    if unreadable:
        raise RuntimeError(f"can't read the current {', '.join(unreadable)}, so it couldn't be undone. Not applying it.")
    if calibration.ramp is not None and state.ramp is None:
        raise RuntimeError(f"can't read the current gamma ramp ({state.ramp_error}), so it couldn't be undone")
    if calibration.brightness is not None and state.brightness is None:
        raise RuntimeError("can't read the current panel brightness, so it couldn't be undone")

    snapshot = snapshot_of(state)
    if backup.save_original_if_missing(snapshot):
        backup.log(display.key, "saved the original settings")
    backup.save(snapshot, "previous")
    backup.set_pending(snapshot)
    backup.log(display.key, f"applying “{calibration.title}” ({calibration.changes()})")
    try:
        apply(display, calibration)
    except Exception:
        restore(display, snapshot)
        backup.clear_pending(display.key)
        backup.log(display.key, "applying failed, restored the previous settings")
        raise
    return snapshot


def finish(display: Display, calibration: Calibration, snapshot: backup.Snapshot, keep: bool) -> list[str]:
    problems = []
    if keep:
        backup.save_confirmed(display.key, calibration)
        backup.set_declined(display.key, calibration.id, False)
        backup.log(display.key, f"kept “{calibration.title}”")
    else:
        problems = restore(display, snapshot)
        backup.set_declined(display.key, calibration.id, True)
        backup.log(display.key, "reverted" + (f" (problems: {'; '.join(problems)})" if problems else ""))
    backup.clear_pending(display.key)
    return problems


def undo(display: Display, kind: str = "previous") -> list[str]:
    """Restore the 'previous' (before last change) or 'original' (first ever) snapshot."""
    snapshot = backup.load(display.key, kind)
    if snapshot is None:
        raise RuntimeError(f"there is no {kind} backup for {display.name} yet")
    problems = restore(display, snapshot)
    backup.clear_confirmed(display.key)
    backup.log(display.key, f"restored the {kind} settings from {snapshot.taken_at}")
    return problems


def recover_pending(displays: list[Display]) -> list[str]:
    """Undo changes a previous run applied but never got a keep/revert answer for."""
    messages = []
    by_key = {d.key: d for d in displays}
    for snapshot in backup.pending_snapshots():
        display = by_key.get(snapshot.display_key)
        if display is None:
            messages.append(
                f"An unconfirmed change on {snapshot.display_name} is still pending. Connect it and start again to undo it."
            )
            continue
        problems = restore(display, snapshot)
        backup.clear_pending(display.key)
        backup.log(display.key, "undid an unconfirmed change left by a previous run")
        messages.append(
            f"Undid an unconfirmed change on {display.name} left by a previous run."
            + (f" Problems: {'; '.join(problems)}" if problems else "")
        )
    return messages


def is_active(state: DisplayState, calibration: Calibration) -> bool:
    if calibration.ramp is not None and (state.ramp is None or not gamma.close(state.ramp, calibration.ramp)):
        return False
    return _color_ddc_matches(state, calibration)


def _color_ddc_matches(state: DisplayState, calibration: Calibration) -> bool:
    return all(state.ddc_current(c) == v for c, v in calibration.ddc.items() if c in ddc.COLOR_CODES)


@dataclass
class AutoPlan:
    action: str  # "apply" (ask to keep), "reapply" (already confirmed once), "none"
    calibration: Calibration | None
    message: str


def plan_auto(display: Display, state: DisplayState, candidates: list[Calibration], on_launch: bool) -> AutoPlan:
    """Decide what auto-calibration should do, without nagging on every launch."""
    best = profiles.best(candidates)
    confirmed = backup.load_confirmed(display.key)
    if confirmed and on_launch:
        if is_active(state, confirmed):
            message = f"Already calibrated with “{confirmed.title}”."
            if best and best.id != confirmed.id and best.score > confirmed.score:
                message += f" A better match is available: “{best.title}”."
            return AutoPlan("none", None, message)
        if confirmed.ramp is not None and _color_ddc_matches(state, confirmed):
            return AutoPlan("reapply", confirmed, f"Put “{confirmed.title}” back. Windows had reset the colors.")
        return AutoPlan(
            "none", None,
            f"The monitor's color settings changed since you kept “{confirmed.title}”, so I left them alone. "
            "Press Calibrate to redo it.",
        )
    if best is None:
        return AutoPlan(
            "none", None,
            "No calibration found for this screen. Search online, put an ICC file in the profiles folder, "
            "or adjust it by eye and use Save for this monitor.",
        )
    if is_active(state, best):
        return AutoPlan("none", None, f"“{best.title}” is already on.")
    if on_launch and best.id in backup.declined(display.key):
        return AutoPlan(
            "none", None,
            f"You went back from “{best.title}” before, so I won't apply it by myself. Press Calibrate to try again.",
        )
    return AutoPlan("apply", best, f"Best match: “{best.title}”.")


def apply_saved(display: Display) -> str | None:
    """Re-load a kept calibration's GPU ramp (Windows drops it on reboot / sleep / reconnect)."""
    confirmed = backup.load_confirmed(display.key)
    if confirmed is None or confirmed.ramp is None:
        return None
    gamma.write(display.gdi_name, confirmed.ramp)
    return f"re-loaded “{confirmed.title}”"


def guard_kept() -> list[str]:
    """Windows resets GPU color curves to neutral after sleep, sign-in or when Night Light switches off.

    Put kept calibrations back, but only when the curve is plain neutral, so Night Light,
    games or other color apps that set their own curve are never overridden.
    """
    messages = []
    for display in list_displays():
        confirmed = backup.load_confirmed(display.key)
        if confirmed is None or confirmed.ramp is None or gamma.is_identity(confirmed.ramp):
            continue
        try:
            if gamma.is_identity(gamma.read(display.gdi_name)):
                gamma.write(display.gdi_name, confirmed.ramp)
                messages.append(f"Windows reset the colors of {display.name}; put “{confirmed.title}” back.")
        except gamma.GammaError:
            continue
    return messages


def tuned_calibration(state: DisplayState, gpu: dict | None, gpu_start: dict | None, monitor: dict[int, int],
                      panel_brightness: int | None, title: str) -> Calibration | None:
    """Describe what a live session (Match screens) left on a screen, counting only what changed.

    gpu/gpu_start: the color settings now and when the session began. monitor: VCP code -> value now.
    """
    ramp = gamma.from_settings(gpu) if gpu is not None and gpu != gpu_start else None
    changed = {code: value for code, value in monitor.items() if state.ddc_current(code) != value}
    ordered = {code: changed[code] for code in ddc.APPLY_ORDER if code in changed}
    brightness = panel_brightness if panel_brightness is not None and panel_brightness != state.brightness else None
    if ramp is None and not ordered and brightness is None:
        return None
    fingerprint = hashlib.sha1(json.dumps([gpu if ramp else None, ordered, brightness]).encode()).hexdigest()[:10]
    return Calibration(
        id=f"tuned:{fingerprint}", title=title, source="matched by eye", confidence="", auto=False,
        ramp=ramp, ddc=ordered, brightness=brightness, settings={"gpu": gpu} if ramp else {},
    )
