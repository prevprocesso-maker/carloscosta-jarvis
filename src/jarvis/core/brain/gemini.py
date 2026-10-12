from __future__ import annotations

import json
import time
from typing import AsyncIterator, Optional
from google import genai
from google.genai import types

from .base import Brain, BrainConfig, BrainContext, BrainResponse, ToolCall
from jarvis.core.actions.registry import get_openai_schemas, execute_action


class GeminiBrain(Brain):
    def __init__(self, settings):
        self.settings = settings
        self.client = genai.Client(api_key=settings.gemini.api_key)

    @property
    def config(self) -> BrainConfig:
        return BrainConfig(
            fast_model=self.settings.gemini.fast_model,
            fast_fallback_model=self.settings.gemini.fast_fallback_model,
            reserve_model=self.settings.gemini.reserve_model,
        )

    def get_available_tools(self) -> list[dict]:
        return get_openai_schemas()

    async def tts_stream(self, text: str, voice: Optional[str] = None) -> AsyncIterator[bytes]:
        yield b""

    def _desembrulhar(self, ferramentas_openai: list) -> list:
        def sobe(no):
            if isinstance(no, dict):
                return {k: (v.upper() if k == "type" and isinstance(v, str) else sobe(v)) for k, v in no.items()}
            if isinstance(no, list):
                return [sobe(x) for x in no]
            return no

        cruas = []
        for f in ferramentas_openai or []:
            corpo = f.get("function") if isinstance(f, dict) else None
            if not corpo or not corpo.get("name"):
                continue
            crua = {"name": corpo["name"], "description": corpo.get("description") or ""}
            params = corpo.get("parameters") or {}
            if params.get("properties"):
                crua["parameters"] = sobe(params)
            cruas.append(crua)
        return cruas

    def _para_conteudo(self, mensagens: list) -> tuple[str, list]:
        sistema = ""
        conteudos = []
        for m in mensagens or []:
            papel = m.get("role")
            texto = m.get("content")
            if papel == "system":
                sistema = str(texto or "")
                continue
            if papel == "tool":
                conteudos.append(types.Content(
                    role="user",
                    parts=[types.Part(text=f"[resultado] {str(texto or '')[:2000]}")]))
                continue
            if not texto:
                continue
            conteudos.append(types.Content(
                role=("model" if papel == "assistant" else "user"),
                parts=[types.Part(text=str(texto))]))
        return sistema, conteudos

    async def process(self, context: BrainContext, audio_chunk: Optional[bytes] = None,
                       text_input: Optional[str] = None) -> AsyncIterator[BrainResponse]:
        ferramentas = get_openai_schemas()
        cruas = self._desembrulhar(ferramentas)

        sistema, conteudos = self._para_conteudo(context.history)
        if text_input:
            conteudos.append(types.Content(role="user", parts=[types.Part(text=text_input)]))

        cfg = types.GenerateContentConfig(
            system_instruction=sistema or None,
            tools=([types.Tool(function_declarations=cruas)] if cruas else None),
            temperature=0.7,
            max_output_tokens=400,
        )

        resp = await self.client.aio.models.generate_content(
            model=self.settings.gemini.reserve_model,
            contents=conteudos,
            config=cfg,
        )

        texto = ""
        tool_calls = []
        for cand in (resp.candidates or []):
            for p in getattr(getattr(cand, "content", None), "parts", []) or []:
                if getattr(p, "text", None):
                    texto += str(p.text)
                if getattr(p, "function_call", None):
                    fc = p.function_call
                    tool_calls.append(ToolCall(name=fc.name, args=dict(fc.args or {}), call_id=fc.id or ""))

        if tool_calls:
            for tc in tool_calls:
                result = execute_action(tc.name, tc.args, context)
                yield BrainResponse(
                    text=texto or "Executando...",
                    tool_calls=[tc],
                    turn_complete=False,
                )
        else:
            yield BrainResponse(text=texto or "Ok.", turn_complete=True)

    async def interrupt(self, session_id: str) -> None:
        pass