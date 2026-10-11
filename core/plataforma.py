# -*- coding: utf-8 -*-
"""O QUE MUDA DE UM SISTEMA PARA O OUTRO — num lugar so (05/09/2026).

Escrito quando o Diego pegou um cliente de Mac. O JARVIS nasceu no Windows e
esta cheio de coisa que so existe la; o levantamento antes de comecar deu isto:

    imports de Windows no TOPO dos arquivos ............ 0
    chamadas de Windows dentro de funcao .............. 18 arquivos

O primeiro numero e a boa noticia e e por isso que o porte e viavel: nada de
Windows e carregado quando o programa abre, entao no Mac ele SOBE, ouve, pensa
pela VPS e fala. O que quebra sao as funcoes de mexer no computador, quando
chamadas — e e isso que este arquivo comeca a resolver.

A REGRA AO MEXER AQUI: no Windows o comportamento tem de ficar IDENTICO ao de
antes. O cliente de Windows esta em producao; o Mac e o que esta chegando.
Cada funcao daqui faz no Windows exatamente o que o codigo fazia antes, e so
acrescenta o galho dos outros sistemas.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

SISTEMA = platform.system()          # "Windows" | "Darwin" | "Linux"
E_WINDOWS = SISTEMA == "Windows"
E_MAC = SISTEMA == "Darwin"
E_LINUX = not (E_WINDOWS or E_MAC)


def sem_janela() -> dict:
    """Os `kwargs` que escondem a janela preta do console.

    No Windows o `main.py` ja obriga isso em TODO `Popen`; aqui fica a versao
    para quem chama `subprocess.run`. Fora do Windows nao ha janela para
    esconder e o dicionario volta vazio.
    """
    if E_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def abrir_no_sistema(alvo) -> bool:
    """Abre arquivo, pasta ou endereco no programa padrao do sistema.

    `os.startfile` NAO EXISTE fora do Windows — nem da erro de import, some do
    modulo `os` e estoura `AttributeError` na hora de usar. Eram oito lugares
    chamando ele direto, entre eles o atalho de abrir pasta, que e um dos
    pedidos mais comuns do dono.
    """
    caminho = str(alvo or "").strip()
    if not caminho:
        return False
    try:
        if E_WINDOWS:
            os.startfile(caminho)          # noqa: S606  (o mesmo de sempre)
            return True
        cmd = ["open", caminho] if E_MAC else ["xdg-open", caminho]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# ── AS PASTAS DO DONO ────────────────────────────────────────────────────────
#
# No Windows o caminho certo sai do registro (a pessoa pode ter movido a pasta
# Downloads para outro disco). No Mac e no Linux nao ha registro: sao os nomes
# em ingles dentro da casa do usuario, e o Mac nunca os traduz no disco — a
# tela mostra "Transferencias", mas a pasta se chama "Downloads".
_NOMES_PADRAO = {
    "desktop": "Desktop", "downloads": "Downloads", "documents": "Documents",
    "pictures": "Pictures", "music": "Music", "videos": "Movies" if E_MAC else "Videos",
}


def pasta_conhecida(chave: str):
    """Devolve o caminho da pasta padrao ('downloads', 'desktop'...) ou None."""
    nome = _NOMES_PADRAO.get(str(chave or "").strip().lower())
    if not nome:
        return None
    p = Path.home() / nome
    return p if p.is_dir() else None


# ── UM JARVIS DE CADA VEZ ────────────────────────────────────────────────────
#
# No Windows isso e um cadeado nomeado do kernel (ver `_ja_existe_um_jarvis` no
# main.py). No Mac nao existe CreateMutexW, e sem substituto o cliente abriria
# dois JARVIS que brigam pela porta 8000 do painel — o mesmo defeito que o
# cadeado do Windows foi criado para matar, em 01/09/2026.
#
# Aqui e um arquivo trancado com `flock`: se o programa for morto, o sistema
# solta a tranca sozinho, entao nao fica lixo travando a proxima abertura (que
# e o defeito classico de "arquivo de bloqueio" feito na mao).
_TRANCA = None


def ja_existe_outro(nome: str = "jarvis-cyberbot") -> bool:
    global _TRANCA
    if E_WINDOWS:
        return False                       # quem manda no Windows e o mutex
    try:
        import fcntl  # noqa: PLC0415
        base = (Path.home() / "Library" / "Application Support" / "JARVIS-CyberBot"
                if E_MAC else Path.home() / ".cache" / "jarvis-cyberbot")
        base.mkdir(parents=True, exist_ok=True)
        f = open(base / f"{nome}.lock", "w")     # noqa: SIM115
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return True                    # outro processo esta com a tranca
        f.write(str(os.getpid()))
        f.flush()
        _TRANCA = f                        # guardado: se fechar, a tranca abre
        return False
    except Exception:
        return False                       # sem tranca e melhor que sem JARVIS


# ── FOTO DA TELA NO WAYLAND (26/09/2026, cliente do Big Linux) ─────────────
#
# O mss e o pyautogui tiram a foto pelo X11. Na sessao Wayland do KDE eles so
# enxergam o XWayland, e a foto do cliente saiu 100% PRETA nos tres monitores:
# "olhe minha tela" respondia "a tela esta toda preta". O Spectacle, que e do
# proprio KDE, tem permissao de fotografar a tela — e por ele que vai.
def na_sessao_wayland() -> bool:
    return (sys.platform.startswith("linux")
            and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland")


# Mouse e teclado de mentira (pyautogui, xdotool) tambem sao do X11: no Wayland
# o KWin nao deixa programa nenhum mexer neles (testado no cliente: `xdotool
# mousemove` nao move nada). O computer use rodava 3 minutos "trabalhando" sem
# conseguir clicar, e o ESC nao parava porque o Wayland tambem nao deixa ler
# tecla de outra janela. Melhor dizer na hora o que falta.
MSG_PRECISA_X11 = (
    "Para eu mexer no mouse e no teclado, o computador precisa estar na sessao "
    "Plasma (X11). Na sessao atual, que e Wayland, o Linux bloqueia isso. Saia da "
    "sessao e, na tela de login, escolha Plasma (X11) no canto de baixo.")


def bloqueio_do_mouse() -> str:
    """O recado de por que nao da para mexer no mouse aqui, ou "" se da.

    No Wayland, se o usuario pode escrever no /dev/uinput (no Big Linux pode,
    pela regra do KDE Connect), o JARVIS liga o mouse e o teclado VIRTUAIS do
    kernel no lugar do pyautogui (core/entrada_uinput.py) e segue normalmente.
    O cliente nao quis ficar no Plasma X11 ("e muito antigo")."""
    if not na_sessao_wayland():
        return ""
    try:
        from core.entrada_uinput import instalar_no_pyautogui  # noqa: PLC0415
        if instalar_no_pyautogui():
            return ""
    except Exception as exc:
        print(f"[plataforma] mouse virtual nao ligou: {exc}")
    return MSG_PRECISA_X11


def foto_da_tela_wayland(tudo: bool = False):
    """A tela como PIL.Image (so o monitor principal, ou todos com `tudo`).

    None se nao der (sem Spectacle, sem sessao); quem chama cai no caminho de
    sempre."""
    import tempfile  # noqa: PLC0415
    import shutil  # noqa: PLC0415
    if not shutil.which("spectacle"):
        return None
    arq = Path(tempfile.gettempdir()) / f"jarvis-tela-{os.getpid()}.png"
    try:
        arq.unlink(missing_ok=True)
        env = dict(os.environ)
        env.pop("QT_QPA_PLATFORM", None)     # o Spectacle roda nativo no Wayland
        subprocess.run(["spectacle", "-b", "-n", "-f", "-o", str(arq)],
                       capture_output=True, timeout=15, env=env)
        if not arq.is_file():
            return None
        from PIL import Image  # noqa: PLC0415
        img = Image.open(arq)
        img.load()
        img = img.convert("RGB")
    except Exception:
        return None
    finally:
        try:
            arq.unlink(missing_ok=True)
        except Exception:
            pass
    if tudo:
        return img
    # recorta o monitor principal, com a geometria que o XWayland conhece
    try:
        import mss  # noqa: PLC0415
        with mss.mss() as sct:
            todos, mons = sct.monitors[0], sct.monitors[1:]
        principal = next((m for m in mons if m.get("is_primary")), mons[0] if mons else None)
        if principal and img.size == (todos["width"], todos["height"]):
            x = principal["left"] - todos["left"]
            y = principal["top"] - todos["top"]
            return img.crop((x, y, x + principal["width"], y + principal["height"]))
    except Exception:
        pass
    return img


def avisar_na_tela(titulo: str, texto: str) -> None:
    """Recado para quem abriu o programa, sem depender da janela do Qt."""
    if E_WINDOWS:
        try:
            import ctypes  # noqa: PLC0415
            ctypes.windll.user32.MessageBoxW(None, texto, titulo, 0x40)
            return
        except Exception:
            pass
    elif E_MAC:
        try:
            escapado = texto.replace('"', "'")
            subprocess.run(
                ["osascript", "-e",
                 f'display dialog "{escapado}" with title "{titulo}" buttons {{"OK"}}'],
                timeout=20, check=False)
            return
        except Exception:
            pass
    print(f"[{titulo}] {texto}")


def diagnostico() -> dict:
    """O que este computador tem — para eu ver de longe o que falta no Mac."""
    info = {"sistema": SISTEMA, "versao": platform.mac_ver()[0] if E_MAC
            else platform.version(), "arquitetura": platform.machine(),
            "python": sys.version.split()[0], "bibliotecas": {}}
    for mod in ("PyQt6", "sounddevice", "numpy", "faster_whisper", "psutil",
                "requests", "websockets", "cv2", "mss", "pyautogui", "PIL"):
        try:
            __import__(mod)
            info["bibliotecas"][mod] = "ok"
        except Exception as exc:
            info["bibliotecas"][mod] = f"FALTA ({type(exc).__name__})"
    return info


if __name__ == "__main__":
    import json
    print(json.dumps(diagnostico(), indent=2, ensure_ascii=False))
