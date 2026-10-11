from __future__ import annotations

import asyncio
import aiohttp
import hashlib
import os
from pathlib import Path
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass

from ..update.checker import ReleaseAsset, ReleaseInfo


@dataclass
class DownloadProgress:
    total: int = 0
    downloaded: int = 0
    speed_bps: float = 0
    eta_seconds: float = 0
    completed: bool = False
    error: Optional[str] = None


class Downloader:
    def __init__(self, download_dir: Path, max_concurrent: int = 3):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.max_concurrent = max_concurrent
        self.session: Optional[aiohttp.ClientSession] = None
        self._progress_callbacks: Dict[str, Callable[[DownloadProgress], None]] = {}

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(limit=self.max_concurrent)
        timeout = aiohttp.ClientTimeout(total=300, connect=30)
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def set_progress_callback(self, asset_name: str, callback: Callable[[DownloadProgress], None]):
        self._progress_callbacks[asset_name] = callback

    async def download_asset(self, asset: ReleaseAsset, dest_path: Optional[Path] = None, 
                            expected_sha256: Optional[str] = None) -> Path:
        """Baixa um asset com verificação de checksum."""
        if dest_path is None:
            dest_path = self.download_dir / asset.name
        
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Check if already downloaded and valid
        if dest_path.exists() and expected_sha256:
            if self._verify_checksum(dest_path, expected_sha256):
                print(f"[Download] {asset.name} already exists and valid")
                return dest_path
        
        print(f"[Download] Starting {asset.name} ({asset.size / 1024 / 1024:.1f} MB)")
        
        progress = DownloadProgress(total=asset.size)
        callback = self._progress_callbacks.get(asset.name)
        
        try:
            async with self.session.get(asset.download_url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                
                total = asset.size or int(resp.headers.get("Content-Length", 0))
                progress.total = total
                
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                
                sha256 = hashlib.sha256()
                downloaded = 0
                start_time = asyncio.get_event_loop().time()
                
                with open(dest_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
                        sha256.update(chunk)
                        downloaded += len(chunk)
                        progress.downloaded = downloaded
                        
                        # Calculate speed and ETA
                        elapsed = asyncio.get_event_loop().time() - start_time
                        if elapsed > 0:
                            progress.speed_bps = downloaded / elapsed
                            if progress.speed_bps > 0:
                                progress.eta_seconds = (total - downloaded) / progress.speed_bps
                        
                        if callback:
                            try:
                                callback(progress)
                            except Exception:
                                pass
                
                progress.completed = True
                if callback:
                    callback(progress)
                
                # Verify checksum
                if expected_sha256:
                    actual = sha256.hexdigest()
                    if not self._verify_sha256(actual, expected_sha256):
                        dest_path.unlink(missing_ok=True)
                        raise Exception(f"Checksum mismatch for {asset.name}")
                
                print(f"[Download] Completed: {asset.name}")
                return dest_path
                
        except Exception as e:
            progress.error = str(e)
            progress.completed = True
            if callback:
                callback(progress)
            if dest_path.exists():
                dest_path.unlink(missing_ok=True)
            raise

    async def download_release_assets(self, release: "ReleaseInfo", 
                                     patterns: list[str] = None) -> Dict[str, Path]:
        """Baixa assets de um release que correspondem aos patterns."""
        if patterns is None:
            patterns = ["*.tar.gz", "*.whl", "*.exe", "*.dmg", "*.AppImage"]
        
        assets_to_download = []
        for pattern in patterns:
            import fnmatch
            for asset in release.assets:
                if fnmatch.fnmatch(asset.name.lower(), pattern.lower()):
                    assets_to_download.append(asset)
        
        if not assets_to_download:
            return {}
        
        # Download with semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def download_one(asset: ReleaseAsset):
            async with semaphore:
                return await self.download_asset(asset)
        
        tasks = [download_one(asset) for asset in assets_to_download]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        downloaded = {}
        for asset, result in zip(assets_to_download, results):
            if isinstance(result, Exception):
                print(f"[Download] Failed {asset.name}: {result}")
            else:
                downloaded[asset.name] = result
        
        return downloaded

    def _verify_sha256(self, actual: str, expected: str) -> bool:
        import hmac
        return hmac.compare_digest(actual.lower(), expected.lower())

    def _verify_file_checksum(self, file_path: Path, expected_sha256: str) -> bool:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        actual = sha256.hexdigest()
        import hmac
        return hmac.compare_digest(actual.lower(), expected_sha256.lower())