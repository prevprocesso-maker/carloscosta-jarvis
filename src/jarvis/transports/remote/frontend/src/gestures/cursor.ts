// Virtual Cursor: follows index finger with exponential smoothing + mirrored X

interface CursorPosition {
  x: number; // 0-1 normalized
  y: number; // 0-1 normalized
}

type GestureType = 'none' | 'point' | 'pinch' | 'fist' | 'v_sign' | 'thumbs_up' | 'thumbs_down' | 'open_palm';

export class VirtualCursor {
  private element: HTMLElement | null = null;
  private trailElements: HTMLElement[] = [];
  private currentPos: CursorPosition = { x: 0.5, y: 0.5 };
  private targetPos: CursorPosition = { x: 0.5, y: 0.5 };
  private smoothing = 0.15; // Exponential smoothing factor (lower = smoother)
  private currentGesture: GestureType = 'none';
  private canvas: HTMLCanvasElement;
  private isVisible = false;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.createCursorElement();
    this.createTrailElements();
  }

  private createCursorElement(): void {
    this.element = document.createElement('div');
    this.element.className = 'cursor-dot';
    this.element.style.position = 'fixed';
    this.element.style.pointerEvents = 'none';
    this.element.style.zIndex = '1000';
    this.element.style.transition = 'transform 0.08s ease-out';
    document.body.appendChild(this.element);
  }

  private createTrailElements(): void {
    for (let i = 0; i < 5; i++) {
      const trail = document.createElement('div');
      trail.className = 'cursor-trail';
      trail.style.position = 'fixed';
      trail.style.pointerEvents = 'none';
      trail.style.zIndex = '999';
      trail.style.opacity = '0';
      trail.style.transition = 'opacity 0.1s, transform 0.05s';
      document.body.appendChild(trail);
      this.trailElements.push(trail);
    }
  }

  update(normalizedX: number, normalizedY: number): void {
    // Clamp to [0, 1]
    this.targetPos.x = Math.max(0, Math.min(1, normalizedX));
    this.targetPos.y = Math.max(0, Math.min(1, normalizedY));
    
    if (!this.isVisible) {
      this.currentPos = { ...this.targetPos };
      this.isVisible = true;
      this.render();
    }
  }

  setGesture(gesture: GestureType): void {
    this.currentGesture = gesture;
    if (this.element) {
      this.element.className = `cursor-dot ${gesture}`;
    }
  }

  private render(): void {
    if (!this.element) return;

    // Exponential smoothing
    this.currentPos.x += (this.targetPos.x - this.currentPos.x) * this.smoothing;
    this.currentPos.y += (this.targetPos.y - this.currentPos.y) * this.smoothing;

    // Convert to screen coordinates
    const screenX = this.currentPos.x * window.innerWidth;
    const screenY = this.currentPos.y * window.innerHeight;

    // Update cursor position
    this.element.style.transform = `translate(${screenX}px, ${screenY}px) translate(-50%, -50%)`;

    // Update trail (simplified - just last position)
    // In a full implementation, you'd maintain a history buffer
    
    // Continue animation loop
    if (this.isVisible) {
      requestAnimationFrame(() => this.render());
    }
  }

  hide(): void {
    this.isVisible = false;
    if (this.element) {
      this.element.style.display = 'none';
    }
    this.trailElements.forEach(t => t.style.display = 'none');
  }

  show(): void {
    this.isVisible = true;
    if (this.element) {
      this.element.style.display = 'block';
    }
    this.trailElements.forEach(t => t.style.display = 'block');
  }

  setSmoothing(factor: number): void {
    this.smoothing = Math.max(0.01, Math.min(1, factor));
  }

  getPosition(): CursorPosition {
    return { ...this.currentPos };
  }

  getGesture(): GestureType {
    return this.currentGesture;
  }

  resize(): void {
    // VirtualCursor uses window.innerWidth/innerHeight directly in render()
    // so no explicit resize handling needed - it reads window dimensions each frame
  }

  destroy(): void {
    this.hide();
    if (this.element) {
      this.element.remove();
      this.element = null;
    }
    this.trailElements.forEach(t => t.remove());
    this.trailElements = [];
  }
}