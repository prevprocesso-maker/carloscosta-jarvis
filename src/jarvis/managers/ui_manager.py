from __future__ import annotations

"""UI Manager - Handles PyQt6 UI initialization and callbacks."""

import asyncio
from pathlib import Path
from typing import Optional, Callable, Tuple

from src.jarvis.core.config import settings
from src.jarvis.core.telemetry import get_telemetry
from src.jarvis.core.plataforma import get_base_dir


class UIManager:
    """Manages PyQt6 UI: initialization, callbacks, face, logs."""

    def __init__(self, orchestrator_ref):
        self.orchestrator = orchestrator_ref
        self.settings = orchestrator_ref.settings
        self.telemetry = get_telemetry()
        self.ui = None
        self._face_path = None

    def initialize(self):
        """Initialize UI."""
        self._init_ui()
        self._wire_ui_callbacks()

    def _init_ui(self) -> None:
        """Initialize the PyQt6 UI."""
        base_dir = get_base_dir()
        self._face_path = str(base_dir / "assets" / "jarvis_face.png")

        from src.jarvis.ui import JarvisUI
        self.ui = JarvisUI(face_path=self._face_path)
        # Store reference for backward compatibility
        self.ui.app = self.ui._app
        self.ui.root = self.ui.root

    def _wire_ui_callbacks(self) -> None:
        """Wire up UI callbacks to orchestrator/manager methods."""
        if not self.ui:
            return

        # Text command from UI (typed input)
        self.ui.on_text_command = self._on_ui_text_command

        # Remote button clicked
        self.ui.on_remote_clicked = self._on_ui_remote_clicked

        # WhatsApp button clicked
        self.ui.on_whatsapp_clicked = self._on_ui_whatsapp_clicked

        # Interrupt button (stop speaking)
        self.ui.on_interrupt = self._on_ui_interrupt

        # Computer Use button
        self.ui.on_computer_use = self._on_ui_computer_use

        # Computer Use estado
        self.ui.on_computer_use_estado = self._get_computer_use_estado

        # So com nome toggle
        self.ui.on_so_nome_changed = self._on_so_nome_changed

        # Cowork action
        self.ui.on_cowork_acao = self._on_cowork_acao

    # --- UI Callbacks ---

    def _on_ui_text_command(self, text: str) -> None:
        """Handle text command from UI."""
        if text:
            asyncio.create_task(self.orchestrator._process_text_command(text))

    def _on_ui_remote_clicked(self) -> Tuple[str, str] | None:
        """Handle remote button click - return (url, key)."""
        return self.orchestrator.transport_manager.get_remote_info()

    def _on_ui_whatsapp_clicked(self) -> None:
        """Handle WhatsApp button click."""
        if self.ui:
            self.ui.open_whatsapp_qr()

    def _on_ui_interrupt(self) -> None:
        """Handle interrupt button - stop current speech."""
        if self.orchestrator.voice_loop_manager and self.orchestrator.voice_loop_manager._voice_loop_task and not self.orchestrator.voice_loop_manager._voice_loop_task.done():
            self.orchestrator.voice_loop_manager._voice_loop_task.cancel()
        if hasattr(self.orchestrator, 'tts') and self.orchestrator.tts:
            try:
                asyncio.create_task(self.orchestrator.transport_manager.send_interrupt())
            except Exception:
                pass

    def _get_computer_use_estado(self) -> str:
        """Get Computer Use state for UI."""
        return "livre"

    def _on_ui_computer_use(self) -> None:
        """Handle Computer Use button click."""
        if self.ui:
            self.ui.write_log("Computer Use ativado")

    def _on_so_nome_changed(self, enabled: bool) -> None:
        """Handle 'Só com nome' toggle."""
        self.orchestrator.settings._config['ui']['so_com_nome'] = enabled
        if self.ui:
            self.ui.write_log(f"Modo 'só com nome': {'ligado' if enabled else 'desligado'}")

    def _on_cowork_acao(self, acao: str, valor: str = "") -> None:
        """Handle Cowork action from UI."""
        if self.ui:
            self.ui.write_log(f"Cowork: {acao} = {valor}")

    async def shutdown(self):
        """Close UI."""
        if self.ui:
            self.ui.close()