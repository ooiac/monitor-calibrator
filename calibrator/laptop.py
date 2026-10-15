"""Built-in panel brightness through WMI (laptops don't speak DDC/CI)."""
from __future__ import annotations

import subprocess

CREATE_NO_WINDOW = 0x08000000


def _powershell(script: str) -> str:
    """Run a short PowerShell snippet; '' if it fails or hangs (WMI can stall after sleep)."""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip()


def _instance_prefix(device_path: str) -> str:
    r"""\\?\DISPLAY#BOE0900#4&1a2b3c4d&0&UID265988#{guid} -> DISPLAY\BOE0900\4&1a2b3c4d&0&UID265988"""
    parts = device_path.split("#")
    if len(parts) < 3:
        return ""
    return "\\".join(("DISPLAY", parts[1], parts[2])).replace("'", "")


def get_brightness(device_path: str) -> int | None:
    prefix = _instance_prefix(device_path).lower()
    if not prefix:
        return None
    output = _powershell(
        "Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness -ErrorAction SilentlyContinue"
        " | ForEach-Object { $_.InstanceName + '|' + $_.CurrentBrightness }"
    )
    for line in output.splitlines():
        name, _, value = line.rpartition("|")
        if name.lower().startswith(prefix) and value.strip().isdigit():
            return int(value)
    return None


def set_brightness(device_path: str, value: int) -> bool:
    prefix = _instance_prefix(device_path)
    if not prefix:
        return False
    value = max(0, min(100, int(value)))
    output = _powershell(
        "$m = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods"
        f" | Where-Object {{ $_.InstanceName.StartsWith('{prefix}', 'OrdinalIgnoreCase') }};"
        " if ($m) { Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness"
        f" -Arguments @{{Timeout=[uint32]0; Brightness=[byte]{value}}} | Out-Null; 'ok' }}"
    )
    return output.endswith("ok")
