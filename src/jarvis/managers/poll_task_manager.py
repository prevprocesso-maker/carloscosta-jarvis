from __future__ import annotations

"""Poll Task Manager - Handles periodic background tasks."""

import asyncio
import psutil
from typing import Optional, Callable, Awaitable

from src.jarvis.core.telemetry import get_telemetry
from src.jarvis.core.config import settings


class PollTaskManager:
    """Manages periodic background tasks (polling)."""

    def __init__(self, orchestrator_ref):
        self.orchestrator = orchestrator_ref
        self.telemetry = get_telemetry()
        self._poll_tasks = []
        self._running = False

    def add_task(self, coro_func: Callable[[], Awaitable[None]], interval: float, name: str = None):
        """Add a periodic task."""
        task_name = name or coro_func.__name__
        task = asyncio.create_task(self._run_periodic(coro_func, interval, task_name))
        self._poll_tasks.append(task)

    async def _run_periodic(self, coro_func: Callable[[], Awaitable[None]], interval: float, name: str):
        """Run a coroutine periodically."""
        while True:
            try:
                await coro_func()
            except Exception as e:
                # Log but don't stop other tasks
                print(f"[PollTask:{name}] Error: {e}")
            
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def start_all(self):
        """Start all standard poll tasks."""
        self.add_task(self._poll_whatsapp_alerts, 30, "whatsapp_alerts")
        self.add_task(self._poll_due_scheduled_messages, 60, "scheduled_messages")
        self.add_task(self._poll_whatsapp_agent_events, 10, "whatsapp_agent_events")
        self.add_task(self._poll_system_monitor, 60, "system_monitor")
        self.add_task(self._poll_vault_sync, 300, "vault_sync")

    async def stop_all(self):
        """Stop all poll tasks."""
        for task in self._poll_tasks:
            task.cancel()
        if self._poll_tasks:
            await asyncio.gather(*self._poll_tasks, return_exceptions=True)
        self._poll_tasks.clear()

    # --- Default Poll Tasks ---

    async def _poll_whatsapp_alerts(self):
        """Poll for WhatsApp alerts."""
        # Check for unread messages or connection issues
        if self.orchestrator.whatsapp_manager and self.orchestrator.whatsapp_manager.is_connected:
            try:
                # Could check for unread messages, connection health, etc.
                pass
            except Exception as e:
                self.telemetry.logger.debug(f"WhatsApp alerts poll error: {e}")

    async def _poll_due_scheduled_messages(self):
        """Poll for due scheduled messages."""
        # Check for scheduled messages that are due to be sent
        try:
            # TODO: Implement scheduled message checking
            pass
        except Exception as e:
            self.telemetry.logger.debug(f"Scheduled messages poll error: {e}")

    async def _poll_whatsapp_agent_events(self):
        """Poll for WhatsApp agent events."""
        # Check for agent events from WhatsApp
        try:
            # TODO: Implement agent event polling
            pass
        except Exception as e:
            self.telemetry.logger.debug(f"WhatsApp agent events poll error: {e}")

    async def _poll_system_monitor(self):
        """Monitor system resources."""
        try:
            import platform
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            # Use correct path for Windows vs Unix
            import platform as platform_module
            if platform_module.system() == "Windows":
                disk = psutil.disk_usage('C:\\')
            else:
                disk = psutil.disk_usage('/')
            
            # Log if resources are critical
            if cpu_percent > 90:
                self.telemetry.logger.warning(f"High CPU usage: {cpu_percent}%")
            if memory.percent > 90:
                self.telemetry.logger.warning(f"High memory usage: {memory.percent}%")
            if disk.percent > 90:
                self.telemetry.logger.warning(f"High disk usage: {disk.percent}%")
                
        except Exception as e:
            self.telemetry.logger.debug(f"System monitor error: {e}")

    async def _poll_vault_sync(self):
        """Periodic Vault sync with Knowledge Base."""
        try:
            if self.orchestrator.vault_manager:
                await self.orchestrator.vault_manager.sync_to_kb()
        except Exception as e:
            self.telemetry.logger.error(f"Vault sync error: {e}")