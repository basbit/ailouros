from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


_DEFECT_BLOCK = re.compile(
    r"<defect>\s*(.*?)\s*</defect>",
    re.DOTALL | re.IGNORECASE,
)
_FILE_LINE = re.compile(r"^\s*file\s*:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_SEVERITY_LINE = re.compile(
    r"^\s*severity\s*:\s*(critical|high|medium|low)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SUMMARY_LINE = re.compile(r"^\s*summary\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class DefectEntry:
    defect_id: str
    file: str
    severity: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairContract:
    defects: tuple[DefectEntry, ...]
    files_to_touch: tuple[str, ...]

    def is_empty(self) -> bool:
        return not self.defects

    def to_dict(self) -> dict[str, Any]:
        return {
            "defects": [defect.to_dict() for defect in self.defects],
            "files_to_touch": list(self.files_to_touch),
        }


@dataclass
class RepairProgress:
    fixed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    worse: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixed": list(self.fixed),
            "unchanged": list(self.unchanged),
            "worse": list(self.worse),
        }


def _strip_path(text: str) -> str:
    cleaned = (text or "").strip().strip("\"'")
    while cleaned.startswith(("./", "/")):
        cleaned = cleaned.lstrip("./").lstrip("/")
    return cleaned


def parse_review_defects(review_text: str) -> tuple[DefectEntry, ...]:
    if not isinstance(review_text, str) or not review_text:
        return ()
    blocks = _DEFECT_BLOCK.findall(review_text)
    if not blocks:
        return ()
    parsed: list[DefectEntry] = []
    for index, block in enumerate(blocks):
        file_match = _FILE_LINE.search(block)
        severity_match = _SEVERITY_LINE.search(block)
        summary_match = _SUMMARY_LINE.search(block)
        if file_match is None:
            continue
        target_file = _strip_path(file_match.group(1))
        if not target_file:
            continue
        parsed.append(
            DefectEntry(
                defect_id=f"d{index + 1:02d}",
                file=target_file,
                severity=(severity_match.group(1) if severity_match else "medium").lower(),
                summary=(summary_match.group(1).strip() if summary_match else ""),
            )
        )
    return tuple(parsed)


def build_repair_contract(review_text: str) -> RepairContract:
    defects = parse_review_defects(review_text)
    files_to_touch = tuple(sorted({defect.file for defect in defects}))
    return RepairContract(defects=defects, files_to_touch=files_to_touch)


def evaluate_retry_progress(
    contract: RepairContract,
    written_files: list[str],
    new_review_text: str,
) -> RepairProgress:
    progress = RepairProgress()
    if contract.is_empty():
        return progress
    new_defects = parse_review_defects(new_review_text)
    new_by_file: dict[str, list[DefectEntry]] = {}
    for defect in new_defects:
        new_by_file.setdefault(defect.file, []).append(defect)
    written_set = {_strip_path(path) for path in written_files or []}
    for defect in contract.defects:
        target = defect.file
        same_file_in_new = new_by_file.get(target, [])
        if not same_file_in_new and target in written_set:
            progress.fixed.append(defect.defect_id)
            continue
        if not same_file_in_new and target not in written_set:
            progress.unchanged.append(defect.defect_id)
            continue
        if same_file_in_new and target not in written_set:
            progress.unchanged.append(defect.defect_id)
            continue
        if any(
            other.severity in {"critical", "high"} and defect.severity not in {"critical", "high"}
            for other in same_file_in_new
        ):
            progress.worse.append(defect.defect_id)
        else:
            progress.unchanged.append(defect.defect_id)
    return progress


def retry_should_block(
    contract: RepairContract,
    written_files: list[str],
) -> Optional[str]:
    if contract.is_empty():
        return None
    if not written_files:
        return (
            "repair_contract: no files written on retry — "
            f"expected one of {list(contract.files_to_touch)}"
        )
    written_set = {_strip_path(path) for path in written_files}
    targets = set(contract.files_to_touch)
    if written_set.isdisjoint(targets):
        return (
            "repair_contract: retry touched no implicated file; "
            f"expected one of {sorted(targets)}, got {sorted(written_set)}"
        )
    return None
