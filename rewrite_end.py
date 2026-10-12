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

# Keep everything up to the class definition + the class content
# Find the end of the class (before factory)
class_end = -1
for i in range(len(lines) - 1, -1, -1):
    if lines[i].strip() == '# ==================== Factory ====================':
        class_end = i
        break

if class_start >= 0 and class_end > class_start:
    # Keep everything up to the factory section
    new_lines = lines[:class_end]
    
    # Ensure proper ending
    # The class should end with proper indentation
    # Add proper ending methods
    ending = [
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
        '    from ..core.paths import get_jarvis_paths',
        '    paths = get_jarvis_paths()',
        '    vault_path = paths.base / "vault"',
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
    
    new_lines = lines[:class_end] + ending
    new_content = '\n'.join(new_lines) + '\n'
    
    with open('src/jarvis/integrations/obsidian_vault.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print('Rewrote file!')