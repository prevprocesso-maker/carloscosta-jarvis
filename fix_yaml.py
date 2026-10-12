from pathlib import Path
import yaml

config_path = Path.home() / 'AppData' / 'Roaming' / 'JARVIS' / 'config' / 'config.yaml'
content = config_path.read_text(encoding='utf-8')

# Fix the vault path - need double backslashes in YAML
old = 'vault_path: "C:\\Users\\NIKE\\Downloads\\Jarvis-obsidian-teste"'
new = 'vault_path: "C:\\\\Users\\\\NIKE\\\\Downloads\\\\Jarvis-obsidian-teste"'

content = content.replace(old, new)

with open(config_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed!')