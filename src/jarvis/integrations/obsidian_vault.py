from __future__ import annotations

"""Obsidian Vault Integration - CRUD + Watcher + Local REST API Client."""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
import aiohttp
import aiofiles
import struct
import uuid
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.knowledge import KnowledgeBase
    from ..core.brain.base import Brain


class VaultEventType(Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"


@dataclass
class VaultFileEvent:
    event_type: VaultEventType
    path: str
    old_path: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    is_directory: bool = False


class VaultFileEventHandler(FileSystemEventHandler):
    """Watchdog handler para eventos do vault."""

    def __init__(self, callback: Callable[[VaultFileEvent], None], vault_root: Path):
        self.callback = callback
        self.vault_root = vault_root
        self._debounce: Dict[str, float] = {}

    def _should_process(self, path: str) -> bool:
        """Debounce rapid events."""
        now = time.time()
        last = self._debounce.get(path, 0)
        if now - last < 0.5:
            return False
        self._debounce[path] = now
        return True

    def _make_event(self, event_type: VaultEventType, src_path: str, dest_path: str = None) -> VaultFileEvent:
        rel_path = os.path.relpath(src_path, self.vault_root)
        return VaultFileEvent(
            event_type=event_type,
            path=rel_path,
            old_path=os.path.relpath(dest_path, self.vault_root) if dest_path else None,
            is_directory=os.path.isdir(src_path)
        )

    def on_created(self, event):
        if not event.is_directory and self._should_process(event.src_path):
            self.callback(self._make_event(VaultEventType.CREATED, event.src_path))

    def on_modified(self, event):
        if not event.is_directory and self._should_process(event.src_path):
            self.callback(self._make_event(VaultEventType.MODIFIED, event.src_path))

    def on_deleted(self, event):
        if not event.is_directory:
            self.callback(self._make_event(VaultEventType.DELETED, event.src_path))

    def on_moved(self, event):
        if not event.is_directory:
            self.callback(self._make_event(VaultEventType.MOVED, event.dest_path, event.src_path))


@dataclass
class VaultNote:
    """Representa uma nota no vault Obsidian."""
    path: str
    content: str
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()

    def to_markdown(self) -> str:
        """Converte para markdown com frontmatter."""
        import yaml
        if self.frontmatter:
            fm = yaml.dump(self.frontmatter, allow_unicode=True)
            return f"---\n{fm}---\n\n{self.content}"
        return self.content

    @classmethod
    def from_markdown(cls, path: str, markdown: str) -> "VaultNote":
        """Cria VaultNote a partir de markdown com frontmatter."""
        import yaml
        if markdown.startswith("---"):
            parts = markdown.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1]) or {}
                content = parts[2].strip()
                return cls(path=path, content=content, frontmatter=frontmatter)
        return cls(path=path, content=markdown)


class ObsidianVaultManager:
    """Gerenciador completo do vault Obsidian com CRUD, Watcher e REST API."""

    def __init__(
        self,
        vault_path: Path,
        api_key: Optional[str] = None,
        host: str = "localhost",
        port: int = 27124,
        verify_ssl: bool = True,
        knowledge_base: Optional["KnowledgeBase"] = None,
    ):
        self.vault_path = Path(vault_path)
        self.api_key = api_key
        self.host = host
        self.port = port
        self.verify_ssl = verify_ssl
        if not verify_ssl:
            import warnings
            warnings.warn(
                "SSL verification disabled for Obsidian Vault connection. "
                "This is insecure and should only be used for local development with self-signed certificates. "
                "Set verify_ssl=True in production.",
                UserWarning,
                stacklevel=2
            )
        self.base_url = f"https://{host}:{port}" if verify_ssl else f"http://{host}:{port}"
        self.knowledge_base = knowledge_base
        self._observer: Optional[Observer] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._event_handlers: List[Callable[[VaultFileEvent], None]] = []

    async def _init_session(self):
        if self._session is None or self._session.closed:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            connector = aiohttp.TCPConnector(verify_ssl=self.verify_ssl)
            self._session = aiohttp.ClientSession(headers=headers, connector=connector)

    async def _close_session(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        await self._init_session()
        url = f"{self.base_url}{endpoint}"
        async with self._session.request(method, url, **kwargs) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise RuntimeError(f"Obsidian API error {resp.status}: {text}")
            if resp.content_type == "application/json":
                return await resp.json()
            return await resp.text()

    def _parse_frontmatter(self, content: str) -> Tuple[Dict, str]:
        """Parse YAML frontmatter from markdown content."""
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                import yaml
                frontmatter = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
                return frontmatter, body
        return {}, content

    async def read_note(self, path: str) -> Optional[Dict]:
        """Lê uma nota do vault."""
        try:
            result = await self._request("GET", f"/vault/{path}")
            if isinstance(result, str):
                frontmatter, body = self._parse_frontmatter(result)
                return {"frontmatter": frontmatter, "content": body, "path": path}
            return result
        except Exception as e:
            print(f"[Vault] Error reading {path}: {e}")
            return None

    async def write_note(self, path: str, content: str, frontmatter: Optional[Dict] = None) -> bool:
        """Escreve uma nota no vault."""
        try:
            full_content = content
            if frontmatter:
                import yaml
                fm_str = yaml.dump(frontmatter, allow_unicode=True)
                full_content = f"---\n{fm_str}---\n\n{content}"
            await self._request("PUT", f"/vault/{path}", data=full_content)
            return True
        except Exception as e:
            print(f"[Vault] Error writing {path}: {e}")
            return False

    async def archive_note(self, path: str) -> bool:
        """Arquiva uma nota movendo para arquivo/ com timestamp."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            archive_path = f"arquivo/{timestamp}-{path}"
            note = await self.read_note(path)
            if note:
                await self.write_note(archive_path, note.get("content", ""), note.get("frontmatter", {}))
            await self._request("DELETE", f"/vault/{path}")
            return True
        except Exception as e:
            print(f"[Vault] Error archiving {path}: {e}")
            return False

    async def delete_note(self, path: str) -> bool:
        """Deleta permanentemente uma nota do vault (não arquiva)."""
        try:
            await self._request("DELETE", f"/vault/{path}")
            return True
        except Exception as e:
            print(f"[Vault] Error deleting {path}: {e}")
            return False

    async def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Busca no vault via API."""
        try:
            result = await self._request("POST", "/search", json={"query": query, "limit": limit})
            return result.get("results", [])
        except Exception as e:
            print(f"[Vault] Search error: {e}")
            return []

    async def list_files(self, path: str = "") -> List[Dict]:
        """Lista arquivos no vault."""
        try:
            result = await self._request("GET", f"/vault/{path}")
            return result if isinstance(result, list) else []
        except Exception as e:
            print(f"[Vault] List error: {e}")
            return []

    async def add_file(self, file_path: Path, dest_path: Optional[str] = None) -> bool:
        """Adiciona arquivo ao vault."""
        try:
            content = file_path.read_text(encoding="utf-8")
            target = dest_path or file_path.name
            return await self.write_note(target, content)
        except Exception as e:
            print(f"[Vault] Add file error: {e}")
            return False

    async def add_directory(self, dir_path: Path, dest_prefix: str = "") -> int:
        """Adiciona diretório inteiro ao vault."""
        count = 0
        for file_path in dir_path.rglob("*"):
            if file_path.is_file():
                rel = file_path.relative_to(dir_path)
                target = f"{dest_prefix}/{rel}" if dest_prefix else str(rel)
                if await self.add_file(file_path, target):
                    count += 1
        return count

    async def sync_to_knowledge_base(self, patterns: List[str] = None) -> int:
        """Sincroniza vault com Knowledge Base."""
        if not self.knowledge_base:
            print("[Vault] No knowledge base configured")
            return 0

        patterns = patterns or ["**/*.md", "**/*.txt", "**/*.py"]
        count = 0

        for pattern in patterns:
            for file_path in self.vault_path.glob(pattern):
                if file_path.is_file():
                    try:
                        await self.knowledge_base.add_file(file_path)
                        count += 1
                    except Exception as e:
                        print(f"[Vault] Sync error for {file_path}: {e}")
        return count

    def _on_file_event(self, event: VaultFileEvent):
        """Callback para eventos do watchdog."""
        asyncio.create_task(self._event_queue.put(event))

    async def start_watcher(self):
        """Inicia watcher de arquivos."""
        if self._running:
            return

        self._running = True
        self._observer = Observer()
        handler = VaultFileEventHandler(self._on_file_event, self.vault_path)
        self._observer.schedule(handler, str(self.vault_path), recursive=True)
        self._observer.start()
        print(f"[Vault] Watcher started on {self.vault_path}")

    async def stop_watcher(self):
        """Para watcher de arquivos."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._running = False
        print("[Vault] Watcher stopped")

    async def process_events(self):
        """Processa eventos da fila (chamar em loop)."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                if self.knowledge_base:
                    await self._sync_event_to_kb(event)
                for handler in self._event_handlers:
                    handler(event)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"[Vault] Event processing error: {e}")

    async def _sync_event_to_kb(self, event: VaultFileEvent):
        """Sincroniza evento individual com KB."""
        if event.is_directory:
            return

        file_path = self.vault_path / event.path

        if event.event_type in (VaultEventType.CREATED, VaultEventType.MODIFIED):
            if file_path.exists():
                try:
                    await self.knowledge_base.add_file(file_path)
                except Exception as e:
                    print(f"[Vault] KB sync error: {e}")
        elif event.event_type == VaultEventType.DELETED:
            # TODO: implement KB deletion by source path
            pass

    def on_event(self, handler: Callable[[VaultFileEvent], None]):
        """Registra handler de eventos."""
        self._event_handlers.append(handler)

    async def __aenter__(self):
        await self._init_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop_watcher()
        await self._close_session()


async def create_vault_manager(
    vault_path: Path,
    api_key: Optional[str] = None,
    host: str = "localhost",
    port: int = 27124,
    verify_ssl: bool = True,
    knowledge_base: Optional["KnowledgeBase"] = None,
) -> ObsidianVaultManager:
    """Factory para criar e inicializar VaultManager."""
    manager = ObsidianVaultManager(
        vault_path=vault_path,
        api_key=api_key,
        host=host,
        port=port,
        verify_ssl=verify_ssl,
        knowledge_base=knowledge_base,
    )
    await manager.start_watcher()
    return manager