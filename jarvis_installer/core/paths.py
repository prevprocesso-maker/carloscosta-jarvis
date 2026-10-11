from __future__ import annotations

import os
import sys
import platform
from pathlib import Path
from dataclasses import dataclass


@dataclass
class JARVISPaths:
    """Paths padrão do JARVIS por SO."""
    base: Path
    venv: Path
    frontend: Path
    frontend_dist: Path
    frontend_backup: Path
    config: Path
    config_file: Path
    data: Path
    memory: Path
    whatsapp: Path
    logs: Path
    bin: Path
    updates: Path
    updates_pending: Path
    updates_applied: Path
    updates_rollback: Path
    log_file: Path
    installer_log: Path


def get_jarvis_paths(base_dir: Optional[Path] = None) -> JARVISPaths:
    """Retorna paths padrão do JARVIS para o SO atual."""
    if base_dir is None:
        base_dir = get_default_base_dir()
    
    base = Path(base_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    
    return JARVISPaths(
        base=base,
        venv=base / "venv",
        frontend=base / "frontend",
        frontend_dist=base / "frontend" / "dist",
        frontend_backup=base / "frontend" / "dist.backup",
        config=base / "config",
        config_file=base / "config" / "config.yaml",
        data=base / "data",
        memory=base / "data" / "memory",
        whatsapp=base / "data" / "whatsapp",
        logs=base / "logs",
        bin=base / "bin",
        updates=base / "updates",
        updates_pending=base / "updates" / "pending",
        updates_applied=base / "updates" / "applied",
        updates_rollback=base / "updates" / "rollback",
        log_file=base / "logs" / "jarvis.log",
        installer_log=base / "logs" / "installer.log",
    )


def get_default_base_dir() -> Path:
    """Retorna diretório base padrão por SO."""
    system = platform.system().lower()
    
    if system == "windows":
        # %APPDATA%\JARVIS
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        return Path(appdata) / "JARVIS"
    
    elif system == "darwin":
        # ~/Library/Application Support/JARVIS
        return Path.home() / "Library" / "Application Support" / "JARVIS"
    
    else:
        # Linux: XDG_DATA_HOME ou ~/.local/share
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / "jarvis"
        return Path.home() / ".local" / "share" / "jarvis"


def get_python_executable(venv_path: Path) -> Path:
    """Retorna path do python do venv."""
    if platform.system().lower() == "windows":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def get_pip_executable(venv_path: Path) -> Path:
    """Retorna path do pip do venv."""
    if platform.system().lower() == "windows":
        return venv_path / "Scripts" / "pip.exe"
    return venv_path / "bin" / "pip"


def ensure_dirs(paths: JARVISPaths) -> None:
    """Cria todos os diretórios necessários."""
    for attr in dir(paths):
        if not attr.startswith("_"):
            path = getattr(paths, attr)
            if isinstance(path, Path) and not path.suffix:
                path.mkdir(parents=True, exist_ok=True)


def get_installer_path() -> Path:
    """Retorna path do instalador (onde está o jarvis_installer)."""
    return Path(__file__).resolve().parent.parent


def get_templates_dir() -> Path:
    """Retorna diretório de templates."""
    return get_installer_path() / "templates"


def get_scripts_dir() -> Path:
    """Retorna diretório de scripts."""
    return get_installer_path() / "scripts"