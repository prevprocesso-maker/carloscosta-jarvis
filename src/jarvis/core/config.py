from __future__ import annotations

from .bootstrap import AppSettings, load_config, create_application, Container

settings = load_config()

__all__ = ["AppSettings", "load_config", "create_application", "Container", "settings"]