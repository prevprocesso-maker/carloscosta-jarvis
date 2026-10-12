<# 
.SYNOPSIS
    JARVIS CyberBot - Instalador de Sistema (Admin) com Serviço Windows
.DESCRIPTION
    Instalação completa: venv, dependências, frontend, serviço NSSM (auto-start),
    atalhos, desinstalador, atualizador automático.
.REQUIRES
    - PowerShell 5.1+ (Admin)
    - Windows 10/11
#>

# ──────────────────────────────────────────────────────────────────────────────
# VERIFICA ADMIN
# ──────────────────────────────────────────────────────────────────────────────
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "⚠ Rode como ADMINISTRADOR (botão direito → Executar como Administrador)"
    exit 1
}

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
$RepoUrl        = "https://github.com/prevprocesso-maker/carloscosta-jarvis.git"
$Branch         = "phase5/desktop-integration"
$InstallDir     = "C:\Program Files\JARVIS"          # Sistema (requer Admin)
$ServiceName    = "JARVIS-CyberBot"
$ServiceDisplay = "JARVIS CyberBot Assistant"
$PythonMinVer   = "3.11"
$NodeMinVer     = "18"
$NssmUrl        = "https://github.com/nssm/nssm/releases/download/v2.24/nssm-2.24.zip"

Write-Host "`n╔══════════════════════════════════════════════════════════════╗" -Fore Cyan
Write-Host "║  JARVIS CyberBot — Instalador de SISTEMA (Admin) v2.0.0      ║" -Fore Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝`n" -Fore Cyan

function Log   { param($m) Write-Host "[$(Get-Date -Format HH:mm:ss)] $m" -Fore Green }
function Warn  { param($m) Write-Host "[$(Get-Date -Format HH:mm:ss)] ⚠ $m" -Fore Yellow }
function Err   { param($m) Write-Error $m; exit 1 }
function Ask   { param($q,$d) $r=Read-Host "`n$q [$d]"; if(-not $r){$d}else{$r} }

# ──────────────────────────────────────────────────────────────────────────────
# 1. PYTHON + GIT
# ──────────────────────────────────────────────────────────────────────────────
Log "Verificando Python $PythonMinVer+..."
$py = (Get-Command python -ea 0) ?? (Get-Command py -ea 0)
if(-not $py){ Err "Python não encontrado. Instale de python.org (marque 'Add to PATH')." }
$ver = & $py --version 2>&1 | Select-String "(\d+\.\d+)" | %{$_.Matches[0].Value}
if([version]$ver -lt [version]$PythonMinVer){ Err "Python $ver < $PythonMinVer" }
Log "Python $ver — $($py.Source)"

$git = Get-Command git -ea 0
if(-not $git){ Warn "Git não no PATH. Instalando via winget..."; winget install -e --id Git.Git -s winget -h; $git=Get-Command git -ea 0 }
if(-not $git){ Err "Git necessário (git-scm.com)" }
Log "Git OK"

# ──────────────────────────────────────────────────────────────────────────────
# 2. DIRETÓRIOS DE SISTEMA
# ──────────────────────────────────────────────────────────────────────────────
$Dirs = @(
    $InstallDir,
    "$InstallDir\venv",
    "$InstallDir\logs",
    "$InstallDir\data",
    "$InstallDir\data\memory",
    "$InstallDir\data\whatsapp",
    "$InstallDir\updates",
    "$env:PROGRAMDATA\JARVIS\config",
    "$env:PROGRAMDATA\JARVIS\logs"
)
foreach($d in $Dirs){ if(-not (Test-Path $d)){ New-Item -ItemType Directory -Force -Path $d | Out-Null; Log "Criado: $d" } }

# Permissões para usuários comuns (logs, data)
$acl = Get-Acl "$InstallDir\logs"
$rule = New-Object Security.AccessControl.FileSystemAccessRule("Users","Modify","ContainerInherit,ObjectInherit","None","Allow")
$acl.AddAccessRule($rule)
Set-Acl "$InstallDir\logs" $acl
$acl = Get-Acl "$InstallDir\data"
$acl.AddAccessRule($rule)
Set-Acl "$InstallDir\data" $acl

# ──────────────────────────────────────────────────────────────────────────────
# 3. CLONE / ATUALIZA
# ──────────────────────────────────────────────────────────────────────────────
Log "Clonando/atualizando repo em $InstallDir..."
if(Test-Path "$InstallDir\.git"){
    Set-Location $InstallDir
    git fetch --all
    git reset --hard origin/$Branch
    git clean -fd
}else{
    git clone --branch $Branch --depth 1 $RepoUrl $InstallDir
    Set-Location $InstallDir
}

# ──────────────────────────────────────────────────────────────────────────────
# 4. VENV + DEPS
# ──────────────────────────────────────────────────────────────────────────────
$venv = "$InstallDir\venv"
if(-not (Test-Path "$venv\Scripts\python.exe")){
    Log "Criando venv..."
    & $py -m venv $venv
}
$pip = "$venv\Scripts\pip.exe"
$pyp = "$venv\Scripts\python.exe"

Log "Instalando JARVIS..."
& $pip install --upgrade pip --quiet
& $pip install -e . --no-cache-dir 2>&1 | Tee-Object -Variable pipOut | Where{$_ -match "Successfully|ERROR|FAILED"} | %{Log $_}
if($LASTEXITCODE -ne 0){ Err "Falha no pip install" }

# ──────────────────────────────────────────────────────────────────────────────
# 5. NODE + FRONTEND
# ──────────────────────────────────────────────────────────────────────────────
if(Get-Command node -ea 0){
    $nv = node --version
    Log "Node.js $nv — buildando frontend..."
    Set-Location "$InstallDir\src\jarvis\transports\remote\frontend"
    npm ci --silent 2>&1 | Out-Null
    npm run build --silent 2>&1 | Out-Null
    Set-Location $InstallDir
    Log "Frontend OK"
}else{ Warn "Node.js não encontrado — UI web desabilitada" }

# ──────────────────────────────────────────────────────────────────────────────
# 6. BAIXA NSSM
# ──────────────────────────────────────────────────────────────────────────────
$nssmExe = "$InstallDir\nssm.exe"
if(-not (Test-Path $nssmExe)){
    Log "Baixando NSSM..."
    $zip = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri $NssmUrl -OutFile $zip -UseBasicParsing
    Expand-Archive -Force -Path $zip -DestinationPath "$env:TEMP\nssm"
    Copy-Item "$env:TEMP\nssm\nssm-2.24\win64\nssm.exe" $nssmExe -Force
    Remove-Item $zip -Force; Remove-Item "$env:TEMP\nssm" -Recurse -Force
    Log "NSSM instalado em $nssmExe"
}

# ──────────────────────────────────────────────────────────────────────────────
# 7. INSTALA SERVIÇO WINDOWS (NSSM)
# ──────────────────────────────────────────────────────────────────────────────
Log "Instalando serviço Windows: $ServiceName"
& $nssmExe install $ServiceName $pyp "-m src.jarvis" 2>&1 | Out-Null
& $nssmExe set $ServiceName AppDirectory $InstallDir 2>&1 | Out-Null
& $nssmExe set $ServiceName AppStdout "$InstallDir\logs\jarvis-service.log" 2>&1 | Out-Null
& $nssmExe set $ServiceName AppStderr "$InstallDir\logs\jarvis-service-error.log" 2>&1 | Out-Null
& $nssmExe set $ServiceName AppRotateFiles 1 2>&1 | Out-Null
& $nssmExe set $ServiceName AppRotateOnline 1 2>&1 | Out-Null
& $nssmExe set $ServiceName AppEnvironmentExtra "JARVIS_SYSTEM_INSTALL=1" 2>&1 | Out-Null
& $nssmExe set $ServiceName Start SERVICE_AUTO_START 2>&1 | Out-Null
& $nssmExe set $ServiceName Description "JARVIS CyberBot - Assistente pessoal unificado (local + remoto + WhatsApp)" 2>&1 | Out-Null
& $nssmExe set $ServiceName ObjectName "LocalSystem" 2>&1 | Out-Null

# Inicia serviço
& $nssmExe start $ServiceName 2>&1 | Out-Null
Start-Sleep 2
$status = & $nssmExe status $ServiceName 2>&1
Log "Serviço: $status"

# ──────────────────────────────────────────────────────────────────────────────
# 8. ATALHOS (All Users)
# ──────────────────────────────────────────────────────────────────────────────
Log "Criando atalhos (All Users)..."
$ws = New-Object -ComObject WScript.Shell

# Desktop Público
$desk = [Environment]::GetFolderPath("CommonDesktopDirectory")
$lnk = $ws.CreateShortcut("$desk\JARVIS CyberBot.lnk")
$lnk.TargetPath = $pyp
$lnk.Arguments = "-m src.jarvis"
$lnk.WorkingDirectory = $InstallDir
$lnk.IconLocation = "$InstallDir\assets\icons\jarvis_installer_icon.ico"
$lnk.Description = "JARVIS CyberBot — Assistente Pessoal"
$lnk.Save()

# Menu Iniciar All Users
$start = [Environment]::GetFolderPath("CommonPrograms")
$jarvisStart = "$start\JARVIS CyberBot"
New-Item -ItemType Directory -Force -Path $jarvisStart | Out-Null
$lnk2 = $ws.CreateShortcut("$jarvisStart\JARVIS CyberBot.lnk")
$lnk2.TargetPath = $pyp
$lnk2.Arguments = "-m src.jarvis"
$lnk2.WorkingDirectory = $InstallDir
$lnk2.IconLocation = "$lnk.IconLocation"
$lnk2.Save()

# Desinstalador no Menu Iniciar
$lnkUn = $ws.CreateShortcut("$jarvisStart\Desinstalar JARVIS.lnk")
$lnkUn.TargetPath = "powershell.exe"
$lnkUn.Arguments = "-ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
$lnkUn.WorkingDirectory = $InstallDir
$lnkUn.IconLocation = "C:\Windows\System32\shell32.dll,161"
$lnkUn.Save()

Log "Atalhos criados"

# ──────────────────────────────────────────────────────────────────────────────
# 9. CONFIG + DESINSTALADOR
# ──────────────────────────────────────────────────────────────────────────────
$cfgDir = "$env:PROGRAMDATA\JARVIS\config"
$cfgFile = "$cfgDir\config.yaml"
if(-not (Test-Path $cfgFile)){
    @"
gemini:
  api_key: ""
transports:
  local:
    enabled: true
  remote:
    enabled: true
    host: 0.0.0.0
    port: 8000
  webhook:
    enabled: true
    whatsapp_port: 8765
update:
  channel: nightly
  auto_check: true
"@ | Set-Content -Encoding utf8 $cfgFile
    Log "Config: $cfgFile"
}

# Desinstalador
@"
<# 
.SYNOPSIS
    Desinstalador JARVIS CyberBot (Admin)
#>
if(-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    Write-Error 'Rode como Administrador'; exit 1
}
\$svc = 'JARVIS-CyberBot'
\$nssm = 'C:\Program Files\JARVIS\nssm.exe'
Write-Host 'Parando serviço...'
& \$nssm stop \$svc 2>\$null
Start-Sleep 2
Write-Host 'Removendo serviço...'
& \$nssm remove \$svc confirm 2>\$null
Write-Host 'Removendo atalhos...'
Remove-Item \"\$env:PUBLIC\Desktop\JARVIS CyberBot.lnk\" -ea 0
Remove-Item \"\$env:ALLUSERSPROFILE\Microsoft\Windows\Start Menu\Programs\JARVIS CyberBot\" -Recurse -Force -ea 0
Write-Host 'Removendo arquivos...'
Remove-Item 'C:\Program Files\JARVIS' -Recurse -Force -ea 0
Remove-Item '\$env:PROGRAMDATA\JARVIS' -Recurse -Force -ea 0
Write-Host '✅ Desinstalação completa'
Read-Host 'Pressione Enter para sair'
"@ | Set-Content -Encoding utf8 "$InstallDir\uninstall.ps1"

# Atualizador simples (scheduled task)
$taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$InstallDir\update.ps1`""
$taskTrigger = New-ScheduledTaskTrigger -Daily -At 3am
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd
Register-ScheduledTask -TaskName "JARVIS-AutoUpdate" -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -RunLevel Highest -Force -User "SYSTEM" | Out-Null
Log "Agendador de atualização criado (diário 3h)"

# Script de atualização
@"
param([string]\$Channel='nightly')
\$InstallDir = 'C:\Program Files\JARVIS'
\$venv = \"\$InstallDir\venv\Scripts\pip.exe\"
Write-Host \"[\$(Get-Date)] Verificando atualizações (\$Channel)...\"
Set-Location \$InstallDir
git fetch --all
git checkout \$Branch
\$local = git rev-parse HEAD
\$remote = git rev-parse origin/\$Branch
if(\$local -ne \$remote){
    Write-Host \"Nova versão encontrada — atualizando...\"
    git pull
    & \$venv install -e . --no-cache-dir
    Write-Host \"Reiniciando serviço...\"
    & \"\$InstallDir\nssm.exe\" restart JARVIS-CyberBot
    Write-Host \"✅ Atualizado\"
}else{
    Write-Host \"Já na versão mais recente\"
}
"@ | Set-Content -Encoding utf8 "$InstallDir\update.ps1"

# ──────────────────────────────────────────────────────────────────────────────
# 10. FINAL
# ──────────────────────────────────────────────────────────────────────────────
Write-Host "`n╔══════════════════════════════════════════════════════════════╗" -Fore Cyan
Write-Host "║  ✅ INSTALAÇÃO DE SISTEMA CONCLUÍDA                           ║" -Fore Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝`n" -Fore Cyan

Write-Host "📁 Pasta:           $InstallDir"
Write-Host "🐍 Venv:            $venv"
Write-Host "⚙️  Config:         $cfgFile"
Write-Host "🔧 Serviço:         $ServiceName (Auto-start, LocalSystem)"
Write-Host "📋 Logs serviço:    $InstallDir\logs\jarvis-service*.log"
Write-Host "🔗 Atalhos:         Área de Trabalho + Menu Iniciar (Todos)"
Write-Host "🗑  Desinstalador:  Menu Iniciar → JARVIS CyberBot → Desinstalar"
Write-Host "🔄 Auto-update:     Diário 3h (Scheduled Task) — canal: $Branch"
Write-Host "`n🔑 Configure a Gemini API Key em: $cfgFile"
Write-Host "   Ou abra a UI (engrenagem → API Keys) na 1ª execução.`n"

$run = Ask "Iniciar serviço agora?" "S"
if($run -match '^[sS]'){ & $nssmExe start $ServiceName; Log "Serviço iniciado" }