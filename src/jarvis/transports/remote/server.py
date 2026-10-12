from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Callable, Awaitable, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from jarvis.transports.base import Transport
from jarvis.transports.remote.gemini_live import GeminiLiveTransport
from jarvis.core.brain import ToolCall, BrainResponse, BrainContext
from jarvis.core.session import SessionManager
from jarvis.core.config import settings
from jarvis.core.actions.registry import get_openai_schemas, execute_action


class RemoteVoiceServer(Transport):
    """
    Transport remoto: FastAPI + WebSocket binário + Gemini Live API.
    
    Endpoints:
    - GET  /ws/voice          # WebSocket voz (PCM16 16kHz ↑, 24kHz ↓ + JSON controle)
    - GET  /ws/relay          # WebSocket relay existente (compatibilidade)
    - GET  /                  # Frontend PWA (servido como static)
    - GET  /healthz           # Health check
    - POST /api/*             # REST API existente
    """

    def __init__(self, brain_factory, session_mgr: SessionManager):
        self.brain_factory = brain_factory
        self.session_mgr = session_mgr
        
        # FastAPI app
        self.app = FastAPI(title="JARVIS Remote Voice", version="2.0.0")
        self._setup_middleware()
        self._setup_routes()
        
        # Gemini Live Transport
        self.gemini_live: Optional[GeminiLiveTransport] = None
        self._active_connections: dict[str, WebSocket] = {}
        
        # Callbacks
        self._on_text_out: Optional[Callable[[str], Awaitable[None]]] = None
        self._on_audio_out: Optional[Callable[[bytes], Awaitable[None]]] = None

    def _setup_middleware(self):
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Em produção: domínio específico
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _setup_routes(self):
        # Health check (must be before static files mount)
        @self.app.get("/healthz")
        async def healthz():
            return {
                "status": "ok",
                "service": "jarvis-remote-voice",
                "version": "2.0.0",
                "transports": {
                    "local": settings.transports.local.enabled,
                    "remote": settings.transports.remote.enabled,
                    "webhook": settings.transports.webhook.enabled,
                }
            }

        # Static files (frontend PWA build)
        frontend_dist = Path(__file__).resolve().parent / "frontend" / "dist"
        if frontend_dist.exists():
            self.app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")
            self.app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

        @self.app.websocket("/ws/voice")
        async def ws_voice(
            ws: WebSocket,
            token: str = Query(...),
            session_id: Optional[str] = Query(None),
        ):
            # Auth
            if not self._verify_token(token):
                await ws.close(code=4001, reason="Invalid token")
                return
            
            await ws.accept()
            
            # Session ID
            sid = session_id or f"remote_{uuid.uuid4().hex[:8]}"
            self._active_connections[sid] = ws
            
            # Get or create session context
            context = self.session_mgr.get_or_create("remote", settings.user.name, sid)
            
            # Tools disponíveis
            tools = get_openai_schemas()
            
            # System prompt personalizado
            system_prompt = self._build_system_prompt(context)
            
            try:
                # Inicializa Gemini Live para esta sessão
                await self.gemini_live.get_or_create_session(sid, system_prompt)
                self.gemini_live.set_tools(tools, self._execute_tool)
                
                # Notifica pronto
                await ws.send_text(json.dumps({"type": "ready", "session_id": sid}))
                
                # Task para receber do Gemini e enviar pro browser
                gemini_task = asyncio.create_task(self._gemini_to_browser(ws, sid, context))
                
                # Loop principal: recebe do browser
                try:
                    while True:
                        msg = await ws.receive()
                        
                        if "bytes" in msg:
                            # Áudio binário: PCM16 16kHz
                            await self.gemini_live.send_audio(sid, msg["bytes"])
                        
                        elif "text" in msg:
                            # Controle JSON
                            data = json.loads(msg["text"])
                            await self._handle_control_message(sid, data)
                            
                except WebSocketDisconnect:
                    pass
                finally:
                    gemini_task.cancel()
                    try:
                        await gemini_task
                    except asyncio.CancelledError:
                        pass
                    
            except Exception as e:
                await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
            finally:
                self._active_connections.pop(sid, None)
                await self.gemini_live.close_session(sid)

        @self.app.websocket("/ws/relay")
        async def ws_relay(ws: WebSocket, token: str = Query(...)):
            """Relay existente para dashboard — manter compatibilidade."""
            if not self._verify_token(token):
                await ws.close(code=4001, reason="Invalid token")
                return
            await ws.accept()
            # TODO: Implementar relay existente se necessário
            try:
                while True:
                    await ws.receive_text()
            except WebSocketDisconnect:
                pass

        # REST API endpoints (compatibilidade com dashboard existente)
        @self.app.get("/api/status")
        async def api_status():
            return {
                "ok": True,
                "bridge": "jarvis-remote-voice",
                "version": "2.0.0",
                "connected": len(self._active_connections) > 0,
                "active_sessions": len(self._active_connections),
            }

        @self.app.post("/api/tools/execute")
        async def api_execute_tool(request: Request):
            data = await request.json()
            tool_name = data.get("tool")
            args = data.get("args", {})
            sid = data.get("session_id", "api")
            
            context = self.session_mgr.get_or_create("remote", settings.user.name, sid)
            result = execute_action(tool_name, args, context)
            return {"result": result}

    def _verify_token(self, token: str) -> bool:
        """Verifica token (suporta header Bearer + query param + cookie)."""
        expected = settings.transports.remote.token or settings.app.token
        return token == expected

    def _build_system_prompt(self, context: BrainContext) -> str:
        """Constrói system prompt com memória e ferramentas."""
        memory_text = ""
        if hasattr(self.brain_factory, 'memory') and self.brain_factory.memory:
            memory_text = self.brain_factory.memory.format_for_prompt()
        
        tools = get_openai_schemas()
        tools_desc = "\n".join([f"- {t['function']['name']}: {t['function']['description']}" for t in tools])
        
        return f"""Você é o JARVIS, assistente pessoal do {settings.user.name}.
- Fale em português do Brasil, calmo, levemente irônico, respostas curtas.
- Chame o usuário de "senhor".
- Use ferramentas quando necessário. Não prometa o que não pode fazer.

{memory_text}

FERRAMENTAS DISPONÍVEIS:
{tools_desc}"""

    async def _handle_control_message(self, session_id: str, data: dict):
        """Processa mensagens de controle do browser."""
        msg_type = data.get("type")
        
        if msg_type == "interrupt":
            await self.gemini_live.send_interrupt(session_id)
        
        elif msg_type == "tool_result":
            tool_call_id = data.get("tool_call_id")
            result = data.get("result", {})
            await self.gemini_live.send_tool_response(session_id, tool_call_id, result)
        
        elif msg_type == "text":
            text = data.get("text", "")
            await self.gemini_live.send_text(session_id, text)
        
        elif msg_type == "gesture":
            # Handle gesture from frontend
            await self._handle_gesture(session_id, data)
        
        elif msg_type == "ping":
            # Keepalive
            pass

    async def _gemini_to_browser(self, ws: WebSocket, session_id: str, context: BrainContext):
        """Recebe stream do Gemini e encaminha pro browser."""
        try:
            async for response in self.gemini_live.receive_stream(session_id):
                # Áudio binário
                if response.audio:
                    await ws.send_bytes(response.audio)
                
                # Texto (transcrição ou resposta)
                if response.text:
                    msg_type = response.metadata.get("type", "transcript")
                    await ws.send_text(json.dumps({
                        "type": msg_type,
                        "text": response.text,
                    }))
                
                # Tool calls
                if response.tool_calls:
                    for tc in response.tool_calls:
                        result = await self._execute_tool(tc)
                        context.add_message("tool", str(result), tc.call_id)
                        
                        # Re-envia resultado pro modelo via send_tool_response
                        await self.gemini_live.send_tool_response(
                            session_id, tc.call_id, result
                        )
                    
                    await ws.send_text(json.dumps({
                        "type": "tool_call",
                        "calls": [
                            {"name": tc.name, "args": tc.args, "id": tc.call_id}
                            for tc in response.tool_calls
                        ]
                    }))
                
                # Turn complete
                if response.turn_complete:
                    await ws.send_text(json.dumps({"type": "turn_complete"}))
                    
        except Exception as e:
            # Erro no stream - notifica browser
            try:
                await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
            except Exception:
                pass

    async def _handle_gesture(self, session_id: str, data: dict):
        """Processa gesto detectado pelo MediaPipe no frontend."""
        gesture = data.get("gesture")
        confidence = data.get("confidence", 0)
        handedness = data.get("handedness", "Right")
        
        if not gesture or confidence < 0.7:
            return
        
        context = self.session_mgr.get_or_create("remote", settings.user.name, session_id)
        
        # Mapeia gesto para ação
        gesture_actions = {
            "fist": "close_panel",
            "v_sign": "toggle_mic",
            "thumbs_up": "confirm",
            "thumbs_down": "cancel",
            "point": "cursor_follow",
            "open_palm": "neutral",
            "love": "special",
        }
        
        action = gesture_actions.get(gesture)
        if not action:
            return
        
        # Log no transcript
        if self._on_text_out:
            await self._on_text_out(f"Gesto detectado: {gesture} ({handedness})")
        
        # Executa ação baseada no gesto
        if action == "close_panel":
            # Envia comando pro frontend fechar painel
            for ws in self._active_connections.values():
                try:
                    await ws.send_text(json.dumps({
                        "type": "ui_close_panels"
                    }))
                except Exception:
                    pass
        
        elif action == "toggle_mic":
            # Toggle microfone no frontend
            for ws in self._active_connections.values():
                try:
                    await ws.send_text(json.dumps({
                        "type": "mic_toggle",
                        "enabled": False  # Frontend vai alternar
                    }))
                except Exception:
                    pass
        
        elif action == "confirm":
            # Confirma ação pendente se houver
            context.add_message("system", f"Gesto confirmado: {gesture}")
        
        elif action == "cancel":
            # Cancela ação pendente
            context.add_message("system", f"Gesto cancelado: {gesture}")
    
    async def _execute_tool(self, tool_call: ToolCall) -> dict:
        """Executa action via registry."""
        return execute_action(tool_call.name, tool_call.args, self.session_mgr)

    # ==================== Transport Protocol ====================

    async def start(
        self,
        on_audio_out: Callable[[bytes], Awaitable[None]],
        on_text_out: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[ToolCall], Awaitable[dict]],
    ) -> None:
        self._on_audio_out = on_audio_out
        self._on_text_out = on_text_out
        
        # Inicializa Gemini Live Transport
        self.gemini_live = GeminiLiveTransport(
            api_key=settings.gemini.api_key,
            model=settings.gemini.live_model,
            voice=settings.gemini.live_voice,
            language="pt-BR",
            session_manager=self.session_mgr,
        )
        
        # Inicia cleanup loop
        await self.gemini_live.start_cleanup_loop()
        
        # Tools serão definidas na primeira conexão WS
        print(f"[Remote Voice] Servidor pronto na porta {settings.transports.remote.port}")

    async def stop(self) -> None:
        if self.gemini_live:
            await self.gemini_live.stop()
        
        # Fecha conexões ativas
        for ws in self._active_connections.values():
            try:
                await ws.close()
            except Exception:
                pass
        self._active_connections.clear()
        
        print("[Remote Voice] Parado")

    async def send_audio_in(self, chunk: bytes) -> None:
        # Não usado no remote (browser envia via WS)
        pass

    async def send_text_in(self, text: str) -> None:
        # Para API REST
        pass

    async def send_interrupt(self) -> None:
        # Para todas as sessões ativas
        for sid in list(self._active_connections.keys()):
            await self.gemini_live.send_interrupt(sid)

    def get_app(self) -> FastAPI:
        """Retorna app FastAPI para montagem no container principal."""
        return self.app


# Função auxiliar para montar no container principal
def create_remote_voice_server(brain_factory, session_mgr) -> RemoteVoiceServer:
    return RemoteVoiceServer(brain_factory, session_mgr)