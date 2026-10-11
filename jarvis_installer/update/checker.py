from __future__ import annotations

import json
import asyncio
import aiohttp
import hashlib
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from pathlib import Path
from datetime import datetime, timedelta

from ..core.version import VersionInfo, parse_version, Channel, is_newer, get_current_version, get_current_channel
from ..core.config import load_config, save_config
from ..paths import get_jarvis_paths


GITHUB_API = "https://api.github.com"
REPO = "prevprocesso-maker/carloscosta-jarvis"


@dataclass
class ReleaseAsset:
    name: str
    size: int
    download_url: str
    checksum: Optional[str] = None


@dataclass
class ReleaseInfo:
    version: VersionInfo
    name: str
    body: str
    published_at: str
    prerelease: bool
    draft: bool
    html_url: str
    assets: List[ReleaseAsset] = field(default_factory=list)

    def get_asset(self, pattern: str) -> Optional[ReleaseAsset]:
        import fnmatch
        for asset in self.assets:
            if fnmatch.fnmatch(asset.name.lower(), pattern.lower()):
                return asset
        return None


class UpdateChecker:
    def __init__(self, config_path: Optional[Path] = None):
        self.paths = get_jarvis_paths()
        self.config_path = config_path or self.paths.config_file
        self.config = load_config(self.config_path)
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def check_for_updates(self, channel: Optional[Channel] = None) -> Optional[ReleaseInfo]:
        """Verifica se há atualizações disponíveis."""
        channel = channel or get_current_channel()
        current = get_current_version()
        
        try:
            releases = await self._fetch_releases()
            if not releases:
                return None
            
            # Filter by channel
            filtered = [r for r in releases if self._matches_channel(r, channel)]
            if not filtered:
                return None
            
            # Find latest newer version
            latest = max(filtered, key=lambda r: r.version)
            
            if is_newer(str(current), str(latest.version)):
                return latest
            
            return None
        except Exception as e:
            print(f"[Update] Check failed: {e}")
            return None

    async def _fetch_releases(self) -> List[ReleaseInfo]:
        """Busca releases do GitHub."""
        if not self.session:
            self.session = aiohttp.ClientSession()
        
        url = f"{GITHUB_API}/repos/{REPO}/releases"
        headers = {"Accept": "application/vnd.github.v3+json"}
        
        try:
            async with self.session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                
                releases = []
                for rel in data:
                    if rel.get("draft"):
                        continue
                    
                    version_str = rel.get("tag_name", "").lstrip("v")
                    try:
                        version = parse_version(version_str)
                    except ValueError:
                        continue
                    
                    assets = []
                    for asset in rel.get("assets", []):
                        assets.append(ReleaseAsset(
                            name=asset.get("name", ""),
                            size=asset.get("size", 0),
                            download_url=asset.get("browser_download_url", ""),
                        ))
                    
                    releases.append(ReleaseInfo(
                        version=version,
                        name=rel.get("name", ""),
                        body=rel.get("body", ""),
                        published_at=rel.get("published_at", ""),
                        prerelease=rel.get("prerelease", False),
                        draft=rel.get("draft", False),
                        html_url=rel.get("html_url", ""),
                        assets=assets,
                    ))
                
                return releases
        except Exception as e:
            print(f"[Update] Fetch failed: {e}")
            return []

    def _matches_channel(self, release: ReleaseInfo, channel: Channel) -> bool:
        """Verifica se release corresponde ao canal."""
        if release.prerelease:
            return channel != Channel.STABLE
        
        # For stable releases, check version suffix or assume stable
        if release.version.channel == channel:
            return True
        
        # Stable releases match stable channel
        if not release.prerelease and channel == Channel.STABLE:
            return True
        
        return False

    async def get_latest_stable(self) -> Optional[ReleaseInfo]:
        return await self.check_for_updates(Channel.STABLE)

    async def get_latest_beta(self) -> Optional[ReleaseInfo]:
        return await self.check_for_updates(Channel.BETA)

    async def get_latest_nightly(self) -> Optional[ReleaseInfo]:
        return await self.check_for_updates(Channel.NIGHTLY)

    def get_cached_check_time(self) -> Optional[datetime]:
        """Retorna última verificação cacheada."""
        last_check = self.config.get("update", {}).get("last_check")
        if last_check:
            try:
                return datetime.fromisoformat(last_check)
            except Exception:
                pass
        return None

    def should_check(self) -> bool:
        """Verifica se deve checar updates baseado no intervalo."""
        if not self.config.get("update", {}).get("auto_check", True):
            return False
        
        last = self.get_cached_check_time()
        if not last:
            return True
        
        interval = self.config.get("update", {}).get("check_interval_hours", 6)
        return datetime.now() - last > timedelta(hours=interval)

    def update_last_check(self):
        """Atualiza timestamp da última verificação."""
        self.config.setdefault("update", {})["last_check"] = datetime.now().isoformat()
        save_config(self.config, self.config_path)