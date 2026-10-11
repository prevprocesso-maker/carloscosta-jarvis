from __future__ import annotations

import os
import yaml
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GeminiSettings(BaseSettings):
    api_key: str = ""
    fast_model: str = "gemini-3.5-flash-lite"
    fast_fallback_model: str = "gemini-3.1-flash-lite"
    reserve_model: str = "gemini-2.5-flash"
    live_model: str = "gemini-2.5-flash-native-audio-preview"
    live_voice: str = "Charon"
    live_fallback_voices: list[str] = ["Puck", "Zephyr", "Kore"]


class BrainSettings(BaseSettings):
    fast_timeout_ms: int = 9000
    reserve_timeout_ms: int = 30000
    cascade_on: list[str] = ["429", "503", "404", "timeout"]
    max_tool_rounds: int = 3
    max_output_tokens_decision: int = 400
    max_output_tokens_text: int = 8192


class TransportLocalSettings(BaseSettings):
    enabled: bool = True
    whisper_model: str = "large-v3"
    whisper_device: str = "auto"
    vad_threshold: float = 0.09
    sample_rate: int = 16000
    chunk_size: int = 640


class TransportRemoteSettings(BaseSettings):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    max_sessions: int = 10
    token: str = ""


class TransportWebhookSettings(BaseSettings):
    enabled: bool = True
    whatsapp_port: int = 8765


class UISettings(BaseSettings):
    desktop_enabled: bool = True
    web_enabled: bool = True
    theme: str = "stark"


class DeploySettings(BaseSettings):
    traefik_enabled: bool = False
    domain: str = ""
    letsencrypt_email: str = ""


class UserSettings(BaseSettings):
    name: str = "Raphael"
    language: str = "pt-BR"


class AppSettings(BaseSettings):
    name: str = "jarvis"
    token: str = ""
    user: UserSettings = Field(default_factory=UserSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    brain: BrainSettings = Field(default_factory=BrainSettings)
    transport_local: TransportLocalSettings = Field(default_factory=TransportLocalSettings)
    transport_remote: TransportRemoteSettings = Field(default_factory=TransportRemoteSettings)
    transport_webhook: TransportWebhookSettings = Field(default_factory=TransportWebhookSettings)
    ui: UISettings = Field(default_factory=UISettings)
    deploy: DeploySettings = Field(default_factory=DeploySettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )


def load_config(config_path: Optional[str] = None) -> AppSettings:
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}
        return AppSettings(**yaml_data)
    return AppSettings()


def get_base_dir() -> Path:
    if getattr(__import__("sys"), "frozen", False):
        return Path(__import__("sys").executable).parent
    return Path(__file__).resolve().parent.parent.parent


def generate_token() -> str:
    import secrets
    return secrets.token_urlsafe(32)


def ensure_config_files() -> None:
    base = get_base_dir()
    config_dir = base / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    settings = load_config()
    if not settings.app.token:
        settings.app.token = generate_token()
        save_config(settings)


def save_config(settings: AppSettings) -> None:
    base = get_base_dir()
    config_path = base / "config" / "settings.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = settings.model_dump(exclude_none=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


# Global settings instance (lazy loaded)
_settings: Optional[AppSettings] = None


def get_settings() -> AppSettings:
    global _settings
    if _settings is None:
        _settings = load_config()
    return _settings


settings = get_settings()

__all__ = [
    "AppSettings", "load_config", "get_settings", "get_base_dir",
    "generate_token", "ensure_config_files", "save_config", "settings",
    "GeminiSettings", "BrainSettings", "TransportLocalSettings",
    "TransportRemoteSettings", "TransportWebhookSettings",
    "UISettings", "DeploySettings", "UserSettings",
]