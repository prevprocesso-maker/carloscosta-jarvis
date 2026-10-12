<# 
.SYNOPSIS
    Build script para gerar instalador JARVIS assinado (Inno Setup + Code Signing)
.DESCRIPTION
    1. Prepara assets (Python embedded, NSSM, ícones)
    2. Compila Inno Setup (.iss → .exe)
    3. Assina digitalmente (Authenticode) com certificado PFX ou Azure Key Vault
    4. Verifica assinatura
    5. Gera hash SHA256 para release notes
.REQUIRES
    - Inno Setup 6 (iscc.exe no PATH)
    - Windows SDK (signtool.exe) OU Azure Key Vault CLI
    - Certificado Code Signing (PFX) ou acesso Azure Key Vault
#>

param(
    [string]$Version = "2.0.0",
    [string]$Branch = "phase5/desktop-integration",
    [string]$CertPath = "",           # Caminho do .pfx (se local)
    [string]$CertPassword = "",       # Senha do .pfx
    [string]$AzureKeyVault = "",      # Nome do Key Vault (se Azure)
    [string]$AzureCertName = "",      # Nome do certificado no Key Vault
    [string]$TimestampUrl = "http://timestamp.sectigo.com",  # RFC3161
    [string]$OutputDir = "dist",
    [switch]$SkipSign = $false
)

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
$RepoRoot = (Get-Item -Path ".\" -Verbose).FullName
$IssFile = "$RepoRoot\jarvis_installer\scripts\jarvis.iss"
$SetupExe = "$RepoRoot\$OutputDir\setup_jarvis_v$Version.exe"
$SignedExe = "$RepoRoot\$OutputDir\setup_jarvis_v$Version-signed.exe"
$HashFile = "$RepoRoot\$OutputDir\setup_jarvis_v$Version-signed.exe.sha256"

Write-Host "`n╔══════════════════════════════════════════════════════════════╗" -Fore Cyan
Write-Host "║  JARVIS CyberBot — Build Instalador Assinado v$Version        ║" -Fore Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝`n" -Fore Cyan

function Log   { param($m) Write-Host "[$(Get-Date -Format HH:mm:ss)] $m" -Fore Green }
function Warn  { param($m) Write-Host "[$(Get-Date -Format HH:mm:ss)] ⚠ $m" -Fore Yellow }
function Err   { param($m) Write-Error $m; exit 1 }

# ──────────────────────────────────────────────────────────────────────────────
# 1. PREPARA ASSETS NECESSÁRIOS
# ──────────────────────────────────────────────────────────────────────────────
Log "Verificando ferramentas..."

# Inno Setup
$iscc = Get-Command iscc -ea 0
if(-not $iscc){ Err "Inno Setup não encontrado. Instale: choco install innosetup" }
Log "Inno Setup: $($iscc.Source)"

# signtool (Windows SDK)
$signtool = Get-Command signtool -ea 0
if(-not $signtool -and -not $SkipSign -and -not $AzureKeyVault){
    Warn "signtool não no PATH. Tentando local padrão..."
    $sdkPaths = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe",
        "${env:ProgramFiles}\Windows Kits\10\bin\*\x64\signtool.exe"
    )
    foreach($p in $sdkPaths){
        $matches = Get-Item $p -ea 0
        if($matches){ $signtool = $matches | Sort-Object LastWriteTime -Descending | Select-Object -First 1; break }
    }
}
if($signtool){ Log "signtool: $($signtool.Source)" } else { Warn "signtool não encontrado — pular assinatura local" }

# Python Embedded (baixa se não existir)
$pyEmbedDir = "$RepoRoot\jarvis_installer\assets\python-embedded"
if(-not (Test-Path "$pyEmbedDir\python.exe")){
    Log "Baixando Python Embedded 3.11..."
    $pyUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
    $zip = "$env:TEMP\python-embedded.zip"
    Invoke-WebRequest -Uri $pyUrl -OutFile $zip -UseBasicParsing
    Expand-Archive -Force -Path $zip -DestinationPath $pyEmbedDir
    Remove-Item $zip
    # Habilita site-packages no python311._pth
    $pth = "$pyEmbedDir\python311._pth"
    if(Test-Path $pth){
        (Get-Content $pth) -replace '^#import site', 'import site' | Set-Content $pth
    }
    Log "Python Embedded pronto em $pyEmbedDir"
}

# NSSM
$nssmSrc = "$RepoRoot\jarvis_installer\assets\nssm.exe"
if(-not (Test-Path $nssmSrc)){
    Log "Baixando NSSM..."
    $nssmUrl = "https://github.com/nssm/nssm/releases/download/v2.24/nssm-2.24.zip"
    $zip = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri $nssmUrl -OutFile $zip -UseBasicParsing
    Expand-Archive -Force -Path $zip -DestinationPath "$env:TEMP\nssm"
    $nssmExe = Get-ChildItem "$env:TEMP\nssm\nssm-2.24\win64\nssm.exe" -ea 0
    if($nssmExe){ Copy-Item $nssmExe.FullName $nssmSrc -Force }
    Remove-Item $zip -Force; Remove-Item "$env:TEMP\nssm" -Recurse -Force
    Log "NSSM salvo em $nssmSrc"
}

# Ícone
$iconSrc = "$RepoRoot\assets\icons\jarvis_installer_icon.ico"
if(-not (Test-Path $iconSrc)){
    Err "Ícone não encontrado: $iconSrc"
}

# README/License para Inno
if(-not (Test-Path "$RepoRoot\README.txt")){
    Get-Content "$RepoRoot\README.md" -Raw | Set-Content -Encoding utf8 "$RepoRoot\README.txt"
}
if(-not (Test-Path "$RepoRoot\LICENSE.txt")){
    @"
MIT License

Copyright (c) 2026 Carlos Costa

Permission is hereby granted...
"@ | Set-Content -Encoding utf8 "$RepoRoot\LICENSE.txt"
}

# ──────────────────────────────────────────────────────────────────────────────
# 2. COMPILA INNO SETUP
# ──────────────────────────────────────────────────────────────────────────────
Log "Compilando Inno Setup..."
$isccArgs = @(
    "/O$OutputDir",
    "/Fsetup_jarvis_v$Version",
    "/DAppVersion=$Version",
    "/DBranch=$Branch",
    $IssFile
)
& $iscc.Source $isccArgs 2>&1 | Tee-Object -Variable isccOut | Where{$_ -match "Error|Success|Compiled"} | %{Log $_}
if($LASTEXITCODE -ne 0){ Err "Falha na compilação Inno Setup" }

if(-not (Test-Path $SetupExe)){ Err "Instalador não gerado: $SetupExe" }
$size = (Get-Item $SetupExe).Length / 1MB
Log "Instalador gerado: $SetupExe ($([math]::Round($size,1)) MB)"

# ──────────────────────────────────────────────────────────────────────────────
# 3. ASSINATURA DIGITAL (Authenticode)
# ──────────────────────────────────────────────────────────────────────────────
if(-not $SkipSign){
    if($AzureKeyVault -and $AzureCertName){
        Log "Assinando via Azure Key Vault..."
        # Requer: az login, az keyvault certificate sign
        $azArgs = @(
            "keyvault", "certificate", "sign",
            "--vault-name", $AzureKeyVault,
            "--name", $AzureCertName,
            "--algorithm", "SHA256",
            "--file", $SetupExe,
            "--output-file", $SignedExe
        )
        & az $azArgs 2>&1 | Tee-Object -Variable azOut | %{Log $_}
        if($LASTEXITCODE -ne 0){ Err "Falha na assinatura Azure" }
    } elseif($CertPath -and $CertPassword){
        Log "Assinando com certificado local (PFX)..."
        if(-not (Test-Path $CertPath)){ Err "Certificado não encontrado: $CertPath" }
        if(-not $signtool){ Err "signtool necessário para assinatura local" }
        
        $signArgs = @(
            "sign",
            "/f", $CertPath,
            "/p", $CertPassword,
            "/tr", $TimestampUrl,
            "/td", "SHA256",
            "/fd", "SHA256",
            "/v",
            $SetupExe
        )
        & $signtool.Source $signArgs 2>&1 | Tee-Object -Variable signOut | %{Log $_}
        if($LASTEXITCODE -ne 0){ Err "Falha na assinatura local" }
        Copy-Item $SetupExe $SignedExe -Force
    } else {
        Warn "Nenhum método de assinatura configurado — pulando (use -CertPath/-CertPassword ou -AzureKeyVault/-AzureCertName)"
        Copy-Item $SetupExe $SignedExe -Force
    }
} else {
    Log "Pulando assinatura (--SkipSign)"
    Copy-Item $SetupExe $SignedExe -Force
}

# ──────────────────────────────────────────────────────────────────────────────
# 4. VERIFICA ASSINATURA
# ──────────────────────────────────────────────────────────────────────────────
if(Test-Path $SignedExe -and -not $SkipSign){
    Log "Verificando assinatura..."
    if($signtool){
        & $signtool.Source verify /pa /v $SignedExe 2>&1 | Tee-Object -Variable verifyOut | %{Log $_}
        if($LASTEXITCODE -eq 0){
            Log "✅ Assinatura VÁLIDA"
        } else {
            Err "❌ Assinatura INVÁLIDA"
        }
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# 5. HASH SHA256
# ──────────────────────────────────────────────────────────────────────────────
$hash = Get-FileHash -Algorithm SHA256 $SignedExe | Select-Object -ExpandProperty Hash
"$hash  setup_jarvis_v$Version-signed.exe" | Set-Content -Encoding utf8 $HashFile
Log "SHA256: $hash"
Log "Hash salvo em: $HashFile"

# ──────────────────────────────────────────────────────────────────────────────
# 6. RESUMO
# ──────────────────────────────────────────────────────────────────────────────
Write-Host "`n╔══════════════════════════════════════════════════════════════╗" -Fore Cyan
Write-Host "║  ✅ BUILD CONCLUÍDO                                           ║" -Fore Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝`n" -Fore Cyan

Write-Host "📦 Instalador:     $SignedExe"
Write-Host "🔐 SHA256:         $hash"
Write-Host "📄 Hash file:      $HashFile"
Write-Host "`nPara distribuir: copie $SignedExe para releases GitHub / CDN"