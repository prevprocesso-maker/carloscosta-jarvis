from __future__ import annotations

"""Vault Manager - Handles Obsidian Vault integration."""

import asyncio
from pathlib import Path
from typing import Optional

from jarvis.core.config import settings
from jarvis.core.telemetry import get_telemetry
from jarvis.integrations import (
    ObsidianVaultManager,
    VaultRouter,
    IntentClassifier,
    Intent,
    create_vault_manager,
    VaultFileEvent,
)
from jarvis.core.knowledge import get_knowledge_base


class VaultManager:
    """Manages Obsidian Vault integration: CRUD, routing, sync."""

    def __init__(self, orchestrator_ref):
        self.orchestrator = orchestrator_ref
        self.settings = orchestrator_ref.settings
        self.telemetry = get_telemetry()
        self.knowledge_base = get_knowledge_base()

        self._vault_manager: Optional[ObsidianVaultManager] = None
        self._vault_router = None
        self._intent_classifier = None

    async def initialize(self):
        """Initialize vault integration."""
        await self._init_vault()

    async def _init_vault(self):
        """Inicializa integração com Vault (Obsidian)."""
        try:
            # Determina caminho do vault
            vault_path = getattr(self.settings, 'vault_path', None)
            if not vault_path:
                from jarvis.core.plataforma import get_jarvis_paths
                paths = get_jarvis_paths()
                vault_path = paths.data / "vault"
            else:
                vault_path = Path(vault_path)

            # Configurações do vault
            api_key = getattr(self.settings, 'vault_api_key', None)
            vault_host = getattr(self.settings, 'vault_host', 'localhost')
            vault_port = getattr(self.settings, 'vault_port', 27124)
            verify_ssl = getattr(self.settings, 'vault_verify_ssl', False)

            # Cria manager
            self._vault_manager = await create_vault_manager(
                vault_path=vault_path,
                api_key=api_key,
                host=vault_host,
                port=vault_port,
                verify_ssl=verify_ssl,
                knowledge_base=self.knowledge_base,
            )

            # Inicializa router
            self._vault_router = VaultRouter(vault_path)

            # Inicializa classificador de intents
            self._intent_classifier = IntentClassifier()

            # Registra handler de eventos do vault
            self._vault_manager.on_event(self._on_vault_event)

            print("[JARVIS] Vault (Obsidian) inicializado com sucesso")

        except Exception as e:
            print(f"Erro ao integrar Vault: {e}")
            raise

    @property
    def vault_manager(self):
        return self._vault_manager

    @property
    def vault_router(self):
        return self._vault_router

    @property
    def intent_classifier(self):
        return self._intent_classifier

    async def _on_vault_event(self, event) -> None:
        """Callback para eventos do watcher do vault."""
        self.orchestrator.telemetry.logger.debug(f"Vault event: {event.event_type.value} - {event.path}")

    async def sync_to_kb(self):
        """Sincroniza vault com Knowledge Base."""
        try:
            if self._vault_manager:
                count = await self._vault_manager.sync_to_knowledge_base()
                self.telemetry.logger.info(f"Vault sincronizado: {count} documentos")
        except Exception as e:
            self.telemetry.logger.error(f"Erro ao sincronizar vault: {e}")

    async def detect_intent(self, text: str):
        """Detecta intent do texto usando classificador."""
        if self._intent_classifier:
            return self._intent_classifier.classify_intent(text)
        return None

    async def route_to_vault(self, intent, text: str):
        """Roteia intent para ação no vault."""
        if not self._vault_router or not self._vault_manager:
            return

        try:
            route_result = self._vault_router.route(intent)
            if route_result.get("action") != "inbox":
                result = await self._vault_router.execute_route(route_result, self._vault_manager)
                # UI notification handled by orchestrator
        except Exception as e:
            print(f"Erro ao rotear para vault: {e}")

    async def process_text_command(self, text: str):
        """Processa comando de texto via UI."""
        if not text:
            return

        # Detecta intent
        intent = await self.detect_intent(text)

        if intent and intent.is_action:
            # Roteia para vault se for ação
            await self.route_to_vault(intent, text)
        else:
            # Processa via brain normalmente - delegate to orchestrator
            return "PROCESS_WITH_BRAIN"

    def _check_wake_word(self, text: str) -> bool:
        """Verifica se texto contém wake word."""
        wake_words = ["jarvis", "friday", "hey jarvis", "ok jarvis"]
        text_lower = text.lower()
        return any(w in text_lower for w in wake_words)