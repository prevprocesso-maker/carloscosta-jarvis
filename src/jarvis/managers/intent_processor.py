from __future__ import annotations

"""Intent Processor - Handles intent detection and routing."""

from typing import Optional

from jarvis.core.telemetry import get_telemetry
from jarvis.integrations import IntentClassifier, Intent, IntentType
from jarvis.integrations import VaultRouter


class IntentProcessor:
    """Processes intents: detection, classification, and routing."""

    def __init__(self, orchestrator_ref):
        self.orchestrator = orchestrator_ref
        self.telemetry = get_telemetry()
        
        self._intent_classifier = IntentClassifier()
        self._vault_router = None
        self._vault_manager = None

    def set_vault_components(self, vault_router, vault_manager):
        """Set vault components for routing."""
        self._vault_router = vault_router
        self._vault_manager = vault_manager

    async def detect_intent(self, text: str):
        """Detecta intent do texto usando classificador."""
        return self._intent_classifier.classify_intent(text)

    def _check_wake_word(self, text: str) -> bool:
        """Verifica se texto contém wake word."""
        wake_words = ["jarvis", "friday", "hey jarvis", "ok jarvis"]
        text_lower = text.lower()
        return any(w in text_lower for w in wake_words)

    async def process_text_command(self, text: str):
        """Processa comando de texto via UI."""
        if not text:
            return

        # Detecta intent
        intent = await self.detect_intent(text)

        if intent and intent.is_action:
            # Roteia para vault se for ação
            await self._route_to_vault(intent)
        else:
            # Processa via brain normalmente
            return "PROCESS_WITH_BRAIN"

    async def _route_to_vault(self, intent):
        """Roteia intent para ação no vault."""
        if not self._vault_router or not self._vault_manager:
            return

        try:
            route_result = self._vault_router.route(intent)
            if route_result.get("action") != "inbox":
                result = await self._vault_router.execute_route(route_result, self._vault_manager)
                if result.get("success"):
                    print(f"Vault: {result.get('message', 'Ação executada')}")
        except Exception as e:
            print(f"Erro ao rotear para vault: {e}")

    def _check_wake_word(self, text: str) -> bool:
        """Verifica se texto contém wake word."""
        wake_words = ["jarvis", "friday", "hey jarvis", "ok jarvis"]
        text_lower = text.lower()
        return any(w in text_lower for w in wake_words)

    async def process_with_brain(self, text: str):
        """Processa texto com brain e detecta intent para vault."""
        # Primeiro detecta intent
        intent = await self.detect_intent(text)

        if intent and intent.is_action:
            # Roteia para vault
            await self._route_to_vault(intent)
            return "Ação executada no vault."

        # Processa normalmente com brain - delegate to orchestrator's brain
        return "PROCESS_WITH_BRAIN"

    def _check_wake_word(self, text: str) -> bool:
        """Verifica se texto contém wake word."""
        wake_words = ["jarvis", "friday", "hey jarvis", "ok jarvis"]
        text_lower = text.lower()
        return any(w in text_lower for w in wake_words)

    async def process_with_brain(self, text: str):
        """Processa texto com brain e detecta intent para vault."""
        # Primeiro detecta intent
        intent = await self.detect_intent(text)

        if intent and intent.is_action:
            # Roteia para vault
            await self._route_to_vault(intent)
            return "Ação executada no vault."

        # Processa normalmente com brain - delegate to orchestrator
        return "PROCESS_WITH_BRAIN"