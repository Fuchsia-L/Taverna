"""
Multi-provider config: JSON-based storage, client cache, model lookup.

Config file: backend/config/providers.json
"""

import json
import logging
import os
import tempfile
from pathlib import Path

from openai import AsyncOpenAI, OpenAI

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "providers.json"

_DEFAULT_CONFIG: dict = {
    "providers": [],
    "enabled_models": [],
    "default_model": None,
    "compressor_model": None,
}


# ── Config I/O ──────────────────────────────────────────────────────


def load_config() -> dict:
    if not _CONFIG_PATH.exists():
        save_config(_DEFAULT_CONFIG)
        return dict(_DEFAULT_CONFIG)
    try:
        return json.loads(_CONFIG_PATH.read_text("utf-8"))
    except Exception:
        logger.exception("Failed to read %s, returning defaults", _CONFIG_PATH)
        return dict(_DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(config, indent=2, ensure_ascii=False)
    # Atomic write: write to temp file then rename
    fd, tmp = tempfile.mkstemp(dir=str(_CONFIG_PATH.parent), suffix=".tmp")
    fd_closed = False
    try:
        os.write(fd, data.encode("utf-8"))
        os.close(fd)
        fd_closed = True
        # On Windows, target must not exist for rename
        if _CONFIG_PATH.exists():
            _CONFIG_PATH.unlink()
        os.rename(tmp, str(_CONFIG_PATH))
    except Exception:
        if not fd_closed:
            try:
                os.close(fd)
            except OSError:
                pass
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ── Config accessors ────────────────────────────────────────────────


def get_providers() -> list[dict]:
    return load_config().get("providers", [])


def get_enabled_models() -> list[dict]:
    return load_config().get("enabled_models", [])


def get_model_config(model_id: str) -> dict | None:
    for m in get_enabled_models():
        if m.get("id") == model_id:
            return m
    return None


def get_provider_thinking_mode(model_id: str) -> str:
    """Return the thinking_mode for the provider of a model: 'pair', 'params', or 'none'."""
    model = get_model_config(model_id)
    if not model:
        return "none"
    provider = get_provider_by_id(model["provider_id"])
    if not provider:
        return "none"
    return provider.get("thinking_mode", "none")


def get_thinking_extra_params(model_id: str) -> dict | None:
    """Return thinking_extra_params for a model (used by 'params' mode providers)."""
    model = get_model_config(model_id)
    if not model:
        return None
    params = model.get("thinking_extra_params")
    return params if isinstance(params, dict) and params else None


def get_thinking_pair(model_id: str) -> str | None:
    """For 'pair' mode: find the thinking counterpart among enabled models.

    Priority: 1) explicit thinking_pair field  2) -thinking suffix match.
    Also checks reverse: if another model's thinking_pair points to us.
    """
    model = get_model_config(model_id)
    if not model:
        return None
    if get_provider_thinking_mode(model_id) != "pair":
        return None
    provider_id = model["provider_id"]
    enabled = get_enabled_models()

    # 1) Explicit thinking_pair on this model
    explicit = model.get("thinking_pair")
    if explicit:
        for m in enabled:
            if m.get("id") == explicit and m.get("provider_id") == provider_id:
                return explicit

    # 2) Reverse: another model's thinking_pair points to us
    for m in enabled:
        if m.get("thinking_pair") == model_id and m.get("provider_id") == provider_id:
            return m["id"]

    # 3) Fallback: -thinking suffix auto-match
    if model_id.endswith("-thinking"):
        base_id = model_id.removesuffix("-thinking")
    else:
        base_id = model_id + "-thinking"
    for m in enabled:
        if m.get("id") == base_id and m.get("provider_id") == provider_id:
            return base_id

    return None


def get_provider_by_id(provider_id: str) -> dict | None:
    for p in get_providers():
        if p.get("id") == provider_id:
            return p
    return None


def resolve_model(req_model: str | None) -> str:
    config = load_config()
    models = config.get("enabled_models", [])
    default = config.get("default_model")

    if req_model:
        for m in models:
            if m.get("id") == req_model:
                return req_model
        logger.warning("Unknown model %s, falling back to %s", req_model, default)

    if default:
        for m in models:
            if m.get("id") == default:
                return default

    # Last resort: first enabled model
    if models:
        return models[0]["id"]

    raise ValueError("No models configured. Add a provider and enable models first.")


# ── Client cache ────────────────────────────────────────────────────

_sync_clients: dict[tuple[str, str], OpenAI] = {}
_async_clients: dict[tuple[str, str], AsyncOpenAI] = {}


def clear_client_cache() -> None:
    """Clear cached clients. Call after provider credentials change."""
    _sync_clients.clear()
    _async_clients.clear()


def _get_provider_for_model(model_id: str) -> dict:
    model = get_model_config(model_id)
    if not model:
        raise ValueError(f"Model not found in enabled_models: {model_id}")
    provider = get_provider_by_id(model["provider_id"])
    if not provider:
        raise ValueError(
            f"Provider '{model['provider_id']}' not found for model '{model_id}'"
        )
    return provider


def get_sync_client_for_model(model_id: str) -> OpenAI:
    provider = _get_provider_for_model(model_id)
    key = (provider["base_url"], provider["api_key"])
    if key not in _sync_clients:
        _sync_clients[key] = OpenAI(api_key=key[1], base_url=key[0])
    return _sync_clients[key]


def get_async_client_for_model(model_id: str) -> AsyncOpenAI:
    provider = _get_provider_for_model(model_id)
    key = (provider["base_url"], provider["api_key"])
    if key not in _async_clients:
        _async_clients[key] = AsyncOpenAI(api_key=key[1], base_url=key[0])
    return _async_clients[key]


# ── Remote model discovery ──────────────────────────────────────────


def fetch_remote_models(base_url: str, api_key: str) -> list[dict]:
    """Call a provider's /v1/models endpoint, return list of {id, owned_by}."""
    client = OpenAI(api_key=api_key, base_url=base_url)
    try:
        response = client.models.list()
        return [
            {"id": m.id, "owned_by": getattr(m, "owned_by", "")}
            for m in response.data
        ]
    except Exception as exc:
        logger.warning("fetch_remote_models failed for %s: %s", base_url, exc)
        raise
