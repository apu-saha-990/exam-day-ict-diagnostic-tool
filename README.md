# Exam-Day ICT Diagnostic Tool

A tool that checks a Windows computer for the technical problems most likely to disrupt an online or in-person exam, before they happen.

## What Problem Does This Solve

If a candidate's microphone doesn't work, or their webcam gets grabbed by another program, or Sticky Keys switches on halfway through typing an answer — a high-stakes clinical exam can be disrupted, and the person dealing with it in that moment usually isn't a network engineer. It's an invigilator, standing in a room, with no time to go digging through Windows settings menus. This tool solves that by running fast, right before or during the exam, and telling that person in plain language exactly what's wrong and what to do about it — colour coded, so nothing gets missed. Less time spent guessing means less time the exam itself is stuck waiting.

## How It Works

1. Someone runs the tool on the exam computer — checking everything at once, or one thing at a time.
2. It looks at the microphone and speakers, the camera, the internet connection, the screen, the mouse and keyboard, and the overall health of every device on the machine.
3. Each individual check prints one plain sentence explaining what it found, with a colour: green means fine, yellow means worth a look, red means fix it first.
4. At the end, it prints a short "what to do" list — only the things that actually need attention, not everything it checked.
5. It finishes with one plain sentence: is this computer ready for the exam, or not.
6. The whole thing takes well under a minute, timed automatically, since it's meant to be run right before a live exam starts.

## What's Built

* **Sound check** — checks the microphone and speakers are plugged in, working, and not muted, and that Windows hasn't quietly blocked apps from using the microphone. *(Audio module)*
* **Camera check** — checks the webcam is detected and working, that privacy settings aren't blocking it, and whether another program like Teams or Zoom already has the camera locked — the single most common reason a camera "doesn't work." *(Webcam module)*
* **Internet check** — checks the computer is actually connected, whether it's on shaky Wi-Fi when a wired connection is sitting right there unused, whether it can reach the internet (not just the home router), whether website names resolve properly, how much lag and dropped data there is, and roughly how fast the connection is. *(Network module)*
* **Screen check** — checks how many monitors are connected, what resolution and scaling they're set to, and whether the graphics driver is healthy. *(Display module)*
* **Mouse and keyboard check** — checks both are detected and working, flags accessibility shortcuts (like Sticky Keys) that get triggered by accident, and includes a genuine live test: click a button, press a key, and it actually tells you which one — if either — isn't working. *(Input Devices module)*
* **Whole-computer device check** — scans every single device Windows knows about, not just the ones above, and flags anything with a broken or missing driver, including devices Windows can't even identify. *(Driver Health module)*
* **Recent problems check** — looks at the Windows event log for the last day, checks whether the computer has crashed or rebooted unexpectedly in the last week, and checks how much memory, processing power, and disk space are free right now. *(System/Event Log module)*

## Why I Built This From Scratch

I wanted to prove I actually understand this specific job, not just that I can follow a tutorial. Anyone can list "troubleshooting skills" on a resume. Building something that checks the exact problems an exam support officer deals with, and explains them the way I'd actually explain them to an invigilator standing in front of me, is a much harder thing to fake.

## A Bug I Found

I built a check for screen scaling, since a screen set to the wrong zoom level can make exam software render incorrectly. The first version read the scaling using a standard .NET method (`System.Drawing.Graphics.DpiX`) and it worked — no errors, a clean number came back, `100%`.

Except it was wrong. My actual laptop screen was set to `125%` scaling. I only caught it because the resolution the tool reported (3072×1728) looked oddly specific — I recognised it as the exact number a 4K screen shows when it's scaled to 125%. So I went into Windows Settings and checked manually. Sure enough: 125%, not 100%.

The problem was that the method I used isn't "DPI-aware." Windows has a compatibility feature where, if a program doesn't explicitly say it understands screen scaling, Windows lies to it and always reports the old default of 96 DPI — which looks like 100% — no matter what the real setting is. My code never crashed, never threw a warning, it just confidently returned the wrong answer.

The fix was to tell Windows, explicitly, that my tool understands per-monitor scaling, using a lower-level Windows function instead of the easy .NET shortcut:

```powershell
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class DpiHelper {
    \[DllImport("user32.dll")]
    public static extern bool SetProcessDpiAwarenessContext(int dpiContext);
    \[DllImport("user32.dll")]
    public static extern uint GetDpiForSystem();
}
"@
\[void]\[DpiHelper]::SetProcessDpiAwarenessContext(-4)  # PER\_MONITOR\_AWARE\_V2
$dpi = \[DpiHelper]::GetDpiForSystem()
```

After that, the tool correctly reported 125%, matching Windows Settings exactly.

What I learned: a check that runs without error isn't the same as a check that's correct. I only found this because I tested it against something I could verify myself, by eye, rather than trusting that "it ran, so it must be right." Now I default to asking what a check is actually measuring, and whether I have any independent way to confirm the number it gives me.

## How I Built This

I'm a career changer — I came from restaurant kitchens and food manufacturing, not a computer science degree or a bootcamp. I use AI (Claude) throughout development — as a learning tool, code reviewer, and debugging partner. Every terminal error went back to Claude. The decisions are mine. The systems run.

What that meant in practice on this project: I decided which categories mattered and why, based on what actually goes wrong during an exam, not from a generic checklist. I decided the tone every message should take, because I know who's actually going to be reading this on the day. I ran every single check on my own machine, on purpose — not just once, but by deliberately breaking things (unplugging a mic, opening a camera app, disabling Sticky Keys, disconnecting a cable) to see if the tool actually caught it. The DPI bug above is the clearest example: I caught it because I checked the real setting myself, not because the code told me something was wrong.

This project is really a demonstration of how I'd actually work in the role: find the real problem, build something practical for it, test it properly instead of assuming it works, and explain the result in plain language to whoever needs to act on it.

## What I Learned

* A check passing without an error is not the same as a check being correct — see the DPI bug above.
* Testing on real hardware finds things simulated/mock data never will. My mock data for the network check used "Disconnected" for an idle adapter; my real laptop showed "Not Present" instead, which meant something different and needed different advice — I only found that by running it for real.
* Two independent checks agreeing with each other is a good sign the checks themselves are trustworthy. My whole-computer device scan and my network check both independently flagged the same disabled Ethernet adapter, using two completely different Windows data sources.
* Some Windows background noise looks like an error but isn't. A completely healthy computer still logs entries that look alarming (like "DistributedCOM" errors) — a good diagnostic tool has to know the difference, not just report every log entry as if it's equally serious.
* Writing for a specific reader changes the writing. Early on I used the word "critical" in a warning message, copying Windows' own internal terminology, and it read as far more alarming than the actual situation — I had to go back and reword it once I thought about an invigilator, not a developer, reading it cold.

## Running It

```powershell
# Optional -- enables full audio checks (Windows doesn't expose default audio device state any other way)
Install-Module -Name AudioDeviceCmdlets -Scope CurrentUser

python main.py                      # interactive menu
python main.py --full               # run every category
python main.py --category audio     # run one category: audio, webcam, network, display, input, drivers, system\_logs
python main.py --export report.txt  # also save a timestamped text report
python main.py --simulate           # run against fake data, no real Windows machine needed
python main.py --no-interactive     # skip the live mouse/keyboard click-and-type test
```

## What's Next

* Add a log that records every check run, with a timestamp, so there's a clear record if an exam session is ever disputed afterward.
* Add a check that flags when security-relevant settings — camera/microphone permissions, in particular — have changed from a known-safe baseline since the last time this machine was checked.
* Add continuous monitoring during the exam itself, not just a one-time check beforehand, so a fault that appears mid-exam gets caught too, not only one that was already present at the start.
* Add a way to compare today's result against the last time this exact machine was checked, to catch things drifting in the wrong direction before they become a real problem.
* Add a way to save the exported report so it can't be silently edited afterward, so the text file itself can be trusted as evidence if something goes wrong during a real exam.

## Tech Stack

|What it does|Technology|
|-|-|
|Runs on the exam computer|Windows 10/11|
|Main program logic|Python 3.10+|
|Reads hardware, driver, network, and registry state|PowerShell (via WMI/CIM)|
|Reads the current default audio device and mute state|AudioDeviceCmdlets (PowerShell module)|
|Live mouse click test|tkinter (built into Python)|
|Live keyboard press test|msvcrt (built into Python, Windows-only)|
|Version control / hosting|Git, GitHub|

## Project Structure

```
exam\_diagnostic\_tool/
├── main.py
├── README.md
├── .gitignore
└── modules/
    ├── \_\_init\_\_.py
    ├── powershell\_bridge.py
    ├── report.py
    ├── audio.py
    ├── webcam.py
    ├── network.py
    ├── display.py
    ├── input\_devices.py
    ├── driver\_health.py
    └── event\_log.py
```

## Why Not Use An Existing Tool

Windows already has separate troubleshooters buried in different settings menus — one for sound, one for network, one for camera privacy — each written for one person fixing their own computer, in their own time. None of them are built for someone else's computer, under time pressure, in the middle of a live exam. I built this to pull the relevant checks into one thing that runs fast and explains itself in plain language to whoever's actually standing there when something breaks.

