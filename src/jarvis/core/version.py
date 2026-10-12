from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from packaging import version as pkg_version


class Channel(Enum):
    STABLE = "stable"
    BETA = "beta"
    NIGHTLY = "nightly"


@dataclass
class VersionInfo:
    major: int
    minor: int
    patch: int
    channel: Channel = Channel.STABLE
    build: Optional[int] = None
    commit: Optional[str] = None
    date: Optional[str] = None

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.channel != Channel.STABLE:
            base += f"-{self.channel.value}"
        if self.build:
            base += f"+{self.build}"
        return base

    def __lt__(self, other: "VersionInfo") -> bool:
        if not isinstance(other, VersionInfo):
            return NotImplemented
        # Compare core version first
        self_ver = pkg_version.parse(f"{self.major}.{self.minor}.{self.patch}")
        other_ver = pkg_version.parse(f"{other.major}.{other.minor}.{other.patch}")
        if self_ver != other_ver:
            return self_ver < other_ver
        # Same core version, compare channel priority
        channel_priority = {Channel.NIGHTLY: 0, Channel.BETA: 1, Channel.STABLE: 2}
        return channel_priority.get(self.channel, 2) < channel_priority.get(other.channel, 2)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VersionInfo):
            return NotImplemented
        return (self.major == other.major and 
                self.minor == other.minor and 
                self.patch == other.patch and
                self.channel == other.channel)

    @property
    def is_prerelease(self) -> bool:
        return self.channel != Channel.STABLE


def parse_version(version_str: str) -> VersionInfo:
    """Parse version string into VersionInfo."""
    # Pattern: MAJOR.MINOR.PATCH[-channel][+build]
    pattern = r"^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z]+))?(?:\+(\d+))?$"
    match = re.match(pattern, version_str.strip())
    
    if not match:
        raise ValueError(f"Invalid version format: {version_str}")
    
    major, minor, patch, channel_str, build_str = match.groups()
    
    channel = Channel.STABLE
    if channel_str:
        try:
            channel = Channel(channel_str.lower())
        except ValueError:
            pass
    
    build = int(build_str) if build_str else None
    
    return VersionInfo(
        major=int(major),
        minor=int(minor),
        patch=int(patch),
        channel=channel,
        build=int(build) if build_str else None,
    )


def format_version(info: VersionInfo) -> str:
    """Format VersionInfo to string."""
    return str(info)


def compare_versions(v1: str, v2: str) -> int:
    """Compare two version strings. Returns -1, 0, 1."""
    v1_info = parse_version(v1)
    v2_info = parse_version(v2)
    if v1_info < v2_info:
        return -1
    elif v1_info > v2_info:
        return 1
    return 0


def is_newer(current: str, available: str) -> bool:
    """Check if available version is newer than current."""
    return compare_versions(current, available) < 0


def get_channel_priority(channel: Channel) -> int:
    """Priority for channel (higher = more stable)."""
    return {
        Channel.STABLE: 3,
        Channel.BETA: 2,
        Channel.NIGHTLY: 1,
    }.get(channel, 0)


def is_compatible_channel(current_channel: Channel, target_channel: Channel) -> bool:
    """Check if target channel is compatible (same or more stable)."""
    return get_channel_priority(target_channel) >= get_channel_priority(current_channel)


@dataclass
class ReleaseInfo:
    version: VersionInfo
    name: str
    body: str
    published_at: str
    assets: list[dict]
    prerelease: bool
    draft: bool
    html_url: str

    @property
    def download_urls(self) -> dict[str, str]:
        """Map asset name to download URL."""
        urls = {}
        for asset in self.assets:
            name = asset.get("name", "")
            url = asset.get("browser_download_url", "")
            if name and url:
                urls[name] = url
        return urls

    def get_asset_url(self, pattern: str) -> Optional[str]:
        """Find asset URL matching pattern."""
        import fnmatch
        for name, url in self.download_urls.items():
            if fnmatch.fnmatch(name.lower(), pattern.lower()):
                return url
        return None


# Current version (updated by release script)
CURRENT_VERSION = "2.0.0-dev"
CURRENT_CHANNEL = Channel.NIGHTLY

def get_current_version() -> VersionInfo:
    return parse_version(CURRENT_VERSION)


def get_current_channel() -> Channel:
    return CURRENT_CHANNEL