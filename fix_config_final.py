from pathlib import Path
import yaml

config_path = Path.home() / 'AppData' / 'Roaming' / 'JARVIS' / 'config' / 'config.yaml'

new_content = """gemini:
  api_key: \"\"
transports:
  local:
    enabled: true
  remote:
    enabled: true
    port: 8000
  webhook:
    enabled: true
vault:
  path: \"C:\\\\Users\\\\NIKE\\\\Downloads\\\\Jarvis-obsidian-teste\"
  api_key: \"T2md678c5qMWEIXaYVOFybvgK3X5gpk8CuyAh_bsRoU\"
"""

with open(Path.home() / 'AppData' / 'Roaming' / 'JARVIS' / 'config' / 'config.yaml', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('Config rewritten!')