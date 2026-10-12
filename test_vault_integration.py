import os
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'max_split_size_mb:64'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

import sys
sys.path.insert(0, 'src')
for mod in list(sys.modules.keys()):
    if 'jarvis' in mod:
        del sys.modules[mod]

import sys
sys.path.insert(0, 'src')
from jarvis.core.knowledge import get_knowledge_base
from jarvis.integrations import create_vault_manager
from pathlib import Path
import asyncio

async def test():
    # Test vault manager
    vault_path = Path(r'C:\Users\NIKE\Downloads\Jarvis-obsidian-teste')
    async with create_vault_manager(vault_path=vault_path) as vm:
        print('Vault manager created')
        
        # Test reading a note
        try:
            note = await vm.read_note('conhecimento/raw/algoritmos-de-sistemas-distribuidos.md')
            print('Read note:', note['title'][:50] + '...')
        except Exception as e:
            print('Read error:', e)
        
        # Test search
        results = await vm.search('complexidade', limit=3)
        print('Search results:', len(results))
        for r in results:
            print('  -', r['path'][:50], '(score:', r['score'], ')')

asyncio.run(test())