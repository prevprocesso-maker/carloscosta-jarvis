"""Voz local do JARVIS — Kokoro em ONNX, sem chave e sem cota.

Por que existe (30/08/2026): a cota gratis da Gemini e por dia e por modelo
(10 pedidos/dia no modelo de fala, medido). Quando ela acaba, o JARVIS emudece.
O `edge-tts` resolvia o limite mas os clientes do Diego recusaram a voz — em
pt-BR ele so tem um homem, o `pt-BR-AntonioNeural`, e soa de maquina.

Kokoro-82M roda **na CPU, offline, sem limite nenhum**. Apache 2.0, e o melhor
MOS entre os locais (4,2) com 82 milhoes de parametros — ordens de grandeza
menor que os concorrentes.

Medido nesta maquina em 30/08/2026:
    carga do modelo   0,9 s (uma vez so, fica na memoria)
    pm_santa / pf_dora  10,3 s de fala em 2,9 s  -> 3,6x o tempo real
    pm_alex             10,3 s de fala em 4,5 s  -> 2,3x o tempo real
Numa resposta curta de 3 s de fala isso da menos de 1 segundo. Roda em PC fraco.

Vozes brasileiras: `pm_alex` e `pm_santa` (homem), `pf_dora` (mulher).

O modelo sai em **24 kHz mono**, que e exatamente o `RECEIVE_SAMPLE_RATE` da
fila de audio do `main.py` — entra sem conversao de taxa.

Usa a versao **ONNX** de proposito: o pacote `kokoro` normal exige Python
3.10 a 3.12 e o runtime daqui e 3.13.7, entao ele nem instala. O `kokoro-onnx`
nao tem essa trava e aproveita o `onnxruntime` que ja estava instalado.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

MODELO_DIR = Path(__file__).resolve().parents[1] / "models" / "kokoro"
MODELO = MODELO_DIR / "kokoro-v1.0.onnx"
VOZES = MODELO_DIR / "voices-v1.0.bin"

TAXA = 24_000

# Misturas de timbre (30/08/2026). O Kokoro guarda cada voz como um vetor de
# estilo, e a media de dois vetores e uma voz nova e valida — o idioma NAO vem
# do vetor, vem da fonetizacao (`lang="pt-br"`). Da para pegar o timbre de uma
# voz britanica mantendo a pronuncia correta do portugues, que e o sotaque do
# mordomo do filme sem estropiar as palavras.
#
# Quanto mais peso na voz britanica, mais o sotaque aparece — e mais as vogais
# do portugues sofrem. 70/30 e o meio-termo seguro.
MISTURAS = {
    "jarvis":        [("pm_santa", 0.7), ("bm_george", 0.3)],
    "jarvis_forte":  [("pm_santa", 0.5), ("bm_george", 0.5)],
    "jarvis_alex":   [("pm_alex",  0.6), ("bm_george", 0.4)],
    "jarvis_daniel": [("pm_santa", 0.5), ("bm_daniel", 0.5)],
}

# Vozes puras que valem a pena para um assistente masculino.
PURAS = ["pm_santa", "pm_alex", "pf_dora",
         "bm_george", "bm_daniel", "bm_lewis", "bm_fable",
         "am_onyx", "am_michael", "am_fenrir"]

# ESCOLHA DO DIEGO (30/08/2026): ele ouviu as 11 amostras e apontou a
# `KOKORO-pm_alex.wav`. Voz brasileira pura, sem mistura — as misturas com
# timbre britanico ele ouviu e disse que soavam iguais.
# As receitas de mistura ficam guardadas em MISTURAS para quem quiser
# experimentar com /vozpadrao, mas o padrao e a que ele escolheu.
# O padrao agora e um PERSONAGEM (ver core/personas.py), nao uma voz crua:
# personagem traz velocidade propria e tem nome que o cliente entende.
# "alex" e o pm_alex a 1.0 — exatamente o que o Diego escolheu.
#
# ELA DEIXOU DE EXISTIR E NINGUEM VIU (consertado em 22/09/2026). O personagem
# "alex" saiu quando os personagens viraram os nomes das vozes da Gemini
# (charon, puck, leda, sulafat...), e esta linha ficou apontando para um nome
# que nao esta em PERSONAGENS, nem em MISTURAS, nem em PURAS. Conferido:
# `'alex' in VozLocal.nomes()` -> False.
#
# Consequencia, que aparecia no pior momento: quando a voz de nuvem falhava e o
# programa caia na voz local de socorro, o Kokoro respondia "Voice alex not
# found" e o dono ficava no SILENCIO — justamente na hora em que a voz de
# verdade ja tinha falhado.
#
# `pm_alex` e a voz que ele escolheu ouvindo as 11 amostras em 30/08; e ela que
# fica aqui, com o nome que existe de verdade.
VOZ_PADRAO = "pm_alex"

# A CADENCIA VEM DE UM LUGAR SO (01/09/2026). O Diego ouviu todas as vozes e
# pediu uma vez e meia mais rapido; o numero mora em `core/velocidade_fala.py`
# porque as vozes da OpenAI e da Gemini precisam do MESMO fator, e espalhar
# isso por quatro arquivos garante que um dia um deles fique para tras.
#
# Aqui o Kokoro acelera de graca, no proprio modelo (parametro `speed`): nao ha
# corte de audio nenhum, ele so gera a fala mais rapida — a melhor qualidade
# possivel entre os tres caminhos.
from core.velocidade_fala import VELOCIDADE as FATOR_GLOBAL  # noqa: E402

VELOCIDADE = 1.0 * FATOR_GLOBAL


class VozLocal:
    """Fala local com Kokoro. Carrega o modelo uma vez e reaproveita."""

    _lock = threading.Lock()
    _kokoro = None          # compartilhado: 325 MB nao merecem duas copias

    def __init__(self, voz: str = VOZ_PADRAO) -> None:
        self.voz = voz
        if not MODELO.exists() or not VOZES.exists():
            raise RuntimeError(
                f"Modelo da voz local ausente em {MODELO_DIR}. "
                "Faltam kokoro-v1.0.onnx e voices-v1.0.bin."
            )

    @classmethod
    def _motor(cls):
        # Carga preguicosa e sob trava: o primeiro uso paga 0,9 s, o resto nao
        # paga nada. Duas threads pedindo ao mesmo tempo carregariam duas vezes.
        with cls._lock:
            if cls._kokoro is None:
                from kokoro_onnx import Kokoro  # noqa: PLC0415
                cls._kokoro = Kokoro(str(MODELO), str(VOZES))
            return cls._kokoro

    def disponivel(self) -> bool:
        try:
            self._motor()
            return True
        except Exception:
            return False

    def vozes(self) -> list[str]:
        try:
            return sorted(self._motor().get_voices())
        except Exception:
            return []

    @classmethod
    def nomes(cls) -> list[str]:
        """Tudo que pode ser pedido: personagens, misturas e vozes puras."""
        try:
            from core.personas import PERSONAGENS  # noqa: PLC0415
            personagens = list(PERSONAGENS.keys())
        except Exception:
            personagens = []
        return personagens + list(MISTURAS.keys()) + PURAS

    # A voz do Kokoro usada quando quem falava era da nuvem e a nuvem falhou.
    # `pm_alex` e a brasileira neutra — a que menos assusta trocando no meio.
    SOCORRO_LOCAL = "pm_alex"
    SOCORRO_LOCAL_F = "pf_dora"

    def _receita(self, nome: str):
        """Resolve um nome para (estilo, velocidade).

        Aceita, nesta ordem: personagem de `personas.py` (que traz velocidade
        propria), mistura local de MISTURAS, ou voz pura do Kokoro.
        """
        try:
            from core.personas import PERSONAGENS  # noqa: PLC0415
        except Exception:
            PERSONAGENS = {}

        personagem = PERSONAGENS.get(nome)
        # PERSONAGEM DE NUVEM NAO TEM VOZ AQUI (05/09/2026). Desde que a lista
        # da engrenagem virou so Gemini, `base` vale "__gemini__" — e isso nao e
        # nome de voz do Kokoro. Sem esta saida o socorro mudo (quando a chave
        # do cliente falha) quebrava com "voz desconhecida" e o JARVIS ficava
        # SEM voz nenhuma, que e o unico jeito de piorar um dia ruim.
        if personagem is not None and str(personagem.get("base", "")).startswith("__"):
            # O socorro segue o SEXO da voz escolhida: `pf_dora` para as
            # femininas, `pm_alex` para as masculinas. Sem isto o dono ouve a
            # voz mudar de sexo no meio da conversa e acha que quebrou.
            if str(personagem.get("sexo") or "m") == "f":
                return self.SOCORRO_LOCAL_F, FATOR_GLOBAL
            return self.SOCORRO_LOCAL, FATOR_GLOBAL
        if personagem is not None:
            # O `vel` do personagem e o tempero dele; o fator global e a
            # cadencia que o dono pediu. Os dois se multiplicam, entao um
            # personagem mais pausado continua mais pausado que os outros.
            return (self._vetor(personagem["base"]),
                    float(personagem.get("vel", 1.0)) * FATOR_GLOBAL)
        if nome in MISTURAS:
            return self._vetor(MISTURAS[nome]), VELOCIDADE
        return nome, VELOCIDADE

    def _vetor(self, base):
        """`base` e um nome de voz do Kokoro ou uma lista de (voz, peso)."""
        if isinstance(base, str):
            return base
        motor = self._motor()
        vetor = None
        for parte, peso in base:
            v = motor.get_voice_style(parte) * peso
            vetor = v if vetor is None else vetor + v
        return vetor

    def pcm(self, texto: str, voz: str | None = None) -> bytes:
        """Devolve PCM 16 bits, mono, 24 kHz — o formato da fila de audio."""
        texto = str(texto or "").strip()
        if not texto:
            return b""
        estilo, velocidade = self._receita(voz or self.voz)
        amostras, taxa = self._motor().create(
            texto, voice=estilo, speed=velocidade, lang="pt-br"
        )
        arr = np.asarray(amostras, dtype=np.float32)
        if taxa != TAXA:
            # Nao deveria acontecer (o modelo e 24 kHz), mas se um dia mudar,
            # reamostra por interpolacao simples em vez de sair rouco.
            alvo = int(len(arr) * TAXA / float(taxa))
            arr = np.interp(
                np.linspace(0, len(arr) - 1, alvo), np.arange(len(arr)), arr
            ).astype(np.float32)
        # Corta o estouro antes de virar inteiro: passar de 1.0 vira chiado.
        arr = np.clip(arr, -1.0, 1.0)
        return (arr * 32767.0).astype(np.int16).tobytes()

    def stream_audio(self, texto: str, on_chunk) -> str:
        """Mesma assinatura das vozes da Gemini, para trocar uma pela outra.

        Devolve o proprio texto como transcricao: e sintetizador puro, le
        exatamente o que recebe, entao nao existe divergencia a conferir.
        """
        bruto = self.pcm(texto)
        if not bruto:
            raise RuntimeError("a voz local devolveu audio vazio")
        for pos in range(0, len(bruto), 2400):
            on_chunk(bruto[pos: pos + 2400])
        return texto

    def salvar_wav(self, texto: str, destino: Path, voz: str | None = None) -> Path:
        import wave  # noqa: PLC0415
        bruto = self.pcm(texto, voz=voz)
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destino), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(TAXA)
            wf.writeframes(bruto)
        return destino
