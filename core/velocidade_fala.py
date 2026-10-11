# -*- coding: utf-8 -*-
"""A velocidade da fala do JARVIS — um numero so, para todas as vozes.

Por que existe (01/09/2026)
---------------------------
O Diego ouviu as vozes e disse: "todas estao falando lento, quero uma vez e
meia mais rapido". As vozes vem de TRES motores diferentes e cada um acelera de
um jeito, entao sem um lugar unico o numero se espalharia por quatro arquivos e
na proxima vez que ele pedisse outra cadencia teria de mexer nos quatro.

    Kokoro (as 6 vozes locais) .... parametro `speed` do proprio modelo
    OpenAI (as 4 de estudio) ...... parametro `speed` da API
    Charon (Gemini) ............... nao tem parametro: acelerado aqui, no audio
    voz pela VPS (cliente) ........ idem, acelerado aqui

MEDIDO em 01/09/2026 na API da OpenAI, a mesma frase:

    gpt-4o-mini-tts sem speed .............. 10,85 s de fala
    gpt-4o-mini-tts com speed=1.5 ...........  5,82 s de fala
    gpt-4o-mini-tts so pedindo pressa no texto  8,55 s de fala

Ou seja: PEDIR pressa na instrucao quase nao adianta (1,27x), o parametro
adianta. Por isso o `speed` e usado onde existe, e so onde nao existe e que o
audio e acelerado aqui.

Acelerar o audio NAO e reamostrar. Reamostrar sobe o tom junto e vira voz de
desenho animado — foi o erro que ja tinha sido cometido e abandonado em
`core/personas.py`. Aqui e SOLA (sobreposicao com busca de sincronia): joga
fora pedacos de onda inteiros, sempre no ponto em que a onda se repete, entao a
altura da voz continua a mesma.
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# O NUMERO. Mexer aqui muda TODAS as vozes de uma vez.
# 1.0 = a cadencia natural de cada motor; 1.5 = uma vez e meia mais rapido.
# ---------------------------------------------------------------------------
VELOCIDADE = 1.5

TAXA = 24_000          # todas as vozes do JARVIS saem em 24 kHz mono, 16 bits


class Acelerador:
    """Acelera PCM 16 bits sem mudar o tom, aceitando o audio em pedacos.

    Guarda estado entre as chamadas de proposito: a voz chega em pedacos de
    50 ms e cortar cada pedaco por conta propria deixaria um estalo em cada
    emenda. Com o estado, a emenda cai sempre no ponto certo da onda.
    """

    def __init__(self, fator: float = VELOCIDADE, taxa: int = TAXA) -> None:
        self.fator = max(1.0, float(fator))
        self.taxa  = int(taxa)
        # 30 ms de janela: menor que isso corta a onda da voz masculina grave,
        # maior comeca a soar com eco.
        self.janela = int(self.taxa * 0.030)
        self.meia   = self.janela // 2                       # o tanto que cruza
        self.passo_saida   = self.janela - self.meia
        self.passo_entrada = int(round(self.passo_saida * self.fator))
        # 5 ms de folga para procurar onde a onda se repete.
        self.busca = int(self.taxa * 0.005)
        self._rampa = np.linspace(0.0, 1.0, self.meia, dtype=np.float32)

        self._x     = np.zeros(0, dtype=np.float32)   # o que ainda nao foi lido
        self._p     = 0                               # onde comeca a proxima janela
        self._cauda = None                            # o que espera o cruzamento
        self._fim_da_janela = 0                       # fim absoluto da ultima janela
        self._meio_byte = b""                         # meia amostra do pedaco anterior

    # -- interno ------------------------------------------------------------
    def _melhor_deslocamento(self) -> int:
        """Onde, perto de `_p`, a onda mais se parece com a cauda pendente."""
        if self._cauda is None:
            return 0
        ini = max(0, self._p - self.busca)
        fim = min(len(self._x) - self.janela, self._p + self.busca)
        if fim <= ini:
            return 0
        trecho = self._x[ini: fim + self.meia]
        if len(trecho) < self.meia:
            return 0
        semelhanca = np.correlate(trecho, self._cauda, mode="valid")
        energia = np.sqrt(
            np.convolve(trecho * trecho, np.ones(self.meia, dtype=np.float32),
                        mode="valid")) + 1e-6
        n = min(len(semelhanca), len(energia))
        return int(ini + int(np.argmax(semelhanca[:n] / energia[:n])) - self._p)

    def _moer(self) -> list:
        saida = []
        while True:
            k = self._melhor_deslocamento()
            inicio = self._p + k
            if inicio < 0 or inicio + self.janela > len(self._x):
                break
            f = self._x[inicio: inicio + self.janela]
            if self._cauda is None:
                saida.append(f[: self.janela - self.meia])
            else:
                cruzado = self._cauda * (1.0 - self._rampa) + f[: self.meia] * self._rampa
                saida.append(cruzado)
                if self.janela - self.meia > self.meia:
                    saida.append(f[self.meia: self.janela - self.meia])
            self._cauda = f[self.janela - self.meia:].copy()
            self._fim_da_janela = inicio + self.janela
            self._p = inicio + self.passo_entrada
            # Descarta o que ja passou, guardando a folga da busca.
            corte = max(0, min(self._p, self._fim_da_janela) - self.busca)
            if corte > self.taxa:                 # so mexe quando vale a pena
                self._x = self._x[corte:]
                self._p -= corte
                self._fim_da_janela -= corte
        return saida

    @staticmethod
    def _para_bytes(pedacos: list) -> bytes:
        if not pedacos:
            return b""
        arr = np.concatenate(pedacos)
        arr = np.clip(arr, -32768.0, 32767.0)
        return arr.astype(np.int16).tobytes()

    # -- uso ----------------------------------------------------------------
    def empurrar(self, raw: bytes) -> bytes:
        """Recebe um pedaco de audio e devolve o que ja da para tocar."""
        if not raw:
            return b""
        if self.fator <= 1.001:
            return raw
        # PEDACO COM NUMERO IMPAR DE BYTES (achado em 16/09, consertado em
        # 22/09/2026). O audio e int16: cada amostra sao DOIS bytes. A rede nao
        # promete entregar pedaco de tamanho par, e quando chegava um impar o
        # `np.frombuffer` estourava com "buffer size must be a multiple of
        # element size" — a voz de socorro `onyx` morria no meio e o dono ficava
        # no silencio (no PC do cliente ela so falou 2 de ~45 vezes).
        #
        # O byte que sobra NAO se joga fora: ele e a primeira metade da proxima
        # amostra. Fica guardado e entra na frente do pedaco seguinte.
        if self._meio_byte:
            raw = self._meio_byte + raw
            self._meio_byte = b""
        if len(raw) % 2:
            self._meio_byte = raw[-1:]
            raw = raw[:-1]
            if not raw:
                return b""
        novo = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        self._x = np.concatenate([self._x, novo]) if len(self._x) else novo
        return self._para_bytes(self._moer())

    def fim(self) -> bytes:
        """Fecha a fala: devolve a cauda e o restinho que nao encheu uma janela."""
        if self.fator <= 1.001:
            return b""
        sobra = []
        if self._cauda is not None:
            sobra.append(self._cauda)
            self._cauda = None
        if self._fim_da_janela < len(self._x):
            sobra.append(self._x[self._fim_da_janela:])
        self._x = np.zeros(0, dtype=np.float32)
        self._p = 0
        self._fim_da_janela = 0
        self._meio_byte = b""      # fala nova nao herda meia amostra da anterior
        return self._para_bytes(sobra)


def acelerar_pcm16(raw: bytes, fator: float = VELOCIDADE, taxa: int = TAXA) -> bytes:
    """Acelera um audio INTEIRO. Se algo der errado, devolve o original.

    Nunca deixar o dono no silencio por causa da velocidade: qualquer falha aqui
    cai no audio de sempre, so que na cadencia antiga.
    """
    if not raw or float(fator) <= 1.001:
        return raw
    try:
        a = Acelerador(fator, taxa)
        return a.empurrar(raw) + a.fim()
    except Exception:
        return raw
