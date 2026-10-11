from __future__ import annotations

import subprocess
import sys
import os
import platform
import venv
import shutil
from pathlib import Path
from typing import List, Optional
import time

from ..paths import get_jarvis_paths, get_python_executable, get_pip_executable
from ..core import SystemInfo, detect_system


def create_venv(venv_path: Path, python: str = "python") -> bool:
    """Cria virtual environment."""
    try:
        if venv_path.exists():
            shutil.rmtree(venv_path)
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Use system python to create venv
        result = subprocess.run([python, "-m", "venv", str(venv_path)], 
                              capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"[Venv] Failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"[Venv] Error: {e}")
        return False


def upgrade_pip(venv_path: Path) -> bool:
    """Atualiza pip no venv."""
    pip = get_pip_executable(Path(venv_path))
    try:
        result = subprocess.run([str(pip), "install", "--upgrade", "pip", "setuptools", "wheel"],
                              capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except Exception:
        return False


def install_python_deps(venv_path: Path, requirements: List[str] = None, editable: bool = False) -> bool:
    """Instala dependências Python no venv."""
    pip = get_pip_executable(Path(venv_path))
    
    if requirements is None:
        # Default requirements
        requirements = [
            "jarvis @ file:///path/to/jarvis",  # Will be replaced
        ]
    
    cmd = [str(pip), "install", "--no-cache-dir"]
    if editable:
        cmd.append("-e")
    cmd.extend(requirements)
    
    try:
        print(f"[Deps] Installing: {' '.join(requirements[:3])}...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"[Deps] Failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"[Deps] Error: {e}")
        return False


def install_jarvis_package(venv_path: Path, source_path: Path, editable: bool = True) -> bool:
    """Instala pacote JARVIS do source."""
    pip = get_pip_executable(Path(venv_path))
    source = Path(source_path).resolve()
    
    if not (source / "pyproject.toml").exists():
        print(f"[Jarvis] pyproject.toml not found at {source}")
        return False
    
    cmd = [str(pip), "install", "--no-cache-dir"]
    if editable:
        cmd.append("-e")
    cmd.append(str(source))
    
    try:
        print(f"[Jarvis] Installing from {source}...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            print(f"[Jarvis] Failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"[Jarvis] Error: {e}")
        return False


def verify_installation(venv_path: Path) -> bool:
    """Verifica se instalação funcionou."""
    python = get_python_executable(Path(venv_path))
    try:
        result = subprocess.run([
            str(python), "-c", 
            "import jarvis; from jarvis.core import detect_system; print('OK')"
        ], capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception:
        return False


def get_site_packages(venv_path: Path) -> Path:
    """Retorna path do site-packages do venv."""
    if platform.system().lower() == "windows":
        return Path(venv_path) / "Lib" / "site-packages"
    # Linux/macOS
    import sys
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return Path(venv_path) / "lib" / version / "site-packages"