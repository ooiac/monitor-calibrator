"""ctypes declarations for the Win32 APIs the calibrator uses.

Kept free of logic so the other modules read like Python, not like C.
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
try:
    mscms = ctypes.WinDLL("mscms", use_last_error=True)
except OSError:
    mscms = None


def make_dpi_aware():
    """Use real pixel coordinates so monitor sizes and window positions are not virtualized."""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per-monitor v2
            return
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        user32.SetProcessDPIAware()


# --- monitors (multi-monitor API) -------------------------------------------

MONITOR_DEFAULTTONEAREST = 2
MONITORINFOF_PRIMARY = 1


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


MonitorEnumProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)

user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MonitorEnumProc, wintypes.LPARAM]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFOEXW)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND

# Dark title bar on Windows 10 20H1+ / 11 (DWMWA_USE_IMMERSIVE_DARK_MODE).
try:
    dwmapi = ctypes.WinDLL("dwmapi")
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
except OSError:
    dwmapi = None


# --- display configuration (connection type, EDID path, HDR state) ----------

QDC_ONLY_ACTIVE_PATHS = 0x2
ERROR_INSUFFICIENT_BUFFER = 122
DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1
DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2
DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO = 9


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DISPLAYCONFIG_RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", ctypes.c_uint32), ("Denominator", ctypes.c_uint32)]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
        ("modeInfoIdx", ctypes.c_uint32),
        ("statusFlags", ctypes.c_uint32),
    ]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
        ("modeInfoIdx", ctypes.c_uint32),
        ("outputTechnology", ctypes.c_uint32),
        ("rotation", ctypes.c_uint32),
        ("scaling", ctypes.c_uint32),
        ("refreshRate", DISPLAYCONFIG_RATIONAL),
        ("scanLineOrdering", ctypes.c_uint32),
        ("targetAvailable", wintypes.BOOL),
        ("statusFlags", ctypes.c_uint32),
    ]


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
        ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
        ("flags", ctypes.c_uint32),
    ]


class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
    # The trailing union (target/source/desktop mode) is 48 bytes, 8-byte aligned.
    _fields_ = [
        ("infoType", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
        ("adapterId", LUID),
        ("data", ctypes.c_uint64 * 6),
    ]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
    ]


class DISPLAYCONFIG_SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("viewGdiDeviceName", wintypes.WCHAR * 32),
    ]


class DISPLAYCONFIG_TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("flags", ctypes.c_uint32),
        ("outputTechnology", ctypes.c_uint32),
        ("edidManufactureId", ctypes.c_uint16),
        ("edidProductCodeId", ctypes.c_uint16),
        ("connectorInstance", ctypes.c_uint32),
        ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
        ("monitorDevicePath", wintypes.WCHAR * 128),
    ]


class DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("value", ctypes.c_uint32),  # bit 0 supported, bit 1 enabled
        ("colorEncoding", ctypes.c_uint32),
        ("bitsPerColorChannel", ctypes.c_uint32),
    ]


user32.GetDisplayConfigBufferSizes.argtypes = [
    ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)
]
user32.GetDisplayConfigBufferSizes.restype = wintypes.LONG
user32.QueryDisplayConfig.argtypes = [
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(DISPLAYCONFIG_PATH_INFO),
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(DISPLAYCONFIG_MODE_INFO),
    ctypes.c_void_p,
]
user32.QueryDisplayConfig.restype = wintypes.LONG
user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
user32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG


# --- GPU gamma ramp (what Night Light / f.lux use) ---------------------------

GammaRamp = (wintypes.WORD * 256) * 3

gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
gdi32.CreateDCW.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.GetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.GetDeviceGammaRamp.restype = wintypes.BOOL
gdi32.SetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.SetDeviceGammaRamp.restype = wintypes.BOOL


# --- DDC/CI (the monitor's own on-screen-menu settings) ----------------------


class PHYSICAL_MONITOR(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("hPhysicalMonitor", wintypes.HANDLE),
        ("szPhysicalMonitorDescription", wintypes.WCHAR * 128),
    ]


dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
dxva2.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
dxva2.DestroyPhysicalMonitors.argtypes = [wintypes.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
dxva2.DestroyPhysicalMonitors.restype = wintypes.BOOL
dxva2.GetVCPFeatureAndVCPFeatureReply.argtypes = [
    wintypes.HANDLE, ctypes.c_ubyte, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)
]
dxva2.GetVCPFeatureAndVCPFeatureReply.restype = wintypes.BOOL
dxva2.SetVCPFeature.argtypes = [wintypes.HANDLE, ctypes.c_ubyte, wintypes.DWORD]
dxva2.SetVCPFeature.restype = wintypes.BOOL
dxva2.GetCapabilitiesStringLength.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
dxva2.GetCapabilitiesStringLength.restype = wintypes.BOOL
dxva2.CapabilitiesRequestAndCapabilitiesReply.argtypes = [wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD]
dxva2.CapabilitiesRequestAndCapabilitiesReply.restype = wintypes.BOOL


# --- Windows color management (which ICC profile is set for a display) -------

kernel32.LocalFree.argtypes = [ctypes.c_void_p]
kernel32.LocalFree.restype = ctypes.c_void_p

ColorProfileGetDisplayDefault = None
if mscms is not None:
    try:
        ColorProfileGetDisplayDefault = mscms.ColorProfileGetDisplayDefault
        ColorProfileGetDisplayDefault.argtypes = [
            ctypes.c_int, LUID, ctypes.c_uint32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
        ]
        ColorProfileGetDisplayDefault.restype = ctypes.c_long
    except AttributeError:  # older than Windows 10 2004
        ColorProfileGetDisplayDefault = None

shell32.ShellExecuteW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int
]
shell32.ShellExecuteW.restype = ctypes.c_void_p
