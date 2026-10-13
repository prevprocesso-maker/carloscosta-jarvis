from __future__ import annotations

"""Local Gesture Recognition for Desktop (PyQt6 + MediaPipe)."""

import asyncio
import cv2
import numpy as np
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, List, Dict, Any
from pathlib import Path
import threading
import time
from collections import deque


class LocalGestureType(Enum):
    """Tipos de gestos reconhecidos localmente."""
    FIST = "fist"                    # Punho fechado
    OPEN_PALM = "open_palm"          # Palma aberta
    POINT = "point"                  # Apontando
    V_SIGN = "v_sign"                # Sinal de V (vitória)
    THUMBS_UP = "thumbs_up"          # Joinha
    THUMBS_DOWN = "thumbs_down"      # Joinha pra baixo
    LOVE = "love"                    # ILoveYou
    UNKNOWN = "unknown"


@dataclass
class LocalGestureResult:
    """Resultado de detecção de gesto local."""
    gesture: LocalGestureType
    confidence: float
    handedness: str  # "Left" or "Right"
    landmarks: List[Dict[str, float]]  # 21 landmarks {x, y, z}
    bounding_box: Optional[Dict[str, float]] = None
    timestamp: float = 0.0


class LocalGestureRecognizer:
    """
    Reconhecimento de gestos local para Desktop (PyQt6).
    
    Usa MediaPipe Tasks Vision (Python) para detectar:
    - 7 gestos pré-definidos
    - 21 landmarks da mão
    - Handedness (esquerda/direita)
    
    Roda em thread separada para não bloquear UI.
    """
    
    GESTURE_MAP = {
        'Closed_Fist': LocalGestureType.FIST,
        'Open_Palm': LocalGestureType.OPEN_PALM,
        'Pointing_Up': LocalGestureType.POINT,
        'Victory': LocalGestureType.V_SIGN,
        'Thumb_Up': LocalGestureType.THUMBS_UP,
        'Thumb_Down': LocalGestureType.THUMBS_DOWN,
        'ILoveYou': LocalGestureType.LOVE,
    }
    
    def __init__(
        self,
        camera_index: int = 0,
        resolution: tuple = (1280, 720),
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        gesture_cooldown_ms: int = 500,
        mirror: bool = True,
    ):
        if camera_index < 0:
            raise ValueError(
                f"camera_index must be >= 0, got {camera_index}. "
                "Use 0 for default camera, or positive integer for specific device."
            )
        self.camera_index = camera_index
        self.resolution = resolution
        self.min_detection_confidence = min_detection_confidence
        self.min_tracking_confidence = min_tracking_confidence
        self.gesture_cooldown_ms = gesture_cooldown_ms
        self.mirror = mirror
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cap: Optional[cv2.VideoCapture] = None
        
        self._gesture_recognizer = None
        self._hand_landmarker = None
        self._mp_vision = None
        
        # Callbacks
        self.on_gesture: Optional[Callable[[LocalGestureResult], None]] = None
        self.on_hand_landmarks: Optional[Callable[[List[Dict], str], None]] = None
        self.on_frame: Optional[Callable[[np.ndarray], None]] = None  # For UI preview
        
        # State
        self._last_gesture_time = 0.0
        self._last_gesture: Optional[LocalGestureType] = None
        self._frame_buffer: deque = deque(maxlen=2)
        
        # Preview
        self._preview_enabled = False
        self._preview_callback: Optional[Callable[[np.ndarray], None]] = None
    
    def _init_mediapipe(self):
        """Inicializa MediaPipe Tasks Vision."""
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision
            self._mp_vision = mp_vision
            
            # Base options para modelos
            base_options = mp_python.BaseOptions(
                model_asset_path=self._get_model_path('gesture_recognizer.task'),
                delegate=mp_python.BaseOptions.Delegate.GPU
            )
            
            # Gesture Recognizer
            gesture_options = mp_vision.GestureRecognizerOptions(
                base_options=base_options,
                running_mode=mp_vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=self.min_detection_confidence,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=self.min_tracking_confidence,
            )
            self._gesture_recognizer = mp_vision.GestureRecognizer.create_from_options(gesture_options)
            
            # Hand Landmarker
            landmark_base = mp_python.BaseOptions(
                model_asset_path=self._get_model_path('hand_landmarker.task'),
                delegate=mp_python.BaseOptions.Delegate.GPU
            )
            landmark_options = mp_vision.HandLandmarkerOptions(
                base_options=landmark_base,
                running_mode=mp_vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=self.min_detection_confidence,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=self.min_tracking_confidence,
            )
            self._hand_landmarker = mp_vision.HandLandmarker.create_from_options(landmark_options)
            
            print("[LocalGesture] MediaPipe initialized")
            
        except ImportError:
            print("[LocalGesture] MediaPipe not available. Install: pip install mediapipe")
            self._gesture_recognizer = None
            self._hand_landmarker = None
        except Exception as e:
            print(f"[LocalGesture] MediaPipe init failed: {e}")
            self._gesture_recognizer = None
            self._hand_landmarker = None
    
    def _get_model_path(self, model_name: str) -> str:
        """Baixa ou retorna path do modelo MediaPipe."""
        import urllib.request
        import os
        
        model_urls = {
            'gesture_recognizer.task': 'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task',
            'hand_landmarker.task': 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',
        }
        
        cache_dir = Path.home() / ".cache" / "jarvis" / "mediapipe"
        cache_dir.mkdir(parents=True, exist_ok=True)
        model_path = cache_dir / model_name
        
        if not model_path.exists():
            print(f"[LocalGesture] Downloading {model_name}...")
            try:
                urllib.request.urlretrieve(model_urls[model_name], model_path)
                print(f"[LocalGesture] Downloaded {model_name}")
            except Exception as e:
                print(f"[LocalGesture] Download failed: {e}")
                raise
        
        return str(model_path)
    
    def start(self):
        """Inicia reconhecimento em thread separada."""
        if self._running:
            return
        
        self._init_mediapipe()
        
        if not self._gesture_recognizer:
            print("[LocalGesture] Cannot start: MediaPipe not initialized")
            return
        
        # Inicia câmera
        self._cap = cv2.VideoCapture(self.camera_index)
        if not self._cap.isOpened():
            print(f"[LocalGesture] Camera {self.camera_index} not available")
            return
        
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
        self._cap.set(cv2.CAP_PROP_FPS, 30)
        
        self._running = True
        self._thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._thread.start()
        print("[LocalGesture] Started")
    
    def stop(self):
        """Para reconhecimento."""
        self._running = False
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        
        if self._cap:
            self._cap.release()
            self._cap = None
        
        if self._gesture_recognizer:
            self._gesture_recognizer = None
        if self._hand_landmarker:
            self._hand_landmarker = None
        
        print("[LocalGesture] Stopped")
    
    def enable_preview(self, callback: Callable[[np.ndarray], None]):
        """Habilita preview de vídeo para UI."""
        self._preview_enabled = True
        self._preview_callback = callback
    
    def disable_preview(self):
        """Desabilita preview."""
        self._preview_enabled = False
        self._preview_callback = None
    
    def _detection_loop(self):
        """Loop principal de detecção (roda em thread separada)."""
        import mediapipe as mp
        
        while self._running and self._cap and self._cap.isOpened():
            ret, frame = self._cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            
            if self.mirror:
                frame = cv2.flip(frame, 1)
            
            # Converte para RGB (MediaPipe usa RGB)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Cria mp.Image
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            timestamp_ms = int(time.time() * 1000)
            
            # Gesture Recognition
            gesture_result = None
            if self._gesture_recognizer:
                gesture_result = self._gesture_recognizer.recognize_for_video(mp_image, timestamp_ms)
            
            # Hand Landmarks
            landmark_result = None
            if self._hand_landmarker:
                landmark_result = self._hand_landmarker.detect_for_video(mp_image, timestamp_ms)
            
            # Processa resultados
            self._process_results(gesture_result, landmark_result, frame, timestamp_ms)
            
            # Preview para UI
            if self._preview_enabled and self._preview_callback:
                # Desenha landmarks no frame
                annotated = self._draw_annotations(frame, landmark_result)
                try:
                    self._preview_callback(annotated)
                except Exception:
                    pass
            
            # Buffer para possível acesso síncrono
            self._frame_buffer.append(frame)
            
            # Controla FPS (~30fps)
            time.sleep(1/30)
    
    def _process_results(
        self,
        gesture_result: Any,
        landmark_result: Any,
        frame: np.ndarray,
        timestamp_ms: int
    ):
        """Processa resultados do MediaPipe."""
        if not gesture_result or not gesture_result.gestures:
            return
        
        # Pega o melhor gesto
        top_gesture = gesture_result.gestures[0][0]
        mp_gesture_name = top_gesture.category_name
        confidence = top_gesture.score
        handedness = gesture_result.handedness[0][0].display_name if gesture_result.handedness else "Right"
        
        # Mapeia para nosso enum
        gesture_type = self.GESTURE_MAP.get(mp_gesture_name, LocalGestureType.UNKNOWN)
        
        # Cooldown
        now = time.time() * 1000
        if now - self._last_gesture_time < self.gesture_cooldown_ms:
            if gesture_type == self._last_gesture:
                return
        
        # Landmarks
        landmarks = []
        bounding_box = None
        if landmark_result and landmark_result.landmarks:
            hand_landmarks = landmark_result.landmarks[0]
            landmarks = [{"x": lm.x, "y": lm.y, "z": lm.z} for lm in hand_landmarks]
            
            # Bounding box
            xs = [lm.x for lm in hand_landmarks]
            ys = [lm.y for lm in hand_landmarks]
            h, w = frame.shape[:2]
            bounding_box = {
                "x": min(xs) * w,
                "y": min(ys) * h,
                "width": (max(xs) - min(xs)) * w,
                "height": (max(ys) - min(ys)) * h,
            }
        
        result = LocalGestureResult(
            gesture=gesture_type,
            confidence=confidence,
            handedness=handedness,
            landmarks=landmarks,
            bounding_box=bounding_box,
            timestamp=timestamp_ms / 1000,
        )
        
        # Atualiza estado
        self._last_gesture_time = now
        self._last_gesture = gesture_type
        
        # Callback assíncrono
        if self.on_gesture:
            # Usa thread pool para não bloquear loop de detecção
            asyncio.run_coroutine_threadsafe(
                self._async_gesture_callback(result),
                asyncio.get_event_loop()
            )
        
        if self.on_hand_landmarks and landmarks:
            asyncio.run_coroutine_threadsafe(
                self._async_hand_callback(landmarks, handedness),
                asyncio.get_event_loop()
            )
    
    async def _async_gesture_callback(self, result: LocalGestureResult):
        if self.on_gesture:
            self.on_gesture(result)
    
    async def _async_hand_callback(self, landmarks: List[Dict], handedness: str):
        if self.on_hand_landmarks:
            self.on_hand_landmarks(landmarks, handedness)
    
    def _draw_annotations(self, frame: np.ndarray, landmark_result: Any) -> np.ndarray:
        """Desenha anotações no frame (skeleton + gesture label)."""
        annotated = frame.copy()
        
        if landmark_result and landmark_result.landmarks:
            hand_landmarks = landmark_result.landmarks[0]
            h, w = frame.shape[:2]
            
            # Desenha conexões da mão
            connections = [
                (0, 1), (1, 2), (2, 3), (3, 4),  # Thumb
                (0, 5), (5, 6), (6, 7), (7, 8),  # Index
                (5, 9), (9, 10), (10, 11), (11, 12),  # Middle
                (9, 13), (13, 14), (14, 15), (15, 16),  # Ring
                (13, 17), (17, 18), (18, 19), (19, 20),  # Pinky
                (0, 17),  # Palm
            ]
            
            for start, end in connections:
                if start < len(hand_landmarks) and end < len(hand_landmarks):
                    pt1 = (int(hand_landmarks[start].x * w), int(hand_landmarks[start].y * h))
                    pt2 = (int(hand_landmarks[end].x * w), int(hand_landmarks[end].y * h))
                    cv2.line(annotated, pt1, pt2, (0, 255, 0), 2)
            
            # Desenha pontos
            for lm in hand_landmarks:
                pt = (int(lm.x * w), int(lm.y * h))
                cv2.circle(annotated, pt, 4, (0, 0, 255), -1)
            
            # Label do gesto atual
            if self._last_gesture:
                label = f"{self._last_gesture.value} ({self._last_gesture_time/1000:.1f}s ago)"
                cv2.putText(annotated, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        
        return annotated
    
    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Retorna último frame capturado (para UI)."""
        if self._frame_buffer:
            return self._frame_buffer[-1].copy()
        return None
    
    def get_current_gesture(self) -> Optional[LocalGestureType]:
        """Retorna último gesto detectado."""
        return self._last_gesture


class LocalGestureBridge:
    """
    Bridge para integrar LocalGestureRecognizer com Orchestrator.
    Roda no asyncio event loop principal.
    """
    
    def __init__(self, recognizer: LocalGestureRecognizer):
        self.recognizer = recognizer
        self._orchestrator = None
        self._running = False
    
    def set_orchestrator(self, orchestrator):
        self._orchestrator = orchestrator
        # Registra callbacks
        self.recognizer.on_gesture = self._on_gesture
        self.recognizer.on_hand_landmarks = self._on_hand_landmarks
    
    def _on_gesture(self, result: LocalGestureResult):
        """Callback síncrono (chamado da thread de detecção)."""
        if not self._orchestrator:
            return
        
        # Agenda no event loop principal (evita lambda late binding)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        
        def _schedule_gesture(gesture_result: LocalGestureResult):
            loop.call_soon_threadsafe(
                asyncio.create_task, self._orchestrator._on_local_gesture(gesture_result)
            )
        loop.call_soon_threadsafe(_schedule_gesture, result)
    
    def _on_hand_landmarks(self, landmarks: List[Dict], handedness: str):
        """Callback para landmarks da mão."""
        if not self._orchestrator:
            return
        
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        
        # Captura valores atuais para evitar late binding
        captured_landmarks = landmarks
        captured_handedness = handedness
        
        def _schedule_hand(lm: List[Dict], hd: str):
            loop.call_soon_threadsafe(
                asyncio.create_task, self._orchestrator._on_local_hand_landmarks(lm, hd)
            )
        loop.call_soon_threadsafe(_schedule_hand, captured_landmarks, captured_handedness)
    
    async def start(self):
        """Inicia reconhecimento."""
        if self._running:
            return
        self._running = True
        self.recognizer.start()
        print("[LocalGestureBridge] Started")
    
    async def stop(self):
        """Para reconhecimento."""
        self._running = False
        self.recognizer.stop()
        print("[LocalGestureBridge] Stopped")


async def create_local_gesture_bridge(
    camera_index: int = 0,
    resolution: tuple = (1280, 720),
    gesture_config: Optional[Dict] = None,
) -> LocalGestureBridge:
    """Factory para criar e iniciar bridge de gestos local."""
    if gesture_config is None:
        try:
            from jarvis.core.config import settings
            gesture_config = settings.transports.local.gesture.to_dict() if hasattr(settings.transports.local, 'gesture') else {}
        except Exception:
            gesture_config = {}
    
    recognizer = LocalGestureRecognizer(
        camera_index=camera_config.get("camera_index", 0),
        resolution=tuple(gesture_config.get("resolution", [1280, 720])),
        min_detection_confidence=gesture_config.get("min_detection_confidence", 0.5),
        min_tracking_confidence=gesture_config.get("min_tracking_confidence", 0.5),
        gesture_cooldown_ms=gesture_config.get("gesture_cooldown_ms", 500),
        mirror=gesture_config.get("mirror", True),
    )
    bridge = LocalGestureBridge(recognizer)
    return bridge