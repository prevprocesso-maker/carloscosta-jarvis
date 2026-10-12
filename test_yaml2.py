import yaml

content = """gemini:
  api_key: ""
transports:
  local:
    enabled: true
  remote:
    enabled: true
    port: 8000
  webhook:
    enabled: true
  vault_path: "C:\\\\Users\\\\NIKE\\\\Downloads\\\\Jarvis-obsidian-teste"
  vault_api_key: "T2md678c5qMWEIXaYVOFybvgK3X5gpk8CuyAh_bsRoU\""""

data = yaml.safe_load(content)
print('Keys:', list(data.keys()))
print('vault_path:', data.get('vault_path'))
print('vault_api_key:', data.get('vault_api_key'))