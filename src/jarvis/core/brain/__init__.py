from __future__ import annotations

from .base import Brain, BrainConfig, BrainContext, BrainResponse, ToolCall, BrainFactory
from .fast import FastBrain
from .gemini import GeminiBrain
from .openrouter import OpenRouterBrain

from .fast import FastBrain as _FastBrain
from .gemini import GeminiBrain as _GeminiBrain
from .openrouter import OpenRouterBrain as _OpenRouterBrain


class BrainFactory:
    """Factory for creating brain instances."""
    
    def __init__(self, settings, memory=None):
        self.settings = settings
        self.memory = memory
        self._fast_brain = None
        self._gemini_brain = None
        self._openrouter_brain = None
    
    def get_fast_brain(self) -> "FastBrain":
        if self._fast_brain is None:
            self._fast_brain = _FastBrain(self.settings, self.memory)
        return self._fast_brain
    
    def get_gemini_brain(self) -> "GeminiBrain":
        if self._gemini_brain is None:
            self._gemini_brain = _GeminiBrain(self.settings)
        return self._gemini_brain
    
    def get_openrouter_brain(self) -> "OpenRouterBrain":
        if self._openrouter_brain is None:
            self._openrouter_brain = _OpenRouterBrain(self.settings)
        return self._openrouter_brain
    
    def initialize_brains(self):
        """Pre-initialize all brain instances."""
        _ = self.get_fast_brain()
        _ = self.get_gemini_brain()
        _ = self.get_openrouter_brain()


def get_brain_factory(settings=None, memory=None):
    """Factory function to create a BrainFactory instance."""
    if settings is None:
        from jarvis.core.config import get_settings
        settings = get_settings()
    return BrainFactory(settings, memory)


__all__ = [
    "Brain",
    "BrainConfig",
    "BrainContext",
    "BrainResponse",
    "ToolCall",
    "BrainFactory",
    "FastBrain",
    "GeminiBrain",
    "OpenRouterBrain",
    "get_brain_factory",
]