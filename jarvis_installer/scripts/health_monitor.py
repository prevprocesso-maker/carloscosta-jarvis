#!/usr/bin/env python3
"""Health monitor - runs periodically to check system health."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis_installer.paths import get_jarvis_paths
from jarvis_installer.core.health import run_health_check, HealthChecker
from jarvis_installer.update.notifier import UpdateNotifier
from jarvis_installer.install.service import get_service_status, start_service


async def run_health_monitor(interval: int = 300):  # 5 minutes
    """Runs health monitor loop."""
    paths = get_jarvis_paths()
    notifier = UpdateNotifier()
    
    print(f"[HealthMonitor] Starting monitor (interval: {interval}s)")
    
    consecutive_failures = 0
    max_failures = 3
    
    while True:
        try:
            # Run health check
            report = await run_health_check()
            
            if report.overall_healthy:
                consecutive_failures = 0
                print(f"[HealthMonitor] All checks OK ({time.strftime('%H:%M:%S')})")
            else:
                consecutive_failures += 1
                failed = [k for k, v in report.results.items() if not v.healthy]
                print(f"[HealthMonitor] Issues detected: {failed}")
                
                if consecutive_failures >= max_failures:
                    print(f"[HealthMonitor] {consecutive_failures} consecutive failures - alerting")
                    notifier = UpdateNotifier()
                    await notifier.notifiers[0].send(
                        "JARVIS Health Alert",
                        f"{consecutive_failures} consecutive health check failures: {', '.join(failed)}",
                        "warning"
                    )
                    
                    # Try to restart service if critical
                    if "service" in failed or "venv_exists" in failed:
                        print("[HealthMonitor] Attempting service restart...")
                        # start_service()  # Be careful with auto-restart
            
        except Exception as e:
            print(f"[HealthMonitor] Error: {e}")
        
        await asyncio.sleep(interval)


async def run_single_check() -> int:
    """Runs single health check, returns exit code."""
    try:
        report = await run_health_check()
        if report.overall_healthy:
            print("OK")
            return 0
        else:
            failed = [k for k, v in report.results.items() if not v.healthy]
            print(f"FAILED: {', '.join(failed)}")
            return 1
    except Exception as e:
        print(f"ERROR: {e}")
        return 2


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--interval", type=int, default=300, help="Check interval (seconds)")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()
    
    if args.once:
        return await run_single_check()
    elif args.daemon:
        await run_health_monitor(args.interval)
    else:
        return await run_single_check()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))