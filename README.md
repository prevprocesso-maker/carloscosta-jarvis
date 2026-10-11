# JARVIS CyberBot v2 — Assistente Pessoal Unificado (Local + Remoto)

[![CI](https://github.com/prevprocesso-maker/carloscosta-jarvis/actions/workflows/ci.yml/badge.svg)](https://github.com/prevprocesso-maker/carloscosta-jarvis/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Architecture](https://img.shields.io/badge/architecture-core%2Btransports-orange)](ARCHITECTURE.md)

> **Um core, dois transports, duas UIs.** Controle total do seu PC por voz localmente + acesso por voz nativa no browser de qualquer dispositivo.

---

## 🎯 O Que É

**JARVIS CyberBot v2** é a evolução do assistente pessoal estilo J.A.R.V.I.S. (Homem de Ferro) com arquitetura **core único + transports plugáveis**:

| Transport | Onde Roda | Caso de Uso |
|-----------|-----------|-------------|
| **Local** | Seu PC (PyQt6) | Controle total: apps, arquivos, mouse, keyboard, vision, WhatsApp, dev tools |
| **Remoto** | Browser (PWA) | Fale com JARVIS do celular/tablet/notebook alheio — voz nativa Gemini Live API |
| **Webhook** | REST API | WhatsApp Bridge (Baileys), automações externas, monitoramento |

**Não são dois JARVIS.** É **uma base de código**, compartilhando:
- 🧠 **Brain Engine** — 3 cérebros em cascata (Fast local <1s → Reserva → OpenRouter)
- 💾 **Memory Layer** — JSON estruturado + vector store opcional
- ⚡ **Action Registry** — 27 actions em 7 namespaces (`@action` decorator)
- 🔄 **Session Manager** — Histórico, tool chains, isolamento por transport

---

## 🏗️ Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                    JARVIS CORE (Processo Único)             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐            │
│  │   Brain     │ │  Memory     │ │  Actions    │            │
│  │  (3 cérebros)│ │  (JSON+Vec) │ │  (27 @action)│           │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘            │
└─────────┼───────────────┼───────────────┼────────────────────┘
          │               │               │
    ┌─────┴─────┐   ┌─────┴─────┐   ┌─────┴─────┐
    ▼           ▼   ▼           ▼   ▼           ▼
┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
│ LOCAL  │ │ REMOTO │ │ WHATSAPP │ │ DASHBOARD│ │ DEV    │
│ sound- │ │ WS bin │ │ Baileys  │ │ REST/WS  │ │ TOOLS  │
│ device │ │+Gemini │ │ (Node.js)│ │ metrics  │ │ codex  │
└────────┘ └────────┘ └────────┘ └────────┘ └────────┘
```

Ver [`ARQUITETURA_UNIFICADA_JARVIS.md`](ARQUITETURA_UNIFICADA_JARVIS.md) para detalhes técnicos completos.

---

## ✨ Funcionalidades (v2.0)

### 🧠 Brain Engine (Cascata Resiliente)
| Cérebro | Modelo | Latência | Uso |
|---------|--------|----------|-----|
| **FastBrain** | `gemini-3.5-flash-lite` (free) | **< 1s** | 95% das decisões |
| **GeminiBrain** | `gemini-2.5-flash` (pago) | 1-3s | Fallback cota/erro |
| **OpenRouterBrain** | Codex/outros (free) | 6-20s | Fallback final |

- Fallback automático em `429`, `503`, `404`, `timeout`
- Corrida de 2 modelos lite paralelos (quem responde primeiro vence)
- Mesmas regras do servidor (prompt copiado, travas anti-mentira)

### ⚡ 27 Actions (Auto-descobertas via `@action`)

| Namespace | Actions |
|-----------|---------|
| `computer` | `open_app`, `type_text`, `click`, `scroll`, `screenshot`, `window_control` |
| `files` | `read`, `write`, `list`, `search`, `zip`, `organize` |
| `web` | `search`, `browse`, `youtube`, `weather`, `flights` |
| `comm` | `whatsapp_send`, `whatsapp_read`, `send_message` |
| `dev` | `code_helper`, `dev_agent`, `codex_remote` |
| `media` | `video_edit`, `play_audio`, `record_screen` |
| `system` | `volume`, `brightness`, `monitor`, `reminder`, `proactive` |

### 🎤 Voice Pipeline

**Local (Desktop):**
```
Mic (16kHz) → sounddevice → Whisper large-v3 (GPU 0.39s / CPU 1.7s)
  → VAD local (threshold 0.09) → FastBrain → Gemini Live TTS (24kHz)
  → sounddevice → Speaker
```

**Remoto (Browser PWA):**
```
Mic → AudioWorklet (PCM16 16kHz) → WebSocket binário
  → Gemini Live API (bidiGenerateContent: STT+VAD+TTS+Turn detection nativo)
  → WebSocket binário → AudioWorklet (24kHz) → Speaker
```

### 💾 Memory Layer
- **Estruturada**: `identity`, `preferences`, `projects`, `relationships`, `wishes`, `notes`
- **Persistência**: JSON thread-safe (`memory/long_term.json`)
- **Prompt injection**: Formatado para cérebro (max 2000 chars)
- **Opcional**: Vector store (Chroma) para RAG sobre histórico

### 📱 WhatsApp Bridge (Baileys)
- Conexão única guardada (`geracao` counter anti-dual-connection)
- 28 actions: enviar/ler mídia, agendar, alertas de contatos/grupos
- DB JSON local (3k msgs, limpeza automática `[unknown]`)
- Poll de eventos para JARVIS processar (`/agent/events`)

### 🖥️ UI Desktop (PyQt6)
- **NucleoVisual**: Anéis animados 60fps (energia, anel interno/externo)
- **ConversationArea**: Bolhas chat + markdown
- **SettingsDialog**: Abas API Keys, Cores (picker RGB/HEX live), Voz, WhatsApp, Computer Use
- **SystemMetrics**: CPU, RAM, GPU (NVML), Rede, Temp tempo real
- **Tema dinâmico**: CSS injetado runtime via `core/tema.py`

### 🌐 UI Web (PWA)
- Vite + TypeScript + AudioWorklet + MediaPipe GestureRecognizer
- 7 gestos: pinça (arrastar), punho (fechar), joinha (confirmar), V (mic), etc.
- Cursor virtual suave (dedo indicador, x espelhado)
- Instalável (manifest + service worker), offline-first

---

## 🚀 Quick Start

### Pré-requisitos
- Python 3.11+
- Node.js 18+ (WhatsApp Bridge)
- NVIDIA GPU opcional (Whisper large-v3 + Kokoro TTS)
- Chave Gemini: [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

### Instalação Local

```bash
# 1. Clone
git clone https://github.com/prevprocesso-maker/carloscosta-jarvis.git
cd carloscosta-jarvis

# 2. Venv + deps
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev]"

# 3. Playwright (browser automation)
playwright install chromium

# 4. WhatsApp Bridge
cd whatsapp_bridge && npm install && cd ..

# 5. Config
cp config/api_keys.example.json config/api_keys.json
# Edite config/api_keys.json com sua GEMINI_API_KEY

# 6. Rode
python -m jarvis
```

### Desenvolvimento

```bash
# Testes
pytest tests/unit -v
pytest tests/integration -v

# Lint + Format
ruff check src/
ruff format src/

# Type check
mypy src/

# Hot reload
python scripts/dev.py
```

### Deploy (Docker)

```bash
# Build multi-arch
docker buildx build --platform linux/amd64,linux/arm64 -t carloscosta-jarvis:latest .

# Deploy em VPS
./docker/deploy.sh

# Ou docker compose local
docker compose -f docker/docker-compose.yml up -d
```

---

## ⚙️ Configuração

| Arquivo | Descrição |
|---------|-----------|
| `config/settings.yaml` | Config principal (YAML, versionado) |
| `config/api_keys.json` | Chaves sensíveis (**.gitignored**) |
| `.env` | Override de variáveis (**.gitignored**) |

**Exemplo `settings.yaml`:**
```yaml
app:
  name: "jarvis"
  token: "auto-gerado-no-primeiro-run"
  user:
    name: "Raphael"
    language: "pt-BR"

gemini:
  api_key: ""  # obrigatório - coloque em api_keys.json ou .env
  fast_model: "gemini-3.5-flash-lite"
  fast_fallback_model: "gemini-3.1-flash-lite"
  reserve_model: "gemini-2.5-flash"
  live_model: "gemini-2.5-flash-native-audio-preview"
  live_voice: "Charon"

brain:
  fast_timeout_ms: 9000
  reserve_timeout_ms: 30000
  cascade_on: ["429", "503", "404", "timeout"]

transports:
  local:
    enabled: true
    whisper_model: "large-v3"
    whisper_device: "auto"
    vad_threshold: 0.09
  remote:
    enabled: true
    port: 8000
    max_sessions: 10

ui:
  desktop_enabled: true
  web_enabled: true
  theme: "stark"

whatsapp:
  enabled: true
  bridge_port: 8765

deploy:
  traefik_enabled: false
  domain: "jarvis.seudominio.com"
  letsencrypt_email: "voce@email.com"
```

---

## 📁 Estrutura do Projeto

```
carloscosta-jarvis/
├── .github/workflows/ci.yml          # CI/CD (lint, test, build, deploy)
├── config/
│   ├── api_keys.example.json         # Template de chaves
│   ├── default.yaml                  # Config padrão
│   └── schema.json                   # JSON Schema validação
├── docker/
│   ├── Dockerfile                    # Multi-stage (Node → Python)
│   ├── docker-compose.yml            # Compose + Traefik opcional
│   └── deploy.sh                     # Build + tag + push + restart
├── src/jarvis/
│   ├── __init__.py
│   ├── __main__.py                   # Entry point: python -m jarvis
│   ├── bootstrap.py                  # DI Container + config
│   ├── core/
│   │   ├── brain/                    # FastBrain, GeminiBrain, OpenRouterBrain
│   │   ├── memory/                   # MemoryManager
│   │   ├── actions/                  # @action registry + 7 namespaces
│   │   └── session/                  # SessionManager
│   ├── transports/
│   │   ├── base.py                   # Transport protocol
│   │   ├── local/                    # sounddevice + Whisper + TTS
│   │   ├── remote/                   # FastAPI + WS + Gemini Live API
│   │   └── webhook/                  # REST + WhatsApp Bridge
│   ├── ui/
│   │   ├── desktop/                  # PyQt6 (NucleoVisual, Computer Use)
│   │   └── web/                      # Vite/TS + AudioWorklet + MediaPipe
│   └── integrations/                 # WhatsApp Bridge (subprocess)
├── tests/
│   ├── unit/                         # test_action_registry, test_memory...
│   ├── integration/                  # test_voice_loop_local, remote...
│   └── fixtures/
├── scripts/
│   ├── dev.py                        # Hot reload dev
│   ├── install.py                    # Instalador cross-platform
│   └── benchmark.py                  # Latência, cota, memória
├── ARCHITECTURE.md                   # Arquitetura técnica detalhada
├── PRD.md                            # Product Requirements Doc
├── pyproject.toml                    # Deps + tool config (ruff, mypy, pytest)
├── uv.lock                           # Lockfile reprodutível
└── README.md                         # Este arquivo
```

---

## 🧪 Testes & Qualidade

```bash
# Unit tests (obrigatório em toda PR)
pytest tests/unit -v --cov=src/jarvis --cov-report=term-missing

# Integration tests
pytest tests/integration -v

# Lint (ruff - rápido, substitui flake8+isort+black)
ruff check src/
ruff format src/

# Type check (mypy strict)
mypy src/

# Pre-commit (instala hooks)
pre-commit install
```

**Cobertura alvo:** >80% unit, >60% integration

---

## 📋 Roadmap (6 Fases)

| Fase | Status | Entregável |
|------|--------|------------|
| **1. Core Foundation** | ✅ Done | Brain protocol, Actions, Memory, Session, DI, Tests |
| **2. Transport Local** | 🔄 Em andamento | Whisper STT + VAD + Gemini Live TTS + voz local end-to-end |
| **3. Transport Remoto** | ⏳ Próxima | FastAPI + WS binário + Gemini Live API + Web PWA (AudioWorklet + MediaPipe) |
| **4. Desktop UI + Integrações** | ⏳ | PyQt6 modular + WhatsApp Bridge + Dashboard |
| **5. Deploy + Observabilidade** | ⏳ | Docker multi-stage + Traefik + Prometheus/Grafana + Structlog |
| **6. Polish + Release v2.0** | ⏳ | Docs completos, benchmarks, CHANGELOG, GitHub Release |

Ver [`PRD.md`](PRD.md) para plano detalhado com TDD strategy, ADRs, riscos e KPIs.

---

## 🤝 Contribuindo

1. Fork → branch `feature/nome` ou `fix/nome`
2. `pre-commit install` (hooks: ruff, mypy, pytest)
3. Commits convencionais: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`
4. PR → CI passa → Review → Merge (squash)

---

## 📄 Licença

MIT — veja [`LICENSE`](LICENSE)

---

## 🙏 Créditos

- **Gemini Live API** — Google AI (voz nativa bidirecional)
- **faster-whisper** — STT offline (Guillaume Klein)
- **Kokoro TTS** — Neural TTS offline (Hexgrad)
- **MediaPipe** — Gestos/hand landmarks no browser
- **Baileys** — WhatsApp Web API (@whiskeysockets)
- **PyQt6** — UI desktop nativa
- **FastAPI + Uvicorn** — Backend async

---

## 📞 Contato

**Carlos Costa** — `carlos@exemplo.com`  
Repo: https://github.com/prevprocesso-maker/carloscosta-jarvis  
Issues: https://github.com/prevprocesso-maker/carloscosta-jarvis/issues

---

> **Status Atual:** Phase 2 em desenvolvimento — Transport Local (voz desktop).  
> Branch ativa: `phase2/transport-local`  
> PRD: [`PRD.md`](PRD.md) | Arquitetura: [`ARQUITETURA_UNIFICADA_JARVIS.md`](ARQUITETURA_UNIFICADA_JARVIS.md)