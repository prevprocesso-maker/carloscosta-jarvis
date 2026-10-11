// Audio Capture: getUserMedia + AudioWorklet
// Converts microphone input to PCM16 16kHz via AudioWorklet

import { WebSocketManager } from '../websocket/websocket';

export class AudioCapture {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private ws: WebSocketManager | null = null;
  private enabled = true;

  constructor(ws: WebSocketManager) {
    this.ws = ws;
  }

  async init(): Promise<void> {
    // Request microphone permission
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: 48000,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });

    // Create AudioContext
    this.context = new AudioContext({ sampleRate: 48000 });
    
    // Load AudioWorklet
    await this.context.audioWorklet.addModule('/src/audio/worklet.js');
    
    // Create worklet node
    this.workletNode = new AudioWorkletNode(this.context, 'jarvis-audio-processor', {
      outputChannelCount: [1],
    });

    // Handle capture messages from worklet
    this.workletNode.port.onmessage = (e) => {
      if (e.data.type === 'capture' && this.enabled && this.ws?.isConnected) {
        this.ws.sendAudio(e.data.buffer);
      }
    };

    // Connect microphone -> worklet -> destination (silent)
    this.source = this.context.createMediaStreamSource(this.stream);
    this.source.connect(this.workletNode).connect(this.context.destination);
    
    // Resume context if suspended
    if (this.context.state === 'suspended') {
      await this.context.resume();
    }

    console.log('[AudioCapture] Initialized');
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (this.workletNode) {
      this.workletNode.port.postMessage({ type: 'set_enabled', enabled });
    }
  }

  async stop(): Promise<void> {
    this.enabled = false;
    
    if (this.source) {
      this.source.disconnect();
      this.source = null;
    }
    
    if (this.workletNode) {
      this.workletNode.disconnect();
      this.workletNode = null;
    }
    
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
    
    if (this.context) {
      await this.context.close();
      this.context = null;
    }
    
    console.log('[AudioCapture] Stopped');
  }
}