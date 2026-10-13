from __future__ import annotations

"""Orquestrador principal do JARVIS - Coordena managers e UI."""

import asyncio
import signal
import sys
from contextlib import asynccontextmanager

import uvicorn

from jarvis.bootstrap import create_application, ensure_config_files
from jarvis.core.config import settings
from jarvis.core.telemetry import get_telemetry, init_telemetry
from jarvis.core.guardrails import get_guardrails, init_guardrails
from jarvis.core.knowledge import get_knowledge_base, init_knowledge_base
from jarvis.core.session import SessionManager
from jarvis.core.actions.registry import get_openai_schemas, execute_action
from jarvis.core.memory import get_memory_manager
from jarvis.core.plataforma import get_base_dir
from jarvis.core.brain import get_brain_factory

from jarvis.ui import JarvisUI
from jarvis.managers import (
    VoiceLoopManager,
    TransportManager,
    VaultManager,
    GestureManager,
    IntentProcessor,
    UIManager,
    WhatsAppManager,
    PollTaskManager,
    AutoUpdater,
)


class JarvisOrchestrator:
    """Orquestrador principal que coordena managers e UI."""

    def __init__(self):
        self.settings = settings
        self.base_dir = get_base_dir()

        # Paths
        self.prompt_path = self.base_dir / "core" / "prompt.txt"
        self.voice_diag_path = self.base_dir / "logs" / "voice_session.log"

        # Core components
        self.ui = None
        self._brain_factory = None
        self.session_manager = SessionManager()
        self.session_mgr = self.session_manager
        self.memory_manager = get_memory_manager()
        self.telemetry = get_telemetry()
        self.guardrails = get_guardrails()
        self.knowledge_base = get_knowledge_base()

        # Managers
        self._voice_loop_manager = None
        self._transport_manager = None
        self._vault_manager = None
        self._gesture_manager = None
        self._intent_processor = None
        self._ui_manager = None
        self._whatsapp_manager = None
        self._poll_task_manager = None
        self._auto_updater = None

        # State
        self.running = False
        self.shutdown_event = asyncio.Event()

    @property
    def brain_factory(self):
        """Lazy load brain factory to avoid circular imports."""
        if self._brain_factory is None:
            from jarvis.core.brain import get_brain_factory
            self._brain_factory = get_brain_factory(self.settings, self.memory_manager)
        return self._brain_factory

    @property
    def voice_loop_manager(self):
        if self._voice_loop_manager is None:
            self._voice_loop_manager = VoiceLoopManager(self)
        return self._voice_loop_manager

    @property
    def transport_manager(self):
        if self._transport_manager is None:
            self._transport_manager = TransportManager(self)
        return self._transport_manager

    @property
    def vault_manager(self):
        if self._vault_manager is None:
            self._vault_manager = VaultManager(self)
        return self._vault_manager

    @property
    def gesture_manager(self):
        if self._gesture_manager is None:
            self._gesture_manager = GestureManager(self)
        return self._gesture_manager

    @property
    def intent_processor(self):
        if self._intent_processor is None:
            self._intent_processor = IntentProcessor(self)
        return self._intent_processor

    @property
    def ui_manager(self):
        if self._ui_manager is None:
            self._ui_manager = UIManager(self)
        return self._ui_manager

    @property
    def whatsapp_manager(self):
        if self._whatsapp_manager is None:
            self._whatsapp_manager = WhatsAppManager(self)
        return self._whatsapp_manager

    @property
    def poll_task_manager(self):
        if self._poll_task_manager is None:
            self._poll_task_manager = PollTaskManager(self)
        return self._poll_task_manager

    @property
    def auto_updater(self):
        if self._auto_updater is None:
            self._auto_updater = AutoUpdater(self)
        return self._auto_updater

    async def initialize_brains(self):
        """Initialize just the brain factory (for backward compatibility)."""
        self._brain_factory = get_brain_factory(self.settings, self.memory_manager)
        self._brain_factory.initialize_brains()
        print("[JARVIS] Cerebros inicializados")

    async def initialize(self):
        """Initialize all core components."""
        self._brain_factory = get_brain_factory(self.settings, self.memory_manager)
        self._brain_factory.initialize_brains()
        print("[JARVIS] Cerebros inicializados")

        # Initialize managers
        await self.vault_manager.initialize()
        await self.gesture_manager.initialize()
        await self.ui_manager.initialize()
        await self.whatsapp_manager.initialize()
        await self.poll_task_manager.start_all()
        
        # Start auto updater
        await self.auto_updater.start()

    async def start_all_transports(self):
        """Start all transports."""
        # Set up callbacks
        self.transport_manager.set_callbacks(
            audio_out_cb=self._on_audio_out,
            text_out_cb=self._on_text_out,
            tool_call_cb=self._on_tool_call,
        )

        await self.transport_manager.start_all(self.brain_factory, self.session_manager)
        await self.gesture_manager.start()

    async def _on_audio_out(self, chunk: bytes):
        if self.voice_loop_manager:
            await self.voice_loop_manager._speak(chunk)

    async def _on_text_out(self, text: str):
        if self.ui_manager and self.ui_manager.ui:
            self.ui_manager.ui.write_log(f"[JARVIS] {text}")

    async def _on_tool_call(self, tool_call):
        return await execute_action(tool_call.name, tool_call.args, self.session_manager)

    async def start_all(self):
        """Start all components."""
        await self.initialize()
        await self.start_all_transports()
        self.running = True

    async def shutdown(self):
        """Shutdown all components."""
        print("[JARVIS] Encerrando...")
        
        # Stop voice loop
        if self.voice_loop_manager:
            await self.voice_loop_manager.stop()

        # Stop transports
        if self.transport_manager:
            await self.transport_manager.stop_all()

        # Stop gesture manager
        if self.gesture_manager:
            await self.gesture_manager.stop()

        # Stop poll tasks
        if self.poll_task_manager:
            await self.poll_task_manager.stop_all()

        # Stop auto updater
        if self._auto_updater:
            await self.auto_updater.stop()

        # Close vault
        if self.vault_manager and self.vault_manager._vault_manager:
            await self.vault_manager._vault_manager.stop_watcher()
            await self.vault_manager._vault_manager._close_session()

        # Close WhatsApp
        if self.whatsapp_manager:
            await self.whatsapp_manager.shutdown()

        # Close UI
        if self.ui_manager and self.ui_manager.ui:
            self.ui_manager.ui.close()

        await self.telemetry.shutdown()

        print("[JARVIS] Encerrado com sucesso")

    def _check_wake_word(self, text: str) -> bool:
        """Verifica se texto contém wake word."""
        wake_words = ["jarvis", "friday", "hey jarvis", "ok jarvis"]
        text_lower = text.lower()
        return any(w in text_lower for w in wake_words)

    async def _process_with_brain(self, text: str):
        """Processa texto com brain e detecta intent para vault."""
        # Primeiro detecta intent
        intent = await self.intent_processor.detect_intent(text)

        if intent and intent.is_action:
            # Roteia para vault
            await self.intent_processor._route_to_vault(intent)
            return "Ação executada no vault."

        # Processa normalmente com brain
        brain = self.brain_factory.get_fast_brain()
        session = self.session_manager.get_or_create("local", "user", "main")
        session.add_message("user", text)

        async for response in brain.process(None, text_input=text):
            if response.text:
                session.add_message("assistant", response.text)
                return response.text

        return "Processado."

    def run(self):
        """Run the main application loop with UI."""
        self.running = True

        # Initialize UI via UIManager
        self.ui_manager.initialize()

        # Start voice loop
        if self.voice_loop_manager:
            self.voice_loop_manager._voice_loop_task = asyncio.create_task(self.voice_loop_manager._voice_loop())

        # Run UI event loop (blocking)
        try:
            import sys
            sys.exit(self.ui_manager.ui._app.exec())
        except KeyboardInterrupt:
            pass
        finally:
            asyncio.run(self.shutdown())

    # UI initialization and callbacks are now handled by UIManager
    # WhatsApp message/status handling is delegated to WhatsAppManager
    # Poll tasks are handled by PollTaskManager