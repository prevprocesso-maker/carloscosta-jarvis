import os
import shutil
import platform
import unicodedata
import subprocess
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from datetime import datetime
import time

try:
    import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

_SAFE_ROOTS: list[Path] = [
    Path.home(),
]

def _notify_task(player, state: str | None = None, log: str | None = None) -> None:
    """Atualiza a interface sem quebrar execução em modo console/teste."""
    if not player:
        return
    try:
        if state and hasattr(player, "set_state"):
            player.set_state(state)
    except Exception:
        pass
    try:
        if log and hasattr(player, "write_log"):
            player.write_log(log)
    except Exception:
        pass

def _windows_drive_roots() -> list[Path]:
    """Retorna todos os drives visíveis no Windows: C:, D:, E:, pendrive etc.

    Usa mais de uma fonte porque alguns ambientes do Windows não retornam
    pendrive/disco externo de forma confiável em uma única API.
    """
    roots: list[Path] = []
    if _OS != "Windows":
        return roots

    def add_root(value: str) -> None:
        text = str(value or "").strip().strip('"').replace("\\", "/")
        if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
            root = Path(f"{text[0].upper()}:/")
            try:
                if os.path.isdir(str(root)):
                    roots.append(root)
            except OSError:
                pass

    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for index in range(26):
            if mask & (1 << index):
                add_root(f"{chr(65 + index)}:/")
    except Exception:
        pass

    # Fallback pelo CMD: costuma listar inclusive unidades removíveis.
    try:
        completed = subprocess.run(
            ["cmd", "/d", "/s", "/c", "fsutil fsinfo drives"],
            capture_output=True,
            text=True,
            errors="ignore",
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for token in (completed.stdout or "").replace("Drives:", "").split():
            add_root(token)
    except Exception:
        pass

    # Último fallback: testa letras comuns manualmente.
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        add_root(f"{letter}:/")

    return _dedupe_paths(roots)

def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    clean: list[Path] = []
    for path in paths:
        try:
            key = str(path.resolve()).lower() if _OS == "Windows" else str(path.resolve())
        except Exception:
            key = str(path).lower() if _OS == "Windows" else str(path)
        if key in seen:
            continue
        seen.add(key)
        clean.append(path)
    return clean

def _broad_search_roots() -> list[Path]:
    """Raízes usadas quando o usuário não sabe onde o item está.

    A busca ampla não pode ficar presa na Área de Trabalho/Home e deixar de
    olhar outros discos. Por isso, no Windows, os discos extras e pendrives
    entram antes do C:. A varredura em si é feita de forma intercalada.
    """
    if _OS == "Windows":
        drives = _windows_drive_roots()
        c_roots = [d for d in drives if str(d).lower().startswith("c:")]
        other_roots = [d for d in drives if not str(d).lower().startswith("c:")]
        common = [
            _get_desktop(), _get_downloads(), _get_documents(),
            _get_pictures(), _get_music(), _get_videos(), Path.home(),
        ]
        roots = other_roots + common + c_roots
        return _dedupe_paths([r for r in roots if r])
    # Linux/macOS: mantém a busca dentro da pasta do usuário para evitar varrer sistema inteiro.
    return [Path.home()]

def _is_broad_search_path(raw: str) -> bool:
    normalized = _normalize_shortcut(raw)
    return normalized in {
        "", "computer", "computador", "meu computador", "este computador",
        "pc", "maquina", "todos", "tudo", "todos discos", "todos os discos",
        "drives", "all", "all drives", "whole computer", "sistema inteiro",
    }


_SEARCH_STOPWORDS = {
    "arquivo", "arquivos", "pasta", "pastas", "item", "itens",
    "nome", "chamado", "chamada", "chama", "com", "do", "da", "de",
    "no", "na", "em", "meu", "minha", "um", "uma", "o", "a",
    "procura", "procurar", "procurando", "busca", "buscar", "localiza",
    "localizar", "encontra", "encontrar", "ache", "achar", "estou", "to",
    "ta", "esta", "que", "eu", "pra", "para", "por", "favor",
}

# Nomes normalizados com _normalize_search_text(). Mantém a busca ampla rápida
# evitando pastas técnicas/cache que costumam ter centenas de milhares de itens.
_BROAD_SEARCH_SKIP_DIRS = {
    "recycle bin", "system volume information", "recovery", "windows",
    "program files", "program files x86", "programdata", "appdata",
    "node modules", "pycache", "git", "venv", "env",
    "mypy cache", "pytest cache", "ruff cache",
    "gradle", "nuget", "rustup", "jarvis profiles",
    "autofillaimodelcache", "temporary internet files",
}

# Por segurança, o comando de abrir arquivo/pasta não executa scripts,
# instaladores ou programas. Aplicativos continuam sendo abertos pelo open_app.
_BLOCKED_OPEN_EXTENSIONS = {
    ".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs",
    ".js", ".jse", ".wsf", ".wsh", ".scr", ".com",
    ".reg", ".jar",
}


def _normalize_search_text(value: str) -> str:
    """Normaliza texto para busca tolerante a acentos, hífen, underline e espaços."""
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    out: list[str] = []
    for ch in text.lower():
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def _is_broad_skip_name(name: str) -> bool:
    raw = str(name or "").strip()
    if not raw:
        return False
    normalized = _normalize_search_text(raw)
    # Em busca ampla, pastas ocultas/técnicas prendem a varredura e raramente
    # são o alvo de uma busca por voz. Busca direcionada não usa esse filtro.
    if raw.startswith("."):
        return True
    if normalized in _BROAD_SEARCH_SKIP_DIRS:
        return True
    if normalized.endswith(" cache") or normalized.endswith(" caches"):
        return True
    return False


def _is_inside_broad_skipped_path(path: Path) -> bool:
    try:
        parts = path.parts
    except Exception:
        parts = str(path).replace("\\", "/").split("/")
    return any(_is_broad_skip_name(part) for part in parts)


def _is_windows_c_path(path: Path) -> bool:
    return _OS == "Windows" and str(path).replace("\\", "/").lower().startswith("c:/")


def _search_query_tokens(query: str) -> list[str]:
    tokens = _normalize_search_text(query).split()
    clean = [t for t in tokens if t and t not in _SEARCH_STOPWORDS]
    return clean or tokens


def _matches_search_query(entry_name: str, query_name: str, ext: str, is_file: bool) -> bool:
    if ext and (not is_file or Path(entry_name).suffix.lower() != ext):
        return False
    if not query_name:
        return True

    candidate_norm = _normalize_search_text(entry_name)
    query_norm = _normalize_search_text(query_name)
    if query_norm and query_norm in candidate_norm:
        return True

    candidate_compact = candidate_norm.replace(" ", "")
    query_compact = query_norm.replace(" ", "")
    if query_compact and query_compact in candidate_compact:
        return True

    tokens = _search_query_tokens(query_name)
    if tokens and all((token in candidate_norm) or (token in candidate_compact) for token in tokens):
        return True

    return False


def _is_exact_search_match(entry_name: str, query_name: str) -> bool:
    """Reconhece nome exato mesmo quando a consulta veio como frase de voz."""
    if not query_name:
        return False
    candidate = _normalize_search_text(entry_name)
    candidate_stem = _normalize_search_text(Path(entry_name).stem)
    raw_query = _normalize_search_text(query_name)
    useful_query = " ".join(_search_query_tokens(query_name))
    return candidate in {raw_query, useful_query} or candidate_stem in {raw_query, useful_query}


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve()).lower() if _OS == "Windows" else str(path.resolve())
    except Exception:
        return str(path).lower() if _OS == "Windows" else str(path)


def _should_skip_broad_dir(path: Path) -> bool:
    return _is_broad_skip_name(path.name)


def _iter_search_entries_breadth_first(roots: list[Path], broad: bool = False):
    """Varre pastas sem ficar presa em caches enormes.

    Na busca ampla, cada raiz é varrida por vez na ordem escolhida
    (D:/E:/pendrive antes do C:). Isso evita que o C: roube a busca antes de
    olhar o disco onde o usuário provavelmente salvou a pasta.
    """
    root_groups = ([root] for root in roots) if broad else (roots,)
    for root_group in root_groups:
        queue: list[Path] = list(root_group)
        index = 0
        while index < len(queue):
            current = queue[index]
            index += 1
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except (OSError, PermissionError):
                            is_dir = False
                        yield entry
                        if is_dir:
                            child = Path(entry.path)
                            if broad and _should_skip_broad_dir(child):
                                continue
                            queue.append(child)
            except (OSError, PermissionError):
                continue


def _windows_dir_patterns(query_name: str, ext: str) -> list[str]:
    """Padrões para busca rápida via DIR, mantendo poucos passes por disco."""
    patterns: list[str] = []
    query_name = str(query_name or "").strip().strip('"').strip("'")
    ext = str(ext or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = "." + ext

    def add(pattern: str) -> None:
        pattern = pattern.replace('"', "").strip()
        # O padrão passa pelo cmd.exe. Nomes com metacaracteres ficam para o
        # fallback Python, sem transformar texto ditado em parte de um comando.
        pattern = "".join(ch for ch in pattern if ch not in "&|<>^%!")
        if pattern and pattern not in patterns:
            patterns.append(pattern)

    if query_name:
        normalized = _normalize_search_text(query_name)
        normalized_tokens = [t for t in _search_query_tokens(query_name) if len(t) >= 2]
        raw_tokens = [t.strip() for t in query_name.replace("_", " ").replace("-", " ").split() if len(t.strip()) >= 2]
        if normalized_tokens:
            add("*" + "*".join(normalized_tokens) + "*")
        add(f"*{query_name}*")
        if normalized and normalized != query_name.lower():
            add("*" + normalized.replace(" ", "*") + "*")
        for token in sorted(set(raw_tokens + normalized_tokens), key=len, reverse=True):
            add(f"*{token}*")
    elif ext:
        add(f"*{ext}")

    return patterns[:4] or ["*"]


def _windows_fast_search_roots(roots: list[Path]) -> list[Path]:
    drives = _windows_drive_roots()
    other_drives = [d for d in drives if not _is_windows_c_path(d)]
    common = [_get_desktop(), _get_downloads(), _get_documents(), _get_pictures(), _get_music(), _get_videos()]
    # Pastas pessoais vêm primeiro. Path.home() repetiria todas elas e ainda
    # obrigaria o DIR a atravessar AppData. Outros discos vêm logo depois.
    return _dedupe_paths([p for p in common + other_drives if p and Path(p).exists()]) or roots


def _find_files_windows_dir_fast(query_name: str, ext: str, roots: list[Path],
                                 max_results: int, seconds_budget: float,
                                 player=None) -> list[Path]:
    """Busca rápida usando o DIR do Windows, sem despejar cada pasta no log."""
    if _OS != "Windows" or seconds_budget <= 1:
        return []

    patterns = _windows_dir_patterns(query_name, ext)
    found: list[Path] = []
    seen: set[str] = set()
    started = time.monotonic()

    for root in _windows_fast_search_roots(roots):
        remaining = seconds_budget - (time.monotonic() - started)
        if remaining <= 1 or len(found) >= max_results:
            break

        root_text = str(root).rstrip("\\/") + "\\"
        _notify_task(player, "PROCURANDO", f"[file] Busca rápida em {root_text}")

        # Discos extras ganham mais tempo; pastas comuns do C: ganham menos.
        root_budget = min(remaining, 16 if not _is_windows_c_path(root) else 6)
        per_pattern_budget = max(2.0, root_budget / max(len(patterns), 1))

        for pattern in patterns:
            remaining = seconds_budget - (time.monotonic() - started)
            if remaining <= 1 or len(found) >= max_results:
                break
            timeout = min(per_pattern_budget, remaining)
            target = root_text + pattern
            stdout = ""
            try:
                completed = subprocess.run(
                    ["cmd", "/d", "/c", "dir", "/s", "/b", "/a", target],
                    capture_output=True,
                    text=True,
                    errors="ignore",
                    timeout=timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                stdout = completed.stdout or ""
            except subprocess.TimeoutExpired as exc:
                output = exc.output or ""
                stdout = output.decode(errors="ignore") if isinstance(output, bytes) else str(output)
            except Exception:
                continue

            for line in stdout.splitlines():
                text = line.strip().strip('"')
                if not text:
                    continue
                item = Path(text)
                if _is_inside_broad_skipped_path(item):
                    continue
                try:
                    is_file = item.is_file()
                    is_dir = item.is_dir()
                except (OSError, PermissionError):
                    continue
                if not is_file and not is_dir:
                    continue
                if not _matches_search_query(item.name, query_name, ext, is_file):
                    continue
                key = _path_key(item)
                if key in seen:
                    continue
                seen.add(key)
                found.append(item)
                if len(found) >= max_results:
                    break

            # Para um nome exato, continuar em Imagens, Vídeos e outros discos
            # só aumenta a latência. Mantém todas as cópias exatas produzidas
            # pelo DIR desta raiz e devolve imediatamente.
            if any(_is_exact_search_match(item.name, query_name) for item in found):
                return found

    return found


def _is_safe_path(target: Path) -> bool:
    """Permite a pasta do usuário e discos de dados, mas protege o disco do sistema."""
    try:
        resolved = target.resolve()
        if any(
            resolved == root.resolve() or resolved.is_relative_to(root.resolve())
            for root in _SAFE_ROOTS
        ):
            return True
        if _OS == "Windows":
            target_drive = resolved.drive.upper()
            system_drive = os.environ.get("SystemDrive", "C:").upper()
            return bool(target_drive and target_drive != system_drive)
        return False
    except Exception:
        return False

def _get_windows_shell_folder(value_names: tuple[str, ...], fallback: Path) -> Path:
    """Lê uma pasta conhecida real do Windows, inclusive redirecionamentos."""
    if _OS != "Windows":
        return fallback
    try:
        import winreg
        locations = (
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
        for subkey in locations:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                    for value_name in value_names:
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                            candidate = Path(os.path.expandvars(str(value))).expanduser()
                            if candidate.exists():
                                return candidate
                        except OSError:
                            continue
            except OSError:
                continue
    except Exception:
        pass
    return fallback


def _get_desktop() -> Path:
    """Return the user's real Desktop/Área de Trabalho path.

    On Windows, many Brazilian installs redirect the Desktop to OneDrive
    (for example: C:/Users/alfam/OneDrive/Área de Trabalho).
    Path.home() / "Desktop" is often the wrong folder in that case, so we
    first read Windows known-folder registry values and then try OneDrive
    candidates before falling back to the classic Desktop path.
    """
    home = Path.home()

    if _OS == "Windows":
        candidates: list[Path] = []

        try:
            import winreg

            registry_locations = [
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"),
            ]
            for root, subkey in registry_locations:
                try:
                    with winreg.OpenKey(root, subkey) as key:
                        value, _ = winreg.QueryValueEx(key, "Desktop")
                        if value:
                            candidates.append(Path(os.path.expandvars(str(value))).expanduser())
                except OSError:
                    continue
        except Exception:
            pass

        for env_name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            one_drive = os.environ.get(env_name)
            if one_drive:
                base = Path(one_drive).expanduser()
                candidates.extend([
                    base / "Área de Trabalho",
                    base / "Area de Trabalho",
                    base / "Desktop",
                ])

        candidates.extend([
            home / "OneDrive" / "Área de Trabalho",
            home / "OneDrive" / "Area de Trabalho",
            home / "OneDrive" / "Desktop",
            home / "Desktop",
        ])

        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate
            except OSError:
                continue

        return candidates[0] if candidates else home / "Desktop"

    if _OS == "Linux":
        xdg = os.environ.get("XDG_DESKTOP_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)

    return home / "Desktop"

def _get_downloads() -> Path:
    if _OS == "Windows":
        return _get_windows_shell_folder(
            ("{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
            Path.home() / "Downloads",
        )
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DOWNLOAD_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Downloads"

def _get_documents() -> Path:
    if _OS == "Windows":
        return _get_windows_shell_folder(("Personal",), Path.home() / "Documents")
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DOCUMENTS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Documents"

def _get_pictures() -> Path:
    if _OS == "Windows":
        return _get_windows_shell_folder(("My Pictures",), Path.home() / "Pictures")
    if _OS == "Linux":
        xdg = os.environ.get("XDG_PICTURES_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Pictures"

def _get_music() -> Path:
    if _OS == "Windows":
        return _get_windows_shell_folder(("My Music",), Path.home() / "Music")
    if _OS == "Linux":
        xdg = os.environ.get("XDG_MUSIC_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Music"

def _get_videos() -> Path:
    if _OS == "Windows":
        return _get_windows_shell_folder(("My Video",), Path.home() / "Videos")
    if _OS == "Linux":
        xdg = os.environ.get("XDG_VIDEOS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Videos"


def _normalize_shortcut(raw: str) -> str:
    text = str(raw or "").strip().strip('"').strip("'")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.lower().replace("_", " ").replace("-", " ").split())


def _conserta_usuario_inventado(caminho: Path) -> Path:
    """O modelo chutou o nome do usuario? Traz o caminho para a casa CERTA.

    Defeito real (05/09/2026): o Diego pediu para salvar a historia na area de
    trabalho e o cerebro montou `C:\\Users\\Edwin\\Desktop\\Chapeuzinho.txt`.
    "Edwin" nao existe nesta maquina — o modelo inventou um nome de usuario.
    O arquivo caia fora da casa do dono, `_is_safe_path` recusava, e a resposta
    era "Access denied" duas vezes seguidas, com ele repetindo o pedido.

    Aqui o `Users\\<qualquer>` vira o `Users\\<dono>` de verdade e o resto do
    caminho e preservado. E mais seguro do que recusar: o arquivo passa a
    nascer DENTRO da pasta do dono, que e onde ele mandou salvar.

    So mexe quando o nome esta errado — caminho certo passa intacto, e nada
    fora de `Users` (ou `/Users` no Mac) e tocado.
    """
    try:
        partes = list(caminho.parts)
        if len(partes) < 3:
            return caminho
        casa = Path.home()
        dono = casa.name
        for i, p in enumerate(partes[:-1]):
            if p.lower() in ("users", "usuarios") and partes[i + 1].lower() != dono.lower():
                # A pasta do outro usuario existe de verdade? Entao respeita:
                # pode ser uma maquina com duas contas e um pedido legitimo.
                if (Path(*partes[:i + 2])).is_dir():
                    return caminho
                novo = casa.joinpath(*partes[i + 2:])
                try:
                    print(f"[file] usuario inventado: {caminho} -> {novo}")
                except Exception:
                    pass
                return novo
    except Exception:
        pass
    return caminho


def _resolve_path(raw: str) -> Path:
    raw_text = str(raw or "").strip().strip('"').strip("'")
    normalized = _normalize_shortcut(raw_text)

    if _OS == "Windows" and len(raw_text) == 2 and raw_text[1] == ":" and raw_text[0].isalpha():
        return Path(raw_text + "/")

    shortcuts: dict[str, Path] = {
        "desktop":             _get_desktop(),
        "area de trabalho":    _get_desktop(),
        "area trabalho":       _get_desktop(),
        "minha area de trabalho": _get_desktop(),
        "downloads":           _get_downloads(),
        "download":            _get_downloads(),
        "documents":           _get_documents(),
        "documentos":          _get_documents(),
        "pictures":            _get_pictures(),
        "imagens":             _get_pictures(),
        "music":               _get_music(),
        "musicas":             _get_music(),
        "videos":              _get_videos(),
        "home":                Path.home(),
        "usuario":             Path.home(),
    }
    if normalized in shortcuts:
        return shortcuts[normalized]

    # Também resolve atalhos no início de caminhos compostos, por exemplo
    # "documentos/Projetos" ou "Área de Trabalho\\Relatórios".
    # SO EM CAMINHO RELATIVO (26/09/2026). No Linux todo caminho absoluto
    # comeca com "/home/<dono>/...", e o "home" casava com o atalho acima:
    # "/home/johndr/Downloads" virava "/home/johndr/johndr/Downloads" e o
    # JARVIS respondia que a pasta nao existia.
    absoluto = raw_text.startswith(("/", "\\", "~")) or (
        len(raw_text) >= 2 and raw_text[1] == ":")
    path_parts = [part for part in raw_text.replace("\\", "/").split("/") if part]
    if len(path_parts) > 1 and not absoluto:
        shortcut_root = shortcuts.get(_normalize_shortcut(path_parts[0]))
        if shortcut_root:
            return shortcut_root.joinpath(*path_parts[1:])

    expanded = Path(os.path.expandvars(raw_text)).expanduser()
    expanded = _conserta_usuario_inventado(expanded)

    # If Gemini/LLM passes the classic Windows Desktop path, redirect it to
    # the real Desktop detected above (usually OneDrive\Área de Trabalho).
    if _OS == "Windows":
        try:
            home_desktop = (Path.home() / "Desktop").resolve()
            home_area = (Path.home() / "Área de Trabalho").resolve()
            expanded_resolved = expanded.resolve()
            if expanded_resolved in {home_desktop, home_area}:
                return _get_desktop()
        except Exception:
            pass

    return expanded


def _looks_like_known_location(raw: str) -> bool:
    """Diz se o texto parece um caminho/local real, e não apenas um nome."""
    text = str(raw or "").strip().strip('"').strip("'")
    normalized = _normalize_shortcut(text)
    if normalized in {
        "desktop", "area de trabalho", "area trabalho", "minha area de trabalho",
        "downloads", "download", "documents", "documentos", "pictures", "imagens",
        "music", "musicas", "videos", "home", "usuario",
        "computer", "computador", "meu computador", "este computador", "pc",
    }:
        return True
    if _OS == "Windows" and len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        return True
    return any(sep in text for sep in ("\\", "/", "~", "%"))


def _open_this_computer(player=None) -> str:
    """Abre 'Este Computador' quando o usuário pede para abrir o computador."""
    _notify_task(player, "ABRINDO", "[file] abrindo Este Computador")
    try:
        if _OS == "Windows":
            subprocess.Popen(
                ["explorer.exe", "shell:MyComputerFolder"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return "Abrindo Este Computador."
        if _OS == "Darwin":
            subprocess.Popen(["open", str(Path.home())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "Abrindo a pasta do usuário."
        subprocess.Popen(["xdg-open", str(Path.home())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "Abrindo a pasta do usuário."
    except Exception as e:
        return f"Não consegui abrir o computador: {e}"


def _unique_site_extract_path(candidate: Path) -> Path:
    """Escolhe uma pasta de extração sem sobrescrever um site já entregue."""
    candidate = Path(candidate)
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        option = candidate.parent / f"{candidate.name}_{index}"
        if not option.exists():
            return option
    return candidate.parent / f"{candidate.name}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _extract_site_archive(target: Path) -> Path | None:
    """Extrai um ZIP de site para uma pasta real antes de abrir o HTML.

    O Explorer/navegadores do Windows podem abrir um HTML diretamente dentro
    da visualização temporária de um ZIP. Nessa visualização, referências
    relativas a CSS, JavaScript e imagens podem falhar, deixando a página com
    o estilo padrão. Só tratamos o arquivo como site quando há um index.html
    HTML e uma folha de estilo; ZIPs comuns continuam sendo abertos pelo app
    padrão.
    """
    if Path(target).suffix.lower() != ".zip":
        return None

    root: Path | None = None
    try:
        with zipfile.ZipFile(target, "r") as archive:
            files = [info for info in archive.infolist() if not info.is_dir()]
            if not files or len(files) > 5000:
                return None

            index_info = None
            for info in sorted(files, key=lambda item: (item.filename.count("/") + item.filename.count("\\"), item.filename)):
                normalized = str(info.filename or "").replace("\\", "/")
                if PurePosixPath(normalized).name.lower() == "index.html":
                    index_info = info
                    break
            if index_info is None or index_info.file_size > 4 * 1024 * 1024:
                return None

            try:
                index_text = archive.read(index_info).decode("utf-8", errors="replace").lower()
            except Exception:
                return None
            if "<html" not in index_text and "<!doctype" not in index_text:
                return None
            if "stylesheet" not in index_text and ".css" not in index_text:
                return None

            total_size = sum(max(0, int(info.file_size or 0)) for info in files)
            if total_size > 200 * 1024 * 1024:
                return None

            # Mantém os resultados agrupados ao lado do ZIP e nunca sobrescreve
            # outra entrega do mesmo nome.
            root = _unique_site_extract_path(Path(target).with_suffix(""))
            root.mkdir(parents=True, exist_ok=False)
            root_resolved = root.resolve()

            for info in files:
                normalized = str(info.filename or "").replace("\\", "/")
                parts = PurePosixPath(normalized).parts
                if not parts or normalized.startswith("/") or ":" in parts[0] or ".." in parts:
                    raise ValueError("membro inválido no arquivo compactado")
                destination = (root.joinpath(*parts)).resolve()
                if os.path.commonpath([str(root_resolved), str(destination)]) != str(root_resolved):
                    raise ValueError("membro fora da pasta de extração")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

        extracted_index = next(
            (path for path in root.rglob("index.html") if path.is_file()),
            None,
        )
        if extracted_index is None:
            shutil.rmtree(root, ignore_errors=True)
            return None
        return extracted_index
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)
        return None


def _open_path_with_default_app(target: Path, player=None) -> str:
    """Abre arquivo/pasta com o app padrão do sistema, sem exigir _SAFE_ROOTS."""
    try:
        target = Path(target).expanduser()
        if not target.exists():
            return f"Não encontrei: {target}"

        if target.is_file() and target.suffix.lower() in _BLOCKED_OPEN_EXTENSIONS:
            return (
                f"Chefe, por segurança eu não executei esse tipo de arquivo: {target.name}. "
                "Se for um programa, peça para abrir pelo nome do aplicativo."
            )

        if target.is_file() and target.suffix.lower() == ".zip":
            extracted_index = _extract_site_archive(target)
            if extracted_index is not None:
                _notify_task(player, "ABRINDO", f"[file] abrindo site: {extracted_index.name}")
                try:
                    if _OS == "Windows":
                        os.startfile(str(extracted_index))  # type: ignore[attr-defined]
                    elif _OS == "Darwin":
                        subprocess.Popen(["open", str(extracted_index)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        subprocess.Popen(["xdg-open", str(extracted_index)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return f"Abrindo site gerado: {extracted_index.parent.name}"
                except (OSError, PermissionError):
                    # Se o navegador padrão não puder ser iniciado, a abertura
                    # normal do ZIP abaixo ainda deixa o arquivo disponível.
                    pass

        kind = "pasta" if target.is_dir() else "arquivo"
        _notify_task(player, "ABRINDO", f"[file] abrindo {kind}: {target.name}")

        if _OS == "Windows":
            try:
                os.startfile(str(target))  # type: ignore[attr-defined]
            except (OSError, PermissionError):
                # Fallback para Windows quando startfile devolve 'Access denied'
                # em alguns caminhos do OneDrive/discos externos.
                if target.is_dir():
                    subprocess.Popen(
                        ["explorer.exe", str(target)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                else:
                    subprocess.Popen(
                        ["cmd", "/d", "/s", "/c", "start", "", str(target)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            return f"Abrindo {kind}: {target.name}"

        if _OS == "Darwin":
            subprocess.Popen(["open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Abrindo {kind}: {target.name}"

    except PermissionError:
        return (
            f"Chefe, o Windows negou permissão para abrir: {target}. "
            "Tente abrir como administrador ou confira se o arquivo ainda existe."
        )
    except Exception as e:
        return f"Não consegui abrir {target}: {e}"


def _find_first_path_for_open(query_name: str, path: str = "computer", max_seconds: int = 35, player=None) -> Path | None:
    """Busca o primeiro arquivo/pasta para abrir, usando a mesma lógica da busca rápida."""
    query_name = str(query_name or "").strip().strip('"').strip("'")
    raw_path = str(path or "computer").strip()
    if not query_name:
        return None

    broad = _is_broad_search_path(raw_path)
    if broad:
        roots = _broad_search_roots()
        scope_label = "todo o computador"
    else:
        try:
            search_path = _resolve_path(raw_path)
        except Exception:
            search_path = Path(raw_path)
        if not search_path.exists():
            return None
        roots = [search_path]
        scope_label = str(search_path)

    roots = _dedupe_paths([r for r in roots if r])
    if not roots:
        return None

    max_seconds = max(8, min(int(max_seconds or 35), 45 if broad else 90))
    started = time.monotonic()
    _notify_task(player, "PROCURANDO", f"[file] Procurando {query_name} para abrir em {scope_label}...")

    if broad and _OS == "Windows":
        fast_found = _find_files_windows_dir_fast(
            query_name=query_name,
            ext="",
            roots=roots,
            max_results=1,
            seconds_budget=min(22.0, max_seconds * 0.65),
            player=player,
        )
        if fast_found:
            return fast_found[0]

    for root in roots:
        if time.monotonic() - started >= max_seconds:
            break
        try:
            if root.exists() and _matches_search_query(root.name, query_name, "", root.is_file()):
                return root
        except (OSError, PermissionError):
            continue

    last_status = 0.0
    iterator = _iter_search_entries_breadth_first(roots, broad=broad)
    checked = 0
    for entry in iterator:
        elapsed = time.monotonic() - started
        if elapsed >= max_seconds:
            break
        checked += 1
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
            if not is_dir and not is_file:
                continue
            if broad and is_dir and _should_skip_broad_dir(Path(entry.path)):
                continue
            if elapsed - last_status >= 10.0:
                last_status = elapsed
                _notify_task(player, "PROCURANDO", f"[file] Procurando para abrir... {int(elapsed)}s | {checked} itens")
            if _matches_search_query(entry.name, query_name, "", is_file):
                return Path(entry.path)
        except (OSError, PermissionError):
            continue
    return None


def open_item(path: str = "computer", name: str = "", max_seconds: int = 35, player=None) -> str:
    """Abre pasta, documento, imagem ou arquivo com o app padrão do Windows/macOS/Linux."""
    raw_path = str(path or "computer").strip()
    raw_name = str(name or "").strip().strip('"').strip("'")

    try:
        broad = _is_broad_search_path(raw_path)

        if broad and not raw_name:
            return _open_this_computer(player=player)

        if broad and raw_name:
            found = _find_first_path_for_open(raw_name, path="computer", max_seconds=max_seconds, player=player)
            if not found:
                return (
                    f"Chefe, procurei '{raw_name}' para abrir em todo o computador, mas não encontrei. "
                    "Se você souber o disco ou a pasta aproximada, me diga que eu abro direto."
                )
            return _open_path_with_default_app(found, player=player)

        base = _resolve_path(raw_path)
        target = (base / raw_name) if raw_name else base
        if target.exists():
            return _open_path_with_default_app(target, player=player)

        # Caso o modelo mande o nome no campo path em vez do campo name:
        # action=open, path="aleatorio D2", name="".
        if not raw_name and raw_path and not _looks_like_known_location(raw_path):
            found = _find_first_path_for_open(raw_path, path="computer", max_seconds=max_seconds, player=player)
            if found:
                return _open_path_with_default_app(found, player=player)

        where = str(base) if raw_name else raw_path
        return f"Chefe, não encontrei '{raw_name or raw_path}' em {where}."

    except Exception as e:
        return f"Não consegui abrir: {e}"


def _format_size(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def _safe_trash(target: Path) -> str:

    if not _SEND2TRASH:
        return (
            "Chefe, não consegui enviar para a Lixeira porque o pacote send2trash "
            "não está instalado. A exclusão permanente ficou bloqueada por segurança."
        )
    send2trash.send2trash(str(target))
    # Anota para o "pode desfazer" (01/09/2026): restaura pela Lixeira.
    try:
        from actions.lixeira import registrar_exclusao  # noqa: PLC0415
        registrar_exclusao(target)
    except Exception:
        pass
    return f"Movido para a Lixeira: {target.name}"


def list_files(path: str = "desktop", show_hidden: bool = False) -> str:
    try:
        target = _resolve_path(path)
        if not _is_safe_path(target):
            return f"Nao tenho acesso a esse lugar: {target}"
        if not target.exists():
            return f"Nao achei esse caminho: {target}"
        if not target.is_dir():
            return f"Not a directory: {target}"

        items = []
        for item in sorted(target.iterdir()):
            if not show_hidden and item.name.startswith("."):
                continue
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = _format_size(item.stat().st_size)
                items.append(f"📄 {item.name} ({size})")

        if not items:
            return f"Directory is empty: {target.name}/"

        return f"Contents of {target.name}/ ({len(items)} items):\n" + "\n".join(items)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Error listing files: {e}"


def create_file(path: str, name: str = "", content: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Nao tenho acesso a esse lugar: {target}"
        # "SALVA ESSE BLOCO DE NOTAS" (05/09/2026). Nesse pedido o conteudo nao
        # esta na conversa: esta na TELA, escrito pelo proprio JARVIS minutos
        # antes. O modelo manda so o caminho, e sem esta ponte o arquivo nascia
        # VAZIO — o que e pior do que falhar, porque parece que deu certo.
        if not str(content or "").strip() and target.suffix.lower() in (
                ".txt", ".md", ".log", ""):
            try:
                from actions import digitar_na_tela as _dig  # noqa: PLC0415
                escrito = str(getattr(_dig, "ULTIMO_TEXTO", "") or "")
                if escrito.strip():
                    content = escrito
                    print(f"[file] salvando o texto que eu escrevi ({len(content)} letras)")
            except Exception:
                pass
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if not str(content or "").strip():
            return (f"Criei {target.name}, mas VAZIO: nao me passaram conteudo "
                    f"nenhum e nao havia texto meu para salvar.")
        return f"Salvei em {target}, com {len(content)} letras."
    except Exception as e:
        return f"Could not create file: {e}"


def create_folder(path: str, name: str = "") -> str:
    try:
        base = _resolve_path(path)

        # SEM NOME NAO SE CRIA NADA (corrigido em 30/08/2026).
        #
        # Antes, com `name` vazio o alvo virava a PROPRIA pasta base — que ja
        # existe — e o `mkdir(exist_ok=True)` passava limpo, devolvendo
        # "Folder created". O JARVIS entao dizia ao cliente que tinha criado a
        # pasta, e nao havia pasta nenhuma. Mentira com cara de sucesso.
        nome = str(name or "").strip().strip('"').strip("'")
        if not nome:
            # MAS o caminho INTEIRO em `path` nao e esse defeito (08/09/2026).
            # O modelo tambem manda `path=C:\...\Desktop\Cotacao do Dolar` e
            # `name` vazio. Ali em cima a pasta JA EXISTIA e o "criei" era
            # mentira; aqui ela NAO existe, entao a ponta do caminho E o nome
            # que o cliente falou. Recusar isso derrubou a tarefa inteira com o
            # nome da pasta escrito na frente do JARVIS.
            if base.parent != base and not base.exists():
                base, nome = base.parent, base.name
            else:
                return ("Nao criei nada: faltou o nome da pasta. "
                        "Pergunte ao cliente como ele quer chamar a pasta.")

        target = base / nome
        if not _is_safe_path(target):
            return f"Nao tenho acesso a esse lugar: {target}"
        if target.exists():
            return f"Ja existia uma pasta chamada {target.name} em {base}."

        target.mkdir(parents=True, exist_ok=False)

        # Confirma no disco. Dizer que criou sem conferir foi exatamente o
        # defeito que este bloco existe para impedir.
        if not target.is_dir():
            return f"Tentei criar {nome} mas ela nao apareceu em {base}."
        return f"Pasta criada: {target}"
    except Exception as e:
        return f"Nao consegui criar a pasta: {e}"


def _is_confirmed(value) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _normalize_shortcut(str(value or ""))
    return normalized in {
        "yes", "true", "1", "confirm", "confirmed",
        "sim", "s", "confirmar", "confirmado",
        "pode excluir", "pode deletar", "excluir", "deletar",
    }


def _is_drive_root(path: Path) -> bool:
    text = str(path).replace("\\", "/").rstrip("/")
    return _OS == "Windows" and len(text) == 2 and text[1] == ":"


def _is_protected_delete_target(target: Path) -> bool:
    """Bloqueia exclusão de pastas raiz/principais, mas permite itens comuns em C:/D:/E:/USB."""
    try:
        resolved = target.resolve()
    except Exception:
        resolved = target

    protected = {
        _get_desktop(), _get_downloads(), _get_documents(),
        _get_pictures(), _get_music(), _get_videos(), Path.home()
    }
    try:
        if resolved in {p.resolve() for p in protected}:
            return True
    except Exception:
        pass

    if _OS == "Windows":
        text = str(resolved).replace("\\", "/").lower().rstrip("/")
        if _is_drive_root(resolved):
            return True
        blocked_exact = {
            "c:/windows", "c:/program files", "c:/program files (x86)",
            "c:/programdata", "c:/users", "c:/$recycle.bin",
            "c:/system volume information",
        }
        if text in blocked_exact:
            return True
    else:
        if str(resolved) in {"/", str(Path.home().parent)}:
            return True
    return False


def _delete_location_label(path: Path) -> str:
    """Retorna uma indicação curta do local sem despejar caminhos enormes no Gemini Live."""
    try:
        parent = Path(path).parent
        desktop = _get_desktop().resolve()
        if parent.resolve() == desktop:
            return "Área de Trabalho"
    except Exception:
        pass
    try:
        drive = Path(path).anchor.replace("\\", "") or str(Path(path).parent)
        return drive or "local encontrado"
    except Exception:
        return "local encontrado"


def delete_file(path: str, name: str = "", confirmed=False, max_seconds: int = 30, player=None) -> str:
    try:
        raw_path = str(path or "computer").strip()
        raw_name = str(name or "").strip().strip('"').strip("'")

        target = _resolve_target_for_file_action(
            raw_path, raw_name, max_seconds=max_seconds, player=player
        )

        if not target:
            return (
                f"Chefe, não encontrei '{raw_name or raw_path}' para excluir. "
                "Se souber o disco ou a pasta aproximada, me diga o local."
            )

        target = Path(target)
        if not target.exists():
            return f"Chefe, não encontrei para excluir: {target.name}"

        if _is_protected_delete_target(target):
            return f"Chefe, por segurança não posso excluir essa pasta principal do sistema: {target.name}"

        kind = "pasta" if target.is_dir() else "arquivo"
        article = "a" if target.is_dir() else "o"
        if not _is_confirmed(confirmed):
            _notify_task(player, "CONFIRMANDO", f"[file] confirmação de exclusão: {target}")
            location = _delete_location_label(target)
            return (
                f"Chefe, encontrei {article} {kind} '{target.name}' em {location}. "
                f"Confirma que posso enviar para a Lixeira?"
            )

        _notify_task(player, "EXCLUINDO", f"[file] enviando para a Lixeira: {target}")
        trash_result = _safe_trash(target)
        if trash_result.lower().startswith("movido para"):
            return f"Excluído com sucesso: {target.name}. Enviei para a Lixeira."
        return trash_result

    except PermissionError:
        return f"Chefe, o Windows negou permissão para excluir: {name or path}"
    except Exception as e:
        return f"Não consegui excluir: {e}"


def _resolve_transfer_destination(source: Path, destination: str) -> tuple[Path | None, str | None]:
    """Resolve destino de copiar/mover sem confundir pasta nova com novo nome."""
    raw_destination = str(destination or "").strip().strip('"').strip("'")
    if not raw_destination:
        return None, "Nenhum destino foi informado."

    # Quando o modelo recebe "para a subpasta destino", ele normalmente envia
    # apenas "destino". Um caminho relativo assim pertence à pasta da origem,
    # não ao diretório de execução do aplicativo. Atalhos conhecidos e caminhos
    # absolutos continuam sendo resolvidos normalmente.
    expanded_destination = Path(os.path.expandvars(raw_destination)).expanduser()
    first_part = raw_destination.replace("\\", "/").split("/", 1)[0]
    explicit_location = (
        expanded_destination.is_absolute()
        or (_OS == "Windows" and len(raw_destination) >= 2 and raw_destination[1] == ":")
        or raw_destination.startswith(("~", "%"))
        or _looks_like_known_location(first_part)
    )
    destination_path = (
        _resolve_path(raw_destination)
        if explicit_location
        else source.parent / expanded_destination
    )
    if not _is_safe_path(destination_path):
        return None, f"Acesso negado ao destino: {destination_path}"

    if destination_path.exists():
        if destination_path.is_dir():
            return destination_path / source.name, None
        return destination_path, None

    # Move/copy usam destination como pasta por padrão. Para trocar o nome existe
    # a ação rename; só tratamos como caminho de arquivo quando há uma extensão.
    looks_like_folder = (
        raw_destination.endswith(("/", "\\"))
        or not Path(raw_destination).suffix
    )
    if looks_like_folder:
        destination_path.mkdir(parents=True, exist_ok=True)
        return destination_path / source.name, None

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    return destination_path, None


def move_file(path: str, name: str = "", destination: str = "",
              max_seconds: int = 18, player=None) -> str:
    try:
        src = _resolve_target_for_file_action(
            path, name, max_seconds=max_seconds, player=player
        )
        if not src:
            return f"Não encontrei '{name or path}' para mover. Informe o local aproximado se souber."
        src = Path(src)
        if not _is_safe_path(src):
            return f"Acesso negado à origem: {src}"

        dst, error = _resolve_transfer_destination(src, destination)
        if error or dst is None:
            return error or "Destino inválido."
        if dst.exists():
            return f"O destino já contém '{dst.name}'. Não substituí o item existente."

        dst.parent.mkdir(parents=True, exist_ok=True)
        _notify_task(player, "MOVENDO", f"[file] Movendo {src.name} para {dst.parent}")
        shutil.move(str(src), str(dst))
        return f"Movido com sucesso: {src.name} → {dst.parent}"

    except Exception as e:
        return f"Não consegui mover: {e}"


def copy_file(path: str, name: str = "", destination: str = "",
              max_seconds: int = 18, player=None) -> str:
    try:
        src = _resolve_target_for_file_action(
            path, name, max_seconds=max_seconds, player=player
        )
        if not src:
            return f"Não encontrei '{name or path}' para copiar. Informe o local aproximado se souber."
        src = Path(src)
        if not _is_safe_path(src):
            return f"Acesso negado à origem: {src}"

        dst, error = _resolve_transfer_destination(src, destination)
        if error or dst is None:
            return error or "Destino inválido."
        if dst.exists():
            return f"O destino já contém '{dst.name}'. Não substituí o item existente."

        dst.parent.mkdir(parents=True, exist_ok=True)
        _notify_task(player, "COPIANDO", f"[file] Copiando {src.name} para {dst.parent}")

        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))

        return f"Copiado com sucesso: {src.name} → {dst.parent}"

    except Exception as e:
        return f"Não consegui copiar: {e}"


def rename_file(path: str, name: str = "", new_name: str = "") -> str:
    try:
        base     = _resolve_path(path)
        target   = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Nao tenho acesso a esse lugar: {target}"
        if not target.exists():
            return f"Not found: {target.name}"
        if not new_name:
            return "No new name provided."

        new_path = target.parent / new_name
        if new_path.exists():
            return f"A file named '{new_name}' already exists here."

        target.rename(new_path)
        return f"Renamed: {target.name} → {new_name}"

    except Exception as e:
        return f"Could not rename: {e}"


def read_file(path: str, name: str = "", max_chars: int = 4000) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Nao tenho acesso a esse lugar: {target}"
        if not target.exists():
            return f"File not found: {target.name}"
        if not target.is_file():
            return f"Not a file: {target.name}"

        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[Truncated — {len(content)} total chars]"
        return content

    except Exception as e:
        return f"Could not read file: {e}"


def write_file(path: str, name: str = "", content: str = "",
               append: bool = False) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Nao tenho acesso a esse lugar: {target}"
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(content)
        action = "Appended to" if append else "Written to"
        return f"{action}: {target.name}"
    except Exception as e:
        return f"Could not write file: {e}"


def _iter_search_children(root: Path):
    """Compatibilidade: varredura em largura para uma raiz direcionada."""
    yield from _iter_search_entries_breadth_first([root], broad=False)


def _format_found_item(path: Path) -> str:
    try:
        if path.is_dir():
            return f"📁 {path.name}/ — {path.parent}"
        size = _format_size(path.stat().st_size)
        return f"📄 {path.name} ({size}) — {path.parent}"
    except Exception:
        return f"📄 {path.name} — {path.parent}"


def find_files(name: str = "", extension: str = "",
               path: str = "computer", max_results: int = 10,
               max_seconds: int = 20, player=None) -> str:
    """Busca arquivos e pastas.

    - Sem local informado: faz busca ampla rápida em discos visíveis.
    - Com local informado: busca somente no local pedido.
    - O nome é comparado de forma flexível: sem acento, sem depender de hífen,
      underline ou espaços exatos, e ignorando palavras soltas como "pasta".
    """
    try:
        raw_path = str(path or "computer").strip()
        query_name = str(name or "").strip().strip('"').strip("'")
        ext = str(extension or "").strip().lower()
        if ext and not ext.startswith("."):
            ext = "." + ext

        if not query_name and not ext:
            return "Chefe, me diga o nome do arquivo, pasta ou extensão que você quer procurar."

        broad = _is_broad_search_path(raw_path)
        if broad:
            roots = _broad_search_roots()
            scope_label = "todo o computador"
        else:
            search_path = _resolve_path(raw_path)
            if not search_path.exists():
                return f"Local de busca não encontrado: {raw_path}"
            # Busca direcionada pode ser feita fora da home porque é somente leitura.
            roots = [search_path]
            scope_label = str(search_path)

        roots = _dedupe_paths(roots)
        if not roots:
            return "Chefe, não encontrei nenhum disco ou pasta acessível para procurar."

        max_results = max(1, min(int(max_results or 20), 100))
        requested_seconds = int(max_seconds or (20 if broad else 30))
        if broad:
            # Busca ampla precisa ser útil, não infinita. O Jarvis responde em até
            # ~45s, em vez de ficar vários minutos varrendo cache/node_modules.
            max_seconds = max(8, min(requested_seconds, 30))
        else:
            max_seconds = max(8, min(requested_seconds, 90))

        started = time.monotonic()
        results: list[str] = []
        seen_found_paths: set[str] = set()
        scanned_dirs = 0
        scanned_items = 0
        query = query_name or ext

        _notify_task(player, "PROCURANDO", f"[file] Procurando {query} em {scope_label}...")

        # 1) No Windows, tenta primeiro a busca nativa rápida. Ela é bem melhor
        # para D:, E: e pendrives e não fica escrevendo cada pasta no chat.
        if broad and _OS == "Windows":
            fast_budget = min(14.0, max_seconds * 0.60)
            fast_found = _find_files_windows_dir_fast(
                query_name=query_name,
                ext=ext,
                roots=roots,
                max_results=max_results,
                seconds_budget=fast_budget,
                player=player,
            )
            for item_path in fast_found:
                key = _path_key(item_path)
                if key in seen_found_paths:
                    continue
                seen_found_paths.add(key)
                results.append(_format_found_item(item_path))
                if len(results) >= max_results:
                    break
            if results:
                elapsed = time.monotonic() - started
                searched_in = ", ".join(str(r) for r in roots)
                header = (
                    f"Encontrei {len(results)} resultado(s) para '{query}' em {scope_label}."
                    f"\nLocais verificados: {searched_in}"
                    f"\nTempo: {elapsed:.1f}s"
                )
                if len(results) >= max_results:
                    header += f"\nMostrando os primeiros {max_results} resultados."
                return header + "\n" + "\n".join(results)

        # 2) Fallback Python com poda de pastas técnicas. Não lista cada pasta;
        # manda só progresso resumido para a interface/log.
        remaining_seconds = max_seconds - (time.monotonic() - started)
        if remaining_seconds <= 1:
            remaining_seconds = 1

        # Testa as próprias raízes antes dos filhos.
        for root in roots:
            if time.monotonic() - started >= max_seconds:
                break
            try:
                if not root.exists():
                    continue
                is_file = root.is_file()
                if _matches_search_query(root.name, query_name, ext, is_file):
                    key = _path_key(root)
                    if key not in seen_found_paths:
                        seen_found_paths.add(key)
                        results.append(_format_found_item(root))
                        if len(results) >= max_results:
                            break
            except (OSError, PermissionError):
                continue

        iterator = _iter_search_entries_breadth_first(roots, broad=broad)
        last_status = 0.0
        for entry in iterator:
            elapsed_now = time.monotonic() - started
            if elapsed_now >= max_seconds:
                break

            scanned_items += 1
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                if is_dir:
                    scanned_dirs += 1
                is_file = entry.is_file(follow_symlinks=False)
                if not is_dir and not is_file:
                    continue

                if broad and is_dir and _should_skip_broad_dir(Path(entry.path)):
                    continue

                if elapsed_now - last_status >= 5.0:
                    last_status = elapsed_now
                    _notify_task(
                        player,
                        "PROCURANDO",
                        f"[file] Procurando... {int(elapsed_now)}s | {scanned_items} itens verificados",
                    )

                if not _matches_search_query(entry.name, query_name, ext, is_file):
                    continue

                item_path = Path(entry.path)
                key = _path_key(item_path)
                if key in seen_found_paths:
                    continue
                seen_found_paths.add(key)
                results.append(_format_found_item(item_path))
                if len(results) >= max_results:
                    break
            except (OSError, PermissionError):
                continue

        elapsed = time.monotonic() - started
        searched_in = ", ".join(str(r) for r in roots)

        if not results:
            extra = ""
            if elapsed >= max_seconds:
                extra = f" A busca parou no limite de {max_seconds}s para não travar o sistema."
            if broad:
                extra += " Se você souber o disco ou a pasta aproximada, me diga, que eu procuro direto nesse local."
            return (
                f"Chefe, procurei por '{query}' em {scope_label}, mas não encontrei."
                f"\nLocais verificados: {searched_in}"
                f"\nItens verificados: {scanned_items}. Pastas verificadas: {scanned_dirs}."
                f"{extra}"
            )

        header = (
            f"Encontrei {len(results)} resultado(s) para '{query}' em {scope_label}."
            f"\nLocais verificados: {searched_in}"
            f"\nTempo: {elapsed:.1f}s | Itens verificados: {scanned_items} | Pastas: {scanned_dirs}"
        )
        if len(results) >= max_results:
            header += f"\nMostrando os primeiros {max_results} resultados."
        elif elapsed >= max_seconds:
            header += f"\nA busca parou no limite de {max_seconds}s para não travar o sistema."
        return header + "\n" + "\n".join(results)

    except Exception as e:
        return f"Erro na busca: {e}"


def _unique_zip_path(candidate: Path) -> Path:
    """Evita sobrescrever um ZIP já existente no mesmo local."""
    candidate = Path(candidate)
    if candidate.suffix.lower() != ".zip":
        candidate = candidate.with_suffix(candidate.suffix + ".zip") if candidate.suffix else candidate.with_suffix(".zip")
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    parent = candidate.parent
    for index in range(1, 1000):
        option = parent / f"{stem} ({index}){suffix}"
        if not option.exists():
            return option
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return parent / f"{stem}-{stamp}{suffix}"


def _is_protected_compress_target(target: Path) -> bool:
    """Bloqueia compactação acidental de raízes enormes/principais."""
    try:
        resolved = target.resolve()
    except Exception:
        resolved = target

    protected = {
        _get_desktop(), _get_downloads(), _get_documents(),
        _get_pictures(), _get_music(), _get_videos(), Path.home()
    }
    try:
        if resolved in {p.resolve() for p in protected}:
            return True
    except Exception:
        pass

    if _OS == "Windows":
        text = str(target).replace("\\", "/")
        # C:/, D:/, E:/ etc.
        if len(text) <= 3 and len(text) >= 2 and text[1] == ":":
            return True
    else:
        if str(target) in {"/", str(Path.home().parent)}:
            return True
    return False


def _resolve_target_for_file_action(path: str, name: str = "", max_seconds: int = 35, player=None) -> Path | None:
    """Resolve um arquivo/pasta existente para ações como abrir e compactar."""
    raw_path = str(path or "computer").strip()
    raw_name = str(name or "").strip().strip('"').strip("'")
    broad = _is_broad_search_path(raw_path)

    if broad:
        if not raw_name:
            return None
        return _find_first_path_for_open(raw_name, path="computer", max_seconds=max_seconds, player=player)

    try:
        base = _resolve_path(raw_path)
    except Exception:
        base = Path(raw_path).expanduser()

    target = (base / raw_name) if raw_name else base
    try:
        if target.exists():
            return target
    except OSError:
        pass

    if raw_name:
        # Busca tolerante dentro do local informado: resolve acentos, espaços e letras diferentes.
        return _find_first_path_for_open(raw_name, path=raw_path, max_seconds=min(max_seconds, 30), player=player)

    # Caso o modelo coloque o nome do item em path em vez de name.
    if raw_path and not _looks_like_known_location(raw_path):
        return _find_first_path_for_open(raw_path, path="computer", max_seconds=max_seconds, player=player)

    return None


def _zip_directory(source: Path, output_zip: Path, player=None) -> int:
    count = 0
    last_status = time.monotonic()
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        # Inclui a pasta raiz no ZIP, inclusive diretórios vazios.
        for current, dirs, files in os.walk(source):
            current_path = Path(current)
            dirs[:] = [d for d in dirs if not Path(current_path / d).is_symlink()]

            if not dirs and not files:
                arc_dir = Path(source.name) / current_path.relative_to(source)
                zf.writestr(str(arc_dir).replace("\\", "/").rstrip("/") + "/", "")
                count += 1

            for file_name in files:
                item = current_path / file_name
                try:
                    if item.is_symlink() or item.resolve() == output_zip.resolve():
                        continue
                except Exception:
                    continue
                arcname = Path(source.name) / item.relative_to(source)
                zf.write(item, str(arcname).replace("\\", "/"))
                count += 1
                now = time.monotonic()
                if now - last_status >= 5:
                    last_status = now
                    _notify_task(player, "COMPACTANDO", f"[file] Compactando... {count} item(ns)")
    return count


def compress_item(path: str = "computer", name: str = "", output_name: str = "",
                  max_seconds: int = 18, player=None) -> str:
    """Compacta arquivo ou pasta em ZIP no mesmo local do item original."""
    raw_path = str(path or "computer").strip()
    raw_name = str(name or "").strip().strip('"').strip("'")

    try:
        target = _resolve_target_for_file_action(raw_path, raw_name, max_seconds=max_seconds, player=player)
        if not target:
            return (
                f"Chefe, não encontrei '{raw_name or raw_path}' para compactar. "
                "Se souber o local, me diga a pasta ou o disco exato."
            )

        target = Path(target)
        if not target.exists():
            return f"Chefe, não encontrei para compactar: {target}"
        if _is_protected_compress_target(target):
            return f"Chefe, por segurança não vou compactar essa pasta principal inteira: {target}"

        parent = target.parent
        desired_name = str(output_name or target.name).strip().strip('"').strip("'") or target.name
        if desired_name.lower().endswith(".zip"):
            output_zip = parent / desired_name
        else:
            output_zip = parent / f"{desired_name}.zip"
        output_zip = _unique_zip_path(output_zip)

        _notify_task(player, "COMPACTANDO", f"[file] Compactando {target.name}...")

        if target.is_dir():
            item_count = _zip_directory(target, output_zip, player=player)
        else:
            with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                zf.write(target, target.name)
            item_count = 1

        size_text = _format_size(output_zip.stat().st_size)
        return (
            f"Compactado com sucesso: {output_zip.name}\n"
            f"Local: {output_zip.parent}\n"
            f"Itens compactados: {item_count}\n"
            f"Tamanho do ZIP: {size_text}"
        )

    except PermissionError:
        return f"Chefe, o Windows negou permissão para compactar: {raw_name or raw_path}"
    except Exception as e:
        return f"Não consegui compactar: {e}"

def get_largest_files(path: str = "downloads", count: int = 10) -> str:
    count = min(count, 50)  # maksimum 50
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Path not found: {path}"

        files = []
        for item in search_path.rglob("*"):
            if item.is_file():
                try:
                    files.append((item.stat().st_size, item))
                except Exception:
                    continue

        files.sort(reverse=True)
        top = files[:count]

        if not top:
            return "No files found."

        lines = [f"Top {len(top)} largest files in {search_path.name}/:"]
        for size, f in top:
            lines.append(f"  {_format_size(size):>10}  {f.name}  ({f.parent})")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


def get_disk_usage(path: str = "home") -> str:
    try:
        target = _resolve_path(path)
        usage  = shutil.disk_usage(target)
        pct    = usage.used / usage.total * 100
        return (
            f"Disk usage ({target}):\n"
            f"  Total : {_format_size(usage.total)}\n"
            f"  Used  : {_format_size(usage.used)} ({pct:.1f}%)\n"
            f"  Free  : {_format_size(usage.free)}"
        )
    except Exception as e:
        return f"Could not get disk usage: {e}"


def organize_desktop() -> str:
    type_map = {
        "Images":    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
        "Documents": {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                      ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
        "Videos":    {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
        "Music":     {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
        "Archives":  {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
        "Code":      {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                      ".cpp", ".java", ".cs", ".go", ".rs", ".sh"},
    }

    desktop = _get_desktop()
    moved, skipped = [], []

    try:
        for item in desktop.iterdir():
            # Klasörlere, gizli dosyalara ve organize klasörlerine dokunma
            if item.is_dir() or item.name.startswith("."):
                continue
            if item.name in {k for k in type_map}:
                continue

            ext        = item.suffix.lower()
            target_dir = desktop / "Others"
            for folder, exts in type_map.items():
                if ext in exts:
                    target_dir = desktop / folder
                    break

            target_dir.mkdir(exist_ok=True)
            new_path = target_dir / item.name

            if new_path.exists():
                skipped.append(item.name)
                continue

            shutil.move(str(item), str(new_path))
            moved.append(f"{item.name} → {target_dir.name}/")

        result = f"Desktop organized: {len(moved)} files moved."
        if moved:
            preview = moved[:8]
            result += "\n" + "\n".join(preview)
            if len(moved) > 8:
                result += f"\n... and {len(moved) - 8} more."
        if skipped:
            result += f"\n{len(skipped)} file(s) skipped (name conflict)."
        return result

    except Exception as e:
        return f"Could not organize desktop: {e}"


def get_file_info(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return f"Nao tenho acesso a esse lugar: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        stat = target.stat()
        info = {
            "Name":      target.name,
            "Type":      "Folder" if target.is_dir() else "File",
            "Size":      _format_size(stat.st_size),
            "Location":  str(target.parent),
            "Created":   datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            "Modified":  datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "Extension": target.suffix or "—",
        }
        return "\n".join(f"  {k}: {v}" for k, v in info.items())

    except Exception as e:
        return f"Could not get file info: {e}"

def file_controller(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "").lower().strip().replace(" ", "_").replace("-", "_")
    action_aliases = {
        "zip": "compress",
        "zipar": "compress",
        "archive": "compress",
        "compact": "compress",
        "compactar": "compress",
        "compress_file": "compress",
        "compress_folder": "compress",
        "compactar_arquivo": "compress",
        "compactar_pasta": "compress",
        # "SALVA ESSE BLOCO DE NOTAS" (05/09/2026). O modelo chamou a acao
        # `save`, que nao existia, e a resposta foi "essa acao nao foi
        # reconhecida" — com o caminho certo e o texto pronto do lado de fora.
        # Salvar E criar um arquivo com conteudo; o nome que ele deu era o
        # unico problema.
        "save": "create_file",
        "salvar": "create_file",
        "salva": "create_file",
        "save_file": "create_file",
        "save_as": "create_file",
        "gravar": "create_file",
        "escrever_arquivo": "create_file",
        "criar_arquivo": "create_file",
    }
    action = action_aliases.get(action, action)
    raw_path = params.get("path", None)
    name   = params.get("name", "")
    path   = raw_path if raw_path not in (None, "") else (
        "computer" if action in {"find", "open", "compress"} or (action in {"delete", "move", "copy"} and name) else "desktop"
    )

    if player:
        if action == "find":
            player.write_log(f"[file] procurando {name or params.get('extension', '') or path}")
        else:
            player.write_log(f"[file] {action} {name or path}")

    try:
        if action == "list":
            return list_files(path)

        elif action == "create_file":
            return create_file(path, name=name, content=params.get("content", ""))

        elif action == "create_folder":
            return create_folder(path, name=name)

        elif action == "open":
            return open_item(
                path=path,
                name=name,
                max_seconds=int(params.get("max_seconds", 35)),
                player=player,
            )

        elif action == "compress":
            return compress_item(
                path=path,
                name=name,
                output_name=params.get("output_name", ""),
                max_seconds=int(params.get("max_seconds", 18)),
                player=player,
            )

        elif action == "delete":
            return delete_file(
                path,
                name=name,
                confirmed=params.get("confirmed", False),
                max_seconds=int(params.get("max_seconds", 30)),
                player=player,
            )

        elif action == "move":
            return move_file(
                path,
                name=name,
                destination=params.get("destination", ""),
                max_seconds=int(params.get("max_seconds", 18)),
                player=player,
            )

        elif action == "copy":
            return copy_file(
                path,
                name=name,
                destination=params.get("destination", ""),
                max_seconds=int(params.get("max_seconds", 18)),
                player=player,
            )

        elif action == "rename":
            return rename_file(path, name=name, new_name=params.get("new_name", ""))

        elif action == "read":
            return read_file(path, name=name)

        elif action == "write":
            return write_file(
                path, name=name,
                content=params.get("content", ""),
                append=params.get("append", False)
            )

        elif action == "find":
            return find_files(
                name=name or params.get("name", ""),
                extension=params.get("extension", ""),
                path=path,
                max_results=min(int(params.get("max_results", 10)), 100),
                max_seconds=int(params.get("max_seconds", 20)),
                player=player,
            )

        elif action == "largest":
            return get_largest_files(
                path=path,
                count=int(params.get("count", 10)),
            )

        elif action == "disk_usage":
            return get_disk_usage(path)

        elif action == "organize_desktop":
            return organize_desktop()

        elif action == "info":
            return get_file_info(path, name=name)

        else:
            return f"Unknown action: '{action}'"

    except Exception as e:
        return f"File controller error ({action}): {e}"
