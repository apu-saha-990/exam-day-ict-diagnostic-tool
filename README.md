# Exam-Day ICT Diagnostic Tool

A Windows CLI tool that checks the ICT issues most likely to disrupt a
high-stakes online or clinical exam session — audio, webcam, network,
display, input devices, driver health, and more — and prints a plain-English,
traffic-light (PASS / WARNING / FAIL) report designed to be read by a
non-technical invigilator during a live exam, not just an engineer.

Built as a targeted portfolio project for an ICT Exam Support Officer role,
based directly on the kinds of issues that role would actually need to
triage in real time.

## Status

| Category | Status |
|---|---|
| Audio | Implemented |
| Webcam / video | Planned |
| Network / connectivity | Planned |
| Display / monitor | Planned |
| Input devices | Planned |
| Driver health (system-wide) | Planned |
| Event log / system glitch detection | Planned |
| Power / battery | Planned |
| Exam software / browser | Stretch goal |

Categories are being built one at a time; each new one follows the same
pattern as `modules/audio.py`.

## Requirements

- Windows 10/11
- Python 3.10+
- PowerShell (built in on Windows)
- Optional, for full audio checks: the [AudioDeviceCmdlets](https://github.com/frgnca/AudioDeviceCmdlets)
  PowerShell module, since Windows doesn't expose "what's the current
  default audio device" through built-in WMI/CIM:
  ```powershell
  Install-Module -Name AudioDeviceCmdlets -Scope CurrentUser
  ```
  Without it, the tool still runs the driver-health and microphone privacy
  checks, and reports the default-device checks as SKIPPED with install
  instructions rather than failing.

## Usage

```
python main.py                    # interactive menu
python main.py --full             # run every implemented category
python main.py --category audio   # run one category
python main.py --export report.txt  # also write a timestamped text report
python main.py --simulate         # use mock data (for demoing off a real exam machine)
```

## How it works

The tool shells out to `powershell.exe` and reads back JSON (`modules/powershell_bridge.py`),
rather than pulling in Windows-specific Python packages, so it has zero
third-party dependencies and can run on a locked-down exam laptop where
installing packages may not be possible. It pulls hardware/driver state
from WMI (`Win32_SoundDevice`, `Win32_PnPEntity`, etc.), OS-level privacy
settings from the registry, and default-device state from the
AudioDeviceCmdlets module (see Requirements above for why).

Every check produces a `CheckResult` (`modules/report.py`) with:
- a plain-English summary (what an invigilator reads first)
- optional technical detail (for the support officer / incident report)
- optional recommendation (what to actually do about it)

Categories roll up into a traffic-light PASS/WARNING/FAIL/INFO status, and
a `--simulate` mode runs the same logic against mock data so the tool can
be tested and demonstrated on any machine, not just a Windows exam laptop.

## Known limitations (candidates for future work)

- Per-app volume mixer muting (exam software muted while the system isn't)
  needs deeper COM interop (`ISimpleAudioVolume` sessions) and isn't
  implemented yet.
- Audio enhancement / spatial sound state lives in per-driver registry
  keys that vary by manufacturer, so it isn't checked generically yet.
- Bluetooth-vs-default matching is a best-effort name comparison, not a
  guaranteed match.

## AI disclosure

Claude (Anthropic) assisted with the implementation of this tool — writing
and debugging code, structuring modules, and drafting this README — the
same way a developer would use documentation or Stack Overflow while
building something. The project's architecture, the choice of what to
check and why (based on direct knowledge of what disrupts exam sessions),
and the decisions about what to include, defer, or flag as a known gap are
my own.

— Apu Saha
