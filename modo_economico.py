# -*- coding: utf-8 -*-
"""
MODO ECONOMICO do JARVIS — a mesma voz Charon, sem streaming de audio pago.

Por que existe
--------------
No modo normal o microfone manda audio para o Google o TEMPO TODO, e a Live API
cobra por segundo de audio nos dois sentidos. Alem do custo, a sessao de voz do
Google morre sozinha de tempos em tempos: foi isso que fez o JARVIS parar de
responder no meio das gravacoes.

Aqui o desenho e outro:

    microfone -> Whisper LOCAL (gratis, o audio nao sai do PC)
              -> modelo de TEXTO (centavos por pergunta)
              -> GeminiCharonVoice  <- A MESMA VOZ CHARON de sempre

Nao existe sessao de conversa aberta, entao nao ha o que cair no meio do video.

Como rodar (com o JARVIS fechado, para nao disputar a chave):
    runtime/python/python.exe modo_economico.py
"""
from __future__ import annotations

import os
import sys
import time
import json
import queue
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# O cache do Whisper nao consegue criar link simbolico sem privilegio de
# administrador nesta maquina; sem isto o download do modelo falha com
# "WinError 1314". Tem de vir ANTES de importar o faster_whisper.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

TAXA_MIC   = 16000
BLOCO      = 640          # 20 ms
MODELO_STT = "small"      # medido nesta maquina: 1,43 s contra 3,08 s do "base"
# Fila do cerebro, medida em 30/08 com a chave gratuita do cliente.
#
# O `flash` devolve "503 high demand" com frequencia no gratuito, e o
# `flash-lite` responde bem e mais rapido — por isso ele vem primeiro.
MODELOS_TXT = ["gemini-2.5-flash-lite", "gemini-3.5-flash-lite", "gemini-2.5-flash"]

# Rede de seguranca: se TODO o Gemini estiver fora, o OpenRouter responde algo
# em vez de o cliente ficar falando sozinho. Nao e o cerebro principal — medi
# os gratuitos deles e nenhum serve: o unico rapido (1,5 s) e um modelo de
# 2,6 B que responde em duas palavras, e os bons levam 15 s ou vazam raciocinio
# em ingles. Serve para nao emudecer, so isso.
MODELOS_SOCORRO = ["liquid/lfm-2.5-2.6b:free", "minimax/minimax-m3:free"]

# ── Portao de voz ───────────────────────────────────────────────────────────
# Aqui o portao NAO e economia de cota (o audio nem sai do PC): ele serve para
# saber QUANDO a frase comecou e terminou, para mandar ao Whisper so a fala.
PISO_DE_VOZ       = 200.0   # medido no microfone desta maquina (sala bate 199)
BLOCOS_PARA_ABRIR = 4       # 4 x 20 ms = 80 ms de som sustentado
SILENCIO_FIM_S    = 1.0     # silencio que encerra a frase
FALA_MAX_S        = 30.0    # teto de seguranca
PREROLL           = 16      # 320 ms guardados antes da primeira silaba


def _carregar_chave() -> str:
    caminho = BASE / "config" / "api_keys.json"
    with open(caminho, encoding="utf-8") as f:
        return json.load(f).get("gemini_api_key", "").strip()


def _lingua() -> str:
    try:
        from core.idioma import lingua_do_ouvido  # noqa: PLC0415
        return lingua_do_ouvido()
    except Exception:
        return "pt"


def _carregar_prompt() -> str:
    try:
        return (BASE / "core" / "prompt.txt").read_text(encoding="utf-8")
    except Exception:
        return "Voce e o JARVIS, assistente pessoal. Responda em portugues do Brasil."


class Ouvido:
    """Escuta o microfone e devolve UMA frase por vez, ja recortada."""

    def __init__(self) -> None:
        from faster_whisper import WhisperModel
        print(f"[STT] carregando Whisper '{MODELO_STT}'...", flush=True)
        t0 = time.monotonic()
        self.modelo = WhisperModel(MODELO_STT, device="cpu", compute_type="int8")
        print(f"[STT] pronto em {time.monotonic() - t0:.1f}s", flush=True)
        self.frases: queue.Queue[tuple[np.ndarray, float]] = queue.Queue()
        self._parar = threading.Event()
        self.mudo = False          # fica True enquanto o JARVIS fala

    # -- captura -------------------------------------------------------------
    def escutar(self) -> None:
        buffer: list[np.ndarray] = []
        preroll: list[np.ndarray] = []
        blocos_altos = 0
        ruido = 0.0
        falando = False
        ultimo_som = 0.0
        comeco = 0.0

        def callback(indata, frames, tinfo, status):
            nonlocal buffer, preroll, blocos_altos, ruido, falando, ultimo_som, comeco
            if self.mudo:
                preroll.clear(); blocos_altos = 0
                return
            bloco = indata.reshape(-1).astype("float32")
            nivel = float((bloco * bloco).mean()) ** 0.5
            agora = time.monotonic()
            limiar = max(ruido * 2.5, PISO_DE_VOZ)

            if nivel >= limiar:
                blocos_altos = min(blocos_altos + 1, 20)
                ultimo_som = agora
            elif nivel < limiar * 0.5:
                blocos_altos = 0
                ruido = ruido * 0.98 + nivel * 0.02 if ruido > 0 else nivel

            if not falando:
                if blocos_altos >= BLOCOS_PARA_ABRIR:
                    falando = True
                    comeco = agora
                    buffer = list(preroll)      # nao perde a primeira silaba
                    preroll.clear()
                else:
                    preroll.append(bloco.copy())
                    if len(preroll) > PREROLL:
                        preroll.pop(0)
                    return

            buffer.append(bloco.copy())
            acabou = (agora - ultimo_som) >= SILENCIO_FIM_S
            estourou = (agora - comeco) >= FALA_MAX_S
            if acabou or estourou:
                falando = False
                if len(buffer) > 15:            # ignora estalo de menos de 0,3 s
                    self.frases.put((np.concatenate(buffer), agora - comeco))
                buffer = []

        with sd.InputStream(samplerate=TAXA_MIC, channels=1, dtype="int16",
                            blocksize=BLOCO, callback=callback):
            print("[MIC] ouvindo — fale à vontade (Ctrl+C encerra)\n", flush=True)
            while not self._parar.is_set():
                time.sleep(0.1)

    def parar(self) -> None:
        self._parar.set()

    # -- transcricao ---------------------------------------------------------
    def transcrever(self, audio: np.ndarray) -> tuple[str, float]:
        t0 = time.monotonic()
        # /32768 porque o microfone entrega int16 e o Whisper espera -1..1
        segs, _ = self.modelo.transcribe(
            audio / 32768.0, language=_lingua(), beam_size=1, vad_filter=True,
            condition_on_previous_text=False,
        )
        texto = " ".join(s.text for s in segs).strip()
        return texto, time.monotonic() - t0


class Cerebro:
    """Modelo de TEXTO. E aqui que mora a economia: texto custa centavos."""

    def __init__(self, chave: str) -> None:
        from google import genai
        from google.genai import types
        self.types = types
        # Teto por tentativa: sem ele o SDK reinsiste sozinho no modelo
        # congestionado e medi 55 s so para descobrir que o `flash` estava fora.
        #
        # ATENCAO ao numero: 10 s e o MINIMO que a API aceita. Eu tinha posto
        # 8 s e TODAS as chamadas voltavam com
        #   400 "Manually set deadline 8s is too short. Minimum allowed is 10s"
        # — ou seja, o teto que eu criei para falhar rapido fazia falhar SEMPRE,
        # e a conversa caia no socorro em todas as frases.
        self.cliente = genai.Client(api_key=chave,
                                    http_options={"timeout": 12000})
        # Chave do OpenRouter, usada so como socorro (ver _socorro).
        try:
            with open(BASE / "config" / "api_keys.json", encoding="utf-8") as f:
                self.chave_or = (json.load(f).get("openrouter_api_key") or "").strip()
        except Exception:
            self.chave_or = ""
        self.historico: list = []
        self.instrucao = (
            _carregar_prompt()
            + "\n\nResponda SEMPRE em portugues do Brasil, em no maximo 3 frases, "
              "em tom de conversa falada — o texto vai ser lido em voz alta."
        )
        try:  # cliente em ingles (24/09/2026): ver core/idioma.py
            from core.idioma import com_idioma, no_idioma  # noqa: PLC0415
            self.instrucao = com_idioma(no_idioma(self.instrucao))
        except Exception:
            pass

    def responder(self, pergunta: str) -> tuple[str, float]:
        t0 = time.monotonic()
        self.historico.append(
            self.types.Content(role="user", parts=[self.types.Part(text=pergunta)]))
        del self.historico[:-12]        # memoria curta: 6 turnos

        cfg = self.types.GenerateContentConfig(
            system_instruction=self.instrucao,
            temperature=0.8,
            max_output_tokens=300,
        )
        # Tenta os modelos em ordem. Uma recusa por congestionamento (503) nao
        # pode derrubar a conversa: o cliente falou, e ele precisa de resposta.
        ultimo_erro = ""
        for tentativa in range(2):
            for modelo in MODELOS_TXT:
                try:
                    r = self.cliente.models.generate_content(
                        model=modelo, contents=self.historico, config=cfg)
                    texto = (r.text or "").strip()
                    if texto:
                        if modelo != MODELOS_TXT[0]:
                            print(f"  [cerebro] usei {modelo} ({ultimo_erro[:40]})")
                        self.historico.append(self.types.Content(
                            role="model", parts=[self.types.Part(text=texto)]))
                        return texto, time.monotonic() - t0
                except Exception as e:
                    ultimo_erro = f"{type(e).__name__}: {str(e)[:400]}"
                    print(f"  [cerebro] {modelo} recusou -> {ultimo_erro}")
                    continue
            time.sleep(1.0)             # deu a volta inteira: respira e repete

        # Gemini inteiro fora: tenta o socorro para nao emudecer.
        texto = self._socorro(pergunta)
        if texto:
            print(f"  [cerebro] socorro do OpenRouter ({ultimo_erro[:40]})")
            self.historico.append(self.types.Content(
                role="model", parts=[self.types.Part(text=texto)]))
            return texto, time.monotonic() - t0

        self.historico.pop()            # nao guarda pergunta sem resposta
        print(f"  [cerebro] todos recusaram — {ultimo_erro}")
        return ("Chefe, o servidor de IA está congestionado agora. "
                "Repete daqui a pouco."), time.monotonic() - t0

    def _socorro(self, pergunta: str) -> str:
        """Ultimo recurso, pelo OpenRouter. Ver MODELOS_SOCORRO."""
        if not self.chave_or:
            return ""
        import json as _json, urllib.request, urllib.error
        for modelo in MODELOS_SOCORRO:
            corpo = _json.dumps({
                "model": modelo,
                "messages": [{"role": "system", "content": self.instrucao[:2000]},
                             {"role": "user", "content": pergunta}],
                "max_tokens": 150, "temperature": 0.8,
            }).encode()
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions", data=corpo,
                headers={"Authorization": "Bearer " + self.chave_or,
                         "Content-Type": "application/json"})
            try:
                r = _json.load(urllib.request.urlopen(req, timeout=20))
                t = (r["choices"][0]["message"]["content"] or "").strip()
                if t:
                    return t
            except Exception:
                continue
        return ""


MODELO_TTS = "gemini-2.5-flash-preview-tts"


class Voz:
    """A Charon de sempre, pelo modelo de TTS DEDICADO.

    Por que nao a Live API
    ----------------------
    O `GeminiCharonVoice` sintetiza abrindo uma sessao Live. Medido em 30/08 na
    chave do cliente: **de 7 a 31 segundos** para a mesma frase curta — a mesma
    instabilidade da Live que atrapalhou a noite inteira. O modelo de TTS
    dedicado e uma chamada normal, sem sessao: medi 5,44 s para 3,05 s de audio,
    sempre parecido. Previsivel vale mais que ocasionalmente rapido.

    E o truque que esconde a espera
    -------------------------------
    A resposta e partida em FRASES. A primeira e sintetizada e ja comeca a
    tocar enquanto a segunda esta sendo gerada. O cliente ouve a voz comecar
    muito antes de a resposta inteira estar pronta.
    """

    def __init__(self, chave: str) -> None:
        from google import genai
        from google.genai import types
        self.types = types
        # 25 s de teto. Sem timeout a chamada pendura e CONGELA a conversa
        # inteira — foi o que aconteceu no primeiro teste. (O minimo aceito
        # pela API e 10 s; abaixo disso ela recusa com 400.)
        self.cliente = genai.Client(api_key=chave,
                                    http_options={"timeout": 25000})
        self.cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"))),
        )

    def _sintetizar(self, texto: str) -> bytes:
        r = self.cliente.models.generate_content(
            model=MODELO_TTS, contents=texto, config=self.cfg)
        return r.candidates[0].content.parts[0].inline_data.data

    @staticmethod
    def _partir(texto: str) -> list[str]:
        """Quebra em frases, juntando as curtas demais para nao picotar."""
        import re
        pedacos = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", texto) if p.strip()]
        saida: list[str] = []
        for p in pedacos:
            if saida and len(saida[-1]) < 40:
                saida[-1] = saida[-1] + " " + p
            else:
                saida.append(p)
        return saida or [texto]

    def falar(self, texto: str) -> None:
        frases = self._partir(texto)
        pronto: queue.Queue = queue.Queue()

        def gerar():
            for f in frases:
                try:
                    pronto.put(self._sintetizar(f))
                except Exception as e:
                    print(f"  [voz] frase falhou: {type(e).__name__}: {str(e)[:90]}")
            pronto.put(None)            # marca o fim

        threading.Thread(target=gerar, daemon=True).start()

        with sd.RawOutputStream(samplerate=24000, channels=1,
                                dtype="int16", blocksize=1024) as saida:
            while True:
                try:
                    # NUNCA esperar para sempre. Se a sintese pendurar, o
                    # cliente tem de voltar a ser ouvido — e a regra que este
                    # projeto aprendeu apanhando: toda espera precisa de saida.
                    pcm = pronto.get(timeout=30)
                except queue.Empty:
                    print("  [voz] a sintese demorou demais; desisti desta fala")
                    break
                if pcm is None:
                    break
                saida.write(pcm)        # toca esta enquanto a proxima e gerada


def _limpar_para_voz(texto: str) -> str:
    """Tira o que nao se le em voz alta (emoji, marcacao de markdown)."""
    import re
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", texto)
    t = re.sub(r"[*_`#]+", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def principal() -> None:
    chave = _carregar_chave()
    if not chave:
        print("Nenhuma chave Gemini em config/api_keys.json."); return

    ouvido = Ouvido()
    cerebro = Cerebro(chave)
    voz = Voz(chave)
    print(f"[VOZ] Charon pelo modelo dedicado ({MODELO_TTS})", flush=True)

    threading.Thread(target=ouvido.escutar, daemon=True).start()

    print("=" * 62)
    print(" MODO ECONOMICO — Whisper local + texto + voz Charon")
    print(" O audio do microfone NAO sai deste computador.")
    print("=" * 62 + "\n")

    try:
        while True:
            audio, dur = ouvido.frases.get()
            try:
                texto, t_stt = ouvido.transcrever(audio)
            except Exception as e:
                print(f"  [ouvido] falhou: {e}")
                continue
            if not texto or len(texto) < 2:
                continue
            print(f"VOCE ({dur:.1f}s de fala): {texto}")

            try:
                resposta, t_llm = cerebro.responder(texto)
            except Exception as e:
                # Rede de seguranca final: NADA pode derrubar o laco. Se cair
                # aqui, o cliente fica falando sozinho — que e exatamente o
                # defeito que este modo veio resolver.
                print(f"  [cerebro] erro inesperado: {type(e).__name__}: {e}")
                continue
            if not resposta:
                continue
            print(f"JARVIS: {resposta}")

            ouvido.mudo = True          # nao escuta a propria voz
            t0 = time.monotonic()
            try:
                voz.falar(_limpar_para_voz(resposta))
            except Exception as e:
                print(f"  [voz] falhou: {type(e).__name__}: {str(e)[:120]}")
            t_voz = time.monotonic() - t0
            time.sleep(0.4)             # rabicho: caixa de som esvaziando
            ouvido.mudo = False

            print(f"  tempos -> ouvir {t_stt:.2f}s | pensar {t_llm:.2f}s | "
                  f"falar {t_voz:.2f}s | TOTAL {t_stt + t_llm + t_voz:.2f}s\n")
    except KeyboardInterrupt:
        ouvido.parar()
        print("\nencerrado.")


if __name__ == "__main__":
    principal()
