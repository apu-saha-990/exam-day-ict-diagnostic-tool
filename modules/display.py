"""
display.py

Covers planning doc section 4 (DISPLAY / MONITOR ISSUES).

What this checks, and how:
  - Monitor(s) detected, resolution, which is primary  -> .NET:
        System.Windows.Forms.Screen.AllScreens (more reliable for "what's
        actually active right now" than WMI's older display classes)
  - Graphics driver health                             -> WMI: Win32_VideoController
  - Resolution too low for exam software                -> derived from the screen list above
  - Refresh rate                                         -> WMI: Win32_VideoController.CurrentRefreshRate
  - Multiple monitor confusion                           -> screen count + primary flag
  - Scaling / DPI                                        -> .NET: System.Drawing.Graphics DpiX

Why .NET Screen + a DPI-aware Win32 call instead of pure WMI:
Windows' older WMI display classes (Win32_DesktopMonitor,
Win32_PnPMonitor) are notoriously unreliable at reporting whether a
monitor is actually active right now versus just ever having been
plugged in. `System.Windows.Forms.Screen` reflects what Windows currently
believes is connected and in use, which is what actually matters for an
exam session. Win32_VideoController is still the right source for driver
health and refresh rate, since that's adapter-level information, not
per-monitor.

DPI specifically needs care: the straightforward `System.Drawing.Graphics`
approach is NOT "DPI-aware" by default, so Windows silently virtualizes
its answer and always reports the legacy 96 DPI (100%) no matter what the
real scaling setting is. This was caught during real-hardware testing --
a screen actually set to 125% scaling was reported as 100%. Fixed by
explicitly declaring per-monitor-v2 DPI awareness via
`SetProcessDpiAwarenessContext` before reading the real value with
`GetDpiForSystem`.

Known gaps (candidates for planning doc section 11):
  - Refresh rate is read per graphics *adapter*, not per individual
    monitor -- on a multi-monitor setup with mixed refresh rates, this
    won't catch a mismatch between them specifically.
  - DPI is read for the primary display only; a mixed-DPI multi-monitor
    setup (a common real cause of exam software rendering oddly on a
    second screen) isn't checked per-monitor yet.
"""

from __future__ import annotations

from .powershell_bridge import run_ps_json, PowerShellError
from .report import CategoryReport, CheckResult, Status

MIN_REASONABLE_WIDTH = 1024
MIN_REASONABLE_HEIGHT = 768
LOW_REFRESH_HZ = 30
HIGH_DPI_SCALE_PCT = 150


def _simulated_data() -> dict:
    """Mock data so the tool can be demoed/tested off a Windows exam laptop."""
    return {
        "screens": [
            {"DeviceName": r"\\.\DISPLAY1", "Width": 1920, "Height": 1080, "Primary": True},
        ],
        "video_controllers": [
            {"Name": "Intel(R) UHD Graphics", "Status": "OK", "ConfigManagerErrorCode": 0,
             "CurrentRefreshRate": 60},
        ],
        "dpi_x": 96.0,
    }


def _query() -> dict:
    script = r"""
Add-Type -AssemblyName System.Windows.Forms

$screens = [System.Windows.Forms.Screen]::AllScreens | ForEach-Object {
    [PSCustomObject]@{
        DeviceName = $_.DeviceName
        Width      = $_.Bounds.Width
        Height     = $_.Bounds.Height
        Primary    = $_.Primary
    }
}

$videoControllers = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
    Select-Object Name, Status, ConfigManagerErrorCode, CurrentRefreshRate

# System.Drawing.Graphics.DpiX is NOT DPI-aware by default and silently
# reports the legacy 96 DPI (100%) regardless of the real scaling setting,
# which produced a wrong reading during testing (a 125%-scaled screen was
# reported as 100%). Fixed by explicitly declaring per-monitor DPI
# awareness via the modern Win32 API before asking for the real value.
$dpiX = $null
try {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public class DpiHelper {
    [DllImport("user32.dll")]
    public static extern bool SetProcessDpiAwarenessContext(int dpiContext);
    [DllImport("user32.dll")]
    public static extern uint GetDpiForSystem();
}
"@
    [void][DpiHelper]::SetProcessDpiAwarenessContext(-4)  # PER_MONITOR_AWARE_V2
    $dpiX = [DpiHelper]::GetDpiForSystem()
} catch {}

$result = [PSCustomObject]@{
    Screens          = $screens
    VideoControllers = $videoControllers
    DpiX             = $dpiX
}
$result | ConvertTo-Json -Depth 4
""".strip()

    result = run_ps_json(script, timeout=12)
    if not result.success:
        raise PowerShellError(f"Display query failed: {result.raw_stderr}")

    row = result.data[0] if result.data else {}
    screens = row.get("Screens") or []
    if isinstance(screens, dict):
        screens = [screens]
    controllers = row.get("VideoControllers") or []
    if isinstance(controllers, dict):
        controllers = [controllers]

    return {
        "screens": screens,
        "video_controllers": controllers,
        "dpi_x": row.get("DpiX"),
    }


def run(simulate: bool = False) -> CategoryReport:
    report = CategoryReport(category="Display")

    if simulate:
        data = _simulated_data()
    else:
        try:
            data = _query()
        except PowerShellError as exc:
            report.add(CheckResult(
                name="Display subsystem query",
                status=Status.FAIL,
                summary="Couldn't read display information from Windows at all.",
                detail=str(exc),
                recommendation="Try running the tool again.",
            ))
            return report

    # --- Monitor detection -------------------------------------------------------
    screens = data.get("screens", [])
    if not screens:
        report.add(CheckResult(
            name="Monitor detected",
            status=Status.FAIL,
            summary="No display was detected at all -- this shouldn't be possible on a "
                     "working computer, so something is seriously wrong.",
            recommendation="Check the video cable/connection, or restart the computer.",
        ))
        return report

    report.add(CheckResult(
        name="Monitors found",
        status=Status.INFO,
        summary=f"Found {len(screens)} display(s).",
    ))

    if len(screens) > 1:
        primary = next((s for s in screens if s.get("Primary")), None)
        primary_name = primary.get("DeviceName") if primary else "unknown"
        report.add(CheckResult(
            name="Multiple monitors detected",
            status=Status.WARNING,
            summary=f"More than one monitor is connected ({len(screens)}). The primary "
                     f"display is currently '{primary_name}'.",
            recommendation="Confirm the exam software opens on the correct screen -- "
                            "it's common for video conferencing/exam apps to default to "
                            "the wrong monitor in a multi-screen setup.",
        ))
    else:
        report.add(CheckResult(
            name="Multiple monitors detected",
            status=Status.PASS,
            summary="Single display -- no risk of the exam software opening on the "
                     "wrong screen.",
        ))

    # --- Resolution ----------------------------------------------------------------
    low_res_screens = [
        s for s in screens
        if s.get("Width", 0) < MIN_REASONABLE_WIDTH or s.get("Height", 0) < MIN_REASONABLE_HEIGHT
    ]
    if low_res_screens:
        for s in low_res_screens:
            report.add(CheckResult(
                name=f"Resolution: {s.get('DeviceName', 'display')}",
                status=Status.WARNING,
                summary=f"Resolution is {s.get('Width')}x{s.get('Height')}, which is "
                         "lower than what most exam software expects to render properly.",
                recommendation="Increase the resolution in Windows display settings if "
                                "the monitor supports it.",
            ))
    else:
        res_summary = ", ".join(f"{s.get('Width')}x{s.get('Height')}" for s in screens)
        report.add(CheckResult(
            name="Resolution",
            status=Status.PASS,
            summary=f"Resolution looks fine: {res_summary}.",
        ))

    # --- Graphics driver health -----------------------------------------------------
    controllers = data.get("video_controllers", [])
    if not controllers:
        report.add(CheckResult(
            name="Graphics driver",
            status=Status.SKIPPED,
            summary="Couldn't read graphics adapter information.",
        ))
    else:
        for c in controllers:
            name = c.get("Name", "Unknown graphics adapter")
            err_code = c.get("ConfigManagerErrorCode", 0)
            if err_code and err_code != 0:
                report.add(CheckResult(
                    name=f"Graphics driver: {name}",
                    status=Status.FAIL,
                    summary=f"'{name}' has a driver problem and may not render the "
                             "display correctly.",
                    detail=f"ConfigManagerErrorCode={err_code}",
                    recommendation="Update or reinstall the graphics driver in Device Manager.",
                ))
            else:
                report.add(CheckResult(
                    name=f"Graphics driver: {name}",
                    status=Status.PASS,
                    summary=f"'{name}' driver is working normally.",
                ))

            refresh = c.get("CurrentRefreshRate")
            if refresh and refresh < LOW_REFRESH_HZ:
                report.add(CheckResult(
                    name=f"Refresh rate: {name}",
                    status=Status.WARNING,
                    summary=f"Refresh rate is unusually low ({refresh} Hz), which can "
                             "cause visible flicker or glitching.",
                    recommendation="Check the display settings for a higher refresh "
                                    "rate option.",
                ))

    # --- DPI / scaling -----------------------------------------------------------------
    dpi_x = data.get("dpi_x")
    if dpi_x:
        scale_pct = round((dpi_x / 96.0) * 100)
        if scale_pct > HIGH_DPI_SCALE_PCT:
            report.add(CheckResult(
                name="Display scaling",
                status=Status.WARNING,
                summary=f"Display scaling is set to {scale_pct}%, which is high enough "
                         "that some exam software may render UI elements oddly or "
                         "cut off.",
                recommendation="If the exam software looks wrong, try reducing display "
                                "scaling to 100-125% in Windows display settings.",
            ))
        else:
            report.add(CheckResult(
                name="Display scaling",
                status=Status.PASS,
                summary=f"Display scaling is {scale_pct}%, within a normal range.",
            ))
    else:
        report.add(CheckResult(
            name="Display scaling",
            status=Status.SKIPPED,
            summary="Couldn't determine the current display scaling percentage.",
        ))

    return report
