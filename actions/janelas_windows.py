# -*- coding: utf-8 -*-
"""AS JANELAS DO WINDOWS, VISTAS DE VERDADE (05/09/2026).

Queixa do Diego, e ela vale como regra geral do produto:

    "quando ouve um erro desse ele tem que verificar se realmente esta fechado
     ou aberto pra nao ficar inventando"

O caso: a pasta Downloads estava ABERTA na frente dele e o JARVIS repetiu quatro
vezes *"o pasta de download nao esta aberto — nao havia o que fechar"*. Nao era
teimosia: o `fechar_app` so sabia olhar PROCESSO, e janela de pasta nao tem
processo proprio — ela e uma janela do `explorer.exe`, que esta na lista dos
protegidos justamente para o JARVIS nunca derrubar a area de trabalho inteira.
Entao ele procurava um processo chamado "pasta de download", nao achava, e
concluia que nada estava aberto. Concluia errado.

Este arquivo da ao JARVIS o que faltava: enxergar a JANELA. Sem dependencia nova —
e a API do proprio Windows (`user32`) por `ctypes`.

Fechar e educado: `WM_CLOSE` e o mesmo que clicar no X. Nao ha forca aqui de
proposito; janela que se recusa a fechar (um "quer salvar?" na frente) tem de
ser contada como nao fechada, e nao morta a pauladas.
"""
from __future__ import annotations

import re
import time
import unicodedata

# As duas classes de janela do explorador de arquivos. A primeira e a janela de
# pasta normal; a segunda, a antiga com a arvore do lado.
CLASSES_DE_PASTA = ("CabinetWClass", "ExploreWClass")
_WM_CLOSE = 0x0010

# Janelas que NUNCA devem ser fechadas por casar de raspao com o que ele falou.
# "Program Manager" e a area de trabalho; fechar derruba os icones.
TITULOS_INTOCAVEIS = ("program manager", "windows input experience",
                      "microsoft text input application", "ted", "jarvis")


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def listar(so_pastas: bool = False) -> list[dict]:
    """Todas as janelas visiveis com titulo: [{hwnd, titulo, classe}]."""
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
    except Exception:
        return []
    user32 = ctypes.windll.user32
    achadas: list[dict] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _visitar(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if so_pastas and cls.value not in CLASSES_DE_PASTA:
                return True
            achadas.append({"hwnd": hwnd, "titulo": buf.value, "classe": cls.value})
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(_visitar, 0)
    except Exception:
        return []
    return achadas


def _casa(titulo: str, nome: str, caminho: str = "") -> bool:
    """O titulo da janela e o que o dono chamou de tal coisa?"""
    t, n = normalizar(titulo), normalizar(nome)
    if not t or not n or t in TITULOS_INTOCAVEIS:
        return False
    # "pasta de download" -> as palavras que importam sao "download"
    recheio = {"pasta", "a", "o", "de", "do", "da", "minha", "meu", "as", "os", "e"}
    palavras = [p for p in n.split() if p not in recheio]
    if not palavras:
        return False
    if all(p in t for p in palavras):
        return True
    # "download" x a pasta real "Downloads" (e vice-versa)
    for p in palavras:
        for parte in t.split():
            if len(p) >= 4 and (parte.startswith(p) or p.startswith(parte)):
                return True
    if caminho:
        base = normalizar(str(caminho).replace("\\", "/").rstrip("/").split("/")[-1])
        if base and base in t:
            return True
    return False


def pastas_abertas(nome: str = "", caminho: str = "") -> list[dict]:
    """Janelas de pasta abertas agora; filtradas pelo nome, se houver."""
    janelas = listar(so_pastas=True)
    if not nome and not caminho:
        return janelas
    return [j for j in janelas if _casa(j["titulo"], nome, caminho)]


def fechar_pasta(nome: str, caminho: str = "") -> tuple[int, int]:
    """Fecha as janelas de pasta que casam. Devolve (fechadas, achadas).

    Conferir DEPOIS faz parte do trabalho: o `WM_CLOSE` e um pedido, nao uma
    ordem — uma janela com dialogo aberto na frente ignora e continua ali.
    """
    alvos = pastas_abertas(nome, caminho)
    if not alvos:
        return (0, 0)
    try:
        import ctypes  # noqa: PLC0415
        user32 = ctypes.windll.user32
    except Exception:
        return (0, len(alvos))
    for j in alvos:
        try:
            user32.PostMessageW(j["hwnd"], _WM_CLOSE, 0, 0)
        except Exception:
            pass
    # Ate 1,5 s para elas sumirem, conferindo a cada 0,1 s.
    fim = time.time() + 1.5
    vivas = alvos
    while time.time() < fim:
        time.sleep(0.1)
        ainda = {j["hwnd"] for j in pastas_abertas(nome, caminho)}
        vivas = [j for j in alvos if j["hwnd"] in ainda]
        if not vivas:
            break
    return (len(alvos) - len(vivas), len(alvos))


def alguma_janela_com(nome: str) -> str:
    """O titulo da primeira janela que casa com o nome falado, ou "".

    Serve para a honestidade da resposta: antes de dizer "nao estava aberto",
    o JARVIS olha se ha uma janela com aquela cara na tela.
    """
    for j in listar():
        if _casa(j["titulo"], nome):
            return j["titulo"]
    return ""
