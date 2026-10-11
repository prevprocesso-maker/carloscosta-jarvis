# -*- coding: utf-8 -*-
"""RECEITAS: o que o Bob ja sabe fazer de cor, sem gastar volta na VPS.

Por que existe (03/09/2026)
---------------------------
O laco de olhar-e-clicar resolve QUALQUER coisa, mas cada volta custa ~8 s.
O cliente da TJ Mecanica nao pede "qualquer coisa": ele pede as MESMAS cinco
coisas no MESMO sistema, o dia inteiro ("abre o Simplo e joga a placa ABC1D23").
Isso nao precisa de adivinhacao: precisa de receita.

Entao aqui tem duas camadas, e as duas alimentam o mesmo laco:

  1. RECEITA — passos fixos, escritos num .json em `config/receitas/`. Abrir a
     URL certa, esperar o campo aparecer, digitar, Enter. Sem modelo, sem foto,
     sem VPS: 2 segundos em vez de 25, e sempre igual.
  2. DICAS — quando a receita nao cobre o pedido inteiro, o que o Bob SABE
     daquele site (as rotas, os nomes dos botoes, o jeito de entrar) vai junto
     com a tarefa para o Codex. Ele deixa de procurar menu na tela e vai direto.

E CONFIGURAVEL SEM PROGRAMAR: cada site e um arquivo .json. Para ensinar uma
tarefa nova, o Diego (ou o proprio cliente) copia um bloco em `tarefas` e
escreve os passos. Nada aqui e chumbado em codigo.

Ordem de busca: `config/receitas/*.json`, alfabetica. O primeiro perfil cujo
`acorda_com` casar com o pedido vence.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_PASTA = Path(__file__).resolve().parent.parent / "config" / "receitas"


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t or "")
                   if unicodedata.category(c) != "Mn").lower()


def carregar_perfis() -> list[dict]:
    """Todos os sites que o Bob conhece. Arquivo quebrado e ignorado, nao derruba."""
    perfis = []
    try:
        arquivos = sorted(_PASTA.glob("*.json"))
    except Exception:
        return perfis
    for arq in arquivos:
        try:
            p = json.loads(arq.read_text("utf-8"))
            if isinstance(p, dict) and p.get("nome"):
                p["_arquivo"] = arq.name
                perfis.append(p)
        except Exception:
            continue
    return perfis


def perfil_para(tarefa: str, titulo_janela: str = "") -> dict | None:
    """O site deste pedido — pelo que a pessoa falou ou pela janela aberta.

    `nao_acorda_com` existe por um erro real (03/09/2026): o dono pediu
    "pesquisa NA INTERNET quantos litros de OLEO pega um Corolla", e o perfil do
    Simplo acordou pela palavra "oleo" — que na tabela de traducao vira o menu
    LUBRITEC. O Bob abriu o Simplo em vez de pesquisar. Palavra que aponta para
    FORA do sistema (internet, google, youtube) desliga o perfil inteiro.
    """
    alvo = _sem_acento(tarefa) + " || " + _sem_acento(titulo_janela)
    for p in carregar_perfis():
        if any(_sem_acento(str(x)) in _sem_acento(tarefa)
               for x in (p.get("nao_acorda_com") or [])):
            continue
        for gatilho in (p.get("acorda_com") or []):
            if _sem_acento(str(gatilho)) in alvo:
                return p
        dominio = _sem_acento(str(p.get("dominio") or ""))
        if dominio and dominio in _sem_acento(titulo_janela):
            return p
    return None


def dicas_para_o_codex(tarefa: str, titulo_janela: str = "") -> str:
    """Bloco de texto que vai junto com a tarefa quando o site e conhecido."""
    p = perfil_para(tarefa, titulo_janela)
    if not p:
        return ""
    linhas = [f"SITE CONHECIDO: {p['nome']} ({p.get('url_base') or p.get('dominio') or ''})"]
    rotas = p.get("rotas") or {}
    if rotas:
        linhas.append("Enderecos diretos (use abrir_url em vez de procurar menu na tela):")
        for caminho, para_que in rotas.items():
            linhas.append(f"  {(p.get('url_base') or '').rstrip('/')}{caminho} — {para_que}")
    login = p.get("login") or {}
    if login:
        linhas.append(
            f"Se cair na tela de login: campo \"{login.get('campo_email', 'E-mail')}\", "
            f"campo \"{login.get('campo_senha', 'Senha')}\", botao \"{login.get('botao', 'ENTRAR')}\". "
            f"Use credencial com apelido \"{p.get('cofre') or p['nome'].lower()}\" "
            f"(campo usuario e campo senha) — voce nunca ve o valor.")
    for dica in (p.get("dicas") or []):
        linhas.append(str(dica))
    return "\n".join(linhas)


def _janela_e_do_site(perfil: dict, titulo_janela: str) -> bool:
    """A janela na frente e mesmo a do sistema deste perfil?"""
    t = _sem_acento(titulo_janela)
    if not t:
        return False
    marcas = [perfil.get("nome", ""), perfil.get("dominio", "")]
    marcas += [str(x) for x in (perfil.get("titulos") or [])]
    return any(_sem_acento(m).split(".")[0] in t for m in marcas if m)


def _casar_tarefa(perfil: dict, tarefa: str, titulo_janela: str = ""):
    """Acha a receita deste pedido e os dados que ela precisa (placa, nome...).

    A MAIS ESPECIFICA GANHA, nao a primeira da lista. "abre o simplo e adiciona
    a placa ABC1D23" casa com duas receitas: "abrir o Simplo" e "consultar
    placa". A segunda e a que o dono quer — e da para saber qual e sem
    adivinhar: ela CAPTUROU um dado (a placa). Entre duas que capturam, vence a
    que casou o trecho mais longo.
    """
    candidatas = []
    for t in (perfil.get("tarefas") or []):
        padrao = t.get("quando")
        if not padrao:
            continue
        # `exige_janela`: a receita so vale com o sistema JA aberto na frente.
        # "abrir a categoria oleo" so faz sentido dentro do Simplo; solta, ela
        # sequestrava pedidos que nada tinham a ver (o caso do "pesquisa na
        # internet quantos litros de oleo").
        if t.get("exige_janela") and not _janela_e_do_site(perfil, titulo_janela):
            continue
        try:
            m = re.search(padrao, tarefa, re.I)
        except re.error:
            continue
        if m:
            candidatas.append((int(t.get("prioridade") or 0),
                               len([v for v in (m.groupdict() or {}).values() if v]),
                               len(m.group(0)), t, m))
    if not candidatas:
        return None, {}
    # PRIORIDADE primeiro, e por um motivo pratico: "adicione a placa ORP8F38 e
    # abra o manual motores" casa com as duas receitas, e a que tem de rodar e a
    # da PLACA (sem o veiculo carregado nao ha manual nenhum para abrir). O
    # resto do pedido a visao termina. Empatou na prioridade, ganha quem
    # capturou mais dados; empatou de novo, o trecho mais longo.
    candidatas.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
    _prio, _quantos, _tamanho, tarefa_escolhida, m = candidatas[0]
    dados = {k: (v or "") for k, v in (m.groupdict() or {}).items()}
    # A fala vira texto solto: "placa A B C 1 D 23", "codigo P 0300". Dado de
    # sistema nao tem espaco — limpa antes de digitar, senao a busca do site
    # nao acha nada e parece defeito do Bob.
    for chave in ("placa", "codigo", "chassi", "documento"):
        if dados.get(chave):
            dados[chave] = re.sub(r"[\s.\-]", "", dados[chave]).upper()
    # DE-PARA do jeito que a pessoa fala para o que esta ESCRITO na tela:
    # "abre a injecao dele" -> clicar em "INJEÇÃO ELETRÔNICA". Sem isto o Bob
    # procuraria a palavra "injecao" e nao acharia botao nenhum.
    for grupo, mapa in (perfil.get("traduzir") or {}).items():
        valor = _sem_acento(dados.get(grupo) or "")
        if not valor or not isinstance(mapa, dict):
            continue
        # O mais especifico primeiro: "injecao diesel" antes de "injecao".
        for termo in sorted(mapa, key=len, reverse=True):
            if _sem_acento(termo) in valor or valor in _sem_acento(termo):
                dados[grupo] = str(mapa[termo])
                break
    return tarefa_escolhida, dados


def _tarefa_por_nome(perfil: dict, nome: str):
    for t in (perfil.get("tarefas") or []):
        if str(t.get("nome") or "") == nome:
            return t
    return None


def receita_para(tarefa: str, titulo_janela: str = ""):
    """(passos ja preenchidos, nome da receita, o que fazer depois) ou (None, "", "").

    UMA FRASE, DUAS RECEITAS (03/09/2026). O dono fala "pesquisa a placa ORP8F38
    e o manual de motores" — que e a placa E a categoria, na ordem. Antes so a
    primeira virava receita e o resto ficava para a visao adivinhar; ela abria a
    pagina certa e depois saia clicando em qualquer coisa parecida. Com
    `depois_tambem`, os passos da segunda entram no fim dos da primeira e o
    caminho inteiro fica determinado.
    """
    perfil = perfil_para(tarefa, titulo_janela)
    if not perfil:
        return None, "", ""
    receita, dados = _casar_tarefa(perfil, tarefa, titulo_janela)
    if not receita:
        return None, "", ""
    valores = {"url_base": (perfil.get("url_base") or "").rstrip("/"),
               "cofre": perfil.get("cofre") or perfil["nome"].lower()}
    for k, v in dados.items():
        valores[k] = (v or "").strip()

    # A segunda receita da frase, quando existe.
    passos_extra = []
    seguinte = _tarefa_por_nome(perfil, str(receita.get("depois_tambem") or ""))
    if seguinte is not None:
        try:
            m2 = re.search(seguinte.get("quando") or "", tarefa, re.I)
        except re.error:
            m2 = None
        if m2:
            dados2 = {k: (v or "") for k, v in (m2.groupdict() or {}).items()}
            for grupo, mapa in (perfil.get("traduzir") or {}).items():
                valor = _sem_acento(dados2.get(grupo) or "")
                if not valor or not isinstance(mapa, dict):
                    continue
                for termo in sorted(mapa, key=len, reverse=True):
                    if _sem_acento(termo) in valor or valor in _sem_acento(termo):
                        dados2[grupo] = str(mapa[termo])
                        break
            valores.update({k: (v or "").strip() for k, v in dados2.items() if v})
            passos_extra = list(seguinte.get("passos") or [])

    passos = []
    for passo in list(receita.get("passos") or []) + passos_extra:
        novo = {}
        for chave, valor in passo.items():
            if isinstance(valor, str):
                try:
                    novo[chave] = valor.format(**valores)
                except (KeyError, IndexError, ValueError):
                    novo[chave] = valor
            else:
                novo[chave] = valor
        # Passo com dado vazio ("{placa}" sem placa no pedido) e passo furado:
        # melhor deixar a visao resolver do que digitar vazio no sistema.
        if novo.get("acao") in ("digitar", "clicar_nome", "esperar_elemento"):
            texto = str(novo.get("texto") or novo.get("nome") or "")
            if not texto.strip() or "{" in texto:
                return None, "", ""
        passos.append(novo)
    return passos, str(receita.get("nome") or ""), str(receita.get("depois") or "visao")
