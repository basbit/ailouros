
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


@dataclass
class Defect:

    id: str = field(default_factory=lambda: f"DEF-{uuid.uuid4().hex[:8]}")
    title: str = ""
    severity: Severity = Severity.P1
    file_paths: list[str] = field(default_factory=list)
    expected: str = ""
    actual: str = ""
    repro_steps: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    category: str = ""  # e.g. "namespace", "stub", "missing_file", "regression", "logic"
    fixed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity.value,
            "file_paths": self.file_paths,
            "expected": self.expected,
            "actual": self.actual,
            "repro_steps": self.repro_steps,
            "acceptance": self.acceptance,
            "category": self.category,
            "fixed": self.fixed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Defect:
        sev = data.get("severity", "P1")
        return cls(
            id=data.get("id", f"DEF-{uuid.uuid4().hex[:8]}"),
            title=data.get("title", ""),
            severity=Severity(sev) if sev in ("P0", "P1", "P2") else Severity.P1,
            file_paths=list(data.get("file_paths") or []),
            expected=data.get("expected", ""),
            actual=data.get("actual", ""),
            repro_steps=list(data.get("repro_steps") or []),
            acceptance=list(data.get("acceptance") or []),
            category=data.get("category", ""),
            fixed=bool(data.get("fixed")),
        )


@dataclass
class DefectReport:

    defects: list[Defect] = field(default_factory=list)
    test_scenarios: list[str] = field(default_factory=list)
    edge_cases: list[str] = field(default_factory=list)
    regression_checks: list[str] = field(default_factory=list)

    @property
    def open_p0(self) -> list[Defect]:
        return [d for d in self.defects if d.severity == Severity.P0 and not d.fixed]

    @property
    def open_p1(self) -> list[Defect]:
        return [d for d in self.defects if d.severity == Severity.P1 and not d.fixed]

    @property
    def has_blockers(self) -> bool:
        return bool(self.open_p0 or self.open_p1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "defects": [d.to_dict() for d in self.defects],
            "test_scenarios": self.test_scenarios,
            "edge_cases": self.edge_cases,
            "regression_checks": self.regression_checks,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DefectReport:
        return cls(
            defects=[Defect.from_dict(d) for d in (data.get("defects") or [])],
            test_scenarios=list(data.get("test_scenarios") or []),
            edge_cases=list(data.get("edge_cases") or []),
            regression_checks=list(data.get("regression_checks") or []),
        )

    def merge(self, other: DefectReport) -> None:
        existing_ids = {d.id for d in self.defects}
        for defect in other.defects:
            if defect.id not in existing_ids:
                self.defects.append(defect)
        self.test_scenarios.extend(other.test_scenarios)
        self.edge_cases.extend(other.edge_cases)
        self.regression_checks.extend(other.regression_checks)


def cluster_defects(defects: list[Defect]) -> dict[str, list[Defect]]:
    clusters: dict[str, list[Defect]] = {}
    for defect in defects:
        key = defect.category or "uncategorized"
        clusters.setdefault(key, []).append(defect)
    return clusters


def parse_defects_from_text(text: str) -> list[Defect]:
    import re

    defects: list[Defect] = []
    blocks = re.split(r"(?:^|\n)DEFECT\s*:\s*", text, flags=re.IGNORECASE)
    for block in blocks[1:]:  # skip text before first DEFECT
        lines = block.strip().split("\n")
        title = lines[0].strip() if lines else ""
        severity = Severity.P1
        file_paths: list[str] = []
        expected = ""
        actual = ""
        category = ""

        for line in lines[1:]:
            line_stripped = line.strip()
            upper = line_stripped.upper()
            if upper.startswith("SEVERITY:"):
                sev = line_stripped.split(":", 1)[1].strip().upper()
                if sev in ("P0", "P1", "P2"):
                    severity = Severity(sev)
            elif upper.startswith("FILES:") or upper.startswith("FILE_PATHS:"):
                paths_str = line_stripped.split(":", 1)[1].strip()
                file_paths = [p.strip() for p in paths_str.split(",") if p.strip()]
            elif upper.startswith("EXPECTED:"):
                expected = line_stripped.split(":", 1)[1].strip()
            elif upper.startswith("ACTUAL:"):
                actual = line_stripped.split(":", 1)[1].strip()
            elif upper.startswith("CATEGORY:"):
                category = line_stripped.split(":", 1)[1].strip()

        if title:
            defects.append(Defect(
                title=title,
                severity=severity,
                file_paths=file_paths,
                expected=expected,
                actual=actual,
                category=category,
            ))

    return defects


def parse_defect_report(text: str) -> DefectReport:
    import json
    import re

    visible_text = re.sub(r"```.*?```", "", text or "", flags=re.DOTALL)
    match = re.search(
        r"<defect_report>\s*(.*?)\s*</defect_report>",
        visible_text,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return DefectReport.from_dict(data)
        except (json.JSONDecodeError, TypeError, ValueError):
            return DefectReport()

    defects = parse_defects_from_text(visible_text)
    if defects:
        return DefectReport(defects=defects)
    return DefectReport()
