from __future__ import annotations

import pytest
import tempfile
import os
from jarvis.core.memory.manager import MemoryManager, MemoryStore


class MockSettings:
    pass


def test_memory_manager_basic():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Patch base_dir
        import jarvis.core.memory.manager as mem_module
        original_get_base_dir = mem_module.MemoryManager._get_base_dir

        def mock_get_base_dir(self):
            from pathlib import Path
            return Path(tmpdir)

        mem_module.MemoryManager._get_base_dir = mock_get_base_dir

        try:
            settings = MockSettings()
            manager = MemoryManager(settings)

            # Teste update
            store = manager.update({"identity": {"nome": "Raphael"}})
            assert manager.get("identity", "nome") == "Raphael"

            # Teste format_for_prompt
            prompt = manager.format_for_prompt(store)
            assert "Identity" in prompt  # category.title() produces "Identity"
            assert "Raphael" in prompt

            # Teste delete
            assert manager.delete("identity", "nome") is True
            assert manager.get("identity", "nome") is None
        finally:
            mem_module.MemoryManager._get_base_dir = original_get_base_dir


def test_memory_truncation():
    with tempfile.TemporaryDirectory() as tmpdir:
        import jarvis.core.memory.manager as mem_module
        original_get_base_dir = mem_module.MemoryManager._get_base_dir

        def mock_get_base_dir(self):
            from pathlib import Path
            return Path(tmpdir)

        mem_module.MemoryManager._get_base_dir = mock_get_base_dir

        try:
            settings = MockSettings()
            manager = MemoryManager(settings)

            long_value = "x" * 500
            store = manager.update({"notes": {"longa": long_value}})
            assert manager.get("notes", "longa").endswith("…")
            assert len(manager.get("notes", "longa")) <= 380 + 3  # 380 + "…"
        finally:
            mem_module.MemoryManager._get_base_dir = original_get_base_dir


def test_memory_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        import jarvis.core.memory.manager as mem_module
        original_get_base_dir = mem_module.MemoryManager._get_base_dir

        def mock_get_base_dir(self):
            from pathlib import Path
            return Path(tmpdir)

        mem_module.MemoryManager._get_base_dir = mock_get_base_dir

        try:
            settings = MockSettings()
            manager1 = MemoryManager(settings)
            manager1.update({"preferences": {"tema": "escuro"}})

            # Nova instância deve carregar o mesmo arquivo
            manager2 = MemoryManager(settings)
            assert manager2.get("preferences", "tema") == "escuro"
        finally:
            mem_module.MemoryManager._get_base_dir = original_get_base_dir