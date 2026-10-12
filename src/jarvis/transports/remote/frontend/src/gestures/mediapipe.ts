// MediaPipe Gesture Recognizer + Hand Landmarker
// Runs in browser, detects 7 gestures + 21 hand landmarks

import { FilesetResolver, GestureRecognizer as MPGestureRecognizer, HandLandmarker, DrawingUtils } from '@mediapipe/tasks-vision';

export { MPGestureRecognizer as GestureRecognizer };

export interface GestureResult {
  gesture: string;
  confidence: number;
  handedness: 'Left' | 'Right';
  landmarks: Array<{ x: number; y: number; z: number }>;
  boundingBox?: { x: number; y: number; width: number; height: number };
}

type GestureCallback = (result: GestureResult | null) => void;
type HandCallback = (landmarks: Array<{ x: number; y: number; z: number }>, handedness: string) => void;

export class GestureRecognizerWrapper {
  private gestureRecognizer: MPGestureRecognizer | null = null;
  private handLandmarker: HandLandmarker | null = null;
  private drawingUtils: DrawingUtils | null = null;
  private video: HTMLVideoElement | null = null;
  private canvas: HTMLCanvasElement | null = null;
  private ctx: CanvasRenderingContext2D | null = null;
  private virtualCursor: any = null;
  private panelManager: any = null;
  private hud: any = null;
  private animationId: number | null = null;
  private isRunning = false;
  private cameraEnabled = true;
  private lastGestureTime = 0;
  private gestureCooldown = 500; // ms between gesture actions
  
  // Callbacks
  onGesture: GestureCallback = () => {};
  onHand: HandCallback = () => {};

  constructor(
    videoContainer: HTMLElement,
    canvasOverlay: HTMLCanvasElement,
    virtualCursor: any,
    panelManager: any,
    hud: any
  ) {
    this.canvas = canvasOverlay;
    this.virtualCursor = virtualCursor;
    this.panelManager = panelManager;
    this.hud = hud;
    
    // Create video element
    this.video = document.createElement('video');
    this.video.autoplay = true;
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.style.width = '100%';
    this.video.style.height = '100%';
    this.video.style.objectFit = 'cover';
    this.video.style.transform = 'scaleX(-1)'; // Mirror
    videoContainer.appendChild(this.video);
    
    // Setup canvas
    this.ctx = this.canvas.getContext('2d')!;
    this.resize();
  }

  async init(): Promise<void> {
    // Load MediaPipe vision
    const vision = await FilesetResolver.forVisionTasks(
      'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.0/wasm'
    );

    // Gesture Recognizer
    this.gestureRecognizer = await MPGestureRecognizer.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: 'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task',
        delegate: 'GPU',
      },
      runningMode: 'VIDEO',
      numHands: 1,
      minHandDetectionConfidence: 0.5,
      minHandPresenceConfidence: 0.5,
      minTrackingConfidence: 0.5,
    });

    // Hand Landmarker (for skeleton drawing)
    this.handLandmarker = await HandLandmarker.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',
        delegate: 'GPU',
      },
      runningMode: 'VIDEO',
      numHands: 1,
      minHandDetectionConfidence: 0.5,
      minHandPresenceConfidence: 0.5,
      minTrackingConfidence: 0.5,
    });

    this.drawingUtils = new DrawingUtils(this.ctx!);

    // Start camera
    await this.startCamera();
    
    // Start detection loop
    this.isRunning = true;
    this.detectLoop();
    
    console.log('[GestureRecognizer] Initialized');
  }

  private async startCamera(): Promise<void> {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 1280 },
          height: { ideal: 720 },
          facingMode: 'user',
        },
        audio: false,
      });
      
      this.video!.srcObject = stream;
      await this.video!.play();
      
      console.log('[GestureRecognizer] Camera started');
    } catch (error) {
      console.error('[GestureRecognizer] Camera error:', error);
      throw error;
    }
  }

  private detectLoop = async (): Promise<void> => {
    if (!this.isRunning) return;
    
    const video = this.video!;
    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      const timestamp = performance.now();
      
      // Gesture recognition
      if (this.gestureRecognizer) {
        const gestureResult = this.gestureRecognizer.recognizeForVideo(video, timestamp);
        this.processGestureResult(gestureResult);
      }
      
      // Hand landmarks (for drawing)
      if (this.handLandmarker) {
        const landmarkResult = this.handLandmarker.detectForVideo(video, timestamp);
        this.processLandmarkResult(landmarkResult);
      }
      
      // Draw skeleton
      this.drawSkeleton();
    }
    
    this.animationId = requestAnimationFrame(this.detectLoop);
  };

  private processGestureResult(result: any): void {
    if (!result.gestures.length) {
      this.virtualCursor?.setGesture('none');
      this.onGesture?.(null);
      return;
    }

    const topGesture = result.gestures[0][0];
    const handedness = result.handedness[0][0].displayName;
    const confidence = topGesture.score;
    const gestureName = topGesture.categoryName;

    // Map MediaPipe gestures to our actions
    const mappedGesture = this.mapGesture(gestureName);
    
    if (mappedGesture && confidence > 0.7) {
      const now = Date.now();
      if (now - this.lastGestureTime > this.gestureCooldown) {
        this.lastGestureTime = now;
        this.handleGestureAction(mappedGesture, handedness, confidence);
      }
    }

    // Update virtual cursor
    this.virtualCursor?.setGesture(mappedGesture || 'none');

    this.onGesture?.({
      gesture: mappedGesture || gestureName,
      confidence,
      handedness,
      landmarks: [],
    });
  }

  private mapGesture(mpGesture: string): string | null {
    // MediaPipe gestures: 'Closed_Fist', 'Open_Palm', 'Pointing_Up', 'Thumb_Down', 'Thumb_Up', 'Victory', 'ILoveYou'
    const map: Record<string, string> = {
      'Closed_Fist': 'fist',           // Close panel
      'Open_Palm': 'open_palm',        // Neutral
      'Pointing_Up': 'point',          // Cursor follow
      'Victory': 'v_sign',             // Toggle mic
      'Thumb_Up': 'thumbs_up',         // Confirm
      'Thumb_Down': 'thumbs_down',     // Cancel/deny
      'ILoveYou': 'love',              // Special
    };
    return map[mpGesture] || null;
  }

  private handleGestureAction(gesture: string, handedness: string, confidence: number): void {
    const eventMap: Record<string, string> = {
      'fist': 'gesture:fist',
      'v_sign': 'gesture:v_sign',
      'thumbs_up': 'gesture:thumbs_up',
      'thumbs_down': 'gesture:thumbs_down',
      'point': 'gesture:point',
      'open_palm': 'gesture:open_palm',
      'love': 'gesture:love',
    };

    const eventName = eventMap[gesture];
    if (eventName) {
      window.dispatchEvent(new CustomEvent(eventName, { detail: { gesture, handedness, confidence } }));
    }

    switch (gesture) {
      case 'fist':
        // Close panel under cursor
        this.panelManager?.closePanelAtCursor(this.virtualCursor);
        this.hud?.addTranscript('Sistema', 'Painel fechado (punho)', 'system');
        break;
        
      case 'v_sign':
        // Toggle microphone
        this.hud?.addTranscript('Sistema', 'Microfone alternado (V)', 'system');
        break;
        
      case 'thumbs_up':
        // Confirm action
        this.hud?.addTranscript('Sistema', 'Confirmado (joinha)', 'system');
        break;
        
      case 'thumbs_down':
        // Cancel/deny
        this.hud?.addTranscript('Sistema', 'Cancelado (joinha pra baixo)', 'system');
        break;
    }
  }

  private processLandmarkResult(result: any): void {
    if (!result.landmarks.length) return;
    
    const landmarks = result.landmarks[0]; // First hand
    const handedness = result.handedness[0]?.[0]?.displayName || 'Right';
    
    // Use index finger tip (landmark 8) for cursor
    const indexTip = landmarks[8];
    if (indexTip) {
      // Mirror X (video is mirrored)
      const x = 1 - indexTip.x;
      const y = indexTip.y;
      
      this.virtualCursor?.update(x, y);
      this.onHand?.(landmarks, handedness);
    }
  }

  private drawSkeleton(): void {
    if (!this.ctx || !this.handLandmarker || !this.video) return;
    
    const canvas = this.canvas!;
    const video = this.video!;
    
    // Clear canvas
    this.ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    // Draw hand landmarks if available
    // Note: For performance, we could cache the last landmark result
    // For now, skip drawing in detect loop to avoid double detection
    // The landmarks are drawn via the landmark result in processGestureResult
  }

  setCameraEnabled(enabled: boolean): void {
    this.cameraEnabled = enabled;
    if (this.video?.srcObject) {
      const stream = this.video.srcObject as MediaStream;
      const tracks = stream.getVideoTracks();
      tracks?.forEach(t => t.enabled = enabled);
    }
  }

  resize(): void {
    if (!this.canvas || !this.video) return;
    
    const rect = this.canvas.parentElement!.getBoundingClientRect();
    this.canvas.width = rect.width * window.devicePixelRatio;
    this.canvas.height = rect.height * window.devicePixelRatio;
    this.canvas.style.width = rect.width + 'px';
    this.canvas.style.height = rect.height + 'px';
    
    if (this.ctx) {
      this.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    }
  }

  async stop(): Promise<void> {
    this.isRunning = false;
    
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
      this.animationId = null;
    }
    
    if (this.video?.srcObject) {
      const stream = this.video.srcObject as MediaStream;
      const tracks = stream.getTracks();
      tracks.forEach(t => t.stop());
      this.video.srcObject = null;
    }
    
    if (this.gestureRecognizer) {
      this.gestureRecognizer = null;
    }
    
    if (this.handLandmarker) {
      this.handLandmarker = null;
    }
    
    if (this.video?.parentElement) {
      this.video.parentElement.removeChild(this.video);
    }
    
    console.log('[GestureRecognizer] Stopped');
  }
}