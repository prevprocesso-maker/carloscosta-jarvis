"""
dashboard/server.py — JARVIS Local HTTP Dashboard

Plain HTTP on port 8000 (no SSL warnings, no firewall issues).
Security at the application layer: AES-256-CBC with session-key-derived key.
CryptoJS is auto-downloaded once and served locally — no CDN needed after that.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import secrets
import socket
import string
import time
import unicodedata
from pathlib import Path

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response, StreamingResponse
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    _UPLOAD_OK = True
except Exception:
    pass

BASE_DIR    = Path(__file__).resolve().parent.parent
STATIC_DIR  = Path(__file__).parent / "static"
PORT        = 8000
MAX_UPLOAD_MB = 500
REMOTE_SESSIONS_FILE = BASE_DIR / "config" / "remote_devices.json"


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "JARVIS Uploads",
        Path.home() / "Documents" / "JARVIS Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            pass
    return BASE_DIR / "uploads"


UPLOADS_DIR = _make_uploads_dir()

def _get_gemini_key() -> str | None:
    try:
        import json as _json
        with open(BASE_DIR / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            return _json.load(f).get("gemini_api_key")
    except Exception:
        return None

_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
_AES_SALT = b'JARVIS-DASHBOARD-v1'


def _derive_key(session_key: str) -> bytes:
    """SHA-256(sessionKey‖salt) → 32-byte AES-256 key (microseconds, no PBKDF2 needed)."""
    return hashlib.sha256(session_key.encode('utf-8') + _AES_SALT).digest()


def _decrypt_cbc(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(IV[16] ‖ ciphertext) with AES-256-CBC + PKCS7."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    raw      = base64.b64decode(enc_b64)
    iv, ct   = raw[:16], raw[16:]
    dec      = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded   = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')


# ── CryptoJS (auto-download once, served locally) ─────────────────────────────
_CRYPTOJS_CDN  = ("https://cdnjs.cloudflare.com/ajax/libs/"
                  "crypto-js/4.2.0/crypto-js.min.js")
_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Cross-platform, best-effort: open port in the OS firewall for LAN access.

    Runs in a background thread — never blocks uvicorn startup.

    Windows : writes a .bat file, runs it elevated via Windows ShellExecuteW
              (native UAC dialog, guaranteed to appear). One-time setup.
    macOS   : osascript admin dialog if the Application Firewall is on.
    Linux   : pkexec GUI → sudo -n → prints manual command as fallback.
    """
    import sys, subprocess, os, tempfile, threading

    # ── Windows ──────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        import ctypes, time

        port_rule = f"JARVIS Dashboard Port {port}"
        prog_rule  = "JARVIS Dashboard Python"
        py_exe     = sys.executable

        def _netsh_rule_exists(name: str) -> bool:
            try:
                r = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                    capture_output=True, text=True, timeout=5,
                )
                return r.returncode == 0 and "No rules match" not in r.stdout
            except Exception:
                return False

        def _network_is_public() -> bool:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-NetConnectionProfile | "
                     "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                     "Measure-Object).Count"],
                    capture_output=True, text=True, timeout=6,
                )
                return r.stdout.strip() not in ("", "0")
            except Exception:
                return False

        need_port    = not _netsh_rule_exists(port_rule)
        need_prog    = not _netsh_rule_exists(prog_rule)
        need_private = _network_is_public()

        if not need_port and not need_prog and not need_private:
            return  # already fully configured

        # Build a .bat file — netsh + powershell, runs fast when elevated
        bat_lines = ["@echo off"]
        if need_private:
            bat_lines.append(
                'powershell -NoProfile -NonInteractive -Command "'
                'Get-NetConnectionProfile | '
                "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                'Set-NetConnectionProfile -NetworkCategory Private"'
            )
        if need_port:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{port_rule}" protocol=TCP dir=in '
                f'localport={port} action=allow'
            )
        if need_prog:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{prog_rule}" dir=in action=allow '
                f'program="{py_exe}" enable=yes'
            )

        bat_body = "\r\n".join(bat_lines) + "\r\n"
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="jarvis_fw_")
        try:
            os.write(fd, bat_body.encode("mbcs"))   # Windows cmd.exe expects ANSI
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return

        # ── Try running directly (succeeds when already admin) ────────────────
        try:
            r = subprocess.run(
                [bat_path], capture_output=True, timeout=8, shell=True
            )
            if r.returncode == 0:
                print(f"[Dashboard] Firewall configured for port {port}.")
                try:
                    os.unlink(bat_path)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # ── ShellExecuteW: native UAC elevation (most reliable on Windows) ────
        # ShellExecuteW with verb "runas" always shows the UAC dialog regardless
        # of UAC level settings. Non-blocking — uvicorn is already running.
        print("[Dashboard] One-time network setup required.")
        print("[Dashboard] >>> A Windows security dialog will appear — click 'Yes' <<<")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,       # hwnd  (no parent window)
                "runas",    # verb  (request elevation)
                bat_path,   # file  (our .bat)
                None,       # params
                None,       # working dir
                0,          # SW_HIDE (run without a visible cmd window)
            )
            if int(ret) > 32:
                # ShellExecuteW returns immediately; bat finishes in ~1 second.
                # Sleep briefly so the rules are in place before the first retry.
                time.sleep(2)
                print(f"[Dashboard] Network setup complete — port {port} is open.")
                print("[Dashboard] Refresh your phone browser to connect.")
            else:
                print("[Dashboard] Setup was not allowed.")
                print("[Dashboard] Phone connections may fail until JARVIS is run as Administrator.")
        except Exception as e:
            print(f"[Dashboard] Firewall setup error: {e}")
        finally:
            # Cleanup after the bat has had time to run
            def _cleanup(path: str) -> None:
                time.sleep(5)
                try:
                    os.unlink(path)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()
        return

    # ── macOS ─────────────────────────────────────────────────────────────────
    if sys.platform == "darwin":
        fw_ctl = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        try:
            r = subprocess.run(
                [fw_ctl, "--getglobalstate"], capture_output=True, text=True, timeout=5,
            )
            if "disabled" in r.stdout.lower():
                return  # firewall off — nothing to do

            py = sys.executable
            listed = subprocess.run(
                [fw_ctl, "--listapps"], capture_output=True, text=True, timeout=5,
            )
            if py in listed.stdout:
                return  # already allowed

            print("[Dashboard] One-time network setup — enter your password in the macOS dialog.")
            subprocess.run(
                ["osascript", "-e",
                 f'do shell script "{fw_ctl} --add {py} && {fw_ctl} --unblockapp {py}"'
                 f' with administrator privileges'],
                timeout=60,
            )
        except Exception:
            pass  # macOS firewall is off by default — silent failure is fine
        return

    # ── Linux ─────────────────────────────────────────────────────────────────
    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = subprocess.run(prefix + cmd, capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    try:  # ufw
        r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            if _privileged(["ufw", "allow", f"{port}/tcp"]):
                print(f"[Dashboard] ufw: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo ufw allow {port}/tcp")
            return
    except FileNotFoundError:
        pass

    try:  # firewalld
        r = subprocess.run(
            ["firewall-cmd", "--state"], capture_output=True, text=True, timeout=5,
        )
        if "running" in r.stdout.lower():
            ok = (_privileged(["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"])
                  and _privileged(["firewall-cmd", "--reload"]))
            if ok:
                print(f"[Dashboard] firewalld: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload")
            return
    except FileNotFoundError:
        pass

    try:  # iptables (not persistent but works until reboot)
        r = subprocess.run(["iptables", "-L", "INPUT", "-n"], capture_output=True, timeout=5)
        if r.returncode == 0:
            if _privileged(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]):
                print(f"[Dashboard] iptables: port {port} opened.")
            else:
                print(f"[Dashboard] Run manually:  sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT")
    except FileNotFoundError:
        pass  # no iptables means firewall is probably off — nothing to do


def _ensure_crypto_js() -> None:
    if _CRYPTOJS_FILE.exists():
        return
    try:
        import urllib.request
        print("[Dashboard] Downloading CryptoJS (one-time setup)…")
        urllib.request.urlretrieve(_CRYPTOJS_CDN, str(_CRYPTOJS_FILE))
        print("[Dashboard] CryptoJS cached — will serve locally from now on.")
    except Exception as e:
        print(f"[Dashboard] CryptoJS download failed: {e}")
        print(f"[Dashboard] Encryption will fall back to CDN load on client.")


_ensure_crypto_js()


# ── helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    # Method 1: route trick (fast, works when internet is available)
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass

    # Method 2: hostname resolution (works offline on most systems)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Method 3: enumerate all interfaces (fully offline, no external deps)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ── DashboardServer ───────────────────────────────────────────────────────────

class DashboardServer:

    def __init__(self):
        self._ip                          = _local_ip()
        self._tokens: set[str]            = set()
        self._token_keys: dict[str, str]  = {}   # auth_token → session_key
        self._aes_cache:  dict[str, bytes]= {}   # session_key → AES bytes
        self._clients: set[WebSocket]     = set()
        self._history: list[dict]         = []
        self._command_queue               = asyncio.Queue()
        self._wake_callback               = None
        self._connect_callback            = None
        self._pending_keys: dict[str, float] = {}
        self._device_sessions: dict[str, dict] = {}  # sha256(device_token) → session metadata
        self._phone_audio_queue: asyncio.Queue    = asyncio.Queue(maxsize=200)
        self._screen_image: bytes | None         = None
        self._screen_captured_at: float          = 0.0
        self._webcam_active                      = False
        self._webcam_generation                  = 0
        self._screen_live_active                 = False
        self._screen_live_generation             = 0
        self._uploads_dir                 = UPLOADS_DIR
        self._login_html                  = _read("login.html")
        self._app_html                    = _read("app.html")
        self._load_device_sessions()
        try:
            from dashboard.relay_client import RelayClient
            self._relay = RelayClient(self, BASE_DIR)
        except Exception as exc:
            print(f"[JARVIS Relay] disabled: {exc}")
            self._relay = None
        self.app                          = self._build_app()

    @staticmethod
    def _device_token_id(device_token: str) -> str:
        return hashlib.sha256(device_token.encode("utf-8")).hexdigest()

    def _load_device_sessions(self) -> None:
        """Restore paired phones after JARVIS restarts without storing raw device tokens."""
        try:
            payload = json.loads(REMOTE_SESSIONS_FILE.read_text(encoding="utf-8"))
            devices = payload.get("devices", {}) if isinstance(payload, dict) else {}
            self._device_sessions = {
                str(token_id): {
                    "session_key": str(data["session_key"]),
                    "paired_at": float(data.get("paired_at", 0)),
                }
                for token_id, data in devices.items()
                if isinstance(data, dict) and data.get("session_key")
            }
        except Exception:
            self._device_sessions = {}

    def _save_device_sessions(self) -> None:
        try:
            REMOTE_SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            temp = REMOTE_SESSIONS_FILE.with_suffix(".tmp")
            temp.write_text(
                json.dumps({"version": 1, "devices": self._device_sessions}, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, REMOTE_SESSIONS_FILE)
        except Exception as exc:
            print(f"[Dashboard] Could not save paired devices: {exc}")

    # ── one-time key management ───────────────────────────────────────────

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        if self._relay and not self._relay.wait_connected(timeout=15):
            detail = getattr(self._relay, "last_error", "") or "sem resposta do servidor remoto"
            raise RuntimeError(f"A ponte remota ainda nao conectou ao servidor ({detail}).")
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        if self._relay:
            if not self._relay.set_pairing(key, now + expiry_secs):
                self._pending_keys.pop(key, None)
                raise RuntimeError("Nao consegui publicar a chave remota no servidor. Tente novamente.")
        return key

    @staticmethod
    def _ssl_enabled() -> bool:
        certs = BASE_DIR / "config" / "certs"
        return (certs / "jarvis.key").exists() and (certs / "jarvis.crt").exists()

    def get_url(self) -> str:
        if self._relay:
            return self._relay.public_url
        proto = "https" if self._ssl_enabled() else "http"
        return f"{proto}://{self._ip}:{PORT}"

    def get_pairing_url(self, key: str) -> str:
        if self._relay:
            return self._relay.pairing_url(key)
        return f"{self.get_url()}/auto-login?key={key}"

    def get_manual_url(self) -> str:
        """URL for manual browser entry. When HTTPS active, points to alias port (also HTTPS)."""
        if self._relay:
            return self._relay.public_url
        if self._ssl_enabled():
            return f"{self._ip}:{PORT + 1}"
        return f"{self._ip}:{PORT}"

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk:
            return None
        try:
            return _decrypt_cbc(self._aes_key(sk), enc_b64)
        except Exception:
            return None

    # ── callbacks ────────────────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    @staticmethod
    def _plain_command(text: str) -> str:
        value = unicodedata.normalize("NFD", str(text or ""))
        value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    def remote_media_intent(self, text: str) -> str:
        """Recognize only explicit Remote Control screen/webcam requests."""
        cmd = self._plain_command(text)
        live_screen = any(term in cmd for term in ("tela em tempo real", "tela ao vivo", "ver tela ao vivo", "transmitir tela", "transmissao da tela"))
        stop = any(term in cmd for term in ("fechar", "fecha", "parar", "pare", "desligar", "desliga", "encerrar", "encerra"))
        if live_screen and stop:
            return "screen_live_stop"
        if live_screen:
            return "screen_live_start"
        camera = any(term in cmd for term in ("webcam", "camera", "cam ao vivo", "camera ao vivo"))
        if camera and stop:
            return "webcam_stop"
        live = any(term in cmd for term in ("tempo real", "ao vivo", "abrir", "abre", "mostrar", "mostra", "ver webcam", "ver camera", "ligar", "liga"))
        if camera and live:
            return "webcam_start"
        screen = any(term in cmd for term in ("print da tela", "print de tela", "screenshot", "captura da tela", "capturar a tela", "foto da tela", "mostra minha tela", "mostrar minha tela", "tela do pc", "tela do computador"))
        send = any(term in cmd for term in ("envia", "enviar", "manda", "mandar", "mostra", "mostrar", "captura", "capturar", "print", "screenshot"))
        if screen and send:
            return "screenshot"
        return ""

    @staticmethod
    def _capture_screen_jpeg() -> bytes:
        import mss
        import numpy as np
        from PIL import Image
        try:  # Wayland do KDE: o mss devolve a tela preta (core/plataforma.py)
            from core.plataforma import na_sessao_wayland, foto_da_tela_wayland
            if na_sessao_wayland():
                image = foto_da_tela_wayland(tudo=True)
                if image is not None:
                    image.thumbnail((1600, 1000))
                    out = io.BytesIO()
                    image.save(out, format="JPEG", quality=80, optimize=True)
                    return out.getvalue()
        except Exception:
            pass
        with mss.mss() as sct:
            monitors = sct.monitors
            target = monitors[0] if monitors else None
            if not target:
                raise RuntimeError("Nenhuma tela foi encontrada.")
            shot = sct.grab(target)
            image = Image.fromarray(np.asarray(shot)[:, :, :3][:, :, ::-1])
        image.thumbnail((1600, 1000))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=80, optimize=True)
        return out.getvalue()

    async def remote_media_action(self, action: str, announce: bool = True) -> str:
        action = str(action or "").lower().strip().replace("-", "_")
        if action in {"screenshot", "screen", "screen_capture", "print"}:
            try:
                self._screen_image = await asyncio.to_thread(self._capture_screen_jpeg)
                self._screen_captured_at = time.time()
            except Exception as exc:
                message = f"Não consegui capturar a tela: {exc}"
                if announce:
                    await self.broadcast({"type": "log", "speaker": "jarvis", "text": message})
                return message
            version = int(self._screen_captured_at * 1000)
            await self.broadcast({"type": "screen_capture", "version": version})
            message = "Prontinho, chefe. Enviei a captura atual da tela para o Controle Remoto."
            if announce:
                await self.broadcast({"type": "log", "speaker": "jarvis", "text": message})
            return message
        if action in {"webcam_start", "camera_start", "webcam", "camera", "start_webcam"}:
            self._webcam_active = True
            self._webcam_generation += 1
            await self.broadcast({"type": "webcam_started", "generation": self._webcam_generation})
            message = "Webcam ao vivo aberta no Controle Remoto. Ela ficará ativa até você pedir para fechar."
            if announce:
                await self.broadcast({"type": "log", "speaker": "jarvis", "text": message})
            return message
        if action in {"webcam_stop", "camera_stop", "stop_webcam", "close_webcam", "webcam_close"}:
            self._webcam_active = False
            self._webcam_generation += 1
            await self.broadcast({"type": "webcam_stopped", "generation": self._webcam_generation})
            message = "Webcam remota fechada."
            if announce:
                await self.broadcast({"type": "log", "speaker": "jarvis", "text": message})
            return message
        if action in {"screen_live_start", "live_screen_start", "screen_stream_start"}:
            self._screen_live_active = True
            self._screen_live_generation += 1
            await self.broadcast({"type": "screen_live_started", "generation": self._screen_live_generation})
            message = "Tela do computador aberta ao vivo no Controle Remoto."
            if announce:
                await self.broadcast({"type": "log", "speaker": "jarvis", "text": message})
            return message
        if action in {"screen_live_stop", "live_screen_stop", "screen_stream_stop"}:
            self._screen_live_active = False
            self._screen_live_generation += 1
            await self.broadcast({"type": "screen_live_stopped", "generation": self._screen_live_generation})
            message = "Transmissão ao vivo da tela fechada."
            if announce:
                await self.broadcast({"type": "log", "speaker": "jarvis", "text": message})
            return message
        return "Ação remota inválida."

    async def handle_remote_media_command(self, text: str) -> bool:
        action = self.remote_media_intent(text)
        if not action:
            return False
        await self.remote_media_action(action)
        return True

    def remote_system_intent(self, text: str) -> str:
        cmd = self._plain_command(text)
        computer = any(term in cmd for term in ("computador", "pc", "windows", "maquina"))
        if not computer:
            return ""
        if any(term in cmd for term in ("cancelar desligamento", "cancela desligamento", "nao desligue")):
            return "cancel_shutdown"
        if any(term in cmd for term in ("bloquear", "bloqueia", "travar", "trava")):
            return "lock"
        if any(term in cmd for term in ("desligar", "desliga", "apagar o computador")):
            return "shutdown"
        return ""

    async def handle_remote_system_command(self, text: str) -> bool:
        action = self.remote_system_intent(text)
        if not action:
            return False
        if action == "cancel_shutdown":
            result = await self.remote_system_action(action)
            await self.broadcast({"type": "log", "speaker": "jarvis", "text": result})
        else:
            await self.broadcast({"type": "confirmation_required", "action": action})
        return True

    def _webcam_frames(self, token: str, generation: int):
        cap = None
        try:
            import cv2
            from actions.screen_processor import _get_camera_index, _cv2_backend
            index = _get_camera_index()
            cap = cv2.VideoCapture(index, _cv2_backend())
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(index)
            if not cap.isOpened():
                return
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            while self._webcam_active and generation == self._webcam_generation and token in self._tokens:
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.12)
                    continue
                height, width = frame.shape[:2]
                if width > 960:
                    scale = 960.0 / width
                    frame = cv2.resize(frame, (960, max(1, int(height * scale))))
                ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
                if ok:
                    payload = encoded.tobytes()
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\n\r\n" + payload + b"\r\n")
                time.sleep(0.12)
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    def _screen_live_frames(self, token: str, generation: int):
        """Low-bandwidth MJPEG desktop stream for phones (about 4 FPS)."""
        try:
            import mss
            import numpy as np
            import cv2
            with mss.mss() as sct:
                target = sct.monitors[0] if sct.monitors else None
                if not target:
                    return
                while self._screen_live_active and generation == self._screen_live_generation and token in self._tokens:
                    shot = sct.grab(target)
                    frame = np.asarray(shot)[:, :, :3]
                    height, width = frame.shape[:2]
                    if width > 1280:
                        scale = 1280.0 / width
                        frame = cv2.resize(frame, (1280, max(1, int(height * scale))))
                    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 62])
                    if ok:
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\n\r\n" + encoded.tobytes() + b"\r\n")
                    time.sleep(0.24)
        except Exception as exc:
            print(f"[Dashboard] Live screen stopped: {exc}")

    async def remote_system_action(self, action: str) -> str:
        """Execute an explicitly confirmed Windows power/security action."""
        import subprocess
        action = str(action or "").strip().lower()
        if action == "lock":
            if os.name != "nt":
                return "Bloqueio remoto disponível apenas no Windows."
            import ctypes
            ok = await asyncio.to_thread(ctypes.windll.user32.LockWorkStation)
            return "Computador bloqueado." if ok else "Não foi possível bloquear o computador."
        if action == "shutdown":
            if os.name != "nt":
                return "Desligamento remoto disponível apenas no Windows."
            await asyncio.to_thread(subprocess.run, ["shutdown.exe", "/s", "/t", "60", "/c", "Desligamento solicitado pelo Controle Remoto do JARVIS"], capture_output=True, timeout=10)
            return "Desligamento agendado para 60 segundos. Use CANCELAR se mudou de ideia."
        if action == "cancel_shutdown":
            if os.name != "nt":
                return "Cancelamento disponível apenas no Windows."
            await asyncio.to_thread(subprocess.run, ["shutdown.exe", "/a"], capture_output=True, timeout=10)
            return "Desligamento cancelado."
        return "Ação de sistema inválida."

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead
        if self._relay:
            await self._relay.publish_event(msg)

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        def _auth(req: Request) -> bool:
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return bool(tok) and tok in self._tokens

        # serve CryptoJS from local cache, fallback to CDN redirect
        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(str(_CRYPTOJS_FILE),
                                    media_type="application/javascript")
            from fastapi.responses import RedirectResponse
            return RedirectResponse(_CRYPTOJS_CDN)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            # Auth is handled client-side via sessionStorage bearer token.
            # Server-side header auth can't work here because browser navigations
            # don't send custom headers (location.href doesn't carry Authorization).
            html = (self._app_html
                    .replace("__IP__", self._ip)
                    .replace("__PORT__", str(PORT)))
            return HTMLResponse(html)

        @app.post("/login")
        async def login(req: Request):
            body    = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            now     = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                del self._pending_keys[entered]          # one-time use
                tok = secrets.token_urlsafe(32)
                dev_tok = secrets.token_urlsafe(32)
                self._tokens.add(tok)
                self._token_keys[tok] = entered
                self._aes_key(entered)                   # pre-derive & cache
                self._device_sessions[self._device_token_id(dev_tok)] = {
                    "session_key": entered,
                    "paired_at": time.time(),
                }
                self._save_device_sessions()
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Remote connection established."}
                ))
                # Bearer token in response body — no cookies needed (works on any browser/HTTP)
                return JSONResponse({"ok": True, "token": tok, "device_token": dev_tok, "key": entered})
            return JSONResponse({"ok": False, "error": "Invalid or expired key"},
                                status_code=401)

        @app.get("/auto-login")
        async def auto_login(key: str = ""):
            """QR code target — validates one-time key, creates session, redirects phone."""
            now = time.time()
            if not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h2{color:#f87171;margin-bottom:12px}p{color:#5e6a7e;font-size:14px}
</style></head>
<body><div><h2>Link Expired</h2>
<p>Press <strong style="color:#dde3ed">Remote Control</strong> in JARVIS to get a new QR code.</p>
</div></body></html>""")

            del self._pending_keys[key]
            tok     = secrets.token_urlsafe(32)
            dev_tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = key
            self._aes_key(key)
            self._device_sessions[self._device_token_id(dev_tok)] = {
                "session_key": key,
                "paired_at": time.time(),
            }
            self._save_device_sessions()

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Remote connection established via QR code."}
            ))

            return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}}
  p{{color:#5e6a7e;font-size:14px}}
</style></head>
<body>
<script>
  sessionStorage.setItem('jarvis_token','{tok}');
  sessionStorage.setItem('jarvis_key','{key}');
  localStorage.setItem('jarvis_device_token','{dev_tok}');
  setTimeout(function(){{location.replace('/')}},400);
</script>
<p>Connecting to JARVIS…</p>
</body></html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            """Return a fresh auth token for a previously paired device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            dev_tok = (body.get("device_token") or "").strip()
            token_id = self._device_token_id(dev_tok) if dev_tok else ""
            if not token_id or token_id not in self._device_sessions:
                return JSONResponse({"ok": False}, status_code=401)
            session_key = self._device_sessions[token_id]["session_key"]
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = session_key
            self._aes_key(session_key)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Known device reconnected automatically."}
            ))
            return JSONResponse({"ok": True, "token": tok, "key": session_key})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            """Invalidate all persistent device tokens (admin action)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            count = len(self._device_sessions)
            self._device_sessions.clear()
            self._save_device_sessions()
            self._tokens.clear()
            self._token_keys.clear()
            return JSONResponse({"ok": True, "revoked": count})

        @app.get("/api/remote-media/status")
        async def remote_media_status(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return JSONResponse({
                "ok": True,
                "webcam_active": bool(self._webcam_active),
                "webcam_generation": self._webcam_generation,
                "screen_available": self._screen_image is not None,
                "screen_version": int(self._screen_captured_at * 1000),
                "screen_live_active": bool(self._screen_live_active),
                "screen_live_generation": self._screen_live_generation,
            })

        @app.get("/api/remote-media/screen")
        async def remote_screen(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if not self._screen_image:
                return JSONResponse({"error": "No screen capture available"}, status_code=404)
            return Response(
                content=self._screen_image,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store, max-age=0"},
            )

        @app.get("/api/remote-media/webcam")
        async def remote_webcam_stream(token: str = "", generation: int = 0):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if not self._webcam_active:
                return JSONResponse({"error": "Webcam is not active"}, status_code=409)
            current_generation = self._webcam_generation
            if generation and generation != current_generation:
                return JSONResponse({"error": "Webcam stream expired"}, status_code=409)
            return StreamingResponse(
                self._webcam_frames(tok, current_generation),
                media_type="multipart/x-mixed-replace; boundary=frame",
                headers={"Cache-Control": "no-store, max-age=0"},
            )

        @app.get("/api/remote-media/screen-live")
        async def remote_screen_live(token: str = "", generation: int = 0):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if not self._screen_live_active:
                return JSONResponse({"error": "Live screen is not active"}, status_code=409)
            current_generation = self._screen_live_generation
            if generation and generation != current_generation:
                return JSONResponse({"error": "Live screen expired"}, status_code=409)
            return StreamingResponse(
                self._screen_live_frames(tok, current_generation),
                media_type="multipart/x-mixed-replace; boundary=frame",
                headers={"Cache-Control": "no-store, max-age=0"},
            )

        @app.post("/api/remote-media")
        async def remote_media(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            action = str(body.get("action") or "").strip()
            result = await self.remote_media_action(action)
            return JSONResponse({"ok": not result.startswith("Ação remota inválida"), "result": result})

        @app.post("/api/remote-action")
        async def remote_action(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            action = str(body.get("action") or "").strip().lower()
            if action not in {"lock", "shutdown", "cancel_shutdown"}:
                return JSONResponse({"error": "Invalid action"}, status_code=400)
            if action in {"lock", "shutdown"} and body.get("confirmed") is not True:
                return JSONResponse({"ok": False, "confirmation_required": True, "action": action}, status_code=409)
            result = await self.remote_system_action(action)
            await self.broadcast({"type": "log", "speaker": "jarvis", "text": result})
            return JSONResponse({"ok": True, "result": result})

        @app.post("/api/command")
        async def command(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body  = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc   = body.get("enc", "")
            if enc:
                text = self._decrypt(token, enc)
                if text is None:
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                text = (body.get("text") or "").strip()
            if text and await self.handle_remote_media_command(text):
                return JSONResponse({"ok": True, "handled": "remote_media"})
            if text and await self.handle_remote_system_command(text):
                return JSONResponse({"ok": True, "handled": "remote_system"})
            if text:
                await self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        # ── Phone mic real-time audio → Gemini Live ──────────────────────────

        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Phone microphone live."}
            ))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_queue.put_nowait(
                            {"data": data, "mime_type": "audio/pcm"}
                        )
                    except asyncio.QueueFull:
                        pass  # drop frame rather than block
            except WebSocketDisconnect:
                pass
            finally:
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Phone microphone stopped."}
                ))

        # ── File sharing ──────────────────────────────────────────────────────

        def _safe_filename(raw: str) -> str:
            name = Path(raw).name                          # strip path components
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(". ")
            return name or "upload"

        if _UPLOAD_OK:
            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not _auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = _safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {"error": f"File too large (max {MAX_UPLOAD_MB} MB)"},
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(self.broadcast({
                    "type": "file_received",
                    "name": dest.name,
                    "size": size,
                    "saved_to": str(self._uploads_dir),
                }))
                return JSONResponse({"ok": True, "name": dest.name, "size": size})
        else:
            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception:
                pass
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, token: str = ""):
            # Auth via query param — browser <a download> can't send custom headers
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r'[/\\]', '', filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe)

        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "command":
                        enc = data.get("enc", "")
                        t   = self._decrypt(tok, enc) if enc else (data.get("text") or "").strip()
                        if t and await self.handle_remote_media_command(t):
                            continue
                        if t and await self.handle_remote_system_command(t):
                            continue
                        if t:
                            await self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)

        return app

    # ── serve ─────────────────────────────────────────────────────────────

    async def _serve_alias(self) -> None:
        """Second HTTPS server on PORT+1 sharing the same app and in-memory state.
        Chrome HTTPS-upgrades any bare IP:PORT the user types, so this port also needs TLS.
        User types IP:8001 → Chrome tries https → self-signed cert warning → accept once → done."""
        ssl_key  = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT + 1)
        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT + 1, log_level="warning",
            ssl_keyfile=str(ssl_key), ssl_certfile=str(ssl_cert),
        )
        print(f"[Dashboard] Manual entry:  {self._ip}:{PORT + 1}  (type in browser, accept cert once)")
        await uvicorn.Server(cfg).serve()

    async def serve(self) -> None:
        if not _DEPS_OK:
            print("[Dashboard] fastapi/uvicorn not installed — dashboard disabled.")
            print("[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography")
            return

        if self._relay:
            self._relay.start(asyncio.get_running_loop())

        # Firewall setup runs in a thread — uvicorn starts immediately,
        # no waiting for UAC dialogs or subprocess timeouts.
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT)

        use_ssl  = self._ssl_enabled()
        ssl_key  = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"

        if use_ssl:
            asyncio.create_task(self._serve_alias())

        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT, log_level="warning",
            **({"ssl_keyfile": str(ssl_key), "ssl_certfile": str(ssl_cert)} if use_ssl else {}),
        )

        proto = "https" if use_ssl else "http"
        print(f"[Dashboard] {proto}://{self._ip}:{PORT}")
        print("[Dashboard] Press 'Remote Control' in JARVIS UI to get the QR code.")
        # O PAINEL NAO PODE DERRUBAR O ASSISTENTE (01/09/2026).
        #
        # Porta 8000 ocupada -> o uvicorn faz `sys.exit(3)`. Isso e SystemExit,
        # o vigia do motor entende como queda e reinicia; o painel tenta a mesma
        # porta e morre de novo. O Diego pegou o JARVIS nesse laco, reiniciando
        # a cada 3, 6, 9 segundos, so porque tinha outro JARVIS aberto.
        #
        # Painel e acessorio. Voz e o produto. Se um cai, o outro segue.
        try:
            await uvicorn.Server(cfg).serve()
        except SystemExit:
            print(f"[Dashboard] a porta {PORT} esta ocupada; o Controle Remoto "
                  f"ficou de fora, mas o JARVIS continua funcionando.")
        except Exception as exc:
            print(f"[Dashboard] nao subiu ({type(exc).__name__}: {exc}); "
                  f"o JARVIS continua funcionando.")
