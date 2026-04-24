"""Tests for AgentFactoryPort (domain) and ConcreteAgentFactory (infrastructure)."""
from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from backend.App.orchestration.domain.agent_factory import AgentFactoryPort
from backend.App.orchestration.infrastructure.agent_factory import ConcreteAgentFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_ROLES = [
    "reviewer", "pm", "dev", "qa", "ba",
    "arch", "devops", "stack_reviewer", "dev_lead",
]


def _mock_registry(**overrides: MagicMock) -> dict[str, MagicMock]:
    """Return a registry dict where every role maps to a MagicMock class."""
    registry = {role: MagicMock() for role in _ALL_ROLES}
    registry.update(overrides)
    return registry


# ---------------------------------------------------------------------------
# 1. known role returns an instance of the mocked class
# ---------------------------------------------------------------------------
def test_known_role_returns_instance():
    mock_pm_cls = MagicMock()
    mock_pm_instance = MagicMock()
    mock_pm_cls.return_value = mock_pm_instance

    registry = _mock_registry(pm=mock_pm_cls)
    factory = ConcreteAgentFactory()

    with patch.object(
        type(factory), "_registry", new_callable=PropertyMock, return_value=registry
    ):
        result = factory.create("pm")

    assert result is mock_pm_instance
    mock_pm_cls.assert_called_once()


# ---------------------------------------------------------------------------
# 2. unknown role raises KeyError
# ---------------------------------------------------------------------------
def test_unknown_role_raises_key_error():
    factory = ConcreteAgentFactory()

    with patch.object(
        type(factory), "_registry", new_callable=PropertyMock, return_value=_mock_registry()
    ):
        try:
            factory.create("unknown_xyz")
            assert False, "Expected KeyError was not raised"
        except KeyError:
            pass


# ---------------------------------------------------------------------------
# 3. registry contains all expected roles
# ---------------------------------------------------------------------------
def test_registry_contains_expected_roles():
    expected_roles = set(_ALL_ROLES)
    factory = ConcreteAgentFactory()

    with patch.object(
        type(factory), "_registry", new_callable=PropertyMock, return_value=_mock_registry()
    ):
        assert expected_roles == set(factory._registry.keys())


# ---------------------------------------------------------------------------
# 4. kwargs are forwarded to the constructor
# ---------------------------------------------------------------------------
def test_kwargs_passed_to_constructor():
    mock_qa_cls = MagicMock()
    registry = _mock_registry(qa=mock_qa_cls)
    factory = ConcreteAgentFactory()

    with patch.object(
        type(factory), "_registry", new_callable=PropertyMock, return_value=registry
    ):
        factory.create("qa", agent_config="my-config", temperature=0.5)

    mock_qa_cls.assert_called_once_with(agent_config="my-config", temperature=0.5)


# ---------------------------------------------------------------------------
# 5. ConcreteAgentFactory is an instance of the port
# ---------------------------------------------------------------------------
def test_implements_port():
    assert isinstance(ConcreteAgentFactory(), AgentFactoryPort)
