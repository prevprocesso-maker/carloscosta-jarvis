with open('src/jarvis/integrations/obsidian_vault.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the last non-empty line
last_content = len(lines) - 1
while last_content >= 0 and lines[last_content].strip() == '':
    last_content -= 1

# Truncate
lines = lines[:last_content + 1]

# Build proper ending
ending = [
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
    '    from ..core.paths import get_jarvis_paths\n',
    '    paths = get_jarvis_paths()\n',
    '    vault_path = paths.base / "vault"\n',
    '    if api_key is None:\n',
    '        if config_path and config_path.exists():\n',
    '            with open(config_path) as f:\n',
    '                api_key = f.read().strip()\n',
    '\n',
    '    manager = ObsidianVaultManager(vault_path, api_key)\n',
    '    await manager.__aenter__()\n',
    '    return manager\n',
    '\n',
]

new_lines = lines + ending

with open('src/jarvis/integrations/obsidian_vault.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('Fixed!')