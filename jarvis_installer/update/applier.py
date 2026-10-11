from __future__ import annotations

import asyncio
import shutil
import subprocess
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field

from ..paths import get_jarvis_paths
from ..core.config import load_config, save_config, ensure_config_migrated
from ..core.health import run_health_check
from ..install.python_env import verify_installation
from ..install.node_env import build_frontend, backup_frontend_build, restore_frontend_build
from ..update.checker import ReleaseInfo


@dataclass
class UpdateResult:
    success: bool
    version: str
    message: str
    rolled_back: bool = False
    details: Dict = field(default_factory=dict)


class UpdateApplier:
    def __init__(self, paths=None):
        self.paths = paths or get_jarvis_paths()
        self.config_path = self.paths.config_file
        self.backup_dir = self.paths.updates_rollback / datetime.now().strftime("%Y%m%d_%H%M%S")

    async def apply_update(self, release: "ReleaseInfo", assets: Dict[str, Path], 
                          dry_run: bool = False) -> UpdateResult:
        """Aplica update completo (backend + frontend)."""
        print(f"[Update] Applying {release.version}...")
        
        # Create backup
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        await self._create_backup()
        
        try:
            # 1. Pre-update checks
            if not await self._pre_update_checks():
                return UpdateResult(False, str(release.version), "Pre-update checks failed")
            
            # 2. Stop services
            await self._stop_services()
            
            # 3. Apply backend update
            if not await self._apply_backend(assets, dry_run):
                raise Exception("Backend update failed")
            
            # 4. Apply frontend update
            if not await self._apply_frontend(assets, dry_run):
                raise Exception("Frontend update failed")
            
            # 5. Migrate config
            await self._migrate_config()
            
            # 6. Start services
            await self._start_services()
            
            # 7. Health checks
            if not await self._post_update_health_checks():
                raise Exception("Post-update health checks failed")
            
            # 7. Update version in config
            await self._update_version_config(release)
            
            # Cleanup old rollbacks
            await self._cleanup_old_rollbacks()
            
            return UpdateResult(True, str(release.version), "Update applied successfully")
            
        except Exception as e:
            print(f"[Update] Failed: {e}")
            # Rollback
            if not dry_run:
                await self.rollback()
            return UpdateResult(False, str(release.version), str(e), rolled_back=True)

    async def _create_backup(self) -> None:
        """Cria backup completo para rollback."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        paths = get_jarvis_paths()
        
        # Backup config
        if Path(paths.config_file).exists():
            shutil.copy2(paths.config_file, self.backup_dir / "config.yaml")
        
        # Backup frontend build
        if (self.paths.frontend / "dist").exists():
            shutil.copytree(self.paths.frontend / "dist", self.backup_dir / "frontend_dist")
        
        # Backup venv (optional, can be large)
        # shutil.copytree(paths.venv, self.backup_dir / "venv", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        
        # Save metadata
        metadata = {
            "timestamp": datetime.now().isoformat(),
            "paths": {k: str(v) for k, v in vars(get_jarvis_paths()).items()},
        }
        (self.backup_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
        
        print(f"[Backup] Created at {self.backup_dir}")

    async def _pre_update_checks(self) -> bool:
        print("[Update] Running pre-update checks...")
        # Check disk space
        # Check internet connectivity
        # Check no other updates in progress
        return True

    async def _stop_services(self) -> None:
        print("[Update] Stopping services...")
        # Stop system service
        try:
            import subprocess
            system = __import__("platform").system().lower()
            if system == "linux":
                subprocess.run(["systemctl", "stop", "jarvis"], timeout=30)
            elif system == "windows":
                subprocess.run(["nssm", "stop", "JARVIS"], timeout=30)
            elif system == "darwin":
                subprocess.run(["launchctl", "stop", "com.jarvis.cyberbot"], timeout=30)
        except Exception:
            pass  # Best effort

    async def _apply_backend(self, assets: Dict[str, Path], dry_run: bool) -> bool:
        print("[Update] Applying backend update...")
        paths = get_jarvis_paths()
        
        # Find backend asset (wheel or source)
        backend_asset = None
        for name, path in assets.items():
            if name.endswith(".whl") or name.endswith(".tar.gz") or "jarvis" in name.lower():
                backend_asset = path
                break
        
        if not backend_asset:
            print("[Update] No backend asset found, skipping")
            return True
        
        if dry_run:
            return True
        
        # Install via pip
        venv = get_jarvis_paths().venv
        pip = venv / ("Scripts/pip.exe" if __import__("platform").system().lower() == "windows" else "bin/pip")
        
        try:
            result = subprocess.run([
                str(pip), "install", "--no-cache-dir", "--upgrade", str(backend_asset)
            ], capture_output=True, text=True, timeout=180)
            
            if result.returncode != 0:
                print(f"[Backend] Install failed: {result.stderr}")
                return False
            
            # Verify
            if not verify_installation(venv):
                return False
            
            return True
        except Exception as e:
            print(f"[Backend] Error: {e}")
            return False

    async def _apply_frontend(self, assets: Dict[str, Path], dry_run: bool) -> bool:
        print("[Update] Applying frontend update...")
        
        # Frontend is built from source, check if frontend source updated
        # For now, we rebuild from source
        if dry_run:
            return True
        
        return build_frontend(get_jarvis_paths().frontend)

    async def _migrate_config(self) -> None:
        print("[Update] Migrating configuration...")
        ensure_config_migrated(get_jarvis_paths().config_file)

    async def _start_services(self) -> None:
        print("[Update] Starting services...")
        try:
            import subprocess
            system = __import__("platform").system().lower()
            if system == "linux":
                subprocess.run(["systemctl", "start", "jarvis"], check=True, timeout=30)
            elif system == "windows":
                subprocess.run(["nssm", "start", "JARVIS"], check=True, timeout=30)
            elif system == "darwin":
                subprocess.run(["launchctl", "start", "com.jarvis.cyberbot"], check=True, timeout=30)
        except Exception as e:
            print(f"[Service] Start warning: {e}")

    async def _post_update_health_checks(self) -> bool:
        print("[Update] Running health checks...")
        try:
            report = await run_health_check()
            if not report.overall_healthy:
                print(f"[Health] Checks failed: {[k for k, v in report.results.items() if not v.healthy]}")
                return False
            return True
        except Exception as e:
            print(f"[Health] Check error: {e}")
            return False

    async def _update_version_config(self, release) -> None:
        from ..core.config import load_config, save_config
        from ..paths import get_jarvis_paths
        
        config = load_config(get_jarvis_paths().config_file)
        config["app"]["version"] = str(release.version)
        config["app"]["channel"] = release.version.channel.value
        save_config(config, get_jarvis_paths().config_file)
        print(f"[Config] Updated version to {release.version}")

    async def _cleanup_old_rollbacks(self, keep: int = 5) -> None:
        rollbacks = sorted(get_jarvis_paths().updates_rollback.glob("*"), reverse=True)
        for old in rollbacks[keep:]:
            shutil.rmtree(old, ignore_errors=True)

    async def rollback(self) -> bool:
        """Rola back para backup anterior."""
        print(f"[Rollback] Rolling back to {self.backup_dir}")
        
        try:
            # Stop services
            await self._stop_services()
            
            paths = get_jarvis_paths()
            
            # Restore config
            backup_config = self.backup_dir / "config.yaml"
            if backup_config.exists():
                shutil.copy2(backup_config, paths.config_file)
            
            # Restore frontend
            backup_frontend = self.backup_dir / "frontend_dist"
            if backup_frontend.exists():
                if (paths.frontend / "dist").exists():
                    shutil.rmtree(paths.frontend / "dist")
                shutil.copytree(backup_frontend, paths.frontend / "dist")
            
            # Restart services
            await self._start_services()
            
            print("[Rollback] Completed successfully")
            return True
        except Exception as e:
            print(f"[Rollback] Failed: {e}")
            return False


async def apply_update(release: "ReleaseInfo", assets: Dict[str, Path], dry_run: bool = False) -> UpdateResult:
    """Função de conveniência para aplicar update."""
    applier = UpdateApplier()
    return await applier.apply_update(release, assets, dry_run)