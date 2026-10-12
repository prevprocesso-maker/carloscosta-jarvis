from __future__ import annotations

"""JARVIS CyberBot - Entry point principal."""

import asyncio
import sys
import signal
from contextlib import asynccontextmanager

from src.jarvis.bootstrap import initialize_bootstrap
from src.jarvis.orchestrator import JarvisOrchestrator
from src.jarvis.core.config import settings


# Inicializa bootstrap (Linux launcher, emoji-safe stdout, subprocess patches, etc.)
initialize_bootstrap()


@asynccontextmanager
async def lifespan(app: JarvisOrchestrator):
    """Gerencia ciclo de vida da aplicação."""
    # Inicializa
    await app.initialize()
    
    print(f"\n{'='*50}")
    print(f"  JARVIS v{__import__('src.jarvis').__version__} - Ativo")
    print(f"  Usuário: {settings.user.name}")
    print(f"  Transporte: Local (Desktop) + Remoto (Web)")
    print(f"  Diga 'JARVIS' ou acesse a Web UI")
    print(f"{'='*50}\n")
    
    try:
        yield app
    finally:
        await app.shutdown()


async def main():
    # Signal handling
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    
    def signal_handler():
        print("\n[JARVIS] Encerrando...")
        shutdown_event.set()
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows não suporta add_signal_handler para todos os sinais
            pass
    
    # Cria e executa aplicação
    app = JarvisOrchestrator()
    
    async with lifespan(app):
        # Run UI event loop (blocking)
        try:
            import sys
            sys.exit(app.ui.app.exec())
        except KeyboardInterrupt:
            pass
        finally:
            shutdown_event.set()
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass