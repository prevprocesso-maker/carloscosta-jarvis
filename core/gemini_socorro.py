# -*- coding: utf-8 -*-
"""SOCORRO PARA TUDO QUE A GEMINI FAZ (05/09/2026).

Pergunta do Diego, e ela e a certa: "se a Gemini para, fudeu — tem que ter
socorro em tudo que ela faz".

Ate aqui o JARVIS tinha piso para PENSAR (o Codex da VPS, sem cota) e, desde hoje
de manha, para VER a tela. Mas a chave gratuita da Gemini ainda era usada
DIRETO por sete ferramentas — gerar codigo, processar arquivo, ler documento,
interpretar comando de sistema, achar voo, mexer na area de trabalho. Nessas,
acabou a cota, acabou a funcao: erro tecnico na cara do cliente, no meio do
dia, sem explicacao e sem alternativa.

Este arquivo e a peca que faltava. Ele imita o cliente da Gemini (mesmo
`.models.generate_content(model=..., contents=...)`, mesma resposta com
`.text`), mas por dentro:

    1. tem chave e a cota deixa?  vai pela Gemini, como sempre foi;
    2. sem chave, 429, cota ou erro?  a MESMA pergunta vai para o Codex da
       VPS — que ja e o cerebro do JARVIS e nao tem cota.

Assim nenhuma ferramenta precisou ser reescrita: onde estava
`genai.Client(api_key=...)` passou a estar `gemini_socorro.cliente()`, e o
resto do arquivo continua igual.

O QUE ELE NAO FAZ: nao inventa. Se a Gemini falhar e a ponte com a VPS estiver
fora, a resposta e vazia e quem chamou avisa o dono — mentir aqui seria pior do
que a falha, porque estas ferramentas escrevem codigo e mexem em arquivo.
"""
from __future__ import annotations

import time

# A ponte com a VPS (o relay). Quem entrega e o main, na abertura.
_PONTE = None
# Ate quando nao vale a pena tentar a Gemini de novo (cota estourada).
_GEMINI_ATE = 0.0


def definir_ponte(ponte) -> None:
    global _PONTE
    _PONTE = ponte


def _chave() -> str:
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    try:
        arq = Path(__file__).resolve().parents[1] / "config" / "api_keys.json"
        return str(json.loads(arq.read_text(encoding="utf-8")).get("gemini_api_key") or "").strip()
    except Exception:
        return ""


def chave_parece_boa(chave: str) -> bool:
    """A chave tem CARA de chave da Google (comeca com AIza, ~39 letras)?

    Nao e frescura: o campo do setup aceita qualquer coisa, e o JARVIS do Diego
    estava rodando com `asdasdasdasdasdasdsasd` — vinte letras digitadas so
    para passar da tela. Sem esta conferencia, cada pedido gastava uma ida a
    Google para ouvir "API key not valid" antes de cair no socorro. Com ela, o
    JARVIS nem tenta: vai direto para quem responde.
    """
    c = str(chave or "").strip()
    # DOIS FORMATOS, NAO UM (05/09/2026, no porte para o JARVIS). A conferencia
    # veio do TED exigindo o prefixo `AIza` — o classico do Google. So que a
    # chave que o Diego usa AQUI comeca com `AQ.` (o formato novo do AI Studio),
    # e a regra antiga a reprovaria: a Gemini dele seria desligada em silencio e
    # TODA pergunta cairia no degrau lento, sem uma linha explicando por que.
    # O que esta guarda existe para barrar e lixo digitado so para passar da
    # tela ("asdasdasdasd"), nao chave de verdade num formato mais novo.
    return (c.startswith("AIza") or c.startswith("AQ.")) and len(c) >= 20


def _calar_gemini(erro: str) -> None:
    """Silencia a Gemini pelo tempo que ELA pediu — nunca por um dia inteiro.

    Mesma licao do cerebro (04/09): o 429 dela traz o prazo real ("retry in
    34s") junto de um rotulo que fala em "PerDay". Quem manda e o prazo.
    """
    global _GEMINI_ATE
    try:
        from core.cerebro_openrouter import _quanto_calar_a_gemini  # noqa: PLC0415
        espera, _motivo = _quanto_calar_a_gemini(erro)
    except Exception:
        espera = 60.0
    _GEMINI_ATE = time.time() + float(espera)


class _Resposta:
    """O minimo que as ferramentas leem: `.text`."""

    def __init__(self, texto: str):
        self.text = str(texto or "")
        self.candidates = []

    def __bool__(self) -> bool:
        return bool(self.text)


def _partes(contents) -> tuple[str, bytes | None, bool]:
    """Separa o que veio em (texto, imagem, tem_coisa_que_o_codex_nao_le).

    Aceita os formatos que as ferramentas usam: string, lista de strings,
    PIL.Image, bytes e Part. O terceiro campo existe por causa do audio do
    WhatsApp: mandar um .ogg para o Codex como se fosse foto nao daria erro
    limpo — daria uma resposta inventada sobre uma imagem que nao existe.
    """
    if contents is None:
        return "", None, False
    itens = contents if isinstance(contents, (list, tuple)) else [contents]
    textos, imagem, alheio = [], None, False
    for item in itens:
        if isinstance(item, str):
            textos.append(item)
            continue
        if isinstance(item, (bytes, bytearray)):
            imagem = bytes(item)
            continue
        # PIL.Image
        salvar = getattr(item, "save", None)
        if callable(salvar):
            try:
                import io as _io  # noqa: PLC0415
                buf = _io.BytesIO()
                item.convert("RGB").save(buf, format="JPEG", quality=80)
                imagem = buf.getvalue()
                continue
            except Exception:
                pass
        # Part da propria Gemini (from_bytes)
        bloco = getattr(item, "inline_data", None)
        dados = getattr(bloco, "data", None)
        if dados:
            tipo = str(getattr(bloco, "mime_type", "") or "")
            if tipo.startswith("image/") or not tipo:
                imagem = bytes(dados)
            else:
                alheio = True          # audio, video, pdf: o Codex nao le
            continue
        texto = getattr(item, "text", None)
        if texto:
            textos.append(str(texto))
    return "\n".join(t for t in textos if t), imagem, alheio


def _pede_formato_de_maquina(texto: str) -> bool:
    """O pedido quer um formato que um PROGRAMA vai ler (JSON, x,y, uma data)?

    Importa porque o Codex, por padrao, responde como quem conversa. Para o
    `_detect_action` isso e fatal: ele faz `json.loads` na resposta. Mas nem
    todo pedido e assim — a analise de tela do `code_helper` PRECISA das cercas
    de codigo, e apertar o formato nela quebraria o "salvar o codigo corrigido".
    Por isso a regra so aperta quando o proprio pedido pede.
    """
    t = str(texto or "")
    if "JSON" in t or "NOT_FOUND" in t or "YYYY-MM-DD" in t:
        return True
    baixo = t.lower()
    return ("only the" in baixo or "only a " in baixo or "nothing else" in baixo
            or "no markdown" in baixo or "sem markdown" in baixo)


def _instrucao_de_sistema(kwargs: dict) -> str:
    """A `system_instruction` do `config=` (o flight_finder usa) nao pode se
    perder no caminho do socorro — e ela que diz o que a resposta deve ser."""
    cfg = kwargs.get("config")
    if not cfg:
        return ""
    for nome in ("system_instruction", "systemInstruction"):
        valor = cfg.get(nome) if isinstance(cfg, dict) else getattr(cfg, nome, None)
        if valor:
            return str(valor)
    return ""


class _Models:
    def __init__(self, chave: str, so_gemini: bool = False, so_codex: bool = False):
        self._chave = chave
        self._so_gemini = bool(so_gemini)
        self._so_codex = bool(so_codex)

    def generate_content(self, model: str = "", contents=None, **kwargs):
        texto, imagem, alheio = _partes(contents)
        # 1) A GEMINI, quando da.
        if self._so_gemini and not chave_parece_boa(self._chave):
            raise RuntimeError("sem chave da Gemini valida")
        if (not self._so_codex and chave_parece_boa(self._chave)
                and time.time() >= _GEMINI_ATE):
            try:
                from google import genai as _genai  # noqa: PLC0415
                # Prazo (01/10/2026): sem ele, um 503 do Google prendia 51 s.
                cli = _genai.Client(api_key=self._chave, http_options={"timeout": 60000})
                r = cli.models.generate_content(
                    model=model or "gemini-3.6-flash", contents=contents, **kwargs)
                if getattr(r, "text", ""):
                    return r
            except Exception as exc:
                msg = str(exc)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                    _calar_gemini(msg)
                    print("[socorro] Gemini sem cota; indo pelo Codex da VPS")
                else:
                    print(f"[socorro] Gemini falhou ({msg[:90]}); indo pelo Codex")
                # Em `so_gemini` quem escolhe o proximo passo e quem chamou —
                # e o caso do WhatsApp, que ainda tem outros modelos da Gemini
                # para tentar antes de valer a pena acordar a VPS.
                if self._so_gemini:
                    raise
            if self._so_gemini:
                raise RuntimeError("a Gemini respondeu vazio")
        # 2) O CODEX DA VPS — sem cota, e o mesmo cerebro do JARVIS.
        #    (audio e video ficam de fora: ele nao ouve. Melhor a ferramenta
        #     dizer que nao deu do que ele descrever um som que nao escutou.)
        if alheio and imagem is None:
            return _Resposta("")
        try:
            from core.cerebro_codex import CerebroCodex  # noqa: PLC0415
            if _PONTE is None:
                return _Resposta("")
            c = CerebroCodex(ponte=_PONTE)
            if not c.disponivel():
                return _Resposta("")
            sistema = _instrucao_de_sistema(kwargs)
            cru = _pede_formato_de_maquina(texto) or _pede_formato_de_maquina(sistema)
            if imagem:
                return _Resposta(c.ver(imagem, (sistema + "\n" + texto).strip(), cru=cru))
            return _Resposta(c.escrever(sistema, texto, cru=cru))
        except Exception as exc:
            print(f"[socorro] o Codex tambem nao respondeu: {str(exc)[:120]}")
            return _Resposta("")


class Cliente:
    """Do lado de fora, e o cliente da Gemini de sempre.

    `so_gemini`: nao chama o Codex, levanta o erro — para quem ainda tem outra
    coisa a tentar antes (o WhatsApp tem uma escada de tres modelos).
    `so_codex`: pula a Gemini de proposito — o ultimo degrau dessa escada.
    """

    def __init__(self, api_key: str = "", so_gemini: bool = False,
                 so_codex: bool = False, **_):
        self.models = _Models(str(api_key or _chave()), so_gemini, so_codex)


def cliente(api_key: str = "", so_gemini: bool = False, so_codex: bool = False) -> Cliente:
    return Cliente(api_key, so_gemini, so_codex)
