"""Lazy-imported Google Gemini client factory supporting Vertex Express and AI Studio keys."""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("finintel.rag.gemini")

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
FALLBACK_GEMINI_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)

_DEFAULT_ENV_CANDIDATES: tuple[Path, ...] = (
    Path(".env"),
    Path("d:/KiCuoiCung/DATN/financial-intelligence/.env"),
)


@dataclass(frozen=True, slots=True)
class GeminiClientBundle:
    """Resolved Gemini SDK client alongside authentication mode and active model."""

    client: Any
    mode: str
    model: str


def load_gemini_api_key(
    explicit: str | None = None,
    *,
    env_path: Path | None = None,
) -> str:
    """Load ``GEMINI_API_KEY`` from explicit argument, environment, or gitignored ``.env``."""
    if explicit is not None and explicit.strip():
        return explicit.strip()

    from_env = os.getenv("GEMINI_API_KEY", "").strip()
    if from_env:
        return from_env

    candidates: list[Path] = []
    if env_path is not None:
        candidates.append(env_path)
    candidates.extend(_DEFAULT_ENV_CANDIDATES)

    for path in candidates:
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key_name, raw_val = line.split("=", 1)
            if key_name.strip() == "GEMINI_API_KEY":
                cleaned = raw_val.strip().strip('"').strip("'").strip()
                if cleaned:
                    return cleaned

    raise RuntimeError(
        "GEMINI_API_KEY is not configured. Set GEMINI_API_KEY in the environment or .env file."
    )


def resolve_gemini_model(explicit: str | None = None) -> str:
    """Resolve Gemini model name from explicit override or environment."""
    if explicit is not None and explicit.strip():
        return explicit.strip()
    env_model = os.getenv("FININTEL_GEMINI_MODEL", "").strip()
    return env_model or DEFAULT_GEMINI_MODEL


def create_gemini_client(
    api_key: str,
    *,
    prefer_vertexai: bool | None = None,
) -> tuple[Any, str]:
    """Create a ``google.genai.Client`` with Vertex Express preference for ``AQ.`` keys."""
    genai: Any = importlib.import_module("google.genai")  # Lazy import (M-03)

    use_vertex = (
        prefer_vertexai if prefer_vertexai is not None else api_key.startswith("AQ.")
    )
    if use_vertex:
        try:
            return genai.Client(vertexai=True, api_key=api_key), "vertexai"
        except Exception as exc:
            logger.warning(
                "Vertex Express client init failed (%s); falling back to standard API key mode.",
                type(exc).__name__,
            )
    return genai.Client(api_key=api_key), "api_key"


def create_verified_gemini_bundle(
    *,
    api_key: str | None = None,
    env_path: Path | None = None,
    model: str | None = None,
) -> GeminiClientBundle:
    """Probe and return a working ``GeminiClientBundle`` with automatic Vertex/API-key fallback."""
    resolved_key = load_gemini_api_key(api_key, env_path=env_path)
    preferred_model = resolve_gemini_model(model)
    candidate_models = tuple(dict.fromkeys((preferred_model, *FALLBACK_GEMINI_MODELS)))
    modes: list[bool] = [True, False] if resolved_key.startswith("AQ.") else [False]

    last_error: Exception | None = None
    for use_vertex in modes:
        client, mode_label = create_gemini_client(
            resolved_key, prefer_vertexai=use_vertex
        )
        for candidate_model in candidate_models:
            try:
                client.models.generate_content(
                    model=candidate_model,
                    contents="OK",
                )
                return GeminiClientBundle(
                    client=client,
                    mode=mode_label,
                    model=candidate_model,
                )
            except Exception as exc:
                last_error = exc
                err_text = str(exc)
                # If auth/service is disabled on this mode, switch mode immediately.
                if "PERMISSION_DENIED" in err_text or "403" in err_text:
                    logger.info(
                        "Gemini mode=%s returned 403/PERMISSION_DENIED; trying fallback mode.",
                        mode_label,
                    )
                    break
                # If model is 404/NOT_FOUND or 429/RESOURCE_EXHAUSTED, try next candidate model.
                if (
                    "NOT_FOUND" in err_text
                    or "404" in err_text
                    or "RESOURCE_EXHAUSTED" in err_text
                    or "429" in err_text
                ):
                    logger.info(
                        "Gemini model=%s unavailable/exhausted on mode=%s; trying next model.",
                        candidate_model,
                        mode_label,
                    )
                    continue
                raise

    raise RuntimeError(
        f"Failed to initialize a working Gemini client: {type(last_error).__name__}: {last_error}"
    )


__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "FALLBACK_GEMINI_MODELS",
    "GeminiClientBundle",
    "create_gemini_client",
    "create_verified_gemini_bundle",
    "load_gemini_api_key",
    "resolve_gemini_model",
]
