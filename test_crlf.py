import yaml

# Test with CRLF
content = b'gemini:\r\n  api_key: ""\r\ntransports:\r\n  local:\r\n    enabled: true\r\n  remote:\r\n    enabled: true\r\n    port: 8000\r\n  webhook:\r\n    enabled: true\r\nvault_path: "C:\\\\Users\\\\NIKE\\\\Downloads\\\\Jarvis-obsidian-teste"\r\nvault_api_key: "T2md678c5qMWEIXaYVOFybvgK3X5gpk8CuyAh_bsRoU"\r\n\r\n'

data = yaml.safe_load(content.decode('utf-8'))
print('With CRLF - Keys:', list(data.keys()))
print('vault_path:', data.get('vault_path'))

# Test with LF
content2 = content.replace(b'\r\n', b'\n')
data2 = yaml.safe_load(content2.decode('utf-8'))
print('With LF - Keys:', list(data2.keys()))
print('vault_path:', data2.get('vault_path'))