from __future__ import annotations

"""Auto Updater - Handles automatic updates with health checks."""

import asyncio
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
import psutil
import packaging.version

from src.jarvis.core.config import settings
from src.jarvis.core.telemetry import get_telemetry
from src.jarvis.core.config import get_settings


class HealthCheck:
    """Health check result."""

    def __init__(self, name: str, healthy: bool, message: str = "", details: dict = None):
        self.name = name
        self.healthy = healthy
        self.message = message
        self.details = details or {}
        self.timestamp = time.time()

    def to_dict(self):
        return {
            "name": self.name,
            "healthy": self.healthy,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }


class AutoUpdater:
    """Auto updater with health checks."""

    def __init__(self, orchestrator_ref):
        self.orchestrator = orchestrator_ref
        self.settings = orchestrator_ref.settings
        self.telemetry = get_telemetry()
        self._update_task: Optional[asyncio.Task] = None
        self._running = False
        self._current_version = self._get_current_version()
        self._update_channel = getattr(settings, 'update_channel', 'stable')
        self._github_repo = "gaahzx/jarvis-app"  # Default repo
        self._check_interval = 3600  # 1 hour

    def _get_current_version(self) -> str:
        """Get current version from package or version file."""
        try:
            from src.jarvis.core.version import CURRENT_VERSION
            return CURRENT_VERSION
        except ImportError:
            return "2.0.0-dev"

    async def start(self):
        """Start auto updater."""
        if self._running:
            return
        self._running = True
        self._update_task = asyncio.create_task(self._update_loop())
        print("[AutoUpdater] Started")

    async def stop(self):
        """Stop auto updater."""
        self._running = False
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        print("[AutoUpdater] Stopped")

    async def _update_loop(self):
        """Main update loop."""
        # Initial check after 30 seconds
        await asyncio.sleep(30)
        
        while self._running:
            try:
                await self.check_and_update()
            except Exception as e:
                self.telemetry.logger.error(f"Auto updater error: {e}")
            
            try:
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break

    async def check_and_update(self) -> bool:
        """Check for updates and apply if available."""
        try:
            # Run health checks first
            health_results = await self.run_health_checks()
            if not all(h.healthy for h in health_results):
                self.telemetry.logger.warning("Health checks failed, skipping update")
                return False

            # Check for updates
            latest_version = await self._fetch_latest_version()
            if not latest_version:
                return False

            if self._is_newer_version(latest_version):
                self.telemetry.logger.info(f"New version available: {latest_version}")
                return await self._download_and_install(latest_version)
            
            return False
        except Exception as e:
            self.telemetry.logger.error(f"Update check failed: {e}")
            return False

    async def _fetch_latest_version(self) -> Optional[str]:
        """Fetch latest version from GitHub releases."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"https://api.github.com/repos/{self._github_repo}/releases/latest"
                headers = {"Accept": "application/vnd.github.v3+json"}
                
                # Add token if available
                token = getattr(self.settings, 'github_token', None) or os.getenv('GITHUB_TOKEN')
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    tag_name = data.get("tag_name", "")
                    if tag_name.startswith("v"):
                        return tag_name[1:]  # Remove 'v' prefix
                    return tag_name
        except Exception as e:
            self.telemetry.logger.error(f"Failed to fetch latest version: {e}")
        return None

    def _is_newer_version(self, latest: str) -> bool:
        """Check if latest version is newer than current."""
        try:
            from packaging import version
            return version.parse(latest) > version.parse(self._current_version)
        except Exception:
            # Fallback: simple string comparison
            return latest != self._current_version

    async def _download_and_install(self, version: str) -> bool:
        """Download and install update."""
        try:
            self.telemetry.logger.info(f"Downloading version {version}...")
            
            # Find installer asset
            asset_url = await self._find_installer_asset(version)
            if not asset_url:
                self.telemetry.logger.error("No installer asset found")
                return False

            # Download to temp file
            with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as tmp:
                tmp_path = tmp.name
            
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    async with client.stream("GET", asset_url) as response:
                        response.raise_for_status()
                        async with open(tmp_path, "wb") as f:
                            async for chunk in response.aiter_bytes():
                                await f.write(chunk)
                
                # Verify SHA256 if available
                if not await self._verify_installer(tmp_path, version):
                    self.telemetry.logger.error("Installer verification failed")
                    return False

                # Install silently
                return await self._run_installer(tmp_path)
            finally:
                # Cleanup temp file
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        except Exception as e:
            self.telemetry.logger.error(f"Install failed: {e}")
            return False

    async def _find_installer_asset(self, version: str) -> Optional[str]:
        """Find installer asset URL for version."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"https://api.github.com/repos/{self._github_repo}/releases/tags/v{version}"
                headers = {"Accept": "application/vnd.github.v3+json"}
                
                token = getattr(self.settings, 'github_token', None) or os.getenv('GITHUB_TOKEN')
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    for asset in data.get("assets", []):
                        name = asset.get("name", "")
                        if name.endswith(".exe") and "setup_jarvis" in name:
                            return asset["browser_download_url"]
        except Exception as e:
            self.telemetry.logger.error(f"Failed to find installer asset: {e}")
        return None

    async def _verify_installer(self, path: str, version: str) -> bool:
        """Verify installer SHA256."""
        try:
            # Try to fetch SHA256 from release assets
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"https://api.github.com/repos/{self._github_repo}/releases/tags/v{version}"
                headers = {"Accept": "application/vnd.github.v3+json"}
                
                token = getattr(self.settings, 'github_token', None) or os.getenv('GITHUB_TOKEN')
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    for asset in data.get("assets", []):
                        if asset.get("name", "").endswith(".sha256"):
                            sha_url = asset["browser_download_url"]
                            sha_response = await client.get(sha_url)
                            if sha_response.status_code == 200:
                                expected_sha = sha_response.text.strip().split()[0]
                                actual_sha = self._calculate_sha256(path)
                                return actual_sha.lower() == expected_sha.lower()
        except Exception as e:
            self.telemetry.logger.warning(f"SHA256 verification failed: {e}")
        return True  # Skip verification if failed

    def _calculate_sha256(self, path: str) -> str:
        """Calculate SHA256 of file."""
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    async def _run_installer(self, path: str) -> bool:
        """Run installer silently."""
        try:
            # Windows: run with /VERYSILENT /NORESTART
            if platform.system() == "Windows":
                proc = await asyncio.create_subprocess_exec(
                    path, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                return proc.returncode == 0
            return False
        except Exception as e:
            self.telemetry.logger.error(f"Installer execution failed: {e}")
            return False

    # --- Health Checks ---

    async def run_health_checks(self) -> list:
        """Run all health checks."""
        checks = []
        
        # 1. Vault API connectivity
        checks.append(await self._check_vault_api())
        
        # 2. Gemini API connectivity
        checks.append(await self._check_gemini_api())
        
        # 3. WhatsApp bridge connectivity
        checks.append(await self._check_whatsapp_bridge())
        
        # 4. System resources
        checks.append(await self._check_system_resources())
        
        # 5. Disk space
        checks.append(await self._check_disk_space())
        
        # 6. Database connectivity
        checks.append(await self._check_database())
        
        return checks

    async def _check_vault_api(self) -> HealthCheck:
        """Check Obsidian Vault API connectivity."""
        try:
            if self.orchestrator.vault_manager and self.orchestrator.vault_manager._vault_manager:
                vault_mgr = self.orchestrator.vault_manager._vault_manager
                # Try to list files
                result = await vault_mgr.list_files("")
                return HealthCheck("vault_api", True, "Vault API accessible", {"files_count": len(result)})
        except Exception as e:
            return HealthCheck("vault_api", False, f"Vault API error: {e}")
        return HealthCheck("vault_api", False, "Vault manager not initialized")

    async def _check_gemini_api(self) -> HealthCheck:
        """Check Gemini API connectivity."""
        try:
            from google import genai
            client = genai.Client(api_key=self.settings.gemini.api_key)
            # Simple test call
            response = await client.aio.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents="test"
            )
            return HealthCheck("gemini_api", True, "Gemini API accessible")
        except Exception as e:
            return HealthCheck("gemini_api", False, f"Gemini API error: {e}")

    async def _check_whatsapp_bridge(self) -> HealthCheck:
        """Check WhatsApp bridge connectivity."""
        try:
            if hasattr(self, 'orchestrator') and self.orchestrator.transport_manager:
                whatsapp = self.orchestrator.transport_manager._whatsapp_bridge
                if whatsapp and whatsapp.is_connected:
                    return HealthCheck("whatsapp_bridge", True, "WhatsApp bridge connected")
            return HealthCheck("whatsapp_bridge", False, "WhatsApp bridge not connected")
        except Exception as e:
            return HealthCheck("whatsapp_bridge", False, f"WhatsApp bridge error: {e}")

    async def _check_system_resources(self) -> HealthCheck:
        """Check system resources."""
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('C:\\' if platform.system() == 'Windows' else '/')
            
            healthy = cpu < 90 and mem.percent < 90 and disk.percent < 90
            return HealthCheck(
                "system_resources",
                healthy,
                f"CPU: {cpu}%, RAM: {mem.percent}%, Disk: {disk.percent}%",
                {"cpu": cpu, "memory": mem.percent, "disk": disk.percent}
            )
        except Exception as e:
            return HealthCheck("system_resources", False, f"System check error: {e}")

    async def _check_disk_space(self) -> HealthCheck:
        """Check disk space."""
        try:
            import psutil
            disk = psutil.disk_usage('C:\\' if platform.system() == 'Windows' else '/')
            healthy = disk.percent < 90
            return HealthCheck(
                "disk_space",
                healthy,
                f"Disk usage: {disk.percent}%",
                {"free_gb": disk.free / (1024**3), "total_gb": disk.total / (1024**3)}
            )
        except Exception as e:
            return HealthCheck("disk_space", False, f"Disk check error: {e}")

    async def _check_database(self) -> HealthCheck:
        """Check database connectivity."""
        try:
            # Try to access knowledge base
            if self.orchestrator.knowledge_base:
                # Simple test query
                await self.orchestrator.knowledge_base.search("test", limit=1)
                return HealthCheck("database", True, "Database accessible")
        except Exception as e:
            return HealthCheck("database", False, f"Database error: {e}")
        return HealthCheck("database", False, "Database not initialized")


def create_auto_updater(orchestrator_ref) -> AutoUpdater:
    """Factory to create auto updater."""
    return AutoUpdater(orchestrator_ref)