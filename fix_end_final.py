return frontmatter, body

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._session:
            await self._session.close()
        self.stop_watcher()
        return False


# ==================== Factory ====================

def create_vault_manager(
    vault_path: Optional[Path] = None,
    api_key: Optional[str] = None,
    config_path: Optional[Path] = None
) -> ObsidianVaultManager:
    """Factory para criar VaultManager configurado."""

    if vault_path is None:
        from ..core.paths import get_jarvis_paths
        paths = get_jarvis_paths()
        vault_path = paths.base / "vault"
    if api_key is None:
        if config_path and config_path.exists():
            with open(config_path) as f:
                api_key = f.read().strip()

    return ObsidianVaultManager(vault_path, api_key)