from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


_TOP_LEVEL_DEF = re.compile(r"^(def |class |async def |@)")
_NESTED_DEF = re.compile(r"^(\s+)(def |class |async def |@)")


def _normalize_text(text: str) -> str:
    lines = text.splitlines(keepends=False)
    output: list[str] = []
    previous_blank_run = 0
    for index, line in enumerate(lines):
        is_blank = line.strip() == ""
        if _TOP_LEVEL_DEF.match(line) and output:
            non_blank_above = next(
                (
                    candidate
                    for candidate in reversed(output)
                    if candidate.strip() != ""
                ),
                "",
            )
            if non_blank_above and not non_blank_above.startswith("@"):
                while output and output[-1].strip() == "":
                    output.pop()
                output.append("")
                output.append("")
        if is_blank:
            previous_blank_run += 1
            if previous_blank_run > 2:
                continue
        else:
            previous_blank_run = 0
        output.append(line)
    text_out = "\n".join(output)
    if not text_out.endswith("\n"):
        text_out += "\n"
    return text_out


def process(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    cleaned = _normalize_text(original)
    if cleaned != original:
        path.write_text(cleaned, encoding="utf-8")
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ensure 2 blank lines between top-level defs/classes.")
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    changed = 0
    for raw in args.paths:
        root = Path(raw)
        targets = (
            [root]
            if root.is_file()
            else [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]
        )
        for path in targets:
            if process(path):
                changed += 1
    print(f"{changed} file(s) changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
