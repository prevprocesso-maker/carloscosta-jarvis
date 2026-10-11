# JARVIS CyberBot v1.0.32 — Análise Completa de Arquitetura e Código

---

## 1. Visão Geral do Projeto

**JARVIS CyberBot** é um assistente pessoal de voz multimodal que roda localmente no computador do usuário, com:
- **Interface gráfica premium** (PyQt6) com visual "Iron Man" / JARVIS
- **Voz nativa** via Gemini Live API (modelos `gemini-2.5-flash-native-audio-preview`, `gemini-3.1-flash-live-preview`, `gemini-3.8-live`)
- **Cérebro rápido local** via Gemini gratuita do próprio usuário (modelos `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`)
- **Cérebro reserva** via OpenRouter (Codex) e Gemini paga
- **Controle total do computador** (abrir apps, digitar, clicar, vision, browser automation, file ops)
- **WhatsApp Bridge** (Baileys/Node.js) para envio/recebimento de mensagens
- **Dashboard remoto** (FastAPI + WebSocket + CryptoJS) para controle via LAN
- **Memória de longo prazo** estruturada por categorias
- **Computer Use** nativo (Playwright + pyautogui + pywinauto)

---

## 2. Arquitetura de Alto Nível

```
┌─────────────────────────────────────────────────────────────────┐
│                        MAIN.PY (Entry Point)                    │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │   UI Thread  │ │  Audio Thread│ │  Brain Thread│             │
│  │  (PyQt6)     │ │  (sounddevice)│ │ (asyncio)    │             │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘             │
└─────────┼────────────────┼────────────────┼─────────────────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      CORE MODULES                                │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌───────────┐  │
│  │ gemini_voice│ │cerebro_rapido│ │cerebro_gemini│ │cerebro_   │  │
│  │ .py         │ │ .py         │ │ .py         │ │openrouter │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └───────────┘  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌───────────┐  │
│  │ stt.py      │ │ tts.py      │ │ personas.py │ │ tema.py   │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └───────────┘  │
└─────────────────────────────────────────────────────────────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ACTIONS (27 módulos)                        │
│  computer_control, browser_control, file_controller,            │
│  usar_computador, vision_control, whatsapp_bridge,              │
│  code_helper, dev_agent, desenhar_na_tela, editar_video, ...    │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      EXTERNAL SERVICES                           │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌───────────┐  │
│  │ Gemini API  │ │ OpenRouter  │ │ WhatsApp    │ │ Dashboard │  │
│  │ (Voice+Brain)│ │ (Codex)     │ │ (Baileys)   │ │ (FastAPI) │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └───────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Módulos Principais — Análise Detalhada

### 3.1 `main.py` (12.074 linhas) — Orquestrador Central

**Responsabilidades:**
- Inicialização e patches de plataforma (Linux/macOS/Windows)
- Configuração de áudio (sounddevice, sample rates, chunk sizes)
- Carregamento de todas as actions (27 importações)
- Loop principal de voz: STT → Cerebro → TTS
- Gerenciamento de estado (memória, histórico, contexto)
- Fallback em cascata: `cerebro_rapido` → `cerebro_gemini` → `cerebro_openrouter` → `cerebro_codex`

**Pontos Críticos:**
- **Monolito único**: 12k linhas em um arquivo — difícil de manter/testar
- **Acoplamento forte**: importa tudo diretamente, sem injeção de dependência
- **Threading complexo**: 37+ threads relatadas, sem pool unificado
- **State global**: variáveis globais para modelos, timeouts, caches

**Padrões Notáveis:**
- `_saida_de_texto_a_prova_de_emoji()` — patch global para stdout/stderr UTF-8
- `subprocess.Popen` patchado com `CREATE_NO_WINDOW` no Windows
- `os.startfile` polyfill para macOS/Linux

---

### 3.2 `core/gemini_voice.py` (1.100+ linhas) — Voice Engine

**Arquitetura:**
- `GeminiCharonVoice` — sessão persistente WebSocket para áudio nativo
- **Corrida de conexões (hedging)**: abre 2ª conexão se 1ª não entrega áudio em ~2s
- **Troca de vencedor**: quem fala primeiro vira a conexão titular
- **Completar frase cortada**: detecta áudio truncado e reenvia o restante
- **Fila de modelos**: 3 modelos em ordem de prioridade (2.5 native → 3.1 live → 3.8 live)
- **Cota por sessão**: controle de `_DESCANSO_COTA_S` (5s) ao receber 429/1011

**Inovações Técnicas:**
- `stream_audio_persistent()` — streaming chunk-a-chunk com medição de latência por etapa (trava/conexão/envio/servidor)
- `_corrida()` / `_corrida_unica()` — hedging adaptativo com `prazo_da_corrida()` baseado em mediana histórica
- `warm_persistent_session()` — aquecimento assíncrono em background thread dedicado

**Problemas:**
- Classe de 1.100 linhas — viola SRP (faz conexão, streaming, hedging, logging, model fallback)
- `_VIVAS` WeakSet global para cleanup — acoplamento隐蔽
- Lógica de "completar frase" duplicada em dois métodos (`_o_que_faltou`, `_faltou_pela_transcricao`)

---

### 3.3 `core/cerebro_rapido.py` (807 linhas) — Fast Brain Local

**Conceito:** Decisão de ferramenta/localização rodando **local** com chave Gemini gratuita do usuário (0.66–0.85s)

**Arquitetura:**
- **Corrida entre 2 modelos lite** (`gemini-3.5-flash-lite` + `gemini-3.1-flash-lite`) — quem responde primeiro vence
- **Timeouts agressivos**: 9s decisão, 12s fila total, 60s/90s para texto longo
- **Prompt idêntico ao servidor** (copiado de `jarvis-cerebro.js`) — regras, ferramentas, travas anti-mentira
- **Travas de segurança**: `_PROMESSA_VAZIA`, `_NAO_SEI`, `_RECUSA_INVENTADA` — cobra o modelo se prometer sem fazer
- **Caber inteligente**: corta prompt pelo meio (35% cabeça + 65% cauda) preservando a fala do dono

**Fluxo:**
```
pergunta + histórico + ferramentas + contexto
         │
         ▼
    _gerar_em_corrida()  ──►  modelo A (1.6s)  │
         │                modelo B (paralelo)   │──► primeiro que responde
         ▼
    _extrair_json() → _limpar() → valida nomes válidos
         │
         ▼
    _prometeu_sem_fazer() / _recusou_sem_motivo() → 1 cobrança
         │
         ▼
    retorna {"fala": "...", "ferramenta": "...", "args": {...}, "faltam": true/false}
```

**Pontos Fortes:**
- Latência medida < 1s para decisões simples
- Funciona offline da VPS (resiliente)
- Mesmas regras do servidor = comportamento consistente

**Riscos:**
- Dependência total da cota gratuita Gemini (15 req/min por modelo)
- `_calado_ate` por modelo — pode silenciar modelo por 6h em 404
- Prompt de 3.000+ tokens a cada chamada — custo de latência

---

### 3.4 `core/cerebro_gemini.py` (265 linhas) — Fallback Brain

**Papel:** Reserva quando `cerebro_rapido` falha (cota, modelo fora, rede)

**Diferenças do `cerebro_rapido`:**
- Single model com fila de 3 modelos (sem corrida)
- `forcar_ferramenta` via `FunctionCallingConfigMode.ANY`
- Completa fala se ferramenta executou mas modelo não falou
- Timeout fixo 30s (vs 9/12s do rápido)

---

### 3.5 `core/cerebro_openrouter.py` (88.439 bytes) — Server Brain (Codex)

**Papel:** Cérebro principal histórico (OpenAI gpt-4o-mini → Codex)

**Características:**
- Tradução de ferramentas OpenAI ↔ Gemini
- Conversa multi-turno com histórico
- `escrever_longo()` para geração de código completo
- Métricas de latência detalhadas

---

### 3.6 `actions/usar_computador.py` (236.026 bytes) — Computer Use Engine

**O maior módulo de actions.** Funcionalidades:
- **Screen analysis**: `tela_visao.py`, `screen_processor.py`, `vision_control.py`
- **Input simulation**: `digitar_na_tela.py`, `desenhar_na_tela.py`, `entrada_uinput.py`
- **Window management**: `janelas_windows.py`, `computer_control.py`
- **Task orchestration**: `tarefa_comando.py`, `proactive.py`
- **Vision + Action loop**: vê tela → decide ação → executa → reavalia

**Arquitetura interna:**
- `UsarComputador` class com estado de tarefa ativa
- Integração com Playwright para browser automation
- `computer_control` como fallback genérico (pyautogui/pywinauto)
- Vision via `cerebro_rapido.olhar()` (Gemini flash-lite em 1.5–1.8s)

---

### 3.7 `whatsapp_bridge/server.js` (86.511 bytes) — WhatsApp Bridge

**Stack:** Node.js + Express + Baileys (@whiskeysockets/baileys v6.7.18)

**Features:**
- **Single connection guard** (`geracao` counter) — evita dual-connection bug
- **QR Code** via endpoint HTTP + arquivo PNG
- **Message DB** JSON local (3.000 msg cap, limpeza de `[unknown]`)
- **Alert watch** — monitora contatos/grupos específicos
- **Agent queue** — fila de eventos para JARVIS processar
- **Media download** com ffmpeg-static
- **Rate-limited DB saves** (1.5s debounce)

**Endpoints principais:**
- `GET /status` — estado conexão, QR, contagens
- `POST /send` — enviar mensagem/mídia
- `POST /reset` — nova conexão (QR)
- `GET /messages` — histórico
- `POST /watch` — configurar alertas
- `GET /agent/events` — poll para JARVIS

---

### 3.8 `dashboard/server.py` (1.202 linhas) — Remote Dashboard

**Stack:** FastAPI + Uvicorn + WebSocket + CryptoJS (AES-256-CBC)

**Segurança:**
- Chave de sessão gerada no login (AES-256-CBC, SHA-256 derive)
- CryptoJS servido localmente (auto-download CDN once)
- Firewall auto-config (Windows netsh, macOS pfctl, Linux iptables/ufw)

**Features:**
- WebSocket para comandos em tempo real
- Upload de arquivos (500MB max)
- System metrics (CPU, RAM, GPU via NVML, rede, temp)
- Remote device pairing (AES encrypted)
- Tela ao vivo (screenshot stream)

---

### 3.9 `memory/memory_manager.py` (156 linhas) — Long-term Memory

**Estrutura:**
```json
{
  "identity": {},
  "preferences": {},
  "projects": {},
  "relationships": {},
  "wishes": {},
  "notes": {}
}
```

**Mecânica:**
- `update_memory()` — merge recursivo com truncagem (380 chars/valor, 2.200 chars total)
- `format_memory_for_prompt()` — injeta no prompt do cérebro (max 2.000 chars)
- Thread-safe com `Lock`
- Persistência em `memory/long_term.json`

---

### 3.10 `ui.py` (5.899 linhas) — Interface Gráfica

**Stack:** PyQt6 com estilo "Premium Visual" (ciano JARVIS, âmbar, verde, fundo azul-noite)

**Componentes:**
- `MainWindow` — janela principal com barra lateral (96px) + área central
- `NucleoVisual` — animação de anéis (energia, anel interno/externo) 60fps
- `ConversationArea` — bolhas de chat com markdown rendering
- `SettingsDialog` — abas: API Keys, Cores, Voz, WhatsApp, Computer Use, Avançado
- `ColorCustomizer` — picker RGB/HEX live preview
- `SystemMetricsWidget` — CPU/RAM/GPU/Net/Temp em tempo real

**Tema:**
- `core/tema.py` — engine de CSS dinâmico com variáveis de cor
- `QWidget.setStyleSheet` monkey-patched para injeção automática de tema
- Cores salvas em `config/tema_usuario.json`

---

## 4. Fluxo de Dados — Voz Completa

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Microfone  │────►│   STT       │────►│  Cerebro    │────►│   TTS       │
│  (sounddev) │     │  (Whisper)  │     │  (Gemini)   │     │  (Gemini    │
└─────────────┘     └─────────────┘     └─────────────┘     │   Live)     │
                                                           └──────┬──────┘
                                                                  │
                                                           ┌──────▼──────┐
                                                           │  Sounddevice│
                                                           │  RawOutput  │
                                                           └─────────────┘
```

**Detalhes por etapa:**

1. **STT** (`main.py` + `faster_whisper`):
   - GPU: `large-v3` CUDA (0.39s) | CPU: `small` int8 (1.7s)
   - Filtros: pico mínimo 0.09, resgate 0.045, anti-alucinação, anti-eco vocabulario
   - Correção de nomes próprios via `difflib` (cutoff 0.75)

2. **Vocativo** — Detecta "JARVIS", "Friday", nome da voz escolhida (som fonético, SequenceMatcher 0.85)

3. **Cérebro** — Cascata:
   ```
   cerebro_rapido (local, 0.7s) 
       │ falha (cota/404/503/timeout)
       ▼
   cerebro_gemini (reserva paga, 1-3s)
       │ falha
       ▼
   cerebro_openrouter (Codex, 6-20s)
       │ falha
       ▼
   Resposta escrita "Chefe, o cerebro não respondeu..."
   ```

4. **TTS** — `GeminiCharonVoice`:
   - Sessão persistente aquecida (0.65s cold → 1.2s warm)
   - Hedging: 2ª conexão em `prazo_da_corrida()` (mediana + 0.7s, piso 1.5s)
   - Completa frase se áudio < 55% do esperado
   - 11 vozes disponíveis (Charon, Puck, Zephyr, etc.)

---

## 5. Dependências e Requisitos

### Python (requirements.txt)
```
PyQt6>=6.7              # UI
sounddevice             # Audio I/O
google-genai            # Gemini SDK (Live + Gen)
google-generativeai     # Legacy Gemini
pillow                  # Imagens
requests                # HTTP
beautifulsoup4          # HTML parsing
duckduckgo-search       # Web search
playwright              # Browser automation
pyautogui               # Input simulation
pyperclip               # Clipboard
pygetwindow             # Window management
opencv-python           # Vision
numpy                   # Array math
mss                     # Screenshot
psutil                  # System metrics
send2trash              # Safe delete
youtube-transcript-api  # YouTube
python-pptx             # PowerPoint
fastapi                 # Dashboard API
uvicorn[standard]       # ASGI server
cryptography            # AES encryption
python-multipart        # File upload
comtypes; win32         # COM Windows
pycaw; win32            # Audio control Windows
win10toast; win32       # Notifications Windows
pywinauto; win32        # UI Automation Windows
qrcode[pil]             # QR codes
mediapipe               # Gesture recognition
```

### Node.js (whatsapp_bridge/package.json)
```
@whiskeysockets/baileys@^6.7.18  # WhatsApp Web API
express@^4.19.2                  # HTTP server
qrcode@^1.5.4                    # QR generation
pino@^9.3.2                      # Logging
cors@^2.8.5                      # CORS
ffmpeg-static@^5.2.0             # Media conversion
mime-types@^2.1.35               # MIME detection
```

### Models (bundled)
- `models/gesture_recognizer.task` (8.4MB) — MediaPipe gesture recognition
- `models/whisper-small/` (bundled no instalador) — STT offline fallback

---

## 6. Pontos de Atenção / Dívida Técnica

### 6.1 Arquitetura
| Item | Severidade | Descrição |
|------|------------|-----------|
| **Monolito main.py** | 🔴 Crítico | 12k linhas, 170+ imports, 37 threads, state global |
| **Acoplamento forte** | 🔴 Crítico | Actions importadas diretamente, sem DI/registry |
| **God Classes** | 🟡 Alto | `GeminiCharonVoice` (1100 linhas), `UsarComputador` (236KB) |
| **Sem testes** | 🟡 Alto | Zero test files, zero CI/CD |

### 6.2 Confiabilidade
| Item | Severidade | Descrição |
|------|------------|-----------|
| **Cota Gemini** | 🔴 Crítico | Plano gratuito: 15 req/min/modelo, 10 sessões voz/dia. Sem backoff exponencial robusto. |
| **Model rot** | 🟡 Alto | Nomes de modelo expiram (404). Fila hardcoded, sem auto-discovery. |
| **Thread safety** | 🟡 Alto | `threading.Lock` em memory, mas state global em `cerebro_rapido` (`_calado_ate`, `_sem_minimal`) |
| **Error handling** | 🟡 Alto | Muitos `except Exception: pass` silenciosos |

### 6.3 Performance
| Item | Severidade | Descrição |
|------|------------|-----------|
| **Prompt size** | 🟡 Alto | ~3-5k tokens/turno no `cerebro_rapido` (regras + ferramentas + histórico) |
| **Whisper reload** | 🟢 Baixo | Modelo carregado once no startup — OK |
| **DB WhatsApp** | 🟡 Alto | JSON file 3MB, save debounced 1.5s — OK mas frágil |
| **UI animations** | 🟢 Baixo | 60fps nuclei visual — GPU accelerated |

### 6.4 Segurança
| Item | Severidade | Descrição |
|------|------------|-----------|
| **API keys** | 🟢 Baixo | Em `config/api_keys.json` (gitignored), exemplo fornecido |
| **Dashboard** | 🟡 Médio | AES-256-CBC session key, mas sem rate limit / brute force protection |
| **WhatsApp** | 🟡 Médio | Baileys armazena credenciais em `auth/` — filesystem access |
| **Computer Use** | 🔴 Crítico | Execução arbitrária de código/comandos — sem sandbox/confirmation |

### 6.5 Manutenibilidade
| Item | Severidade | Descrição |
|------|------------|-----------|
| **Type hints** | 🟢 Baixo | Parciais, `from __future__ import annotations` usado |
| **Docs inline** | 🟢 Baixo | Excelentes comentários com datas/medições/razões |
| **Modularização** | 🔴 Crítico | Tudo em `actions/` flat, sem subpackages por domínio |
| **Configuração** | 🟡 Médio | Espalhada: `config/`, `core/`, env vars, hardcoded constants |

---

## 7. Oportunidades de Melhoria (Roadmap)

### 7.1 Curto Prazo (1-2 semanas)
1. **Modularizar `main.py`** → `app/` package com `bootstrap.py`, `orchestrator.py`, `voice_loop.py`
2. **Registry de Actions** — `@action` decorator + discovery automático
3. **Config centralizada** — Pydantic Settings + `config.yaml` + env override
4. **Health checks** — `/health` endpoint no dashboard + watchdog no main
5. **Structured logging** — `structlog` + JSONL + rotação, substituir `print`/`_voice_diag`

### 7.2 Médio Prazo (1-2 meses)
6. **Plugin system** — Actions como plugins carregáveis (entry points)
7. **Sandbox Computer Use** — Docker/WSL2 isolamento + policy engine
8. **Multi-provider STT/TTS** — Whisper.cpp local, Piper, ElevenLabs, OpenAI TTS
9. **Conversation memory** — RAG sobre histórico (vector DB: Chroma/Qdrant local)
10. **Telemetry opt-in** — Métricas de latência/erro/uso para melhoria contínua

### 7.3 Longo Prazo (3-6 meses)
11. **Multi-tenant** — Perfis de usuário isolados (família, equipe)
12. **Mobile companion** — React Native / Flutter app + WebRTC para voz
13. **Agent marketplace** — Skills installáveis (VS Code style)
14. **Self-hosted LLM** — Ollama / llama.cpp integration para offline total
15. **CI/CD Pipeline** — GitHub Actions: lint, typecheck, test, build exe, release

---

## 8. Checklist para Publicação no GitHub

### Preparação do Repo
- [ ] **Remover binários**: `JARVIS-CyberBot.exe` (273KB) — não versionar executáveis
- [ ] **Remover models pesados**: `models/gesture_recognizer.task` (8.4MB) — usar Git LFS ou download no install
- [ ] **Sanitizar `config/api_keys.json`** — manter apenas `api_keys.example.json`
- [ ] **Adicionar `.gitignore`** robusto (venv, __pycache__, logs, media, auth, uploads, *.pyc)
- [ ] **Criar `LICENSE`** (MIT/Apache-2.0 recomendado)
- [ ] **Criar `CONTRIBUTING.md`** com guidelines

### Documentação
- [ ] **README.md** completo: badges, arquitetura, quickstart, screenshots, roadmap
- [ ] **ARCHITECTURE.md** — este documento expandido
- [ ] **DEVELOPMENT.md** — setup dev, debug, test, hot reload
- [ ] **DEPLOYMENT.md** — instalador, auto-update, code signing
- [ ] **API_REFERENCE.md** — endpoints dashboard, websocket events, action schemas

### CI/CD
- [ ] **GitHub Actions**:
  - `lint.yml` — ruff + mypy + pre-commit
  - `test.yml` — pytest (quando existirem testes)
  - `build.yml` — PyInstaller/Nuitka para Windows/macOS/Linux
  - `release.yml` — tag → build → GitHub Release + assets

### Segurança
- [ ] **Dependabot** — alerts + auto-merge patch
- [ ] **CodeQL** — static analysis
- [ ] **Secret scanning** — push protection
- [ ] **SBOM** — `cyclonedx-bom` para supply chain

---

## 9. Estrutura Recomendada Pós-Refatoração

```
jarvis-cyberbot/
├── .github/
│   ├── workflows/
│   └── dependabot.yml
├── config/
│   ├── api_keys.example.json
│   ├── default_settings.yaml
│   └── schema.json
├── src/
│   ├── jarvis/
│   │   ├── __init__.py
│   │   ├── bootstrap.py          # App initialization
│   │   ├── orchestrator.py       # Main loop coordinator
│   │   ├── voice_loop.py         # STT → Brain → TTS pipeline
│   │   ├── config.py             # Pydantic Settings
│   │   ├── logging.py            # Structured logging setup
│   │   ├── plugins/
│   │   │   ├── __init__.py
│   │   │   ├── registry.py       # @action decorator
│   │   │   └── loader.py
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── brain/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── base.py       # AbstractBrain
│   │   │   │   ├── fast.py       # cerebro_rapido
│   │   │   │   ├── gemini.py     # cerebro_gemini
│   │   │   │   ├── openrouter.py
│   │   │   │   └── factory.py    # BrainFactory with fallback chain
│   │   │   ├── voice/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── gemini_live.py
│   │   │   │   ├── stt.py
│   │   │   │   └── tts.py
│   │   │   ├── memory/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── manager.py
│   │   │   │   └── vector_store.py
│   │   │   ├── persona/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── loader.py
│   │   │   │   └── styles.py
│   │   │   └── theme/
│   │   │       ├── __init__.py
│   │   │       └── engine.py
│   │   ├── actions/
│   │   │   ├── __init__.py
│   │   │   ├── computer/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── control.py
│   │   │   │   ├── vision.py
│   │   │   │   ├── input.py
│   │   │   │   └── browser.py
│   │   │   ├── files/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── controller.py
│   │   │   │   └── processor.py
│   │   │   ├── web/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── search.py
│   │   │   │   └── browser.py
│   │   │   ├── communication/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── whatsapp.py
│   │   │   │   └── messaging.py
│   │   │   ├── development/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── code_helper.py
│   │   │   │   └── dev_agent.py
│   │   │   └── media/
│   │   │       ├── __init__.py
│   │   │       ├── video_editor.py
│   │   │       └── youtube.py
│   │   ├── ui/
│   │   │   ├── __init__.py
│   │   │   ├── main_window.py
│   │   │   ├── widgets/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── nuclei.py
│   │   │   │   ├── conversation.py
│   │   │   │   ├── metrics.py
│   │   │   │   └── settings.py
│   │   │   └── theme/
│   │   │       ├── __init__.py
│   │   │       └── styles.qss
│   │   ├── dashboard/
│   │   │   ├── __init__.py
│   │   │   ├── server.py
│   │   │   ├── auth.py
│   │   │   ├── websocket.py
│   │   │   └── static/
│   │   └── whatsapp_bridge/
│   │       ├── package.json
│   │       ├── server.js
│   │       └── types.d.ts
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── scripts/
│   ├── install.sh
│   ├── build.ps1
│   └── dev.py
├── docs/
│   ├── architecture.md
│   ├── development.md
│   ├── deployment.md
│   └── api_reference.md
├── pyproject.toml
├── uv.lock / requirements.txt
├── README.md
├── LICENSE
├── CONTRIBUTING.md
└── CHANGELOG.md
```

---

## 10. Comandos Úteis para Início Rápido

```bash
# Clone e setup
git clone <repo>
cd JARVIS-CyberBot

# Python 3.11+
python -m venv venv
venv\Scripts\activate  # Windows
pip install -U pip setuptools wheel
pip install -r requirements.txt
playwright install chromium

# WhatsApp Bridge
cd whatsapp_bridge
npm install
cd ..

# Config
cp config/api_keys.example.json config/api_keys.json
# Edite config/api_keys.json com sua chave Gemini (aistudio.google.com/apikey)

# Run
python main.py

# Dashboard (terminal separado)
python -m dashboard.server

# WhatsApp Bridge (terminal separado)
cd whatsapp_bridge && npm start
```

---

## 11. Conclusão

**JARVIS CyberBot v1.0.32** é um projeto **impressionantemente funcional** com:
- ✅ Arquitetura de voz nativa inovadora (Gemini Live + hedging adaptativo)
- ✅ Cérebro local rápido (<1s) com fallback resiliente em cascata
- ✅ Computer Use completo (vision + action loop)
- ✅ UI premium com tema dinâmico
- ✅ WhatsApp Bridge robusto (single-connection guard)
- ✅ Dashboard remoto seguro (AES-256 + WebSocket)

**Principais riscos para produção:**
1. **Monolito main.py** — bloqueia manutenção e testes
2. **Cota Gemini gratuita** — limite duro, sem enterprise SLA
3. **Computer Use sem sandbox** — risco de segurança crítico
4. **Ausência de testes/CI** — regressões silenciosas

**Recomendação:** Priorizar refatoração modular (Seção 9) + sandbox Computer Use + testes antes de abrir para comunidade ampla. O core de voz/cérebro é **sólido e inovador** — vale a pena preservar e evoluir.

---

*Relatório gerado em 11/10/2026 — Análise baseada no código-fonte sanitizado v1.0.32*