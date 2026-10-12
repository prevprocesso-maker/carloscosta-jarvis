import os
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'max_split_size_mb:64'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

import sys
sys.path.insert(0, 'src')
from pathlib import Path

from jarvis.integrations import create_vault_manager

import asyncio

async def test():
    vm = await create_vault_manager(vault_path=Path(r'C:\Users\NIKE\Downloads\Jarvis-obsidian-teste'))
    print('Vault manager created')
    print('Vault path:', vm.vault_path)
    print('API key:', vm.api_key[:20] + '...' if vm.api_key else 'None')
    
    # Test reading a note
    note = await vm.read_note('conhecimento/raw/algoritmos-de-sistemas-distribuidos.md')
    print('Note read:', note['title'][:50] + '...' if note else 'None')
    
    # Test search
    results = await vm.search('complexidade', limit=3)
    print(f'Search results: {len(results)}')
    for r in results:
        print(f'  - {r["path"][:50]} (score: {r["score"]:.3f})')
    
    await vm.close()
    print('Test completed!')

asyncio.run(test())