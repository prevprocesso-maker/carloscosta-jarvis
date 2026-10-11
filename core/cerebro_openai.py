# -*- coding: utf-8 -*-
"""O cerebro pela API da OpenAI — modelo Luna 5.6, esforco baixo.

Por que existe (31/08/2026)
---------------------------
A chave da Gemini do cliente e do nivel gratuito e acaba no meio do dia. Quando
ela morre, o JARVIS passa a pensar por AQUI, na conta do Diego, e o cliente nem
percebe. Passadas as 24 horas do Google, ele volta sozinho para a chave dele.

REGRA FECHADA PELO DONO, NAO MUDAR SEM ELE PEDIR:

    modelo ............ gpt-5.6-luna
    esforco ........... low

E o mais barato e o mais rapido da familia. Em 31/08/2026 ele foi explicito:
*"e obrigatorio usar apenas Luna no esforco leve"*. Com esforco baixo o modelo
nao queima fichas de raciocinio (medido: `reasoning_tokens: 0`), que sao as
caras.

## Por que /v1/responses e nao /v1/chat/completions

Tentei primeiro o chat/completions e a propria API recusou:

    "Function tools with reasoning_effort are not supported for gpt-5.6-luna
     in /v1/chat/completions. To use function tools, use /v1/responses"

Ou seja: com este modelo, ferramenta e esforco baixo **so convivem no
/v1/responses**. Medido la: 1,5 s com ferramenta na mesa.

O formato de ferramenta muda um pouco — o chat usa
`{"type":"function","function":{...}}` e o responses usa o miolo achatado. A
traducao esta no `_achatar` aqui embaixo.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://api.openai.com/v1/responses"

# NAO TROCAR. Ver o cabecalho deste arquivo.
MODELO = "gpt-5.6-luna"
# `low` -> `none` em 03/09/2026, medido no Bob (A/B alternado, 8 pares):
#     low .... 4,76 s por turno com ferramenta | 13 fichas de raciocinio
#     none ... 4,08 s por turno                |  0 fichas
# A escolha de ferramenta nao piorou: 8 de 8 nos dois. As 13 fichas do `low`
# nao compravam nada. `minimal` NAO existe neste modelo (400) — a lista aceita
# e none/low/medium/high/xhigh/max.
ESFORCO = "none"


# ── A CONEXAO FICA VIVA ENTRE UMA PERGUNTA E OUTRA (07/09/2026) ───────────
#
# Queixa do Diego: *"tem hora que ele ta demorando de cinco a seis segundos pra
# responder... se voce puder acelerar, sem prejudicar"*.
#
# O `urlopen` abre uma conexao NOVA a cada chamada, e cada conexao nova paga o
# aperto de mao TLS com a OpenAI. Medido aqui, A/B alternado em 5 pares (o
# unico jeito que vale, ver a memoria de medicao):
#
#     urlopen ........ media 1,25 s   mediana 1,17 s
#     conexao viva ... media 0,81 s   mediana 0,80 s
#     ganho .......... 0,44 s POR CHAMADA
#
# E o ganho se multiplica: um turno em que o modelo chama uma ferramenta faz
# duas ou tres chamadas seguidas — ali sao 0,9 a 1,3 s a menos.
#
# E a mesma pedra que ja tinha sido achada na VOZ em 05/09 ("o vilao era o
# aperto de mao TLS de cada fala"): so que la foi consertado e aqui, no
# cerebro, tinha ficado.
#
# `requests` cuida do pool de conexoes (urllib3), que e seguro entre threads
# desde que ninguem mexa na sessao depois de criada — e ninguem mexe. Se o
# `requests` faltar, cai no `urlopen` de antes e nada quebra.
_SESSAO = None


def _sessao():
    global _SESSAO
    if _SESSAO is None:
        try:
            import requests  # noqa: PLC0415
            _SESSAO = requests.Session()
        except Exception:
            _SESSAO = False          # nao ha requests: segue no urlopen
    return _SESSAO or None


def _postar(chave: str, corpo: dict, timeout: float) -> dict:
    """Manda o pedido e devolve o JSON. Erro de HTTP vira RuntimeError com a
    mensagem da propria OpenAI — e ela que diz "sem credito" ou "chave ruim"."""
    dados = json.dumps(corpo).encode()
    cabeca = {"Authorization": f"Bearer {chave}", "Content-Type": "application/json"}
    ses = _sessao()
    if ses is not None:
        r = ses.post(URL, data=dados, headers=cabeca, timeout=timeout)
        if r.status_code >= 400:
            detalhe = ""
            try:
                detalhe = (r.json().get("error", {}) or {}).get("message", "")
            except Exception:
                detalhe = r.text[:180]
            raise RuntimeError(f"OpenAI {r.status_code}: {str(detalhe)[:180]}")
        return r.json()
    req = urllib.request.Request(URL, data=dados, headers=cabeca)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detalhe = ""
        try:
            detalhe = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            pass
        raise RuntimeError(f"OpenAI {e.code}: {detalhe[:180]}") from e


def _achatar(ferramentas_openai: list) -> list:
    """Formato do chat/completions -> formato do /v1/responses.

    O primeiro embrulha a ferramenta em {"type":"function","function":{...}};
    o segundo quer nome, descricao e parametros no mesmo nivel.
    """
    saida = []
    for f in ferramentas_openai or []:
        corpo = f.get("function") if isinstance(f, dict) else None
        if not corpo or not corpo.get("name"):
            continue
        saida.append({
            "type": "function",
            "name": corpo["name"],
            "description": corpo.get("description") or "",
            "parameters": corpo.get("parameters") or {"type": "object",
                                                      "properties": {}},
        })
    return saida


class CerebroOpenAI:
    """Mesmo contrato dos outros cerebros do JARVIS."""

    def __init__(self, chave: str | None = None) -> None:
        self.chave = (chave or self._chave_do_config() or "").strip()
        if not self.chave:
            raise RuntimeError("Chave da OpenAI ausente em config/api_keys.json")

    @staticmethod
    def _chave_do_config() -> str:
        caminho = Path(__file__).resolve().parents[1] / "config" / "api_keys.json"
        try:
            return str(json.loads(caminho.read_text(encoding="utf-8"))
                       .get("openai_api_key") or "")
        except Exception:
            return ""

    @staticmethod
    def _entrada(mensagens: list) -> list:
        """Mensagens no formato do JARVIS -> itens de entrada do /v1/responses.

        As respostas de ferramenta de turnos ANTIGOS viram texto simples: elas
        so servem para o modelo saber o que ja aconteceu, e remontar o par
        chamada/resposta de um turno passado nao muda o que ele responde agora.
        """
        itens = []
        for m in mensagens or []:
            papel = m.get("role")
            texto = m.get("content")
            if papel == "tool":
                itens.append({"role": "user",
                              "content": f"[resultado da ferramenta] {str(texto or '')[:2000]}"})
            elif texto:
                itens.append({"role": papel or "user", "content": str(texto)})
        return itens

    def _chamar(self, entrada: list, ferramentas: list, forcar: bool,
                timeout: float) -> list:
        corpo = {
            "model": MODELO,
            "reasoning": {"effort": ESFORCO},
            "input": entrada,
            "max_output_tokens": 700,
        }
        if ferramentas:
            corpo["tools"] = ferramentas
            # `required` obriga a chamar alguma coisa. So quando a trava
            # anti-mentira esta cobrando — senao ele pediria ferramenta ate
            # para "bom dia".
            corpo["tool_choice"] = "required" if forcar else "auto"
        dados = _postar(self.chave, corpo, timeout)
        return dados.get("output") or []

    def escrever_longo(self, instrucao: str, pedido: str, max_tokens: int = 9000,
                       timeout: float = 120.0) -> str:
        """Texto LONGO sem ferramenta (codigo inteiro, documento, plano de desenho).

        O `responder` tem teto de 700 fichas de saida porque e conversa falada;
        um programa de calculadora passa de 1.500. Aqui o teto e outro.

        4.000 -> 9.000 EM 07/09/2026, e o motivo esta no registro do Diego:
        `a segunda passada veio mais pobre (0 contra 58); fiquei com a primeira`
        — duas vezes seguidas. Um plano de desenho com 84 formas passa de 4.600
        fichas; a resposta era cortada no meio, o JSON nao fechava e a critica
        virava zero. Ou seja: a melhor parte do desenho estava sendo jogada
        fora em silencio por um teto que ninguem tinha revisado desde que ele
        servia so para codigo. Teto de saida so custa o que a resposta usar.
        """
        corpo = {
            "model": MODELO,
            "reasoning": {"effort": ESFORCO},
            "input": [{"role": "system", "content": str(instrucao)},
                      {"role": "user", "content": str(pedido)}],
            "max_output_tokens": int(max_tokens),
        }
        dados = _postar(self.chave, corpo, timeout)
        return self._texto_da_saida(dados.get("output") or [])

    @staticmethod
    def _texto_da_saida(saida: list) -> str:
        partes = []
        for item in saida:
            if item.get("type") == "message":
                for c in (item.get("content") or []):
                    if c.get("type") in ("output_text", "text"):
                        partes.append(str(c.get("text") or ""))
        return "".join(partes).strip()

    def responder(self, mensagens: list, ferramentas_openai: list, executor,
                  forcar_ferramenta: bool = False,
                  timeout: float = 60.0) -> dict:
        """Devolve {"texto": ..., "chamou_ferramenta": bool}.

        Teto de 3 rodadas, igual aos outros cerebros: sem ele, um modelo confuso
        fica pedindo ferramenta para sempre e o dono espera calado.
        """
        entrada = self._entrada(mensagens)
        ferramentas = _achatar(ferramentas_openai)
        chamou = False
        texto = ""
        forcar = bool(forcar_ferramenta)

        for _ in range(3):
            saida = self._chamar(entrada, ferramentas, forcar, timeout)
            pedidos = [i for i in saida if i.get("type") == "function_call"]
            if not pedidos:
                texto = self._texto_da_saida(saida)
                break

            chamou = True
            forcar = False        # ja chamou; a proxima rodada pode falar
            # DEVOLVER A SAIDA INTEIRA, nao so as chamadas.
            #
            # O modelo emite um item `reasoning` junto com a `function_call`, e
            # a API exige os dois de volta: mandando so a chamada ela recusa com
            # "function_call was provided without its required 'reasoning' item"
            # (medido em 31/08/2026). Como o esforco e baixo, esse item vem
            # praticamente vazio — nao custa fichas, so precisa estar la.
            entrada.extend(saida)
            for pedido in pedidos:
                try:
                    args = json.loads(pedido.get("arguments") or "{}")
                except Exception:
                    args = {}
                try:
                    res = (executor(pedido.get("name") or "", args)
                           if executor else "Ferramenta indisponivel.")
                except Exception as exc:
                    res = f"A ferramenta falhou: {str(exc)[:160]}"
                entrada.append({"type": "function_call_output",
                                "call_id": pedido.get("call_id"),
                                "output": str(res)[:6000]})

        # Chamou a ferramenta e nao escreveu nada: o dono ficaria ouvindo
        # silencio depois de a tarefa ter sido feita.
        if chamou and not texto:
            texto = "Feito."
        return {"texto": texto, "chamou_ferramenta": chamou}
