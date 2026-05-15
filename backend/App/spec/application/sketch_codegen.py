from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backend.App.spec.domain.ports import LLMClient
from backend.App.spec.domain.sketch_filler import apply_filled_bodies
from backend.App.spec.domain.sketch_holes import (
    Hole,
    NoHolesFoundError,
    compare_public_surface,
    extract_holes,
)


class SketchCodegenError(Exception):
    pass


class SignaturePreservationError(SketchCodegenError):
    def __init__(self, differences: tuple[str, ...]) -> None:
        joined = "; ".join(differences)
        super().__init__(
            f"LLM response changed the public surface: {joined}"
        )
        self.differences = differences


class LLMResponseParseError(SketchCodegenError):
    pass


@dataclass(frozen=True)
class SketchCodegenOutcome:
    filled_source: str
    holes_filled: int
    hole_qualnames: tuple[str, ...]


def _build_sketch_prompt(sketch_text: str, holes: tuple[Hole, ...]) -> str:
    hole_instructions = "\n".join(
        f"- Fill the body of `{h.qualname}` with signature `{h.signature}`"
        for h in holes
    )
    return (
        "You are a code generation assistant. "
        "Fill in the body of each marked function in the sketch below.\n\n"
        "## Sketch\n\n"
        f"```python\n{sketch_text}\n```\n\n"
        "## Instructions\n\n"
        f"{hole_instructions}\n\n"
        "Return a JSON object mapping each qualname to its body. "
        "The body should be indented by 4 spaces. "
        "Do NOT change any function signatures, class names, or structure. "
        "Example format:\n"
        '{"ClassName.method_name": "    return value"}\n\n'
        "Return ONLY the JSON object. No markdown, no extra text."
    )


def _parse_llm_json(response: str) -> dict[str, str]:
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        text = "\n".join(inner).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMResponseParseError(
            f"LLM response is not valid JSON: {exc}. Response was: {text[:200]!r}"
        ) from exc

    if not isinstance(parsed, dict):
        raise LLMResponseParseError(
            f"LLM response JSON must be a dict, got {type(parsed).__name__}"
        )

    result: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise LLMResponseParseError(
                f"LLM response dict must map str→str; got key={key!r} value={value!r}"
            )
        result[key] = value

    return result


def run_sketch_codegen(
    request: Any,
    sketch_text: str,
    *,
    llm_client: LLMClient,
) -> SketchCodegenOutcome:
    try:
        holes = extract_holes(sketch_text)
    except NoHolesFoundError as exc:
        raise SketchCodegenError(str(exc)) from exc

    prompt = _build_sketch_prompt(sketch_text, holes)

    model = getattr(request, "model_name", "stub")
    seed = getattr(request, "seed", 0)

    try:
        raw_response = llm_client.generate(prompt, model=model, seed=seed)
    except Exception as exc:
        raise SketchCodegenError(f"LLM call failed: {exc}") from exc

    filled_bodies = _parse_llm_json(raw_response)

    try:
        result_source = apply_filled_bodies(sketch_text, filled_bodies)
    except Exception as exc:
        raise SketchCodegenError(f"Failed to apply filled bodies: {exc}") from exc

    differences = compare_public_surface(sketch_text, result_source)
    if differences:
        raise SignaturePreservationError(differences)

    filled_qualnames = tuple(filled_bodies.keys())
    return SketchCodegenOutcome(
        filled_source=result_source,
        holes_filled=len(filled_qualnames),
        hole_qualnames=filled_qualnames,
    )


__all__ = [
    "LLMResponseParseError",
    "SignaturePreservationError",
    "SketchCodegenError",
    "SketchCodegenOutcome",
    "run_sketch_codegen",
]
