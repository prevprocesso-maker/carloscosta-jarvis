# -*- coding: utf-8 -*-
"""Digitar AO VIVO na janela aberta — o dono ve o texto nascendo na tela.

Por que existe (01/09/2026)
---------------------------
O Diego pediu "digita um codigo de calculadora nesse Bloco de Notas" e o JARVIS
CRIOU um arquivo na Area de Trabalho (code_helper). Nao era isso: ele queria
ver o JARVIS codando na frente dele, letra por letra, dentro do Bloco de Notas.
A regra que ele deu: "digita/escreve AI" = digitar ao vivo onde esta aberto;
"cria um codigo" = gerar o arquivo e por onde ele pediu.

Como digita: `SendInput` com KEYEVENTF_UNICODE, caractere a caractere. E o
unico jeito que nao depende do layout do teclado — o pyautogui pula acento e
simbolo que nao existe na tecla. Enter e Tab vao como tecla de verdade, porque
alguns editores ignoram o '\\n' unicode.

Cuidado conhecido: editor com auto-indentacao (VS Code) vai dobrar o recuo do
codigo digitado. No Bloco de Notas e no Word sai exato.
"""
from __future__ import annotations

import ctypes
import os
import re
import time
from ctypes import wintypes

# ---------------------------------------------------------------- SendInput
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_VK_RETURN, _VK_TAB = 0x0D, 0x09

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_ubyte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


# O ultimo texto que o JARVIS escreveu na tela. E o que o `file_controller`
# salva quando o dono manda "salva esse bloco de notas" — nesse pedido o
# conteudo esta na TELA, nao na conversa, entao o modelo manda so o caminho.
# Ver `create_file` em actions/file_controller.py.
ULTIMO_TEXTO = ""


def _enviar(entradas: list) -> None:
    arr = (_INPUT * len(entradas))(*entradas)
    ctypes.windll.user32.SendInput(len(entradas), arr, ctypes.sizeof(_INPUT))


def _tecla(vk: int) -> None:
    baixo = _INPUT(type=_INPUT_KEYBOARD, u=_INPUTUNION(ki=_KEYBDINPUT(wVk=vk)))
    cima = _INPUT(type=_INPUT_KEYBOARD, u=_INPUTUNION(ki=_KEYBDINPUT(wVk=vk, dwFlags=_KEYEVENTF_KEYUP)))
    _enviar([baixo, cima])


def _caractere(ch: str) -> None:
    # Um caractere pode ocupar duas unidades UTF-16 (emoji); cada uma vai
    # como um evento unicode proprio, e o Windows junta.
    bruto = ch.encode("utf-16-le")
    for i in range(0, len(bruto), 2):
        cod = int.from_bytes(bruto[i:i + 2], "little")
        baixo = _INPUT(type=_INPUT_KEYBOARD, u=_INPUTUNION(ki=_KEYBDINPUT(wScan=cod, dwFlags=_KEYEVENTF_UNICODE)))
        cima = _INPUT(type=_INPUT_KEYBOARD, u=_INPUTUNION(ki=_KEYBDINPUT(wScan=cod, dwFlags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)))
        _enviar([baixo, cima])


# ---------------------------------------------------------------- Linux
# 26/09/2026, cliente do Big Linux. O SendInput e so do Windows: no Linux esta
# ferramenta nunca digitou nada. Aqui vai pelo teclado virtual do kernel
# (core/entrada_uinput.py), que funciona no Wayland e no X11. Codigo (texto com
# recuo) vai COLADO linha a linha: o Kate/KWrite recuam sozinho a cada Enter e
# digitar o recuo por cima dobraria a indentacao — colar nao dispara isso, e o
# dono continua vendo as linhas nascerem uma a uma.
import sys as _sys
_LINUX = _sys.platform.startswith("linux")


def _colar_linux(texto: str) -> None:
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    from core.entrada_uinput import entrada  # noqa: PLC0415
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        cmd = ["wl-copy"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
    elif shutil.which("xsel"):
        cmd = ["xsel", "-b", "-i"]
    else:
        return
    # quem guarda a area de transferencia fica vivo em segundo plano: sem o
    # DEVNULL ele herdava a saida do JARVIS e segurava quem estivesse lendo
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    p.communicate(texto.encode("utf-8"), timeout=5)
    time.sleep(0.08)
    entrada().atalho("ctrl", "v")
    time.sleep(0.05)


def _digitar_texto_linux(texto: str, intervalo: float, pausa_linha: float, deve_parar) -> int:
    from core.entrada_uinput import entrada, KEYS, SHIFT  # noqa: PLC0415
    e = entrada()
    texto = str(texto or "").replace("\r\n", "\n").replace("\r", "\n")
    linhas = texto.split("\n")
    e_codigo = any(l.startswith((" ", "\t")) for l in linhas) and len(linhas) > 2
    n = 0
    if e_codigo:
        for i, linha in enumerate(linhas):
            if deve_parar and deve_parar():
                break
            pedaco = linha + ("\n" if i + 1 < len(linhas) else "")
            if pedaco:
                _colar_linux(pedaco)
            n += len(pedaco)
            time.sleep(pausa_linha)
        return n
    for i, ch in enumerate(texto):
        if deve_parar and (i % 40 == 0) and deve_parar():
            break
        if ch == "\n":
            e.apertar("enter")
            time.sleep(pausa_linha)
        elif ch == "\t":
            e.apertar("tab")
        elif ch.isascii() and (ch in KEYS or ch.lower() in KEYS or ch in SHIFT):
            e.escrever(ch)
            time.sleep(intervalo)
        else:
            _colar_linux(ch)            # acento e simbolo fora do teclado americano
        n += 1
    return n


_EDITORES_LINUX = ("kwrite", "kate", "gnome-text-editor", "gedit", "mousepad", "xed")


def _preparar_janela_linux(nome: str, log) -> str:
    """Bloco de notas = um editor de texto novo (janela nova ganha o foco).
    Outra janela: no X11 traz para a frente pelo wmctrl; no Wayland digita onde
    estiver o foco (o Wayland nao deixa programa escolher a janela da frente)."""
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    chave = (nome or "").strip().lower()
    if not chave:
        return ""
    if chave in ("bloco de notas", "notepad", "editor", "text editor", "kate", "kwrite"):
        editor = next((x for x in _EDITORES_LINUX if shutil.which(x)), "")
        if not editor:
            return "Nao achei um editor de texto instalado."
        # DOCUMENTO NOVO com nome: sem arquivo o KWrite/Kate abrem na tela de
        # boas-vindas, que nao tem onde digitar — o texto sumia (testado no
        # PC do cliente, 26/09/2026). O arquivo nasce vazio na pasta
        # Documentos e o dono salva com Ctrl+S quando quiser.
        from pathlib import Path as _P  # noqa: PLC0415
        try:
            r = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, timeout=5)
            pasta = _P(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else _P.home()
        except Exception:
            pasta = _P.home()
        doc = pasta / time.strftime("JARVIS-%Y-%m-%d-%H%M%S.txt")
        try:
            from actions.open_app import _iniciar_linux  # noqa: PLC0415
            _iniciar_linux([editor, str(doc)])
        except Exception:
            subprocess.Popen([editor, str(doc)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.5)                 # janela nova engole as primeiras teclas
        return ""
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland" and shutil.which("wmctrl"):
        termos = _JANELAS.get(chave) or (chave,)
        for t in termos:
            if subprocess.run(["wmctrl", "-a", t], capture_output=True).returncode == 0:
                time.sleep(0.5)
                return ""
        log(f"[digitar] nao consegui trazer {nome} para a frente; digitando assim mesmo")
    return ""


def digitar_texto(texto: str, intervalo: float = 0.012, pausa_linha: float = 0.12,
                  deve_parar=None) -> int:
    """Digita o texto na janela em foco. Devolve quantos caracteres saiu."""
    if _LINUX:
        return _digitar_texto_linux(texto, intervalo, pausa_linha, deve_parar)
    n = 0
    for i, ch in enumerate(str(texto or "").replace("\r\n", "\n").replace("\r", "\n")):
        if deve_parar and (i % 40 == 0) and deve_parar():
            break
        if ch == "\n":
            _tecla(_VK_RETURN)
            time.sleep(pausa_linha)
        elif ch == "\t":
            _tecla(_VK_TAB)
        else:
            _caractere(ch)
            time.sleep(intervalo)
        n += 1
    return n


# ---------------------------------------------------------------- janela
_JANELAS = {
    "bloco de notas": ("bloco de notas", "notepad"), "notepad": ("bloco de notas", "notepad"),
    "word": ("word",), "wordpad": ("wordpad",), "vscode": ("visual studio code",),
    "visual studio code": ("visual studio code",), "excel": ("excel",),
    "navegador": ("chrome", "edge", "firefox"), "chrome": ("chrome",), "edge": ("edge",),
}


def _achar_janela(termos) -> int:
    import win32gui  # noqa: PLC0415
    achadas = []

    def _cb(h, _):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            if t and any(x in t.lower() for x in termos):
                achadas.append(h)
        return True
    win32gui.EnumWindows(_cb, None)
    return achadas[0] if achadas else 0


def _focar(hwnd: int) -> bool:
    import win32con  # noqa: PLC0415
    import win32gui  # noqa: PLC0415
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.2)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        # SO SE O WINDOWS RECUSAR: o truque do Alt solto libera o
        # SetForegroundWindow — mas um Alt solto tambem ABRE O MODO DE MENU do
        # programa, e as primeiras letras digitadas vao para o menu em vez do
        # texto. Foi assim que "# Teste " sumiu tres vezes seguidas
        # (01/09/2026). Por isso o Esc logo depois: fecha o menu sem efeito
        # sobre o texto.
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, _KEYEVENTF_KEYUP, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.2)
        _tecla(0x1B)
        time.sleep(0.15)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _preparar_janela(nome: str, log) -> str:
    """Poe a janela pedida na frente; abre o Bloco de Notas se for ele e nao existir."""
    if _LINUX:
        return _preparar_janela_linux(nome, log)
    chave = (nome or "").strip().lower()
    termos = _JANELAS.get(chave) or ((chave,) if chave else ())
    if not termos:
        return ""                       # digita onde estiver o foco
    hwnd = _achar_janela(termos)
    if not hwnd and chave in ("bloco de notas", "notepad"):
        try:
            os.startfile("notepad.exe")  # noqa: S606
        except Exception:
            return "Nao consegui abrir o Bloco de Notas."
        for _ in range(40):
            time.sleep(0.15)
            hwnd = _achar_janela(termos)
            if hwnd:
                break
        # Janela recem-criada engole as primeiras teclas mesmo ja na frente
        # (medido: 8 caracteres perdidos duas vezes seguidas). Respirar.
        time.sleep(1.5)
    if not hwnd:
        return f"Nao achei a janela {nome} aberta."
    # ESPERAR O FOCO CHEGAR (01/09/2026). No teste, os 8 primeiros caracteres
    # ("# Teste ") se perderam: o SetForegroundWindow ja tinha voltado, mas o
    # teclado ainda apontava para a janela antiga. So digitar depois de VER a
    # janela na frente, e ainda dar um respiro.
    import win32gui  # noqa: PLC0415
    _focar(hwnd)
    for _ in range(20):
        if win32gui.GetForegroundWindow() == hwnd:
            break
        time.sleep(0.1)
        _focar(hwnd)
    else:
        log(f"[digitar] nao consegui trazer {nome} para a frente; digitando assim mesmo")
    time.sleep(0.4)
    return ""


# ---------------------------------------------------------------- conteudo
def _sem_cerca(texto: str) -> str:
    s = str(texto or "").strip()
    m = re.search(r"```[a-zA-Z0-9_+-]*\n(.*?)```", s, re.S)
    if m:
        s = m.group(1)
    return s.strip("\n") + "\n"


def _janela_no_pedido(pedido: str) -> str:
    p = pedido.lower()
    for chave in ("bloco de notas", "notepad", "wordpad", "visual studio code", "vscode",
                  "word", "excel", "navegador", "chrome", "edge"):
        if chave in p:
            return chave
    return ""


def digitar_na_tela(parameters: dict | None = None, player=None, gerar=None,
                    deve_parar=None) -> str:
    """Entrada da ferramenta.

    `conteudo`  texto pronto para digitar (o modelo ja escreveu), ou
    `pedido`    o que escrever ("codigo Python de uma calculadora") — o JARVIS
                gera pelo cerebro (`gerar(pedido, linguagem) -> str`).
    `janela`    onde digitar: bloco de notas (padrao quando o pedido cita),
                word, vscode... Sem janela: digita onde o cursor estiver.
    """
    try:  # Linux no Wayland: mouse e teclado bloqueados (core/plataforma.py)
        from core.plataforma import bloqueio_do_mouse  # noqa: PLC0415
        _recado = bloqueio_do_mouse()
        if _recado:
            return _recado
    except Exception:
        pass
    p = parameters or {}

    def log(msg):
        if player is not None and hasattr(player, "write_log"):
            try:
                player.write_log(msg)
            except Exception:
                pass

    conteudo = str(p.get("conteudo") or p.get("texto") or "").strip()
    pedido = str(p.get("pedido") or p.get("description") or "").strip()
    linguagem = str(p.get("linguagem") or p.get("language") or "").strip()
    janela = str(p.get("janela") or "").strip() or _janela_no_pedido(pedido)

    if not conteudo:
        if not pedido:
            return "Faltou dizer o que digitar."
        if gerar is None:
            return "Nao tenho como escrever o conteudo agora."
        t0 = time.monotonic()
        try:
            conteudo = _sem_cerca(gerar(pedido, linguagem))
        except Exception as exc:
            return f"Nao consegui escrever o conteudo: {str(exc)[:80]}"
        log(f"[digitar] conteudo gerado em {time.monotonic() - t0:.1f}s, {len(conteudo)} caracteres")
        if not conteudo.strip():
            return "O cerebro devolveu vazio; nao digitei nada."
        # O QUE FOI ESCRITO VAI PARA O DIARIO (05/09/2026). A frase de volta e
        # so "digitei N linhas" — com ela, quando o Diego perguntou "voce lembra
        # o que voce escreveu?", nao havia onde olhar e ele respondeu que nao
        # sabia. Agora o comeco do texto fica guardado.
        try:
            from memory.diario import anotar as _anotar  # noqa: PLC0415
            _anotar("fez", f"escrevi este texto: {conteudo.strip()[:280]}")
        except Exception:
            pass
        # GUARDADO INTEIRO PARA O "SALVA ESSE BLOCO DE NOTAS" (05/09/2026).
        #
        # Quando ele manda salvar, o modelo pede um arquivo com o CAMINHO mas
        # sem o conteudo — porque o conteudo esta na tela, nao na conversa. Sem
        # isto o arquivo nascia vazio. O `file_controller` vem buscar aqui.
        global ULTIMO_TEXTO
        ULTIMO_TEXTO = conteudo

    erro = _preparar_janela(janela, log)
    if erro:
        return erro
    if deve_parar and deve_parar():
        return "Parei antes de comecar."
    # Teto de tamanho: 12 mil caracteres sao ~3 minutos digitando. E termina
    # com UMA quebra de linha, como todo arquivo de codigo.
    conteudo = conteudo[:12000].strip("\r\n") + "\n"
    t0 = time.monotonic()
    n = digitar_texto(conteudo, deve_parar=deve_parar)
    linhas = conteudo[:n].count("\n")
    onde = f" no {janela}" if janela else ""
    if n < len(conteudo):
        return f"Parei no meio, como voce pediu: {linhas} linhas digitadas{onde}."
    log(f"[digitar] {n} caracteres em {time.monotonic() - t0:.0f}s")
    return f"Pronto, digitei {linhas} linhas{onde}."
