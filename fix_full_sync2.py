with open('src/jarvis/integrations/obsidian_vault.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix the full_sync method (around line 213)
# Current state:
# Line 213 (index 212): '    async def full_sync(self, knowledge_base, force: bool = False) -> int:\n' (indent=4)
# Line 214 (index 213): empty with wrong indent
# Line 217 (index 216): factory section starts

# Fix: Ensure proper indentation for full_sync method
# Line 212 (index 211): should be empty with indent=4
# Line 213 (index 212): method def at indent=4
# Line 214 (index 213): docstring at indent=8
# Then method body at indent=8

# Fix line 212 (index 211): empty line in class should have indent=4
lines[211] = '    \n'

# Ensure line 213 (index 212) has correct indent (already 4)
# Add method body after docstring

# Find the line with '"""Sincronização completa'
for i, line in enumerate(lines):
    if 'Sincronização completa' in line:
        docstring_line = i
        break

# Insert method body after docstring (after line with '"""')
docstring_end = i + 1
while docstring_end < len(lines) and lines[docstring_end].strip() == '':
    docstring_end += 1

# Insert method body after docstring
body_lines = [
    '        print(f"[Vault Sync] Iniciando sync completo...")\n',
    '        count = await self.sync_to_knowledge_base(knowledge_base)\n',
    '        print(f"[Vault Sync] Concluído: {count} documentos sincronizados")\n',
    '        return count\n',
    '\n'
]

lines = lines[:docstring_end + 1] + body_lines + lines[docstring_end + 1:]

with open('src/jarvis/integrations/obsidian_vault.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed full_sync method!')