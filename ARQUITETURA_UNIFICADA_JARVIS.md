# JARVIS Unificado — Arquitetura Honestamente Integrada
## Um Core, Dois Transports, Duas UIs, Zero Frankenstein

---

## 1. O Princípio: **Core Isolado, Transports Plugáveis**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        JARVIS CORE (Processo Único)                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  BRAIN ENGINE                                                       │   │
│  │  • cerebro_rapido (Gemini free local, <1s decisão)                  │   │
│  │  • cerebro_gemini (fallback pago)                                   │   │
│  │  • cerebro_openrouter (Codex, fallback final)                       │   │
│  │  • Fallback automático em cascata                                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  MEMORY LAYER                                                       │   │
│  │  • memory_manager (JSON estruturado: identity, prefs, projects...)  │   │
│  │  • Opcional: Vector store (Chroma local) para RAG sobre histórico   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  ACTION REGISTRY (27 actions descobertas automaticamente)           │   │
│  │  • @action decorator → auto-registro, schema OpenAI, validação      │   │
│  │  • Namespaces: computer, files, web, comm, dev, media, system       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  SESSION MANAGER                                                    │   │
│  │  • Conversation history (curto prazo)                               │   │
│  │  • Tool call chain state (multi-step tasks)                         │   │
│  │  • Per-transport session isolation                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
         ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
         │ TRANSPORT LOCAL │ │ TRANSPORT REMOTO│ │   API/WEBHOOK   │
         │ (sounddevice +  │ │ (WebSocket bin  │ │ (REST para      │
         │  Whisper local) │ │  audio + Gemini │ │  integrações)   │
         │                 │ │  Live API)      │ │                 │
         │ • STT: Whisper  │ │ • STT+TTS+VAD:  │ │ • Healthcheck   │
         │   (GPU/CPU)     │ │   Gemini Live   │ │ • Metrics       │
         │ • TTS: Gemini   │ │   nativo        │ │ • Webhooks      │
         │   Live TTS      │ │ • Interrupção   │ │ • WhatsApp      │
         │ • VAD: local    │ │   nativa        │ │   Bridge        │
         │   threshold     │ │ • MediaPipe     │ │                 │
         └────────┬────────┘ └────────┬────────┘ └────────┬────────┘
                  │                   │                   │
                  ▼                   ▼                   ▼
         ┌─────────────────┐ ┌─────────────────┐
         │ UI DESKTOP      │ │ UI WEB (PWA)    │
         │ (PyQt6 nativo)  │ │ (Vite/TS,       │
         │ • NucleoVisual  │ │  AudioWorklet,  │
         │ • Computer Use  │ │  MediaPipe,     │
         │ • Settings      │ │  Service Worker)│
         │ • Metrics tempo │ │                 │
         │   real          │ │ • Instalável    │
         └─────────────────┘ └─────────────────┘
```

**Regra de Ouro:** **Core NÃO conhece transports nem UIs.** Transports chamam Core via interface tipada. UIs chamam Core via mesmo protocolo (WebSocket JSON + binary audio).

---

## 2. Estrutura De Pastas Real (O Que Você Vai Ter)

```
jarvis-unificado/
├── .github/
│   └── workflows/
│       ├── ci.yml              # lint, typecheck, test
│       ├── build.yml           # multi-arch docker + installer
│       └── release.yml         # tag → build → GitHub Release
├── docker/
│   ├── Dockerfile              # multi-stage: frontend build → python runtime
│   ├── docker-compose.yml      # local dev + prod (traefik opcional)
│   ├── deploy.sh               # build + tag + push/pull + restart
│   └── .dockerignore
├── src/
│   └── jarvis/
│       ├── __init__.py
│       ├── __main__.py         # entry point: python -m jarvis
│       ├── bootstrap.py        # config, logging, DI container
│       ├── core/
│       │   ├── __init__.py
│       │   ├── brain/
│       │   │   ├── __init__.py
│       │   │   ├── base.py          # AbstractBrain protocol
│       │   │   ├── fast.py          # cerebro_rapido (refatorado)
│       │   │   ├── gemini.py        # cerebro_gemini
│       │   │   ├── openrouter.py    # cerebro_openrouter
│       │   │   └── factory.py       # BrainFactory com fallback chain
│       │   ├── memory/
│       │   │   ├── __init__.py
│       │   │   ├── manager.py       # memory_manager refatorado
│       │   │   └── vector_store.py  # Chroma local (opcional)
│       │   ├── actions/
│       │   │   ├── __init__.py
│       │   │   ├── registry.py      # @action decorator, discovery
│       │   │   ├── schemas.py       # Pydantic models para function calling
│       │   │   ├── computer/
│       │   │   ├── files/
│       │   │   ├── web/
│       │   │   ├── comm/
│       │   │   ├── dev/
│       │   │   ├── media/
│       │   │   └── system/
│       │   ├── session/
│       │   │   ├── __init__.py
│       │   │   ├── manager.py       # histórico, tool chains, estado
│       │   │   └── context.py       # contexto por transport/sessão
│       │   └── config.py            # Pydantic Settings (yaml + env)
│       ├── transports/
│       │   ├── __init__.py
│       │   ├── base.py              # Transport protocol (abstract)
│       │   ├── local/
│       │   │   ├── __init__.py
│       │   │   ├── audio.py         # sounddevice + Whisper + VAD local
│       │   │   └── tts.py           # Gemini Live TTS (streaming)
│       │   ├── remote/
│       │   │   ├── __init__.py
│       │   │   ├── server.py        # FastAPI + WebSocket /ws/voice
│       │   │   ├── gemini_live.py   # cliente Gemini Live API (bidi)
│       │   │   ├── audio_worklet.js # compilado para static/
│       │   │   └── frontend/        # Vite/TS source (build → static/)
│       │   └── webhook/
│       │       ├── __init__.py
│       │       ├── server.py        # FastAPI REST + health/metrics
│       │       └── whatsapp.py      # Baileys bridge (subprocess Node)
│       ├── ui/
│       │   ├── __init__.py
│       │   ├── desktop/
│       │   │   ├── __init__.py
│       │   │   ├── main_window.py
│       │   │   ├── widgets/
│       │   │   └── theme/
│       │   └── web/
│       │       ├── static/          # frontend build output
│       │       └── templates/
│       └── integrations/
│           ├── __init__.py
│           ├── whatsapp_bridge/     # Node.js Baileys (como subprocess gerenciado)
│           └── dashboard/           # REST + WebSocket para monitoramento
├── config/
│   ├── default.yaml                # configuração padrão (versionada)
│   ├── schema.json                 # JSON Schema para validação
│   └── api_keys.example.json
├── tests/
│   ├── unit/
│   │   ├── test_brain_fast.py
│   │   ├── test_memory.py
│   │   ├── test_action_registry.py
│   │   └── test_session.py
│   ├── integration/
│   │   ├── test_voice_loop_local.py
│   │   └── test_voice_loop_remote.py
│   └── fixtures/
├── scripts/
│   ├── dev.py                      # hot reload dev environment
│   ├── install.py                  # instalador cross-platform
│   └── benchmark.py                # latência, cota, memoria
├── pyproject.toml                  # deps, tool config (ruff, mypy, pytest)
├── uv.lock                         # lockfile reprodutível
├── README.md
├── ARCHITECTURE.md                 # este documento
├── CHANGELOG.md
└── LICENSE
```

---

## 3. Contratos (Interfaces) — O Que Garante Zero Colcha De Retalho

### 3.1 Brain Protocol (src/jarvis/core/brain/base.py)

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator
from pydantic import BaseModel

class ToolCall(BaseModel):
    name: str
    args: dict
    call_id: str

class BrainResponse(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = []
    turn_complete: bool = True
    interrupted: bool = False

class BrainContext(BaseModel):
    session_id: str
    transport: str  # "local" | "remote"
    user_id: str
    history: list[dict]  # formato OpenAI messages
    available_tools: list[dict]  # OpenAI function schemas
    metadata: dict = {}

class Brain(ABC):
    @abstractmethod
    async def process(self, context: BrainContext, audio_chunk: bytes | None = None) -> AsyncIterator[BrainResponse]:
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
```

### 3.2 Transport Protocol (src/jarvis/transports/base.py)

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Awaitable
from jarvis.core.brain import BrainContext, BrainResponse

class Transport(ABC):
    @abstractmethod
    async def start(self, on_audio_out: Callable[[bytes], Awaitable[None]],
                    on_text_out: Callable[[str], Awaitable[None]],
                    on_tool_call: Callable[[ToolCall], Awaitable[dict]]) -> None:
        """Inicia transport. Callbacks para enviar saída ao usuário."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Para transport limpo."""
        ...

    @abstractmethod
    async def send_audio_in(self, chunk: bytes) -> None:
        """Recebe áudio do usuário (chamado pelo frontend)."""
        ...

    @abstractmethod
    async def send_text_in(self, text: str) -> None:
        """Recebe texto do usuário (chat digita)."""
        ...

    @abstractmethod
    async def send_interrupt(self) -> None:
        """Usuário interrompeu (barge-in)."""
        ...
```

### 3.3 Action Registry (src/jarvis/core/actions/registry.py)

```python
from functools import wraps
from typing import get_type_hints
from pydantic import BaseModel, create_model
import inspect

_action_registry: dict[str, dict] = {}

def action(name: str | None = None, namespace: str = "general", description: str = ""):
    """Decorator para registrar actions automaticamente."""
    def decorator(func):
        nonlocal name
        name = name or func.__name__
        full_name = f"{namespace}.{name}"
        
        # Extrai schema dos parâmetros via type hints + docstring
        sig = inspect.signature(func)
        hints = get_type_hints(func)
        fields = {}
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls", "session"): continue
            param_type = hints.get(param_name, str)
            fields[param_name] = (param_type, ... if param.default is param.empty else param.default)
        
        ArgsModel = create_model(f"{full_name}_Args", **fields)
        
        _action_registry[full_name] = {
            "func": func,
            "name": full_name,
            "description": description or func.__doc__ or "",
            "args_schema": ArgsModel.model_json_schema(),
            "namespace": namespace,
        }
        return func
    return decorator

def get_action(name: str):
    return _action_registry.get(name)

def list_actions(namespace: str | None = None) -> list[dict]:
    if namespace:
        return [a for a in _action_registry.values() if a["namespace"] == namespace]
    return list(_action_registry.values())

def get_openai_schemas(namespace: str | None = None) -> list[dict]:
    """Retorna schemas no formato OpenAI function calling."""
    schemas = []
    for a in list_actions(namespace):
        schemas.append({
            "type": "function",
            "function": {
                "name": a["name"],
                "description": a["description"],
                "parameters": a["args_schema"],
            }
        })
    return schemas
```

**Uso nas actions (exemplo real):**

```python
# src/jarvis/core/actions/computer/control.py
from jarvis.core.actions.registry import action

@action(namespace="computer", description="Abre um aplicativo pelo nome")
def open_app(name: str, session: "SessionContext") -> dict:
    # implementação real usa pywin32/pyautogui
    return {"success": True, "message": f"{name} aberto"}

@action(namespace="computer", description="Digita texto no foco atual")
def type_text(text: str, interval: float = 0.01, session: "SessionContext") -> dict:
    return {"success": True, "chars": len(text)}
```

**Descoberta automática no bootstrap:**

```python
# src/jarvis/bootstrap.py
def discover_actions():
    import pkgutil, importlib
    from jarvis.core.actions import registry
    
    # Importa todos módulos em actions/*/ para disparar @action
    for pkg_name in ["computer", "files", "web", "comm", "dev", "media", "system"]:
        importlib.import_module(f"jarvis.core.actions.{pkg_name}")
    
    return registry.list_actions()
```

---

## 4. Loop Principal Unificado (src/jarvis/__main__.py)

```python
import asyncio
import signal
from contextlib import asynccontextmanager
from jarvis.bootstrap import create_application
from jarvis.core.config import settings
from jarvis.transports.local.audio import LocalAudioTransport
from jarvis.transports.remote.server import RemoteVoiceServer
from jarvis.transports.webhook.server import WebhookServer
from jarvis.ui.desktop.main_window import run_desktop_ui

@asynccontextmanager
async def lifespan():
    app = create_application()  # Inicializa core, brain, memory, actions
    
    # Transports
    local_transport = LocalAudioTransport(app.brain, app.session_mgr)
    remote_server = RemoteVoiceServer(app.brain, app.session_mgr)
    webhook_server = WebhookServer(app.brain, app.session_mgr)
    
    # Inicia transports
    await local_transport.start()
    await remote_server.start()
    await webhook_server.start()
    
    # UI Desktop (roda em thread separada se necessário)
    ui_task = None
    if settings.ui.desktop_enabled:
        ui_task = asyncio.create_task(run_desktop_ui(app))
    
    try:
        yield app
    finally:
        await local_transport.stop()
        await remote_server.stop()
        await webhook_server.stop()
        if ui_task:
            ui_task.cancel()

async def main():
    async with lifespan() as app:
        # Signal handling
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))
        
        # Mantém vivo
        await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 5. Transport Local — Áudio No Próprio Processo

```python
# src/jarvis/transports/local/audio.py
import sounddevice as sd
import numpy as np
from faster_whisper import WhisperModel
from jarvis.transports.base import Transport
from jarvis.core.brain import BrainContext, BrainResponse

class LocalAudioTransport(Transport):
    def __init__(self, brain, session_mgr):
        self.brain = brain
        self.session_mgr = session_mgr
        self.whisper = WhisperModel("large-v3", device="cuda", compute_type="float16")
        self.vad_threshold = 0.09  # calibrável
        self.sample_rate = 16000
        self.chunk_size = 640
        self._stream = None
        self._session_id = "local_main"
        self._running = False
    
    async def start(self, on_audio_out, on_text_out, on_tool_call):
        self._on_audio_out = on_audio_out
        self._on_text_out = on_text_out
        self._on_tool_call = on_tool_call
        self._running = True
        
        # Inicia stream de captura
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.chunk_size,
            callback=self._audio_callback
        )
        self._stream.start()
        
        # Loop de processamento VAD + STT + Brain
        asyncio.create_task(self._process_loop())
    
    def _audio_callback(self, indata, frames, time, status):
        # VAD simples: RMS > threshold
        rms = np.sqrt(np.mean(indata**2))
        if rms > self.vad_threshold:
            self._audio_buffer.append(indata.copy())
        elif self._audio_buffer:
            self._vad_triggered.set()
    
    async def _process_loop(self):
        while self._running:
            await self._vad_triggered.wait()
            audio = np.concatenate(self._audio_buffer)
            self._audio_buffer.clear()
            self._vad_triggered.clear()
            
            # STT local
            segments, _ = self.whisper.transcribe(audio.flatten(), language="pt")
            text = " ".join(s.text for s in segments).strip()
            if not text: continue
            
            # Brain
            context = BrainContext(
                session_id=self._session_id,
                transport="local",
                user_id=settings.user.name,
                history=self.session_mgr.get_history(self._session_id),
                available_tools=self.brain.get_available_tools(),
            )
            
            async for response in self.brain.process(context):
                if response.text:
                    await self._on_text_out(response.text)
                    # TTS via Gemini Live (streaming)
                    async for chunk in self._tts_stream(response.text):
                        await self._on_audio_out(chunk)
                
                for tc in response.tool_calls:
                    result = await self._on_tool_call(tc)
                    # Re-injeta resultado no brain (multi-turn)
                    context.history.append({"role": "tool", "tool_call_id": tc.call_id, "content": str(result)})
    
    async def _tts_stream(self, text: str) -> AsyncIterator[bytes]:
        # Usa Gemini Live TTS (já implementado em gemini_voice.py)
        async for chunk in self.brain.tts_stream(text):
            yield chunk
    
    async def stop(self):
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
```

---

## 6. Transport Remoto — Gemini Live API Nativo (WebSocket Binário)

```python
# src/jarvis/transports/remote/gemini_live.py
from google import genai
from google.genai import types
from jarvis.core.brain import BrainContext, BrainResponse

class GeminiLiveTransport:
    """Wrapper sobre Gemini Live API para transport remoto."""
    
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-native-audio-preview", voice: str = "Charon"):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.voice = voice
        self._sessions: dict[str, types.AsyncSession] = {}
    
    async def get_session(self, session_id: str, tools: list[dict], system_prompt: str):
        if session_id in self._sessions:
            return self._sessions[session_id]
        
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=tools)],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice)
                )
            ),
        )
        
        session = await self.client.aio.live.connect(model=self.model, config=config)
        self._sessions[session_id] = session
        return session
    
    async def process_audio(self, session_id: str, audio_chunk: bytes) -> AsyncIterator[BrainResponse]:
        session = await self.get_session(session_id, ...)  # tools + system prompt
        
        # Envia áudio
        await session.send_realtime_input(audio=types.Blob(data=audio_chunk, mime_type="audio/pcm;rate=16000"))
        
        # Recebe stream
        async for response in session.receive():
            # Áudio de resposta
            for part in response.data or []:
                yield BrainResponse(audio=part)
            
            # Transcrição
            if response.server_content and response.server_content.output_transcription:
                yield BrainResponse(text=response.server_content.output_transcription.text)
            
            # Tool calls
            if response.tool_calls:
                for tc in response.tool_calls:
                    yield BrainResponse(tool_calls=[ToolCall(
                        name=tc.name, args=tc.args, call_id=tc.id
                    )])
            
            # Turn complete
            if response.server_content and response.server_content.turn_complete:
                yield BrainResponse(turn_complete=True)
```

```python
# src/jarvis/transports/remote/server.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from jarvis.transports.base import Transport
from jarvis.transports.remote.gemini_live import GeminiLiveTransport
from jarvis.core.brain import BrainContext

class RemoteVoiceServer(Transport):
    def __init__(self, brain, session_mgr):
        self.brain = brain
        self.session_mgr = session_mgr
        self.gemini_live = GeminiLiveTransport(settings.gemini.api_key)
        self.app = FastAPI()
        self._setup_routes()
    
    def _setup_routes(self):
        self.app.mount("/static", StaticFiles(directory="src/jarvis/ui/web/static"), name="static")
        
        @self.app.get("/healthz")
        async def healthz():
            return {"status": "ok"}
        
        @self.app.websocket("/ws/voice")
        async def ws_voice(ws: WebSocket, token: str = Query(...), session_id: str = Query(...)):
            if token != settings.app.token:
                await ws.close(code=4001)
                return
            await ws.accept()
            
            context = BrainContext(
                session_id=session_id,
                transport="remote",
                user_id=settings.user.name,
                history=self.session_mgr.get_history(session_id),
                available_tools=self.brain.get_available_tools(),
            )
            
            gemini_session = await self.gemini_live.get_session(session_id, ...)
            
            async def receive_from_browser():
                try:
                    while True:
                        msg = await ws.receive()
                        if "bytes" in msg:
                            await gemini_session.send_realtime_input(
                                audio=types.Blob(data=msg["bytes"], mime_type="audio/pcm;rate=16000")
                            )
                        elif "text" in msg:
                            data = json.loads(msg["text"])
                            if data["type"] == "tool_result":
                                await gemini_session.send_tool_response(...)
                            elif data["type"] == "interrupt":
                                # Gemini Live não tem interrupt explícito - nova sessão
                                pass
                except WebSocketDisconnect:
                    pass
            
            async def send_to_browser():
                async for response in gemini_session.receive():
                    for part in response.data or []:
                        await ws.send_bytes(part)
                    if response.server_content and response.server_content.output_transcription:
                        await ws.send_text(json.dumps({"type": "transcript", "text": ...}))
                    if response.tool_calls:
                        await ws.send_text(json.dumps({"type": "tool_call", ...}))
                    if response.server_content and response.server_content.turn_complete:
                        await ws.send_text(json.dumps({"type": "turn_complete"}))
            
            await asyncio.gather(receive_from_browser(), send_to_browser())
```

---

## 7. Frontend Web (Vite + TypeScript + AudioWorklet)

```
src/jarvis/transports/remote/frontend/
├── package.json
├── tsconfig.json
├── vite.config.ts
├── index.html
├── public/
│   ├── manifest.json          # PWA
│   └── sw.js                  # Service Worker
├── src/
│   ├── main.ts
│   ├── style.css
│   ├── audio/
│   │   ├── worklet.ts         # AudioWorkletProcessor (PCM16 16kHz ↔ 24kHz)
│   │   ├── capture.ts         # getUserMedia + WorkletNode
│   │   └── playback.ts        # AudioContext + queue + flush on interrupt
│   ├── gestures/
│   │   ├── mediapipe.ts       # @mediapipe/tasks-vision GestureRecognizer
│   │   └── cursor.ts          # Virtual cursor + smoothing
│   ├── ui/
│   │   ├── hud.ts             # Orbe, log, painéis arrastáveis
│   │   └── panels.ts          # Panel manager (abrir/fechar/arrastar)
│   └── websocket.ts           # WebSocket manager + reconnect + binary
```

**AudioWorklet (chave para latência baixa):**

```javascript
// src/jarvis/transports/remote/frontend/src/audio/worklet.ts
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.inputBuffer = [];
    this.outputBuffer = [];
    this.port.onmessage = (e) => {
      if (e.data.type === "play") this.outputBuffer.push(new Float32Array(e.data.buffer));
      if (e.data.type === "flush") this.outputBuffer = [];
    };
  }
  
  process(inputs, outputs) {
    const input = inputs[0][0]; // Float32 48kHz
    const output = outputs[0][0];
    
    // Capture: resample 48k→16k + Float32→PCM16
    if (input) {
      const resampled = this.resample(input, 48000, 16000);
      const pcm16 = this.floatTo16BitPCM(resampled);
      this.port.postMessage({ type: "capture", buffer: pcm16.buffer }, [pcm16.buffer]);
    }
    
    // Playback: 24kHz PCM16 → Float32 48kHz
    if (this.outputBuffer.length && output.length) {
      const chunk = this.outputBuffer[0];
      const resampled = this.resample(chunk, 24000, 48000);
      output.set(resampled.subarray(0, output.length));
      if (resampled.length <= output.length) this.outputBuffer.shift();
      else this.outputBuffer[0] = resampled.subarray(output.length);
    }
    return true;
  }
  
  floatTo16BitPCM(float32) { ... }
  resample(buffer, fromRate, toRate) { ... }
}

registerProcessor("pcm-processor", PCMProcessor);
```

---

## 8. Deploy Automatizado (Uma Coisa Só)

```bash
# docker/deploy.sh
#!/usr/bin/env bash
set -euo pipefail

IMAGE="jarvis-unificado"
TAG="${1:-latest}"
REGISTRY="${DOCKER_REGISTRY:-}"

# Build multi-arch
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ${REGISTRY}${IMAGE}:${TAG} \
  -t ${REGISTRY}${IMAGE}:$(date +%Y%m%d-%H%M%S) \
  --push .

# Deploy no servidor (via SSH)
ssh ${DEPLOY_HOST} << EOF
  cd /opt/jarvis
  docker compose pull
  docker compose up -d --remove-orphans
  docker image prune -f
EOF
```

```yaml
# docker/docker-compose.yml
services:
  jarvis:
    image: ${IMAGE}:${TAG:-latest}
    environment:
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - APP_TOKEN=${APP_TOKEN}
      - USER_NAME=${USER_NAME}
      - CONFIG_FILE=/config/production.yaml
    volumes:
      - jarvis_data:/data
      - jarvis_config:/config
      - /tmp/.X11-unix:/tmp/.X11-unix  # para UI desktop se necessário
    ports:
      - "8000:8000"   # WebSocket voice + REST + dashboard
      - "8765:8765"   # WhatsApp bridge
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3

  traefik:
    image: traefik:v2.11
    command:
      - --api.insecure=true
      - --providers.docker=true
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --certificatesresolvers.letsencrypt.acme.httpchallenge=true
      - --certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web
      - --certificatesresolvers.letsencrypt.acme.email=${LETSENCRYPT_EMAIL}
      - --certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json
    ports: ["80:80", "443:443", "8080:8080"]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - letsencrypt:/letsencrypt
    labels:
      - traefik.enable=true

volumes:
  jarvis_data:
  jarvis_config:
  letsencrypt:

networks:
  default:
    name: jarvis_net
```

---

## 9. O Que Muda No Código Existente (Migração Real)

| Arquivo Atual (Repo 1) | Ação | Novo Local |
|------------------------|------|------------|
| `main.py` (12k linhas) | **Extrair** → bootstrap, brain factory, session mgr, transport local | `src/jarvis/bootstrap.py`, `core/brain/factory.py`, `core/session/`, `transports/local/` |
| `core/cerebro_rapido.py` | **Refatorar** → implementa `Brain` protocol, remover globals | `core/brain/fast.py` |
| `core/gemini_voice.py` | **Dividir**: TTS streaming → `transports/local/tts.py`, Gemini Live client → `transports/remote/gemini_live.py` | |
| `core/cerebro_gemini.py` | **Refatorar** → `Brain` protocol | `core/brain/gemini.py` |
| `core/cerebro_openrouter.py` | **Refatorar** → `Brain` protocol | `core/brain/openrouter.py` |
| `actions/*.py` (27 arquivos) | **Adicionar** `@action` decorator, mover para namespaces | `core/actions/{computer,files,web,comm,dev,media,system}/` |
| `memory/memory_manager.py` | **Refatorar** → `MemoryManager` class + vector store opcional | `core/memory/manager.py` |
| `ui.py` (5.9k linhas) | **Modularizar** → widgets separados, theme engine | `ui/desktop/` |
| `dashboard/server.py` | **Dividir**: REST → `transports/webhook/server.py`, WebSocket voice → `transports/remote/server.py`, static frontend → `ui/web/static/` | |
| `whatsapp_bridge/server.js` | **Manter** como subprocess gerenciado pelo Core | `integrations/whatsapp_bridge/` |
| `config/api_keys.json` | **Migrar** → Pydantic Settings (YAML + env) | `config/default.yaml` + `config/schema.json` |

---

## 10. Checklist De Execução (Sem Frankstein)

### Fase 1: Core Isolado (Semana 1-2)
- [ ] Criar `src/jarvis/` structure
- [ ] Definir protocols: `Brain`, `Transport`, `Action`
- [ ] Extrair `cerebro_rapido` → `FastBrain` implementando `Brain`
- [ ] Extrair `memory_manager` → `MemoryManager` class
- [ ] Criar `@action` registry + migrar 3 actions piloto
- [ ] Testes unitários: brain, memory, registry

### Fase 2: Transport Local (Semana 2-3)
- [ ] `LocalAudioTransport` implementando `Transport`
- [ ] Whisper STT + VAD local + Gemini Live TTS streaming
- [ ] Integração com `FastBrain` + `SessionManager`
- [ ] Testes: loop voz local funcional

### Fase 3: Transport Remoto + Web UI (Semana 3-4)
- [ ] `RemoteVoiceServer` (FastAPI + WebSocket)
- [ ] `GeminiLiveTransport` wrapper
- [ ] Frontend Vite/TS: AudioWorklet + MediaPipe + HUD
- [ ] PWA (manifest + service worker)
- [ ] Testes: voz no browser → brain → resposta áudio

### Fase 4: UI Desktop + Integrações (Semana 4-5)
- [ ] Modularizar `ui.py` → `ui/desktop/`
- [ ] Conectar UI Desktop ao mesmo `Transport` local
- [ ] WhatsApp Bridge como subprocess gerenciado
- [ ] Dashboard REST + WebSocket (monitoramento)

### Fase 5: Deploy + Observabilidade (Semana 5-6)
- [ ] Docker multi-stage + Compose + `deploy.sh`
- [ ] Traefik + Let's Encrypt (opcional, para acesso remoto HTTPS)
- [ ] Logs estruturados (structlog + JSONL)
- [ ] Métricas (Prometheus + Grafana dashboards)
- [ ] Healthchecks + alertas

### Fase 6: Polish + Docs (Semana 6)
- [ ] `README.md` completo
- [ ] `ARCHITECTURE.md` (este doc)
- [ ] `DEVELOPMENT.md` (hot reload, debug, test)
- [ ] Benchmarks de latência, cota, memória
- [ ] Release v2.0.0

---

## 11. Por Que Isso NÃO É Frankstein

| Característica Frankstein | Esta Arquitetura |
|---------------------------|------------------|
| Código duplicado | **Core único** — brain, memory, actions compartilhados |
| Acoplamento forte | **Protocols/Interfaces** — transports não conhecem core internals |
| Config espalhada | **Pydantic Settings** — single source of truth |
| Deploy manual | **Docker Compose + deploy.sh** — um comando |
| Testes inexistentes | **Pytest** desde Fase 1 (unit + integration) |
| "Gambiarra" pra funcionar | **Type hints + validação** — mypy + pydantic em tudo |
| Duas codebases | **Um repo, um pyproject.toml, um lockfile** |
| Decisões implícitas | **ADR (Architecture Decision Records)** em `docs/adr/` |

---

## 12. Decisões Que Você Precisa Tomar Agora

```yaml
# config/default.yaml — exemplos de decisões
app:
  name: "jarvis"
  token: ""  # gerado no primeiro run
  user:
    name: "Raphael"
    language: "pt-BR"

gemini:
  api_key: ""  # obrigatório
  fast_model: "gemini-3.5-flash-lite"      # cerebro_rapido
  fast_fallback: "gemini-3.1-flash-lite"
  reserve_model: "gemini-2.5-flash"        # cerebro_gemini
  live_model: "gemini-2.5-flash-native-audio-preview"  # TTS local + remote
  live_voice: "Charon"
  live_fallback_voices: ["Puck", "Zephyr", "Kore"]

brain:
  fast_timeout_ms: 9000
  reserve_timeout_ms: 30000
  cascade_on: ["429", "503", "404", "timeout"]

transports:
  local:
    enabled: true
    whisper_model: "large-v3"
    whisper_device: "cuda"  # auto-detect
    vad_threshold: 0.09
  remote:
    enabled: true
    port: 8000
    host: "0.0.0.0"
    max_sessions: 10

ui:
  desktop_enabled: true
  web_enabled: true
  theme: "stark"  # ou "custom"

whatsapp:
  enabled: true
  bridge_port: 8765

deploy:
  traefik_enabled: false  # true se quiser HTTPS automático
  domain: "jarvis.seudominio.com"
  letsencrypt_email: "voce@email.com"
```

---

## 13. Conclusão: É Viável, É Limpo, É Um Só

**Sim, dá para fazer UM JARVIS unificado.** Não é frankstein se:
1. **Core é isolado** (protocols, sem imports de transports/UIs)
2. **Transports são plugins** (mesma interface, implementações diferentes)
3. **Actions são descobertas** (decorator, zero boilerplate)
4. **Config é centralizada** (YAML + env, validada no startup)
5. **Deploy é único** (uma imagem Docker, um comando)
6. **Testes existem** (desde o primeiro commit)

**O segredo:** Não misture responsabilidades. `main.py` vira `bootstrap.py` (100 linhas). `cerebro_rapido.py` vira `FastBrain` (300 linhas, protocol). `gemini_voice.py` vira **dois** transports (local TTS + remote Live API). `ui.py` vira **duas** UIs (desktop + web) falando com mesmo core.

**Você tem todo o código necessário no Repo 1.** O Repo 2 contribui com: **padrão WebSocket audio binário + AudioWorklet + MediaPipe no browser + Docker/Traefik deploy**. Nada mais.

**Próximo passo:** Crie a pasta `src/jarvis/` e comece pelo `bootstrap.py` + `core/brain/base.py` + `core/actions/registry.py`. O resto encaixa.

---

*Arquitetura desenhada para ser implementada incrementalmente, sem quebrar o que funciona, sem duplicar lógica, sem "colcha de retalho". 11/10/2026*