# -*- coding: utf-8 -*-
"""Quem e o dono desta instalacao — e como o JARVIS deve trata-lo.

Por que existe (01/09/2026)
---------------------------
Os clientes do Diego instalavam o JARVIS e **ele os chamava de "Diego"**. O nome
estava escrito na pedra dentro do prompt (`REGRAS_FIXAS`), entao todo cliente
herdava o nome do criador. Constrangedor, e o tipo de detalhe que derruba a
sensacao de produto proprio.

Agora o cliente preenche na engrenagem: como quer ser chamado, apelido, idade,
genero e o que mais quiser contar. Tudo isso entra no prompt, e as treze
personalidades passam a falar com ELE.

O arquivo fica em `config/dono.json` e **nao vai no instalador** — cada
instalacao comeca em branco e o JARVIS pergunta na primeira conversa.
"""
from __future__ import annotations

import json
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parents[1] / "config" / "dono.json"

# Cada campo tem um rotulo para a engrenagem e uma dica do que escrever.
CAMPOS = {
    "como_chamar": ("Como voce quer que eu te chame?", "Ex.: Flavio, chefe, doutor..."),
    "apelido":     ("Tem algum apelido?", "Opcional. Ex.: Flavinho"),
    "idade":       ("Sua idade", "Opcional. Ajuda ele a falar do seu jeito."),
    "genero":      ("Voce e homem ou mulher?", "Opcional. Ex.: homem, mulher, prefiro nao dizer"),
    "sobre":       ("Algo que ele deva saber sobre voce", "Opcional. Ex.: sou dentista, jogo bola aos domingos"),
}

_PADRAO = {chave: "" for chave in CAMPOS}


def carregar() -> dict:
    try:
        dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
        return {**_PADRAO, **{k: str(dados.get(k) or "") for k in CAMPOS}}
    except Exception:
        return dict(_PADRAO)


def salvar(dados: dict) -> dict:
    atual = carregar()
    for chave in CAMPOS:
        if chave in dados:
            atual[chave] = str(dados[chave] or "").strip()
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    temp = ARQUIVO.with_suffix(".tmp")
    temp.write_text(json.dumps(atual, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(ARQUIVO)
    return atual


def como_chamar() -> str:
    """O nome que o JARVIS usa. Vazio significa 'ainda nao sei'."""
    d = carregar()
    return d.get("como_chamar") or d.get("apelido") or ""


def resumo_para_prompt() -> str:
    """O pedaco do prompt que fala do dono. Vazio se ele nao preencheu nada.

    Quando nao ha nome, o JARVIS e instruido a PERGUNTAR — melhor do que
    inventar um tratamento generico ou, pior, usar o nome de outra pessoa.
    """
    d = carregar()
    nome = d.get("como_chamar") or d.get("apelido")
    linhas = []
    if nome:
        linhas.append(f"O dono se chama {nome}. Trate-o assim, pelo nome, "
                      f"naturalmente, sem repetir a toda frase.")
        if d.get("apelido") and d["apelido"] != nome:
            linhas.append(f"Apelido dele: {d['apelido']}. Use quando couber, com intimidade.")
    else:
        linhas.append("VOCE AINDA NAO SABE O NOME DO DONO. Na primeira "
                      "oportunidade natural, pergunte como ele quer ser chamado — "
                      "uma vez so, sem insistir. Ate la, nao invente nome nenhum "
                      "e nao chame ninguem por um nome que voce nao ouviu dele.")
    # CADA LINHA E UM FATO, escrito como frase.
    #
    # A primeira versao dizia "Idade: 35. Fale no registro da idade dele." e o
    # modelo lia aquilo como INSTRUCAO DE ESTILO, nao como dado: perguntado
    # "quantos anos eu tenho", respondia "nao sei sua idade" (01/09/2026). Fato
    # tem de vir em forma de afirmacao, nao de rotulo com dois-pontos.
    if d.get("idade"):
        linhas.append(f"Ele tem {d['idade']} anos de idade — se perguntarem a "
                      f"idade dele, e essa. Fale no registro dessa idade.")
    if d.get("genero"):
        linhas.append(f"Ele e {d['genero']}. Concorde o genero das palavras com isso.")
    if d.get("sobre"):
        linhas.append(f"Ele contou o seguinte sobre si: \"{d['sobre']}\". "
                      f"Isso e verdade e voce sabe. Use quando vier ao caso, "
                      f"sem forcar em toda resposta.")
    return "\n".join(linhas)
