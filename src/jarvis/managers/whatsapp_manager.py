from __future__ import annotations

"""WhatsApp Manager - Handles WhatsApp Bridge integration."""

import asyncio
from pathlib import Path
from typing import Optional

from src.jarvis.core.config import settings
from src.jarvis.core.telemetry import get_telemetry
from src.jarvis.core.plataforma import get_base_dir


class WhatsAppManager:
    """Manages WhatsApp Bridge: connection, messages, QR, status."""

    def __init__(self, orchestrator_ref):
        self.orchestrator = orchestrator_ref
        self.settings = orchestrator_ref.settings
        self.telemetry = get_telemetry()
        self._whatsapp_bridge = None

    async def initialize(self):
        """Initialize WhatsApp Bridge if enabled."""
        if not self.settings.transports.webhook.enabled:
            return

        try:
            from src.jarvis.integrations import start_whatsapp_bridge
            bridge_dir = get_base_dir() / "whatsapp_bridge"
            self._whatsapp_bridge = await start_whatsapp_bridge(
                port=self.settings.transports.webhook.whatsapp_port,
                bridge_dir=bridge_dir,
            )
            self._whatsapp_bridge.set_message_callback(self._on_whatsapp_message)
            self._whatsapp_bridge.set_status_callback(self._on_whatsapp_status)
            print("[JARVIS] WhatsApp Bridge iniciado")
        except Exception as e:
            print(f"Erro ao iniciar WhatsApp Bridge: {e}")
            if not self.settings.transports.webhook.get("optional", False):
                raise

    @property
    def bridge(self):
        return self._whatsapp_bridge

    @property
    def is_connected(self) -> bool:
        return self._whatsapp_bridge and self._whatsapp_bridge.is_connected

    async def shutdown(self):
        """Close WhatsApp Bridge."""
        if self._whatsapp_bridge:
            try:
                await self._whatsapp_bridge.close()
            except Exception as e:
                print(f"Erro ao fechar WhatsApp Bridge: {e}")

    async def _on_whatsapp_message(self, message: dict) -> None:
        """Handle incoming WhatsApp message."""
        from_number = message.get("from", "")
        text = message.get("text", "")

        # Log to UI
        if hasattr(self, '_ui') and self._ui:
            # This will be set by orchestrator
            pass

        if self.orchestrator.ui:
            self.orchestrator.ui.write_log(f"WhatsApp de {from_number}: {text}")

        if text.startswith("jarvis") or text.startswith("JARVIS"):
            cmd = text[6:].strip()
            # Process command through orchestrator
            await self._process_whatsapp_command(cmd, from_number)

    async def _process_whatsapp_command(self, cmd: str, from_number: str):
        """Process WhatsApp command through brain."""
        # Process via intent processor
        await self.orchestrator._process_text_command(cmd)

        # Send response back via WhatsApp
        if self._whatsapp_bridge and self._whatsapp_bridge.is_connected:
            from src.jarvis.core.brain import get_brain_factory
            brain = self._get_brain_factory().get_fast_brain()
            session = self.orchestrator.session_manager.get_or_create("whatsapp", from_number, from_number)
            session.add_message("user", cmd)

            async for response in brain.process(None, text_input=cmd):
                if response.text:
                    session.add_message("assistant", response.text)
                    await self._whatsapp_bridge.send_message(from_number, response.text)
                    break

    def _get_brain_factory(self):
        if self._brain_factory is None:
            from src.jarvis.core.brain import get_brain_factory
            from src.jarvis.core.config import settings
            from src.jarvis.core.memory import get_memory_manager
            self._brain_factory = get_brain_factory(settings, get_memory_manager())
        return self._brain_factory

    async def _on_whatsapp_status(self, status: dict):
        """Handle WhatsApp connection status change."""
        connected = status.get("connected", False)
        qr_code = status.get("qr_code")

        if self.orchestrator.ui:
            if connected:
                self.orchestrator.ui.write_log("WhatsApp conectado")
                self.orchestrator.ui.notify_phone_connected()
            else:
                self.orchestrator.ui.write_log("WhatsApp desconectado")
                if qr_code:
                    self.orchestrator.ui.write_log("Novo QR Code gerado")

    async def get_qr_code(self):
        """Get QR code for pairing."""
        if self._whatsapp_bridge:
            return await self._whatsapp_bridge.get_qr_code()
        return None

    async def get_contacts(self):
        """Get WhatsApp contacts."""
        if self._whatsapp_bridge:
            return await self._whatsapp_bridge.get_contacts()
        return {"contacts": []}