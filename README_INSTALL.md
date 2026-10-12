# JARVIS CyberBot - Guia de Instalação e Atualização

> **Versão**: 2.0.0-dev | **Branch**: `phase5/desktop-integration` | **Última atualização**: 2026-10-11

---

## 📋 Pré-requisitos

| Componente | Versão Mínima | Observação |
|------------|---------------|------------|
| **Python** | 3.11+ | Recomendado 3.11 ou 3.12 |
| **Git** | 2.30+ | Para clonar e atualizar |
| **Node.js** | 18+ | Opcional - para build do frontend PWA |
| **FFmpeg** | 5.0+ | Necessário para áudio (WhatsApp/Voice) |
| **Windows** | 10/11 | Testado no Windows 10/11 |

> ⚠️ **Python 3.14** (atual) pode ter problemas com `torch`. Recomendado **Python 3.11 ou 3.12**.

---

## 🚀 Instalação Rápida (Usuário Final)

### Opção 1: Script PowerShell (Recomendado Windows)

```powershell
# 1. Baixe o instalador
git clone https://github.com/prevprocesso-maker/carloscosta-jarvis.git
cd carloscosta-jarvis

# 2. Execute como Administrador (para instalar serviço Windows)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install_jarvis_system.ps1

# Ou sem Admin (instala em %APPDATA%\JARVIS)
.\install_jarvis.ps1
```

**O que faz:**
- ✅ Clona/atualiza branch `phase5/desktop-integration`
- ✅ Cria venv Python em `%APPDATA%\JARVIS\venv` (ou `C:\Program Files\JARVIS\venv`)
- ✅ Instala dependências Python (`pip install -e .`)
- ✅ Baixa modelo Whisper (primeira vez ~1.5GB)
- ✅ Builda frontend PWA (se Node.js disponível)
- ✅ Cria atalhos: **Área de Trabalho** + **Menu Iniciar**
- ✅ Cria serviço Windows (auto-start) - *apenas script Admin*
- ✅ Agenda auto-update diário (3h)

---

### Opção 2: Instalação Manual (Desenvolvedor)

```bash
# 1. Clone
git clone -b phase5/desktop-integration https://github.com/prevprocesso-maker/carloscosta-jarvis.git
cd carloscosta-jarvis

# 2. Crie venv
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 3. Instale dependências
pip install --upgrade pip
pip install -e . --no-cache-dir

# 4. (Opcional) Frontend PWA
cd src/jarvis/transports/remote/frontend
npm ci && npm run build
cd ../../../../..

# 5. Configure API Key
# Edite %APPDATA%\JARVIS\config\config.yaml ou config/config.yaml
# gemini:
#   api_key: "SUA_CHAVE_GEMINI_AQUI"

# 6. Rode
python -m src.jarvis
```

---

## 🔑 Configuração da API Key (Obrigatório)

### Opção A: Via UI (Primeira execução)
1. Rode `python -m src.jarvis`
2. Clique na **engrenagem ⚙️** → aba **API Keys**
3. Cole sua **Gemini API Key** (pegue em https://aistudio.google.com/apikey)
4. Salve → app reinicia automaticamente

### Opção B: Arquivo de Configuração
```yaml
# %APPDATA%\JARVIS\config\config.yaml (Windows)
# ou config/config.yaml (dev)
gemini:
  api_key: "SUA_CHAVE_AQUI"
transports:
  local:
    enabled: true
  remote:
    enabled: true
    port: 8000
  webhook:
    enabled: true
    whatsapp_port: 8765
```

---

## 🔄 Atualização

### Automática (Recomendada)
O agendador roda **diariamente às 3h** (configurável em `update.check_interval_hours`).

### Manual
```powershell
# Opção 1: Script
.\install_jarvis.ps1  # ou install_jarvis_system.ps1

# Opção 2: Git + Pip
git pull origin phase5/desktop-integration
venv\Scripts\pip install -e . --no-cache-dir

# Se houver mudanças no frontend:
cd src/jarvis/transports/remote/frontend
npm ci && npm run build
```

### Verificar Versão
```bash
python -c "from jarvis import __version__; print(__version__)"
# ou
jarvis-installer --version
```

---

## 🛠️ Desenvolvimento

### Estrutura do Projeto
```
carloscosta-jarvis/
├── src/jarvis/
│   ├── __main__.py           # Entry point
│   ├── bootstrap.py          # Inicialização, config, factory
│   ├── orchestrator.py       # Core: brains, transports, UI, WhatsApp
│   ├── voice_loop.py         # STT → Brain → TTS loop
│   ├── bootstrap.py          # Bootstrap & config
│   ├── core/
│   │   ├── brain/            # Brains: Fast, Gemini, OpenRouter
│   │   ├── knowledge.py      # RAG: Vector + FTS5 + Reranker
│   │   ├── config.py         # Config YAML + migrations
│   │   ├── telemetry.py      # Logging, metrics, tracing
│   │   ├── guardrails.py     # Regras + approval workflow
│   │   ├── memory.py         # Episodic + Semantic memory
│   │   ├── actions/          # @action registry
│   │   └── ...
│   ├── transports/
│   │   ├── local/audio.py    # Whisper + VAD + Gemini TTS
│   │   └── remote/           # FastAPI + WebSocket + PWA
│   ├── ui/desktop/ui.py      # PyQt6 UI
│   └── integrations/whatsapp.py
├── jarvis_installer/         # Auto-installer + updater
├── config/config.yaml        # Configuração local
├── install_jarvis.ps1        # Instalador usuário
├── install_jarvis_system.ps1 # Instalador admin (serviço)
└── .github/workflows/        # CI/CD
```

### Rodar Testes
```bash
# Testes rápidos
python -m pytest tests/ -v --tb=short

# Teste específico KB
python test_hybrid_search.py

# Teste RAG completo
python -c "
import asyncio, sys; sys.path.insert(0, 'src')
from jarvis.core.knowledge import get_knowledge_base
async def t():
    kb = get_knowledge_base()
    await kb.initialize()
    r = await kb.hybrid_search('complexidade', limit=3, rerank=True)
    for x in r: print(x.document.title, x.score)
asyncio.run(t())
"
```

---

## 🔧 Configuração Avançada

### Variáveis de Ambiente
```bash
# Python/Threading
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:64
TOKENIZERS_PARALLELISM=false

# Hugging Face (para downloads mais rápidos)
HF_TOKEN=seu_token_hf

# Google Gemini
GEMINI_API_KEY=sua_chave

# Hugging Face Hub
HF_HUB_DISABLE_TELEMETRY=1
HF_HUB_DISABLE_PROGRESS_BARS=1
```

### Modelos Suportados (Knowledge Base)

| Modelo | Dimensão | Idiomas | Uso Recomendado |
|--------|----------|---------|-----------------|
| `all-MiniLM-L6-v2` | 384 | 50+ | **Padrão - Rápido e bom** ✅ |
| `paraphrase-multilingual-MiniLM-L12-v2` | 384 | 50+ | Melhor qualidade |
| `BAAI/bge-m3` | 1024 | 100+ | Estado da arte |
| `intfloat/multilingual-e5-large` | 1024 | 100+ | Melhor retrieval |
| `neuralmind/bert-base-portuguese-cased` | 768 | PT-BR | Especializado PT |

```yaml
# config.yaml
knowledge:
  embedding_model: "sentence-transformers/all-MiniLM-L6-v2"
  embedding_device: "auto"  # cuda/cpu/auto
  chunk_size: 512
  chunk_overlap: 50
  search_threshold: 0.25
  hybrid_search: true
  rerank: false
```

---

## 🐛 Troubleshooting

| Problema | Solução |
|----------|---------|
| `ModuleNotFoundError: src` | Rode da raiz do projeto: `python -m src.jarvis` |
| `SSL CA bundle error (PostgreSQL)` | `pip config set global.trusted-host "pypi.org files.pythonhosted.org"` |
| `OpenBLAS Memory allocation failed` | `set OMP_NUM_THREADS=1 && set MKL_NUM_THREADS=1` |
| `torch.dll not found` | Reinstale: `pip install torch --index-url https://download.pytorch.org/whl/cpu` |
| Whisper baixa modelo toda vez | Modelo salvo em `%APPDATA%\JARVIS\models\whisper-*` |
| Frontend não carrega | `cd src/jarvis/transports/remote/frontend && npm ci && npm run build` |
| Serviço não inicia | Verifique logs em `C:\Program Files\JARVIS\logs\jarvis-service*.log` |

---

## 📦 Desinstalação

```powershell
# Via Menu Iniciar
Menu Iniciar → JARVIS CyberBot → Desinstalar JARVIS

# Ou manual (Admin)
powershell -ExecutionPolicy Bypass -File uninstall.ps1
```

---

## 📚 Referências

- **Repo**: https://github.com/prevprocesso-maker/carloscosta-jarvis
- **Branch principal**: `phase5/desktop-integration`
- **Issues**: https://github.com/prevprocesso-maker/carloscosta-jarvis/issues
- **Gemini API**: https://aistudio.google.com/apikey
- **Hugging Face Models**: https://huggingface.co/models

---

## 📝 Licença

MIT License - Veja [LICENSE](LICENSE) para detalhes.

---

> **Última verificação**: 2026-10-11 | **Testado em**: Windows 11, Python 3.11, Node 18