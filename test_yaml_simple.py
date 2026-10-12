import yaml

# Test 1: Simple case
content = """
vault_path: test
vault_api_key: test_key
"""

data = yaml.safe_load(content)
print('Test 1 - Keys:', list(data.keys()))
print('vault_path:', data.get('vault_path'))

# Test 2: With gemini first
content2 = """
gemini:
  api_key: ""
vault_path: test
"""

data2 = yaml.safe_load(content2)
print('Test 2 - Keys:', list(data2.keys()))
print('vault_path:', data2.get('vault_path'))

# Test 3: With transports
content3 = """
gemini:
  api_key: ""
transports:
  local:
    enabled: true
vault_path: "test"
"""

data3 = yaml.safe_load(content3)
print('Test 3 - Keys:', list(data3.keys()))
print('vault_path:', data3.get('vault_path'))