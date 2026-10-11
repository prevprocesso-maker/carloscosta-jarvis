# -*- coding: utf-8 -*-
"""DESENHAR DE VERDADE num editor de imagem — 07/09/2026.

Por que existe
--------------
O Diego pediu "abre o Paint e desenha uma casa" e o JARVIS abriu o Paint e
riscou quatro tracos tortos. Nao era falta de talento do modelo: era o CAMINHO.
O computer use (`usar_computador`) manda UMA foto da tela para a VPS e recebe
UM movimento de mouse por volta, a ~5 segundos cada. Desenhar assim e desenhar
com quatro movimentos — da nisso mesmo.

A licao e a mesma do `digitar_na_tela` (01/09): o que precisa de MUITOS passos
seguidos nao pode ir passo a passo pela rede. O cerebro entrega o desenho
INTEIRO de uma vez, como uma lista de formas, e o computador executa aqui,
local, em segundos.

Como e feito no Paint (Windows 11)
----------------------------------
Nao e mao livre imitando forma: e a FERRAMENTA CERTA do Paint para cada peca.
Retangulo com a ferramenta Retangulo, sol com a Oval, telhado com o Triangulo,
estrela com a Estrela — geometria limpa, sem tremido. Cada peca sai com cor de
contorno, cor de dentro e espessura escolhidas.

Quem enxerga os botoes e a API de acessibilidade do Windows (UIA), a mesma que
um leitor de tela usa. Medido em 07/09: achar um botao pelo nome custa 7 a 30
MILISSEGUNDOS. E por nome, nao por pixel — muda a resolucao, muda o tema, muda
a janela de lugar, e continua achando.

A folha sempre no MAIOR tamanho que couber na janela — pedido do Diego, e ele
tem razao duas vezes: o desenho fica grande, e o Paint GUARDA o tamanho da
ultima imagem (basta ele ter aberto uma foto pequena um dia para todo desenho
nascer do tamanho de um selo).

E NUNCA o mesmo desenho duas vezes: um empurrao sorteado entra no fim da
encomenda (`_EMPURROES`). Modelo de linguagem repete — pedir "uma casa" duas
vezes da a mesma casa. Pedir "seja criativo" nao resolve; mudar o PEDIDO sim.

Windows 10 e Windows 11
-----------------------
Nao se pergunta a versao do Windows, e sim qual Paint esta na frente
(`_e_paint_do_windows_11`): o Paint novo tambem chega em Windows 10 pela Loja.
  - Paint NOVO (WinUI): desenho ao vivo com as ferramentas, como acima.
  - Paint CLASSICO de faixa (o do Windows 10), Photoshop, GIMP, Krita: o mesmo
    plano e desenhado com o Pillow e colado com Ctrl+V. Sai perfeito e de uma
    vez, e vira imagem normal para ele editar.
NOTA HONESTA: nesta maquina so existe o Paint novo. O caminho de colar foi
testado, mas nao contra um Windows 10 nem contra um Photoshop de verdade.

A partir de uma FOTO do dono
----------------------------
  - "desenha essa foto": a foto vira desenho AQUI no computador, com o OpenCV
    que o JARVIS ja carrega — manchas de cor grandes primeiro, contorno por
    cima (a ordem de quem pinta de verdade). Nao passa pela rede e nao gasta
    cota.
  - "poe um chapeu nessa foto": a foto e aberta no Paint e vira a folha; o
    desenho pedido vai por cima dela.
A foto so e usada se o dono FALAR nela (`_CITA_A_FOTO`) — senao uma imagem
arrastada para a janela ha uma hora sequestraria um "desenha uma casa".

AS QUATRO ARMADILHAS DESTE ARQUIVO (custaram a tarde)
------------------------------------------------------
1. **UIA so funciona em thread MTA.** Com a thread em STA (que e o padrao do
   comtypes, e o JARVIS ja importa comtypes por causa do pycaw) a PRIMEIRA
   chamada trava para sempre, sem erro e sem tempo limite. Por isso todo o
   trabalho roda numa thread NOVA que comeca com `CoInitializeEx(None, 0)` — e
   o comtypes e importado ANTES dela, senao o import estoura la dentro.
2. **`SetCursorPos` + `mouse_event` nao desenha no Paint novo.** O jeito antigo
   falha quase sempre e em silencio: de 18 formas sairam 2. O Paint e WinUI 3 e
   le entrada por pointer — tem de ser `SendInput` com MOVE|ABSOLUTE.
3. **O Enter NAO carimba a forma.** Depois do Enter ela continua editavel: se
   eu trocar a cor, ela troca junto. Por isso cada peca saia com a cor da peca
   SEGUINTE. Quem carimba e TROCAR DE FERRAMENTA (ver `carimbar`).
4. **Cor 1 e o PREENCHIMENTO e Cor 2 e o CONTORNO** — ao contrario do Paint
   antigo. Medido com uma bola de Cor 1 vermelha e Cor 2 amarela.

E mais duas que valem o lembrete: chamada de UIA que estoura o tempo deixa o
Paint travado para SEMPRE (so reabrindo) — por isso nada aqui abre o dialogo
"Editar cores", que foi quem travou o Paint quatro vezes nos testes; e a ALCA
de redimensionar a folha fica FORA da folha (4 px), nao em cima dela.
"""
from __future__ import annotations

import ctypes
import json
import math
import random
import re
import subprocess
import threading
import time
import unicodedata

# O ultimo plano que o cerebro devolveu — para o dono perguntar "o que voce
# desenhou?" e para eu conferir quando algo sair torto.
ULTIMO_PLANO: dict = {}
# E o ultimo PEDIDO, com a hora. E o que faz "melhora essa casa" funcionar: a
# frase dele nao diz o que desenhar, quem sabe sou eu. Ver `ultimo_desenho()`.
ULTIMO_PEDIDO: str = ""
ULTIMO_EM: float = 0.0


def ultimo_desenho(dentro_de_s: float = 1800.0) -> str:
    """O que ele mandou desenhar por ultimo, se foi ha pouco. Senao, vazio."""
    if not ULTIMO_PEDIDO or not ULTIMO_EM:
        return ""
    return ULTIMO_PEDIDO if (time.monotonic() - ULTIMO_EM) <= dentro_de_s else ""

# ── OS DOIS TETOS DO DESENHO, E POR QUE ELES EXISTEM ─────────────────────
#
# O Diego pegou isto olhando o desenho de uma rua: *"ele sempre para no passo
# 44, ele nao concluiu esse desenho e falou que terminou"*. Estava certo — o
# teto era 44 pecas, o plano vinha com mais, e eu cortava CALADO. Como o plano
# vai do fundo para a frente, o que sobrava de fora era justamente o detalhe:
# o segundo carro ficou sem as rodas.
#
# Duas coisas mudaram. O teto subiu para 70 (a peca custa ~1,3 s, entao 70 sao
# ~90 s de desenho, que e o que ele aguenta ver), e o que ficar de fora e DITO
# na resposta. Teto que corta em silencio faz o assistente mentir — e mentira
# pequena e a que estraga a confianca.
TETO_DE_PECAS = 70
TETO_DE_TEMPO_S = 210.0     # se estourar, ele para e AVISA quantas faltaram

_VK_RETURN, _VK_ESCAPE = 0x0D, 0x1B
_MOUSE_DOWN, _MOUSE_UP = 0x0002, 0x0004

# Identificadores do UIA (sao numeros fixos da Microsoft, nao mudam).
_UIA_Name, _UIA_AutoId, _UIA_ControlType = 30005, 30011, 30003
_TIPO_BOTAO, _TIPO_ITEM_LISTA, _TIPO_MENU = 50000, 50007, 50011
_TIPO_RADIO, _TIPO_SLIDER = 50013, 50015
_ESCOPO_DESCENDENTES = 4
_PADRAO_FAIXA = 10003            # RangeValuePattern (o cursor de Tamanho)


# ─────────────────────────────────────────────────────────── texto e cor
def _vazio(el) -> bool:
    """O UIA nao devolve None quando nao acha: devolve um PONTEIRO NULO.

    Custou a primeira rodada de testes (07/09/2026). `if el is None` dava False
    para o ponteiro nulo, o codigo seguia e a linha seguinte estourava com
    "NULL COM pointer access" — 13 formas puladas de uma vez. O ponteiro nulo
    e FALSO num if, entao a conferencia certa e esta.
    """
    return el is None or not bool(el)


def _sem_acento(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").strip().lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t)


def _hex_para_rgb(valor: str):
    s = str(valor or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def _distancia_de_cor(a, b) -> float:
    """Quao diferentes duas cores parecem AO OLHO, nao na conta.

    A media do vermelho entra na conta de proposito: sem ela, o azul escuro e
    o preto ficam "perto" e um ceu noturno vira um borrao. E a formula de
    ponderacao classica, boa o bastante e sem depender de biblioteca nenhuma.
    """
    rm = (a[0] + b[0]) / 2.0
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt((2 + rm / 256.0) * dr * dr + 4 * dg * dg
                     + (2 + (255 - rm) / 256.0) * db * db)


# Só serve de rede: o normal é ler os nomes e as cores do próprio Paint.
_PALETA_CONHECIDA = {
    "preto": (0, 0, 0), "cinza": (127, 127, 127), "vermelho escuro": (136, 0, 21),
    "vermelho": (237, 28, 36), "laranja": (255, 127, 39), "amarelo": (255, 242, 0),
    "verde": (34, 177, 76), "turquesa": (0, 162, 232), "indigo": (63, 72, 204),
    "roxo": (163, 73, 164), "branco": (255, 255, 255), "cinza claro": (195, 195, 195),
    "marrom": (185, 122, 87), "rosa": (255, 174, 201), "dourado": (255, 201, 14),
    "amarelo claro": (239, 228, 176), "lima": (181, 230, 29),
    "turqueza claro": (153, 217, 234), "cinza azulado": (112, 146, 190),
    "lavanda": (200, 191, 231),
}

# Como o dono (e o modelo) chamam a cor -> como o Paint chama.
_APELIDO_DE_COR = {
    "azul": "turquesa", "azul claro": "turqueza claro", "azul escuro": "indigo",
    "ceu": "turqueza claro", "celeste": "turqueza claro", "anil": "indigo",
    "verde claro": "lima", "verde escuro": "verde", "verde limao": "lima",
    "bege": "amarelo claro", "creme": "amarelo claro", "areia": "amarelo claro",
    "ouro": "dourado", "amarelo ouro": "dourado", "mostarda": "dourado",
    "vinho": "vermelho escuro", "bordo": "vermelho escuro", "telha": "vermelho escuro",
    "tijolo": "vermelho escuro", "salmao": "rosa", "pele": "rosa",
    "madeira": "marrom", "terra": "marrom", "cafe": "marrom", "castanho": "marrom",
    "prata": "cinza claro", "chumbo": "cinza", "grafite": "cinza",
    "violeta": "roxo", "lilas": "lavanda", "magenta": "roxo", "purpura": "roxo",
    "ciano": "turquesa", "turqueza": "turquesa", "laranja escuro": "laranja",
    "cinza-azulado": "cinza azulado",
}

# Como o modelo pede a forma -> o nome do botao no Paint (sem acento).
_APELIDO_DE_FORMA = {
    "retangulo": "retangulo", "quadrado": "retangulo", "caixa": "retangulo",
    "retangulo arredondado": "retangulo arredondado",
    "quadrado arredondado": "retangulo arredondado",
    "circulo": "oval", "elipse": "oval", "oval": "oval", "bola": "oval",
    "triangulo": "triangulo", "triangulo retangulo": "triangulo retangulo",
    "losango": "losango", "diamante": "losango",
    "pentagono": "pentagono", "hexagono": "hexagono",
    "estrela": "estrela de cinco pontas",
    "estrela de quatro pontas": "estrela de quatro pontas",
    "estrela de cinco pontas": "estrela de cinco pontas",
    "estrela de seis pontas": "estrela de seis pontas",
    "coracao": "coracao", "raio": "raio",
    "seta": "seta para a direita",
    "seta para a direita": "seta para a direita",
    "seta para a esquerda": "seta para a esquerda",
    "seta para cima": "seta para cima", "seta para baixo": "seta para baixo",
    "balao": "balao em elipse", "balao em elipse": "balao em elipse",
    "balao retangular arredondado": "balao retangular arredondado",
    "nuvem": "balao em nuvem", "balao em nuvem": "balao em nuvem",
}
_FORMAS_DE_PONTOS = ("linha", "poligono", "traco", "curva", "livre", "mao livre")


# ─────────────────────────────────────────────────────── mouse e teclado
#
# POR QUE `SendInput` E NAO `mouse_event` (07/09/2026, custou 3 rodadas)
# ---------------------------------------------------------------------
# O par `SetCursorPos` + `mouse_event` e o jeito antigo, e no Paint do Windows
# 11 ele FALHA QUASE SEMPRE, sem erro nenhum: o mouse anda na tela, a foto
# mostra o ponteiro no lugar certo, e o Paint simplesmente nao desenha. De 18
# formas, sairam 2. E flagrante quando se olha: o teste desenhou UM triangulo
# de tres tentativas identicas.
#
# O Paint novo e WinUI 3 e le entrada por POINTER, nao pela fila antiga de
# mouse. `SendInput` com MOVE|ABSOLUTE entra pelo caminho que ele escuta — e ai
# desenha as 18 de 18. Nao mexer nisto de volta.
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_ulonglong
                 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong)]


class _ENTRADA(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("enchimento", ctypes.c_ubyte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("tipo", ctypes.c_ulong), ("u", _U)]


_MOVER_ABS = 0x0001 | 0x8000          # MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE


def _tela_virtual():
    """(esquerda, topo, largura, altura) de TODOS os monitores juntos."""
    u = ctypes.windll.user32
    larg = u.GetSystemMetrics(78) or u.GetSystemMetrics(0)
    alt = u.GetSystemMetrics(79) or u.GetSystemMetrics(1)
    return u.GetSystemMetrics(76), u.GetSystemMetrics(77), max(2, larg), max(2, alt)


def _enviar_mouse(x=None, y=None, bandeiras: int = 0) -> None:
    ev = _ENTRADA(tipo=0)
    if x is not None:
        esq, topo, larg, alt = _tela_virtual()
        ev.mi.dx = int((int(x) - esq) * 65535 / (larg - 1))
        ev.mi.dy = int((int(y) - topo) * 65535 / (alt - 1))
        bandeiras |= _MOVER_ABS
    ev.mi.dwFlags = bandeiras
    ctypes.windll.user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(_ENTRADA))


def _mover(x: int, y: int) -> None:
    _enviar_mouse(x, y)


def _clique(x: int = None, y: int = None, espera: float = 0.05) -> None:
    if x is not None:
        _mover(x, y)
        time.sleep(0.06)
    _enviar_mouse(bandeiras=_MOUSE_DOWN)
    time.sleep(0.05)
    _enviar_mouse(bandeiras=_MOUSE_UP)
    time.sleep(espera)


# O freio do ESC, enquanto um desenho esta em andamento. Fica aqui em cima
# porque o `_tecla` precisa dele: o PROPRIO JARVIS aperta ESC (fechar menu,
# soltar selecao) e o freio nao distingue tecla de gente de tecla de programa —
# sem este aviso ele se pararia sozinho. Mesma pedra do computer use.
_FREIO = None


def _tecla(vk: int) -> None:
    if vk == _VK_ESCAPE and _FREIO is not None:
        try:
            _FREIO.ignorar_por(0.9)
        except Exception:
            pass
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    time.sleep(0.05)


def _arrastar(x1, y1, x2, y2, passos: int = 10) -> None:
    """Arrasto com passos no meio.

    O Paint NAO desenha se o mouse pular do inicio ao fim: ele desenha entre as
    posicoes que recebe. Sem os passos do meio a forma sai do tamanho errado ou
    nem sai.
    """
    _mover(x1, y1)
    time.sleep(0.09)
    _enviar_mouse(bandeiras=_MOUSE_DOWN)
    time.sleep(0.08)
    for i in range(1, passos + 1):
        _mover(x1 + (x2 - x1) * i / passos, y1 + (y2 - y1) * i / passos)
        time.sleep(0.010)
    time.sleep(0.08)
    _enviar_mouse(bandeiras=_MOUSE_UP)
    time.sleep(0.11)


def _poligono(pontos) -> None:
    """A ferramenta POLIGONO do Paint: arrasta o 1o lado, clica os vertices e
    fecha com clique duplo no ponto de partida.

    POR QUE ELA, E NAO O BALDE DE TINTA (07/09/2026, defeito que o Diego viu)
    -------------------------------------------------------------------------
    Antes o poligono era feito a lapis e preenchido com o balde. O balde so
    pinta a mancha CONTINUA de cor igual embaixo do clique — e se ja houver
    desenho atras (uma montanha cruzando o telhado), a area de dentro esta
    cortada em pedacos e ele pinta so um pedaco. Na foto do Diego o telhado e
    as janelas ficaram VAZADOS, com a montanha aparecendo por dentro:
    *"ele nao esta pintando correto quando tem desenhos atras"*.

    O poligono nativo nao tem esse problema: ele cobre a area inteira, aconteca
    o que acontecer embaixo. Conferido contra um fundo listrado.
    """
    if len(pontos) < 3:
        _tracar_pontos(pontos)
        return
    _arrastar(*pontos[0], *pontos[1], passos=10)     # o primeiro lado
    for x, y in pontos[2:]:
        _clique(x, y, espera=0.16)
    # fecha voltando ao ponto de partida, com clique duplo
    _mover(*pontos[0])
    time.sleep(0.18)
    _clique(espera=0.10)
    _clique(espera=0.45)


def _menos_pontos(pontos, teto: int = 14):
    """Poligono com 40 vertices sao 40 cliques (14 s). Guarda os que importam.

    Tira sempre o vertice mais "reto" — aquele cuja retirada menos muda a
    silhueta. E o mesmo criterio do Douglas-Peucker, so que por contagem.
    """
    pts = [tuple(p) for p in pontos]
    while len(pts) > teto:
        pior, pior_custo = 1, None
        for i in range(1, len(pts) - 1):
            (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
            # area do triangulo: quanto o ponto do meio foge da reta
            custo = abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0))
            if pior_custo is None or custo < pior_custo:
                pior, pior_custo = i, custo
        pts.pop(pior)
    return pts


def _tracar_pontos(pontos, passo_px: float = 12.0) -> None:
    """Mao livre: aperta, passeia por todos os pontos, solta."""
    if len(pontos) < 2:
        return
    _mover(*pontos[0])
    time.sleep(0.12)
    _enviar_mouse(bandeiras=_MOUSE_DOWN)
    time.sleep(0.10)
    for (x1, y1), (x2, y2) in zip(pontos, pontos[1:]):
        dist = math.hypot(x2 - x1, y2 - y1)
        # 2 posicoes no meio ja bastam num trecho reto (o Paint liga o que
        # recebe com reta); a densidade so importa em curva, e ai o proprio
        # plano ja traz muitos pontos. Com passo de 6 px um contorno grande de
        # foto levava 4 s sozinho.
        n = max(2, int(dist / passo_px))
        for i in range(1, n + 1):
            _mover(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n)
            time.sleep(0.006)
    time.sleep(0.10)
    _enviar_mouse(bandeiras=_MOUSE_UP)
    time.sleep(0.15)


# ───────────────────────────────────────────────────────── o Paint pelo UIA
class PaintPorAcessibilidade:
    """Os botoes do Paint achados pelo NOME, nao pelo pixel."""

    def __init__(self, hwnd: int, log=None):
        import comtypes.client  # noqa: PLC0415
        self._log = log or (lambda _m: None)
        self._mod = comtypes.client.GetModule("UIAutomationCore.dll")
        self._uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=self._mod.IUIAutomation)
        self.hwnd = hwnd
        self._raiz = self._uia.ElementFromHandle(hwnd)
        self._cache: dict = {}
        # o que ja esta escolhido, para nao clicar duas vezes na mesma coisa
        self._ferramenta = ""
        self._cor1 = ""
        self._cor2 = ""
        self._modo_preenche = ""
        self._grossura_forma = 0
        self._grossura_lapis = -1
        self._forma_solta = False        # ha uma forma desenhada e nao fixada

    # -- procurar -------------------------------------------------------
    def _achar(self, nome=None, autoid=None, tipo=None, de=None):
        cond = []
        if nome is not None:
            cond.append(self._uia.CreatePropertyCondition(_UIA_Name, nome))
        if autoid is not None:
            cond.append(self._uia.CreatePropertyCondition(_UIA_AutoId, autoid))
        if tipo is not None:
            cond.append(self._uia.CreatePropertyCondition(_UIA_ControlType, tipo))
        if not cond:
            return None
        c = cond[0] if len(cond) == 1 else self._uia.CreateAndConditionFromArray(cond)
        return (de or self._raiz).FindFirst(_ESCOPO_DESCENDENTES, c)

    def _todos(self, tipo, de=None):
        c = self._uia.CreatePropertyCondition(_UIA_ControlType, tipo)
        achados = (de or self._raiz).FindAll(_ESCOPO_DESCENDENTES, c)
        return [achados.GetElement(i) for i in range(achados.Length)]

    @staticmethod
    def _centro(el):
        r = el.CurrentBoundingRectangle
        return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)

    def _clicar_elemento(self, el, espera: float = 0.12) -> bool:
        if _vazio(el):
            return False
        x, y = self._centro(el)
        if x <= 0 and y <= 0:
            return False
        _clique(x, y, espera)
        return True

    def _por_nome(self, nome: str, tipo=None):
        """Acha pelo nome exato; guarda para nao procurar de novo.

        NAO guarda quando nao acha: menu de flyout so existe enquanto esta
        aberto, e guardar o vazio faria a segunda tentativa falhar de graca.
        """
        chave = (nome, tipo)
        achado = self._cache.get(chave)
        if _vazio(achado):
            achado = self._achar(nome=nome, tipo=tipo)
            if not _vazio(achado):
                self._cache[chave] = achado
        return achado

    # -- pecas do Paint --------------------------------------------------
    def retangulo_da_tela(self):
        """A area branca onde se desenha, em pixel de tela."""
        el = self._achar(autoid="image")
        if _vazio(el):
            return None
        r = el.CurrentBoundingRectangle
        return (r.left, r.top, r.right, r.bottom)

    def area_visivel(self):
        """O espaco onde a folha cabe (a parte rolavel da janela)."""
        el = self._achar(autoid="scrollViewer")
        if _vazio(el):
            return None
        r = el.CurrentBoundingRectangle
        return (r.left, r.top, r.right, r.bottom)

    def usar_folha_maxima(self, tentativas: int = 5) -> bool:
        """Deixa a folha do tamanho da janela ANTES de desenhar.

        Pedido do Diego em 07/09/2026: *"deixa pra ele fazer sempre assim com a
        tela tamanho maximo para desenhar"*. E ele tem razao por dois motivos:
        o desenho fica grande na tela, e o Paint GUARDA o tamanho da ultima
        imagem — basta o dono ter aberto uma foto pequena um dia para todo
        desenho nascer do tamanho de um selo (num teste a folha ficou 1x295).

        So AUMENTA, nunca diminui: encolher cortaria a imagem do dono.
        Aumentar so acrescenta branco em volta, nao apaga nada.

        VARIAS voltas porque a folha e CENTRALIZADA: quando ela cresce, ela
        anda para a esquerda, e a alca que eu acabei de soltar ja nao esta onde
        a conta dizia — cada volta ganha so metade do que falta. Com duas
        voltas ela parava em 1639 de 1900 possiveis e o Diego reclamou, com
        razao. Cinco voltas fecham a conta, e o laco para sozinho quando o
        ganho fica pequeno.
        """
        mudou = False
        for _ in range(max(1, tentativas)):
            folha = self.retangulo_da_tela()
            espaco = self.area_visivel()
            if not folha or not espaco:
                return mudou
            fl, ft, fr, fb = folha
            el_, et, er, eb = espaco
            alvo_x = min(fl + (er - el_) - 44, er - 22)
            alvo_y = min(ft + (eb - et) - 44, eb - 22)
            if alvo_x <= fr + 12 and alvo_y <= fb + 12:
                return mudou            # ja esta do tamanho da janela
            self._log(f"[desenhar] folha {fr - fl}x{fb - ft}; esticando para caber na janela")
            # A ALCA FICA FORA DA FOLHA, NAO EM CIMA DELA (medido em 07/09/2026
            # pegando de 4 jeitos diferentes): em (fr,fb) e em (fr-3,fb-3) nao
            # acontece nada; em (fr+2,fb+2) e (fr+5,fb+5) ela estica. Por isso
            # os 4 px para fora. Foi isso que fez a folha empacar em 1639 e o
            # Diego dizer, com razao, que ela nao chegava ao maximo.
            _arrastar(fr + 4, fb + 4, max(alvo_x, fr), max(alvo_y, fb), passos=18)
            time.sleep(0.6)
            mudou = True
        return mudou

    def paleta(self) -> dict:
        """{nome sem acento: (elemento, (r,g,b))} — as 20 amostras da barra."""
        if "paleta" in self._cache:
            return self._cache["paleta"]
        grupo = self._achar(nome="Cores")
        itens = [] if _vazio(grupo) else self._todos(_TIPO_ITEM_LISTA, de=grupo)
        foto = None
        try:
            from PIL import ImageGrab  # noqa: PLC0415
            foto = ImageGrab.grab(all_screens=True)
        except Exception:
            pass
        saida = {}
        for el in itens:
            nome = _sem_acento(el.CurrentName)
            if not nome:
                continue
            rgb = _PALETA_CONHECIDA.get(nome)
            if foto is not None:
                try:
                    rgb = foto.getpixel(self._centro(el))[:3]
                except Exception:
                    pass
            if rgb:
                saida[nome] = (el, tuple(rgb))
        self._cache["paleta"] = saida
        return saida

    def _amostra_de(self, cor: str):
        """Do que o modelo pediu para a amostra que existe de verdade."""
        paleta = self.paleta()
        if not paleta:
            return None, ""
        pedido = _sem_acento(cor)
        if not pedido:
            return None, ""
        pedido = _APELIDO_DE_COR.get(pedido, pedido)
        if pedido in paleta:
            return paleta[pedido][0], pedido
        alvo = _hex_para_rgb(pedido)
        if alvo is None:
            # nao e nome conhecido nem codigo: tenta pelo pedaco do nome
            for nome in paleta:
                if pedido in nome or nome in pedido:
                    return paleta[nome][0], nome
            alvo = _PALETA_CONHECIDA.get(pedido)
        if alvo is None:
            return None, ""
        melhor = min(paleta.items(), key=lambda kv: _distancia_de_cor(alvo, kv[1][1]))
        return melhor[1][0], melhor[0]

    def cor(self, qual: int, nome_da_cor: str) -> str:
        """Poe a cor na Cor 1 ou na Cor 2.

        CUIDADO, ISTO E AO CONTRARIO DO QUE PARECE (medido em 07/09/2026):
        no Paint do Windows 11, para as FORMAS, a **Cor 1 e o PREENCHIMENTO** e
        a **Cor 2 e o CONTORNO** — o contrario do Paint antigo. Conferido com
        uma bola de Cor 1 vermelha e Cor 2 amarela: saiu vermelha por dentro e
        com a borda amarela. Para o Lapis e para o balde, Cor 1 e a cor que
        pinta. Quem chama deve usar `cor_de_dentro` e `cor_do_contorno`, que
        ja tratam disso pelo nome.
        """
        amostra, nome = self._amostra_de(nome_da_cor)
        if _vazio(amostra):
            return ""
        atual = self._cor1 if qual == 1 else self._cor2
        if atual == nome:
            return nome
        prefixo = "Cor 1" if qual == 1 else "Cor 2"
        alvo = None
        for el in self._todos(_TIPO_RADIO):
            if (el.CurrentName or "").startswith(prefixo):
                alvo = el
                break
        if not _vazio(alvo):
            self._clicar_elemento(alvo, 0.07)
        self._clicar_elemento(amostra, 0.07)
        if qual == 1:
            self._cor1 = nome
        else:
            self._cor2 = nome
        return nome

    def cor_de_dentro(self, nome_da_cor: str) -> str:
        """A cor que enche a forma. No Paint do Win11 e a Cor 1."""
        return self.cor(1, nome_da_cor)

    def cor_do_contorno(self, nome_da_cor: str) -> str:
        """A cor da borda da forma. No Paint do Win11 e a Cor 2."""
        return self.cor(2, nome_da_cor)

    def limpar_folha(self) -> None:
        """Apaga tudo e deixa a folha branca. So para REFAZER o desenho.

        Ctrl+A seleciona a folha inteira e Delete apaga para branco. E o que o
        dono espera quando diz "esse desenho ficou ruim, faz de novo": desenhar
        por cima do que ja estava daria um borrao com duas casas.
        """
        self.carimbar()
        ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)      # Ctrl
        _tecla(0x41)                                          # A
        ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
        time.sleep(0.35)
        _tecla(0x2E)                                          # Delete
        time.sleep(0.5)
        _tecla(_VK_ESCAPE)                                    # solta a selecao
        time.sleep(0.3)
        # a selecao apagada zera o que o Paint tinha escolhido
        self._ferramenta = ""

    def carimbar(self) -> None:
        """Fixa na imagem a forma que ainda esta 'pousada'.

        O ENTER NAO CARIMBA (medido em 07/09/2026, com foto de cada passo).
        Depois do Enter a forma continua editavel: mudei a Cor 1 e ela mudou de
        cor junto. Foi isso que estragou o primeiro desenho — cada forma saia
        com a cor da forma SEGUINTE, porque eu trocava as cores antes de ela ter
        sido fixada.
        O que fixa e TROCAR DE FERRAMENTA. O Lapis serve de martelo: e barato,
        nao desenha nada por ser escolhido, e deixa tudo pronto para a proxima.
        """
        if not self._forma_solta:
            return
        self._forma_solta = False
        self.ferramenta("Lápis")

    def ferramenta(self, nome_uia: str) -> bool:
        """Escolhe a ferramenta/forma pelo nome que o Paint mostra."""
        if self._ferramenta == nome_uia:
            return True
        el = self._por_nome(nome_uia)
        if _vazio(el):
            # o nome pode ter acento diferente do que eu escrevi: varre a lista
            for cand in self._todos(_TIPO_ITEM_LISTA) + self._todos(_TIPO_BOTAO):
                if _sem_acento(cand.CurrentName) == _sem_acento(nome_uia):
                    el = cand
                    break
        if _vazio(el):
            self._log(f"[desenhar] nao achei a ferramenta {nome_uia!r}")
            return False
        if not self._clicar_elemento(el, 0.12):
            return False
        self._ferramenta = nome_uia
        return True

    def grossura_do_lapis(self, px: int) -> None:
        px = max(1, min(90, int(px)))
        if self._grossura_lapis == px:
            return
        for el in self._todos(_TIPO_SLIDER):
            if (el.CurrentName or "") != "Tamanho":
                continue
            try:
                faixa = el.GetCurrentPattern(_PADRAO_FAIXA).QueryInterface(
                    self._mod.IUIAutomationRangeValuePattern)
                faixa.SetValue(float(px))
                self._grossura_lapis = px
            except Exception as exc:
                self._log(f"[desenhar] cursor de tamanho nao aceitou: {str(exc)[:60]}")
            return

    def grossura_da_forma(self, px: int) -> None:
        """O menu Tamanho da barra de Formas: 3px, 5px, 8px."""
        px = 8 if px >= 7 else (5 if px >= 4 else 3)
        if self._grossura_forma == px:
            return
        botao = None
        for el in self._todos(_TIPO_BOTAO):
            if (el.CurrentName or "") == "Tamanho":
                r = el.CurrentBoundingRectangle
                if r.top < 200:                    # o da barra, nao o cursor lateral
                    botao = el
                    break
        if _vazio(botao) or not self._clicar_elemento(botao, 0.38):
            return
        alvo = self._achar(nome=f"{px}px", tipo=_TIPO_MENU)
        if _vazio(alvo):
            _tecla(_VK_ESCAPE)
            return
        self._clicar_elemento(alvo, 0.16)
        self._grossura_forma = px

    def preenchimento(self, ligado: bool) -> None:
        """Liga/desliga o preenchimento solido das formas (usa a Cor 2)."""
        desejado = "Preenchimento sólido" if ligado else "Sem preenchimento"
        if self._modo_preenche == desejado:
            return
        botao = self._por_nome("Preenchimento de forma", _TIPO_BOTAO)
        if _vazio(botao) or not self._clicar_elemento(botao, 0.38):
            return
        alvo = self._achar(nome=desejado, tipo=_TIPO_MENU)
        if _vazio(alvo):
            _tecla(_VK_ESCAPE)
            return
        self._clicar_elemento(alvo, 0.16)
        self._modo_preenche = desejado


    def desfazer_tudo(self, vezes: int = 40) -> None:
        for _ in range(vezes):
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)      # Ctrl
            _tecla(0x5A)                                         # Z
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)


# ────────────────────────────────────────────────────────────── a janela
_NOMES_DE_EDITOR = {
    "paint": ("paint",), "ms paint": ("paint",), "mspaint": ("paint",),
    "photoshop": ("photoshop",), "ps": ("photoshop",),
    "gimp": ("gimp",), "krita": ("krita",), "paint.net": ("paint.net",),
    "paint 3d": ("paint 3d",), "figma": ("figma",),
}


def _janelas_visiveis():
    import win32gui  # noqa: PLC0415
    achadas = []

    def _cb(h, _):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            if t:
                achadas.append((h, t, win32gui.GetClassName(h)))
        return True
    win32gui.EnumWindows(_cb, None)
    return achadas


def _achar_editor(termos):
    for h, t, _cls in _janelas_visiveis():
        baixo = t.lower()
        if any(x in baixo for x in termos):
            return h, t
    return 0, ""


def _focar(hwnd: int) -> bool:
    import win32con  # noqa: PLC0415
    import win32gui  # noqa: PLC0415
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.4)
        for _ in range(3):
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            time.sleep(0.25)
        # SO se o Windows recusar: o Alt solto libera o foco — e abre o menu do
        # programa junto, por isso o Esc logo atras (a mesma pedra do
        # `digitar_na_tela`, 01/09/2026).
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.25)
        _tecla(_VK_ESCAPE)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _abrir_paint(log):
    hwnd, _t = _achar_editor(("paint",))
    nasceu_agora = False
    if not hwnd:
        try:
            subprocess.Popen(["mspaint.exe"])                     # noqa: S607
        except Exception as exc:
            return 0, False, f"Nao consegui abrir o Paint: {str(exc)[:60]}"
        for _ in range(80):
            time.sleep(0.25)
            hwnd, _t = _achar_editor(("paint",))
            if hwnd:
                break
        if not hwnd:
            return 0, False, "Abri o Paint mas a janela nao apareceu."
        nasceu_agora = True
        time.sleep(2.0)                 # janela recem-nascida engole clique
    return hwnd, nasceu_agora, ""


def _maximizar(hwnd: int, sempre: bool = False) -> None:
    """Janela maior = folha maior = desenho maior.

    Para o PAINT vale SEMPRE (`sempre=True`): o Diego pediu "a tela no tamanho
    maximo para desenhar", e a folha so pode crescer ate onde a janela deixa.
    Com o Paint numa janela de 1536x864 a folha parou em 1500x617; maximizado
    ela vai a 1840x747. Em editor dos OUTROS (Photoshop, GIMP) so mexo se
    estiver mesmo apertado — nao vou reorganizar a mesa de trabalho dele.
    """
    import win32con  # noqa: PLC0415
    import win32gui  # noqa: PLC0415
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        if sempre or (r - l) < 1000 or (b - t) < 700:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            time.sleep(0.8)
    except Exception:
        pass


# ───────────────────────────────────────────────────── o plano do desenho
_CORES_PARA_O_MODELO = ("preto, cinza, cinza claro, branco, vermelho escuro, "
                        "vermelho, laranja, amarelo, amarelo claro, dourado, "
                        "verde, lima, turquesa, turqueza claro, indigo, roxo, "
                        "marrom, rosa, cinza azulado, lavanda")

# POR QUE A RECEITA VAI NA PERGUNTA, E NAO NA INSTRUCAO (07/09/2026):
# o `cerebro_codex.escrever` CORTA a instrucao em 800 caracteres antes de
# mandar para a VPS (`ctx["instrucao"] = ...[:800]`). Esta receita tem mais de
# 2.500 — pela instrucao ela chegaria pela metade, sem a lista de formas nem as
# regras, e o modelo devolveria qualquer coisa. Na pergunta chega inteira nos
# tres cerebros (nossa OpenAI, Gemini do cliente e Codex).
_INSTRUCAO_CURTA = ("Responda SOMENTE com um objeto JSON valido, comecando com "
                    "{ e terminando com }. Sem cerca de codigo, sem titulo, sem "
                    "explicacao antes ou depois.")

_RECEITA = (
    "Voce desenha por meio de um robo que opera o Microsoft Paint. Responda "
    "SOMENTE com um objeto JSON valido: sem cerca de codigo, sem titulo, sem "
    "explicacao antes ou depois.\n"
    "\n"
    "FORMATO\n"
    '{"nome":"<o que e>","formas":[ ... ]}\n'
    "\n"
    "Cada forma e um objeto. Quem tem tamanho usa x, y, l, a:\n"
    '  {"forma":"retangulo","x":0.05,"y":0.60,"l":0.90,"a":0.38,'
    '"cor":"verde","preenche":"lima","grossura":5}\n'
    "  x,y = canto de cima e da esquerda, de 0 a 1 (0 = borda esquerda/topo)\n"
    "  l,a = largura e altura, de 0 a 1\n"
    "  cor = cor do contorno.  preenche = cor de dentro (omita para vazio).\n"
    "  grossura = 3, 5 ou 8. Padrao 5.\n"
    "\n"
    "FORMAS COM TAMANHO: retangulo, retangulo arredondado, circulo, elipse, "
    "triangulo, triangulo retangulo, losango, pentagono, hexagono, estrela, "
    "estrela de seis pontas, coracao, raio, seta para a direita, seta para a "
    "esquerda, seta para cima, seta para baixo, balao, nuvem.\n"
    "\n"
    "FORMAS COM PONTOS (usam pontos em vez de x,y,l,a):\n"
    '  {"forma":"poligono","pontos":[[0.2,0.55],[0.5,0.30],[0.8,0.55]],'
    '"cor":"vermelho escuro","preenche":"vermelho","grossura":5}\n'
    '  {"forma":"linha","pontos":[[0.1,0.9],[0.9,0.9]],"cor":"preto","grossura":5}\n'
    '  {"forma":"traco","pontos":[[0.3,0.2],[0.35,0.15],[0.42,0.18]],'
    '"cor":"cinza","grossura":3}\n'
    "  poligono fecha sozinho e pode ser preenchido (bom para telhado, monte, "
    "vela de barco). linha liga dois pontos. traco e mao livre, nao fecha e "
    "nao preenche (bom para fumaca, cabelo, onda, galho, detalhe).\n"
    "\n"
    "CORES — use EXATAMENTE um destes nomes: " + _CORES_PARA_O_MODELO + ".\n"
    "\n"
    "O QUE FAZ O DESENHO FICAR BOM (e o que estava faltando):\n"
    "1. De 18 a 34 formas. Quatro tracos nao e desenho, e rabisco.\n"
    "2. Ordem do FUNDO para a FRENTE: ceu e chao primeiro, depois os objetos "
    "grandes, por ultimo os detalhes pequenos. O que vem depois cobre o que "
    "veio antes — use isso a seu favor.\n"
    "3. PREENCHA TUDO QUE E SOLIDO. Forma sem `preenche` fica OCA: aparece por "
    "dentro dela o que estava desenhado atras, e o desenho fica furado — "
    "parede com montanha aparecendo dentro, janela transparente, telhado "
    "vazado. Parede, telhado, porta, janela, vidro, roda, tronco, copa, ceu, "
    "chao, agua: TODOS com `preenche`. Deixe sem `preenche` so linha e detalhe "
    "fino (fio, antena, raio de sol, risco do chao).\n"
    "4. Use a folha inteira: de 0.02 a 0.98. Nada espremido num canto.\n"
    "5. Detalhe e o que separa desenho de rabisco: janela com cruz, porta com "
    "macaneta, telha, raio de sol, folhas na arvore, sombra no chao.\n"
    "6. Nada de texto escrito. Nenhum numero fora de 0 a 1.\n"
    "7. CADA COISA E MONTADA COM VARIAS PECAS, nunca com uma so. Um carro nao e "
    "um retangulo: e a carroceria, o teto mais estreito em cima, dois vidros, "
    "duas rodas pretas com o miolo cinza, o farol e o para-choque. Uma arvore "
    "nao e uma bola: e o tronco, duas ou tres copas de tamanhos diferentes e "
    "uns galhos. Uma casa nao e um quadrado com um triangulo: e a parede, o "
    "telhado passando das bordas, a porta com macaneta, duas janelas com "
    "caixilho em cruz, o degrau e a chamine. Quem desenha bem gasta 5 a 8 "
    "pecas no assunto principal.\n"
    "8. PROPORCAO DE GENTE GRANDE: o telhado e mais largo que a parede; a "
    "roda encosta no chao; a cabeca nao e maior que o corpo; nada flutuando "
    "no ar sem apoio. Antes de responder, releia a sua lista e confira, peca "
    "por peca, se as coordenadas encaixam de verdade — e o passo que separa um "
    "desenho de um monte de formas soltas."
)

# NUNCA DUAS VEZES O MESMO DESENHO (pedido do Diego, 07/09/2026):
#   "sempre faz diferente ne para nao ser sempre a mesma coisa, por exemplo
#    'faca uma casa' ele sempre vai fazer dessa forma; sempre muda alguma coisa"
#
# Modelo de linguagem repete: pedir "uma casa" duas vezes da a MESMA casa, com
# o mesmo telhado vermelho no mesmo lugar. Pedir "seja criativo" nao resolve —
# ele e criativo do mesmo jeito toda vez. O que resolve e MUDAR O PEDIDO: um
# empurrao sorteado entra no fim da encomenda e leva o desenho para outro lado.
# E o mesmo remedio do teto de fichas: instrucao e pedido, restricao e que
# manda.
_EMPURROES = (
    "Mude a HORA: faca de manha cedo, ou ao entardecer, ou de noite com lua e "
    "estrelas — e deixe as cores combinarem com a hora escolhida.",
    "Mude o ANGULO e a COMPOSICAO: nao ponha o assunto no meio; jogue para um "
    "lado e deixe o outro lado com o cenario.",
    "Acrescente DOIS elementos que ninguem pediu, mas que combinam com a cena, "
    "e de a eles o mesmo capricho do assunto principal.",
    "Mude o CLIMA: chuva, neve, vento, muita nuvem ou sol a pino — e mostre "
    "isso no desenho, nao so nas cores.",
    "Fuja da cor OBVIA: escolha uma paleta diferente da primeira que vier a "
    "cabeca, sem ficar feio.",
    "Faca em PLANO FECHADO: o assunto ocupando quase a folha inteira, com "
    "muito detalhe, e pouco cenario.",
    "Faca em PLANO ABERTO: o assunto menor, com bastante cenario em volta e "
    "coisas acontecendo ao fundo.",
    "Ponha VIDA na cena: um bicho, um passaro, uma pessoa ao longe, algo em "
    "movimento.",
    "Mude a ESTACAO do ano e deixe isso claro no desenho.",
    "Escolha um PONTO DE VISTA diferente: de baixo para cima, de cima para "
    "baixo, ou de bem perto.",
)


def _empurrao_de_variacao() -> str:
    """Um empurrao sorteado, para o mesmo pedido nunca dar o mesmo desenho."""
    import random  # noqa: PLC0415
    return ("\n\nESTE DESENHO TEM DE SER DIFERENTE DE QUALQUER OUTRO QUE VOCE "
            "JA TENHA FEITO DESTE MESMO ASSUNTO. " + random.choice(_EMPURROES))


_CONSERTAR = (
    "Este e o plano de desenho que VOCE acabou de fazer para o pedido acima.\n"
    "Agora seja o critico dele, e seja duro. Confira, nesta ordem, o que mais "
    "estraga um desenho:\n"
    "  a) PECA TAPANDO PECA: nada pode cobrir a porta, a janela, o rosto, a "
    "roda ou o assunto principal. Se duas se sobrepoem sem querer, mude a "
    "posicao ou o tamanho de uma delas.\n"
    "  b) CORTADO NA BORDA: nada de meio carro, meia arvore ou meia casa "
    "saindo da folha. Tudo entre 0.02 e 0.98.\n"
    "  c) FLUTUANDO: casa, arvore, carro e pessoa tem de encostar no chao. A "
    "roda encosta na rua, o tronco encosta na grama.\n"
    "  d) CONTORNO SOLTO: forma sem `preenche` por cima de coisa colorida "
    "vira um risco solto no meio do desenho. Ou preencha ou tire.\n"
    "  e) MESMO MATERIAL, MESMA COR: as duas janelas da mesma casa tem a "
    "mesma cor; as duas rodas do mesmo carro tambem.\n"
    "  f) POUCA PECA no assunto principal, proporcao errada, ou forma "
    "sobrando que nao ajuda em nada.\n"
    "Devolva o plano CONSERTADO e mais rico, no MESMO formato JSON — so o "
    "objeto JSON, nada mais. Mantenha o que ja estava bom, conserte o que "
    "estava errado e ACRESCENTE os detalhes que faltam. Nunca devolva menos "
    "formas do que recebeu.\n\nPLANO ATUAL:\n")


def _melhorar_o_plano(plano: dict, pedido: str, gerar, log) -> dict:
    """UMA segunda passada: o modelo critica e conserta o proprio desenho.

    Custa ~10 s num desenho que leva 70 s para sair, e e a mudanca que mais
    pesa na qualidade — muito mais do que pedir "capriche" na primeira volta.
    O motivo e simples: na primeira vez ele esta INVENTANDO, e na segunda ele
    esta OLHANDO. Se a segunda volta falhar ou vier mais pobre, fica a
    primeira; nunca piora.
    """
    # RODA SEMPRE (07/09/2026, decidido duas vezes). Eu tinha pulado a critica
    # quando o plano ja vinha com 30 pecas, para ganhar 15 s. Foi troca ruim: a
    # casa com montanhas veio com 34 pecas, pulou a critica, e saiu com o
    # degrau em cima da porta, um contorno solto na arvore e o carro cortado na
    # borda — exatamente o que a critica pega. QUANTIDADE DE PECAS NAO DIZ NADA
    # SOBRE COLOCACAO. O tempo o dono ja nao sente: ele ouve o aviso completo na
    # hora em que pede, e a faixa fica na tela o tempo todo.
    if gerar is None or not plano.get("formas"):
        return plano
    try:
        # PLANO COMPACTO: sem espacos e com as coordenadas em 3 casas. Um
        # plano de 80 formas encolhe quase pela metade, e o que entra de fichas
        # na pergunta sobra para a resposta.
        enxuto = {"nome": plano.get("nome", ""), "formas": [
            {k: (round(v, 3) if isinstance(v, float) else
                 [[round(x, 3), round(y, 3)] for x, y in v] if k == "pontos" else v)
             for k, v in f.items() if v not in ("", None)}
            for f in plano["formas"]]}
        bruto = _so_json(gerar(
            f"{_RECEITA}\n\nPEDIDO DO DONO: {pedido}\n\n{_CONSERTAR}"
            + json.dumps(enxuto, ensure_ascii=False, separators=(",", ":")),
            _INSTRUCAO_CURTA))
    except Exception as exc:
        log(f"[desenhar] a segunda passada falhou: {str(exc)[:80]}")
        return plano
    melhor = _limpar_plano(bruto)
    if len(melhor["formas"]) < len(plano["formas"]):
        log(f"[desenhar] a segunda passada veio mais pobre "
            f"({len(melhor['formas'])} contra {len(plano['formas'])}); fiquei com a primeira")
        return plano
    log(f"[desenhar] segunda passada: {len(plano['formas'])} -> {len(melhor['formas'])} pecas")
    if len(melhor["formas"]) > TETO_DE_PECAS:
        # Nao adianta o critico pedir 90 pecas: eu so desenho 70. Melhor cortar
        # AQUI, onde as ultimas ainda sao detalhe fino, do que no meio do laco.
        melhor["formas"] = melhor["formas"][:TETO_DE_PECAS]
    melhor["nome"] = melhor.get("nome") or plano.get("nome") or ""
    return melhor


def _so_json(texto: str) -> dict:
    """Tira cerca de codigo e conversa; devolve o objeto ou {}."""
    s = str(texto or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        dados = json.loads(s[i:j + 1])
    except Exception:
        # vírgula sobrando antes de } ou ] derruba o json e e o erro mais comum
        try:
            dados = json.loads(re.sub(r",\s*([}\]])", r"\1", s[i:j + 1]))
        except Exception:
            return {}
    return dados if isinstance(dados, dict) else {}


def _numero(v, padrao=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao


def _limpar_plano(bruto: dict) -> dict:
    """Deixa so o que da para desenhar, e conserta o que da para consertar."""
    formas = []
    for f in (bruto.get("formas") or bruto.get("shapes") or []):
        if not isinstance(f, dict):
            continue
        nome = _sem_acento(f.get("forma") or f.get("tipo") or f.get("shape") or "")
        if not nome:
            continue
        cor = str(f.get("cor") or f.get("contorno") or "preto")
        preenche = f.get("preenche") or f.get("preenchimento") or f.get("fill") or ""
        grossura = int(_numero(f.get("grossura") or f.get("espessura"), 5))
        pontos = f.get("pontos") or f.get("points") or []
        limpos = []
        for p in pontos if isinstance(pontos, (list, tuple)) else []:
            if isinstance(p, dict):
                p = [p.get("x"), p.get("y")]
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                x, y = _numero(p[0], -1), _numero(p[1], -1)
                if 0 <= x <= 1 and 0 <= y <= 1:
                    limpos.append((x, y))
        if nome in _FORMAS_DE_PONTOS or (limpos and nome not in _APELIDO_DE_FORMA):
            if len(limpos) < 2:
                continue
            formas.append({"forma": "traco" if nome in ("traco", "curva", "livre",
                                                        "mao livre") else nome,
                           "pontos": limpos, "cor": cor,
                           "preenche": str(preenche), "grossura": grossura})
            continue
        alvo = _APELIDO_DE_FORMA.get(nome)
        if alvo is None:
            for apelido, real in _APELIDO_DE_FORMA.items():
                if apelido in nome or nome in apelido:
                    alvo = real
                    break
        if alvo is None:
            continue
        x, y = _numero(f.get("x"), -1), _numero(f.get("y"), -1)
        l = _numero(f.get("l") or f.get("largura") or f.get("w"), 0)
        a = _numero(f.get("a") or f.get("altura") or f.get("h"), 0)
        if x < 0 or y < 0 or l <= 0 or a <= 0:
            continue
        x, y = min(max(x, 0.0), 0.99), min(max(y, 0.0), 0.99)
        l, a = min(l, 1.0 - x), min(a, 1.0 - y)
        if l < 0.01 or a < 0.01:
            continue
        formas.append({"forma": alvo, "x": x, "y": y, "l": l, "a": a, "cor": cor,
                       "preenche": str(preenche), "grossura": grossura})
    return {"nome": str(bruto.get("nome") or bruto.get("titulo") or "").strip(),
            "formas": formas}


def _centro_do_poligono(pontos):
    """Um ponto DENTRO do poligono, para o balde de tinta acertar.

    O centro de massa serve para forma convexa; para forma em L ele cai fora e
    a tinta vazaria pela tela toda. Entao ele so vale se estiver dentro — e a
    conferencia e o teste do raio (contar quantas arestas ficam a esquerda).
    """
    n = len(pontos)
    if n < 3:
        return None
    area = cx = cy = 0.0
    for i in range(n):
        x1, y1 = pontos[i]
        x2, y2 = pontos[(i + 1) % n]
        cruz = x1 * y2 - x2 * y1
        area += cruz
        cx += (x1 + x2) * cruz
        cy += (y1 + y2) * cruz
    if abs(area) < 1e-9:
        return None
    cx, cy = cx / (3 * area), cy / (3 * area)
    if _dentro(pontos, cx, cy):
        return (cx, cy)
    # nao deu: procura um ponto de dentro varrendo o meio da figura
    xs = [p[0] for p in pontos]
    ys = [p[1] for p in pontos]
    for k in range(1, 8):
        yy = min(ys) + (max(ys) - min(ys)) * k / 8.0
        for j in range(1, 16):
            xx = min(xs) + (max(xs) - min(xs)) * j / 16.0
            if _dentro(pontos, xx, yy):
                return (xx, yy)
    return None


def _dentro(pontos, x, y) -> bool:
    dentro = False
    n = len(pontos)
    for i in range(n):
        x1, y1 = pontos[i]
        x2, y2 = pontos[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            corte = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < corte:
                dentro = not dentro
    return dentro


# ──────────────────────────────────────────────────── desenhar no Paint
def _desenhar_no_paint(plano: dict, hwnd: int, log, deve_parar,
                       esticar_folha: bool = True, limpar: bool = False,
                       ao_andar=None) -> tuple[int, str]:
    """Executa o plano. Devolve (quantas formas sairam, aviso)."""
    paint = PaintPorAcessibilidade(hwnd, log=log)
    if esticar_folha:
        paint.usar_folha_maxima()
    if limpar:
        paint.limpar_folha()
    tela = paint.retangulo_da_tela()
    if not tela:
        return 0, "nao achei a area de desenho do Paint"
    esq, topo, dir_, base = tela
    # RESPIRO NA BORDA — NAO E FRESCURA (07/09/2026). A folha do Paint tem oito
    # ALCAS de redimensionar, uma em cada canto e uma no meio de cada lado, e
    # elas ficam EM CIMA da borda. Um arrasto que comeca a 6 px do canto pega a
    # alca: o teste encolheu a folha de 976x589 para 1x295 e o desenho inteiro
    # sumiu. Com 14 px de folga a alca fica para tras.
    marg = 10
    esq, topo = esq + marg, topo + marg
    dir_, base = dir_ - marg, base - marg
    larg, alt = max(1, dir_ - esq), max(1, base - topo)
    log(f"[desenhar] folha {larg}x{alt} em ({esq},{topo})")

    def px(fx, fy):
        return (int(esq + fx * larg), int(topo + fy * alt))

    feitas = 0
    fim = time.monotonic() + TETO_DE_TEMPO_S
    for forma in plano["formas"]:
        if deve_parar and deve_parar():
            paint.carimbar()
            return feitas, "parado pelo dono"
        if time.monotonic() > fim:
            paint.carimbar()
            log(f"[desenhar] estourei o tempo com {feitas} de {len(plano['formas'])}")
            return feitas, "faltou tempo"
        nome = forma["forma"]
        grossura = forma.get("grossura") or 5
        try:
            # A ORDEM DESTE BLOCO E O DESENHO INTEIRO (07/09/2026):
            # 1o CARIMBAR a forma anterior, 2o escolher a ferramenta,
            # 3o so entao as cores. Trocar cor com uma forma ainda solta
            # RECOLORE a forma solta — foi o que fez cada peca sair com a cor
            # da peca seguinte no primeiro desenho.
            paint.carimbar()
            if nome == "poligono" and len(forma["pontos"]) >= 3:
                # FERRAMENTA POLIGONO, nao lapis + balde: ver `_poligono`.
                nome_uia = _nome_de_botao_da_forma(paint, "poligono")
                if not nome_uia or not paint.ferramenta(nome_uia):
                    continue
                tem_dentro = bool(forma.get("preenche"))
                paint.cor_de_dentro(forma.get("preenche") or forma["cor"])
                paint.cor_do_contorno(forma["cor"])
                paint.preenchimento(tem_dentro)
                paint.grossura_da_forma(grossura)
                _poligono([px(x, y) for x, y in _menos_pontos(forma["pontos"])])
                paint._forma_solta = True
            elif nome in ("traco", "linha", "poligono"):
                if not paint.ferramenta("Lápis"):
                    continue
                paint.cor(1, forma["cor"])          # o Lapis pinta com a Cor 1
                paint.grossura_do_lapis(max(2, min(20, grossura)))
                _tracar_pontos([px(x, y) for x, y in forma["pontos"]])
            else:
                # A FORMA VEM PRIMEIRO, E NAO POR CAPRICHO: "Preenchimento de
                # forma" e "Tamanho" so ficam ativos DEPOIS que uma forma esta
                # escolhida. Com a ordem invertida, o clique nao abria o menu, a
                # busca pelo item voltava vazia e as 13 formas do teste foram
                # puladas de uma vez.
                nome_uia = _nome_de_botao_da_forma(paint, nome)
                if not nome_uia or not paint.ferramenta(nome_uia):
                    continue
                tem_dentro = bool(forma.get("preenche"))
                paint.cor_de_dentro(forma.get("preenche") or forma["cor"])
                paint.cor_do_contorno(forma["cor"])
                paint.preenchimento(tem_dentro)
                paint.grossura_da_forma(grossura)
                x1, y1 = px(forma["x"], forma["y"])
                x2, y2 = px(forma["x"] + forma["l"], forma["y"] + forma["a"])
                _arrastar(x1, y1, x2, y2)
                paint._forma_solta = True
            feitas += 1
            if ao_andar:
                ao_andar(feitas, len(plano["formas"]))
        except Exception as exc:
            log(f"[desenhar] pulei {nome}: {type(exc).__name__} {str(exc)[:70]}")
    paint.carimbar()          # a ULTIMA forma tambem precisa ser fixada
    return feitas, ""


def _nome_de_botao_da_forma(paint, nome_sem_acento: str) -> str:
    """'coracao' -> 'Coração' (o nome com acento que o Paint mostra)."""
    chave = f"botao:{nome_sem_acento}"
    if chave in paint._cache:
        return paint._cache[chave]
    achado = ""
    for el in paint._todos(_TIPO_ITEM_LISTA) + paint._todos(_TIPO_BOTAO):
        if _sem_acento(el.CurrentName) == nome_sem_acento:
            achado = el.CurrentName
            break
    paint._cache[chave] = achado
    return achado


# ─────────────────────────── qualquer outro editor: montar e colar
def _cor_rgb(nome: str) -> tuple:
    n = _sem_acento(nome)
    n = _APELIDO_DE_COR.get(n, n)
    if n in _PALETA_CONHECIDA:
        return _PALETA_CONHECIDA[n]
    achado = _hex_para_rgb(n)
    if achado:
        return achado
    melhor = min(_PALETA_CONHECIDA.items(),
                 key=lambda kv: _distancia_de_cor((0, 0, 0), kv[1]))
    return melhor[1]


def _montar_imagem(plano: dict, larg: int, alt: int):
    """O mesmo plano, desenhado com o Pillow. Serve para colar em qualquer editor."""
    from PIL import Image, ImageDraw  # noqa: PLC0415
    img = Image.new("RGB", (larg, alt), (255, 255, 255))
    d = ImageDraw.Draw(img)

    def cx(v):
        return int(v * larg)

    def cy(v):
        return int(v * alt)

    for f in plano["formas"]:
        cor = _cor_rgb(f["cor"])
        dentro = _cor_rgb(f["preenche"]) if f.get("preenche") else None
        larg_linha = max(1, int(f.get("grossura") or 5))
        nome = f["forma"]
        if nome in ("traco", "linha", "poligono"):
            pts = [(cx(x), cy(y)) for x, y in f["pontos"]]
            if nome == "poligono":
                d.polygon(pts, fill=dentro, outline=cor, width=larg_linha)
            else:
                d.line(pts, fill=cor, width=larg_linha, joint="curve")
            continue
        cxy = [cx(f["x"]), cy(f["y"]), cx(f["x"] + f["l"]), cy(f["y"] + f["a"])]
        if nome == "oval":
            d.ellipse(cxy, fill=dentro, outline=cor, width=larg_linha)
        elif nome == "retangulo":
            d.rectangle(cxy, fill=dentro, outline=cor, width=larg_linha)
        elif nome == "retangulo arredondado":
            d.rounded_rectangle(cxy, radius=max(4, (cxy[2] - cxy[0]) // 10),
                                fill=dentro, outline=cor, width=larg_linha)
        else:
            pts = _pontos_da_forma(nome, cxy)
            if pts:
                d.polygon(pts, fill=dentro, outline=cor, width=larg_linha)
            else:
                d.rectangle(cxy, fill=dentro, outline=cor, width=larg_linha)
    return img


def _pontos_da_forma(nome: str, caixa) -> list:
    """Triangulo, losango, estrela... como poligono dentro da caixa."""
    x1, y1, x2, y2 = caixa
    w, h = x2 - x1, y2 - y1
    cx, cy = x1 + w / 2, y1 + h / 2
    if nome == "triangulo":
        return [(cx, y1), (x2, y2), (x1, y2)]
    if nome == "triangulo retangulo":
        return [(x1, y1), (x2, y2), (x1, y2)]
    if nome == "losango":
        return [(cx, y1), (x2, cy), (cx, y2), (x1, cy)]
    if nome in ("pentagono", "hexagono"):
        n = 5 if nome == "pentagono" else 6
        return [(cx + w / 2 * math.sin(2 * math.pi * i / n),
                 cy - h / 2 * math.cos(2 * math.pi * i / n)) for i in range(n)]
    if nome.startswith("estrela"):
        pontas = 4 if "quatro" in nome else (6 if "seis" in nome else 5)
        saida = []
        for i in range(pontas * 2):
            raio = 1.0 if i % 2 == 0 else 0.42
            ang = math.pi * i / pontas - math.pi / 2
            saida.append((cx + w / 2 * raio * math.cos(ang),
                          cy + h / 2 * raio * math.sin(ang)))
        return saida
    if nome == "coracao":
        saida = []
        for i in range(60):
            t = 2 * math.pi * i / 60
            sx = 16 * math.sin(t) ** 3
            sy = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            saida.append((cx + w / 2 * sx / 17.0, cy - h / 2 * sy / 17.0))
        return saida
    if nome.startswith("seta"):
        if "direita" in nome:
            return [(x1, cy - h / 4), (x1 + w * 0.6, cy - h / 4), (x1 + w * 0.6, y1),
                    (x2, cy), (x1 + w * 0.6, y2), (x1 + w * 0.6, cy + h / 4),
                    (x1, cy + h / 4)]
        if "esquerda" in nome:
            return [(x2, cy - h / 4), (x1 + w * 0.4, cy - h / 4), (x1 + w * 0.4, y1),
                    (x1, cy), (x1 + w * 0.4, y2), (x1 + w * 0.4, cy + h / 4),
                    (x2, cy + h / 4)]
        if "cima" in nome:
            return [(cx - w / 4, y2), (cx - w / 4, y1 + h * 0.4), (x1, y1 + h * 0.4),
                    (cx, y1), (x2, y1 + h * 0.4), (cx + w / 4, y1 + h * 0.4),
                    (cx + w / 4, y2)]
        return [(cx - w / 4, y1), (cx - w / 4, y1 + h * 0.6), (x1, y1 + h * 0.6),
                (cx, y2), (x2, y1 + h * 0.6), (cx + w / 4, y1 + h * 0.6),
                (cx + w / 4, y1)]
    return []


def _copiar_imagem(img) -> bool:
    """Poe a imagem na area de transferencia no formato que editor entende (DIB)."""
    import io  # noqa: PLC0415
    try:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "BMP")
        dados = buf.getvalue()[14:]                 # DIB = BMP sem o cabecalho
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        if not u.OpenClipboard(None):
            return False
        try:
            u.EmptyClipboard()
            bloco = k.GlobalAlloc(0x2000, len(dados))   # GMEM_MOVEABLE(2)|DDESHARE
            ponteiro = k.GlobalLock(bloco)
            ctypes.memmove(ponteiro, dados, len(dados))
            k.GlobalUnlock(bloco)
            u.SetClipboardData(8, bloco)                # CF_DIB
        finally:
            u.CloseClipboard()
        return True
    except Exception:
        return False


def _area_branca(hwnd) -> tuple | None:
    """Acha a FOLHA de um editor qualquer: a maior mancha clara da janela.

    Serve para quem nao tem acessibilidade decente — o Paint do Windows 10, o
    Photoshop, o GIMP. A folha de desenho e, em todo editor, a maior area de
    cor uniforme e clara dentro da janela. Nao depende de nome de botao nem de
    versao de programa.
    """
    try:
        import numpy as np  # noqa: PLC0415
        import win32gui  # noqa: PLC0415
        from PIL import ImageGrab  # noqa: PLC0415
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        if r - l < 200 or b - t < 200:
            return None
        foto = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
        arr = np.asarray(foto.convert("L"))
        claro = (arr >= 235).astype(np.uint8)
        # a maior mancha clara conexa; o cv2 ja vem com o JARVIS (vision)
        import cv2  # noqa: PLC0415
        n, _rot, dados, _cent = cv2.connectedComponentsWithStats(claro, 8)
        if n <= 1:
            return None
        melhor, area = 0, 0
        for i in range(1, n):
            if dados[i, cv2.CC_STAT_AREA] > area:
                melhor, area = i, dados[i, cv2.CC_STAT_AREA]
        x = int(dados[melhor, cv2.CC_STAT_LEFT])
        y = int(dados[melhor, cv2.CC_STAT_TOP])
        w = int(dados[melhor, cv2.CC_STAT_WIDTH])
        h = int(dados[melhor, cv2.CC_STAT_HEIGHT])
        if w < 200 or h < 150:
            return None
        return (l + x, t + y, l + x + w, t + y + h)
    except Exception:
        return None


def _colar_no_editor(plano: dict, hwnd: int, log) -> tuple[int, str]:
    """Monta a imagem com o Pillow e cola com Ctrl+V.

    O caminho universal: Paint do Windows 10, Photoshop, GIMP, Krita, Word.
    Nao depende de achar ferramenta nem cor — so da area de transferencia, que
    todo editor de imagem entende. O resultado sai perfeito e de uma vez.
    """
    import win32gui  # noqa: PLC0415
    folha = _area_branca(hwnd)
    if folha:
        larg, alt = folha[2] - folha[0], folha[3] - folha[1]
    else:
        try:
            l, t, r, b = win32gui.GetClientRect(hwnd)
            larg = max(400, min(1600, r - l - 80))
            alt = max(300, min(1200, b - t - 220))
        except Exception:
            larg, alt = 900, 600
    # Nao passar do tamanho da folha: imagem maior faz o Paint perguntar se
    # pode aumentar a tela, e uma pergunta na frente do dono trava tudo.
    larg, alt = max(200, min(2400, larg)), max(150, min(1600, alt))
    img = _montar_imagem(plano, larg, alt)
    if not _copiar_imagem(img):
        return 0, "nao consegui por a imagem na area de transferencia"
    _focar(hwnd)
    time.sleep(0.4)
    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)      # Ctrl
    _tecla(0x56)                                          # V
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
    time.sleep(1.2)
    # o que foi colado vem como SELECAO flutuante; o Esc solta a selecao e
    # deixa a imagem na folha, sem apagar nada.
    _tecla(_VK_ESCAPE)
    log(f"[desenhar] colei uma imagem de {larg}x{alt}")
    return len(plano["formas"]), ""


# ══════════════ OLHAR O PROPRIO DESENHO DEPOIS DE PRONTO ═════════════════
#
# Ideia do Diego (07/09/2026): *"nao acha melhor os desenhos passarem pelo
# Codex CLI, ele analisa cada imagem e corrige e melhora os desenhos?"* — e ele
# tem razao. Ate aqui a critica olhava o PLANO (uma lista de numeros); agora ela
# olha o DESENHO, que e o que ele ve.
#
# UM LIMITE HONESTO: o Codex NAO consegue "consertar" o que ja esta pintado —
# no Paint nao da para mover uma peca depois de carimbada. Entao o que ele faz e
# JULGAR, e quando esta ruim o desenho e refeito inteiro, com a critica dele
# junto. Por isso o conserto so acontece UMA vez: duas seriam quatro minutos.
#
# Antes disso vem uma conferencia de graca, aqui mesmo, sem rede: se a folha
# saiu quase toda branca ou com duas cores, alguma coisa quebrou no caminho — e
# foi exatamente o que aconteceu na noite de 07/09, quando um pedido escapou
# para o computer use e a folha ficou com quatro retangulos vazios.
def _foto_da_folha(hwnd):
    """(imagem PIL, jpeg em bytes) da area de desenho. Nada, se nao der."""
    try:
        from PIL import ImageGrab  # noqa: PLC0415
        paint = PaintPorAcessibilidade(hwnd)
        tela = paint.retangulo_da_tela()
        if not tela:
            return None, b""
        img = ImageGrab.grab(bbox=tela, all_screens=True)
        import io  # noqa: PLC0415
        buf = io.BytesIO()
        copia = img.copy()
        copia.thumbnail((1100, 1100))
        copia.convert("RGB").save(buf, "JPEG", quality=72)
        return img, buf.getvalue()
    except Exception:
        return None, b""


def _folha_suspeita(img) -> str:
    """Vazio quando parece um desenho; senao, o que ha de errado com ela."""
    try:
        import numpy as np  # noqa: PLC0415
        pequena = img.copy()
        pequena.thumbnail((240, 240))
        arr = np.asarray(pequena.convert("RGB"))
        claros = (arr >= 244).all(axis=2).mean()
        cores = len({tuple(c) for c in arr.reshape(-1, 3)[::7]})
        if claros > 0.93:
            return f"a folha ficou {claros * 100:.0f}% branca"
        if cores < 6:
            return f"so {cores} cores na folha inteira"
    except Exception:
        pass
    return ""


_PERGUNTA_AO_CODEX = (
    "Esta imagem e um desenho que EU acabei de fazer no Paint para atender o "
    "pedido abaixo. Olhe com olho de professor de desenho e responda SO com um "
    "objeto JSON, sem cerca de codigo:\n"
    '{"bom": true ou false, "nota": 0 a 10, "problemas": "uma frase por '
    'problema, separadas por ponto e virgula"}\n'
    "Ponha `bom` como false se: o desenho nao parece o que foi pedido; falta o "
    "assunto principal; ha peca tapando a porta, o rosto ou o assunto; ha coisa "
    "cortada na borda; ha forma flutuando sem apoio; a folha esta quase vazia. "
    "Se estiver so com defeitos pequenos, `bom` e true.\n\nO PEDIDO FOI: ")


def _veredito(jpeg: bytes, pedido: str, olhar, log) -> tuple:
    """(esta_bom, problemas). Se nao der para olhar, considera bom."""
    if not jpeg or olhar is None:
        return True, ""
    t0 = time.monotonic()
    try:
        resposta = olhar(jpeg, _PERGUNTA_AO_CODEX + str(pedido or ""))
    except Exception as exc:
        log(f"[desenhar] nao consegui olhar o desenho: {str(exc)[:80]}")
        return True, ""
    dados = _so_json(resposta)
    if not dados:
        # ELE NEM SEMPRE OBEDECE O FORMATO. No teste do Diego o Codex descreveu
        # o desenho em prosa ("o desenho mostra uma casa vermelha com telhado,
        # chamine, arvores...") e eu jogava fora e aprovava por omissao. Ler a
        # prosa e melhor do que ignorar: se ela fala em faltar, cortado, errado
        # ou vazio, e reprovacao.
        texto = _sem_acento(str(resposta or ""))
        if not texto:
            return True, ""
        ruim = any(x in texto for x in (
            "falta", "faltou", "faltando", "cortad", "incompleto", "vazio",
            "nao da para", "nao e possivel distinguir", "nao parece",
            "esta errado", "confuso", "ilegivel", "irreconhecivel"))
        log(f"[desenhar] o veredito veio em prosa ({'ruim' if ruim else 'bom'}): "
            f"{str(resposta)[:110]}")
        return (not ruim), (str(resposta)[:300] if ruim else "")
    problemas = str(dados.get("problemas") or "").strip()
    try:
        nota = float(dados.get("nota", 5))
    except (TypeError, ValueError):
        nota = 5.0
    # SO REFAZ QUANDO ESTA MESMO RUIM. Na cidade que o Diego pediu o Codex deu
    # NOTA 7 e reprovou por causa de duas arvores cortadas na borda — e o
    # desenho inteiro foi refeito: 46 s de plano + 101 s desenhando jogados
    # fora, quase SEIS MINUTOS no total. Nota 6 ou mais o desenho esta bom o
    # bastante; refazer por causa de detalhe custa mais do que o detalhe vale.
    bom = bool(dados.get("bom", True)) or nota >= 6
    log(f"[desenhar] olhei o desenho em {time.monotonic() - t0:.0f}s: "
        f"{'bom' if bom else 'ruim'} (nota {nota:.0f}) {problemas[:140]}")
    return bom, problemas


# ────────────────────────────────────────────────────────────── entrada
def _faixa_na_tela(player, ligado: bool, texto: str = "") -> None:
    """A faixa "JARVIS ESTA DESENHANDO" no alto da tela.

    Cobranca do Diego na 2a rodada: *"nao fica nome la em cima, JARVIS esta
    usando computador, aperte ESC para parar"*. E a mesma faixa do computer
    use (`AvisoComputador`, em ui.py) — ela nao aparece na foto da tela e nao
    rouba clique. Voz o dono esquece; a faixa fica ali o tempo todo.
    """
    if player is None:
        return
    for nome in ("mostrar_aviso_pc", "aviso_computador"):
        fn = getattr(player, nome, None)
        if callable(fn):
            try:
                fn(ligado, texto)
            except Exception:
                pass
            return


def _pedido_cita(pedido: str, chaves) -> str:
    p = _sem_acento(pedido)
    for c in chaves:
        if c in p:
            return c
    return ""


# ═══════════════════════════ DESENHAR A PARTIR DE UMA FOTO ═══════════════
#
# Pedido do Diego em 07/09/2026: *"o cliente pode ter uma imagem ou uma foto de
# uma pessoa como exemplo para o JARVIS desenhar em cima daquela imagem que foi
# enviada como referencia"*.
#
# Duas coisas diferentes, e as duas estao aqui:
#   A) DESENHAR A FOTO — a foto vira um desenho: manchas de cor grandes e o
#      contorno por cima. Feito AQUI no computador, com o OpenCV que o JARVIS
#      ja carrega para a visao. Nao passa pela rede, nao gasta cota e nao
#      depende de o modelo "enxergar" bem.
#   B) DESENHAR EM CIMA DA FOTO — a foto e aberta no Paint e vira a folha; o
#      desenho pedido e feito por cima dela (um chapeu, um bigode, uma seta).
#
# Por que as manchas antes do contorno: e a ordem de quem pinta de verdade —
# primeiro a cor, depois o traco por cima. Ao contrario, a cor cobriria o traco.
_EXTENSOES_DE_FOTO = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif",
                      ".tiff")

# "desenha EM CIMA da foto" x "desenha A foto"
_EM_CIMA_DA_FOTO = re.compile(
    r"\b(em cima|por cima|sobre (a|essa|esta|aquela) (foto|imagem)|"
    r"nessa foto|nesta foto|nessa imagem|nesta imagem|na foto|na imagem|"
    r"adiciona|acrescenta|poe|poem|coloca|bota|desenha nel[ae])\b", re.I)


def _abrir_foto(caminho: str):
    """Le a imagem mesmo com acento no caminho (o cv2.imread nao le)."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    dados = np.fromfile(caminho, dtype=np.uint8)
    return cv2.imdecode(dados, cv2.IMREAD_COLOR)


def _nome_da_cor_mais_perto(rgb) -> str:
    return min(_PALETA_CONHECIDA.items(),
               key=lambda kv: _distancia_de_cor(rgb, kv[1]))[0]


def _plano_da_foto(caminho: str, estilo: str = "colorido", log=None) -> dict:
    """A foto virada em plano de desenho: manchas de cor + contorno.

    `estilo`: "colorido" (manchas + contorno) ou "contorno" (so o traco preto,
    tipo esboco a lapis — mais rapido e melhor para rosto).
    """
    log = log or (lambda _m: None)
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    img = _abrir_foto(caminho)
    if img is None:
        return {"nome": "", "formas": []}
    alt0, larg0 = img.shape[:2]
    # tamanho de trabalho: 420 px no maior lado. Maior que isso so aumenta o
    # numero de pontinhos do contorno, e nao a qualidade do desenho.
    escala = 420.0 / max(alt0, larg0)
    if escala < 1.0:
        img = cv2.resize(img, (max(1, int(larg0 * escala)), max(1, int(alt0 * escala))),
                         interpolation=cv2.INTER_AREA)
    alt, larg = img.shape[:2]
    formas: list[dict] = []

    if estilo != "contorno":
        # 1) achatar a foto: o filtro bilateral apaga a textura (poro da pele,
        #    fio de grama) e MANTEM a borda — sem ele cada folha de arvore vira
        #    uma mancha e o desenho fica com 300 pecas.
        macio = cv2.bilateralFilter(img, 9, 90, 90)
        rgb = cv2.cvtColor(macio, cv2.COLOR_BGR2RGB).astype(np.float32)
        # 2) cada pixel vai para a cor mais parecida da paleta do Paint
        nomes = list(_PALETA_CONHECIDA)
        tabela = np.array([_PALETA_CONHECIDA[n] for n in nomes], dtype=np.float32)
        rm = (rgb[:, :, None, 0] + tabela[None, None, :, 0]) / 2.0
        d = rgb[:, :, None, :] - tabela[None, None, :, :]
        peso_r = 2 + rm / 256.0
        peso_b = 2 + (255 - rm) / 256.0
        dist = peso_r * d[..., 0] ** 2 + 4 * d[..., 1] ** 2 + peso_b * d[..., 2] ** 2
        indice = np.argmin(dist, axis=2).astype(np.int32)
        # 3) uma mancha por cor, da maior para a menor
        nucleo = np.ones((3, 3), np.uint8)
        minima = 0.004 * alt * larg          # menos de 0,4% da foto: e sujeira
        # A COR QUE DOMINA A FOTO VIRA O FUNDO, NAO UMA MANCHA (07/09/2026).
        # Numa foto escura, o preto tomava 70% da imagem e o "desenho" saia um
        # retangulo preto com o contorno preto por cima — invisivel. Quem cobre
        # quase tudo e o FUNDO: sai como um retangulo da folha inteira, uma
        # peca so, e nao concorre com o resto.
        contagem = np.bincount(indice.ravel(), minlength=len(nomes))
        dominante = int(np.argmax(contagem))
        fundo = ""
        if contagem[dominante] > 0.42 * alt * larg:
            fundo = nomes[dominante]
            formas.append({"forma": "retangulo", "x": 0.0, "y": 0.0, "l": 1.0,
                           "a": 1.0, "cor": fundo, "preenche": fundo, "grossura": 3})
        achadas = []
        for i, nome in enumerate(nomes):
            if nome == fundo:
                continue
            mascara = (indice == i).astype(np.uint8)
            if mascara.sum() < minima:
                continue
            mascara = cv2.morphologyEx(mascara, cv2.MORPH_OPEN, nucleo, iterations=2)
            mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, nucleo, iterations=2)
            contornos, _h = cv2.findContours(mascara, cv2.RETR_EXTERNAL,
                                             cv2.CHAIN_APPROX_SIMPLE)
            for c in contornos:
                area = cv2.contourArea(c)
                if area < minima:
                    continue
                aprox = cv2.approxPolyDP(c, 0.008 * cv2.arcLength(c, True), True)
                if len(aprox) < 3:
                    continue
                achadas.append((area, nome, aprox[:40]))
        achadas.sort(key=lambda x: -x[0])
        for _area, nome, c in achadas[:20]:
            formas.append({
                "forma": "poligono",
                "pontos": [[float(px[0][0]) / larg, float(px[0][1]) / alt] for px in c],
                "cor": nome, "preenche": nome, "grossura": 3})
        log(f"[desenhar] {len(formas)} manchas de cor tiradas da foto")

    # 4) o contorno por cima — e ele que faz a foto ser reconhecida.
    # PRETO EM FOTO ESCURA NAO APARECE: numa imagem de fundo escuro o traco
    # preto some dentro da mancha preta e o desenho fica so de manchas. Entao a
    # cor do traco vem do brilho medio da foto.
    cinza = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cor_do_traco = "branco" if float(cinza.mean()) < 92 else "preto"
    cinza = cv2.GaussianBlur(cinza, (5, 5), 0)
    bordas = cv2.Canny(cinza, 60, 150)
    bordas = cv2.dilate(bordas, np.ones((2, 2), np.uint8), iterations=1)
    contornos, _h = cv2.findContours(bordas, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    riscos = []
    for c in contornos:
        if cv2.arcLength(c, False) < max(40, 0.08 * max(alt, larg)):
            continue
        aprox = cv2.approxPolyDP(c, 2.0, False)
        if len(aprox) < 2:
            continue
        riscos.append((cv2.arcLength(c, False), aprox[:60]))
    riscos.sort(key=lambda x: -x[0])
    teto = 14 if estilo != "contorno" else 26
    for _comp, c in riscos[:teto]:
        formas.append({
            "forma": "traco",
            "pontos": [[float(px[0][0]) / larg, float(px[0][1]) / alt] for px in c],
            "cor": cor_do_traco, "preenche": "", "grossura": 2})
    log(f"[desenhar] {len(formas)} pecas no total (manchas + contorno)")
    return {"nome": "o desenho da sua foto", "formas": formas}


# O dono TEM de citar a foto para ela virar referencia. Sem isto, uma imagem
# que ele arrastou para a janela ha uma hora, para outra coisa, sequestraria um
# "desenha uma casa" e o JARVIS desenharia a foto antiga.
_CITA_A_FOTO = re.compile(
    r"\b(essa|esta|aquela|a) (foto|imagem|figura|referencia)\b|"
    r"\b(foto|imagem|figura) que (eu )?(te )?(mandei|enviei|passei|arrastei|"
    r"coloquei|anexei)\b|\b(de|da|do) referencia\b|\bcomo (referencia|exemplo|"
    r"modelo|base)\b|\bnessa (foto|imagem)\b|\bdessa (foto|imagem)\b", re.I)


def _achar_referencia(p: dict, player) -> str:
    """De onde vem a foto: do pedido, ou do quadro de arquivo do JARVIS."""
    from pathlib import Path  # noqa: PLC0415
    for chave in ("referencia", "imagem", "foto", "image", "arquivo"):
        bruto = str(p.get(chave) or "").strip().strip('"')
        if bruto and Path(bruto).is_file():
            return bruto
    pedido = str(p.get("pedido") or p.get("description") or "")
    if pedido and not _CITA_A_FOTO.search(pedido):
        return ""
    # o arquivo que o dono arrastou para a janela do JARVIS
    try:
        atual = getattr(player, "current_file", None)
        if atual and Path(str(atual)).is_file():
            if Path(str(atual)).suffix.lower() in _EXTENSOES_DE_FOTO:
                return str(atual)
    except Exception:
        pass
    return ""


def _abrir_foto_no_paint(caminho: str, log):
    """Abre a foto NO Paint: ela vira a folha, e o desenho vai por cima."""
    from pathlib import Path                             # noqa: PLC0415
    try:
        subprocess.Popen(["mspaint.exe", caminho])       # noqa: S607
    except Exception as exc:
        return 0, f"Nao consegui abrir a foto no Paint: {str(exc)[:60]}"
    alvo = _sem_acento(Path(caminho).stem)[:20]
    for _ in range(80):
        time.sleep(0.25)
        hwnd, titulo = _achar_editor(("paint",))
        if hwnd and (not alvo or alvo in _sem_acento(titulo)):
            time.sleep(1.8)
            return hwnd, ""
    return 0, "Abri a foto no Paint mas a janela nao apareceu."


def _e_paint_do_windows_11(paint) -> bool:
    """O Paint novo (WinUI) x o Paint classico de faixa do Windows 10.

    NAO se pergunta a versao do Windows: o que importa e o programa que esta na
    frente — o Paint novo tambem chega em maquinas com Windows 10 pela Loja, e
    uma maquina com Windows 11 pode ter o classico. As duas marcas do Paint
    novo sao a folha com o identificador "image" e o botao "Preenchimento de
    forma"; nenhuma das duas existe no Paint de faixa.

    No Paint classico o desenho sai pelo caminho universal (montar a imagem e
    colar), igual ao do Photoshop, GIMP e Krita.
    """
    try:
        if not paint.retangulo_da_tela():
            return False
        return not _vazio(paint._por_nome("Preenchimento de forma", _TIPO_BOTAO))
    except Exception:
        return False


def _trabalho(p: dict, gerar, log, deve_parar, saida: dict, player=None,
              falar=None, olhar=None) -> None:
    """Tudo o que mexe no editor. Roda numa thread MTA (ver o cabecalho)."""
    ctypes.windll.ole32.CoInitializeEx(None, 0)          # 0 = MULTITHREADED

    pedido = str(p.get("pedido") or p.get("description") or p.get("o_que") or "").strip()
    # "MELHORA ESSA CASA" (07/09/2026). A frase dele nao diz o que desenhar —
    # quem sabe sou eu. Entra o pedido de antes, a folha e limpa e a critica
    # dele vai junto para o cerebro. Sem isto a frase caia no `editar_imagem`,
    # que procurou uma foto que nao existia e respondeu "arraste a imagem".
    refazer = bool(p.get("melhorar") or p.get("refazer"))
    critica = str(p.get("critica") or "").strip()
    if refazer and not pedido:
        pedido = ultimo_desenho()
    programa = _sem_acento(p.get("programa") or p.get("janela") or p.get("editor") or "")
    if not programa:
        programa = _pedido_cita(pedido, tuple(_NOMES_DE_EDITOR)) or "paint"
    termos = _NOMES_DE_EDITOR.get(programa, (programa,))
    e_paint = "paint" in termos[0] and "3d" not in termos[0]

    # ── a foto de referencia, se houver ────────────────────────────────
    referencia = _achar_referencia(p, player)
    sobre_a_foto = False
    if referencia:
        pediu_em_cima = bool(_EM_CIMA_DA_FOTO.search(pedido))
        escolha = _sem_acento(p.get("modo") or "")
        if escolha in ("em cima", "sobre", "por cima"):
            pediu_em_cima = True
        elif escolha in ("copiar", "desenhar a foto", "reproduzir"):
            pediu_em_cima = False
        # Sem pedido nenhum ("desenha essa foto"), o que ele quer e a foto
        # virada desenho — nao um rabisco por cima dela.
        sobre_a_foto = pediu_em_cima and bool(pedido)
        log(f"[desenhar] referencia: {referencia} "
            f"({'por cima dela' if sobre_a_foto else 'virando desenho'})")

    # ── 1) a janela ────────────────────────────────────────────────────
    nasceu_agora = False
    if sobre_a_foto and e_paint:
        hwnd, erro = _abrir_foto_no_paint(referencia, log)
        if erro:
            saida["texto"] = erro
            return
    elif e_paint:
        hwnd, nasceu_agora, erro = _abrir_paint(log)
        if erro:
            saida["texto"] = erro
            return
    else:
        hwnd, titulo = _achar_editor(termos)
        if not hwnd:
            saida["texto"] = (f"Nao achei o {programa} aberto. Abre ele que eu desenho, "
                              "ou me manda desenhar no Paint.")
            return
        log(f"[desenhar] editor: {titulo}")
    _maximizar(hwnd, sempre=e_paint)
    if not _focar(hwnd):
        log("[desenhar] a janela nao veio para a frente; sigo assim mesmo")
    time.sleep(0.3)

    # ── 2) O AVISO SAI AGORA, ANTES DE PENSAR ──────────────────────────
    #
    # Montar o plano leva de 20 a 40 s. O Diego reclamou, com razao, de ficar
    # esse tempo sem saber se podia encostar no mouse: *"ele apenas fala que
    # vai desenhar, fica quase 1 min sem fazer nada, depois fala nao mexa no
    # teclado nem mouse e inicia"*. Entao o recado completo — o que vai
    # desenhar, que ha um tempo de pensar, que nao e para mexer no mouse e que
    # ESC para — e a PRIMEIRA coisa que sai, junto com a faixa na tela.
    assunto_falado = pedido or "o que voce pediu"
    if falar is not None:
        try:
            from actions.narrador_pc import ABERTURA_DESENHO  # noqa: PLC0415
            falar(random.choice(ABERTURA_DESENHO).replace("{assunto}", assunto_falado))
        except Exception as exc:
            log(f"[desenhar] nao consegui avisar: {str(exc)[:60]}")
    _faixa_na_tela(player, True,
                   "JARVIS ESTA DESENHANDO  ·  ESC interrompe  ·  "
                   "nao mexa no mouse nem no teclado")

    # ESC PARA NA HORA, em qualquer janela — o mesmo freio do computer use.
    global _FREIO
    freio = None
    try:
        from actions.usar_computador import FreioEsc  # noqa: PLC0415
        freio = FreioEsc()
        freio.ligar()
        _FREIO = freio
    except Exception as exc:
        log(f"[desenhar] sem freio de ESC: {str(exc)[:60]}")
    parar_de_verdade = deve_parar
    if freio is not None:
        def parar_de_verdade():
            return bool(freio.apertado) or bool(deve_parar and deve_parar())

    try:
        _desenhar_mesmo(p, gerar, log, parar_de_verdade, saida, player, falar,
                        pedido, refazer, critica, referencia, sobre_a_foto,
                        e_paint, programa, hwnd, olhar)
    finally:
        _FREIO = None
        if freio is not None:
            freio.parar()
        _faixa_na_tela(player, False)


def _desenhar_mesmo(p, gerar, log, deve_parar, saida, player, falar, pedido,
                    refazer, critica, referencia, sobre_a_foto, e_paint,
                    programa, hwnd, olhar=None, ja_refez: bool = False) -> None:
    """A parte que pensa e desenha. Separada so para a faixa e o freio do ESC
    serem desligados no `finally` de quem chamou, aconteca o que acontecer."""
    # ── o plano ────────────────────────────────────────────────────────
    t0 = time.monotonic()
    if isinstance(p.get("plano"), dict):
        plano = _limpar_plano(p["plano"])
    elif referencia and not sobre_a_foto:
        # A FOTO VIRA DESENHO AQUI NO COMPUTADOR, sem rede e sem cota: manchas
        # de cor grandes primeiro, contorno preto por cima. E a ordem de quem
        # pinta de verdade — ao contrario, a cor cobriria o traco.
        try:
            plano = _limpar_plano(_plano_da_foto(
                referencia, _sem_acento(p.get("estilo") or "colorido"), log))
        except Exception as exc:
            log(f"[desenhar] nao consegui ler a foto: {type(exc).__name__} {str(exc)[:70]}")
            saida["texto"] = "Nao consegui ler essa imagem para desenhar."
            return
    else:
        if gerar is None:
            saida["texto"] = "Nao tenho como pensar o desenho agora."
            return
        extra = _empurrao_de_variacao()
        if refazer:
            extra = ("\n\nO DONO VIU O SEU DESENHO ANTERIOR DISTO E NAO GOSTOU"
                     + (f': "{critica}".' if critica else ".")
                     + " Faca OUTRO, bem melhor: mais pecas, mais detalhe, "
                     "proporcao certa e composicao diferente da anterior. "
                     "Nao repita o desenho de antes.")
        if sobre_a_foto:
            extra += ("\n\nATENCAO: a folha JA TEM UMA FOTO. Nao desenhe fundo, "
                      "ceu nem chao — desenhe SO o que foi pedido, por cima da "
                      "foto, e deixe o resto da folha em paz.")
        bruto = {}
        for tentativa in (1, 2):
            cobranca = f"{_RECEITA}\n\nAGORA DESENHE ISTO: {pedido}{extra}"
            if tentativa == 2:
                cobranca += ("\n\nATENCAO: a resposta anterior nao era JSON valido. "
                             "Responda SO o objeto JSON, comecando com { e "
                             "terminando com }.")
            try:
                bruto = _so_json(gerar(cobranca, _INSTRUCAO_CURTA))
            except Exception as exc:
                log(f"[desenhar] o cerebro falhou: {str(exc)[:90]}")
                bruto = {}
            if bruto.get("formas"):
                break
        plano = _limpar_plano(bruto)
        # A SEGUNDA PASSADA E O QUE MAIS PESA NA QUALIDADE: na primeira volta
        # ele inventa, na segunda ele OLHA o que fez e conserta. Ver
        # `_melhorar_o_plano`. Nao roda quando o plano veio de foto (ali quem
        # manda e o OpenCV) nem quando alguem entregou o plano pronto.
        plano = _melhorar_o_plano(plano, pedido + extra, gerar, log)
    log(f"[desenhar] plano em {time.monotonic() - t0:.1f}s, {len(plano['formas'])} formas")

    if not plano["formas"]:
        saida["texto"] = ("Nao consegui montar o desenho — nao veio um plano que eu "
                          "entendesse. Tenta pedir de novo?")
        return
    global ULTIMO_PLANO
    ULTIMO_PLANO = plano
    cortadas = max(0, len(plano["formas"]) - TETO_DE_PECAS)
    if cortadas:
        plano["formas"] = plano["formas"][:TETO_DE_PECAS]
        log(f"[desenhar] o plano tinha {len(plano['formas']) + cortadas} pecas; "
            f"desenho {TETO_DE_PECAS} e digo que deixei {cortadas} de fora")
    global ULTIMO_PEDIDO, ULTIMO_EM
    if pedido:
        ULTIMO_PEDIDO, ULTIMO_EM = pedido, time.monotonic()
    if deve_parar and deve_parar():
        saida["texto"] = "Parei antes de comecar."
        return

    # ── os lembretes durante o desenho ─────────────────────────────────
    # A abertura JA foi falada la em cima, na hora do pedido; aqui so entram os
    # lembretes de 40 em 40 segundos ("ainda desenhando, nao mexa no mouse").
    narrador = None
    if falar is not None:
        try:
            from actions.narrador_pc import MEIO_DESENHO, Narrador  # noqa: PLC0415
            narrador = Narrador(falar, intervalo_s=40.0, lembretes=MEIO_DESENHO)
            narrador.comecar(falar_abertura=False)
        except Exception as exc:
            log(f"[desenhar] sem narrador: {str(exc)[:70]}")

    # ── 4) desenhar ────────────────────────────────────────────────────
    t0 = time.monotonic()
    modo = "colado"
    if e_paint:
        try:
            paint = PaintPorAcessibilidade(hwnd, log=log)
            novo_paint = _e_paint_do_windows_11(paint)
        except Exception as exc:
            log(f"[desenhar] acessibilidade fora do ar: {str(exc)[:70]}")
            novo_paint = False
        if novo_paint:
            modo = "a mao"
            feitas, aviso = _desenhar_no_paint(
                plano, hwnd, log, deve_parar, esticar_folha=not sobre_a_foto,
                limpar=refazer and not sobre_a_foto,
                ao_andar=(narrador.andamento if narrador else None))
        else:
            log("[desenhar] Paint classico (Windows 10) — monto a imagem e colo")
            feitas, aviso = _colar_no_editor(plano, hwnd, log)
    else:
        feitas, aviso = _colar_no_editor(plano, hwnd, log)
    log(f"[desenhar] {feitas} pecas em {time.monotonic() - t0:.0f}s {aviso}")

    # ── ELE OLHA O QUE ACABOU DE FAZER ─────────────────────────────────
    if feitas and modo == "a mao" and aviso != "parado pelo dono" and not ja_refez:
        img, jpeg = _foto_da_folha(hwnd)
        estragado = _folha_suspeita(img) if img is not None else ""
        bom, problemas = (False, estragado) if estragado else _veredito(
            jpeg, pedido, olhar, log)
        if not bom and gerar is not None and pedido:
            log(f"[desenhar] vou refazer: {(estragado or problemas)[:120]}")
            if narrador is not None:
                narrador.parar()
                narrador = None
            try:
                falar and falar("Olhei aqui e nao gostei do resultado. "
                                "Vou refazer, so um minuto.")
            except Exception:
                pass
            return _desenhar_mesmo(
                {**p, "melhorar": True,
                 "critica": (estragado or problemas)[:400], "plano": None},
                gerar, log, deve_parar, saida, player, falar, pedido, True,
                (estragado or problemas)[:400], referencia, sobre_a_foto,
                e_paint, programa, hwnd, olhar, ja_refez=True)

    if narrador is not None:
        narrador.parar()

    onde = "no Paint" if e_paint else f"no {programa}"
    o_que = plano.get("nome") or pedido or "o desenho"
    if not feitas:
        saida["texto"] = f"Nao consegui desenhar {onde}. {aviso}".strip()
        return
    if aviso == "parado pelo dono":
        saida["texto"] = (f"Parei como voce pediu — {feitas} pecas ja estavam na "
                          "tela. O computador e seu de novo.")
        return
    # A FRASE DO FIM DEVOLVE O COMPUTADOR (pedido do Diego, 07/09/2026):
    # *"quando terminou nao avisou que terminou, senhor, o computador esta
    # livre"*. Quem ficou um minuto sem poder tocar no mouse precisa ouvir que
    # pode voltar a usar.
    from actions.narrador_pc import FIM_DESENHO  # noqa: PLC0415
    # O QUE FICOU DE FORA E DITO. Ver o comentario do TETO_DE_PECAS: teto que
    # corta calado faz o JARVIS dizer "terminei" com o desenho pela metade.
    faltou = ""
    se_faltaram = cortadas + max(0, len(plano["formas"]) - feitas)
    if aviso == "faltou tempo":
        faltou = (f" Parei em {feitas} pecas para nao passar de tres minutos; "
                  f"ficaram {se_faltaram} detalhes de fora. Se quiser, eu "
                  "completo.")
    elif se_faltaram:
        faltou = (f" Deixei {se_faltaram} detalhes de fora para nao demorar "
                  "demais.")
    if modo == "colado":
        saida["texto"] = f"Pronto, montei {o_que} e coloquei {onde}.{FIM_DESENHO}"
    else:
        saida["texto"] = (f"Pronto, desenhei {o_que} {onde}, com {feitas} pecas."
                          + faltou + FIM_DESENHO)
    try:
        from memory.diario import anotar as _anotar  # noqa: PLC0415
        _anotar("fez", f"desenhei {o_que} {onde} ({feitas} pecas)")
    except Exception:
        pass


def desenhar_na_tela(parameters: dict | None = None, player=None, gerar=None,
                     falar=None, deve_parar=None, olhar=None) -> str:
    """Ferramenta: desenha o que o dono pediu, num editor de imagem.

    `pedido`    o que desenhar ("uma casa com sol e uma arvore")
    `programa`  paint (padrao) | photoshop | gimp | krita | paint.net
    `plano`     um plano pronto, se alguem ja tiver montado
    """
    import sys as _sys  # noqa: PLC0415
    if _sys.platform.startswith("linux"):
        # 26/09/2026: o desenhista dirige o Paint do Windows (UI Automation,
        # ferramentas pelo nome da janela). No Linux ele nao tem com quem falar.
        return ("Desenhar na tela ainda so funciona no Windows, porque eu desenho "
                "usando o Paint. Aqui no Linux eu posso criar uma imagem para voce.")
    p = parameters or {}

    def log(msg):
        if player is not None and hasattr(player, "write_log"):
            try:
                player.write_log(msg)
            except Exception:
                pass

    tem_pedido = bool(str(p.get("pedido") or p.get("description") or "").strip())
    if p.get("melhorar") or p.get("refazer"):
        tem_pedido = tem_pedido or bool(ultimo_desenho())
        if not tem_pedido:
            return "Nao lembro qual desenho voce quer que eu melhore. Me diz o que desenhar?"
    if not tem_pedido and not p.get("plano") and not _achar_referencia(p, player):
        return "Faltou dizer o que desenhar."

    # O COMTYPES TEM DE SER CARREGADO AQUI, ANTES DA THREAD (07/09/2026).
    # Ao ser importado pela PRIMEIRA vez no processo, o comtypes chama
    # `CoInitializeEx` com a bandeira dele (STA). Dentro da minha thread, que
    # ja e MTA, isso estoura na cara: "nao e possivel alterar o modo de thread
    # depois de o mesmo estar definido". Importando antes, la dentro o import
    # ja e so uma consulta ao cache e nao mexe em COM nenhum.
    try:
        import comtypes.client  # noqa: F401,PLC0415
    except Exception as exc:
        return f"Falta uma peca do Windows para eu desenhar: {str(exc)[:70]}"

    # POR QUE UMA THREAD NOVA: a API de acessibilidade do Windows so responde
    # em thread MTA. A thread de onde isto e chamado pode ja estar em STA (o
    # executor do asyncio e reaproveitado, e o pycaw/lixeira ja inicializam COM
    # nela) — e ai a PRIMEIRA chamada de UIA trava para sempre, sem erro.
    saida: dict = {}
    t = threading.Thread(
        target=lambda: _trabalho(p, gerar, log, deve_parar, saida, player, falar,
                                olhar),
        daemon=True)
    t.start()
    t.join(900)
    if t.is_alive():
        return "O desenho demorou demais e eu parei no meio."
    return saida.get("texto") or "Nao consegui desenhar agora."
