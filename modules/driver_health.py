"""
driver_health.py

Covers planning doc section 6 (DRIVER HEALTH - SYSTEM-WIDE).

What this checks, and how:
  - Every device on the system, not just audio/video/network/display/
    input (which already have their own dedicated categories)  -> WMI:
    Win32_PnPEntity, scanning ALL entries rather than filtering by class
  - "Unknown device" / missing driver / conflict flags           -> Name
    matching + ConfigManagerErrorCode
  - Simple traffic-light summary (green/yellow/red)               -> this
    falls out naturally from CategoryReport's existing PASS/WARNING/FAIL
    rollup, so no extra work was needed to satisfy that part of the
    planning doc

Why this exists alongside the more specific categories:
Audio, webcam, network, display, and input devices each check their own
device class in detail. This category is the deliberate broad net behind
them -- printers, USB controllers, storage controllers, chipset devices,
docking station hardware, anything with a completely missing driver --
that wouldn't be caught by any of the more targeted checks. Some overlap
with the other categories is expected and fine (e.g. a broken audio
device driver will show up here too); the point isn't to be non-
overlapping, it's to make sure nothing on the whole machine falls through
every category's cracks.

How error codes are interpreted:
Windows exposes a numeric ConfigManagerErrorCode for every PnP device.
0 means healthy. This module maps the common non-zero codes to a plain-
English description (via a lookup table) rather than showing a bare
"error 43" to a non-technical invigilator. Code 22 (device disabled) is
treated as a WARNING rather than a FAIL, since a disabled device is
often intentional (e.g. someone disabled an unused Bluetooth radio) --
every other non-zero code is treated as a genuine problem.

Known gaps (candidates for planning doc section 11):
  - This is a snapshot, not continuous monitoring -- a device that fails
    mid-exam after this check ran clean wouldn't be caught until the tool
    is run again. Live monitoring belongs in the event-log category.
  - Some virtual/software-only devices occasionally report non-zero
    codes that are cosmetic rather than functional (certain Microsoft
    virtual adapters, for instance) -- this module doesn't maintain an
    exclusion list for those yet, so a handful of harmless WARNINGs are
    possible on some machines. Worth refining once tested more broadly.
"""

from __future__ import annotations

from .powershell_bridge import run_ps_json, PowerShellError
from .report import CategoryReport, CheckResult, Status

# Common Win32 ConfigManagerErrorCode values, in plain English.
# Reference: Microsoft's published CM_PROB_* device status codes.
ERROR_CODE_DESCRIPTIONS = {
    1: "This device isn't configured correctly.",
    3: "The driver for this device might be corrupted, or the system is low on resources.",
    10: "This device can't start.",
    12: "This device can't find enough free resources to use.",
    14: "This device won't work properly until the computer is restarted.",
    16: "Windows can't identify all the resources this device uses.",
    18: "The drivers for this device need to be reinstalled.",
    19: "This device's configuration information is incomplete or damaged.",
    21: "Windows is in the process of removing this device.",
    22: "This device is disabled.",
    24: "This device isn't present, isn't working properly, or is missing drivers.",
    28: "The drivers for this device aren't installed.",
    31: "Windows can't load the drivers this device needs.",
    32: "A driver (service) for this device has been disabled.",
    33: "Windows can't determine which resources this device needs.",
    34: "Windows can't determine the settings for this device.",
    36: "This device is requesting a PCI interrupt but is configured for an ISA interrupt.",
    37: "Windows can't initialize the driver for this device.",
    38: "A previous copy of this device's driver is still in memory.",
    39: "The driver for this device may be corrupted or missing.",
    40: "This device's registry service key information is missing or wrong.",
    41: "Windows loaded the driver but can't find the hardware.",
    42: "There's a duplicate device already running on this system.",
    43: "Windows has stopped this device because it reported problems.",
    44: "An application or service has shut down this device.",
    45: "This hardware device isn't currently connected to the computer.",
    47: "This device has been prepared for safe removal but hasn't been unplugged yet.",
    48: "This device's software has been blocked from starting.",
    52: "Windows can't verify the digital signature for this device's drivers.",
}

WARNING_ONLY_CODES = {22}  # "Disabled" is often intentional, not a fault


def _describe_error_code(code: int) -> str:
    return ERROR_CODE_DESCRIPTIONS.get(code, f"Unrecognised driver problem (error code {code}).")


def _simulated_data() -> dict:
    """Mock data so the tool can be demoed/tested off a Windows exam laptop."""
    return {
        "total_devices": 84,
        "problem_devices": [
            {"Name": "Unknown Device", "PNPClass": None, "ConfigManagerErrorCode": 28,
             "Manufacturer": "Unknown"},
        ],
    }


def _query() -> dict:
    script = r"""
$devices = Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
    Select-Object Name, Status, ConfigManagerErrorCode, PNPClass, Manufacturer

$total = $devices.Count
$problems = $devices | Where-Object { $_.ConfigManagerErrorCode -ne 0 }

$result = [PSCustomObject]@{
    TotalDevices    = $total
    ProblemDevices  = $problems
}
$result | ConvertTo-Json -Depth 4
""".strip()

    result = run_ps_json(script, timeout=20)
    if not result.success:
        raise PowerShellError(f"System-wide driver query failed: {result.raw_stderr}")

    row = result.data[0] if result.data else {}
    problems = row.get("ProblemDevices") or []
    if isinstance(problems, dict):
        problems = [problems]

    return {
        "total_devices": row.get("TotalDevices", 0),
        "problem_devices": problems,
    }


def run(simulate: bool = False) -> CategoryReport:
    report = CategoryReport(category="Driver Health (System-Wide)")

    if simulate:
        data = _simulated_data()
    else:
        try:
            data = _query()
        except PowerShellError as exc:
            report.add(CheckResult(
                name="System-wide driver scan",
                status=Status.FAIL,
                summary="Couldn't scan devices on this computer at all.",
                detail=str(exc),
                recommendation="Try running the tool again.",
            ))
            return report

    total = data.get("total_devices", 0)
    problems = data.get("problem_devices", [])

    report.add(CheckResult(
        name="Devices scanned",
        status=Status.INFO,
        summary=f"Scanned {total} device(s) on this computer.",
    ))

    if not problems:
        report.add(CheckResult(
            name="System-wide driver health",
            status=Status.PASS,
            summary="No driver problems found anywhere on the system.",
        ))
        return report

    for d in problems:
        name = d.get("Name") or "Unnamed device"
        code = d.get("ConfigManagerErrorCode", 0)
        manufacturer = d.get("Manufacturer")
        is_unknown = "unknown device" in name.lower()

        description = _describe_error_code(code)
        status = Status.WARNING if code in WARNING_ONLY_CODES else Status.FAIL

        label = f"Driver problem: {name}"
        summary = description
        if is_unknown:
            summary = (
                f"An unidentified device is present with no working driver. {description}"
            )

        detail_parts = [f"ConfigManagerErrorCode={code}"]
        if manufacturer:
            detail_parts.append(f"Manufacturer={manufacturer}")

        report.add(CheckResult(
            name=label,
            status=status,
            summary=summary,
            detail=", ".join(detail_parts),
            recommendation="Check Device Manager for this device -- update, reinstall, "
                            "or roll back its driver, or reconnect it if it's external."
                            if status == Status.FAIL else
                            "If this was disabled intentionally, no action needed -- "
                            "otherwise, re-enable it in Device Manager.",
        ))

    return report
