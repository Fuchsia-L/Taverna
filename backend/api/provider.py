"""Provider management API — CRUD for providers, model discovery, model selection."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.providers import (
    clear_client_cache,
    fetch_remote_models,
    get_provider_by_id,
    load_config,
    save_config,
)

router = APIRouter()


# ── Request / Response models ───────────────────────────────────────


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    api_key: str
    thinking_mode: str = "none"  # "pair" | "params" | "none"


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # empty or "...xxxx" means no change
    thinking_mode: str | None = None


class EnabledModel(BaseModel):
    id: str
    display_name: str
    provider_id: str
    is_thinking: bool = False
    thinking_pair: str | None = None
    thinking_extra_params: dict | None = None


class ModelsUpdate(BaseModel):
    enabled_models: list[EnabledModel]


class PreferencesUpdate(BaseModel):
    default_model: str | None = None
    compressor_model: str | None = None


# ── Helpers ─────────────────────────────────────────────────────────


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return f"...{key[-4:]}"


def _sanitize_provider(p: dict) -> dict:
    return {**p, "api_key": _mask_key(p.get("api_key", ""))}


# ── Provider CRUD ───────────────────────────────────────────────────


@router.get("/providers")
async def list_providers() -> dict:
    config = load_config()
    return {"providers": [_sanitize_provider(p) for p in config.get("providers", [])]}


@router.post("/providers")
async def create_provider(req: ProviderCreate) -> dict:
    config = load_config()
    provider = {
        "id": str(uuid.uuid4()),
        "name": req.name,
        "base_url": req.base_url.rstrip("/"),
        "api_key": req.api_key,
        "thinking_mode": req.thinking_mode,
    }
    config.setdefault("providers", []).append(provider)
    save_config(config)
    return {"provider": _sanitize_provider(provider)}


@router.put("/providers/{provider_id}")
async def update_provider(provider_id: str, req: ProviderUpdate) -> dict:
    config = load_config()
    for p in config.get("providers", []):
        if p["id"] == provider_id:
            if req.name is not None:
                p["name"] = req.name
            if req.base_url is not None:
                p["base_url"] = req.base_url.rstrip("/")
            if req.api_key is not None and not req.api_key.startswith("..."):
                p["api_key"] = req.api_key
            if req.thinking_mode is not None:
                p["thinking_mode"] = req.thinking_mode
            save_config(config)
            clear_client_cache()
            return {"provider": _sanitize_provider(p)}
    raise HTTPException(status_code=404, detail="Provider not found")


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str) -> dict:
    config = load_config()
    providers = config.get("providers", [])
    original_len = len(providers)
    config["providers"] = [p for p in providers if p["id"] != provider_id]
    if len(config["providers"]) == original_len:
        raise HTTPException(status_code=404, detail="Provider not found")
    # Remove models belonging to deleted provider
    config["enabled_models"] = [
        m for m in config.get("enabled_models", []) if m.get("provider_id") != provider_id
    ]
    save_config(config)
    clear_client_cache()
    return {"status": "ok"}


# ── Model discovery ─────────────────────────────────────────────────


@router.post("/providers/{provider_id}/fetch-models")
async def fetch_models(provider_id: str) -> dict:
    import asyncio

    provider = get_provider_by_id(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    try:
        # fetch_remote_models uses sync OpenAI client — run in thread to avoid blocking event loop
        models = await asyncio.to_thread(
            fetch_remote_models, provider["base_url"], provider["api_key"]
        )
        return {"models": models}
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch models from provider: {exc}",
        )


# ── Enabled models ──────────────────────────────────────────────────


@router.get("/models")
async def list_models() -> dict:
    config = load_config()
    provider_map = {p["id"]: p for p in config.get("providers", [])}
    models = config.get("enabled_models", [])
    enriched = []
    for m in models:
        p = provider_map.get(m.get("provider_id"), {})
        enriched.append({
            **m,
            "provider_name": p.get("name", "Unknown"),
            "thinking_mode": p.get("thinking_mode", "none"),
        })
    return {
        "models": enriched,
        "default_model": config.get("default_model"),
    }


@router.put("/models")
async def update_models(req: ModelsUpdate) -> dict:
    config = load_config()
    config["enabled_models"] = [m.model_dump() for m in req.enabled_models]
    save_config(config)
    return {"status": "ok"}


# ── Preferences ─────────────────────────────────────────────────────


@router.get("/preferences")
async def get_preferences() -> dict:
    config = load_config()
    return {
        "default_model": config.get("default_model"),
        "compressor_model": config.get("compressor_model"),
    }


@router.put("/preferences")
async def update_preferences(req: PreferencesUpdate) -> dict:
    config = load_config()
    if req.default_model is not None:
        config["default_model"] = req.default_model
    if req.compressor_model is not None:
        config["compressor_model"] = req.compressor_model
    save_config(config)
    return {"status": "ok"}
