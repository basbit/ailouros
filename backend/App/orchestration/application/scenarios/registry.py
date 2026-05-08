from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Optional

from backend.App.orchestration.domain.scenarios.errors import (
    ScenarioInvalid,
    ScenarioNotFound,
    ScenarioRegistryError,
)
from backend.App.orchestration.domain.scenarios.scenario import Scenario
from backend.App.orchestration.domain.scenarios.validation import validate_scenario_payload

logger = logging.getLogger(__name__)


class ScenarioRegistry:
    def __init__(
        self,
        loader: Any = None,
        known_step_ids_factory: Optional[Callable[[], frozenset[str]]] = None,
    ) -> None:
        self._loader = loader
        self._known_step_ids_factory = known_step_ids_factory
        self._cache: Optional[dict[str, Scenario]] = None

    def _get_loader(self) -> Any:
        if self._loader is not None:
            return self._loader
        from backend.App.orchestration.infrastructure.scenario_loader import ScenarioFileLoader
        return ScenarioFileLoader()

    def _get_known_step_ids(self) -> frozenset[str]:
        if self._known_step_ids_factory is not None:
            return self._known_step_ids_factory()
        from backend.App.orchestration.application.routing.step_registry import PIPELINE_STEP_REGISTRY
        return frozenset(PIPELINE_STEP_REGISTRY.keys())

    def _load(self) -> dict[str, Scenario]:
        if self._cache is not None:
            return self._cache
        known = self._get_known_step_ids()
        loader = self._get_loader()
        registry: dict[str, Scenario] = {}
        for path, payload in loader.load_all():
            try:
                scenario = validate_scenario_payload(payload, known)
            except ScenarioInvalid as exc:
                logger.warning(
                    "scenario_registry: skipping invalid scenario in %s: %s",
                    path,
                    exc,
                )
                continue
            if scenario.id in registry:
                raise ScenarioRegistryError(
                    f"Duplicate scenario id {scenario.id!r} found in {path}"
                )
            registry[scenario.id] = scenario
        self._cache = registry
        return registry

    def list_all(self) -> list[Scenario]:
        return list(self._load().values())

    def get(self, scenario_id: str) -> Scenario:
        registry = self._load()
        if scenario_id not in registry:
            raise ScenarioNotFound(f"Scenario {scenario_id!r} not found")
        return registry[scenario_id]

    def reload(self) -> None:
        self._cache = None


@functools.lru_cache(maxsize=1)
def default_scenario_registry() -> ScenarioRegistry:
    return ScenarioRegistry()
