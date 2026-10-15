"""Checks what Monitor Calibrator needs and installs what's missing.

Calibrate.bat runs this once, the first time (and finds or installs Python before that).
When setup finishes it saves "setup_done": true in the settings, so later launches skip it.

    python install.py              run the setup again
    python install.py --check      only check (exit code 1 if something needed is missing)
    python install.py --yes        install without asking
    python install.py --uninstall  remove the shortcuts this app made
"""
import sys

if sys.version_info < (3, 10):  # checked before anything else, so old Pythons get a clear message
    print(f"Monitor Calibrator needs Python 3.10 or newer, and this is {sys.version.split()[0]}.")
    print("Get a newer one from https://www.python.org/downloads/ or run: winget install Python.Python.3.12")
    sys.exit(1)
if sys.platform != "win32":
    print("Monitor Calibrator runs on Windows only.")
    sys.exit(1)

import argparse  # noqa: E402  (after the checks above)
import shutil  # noqa: E402

from calibrator import backup, requirements, startup  # noqa: E402


def ask(question: str, default: bool = True, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {hint} ").strip().lower()
    except EOFError:  # no keyboard (e.g. piped): take the default
        return default
    return default if not answer else answer in ("y", "yes")


def report(items) -> None:
    width = max(len(item.name) for item in items)
    for item in items:
        if item.ok:
            status = "ok"
        else:
            status = "MISSING" if item.required else "missing"
        print(f"  {status:<9}{item.name:<{width + 3}}{item.detail}")


def mark_done() -> None:
    """Calibrate.bat skips setup from now on. Missing extras are offered by the app when they're needed."""
    settings = backup.load_settings()
    settings["setup_done"] = True
    backup.save_settings(settings)


def setup(assume_yes: bool, check_only: bool, first_run: bool = False) -> int:
    print("Monitor Calibrator setup" + (" (first run only)" if first_run else "") + "\n")
    items = requirements.scan()
    report(items)
    blocking = [item for item in items if item.required and not item.ok]
    if blocking:
        print("\nSomething it needs is missing (in capitals above). Fix that first, then run this again.")
        return 1
    specs = requirements.missing_packages(items)
    if check_only:
        print("\nEverything it needs is here." + (" Some extras are missing." if specs else ""))
        return 0
    failed = False
    if specs:
        has_pip = next(item.ok for item in items if item.name.startswith("pip"))
        if not has_pip:
            print("\npip is missing, so the extras can't be installed. Run: python -m ensurepip --upgrade")
            failed = True
        elif ask(f"\nInstall the missing extras now? ({', '.join(specs)})", True, assume_yes):
            print("Installing. This can take a minute…")
            worked, output = requirements.install(specs)
            print(output)
            print("Installed." if worked else "That didn't work. The app still runs; only the phone camera needs these.")
            failed = not worked
    else:
        print("\nEverything is in place.")
    if not startup.has_menu_shortcut():
        if ask("\nAdd Monitor Calibrator to the Start menu?", True, assume_yes):
            try:
                startup.add_menu_shortcut()
                print("Added. Look for \"Monitor Calibrator\" in the Start menu.")
            except Exception as exc:  # shortcuts are a convenience; never fail setup over one
                print(f"Couldn't add it ({exc}). You can always start it with Calibrate.bat.")
    mark_done()
    if first_run:
        print("\nSetup is done and won't run again. Starting Monitor Calibrator…")
        if failed and not assume_yes:
            ask("Press Enter to continue.", True)  # leave the error on screen until it's been read
    else:
        print("\nDone. Start it with Calibrate.bat" + (" or from the Start menu." if startup.has_menu_shortcut() else "."))
    return 0


def uninstall() -> int:
    removed = startup.remove_shortcuts()
    print("Removed: " + ", ".join(removed) if removed else "No shortcuts to remove.")
    if backup.DATA_DIR.exists():
        print(f"\nYour backups and settings are in {backup.DATA_DIR}.")
        print("To put your screens back first, use History & safety > Restore the original settings.")
        if ask("Delete them now?", False):  # never assumed: it's the only way back to the original settings
            shutil.rmtree(backup.DATA_DIR, ignore_errors=True)
            print("Deleted.")
    print("\nThe extras stay installed. Remove them with: python -m pip uninstall pillow qrcode")
    print("Then delete this folder to remove the app.")
    return 0


def main(argv=None) -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Check and install what Monitor Calibrator needs.")
    parser.add_argument("--check", action="store_true", help="only check, change nothing")
    parser.add_argument("--yes", action="store_true", help="install without asking")
    parser.add_argument("--uninstall", action="store_true", help="remove the shortcuts this app made")
    parser.add_argument("--first-run", action="store_true", help=argparse.SUPPRESS)  # used by Calibrate.bat
    args = parser.parse_args(argv)
    if args.uninstall:
        return uninstall()
    return setup(args.yes, args.check, args.first_run)


if __name__ == "__main__":
    sys.exit(main())
