from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import AsyncIterator, Callable, Awaitable, Optional, List, Dict, Any
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from jarvis.core.brain import ToolCall, BrainResponse
from jarvis.core.config import settings
from jarvis.core.session import SessionManager


@dataclass
class LiveSessionState:
    session_id: str
    gemini_session: Any = None
    session_cm: Any = None
    created_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    pending_tool_calls: Dict[str, ToolCall] = field(default_factory=dict)


class GeminiLiveTransport:
    """
    Wrapper para Gemini Live API (bidiGenerateContent).
    
    Gerencia sessões persistentes por session_id, com:
    - STT + VAD + TTS nativo no modelo
    - Function calling para actions
    - Interrupção (barge-in) via nova sessão
    - Pool de sessões com cleanup automático
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash-native-audio-preview",
        voice: str = "Charon",
        language: str = "pt-BR",
        session_manager: Optional[SessionManager] = None,
    ):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.voice = voice
        self.language = language
        self.session_manager = session_manager
        
        self._sessions: Dict[str, LiveSessionState] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

        # Tools disponíveis (encaminhadas do core)
        self._available_tools: List[Dict] = []
        self._tool_executor: Optional[Callable[[ToolCall], Awaitable[Dict]]] = None

    def set_tools(self, tools: List[Dict], executor: Callable[[ToolCall], Awaitable[Dict]]):
        """Define tools disponíveis e executor (chamado no start)."""
        self._available_tools = tools
        self._tool_executor = executor

    def _build_live_config(self, system_prompt: Optional[str] = None) -> types.LiveConnectConfig:
        """Constrói configuração da sessão Live."""
        tool_decls = []
        for tool in self._available_tools:
            fn = tool.get("function", {})
            if fn.get("name"):
                tool_decls.append(types.FunctionDeclaration(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    parameters=fn.get("parameters", {"type": "object", "properties": {}}),
                ))

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction=system_prompt or self._default_system_prompt(),
            tools=[types.Tool(function_declarations=tool_decls)] if tool_decls else None,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice)
                )
            ),
            # VAD automático do modelo
            # thinking_config=types.ThinkingConfig(thinking_budget=0),  # Para TTS puro
        )

    def _default_system_prompt(self) -> str:
        return f"""Você é o JARVIS, assistente pessoal do {settings.user.name}.
- Fale em português do Brasil, calmo, levemente irônico, respostas curtas.
- Chame o usuário de "senhor".
- Use ferramentas quando necessário. Não prometa o que não pode fazer.
- Ferramentas disponíveis: {', '.join(t['function']['name'] for t in self._available_tools if t.get('function'))}"""

    async def get_or_create_session(self, session_id: str, system_prompt: Optional[str] = None) -> LiveSessionState:
        """Obtém sessão existente ou cria nova."""
        async with self._lock:
            if session_id in self._sessions:
                state = self._sessions[session_id]
                state.last_activity = time.monotonic()
                return state

            # Cria nova sessão
            config = self._build_live_config(system_prompt)
            session_cm = self.client.aio.live.connect(model=self.model, config=config)
            gemini_session = await session_cm.__aenter__()

            state = LiveSessionState(
                session_id=session_id,
                gemini_session=gemini_session,
                session_cm=session_cm,
            )
            self._sessions[session_id] = state
            return state

    async def close_session(self, session_id: str):
        """Fecha sessão específica."""
        async with self._lock:
            state = self._sessions.pop(session_id, None)
            if state and state.session_cm:
                try:
                    await state.session_cm.__aexit__(None, None, None)
                except Exception:
                    pass

    async def send_audio(self, session_id: str, audio_bytes: bytes, mime_type: str = "audio/pcm;rate=16000"):
        """Envia chunk de áudio para o modelo."""
        state = await self.get_or_create_session(session_id)
        state.last_activity = time.monotonic()
        
        await state.gemini_session.send_realtime_input(
            audio=types.Blob(data=audio_bytes, mime_type=mime_type)
        )

    async def send_text(self, session_id: str, text: str):
        """Envia texto (digitado) para o modelo."""
        state = await self.get_or_create_session(session_id)
        state.last_activity = time.monotonic()
        
        await state.gemini_session.send_client_content(
            turns={"role": "user", "parts": [{"text": text}]},
            turn_complete=True,
        )

    async def send_tool_response(self, session_id: str, tool_call_id: str, result: Dict):
        """Envia resultado de tool call de volta para o modelo."""
        state = await self.get_or_create_session(session_id)
        state.last_activity = time.monotonic()
        
        await state.gemini_session.send_tool_response(
            function_responses=[types.FunctionResponse(
                name=state.pending_tool_calls.get(tool_call_id, ToolCall(name="", args={}, call_id=tool_call_id)).name,
                response=result,
                id=tool_call_id,
            )]
        )
        state.pending_tool_calls.pop(tool_call_id, None)

    async def send_interrupt(self, session_id: str):
        """Interrompe resposta atual (barge-in) — fecha sessão atual, próxima cria nova."""
        await self.close_session(session_id)
        # Próxima chamada criará nova sessão automaticamente

    async def receive_stream(self, session_id: str) -> AsyncIterator[BrainResponse]:
        """Gera stream de respostas do modelo."""
        state = await self.get_or_create_session(session_id)
        state.last_activity = time.monotonic()

        try:
            async for response in state.gemini_session.receive():
                state.last_activity = time.monotonic()
                
                # Áudio de resposta
                for part in response.data or []:
                    yield BrainResponse(audio=part)
                
                # Transcrição de entrada (STT)
                if response.server_content and response.server_content.input_transcription:
                    yield BrainResponse(
                        text=response.server_content.input_transcription.text,
                        metadata={"type": "input_transcript"}
                    )
                
                # Transcrição de saída (TTS)
                if response.server_content and response.server_content.output_transcription:
                    yield BrainResponse(
                        text=response.server_content.output_transcription.text,
                        metadata={"type": "output_transcript"}
                    )
                
                # Tool calls
                if response.tool_calls:
                    tool_calls = []
                    for tc in response.tool_calls:
                        tool_call = ToolCall(
                            name=tc.name,
                            args=dict(tc.args or {}),
                            call_id=tc.id or str(uuid.uuid4()),
                        )
                        tool_calls.append(tool_call)
                        state.pending_tool_calls[tc.id] = tool_call
                    
                    yield BrainResponse(
                        tool_calls=tool_calls,
                        turn_complete=False,
                    )
                
                # Turn complete
                if response.server_content and response.server_content.turn_complete:
                    yield BrainResponse(turn_complete=True)
                    
        except Exception as e:
            # Sessão pode ter expirado ou erro de rede
            await self.close_session(session_id)
            raise

    async def start_cleanup_loop(self, interval: int = 60, max_idle: int = 300):
        """Inicia loop de limpeza de sessões ociosas."""
        self._running = True
        
        async def cleanup():
            while self._running:
                await asyncio.sleep(interval)
                now = time.monotonic()
                to_remove = []
                
                async with self._lock:
                    for sid, state in self._sessions.items():
                        if now - state.last_activity > max_idle:
                            to_remove.append(sid)
                    
                    for sid in to_remove:
                        state = self._sessions.pop(sid)
                        if state.session_cm:
                            try:
                                await state.session_cm.__aexit__(None, None, None)
                            except Exception:
                                pass
        
        self._cleanup_task = asyncio.create_task(cleanup())

    async def stop(self):
        """Para transport e limpa todas as sessões."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        async with self._lock:
            for state in self._sessions.values():
                if state.session_cm:
                    try:
                        await state.session_cm.__aexit__(None, None, None)
                    except Exception:
                        pass
            self._sessions.clear()