from __future__ import annotations

import subprocess
import platform
import shutil
from pathlib import Path
from typing import Optional, Dict, List


def check_docker_available() -> bool:
    """Verifica se Docker está disponível."""
    try:
        result = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def check_docker_compose_available() -> bool:
    """Verifica se Docker Compose está disponível."""
    # Try docker compose (v2)
    try:
        result = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return True
    except Exception:
        pass
    
    # Try docker-compose (v1)
    try:
        result = subprocess.run(["docker-compose", "--version"], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def check_docker_daemon() -> bool:
    """Verifica se Docker daemon está rodando."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def build_docker_image(dockerfile: Path, tag: str, context: Path = None, build_args: Dict[str, str] = None) -> bool:
    """Build de imagem Docker."""
    if context is None:
        context = dockerfile.parent
    
    cmd = ["docker", "build", "-t", tag, "-f", str(dockerfile), str(context)]
    
    if build_args:
        for key, value in build_args.items():
            cmd.extend(["--build-arg", f"{key}={value}"])
    
    try:
        print(f"[Docker] Building {tag}...")
        result = subprocess.run(cmd, cwd=context, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"[Docker] Build failed: {result.stderr}")
            return False
        print(f"[Docker] Built {tag}")
        return True
    except Exception as e:
        print(f"[Docker] Error: {e}")
        return False


def docker_compose_up(compose_file: Path, project_name: str = "jarvis", detach: bool = True) -> bool:
    """Sobe containers via docker-compose."""
    cmd = ["docker", "compose", "-f", str(compose_file), "-p", project_name, "up"]
    if detach:
        cmd.append("-d")
    
    try:
        print(f"[Docker] Starting {project_name}...")
        result = subprocess.run(cmd, cwd=compose_file.parent, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"[Docker] Compose up failed: {result.stderr}")
            return False
        print(f"[Docker] {project_name} started")
        return True
    except Exception as e:
        print(f"[Docker] Error: {e}")
        return False


def docker_compose_down(compose_file: Path, project_name: str = "jarvis", volumes: bool = False) -> bool:
    """Para containers via docker-compose."""
    cmd = ["docker", "compose", "-f", str(compose_file), "-p", project_name, "down"]
    if volumes:
        cmd.append("-v")
    
    try:
        print(f"[Docker] Stopping {project_name}...")
        result = subprocess.run(cmd, cwd=compose_file.parent, capture_output=True, text=True, timeout=60)
        return result.returncode == 0
    except Exception:
        return False


def docker_pull_image(image: str) -> bool:
    """Pull de imagem Docker."""
    try:
        result = subprocess.run(["docker", "pull", image], capture_output=True, text=True, timeout=300)
        return result.returncode == 0
    except Exception:
        return False


def docker_image_exists(image: str) -> bool:
    """Verifica se imagem existe localmente."""
    try:
        result = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def get_docker_info() -> dict:
    """Retorna info do Docker."""
    info = {}
    try:
        info["version"] = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        info["version"] = None
    try:
        info["compose"] = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        info["compose"] = None
    try:
        info["daemon"] = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        info["daemon"] = None
    return info


def cleanup_docker(remove_images: bool = False, remove_volumes: bool = False) -> bool:
    """Limpa recursos Docker não utilizados."""
    try:
        # Remove containers parados
        subprocess.run(["docker", "container", "prune", "-f"], timeout=30)
        
        # Remove networks não utilizados
        subprocess.run(["docker", "network", "prune", "-f"], timeout=30)
        
        if remove_images:
            # Remove imagens não utilizadas
            subprocess.run(["docker", "image", "prune", "-a", "-f"], timeout=60)
        
        if remove_volumes:
            # Remove volumes não utilizados
            subprocess.run(["docker", "volume", "prune", "-f"], timeout=30)
        
        return True
    except Exception:
        return False


def generate_docker_compose(template_path: Path, output_path: Path, context: dict) -> bool:
    """Gera docker-compose.yml a partir de template Jinja2."""
    try:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(template_path.parent))
        template = env.get_template(template_path.name)
        rendered = template.render(**context)
        output_path.write_text(rendered)
        return True
    except Exception as e:
        print(f"[Docker] Template error: {e}")
        return False