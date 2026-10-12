import ast

with open('src/jarvis/integrations/obsidian_vault.py', 'r', encoding='utf-8') as f:
    source = f.read()

# Test parsing the whole file
try:
    ast.parse(source)
    print('Full file: OK')
except SyntaxError as e:
    print(f'Full file error at line {e.lineno}, offset {e.offset}: {e.msg}')
    lines = source.split('\n')
    for i in range(max(0, e.lineno-3), min(len(lines), e.lineno+2)):
        marker = '>>>' if i+1 == e.lineno else '   '
        print(f'{marker} {i+1:4}: {lines[i]}')

# Test isolated method
method_code = '''
    async def __aenter__(self):
        """Async context manager entry."""
        return self
'''
try:
    ast.parse(method_code)
    print('Isolated method: OK')
except SyntaxError as e:
    print(f'Isolated method error at line {e.lineno}: {e.msg}')