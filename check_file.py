import os
from pathlib import Path

filepath = Path("src/jarvis/integrations/obsidian_vault.py")

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

print("Current file size:", len(content))
print("First 200 chars:", repr(content[:200]))
print("Last 200 chars:", repr(content[-200:]))