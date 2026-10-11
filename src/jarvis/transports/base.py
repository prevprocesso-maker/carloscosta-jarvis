from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Awaitable, Optional
from jarvis.core.brain import ToolCall


class Transport(ABC):
    @abstractmethod
    async def start(
        self,
        on_audio_out: Callable[[bytes], Awaitable[None]],
        on_text_out: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[ToolCall], Awaitable[dict]],
    ) -> None:
        """Inicia transport. Callbacks para enviar saída ao usuário."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Para transport limpo."""
        ...

    @abstractmethod
    async def send_audio_in(self, chunk: bytes) -> None:
        """Recebe áudio do usuário."""
        ...

    @abstractmethod
    async def send_text_in(self, text: str) -> None:
        """Recebe texto do usuário."""
        ...

    @abstractmethod
    async def send_interrupt(self) -> None:
        """Usuário interrompeu (barge-in)."""
        ...


class TransportFactory:
    _transports: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, transport_class: type):
        cls._transports[name] = transport_class

    @classmethod
    def get(cls, name: str):
        return cls._transports.get(name)

    @classmethod
    def create(cls, name: str, *args, **kwargs):
        transport_class = cls.get(name)
        if not transport_class:
            raise ValueError(f"Transport não registrado: {name}")
        return transport_class(*args, **kwargs)