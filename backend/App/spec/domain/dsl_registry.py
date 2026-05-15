from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Protocol

from backend.App.spec.domain.dsl_block import FencedDslBlock

DslSeverity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class DslFinding:
    kind: str
    severity: DslSeverity
    message: str
    line_start: int = 0


@dataclass(frozen=True)
class DslParseResult:
    kind: str
    payload: dict[str, object] = field(default_factory=dict)
    findings: tuple[DslFinding, ...] = ()


class DslParser(Protocol):
    kind: str

    def parse(self, block: FencedDslBlock) -> DslParseResult: ...


class DslRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, DslParser] = {}

    def register(self, parser: DslParser) -> None:
        kind = parser.kind.strip()
        if not kind:
            raise ValueError("DSL parser must declare a non-empty kind")
        self._parsers[kind] = parser

    def unregister(self, kind: str) -> None:
        self._parsers.pop(kind, None)

    def is_known(self, kind: str) -> bool:
        return kind in self._parsers

    def known_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._parsers))

    def parse(self, block: FencedDslBlock) -> Optional[DslParseResult]:
        parser = self._parsers.get(block.kind)
        if parser is None:
            return None
        return parser.parse(block)


def make_default_registry() -> DslRegistry:
    from backend.App.spec.domain.dsl_invariants import InvariantsParser
    from backend.App.spec.domain.dsl_python_sig import PythonSignatureParser
    from backend.App.spec.domain.dsl_ts_sig import TypeScriptSignatureParser

    registry = DslRegistry()
    registry.register(PythonSignatureParser())
    registry.register(TypeScriptSignatureParser())
    registry.register(InvariantsParser())
    return registry


__all__ = [
    "DslFinding",
    "DslParseResult",
    "DslParser",
    "DslRegistry",
    "DslSeverity",
    "make_default_registry",
]
