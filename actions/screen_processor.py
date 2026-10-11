from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

# Silence noisy native OpenCV camera warnings in the terminal.
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")

import numpy as np
import sounddevice as sd

try:
    import cv2
    try:
        cv2.setLogLevel(0)
    except Exception:
        pass
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    import PIL.Image
    _PIL = True
except ImportError:
    _PIL = False

from google import genai
from google.genai import types as gtypes

try:
    import requests
    _REQUESTS_OK = True
except Exception:
    requests = None
    _REQUESTS_OK = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE        = _base_dir()
_CONFIG_PATH = _BASE / "config" / "api_keys.json"



def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config_key(key: str, value) -> None:
    try:
        cfg = _load_config()
        cfg[key] = value
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[Vision] ⚠️  Could not save config key '{key}': {e}")


def _get_api_key() -> str:
    key = _load_config().get("gemini_api_key", "")
    if not key:
        raise RuntimeError("gemini_api_key not found in config.")
    return key


_QUOTA_STATE_PATH = _BASE / "config" / "vision_quota_state.json"


# ── O SOCORRO SEM COTA: o Codex da VPS (05/09/2026) ────────────────────────
#
# Pergunta do Diego: "e se a cota da Gemini acabar? As funcoes de ver imagem
# param?" Paravam. O cerebro ja tinha piso (o Codex); a visao nao tinha.
#
# Nao ha chave nova aqui: e a mesma ponte por onde o JARVIS ja manda a foto da
# tela no computer use — la o Codex olha a imagem e decide o movimento; aqui
# ele olha e responde. Quem passa a ponte e o main, no `_visao_ponte`.
_PONTE_DA_VISAO = None


def definir_ponte(ponte) -> None:
    """O main entrega a ponte da VPS (relay) para o socorro da visao."""
    global _PONTE_DA_VISAO
    _PONTE_DA_VISAO = ponte


def _anotar(evento: str, detalhe: str = "") -> None:
    """Escreve no mesmo diario do JARVIS (`logs/voice_session.log`).

    O `print` daqui nao serve para nada em producao: o JARVIS roda no
    `pythonw.exe`, que nao tem janela — a linha some. E foi por nao ter registro
    que o socorro da visao passou despercebido, devolvendo desculpa de
    OpenRouter em vez da analise.
    """
    try:
        from datetime import datetime as _dt  # noqa: PLC0415
        caminho = _BASE / "logs" / "voice_session.log"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as f:
            f.write(f"{_dt.now().isoformat(timespec='milliseconds')}\t{evento}\t{detalhe}\n")
    except Exception:
        pass


def _analisar_pelo_codex(image_bytes: bytes, user_text: str, angle: str) -> str:
    """Descreve a imagem pelo Codex. Devolve "" quando nao da para usar."""
    if _PONTE_DA_VISAO is None or not image_bytes:
        _anotar("visao_sem_socorro",
                "sem ponte com a VPS" if _PONTE_DA_VISAO is None else "sem imagem")
        return ""
    try:
        from core.cerebro_codex import CerebroCodex  # noqa: PLC0415
        olho = CerebroCodex(ponte=_PONTE_DA_VISAO)
        if not olho.disponivel():
            _anotar("visao_sem_socorro", "a ponte existe mas nao esta pronta")
            return ""
        pergunta = (str(user_text or "").strip()
                    or ("O que aparece nesta imagem da webcam?" if angle == "camera"
                        else "O que aparece nesta tela?"))
        t0 = time.time()
        texto = olho.ver(image_bytes, pergunta)
        _anotar("visao_pelo_codex",
                f"{len(image_bytes)//1024}KB {time.time()-t0:.1f}s -> {texto[:120] or 'VAZIO'}")
        return texto
    except Exception as exc:
        _anotar("visao_pelo_codex_falhou", f"{type(exc).__name__}: {str(exc)[:200]}")
        return ""


def _load_quota_state() -> dict:
    try:
        return json.loads(_QUOTA_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_quota_state(state: dict) -> None:
    try:
        _QUOTA_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _QUOTA_STATE_PATH.write_text(json.dumps(state, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[Vision] ⚠️  Could not save quota state: {e}")


def _chave_da_gemini_serve() -> bool:
    """A chave configurada tem cara de chave da Google? (ver gemini_socorro)"""
    chave = str(_load_config().get("gemini_api_key", "") or "").strip()
    try:
        from core.gemini_socorro import chave_parece_boa  # noqa: PLC0415
        return chave_parece_boa(chave)
    except Exception:
        # mesma regra do `gemini_socorro`: os DOIS formatos do Google valem
        return (chave.startswith("AIza") or chave.startswith("AQ.")) and len(chave) >= 20


def _marca_da_chave() -> str:
    """Assinatura curta da chave em uso — nunca a chave."""
    import hashlib  # noqa: PLC0415
    chave = str(_load_config().get("gemini_api_key", "") or "")
    return hashlib.sha1(chave.encode("utf-8")).hexdigest()[:12] if chave else ""


def _quota_cooldown_seconds() -> int:
    state = _load_quota_state()
    # CASTIGO DE CHAVE INVALIDA MORRE QUANDO A CHAVE MUDA. Sem isto, o dono
    # colaria uma chave nova e continuaria uma hora sem visao pela Gemini,
    # sem entender por que — o castigo era do dono anterior da linha.
    ruim = str(state.get("chave_ruim", "") or "")
    if ruim and ruim != _marca_da_chave():
        return 0
    until = float(state.get("cooldown_until", 0) or 0)
    remaining = int(max(0, until - time.time()))
    return remaining


def _set_quota_cooldown_from_error(error_text: str) -> int:
    """Avoid hammering Gemini after quota/rate-limit responses.

    This does not bypass quota. It simply stops repeated failed requests, keeps the
    terminal clean, and makes the assistant answer immediately until the cooldown
    expires. Daily quota errors get a longer pause; per-minute errors use the
    RetryInfo value when present.
    """
    retry_seconds = 60
    try:
        found = re.findall(r'retryDelay[\'\"]?:\s*[\'\"]?(\d+)', error_text)
        if not found:
            found = re.findall(r"Please retry in\s+([0-9.]+)s", error_text, flags=re.I)
        if found:
            retry_seconds = max(15, int(float(found[0])) + 5)
    except Exception:
        pass

    lowered = error_text.lower()
    if "perday" in lowered or "per day" in lowered or "daily" in lowered or "quotaid': 'generaterequestsperday" in lowered:
        retry_seconds = max(retry_seconds, 30 * 60)

    retry_seconds = min(max(retry_seconds, 15), 6 * 60 * 60)
    _save_quota_state({
        "cooldown_until": time.time() + retry_seconds,
        "retry_seconds": retry_seconds,
        "last_error": error_text[:1200],
    })
    return retry_seconds


def _quota_message(seconds: int) -> str:
    if seconds >= 3600:
        when = f"daqui a {max(1, seconds // 3600)} hora(s)"
    elif seconds >= 60:
        when = f"daqui a {max(1, seconds // 60)} minuto(s)"
    else:
        when = f"em uns {max(1, seconds)} segundos"
    return (
        "Chefe, eu capturei a imagem, mas a IA visual bateu o limite da API agora. "
        f"Sem burlar limite: vou segurar novas análises até {when} para não ficar gastando tentativa à toa nem floodando o terminal."
    )


def _get_os() -> str:
    return _load_config().get("os_system", "windows").lower()

_LIVE_MODEL         = "models/gemini-2.5-flash-native-audio-preview-12-2025"
_CHANNELS           = 1
_RECEIVE_SAMPLE_RATE = 24_000
_CHUNK_SIZE         = 1_024

# Qualidade padrão para tela. Mantém o print leve.
_IMG_MAX_W = 560
_IMG_MAX_H = 315
_JPEG_Q    = 52

# Qualidade maior para webcam. O OpenRouter/free estava recebendo imagens
# pequenas demais (6-9 KB), e isso reduzia muito a precisão para objetos na mão.
_CAM_MAX_W = 960
_CAM_MAX_H = 540
_CAM_JPEG_Q = 82
_MIN_LIVE_CAMERA_BYTES_FOR_ANALYSIS = 18_000

_SYSTEM_PROMPT = (
    "You are JARVIS, a smart desktop vision assistant. "
    "You are given an image from either the user's screen or their webcam. "
    "Analyze visible objects, text, UI, components, clothing, accessories, pose, lighting, and context clearly. "
    "For technical questions (circuits, code, hardware) give specific, expert answers. "
    "When people are visible, keep descriptions neutral and helpful. Do not rate beauty, ugliness, body size, attractiveness, or give appearance scores. "
    "If asked whether the user is fat, ugly, beautiful, or for a 0-10 score, answer warmly without judgment and offer practical tips about lighting, framing, posture, outfit, or general wellness if useful. "
    "Be concise — 2-4 sentences — unless the question demands more detail. "
    "Speak directly to the user in their language, with a natural and slightly informal tone in Brazilian Portuguese."
)
try:  # cliente em ingles (24/09/2026): ver core/idioma.py
    from core.idioma import no_idioma as _no_idioma, em_ingles as _em_ingles_sp
    _SYSTEM_PROMPT = _no_idioma(_SYSTEM_PROMPT)
except Exception:
    def _em_ingles_sp():
        return False


def _compress(img_bytes: bytes, source_format: str = "PNG") -> tuple[bytes, str]:
    if not _PIL:
        return img_bytes, f"image/{source_format.lower()}"

    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[Vision] ⚠️  Image compress failed: {e}")
        return img_bytes, f"image/{source_format.lower()}"

def _camera_bytes_to_analysis_jpeg(img_bytes: bytes, mime_type: str = "image/jpeg") -> tuple[bytes, str]:
    """Prepare webcam image with enough detail for object recognition.

    Do not keep the tiny preview JPEG used by the UI if we can safely re-encode it.
    The goal is not to bypass any quota; it is only to avoid sending a blurry
    thumbnail to the fallback vision model.
    """
    if not img_bytes:
        raise RuntimeError("Camera returned an empty frame.")
    if not _PIL:
        return img_bytes, mime_type or "image/jpeg"
    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        # Keep aspect ratio, but allow a larger webcam frame than the terminal/HUD preview.
        img.thumbnail((_CAM_MAX_W, _CAM_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_CAM_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return img_bytes, mime_type or "image/jpeg"


def _frame_to_jpeg(frame) -> tuple[bytes, str]:
    """Convert a cv2 BGR frame to JPEG bytes without depending on a live stream."""
    if frame is None:
        raise RuntimeError("Camera returned an empty frame.")

    if _PIL:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail((_CAM_MAX_W, _CAM_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_CAM_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"

    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _CAM_JPEG_Q])
    if not ok:
        raise RuntimeError("Could not encode camera frame.")
    return buf.tobytes(), "image/jpeg"


@contextmanager
def _quiet_native_stderr():
    """Temporarily silence native OpenCV warnings printed directly to stderr.

    Some camera backends print hundreds of MSMF/DSHOW warnings from C/C++ before
    Python can catch the error. This keeps the JARVIS terminal readable while
    preserving normal Python errors outside the tiny camera-open window.
    """
    try:
        fd = 2
        old = os.dup(fd)
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), fd)
            try:
                yield
            finally:
                os.dup2(old, fd)
                os.close(old)
    except Exception:
        yield


def _latest_frame_from_player(player=None, max_age: float = 4.0, min_bytes: int = 0) -> tuple[bytes, str] | None:
    """Use the frame already shown in VISION CAM when Vision Control owns webcam.

    This avoids opening the same camera twice. Very tiny HUD preview frames are
    ignored when possible because they make OpenRouter identify objects poorly.
    """
    if not player:
        return None
    getter = getattr(player, "get_latest_camera_frame_bytes", None)
    if not callable(getter):
        return None
    try:
        data = getter(max_age=max_age)
        if data and len(data) >= int(min_bytes or 0):
            return _camera_bytes_to_analysis_jpeg(data, "image/jpeg")
    except Exception as e:
        print(f"[Vision] ⚠️  Could not reuse live camera frame: {e}")
    return None


def _capture_screen() -> tuple[bytes, str]:

    # Wayland do KDE: o mss devolve a tela PRETA (ver core/plataforma.py)
    try:
        from core.plataforma import na_sessao_wayland, foto_da_tela_wayland  # noqa: PLC0415
        if na_sessao_wayland():
            img = foto_da_tela_wayland()
            if img is not None:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return _compress(buf.getvalue(), "PNG")
    except Exception as e:
        print(f"[Vision] foto pelo Spectacle falhou: {e}")

    if not _MSS:
        raise RuntimeError("mss is not installed. Run: pip install mss")

    with mss.mss() as sct:
        monitors = sct.monitors          # [0] = all combined, [1..n] = real screens
        target   = monitors[1] if len(monitors) > 1 else monitors[0]
        shot     = sct.grab(target)
        png      = mss.tools.to_png(shot.rgb, shot.size)

    return _compress(png, "PNG")


def _cv2_backend() -> int:
    """Return the best OpenCV camera backend for the current OS."""
    if not _CV2:
        return 0
    os_name = _get_os()
    if os_name == "windows":
        return cv2.CAP_DSHOW    
    if os_name == "mac":
        return cv2.CAP_AVFOUNDATION  
    return cv2.CAP_ANY


def _probe_camera(index: int, backend: int, warmup: int = 5) -> bool:

    if not _CV2:
        return False
    cap = None
    with _quiet_native_stderr():
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            return False
        for _ in range(warmup):
            cap.read()
        ret, frame = cap.read()
        cap.release()
    if not ret or frame is None:
        return False
    return bool(np.mean(frame) > 8)


def _detect_camera_index() -> int:

    backend = _cv2_backend()
    print("[Vision] 🔍 Auto-detecting camera...")
    for idx in range(6):
        if _probe_camera(idx, backend):
            print(f"[Vision] ✅ Camera found at index {idx}")
            _save_config_key("camera_index", idx)
            return idx
        print(f"[Vision] ⚠️  Camera index {idx}: no usable frame")

    print("[Vision] ⚠️  No camera found — defaulting to index 0")
    _save_config_key("camera_index", 0)
    return 0


def _get_camera_index() -> int:
    cfg = _load_config()
    if "camera_index" in cfg:
        return int(cfg["camera_index"])
    return _detect_camera_index()


def _capture_camera(player=None) -> tuple[bytes, str]:
    if not _CV2:
        raise RuntimeError("OpenCV (cv2) is not installed. Run: pip install opencv-python")

    # First choice: reuse the frame already being displayed by Vision Control,
    # but only if it is not a tiny preview. Tiny 6-9 KB frames were causing
    # wrong object guesses like “clipe” or “imagem ruim”.
    live = _latest_frame_from_player(player, min_bytes=_MIN_LIVE_CAMERA_BYTES_FOR_ANALYSIS)
    tiny_live = None if live else _latest_frame_from_player(player, min_bytes=0)
    if live:
        print(f"[Vision] 📷 Reusing live VISION CAM frame ({len(live[0]):,} bytes)")
        return live

    index = _get_camera_index()

    # Keep this list short to avoid long blocking attempts. DSHOW works best on
    # most Windows webcams; CAP_ANY is only a fallback and is silenced.
    backends = []
    primary = _cv2_backend()
    backends.append(primary)
    if primary != cv2.CAP_ANY:
        backends.append(cv2.CAP_ANY)

    last_error = None
    for backend in backends:
        cap = None
        try:
            with _quiet_native_stderr():
                cap = cv2.VideoCapture(index, backend)
                if not cap.isOpened():
                    last_error = RuntimeError(f"Camera index {index} could not be opened with backend {backend}.")
                    continue
                try:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                frame = None
                for _ in range(8):
                    ret, frame = cap.read()
                    if ret and frame is not None and float(np.mean(frame)) > 5:
                        return _frame_to_jpeg(frame)
                last_error = RuntimeError("Camera returned no usable frame.")
        except Exception as e:
            last_error = e
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    if tiny_live:
        print(f"[Vision] 📷 Using small live VISION CAM frame as last fallback ({len(tiny_live[0]):,} bytes)")
        return tiny_live

    raise RuntimeError(str(last_error or f"Camera index {index} could not be opened."))

class _VisionSession:
    def __init__(self):
        self._loop:       Optional[asyncio.AbstractEventLoop] = None
        self._thread:     Optional[threading.Thread]          = None
        self._session                                          = None
        self._out_queue:  Optional[asyncio.Queue]             = None
        self._audio_in:   Optional[asyncio.Queue]             = None
        self._ready_evt:  threading.Event                     = threading.Event()
        self._player                                           = None
        self._lock:       threading.Lock                       = threading.Lock()

    def start(self, player=None, timeout: float = 25.0) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                if player is not None:
                    self._player = player
                return
            self._player = player
            self._thread = threading.Thread(
                target=self._run_event_loop,
                daemon=True,
                name="VisionSessionThread",
            )
            self._thread.start()

        if not self._ready_evt.wait(timeout=timeout):
            raise RuntimeError(f"Vision session did not connect within {timeout}s.")
        print("[Vision] ✅ Session ready")

    def analyze(self, image_bytes: bytes, mime_type: str, user_text: str) -> None:
        if not self._loop or not self._out_queue:
            print("[Vision] ⚠️  Session not started — dropping request")
            return
        asyncio.run_coroutine_threadsafe(
            self._out_queue.put((image_bytes, mime_type, user_text)),
            self._loop,
        )

    def is_ready(self) -> bool:
        return self._session is not None

    def _run_event_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._session_loop())

    async def _session_loop(self) -> None:
        self._out_queue = asyncio.Queue(maxsize=30)
        self._audio_in  = asyncio.Queue()

        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"},
        )
        config = gtypes.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            system_instruction=_SYSTEM_PROMPT,
            speech_config=gtypes.SpeechConfig(
                voice_config=gtypes.VoiceConfig(
                    prebuilt_voice_config=gtypes.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

        backoff = 2.0
        while True:
            try:
                print("[Vision] 🔌 Connecting...")
                async with client.aio.live.connect(
                    model=_LIVE_MODEL, config=config
                ) as session:
                    self._session = session
                    self._ready_evt.set()
                    backoff = 2.0  
                    print("[Vision] ✅ Connected")

                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(self._send_loop())
                        tg.create_task(self._recv_loop())
                        tg.create_task(self._play_loop())

            except* Exception as eg:
                for exc in eg.exceptions:
                    print(f"[Vision] ⚠️  Session error: {exc}")
            finally:
                self._session = None
                self._ready_evt.clear()

            print(f"[Vision] 🔄 Reconnecting in {backoff:.0f}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 30.0)
            self._ready_evt.set()  

    async def _send_loop(self) -> None:
        while True:
            image_bytes, mime_type, user_text = await self._out_queue.get()
            if not self._session:
                print("[Vision] ⚠️  No session — dropping image")
                continue
            try:
                b64 = base64.b64encode(image_bytes).decode("ascii")
                await self._session.send_client_content(
                    turns={
                        "parts": [
                            {"inline_data": {"mime_type": mime_type, "data": b64}},
                            {"text": user_text},
                        ]
                    },
                    turn_complete=True,
                )
                print(f"[Vision] 📤 Sent {len(image_bytes):,} bytes — '{user_text[:60]}'")
            except Exception as e:
                print(f"[Vision] ⚠️  Send error: {e}")
                raise  # propagate to TaskGroup → triggers session reconnect

    async def _recv_loop(self) -> None:
        transcript: list[str] = []
        try:
            async for response in self._session.receive():
                if response.data:
                    await self._audio_in.put(response.data)

                sc = response.server_content
                if not sc:
                    continue

                if sc.output_transcription and sc.output_transcription.text:
                    chunk = sc.output_transcription.text.strip()
                    if chunk:
                        transcript.append(chunk)

                if sc.turn_complete:
                    if transcript and self._player:
                        full = re.sub(r"\s+", " ", " ".join(transcript)).strip()
                        if full:
                            self._player.write_log(f"JARVIS: {full}")
                            print(f"[Vision] 💬 {full}")
                    transcript = []
                    # Auto-close camera ~2s after JARVIS finishes speaking
                    if self._player and hasattr(self._player, "stop_camera_stream"):
                        async def _deferred_close():
                            await asyncio.sleep(2.0)
                            try:
                                self._player.stop_camera_stream()
                            except Exception:
                                pass
                        asyncio.create_task(_deferred_close())

        except Exception as e:
            print(f"[Vision] ⚠️  Recv error: {e}")
            raise  

    async def _play_loop(self) -> None:
        stream = sd.RawOutputStream(
            samplerate=_RECEIVE_SAMPLE_RATE,
            channels=_CHANNELS,
            dtype="int16",
            blocksize=_CHUNK_SIZE,
        )
        stream.start()
        try:
            while True:
                chunk = await self._audio_in.get()
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[Vision] ❌ Play error: {e}")
            raise
        finally:
            stream.stop()
            stream.close()

_session      = _VisionSession()
_session_lock = threading.Lock()
_session_up   = False


def _ensure_session(player=None) -> None:
    global _session_up
    with _session_lock:
        if not _session_up:
            _session.start(player=player)
            _session_up = True
        elif player is not None:
            _session._player = player


def _extract_text_from_generate_result(result) -> str:
    """Return plain text from a google-genai generate_content result without crashing."""
    parts: list[str] = []
    try:
        text = getattr(result, "text", None)
        if text:
            # `result.text` JA E a juncao das partes. Somar as partes de novo
            # fazia toda descricao da tela sair DUAS VEZES (01/10/2026: "Esta
            # aberta uma interface de chat... Esta aberta uma interface de chat...").
            return re.sub(r"\s+", " ", str(text)).strip()
    except Exception:
        pass

    try:
        for candidate in getattr(result, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                ptxt = getattr(part, "text", None)
                if ptxt:
                    parts.append(str(ptxt))
    except Exception:
        pass

    answer = " ".join(p.strip() for p in parts if p and p.strip()).strip()
    return re.sub(r"\s+", " ", answer).strip()


def _vision_prompt(user_text: str, angle: str) -> str:
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        "IMPORTANT RESPONSE RULES:\n"
        + ("- Respond in English unless the user clearly asks for another language.\n"
           if _em_ingles_sp()
           else "- Respond in Brazilian Portuguese unless the user clearly asks for another language.\n") +
        "- Be natural, slightly informal, and useful.\n"
        "- If a person appears, describe visible neutral details and give practical suggestions: lighting, camera angle, framing, posture, clothes, scene, objects, or presentation.\n"
        "- Do not humiliate, insult, rate beauty, give a 0-10 appearance score, or label the person as fat/skinny/ugly.\n"
        "- If the user asks jokingly about being fat, skinny, ugly, or beautiful, keep the joke light and supportive, then give one useful tip.\n"
        "- Do not diagnose health or infer medical conditions from the image.\n"
        "- Keep it short: 1 to 3 sentences.\n\n"
        f"Image source: {angle}\n"
        f"User request: {user_text}"
    )


def _generate_vision_answer(image_bytes: bytes, mime_type: str, user_text: str, angle: str) -> str:
    """Analisa uma foto da tela ou da camera e devolve TEXTO para o JARVIS falar.

    Dois degraus, nesta ordem: a Gemini do cliente (rapida, mas com cota que
    acaba no meio do dia) e o Codex da VPS (mais lento, sem cota). O OpenRouter
    ficava no meio ate 05/09/2026 e foi removido: alem de o Diego ter tirado ele
    do produto, ele devolvia FRASE DE DESCULPA no lugar de `None` quando estava
    de castigo — e essa desculpa saia pela boca do JARVIS como se fosse a analise,
    escondendo o socorro que vinha depois.
    """
    prompt = _vision_prompt(user_text, angle)

    # SEM CHAVE DA GEMINI — OU COM UMA QUE NAO TEM CARA DE CHAVE — NEM TENTA:
    # vai direto ao Codex. A chave e opcional no JARVIS desde 05/09/2026, e o
    # campo do setup aceita qualquer coisa (o JARVIS do Diego rodou com "asdasd..."
    # ate 05/09). Sem esta linha, cada pedido gastava uma ida a Google so para
    # ouvir "API key not valid" antes do socorro.
    if not _chave_da_gemini_serve():
        pelo_codex = _analisar_pelo_codex(image_bytes, user_text, angle)
        if pelo_codex:
            return pelo_codex
        return ("Chefe, para eu enxergar a tela preciso do nosso servidor no ar "
                "ou da chave da Gemini configurada.")

    cooldown = _quota_cooldown_seconds()
    if cooldown > 0:
        _anotar("visao_gemini_de_castigo", f"faltam {cooldown}s; quem olha e o Codex")
        # A COTA DA GEMINI ACABOU: quem enxerga agora e o Codex da VPS, que
        # nao tem cota. Antes daqui saia so um "tente de novo em N minutos".
        pelo_codex = _analisar_pelo_codex(image_bytes, user_text, angle)
        if pelo_codex:
            return pelo_codex
        return _quota_message(cooldown)

    # PRAZO DE 15 s POR MODELO (01/10/2026). Sem prazo, num pico do Google o
    # pedido ficou 51 s preso ate voltar um 503 — o dono perguntou o que tinha
    # na tela e esperou quase um minuto pela desculpa.
    client = genai.Client(api_key=_get_api_key(),
                          http_options={"api_version": "v1beta", "timeout": 15000})

    configured = _load_config().get("vision_model", "").strip()
    # O RAPIDO NA FRENTE (01/10/2026). Medido na mesma foto da tela:
    # gemini-3.5-flash-lite 1,5 a 1,8 s, lendo ate os numeros pequenos;
    # gemini-3.6-flash 5,3 s, e na segunda tentativa 503 (alta demanda). Cada um
    # tem a sua cota, entao o segundo segue como reserva antes do Codex.
    models = [configured] if configured else ["gemini-3.5-flash-lite", "gemini-3.6-flash"]

    tried: set[str] = set()
    last_error: Exception | None = None
    lista = [m for m in models if m and not (m in tried or tried.add(m))]
    for i_model, model in enumerate(lista):
        try:
            cfg = None
            if "lite" in model:
                cfg = gtypes.GenerateContentConfig(
                    thinking_config=gtypes.ThinkingConfig(thinking_level="minimal"))
            result = client.models.generate_content(
                model=model,
                contents=[
                    gtypes.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt,
                ],
                config=cfg,
            )
            answer = _extract_text_from_generate_result(result)
            if answer:
                _anotar("visao_pela_gemini", f"{model} -> {answer[:100]}")
                return answer
            last_error = RuntimeError(f"{model} returned no text")
        except Exception as e:
            last_error = e
            msg = str(e)
            # Cota ou servidor cheio em UM modelo: o proximo tem outra cota e
            # outra fila. So depois do ultimo vale o castigo e o Codex.
            if i_model < len(lista) - 1 and any(x in msg for x in (
                    "429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500", "INTERNAL",
                    "404", "NOT_FOUND", "thinking")):
                _anotar("visao_gemini_proximo", f"{model}: {msg[:90]}")
                continue
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                retry_seconds = _set_quota_cooldown_from_error(msg)
                _anotar("visao_gemini_sem_cota", f"castigo de {retry_seconds}s; vai pelo Codex")
                pelo_codex = _analisar_pelo_codex(image_bytes, user_text, angle)
                if pelo_codex:
                    return pelo_codex
                return _quota_message(retry_seconds)

            # CHAVE INVALIDA nao e cota: nao volta em 60 segundos e nao adianta
            # tentar de novo a cada pedido. Uma hora de castigo evita gastar a
            # espera da rede em toda analise ate o dono trocar a chave.
            if "API_KEY_INVALID" in msg or "API key not valid" in msg:
                _save_quota_state({"cooldown_until": time.time() + 3600,
                                   "retry_seconds": 3600, "last_error": msg[:1200],
                                   "chave_ruim": _marca_da_chave()})
                _anotar("visao_chave_invalida", "a chave da Gemini nao vale; so o Codex ate trocar")
            else:
                _anotar("visao_gemini_falhou", f"{model}: {msg[:200]}")
            pelo_codex = _analisar_pelo_codex(image_bytes, user_text, angle)
            if pelo_codex:
                return pelo_codex
            break

    msg = str(last_error or "unknown error")
    if "API_KEY_INVALID" in msg or "API key not valid" in msg:
        return ("Chefe, capturei a imagem, mas a chave da Gemini configurada não é "
                "mais válida e o nosso servidor não respondeu agora. Trocando a chave "
                "ou com o servidor no ar, eu volto a enxergar.")
    return f"Chefe, capturei a imagem, mas não consegui analisar agora: {msg[:200]}"


def screen_process(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Capture screen/camera and return a text answer.

    Important: this is synchronous on purpose. The main Live audio session receives
    only this text as the tool result, never raw image bytes, avoiding the 1007
    CONTENT_TYPE_AUDIO crash seen when image frames are injected into the audio session.
    """

    params    = parameters or {}
    user_text = (params.get("text") or params.get("user_text") or "").strip()
    angle     = (params.get("angle") or "screen").lower().strip()
    # "WEBCAM" E "CAMERA" SAO A MESMA COISA (05/09/2026).
    #
    # O codigo so reconhecia `camera`; a declaracao da ferramenta diz
    # "'camera' for webcam" — e o modelo, lendo a palavra webcam ali, mandou
    # `angle="webcam"`. Nao casava com nada, caia no ramo da TELA, e o JARVIS
    # analisou a propria janela dele: o Diego pediu "olha minha webcam" e ouviu
    # de volta uma descricao da interface do proprio programa.
    #
    # Sinonimo do dono nao pode virar defeito silencioso. Tudo que soa como
    # camera vira `camera`.
    if angle in ("webcam", "web cam", "cam", "camara", "câmara", "camera do pc",
                 "front", "frontal", "face", "rosto", "eu"):
        angle = "camera"
    # REDE DE SEGURANCA: O PEDIDO MANDA MAIS QUE O CAMPO (05/09/2026).
    #
    # O modelo errou este campo duas vezes no mesmo dia — mandou
    # `angle="screen"` com o dono dizendo "analisa minha WEBCAM e ve o objeto
    # que estou segurando". O JARVIS analisou a tela e respondeu que so via o
    # computador, com a cara dele na frente da camera.
    #
    # Quando a PERGUNTA fala claramente em camera e o campo diz tela, quem
    # ganha e a pergunta: o dono sabe para onde quer que ele olhe.
    if angle != "camera":
        import unicodedata as _u  # noqa: PLC0415
        _p = _u.normalize("NFD", str(user_text or "").lower())
        _p = "".join(c for c in _p if _u.category(c) != "Mn")
        if re.search(r"\b(web ?cam|minha camera|camera do (pc|computador|note|notebook)|"
                     r"camera frontal|na camera)\b", _p):
            angle = "camera"

    if not user_text:
        print("[Vision] ⚠️  No question provided — aborting")
        return "Chefe, me diz o que você quer que eu analise na tela ou na webcam."

    print(f"[Vision] ▶ sync angle={angle!r}  question='{user_text[:80]}'")

    try:
        if angle == "camera":
            # Do not start a second webcam stream here. If Vision Control is active,
            # reuse its latest frame. If it is not active, take one quiet snapshot.
            image_bytes, mime_type = _capture_camera(player=player)
            print(f"[Vision] 📷 Camera: {len(image_bytes):,} bytes")
            if player and hasattr(player, "show_camera_frame"):
                try:
                    player.show_camera_frame(image_bytes)
                except Exception:
                    pass
        else:
            image_bytes, mime_type = _capture_screen()
            print(f"[Vision] 🖥️  Screen: {len(image_bytes):,} bytes")
    except Exception as e:
        print(f"[Vision] ❌ Capture error: {e}")
        return f"Chefe, não consegui capturar {'a webcam' if angle == 'camera' else 'a tela'} agora: {e}"

    answer = _generate_vision_answer(image_bytes, mime_type, user_text, angle)

    # Keep camera preview from staying open forever after a one-shot analysis.
    if angle == "camera" and player and hasattr(player, "stop_camera_stream"):
        def _close_later():
            try:
                time.sleep(2.5)
                player.stop_camera_stream()
            except Exception:
                pass
        threading.Thread(target=_close_later, daemon=True, name="VisionCameraAutoClose").start()

    return answer

def warmup_session(player=None) -> None:
    """No-op by design.

    The JARVIS main audio session is the only live/audio session allowed.
    Webcam/screen analysis in this file is synchronous and returns plain text,
    so adding OpenRouter fallback cannot interfere with microphone listening or
    JARVIS voice playback.
    """
    return None

if __name__ == "__main__":
    print("[TEST] screen_processor.py")
    print("=" * 52)
    mode = input("angle — screen / camera (default: screen): ").strip().lower() or "screen"
    q    = input("Question (Enter = default): ").strip() or "What do you see? Be brief."

    t0 = time.perf_counter()
    warmup_session()
    print(f"Session ready in {time.perf_counter()-t0:.2f}s\n")

    t1 = time.perf_counter()
    ok = screen_process({"angle": mode, "text": q})
    print(f"Queued in {time.perf_counter()-t1:.3f}s — waiting for audio...")
    time.sleep(10)
    print("Done." if ok else "Failed.")
