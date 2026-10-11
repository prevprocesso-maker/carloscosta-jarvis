from __future__ import annotations

import asyncio
import signal
import sys
from contextlib import asynccontextmanager

from jarvis.bootstrap import create_application, ensure_config_files
from jarvis.core.config import settings


@asynccontextmanager
async def lifespan(app):
    # Inicializa brains
    app.initialize_brains()

    # Callbacks para output
    async def on_audio_out(chunk: bytes):
        # No modo local, o transport já toca via sounddevice
        pass

    async def on_text_out(text: str):
        print(f"[JARVIS] {text}")

    async def on_tool_call(tool_call):
        from jarvis.core.actions.registry import execute_action
        return execute_action(tool_call.name, tool_call.args, app.session_mgr)

    # Override callbacks
    app._audio_out_callback = on_audio_out
    app._text_out_callback = on_text_out
    app._tool_call_callback = on_tool_call

    # Inicia transports
    await app.start_transports()

    print(f"\n{'='*50}")
    print(f"  JARVIS v{__import__('jarvis').__version__} - Ativo")
    print(f"  Usuário: {settings.user.name}")
    print(f"  Transporte: Local (Desktop)")
    print(f"  Diga 'JARVIS' ou digite comandos")
    print(f"{'='*50}\n")

    try:
        yield app
    finally:
        if app._transport_local:
            await app._transport_local.stop()


async def main():
    # Garante arquivos de config
    ensure_config_files()

    # Cria aplicação
    app = create_application()

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

    async with lifespan(app):
        await shutdown_event.wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass