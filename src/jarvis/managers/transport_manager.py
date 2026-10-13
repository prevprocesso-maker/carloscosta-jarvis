from __future__ import annotations

"""Transport Manager - Handles all transports (local, remote, webhook)."""

import asyncio
import os
from pathlib import Path
from typing import Optional

from jarvis.core.config import settings
from jarvis.core.telemetry import get_telemetry
from jarvis.core.session import SessionManager


class TransportManager:
    """Manages all transports: local (desktop), remote (web), webhook (WhatsApp)."""

    def __init__(self, orchestrator_ref):
        self.orchestrator = orchestrator_ref
        self.settings = orchestrator_ref.settings
        self.telemetry = get_telemetry()
        
        # Transport components
        self._transport_local = None
        self._transport_remote = None
        self._whatsapp_bridge = None
        
        # Callbacks for transport output
        self._audio_out_callback = None
        self._text_out_callback = None
        self._tool_call_callback = None
        
        # Session manager
        self.session_manager = SessionManager()

    def set_callbacks(self, audio_out_cb, text_out_cb, tool_call_cb):
        """Set output callbacks."""
        self._audio_out_callback = audio_out_cb
        self._text_out_callback = text_out_cb
        self._tool_call_callback = tool_call_cb

    @property
    def transport_local(self):
        return self._transport_local

    @property
    def transport_remote(self):
        return self._transport_remote

    @property
    def whatsapp_bridge(self):
        return self._whatsapp_bridge

    async def start_all(self, brain_factory, session_manager: SessionManager):
        """Start all enabled transports."""
        self.session_manager = session_manager
        
        # Callbacks para output
        async def on_audio_out(chunk: bytes):
            if self._audio_out_callback:
                await self._audio_out_callback(chunk)

        async def on_text_out(text: str):
            if self._text_out_callback:
                await self._text_out_callback(text)

        async def on_tool_call(tool_call):
            if self._tool_call_callback:
                return await self._tool_call_callback(tool_call)
            return {}

        # Inicia transport local (Desktop)
        if self.settings.transports.local.enabled:
            try:
                from jarvis.transports.local import LocalAudioTransport
                brain = self.orchestrator.brain_factory.get_fast_brain()
                test_mode = os.environ.get('QT_QPA_PLATFORM') == 'offscreen'
                self._transport_local = LocalAudioTransport(
                    brain=brain,
                    session_mgr=session_manager,
                    test_mode=True,  # test_mode for now
                )
                await self._transport_local.start(
                    on_audio_out=on_audio_out,
                    on_text_out=on_text_out,
                    on_tool_call=on_tool_call,
                )
                print("[JARVIS] Transport local iniciado")
            except Exception as e:
                self.telemetry.logger.exception("Falha ao iniciar transport local (Desktop)")
                if not self.settings.transports.local.get("optional", False):
                    raise

        # Inicia transport remoto (Web)
        if self.settings.transports.remote.enabled:
            try:
                from jarvis.transports.remote import create_remote_voice_server
                self._transport_remote = create_remote_voice_server(
                    brain_factory=self.orchestrator.brain_factory,
                    session_mgr=session_manager,
                )
                print("[JARVIS] Transport remoto configurado")
            except Exception as e:
                self.telemetry.logger.exception("Falha ao configurar transport remoto (Web)")
                if not self.settings.transports.remote.get("optional", False):
                    raise

        # Inicia WhatsApp Bridge
        if self.settings.transports.webhook.enabled:
            try:
                from jarvis.integrations import start_whatsapp_bridge
                from jarvis.core.plataforma import get_base_dir
                bridge_dir = get_base_dir() / "whatsapp_bridge"
                self._whatsapp_bridge = await start_whatsapp_bridge(
                    port=self.settings.transports.webhook.whatsapp_port,
                    bridge_dir=bridge_dir,
                )
                self._whatsapp_bridge.set_message_callback(self.orchestrator._on_whatsapp_message)
                self._whatsapp_bridge.set_status_callback(self.orchestrator._on_whatsapp_status)
                print("[JARVIS] WhatsApp Bridge iniciado")
            except Exception as e:
                self.telemetry.logger.exception("Falha ao iniciar WhatsApp Bridge")
                if not self.settings.transports.webhook.get("optional", False):
                    raise

    async def stop_all(self):
        """Stop all transports."""
        if self._transport_local:
            try:
                await self._transport_local.stop()
            except Exception as e:
                print(f"Erro ao parar transport local: {e}")

        if self._transport_remote:
            try:
                await self._transport_remote.stop()
            except Exception as e:
                print(f"Erro ao parar transport remoto: {e}")

        if self._whatsapp_bridge:
            try:
                await self._whatsapp_bridge.close()
            except Exception as e:
                print(f"Erro ao fechar WhatsApp Bridge: {e}")

    async def send_interrupt(self):
        """Signal interrupt to active transports."""
        if self._transport_local and hasattr(self._transport_local, 'send_interrupt'):
            await self._transport_local.send_interrupt()
        if self._transport_remote and hasattr(self._transport_remote, 'send_interrupt'):
            await self._transport_remote.send_interrupt()

    def get_remote_info(self) -> tuple[str, str] | None:
        """Get remote transport info for UI."""
        if self._transport_remote:
            port = self.settings.transports.remote.port
            token = self.settings.transports.remote.token or ""
            return (f"http://localhost:{port}", token)
        return None

    # WhatsApp callbacks (delegated to orchestrator)
    async def _on_whatsapp_message(self, message: dict):
        """Handle incoming WhatsApp message."""
        # Delegate to orchestrator
        pass

    async def _on_whatsapp_status(self, status: dict):
        """Handle WhatsApp connection status change."""
        pass