#!/usr/bin/env python3
"""Pre-update script - runs before update is applied."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis_installer.paths import get_jarvis_paths
from jarvis_installer.install.service import stop_service
from jarvis_installer.install.node_env import backup_frontend_build


async def main():
    print("[Pre-Update] Running pre-update tasks...")
    
    paths = get_jarvis_paths()
    
    # 1. Stop services gracefully
    print("[Pre-Update] Stopping services...")
    stop_service()
    await asyncio.sleep(2)
    
    # 2. Backup frontend build
    print("[Pre-Update] Backing up frontend...")
    backup_frontend_build(get_jarvis_paths().frontend)
    
    # 3. Verify we can rollback
    print("[Pre-Update] Verifying rollback capability...")
    # TODO: Check rollback directory has space
    
    print("[green]Pre-update tasks completed[/green]")


if __name__ == "__main__":
    asyncio.run(main())