#!/usr/bin/env python3
"""Post-installation script - runs after installation completes."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add installer to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis_installer.paths import get_jarvis_paths
from jarvis_installer.core.config import ensure_config_migrated
from jarvis_installer.core.health import run_health_check
from jarvis_installer.install.service import get_service_status


async def main():
    print("[Post-Install] Running post-installation tasks...")
    
    paths = get_jarvis_paths()
    
    # 1. Ensure config migrated
    print("[Post-Install] Migrating config...")
    ensure_config_migrated(paths.config_file)
    
    # 2. Health check
    print("[Post-Install] Running health check...")
    report = await run_health_check()
    
    if report.overall_healthy:
        print("[green]All health checks passed[/green]")
    else:
        print("[yellow]Some health checks failed:[/yellow]")
        for name, result in report.results.items():
            if not result.healthy:
                print(f"  - {name}: {result.message}")
    
    # 3. Service status
    status = get_service_status()
    print(f"[Post-Install] Service status: {status}")
    
    # 4. Print next steps
    print("\n[bold cyan]Installation Complete![/bold cyan]")
    print("Next steps:")
    print("  1. Configure Gemini API key in config.yaml")
    print("  2. Run 'jarvis' to start")
    print("  3. Access Web UI at http://localhost:8000")
    print("  4. Run 'jarvis doctor' for diagnostics")


if __name__ == "__main__":
    asyncio.run(main())