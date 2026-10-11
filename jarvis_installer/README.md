# JARVIS Installer & Updater

Sistema de instalação e atualização automática para JARVIS CyberBot v2+.

## Características

- **Instalação Zero-Config**: Detecta SO, Python, Node.js, GPU automaticamente
- **Auto-Update**: Verifica GitHub Releases, baixa deltas, aplica com rollback automático
- **Multi-Plataforma**: Windows (NSSM), Linux (systemd), macOS (launchd)
- **Frontend PWA**: Build automático Vite + TypeScript + MediaPipe
- **Health Checks**: Monitoramento contínuo com auto-recovery
- **Rollback**: Snapshots completos para rollback instantâneo
- **TUI/CLI**: Interface textual rica + CLI completa

## Instalação Rápida

```bash
# Via pipx (recomendado)
pipx install jarvis-installer
jarvis-installer install

# Ou direto do source
git clone https://github.com/prevprocesso-maker/carloscosta-jarvis.git
cd carloscosta-jarvis
pip install -e ./jarvis_installer
jarvis-installer install
```

## Comandos Principais

```bash
# Instalação
jarvis-installer install                    # Interativo
jarvis-installer install --channel beta     # Canal beta
jarvis-installer install --headless         # Servidor (sem GUI)
jarvis-installer install --docker           # Com Docker

# Atualização
jarvis-installer update                     # Verifica + aplica
jarvis-installer update --check-only        # Só verifica
jarvis-installer update --channel nightly   # Muda canal
jarvis-installer update --rollback          # Rollback manual
jarvis-installer update --list-rollbacks    # Lista rollbacks

# Serviço
jarvis-installer service status|start|stop|restart|install|uninstall

# Configuração
jarvis-installer config show
jarvis-installer config set gemini.api_key "sua-chave"
jarvis-installer config migrate

# Status e Diagnóstico
jarvis-installer status
jarvis-installer doctor
jarvis-installer logs -f

# Frontend
jarvis-installer frontend build
jarvis-installer frontend dev

# Benchmark
jarvis-installer benchmark --iterations 20

# Desinstalação
jarvis-installer uninstall --keep-data
```

## Canais de Release

| Canal | Frequência | Estabilidade |
|-------|------------|--------------|
| `stable` | Mensal | Produção |
| `beta` | Semanal | Testes |
| `nightly` | Diária | Experimental |

## Estrutura de Diretórios

```
%APPDATA%/JARVIS/           # Windows
~/.local/share/jarvis/      # Linux
~/Library/Application Support/JARVIS/  # macOS

├── venv/                   # Python virtual environment
├── frontend/
│   ├── dist/               # Build atual (servido)
│   ├── dist.backup/        # Build anterior (rollback)
│   └── package.json
├── config/
│   ├── config.yaml         # Configuração atual
│   ├── config.yaml.bak     # Backup pré-update
│   └── migrations/         # Scripts de migração
├── data/
│   ├── memory/             # MemoryManager JSON
│   ├── whatsapp/           # Baileys auth + media
│   └── logs/               # Logs rotativos
├── bin/                    # Launcher scripts
├── updates/
│   ├── pending/            # Update baixado, não aplicado
│   ├── applied/            # Histórico de updates
│   └── rollback/           # Snapshots para rollback
└── jarvis.log
```

## Configuração (config.yaml)

```yaml
app:
  channel: "stable"  # stable, beta, nightly

gemini:
  api_key: ""  # OBRIGATÓRIO: https://aistudio.google.com/apikey

transports:
  local:
    enabled: true
    whisper_model: "large-v3"
  remote:
    enabled: true
    port: 8000

update:
  channel: "stable"
  auto_check: true
  auto_apply: false
  rollback_on_health_failure: true
```

## Auto-Update em Produção

### Windows (Task Scheduler)
```xml
<Task>
  <Triggers><CalendarTrigger><StartBoundary>2024-10-11T03:00:00</StartBoundary><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Actions><Exec><Command>jarvis-installer.exe</Command><Arguments>update --silent</Arguments></Exec></Actions>
</Task>
```

### Linux (systemd timer)
```ini
# /etc/systemd/system/jarvis-update.timer
[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
RandomizedDelaySec=3600
```

## Desenvolvimento

```bash
# Clone
git clone https://github.com/prevprocesso-maker/carloscosta-jarvis.git
cd carloscosta-jarvis

# Install dev deps
pip install -e ".[dev]"

# Frontend dev
cd jarvis_installer/src/jarvis/transports/remote/frontend
npm install
npm run dev

# Run tests
pytest tests/ -v

# Lint
ruff check .
mypy .
```

## Build Distributável

```bash
# Build installer executável
pip install -e ".[build]"
python -m jarvis_installer.cli build --all-platforms

# Ou via CI/CD (GitHub Actions)
# Trigger: push tag v*
```

## Arquitetura

```
jarvis-installer/
├── core/           # Sistema, paths, version, config, health, security
├── install/        # Python env, Node env, Docker, shortcuts, services
├── update/         # Checker, downloader, applier, rollback, notifier
├── cli/            # Typer CLI + Textual TUI
├── scripts/        # post_install, pre_update, post_update, health_monitor, benchmark
├── templates/      # config.yaml, Dockerfile, docker-compose, service templates
├── scripts/        # Helper scripts
└── pyproject.toml
```

## Segurança

- Assinatura de releases via cosign/keyless
- Checksums SHA256 verificados em todos downloads
- Verificação de assinatura RSA-PSS opcional
- Sandbox: instalação em user-space (sem sudo exceto services)
- Rollback sempre disponível

## Licença

MIT