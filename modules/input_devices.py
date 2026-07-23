"""
input_devices.py

Covers planning doc section 5 (INPUT DEVICES - MOUSE / KEYBOARD).

What this checks, and how:
  - Mouse/keyboard detected + driver health   -> WMI: Win32_PointingDevice, Win32_Keyboard
  - Multiple pointing devices (touchpad +
    mouse both active, a common conflict
    source)                                    -> count of Win32_PointingDevice entries
  - Sticky Keys / Filter Keys / Toggle Keys
    accidentally enabled mid-exam              -> registry: Control Panel\\Accessibility
  - Interactive click/keypress confirmation    -> two SEPARATE live tests, since a single
                                                   "press Enter" prompt can't actually tell
                                                   the two devices apart (see note below)

Why mouse and keyboard get two separate interactive tests, not one:
An early version asked the person to click, then press Enter -- but Enter
is a keyboard action, so a broken mouse would silently pass (nothing was
actually testing it) and a broken keyboard would make the tool hang
forever waiting for a keypress that could never come. Fixed by testing
each device through a mechanism the other can't fake: the mouse test
opens a small window with a button that can only be meaningfully
confirmed by an actual click, and the keyboard test waits for any raw
keypress (not specifically Enter). Both auto-timeout, so a genuinely
broken device produces a clear FAIL instead of hanging the tool -- which
matters a lot for something meant to be useful in a live exam room.

Known gaps (candidates for planning doc section 11):
  - Battery level for wireless mice/keyboards isn't checked. Windows
    doesn't expose peripheral battery percentage through a simple,
    generically-scriptable WMI class the way it does for a laptop's main
    battery (Win32_Battery) -- reading it reliably needs per-device HID
    battery report parsing, which is a bigger piece of work than this
    pass covers. Driver/connection status is checked instead, which
    catches "dongle unplugged" but not "connected but about to die."
"""

from __future__ import annotations

import sys

from .powershell_bridge import run_ps_json, PowerShellError
from .report import CategoryReport, CheckResult, Status


def _simulated_data() -> dict:
    """Mock data so the tool can be demoed/tested off a Windows exam laptop."""
    return {
        "pointing_devices": [
            {"Name": "HID-compliant mouse", "Status": "OK", "ConfigManagerErrorCode": 0},
        ],
        "keyboards": [
            {"Name": "HID Keyboard Device", "Status": "OK", "ConfigManagerErrorCode": 0},
        ],
        "sticky_keys_on": False,
        "filter_keys_on": False,
        "toggle_keys_on": False,
    }


def _query() -> dict:
    script = r"""
$mice = Get-CimInstance Win32_PointingDevice -ErrorAction SilentlyContinue |
    Select-Object Name, Status, ConfigManagerErrorCode
$keyboards = Get-CimInstance Win32_Keyboard -ErrorAction SilentlyContinue |
    Select-Object Name, Status, ConfigManagerErrorCode

function Get-AccessibilityFlag($path) {
    try {
        $val = (Get-ItemProperty -Path $path -Name Flags -ErrorAction Stop).Flags
        return ([int]$val -band 1) -eq 1
    } catch {
        return $null
    }
}

$stickyKeys = Get-AccessibilityFlag 'HKCU:\Control Panel\Accessibility\StickyKeys'
$filterKeys = Get-AccessibilityFlag 'HKCU:\Control Panel\Accessibility\Keyboard Response'
$toggleKeys = Get-AccessibilityFlag 'HKCU:\Control Panel\Accessibility\ToggleKeys'

$result = [PSCustomObject]@{
    PointingDevices = $mice
    Keyboards       = $keyboards
    StickyKeysOn    = $stickyKeys
    FilterKeysOn    = $filterKeys
    ToggleKeysOn    = $toggleKeys
}
$result | ConvertTo-Json -Depth 4
""".strip()

    result = run_ps_json(script, timeout=10)
    if not result.success:
        raise PowerShellError(f"Input device query failed: {result.raw_stderr}")

    row = result.data[0] if result.data else {}
    mice = row.get("PointingDevices") or []
    if isinstance(mice, dict):
        mice = [mice]
    keyboards = row.get("Keyboards") or []
    if isinstance(keyboards, dict):
        keyboards = [keyboards]

    return {
        "pointing_devices": mice,
        "keyboards": keyboards,
        "sticky_keys_on": row.get("StickyKeysOn"),
        "filter_keys_on": row.get("FilterKeysOn"),
        "toggle_keys_on": row.get("ToggleKeysOn"),
    }


def _mouse_click_test(timeout_sec: int = 15):
    """
    A real, mouse-only test: a small window with a button that can only be
    confirmed by an actual click (or keyboard Tab+Enter navigation, which
    is a rare enough edge case to accept). Auto-closes after a timeout so
    a broken mouse can't hang the tool waiting for a click that never
    comes. Returns True/False/None (None = tkinter unavailable, e.g. a
    stripped-down Windows install without a GUI toolkit).
    """
    try:
        import tkinter as tk
    except ImportError:
        return None

    clicked = {"value": False}

    root = tk.Tk()
    root.title("Mouse check")
    root.geometry("340x160")
    root.attributes("-topmost", True)
    tk.Label(root, text="Click the button below to confirm the mouse works.\n"
                          f"This closes automatically in {timeout_sec}s.",
             pady=10).pack()

    def on_click():
        clicked["value"] = True
        root.destroy()

    tk.Button(root, text="Click me", command=on_click, width=20, height=3).pack(pady=10)
    root.after(timeout_sec * 1000, root.destroy)
    root.mainloop()

    return clicked["value"]


def _keyboard_test(timeout_sec: int = 15):
    """
    A real, keyboard-only test: waits for any single keypress (not
    specifically Enter) with a hard timeout, so a broken keyboard can't
    hang the tool forever waiting for input that will never arrive.
    Windows-only (msvcrt), which is fine since this whole tool is
    Windows-only anyway. Returns True/False.
    """
    import msvcrt
    import time

    print(f"  Press any key within {timeout_sec} seconds to confirm the keyboard works...")
    start = time.time()
    while time.time() - start < timeout_sec:
        if msvcrt.kbhit():
            msvcrt.getch()
            return True
        time.sleep(0.1)
    return False


def _interactive_input_test(report: CategoryReport) -> None:
    """
    Genuinely separate mouse and keyboard confirmation, each of which can
    fail independently and neither of which can hang the tool -- both
    have a hard timeout, and the two devices are tested through entirely
    different mechanisms (a clickable GUI button for the mouse, raw
    keypress detection for the keyboard) rather than one Enter-press
    standing in for both.
    """
    if not sys.stdin.isatty():
        report.add(CheckResult(
            name="Interactive input confirmation",
            status=Status.SKIPPED,
            summary="Skipped -- not running in an interactive terminal.",
        ))
        return

    print("\n  --- Quick input check ---")
    mouse_ok = _mouse_click_test()

    if mouse_ok is None:
        report.add(CheckResult(
            name="Mouse click confirmation",
            status=Status.SKIPPED,
            summary="Couldn't run the mouse click test (GUI toolkit unavailable on "
                     "this Windows install).",
        ))
    elif mouse_ok:
        report.add(CheckResult(
            name="Mouse click confirmation",
            status=Status.PASS,
            summary="Mouse click confirmed working.",
        ))
    else:
        report.add(CheckResult(
            name="Mouse click confirmation",
            status=Status.FAIL,
            summary="No mouse click was registered within the time limit.",
            recommendation="Check the mouse connection/battery, or try a different "
                            "USB port, then re-run this check.",
        ))

    keyboard_ok = _keyboard_test()
    if keyboard_ok:
        report.add(CheckResult(
            name="Keyboard confirmation",
            status=Status.PASS,
            summary="Keyboard input confirmed working.",
        ))
    else:
        report.add(CheckResult(
            name="Keyboard confirmation",
            status=Status.FAIL,
            summary="No key press was detected within the time limit.",
            recommendation="Check the keyboard connection/battery, or try a different "
                            "USB port, then re-run this check.",
        ))


def run(simulate: bool = False, interactive: bool = True) -> CategoryReport:
    report = CategoryReport(category="Input Devices")

    if simulate:
        data = _simulated_data()
    else:
        try:
            data = _query()
        except PowerShellError as exc:
            report.add(CheckResult(
                name="Input device query",
                status=Status.FAIL,
                summary="Couldn't read mouse/keyboard information from Windows at all.",
                detail=str(exc),
                recommendation="Try running the tool again.",
            ))
            return report

    # --- Mouse / pointing device detection ---------------------------------------
    mice = data.get("pointing_devices", [])
    if not mice:
        report.add(CheckResult(
            name="Mouse detected",
            status=Status.FAIL,
            summary="No mouse or pointing device was found at all.",
            recommendation="Check the USB connection, or replace the wireless dongle/"
                            "battery if it's a wireless mouse.",
        ))
    else:
        names = ", ".join(m.get("Name", "Unknown device") for m in mice)
        report.add(CheckResult(
            name="Pointing devices found",
            status=Status.INFO,
            summary=f"Found {len(mice)} pointing device(s): {names}.",
        ))
        if len(mice) > 1:
            report.add(CheckResult(
                name="Multiple pointing devices",
                status=Status.WARNING,
                summary=f"More than one pointing device is active ({len(mice)}) -- "
                         "commonly a laptop touchpad plus an external mouse both "
                         "enabled at once.",
                recommendation="If the cursor behaves unpredictably, consider disabling "
                                "the touchpad in Windows settings while using an "
                                "external mouse.",
            ))
        for m in mice:
            name = m.get("Name", "Unknown device")
            err_code = m.get("ConfigManagerErrorCode", 0)
            if err_code and err_code != 0:
                report.add(CheckResult(
                    name=f"Driver status: {name}",
                    status=Status.FAIL,
                    summary=f"'{name}' has a driver problem.",
                    detail=f"ConfigManagerErrorCode={err_code}",
                    recommendation="Reconnect the device or update its driver in "
                                    "Device Manager.",
                ))
            else:
                report.add(CheckResult(
                    name=f"Driver status: {name}",
                    status=Status.PASS,
                    summary=f"'{name}' driver is working normally.",
                ))

    # --- Keyboard detection ----------------------------------------------------------
    keyboards = data.get("keyboards", [])
    if not keyboards:
        report.add(CheckResult(
            name="Keyboard detected",
            status=Status.FAIL,
            summary="No keyboard was found at all.",
            recommendation="Check the USB connection, or replace the wireless dongle/"
                            "battery if it's a wireless keyboard.",
        ))
    else:
        for k in keyboards:
            name = k.get("Name", "Unknown keyboard")
            err_code = k.get("ConfigManagerErrorCode", 0)
            if err_code and err_code != 0:
                report.add(CheckResult(
                    name=f"Driver status: {name}",
                    status=Status.FAIL,
                    summary=f"'{name}' has a driver problem.",
                    detail=f"ConfigManagerErrorCode={err_code}",
                    recommendation="Reconnect the device or update its driver in "
                                    "Device Manager.",
                ))
            else:
                report.add(CheckResult(
                    name=f"Driver status: {name}",
                    status=Status.PASS,
                    summary=f"'{name}' driver is working normally.",
                ))

    # --- Accessibility features accidentally enabled --------------------------------
    accessibility_checks = [
        ("sticky_keys_on", "Sticky Keys", "Sticky Keys is currently turned on -- keyboard "
         "shortcuts using Shift/Ctrl/Alt will behave differently than expected."),
        ("filter_keys_on", "Filter Keys", "Filter Keys is currently turned on -- Windows "
         "may ignore brief or repeated keystrokes, which can feel like a broken keyboard."),
        ("toggle_keys_on", "Toggle Keys", "Toggle Keys is currently turned on -- this just "
         "plays a sound on Caps/Num/Scroll Lock, low-impact but worth knowing about."),
    ]
    any_accessibility_on = False
    for key, label, message in accessibility_checks:
        value = data.get(key)
        if value is True:
            any_accessibility_on = True
            report.add(CheckResult(
                name=label,
                status=Status.WARNING,
                summary=message,
                recommendation=f"Turn off {label} in Settings > Accessibility > Keyboard "
                                "if it was triggered accidentally (usually by holding "
                                "Shift, Num Lock, or a similar key combination too long).",
            ))
    if not any_accessibility_on and all(
        data.get(k) is not None for k, _, _ in accessibility_checks
    ):
        report.add(CheckResult(
            name="Accessibility keyboard features",
            status=Status.PASS,
            summary="Sticky Keys, Filter Keys, and Toggle Keys are all off.",
        ))

    # --- Interactive confirmation ------------------------------------------------------
    if simulate:
        report.add(CheckResult(
            name="Interactive input confirmation",
            status=Status.SKIPPED,
            summary="Skipped in simulate mode.",
        ))
    elif interactive:
        _interactive_input_test(report)

    return report
