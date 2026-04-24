from __future__ import annotations

import logging
from typing import Any

from backend.App.shared.application.settings_resolver import get_setting

try:
    import redis as _redis_module  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    _redis_module = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

__all__ = [
    "make_redis_client",
    "redis_available",
    "redis_timeout_params",
]

_UNLIMITED_TIMEOUT_VALUES = {"none", "off", "unlimited"}


def redis_timeout_params(
    *,
    allow_unlimited: bool = True,
    connect_default: float = 5.0,
    socket_default: float = 30.0,
) -> dict[str, Any]:
    connect_timeout = _resolve_timeout_setting(
        settings_key="redis.socket_connect_timeout",
        env_key="REDIS_SOCKET_CONNECT_TIMEOUT",
        default=connect_default,
        allow_unlimited=allow_unlimited,
    )
    socket_timeout = _resolve_timeout_setting(
        settings_key="redis.socket_timeout",
        env_key="REDIS_SOCKET_TIMEOUT",
        default=socket_default,
        allow_unlimited=allow_unlimited,
    )
    params: dict[str, Any] = {}
    if connect_timeout is not None:
        params["socket_connect_timeout"] = connect_timeout
    if socket_timeout is not None:
        params["socket_timeout"] = socket_timeout
    return params


def make_redis_client(
    url: str,
    *,
    redis_module: Any = None,
    allow_unlimited_timeouts: bool = True,
    **overrides: Any,
) -> Any:
    module = redis_module if redis_module is not None else _redis_module
    if module is None:
        raise ImportError("redis package is not installed")
    params = redis_timeout_params(allow_unlimited=allow_unlimited_timeouts)
    params.update(overrides)
    return module.Redis.from_url(url, **params)


def redis_available(url: str, *, redis_module: Any = None, **overrides: Any) -> bool:
    client = None
    try:
        client = make_redis_client(url, redis_module=redis_module, **overrides)
        client.ping()
        return True
    except Exception as exc:
        logger.debug("Redis availability check failed for %s: %s", url, exc)
        return False
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as close_exc:
                logger.debug(
                    "Redis availability check close failed for %s: %s", url, close_exc
                )


def _resolve_timeout_setting(
    settings_key: str,
    env_key: str,
    default: float,
    *,
    allow_unlimited: bool,
) -> float | None:
    raw_value: str = get_setting(settings_key, env_key=env_key, default="")
    if not raw_value or not str(raw_value).strip():
        return default
    normalized = str(raw_value).strip().lower()
    if normalized in _UNLIMITED_TIMEOUT_VALUES:
        return None if allow_unlimited else default
    try:
        parsed_value = float(normalized)
    except ValueError:
        raise ValueError(
            f"settings resolution failed: operation=parse_float_setting "
            f"key={settings_key!r} env_key={env_key!r} "
            f"expected=positive float or unlimited sentinel actual={raw_value!r}"
        )
    if parsed_value > 0:
        return parsed_value
    return None if allow_unlimited else default
