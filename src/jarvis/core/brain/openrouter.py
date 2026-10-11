from __future__ import annotations

import os
import json
from typing import AsyncIterator, Optional
import httpx

from jarvis.core.brain.base import Brain, BrainConfig, BrainContext, BrainResponse, ToolCall
from jarvis.core.actions.registry import get_openai_schemas, execute_action


class OpenRouterBrain(Brain):
    def __init__(self, settings):
        self.settings = settings
        self.api_key = getattr(settings, 'openrouter_api_key', '') or os.getenv('OPENROUTER_API_KEY', '')
        self.base_url = "https://openrouter.ai/api/v1"
        self.model = "openrouter/cypher-alpha:free"
        self.client = httpx.AsyncClient(timeout=60.0)

    @property
    def config(self) -> BrainConfig:
        return BrainConfig()

    def get_available_tools(self) -> list[dict]:
        return get_openai_schemas()

    async def tts_stream(self, text: str, voice: Optional[str] = None) -> AsyncIterator[bytes]:
        yield b""

    async def process(self, context: BrainContext, audio_chunk: Optional[bytes] = None,
                       text_input: Optional[str] = None) -> AsyncIterator[BrainResponse]:
        if not self.api_key:
            yield BrainResponse(text="OpenRouter não configurado.", turn_complete=True)
            return

        ferramentas = get_openai_schemas()
        system_prompt = self._build_system_prompt(ferramentas, context.metadata)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(context.history[-8:])
        if text_input:
            messages.append({"role": "user", "content": text_input})

        payload = {
            "model": self.model,
            "messages": messages,
            "tools": [{"type": "function", "function": f["function"]} for f in ferramentas],
            "tool_choice": "auto",
            "max_tokens": 400,
            "temperature": 0.7,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = await self.client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]

            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    yield BrainResponse(
                        text=msg.get("content") or "Executando...",
                        tool_calls=[ToolCall(
                            name=tc["function"]["name"],
                            args=json.loads(tc["function"]["arguments"]),
                            call_id=tc["id"],
                        )],
                        turn_complete=False,
                    )
            else:
                yield BrainResponse(text=msg.get("content", "Ok."), turn_complete=True)
        except Exception as e:
            yield BrainResponse(text=f"Erro no OpenRouter: {e}", turn_complete=True)

    def _build_system_prompt(self, ferramentas: list, metadata: dict) -> str:
        linhas = [
            "Você é o CÉREBRO de reserva do JARVIS (via OpenRouter).",
            "Mesmas regras do cérebro principal: JSON, ferramentas só da lista, sem promessas vazias.",
            "",
            "RESPONDA SEMPRE JSON:",
            '  {"fala":"..."}',
            '  {"ferramenta":"...","args":{...},"fala":"..."}',
            '  {"ferramenta":"...","args":{...},"fala":"...","faltam":true}',
            "",
            "FERRAMENTAS:",
        ]
        for f in ferramentas:
            fn = f["function"]
            linhas.append(f'  - {fn["name"]}: {fn["description"]}')
        return "\n".join(linhas)

    async def interrupt(self, session_id: str) -> None:
        pass

    async def aclose(self):
        await self.client.aclose()