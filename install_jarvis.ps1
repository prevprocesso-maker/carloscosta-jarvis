<# 
.SYNOPSIS
    JARVIS CyberBot - Instalador Windows (PowerShell único)
.DESCRIPTION
    Baixa, configura e instala o JARVIS completo: Python venv, dependências,
    frontend, atalhos no Menu Iniciar e Área de Trabalho.
.NOTES
    Requisitos: Windows 10/11, PowerShell 5.1+, Internet
    Rode como Administrador se quiser instalar serviço do sistema.
#>

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ──────────────────────────────────────────────────────────────────────────────
$RepoUrl      = "https://github.com/prevprocesso-maker/carloscosta-jarvis.git"
$Branch       = "phase5/desktop-integration"
$InstallDir   = "$env:APPDATA\JARVIS"
$PythonMinVer = "3.11"
$NodeMinVer   = "18"

Write-Host "`n╔══════════════════════════════════════════════════════════════╗" -Fore Cyan
Write-Host "║     JARVIS CyberBot — Instalador Windows v2.0.0             ║" -Fore Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝`n" -Fore Cyan

# ──────────────────────────────────────────────────────────────────────────────
# FUNÇÕES AUXILIARES
# ──────────────────────────────────────────────────────────────────────────────
function Log   { param($msg) Write-Host "[$(Get-Date -Format HH:mm:ss)] $msg" -Fore Green }
function Warn  { param($msg) Write-Host "[$(Get-Date -Format HH:mm:ss)] ⚠ $msg" -Fore Yellow }
function Err   { param($msg) Write-Host "[$(Get-Date -Format HH:mm:ss)] ✗ $msg" -Fore Red; exit 1 }
function Ask   { param($q, $default) $r = Read-Host "`n$q [$default]"; if (-not $r) { $default } else { $r } }

# ──────────────────────────────────────────────────────────────────────────────
# 1. VERIFICA PYTHON
# ──────────────────────────────────────────────────────────────────────────────
Log "Verificando Python $PythonMinVer+..."
$py = (Get-Command python -ErrorAction SilentlyContinue) ?? (Get-Command py -ErrorAction SilentlyContinue)
if (-not $py) { Err "Python não encontrado. Instale em python.org/downloads (marque 'Add to PATH')." }
$ver = & $py --version 2>&1 | Select-String "(\d+\.\d+)" | ForEach { $_.Matches[0].Value }
if ([version]$ver -lt [version]$PythonMinVer) { Err "Python $ver < $PythonMinVer. Atualize." }
Log "Python $ver OK — $($py.Source)"

# ──────────────────────────────────────────────────────────────────────────────
# 2. VERIFICA GIT
# ──────────────────────────────────────────────────────────────────────────────
Log "Verificando Git..."
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) { Warn "Git não no PATH. Tentando winget..."; winget install -e --id Git.Git -s winget -h; $git = Get-Command git -ErrorAction SilentlyContinue }
if (-not $git) { Err "Git necessário. Instale em git-scm.com" }
Log "Git OK — $($git.Source)"

# ──────────────────────────────────────────────────────────────────────────────
# 3. CLONE / ATUALIZA REPO
# ──────────────────────────────────────────────────────────────────────────────
Log "Preparando pasta: $InstallDir"
if (Test-Path $InstallDir) {
    Log "Pasta existe — atualizando..."
    Set-Location $InstallDir
    git fetch --all
    git reset --hard origin/$Branch
    git clean -fd
} else {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Log "Clonando branch $Branch..."
    git clone --branch $Branch --depth 1 $RepoUrl $InstallDir
    Set-Location $InstallDir
}
Log "Repo pronto em $(Get-Location)"

# ──────────────────────────────────────────────────────────────────────────────
# 4. CRIA VENV E INSTALA DEPENDÊNCIAS
# ──────────────────────────────────────────────────────────────────────────────
$venv = "$InstallDir\venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Log "Criando venv..."
    & $py -m venv $venv
}
$pip = "$venv\Scripts\pip.exe"
$pyp = "$venv\Scripts\python.exe"

Log "Atualizando pip..."
& $pip install --upgrade pip --quiet

Log "Instalando JARVIS (editable)..."
& $pip install -e . --no-cache-dir 2>&1 | Tee-Object -Variable pipOut | Where { $_ -match "Successfully installed|ERROR|FAILED" } | ForEach { Log $_ }
if ($LASTEXITCODE -ne 0) { Err "Falha no pip install. Veja log acima." }

# ──────────────────────────────────────────────────────────────────────────────
# 5. NODE.JS + FRONTEND (opcional)
# ──────────────────────────────────────────────────────────────────────────────
if (Get-Command node -ErrorAction SilentlyContinue) {
    $nv = node --version
    Log "Node.js $nv detectado — buildando frontend..."
    Set-Location "$InstallDir\src\jarvis\transports\remote\frontend"
    & npm ci --silent 2>&1 | Out-Null
    & npm run build --silent 2>&1 | Out-Null
    Set-Location $InstallDir
    Log "Frontend buildado em dist/"
} else {
    Warn "Node.js não encontrado — pulando build do frontend (UI web não funcionará)."
    Warn "Instale em nodejs.org para habilitar."
}

# ──────────────────────────────────────────────────────────────────────────────
# 6. CRIA ATALHOS (Área de Trabalho + Menu Iniciar)
# ──────────────────────────────────────────────────────────────────────────────
Log "Criando atalhos..."
$ws = New-Object -ComObject WScript.Shell

# Área de Trabalho
$desk = [Environment]::GetFolderPath("Desktop")
$lnk  = $ws.CreateShortcut("$desk\JARVIS.lnk")
$lnk.TargetPath = "$pyp"
$lnk.Arguments  = "-m src.jarvis"
$lnk.WorkingDirectory = $InstallDir
$lnk.IconLocation = "$InstallDir\assets\icons\jarvis_installer_icon.ico"
$lnk.Description = "JARVIS CyberBot — Assistente Pessoal"
$lnk.Save()

# Menu Iniciar
$start = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\JARVIS"
New-Item -ItemType Directory -Force -Path $start | Out-Null
$lnk2 = $ws.CreateShortcut("$start\JARVIS CyberBot.lnk")
$lnk2.TargetPath = $pyp
$lnk2.Arguments  = "-m src.jarvis"
$lnk2.WorkingDirectory = $InstallDir
$lnk2.IconLocation = "$InstallDir\assets\icons\jarvis_installer_icon.ico"
$lnk2.Save()

Log "Atalhos criados."

# ──────────────────────────────────────────────────────────────────────────────
# 7. CONFIG INICIAL (config.yaml)
# ──────────────────────────────────────────────────────────────────────────────
$cfgDir = "$env:APPDATA\JARVIS\config"
$cfgFile = "$cfgDir\config.yaml"
if (-not (Test-Path $cfgFile)) {
    New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
    @"
gemini:
  api_key: ""
transports:
  local:
    enabled: true
  remote:
    enabled: true
    port: 8000
  webhook:
    enabled: true
"@ | Set-Content -Encoding utf8 $cfgFile
    Log "Config criada em $cfgFile (edite para adicionar sua Gemini API Key)."
}

# ──────────────────────────────────────────────────────────────────────────────
# 8. FINALIZAÇÃO
# ──────────────────────────────────────────────────────────────────────────────
Write-Host "`n╔══════════════════════════════════════════════════════════════╗" -Fore Cyan
Write-Host "║  ✅ INSTALAÇÃO CONCLUÍDA                                      ║" -Fore Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝`n" -Fore Cyan

Write-Host "📁 Pasta:      $InstallDir"
Write-Host "🐍 Venv:       $venv"
Write-Host "⚙️  Config:    $cfgFile"
Write-Host "🔗 Atalhos:    Área de Trabalho + Menu Iniciar"
Write-Host "`n🚀 Para rodar agora:"
Write-Host "   & '$pyp' -m src.jarvis"
Write-Host "`n🔑 Próximo passo: edite $cfgFile e cole sua Gemini API Key."
Write-Host "   Ou abra a UI (engrenagem → API Keys) na primeira execução.`n"

# Pergunta se quer rodar agora
$run = Ask "Rodar JARVIS agora?" "S"
if ($run -match "^[sS]") {
    & $pyp -m src.jarvis
}