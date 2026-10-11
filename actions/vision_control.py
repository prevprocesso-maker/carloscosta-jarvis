# actions/vision_control.py
# JARVIS — Vision Control / Controle por Visao
#
# Versao integrada com a logica do Gesture Control que funcionava no projeto antigo.
# - Usa MediaPipe Hands quando mp.solutions existe.
# - Usa MediaPipe Tasks + models/gesture_recognizer.task quando mp.solutions nao existe.
# - Nao usa fallback por cor de pele para mover mouse, evitando o bug do corpo/rosto mover o cursor.
# - Webcam embutida no painel lateral do JARVIS.

from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import threading
import time
import types as _types
from pathlib import Path
from types import SimpleNamespace

try:
    import cv2
except Exception as _exc:  # pragma: no cover
    cv2 = None
    _ERRO_CV2 = f"{type(_exc).__name__}: {_exc}"
else:
    _ERRO_CV2 = ""


# ── O VISION CONTROL NAO PODE MORRER POR CAUSA DE UM DESENHO (22/09/2026) ────
#
# Queixa de cliente (16/09): "[VisionControl] mediapipe nao esta instalado ou
# nao carregou. Rode 1_instalar_dependencias.bat" — e esse .bat nem existe no
# pacote. A causa, provada na epoca: o `mediapipe` carrega
# `tasks/python/vision/drawing_utils.py`, que faz `import matplotlib.pyplot` no
# topo. O pacote do cliente tinha perdido a pasta `matplotlib` no
# emagrecimento, entao o import inteiro do mediapipe morria e o modulo ficava
# `mp = None`. Na epoca a correcao foi copiar 21 MB de matplotlib para o
# instalador — o que conserta quem instalar DEPOIS, e deixa na mao todo cliente
# que ja tem o pacote antigo.
#
# Aqui a correcao e definitiva e nao depende do pacote: NOS NAO USAMOS o
# `drawing_utils` (as linhas da mao sao desenhadas com cv2, logo abaixo, no
# `HAND_CONNECTIONS`). Entao, se o matplotlib de verdade nao existir, entra um
# DUBLE no lugar dele so para o import do mediapipe passar. Se o matplotlib
# existir, nada disto acontece — o duble so nasce quando falta.
#
# Regra: dublar SO o que e enfeite. Nunca numpy, cv2 ou protobuf — sem eles o
# mediapipe nao funcionaria de verdade, e um duble esconderia o defeito.
_DUBLAVEIS = ("matplotlib", "mpl_toolkits", "seaborn", "pandas")


def _duble_de_modulo(nome: str) -> None:
    """Poe um modulo vazio no lugar de um que so serve para desenhar grafico."""
    if nome in sys.modules:
        return
    falso = _types.ModuleType(nome)
    falso.__path__ = []                  # deixa `import x.y` funcionar
    falso.__spec__ = None
    falso.__duble_do_jarvis__ = True

    def _qualquer_coisa(*_a, **_k):      # pyplot.figure(), plt.plot()...
        return None

    falso.__getattr__ = lambda _n: _qualquer_coisa
    sys.modules[nome] = falso


def _esquecer_mediapipe() -> None:
    """Tira da memoria o meio-mediapipe que ficou da tentativa que falhou.

    Sem isto a segunda tentativa nao adianta nada: o Python guarda em
    `sys.modules` o pedaco que ja tinha carregado antes do erro, e
    `mediapipe/tasks/python/vision/__init__.py` explode com
    'NameError: name core is not defined' — o import parcial, nao a falta de
    verdade. Medido no python do proprio pacote.
    """
    for nome in list(sys.modules):
        if nome == "mediapipe" or nome.startswith("mediapipe."):
            del sys.modules[nome]


def _importar_mediapipe():
    """Importa o mediapipe, dublando enfeite que falte. Devolve (mp, erro)."""
    ultimo = ""
    for _ in range(6):                   # 6 tentativas: uma por peca que falte
        try:
            import mediapipe as _mp  # noqa: PLC0415
            return _mp, ""
        except ModuleNotFoundError as exc:
            faltou = str(getattr(exc, "name", "") or "")
            raiz = faltou.split(".")[0]
            ultimo = f"falta o modulo '{faltou}'"
            if raiz and raiz in _DUBLAVEIS:
                # So enfeite: poe o duble (a raiz e o modulo exato) e tenta de novo.
                _duble_de_modulo(raiz)
                if faltou != raiz:
                    _duble_de_modulo(faltou)
                _esquecer_mediapipe()
                continue
            return None, ultimo
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"
    return None, ultimo or "o mediapipe nao carregou"


mp, _ERRO_MEDIAPIPE = _importar_mediapipe()

try:
    from mediapipe.tasks.python import vision as mp_vision
    from mediapipe.tasks import python as mp_python
except Exception as _exc:  # pragma: no cover
    mp_vision = None
    mp_python = None
    if not _ERRO_MEDIAPIPE:
        _ERRO_MEDIAPIPE = f"mediapipe.tasks fora: {type(_exc).__name__}: {_exc}"

try:
    import pyautogui
except Exception as _exc:  # pragma: no cover
    pyautogui = None
    _ERRO_PYAUTOGUI = f"{type(_exc).__name__}: {_exc}"
else:
    _ERRO_PYAUTOGUI = ""


def diagnostico() -> str:
    """O que falta para o Vision Control funcionar, em uma linha."""
    faltas = []
    if cv2 is None:
        faltas.append(f"opencv ({_ERRO_CV2})")
    if mp is None:
        faltas.append(f"mediapipe ({_ERRO_MEDIAPIPE})")
    if pyautogui is None:
        faltas.append(f"pyautogui ({_ERRO_PYAUTOGUI})")
    return "; ".join(faltas)

BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = BASE_DIR / "models" / "gesture_recognizer.task"
CONTROL_NAME = "Vision Control"

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]

# Ajustes finos do mouse/gestos.
# Valores maiores deixam o controle mais sensivel e facilitam pinça/punho.
MOVE_SENSITIVITY = 1.34
DRAG_SENSITIVITY = 1.62
# Arraste: suavizacao propria para o punho nao tremer.
DRAG_HAND_SMOOTH_ALPHA = 0.22
DRAG_CURSOR_SMOOTH_ALPHA = 0.34
DRAG_RELATIVE_DEADZONE = 0.0058
DRAG_ABSOLUTE_DEADZONE_PX = 10
DRAG_LOST_HAND_DRAW_GRACE = 1.25
DRAG_LOST_HAND_INERTIA_GRACE = 0.34
PINCH_CLOSE_MIN = 0.082
PINCH_CLOSE_SCALE = 0.43
PINCH_RELEASE_MIN = 0.122
PINCH_RELEASE_SCALE = 0.58
PINCH_DOUBLE_HOLD_SECONDS = 0.24
# Quando a pinça começa a fechar, congela o cursor na posição atual.
# Isso evita o cursor descer junto com o dedo antes do clique.
PINCH_PRELOCK_MIN = 0.150
PINCH_PRELOCK_SCALE = 0.72
PINCH_LOCK_RELEASE_GRACE = 0.16
FIST_MEAN_DISTANCE_THRESHOLD = 0.268
# 3 dedos dobrados evita confundir pinça com punho, sem quebrar o arraste.
FIST_FOLDED_MIN = 3
OPEN_HAND_MEAN_DISTANCE_THRESHOLD = 0.270
FIST_HOLD_DELAY = 0.10
FIST_RELEASE_GRACE = 0.55
NO_HAND_DRAG_GRACE = 1.05
PINCH_BLOCK_AFTER_DRAG = 0.45
PROCESS_INTERVAL_NORMAL = 0.035
PROCESS_INTERVAL_DRAG = 0.010




def _gesture_model_path() -> Path:
    """Copia o modelo para AppData para evitar problemas com acento/OneDrive no caminho."""
    if os.name == "nt":
        target = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MarkXLVIII_Gesture" / "gesture_recognizer.task"
        try:
            if MODEL_PATH.exists() and (not target.exists() or target.stat().st_size != MODEL_PATH.stat().st_size):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(MODEL_PATH, target)
            if target.exists():
                return target
        except Exception:
            pass
    return MODEL_PATH


def _open_camera(preferred_index: int = 0):
    if cv2 is None:
        return None, None

    indexes = [int(preferred_index or 0)]
    for idx in range(0, 8):
        if idx not in indexes:
            indexes.append(idx)

    backends: list[int | None] = []
    if os.name == "nt":
        backends.extend([
            getattr(cv2, "CAP_DSHOW", 700),
            getattr(cv2, "CAP_MSMF", 1400),
        ])
    backends.append(None)

    for idx in indexes:
        for backend in backends:
            cap = None
            try:
                cap = cv2.VideoCapture(idx, backend) if backend is not None else cv2.VideoCapture(idx)
                if not cap or not cap.isOpened():
                    raise RuntimeError("camera nao abriu")
                try:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
                    cap.set(cv2.CAP_PROP_FPS, 30)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                for _ in range(12):
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.size:
                        return cap, idx
                    time.sleep(0.035)
            except Exception:
                try:
                    if cap is not None:
                        cap.release()
                except Exception:
                    pass
    return None, None


def _make_detector():
    """Retorna (mode, detector, error).

    mode='solutions': detector eh mp.solutions.hands.Hands.
    mode='tasks': detector eh GestureRecognizer.
    """
    if mp is None:
        return None, None, "mediapipe nao importou"

    # Caminho 1: MediaPipe classico.
    try:
        solutions = getattr(mp, "solutions", None)
        if solutions is not None and getattr(solutions, "hands", None) is not None:
            hands_mod = solutions.hands
            detector = hands_mod.Hands(
                static_image_mode=False,
                max_num_hands=1,
                model_complexity=0,
                min_detection_confidence=0.32,
                min_tracking_confidence=0.32,
            )
            return "solutions", detector, ""
    except Exception as exc:
        last_error = f"mp.solutions falhou: {exc}"
    else:
        last_error = "mp.solutions nao disponivel"

    # Caminho 2: MediaPipe Tasks com modelo .task enviado do Brahma antigo.
    if mp_vision is None or mp_python is None:
        return None, None, f"{last_error}; mediapipe.tasks nao disponivel"

    model_path = _gesture_model_path()
    if not model_path.exists():
        return None, None, f"{last_error}; modelo nao encontrado em {MODEL_PATH}"

    try:
        try:
            options = mp_vision.GestureRecognizerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
                running_mode=mp_vision.RunningMode.IMAGE,
                num_hands=1,
                min_hand_detection_confidence=0.28,
                min_hand_presence_confidence=0.28,
                min_tracking_confidence=0.28,
            )
        except TypeError:
            # Algumas builds do MediaPipe Tasks no Python 3.13 não aceitam
            # esses parâmetros. Mantém compatibilidade sem quebrar o Vision Control.
            options = mp_vision.GestureRecognizerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
                running_mode=mp_vision.RunningMode.IMAGE,
                num_hands=1,
            )
        detector = mp_vision.GestureRecognizer.create_from_options(options)
        return "tasks", detector, ""
    except Exception as exc:
        return None, None, f"{last_error}; GestureRecognizer falhou: {exc}"


def _as_points(hand_obj):
    if hand_obj is None:
        return []
    if hasattr(hand_obj, "landmark"):
        return list(hand_obj.landmark)
    return list(hand_obj)


def _smooth_points(previous, points, alpha: float = 0.20):
    if not points:
        return None, points
    if not previous or len(previous) != len(points):
        state = [(p.x, p.y, getattr(p, "z", 0.0)) for p in points]
        return state, points
    state = []
    out = []
    for prev, point in zip(previous, points):
        x = prev[0] + (point.x - prev[0]) * alpha
        y = prev[1] + (point.y - prev[1]) * alpha
        z = prev[2] + (getattr(point, "z", 0.0) - prev[2]) * alpha
        state.append((x, y, z))
        out.append(SimpleNamespace(x=x, y=y, z=z))
    return state, out


def _draw_points(frame, points, label: str, status: str) -> None:
    if cv2 is None:
        return
    try:
        h, w = frame.shape[:2]
        for a, b in HAND_CONNECTIONS:
            p1 = (int(points[a].x * w), int(points[a].y * h))
            p2 = (int(points[b].x * w), int(points[b].y * h))
            cv2.line(frame, p1, p2, (220, 220, 220), 1)
        for idx, point in enumerate(points):
            cx, cy = int(point.x * w), int(point.y * h)
            radius = 4 if idx in (4, 8) else 3
            color = (0, 240, 255) if idx in (4, 8) else (220, 220, 220)
            cv2.circle(frame, (cx, cy), radius, color, -1)
    except Exception:
        pass
    try:
        h, w = frame.shape[:2]
        cv2.rectangle(frame, (0, 0), (w, 34), (0, 5, 8), -1)
        cv2.putText(frame, label, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 240, 255), 1)
        cv2.putText(frame, status, (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 120), 1)
    except Exception:
        pass


def _put_status(frame, label: str, status: str) -> None:
    if cv2 is None or frame is None:
        return
    try:
        h, w = frame.shape[:2]
        cv2.rectangle(frame, (0, 0), (w, 34), (0, 5, 8), -1)
        cv2.putText(frame, label, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 240, 255), 1)
        cv2.putText(frame, status, (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 120), 1)
    except Exception:
        pass


class _VisionController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error = ""
        self._active_camera: int | None = None
        self._player = None
        self._smoothed_landmarks = None
        self._cursor_x = 0
        self._cursor_y = 0
        self._smooth_x = 0.0
        self._smooth_y = 0.0
        self._pinch_down = False
        self._pinch_started = 0.0
        self._pinch_double_fired = False
        self._last_pinch_tap = 0.0
        self._pinch_anchor_cursor = None
        self._pinch_lock_until = 0.0
        self._dragging = False
        self._fist_was_closed = False
        self._fist_started = 0.0
        self._drag_anchor_hand = None
        self._drag_anchor_cursor = None
        self._drag_smooth_hand = None
        self._last_drag_state_change = 0.0
        self._last_fist_seen = 0.0
        self._last_hand_seen = 0.0
        self._fist_candidate_since = 0.0
        self._last_loop_label = "MOSTRE A MAO"
        self._last_loop_status = "NO HAND"
        self._last_good_landmarks = None
        self._last_good_landmarks_ts = 0.0
        self._last_drag_pointer = None
        self._last_drag_pointer_ts = 0.0
        self._drag_velocity = (0.0, 0.0)
        self._drag_inertia_ts = 0.0
        self._last_drag_move_ts = 0.0
        self._last_drag_deadzone = False

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, camera_index: int = 0, player=None) -> str:
        with self._lock:
            if self.is_alive():
                return "Vision Control já está ativo no painel lateral."
            self._stop.clear()
            self._ready.clear()
            self._error = ""
            self._active_camera = None
            self._player = player
            self._smoothed_landmarks = None
            self._pinch_down = False
            self._pinch_started = 0.0
            self._pinch_double_fired = False
            self._pinch_anchor_cursor = None
            self._pinch_lock_until = 0.0
            self._dragging = False
            self._last_fist_seen = 0.0
            self._last_hand_seen = 0.0
            self._fist_candidate_since = 0.0
            self._last_loop_label = "MOSTRE A MAO"
            self._last_loop_status = "NO HAND"
            self._last_good_landmarks = None
            self._last_good_landmarks_ts = 0.0
            self._last_drag_pointer = None
            self._last_drag_pointer_ts = 0.0
            self._drag_velocity = (0.0, 0.0)
            self._drag_inertia_ts = 0.0
            self._last_drag_move_ts = 0.0
            self._last_drag_deadzone = False
            self._thread = threading.Thread(
                target=self._run,
                args=(camera_index, player),
                daemon=True,
                name="MarkXLVIII-VisionControl",
            )
            self._thread.start()

        deadline = time.time() + 8.0
        while time.time() < deadline:
            if self._ready.is_set():
                cam = self._active_camera if self._active_camera is not None else camera_index
                return f"Vision Control ativado no painel lateral usando câmera {cam}."
            if self._error:
                return f"Vision Control não ativou: {self._error}"
            if not self.is_alive():
                return f"Vision Control não ativou: {self._error or 'o controle fechou antes da webcam iniciar.'}"
            time.sleep(0.08)
        if self.is_alive():
            return "Vision Control iniciou, mas a webcam ainda não confirmou imagem. Aguarde alguns segundos."
        return f"Vision Control não ativou: {self._error or 'falha desconhecida.'}"

    def stop(self) -> str:
        with self._lock:
            if not self.is_alive():
                self._notify_panel(False, "OFFLINE")
                return "Vision Control já está desativado."
            self._stop.set()
            thread = self._thread
        if thread:
            thread.join(timeout=3.0)
        self._notify_panel(False, "OFFLINE")
        return "Vision Control desativado."

    def status(self) -> str:
        if self.is_alive():
            cam = self._active_camera if self._active_camera is not None else "?"
            return f"Vision Control está ativo no painel lateral. Câmera {cam}."
        return "Vision Control está desativado."

    def restart(self, camera_index: int = 0, player=None) -> str:
        self.stop()
        time.sleep(0.20)
        return self.start(camera_index=camera_index, player=player)

    def _notify_panel(self, active: bool, status: str) -> None:
        player = self._player
        try:
            if player and hasattr(player, "set_vision_control_active"):
                player.set_vision_control_active(active, status)
            elif player and active and hasattr(player, "start_camera_stream"):
                player.start_camera_stream()
            elif player and not active and hasattr(player, "stop_camera_stream"):
                player.stop_camera_stream()
        except Exception:
            pass

    def _notify_frame(self, frame_bgr, status: str) -> None:
        player = self._player
        if player is None or cv2 is None or frame_bgr is None:
            return
        try:
            ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 62])
            if not ok:
                return
            data = buf.tobytes()
            if hasattr(player, "update_vision_control_frame"):
                player.update_vision_control_frame(data, status)
            elif hasattr(player, "show_camera_frame"):
                player.show_camera_frame(data)
        except Exception:
            pass

    @staticmethod
    def _distance(a, b):
        return math.hypot(a.x - b.x, a.y - b.y)

    @staticmethod
    def _finger_fold_count(lm) -> int:
        """Conta dedos dobrados. Funciona melhor que distancia pura em cameras diferentes."""
        folded = 0
        # indicador, medio, anelar, minimo: ponta abaixo/proxima da junta PIP = dedo dobrado.
        for tip_idx, pip_idx in ((8, 6), (12, 10), (16, 14), (20, 18)):
            try:
                if lm[tip_idx].y > lm[pip_idx].y - 0.010:
                    folded += 1
            except Exception:
                pass
        return folded

    @staticmethod
    def _is_closed_fist(lm):
        wrist = lm[0]
        tips = [lm[8], lm[12], lm[16], lm[20]]
        mean_dist = sum(math.hypot(t.x - wrist.x, t.y - wrist.y) for t in tips) / 4.0
        folded = _VisionController._finger_fold_count(lm)
        return mean_dist < FIST_MEAN_DISTANCE_THRESHOLD or folded >= FIST_FOLDED_MIN

    @staticmethod
    def _is_open_hand(lm):
        wrist = lm[0]
        tips = [lm[8], lm[12], lm[16], lm[20]]
        mean_dist = sum(math.hypot(t.x - wrist.x, t.y - wrist.y) for t in tips) / 4.0
        folded = _VisionController._finger_fold_count(lm)
        return mean_dist > OPEN_HAND_MEAN_DISTANCE_THRESHOLD and folded <= 1

    @staticmethod
    def _drag_pointer(lm):
        """Ponto usado para arrastar com punho fechado.

        Usar ponta de dedo no punho fechado costuma travar ou tremer. O Brahma
        antigo funcionava melhor seguindo a palma/estrutura da mao. Aqui usamos
        pulso + bases dos dedos, que continuam visiveis mesmo com a mao fechada.
        """
        try:
            pts = [lm[0], lm[5], lm[9], lm[13], lm[17]]
            return (sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts))
        except Exception:
            return ((lm[0].x + lm[5].x + lm[17].x) / 3.0, (lm[0].y + lm[5].y + lm[17].y) / 3.0)

    def _move_cursor_absolute(self, hand_x, hand_y, screen_w, screen_h):
        # Margem maior + ganho ao redor do centro = mouse mais sensivel com menos movimento da mão.
        margin = 0.10
        nx = min(1.0, max(0.0, (hand_x - margin) / (1 - 2 * margin)))
        ny = min(1.0, max(0.0, (hand_y - margin) / (1 - 2 * margin)))
        target_x = int((0.5 + (nx - 0.5) * MOVE_SENSITIVITY) * screen_w)
        target_y = int((0.5 + (ny - 0.5) * MOVE_SENSITIVITY) * screen_h)
        target_x = max(0, min(screen_w - 1, target_x))
        target_y = max(0, min(screen_h - 1, target_y))
        # Filtro anti-tremedeira: suaviza mais e ignora micro-movimentos.
        self._smooth_x += (target_x - self._smooth_x) * 0.45
        self._smooth_y += (target_y - self._smooth_y) * 0.45
        next_x = int(self._smooth_x)
        next_y = int(self._smooth_y)
        if abs(next_x - self._cursor_x) < 5 and abs(next_y - self._cursor_y) < 5:
            return
        self._cursor_x = next_x
        self._cursor_y = next_y
        pyautogui.moveTo(self._cursor_x, self._cursor_y, duration=0)

    def _move_cursor_drag_absolute(self, hand_x, hand_y, screen_w, screen_h):
        """Move o cursor enquanto o botão esquerdo está pressionado.

        No punho fechado alguns modelos estabilizam os pontos dos dedos e o modo
        relativo pode parecer travado. Para arrastar de verdade, seguramos o
        clique e fazemos o cursor seguir a palma com o mesmo mapa da mão aberta,
        mas com suavização/dead-zone própria para não tremer.
        """
        margin = 0.10
        nx = min(1.0, max(0.0, (hand_x - margin) / (1 - 2 * margin)))
        ny = min(1.0, max(0.0, (hand_y - margin) / (1 - 2 * margin)))
        target_x = int((0.5 + (nx - 0.5) * MOVE_SENSITIVITY) * screen_w)
        target_y = int((0.5 + (ny - 0.5) * MOVE_SENSITIVITY) * screen_h)
        target_x = max(0, min(screen_w - 1, target_x))
        target_y = max(0, min(screen_h - 1, target_y))

        # Arraste precisa responder, mas sem vibrar. Aqui suavizamos mais que
        # no movimento normal para cortar tremedeira com o punho fechado.
        self._smooth_x += (target_x - self._smooth_x) * (DRAG_CURSOR_SMOOTH_ALPHA * 0.88)
        self._smooth_y += (target_y - self._smooth_y) * (DRAG_CURSOR_SMOOTH_ALPHA * 0.68)
        next_x = int(self._smooth_x)
        next_y = int(self._smooth_y)

        if abs(next_x - self._cursor_x) < DRAG_ABSOLUTE_DEADZONE_PX and abs(next_y - self._cursor_y) < DRAG_ABSOLUTE_DEADZONE_PX:
            return
        self._cursor_x = next_x
        self._cursor_y = next_y
        pyautogui.moveTo(self._cursor_x, self._cursor_y, duration=0)

    def _move_cursor_relative(self, hand_x, hand_y, screen_w, screen_h):
        now = time.time()
        self._last_drag_deadzone = False
        if self._last_drag_pointer is not None and self._last_drag_pointer_ts:
            dt = max(0.001, now - self._last_drag_pointer_ts)
            vx = (hand_x - self._last_drag_pointer[0]) / dt
            vy = (hand_y - self._last_drag_pointer[1]) / dt
            old_vx, old_vy = self._drag_velocity
            # Suaviza mais o eixo Y porque é nele que o punho vibra mais.
            self._drag_velocity = (old_vx * 0.65 + vx * 0.35, old_vy * 0.72 + vy * 0.28)
        self._last_drag_pointer = (hand_x, hand_y)
        self._last_drag_pointer_ts = now
        self._drag_inertia_ts = now

        if not self._drag_anchor_hand or not self._drag_anchor_cursor:
            self._drag_anchor_hand = (hand_x, hand_y)
            self._drag_anchor_cursor = (self._cursor_x, self._cursor_y)
            self._drag_smooth_hand = (hand_x, hand_y)
            return False
        if not self._drag_smooth_hand:
            self._drag_smooth_hand = (hand_x, hand_y)

        # Suaviza o ponto da palma/punho para cortar tremedeira sem congelar.
        # O eixo Y recebe filtro mais forte para evitar subir/descer parado.
        hand_x = self._drag_smooth_hand[0] + (hand_x - self._drag_smooth_hand[0]) * DRAG_HAND_SMOOTH_ALPHA
        hand_y = self._drag_smooth_hand[1] + (hand_y - self._drag_smooth_hand[1]) * (DRAG_HAND_SMOOTH_ALPHA * 0.66)
        self._drag_smooth_hand = (hand_x, hand_y)
        dx = hand_x - self._drag_anchor_hand[0]
        dy = hand_y - self._drag_anchor_hand[1]

        # Dead-zone separado: micro oscilação vertical não deve virar arraste.
        if abs(dx) < DRAG_RELATIVE_DEADZONE:
            dx = 0.0
        if abs(dy) < (DRAG_RELATIVE_DEADZONE * 1.45):
            dy = 0.0
        if dx == 0.0 and dy == 0.0:
            self._last_drag_deadzone = True
            return False

        target_x = int(self._drag_anchor_cursor[0] + dx * screen_w * DRAG_SENSITIVITY)
        target_y = int(self._drag_anchor_cursor[1] + dy * screen_h * DRAG_SENSITIVITY)
        target_x = max(0, min(screen_w - 1, target_x))
        target_y = max(0, min(screen_h - 1, target_y))
        next_x = int(self._cursor_x + (target_x - self._cursor_x) * DRAG_CURSOR_SMOOTH_ALPHA)
        next_y = int(self._cursor_y + (target_y - self._cursor_y) * (DRAG_CURSOR_SMOOTH_ALPHA * 0.82))
        if abs(next_x - self._cursor_x) < 2 and abs(next_y - self._cursor_y) < 2:
            self._last_drag_deadzone = True
            return False
        self._cursor_x = next_x
        self._cursor_y = next_y
        self._smooth_x = float(self._cursor_x)
        self._smooth_y = float(self._cursor_y)
        self._last_drag_move_ts = now
        pyautogui.moveTo(self._cursor_x, self._cursor_y, duration=0)
        return True

    def _continue_drag_inertia(self, screen_w, screen_h):
        """Mantem o arraste suave por poucos ms quando a mao pisca no detector.

        Isso nao tenta adivinhar por muito tempo; so cobre aquela falha rapida em
        que o esqueleto some e volta no punho fechado.
        """
        if not self._dragging or not self._last_drag_pointer_ts:
            return
        now = time.time()
        dt_lost = now - self._last_drag_pointer_ts
        if dt_lost > DRAG_LOST_HAND_INERTIA_GRACE:
            return
        vx, vy = self._drag_velocity
        if abs(vx) < 0.018 and abs(vy) < 0.018:
            return
        if not self._drag_inertia_ts:
            self._drag_inertia_ts = self._last_drag_pointer_ts
        step = max(0.001, min(0.025, now - self._drag_inertia_ts))
        self._drag_inertia_ts = now
        # Desacelera rapido para nao sair arrastando sozinho.
        decay = max(0.0, 1.0 - (dt_lost / DRAG_LOST_HAND_INERTIA_GRACE))
        move_x = int(vx * step * screen_w * DRAG_SENSITIVITY * decay)
        move_y = int(vy * step * screen_h * DRAG_SENSITIVITY * 0.72 * decay)
        if abs(move_x) < 2 and abs(move_y) < 2:
            return
        self._cursor_x = max(0, min(screen_w - 1, self._cursor_x + move_x))
        self._cursor_y = max(0, min(screen_h - 1, self._cursor_y + move_y))
        self._smooth_x = float(self._cursor_x)
        self._smooth_y = float(self._cursor_y)
        try:
            pyautogui.moveTo(self._cursor_x, self._cursor_y, duration=0)
        except Exception:
            pass

    def _pinch_prelock_threshold(self, lm):
        """Distancia de pré-pinça usada só para travar o cursor antes do clique."""
        try:
            palm_width = self._distance(lm[5], lm[17])
        except Exception:
            palm_width = 0.20
        return max(PINCH_PRELOCK_MIN, palm_width * PINCH_PRELOCK_SCALE)

    def _pinch_lock_active(self) -> bool:
        """Mantém o cursor parado por poucos ms durante/apos a pinça."""
        if not self._pinch_anchor_cursor:
            return False
        now = time.time()
        if now > self._pinch_lock_until and not self._pinch_down:
            self._pinch_anchor_cursor = None
            return False
        try:
            x, y = self._pinch_anchor_cursor
            if abs(self._cursor_x - x) > 1 or abs(self._cursor_y - y) > 1:
                self._cursor_x, self._cursor_y = int(x), int(y)
                self._smooth_x, self._smooth_y = float(x), float(y)
                pyautogui.moveTo(int(x), int(y), duration=0)
        except Exception:
            pass
        return True

    def _click_at_pinch_anchor(self, double: bool = False) -> None:
        """Executa o clique exatamente no ponto onde a pinça começou a fechar."""
        try:
            x, y = self._pinch_anchor_cursor or (self._cursor_x, self._cursor_y)
            self._cursor_x, self._cursor_y = int(x), int(y)
            self._smooth_x, self._smooth_y = float(x), float(y)
            pyautogui.moveTo(int(x), int(y), duration=0)
            if double:
                pyautogui.doubleClick(button="left", interval=0.045)
            else:
                pyautogui.click(button="left")
        except Exception:
            if double:
                pyautogui.doubleClick(button="left", interval=0.045)
            else:
                pyautogui.click(button="left")

    def _pinch_thresholds(self, lm):
        # Ajuste automatico por tamanho da mão na imagem.
        try:
            palm_width = self._distance(lm[5], lm[17])
        except Exception:
            palm_width = 0.20
        close_threshold = max(PINCH_CLOSE_MIN, palm_width * PINCH_CLOSE_SCALE)
        release_threshold = max(PINCH_RELEASE_MIN, palm_width * PINCH_RELEASE_SCALE)
        return close_threshold, release_threshold

    @staticmethod
    def _other_fingers_extended_for_pinch(lm) -> bool:
        """Pinça de clique só vale quando a mão não está virando punho.

        Ficou menos rígido que antes: basta pelo menos um dos outros dedos estar
        claramente aberto. Isso recupera o clique quando a câmera vê a pinça,
        mas bloqueia quando todos os dedos estão fechando para formar punho.
        """
        extended = 0
        for tip_idx, pip_idx in ((12, 10), (16, 14), (20, 18)):
            try:
                if lm[tip_idx].y < lm[pip_idx].y - 0.010:
                    extended += 1
            except Exception:
                pass
        return extended >= 1

    def _handle_pinch_click(self, lm):
        now = time.time()

        # Durante ou logo depois de arrastar, nunca transforme a mão fechando em click.
        if self._dragging or (now - self._last_drag_state_change) < PINCH_BLOCK_AFTER_DRAG:
            self._pinch_down = False
            self._pinch_started = 0.0
            self._pinch_double_fired = False
            self._pinch_anchor_cursor = None
            self._pinch_lock_until = 0.0
            return False, None

        folded = self._finger_fold_count(lm)
        if folded >= 3 or not self._other_fingers_extended_for_pinch(lm):
            # Isto é mão fechando/punho, não pinça de click.
            self._pinch_down = False
            self._pinch_started = 0.0
            self._pinch_double_fired = False
            if not self._pinch_lock_active():
                self._pinch_anchor_cursor = None
            return False, None

        pinch_dist = self._distance(lm[4], lm[8])
        close_threshold, release_threshold = self._pinch_thresholds(lm)
        prelock_threshold = max(self._pinch_prelock_threshold(lm), release_threshold)
        pinch = pinch_dist < close_threshold
        near_pinch = pinch_dist < prelock_threshold
        released = pinch_dist > release_threshold
        label = None

        # Assim que o indicador e o dedão começam a aproximar, trava o cursor
        # na posição atual. Isso evita o "abaixar" do cursor quando o dedo fecha.
        if near_pinch and self._pinch_anchor_cursor is None:
            self._pinch_anchor_cursor = (self._cursor_x, self._cursor_y)
        if near_pinch or self._pinch_down:
            self._pinch_lock_until = now + PINCH_LOCK_RELEASE_GRACE
            self._pinch_lock_active()

        # Pinça curta: clica quando soltar.
        # Pinça segurada por alguns ms: duplo clique uma vez.
        if pinch and not self._pinch_down:
            self._pinch_down = True
            self._pinch_started = now
            self._pinch_double_fired = False
            return True, "PINCA: CURSOR FIXO"

        if self._pinch_down and pinch:
            held_for = now - self._pinch_started if self._pinch_started else 0.0
            if (not self._pinch_double_fired) and held_for >= PINCH_DOUBLE_HOLD_SECONDS:
                self._click_at_pinch_anchor(double=True)
                self._pinch_double_fired = True
                self._last_pinch_tap = 0.0
                self._pinch_lock_until = now + PINCH_LOCK_RELEASE_GRACE
                return True, "PINCA: DUPLO CLICK"
            return True, "PINCA: SEGURANDO"

        if released and self._pinch_down:
            if not self._pinch_double_fired:
                self._click_at_pinch_anchor(double=False)
                self._last_pinch_tap = now
                label = "PINCA: CLICK"
            self._pinch_started = 0.0
            self._pinch_down = False
            self._pinch_double_fired = False
            self._pinch_lock_until = now + PINCH_LOCK_RELEASE_GRACE
            self._pinch_lock_active()
            return True, label

        if near_pinch or self._pinch_lock_active():
            return True, "PINCA: CURSOR FIXO"

        return False, label

    def _handle_fist_drag(self, lm):
        now = time.time()
        closed_fist = self._is_closed_fist(lm)

        if closed_fist:
            self._last_fist_seen = now
            self._pinch_down = False
            self._pinch_started = 0.0
            self._pinch_double_fired = False
            self._pinch_anchor_cursor = None
            self._pinch_lock_until = 0.0

            palm_x, palm_y = self._drag_pointer(lm)

            if not self._fist_was_closed:
                self._fist_started = now
                self._fist_candidate_since = now
                self._drag_anchor_hand = (palm_x, palm_y)
                self._drag_anchor_cursor = (self._cursor_x, self._cursor_y)
                self._drag_smooth_hand = (palm_x, palm_y)

            # Só entra em mouseDown depois do punho ficar estável por alguns ms.
            # Isso evita que o fechamento da mão vire vários clickzinhos na pasta.
            if (not self._dragging) and self._fist_candidate_since and (now - self._fist_candidate_since) >= FIST_HOLD_DELAY:
                pyautogui.mouseDown(button="left")
                self._dragging = True
                self._last_drag_state_change = now

        else:
            # Se a leitura oscilar enquanto o punho está fechado, não solte na hora.
            # Só solta quando detectar mão aberta ou quando perder o punho por tempo suficiente.
            should_release = False
            if self._dragging:
                if self._is_open_hand(lm):
                    should_release = True
                elif (now - self._last_fist_seen) > FIST_RELEASE_GRACE:
                    should_release = True
            else:
                self._fist_candidate_since = 0.0

            if should_release:
                self._fist_started = 0.0
                self._fist_candidate_since = 0.0
                if now - self._last_drag_state_change > 0.06:
                    pyautogui.mouseUp(button="left")
                    self._dragging = False
                    self._last_drag_state_change = now
                self._drag_anchor_hand = None
                self._drag_anchor_cursor = None
                self._drag_smooth_hand = None
                self._last_drag_pointer = None
                self._last_drag_pointer_ts = 0.0
                self._drag_velocity = (0.0, 0.0)
                self._drag_inertia_ts = 0.0
                self._last_drag_move_ts = 0.0
                self._last_drag_deadzone = False

        self._fist_was_closed = closed_fist
        return closed_fist or self._dragging

    def _release_drag_after_no_hand_grace(self):
        now = time.time()
        if not self._dragging:
            return
        # Igual ao Brahma antigo: não solta no primeiro frame que perdeu a mão.
        if (now - self._last_fist_seen) <= NO_HAND_DRAG_GRACE:
            return
        try:
            pyautogui.mouseUp(button="left")
        except Exception:
            pass
        self._dragging = False
        self._last_drag_state_change = now
        self._drag_anchor_hand = None
        self._drag_anchor_cursor = None
        self._drag_smooth_hand = None
        self._last_drag_pointer = None
        self._last_drag_pointer_ts = 0.0
        self._drag_velocity = (0.0, 0.0)
        self._drag_inertia_ts = 0.0
        self._last_drag_move_ts = 0.0
        self._last_drag_deadzone = False
        self._fist_candidate_since = 0.0

    def _detect_hands(self, mode, detector, rgb):
        if mode == "solutions":
            result = detector.process(rgb)
            return result.multi_hand_landmarks or []
        if mode == "tasks":
            # MediaPipe Tasks exige RGB continuo na memoria.
            rgb2 = rgb.copy()
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb2)
            result = detector.recognize(mp_image)
            return result.hand_landmarks or []
        return []

    def _run(self, camera_index: int, player=None) -> None:
        cap = None
        detector = None
        mode = None
        try:
            # A MENSAGEM TEM DE DIZER O QUE FALTOU (22/09/2026). As tres frases
            # antigas mandavam rodar `1_instalar_dependencias.bat`, que NAO
            # existe no pacote — o cliente ficava procurando um arquivo que
            # ninguem entregou e nao sabia qual peca estava faltando.
            if cv2 is None:
                self._error = ("a camera do Vision Control nao carregou nesta "
                               f"instalacao ({_ERRO_CV2 or 'opencv ausente'}). "
                               "Reinstale o JARVIS por cima para repor a peca.")
                print(f"[VisionControl] opencv fora: {_ERRO_CV2}")
                self._notify_panel(False, "ERRO OPENCV")
                return
            if mp is None:
                self._error = ("o reconhecimento de mao nao carregou nesta "
                               f"instalacao ({_ERRO_MEDIAPIPE or 'mediapipe ausente'}). "
                               "Reinstale o JARVIS por cima para repor a peca.")
                print(f"[VisionControl] mediapipe fora: {_ERRO_MEDIAPIPE}")
                self._notify_panel(False, "ERRO MEDIAPIPE")
                return
            if pyautogui is None:
                self._error = ("o controle do mouse nao carregou nesta "
                               f"instalacao ({_ERRO_PYAUTOGUI or 'pyautogui ausente'}). "
                               "Reinstale o JARVIS por cima para repor a peca.")
                print(f"[VisionControl] pyautogui fora: {_ERRO_PYAUTOGUI}")
                self._notify_panel(False, "ERRO PYAUTOGUI")
                return

            try:
                pyautogui.FAILSAFE = True
                pyautogui.PAUSE = 0
            except Exception:
                pass

            self._notify_panel(True, "ABRINDO CAM")
            cap, active_idx = _open_camera(camera_index)
            if cap is None:
                self._error = "não consegui abrir nenhuma webcam. Feche OBS/Discord/Camera e tente novamente."
                self._notify_panel(False, "SEM WEBCAM")
                return
            self._active_camera = active_idx

            mode, detector, det_error = _make_detector()
            if not detector:
                # Nao mexe o mouse sem rastreador de mao. So mostra a camera e explica no painel/log.
                self._ready.set()
                self._notify_panel(True, "SEM HAND MODEL")
                self._camera_only_loop(cap, active_idx, det_error)
                return

            screen_w, screen_h = pyautogui.size()
            self._cursor_x = screen_w // 2
            self._cursor_y = screen_h // 2
            self._smooth_x = float(self._cursor_x)
            self._smooth_y = float(self._cursor_y)
            last_frame_sent = 0.0
            last_process = 0.0

            self._ready.set()
            self._notify_panel(True, f"CAM {active_idx} HAND")

            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.01)
                    continue

                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                now = time.time()

                # IMPORTANTE: antes, nos frames em que a gente nao processava o
                # MediaPipe, o codigo caia como se "nao tivesse mao". Com o
                # punho fechado isso fazia o cursor parar no lugar. Agora, quando
                # ainda nao chegou a hora de processar outro frame, apenas atualiza
                # a camera no painel e mantem o estado atual do arraste.
                process_interval = PROCESS_INTERVAL_DRAG if (self._dragging or self._fist_was_closed or self._fist_candidate_since) else PROCESS_INTERVAL_NORMAL
                if now - last_process < process_interval:
                    if self._dragging and self._last_good_landmarks is not None and (now - self._last_good_landmarks_ts) <= DRAG_LOST_HAND_DRAW_GRACE:
                        _draw_points(frame, self._last_good_landmarks, self._last_loop_label, self._last_loop_status)
                    else:
                        _put_status(frame, self._last_loop_label, self._last_loop_status)
                    if now - last_frame_sent >= 0.040:
                        self._notify_frame(frame, self._last_loop_status)
                        last_frame_sent = now
                    time.sleep(0.001)
                    continue

                last_process = now
                try:
                    hand_items = self._detect_hands(mode, detector, rgb)
                except Exception as exc:
                    self._error = f"erro ao ler mão: {exc}"
                    hand_items = []

                label = "MOSTRE A MAO"
                status = f"CAM {active_idx} / {mode}"

                if hand_items:
                    self._last_hand_seen = time.time()
                    raw_points = _as_points(hand_items[0])
                    # Usa pontos suavizados para mover normal/clicar, mas usa pontos
                    # brutos para o punho. No punho fechado a suavizacao pode parecer
                    # travamento, porque a mao muda de formato e anda pouco no frame.
                    self._smoothed_landmarks, lm = _smooth_points(self._smoothed_landmarks, raw_points)
                    raw_lm = raw_points if len(raw_points) >= 21 else lm
                    if len(raw_lm) >= 21:
                        self._last_good_landmarks = raw_lm
                        self._last_good_landmarks_ts = time.time()
                    if len(lm) >= 21:
                        palm_x = (lm[0].x + lm[5].x + lm[17].x) / 3.0
                        palm_y = (lm[0].y + lm[5].y + lm[17].y) / 3.0
                        drag_x, drag_y = self._drag_pointer(raw_lm)
                        try:
                            # Punho fechado tem prioridade total sobre pinça.
                            # Assim a mao fechada segura/arrasta; abrir pasta fica somente na pinça.
                            fist_active = self._handle_fist_drag(raw_lm)
                            if fist_active:
                                # Punho fechado = mouseDown + movimento pela palma.
                                # Agora seguimos a palma BRUTA em modo relativo, igual a
                                # logica do Brahma antigo. Isso evita o bug em que fechar
                                # o punho congelava o cursor.
                                if self._dragging:
                                    before_x, before_y = self._cursor_x, self._cursor_y
                                    moved = self._move_cursor_relative(drag_x, drag_y, screen_w, screen_h)
                                    # Fallback absoluto só entra quando o relativo realmente
                                    # parece travado. Antes ele entrava também nos micro-movimentos
                                    # filtrados pela dead-zone, causando tremedeira para cima/baixo.
                                    stalled_for = time.time() - (self._last_drag_move_ts or self._last_drag_state_change or time.time())
                                    if (not moved) and (not getattr(self, "_last_drag_deadzone", False)) and stalled_for > 0.18:
                                        self._move_cursor_drag_absolute(drag_x, drag_y, screen_w, screen_h)
                                label = "PUNHO: SEGURAR/ARRASTAR"
                                status = "DRAG" if self._dragging else "SEGURANDO"
                            else:
                                pinch, click_label = self._handle_pinch_click(lm)
                                if click_label:
                                    label = click_label
                                    status = "CLICK"
                                elif self._pinch_lock_active():
                                    label = "PINCA: CURSOR FIXO"
                                    status = "PINCH LOCK"
                                elif self._is_open_hand(lm) and not pinch:
                                    self._move_cursor_absolute(palm_x, palm_y, screen_w, screen_h)
                                    label = "MAO ABERTA: MOVER"
                                    status = "MOVE"
                                elif pinch:
                                    label = "PINCA: AGUARDE SOLTAR"
                                    status = "PINCH"
                                else:
                                    label = "GESTO INCERTO"
                                    status = "STANDBY"
                        except pyautogui.FailSafeException:
                            self._error = "fail-safe do mouse ativado no canto da tela."
                            break
                        except Exception as exc:
                            label = "ERRO MOUSE"
                            status = str(exc)[:22]
                        _draw_points(frame, lm, label, status)
                else:
                    # Quando o punho fecha, o MediaPipe pode perder a mao por
                    # alguns frames. Nao limpe tudo imediatamente: mantenha o
                    # desenho/estado por um curto tempo e continue segurando.
                    now_lost = time.time()
                    recent_cached_hand = (
                        self._last_good_landmarks is not None
                        and (now_lost - self._last_good_landmarks_ts) <= DRAG_LOST_HAND_DRAW_GRACE
                    )
                    if self._dragging:
                        self._continue_drag_inertia(screen_w, screen_h)
                        self._release_drag_after_no_hand_grace()
                        label = "PUNHO: SEGURANDO" if self._dragging else label
                        status = "DRAG HOLD" if self._dragging else "NO HAND"
                        if self._dragging and recent_cached_hand:
                            _draw_points(frame, self._last_good_landmarks, label, status)
                        else:
                            _put_status(frame, label, status)
                    else:
                        self._smoothed_landmarks = None
                        self._fist_was_closed = False
                        self._pinch_down = False
                        self._pinch_double_fired = False
                        status = "NO HAND"
                        _put_status(frame, label, status)

                self._last_loop_label = label
                self._last_loop_status = status
                now = time.time()
                if now - last_frame_sent >= 0.040:
                    self._notify_frame(frame, status)
                    last_frame_sent = now
                time.sleep(0.001)

        except Exception as exc:
            self._error = str(exc)
            self._notify_panel(False, "ERRO")
        finally:
            try:
                if self._dragging and pyautogui is not None:
                    pyautogui.mouseUp(button="left")
            except Exception:
                pass
            self._dragging = False
            try:
                if detector is not None and hasattr(detector, "close"):
                    detector.close()
            except Exception:
                pass
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
            self._ready.clear()
            self._notify_panel(False, "OFFLINE")

    def _camera_only_loop(self, cap, active_idx, error: str) -> None:
        last_frame_sent = 0.0
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.02)
                continue
            frame = cv2.flip(frame, 1)
            _put_status(frame, "CAMERA OK / SEM LEITURA DA MAO", "FALTA MODELO")
            now = time.time()
            if now - last_frame_sent >= 0.08:
                self._notify_frame(frame, "SEM HAND MODEL")
                last_frame_sent = now
        try:
            cap.release()
        except Exception:
            pass


_CONTROLLER = _VisionController()


def vision_control(parameters: dict | None = None, response=None, player=None, session_memory=None) -> str:
    """Entrada da ferramenta Vision Control usada pelo JARVIS."""
    params = parameters or {}
    raw_action = str(params.get("action") or params.get("mode") or "start").strip().lower()
    if raw_action in {"start", "on", "enable", "iniciar", "abrir", "ligar", "ativar", "restart", "reiniciar"}:
        try:  # Linux no Wayland: mouse e teclado bloqueados (core/plataforma.py)
            from core.plataforma import bloqueio_do_mouse  # noqa: PLC0415
            _recado = bloqueio_do_mouse()
            if _recado:
                return _recado
        except Exception:
            pass
        import sys as _sys  # noqa: PLC0415
        import glob as _glob  # noqa: PLC0415
        if _sys.platform.startswith("linux") and not _glob.glob("/dev/video*"):
            # 26/09/2026: o PC do cliente do Big Linux nao tem webcam ligada.
            return ("O Vision Control precisa de uma webcam para ver a sua mao, e nao "
                    "encontrei nenhuma camera ligada neste computador.")
    raw_action = raw_action.replace("ligar", "start").replace("ativar", "start")
    raw_action = raw_action.replace("desligar", "stop").replace("desativar", "stop")
    raw_action = raw_action.replace("parar", "stop").replace("fechar", "stop")

    try:
        camera_index = int(params.get("camera_index") or params.get("camera") or 0)
    except Exception:
        camera_index = 0

    if raw_action in {"start", "on", "enable", "iniciar", "abrir"}:
        result = _CONTROLLER.start(camera_index=camera_index, player=player)
    elif raw_action in {"stop", "off", "disable", "encerrar"}:
        result = _CONTROLLER.stop()
    elif raw_action in {"status", "estado"}:
        result = _CONTROLLER.status()
    elif raw_action in {"restart", "reiniciar"}:
        result = _CONTROLLER.restart(camera_index=camera_index, player=player)
    else:
        result = "Ação inválida para Vision Control. Use start, stop, status ou restart."

    try:
        if player and hasattr(player, "write_log"):
            player.write_log(f"[VisionControl] {result}")
    except Exception:
        pass
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JARVIS Vision Control")
    parser.add_argument("--start", action="store_true", help="inicia o controle")
    parser.add_argument("--stop", action="store_true", help="para o controle")
    parser.add_argument("--status", action="store_true", help="mostra status")
    parser.add_argument("--camera", type=int, default=0, help="indice da webcam")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.stop:
        print(_CONTROLLER.stop())
    elif args.status:
        print(_CONTROLLER.status())
    else:
        print("Para testar com painel embutido, abra o JARVIS e diga: ativar vision control.")
