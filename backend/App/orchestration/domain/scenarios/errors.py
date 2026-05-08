from __future__ import annotations


class ScenarioError(Exception):
    pass


class ScenarioNotFound(ScenarioError):
    pass


class ScenarioInvalid(ScenarioError):
    pass


class ScenarioRegistryError(ScenarioError):
    pass
