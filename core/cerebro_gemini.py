# -*- coding: utf-8 -*-
"""Cerebro reserva: Gemini paga, para quando a cota gratis do OpenRouter acaba.

Por que existe (30/08/2026, medido no proprio uso do Diego)
------------------------------------------------------------
A conta do OpenRouter e gratuita: ~50 pedidos por dia. Nesta noite ela acabou no
meio da conversa e o JARVIS **emudeceu** — o microfone ouvia certinho
(`local_ouviu 'Minha voz nao esta saindo la...'`) e logo em seguida vinha
`openrouter_falhou ... free-models-per-day`. Sem cerebro, nao ha resposta.

O Diego pediu a Gemini **so para a voz**. Isto aqui nao muda esse desenho: a
Gemini continua so falando no dia a dia. Ela so pensa quando o OpenRouter ja
morreu e a alternativa e o JARVIS ficar mudo.

Modelo: `gemini-2.5-flash-lite`, o mais barato da lista da chave dele.

As ferramentas do JARVIS ja nascem no formato da Gemini (`TOOL_DECLARATIONS` no
main.py) — o `CerebroOpenRouter` e que as traduz para o formato OpenAI. Aqui a
traducao volta atras.
"""
from __future__ import annotations

import json
from pathlib import Path

from google import genai
from google.genai import types

# FILA DE MODELOS, NAO UM SO (05/09/2026).
#
# Estava fixo em `gemini-2.5-flash-lite` e, com a chave do Diego, o Google passou
# a responder **404: "no longer available to new users"** — o degrau rapido do
# cerebro estava morto ha dias e o sintoma nao dizia isso em lugar nenhum: so
# parecia que "a Gemini nao responde". Nome de modelo APODRECE; e a segunda vez
# que isso morde este projeto (a primeira foi na voz, em 31/08).
#
# Medido com a chave dele, na hora:
#     gemini-3.5-flash-lite ....... 0,78 s   <- o escolhido
#     gemini-flash-lite-latest .... 6,37 s   (funciona, mas frio e lento)
#     gemini-2.5-flash ............ 404, aposentado
#     gemini-flash-latest ......... 503, instavel
#
# A fila anda sozinha quando um cai, e `_indice` guarda quem respondeu para nao
# pagar pedagio na proxima pergunta.
MODELOS = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
]
MODELO = MODELOS[0]
_indice = 0


def _gerar(cliente, **kw):
    """`generate_content` que atravessa modelo aposentado ou fora do ar.

    Passa adiante so no que E do modelo (404 aposentado, 503 fora do ar). Cota
    (429) e erro de chave sobem para quem chamou: trocar de modelo nao resolve
    nenhum dos dois, e mascarar isso ja custou caro aqui.
    """
    global _indice
    ultimo = None
    for passo in range(len(MODELOS)):
        i = (_indice + passo) % len(MODELOS)
        try:
            r = cliente.models.generate_content(model=MODELOS[i], **kw)
            _indice = i
            return r
        except Exception as exc:
            texto = str(exc)
            # 500 ENTROU NA LISTA (05/09/2026). Medido no teste de latencia: a
            # Gemini devolveu `500 INTERNAL` duas vezes em oito perguntas, e
            # cada uma jogava a pergunta no degrau seguinte — 0,9 s viravam
            # 2,7 s. E erro passageiro do servidor, nao da pergunta: tentar de
            # novo no proximo modelo da fila custa menos do que descer o degrau.
            if ("404" in texto or "503" in texto or "500" in texto
                    or "NOT_FOUND" in texto or "INTERNAL" in texto):
                ultimo = exc
                continue
            raise
    raise ultimo or RuntimeError("nenhum modelo da Gemini respondeu")


def _desembrulhar(ferramentas_openai: list) -> list:
    """Formato OpenAI -> declaracao crua da Gemini.

    O caminho de ida (`CerebroOpenRouter.converter_ferramentas`) so embrulha em
    {"type": "function", "function": {...}} e poe os tipos em minuscula. A volta
    desfaz as duas coisas.
    """
    def sobe(no):
        if isinstance(no, dict):
            return {k: (v.upper() if (k == "type" and isinstance(v, str)) else sobe(v))
                    for k, v in no.items()}
        if isinstance(no, list):
            return [sobe(x) for x in no]
        return no

    cruas = []
    for f in ferramentas_openai or []:
        corpo = f.get("function") if isinstance(f, dict) else None
        if not corpo or not corpo.get("name"):
            continue
        crua = {"name": corpo["name"],
                "description": corpo.get("description") or ""}
        params = corpo.get("parameters") or {}
        if params.get("properties"):
            crua["parameters"] = sobe(params)
        cruas.append(crua)
    return cruas


class CerebroGemini:
    """Mesmo contrato do cerebro principal, no modelo barato da Gemini."""

    def __init__(self, chave: str | None = None) -> None:
        if not chave:
            caminho = Path(__file__).resolve().parents[1] / "config" / "api_keys.json"
            chave = str(json.loads(caminho.read_text(encoding="utf-8"))
                        .get("gemini_api_key") or "").strip()
        if not chave:
            raise RuntimeError("Chave Gemini ausente em config/api_keys.json")
        # Prazo de 30 s (01/10/2026): num pico do Google o pedido ficava ate 51 s
        # preso antes de voltar 503, e o degrau seguinte nem chegava a tentar.
        self.client = genai.Client(api_key=chave, http_options={"timeout": 30000})

    @staticmethod
    def _para_conteudo(mensagens: list) -> tuple[str, list]:
        """Mensagens no formato OpenAI -> (instrucao de sistema, conteudos Gemini).

        As mensagens de ferramenta viram texto simples: o histórico serve so para
        o modelo saber o que ja aconteceu, e reconstruir o par
        functionCall/functionResponse de turnos ANTIGOS nao muda a resposta.
        """
        sistema = ""
        conteudos = []
        for m in mensagens or []:
            papel = m.get("role")
            texto = m.get("content")
            if papel == "system":
                sistema = str(texto or "")
                continue
            if papel == "tool":
                conteudos.append(types.Content(
                    role="user",
                    parts=[types.Part(text=f"[resultado da ferramenta] {str(texto or '')[:2000]}")]))
                continue
            if not texto:
                continue
            conteudos.append(types.Content(
                role=("model" if papel == "assistant" else "user"),
                parts=[types.Part(text=str(texto))]))
        return sistema, conteudos

    def escrever_longo(self, instrucao: str, pedido: str, max_tokens: int = 4000) -> str:
        """Texto LONGO sem ferramenta (codigo inteiro). Ver o mesmo no cerebro OpenAI."""
        cfg = types.GenerateContentConfig(
            system_instruction=str(instrucao) or None,
            temperature=0.4,
            max_output_tokens=int(max_tokens),
        )
        resp = _gerar(
            self.client,
            contents=[types.Content(role="user", parts=[types.Part(text=str(pedido))])],
            config=cfg)
        partes = []
        for cand in (resp.candidates or []):
            for p in (getattr(getattr(cand, "content", None), "parts", None) or []):
                t = getattr(p, "text", None)
                if t:
                    partes.append(str(t))
        return "".join(partes).strip()

    def responder(self, mensagens: list, ferramentas_openai: list, executor,
                  forcar_ferramenta: bool = False) -> dict:
        """Devolve {"texto": ..., "chamou_ferramenta": bool}.

        Laco com teto de 3 rodadas, igual ao cerebro principal: sem teto, um
        modelo confuso fica pedindo ferramenta para sempre.
        """
        sistema, conteudos = self._para_conteudo(mensagens)
        cruas = _desembrulhar(ferramentas_openai)
        # FORCAR A CHAMADA (31/08/2026). Quando a trava anti-mentira cobra o
        # modelo, ele costumava pedir desculpa em vez de agir: "opa, me empolguei
        # e pulei a etapa da ferramenta" — e continuava sem fazer nada. Medido em
        # 3 tentativas de "cria um site": so 1 chamou a ferramenta.
        # Com o modo ANY ele NAO PODE responder sem chamar alguma coisa.
        tool_cfg = None
        if forcar_ferramenta and cruas:
            tool_cfg = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY))
        cfg = types.GenerateContentConfig(
            system_instruction=sistema or None,
            tools=([types.Tool(function_declarations=cruas)] if cruas else None),
            tool_config=tool_cfg,
            temperature=0.7,
            max_output_tokens=400,
        )

        chamou = False
        texto = ""
        for _ in range(3):
            resp = _gerar(self.client, contents=conteudos, config=cfg)
            partes = []
            for cand in (resp.candidates or []):
                partes.extend(getattr(getattr(cand, "content", None), "parts", None) or [])

            pedidos = [p.function_call for p in partes if getattr(p, "function_call", None)]
            if not pedidos:
                texto = "".join(str(getattr(p, "text", "") or "") for p in partes).strip()
                break

            chamou = True
            # Solta a obrigacao: a proxima rodada tem de poder FALAR o
            # resultado, senao ele ficaria chamando ferramenta para sempre.
            if tool_cfg is not None:
                tool_cfg = None
                cfg = types.GenerateContentConfig(
                    system_instruction=sistema or None,
                    tools=([types.Tool(function_declarations=cruas)] if cruas else None),
                    temperature=0.7,
                    max_output_tokens=400,
                )
            conteudos.append(types.Content(role="model", parts=[
                types.Part(function_call=fc) for fc in pedidos]))
            respostas = []
            for fc in pedidos:
                try:
                    resultado = (executor(fc.name, dict(fc.args or {}))
                                 if executor else "Ferramenta indisponivel.")
                except Exception as exc:
                    resultado = f"A ferramenta falhou: {str(exc)[:160]}"
                respostas.append(types.Part(function_response=types.FunctionResponse(
                    name=fc.name, response={"resultado": str(resultado)[:6000]})))
            conteudos.append(types.Content(role="user", parts=respostas))

        # CHAMOU A FERRAMENTA E NAO FALOU NADA.
        #
        # Acontecia de verdade: em 01/09/2026 o Diego perguntou a hora, a
        # ferramenta devolveu certo e a resposta saiu "Feito." — o dado estava
        # na mao e o cliente nao ouviu. "Feito" nao responde "que horas sao".
        #
        # Em vez de inventar uma palavra, pergunta de novo SEM ferramenta na
        # mesa: sem ela o modelo nao tem para onde fugir e escreve a frase.
        if chamou and not texto:
            try:
                cfg_texto = types.GenerateContentConfig(
                    system_instruction=sistema or None,
                    temperature=0.7,
                    max_output_tokens=400,
                )
                conteudos.append(types.Content(role="user", parts=[types.Part(
                    text="Agora responda ao dono em UMA frase curta, usando o "
                         "resultado da ferramenta acima. So a frase.")]))
                resp = _gerar(self.client, contents=conteudos, config=cfg_texto)
                partes = []
                for cand in (resp.candidates or []):
                    partes.extend(getattr(getattr(cand, "content", None), "parts", None) or [])
                texto = "".join(str(getattr(p, "text", "") or "") for p in partes).strip()
            except Exception:
                pass
        if chamou and not texto:
            texto = "Feito."
        return {"texto": texto, "chamou_ferramenta": chamou}
