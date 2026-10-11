from __future__ import annotations

import os
import yaml
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.core.brain.base import BrainConfig, BrainFactory
from jarvis.core.actions.registry import list_actions, get_openai_schemas
from jarvis.core.memory.manager import MemoryManager


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


class Container:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._memory: Optional[MemoryManager] = None
        self._brain_factory: Optional[BrainFactory] = None
        self._initialized = False

    @property
    def memory(self) -> MemoryManager:
        if self._memory is None:
            self._memory = MemoryManager(self.settings)
        return self._memory

    @property
    def brain_factory(self) -> BrainFactory:
        if self._brain_factory is None:
            brain_config = BrainConfig(
                fast_model=self.settings.gemini.fast_model,
                fast_fallback_model=self.settings.gemini.fast_fallback_model,
                reserve_model=self.settings.gemini.reserve_model,
                live_model=self.settings.gemini.live_model,
                live_voice=self.settings.gemini.live_voice,
                fast_timeout_ms=self.settings.brain.fast_timeout_ms,
                reserve_timeout_ms=self.settings.brain.reserve_timeout_ms,
                cascade_on=self.settings.brain.cascade_on,
                max_tool_rounds=self.settings.brain.max_tool_rounds,
                max_output_tokens_decision=self.settings.brain.max_output_tokens_decision,
                max_output_tokens_text=self.settings.brain.max_output_tokens_text,
            )
            self._brain_factory = BrainFactory(brain_config)
        return self._brain_factory

    def get_available_tools(self) -> list[dict]:
        return get_openai_schemas()

    def initialize_brains(self) -> None:
        if self._initialized:
            return

        from jarvis.core.brain.fast import FastBrain
        from jarvis.core.brain.gemini import GeminiBrain
        from jarvis.core.brain.openrouter import OpenRouterBrain

        self.brain_factory.register("fast", FastBrain(self.settings, self.memory))
        self.brain_factory.register("reserve", GeminiBrain(self.settings))
        self.brain_factory.register("openrouter", OpenRouterBrain(self.settings))

        self._initialized = True


def load_config(config_path: Optional[str] = None) -> AppSettings:
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}
        return AppSettings(**yaml_data)
    return AppSettings()


def create_application(config_path: Optional[str] = None) -> Container:
    settings = load_config(config_path)
    container = Container(settings)
    return container


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