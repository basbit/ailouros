from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any


_ASSET_REQUEST_PATTERN = re.compile(
    r"<swarm_asset_request\b(?P<attributes>[^>]*)>(?P<body>.*?)</swarm_asset_request>"
    r"|<swarm_asset_request\b(?P<self_closing_attributes>[^>]*)/>",
    re.DOTALL | re.IGNORECASE,
)
_ATTRIBUTE_PATTERN = re.compile(r"([a-zA-Z_][\w.-]*)\s*=\s*(['\"])(.*?)\2", re.DOTALL)


@dataclass(frozen=True)
class AssetRequest:
    path: str
    source: str
    url: str = ""
    prompt: str = ""
    license: str = ""
    provenance: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source": self.source,
            "url": self.url,
            "prompt": self.prompt,
            "license": self.license,
            "provenance": self.provenance,
        }


def parse_asset_requests(text: str) -> list[AssetRequest]:
    requests: list[AssetRequest] = []
    for match in _ASSET_REQUEST_PATTERN.finditer(text):
        attributes_text = match.group("attributes") or match.group("self_closing_attributes") or ""
        body = str(match.group("body") or "").strip()
        attributes = {
            key.lower(): html.unescape(value.strip())
            for key, _, value in _ATTRIBUTE_PATTERN.findall(attributes_text)
        }
        path = str(attributes.get("path") or "").strip()
        if not path:
            continue
        source = str(attributes.get("source") or attributes.get("mode") or "upload").strip().lower()
        prompt = str(attributes.get("prompt") or body).strip()
        requests.append(
            AssetRequest(
                path=path,
                source=source,
                url=str(attributes.get("url") or "").strip(),
                prompt=prompt,
                license=str(attributes.get("license") or "").strip(),
                provenance=str(attributes.get("provenance") or "").strip(),
            )
        )
    return requests


def asset_requests_to_dicts(requests: list[AssetRequest]) -> list[dict[str, Any]]:
    return [request.to_dict() for request in requests]
