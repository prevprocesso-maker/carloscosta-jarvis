from __future__ import annotations

import os
import sys
import platform
import subprocess
import shutil
import json
from dataclasses import dataclass, field
from typing import Optional, List
from pathlib import Path


@dataclass
class SystemInfo:
    os: str                    # windows, linux, darwin
    os_version: str
    arch: str                  # x86_64, arm64, etc
    python_version: str
    python_executable: str
    has_gpu: bool
    gpu_name: Optional[str] = None
    gpu_memory_mb: Optional[int] = None
    cuda_available: bool = False
    cuda_version: Optional[str] = None
    total_memory_mb: int = 0
    available_memory_mb: int = 0
    disk_free_gb: float = 0
    is_admin: bool = False
    node_version: Optional[str] = None
    npm_version: Optional[str] = None
    docker_available: bool = False
    docker_compose_available: bool = False


def detect_system() -> SystemInfo:
    """Detecta informações completas do sistema."""
    info = SystemInfo(
        os=platform.system().lower(),
        os_version=platform.version(),
        arch=platform.machine(),
        python_version=platform.python_version(),
        python_executable=sys.executable,
        has_gpu=False,
    )

    # Memory
    try:
        import psutil
        mem = psutil.virtual_memory()
        info.total_memory_mb = mem.total // (1024 * 1024)
        info.available_memory_mb = mem.available // (1024 * 1024)
        disk = psutil.disk_usage(os.path.expanduser("~"))
        info.disk_free_gb = disk.free / (1024 ** 3)
    except Exception:
        pass

    # Admin check
    try:
        if info.os == "windows":
            import ctypes
            info.is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            info.is_admin = os.geteuid() == 0
    except Exception:
        pass

    # GPU detection
    info.has_gpu, info.gpu_name, info.gpu_memory_mb, info.cuda_available, info.cuda_version = _detect_gpu()

    # Node.js
    info.node_version = _run_cmd(["node", "--version"])
    info.npm_version = _run_cmd(["npm", "--version"])

    # Docker
    info.docker_available = _run_cmd(["docker", "--version"]) is not None
    info.docker_compose_available = _run_cmd(["docker", "compose", "version"]) is not None or \
                                     _run_cmd(["docker-compose", "--version"]) is not None

    return info


def _detect_gpu() -> tuple[bool, Optional[str], Optional[int], bool, Optional[str]]:
    """Detecta GPU NVIDIA e CUDA."""
    has_gpu = False
    gpu_name = None
    gpu_memory = None
    cuda_available = False
    cuda_version = None

    try:
        # nvidia-smi
        result = _run_cmd(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        if result:
            lines = result.strip().split('\n')
            if lines:
                has_gpu = True
                parts = lines[0].split(', ')
                gpu_name = parts[0] if parts else None
                gpu_memory = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    except Exception:
        pass

    try:
        # nvcc --version
        result = _run_cmd(["nvcc", "--version"])
        if result and "release" in result:
            cuda_available = True
            for line in result.split('\n'):
                if "release" in line:
                    cuda_version = line.split("release")[1].split(",")[0].strip()
                    break
    except Exception:
        pass

    try:
        # torch cuda
        import torch
        if torch.cuda.is_available():
            cuda_available = True
            if not gpu_name:
                gpu_name = torch.cuda.get_device_name(0)
            if not gpu_memory:
                gpu_memory = torch.cuda.get_device_properties(0).total_memory // (1024 * 1024)
    except Exception:
        pass

    return has_gpu, gpu_name, gpu_memory, cuda_available, cuda_version


def _run_cmd(cmd: List[str]) -> Optional[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def check_python_version(min_version: str = "3.11") -> bool:
    """Verifica se Python atende versão mínima."""
    from packaging import version
    return version.parse(platform.python_version()) >= version.parse(min_version)


def get_package_manager() -> Optional[str]:
    """Detecta gerenciador de pacotes (Linux)."""
    if platform.system().lower() != "linux":
        return None
    for pm in ["apt", "dnf", "pacman", "zypper", "apk"]:
        if shutil.which(pm):
            return pm
    return None


def install_system_deps(packages: List[str]) -> bool:
    """Instala dependências de sistema (Linux)."""
    pm = get_package_manager()
    if not pm:
        return False
    
    cmds = {
        "apt": ["apt", "update", "&&", "apt", "install", "-y"] + packages,
        "dnf": ["dnf", "install", "-y"] + packages,
        "pacman": ["pacman", "-S", "--noconfirm"] + packages,
        "zypper": ["zypper", "install", "-y"] + packages,
        "apk": ["apk", "add"] + packages,
    }
    
    cmd = cmds.get(pm)
    if not cmd:
        return False
    
    try:
        subprocess.run(cmd, check=True)
        return True
    except Exception:
        return False