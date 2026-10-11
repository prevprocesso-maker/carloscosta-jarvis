from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class ToolCall(BaseModel):
    name: str
    args: dict
    call_id: str


class BrainResponse(BaseModel):
    text: Optional[str] = None
    audio: Optional[bytes] = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    turn_complete: bool = True
    interrupted: bool = False
    metadata: dict = Field(default_factory=dict)


class BrainContext(BaseModel):
    session_id: str
    transport: str
    user_id: str
    history: list[dict] = Field(default_factory=list)
    available_tools: list[dict] = Field(default_factory=list)
    system_prompt: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class BrainConfig(BaseModel):
    fast_model: str = "gemini-3.5-flash-lite"
    fast_fallback_model: str = "gemini-3.1-flash-lite"
    reserve_model: str = "gemini-2.5-flash"
    live_model: str = "gemini-2.5-flash-native-audio-preview"
    live_voice: str = "Charon"
    fast_timeout_ms: int = 9000
    reserve_timeout_ms: int = 30000
    cascade_on: list[str] = Field(default_factory=lambda: ["429", "503", "404", "timeout"])
    max_tool_rounds: int = 3
    max_output_tokens_decision: int = 400
    max_output_tokens_text: int = 8192


class Brain(ABC):
    @abstractmethod
    async def process(
        self,
        context: BrainContext,
        audio_chunk: Optional[bytes] = None,
        text_input: Optional[str] = None,
    ) -> AsyncIterator[BrainResponse]:
        """Processa áudio/texto e produz stream de respostas."""
        ...

    @abstractmethod
    async def interrupt(self, session_id: str) -> None:
        """Cancela processamento atual (barge-in)."""
        ...

    @abstractmethod
    def get_available_tools(self) -> list[dict]:
        """Retorna schemas OpenAI das tools disponíveis."""
        ...

    @abstractmethod
    async def tts_stream(self, text: str, voice: Optional[str] = None) -> AsyncIterator[bytes]:
        """Stream de áudio TTS para o texto dado."""
        ...

    @property
    @abstractmethod
    def config(self) -> BrainConfig:
        """Configuração do brain."""
        ...


class BrainFactory:
    def __init__(self, config: BrainConfig):
        self.config = config
        self._brains: dict[str, Brain] = {}

    def register(self, name: str, brain: Brain) -> None:
        self._brains[name] = brain

    def get(self, name: str) -> Optional[Brain]:
        return self._brains.get(name)

    def get_chain(self) -> list[Brain]:
        """Retorna cérebros em ordem de fallback."""
        chain = []
        for name in ["fast", "reserve", "openrouter"]:
            if name in self._brains:
                chain.append(self._brains[name])
        return chain