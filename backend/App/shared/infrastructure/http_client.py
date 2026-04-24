from __future__ import annotations

from typing import Any

import httpx

from backend.App.shared.application.settings_resolver import get_setting_bool, get_setting_int

__all__ = ["make_sync_httpx_client"]


def make_sync_httpx_client(
    timeout: float | httpx.Timeout | None = None,
    *,
    follow_redirects: bool = True,
    limits: httpx.Limits | None = None,
    **kwargs: Any,
) -> httpx.Client:
    if limits is None:
        keepalive_disabled = get_setting_bool(
            "httpx.disable_keepalive",
            env_key="SWARM_HTTPX_DISABLE_KEEPALIVE",
            default=False,
        )
        if keepalive_disabled:
            limits = httpx.Limits(max_keepalive_connections=0)
        else:
            keepalive_max = get_setting_int(
                "httpx.keepalive_max",
                env_key="SWARM_HTTPX_KEEPALIVE_MAX",
                default=20,
            )
            keepalive_expiry = get_setting_int(
                "httpx.keepalive_expiry_seconds",
                env_key="SWARM_HTTPX_KEEPALIVE_EXPIRY",
                default=5,
            )
            limits = httpx.Limits(
                max_keepalive_connections=keepalive_max,
                keepalive_expiry=float(keepalive_expiry),
            )
    return httpx.Client(
        timeout=timeout,
        limits=limits,
        follow_redirects=follow_redirects,
        **kwargs,
    )
