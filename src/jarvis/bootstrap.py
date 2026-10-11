from __future__ import annotations

import os
import yaml
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.core.brain.base import BrainConfig, BrainFactory, ToolCall
from jarvis.core.actions.registry import list_actions, get_openai_schemas
from jarvis.core.memory.manager import MemoryManager
from jarvis.core.session import SessionManager
from jarvis.transports.local import LocalAudioTransport
from jarvis.transports.remote import RemoteVoiceServer
from jarvis.core.config import AppSettings, get_settings, get_base_dir as _get_base_dir


class Container:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._memory: Optional[MemoryManager] = None
        self._brain_factory: Optional[BrainFactory] = None
        self._session_mgr: Optional[SessionManager] = None
        self._transport_local: Optional[LocalAudioTransport] = None
        self._transport_remote: Optional[RemoteVoiceServer] = None
        self._initialized = False

    @property
    def memory(self) -> MemoryManager:
        if self._memory is None:
            self._memory = MemoryManager(self.settings)
        return self._memory

    @property
    def session_mgr(self) -> SessionManager:
        if self._session_mgr is None:
            self._session_mgr = SessionManager()
        return self._session_mgr

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

    @property
    def transport_local(self) -> LocalAudioTransport:
        if self._transport_local is None:
            brain = self.brain_factory.get("fast") or self.brain_factory.get_chain()[0]
            self._transport_local = LocalAudioTransport(brain, self.session_mgr)
        return self._transport_local

    @property
    def transport_remote(self) -> RemoteVoiceServer:
        if self._transport_remote is None:
            self._transport_remote = RemoteVoiceServer(self.brain_factory, self.session_mgr)
        return self._transport_remote

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

    async def start_transports(self) -> None:
        # Start local transport (desktop)
        if self.settings.transport_local.enabled:
            await self.transport_local.start(
                on_audio_out=self._audio_out_callback,
                on_text_out=self._text_out_callback,
                on_tool_call=self._tool_call_callback,
            )
        
        # Start remote transport (web voice)
        if self.settings.transport_remote.enabled:
            await self.transport_remote.start(
                on_audio_out=self._audio_out_callback,
                on_text_out=self._text_out_callback,
                on_tool_call=self._tool_call_callback,
            )

    async def _audio_out_callback(self, chunk: bytes) -> None:
        pass

    async def _text_out_callback(self, text: str) -> None:
        pass

    async def _tool_call_callback(self, tool_call: ToolCall) -> dict:
        from jarvis.core.actions.registry import execute_action
        return execute_action(tool_call.name, tool_call.args, self.session_mgr)

    def get_fastapi_app(self):
        """Retorna app FastAPI do transport remoto para montagem externa se necessário."""
        if self._transport_remote:
            return self._transport_remote.get_app()
        return None


def create_application(config_path: Optional[str] = None) -> Container:
    settings = get_settings()
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}
        # Merge YAML with existing settings
        for key, value in yaml_data.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
    container = Container(settings)
    return container


def generate_token() -> str:
    import secrets
    return secrets.token_urlsafe(32)


def ensure_config_files() -> None:
    base = _get_base_dir()
    config_dir = base / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    if not settings.app.token:
        settings.app.token = generate_token()
        save_config(settings)


def save_config(settings: AppSettings) -> None:
    base = _get_base_dir()
    config_path = base / "config" / "settings.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = settings.model_dump(exclude_none=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


# Re-export for backward compatibility
__all__ = [
    "Container",
    "create_application",
    "generate_token",
    "ensure_config_files",
    "save_config",
    "get_base_dir",
]