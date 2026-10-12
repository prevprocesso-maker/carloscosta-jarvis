with open('src/jarvis/integrations/obsidian_vault.py', 'rb') as f:
    content = f.read()
lines = content.split(b'\n')
for i in range(390, 398):
    line = lines[i]
    leading = len(line) - len(line.lstrip(b' \t'))
    print(f'{i+1}: indent={leading} bytes={list(line[:60])}')