from pathlib import Path

with open('src/jarvis/integrations/obsidian_vault.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Split into lines
lines = content.split('\n')

# Find the class definition
class_start = -1
for i, line in enumerate(lines):
    if line.strip().startswith('class ObsidianVaultManager:'):
        class_start = i
        break

# Find the factory section
factory_start = -1
for i, line in enumerate(lines):
    if line.strip() == '# ==================== Factory ====================':
        factory_start = i
        break

# Keep everything up to the class content (before the broken methods)
# We'll reconstruct the class properly
class_lines = lines[:class_start + 1]  # Include class definition line

# Add proper class methods with correct indentation
class_methods = [
    '    async def full_sync(self, knowledge_base, force: bool = False) -> int:',
    '        """Sincronização completa vault -> KB."""',
    '        print(f"[Vault Sync] Iniciando sync completo...")',
    '        count = await self.sync_to_knowledge_base(knowledge_base)',
    '        print(f"[Vault Sync] Concluído: {count} documentos sincronizados")',
    '        return count',
    '',
    '    async def __aenter__(self):',
    '        """Async context manager entry."""',
    '        return self',
    '',
    '    async def __aexit__(self, exc_type, exc_val, exc_tb):',
    '        """Async context manager exit."""',
    '        if self._session:',
    '            await self._session.close()',
    '        self.stop_watcher()',
    '        return False',
    '',
    '',
    '# ==================== Factory ====================',
    '',
    'async def create_vault_manager(',
    '    vault_path: Optional[Path] = None,',
    '    api_key: Optional[str] = None,',
    '    config_path: Optional[Path] = None',
    ') -> ObsidianVaultManager:',
    '    """Factory para criar VaultManager configurado."""',
    '',
    '    if vault_path is None:',
    '        from ..core.paths import get_jarvis_paths',
    '        paths = get_jarvis_paths()',
    '        vault_path = paths.base / "vault"',
    '    if api_key is None:',
    '        if config_path and config_path.exists():',
    '            with open(config_path) as f:',
    '                api_key = f.read().strip()',
    '',
    '    manager = ObsidianVaultManager(vault_path, api_key)',
    '    await manager.__aenter__()',
    '    return manager',
    '',
]

# Reconstruct
new_lines = lines[:class_start + 1] + class_methods + ['']
new_content = '\n'.join(new_lines) + '\n'

with open('src/jarvis/integrations/obsidian_vault.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('Rewrote file completely!')