// WebSocket Manager: binary audio + JSON control + reconnect + auth

export interface WSMessage {
  type: string;
  [key: string]: any;
}

export interface WSConfig {
  token: string;
  sessionId: string;
  host?: string;
}

type MessageHandler = (data: any) => void;
type CloseHandler = () => void;
type ErrorHandler = (error: Error) => void;

export class WebSocketManager {
  private ws: WebSocket | null = null;
  private config: WSConfig;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30000;
  private isConnecting = false;
  private shouldReconnect = false;
  
  // Callbacks
  onMessage: MessageHandler = () => {};
  onClose: CloseHandler = () => {};
  onError: ErrorHandler = () => {};

  constructor(config: WSConfig) {
    this.config = {
      host: 'localhost:8000',
      ...config,
    };
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  async connect(): Promise<void> {
    if (this.isConnecting || this.isConnected) return;
    
    this.isConnecting = true;
    this.shouldReconnect = true;

    return new Promise((resolve, reject) => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${protocol}//${this.config.host}/ws/voice?token=${encodeURIComponent(this.config.token)}&session_id=${encodeURIComponent(this.config.sessionId)}`;
      
      console.log('[WS] Connecting to:', url);
      
      this.ws = new WebSocket(url);
      this.ws.binaryType = 'arraybuffer';

      this.ws.onopen = () => {
        console.log('[WS] Connected');
        this.isConnecting = false;
        this.reconnectAttempts = 0;
        this.reconnectDelay = 1000;
        resolve();
      };

      this.ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          // Binary audio from server
          this.onMessage({ type: 'audio', buffer: event.data });
        } else if (typeof event.data === 'string') {
          try {
            const data = JSON.parse(event.data);
            this.onMessage(data);
          } catch (e) {
            console.error('[WS] Parse error:', e);
          }
        }
      };

      this.ws.onclose = (event) => {
        console.log('[WS] Closed:', event.code, event.reason);
        this.isConnecting = false;
        this.onClose();
        
        if (this.shouldReconnect && !event.wasClean) {
          this.scheduleReconnect();
        }
      };

      this.ws.onerror = (error) => {
        console.error('[WS] Error:', error);
        this.onError(error instanceof Error ? error : new Error('WebSocket error'));
        
        if (this.isConnecting) {
          this.isConnecting = false;
          reject(error instanceof Error ? error : new Error('Connection failed'));
        }
      };
    });
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('[WS] Max reconnect attempts reached');
      this.onError(new Error('Max reconnect attempts reached'));
      return;
    }

    const delay = Math.min(
      this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts),
      this.maxReconnectDelay
    ) + Math.random() * 1000; // Jitter

    console.log(`[WS] Reconnecting in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts + 1})`);
    
    this.reconnectAttempts++;
    
    setTimeout(() => {
      if (this.shouldReconnect) {
        this.connect().catch(() => {});
      }
    }, delay);
  }

  sendAudio(buffer: ArrayBuffer): void {
    if (this.isConnected) {
      this.ws!.send(buffer);
    }
  }

  sendText(text: string): void {
    if (this.isConnected) {
      this.ws!.send(JSON.stringify({ type: 'text', text }));
    }
  }

  sendControl(data: object): void {
    if (this.isConnected) {
      this.ws!.send(JSON.stringify(data));
    }
  }

  async disconnect(): Promise<void> {
    this.shouldReconnect = false;
    
    if (this.ws) {
      if (this.ws.readyState === WebSocket.OPEN) {
        this.ws.close(1000, 'Client disconnect');
      }
      this.ws = null;
    }
    
    this.reconnectAttempts = 0;
    this.reconnectDelay = 1000;
  }
}