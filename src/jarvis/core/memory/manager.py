from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional
import sys

from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    value: str
    updated: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    category: str = "notes"


class MemoryStore(BaseModel):
    identity: dict[str, MemoryEntry] = Field(default_factory=dict)
    preferences: dict[str, MemoryEntry] = Field(default_factory=dict)
    projects: dict[str, MemoryEntry] = Field(default_factory=dict)
    relationships: dict[str, MemoryEntry] = Field(default_factory=dict)
    wishes: dict[str, MemoryEntry] = Field(default_factory=dict)
    notes: dict[str, MemoryEntry] = Field(default_factory=dict)


class MemoryManager:
    def __init__(self, settings):
        self.settings = settings
        self.base_dir = self._get_base_dir()
        self.memory_path = self.base_dir / "memory" / "long_term.json"
        self._lock = Lock()
        self.MAX_VALUE_LENGTH = 380
        self.MEMORY_MAX_CHARS = 2200
        self._ensure_memory_file()

    def _get_base_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).parent
        return Path(__file__).resolve().parent.parent.parent

    def _ensure_memory_file(self) -> None:
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.memory_path.exists():
            self.save(MemoryStore())

    def load(self) -> MemoryStore:
        with self._lock:
            try:
                data = json.loads(self.memory_path.read_text(encoding="utf-8"))
                return MemoryStore(**data)
            except Exception:
                return MemoryStore()

    def save(self, store: MemoryStore) -> None:
        store = self._trim_to_limit(store)
        with self._lock:
            self.memory_path.write_text(
                store.model_dump_json(indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def _trim_to_limit(self, store: MemoryStore) -> MemoryStore:
        if len(store.model_dump_json(ensure_ascii=False)) <= self.MEMORY_MAX_CHARS:
            return store

        entries = []
        for category in store.model_fields:
            items = getattr(store, category)
            for key, entry in items.items():
                entries.append((category, key, entry.updated))

        entries.sort(key=lambda x: x[2])
        for category, key, _ in entries:
            if len(store.model_dump_json(ensure_ascii=False)) <= self.MEMORY_MAX_CHARS:
                break
            getattr(store, category).pop(key, None)
        return store

    def _truncate(self, value: str) -> str:
        if len(value) > self.MAX_VALUE_LENGTH:
            return value[:self.MAX_VALUE_LENGTH].rstrip() + "…"
        return value

    def update(self, updates: dict) -> MemoryStore:
        store = self.load()
        changed = False
        for category, items in updates.items():
            if category not in store.model_fields:
                continue
            cat_dict = getattr(store, category)
            for key, value in items.items():
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                entry = MemoryEntry(
                    value=self._truncate(str(value)),
                    updated=datetime.now().strftime("%Y-%m-%d"),
                    category=category,
                )
                if cat_dict.get(key) != entry:
                    cat_dict[key] = entry
                    changed = True
        if changed:
            self.save(store)
        return store

    def get(self, category: str, key: str) -> Optional[str]:
        store = self.load()
        cat_dict = getattr(store, category, {})
        entry = cat_dict.get(key)
        return entry.value if entry else None

    def delete(self, category: str, key: str) -> bool:
        store = self.load()
        cat_dict = getattr(store, category, {})
        if key in cat_dict:
            cat_dict.pop(key)
            self.save(store)
            return True
        return False

    def format_for_prompt(self, store: Optional[MemoryStore] = None) -> str:
        if store is None:
            store = self.load()
        lines = []
        for category in store.model_fields:
            items = getattr(store, category)
            if not items:
                continue
            lines.append(f"{category.title()}:")
            for key, entry in list(items.items())[:15]:
                lines.append(f"  - {key.replace('_', ' ').title()}: {entry.value}")
        if not lines:
            return ""
        result = "[MEMÓRIA DO USUÁRIO — use naturalmente, não recite como lista]\n" + "\n".join(lines)
        return result[:1997] + "…\n" if len(result) > 2000 else result + "\n"

    def remember(self, key: str, value: str, category: str = "notes") -> str:
        if category not in store.model_fields:
            category = "notes"
        self.update({category: {key: value}})
        return f"Lembrado: {category}/{key} = {value}"

    def forget(self, key: str, category: str = "notes") -> str:
        if self.delete(category, key):
            return f"Esquecido: {category}/{key}"
        return f"Não encontrado: {category}/{key}"


store = None  # type: ignore