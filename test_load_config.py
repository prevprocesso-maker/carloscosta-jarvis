import yaml

# Read the actual config file
from pathlib import Path

config_path = Path.home() / 'AppData' / 'Roaming' / 'JARVIS' / 'config' / 'config.yaml'
content = config_path.read_text(encoding='utf-8')

print('Content:')
print(repr(content))

data = yaml.safe_load(content)
print('Keys:', list(data.keys()))
print('vault_path:', data.get('vault_path'))
print('vault_api_key:', data.get('vault_api_key'))