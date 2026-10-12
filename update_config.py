from pathlib import Path

config_path = Path.home() / 'AppData' / 'Roaming' / 'JARVIS' / 'config' / 'config.yaml'
content = config_path.read_text(encoding='utf-8')

# Add vault section if not present
if 'vault_path' not in content:
    old_text = '  webhook:\n    enabled: true'
    new_text = '''  webhook:
    enabled: true
  vault_path: "C:\\Users\\NIKE\\Downloads\\Jarvis-obsidian-teste"
  vault_api_key: "T2md678c5qMWEIXaYVOFybvgK3X5gpk8CuyAh_bsRoU"
'''
    content = content.replace(old_text, new_text)
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('Config updated!')
else:
    print('Vault config already exists')