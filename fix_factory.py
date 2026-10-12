from pathlib import Path

with open('src/jarvis/integrations/obsidian_vault.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the factory function
factory_start = -1
for i, line in enumerate(lines):
    if 'def create_vault_manager(' in line:
        factory_start = i
        break

# Find the end of the file
factory_end = len(lines)

# Rebuild the factory function with correct indentation
factory_lines = [
    '\n',
    '# ==================== Factory ====================\n',
    '\n',
    'async def create_vault_manager(\n',
    '    vault_path: Optional[Path] = None,\n',
    '    api_key: Optional[str] = None,\n',
    '    config_path: Optional[Path] = None\n',
    ') -> ObsidianVaultManager:\n',
    '    """Factory para criar VaultManager configurado.\"\"\"\n',
    '\n',
    '    if vault_path is None:\n',
    '        from ..core.paths import get_jarvis_paths\n',
    '        paths = get_jarvis_paths()\n',
    '        vault_path = paths.base / "vault"\n',
    '    if api_key is None:\n',
    '        if config_path and config_path.exists():\n',
    '            with open(config_path) as f:\n',
    '                api_key = f.read().strip()\n',
    '\n',
    '    return ObsidianVaultManager(vault_path, api_key)\n',
    '\n',
]

# Keep everything before the factory
new_lines = lines[:213] + ['\n'] + factory_lines

with open('src/jarvis/integrations/obsidian_vault.py', 'w', encoding='utf-8') as f:
    f.writelines([line + '\n' if not line.endswith('\n') else line for line in new_lines])

print('Fixed factory function!')