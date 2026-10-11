"""Em que lingua o JARVIS fala (24/09/2026).

O padrao continua PORTUGUES DO BRASIL, como sempre foi, e nada muda para quem
nao tiver o arquivo abaixo.

Pedido do Diego para um cliente que mora no Canada: o JARVIS (e todas as
outras vozes/personagens) falando INGLES desde a primeira frase. O pacote
desse cliente leva `config/idioma.json` com {"idioma": "en"}; o resto do
programa pergunta AQUI antes de montar prompt, escolher o ouvido, falar a
saudacao ou uma frase fixa.

Por que um arquivo so e nao um campo no persona.json: o persona.json e do
cliente (a engrenagem regrava), e o idioma e decisao da entrega.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
_ARQ = _RAIZ / "config" / "idioma.json"
_conf: dict | None = None


def _config() -> dict:
    """O idioma.json, lido uma vez: trocar pede reabrir o JARVIS."""
    global _conf
    if _conf is None:
        try:
            dados = json.loads(_ARQ.read_text(encoding="utf-8"))
            _conf = dados if isinstance(dados, dict) else {}
        except Exception:
            _conf = {}
    return _conf


def idioma() -> str:
    """'pt' (padrao) ou 'en'."""
    v = str(_config().get("idioma") or "pt").strip().lower()[:2]
    return v if v in ("pt", "en") else "pt"


def caminho_da_bandeira() -> Path | None:
    """A bandeira do pais do cliente, se a entrega pediu uma ("bandeira": "ca")."""
    codigo = re.sub(r"[^a-z]", "", str(_config().get("bandeira") or "").lower())[:3]
    if not codigo:
        return None
    p = _RAIZ / "assets" / "flags" / f"{codigo}.png"
    return p if p.is_file() else None


def em_ingles() -> bool:
    return idioma() == "en"


def lingua_do_ouvido() -> str:
    """O `language` do whisper e do ouvido da VPS."""
    return "en" if em_ingles() else "pt"


# A regra vai no FIM do prompt: o fim pesa mais que o comeco, e o resto do
# programa (regras, memoria, diario, resultado de ferramenta) esta escrito em
# portugues — sem esta frase o modelo acompanha a lingua do contexto.
REGRA_INGLES = (
    "LANGUAGE RULE — this overrides every earlier instruction about language: "
    "always speak and write to the user in natural, fluent English, even though "
    "these instructions, the memory, the diary and the tool results are written "
    "in Portuguese. Translate tool results and any Portuguese text into English "
    "before saying them; never answer in Portuguese. Only switch languages if the "
    "user explicitly asks you to.")


def com_idioma(instrucao: str) -> str:
    """Acrescenta a regra do ingles no fim de um prompt (no portugues, nada)."""
    if not em_ingles():
        return instrucao
    return f"{instrucao}\n\n{REGRA_INGLES}"


# Os pedacos de prompt que PEDEM portugues, trocados pelo pedido de ingles.
# Casados sem acento e sem caixa: o codigo escreve das duas formas.
_TROCAS = [
    (r"mordomo ingl[eê]s falando portugu[eê]s\b", "English butler"),
    (r"sotaque neutro brasileiro", "neutral North American accent"),
    (r"\bbrazilian portuguese\b", "English"),
    (r"\bportuguese\b", "English"),
    (r"\b(em\s+)?portugu[eê]s\s+do\s+brasil\b", "in English"),
    (r"\b(em\s+)?portugu[eê]s\b", "in English"),
]


def no_idioma(texto: str) -> str:
    """Troca, dentro de um prompt, o pedido de portugues pelo de ingles.

    Serve para instrucoes que ja existem e so dizem EM QUE LINGUA falar (a da
    voz, a da tela, a da camera). No portugues devolve o texto intocado.
    """
    if not em_ingles() or not texto:
        return texto
    t = str(texto)
    for padrao, novo in _TROCAS:
        t = re.sub(padrao, novo, t, flags=re.I)
    return t


# ── FRASES FIXAS que o JARVIS fala sem passar pelo cerebro ────────────────
#
# Saudacao, avisos do computer use, "so um instante". O cerebro ja responde em
# ingles pela REGRA_INGLES; estas nao passam por ele, entao a traducao e aqui.
# Chave SEM acento e sem caixa (ver `_chave`).
_FRASES_EN = {
    "anotado, chefe.": "Noted, boss.",
    "certo, parei. nao encosto mais no computador.": "Alright, I stopped. I won't touch the computer anymore.",
    "deixa comigo, vou digitar ai.": "Leave it to me, I'll type it in.",
    "so um instante, chefe.": "Just a moment, boss.",
    "ate logo, chefe.": "See you later, boss.",
    "para eu mexer no computador, diga primeiro: ativar computador.":
        "For me to use the computer, first say: activate computer.",
    "para eu desenhar eu preciso mexer no seu computador. diga primeiro: ativar computador.":
        "To draw, I need to use your computer. First say: activate computer.",
    "certo, chefe. o que o senhor quer que eu faca no computador?":
        "Alright, boss. What would you like me to do on the computer?",
    # quem foi chamado pelo nome e ainda nao ouviu o pedido
    "pois nao, chefe.": "Yes, boss?",
    "estou aqui.": "I'm here.",
    "pode falar, chefe.": "Go ahead, boss.",
    "diga, chefe.": "Tell me, boss.",
    "as ordens.": "At your service.",
    # aviso do Linux quando ja ha um JARVIS aberto (26/09/2026)
    "o jarvis ja esta aberto. procure a janela dele.":
        "JARVIS is already open. Look for its window.",
}


def _chave(texto: str) -> str:
    import unicodedata as _u  # noqa: PLC0415
    t = _u.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", t).strip().lower()


def frase(texto: str) -> str:
    """A frase fixa na lingua do JARVIS. Frase desconhecida volta como veio."""
    if not em_ingles() or not texto:
        return texto
    return _FRASES_EN.get(_chave(texto), texto)


# Palavras que so aparecem em portugues. Serve para decidir se um resultado de
# ferramenta pode ser falado direto ou se o cerebro tem de traduzir antes.
_SO_PORTUGUES = re.compile(
    r"\b(nao|não|voce|você|esta|está|estou|aberto|aberta|abri|abrir|consegui|"
    r"arquivo|pasta|pronto|feito|agora|tela|computador|senhor|chefe|com|para|"
    r"uma|um|os|de|da|dos|das|na|nas|nos|em|ao|pelo|pela|que|foi|seu|sua|isso|"
    r"aqui|ja|sem|mas|ou|tem|sao|vou|fechei|salvei|salvo|bloco|notas|janela|"
    r"janelas|fechar|fechado|fechada|encontrei|nenhuma|nenhum|havia|abrindo|"
    r"mandei|recusou|quis|chefe|"
    r"programa|erro|falhou|ferramenta)\b", re.I)


def parece_portugues(texto: str) -> bool:
    t = str(texto or "")
    if re.search(r"[ãõçáéíóúâêô]", t, re.I):
        return True
    achou = len(_SO_PORTUGUES.findall(t))
    # Resultado de ferramenta costuma ser curto ("Pronto.", "Pasta aberta."):
    # ai uma palavra so ja decide.
    return achou >= 2 or (achou >= 1 and len(t.split()) <= 5)
