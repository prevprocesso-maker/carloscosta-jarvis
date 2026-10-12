# Changelog - JARVIS CyberBot

Todas as mudanças notáveis neste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/),
e este projeto adere ao [Semantic Versioning](https://semver.org/lang/pt-BR/).

---

## [2.0.0-dev] - 2026-10-11 - **BREAKING CHANGES**

### ⚠️ Breaking Changes (Migração Obrigatória)

| Área | Antes | Agora | Ação Necessária |
|------|-------|-------|-----------------|
| **Entry Point** | `python main.py` | `python -m src.jarvis` | Atualize scripts de inicialização |
| **Imports UI** | `from ui import JarvisUI` | `from src.jarvis.ui import JarvisUI` | Atualize imports |
| **Config Model** | `paraphrase-multilingual-MiniLM-L12-v2` | `all-MiniLM-L6-v2` | Reimporte brainpacks |
| **Orchestrator API** | Métodos diretos | `bootstrap.py` + `orchestrator.py` | Use `create_application()` |
| **main.py** | Arquivo principal (12k linhas) | **REMOVIDO** | Use `src/jarvis/__main__.py` |

### 📦 Adicionado

#### Knowledge Base RAG (Phase 4)
- **SentenceTransformerEmbeddingProvider** - Suporte a modelos `sentence-transformers` (BGE, MiniLM, E5, etc.)
- **SQLiteVectorStore + FTS5** - Busca vetorial + BM25 nativo com tokenizer `unicode61` (suporta PT-BR/acentos)
- **Hybrid Search (RRF)** - Reciprocal Rank Fusion: Vector + Keyword com peso configurável
- **CrossEncoderReranker** - `BAAI/bge-reranker-base` (lazy load, primeiro uso baixa ~500MB)
- **Agentic Pipeline** - Query → Hybrid (RRF) → Rerank → Top-K
- **Chunking Recursivo** - Separadores hierárquicos (`\n\n`, `\n`, `. `, `! `, `? `, `; `, `, `, ` `)
- **CLI Knowledge** - `jarvis-installer knowledge add/search/ask/list/stats`

#### Desktop Integration (Phase 5)
- **Modular Architecture** - `bootstrap.py`, `orchestrator.py`, `voice_loop.py` separados
- **PyQt6 UI Integration** - `src/jarvis/ui/desktop/ui.py` (258k linhas) integrado ao orchestrator
- **WhatsApp Bridge** - Cliente Python para bridge Node.js (Baileys) com WebSocket
- **Transport Abstraction** - `Transport` ABC + `TransportFactory` (Local/Remote/Webhook)
- **Callbacks Unificados** - `on_audio_out`, `on_text_out`, `on_tool_call` unificados

#### Installer & Auto-Updater
- **install_jarvis.ps1** - Instalador usuário (`%APPDATA%\JARVIS`)
- **install_jarvis_system.ps1** - Instalador Admin (serviço Windows, `C:\Program Files\JARVIS`)
- **NSSM Service** - Auto-start, log rotation, restart on failure
- **Auto-Update** - Scheduled Task diário (3h), canal configurável
- **Inno Setup** - `jarvis_installer/scripts/jarvis.iss` (assinável, silencioso)
- **Build Assinado** - `build_signed_installer.ps1` (PFX ou Azure Key Vault)

#### Configuração
- **knowledge section** no `config.yaml` com todos parâmetros RAG
- **embedding_model default**: `all-MiniLM-L6-v2` (testado, 384 dims, multilingue)
- **search_threshold default**: 0.25 (era 0.7)
- **hybrid_search**: true por padrão
- **rerank**: false por padrão (opt-in)

### 🔧 Melhorado

- **MockEmbeddingProvider** → Determinístico (MD5 hash) para testes reprodutíveis
- **Chunking Recursivo** → Separa semântica preservada
- **FTS5 Tokenizer** → `unicode61` (suporta acentos/PT-BR nativo)
- **Hybrid Search** → RRF (k=60) + Cross-Encoder opcional
- **Auto-Update** → Scheduled Task + health check + rollback automático
- **Logs** → Estruturados (JSON), rotação diária, retenção 7 dias

### 🗑️ Removido

- `main.py` (12.129 linhas monolíticas)
- `orchestrator_new.py` (duplicata)
- `ui.py` raiz (movido para `src/jarvis/ui/desktop/ui.py`)
- 43 arquivos de debug/teste (`test_*.py`, `fix_*.py`, `check_*.py`, `search_*.py`, `diff_*.txt`)
- `orchestrator_new.py` (duplicata)
- `ui.py` raiz (movido para `src/jarvis/ui/desktop/ui.py`)

### 🔧 Corrigido

- **Config/Model Mismatch** - Config e código agora usam `all-MiniLM-L6-v2`
- **FTS5 Tokenizer** - `unicode61` suporta acentos/PT-BR (antes falhava em "programação")
- **MockEmbeddingProvider** - Determinístico (hash MD5) para testes
- **Duplicate _cosine_similarity** - Removida duplicata
- **Orphaned lines** - Removidas linhas órfãs no `_recursive_split`
- **Indentation Errors** - Arquivo reescrito com LF + UTF-8

---

## [1.0.0] - 2026-09-08

### Adicionado
- Core Foundation (Phase 1): Brain Protocol, Action Registry, MemoryManager, SessionManager, DI Container
- Transport Local (Phase 2): Whisper STT + VAD + Gemini Live TTS
- Transport Remoto + Web PWA (Phase 3): FastAPI + WebSocket + Gemini Live + PWA Vite/TS
- Observability + Guardrails + Knowledge + Versioning (Phase 4)
- Auto-Installer & Auto-Updater completo

---

## 📋 Guia de Migração v1.x → v2.0

### 1. Atualize Scripts de Inicialização
```bash
# Antes
python main.py

# Agora
python -m src.jarvis
```

### 2. Atualize Imports
```python
# Antes
from ui import JarvisUI
from core.knowledge import get_knowledge_base

# Agora
from src.jarvis.ui import JarvisUI
from src.jarvis.core.knowledge import get_knowledge_base
```

### 3. Reimporte Brainpacks (Embedding Model Mudou)
```bash
# Limpe base antiga
rm -rf %APPDATA%\JARVIS\data\knowledge\vectors.db

# Reimporte
python -c "
import asyncio, sys; sys.path.insert(0, 'src')
from jarvis.core.knowledge import get_knowledge_base
import zipfile, tempfile
from pathlib import Path

async def main():
    kb = get_knowledge_base()
    await kb.initialize()
    bp_dir = Path(r'C:\Users\NIKE\Downloads\Brainpacks')
    for bp in bp_dir.glob('*.brainpack'):
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(bp, 'r') as z:
                z.extractall(tmp)
            docs_dir = Path(tmp) / 'documents'
            if docs_dir.exists():
                for md in docs_dir.glob('*.md'):
                    await kb.add_file(md, metadata={'source': f'brainpack:{bp.stem}'})
asyncio.run(main())
"
```

### 4. Atualize Config.yaml
```yaml
knowledge:
  embedding_model: "sentence-transformers/all-MiniLM-L6-v2"
  search_threshold: 0.25
  hybrid_search: true
  rerank: false
```

---

## 🔮 Próximas Versões

### [2.1.0] - Planejado
- Query Rewriting (expansão semântica PT-BR)
- Multi-hop Reasoning (decomposição de queries complexas)
- Citation Tracking (rastreamento de fontes nas respostas)
- Cache de Embeddings (persistência + invalidação inteligente)

### [2.2.0] - Planejado
- Multi-hop Reasoning (decomposição em sub-queries)
- Self-Reflection Loop (verificação de respostas)
- Streaming RAG (respostas incrementais)
- Métricas de Qualidade RAG (Precision@K, MRR, NDCG)

---

**Mantido por**: Carlos Costa | **Repo**: https://github.com/prevprocesso-maker/carloscosta-jarvis