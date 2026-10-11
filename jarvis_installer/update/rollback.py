from __future__ import annotations

import asyncio
import shutil
import subprocess
import json
from pathlib import Path
from typing import Optional, List
from datetime import datetime
from dataclasses import dataclass, field

from ..paths import get_jarvis_paths


@dataclass
class RollbackPoint:
    path: Path
    timestamp: datetime
    version: str
    description: str
    size_mb: float


class RollbackManager:
    def __init__(self, paths=None):
        self.paths = paths or get_jarvis_paths()
        self.rollback_dir = self.paths.updates_rollback
        self.rollback_dir.mkdir(parents=True, exist_ok=True)

    def list_rollbacks(self) -> List[RollbackPoint]:
        """Lista pontos de rollback disponíveis."""
        points = []
        for path in sorted(self.rollback_dir.glob("*"), reverse=True):
            if not path.is_dir():
                continue
            
            meta_file = path / "metadata.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text())
                    timestamp = datetime.fromisoformat(meta.get("timestamp", ""))
                    version = meta.get("version", "unknown")
                except Exception:
                    timestamp = datetime.fromtimestamp(path.stat().st_mtime)
                    version = "unknown"
            else:
                timestamp = datetime.fromtimestamp(path.stat().st_mtime)
                version = "unknown"
            
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / (1024 * 1024)
            
            points.append(RollbackPoint(
                path=path,
                timestamp=timestamp,
                version=version,
                description=f"Rollback to {version}",
                size_mb=size,
            ))
        
        return points

    def get_latest(self) -> Optional[RollbackPoint]:
        points = self.list_rollbacks()
        return points[0] if points else None

    async def rollback_to(self, point: RollbackPoint) -> bool:
        """Rola back para ponto específico."""
        print(f"[Rollback] Rolling back to {point.version} ({point.timestamp})")
        
        try:
            # Stop services
            await self._stop_services()
            
            from ..paths import get_jarvis_paths
            paths = get_jarvis_paths()
            
            # Restore config
            backup_config = point.path / "config.yaml"
            if backup_config.exists():
                import shutil
                shutil.copy2(backup_config, get_jarvis_paths().config_file)
            
            # Restore frontend
            backup_frontend = point.path / "frontend_dist"
            if backup_frontend.exists():
                frontend_dist = get_jarvis_paths().frontend / "dist"
                if frontend_dist.exists():
                    shutil.rmtree(frontend_dist)
                shutil.copytree(backup_frontend, get_jarvis_paths().frontend / "dist")
            
            # Start services
            await self._start_services()
            
            print(f"[Rollback] Completed to {point.version}")
            return True
        except Exception as e:
            print(f"[Rollback] Failed: {e}")
            return False

    async def rollback_latest(self) -> bool:
        """Rollback para o ponto mais recente."""
        latest = self.get_latest()
        if not latest:
            print("[Rollback] No rollback points available")
            return False
        return await self.rollback_to(latest)

    def cleanup_old(self, keep: int = 5) -> int:
        """Remove rollbacks antigos, mantendo os mais recentes."""
        points = self.list_rollbacks()
        removed = 0
        for point in points[keep:]:
            try:
                shutil.rmtree(point.path, ignore_errors=True)
                removed += 1
            except Exception:
                pass
        return removed

    def get_rollback_size(self) -> float:
        """Retorna tamanho total dos rollbacks em MB."""
        total = 0
        for path in self.rollback_dir.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total / (1024 * 1024)

    async def _stop_services(self):
        import subprocess
        import platform
        system = platform.system().lower()
        try:
            if system == "linux":
                subprocess.run(["systemctl", "stop", "jarvis"], timeout=30)
            elif system == "windows":
                subprocess.run(["nssm", "stop", "JARVIS"], timeout=30)
            elif system == "darwin":
                subprocess.run(["launchctl", "stop", "com.jarvis.cyberbot"], timeout=30)
        except Exception:
            pass

    async def _start_services(self):
        import subprocess
        import platform
        system = platform.system().lower()
        try:
            if system == "linux":
                subprocess.run(["systemctl", "start", "jarvis"], check=True, timeout=30)
            elif system == "windows":
                subprocess.run(["nssm", "start", "JARVIS"], check=True, timeout=30)
            elif system == "darwin":
                subprocess.run(["launchctl", "start", "com.jarvis.cyberbot"], check=True, timeout=30)
        except Exception as e:
            print(f"[Service] Start warning: {e}")