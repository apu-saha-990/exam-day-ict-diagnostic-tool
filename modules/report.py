"""
report.py

Shared data structures + formatting for every diagnostic category.

Design intent: an invigilator with no IT background may be the one reading
this on screen during a live exam, so the *first* thing printed for any
check is a plain-English sentence, not a device name or error code. The
technical detail is still captured (for the support officer / incident
report) but it's secondary.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Status(Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    INFO = "INFO"      # not a problem, just useful context (e.g. device list)
    SKIPPED = "SKIPPED"  # check couldn't run (e.g. missing dependency, not Windows)

    @property
    def severity(self) -> int:
        # Higher = worse. Used to roll up a category's overall status.
        return {
            Status.PASS: 0,
            Status.INFO: 0,
            Status.SKIPPED: 1,
            Status.WARNING: 2,
            Status.FAIL: 3,
        }[self]


# ANSI colours. Windows Terminal / PowerShell 7 / modern cmd all support
# these; if output isn't a real terminal (e.g. piped to a file) we skip
# colour entirely so exported reports stay plain text.
_COLOR = {
    Status.PASS: "\033[32m",     # green
    Status.WARNING: "\033[33m",  # yellow
    Status.FAIL: "\033[31m",     # red
    Status.INFO: "\033[36m",     # cyan
    Status.SKIPPED: "\033[90m",  # grey
}
_RESET = "\033[0m"


def _supports_color() -> bool:
    return sys.stdout.isatty()


@dataclass
class CheckResult:
    name: str                 # short label, e.g. "Default playback device"
    status: Status
    summary: str               # plain-English, one sentence, no jargon
    detail: str = ""           # technical detail for the support officer / log
    recommendation: str = ""   # what to do about it, if anything

    def render(self, color: bool = True) -> str:
        tag = f"[{self.status.value}]"
        if color and _supports_color():
            tag = f"{_COLOR[self.status]}{tag}{_RESET}"
        lines = [f"  {tag} {self.name}: {self.summary}"]
        if self.detail:
            lines.append(f"        detail: {self.detail}")
        if self.recommendation:
            lines.append(f"        do this: {self.recommendation}")
        return "\n".join(lines)


@dataclass
class CategoryReport:
    category: str
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, check: CheckResult) -> None:
        self.checks.append(check)

    @property
    def overall_status(self) -> Status:
        # INFO checks are context, not a verdict -- they shouldn't be able to
        # outrank a PASS just by coming first in the list. Only fall back to
        # INFO if literally every check in the category is informational.
        substantive = [c.status for c in self.checks if c.status != Status.INFO]
        pool = substantive or [c.status for c in self.checks]
        if not pool:
            return Status.SKIPPED
        return max(pool, key=lambda s: s.severity)

    def render(self, color: bool = True) -> str:
        tag = f"[{self.overall_status.value}]"
        if color and _supports_color():
            tag = f"{_COLOR[self.overall_status]}{tag}{_RESET}"
        header = f"\n{tag} {self.category}"
        body = "\n".join(c.render(color=color) for c in self.checks)
        return f"{header}\n{body}" if body else header


@dataclass
class FullReport:
    generated_at: datetime = field(default_factory=datetime.now)
    categories: list[CategoryReport] = field(default_factory=list)
    duration_seconds: float | None = None  # set by main.py once the run finishes

    def add(self, category_report: CategoryReport) -> None:
        self.categories.append(category_report)

    @property
    def overall_status(self) -> Status:
        if not self.categories:
            return Status.SKIPPED
        return max((c.overall_status for c in self.categories), key=lambda s: s.severity)

    @property
    def action_items(self) -> list[tuple[str, CheckResult]]:
        """Every FAIL/WARNING check across all categories, paired with its
        category name. This is what actually needs a human's attention --
        PASS/INFO/SKIPPED entries are reassurance, not action items."""
        items = []
        for cat in self.categories:
            for check in cat.checks:
                if check.status in (Status.FAIL, Status.WARNING):
                    items.append((cat.category, check))
        # Worst-first, so the most urgent thing an invigilator needs to
        # deal with is at the top of the list, not buried at the bottom.
        items.sort(key=lambda pair: pair[1].status.severity, reverse=True)
        return items

    @property
    def readiness_verdict(self) -> tuple[str, str]:
        """A plain-English yes/no answer to the question an invigilator
        actually has -- 'can I start the exam on this machine?' -- rather
        than making them infer it from a PASS/WARNING/FAIL tag. Returns
        (banner_text, detail_text)."""
        status = self.overall_status
        fail_count = sum(1 for _, c in self.action_items if c.status == Status.FAIL)
        warning_count = sum(1 for _, c in self.action_items if c.status == Status.WARNING)

        if status == Status.FAIL:
            return (
                "NOT READY -- fix the FAIL item(s) above first",
                f"{fail_count} thing(s) need fixing before this machine should be used "
                "for the exam. See 'WHAT TO DO' above for exactly what and where to look "
                "(each item names the specific setting or screen to check).",
            )
        if status == Status.WARNING:
            return (
                "READY, WITH CAUTION",
                f"Nothing is broken, but {warning_count} thing(s) above are worth a "
                "quick look before relying on this machine -- see 'WHAT TO DO' for "
                "exactly what and where to check.",
            )
        if status == Status.SKIPPED:
            return (
                "INCOMPLETE CHECK",
                "Some checks couldn't run (see SKIPPED entries above) -- re-run the "
                "tool, ideally as a full check, before treating this as a clean result.",
            )
        return (
            "READY -- no issues found",
            "Every check passed. This machine looks good to go for the exam.",
        )

    def render(self, color: bool = True) -> str:
        lines = [
            "=" * 60,
            "EXAM-DAY ICT DIAGNOSTIC REPORT",
            f"Generated: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        if self.duration_seconds is not None:
            lines.append(f"Check completed in {self.duration_seconds:.1f}s")
        lines.append("=" * 60)

        for cat in self.categories:
            lines.append(cat.render(color=color))

        # Plain-English wrap-up: what does a non-technical invigilator
        # actually need to DO, without re-reading every category above?
        lines.append("")
        lines.append("-" * 60)
        lines.append("WHAT TO DO")
        lines.append("-" * 60)
        items = self.action_items
        if not items:
            lines.append("Nothing needs attention -- every check passed.")
        else:
            for i, (category, check) in enumerate(items, start=1):
                tag = f"[{check.status.value}]"
                if color and _supports_color():
                    tag = f"{_COLOR[check.status]}{tag}{_RESET}"
                lines.append(f"{i}. {tag} ({category}) {check.summary}")
                if check.recommendation:
                    lines.append(f"   -> {check.recommendation}")

        # The actual yes/no answer an invigilator needs, spelled out --
        # not left for them to infer from a coloured tag.
        lines.append("")
        lines.append("=" * 60)
        banner, detail = self.readiness_verdict
        status = self.overall_status
        banner_display = banner
        if color and _supports_color():
            banner_display = f"{_COLOR[status]}{banner}{_RESET}"
        lines.append(f"VERDICT: {banner_display}")
        lines.append(detail)
        lines.append("=" * 60)
        return "\n".join(lines)

    def export(self, path: str | None = None) -> str:
        """Write a plain-text (no ANSI colour) copy of the report to disk.
        Returns the path written to."""
        if path is None:
            stamp = self.generated_at.strftime("%Y%m%d_%H%M%S")
            path = f"exam_ict_report_{stamp}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.render(color=False))
            f.write("\n")
        return path
