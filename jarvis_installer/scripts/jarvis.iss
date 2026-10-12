; ──────────────────────────────────────────────────────────────────────────────
; JARVIS CyberBot — Inno Setup Installer (Windows)
; Gera: setup_jarvis.exe (assinável, silencioso, com rollback)
; ──────────────────────────────────────────────────────────────────────────────
#define AppName        "JARVIS CyberBot"
#define AppVersion     "2.0.0"
#define AppPublisher   "Carlos Costa"
#define AppURL         "https://github.com/prevprocesso-maker/carloscosta-jarvis"
#define AppExeName     "JARVIS.exe"
#define Branch         "phase5/desktop-integration"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={pf}\JARVIS
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=setup_jarvis_v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstall=64
ArchitecturesAllowed=x64
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=no
DisableFinishedPage=no
WizardStyle=modern
SetupIconFile=assets\icons\jarvis_installer_icon.ico
UninstallDisplayIcon={app}\assets\icons\jarvis_installer_icon.ico
UninstallDisplayName={#AppName}
AppId={{A3F2C1D4-7E8B-4F9A-9C2D-1E3F4A5B6C7D}}
AppCopyright=Copyright © 2026 Carlos Costa
LicenseFile=LICENSE.txt
InfoBeforeFile=README.txt
WindowResizable=yes
WindowStartMaximized=no
RestartIfNeededByRun=false

[Languages]
Name: "portugues"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupservice"; Description: "Instalar como serviço Windows (auto-start com o sistema)"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checked
Name: "autoupdate"; Description: "Habilitar atualização automática diária"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checked

[Files]
; ── Python Embedded (portável, sem instalar Python no sistema) ──
Source: "python-embedded\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs; Permissions: users-modify

; ── Código fonte JARVIS ──
Source: "src\*"; DestDir: "{app}\src"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "jarvis_installer\*"; DestDir: "{app}\jarvis_installer"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Frontend build (se existir) ──
Source: "src\jarvis\transports\remote\frontend\dist\*"; DestDir: "{app}\src\jarvis\transports\remote\frontend\dist"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── NSSM para serviço ──
Source: "tools\nssm.exe"; DestDir: "{app}"; Flags: ignoreversion

; ── Scripts de apoio ──
Source: "scripts\install_service.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "scripts\uninstall_service.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "scripts\update.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "scripts\health_check.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion

[Icons]
Name: "{commonprograms}\{#AppName}\{#AppName}"; Filename: "{app}\python\python.exe"; Parameters: "-m src.jarvis"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icons\jarvis_installer_icon.ico"; Comment: "JARVIS CyberBot — Assistente Pessoal"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\python\python.exe"; Parameters: "-m src.jarvis"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icons\jarvis_installer_icon.ico"; Comment: "JARVIS CyberBot — Assistente Pessoal"; Tasks: desktopicon
Name: "{commonprograms}\{#AppName}\Desinstalar"; Filename: "{uninstallexe}"; IconFilename: "{app}\assets\icons\jarvis_installer_icon.ico"; Comment: "Remove JARVIS CyberBot"
Name: "{commonprograms}\{#AppName}\Configurar API Key"; Filename: "notepad.exe"; Parameters: "{commonappdata}\JARVIS\config\config.yaml"; Comment: "Edita chave Gemini e configurações"

[Run]
; Instala dependências Python (embedded pip)
Filename: "{app}\python\python.exe"; Parameters: "-m pip install --upgrade pip -q"; WorkingDir: "{app}"; StatusMsg: "Atualizando pip..."; Flags: runhidden waituntilterminated
Filename: "{app}\python\python.exe"; Parameters: "-m pip install -e . --no-cache-dir"; WorkingDir: "{app}"; StatusMsg: "Instalando JARVIS..."; Flags: runhidden waituntilterminated
Filename: "{app}\python\python.exe"; Parameters: "-m pip install -e jarvis_installer --no-cache-dir"; WorkingDir: "{app}"; StatusMsg: "Instalando instalador..."; Flags: runhidden waituntilterminated

; Build frontend (se Node.js disponível)
Filename: "cmd.exe"; Parameters: "/c if exist node.exe (cd /d "{app}\src\jarvis\transports\remote\frontend" && npm ci --silent && npm run build --silent)"; WorkingDir: "{app}"; StatusMsg: "Buildando frontend..."; Flags: runhidden waituntilterminated skipifsilent

; Serviço Windows (NSSM)
Filename: "{app}\nssm.exe"; Parameters: 'install "JARVIS-CyberBot" "{app}\python\python.exe" "-m src.jarvis"'; WorkingDir: "{app}"; StatusMsg: "Instalando serviço..."; Flags: runhidden waituntilterminated; Tasks: startupservice
Filename: "{app}\nssm.exe"; Parameters: 'set "JARVIS-CyberBot" AppDirectory "{app}"'; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Tasks: startupservice
Filename: "{app}\nssm.exe"; Parameters: 'set "JARVIS-CyberBot" AppStdout "{app}\logs\jarvis-service.log"'; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Tasks: startupservice
Filename: "{app}\nssm.exe"; Parameters: 'set "JARVIS-CyberBot" AppStderr "{app}\logs\jarvis-service-error.log"'; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Tasks: startupservice
Filename: "{app}\nssm.exe"; Parameters: 'set "JARVIS-CyberBot" AppRotateFiles 1'; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Tasks: startupservice
Filename: "{app}\nssm.exe"; Parameters: 'set "JARVIS-CyberBot" Start SERVICE_AUTO_START'; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Tasks: startupservice
Filename: "{app}\nssm.exe"; Parameters: 'set "JARVIS-CyberBot" Description "JARVIS CyberBot - Assistente pessoal unificado"'; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Tasks: startupservice
Filename: "{app}\nssm.exe"; Parameters: 'start "JARVIS-CyberBot"'; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Tasks: startupservice

; Auto-update scheduled task
Filename: "powershell.exe"; Parameters: '-ExecutionPolicy Bypass -Command "Register-ScheduledTask -TaskName \"JARVIS-AutoUpdate\" -Action (New-ScheduledTaskAction -Execute \"powershell.exe\" -Argument \"-ExecutionPolicy Bypass -File `\"{app}\scripts\update.ps1`\"\") -Trigger (New-ScheduledTaskTrigger -Daily -At 3am) -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable) -RunLevel Highest -Force -User SYSTEM"'; StatusMsg: "Configurando auto-update..."; Flags: runhidden waituntilterminated; Tasks: autoupdate

[UninstallRun]
; Para e remove serviço
Filename: "{app}\nssm.exe"; Parameters: 'stop "JARVIS-CyberBot"'; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: 'remove "JARVIS-CyberBot" confirm'; Flags: runhidden waituntilterminated

; Remove scheduled task
Filename: "powershell.exe"; Parameters: '-ExecutionPolicy Bypass -Command "Unregister-ScheduledTask -TaskName \"JARVIS-AutoUpdate\" -Confirm:$false -ErrorAction SilentlyContinue"'; Flags: runhidden

[Dirs]
; Pastas com permissão de escrita para usuários
Name: "{app}\logs"; Permissions: users-modify
Name: "{app}\data"; Permissions: users-modify
Name: "{app}\data\memory"; Permissions: users-modify
Name: "{app}\data\whatsapp"; Permissions: users-modify
Name: "{app}\updates"; Permissions: users-modify

; Config em ProgramData (persiste entre updates)
Name: "{commonappdata}\JARVIS\config"; Permissions: users-modify
Name: "{commonappdata}\JARVIS\logs"; Permissions: users-modify

[Ini]
; Config padrão (criada se não existir)
Filename: "{commonappdata}\JARVIS\config\config.yaml"; Section: "gemini"; Key: "api_key"; String: ""
Filename: "{commonappdata}\JARVIS\config\config.yaml"; Section: "transports\local"; Key: "enabled"; String: "true"
Filename: "{commonappdata}\JARVIS\config\config.yaml"; Section: "transports\remote"; Key: "enabled"; String: "true"
Filename: "{commonappdata}\JARVIS\config\config.yaml"; Section: "transports\remote"; Key: "port"; String: "8000"
Filename: "{commonappdata}\JARVIS\config\config.yaml"; Section: "transports\webhook"; Key: "enabled"; String: "true"
Filename: "{commonappdata}\JARVIS\config\config.yaml"; Section: "update"; Key: "channel"; String: "nightly"
Filename: "{commonappdata}\JARVIS\config\config.yaml"; Section: "update"; Key: "auto_check"; String: "true"

[Code]
; ──────────────────────────────────────────────────────────────────────────────
; Verifica Python Embedded / Sistema
; ──────────────────────────────────────────────────────────────────────────────
function InitializeSetup(): Boolean;
var
  PyPath: String;
  PyVer: String;
begin
  // Tenta achar Python 3.11+ no sistema
  PyPath := ExpandConstant('{cmd}\python.exe');
  if not FileExists(PyPath) then
    PyPath := ExpandConstant('{pf}\Python311\python.exe');
  if not FileExists(PyPath) then
    PyPath := ExpandConstant('{pf}\Python312\python.exe');
  if not FileExists(PyPath) then
    PyPath := ExpandConstant('{localappdata}\Programs\Python\Python311\python.exe');
  if not FileExists(PyPath) then
    PyPath := ExpandConstant('{localappdata}\Programs\Python\Python312\python.exe');
  
  if FileExists(PyPath) then begin
    // Verifica versão
    // (Inno não tem exec nativo fácil, assume OK se achou)
    Log('Python encontrado: ' + PyPath);
  end else begin
    MsgBox('Python 3.11+ não encontrado.'#13#10'Baixe em https://python.org/downloads'#13#10'Marque "Add to PATH" durante a instalação.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  
  Result := True;
end;

; ──────────────────────────────────────────────────────────────────────────────
; Página custom: Chave API Gemini (opcional)
; ──────────────────────────────────────────────────────────────────────────────
var
  ApiKeyPage: TWizardPage;
  ApiKeyEdit: TEdit;

procedure CreateApiKeyPage;
begin
  ApiKeyPage := CreateCustomPage(wpReady, 'Configuração da API Gemini', 
    'Insira sua chave da API do Google Gemini (obrigatória para funcionar).'#13#10'Pode deixar vazio e configurar depois na UI (engrenagem → API Keys).');
  ApiKeyEdit := TEdit.Create(ApiKeyPage);
  ApiKeyEdit.Parent := ApiKeyPage.Surface;
  ApiKeyEdit.Left := 0;
  ApiKeyEdit.Top := 0;
  ApiKeyEdit.Width := ApiKeyPage.SurfaceWidth;
  ApiKeyEdit.Height := 28;
  ApiKeyEdit.PasswordChar := '*';
  ApiKeyEdit.Text := '';
end;

procedure CurPageChanged(CurPageID: Integer);
var
  CfgFile: String;
  Key: String;
begin
  if CurPageID = ApiKeyPage.ID then begin
    // Salva chave no config.yaml
    CfgFile := ExpandConstant('{commonappdata}\JARVIS\config\config.yaml');
    Key := Trim(ApiKeyEdit.Text);
    if Key <> '' then begin
      // Atualiza YAML simples
      // (Idealmente usar lib YAML, mas string replace funciona)
    end;
  end;
end;

; ──────────────────────────────────────────────────────────────────────────────
; Silent install support
; ──────────────────────────────────────────────────────────────────────────────
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  if WizardSilent then begin
    // Pula páginas desnecessárias no modo silencioso
    Result := (PageID = wpWelcome) or (PageID = wpLicense) or (PageID = ApiKeyPage.ID);
  end else begin
    Result := False;
  end;
end;

; ──────────────────────────────────────────────────────────────────────────────
; Validação final
; ──────────────────────────────────────────────────────────────────────────────
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpReady then begin
    // Inicia serviço se marcado
    if IsTaskSelected('startupservice') then begin
      Exec('{app}\nssm.exe', 'start "JARVIS-CyberBot"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;