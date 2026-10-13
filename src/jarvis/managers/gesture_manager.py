from __future__ import annotations

"""Gesture Manager - Handles gesture recognition (local + remote)."""

import asyncio
from typing import Optional, List, Dict

from jarvis.core.telemetry import get_telemetry
from jarvis.transports.local import create_local_gesture_bridge, LocalGestureResult
from jarvis.core.config import settings


class GestureManager:
    """Manages gesture recognition: local (MediaPipe) and remote (WebSocket)."""

    def __init__(self, orchestrator_ref):
        self.orchestrator = orchestrator_ref
        self.telemetry = get_telemetry()
        self._gesture_bridge = None
        self._running = False

    async def initialize(self):
        """Initialize gesture recognition."""
        if not settings.transports.local.enabled:
            return

        try:
            gesture_cfg = settings.transports.local.gesture.to_dict() if hasattr(settings.transports.local, 'gesture') else {}
            from jarvis.transports.local import create_local_gesture_bridge
            self._bridge = await create_local_gesture_bridge(gesture_config=gesture_cfg)
            self._bridge.set_orchestrator(self)
            await self._bridge.start()
            print("[JARVIS] Local Gesture Recognition iniciado")
        except Exception as e:
            print(f"Erro ao iniciar Local Gesture Recognition: {e}")

    @property
    def bridge(self):
        return self._bridge

    async def start(self):
        if self._bridge:
            await self._bridge.start()

    async def stop(self):
        if self._bridge:
            await self._bridge.stop()

    async def on_local_gesture(self, result):
        """Callback para gestos detectados localmente (Desktop)."""
        gesture = result.gesture.value
        confidence = result.confidence
        handedness = result.handedness

        self.telemetry.logger.debug(f"Local gesture: {gesture} ({handedness}) confidence={confidence:.2f}")

        # Mapeia gesto para ação
        if gesture == "fist":
            # Fecha painel / cancela ação
            print("Gesto: Punho fechado - fechando painel")
        
        elif gesture == "v_sign":
            # Toggle microfone
            await self._signal_interrupt()
        
        elif gesture == "thumbs_up":
            # Confirma ação pendente
            print("Gesto: Joinha - confirmado")
        
        elif gesture == "thumbs_down":
            # Cancela ação pendente
            print("Gesto: Joinha pra baixo - cancelado")

    async def on_local_hand_landmarks(self, landmarks: list[dict], handedness: str):
        """Callback para landmarks da mão (cursor virtual, etc.)."""
        pass

    async def _signal_interrupt(self):
        """Signal interrupt to active transports."""
        # Delegate to transport manager
        pass

    async def on_remote_gesture(self, gesture: str, confidence: float, handedness: str):
        """Callback para gestos remotos (WebSocket)."""
        self.telemetry.logger.debug(f"Remote gesture: {gesture} ({handedness}) confidence={confidence:.2f}")
        
        # Map gesture to action
        if gesture == "fist":
            # Close panel / cancel action
            pass
        elif gesture == "v_sign":
            # Toggle microphone
            pass
        elif gesture == "thumbs_up":
            # Confirm pending action
            pass
        elif gesture == "thumbs_down":
            # Cancel pending action
            pass