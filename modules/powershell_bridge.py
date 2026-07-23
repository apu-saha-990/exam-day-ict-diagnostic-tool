"""
powershell_bridge.py

Thin wrapper around calling PowerShell from Python. Windows exposes almost
all of the hardware/driver/network state we care about through WMI (CIM)
classes and PowerShell cmdlets, but Python doesn't have first-class access
to those without extra libraries (pywin32, WMI package, etc). Rather than
add a big dependency chain, this tool shells out to `powershell.exe`
directly and reads back JSON. That keeps the tool to the Python standard
library only, which matters because it needs to run on locked-down exam
laptops where installing packages may not be possible or permitted.

Every function in the diagnostic modules should go through here rather than
calling subprocess directly, so there's one place that handles:
  - timeouts (a hung WMI query must never hang the whole exam check)
  - JSON parsing quirks (PowerShell's ConvertTo-Json returns a single
    object, not a list, when there's only one result -- this normalises
    that back to a list so calling code doesn't have to special-case it)
  - simulate mode, so the tool can be demoed/tested on a machine that
    isn't Windows, or without touching real exam hardware
"""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass
from typing import Any


class PowerShellError(Exception):
    """Raised when a PowerShell command fails or times out."""


@dataclass
class PSResult:
    success: bool
    data: Any          # parsed JSON (dict, list, or None)
    raw_stdout: str
    raw_stderr: str


def is_windows() -> bool:
    return platform.system() == "Windows"


def run_ps(command: str, timeout: int = 10) -> PSResult:
    """
    Run a PowerShell command and return the raw result.
    Does NOT attempt JSON parsing -- use run_ps_json for that.
    """
    if not is_windows():
        raise PowerShellError(
            "PowerShell is only available on Windows. "
            "Run with --simulate to use mock data on this platform."
        )

    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PowerShellError(f"PowerShell command timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise PowerShellError("powershell.exe not found on this system") from exc

    return PSResult(
        success=(completed.returncode == 0),
        data=None,
        raw_stdout=completed.stdout,
        raw_stderr=completed.stderr,
    )


def run_ps_json(command: str, timeout: int = 10) -> PSResult:
    """
    Run a PowerShell command that ends in `| ConvertTo-Json -Depth N` (or
    similar) and parse the result. Normalises single objects into a
    one-item list so calling code can always iterate.
    """
    result = run_ps(command, timeout=timeout)

    if not result.success:
        return result

    stdout = result.raw_stdout.strip()
    if not stdout:
        result.data = []
        return result

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise PowerShellError(
            f"Could not parse PowerShell output as JSON: {exc}\nOutput was:\n{stdout[:500]}"
        ) from exc

    if isinstance(parsed, dict):
        parsed = [parsed]

    result.data = parsed
    return result


def module_available(module_name: str, timeout: int = 8) -> bool:
    """Check whether a given PowerShell module is installed (not just imported)."""
    if not is_windows():
        return False
    cmd = f"if (Get-Module -ListAvailable -Name '{module_name}') {{ 'true' }} else {{ 'false' }}"
    try:
        result = run_ps(cmd, timeout=timeout)
    except PowerShellError:
        return False
    return result.success and result.raw_stdout.strip().lower() == "true"
