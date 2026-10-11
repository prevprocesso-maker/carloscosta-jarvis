# -*- coding: utf-8 -*-
"""A fala do JARVIS pela API da OpenAI — as vozes do ChatGPT.

Por que existe (31/08/2026)
---------------------------
A voz da Gemini e boa, mas o teto do nivel gratuito e de **100 falas por dia por
modelo**, e o cliente estourava isso em uma tarde. Aqui a conta e por uso, sem
teto diario, e a chave e do Diego (fica na VPS, nunca no PC do cliente).

MEDIDO nesta maquina, mesma frase:

    gemini-2.5-flash-preview-tts .... 1o som em 3,7 s
    gpt-4o-mini-tts (em fluxo) ...... 1o som em 2,1 s

O ganho vem de LER A RESPOSTA ENQUANTO ELA CHEGA. O endpoint devolve o audio em
fluxo; pedindo `response_format: pcm` sai PCM 16 bits, mono, 24 kHz — exatamente
o formato que a fila de audio do JARVIS ja espera, sem conversao nenhuma. O
primeiro pedaco toca enquanto o resto ainda esta sendo gerado.

Modelo: `gpt-4o-mini-tts`, o mais barato da familia de fala.
"""
from __future__ import annotations

import contextlib
import http.client
import json
import ssl
import threading
import urllib.request
from pathlib import Path

from core.velocidade_fala import VELOCIDADE, Acelerador

HOST = "api.openai.com"
CAMINHO_FALA = "/v1/audio/speech"
URL = "https://api.openai.com/v1/audio/speech"


class Conexao:
    """Uma conexao HTTPS aberta com a OpenAI, reaproveitada entre as falas.

    Por que existe (02/09/2026)
    ---------------------------
    Cada fala abria uma conexao nova, e abrir custa o aperto de mao TLS inteiro
    antes de o servidor sequer ver o pedido. MEDIDO nesta chave, cinco frases
    seguidas, tempo ate o PRIMEIRO pedaco de audio:

        conexao nova a cada fala .... 1,20 s   (2,12  0,75  1,56  0,89  0,69)
        conexao viva ............... 0,65 s   (0,57  0,68  0,66  0,71  0,64)

    Quase metade — e, o que importa tanto quanto, sem os picos de 1,5 e 2,1 s.
    O pico era o que fazia o JARVIS parecer travado de vez em quando; a media
    escondia isso.

    Uma so, com trava: o JARVIS fala uma frase por vez, e duas threads usando o
    mesmo cano ao mesmo tempo embaralhariam as respostas. A trava e segurada
    ate o audio TERMINAR de ser lido, porque a conexao so serve de novo depois
    que o corpo anterior saiu inteiro.

    Se ela morrer (o servidor fecha conexao parada, a rede cai), a proxima
    chamada reabre sozinha e ninguem fica sabendo.
    """

    _trava = threading.Lock()
    _con = None

    @classmethod
    def _abrir(cls):
        con = http.client.HTTPSConnection(HOST, timeout=90,
                                          context=ssl.create_default_context())
        con.connect()
        return con

    @classmethod
    def _descartar(cls) -> None:
        try:
            if cls._con is not None:
                cls._con.close()
        except Exception:
            pass
        cls._con = None

    @classmethod
    def aquecer(cls) -> bool:
        """Abre a conexao ANTES da primeira fala.

        Sem isto a primeira frase do dia ainda pagaria o aperto de mao — e a
        primeira e justo a que ele repara, porque acabou de abrir o programa.

        SO NO PC DE QUEM TEM CHAVE LOCAL. No PC do cliente a fala sai pela VPS
        e esta conexao nunca seria usada: abrir seria gastar um soquete com a
        OpenAI para nada, num programa que ja fica com o dele de pe.
        """
        if not VozOpenAI._chave_do_config():
            return False
        with cls._trava:
            if cls._con is not None:
                return True
            try:
                cls._con = cls._abrir()
                return True
            except Exception as exc:
                print(f"[JARVIS] nao aqueci a conexao da voz: {str(exc)[:90]}")
                cls._con = None
                return False

    @classmethod
    @contextlib.contextmanager
    def resposta(cls, caminho: str, corpo: bytes, cabecalho: dict):
        """Entrega a resposta aberta, pronta para ler em pedacos."""
        cls._trava.acquire()
        limpo = False
        try:
            erro = None
            r = None
            for tentativa in (1, 2):
                if cls._con is None:
                    try:
                        cls._con = cls._abrir()
                    except Exception as exc:
                        erro = exc
                        break
                try:
                    cls._con.request("POST", caminho, body=corpo, headers=cabecalho)
                    r = cls._con.getresponse()
                    break
                except Exception as exc:
                    # Conexao morta (o servidor fechou a parada). Reabre e tenta
                    # de novo UMA vez; duas ja seria mascarar problema de rede.
                    erro = exc
                    cls._descartar()
            if r is None:
                cls._descartar()
                raise erro or RuntimeError("nao consegui falar com a OpenAI")
            if r.status >= 400:
                detalhe = ""
                try:
                    detalhe = r.read().decode("utf-8", "ignore")[:180]
                except Exception:
                    pass
                cls._descartar()
                limpo = True
                raise RuntimeError(f"OpenAI {r.status}: {detalhe}")
            yield r
            # Esvazia o resto: conexao com corpo pela metade nao serve de novo.
            try:
                r.read()
            except Exception:
                cls._descartar()
            limpo = True
        finally:
            if not limpo:
                cls._descartar()
            cls._trava.release()
MODELO = "gpt-4o-mini-tts"

# VOZ NATURAL (01/09/2026). Ver o cabecalho do modulo.
#
# `gpt-audio-mini` e da familia do modo de voz do ChatGPT: ele INTERPRETA em vez
# de ler. Vai pelo /v1/chat/completions com `modalities: [text, audio]` e
# `stream: true`, entao o primeiro pedaco chega em 1,0 s — igual ao de leitura.
#
# Se ele falhar por qualquer motivo, a fala cai sozinha no modelo de leitura, que
# e mais barato e sempre esteve funcionando. O cliente nunca fica mudo.
URL_NATURAL = "https://api.openai.com/v1/chat/completions"
MODELO_NATURAL = "gpt-audio-mini"

# As vozes do ChatGPT, conferidas uma a uma nesta chave em 31/08/2026 — as treze
# responderam. A descricao e para o cliente escolher de ouvido, na engrenagem.
VOZES = {
    "onyx":    "Masculina, grave e firme. A mais parecida com o JARVIS.",
    "ash":     "Masculina, seca e direta.",
    "echo":    "Masculina, clara e neutra.",
    "verse":   "Masculina, expressiva, com mais emocao.",
    "ballad":  "Masculina, calma e pausada.",
    "cedar":   "Masculina, encorpada.",
    "alloy":   "Neutra, equilibrada. O padrao da OpenAI.",
    "sage":    "Neutra, serena.",
    "fable":   "Neutra, de narrador.",
    "nova":    "Feminina, jovem e animada.",
    "shimmer": "Feminina, suave.",
    "coral":   "Feminina, calorosa.",
    "marin":   "Feminina, tranquila.",
}


# COMO FALAR, nao so o que falar (01/09/2026).
#
# O Diego ouviu a voz e reclamou: "esta robotica, nao tem risada, nao tem o hum,
# nao tem os gestos que o ChatGPT tem". Estava certo — sem instrucao, o modelo
# LE o texto como locutor de telejornal.
#
# O parametro `instructions` do gpt-4o-mini-tts dirige a INTERPRETACAO: ritmo,
# emocao, hesitacao, riso. E de graca, nao conta ficha nenhuma. Nao chega ao
# nivel do modo de voz do ChatGPT (aquele e outro modelo, fala-para-fala), mas
# tira o robo do caminho.
ESTILO_BASE = (
    "Voce e uma PESSOA conversando, nao um locutor lendo. Portugues do Brasil, "
    "sotaque neutro brasileiro. "
    "RITMO: varie a velocidade dentro da frase — acelere no que e detalhe, "
    "desacelere no que importa. Deixe uma micro-pausa antes da informacao "
    "principal, como quem organiza o pensamento na hora. "
    "ENTONACAO: nunca termine todas as frases na mesma nota. Suba no fim das "
    "perguntas, desca no fim das afirmacoes, e deixe algumas frases no ar, sem "
    "fechar. "
    "RESPIRACAO: respire de leve entre as frases, audivel, como pessoa de "
    "verdade faz. "
    "EMOCAO: sorria com a voz quando a noticia for boa, solte uma risadinha "
    "curta quando tiver graca, hesite com um 'hum' curto quando estiver "
    "pensando, e deixe transparecer surpresa, duvida ou alivio conforme o "
    "assunto. "
    "PROIBIDO: tom de telejornal, cadencia igual do comeco ao fim, volume "
    "constante. Se soar como leitura, esta errado. "
    # OS SINAIS QUE O CEREBRO ESCREVE (01/09/2026). O texto agora traz "haha",
    # "hmm", "ahh" de proposito — ver EXPRESSAO_INTERPRETA em core/personas.py.
    # Sem esta instrucao o modelo as vezes LE as letras em vez de rir.
    "SINAIS NO TEXTO, e isto e obrigatorio: 'haha' e uma RISADA de verdade, "
    "solta e curta — nunca leia as letras. 'hmm' e hesitacao de quem pensa. "
    "'ahh', 'ufa' e 'ai' sao suspiro, alivio ou surpresa, com ar saindo. "
    "Reticencias sao pausa de meio segundo. Exclamacao e empolgacao real. "
    "Sorria na voz nas frases de carinho e de brincadeira."
)

# Cada jeito de ser pede uma entrega diferente. A chave e a mesma do
# `core/personas.py`.
ESTILO_POR_JEITO = {
    "brincalhao": "Tom divertido e provocador, quase rindo. Solte a risada quando a piada for sua.",
    "ignorante":  "Tom resmungao e impaciente, como quem responde de mau humor mas responde.",
    "malandro":   "Tom de amigo de bar, malemolente, arrastando as palavras com carisma.",
    "profissional": "Tom sobrio e seguro, sem frieza — profissional que conhece o chefe ha anos.",
    "rigoroso":   "Tom firme e direto, sem rodeio, mas ainda humano.",
    "mordomo":    "Tom cortes e elegante, pausado, de mordomo ingles falando portugues.",
    "amigo":      "Tom proximo e afetuoso, de quem gosta da pessoa do outro lado.",
    "direto":     "Tom objetivo e rapido, sem enfeite, mas natural.",
    "motivador":  "Tom animado e energico, empurrando a pessoa para a frente.",
    "narrador":   "Tom de locutor de futebol: energia alta, voz projetada, acelera no lance e estica a palavra no gol.",
    "amoroso":    "Tom de paquera bem-humorada: voz baixa e sorrindo, pausas de charme, um suspiro aqui e ali.",
    "sarcastico": "Tom irônico, com meio sorriso na voz e enfase nas ironias.",
    "equilibrado": "Tom cordial e natural, de conversa tranquila.",
}


class VozOpenAI:
    """Fala pela API da OpenAI. Mesmo contrato das outras vozes do JARVIS."""

    def __init__(self, voz: str = "onyx", chave: str | None = None,
                 jeito: str = "", natural: bool = False, ponte=None) -> None:
        self.voz = voz if voz in VOZES else "onyx"
        self.jeito = str(jeito or "")
        # DESLIGADA POR PADRAO desde 01/09/2026, e o motivo e grave.
        #
        # A voz "natural" (gpt-audio-mini) e, por baixo, um modelo de CONVERSA.
        # Ele nao le o texto: reage a ele. Medido em tres frases reais:
        #   "te mandei a imagem com o nome Todos circulado"
        #      -> falou "Entendi! Vamos la, vou olhar direitinho a imagem..."
        #   "sua CPU esta em doze por cento"  -> essa saiu certa
        #   "pronto, chefe. Bloco de notas aberto"  -> certa
        # Ou seja: em texto curto ela le, em texto de conversa ela RESPONDE no
        # lugar do JARVIS. O dono ouve uma coisa e o chat mostra outra — e o
        # que ele ouve nem e a resposta certa.
        #
        # Isso nao e questao de estilo, e ERRO DE CONTEUDO. Entre soar um pouco
        # menos solta e dizer outra coisa, fica a que diz a verdade. A de
        # leitura e fiel por construcao, custa menos (2,5 contra 3,1 centavos
        # por minuto) e, com a direcao de interpretacao do `_estilo()`, esta
        # longe do robo que ele ouviu na primeira versao.
        #
        # Para voltar a testar a outra: VozOpenAI(..., natural=True).
        self.natural = bool(natural)
        # O que ela falou de verdade na ultima fala. Comparar com o texto que
        # foi pedido e o que permite o chat mostrar a versao correta.
        self.ultima_transcricao = ""
        # A PONTE E O CAMINHO DO CLIENTE (01/09/2026).
        #
        # No PC do dono existe chave local e a fala vai direto. No PC do cliente
        # nao existe chave nenhuma — de proposito — e a fala vai pela VPS, que
        # tem a chave. Os dois caminhos entregam o mesmo PCM 24 kHz, entao o
        # resto do JARVIS nao percebe diferenca.
        self.ponte = ponte
        self.chave = (chave or self._chave_do_config() or "").strip()
        if not self.chave and self.ponte is None:
            raise RuntimeError("Sem chave da OpenAI e sem ponte para falar")

    def _estilo(self) -> str:
        extra = ESTILO_POR_JEITO.get(self.jeito, "")
        estilo = (ESTILO_BASE + " " + extra).strip()
        try:
            from core.idioma import no_idioma  # noqa: PLC0415
            estilo = no_idioma(estilo)
        except Exception:
            pass
        return estilo

    @staticmethod
    def _acelerando(on_chunk):
        """Envelope que acelera o audio pedaco a pedaco, para os caminhos que
        NAO tem parametro de velocidade: a voz que interpreta (`gpt-audio-mini`)
        e a fala que vem pela VPS, que ainda sai na cadencia natural de la.

        O caminho normal (`_fala_leitura`) nao passa por aqui — la a API ja
        entrega o audio na velocidade certa, que soa melhor.
        """
        acel = Acelerador()

        def entregar(raw: bytes) -> None:
            pronto = acel.empurrar(raw)
            if pronto:
                on_chunk(pronto)

        def fechar() -> None:
            resto = acel.fim()
            if resto:
                on_chunk(resto)

        return entregar, fechar

    @staticmethod
    def _chave_do_config() -> str:
        caminho = Path(__file__).resolve().parents[1] / "config" / "api_keys.json"
        try:
            return str(json.loads(caminho.read_text(encoding="utf-8"))
                       .get("openai_api_key") or "")
        except Exception:
            return ""

    def _corpo_leitura(self, texto: str) -> bytes:
        corpo = json.dumps({
            "model": MODELO,
            "voice": self.voz,
            "input": texto,
            # PCM cru: o mesmo formato da fila de audio (16 bits, mono, 24 kHz).
            # Pedir mp3 obrigaria a decodificar antes de tocar, o que joga fora
            # justamente o ganho de tocar enquanto chega.
            "response_format": "pcm",
            # Dirige a INTERPRETACAO. Sem isto ele le como locutor.
            "instructions": self._estilo(),
            # A CADENCIA, e ela e de verdade (medido em 01/09/2026 nesta chave,
            # a mesma frase): sem `speed` deu 10,85 s de fala, com `speed=1.5`
            # deu 5,82 s. So PEDIR pressa dentro do `instructions` deu 8,55 s —
            # o modelo obedece pouco quando e conversa e obedece sempre quando
            # e parametro. Ver `core/velocidade_fala.py`.
            "speed": float(VELOCIDADE),
        }).encode()
        return corpo

    def _cabecalho(self) -> dict:
        return {"Authorization": f"Bearer {self.chave}",
                "Content-Type": "application/json"}

    def _fala_natural(self, texto: str, on_chunk, timeout: float) -> bool:
        """A voz que interpreta. Devolve True se saiu audio.

        E um modelo de CONVERSA gerando audio, entao a instrucao precisa ser
        dura: ele tem de DIZER o texto, nao responder a ele. Sem isso, ele
        conversa com a propria frase.
        """
        import base64  # noqa: PLC0415
        corpo = json.dumps({
            "model": MODELO_NATURAL,
            "modalities": ["text", "audio"],
            "audio": {"voice": self.voz, "format": "pcm16"},
            "stream": True,
            "messages": [
                # LOCUTOR DE ESTUDIO, e o texto vem DELIMITADO.
                #
                # A primeira tentativa dizia so "leia exatamente" e ele
                # acrescentava: em 01/09/2026 saiu "Hahaha, exato! Trinta e
                # cinco anos... Hum...", e o chat nao batia com a voz. Endurecer
                # o texto ajudou, mas frases que COMECAM parecendo instrucao
                # ("Pronto, chefe...") ainda viravam "Pronto, aqui vai: ...".
                #
                # Com o texto entre <<< e >>> a duvida acaba: o que esta entre as
                # marcas e conteudo, nao ordem. Medido: 4 de 4 identicas.
                {"role": "system", "content":
                 "Voce e um LOCUTOR DE ESTUDIO. A proxima mensagem traz um texto "
                 "entre as marcas <<< e >>>. Sua unica funcao e LER EM VOZ ALTA o "
                 "que esta entre as marcas, palavra por palavra, do inicio ao fim. "
                 "NAO leia as marcas. PROIBIDO acrescentar qualquer palavra antes, "
                 "durante ou depois; proibido interjeicao, risada, hum, ah, opa, "
                 "'aqui vai', 'pronto', comentario ou resposta. O que sair da sua "
                 "boca tem de ser exatamente o texto entre as marcas. "
                 "Agora COMO ler: " + self._estilo()},
                {"role": "user", "content": "<<<" + texto + ">>>"},
            ],
        }).encode()
        cabecalho = self._cabecalho()
        saiu = False
        # A propria API devolve, no meio do fluxo, a TRANSCRICAO do que saiu da
        # boca dela. Guardar isso e o unico jeito honesto de saber se ela leu o
        # texto ou resolveu conversar.
        self.ultima_transcricao = ""
        with Conexao.resposta("/v1/chat/completions", corpo, cabecalho) as r:
            for linha in r:
                s = linha.decode("utf-8", "ignore").strip()
                if not s.startswith("data: "):
                    continue
                if s == "data: [DONE]":
                    break
                try:
                    d = json.loads(s[6:])
                except Exception:
                    continue
                for ch in d.get("choices", []):
                    audio = (ch.get("delta") or {}).get("audio") or {}
                    if audio.get("transcript"):
                        self.ultima_transcricao += audio["transcript"]
                    dados = audio.get("data")
                    if dados:
                        saiu = True
                        on_chunk(base64.b64decode(dados))
        return saiu

    def _fala_leitura(self, texto: str, on_chunk, timeout: float) -> bool:
        """A voz que le. Mais barata, e a reserva quando a natural falha."""
        saiu = False
        with Conexao.resposta(CAMINHO_FALA, self._corpo_leitura(texto),
                              self._cabecalho()) as r:
            while True:
                bloco = r.read(2400)      # 50 ms de audio por pedaco
                if not bloco:
                    break
                saiu = True
                on_chunk(bloco)
        return saiu

    def speak_para_fila(self, texto: str, on_chunk, timeout: float = 60.0) -> None:
        """Manda o texto e devolve o audio em pedacos, conforme ele chega.

        Sincrono de proposito: quem chama e uma thread do modo local, e o laco
        de eventos do JARVIS esta ocupado com microfone e alto-falante.
        """
        texto = str(texto or "").strip()
        if not texto:
            return
        self.ultima_transcricao = ""
        # SEM CHAVE AQUI: quem fala e a VPS. E o caso de todo cliente.
        if not self.chave:
            # A VPS ainda pede a fala sem `speed`, entao o audio chega na
            # cadencia antiga e a pressa e feita aqui, no PC do cliente. Quando
            # a VPS passar a mandar o `speed`, TIRAR este envelope — senao a
            # fala sai acelerada duas vezes.
            entregar, fechar = self._acelerando(on_chunk)
            try:
                segundos = self.ponte.falar_pela_ponte(
                    self.voz, texto, self._estilo(), entregar, timeout=timeout)
            finally:
                fechar()
            if not segundos:
                raise RuntimeError("a ponte de voz nao devolveu audio")
            return
        if self.natural:
            entregar, fechar = self._acelerando(on_chunk)
            try:
                saiu = self._fala_natural(texto, entregar, timeout)
            except Exception as exc:
                # Nunca deixar o dono no silencio por causa disto: cai na
                # leitura, que e mais barata e sempre funcionou.
                saiu = False
                print(f"[JARVIS] voz natural falhou ({str(exc)[:90]}); indo na leitura")
            finally:
                fechar()
            if saiu:
                return
        if not self._fala_leitura(texto, on_chunk, timeout):
            raise RuntimeError("a API de fala nao devolveu audio")

    async def pcm(self, texto: str) -> bytes:
        """PCM inteiro, para quem precisa do audio de uma vez."""
        import asyncio  # noqa: PLC0415
        pedacos: list[bytes] = []
        await asyncio.to_thread(self.speak_para_fila, texto, pedacos.append)
        return b"".join(pedacos)

    async def stream_audio(self, texto: str, on_chunk) -> str:
        """Mesma assinatura da voz da Gemini, para trocar uma pela outra."""
        import asyncio  # noqa: PLC0415
        await asyncio.to_thread(self.speak_para_fila, texto, on_chunk)
        return texto

    async def stream_audio_persistent(self, texto: str, on_chunk) -> str:
        return await self.stream_audio(texto, on_chunk)

    async def warm_persistent_session(self) -> None:
        """Nao ha sessao para aquecer: cada fala e um pedido isolado."""
        return None
