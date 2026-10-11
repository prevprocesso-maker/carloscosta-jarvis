// AudioWorkletProcessor for PCM16 resampling
// Capture: Float32 48kHz -> PCM16 16kHz
// Playback: PCM16 24kHz -> Float32 48kHz

class JARVISAudioProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    
    // Capture buffer (48kHz -> 16kHz)
    this.captureBuffer = new Float32Array(0);
    this.captureRatio = 48000 / 16000; // 3:1
    
    // Playback buffer (24kHz -> 48kHz)
    this.playbackBuffer = new Float32Array(0);
    this.playbackRatio = 48000 / 24000; // 2:1
    this.playbackQueue: Float32Array[] = [];
    this.isPlaying = false;
    
    // Message handling
    this.port.onmessage = (e) => {
      if (e.data.type === 'play') {
        this.enqueuePlayback(e.data.buffer);
      } else if (e.data.type === 'flush') {
        this.flushPlayback();
      } else if (e.data.type === 'set_enabled') {
        this.captureEnabled = e.data.enabled;
      }
    };
    
    this.captureEnabled = true;
  }
  
  // Linear interpolation resampling
  resample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
    if (fromRate === toRate) return input;
    
    const ratio = fromRate / toRate;
    const outputLength = Math.floor(input.length / ratio);
    const output = new Float32Array(outputLength);
    
    for (let i = 0; i < outputLength; i++) {
      const srcIndex = i * ratio;
      const idx = Math.floor(srcIndex);
      const frac = srcIndex - idx;
      
      if (idx + 1 < input.length) {
        output[i] = input[idx] * (1 - frac) + input[idx + 1] * frac;
      } else {
        output[i] = input[idx];
      }
    }
    
    return output;
  }
  
  // Float32 to PCM16 (Int16Array)
  floatToPCM16(float32: Float32Array): Int16Array {
    const pcm16 = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      const clamped = Math.max(-1, Math.min(1, float32[i]));
      pcm16[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7FFF;
    }
    return pcm16;
  }
  
  // PCM16 (Int16Array) to Float32
  pcm16ToFloat(pcm16: Int16Array): Float32Array {
    const float32 = new Float32Array(pcm16.length);
    for (let i = 0; i < pcm16.length; i++) {
      float32[i] = pcm16[i] / (pcm16[i] < 0 ? 0x8000 : 0x7FFF);
    }
    return float32;
  }
  
  enqueuePlayback(pcm16Buffer: ArrayBuffer) {
    const pcm16 = new Int16Array(pcm16Buffer);
    const float32 = this.pcm16ToFloat(pcm16);
    const resampled = this.resample(float32, 24000, 48000);
    this.playbackQueue.push(resampled);
    this.isPlaying = true;
  }
  
  flushPlayback() {
    this.playbackQueue = [];
    this.isPlaying = false;
  }
  
  process(inputs: Float32[][], outputs: Float32[][], parameters: Record<string, Float32Array>): boolean {
    const input = inputs[0]?.[0]; // Float32 48kHz mono
    const output = outputs[0]?.[0]; // Float32 48kHz mono
    
    if (!output) return true;
    
    // --- CAPTURE: 48kHz -> 16kHz PCM16 ---
    if (input && this.captureEnabled) {
      this.captureBuffer = this.appendBuffer(this.captureBuffer, input);
      
      // Process when we have enough for 16kHz chunk (640 samples at 16kHz = 1920 at 48kHz)
      const captureChunkSize = 640 * this.captureRatio; // 1920
      while (this.captureBuffer.length >= captureChunkSize) {
        const chunk = this.captureBuffer.slice(0, captureChunkSize);
        this.captureBuffer = this.captureBuffer.slice(captureChunkSize);
        
        // Resample 48kHz -> 16kHz
        const resampled = this.resample(chunk, 48000, 16000);
        
        // Convert to PCM16
        const pcm16 = this.floatToPCM16(resampled);
        
        // Send to main thread
        this.port.postMessage({
          type: 'capture',
          buffer: pcm16.buffer
        }, [pcm16.buffer]);
      }
    }
    
    // --- PLAYBACK: 24kHz PCM16 -> 48kHz Float32 ---
    if (this.isPlaying && this.playbackQueue.length > 0) {
      let outputFilled = 0;
      
      while (outputFilled < output.length && this.playbackQueue.length > 0) {
        const chunk = this.playbackQueue[0];
        const remaining = chunk.length - this.playbackOffset;
        const toCopy = Math.min(remaining, output.length - outputFilled);
        
        output.set(chunk.subarray(this.playbackOffset, this.playbackOffset + toCopy), outputFilled);
        
        outputFilled += toCopy;
        this.playbackOffset += toCopy;
        
        if (this.playbackOffset >= chunk.length) {
          this.playbackQueue.shift();
          this.playbackOffset = 0;
        }
      }
      
      // Fill remaining with silence
      if (outputFilled < output.length) {
        output.fill(0, outputFilled);
        if (this.playbackQueue.length === 0) {
          this.isPlaying = false;
        }
      }
    } else {
      // No playback - silence
      output.fill(0);
    }
    
    return true;
  }
  
  appendBuffer(existing: Float32Array, newData: Float32Array): Float32Array {
    if (existing.length === 0) return newData;
    const combined = new Float32Array(existing.length + newData.length);
    combined.set(existing);
    combined.set(newData, existing.length);
    return combined;
  }
  
  // State for playback
  playbackOffset = 0;
  captureEnabled = true;
}

registerProcessor('jarvis-audio-processor', JARVISAudioProcessor);

// Export for TypeScript
export {};