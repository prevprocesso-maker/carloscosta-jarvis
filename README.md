# JARVIS CyberBot v2 — Arquitetura Unificada

Assistente pessoal estilo J.A.R.V.I.S. com **core único** e **transports plugáveis**:
- **Local**: Whisper STT + Gemini Live TTS + PyQt6 (controle total do PC)
- **Remoto**: Gemini Live API nativo (bidiGenerateContent) + Web PWA (acesso de qualquer browser)
- **Webhook**: WhatsApp Bridge (Baileys) + REST API

## Estrutura

```
src/jarvis/
├── core/                    # Cérebro, memória, actions, sessões (compartilhado)
│   ├── brain/               # FastBrain (<1s), GeminiBrain, OpenRouterBrain
│   ├── memory/              # MemoryManager (JSON + vector store opcional)
│   ├── actions/             # @action decorator, 27 actions em namespaces
│   └── session/             # SessionManager (histórico, tool chains)
├── transports/
│   ├── local/               # sounddevice + Whisper + VAD + TTS streaming
│   ├── remote/              # FastAPI + WebSocket binário + Gemini Live API
│   └── webhook/             # REST + WhatsApp Bridge (Node.js subprocess)
├── ui/
│   ├── desktop/             # PyQt6 nativo (NucleoVisual, Computer Use)
│   └── web/                 # Vite/TS + AudioWorklet + MediaPipe (PWA)
└── integrations/            # WhatsApp Bridge, etc.
```

## Instalação Rápida

```bash
# 1. Clone e entre
git clone <repo>
cd JARVIS-CyberBot

# 2. Crie venv e instale deps
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev]"

# 3. Configure
cp config/api_keys.example.json config/api_keys.json
# Edite config/api_keys.json com sua GEMINI_API_KEY (aistudio.google.com/apikey)

# 4. Rode
python -m jarvis
```

## Desenvolvimento

```bash
# Testes
pytest tests/unit -v

# Lint
ruff check src/
ruff format src/

# Type check
mypy src/

# Hot reload dev
python scripts/dev.py
```

## Deploy (Docker)

```bash
# Build multi-arch
docker buildx build --platform linux/amd64,linux/arm64 -t jarvis:latest .

# Deploy
./docker/deploy.sh
```

## Configuração

- `config/settings.yaml` — configuração principal (YAML)
- `config/api_keys.json` — chaves de API (não versionar)
- `.env` — override de variáveis sensíveis

## Actions (27, auto-descobertas)

| Namespace | Actions |
|-----------|---------|
| `computer` | open_app, type_text, click, scroll, screenshot, window_control |
| `files` | read, write, list, search, zip, organize |
| `web` | search, browse, youtube, weather, flights |
| `comm` | whatsapp_send, whatsapp_read, send_message |
| `dev` | code_helper, dev_agent, codex_remote |
| `media` | video_edit, play_audio, record_screen |
| `system` | volume, brightness, monitor, reminder, proactive |

## Arquitetura

Ver [`ARCHITECTURE.md`](ARCHITECTURE.md) para detalhes técnicos:
- Protocols (`Brain`, `Transport`, `Action`)
- DI Container (`bootstrap.py`)
- Fallback chain (Fast → Reserve → OpenRouter)
- Session isolation por transport

## Roadmap

- [x] Phase 1: Core Foundation (Brain, Memory, Actions, Session)
- [ ] Phase 2: Transport Local (Whisper + TTS)
- [ ] Phase 3: Transport Remote (WebSocket + Gemini Live + Web UI)
- [ ] Phase 4: Desktop UI + WhatsApp + Dashboard
- [ ] Phase 5: Docker + Deploy + Observabilidade
- [ ] Phase 6: Polish + Docs + Release v2.0

## Licença

MIT