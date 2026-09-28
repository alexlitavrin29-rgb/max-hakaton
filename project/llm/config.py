"""Server-side LLM configuration; secrets never appear in settings repr."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


class LLMConfigurationError(ValueError):
    """Configuration is missing or invalid. Messages contain no values."""


@dataclass(frozen=True)
class LLMSettings:
    api_key: str = field(repr=False)
    base_url: str = "https://polza.ai/api/v1"
    model: str = "deepseek/deepseek-v4-flash"
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise LLMConfigurationError("LLM API key is required.")
        try:
            url = urlsplit(self.base_url)
        except ValueError:
            raise LLMConfigurationError("LLM_BASE_URL must be an HTTP(S) URL.") from None
        if url.scheme not in {"https", "http"} or not url.hostname:
            raise LLMConfigurationError("LLM_BASE_URL must be an HTTP(S) URL.")
        if url.username or url.password or url.query or url.fragment:
            raise LLMConfigurationError("LLM_BASE_URL must not contain credentials or query parameters.")
        if not self.model.strip():
            raise LLMConfigurationError("LLM_MODEL must not be empty.")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise LLMConfigurationError("LLM_TIMEOUT_SECONDS must be a positive finite number.")

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "LLMSettings":
        # Explicit environment wins over the local file; nothing is exported globally.
        path = env_file if env_file is not None else Path(__file__).resolve().parents[2] / ".env"
        values = {**dotenv_values(path, interpolate=False), **os.environ}
        try:
            timeout = float(values.get("LLM_TIMEOUT_SECONDS") or "30")
        except ValueError:
            raise LLMConfigurationError("LLM_TIMEOUT_SECONDS must be a number.") from None
        provider = (values.get("LLM_PROVIDER") or "polza").strip().lower()
        providers = {
            "polza": ("POLZA_API_KEY", cls.base_url, cls.model),
            "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
            "proxyapi": ("PROXYAPI_API_KEY", "https://api.proxyapi.ru/v1", cls.model),
        }
        if provider not in providers:
            raise LLMConfigurationError("LLM_PROVIDER must be polza, groq or proxyapi.")
        key_name, default_url, default_model = providers[provider]
        if not (values.get(key_name) or "").strip():
            raise LLMConfigurationError(f"{key_name} is required.")
        base_url = (values.get("LLM_BASE_URL") or default_url).strip()
        try:
            allowed_host = urlsplit(base_url).hostname == urlsplit(default_url).hostname
        except ValueError:
            allowed_host = False
        if not allowed_host:
            raise LLMConfigurationError("LLM_BASE_URL host is not allowed for the selected provider.")
        return cls(
            api_key=(values.get(key_name) or "").strip(),
            base_url=base_url,
            model=(values.get("LLM_MODEL") or default_model).strip(),
            timeout_seconds=timeout,
        )
