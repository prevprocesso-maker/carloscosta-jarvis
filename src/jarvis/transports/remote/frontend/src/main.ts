import './style.css';
import { WebSocketManager } from './websocket/websocket';
import { AudioCapture } from './audio/capture';
import { AudioPlayback } from './audio/playback';
import { GestureRecognizerWrapper } from './gestures/mediapipe';
import { VirtualCursor } from './gestures/cursor';
import { HUD } from './ui/hud';
import { PanelManager } from './ui/panels';

// Extend Window for custom events
declare global {
  interface WindowEventMap {
    'gesture:pinch_start': CustomEvent<{ x: number; y: number }>;
    'gesture:pinch_move': CustomEvent<{ x: number; y: number }>;
    'gesture:pinch_end': CustomEvent<{}>;
    'gesture:v_sign': CustomEvent<{}>;
    'gesture:thumbs_up': CustomEvent<{}>;
    'gesture:thumbs_down': CustomEvent<{}>;
  }
}

// Register AudioWorklet (async IIFE)
(async () => {
  if ('audioWorklet' in AudioContext.prototype) {
    await AudioContext.prototype.audioWorklet.addModule('/src/audio/worklet.js');
  }
})();

// App State
interface AppState {
  token: string;
  sessionId: string;
  ws: WebSocketManager | null;
  audioCapture: AudioCapture | null;
  audioPlayback: AudioPlayback | null;
  gestureRecognizer: GestureRecognizerWrapper | null;
  virtualCursor: VirtualCursor | null;
  hud: HUD | null;
  panelManager: PanelManager | null;
  isActive: boolean;
  micEnabled: boolean;
  cameraEnabled: boolean;
  videoContainer: HTMLDivElement | null;
  canvasOverlay: HTMLCanvasElement | null;
}

const state: AppState = {
  token: '',
  sessionId: '',
  ws: null,
  audioCapture: null,
  audioPlayback: null,
  gestureRecognizer: null,
  virtualCursor: null,
  hud: null,
  panelManager: null,
  isActive: false,
  micEnabled: false,
  cameraEnabled: false,
  videoContainer: null as any,
  canvasOverlay: null as any,
};

// DOM Elements
const elements = {
  activateBtn: document.getElementById('activate-btn') as HTMLButtonElement,
  micBtn: document.getElementById('mic-btn') as HTMLButtonElement,
  cameraBtn: document.getElementById('camera-btn') as HTMLButtonElement,
  textInput: document.getElementById('text-input') as HTMLInputElement,
  sendTextBtn: document.getElementById('send-text-btn') as HTMLButtonElement,
  tokenPrompt: document.getElementById('token-prompt') as HTMLDivElement,
  tokenInput: document.getElementById('token-input') as HTMLInputElement,
  tokenSubmit: document.getElementById('token-submit') as HTMLButtonElement,
  errorModal: document.getElementById('error-modal') as HTMLDivElement,
  errorMessage: document.getElementById('error-message') as HTMLSpanElement,
  errorClose: document.getElementById('error-close') as HTMLButtonElement,
  videoContainer: document.getElementById('video-container') as HTMLDivElement,
  canvasOverlay: document.getElementById('canvas-overlay') as HTMLCanvasElement,
  orb: document.getElementById('orb') as HTMLDivElement,
  statusDot: document.getElementById('status-dot') as HTMLDivElement,
  statusText: document.getElementById('status-text') as HTMLSpanElement,
  transcriptLog: document.getElementById('transcript-log') as HTMLDivElement,
  panelsContainer: document.getElementById('panels-container') as HTMLDivElement,
};

// Initialize App
async function init() {
  // Load token from localStorage
  const savedToken = localStorage.getItem('jarvis_token');
  if (savedToken) {
    state.token = savedToken;
    elements.tokenPrompt.style.display = 'none';
  } else {
    elements.tokenPrompt.style.display = 'flex';
  }

  // Generate session ID
  state.sessionId = `web_${crypto.randomUUID().slice(0, 8)}`;

  // Initialize HUD
  state.hud = new HUD(elements.orb, elements.statusDot, elements.statusText, elements.transcriptLog);

  // Initialize Panel Manager
  state.panelManager = new PanelManager(elements.panelsContainer);

  // Initialize Virtual Cursor
  const canvas = elements.canvasOverlay;
  state.virtualCursor = new VirtualCursor(canvas);

  // Event Listeners
  elements.activateBtn.addEventListener('click', toggleActivation);
  elements.micBtn.addEventListener('click', toggleMic);
  elements.cameraBtn.addEventListener('click', toggleCamera);
  elements.sendTextBtn.addEventListener('click', sendTextMessage);
  elements.textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendTextMessage();
  });
  elements.tokenSubmit.addEventListener('click', saveToken);
  elements.tokenInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') saveToken();
  });
  elements.errorClose.addEventListener('click', () => {
    elements.errorModal.style.display = 'none';
  });

  // Resize canvas on window resize
  window.addEventListener('resize', () => {
    if (state.gestureRecognizer) {
      state.gestureRecognizer.resize();
    }
    if (state.virtualCursor) {
      state.virtualCursor.resize();
    }
  });

  // Register Service Worker for PWA
  if ('serviceWorker' in navigator) {
    try {
      await navigator.serviceWorker.register('/sw.js');
      console.log('[SW] Registered');
    } catch (e) {
      console.warn('[SW] Registration failed:', e);
    }
  }
}

async function toggleActivation() {
  if (state.isActive) {
    await deactivate();
  } else {
    await activate();
  }
}

async function activate() {
  if (!state.token) {
    showError('Token não configurado');
    return;
  }

  try {
    state.hud?.setStatus('connecting', 'Conectando...');

    // Initialize WebSocket
    state.ws = new WebSocketManager({ token: state.token, sessionId: state.sessionId });
    state.ws.onMessage = handleWSMessage;
    state.ws.onClose = handleWSClose;
    state.ws.onError = handleWSError;
    await state.ws.connect();

    // Initialize Audio Playback (output)
    state.audioPlayback = new AudioPlayback();
    await state.audioPlayback.init();

    // Initialize Audio Capture (input)
    state.audioCapture = new AudioCapture(state.ws);
    await state.audioCapture.init();

    // Initialize Gesture Recognizer
    state.gestureRecognizer = new GestureRecognizerWrapper(
      state.videoContainer!,
      state.canvasOverlay!,
      state.virtualCursor!,
      state.panelManager!,
      state.hud!
    );
    await state.gestureRecognizer.init();

    state.isActive = true;
    state.micEnabled = true;
    state.cameraEnabled = true;

    elements.activateBtn.textContent = 'DESATIVAR';
    elements.activateBtn.classList.add('active');
    elements.micBtn.classList.add('active');
    elements.cameraBtn.classList.add('active');

    state.hud?.setStatus('online', 'Online');
    state.hud?.addTranscript('Sistema', 'JARVIS ativado. Diga "JARVIS" para começar.', 'system');

  } catch (error) {
    console.error('[App] Activation failed:', error);
    showError(`Falha ao ativar: ${error instanceof Error ? error.message : String(error)}`);
    await deactivate();
  }
}

async function deactivate() {
  state.isActive = false;
  state.micEnabled = false;
  state.cameraEnabled = false;

  elements.activateBtn.textContent = 'ATIVAR';
  elements.activateBtn.classList.remove('active');
  elements.micBtn.classList.remove('active');
  elements.cameraBtn.classList.remove('active');

  // Cleanup
  if (state.audioCapture) {
    await state.audioCapture.stop();
    state.audioCapture = null;
  }
  if (state.audioPlayback) {
    await state.audioPlayback.stop();
    state.audioPlayback = null;
  }
  if (state.gestureRecognizer) {
    await state.gestureRecognizer.stop();
    state.gestureRecognizer = null;
  }
  if (state.ws) {
    await state.ws.disconnect();
    state.ws = null;
  }
  if (state.virtualCursor) {
    state.virtualCursor.hide();
  }

  state.hud?.setStatus('offline', 'Desconectado');
  state.hud?.addTranscript('Sistema', 'JARVIS desativado.', 'system');
}

async function toggleMic() {
  if (!state.isActive) return;
  state.micEnabled = !state.micEnabled;
  elements.micBtn.classList.toggle('active', state.micEnabled);
  
  if (state.audioCapture) {
    state.audioCapture.setEnabled(state.micEnabled);
  }
  
  // Send mic toggle to server
  state.ws?.sendControl({ type: 'mic_toggle', enabled: state.micEnabled });
  
  state.hud?.addTranscript('Sistema', `Microfone ${state.micEnabled ? 'ativado' : 'desativado'}`, 'system');
}

async function toggleCamera() {
  if (!state.isActive) return;
  state.cameraEnabled = !state.cameraEnabled;
  elements.cameraBtn.classList.toggle('active', state.cameraEnabled);
  
  if (state.gestureRecognizer) {
    state.gestureRecognizer.setCameraEnabled(state.cameraEnabled);
  }
  
  state.hud?.addTranscript('Sistema', `Câmera ${state.cameraEnabled ? 'ativada' : 'desativada'}`, 'system');
}

function sendTextMessage() {
  const text = elements.textInput.value.trim();
  if (!text) return;
  
  elements.textInput.value = '';
  
  if (state.ws) {
    state.ws.sendText(text);
    state.hud?.addTranscript('Você', text, 'user');
  }
}

function saveToken() {
  const token = elements.tokenInput.value.trim();
  if (!token) {
    showError('Token não pode ser vazio');
    return;
  }
  
  state.token = token;
  localStorage.setItem('jarvis_token', token);
  elements.tokenPrompt.style.display = 'none';
  state.hud?.addTranscript('Sistema', 'Token salvo com sucesso.', 'system');
}

function handleWSMessage(data: any) {
  switch (data.type) {
    case 'ready':
      console.log('[WS] Ready:', data.session_id);
      break;
    
    case 'transcript':
      state.hud?.addTranscript('JARVIS', data.text, 'jarvis');
      break;
    
    case 'output_transcript':
      state.hud?.addTranscript('JARVIS', data.text, 'jarvis');
      break;
    
    case 'input_transcript':
      state.hud?.addTranscript('Você', data.text, 'user');
      break;
    
    case 'tool_call':
      data.calls.forEach((call: any) => {
        state.hud?.addTranscript('Sistema', `Executando: ${call.name}`, 'system');
      });
      break;
    
    case 'turn_complete':
      // Turn finished
      break;
    
    case 'error':
      showError(data.message);
      break;
    
    case 'ui_open_panel':
      state.panelManager?.openPanel(data.title, data.content);
      break;
    
    case 'ui_close_panels':
      state.panelManager?.closeAll();
      break;
    
    case 'ui_show_text':
      state.hud?.addTranscript('JARVIS', data.text, 'jarvis');
      break;
  }
}

function handleWSClose() {
  console.log('[WS] Disconnected');
  state.hud?.setStatus('offline', 'Desconectado');
  state.isActive = false;
  elements.activateBtn.textContent = 'ATIVAR';
  elements.activateBtn.classList.remove('active');
}

function handleWSError(error: Error) {
  console.error('[WS] Error:', error);
  showError(`Erro de conexão: ${error.message}`);
}

function showError(message: string) {
  elements.errorMessage.textContent = message;
  elements.errorModal.style.display = 'flex';
}

// Initialize
init().catch(console.error);