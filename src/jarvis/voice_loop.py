from __future__ import annotations

"""Loop de voz do JARVIS - STT -> Brain -> TTS."""

import asyncio
import numpy as np
import sounddevice as sd
from pathlib import Path
from typing import Optional

from src.jarvis.core.voice import GeminiLiveTTS
from src.jarvis.core.telemetry import get_telemetry
from src.jarvis.orchestrator import JarvisOrchestrator


class VoiceLoop:
    """Gerencia o loop de voz completo: captura -> STT -> Brain -> TTS."""
    
    def __init__(self, orchestrator: JarvisOrchestrator):
        self.orchestrator = orchestrator
        self.telemetry = get_telemetry()
        self._whisper_model = None
        self._whisper_on_gpu = False
        self.tts: Optional[GeminiLiveTTS] = None
        self._running = False
        self._listen_task: Optional[asyncio.Task] = None
        
        # Audio config
        self.send_sample_rate = 16000
        self.receive_sample_rate = 24000
        self.chunk_size = 1024
        self.mic_chunk_size = 640
        
        # Whisper config
        self._MODELO_WHISPER = "small"
        self._MODELO_WHISPER_GPU = "large-v3"
        self._PICO_MINIMO_DA_FALA = 0.09
        self._PICO_DE_RESGATE = 0.045
        
        # VAD state
        self._audio_buffer = []
        self._speech_frames = 0
        self._silence_frames = 0
        self._vad_threshold = 0.09
        
        # Stream
        self._stream: Optional[sd.InputStream] = None
        
    async def initialize(self) -> None:
        """Inicializa componentes de voz."""
        await self._init_whisper()
        self.tts = GeminiLiveTTS()
        print("[VoiceLoop] Inicializado")
    
    async def _init_whisper(self) -> None:
        """Inicializa modelo Whisper (GPU se disponível)."""
        from faster_whisper import WhisperModel
        import torch
        
        if self._try_cuda():
            try:
                self._whisper_model = WhisperModel(
                    "large-v3", device="cuda", compute_type="float16")
                self._whisper_on_gpu = True
                print("[VoiceLoop] Whisper: large-v3 na GPU")
                return
            except Exception as e:
                print(f"[VoiceLoop] GPU recusou Whisper ({e}); usando CPU")
        
        # Fallback CPU
        import os
        local_model = Path(__file__).resolve().parent.parent / "models" / "whisper-small"
        model_path = str(local_model) if (local_model / "model.bin").exists() else "small"
        
        import os as _os
        _nucleos = max(4, (_os.cpu_count() or 8) // 2)
        self._whisper_model = WhisperModel(
            "small", device="cpu", compute_type="int8", cpu_threads=_nucleos)
        self._whisper_on_gpu = False
        print("[VoiceLoop] Whisper: small no CPU")
    
    def _try_cuda(self) -> bool:
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False
    
    async def start(self) -> None:
        """Inicia o loop de voz."""
        self._running = True
        
        # Start audio stream
        self._stream = sd.InputStream(
            samplerate=self.send_sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.mic_chunk_size,
            callback=self._audio_callback,
        )
        self._stream.start()
        
        # Start processing loop
        self._listen_task = asyncio.create_task(self._process_loop())
        print("[VoiceLoop] Iniciado")
    
    async def stop(self) -> None:
        """Para o loop de voz."""
        self._running = False
        
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        
        print("[VoiceLoop] Parado")
    
    def _audio_callback(self, indata, frames, time_info, status):
        """Callback de áudio do sounddevice (roda em thread separada)."""
        if status:
            print(f"[Audio] Status: {status}")
        if self._running:
            # Copy to buffer for processing
            self._audio_buffer.append(indata.copy())
    
    async def _process_loop(self) -> None:
        """Loop principal de processamento de áudio."""
        VAD_THRESHOLD = 0.09
        MIN_SPEECH_FRAMES = int(0.3 * self.send_sample_rate / self.mic_chunk_size)  # 300ms
        MAX_SILENCE_FRAMES = int(1.0 * self.send_sample_rate / self.mic_chunk_size)  # 1s
        
        while self._running:
            if not self._audio_buffer:
                await asyncio.sleep(0.01)
                continue
            
            # Get audio chunk
            chunk = self._audio_buffer.pop(0)
            audio_flat = chunk.flatten()
            
            # VAD
            rms = float(np.sqrt(np.mean(audio_flat**2) + 1e-12))
            is_speech = rms > self._vad_threshold
            
            if is_speech:
                self._speech_frames += 1
                self._silence_frames = 0
            elif self._speech_frames > 0:
                self._silence_frames += 1
            
            # End of utterance detected
            if self._speech_frames > 0 and self._silence_frames >= MAX_SILENCE_FRAMES:
                if self._speech_frames >= MIN_SPEECH_FRAMES:
                    await self._process_utterance()
                self._speech_frames = 0
                self._silence_frames = 0
            
            await asyncio.sleep(0)
    
    async def _process_utterance(self) -> None:
        """Processa uma fala completa: STT -> Brain -> TTS."""
        try:
            # Concatenate buffered audio
            audio = np.concatenate(self._audio_buffer, axis=0).flatten().astype(np.float32)
            self._audio_buffer.clear()
            
            # Transcribe
            text = await self._transcribe(audio)
            if not text or len(text.strip()) < 2:
                return
            
            print(f"[STT] {text}")
            
            # Check wake word
            if not self._check_wake_word(text):
                return
            
            # Process with brain
            response = await self._process_with_brain(text)
            
            # Speak response
            if response:
                await self._speak(response)
                
        except Exception as e:
            self.telemetry.logger.error(f"Voice processing error: {e}")
    
    async def _transcribe(self, audio: np.ndarray) -> Optional[str]:
        """Transcreve áudio usando Whisper."""
        if not self._whisper_model:
            return None
        
        try:
            segments, _ = self._whisper_model.transcribe(
                audio,
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
        """Verifica se o texto contém a wake word."""
        text_lower = text.lower().strip()
        wake_words = ["jarvis", "friday"]
        return any(w in text_lower for w in wake_words)
    
    async def _process_with_brain(self, text: str) -> Optional[str]:
        """Processa texto com o cérebro."""
        # This will be implemented by the orchestrator
        return await self.orchestrator._process_with_brain(text)
    
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
        """Verifica se o texto contém a wake word."""
        text_lower = text.lower().strip()
        wake_words = ["jarvis", "friday"]
        return any(w in text_lower for w in wake_words)
    
    def _try_cuda(self) -> bool:
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False