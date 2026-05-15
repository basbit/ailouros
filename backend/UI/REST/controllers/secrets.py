from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from backend.App.shared.infrastructure.secrets_store import (
    delete_secret,
    list_secret_names,
    save_secret,
)

router = APIRouter()


class SecretBody(BaseModel):
    name: str
    value: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, raw: str) -> str:
        if not raw.strip():
            raise ValueError("name must not be blank")
        return raw.strip()

    @field_validator("value")
    @classmethod
    def validate_value(cls, raw: str) -> str:
        if not raw.strip():
            raise ValueError("value must not be blank")
        return raw


@router.get("/v1/secrets")
def list_secrets() -> dict:
    return {"names": list_secret_names()}


@router.put("/v1/secrets")
def upsert_secret(body: SecretBody) -> dict:
    save_secret(body.name, body.value)
    return {"name": body.name, "stored": True}


@router.delete("/v1/secrets/{name}")
def remove_secret(name: str) -> dict:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="name must not be blank")
    removed = delete_secret(cleaned)
    if not removed:
        raise HTTPException(status_code=404, detail=f"secret {cleaned!r} not found")
    return {"name": cleaned, "removed": True}


__all__ = ["router"]
