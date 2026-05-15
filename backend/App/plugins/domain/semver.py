from __future__ import annotations

import re
from dataclasses import dataclass


class SemverError(ValueError):
    pass


def _parse_version(v: str) -> tuple[int, int, int]:
    v = v.strip().lstrip("v")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[.\-+].*)?", v)
    if not match:
        raise SemverError(f"Cannot parse semver version '{v}'")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _caret_upper(major: int, minor: int, patch: int) -> tuple[int, int, int]:
    if major > 0:
        return (major + 1, 0, 0)
    if minor > 0:
        return (0, minor + 1, 0)
    return (0, 0, patch + 1)


def _single_matches(constraint: str, version_tuple: tuple[int, int, int]) -> bool:
    constraint = constraint.strip()
    if not constraint:
        return True

    for op in (">=", "<=", "!=", "==", ">", "<", "="):
        if constraint.startswith(op):
            ver_str = constraint[len(op):].strip()
            required = _parse_version(ver_str)
            if op in ("==", "="):
                return version_tuple == required
            if op == ">=":
                return version_tuple >= required
            if op == ">":
                return version_tuple > required
            if op == "<=":
                return version_tuple <= required
            if op == "<":
                return version_tuple < required
            if op == "!=":
                return version_tuple != required

    if constraint.startswith("^"):
        ver_str = constraint[1:].strip()
        lower = _parse_version(ver_str)
        upper = _caret_upper(*lower)
        return lower <= version_tuple < upper

    if constraint.startswith("~"):
        ver_str = constraint[1:].strip()
        lower = _parse_version(ver_str)
        upper = (lower[0], lower[1] + 1, 0)
        return lower <= version_tuple < upper

    required = _parse_version(constraint)
    return version_tuple == required


@dataclass(frozen=True)
class SemverRange:
    _raw: str

    def matches(self, version: str) -> bool:
        if not self._raw.strip():
            return True
        version_tuple = _parse_version(version)
        parts = [p.strip() for p in self._raw.split(",") if p.strip()]
        if not parts:
            return True
        return all(_single_matches(part, version_tuple) for part in parts)

    @staticmethod
    def parse(raw: str) -> "SemverRange":
        return SemverRange(_raw=raw)

    def __str__(self) -> str:
        return self._raw
