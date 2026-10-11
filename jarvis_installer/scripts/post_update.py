#!/usr/bin/env python3
"""Post-update script - runs after update is applied."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis_installer.paths import get_jarvis_paths
from jarvis_installer.core.config import ensure_config_migrated
from jarvis_installer.core.health import run_health_check
from jarvis_installer.install.service import start_service
from jarvis_installer.update.notifier import UpdateNotifier


async def main():
    print("[Post-Update] Running post-update tasks...")
    
    paths = get_jarvis_paths()
    
    # 1. Migrate config
    print("[Post-Update] Migrating configuration...")
    ensure_config_migrated(paths.config_file)
    
    # 2. Start services
    print("[Post-Update] Starting services...")
    start_service()
    await asyncio.sleep(3)
    
    # 3. Health check
    print("[Post-Update] Running health check...")
    report = await run_health_check()
    
    if report.overall_healthy:
        print("[green]All health checks passed[/green]")
    else:
        print("[red]Some health checks failed![/red]")
        for name, result in report.results.items():
            if not result.healthy:
                print(f"  - {name}: {result.message}")
    
    # 4. Notify
    notifier = UpdateNotifier()
    # Version would be passed as arg in real usage
    # await notifier.notify_update_success("x.x.x")
    
    print("\n[bold green]Post-update tasks completed![/bold green]")


if __name__ == "__main__":
    asyncio.run(main())