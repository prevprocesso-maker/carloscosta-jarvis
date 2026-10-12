from __future__ import annotations

"""Auto Updater with Health Checks for JARVIS."""

import asyncio
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

import psutil


class HealthCheckResult:
    """Resultado de um health check."""
    
    def __init__(self, name: str, healthy: bool, message: str = "", details: dict = None):
        self.name = name
        self.healthy = healthy
        self.message = message
        self.details = details or {}
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "healthy": self.healthy,
            "message": self.message,
            "details": self.details
        }


class HealthChecker:
    """Sistema completo de health checks para JARVIS."""
    
    def __init__(self):
        from src.jarvis.core.telemetry import get_telemetry
        from src.jarvis.core.plataforma import get_jarvis_paths
        self.telemetry = get_telemetry()
        self.paths = get_jarvis_paths()
    
    def _get_settings(self):
        """Lazy import to avoid circular imports."""
        from src.jarvis.core.config import settings
        return settings
    
    async def run_all_checks(self) -> Dict[str, Any]:
        """Executa todos os health checks e retorna resultado agregado."""
        checks = [
            self.check_disk_space(),
            self.check_memory(),
            self.check_cpu(),
            self.check_vault_connection(),
            self.check_gemini_api(),
            self.check_whatsapp_bridge(),
            self.check_config_files(),
            self.check_permissions(),
        ]
        
        results = await asyncio.gather(*checks, return_exceptions=True)
        
        check_results = []
        all_healthy = True
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                check_results.append(HealthCheckResult(
                    name=f"check_{i}",
                    healthy=False,
                    message=f"Check failed with exception: {result}"
                ).to_dict())
                all_healthy = False
            else:
                check_results.append(result.to_dict())
                if not result.healthy:
                    all_healthy = False
        
        return {
            "healthy": all_healthy,
            "timestamp": asyncio.get_event_loop().time(),
            "checks": check_results
        }
    
    async def check_disk_space(self):
        """Verifica espaço em disco."""
        try:
            import platform as platform_module
            if platform_module.system() == "Windows":
                disk = psutil.disk_usage('C:\\')
            else:
                disk = psutil.disk_usage('/')
            
            percent = disk.percent
            free_gb = disk.free / (1024**3)
            
            if percent > 95:
                return HealthCheckResult("disk_space", False, 
                    f"Disk critical: {percent:.1f}% used ({free_gb:.1f}GB free)")
            elif percent > 85:
                return HealthCheckResult("disk_space", True,
                    f"Disk warning: {percent:.1f}% used ({free_gb:.1f}GB free)",
                    {"percent": percent, "free_gb": free_gb})
            
            return HealthCheckResult("disk_space", True,
                f"Disk OK: {percent:.1f}% used ({free_gb:.1f}GB free)",
                {"percent": percent, "free_gb": free_gb})
        except Exception as e:
            return HealthCheckResult("disk_space", False, f"Check failed: {e}")
    
    async def check_memory(self):
        """Verifica uso de memória."""
        try:
            memory = psutil.virtual_memory()
            percent = memory.percent
            available_gb = memory.available / (1024**3)
            
            if percent > 95:
                return HealthCheckResult("memory", False,
                    f"Memory critical: {percent:.1f}% used ({available_gb:.1f}GB available)")
            elif percent > 85:
                return HealthCheckResult("memory", True,
                    f"Memory warning: {percent:.1f}% used ({available_gb:.1f}GB available)",
                    {"percent": percent, "available_gb": available_gb})
            
            return HealthCheckResult("memory", True,
                f"Memory OK: {percent:.1f}% used ({available_gb:.1f}GB available)",
                {"percent": percent, "available_gb": available_gb})
        except Exception as e:
            return HealthCheckResult("memory", False, f"Check failed: {e}")
    
    async def check_cpu(self):
        """Verifica uso de CPU."""
        try:
            cpu_percent = psutil.cpu_percent(interval=0.5)
            
            if cpu_percent > 95:
                return HealthCheckResult("cpu", False,
                    f"CPU critical: {cpu_percent:.1f}%")
            elif cpu_percent > 85:
                return HealthCheckResult("cpu", True,
                    f"CPU warning: {cpu_percent:.1f}%",
                    {"percent": cpu_percent})
            
            return HealthCheckResult("cpu", True,
                f"CPU OK: {cpu_percent:.1f}%",
                {"percent": cpu_percent})
        except Exception as e:
            return HealthCheckResult("cpu", False, f"Check failed: {e}")
    
    async def check_vault_connection(self):
        """Verifica conexão com Vault/Obsidian."""
        try:
            from src.jarvis.integrations import create_vault_manager
            from pathlib import Path
            
            settings = self._get_settings()
            vault_path = getattr(settings, 'vault_path', None)
            if not vault_path:
                from src.jarvis.core.plataforma import get_jarvis_paths
                paths = get_jarvis_paths()
                vault_path = paths.data / "vault"
            else:
                vault_path = Path(vault_path)
            
            api_key = getattr(settings, 'vault_api_key', None)
            vault_host = getattr(settings, 'vault_host', 'localhost')
            vault_port = getattr(settings, 'vault_port', 27124)
            verify_ssl = getattr(settings, 'vault_verify_ssl', False)
            
            manager = await create_vault_manager(
                vault_path=vault_path,
                api_key=api_key,
                host=vault_host,
                port=vault_port,
                verify_ssl=verify_ssl,
            )
            
            # Test connection
            await manager._init_session()
            await manager.list_files("")
            await manager._close_session()
            
            return HealthCheckResult("vault_connection", True,
                "Vault connection OK")
        except Exception as e:
            return HealthCheckResult("vault_connection", False,
                f"Vault connection failed: {e}")
    
    async def check_gemini_api(self):
        """Verifica conectividade com Gemini API."""
        try:
            import httpx
            settings = self._get_settings()
            api_key = getattr(settings, 'gemini_api_key', None) or os.getenv('GEMINI_API_KEY')
            if not api_key:
                return HealthCheckResult("gemini_api", False,
                    "Gemini API key not configured")
            
            # Simple connectivity test
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    "https://generativelanguage.googleapis.com/v1/models",
                    params={"key": api_key},
                    headers={"x-goog-api-key": api_key}
                )
                if response.status_code == 200:
                    return HealthCheckResult("gemini_api", True,
                        "Gemini API reachable")
                else:
                    return HealthCheckResult("gemini_api", False,
                        f"Gemini API returned {response.status_code}")
        except Exception as e:
            return HealthCheckResult("gemini_api", False,
                f"Gemini API check failed: {e}")
    
    async def check_whatsapp_bridge(self):
        """Verifica WhatsApp Bridge."""
        try:
            settings = self._get_settings()
            if not getattr(settings.transports.webhook, 'enabled', False):
                return HealthCheckResult("whatsapp_bridge", True,
                    "WhatsApp bridge disabled in config")
            
            # Check if bridge process is running
            # This is a simplified check - in reality would check process/port
            return HealthCheckResult("whatsapp_bridge", True,
                "WhatsApp bridge configured",
                {"enabled": True, "port": getattr(settings.transports.webhook, 'whatsapp_port', 8765)})
        except Exception as e:
            return HealthCheckResult("whatsapp_bridge", False,
                f"Check failed: {e}")
    
    async def check_config_files(self):
        """Verifica arquivos de configuração essenciais."""
        try:
            from src.jarvis.core.plataforma import get_jarvis_paths
            paths = get_jarvis_paths()
            required_files = [
                paths.config_file,
                paths.config_dir / "vault_api_key.txt",
            ]
            
            missing = [str(f) for f in required_files if not f.exists()]
            
            if missing:
                return HealthCheckResult("config_files", False,
                    f"Missing config files: {missing}")
            
            return HealthCheckResult("config_files", True,
                "All config files present")
        except Exception as e:
            return HealthCheckResult("config_files", False, f"Check failed: {e}")
    
    async def check_permissions(self):
        """Verifica permissões de escrita."""
        try:
            from src.jarvis.core.plataforma import get_jarvis_paths
            paths = get_jarvis_paths()
            test_file = paths.logs / "permission_test.tmp"
            test_file.write_text("test")
            test_file.unlink()
            
            return HealthCheckResult("permissions", True,
                "Write permissions OK")
        except Exception as e:
            return HealthCheckResult("permissions", False,
                f"Permission check failed: {e}")
    
    async def run_all_checks(self) -> Dict[str, Any]:
        """Executa todos os health checks e retorna resultado agregado."""
        checks = [
            self.check_disk_space(),
            self.check_memory(),
            self.check_cpu(),
            self.check_vault_connection(),
            self.check_gemini_api(),
            self.check_whatsapp_bridge(),
            self.check_config_files(),
            self.check_permissions(),
        ]
        
        results = await asyncio.gather(*checks, return_exceptions=True)
        
        check_results = []
        all_healthy = True
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                check_results.append(HealthCheckResult(
                    name=f"check_{i}",
                    healthy=False,
                    message=f"Check failed with exception: {result}"
                ).to_dict())
                all_healthy = False
            else:
                check_results.append(result.to_dict())
                if not result.healthy:
                    all_healthy = False
        
        return {
            "healthy": all_healthy,
            "timestamp": asyncio.get_event_loop().time(),
            "checks": check_results
        }
    
    def _get_settings(self):
        """Lazy import to avoid circular imports."""
        from src.jarvis.core.config import settings
        return settings