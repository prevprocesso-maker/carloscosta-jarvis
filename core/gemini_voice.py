from __future__ import annotations

import asyncio
import json
import threading
import re
import unicodedata
import wave
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types

from core.velocidade_fala import acelerar_pcm16


LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
RECEIVE_SAMPLE_RATE = 24000

# A VOZ PELO MODELO MAIS RAPIDO (08/10/2026). Queixa do Diego: "as vozes,
# principalmente a do JARVIS, estao demorando muito para responder". O registro
# do dia mostrou o 2.5 native-audio entregando o primeiro som em 2 a 7 s e
# ficando mudo por 8 s duas vezes. Medido com a chave dele, mesma voz Charon,
# sessao aberta e 4 frases seguidas (1o pedaco de audio):
#     gemini-2.5-flash-native-audio-preview-12-2025 ... 1,10 a 1,82 s
#     gemini-3.1-flash-live-preview ................... 0,88 a 1,32 s
#     gemini-3.8-live ................................. 0,57 a 0,85 s
# A fila desce sozinha para o seguinte quando um modelo recusa a conexao (e
# fica nele). A ORDEM foi decidida depois — ver logo abaixo.
#
# ...MAS A VOZ PRINCIPAL CONTINUA O 2.5 (08/10/2026, regra do Diego): "tem que
# ser o modelo free da Google, aquele que usa varias horas falando sem dar
# limite". O 2.5 native-audio conta por SESSAO e aguenta o dia de graca (ver a
# memoria da cota gratis); os 3.x nao tem limite gratis publicado. E no teste
# de leitura literal o 3.8 pulou a segunda frase em ate 6 de 8 conexoes novas e
# saiu mudo 2 vezes; o 3.1 travou 4 de 16. Os dois ficam so de RESERVA, para
# quando o 2.5 recusar (cota, fora do ar) — antes de cair no modelo de fala por
# pedido (10 por dia no gratis).
LIVE_MODELOS = [
    LIVE_MODEL,
    "models/gemini-3.1-flash-live-preview",
    "models/gemini-3.8-live",
]


# FRASE QUE SAI PELA METADE (08/10/2026). Medido com o 3.8-live: de vez em
# quando, na PRIMEIRA fala de uma conexao recem-aberta, ele diz so a primeira
# frase ("Pois nao, senhor.") e encerra o turno — o texto do resto chega na
# transcricao, mas sem som. Nas frases seguintes da mesma conexao: 0 cortes em
# 16. Conserto: no fim do turno, se o audio saiu bem mais curto do que o texto
# pede, a parte que ficou muda e mandada de novo, na hora, na mesma conexao.
_LETRAS_POR_S = 15.5
_FRASES = re.compile(r"[^.!?…]+[.!?…]*\s*")


def _o_que_faltou(texto: str, dur: float) -> str:
    """As frases do fim que nao couberam no audio que saiu ("" se saiu tudo)."""
    texto = str(texto or "").strip()
    if not texto:
        return ""
    # TURNO SEM SOM NENHUM (08/10/2026, 15:32 no PC do Diego): o 2.5 fechou o
    # turno sem mandar audio, nas DUAS conexoes. A primeira versao desta conta
    # supunha que pelo menos a 1a frase tinha saido e mandava so o fim — o dono
    # ouviu so "Ces tao maneiros, chefia!". Sem som, repete a fala INTEIRA.
    if dur < 0.3:
        return texto
    esperado = len(texto) / _LETRAS_POR_S
    if esperado < 1.6 or dur >= 0.55 * esperado:
        return ""
    frases = [f.strip() for f in _FRASES.findall(texto) if f.strip()]
    if len(frases) < 2:
        return ""
    acum, ditas = 0.0, 0
    for f in frases:
        acum += len(f) / _LETRAS_POR_S
        if acum <= dur * 1.25 + 0.3:
            ditas += 1
        else:
            break
    return " ".join(frases[ditas:]) if ditas < len(frases) else ""


# COTA DO AO-VIVO (08/10/2026). Depois de um dia inteiro de testes, o Google
# passou a responder "1011 You exceeded your current quota" na conexao — e o
# JARVIS reconectava na hora, varias vezes seguidas, o que so gasta mais. Viu
# cota, a voz ao vivo descansa `_DESCANSO_COTA_S` sem abrir conexao nenhuma (a
# resposta sai escrita nesse meio tempo) e depois tenta de novo sozinha.
# 30 -> 5 s no mesmo dia: o 1011 que apareceu depois era de sessao antiga ainda
# contada pelo Google (logo depois de reabrir o JARVIS), e 30 s de descanso
# deixaram duas falas escritas. 5 s ja impedem a reconexao em rajada.
_DESCANSO_COTA_S = 5.0
_cota_ate = [0.0]


def _e_cota(exc) -> bool:
    t = f"{type(exc).__name__} {exc}".lower()
    return ("exceeded your current quota" in t or "resource_exhausted" in t
            or ("1011" in t and "quota" in t) or " 429" in t)


def _anotar_cota(exc) -> None:
    import time as _t  # noqa: PLC0415
    _cota_ate[0] = _t.monotonic() + _DESCANSO_COTA_S
    try:
        from main import _voice_diag  # noqa: PLC0415
        _voice_diag("voz_cota_descansando",
                    f"{_DESCANSO_COTA_S:.0f}s sem abrir conexao ({str(exc)[:70]})")
    except Exception:
        pass


# FECHAR AS SESSOES AO SAIR (08/10/2026). O fechamento do JARVIS mata o
# processo na hora (ver `_encerrar_tudo` no main) e as sessoes ao vivo ficavam
# contando do lado do Google por alguns minutos: reabrir o JARVIS logo depois
# dava "You exceeded your current quota". Toda conexao se registra aqui, e o
# fechamento despede todas (teto de 1 s) antes de sair.
import weakref as _weakref
_VIVAS = _weakref.WeakSet()


def fechar_todas_as_sessoes(prazo: float = 1.0) -> int:
    import time as _t  # noqa: PLC0415
    futuros = []
    for v in list(_VIVAS):
        if getattr(v, "_persistent_session", None) is None:
            continue
        try:
            futuros.append(asyncio.run_coroutine_threadsafe(
                v._reset_persistent_session(), v._garantir_laco()))
        except Exception:
            pass
    fim = _t.monotonic() + float(prazo)
    for f in futuros:
        try:
            f.result(timeout=max(0.05, fim - _t.monotonic()))
        except Exception:
            pass
    return len(futuros)


def _palavras(s: str) -> list:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+", s)


def _faltou_pela_transcricao(texto: str, dito: str) -> str:
    """As frases do FIM que a voz nao falou, comparando o texto pedido com a
    transcricao do audio que saiu (o 2.5 transcreve o que ele realmente disse).

    Por que (08/10/2026, 16:15): a fala parou em "Na moral," e faltou "o Peixe
    vai ter que entrar com tudo pra cima." — 13 s de audio para ~16 esperados,
    acima da conta de tamanho (55%). Pelo texto da pra ver EXATAMENTE o que
    faltou. So conta como faltando o RABO: uma frase do meio que ele falou de
    outro jeito ("10" x "dez") nao pode virar repeticao."""
    frases = [f.strip() for f in _FRASES.findall(str(texto or "")) if f.strip()]
    ditas = _palavras(dito)
    if len(frases) < 2 or len(ditas) < 3:
        return ""

    def cobertura(frase: str, inicio: int):
        pal = _palavras(frase)
        if not pal:
            return 1.0, inicio
        pos, achou = inicio, 0
        for w in pal:
            for j in range(pos, min(len(ditas), pos + 6)):
                if ditas[j] == w:
                    achou += 1
                    pos = j + 1
                    break
        return achou / len(pal), pos

    pos, ultima_dita = 0, -1
    marcas = []
    for i, f in enumerate(frases):
        c, novo = cobertura(f, pos)
        marcas.append(c)
        if c >= 0.6:
            ultima_dita, pos = i, novo
    resto = frases[ultima_dita + 1:]
    if not resto or any(m >= 0.3 for m in marcas[ultima_dita + 1:]):
        return ""
    return " ".join(resto)


def _modelo_fora(exc: Exception) -> bool:
    """Para a VOZ AO VIVO: o modelo nao existe / nao e liberado para esta chave.

    Cota por minuto, pico (429/503) ou pedido mal formado NAO tiram a fala do
    2.5 — ele e o unico gratis por sessao, e um pico passa sozinho."""
    t = f"{type(exc).__name__} {exc}".lower()
    return any(s in t for s in ("404", "not found", "not supported", "unsupported",
                                "permission_denied", "permission denied", "403"))


def _erro_de_modelo(exc: Exception) -> bool:
    """O servidor recusou o MODELO (nao existe para esta chave, sem cota ou nao
    aceita a configuracao) — e nao uma queda de rede passageira."""
    t = f"{type(exc).__name__} {exc}".lower()
    return any(s in t for s in ("404", "not found", "not supported", "unsupported",
                                "429", "resource_exhausted", "quota",
                                "1008", "1007", "invalid argument", "invalid_argument",
                                "permission", "403"))

# QUANDO ABRIR A SEGUNDA CONEXAO (05/09/2026) — ver `_corrida`.
#
# Medido no log do dia, 30 falas: o primeiro som chega em 1,2 a 4,0 s na maior
# parte das vezes, mas em uma de cada seis a sessao TRAVA e so volta quando o
# prazo estoura. Os numeros reais das travadas: 6,1s / 8,5s / 9,3s / 9,6s /
# 22,1s. O 22,1 foi o que o Diego viu como "falar 31,7s".
#
# A travada nao avisa: nao ha erro, o `receive()` simplesmente nao devolve
# nada. Entao esperar e a unica forma de descobrir — e esperar era exatamente
# o problema. A saida e correr: passados HEDGE_S sem UM BYTE de audio, abre uma
# SEGUNDA conexao com a mesma voz e deixa as duas gerarem. Quem entregar o
# primeiro pedaco leva o turno; a outra e cancelada e a sessao dela, derrubada.
#
# 2,5 s: acima da conexao boa aquecida (1,2-2,0 s), abaixo da travada mais
# curta (6,1 s). Correr cedo demais gastaria uma sessao a toa em toda fala.
HEDGE_S = 2.5

# ...SO QUE 2,5 s FIXO NAO SERVE PARA SEMPRE (05/09/2026, no mesmo dia).
#
# Com o prazo fixo, a corrida disparou em QUASE TODA fala: naquela tarde o
# servidor estava devolvendo o primeiro som em 2,8-2,9 s, e nao nos 1,2-2,0 s
# de antes. A fala saia certa, mas gastava uma sessao a toa toda vez.
#
# Entao o prazo passa a ser medido: guarda-se o tempo do primeiro som das
# ultimas falas e a corrida abre em MEDIANA + FOLGA. Num dia bom o prazo cai
# para o piso e a corrida quase nunca acontece; num dia ruim ele sobe sozinho e
# so as travadas de verdade pagam a segunda sessao.
# ...E A FOLGA ESTAVA CARA DEMAIS (05/09/2026, noite, medido no PC do Diego).
#
# O registro de 15 falas mostrou o que a primeira versao nao tinha como saber:
# QUANDO A SEGUNDA CONEXAO ENTRA, ELA E RAPIDA.
#
#     19:39:05  corrida abriu aos 3,4s  ->  19:39:06  ganhou   = 1,3s depois
#     19:40:08  corrida abriu aos 3,4s  ->  19:40:10  ganhou   = 2,1s depois
#
# Ou seja: a espera de 3,4 s era quase toda a demora, e nao a segunda conexao.
# A sessao dela ja fica de pe entre as falas (`conexao=+0.00s` no registro), o
# que sobra e o servidor gerando — 1,3 a 2,1 s, o mesmo de uma fala boa.
#
# Entao o prazo desce: folga 1,2 -> 0,7 s e piso 2,0 -> 1,5 s. Com a mediana
# medida naquela noite (1,4 s), o prazo cai de 2,6 para 2,1 s, e a fala travada
# passa a custar ~3,6 s em vez de ~4,9 s.
#
# O TETO DESCE JUNTO (5,0 -> 3,0). Ele existia para nao correr num dia ruim,
# mas dia ruim e exatamente quando a corrida vale mais: com o teto em 5 s, uma
# tarde lenta deixava o dono esperando 5 s antes de sequer tentar a segunda.
#
# O QUE ISSO CUSTA, escrito para nao parecer de graca: a corrida dispara mais
# vezes, e cada disparo e uma geracao a mais no servidor. Nao gasta sessao nova
# (a espelho e persistente), so trabalho — e a conta ja foi feita: 1,5 s a menos
# de silencio, toda vez, vale mais que economizar uma geracao que o plano cobre.
FOLGA_DA_CORRIDA_S = 0.7
CORRIDA_MIN_S = 1.5
CORRIDA_MAX_S = 2.3
TEMPOS_OLHADOS = 8
DIA_LENTO_S = 1.6

# Prazo ate o PRIMEIRO som. Era 20 s — e 20 s era o proprio defeito: a fala so
# saia DEPOIS de estourar. Com a corrida cobrindo a travada, ele so existe para
# o caso das DUAS conexoes morrerem juntas.
#
# 25 -> 12 s (05/09/2026, medido no PC do cliente da AJ). La o servidor da
# Gemini oscila muito: 26 falas deram mediana 2,5 s, melhor 1,1 s e PIOR
# 34,3 s - com as DUAS conexoes lentas ao mesmo tempo, que e o caso que a
# corrida nao cobre. Nao havia 429 nenhum no log: e o servidor mesmo.
# 12 s e cinco vezes a mediana: fala normal nunca chega perto, e quando as
# duas morrem o dono espera 12 s em vez de 34 e ouve a MESMA voz pela
# reserva. Cauda limitada vale mais que meio minuto de silencio.
# 12 -> 8 s (05/09/2026, noite). Com a corrida abrindo a segunda conexao aos
# ~2,1 s, esperar ate 12 s para desistir das duas era somar 8 s de silencio a
# uma fala que ja estava perdida: no caso medido, o dono esperou 12 s aqui e
# mais 33 s na reserva. Se nem a segunda conexao devolveu um byte em 8 s, ela
# nao vai devolver — e o socorro local agora resolve em ~1 s.
PRAZO_TURNO_S = 8.0

# Depois que o som COMECOU, o que se vigia e o silencio entre um pedaco e o
# proximo. O defeito conhecido e a sessao emudecer no fim do turno, com a fala
# ja quase toda entregue; esperar o turno "fechar" nesse caso so atrasa a
# proxima resposta. 8 s cobre folgado a pausa normal entre frases.
SILENCIO_MAX_S = 8.0


class GeminiCharonVoice:
    """A sessao ao vivo. O nome da classe ficou Charon por historia; a VOZ
    agora vem de fora (05/09/2026, quando a engrenagem virou 11 vozes da
    Gemini). `voz` e o nome do timbre no servidor, nao a chave do personagem.
    """

    def __init__(self, voz: str = "Charon", jeito: str = "") -> None:
        self.voz = str(voz or "Charon")
        # O JEITO ESCOLHIDO NA ENGRENAGEM TAMBEM DIRIGE A VOZ (05/09/2026).
        # Ate aqui so as vozes de estudio recebiam a direcao de entrega; a da
        # Gemini falava sempre igual, e trocar de "mordomo" para "malandro"
        # mudava as PALAVRAS e nao mudava o TOM. Ver `_direcao_do_jeito`.
        self.jeito = str(jeito or "")
        config_path = Path(__file__).resolve().parents[1] / "config" / "api_keys.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        api_key = str(data.get("gemini_api_key") or "").strip()
        if not api_key:
            raise RuntimeError("Chave Gemini ausente em config/api_keys.json")
        self.client = genai.Client(api_key=api_key)
        self._persistent_cm = None
        self._persistent_session = None
        self._persistent_lock = None
        self._usada_em = 0.0          # ultima vez que a sessao abriu ou falou
        # A SEGUNDA CONEXAO, para a corrida. So nasce quando a primeira trava
        # pela primeira vez, e daí em diante fica de pe, aquecida, esperando.
        # `_e_espelho` impede que o espelho crie um espelho dele.
        self._espelho = None
        self._e_espelho = False
        _VIVAS.add(self)

    def speak(self, text: str, on_audio_start=None, on_audio_end=None) -> None:
        asyncio.run(self._speak(text, on_audio_start=on_audio_start, on_audio_end=on_audio_end))

    def synthesize_wav(self, text: str, out: Path) -> Path:
        return asyncio.run(self._synthesize_wav(text, out))

    def _norm(self, value: str) -> str:
        value = unicodedata.normalize("NFD", value or "")
        value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
        value = re.sub(r"[^a-zA-Z0-9]+", " ", value.lower())
        return re.sub(r"\s+", " ", value).strip()

    def _is_safe_transcript(self, expected: str, actual: str) -> bool:
        if not actual:
            return True
        bad = (
            "nao posso",
            "nao consigo",
            "como assistente",
            "nao tenho capacidade",
            "não posso",
            "não consigo",
        )
        actual_norm = self._norm(actual)
        if any(self._norm(term) in actual_norm for term in bad):
            return False
        return True

    async def _speak(self, text: str, on_audio_start=None, on_audio_end=None) -> None:
        chunks, transcript = await self._generate_audio(text)
        if not self._is_safe_transcript(text, transcript):
            raise RuntimeError(f"transcrição de voz diferente do chat: {transcript}")
        if on_audio_start:
            on_audio_start()
        with sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=1024,
        ) as stream:
            try:
                for chunk in chunks:
                    stream.write(chunk)
            finally:
                if on_audio_end:
                    on_audio_end()

    async def _generate_audio(self, text: str) -> tuple[list[bytes], str]:
        cfg = self._live_config()
        chunks: list[bytes] = []
        transcript_parts: list[str] = []
        async with self.client.aio.live.connect(model=self._modelo_vivo(), config=cfg) as session:
            await session.send_client_content(
                turns={"role": "user", "parts": [{"text": f"Leia literalmente, sem alterar nenhuma palavra: {text}"}]},
                turn_complete=True,
            )
            async for response in session.receive():
                data = getattr(response, "data", None)
                if data:
                    chunks.append(data)
                server_content = getattr(response, "server_content", None)
                if server_content:
                    out = getattr(server_content, "output_transcription", None)
                    if out and getattr(out, "text", None):
                        transcript_parts.append(out.text)
                    if getattr(server_content, "turn_complete", False):
                        break
        return chunks, " ".join(transcript_parts).strip()

    def _live_config(self) -> types.LiveConnectConfig:
        # SEM PENSAR ANTES DE FALAR (05/09/2026). Este modelo e de audio NATIVO:
        # ele raciocina antes de abrir a boca, e o proprio SDK avisa que a
        # resposta veio com partes `text` e `thought`. Para CONVERSAR isso e
        # otimo — e o que faz a voz do repositorio soar inteligente. Para LER
        # uma frase que o cerebro ja escreveu, e so espera.
        #
        # Medido em A/B alternado, 2 voltas de 3 frases, com a chave do Diego:
        #     pensando ....... 2,22 s ate o primeiro som
        #     sem pensar ..... 1,60 s   (e 1,20 s nos turnos ja aquecidos)
        # O audio saiu do mesmo tamanho: nao se perdeu fala, so a espera.
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            # FALAR COMO GENTE, NAO COMO QUEM LE EM VOZ ALTA (05/09/2026).
            #
            # A instrucao antiga dizia "voce e apenas o sintetizador de voz" e
            # "leia literalmente" — e era exatamente assim que saia: correto e
            # sem alma. O que faz a voz do repositorio de referencia soar viva e
            # que la o modelo esta CONVERSANDO. Aqui ele nao pode conversar (o
            # texto vem pronto do cerebro), mas pode DIZER em vez de ler.
            #
            # A fidelidade continua exigida palavra por palavra: o que muda e a
            # entrega — respiracao, entonacao de pergunta, a emocao que o
            # proprio texto pede.
            # ELE LIA A INSTRUCAO EM VOZ ALTA (05/09/2026).
            #
            # A direcao de entrega entrava no fim como um bloco rotulado —
            # "DIRECAO DE ENTREGA (obedeça...): Tom cortês e elegante..." — e o
            # modelo comecou a FALAR aquilo antes da frase: o Diego ouvia "Tom
            # da voz..." em toda resposta. Rotulo em caixa alta com dois pontos
            # parece conteudo a ler, nao regra a obedecer.
            #
            # O conserto tem tres partes: dizer explicitamente que ESTA
            # instrucao nunca e falada, apontar exatamente ONDE comeca o texto
            # a dizer (depois do "Leia literalmente:"), e costurar o tom no meio
            # da frase em vez de pendurar um bloco rotulado no fim.
            system_instruction=(
                "Você é a VOZ do JARVIS falando com o dono dele. O que você fala é "
                "SÓ o que vier na mensagem do usuário depois de 'Leia "
                "literalmente:' — palavra por palavra, sem trocar, tirar nem "
                "acrescentar nada. ESTA INSTRUÇÃO AQUI NUNCA É FALADA: ela só diz "
                "COMO dizer, e nenhuma palavra dela pode sair na sua voz. "
                "Diga como GENTE fala, não como quem lê em voz alta: ritmo "
                "natural, respiração entre as frases, a entonação subindo na "
                "pergunta e caindo no fim da afirmação, e a emoção que o próprio "
                "texto pede. " + _lingua_da_voz() + ", voz confiante e próxima"
                + self._direcao_do_jeito() + ". Nunca responda ao texto, nunca "
                "comente, nunca avise que vai ler."
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=getattr(self, "voz", "Charon"))
                )
            ),
        )

    def _direcao_do_jeito(self) -> str:
        """A direcao de entrega do jeito escolhido, em uma frase.

        Reaproveita a tabela que ja existia para as vozes de estudio
        (`voz_openai.ESTILO_POR_JEITO`) — escrever uma segunda tabela faria as
        duas desandarem em silencio na primeira vez que alguem mexesse numa so.
        """
        if not getattr(self, "jeito", ""):
            return ""
        try:
            from core.voz_openai import ESTILO_POR_JEITO  # noqa: PLC0415
            direcao = ESTILO_POR_JEITO.get(self.jeito, "")
        except Exception:
            direcao = ""
        if not direcao:
            return ""
        # As frases da tabela comecam com "Tom ..." porque foram escritas para
        # outro lugar. Aqui elas entram no MEIO de uma frase, entao o "Tom" sai
        # e a primeira letra vira minuscula — costurado, e nao colado.
        direcao = direcao.strip().rstrip(".")
        if direcao.lower().startswith("tom "):
            direcao = direcao[4:]
        return ", com tom " + direcao[0].lower() + direcao[1:]

    # Qual modelo da `LIVE_MODELOS` esta valendo. Da classe de proposito: a
    # titular e o espelho falam com o mesmo servidor e devem andar juntos.
    _modelo_idx = 0

    # LER (esta classe) x CONVERSAR (`GeminiConversa`, 08/10/2026).
    _CONFERE_TAMANHO = True
    ultima_transcricao = ""
    _PRAZO_TOTAL_S = PRAZO_TURNO_S + 5
    _PRAZO_1O_SOM_S = PRAZO_TURNO_S

    def _texto_do_turno(self, texto: str) -> str:
        return f"Leia literalmente, sem alterar nenhuma palavra: {texto}"

    def _irmao(self):
        """Uma conexao igual a esta (para a corrida)."""
        return GeminiCharonVoice(self.voz, self.jeito)

    def _mesma_config(self, outro) -> bool:
        return (outro is not None and type(outro) is type(self)
                and outro.voz == self.voz and outro.jeito == self.jeito)

    @classmethod
    def _modelo_vivo(cls) -> str:
        return LIVE_MODELOS[min(cls._modelo_idx, len(LIVE_MODELOS) - 1)]

    # SESSAO PARADA MORRE CALADA (08/10/2026). Medido com o 3.8-live: parada
    # 15 s e 25 s, a fala seguinte saiu em 0,5-0,6 s; parada 45 s, a sessao
    # nao devolveu NADA por 20 s (o servidor fecha sem avisar). Foi a "voz 3.4s"
    # do Diego: a corrida e que salvou. Agora, parada mais que isto, ela e
    # trocada por uma nova ANTES de falar — de preferencia no aquecimento, que
    # roda enquanto o cerebro pensa (abrir custa ~0,65 s).
    #
    # ...E ISSO FOI DESLIGADO NO MESMO DIA (08/10/2026, 16h). O gratis do
    # ao-vivo conta por SESSAO ABERTA: trocar a sessao a cada 20 s parada
    # multiplicou as aberturas e, somado aos testes do dia, o Google passou a
    # responder "You exceeded your current quota". O Diego: "antes nao dava
    # limite de forma alguma" — era UMA sessao aberta por horas. Volta a ser
    # assim: a sessao so e trocada quando ela cai ou trava de verdade.
    _OCIOSA_MAX_S = None

    def _sessao_velha(self) -> bool:
        import time as _t  # noqa: PLC0415
        if not self._OCIOSA_MAX_S:
            return False
        usada = getattr(self, "_usada_em", None)
        if usada is None or getattr(self, "_persistent_session", None) is None:
            return False
        return _t.monotonic() - usada > self._OCIOSA_MAX_S

    def _marcar_uso(self) -> None:
        import time as _t  # noqa: PLC0415
        self._usada_em = _t.monotonic()

    async def _ensure_persistent_session(self):
        if self._persistent_lock is None:
            self._persistent_lock = asyncio.Lock()
        if self._sessao_velha():
            await self._reset_persistent_session()
        import time as _tcota  # noqa: PLC0415
        if self._persistent_session is None and _tcota.monotonic() < _cota_ate[0]:
            raise RuntimeError("voz ao vivo descansando: exceeded your current quota (cota)")
        while self._persistent_session is None:
            modelo = self._modelo_vivo()
            try:
                self._persistent_cm = self.client.aio.live.connect(
                    model=modelo,
                    config=self._live_config(),
                )
                self._persistent_session = await self._persistent_cm.__aenter__()
                self._marcar_uso()
            except Exception as exc:
                self._persistent_cm = None
                self._persistent_session = None
                if _e_cota(exc):
                    _anotar_cota(exc)
                    raise
                ultimo = GeminiCharonVoice._modelo_idx >= len(LIVE_MODELOS) - 1
                if ultimo or not _modelo_fora(exc):
                    raise
                GeminiCharonVoice._modelo_idx += 1
                print(f"[JARVIS] voz: {modelo} recusou ({str(exc)[:90]}); "
                      f"agora pelo {self._modelo_vivo()}")
        return self._persistent_session

    async def _reset_persistent_session(self) -> None:
        cm = self._persistent_cm
        self._persistent_cm = None
        self._persistent_session = None
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass

    async def warm_persistent_session(self) -> None:
        # SESSAO JA DE PE NAO ENCOSTA NA TRAVA (05/09/2026).
        #
        # Medido no proprio log: `trava=1.22s`. O aquecimento pegava a trava
        # para descobrir que nao havia nada a fazer, e a fala que chegava no
        # meio disso esperava a volta inteira. Aquecer existe para TIRAR tempo
        # do caminho da fala, nunca para pôr.
        if self._persistent_session is not None and not self._sessao_velha():
            return
        if self._persistent_lock is None:
            self._persistent_lock = asyncio.Lock()
        async with self._persistent_lock:
            await self._ensure_persistent_session()

    async def generate_audio_persistent(self, text: str) -> tuple[list[bytes], str]:
        if self._persistent_lock is None:
            self._persistent_lock = asyncio.Lock()
        async with self._persistent_lock:
            last_error = None
            for _attempt in range(2):
                try:
                    session = await self._ensure_persistent_session()
                    chunks: list[bytes] = []
                    transcript_parts: list[str] = []
                    await session.send_client_content(
                        turns={"role": "user", "parts": [{"text": f"Leia literalmente, sem alterar nenhuma palavra: {text}"}]},
                        turn_complete=True,
                    )
                    async for response in session.receive():
                        data = getattr(response, "data", None)
                        if data:
                            chunks.append(data)
                        server_content = getattr(response, "server_content", None)
                        if server_content:
                            out = getattr(server_content, "output_transcription", None)
                            if out and getattr(out, "text", None):
                                transcript_parts.append(out.text)
                            if getattr(server_content, "turn_complete", False):
                                self._marcar_uso()
                                return chunks, " ".join(transcript_parts).strip()
                    raise RuntimeError("sessão literal encerrou sem turn_complete")
                except Exception as exc:
                    last_error = exc
                    await self._reset_persistent_session()
            raise RuntimeError(f"falha na sessão literal: {last_error}")

    async def stream_audio_persistent(self, text: str, on_chunk) -> str:
        """Entrega PCM conforme chega, mantendo a sessão literal já aquecida.

        ONDE VAI O TEMPO ATE O PRIMEIRO SOM (05/09/2026). Fora do JARVIS, esta
        mesma sessao entrega em 1,2 s; dentro dele, em 2,9 s. Sem separar as
        tres etapas — esperar a trava, abrir a conexao, mandar o texto — nao ha
        como saber qual delas engordou, e este projeto ja perdeu noites
        adivinhando isso. Os tres numeros saem no `charon_primeiro_som`.
        """
        import time as _t  # noqa: PLC0415
        _t0 = _t.monotonic()
        self._gasto_trava = self._gasto_conexao = self._gasto_envio = 0.0
        self.ultima_transcricao = ""
        if self._persistent_lock is None:
            self._persistent_lock = asyncio.Lock()
        async with self._persistent_lock:
            self._gasto_trava = _t.monotonic() - _t0
            last_error = None
            for _attempt in range(2):
                emitted = False
                try:
                    session = await self._ensure_persistent_session()
                    self._gasto_conexao = _t.monotonic() - _t0
                    transcript_parts: list[str] = []
                    texto_turno = text
                    completou = False
                    while True:
                        await session.send_client_content(
                            turns={"role": "user", "parts": [{"text": self._texto_do_turno(texto_turno)}]},
                            turn_complete=True,
                        )
                        if not completou:
                            self._gasto_envio = _t.monotonic() - _t0
                        bytes_turno = 0
                        fechou = False
                        async for response in session.receive():
                            data = getattr(response, "data", None)
                            if data:
                                emitted = True
                                bytes_turno += len(data)
                                on_chunk(data)
                            server_content = getattr(response, "server_content", None)
                            if server_content:
                                out = getattr(server_content, "output_transcription", None)
                                if out and getattr(out, "text", None):
                                    transcript_parts.append(out.text)
                                if getattr(server_content, "turn_complete", False):
                                    fechou = True
                                    break
                        if not fechou:
                            raise RuntimeError("sessão literal encerrou sem turn_complete")
                        self._marcar_uso()
                        self.ultima_transcricao = " ".join(transcript_parts).strip()
                        if not self._CONFERE_TAMANHO:
                            # Conversa: o texto e DELE, nao ha tamanho esperado.
                            # Turno sem som nenhum e falha (a outra conexao, ou
                            # o cerebro, assume).
                            if bytes_turno == 0:
                                raise RuntimeError("a conversa fechou o turno sem audio")
                            return self.ultima_transcricao
                        falta = "" if completou else _o_que_faltou(
                            texto_turno, bytes_turno / (2.0 * RECEIVE_SAMPLE_RATE))
                        if not falta and not completou:
                            falta = _faltou_pela_transcricao(
                                texto_turno, " ".join(transcript_parts))
                        if not falta:
                            return self.ultima_transcricao
                        completou = True
                        try:
                            from main import _voice_diag  # noqa: PLC0415
                            _voice_diag("voz_cortada_completei",
                                        f"{bytes_turno / 48000:.1f}s para {len(texto_turno)} letras — "
                                        f"faltou {falta[:60]!r}")
                        except Exception:
                            pass
                        texto_turno = falta
                except Exception as exc:
                    last_error = exc
                    await self._reset_persistent_session()
                    # Nunca reinicia depois de já tocar áudio, evitando duplicação.
                    if emitted:
                        break
                    if _e_cota(exc):
                        if "descansando" not in str(exc):
                            _anotar_cota(exc)
                        break
                    # O modelo da frente recusou no meio (cota do dia, por
                    # exemplo): a segunda tentativa ja vai pelo seguinte.
                    if (_modelo_fora(exc)
                            and GeminiCharonVoice._modelo_idx < len(LIVE_MODELOS) - 1):
                        GeminiCharonVoice._modelo_idx += 1
                        print(f"[JARVIS] voz: modelo recusou no meio ({str(exc)[:80]}); "
                              f"agora pelo {self._modelo_vivo()}")
            raise RuntimeError(f"falha no streaming literal: {last_error}")

    # Os tempos do primeiro som das ultimas falas. Class-level de proposito: o
    # espelho e a titular sao objetos diferentes e medem o MESMO servidor.
    _tempos_1o_som = []

    @classmethod
    def _anotar_1o_som(cls, segundos: float) -> None:
        cls._tempos_1o_som.append(float(segundos))
        del cls._tempos_1o_som[:-TEMPOS_OLHADOS]

    @classmethod
    def _prazo_da_corrida(cls) -> float:
        """Quando abrir a segunda conexao, medido no proprio dia.

        Mediana das ultimas falas + folga, presa entre o piso e o teto. Sem
        historico ainda, vale o `HEDGE_S` de sempre.
        """
        tempos = sorted(cls._tempos_1o_som)
        if len(tempos) < 3:
            return CORRIDA_MIN_S
        meio = tempos[len(tempos) // 2]
        # DIA LENTO, AS DUAS JUNTAS (08/10/2026). A conta antiga subia o prazo
        # junto com a mediana — num dia ruim do Google (mediana de 3 s, picos
        # de 6 a 12 s, medido hoje com a chave do Diego) a segunda conexao so
        # abria aos 3 s, e o dono pagava a espera inteira. Dia ruim e quando a
        # corrida vale mais: com a mediana acima de `DIA_LENTO_S`, as duas
        # conexoes saem JUNTAS e fala a primeira. O ao-vivo 2.5 e gratis por
        # sessao, entao a geracao a mais nao gasta cota.
        if meio > DIA_LENTO_S:
            return 0.0
        return min(CORRIDA_MAX_S, max(CORRIDA_MIN_S, meio + FOLGA_DA_CORRIDA_S))

    _laco_proprio = None
    _lock_laco = threading.Lock()

    @classmethod
    def _garantir_laco(cls):
        """O laco de eventos proprio, numa thread so dele.

        A sessao persistente precisa sempre do MESMO laco, e o laco do JARVIS
        esta ocupado com o microfone e a caixa de som.
        """
        with cls._lock_laco:
            if cls._laco_proprio is None:
                laco = asyncio.new_event_loop()

                def rodar():
                    asyncio.set_event_loop(laco)
                    laco.run_forever()

                threading.Thread(target=rodar, daemon=True,
                                 name="charon-loop").start()
                cls._laco_proprio = laco
            return cls._laco_proprio

    def _garantir_espelho(self):
        """A segunda conexao, mesma voz e mesmo jeito."""
        if self._e_espelho:
            return None
        if not self._mesma_config(self._espelho):
            espelho = self._irmao()
            if espelho is None:
                return None
            espelho._e_espelho = True
            self._espelho = espelho
        return self._espelho

    def aquecer_em_fundo(self) -> None:
        """Abre (ou reabre) as duas conexoes SEM segurar quem chamou.

        Chamada em tres momentos, todos fora do caminho da fala: ao abrir o
        programa, assim que o whisper entende a frase — enquanto o cerebro
        pensa — e logo depois de cada fala terminar. O objetivo e que nenhuma
        fala pague o aperto de mao: medido, a conexao fria custa 6,1 s e a
        aquecida 1,2 s ate o primeiro som.
        """
        laco = self._garantir_laco()

        async def _abrir_as_duas():
            try:
                await self.warm_persistent_session()
            except Exception:
                pass
            # A SEGUNDA CONEXAO SO E AQUECIDA SE JA EXISTIR — ou seja, se uma
            # corrida ja aconteceu. Criar uma aqui deixaria DUAS sessoes vivas
            # desde a abertura, num dia em que talvez nenhuma corrida aconteca:
            # sessao aberta a toa e o que aperta o limite por minuto da Gemini
            # (ver a memoria da cota do ao-vivo).
            # LIMITE DE CONEXOES DA CHAVE GRATIS (08/10/2026): medido, com o
            # JARVIS aberto so mais 2 sessoes ao vivo abriram; da 3a em diante,
            # "You exceeded your current quota". Por isso a espelho continua
            # nascendo so na primeira corrida (e nao no aquecimento), e a
            # conversa direta (`GeminiConversa`) nao tem espelho: no pior caso
            # sao 3 sessoes abertas.
            espelho = self._espelho
            if espelho is not None:
                try:
                    await espelho.warm_persistent_session()
                except Exception:
                    pass

        try:
            asyncio.run_coroutine_threadsafe(_abrir_as_duas(), laco)
        except Exception:
            pass

    async def _corrida_unica(self, texto: str, on_chunk) -> str:
        """Uma conexao so: se travar sem som, derruba e repete numa nova.

        LIMITE DE SESSOES DA CHAVE GRATIS (08/10/2026): com o JARVIS de pe so
        mais 2 sessoes ao vivo abriram. O JARVIS fica com DUAS: a que conversa
        (`GeminiConversa`) e a que le o texto do cerebro — cada uma SEM espelho.
        Se nao sair som em `_PRAZO_1O_SOM_S`, a conexao e derrubada e a frase
        vai de novo numa conexao NOVA (~0,7 s para abrir): nunca passam de duas.
        """
        import time as _t  # noqa: PLC0415
        ultimo_erro = None
        for tentativa in range(1, int(getattr(self, "_TENTATIVAS_UNICA", 2)) + 1):
            t0 = _t.monotonic()
            primeiro = asyncio.Event()
            ultimo = {"som": t0}

            def entrega(raw, _t0=t0, _primeiro=primeiro, _ultimo=ultimo):
                if not raw:
                    return
                _ultimo["som"] = _t.monotonic()
                if not _primeiro.is_set():
                    _primeiro.set()
                    self._anotar_1o_som(_t.monotonic() - _t0)
                    try:
                        from main import _voice_diag  # noqa: PLC0415
                        _tr = getattr(self, "_gasto_trava", 0.0)
                        _cx = getattr(self, "_gasto_conexao", 0.0)
                        _en = getattr(self, "_gasto_envio", 0.0)
                        _voice_diag(
                            "voz_onde_foi_o_tempo",
                            f"unica{'' if tentativa == 1 else ' (2a conexao)'}: "
                            f"trava={_tr:.2f}s conexao=+{_cx - _tr:.2f}s "
                            f"envio=+{_en - _cx:.2f}s servidor=+"
                            f"{max(0.0, _t.monotonic() - _t0 - _en):.2f}s")
                    except Exception:
                        pass
                on_chunk(raw)

            tarefa = asyncio.ensure_future(self.stream_audio_persistent(texto, entrega))
            espera = asyncio.ensure_future(primeiro.wait())
            await asyncio.wait([tarefa, espera], timeout=self._PRAZO_1O_SOM_S,
                               return_when=asyncio.FIRST_COMPLETED)
            espera.cancel()
            if primeiro.is_set():
                # Falando: espera o fim, vigiando silencio entre os pedacos (o
                # defeito conhecido e a sessao emudecer no FIM do turno).
                while not tarefa.done():
                    sobra = SILENCIO_MAX_S - (_t.monotonic() - ultimo["som"])
                    if sobra <= 0:
                        break
                    await asyncio.wait([tarefa], timeout=sobra)
                if not tarefa.done():
                    tarefa.cancel()
                    try:
                        await asyncio.wait([tarefa], timeout=2.0)
                    except Exception:
                        pass
                    await self._reset_persistent_session()
                elif tarefa.exception() is not None:
                    await self._reset_persistent_session()
                return str(getattr(self, "ultima_transcricao", "") or "")
            # Sem som: travou (ou caiu). Derruba e tenta numa conexao nova.
            if tarefa.done():
                ultimo_erro = tarefa.exception() or RuntimeError("turno sem audio")
                if _e_cota(ultimo_erro):
                    break           # cota: insistir so gasta mais (ver _DESCANSO_COTA_S)
            else:
                tarefa.cancel()
                try:
                    await asyncio.wait([tarefa], timeout=2.0)
                except Exception:
                    pass
                ultimo_erro = RuntimeError(
                    f"a conexao ficou muda por {self._PRAZO_1O_SOM_S:.1f}s")
            await self._reset_persistent_session()
            try:
                from main import _voice_diag  # noqa: PLC0415
                _voice_diag("voz_unica_travou",
                            f"tentativa {tentativa}: {str(ultimo_erro)[:80]} — conexao nova")
            except Exception:
                pass
        raise ultimo_erro or RuntimeError("a voz ficou muda")

    async def _corrida(self, texto: str, on_chunk) -> None:
        """Duas conexoes correndo; quem falar primeiro leva o turno.

        Ver a explicacao de `HEDGE_S` no topo do arquivo. O ponto fino aqui e
        que a PERDEDORA nao pode encostar no `on_chunk`: se as duas entregarem
        audio, o dono ouve a frase duas vezes — armadilha que ja pegou este
        projeto em 30/08. Por isso todo pedaco passa pelo `portao`, que so
        deixa passar o audio de quem chegou primeiro.
        """
        if getattr(self, "_SEM_ESPELHO", False):
            return await self._corrida_unica(texto, on_chunk)
        import time as _t  # noqa: PLC0415
        _t_pedido = _t.monotonic()
        prazo = self._prazo_da_corrida()
        dono = {"quem": None}
        ultimo = {"som": _t_pedido}
        primeiro = asyncio.Event()

        def portao(qual):
            def _entrega(raw):
                if not raw:
                    return
                ultimo["som"] = _t.monotonic()
                if dono["quem"] is None:
                    dono["quem"] = qual
                    primeiro.set()
                    # SO A CONEXAO TITULAR ENTRA NA MEDIA. O tempo da segunda ja
                    # carrega o prazo de espera dentro dele; realimentar com ele
                    # faria o prazo subir sozinho ate o teto.
                    if qual == "primeira" or prazo <= 0.0:
                        self._anotar_1o_som(_t.monotonic() - _t_pedido)
                    # ONDE FOI O TEMPO: trava / conexao / envio / servidor.
                    alvo = self if qual == "primeira" else self._espelho
                    try:
                        from main import _voice_diag  # noqa: PLC0415
                        _tr = getattr(alvo, "_gasto_trava", 0.0)
                        _cx = getattr(alvo, "_gasto_conexao", 0.0)
                        _en = getattr(alvo, "_gasto_envio", 0.0)
                        _voice_diag(
                            "voz_onde_foi_o_tempo",
                            f"{qual}: trava={_tr:.2f}s conexao=+{_cx - _tr:.2f}s "
                            f"envio=+{_en - _cx:.2f}s servidor=+"
                            f"{max(0.0, _t.monotonic() - _t_pedido - _en):.2f}s")
                    except Exception:
                        pass
                    # QUEM GANHOU A CORRIDA, no registro. Sem isto nao da para
                    # saber se a segunda conexao salvou a fala ou se foi gasto
                    # a toa — e e essa conta que diz se o prazo esta certo.
                    if qual == "segunda":
                        try:
                            from main import _voice_diag  # noqa: PLC0415
                            _voice_diag("voz_corrida_ganhou",
                                        "a segunda conexao salvou a fala")
                        except Exception:
                            pass
                if dono["quem"] != qual:
                    return          # a perdedora fala sozinha, no vazio
                on_chunk(raw)
            return _entrega

        tarefas = {"primeira": asyncio.ensure_future(
            self.stream_audio_persistent(texto, portao("primeira")))}
        espera = asyncio.ensure_future(primeiro.wait())
        await asyncio.wait([tarefas["primeira"], espera], timeout=prazo,
                           return_when=asyncio.FIRST_COMPLETED)
        espera.cancel()

        if not primeiro.is_set() and not tarefas["primeira"].done():
            # TRAVOU. Abre a segunda e deixa as duas correrem.
            espelho = self._garantir_espelho()
            if espelho is not None:
                tarefas["segunda"] = asyncio.ensure_future(
                    espelho.stream_audio_persistent(texto, portao("segunda")))
                # DEIXA A SEGUNDA COMECAR ANTES DE ESCREVER NO LOG.
                #
                # Medido: sem esta linha o primeiro som saia 0,97 s depois de a
                # corrida abrir — e nao 0,2 s. O culpado era o `import main`
                # logo abaixo: numa importacao fria ele segura o laco inteiro,
                # e a tarefa recem-criada nao chega a rodar. Uma volta do laco
                # resolve, e no JARVIS de pe nao custa nada.
                await asyncio.sleep(0)
                try:
                    from main import _voice_diag  # noqa: PLC0415
                    _voice_diag("voz_corrida",
                                f"{prazo:.1f}s sem som — abri a segunda conexao")
                except Exception:
                    pass

        # Espera o VENCEDOR terminar de entregar. Uma perdedora que acaba antes
        # nao encerra nada: o audio que importa ainda esta chegando pela outra.
        #
        # DOIS PRAZOS DIFERENTES, e a diferenca importa. Enquanto NAO saiu som,
        # vale o prazo cheio: e a espera do dono, e desistir cedo o deixa mudo.
        # Depois que o som comecou, o que se vigia e o SILENCIO entre pedacos —
        # o defeito conhecido e a sessao emudecer no FIM do turno, ja com quase
        # tudo falado. Antes isso segurava a proxima fala por dezenas de
        # segundos; agora sai em `SILENCIO_MAX_S` e ninguem percebe.
        erro = None
        while True:
            pendentes = [t for t in tarefas.values() if not t.done()]
            if not pendentes:
                break
            comecou = primeiro.is_set()
            # O que sobra do orcamento de silencio, e nao o orcamento inteiro:
            # esperar 8 s cheios a cada volta fazia a conta dar 16 s.
            limite = (max(0.5, SILENCIO_MAX_S - (_t.monotonic() - ultimo["som"]))
                      if comecou else self._PRAZO_1O_SOM_S)
            feitas, _ = await asyncio.wait(pendentes, timeout=limite,
                                           return_when=asyncio.FIRST_COMPLETED)
            if not feitas:
                if comecou and _t.monotonic() - ultimo["som"] >= SILENCIO_MAX_S:
                    break          # ja falou; o rabo do turno nao vale a espera
                if not comecou:
                    erro = erro or RuntimeError(
                        f"as duas conexoes ficaram mudas por {self._PRAZO_1O_SOM_S:.0f}s")
                    break
                continue
            if dono["quem"] and tarefas.get(dono["quem"]) in feitas:
                break
            if dono["quem"] is None and all(t.done() for t in tarefas.values()):
                break

        # Limpa o que sobrou. Quem nao venceu esta com a sessao suspeita —
        # derruba, e o proximo `aquecer_em_fundo` abre uma nova, boa.
        for nome, tarefa in tarefas.items():
            if nome == dono["quem"]:
                if tarefa.done():
                    if tarefa.exception() is not None and not primeiro.is_set():
                        erro = erro or tarefa.exception()
                    continue
                # VENCEDOR AINDA PENDURADO: emudeceu no fim do turno e a
                # vigia de silencio desistiu. Ele ja falou o que tinha a
                # falar — mas a sessao ficou com um turno aberto, e reusa-la
                # derramaria esse audio velho na proxima frase. Derruba.
            if not tarefa.done():
                tarefa.cancel()
                # Espera ela desenrolar ANTES de mexer na sessao dela: cancelar
                # nao e instantaneo, e fechar a conexao por baixo de quem ainda
                # esta dentro do `receive()` fecha duas vezes.
                try:
                    await asyncio.wait([tarefa], timeout=2.0)
                except Exception:
                    pass
            alvo = self if nome == "primeira" else self._espelho
            if alvo is not None:
                try:
                    await alvo._reset_persistent_session()
                except Exception:
                    pass

        _vencedor = self if dono["quem"] == "primeira" else (
            self._espelho if dono["quem"] == "segunda" else None)
        _dito = str(getattr(_vencedor, "ultima_transcricao", "") or "") if _vencedor else ""

        # QUEM GANHOU VIRA A PRIMEIRA (05/09/2026).
        #
        # Sem esta troca, uma conexao ruim continuaria sendo a titular: toda
        # fala pagaria os 2,5 s da corrida antes de o espelho salvar. Trocando
        # as duas de lugar, a proxima fala ja comeca pela conexao que acabou de
        # provar que funciona, e a corrida volta a ser excecao.
        if dono["quem"] == "segunda" and self._espelho is not None:
            outro = self._espelho
            (self._persistent_cm, outro._persistent_cm) = (
                outro._persistent_cm, self._persistent_cm)
            (self._persistent_session, outro._persistent_session) = (
                outro._persistent_session, self._persistent_session)
            (self._persistent_lock, outro._persistent_lock) = (
                outro._persistent_lock, self._persistent_lock)
            _u1, _u2 = getattr(self, "_usada_em", 0.0), getattr(outro, "_usada_em", 0.0)
            self._usada_em, outro._usada_em = _u2, _u1

        if dono["quem"] is None:
            if erro is None:
                for tarefa in tarefas.values():
                    if tarefa.done() and tarefa.exception() is not None:
                        erro = tarefa.exception()
                        break
            if erro is not None:
                raise erro
        return _dito

    def speak_para_fila(self, texto: str, on_chunk, modo: str = "ler") -> None:
        """Sincrono: manda TEXTO, devolve os pedacos de audio pelo `on_chunk`.

        Feito para ser chamado de uma thread do modo local, onde a Gemini serve
        APENAS de boca — o cerebro esta no OpenRouter e as tarefas no Codex.

        Por que isso nao queima a cota como a conversa de manha: a cota do Live
        e contada por SESSAO e por SEGUNDO DE AUDIO ENVIADO. Mandando so texto,
        nao ha audio subindo, e a sessao persistente aguenta horas — medido em
        30/08, com uma conexao servindo varios turnos enquanto os modelos
        cobrados por pedido estavam todos estourados.

        O laco de eventos e proprio e vive numa thread separada: a sessao
        persistente precisa sempre do MESMO laco, e o laco do JARVIS esta
        ocupado com o microfone e a caixa de som.
        """
        laco = self._garantir_laco()
        self._modo_turno = modo

        # A sessao persistente entrega o PRIMEIRO turno e depois as vezes
        # trava: medido em 30/08, frase 1 em 5,7 s e frases 2 e 3 estourando o
        # prazo. O iterador do `receive()` termina a cada turno completo e
        # reabri-lo dentro da mesma sessao nem sempre volta.
        #
        # ANTES: esperava 20 s, derrubava a sessao e tentava de novo. Funcionava
        # — e os 20 s de espera eram exatamente o que o dono ouvia como silencio
        # (o pico de 22,1 s do log de 05/09).
        #
        # AGORA: `_corrida` abre a segunda conexao depois de 2,5 s de silencio e
        # deixa as duas correrem. O prazo aqui virou so a rede de seguranca do
        # caso em que as DUAS morrem.
        #
        # NUNCA repetir depois de ja ter saido audio: em 30/08 a primeira
        # tentativa emitiu a fala inteira e estourou o prazo no fim; a segunda
        # falou TUDO DE NOVO, e o Diego ouviu a mesma frase duas vezes.
        ja_saiu = {"sim": False}

        def _marcado(raw):
            if raw:
                ja_saiu["sim"] = True
            on_chunk(raw)

        futuro = asyncio.run_coroutine_threadsafe(
            self._corrida(texto, _marcado), laco)
        try:
            _dito = futuro.result(timeout=self._PRAZO_TOTAL_S)
            self.ultima_transcricao = str(_dito or "")
            return self.ultima_transcricao
        except Exception:
            futuro.cancel()
            if ja_saiu["sim"]:
                # Ja falou. Estourar o prazo no fim nao justifica repetir.
                return
            try:
                asyncio.run_coroutine_threadsafe(
                    self._reset_persistent_session(), laco).result(timeout=8)
            except Exception:
                pass
            raise
        finally:
            self._modo_turno = "ler"
            # A PROXIMA FALA JA ENCONTRA AS DUAS CONEXOES DE PE. Aquecer aqui,
            # e nao na hora de falar, e o que tira o aperto de mao do caminho.
            try:
                self.aquecer_em_fundo()
            except Exception:
                pass

    async def _synthesize_wav(self, text: str, out: Path) -> Path:
        cfg = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=(
                "Fale exatamente o texto recebido, em " + _lingua_da_voz() + ", "
                "com voz formal, natural e inteligente. Não adicione nada."
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                )
            ),
        )
        chunks, transcript = await self._generate_audio(text)
        if not self._is_safe_transcript(text, transcript):
            raise RuntimeError(f"transcrição de voz diferente do chat: {transcript}")
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RECEIVE_SAMPLE_RATE)
            wf.writeframes(b"".join(chunks))
        return out

# ---------------------------------------------------------------------------
# Voz Charon pelo modelo de FALA (barato) — 30/08/2026
#
# A GeminiCharonVoice acima abre uma sessao do Live API so para ler uma frase.
# O Live API cobra por SEGUNDO DE AUDIO e carrega prompt + ferramentas em toda
# resposta (~10.116 fichas fixas medidas). Ler "Pronto, chefe." por esse caminho
# custa o mesmo que uma resposta inteira.
#
# O gemini-2.5-flash-preview-tts aceita a MESMA voz Charon, e um modelo de fala
# puro (nao raciocina, nao recusa, nao reescreve) e vive em OUTRO balde de cota
# — medido: 328 fichas na frase de teste, contra a sessao Live inteira.
#
# Por ser outro balde, ele continua falando depois que a cota do ao-vivo acaba.
# E por nao raciocinar, ele nao inventa nem recusa: a conferencia de transcricao
# que a classe de cima precisa fazer aqui nao tem motivo de existir.
#
# Medido em 30/08: 2 a 3,5 s ate o audio ficar pronto, contra ~1 s do ao-vivo.
# O modelo NAO entrega o audio em pedacos (generate_content_stream devolve tudo
# de uma vez), entao nao ha ganho em usar a versao de streaming do SDK.
import re as _re_fala

TTS_MODEL = "gemini-2.5-flash-preview-tts"

# FILA DE MODELOS DE FALA (31/08/2026).
#
# Cada modelo tem o SEU proprio teto diario — o `2.5-flash-tts` estourou em
# 100 pedidos e o JARVIS caiu para a voz local no meio da gravacao do Diego.
# Medido na hora, com o mesmo balde ja esgotado no primeiro:
#     gemini-2.5-flash-preview-tts .... BLOQUEADO (teto 100/dia)
#     gemini-3.1-flash-tts-preview ....  2,7 s   OK
#     gemini-2.5-pro-preview-tts ......  2,8 s   OK
#
# Todos aceitam a MESMA voz Charon, entao o dono nao ouve diferenca. Somando os
# baldes, o dia rende cerca de tres vezes mais antes de cair no Kokoro local.
TTS_MODELOS = [
    # 08/10/2026: o 3.8-flash-lite-tts entrega o audio em ~0,5 s (medido com a
    # chave do Diego) e e o MAIS BARATO dos de fala (US$ 6 por milhao de fichas
    # de audio, contra 9 do 3.8-flash-tts) — regra do Diego: "free e mais
    # barato". Os antigos seguem de reserva.
    "gemini-3.8-flash-lite-tts",
    "gemini-2.5-flash-preview-tts",   # o mais barato, tenta sempre primeiro
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-pro-preview-tts",
]


class GeminiConversa(GeminiCharonVoice):
    """O ao-vivo 2.5 CONVERSANDO, sem o cerebro no meio (08/10/2026).

    Pedido do Diego: "ainda ta demorando uns 4 segundos, nao tem como diminuir
    para pelo menos 2?". O caminho de sempre e: ouvido -> cerebro (texto) ->
    voz (le o texto). Sao duas idas ao Google em serie, e no dia o cerebro
    gratis devolvia 503 e quem respondia era o servidor (3,6 a 4,3 s). Para
    CONVERSA (sem acao no PC), o proprio ao-vivo 2.5 — o gratis por sessao —
    ouve a frase (em texto) e responde FALANDO, com o jeito do JARVIS: medido,
    1,1 a 1,6 s ate o primeiro som. O que e acao continua indo ao cerebro.

    Mesma corrida de duas conexoes e mesma renovacao da sessao parada da voz
    que le; muda a instrucao (o prompt de personalidade inteiro, montado na
    hora de abrir cada conexao, com a hora e a conversa recente) e o turno
    (a frase do dono, crua).
    """

    _tempos_1o_som = []
    _laco_proprio = None
    _PRAZO_TOTAL_S = 30.0
    # UMA CONEXAO SO (08/10/2026, pedido do Diego: "deixa uma conexao so para a
    # voz dele"). A chave gratis aguenta poucas sessoes ao vivo ao mesmo tempo,
    # entao nao ha segunda conexao correndo junto. Se em `_PRAZO_1O_SOM_S` nao
    # sair som, esta conexao e derrubada e a frase vai de novo numa conexao
    # NOVA (abrir custa ~0,7 s) — medido no dia: uma leitura travou 61 s.
    # PACIENCIA (08/10/2026, 16h20): 3 s era pouco — no dia o Google levava 4 a
    # 7 s e a conversa desistia de respostas que iam chegar. Agora espera ate
    # `_PRAZO_1O_SOM_S` na MESMA conexao, uma vez so; sem som ate la, a frase
    # vai pelo cerebro (cuja leitura tem a corrida de duas conexoes).
    _PRAZO_1O_SOM_S = 7.0
    _TENTATIVAS_UNICA = 1
    _SEM_ESPELHO = True
    _CONFERE_TAMANHO = False
    # LER e CONVERSAR NA MESMA conexao foi testado e REPROVADO (08/10/2026):
    # com o texto pronto entre <<< >>> deu 5/5 num teste limpo, mas no uso
    # misturado ele respondia no lugar de ler, falava "<<<" e passou a por
    # "<<<" nas proprias respostas. Cada conexao faz uma coisa so.

    def __init__(self, voz: str = "Charon", jeito: str = "", montar_instrucao=None) -> None:
        super().__init__(voz, jeito)
        # `montar_instrucao()` -> str: chamado a cada conexao nova, para a hora
        # e a conversa recente estarem sempre frescas.
        self.montar_instrucao = montar_instrucao

    def _texto_do_turno(self, texto: str) -> str:
        return texto

    def _irmao(self):
        # Sem segunda conexao: ver o limite de sessoes no `aquecer_em_fundo`.
        return None

    def _mesma_config(self, outro) -> bool:
        return (super()._mesma_config(outro)
                and getattr(outro, "montar_instrucao", None) is self.montar_instrucao)

    def _live_config(self) -> types.LiveConnectConfig:
        instrucao = ""
        try:
            if self.montar_instrucao is not None:
                instrucao = str(self.montar_instrucao() or "")
        except Exception:
            instrucao = ""
        if not instrucao:
            instrucao = ("Voce e o JARVIS, assistente pessoal do dono deste computador. "
                         "Responda falando, curto e natural, em " + _lingua_da_voz() + ".")
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            system_instruction=instrucao,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=getattr(self, "voz", "Charon"))
                )
            ),
        )


class GeminiCharonTTS:
    """Mesma voz Charon, pelo modelo de fala — outro balde de cota, bem mais barato."""

    def __init__(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config" / "api_keys.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        api_key = str(data.get("gemini_api_key") or "").strip()
        if not api_key:
            raise RuntimeError("Chave Gemini ausente em config/api_keys.json")
        self.client = genai.Client(api_key=api_key)

    def _config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                )
            ),
        )

    # Qual modelo respondeu por ultimo. Comeca no primeiro da fila e so anda
    # para a frente quando o balde do dia estoura — assim uma resposta nao paga
    # o pedagio de tentar um modelo que ja se sabe esgotado.
    _modelo_atual = 0

    def _pcm_sync(self, text: str) -> bytes:
        """Chamada bloqueante do SDK. Sempre rodar fora do laco de eventos.

        Percorre a fila de modelos: quando um estoura o teto do dia (429), o
        proximo assume com a MESMA voz Charon. So depois de todos falharem e que
        o JARVIS cai para a voz local.
        """
        ultimo = None
        for passo in range(len(TTS_MODELOS)):
            indice = (GeminiCharonTTS._modelo_atual + passo) % len(TTS_MODELOS)
            modelo = TTS_MODELOS[indice]
            try:
                resp = self.client.models.generate_content(
                    model=modelo,
                    contents=text,
                    config=self._config(),
                )
            except Exception as exc:
                ultimo = exc
                if _erro_de_modelo(exc):
                    continue          # balde do dia acabou (ou modelo fora): proximo da fila
                raise
            for cand in (resp.candidates or []):
                content = getattr(cand, "content", None)
                for part in (getattr(content, "parts", None) or []):
                    inline = getattr(part, "inline_data", None)
                    data = getattr(inline, "data", None)
                    if data:
                        if indice != GeminiCharonTTS._modelo_atual:
                            GeminiCharonTTS._modelo_atual = indice
                            print(f"[JARVIS] voz Charon agora pelo {modelo}")
                        # A RESERVA NAO ACELERA MAIS (05/09/2026).
                        #
                        # Ela acelerava 1,5x por SOLA desde 01/09, quando era o
                        # caminho PRINCIPAL e o dono tinha pedido pressa. Hoje
                        # ela e so a reserva do ao-vivo — e o ao-vivo NAO
                        # acelera. Resultado: bastava um 429 de um minuto para
                        # a voz mudar de ritmo no meio da conversa, e o Diego
                        # ouviu isso hoje: "apareceu uma voz robotica que nao
                        # era para estar".
                        #
                        # Ritmo diferente entre o caminho normal e o socorro e
                        # pior do que ritmo errado: o dono nao sabe que trocou,
                        # so ouve o assistente ficar estranho sem motivo.
                        return data
            ultimo = RuntimeError(f"{modelo} nao devolveu audio")
        raise ultimo or RuntimeError("nenhum modelo de fala respondeu")

    async def pcm(self, text: str) -> bytes:
        """PCM 16 bits, mono, 24 kHz — mesmo formato que a fila de audio ja espera."""
        text = str(text or "").strip()
        if not text:
            return b""
        # to_thread e obrigatorio: o SDK bloqueia, e travar o laco aqui
        # engasgaria o microfone e o alto-falante junto.
        return await asyncio.to_thread(self._pcm_sync, text)

    async def stream_audio(self, text: str, on_chunk) -> str:
        """Mesma assinatura da versao Live, para trocar uma pela outra sem cirurgia.

        Devolve o proprio texto como transcricao: modelo de fala le literalmente,
        entao nao existe divergencia entre o que foi escrito e o que foi falado.
        """
        raw = await self.pcm(text)
        if not raw:
            raise RuntimeError("modelo de fala devolveu audio vazio")
        for offset in range(0, len(raw), 2400):
            on_chunk(raw[offset: offset + 2400])
        return text

    # Quebra a resposta em sentencas. Frase curta demais nao vale um pedido
    # separado (o custo fixo de ida e volta come o ganho), entao pedacos de
    # menos de 25 caracteres sao emendados no anterior.
    _FIM_DE_FRASE = _re_fala.compile(r"(?<=[.!?…:;])\s+")

    @classmethod
    def _sentencas(cls, texto: str, minimo: int = 25) -> list:
        """No MAXIMO dois pedidos: a primeira frase e todo o resto.

        Por que dois e nao um por frase: a chave do Diego tem teto de **100
        pedidos por dia** neste modelo de fala (medido em 31/08/2026, quando
        estourou e o JARVIS caiu para a voz local). Um pedido por frase gastava
        3 e 4 fichas por resposta e queimou o dia inteiro numa noite.

        Dois resolve os dois lados: o primeiro sai rapido (a frase e curta) e
        toca enquanto o segundo e gerado, e a conta do dia rende o triplo.
        Resposta curta continua sendo UM pedido so.
        """
        texto = str(texto or "").strip()
        if not texto:
            return []
        # Resposta curta nao vale dividir: uma frase so ja sai rapido.
        if len(texto) <= 140:
            return [texto]
        partes = [p.strip() for p in cls._FIM_DE_FRASE.split(texto) if p.strip()]
        if len(partes) < 2:
            return [texto]
        primeira = partes[0]
        # Primeira frase curta demais nao ganha nada: junta a seguinte.
        i = 1
        while len(primeira) < minimo and i < len(partes):
            primeira += " " + partes[i]
            i += 1
        resto = " ".join(partes[i:]).strip()
        return [primeira, resto] if resto else [primeira]

    def speak_para_fila(self, texto: str, on_chunk) -> None:
        """Mesmo contrato da versao Live: manda texto, devolve audio em pedacos.

        Sincrono de proposito — quem chama e uma thread do modo local, e o laco
        de eventos do JARVIS esta ocupado com microfone e caixa de som.

        FRASE POR FRASE (30/08/2026). Gerar a resposta inteira de uma vez
        custava de 4 a 9 s ate sair o PRIMEIRO som — foi a queixa do Diego de
        "dezesseis, vinte segundos pra responder". Gerando por sentenca, a
        primeira sai em 1,5 a 2,5 s e as outras nascem enquanto ela toca.
        """
        texto = str(texto or "").strip()
        if not texto:
            return
        frases = self._sentencas(texto) or [texto]
        saiu_algum = False
        for frase in frases:
            try:
                raw = self._pcm_sync(frase)
            except Exception:
                # Uma frase que falha no meio nao pode calar o resto da
                # resposta; o que ja saiu continua valendo.
                if saiu_algum:
                    break
                raise
            if not raw:
                continue
            saiu_algum = True
            for offset in range(0, len(raw), 2400):
                on_chunk(raw[offset: offset + 2400])
        if not saiu_algum:
            raise RuntimeError("modelo de fala devolveu audio vazio")

    async def stream_audio_persistent(self, text: str, on_chunk) -> str:
        """Nome que o main.py usa na confirmacao literal. Aqui nao ha sessao
        persistente: cada fala e um pedido isolado, que e justamente o barato."""
        return await self.stream_audio(text, on_chunk)

    async def warm_persistent_session(self) -> None:
        """Nao ha o que aquecer: sem sessao aberta, nao ha sessao para cair.
        Existe so para o main.py poder trocar uma classe pela outra sem cirurgia."""
        return None

    def aquecer_em_fundo(self) -> None:
        """Mesmo motivo do metodo acima: contrato, nao trabalho."""
        return None

    def speak(self, text: str) -> None:
        """Fala e espera terminar. Para avisos soltos, fora da fila de audio."""
        raw = asyncio.run(self.pcm(text))
        if not raw:
            return
        with sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=1024,
        ) as stream:
            stream.write(raw)

    def synthesize_wav(self, text: str, out: Path) -> Path:
        raw = asyncio.run(self.pcm(text))
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RECEIVE_SAMPLE_RATE)
            wf.writeframes(raw)
        return out


# ---------------------------------------------------------------------------
# A VOZ CHARON PELO CAMINHO QUE DURA O DIA INTEIRO NO GRATUITO — 05/09/2026
#
# O Diego trouxe o repositorio de referencia (Mark-LII) dizendo que a voz DE LA
# nao acaba. Fui ver: ele fala com o MESMO modelo e a MESMA voz que a
# `GeminiCharonVoice` la de cima — `gemini-2.5-flash-native-audio-preview`, pelo
# **Live API**. Nao ha segredo nenhum no repositorio; o que mudou foi AQUI.
#
# Em 30/08/2026, com a chave PAGA na mao, o `main.py` foi trocado para o modelo
# de fala por pedido (`gemini-2.5-flash-preview-tts`) porque ele custa menos
# FICHAS por frase. Com chave gratuita a conta se inverte, e essa e a regra que
# explica tudo:
#
#     Live API .............. cobrado por SESSAO  -> uma conexao serve o dia
#     modelo de fala ........ cobrado por PEDIDO  -> 10 por dia no gratuito
#
# Comparar ficha com ficha nao vale quando as duas cotas nao sao contadas na
# mesma moeda. Entao a ordem volta a ser a do repositorio: **ao-vivo primeiro**.
#
# O modelo de fala NAO foi jogado fora — ele vive em outro balde de cota, entao
# continua servindo depois que o ao-vivo recusa. Vira reserva, nao caminho.
# ---------------------------------------------------------------------------

def _lingua_da_voz() -> str:
    """'Português do Brasil' ou, no cliente em ingles, 'Natural English'."""
    try:
        from core.idioma import em_ingles  # noqa: PLC0415
        if em_ingles():
            return "Natural English, neutral North American accent"
    except Exception:
        pass
    return "Português do Brasil"


class VozCharon:
    """Charon: tenta o ao-vivo (gratis o dia todo) e cai no modelo de fala.

    Tem o mesmo contrato das duas classes acima — `speak_para_fila`,
    `stream_audio_persistent` e `warm_persistent_session` —, entao o `main.py`
    troca uma pela outra sem cirurgia.
    """

    # Ate quando nao vale a pena tentar o ao-vivo de novo. Insistir a cada frase
    # so faria o dono esperar o erro chegar, de novo e de novo.
    _vivo_ate = 0.0

    # A ultima falha foi so uma engasgada (as conexoes ficaram mudas, sem erro
    # nenhum do Google)? Entao NAO se troca a voz: fica calado neste turno e a
    # resposta vai para o chat. Ver `_falar_local` no main.py.
    mudez_passageira = False

    def __init__(self, voz: str = "Charon", jeito: str = "") -> None:
        self.voz = str(voz or "Charon")
        self.jeito = str(jeito or "")
        self._vivo = None
        self._barato = None
        # VOZ UNICA (08/10/2026): `vivo_externo()` -> a `GeminiConversa` do
        # main, a mesma conexao que conversa. Com ela, a leitura nao abre
        # conexao propria (limite de sessoes da chave gratis).
        self.vivo_externo = None

    # -- os dois motores, criados so quando precisam --------------------------

    def _ao_vivo(self):
        externo = self.vivo_externo
        if callable(externo):
            try:
                v = externo()
            except Exception:
                v = None
            if v is not None:
                return v
        # TROCOU DE VOZ, TROCA A SESSAO. A voz vai na configuracao da conexao:
        # reaproveitar a sessao antiga faria o cliente escolher a Leda na
        # engrenagem e continuar ouvindo a anterior ate reabrir o programa.
        # O JEITO entra na MESMA conferencia: ele tambem vive na instrucao de
        # sistema da conexao, entao trocar de "mordomo" para "malandro" sem
        # refazer a sessao deixaria o tom antigo valendo ate reabrir o programa.
        if (self._vivo is None
                or getattr(self._vivo, "voz", None) != self.voz
                or getattr(self._vivo, "jeito", None) != self.jeito):
            # A LEITURA USA A CORRIDA DE SEMPRE (08/10/2026, 16h20). Eu tinha
            # posto uma conexao so, que DESISTIA com 3 s sem som e abria outra.
            # Num dia em que o Google leva 4 a 7 s para o primeiro som, ela
            # largava toda resposta que ia chegar e nao terminava nenhuma
            # ("a voz engasgou" duas vezes seguidas). A corrida nao mata a
            # primeira: abre a segunda JUNTO e fala quem chegar antes; e a
            # segunda fica de pe depois (nao reabre sessao a cada fala).
            self._vivo = GeminiCharonVoice(self.voz, self.jeito)
        return self._vivo

    def _de_reserva(self):
        if self._barato is None:
            self._barato = GeminiCharonTTS()
        return self._barato

    def _anotar_falha(self, exc: Exception) -> None:
        """Quanto tempo calar o ao-vivo depende do QUE ele reclamou."""
        import time as _t  # noqa: PLC0415
        texto = str(exc)
        baixo = texto.lower()
        cota = ("429" in texto or "RESOURCE_EXHAUSTED" in texto
                or "quota" in baixo)
        # CHAVE RECUSADA NAO E FALHA PASSAGEIRA. Sem esta faixa, uma chave
        # invalida fazia o ao-vivo ser tentado DE NOVO a cada minuto, e cada
        # tentativa e uma ida a rede com o dono esperando o erro chegar. Fica
        # de lado ate a proxima abertura do programa (a chave nova entra por
        # ali, na tela de chaves).
        # "ausente" cobre o cliente que instalou e nunca pos chave nenhuma: com
        # a Charon virando a voz PADRAO, sem esta linha ele tentaria o ao-vivo em
        # toda frase, para sempre, so para ouvir o mesmo erro.
        chave_ruim = ("api key not valid" in baixo or "api_key_invalid" in baixo
                      or "api key expired" in baixo or "chave gemini ausente" in baixo)
        # QUANTO CALAR DEPENDE DO QUE ESTOUROU (05/09/2026).
        #
        # Estava fixo em 15 min para qualquer 429 — e o 429 que a gente ve na
        # pratica e o de RITMO (limite por minuto), que passa em segundos. O
        # Diego levou 15 minutos na voz de reserva por causa de um aperto que
        # ja tinha passado quando ele falou a frase seguinte.
        #
        # O corpo do erro traz `retryDelay` quando o proprio Google sabe o
        # prazo; `_quanto_calar_a_gemini` (no cerebro) ja sabe ler isso e
        # separar "cota do DIA" de "limite por MINUTO". Reaproveitar e melhor do
        # que ter duas regras diferentes para a mesma familia de erro.
        if chave_ruim:
            espera = 12 * 3600.0
        elif cota:
            try:
                from core.cerebro_openrouter import _quanto_calar_a_gemini  # noqa: PLC0415
                espera = float(_quanto_calar_a_gemini(texto)[0])
            except Exception:
                espera = 60.0
        # Marcado para o `_falar_local` saber que NAO deve trocar de voz: numa
        # engasgada passageira, e melhor ficar calado neste turno e escrever no
        # chat do que responder com a voz de outra pessoa.
        VozCharon.mudez_passageira = ("mudas" in baixo)
        if "mudas" in baixo:
            # MUDEZ NAO E CASTIGO DE UM MINUTO (05/09/2026, noite).
            #
            # Quando as duas conexoes emudecem, nao ha erro do Google: e uma
            # engasgada passageira, e o registro mostra a voz normal voltando
            # na fala seguinte. Com 60 s de castigo o Diego ouvia OUTRA VOZ por
            # um minuto inteiro por causa de um tropeco de oito segundos — e a
            # troca de voz e a coisa que ele mais reclama.
            #
            # 15 s: o turno que tropecou sai pela voz local e o proximo ja volta
            # a ser ele. Se a mudez for de verdade (servidor ruim de longe), o
            # proximo turno cai aqui de novo e o castigo se renova sozinho.
            espera = 15.0
        else:
            espera = 60.0
        VozCharon._vivo_ate = _t.time() + espera
        try:
            from main import _voice_diag  # noqa: PLC0415
            _motivo = "chave recusada" if chave_ruim else ("cota" if cota else "erro")
            _voice_diag("charon_ao_vivo_fora",
                        f"{_motivo}: {texto[:90]} "
                        + ("(so na proxima abertura, com chave nova)" if chave_ruim
                           else (f"(reserva por {espera:.0f} s)" if espera < 60
                                 else f"(reserva por {espera/60:.0f} min)")))
        except Exception:
            pass

    # -- o contrato que o main.py usa ----------------------------------------

    def speak_para_fila(self, texto: str, on_chunk) -> None:
        """SO A VOZ AO VIVO DA GEMINI (08/10/2026, regra do Diego).

        Queixa: "parou, demorou muito e voltou com uma voz local — essa voz
        local nao deve existir". No registro das 16:03 a conexao de leitura
        ficou muda, o modelo de fala de reserva (outro timbre) entrou, o
        socorro da OpenAI deu 429, a voz LOCAL falou e o ao-vivo ficou desligado
        por 1 minuto — a resposta seguinte saiu na voz de reserva tambem.

        Agora: so o ao-vivo. Travou sem som, a conexao e FECHADA (libera a vaga
        da chave gratis — antes ela era largada aberta) e a frase vai de novo
        numa conexao nova. Nada de outra voz e nada de desligar o ao-vivo por
        um tempo. Se nem assim sair, quem chamou deixa a resposta escrita.
        """
        saiu = {"sim": False}

        def _marcado(raw):
            if raw:
                saiu["sim"] = True
                on_chunk(raw)

        ultimo = None
        for tentativa in range(1, self.TENTATIVAS + 1):
            vivo = self._ao_vivo()
            try:
                vivo.speak_para_fila(texto, _marcado, modo="ler")
                if saiu["sim"]:
                    return
                ultimo = RuntimeError("a voz devolveu o turno sem audio")
            except Exception as exc:
                if saiu["sim"]:
                    return          # ja falou: nunca repetir a frase
                ultimo = exc
            try:
                from main import _voice_diag  # noqa: PLC0415
                _voice_diag("voz_tentando_de_novo",
                            f"tentativa {tentativa}: {str(ultimo)[:80]} — conexao nova")
            except Exception:
                pass
            try:
                asyncio.run_coroutine_threadsafe(
                    vivo._reset_persistent_session(), vivo._garantir_laco()).result(timeout=5)
            except Exception:
                pass
        raise ultimo or RuntimeError("a voz ao vivo nao saiu")

    # A conexao ja tenta 2 vezes por dentro (`_corrida_unica`, conexao nova a
    # cada vez): uma volta aqui = ate ~7 s antes de a resposta ficar escrita.
    TENTATIVAS = 1

    RESERVA_MAX_S = 6.0

    # QUANDO A RESERVA ENTRA NA CORRIDA (05/09/2026, noite, terceira medicao).
    #
    # Antes ela era o degrau SEGUINTE: o ao-vivo tinha 8 s para dar sinal, e so
    # depois a reserva ganhava os seus 6 s. Somando, o Diego esperou 16,3 s e
    # nao ouviu NADA — o pior dos dois mundos.
    #
    # Agora a reserva COMECA JUNTO, aos 3,5 s de silencio, e as tres correm com
    # o mesmo portao: as duas conexoes do ao-vivo e ela. Quem entregar primeiro
    # leva o turno; as outras entregam no vazio. Como a reserva e o MESMO
    # timbre, o dono nao percebe qual atendeu.
    #
    # 3,5 s e depois da corrida do ao-vivo (que abre por volta de 2,1 s): numa
    # fala normal, de 1,2 a 1,9 s, a reserva nem chega a ser chamada — e ela e
    # cobrada POR PEDIDO, entao acionar a toa custaria a cota do dia.
    PRAZO_PARA_A_RESERVA_S = 3.5

    def _reserva_com_relogio(self, texto: str, on_chunk, saiu) -> None:
        """A reserva com prazo; estourou, a voz local assume o turno.

        O PORTAO E OBRIGATORIO. Se a reserva acordar DEPOIS de a voz local ter
        comecado, os dois audios cairiam na mesma fila e o dono ouviria a frase
        duas vezes — armadilha que ja pegou este projeto em 30/08/2026.
        """
        import threading as _th  # noqa: PLC0415
        dono = {"quem": None}
        falou = _th.Event()

        def portao(qual):
            def _entrega(raw):
                if not raw:
                    return
                if dono["quem"] is None:
                    dono["quem"] = qual
                    falou.set()
                if dono["quem"] != qual:
                    return          # a perdedora entrega no vazio
                saiu["sim"] = True
                on_chunk(raw)
            return _entrega

        erro = {}

        def _tentar_reserva():
            try:
                self._de_reserva().speak_para_fila(texto, portao("reserva"))
            except Exception as exc:
                erro["exc"] = exc
            finally:
                falou.set()

        thread = _th.Thread(target=_tentar_reserva, daemon=True)
        thread.start()
        falou.wait(self.RESERVA_MAX_S)
        if dono["quem"] == "reserva":
            thread.join()           # deixa ela terminar de entregar a frase
            return

        # A RESERVA ESTOUROU O PRAZO — E NAO HA VOZ SUBSTITUTA (05/09/2026).
        #
        # Aqui existia um socorro com a voz local (Kokoro). O Diego mandou tirar:
        # *"por que voce nao retira 100% dessa voz local que nao iremos mais
        # usar"*. E ele tem razao pelos numeros — das 11 vozes da engrenagem,
        # ZERO sao locais; todas sao da Gemini. A voz local nao e mais um
        # produto, era so um remendo, e o remendo trocava a PESSOA no meio da
        # conversa, que e a coisa que ele mais reclama.
        #
        # Entao a decisao passou a ser: ou fala com a voz DELE, ou nao fala —
        # e quem chama escreve a resposta no chat, para nada se perder. Ver o
        # tratamento de `mudez_passageira` no `_falar_local` do main.py.
        try:
            from main import _voice_diag  # noqa: PLC0415
            _voice_diag("voz_reserva_estourou",
                        f"a reserva passou de {self.RESERVA_MAX_S:.0f}s"
                        + (f": {str(erro['exc'])[:60]}" if erro else ""))
        except Exception:
            pass

    async def stream_audio_persistent(self, text: str, on_chunk) -> str:
        import time as _t  # noqa: PLC0415
        saiu = {"sim": False}

        def _marcado(raw):
            if raw:
                saiu["sim"] = True
            on_chunk(raw)

        # So o ao-vivo (ver `speak_para_fila`): nada de modelo de reserva.
        return await self._ao_vivo().stream_audio_persistent(text, _marcado)

    def aquecer_em_fundo(self) -> None:
        """Abre as conexoes do ao-vivo sem segurar quem chamou.

        Sincrona de proposito: e chamada de threads do modo local (ao abrir o
        programa e enquanto o cerebro pensa), onde nao ha laco de eventos.
        """
        import time as _t  # noqa: PLC0415
        if _t.time() < VozCharon._vivo_ate:
            return None
        try:
            self._ao_vivo().aquecer_em_fundo()
        except Exception as exc:
            try:
                from main import _voice_diag  # noqa: PLC0415
                _voice_diag("charon_nao_aqueceu", str(exc)[:110])
            except Exception:
                pass
        return None

    async def warm_persistent_session(self) -> None:
        """Abre a sessao do ao-vivo antes da primeira fala.

        Falha aqui NAO derruba nada nem cala o ao-vivo: a sessao pode subir na
        hora da fala. Aquecer e cortesia, nao requisito.
        """
        import time as _t  # noqa: PLC0415
        if _t.time() < VozCharon._vivo_ate:
            return None
        try:
            return await self._ao_vivo().warm_persistent_session()
        except Exception as exc:
            try:
                from main import _voice_diag  # noqa: PLC0415
                _voice_diag("charon_nao_aqueceu", str(exc)[:110])
            except Exception:
                pass
            return None
