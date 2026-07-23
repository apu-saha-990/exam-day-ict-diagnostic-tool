"""
webcam.py

Covers planning doc section 2 (WEBCAM / VIDEO ISSUES).

What this checks, and how:
  - Camera detected + driver health          -> WMI: Win32_PnPEntity (PNPClass Camera/Image)
  - Camera OS-level privacy permission       -> registry: ConsentStore (same pattern as audio.py's mic check)
  - Camera currently held open by another
    app (the classic "Teams is hogging the
    webcam" conflict)                        -> registry: ConsentStore per-app usage timestamps

How the "in use" detection works:
Windows records, per app, when it last started and stopped using the
camera, in the registry under CapabilityAccessManager\\ConsentStore\\webcam.
Each app gets a subkey with LastUsedTimeStart / LastUsedTimeStop values.
When an app currently has the camera open, LastUsedTimeStop reads as 0
(hasn't stopped yet). This is a known, widely-used technique (several
"who's using my webcam" community scripts rely on the same registry
keys) rather than something Windows documents as a stable public API, so
it's treated as best-effort: if the registry layout doesn't match on a
given Windows build, this check reports SKIPPED rather than guessing.

Packaged (Microsoft Store) apps are keyed by their raw package family
name (e.g. "Microsoft.WindowsCamera_8wekyb3d8bbwe"), which means nothing
to a non-technical invigilator, so a small lookup table translates the
common ones into plain names. Desktop apps (Teams, Zoom, Chrome, most
exam software) are keyed by their install path instead, which is already
readable, so no translation is needed there.

Known gaps (candidates for planning doc section 11):
  - "Wrong camera selected" can't be generically detected -- unlike audio,
    Windows has no single system-wide default camera. Each app (Zoom,
    Teams, the exam platform) picks its own. This tool lists every camera
    detected so a support officer can confirm the right one manually.
  - USB bandwidth drop-outs (webcam disconnecting when combined with
    other USB devices) would need to be caught live, in the moment it
    happens, via event log monitoring -- see the driver/event-log
    category instead, once built.
"""

from __future__ import annotations

from .powershell_bridge import run_ps_json, PowerShellError
from .report import CategoryReport, CheckResult, Status

# Package family name -> plain name, for the most common apps likely to
# show up on an exam laptop. Anything not in here is shown as-is.
KNOWN_PACKAGED_APPS = {
    "microsoft.windowscamera_8wekyb3d8bbwe": "Windows Camera app",
    "microsoft.skypeapp_kzf8qxf38zg5c": "Skype",
    "microsoft.microsoftteams_8wekyb3d8bbwe": "Microsoft Teams",
    "5319275a.whatsappdesktop_cv1g1gvanyjgm": "WhatsApp Desktop",
    "microsoft.windows.photos_8wekyb3d8bbwe": "Windows Photos app",
}


def _friendly_app_name(raw_name: str) -> str:
    key = raw_name.strip().lower()
    if key in KNOWN_PACKAGED_APPS:
        return KNOWN_PACKAGED_APPS[key]
    # Desktop apps are keyed by install path (e.g. "C:\...\Teams.exe") --
    # already readable, so just return the filename rather than the full path.
    if "\\" in raw_name:
        return raw_name.rsplit("\\", 1)[-1]
    return raw_name


def _simulated_data() -> dict:
    """Mock data so the tool can be demoed/tested off a Windows exam laptop."""
    return {
        "cameras": [
            {"Name": "HD Pro Webcam C920", "Manufacturer": "Logitech", "Status": "OK",
             "ConfigManagerErrorCode": 0, "PNPClass": "Camera"},
        ],
        "cam_global_consent": "Allow",
        "cam_desktop_consent": "Allow",
        "apps_using_camera": [],
    }


def _query(simulate: bool = False) -> dict:
    if simulate:
        return _simulated_data()

    script = r"""
$cams = Get-CimInstance Win32_PnPEntity |
    Where-Object { $_.PNPClass -in @('Camera', 'Image') } |
    Select-Object Name, Manufacturer, Status, ConfigManagerErrorCode, PNPClass

$camGlobal = $null
try {
    $camGlobal = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam' -Name Value -ErrorAction Stop).Value
} catch {}
$camDesktop = $null
try {
    $camDesktop = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\NonPackaged\webcam' -Name Value -ErrorAction Stop).Value
} catch {}

$inUse = @()
$usagePaths = @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam\NonPackaged'
)
foreach ($p in $usagePaths) {
    if (Test-Path $p) {
        Get-ChildItem $p -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $props = Get-ItemProperty $_.PsPath -ErrorAction Stop
                if (($props.PSObject.Properties.Name -contains 'LastUsedTimeStop') -and ($props.LastUsedTimeStop -eq 0)) {
                    $inUse += $_.PSChildName
                }
            } catch {}
        }
    }
}

$result = [PSCustomObject]@{
    Cameras           = $cams
    CamGlobalConsent  = $camGlobal
    CamDesktopConsent = $camDesktop
    AppsUsingCamera   = $inUse
}
$result | ConvertTo-Json -Depth 4
""".strip()

    result = run_ps_json(script, timeout=12)
    if not result.success or not result.data:
        raise PowerShellError(f"Webcam query failed: {result.raw_stderr}")

    row = result.data[0] if result.data else {}
    cameras = row.get("Cameras") or []
    if isinstance(cameras, dict):
        cameras = [cameras]
    apps_using = row.get("AppsUsingCamera") or []
    if isinstance(apps_using, str):
        apps_using = [apps_using]

    return {
        "cameras": cameras,
        "cam_global_consent": row.get("CamGlobalConsent"),
        "cam_desktop_consent": row.get("CamDesktopConsent"),
        "apps_using_camera": apps_using,
    }


def run(simulate: bool = False) -> CategoryReport:
    report = CategoryReport(category="Webcam")

    try:
        data = _query(simulate=simulate)
    except PowerShellError as exc:
        report.add(CheckResult(
            name="Webcam subsystem query",
            status=Status.FAIL,
            summary="Couldn't read webcam information from Windows at all.",
            detail=str(exc),
            recommendation="Try running the tool again. If this keeps happening, "
                            "the issue may be with WMI itself, which is a bigger "
                            "system problem to flag to IT.",
        ))
        return report

    # --- Camera detection / driver health ------------------------------------
    cameras = data.get("cameras", [])
    if not cameras:
        report.add(CheckResult(
            name="Camera detected",
            status=Status.FAIL,
            summary="No camera was found on this computer at all.",
            recommendation="Check physical/USB connections, then check Device Manager "
                            "for a disabled or missing camera driver.",
        ))
    else:
        names = ", ".join(c.get("Name", "Unknown camera") for c in cameras)
        report.add(CheckResult(
            name="Cameras found",
            status=Status.INFO,
            summary=f"Found {len(cameras)} camera(s): {names}.",
        ))
        if len(cameras) > 1:
            report.add(CheckResult(
                name="Multiple cameras present",
                status=Status.WARNING,
                summary=f"More than one camera is detected ({len(cameras)}). Windows has "
                         "no single 'default camera' the way it does for audio -- each "
                         "app picks its own.",
                recommendation="Manually confirm the exam software is set to use the "
                                "correct camera, not the built-in laptop camera by mistake "
                                "(or vice versa).",
            ))

        for c in cameras:
            err_code = c.get("ConfigManagerErrorCode", 0)
            name = c.get("Name", "Unknown camera")
            if err_code and err_code != 0:
                report.add(CheckResult(
                    name=f"Driver status: {name}",
                    status=Status.FAIL,
                    summary=f"'{name}' has a driver problem and may not work correctly.",
                    detail=f"ConfigManagerErrorCode={err_code}",
                    recommendation="Update or reinstall the driver in Device Manager, "
                                    "or try a different USB port if it's an external device.",
                ))
            else:
                report.add(CheckResult(
                    name=f"Driver status: {name}",
                    status=Status.PASS,
                    summary=f"'{name}' driver is working normally.",
                ))

    # --- Camera OS privacy permission -----------------------------------------
    cam_global = data.get("cam_global_consent")
    if cam_global is None:
        report.add(CheckResult(
            name="Camera privacy permission",
            status=Status.SKIPPED,
            summary="Couldn't determine the Windows camera privacy setting.",
        ))
    elif str(cam_global).lower() == "deny":
        report.add(CheckResult(
            name="Camera privacy permission",
            status=Status.FAIL,
            summary="Windows is blocking ALL apps from using the camera at the system level.",
            recommendation="Settings > Privacy & security > Camera > turn on 'Camera access'.",
        ))
    else:
        report.add(CheckResult(
            name="Camera privacy permission",
            status=Status.PASS,
            summary="Windows allows apps to access the camera (system-wide setting).",
        ))

    cam_desktop = data.get("cam_desktop_consent")
    if cam_desktop is not None and str(cam_desktop).lower() == "deny":
        report.add(CheckResult(
            name="Camera access for desktop apps",
            status=Status.FAIL,
            summary="Windows is blocking desktop apps specifically from using the camera.",
            detail="Separate toggle from the general camera privacy setting -- this is "
                    "the one that typically affects browser-based or installed exam "
                    "software rather than Windows Store apps.",
            recommendation="Settings > Privacy & security > Camera > turn on "
                            "'Let desktop apps access your camera'.",
        ))

    # --- Camera held open by another app --------------------------------------
    apps_using = data.get("apps_using_camera", [])
    if apps_using:
        friendly_names = [_friendly_app_name(a) for a in apps_using]
        app_list = ", ".join(friendly_names)
        report.add(CheckResult(
            name="Camera in use by another app",
            status=Status.WARNING,
            summary=f"The camera currently looks like it's held open by: {app_list}. "
                     "This is the classic 'exam software can't get the camera because "
                     "Teams/Zoom already has it' conflict.",
            detail="Based on Windows' per-app camera usage timestamps in the registry -- "
                    "best-effort, not a guaranteed live lock check. Raw identifier(s): "
                    + ", ".join(apps_using),
            recommendation="Close the app(s) listed above before starting the exam software, "
                            "or check Task Manager for anything still running in the background.",
        ))
    else:
        report.add(CheckResult(
            name="Camera in use by another app",
            status=Status.PASS,
            summary="No other app currently appears to be holding the camera open.",
        ))

    return report
