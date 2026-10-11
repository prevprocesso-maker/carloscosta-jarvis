// Panel Manager: draggable panels (mouse + pinch gesture)

interface Panel {
  id: string;
  element: HTMLElement;
  title: string;
  content: string;
  x: number;
  y: number;
  isDragging: boolean;
  dragOffsetX: number;
  dragOffsetY: number;
}

export class PanelManager {
  private container: HTMLElement;
  private panels: Map<string, Panel> = new Map();
  private panelCounter = 0;
  private virtualCursor: any = null;

  constructor(container: HTMLElement, virtualCursor?: any) {
    this.container = container;
    this.virtualCursor = virtualCursor;
  }

  setVirtualCursor(cursor: any): void {
    this.virtualCursor = cursor;
  }

  openPanel(title: string, content: string): string {
    const id = `panel_${++this.panelCounter}_${Date.now()}`;
    
    const element = document.createElement('div');
    element.className = 'panel';
    element.dataset.panelId = id;
    
    // Stagger position
    const staggerX = 20 + (this.panelCounter % 5) * 20;
    const staggerY = 100 + Math.floor(this.panelCounter / 5) * 20;
    
    element.style.left = `${staggerX}px`;
    element.style.top = `${staggerY}px`;
    element.style.position = 'absolute';
    
    element.innerHTML = `
      <div class="panel-header">
        <span class="panel-title">${this.escapeHtml(title)}</span>
        <button class="panel-close" data-panel-id="${id}">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
      <div class="panel-content">${this.escapeHtml(content)}</div>
    `;
    
    this.container.appendChild(element);
    
    // Position
    element.style.left = `${20 + (this.panels.size % 5) * 20}px`;
    element.style.top = `${100 + Math.floor(this.panels.size / 5) * 20}px`;
    
    // Draggable
    this.makeDraggable(element);
    
    // Close button
    const closeBtn = element.querySelector('.panel-close') as HTMLButtonElement;
    closeBtn.addEventListener('click', () => this.closePanel(id));
    
    const panel: Panel = {
      id,
      element,
      title,
      content,
      x: 20,
      y: 100,
      isDragging: false,
      dragOffsetX: 0,
      dragOffsetY: 0,
    };
    
    this.panels.set(id, panel);
    
    // Pinch gesture handling
    window.addEventListener('gesture:pinch_start', this.handlePinchStart.bind(this));
    window.addEventListener('gesture:pinch_move', this.handlePinchMove.bind(this));
    window.addEventListener('gesture:pinch_end', this.handlePinchEnd.bind(this));
    
    return id;
  }

  closePanel(id: string): void {
    const panel = this.panels.get(id);
    if (panel) {
      panel.element.remove();
      this.panels.delete(id);
    }
  }

  closeAll(): void {
    this.panels.forEach(panel => panel.element.remove());
    this.panels.clear();
  }

  closePanelAtCursor(virtualCursor: any): void {
    // Find panel under cursor
    const cursorPos = virtualCursor?.getPosition();
    if (!cursorPos) return;
    
    const screenX = cursorPos.x * window.innerWidth;
    const screenY = cursorPos.y * window.innerHeight;
    
    this.panels.forEach((panel, id) => {
      const rect = panel.element.getBoundingClientRect();
      if (
        screenX >= rect.left &&
        screenX <= rect.right &&
        screenY >= rect.top &&
        screenY <= rect.bottom
      ) {
        this.closePanel(id);
      }
    });
  }

  private makeDraggable(element: HTMLElement): void {
    let isDragging = false;
    let offsetX = 0;
    let offsetY = 0;
    
    const header = element.querySelector('.panel-header') as HTMLElement;
    
    header.addEventListener('mousedown', (e) => {
      isDragging = true;
      const rect = element.getBoundingClientRect();
      offsetX = e.clientX - rect.left;
      offsetY = e.clientY - rect.top;
      element.style.zIndex = '1000';
      element.style.transition = 'none';
      document.body.style.userSelect = 'none';
    });
    
    document.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      
      const x = e.clientX - offsetX;
      const y = e.clientY - offsetY;
      
      // Constrain to viewport
      const maxX = window.innerWidth - element.offsetWidth - 10;
      const maxY = window.innerHeight - element.offsetHeight - 10;
      
      element.style.left = `${Math.max(10, Math.min(x, maxX))}px`;
      element.style.top = `${Math.max(10, Math.min(y, maxY))}px`;
    });
    
    document.addEventListener('mouseup', () => {
      if (isDragging) {
        isDragging = false;
        element.style.zIndex = '';
        element.style.transition = '';
        document.body.style.userSelect = '';
      }
    });
    
    // Touch support
    header.addEventListener('touchstart', (e) => {
      isDragging = true;
      const touch = e.touches[0];
      const rect = element.getBoundingClientRect();
      offsetX = touch.clientX - rect.left;
      offsetY = touch.clientY - rect.top;
      element.style.zIndex = '1000';
    }, { passive: true });
    
    document.addEventListener('touchmove', (e) => {
      if (!isDragging) return;
      
      const touch = e.touches[0];
      const x = touch.clientX - offsetX;
      const y = touch.clientY - offsetY;
      
      const maxX = window.innerWidth - element.offsetWidth - 10;
      const maxY = window.innerHeight - element.offsetHeight - 10;
      
      element.style.left = `${Math.max(10, Math.min(x, maxX))}px`;
      element.style.top = `${Math.max(10, Math.min(y, maxY))}px`;
    }, { passive: true });
    
    document.addEventListener('touchend', () => {
      if (isDragging) {
        isDragging = false;
        element.style.zIndex = '';
      }
    });
  }

  // Pinch gesture handling (for future integration with MediaPipe)
  private draggingPanel: string | null = null;
  private pinchOffsetX = 0;
  private pinchOffsetY = 0;

  handlePinchStart(e: CustomEvent): void {
    const { x, y } = e.detail; // normalized 0-1
    const screenX = x * window.innerWidth;
    const screenY = y * window.innerHeight;
    
    this.panels.forEach((panel, id) => {
      const rect = panel.element.getBoundingClientRect();
      if (
        screenX >= rect.left &&
        screenX <= rect.right &&
        screenY >= rect.top &&
        screenY <= rect.bottom
      ) {
        this.draggingPanel = id;
        const rect = panel.element.getBoundingClientRect();
        panel.dragOffsetX = screenX - rect.left;
        panel.dragOffsetY = screenY - rect.top;
        panel.isDragging = true;
        panel.element.style.zIndex = '1000';
      }
    });
  }

  handlePinchMove(e: CustomEvent): void {
    if (!this.draggingPanel) return;
    
    const { x, y } = e.detail;
    const screenX = x * window.innerWidth;
    const screenY = y * window.innerHeight;
    
    const panel = this.panels.get(this.draggingPanel);
    if (!panel) return;
    
    const newX = screenX - panel.dragOffsetX;
    const newY = screenY - panel.dragOffsetY;
    
    const maxX = window.innerWidth - panel.element.offsetWidth - 10;
    const maxY = window.innerHeight - panel.element.offsetHeight - 10;
    
    panel.element.style.left = `${Math.max(10, Math.min(newX, maxX))}px`;
    panel.element.style.top = `${Math.max(10, Math.min(newY, maxY))}px`;
  }

  handlePinchEnd(): void {
    if (this.draggingPanel) {
      const panel = this.panels.get(this.draggingPanel);
      if (panel) {
        panel.isDragging = false;
        panel.element.style.zIndex = '';
      }
      this.draggingPanel = null;
    }
  }

  private escapeHtml(text: string): string {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}