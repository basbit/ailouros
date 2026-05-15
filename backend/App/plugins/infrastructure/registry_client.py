from __future__ import annotations

import hashlib
import urllib.request
import urllib.error
from pathlib import Path

from backend.App.plugins.domain.registry_listing import RegistryListing, RegistryListingError, parse_registry_listing


class RegistryFetchError(OSError):
    pass


class BlobIntegrityError(ValueError):
    pass


def fetch_registry(url: str) -> RegistryListing:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            status = resp.getcode()
            if status < 200 or status >= 300:
                raise RegistryFetchError(
                    f"Registry fetch failed: HTTP {status} from {url}"
                )
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise RegistryFetchError(
            f"Registry fetch failed: HTTP {exc.code} {exc.reason} from {url}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RegistryFetchError(
            f"Registry fetch failed: cannot reach {url} — {exc.reason}"
        ) from exc

    try:
        return parse_registry_listing(raw)
    except RegistryListingError as exc:
        raise RegistryFetchError(
            f"Registry at {url} returned invalid JSON structure: {exc}"
        ) from exc


def download_blob(url: str, expected_sha256: str, dest: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            status = resp.getcode()
            if status < 200 or status >= 300:
                raise RegistryFetchError(
                    f"Blob download failed: HTTP {status} from {url}"
                )
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise RegistryFetchError(
            f"Blob download failed: HTTP {exc.code} {exc.reason} from {url}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RegistryFetchError(
            f"Blob download failed: cannot reach {url} — {exc.reason}"
        ) from exc

    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256.lower():
        raise BlobIntegrityError(
            f"SHA-256 mismatch for blob from {url}: "
            f"expected {expected_sha256}, got {actual}. "
            "The file may be corrupt or tampered with."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
