# -*- coding: utf-8 -*-
"""O DIARIO: o que foi dito e o que foi FEITO (05/09/2026).

Pedido do Diego, com o caso na mao. Ele mandou o JARVIS escrever "bom dia" no
bloco de notas e salvar; minutos depois perguntou "voce lembra o que voce
escreveu?" e ouviu:

    "Nao tenho como saber o conteudo do arquivo. Voce que digitou."

Duas coisas erradas de uma vez: ele NAO sabia (nao tinha onde olhar) e ainda
jogou a culpa no dono por uma coisa que ele mesmo tinha feito.

POR QUE NAO BASTAVA A MEMORIA QUE JA EXISTIA. O `long_term.json` guarda FATOS
sobre a pessoa — nome, cidade, time, gosto. E o certo para "voce sabe que eu
sou santista". Nao serve para "o que voce fez ha dez minutos": isso e
EPISODIO, tem hora, acontece o dia inteiro e envelhece.

Sao coisas diferentes e ficam em arquivos diferentes:

    long_term.json  ... quem o dono E          (poucos, para sempre)
    diario.jsonl    ... o que ACONTECEU        (muitos, em ordem, com hora)

O formato e uma linha de JSON por acontecimento, e nao um JSON grande: assim
escrever custa um `append` (sem reler nem reescrever o arquivo inteiro a cada
fala) e um arquivo cortado no meio de uma queda perde UMA linha, nao tudo.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path


def _base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CAMINHO = _base() / "memory" / "diario.jsonl"
_TRAVA = threading.Lock()

# A CONVERSA E POR SESSAO (10/09/2026, pedido do Diego). Cada vez que o
# programa abre e uma sessao nova. O caso: ele fechou o JARVIS logo depois de
# dizer "ficou top, voce ja fez ja", abriu de novo, arrastou tres arquivos — e
# ouviu "Valeu, chefe! Que bom que curtiu", resposta a uma fala da sessao
# ANTERIOR, que o diario continuava mandando ao cerebro como se fosse de
# agora. Nas palavras dele: "as principais memorias, como nome e gosto, ficam
# permanentes; memoria de conversa tem que ser por sessao".
#
# O que e permanente continua no `long_term.json` (quem o dono e). O diario
# guarda tudo, com a sessao de cada linha; o cerebro recebe so a sessao atual —
# e as antigas SO quando o dono pergunta do passado ("o que a gente fez
# ontem?"), marcadas como de antes.
SESSAO = f"{os.getpid()}-{int(time.time())}"

# Quantas linhas ficam no arquivo. 4.000 dao varias semanas de uso pesado e o
# arquivo nao passa de ~1 MB. Passou disso, as mais velhas saem.
MAX_LINHAS = 4000
# O que cabe em cada campo. Nao e economia de disco: e o texto que sera lido
# pelo cerebro depois, e um `digitar` de 2.000 letras entupiria a nota.
MAX_TEXTO = 300


def _agora() -> str:
    return datetime.now().strftime("%d/%m %H:%M")


def anotar(tipo: str, texto: str, detalhe: str = "") -> None:
    """Grava um acontecimento. NUNCA levanta excecao: diario nao pode derrubar
    conversa."""
    try:
        linha = {
            "quando": _agora(),
            "tipo": str(tipo or "")[:24],
            "texto": str(texto or "").strip()[:MAX_TEXTO],
            "detalhe": str(detalhe or "").strip()[:MAX_TEXTO],
            "sessao": SESSAO,
        }
        if not linha["texto"] and not linha["detalhe"]:
            return
        with _TRAVA:
            CAMINHO.parent.mkdir(parents=True, exist_ok=True)
            with CAMINHO.open("a", encoding="utf-8") as f:
                f.write(json.dumps(linha, ensure_ascii=False) + "\n")
            _aparar()
    except Exception:
        pass


def _aparar() -> None:
    """Corta o arquivo quando ele passa do teto. Roda dentro da trava."""
    try:
        if not CAMINHO.exists() or CAMINHO.stat().st_size < 400_000:
            return                      # barato: so conta linha quando cresce
        linhas = CAMINHO.read_text(encoding="utf-8", errors="ignore").splitlines()
        if len(linhas) <= MAX_LINHAS:
            return
        CAMINHO.write_text("\n".join(linhas[-MAX_LINHAS:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def ler(limite: int = 40) -> list:
    """As ultimas anotacoes, da mais antiga para a mais nova."""
    try:
        if not CAMINHO.exists():
            return []
        linhas = CAMINHO.read_text(encoding="utf-8", errors="ignore").splitlines()
        saida = []
        for l in linhas[-max(1, limite):]:
            try:
                saida.append(json.loads(l))
            except Exception:
                continue
        return saida
    except Exception:
        return []


def procurar(termo: str, limite: int = 8) -> list:
    """As anotacoes que falam de alguma coisa — para quando ele perguntar
    'o que voce escreveu no bloco de notas?'."""
    alvo = str(termo or "").strip().lower()
    if not alvo:
        return []
    achados = [a for a in ler(MAX_LINHAS)
               if alvo in (a.get("texto", "") + " " + a.get("detalhe", "")).lower()]
    return achados[-limite:]


# Quantas linhas vao para o cerebro em toda pergunta. 12 cobre a conversa que
# esta acontecendo agora e o que ele fez ha pouco, sem virar um paredao de
# texto — o que atrasaria a resposta e nao ajudaria em nada.
PARA_O_CEREBRO = 12


# Pergunta sobre OUTRA vez em que o programa esteve aberto. So com uma destas
# o cerebro ve as sessoes antigas; sem elas, cada abertura comeca do zero.
#
# ELE ESTAVA ABRINDO O PASSADO QUASE SEMPRE (22/09/2026, queixa do Diego:
# "quando eu fecho e abro ele, ele fica puxando conversas da sessao anterior").
# A culpa era das quatro ultimas palavras da lista antiga: `lembra|lembrar|
# lembrou|recorda`, SOLTAS. "lembra de me acordar as 7", "me lembra de comprar
# pao", "voce lembra de mim?" — nenhuma dessas pergunta o que foi conversado
# em outra sessao, e todas destrancavam o diario inteiro. Logo depois de abrir
# o programa, quando nao ha NADA da sessao de agora, o cerebro so recebia
# conversa velha — e respondia como se ela estivesse acontecendo.
#
# Agora "lembra" so abre o passado quando ele pergunta sobre o que foi dito ou
# feito ("lembra o que", "lembra quando", "lembra da conversa"). Lembrete de
# tarefa futura ("me lembra de...") fica de fora de proposito.
_FALA_DO_PASSADO = re.compile(
    r"\b(ontem|anteontem|outro dia|semana passada|mes passado|"
    r"(da|na|a) (ultima|outra) vez|(sessao|conversa) (passada|anterior)|"
    r"antes de (fechar|reiniciar|desligar|abrir)|mais cedo|hoje cedo|"
    r"(lembra|lembrar|lembrou|recorda)s? (o que|do que|quando|onde|qual|"
    r"que (eu|voce|a gente|nos)|da (nossa )?conversa|do (nosso )?papo|disso|"
    r"daquilo|daquela|daquele))\b")

# Linha mais velha que isto nunca conta como "a conversa de agora", mesmo que a
# marca de sessao bata. Rede de seguranca contra o caso raro de uma sessao
# antiga ser reconhecida como a atual (o programa reiniciado pelo vigia no
# mesmo segundo, por exemplo).
_HORAS_DE_VALIDADE = 8.0


def _sem_acento(t: str) -> str:
    s = unicodedata.normalize("NFKD", str(t or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _linha(a: dict) -> str:
    tipo = a.get("tipo", "")
    quando = a.get("quando", "")
    texto = a.get("texto", "")
    detalhe = a.get("detalhe", "")
    if tipo == "dono":
        return f"{quando} o dono disse: {texto}"
    if tipo == "voce":
        return f"{quando} voce respondeu: {texto}"
    if tipo == "fez":
        return f"{quando} VOCE FEZ: {texto}" + (f" -> {detalhe}" if detalhe else "")
    return f"{quando} {tipo}: {texto}"


def _e_de_agora(a: dict) -> bool:
    """A linha e desta abertura do programa? Marca de sessao E idade."""
    if a.get("sessao") != SESSAO:
        return False
    quando = str(a.get("quando") or "")
    if not quando:
        return True
    try:
        # "dd/mm HH:MM" — sem ano; monta com o ano de hoje e aceita a virada.
        hoje = datetime.now()
        d = datetime.strptime(quando, "%d/%m %H:%M").replace(year=hoje.year)
        if d - hoje > timedelta(days=1):      # 31/12 lido em 01/01
            d = d.replace(year=hoje.year - 1)
        return (hoje - d) <= timedelta(hours=_HORAS_DE_VALIDADE)
    except Exception:
        return True


def resumo_para_o_cerebro(limite: int = PARA_O_CEREBRO, pergunta: str = "") -> str:
    """O diario em texto curto, do jeito que o cerebro le melhor.

    So a SESSAO ATUAL. Linha de sessao anterior (ou sem sessao, de antes desta
    regra) so entra quando a `pergunta` fala do passado, e vem separada.
    """
    todas = ler(300)
    desta = [a for a in todas if _e_de_agora(a)][-limite:]
    linhas = [_linha(a) for a in desta]
    if pergunta and _FALA_DO_PASSADO.search(_sem_acento(pergunta)):
        antigas = [a for a in todas if not _e_de_agora(a)][-limite:]
        if antigas:
            # O ROTULO E PARTE DO CONSERTO. Antes dizia so "DE OUTRAS VEZES",
            # e o cerebro emendava aquilo na conversa de agora — respondendo a
            # uma fala de outro dia como se tivesse acabado de ouvi-la.
            linhas = (["CONVERSA DE OUTRO DIA/OUTRA ABERTURA DO PROGRAMA. NAO "
                       "e a conversa de agora, NAO responda a estas falas nem "
                       "de continuidade a elas: use so para responder o que ele "
                       "perguntou sobre o passado."]
                      + [_linha(a) for a in antigas]
                      + (["A CONVERSA DE AGORA (esta sim e a que esta "
                          "acontecendo):"] + linhas if linhas else
                         ["A CONVERSA DE AGORA ainda nao tem nada: ele acabou "
                          "de abrir o programa."]))
    return "\n".join(linhas)
