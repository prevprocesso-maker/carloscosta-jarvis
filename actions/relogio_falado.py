# -*- coding: utf-8 -*-
"""A hora e a data JA ESCRITAS COMO SE FALA.

Por que existe (01/09/2026)
---------------------------
A ferramenta `hora_agora` devolvia `{'hora': '01:16'}` e o modelo leu isso como
**"dezesseis horas e dezesseis minutos"** — a uma e dezesseis da madrugada. Ele
nao inventou: leu errado um formato ambiguo.

Numero em digito e convite ao erro num sistema que FALA. Entao o relogio passa a
devolver tambem a frase pronta, por extenso, e o modelo so repete. Nao ha o que
interpretar.

Bonus: sai junto o pedaco do dia ("da madrugada", "da tarde"), que e o que faz a
resposta soar de gente em vez de painel de aeroporto.
"""
from __future__ import annotations

from datetime import datetime

_UNIDADES = ["zero", "uma", "duas", "tres", "quatro", "cinco", "seis", "sete",
             "oito", "nove", "dez", "onze", "doze", "treze", "quatorze",
             "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
_DEZENAS = {20: "vinte", 30: "trinta", 40: "quarenta", 50: "cinquenta"}
_MESES = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro"]
_DIAS = ["segunda-feira", "terca-feira", "quarta-feira", "quinta-feira",
         "sexta-feira", "sabado", "domingo"]


def _numero(n: int) -> str:
    if n < 20:
        return _UNIDADES[n]
    dez = (n // 10) * 10
    resto = n % 10
    base = _DEZENAS.get(dez, str(n))
    return base if resto == 0 else f"{base} e {_UNIDADES[resto]}"


def _pedaco_do_dia(h: int) -> str:
    if h < 5:
        return "da madrugada"
    if h < 12:
        return "da manha"
    if h < 18:
        return "da tarde"
    return "da noite"


def hora_por_extenso(agora: datetime | None = None) -> str:
    """'uma e dezesseis da madrugada' — do jeito que uma pessoa fala."""
    agora = agora or datetime.now()
    h24 = agora.hour
    h12 = h24 % 12 or 12
    m = agora.minute

    if h24 == 0 and m == 0:
        return "meia-noite em ponto"
    if h24 == 12 and m == 0:
        return "meio-dia em ponto"

    hora = "meia-noite" if h24 == 0 else ("meio-dia" if h24 == 12 else _numero(h12))
    if m == 0:
        corpo = f"{hora} em ponto"
    elif m == 30:
        corpo = f"{hora} e meia"
    else:
        corpo = f"{hora} e {_numero(m)}"
    if h24 in (0, 12):
        return corpo
    return f"{corpo} {_pedaco_do_dia(h24)}"


def data_por_extenso(agora: datetime | None = None) -> str:
    """'terca-feira, primeiro de setembro de dois mil e vinte e seis'."""
    agora = agora or datetime.now()
    dia = "primeiro" if agora.day == 1 else _numero(agora.day)
    ano = agora.year
    if ano == 2026:
        ano_txt = "dois mil e vinte e seis"
    elif 2000 <= ano < 2100:
        ano_txt = f"dois mil e {_numero(ano - 2000)}"
    else:
        ano_txt = str(ano)
    return f"{_DIAS[agora.weekday()]}, {dia} de {_MESES[agora.month - 1]} de {ano_txt}"


def _o_que_foi_pedido(pergunta: str) -> str:
    """'hora', 'data' ou 'tudo' — pelo que o dono perguntou.

    01/09/2026: "que hora e agora?" e o JARVIS respondia hora, dia da semana e
    data completa. Ele pediu SO a hora. Quem pergunta a hora quer a hora; quem
    pergunta o dia quer o dia. Os dois juntos so quando ele pedir os dois — ou
    quando nao der para saber o que ele pediu (a ferramenta foi chamada sem
    pergunta nenhuma por perto).
    """
    p = pergunta.lower()
    p = "".join(c for c in p if c.isalnum() or c.isspace())
    # "data", "dia", "semana", "mes", "ano" — e a forma sem acento que o STT
    # costuma mandar ("que dia e hoje", "que mes estamos").
    pede_data = any(x in p for x in (
        "data", " dia", "dia ", "semana", " mes", "mes ", "ano ", " ano",
        "hoje e", "hoje eh", "estamos em", "calendario", "feriado",
    ))
    pede_hora = any(x in p for x in ("hora", "horas", "relogio", "que horas"))
    if pede_hora and pede_data:
        return "tudo"
    if pede_data:
        return "data"
    if pede_hora:
        return "hora"
    return "tudo"


def agora_falado(pergunta: str = "") -> dict:
    """Tudo que o modelo precisa para falar a hora sem interpretar nada.

    `pergunta` e o que o dono acabou de dizer: decide se `falar_assim` leva so
    a hora, so a data, ou as duas.
    """
    agora = datetime.now()
    pedido = _o_que_foi_pedido(str(pergunta or ""))
    if pedido == "hora":
        falar = hora_por_extenso(agora)
    elif pedido == "data":
        falar = data_por_extenso(agora)
    else:
        falar = f"{hora_por_extenso(agora)}, {data_por_extenso(agora)}"
    return {
        "falar_assim": falar,
        "pedido": pedido,
        "hora_falada": hora_por_extenso(agora),
        "data_falada": data_por_extenso(agora),
        # Os campos antigos continuam, para quem ja dependia deles.
        "hora": agora.strftime("%H:%M"),
        "data": agora.strftime("%d/%m/%Y"),
        "dia_da_semana": _DIAS[agora.weekday()],
    }
