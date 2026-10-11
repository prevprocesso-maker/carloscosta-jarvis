from __future__ import annotations

import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch

from jarvis.transports.local.audio import WhisperSTT, LocalVAD, GeminiLiveTTS, LocalAudioTransport
from jarvis.core.brain.base import Brain, BrainContext, BrainResponse, ToolCall


class MockBrain(Brain):
    def __init__(self):
        self._config = MagicMock()

    async def process(self, context, audio_chunk=None, text_input=None):
        yield BrainResponse(text="Resposta de teste", turn_complete=True)

    async def interrupt(self, session_id: str):
        pass

    def get_available_tools(self):
        return []

    async def tts_stream(self, text: str, voice=None):
        yield b""

    @property
    def config(self):
        return self._config


class MockSessionMgr:
    def get_or_create(self, transport, user_id, session_id=None):
        return MagicMock()


@pytest.fixture
def mock_brain():
    return MockBrain()


@pytest.fixture
def mock_session_mgr():
    return MockSessionMgr()


def test_vad_initialization():
    vad = LocalVAD(initial_threshold=0.09, sample_rate=16000)
    assert vad.threshold == 0.09
    assert vad.sample_rate == 16000
    assert not vad._calibrated


def test_vad_process_silence():
    vad = LocalVAD(initial_threshold=0.09, sample_rate=16000)
    # Silêncio (zeros)
    chunk = np.zeros(640, dtype=np.float32)
    is_speech, rms = vad.process(chunk)
    assert is_speech is False
    assert rms < 1e-6  # near zero


def test_vad_process_speech():
    vad = LocalVAD(initial_threshold=0.01, sample_rate=16000)
    # Sinal com amplitude
    chunk = np.ones(640, dtype=np.float32) * 0.1
    is_speech, rms = vad.process(chunk)
    assert is_speech is True
    assert rms > 0.0


def test_vad_auto_calibration():
    vad = LocalVAD(initial_threshold=0.5, sample_rate=16000)  # Threshold alto
    # Alimenta com ruído baixo por 200 frames
    noise = np.random.normal(0, 0.01, 640).astype(np.float32)
    for _ in range(210):
        vad.process(noise)
    assert vad._calibrated
    # Threshold deve ter se ajustado para ~2.5x noise floor
    assert vad.threshold < 0.1


@pytest.mark.asyncio
async def test_gemini_live_tts_init():
    with patch("google.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_session = AsyncMock()
        mock_client.aio.live.connect.return_value.__aenter__.return_value = mock_session

        tts = GeminiLiveTTS(api_key="test_key", voice="Charon")
        assert tts.client == mock_client
        assert tts.voice == "Charon"


@pytest.mark.asyncio
async def test_local_audio_transport_start_stop(mock_brain, mock_session_mgr):
    with patch("jarvis.transports.local.audio.sd") as mock_sd, \
         patch("jarvis.transports.local.audio.WhisperSTT"), \
         patch("jarvis.transports.local.audio.GeminiLiveTTS") as mock_tts_class:

        mock_stream = MagicMock()
        mock_sd.InputStream.return_value = mock_stream

        # Mock TTS with async close
        mock_tts = AsyncMock()
        mock_tts.close = AsyncMock()
        mock_tts_class.return_value = mock_tts

        transport = LocalAudioTransport(mock_brain, mock_session_mgr)

        async def dummy_audio(chunk): pass
        async def dummy_text(text): pass
        async def dummy_tool(tc): return {}

        await transport.start(dummy_audio, dummy_text, dummy_tool)
        assert transport._running is True
        mock_stream.start.assert_called_once()

        await transport.stop()
        assert transport._running is False
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()
        mock_tts.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_audio_transport_send_text(mock_brain, mock_session_mgr):
    with patch("jarvis.transports.local.audio.sd"), \
         patch("jarvis.transports.local.audio.WhisperSTT"), \
         patch("jarvis.transports.local.audio.GeminiLiveTTS") as mock_tts_class:

        mock_tts = AsyncMock()
        mock_tts.close = AsyncMock()
        mock_tts_class.return_value = mock_tts

        transport = LocalAudioTransport(mock_brain, mock_session_mgr)

        async def dummy_audio(chunk): pass
        async def dummy_text(text): pass
        async def dummy_tool(tc): return {}

        await transport.start(dummy_audio, dummy_text, dummy_tool)
        await transport.send_text_in("que horas são")
        await transport.stop()
        mock_tts.close.assert_awaited_once()


def test_whisper_stt_init():
    with patch("faster_whisper.WhisperModel") as mock_model:
        mock_model.return_value = MagicMock()
        stt = WhisperSTT(model_name="large-v3", device="cpu", language="pt")
        assert stt._language == "pt"