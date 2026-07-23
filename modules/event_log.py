"""
event_log.py

Covers planning doc section 7 (SYSTEM / EVENT LOG BASED "GLITCH" DETECTION).

What this checks, and how:
  - Recent hardware errors / driver crashes    -> Windows Event Log (System),
                                                   Critical + Error level, last 24h
  - Recent unexpected reboots/shutdowns        -> Event IDs 41 (Kernel-Power,
                                                   unclean reboot) and 6008
                                                   (EventLog, unexpected
                                                   shutdown), last 7 days
  - Available RAM / CPU load right now         -> WMI: Win32_OperatingSystem,
                                                   Win32_Processor
  - Disk space on fixed drives                 -> WMI: Win32_LogicalDisk

Why event IDs 41 and 6008 specifically:
These are the two standard Windows signatures for "this computer did not
shut down properly" -- 41 fires on the next boot after a hard power loss
or crash (no clean shutdown was recorded), 6008 is logged by the Event
Log service itself for the same situation. Either one showing up
repeatedly is a real instability signal worth flagging before an exam,
even without knowing the specific root cause.

This is a point-in-time snapshot, not continuous monitoring:
RAM/CPU/disk are read once, at the moment this check runs -- a spike five
minutes later wouldn't be caught. That's a deliberate scope limit, not an
oversight; genuine live monitoring during an exam is a different kind of
tool than a pre-exam diagnostic pass.

Known gaps (candidates for planning doc section 11):
  - The Critical/Error event scan is intentionally broad (any provider),
    not narrowed to a curated list of "driver crash" event IDs, because
    trying to hardcode every relevant ID/provider combination would be a
    never-finished list. It reports total count + top offending providers
    so a support officer can judge relevance themselves, rather than
    silently filtering out something that mattered.
  - No baseline/trend comparison -- this reports what's true right now,
    not "is this worse than usual for this specific machine."
"""

from __future__ import annotations

from .powershell_bridge import run_ps_json, PowerShellError
from .report import CategoryReport, CheckResult, Status

RAM_FREE_WARNING_PCT = 15
RAM_FREE_FAIL_PCT = 5
DISK_FREE_WARNING_PCT = 15
DISK_FREE_FAIL_PCT = 5
CPU_LOAD_WARNING_PCT = 85

# Providers that are famous for logging routine noise rather than actual
# hardware/driver problems -- found via real-world testing (a completely
# healthy machine still showed 5 "error" events, mostly DistributedCOM).
# These aren't hidden, just not treated as the headline WARNING on their
# own -- if something else is also wrong, the overall status still
# reflects that; this only stops routine background noise from making a
# perfectly healthy machine look like it has a driver problem.
KNOWN_NOISY_PROVIDERS = {
    "microsoft-windows-distributedcom",  # routine DCOM permission noise, near-universal
    "microsoft-windows-deviceassociationservice",  # routine device-pairing service noise
}


def _simulated_data() -> dict:
    """Mock data so the tool can be demoed/tested off a Windows exam laptop."""
    return {
        "recent_error_count": 0,
        "error_summary": [],
        "reboot_events": [],
        "free_mem_pct": 62.0,
        "free_mem_mb": 9800,
        "cpu_load_pct": 12,
        "disks": [
            {"DeviceID": "C:", "FreeGB": 180.0, "TotalGB": 476.0, "FreePct": 37.8},
        ],
    }


def _query() -> dict:
    script = r"""
$now = Get-Date

$recentErrors = @()
try {
    $recentErrors = Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; StartTime=$now.AddHours(-24)} -MaxEvents 500 -ErrorAction Stop |
        Select-Object TimeCreated, ProviderName, Id, LevelDisplayName
} catch {}

$errorSummary = @()
if ($recentErrors) {
    $errorSummary = $recentErrors | Group-Object ProviderName | Sort-Object Count -Descending |
        Select-Object -First 5 -Property Name, Count
}

$rebootEvents = @()
try {
    $rebootEvents = Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,6008; StartTime=$now.AddDays(-7)} -MaxEvents 100 -ErrorAction Stop |
        Select-Object TimeCreated, Id, ProviderName
} catch {}

$os = Get-CimInstance Win32_OperatingSystem
$totalMemKB = $os.TotalVisibleMemorySize
$freeMemKB = $os.FreePhysicalMemory
$freeMemPct = if ($totalMemKB -gt 0) { [math]::Round(($freeMemKB / $totalMemKB) * 100, 1) } else { $null }

$cpuLoad = $null
try {
    $cpuLoad = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
} catch {}

$disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction SilentlyContinue |
    Select-Object DeviceID,
        @{N='FreeGB'; E={[math]::Round($_.FreeSpace/1GB,1)}},
        @{N='TotalGB'; E={[math]::Round($_.Size/1GB,1)}},
        @{N='FreePct'; E={ if ($_.Size -gt 0) { [math]::Round(($_.FreeSpace/$_.Size)*100,1) } else { $null } }}

$result = [PSCustomObject]@{
    RecentErrorCount = $recentErrors.Count
    ErrorSummary     = $errorSummary
    RebootEvents     = $rebootEvents
    FreeMemPct       = $freeMemPct
    FreeMemMB        = [math]::Round($freeMemKB/1024,0)
    CpuLoadPct       = $cpuLoad
    Disks            = $disks
}
$result | ConvertTo-Json -Depth 5
""".strip()

    result = run_ps_json(script, timeout=25)
    if not result.success:
        raise PowerShellError(f"Event log / system resource query failed: {result.raw_stderr}")

    row = result.data[0] if result.data else {}
    error_summary = row.get("ErrorSummary") or []
    if isinstance(error_summary, dict):
        error_summary = [error_summary]
    reboot_events = row.get("RebootEvents") or []
    if isinstance(reboot_events, dict):
        reboot_events = [reboot_events]
    disks = row.get("Disks") or []
    if isinstance(disks, dict):
        disks = [disks]

    return {
        "recent_error_count": row.get("RecentErrorCount", 0),
        "error_summary": error_summary,
        "reboot_events": reboot_events,
        "free_mem_pct": row.get("FreeMemPct"),
        "free_mem_mb": row.get("FreeMemMB"),
        "cpu_load_pct": row.get("CpuLoadPct"),
        "disks": disks,
    }


def run(simulate: bool = False) -> CategoryReport:
    report = CategoryReport(category="System / Event Log")

    if simulate:
        data = _simulated_data()
    else:
        try:
            data = _query()
        except PowerShellError as exc:
            report.add(CheckResult(
                name="Event log / system resource query",
                status=Status.FAIL,
                summary="Couldn't read the Windows Event Log or system resource "
                         "information at all.",
                detail=str(exc),
                recommendation="Try running the tool again.",
            ))
            return report

    # --- Recent critical/error events (last 24h) --------------------------------
    error_count = data.get("recent_error_count", 0)
    top_sources = data.get("error_summary", [])

    if error_count == 0:
        report.add(CheckResult(
            name="Recent system errors",
            status=Status.PASS,
            summary="No critical or error-level entries in the System event log over "
                     "the last 24 hours.",
        ))
    else:
        sources_text = ", ".join(f"{s.get('Name')} ({s.get('Count')})" for s in top_sources)
        # Of the providers we can actually see (top 5 by count), how much is
        # known routine noise vs something worth a second look? This is an
        # approximation limited to what's visible in that top-5 summary --
        # good enough to stop routine noise from making a healthy machine
        # look broken, without hiding anything from the detail field.
        noisy_count = sum(
            s.get("Count", 0) for s in top_sources
            if str(s.get("Name", "")).lower() in KNOWN_NOISY_PROVIDERS
        )
        non_noisy_count = sum(
            s.get("Count", 0) for s in top_sources
            if str(s.get("Name", "")).lower() not in KNOWN_NOISY_PROVIDERS
        )

        if non_noisy_count == 0 and noisy_count > 0:
            report.add(CheckResult(
                name="Recent system errors",
                status=Status.PASS,
                summary=f"{error_count} error(s) were logged in the last 24 hours, but "
                         "the identifiable sources are routine Windows background noise "
                         "(DCOM permission chatter / device-pairing service), not signs "
                         "of an actual hardware or driver problem.",
                detail=f"Sources (Critical/Error level in Event Viewer): {sources_text}",
            ))
        else:
            report.add(CheckResult(
                name="Recent system errors",
                status=Status.WARNING,
                summary=f"{error_count} error(s) were logged in the last 24 hours -- not "
                         "necessarily serious, but worth a quick look since the source "
                         "isn't a known-harmless one.",
                detail=f"Sources (Critical/Error level in Event Viewer): {sources_text}" if sources_text else "",
                recommendation="Open Event Viewer (search 'Event Viewer' in the Start "
                                "menu) > Windows Logs > System, and check the entries "
                                "listed above if the exam software behaves oddly.",
            ))

    # --- Unexpected reboots/shutdowns (last 7 days) -------------------------------
    reboot_events = data.get("reboot_events", [])
    if not reboot_events:
        report.add(CheckResult(
            name="Unexpected reboots/shutdowns",
            status=Status.PASS,
            summary="No unexpected reboots or shutdowns in the last 7 days.",
        ))
    else:
        count = len(reboot_events)
        most_recent = reboot_events[0].get("TimeCreated", "unknown time")
        status = Status.FAIL if count >= 3 else Status.WARNING
        report.add(CheckResult(
            name="Unexpected reboots/shutdowns",
            status=status,
            summary=f"{count} unexpected reboot/shutdown event(s) in the last 7 days "
                     f"(most recent: {most_recent}) -- a sign of possible instability.",
            recommendation="If this keeps happening, treat it as a hardware/stability "
                            "issue worth investigating before relying on this machine "
                            "for an exam session.",
        ))

    # --- RAM ---------------------------------------------------------------------------
    free_mem_pct = data.get("free_mem_pct")
    free_mem_mb = data.get("free_mem_mb")
    if free_mem_pct is not None:
        if free_mem_pct < RAM_FREE_FAIL_PCT:
            report.add(CheckResult(
                name="Available memory",
                status=Status.FAIL,
                summary=f"Only {free_mem_pct}% RAM free ({free_mem_mb} MB) -- high risk "
                         "of the exam software freezing or crashing.",
                recommendation="Close unnecessary applications and browser tabs before "
                                "starting the exam.",
            ))
        elif free_mem_pct < RAM_FREE_WARNING_PCT:
            report.add(CheckResult(
                name="Available memory",
                status=Status.WARNING,
                summary=f"{free_mem_pct}% RAM free ({free_mem_mb} MB) -- on the low side.",
                recommendation="Consider closing unused applications before the exam.",
            ))
        else:
            report.add(CheckResult(
                name="Available memory",
                status=Status.PASS,
                summary=f"{free_mem_pct}% RAM free ({free_mem_mb} MB) -- healthy.",
            ))
    else:
        report.add(CheckResult(
            name="Available memory",
            status=Status.SKIPPED,
            summary="Couldn't read memory information.",
        ))

    # --- CPU ------------------------------------------------------------------------------
    cpu_load = data.get("cpu_load_pct")
    if cpu_load is not None:
        if cpu_load > CPU_LOAD_WARNING_PCT:
            report.add(CheckResult(
                name="CPU load",
                status=Status.WARNING,
                summary=f"CPU is currently at {cpu_load}% load -- a single-moment "
                         "snapshot, but worth checking what's running if it stays high.",
                recommendation="Check Task Manager for anything unexpectedly using the "
                                "CPU before starting the exam.",
            ))
        else:
            report.add(CheckResult(
                name="CPU load",
                status=Status.PASS,
                summary=f"CPU load is {cpu_load}% right now -- healthy.",
            ))
    else:
        report.add(CheckResult(
            name="CPU load",
            status=Status.SKIPPED,
            summary="Couldn't read CPU load information.",
        ))

    # --- Disk space -----------------------------------------------------------------------
    disks = data.get("disks", [])
    if not disks:
        report.add(CheckResult(
            name="Disk space",
            status=Status.SKIPPED,
            summary="Couldn't read disk space information.",
        ))
    else:
        for d in disks:
            drive = d.get("DeviceID", "Unknown drive")
            free_gb = d.get("FreeGB")
            free_pct = d.get("FreePct")
            if free_pct is None:
                continue
            if free_pct < DISK_FREE_FAIL_PCT:
                report.add(CheckResult(
                    name=f"Disk space: {drive}",
                    status=Status.FAIL,
                    summary=f"Drive {drive} has only {free_gb} GB free ({free_pct}%) -- "
                             "very likely to cause problems if the exam software needs "
                             "to write recordings or logs.",
                    recommendation="Free up space before the exam -- delete temp files, "
                                    "empty the recycle bin, or move large files elsewhere.",
                ))
            elif free_pct < DISK_FREE_WARNING_PCT:
                report.add(CheckResult(
                    name=f"Disk space: {drive}",
                    status=Status.WARNING,
                    summary=f"Drive {drive} has {free_gb} GB free ({free_pct}%) -- on "
                             "the low side.",
                    recommendation="Consider freeing up some space before the exam.",
                ))
            else:
                report.add(CheckResult(
                    name=f"Disk space: {drive}",
                    status=Status.PASS,
                    summary=f"Drive {drive} has {free_gb} GB free ({free_pct}%) -- plenty.",
                ))

    return report
