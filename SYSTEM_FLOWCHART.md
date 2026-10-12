# JARVIS CyberBot - Arquitetura Completa (Flowchart + Dependências)

## 1. Visão Geral - Camadas do Sistema

```mermaid
graph TB
    subgraph USER["👤 Usuário"]
        VOICE[Voz/Microfone]
        TEXT[Texto/Chat/UI]
        WHATSAPP[WhatsApp]
    end

    subgraph TRANSPORTS["🎙️ TRANSPORTES (I/O)"]
        LOCAL[LocalAudioTransport<br/>Desktop PyQt6]
        REMOTE[RemoteVoiceServer<br/>Web PWA + WebSocket]
        WABRIDGE[WhatsApp Bridge<br/>Webhook 8765]
    end

    subgraph ORCHESTRATOR["🎯 ORQUESTRADOR CENTRAL"]
        ORCH[JarvisOrchestrator]
    end

    subgraph BRAINS["🧠 CÉREBROS (BrainFactory)"]
        FAST[FastBrain<br/>gemini-3.5-flash-lite]
        GEMINI[GeminiBrain<br/>gemini-2.5-flash]
        OPENR[OpenRouterBrain<br/>cypher-alpha:free]
    end

    subgraph KNOWLEDGE["📚 KNOWLEDGE BASE (RAG)"]
        KB[KnowledgeBase]
        VECTOR[SQLiteVectorStore<br/>Vetores + FTS5]
        EMBED[Embeddings<br/>MiniLM-L6-v2]
        RERANK[CrossEncoder<br/>bge-reranker-base]
    end

    subgraph VAULT["📁 OBSIDIAN VAULT"]
        VMANAGER[ObsidianVaultManager<br/>CRUD + Watcher]
        VROUTER[VaultRouter<br/>CLAUDE.md routing]
        VCLASS[IntentClassifier<br/>10 regex patterns]
        VCONST[VaultConstitution<br/>CLAUDE.md generator]
        VSETUP[VaultSetup<br/>Estrutura 15 pastas]
    end

    subgraph ACTIONS["⚙️ ACTION REGISTRY"]
        REG[Action Registry<br/>@action decorator]
        OPENSQUAD[OpenSquad Skills<br/>11 actions]
        CORE_ACTIONS[Core Actions<br/>computer, files, etc.]
    end

    subgraph MEMORY["💾 MEMÓRIA & SESSÕES"]
        MGR[MemoryManager]
        SESS[SessionManager]
    end

    subgraph OBSERVABILITY["📊 OBSERVABILIDADE"]
        TELEM[Telemetry<br/>Tracing/Metrics]
        GUARD[Guardrails<br/>Rules/Approval]
        VERSION[Versioning<br/>Migrations/Releases]
    end

    %% Conexões principais
    USER --> TRANSPORTS
    TRANSPORTS --> ORCH
    ORCH --> BRAINS
    ORCH --> KNOWLEDGE
    ORCH --> VAULT
    ORCH --> ACTIONS
    ORCH --> MEMORY
    ORCH --> OBSERVABILITY

    BRAINS --> ACTIONS
    VAULT --> KNOWLEDGE
    KNOWLEDGE --> VECTOR
    KNOWLEDGE --> EMBED
    KNOWLEDGE --> RERANK
```

---

## 2. Fluxo de Dados - Voice Loop (Desktop)

```mermaid
sequenceDiagram
    participant U as Usuário
    participant MIC as Microfone
    participant VAD as VAD (Silero)
    participant STT as Whisper STT
    participant ORCH as Orchestrator
    participant INTENT as IntentClassifier
    participant VROUTER as VaultRouter
    participant VAULT as ObsidianVaultManager
    participant BRAIN as FastBrain
    participant TOOLS as Action Registry
    participant TTS as Gemini Live TTS
    participant SPK as Speaker

    U->>MIC: Fala "JARVIS, reunião amanhã com João"
    MIC->>VAD: Audio stream 16kHz
    VAD->>STT: Segmentos de voz detectados
    STT->>ORCH: Texto transcrito
    ORCH->>INTENT: _detect_intent(texto)
    INTENT-->>ORCH: Intent{type: CREATE_MEETING, params: {who: João, when: tomorrow}}
    
    alt Intent é Action (is_action=true)
        ORCH->>VROUTER: route(intent)
        VROUTER-->>ORCH: {action: create_meeting, folder: clientes/joao/reunioes/, ...}
        ORCH->>VAULT: write_note(path, content, frontmatter)
        VAULT-->>ORCH: success
        ORCH->>U: "Reunião criada no vault"
    else Intent não é Action
        ORCH->>BRAIN: process(text_input)
        BRAIN->>TOOLS: get_openai_schemas()
        BRAIN->>BRAIN: Decide tool ou resposta
        BRAIN-->>ORCH: BrainResponse{text, tool_calls}
        ORCH->>TOOLS: execute_action(name, args)
        TOOLS-->>ORCH: resultado
        ORCH->>TTS: synthesize_stream(texto)
        TTS->>SPK: Audio chunks 24kHz
        SPK->>U: Resposta falada
    end
```

---

## 3. Fluxo de Dados - Web PWA (Remoto)

```mermaid
sequenceDiagram
    participant Browser as Navegador
    participant WS as WebSocket (ws://:8000/ws/voice)
    participant AUDIO as AudioWorklet (16kHz)
    participant MEDIAPIPE as MediaPipe Hands
    participant SERVER as RemoteVoiceServer
    participant BRAIN as BrainFactory
    participant SESSION as SessionManager

    Browser->>WS: Conecta + auth token
    WS->>SERVER: Upgrade HTTP → WS
    Browser->>AUDIO: getUserMedia + AudioWorklet
    AUDIO->>WS: Binary frames (Int16Array 16kHz)
    Browser->>MEDIAPIPE: Webcam → landmarks
    MEDIAPIPE->>WS: Gesture events (JSON)
    
    loop Voice Stream
        WS->>SERVER: Audio chunks + gestures
        SERVER->>BRAIN: process(audio_chunk, context)
        BRAIN-->>SERVER: BrainResponse stream
        SERVER->>WS: Audio (base64) + Text + Tool calls
        WS->>Browser: Play audio + Update UI
    end
```

---

## 4. Dependências - BrainFactory (Cadeia de Fallback)

```mermaid
graph TD
    BF[BrainFactory<br/>src/jarvis/core/brain/__init__.py]
    
    BF -->|get_fast_brain()| FAST[FastBrain<br/>src/jarvis/core/brain/fast.py]
    BF -->|get_gemini_brain()| GEMINI[GeminiBrain<br/>src/jarvis/core/brain/gemini.py]
    BF -->|get_openrouter_brain()| OPENR[OpenRouterBrain<br/>src/jarvis/core/brain/openrouter.py]
    
    FAST -->|usa| GEMINI_API[Gemini API Key]
    FAST -->|usa| ACTION_REG[Action Registry]
    FAST -->|usa| MEMORY[MemoryManager]
    
    GEMINI -->|usa| GEMINI_API
    GEMINI -->|usa| ACTION_REG
    GEMINI -->|function calling nativo| GEMINI_TOOLS[Gemini Function Calling]
    
    OPENR -->|usa| OPENROUTER_KEY[OpenRouter API Key]
    OPENR -->|usa| ACTION_REG
    OPENR -->|HTTP| OPENROUTER_API[openrouter.ai/api/v1]
    
    %% Fallback chain
    FAST -.->|falha: 429, 503, 404, timeout| GEMINI
    GEMINI -.->|falha| OPENR
    
    classDef primary fill:#e3f2fd,stroke:#1976d2,stroke-width:2px;
    classDef fallback fill:#fff3e0,stroke:#f57c00,stroke-width:2px;
    classDef external fill:#fce4ec,stroke:#c2185b,stroke-width:2px;
    
    class FAST primary;
    class GEMINI fallback;
    class OPENR fallback;
    class GEMINI_API,OPENROUTER_KEY external;
```

---

## 5. Vault → Knowledge Base Sync (One-Way)

```mermaid
graph LR
    subgraph VAULT["Obsidian Vault (Source of Truth)"]
        MD[(.md files<br/>15 pastas)]
        WATCHER[Watchdog Watcher<br/>FileSystemEventHandler]
        REST[Local REST API<br/>Porta 27124]
    end
    
    subgraph SYNC["Sync Pipeline"]
        EVENT[VaultFileEvent<br/>CREATED/MODIFIED/DELETED]
        QUEUE[asyncio.Queue<br/>Debounce 500ms]
        PROCESS[_sync_event_to_kb()]
    end
    
    subgraph KB["Knowledge Base (Search Engine)"]
        CHUNKER[Chunker<br/>Recursive 512/50]
        EMBED_PROV[EmbeddingProvider<br/>MiniLM-L6-v2]
        VECTOR_DB[(SQLiteVectorStore<br/>vectors.db)]
        FTS5[FTS5 BM25 Index<br/>chunks_fts]
    end
    
    MD -->|watchdog| WATCHER
    WATCHER -->|VaultFileEvent| EVENT
    REST -->|CRUD| MD
    EVENT --> QUEUE
    QUEUE --> PROCESS
    PROCESS -->|add_file()| KB
    KB --> CHUNKER
    CHUNKER --> EMBED_PROV
    EMBED_PROV --> VECTOR_DB
    CHUNKER --> FTS5
    
    %% Search flow
    QUERY[Query] -->|hybrid_search()| VECTOR_DB
    QUERY -->|keyword search| FTS5
    VECTOR_DB -->|vector results| RRF[RRF Fusion k=60]
    FTS5 -->|bm25 results| RRF
    RRF -->|top-k| RERANK[CrossEncoder Reranker]
    RERANK -->|final results| USER
```

---

## 6. Action Registry - Arquitetura de Tools

```mermaid
graph TB
    subgraph DECORATOR["@action Decorator"]
        DEC[@action<br/>namespace, description]
        SIG[inspect.signature]
        HINTS[get_type_hints]
        PYDANTIC[create_model<br/>ArgsModel]
        SCHEMA[model_json_schema]
    end
    
    subgraph REGISTRY["_action_registry dict"]
        CORE[Core Actions<br/>namespace: computer, files, etc.]
        OPENSQ[OpenSquad Skills<br/>namespace: opensquad]
    end
    
    subgraph EXECUTION["execute_action()"]
        LOOKUP[_action_registry.get(name)]
        INJECT[Inject session/context]
        CALL[func(**kwargs)]
    end
    
    subgraph BRAIN_INTEGRATION["Brain Integration"]
        GET_SCHEMAS[get_openai_schemas()<br/>→ OpenAI function calling format]
        FAST_BRAIN[FastBrain<br/>_regras() + _limpar()]
        GEMINI_BRAIN[GeminiBrain<br/>_desembrulhar() + function_declarations]
        OPENR_BRAIN[OpenRouterBrain<br/>tools array]
    end
    
    DEC --> SIG
    SIG --> HINTS
    HINTS --> PYDANTIC
    PYDANTIC --> SCHEMA
    SCHEMA --> REGISTRY
    
    REGISTRY --> CORE
    REGISTRY --> OPENSQ
    
    REGISTRY --> EXECUTION
    LOOKUP --> INJECT
    INJECT --> CALL
    
    REGISTRY --> GET_SCHEMAS
    GET_SCHEMAS --> FAST_BRAIN
    GET_SCHEMAS --> GEMINI_BRAIN
    GET_SCHEMAS --> OPENR_BRAIN
```

---

## 7. Inicialização - Ordem de Boot (Orchestrator)

```mermaid
sequenceDiagram
    participant MAIN as __main__.py
    participant BOOT as bootstrap.py
    participant ORCH as JarvisOrchestrator
    participant SETTINGS as Settings
    participant TELEM as Telemetry
    participant GUARD as Guardrails
    participant KB as KnowledgeBase
    participant VAULT as Vault
    participant OPENSQ as OpenSquad
    participant TRANSPORTS as Transports
    participant UI as PyQt6 UI

    MAIN->>BOOT: initialize_bootstrap()
    BOOT->>SETTINGS: ensure_config_files()
    MAIN->>ORCH: create_application()
    ORCH->>SETTINGS: get_settings()
    ORCH->>TELEM: get_telemetry()
    ORCH->>GUARD: get_guardrails()
    ORCH->>KB: get_knowledge_base()
    ORCH->>MEMORY: get_memory_manager()
    
    par Inicialização Paralela
        ORCH->>ORCH: initialize_brains() → BrainFactory
        ORCH->>VAULT: _init_vault()
        VAULT->>VAULT: create_vault_manager()
        VAULT->>VAULT: start_watcher()
        VAULT->>VROUTER: VaultRouter(vault_path)
        VAULT->>VCLASS: IntentClassifier()
        VAULT->>OPENSQ: init_opensquad_skills()
        OPENSQ->>REGISTRY: register_opensquad_skills()
    end
    
    ORCH->>TRANSPORTS: start_transports()
    TRANSPORTS->>LOCAL: LocalAudioTransport(FastBrain)
    TRANSPORTS->>REMOTE: create_remote_voice_server()
    TRANSPORTS->>WABRIDGE: start_whatsapp_bridge()
    
    ORCH->>UI: _init_ui() → JarvisUI
    ORCH->>UI: _wire_ui_callbacks()
    ORCH->>ORCH: run() → _voice_loop() + _poll_tasks + UI.exec()
```

---

## 8. Mapa de Arquivos → Módulos → Responsabilidades

```mermaid
graph TB
    subgraph ENTRY["Entry Points"]
        MAIN[src/jarvis/__main__.py]
        BOOT[src/jarvis/bootstrap.py]
    end
    
    subgraph CORE["src/jarvis/core/"]
        CONFIG[config.py<br/>Settings, migrations, vault config]
        PATHS[paths.py<br/>Dirs por SO]
        TELEM[telemetry.py<br/>Tracing, metrics, logging]
        GUARD[guardrails.py<br/>Rules, approval, rate limit]
        KNOWLEDGE[knowledge.py<br/>RAG: Vector+BM25+RRF+Rerank]
        SESSION[session.py<br/>SessionManager]
        MEMORY[memory/manager.py<br/>MemoryManager]
        ACTIONS[actions/registry.py<br/>@action, schemas, execution]
        BRAIN_BASE[brain/base.py<br/>Brain ABC, BrainFactory]
        BRAIN_FAST[brain/fast.py<br/>FastBrain - principal]
        BRAIN_GEM[brain/gemini.py<br/>GeminiBrain - reserva]
        BRAIN_OR[brain/openrouter.py<br/>OpenRouterBrain - fallback]
        VERSION[version.py<br/>Semver, migrations]
    end
    
    subgraph TRANSPORTS["src/jarvis/transports/"]
        LOCAL[local/<br/>audio.py, stt.py, tts.py, vad.py]
        REMOTE[remote/<br/>server.py, frontend/]
        WEBHOOK[webhook/<br/>whatsapp bridge]
    end
    
    subgraph INTEGRATIONS["src/jarvis/integrations/"]
        VAULT_OBS[obsidian_vault.py<br/>CRUD + Watcher + REST]
        VAULT_ROUTER[vault_router.py<br/>IntentClassifier + VaultRouter]
        VAULT_CONST[vault_constitution.py<br/>CLAUDE.md generator]
        VAULT_SETUP[vault_setup.py<br/>Estrutura pastas]
        OPENSQ[opensquad_bridge.py<br/>SKILL.md parser + executor]
        WHATSAPP[whatsapp.py<br/>Bridge client/manager]
    end
    
    subgraph UI["src/jarvis/ui/"]
        JARVIS_UI[__init__.py<br/>JarvisUI PyQt6]
    end
    
    MAIN --> BOOT
    BOOT --> CONFIG
    MAIN --> ORCH[orchestrator.py]
    ORCH --> CONFIG
    ORCH --> TELEM
    ORCH --> GUARD
    ORCH --> KNOWLEDGE
    ORCH --> SESSION
    ORCH --> MEMORY
    ORCH --> BRAIN_BASE
    ORCH --> TRANSPORTS
    ORCH --> VAULT_OBS
    ORCH --> VAULT_ROUTER
    ORCH --> OPENSQ
    ORCH --> ACTIONS
    ORCH --> UI
    
    BRAIN_BASE --> BRAIN_FAST
    BRAIN_BASE --> BRAIN_GEM
    BRAIN_BASE --> BRAIN_OR
    BRAIN_FAST --> ACTIONS
    BRAIN_GEM --> ACTIONS
    BRAIN_OR --> ACTIONS
    
    VAULT_OBS --> KNOWLEDGE
    VAULT_ROUTER --> VAULT_CONST
    OPENSQ --> ACTIONS
```

---

## 9. Configuração Centralizada (config.yaml)

```mermaid
graph TD
    CONFIG[config.yaml<br/>%APPDATA%/JARVIS/config/config.yaml]
    
    CONFIG --> GEMINI[gemini:<br/>api_key, models, voice]
    CONFIG --> TRANSPORTS_CFG[transports:<br/>local, remote, webhook]
    CONFIG --> UI_CFG[ui:<br/>desktop_enabled, theme]
    CONFIG --> VAULT_CFG[vault:<br/>path, api_key, host, port, verify_ssl]
    CONFIG --> KNOWLEDGE_CFG[knowledge:<br/>embedding_model, chunk_size, hybrid_search, rerank]
    CONFIG --> UPDATE_CFG[update:<br/>channel, auto_check, rollback]
    CONFIG --> LOGGING_CFG[logging:<br/>level, rotation, format]
    
    GEMINI --> FAST_BRAIN
    GEMINI --> GEMINI_BRAIN
    GEMINI --> TTS_LOCAL
    
    TRANSPORTS_CFG --> LOCAL_TRANSPORT
    TRANSPORTS_CFG --> REMOTE_TRANSPORT
    TRANSPORTS_CFG --> WHATSAPP_BRIDGE
    
    VAULT_CFG --> OBSIDIAN_MANAGER
    VAULT_CFG --> VAULT_ROUTER
    VAULT_CFG --> REST_API_CLIENT
    
    KNOWLEDGE_CFG --> EMBEDDING_PROVIDER
    KNOWLEDGE_CFG --> CHUNKER
    KNOWLEDGE_CFG --> HYBRID_SEARCH
    KNOWLEDGE_CFG --> RERANKER
```

---

## 10. Resumo de Dependências Externas (requirements)

```mermaid
graph LR
    subgraph PYTHON["Python Core"]
        FASTAPI[fastapi + uvicorn]
        PYDANTIC[pydantic + pydantic-settings]
        SQLITE[sqlite3 + aiosqlite]
        WATCHDOG[watchdog]
        AIOHTTP[aiohttp]
        YAML[pyyaml]
    end
    
    subgraph AI_ML["AI/ML"]
        GENAI[google-genai<br/>Gemini SDK]
        WHISPER[faster-whisper<br/>ctranslate2]
        SENTENCE[sentence-transformers<br/>torch]
        NUMPY[numpy]
        SOUNDDEVICE[sounddevice<br/>PortAudio]
    end
    
    subgraph FRONTEND["Frontend (Node.js)"]
        VITE[vite + typescript]
        REACT[react + react-dom]
        PHASER[phaser<br/>Dashboard 3D]
        TAILWIND[tailwindcss]
    end
    
    subgraph EXTERNAL_APIS["APIs Externas"]
        GEMINI_API[Gemini API<br/>google.genai]
        OPENROUTER[OpenRouter API<br/>httpx]
        WHATSAPP_API[WhatsApp Web<br/>whatsapp-web.js]
        OBSIDIAN_REST[Obsidian Local REST API<br/>Plugin comunitário]
        OPENROUTER_IMG[OpenRouter Images<br/>para image-ai-generator]
    end
    
    PYTHON --> FASTAPI
    AI_ML --> GENAI
    AI_ML --> WHISPER
    AI_ML --> SENTENCE
    FRONTEND --> VITE
    EXTERNAL_APIS --> GEMINI_API
    EXTERNAL_APIS --> OPENROUTER
    EXTERNAL_APIS --> OBSIDIAN_REST
```

---

## 11. Como Testar Cada Componente

```bash
# 1. Testar imports básicos
python -c "from jarvis.integrations import *; print('Imports OK')"

# 2. Testar Vault Setup
python -c "
from jarvis.integrations import VaultSetup, VaultConstitutionGenerator, VaultBriefing
from pathlib import Path
vault = Path(r'C:\Users\NIKE\Downloads\Jarvis-obsidian-teste')
setup = VaultSetup(vault)
result = setup.validate_structure()
print('Vault válido:', result.valid)
briefing = VaultBriefing(nome='Carlos Costa', cidade='Rio de Janeiro', 
    o_que_vende='Direito previdenciário', icp_em_uma_frase='Pessoas com dores do INSS')
gen = VaultConstitutionGenerator(vault_path=vault)
gen.install(vault, briefing)
print('CLAUDE.md gerado')
"

# 3. Testar Intent Classification + Routing
python -c "
from jarvis.integrations import IntentClassifier, VaultRouter
from pathlib import Path
vault = Path(r'C:\Users\NIKE\Downloads\Jarvis-obsidian-teste')
clf = IntentClassifier()
intent = clf.classify_intent('reunião amanhã com João da Acme às 15h')
print('Intent:', intent.type, intent.params)
router = VaultRouter(vault)
route = router.route(intent)
print('Route:', route)
"

# 4. Testar OpenSquad Skills
python -c "
from jarvis.integrations import init_opensquad_skills, get_opensquad_action_schemas
from jarvis.core.actions.registry import register_opensquad_skills, get_openai_schemas
skills = init_opensquad_skills()
schemas = get_opensquad_action_schemas()
print('Skills:', len(skills))
registered = register_opensquad_skills(schemas)
print('Registradas:', registered)
all_schemas = get_openai_schemas()
opensquad = [s for s in all_schemas if 'opensquad' in s['function']['name']]
print('No registry:', len(opensquad))
"

# 5. Testar Knowledge Base
python -c "
import asyncio
from jarvis.core.knowledge import get_knowledge_base
kb = get_knowledge_base()
async def test():
    await kb.initialize()
    doc = await kb.add_document('Teste', 'Conteúdo de teste para RAG', tags=['teste'])
    results = await kb.search('teste', limit=5)
    print('Docs:', len(results))
    for r in results:
        print('  -', r.document.title, 'score:', r.score)
asyncio.run(test())
"
```