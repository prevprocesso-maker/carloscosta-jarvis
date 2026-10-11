from __future__ import annotations

import asyncio
import threading
import time
import numpy as np
import sounddevice as sd
from collections import deque
from typing import AsyncIterator, Callable, Awaitable, Optional
from pathlib import Path
import sys

from jarvis.transports.base import Transport
from jarvis.core.brain import ToolCall, BrainResponse
from jarvis.core.config import settings


class WhisperSTT:
    """Wrapper do faster-whisper com auto device detection."""

    def __init__(self, model_name: str = "large-v3", device: str = "auto", language: str = "pt"):
        from faster_whisper import WhisperModel
        import torch

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        compute = "float16" if device == "cuda" else "int8"

        print(f"[STT] Carregando Whisper '{model_name}' em {device} ({compute})...")

        # Modelo local bundlado no instalador
        local_model = Path(__file__).resolve().parent.parent.parent.parent / "models" / f"whisper-{model_name}"
        model_path = str(local_model) if local_model.exists() else model_name

        self._model = WhisperModel(model_path, device=device, compute_type=compute)
        self._language = language if language != "auto" else None
        print(f"[STT] Whisper '{model_name}' pronto")

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcreve array float32 mono 16kHz."""
        segments, _ = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=1,
            best_of=1,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        return " ".join(s.text for s in segments).strip()


class LocalVAD:
    """Voice Activity Detection local com threshold adaptativo."""

    def __init__(self, initial_threshold: float = 0.09, sample_rate: int = 16000):
        self.threshold = initial_threshold
        self.sample_rate = sample_rate
        self._calibration_samples: deque = deque(maxlen=500)
        self._calibrated = False

    def process(self, chunk: np.ndarray) -> tuple[bool, float]:
        """
        Processa chunk de áudio.
        Returns: (is_speech, rms)
        """
        rms = float(np.sqrt(np.mean(chunk**2) + 1e-12))

        # Auto-calibração nos primeiros segundos
        if not self._calibrated:
            self._calibration_samples.append(rms)
            if len(self._calibration_samples) >= 200:
                # Threshold = 2.5x ruído de fundo mediano
                noise_floor = np.median(list(self._calibration_samples)[:100])
                self.threshold = max(0.03, noise_floor * 2.5)
                self._calibrated = True
                print(f"[VAD] Calibrado: threshold={self.threshold:.4f}, noise_floor={noise_floor:.4f}")

        return rms > self.threshold, rms

    def reset(self):
        self._calibration_samples.clear()
        self._calibrated = False


class GeminiLiveTTS:
    """Streaming TTS via Gemini Live API (reutiliza sessão persistente)."""

    def __init__(self, api_key: str, voice: str = "Charon", model: str = "gemini-2.5-flash-native-audio-preview"):
        from google import genai
        from google.genai import types

        self.client = genai.Client(api_key=api_key)
        self.voice = voice
        self.model = model
        self._session = None
        self._lock = asyncio.Lock()
        self._config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
            # Sem thinking para TTS puro
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

    async def _ensure_session(self):
        if self._session is None:
            self._session = await self.client.aio.live.connect(
                model=self.model, config=self._config
            ).__aenter__()

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Gera áudio PCM16 24kHz em chunks."""
        async with self._lock:
            await self._ensure_session()

            await self._session.send_client_content(
                turns={"role": "user", "parts": [{"text": f"Leia literalmente: {text}"}]},
                turn_complete=True,
            )

            async for response in self._session.receive():
                for part in response.data or []:
                    yield part
                if response.server_content and response.server_content.turn_complete:
                    break

    async def close(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
            self._session = None


class LocalAudioTransport(Transport):
    """Transport local: Mic → Whisper → Brain → Gemini TTS → Speaker."""

    def __init__(self, brain, session_mgr):
        self.brain = brain
        self.session_mgr = session_mgr

        # Config
        self.sample_rate = settings.transport_local.sample_rate
        self.chunk_size = settings.transport_local.chunk_size
        self.whisper_model = settings.transport_local.whisper_model
        self.whisper_device = settings.transport_local.whisper_device

        # Componentes
        self.stt: Optional[WhisperSTT] = None
        self.vad: Optional[LocalVAD] = None
        self.tts: Optional[GeminiLiveTTS] = None

        # Estado
        self._running = False
        self._session_id = "local_main"
        self._audio_buffer = deque()
        self._stream: Optional[sd.InputStream] = None
        self._process_task: Optional[asyncio.Task] = None

        # Callbacks
        self._on_audio_out: Optional[Callable[[bytes], Awaitable[None]]] = None
        self._on_text_out: Optional[Callable[[str], Awaitable[None]]] = None
        self._on_tool_call: Optional[Callable[[ToolCall], Awaitable[dict]]] = None

    async def start(
        self,
        on_audio_out: Callable[[bytes], Awaitable[None]],
        on_text_out: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[ToolCall], Awaitable[dict]],
    ) -> None:
        self._on_audio_out = on_audio_out
        self._on_text_out = on_text_out
        self._on_tool_call = on_tool_call

        # Inicializa componentes
        self.stt = WhisperSTT(
            model_name=self.whisper_model,
            device=self.whisper_device,
            language="pt",
        )
        self.vad = LocalVAD(
            initial_threshold=settings.transport_local.vad_threshold,
            sample_rate=self.sample_rate,
        )
        self.tts = GeminiLiveTTS(
            api_key=settings.gemini.api_key,
            voice=settings.gemini.live_voice,
        )

        # Stream de captura
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.chunk_size,
            callback=self._audio_callback,
        )
        self._stream.start()

        self._running = True
        self._process_task = asyncio.create_task(self._process_loop())
        print("[Transport Local] Iniciado - ouvindo...")

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"[Audio] Status: {status}")
        if self._running:
            # Copia para buffer thread-safe
            self._audio_buffer.append(indata.copy())

    async def _process_loop(self):
        speech_frames = deque()
        silence_frames = 0
        min_speech_frames = int(0.3 * self.sample_rate / self.chunk_size)  # 300ms mínimo
        max_silence_frames = int(1.0 * self.sample_rate / self.chunk_size)  # 1s silêncio

        while self._running:
            if not self._audio_buffer:
                await asyncio.sleep(0.01)
                continue

            chunk = self._audio_buffer.popleft()
            is_speech, rms = self.vad.process(chunk.flatten())

            if is_speech:
                speech_frames.append(chunk)
                silence_frames = 0
            elif speech_frames:
                silence_frames += 1

            # Fim de utterance detectado
            if speech_frames and silence_frames >= max_silence_frames:
                if len(speech_frames) >= min_speech_frames:
                    await self._process_utterance(speech_frames)
                speech_frames.clear()
                silence_frames = 0
                self.vad.reset()  # Recalibra VAD

            await asyncio.sleep(0)

    async def _process_utterance(self, frames: deque):
        """Processa fala completa: STT → Brain → TTS."""
        try:
            # Concatena frames
            audio = np.concatenate(list(frames), axis=0).flatten().astype(np.float32)

            # STT
            t0 = time.time()
            text = self.stt.transcribe(audio)
            stt_time = time.time() - t0
            print(f"[STT] {stt_time:.2f}s: '{text}'")

            if not text or len(text.strip()) < 2:
                return

            # Callback de transcrição (opcional)
            if self._on_text_out:
                await self._on_text_out(f"🎤 {text}")

            # Brain
            context = self.session_mgr.get_or_create("local", settings.user.name, self._session_id)
            context.add_message("user", text)

            brain_start = time.time()
            async for response in self.brain.process(context, text_input=text):
                if response.text and self._on_text_out:
                    await self._on_text_out(response.text)

                if response.tool_calls:
                    for tc in response.tool_calls:
                        result = await self._on_tool_call(tc)
                        context.add_message("tool", str(result), tc.call_id)

                if response.turn_complete:
                    brain_time = time.time() - brain_start
                    print(f"[Brain] {brain_time:.2f}s completo")

                    # TTS se há resposta textual
                    if response.text:
                        await self._speak_response(response.text)
                    break

        except Exception as e:
            print(f"[Transport Local] Erro processando utterance: {e}")

    async def _speak_response(self, text: str):
        """Stream TTS → callback audio_out."""
        try:
            async for chunk in self.tts.synthesize_stream(text):
                if self._on_audio_out and chunk:
                    await self._on_audio_out(chunk)
        except Exception as e:
            print(f"[TTS] Erro: {e}")

    async def send_audio_in(self, chunk: bytes) -> None:
        # Para compatibilidade - transport local usa mic direto
        pass

    async def send_text_in(self, text: str) -> None:
        """Entrada de texto (digitado na UI)."""
        context = self.session_mgr.get_or_create("local", settings.user.name, self._session_id)
        context.add_message("user", text)

        async for response in self.brain.process(context, text_input=text):
            if response.text and self._on_text_out:
                await self._on_text_out(response.text)

            if response.tool_calls:
                for tc in response.tool_calls:
                    result = await self._on_tool_call(tc)
                    context.add_message("tool", str(result), tc.call_id)

            if response.turn_complete and response.text:
                await self._speak_response(response.text)
            break

    async def send_interrupt(self) -> None:
        await self.brain.interrupt(self._session_id)
        # Para TTS atual
        sd.stop()

    async def stop(self) -> None:
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
        if self.tts:
            await self.tts.close()
        print("[Transport Local] Parado")