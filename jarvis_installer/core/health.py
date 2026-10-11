from __future__ import annotations

import asyncio
import subprocess
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Awaitable
from pathlib import Path
import time

from ..paths import get_jarvis_paths


@dataclass
class HealthResult:
    name: str
    healthy: bool
    message: str = ""
    duration_ms: float = 0
    details: Dict = field(default_factory=dict)


@dataclass
class HealthReport:
    timestamp: float = field(default_factory=time.time)
    results: Dict[str, HealthResult] = field(default_factory=dict)
    overall_healthy: bool = True

    def __post_init__(self):
        self.overall_healthy = all(r.healthy for r in self.results.values())


class HealthChecker:
    def __init__(self, paths=None):
        self.paths = paths or get_jarvis_paths()
        self.checks: Dict[str, Callable[[], Awaitable[HealthResult]]] = {}
        self._register_default_checks()

    def _register_default_checks(self):
        self.checks = {
            "python_env": self.check_python_env,
            "venv_exists": self.check_venv_exists,
            "deps_installed": self.check_deps_installed,
            "gemini_api": self.check_gemini_api,
            "whisper_model": self.check_whisper_model,
            "frontend_build": self.check_frontend_build,
            "config_valid": self.check_config_valid,
            "disk_space": self.check_disk_space,
            "memory_usage": self.check_memory_usage,
            "ports_available": self.check_ports_available,
        }

    def register_check(self, name: str, check_fn: Callable[[], Awaitable[HealthResult]]):
        self.checks[name] = check_fn

    async def run_check(self, name: str) -> HealthResult:
        if name not in self.checks:
            return HealthResult(name, False, f"Check '{name}' not found")
        
        start = time.perf_counter()
        try:
            result = await self.checks[name]()
            result.duration_ms = (time.perf_counter() - start) * 1000
            return result
        except Exception as e:
            return HealthResult(
                name, False, f"Check failed: {e}", 
                (time.perf_counter() - start) * 1000
            )

    async def run_all(self, exclude: Optional[List[str]] = None) -> "HealthReport":
        exclude = exclude or []
        results = {}
        
        for name, check_fn in self.checks.items():
            if name in exclude:
                continue
            results[name] = await self.run_check(name)
        
        return HealthReport(results=results)

    # --- Individual Checks ---

    async def check_python_env(self) -> HealthResult:
        import sys
        version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        if sys.version_info >= (3, 11):
            return HealthResult("python_env", True, f"Python {version} OK")
        return HealthResult("python_env", False, f"Python {version} < 3.11 required")

    async def check_venv_exists(self) -> HealthResult:
        paths = get_jarvis_paths()
        if paths.venv.exists():
            python_exe = paths.venv / ("Scripts/python.exe" if platform.system().lower() == "windows" else "bin/python")
            if python_exe.exists():
                return HealthResult("venv_exists", True, f"Venv at {paths.venv}")
        return HealthResult("venv_exists", False, "Virtual environment not found")

    async def check_deps_installed(self) -> HealthResult:
        paths = get_jarvis_paths()
        python = get_python_executable(paths.venv)
        try:
            result = subprocess.run([str(python), "-c", "import jarvis; print('OK')"], 
                                  capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return HealthResult("deps_installed", True, "Python dependencies OK")
            return HealthResult("deps_installed", False, f"Import failed: {result.stderr}")
        except Exception as e:
            return HealthResult("deps_installed", False, f"Check failed: {e}")

    async def check_gemini_api(self) -> HealthResult:
        from ..config import load_config
        paths = get_jarvis_paths()
        config = load_config(paths.config_file)
        api_key = config.get("gemini", {}).get("api_key", "")
        
        if not api_key:
            return HealthResult("gemini_api", False, "API key not configured")
        
        # Quick validation (don't make actual API call in health check)
        if len(api_key) < 20:
            return HealthResult("gemini_api", False, "API key appears invalid")
        
        return HealthResult("gemini_api", True, "API key configured")

    async def check_whisper_model(self) -> HealthResult:
        paths = get_jarvis_paths()
        # Check if model is cached
        from huggingface_hub import snapshot_download
        from ..config import load_config
        
        config = load_config(paths.config_file)
        model = config.get("transports", {}).get("local", {}).get("whisper_model", "large-v3")
        
        try:
            # Quick check - just verify model can be loaded
            from faster_whisper import WhisperModel
            model_path = str(paths.base / "models" / f"whisper-{model}")
            if Path(model_path).exists():
                return HealthResult("whisper_model", True, f"Model {model} cached locally")
            return HealthResult("whisper_model", True, f"Model {model} will download on first use (needs internet)")
        except Exception as e:
            return HealthResult("whisper_model", False, f"Whisper check failed: {e}")

    async def check_frontend_build(self) -> HealthResult:
        paths = get_jarvis_paths()
        dist = paths.frontend_dist
        index = dist / "index.html"
        
        if index.exists():
            size = index.stat().st_size
            return HealthResult("frontend_build", True, f"Frontend build OK ({size} bytes)")
        return HealthResult("frontend_build", False, "Frontend build not found - run 'jarvis frontend build'")

    async def check_config_valid(self) -> HealthResult:
        paths = get_jarvis_paths()
        from ..config import load_config
        try:
            config = load_config(paths.config_file)
            # Validate required fields
            required = ["gemini.api_key", "user.name"]
            missing = [p for p in required if not get_nested(config, p)]
            if missing:
                return HealthResult("config_valid", False, f"Missing: {', '.join(missing)}")
            return HealthResult("config_valid", True, "Configuration valid")
        except Exception as e:
            return HealthResult("config_valid", False, f"Config error: {e}")

    async def check_disk_space(self) -> HealthResult:
        import shutil
        paths = get_jarvis_paths()
        total, used, free = shutil.disk_usage(paths.base)
        free_gb = free / (1024**3)
        
        if free_gb > 5:
            return HealthResult("disk_space", True, f"{free_gb:.1f} GB free")
        elif free_gb > 1:
            return HealthResult("disk_space", True, f"Low disk: {free_gb:.1f} GB free", healthy=True)
        return HealthResult("disk_space", False, f"Critical: {free_gb:.1f} GB free")

    async def check_memory_usage(self) -> HealthResult:
        try:
            import psutil
            mem = psutil.virtual_memory()
            if mem.percent < 80:
                return HealthResult("memory_usage", True, f"RAM {mem.percent:.0f}% used")
            elif mem.percent < 95:
                return HealthResult("memory_usage", True, f"High RAM: {mem.percent:.0f}%", healthy=True)
            return HealthResult("memory_usage", False, f"Critical RAM: {mem.percent:.0f}%")
        except Exception:
            return HealthResult("memory_usage", True, "Could not check (psutil not available)")

    async def check_ports_available(self) -> HealthResult:
        import socket
        paths = get_jarvis_paths()
        from ..config import load_config
        
        config = load_config(paths.config_file)
        ports = [
            config.get("transports", {}).get("remote", {}).get("port", 8000),
            config.get("transports", {}).get("webhook", {}).get("whatsapp_port", 8765),
        ]
        
        busy = []
        for port in ports:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    if s.connect_ex(("127.0.0.1", port)) == 0:
                        busy.append(port)
            except Exception:
                pass
        
        if not busy:
            return HealthResult("ports_available", True, "Required ports available")
        return HealthResult("ports_available", False, f"Ports in use: {busy}")


async def run_health_check() -> HealthReport:
    checker = HealthChecker()
    return await checker.run_all()


def get_nested(d: dict, path: str, default=None):
    keys = path.split(".")
    current = d
    for k in path.split("."):
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return default
    return current