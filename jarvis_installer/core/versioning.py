from __future__ import annotations

import re
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Set
from packaging import version as pkg_version

from ..paths import get_jarvis_paths


class ReleaseChannel(Enum):
    STABLE = "stable"
    BETA = "beta"
    NIGHTLY = "nightly"
    CANARY = "canary"


class VersionComponent(Enum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


@dataclass
class Version:
    major: int
    minor: int
    patch: int
    channel: ReleaseChannel = ReleaseChannel.STABLE
    build: Optional[int] = None
    commit: Optional[str] = None
    timestamp: Optional[str] = None

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.channel != ReleaseChannel.STABLE:
            base += f"-{self.channel.value}"
        if self.build is not None:
            base += f"+{self.build}"
        return base

    def __lt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        # Compare core version
        self_tuple = (self.major, self.minor, self.patch)
        other_tuple = (other.major, other.minor, other.patch)
        if self_tuple != other_tuple:
            return self_tuple < other_tuple
        # Same core, compare channel priority
        channel_priority = {
            ReleaseChannel.CANARY: 0,
            ReleaseChannel.NIGHTLY: 1,
            ReleaseChannel.BETA: 2,
            ReleaseChannel.STABLE: 3,
        }
        return channel_priority.get(self.channel, 3) < channel_priority.get(other.channel, 3)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return (self.major == other.major and
                self.minor == other.minor and
                self.patch == other.patch and
                self.channel == other.channel)

    def bump(self, component: VersionComponent) -> "Version":
        if component == VersionComponent.MAJOR:
            return Version(self.major + 1, 0, 0, self.channel)
        elif component == VersionComponent.MINOR:
            return Version(self.major, self.minor + 1, 0, self.channel)
        elif component == VersionComponent.PATCH:
            return Version(self.major, self.minor, self.patch + 1, self.channel)
        return self

    def to_dict(self) -> Dict:
        return {
            "major": self.major,
            "minor": self.minor,
            "patch": self.patch,
            "channel": self.channel.value,
            "build": self.build,
            "commit": self.commit,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Version":
        return cls(
            major=data["major"],
            minor=data["minor"],
            patch=data["patch"],
            channel=ReleaseChannel(data.get("channel", "stable")),
            build=data.get("build"),
            commit=data.get("commit"),
            timestamp=data.get("timestamp"),
        )


def parse_version(version_str: str) -> Version:
    """Parse version string into Version object."""
    # Pattern: MAJOR.MINOR.PATCH[-channel][+build]
    pattern = r"^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z]+))?(?:\+(\d+))?$"
    match = re.match(pattern, version_str.strip())
    if not match:
        raise ValueError(f"Invalid version format: {version_str}")

    major, minor, patch, channel_str, build_str = match.groups()
    channel = ReleaseChannel.STABLE
    if channel_str:
        try:
            channel = ReleaseChannel(channel_str.lower())
        except ValueError:
            pass
    build = int(build_str) if build_str else None
    return Version(
        major=int(major), minor=int(minor), patch=int(patch),
        channel=channel, build=build
    )


@dataclass
class ReleaseInfo:
    version: Version
    name: str
    body: str
    published_at: str
    prerelease: bool
    draft: bool
    html_url: str
    assets: List[Dict] = field(default_factory=list)
    checksums: Dict[str, str] = field(default_factory=dict)  # filename -> sha256

    @property
    def is_prerelease(self) -> bool:
        return self.prerelease or self.version.channel != ReleaseChannel.STABLE


@dataclass
class Migration:
    version: str
    name: str
    description: str
    up: Callable[[Dict], Dict]  # Config migration function
    down: Optional[Callable[[Dict], Dict]] = None  # Rollback function
    dependencies: List[str] = field(default_factory=list)  # Required previous migrations
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class MigrationManager:
    def __init__(self, migrations_path: Path = None):
        self.migrations: List[Migration] = []
        self.migrations_path = migrations_path or Path(__file__).parent / "migrations"
        self.applied_file = self.migrations_path / "applied_migrations.json"
        self._load_migrations()

    def _load_migrations(self):
        """Carrega migrações do diretório."""
        if not self.migrations_path.exists():
            return
        for file in sorted(self.migrations_path.glob("*.py")):
            if file.name.startswith("_"):
                continue
            # Import migration module
            import importlib.util
            spec = importlib.util.spec_from_file_location(file.stem, file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "migration"):
                self.migrations.append(module.migration)
        self.migrations.sort(key=lambda m: pkg_version.parse(m.version))

    def register(self, migration: Migration):
        """Registra migração programaticamente."""
        self.migrations.append(migration)
        self.migrations.sort(key=lambda m: pkg_version.parse(m.version))

    def get_applied(self) -> List[str]:
        if not self.applied_file.exists():
            return []
        with open(self.applied_file) as f:
            return json.load(f)

    def mark_applied(self, version: str):
        applied = self.get_applied()
        if version not in applied:
            applied.append(version)
            self.applied_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.applied_file, "w") as f:
                json.dump(applied, f, indent=2)

    def mark_rolled_back(self, version: str):
        applied = self.get_applied()
        if version in applied:
            applied.remove(version)
            with open(self.applied_file, "w") as f:
                json.dump(applied, f, indent=2)

    def get_pending(self, current_version: str) -> List[Migration]:
        current = pkg_version.parse(current_version)
        applied = set(self.get_applied())
        return [
            m for m in self.migrations
            if pkg_version.parse(m.version) > current and m.version not in applied
        ]

    def get_rollback_candidates(self, current_version: str) -> List[Migration]:
        current = pkg_version.parse(current_version)
        applied = set(self.get_applied())
        return [
            m for m in self.migrations
            if pkg_version.parse(m.version) <= current and m.version in applied and m.down is not None
        ]

    def migrate(self, config: Dict, target_version: str = None) -> Dict:
        """Aplica migrações até versão alvo."""
        current_version = config.get("version", "0.0.0")
        current = pkg_version.parse(current_version)
        target = pkg_version.parse(target_version) if target_version else None

        pending = self.get_pending(current_version)
        if target:
            pending = [m for m in pending if pkg_version.parse(m.version) <= target]

        for migration in pending:
            print(f"[Migration] Applying {migration.version}: {migration.name}")
            config = migration.up(config)
            config["version"] = migration.version
            self.mark_applied(migration.version)

        return config

    def rollback(self, config: Dict, target_version: str) -> Dict:
        """Rollback para versão anterior."""
        current_version = config.get("version", "0.0.0")
        current = pkg_version.parse(current_version)
        target = pkg_version.parse(target_version)

        if target >= current:
            raise ValueError("Target version must be lower than current")

        candidates = self.get_rollback_candidates(current_version)
        candidates = [m for m in candidates if pkg_version.parse(m.version) > target]
        candidates.sort(key=lambda m: pkg_version.parse(m.version), reverse=True)

        for migration in candidates:
            if not migration.down:
                raise ValueError(f"Migration {migration.version} has no rollback function")
            print(f"[Migration] Rolling back {migration.version}: {migration.name}")
            config = migration.down(config)
            config["version"] = migration.version
            self.mark_rolled_back(migration.version)

        return config


class VersionManager:
    def __init__(self, config_path: Path = None):
        self.config_path = config_path or get_jarvis_paths().config / "version.json"
        self.migration_manager = MigrationManager()
        self._register_core_migrations()

    def _register_core_migrations(self):
        # Migração 1.0.0 -> 2.0.0: Nova estrutura de transports
        self.migration_manager.register(Migration(
            version="2.0.0",
            name="Transport Architecture",
            description="Nova estrutura de transports (local/remote/webhook)",
            up=lambda c: self._migrate_1_to_2(c),
            down=lambda c: self._rollback_2_to_1(c),
        ))

        # Migração 2.0.0 -> 2.1.0: Novos campos de update
        self.migration_manager.register(Migration(
            version="2.1.0",
            name="Update Config Fields",
            description="Adiciona campos de health check e rollback automático",
            up=lambda c: self._migrate_2_to_21(c),
            down=lambda c: self._rollback_21_to_2(c),
        ))

        # Migração 2.1.0 -> 2.2.0: Guardrails
        self.migration_manager.register(Migration(
            version="2.2.0",
            name="Guardrails Engine",
            description="Adiciona motor de guardrails de segurança",
            up=lambda c: self._migrate_21_to_22(c),
            down=lambda c: self._rollback_22_to_21(c),
        ))

        # Migração 2.2.0 -> 2.3.0: Knowledge Base
        self.migration_manager.register(Migration(
            version="2.3.0",
            name="Knowledge Base",
            description="Adiciona base de conhecimento RAG",
            up=lambda c: self._migrate_22_to_23(c),
            down=lambda c: self._rollback_23_to_22(c),
        ))

    def _migrate_1_to_2(self, config: Dict) -> Dict:
        """Migra estrutura v1 para v2 (transports)."""
        if "transports" not in config:
            config["transports"] = {
                "local": config.get("stt", {}),
                "remote": {"enabled": True, "port": 8000},
                "webhook": {"enabled": True, "whatsapp_port": 8765},
            }
        if "stt" in config:
            del config["stt"]
        if "tts" in config:
            del config["tts"]
        return config

    def _rollback_2_to_1(self, config: Dict) -> Dict:
        if "transports" in config and "local" in config["transports"]:
            config["stt"] = config["transports"]["local"]
        return config

    def _migrate_2_to_21(self, config: Dict) -> Dict:
        config.setdefault("update", {}).setdefault("health_check_timeout_seconds", 30)
        config.setdefault("update", {}).setdefault("rollback_on_health_failure", True)
        config.setdefault("deploy", {}).setdefault("traefik_enabled", False)
        return config

    def _rollback_21_to_2(self, config: Dict) -> Dict:
        if "update" in config:
            config["update"].pop("health_check_timeout_seconds", None)
            config["update"].pop("rollback_on_health_failure", None)
        config.get("deploy", {}).pop("traefik_enabled", None)
        return config

    def _migrate_21_to_22(self, config: Dict) -> Dict:
        config.setdefault("guardrails", {"enabled": True, "rules_file": "guardrails.json"})
        return config

    def _rollback_22_to_21(self, config: Dict) -> Dict:
        config.pop("guardrails", None)
        return config

    def _migrate_22_to_23(self, config: Dict) -> Dict:
        config.setdefault("knowledge", {
            "enabled": True,
            "chunk_size": 512,
            "chunk_overlap": 50,
            "embedding_provider": "mock",
        })
        return config

    def _rollback_23_to_22(self, config: Dict) -> Dict:
        config.pop("knowledge", None)
        return config

    def get_current_version(self) -> Version:
        if self.config_path.exists():
            with open(self.config_path) as f:
                data = json.load(f)
                return Version.from_dict(data.get("version", {}))
        return Version(0, 0, 0)

    def save_version(self, version: Version, metadata: Dict = None):
        data = {
            "version": version.to_dict(),
            "metadata": metadata or {},
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(data, f, indent=2)

    def get_full_version_string(self) -> str:
        version = self.get_current_version()
        return str(version)

    def bump_version(self, component: str) -> Version:
        current = self.get_current_version()
        comp = VersionComponent(component.lower())
        new_version = current.bump(comp)
        self.save_version(new_version)
        return new_version


class ReleaseManager:
    def __init__(self, repo: str = "prevprocesso-maker/carloscosta-jarvis"):
        self.repo = repo
        self.api_base = "https://api.github.com"

    async def create_release(
        self,
        version: Version,
        name: str,
        body: str,
        assets: List[Path] = None,
        prerelease: bool = False,
        draft: bool = False,
    ) -> Dict:
        """Cria release no GitHub."""
        import aiohttp
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN required")

        tag = f"v{version}"
        data = {
            "tag_name": tag,
            "name": name,
            "body": body,
            "prerelease": prerelease or version.channel != ReleaseChannel.STABLE,
            "draft": draft,
        }

        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"token {token}"}
            async with session.post(f"{self.api_base}/repos/{self.repo}/releases", json=data, headers=headers) as resp:
                release = await resp.json()
                release_id = release["id"]

                # Upload assets
                if assets:
                    for asset_path in assets:
                        await self._upload_asset(session, release_id, asset_path, token)

                return release

    async def _upload_asset(self, session: aiohttp.ClientSession, release_id: int, asset_path: Path, token: str):
        headers = {
            "Authorization": f"token {token}",
            "Content-Type": "application/octet-stream",
        }
        upload_url = f"https://uploads.github.com/repos/{self.repo}/releases/{release_id}/assets?name={asset_path.name}"
        with open(asset_path, "rb") as f:
            async with session.post(upload_url, data=f.read(), headers=headers) as resp:
                return await resp.json()

    async def get_latest_release(self, channel: ReleaseChannel = None) -> Optional[ReleaseInfo]:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.api_base}/repos/{self.repo}/releases") as resp:
                releases = await resp.json()
                for rel in releases:
                    if rel.get("draft"):
                        continue
                    version_str = rel.get("tag_name", "").lstrip("v")
                    try:
                        version = parse_version(version_str)
                    except ValueError:
                        continue
                    if channel and version.channel != channel:
                        continue
                    return ReleaseInfo(
                        version=version,
                        name=rel.get("name", ""),
                        body=rel.get("body", ""),
                        published_at=rel.get("published_at", ""),
                        prerelease=rel.get("prerelease", False),
                        draft=rel.get("draft", False),
                        html_url=rel.get("html_url", ""),
                        assets=[
                            {"name": a["name"], "size": a["size"], "url": a["browser_download_url"]}
                            for a in rel.get("assets", [])
                        ],
                    )
        return None


# Global instances
_version_manager: Optional[VersionManager] = None
_release_manager: Optional[ReleaseManager] = None


def get_version_manager(config_path: Path = None) -> VersionManager:
    global _version_manager
    if _version_manager is None:
        _version_manager = VersionManager(config_path)
    return _version_manager


def get_release_manager(repo: str = None) -> ReleaseManager:
    global _release_manager
    if _release_manager is None:
        _release_manager = ReleaseManager(repo or "prevprocesso-maker/carloscosta-jarvis")
    return _release_manager


def get_current_version() -> Version:
    return get_version_manager().get_current_version()


def get_current_channel() -> ReleaseChannel:
    return get_current_version().channel


def bump_version(component: str) -> Version:
    return get_version_manager().bump_version(component)