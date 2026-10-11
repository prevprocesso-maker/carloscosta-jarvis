from __future__ import annotations

import subprocess
import platform
import shutil
from pathlib import Path
from typing import Optional, List


def check_node_version(min_version: str = "18") -> bool:
    """Verifica versão do Node.js."""
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version = result.stdout.strip().lstrip("v")
            major = int(version.split(".")[0])
            return major >= int(min_version)
    except Exception:
        pass
    return False


def check_npm_available() -> bool:
    """Verifica se npm está disponível."""
    try:
        result = subprocess.run(["npm", "--version"], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def install_node_deps(frontend_path: Path, production: bool = False) -> bool:
    """Instala dependências npm."""
    if not frontend_path.exists():
        print(f"[Node] Frontend path not found: {frontend_path}")
        return False
    
    package_json = frontend_path / "package.json"
    if not package_json.exists():
        print(f"[Node] package.json not found")
        return False
    
    cmd = ["npm", "ci"] if production else ["npm", "install"]
    
    try:
        print(f"[Node] Installing dependencies in {frontend_path}...")
        result = subprocess.run(cmd, cwd=frontend_path, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"[Node] Failed: {result.stderr}")
            return False
        print("[Node] Dependencies installed")
        return True
    except Exception as e:
        print(f"[Node] Error: {e}")
        return False


def build_frontend(frontend_path: Path, output_path: Path = None) -> bool:
    """Build do frontend (Vite)."""
    if output_path is None:
        output_path = frontend_path / "dist"
    
    if not frontend_path.exists():
        print(f"[Build] Frontend path not found")
        return False
    
    try:
        print(f"[Build] Building frontend...")
        result = subprocess.run(["npm", "run", "build"], cwd=frontend_path, 
                              capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"[Build] Failed: {result.stderr}")
            return False
        
        # Verify output
        index = output_path / "index.html"
        if index.exists():
            size = index.stat().st_size
            print(f"[Build] Success: {output_path} ({index.stat().st_size} bytes)")
            return True
        print("[Build] Output not found")
        return False
    except Exception as e:
        print(f"[Build] Error: {e}")
        return False


def backup_frontend_build(frontend_path: Path) -> bool:
    """Backup do build atual para rollback."""
    dist = frontend_path / "dist"
    backup = frontend_path / "dist.backup"
    
    if dist.exists():
        if backup.exists():
            shutil.rmtree(backup)
        shutil.copytree(dist, backup)
        print(f"[Backup] Frontend backed up to {backup}")
        return True
    return True  # No build to backup


def restore_frontend_build(frontend_path: Path) -> bool:
    """Restaura build do backup."""
    dist = frontend_path / "dist"
    backup = frontend_path / "dist.backup"
    
    if backup.exists():
        if dist.exists():
            shutil.rmtree(dist)
        shutil.copytree(backup, dist)
        print(f"[Rollback] Frontend restored from backup")
        return True
    print("[Rollback] No backup found")
    return False


def get_node_info() -> dict:
    """Retorna info do Node.js."""
    info = {}
    try:
        info["node"] = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        info["node"] = None
    try:
        info["npm"] = subprocess.run(["npm", "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        info["npm"] = None
    try:
        info["npx"] = subprocess.run(["npx", "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        info["npx"] = None
    return info


def ensure_node_available(min_version: str = "18") -> bool:
    """Garante que Node.js está disponível, instala se necessário (Linux)."""
    if check_node_version(min_version) and check_npm_available():
        return True
    
    system = platform.system().lower()
    if system == "linux":
        # Try package manager
        try:
            import subprocess
            subprocess.run(["apt", "update"], capture_output=True)
            subprocess.run(["apt", "install", "-y", "nodejs", "npm"], timeout=300)
            return check_node_version(min_version)
        except Exception:
            pass
    elif system == "darwin":
        # Try brew
        try:
            subprocess.run(["brew", "install", "node"], timeout=300)
            return check_node_version(min_version)
        except Exception:
            pass
    elif system == "windows":
        # Try winget or choco
        try:
            subprocess.run(["winget", "install", "OpenJS.NodeJS"], timeout=300)
            return check_node_version(min_version)
        except Exception:
            try:
                subprocess.run(["choco", "install", "nodejs"], timeout=300)
                return check_node_version(min_version)
            except Exception:
                pass
    
    return False