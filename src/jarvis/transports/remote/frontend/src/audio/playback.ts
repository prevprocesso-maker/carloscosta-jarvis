// Audio Playback: AudioContext + queue + flush on interrupt
// Receives PCM16 24kHz chunks, plays via AudioWorklet

export class AudioPlayback {
  private context: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private queue: ArrayBuffer[] = [];
  private isPlaying = false;
  private currentBuffer: ArrayBuffer | null = null;

  async init(): Promise<void> {
    this.context = new AudioContext({ sampleRate: 48000 });
    
    // Load AudioWorklet
    await this.context.audioWorklet.addModule('/src/audio/worklet.js');
    
    // Create worklet node (output only)
    this.workletNode = new AudioWorkletNode(this.context, 'jarvis-audio-processor', {
      outputChannelCount: [1],
    });

    // Connect to destination
    this.workletNode.connect(this.context.destination);

    // Resume if suspended
    if (this.context.state === 'suspended') {
      await this.context.resume();
    }

    console.log('[AudioPlayback] Initialized');
  }

  play(chunk: ArrayBuffer): void {
    if (!this.enabled) return;
    
    this.queue.push(chunk);
    this.processQueue();
  }

  private async processQueue(): Promise<void> {
    if (this.isPlaying || this.queue.length === 0) return;
    
    this.isPlaying = true;
    
    while (this.queue.length > 0 && this.enabled) {
      const chunk = this.queue.shift()!;
      this.currentBuffer = chunk;
      
      // Send to worklet
      this.workletNode?.port.postMessage({ type: 'play', buffer: chunk });
      
      // Wait for chunk to finish (approximate)
      // 24kHz PCM16: chunk byte length / 2 / 24000 = seconds
      const durationMs = (chunk.byteLength / 2 / 24000) * 1000;
      await this.sleep(durationMs + 50); // Small buffer
    }
    
    this.isPlaying = false;
    this.currentBuffer = null;
  }

  flush(): void {
    this.queue = [];
    this.currentBuffer = null;
    this.workletNode?.port.postMessage({ type: 'flush' });
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (!enabled) {
      this.flush();
    }
  }

  private enabled = true;

  async stop(): Promise<void> {
    this.enabled = false;
    this.flush();
    
    if (this.workletNode) {
      this.workletNode.disconnect();
      this.workletNode = null;
    }
    
    if (this.context) {
      await this.context.close();
      this.context = null;
    }
    
    console.log('[AudioPlayback] Stopped');
  }

  private sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}