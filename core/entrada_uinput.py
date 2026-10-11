"""Mouse e teclado VIRTUAIS pelo kernel (uinput) — o que funciona no Wayland.

26/09/2026, cliente do Big Linux. No Wayland o KWin nao deixa programa nenhum
mexer no mouse pelo X11 (pyautogui e xdotool nao movem nada), e o cliente nao
quis ficar no Plasma X11 ("e muito antigo"). Mas o usuario dele ja tem
permissao de escrita no /dev/uinput (regra do KDE Connect/Steam, ACL `uaccess`),
entao o JARVIS cria dois aparelhos de verdade para o sistema: um MOUSE
ABSOLUTO (como o de maquina virtual: vai direto para o ponto, sem aceleracao) e
um TECLADO. Para o Wayland eles sao iguais a um mouse e um teclado ligados na USB.

`instalar_no_pyautogui()` troca as funcoes do pyautogui por estas, e o resto do
programa (computer use, digitar) continua chamando pyautogui como sempre.
"""
from __future__ import annotations

import fcntl
import os
import struct
import threading
import time

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
SYN_REPORT = 0
REL_HWHEEL, REL_WHEEL = 6, 8
ABS_X, ABS_Y = 0, 1
BTN = {"left": 0x110, "right": 0x111, "middle": 0x112}
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045566, 0x40045567
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
ABS_MAX = 32767
_EVENTO = struct.Struct("llHHi")

# teclas do pyautogui -> codigo do kernel (linux/input-event-codes.h)
KEYS = {
    "esc": 1, "escape": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9,
    "9": 10, "0": 11, "-": 12, "=": 13, "backspace": 14, "tab": 15, "q": 16, "w": 17,
    "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25, "[": 26,
    "]": 27, "enter": 28, "return": 28, "\n": 28, "ctrl": 29, "ctrlleft": 29, "a": 30,
    "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38, ";": 39,
    "'": 40, "`": 41, "shift": 42, "shiftleft": 42, "\\": 43, "z": 44, "x": 45, "c": 46,
    "v": 47, "b": 48, "n": 49, "m": 50, ",": 51, ".": 52, "/": 53, "shiftright": 54,
    "alt": 56, "altleft": 56, " ": 57, "space": 57, "capslock": 58, "f1": 59, "f2": 60,
    "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65, "f8": 66, "f9": 67, "f10": 68,
    "numlock": 69, "scrolllock": 70, "f11": 87, "f12": 88, "ctrlright": 97,
    "altright": 100, "altgr": 100, "home": 102, "up": 103, "pageup": 104, "pgup": 104,
    "left": 105, "right": 106, "end": 107, "down": 108, "pagedown": 109, "pgdn": 109,
    "insert": 110, "delete": 111, "del": 111, "volumemute": 113, "volumedown": 114,
    "volumeup": 115, "pause": 119, "win": 125, "winleft": 125, "super": 125,
    "command": 125, "meta": 125, "winright": 126, "apps": 127, "menu": 127,
    "printscreen": 99, "prtsc": 99, "prtscr": 99, "print": 99,
}
# o que precisa de SHIFT no teclado americano
SHIFT = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
         "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";",
         '"': "'", "~": "`", "<": ",", ">": ".", "?": "/"}


class _Aparelho:
    def __init__(self, nome: str, eventos, teclas=(), rels=(), abss=()):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ev in eventos:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        for k in teclas:
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
        for r in rels:
            fcntl.ioctl(self.fd, UI_SET_RELBIT, r)
        for a in abss:
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, a)
        absmax = [0] * 64
        for a in abss:
            absmax[a] = ABS_MAX
        dev = struct.pack("80sHHHHi", nome.encode()[:79], 0x06, 0x4A56, 0x0001, 1, 0)
        dev += struct.pack("64i", *absmax) + struct.pack("64i", *([0] * 64)) * 3
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def ev(self, tipo, codigo, valor):
        os.write(self.fd, _EVENTO.pack(0, 0, tipo, codigo, valor))

    def syn(self):
        self.ev(EV_SYN, SYN_REPORT, 0)

    def fechar(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            os.close(self.fd)
        except Exception:
            pass


class Entrada:
    """Os dois aparelhos virtuais + a geometria da area de trabalho inteira."""

    def __init__(self):
        self._trava = threading.Lock()
        self.mouse = _Aparelho("JARVIS virtual mouse", (EV_KEY, EV_ABS, EV_REL, EV_SYN),
                               teclas=BTN.values(), rels=(REL_WHEEL, REL_HWHEEL),
                               abss=(ABS_X, ABS_Y))
        self.teclado = _Aparelho("JARVIS virtual keyboard", (EV_KEY, EV_SYN),
                                 teclas=sorted(set(KEYS.values())))
        self.x, self.y = 0, 0
        self._area = None
        time.sleep(0.6)       # o sistema leva um instante para adotar o aparelho novo

    def area(self):
        """(esquerda, topo, largura, altura) da area de trabalho com todos os monitores."""
        if self._area is None:
            try:
                import mss  # noqa: PLC0415
                with mss.mss() as sct:
                    t = sct.monitors[0]
                self._area = (t["left"], t["top"], t["width"], t["height"])
            except Exception:
                self._area = (0, 0, 1920, 1080)
        return self._area

    # ── mouse ──
    def mover(self, x, y):
        esq, topo, larg, alt = self.area()
        ax = round((float(x) - esq) * ABS_MAX / max(1, larg - 1))
        ay = round((float(y) - topo) * ABS_MAX / max(1, alt - 1))
        with self._trava:
            self.mouse.ev(EV_ABS, ABS_X, max(0, min(ABS_MAX, ax)))
            self.mouse.ev(EV_ABS, ABS_Y, max(0, min(ABS_MAX, ay)))
            self.mouse.syn()
        self.x, self.y = int(x), int(y)

    def botao(self, qual="left", apertar=True):
        with self._trava:
            self.mouse.ev(EV_KEY, BTN.get(qual, BTN["left"]), 1 if apertar else 0)
            self.mouse.syn()

    def clicar(self, qual="left", vezes=1, intervalo=0.08):
        for i in range(max(1, int(vezes))):
            self.botao(qual, True)
            time.sleep(0.03)
            self.botao(qual, False)
            if i + 1 < vezes:
                time.sleep(intervalo)

    def rolar(self, passos, horizontal=False):
        with self._trava:
            self.mouse.ev(EV_REL, REL_HWHEEL if horizontal else REL_WHEEL, int(passos))
            self.mouse.syn()

    # ── teclado ──
    def tecla(self, nome, apertar=True):
        codigo = KEYS.get(str(nome).lower())
        if codigo is None:
            return False
        with self._trava:
            self.teclado.ev(EV_KEY, codigo, 1 if apertar else 0)
            self.teclado.syn()
        return True

    def apertar(self, nome):
        if self.tecla(nome, True):
            time.sleep(0.02)
            self.tecla(nome, False)

    def atalho(self, *nomes):
        for n in nomes:
            self.tecla(n, True)
            time.sleep(0.02)
        for n in reversed(nomes):
            self.tecla(n, False)
            time.sleep(0.01)

    def escrever(self, texto, intervalo=0.0):
        """Letra a letra no teclado americano; o que nao existe nele vai colado."""
        texto = str(texto or "")
        if any((not c.isascii()) or (c not in KEYS and c.lower() not in KEYS and c not in SHIFT)
               for c in texto if c not in "\n\t"):
            return self._colar(texto)
        for c in texto:
            if c == "\t":
                self.apertar("tab")
            elif c in SHIFT:
                self.atalho("shift", SHIFT[c])
            elif c.isalpha() and c.isupper():
                self.atalho("shift", c.lower())
            else:
                self.apertar(c)
            if intervalo:
                time.sleep(intervalo)

    def _colar(self, texto):
        import shutil  # noqa: PLC0415
        import subprocess  # noqa: PLC0415
        if shutil.which("wl-copy") and os.environ.get("WAYLAND_DISPLAY"):
            cmd = ["wl-copy"]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-selection", "clipboard"]
        else:
            return
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        p.communicate(texto.encode("utf-8"), timeout=5)
        time.sleep(0.15)
        self.atalho("ctrl", "v")


_ENTRADA = None


def entrada() -> Entrada:
    global _ENTRADA
    if _ENTRADA is None:
        _ENTRADA = Entrada()
    return _ENTRADA


def disponivel() -> bool:
    return os.access("/dev/uinput", os.W_OK)


def instalar_no_pyautogui() -> bool:
    """Troca mouse e teclado do pyautogui pelos aparelhos virtuais. Idempotente."""
    if not disponivel():
        return False
    import pyautogui  # noqa: PLC0415
    if getattr(pyautogui, "_jarvis_uinput", False):
        return True
    e = entrada()

    def _xy(x, y):
        if x is None and y is None:
            return e.x, e.y
        if isinstance(x, (tuple, list)):
            x, y = x[0], x[1]
        return (e.x if x is None else x), (e.y if y is None else y)

    def _andar(x, y, duration=0.0):
        passos = max(1, int(float(duration or 0) / 0.02))
        x0, y0 = e.x, e.y
        for i in range(1, passos + 1):
            e.mover(x0 + (x - x0) * i / passos, y0 + (y - y0) * i / passos)
            if passos > 1:
                time.sleep(0.02)

    def moveTo(x=None, y=None, duration=0.0, *a, **k):
        x, y = _xy(x, y)
        _andar(x, y, duration)

    def moveRel(xOffset=0, yOffset=0, duration=0.0, *a, **k):
        if isinstance(xOffset, (tuple, list)):
            xOffset, yOffset = xOffset
        _andar(e.x + (xOffset or 0), e.y + (yOffset or 0), duration)

    def click(x=None, y=None, clicks=1, interval=0.0, button="left", *a, **k):
        if x is not None or y is not None:
            moveTo(x, y, k.get("duration", 0.0))
            time.sleep(0.05)
        e.clicar(str(button).replace("primary", "left").replace("secondary", "right"),
                 clicks, interval or 0.08)

    def mouseDown(x=None, y=None, button="left", *a, **k):
        if x is not None or y is not None:
            moveTo(x, y)
        e.botao(button, True)

    def mouseUp(x=None, y=None, button="left", *a, **k):
        if x is not None or y is not None:
            moveTo(x, y)
        e.botao(button, False)

    def dragTo(x=None, y=None, duration=0.3, button="left", *a, **k):
        x, y = _xy(x, y)
        e.botao(button, True)
        time.sleep(0.05)
        _andar(x, y, max(0.2, float(duration or 0)))
        time.sleep(0.05)
        e.botao(button, False)

    def dragRel(xOffset=0, yOffset=0, duration=0.3, button="left", *a, **k):
        dragTo(e.x + xOffset, e.y + yOffset, duration, button)

    def scroll(clicks, x=None, y=None, *a, **k):
        if x is not None or y is not None:
            moveTo(x, y)
        e.rolar(int(clicks))

    def hscroll(clicks, x=None, y=None, *a, **k):
        if x is not None or y is not None:
            moveTo(x, y)
        e.rolar(int(clicks), horizontal=True)

    def press(keys, presses=1, interval=0.0, *a, **k):
        for _ in range(max(1, int(presses))):
            for t in (keys if isinstance(keys, (list, tuple)) else [keys]):
                if len(str(t)) == 1 and (str(t) in SHIFT or str(t).isupper()):
                    e.escrever(str(t))
                else:
                    e.apertar(t)
                if interval:
                    time.sleep(interval)

    def hotkey(*keys, **k):
        e.atalho(*keys)

    def typewrite(message, interval=0.0, *a, **k):
        if isinstance(message, (list, tuple)):
            for t in message:
                press(t)
            return
        e.escrever(message, interval or 0.0)

    for nome, f in {"moveTo": moveTo, "moveRel": moveRel, "move": moveRel, "click": click,
                    "leftClick": lambda *a, **k: click(*a, **k),
                    "rightClick": lambda x=None, y=None, *a, **k: click(x, y, button="right"),
                    "middleClick": lambda x=None, y=None, *a, **k: click(x, y, button="middle"),
                    "doubleClick": lambda x=None, y=None, *a, **k: click(x, y, clicks=2),
                    "tripleClick": lambda x=None, y=None, *a, **k: click(x, y, clicks=3),
                    "mouseDown": mouseDown, "mouseUp": mouseUp, "dragTo": dragTo,
                    "dragRel": dragRel, "drag": dragRel, "scroll": scroll, "vscroll": scroll,
                    "hscroll": hscroll, "press": press, "hotkey": hotkey,
                    "keyDown": lambda key, *a, **k: e.tecla(key, True),
                    "keyUp": lambda key, *a, **k: e.tecla(key, False),
                    "typewrite": typewrite, "write": typewrite,
                    "position": lambda *a, **k: pyautogui.Point(e.x, e.y)}.items():
        setattr(pyautogui, nome, f)
    pyautogui._jarvis_uinput = True
    return True
