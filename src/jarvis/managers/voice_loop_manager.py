from __future__ import annotations

"""Voice Loop Manager - Handles STT/TTS voice loop."""

import asyncio
import os
from pathlib import Path
from typing import Optional

from faster_whisper import WhisperModel

from src.jarvis.core.config import settings
from src.jarvis.core.telemetry import get_telemetry


class VoiceLoopManager:
    """Manages the voice loop: STT -> Brain -> TTS."""

    def __init__(self, orchestrator_ref):
        self.orchestrator = orchestrator_ref
        self.settings = orchestrator_ref.settings
        self.telemetry = get_telemetry()
        
        # Audio config
        self.live_model = "models/gemini-2.5-flash-native-audio-preview-12-2025"
        self.channels = 1
        self.send_sample_rate = 16000
        self.receive_sample_rate = 24000
        self.chunk_size = 1024
        self.mic_chunk_size = 640
        
        # Whisper config
        self._MODELO_WHISPER = "small"
        self._MODELO_WHISPER_GPU = "large-v3"
        self._whisper_model = None
        self._whisper_on_gpu = False
        
        # TTS
        self.tts = None
        
        # State
        self._voice_loop_task: Optional[asyncio.Task] = None
        self.running = False

    async def initialize(self):
        """Initialize voice components."""
        await self._init_whisper()
        
        from jarvis.transports.local import GeminiLiveTTS
        self.tts = GeminiLiveTTS(
            api_key=self.settings.gemini.api_key,
            voice=self.settings.gemini.live_voice,
        )

    async def start(self):
        """Start the voice loop."""
        self.running = True
        self._voice_loop_task = asyncio.create_task(self._voice_loop())

    async def stop(self):
        """Stop the voice loop."""
        self.running = False
        if self._voice_loop_task:
            self._voice_loop_task.cancel()
            try:
                await self._voice_loop_task
            except asyncio.CancelledError:
                pass

    async def _init_whisper(self):
        """Inicializa modelo Whisper (GPU se disponivel)."""
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
        # TODO: Implement actual microphone capture with VAD
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

    async def _speak(self, text: str):
        """Fala o texto usando TTS."""
        if self.tts:
            try:
                async for chunk in self.tts.synthesize_stream(text):
                    # TODO: Play audio chunk via sounddevice
                    pass
            except Exception as e:
                self.telemetry.logger.error(f"TTS error: {e}")

    def _check_wake_word(self, text: str) -> bool:
        """Verifica se texto contém wake word."""
        wake_words = ["jarvis", "friday", "hey jarvis", "ok jarvis"]
        text_lower = text.lower()
        return any(w in text_lower for w in wake_words)

    async def process_voice_input(self, text: str) -> Optional[str]:
        """Process voice input through brain."""
        if not self._check_wake_word(text):
            return None
        
        # Delegate to orchestrator's brain processing
        return await self.orchestrator._process_with_brain(text)

    async def _voice_loop(self):
        """Loop principal de voz: STT -> Brain -> TTS."""
        while self.orchestrator.running:
            try:
                audio_data = await self._listen_for_voice()
                if not audio_data:
                    continue

                text = await self._transcribe(audio_data)
                if not text:
                    continue

                if not self._check_wake_word(text):
                    continue

                response = await self.orchestrator._process_with_brain(text)

                if response:
                    await self._speak(response)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.telemetry.logger.error(f"Voice loop error: {e}")
                await asyncio.sleep(1)