from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
import ctypes

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JarvisCyberBot.MarkXLVIII.App")
    except Exception:
        pass
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QEasingCurve, QLineF, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QFontMetricsF, QKeySequence, QLinearGradient, QPainter,
    QImage, QPainterPath, QPen, QPixmap, QRadialGradient, QShortcut, QIcon,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
    QDialog, QGridLayout, QGraphicsDropShadowEffect,
)

# ── CORES DO CLIENTE (08/10/2026) ──────────────────────────────────────────
# Toda folha de estilo passa pelo `core/tema.py` na hora de ser aplicada. O
# codigo da tela continua escrevendo a paleta ORIGINAL (classe C); quem troca
# e este gancho. A folha original fica guardada no proprio widget para a troca
# AO VIVO (`MainWindow.aplicar_tema`). Ver a explicacao no tema.py.
from core import tema as _tema

_SETSS_ORIGINAL = QWidget.setStyleSheet
_SEM_TEMA = "/*sem-tema*/"


def _setss_com_tema(self, css):
    css = "" if css is None else str(css)
    try:
        self.setProperty("_css_tema", css)
    except Exception:
        pass
    if _SEM_TEMA in css or not _tema.ativo():
        _SETSS_ORIGINAL(self, css)
    else:
        _SETSS_ORIGINAL(self, _tema.css(css))


QWidget.setStyleSheet = _setss_com_tema
_tc = _tema.cor

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

def _app_icon_path() -> Path:
    return _base_dir() / "assets" / "icons" / "jarvis_installer_icon.ico"

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

_DEFAULT_W, _DEFAULT_H = 1280, 820
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 96
_RIGHT_W = 0

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    # VISUAL PREMIUM (05/10/2026): mesma familia de cores (ciano do JARVIS,
    # ambar, verde), em tons mais profundos — azul-noite no fundo, vidro nos
    # paineis e o ciano um pouco mais suave, para brilhar sem cansar a vista.
    BG        = "#00060a"
    PANEL     = "#010d14"
    PANEL2    = "#010f18"
    BORDER    = "#0d3347"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0f4060"
    PRI       = "#00d4ff"
    PRI_DIM   = "#007a99"
    PRI_GHO   = "#001f2e"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    TEXT      = "#8ffcff"
    TEXT_DIM  = "#3a8a9a"
    TEXT_MED  = "#5ab8cc"
    WHITE     = "#d8f8ff"
    DARK      = "#000d14"
    BAR_BG    = "#011520"
    INDIGO    = "#0a6f99"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


def _fx(tamanho: float, peso=None) -> QFont:
    """Fonte do visual premium: Bahnschrift, a fonte 'tecnica' que vem no
    Windows 10/11. Onde nao houver (Linux, Mac), cai na do sistema."""
    f = QFont()
    f.setFamilies(["Bahnschrift", "Segoe UI", "Helvetica Neue", "Arial"])
    f.setPointSizeF(float(tamanho))
    if peso is not None:
        f.setWeight(peso)
    return f


def _ftexto(tamanho: float, peso=None) -> QFont:
    """Fonte de leitura (conversa, campo de digitar)."""
    f = QFont()
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", "Arial"])
    f.setPointSizeF(float(tamanho))
    if peso is not None:
        f.setWeight(peso)
    return f


def _espacado(fonte: QFont, px: float) -> QFont:
    fonte.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(px))
    return fonte


def _brilho(widget, cor: str, raio: int = 18, alfa: int = 130) -> None:
    """Halo de luz em volta de um texto (titulo, relogio)."""
    try:
        ef = QGraphicsDropShadowEffect(widget)
        ef.setBlurRadius(raio)
        ef.setOffset(0, 0)
        ef.setColor(qcol(cor, alfa))
        widget.setGraphicsEffect(ef)
    except Exception:
        pass


_ICON_FONT_FAMILY: str | None = None


def _system_icon_font_family() -> str:
    """Use Microsoft's installed high-DPI icon library on Windows 10/11."""
    global _ICON_FONT_FAMILY
    if _ICON_FONT_FAMILY is None:
        families = QFontDatabase.families()
        if "Segoe Fluent Icons" in families:
            _ICON_FONT_FAMILY = "Segoe Fluent Icons"
        elif "Segoe MDL2 Assets" in families:
            _ICON_FONT_FAMILY = "Segoe MDL2 Assets"
        else:
            _ICON_FONT_FAMILY = "Segoe UI Symbol"
    return _ICON_FONT_FAMILY


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free, works on all platforms if installed
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
        except Exception:
            pass

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes
        try:
            import ctypes
            _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            nv = ctypes.CDLL(_lib)
            nv.nvmlInit_v2()
            dev = ctypes.c_void_p()
            nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess)
        if _OS == "Windows":
            try:
                import wmi  # type: ignore
                w = wmi.WMI(namespace="root/wmi")
                tz = w.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                pass

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

# ═════════════════════════════════════════════════════════════════════════════
#  NUCLEO v3 — O REATOR (05/10/2026, terceira versao do dia)
#
#  A esfera desenhada por codigo ficou "muito feia e simples". Agora o nucleo
#  e montado com pecas desenhadas no ChatGPT (assets/nucleo/*.png, fundo preto):
#      anel_externo.png  -> anel de HUD cheio de detalhe, gira devagar
#      anel_interno.png  -> anel do reator, gira para o outro lado
#      energia.png       -> a energia do meio, PULSA com cada silaba da voz
#  Fundo preto + desenho por SOMA DE LUZ (CompositionMode_Plus): o preto nao
#  soma nada, entao nao precisa de transparencia nem aparece quadrado.
#
#  Parado ele fica PEQUENO; quando o JARVIS fala ele CRESCE no ritmo do volume
#  real da voz (main.py manda o volume de cada 20 ms). Os aneis crescem pela
#  fala inteira (envelope lento); a energia do meio salta a cada silaba.
#      dono falando -> pecas ficam verdes e saem ondas
#      pensando     -> dourado, anel de dentro girando rapido
#      mudo         -> vermelho apagado, quase parado
#  Sem as imagens (pasta apagada), desenha aneis simples: nunca quebra.
# ═════════════════════════════════════════════════════════════════════════════

_NUCLEO_COR = {            # cor dos detalhes de cada estado (paleta original)
    "ocioso":   "#00d4ff",
    "jarvis":   "#00d4ff",
    "dono":     "#00ff88",
    "pensando": "#ffcc00",
    "mudo":     "#ff3366",
}


def _misturar(a: QColor, b: QColor, k: float) -> QColor:
    k = max(0.0, min(1.0, k))
    return QColor(
        round(a.red() + (b.red() - a.red()) * k),
        round(a.green() + (b.green() - a.green()) * k),
        round(a.blue() + (b.blue() - a.blue()) * k),
        round(a.alpha() + (b.alpha() - a.alpha()) * k),
    )


class HudCanvas(QWidget):
    """O nucleo do JARVIS: reator que cresce com a voz."""

    _PECAS = ("anel_externo", "anel_interno", "energia")
    # raio de cada peca em relacao ao raio do anel externo
    _RAIO_INTERNO = 0.555     # o anel do reator cabe no buraco do anel de HUD
    _RAIO_ENERGIA = 0.40
    _BASE = 80                # altura do nome do estado + medidor de voz
    _N_BARRAS = 48

    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick = 0
        # O QUANTO O DONO ESTA FALANDO, de 0 a 1 (05/09/2026). Vem do
        # microfone de verdade — o mesmo nivel do portao de voz.
        self.nivel_mic = 0.0
        self._mic_ref = 0.25        # pico recente da voz do dono (escala do grafico)
        self._mic_mostrar = 0.0     # nivel do dono ja na escala do microfone dele
        # O QUANTO O JARVIS ESTA FALANDO, de 0 a 1 (05/10/2026). Vem do som que
        # vai para a caixa (main.py, antes do stream.write), em fatias de 20 ms
        # com a hora em que cada uma toca.
        self.nivel_voz = 0.0
        self._voz_fila: deque = deque()
        self._voz_real_em = 0.0
        self._voz_sint = 0.0
        self._voz_lenta = 0.0

        self._t_ant = time.monotonic()
        self._fase = 0.0
        self._ang = [0.0, 0.0, 0.0, 0.0]   # externo, interno, energia 1, energia 2
        self._tam = 0.60
        self._pulso = 0.0
        self._energia = 0.3
        self._modo = "ocioso"
        self._modo_vis = "ocioso"
        self._modo_ant = "ocioso"
        self._mix = 1.0
        self._cor = QColor(_tema.nucleo("ocioso"))
        self._tema_v = -1
        self._ondas: list[list] = []
        self._vale = 0.0
        self._ultima_onda = 0.0
        # MEDIDOR DE VOZ (05/10/2026, pedido do Diego: "igual o antigo"). Uma
        # fileira de barras embaixo do nome do estado que sobe com a voz: a do
        # JARVIS (ciano, volume real da caixa) ou a do dono (verde, microfone).
        self._barras = [0.0] * self._N_BARRAS
        self._ruido = [random.random() for _ in range(self._N_BARRAS)]
        self._ruido_alvo = list(self._ruido)
        self._pontos = [[random.uniform(0, 2 * math.pi), random.uniform(1.03, 1.20),
                         random.uniform(0.15, 0.45) * random.choice((-1, 1)),
                         random.uniform(0.6, 1.3)] for _ in range(20)]

        self._originais: dict = {}
        for nome in self._PECAS:
            px = QPixmap(str(_base_dir() / "assets" / "nucleo" / f"{nome}.png"))
            self._originais[nome] = None if px.isNull() else px
        self._cache_tam = None
        self._fundo: QPixmap | None = None
        self._pecas: dict = {}          # (nome, modo) -> pixmap no tamanho maximo
        self._luz: QPixmap | None = None
        self._luzes: dict = {}
        self._geo = (0.0, 0.0, 100.0, 120.0)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    # ── entradas de som ─────────────────────────────────────────────────────
    def ouviu(self, nivel: float) -> None:
        """Nivel do microfone do dono (0 a 1). Sobe na hora, desce no `_step`.

        NAO chama `update()` — quem repinta e o cronometro de 16 ms. Tocar em
        widget de outra linha de execucao derruba o Qt.
        """
        try:
            v = float(nivel)
        except Exception:
            return
        if v > self.nivel_mic:
            self.nivel_mic = min(1.0, v)

    def falou(self, niveis, passo: float, inicio: float) -> None:
        """Volume da voz do JARVIS: uma fatia a cada `passo` segundos a partir
        de `inicio` (relogio time.monotonic). Chamado da linha do audio: so
        guarda na fila (deque.append e seguro entre linhas)."""
        try:
            t = float(inicio)
            for v in niveis:
                self._voz_fila.append((t, max(0.0, min(1.0, float(v)))))
                t += passo
        except Exception:
            pass

    # ── movimento ───────────────────────────────────────────────────────────
    def _modo_atual(self) -> str:
        st = str(self.state).upper()
        if self.muted:
            return "mudo"
        if self.speaking:
            return "jarvis"
        if st in ("THINKING", "PROCESSING", "EDITING_IMAGE", "CREATING_IMAGE",
                  "CREATING_SITE", "SEARCHING_LEADS"):
            return "pensando"
        if self.nivel_mic > 0.05:
            return "dono"
        return "ocioso"

    def _step(self):
        self._tick += 1
        agora = time.monotonic()
        dt = max(0.004, min(0.05, agora - self._t_ant))
        self._t_ant = agora
        self._fase += dt

        if self.nivel_mic > 0.001:
            self.nivel_mic *= 0.90
        else:
            self.nivel_mic = 0.0
        # O GRAFICO SE ADAPTA AO MICROFONE DE CADA UM (05/10/2026). Com escala
        # fixa, o microfone do Diego (que capta baixo: o ouvido aplica ganho de
        # 3 a 8 vezes) fazia o grafico dele mal sair do chao, enquanto o do
        # JARVIS ia ao teto. Aqui guardo o PICO recente da voz do dono (desce
        # devagar, ~8 s para cair pela metade) e o grafico mostra o nivel em
        # relacao a ele. O piso de 0.10 impede que chiado vire grafico cheio.
        self._mic_ref = max(0.10, self._mic_ref * (0.9985 ** (dt * 60)), self.nivel_mic)
        self._mic_mostrar = min(1.0, (self.nivel_mic / self._mic_ref) ** 0.8) if self.nivel_mic > 0.001 else 0.0

        alvo_voz = None
        while self._voz_fila and self._voz_fila[0][0] <= agora:
            alvo_voz = self._voz_fila.popleft()[1]
            self._voz_real_em = agora
        if len(self._voz_fila) > 600:            # fila parada: descarta
            self._voz_fila.clear()
        if alvo_voz is not None:
            k = 0.70 if alvo_voz > self.nivel_voz else 0.30
            self.nivel_voz += (alvo_voz - self.nivel_voz) * k
        elif self.speaking and (agora - self._voz_real_em) > 1.2:
            # Voz que nao passa pelo tocador principal (voz local): imita.
            if self._tick % 6 == 0:
                self._voz_sint = random.uniform(0.15, 0.85)
            self.nivel_voz += (self._voz_sint - self.nivel_voz) * 0.30
        else:
            self.nivel_voz *= 0.85
        if not self.speaking and (agora - self._voz_real_em) > 0.4:
            self.nivel_voz *= 0.80
        # envelope lento: os aneis crescem pela FALA, nao por silaba
        alvo_lento = self.nivel_voz if self.speaking else 0.0
        self._voz_lenta += (alvo_lento - self._voz_lenta) * (0.10 if alvo_lento > self._voz_lenta else 0.035)

        modo = self._modo_atual()
        self._modo = modo
        # as pecas so trocam de cor em estados "de verdade" (dono, pensando,
        # mudo); falando e quieto usam as pecas originais, em ciano
        vis = modo if modo in ("dono", "pensando", "mudo") else "ocioso"
        if vis != self._modo_vis:
            self._modo_ant = self._modo_vis
            self._modo_vis = vis
            self._mix = 0.0
        self._mix = min(1.0, self._mix + dt * 3.5)
        self._cor = _misturar(self._cor, QColor(_tema.nucleo(modo)), 0.10)

        respira = math.sin(self._fase * 1.4)
        if modo == "jarvis":
            v = self.nivel_voz
            alvo_tam = 0.62 + 0.38 * min(1.0, self._voz_lenta * 1.6) + 0.05 * v
            alvo_e = 0.55 + 0.45 * v
            giros = (10 + 40 * v, -(22 + 90 * v), 25 + 60 * v, -(40 + 80 * v))
            forca = v
        elif modo == "dono":
            v = self._mic_mostrar
            alvo_tam, alvo_e = 0.66 + 0.10 * v, 0.50 + 0.35 * v
            giros = (8 + 20 * v, -(18 + 40 * v), 20, -32)
            forca = v
        elif modo == "pensando":
            alvo_tam, alvo_e = 0.68 + 0.03 * respira, 0.62
            giros = (14, -120, 40, -70)
            forca = 0.0
        elif modo == "mudo":
            alvo_tam, alvo_e = 0.55, 0.12
            giros = (2, -4, 4, -6)
            forca = 0.0
        else:
            alvo_tam, alvo_e = 0.60 + 0.015 * respira, 0.32 + 0.06 * respira
            giros = (6, -14, 18, -28)
            forca = 0.0
        self._tam += (alvo_tam - self._tam) * (0.16 if alvo_tam > self._tam else 0.06)
        self._energia += (alvo_e - self._energia) * (0.30 if alvo_e > self._energia else 0.08)
        # a energia do meio salta com a silaba (rapido) e volta
        alvo_pulso = self.nivel_voz if modo == "jarvis" else (0.5 * self._mic_mostrar if modo == "dono" else 0.0)
        self._pulso += (alvo_pulso - self._pulso) * (0.55 if alvo_pulso > self._pulso else 0.18)
        for i, g in enumerate(giros):
            self._ang[i] = (self._ang[i] + g * dt) % 360

        # onda que sai do reator a cada "tranco" da voz
        if forca < self._vale:
            self._vale = forca
        if forca - self._vale > 0.22 and (agora - self._ultima_onda) > 0.16 and len(self._ondas) < 7:
            self._ondas.append([0.0, 1.0, QColor(self._cor)])
            self._ultima_onda = agora
            self._vale = forca
        vivas = []
        for o in self._ondas:
            o[0] += dt * 0.9
            o[1] -= dt * 0.85
            if o[1] > 0:
                vivas.append(o)
        self._ondas = vivas

        for pt in self._pontos:
            pt[0] += pt[2] * dt * (0.6 + 2.0 * self._energia)
        self._mover_barras(modo)

        # Minimizado ninguem ve: nao gasta processador repintando.
        win = self.window()
        if win is not None and win.isMinimized():
            return
        if self.isVisible():
            self.update()

    def _mover_barras(self, modo: str) -> None:
        n = self._N_BARRAS
        if self._tick % 4 == 0:
            self._ruido_alvo = [random.random() for _ in range(n)]
        t = self._fase
        if modo == "jarvis":
            nivel = self.nivel_voz
        elif modo == "dono":
            nivel = self._mic_mostrar
        else:
            nivel = 0.0
        for i in range(n):
            self._ruido[i] += (self._ruido_alvo[i] - self._ruido[i]) * 0.35
            u = (i + 0.5) / n
            corcova = math.sin(math.pi * u) ** 0.6       # o meio sobe mais
            if modo in ("jarvis", "dono"):
                onda = 0.55 + 0.45 * math.sin(t * 9.0 + i * 0.7)
                alvo = min(1.0, 1.6 * nivel * corcova * (0.30 + 0.45 * self._ruido[i] + 0.25 * onda))
            elif modo == "pensando":
                alvo = 0.10 + 0.08 * math.sin(t * 5.0 + i * 0.45)
            elif modo == "mudo":
                alvo = 0.02
            else:   # quieto: respira baixinho
                alvo = 0.05 + 0.03 * math.sin(t * 1.8 + i * 0.35)
            atual = self._barras[i]
            self._barras[i] = atual + (alvo - atual) * (0.55 if alvo > atual else 0.18)

    # ── caches ──────────────────────────────────────────────────────────────
    def _geometria(self, W: int, H: int):
        cx = W / 2.0
        livre = H - self._BASE                  # embaixo: estado + medidor de voz
        cy = livre * 0.5 + 4
        alcance = max(60.0, min(cx, W - cx, cy, livre - cy + 8) - 2.0)
        R = max(40.0, min(alcance * 0.96, W * 0.30))     # raio do anel externo no maximo
        return cx, cy, R, alcance

    def _montar_cache(self, W: int, H: int) -> None:
        self._cache_tam = (W, H)
        cx, cy, R, alcance = self._geometria(W, H)
        self._geo = (cx, cy, R, alcance)
        dpr = self.devicePixelRatioF()

        # Fundo do JARVIS de sempre: quase preto, grade de pontinhos e uma luz
        # no centro que some na cor da janela (sem emenda com o resto).
        fundo = QPixmap(max(1, int(W * dpr)), max(1, int(H * dpr)))
        fundo.setDevicePixelRatio(dpr)
        fundo.fill(qcol(_tc(C.BG)))
        p = QPainter(fundo)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QRadialGradient(QPointF(cx, cy), alcance)
        g.setColorAt(0.0, qcol(_tc("#00263a")))
        g.setColorAt(0.40, qcol(_tc("#001522")))
        g.setColorAt(0.80, qcol(_tc("#000a10")))
        g.setColorAt(1.0, qcol(_tc(C.BG)))
        p.fillRect(QRectF(0, 0, W, H), QBrush(g))
        p.setPen(QPen(qcol(_tc(C.PRI_GHO)), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                p.drawPoint(x, y)
        p.end()
        self._fundo = fundo

        # Pecas ja no tamanho maximo da tela (diminuir um pouco a cada quadro
        # e barato; aumentar borraria).
        self._pecas = {}
        lado = {"anel_externo": 2 * R,
                "anel_interno": 2 * R * self._RAIO_INTERNO,
                "energia": 2 * R * self._RAIO_ENERGIA * 1.7}
        for nome, px in self._originais.items():
            if px is None:
                continue
            n = max(8, int(lado[nome] * dpr))
            esc = px.scaled(n, n, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
            self._pecas[(nome, "ocioso")] = esc

        if self._luz is None:
            n = 128
            px = QPixmap(n, n)
            px.fill(Qt.GlobalColor.transparent)
            p = QPainter(px)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            lg = QRadialGradient(QPointF(n / 2, n / 2), n / 2)
            lg.setColorAt(0.0, QColor(255, 255, 255, 255))
            lg.setColorAt(0.25, QColor(255, 255, 255, 170))
            lg.setColorAt(0.60, QColor(255, 255, 255, 50))
            lg.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(lg))
            p.drawEllipse(QRectF(0, 0, n, n))
            p.end()
            self._luz = px

    def _peca(self, nome: str, modo: str):
        """A peca na cor do estado. A original (ciano com detalhes laranja)
        vale para quieto e falando; as outras sao a mesma peca em tons de uma
        cor so, feitas uma vez por tamanho de tela."""
        # Nucleo com cor escolhida pelo cliente: em repouso as pecas tambem sao
        # tingidas (a arte original e ciano com detalhes laranja).
        if modo == "ocioso" and _tema.nucleo_tingido():
            modo = "ocioso_tingido"
        px = self._pecas.get((nome, modo))
        if px is not None:
            return px
        base = self._pecas.get((nome, "ocioso"))
        if base is None:
            return None
        img = base.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
        img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        p.fillRect(img.rect(), QColor(_tema.nucleo("ocioso" if modo == "ocioso_tingido" else modo)))
        p.end()
        px = QPixmap.fromImage(img)
        px.setDevicePixelRatio(base.devicePixelRatio())
        self._pecas[(nome, modo)] = px
        return px

    def _luz_cor(self, cor: QColor) -> QPixmap:
        chave = (cor.red() // 8, cor.green() // 8, cor.blue() // 8)
        px = self._luzes.get(chave)
        if px is None:
            if len(self._luzes) > 120:
                self._luzes.clear()
            px = QPixmap(self._luz.size())
            px.fill(Qt.GlobalColor.transparent)
            p = QPainter(px)
            p.drawPixmap(0, 0, self._luz)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(px.rect(), QColor(cor.red(), cor.green(), cor.blue()))
            p.end()
            self._luzes[chave] = px
        return px

    # ── desenho de cada quadro ──────────────────────────────────────────────
    def _desenhar_peca(self, p, nome: str, cx: float, cy: float, raio: float,
                       ang: float, opac: float) -> bool:
        """Desenha a peca centrada, girada e no raio pedido (com troca de cor
        suave entre estados). Devolve False se a imagem nao existe."""
        atual = self._peca(nome, self._modo_vis)
        if atual is None:
            return False
        lado_px = atual.width() / atual.devicePixelRatio()
        if lado_px <= 0:
            return False
        esc = (2 * raio) / lado_px
        p.save()
        p.translate(cx, cy)
        p.rotate(ang)
        p.scale(esc, esc)
        alvo = QRectF(-lado_px / 2, -lado_px / 2, lado_px, lado_px)
        if self._mix < 1.0:
            ant = self._peca(nome, self._modo_ant)
            if ant is not None:
                p.setOpacity(opac * (1.0 - self._mix))
                p.drawPixmap(alvo, ant, QRectF(ant.rect()))
        p.setOpacity(opac * self._mix)
        p.drawPixmap(alvo, atual, QRectF(atual.rect()))
        p.restore()
        return True

    def paintEvent(self, _):
        W, H = self.width(), self.height()
        if self._cache_tam != (W, H) or self._tema_v != _tema.versao():
            self._tema_v = _tema.versao()
            self._montar_cache(W, H)
        cx, cy, R, alcance = self._geo
        e = self._energia
        cor = self._cor
        r = R * self._tam                      # raio do anel externo AGORA
        FR = QPainter.PixmapFragment.create
        FS = QRectF(0, 0, 128, 128)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(0, 0, self._fundo)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        luz = self._luz_cor(cor)
        esc = min(alcance, r * 1.35) / 64.0
        p.drawPixmapFragments([FR(QPointF(cx, cy), FS, esc, esc, 0, 0.10 + 0.22 * e)], luz)

        # ondas de voz saindo do reator
        p.setBrush(Qt.BrushStyle.NoBrush)
        for raio_rel, vida, cor_o in self._ondas:
            rr = r + (alcance - r) * min(1.0, raio_rel / 1.1)
            p.setPen(QPen(QColor(cor_o.red(), cor_o.green(), cor_o.blue(), int(140 * vida * vida)),
                          1.0 + 1.5 * vida))
            p.drawEllipse(QPointF(cx, cy), rr, rr)

        tem = self._desenhar_peca(p, "anel_externo", cx, cy, r, self._ang[0], 0.55 + 0.45 * e)
        self._desenhar_peca(p, "anel_interno", cx, cy, r * self._RAIO_INTERNO, self._ang[1],
                            0.60 + 0.40 * e)
        # a energia: duas copias girando ao contrario (parece que ferve),
        # crescendo a cada silaba
        re = r * self._RAIO_ENERGIA * (0.80 + 0.55 * self._pulso)
        tem_e = self._desenhar_peca(p, "energia", cx, cy, re, self._ang[2], 0.75 + 0.25 * e)
        if tem_e:
            self._desenhar_peca(p, "energia", cx, cy, re * 0.92, self._ang[3], 0.35 + 0.35 * e)
        if not tem:
            self._aneis_simples(p, cx, cy, r, e)

        # brilho branco no centro, que bate com a voz
        branco = self._luz_cor(_misturar(cor, QColor(255, 255, 255), 0.6))
        esc = r * (0.16 + 0.22 * self._pulso) / 64.0
        p.drawPixmapFragments([FR(QPointF(cx, cy), FS, esc, esc, 0, 0.35 + 0.55 * e)], branco)

        # pontinhos de luz girando em volta
        pts = []
        for ang, dist, _vel, tam in self._pontos:
            rr = min(r * dist, alcance - 6)
            esc = (3.0 + 2.5 * e) * tam / 64.0
            pts.append(FR(QPointF(cx + math.cos(ang) * rr, cy + math.sin(ang) * rr), FS,
                          esc, esc, 0, min(1.0, 0.15 + 0.85 * e)))
        p.drawPixmapFragments(pts, luz)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        y_txt = self._desenhar_status(p, W, H, cx, cy, R)
        self._desenhar_medidor(p, W, y_txt + 26)
        p.end()

    def _aneis_simples(self, p, cx, cy, r, e) -> None:
        """Reserva: sem as imagens do nucleo, aneis desenhados por codigo."""
        cor = self._cor
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k, (fr, larg, comp, ang) in enumerate(((1.0, 2.0, 70, self._ang[0]),
                                                  (0.78, 1.4, 40, self._ang[1]),
                                                  (0.56, 3.0, 90, -self._ang[0]))):
            rr = r * fr
            ret = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)
            p.setPen(QPen(QColor(cor.red(), cor.green(), cor.blue(), int(120 + 120 * e)), larg,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for rep in range(3):
                p.drawArc(ret, int((ang + rep * 120) * 16), int(comp * 16))

    def _desenhar_status(self, p, W, H, cx, cy, R) -> None:
        # Tarefa longa aparece na frente de "FALANDO": enquanto o JARVIS
        # desenha uma imagem ou busca leads, o cliente precisa ver o que esta
        # sendo feito.
        _TAREFAS = {
            "EDITING_IMAGE":   "EDITANDO IMAGEM",
            "CREATING_IMAGE":  "CRIANDO IMAGEM",
            "CREATING_SITE":   "CRIANDO SITE",
            "SEARCHING_LEADS": "BUSCANDO LEADS",
        }
        _tarefa = _TAREFAS.get(str(self.state).upper())
        if self.muted:
            txt = "MICROFONE MUDO"
        elif _tarefa:
            txt = _tarefa
        elif self.speaking:
            txt = "FALANDO"
        elif self.state == "THINKING":
            txt = "PENSANDO"
        elif self.state == "PROCESSING":
            txt = "PROCESSANDO"
        elif self.state == "LISTENING":
            txt = "OUVINDO"
        else:
            txt = {
                "INITIALISING": "INICIANDO",
                "MUTED": "MICROFONE MUDO",
                "SPEAKING": "FALANDO",
                "THINKING": "PENSANDO",
                "PROCESSING": "PROCESSANDO",
                "LISTENING": "OUVINDO",
            }.get(str(self.state).upper(), str(self.state))
        f = getattr(self, "_fonte_status", None)
        if f is None:
            f = self._fonte_status = _espacado(_fx(9.5, QFont.Weight.DemiBold), 5.0)
        p.setFont(f)
        cor = self._cor
        # lugar fixo, abaixo do tamanho maximo: nao pula enquanto o reator cresce
        y = min(H - self._BASE + 4.0, cy + R + 4)
        p.setPen(QPen(QColor(cor.red(), cor.green(), cor.blue(), 220), 1))
        p.drawText(QRectF(0, y, W, 22), Qt.AlignmentFlag.AlignCenter, txt)
        return y

    def _desenhar_medidor(self, p, W, topo: float) -> None:
        """As barras de voz: sobem a partir de uma linha, mais claras na ponta,
        com um reflexo fraco embaixo (como o medidor antigo, mais caprichado)."""
        n = self._N_BARRAS
        alto = 32.0
        larg, vao = 5.0, 3.0
        total = n * (larg + vao) - vao
        x0 = W / 2.0 - total / 2.0
        base = topo + alto
        cor = self._cor
        r, g, b = cor.red(), cor.green(), cor.blue()
        ativo = self._modo in ("jarvis", "dono")
        grad = QLinearGradient(QPointF(0, base), QPointF(0, topo))
        grad.setColorAt(0.0, QColor(r, g, b, 150 if ativo else 70))
        grad.setColorAt(1.0, QColor(min(255, r + 120), min(255, g + 60), min(255, b + 40),
                                    255 if ativo else 110))
        reflexo = QColor(r, g, b, 40 if ativo else 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        for i, v in enumerate(self._barras):
            h = max(2.0, alto * min(1.0, v))
            x = x0 + i * (larg + vao)
            p.drawRoundedRect(QRectF(x, base - h, larg, h), 1.5, 1.5)
        p.setBrush(QBrush(reflexo))
        for i, v in enumerate(self._barras):
            h = max(1.0, alto * 0.35 * min(1.0, v))
            p.drawRect(QRectF(x0 + i * (larg + vao), base + 2, larg, h))
        # linha fina de base que some nas pontas
        lg = QLinearGradient(QPointF(x0 - 30, 0), QPointF(x0 + total + 30, 0))
        lg.setColorAt(0.0, QColor(r, g, b, 0))
        lg.setColorAt(0.5, QColor(r, g, b, 120))
        lg.setColorAt(1.0, QColor(r, g, b, 0))
        p.setPen(QPen(QBrush(lg), 1.0))
        p.drawLine(QPointF(x0 - 30, base + 1), QPointF(x0 + total + 30, base + 1))


class _MarcaJarvis(QWidget):
    """O simbolo no alto da barra lateral: um mini nucleo que gira devagar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 52)
        self._ang = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._girar)
        self._tmr.start(40)

    def _girar(self):
        self._ang = (self._ang + 2.4) % 360
        win = self.window()
        if self.isVisible() and not (win is not None and win.isMinimized()):
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(26, 26)
        halo = QRadialGradient(c, 26)
        halo.setColorAt(0.0, qcol(_tc(C.PRI), 70))
        halo.setColorAt(1.0, qcol(_tc(C.PRI), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(c, 26, 26)
        anel = QConicalGradient(c, -self._ang)
        anel.setColorAt(0.0, qcol(_tc(C.PRI), 255))
        anel.setColorAt(0.35, qcol(_tc(C.INDIGO), 190))
        anel.setColorAt(0.70, qcol(_tc(C.PRI), 20))
        anel.setColorAt(1.0, qcol(_tc(C.PRI), 255))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(anel), 2.2))
        p.drawEllipse(c, 16.5, 16.5)
        p.setPen(QPen(qcol(_tc(C.PRI), 70), 1.0))
        p.drawEllipse(c, 11.0, 11.0)
        p.setPen(QPen(qcol(_tc(C.PRI), 170), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        ret = QRectF(c.x() - 21, c.y() - 21, 42, 42)
        for rep in range(2):
            p.drawArc(ret, int(((self._ang * 1.6) + rep * 180) * 16), 38 * 16)
        nucleo = QRadialGradient(c, 7.0)
        nucleo.setColorAt(0.0, qcol(_tc("#ffffff"), 255))
        nucleo.setColorAt(0.35, qcol(_tc(C.PRI), 230))
        nucleo.setColorAt(1.0, qcol(_tc(C.PRI), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(nucleo))
        p.drawEllipse(c, 7.0, 7.0)
        p.end()


def _linha_de_luz(vertical: bool = False) -> QFrame:
    """Filete de 1 px que acende no meio e some nas pontas."""
    f = QFrame()
    if vertical:
        f.setFixedWidth(1)
        eixo = "x1:0, y1:0, x2:0, y2:1"
    else:
        f.setFixedHeight(1)
        eixo = "x1:0, y1:0, x2:1, y2:0"
    f.setStyleSheet(
        f"background: qlineargradient({eixo}, stop:0 rgba(0,212,255,0),"
        " stop:0.5 rgba(0,212,255,150), stop:1 rgba(0,212,255,0)); border: none;")
    return f


class MetricBar(QWidget):
    """Cartao de medida do sistema (CPU, RAM...): anel + numero."""

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(56)
        self.setMinimumWidth(86)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        W, H = self.width(), self.height()
        r = QRectF(0.5, 0.5, W - 1, H - 1)
        p.setBrush(QBrush(qcol(_tc("#ffffff"), 7)))
        p.setPen(QPen(qcol(_tc("#00d4ff"), 24), 1))
        p.drawRoundedRect(r, 12, 12)

        if self._value > 85:
            cor = qcol(_tc(C.RED))
        elif self._value > 65:
            cor = qcol(_tc(C.ACC))
        else:
            cor = qcol(_tc(self._color))

        c = QPointF(23.0, H / 2.0)
        raio = 13.0
        ret = QRectF(c.x() - raio, c.y() - raio, raio * 2, raio * 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(_tc(C.BAR_BG)), 3.4))
        p.drawEllipse(c, raio, raio)
        if self._value > 0.5:
            p.setPen(QPen(cor, 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ret, 90 * 16, -int(self._value * 3.6 * 16))
            ang = math.radians(90 - self._value * 3.6)
            ponta = QPointF(c.x() + raio * math.cos(ang), c.y() - raio * math.sin(ang))
            halo = QRadialGradient(ponta, 7.0)
            halo.setColorAt(0.0, qcol(cor.name(), 170))
            halo.setColorAt(1.0, qcol(cor.name(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(halo))
            p.drawEllipse(ponta, 7.0, 7.0)
        miolo = QRadialGradient(c, raio * 0.75)
        miolo.setColorAt(0.0, qcol(cor.name(), 70))
        miolo.setColorAt(1.0, qcol(cor.name(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(miolo))
        p.drawEllipse(c, raio * 0.75, raio * 0.75)

        x = 45.0
        p.setFont(_espacado(_fx(6.4, QFont.Weight.DemiBold), 1.4))
        p.setPen(QPen(qcol(_tc(C.TEXT_DIM)), 1))
        p.drawText(QRectF(x, 9, W - x - 6, 13),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)
        p.setFont(_fx(11.0 if len(self._text) <= 4 else 9.0, QFont.Weight.DemiBold))
        apagado = self._text in ("--", "N/D")
        p.setPen(QPen(qcol(_tc(C.TEXT_DIM)) if apagado else qcol(_tc(C.WHITE), 238), 1))
        p.drawText(QRectF(x, 23, W - x - 4, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._text)


class ControlCard(QPushButton):
    """Vector control surface; native QPushButton signals remain untouched."""

    def __init__(self, text: str, color: str, background: str, parent=None):
        super().__init__(parent)
        self._card_color = color
        self._card_background = background
        self._display_color = QColor(color)
        self._display_background = QColor(background)
        self._hovering = False
        self._hover_progress = 0.0
        self._press_progress = 0.0
        self._ripple_progress = 1.0
        self._ripple_origin = QPointF(54.0, 58.0)
        self._whatsapp_renderer: QSvgRenderer | None = None

        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._set_hover_progress)

        self._press_anim = QVariantAnimation(self)
        self._press_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._press_anim.valueChanged.connect(self._set_press_progress)

        self._ripple_anim = QVariantAnimation(self)
        self._ripple_anim.setDuration(380)
        self._ripple_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._ripple_anim.valueChanged.connect(self._set_ripple_progress)

        self._theme_from_color = QColor(color)
        self._theme_to_color = QColor(color)
        self._theme_from_background = QColor(background)
        self._theme_to_background = QColor(background)
        self._theme_anim = QVariantAnimation(self)
        self._theme_anim.setDuration(240)
        self._theme_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._theme_anim.valueChanged.connect(self._set_theme_progress)

        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background: transparent; border: none;")

    def set_card_theme(self, color: str, background: str) -> None:
        self._theme_anim.stop()
        self._theme_from_color = QColor(self._display_color)
        self._theme_to_color = QColor(color)
        self._theme_from_background = QColor(self._display_background)
        self._theme_to_background = QColor(background)
        self._card_color = color
        self._card_background = background
        self._theme_anim.setStartValue(0.0)
        self._theme_anim.setEndValue(1.0)
        self._theme_anim.start()

    @staticmethod
    def _mix_color(a: QColor, b: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, float(amount)))
        inv = 1.0 - amount
        return QColor(
            round(a.red() * inv + b.red() * amount),
            round(a.green() * inv + b.green() * amount),
            round(a.blue() * inv + b.blue() * amount),
            round(a.alpha() * inv + b.alpha() * amount),
        )

    @staticmethod
    def _run_animation(
        animation: QVariantAnimation, start: float, end: float, duration: int
    ) -> None:
        animation.stop()
        animation.setDuration(duration)
        animation.setStartValue(float(start))
        animation.setEndValue(float(end))
        animation.start()

    def _set_hover_progress(self, value) -> None:
        self._hover_progress = float(value)
        self.update()

    def _set_press_progress(self, value) -> None:
        self._press_progress = float(value)
        self.update()

    def _set_ripple_progress(self, value) -> None:
        self._ripple_progress = float(value)
        self.update()

    def _set_theme_progress(self, value) -> None:
        progress = float(value)
        self._display_color = self._mix_color(
            self._theme_from_color, self._theme_to_color, progress
        )
        self._display_background = self._mix_color(
            self._theme_from_background, self._theme_to_background, progress
        )
        self.update()

    def enterEvent(self, event):
        self._hovering = True
        self._run_animation(self._hover_anim, self._hover_progress, 1.0, 170)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        self._run_animation(self._hover_anim, self._hover_progress, 0.0, 220)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._ripple_origin = event.position()
            self._ripple_progress = 0.0
            self._run_animation(self._press_anim, self._press_progress, 1.0, 85)
            self._run_animation(self._ripple_anim, 0.0, 1.0, 380)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._run_animation(self._press_anim, self._press_progress, 0.0, 155)

    def _paint_vector_icon(
        self, painter: QPainter, center: QPointF, color: QColor, status: str,
        k: float = 1.0
    ) -> None:
        """Render crisp controls from Windows' native Fluent icon library."""
        del status  # State is already conveyed by color, LED and the status pill.
        if self.objectName() == "whatsapp":
            if self._whatsapp_renderer is None:
                asset = _base_dir() / "assets" / "icons" / "whatsapp.svg"
                self._whatsapp_renderer = QSvgRenderer(str(asset), self)
            if self._whatsapp_renderer.isValid():
                painter.save()
                painter.setOpacity(0.92 + self._hover_progress * 0.08)
                self._whatsapp_renderer.render(
                    painter,
                    QRectF(center.x() - 14 * k, center.y() - 14 * k, 28 * k, 28 * k),
                )
                painter.restore()
                return
        glyphs = {
            "mute": "\ue720",       # Microphone
            "interrupt": "\ue71a",  # Stop
            "whatsapp": "\ue8bd",   # Message
            "remote": "\ue703",     # Connect devices
            "webcam": "\ue722",     # Camera
            "cowork": "\ue943",     # Code
            "computer_use": "\ue7f4",  # Monitor (08/10/2026)
        }
        # O ICONE VEM DAQUI, NAO DO TEXTO DO CARTAO.
        #
        # Eu tinha posto um cadeado na PRIMEIRA linha do texto achando que ela
        # virava icone — nao vira: o `paintEvent` usa a linha 1 como titulo e a
        # 2 como estado, e a linha 0 e ignorada. O cartao novo caia no
        # `\uecca` generico e ficava com o mesmo circulo nos dois estados.
        #
        # Este e o unico cartao que muda de icone com o estado, entao ele nao
        # cabe na tabela: o cadeado fecha quando o portao esta ligado.
        if self.objectName() == "so_nome":
            linhas = self.text().splitlines()
            titulo = linhas[1] if len(linhas) > 1 else ""
            glyph = "\ue72e" if titulo.startswith("SÓ") else "\ue785"
        else:
            glyph = glyphs.get(self.objectName(), "\uecca")
        painter.save()
        painter.setFont(QFont(_system_icon_font_family(), max(12, int(round(22 * k)))))
        painter.setPen(QPen(color, 1.0))
        painter.drawText(
            QRectF(center.x() - 18 * k, center.y() - 18 * k, 36 * k, 36 * k),
            Qt.AlignmentFlag.AlignCenter,
            glyph,
        )
        painter.restore()

    def paintEvent(self, _):
        # BOTAO DO DOCK (visual novo, 05/10/2026). Texto em 3 linhas, animacoes
        # e sinais iguais aos de antes; so o desenho mudou.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        hover = self._hover_progress
        press = self._press_progress
        y_shift = -2.5 * hover + 1.5 * press
        rect = QRectF(3.0, 3.0 + y_shift, w - 6.0, h - 6.0)
        radius = 16.0
        accent = QColor(_tema.cor_botao(self.objectName(), self._display_color.name())
                        or self._display_color.name())

        if hover > 0.01 or press > 0.01:
            fundo = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            fundo.setColorAt(0.0, qcol(accent.name(), round(34 * hover + 30 * press)))
            fundo.setColorAt(1.0, qcol(accent.name(), round(8 * hover)))
            p.setBrush(QBrush(fundo))
            p.setPen(QPen(qcol(accent.name(), round(90 * hover)), 1.0))
            p.drawRoundedRect(rect, radius, radius)

        lines = self.text().splitlines()
        title = lines[1] if len(lines) > 1 else ""
        status = lines[2] if len(lines) > 2 else ""

        ic = QPointF(w / 2.0, 28.0 + y_shift)
        r_ic = 18.0
        halo = QRadialGradient(ic, r_ic * 2.1)
        halo.setColorAt(0.0, qcol(accent.name(), round(55 + 70 * hover)))
        halo.setColorAt(0.55, qcol(accent.name(), round(12 + 20 * hover)))
        halo.setColorAt(1.0, qcol(accent.name(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(ic, r_ic * 2.1, r_ic * 2.1)

        disco = QLinearGradient(QPointF(ic.x(), ic.y() - r_ic), QPointF(ic.x(), ic.y() + r_ic))
        disco.setColorAt(0.0, self._mix_color(QColor(_tc("#042a3a")), accent, 0.30))
        disco.setColorAt(1.0, self._mix_color(QColor(_tc("#010d14")), accent, 0.10))
        p.setBrush(QBrush(disco))
        p.setPen(QPen(qcol(accent.name(), round(120 + 100 * hover)), 1.2))
        p.drawEllipse(ic, r_ic, r_ic)
        cor_icone = self._mix_color(accent, QColor(_tc("#ffffff")), 0.30 + 0.25 * hover)
        self._paint_vector_icon(p, ic, cor_icone, status, 0.82)

        if status.strip().upper() in {"ATIVO", "ATIVA"}:
            led = QPointF(ic.x() + r_ic * 0.74, ic.y() - r_ic * 0.74)
            luz = QRadialGradient(led, 7.0)
            luz.setColorAt(0.0, qcol(accent.name(), 210))
            luz.setColorAt(1.0, qcol(accent.name(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(luz))
            p.drawEllipse(led, 7.0, 7.0)
            p.setBrush(QBrush(self._mix_color(accent, QColor(_tc("#ffffff")), 0.45)))
            p.drawEllipse(led, 2.4, 2.4)

        p.setFont(_ftexto(7.3, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(_tc(C.WHITE), 235), 1))
        p.drawText(QRectF(1, 50 + y_shift, w - 2, 14), Qt.AlignmentFlag.AlignCenter, title)
        p.setFont(_espacado(_fx(6.0, QFont.Weight.DemiBold), 0.8))
        p.setPen(QPen(self._mix_color(accent, QColor(_tc("#ffffff")), 0.10), 1))
        p.drawText(QRectF(1, 64 + y_shift, w - 2, 12), Qt.AlignmentFlag.AlignCenter, status)

        if self._ripple_progress < 1.0:
            ripple_path = QPainterPath()
            ripple_path.addRoundedRect(rect, radius, radius)
            p.save()
            p.setClipPath(ripple_path)
            reach = math.hypot(w, h) * (0.12 + self._ripple_progress)
            ripple_alpha = round(80 * (1.0 - self._ripple_progress) ** 1.5)
            ripple = QRadialGradient(self._ripple_origin, reach)
            ripple.setColorAt(0.0, qcol(accent.name(), ripple_alpha))
            ripple.setColorAt(0.65, qcol(accent.name(), round(ripple_alpha * 0.32)))
            ripple.setColorAt(1.0, qcol(accent.name(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(ripple))
            p.drawEllipse(self._ripple_origin, reach, reach)
            p.restore()

        if self.hasFocus():
            p.setPen(QPen(qcol(accent.name(), 170), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, radius, radius)
        p.end()


def _translate_log_text(text: str) -> str:
    """Translate only the visual log text; command flow stays untouched."""
    if not isinstance(text, str):
        return str(text)
    # CLIENTE EM INGLES (26/09/2026): o caminho e o contrario — tudo para o
    # ingles. Ver core/interface_en.py.
    try:
        from core.idioma import em_ingles as _em_ingles_log  # noqa: PLC0415
        if _em_ingles_log():
            from core.interface_en import traduzir_linha  # noqa: PLC0415
            return traduzir_linha(text)
    except Exception:
        pass

    exact = {
        "SYS: JARVIS online.": "SIS: JARVIS ativo.",
        "SYS: Startup greeting sent. News briefing disabled.": "SIS: Saudação inicial enviada. Notícias desativadas.",
        "SYS: Briefing phase 2 (news) sent.": "SIS: Resumo de notícias enviado.",
        "SYS: Interrupted — listening...": "SIS: Interrompido — ouvindo...",
        "SYS: Shutdown requested.": "SIS: Desligamento solicitado.",
        "SYS: Proactive check-in.": "SIS: Verificação proativa.",
        "SYS: Phone connected via Remote Dashboard.": "SIS: Celular conectado pelo painel remoto.",
    }
    if text in exact:
        return exact[text]

    prefixes = (
        ("SYS:", "SIS:"),
        ("FILE:", "ARQ:"),
        ("ERR:", "ERRO:"),
        ("You:", "Você:"),
    )
    for old, new in prefixes:
        if text.startswith(old):
            return new + text[len(old):]
    return text

# setText "cru" da QLabel, guardado ANTES do interface_en embrulhar o metodo
# (cliente em ingles). O texto da conversa ja chega traduzido pelo
# `_translate_log_text`; passar cada letra digitada pelo tradutor so gastava.
_SET_TEXT_CRU = QLabel.setText


class LogWidget(QScrollArea):
    """A conversa em baloes (visual novo, 05/10/2026).

    Quem fala (JARVIS ou a voz escolhida) fica a esquerda, o dono a direita, e
    os avisos do sistema no meio, pequenos. Continua recebendo tudo pelo
    `append_log` (de qualquer linha, via sinal) e "digitando" aos poucos.
    """
    _sig = pyqtSignal(str)
    _MAX_MSGS = 150
    # "LOCAL:" = tempos e avisos do modo local (ouvir 0.8s | pensar 1.3s...): aviso, nao fala
    _SISTEMA = {"sis", "sys", "arq", "file", "err", "erro", "error", "aviso", "status", "info", "local"}

    _ESTILOS = {
        "ai": ("QLabel { color: #d8f8ff; background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
               " stop:0 #03222f, stop:1 #011620); border: 1px solid #0f4060;"
               " border-radius: 14px; border-top-left-radius: 4px; padding: 8px 12px; }"),
        "you": ("QLabel { color: #00121a; background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                " stop:0 #00d4ff, stop:1 #008fbf); border: none;"
                " border-radius: 14px; border-top-right-radius: 4px; padding: 8px 12px; }"),
        "sys": ("QLabel { color: #ffcc00; background: rgba(255,204,0,0.06);"
                " border: 1px solid rgba(255,204,0,0.16); border-radius: 10px; padding: 4px 10px; }"),
        "err": ("QLabel { color: #ff6680; background: rgba(255,51,85,0.08);"
                " border: 1px solid rgba(255,51,85,0.25); border-radius: 10px; padding: 4px 10px; }"),
        "file": ("QLabel { color: #00ff88; background: rgba(0,255,136,0.07);"
                 " border: 1px solid rgba(0,255,136,0.22); border-radius: 10px; padding: 4px 10px; }"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px 0 2px 0; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
        self.viewport().setStyleSheet("background: transparent;")
        self._corpo = QWidget()
        self._corpo.setObjectName("ChatCorpo")
        self._corpo.setStyleSheet("QWidget#ChatCorpo { background: transparent; }")
        self._lista = QVBoxLayout(self._corpo)
        self._lista.setContentsMargins(2, 4, 10, 4)
        self._lista.setSpacing(8)
        self._lista.addStretch(1)          # as mensagens ficam no pe, como num chat
        self.setWidget(self._corpo)
        self._linhas: list[QWidget] = []
        self._baloes: list[QLabel] = []
        self._queue: list[str] = []
        self._typing = False
        self._lbl: QLabel | None = None
        self._text = ""
        self._pos = 0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)
        self.verticalScrollBar().rangeChanged.connect(self._rolar_fim)

    def append_log(self, text: str):
        self._sig.emit(_translate_log_text(text))

    def _rolar_fim(self, *_):
        barra = self.verticalScrollBar()
        barra.setValue(barra.maximum())

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    @classmethod
    def _separar(cls, texto: str):
        """(tipo, quem, corpo). A fala da IA vem assinada com o nome da voz
        ("JARVIS: ...", "Leda: ..."), por isso qualquer nome curto que NAO
        seja etiqueta do sistema conta como a IA."""
        cab, sep, resto = texto.partition(":")
        chave = cab.strip().lower()
        if sep and chave in ("você", "voce", "you"):
            return "you", "", resto.strip()
        if sep and chave in cls._SISTEMA:
            corpo = resto.strip()
            if chave in ("arq", "file"):
                return "file", "", corpo
            if chave in ("err", "erro", "error") or "erro" in corpo.lower():
                return "err", "", corpo
            return "sys", "", corpo
        if sep and cab.strip() and len(cab.strip()) <= 24 and " " not in cab.strip()[:24].strip() \
                and not cab.strip().startswith(("http", "[")):
            return "ai", cab.strip(), resto.strip()
        tl = texto.lower()
        return ("err" if ("err" in tl or "erro" in tl) else "sys"), "", texto

    def _largura_max(self, tipo: str) -> int:
        base = max(160, self.viewport().width() - 14)
        return int(base * (0.86 if tipo in ("you", "ai") else 0.98))

    def _largura_certa(self, lbl: QLabel) -> None:
        tipo = str(lbl.property("_tipo_balao") or "sys")
        maximo = self._largura_max(tipo)
        texto = str(lbl.property("_texto_todo") or "")
        folga = 28 if tipo in ("you", "ai") else 24
        natural = max((lbl.fontMetrics().horizontalAdvance(l) for l in texto.splitlines() or [""]),
                      default=0) + folga
        if natural <= maximo:
            # cabe numa linha: sem quebra, na largura exata (altura certa)
            lbl.setWordWrap(False)
            lbl.setFixedWidth(max(40, natural))
        else:
            lbl.setWordWrap(True)
            lbl.setFixedWidth(maximo)

    def _novo_balao(self, tipo: str, quem: str, texto_todo: str = "") -> QLabel:
        linha = QWidget()
        linha.setStyleSheet("background: transparent;")
        col = QVBoxLayout(linha)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        if tipo == "ai" and quem:
            nome = QLabel()
            _SET_TEXT_CRU(nome, quem.upper())
            nome.setFont(_espacado(_fx(6.4, QFont.Weight.DemiBold), 1.8))
            nome.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; padding-left: 4px;")
            col.addWidget(nome)
        hl = QHBoxLayout()
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setStyleSheet(self._ESTILOS[tipo])
        lbl.setFont(_ftexto(9.5) if tipo in ("you", "ai") else _ftexto(8.0))
        lbl.setProperty("_tipo_balao", tipo)
        # A QLabel com quebra de linha escolhe sozinha uma largura estreita
        # (fica um balao fino e comprido). Aqui ela ja nasce na largura que o
        # texto inteiro pede, ate o maximo — e o texto vai aparecendo dentro.
        lbl.setProperty("_texto_todo", texto_todo)
        self._largura_certa(lbl)
        if tipo == "you":
            hl.addStretch(1)
            hl.addWidget(lbl)
        elif tipo == "ai":
            hl.addWidget(lbl)
            hl.addStretch(1)
        else:
            hl.addStretch(1)
            hl.addWidget(lbl)
            hl.addStretch(1)
        col.addLayout(hl)
        self._lista.addWidget(linha)
        self._linhas.append(linha)
        self._baloes.append(lbl)
        # A conversa nao cresce sem fim: as mais velhas saem.
        while len(self._linhas) > self._MAX_MSGS:
            velha = self._linhas.pop(0)
            self._baloes.pop(0)
            self._lista.removeWidget(velha)
            velha.deleteLater()
        return lbl

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        for lbl in self._baloes:
            try:
                self._largura_certa(lbl)
            except RuntimeError:
                pass

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        texto = self._queue.pop(0)
        tipo, quem, corpo = self._separar(texto)
        self._text = corpo or texto
        self._lbl = self._novo_balao(tipo, quem, self._text)
        self._pos = 0
        self._tmr.start(14)

    def _step(self):
        if self._lbl is None:
            self._tmr.stop()
            self._typing = False
            return
        # Com fila acumulada, escreve de uma vez para nao ficar para tras.
        passo = 3 if len(self._queue) < 3 else len(self._text)
        self._pos = min(len(self._text), self._pos + passo)
        try:
            _SET_TEXT_CRU(self._lbl, self._text[: self._pos])
        except RuntimeError:          # o balao ja saiu da conversa
            self._pos = len(self._text)
        if self._pos >= len(self._text):
            self._tmr.stop()
            QTimer.singleShot(20, self._next)


_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


_EXT_VIDEO_DROP = (".mp4", ".mov", ".mkv", ".avi", ".wmv", ".webm", ".m4v", ".3gp")


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)
    files_selected = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._thumb: QPixmap | None = None      # previa da imagem carregada
        self._thumb_cache: dict = {}            # previa ja no tamanho de cada card
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._hover_progress = 0.0
        self._press_progress = 0.0
        self._ripple_progress = 1.0
        self._ripple_origin = QPointF(54.0, 58.0)
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        target = 1.0 if (self._hovering or self._drag_over) else 0.0
        self._hover_progress += (target - self._hover_progress) * 0.24
        if abs(target - self._hover_progress) < 0.01:
            self._hover_progress = target
        self._press_progress = max(0.0, self._press_progress - 0.20)
        if self._ripple_progress < 1.0:
            self._ripple_progress = min(1.0, self._ripple_progress + 0.11)
        speed = 1.45 if self._drag_over else (0.95 if self._hovering else 0.55)
        self._dash_offset = (self._dash_offset + speed) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        # VARIOS ARQUIVOS DE UMA VEZ (10/09/2026): o Diego arrastou um video e
        # duas imagens juntos e so a primeira chegava — as outras sumiam sem
        # aviso. Agora vao todos; o video (se houver) fica sendo o principal.
        caminhos = []
        for u in e.mimeData().urls():
            c = u.toLocalFile()
            if c and Path(c).is_file() and c not in caminhos:
                caminhos.append(c)
        if len(caminhos) == 1:
            self._set_file(caminhos[0])
        elif caminhos:
            videos = [c for c in caminhos if Path(c).suffix.lower() in _EXT_VIDEO_DROP]
            ordem = videos + [c for c in caminhos if c not in videos]
            self._current_file = ordem[0]
            self._thumb = self._carregar_thumb(ordem[0])
            self._thumb_cache.clear()
            self._canvas.update()
            self.files_selected.emit(ordem)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_progress = 1.0
            self._ripple_progress = 0.0
            self._ripple_origin = e.position()
            self._canvas.update()
            self._browse()
        super().mousePressEvent(e)

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()
        super().leaveEvent(e)

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None
        self._thumb = None
        self._thumb_cache.clear()
        self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecionar arquivo para o JARVIS", str(Path.home()),
            "Todos os arquivos (*.*);;"
            "Imagens (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documentos (*.pdf *.docx *.txt *.md *.pptx);;"
            "Dados (*.csv *.xlsx *.json *.xml);;"
            "Código (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Áudio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Vídeo (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Arquivos compactados (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._thumb = self._carregar_thumb(path)
        self._thumb_cache.clear()
        self._canvas.update()
        self.file_selected.emit(path)

    def thumb_para(self, largura: int, altura: int):
        """Previa no tamanho pedido, redimensionada UMA VEZ SO.

        O quadro do arquivo e redesenhado 25 vezes por segundo pela animacao.
        Redimensionar a foto a cada desenho consumia processador sem parar e
        fazia a VOZ do JARVIS engasgar durante a conversa. Agora o resultado
        fica guardado e o desenho so copia.
        """
        if self._thumb is None or self._thumb.isNull():
            return None
        largura = max(1, int(largura))
        altura = max(1, int(altura))
        chave = (largura, altura)
        pronta = self._thumb_cache.get(chave)
        if pronta is None:
            pronta = self._thumb.scaled(
                largura, altura,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._thumb_cache[chave] = pronta
        return pronta

    @staticmethod
    def _carregar_thumb(path: str) -> QPixmap | None:
        """Previa da imagem, para o cliente ver o que carregou."""
        try:
            if _file_category(Path(path)) != "image":
                return None
            pix = QPixmap(path)
            if pix.isNull():
                return None
            # Guardamos ja pequena: nao adianta segurar a foto inteira em memoria.
            return pix.scaled(
                220, 220,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        except Exception:
            return None


class _DropCanvas(QWidget):
    """O botao ENVIAR ARQUIVO do dock (mesmo desenho dos outros, com borda
    tracejada que corre: e ali que se solta o arquivo)."""

    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        z = self._z
        W, H = self.width(), self.height()
        hover = z._hover_progress
        y_shift = -2.5 * hover + 1.5 * z._press_progress
        rect = QRectF(3.0, 3.0 + y_shift, W - 6.0, H - 6.0)
        radius = 16.0
        tem = bool(z._current_file)
        accent = QColor(_tema.cor_botao("fileUpload", C.GREEN if tem else C.PRI))
        forca = 1.0 if z._drag_over else hover

        if forca > 0.01:
            fundo = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            fundo.setColorAt(0.0, qcol(accent.name(), round(40 * forca)))
            fundo.setColorAt(1.0, qcol(accent.name(), round(8 * forca)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fundo))
            p.drawRoundedRect(rect, radius, radius)
        pen = QPen(qcol(accent.name(), round(60 + 130 * forca)), 1.1, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)

        ic = QPointF(W / 2.0, 28.0 + y_shift)
        r_ic = 18.0
        halo = QRadialGradient(ic, r_ic * 2.1)
        halo.setColorAt(0.0, qcol(accent.name(), round(50 + 70 * forca)))
        halo.setColorAt(1.0, qcol(accent.name(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(ic, r_ic * 2.1, r_ic * 2.1)
        disco = QLinearGradient(QPointF(ic.x(), ic.y() - r_ic), QPointF(ic.x(), ic.y() + r_ic))
        disco.setColorAt(0.0, qcol(_tc("#042a3a")))
        disco.setColorAt(1.0, qcol(_tc("#010d14")))
        p.setBrush(QBrush(disco))
        p.setPen(QPen(qcol(accent.name(), round(120 + 100 * forca)), 1.2))
        p.drawEllipse(ic, r_ic, r_ic)

        if tem:
            path = Path(z._current_file)
            cat = _file_category(path)
            icone, icone_cor = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
            try:
                tamanho = _fmt_size(path.stat().st_size)
            except OSError:
                tamanho = "movido"
            miniatura = z.thumb_para(r_ic * 2 - 4, r_ic * 2 - 4)
            if miniatura is not None:
                p.save()
                redondo = QPainterPath()
                redondo.addEllipse(ic, r_ic - 1.5, r_ic - 1.5)
                p.setClipPath(redondo)
                p.drawPixmap(int(ic.x() - miniatura.width() / 2), int(ic.y() - miniatura.height() / 2),
                             miniatura)
                p.restore()
            else:
                p.setFont(QFont("Segoe UI Emoji", 13) if _OS == "Windows" else QFont("Arial", 13))
                p.setPen(QPen(qcol(icone_cor), 1))
                p.drawText(QRectF(ic.x() - r_ic, ic.y() - r_ic, r_ic * 2, r_ic * 2),
                           Qt.AlignmentFlag.AlignCenter, icone)
            nome = path.name if len(path.name) <= 12 else path.name[:10] + "…"
            titulo = nome
            status = f"{path.suffix.upper().lstrip('.') or 'ARQ'} · {tamanho}"
            p.setFont(_fx(9, QFont.Weight.Bold))
            p.setPen(QPen(qcol(_tc(C.RED), 210), 1))
            p.drawText(QRectF(W - 22, 3, 18, 18), Qt.AlignmentFlag.AlignCenter, "✕")
        else:
            fam = _system_icon_font_family()
            fluente = fam != "Segoe UI Symbol"
            glyph = ("" if z._drag_over else "") if fluente else "⇪"
            p.setFont(QFont(fam, 15))
            p.setPen(QPen(ControlCard._mix_color(accent, QColor(_tc("#ffffff")), 0.30), 1))
            p.drawText(QRectF(ic.x() - r_ic, ic.y() - r_ic, r_ic * 2, r_ic * 2),
                       Qt.AlignmentFlag.AlignCenter, glyph)
            titulo = "SOLTE" if z._drag_over else "ARQUIVO"
            status = "PARA ENVIAR" if z._drag_over else "ARRASTE AQUI"

        p.setFont(_ftexto(7.3, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(_tc(C.WHITE), 235), 1))
        p.drawText(QRectF(1, 50 + y_shift, W - 2, 14), Qt.AlignmentFlag.AlignCenter, titulo)
        p.setFont(_espacado(_fx(6.0, QFont.Weight.DemiBold), 0.6))
        p.setPen(QPen(ControlCard._mix_color(accent, QColor(_tc("#ffffff")), 0.10), 1))
        p.drawText(QRectF(1, 64 + y_shift, W - 2, 12), Qt.AlignmentFlag.AlignCenter, status)

        if z._ripple_progress < 1.0:
            clip = QPainterPath()
            clip.addRoundedRect(rect, radius, radius)
            p.save()
            p.setClipPath(clip)
            reach = math.hypot(W, H) * (0.12 + z._ripple_progress)
            alpha = round(65 * (1.0 - z._ripple_progress) ** 1.5)
            ripple = QRadialGradient(z._ripple_origin, reach)
            ripple.setColorAt(0.0, qcol(accent.name(), alpha))
            ripple.setColorAt(0.68, qcol(accent.name(), round(alpha * 0.28)))
            ripple.setColorAt(1.0, qcol(accent.name(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(ripple))
            p.drawEllipse(z._ripple_origin, reach, reach)
            p.restore()
        p.end()

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file:
            # Com arquivo carregado, o unico clique que faz algo e o do "x",
            # que descarta. Antes, clicar em qualquer ponto reabria a janela de
            # escolha e o cliente tinha de selecionar o arquivo de novo.
            if e.pos().x() > self.width() - 26 and e.pos().y() < 28:
                z.clear_file()
            return
        z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  ENTRADA VISUAL")
        title.setFont(_fx(7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(_fx(8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(_fx(font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  CONFIGURAÇÃO", 13, True))
        layout.addWidget(_lbl("Você pode começar sem chave nenhuma.", 9, color=C.PRI_DIM))
        # O OPENROUTER SAIU DAQUI (05/09/2026, ordem do Diego). O cérebro do
        # JARVIS agora é a Gemini do cliente e, quando ela não dá, o Codex da
        # nossa VPS — que não tem cota. Pedir uma chave que não é mais usada só
        # atrasava o cliente na primeira tela. A do Gemini ficou, e virou
        # OPCIONAL: sem ela o JARVIS pensa, vê e fala do mesmo jeito, pela VPS.
        layout.addWidget(_lbl("A chave do Gemini é opcional: deixa o JARVIS mais rápido.", 8,
                              color=C.TEXT_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("CHAVE API GEMINI", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(_fx(10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(8)

        # (o campo do OpenRouter foi removido em 05/09/2026 — ver o comentário
        #  no topo desta tela)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("SISTEMA OPERACIONAL", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Detectado automaticamente: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(_fx(9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INICIAR SISTEMAS")
        init_btn.setFont(_fx(10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        # CAMPO VAZIO NAO TRAVA MAIS A ABERTURA (05/09/2026). Antes o botao nao
        # fazia nada sem a chave do Gemini — e hoje o JARVIS funciona inteiro
        # sem chave nenhuma do cliente (pensa, ve e fala pela nossa VPS). Quem
        # tiver a chave ganha o degrau rapido; quem nao tiver, entra assim mesmo.
        key = self._key_input.text().strip()
        self.done.emit(key, "", self._sel_os)


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(_fx(fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  ACESSO REMOTO", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Aponte a câmera do celular para conectar", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Ou digite manualmente:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(_fx(8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(_fx(28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(_fx(8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NOVA CHAVE")
        new_btn.setFixedHeight(32)
        new_btn.setFont(_fx(8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("FECHAR")
        close_btn.setFixedHeight(32)
        close_btn.setFont(_fx(8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(_fx(8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(_fx(7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Chave expira em  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONECTADO")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(_fx(54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Celular conectado — JARVIS pronto")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class WhatsAppQrOverlay(QWidget):
    """Janela flutuante do WhatsApp: mostra QR Code e status conectado."""

    closed = pyqtSignal()
    _OW, _OH = 430, 520

    def __init__(self, qr_path: str, parent=None):
        super().__init__(parent)
        self._qr_path = str(qr_path)
        self._status_url = "http://127.0.0.1:8765/status"
        self._qr_url = "http://127.0.0.1:8765/qr"
        self._reset_url = "http://127.0.0.1:8765/reset"
        self._connected_once = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            WhatsAppQrOverlay {{
                background: rgba(0, 8, 12, 0.97);
                border: 1px solid {C.GREEN};
                border-radius: 14px;
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(8)

        title = QLabel("◉  CONECTAR WHATSAPP")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(_fx(13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        lay.addWidget(title)

        info = QLabel(
            "Abra o WhatsApp no celular → Aparelhos conectados → Conectar aparelho.\n"
            "Escaneie este QR Code."
        )
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setWordWrap(True)
        info.setFont(_fx(8))
        info.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(info)

        self._qr = QLabel("Gerando QR Code...\nAguarde até 30 segundos.")
        self._qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr.setFixedSize(330, 330)
        self._qr.setStyleSheet("background: white; color: #111; border-radius: 8px; padding: 6px;")
        self._qr.setFont(_fx(11, QFont.Weight.Bold))
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._qr)
        row.addStretch()
        lay.addLayout(row)

        self._status = QLabel("Status: iniciando ponte WhatsApp...")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setFont(_fx(8))
        self._status.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._refresh_btn = QPushButton("ATUALIZAR")
        self._refresh_btn.setFixedHeight(32)
        self._refresh_btn.setFont(_fx(8, QFont.Weight.Bold))
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setStyleSheet(f"""
            QPushButton {{ background: #00140a; color: {C.GREEN}; border: 1px solid {C.GREEN_D}; border-radius: 5px; }}
            QPushButton:hover {{ background: #001f10; border: 1px solid {C.GREEN}; }}
        """)
        self._refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(self._refresh_btn)

        reset_btn = QPushButton("GERAR QR NOVO")
        reset_btn.setFixedHeight(32)
        reset_btn.setFont(_fx(8, QFont.Weight.Bold))
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setStyleSheet(f"""
            QPushButton {{ background: #201200; color: {C.ACC2}; border: 1px solid #805000; border-radius: 5px; }}
            QPushButton:hover {{ background: #2a1800; border: 1px solid {C.ACC2}; }}
        """)
        reset_btn.clicked.connect(self._reset_qr)
        btn_row.addWidget(reset_btn)

        close_btn = QPushButton("FECHAR")
        close_btn.setFixedHeight(32)
        close_btn.setFont(_fx(8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 5px; }}
            QPushButton:hover {{ color: {C.GREEN}; border: 1px solid {C.GREEN}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1700)
        self._refresh()

    def _read_bridge_status(self) -> dict:
        try:
            import json as _json
            import urllib.request as _urlreq
            with _urlreq.urlopen(self._status_url, timeout=2.0) as r:
                raw = r.read().decode("utf-8", errors="ignore")
            return _json.loads(raw or "{}")
        except Exception:
            try:
                import json as _json
                p = Path(self._qr_path).parent / "status.json"
                if p.exists():
                    return _json.loads(p.read_text(encoding="utf-8", errors="ignore") or "{}")
            except Exception:
                pass
            return {}

    def _load_qr_pixmap(self) -> QPixmap:
        """Carrega o QR primeiro pelo servidor local e depois pelo arquivo.

        Em alguns PCs o arquivo demora a aparecer no disco/OneDrive, mas o
        endpoint /qr já responde. Esse fallback evita a tela ficar presa em
        "PONTE WHATSAPP INICIANDO" mesmo com QR gerado.
        """
        try:
            import urllib.request as _urlreq
            with _urlreq.urlopen(self._qr_url + "?ts=" + str(int(time.time() * 1000)), timeout=2.0) as r:
                raw = r.read()
            if raw:
                px = QPixmap()
                if px.loadFromData(raw):
                    return px
        except Exception:
            pass
        try:
            qr_path = Path(self._qr_path)
            if qr_path.exists():
                return QPixmap(str(qr_path))
        except Exception:
            pass
        return QPixmap()

    def _reset_qr(self):
        """Pede QR novo quando a sessão antiga ficou presa/desatualizada."""
        try:
            import urllib.request as _urlreq
            req = _urlreq.Request(self._reset_url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
            _urlreq.urlopen(req, timeout=2.0).read()
            self._status.setText("Status: sessão limpa. Gerando QR Code novo...")
            self._qr.setText("GERANDO\nQR CODE\nNOVO")
        except Exception:
            self._status.setText("Status: não consegui pedir QR novo agora. Tente fechar e abrir a conexão novamente.")
        QTimer.singleShot(1200, self._refresh)

    def _show_connected(self, contacts: int = 0):
        self._connected_once = True
        self._qr.clear()
        self._qr.setStyleSheet(
            "background: #001a0d; color: #00ff88; border: 2px solid #00ff88; "
            "border-radius: 8px; padding: 6px;"
        )
        self._qr.setFont(_fx(34, QFont.Weight.Bold))
        self._qr.setText("✓\nWHATSAPP\nCONECTADO")
        extra = f" | contatos carregados: {contacts}" if contacts else ""
        self._status.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        self._status.setText(f"Status: conectado com sucesso{extra}. Já pode fechar esta janela.")

    def _refresh(self):
        try:
            data = self._read_bridge_status()
            if data.get("connected"):
                self._show_connected(int(data.get("contacts") or 0))
                return

            # Se o bridge disser que há QR, tenta buscar via HTTP. Se o arquivo já existir,
            # também carrega direto do disco.
            px = self._load_qr_pixmap() if data.get("has_qr") or Path(self._qr_path).exists() else QPixmap()
            if not px.isNull():
                self._qr.setStyleSheet("background: white; color: #111; border-radius: 8px; padding: 6px;")
                self._qr.setFont(_fx(9))
                self._qr.setPixmap(px.scaled(
                    318, 318,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                self._status.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
                self._status.setText("Status: escaneie o QR Code para conectar.")
                return

            self._qr.clear()
            last_event = str(data.get("last_event") or "")
            msg = str(data.get("message") or "Gerando QR Code. Aguarde...")
            error = data.get("error")

            if not data:
                self._qr.setStyleSheet(
                    "background: #180000; color: #ff6680; border: 2px solid #ff3355; "
                    "border-radius: 8px; padding: 6px;"
                )
                self._qr.setFont(_fx(15, QFont.Weight.Bold))
                self._qr.setText("PONTE\nWHATSAPP\nINICIANDO")
                self._status.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
                self._status.setText("Status: iniciando ponte local. Aguarde alguns segundos.")
            elif error or "timeout" in last_event or last_event == "error":
                self._qr.setStyleSheet(
                    "background: #1a0d00; color: #ffcc00; border: 2px solid #ffcc00; "
                    "border-radius: 8px; padding: 6px;"
                )
                self._qr.setFont(_fx(13, QFont.Weight.Bold))
                self._qr.setText("QR CODE\nAINDA NÃO\nGERADO")
                self._status.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
                detalhe = f" | erro: {error}" if error else ""
                self._status.setText(f"Status: {msg}{detalhe} | veja whatsapp_bridge\\bridge.log se continuar.")
            else:
                self._qr.setStyleSheet("background: white; color: #111; border-radius: 8px; padding: 6px;")
                self._qr.setFont(_fx(11, QFont.Weight.Bold))
                self._qr.setText("Gerando QR Code...\nAguarde até 30 segundos.")
                self._status.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
                self._status.setText(f"Status: {msg}")
        except Exception as e:
            self._status.setText(f"Erro ao carregar QR/status: {e}")

    def _do_close(self):
        try:
            self._timer.stop()
        except Exception:
            pass
        self.hide()
        self.closed.emit()


class AvisoComputador(QWidget):
    """A faixa "JARVIS ESTA USANDO O COMPUTADOR", enquanto ele mexe sozinho.

    Por que existe: quando o JARVIS assume o mouse, o dono precisa saber que
    aquilo NAO e travamento nem virus, e precisa NAO encostar no mouse (um
    clique dele no meio do caminho manda o proximo passo para o lugar errado).
    A voz avisa, mas voz o dono esquece; a faixa fica na tela o tempo todo.

    DUAS EXIGENCIAS QUE MANDAM NO DESENHO:

    1. Nao pode aparecer na FOTO. A foto da tela e o que o Codex olha para
       decidir o proximo movimento; um aviso gigante em cima dela atrapalha e
       ainda ocupa espaco de coisa util. Resolvido com
       `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`, do proprio Windows:
       a janela continua visivel para a pessoa e some de qualquer captura.
       Se o Windows recusar (versao antiga), a faixa NAO e mostrada — melhor
       sem aviso do que com o Codex enxergando o aviso.
    2. Nao pode roubar clique. E `WindowTransparentForInput`: o mouse atravessa.
    """

    _ALTURA = 44
    WDA_EXCLUDEFROMCAPTURE = 0x00000011

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        caixa = QFrame(self)
        caixa.setObjectName("faixa")
        caixa.setStyleSheet(f"""
            QFrame#faixa {{
                background: rgba(5, 9, 16, 0.95);
                border: 2px solid {C.PRI};
                border-radius: 12px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(caixa)

        interna = QHBoxLayout(caixa)
        interna.setContentsMargins(16, 6, 16, 6)
        self._texto = QLabel("JARVIS ESTÁ USANDO O COMPUTADOR  ·  ESC interrompe  ·  "
                             "não mexa no mouse nem no teclado")
        self._texto.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._texto.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._texto.setAlignment(Qt.AlignmentFlag.AlignCenter)
        interna.addWidget(self._texto)

        self._ok_para_mostrar = None      # None = ainda nao testei o Windows

    def _fora_da_foto(self) -> bool:
        """Pede ao Windows para esconder esta janela de qualquer captura."""
        try:
            return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
                int(self.winId()), self.WDA_EXCLUDEFROMCAPTURE))
        except Exception:
            return False

    def mostrar(self, texto: str = "") -> bool:
        if texto:
            self._texto.setText(texto)
        try:
            tela = QApplication.primaryScreen().availableGeometry()
        except Exception:
            return False
        # Ficou mais larga em 04/09/2026 porque o texto cresceu (entrou o
        # "aperte ESC para parar"): com 820 px a frase saía cortada no meio.
        largura = max(420, min(1000, int(tela.width() * 0.62)))
        self.setGeometry(tela.x() + (tela.width() - largura) // 2,
                         tela.y() + 12, largura, self._ALTURA)
        self.show()
        self.raise_()
        if self._ok_para_mostrar is None:
            self._ok_para_mostrar = self._fora_da_foto()
        if not self._ok_para_mostrar:
            # O Windows nao sabe esconder da captura: some, para nao poluir a
            # foto que o Codex analisa. A voz continua avisando.
            self.hide()
            return False
        return True


class _SeletorCor(QWidget):
    """Quadro de cor (saturacao x brilho) + faixa de matiz.

    Passar o mouse emite `passou(cor)` — a cor EXATA debaixo do ponteiro;
    clicar (ou arrastar e soltar) emite `escolheu(cor)`."""

    passou = pyqtSignal(QColor)
    escolheu = pyqtSignal(QColor)
    saiu = pyqtSignal()

    _QW, _QH, _FAIXA, _VAO = 340, 176, 18, 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFixedSize(self._QW, self._QH + self._VAO + self._FAIXA)
        self._h, self._s, self._v = 190.0, 1.0, 1.0
        self._mouse = None          # ponto do ponteiro (para a mira)
        self._arrastando = None     # "quadro" | "faixa" | None
        self._img_quadro = None
        self._h_img = None

    def set_cor(self, cor: QColor) -> None:
        h, s, v, _a = cor.getHsvF()
        if h is not None and h >= 0:
            self._h = h * 360.0
        self._s, self._v = float(s), float(v)
        self.update()

    def _cor(self) -> QColor:
        return QColor.fromHsvF((self._h % 360.0) / 360.0, self._s, self._v)

    def _de_ponto(self, pos):
        """(zona, cor) do ponto, sem mexer no estado."""
        x = max(0.0, min(float(self._QW - 1), pos.x()))
        y = pos.y()
        if y <= self._QH + self._VAO / 2:
            y = max(0.0, min(float(self._QH - 1), y))
            s = x / (self._QW - 1)
            v = 1.0 - y / (self._QH - 1)
            return "quadro", QColor.fromHsvF((self._h % 360.0) / 360.0, s, v), (s, v)
        h = 359.0 * x / (self._QW - 1)
        return "faixa", QColor.fromHsvF(h / 360.0, self._s, self._v), h

    def _montar_quadro(self):
        img = QImage(self._QW, self._QH, QImage.Format.Format_RGB32)
        p = QPainter(img)
        p.fillRect(img.rect(), QColor.fromHsvF((self._h % 360.0) / 360.0, 1.0, 1.0))
        g1 = QLinearGradient(0, 0, self._QW, 0)
        g1.setColorAt(0.0, QColor(255, 255, 255, 255))
        g1.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(img.rect(), QBrush(g1))
        g2 = QLinearGradient(0, 0, 0, self._QH)
        g2.setColorAt(0.0, QColor(0, 0, 0, 0))
        g2.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(img.rect(), QBrush(g2))
        p.end()
        self._img_quadro = img
        self._h_img = self._h

    def paintEvent(self, _):
        if self._img_quadro is None or self._h_img != self._h:
            self._montar_quadro()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        caminho = QPainterPath()
        caminho.addRoundedRect(QRectF(0, 0, self._QW, self._QH), 8, 8)
        p.setClipPath(caminho)
        p.drawImage(0, 0, self._img_quadro)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, self._QW - 1, self._QH - 1), 8, 8)
        # faixa de matiz
        y0 = self._QH + self._VAO
        g = QLinearGradient(0, 0, self._QW, 0)
        for i in range(13):
            g.setColorAt(i / 12.0, QColor.fromHsvF((i / 12.0) % 1.0 if i < 12 else 0.9999, 1.0, 1.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(0, y0, self._QW, self._FAIXA), 6, 6)
        # marcas da cor escolhida
        sx = self._s * (self._QW - 1)
        sy = (1.0 - self._v) * (self._QH - 1)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 200), 3))
        p.drawEllipse(QPointF(sx, sy), 7, 7)
        p.setPen(QPen(QColor(255, 255, 255), 1.6))
        p.drawEllipse(QPointF(sx, sy), 7, 7)
        hx = (self._h % 360.0) / 359.0 * (self._QW - 1)
        p.setPen(QPen(QColor(0, 0, 0, 200), 3))
        p.drawRoundedRect(QRectF(hx - 3, y0 - 2, 6, self._FAIXA + 4), 2, 2)
        p.setPen(QPen(QColor(255, 255, 255), 1.4))
        p.drawRoundedRect(QRectF(hx - 3, y0 - 2, 6, self._FAIXA + 4), 2, 2)
        # mira do ponteiro: um circulo com a cor exata debaixo dele
        if self._mouse is not None and self._arrastando is None:
            zona, cor, _v = self._de_ponto(self._mouse)
            mx = max(0.0, min(float(self._QW), self._mouse.x()))
            my = self._mouse.y() if zona == "quadro" else y0 + self._FAIXA / 2
            p.setPen(QPen(QColor(255, 255, 255, 230), 2))
            p.setBrush(QBrush(cor))
            p.drawEllipse(QPointF(mx, my), 9, 9)
        p.end()

    def _aplicar_ponto(self, pos, zona):
        _z, cor, val = self._de_ponto(pos)
        if zona == "quadro" and _z == "quadro":
            self._s, self._v = val
        elif zona == "faixa":
            if _z == "faixa":
                self._h = val
            else:
                self._h = 359.0 * max(0.0, min(float(self._QW - 1), pos.x())) / (self._QW - 1)
        self.update()
        return self._cor()

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        zona, _c, _v = self._de_ponto(ev.position())
        self._arrastando = zona
        self.passou.emit(self._aplicar_ponto(ev.position(), zona))

    def mouseMoveEvent(self, ev):
        self._mouse = ev.position()
        if self._arrastando:
            self.passou.emit(self._aplicar_ponto(ev.position(), self._arrastando))
            return
        _z, cor, _v = self._de_ponto(ev.position())
        self.passou.emit(cor)
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton or not self._arrastando:
            return
        cor = self._aplicar_ponto(ev.position(), self._arrastando)
        self._arrastando = None
        self.escolheu.emit(cor)

    def leaveEvent(self, ev):
        self._mouse = None
        self.update()
        self.saiu.emit()
        super().leaveEvent(ev)


class _PreviaTema(QWidget):
    """Miniatura do JARVIS com as cores de agora — ou com a cor debaixo do
    mouse, antes de clicar (`prever`)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(250, 132)
        self._teste = (None, None)
        self._pecas = {}
        for nome in ("anel_interno", "energia"):
            px = QPixmap(str(_base_dir() / "assets" / "nucleo" / f"{nome}.png"))
            self._pecas[nome] = None if px.isNull() else px.scaled(
                76, 76, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def prever(self, chave, valor) -> None:
        self._teste = (chave, valor)
        self.update()

    @staticmethod
    def _tingir(px: QPixmap, cor: str) -> QPixmap:
        img = px.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
        img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        p.fillRect(img.rect(), QColor(cor))
        p.end()
        return QPixmap.fromImage(img)

    def paintEvent(self, _):
        chave, valor = self._teste
        guardado = None
        if chave:
            guardado = (dict(_tema._escolhas), _tema._versao)
            _tema.definir(chave, valor, gravar=False)
        try:
            self._desenhar()
        finally:
            if guardado is not None:
                _tema._escolhas.clear()
                _tema._escolhas.update(guardado[0])
                _tema._cache.clear()
                _tema._versao = guardado[1]

    def _desenhar(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(_tc(C.BG))))
        p.drawRoundedRect(QRectF(0, 0, W, H), 8, 8)
        # o nucleo
        cx, cy = 50.0, H / 2.0
        luz = QRadialGradient(QPointF(cx, cy), 48)
        luz.setColorAt(0.0, qcol(_tc("#00263a")))
        luz.setColorAt(1.0, qcol(_tc(C.BG), 0))
        p.setBrush(QBrush(luz))
        p.drawEllipse(QPointF(cx, cy), 48, 48)
        cor_n = _tema.nucleo("ocioso")
        for nome in ("anel_interno", "energia"):
            px = self._pecas.get(nome)
            if px is None:
                continue
            if _tema.nucleo_tingido():
                px = self._tingir(px, cor_n)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.drawPixmap(QPointF(cx - px.width() / 2, cy - px.height() / 2), px)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        # o painel com texto
        painel = QRectF(104, 12, W - 114, 56)
        p.setBrush(QBrush(QColor(_tc(C.PANEL))))
        p.setPen(QPen(QColor(_tc(C.BORDER_B)), 1))
        p.drawRoundedRect(painel, 7, 7)
        p.setFont(_fx(8.2, QFont.Weight.Bold))
        p.setPen(QPen(QColor(_tc(C.PRI))))
        p.drawText(QRectF(painel.x() + 8, painel.y() + 5, painel.width() - 10, 16),
                   Qt.AlignmentFlag.AlignLeft, "JARVIS")
        p.setFont(_ftexto(7.6))
        p.setPen(QPen(QColor(_tc(C.TEXT))))
        p.drawText(QRectF(painel.x() + 8, painel.y() + 21, painel.width() - 10, 14),
                   Qt.AlignmentFlag.AlignLeft, "Pois não, senhor.")
        p.setPen(QPen(QColor(_tc(C.TEXT_DIM))))
        p.drawText(QRectF(painel.x() + 8, painel.y() + 36, painel.width() - 10, 14),
                   Qt.AlignmentFlag.AlignLeft, "Tudo pronto por aqui.")
        # tres botoes da barra
        for i, (nome, cor0) in enumerate((("mute", C.GREEN), ("cowork", C.ACC),
                                          ("computer_use", C.PRI))):
            c = QColor(_tema.cor_botao(nome, cor0) or cor0)
            bx = 118 + i * 42
            by = 98
            halo = QRadialGradient(QPointF(bx, by), 18)
            halo.setColorAt(0.0, qcol(c.name(), 90))
            halo.setColorAt(1.0, qcol(c.name(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(halo))
            p.drawEllipse(QPointF(bx, by), 18, 18)
            p.setBrush(QBrush(QColor(_tc("#010d14"))))
            p.setPen(QPen(c, 1.4))
            p.drawEllipse(QPointF(bx, by), 11, 11)
            p.setBrush(QBrush(c))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(bx, by), 4, 4)
        p.end()


class MainWindow(QMainWindow):
    _log_sig     = pyqtSignal(str)
    _state_sig   = pyqtSignal(str)
    _content_sig = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig = pyqtSignal()          # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)   # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)   # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)  # live camera frame → HUD area
    _vision_panel_sig = pyqtSignal(bool, str)   # Vision Control panel active/status
    _vision_frame_sig = pyqtSignal(bytes, str)  # Vision Control frame/status → left panel
    _open_whatsapp_sig = pyqtSignal()       # thread-safe WhatsApp QR overlay
    _aviso_pc_sig     = pyqtSignal(bool, str)  # faixa "estou usando o computador"
    _cowork_sig       = pyqtSignal(object)     # estado do Cowork (vem da thread dele)
    _cowork_pasta_sig = pyqtSignal()           # abrir a escolha da pasta do projeto

    def __init__(self, face_path: str):
        # ANTES DE MONTAR QUALQUER PAINEL. O cartao "so com o nome" e criado
        # dentro de `_build_right_panel`, que roda no meio deste __init__ e ja
        # chama `_style_nome_btn()`. Declarado mais abaixo, junto com o resto do
        # estado, o programa nao abria: AttributeError no primeiro estilo.
        #
        # E SEMPRE COMECA DESLIGADO, tambem de proposito. Ele chegou a ser
        # lembrado entre uma abertura e outra, mas um portao de microfone que
        # volta ligado sozinho e uma armadilha: o dono abre, fala, nao e
        # respondido, e a conclusao natural e "o JARVIS quebrou" — nao
        # "esqueci que deixei ligado ontem". Ligar custa um clique; descobrir
        # por que ele emudeceu custa a tarde.
        self._so_com_nome = False
        # Avisado pelo main.py quando o valor muda (para gravar e aplicar).
        self.on_so_nome_changed = None
        self._nome_da_ia = "JARVIS"
        super().__init__()
        self._face_path = face_path
        self.setWindowTitle("JARVIS")
        icon_path = _app_icon_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(_MIN_W, _MIN_H)
        screen = QApplication.primaryScreen().availableGeometry()
        target_w = min(_DEFAULT_W, max(_MIN_W, int(screen.width() * 0.90)))
        target_h = min(_DEFAULT_H, max(_MIN_H, int(screen.height() * 0.90)))
        self.resize(target_w, target_h)
        self.move(
            screen.x() + (screen.width()  - target_w) // 2,
            screen.y() + (screen.height() - target_h) // 2,
        )

        self.on_text_command     = None
        self.on_remote_clicked   = None   # callable: () -> (url, key) | None
        self.on_whatsapp_clicked = None   # callable: () -> None — open/connect WhatsApp QR
        self.on_interrupt        = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_cowork_acao      = None   # callable: (acao, valor) — botoes do Cowork
        self.on_computer_use        = None   # callable: () — botao COMPUTER USE do dock
        self.on_computer_use_estado = None   # callable: () -> "livre" | "esperando" | "trabalhando"
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._whatsapp_overlay: WhatsAppQrOverlay | None = None
        self._whatsapp_connecting = False

        # ESTRUTURA NOVA (05/10/2026): topo, palco (esfera + dock) e a coluna
        # da direita (sistema + conversa). Fundo liso na cor da janela: a luz
        # atras da esfera some nessa mesma cor, sem emenda.
        central = QWidget()
        central.setObjectName("JarvisFundo")
        central.setStyleSheet(f"QWidget#JarvisFundo {{ background: {C.BG}; }}")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        corpo = QHBoxLayout()
        corpo.setContentsMargins(14, 4, 14, 14)
        corpo.setSpacing(14)
        root.addLayout(corpo, 1)

        shell = QWidget()
        shell.setObjectName("JarvisShell")
        shell.setStyleSheet("QWidget#JarvisShell { background: transparent; }")
        shell_lay = QVBoxLayout(shell)
        shell_lay.setContentsMargins(0, 0, 0, 0)
        shell_lay.setSpacing(0)
        corpo.addWidget(shell, stretch=1)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()

        # Keep the original animated HUD alive at all times.  The real camera
        # feed is only a visual overlay and reuses the existing stream methods.
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.setObjectName("HudStage")
        self._hud_cam_stack.setStyleSheet(f"QStackedWidget#HudStage {{ background: {C.BG}; }}")
        self._hud_cam_stack.addWidget(self.hud)

        self._cam_cont = QWidget(self._hud_cam_stack)
        self._cam_cont.setObjectName("LiveCameraPanel")
        self._cam_cont.setStyleSheet(f"""
            QWidget#LiveCameraPanel {{
                background: rgba(0, 8, 13, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)
        _cam_v = QVBoxLayout(self._cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(10, 7, 8, 5)
        _cam_title = QLabel("WEBCAM")
        _cam_title.setFont(_fx(8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        self._cam_status_lbl = QLabel("●  AO VIVO")
        self._cam_status_lbl.setFont(_fx(7, QFont.Weight.Bold))
        self._cam_status_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        _cam_hdr.addWidget(self._cam_status_lbl)
        _cam_x = QPushButton("✕  FECHAR")
        _cam_x.setFont(_fx(7, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 5px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setText("AGUARDANDO CÂMERA…")
        self._cam_live_lbl.setFont(_fx(8, QFont.Weight.Bold))
        self._cam_live_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: #000308; border: none; margin: 0 7px 7px 7px;"
        )
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)
        self._cam_cont.hide()
        self._cam_stream_active = False

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: transparent;
                height: 8px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        shell_lay.addWidget(self._center_split, stretch=1)

        self._right_panel = self._build_dock()
        shell_lay.addWidget(self._right_panel, stretch=0)
        self._left_panel = self._build_coluna_lateral()
        corpo.addWidget(self._left_panel, stretch=0)

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metrik güncelleme timer'ı
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._vision_panel_sig.connect(self._set_vision_control_panel)
        self._vision_frame_sig.connect(self._on_vision_control_frame)
        self._open_whatsapp_sig.connect(self._show_whatsapp_overlay)
        self._aviso_pc_sig.connect(self._aplicar_aviso_pc)
        self._cowork_sig.connect(self._aplicar_cowork_estado)
        self._cu_tmr = QTimer(self)
        self._cu_tmr.timeout.connect(self._atualizar_computer_use)
        self._cu_tmr.start(400)
        self._cowork_pasta_sig.connect(self._cowork_escolher_pasta)
        self._aviso_pc = None
        self._cam_stop = threading.Event()
        self._latest_cam_frame_bytes: bytes | None = None
        self._latest_cam_frame_ts: float = 0.0
        self._latest_vision_frame_bytes: bytes | None = None
        self._latest_vision_frame_ts: float = 0.0
        self._latest_frame_lock = threading.Lock()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)
        # Trocar a chave de IA depois da instalacao. Antes, a tela de
        # configuracao so aparecia na primeira abertura ou quando a chave era
        # recusada: quem precisasse trocar a chave (limite estourado, chave
        # nova) simplesmente nao tinha por onde.
        sc_cfg = QShortcut(QKeySequence("F2"), self)
        sc_cfg.activated.connect(self._show_setup)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        self._position_camera_panels()

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        self._cam_stream_active = bool(start)
        if start:
            self._cam_live_lbl.setText("AGUARDANDO CÂMERA…")
            self._cam_cont.show()
            self._cam_cont.raise_()
        else:
            self._cam_live_lbl.clear()
            self._cam_cont.hide()
        self._style_webcam_btn()
        self._position_camera_panels()
        QTimer.singleShot(0, self._position_camera_panels)

    def _toggle_camera_stream(self) -> None:
        """Visual control for the already existing real webcam stream."""
        if self._cam_stream_active:
            self.stop_camera_stream()
        else:
            self.start_camera_stream()

    def _style_webcam_btn(self) -> None:
        if not hasattr(self, "_webcam_btn"):
            return
        if self._cam_stream_active:
            text, color, bg = "🎥\nWEBCAM\nATIVA", C.GREEN, "#00140a"
        else:
            text, color, bg = "🎥\nWEBCAM\nINATIVA", C.PRI, C.PANEL
        self._webcam_btn.setText(text)
        if isinstance(self._webcam_btn, ControlCard):
            self._webcam_btn.set_card_theme(color, bg)
        else:
            self._webcam_btn.setStyleSheet(self._control_card_style(color, bg))

    def _on_cam_frame(self, data: bytes) -> None:
        try:
            with self._latest_frame_lock:
                self._latest_cam_frame_bytes = bytes(data)
                self._latest_cam_frame_ts = time.time()
        except Exception:
            pass
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    def get_latest_camera_frame_bytes(self, max_age: float = 4.0) -> bytes | None:
        """Return the newest live webcam JPEG already shown in the UI.

        Vision analysis uses this before trying to open the webcam again. This
        prevents camera conflicts when Vision Control already owns the device.
        """
        now = time.time()
        try:
            with self._latest_frame_lock:
                if self._latest_vision_frame_bytes and (now - self._latest_vision_frame_ts) <= max_age:
                    return bytes(self._latest_vision_frame_bytes)
                if self._latest_cam_frame_bytes and (now - self._latest_cam_frame_ts) <= max_age:
                    return bytes(self._latest_cam_frame_bytes)
        except Exception:
            return None
        return None

    # --- Vision Control embedded preview in left SYS MONITOR panel -----
    def _set_vision_control_panel(self, active: bool, status: str = "") -> None:
        try:
            self._vision_panel.setVisible(bool(active))
            label = (status or ("ATIVA" if active else "INATIVA")).upper()[:28]
            label = {
                "ACTIVE": "ATIVA",
                "OFFLINE": "INATIVA",
                "OPENING WEBCAM": "ABRINDO WEBCAM",
            }.get(label, label)
            self._vision_status_lbl.setText(label)
            if active:
                self._vision_status_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
                if not self._vision_img_lbl.pixmap():
                    self._vision_img_lbl.setText("ABRINDO\nWEBCAM")
                    self._vision_img_lbl.setStyleSheet(f"""
                        QLabel {{
                            color: {C.GREEN}; background: #000308;
                            border: 1px solid {C.GREEN_D}; border-radius: 10px;
                        }}
                    """)
            else:
                self._vision_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
                self._vision_img_lbl.clear()
                self._vision_img_lbl.setText("VISÃO\nDESL.")
                self._vision_img_lbl.setStyleSheet(f"""
                    QLabel {{
                        color: {C.TEXT_DIM}; background: #000308;
                        border: 1px solid {C.BORDER_A}; border-radius: 10px;
                    }}
                """)
        except Exception:
            pass

    def _on_vision_control_frame(self, data: bytes, status: str = "") -> None:
        try:
            with self._latest_frame_lock:
                self._latest_vision_frame_bytes = bytes(data)
                self._latest_vision_frame_ts = time.time()
        except Exception:
            pass
        try:
            self._set_vision_control_panel(True, status or "ACTIVE")
            px = QPixmap()
            px.loadFromData(data)
            if px.isNull():
                return
            target_w = max(80, self._vision_img_lbl.width() - 4)
            target_h = max(58, self._vision_img_lbl.height() - 4)
            scaled = px.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._vision_img_lbl.setText("")
            self._vision_img_lbl.setPixmap(scaled)
            self._vision_img_lbl.setStyleSheet(f"""
                QLabel {{
                    color: {C.GREEN}; background: #000308;
                    border: 1px solid {C.PRI_DIM}; border-radius: 10px;
                }}
            """)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = Path.home() / "Desktop"

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SIS: Atalho na área de trabalho criado.")
        except Exception as e:
            self._log.append_log(f"ERRO: Falha ao criar atalho — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._whatsapp_overlay and self._whatsapp_overlay.isVisible():
            ow, oh = WhatsAppQrOverlay._OW, WhatsAppQrOverlay._OH
            self._whatsapp_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        self._position_camera_panels()

        # Keep secondary rail information available without crowding small
        # windows. Core controls and metrics remain visible at every size.
        compact = self.height() < 720
        if hasattr(self, "_rail_optional_widgets"):
            for widget in self._rail_optional_widgets:
                widget.setVisible(not compact)

        QTimer.singleShot(0, self._position_camera_panels)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._position_camera_panels)

    def _position_camera_panels(self) -> None:
        if not hasattr(self, "_hud_cam_stack"):
            return

        stage_w = max(1, self._hud_cam_stack.width())
        stage_h = max(1, self._hud_cam_stack.height())
        cam_w = min(390, max(260, int(stage_w * 0.31)))
        cam_h = min(255, max(170, int(cam_w * 0.64)))
        self._cam_cont.setGeometry(
            max(10, stage_w - cam_w - 18),
            max(10, stage_h - cam_h - 18),
            cam_w,
            cam_h,
        )

        if hasattr(self, "_cam_preview"):
            cw = self.centralWidget()
            pw = _CameraPreview._W
            ph = self._cam_preview.height() or _CameraPreview._H
            self._cam_preview.setGeometry(
                max(_LEFT_W + 12, cw.width() - pw - 18),
                max(68, cw.height() - ph - 228),
                pw,
                ph,
            )

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/D")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/D")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"LIG  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("LIG  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        # TOPO NOVO (05/10/2026): marca a esquerda, estado no meio (pilula que
        # muda de cor), relogio e engrenagem a direita.
        w = QWidget()
        w.setObjectName("TopoBar")
        w.setFixedHeight(66)
        w.setStyleSheet("QWidget#TopoBar { background: transparent; }")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 8, 18, 4)
        lay.setSpacing(0)

        esquerda = QWidget()
        esquerda.setFixedWidth(300)
        esquerda.setStyleSheet("background: transparent;")
        el = QHBoxLayout(esquerda)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(10)
        el.addWidget(_MarcaJarvis(), 0, Qt.AlignmentFlag.AlignVCenter)
        nomes = QVBoxLayout()
        nomes.setSpacing(0)
        nomes.addStretch()
        title = QLabel("J.A.R.V.I.S")
        title.setFont(_espacado(_fx(15, QFont.Weight.Light), 5.0))
        title.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
        _brilho(title, C.PRI, 20, 140)
        nomes.addWidget(title)

        # BANDEIRA DO PAIS DO CLIENTE (24/09/2026): o cliente do Canada pediu a
        # dele no alto da janela. Sem "bandeira" no config/idioma.json, nada.
        _flag = None
        try:
            from core.idioma import caminho_da_bandeira  # noqa: PLC0415
            _flag = caminho_da_bandeira()
        except Exception:
            _flag = None
        sub_lin = QHBoxLayout()
        sub_lin.setSpacing(6)
        sub = QLabel("JARVIS Intelligent System" if _flag else "Sistema Inteligente do JARVIS")
        fsub = _espacado(_fx(6.6, QFont.Weight.DemiBold), 2.2)
        fsub.setCapitalization(QFont.Capitalization.AllUppercase)
        sub.setFont(fsub)
        sub.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; border: none;")
        sub_lin.addWidget(sub)
        _pm = QPixmap(str(_flag)) if _flag else None
        if _pm is not None and not _pm.isNull():
            _img = QLabel()
            try:
                _dpr = max(1.0, float(QApplication.primaryScreen().devicePixelRatio()))
            except Exception:
                _dpr = 1.0
            _pm = _pm.scaled(int(22 * _dpr), int(12 * _dpr),
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
            _pm.setDevicePixelRatio(_dpr)
            _img.setPixmap(_pm)
            _img.setStyleSheet("background: transparent; border: none;")
            _img.setToolTip("Canada" if "ca" == _flag.stem else _flag.stem.upper())
            sub_lin.addWidget(_img)
        sub_lin.addStretch()
        nomes.addLayout(sub_lin)
        nomes.addStretch()
        el.addLayout(nomes)
        el.addStretch()
        lay.addWidget(esquerda)
        lay.addStretch()

        # o estado (OUVINDO, FALANDO...) numa pilula no meio do topo
        self._rail_state_lbl = QLabel("●  INICIANDO")
        self._rail_state_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rail_state_lbl.setFont(_espacado(_fx(8, QFont.Weight.DemiBold), 2.2))
        self._rail_state_lbl.setStyleSheet(self._estilo_pilula(C.PRI))
        lay.addWidget(self._rail_state_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch()

        direita = QWidget()
        direita.setFixedWidth(300)
        direita.setStyleSheet("background: transparent;")
        dl = QHBoxLayout(direita)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(14)
        dl.addStretch()
        relogio = QVBoxLayout()
        relogio.setSpacing(0)
        relogio.addStretch()
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(_espacado(_fx(17, QFont.Weight.Light), 1.5))
        self._clock_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        _brilho(self._clock_lbl, C.PRI, 16, 110)
        relogio.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        fdata = _espacado(_fx(6.6, QFont.Weight.DemiBold), 1.6)
        fdata.setCapitalization(QFont.Capitalization.AllUppercase)
        self._date_lbl.setFont(fdata)
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        relogio.addWidget(self._date_lbl)
        relogio.addStretch()
        dl.addLayout(relogio)
        dl.addWidget(self._build_botao_config(), 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(direita)
        return w

    @staticmethod
    def _estilo_pilula(cor: str) -> str:
        c = QColor(cor)
        r, g, b = c.red(), c.green(), c.blue()
        return (f"QLabel {{ color: {cor}; background: rgba({r},{g},{b},0.10);"
                f" border: 1px solid rgba({r},{g},{b},0.45); border-radius: 15px;"
                " padding: 6px 18px; }")

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        if getattr(self, "_datas_em_ingles", None) is None:
            try:
                from core.idioma import em_ingles  # noqa: PLC0415
                self._datas_em_ingles = em_ingles()
            except Exception:
                self._datas_em_ingles = False
        if self._datas_em_ingles:
            week = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][time.localtime().tm_wday]
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        else:
            week = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"][time.localtime().tm_wday]
            months = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
        now = time.localtime()
        self._date_lbl.setText(f"{week} {now.tm_mday:02d} {months[now.tm_mon - 1]} {now.tm_year}")


    def _build_vision_control_panel(self) -> QWidget:
        # Cartao da VISAO (a camera do modo de controle por visao). So aparece
        # quando ela esta ligada — desligada, so ocupava lugar.
        panel = QWidget()
        panel.setFixedHeight(176)
        panel.setObjectName("VisionRail")
        panel.setStyleSheet(f"""
            QWidget#VisionRail {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(1,26,38,0.80), stop:1 rgba(0,10,16,0.80));
                border: 1px solid rgba(26,120,160,0.42);
                border-radius: 18px;
            }}
        """)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)

        hdr = QLabel("◈ CÂMERA")
        hdr.setFont(_espacado(_fx(7.2, QFont.Weight.DemiBold), 2.4))
        hdr.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        lay.addWidget(hdr)

        self._vision_img_lbl = QLabel("VISÃO\nDESL.")
        self._vision_img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._vision_img_lbl.setMinimumHeight(48)
        self._vision_img_lbl.setStyleSheet(f"""
            QLabel {{
                color: {C.TEXT_DIM};
                background: #000308;
                border: 1px solid {C.BORDER_A};
                border-radius: 10px;
            }}
        """)
        self._vision_img_lbl.setFont(_fx(8, QFont.Weight.Bold))
        lay.addWidget(self._vision_img_lbl, stretch=1)

        self._vision_status_lbl = QLabel("INATIVA")
        self._vision_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._vision_status_lbl.setFont(_espacado(_fx(6.4, QFont.Weight.Bold), 1.4))
        self._vision_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        lay.addWidget(self._vision_status_lbl)
        panel.hide()
        return panel

    def _build_botao_config(self) -> QWidget:
        """A engrenagem: abre TUDO numa janela so (chave, voz, jeito, microfone,
        som e os dados do dono, em secoes). Agora fica no topo, a direita."""
        _fam_icone = _system_icon_font_family()
        _fluent = _fam_icone != "Segoe UI Symbol"
        b = QPushButton("" if _fluent else "⚙")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip("Configurar: chave, voz, jeito, microfone, som e seus dados")
        b.setFont(QFont(_fam_icone, 13 if _fluent else 16))
        b.setFixedSize(40, 40)
        b.setStyleSheet(
            f"QPushButton {{ color: {C.PRI}; background: rgba(0,212,255,0.07);"
            f" border: 1px solid rgba(0,212,255,0.28); border-radius: 20px; }}"
            f"QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI};"
            f" background: rgba(0,212,255,0.18); }}")

        def _abrir():
            # Rede de seguranca: excecao aqui nao pode fechar o JARVIS.
            try:
                self._show_personas()
            except Exception as exc:
                import traceback as _tb
                _tb.print_exc()
                casca = getattr(self, "_ui", None)
                if casca is not None:
                    casca.write_log(
                        f"ERR: configuracoes falharam — {type(exc).__name__}: {str(exc)[:110]}")
        b.clicked.connect(_abrir)
        return b

    @staticmethod
    def _cartao(nome_obj: str) -> QWidget:
        w = QWidget()
        w.setObjectName(nome_obj)
        w.setStyleSheet(
            f"QWidget#{nome_obj} {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            " stop:0 rgba(1,26,38,0.80), stop:1 rgba(0,10,16,0.80));"
            " border: 1px solid rgba(26,120,160,0.42); border-radius: 18px; }")
        return w

    @staticmethod
    def _titulo_cartao(texto: str, cor: str = C.PRI) -> QWidget:
        linha = QWidget()
        linha.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(linha)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        ponto = QLabel("●")
        ponto.setFont(_fx(6))
        ponto.setStyleSheet(f"color: {cor}; background: transparent; border: none;")
        hl.addWidget(ponto)
        t = QLabel(texto)
        t.setFont(_espacado(_fx(7.2, QFont.Weight.DemiBold), 2.4))
        t.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        hl.addWidget(t)
        hl.addStretch()
        return linha

    def _build_coluna_lateral(self) -> QWidget:
        """Coluna da direita: SISTEMA (medidores), CAMERA (so quando ligada) e
        a CONVERSA em baloes com o campo de digitar."""
        col = QWidget()
        col.setObjectName("ColunaLateral")
        col.setStyleSheet("QWidget#ColunaLateral { background: transparent; }")
        col.setMinimumWidth(344)
        col.setMaximumWidth(420)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # ── SISTEMA ──────────────────────────────────────────────────────────
        sistema = self._cartao("CartaoSistema")
        sv = QVBoxLayout(sistema)
        sv.setContentsMargins(14, 12, 14, 14)
        sv.setSpacing(10)
        sv.addWidget(self._titulo_cartao("SISTEMA", C.GREEN))
        grade = QGridLayout()
        grade.setHorizontalSpacing(8)
        grade.setVerticalSpacing(8)
        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("RAM", C.ACC2)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_net = MetricBar("REDE", C.GREEN)
        self._bar_tmp = MetricBar("TEMP", "#ff6688")
        grade.addWidget(self._bar_cpu, 0, 0)
        grade.addWidget(self._bar_mem, 0, 1)
        grade.addWidget(self._bar_gpu, 0, 2)
        grade.addWidget(self._bar_net, 1, 0)
        grade.addWidget(self._bar_tmp, 1, 1)

        info = QWidget()
        info.setObjectName("InfoTile")
        info.setFixedHeight(56)
        info.setStyleSheet(
            "QWidget#InfoTile { background: rgba(255,255,255,0.027);"
            " border: 1px solid rgba(26,120,160,0.30); border-radius: 12px; }")
        il = QVBoxLayout(info)
        il.setContentsMargins(11, 5, 8, 5)
        il.setSpacing(0)
        self._uptime_lbl = QLabel("LIG  --:--")
        self._uptime_lbl.setFont(_fx(7.6, QFont.Weight.DemiBold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        il.addWidget(self._uptime_lbl)
        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(_fx(7.6))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        il.addWidget(self._proc_lbl)
        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"SO  {os_name}")
        os_lbl.setFont(_fx(7.6))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        il.addWidget(os_lbl)
        grade.addWidget(info, 1, 2)
        for c in range(3):
            grade.setColumnStretch(c, 1)
        sv.addLayout(grade)
        v.addWidget(sistema)

        # ── CAMERA (so quando a visao esta ligada) ───────────────────────────
        self._vision_panel = self._build_vision_control_panel()
        v.addWidget(self._vision_panel)

        # ── COWORK (so com projeto aberto) ───────────────────────────────────
        self._cw_card = self._build_cartao_cowork()
        v.addWidget(self._cw_card)

        # ── CONVERSA ─────────────────────────────────────────────────────────
        conversa = self._cartao("CartaoConversa")
        cv = QVBoxLayout(conversa)
        cv.setContentsMargins(14, 12, 14, 14)
        cv.setSpacing(8)
        cv.addWidget(self._titulo_cartao("CONVERSA", C.PRI))
        self._log = LogWidget()
        self._log.setMinimumHeight(90)
        cv.addWidget(self._log, stretch=1)
        self._file_hint = QLabel("Nenhum arquivo carregado — clique ou arraste no botão ARQUIVO")
        self._file_hint.setFont(_ftexto(7))
        self._file_hint.setWordWrap(True)
        self._file_hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        self._file_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cv.addWidget(self._file_hint)
        cv.addLayout(self._build_input_row())
        v.addWidget(conversa, stretch=1)
        return col

    def _build_dock(self) -> QWidget:
        """Os botoes numa barra de vidro flutuando embaixo da esfera."""
        area = QWidget()
        area.setObjectName("DockArea")
        area.setStyleSheet("QWidget#DockArea { background: transparent; }")
        v = QVBoxLayout(area)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(6)

        pilula = QWidget()
        pilula.setObjectName("DockVidro")
        pilula.setStyleSheet(
            "QWidget#DockVidro { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            " stop:0 rgba(2,32,46,0.88), stop:1 rgba(0,13,20,0.88));"
            " border: 1px solid rgba(26,120,160,0.55); border-radius: 28px; }")
        row = QHBoxLayout(pilula)
        row.setContentsMargins(12, 7, 12, 7)
        row.setSpacing(4)

        self._mute_btn = self._make_control_button("mute", "🎙\nMICROFONE\nATIVO", C.GREEN)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        row.addWidget(self._mute_btn)

        # PALAVRA DE ATIVACAO (02/09/2026). Ligado, ele so responde quando
        # ouve o proprio nome; depois disso fica 30 s acordado e aceita tudo.
        # Fica ao lado do microfone de proposito: e o mesmo assunto.
        self._nome_btn = self._make_control_button("so_nome", "◇\nSEMPRE\nOUVINDO", C.PRI)
        self._nome_btn.clicked.connect(self._toggle_so_nome)
        self._style_nome_btn()
        row.addWidget(self._nome_btn)

        self._interrupt_btn = self._make_control_button(
            "interrupt", "■\nINTERROMPER\n[ESC]", C.MUTED_C, "#140008")
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        row.addWidget(self._interrupt_btn)

        self._whatsapp_btn = self._make_control_button(
            "whatsapp", "☘\nCONECTAR\nWHATSAPP", C.GREEN, "#00140a")
        self._whatsapp_btn.clicked.connect(self._open_whatsapp)
        row.addWidget(self._whatsapp_btn)

        self._remote_btn = self._make_control_button(
            "remote", "◉\nCONTROLE\nREMOTO", C.PRI, "#00091a")
        self._remote_btn.clicked.connect(self._open_remote)
        row.addWidget(self._remote_btn)

        self._webcam_btn = self._make_control_button("webcam", "🎥\nWEBCAM\nINATIVA", C.PRI)
        self._webcam_btn.clicked.connect(self._toggle_camera_stream)
        self._style_webcam_btn()
        row.addWidget(self._webcam_btn)

        # COWORK (05/10/2026): o programador do JARVIS. Sem pasta, o clique
        # pede para escolher uma; com pasta, mostra/esconde o cartao dele.
        self._cowork_btn = self._make_control_button("cowork", "⌨\nCOWORK\nSEM PASTA", C.ACC, "#1a0d00")
        self._cowork_btn.clicked.connect(self._cowork_clicou)
        row.addWidget(self._cowork_btn)

        # COMPUTER USE (08/10/2026, pedido do Diego): o mesmo que dizer
        # "ativar computador". Clicou, ele pergunta o que fazer; a proxima
        # frase (falada ou digitada) e a tarefa. Rodando, o clique para.
        self._cu_btn = self._make_control_button(
            "computer_use", "\U0001f5b1\nCOMPUTER USE\nATIVAR", C.PRI, "#00091a")
        self._cu_btn.setToolTip("Computer use: clique e diga (ou digite) o que o JARVIS deve fazer no computador")
        self._cu_btn.clicked.connect(self._computer_use_clicou)
        self._cu_estado = "livre"
        row.addWidget(self._cu_btn)


        self._drop_zone = FileDropZone()
        self._drop_zone.setObjectName("fileUpload")
        self._drop_zone.setFixedSize(80, 84)
        self._drop_zone.file_selected.connect(self._on_file_selected)
        self._drop_zone.files_selected.connect(self._on_files_selected)
        row.addWidget(self._drop_zone)
        pilula.adjustSize()

        # Janela estreita: o dock rola para o lado em vez de esconder botao.
        rolagem = QScrollArea()
        rolagem.setWidgetResizable(False)
        rolagem.setFrameShape(QFrame.Shape.NoFrame)
        rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        rolagem.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rolagem.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:horizontal {{ background: transparent; height: 5px; border: none; }}
            QScrollBar::handle:horizontal {{ background: {C.BORDER_B}; border-radius: 2px; min-width: 30px; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; border: none; }}
        """)
        rolagem.viewport().setStyleSheet("background: transparent;")
        rolagem.setWidget(pilula)
        rolagem.setFixedHeight(pilula.sizeHint().height() + 8)
        rolagem.setMaximumWidth(pilula.sizeHint().width() + 2)
        linha = QHBoxLayout()
        linha.setContentsMargins(0, 0, 0, 0)
        linha.addStretch()
        linha.addWidget(rolagem, stretch=1)
        linha.addStretch()
        v.addLayout(linha)

        self._atalhos_lbl = QLabel("[F4] Mudo  ·  [F11] Tela cheia  ·  [F2] Chave de IA")
        self._atalhos_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._atalhos_lbl.setFont(_espacado(_fx(6.6), 1.0))
        self._atalhos_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        v.addWidget(self._atalhos_lbl)

        self._dock_content = pilula
        self._dock_expanded = True
        self._rail_optional_widgets = [self._atalhos_lbl]
        return area

    # ── COMPUTER USE (08/10/2026) ─────────────────────────────────────────
    _CU_VISUAL = {
        "livre":       ("ATIVAR",      "PRI",   "#00091a"),
        "esperando":   ("OUVINDO",     "GREEN", "#00140a"),
        "trabalhando": ("PARAR [ESC]", "ACC2",  "#1a1400"),
    }

    def _computer_use_clicou(self) -> None:
        cb = getattr(self, "on_computer_use", None)
        if not callable(cb):
            self._log.append_log("SIS: o computer use ainda esta carregando.")
            return
        try:
            cb()
        except Exception as exc:
            self._log.append_log(f"ERR: computer use — {str(exc)[:100]}")
        self._atualizar_computer_use()

    def _atualizar_computer_use(self) -> None:
        """O botao mostra o estado: ATIVAR, OUVINDO (esperando a tarefa) ou
        PARAR (trabalhando). Lido do main a cada 0,4 s — barato."""
        btn = getattr(self, "_cu_btn", None)
        cb = getattr(self, "on_computer_use_estado", None)
        if btn is None or not callable(cb):
            return
        try:
            estado = str(cb() or "livre")
        except Exception:
            estado = "livre"
        if estado not in self._CU_VISUAL or estado == getattr(self, "_cu_estado", None):
            return
        self._cu_estado = estado
        rotulo, cor, fundo = self._CU_VISUAL[estado]
        btn.setText("\U0001f5b1\nCOMPUTER USE\n" + rotulo)
        if isinstance(btn, ControlCard):
            btn.set_card_theme(getattr(C, cor), fundo)

    # ── CORES (08/10/2026) ──────────────────────────────────────────────────
    def aplicar_tema(self) -> None:
        """Reaplica TODAS as folhas de estilo com as cores escolhidas agora.

        Cada widget guarda a folha original (paleta de fabrica) na propriedade
        `_css_tema`; aqui ela passa de novo pelo tema. So troca o que mudou."""
        t0 = time.monotonic()
        n = 0
        for w in QApplication.allWidgets():
            try:
                css = w.property("_css_tema")
                if not isinstance(css, str) or not css:
                    w.update()
                    continue
                novo = css if (_SEM_TEMA in css or not _tema.ativo()) else _tema.css(css)
                if w.styleSheet() != novo:
                    _SETSS_ORIGINAL(w, novo)
                    n += 1
                w.update()
            except Exception:
                continue
        try:
            self.hud.update()
        except Exception:
            pass
        print(f"[JARVIS] cores aplicadas: {n} estilos em {time.monotonic() - t0:.2f}s")

    def _pagina_cores(self) -> QWidget:
        """A aba CORES da engrenagem: item a esquerda, quadro de cor a direita.

        Passar o mouse no quadro mostra a cor EXATA (RGB e #hex) e a previa;
        clicar aplica no JARVIS inteiro, na hora, e guarda."""
        pag = QWidget()
        raiz = QVBoxLayout(pag)
        raiz.setContentsMargins(0, 8, 0, 0)
        raiz.setSpacing(8)
        exp = QLabel("Escolha o que pintar à esquerda. Passe o mouse no quadro para ver a cor exata; "
                     "clique para aplicar. Muda na hora e fica salvo.")
        exp.setWordWrap(True)
        exp.setFont(_fx(8.6))
        exp.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        raiz.addWidget(exp)

        corpo = QHBoxLayout()
        corpo.setSpacing(14)
        raiz.addLayout(corpo)

        # ── esquerda: grupos e itens ────────────────────────────────────────
        esq = QVBoxLayout()
        esq.setSpacing(6)
        corpo.addLayout(esq, 0)
        abas = QHBoxLayout()
        abas.setSpacing(4)
        esq.addLayout(abas)
        pilha = QStackedWidget()
        pilha.setFixedWidth(250)
        pilha.setFixedHeight(292)
        esq.addWidget(pilha)
        esq.addStretch()
        previa = _PreviaTema()

        estado = {"chave": "principal", "grupo": 0}
        linhas: dict = {}       # chave -> (botao, amostra)
        botoes_aba: list = []

        def _estilo_aba(b, ativo):
            b.setStyleSheet(
                f"QPushButton {{ color: {C.TEXT if ativo else C.PRI};"
                f" background: {'rgba(0,212,255,0.12)' if ativo else 'transparent'};"
                f" border: 1px solid {C.PRI if ativo else C.BORDER_B};"
                f" border-radius: 4px; padding: 4px 8px; }}"
                f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.PRI}; }}")

        def _estilo_linha(b, ativo):
            b.setStyleSheet(
                f"QPushButton {{ text-align: left; padding: 5px 8px 5px 34px;"
                f" border: 1px solid {C.PRI if ativo else C.BORDER};"
                f" border-radius: 4px; background: {C.PRI_GHO if ativo else 'transparent'};"
                f" color: {C.TEXT if ativo else C.TEXT_MED}; }}"
                f"QPushButton:hover {{ border-color: {C.BORDER_B}; color: {C.TEXT}; }}")

        def _pintar_amostras():
            for chave, (_b, amostra) in linhas.items():
                c = _tema.atual(chave)
                marca = "" if _tema.escolhida(chave) else "  "
                amostra.setStyleSheet(
                    _SEM_TEMA + f"background: {c}; border: 1px solid #ffffff55; border-radius: 4px;")
                amostra.setToolTip(c.upper() + ("" if marca else "  (escolhida por você)"))

        def _selecionar(chave):
            estado["chave"] = chave
            for k, (b, _a) in linhas.items():
                _estilo_linha(b, k == chave)
            seletor.set_cor(QColor(_tema.atual(chave)))
            _mostrar(QColor(_tema.atual(chave)), "agora")
            previa.prever(None, None)

        for gi, (grupo, itens) in enumerate(_tema.GRUPOS):
            ba = QPushButton(grupo)
            ba.setCursor(Qt.CursorShape.PointingHandCursor)
            ba.setFont(_fx(7.8, QFont.Weight.Bold))
            ba.setFixedHeight(26)
            botoes_aba.append(ba)
            abas.addWidget(ba)
            lista = QWidget()
            lv = QVBoxLayout(lista)
            lv.setContentsMargins(0, 0, 0, 0)
            lv.setSpacing(4)
            for chave, nome, desc in itens:
                b = QPushButton(nome + chr(10) + desc)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setFont(_fx(7.8))
                b.setMinimumHeight(36)
                amostra = QLabel(b)
                amostra.setGeometry(8, 9, 18, 18)
                amostra.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                b.clicked.connect(lambda _=False, k=chave: _selecionar(k))
                linhas[chave] = (b, amostra)
                lv.addWidget(b)
            lv.addStretch()
            if len(itens) > 6:
                rol = QScrollArea()
                rol.setWidgetResizable(True)
                rol.setFrameShape(QFrame.Shape.NoFrame)
                rol.setStyleSheet("QScrollArea { background: transparent; border: none; }")
                rol.viewport().setStyleSheet("background: transparent;")
                rol.setWidget(lista)
                pilha.addWidget(rol)
            else:
                pilha.addWidget(lista)

        def _ir_grupo(gi):
            estado["grupo"] = gi
            pilha.setCurrentIndex(gi)
            for i, ba in enumerate(botoes_aba):
                _estilo_aba(ba, i == gi)
            primeiro = _tema.GRUPOS[gi][1][0][0]
            _selecionar(primeiro)

        for gi, ba in enumerate(botoes_aba):
            ba.clicked.connect(lambda _=False, g=gi: _ir_grupo(g))

        # ── direita: o quadro de cor, os numeros e os botoes ────────────────
        dir_ = QVBoxLayout()
        dir_.setSpacing(8)
        corpo.addLayout(dir_, 1)
        seletor = _SeletorCor()
        dir_.addWidget(seletor)

        info = QHBoxLayout()
        info.setSpacing(10)
        grande = QLabel()
        grande.setFixedSize(54, 38)
        info.addWidget(grande)
        numeros = QLabel()
        numeros.setFont(_fx(10, QFont.Weight.Bold))
        numeros.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        quando = QLabel()
        quando.setFont(_fx(7.6))
        quando.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        col_num = QVBoxLayout()
        col_num.setSpacing(0)
        col_num.addWidget(numeros)
        col_num.addWidget(quando)
        info.addLayout(col_num, 1)
        dir_.addLayout(info)

        def _mostrar(cor: QColor, como: str):
            grande.setStyleSheet(
                _SEM_TEMA + f"background: {cor.name()}; border: 1px solid #ffffff66; border-radius: 6px;")
            numeros.setText(f"R {cor.red():>3}   G {cor.green():>3}   B {cor.blue():>3}    {cor.name().upper()}")
            quando.setText({"passando": "passando o mouse — clique para aplicar",
                            "agora": "cor que está valendo agora",
                            "aplicada": "aplicada no JARVIS inteiro"}.get(como, ""))

        # RGB e #hex digitados
        from PyQt6.QtWidgets import QSpinBox  # noqa: PLC0415
        linha_rgb = QHBoxLayout()
        linha_rgb.setSpacing(6)
        campos = {}
        estilo_campo = (f"QSpinBox, QLineEdit {{ background: #000d12; color: {C.TEXT};"
                        f" border: 1px solid {C.BORDER_B}; border-radius: 4px; padding: 3px 4px; }}"
                        f"QSpinBox:focus, QLineEdit:focus {{ border-color: {C.PRI}; }}")
        for letra in ("R", "G", "B"):
            rot = QLabel(letra)
            rot.setFont(_fx(8, QFont.Weight.Bold))
            rot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
            sp = QSpinBox()
            sp.setRange(0, 255)
            sp.setFixedWidth(62)
            sp.setStyleSheet(estilo_campo)
            campos[letra] = sp
            linha_rgb.addWidget(rot)
            linha_rgb.addWidget(sp)
        hexa = QLineEdit()
        hexa.setFixedWidth(84)
        hexa.setMaxLength(7)
        hexa.setPlaceholderText("#00d4ff")
        hexa.setStyleSheet(estilo_campo)
        linha_rgb.addWidget(hexa)
        b_aplicar = QPushButton("APLICAR")
        linha_rgb.addWidget(b_aplicar)
        linha_rgb.addStretch()
        dir_.addLayout(linha_rgb)

        linha_bt = QHBoxLayout()
        linha_bt.setSpacing(6)
        b_volta = QPushButton("VOLTAR À ORIGINAL")
        b_tudo = QPushButton("RESTAURAR TODAS AS CORES")
        linha_bt.addWidget(b_volta)
        linha_bt.addWidget(b_tudo)
        linha_bt.addStretch()
        dir_.addLayout(linha_bt)
        rot_previa = QLabel("PRÉVIA")
        rot_previa.setFont(_espacado(_fx(7.4, QFont.Weight.Bold), 1.6))
        rot_previa.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        dir_.addWidget(rot_previa)
        dir_.addWidget(previa)
        dir_.addStretch()
        for bb in (b_aplicar, b_volta, b_tudo):
            bb.setCursor(Qt.CursorShape.PointingHandCursor)
            bb.setFont(_fx(7.8, QFont.Weight.Bold))
            bb.setFixedHeight(28)
            bb.setStyleSheet(
                f"QPushButton {{ color: {C.PRI}; background: transparent;"
                f" border: 1px solid {C.BORDER_B}; border-radius: 4px; padding: 0 12px; }}"
                f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.PRI}; }}")

        _sincronizando = {"v": False}

        def _campos_de(cor: QColor):
            _sincronizando["v"] = True
            try:
                campos["R"].setValue(cor.red())
                campos["G"].setValue(cor.green())
                campos["B"].setValue(cor.blue())
                hexa.setText(cor.name())
            finally:
                _sincronizando["v"] = False

        def _aplicar_cor(cor: QColor):
            _tema.definir(estado["chave"], cor.name())
            self.aplicar_tema()
            _pintar_amostras()
            _campos_de(cor)
            _mostrar(cor, "aplicada")
            previa.prever(None, None)

        def _passou(cor: QColor):
            _mostrar(cor, "passando")
            previa.prever(estado["chave"], cor.name())

        def _saiu():
            atual = QColor(_tema.atual(estado["chave"]))
            _mostrar(atual, "agora")
            previa.prever(None, None)

        def _rgb_digitado(*_):
            if _sincronizando["v"]:
                return
            cor = QColor(campos["R"].value(), campos["G"].value(), campos["B"].value())
            _sincronizando["v"] = True
            hexa.setText(cor.name())
            _sincronizando["v"] = False
            _passou(cor)
            seletor.set_cor(cor)

        def _hex_digitado(txt):
            if _sincronizando["v"]:
                return
            n = _tema.normalizar(txt)
            if n:
                cor = QColor(n)
                _sincronizando["v"] = True
                campos["R"].setValue(cor.red())
                campos["G"].setValue(cor.green())
                campos["B"].setValue(cor.blue())
                _sincronizando["v"] = False
                _passou(cor)
                seletor.set_cor(cor)

        for sp in campos.values():
            sp.valueChanged.connect(_rgb_digitado)
        hexa.textEdited.connect(_hex_digitado)
        b_aplicar.clicked.connect(lambda: _aplicar_cor(
            QColor(campos["R"].value(), campos["G"].value(), campos["B"].value())))
        hexa.returnPressed.connect(lambda: _aplicar_cor(
            QColor(campos["R"].value(), campos["G"].value(), campos["B"].value())))
        seletor.passou.connect(_passou)
        seletor.saiu.connect(_saiu)
        seletor.escolheu.connect(_aplicar_cor)

        def _voltar():
            _tema.definir(estado["chave"], None)
            self.aplicar_tema()
            _pintar_amostras()
            atual = QColor(_tema.atual(estado["chave"]))
            seletor.set_cor(atual)
            _campos_de(atual)
            _mostrar(atual, "agora")
            previa.prever(None, None)

        def _tudo():
            _tema.restaurar_tudo()
            self.aplicar_tema()
            _pintar_amostras()
            _selecionar(estado["chave"])
            _campos_de(QColor(_tema.atual(estado["chave"])))

        b_volta.clicked.connect(_voltar)
        b_tudo.clicked.connect(_tudo)

        _pintar_amostras()
        _ir_grupo(0)
        _campos_de(QColor(_tema.atual("principal")))
        return pag

    # ── COWORK (05/10/2026) ─────────────────────────────────────────────────
    def _build_cartao_cowork(self) -> QWidget:
        card = self._cartao("CartaoCowork")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 11, 14, 12)
        v.setSpacing(5)
        topo = QHBoxLayout()
        topo.setSpacing(6)
        topo.addWidget(self._titulo_cartao("COWORK", C.ACC), 1)
        self._cw_nome = QLabel("")
        self._cw_nome.setFont(_fx(8.5, QFont.Weight.DemiBold))
        self._cw_nome.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
        self._cw_nome.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        topo.addWidget(self._cw_nome, 0)
        self._cw_fechar_btn = QPushButton("✕")
        self._cw_fechar_btn.setFixedSize(20, 20)
        self._cw_fechar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cw_fechar_btn.setToolTip("Fechar o projeto do Cowork (os arquivos continuam na pasta)")
        self._cw_fechar_btn.setFont(_fx(8, QFont.Weight.Bold))
        self._cw_fechar_btn.setStyleSheet(
            f"QPushButton {{ color: {C.TEXT_DIM}; background: transparent; border: none; }}"
            f"QPushButton:hover {{ color: {C.MUTED_C}; }}"
            "QPushButton:disabled { color: #2a3a40; }")
        self._cw_fechar_btn.clicked.connect(lambda: self._cowork_avisa("fechar", ""))
        topo.addWidget(self._cw_fechar_btn, 0)
        v.addLayout(topo)
        self._cw_caminho = QLabel("")
        self._cw_caminho.setFont(_ftexto(7))
        self._cw_caminho.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        v.addWidget(self._cw_caminho)
        self._cw_etapa = QLabel("")
        self._cw_etapa.setFont(_espacado(_fx(7.6, QFont.Weight.DemiBold), 0.6))
        self._cw_etapa.setWordWrap(True)
        self._cw_etapa.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        v.addWidget(self._cw_etapa)
        self._cw_linhas = QLabel("")
        self._cw_linhas.setFont(_ftexto(7.2))
        self._cw_linhas.setWordWrap(True)
        self._cw_linhas.setMaximumHeight(46)
        self._cw_linhas.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        v.addWidget(self._cw_linhas)

        pedido = QHBoxLayout()
        pedido.setSpacing(6)
        self._cw_input = QLineEdit()
        self._cw_input.setPlaceholderText("Peça ao Cowork… (ex.: faz um jogo da cobrinha)")
        self._cw_input.setFont(_ftexto(9))
        self._cw_input.setFixedHeight(30)
        self._cw_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER_A}; border-radius: 10px; padding: 2px 10px;
                selection-background-color: {C.PRI_GHO};
            }}
            QLineEdit:focus {{ border: 1px solid {C.ACC}; }}
            QLineEdit:disabled {{ color: {C.TEXT_DIM}; }}
        """)
        self._cw_input.returnPressed.connect(self._cowork_enviar)
        pedido.addWidget(self._cw_input)
        _fam = _system_icon_font_family()
        _flu = _fam != "Segoe UI Symbol"
        self._cw_enviar_btn = QPushButton("" if _flu else "▸")
        self._cw_enviar_btn.setFixedSize(30, 30)
        self._cw_enviar_btn.setFont(QFont(_fam, 10) if _flu else _fx(11, QFont.Weight.Bold))
        self._cw_enviar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cw_enviar_btn.setToolTip("Mandar para o Cowork")
        self._cw_enviar_btn.setStyleSheet("""
            QPushButton { color: #1a0800; border: none; border-radius: 15px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffcc00, stop:1 #ff6b00); }
            QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffe066, stop:1 #ff8a33); }
            QPushButton:disabled { background: #2a2010; color: #5a4a20; }
        """)
        self._cw_enviar_btn.clicked.connect(self._cowork_enviar)
        pedido.addWidget(self._cw_enviar_btn)
        v.addLayout(pedido)

        botoes = QHBoxLayout()
        botoes.setSpacing(5)
        self._cw_botoes = {}
        for rotulo, acao, dica in (("PASTA", "pasta", "Escolher outra pasta de projeto"),
                                   ("RODAR", "rodar", "Abrir o projeto para ver"),
                                   ("DESFAZER", "desfazer", "Voltar a ultima mudanca do Cowork"),
                                   ("PARAR", "parar", "Parar o trabalho em andamento"),
                                   ("ABRIR", "abrir_pasta", "Abrir a pasta no Explorer")):
            b = QPushButton(rotulo)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(dica)
            b.setFixedHeight(24)
            b.setFont(_espacado(_fx(6.4, QFont.Weight.DemiBold), 0.8))
            cor = C.MUTED_C if acao == "parar" else C.ACC
            b.setStyleSheet(
                f"QPushButton {{ color: {cor}; background: rgba(255,107,0,0.06);"
                f" border: 1px solid rgba(255,107,0,0.30); border-radius: 12px; padding: 0 6px; }}"
                f"QPushButton:hover {{ color: {C.WHITE}; border-color: {cor}; background: rgba(255,107,0,0.16); }}"
                f"QPushButton:disabled {{ color: #3d3a33; border-color: rgba(255,255,255,0.06); background: transparent; }}")
            b.clicked.connect(lambda _=False, a=acao: self._cowork_botao(a))
            botoes.addWidget(b)
            self._cw_botoes[acao] = b
        v.addLayout(botoes)
        self._cw_estado = {}
        self._cw_escondido_pelo_dono = False
        card.hide()
        return card

    def _cowork_avisa(self, acao: str, valor: str = "") -> None:
        cb = getattr(self, "on_cowork_acao", None)
        if cb is None:
            self._log.append_log("ERRO: o Cowork ainda nao esta pronto; tente em alguns segundos.")
            return
        try:
            cb(acao, valor)
        except Exception as exc:
            self._log.append_log(f"ERRO: Cowork — {type(exc).__name__}: {str(exc)[:90]}")

    def _cowork_clicou(self) -> None:
        est = getattr(self, "_cw_estado", {}) or {}
        if not est.get("pasta"):
            self._cowork_escolher_pasta()
            return
        visivel = self._cw_card.isVisible()
        self._cw_card.setVisible(not visivel)
        self._cw_escondido_pelo_dono = visivel

    def _cowork_escolher_pasta(self) -> None:
        if (getattr(self, "_cw_estado", {}) or {}).get("ocupado"):
            self._log.append_log("SIS: Espere o Cowork terminar antes de trocar de pasta.")
            return
        inicio = Path.home() / "Documents" / "JARVIS Cowork"
        if not inicio.is_dir():
            inicio = Path.home() / "Documents"
        pasta = QFileDialog.getExistingDirectory(
            self, "Escolha (ou crie) a pasta do projeto para o Cowork", str(inicio))
        if pasta:
            self._cw_escondido_pelo_dono = False
            self._cowork_avisa("pasta", pasta)

    def _cowork_botao(self, acao: str) -> None:
        if acao == "pasta":
            self._cowork_escolher_pasta()
        else:
            self._cowork_avisa(acao, "")

    def _cowork_enviar(self) -> None:
        txt = self._cw_input.text().strip()
        if not txt:
            return
        self._cw_input.clear()
        self._log.append_log(f"Você: {txt}")
        self._cowork_avisa("pedido", txt)

    def _aplicar_cowork_estado(self, est) -> None:
        est = dict(est or {})
        self._cw_estado = est
        tem = bool(est.get("pasta"))
        ocupado = bool(est.get("ocupado"))
        if not tem:
            texto, cor, fundo = "⌨\nCOWORK\nSEM PASTA", C.ACC, "#1a0d00"
        elif ocupado:
            texto, cor, fundo = "⌨\nCOWORK\nTRABALHANDO", C.ACC2, "#1a1400"
        else:
            texto, cor, fundo = "⌨\nCOWORK\nATIVO", C.ACC, "#1a0d00"
        if hasattr(self, "_cowork_btn"):
            if self._cowork_btn.text() != texto:
                self._cowork_btn.setText(texto)
                self._cowork_btn.set_card_theme(cor, fundo)
        if not hasattr(self, "_cw_card"):
            return
        if not tem:
            self._cw_card.hide()
            return
        if ocupado or not self._cw_escondido_pelo_dono:
            self._cw_card.show()
        self._cw_nome.setText(str(est.get("nome") or "")[:34])
        caminho = str(est.get("pasta") or "")
        largura = max(120, self._cw_caminho.width() or 280)
        self._cw_caminho.setText(QFontMetricsF(self._cw_caminho.font()).elidedText(
            caminho, Qt.TextElideMode.ElideMiddle, float(largura)))
        self._cw_caminho.setToolTip(caminho)
        etapa = str(est.get("etapa") or "")
        self._cw_etapa.setText(("●  " + etapa.upper()) if (ocupado and etapa) else
                               ("●  PRONTO PARA TRABALHAR" if not ocupado else "●  TRABALHANDO"))
        self._cw_etapa.setStyleSheet(
            f"color: {C.ACC2 if ocupado else C.GREEN}; background: transparent; border: none;")
        linhas = [str(x) for x in (est.get("linhas") or []) if str(x).strip()]
        self._cw_linhas.setText("\n".join(f"› {l}" for l in linhas[-3:]) if ocupado else "")
        self._cw_linhas.setVisible(ocupado and bool(linhas))
        self._cw_input.setEnabled(not ocupado)
        self._cw_enviar_btn.setEnabled(not ocupado)
        self._cw_botoes["pasta"].setEnabled(not ocupado)
        self._cw_botoes["rodar"].setEnabled(not ocupado)
        self._cw_botoes["desfazer"].setEnabled(not ocupado and bool(est.get("pode_desfazer")))
        self._cw_botoes["parar"].setEnabled(ocupado)
        self._cw_botoes["abrir_pasta"].setEnabled(True)
        self._cw_fechar_btn.setEnabled(not ocupado)

    def _control_card_style(self, color: str, background: str = C.PANEL) -> str:
        return f"""
            QPushButton {{
                background: {background}; color: {color};
                border: 1px solid {C.BORDER_A}; border-radius: 6px;
                padding: 5px 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {color};
            }}
            QPushButton:pressed {{ background: #002236; }}
        """

    def _make_control_button(
        self, name: str, text: str, color: str, background: str = C.PANEL
    ) -> QPushButton:
        button = ControlCard(text, color, background)
        button.setObjectName(name)
        button.setFixedSize(80, 84)
        return button

    def _toggle_control_dock(self) -> None:
        self._dock_expanded = not getattr(self, "_dock_expanded", True)
        self._dock_content.setVisible(self._dock_expanded)

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Digite um comando ou pergunta…")
        self._input.setFont(_ftexto(9.5))
        self._input.setFixedHeight(34)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER_A}; border-radius: 11px; padding: 3px 12px;
                selection-background-color: {C.PRI_GHO};
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; background: #001a26; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        _fam_icone = _system_icon_font_family()
        _fluent = _fam_icone != "Segoe UI Symbol"
        send = QPushButton("\ue724" if _fluent else "▸")
        send.setFixedSize(34, 34)
        send.setFont(QFont(_fam_icone, 11) if _fluent else _fx(12, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setToolTip("Enviar")
        send.setStyleSheet("""
            QPushButton {
                color: #00121a; border: none; border-radius: 17px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #00d4ff, stop:1 #008fbf);
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5fe8ff, stop:1 #00a8dd);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #00a8cc, stop:1 #006f99);
            }
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def nivel_do_microfone(self, nivel: float) -> None:
        """A faixa de baixo mexe com a voz do DONO, nao so com a do JARVIS.

        Vem da linha do audio, ~25 vezes por segundo. De proposito NAO passa
        por sinal nem chama `update()`: e so guardar um numero, e quem repinta
        e o cronometro de 16 ms do proprio desenho. Emitir sinal 25 vezes por
        segundo so para mexer uma barrinha entupiria a fila do Qt.
        """
        try:
            self.hud.ouviu(nivel)
        except Exception:
            pass

    def mostrar_aviso_pc(self, ligado: bool, texto: str = "") -> None:
        """Liga/desliga a faixa "estou usando o computador".

        Chamado de dentro do laco `usar_computador`, que roda numa thread — por
        isso passa por sinal: mexer em widget fora da thread da interface e o
        jeito classico de derrubar Qt.
        """
        try:
            self._aviso_pc_sig.emit(bool(ligado), str(texto or ""))
        except Exception:
            pass

    def _aplicar_aviso_pc(self, ligado: bool, texto: str) -> None:
        try:
            if ligado:
                if self._aviso_pc is None:
                    self._aviso_pc = AvisoComputador()
                self._aviso_pc.mostrar(texto)
            elif self._aviso_pc is not None:
                self._aviso_pc.hide()
        except Exception:
            pass

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(1,26,38,0.85), stop:1 rgba(0,10,16,0.85));
                border: 1px solid rgba(26,120,160,0.42);
                border-radius: 18px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(_fx(9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("RESUMO")
        self._content_title_lbl.setFont(_fx(8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(_fx(7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("FECHAR  ✕")
        dismiss.setFont(_fx(7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(_fx(8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setObjectName("FooterBar")
        w.setFixedHeight(24)
        w.setStyleSheet("QWidget#FooterBar { background: #00080d; border-top: 1px solid #082838; }")
        lay = QHBoxLayout(w); lay.setContentsMargins(16, 0, 16, 0)

        def _fl(txt, color=C.TEXT_DIM):
            l = QLabel(txt); l.setFont(_espacado(_fx(6.6), 1.0))
            l.setStyleSheet(f"color: {color}; background: transparent; border: none;")
            return l

        lay.addWidget(_fl("[F4] Mudo  ·  [F11] Tela cheia  ·  [F2] Chave de IA"))
        # Botao visivel para trocar a chave: atalho sozinho ninguem descobre, e
        # sem isto o cliente com o limite estourado ficava sem saida.
        # Os dois botoezinhos daqui sairam em 01/09/2026: viraram a
        # engrenagem grande da barra lateral, abaixo da camera. Dois
        # caminhos para a mesma janela so confundiam o cliente.

        lay.addStretch()
        lay.addWidget(_fl("JARVIS Industries  ·  CONFIDENCIAL"))
        lay.addStretch()
        lay.addWidget(_fl("© STARK INDUSTRIES", C.PRI_DIM))
        return w

    def _on_files_selected(self, caminhos: list):
        """Varios arquivos soltos de uma vez: UM aviso so para o JARVIS, com o
        principal em `path=` e os outros em `extra=` (caminho do Windows nao
        tem "|"). Um aviso por arquivo faria ele responder tres vezes."""
        if not caminhos:
            return
        self._current_file = caminhos[0]
        nomes = []
        for c in caminhos:
            p = Path(c)
            try:
                size = _fmt_size(p.stat().st_size)
            except OSError:
                size = "?"
            nomes.append(p.name)
            self._log.append_log(f"ARQ: {p.name} ({size}) carregado")
        self._file_hint.setText(f"📎  {len(caminhos)} arquivos  ·  Diga ao JARVIS o que fazer com eles")
        if self.on_text_command:
            p = Path(caminhos[0])
            try:
                size = _fmt_size(p.stat().st_size)
            except OSError:
                size = "?"
            outros = " | ".join(f"extra={c}" for c in caminhos[1:])
            msg = (
                f"[FILE_UPLOADED] path={caminhos[0]} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | {outros} | "
                f"The user uploaded {len(caminhos)} files at once: {', '.join(nomes)}. "
                f"Briefly tell the user you can see all of them and ask what they'd like to do."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        try:
            size = _fmt_size(p.stat().st_size)
        except OSError:
            size = "?"
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Diga ao JARVIS o que fazer com ele")
        self._log.append_log(f"ARQ: {p.name} ({size}) carregado")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _show_whatsapp_overlay(self):
        """Mostra a janela central com QR/status sem abrir navegador."""
        try:
            bridge_dir = BASE_DIR / "whatsapp_bridge"
            qr_path = bridge_dir / "wa_qr.png"
            if self._whatsapp_overlay and self._whatsapp_overlay.isVisible():
                self._whatsapp_overlay.raise_()
                self._whatsapp_overlay.activateWindow()
                return
            cw = self.centralWidget()
            ov = WhatsAppQrOverlay(str(qr_path), cw)
            ow, oh = WhatsAppQrOverlay._OW, WhatsAppQrOverlay._OH
            ov.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
            ov.closed.connect(lambda: setattr(self, '_whatsapp_overlay', None))
            ov.show()
            ov.raise_()
            self._whatsapp_overlay = ov
        except Exception as e:
            self._log.append_log(f"ERRO: Falha ao abrir painel WhatsApp — {e}")

    def _open_whatsapp(self):
        """Botão visual do WhatsApp: abre a janela central e inicia a ponte uma vez."""
        self._log.append_log("SIS: Abrindo conexão do WhatsApp...")
        self._show_whatsapp_overlay()
        if self._whatsapp_connecting:
            self._log.append_log("SIS: WhatsApp já está iniciando. Aguarde o QR Code.")
            return
        self._whatsapp_connecting = True

        def _run():
            try:
                if self.on_whatsapp_clicked:
                    self.on_whatsapp_clicked()
                elif self.on_text_command:
                    self.on_text_command("conectar WhatsApp")
                else:
                    self._log_sig.emit("SIS: Módulo WhatsApp indisponível.")
            finally:
                self._whatsapp_connecting = False

        threading.Thread(target=_run, daemon=True).start()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SIS: Painel remoto não está rodando — remoto indisponível.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SIS: Não foi possível gerar a chave remota.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SIS: Chave remota gerada — manual: {manual or url}")

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SIS: Microfone silenciado.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SIS: Microfone ativo.")

    def _toggle_so_nome(self):
        self._so_com_nome = not self._so_com_nome
        self._style_nome_btn()
        nome = getattr(self, "_nome_da_ia", None) or "JARVIS"
        if self._so_com_nome:
            self._log.append_log(
                f"SIS: agora eu so respondo se voce me chamar de {nome}. "
                "Depois disso fico 30 segundos acordado.")
        else:
            self._log.append_log("SIS: voltei a responder sem precisar do nome.")
        if self.on_so_nome_changed:
            try:
                self.on_so_nome_changed(self._so_com_nome)
            except Exception as exc:
                self._log.append_log(f"ERR: {type(exc).__name__}: {str(exc)[:90]}")

    def _style_nome_btn(self):
        """Ligado = cadeado fechado e cor de destaque. Desligado = aberto.

        O TEXTO DIZ O ESTADO, nao a acao. "SO COM O NOME" quando esta ligado,
        e nao "clique para ligar": cartao que anuncia a acao faz o dono ler o
        contrario do que esta valendo — foi o que ja acontecia com o microfone
        antes de ele passar a dizer ATIVO/MUDO.
        """
        if self._so_com_nome:
            self._nome_btn.setText("◆\nSÓ COM\nO NOME")
            cor, fundo = C.ACC2, "#1a1400"
        else:
            self._nome_btn.setText("◇\nSEMPRE\nOUVINDO")
            cor, fundo = C.PRI, C.PANEL
        if isinstance(self._nome_btn, ControlCard):
            self._nome_btn.set_card_theme(cor, fundo)
        else:
            self._nome_btn.setStyleSheet(self._control_card_style(cor, fundo))

    def _acordado_ate(self, segundos: float) -> None:
        """O main avisa quando a janela de 30 s abre, so para a tela mostrar."""
        if not self._so_com_nome:
            return
        self._nome_btn.setText("●\nACORDADO\n30s")
        if isinstance(self._nome_btn, ControlCard):
            self._nome_btn.set_card_theme(C.GREEN, "#00140a")
        # Volta ao cadeado quando a janela fecha.
        QTimer.singleShot(int(max(0.5, segundos) * 1000), self._style_nome_btn)

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇\nMICROFONE\nMUDO")
            if isinstance(self._mute_btn, ControlCard):
                self._mute_btn.set_card_theme(C.MUTED_C, "#140006")
            else:
                self._mute_btn.setStyleSheet(self._control_card_style(C.MUTED_C, "#140006"))
        else:
            self._mute_btn.setText("🎙\nMICROFONE\nATIVO")
            if isinstance(self._mute_btn, ControlCard):
                self._mute_btn.set_card_theme(C.GREEN, "#00140a")
            else:
                self._mute_btn.setStyleSheet(self._control_card_style(C.GREEN, "#00140a"))

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"Você: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    _ESTADOS_DE_TAREFA = {"EDITING_IMAGE", "CREATING_IMAGE", "CREATING_SITE", "SEARCHING_LEADS"}
    # Enquanto uma tarefa longa roda, estes avisos comuns são ignorados. Sem
    # isto o painel voltava para OUVINDO no meio da edição de uma imagem: nove
    # pontos diferentes do programa mandam "LISTENING", e qualquer um deles
    # atropelava o aviso da tarefa. O cliente ficava sem saber se o JARVIS
    # estava trabalhando ou parado.
    _AVISOS_COMUNS = {"LISTENING", "SPEAKING", "THINKING", "PROCESSING"}
    _TETO_DA_TAREFA_S = 900   # rede de segurança: 15 min e o painel se solta

    def _apply_state(self, state: str):
        alvo = str(state).upper()

        # Fim explícito da tarefa: só quem começou pode encerrar.
        if alvo == "TAREFA_FIM":
            self._tarefa_ativa_desde = 0.0
            state, alvo = "LISTENING", "LISTENING"
        elif alvo in self._ESTADOS_DE_TAREFA:
            self._tarefa_ativa_desde = time.monotonic()
        elif getattr(self, "_tarefa_ativa_desde", 0.0):
            if (time.monotonic() - self._tarefa_ativa_desde) > self._TETO_DA_TAREFA_S:
                self._tarefa_ativa_desde = 0.0      # travou: solta o painel
            elif alvo in self._AVISOS_COMUNS:
                return                              # a tarefa continua na tela

        self.hud.state    = state
        # Entrando numa tarefa longa, desliga o "falando" para o painel nao
        # continuar mostrando a onda de voz durante os minutos de trabalho.
        if str(state).upper() in self._ESTADOS_DE_TAREFA:
            self.hud.speaking = False
        else:
            self.hud.speaking = (state == "SPEAKING")
        if hasattr(self, "_rail_state_lbl"):
            labels = {
                "LISTENING": ("●\nOUVINDO", C.GREEN),
                "THINKING": ("◆\nPENSANDO", C.ACC2),
                "PROCESSING": ("◆\nPROCESSANDO", C.ACC2),
                "SPEAKING": ("●\nFALANDO", C.ACC),
                "MUTED": ("⊘\nMUDO", C.MUTED_C),
                "INITIALISING": ("○\nINICIANDO", C.PRI),
                "IDLE": ("○\nINATIVO", C.TEXT_DIM),
                "INACTIVE": ("○\nINATIVO", C.TEXT_DIM),
                # Tarefas longas: o cliente precisa ver que algo esta acontecendo.
                "EDITING_IMAGE": ("◆\nEDITANDO\nIMAGEM", C.ACC2),
                "CREATING_IMAGE": ("◆\nCRIANDO\nIMAGEM", C.ACC2),
                "CREATING_SITE": ("◆\nCRIANDO\nSITE", C.ACC2),
                "SEARCHING_LEADS": ("◆\nBUSCANDO\nLEADS", C.ACC2),
            }
            text, color = labels.get(str(state).upper(), (f"○\n{state}", C.PRI))
            # topo novo: uma linha so, numa pilula da cor do estado
            simbolo, _, resto = text.partition("\n")
            self._rail_state_lbl.setText(f"{simbolo}  {resto.replace(chr(10), ' ')}")
            self._rail_state_lbl.setStyleSheet(self._estilo_pilula(color))

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            # A CHAVE DO GEMINI DEIXOU DE SER OBRIGATORIA (05/09/2026): o
            # JARVIS pensa, ve e fala pela nossa VPS mesmo sem chave nenhuma do
            # cliente. Exigi-la aqui so barrava quem nao tem — ver `_submit`.
            return bool(d.get("os_system"))
        except Exception:
            return False

    def _show_personas(self, abrir_em: str | None = None):
        """Painel de escolha: personagem (a boca) e personalidade (a cabeca).

        Clicou, aplicou. Sem botao de salvar de proposito — o cliente quer ouvir
        a diferenca na hora, e um passo a mais so atrapalha.
        """
        try:
            from core.personas import (PERSONAGENS, PERSONALIDADES,
                                       PERSONAGEM_PADRAO, PERSONALIDADE_PADRAO)
        except Exception as exc:
            casca0 = getattr(self, "_ui", None)
            if casca0 is not None:
                casca0.write_log(f"ERR: nao consegui abrir as vozes — {str(exc)[:100]}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("JARVIS · Voz e Jeito de Ser")
        dlg.setMinimumWidth(720)
        dlg.setStyleSheet(f"QDialog {{ background: {C.BG}; }}")
        raiz = QVBoxLayout(dlg)
        raiz.setContentsMargins(18, 16, 18, 16)
        raiz.setSpacing(12)

        topo = QLabel("Escolha como o JARVIS fala e como ele se comporta. "
                      "Clique e ele muda na hora.")
        topo.setFont(_fx(9))
        topo.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        raiz.addWidget(topo)

        # ── BOTOES DE SECAO ──────────────────────────────────────────────────
        #
        # Uma pagina de cada vez. Antes tudo ficava empilhado e a janela passava
        # da tela: o Diego nao conseguia ver os campos do fim (01/09/2026).
        barra = QHBoxLayout()
        barra.setSpacing(6)
        paginas = QStackedWidget()
        _botoes_secao = {}

        def _ir_para(indice):
            def _clique():
                paginas.setCurrentIndex(indice)
                for i, b in _botoes_secao.items():
                    ativo = (i == indice)
                    b.setStyleSheet(
                        f"QPushButton {{ color: {C.TEXT if ativo else C.PRI};"
                        f" background: {'rgba(0,229,255,0.12)' if ativo else 'transparent'};"
                        f" border: 1px solid {C.PRI if ativo else C.BORDER_B};"
                        f" border-radius: 4px; padding: 5px 14px; }}"
                        f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.PRI}; }}")
            return _clique

        def _secao(rotulo, widget):
            indice = paginas.count()
            paginas.addWidget(widget)
            b = QPushButton(rotulo)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(_fx(8, QFont.Weight.Bold))
            b.setFixedHeight(28)
            b.clicked.connect(_ir_para(indice))
            _botoes_secao[indice] = b
            barra.addWidget(b)
            return indice

        raiz.addLayout(barra)
        raiz.addWidget(paginas)

        # ── CHAVE ────────────────────────────────────────────────────────────
        pag_chave = QWidget()
        lay_chave = QVBoxLayout(pag_chave)
        lay_chave.setContentsMargins(0, 8, 0, 0)
        exp_chave = QLabel(
            "As chaves de IA ficam guardadas so neste computador.\n"
            "Sem a chave ele ainda ouve e fala, mas nao pensa.")
        exp_chave.setFont(_fx(9))
        exp_chave.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay_chave.addWidget(exp_chave)
        b_chave = QPushButton("ABRIR O CADASTRO DE CHAVES")
        b_chave.setCursor(Qt.CursorShape.PointingHandCursor)
        b_chave.setFont(_fx(9, QFont.Weight.Bold))
        b_chave.setFixedHeight(34)
        b_chave.setStyleSheet(
            f"QPushButton {{ color: {C.PRI}; background: transparent;"
            f" border: 1px solid {C.BORDER_B}; border-radius: 4px; padding: 0 18px; }}"
            f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.PRI}; }}")

        def _abrir_chaves():
            try:
                dlg.accept()
                self._show_setup()
            except Exception as exc:
                _aviso_chave = getattr(self, "_ui", None)
                if _aviso_chave is not None:
                    _aviso_chave.write_log(f"ERR: nao abri as chaves — {str(exc)[:90]}")
        b_chave.clicked.connect(_abrir_chaves)
        lay_chave.addWidget(b_chave)
        lay_chave.addStretch()
        _secao("CHAVE", pag_chave)

        # ── VOZ E JEITO ──────────────────────────────────────────────────────
        #
        # A pagina virou VERTICAL em 05/09/2026 (antes era so a fileira de
        # colunas) para caber, embaixo delas, o liga/desliga da SAUDACAO. As
        # colunas continuam iguais, dentro de `colunas`.
        pag_voz = QWidget()
        _voz_vert = QVBoxLayout(pag_voz)
        _voz_vert.setContentsMargins(0, 8, 0, 0)
        _voz_vert.setSpacing(10)
        colunas = QHBoxLayout()
        colunas.setContentsMargins(0, 0, 0, 0)
        colunas.setSpacing(16)
        _voz_vert.addLayout(colunas)
        _secao("VOZ E JEITO", pag_voz)

        # Guarda os botoes para conseguir realcar o escolhido depois.
        botoes = {"personagem": {}, "personalidade": {}, "microfone": {}, "saida": {}}

        def _realcar(tipo, escolhido):
            for chave, b in botoes.get(tipo, {}).items():
                ativo = (chave == escolhido)
                b.setStyleSheet(
                    f"QPushButton {{ text-align: left; padding: 7px 10px;"
                    f" border: 1px solid {C.PRI if ativo else C.BORDER};"
                    f" border-radius: 4px;"
                    f" background: {C.PRI_GHO if ativo else 'transparent'};"
                    f" color: {C.TEXT if ativo else C.TEXT_MED}; }}"
                    f"QPushButton:hover {{ border-color: {C.BORDER_B}; color: {C.TEXT}; }}"
                )

        def _coluna(titulo, itens, tipo, atual):
            caixa = QVBoxLayout()
            caixa.setSpacing(6)
            cab = QLabel(titulo)
            cab.setFont(_fx(9, QFont.Weight.Bold))
            cab.setStyleSheet(f"color: {C.PRI}; background: transparent;")
            caixa.addWidget(cab)
            for chave, item in itens.items():
                rotulo = item['nome'] + chr(10) + item['desc']
                b = QPushButton(rotulo)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setFont(_fx(8))
                b.setMinimumHeight(38)
                b.clicked.connect(
                    lambda _=False, k=chave, tp=tipo: (_aplicar(tp, k), _realcar(tp, k)))
                botoes[tipo][chave] = b
                caixa.addWidget(b)
            caixa.addStretch()
            return caixa

        casca = getattr(self, "_ui", None)

        def _aviso(msg):
            if casca is not None:
                casca.write_log(msg)

        def _aplicar(tipo, chave):
            # Nada aqui pode levantar: excecao em slot do Qt fecha a janela.
            try:
                cb = getattr(casca, "on_persona_change", None) if casca else None
                if cb is None:
                    _aviso("SIS: escolha registrada, mas o motor nao respondeu.")
                    return
                threading.Thread(target=cb, args=(tipo, chave), daemon=True).start()
            except Exception as exc:
                _aviso(f"ERR: nao consegui trocar — {str(exc)[:110]}")

        _escolha = (getattr(casca, "persona_atual", None) or {}) if casca else {}
        atual_pg = _escolha.get("personagem", PERSONAGEM_PADRAO)
        atual_pl = _escolha.get("personalidade", PERSONALIDADE_PADRAO)
        colunas.addLayout(_coluna("VOZ  ·  como ele soa", PERSONAGENS, "personagem", atual_pg))
        colunas.addLayout(_coluna("JEITO  ·  como ele age", PERSONALIDADES, "personalidade", atual_pl))

        # ── SAUDACAO AO ABRIR: liga e desliga (05/09/2026) ───────────────────
        #
        # Ela entrou no lugar dos dois recados de tecnico que apareciam no chat
        # ao abrir. Nem todo mundo quer ser cumprimentado toda vez — quem nao
        # quiser desliga aqui, e o JARVIS abre calado.
        #
        # Um botao SO, que alterna: dois botoes (LIGAR/DESLIGAR) obrigam o
        # cliente a descobrir qual esta valendo. O rotulo ja diz o estado.
        linha_saud = QHBoxLayout()
        linha_saud.setSpacing(10)
        rot_saud = QLabel("SAUDAÇÃO AO ABRIR  ·  ele te cumprimenta quando o programa abre")
        rot_saud.setFont(_fx(8))
        rot_saud.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        b_saud = QPushButton()
        b_saud.setCursor(Qt.CursorShape.PointingHandCursor)
        b_saud.setFont(_fx(8, QFont.Weight.Bold))
        b_saud.setFixedHeight(28)
        b_saud.setFixedWidth(130)
        # `_escolha` e o que estava salvo quando o painel abriu; nasce LIGADA.
        _saud = {"ligada": bool(_escolha.get("saudacao", True))}

        def _pintar_saud():
            ligada = _saud["ligada"]
            b_saud.setText("LIGADA" if ligada else "DESLIGADA")
            b_saud.setStyleSheet(
                f"QPushButton {{ border: 1px solid {C.PRI if ligada else C.BORDER};"
                f" border-radius: 4px;"
                f" background: {C.PRI_GHO if ligada else 'transparent'};"
                f" color: {C.TEXT if ligada else C.TEXT_MED}; }}"
                f"QPushButton:hover {{ border-color: {C.BORDER_B}; color: {C.TEXT}; }}")

        def _virar_saud():
            _saud["ligada"] = not _saud["ligada"]
            _pintar_saud()
            _aplicar("saudacao", _saud["ligada"])

        b_saud.clicked.connect(_virar_saud)
        _pintar_saud()
        linha_saud.addWidget(rot_saud)
        linha_saud.addStretch()
        linha_saud.addWidget(b_saud)
        _voz_vert.addLayout(linha_saud)

        # ── MICROFONE ────────────────────────────────────────────────────────
        # O cliente instala em qualquer PC, e o microfone dele pode ser qualquer
        # coisa. Adivinhar pelo nome nao serve: em 30/08 tres aparelhos abriram
        # sem erro e entregaram ZERO bloco. Aqui cada um mostra se FUNCIONA de
        # verdade, medindo os blocos que chegam.
        col_mic = QVBoxLayout()
        col_mic.setSpacing(6)
        cab_mic = QLabel("MICROFONE  ·  por onde ele ouve")
        cab_mic.setFont(_fx(9, QFont.Weight.Bold))
        cab_mic.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        col_mic.addWidget(cab_mic)

        estado_mic = QLabel("Clique em Testar para ver qual funciona.")
        estado_mic.setFont(_fx(8))
        estado_mic.setWordWrap(True)
        estado_mic.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        botoes["microfone"] = {}

        def _montar_microfones(testar_todos=False):
            while col_mic.count() > 1:
                item = col_mic.takeAt(1)
                w = item.widget()
                if w is not None and w is not estado_mic:
                    w.deleteLater()
            botoes["microfone"].clear()
            try:
                from core.audio_dispositivos import listar, testar as testar_mic
            except Exception as exc:
                estado_mic.setText(f"Nao consegui listar: {str(exc)[:70]}")
                col_mic.addWidget(estado_mic)
                return
            lista = [{"indice": None, "nome": "Padrao do Windows",
                      "api": "o que o sistema escolher"}] + listar()
            for ap in lista:
                marca = ""
                if testar_todos:
                    r = testar_mic(ap["indice"])
                    marca = ("  ✓ funcionando" if r["ok"]
                             else "  ✗ " + (r["erro"][:34] or "sem sinal"))
                b = QPushButton(ap["nome"][:38] + chr(10) + ap["api"][:30] + marca)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setFont(_fx(8))
                b.setMinimumHeight(38)
                b.clicked.connect(
                    lambda _=False, k=ap["indice"]: (_aplicar("microfone", k),
                                                     _realcar("microfone", k)))
                botoes["microfone"][ap["indice"]] = b
                col_mic.addWidget(b)
            atual_mic = (getattr(casca, "persona_atual", None) or {}).get("microfone")
            _realcar("microfone", atual_mic)
            col_mic.addWidget(estado_mic)

        btn_testar = QPushButton("TESTAR TODOS")
        btn_testar.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_testar.setFont(_fx(8, QFont.Weight.Bold))
        btn_testar.setFixedHeight(24)
        btn_testar.setStyleSheet(
            f"QPushButton {{ color: {C.ACC2}; background: transparent;"
            f" border: 1px solid {C.BORDER_B}; border-radius: 4px; }}"
            f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.PRI}; }}")

        def _testar_todos():
            estado_mic.setText("Testando cada aparelho, aguarde...")
            QApplication.processEvents()
            try:
                _montar_microfones(testar_todos=True)
                estado_mic.setText("Escolha um que esteja marcado com ✓.")
            except Exception as exc:
                estado_mic.setText(f"Falhou: {str(exc)[:70]}")

        btn_testar.clicked.connect(_testar_todos)
        _montar_microfones()
        col_mic.addWidget(btn_testar)

        # ── SAIDA DE SOM ─────────────────────────────────────────────────────
        # O JARVIS escolhia sozinho para onde falar e errava: o Diego ouve o PC
        # pelo notebook (AudioRelay) e a voz ia para os alto-falantes USB.
        # Falava certo, no lugar errado — e parecia mudo. Testar por medicao nao
        # serve aqui: so o ouvido do cliente sabe se o som chegou nele, entao
        # cada aparelho tem um bip para ele ouvir e decidir.
        cab_saida = QLabel("SOM  ·  por onde ele fala")
        cab_saida.setFont(_fx(9, QFont.Weight.Bold))
        cab_saida.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        col_mic.addSpacing(10)
        col_mic.addWidget(cab_saida)

        dica_saida = QLabel("Clique para ouvir um bip e adotar.")
        dica_saida.setFont(_fx(8))
        dica_saida.setWordWrap(True)
        dica_saida.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        botoes["saida"] = {}
        try:
            from core.audio_dispositivos import listar_saidas
            saidas = [{"indice": None, "nome": "Padrao do Windows", "api": ""}] + listar_saidas()
        except Exception:
            saidas = [{"indice": None, "nome": "Padrao do Windows", "api": ""}]

        for ap in saidas[:8]:
            rot_s = ap["nome"][:38] + (chr(10) + ap["api"][:30] if ap["api"] else "")
            bs = QPushButton(rot_s)
            bs.setCursor(Qt.CursorShape.PointingHandCursor)
            bs.setFont(_fx(8))
            bs.setMinimumHeight(34)
            bs.clicked.connect(
                lambda _=False, k=ap["indice"]: (_aplicar("saida", k), _realcar("saida", k)))
            botoes["saida"][ap["indice"]] = bs
            col_mic.addWidget(bs)

        col_mic.addWidget(dica_saida)
        _realcar("saida", (getattr(casca, "persona_atual", None) or {}).get("saida"))
        pag_som = QWidget()
        lay_som = QHBoxLayout(pag_som)
        lay_som.setContentsMargins(0, 8, 0, 0)
        lay_som.addLayout(col_mic)
        _secao("MICROFONE E SOM", pag_som)
        _realcar("personagem", atual_pg)
        _realcar("personalidade", atual_pl)

        # ── SOBRE VOCE ───────────────────────────────────────────────────────
        #
        # Ate 01/09/2026 o JARVIS chamava TODO cliente de "Diego": o nome estava
        # escrito na pedra dentro do prompt. Aqui o dono diz quem e, e as treze
        # personalidades passam a falar com ELE.
        #
        # Grava ao sair do campo, sem botao de salvar — botao e uma coisa a mais
        # para o cliente esquecer de clicar.
        try:
            from core.dono import CAMPOS as _CAMPOS_DONO, carregar as _dono_carregar
            from core.dono import salvar as _dono_salvar

            _atual_dono = _dono_carregar()

            cab_dono = QLabel("SOBRE VOCE  ·  para ele te conhecer")
            cab_dono.setFont(_fx(9, QFont.Weight.Bold))
            cab_dono.setStyleSheet(f"color: {C.PRI}; background: transparent;")
            pag_dono = QWidget()
            lay_dono = QVBoxLayout(pag_dono)
            lay_dono.setContentsMargins(0, 8, 0, 0)
            lay_dono.addWidget(cab_dono)

            grade = QVBoxLayout()
            grade.setSpacing(4)

            def _gravar(chave, campo):
                def _ao_sair():
                    try:
                        _dono_salvar({chave: campo.text()})
                        if casca is not None and chave == "como_chamar":
                            nome = campo.text().strip()
                            if nome:
                                casca.write_log(f"SIS: agora ele te chama de {nome}.")
                    except Exception as exc:
                        _aviso(f"ERR: nao consegui salvar — {str(exc)[:90]}")
                return _ao_sair

            for _chave, (_rotulo, _dica) in _CAMPOS_DONO.items():
                linha_d = QHBoxLayout()
                lab = QLabel(_rotulo)
                lab.setFont(_fx(8))
                lab.setFixedWidth(250)
                lab.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                campo = QLineEdit(_atual_dono.get(_chave, ""))
                campo.setPlaceholderText(_dica)
                campo.setFont(_fx(8))
                campo.setStyleSheet(
                    f"QLineEdit {{ color: {C.TEXT}; background: transparent;"
                    f" border: 1px solid {C.BORDER_B}; border-radius: 4px; padding: 3px 6px; }}"
                    f"QLineEdit:focus {{ border-color: {C.PRI}; }}")
                campo.editingFinished.connect(_gravar(_chave, campo))
                linha_d.addWidget(lab)
                linha_d.addWidget(campo)
                grade.addLayout(linha_d)
            lay_dono.addLayout(grade)
            lay_dono.addStretch()
            _secao("SOBRE VOCE", pag_dono)
        except Exception as _exc_dono:
            _aviso(f"ERR: nao montei os dados do dono — {str(_exc_dono)[:90]}")

        # ── CORES (08/10/2026) ───────────────────────────────────────────────
        _indice_cores = None
        try:
            _indice_cores = _secao("CORES", self._pagina_cores())
        except Exception as _exc_cores:
            import traceback as _tb_cores
            _tb_cores.print_exc()
            _aviso(f"ERR: nao montei a aba de cores — {str(_exc_cores)[:90]}")

        # Abre em VOZ E JEITO: e a secao que o cliente mais visita.
        try:
            _ir_para(_indice_cores if (abrir_em == "cores" and _indice_cores is not None) else 1)()
        except Exception:
            pass

        rodape = QLabel("Ao escolher uma voz ele fala uma frase de teste. "
                        "A escolha fica salva para as proximas vezes.")
        rodape.setFont(_fx(8))
        rodape.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        raiz.addWidget(rodape)

        fechar = QPushButton("FECHAR")
        fechar.setCursor(Qt.CursorShape.PointingHandCursor)
        fechar.setFont(_fx(8, QFont.Weight.Bold))
        fechar.setFixedHeight(26)
        fechar.setStyleSheet(
            f"QPushButton {{ color: {C.PRI}; background: transparent;"
            f" border: 1px solid {C.BORDER_B}; border-radius: 4px; padding: 0 18px; }}"
            f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.PRI}; }}"
        )
        fechar.clicked.connect(dlg.accept)
        linha = QHBoxLayout()
        linha.addStretch()
        linha.addWidget(fechar)
        raiz.addLayout(linha)

        dlg.exec()

    def _show_setup(self):
        if self._overlay is not None:
            self._overlay.raise_()
            self._overlay.show()
            return
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 455
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        ov.raise_()
        ov.setFocus()
        self._overlay = ov

    def _on_setup_done(self, key: str, openrouter_key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        data = {}
        if API_FILE.exists():
            try:
                data = json.loads(API_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        # Campo deixado em branco NAO apaga a chave que ja existia. Esta tela
        # tambem serve para trocar a chave depois de instalado.
        # (o `openrouter_key` continua no argumento so para nao mexer no sinal
        #  do Qt; ele chega sempre vazio desde 05/09/2026 e nao e mais gravado.)
        gemini_final = key.strip() or data.get("gemini_api_key", "")
        data.pop("openrouter_api_key", None)
        data.pop("openrouter_vision_model", None)
        API_FILE.write_text(
            json.dumps({
                **data,
                "gemini_api_key": gemini_final,
                "os_system": os_name,
            }, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._log.append_log(f"SIS: Inicializado. SO={os_name.upper()}. JARVIS online.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        # Janela em ingles para o cliente do Canada (26/09/2026): botoes,
        # rotulos e o status desenhado passam pelo core/interface_en.py.
        try:
            from core.idioma import em_ingles as _em_ingles_ui  # noqa: PLC0415
            if _em_ingles_ui():
                from core.interface_en import instalar as _instalar_en  # noqa: PLC0415
                _instalar_en(self._app)
        except Exception as _exc_en:
            print(f"[ui] interface em ingles nao ligou: {_exc_en}")
        icon_path = _app_icon_path()
        if icon_path.exists():
            self._app.setWindowIcon(QIcon(str(icon_path)))
        self._win = MainWindow(face_path)
        # A janela precisa alcancar a casca: `write_log`, `on_persona_change` e
        # `persona_atual` vivem AQUI, nao no MainWindow. Sem esta ponte, o painel
        # da engrenagem chamava metodo inexistente e a excecao dentro do slot do
        # Qt **derrubava o programa** (30/08/2026, primeiro clique do Diego).
        self._win._ui = self
        self._win.show()
        if not self._win._ready:
            QTimer.singleShot(0, self._win._show_setup)
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_whatsapp_clicked(self):
        return self._win.on_whatsapp_clicked

    @on_whatsapp_clicked.setter
    def on_whatsapp_clicked(self, cb):
        self._win.on_whatsapp_clicked = cb

    @property
    def so_com_nome(self) -> bool:
        return self._win._so_com_nome

    @so_com_nome.setter
    def so_com_nome(self, v: bool):
        # Sem passar pelo `_toggle`: isto e a RESTAURACAO do que estava salvo,
        # e o toggle escreveria no chat "agora eu so respondo..." toda vez que
        # o programa abrisse.
        self._win._so_com_nome = bool(v)
        self._win._style_nome_btn()

    @property
    def on_so_nome_changed(self):
        return self._win.on_so_nome_changed

    @on_so_nome_changed.setter
    def on_so_nome_changed(self, cb):
        self._win.on_so_nome_changed = cb

    def nome_da_ia_e(self, nome: str) -> None:
        """O main diz qual e o nome da voz atual, para o aviso da tela."""
        self._win._nome_da_ia = str(nome or "JARVIS")

    def acordado_por(self, segundos: float) -> None:
        self._win._acordado_ate(segundos)

    @property
    def on_computer_use(self):
        return getattr(self._win, "on_computer_use", None)

    @on_computer_use.setter
    def on_computer_use(self, cb):
        self._win.on_computer_use = cb

    @property
    def on_computer_use_estado(self):
        return getattr(self._win, "on_computer_use_estado", None)

    @on_computer_use_estado.setter
    def on_computer_use_estado(self, cb):
        self._win.on_computer_use_estado = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def nivel_do_microfone(self, nivel: float) -> None:
        """Repasse do nivel do microfone para a faixa de baixo.

        Mesma razao do `mostrar_aviso_pc` logo abaixo: o `main.py` so enxerga
        ESTA casca, entao um metodo que exista so na `MainWindow` nunca e
        chamado — e o defeito some calado, sem quebrar nada.
        """
        try:
            self._win.nivel_do_microfone(nivel)
        except Exception:
            pass

    def cowork_estado(self, estado) -> None:
        """Thread-safe: o Cowork avisa o que esta fazendo (vem da thread dele)."""
        try:
            self._win._cowork_sig.emit(dict(estado or {}))
        except Exception:
            pass

    def cowork_escolher_pasta(self) -> None:
        """Thread-safe: abre a janela de escolher a pasta do projeto."""
        try:
            self._win._cowork_pasta_sig.emit()
        except Exception:
            pass

    @property
    def on_cowork_acao(self):
        return getattr(self._win, "on_cowork_acao", None)

    @on_cowork_acao.setter
    def on_cowork_acao(self, cb):
        self._win.on_cowork_acao = cb

    def voz_do_jarvis(self, niveis, passo: float, inicio: float) -> None:
        """Volume da voz do JARVIS para o nucleo pulsar junto (05/10/2026).

        `niveis`: uma fatia a cada `passo` segundos, a primeira tocando em
        `inicio` (relogio time.monotonic). Vem da linha do audio, entao o
        nucleo so guarda numa fila; quem desenha e o cronometro dele.
        """
        try:
            self._win.hud.falou(niveis, passo, inicio)
        except Exception:
            pass

    def mostrar_aviso_pc(self, ligado: bool, texto: str = "") -> None:
        """A faixa "JARVIS esta usando o computador" — repassada para a janela.

        ESTE REPASSE E O CONSERTO (04/09/2026). O laco do computer use recebe
        o `player`, que e ESTA classe (a casca), e procura nele o metodo
        `mostrar_aviso_pc` com `getattr`. No Bob o metodo existia so na
        `MainWindow` la dentro: o `getattr` devolvia None, o laco seguia em
        frente sem reclamar (e proposital — nem toda interface tem faixa) e a
        faixa NUNCA aparecia. Defeito silencioso, do tipo que nao quebra teste
        nenhum: some so o aviso, e o dono e quem descobre, mexendo no mouse no
        meio da tarefa.
        """
        try:
            self._win.mostrar_aviso_pc(bool(ligado), str(texto or ""))
        except Exception:
            pass

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    def get_latest_camera_frame_bytes(self, max_age: float = 4.0) -> bytes | None:
        """Thread-safe: latest webcam/Vision Control frame for one-shot analysis."""
        return self._win.get_latest_camera_frame_bytes(max_age=max_age)

    def set_vision_control_active(self, active: bool, status: str = "") -> None:
        """Thread-safe: show/hide Vision Control status in the left SYS MONITOR panel."""
        self._win._vision_panel_sig.emit(bool(active), str(status or ""))

    def update_vision_control_frame(self, img_bytes: bytes, status: str = "") -> None:
        """Thread-safe: update embedded Vision Control webcam preview."""
        self._win._vision_frame_sig.emit(img_bytes, str(status or ""))

    def open_whatsapp_qr(self) -> None:
        """Thread-safe: mostra o QR Code do WhatsApp dentro da interface."""
        try:
            self._win._open_whatsapp_sig.emit()
        except Exception:
            pass

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")
