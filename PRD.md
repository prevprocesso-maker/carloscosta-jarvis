# PRD — JARVIS CyberBot v2 (Arquitetura Unificada)

## 1. Visão Geral

**Produto:** Assistente pessoal multimodal estilo J.A.R.V.I.S. com **core único** e **transports plugáveis**.

**Proposta de Valor:** Um só JARVIS que roda local (controle total do PC) e remoto (voz nativa no browser de qualquer dispositivo), compartilhando cérebro, memória e actions.

**Diferencial:** Não é "dois JARVIS colados". É **uma base de código**, dois transports, duas UIs.

---

## 2. Usuários-Alvo

| Perfil | Necessidade | Transport |
|--------|-------------|-----------|
| Power user (você) | Controlar PC por voz, WhatsApp, Computer Use, files, dev | Local (PyQt6) |
| Você fora de casa | Falar com JARVIS do celular/tablet, ver status | Remoto (PWA) |
| Família/Equipe | Acesso compartilhado controlado | Remoto (token) |
| Integrações | Webhooks, automações externas | Webhook (REST) |

---

## 3. Funcionalidades (MVP v2.0)

### 3.1 Core (Compartilhado)
- [x] **Brain Protocol** — Interface única para 3 cérebros em cascata
- [x] **FastBrain** — Gemini free (`gemini-3.5-flash-lite`) <1s decisão
- [x] **GeminiBrain** — Reserva paga (`gemini-2.5-flash`)
- [x] **OpenRouterBrain** — Fallback final (Codex/outros)
- [x] **Action Registry** — `@action` decorator, 27 actions em 7 namespaces
- [x] **MemoryManager** — JSON estruturado (identity, prefs, projects, relationships, wishes, notes)
- [x] **SessionManager** — Histórico, tool chains, isolamento por transport
- [x] **DI Container** — Pydantic Settings (YAML + env), config centralizada

### 3.2 Transport Local (Desktop)
- [ ] Whisper STT (large-v3 GPU / small CPU) + VAD local
- [ ] Gemini Live TTS streaming (PCM16 24kHz)
- [ ] PyQt6 UI: NucleoVisual, Computer Use, Settings, Metrics
- [ ] 27 actions funcionando nativamente (mouse, keyboard, vision, files, browser)

### 3.3 Transport Remoto (Web)
- [ ] FastAPI + WebSocket binário (`/ws/voice`)
- [ ] Gemini Live API nativo (bidiGenerateContent) — STT+VAD+TTS+Turn detection em 1 stream
- [ ] Frontend PWA: Vite/TS + AudioWorklet (PCM16 16kHz↔24kHz) + MediaPipe Gestures
- [ ] Auth: Bearer token + HttpOnly cookie
- [ ] Instalável (manifest + service worker), offline-first

### 3.4 Transport Webhook (Integrações)
- [ ] REST API (`/healthz`, `/metrics`, `/webhooks/*`)
- [ ] WhatsApp Bridge (Baileys/Node.js) como subprocess gerenciado
- [ ] Rate limiting, auth, observabilidade

### 3.5 Deploy & Observabilidade
- [ ] Docker multi-stage (Node build → Python runtime)
- [ ] Docker Compose + `deploy.sh` (build, tag, push, restart)
- [ ] Traefik + Let's Encrypt (opcional, para HTTPS remoto)
- [ ] Structlog JSONL + Prometheus metrics + Grafana dashboards
- [ ] Healthchecks + alertas

---

## 4. Requisitos Não-Funcionais

| Critério | Target |
|----------|--------|
| Latência voz local (mic → resposta áudio) | < 2s |
| Latência voz remoto (browser → resposta áudio) | < 2.5s |
| Disponibilidade | 99.5% (excluindo cota Gemini) |
| Memória RAM (idle) | < 500MB |
| CPU (idle) | < 5% |
| Cold start (docker) | < 30s |
| Hot reload dev | < 3s |

---

## 5. Arquitetura Técnica

### 5.1 Protocols (Contratos)

```python
# Brain — src/jarvis/core/brain/base.py
class Brain(ABC):
    async def process(context: BrainContext, audio_chunk?, text_input?) -> AsyncIterator[BrainResponse]
    async def interrupt(session_id: str)
    def get_available_tools() -> list[dict]
    async def tts_stream(text: str, voice?) -> AsyncIterator[bytes]

# Transport — src/jarvis/transports/base.py
class Transport(ABC):
    async def start(on_audio_out, on_text_out, on_tool_call)
    async def stop()
    async def send_audio_in(chunk: bytes)
    async def send_text_in(text: str)
    async def send_interrupt()

# Action — src/jarvis/core/actions/registry.py
@action(namespace="computer", description="...")
def open_app(name: str) -> dict: ...
```

### 5.2 Fluxo de Dados

```
LOCAL:  Mic → sounddevice → Whisper → FastBrain → Action → Gemini Live TTS → sounddevice → Speaker
REMOTO: Browser → AudioWorklet → WS binary → Gemini Live API → Action → WS binary → AudioWorklet → Speaker
```

### 5.3 Fallback Chain

```
FastBrain (Gemini free, <1s)
    ↓ 429/503/404/timeout
GeminiBrain (Gemini paga, 1-3s)
    ↓ falha
OpenRouterBrain (Codex, 6-20s)
    ↓ falha
Resposta de erro amigável
```

---

## 6. Plano de Ação (6 Fases / ~6 Semanas)

### Fase 1: Core Foundation ✅ **CONCLUÍDA**
- [x] Brain protocol + 3 implementações
- [x] Action registry + `@action` decorator
- [x] MemoryManager + SessionManager
- [x] DI Container + Pydantic Settings
- [x] Testes unitários (registry, memory)
- [x] pyproject.toml (ruff, mypy, pytest)
- [x] README + .gitignore

**Branch:** `phase1/core-foundation`  
**Commit:** `22622be`

---

### Fase 2: Transport Local (Semana 2)
**Objetivo:** Voz local funcional end-to-end no desktop.

**Entregáveis:**
- `src/jarvis/transports/local/audio.py` — LocalAudioTransport
- Whisper STT + VAD (threshold calibrável)
- Gemini Live TTS streaming (reuso do `gemini_voice.py`)
- Integração com FastBrain + SessionManager
- Testes: `test_voice_loop_local.py`

**Critério de Aceite:** `python -m jarvis --transport local` → fala "que horas são" → responde com áudio <2s.

---

### Fase 3: Transport Remoto + Web UI (Semana 3)
**Objetivo:** Voz nativa no browser via Gemini Live API.

**Entregáveis:**
- `src/jarvis/transports/remote/server.py` — FastAPI + WebSocket `/ws/voice`
- `src/jarvis/transports/remote/gemini_live.py` — Wrapper Gemini Live API
- Frontend: `src/jarvis/transports/remote/frontend/` (Vite/TS)
  - AudioWorklet (PCM16 16kHz capture / 24kHz playback)
  - MediaPipe GestureRecognizer (7 gestos, hand landmarks)
  - HUD: orbe pulsante, log transcrição, painéis arrastáveis
  - PWA (manifest + service worker)
- Auth: Bearer token + HttpOnly cookie
- Testes: `test_voice_loop_remote.py` (Playwright)

**Critério de Aceite:** `https://jarvis.local/ws/voice` → microfone → resposta áudio <2.5s.

---

### Fase 4: Desktop UI + WhatsApp + Dashboard (Semana 4)
**Objetivo:** UI nativa completa + integrações.

**Entregáveis:**
- `src/jarvis/ui/desktop/` — Modularização do `ui.py` atual
  - `main_window.py`, `widgets/` (NucleoVisual, ConversationArea, Metrics, Settings)
  - `theme/` (engine CSS dinâmico + ColorCustomizer)
- WhatsApp Bridge integrado como subprocess gerenciado
- Dashboard REST + WebSocket (métricas, logs, device pairing)
- Computer Use actions conectadas no transport local

**Critério de Aceite:** UI desktop abre, configurações funcionam, WhatsApp envia/recebe, métricas tempo real.

---

### Fase 5: Deploy + Observabilidade (Semana 5)
**Objetivo:** Um comando para subir em produção.

**Entregáveis:**
- `docker/Dockerfile` multi-stage
- `docker/docker-compose.yml` (jarvis + traefik opcional)
- `docker/deploy.sh` (build → tag → push → restart)
- Traefik + Let's Encrypt HTTP-01 (para domínio remoto)
- Structlog JSONL → Loki/Grafana
- Prometheus metrics (`/metrics`) → Grafana
- Healthchecks + alertas (cota Gemini, disco, memória)

**Critério de Aceite:** `./docker/deploy.sh` sobe tudo com HTTPS válido em VPS limpa.

---

### Fase 6: Polish + Docs + Release (Semana 6)
**Objetivo:** Pronto para uso real e contribuição.

**Entregáveis:**
- `ARCHITECTURE.md` (este doc expandido)
- `DEVELOPMENT.md` (hot reload, debug, test, add action)
- `DEPLOYMENT.md` (VPS, Docker, Traefik, backup)
- `API_REFERENCE.md` (endpoints, WebSocket events, action schemas)
- Benchmarks: latência, cota, memória, cold start
- CHANGELOG.md v2.0.0
- Tag `v2.0.0` + GitHub Release com assets

---

## 7. Test Strategy (TDD)

### 7.1 Unit Tests (Obrigatório para toda PR)
| Módulo | Cobertura Mínima | Arquivo |
|--------|------------------|---------|
| Action Registry | 100% | `test_action_registry.py` |
| MemoryManager | 90% | `test_memory.py` |
| SessionManager | 90% | `test_session.py` |
| Brain Protocol | 80% | `test_brain_protocol.py` |
| FastBrain (mocked) | 70% | `test_fast_brain.py` |

### 7.2 Integration Tests
| Cenário | Arquivo |
|---------|---------|
| Voz local: mic → STT → Brain → TTS → speaker | `test_voice_loop_local.py` |
| Voz remoto: browser WS → Live API → Brain → WS → browser | `test_voice_loop_remote.py` |
| Action chain: multi-step (abrir → digitar → salvar) | `test_action_chain.py` |
| Fallback chain: Fast → Reserve → OpenRouter | `test_fallback_chain.py` |
| WhatsApp: send → receive → alert | `test_whatsapp_bridge.py` |

### 7.3 E2E Tests (Playwright)
- Login + token auth
- Microfone → transcrição → resposta áudio
- Gestos MediaPipe (pinça, punho, joinha)
- PWA install + offline

### 7.4 CI Pipeline (GitHub Actions)
```yaml
# .github/workflows/ci.yml
on: [push, pull_request]
jobs:
  lint: ruff + mypy
  test: pytest unit + integration
  build: docker buildx (amd64/arm64)
  deploy: (main branch only) → deploy.sh
```

---

## 8. Riscos & Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|---------------|---------|-----------|
| Cota Gemini free exaurida | Alta | Crítico | Fallback chain + cache de decisões + alerta cota |
| Modelo Gemini aposentado (404) | Média | Alto | Fila de modelos + auto-discovery futuro |
| Whisper GPU falha | Baixa | Médio | Fallback CPU automático (já implementado) |
| WhatsApp Bridge desconecta | Média | Alto | Single-connection guard + reconexão exponencial |
| AudioWorklet não suportado | Baixa | Médio | Fallback ScriptProcessor (deprecated mas funcional) |
| Traefik certificado falha | Baixa | Médio | HTTP-01 challenge + retry + alerta |

---

## 9. Métricas de Sucesso (KPIs)

| Métrica | Baseline (v1) | Target (v2) |
|---------|---------------|-------------|
| Latência voz local | ~3-5s | < 2s |
| Latência voz remoto | N/A | < 2.5s |
| Taxa fallback (cota) | ~30% | < 5% |
| Uptime | Manual | 99.5% |
| Tempo deploy | 30min manual | < 5min automatizado |
| Cobertura testes | 0% | > 80% unit, > 60% integration |

---

## 10. Decisões Arquiteturais (ADRs)

| ADR | Decisão | Data |
|-----|---------|------|
| ADR-001 | Core isolado + transports plugáveis | 11/10/2026 |
| ADR-002 | `@action` decorator para descoberta automática | 11/10/2026 |
| ADR-003 | Pydantic Settings (YAML + env) para config | 11/10/2026 |
| ADR-004 | Gemini Live API nativo para transport remoto | 11/10/2026 |
| ADR-005 | AudioWorklet para áudio binário no browser | 11/10/2026 |
| ADR-006 | Docker Compose (não Swarm) para deploy | 11/10/2026 |
| ADR-007 | Structlog + Prometheus para observabilidade | 11/10/2026 |

---

## 11. Próximos Passos Imediatos

1. **Criar repo no GitHub** → `git remote add origin <url>` → `git push -u origin phase1/core-foundation`
2. **Configurar GitHub Actions** (CI workflow)
3. **Iniciar Fase 2** — `LocalAudioTransport` + testes de voz local
4. **Registrar decisões no SEGUNDO-CEREBRO** (projetos/jarvis-cyberbot.md)

---

## 12. Aprovação

- [ ] **Tech Lead** (você): Arquitetura + stack aprovada
- [ ] **Product Owner** (você): Escopo + priorização confirmados
- [ ] **DevOps** (você): Deploy + observabilidade viáveis

---

*PRD v1.0 — 11/10/2026 — Baseado na arquitetura em `ARQUITETURA_UNIFICADA_JARVIS.md`*