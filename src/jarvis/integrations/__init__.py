from __future__ import annotations

# Integrações externas: WhatsApp Bridge, etc.
from .whatsapp import (
    WhatsAppBridgeClient,
    WhatsAppBridgeManager,
    get_whatsapp_bridge,
    start_whatsapp_bridge,
)

__all__ = [
    "WhatsAppBridgeClient",
    "WhatsAppBridgeManager",
    "get_whatsapp_bridge",
    "start_whatsapp_bridge",
]