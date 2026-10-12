with open('src/jarvis/integrations/obsidian_vault.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix the full_sync method
# Find the line with 'async def full_sync'
for i, line in enumerate(lines):
    if 'async def full_sync' in line:
        method_start = i
        break

# Find the docstring end
docstring_end = -1
for i in range(method_start, len(lines)):
    if '"""' in lines[i] and 'Sincronização' in lines[i]:
        # Find the closing """
        for j in range(i, len(lines)):
            if '"""' in lines[j] and j > i:
                docstring_end = j
                break
        break

# Insert method body after docstring
body_lines = [
    '        print(f"[Vault Sync] Iniciando sync completo...")\n',
    '        count = await self.sync_to_knowledge_base(knowledge_base)\n',
    '        print(f"[Vault Sync] Concluído: {count} documentos sincronizados")\n',
    '        return count\n',
    '\n'
]

# Insert after docstring
insert_pos = docstring_end + 1
lines = lines[:docstring_end + 1] + body_lines + lines[docstring_end + 1:]

with open('src/jarvis/integrations/obsidian_vault.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed!')