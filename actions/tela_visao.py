# -*- coding: utf-8 -*-
"""Os OLHOS do "usar o computador": foto da tela + lista de elementos clicaveis.

Por que existe (03/09/2026)
---------------------------
A primeira versao mandava so a foto e pedia ao Codex um par de coordenadas.
Funcionava, mas erra: modelo de visao chuta pixel, e um botao de 24 px de lado
nao perdoa 15 px de erro. O Windows JA SABE onde estao os botoes — e a arvore
de acessibilidade (UI Automation), a mesma que o leitor de tela usa.

Entao aqui a gente junta as duas coisas:

  1. a FOTO (o Codex enxerga cores, icone, layout, o que nao tem nome);
  2. a LISTA numerada de elementos (nome exato + centro exato em pixel real).

O Codex responde "clica no elemento 12" e o clique cai no CENTRO do botao, sem
erro de escala e sem erro de mira. Quando o que ele quer nao esta na lista
(canvas, video, imagem, area de desenho), ele ainda pode responder x,y da foto —
os dois caminhos convivem.

MEDIDO nesta maquina (03/09/2026): a arvore da janela em primeiro plano custa
**0,02 a 0,05 s**. Nao e o gargalo; o gargalo e a ida ate a VPS (~5 s).

A ARMADILHA DO CHROME (custou uma rodada para entender): o Chrome so liga a
acessibilidade da PAGINA quando alguem pergunta. Na primeira consulta ele
devolve so a moldura do navegador (31 elementos); na segunda, ja com a pagina
inteira (114). Por isso existe `aquecer()`: uma consulta jogada fora no comeco
da tarefa, e dai em diante a pagina aparece sempre.
"""
from __future__ import annotations

import ctypes
import io
import threading
import time

# Quantos elementos, no maximo, vao numerados na foto e na lista. Acima disso a
# foto vira sopa de numeros e o Codex perde mais do que ganha.
MAX_ELEMENTOS = 45
LARGURA_FOTO = 1280
# 62 -> 72 (23/09/2026): na foto reduzida a 1280 px, letra de 9-10 pt ficava
# borrada o bastante para o modelo ler "R$ 5,43" como "R$ 5,48". A foto cresce
# ~25% (~40 KB), o que nao pesa na volta — o que pesa e o modelo pensar.
QUALIDADE_JPEG = 72
# Quanto uma foto AMPLIADA (acao `ampliar`) cresce, no maximo, e a altura
# maxima dela. Ver `olhar(regiao=...)`.
AMPLIACAO_MAXIMA = 3.0
ALTURA_MAXIMA_AMPLIADA = 1000

# Tipos que interessam. A ordem importa: quando sobra elemento demais, os do
# comeco desta lista ficam.
_PRIORIDADE = ("campo", "botao", "link", "item", "aba", "lista", "caixa",
               "opcao", "menu", "texto", "imagem")

_local = threading.local()


# ── UI Automation ──────────────────────────────────────────────────────────

def _uia():
    """Devolve (modulo UIA, cliente) desta thread. COM e por thread, sempre."""
    cli = getattr(_local, "cliente", None)
    if cli is not None:
        return _local.mod, cli
    import comtypes  # noqa: PLC0415
    import comtypes.client  # noqa: PLC0415
    # MTA, nao STA. O laco roda numa thread de trabalho (run_in_executor), e
    # thread de trabalho nao tem laco de mensagens do Windows; um cliente de UI
    # Automation em STA sem laco de mensagens trava esperando resposta de outro
    # processo. Em MTA a chamada nao depende de fila de mensagem nenhuma. Se o
    # apartamento ja estiver definido (a thread principal do Qt e STA), o
    # CoInitializeEx devolve erro e a gente segue com o que ja existe.
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except Exception:
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    cli = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation)
    _local.mod, _local.cliente = mod, cli
    return mod, cli


def _tabela_tipos(mod):
    return {
        mod.UIA_EditControlTypeId: "campo",
        mod.UIA_ButtonControlTypeId: "botao",
        mod.UIA_HyperlinkControlTypeId: "link",
        mod.UIA_ListItemControlTypeId: "item",
        mod.UIA_TreeItemControlTypeId: "item",
        mod.UIA_TabItemControlTypeId: "aba",
        mod.UIA_ComboBoxControlTypeId: "lista",
        mod.UIA_CheckBoxControlTypeId: "caixa",
        mod.UIA_RadioButtonControlTypeId: "opcao",
        mod.UIA_MenuItemControlTypeId: "menu",
        mod.UIA_SplitButtonControlTypeId: "botao",
        mod.UIA_TextControlTypeId: "texto",
        mod.UIA_ImageControlTypeId: "imagem",
    }


def _cache(mod, cli):
    cr = cli.CreateCacheRequest()
    for pid in (mod.UIA_NamePropertyId, mod.UIA_ControlTypePropertyId,
                mod.UIA_BoundingRectanglePropertyId, mod.UIA_IsOffscreenPropertyId,
                mod.UIA_IsEnabledPropertyId, mod.UIA_ValueValuePropertyId,
                # O ESTADO DE CADA COISA (23/09/2026). Antes a lista dizia
                # "botao Notificacoes" e o modelo tinha de ADIVINHAR pela cor
                # da foto se estava ligado — numa foto de 1280 px o interruptor
                # tem 40 px e a diferenca e um tom de azul. O Windows sabe, e
                # entrega na mesma consulta, de graca.
                mod.UIA_IsTogglePatternAvailablePropertyId,
                mod.UIA_ToggleToggleStatePropertyId,
                mod.UIA_SelectionItemIsSelectedPropertyId,
                mod.UIA_ExpandCollapseExpandCollapseStatePropertyId,
                mod.UIA_HasKeyboardFocusPropertyId):
        cr.AddProperty(pid)
    cr.TreeScope = mod.TreeScope_Subtree
    return cr


def _valor_cache(e, pid):
    """Propriedade do cache SO se for numero/sim-nao; o resto vira None.

    Elemento que nao tem o padrao devolve o "nao suportado" do Windows (um
    objeto COM), e nao um erro — por isso o tipo e conferido aqui.
    """
    try:
        v = e.GetCachedPropertyValue(pid)
    except Exception:
        return None
    if isinstance(v, (bool, int)):
        return v
    return None


def _estado_do_elemento(e, mod, tipo: str) -> list:
    """[ligado], [marcado], [selecionado], [expandido], [desativado], [EM FOCO]."""
    tags: list = []
    try:
        if _valor_cache(e, mod.UIA_IsTogglePatternAvailablePropertyId):
            t = _valor_cache(e, mod.UIA_ToggleToggleStatePropertyId)
            if t in (0, 1, 2) and not isinstance(t, bool):
                if tipo == "caixa":
                    tags.append(("desmarcado", "marcado", "meio marcado")[t])
                else:
                    tags.append(("desligado", "ligado", "parcial")[t])
        sel = _valor_cache(e, mod.UIA_SelectionItemIsSelectedPropertyId)
        if sel is True and tipo in ("item", "aba", "opcao"):
            tags.append("marcado" if tipo == "opcao" else "selecionado")
        ex = _valor_cache(e, mod.UIA_ExpandCollapseExpandCollapseStatePropertyId)
        if ex in (0, 1) and not isinstance(ex, bool) and tipo in ("item", "lista", "menu", "botao"):
            tags.append("recolhido" if ex == 0 else "expandido")
        try:
            if not e.CachedIsEnabled:
                tags.append("desativado")
        except Exception:
            pass
        if _valor_cache(e, mod.UIA_HasKeyboardFocusPropertyId) is True:
            tags.append("EM FOCO")
    except Exception:
        pass
    return tags


def aquecer() -> None:
    """Acorda a acessibilidade do navegador. Chamar UMA vez, no inicio."""
    try:
        elementos_da_janela(None, limite=10)
    except Exception:
        pass


def janela_em_frente() -> tuple[int, str]:
    """(hwnd, titulo) da janela em primeiro plano."""
    try:
        import win32gui  # noqa: PLC0415
        h = win32gui.GetForegroundWindow()
        return h, (win32gui.GetWindowText(h) or "")
    except Exception:
        return 0, ""


def janelas_abertas(maximo: int = 12) -> list[str]:
    """Titulos das janelas visiveis, na ordem em que o Windows as empilha."""
    try:
        import win32gui  # noqa: PLC0415
    except Exception:
        return []
    achadas: list[str] = []

    def visita(h, _):
        if len(achadas) >= maximo:
            return
        if not win32gui.IsWindowVisible(h):
            return
        t = (win32gui.GetWindowText(h) or "").strip()
        if not t or t in ("Program Manager", "Configurações"):
            return
        try:
            e, d, dx, dy = win32gui.GetWindowRect(h)
            if (dx - e) < 200 or (dy - d) < 120:
                return
        except Exception:
            return
        achadas.append(t[:70])

    try:
        win32gui.EnumWindows(visita, None)
    except Exception:
        pass
    return achadas


# Janelas que NAO sao a janela em primeiro plano mas fazem parte da tela:
#   #32768 ................. menu do botao direito e menu de programa (File, Editar)
#   Windows.UI.Core.CoreWindow  menu Iniciar, busca do Windows
#   Shell_TrayWnd .......... a barra de tarefas
#   Progman / WorkerW ...... a area de trabalho (os icones)
# As duas primeiras entram SEMPRE (menu aberto e sempre o alvo do momento); as
# duas ultimas so quando a janela da frente rende pouca coisa — senao a barra
# de tarefas ocuparia dez etiquetas em toda foto.
_POPUPS = ("#32768", "Windows.UI.Core.CoreWindow")
_FUNDO = ("Shell_TrayWnd", "Progman", "WorkerW")


def _hwnds_para_ler(poucos: bool = False) -> list[int]:
    hs: list[int] = []
    try:
        f = ctypes.windll.user32.GetForegroundWindow()
        if f:
            hs.append(f)
    except Exception:
        pass
    try:
        import win32gui  # noqa: PLC0415

        def visita(h, _):
            if not win32gui.IsWindowVisible(h) or h in hs:
                return True
            try:
                cls = win32gui.GetClassName(h) or ""
            except Exception:
                return True
            if cls in _POPUPS or (poucos and cls in _FUNDO):
                hs.append(h)
            return True

        win32gui.EnumWindows(visita, None)
    except Exception:
        pass
    return hs[:5]


def elementos_da_tela(limite: int = MAX_ELEMENTOS) -> list[dict]:
    """Tudo que da para clicar na tela AGORA, nao so na janela da frente.

    Por que (03/09/2026): o menu do botao direito, o menu Arquivo/Editar e o
    menu Iniciar sao JANELAS SEPARADAS no Windows. Lendo so a janela em
    primeiro plano, o Bob clicava com o botao direito, o menu abria... e ele
    nao enxergava item nenhum para escolher. Isso derrubava metade do que se
    faz num computador: renomear, copiar, propriedades, "executar como
    administrador", desinstalar pelo menu.
    """
    juntos: list[dict] = []
    for h in _hwnds_para_ler():
        juntos.extend(elementos_da_janela(h, limite=limite))
    if len(juntos) < 8:
        # Janela pobre (area de trabalho, programa sem acessibilidade): vale a
        # pena trazer a barra de tarefas e os icones da area de trabalho.
        for h in _hwnds_para_ler(poucos=True):
            juntos.extend(elementos_da_janela(h, limite=limite))
    vistos, saida = set(), []
    for el in juntos:
        chave = (el["tipo"], el["nome"], el["esq"] // 8, el["topo"] // 8)
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(el)
    saida.sort(key=lambda el: (el["topo"] // 40, el["esq"]))
    return saida[:limite]


def elementos_da_janela(hwnd: int | None, limite: int = MAX_ELEMENTOS) -> list[dict]:
    """Elementos clicaveis da janela (ou da que esta em frente, se hwnd=None).

    Cada elemento: {tipo, nome, valor, x, y, w, h} em PIXEL REAL da tela,
    (x,y) ja no centro. Sem numero ainda — quem numera e `olhar()`.
    """
    mod, cli = _uia()
    if not hwnd:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return []
    try:
        raiz = cli.ElementFromHandle(hwnd)
    except Exception:
        return []
    tipos = _tabela_tipos(mod)
    conds = [cli.CreatePropertyCondition(mod.UIA_ControlTypePropertyId, i) for i in tipos]
    cond = cli.CreateOrConditionFromArray(conds)
    try:
        achados = raiz.FindAllBuildCache(mod.TreeScope_Subtree, cond, _cache(mod, cli))
    except Exception:
        return []

    tela_l = ctypes.windll.user32.GetSystemMetrics(0)
    tela_a = ctypes.windll.user32.GetSystemMetrics(1)
    saida: list[dict] = []
    vistos: set[tuple] = set()
    for i in range(min(achados.Length, 600)):
        try:
            e = achados.GetElement(i)
            if e.CachedIsOffscreen:
                continue
            r = e.CachedBoundingRectangle
            x, y = int(r.left), int(r.top)
            w, h = int(r.right - r.left), int(r.bottom - r.top)
            if w <= 4 or h <= 4:
                continue
            # Fora da tela (janela arrastada para fora) ou do tamanho da tela
            # inteira (painel de fundo) nao servem de alvo.
            if x + w <= 0 or y + h <= 0 or x >= tela_l or y >= tela_a:
                continue
            if w >= tela_l * 0.98 and h >= tela_a * 0.9:
                continue
            tipo = tipos.get(e.CachedControlType, "?")
            nome = (e.CachedName or "").strip().replace("\n", " ")[:70]
            try:
                valor = (e.CachedValueValue or "").strip()[:40]
            except Exception:
                valor = ""
            if not nome and not valor and tipo not in ("campo", "lista"):
                continue
            if tipo in ("texto", "imagem") and not nome:
                continue
            chave = (tipo, nome, x // 8, y // 8)
            if chave in vistos:
                continue
            vistos.add(chave)
            saida.append({"tipo": tipo, "nome": nome, "valor": valor,
                          "x": x + w // 2, "y": y + h // 2, "w": w, "h": h,
                          "esq": x, "topo": y,
                          "estado": _estado_do_elemento(e, mod, tipo)})
        except Exception:
            continue

    # Sobrou mais do que cabe? Duas fatias, nao uma.
    #
    # O primeiro corte que eu fiz ficava so com os alvos pequenos (a ideia era
    # boa: alvo pequeno e o que o modelo erra sozinho, area grande ele acerta no
    # olho). Na foto de teste o resultado foi ruim de outro jeito: sobraram 45
    # botoezinhos da moldura do navegador e NENHUMA linha da lista de conversas
    # — justamente o que a pessoa manda clicar. Entao o teto e dividido:
    # 2/3 para controle (campo, botao, aba...) e 1/3 garantido para conteudo
    # (item de lista, link), que e onde mora o alvo do pedido.
    if len(saida) > limite:
        def peso(el):
            try:
                p = _PRIORIDADE.index(el["tipo"])
            except ValueError:
                p = len(_PRIORIDADE)
            return (p, el["w"] * el["h"])
        conteudo = [e for e in saida if e["tipo"] in ("item", "link")]
        controle = [e for e in saida if e["tipo"] not in ("item", "link")]
        conteudo.sort(key=peso)
        controle.sort(key=peso)
        # A COTA E UM PISO, NAO UM TETO (04/09/2026). Antes o conteudo levava
        # `limite//3` fixo e o controle ficava com o resto — mas se a janela tem
        # poucos controles (uma pasta do Explorador tem meia duzia), o "resto"
        # sobrava sem dono: 15 arquivos numerados numa lista de 300 e 20 vagas
        # vazias na mesma foto. Agora cada lado leva pelo menos sua cota e
        # divide o que o outro nao usou — a foto sai sempre cheia.
        piso_conteudo = min(len(conteudo), max(1, limite // 3))
        cota_controle = min(len(controle), limite - piso_conteudo)
        cota_conteudo = min(len(conteudo), limite - cota_controle)
        saida = controle[:cota_controle] + conteudo[:cota_conteudo]
    # Ordem de leitura (de cima para baixo, da esquerda para a direita): ajuda
    # o modelo a casar o numero com o que ele ve.
    saida.sort(key=lambda el: (el["topo"] // 40, el["esq"]))
    return saida


# ── Foto ───────────────────────────────────────────────────────────────────

# ONDE A FOTO COMECA NA AREA DE TRABALHO (26/09/2026, cliente do Big Linux).
#
# Tudo aqui supunha o monitor fotografado comecando em (0,0). No PC do cliente
# o PRINCIPAL e o do meio, que comeca no pixel 1920: o clique na posicao da
# foto caia no monitor da esquerda. No Linux a foto e sempre do monitor
# principal, e `olhar()` soma esta origem no `desloc_*` que vira coordenada.
# No Windows fica (0,0), como sempre foi.
ORIGEM = [0, 0]


def _monitor_principal(sct):
    mons = sct.monitors[1:] or sct.monitors[:1]
    import sys as _sys  # noqa: PLC0415
    if _sys.platform.startswith("linux"):
        return next((m for m in mons if m.get("is_primary")), mons[0])
    return mons[0]


def _grab():
    # Wayland do KDE: o mss devolve a tela PRETA (ver core/plataforma.py)
    try:
        from core.plataforma import na_sessao_wayland, foto_da_tela_wayland  # noqa: PLC0415
        if na_sessao_wayland():
            img = foto_da_tela_wayland()
            if img is not None:
                try:
                    import mss  # noqa: PLC0415
                    with mss.mss() as sct:
                        m = _monitor_principal(sct)
                        if img.size == (m["width"], m["height"]):
                            ORIGEM[:] = [m["left"], m["top"]]
                except Exception:
                    pass
                return img
    except Exception:
        pass
    import mss  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415
    with mss.mss() as sct:
        mon = _monitor_principal(sct)
        shot = sct.grab(mon)
        import sys as _sys  # noqa: PLC0415
        ORIGEM[:] = ([mon["left"], mon["top"]] if _sys.platform.startswith("linux") else [0, 0])
        return Image.frombytes("RGB", shot.size, shot.rgb)


LADO_ASSINATURA = 64
LADO_BLOCO = 8      # 8x8 blocos de 8x8 pixels dentro da assinatura


def _assinatura(img) -> bytes:
    """Impressao digital pequena da tela, para saber se ela MUDOU."""
    return img.convert("L").resize((LADO_ASSINATURA, LADO_ASSINATURA)).tobytes()


def diferenca(a: bytes, b: bytes) -> float:
    """Quanto duas assinaturas diferem — pelo PEDACO que mais mudou.

    POR QUE NAO E A MEDIA (custou um teste inteiro no cliente, 03/09/2026): no
    Simplo o Bob abriu a sanfona certa, a lista cresceu na tela... e a media da
    tela inteira mexeu 0,9 — abaixo do limiar. O laco concluiu "a tela parou de
    reagir" e desistiu de uma tarefa que estava dando certo.

    A tela e dividida em 64 pedacos (8x8). Vale o pedaco que mais mudou: um
    menu que abre, uma sanfona que estica ou um campo que recebe texto mexem
    MUITO num pedaco, mesmo mexendo pouco no total.
    """
    if not a or not b or len(a) != len(b):
        return 255.0
    lado = LADO_ASSINATURA
    passo = lado // LADO_BLOCO
    pior = 0.0
    for by in range(LADO_BLOCO):
        for bx in range(LADO_BLOCO):
            soma = 0
            for y in range(by * passo, (by + 1) * passo):
                base = y * lado + bx * passo
                for x in range(passo):
                    soma += abs(a[base + x] - b[base + x])
            media_bloco = soma / float(passo * passo)
            if media_bloco > pior:
                pior = media_bloco
    return pior


def impressao_dos_elementos(elementos: list[dict]) -> str:
    """Assinatura da LISTA de elementos — a outra prova de que a tela mudou.

    Em pagina web (o Simplo e uma delas) abrir uma sanfona muitas vezes mexe
    pouco no desenho e MUITO na arvore: aparecem itens novos, com nome. Se a
    lista mudou, a tela mudou — nao interessa o que a foto diz.

    O ESTADO entra desde 23/09/2026: um interruptor que passa de desligado a
    ligado mexe 40 px na foto e nada na lista de nomes — sem o estado aqui, o
    clique certo contava como "a tela nao reagiu".
    """
    return "|".join(f'{e.get("tipo")}:{(e.get("nome") or "")[:28]}:'
                    f'{",".join(t for t in (e.get("estado") or []) if t != "EM FOCO")}'
                    for e in (elementos or []))


# Abaixo disto a tela e "a mesma": cursor piscando, relogio virando o minuto,
# anti-aliasing de fonte. Acima, mudou de verdade. (Medida em cima do PEDACO
# que mais mudou, nao da media — ver `diferenca`.)
LIMIAR_MUDANCA = 6.0
# Para dizer que a tela PAROU de mudar o criterio e mais frouxo: video tocando
# e animacao de menu nunca ficam parados, e nao e neles que a gente espera.
LIMIAR_ESTAVEL = 14.0


def esperar_tela_parar(limite_s: float = 1.6):
    """Espera a tela parar de mudar (pagina carregando, animacao, menu abrindo).

    Devolve a ultima imagem. Sem isso, a foto sai no meio do carregamento e o
    Codex decide em cima de uma tela que nao existe mais quando o clique sai.

    POR QUE COM TOLERANCIA E NAO IGUALDADE: a primeira versao exigia duas fotos
    IDENTICAS e gastava os 3 s inteiros em qualquer tela viva — o WhatsApp Web
    com um audio tocando nunca fica igual duas vezes. Medido: 3,47 s por passo
    so nisso. Com tolerancia, a mesma tela sai em ~0,5 s.
    """
    img = _grab()
    a = _assinatura(img)
    t0 = time.monotonic()
    while time.monotonic() - t0 < limite_s:
        time.sleep(0.20)
        nova = _grab()
        b = _assinatura(nova)
        img = nova
        if diferenca(a, b) < LIMIAR_ESTAVEL:
            break
        a = b
    return img


def olhar(esperar: bool = True, com_elementos: bool = True, regiao=None) -> dict:
    """Uma "olhada" completa na tela. E o que vai para a VPS a cada passo.

    Devolve dict com:
        jpeg, largura, altura      foto reduzida e ja NUMERADA
        escala_x, escala_y         de pixel da foto para pixel da tela
        desloc_x, desloc_y         onde a foto comeca na tela (0 fora do zoom)
        elementos                  lista com "n" (o numero desenhado na foto)
                                   e "px","py" (o centro dele NA FOTO)
        assinatura                 para comparar com o passo anterior
        titulo, janelas            contexto de qual programa esta na frente

    `regiao=(esq, topo, dir, baixo)` em pixel da TELA: a foto sai so com esse
    pedaco, AMPLIADO (acao `ampliar`, 23/09/2026). E o que um operador faz com
    letra miuda — chega perto antes de ler. A assinatura continua sendo da tela
    inteira, para a prova de "mudou?" nao disparar so porque a foto encolheu.
    """
    from PIL import Image  # noqa: PLC0415
    img = esperar_tela_parar() if esperar else _grab()
    assinatura = _assinatura(img)
    tela_l, tela_a = img.size

    hwnd, titulo = janela_em_frente()
    elementos: list[dict] = []
    if com_elementos:
        try:
            elementos = elementos_da_tela()
        except Exception:
            elementos = []

    desloc_x = desloc_y = 0
    base_l, base_a = tela_l, tela_a
    ox, oy = ORIGEM
    if regiao:
        try:
            # a regiao chega em pixel da AREA DE TRABALHO (ja com a origem)
            esq, topo, dir_, baixo = (int(v) - o for v, o in zip(regiao, (ox, oy, ox, oy)))
        except Exception:
            esq, topo, dir_, baixo = 0, 0, tela_l, tela_a
        esq, dir_ = sorted((max(0, min(tela_l, esq)), max(0, min(tela_l, dir_))))
        topo, baixo = sorted((max(0, min(tela_a, topo)), max(0, min(tela_a, baixo))))
        if dir_ - esq >= 24 and baixo - topo >= 16:
            img = img.crop((esq, topo, dir_, baixo))
            desloc_x, desloc_y = esq, topo
            base_l, base_a = dir_ - esq, baixo - topo
            elementos = [el for el in elementos
                         if esq <= el["x"] < dir_ and topo <= el["y"] < baixo]
            fator = min(AMPLIACAO_MAXIMA, LARGURA_FOTO / float(base_l),
                        ALTURA_MAXIMA_AMPLIADA / float(base_a))
            if fator > 1.05:
                img = img.resize((max(1, int(base_l * fator)), max(1, int(base_a * fator))),
                                 Image.LANCZOS)
        else:
            regiao = None
    for i, el in enumerate(elementos, start=1):
        el["n"] = i

    if img.width > LARGURA_FOTO:
        escala = LARGURA_FOTO / float(img.width)
        img = img.resize((LARGURA_FOTO, max(1, int(img.height * escala))), Image.BILINEAR)
    foto_l, foto_a = img.size
    fx, fy = foto_l / float(base_l), foto_a / float(base_a)
    for el in elementos:
        el["px"] = int((el["x"] - desloc_x) * fx)
        el["py"] = int((el["y"] - desloc_y) * fy)

    if elementos:
        img = _numerar(img, elementos, fx, fy, desloc_x, desloc_y)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=QUALIDADE_JPEG)
    return {
        "jpeg": buf.getvalue(), "largura": foto_l, "altura": foto_a,
        "escala_x": base_l / float(foto_l), "escala_y": base_a / float(foto_a),
        "desloc_x": desloc_x + ox, "desloc_y": desloc_y + oy,
        "regiao": (desloc_x, desloc_y, desloc_x + base_l, desloc_y + base_a) if regiao else None,
        "elementos": elementos, "assinatura": assinatura,
        "titulo": titulo, "hwnd": hwnd, "janelas": janelas_abertas(),
        "tela_l": tela_l, "tela_a": tela_a,
    }


def _numerar(img, elementos: list[dict], fx: float, fy: float,
             desloc_x: int = 0, desloc_y: int = 0):
    """Desenha o numero de cada elemento na foto (o "set of mark").

    Etiqueta amarela com numero preto, encostada no canto superior esquerdo do
    elemento. Amarelo porque sobrevive a qualquer tema (claro, escuro, colorido)
    e nenhuma interface do Windows usa amarelo em botao pequeno.
    """
    from PIL import ImageDraw, ImageFont  # noqa: PLC0415
    dw = ImageDraw.Draw(img, "RGBA")
    try:
        fonte = ImageFont.truetype("arialbd.ttf", 13)
    except Exception:
        fonte = ImageFont.load_default()
    for el in elementos:
        x0 = int((el["esq"] - desloc_x) * fx)
        y0 = int((el["topo"] - desloc_y) * fy)
        x1 = int((el["esq"] + el["w"] - desloc_x) * fx)
        y1 = int((el["topo"] + el["h"] - desloc_y) * fy)
        x0, y0 = max(0, x0), max(0, y0)
        dw.rectangle([x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)],
                     outline=(255, 196, 0, 210), width=1)
        etiqueta = str(el["n"])
        lw = 8 + 7 * len(etiqueta)
        # A etiqueta vai ACIMA do elemento quando ha espaco; dentro, se nao ha.
        ey = y0 - 15 if y0 >= 16 else y0
        dw.rectangle([x0, ey, x0 + lw, ey + 15], fill=(255, 196, 0, 235))
        dw.text((x0 + 4, ey + 1), etiqueta, fill=(0, 0, 0, 255), font=fonte)
    return img


def achar_por_nome(pedaco: str, tipos: tuple = (), hwnd: int | None = None) -> dict | None:
    """Acha na tela AGORA o elemento cujo nome contem `pedaco`.

    E o que torna uma receita confiavel: "clique no botao ENTRAR" nao depende
    de pixel nem de o Codex ter visto a etiqueta certa — o nome vem do proprio
    Windows. Casa por pedaco, sem acento e sem caixa; o menor primeiro (o botao
    "Entrar" ganha do painel que contem a palavra "entrar").
    """
    alvo = _sem_acento((pedaco or "").strip().lower())
    if not alvo:
        return None
    candidatos = []
    lista = (elementos_da_janela(hwnd, limite=400) if hwnd
             else elementos_da_tela(limite=400))
    for el in lista:
        if tipos and el["tipo"] not in tipos:
            continue
        nome = _sem_acento((el.get("nome") or "").lower())
        valor = _sem_acento((el.get("valor") or "").lower())
        if alvo in nome or alvo in valor:
            exato = 0 if nome.strip() == alvo else 1
            no_menu = 1 if el["esq"] < LARGURA_MENU_LATERAL else 0
            candidatos.append((no_menu, exato, el["w"] * el["h"], el))
    if not candidatos:
        return None
    candidatos.sort(key=lambda c: (c[0], c[1], c[2]))
    return candidatos[0][3]


# Largura tipica de menu lateral. Elemento a esquerda disto e "menu", nao
# "conteudo" — e quando o MESMO nome aparece nos dois lugares, o dono quer o
# conteudo. No Simplo isso e a diferenca entre abrir a sanfona do veiculo da
# placa (certo) e cair na busca por montadora (errado, e foi o que acontecia).
LARGURA_MENU_LATERAL = 300


def _achar_com_objeto(pedaco: str, hwnd: int | None = None, incluir_fora: bool = True,
                      preferir_padrao: int | None = None):
    """Como `achar_por_nome`, mas devolve tambem o objeto vivo do Windows.

    E o objeto que sabe se rolar sozinho ate aparecer (ScrollItemPattern).
    Inclui o que esta FORA da vista de proposito: e justamente o que precisa
    ser trazido para a tela.
    """
    mod, cli = _uia()
    alvo = _sem_acento((pedaco or "").strip().lower())
    if not alvo:
        return None, None
    janelas = [hwnd] if hwnd else _hwnds_para_ler(poucos=True)
    achados_por_janela = []
    tipos = _tabela_tipos(mod)
    conds = [cli.CreatePropertyCondition(mod.UIA_ControlTypePropertyId, i) for i in tipos]
    for h in janelas:
        if not h:
            continue
        try:
            raiz = cli.ElementFromHandle(h)
            achados_por_janela.append(
                raiz.FindAllBuildCache(mod.TreeScope_Subtree,
                                       cli.CreateOrConditionFromArray(conds),
                                       _cache(mod, cli)))
        except Exception:
            continue
    if not achados_por_janela:
        return None, None
    candidatos = []
    for achados in achados_por_janela:
      for i in range(min(achados.Length, 800)):
        try:
            e = achados.GetElement(i)
            if not incluir_fora and e.CachedIsOffscreen:
                continue
            nome = _sem_acento((e.CachedName or "").strip().lower())
            if not nome or alvo not in nome:
                continue
            r = e.CachedBoundingRectangle
            w, h = int(r.right - r.left), int(r.bottom - r.top)
            # A ORDEM DE DESEMPATE, quando o mesmo nome aparece varias vezes:
            #   1. quem sabe fazer o que foi pedido (expandir, por exemplo);
            #   2. quem esta no CONTEUDO, nao no menu lateral;
            #   3. o nome exato antes do parcial;
            #   4. o menor, que costuma ser o alvo e nao o painel que o contem.
            sabe_fazer = 0
            if preferir_padrao is not None:
                try:
                    sabe_fazer = 0 if e.GetCurrentPattern(preferir_padrao) else 1
                except Exception:
                    sabe_fazer = 1
            no_menu = 1 if int(r.left) < LARGURA_MENU_LATERAL else 0
            candidatos.append((sabe_fazer, no_menu, 0 if nome == alvo else 1, max(1, w * h), e,
                               {"tipo": tipos.get(e.CachedControlType, "?"),
                                "nome": (e.CachedName or "").strip()[:70],
                                "x": int(r.left) + w // 2, "y": int(r.top) + h // 2,
                                "w": w, "h": h, "esq": int(r.left), "topo": int(r.top),
                                "fora": bool(e.CachedIsOffscreen)}))
        except Exception:
            continue
    if not candidatos:
        return None, None
    candidatos.sort(key=lambda c: (c[0], c[1], c[2], c[3]))
    return candidatos[0][4], candidatos[0][5]


def rolar_ate_ver(pedaco: str, hwnd: int | None = None) -> dict | None:
    """Rola ate o elemento `pedaco` ficar visivel. Devolve onde ele parou.

    Por que existe (03/09/2026, defeito visto no cliente): a tela do Simplo tem
    DUAS barras de rolagem — a da pagina e uma interna, na lista de manuais. A
    roda do mouse rola so o que esta embaixo do ponteiro, entao o Bob rolava a
    pagina, a lista ficava parada, e ele nao chegava no item la embaixo.

    O jeito certo nao e a roda: e pedir ao proprio Windows para trazer o
    elemento a vista (`ScrollItemPattern.ScrollIntoView`). Quem rola e o
    navegador, no container certo — nao interessa quantas barras existam.
    Se o programa nao souber fazer isso, cai na roda do mouse EM CIMA DO
    ELEMENTO (que o proprio Windows disse onde fica), e nao no chute.
    """
    mod, cli = _uia()
    obj, dados = _achar_com_objeto(pedaco, hwnd)
    if obj is None:
        return None
    try:
        padrao = obj.GetCurrentPattern(mod.UIA_ScrollItemPatternId)
        if padrao:
            import comtypes  # noqa: PLC0415
            padrao.QueryInterface(mod.IUIAutomationScrollItemPattern).ScrollIntoView()
            time.sleep(0.45)
            _obj2, novo = _achar_com_objeto(pedaco, hwnd)
            return novo or dados
    except Exception:
        pass
    # Reserva: roda do mouse em cima do proprio elemento, ate ele entrar na tela.
    try:
        import pyautogui  # noqa: PLC0415
        larg, alt = pyautogui.size()
        alvo_x = min(max(dados["x"], 5), larg - 5)
        alvo_y = min(max(dados["y"], 5), alt - 5)
        pyautogui.moveTo(larg // 2, alt // 2, duration=0.1)
        for _ in range(12):
            _obj2, novo = _achar_com_objeto(pedaco, hwnd)
            if novo and not novo.get("fora") and 0 < novo["y"] < alt:
                return novo
            pyautogui.scroll(-360 if alvo_y > alt // 2 else 360)
            time.sleep(0.25)
        _obj2, novo = _achar_com_objeto(pedaco, hwnd)
        return novo or dados
    except Exception:
        return dados


def tipo_do_foco() -> str:
    """Que tipo de coisa esta em foco AGORA ("campo", "botao", "documento"...).

    Serve para nao digitar no vazio: se o foco esta num botao, o texto nao vai
    para lugar nenhum e o `ctrl+a` seleciona a pagina inteira em vez do campo.
    Devolve "" quando o Windows nao sabe dizer — nesse caso a gente tenta assim
    mesmo, porque programa sem acessibilidade (jogo, terminal) e comum.
    """
    try:
        mod, cli = _uia()
        e = cli.GetFocusedElement()
        if e is None:
            return ""
        return _tabela_tipos(mod).get(e.CurrentControlType, "")
    except Exception:
        return ""


def foco_agora() -> str:
    """QUEM esta em foco agora, como texto ("item:Logs do Windows").

    A TERCEIRA PROVA DE QUE A TELA MUDOU (04/09/2026, defeito real).

    No Visualizador de Eventos o JARVIS clicava na arvore, a selecao andava de um
    ramo para outro... e as duas provas que existiam nao viam nada: o desenho
    mal muda (uma linha fica azul) e a lista de elementos e exatamente a mesma
    (os mesmos itens, nos mesmos lugares). Seis voltas assim e o laco desistia
    dizendo "a tela parou de reagir ao que eu faco" — com o dono sem ter
    encostado em nada, e a tela reagindo direitinho.

    O foco e o que muda nesses casos, e o Windows entrega de graca.
    """
    try:
        mod, cli = _uia()
        e = cli.GetFocusedElement()
        if e is None:
            return ""
        tipo = _tabela_tipos(mod).get(e.CurrentControlType, "?")
        try:
            nome = (e.CurrentName or "").strip()[:60]
        except Exception:
            nome = ""
        return f"{tipo}:{nome}"
    except Exception:
        return ""


def expandir(pedaco: str, abrir: bool = True, hwnd: int | None = None) -> dict | None:
    """Abre (ou fecha) a setinha de um item: sanfona, no de arvore, lista.

    Por que nao e "clicar na setinha" (03/09/2026): a setinha tem 12 px, some
    quando a lista rola, e em metade dos programas o clique cai na linha em vez
    do triangulo. O Windows tem o comando certo — `ExpandCollapsePattern` — e
    ele vale para TUDO: sanfona de site, arvore do Explorador, lista suspensa
    de instalador, secao das Configuracoes. Quando o programa nao oferece o
    padrao, cai no clique em cima do proprio elemento, que ja e melhor que
    chutar onde esta o triangulo.
    """
    mod, cli = _uia()
    obj, dados = _achar_com_objeto(pedaco, hwnd,
                                   preferir_padrao=mod.UIA_ExpandCollapsePatternId)
    if obj is None:
        return None
    try:
        bruto = obj.GetCurrentPattern(mod.UIA_ExpandCollapsePatternId)
        if bruto:
            padrao = bruto.QueryInterface(mod.IUIAutomationExpandCollapsePattern)
            # 0=recolhido 1=expandido 2=parcial 3=nao expansivel
            estado = padrao.CurrentExpandCollapseState
            if abrir and estado in (0, 2):
                padrao.Expand()
            elif not abrir and estado == 1:
                padrao.Collapse()
            time.sleep(0.4)
            _o, novo = _achar_com_objeto(pedaco, hwnd)
            return novo or dados
    except Exception:
        pass
    try:
        import pyautogui  # noqa: PLC0415
        pyautogui.click(dados["x"], dados["y"])
        time.sleep(0.4)
        _o, novo = _achar_com_objeto(pedaco, hwnd)
        return novo or dados
    except Exception:
        return dados


def escolher_na_lista(lista: str, opcao: str, hwnd: int | None = None) -> tuple[bool, str]:
    """Escolhe `opcao` na lista suspensa `lista`, pelo comando do Windows.

    Por que (23/09/2026, teste real): "mude o modo dos aplicativos para claro"
    levou 17 passos. A lista suspensa das Configuracoes abre por cima da
    pagina, as opcoes ficavam de fora da lista de elementos (a pagina ja tinha
    45), e o modelo ficou abrindo e fechando a lista e apertando seta. O
    Windows sabe escolher item de lista sem mouse: abre a lista
    (ExpandCollapse), acha a opcao pelo NOME dentro dela e seleciona
    (SelectionItem). Vale para lista do Windows, de instalador e de site.

    Devolve (deu_certo, o que aconteceu).
    """
    mod, cli = _uia()
    alvo_opcao = _sem_acento((opcao or "").strip().lower())
    if not alvo_opcao:
        return False, "faltou dizer qual opcao escolher"
    obj, dados = _achar_com_objeto(lista, hwnd, preferir_padrao=mod.UIA_ExpandCollapsePatternId)
    if obj is None:
        return False, f"nao achei a lista {lista!r} na tela"
    nome_lista = (dados or {}).get("nome") or lista
    try:
        bruto = obj.GetCurrentPattern(mod.UIA_ExpandCollapsePatternId)
        if bruto:
            bruto.QueryInterface(mod.IUIAutomationExpandCollapsePattern).Expand()
            time.sleep(0.45)
    except Exception:
        pass

    def _procurar(raiz):
        cond = cli.CreateOrConditionFromArray([
            cli.CreatePropertyCondition(mod.UIA_ControlTypePropertyId, mod.UIA_ListItemControlTypeId),
            cli.CreatePropertyCondition(mod.UIA_ControlTypePropertyId, mod.UIA_MenuItemControlTypeId),
            cli.CreatePropertyCondition(mod.UIA_ControlTypePropertyId, mod.UIA_RadioButtonControlTypeId),
        ])
        try:
            achados = raiz.FindAll(mod.TreeScope_Descendants, cond)
        except Exception:
            return None
        exato, parecido = None, None
        for i in range(min(achados.Length, 300)):
            try:
                e = achados.GetElement(i)
                n = _sem_acento((e.CurrentName or "").strip().lower())
            except Exception:
                continue
            if n == alvo_opcao:
                exato = e
                break
            if parecido is None and alvo_opcao in n:
                parecido = e
        return exato or parecido

    item = _procurar(obj)
    if item is None:
        # A lista abriu numa janela separada (menu, lista de site): procura em
        # quem esta na tela agora.
        for h in _hwnds_para_ler():
            try:
                item = _procurar(cli.ElementFromHandle(h))
            except Exception:
                item = None
            if item is not None:
                break
    if item is None:
        return False, f"abri a lista {nome_lista!r} mas nao achei a opcao {opcao!r} dentro dela"
    nome_item = ""
    try:
        nome_item = (item.CurrentName or "").strip()
    except Exception:
        pass
    feito = False
    for pid, interface, metodo in (
            (mod.UIA_SelectionItemPatternId, mod.IUIAutomationSelectionItemPattern, "Select"),
            (mod.UIA_InvokePatternId, mod.IUIAutomationInvokePattern, "Invoke")):
        try:
            b = item.GetCurrentPattern(pid)
            if b:
                getattr(b.QueryInterface(interface), metodo)()
                feito = True
                break
        except Exception:
            continue
    if not feito:
        try:
            r = item.CurrentBoundingRectangle
            import pyautogui  # noqa: PLC0415
            pyautogui.click(int((r.left + r.right) / 2), int((r.top + r.bottom) / 2))
            feito = True
        except Exception:
            feito = False
    time.sleep(0.35)
    # Fecha a lista se ela continuou aberta (Select nem sempre fecha).
    try:
        bruto = obj.GetCurrentPattern(mod.UIA_ExpandCollapsePatternId)
        if bruto:
            p = bruto.QueryInterface(mod.IUIAutomationExpandCollapsePattern)
            if p.CurrentExpandCollapseState == 1:
                p.Collapse()
                time.sleep(0.25)
    except Exception:
        pass
    if not feito:
        return False, f"achei {nome_item!r} em {nome_lista!r} mas o Windows nao deixou escolher"
    # O que a lista mostra agora (prova para o modelo).
    agora = ""
    try:
        v = obj.GetCurrentPattern(mod.UIA_ValuePatternId)
        if v:
            agora = v.QueryInterface(mod.IUIAutomationValuePattern).CurrentValue or ""
    except Exception:
        agora = ""
    if not agora:
        try:
            sel = obj.GetCurrentPattern(mod.UIA_SelectionPatternId)
            if sel:
                arr = sel.QueryInterface(mod.IUIAutomationSelectionPattern).GetCurrentSelection()
                if arr and arr.Length:
                    agora = arr.GetElement(0).CurrentName or ""
        except Exception:
            agora = ""
    return True, (f"escolhi {nome_item!r} na lista {nome_lista!r}"
                  + (f" (a lista mostra agora: {agora!r})" if agora else ""))


def acionar(pedaco: str, hwnd: int | None = None) -> dict | None:
    """Aperta o elemento pelo comando do Windows, sem depender do mouse.

    Serve quando o clique nao funciona: botao coberto por outra janela, item
    fora da area visivel, controle que so responde a teclado. Tenta, nesta
    ordem, Invoke (botao), SelectionItem (item de lista) e Toggle (caixa).
    """
    mod, cli = _uia()
    obj, dados = _achar_com_objeto(pedaco, hwnd)
    if obj is None:
        return None
    tentativas = (
        (mod.UIA_InvokePatternId, mod.IUIAutomationInvokePattern, "Invoke"),
        (mod.UIA_SelectionItemPatternId, mod.IUIAutomationSelectionItemPattern, "Select"),
        (mod.UIA_TogglePatternId, mod.IUIAutomationTogglePattern, "Toggle"),
    )
    for pid, interface, metodo in tentativas:
        try:
            bruto = obj.GetCurrentPattern(pid)
            if not bruto:
                continue
            getattr(bruto.QueryInterface(interface), metodo)()
            time.sleep(0.35)
            return dados
        except Exception:
            continue
    return None


def esperar_elemento(pedaco: str, limite_s: float = 12.0, tipos: tuple = (),
                     deve_parar=None) -> dict | None:
    """Espera um elemento aparecer (pagina carregando). None se nao veio.

    `deve_parar` desiste na hora quando o dono manda parar — senao o freio de
    mao ficaria preso ate os 15 s da espera acabarem.
    """
    fim = time.monotonic() + float(limite_s)
    while time.monotonic() < fim:
        if deve_parar and deve_parar():
            return None
        achado = achar_por_nome(pedaco, tipos=tipos)
        if achado:
            return achado
        time.sleep(0.4)
    return None


def _sem_acento(texto: str) -> str:
    import unicodedata  # noqa: PLC0415
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def resumo_dos_elementos(elementos: list[dict], maximo: int = MAX_ELEMENTOS) -> list[str]:
    """A lista em texto que acompanha a foto. Uma linha por elemento.

    `12. botao "Notificacoes" @905,233 [ligado]`

    ONDE E COMO ESTA (23/09/2026). A lista dizia so o nome; o modelo tinha de
    casar o numero com a etiqueta amarela na foto (que as vezes cobre a de
    baixo) e adivinhar pela cor se o interruptor estava ligado. Com o centro
    NA FOTO (@x,y) ele separa o "Som" do menu lateral do "Som" do conteudo sem
    olhar etiqueta; com o estado lido do Windows ele nao confunde azul-claro
    com azul-escuro. Cada linha continua abaixo dos 160 caracteres da VPS.
    """
    linhas = []
    for el in elementos[:maximo]:
        nome = (el.get("nome") or "")[:70]
        valor = (el.get("valor") or "")[:40]
        parte = f'{el["n"]}. {el["tipo"]} "{nome}"' if nome else f'{el["n"]}. {el["tipo"]}'
        if valor and valor != nome:
            parte += f' (contem: "{valor}")'
        if el.get("px") is not None:
            parte += f' @{el["px"]},{el["py"]}'
        if el.get("estado"):
            parte += " [" + ", ".join(el["estado"]) + "]"
        linhas.append(parte[:158])
    return linhas


def texto_da_janela(limite: int = 6000) -> str:
    """O TEXTO da janela da frente, lido do Windows (acao `ler_tela`).

    Por que (23/09/2026): "entre no link, leia a pagina e me de o resumo" levou
    18 passos no log da VPS — o modelo rolava e tentava LER pela foto reduzida,
    onde letra de 10 pt vira borrao. Um operador de verdade seleciona o texto;
    aqui o Windows entrega o texto pronto:

      1. o padrao de TEXTO do documento (navegador, Word, Bloco de Notas, PDF
         no navegador) — e o texto inteiro, na ordem, mesmo o que esta fora da
         vista;
      2. se nao houver (programa comum, caixa, Configuracoes), os NOMES dos
         elementos de texto, link, item e campo, na ordem de leitura.

    Devolve "" se nao conseguir nada. Nunca mexe na area de transferencia nem
    na tela.
    """
    try:
        mod, cli = _uia()
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return ""
        raiz = cli.ElementFromHandle(hwnd)
    except Exception:
        return ""
    melhor = ""
    try:
        cond = cli.CreatePropertyCondition(mod.UIA_IsTextPatternAvailablePropertyId, True)
        achados = raiz.FindAll(mod.TreeScope_Subtree, cond)
        for i in range(min(achados.Length, 10)):
            try:
                e = achados.GetElement(i)
                bruto = e.GetCurrentPattern(mod.UIA_TextPatternId)
                if not bruto:
                    continue
                padrao = bruto.QueryInterface(mod.IUIAutomationTextPattern)
                txt = _arrumar_texto(padrao.DocumentRange.GetText(int(limite)) or "")
                if len(txt) > len(melhor):
                    melhor = txt
            except Exception:
                continue
    except Exception:
        pass
    # 200 letras de TEXTO de verdade: o Chrome devolve o documento cheio de
    # marcadores de objeto (U+FFFC, um por imagem/botao) que nao sao texto.
    if len(melhor) >= 200:
        return melhor[:limite]
    # Reserva: os nomes, na ordem em que se le a tela.
    try:
        tipos = _tabela_tipos(mod)
        quero = (mod.UIA_TextControlTypeId, mod.UIA_HyperlinkControlTypeId,
                 mod.UIA_ListItemControlTypeId, mod.UIA_EditControlTypeId,
                 mod.UIA_ButtonControlTypeId, mod.UIA_TabItemControlTypeId,
                 mod.UIA_TreeItemControlTypeId, mod.UIA_DataItemControlTypeId)
        conds = [cli.CreatePropertyCondition(mod.UIA_ControlTypePropertyId, t) for t in quero]
        achados = raiz.FindAllBuildCache(mod.TreeScope_Subtree,
                                         cli.CreateOrConditionFromArray(conds), _cache(mod, cli))
        itens = []
        vistos = set()
        for i in range(min(achados.Length, 1500)):
            try:
                e = achados.GetElement(i)
                if e.CachedIsOffscreen:
                    continue
                nome = (e.CachedName or "").strip()
                try:
                    valor = (e.CachedValueValue or "").strip()
                except Exception:
                    valor = ""
                tipo = tipos.get(e.CachedControlType, "")
                texto = nome
                if valor and valor != nome:
                    texto = f"{nome}: {valor}" if nome else valor
                if not texto or texto in vistos:
                    continue
                vistos.add(texto)
                r = e.CachedBoundingRectangle
                itens.append((int(r.top) // 12, int(r.left), f"[{tipo}] {texto}" if tipo in ("botao", "campo", "link") else texto))
            except Exception:
                continue
        itens.sort()
        juntos = _arrumar_texto("\n".join(t for _a, _b, t in itens))
        if len(juntos) > len(melhor):
            melhor = juntos
    except Exception:
        pass
    return melhor[:limite]


def _arrumar_texto(texto: str) -> str:
    """Tira linha vazia repetida e espaco sobrando — o texto vai para o modelo."""
    import re as _re  # noqa: PLC0415
    t = str(texto or "").replace("\r\n", "\n").replace("\r", "\n").replace("￼", "")
    t = _re.sub(r"[ \t ]+", " ", t)
    t = _re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()
