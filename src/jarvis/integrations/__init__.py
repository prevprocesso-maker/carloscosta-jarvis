from __future__ import annotations

# Integrações externas: WhatsApp Bridge, Obsidian Vault, etc.
from .whatsapp import (
    WhatsAppBridgeClient,
    WhatsAppBridgeManager,
    get_whatsapp_bridge,
    start_whatsapp_bridge,
)
from .obsidian_vault import (
    ObsidianVaultManager,
    VaultFileEvent,
    VaultFileEventHandler,
    VaultNote,
    create_vault_manager,
)
from .vault_constitution import (
    VaultBriefing,
    VaultConstitutionGenerator,
)
from .vault_setup import (
    VaultSetup,
    VaultFolder,
)
from .vault_router import (
    Intent,
    IntentType,
    IntentClassifier,
    VaultRouter,
    VaultRouteResult,
)
from .opensquad_bridge import (
    OpenSquadSkill,
    OpenSquadSkillsRegistry,
    get_opensquad_registry,
    init_opensquad_skills,
    get_opensquad_action_schemas,
    execute_opensquad_skill,
)

__all__ = [
    # WhatsApp
    "WhatsAppBridgeClient",
    "WhatsAppBridgeManager",
    "get_whatsapp_bridge",
    "start_whatsapp_bridge",
    # Obsidian Vault
    "ObsidianVaultManager",
    "VaultFileEvent",
    "VaultFileEventHandler",
    "VaultNote",
    "create_vault_manager",
    "VaultBriefing",
    "VaultConstitutionGenerator",
    "VaultSetup",
    "VaultFolder",
    "Intent",
    "IntentType",
    "IntentClassifier",
    "VaultRouter",
    "VaultRouteResult",
    # OpenSquad Skills
    "OpenSquadSkill",
    "OpenSquadSkillsRegistry",
    "get_opensquad_registry",
    "init_opensquad_skills",
    "get_opensquad_action_schemas",
    "execute_opensquad_skill",
]