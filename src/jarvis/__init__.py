from __future__ import annotations

__version__ = "2.0.0-dev"
__author__ = "Carlos Costa"

# Lazy imports to avoid circular imports
def get_jarvis_orchestrator():
    from .orchestrator import JarvisOrchestrator
    return JarvisOrchestrator

def get_voice_loop():
    from .voice_loop import VoiceLoop
    return VoiceLoop

def get_initialize_bootstrap():
    from .bootstrap import initialize_bootstrap
    return initialize_bootstrap


def __getattr__(name):
    if name == "JarvisOrchestrator":
        return get_jarvis_orchestrator()
    elif name == "VoiceLoop":
        return get_voice_loop()
    elif name == "initialize_bootstrap":
        return get_initialize_bootstrap()
    raise AttributeError(f"module 'jarvis' has no attribute '{name}'")


__all__ = ["JarvisOrchestrator", "VoiceLoop", "initialize_bootstrap"]