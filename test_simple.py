import yaml

content = '''
vault_path: "C:\\\\Users\\\\NIKE\\\\Downloads\\\\Jarvis-obsidian-teste"
vault_api_key: "test-key"
'''

data = yaml.safe_load(content)
print('Keys:', list(data.keys()))
print('vault_path:', data.get('vault_path'))
print('vault_api_key:', data.get('vault_api_key'))