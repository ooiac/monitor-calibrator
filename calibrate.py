"""Auto Monitor Calibrator.

    python calibrate.py                 open the window on the screen you're on (auto-calibrates it)
    python calibrate.py --list          show screens, what can be controlled, and calibrations found
    python calibrate.py --auto          calibrate the screen this console is on, then ask to keep
    python calibrate.py --restore all   undo the last change on every screen (--original: first backup)
    python calibrate.py --apply-saved   re-load kept calibrations (used by the sign-in shortcut)
"""
from __future__ import annotations

import sys

if sys.platform != "win32":
    sys.exit("Monitor Calibrator runs on Windows only.")
if sys.version_info < (3, 10):
    sys.exit("Monitor Calibrator needs Python 3.10 or newer.")

import argparse  # noqa: E402  (after the checks above, so other systems get a clear message)
import ctypes  # noqa: E402
import time  # noqa: E402

from calibrator import backup, confirm, ddc, displays, engine, gamma, profiles, winapi  # noqa: E402
from calibrator.logs import log, setup as setup_logging  # noqa: E402

MUTEX_NAME = "Local\\AutoMonitorCalibrator.SingleInstance"
ERROR_ALREADY_EXISTS = 183
_mutex = None  # held for the life of the process


def _target(all_displays, number):
    if number is None:
        return displays.detect_current(all_displays)
    match = next((d for d in all_displays if d.index == number), None)
    if match is None:
        sys.exit(f"No display number {number}; see --list.")
    return match


def _only_instance() -> bool:
    """One calibrator at a time, since two would fight over the same screens. Holds the mutex for our lifetime."""
    global _mutex
    _mutex = ctypes.WinDLL("kernel32", use_last_error=True).CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def _bring_existing_to_front() -> None:
    found = []

    @winapi.EnumWindowsProc
    def visit(hwnd, _):
        buffer = ctypes.create_unicode_buffer(256)
        winapi.user32.GetWindowTextW(hwnd, buffer, 256)
        if buffer.value.startswith("Monitor Calibrator") and winapi.user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    winapi.user32.EnumWindows(visit, 0)
    if found:
        winapi.user32.ShowWindow(found[0], 9)  # SW_RESTORE
        winapi.user32.SetForegroundWindow(found[0])


def cmd_list(all_displays):
    for d in all_displays:
        print(f"[{d.index}] {d.name}: {d.describe()}")
        state = engine.probe(d)
        print(f"    GPU ramp : {gamma.describe(state.ramp) if state.ramp else state.ramp_error}")
        if state.ddc_values:
            values = ", ".join(f"{ddc.NAMES[c]}={v[0]}/{v[1]}" for c, v in state.ddc_values.items())
            print(f"    DDC/CI   : {values}")
            if state.ddc_caps and state.ddc_caps.presets():
                print(f"    presets  : {', '.join(ddc.PRESETS[p] for p in state.ddc_caps.presets())}")
        else:
            print(f"    DDC/CI   : {state.ddc_error}")
        if state.brightness is not None:
            print(f"    panel    : brightness {state.brightness}%")
        if state.night_light:
            print("    note     : Night Light is ON (it fights GPU-ramp changes)")
        if d.hdr_enabled:
            print("    note     : HDR / auto color management is on")
        candidates = profiles.find(d, state)
        best = profiles.best(candidates)
        for cal in candidates:
            marker = "*" if cal is best else " "
            active = " (in effect)" if engine.is_active(state, cal) else ""
            where = f"{cal.source}, {cal.confidence} confidence" if cal.confidence else cal.source
            print(f"    {marker} {cal.title} [{where}]{active}")
        if not candidates:
            print("      no calibrations found")
        print()


def cmd_auto(display, timeout):
    for message in engine.recover_pending(displays.list_displays()):
        print(message)
    print(f"Display: {display.name} ({display.describe()})")
    state = engine.probe(display)
    plan = engine.plan_auto(display, state, profiles.find(display, state), on_launch=True)
    print(plan.message)
    if plan.action == "reapply":
        engine.apply_saved(display)
    elif plan.action == "apply":
        snapshot = engine.begin(display, plan.calibration)
        keep = confirm.ask_keep(display, timeout, summary=f"{plan.calibration.title}\nChanged: {plan.calibration.changes()}")
        problems = engine.finish(display, plan.calibration, snapshot, keep)
        print("Kept." if keep else "Reverted." + (f" Problems: {'; '.join(problems)}" if problems else ""))


def cmd_restore(all_displays, which, original):
    kind = "original" if original else "previous"
    if which == "all":
        targets = all_displays
    elif which == "current":
        targets = [_target(all_displays, None)]
    elif which.isdigit():
        targets = [_target(all_displays, int(which))]
    else:
        sys.exit("--restore takes 'all', 'current' or a display number from --list.")
    for message in engine.recover_pending(all_displays):
        print(message)
    for d in targets:
        try:
            problems = engine.undo(d, kind)
            print(f"{d.name}: restored the {kind} settings." + (f" Problems: {'; '.join(problems)}" if problems else ""))
        except RuntimeError as exc:
            print(f"{d.name}: {exc}")


def cmd_apply_saved(all_displays):
    for d in all_displays:
        try:
            message = engine.apply_saved(d)
        except gamma.GammaError as exc:
            message = f"failed: {exc}"
        log.info("apply-saved %s: %s", d.name, message or "nothing kept")
        print(f"{d.name}: {message or 'nothing kept yet'}")


def cmd_unlock_gamma_range():
    """Allow strong GPU-ramp changes (very warm / very cool). Needs admin; takes effect after a reboot."""
    params = (r'add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM" '
              r"/v GdiIcmGammaRange /t REG_DWORD /d 256 /f")
    winapi.shell32.ShellExecuteW(None, "runas", "reg.exe", params, None, 0)
    print("Asked Windows for permission to set GdiIcmGammaRange = 256. Reboot for it to take effect.")


def main(argv=None):
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Auto Monitor Calibrator")
    parser.add_argument("--list", action="store_true", help="show screens, controls and calibrations found")
    parser.add_argument("--auto", action="store_true", help="calibrate the screen this console is on")
    parser.add_argument("--restore", nargs="?", const="current", metavar="all|current|N", help="undo the last change")
    parser.add_argument("--original", action="store_true", help="with --restore: go back to the first backup ever")
    parser.add_argument("--apply-saved", action="store_true", help="re-load kept calibrations (Windows resets them)")
    parser.add_argument("--delay", type=int, default=0, metavar="S", help="wait S seconds first (sign-in shortcut)")
    parser.add_argument("--display", type=int, metavar="N", help="pick a screen by its --list number")
    parser.add_argument("--timeout", type=int, default=15, help="seconds before an unconfirmed change reverts")
    parser.add_argument("--no-auto", action="store_true", help="window: don't auto-calibrate on launch")
    parser.add_argument("--unlock-gamma-range", action="store_true", help="allow strong GPU tints (admin, reboot)")
    args = parser.parse_args(argv)

    setup_logging()
    winapi.make_dpi_aware()
    if args.unlock_gamma_range:
        return cmd_unlock_gamma_range()
    if args.delay > 0:
        time.sleep(min(args.delay, 120))  # let the desktop and displays settle after sign-in
    all_displays = displays.list_displays()
    if not all_displays:
        sys.exit("No displays found.")
    for message in backup.migrate_folders(all_displays):
        log.info(message)
    if args.list:
        return cmd_list(all_displays)

    # Emergency restore and the sign-in re-apply always run; they must work even if a window is stuck.
    if args.restore:
        return cmd_restore(all_displays, args.restore, args.original)
    if args.apply_saved:
        return cmd_apply_saved(all_displays)
    if not _only_instance():
        _bring_existing_to_front()
        sys.exit("The calibrator is already running, so I switched to it.")
    if args.auto:
        return cmd_auto(_target(all_displays, args.display), max(5, args.timeout))

    from calibrator import app

    log.info("window started")
    app.run(_target(all_displays, args.display), timeout=max(5, args.timeout),
            auto_on_launch=False if args.no_auto else None)


if __name__ == "__main__":
    main()
