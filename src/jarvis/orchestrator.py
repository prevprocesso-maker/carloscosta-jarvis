from __future__ import annotations

"""Orquestrador principal do JARVIS - Coordena voz, cerebro, acoes e UI."""

import asyncio
import atexit
import json
import os
import re
import sys
import threading
import time
import traceback
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import sounddevice as sd
from google import genai
from google.genai import types

from src.jarvis.core.config import get_settings
from src.jarvis.core.telemetry import get_telemetry, init_telemetry
from src.jarvis.core.guardrails import get_guardrails, init_guardrails
from src.jarvis.core.knowledge import get_knowledge_base, init_knowledge_base
from src.jarvis.core.session import SessionManager
# from src.jarvis.core.brain import get_brain_factory  # Lazy loaded in brain_factory property to avoid circular import
from src.jarvis.core.actions.registry import get_openai_schemas, execute_action
from src.jarvis.core.memory import get_memory_manager
from src.jarvis.core.plataforma import get_base_dir

from src.jarvis.ui import JarvisUI


class JarvisOrchestrator:
    """Orquestrador principal que coordena todos os componentes do JARVIS."""

    def __init__(self):
        self.settings = get_settings()
        self.base_dir = get_base_dir()

        # Paths
        self.prompt_path = self.base_dir / "core" / "prompt.txt"
        self.voice_diag_path = self.base_dir / "logs" / "voice_session.log"

        # Audio config
        self.live_model = "models/gemini-2.5-flash-native-audio-preview-12-2025"
        self.channels = 1
        self.send_sample_rate = 16000
        self.receive_sample_rate = 24000
        self.chunk_size = 1024
        self.mic_chunk_size = 640

        # Components (lazy loaded)
        self.ui = None
        self._brain_factory = None
        self.session_manager = SessionManager()
        self.session_mgr = self.session_manager  # Alias for backward compatibility
        self.memory_manager = get_memory_manager()
        self.telemetry = get_telemetry()
        self.guardrails = get_guardrails()
        self.knowledge_base = get_knowledge_base()

        # Transport components
        self._transport_local = None
        self._transport_remote = None

        # Callbacks for transport output
        self._audio_out_callback = None
        self._text_out_callback = None
        self._tool_call_callback = None

        # State
        self.running = False
        self.shutdown_event = asyncio.Event()
        self._voice_loop_task = None
        self._poll_tasks = []

        # Audio constants
        self._PICO_MINIMO_DA_FALA = 0.09
        self._PICO_DE_RESGATE = 0.045
        self._MODELO_WHISPER = "small"
        self._MODELO_WHISPER_GPU = "large-v3"

        # Whisper model (loaded lazily)
        self._whisper_model = None
        self._whisper_on_gpu = False

    @property
    def brain_factory(self):
        """Lazy load brain factory to avoid circular imports."""
        if self._brain_factory is None:
            from src.jarvis.core.brain import get_brain_factory
            self._brain_factory = get_brain_factory(self.settings, self.memory_manager)
        return self._brain_factory

    async def initialize_brains(self) -> None:
        """Inicializa os cerebros (pre-carrega as factories)."""
        _ = self.brain_factory
        self.brain_factory.initialize_brains()
        print("[JARVIS] Cerebros inicializados")

    async def start_transports(self) -> None:
        """Inicia transports habilitados (local e/ou remoto)."""
        # Callbacks para output
        async def on_audio_out(chunk: bytes):
            if self._audio_out_callback:
                await self._audio_out_callback(chunk)

        async def on_text_out(text: str):
            if self._text_out_callback:
                await self._text_out_callback(text)

        async def on_tool_call(tool_call):
            if self._tool_call_callback:
                return await self._tool_call_callback(tool_call)
            return {}

        # Inicia transport local (Desktop)
        if self.settings.transports.local.enabled:
            try:
                from jarvis.transports.local import LocalAudioTransport
                # LocalAudioTransport expects brain instance, not factory
                brain = self.brain_factory.get_fast_brain()
                # Detect test mode (no display/microphone)
                test_mode = getattr(self, '_test_mode', False) or os.environ.get('QT_QPA_PLATFORM') == 'offscreen'
                self._transport_local = LocalAudioTransport(
                    brain=brain,
                    session_mgr=self.session_manager,
                    test_mode=test_mode,
                )
                await self._transport_local.start(
                    on_audio_out=on_audio_out,
                    on_text_out=on_text_out,
                    on_tool_call=on_tool_call,
                )
                print("[JARVIS] Transport local iniciado")
            except Exception as e:
                self.telemetry.logger.error(f"Erro ao iniciar transport local: {e}")

        # Inicia transport remoto (Web)
        if self.settings.transports.remote.enabled:
            try:
                from jarvis.transports.remote import create_remote_voice_server
                self._transport_remote = create_remote_voice_server(
                    brain_factory=self.brain_factory,
                    session_mgr=self.session_manager,
                )
                # Remote server doesn't need start() - it runs via uvicorn
                print("[JARVIS] Transport remoto configurado")
            except Exception as e:
                self.telemetry.logger.error(f"Erro ao configurar transport remoto: {e}")

        # Inicia WhatsApp Bridge
        if self.settings.transports.webhook.enabled:
            try:
                from src.jarvis.integrations import start_whatsapp_bridge
                from src.jarvis.core.plataforma import get_base_dir
                bridge_dir = get_base_dir() / "whatsapp_bridge"
                self._whatsapp_bridge = await start_whatsapp_bridge(
                    port=self.settings.transports.webhook.whatsapp_port,
                    bridge_dir=bridge_dir,
                )
                # Wire up WhatsApp callbacks
                self._whatsapp_bridge.set_message_callback(self._on_whatsapp_message)
                self._whatsapp_bridge.set_status_callback(self._on_whatsapp_status)
                print("[JARVIS] WhatsApp Bridge iniciado")
            except Exception as e:
                self.telemetry.logger.error(f"Erro ao iniciar WhatsApp Bridge: {e}")

    async def shutdown(self) -> None:
        print("[JARVIS] Encerrando...")
        self.running = False
        self.shutdown_event.set()

        if self._voice_loop_task:
            self._voice_loop_task.cancel()
            try:
                await self._voice_loop_task
            except asyncio.CancelledError:
                pass

        for task in self._poll_tasks:
            task.cancel()
        if self._poll_tasks:
            await asyncio.gather(*self._poll_tasks, return_exceptions=True)

        # Close WhatsApp Bridge
        if hasattr(self, '_whatsapp_bridge') and self._whatsapp_bridge:
            try:
                await self._whatsapp_bridge.close()
            except Exception as e:
                self.telemetry.logger.error(f"Erro ao fechar WhatsApp Bridge: {e}")

        await self.telemetry.shutdown()

        if self.ui:
            self.ui.close()

        print("[JARVIS] Encerrado com sucesso")

    def run(self) -> None:
        """Run the main application loop with UI."""
        self.running = True

        # Initialize UI
        self._init_ui()

        # Wire up UI callbacks
        self._wire_ui_callbacks()

        # Start voice loop
        self._voice_loop_task = asyncio.create_task(self._voice_loop())

        # Start poll tasks
        self._poll_tasks = [
            asyncio.create_task(self._poll_whatsapp_alerts()),
            asyncio.create_task(self._poll_due_scheduled_messages()),
            asyncio.create_task(self._poll_whatsapp_agent_events()),
            asyncio.create_task(self._poll_system_monitor()),
        ]

        # Run UI event loop (blocking)
        try:
            import sys
            sys.exit(self.ui._app.exec())
        except KeyboardInterrupt:
            pass
        finally:
            asyncio.run(self.shutdown())

    def _init_ui(self) -> None:
        """Initialize the PyQt6 UI."""
        from src.jarvis.core.plataforma import get_base_dir
        base_dir = get_base_dir()
        face_path = str(base_dir / "assets" / "jarvis_face.png")
        
        self.ui = JarvisUI(face_path=face_path)
        # Store reference for backward compatibility
        self.ui.app = self.ui._app
        self.ui.root = self.ui.root

    def _wire_ui_callbacks(self) -> None:
        """Wire up UI callbacks to orchestrator methods."""
        if not self.ui:
            return
        
        # Text command from UI (typed input)
        self.ui.on_text_command = self._on_ui_text_command
        
        # Remote button clicked
        self.ui.on_remote_clicked = self._on_ui_remote_clicked
        
        # WhatsApp button clicked
        self.ui.on_whatsapp_clicked = self._on_ui_whatsapp_clicked
        
        # Interrupt button (stop speaking)
        self.ui.on_interrupt = self._on_ui_interrupt
        
        # Computer Use button
        self.ui.on_computer_use = self._on_ui_computer_use
        
        # Computer Use estado
        self.ui.on_computer_use_estado = self._get_computer_use_estado
        
        # So com nome toggle
        self.ui.on_so_nome_changed = self._on_so_nome_changed
        
        # Cowork action
        self.ui.on_cowork_acao = self._on_cowork_acao

    def _on_ui_text_command(self, text: str) -> None:
        """Handle text command from UI."""
        if text:
            asyncio.create_task(self._process_text_command(text))

    async def _process_text_command(self, text: str) -> None:
        """Process text command through brain."""
        try:
            brain = self.brain_factory.get_fast_brain()
            session = self.session_manager.get_or_create("local", "user", "ui_text")
            session.add_message("user", text)
            
            async for response in brain.process(None, text_input=text):
                if response.text:
                    session.add_message("assistant", response.text)
                    if self.ui:
                        self.ui.write_log(f"JARVIS: {response.text}")
                    break
        except Exception as e:
            self.telemetry.logger.error(f"UI text command error: {e}")
            if self.ui:
                self.ui.write_log(f"Erro: {e}")

    def _on_ui_remote_clicked(self) -> tuple[str, str] | None:
        """Handle remote button click - return (url, key)."""
        if self._transport_remote:
            port = self.settings.transports.remote.port
            token = self.settings.transports.remote.token or ""
            return (f"http://localhost:{port}", token)
        return None

    def _on_ui_whatsapp_clicked(self) -> None:
        """Handle WhatsApp button click."""
        # Trigger WhatsApp QR overlay
        if self.ui:
            self.ui.open_whatsapp_qr()

    def _on_ui_interrupt(self) -> None:
        """Handle interrupt button - stop current speech."""
        # Cancel current voice loop processing
        if self._voice_loop_task and not self._voice_loop_task.done():
            self._voice_loop_task.cancel()
        # Stop TTS if playing
        if hasattr(self, 'tts') and self.tts:
            try:
                # Signal interrupt to transport
                asyncio.create_task(self._signal_interrupt())
            except Exception:
                pass

    async def _signal_interrupt(self) -> None:
        """Signal interrupt to active transports."""
        if self._transport_local and hasattr(self._transport_local, 'send_interrupt'):
            await self._transport_local.send_interrupt()
        if self._transport_remote and hasattr(self._transport_remote, 'send_interrupt'):
            await self._transport_remote.send_interrupt()

    def _get_computer_use_estado(self) -> str:
        """Get Computer Use state for UI."""
        # Return current state: "livre", "esperando", "trabalhando"
        return "livre"

    def _on_ui_computer_use(self) -> None:
        """Handle Computer Use button click."""
        # Trigger computer use workflow
        if self.ui:
            self.ui.write_log("Computer Use ativado")

    def _on_so_nome_changed(self, enabled: bool) -> None:
        """Handle 'Só com nome' toggle."""
        # Save to config
        self.settings._config['ui']['so_com_nome'] = enabled
        if self.ui:
            self.ui.write_log(f"Modo 'só com nome': {'ligado' if enabled else 'desligado'}")

    def _on_cowork_acao(self, acao: str, valor: str = "") -> None:
        """Handle Cowork action from UI."""
        if self.ui:
            self.ui.write_log(f"Cowork: {acao} = {valor}")

    async def _on_whatsapp_message(self, message: dict) -> None:
        """Handle incoming WhatsApp message."""
        from_number = message.get("from", "")
        text = message.get("text", "")
        
        if self.ui:
            self.ui.write_log(f"WhatsApp de {from_number}: {text}")
        
        # Process with brain if it's a command
        if text.startswith("jarvis") or text.startswith("JARVIS"):
            cmd = text[6:].strip()
            await self._process_text_command(cmd)
            
            # Send response back via WhatsApp
            if self._whatsapp_bridge and self._whatsapp_bridge.is_connected:
                brain = self.brain_factory.get_fast_brain()
                session = self.session_manager.get_or_create("whatsapp", from_number, from_number)
                session.add_message("user", cmd)
                
                async for response in brain.process(None, text_input=cmd):
                    if response.text:
                        session.add_message("assistant", response.text)
                        await self._whatsapp_bridge.send_message(from_number, response.text)
                        break

    async def _on_whatsapp_status(self, status: dict) -> None:
        """Handle WhatsApp connection status change."""
        connected = status.get("connected", False)
        qr_code = status.get("qr_code")
        
        if self.ui:
            if connected:
                self.ui.write_log("WhatsApp conectado")
                self.ui.notify_phone_connected()
            else:
                self.ui.write_log("WhatsApp desconectado")
                if qr_code:
                    self.ui.write_log("Novo QR Code gerado")
        
        # Update UI state
        if self.ui:
            # The UI will handle QR code display via open_whatsapp_qr
            pass

    async def _on_ui_whatsapp_clicked(self) -> None:
        """Handle WhatsApp button click in UI."""
        if self._whatsapp_bridge:
            # If not connected, show QR code
            if not self._whatsapp_bridge.is_connected:
                qr = await self._whatsapp_bridge.get_qr_code()
                if qr:
                    # The UI handles QR display internally
                    self.ui.open_whatsapp_qr()
            else:
                # Already connected - maybe show contacts or settings
                contacts = await self._whatsapp_bridge.get_contacts()
                if self.ui:
                    self.ui.write_log(f"WhatsApp: {len(contacts.get('contacts', []))} contatos")

    async def _voice_loop(self) -> None:
        """Loop principal de voz: STT -> Brain -> TTS."""
        # Initialize Whisper
        await self._init_whisper()

        # Initialize TTS (Gemini Live)
        from jarvis.transports.local import GeminiLiveTTS
        self.tts = GeminiLiveTTS(
            api_key=self.settings.gemini.api_key,
            voice=self.settings.gemini.live_voice,
        )

        while self.running:
            try:
                # 1. Listen for wake word / voice input
                audio_data = await self._listen_for_voice()
                if not audio_data:
                    continue

                # 2. Transcribe (STT)
                text = await self._transcribe(audio_data)
                if not text:
                    continue

                # 3. Check wake word
                if not self._check_wake_word(text):
                    continue

                # 4. Process with brain
                response = await self._process_with_brain(text)

                # 5. Speak response (TTS)
                if response:
                    await self._speak(response)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.telemetry.logger.error(f"Voice loop error: {e}")
                await asyncio.sleep(1)

    async def _init_whisper(self) -> None:
        """Inicializa modelo Whisper (GPU se disponivel)."""
        from faster_whisper import WhisperModel
        import torch

        if self._try_cuda():
            try:
                self._whisper_model = WhisperModel(
                    self._MODELO_WHISPER_GPU, device="cuda", compute_type="float16")
                self._whisper_on_gpu = True
                print(f"[JARVIS] Whisper: {self._MODELO_WHISPER_GPU} na GPU")
                return
            except Exception as e:
                print(f"[JARVIS] GPU recusou Whisper ({e}); usando CPU")

        # Fallback CPU
        import os
        local_model = Path(__file__).resolve().parent.parent / "models" / "whisper-small"
        model_path = str(local_model) if (local_model / "model.bin").exists() else "small"

        import os as _os
        _nucleos = max(4, (_os.cpu_count() or 8) // 2)
        self._whisper_model = WhisperModel(
            "small", device="cpu", compute_type="int8", cpu_threads=_nucleos)
        self._whisper_on_gpu = False
        print(f"[JARVIS] Whisper: {self._MODELO_WHISPER} no CPU")

    def _try_cuda(self) -> bool:
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False

    async def _listen_for_voice(self) -> Optional[bytes]:
        """Captura audio do microfone com VAD."""
        # Simplified - in reality would use sounddevice with VAD
        # This is a placeholder for the actual implementation
        await asyncio.sleep(0.1)
        return None

    async def _transcribe(self, audio_data: bytes) -> Optional[str]:
        """Transcreve audio usando Whisper."""
        if not self._whisper_model:
            return None

        try:
            import numpy as np
            audio_array = np.frombuffer(audio_data, dtype=np.float32)
            segments, _ = self._whisper_model.transcribe(
                audio_array,
                language="pt",
                beam_size=1,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )
            return " ".join(s.text for s in segments).strip()
        except Exception as e:
            self.telemetry.logger.error(f"Transcription error: {e}")
            return None

    def _check_wake_word(self, text: str) -> bool:
        """Verifica se o texto contem a wake word."""
        text_lower = text.lower().strip()
        wake_words = ["jarvis", "friday"]
        return any(w in text_lower for w in wake_words)

    async def _process_with_brain(self, text: str) -> Optional[str]:
        """Processa texto com o cerebro e executa actions."""
        brain = self.brain_factory
        if not brain:
            return "Cerebro nao disponivel"

        # Create session context
        session = self.session_manager.get_or_create("local", "user", "main")
        session.add_message("user", text)

        # Get available tools
        from src.jarvis.core.actions.registry import get_openai_schemas
        tools = get_openai_schemas()

        # Process with brain
        async for response in self.brain_factory.process(None, text_input=text):
            if response.text:
                session.add_message("assistant", response.text)
                return response.text
            elif response.tool_calls:
                for tc in response.tool_calls:
                    result = execute_action(tc.name, tc.args, session)
                    session.add_message("tool", str(result), tc.call_id)

        return "Processado"

    async def _speak(self, text: str) -> None:
        """Fala o texto usando TTS."""
        if self.tts:
            try:
                async for chunk in self.tts.synthesize_stream(text):
                    # Play audio chunk
                    pass
            except Exception as e:
                self.telemetry.logger.error(f"TTS error: {e}")

    def _check_wake_word(self, text: str) -> bool:
        """Verifica se o texto contem a wake word."""
        text_lower = text.lower().strip()
        wake_words = ["jarvis", "friday"]
        return any(w in text_lower for w in wake_words)

    def _try_cuda(self) -> bool:
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False

    async def _init_whisper(self) -> None:
        """Inicializa modelo Whisper (GPU se disponivel)."""
        from faster_whisper import WhisperModel
        import torch

        if self._try_cuda():
            try:
                self._whisper_model = WhisperModel(
                    "large-v3", device="cuda", compute_type="float16")
                self._whisper_on_gpu = True
                print("[JARVIS] Whisper: large-v3 na GPU")
                return
            except Exception as e:
                print(f"[JARVIS] GPU recusou Whisper ({e}); usando CPU")

        # Fallback CPU
        import os
        local_model = Path(__file__).resolve().parent.parent / "models" / "whisper-small"
        model_path = str(local_model) if (local_model / "model.bin").exists() else "small"

        import os as _os
        _nucleos = max(4, (_os.cpu_count() or 8) // 2)
        self._whisper_model = WhisperModel(
            "small", device="cpu", compute_type="int8", cpu_threads=_nucleos)
        self._whisper_on_gpu = False
        print(f"[JARVIS] Whisper: {self._MODELO_WHISPER} no CPU")

    def _try_cuda(self) -> bool:
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False
