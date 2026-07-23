#!/usr/bin/env python3
"""
Exam-Day ICT Diagnostic Tool
=============================

A Windows CLI tool that checks the most common technical issues that can
disrupt a high-stakes online/clinical exam session -- audio, webcam,
network, display, input devices, driver health, etc -- and prints a
plain-English, traffic-light (PASS / WARNING / FAIL) report that a
non-technical invigilator can read and act on.

Usage:
    python main.py                  Interactive menu
    python main.py --full           Run every category, no menu
    python main.py --category audio Run a single category
    python main.py --export out.txt Also write a timestamped text report
    python main.py --simulate       Use mock data (for testing/demo off Windows)

Categories currently implemented: audio
Remaining categories (webcam, network, display, input devices, driver
health, event log, power, browser/exam software) are being built out
one at a time -- see modules/ for the pattern each one follows.
"""

from __future__ import annotations

import argparse
import sys

from modules.report import FullReport
from modules import audio as audio_module
from modules import webcam as webcam_module
from modules import network as network_module
from modules.powershell_bridge import is_windows

CATEGORY_MODULES = {
    "audio": audio_module,
    "webcam": webcam_module,
    "network": network_module,
    # "display": display_module,
    # "input": input_module,
    # "drivers": drivers_module,
    # "system_logs": system_logs_module,
    # "power": power_module,
}


def run_category(name: str, simulate: bool):
    module = CATEGORY_MODULES[name]
    return module.run(simulate=simulate)


def run_all(simulate: bool) -> FullReport:
    report = FullReport()
    for name in CATEGORY_MODULES:
        print(f"Running {name} checks...", file=sys.stderr)
        report.add(run_category(name, simulate))
    return report


def interactive_menu(simulate: bool):
    while True:
        print("\nExam-Day ICT Diagnostic Tool")
        print("-----------------------------")
        print("1) Run full check (all categories)")
        for i, name in enumerate(CATEGORY_MODULES, start=2):
            print(f"{i}) {name.capitalize()} only")
        print("0) Exit")
        choice = input("\nSelect an option: ").strip()

        if choice == "0":
            return None
        if choice == "1":
            return run_all(simulate)

        try:
            idx = int(choice) - 2
            name = list(CATEGORY_MODULES.keys())[idx]
        except (ValueError, IndexError):
            print("Not a valid option, try again.")
            continue

        report = FullReport()
        report.add(run_category(name, simulate))
        return report


def main():
    parser = argparse.ArgumentParser(description="Exam-Day ICT Diagnostic Tool")
    parser.add_argument("--full", action="store_true", help="Run every category without the menu")
    parser.add_argument("--category", choices=list(CATEGORY_MODULES.keys()), help="Run a single category")
    parser.add_argument("--export", metavar="PATH", nargs="?", const="", help="Export report to a text file")
    parser.add_argument("--simulate", action="store_true", help="Use mock data instead of querying real hardware")
    args = parser.parse_args()

    if not args.simulate and not is_windows():
        print(
            "This machine isn't running Windows, so real hardware checks aren't "
            "available here. Re-run with --simulate to see the tool work against "
            "mock data, or run it on a Windows exam laptop for real results.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.full:
        report = run_all(args.simulate)
    elif args.category:
        report = FullReport()
        report.add(run_category(args.category, args.simulate))
    else:
        report = interactive_menu(args.simulate)

    if report is None:
        return

    print(report.render(color=True))

    if args.export is not None:
        path = args.export or None
        written_to = report.export(path)
        print(f"\nReport exported to: {written_to}")


if __name__ == "__main__":
    main()
