# -*- coding: utf-8 -*-
"""Cores do JARVIS escolhidas pelo cliente (aba CORES da engrenagem).

Por que existe (08/10/2026)
---------------------------
Pedido dos clientes, pelo Diego: "um sistema de personalizacao de cores, onde
o pessoal pode trocar a cor dos botoes, do nucleo, da interface, de tudo".

Como funciona
-------------
A tela inteira e escrita com a paleta ORIGINAL (classe `C` do ui.py, que nunca
muda). Toda folha de estilo passa por `css()` na hora de ser aplicada (o ui.py
intercepta o `setStyleSheet`), e o desenho feito a mao passa por `cor()`.
Assim a troca vale ate para as cores que mudam com o estado (microfone mudo,
webcam ligada) e da para aplicar AO VIVO, sem reconstruir a janela.

Cada cor da paleta pertence a uma FAMILIA (o ciano principal, o fundo, os
textos, o laranja, o dourado, o verde, o vermelho), descoberta pelo matiz e
pela luz dela. Escolher uma cor para a familia gira o matiz e reescala a luz e
a saturacao de TODOS os tons dela, mantendo a relacao entre eles: a borda
continua mais escura que o titulo, o fundo do painel continua um pouco mais
claro que o da janela. Cinza e branco nao tem familia e nunca mudam.

Guardado em config/cores.json (so neste computador; NAO vai no instalador).
"""
from __future__ import annotations

import colorsys
import json
import re
import threading
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parents[1] / "config" / "cores.json"

# A cor de referencia de cada familia: o tom que, na paleta original, a
# representa. Escolher X para a familia faz este tom virar exatamente X.
ORIGINAL = {
    "principal": "#00d4ff",
    "fundo":     "#00060a",
    "texto":     "#8ffcff",
    "destaque":  "#ff6b00",
    "dourado":   "#ffcc00",
    "verde":     "#00ff88",
    "vermelho":  "#ff3355",
    # nucleo: por padrao acompanha a familia (ver `nucleo`)
    "nucleo":          "#00d4ff",
    "nucleo_dono":     "#00ff88",
    "nucleo_pensando": "#ffcc00",
    "nucleo_mudo":     "#ff3366",
}

# Os botoes do dock: a chave e o objectName do cartao no ui.py.
BOTOES = [
    ("mute",         "Microfone"),
    ("so_nome",      "Sempre ouvindo"),
    ("interrupt",    "Interromper"),
    ("whatsapp",     "WhatsApp"),
    ("remote",       "Controle remoto"),
    ("webcam",       "Webcam"),
    ("cowork",       "Cowork"),
    ("computer_use", "Computer use"),
    ("fileUpload",   "Arquivo"),
]

# O que aparece na aba, em grupos. (chave, nome, explicacao)
GRUPOS = [
    ("INTERFACE", [
        ("principal", "Cor principal", "Bordas, títulos, ícones e destaques"),
        ("fundo",     "Fundo",          "Fundo da janela e dos painéis"),
        ("texto",     "Textos",         "Letras da conversa e dos painéis"),
        ("destaque",  "Laranja",        "Cowork, avisos e alertas leves"),
        ("dourado",   "Dourado",        "Estado pensando e avisos"),
        ("verde",     "Verde",          "O que está ligado: microfone, WhatsApp"),
        ("vermelho",  "Vermelho",       "Mudo, interromper e erros"),
    ]),
    ("NÚCLEO", [
        ("nucleo",          "Núcleo",            "Parado e quando o JARVIS fala"),
        ("nucleo_dono",     "Quando você fala",  "O núcleo enquanto ouve a sua voz"),
        ("nucleo_pensando", "Pensando",          "Enquanto ele pensa ou trabalha"),
        ("nucleo_mudo",     "Mudo",              "Com o microfone desligado"),
    ]),
    ("BOTÕES", [("btn_todos", "Todos os botões", "Uma cor só para a barra inteira")]
              + [("btn_" + k, nome, "Botão " + nome.upper() + " da barra") for k, nome in BOTOES]),
]

_FAMILIAS = ("principal", "fundo", "texto", "destaque", "dourado", "verde", "vermelho")

_trava = threading.RLock()
_escolhas: dict[str, str] = {}
_versao = 0
_cache: dict[str, str] = {}


# ── cores em texto ──────────────────────────────────────────────────────────
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def normalizar(valor) -> str | None:
    """'#AbC123' / 'abc123' -> '#abc123'. Qualquer outra coisa: None."""
    m = _HEX.match(str(valor or "").strip())
    return ("#" + m.group(1).lower()) if m else None


def _rgb(hexa: str) -> tuple[int, int, int]:
    h = hexa.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(r: float, g: float, b: float) -> str:
    def c(v):
        return max(0, min(255, int(round(v))))
    return f"#{c(r):02x}{c(g):02x}{c(b):02x}"


def _hls(hexa: str) -> tuple[float, float, float]:
    r, g, b = _rgb(hexa)
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    return h * 360.0, l, s


# ── carregar e guardar ──────────────────────────────────────────────────────
def carregar() -> None:
    global _versao
    with _trava:
        _escolhas.clear()
        try:
            dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
            for k, v in (dados.get("cores") or {}).items():
                n = normalizar(v)
                if n:
                    _escolhas[str(k)] = n
        except Exception:
            pass
        _versao += 1
        _cache.clear()


def salvar() -> None:
    with _trava:
        try:
            ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
            tmp = ARQUIVO.with_suffix(".tmp")
            tmp.write_text(json.dumps({"cores": dict(_escolhas)}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(ARQUIVO)
        except Exception:
            pass


def definir(chave: str, valor: str | None, gravar: bool = True) -> None:
    """Escolhe (ou tira, com None) a cor de um item. Vale na hora."""
    global _versao
    with _trava:
        n = normalizar(valor) if valor else None
        if chave == "btn_todos" and n:
            # "Todos os botoes" apaga as escolhas individuais: a barra fica
            # uma cor so, que e o que o nome promete.
            for k, _ in BOTOES:
                _escolhas.pop("btn_" + k, None)
        if n is None or (chave in ORIGINAL and n == ORIGINAL[chave]):
            _escolhas.pop(chave, None)
        else:
            _escolhas[chave] = n
        _versao += 1
        _cache.clear()
    if gravar:
        salvar()


def restaurar_tudo() -> None:
    global _versao
    with _trava:
        _escolhas.clear()
        _versao += 1
        _cache.clear()
    salvar()


def escolhida(chave: str) -> str | None:
    return _escolhas.get(chave)


def ativo() -> bool:
    return bool(_escolhas)


def versao() -> int:
    return _versao


def atual(chave: str) -> str:
    """A cor que o item esta usando agora (escolhida ou a que ele herda)."""
    if chave in _escolhas:
        return _escolhas[chave]
    if chave.startswith("nucleo"):
        return nucleo({"nucleo": "ocioso", "nucleo_dono": "dono",
                       "nucleo_pensando": "pensando", "nucleo_mudo": "mudo"}[chave])
    if chave == "btn_todos":
        return cor(ORIGINAL["principal"])
    if chave.startswith("btn_"):
        return cor_botao(chave[4:], None) or cor(ORIGINAL["principal"])
    return _escolhas.get(chave) or ORIGINAL.get(chave, "#00d4ff")


# ── a familia de cada tom ───────────────────────────────────────────────────
def _familia(h: float, l: float, s: float) -> str | None:
    if s < 0.12 or l > 0.97 or l < 0.004:
        return None
    if 165.0 <= h <= 225.0:                 # o ciano do JARVIS e seus tons
        if l < 0.085:
            return "fundo"
        if l >= 0.62 or (l >= 0.40 and s < 0.60):
            return "texto"
        return "principal"
    if 15.0 <= h < 36.0:
        return "destaque"
    if 36.0 <= h < 66.0:
        return "dourado"
    if 85.0 <= h < 165.0:
        return "verde"
    if h >= 325.0 or h < 15.0:
        return "vermelho"
    return None


def _mover(c, a, n) -> tuple[float, float, float]:
    """Leva o tom `c` da familia de referencia `a` para a de referencia `n`
    (todos em HLS, matiz em graus). `a` vira exatamente `n`."""
    ch, cl, cs = c
    ah, al, as_ = a
    nh, nl, ns = n
    h = (ch + (nh - ah)) % 360.0
    s = cs * (ns / as_) if as_ > 1e-6 else ns
    if cl <= al:
        l = cl * (nl / al) if al > 1e-6 else cl
    else:
        l = 1.0 - (1.0 - cl) * (1.0 - nl) / (1.0 - al) if al < 1.0 - 1e-6 else cl
    return h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s))


def cor(hexa: str) -> str:
    """Um tom da paleta ORIGINAL -> o mesmo tom na paleta escolhida."""
    n0 = normalizar(hexa)
    if n0 is None or not _escolhas:
        return hexa
    r = _cache.get(n0)
    if r is not None:
        return r
    h, l, s = _hls(n0)
    fam = _familia(h, l, s)
    alvo = _alvo(fam) if fam else None
    if alvo is None:
        r = n0
    else:
        nh, nl, ns = _mover((h, l, s), _hls(ORIGINAL[fam]), alvo)
        rr, gg, bb = colorsys.hls_to_rgb(nh / 360.0, nl, ns)
        r = _hex(rr * 255, gg * 255, bb * 255)
    _cache[n0] = r
    return r


def _alvo(fam: str):
    """A referencia nova da familia (HLS), ou None se ela fica como esta.

    TEXTOS E FUNDO ACOMPANHAM A COR PRINCIPAL quando nao tem escolha propria:
    trocar so a principal para roxo e deixar as letras em ciano parece defeito.
    Eles giram para o mesmo matiz e guardam a propria luz (o fundo continua
    quase preto, o texto continua claro)."""
    if fam in _escolhas:
        return _hls(_escolhas[fam])
    if fam in ("texto", "fundo") and "principal" in _escolhas:
        ah, al, as_ = _hls(ORIGINAL[fam])
        p0h, _p0l, p0s = _hls(ORIGINAL["principal"])
        p1h, _p1l, p1s = _hls(_escolhas["principal"])
        return ((ah + (p1h - p0h)) % 360.0, al, min(1.0, as_ * (p1s / p0s if p0s > 1e-6 else 1.0)))
    return None


_CSS_COR = re.compile(
    r"#([0-9a-fA-F]{6})(?![0-9a-fA-F])"
    r"|rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(,\s*[\d.]+\s*)?\)")


def css(texto: str) -> str:
    """A folha de estilo com as cores trocadas (alpha preservado)."""
    if not _escolhas or not texto:
        return texto

    def troca(m):
        if m.group(1):
            return cor("#" + m.group(1))
        r, g, b = int(m.group(2)), int(m.group(3)), int(m.group(4))
        if max(r, g, b) > 255:
            return m.group(0)
        nr, ng, nb = _rgb(cor(_hex(r, g, b)))
        if m.group(5):
            return f"rgba({nr},{ng},{nb}{m.group(5).strip()})"
        return f"rgb({nr},{ng},{nb})"

    return _CSS_COR.sub(troca, texto)


# ── botoes e nucleo ─────────────────────────────────────────────────────────
def cor_botao(nome: str, hexa_atual: str | None) -> str | None:
    """Cor de destaque do botao do dock. Escolha do botao > 'todos' > familia."""
    escolha = _escolhas.get("btn_" + str(nome)) or _escolhas.get("btn_todos")
    if escolha:
        return escolha
    return cor(hexa_atual) if hexa_atual else None


def nucleo(modo: str) -> str:
    """Cor do nucleo em cada estado ('ocioso', 'jarvis', 'dono', 'pensando', 'mudo').

    Sem escolha propria, o nucleo acompanha a familia (trocar a cor principal
    troca o nucleo junto — e o que a pessoa espera)."""
    chave = {"ocioso": "nucleo", "jarvis": "nucleo", "dono": "nucleo_dono",
             "pensando": "nucleo_pensando", "mudo": "nucleo_mudo"}.get(modo, "nucleo")
    if chave in _escolhas:
        return _escolhas[chave]
    return cor(ORIGINAL[chave])


def nucleo_tingido() -> bool:
    """As pecas do reator em repouso deixam de ser a arte original (ciano com
    detalhes laranja) e passam a ser tingidas na cor escolhida?"""
    return nucleo("ocioso") != ORIGINAL["nucleo"]


carregar()
