from pathlib import Path

config_path = Path.home() / 'AppData' / 'Roaming' / 'JARVIS' / 'config' / 'config.yaml'
content = config_path.read_text(encoding='utf-8')

# Fix the vault section - replace flat keys with nested structure
old = 'vault_path: "C:\\Users\\NIKE\\Downloads\\Jarvis-obsidian-teste"\nvault_api_key: "T2md678c5qMWEIXaYVOFybvgK3X5gpk8CuyAh_bsRoU"'
new = 'vault:\n  path: "C:\\\\Users\\\\NIKE\\\\Downloads\\\\Jarvis-obsidian-teste"\n  api_key: "T2md678c5qMWEIXaYVOFybvgK3X5gpk8CuyAh_bsRoU"'

content = content.replace(old, new)

with open(config_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed config!')