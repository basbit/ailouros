from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

DEFAULT_URL_ENV_KEYS: tuple[str, ...] = (
    "OPENAI_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "LMSTUDIO_BASE_URL",
    "REDIS_URL",
)


def warn_malformed_url_env_vars(
    *,
    logger: logging.Logger,
    env_keys: tuple[str, ...] = DEFAULT_URL_ENV_KEYS,
) -> None:
    for env_key in env_keys:
        value = os.getenv(env_key, "").strip()
        if not value:
            continue
        try:
            parsed = urlparse(value)
            if not parsed.scheme or not parsed.netloc:
                logger.warning(
                    "Env var %s=%r looks malformed (missing scheme or host). "
                    "Expected format: http://host:port/path",
                    env_key,
                    value,
                )
        except Exception as exc:
            logger.warning(
                "Env var %s=%r could not be parsed as a URL: %s", env_key, value, exc
            )
