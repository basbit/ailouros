from __future__ import annotations

import textwrap

from backend.App.spec.domain.sketch_holes import extract_holes


class HoleNotFoundError(KeyError):
    def __init__(self, qualname: str) -> None:
        super().__init__(f"qualname {qualname!r} not found in source")
        self.qualname = qualname


class NoFilledBodyError(ValueError):
    def __init__(self) -> None:
        super().__init__("filled_bodies must not be empty")


def _body_indent(source_lines: list[str], body_lineno_start: int) -> str:
    line = source_lines[body_lineno_start - 1]
    return line[: len(line) - len(line.lstrip())]


def _reindent_body(body_text: str, target_indent: str) -> str:
    stripped = textwrap.dedent(body_text)
    result_lines: list[str] = []
    for line in stripped.splitlines():
        if line.strip():
            result_lines.append(target_indent + line)
        else:
            result_lines.append("")
    return "\n".join(result_lines)


def apply_filled_bodies(original: str, filled_bodies: dict[str, str]) -> str:
    if not filled_bodies:
        raise NoFilledBodyError()

    holes = extract_holes(original)
    hole_map = {h.qualname: h for h in holes}

    for qualname in filled_bodies:
        if qualname not in hole_map:
            raise HoleNotFoundError(qualname)

    source_lines = original.splitlines(keepends=True)

    sorted_qualnames = sorted(
        filled_bodies.keys(),
        key=lambda q: hole_map[q].body_lineno_start,
        reverse=True,
    )

    for qualname in sorted_qualnames:
        hole = hole_map[qualname]
        body_text = filled_bodies[qualname]

        target_indent = _body_indent(source_lines, hole.body_lineno_start)
        reindented = _reindent_body(body_text, target_indent)

        start_idx = hole.body_lineno_start - 1
        end_idx = hole.body_lineno_end

        new_lines = (reindented + "\n").splitlines(keepends=True)
        source_lines[start_idx:end_idx] = new_lines

    return "".join(source_lines)


__all__ = [
    "HoleNotFoundError",
    "NoFilledBodyError",
    "apply_filled_bodies",
]
