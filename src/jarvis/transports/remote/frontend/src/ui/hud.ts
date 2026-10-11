// HUD: Orbe pulsante, log de transcrição, status

export class HUD {
  private orb: HTMLElement;
  private statusDot: HTMLElement;
  private statusText: HTMLElement;
  private transcriptLog: HTMLElement;
  private isActive = false;

  constructor(
    orb: HTMLElement,
    statusDot: HTMLElement,
    statusText: HTMLElement,
    transcriptLog: HTMLElement
  ) {
    this.orb = orb;
    this.statusDot = statusDot;
    this.statusText = statusText;
    this.transcriptLog = transcriptLog;
  }

  setStatus(state: 'online' | 'offline' | 'connecting', text: string): void {
    this.statusDot.className = state;
    this.statusText.textContent = text;
    
    if (state === 'online') {
      this.orb.classList.add('active');
    } else {
      this.orb.classList.remove('active');
    }
  }

  addTranscript(role: string, text: string, type: 'user' | 'jarvis' | 'system' | 'error' = 'system'): void {
    const entry = document.createElement('div');
    entry.className = 'transcript-entry';
    
    const now = new Date();
    const timeStr = now.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    
    const roleLabels: Record<string, string> = {
      'user': 'VOCÊ',
      'jarvis': 'JARVIS',
      'system': 'SISTEMA',
      'error': 'ERRO',
    };
    
    entry.innerHTML = `
      <div class="transcript-header">
        <span class="transcript-role ${type}">${roleLabels[role] || role.toUpperCase()}</span>
        <span class="transcript-time">${timeStr}</span>
      </div>
      <div class="transcript-text">${this.escapeHtml(text)}</div>
    `;
    
    this.transcriptLog.insertBefore(entry, this.transcriptLog.firstChild);
    
    // Limit entries
    while (this.transcriptLog.children.length > 50) {
      this.transcriptLog.removeChild(this.transcriptLog.lastChild!);
    }
    
    // Auto-scroll to top (newest)
    this.transcriptLog.scrollTop = 0;
  }

  private escapeHtml(text: string): string {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  clear(): void {
    this.transcriptLog.innerHTML = '';
  }
}