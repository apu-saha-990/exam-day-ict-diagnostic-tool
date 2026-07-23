"""
audio.py

Covers planning doc section 1 (AUDIO ISSUES).

What this checks, and how:
  - Driver-level health of audio hardware       -> WMI: Win32_SoundDevice
  - Default playback/recording device + mute    -> AudioDeviceCmdlets module
                                                    (see note below on why)
  - Microphone OS-level privacy permission      -> registry: ConsentStore
  - Bluetooth audio device connected but not
    necessarily the active default              -> WMI: Win32_PnPEntity

Why AudioDeviceCmdlets:
Windows doesn't expose "what's the current default playback/recording
device" or "is it muted" through built-in WMI classes or PowerShell
cmdlets -- that state lives behind the Core Audio COM API
(IMMDeviceEnumerator / IAudioEndpointVolume), which isn't scriptable
without either writing custom COM interop or using a wrapper. Rather than
ship untested inline C# for a live exam tool, this uses AudioDeviceCmdlets
(open-source, PowerShell Gallery: Install-Module AudioDeviceCmdlets),
which is a small, widely used wrapper around exactly that API. If it isn't
installed, this module still runs the driver-health and privacy checks
(which need nothing extra) and reports the default-device checks as
SKIPPED with install instructions, rather than failing the whole category.

Known gaps (flagged for planning doc section 11, not silently skipped):
  - Per-app volume mixer muting (exam software muted while system isn't)
    requires enumerating ISimpleAudioVolume sessions per process, which is
    a deeper piece of COM interop. Not implemented yet.
  - Audio enhancements / spatial sound state lives in per-endpoint
    "FxProperties" registry keys whose layout varies by audio driver, so
    it isn't checked generically yet.
"""

from __future__ import annotations

from .powershell_bridge import run_ps_json, module_available, PowerShellError
from .report import CategoryReport, CheckResult, Status

AUDIO_MIXER_CMDLET_MODULE = "AudioDeviceCmdlets"


def _simulated_data() -> dict:
    """Mock data so the tool can be demoed/tested off a Windows exam laptop."""
    return {
        "sound_devices": [
            {"Name": "Realtek(R) Audio", "Manufacturer": "Realtek", "Status": "OK",
             "ConfigManagerErrorCode": 0},
            {"Name": "Logitech USB Headset", "Manufacturer": "Logitech", "Status": "OK",
             "ConfigManagerErrorCode": 0},
        ],
        "mic_global_consent": "Allow",
        "mic_desktop_consent": "Allow",
        "bluetooth_audio_devices": [],
        "cmdlets_available": True,
        "playback_name": "Logitech USB Headset",
        "playback_muted": False,
        "recording_name": "Logitech USB Headset",
        "recording_muted": False,
    }


def _query_wmi_and_registry() -> dict:
    script = r"""
$sound = Get-CimInstance Win32_SoundDevice |
    Select-Object Name, Manufacturer, Status, ConfigManagerErrorCode
$micGlobal = $null
try {
    $micGlobal = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone' -Name Value -ErrorAction Stop).Value
} catch {}
$micDesktop = $null
try {
    $micDesktop = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\NonPackaged\microphone' -Name Value -ErrorAction Stop).Value
} catch {}
$bt = Get-CimInstance Win32_PnPEntity |
    Where-Object { $_.Name -match 'headset|hands-free|bluetooth.*audio|audio.*bluetooth' } |
    Select-Object Name, Status
$result = [PSCustomObject]@{
    SoundDevices          = $sound
    MicGlobalConsent      = $micGlobal
    MicDesktopAppConsent  = $micDesktop
    BluetoothAudioDevices = $bt
}
$result | ConvertTo-Json -Depth 4
""".strip()

    result = run_ps_json(script, timeout=12)
    if not result.success or not result.data:
        raise PowerShellError(f"Audio WMI/registry query failed: {result.raw_stderr}")

    row = result.data[0] if result.data else {}
    sound_devices = row.get("SoundDevices") or []
    if isinstance(sound_devices, dict):
        sound_devices = [sound_devices]
    bt_devices = row.get("BluetoothAudioDevices") or []
    if isinstance(bt_devices, dict):
        bt_devices = [bt_devices]

    return {
        "sound_devices": sound_devices,
        "mic_global_consent": row.get("MicGlobalConsent"),
        "mic_desktop_consent": row.get("MicDesktopAppConsent"),
        "bluetooth_audio_devices": bt_devices,
    }


def _query_default_devices() -> dict:
    if not module_available(AUDIO_MIXER_CMDLET_MODULE):
        return {"cmdlets_available": False}

    script = r"""
Import-Module AudioDeviceCmdlets -ErrorAction Stop
$playback = Get-AudioDevice -Playback
$playbackMute = Get-AudioDevice -PlaybackMute
$recording = Get-AudioDevice -Recording
$recordingMute = Get-AudioDevice -RecordingMute
$allDevices = Get-AudioDevice -List | Select-Object Name, Type, Default
$result = [PSCustomObject]@{
    PlaybackName    = $playback.Name
    PlaybackMuted   = $playbackMute
    RecordingName   = $recording.Name
    RecordingMuted  = $recordingMute
    AllDevices      = $allDevices
}
$result | ConvertTo-Json -Depth 4
""".strip()

    result = run_ps_json(script, timeout=10)
    if not result.success or not result.data:
        return {"cmdlets_available": True, "error": result.raw_stderr}

    row = result.data[0]
    all_devices = row.get("AllDevices") or []
    if isinstance(all_devices, dict):
        all_devices = [all_devices]

    return {
        "cmdlets_available": True,
        "playback_name": row.get("PlaybackName"),
        "playback_muted": row.get("PlaybackMuted"),
        "recording_name": row.get("RecordingName"),
        "recording_muted": row.get("RecordingMuted"),
        # Every device Windows currently has active as an audio endpoint --
        # NOT the same as "every device ever paired." Used to filter out
        # stale Bluetooth PnP entries (see the Bluetooth check below).
        "active_audio_devices": [d.get("Name", "") for d in all_devices],
    }


def run(simulate: bool = False) -> CategoryReport:
    report = CategoryReport(category="Audio")

    if simulate:
        data = _simulated_data()
    else:
        try:
            data = _query_wmi_and_registry()
            data.update(_query_default_devices())
        except PowerShellError as exc:
            report.add(CheckResult(
                name="Audio subsystem query",
                status=Status.FAIL,
                summary="Couldn't read audio hardware information from Windows at all.",
                detail=str(exc),
                recommendation="Try running the tool again. If this keeps happening, "
                                "the issue may be with WMI itself, which is a bigger "
                                "system problem to flag to IT.",
            ))
            return report

    # --- Driver health / device presence -----------------------------------
    sound_devices = data.get("sound_devices", [])
    if not sound_devices:
        report.add(CheckResult(
            name="Audio device detected",
            status=Status.FAIL,
            summary="No audio device was found on this computer at all.",
            recommendation="Check physical connections, then check Device Manager "
                            "for a disabled or missing audio driver.",
        ))
    else:
        names = ", ".join(d.get("Name", "Unknown device") for d in sound_devices)
        report.add(CheckResult(
            name="Audio devices found",
            status=Status.INFO,
            summary=f"Found {len(sound_devices)} audio device(s): {names}.",
        ))
        for d in sound_devices:
            err_code = d.get("ConfigManagerErrorCode", 0)
            name = d.get("Name", "Unknown device")
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

    # --- Default playback / recording device + mute -------------------------
    if not data.get("cmdlets_available"):
        report.add(CheckResult(
            name="Default device / mute check",
            status=Status.SKIPPED,
            summary="Couldn't check which device is set as default, or whether it's muted.",
            detail="Requires the AudioDeviceCmdlets PowerShell module, which isn't installed.",
            recommendation="Install once with: Install-Module -Name AudioDeviceCmdlets -Scope CurrentUser",
        ))
    elif data.get("error"):
        report.add(CheckResult(
            name="Default device / mute check",
            status=Status.WARNING,
            summary="Tried to check the default audio device but it didn't work.",
            detail=data["error"],
        ))
    else:
        playback_name = data.get("playback_name")
        if not playback_name:
            report.add(CheckResult(
                name="Default playback device",
                status=Status.FAIL,
                summary="No default playback (speaker/headphone) device is set.",
                recommendation="Set a default playback device in Windows Sound settings.",
            ))
        elif data.get("playback_muted"):
            report.add(CheckResult(
                name="Default playback device",
                status=Status.FAIL,
                summary=f"'{playback_name}' is set as default output but it's muted.",
                recommendation="Unmute it in Windows Sound settings or the volume mixer.",
            ))
        else:
            report.add(CheckResult(
                name="Default playback device",
                status=Status.PASS,
                summary=f"'{playback_name}' is the default output and it's not muted.",
            ))

        recording_name = data.get("recording_name")
        if not recording_name:
            report.add(CheckResult(
                name="Default recording device",
                status=Status.FAIL,
                summary="No default microphone/recording device is set.",
                recommendation="Set a default recording device in Windows Sound settings.",
            ))
        elif data.get("recording_muted"):
            report.add(CheckResult(
                name="Default recording device",
                status=Status.FAIL,
                summary=f"'{recording_name}' is set as the default microphone but it's muted.",
                recommendation="Unmute it in Windows Sound settings.",
            ))
        else:
            report.add(CheckResult(
                name="Default recording device",
                status=Status.PASS,
                summary=f"'{recording_name}' is the default microphone and it's not muted.",
            ))

    # --- Microphone OS privacy permission ------------------------------------
    mic_global = data.get("mic_global_consent")
    if mic_global is None:
        report.add(CheckResult(
            name="Microphone privacy permission",
            status=Status.SKIPPED,
            summary="Couldn't determine the Windows microphone privacy setting.",
        ))
    elif str(mic_global).lower() == "deny":
        report.add(CheckResult(
            name="Microphone privacy permission",
            status=Status.FAIL,
            summary="Windows is blocking ALL apps from using the microphone at the system level.",
            recommendation="Settings > Privacy & security > Microphone > turn on "
                            "'Microphone access'.",
        ))
    else:
        report.add(CheckResult(
            name="Microphone privacy permission",
            status=Status.PASS,
            summary="Windows allows apps to access the microphone (system-wide setting).",
        ))

    mic_desktop = data.get("mic_desktop_consent")
    if mic_desktop is not None and str(mic_desktop).lower() == "deny":
        report.add(CheckResult(
            name="Microphone access for desktop apps",
            status=Status.FAIL,
            summary="Windows is blocking desktop apps specifically from using the microphone.",
            detail="This is a separate toggle from the general microphone privacy setting, "
                    "and it's the one that typically affects browser-based or installed "
                    "exam software rather than Windows Store apps.",
            recommendation="Settings > Privacy & security > Microphone > turn on "
                            "'Let desktop apps access your microphone'.",
        ))

    # --- Bluetooth audio device present but maybe not selected --------------
    # Windows keeps a PnP "Hands-Free" entry for every Bluetooth device
    # you've EVER paired, whether or not it's anywhere nearby right now --
    # so raw Win32_PnPEntity results are mostly noise. We only care about
    # ones that are currently an active audio endpoint (in Get-AudioDevice
    # -List), which means Windows sees it as live, not just remembered.
    bt_devices = data.get("bluetooth_audio_devices", [])
    active_devices = [d.lower() for d in data.get("active_audio_devices", [])]

    def _normalise(name: str) -> str:
        # "X Hands-Free AG" and "X Hands-Free" are two profiles of the same
        # physical device -- collapse them so we don't report it twice.
        n = name.lower().strip()
        for suffix in (" hands-free ag", " hands-free"):
            if n.endswith(suffix):
                n = n[: -len(suffix)]
        return n.strip()

    seen_bt = set()
    for bt in bt_devices:
        bt_name = bt.get("Name", "Unknown Bluetooth device")
        key = _normalise(bt_name)
        if key in seen_bt:
            continue

        is_active = any(key in ad or ad in key for ad in active_devices) if active_devices else False
        if not is_active:
            continue  # paired at some point, not currently live -- not worth flagging

        seen_bt.add(key)
        default_name = (data.get("playback_name") or "").lower()
        if default_name and key not in default_name and default_name not in key:
            report.add(CheckResult(
                name=f"Bluetooth audio device: {bt_name}",
                status=Status.WARNING,
                summary=f"A Bluetooth audio device ('{bt_name}') is currently active but "
                         f"doesn't look like the current default device.",
                detail="This is a best-effort name match, not a guarantee -- worth "
                        "a manual glance at Sound settings if audio isn't working.",
                recommendation="Confirm in Windows Sound settings which device the "
                                "exam software should actually be using.",
            ))

    return report
