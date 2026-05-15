from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from backend.App.shared.domain.exceptions import DomainError

_T = TypeVar("_T", bound=BaseModel)

_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL,
)

_EXCERPT_LIMIT = 512


class StructuredOutputError(DomainError):
    def __init__(
        self,
        model_name: str,
        attempt: int,
        validation_errors: tuple[str, ...],
        last_response_excerpt: str,
    ) -> None:
        self.model_name = model_name
        self.attempt = attempt
        self.validation_errors = validation_errors
        self.last_response_excerpt = last_response_excerpt
        errors_str = "; ".join(validation_errors) if validation_errors else "<no details>"
        super().__init__(
            f"StructuredOutputError: model={model_name!r} attempt={attempt} "
            f"validation_errors=[{errors_str}] "
            f"last_response_excerpt={last_response_excerpt[:200]!r}"
        )


def _strip_fences(text: str) -> str:
    if not text:
        return text
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _format_pydantic_errors(exc: ValidationError) -> tuple[str, ...]:
    out: list[str] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        path = ".".join(str(p) for p in loc) if loc else "<root>"
        msg = err.get("msg", "invalid")
        etype = err.get("type", "")
        out.append(f"{path}: {msg} (type={etype})")
    return tuple(out)


def parse_structured(response_text: str, schema: type[_T]) -> _T:
    excerpt = (response_text or "")[:_EXCERPT_LIMIT]
    cleaned = _strip_fences(response_text or "")
    if not cleaned:
        raise StructuredOutputError(
            model_name=schema.__name__,
            attempt=1,
            validation_errors=("<root>: empty response (type=empty_response)",),
            last_response_excerpt=excerpt,
        )
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            model_name=schema.__name__,
            attempt=1,
            validation_errors=(
                f"<root>: invalid JSON at line {exc.lineno} col {exc.colno}: {exc.msg} "
                f"(type=json_decode_error)",
            ),
            last_response_excerpt=excerpt,
        ) from exc
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise StructuredOutputError(
            model_name=schema.__name__,
            attempt=1,
            validation_errors=_format_pydantic_errors(exc),
            last_response_excerpt=excerpt,
        ) from exc


__all__ = [
    "StructuredOutputError",
    "parse_structured",
]
