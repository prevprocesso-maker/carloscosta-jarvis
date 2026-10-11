import os
import time
import subprocess
import platform
import shutil
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

_APP_ALIASES: dict[str, dict[str, str]] = {

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}


def _maybe_existing_path(raw: str) -> Path | None:
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        return None
    expanded = Path(os.path.expandvars(text)).expanduser()
    try:
        if expanded.exists():
            return expanded
    except OSError:
        return None
    return None


def _open_existing_path(path: Path) -> bool:
    """Abre arquivo/pasta recebido por engano no open_app."""
    try:
        if _SYSTEM == "Windows":
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
            except (OSError, PermissionError):
                if path.is_dir():
                    subprocess.Popen(
                        ["explorer.exe", str(path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                else:
                    subprocess.Popen(
                        ["cmd", "/d", "/s", "/c", "start", "", str(path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            time.sleep(0.5)
            return True
        if _SYSTEM == "Darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            return _iniciar_linux(["xdg-open", str(path)])
        time.sleep(0.5)
        return True
    except Exception as e:
        print(f"[open_app] path open failed: {e}")
        return False


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

def _launch_windows(app_name: str) -> bool:

    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name:
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _launch_macos(app_name: str) -> bool:

    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


# ── LINUX DE VERDADE (26/09/2026, cliente do Big Linux) ────────────────────
#
# O `_launch_linux` antigo dizia "Abri o browser, chefe." sem abrir nada: o
# `xdg-open browser` falhava e o `try` devolvia True mesmo assim. E ele nao
# conhecia nome generico ("browser", "downloads", "documents") nem programa
# que so existe como atalho do menu (os web apps do Brave, o Discord do
# flatpak). Agora a ordem e: pasta conhecida -> navegador padrao -> programa
# generico (terminal, calculadora...) -> atalho do menu pelo NOME que aparece
# nele -> executavel. E so diz "abri" quando o processo nao morreu com erro.
import re as _re
import unicodedata as _ud


def _sem_acento(txt: str) -> str:
    t = _ud.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode("ascii")
    return _re.sub(r"\s+", " ", t).strip().lower()


_PALAVRAS_DE_ENCHIMENTO = {
    "the", "my", "a", "an", "folder", "directory", "app", "application", "program",
    "o", "a", "os", "as", "meu", "minha", "pasta", "de", "do", "da", "dos", "das",
    "programa", "aplicativo", "please", "open", "abra", "abre", "abrir",
}


def _chave_linux(nome: str) -> str:
    palavras = [p for p in _re.findall(r"[a-z0-9+.-]+", _sem_acento(nome))
                if p not in _PALAVRAS_DE_ENCHIMENTO]
    return " ".join(palavras)


_PASTAS_LINUX = {
    "download": "DOWNLOAD", "downloads": "DOWNLOAD", "transferencias": "DOWNLOAD",
    "document": "DOCUMENTS", "documents": "DOCUMENTS", "documentos": "DOCUMENTS",
    "documento": "DOCUMENTS", "desktop": "DESKTOP", "area trabalho": "DESKTOP",
    "picture": "PICTURES", "pictures": "PICTURES", "photos": "PICTURES",
    "imagens": "PICTURES", "fotos": "PICTURES", "music": "MUSIC", "musicas": "MUSIC",
    "musica": "MUSIC", "video": "VIDEOS", "videos": "VIDEOS", "home": "HOME",
    "files": "HOME", "personal": "HOME", "arquivos": "HOME", "pessoal": "HOME",
}

# Nome generico -> candidatos em ordem (o primeiro instalado ganha). KDE
# primeiro: e o que o Big Linux traz.
_GENERICOS_LINUX = {
    ("browser", "web browser", "internet", "navegador", "navegador internet", "web"):
        ["@navegador"],
    ("file manager", "file explorer", "explorer", "finder", "gerenciador arquivos",
     "explorador arquivos"):
        ["dolphin", "nautilus", "nemo", "thunar", "pcmanfm"],
    ("terminal", "console", "konsole", "cmd", "powershell", "prompt", "command line"):
        ["konsole", "gnome-terminal", "kgx", "xfce4-terminal", "tilix", "alacritty",
         "kitty", "xterm"],
    ("calculator", "calculadora", "calc"):
        ["kcalc", "gnome-calculator", "qalculate-qt", "qalculate-gtk", "galculator",
         "speedcrunch"],
    ("text editor", "notepad", "editor", "bloco notas", "textedit", "notes"):
        ["kate", "kwrite", "gnome-text-editor", "gedit", "mousepad", "xed"],
    ("settings", "system settings", "configuracoes", "control panel",
     "painel controle", "configuracoes sistema"):
        ["systemsettings", "gnome-control-center", "xfce4-settings-manager"],
    ("task manager", "system monitor", "gerenciador tarefas", "monitor sistema"):
        ["plasma-systemmonitor", "ksysguard", "gnome-system-monitor",
         "xfce4-taskmanager"],
    ("paint", "drawing", "desenho"):
        ["kolourpaint", "pinta", "drawing", "gimp", "krita"],
    ("pdf", "pdf reader", "leitor pdf"):
        ["okular", "evince", "atril"],
    ("image viewer", "visualizador imagens"):
        ["gwenview", "eog", "loupe", "ristretto"],
    ("word", "writer"): ["libreoffice --writer"],
    ("excel", "spreadsheet", "planilha"): ["libreoffice --calc"],
    ("powerpoint", "slides", "apresentacao"): ["libreoffice --impress"],
    ("chrome", "google chrome"): ["google-chrome-stable", "google-chrome", "chromium"],
    ("edge", "microsoft edge"): ["microsoft-edge-stable", "microsoft-edge"],
    ("brave", "brave browser"): ["brave-browser", "brave"],
    ("vivaldi",): ["vivaldi-stable", "vivaldi"],
    ("firefox",): ["firefox"],
    ("vscode", "visual studio code", "vs code"): ["code", "codium"],
    ("screenshot", "print screen", "captura tela"): ["spectacle", "gnome-screenshot",
                                                      "flameshot"],
}


def _ambiente_do_programa() -> dict:
    """O ambiente do programa aberto, sem o que so vale para o JARVIS.

    O jarvis.sh poe QT_QPA_PLATFORM=xcb para o PROPRIO JARVIS ir pelo XWayland;
    herdado, isso empurraria os programas do KDE para o X11 tambem."""
    env = dict(os.environ)
    for k in ("QT_QPA_PLATFORM", "PYTHONUTF8", "PYTHONIOENCODING",
              "PLAYWRIGHT_BROWSERS_PATH", "PYTHONWARNINGS", "PYTHONHOME", "PYTHONPATH"):
        env.pop(k, None)
    node = os.path.join("runtime", "node", "bin")
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                  if not p.endswith(node))
    return env


def _iniciar_linux(cmd: list) -> bool:
    """Abre solto do JARVIS e confere que nao morreu com erro no primeiro 1,5 s."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, start_new_session=True,
                                env=_ambiente_do_programa(), cwd=str(Path.home()))
    except Exception as e:
        print(f"[open_app] nao iniciou {cmd}: {e}")
        return False
    for _ in range(15):
        rc = proc.poll()
        if rc is not None:
            # quem so repassa (gtk-launch, xdg-open, os scripts do Big Linux em
            # /usr/local/bin) sai com 0 quando deu certo
            return rc == 0
        time.sleep(0.1)
    return True


def _pasta_xdg(chave: str) -> Path | None:
    if chave == "HOME":
        return Path.home()
    try:
        r = subprocess.run(["xdg-user-dir", chave], capture_output=True, text=True, timeout=5)
        p = Path(r.stdout.strip())
        if r.returncode == 0 and p.is_dir() and p != Path.home():
            return p
    except Exception:
        pass
    padrao = {"DOWNLOAD": "Downloads", "DOCUMENTS": "Documents", "DESKTOP": "Desktop",
              "PICTURES": "Pictures", "MUSIC": "Music", "VIDEOS": "Videos"}.get(chave)
    p = Path.home() / padrao if padrao else None
    return p if p and p.is_dir() else None


def _pastas_de_atalhos() -> list[Path]:
    dados = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    pastas = [Path.home() / ".local" / "share" / "applications"]
    pastas += [Path(d) / "applications" for d in dados.split(":") if d]
    pastas += [Path("/var/lib/flatpak/exports/share/applications"),
               Path.home() / ".local/share/flatpak/exports/share/applications"]
    vistas, saida = set(), []
    for p in pastas:
        if p.is_dir() and str(p) not in vistas:
            vistas.add(str(p))
            saida.append(p)
    return saida


def _ler_atalho(arq: Path) -> dict | None:
    info: dict = {}
    try:
        dentro = False
        for linha in arq.read_text(encoding="utf-8", errors="ignore").splitlines():
            linha = linha.strip()
            if linha.startswith("["):
                dentro = (linha == "[Desktop Entry]")
                continue
            if dentro and "=" in linha:
                k, v = linha.split("=", 1)
                info.setdefault(k.strip(), v.strip())
    except Exception:
        return None
    if info.get("Type", "Application") != "Application":
        return None
    if info.get("Hidden", "").lower() == "true" or not info.get("Exec"):
        return None
    return info


def _achar_atalho(nome: str) -> Path | None:
    """O atalho do menu cujo NOME e o que o dono falou ("WhatsApp", "Discord")."""
    alvo = _chave_linux(nome)
    if not alvo:
        return None
    melhor, nota_melhor = None, 0
    for pasta in _pastas_de_atalhos():
        for arq in pasta.glob("*.desktop"):
            info = _ler_atalho(arq)
            if not info:
                continue
            nomes = {_chave_linux(info.get("Name", "")),
                     _chave_linux(info.get("Name[en_US]", "")),
                     _chave_linux(info.get("Name[en]", "")),
                     _chave_linux(info.get("GenericName", "")),
                     _chave_linux(arq.stem.replace("-", " ").replace("_", " "))}
            nomes.discard("")
            nota = 0
            for n in nomes:
                if n == alvo or n.replace(" ", "") == alvo.replace(" ", ""):
                    nota = max(nota, 3)
                elif n.startswith(alvo + " ") or n.endswith(" " + alvo):
                    nota = max(nota, 2)
                elif len(alvo) >= 4 and alvo in n:
                    nota = max(nota, 1)
            if nota <= 0:
                continue
            nota *= 2
            if info.get("NoDisplay", "").lower() == "true":
                nota -= 2          # atalho escondido so ganha se nao houver outro
            if "__" not in arq.stem and not arq.stem.endswith("-Default"):
                nota += 1          # empate: o programa de verdade antes do web app
            if nota > nota_melhor:
                melhor, nota_melhor = arq, nota
    return melhor


def _abrir_atalho(arq: Path) -> bool:
    if shutil.which("gtk-launch") and _iniciar_linux(["gtk-launch", arq.stem]):
        return True
    if shutil.which("kioclient"):
        if _iniciar_linux(["kioclient", "exec", str(arq)]):
            return True
    info = _ler_atalho(arq) or {}
    import shlex  # noqa: PLC0415
    try:
        partes = [p for p in shlex.split(info.get("Exec", ""))
                  if not _re.fullmatch(r"%[fFuUdDnNickvm]", p)]
    except ValueError:
        return False
    return bool(partes) and _iniciar_linux(partes)


def _abrir_navegador_padrao() -> bool:
    try:
        r = subprocess.run(["xdg-settings", "get", "default-web-browser"],
                           capture_output=True, text=True, timeout=5,
                           env=_ambiente_do_programa())
        desktop = r.stdout.strip()
        if r.returncode == 0 and desktop.endswith(".desktop"):
            for pasta in _pastas_de_atalhos():
                if (pasta / desktop).is_file():
                    return _abrir_atalho(pasta / desktop)
    except Exception:
        pass
    return _iniciar_linux(["xdg-open", "https://www.google.com"])


def abrir_no_linux(app_name: str) -> str:
    """Abre pelo nome falado. Devolve a frase (em portugues: em ingles o
    cerebro traduz antes de falar, ver `_resposta_pronta`)."""
    chave = _chave_linux(app_name)
    # 1) pasta conhecida
    if chave in _PASTAS_LINUX:
        pasta = _pasta_xdg(_PASTAS_LINUX[chave])
        if pasta and _iniciar_linux(["xdg-open", str(pasta)]):
            return f"Abri a pasta {pasta.name}, chefe."
        return f"Nao consegui abrir a pasta {app_name}."
    # 2) generico (navegador, terminal, calculadora...)
    for nomes, candidatos in _GENERICOS_LINUX.items():
        if chave in nomes:
            for cand in candidatos:
                if cand == "@navegador":
                    if _abrir_navegador_padrao():
                        return "Abri o navegador, chefe."
                    continue
                cmd = cand.split()
                if shutil.which(cmd[0]) and _iniciar_linux(cmd):
                    return f"Abri o {app_name}, chefe."
            break
    # 3) atalho do menu pelo nome (web apps do Brave, flatpak, AppImage)
    atalho = _achar_atalho(app_name)
    if atalho is not None and _abrir_atalho(atalho):
        nome = (_ler_atalho(atalho) or {}).get("Name") or app_name
        return f"Abri o {nome}, chefe."
    # 4) executavel com esse nome
    for cand in {app_name, app_name.lower(), app_name.lower().replace(" ", "-"),
                 app_name.lower().replace(" ", "_"), app_name.lower().replace(" ", "")}:
        binario = shutil.which(cand)
        if binario and _iniciar_linux([binario]):
            return f"Abri o {app_name}, chefe."
    return (f"Nao encontrei o {app_name} instalado neste computador. "
            f"Diga o nome como aparece no menu de programas.")


def _launch_linux(app_name: str) -> bool:

    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-")) or
        shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        subprocess.run(
            ["xdg-open", app_name],
            capture_output=True, timeout=5
        )
        return True
    except Exception:
        pass

    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "No application name provided."

    existing_path = _maybe_existing_path(app_name)
    if existing_path is not None:
        if player:
            player.write_log(f"[open_app] abrindo caminho: {existing_path}")
        if _open_existing_path(existing_path):
            kind = "pasta" if existing_path.is_dir() else "arquivo"
            return f"Abrindo {kind}: {existing_path.name}"
        return f"Não consegui abrir: {existing_path}"

    if _SYSTEM == "Linux":
        if player:
            player.write_log(f"[open_app] {app_name}")
        try:
            return abrir_no_linux(app_name)
        except Exception as e:
            print(f"[open_app] Linux: {e}")
            return f"Nao consegui abrir o {app_name}: {str(e)[:80]}"

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    # A FRASE SAI EM PORTUGUES (05/09/2026). Estas voltas vao DIRETO para a
    # boca quando ja sao frase pronta (ver `_resposta_pronta` no cerebro do
    # Codex, que economiza uma volta de 4 a 9 s). Em ingles, o Diego ouviu a
    # assistente dizer "Opened Explorador de Arquivos" — e o atalho que existe
    # para ganhar tempo virou um defeito de idioma.
    try:
        if launcher(normalized):
            return f"Abri o {app_name}, chefe."
        if normalized.lower() != app_name.lower():
            if launcher(app_name):
                return f"Abri o {app_name}, chefe."
        return (f"Mandei abrir o {app_name}, mas nao consegui confirmar que "
                f"abriu — pode estar carregando ou nao estar instalado.")
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Nao consegui abrir o {app_name}: {e}"