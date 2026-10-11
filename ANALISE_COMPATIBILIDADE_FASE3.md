# Análise Completa de Compatibilidade — Fase 3 (Transport Remoto + Gestos)

---

## 1. O Que O Prompt Exige (Gestos) vs O Que Existe

### ✅ Especificado no Prompt (linha 29)
| Gesto | Ação | Detalhe Técnico |
|-------|------|-----------------|
| **Pinça** (polegar+indicador) | Agarra e arrasta painéis | Cursor virtual segue dedo indicador, suavização exponencial, x espelhado |
| **Punho fechado** | Fecha painel sobre o qual está | Detecção sobre painel específico |
| **"V"** (dedos indicador+médio) | Liga/desliga microfone | Toggle mic |
| **Joinha** (polegar para cima) | Confirma | Confirmação de ação |
| **Esqueleto da mão** | Visualização | Canvas sobre vídeo da webcam, 21 landmarks |

### 🔧 Stack Exigida
- `@mediapipe/tasks-vision` — `GestureRecognizer` + `HandLandmarker`
- Modo `VIDEO` (streaming), 1 mão
- AudioWorklet: PCM16 16kHz capture → 24kHz playback
- WebSocket binário: `/ws` (PCM16 16kHz ↑, 24kHz ↓, JSON para controle)

---

## 2. Compatibilidade: Instalação Existente vs Nova Arquitetura

### 2.1 Instalação Atual (C:\Users\NIKE\AppData\Local\Programs\JARVIS-CyberBot\)
```
├── JARVIS-CyberBot.exe          # 273KB - PyInstaller build
├── main.py                      # 665KB - Monolito v1.0.32
├── ui.py                        # 259KB - PyQt6 desktop
├── core/gemini_voice.py         # 88KB - Live API session
├── core/stt.py                  # Whisper + Vosk
├── core/tts.py                  # EdgeTTS + Kokoro + ElevenLabs
├── actions/                     # 27 actions
├── dashboard/                   # FastAPI + WebSocket (relay)
├── whatsapp_bridge/             # Node.js Baileys
├── models/gesture_recognizer.task  # 8.4MB - MediaPipe task
└── config/                      # api_keys.json, jarvis.ico
```

### 2.2 Nova Arquitetura (src/jarvis/)
```
src/jarvis/
├── core/                        # Brain, Memory, Actions, Session (SHARED)
├── transports/
│   ├── local/                   # ✅ Phase 2 - Whisper + VAD + TTS
│   ├── remote/                  # 🔄 Phase 3 - Gemini Live API + WebSocket
│   └── webhook/                 # WhatsApp Bridge
├── ui/
│   ├── desktop/                 # PyQt6 (existente)
│   └── web/                     # 🔄 Phase 3 - Vite/TS + AudioWorklet + MediaPipe
└── __main__.py                  # Entry point unificado
```

### 2.3 Pontos de Conflito Real

| Componente | Existente (v1) | Nova (v2) | Resolução |
|------------|----------------|-----------|-----------|
| **STT** | Whisper local (faster-whisper) | **Remoto**: Gemini Live API (nativo) | **Separar transports** — local usa Whisper, remoto usa Live API |
| **TTS** | EdgeTTS/Kokoro/ElevenLabs | **Remoto**: Live API nativo | **Separar** — local usa engines, remoto usa Live API |
| **VAD** | Threshold local (0.09) | **Remoto**: Live API VAD automático | **Não conflitam** — transports isolados |
| **Gestos** | `models/gesture_recognizer.task` (Python) | **Remoto**: MediaPipe no browser (JS) | **Complementares** — desktop usa Python, web usa JS |
| **WebSocket** | Dashboard relay (JSON) | **Novo**: `/ws/voice` binário | **Endpoints diferentes** — `/ws/voice` vs `/ws/relay` |
| **Auth** | Token no dashboard | **Novo**: Bearer token + HttpOnly cookie | **Compatível** — mesmo `APP_TOKEN` |
| **Computer Use** | 27 actions nativas | **Remoto**: Só `ui_*` panels | **Limitação aceita** — remoto é voz+UI, não controle de PC |

---

## 3. O Que Quebra Se Não For Cuidadoso

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| **Porta 8000 conflito** | Dashboard v1 usa 8000, novo remote usa 8000 | **Mesmo processo** — um FastAPI serve ambos endpoints (`/ws/voice` + `/ws/relay` + REST) |
| **Gemini Live API cota** | v1 usa Live API para TTS (sessões) | **Controle unificado** — `GeminiLiveTTS` (local) + `GeminiLiveTransport` (remoto) compartilham pool de sessões |
| **MediaPipe model** | `models/gesture_recognizer.task` (8.4MB) | **Web usa CDN/JS** — não precisa do .task no servidor |
| **WebSocket relay existente** | `dashboard/relay_client.py` + `server.py` | **Manter** — adicionar `/ws/voice` no mesmo `server.py` |
| **Frontend estático** | `dashboard/static/app.html` (PyQt6 relay) | **Novo** — `src/jarvis/transports/remote/frontend/dist/` servido pelo mesmo FastAPI |
| **APP_TOKEN** | Query param `?token=` | **Migrar** — Header `Authorization: Bearer` + cookie HttpOnly (manter compat via query para legacy) |

---

## 4. Estratégia de Integração Honesta

### 4.1 O Que Reutilizar (Zero Reescrita)
```python
# JÁ EXISTE EM src/jarvis/core/ — USAR DIRETO
- Brain Engine (FastBrain, GeminiBrain, OpenRouterBrain)
- Action Registry (27 actions @action)
- MemoryManager
- SessionManager
- Config (Pydantic Settings)
```

### 4.2 O Que Adaptar (Mínima Mudança)
```python
# dashboard/server.py → src/jarvis/transports/remote/server.py
# ADICIONAR endpoints:
- GET  /ws/voice          # WebSocket binário voz
- GET  /ws/relay          # WebSocket relay existente (manter)
- GET  /                  # Servir frontend web (PWA)
- GET  /healthz           # Existente
- POST /api/*             # REST existente
```

### 4.3 O Que Criar Novo (Phase 3)
```
src/jarvis/transports/remote/
├── server.py              # FastAPI + /ws/voice + static files
├── gemini_live.py         # Wrapper client.aio.live.connect
└── frontend/              # Vite + TypeScript
    ├── package.json
    ├── vite.config.ts
    ├── index.html
    ├── src/
    │   ├── main.ts
    │   ├── style.css
    │   ├── audio/
    │   │   ├── worklet.ts      # AudioWorkletProcessor
    │   │   ├── capture.ts      # getUserMedia + WorkletNode
    │   │   └── playback.ts     # AudioContext queue + flush
    │   ├── gestures/
    │   │   ├── mediapipe.ts    # GestureRecognizer + HandLandmarker
    │   │   └── cursor.ts       # Virtual cursor + smoothing
    │   ├── ui/
    │   │   ├── hud.ts          # Orbe, log, painéis
    │   │   └── panels.ts       # Draggable panels
    │   └── websocket.ts        # WS manager + reconnect
```

---

## 5. Plano de Implementação Fase 3 (Ordem de Dependência)

### Semana 3A: Backend Remote Transport
1. `src/jarvis/transports/remote/gemini_live.py` — Wrapper Live API
2. `src/jarvis/transports/remote/server.py` — FastAPI + `/ws/voice` + static mount
3. Integrar no `Container.start_transports()` 
4. Testes: `test_voice_loop_remote.py` (mock Live API)

### Semana 3B: Frontend Base + AudioWorklet
1. `frontend/package.json` + `vite.config.ts` + `tsconfig.json`
2. `audio/worklet.ts` — PCM16 16kHz↔24kHz resampling
3. `audio/capture.ts` + `audio/playback.ts` — getUserMedia + queue + flush
4. `websocket.ts` — WS manager + binary + reconnect + auth
5. `main.ts` + `index.html` + `style.css` (HUD Stark básico)

### Semana 3C: MediaPipe Gestures + UI
1. `gestures/mediapipe.ts` — GestureRecognizer (VIDEO mode) + HandLandmarker
2. `gestures/cursor.ts` — Virtual cursor (smoothing, mirrored x)
3. `ui/hud.ts` — Orbe pulsante, log transcrição, botões
4. `ui/panels.ts` — Draggable panels (mouse + pinch gesture)
5. Mapear gestos: pinch→drag, fist→close, V→mic toggle, thumbs_up→confirm

### Semana 3D: Integração + PWA + Testes
1. `manifest.json` + `sw.js` (Service Worker)
2. Build: `npm run build` → `dist/` servido por FastAPI
3. Testes E2E: Playwright (mic permissions, WS connect, audio roundtrip)
4. Deploy: Docker multi-stage copia `dist/` para imagem Python

---

## 6. Decisões Arquiteturais Críticas (ADRs Fase 3)

| ADR | Decisão | Justificativa |
|-----|---------|---------------|
| **ADR-008** | Um FastAPI serve `/ws/voice` + `/ws/relay` + REST + static | Evita conflito de porta, compartilha brain/memory/session |
| **ADR-009** | Auth: Header `Authorization: Bearer` + cookie HttpOnly (query param legacy) | Segurança + compatibilidade |
| **ADR-010** | AudioWorklet obrigatório (não ScriptProcessor) | Performance, thread isolation, futuro-proof |
| **ADR-011** | MediaPipe no browser (JS) — não Python | Offload CPU/GPU para cliente, funciona em qualquer device |
| **ADR-012** | Gemini Live API para remoto; Whisper local para desktop | Cada transport usa o melhor STT/TTS para seu contexto |
| **ADR-013** | `ui_*` actions encaminhadas via WS para frontend | Único ponto de extensão remota; Computer Use fica local |

---

## 7. Checklist de Não-Quebra (Validação Contínua)

```bash
# Após cada commit Phase 3:
# 1. Tests passam
pytest tests/unit tests/integration -v

# 2. Local transport ainda funciona
python -m jarvis --transport local  # (manual smoke test)

# 3. Dashboard relay ainda responde
curl http://localhost:8000/healthz
curl http://localhost:8000/ws/relay  # (WebSocket upgrade)

# 4. Novo remote voice responde
curl http://localhost:8000/ws/voice  # (WebSocket upgrade)

# 5. Frontend carrega
curl http://localhost:8000/  # HTML
curl http://localhost:8000/assets/index.js  # JS bundle

# 6. MediaPipe carrega (CDN)
# Verificar console browser: "MediaPipe GestureRecognizer loaded"
```

---

## 8. Estimativa Realista

| Item | Esforço | Risco |
|------|---------|-------|
| Backend remote transport | 2 dias | Baixo (reuso brain/memory) |
| AudioWorklet + WS binary | 2 dias | Médio (resampling edge cases) |
| MediaPipe gestures + cursor | 3 dias | Médio (coordenadas, smoothing) |
| UI HUD + painéis draggable | 2 dias | Baixo |
| PWA + Service Worker | 1 dia | Baixo |
| Testes E2E + CI | 1 dia | Médio |
| **Total** | **~11 dias úteis** | |

---

## 9. Conclusão: Caminho Seguro

> **Não quebrar nada = manter core isolado, transports plugáveis, endpoints separados.**

1. **Core intocado** — `src/jarvis/core/` não muda
2. **Local transport intocado** — `src/jarvis/transports/local/` não muda
3. **Dashboard relay mantido** — `/ws/relay` continua funcionando
4. **Novo `/ws/voice` adicionado** — mesmo FastAPI, nova rota
5. **Frontend web novo** — servido como static files, zero conflito com PyQt6
6. **Gestos no browser** — MediaPipe JS, sem dependência do `models/gesture_recognizer.task` Python

**Próximo passo:** Iniciar `src/jarvis/transports/remote/gemini_live.py` (wrapper Live API) — é a base de tudo.

---

*Análise baseada em inspeção real da instalação existente + arquitetura v2 + prompt requirements. 11/10/2026*