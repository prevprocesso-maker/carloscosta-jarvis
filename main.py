import os as _os
import sys as _sys_inicio


# ── LINUX: SEMPRE PELO LANCADOR DO JARVIS (26/09/2026) ─────────────────────
#
# O cliente do Big Linux abria pelo terminal com `python3 .../app/main.py` (uma
# IA de fora mandou ele fazer assim). Era o Python do SISTEMA: sem o
# faster_whisper e as outras bibliotecas do venv, e sem o cadeado do jarvis.sh.
# Com o atalho ja aberto subiam DOIS JARVIS, que brigavam pela porta 8000, pelo
# relay ("dispositivo duplicado") e pelo WhatsApp (401, deslogado).
# Aberto fora do venv, ele se entrega ao jarvis.sh, que faz tudo do jeito certo.
def _linux_pelo_lancador() -> None:
    if not _sys_inicio.platform.startswith("linux"):
        return
    try:
        app = _os.path.dirname(_os.path.abspath(__file__))
        raiz = _os.path.dirname(app)
        venv = _os.path.join(raiz, "venv")
        lancador = _os.path.join(raiz, "jarvis.sh")
        if not (_os.path.isdir(venv) and _os.path.isfile(lancador)):
            return
        if _os.path.realpath(_sys_inicio.prefix) == _os.path.realpath(venv):
            return
        print("[JARVIS] aberto fora do Python dele; abrindo pelo jarvis.sh")
        _os.execv("/bin/bash", ["bash", lancador])
    except Exception:
        pass


_linux_pelo_lancador()

# Carrega o filtro antes de qualquer biblioteca pesada. Isso impede flood no terminal
# causado por warnings de sounddevice/NumPy/DDG sem esconder erros reais.
_os.environ.setdefault("PYTHONWARNINGS", "ignore")
_os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
_os.environ.setdefault("GLOG_minloglevel", "3")
try:
    import sitecustomize as _mark_quiet  # noqa: F401
except Exception:
    pass

import warnings as _warnings
_warnings.simplefilter("ignore")


# ── UM `print` NUNCA PODE DERRUBAR UMA TAREFA (05/09/2026) ─────────────────
#
# O JARVIS roda no `pythonw.exe`. Dependendo de como ele foi aberto, a saida de
# texto ou nao existe ou fica presa na tabela cp1252 do Windows — e ai
# `print("[Vision] ⚠️ ...")` levanta UnicodeEncodeError. O emoji esta em quase
# toda mensagem de erro do projeto, sempre DENTRO de um `except`, entao o
# estouro acontece no pior lugar possivel: mata o socorro que vinha na linha
# seguinte. Foi assim que a analise de tela morreu calada no teste do Diego —
# o `print` do erro da Gemini estourou antes de o Codex ser chamado.
#
# Duas linhas resolvem para o programa inteiro: escrever em UTF-8 e, se o
# caractere nao couber, trocar por "?" em vez de levantar.
def _saida_de_texto_a_prova_de_emoji() -> None:
    import io as _io       # noqa: PLC0415
    import sys as _sys     # noqa: PLC0415
    for nome in ("stdout", "stderr"):
        fluxo = getattr(_sys, nome, None)
        if fluxo is None:                      # pythonw sem console nenhum
            setattr(_sys, nome, _io.StringIO())
            continue
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:                               # fluxo antigo, sem reconfigure
                setattr(_sys, nome, _io.TextIOWrapper(
                    fluxo.buffer, encoding="utf-8", errors="replace", line_buffering=True))
            except Exception:
                setattr(_sys, nome, _io.StringIO())


_saida_de_texto_a_prova_de_emoji()

import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen
# ─────────────────────────────────────────────────────────────────────────────

# ── FORA DO WINDOWS, `os.startfile` NAO EXISTE (05/09/2026, porte para Mac) ──
#
# Ele nao da erro de import: some do modulo `os` e estoura `AttributeError` na
# hora do uso. Sao OITO lugares chamando ele direto — abrir pasta, abrir
# programa, abrir arquivo baixado, abrir o site gerado —, e o de abrir pasta e
# um dos pedidos mais comuns do dono.
#
# Mesma ideia do remendo do `Popen` acima, e pelo mesmo motivo: um lugar so,
# em vez de oito. No Windows NADA muda — o `if` nem entra. No Mac, `os.startfile`
# passa a existir e chama o `open` do sistema.
if _platform.system() != "Windows":
    import os as _os_para_remendar

    if not hasattr(_os_para_remendar, "startfile"):
        from core.plataforma import abrir_no_sistema as _abrir_no_sistema
        _os_para_remendar.startfile = lambda caminho, *a, **k: _abrir_no_sistema(caminho)
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import atexit
import re
import unicodedata
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import json
import sys
# NAO reduzir o rodizio de tarefas do Python aqui.
#
# Ja tentei `sys.setswitchinterval(0.0005)` para acabar com o picote da voz
# durante uma pesquisa. Em teste isolado parecia otimo (7 cortes -> 0). No
# JARVIS de verdade, que roda com ~37 threads, o efeito foi o oposto: trocar
# de tarefa 10x mais vezes por segundo atrapalhou a conexao de rede e as
# respostas passaram a demorar MINUTOS. O cliente perguntava a hora e recebia
# a resposta quatro minutos depois, com a hora ja errada.
#
# Aplicado 28/08 13:09, revertido 28/08 13:33.
import traceback
from datetime import datetime
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.whatsapp_bridge   import (
    whatsapp_action, poll_whatsapp_alerts, transcribe_audio_path,
    poll_due_scheduled_messages, poll_whatsapp_agent_events,
    process_whatsapp_agent_event,
)
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process as screen_process_action
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.codex_remote      import remote_codex_job
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.vision_control    import vision_control
from actions.usar_computador   import usar_computador, _parece_duvida
from actions.organizar_pasta   import organizar_pasta
from actions.desfazer          import desfazer_tool, desfazer_ultima, ha_algo_recente
from actions.digitar_na_tela   import digitar_na_tela
from actions.desenhar_na_tela  import desenhar_na_tela
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
_TEST_API_CONFIG = _os.environ.get("JARVIS_API_CONFIG", "").strip()
API_CONFIG_PATH = Path(_TEST_API_CONFIG) if _TEST_API_CONFIG else BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
VOICE_DIAG_PATH = BASE_DIR / "logs" / "voice_session.log"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024
# 40 ms de áudio por pacote. O Gemini Live recomenda pacotes entre 20 e 40 ms
# para manter baixa latência sem aumentar demais o overhead do websocket.
MIC_CHUNK_SIZE      = 640    # desfeito 13:35: 1024 nao trouxe ganho medido

# Modelo do reconhecimento de fala local. Era "base" (0,5 s) e trocou para
# "small" (1,7 s) em 30/08/2026 por causa de um prejuizo real: o `base` ouviu
# "O Jarvis" como "O Tiaves" e o cerebro foi PESQUISAR "Tiaves" na internet,
# gastando mais de 80 s num trabalho do servidor por causa de uma palavra
# inventada. Um segundo a mais por fala sai muito mais barato que isso.
_MODELO_WHISPER = "small"          # reserva, quando nao houver placa

# OUVIDO NA PLACA DE VIDEO (30/08/2026).
#
# O `small` no processador levava 1,59 s por frase e confundia palavra comum:
# "Bom dia JARVIS" virou "Bom dia, Visp" e "Corinthians" virou "Gorinjuez". O
# `large-v3` na RTX 3080 leva 0,39 s — quatro vezes mais rapido E mais certeiro,
# que e o que importa com microfone ruim.
_MODELO_WHISPER_GPU = "large-v3"

# PISO DE PICO DA FALA (01/09/2026). Ver `scratchpad/som_de_longe.py`.
#
# Medido em 230 falas reais: a voz do dono nunca desceu de 0,092; o celular
# tocando video de longe nunca passou de 0,058. 0,07 fica no vao entre as duas.
# Isto NAO e o portao do microfone (que decide se grava) — e o corte de quem
# merece acordar o cerebro depois de gravado.
# 0,09 desde 01/09/2026, com os numeros do proprio uso dele nesta noite:
#     voz do Diego .... 0,110 a 0,184 (mediana 0,140)
#     ruido de fundo .. 0,042 a 0,072
# O piso fica 20% abaixo da fala mais baixa dele e 25% acima do ruido mais alto.
# Trocou de microfone ou de sala? Medir de novo — o registro `som_de_longe`
# mostra o pico de tudo que foi descartado.
_PICO_MINIMO_DA_FALA = 0.09
# FAIXA DE RESGATE (08/09/2026): entre este valor e o piso acima, a fala nao e
# mais jogada fora pelo volume — ela e transcrita e quem decide e a confianca do
# whisper. E o que faz o microfone longe da boca voltar a funcionar. Abaixo
# daqui e ruido de sala e continua sendo descarte seco, sem custar o ouvido.
# 0,045 fica logo acima do ruido de fundo medido na sala dele (0,042 a 0,072 era
# o PICO do ruido; o corte por confianca cobre o que passar).
_PICO_DE_RESGATE = 0.045


def _preparar_cuda() -> bool:
    """Poe as DLLs do CUDA no caminho e diz se a placa esta utilizavel.

    As bibliotecas vem dos pacotes `nvidia-cublas-cu12` e `nvidia-cudnn-cu12`.
    O ctranslate2 as procura pelo nome, entao a pasta precisa entrar no PATH
    ANTES de o faster_whisper ser importado — senao o erro e
    "Library cublas64_12.dll is not found".
    """
    import glob as _glob  # noqa: PLC0415
    import os as _os      # noqa: PLC0415
    import sys as _sys    # noqa: PLC0415
    try:
        base = _os.path.join(_os.path.dirname(_sys.executable),
                             "Lib", "site-packages", "nvidia")
        pastas = _glob.glob(_os.path.join(base, "*", "bin"))
        if not pastas:
            return False
        _os.environ["PATH"] = ";".join(pastas) + ";" + _os.environ.get("PATH", "")
        for pasta in pastas:
            try:
                _os.add_dll_directory(pasta)
            except Exception:
                pass
        import ctranslate2  # noqa: PLC0415
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _tirar_grave(amostras):
    """Tira o DC e o ronco abaixo de 80 Hz da fala inteira.

    Devolve (amostras limpas, RMS do que saiu). Existe por causa do cliente
    DESKTOP-HDU4A58 (17/09/2026): a entrada de microfone da placa-mae dele
    entrega um desvio fixo de 887 (0,027 do fundo de escala) mais ronco abaixo
    de 30 Hz. Esse desvio entrava no PICO da fala (o `som_de_longe` media o
    desvio, nao a voz), no ganho da normalizacao e no audio que vai para o
    ouvido. Voz nao tem nada abaixo de 80 Hz que o ouvido use.
    """
    import numpy as _np  # noqa: PLC0415
    if amostras.size < SEND_SAMPLE_RATE // 10:
        return amostras, 0.0
    espectro = _np.fft.rfft(amostras)
    espectro[:int(80 * amostras.size / SEND_SAMPLE_RATE) + 1] = 0
    limpas = _np.fft.irfft(espectro, n=amostras.size).astype(_np.float32)
    return limpas, float(_np.sqrt(((amostras - limpas) ** 2).mean()))


def _abrir_whisper():
    """Devolve o modelo de fala: placa se der, processador se nao der."""
    from faster_whisper import WhisperModel  # noqa: PLC0415
    if _preparar_cuda():
        try:
            modelo = WhisperModel(_MODELO_WHISPER_GPU, device="cuda",
                                  compute_type="float16")
            print(f"[JARVIS] ouvido: {_MODELO_WHISPER_GPU} na placa de video")
            return modelo, True
        except Exception as exc:
            print(f"[JARVIS] placa recusou o ouvido ({str(exc)[:90]}); indo de processador")
    # MODELO QUE VEM NO INSTALADOR. Sem isto o cliente baixa 464 MB do
    # HuggingFace na primeira fala — e fica sem ouvido se a internet falhar
    # justo ali. `models/whisper-small` e empacotado junto com o app.
    local = Path(__file__).resolve().parent / "models" / "whisper-small"
    alvo = str(local) if (local / "model.bin").exists() else _MODELO_WHISPER
    # NUCLEOS DO PROCESSADOR (13/09/2026, cliente com Ryzen 5 5600G). O padrao
    # do ctranslate2 usa 4 threads; com metade dos nucleos logicos a frase saiu
    # mais rapido (2,11 -> 2,01 s). Nunca abaixo de 4.
    import os as _os  # noqa: PLC0415
    _nucleos = max(4, (_os.cpu_count() or 8) // 2)
    modelo = WhisperModel(alvo, device="cpu", compute_type="int8",
                          cpu_threads=_nucleos)
    print(f"[JARVIS] ouvido: {_MODELO_WHISPER} no processador"
          + (" (modelo local)" if alvo != _MODELO_WHISPER else ""))
    return modelo, False

# Palavras que o reconhecimento erra por serem nome proprio, marca ou giria.
# Escrito como frase solta de proposito: e assim que o `initial_prompt` do
# whisper funciona melhor, imitando um trecho de conversa anterior.
#
# Para ensinar um nome novo: acrescente aqui e reinicie. Nao precisa de mais nada.
# Nomes que o reconhecimento erra trocando uma letra. A correcao e feita DEPOIS
# da transcricao, por semelhanca: "MULTRON" e "Moutron" viram "MuTron".
#
# Isto e diferente do `_VOCABULARIO`, que so inclina o whisper. Aqui e conserto
# de verdade, palavra por palavra, para o caso de ele errar mesmo assim.
# Entradas de UMA palavra so: a correcao trabalha palavra por palavra, entao
# "Cazé TV" nunca casaria — o certo e listar "Cazé" sozinho.
# SO OS NOMES DE TODO MUNDO FICAM AQUI (01/10/2026). A lista tinha os nomes do
# DIEGO (Diego, Caze, Casimiro, AlfaMu, MuTron, BotZapBR) e valia para todo
# cliente: no PC de um Diogo, "o Diogo chegou" virava "o Diego chegou". Os nomes
# de cada pessoa agora vem do PC dela — o nome cadastrado na engrenagem, o nome
# da voz escolhida e o `config/vocabulario.txt` daquela maquina. Ver
# `_nomes_do_usuario`.
_NOMES_CERTOS = [
    "JARVIS", "Codex", "WhatsApp", "YouTube", "Chrome", "Spotify", "Steam",
    "Windows", "Netflix", "Instagram", "Facebook", "TikTok", "Discord",
    "Telegram", "Google",
]
_NOME_DA_VOZ = ""


def _nomes_do_usuario() -> list:
    """Os nomes proprios DESTE PC: gerais + dono + voz + vocabulario da maquina."""
    import re as _re  # noqa: PLC0415
    nomes = list(_NOMES_CERTOS)
    try:
        from core.dono import carregar as _carregar_dono  # noqa: PLC0415
        d = _carregar_dono() or {}
        for chave in ("como_chamar", "apelido"):
            v = str(d.get(chave) or "").strip()
            if v and " " not in v:
                nomes.append(v)
    except Exception:
        pass
    if _NOME_DA_VOZ:
        nomes.append(_NOME_DA_VOZ)
    try:
        extra = (Path(__file__).resolve().parent / "config" / "vocabulario.txt"
                 ).read_text(encoding="utf-8")
        # So palavra com MAIUSCULA: e nome proprio. "pizzaria" e "boleto" do
        # vocabulario ajudam o reconhecimento, mas nao sao para corrigir.
        for palavra in _re.findall(r"[A-ZÀ-Ý][\wÀ-ÿ]{3,}", extra):
            nomes.append(palavra)
    except Exception:
        pass
    vistos, saida = set(), []
    for n in nomes:
        if n.lower() not in vistos:
            vistos.add(n.lower())
            saida.append(n)
    return saida


def _corrigir_nomes(texto: str) -> str:
    """Troca palavra parecida pelo nome certo, sem mexer no resto da frase."""
    if not texto:
        return texto
    import difflib  # noqa: PLC0415
    import re as _re  # noqa: PLC0415

    baixo = {n.lower(): n for n in _nomes_do_usuario()}
    def _troca(m):
        palavra = m.group(0)
        if palavra.lower() in baixo:
            return baixo[palavra.lower()]
        # `cutoff` alto de proposito: 0.75 pega MULTRON/Moutron -> MuTron sem
        # transformar palavra comum em nome proprio.
        perto = difflib.get_close_matches(palavra.lower(), baixo.keys(), n=1, cutoff=0.75)
        return baixo[perto[0]] if perto else palavra

    # So palavras de 4 letras ou mais: abaixo disso a semelhanca engana.
    return _re.sub(r"[A-Za-zÀ-ÿ]{4,}", _troca, texto)


# O VOCABULARIO E DE QUEM USA, NAO DO DIEGO (14/09/2026).
#
# Era uma frase fixa com as palavras do Diego ("assistente do Diego", Cazé TV,
# Casimiro, AlfaMu, BotZapBR...) e ia igual para todo cliente. No PC de um
# cliente o whisper passou a INVENTAR essas palavras ("www.Casimiro.com.br",
# "MuOnline ReVoZa") e o nome da voz dele, Leda, saia "Lida" e "Leida" — o
# nome nem estava no prompt. Medido no PC dele: com "Conversa com Leda" no
# prompt, "Leda, me conta uma historia" saiu certo.
#
# Agora vai o nome da voz escolhida + palavras gerais. Palavras de UMA maquina
# (as do Diego) ficam em `config/vocabulario.txt`, que nao sai no instalador.
_VOCABULARIO_GERAL = ("Falamos sobre Bloco de Notas, Chrome, YouTube, WhatsApp, "
                      "Spotify, Google, Instagram, Discord.")


def _montar_vocabulario(nome: str = "JARVIS") -> str:
    nome = str(nome or "").strip() or "JARVIS"
    extra = ""
    try:
        extra = (Path(__file__).resolve().parent / "config" / "vocabulario.txt"
                 ).read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return " ".join(p for p in (f"Conversa com {nome}, assistente pessoal.",
                                _VOCABULARIO_GERAL, extra) if p)


_VOCABULARIO = _montar_vocabulario()


def _atualizar_vocabulario(nome: str) -> str:
    """Refaz o vocabulario com o nome da voz de agora e devolve o texto.

    Global de proposito: o filtro de eco (`_e_eco_do_vocabulario`) compara com
    ESTE mesmo texto, entao prompt e filtro nunca ficam diferentes.
    """
    global _VOCABULARIO, _NOME_DA_VOZ
    _VOCABULARIO = _montar_vocabulario(nome)
    # O nome da voz tambem e nome certo: "Lida" e "Leida" viram "Leda".
    _NOME_DA_VOZ = str(nome or "").strip() if " " not in str(nome or "").strip() else ""
    return _VOCABULARIO

# ALUCINACAO DE SILENCIO — frases que o whisper inventa quando nao ha fala.
#
# Ele aprendeu com legenda de video, entao no vazio devolve o que fecha
# legenda. Sao frases que ninguem diz para um assistente pessoal: se vier so
# isso, nao foi o dono que falou.
_ALUCINACOES = {
    "obrigado por assistir", "obrigado por assistir!", "obrigada por assistir",
    "obrigado por assistirem", "muito obrigado por assistir",
    "inscreva-se no canal", "se inscreva no canal", "deixe seu like",
    "curta e compartilhe", "nao se esqueca de se inscrever",
    "ate a proxima", "ate o proximo video", "ate mais", "tchau tchau",
    "legendas pela comunidade amara.org", "legendado por",
    "legendas por", "traduzido por", "revisao por",
    "obrigado", "obrigada", "valeu", "fim", "tchau",
    "musica", "aplausos", "risos", "silencio",
    "compartilhe o video", "deixe seu comentario",
    # Fecho de video que o whisper repete no silencio. "E assim ficou pronto"
    # apareceu colado com "Caze TV" no chat do Diego em 01/09/2026, com ele
    # calado — e como e frase inteira, so cai aqui.
    "e assim ficou pronto", "assim ficou pronto", "e isso ai", "era isso",
    "e isso", "pronto", "espero que tenham gostado", "nos vemos na proxima",
}


def _so_fantasma(texto: str) -> bool:
    """A frase e feita SO de pedacos fantasma?

    Cada filtro sozinho olha a frase inteira, e por isso dois fantasmas colados
    escapavam: "Caze TV  E assim ficou pronto" tem cinco palavras e nao casa com
    nenhuma lista. Quebrado em pedacos, cada metade e reconhecida.

    Fala de verdade sempre tem pelo menos um pedaco que nao esta em lista
    nenhuma — e ai a frase passa inteira.
    """
    import re as _re
    bruto = str(texto or "").strip()
    if not bruto:
        return True
    # Quebra por pontuacao E por espaco duplo, que e como o whisper separa duas
    # invencoes emendadas.
    pedacos = [p.strip() for p in _re.split(r"[.!?;]+|\s{2,}", bruto) if p.strip()]
    if len(pedacos) < 2:
        return False          # um pedaco so ja e tratado pelos outros filtros
    for pedaco in pedacos:
        if not (_e_eco_do_vocabulario(pedaco) or _e_alucinacao_de_silencio(pedaco)):
            return False
    return True


def _e_alucinacao_de_silencio(texto: str) -> bool:
    """A transcricao inteira e uma frase de fim de legenda?

    So bate quando e a frase TODA. "Obrigado por assistir" sozinho e
    alucinacao; "obrigado, JARVIS, por assistir minha tela" nao entra na lista
    e passa.
    """
    import unicodedata as _u
    s = _u.normalize("NFD", str(texto or "").lower())
    s = "".join(c for c in s if _u.category(c) != "Mn")
    s = "".join(c if (c.isalnum() or c.isspace() or c in ".!-") else " " for c in s)
    s = " ".join(s.split()).strip(" .!")
    return s in _ALUCINACOES


def _e_outra_escrita(texto: str) -> bool:
    """Tem letra fora do alfabeto latino (japones, coreano, chines, cirilico)?

    22/09/2026, cliente DESKTOP-3FKP5JF: risada, "hum" e o rabo da voz do
    JARVIS na caixa voltavam da ponte como 'うん。', '웃으', 'Как за?' e
    '這是大夫' — mesmo com o idioma fixo em pt na VPS. O JARVIS respondia a
    isso e, para o cliente, parecia que nao tinha ouvido a pergunta dele.
    O dono fala portugues: letra de outra escrita nunca e fala dele.
    """
    import unicodedata as _u
    return any(c.isalpha() and not _u.name(c, "").startswith("LATIN")
               for c in str(texto or ""))


# ── CLIENTE EM INGLES: FALA EM OUTRA LINGUA E RUIDO (26/09/2026) ──────────
#
# Cliente do Canada (idioma "en"). Mesmo com o ouvido da VPS fixo em ingles,
# som fraco ou de longe voltava como 'Ja paslėzorywa', 'Ik ben formal',
# 'Sí que estaba aquí', 'Ja, auch...' — e o JARVIS respondia a isso. O dono
# fala ingles: frase com acento, ou feita de palavras de outra lingua sem
# nenhuma do ingles, nao e ele falando com o JARVIS.
_PALAVRAS_EN = frozenset("""
i i'm im you your yours the an is are am was were be been can could would will
should do does did don't dont what whats what's how why when where who which hey
hello hi ok okay yes yeah yep no nope not it it's its this that that's there here
me my mine we our they them he she his her to of in on at for with and or but so
if then now please thank thanks sorry good great fine nice see say tell show open
close turn stop start go get give make let let's lets want need like know think
look find search play jarvis sir morning evening night afternoon bye goodbye oh
well right just more some any all one two time today tomorrow weather music
computer screen volume up down off google chrome firefox spotify youtube
whatsapp netflix discord telegram steam
""".split())
_PALAVRAS_DE_OUTRA_LINGUA = frozenset("""
voce eu nao sim que para pode fazer um uma aqui tenho estou esta consegue ouvir
bastante forte pedir obrigado tudo bem isso como onde quando porque muito agora
entao ja estaba si yo tengo puedes hola gracias ik ben het een niet wat ich nicht
und auch bin ist das der die salut oui merci je suis bonjour mais avec
""".split())


def _e_outra_lingua(texto: str) -> bool:
    """Com o JARVIS em ingles, a transcricao veio em outra lingua?"""
    try:
        if not _em_ingles():
            return False
    except Exception:
        return False
    t = str(texto or "")
    if re.search(r"[À-ÖØ-öø-ÿĀ-ſ]", t):      # acento: o ingles nao tem
        return True
    palavras = re.findall(r"[a-z']+", t.lower())
    en = sum(1 for p in palavras if p in _PALAVRAS_EN)
    fora = sum(1 for p in palavras if p in _PALAVRAS_DE_OUTRA_LINGUA)
    if fora >= 1 and en == 0:
        return True
    # Duas palavras ou mais e NENHUMA do ingles: 'Nowokyta solary.' — ruido
    # que o ouvido transformou em palavra inventada (log de 26/09, 21:37).
    return len(palavras) >= 2 and en == 0


def _e_eco_do_vocabulario(texto: str) -> bool:
    """A transcricao e so um pedaco do `_VOCABULARIO`, sem ninguem ter falado?

    O `initial_prompt` inclina o whisper para estas palavras — util quando ha
    fala, veneno quando ha silencio: ele devolve o proprio vocabulario com toda
    a confianca do mundo, e o filtro de `no_speech_prob` nao pega.

    So vale para frase CURTA e SEM VERBO. "Caze TV" sozinho e eco; "abre a Caze
    TV" e pedido de verdade e passa.
    """
    import unicodedata as _u

    def _limpo(s: str) -> str:
        s = _u.normalize("NFD", str(s or "").lower())
        s = "".join(c for c in s if _u.category(c) != "Mn")
        return " ".join(c for c in s if c.isalnum() or c.isspace()).split() and \
            " ".join("".join(c for c in s if c.isalnum() or c.isspace()).split()) or ""

    alvo = _limpo(texto)
    if not alvo:
        return True
    palavras = alvo.split()
    # O NOME DA VOZ SOZINHO E CHAMADO, NAO ECO (14/09/2026). O nome esta no
    # vocabulario, entao "JARVIS." ou "Leda." dito sozinho caia aqui e sumia:
    # no log do cliente, "JARVIS." com sem_fala 0,02 nunca chegou. O eco de
    # verdade continua barrado pelas peneiras de confianca do whisper.
    if alvo in globals().get("_NOMES_ASSISTENTE", ()):
        return False
    # A MOLDURA DO PROMPT VOLTANDO (23/09/2026). O ouvido do Diego ficou
    # repetindo "Falamos sobre Caze TV." com ele calado — e o comeco do
    # `config/vocabulario.txt` dele, palavra por palavra. Passava porque "sobre"
    # esta em `_VERBOS_QUE_SALVAM` (para salvar "fala sobre X"). Trecho seguido
    # do vocabulario que traz a MOLDURA ("falamos sobre", "conversa com",
    # "assistente pessoal") nao e fala de ninguem, de qualquer tamanho.
    vocab_limpo = f" {_limpo(_VOCABULARIO)} "
    # Moldura + palavra do vocabulario, mesmo que a moldura nao esteja colada
    # nela no prompt: "falamos sobre Caze TV" com o prompt "Falamos sobre
    # Bloco de Notas... Caze TV, Casimiro".
    for moldura in ("falamos sobre ", "conversa com ", "conversa do "):
        if alvo.startswith(moldura):
            resto = alvo[len(moldura):].strip()
            if resto and len(resto.split()) <= 6 and f" {resto} " in vocab_limpo:
                return True
    # Sem moldura, TRES ou mais palavras seguidas do vocabulario, na mesma
    # ordem, tambem e o prompt voltando ("Caze TV, Casimiro, AlfaMu, MuOnline").
    if (len(palavras) >= 2 and f" {alvo} " in vocab_limpo
            and (len(palavras) >= 3
                 or any(m in f" {alvo} " for m in (" falamos sobre ", " conversa com ",
                                                    " conversa do ", " assistente pessoal ")))):
        return True
    if len(palavras) > 4:
        return False          # frase de verdade: deixa passar
    # Tem verbo? Entao e pedido, nao eco.
    for p in palavras:
        if p in _VERBOS_QUE_SALVAM or p.endswith(("ar", "er", "ir", "ou", "ei")):
            return False
    # PALAVRA INTEIRA, NAO PEDACO DE PALAVRA (13/09/2026). Era `alvo in
    # vocabulario` puro, e "sim" e pedaco de "caSIMiro": o cliente respondeu
    # "Sim" tres vezes e as tres foram jogadas fora como eco. Mesmo destino
    # para "ok" (kOKoro). Agora so conta sequencia de palavras inteiras.
    return f" {alvo} " in f" {_limpo(_VOCABULARIO)} "


# ─────────────────────────────────────────────────────────────────────────────
# COMO O DONO CHAMA O ASSISTENTE — e por que isso quebrava tudo
# ─────────────────────────────────────────────────────────────────────────────
#
# Defeito achado em 05/09/2026, com o Diego usando a voz Sulafat: ele pediu
# tres vezes "abre minha pasta de download" e as tres vezes o JARVIS abriu o
# EXPLORADOR DE ARQUIVOS, gastando 6,5 s de rede em cada uma.
#
# A causa nao estava no abrir de pasta: estava no NOME. Os atalhos rapidos
# (`_RE_ABRIR`, `_RE_FECHAR`, `_PARAR_PC`) so conheciam "jarvis" e "friday".
# Com a voz Sulafat escolhida, "Sulafat, abre minha pasta de download" nao
# casava com atalho nenhum, caia no cerebro, e o modelo escolhia a ferramenta
# errada. Ou seja: TODO atalho rapido morria em silencio para quem trocasse a
# voz — e trocar a voz e um botao na engrenagem.
#
# Agora a lista de nomes sai do proprio `core.personas`, entao voz nova ja
# nasce funcionando. E o nome tambem e tirado do FIM da frase ("abre minha
# pasta de download, Sulafat"), onde ele estragava a busca da pasta: o nome
# entrava junto com o nome da pasta e nao achava caminho nenhum.

def _nomes_do_assistente() -> set:
    nomes = {"jarvis", "friday"}
    try:
        from core.personas import NOME_DA_VOZ  # noqa: PLC0415
        nomes |= {str(v).strip().lower() for v in NOME_DA_VOZ.values() if str(v).strip()}
    except Exception:
        pass
    return nomes


_NOMES_ASSISTENTE = _nomes_do_assistente()
# Alternancia para regex, do nome mais longo para o mais curto (senao "orus"
# casaria antes de "orusX" numa lista futura).
_ALT_NOMES = "|".join(sorted((re.escape(n) for n in _NOMES_ASSISTENTE),
                             key=len, reverse=True))


# O NOME TEM DE SER RECONHECIDO PELO SOM (05/09/2026, noite).
#
# Com a voz Orus escolhida, o ouvido escreveu "Horus" e "AORUS". Comparando
# letra por letra, nenhum dos dois e "orus" — entao o atalho nao reconhecia o
# vocativo, a frase inteira ia para o cerebro, e o desastre foi em cascata:
#
#     "O Horus abre minha pasta"  -> abriu uma pasta chamada "Horus"
#     "O AORUS abre minha pasta"  -> abriu um programa chamado "AORUS"
#     "O Horus, abre minha pasta de download" -> "precisa ativar o computador"
#
# A ultima e a pior: sem o atalho, o modelo escolheu a ferramenta de MEXER NA
# TELA (que exige "ativar computador") para uma coisa simples como abrir pasta,
# e ficou recusando tres vezes seguidas.
#
# Pelo som, "horus" e "orus" sao a MESMA palavra (o "h" do comeco nao soa), e
# "aorus" fica a 0,89 de semelhanca. E o mesmo motor de som do corretor de fala.
# ── IDIOMA DO JARVIS (24/09/2026) ─────────────────────────────────────────
# Portugues por padrao. O cliente do Canada recebe `config/idioma.json` com
# {"idioma": "en"} e ai tudo que o JARVIS fala e ouve passa a ser ingles.
# Ver `core/idioma.py`.
try:
    from core.idioma import (em_ingles as _em_ingles, com_idioma as _com_idioma,
                             no_idioma as _no_idioma, frase as _frase_no_idioma,
                             lingua_do_ouvido as _lingua_do_ouvido)
except Exception:      # pragma: no cover
    def _em_ingles():
        return False

    def _com_idioma(t):
        return t

    def _no_idioma(t):
        return t

    def _frase_no_idioma(t):
        return t

    def _lingua_do_ouvido():
        return "pt"

_SONS_DOS_NOMES = set()
try:
    from core.corretor_fala import som as _som_da_palavra
    _SONS_DOS_NOMES = {_som_da_palavra(n) for n in _NOMES_ASSISTENTE}
except Exception:      # pragma: no cover
    _som_da_palavra = None

# Enfeite que costuma vir GRUDADO no nome: "o Jarvis", "oi Leda", "ei Orus".
_ENFEITE_DO_NOME = {"o", "a", "oi", "ei", "ai", "opa", "olha", "psiu"}


def _e_o_nome_dele(palavra: str) -> bool:
    """Esta palavra e o nome do assistente, mesmo mal ouvido?"""
    if not palavra or _som_da_palavra is None:
        return False
    if palavra.lower() in _NOMES_ASSISTENTE:
        return True
    s = _som_da_palavra(palavra)
    if not s or s in _SONS_DOS_NOMES:
        return bool(s)
    if len(s) < 4:
        return False          # curto demais: semelhanca engana
    from difflib import SequenceMatcher  # noqa: PLC0415
    # 0,85 e alto de proposito: pega "aorus"/"orus" (0,89) e "leida"/"leda"
    # (0,89) sem transformar palavra comum em vocativo.
    return any(SequenceMatcher(None, s, alvo).ratio() >= 0.85
               for alvo in _SONS_DOS_NOMES if len(alvo) >= 4)


def _tira_vocativo(t: str) -> str:
    """Tira o nome do assistente do comeco e do fim da frase ja normalizada."""
    partes = t.split()
    mexeu = True
    while mexeu and partes:
        mexeu = False
        if _e_o_nome_dele(partes[0]):
            partes.pop(0)
            mexeu = True
        elif (len(partes) > 1 and partes[0] in _ENFEITE_DO_NOME
              and _e_o_nome_dele(partes[1])):
            del partes[:2]
            mexeu = True
    while partes and _e_o_nome_dele(partes[-1]):
        partes.pop()
    return " ".join(partes)


# Se qualquer um destes aparecer, a frase e um pedido e nao um eco do prompt.
_VERBOS_QUE_SALVAM = {
    "abre", "abra", "abrir", "fecha", "feche", "poe", "poem", "coloca", "bota",
    "toca", "procura", "busca", "pesquisa", "mostra", "ve", "olha", "quero",
    "qual", "quais", "quanto", "quando", "onde", "como", "porque", "por",
    "o", "que", "e", "sobre", "manda", "faz", "cria", "diz", "fala",
}


_VOICE_DIAG_LOCK = threading.Lock()


def _voice_diag(event: str, detail: str = "") -> None:
    """Log técnico sem transcrições ou chaves para diagnosticar quedas da sessão."""
    try:
        stamp = datetime.now().isoformat(timespec="milliseconds")
        safe_detail = str(detail or "").replace("\x00", "")
        with _VOICE_DIAG_LOCK:
            VOICE_DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with VOICE_DIAG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(f"{stamp}\t{event}\t{safe_detail}\n")
    except Exception:
        pass


def _voice_diag_exception(event: str, exc: BaseException) -> None:
    try:
        detail = "".join(traceback.format_exception(exc)).strip()
    except Exception:
        detail = f"{type(exc).__name__}: {exc}"
    _voice_diag(event, detail)

# ── QUEM MATOU O PROGRAMA? (03/09/2026, no Bob) ──────────────────────
#
# O programa simplesmente sumiu no meio de um teste: nenhuma linha no log,
# nenhum registro de falha do Windows, e do lado de fora parecia que "ele
# fechou sozinho". Rodando por `pythonw.exe` NAO HA CONSOLE: uma excecao nao
# tratada mata o processo em silencio absoluto.
#
# Estes tres ganchos custam nada e transformam "sumiu" em uma linha no mesmo
# log de sempre: excecao na thread principal, excecao em qualquer outra thread,
# e a saida normal (que distingue "fechou" de "quebrou").
def _gancho_excecao(tipo, valor, tb) -> None:
    try:
        _voice_diag("programa_morreu_por_excecao",
                    "".join(traceback.format_exception(tipo, valor, tb))[:1500])
    except Exception:
        pass
    sys.__excepthook__(tipo, valor, tb)


def _gancho_excecao_thread(args) -> None:
    try:
        _voice_diag("thread_morreu",
                    f"{getattr(args.thread, 'name', '?')}: "
                    + "".join(traceback.format_exception(
                        args.exc_type, args.exc_value, args.exc_traceback))[:1200])
    except Exception:
        pass


sys.excepthook = _gancho_excecao
try:
    threading.excepthook = _gancho_excecao_thread
except Exception:
    pass
atexit.register(lambda: _voice_diag("programa_encerrou", "saida normal do processo"))

# ── QUANDO NEM O GANCHO DE EXCECAO PEGA (05/09/2026) ─────────────────────
#
# Os tres ganchos acima cobrem o que o Python consegue ver. Em 05/09 o
# JARVIS morreu no meio de um pedido de site e nao deixou NADA: nem excecao,
# nem linha de saida, nem registro de falha do Windows. Isso e assinatura de
# queda NATIVA — dentro do Qt, do driver de audio ou de uma biblioteca em C —
# e nesse caso o interpretador nem chega a rodar o `excepthook`.
#
# O `faulthandler` e do proprio Python e serve exatamente para isso: ele
# escreve a pilha de TODAS as threads no arquivo quando o processo recebe um
# sinal fatal. Custa zero enquanto nada quebra, e da o unico rastro possivel
# quando quebra. Sem ele, "caiu aqui" continua sendo palavra contra palavra.
try:
    import faulthandler as _fh  # noqa: PLC0415
    _ARQ_QUEDA = BASE_DIR / "logs" / "queda_nativa.log"
    _ARQ_QUEDA.parent.mkdir(parents=True, exist_ok=True)
    # Fica aberto de proposito: o `faulthandler` escreve nele durante a
    # queda, e um arquivo fechado nao serviria de nada nessa hora.
    _FLUXO_QUEDA = open(_ARQ_QUEDA, "a", encoding="utf-8", buffering=1)
    _FLUXO_QUEDA.write(chr(10) + "=== JARVIS aberto em "
                       + datetime.now().isoformat(timespec="seconds") + chr(10))
    _fh.enable(file=_FLUXO_QUEDA, all_threads=True)
except Exception as _exc_fh:
    _voice_diag("faulthandler_fora", str(_exc_fh)[:110])


# ── QUANTO DA VOZ DA GEMINI JA FOI GASTO HOJE (05/09/2026) ──────────────────
#
# Pergunta do Diego, e ela e a certa: "funciona o dia todo eu conversando?".
# A resposta honesta so existe MEDINDO — a cota do ao-vivo e contada por SESSAO
# e por SEGUNDO DE AUDIO, e ate hoje ninguem neste projeto sabia quantos
# segundos um dia de uso gasta de verdade. Sem numero, "acho que dura" e palpite.
#
# Custa uma soma e uma linha no registro. Zera sozinho na virada do dia.
_VOZ_GASTA = {"dia": "", "segundos": 0.0, "falas": 0}


def _contar_voz_da_nuvem(motor: str, segundos: float) -> str:
    """Soma no total do dia e devolve o resumo para o registro."""
    try:
        hoje = datetime.now().strftime("%Y-%m-%d")
        if _VOZ_GASTA["dia"] != hoje:
            _VOZ_GASTA.update({"dia": hoje, "segundos": 0.0, "falas": 0})
        _VOZ_GASTA["segundos"] += float(segundos or 0.0)
        _VOZ_GASTA["falas"] += 1
        return (f" | hoje: {_VOZ_GASTA['falas']} falas, "
                f"{_VOZ_GASTA['segundos']/60:.1f} min de {motor}")
    except Exception:
        return ""


def _nome_falante(ui) -> str:
    """O nome com que ele assina no chat — o da VOZ escolhida.

    Com a voz Nova ele se chama Nova, com a Lia se chama Lia. Assinar
    "Jarvis" com a voz da Nova falando e o tipo de detalhe que denuncia que
    o nome esta escrito na pedra em algum lugar.
    """
    try:
        from core.personas import nome_da_ia  # noqa: PLC0415
        escolha = getattr(ui, "persona_atual", None) or {}
        return nome_da_ia(escolha.get("personagem", "")) or "JARVIS"
    except Exception:
        return "JARVIS"


def _texto_do_erro(exc: BaseException, _fundo: int = 0) -> str:
    """Junta a mensagem da excecao com a de TODAS as suas filhas.

    O `str()` de um ExceptionGroup diz apenas "unhandled errors in a TaskGroup
    (1 sub-exception)" — a mensagem de verdade fica escondida nas filhas. Como o
    caminho de voz roda inteiro dentro de um TaskGroup, praticamente todo erro
    chegava embrulhado assim, e nenhuma das verificacoes por texto do laco de
    reconexao funcionava: cota esgotada nao avisava o cliente, chave invalida
    nao pedia troca, e o "motor morto" nunca era reconstruido — a correcao que
    existia para isso estava desligada na pratica.
    """
    partes = [f"{type(exc).__name__}: {exc}"]
    if _fundo < 5:
        for filha in (getattr(exc, "exceptions", None) or []):
            partes.append(_texto_do_erro(filha, _fundo + 1))
        causa = getattr(exc, "__cause__", None)
        if causa is not None and causa is not exc:
            partes.append(_texto_do_erro(causa, _fundo + 1))
    return " | ".join(partes)

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("gemini_api_key") or data.get("GEMINI_API_KEY") or data.get("google_api_key") or ""


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _collapse_immediate_transcript_repetition(text)


def _transcript_key(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-zA-Z0-9]+", " ", text.lower()).strip()


def _collapse_immediate_transcript_repetition(text: str) -> str:
    """Remove um bloco inteiro repetido pela transcrição streaming.

    A Live API ocasionalmente entrega ``frase + frase`` em uma única atualização.
    Só removemos metades normalizadas idênticas com pelo menos quatro palavras,
    evitando alterar repetições curtas e intencionais da conversa.
    """
    current = str(text or "").strip()

    # Remove também uma sentença repetida no meio da resposta, como:
    # "Cinco mensagens enviadas. Cinco mensagens enviadas. Precisa de algo?"
    sentences = re.split(r"(?<=[.!?])\s+", current)
    if len(sentences) > 1:
        compact: list[str] = []
        last_key = ""
        for sentence in sentences:
            key = _transcript_key(sentence)
            if key and key == last_key and len(key.split()) >= 2:
                continue
            compact.append(sentence)
            last_key = key
        current = " ".join(compact).strip()

    while current:
        middle = len(current) // 2
        boundaries = [i for i, ch in enumerate(current) if ch.isspace() or ch in ".!?;:"]
        collapsed = ""
        for split in sorted(boundaries, key=lambda i: abs(i - middle)):
            if abs(split - middle) > max(24, len(current) // 5):
                break
            left = current[:split].strip(" .!?;:")
            right = current[split:].strip(" .!?;:")
            key = _transcript_key(left)
            if len(key.split()) >= 4 and key == _transcript_key(right):
                collapsed = left
                break
        if not collapsed or collapsed == current:
            return current
        current = collapsed
    return current


def _append_streaming_transcript(parts: list[str], text: str) -> None:
    """Mescla atualização incremental/cumulativa sem repetir o prefixo anterior."""
    new = _clean_transcript(text)
    if not new:
        return
    if not parts:
        parts.append(new)
        return
    current = " ".join(parts).strip()
    current_key = _transcript_key(current)
    new_key = _transcript_key(new)
    if not new_key or new_key == current_key or current_key.startswith(new_key):
        return
    if new_key.startswith(current_key):
        parts[:] = [new]
        return

    # Cobre atualizações parcialmente sobrepostas sem juntar palavras duas vezes.
    low_current = current.casefold()
    low_new = new.casefold()
    max_overlap = min(len(current), len(new))
    for size in range(max_overlap, 7, -1):
        if low_current[-size:] == low_new[:size]:
            parts[:] = [(current + new[size:]).strip()]
            return
    parts.append(new)


# O transcritor marca o que não é fala com etiquetas como <noise>, [inaudible]
# ou <silence>. Elas NÃO são comandos do cliente. Sem retirá-las aqui, a regra
# abaixo enxergava a palavra "noise" com 5 letras, tratava como pedido, reenviava
# ao servidor, recebia <noise> de novo — e o chat ficava girando nisso para
# sempre, queimando cota. Foi exatamente o que o cliente viu na tela.
_MARCAS_DE_RUIDO = re.compile(
    r"[<\[]\s*(noise|inaudible|silence|silent|unk|unknown|music|laughter|"
    r"sound|background|blank_audio|no_speech)[^>\]]*[>\]]",
    re.IGNORECASE,
)


def _should_retry_silent_voice_turn(
    transcript: str,
    response_text: str,
    had_model_audio: bool,
    had_tool_call: bool,
    handled_locally: bool = False,
) -> bool:
    """Retenta por texto somente quando o Live encerrou a fala sem fazer nada."""
    limpo = _MARCAS_DE_RUIDO.sub(" ", transcript or "")
    useful_input = re.sub(r"[^\w]+", "", limpo, flags=re.UNICODE)
    return bool(
        len(useful_input) >= 2
        and not (response_text or "").strip()
        and not had_model_audio
        and not had_tool_call
        and not handled_locally
    )


def _normalize_pt_command(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-zA-Z0-9%]+", " ", text.lower())
    return " ".join(text.split())


def _delete_confirmation_decision(text: str) -> str | None:
    """Retorna 'confirm', 'cancel' ou None para confirmações locais de exclusão."""
    normalized = _normalize_pt_command(text)
    if not normalized:
        return None

    cancel_words = (
        "nao", "não", "cancela", "cancelar", "cancelado", "pare",
        "deixa", "deixa quieto", "nao exclui", "nao apagar", "nao delete",
        "não exclui", "não apaga", "esquece",
    )
    if any(word in normalized for word in cancel_words):
        return "cancel"

    confirm_words = (
        "sim", "confirmo", "confirmado", "pode", "pode excluir", "pode apagar",
        "pode deletar", "excluir", "apagar", "deletar", "manda", "lixeira",
        "isso", "pode sim", "confirmar",
    )
    if any(word in normalized for word in confirm_words):
        return "confirm"
    return None


def _norm_pt_command(value: str) -> str:
    value = unicodedata.normalize("NFD", value or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _explicit_known_location(text: str) -> str:
    """Extrai um local conhecido mesmo quando o STT separa sílabas."""
    normalized = _norm_pt_command(text)
    compact = normalized.replace(" ", "")
    if "desktop" in normalized or "areadetrabalho" in compact:
        return "desktop"
    if "downloads" in normalized or "pastadedownload" in compact:
        return "downloads"
    if "documentos" in normalized or "meusdocumentos" in compact:
        return "documents"
    if "imagens" in normalized or "minhasimagens" in compact:
        return "pictures"
    if "musicas" in normalized or "minhasmusicas" in compact:
        return "music"
    if "videos" in normalized or "meusvideos" in compact:
        return "videos"
    return ""


def _explicit_file_extension(text: str) -> str:
    """Reconhece extensões faladas como 'T X T' sem inferir pelo conteúdo."""
    normalized = _norm_pt_command(text)
    compact = normalized.replace(" ", "")
    for token, extension in (
        ("txt", ".txt"), ("pdf", ".pdf"), ("docx", ".docx"),
        ("xlsx", ".xlsx"), ("csv", ".csv"), ("json", ".json"),
        ("html", ".html"), ("javascript", ".js"),
    ):
        if token in compact:
            return extension
    if "como python" in normalized or "extensao python" in normalized:
        return ".py"
    return ""


def _spoken_subfolder_for_location(text: str, root: str) -> str:
    """Extrai a subpasta pedida antes de uma pasta conhecida do Windows."""
    normalized = _norm_pt_command(text)
    for broken, repaired in (
        ("pas ta", "pasta"), ("are a", "area"),
        ("do cu mentos", "documentos"), ("down loads", "downloads"),
    ):
        normalized = normalized.replace(broken, repaired)
    marker = {
        "desktop": r"(?:area de trabalho|desktop)",
        "documents": r"(?:documentos|meus documentos)",
        "downloads": r"(?:downloads|pasta de downloads)",
        "pictures": r"(?:imagens|minhas imagens)",
        "music": r"(?:musicas|minhas musicas)",
        "videos": r"(?:videos|meus videos)",
    }.get(root)
    if not marker:
        return ""
    patterns = (
        rf"(?:na|em|dentro da)\s+(?:minha\s+)?pasta(?:\s+com\s+(?:o\s+)?nome)?\s+(.+?)\s+(?:na|em|dentro da)\s+(?:minha\s+)?{marker}(?:\s|$)",
        rf"(?:na|em|dentro da)\s+pasta\s+(.+?)\s+(?:da|do|na|no)\s+{marker}(?:\s|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" .")
            if value and len(value) <= 100:
                return value
    return ""


def _rebase_known_destination(
    raw_destination: str,
    root: str,
    transcript: str = "",
    include_spoken_subfolder: bool = True,
) -> str:
    """Troca só a raiz conhecida e preserva a subpasta solicitada."""
    raw = str(raw_destination or "").strip().strip('"').strip("'")
    relative = Path()
    known_aliases = {
        "desktop", "area de trabalho", "area trabalho", "minha area de trabalho",
        "documents", "documentos", "downloads", "download", "pictures", "imagens",
        "music", "musicas", "videos",
    }
    if raw:
        raw_path = Path(raw).expanduser()
        if raw_path.is_absolute():
            try:
                from actions.file_controller import _resolve_path
                resolved = raw_path.resolve()
                for alias in ("desktop", "documents", "downloads", "pictures", "music", "videos"):
                    base = _resolve_path(alias).resolve()
                    try:
                        relative = resolved.relative_to(base)
                        break
                    except ValueError:
                        continue
            except Exception:
                relative = Path()
        else:
            parts = [part for part in raw.replace("\\", "/").split("/") if part]
            if parts and _norm_pt_command(parts[0]) in known_aliases:
                parts = parts[1:]
            elif len(parts) == 1 and _norm_pt_command(parts[0]) in known_aliases:
                parts = []
            relative = Path(*parts) if parts else Path()

    if (not relative.parts) and include_spoken_subfolder:
        spoken = _spoken_subfolder_for_location(transcript, root)
        if spoken:
            relative = Path(spoken)
    return str(Path(root) / relative) if relative.parts else root


def _vision_command_action(value: str) -> str | None:
    """Detecta comandos diretos de Vision Control mesmo com transcrição ruim.

    O Gemini Live às vezes entende o comando e responde falando que ativou,
    mas não chama a ferramenta. Este detector é local e garante a execução real.
    """
    norm = _norm_pt_command(value)
    if not norm:
        return None
    compact = norm.replace(" ", "")

    vision_refs = (
        "visioncontrol", "visecontrol", "visicontrole", "visioncontrole",
        "controlvision", "controlevision", "controlevisao", "controlvisao",
        "videocontrol", "videocontrole", "videocontrolo",
        "controleporgestos", "controledegestos", "controlporgestos",
        "mousepelamao", "mousecomamao", "controlarmousepelamao",
    )
    # O whisper erra a PRIMEIRA palavra com frequencia ("vision", "visão",
    # "vizion", "vision control" virou "visão control" varias vezes no
    # registro), mas quase nunca erra o "contro-". Entao vale qualquer grafia
    # do primeiro pedaco junto de "contro" — assim a lista fixa acima deixa de
    # ser a unica defesa e um erro novo de transcricao nao derruba o comando.
    quase_vision = ("vision", "vizion", "vison", "visao", "visio", "visiom",
                    "vishon", "vizao", "bision", "bizion", "vixao")
    has_vision_ref = (
        any(ref in compact for ref in vision_refs)
        or (any(v in compact for v in quase_vision) and "contro" in compact)
        or ("gestos" in norm and "contro" in norm)
    )
    if not has_vision_ref:
        return None

    # PALAVRA INTEIRA, nao pedaco (01/09/2026). Antes a busca era por pedaco
    # dentro da frase sem espacos, e "ativa o vision control para mim" continha
    # "para" — que estava na lista de DESLIGAR. O comando de ligar desligava.
    # O mesmo valia para "off" dentro de outra palavra.
    # "para" e duas coisas em portugues: o verbo parar e a preposicao de "para
    # mim". Como preposicao ela vem colada num pronome — tirando essa forma da
    # frase, o "para" que sobrar e verbo de verdade. Sem isto, "ative o vision
    # control PARA MIM" era lido como DESLIGAR (achado no teste, 01/09/2026).
    norm_acao = re.sub(
        r"\bp(?:ara|ra) (?:mim|nos|voce|ce|ti|eu|ele|ela|a gente|o senhor|todos)\b",
        " ", norm)
    palavras = set(norm_acao.split())
    stop_palavras = {
        "desativar", "desativa", "desative", "desativado", "desligar", "desliga",
        "desligue", "desligado", "parar", "para", "pare", "fechar", "fecha",
        "feche", "encerrar", "encerra", "encerre", "desabilitar", "desabilita",
        "cancelar", "cancela", "tirar", "tira", "stop", "disable", "off",
    }
    start_palavras = {
        "ativar", "ativa", "ative", "ativado", "ligar", "liga", "ligue", "ligado",
        "iniciar", "inicia", "inicie", "abrir", "abre", "abra", "acionar",
        "aciona", "acione", "habilitar", "habilita", "habilite", "comecar",
        "comeca", "comece", "poe", "poem", "bota", "botar", "coloca", "colocar",
        "start", "enable", "on",
    }
    # Os prefixos cobrem a conjugacao que faltar na lista; sao inequivocos
    # (nenhuma outra palavra do portugues comum comeca assim).
    if palavras & stop_palavras or any(p in compact for p in ("desativ", "desli", "desabilit")):
        return "stop"
    if palavras & start_palavras or any(p in compact for p in ("ativ", "habilit")):
        return "start"
    if "status" in norm or "estado" in norm:
        return "status"
    return None

# Ferramentas que rodam no servidor e demoram de verdade. O texto é o que
# aparece no painel para o cliente não achar que o JARVIS travou. Medido em
# 30/08: um pedido de imagem levou 75,7 s do pedido até a resposta.
# QUANTO CADA TRABALHO DEMORA DE VERDADE (05/09/2026, cronometrado pelo Diego).
#
# Estava escrito "cerca de 1 minuto" para TUDO, e prometer um minuto e pior do
# que nao prometer nada: no terceiro minuto o cliente conclui que travou e
# comeca a mexer no programa no meio do trabalho. Os tempos dele:
#
#     imagem .... 1 a 2 minutos
#     site ...... 6 a 8 minutos
#     leads ..... 3 a 5 minutos
#
# Como `codex_remote` faz imagem E site, o texto dele depende do `kind` — ver
# `_aviso_de_demora`. As entradas abaixo sao o que sobra quando nao da para
# saber o tipo.
_TEMPOS_DO_TRABALHO = {
    "image":      "de 1 a 2 minutos",
    "image_edit": "de 1 a 2 minutos",
    "site":       "de 6 a 8 minutos",
    "leads":      "de 3 a 5 minutos",
}

_TAREFAS_DEMORADAS = {
    "codex_remote": "Trabalhando nisso no servidor — costuma levar de 1 a 2 minutos. Já te aviso quando ficar pronto.",
    # `editar_imagem` faltava aqui (achado em 30/08/2026). Ela chama o MESMO
    # `remote_codex_job` do `codex_remote` e demora o mesmo tanto — medido 92 s.
    # Sem estar nesta lista, o cliente pedia a edicao e o JARVIS nao avisava
    # nada: ficava mudo por um minuto e meio e parecia travado.
    "editar_imagem": "Editando a imagem no servidor — costuma levar de 1 a 2 minutos. Já te aviso quando ficar pronta.",
    "buscar_leads": "Buscando os contatos — isso leva de 3 a 5 minutos.",
    "dev_agent":    "Executando a tarefa — pode levar alguns minutos.",
    # A edicao roda AQUI no PC, em segundo plano: a ferramenta volta em
    # segundos com o plano e o tempo, e avisa falando quando o video fica pronto.
    "editar_video": "Preparando a edição do vídeo aqui no computador — já te digo o plano.",
}


def _aviso_de_demora(nome: str, args: dict) -> str:
    """O aviso de espera com o tempo REAL do que foi pedido.

    Site nao e imagem: o `codex_remote` faz os dois, e um aviso so para os dois
    faz o cliente esperar errado — e quem espera errado acha que travou.
    """
    if nome == "codex_remote":
        tipo = str((args or {}).get("kind") or "").lower().strip()
        tempo = _TEMPOS_DO_TRABALHO.get(tipo)
        if tempo:
            o_que = ("Montando o site no servidor" if tipo == "site"
                     else "Buscando os contatos" if tipo == "leads"
                     else "Criando a imagem no servidor")
            return (f"{o_que} — isso leva {tempo}. "
                    f"Pode deixar comigo que eu te aviso quando terminar.")
    return _TAREFAS_DEMORADAS.get(nome, "Trabalhando nisso; já te aviso.")

def _tool_task_state(tool_name: str, args: dict) -> str:
    """Texto curto mostrado no centro do HUD enquanto a ferramenta roda."""
    action = str((args or {}).get("action", "")).lower().strip()
    mapping = {
        "open_app": "ABRINDO APP",
        "weather_report": "CONSULTANDO CLIMA",
        "browser_control": "NAVEGANDO",
        "send_message": "ENVIANDO",
        "reminder": "AGENDANDO",
        "youtube_video": "YOUTUBE",
        "screen_process": "ANALISANDO",
        "close_camera": "FECHANDO CAMERA",
        "computer_settings": "EXECUTANDO",
        "desktop_control": "AREA DE TRAB.",
        "code_helper": "CRIANDO CODIGO",
        "codex_remote": "CRIANDO...",
        "cowork": "COWORK",
        "buscar_leads": "BUSCANDO LEADS",
        "dev_agent": "EXECUTANDO TAREFA",
        "web_search": "PESQUISANDO",
        "file_processor": "LENDO ARQUIVO",
        "editar_video": "EDITANDO VIDEO",
        "vision_control": "VISION CONTROL",
        "computer_control": "CONTROLANDO PC",
        "usar_computador": "USANDO O PC",
        "organizar_pasta": "ORGANIZANDO",
        "game_updater": "ATUALIZANDO JOGO",
        "flight_finder": "BUSCANDO VOOS",
        "system_status": "LENDO SISTEMA",
        "whatsapp_bridge": "WHATSAPP",
        "shutdown_jarvis": "DESLIGANDO",
    }
    if tool_name == "file_controller":
        file_states = {
            "find": "PROCURANDO",
            "list": "LISTANDO",
            "create_file": "CRIANDO ARQUIVO",
            "create_folder": "CRIANDO PASTA",
            "delete": "EXCLUINDO" if (args or {}).get("confirmed") else "CONFIRMANDO",
            "move": "MOVENDO",
            "copy": "COPIANDO",
            "rename": "RENOMEANDO",
            "read": "LENDO ARQUIVO",
            "open": "ABRINDO ARQUIVO",
            "compress": "COMPACTANDO",
            "compactar": "COMPACTANDO",
            "zip": "COMPACTANDO",
            "write": "SALVANDO",
            "largest": "ANALISANDO DISCO",
            "disk_usage": "LENDO DISCO",
            "organize_desktop": "ORGANIZANDO",
            "info": "CONSULTANDO",
        }
        return file_states.get(action, "ARQUIVOS")
    if tool_name == "code_helper":
        code_states = {
            "write": "CRIANDO CODIGO",
            "edit": "EDITANDO CODIGO",
            "explain": "ANALISANDO CODIGO",
            "run": "RODANDO CODIGO",
            "build": "COMPILANDO",
            "auto": "CRIANDO CODIGO",
        }
        return code_states.get(action, mapping["code_helper"])
    if tool_name == "browser_control":
        browser_states = {
            "search": "PESQUISANDO WEB",
            "go_to": "ABRINDO SITE",
            "click": "CLICANDO",
            "type": "DIGITANDO",
            "smart_click": "CLICANDO",
            "smart_type": "DIGITANDO",
            "screenshot": "CAPTURANDO TELA",
            "close": "FECHANDO ABA",
            "close_all": "FECHANDO NAVEG.",
        }
        return browser_states.get(action, mapping["browser_control"])
    if tool_name == "computer_control" and action == "save_as":
        return "SALVANDO"
    return mapping.get(tool_name, "PROCESSANDO")

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "executar_tarefa",
        # UMA frase: o resumo que cabe no teto do OpenRouter corta no fim de
        # frase, entao tudo que importa tem de estar na primeira.
        "description": (
            "Executa QUALQUER tarefa no computador do usuario — ABRIR e FECHAR "
            "programas, criar, apagar, mover, copiar, renomear, listar e zipar "
            "arquivos e pastas, ver disco, memoria, rede e processos, mexer em "
            "configuracoes do Windows, enfim qualquer coisa que o Windows faca: "
            "escreva a tarefa com o VERBO QUE O USUARIO ACABOU DE USAR nesta "
            "mensagem (se ele disse FECHAR, a tarefa e FECHAR; se disse ABRIR, e "
            "ABRIR) e NUNCA repita a tarefa da mensagem anterior."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tarefa": {
                    "type": "STRING",
                    "description": "A tarefa em portugues, com todo o detalhe que o usuario deu (nomes, caminhos, quantidades)."
                }
            },
            "required": ["tarefa"]
        }
    },
    {
        "name": "fechar_app",
        # A PRIMEIRA FRASE E A UNICA GARANTIDA. As descricoes sao resumidas para
        # caber no teto de fichas do OpenRouter, e o corte e feito no fim de uma
        # frase — entao tudo que importa tem de estar na primeira. Em 30/08 esta
        # descricao comecava com "Closes/quits an application by name." e o resto
        # (o QUANDO usar) era cortado: o modelo nao ligava "fecha o bloco de
        # notas" a esta ferramenta e respondia "fechado" sem fechar nada.
        "description": (
            "Fecha, encerra ou sai de um programa aberto (close/quit an app): "
            "use SEMPRE que o usuario disser fechar, encerrar, sair, matar ou "
            "tirar da tela qualquer programa, app ou janela, em portugues ou "
            "ingles, e NUNCA diga que fechou sem chamar esta ferramenta."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Name of the app to close, as the user said it (e.g. 'bloco de notas', 'Chrome')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "system_status",
        "description": (
            # AMPLIADA em 31/08/2026: o Diego pediu para ver os SSDs e ouviu
            # falar de UM SO, porque a descricao nao citava disco e o modelo foi
            # buscar em `file_controller disk_usage`, que olha um caminho so.
            "ANALISE COMPLETA do computador numa chamada so: nome e uso do "
            "processador, nucleos, memoria RAM, nome e uso da placa de video, "
            "temperatura, TODOS OS DISCOS (cada unidade com espaco livre e "
            "total), tempo ligado e numero de processos. "
            "Use SEMPRE que ele pedir para olhar, verificar ou analisar o "
            "sistema, a maquina, o PC, o desempenho, os discos, os SSDs, o HD, "
            "a memoria, a CPU ou a placa de video. "
            "Ao responder, cite TODOS os itens que ele pediu, nao so o primeiro: "
            "se ele tem tres discos, fale dos tres."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "hora_agora",
        "description": (
            "Diz a hora OU a data AGORA, lidas do relogio do computador, so o que foi perguntado: use SEMPRE que perguntarem que horas sao, que dia e hoje, o dia da semana, o mes ou o ano, porque voce NAO sabe a data de cabeca e a que vier da sua memoria estara errada. Repita 'falar_assim' e nada mais."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        # 01/10/2026: a ferramenta passou a DEVOLVER a previsao pronta (ceu,
        # minima/maxima e chance de chuva) em vez de abrir o navegador. Sem
        # cidade ela usa a do dono; o dia sai do `time`.
        "description": "Previsao do tempo pronta para falar (ceu, minima e maxima, chance de chuva). Use para clima, temperatura, vai chover, frio, calor. Sem cidade dita, NAO pergunte: deixe city vazio que o PC usa a cidade do dono.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name (vazio = cidade do dono)"},
                "time": {"type": "STRING", "description": "hoje | amanha | depois de amanha | semana | um dia da semana (sexta, sabado...)"}
            },
            "required": []
        }
    },
    {
        "name": "send_message",
        "description": "Legacy message sender for non-WhatsApp platforms. For any WhatsApp/Whats/Zap request, use whatsapp_bridge instead.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "whatsapp_bridge",
        "description": (
            "Dedicated WhatsApp module using the local Baileys bridge. Use whenever the user says WhatsApp, Whats, Zap, QR Code do WhatsApp, conectar/desconectar WhatsApp, enviar mensagem/áudio/arquivo/pasta pelo WhatsApp, ler mensagens, listar grupos, transcrever áudio, agendar mensagem WhatsApp, alertas de mensagens ou agente WhatsApp. Do not use send_message for WhatsApp. When the user asks for multiple separate text messages to the same contact, make exactly ONE tool call with action=send_multiple_texts and put every text in messages; never make several send_text calls."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "connect | status | disconnect | clean_session | send_text | send_multiple_texts | send_audio | send_file | recent_messages | last_message | transcribe_audio | find_contact | list_groups | watch_messages | stop_watch | watch_status | leave_group | delete_last_sent | delete_sent_messages | save_contact | schedule_message | confirm_schedule | list_schedules | cancel_schedule | agent_load_knowledge | agent_enable | agent_disable | agent_status"},
                "to": {"type": "STRING", "description": "Contact, phone, chat or group name"},
                "contact": {"type": "STRING", "description": "Contact or group name"},
                "target": {"type": "STRING", "description": "Target contact/group/name"},
                "message": {"type": "STRING", "description": "Exact message text or instruction"},
                "messages": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "All separate text messages for one send_multiple_texts call, in sending order"},
                "file_path": {"type": "STRING", "description": "Local file/folder path or name to send/load"},
                "caption": {"type": "STRING", "description": "Caption for media"},
                "mode": {"type": "STRING", "description": "any | private | groups | contact | group"},
                "date": {"type": "STRING", "description": "Schedule date, e.g. hoje or DD/MM/YYYY"},
                "time": {"type": "STRING", "description": "Schedule time, e.g. 22:30"},
                "schedule_at": {"type": "STRING", "description": "Full schedule date/time if available"},
                "confirm": {"type": "BOOLEAN", "description": "True only after user explicitly confirms group leave/schedule"},
                "limit": {"type": "INTEGER", "description": "Message count limit; for deletion, the exact number of latest outgoing messages to delete"},
                "count": {"type": "INTEGER", "description": "Exact quantity of latest messages sent by the user to delete, from 1 to 50"},
                "direction": {"type": "STRING", "description": "incoming | outgoing | all"},
                "phone": {"type": "STRING", "description": "Phone number for alias"},
                "name": {"type": "STRING", "description": "Alias/contact name"},
                "raw_text": {"type": "STRING", "description": "Original user text for scheduling parser"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera. "
            "For people, describe neutral visible details only; do not rate attractiveness, body size, ugliness/beauty, or give 0-10 appearance scores. "
            # OFERECER A MARCACAO (01/09/2026, ideia do Diego). Descrever onde
            # fica resolve pouco quando a pessoa ja esta perdida na tela; MOSTRAR
            # resolve. Sem esta linha o modelo nunca lembraria de oferecer.
            "IMPORTANTE: se o usuario estiver PROCURANDO algo na tela, diga onde "
            "esta E ofereca marcar: 'se voce nao achar, eu circulo na imagem e "
            "te mostro'. Se ele aceitar, ou se pedir para mostrar, chame a "
            "ferramenta marcar_na_tela."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "Use exactly 'screen' to capture the display, or exactly 'camera' to capture the webcam. Default: 'screen'. Do NOT write 'webcam' here — the value is 'camera'."},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "marcar_na_tela",
        "description": (
            "TIRA UM PRINT DA TELA E MARCA COM UM CIRCULO VERMELHO onde esta o "
            "item que o usuario procura, depois abre a imagem marcada para ele. "
            "Use quando ele estiver PROCURANDO algo na tela e nao achar: "
            "'onde fica o botao X', 'nao estou achando o menu Y', 'me mostra "
            "onde esta Z', 'pode marcar pra mim'. "
            "ORDEM OBRIGATORIA: primeiro screen_process para OLHAR e DIZER onde "
            "esta; so chame esta aqui se ele disser que mesmo assim nao achou, "
            "ou se ele pedir a marcacao com todas as letras. "
            "Leva cerca de um minuto, entao avise que esta preparando."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "alvo": {"type": "STRING",
                          "description": "O que circular, com as palavras do usuario. Ex.: 'o botao Configuracao', 'o menu Arquivo'"}
            },
            "required": ["alvo"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "remote_media",
        "description": (
            "Sends a PC screenshot, opens/closes the PC screen live stream, or controls the webcam live view in the authenticated Remote Control dashboard. "
            "Use ONLY for media explicitly requested on the phone/Remote Control. For ordinary visual analysis use screen_process. "
            "Live screen and webcam remain active until their matching stop action is called."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "screenshot | screen_live_start | screen_live_stop | webcam_start | webcam_stop"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                # A LISTA DE ACOES VEM PRIMEIRO (01/10/2026): sem ela o cerebro
                # inventava nome ("minimize_all") e a ferramenta respondia
                # "Unknown action". Esta lista e a do ACTION_MAP de verdade.
                "action":      {"type": "STRING", "description": "volume_set | volume_up | volume_down | mute | unmute | play_pause | brightness_up | brightness_down | show_desktop | minimize | maximize | full_screen | switch_window | close_window | close | screenshot | lock_screen | dark_mode | toggle_wifi | task_manager | file_explorer | open_settings | new_tab | close_tab | next_tab | refresh_page | go_back | scroll_down | scroll_up | copy | paste | undo | select_all | save | zoom_in | zoom_out | snap_left | snap_right | prev_tab | go_forward | restart | shutdown. For volume: volume_set with value 0-100, or volume_up/volume_down. Minimize ALL windows = show_desktop. Use close with description/app_name when user asks to close a named app, e.g. action=close, description=Bloco de Notas."},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: for volume_set/set_volume use 0-100 percent; for type text, the text to type."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "If the user says navegador/meu navegador without naming one, use Chrome. "
            "For a request like 'open my browser on YouTube channel X', call browser_control only once; do not call open_app first. "
            "Always pass the 'browser' parameter only when the user specifies a non-default browser (e.g. Edge, Firefox, Opera). "
            "If a browser is already active, reuse it instead of opening a second browser profile."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex | youtube (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": (
            "Manages files and folders: list, create, open, compress/zip, delete, move, copy, rename, read, write, find, disk usage. "
            "Parameter contract: path is the directory containing the source, name is only the source file/folder name, and destination is the target directory for move/copy. "
            "If the exact full source path is known, put it in path and omit name. Never put a natural-language sentence in path, name, or destination. "
            "For move/copy when the source location is unknown, use path=computer and pass only the item name; the controller will locate it before acting. "
            "Always wait for the tool result and never claim success unless the result explicitly says the operation succeeded. "
            "For compacting/zipping a file or folder, use action=compress in file_controller; save the ZIP in the same folder as the original unless the user asks otherwise. "
            # Sem esta linha o modelo mandava so `path=desktop` e o nome da pasta
            # se perdia: a ferramenta dizia "criado" sem criar nada (30/08/2026).
            "For action=create_folder you MUST send BOTH: path = where to create it (for example desktop), "
            "and name = the NAME of the new folder, taken from what the user said. "
            "Never call create_folder without name. If the user did not say a name, ASK for it instead of calling the tool. "
            "For opening folders, documents, images or any existing file path, use action=open in file_controller, not open_app. "
            "For deletion, NEVER delete on the first request: call delete without confirmed, let the tool ask for confirmation, "
            "and only call delete again with confirmed=true after the user clearly confirms. "
            "For find/search: if the user does NOT name a location, omit path or use path=computer so the tool searches all visible drives (C:, D:, E:, USB drives). Only use path=desktop/downloads/documents or a specific path when the user explicitly names that location. "
            "Use path=desktop when the user says Área de Trabalho/Desktop; the tool resolves the real Windows OneDrive desktop."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | open | compress | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut. For action=find with unknown location, omit this or use computer/all drives. Use desktop/downloads/documents/home/D:/folder only when user explicitly named that place."},
                "destination": {"type": "STRING", "description": "Target directory for move/copy, e.g. desktop, documents/Backup, D:/Arquivos. Use an exact final file path only when the user explicitly requests a new filename."},
                "output_name": {"type": "STRING", "description": "Optional ZIP name for action=compress. Omit to use the original file/folder name."},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File or folder name"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "max_results": {"type": "INTEGER", "description": "Optional result limit for find. Default 10; request more only when the user asks."},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
                "confirmed":   {"type": "BOOLEAN", "description": "Set true only after the user confirms a deletion. Never set true on the first delete request."},
                "max_seconds":  {"type": "INTEGER", "description": "Optional timeout for find/open search when location is unknown. Find defaults to 20 seconds and is capped at 30 seconds."},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "codex_remote",
        "description": (
            "Creation tool for customer-facing websites and image files. "
            "Use this for any request to create or update a website/project or to create an image. "
            # O cliente perguntou "JARVIS, voce consegue criar imagens?" e o
            # modelo COMECOU a criar uma imagem em vez de responder. Pergunta
            # sobre capacidade nao e pedido de trabalho — e o trabalho custa
            # um minuto e um lugar na fila do servidor.
            "REGRA CRITICA — PERGUNTA NAO E PEDIDO. So chame esta ferramenta quando o "
            "usuario MANDAR criar algo concreto. NUNCA chame quando ele estiver apenas "
            "perguntando se voce consegue, o que voce sabe fazer, ou como funciona. "
            "NAO chame: 'voce consegue criar imagens?', 'voce faz site?', 'o que voce sabe "
            "fazer?', 'da para criar uma imagem com voce?', 'voce cria logo?'. Nesses casos "
            "APENAS RESPONDA em uma frase que sim, voce cria, e pergunte o que ele quer. "
            "CHAME: 'cria uma imagem de um gato', 'faz um site para a minha loja', "
            "'me gera uma logo azul', 'pode criar do seu jeito'. "
            "Na duvida entre perguntar e mandar, RESPONDA em vez de chamar: perguntar de "
            "volta custa uma frase, e criar por engano custa um minuto de espera. "
            "It returns a real verified file to the user's Desktop. "
            "Never claim success unless the tool returns a verified file path. "
            # Sem isto o cliente fica ~1 minuto no silencio absoluto e conclui
            # que o JARVIS travou. Medido em 30/08: 75,7 s calado gerando uma
            # imagem que ele mesmo elogiou depois. Avisar NAO e mentir dizendo
            # "vou fazer" — a chamada acontece no MESMO turno, logo a seguir.
            "ANTES de chamar esta ferramenta, e SEMPRE no mesmo turno em que a chama, "
            "diga ao usuario UMA frase curta em portugues avisando que voce ja comecou e "
            "que leva cerca de um minuto (exemplo: 'Pode deixar, chefe, ja comecei — "
            "me da um minutinho'). Nunca fique calado ao iniciar este trabalho: o silencio "
            "faz o usuario achar que o programa travou. Isto NAO contradiz a regra de nao "
            "prometer sem agir, porque a chamada acontece imediatamente depois da frase."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "kind": {"type": "STRING", "description": "site or image"},
                "description": {"type": "STRING", "description": "Detailed requested site or image"},
                "project_name": {"type": "STRING", "description": "Optional project or file name"},
            },
            "required": ["kind", "description"]
        }
    },
    {
        "name": "cowork",
        "description": (
            "JARVIS COWORK: o programador do JARVIS. Trabalha numa PASTA DE PROJETO do usuario: cria e "
            "modifica aplicativos, jogos, sistemas, sites e programas de verdade, testa no PC, olha a tela "
            "e corrige sozinho. Use quando o usuario pedir para programar/criar/modificar um app, jogo, "
            "sistema ou programa no projeto, falar 'cowork', ou reclamar de algo do projeto (cor errada, "
            "bug, erro). acao='pedido' com o pedido COMPLETO nas palavras do usuario; acao='pasta' para "
            "abrir/escolher a pasta do projeto (pasta=nome ou caminho, opcional); 'rodar' abre o projeto; "
            "'desfazer' volta a ultima mudanca; 'parar' cancela o trabalho. O trabalho roda em segundo "
            "plano e o JARVIS avisa sozinho quando termina: so repita ao usuario a frase que a ferramenta "
            "devolver. Nao use para perguntas sobre o que voce sabe fazer: responda."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "acao": {"type": "STRING", "description": "pedido | pasta | rodar | desfazer | parar"},
                "pedido": {"type": "STRING", "description": "O que o usuario quer no projeto, completo"},
                "pasta": {"type": "STRING", "description": "Pasta do projeto (opcional)"},
            },
            "required": ["acao"]
        }
    },
    {
        "name": "editar_imagem",
        "description": (
            "USE SEMPRE que o usuario pedir para alterar, editar, mudar, trocar, refazer, tirar, "
            "colocar, melhorar ou arrumar algo em uma FOTO OU IMAGEM que ele arrastou para a janela, "
            "escolheu no quadro de arquivo ou cujo caminho informou. Exemplos: 'troque a roupa por "
            "azul', 'deixe o fundo branco', 'tire o carro do fundo', 'refaca essa imagem'. "
            "Voce TEM esta capacidade: nunca responda que nao consegue editar imagens e nunca ofereca "
            "apenas descrever a foto quando o pedido e de mudanca. "
            # Mesma armadilha do codex_remote: perguntar se ele consegue nao e
            # mandar fazer, e o trabalho custa um minuto de espera.
            "MAS PERGUNTA NAO E PEDIDO: se ele so quer saber SE voce edita imagens ou COMO "
            "funciona ('voce consegue editar foto?', 'da para trocar a roupa numa imagem?'), "
            "APENAS RESPONDA que sim e pergunte qual foto e o que mudar. So chame a "
            "ferramenta quando houver um pedido concreto de mudanca. "
            "REGRA CRITICA: chame a ferramenta NA MESMA RESPOSTA em que aceitar o pedido. E PROIBIDO "
            "dizer 'vou fazer', 'ja estou fazendo', 'esta processando' ou 'ja vai ficar pronta' sem "
            "ter chamado a ferramenta neste mesmo turno — sem a chamada, NADA acontece e o cliente "
            "espera por uma imagem que nunca comecou. "
            "Se o usuario responder algo como 'do seu jeito', 'como voce quiser' ou 'voce decide', "
            "isso e autorizacao: chame a ferramenta imediatamente com uma descricao criativa sua, "
            "sem fazer mais perguntas. "
            "Leva de um a tres minutos e a imagem editada e salva e aberta no computador. Se nenhuma "
            "imagem estiver carregada, peca ao usuario para arrastar a foto para a janela do JARVIS. "
            "Nao afirme que ficou pronta antes de a ferramenta responder."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description": {"type": "STRING", "description": "O que mudar na imagem, com o maximo de detalhe que o usuario deu"},
                "imagem": {"type": "STRING", "description": "Caminho da imagem, apenas se o usuario informou um caminho; senao deixe vazio"},
                "project_name": {"type": "STRING", "description": "Nome curto para o arquivo, opcional"},
            },
            "required": ["description"]
        }
    },
    {
        # EDITAR VIDEO (10/09/2026) — tudo roda AQUI no PC (ffmpeg do
        # instalador); o cerebro so escreve a receita. Ver actions/editar_video.py.
        "name": "editar_video",
        "description": (
            "EDITA VIDEO aqui no computador, sem mandar o video para lugar nenhum: formato do "
            "TikTok/Reels/Shorts (9:16), YouTube (16:9) ou quadrado; cortar TODOS os silencios "
            "(deixar so a parte falada); cortar ou tirar um trecho; espelhar/inverter, girar, "
            "acelerar ou camera lenta; legenda automatica; texto na tela; colocar imagem ou logo; "
            "musica de fundo; tirar ou aumentar o som; efeitos (preto e branco, vintage, zoom, "
            "tremer, cores vivas...); transicao; juntar videos; deixar leve para o WhatsApp. "
            "USE SEMPRE que o dono pedir qualquer mudanca num VIDEO (o que ele arrastou para a "
            "janela, selecionou na pasta ou citou pelo nome). Foto e editar_imagem, nao esta. "
            "Passe em `pedido` TUDO o que ele pediu, com as palavras dele. O trabalho roda em "
            "segundo plano, o JARVIS avisa falando quando fica pronto e o original nunca e "
            "alterado. Perguntou do andamento: acao=status. Mandou parar: acao=cancelar. "
            "PERGUNTA NAO E PEDIDO: 'voce edita video?' se responde, sem chamar a ferramenta. "
            "Depois do 'Pronto, o video ficou pronto', elogio ('ficou top', 'ficou bom') e "
            "conversa: agradeca, nao edite de novo nem diga que ainda esta editando."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "pedido": {"type": "STRING", "description": "O que fazer no video, completo, com as palavras do dono."},
                "video":  {"type": "STRING", "description": "Caminho ou nome do video, so se ele citou; vazio = o da janela do JARVIS ou o selecionado na pasta."},
                "acao":   {"type": "STRING", "description": "editar (padrao) | status | cancelar"},
            },
            "required": []
        }
    },
    {
        "name": "buscar_leads",
        "description": (
            "Busca leads comerciais reais no Google Maps e entrega uma lista em bloco de notas (.txt). "
            "Use sempre que pedirem lista de empresas, clientes em potencial, contatos comerciais, prospeccao ou leads "
            "de um segmento em uma cidade ou regiao. "
            "OS FILTROS SAO TOTALMENTE LIVRES: com WhatsApp, sem WhatsApp, com site, sem site, com telefone, sem telefone, "
            "nota minima, quantidade minima de avaliacoes, bairro, ou qualquer combinacao que o usuario pedir. "
            "Repasse em description o pedido do usuario inteiro, com quantidade, cidade e TODOS os filtros citados. "
            "Se o usuario disser onde salvar (area de trabalho, documentos, uma pasta especifica, varias pastas), "
            "coloque cada lugar em destinos. Se ele pedir para abrir o arquivo, use abrir=true. "
            "Nunca invente empresas ou contatos: informe apenas o que a ferramenta retornar."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description": {"type": "STRING", "description": "Pedido completo do usuario: segmento, cidade ou regiao, quantidade e todos os filtros mencionados"},
                "destinos": {"type": "STRING", "description": "Onde salvar, separado por virgula. Ex: 'area de trabalho', 'documentos, pasta Clientes'. Vazio = area de trabalho"},
                "abrir": {"type": "BOOLEAN", "description": "true se o usuario pediu para abrir ou ver o arquivo depois de pronto"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch for local-only development. For customer-facing website or image creation, use codex_remote instead.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "vision_control",
        "description": (
            "Controls the mouse using the webcam and hand gestures. "
            "Use when the user says: ativar vision control, ligar vision control, "
            "ativar controle por visao, controle por gestos, control vision, "
            "desativar vision control, desligar controle por visao, parar controle por gestos. "
            "Open hand moves the cursor with mirrored horizontal movement; pinch clicks; closed hand holds/drag. "
            "Always call this tool for these commands; never only answer that it was activated. "
            "This tool shows the webcam inside the small Vision Cam panel under SYS MONITOR, with hand skeleton overlay."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | stop | status | restart"},
                "camera_index": {"type": "INTEGER", "description": "Webcam index. Default: 0"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "computer_control",
        "description": "Keyboard-only control: type text, hotkeys, press keys, scroll, save_as, clipboard. NEVER use click, double_click, right_click, move or screen_click here — to click, select or find ANYTHING the user sees on screen use usar_computador, which looks at the screen. For every Save As request, use exactly one action=save_as with destination, filename and extension. Never implement Save As manually with hotkey/type/press calls; save_as resolves the real Windows folder and validates the final extension. Preserve every requested subfolder in destination. Claim success only when the tool result starts with SAVE_AS_OK; SAVE_AS_FAILED means the file was not confirmed and must be reported as a failure.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | save_as | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
                "destination": {"type": "STRING",  "description": "Exact requested folder for save_as. Preserve subfolders, e.g. desktop/Copa do Mundo, documents/Clientes, downloads, or an absolute folder path. Never shorten desktop/Subfolder to desktop."},
                "filename":    {"type": "STRING",  "description": "Final filename for save_as"},
                "extension":   {"type": "STRING",  "description": "Explicit final extension for save_as, such as .txt; it overrides a conflicting filename extension"},
                "overwrite":   {"type": "BOOLEAN", "description": "Overwrite an existing file only when explicitly requested; otherwise a numbered name is chosen"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (extract_audio/extract_frame/transcribe/info — to CUT, compress or EDIT a video use editar_video), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: extract_audio | extract_frame | transcribe | info | convert (editing = editar_video)\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "usar_computador",
        "description": (
            "Usa o computador do dono sozinho, OLHANDO a tela como uma pessoa: clica (esquerdo, direito ou duplo), digita, usa atalho, rola a pagina, preenche formulario, faz login, navega num sistema e vai ate concluir. "
            "Serve para: 'clica na pasta X', 'seleciona aquele arquivo', 'aperta o botao Entrar', cadastro em site, configuracao dentro de um programa, WhatsApp Web ou e-mail pela tela, resolver uma janela que apareceu. "
            "TAMBEM MEXE EM ARQUIVO: abrir pasta e subpasta, entrar em pasta dentro de pasta, escolher varios arquivos de uma vez, arrastar arquivo ou pasta para DENTRO de outra pasta, renomear, copiar, colar e usar o menu do botao direito. "
            "Se o dono pediu para clicar em algo, NAO pergunte onde esta: chame esta ferramenta, ela enxerga a tela. "
            "Passe em `tarefa` o objetivo completo com todos os dados que o dono deu (site, nome, texto a digitar). "
            "NAO use para abrir programa (open_app), pesquisar (web_search), tocar video (youtube_video) nem para comando simples de PC (executar_tarefa)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tarefa": {"type": "STRING", "description": "O que fazer na tela, completo, em portugues, com os dados necessarios."},
            },
            "required": ["tarefa"]
        }
    },
    {
        "name": "organizar_pasta",
        "description": (
            "Organiza uma pasta separando os arquivos em subpastas por tipo (Imagens, Videos, Documentos, Instaladores...) e DESFAZ quando o dono pedir para voltar como estava. "
            "acao=organizar (padrao) com pasta = downloads, documentos, area de trabalho ou um caminho; acao=desfazer volta a ultima organizacao. "
            "QUANDO O DONO APONTAR PARA A TELA — 'organiza essa pasta', 'essa que esta aberta', "
            "'a pasta aqui' — mande pasta='aberta' ou deixe pasta VAZIA: a ferramenta descobre "
            "sozinha qual pasta esta aberta no Explorer e, se nao houver nenhuma, olha a tela. "
            "Nunca invente um caminho a partir do que ouviu."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "acao":  {"type": "STRING", "description": "organizar | desfazer"},
                "pasta": {"type": "STRING", "description": "downloads (padrao), documentos, area de trabalho, imagens, ou um caminho completo"},
            },
            "required": []
        }
    },
    {
        "name": "digitar_na_tela",
        "description": (
            "DIGITA ao vivo, letra por letra, na janela que esta aberta (Bloco de Notas, Word, editor) para o dono VER o texto ou o codigo nascendo: use quando ele disser 'digita', 'escreve ai', 'vai codando no bloco de notas', 'escreve nesse arquivo'. "
            "Passe em `pedido` o que escrever (ex.: codigo Python de uma calculadora completa) e em `janela` onde (bloco de notas). "
            "NAO e para criar arquivo: 'cria um codigo' e code_helper, 'cria um arquivo' e file_controller."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "pedido":    {"type": "STRING", "description": "O que escrever, completo (linguagem, o que o programa faz)."},
                "janela":    {"type": "STRING", "description": "bloco de notas | word | vscode | (vazio = onde o cursor estiver)"},
                "linguagem": {"type": "STRING", "description": "python, javascript... quando for codigo"},
                "conteudo":  {"type": "STRING", "description": "Texto pronto para digitar, se voce mesmo ja escreveu."},
            },
            "required": []
        }
    },
    {
        "name": "desenhar_na_tela",
        "description": (
            "DESENHA de verdade num editor de imagem (Paint, e tambem Photoshop/GIMP/Krita) quando o dono disser 'desenha', 'faz um desenho', 'desenha uma casa no Paint', 'rabisca um gato', 'ilustra'. "
            "O JARVIS abre o Paint sozinho, deixa a folha do tamanho da janela e desenha peca por peca com as ferramentas de verdade (retangulo, oval, triangulo, estrela, lapis), com cor de contorno, cor de dentro e espessura. NAO precisa de computer use. "
            "Se o dono tiver mandado uma FOTO e falar nela ('desenha essa foto', 'usa essa imagem como referencia'), passe `referencia` (ou deixe vazio que ele pega a imagem do quadro de arquivo): a foto vira desenho. "
            "Se ele pedir para desenhar EM CIMA da foto ('poe um chapeu nessa foto'), a foto e aberta no Paint e o desenho vai por cima. "
            "NAO use para gerar arquivo de imagem por IA (isso e editar_imagem) nem para clicar na tela (isso e usar_computador)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "pedido":     {"type": "STRING", "description": "O que desenhar, com detalhe: 'uma casa com sol, arvore e uma nuvem'."},
                "programa":   {"type": "STRING", "description": "paint (padrao) | photoshop | gimp | krita | paint.net"},
                "referencia": {"type": "STRING", "description": "Caminho de uma foto para servir de referencia. Vazio = a imagem que o dono arrastou para a janela."},
                "estilo":     {"type": "STRING", "description": "Com referencia: colorido (padrao) ou contorno (so o traco preto, tipo esboco)."},
            },
            "required": []
        }
    },
    {
        "name": "desfazer",
        "description": (
            "Desfaz a ULTIMA acao do JARVIS que tem volta: uma organizacao de pasta (os arquivos voltam ao lugar) ou uma exclusao (o item e restaurado da Lixeira para onde estava). "
            "Chame quando o dono disser desfaz, volta como estava, restaura o que voce excluiu, poe de volta."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Diego, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

# --- Plugin system ---


class _TrocouParaGemini(Exception):
    """Sinal interno: sair do laco local porque o cliente pediu a Gemini.

    Um TaskGroup so termina quando TODAS as tarefas acabam, e o microfone nunca
    acaba sozinho. Levantar isto la de dentro cancela as tarefas irmas e devolve
    o controle ao laco de fora, que ai sim abre a sessao da Gemini.
    """


class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session              = None
        # Vigia da conexao: guarda desde quando existe um pedido do usuario sem
        # nenhuma resposta do servidor. Serve para perceber a conexao que
        # "morre em pe" — o JARVIS continua aberto, mas nao responde mais.
        self._aguardando_desde    = None
        self._ultima_resposta     = 0.0
        # True quando a proxima fala deve juntar folga antes de comecar.
        self._aquecer_audio       = True
        # Desde quando o microfone esta mudo por causa de uma ferramenta.
        self._tool_busy_desde     = 0.0
        # Quando saiu o ultimo pedaco de voz pela caixa de som.
        self._ultimo_audio_tocado = 0.0
        # Contadores do caminho do microfone, para achar onde ele para.
        self._mic_capturado = 0     # o Windows entregou audio
        self._mic_bloqueado = 0     # descartado por alguma trava
        # Hora do ultimo bloco recebido do microfone. Ver o vigia em
        # _listen_audio: se parar de andar, o microfone morreu calado.
        self._mic_ultimo_bloco = 0.0
        # "nao-definido" = nunca escolhido; None = padrao do Windows de proposito.
        #
        # LER AQUI, e nao so no `_aquecer_local`. A entrada de audio abre antes
        # do aquecimento, e ate 30/08/2026 ela pegava "nao-definido" e caia no
        # sorteio automatico — que escolhe pelo nivel de AMBIENTE, premiando o
        # microfone mais barulhento. Dava microfone diferente a cada abertura,
        # e transcricao boa numa sessao e "Bom dia, Visp" na outra.
        self._mic_escolhido = "nao-definido"
        self._saida_escolhida = "nao-definido"
        try:
            import json as _json  # noqa: PLC0415
            _persona = _json.loads(
                (Path(__file__).resolve().parent / "config" / "persona.json")
                .read_text(encoding="utf-8"))
            if "microfone" in _persona:
                self._mic_escolhido = _persona["microfone"]
            if "saida" in _persona:
                self._saida_escolhida = _persona["saida"]
        except Exception:
            pass          # sem arquivo ainda: segue no automatico, como antes
        self._trocar_saida = False
        self._mic_na_fila   = 0     # entrou na fila de envio
        self._mic_enviado   = 0     # saiu para o servidor
        self._mic_silencio  = 0     # nao enviado por ser so silencio
        # Portao de silencio: estado do detector de voz local.
        self._ruido_fundo   = 0.0   # media do ruido ambiente da sala
        # Nivel dos ultimos ~8 s: o bloco mais baixo diz se a sala CHIA acima
        # do piso. Ver "CHIADO ACIMA DO PISO" em `_audio_com_voz`.
        from collections import deque as _deque  # noqa: PLC0415
        self._niveis_recentes = _deque(maxlen=200)
        self._fator_ruido   = self._FATOR_DO_RUIDO   # vira o do chiado se a sala chiar
        self._voz_ate       = 0.0   # ate quando ainda contamos como fala
        self._voz_falada_s  = 0.0   # segundos de VOZ neste turno (nao do buffer)
        self._voz_ao_fechar = 0.0   # quanta voz o turno tinha ao fechar
        # A ESPIADA: o whisper roda DURANTE o rabicho. Ver `_ouvir_adiantado`.
        self._adiantado          = None    # (quanta voz, resultado)
        self._adiantado_rodando  = False
        self._adiantado_carimbo  = 0.0
        self._adiantado_voz      = -1.0
        self._adiantado_pronto   = threading.Event()
        # Ultima vez que o portao reconheceu voz. Enquanto for recente, o
        # microfone manda TUDO, como no JARVIS original.
        self._ultima_voz_detectada = 0.0
        self._preroll       = []    # blocos guardados antes da voz comecar
        self._nivel_pico    = 0.0   # maior volume ja visto (para conferir o limiar)
        self._ultimo_envio_de_voz = 0.0  # quando saiu a ultima voz de verdade
        # Quando saiu a PRIMEIRA voz depois da ultima resposta do servidor.
        # E daqui que o vigia conta o tempo. Contar do silencio acumulado do
        # servidor fazia uma sessao parada ha minutos ser derrubada no instante
        # em que o cliente comecava a falar.
        self._envio_cobravel = 0.0
        # True depois que a conexao vingou. Separa "o codigo de retomada foi
        # recusado" de "nos mesmos derrubamos uma sessao que estava de pe".
        self._sessao_vingou = False
        # Ultima vez que avisamos fim de fala por silencio (evita rajada).
        self._ultimo_aviso_fim_fala = 0.0
        # Ultima vez que avisamos que o programa travou (evita rajada).
        self._ultimo_aviso_travou = 0.0
        # Pior atraso do laco e quantas travadas na janela de 5s.
        self._pior_travada = 0.0
        self._travadas_na_janela = 0
        # Ate quando o microfone fica fechado depois da fala (anti-eco).
        self._mic_fechado_ate = 0.0
        # Ultima vez que mandamos QUALQUER coisa (voz ou sinal de vida).
        self._ultimo_trafego = 0.0
        # True depois que o servidor mandou qualquer coisa nesta sessao.
        self._recebeu_algo = False
        # Quanta voz mandamos desde que o vigia foi armado.
        self._bytes_cobraveis = 0
        # Ultima vez que empurramos "acabei de falar" ao servidor.
        self._ultimo_fechamento_em = 0.0
        # Quando o servidor CONFIRMOU (transcreveu) que ouviu fala do cliente.
        self._servidor_ouviu_em = 0.0
        # O que o cliente falou ENQUANTO o JARVIS falava, para entregar depois.
        self._fala_guardada = []
        self._fala_guardada_bytes = 0
        # Medicao do ritmo de chegada do audio do modelo (ver ritmo_audio).
        self._ritmo_inicio = 0.0
        self._ritmo_ultimo = 0.0
        self._ritmo_bytes = 0
        self._ritmo_maior_buraco = 0.0
        # Codigo que permite RETOMAR a sessao no servidor em vez de abrir uma
        # nova. Sem ele, cada reconexao reenvia as instrucoes e as 28
        # ferramentas (~10.600 tokens) e ainda perde o fio da conversa.
        self._codigo_retomada = None
        # Gasto real, medido pelo proprio servidor (nao estimado).
        self._tokens_entrada = 0
        self._tokens_saida   = 0
        self._destravou_em   = 0.0  # quando soltamos a trava por silencio
        self._folga_extra    = False  # proxima fala vem logo apos uma ferramenta
        self._turno_aberto   = False  # avisamos o servidor que a fala comecou
        self._turnos_fechados = 0     # quantas vezes avisamos que ela terminou
        self._ultimo_reenvio = ""     # ultimo texto reenviado por falta de resposta
        self._blocos_altos   = 0      # blocos seguidos com som forte
        self._edicao_em_curso = False # ja existe uma edicao de imagem rodando
        # Cronometro da resposta: do fim da fala do cliente ate a primeira
        # palavra do JARVIS. E o unico jeito de saber se a demora e do servidor
        # ou nossa.
        self._falou_em       = 0.0
        self._esperando_voz  = False
        # Via exclusiva para tocar o som.
        #
        # Tudo o que o JARVIS faz em segundo plano — pesquisar na internet, ler
        # arquivo, WhatsApp, gerar imagem — dividia as MESMAS threads que a voz
        # usa para sair pela caixa de som. Uma pesquisa demorada segurava a vez
        # e a voz travava no meio da frase. Com uma fila so dela, a fala nunca
        # espera por outra tarefa.
        self._audio_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jarvis-audio"
        )
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._tool_busy           = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        self.ui.on_text_command     = self._on_text_command
        # Botao COMPUTER USE do dock (08/10/2026)
        try:
            self.ui.on_computer_use        = self._botao_computer_use
            self.ui.on_computer_use_estado = self._estado_computer_use
        except Exception:
            pass
        # Painel da engrenagem: voz e jeito de ser.
        self.ui.on_persona_change   = self._trocar_persona
        self.ui.persona_atual       = self._carregar_persona()
        self.ui.on_remote_clicked   = self._make_remote_key
        self.ui.on_whatsapp_clicked = self._open_whatsapp_from_button
        self.ui.on_interrupt        = self.interrupt
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._last_direct_vision_at = 0.0
        self._last_vision_tool_at = 0.0
        # Olhar e clicar (`usar_computador`): freio de mao e pergunta ao dono.
        self._usando_pc = False
        self._parar_pc = False
        # Quando foi o ultimo "para": segura gatilho novo por 12 s, para o eco
        # da propria frase (ou uma repeticao do dono) nao religar o computer use.
        self._parou_pc_em = 0.0
        # "Ativar computador": DOIS relogios. `_pc_liberado_ate` diz que o dono
        # autorizou o JARVIS a mexer no computador (vale 2 min, e renova a cada
        # tarefa); `_esperando_tarefa_pc_ate` e a janela curta em que a PROXIMA
        # frase e a tarefa. Sem o primeiro, NADA liga o computer use — nem a
        # rede de fala, nem o modelo. Regra do Diego em 04/09/2026.
        self._pc_liberado_ate = 0.0
        self._esperando_tarefa_pc_ate = 0.0
        # Quantas vezes ele ja pediu a tarefa de novo NESTA espera. Existe para
        # a espera ter FIM: ver o laco sem saida corrigido em 08/09/2026.
        self._tentativas_tarefa_pc = 0
        # Correcoes ditas com o computer use JA rodando; o laco le e obedece.
        self._correcoes_pc: list[str] = []
        self._pergunta_pendente = False
        # A pergunta pendente e de sim-ou-nao? (confirmacao de passo sem volta).
        # Pergunta aberta do Codex ("qual e a placa?") aceita qualquer resposta.
        self._pergunta_sim_nao = False
        self._resposta_do_dono = None
        # Uma tarefa de olhar-e-clicar por vez, disparada pela rede local.
        self._olhar_thread = None
        # PALAVRA DE ATIVACAO (02/09/2026). Ligada no cartao "SO COM O NOME".
        # `_acordado_ate` e o relogio da janela de 30 s.
        # NAO le do arquivo: comeca sempre desligado. Ver o comentario no
        # `MainWindow.__init__` do ui.py.
        self._so_com_nome = False
        self._acordado_ate = 0.0
        self.ui.so_com_nome = self._so_com_nome
        self.ui.on_so_nome_changed = self._mudou_so_com_nome
        # COWORK (05/10/2026): o botao, o cartao e o campo do Cowork chegam em
        # `_cowork_acao`; o estado dele vai para a tela pelo `cowork_estado`
        # (metodo da casca, o mesmo objeto a cada motor novo — nao duplica).
        try:
            from actions.cowork import COWORK as _cw  # noqa: PLC0415
            _cw.ouvir(self.ui.cowork_estado)
            self.ui.on_cowork_acao = self._cowork_acao
            self.ui.cowork_estado(_cw.estado())
        except Exception as _exc_cw:
            _voice_diag("cowork_nao_ligou", str(_exc_cw)[:90])
        self._pending_delete_args: dict | None = None
        self._pending_delete_label = ""
        self._dropped_mic_chunks = 0
        self._last_speak_text = ""
        self._last_speak_at = 0.0
        self._suppress_model_audio_until_turn_complete = False
        self._pending_literal_confirmation = ""
        self._literal_voice = None
        # Recursos avancados de audio do original (v1alpha): dialogo afetivo e
        # audio proativo. Se o servidor recusar, o laco de conexao desliga isto
        # e reconecta na v1beta — mesmo comportamento do Mark-LI.
        # DESLIGADO em 13:39 (30/08/2026) — a ultima troca minha sem ganho medido.
        #
        # Ligado, isto poe a conexao na **v1alpha** (endpoint de previa) para
        # ganhar `affective_dialog` e `proactive_audio`. O proativo ja tinha sido
        # desligado por deixar o JARVIS mudo de proposito; sobrou o afetivo, que
        # so muda o tom da voz e nao vale trocar o endpoint estavel por ele.
        # A v1beta e o que o Diego usava antes de hoje, com latencias de 1,7 a
        # 12,2 s — a mesma faixa de agora. Nao ha evidencia a favor da v1alpha.
        #
        # A queda automatica para v1beta continua no laco de conexao, entao
        # religar isto e so trocar False por True.
        self._enhanced_live = False
        self._literal_tts = None
        self._voz_ja_fechada = 0.0
        self._voz_local = None
        self._charon = None
        # ── MODO LOCAL (30/08/2026) ──────────────────────────────────────────
        # Ouvido, cerebro e boca sem a Gemini: whisper local -> Codex na VPS ->
        # Kokoro local.
        #
        # NASCE LIGADO (30/08/2026, a pedido do Diego): exigir "/local on" era
        # atrito a toa, e a cota da Gemini dele esta estrangulada — o caminho
        # dela e o que trava. Desliga com "/local off" se precisar voltar.
        #
        # A sessao da Gemini ainda ABRE (o chat digitado e as ferramentas
        # dependem dela), mas nao recebe UM BYTE de audio, que e o que a cota
        # cobra por segundo.
        self._modo_local     = True
        # Qual cerebro o modo local usa. O OPENROUTER SAIU EM 05/09/2026
        # (ordem do Diego, com os tres caminhos medidos): a conta gratis dele da
        # 50 pedidos por dia e, quando acaba, a fila cai nos modelos fracos e a
        # piora PARECE defeito de codigo. Hoje a escada e:
        #
        #   Gemini do cliente ..... 0,7 a 1,0 s   quando a cota dela deixa
        #   Codex CLI na VPS ...... 4,5 a 5,4 s   SEM cota, no plano do Diego
        #
        # O rotulo "openrouter" ficou como NOME DO DEGRAU LOCAL: ele nao chama
        # mais o OpenRouter, chama essa escada (`core/cerebro_openrouter.py`,
        # que tambem manteve o nome de arquivo — trocar mexeria em 30 pontos
        # deste arquivo sem mudar comportamento nenhum).
        # Troca com "/cerebro openrouter", "/cerebro gemini", "/cerebro codex".
        self._cerebro        = "openrouter"
        self._openrouter     = None
        self._buf_local      = []      # blocos da fala em andamento
        self._pre_local      = []      # rabicho de tras: nao perder a 1a silaba
        self._local_ocupado  = False   # uma fala por vez
        self._whisper        = None
        self._avisou_cota = False
        self._current_input_transcript = ""
        self._pending_manual_save_destination = ""
        self._pending_manual_save_extension = ""

    def _open_whatsapp_from_button(self) -> None:
        """Abre/conecta o WhatsApp direto pelo botão da interface, sem depender de voz."""
        try:
            self.ui.set_state("WHATSAPP")
            self.ui.write_log("[WhatsApp] Abrindo QR Code/conexão...")
            result = whatsapp_action({"action": "start"}, player=self.ui)
            if result:
                self.ui.write_log("[WhatsApp] " + str(result))
        except Exception as e:
            self.ui.write_log(f"[WhatsApp] erro ao abrir conexão: {str(e)[:160]}")
        finally:
            try:
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
            except Exception:
                pass

    def _drain_audio_queue(self) -> None:
        # Fila esvaziada: a proxima fala recomeca do zero e precisa de folga.
        self._aquecer_audio = True
        q = self.audio_in_queue
        if not q:
            return
        while True:
            try:
                q.get_nowait()
            except Exception:
                break

    def _drain_realtime_queue(self) -> None:
        """Descarta áudio de microfone pendente antes de executar uma ferramenta."""
        q = self.out_queue
        if not q:
            return
        while True:
            try:
                q.get_nowait()
            except Exception:
                break

    # ── Portão de silêncio ────────────────────────────────────────────────────
    # Sintoma que isto resolve: o cliente falava e o JARVIS não respondia, mas
    # respondia normalmente pelo chat. Parecia defeito do microfone.
    #
    # Causa: o microfone mandava áudio para o servidor de voz 100% do tempo —
    # medido: 2.930 blocos de 40 ms em 105 s, ou seja, o silêncio da sala
    # inteiro. A cota do Live API é cobrada por SEGUNDO DE ÁUDIO, então uma
    # sala calada com o programa aberto queimava a cota do dia sozinha. Depois
    # disso o servidor recusava a voz (cara) e ainda aceitava o texto (barato)
    # — exatamente o que o cliente via.
    #
    # Solução: só enviar quando houver som de voz. Guardamos um pedacinho do
    # que veio ANTES (senão a primeira sílaba some) e continuamos mandando um
    # pouco DEPOIS que a voz cai — esse rabicho de silêncio é o que faz o
    # servidor entender que o cliente terminou de falar.

    # Volume mínimo para valer como fala. MEDIDO no microfone desta máquina, em
    # 30/08, com a sala em silêncio: mediana 17, 95% abaixo de 100, picos de
    # até 199. Com o piso em 140 o portão abria SOZINHO, e cada abertura manda
    # 2 s de áudio que o servidor entende como "o cliente voltou a falar" —
    # cancelando a resposta que estava sendo gerada. A fala real mede ~2500 de
    # pico (está no registro: pico=2522), então 260 fica muito acima do ruído e
    # muito abaixo da voz.
    # Se um dia o cliente reclamar que precisa falar alto, medir de novo com
    # scratchpad/mede_portao.py em vez de chutar este número.
    # 260 foi alto demais para o risco que resta: agora que o servidor não
    # interrompe mais (NO_INTERRUPTION), deixar um ruído passar custa pouco,
    # mas deixar de ouvir o cliente custa tudo. 200 continua acima do maior
    # pico do ventilador que medi (199) e muito abaixo da voz dele (2666).
    # MEDIDO no microfone novo em 30/08/2026, 6 s de sala em silencio:
    #     mediana 49  |  p95 607  |  MAXIMO 968
    # Com o piso em 200 o ruido de fundo abria o portao sozinho o tempo todo, e
    # o whisper transformava chiado em palavra — dai as frases que o Diego nunca
    # falou. 750 fica acima do p95 e abaixo da voz dele.
    #
    # NAO CHUTAR ESTE NUMERO. Medir com scratchpad/mede_ruido.py, que imprime
    # mediana, p95 e maximo da sala.
    # 80 desde 31/08/2026. O 750 anterior foi medido no microfone ANTERIOR;
    # com o [24] USB AUDIO a sala inteira mede mediana 0, p95 4, maximo 9 — e
    # 750 virou um muro que so a boca colada no aparelho vencia. O Diego
    # reclamou exatamente disso. 80 e quase nove vezes o maior ruido medido.
    # Trocou de microfone? MEDIR DE NOVO, nao chutar.
    _PISO_DE_VOZ  = 80.0
    # Quantas vezes o ruido da sala a voz precisa ser para abrir o portao.
    # Abaixo de ruido 32 manda o `_PISO_DE_VOZ` — no microfone do Diego (sala 0
    # a 9) o fator nem entra na conta.
    _FATOR_DO_RUIDO = 2.5
    # CHIADO CONSTANTE (17/09/2026) usa fator menor. Chiado de placa-mae quase
    # nao varia: no cliente DESKTOP-HDU4A58, 30 s de sala quieta deram mediana
    # 694 e p99 876 — so 1,26x. Com 2,5 o limiar ia a 1.735 e a fala baixa dele
    # (bloco ~1.000) nunca abria. Varrido em `_scripts\teste_portao_chiado.py`:
    # 1,4 da zero abertura falsa em 120 s de chiado e abre a fala de 1.000; 1,6
    # ja perde a fala baixa. So vale depois que "ruido_da_sala" confirma que o
    # ruido e constante — sala com ruido que oscila segue no 2,5.
    _FATOR_DO_CHIADO = 1.4
    # Segue enviando por este tempo depois que a voz cai. Ele existia para dar
    # ao servidor o silêncio de que ele precisava para decidir sozinho que a
    # fala tinha acabado. Como agora avisamos isso explicitamente, sobrou só o
    # papel de não cortar pausas naturais no meio de uma frase — e 0,8 s basta.
    # Cada décimo cortado aqui é um décimo a menos de espera pela resposta.
    # Tempo de silêncio REAL antes de parar de enviar. Já foi 1,2 s e depois
    # 0,8 s: os dois cortavam no meio das pausas naturais da frase, e o servidor
    # recebia a fala em pedaços — devolvia <noise> em vez das palavras. Com 2 s
    # nenhuma pausa de conversa chega perto, e a economia continua enorme,
    # porque uma sala vazia fica calada por minutos, não por segundos.
    # 30/08: 2,0 s NÃO bastava. Com o aviso explícito de fim de fala ligado, o
    # turno passou a fechar 2,15 s depois da última sílaba — e uma pausa de
    # pensamento no meio da frase é mais longa do que isso. O servidor recebia
    # meia frase: o registro pegou ele transcrevendo "So I'll just go to the"
    # de uma fala em português. Modelo nenhum responde a um fragmento assim.
    # 3,0 s cobre a pausa de quem está pensando e ainda fecha o turno rápido.
    # ERA 3,0 s e e o MAIOR custo da espera do Diego: depois que ele cala, o
    # JARVIS aguarda esse tempo para concluir que a frase acabou. Somado ao
    # whisper (1,6 s), dava os "5 segundos ate aparecer no chat".
    #
    # Os 3 s vinham do caminho da Gemini, onde fechar cedo partia a frase ao
    # meio e o servidor recebia meia pergunta. No caminho local o risco e menor:
    # se cortar cedo, o whisper transcreve o pedaco e o cerebro responde ao que
    # entendeu — nao ha turno de servidor para estragar.
    #
    # 1,5 s ainda cobre a pausa natural de quem pensa no meio da frase, e
    # devolve 1,5 segundo inteiro na resposta. Se comecar a partir frase ao
    # meio, subir para 2,0 — mas medir antes, no registro `local_ouviu`.
    # 1,5 -> 0,6 s (05/09/2026). Era o MAIOR item da espera depois que o
    # cerebro encurtou: mais tempo esperando o dono ficar quieto do que
    # pensando. Abaixo de ~0,5 s a pausa de quem respira fecha o turno e parte
    # a frase no meio.
    _RABICHO_S       = 0.6
    # O RABICHO LONGO ESTAVA PEGANDO A PERGUNTA NORMAL.
    #
    # Queixa do Diego: "quando eu termino de falar passa uns 3 segundos para
    # minha voz chegar no chat". Nao era o ouvido — era esta regra. Ela dava
    # 3,4 s de espera para QUALQUER fala com mais de 3 s de voz, e uma pergunta
    # comum passa disso com folga. Ou seja: o caso mais comum pagava o preco do
    # caso raro.
    #
    # A regra existe para nao cortar quem esta DITANDO, e isso continua valendo
    # — so que agora a partir de 5 s de VOZ, que e onde comeca a explicacao de
    # verdade, e com espera de 2,6 s. Pergunta e pedido comuns fecham em 0,6 s.
    _RABICHO_LONGO_S = 2.6
    _FALA_LONGA_S    = 5.0
    _PREROLL      = 8       # blocos guardados antes da fala (8 x 40ms = 320ms)
    # Um estalo de teclado ou uma batida na mesa passam do limiar por um
    # instante. Fala, não: ela se sustenta. Exigir alguns blocos seguidos evita
    # mandar barulho ao servidor, que devolvia isso como <noise> e fazia o chat
    # entrar em ciclo. 3 blocos = 120 ms, bem abaixo de qualquer palavra.
    # ERA 3 e passava pico de ruido. Um estouro de ventilador dura um ou dois
    # blocos; fala dura dezenas. Exigir 5 seguidos derruba o pico sem perder o
    # comeco da frase (5 blocos = 0,2 s, e o pre-roll recupera o que veio antes).
    # BAIXOU PARA 3 em 30/08/2026: o Diego reclamou que palavra curta ("sim",
    # "nao", "JARVIS") nao chegava. 5 blocos sao 0,2 s de som sustentado, e uma
    # palavra dessas nao sustenta tanto. Com o ouvido na placa custando 0,4 s,
    # sai barato abrir mais cedo e deixar o whisper descartar o que for ruido —
    # ele devolve vazio para barulho, e o `local_vazio` registra.
    _BLOCOS_PARA_ABRIR = 3

    # Quanto tempo sem NENHUMA voz detectada antes de o programa parar de
    # enviar o silêncio da sala. Abaixo disto o comportamento é o do JARVIS
    # original: manda tudo, sem cortar nada — que é o único jeito de o servidor
    # entender o fim da frase, manter a conexão aquecida e transcrever direito.
    # Acima disto não há conversa para estragar, e economizar cota passa a
    # valer: 2 minutos é bem mais que qualquer pausa de conversa real.
    _OCIOSO_PARA_ECONOMIZAR_S = 120.0

    # Tempo sem NENHUM bloco do microfone que ja significa aparelho morto.
    # Cada bloco vale ~40 ms, entao 8 s sao ~200 blocos que nao chegaram —
    # nao ha silencio de sala que explique isso, so o stream ter parado.
    # Folgado de proposito: reabrir o microfone a toa corta a fala do cliente.
    # ERA 8 s e virou defeito pior que o original (30/08, 18h40): o microfone
    # oscilava — "parou de responder" / "de volta" / "parou" — enchendo a tela do
    # Diego de aviso. Oito segundos e curto demais: qualquer engasgo do driver,
    # ou outro programa disputando o aparelho, dispara a reabertura, e reabrir
    # causa o proximo engasgo. Realimentacao.
    #
    # 45 s so dispara para microfone REALMENTE morto, que era o caso original.
    _MIC_CONGELADO_S = 45.0

    # DESLIGADO. O portão cortava o silêncio para economizar cota, e economizava
    # mesmo (93%). Mas ele picotava a fala: o servidor passou a receber pedaços
    # curtos demais e devolvia <noise> em vez das palavras — o cliente falava e
    # o JARVIS não entendia nada. O JARVIS original manda o áudio inteiro e
    # deixa o servidor decidir, e é assim que ele funciona. Cota não vale nada
    # se ele não escuta. Os contadores continuam ligados para medir.
    # LIGADO de novo, agora com 2 s de folga. Sem ele o microfone manda o
    # silêncio da sala para o servidor 24 horas por dia, e a cota do dia acaba
    # sozinha — foi o que aconteceu com duas chaves seguidas.
    # RELIGADO em 30/08/2026, 13:25 — e o motivo importa.
    #
    # Eu tinha desligado para deixar tudo igual ao Mark-LI, que manda todo bloco
    # sempre. So que o portao daqui NAO e o portao agressivo que picotava a fala:
    # enquanto houver voz nos ultimos `_OCIOSO_PARA_ECONOMIZAR_S` (120 s) ele
    # manda TUDO, exatamente como o original. O que ele faz de diferente e parar
    # de enviar quando **nao ha ninguem na sala**.
    #
    # Com ele desligado, o JARVIS mandava a sala calada 24 horas por dia. A cota
    # do Live e cobrada por SEGUNDO DE AUDIO — entao silencio virou a maior
    # despesa do dia, e a conta bateu no sintoma medido:
    #
    #   quieto antes -> resposta:  5s->1,7s | 9s->2,7s | 14s->8,6s
    #                              29s->15,4s | 70s->24,4s
    #
    # Quanto mais tempo o Diego passava calado, mais lenta a resposta seguinte:
    # a cota estrangulada nao recusa, ela entrega devagar. O original nao sofre
    # com isso porque nao esta com a cota no talo; o Diego esta.
    _PORTAO_LIGADO = True

    def _audio_com_voz(self, bloco):
        """Devolve os pedaços que valem a pena enviar; nada, se for só silêncio."""
        try:
            # O NIVEL E SO DO QUE PASSA DE 100 Hz (17/09/2026, DESKTOP-HDU4A58).
            # A entrada de microfone da placa-mae dele entrega um DESVIO FIXO
            # (DC) de 887 com a sala quieta. O RMS de antes somava esse desvio:
            # a sala "media" 896 e o portao nunca fechava. Tirando DC e ronco
            # abaixo de 100 Hz, a mesma sala mede 31 e a voz dele ~1.400. Voz
            # quase nao tem energia ali; no microfone limpo nada muda.
            import numpy as _np  # noqa: PLC0415
            amostras = bloco.reshape(-1).astype("float32")
            _n = amostras.size
            _esp = _np.abs(_np.fft.rfft(amostras)[
                int(100 * _n / SEND_SAMPLE_RATE) + 1:]) ** 2
            nivel = float(max(0.0, 2.0 * _esp.sum()
                              - (_esp[-1] if _n % 2 == 0 else 0.0)) / (_n * _n)) ** 0.5
        except Exception:
            # Na dúvida, envia: é melhor gastar cota do que perder a fala.
            return [bloco.tobytes()]

        agora = time.monotonic()
        limiar = max(self._ruido_fundo * self._fator_ruido, self._PISO_DE_VOZ)

        # CHIADO ACIMA DO PISO (17/09/2026, cliente DESKTOP-HDU4A58: "o JARVIS
        # nao escuta nada").
        #
        # O ruido da sala so e aprendido com bloco ABAIXO do limiar, e o limiar
        # comeca no piso (80). O microfone da placa-mae dele chia a RMS 694 com
        # a sala quieta: nenhum bloco ficava abaixo de 80, o ruido NUNCA era
        # aprendido e o portao ficava aberto para sempre. Cada turno ia ate o
        # teto de 80 s (`turno_fechado voz=100.3s`) e o whisper do Xeon de 2012
        # levou 202 s para ouvir um deles. Do lado dele: surdo.
        #
        # Fala de verdade sempre tem vao (respiracao, fim de frase) em 8 s. Se
        # nem o bloco mais baixo desse tempo fica abaixo do limiar, aquilo e o
        # chiado da sala: a MEDIANA da janela vira o ruido e o fator passa a ser
        # o do chiado. No microfone limpo nunca dispara: o silencio entre as
        # frases fica muito abaixo de 80.
        self._niveis_recentes.append(nivel)
        if len(self._niveis_recentes) == self._niveis_recentes.maxlen:
            if min(self._niveis_recentes) >= limiar:
                janela = sorted(self._niveis_recentes)
                self._ruido_fundo = janela[len(janela) // 2]
                self._fator_ruido = self._FATOR_DO_CHIADO
                limiar = max(self._ruido_fundo * self._fator_ruido, self._PISO_DE_VOZ)
                self._niveis_recentes.clear()
                _voice_diag("ruido_da_sala",
                            f"chiado constante: mediana {self._ruido_fundo:.0f}, "
                            f"menor {janela[0]:.0f} - limiar agora {limiar:.0f}")

        # A FAIXA DE BAIXO MEXE COM A VOZ DELE (05/09/2026, pedido do Diego).
        # O nivel ja esta calculado aqui — e o mesmo que o portao de voz usa —
        # entao mostrar nao custa nada. 4000 e a referencia de fala normal
        # neste microfone (RMS em int16); acima disso a barra ja esta no teto.
        try:
            self.ui.nivel_do_microfone(min(1.0, nivel / 4000.0))
        except Exception:
            pass

        if nivel > self._nivel_pico:
            self._nivel_pico = nivel

        # O CONTADOR SO ZERA ABAIXO DE `baixo`, e o que fica entre `baixo` e o
        # limiar nao mexe nele. Com `limiar * 0.5` sozinho, na sala que chia o
        # chiado inteiro caia nessa faixa do meio: depois da fala o contador
        # nunca zerava e o rabicho era renovado para sempre. O chiado vive em
        # volta de `_ruido_fundo`, entao 1,2x ele tambem zera.
        baixo = max(limiar * 0.5, self._ruido_fundo * 1.2)
        if nivel >= limiar:
            self._blocos_altos = min(self._blocos_altos + 1, 10)
        elif nivel < baixo:
            self._blocos_altos = 0

        if self._blocos_altos >= self._BLOCOS_PARA_ABRIR:
            self._ultima_voz_detectada = agora
            # RABICHO QUE CRESCE COM A FALA (30/08/2026).
            #
            # Queixa do Diego: "quando falo algo muito longo, ele corta minha
            # voz no meio e fica faltando o final". Com rabicho fixo de 1,5 s,
            # uma pausa de quem esta pensando no meio de uma explicacao fecha o
            # turno e o resto da frase vira outro turno — ou se perde, porque o
            # JARVIS ja comecou a responder.
            #
            # Pedido curto ("abre o bloco de notas") continua fechando em 1,5 s,
            # que e o que mantem a resposta rapida. Depois de ~4 s falando ele
            # claramente esta contando alguma coisa, e ai vale esperar mais.
            # Frase longa continuava sendo cortada no meio (queixa de
            # 31/08/2026), entao o rabicho longo subiu de 2,6 para 3,4 s e
            # comeca mais cedo, com 3 s de fala em vez de 4. Pedido curto
            # ("abre o bloco de notas") segue fechando em 1,5 s, que e o que
            # mantem a resposta rapida.
            # O GATILHO MEDIA A COISA ERRADA. Era `len(self._buf_local)` — o
            # tamanho do BUFFER, que engole o silencio do proprio rabicho e as
            # pausas. Uma frase de 1,5 s de voz aparecia como "3,2 s de fala" e
            # disparava o rabicho longo: 3,4 s de espera por um pedido de duas
            # palavras. `_voz_falada_s` conta so os blocos com VOZ.
            self._voz_falada_s = getattr(self, "_voz_falada_s", 0.0) + 0.04
            self._voz_ate = agora + (
                self._RABICHO_LONGO_S
                if self._voz_falada_s > self._FALA_LONGA_S
                else self._RABICHO_S)
            # É AQUI que o vigia passa a ter o direito de cobrar resposta —
            # e só aqui. Antes ele armava em `_send_realtime`, a cada bloco
            # enviado, o que era razoável enquanto o portão só deixava passar
            # fala. Agora que o microfone manda tudo (como o original), "bytes
            # enviados" virou o silêncio da sala: o vigia cruzava o limite em
            # 2 segundos e derrubava a sessão a cada 35 s de você calado.
            # Registro: "sem sinal do servidor apos 37.6s de voz" — 37,6 s de
            # sala vazia, não de fala.
            if not self._envio_cobravel:
                self._envio_cobravel = agora

        # Silêncio: aprende o ruído da sala, devagar, para o limiar acompanhar
        # um ventilador ligado ou uma janela aberta.
        if nivel < limiar:
            self._ruido_fundo = (self._ruido_fundo * 0.98 + nivel * 0.02
                                 if self._ruido_fundo > 0 else nivel)
            # O chiado sumiu (trocou de microfone, desligou o reforco): volta
            # ao fator de sempre.
            if self._ruido_fundo * self._FATOR_DO_CHIADO < self._PISO_DE_VOZ:
                self._fator_ruido = self._FATOR_DO_RUIDO

        # ── CONVERSA EM ANDAMENTO: manda TUDO, como o JARVIS original ────────
        #
        # Esta é a diferença central entre o nosso fluxo de conversa e o do
        # Mark-LI, e foi ela que quebrou tudo. O original não tem portão nenhum:
        # o `callback` dele enfileira TODO bloco enquanto o JARVIS não está
        # falando. O nosso cortava 93% do áudio, e isso estragava três coisas
        # ao mesmo tempo:
        #   1. o servidor nunca recebia o silêncio de que precisa para decidir
        #      que a frase acabou — o turno não fechava e ele não respondia;
        #   2. numa pausa da conversa o programa ficava MINUTOS sem enviar um
        #      byte, e conexão parada assim é derrubada sem erro nem aviso;
        #   3. a fala chegava picada e a transcrição virava fragmento — o
        #      registro pegou "So I'll just go to the" de uma fala em português.
        #
        # Enquanto houver conversa, portanto, o comportamento é o do original.
        if (agora - self._ultima_voz_detectada) < self._OCIOSO_PARA_ECONOMIZAR_S:
            if self._preroll:
                saida = self._preroll + [bloco.tobytes()]
                self._preroll = []
                return saida
            return [bloco.tobytes()]

        # ── NINGUÉM POR PERTO HÁ MUITO TEMPO: aí sim economiza ───────────────
        #
        # O portão existe por um motivo real: com o microfone mandando o
        # silêncio da sala 24 horas por dia, a cota do dia acaba sozinha — já
        # aconteceu com duas chaves. Mas isso só importa quando não há ninguém
        # falando há minutos, e nesse caso não há conversa para estragar.
        # O nível continua sendo medido a cada bloco, então a primeira sílaba
        # reabre o envio contínuo, e o pré-roll guardado vai junto: nada se
        # perde no reinício.
        self._mic_silencio += 1
        self._preroll.append(bloco.tobytes())
        if len(self._preroll) > self._PREROLL:
            self._preroll.pop(0)
        return []

    def _queue_realtime_audio(self, data: bytes) -> None:
        """Enfileira o microfone sem permitir que áudio antigo vire latência.

        Em uma oscilação de rede, continuar guardando áudio antigo faz o Live API
        ouvir a conversa vários segundos depois. Se a fila atingir o limite,
        descartamos o backlog mais antigo e preservamos o áudio mais recente.
        Em operação normal este caminho nunca é acionado.
        """
        q = self.out_queue
        if q is None:
            return
        # DESFEITO em 13:35: o `_manter_conexao_viva` sempre mandou
        # "audio/pcm;rate=16000". Deixar o microfone sem a taxa fazia o MESMO
        # fluxo carregar dois formatos diferentes — risco sem ganho medido.
        packet = {"data": data, "mime_type": "audio/pcm;rate=16000"}
        try:
            q.put_nowait(packet)
            self._mic_na_fila += 1
            return
        except asyncio.QueueFull:
            pass

        dropped = 0
        # Volta para aproximadamente 1 s de backlog antes de inserir o pacote atual.
        while q.qsize() > 25:
            try:
                q.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        try:
            q.put_nowait(packet)
        except asyncio.QueueFull:
            dropped += 1
        self._dropped_mic_chunks += dropped
        print(
            f"[JARVIS] ⚠️ Mic backlog reduced: {dropped} stale chunks discarded "
            f"(total={self._dropped_mic_chunks})"
        )

    def _store_pending_delete(self, args: dict, result: str) -> None:
        action = str((args or {}).get("action", "")).lower().strip().replace(" ", "_").replace("-", "_")
        if action != "delete":
            return
        if (args or {}).get("confirmed"):
            self._pending_delete_args = None
            self._pending_delete_label = ""
            return
        text = str(result or "").lower()
        if "confirma" not in text or "lixeira" not in text:
            return
        clean_args = dict(args or {})
        clean_args["action"] = "delete"
        clean_args["confirmed"] = False
        self._pending_delete_args = clean_args
        self._pending_delete_label = str(clean_args.get("name") or clean_args.get("path") or "item")
        self.ui.write_log(f"[file] aguardando confirmação para excluir: {self._pending_delete_label}")

    async def _handle_pending_delete_confirmation(self, text: str, source: str = "voice") -> bool:
        if not self._pending_delete_args:
            return False
        decision = _delete_confirmation_decision(text)
        if not decision:
            return False

        label = self._pending_delete_label or "item"
        if decision == "cancel":
            self._pending_delete_args = None
            self._pending_delete_label = ""
            self._drain_audio_queue()
            self.ui.write_log(f"[file] exclusão cancelada: {label}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            self.speak(f"Beleza, chefe. Cancelei a exclusão de {label}.")
            return True

        args = dict(self._pending_delete_args)
        args["confirmed"] = True
        # Não deixa o modelo enviar outra chamada duplicada depois da confirmação.
        self._pending_delete_args = None
        self._pending_delete_label = ""
        self._drain_audio_queue()
        self.ui.set_state("EXCLUINDO")
        self.ui.write_log(f"[file] confirmação recebida; excluindo: {label}")

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: file_controller(parameters=args, player=self.ui),
            )
        except Exception as exc:
            result = f"Não consegui excluir: {exc}"
            traceback.print_exc()

        self.ui.write_log(f"[file] {result}")
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        self.speak(str(result))
        return True

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        try:
            key = self._dashboard.new_key()
        except Exception as exc:
            self.ui.write_log(f"SIS: Controle remoto indisponivel: {exc}")
            return None
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        pairing_url = self._dashboard.get_pairing_url(key) if hasattr(self._dashboard, "get_pairing_url") else f"{url}/auto-login?key={key}"
        return url, key, pairing_url, manual

    def _on_text_command(self, text: str):
        # ── COMANDOS DE BARRA ────────────────────────────────────────────────
        # Ficam AQUI, e nao no `speak()`. O texto digitado na caixa vai para
        # `_on_text_command` (ui.py `_send` -> `on_text_command`); o `speak()`
        # so recebe fala e injecoes internas. Eu tinha posto os gatilhos la e
        # eles nunca podiam disparar — `/codex` e `/local` deram zero acionamentos
        # no registro ate isto ser corrigido (30/08/2026).
        #
        # Vem antes da checagem de sessao de proposito: `/local` e `/voz` nao
        # dependem da Gemini, e justamente quando ela esta fora e que o Diego
        # mais precisa deles.
        _cmd = re.sub(r"\s+", " ", str(text or "")).strip()
        _low = _cmd.lower()
        # Digitar TAMBEM e o dono falando: zera o relogio do silencio. Sem isto
        # o check-in proativo interrompia quem estava conversando pelo teclado.
        if _cmd:
            self._last_user_speech = time.monotonic()
        if _low in ("/local on", "/local off", "/local"):
            if _low == "/local":
                self.ui.write_log(
                    f"LOCAL: modo sem Gemini esta {'LIGADO' if self._modo_local else 'desligado'}.")
                return
            self._modo_local = _low.endswith("on")
            self._buf_local = []
            self._pre_local = []
            if self._modo_local:
                self.ui.write_log(
                    "LOCAL: LIGADO — ouvido whisper, cerebro Codex na VPS, voz Kokoro. "
                    "A Gemini nao recebe mais audio nenhum.")
                threading.Thread(target=self._aquecer_local, daemon=True).start()
            else:
                self.ui.write_log("LOCAL: desligado — voltou para a Gemini.")
            return
        if _low.startswith("/cerebro"):
            escolha = _low.replace("/cerebro", "").strip()
            if not escolha:
                self.ui.write_log(f"CEREBRO: {self._cerebro}")
                return
            if escolha not in ("openrouter", "gemini", "codex"):
                self.ui.write_log("CEREBRO: use openrouter, gemini ou codex.")
                return
            self._cerebro = escolha
            explica = {
                "openrouter": "a escada normal: Gemini do cliente e, quando ela nao da, o Codex da VPS.",
                "gemini": "3 a 5 s, com as ferramentas, mas a cota estrangula.",
                "codex": "direto no Codex da VPS, sem cota nenhuma.",
            }[escolha]
            self.ui.write_log(f"CEREBRO: agora e {escolha} — {explica}")
            return
        if _low == "/esquecer":
            if self._openrouter is not None:
                self._openrouter.limpar()
            self.ui.write_log("CEREBRO: memoria da conversa limpa.")
            return
        if _low in ("/vozes", "/voz"):
            from core.voz_local import VozLocal as _VL
            self.ui.write_log("VOZES: " + ", ".join(_VL.nomes()))
            self.ui.write_log(
                "Ouvir uma: /voz jarvis_forte: bom dia chefe   |   "
                "Adotar: /vozpadrao jarvis_forte")
            return
        if _low.startswith("/vozpadrao "):
            from core.voz_local import VozLocal as _VL
            escolha = _cmd[11:].strip()
            if escolha not in _VL.nomes():
                self.ui.write_log(f"VOZ: nao conheco '{escolha}'. Use /vozes para ver a lista.")
                return
            if self._voz_local is None:
                self._voz_local = _VL()
            self._voz_local.voz = escolha
            self.ui.write_log(f"VOZ: agora falo com '{escolha}'.")
            threading.Thread(target=self._falar_local,
                             args=("Pronto, chefe. Essa e a minha voz agora.",),
                             daemon=True).start()
            return
        if _low.startswith("/voz "):
            from core.voz_local import VozLocal as _VL
            resto = _cmd[5:].strip()
            escolha = None
            if ":" in resto[:20]:
                cabeca, _, cauda = resto.partition(":")
                if cabeca.strip() in _VL.nomes():
                    escolha, resto = cabeca.strip(), cauda.strip()
            if resto:
                threading.Thread(target=self._voz_local_teste,
                                 args=(resto, escolha), daemon=True).start()
            return
        if _low.startswith("/codex "):
            pergunta = _cmd[7:].strip()
            if pergunta:
                threading.Thread(target=self._codex_chat_teste,
                                 args=(pergunta,), daemon=True).start()
            return

        # Sem sessao da Gemini (modo local), o texto digitado vai para o mesmo
        # cerebro que atende a voz. Antes ele caia no aviso de "me reconectando"
        # e nunca era respondido.
        if self._modo_local and self._cerebro != "gemini":
            # A rede do Vision Control vem ANTES do cerebro. Ela ficava mais
            # abaixo, depois deste `return`, entao no modo local nunca era
            # alcancada — ver `_rede_vision_local`.
            if self._rede_vision_local(_cmd, falar=True):
                return
            if self._redes_do_pc(_cmd):
                return
            threading.Thread(target=self._responder_texto_local,
                             args=(_cmd,), daemon=True).start()
            return

        if not self._loop or not self.session:
            # Antes a mensagem sumia sem aviso e o cliente ficava achando que
            # o JARVIS o estava ignorando. Agora ele avisa e marca o problema,
            # para o vigia reconectar.
            _voice_diag("texto_descartado", "sem sessao ativa")
            try:
                self.ui.write_log("SIS: Ainda me reconectando — repita em instantes.")
            except Exception:
                pass
            self._marcar_pedido_pendente()
            return
        if self._pending_delete_args and _delete_confirmation_decision(text):
            asyncio.run_coroutine_threadsafe(
                self._handle_pending_delete_confirmation(text, source="typed"),
                self._loop
            )
            return
        action = _vision_command_action(text)
        if action:
            asyncio.run_coroutine_threadsafe(
                self._handle_direct_vision_command(text, speak_result=True, source="typed"),
                self._loop
            )
            return
        # Pedido de edição com uma imagem já carregada: dispara aqui mesmo.
        # Sem isto, acontecia de o modelo apenas conversar ("vou refazer, já
        # fica pronta") sem nunca chamar a ferramenta, e o cliente esperava por
        # uma imagem que ninguém tinha começado.
        if self._pedido_de_edicao_de_imagem(text):
            asyncio.run_coroutine_threadsafe(
                self._editar_imagem_carregada(text),
                self._loop
            )
            return
        self._marcar_pedido_pendente()
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    # ── Edição de imagem disparada pelo próprio programa ──────────────────────
    # O modelo às vezes só conversava ("pode deixar, já vou refazer") sem chamar
    # a ferramenta, e depois ainda dizia que estava processando. Quando há uma
    # imagem carregada e o pedido é claramente de edição, o programa não espera
    # pelo modelo: começa na hora.

    _EDICAO_VERBOS = (
        "edita", "edite", "editar", "muda", "mude", "mudar", "troca", "troque",
        "trocar", "refaz", "refaça", "refaca", "refazer", "altera", "altere",
        "alterar", "coloca", "coloque", "colocar", "tira", "tire", "tirar",
        "remove", "remova", "remover", "melhora", "melhore", "melhorar",
        "arruma", "arrume", "arrumar", "conserta", "conserte", "transforma",
        "transforme", "transformar", "deixa", "deixe", "pinta", "pinte",
        "aumenta", "diminui", "corta", "corte", "recorta",
    )
    # Com uma imagem na tela, "troque a roupa por azul" já é sobre ela: o
    # cliente não repete "na imagem" a cada frase. Por isso não exigimos que
    # ele cite a imagem — exigimos que NÃO esteja falando de outra coisa.
    _EDICAO_OUTRO_ALVO = (
        "youtube", "whatsapp", "musica", "video", "navegador", "chrome", "site",
        "volume", "alarme", "despertador", "lembrete", "email", "e-mail",
        "pasta", "arquivo", "documento", "planilha", "tela", "camera", "webcam",
        "computador", "programa", "aplicativo", "janela", "senha", "wifi",
        "noticia", "clima", "tempo", "leads", "empresa", "contato", "mensagem",
    )

    def _pedido_de_edicao_de_imagem(self, texto: str) -> bool:
        """Só vale com uma imagem carregada e um pedido claro de alteração."""
        atual = getattr(self.ui, "current_file", None)
        if not atual:
            return False
        try:
            if Path(str(atual)).suffix.lower() not in {
                ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"
            }:
                return False
        except Exception:
            return False

        t = unicodedata.normalize("NFD", str(texto or "").lower())
        t = "".join(c for c in t if unicodedata.category(c) != "Mn").strip()
        if not t:
            return False
        # Perguntas sobre a imagem continuam com o modelo (ele descreve melhor).
        if any(p in t for p in ("o que tem", "o que e", "descreve", "descreva",
                                "analise", "analisa", "que imagem", "quem e")):
            return False
        if not any(v in t for v in self._EDICAO_VERBOS):
            return False
        # Falando de outra coisa ("coloca música no YouTube"), o modelo resolve.
        if any(alvo in t for alvo in self._EDICAO_OUTRO_ALVO):
            return False
        return True

    # Quanto esperar antes de reenviar um pedido que o servidor não respondeu.
    # Medido nas conversas reais: respostas legítimas chegaram em 2,7 a 11,4 s.
    _ESPERA_ANTES_DE_REENVIAR = 6.0

    async def _reenviar_se_continuar_mudo(self, texto: str) -> None:
        """Reenvia um pedido só se o servidor realmente não responder."""
        marca = self._ultima_resposta
        await asyncio.sleep(self._ESPERA_ANTES_DE_REENVIAR)
        if self._ultima_resposta != marca:
            # Veio resposta durante a espera: era só atraso.
            _voice_diag("reenvio_cancelado", "resposta chegou atrasada")
            return
        if not self.session:
            return
        _voice_diag("reenvio", texto[:80])
        try:
            self.ui.write_log("SIS: Repetindo seu pedido — o servidor não respondeu.")
        except Exception:
            pass
        try:
            await self.session.send_client_content(
                turns={
                    "parts": [{
                        "text": (
                            "[VOICE TRANSCRIPT FALLBACK] The previous audio turn ended "
                            "without a response or tool call. Treat the transcript below "
                            "as the user's command, execute it exactly once, and answer in "
                            f"{'English' if _em_ingles() else 'Brazilian Portuguese'}.\n\n{texto}"
                        )
                    }]
                },
                turn_complete=True,
            )
        except Exception as exc:
            _voice_diag_exception("reenvio_falhou", exc)

    def _ouvido_para_video(self):
        """O whisper do ouvido, emprestado para a legenda do `editar_video`.

        Carregar um segundo modelo so para a legenda custaria de 0,5 a 3 GB a
        mais; o do ouvido ja esta na memoria (ou na placa de video)."""
        if self._whisper is None:
            self._whisper, self._whisper_na_placa = _abrir_whisper()
        return self._whisper

    async def _editar_imagem_falada(self, texto: str) -> None:
        """Edita a imagem pedida por voz, garantindo que a trava sempre saia."""
        try:
            await self._editar_imagem_carregada(texto)
        finally:
            self._edicao_em_curso = False

    async def _editar_imagem_carregada(self, texto: str) -> None:
        pedido = str(texto or "").strip()
        solto = pedido.lower()
        if "do seu jeito" in solto or "como voce quiser" in solto or "você quiser" in solto:
            pedido = (f"{pedido}. O cliente deixou a critério: faça uma releitura criativa e "
                      f"caprichada desta imagem, mantendo o assunto principal reconhecível.")
        loop = asyncio.get_running_loop()
        args = {
            "kind": "image_edit",
            "description": pedido,
            "project_name": "JarvisImagem",
        }
        try:
            self.ui.write_log("SIS: Comecei a editar a imagem.")
        except Exception:
            pass
        resultado = await loop.run_in_executor(
            None,
            lambda: remote_codex_job(parameters=args, player=self.ui, speak=self.speak),
        )
        # Conta ao modelo o que aconteceu, para ele falar com o cliente.
        if self.session:
            self._marcar_pedido_pendente()
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text":
                        f"[EDICAO_DE_IMAGEM_CONCLUIDA] {resultado} "
                        f"Conte o resultado ao cliente em uma frase curta, em primeira pessoa. "
                        f"Se deu certo, diga que a imagem está pronta e já foi aberta."}]},
                    turn_complete=True,
                )
            except Exception:
                pass

    async def _handle_direct_vision_command(self, text: str, speak_result: bool = False, source: str = "voice") -> bool:
        action = _vision_command_action(text)
        if not action:
            return False
        now = time.monotonic()
        # Se o modelo já chamou a ferramenta nesta mesma fala, não executa em duplicidade.
        if now - self._last_vision_tool_at < 2.5:
            return True
        if now - self._last_direct_vision_at < 1.0:
            return True
        self._last_direct_vision_at = now

        self.ui.set_state("THINKING")
        self.ui.write_log(f"[vision_control] {action}")
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: vision_control(parameters={"action": action}, player=self.ui),
            )
        except Exception as exc:
            result = f"Vision Control falhou: {exc}"
            traceback.print_exc()
        self.ui.write_log(f"Vision Control: {result}")
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        if speak_result:
            self.speak(str(result))
        return True

    def _rede_vision_local(self, texto: str, falar: bool = True) -> bool:
        """Liga/desliga o Vision Control no MODO LOCAL, sem depender do modelo.

        POR QUE (01/09/2026, queixa do Diego: "ele fala que ativou mas nao
        ativou"). A mesma rede de seguranca ja existia em dois lugares — no
        texto digitado com a Gemini e na voz da Gemini — mas o modo local, que e
        o que ele usa, nao passava por nenhum dos dois:

          voz digitada -> `_on_text_command` saia ANTES da rede, na linha que
                          manda o texto para o cerebro local;
          voz falada   -> `_pipeline_local` ia direto do whisper para o
                          OpenRouter, sem rede nenhuma.

        Sobrava so o modelo escolher a ferramenta `vision_control` entre as 28 —
        e quando ele nao escolhe, ele NAO diz que nao consegue: ele responde que
        ativou. Era exatamente isso que o dono estava ouvindo.

        Aqui a decisao e do codigo, nao do modelo: se a frase pede Vision
        Control, a ferramenta roda. Devolve True quando assumiu o pedido — quem
        chamou nao deve mandar a frase para o cerebro depois.

        Roda numa thread propria porque `vision_control(start)` espera ate 8 s
        pela camera; segurar a janela do JARVIS por 8 s pareceria travamento.
        """
        acao = _vision_command_action(texto)
        if not acao:
            return False
        agora = time.monotonic()
        # O modelo pode ter chamado a ferramenta nesta mesma fala: nao repetir.
        if agora - self._last_vision_tool_at < 2.5:
            return True
        if agora - self._last_direct_vision_at < 1.0:
            return True
        self._last_direct_vision_at = agora
        threading.Thread(target=self._executar_vision_local,
                         args=(acao, bool(falar)), daemon=True).start()
        return True

    def _executar_vision_local(self, acao: str, falar: bool) -> None:
        self.ui.write_log(f"[vision_control] {acao}")
        try:
            resultado = vision_control(parameters={"action": acao}, player=self.ui)
        except Exception as exc:
            resultado = f"Vision Control falhou: {exc}"
            traceback.print_exc()
        self.ui.write_log(f"Vision Control: {resultado}")
        if falar:
            try:
                self._falar_local(str(resultado))
            except Exception as exc:
                _voice_diag("vision_local_sem_voz", f"{type(exc).__name__}: {str(exc)[:90]}")

    # ------------------------------------------------------------------
    # Olhar e clicar / organizar pasta: o que o CODIGO decide, sem o modelo
    # ------------------------------------------------------------------
    # O que conta como RESPOSTA a uma pergunta de sim ou nao. Serve para
    # separar o dono ("pode sim") do que a televisao esta falando na sala.
    _RESPOSTA_SIM_NAO = re.compile(
        r"\b(sim|nao|não|pode|podes|manda|vai|isso|claro|autorizo|autorizado|libera|"
        r"liberado|confirmo|confirma|ok|okay|beleza|blz|positivo|negativo|"
        r"cancela|cancelar|para|pare|deixa|esquece|nem|jamais|de jeito nenhum)\b", re.I)
    if _em_ingles():
        # Cliente em ingles (24/09/2026). "no" so entra aqui: em portugues ele
        # e "em + o" e deixaria a TV responder no lugar do dono.
        _RESPOSTA_SIM_NAO = re.compile(
            r"\b(yes|yeah|yep|yup|no|nope|sure|ok|okay|go ahead|do it|of course|"
            r"confirm|confirmed|cancel|stop|don'?t|never|sim|nao|não)\b", re.I)
    _PARAR_PC = re.compile(
        r"^\s*((" + _ALT_NOMES + r")[,!]?\s*)?(para|pare|parar|chega|cancela|cancelar|"
        r"esquece|deixa|deixa pra la|pode parar|para tudo|stop|"
        r"cancel|enough|never mind|forget it)\b", re.I)
    # "PARE DE USAR O COMPUTADOR" — a frase de PARAR carrega as palavras que
    # LIGAM o computer use. Em 03/09/2026 (no Bob) o dono mandou parar e viu o
    # mouse continuar andando: o gatilho de ligar casava com "computer user" e
    # comecava tarefa nova. Aqui o par (verbo de parar + mencao ao computador) e
    # reconhecido ANTES, e vale mesmo com o JARVIS parado. Exige os dois juntos de
    # proposito: "para" sozinho e preposicao ("pesquisa para mim no YouTube").
    _PARAR_COMPUTER_USE = re.compile(
        # O WHISPER NAO ESCREVE "computer user". Em 03/09/2026 ele transcreveu
        # "para o Compute User" e "O aplicativo Computer Use pode parar" — e o
        # freio, escrito com a grafia certinha, nao reconheceu nenhuma das duas:
        # a frase caiu no modelo, que chamou `fechar_app`. Agora a marca e
        # `comp[uú]t...us...`, que cobre computer/compute/computador + user/use/
        # usar, e o verbo de parar vale ANTES ou DEPOIS da mencao.
        r"(\b(para|pare|parar|chega|cancela|cancelar|encerra|encerrar|desliga|desligar|"
        r"pausa|pausar|interrompe|interromper)\b"
        r"[^.!?]{0,30}?\b(comp[uú]t\w*\s*us\w*|de (usar|mexer)|"
        r"usar o computador|mexer no computador)\b)"
        # Na ordem inversa ("computer use pode parar") o "para" SOZINHO nao
        # entra: em portugues ele e preposicao muito mais vezes do que verbo, e
        # "computer user, pesquisa PARA mim a placa..." virava pedido de PARAR —
        # o assistente respondia "parei" e nao fazia nada (03/09/2026). Aqui so
        # valem formas que nao sao preposicao: pare, parar, pode parar, chega...
        r"|(\b(comp[uú]t\w*\s*us\w*)\b[^.!?]{0,30}?"
        r"\b(pode parar|pare|parar|chega|cancela|cancelar|encerra|encerrar|"
        r"desliga|desligar|pausa|pausar)\b)"
        # Em ingles (24/09/2026): "stop using the computer", "cancel computer use".
        r"|(\b(stop|cancel|quit|end|pause)\b[^.!?]{0,30}?"
        r"\b(comp\w*\s*us\w*|using the computer|the computer)\b)", re.I)
    # ── "ATIVAR COMPUTADOR": a UNICA porta do computer use no JARVIS ─────────
    #
    # Regra fechada com o Diego em 04/09/2026. No Bob a palavra e "Bob,
    # ativar"; aqui e "ativar computador" — o dono fala isso, o JARVIS pergunta o
    # que fazer, e a PROXIMA frase e a tarefa. Falar tudo junto ("ativar
    # computador, abre a pasta de fotos") tambem vale.
    #
    # A palavra COMPUTADOR e obrigatoria de proposito. "Ativa" sozinho aparece
    # o tempo todo numa conversa normal ("ativa o wi-fi", "ativa o modo
    # economico", "ativa a camera 2") e ligaria o controle da tela sem ninguem
    # pedir — que e exatamente o que o cliente NAO quer. Com a palavra junto,
    # nao ha frase inocente que caia aqui.
    #
    # As grafias que o Whisper inventa entram todas: "ativar o computador",
    # "ativa computador", "JARVIS, ativar computador", "ativar computer use",
    # "ativar o PC".
    # "ATIVE E AGUARDE" NAO E TAREFA (08/10/2026). No registro do dia o ouvido
    # entendeu "Va computador", o cerebro chamou a ferramenta com "Ative o
    # controle do computador e aguarde minhas proximas instrucoes" e o laco
    # ficou 95 s fotografando a tela e esperando. Pedido assim vira o mesmo
    # "ativar computador": ele pergunta o que fazer e espera a proxima frase.
    _SO_ATIVAR_PC = re.compile(
        r"\b(aguard\w*|esper\w*|fique\s+(pronto|esperando|no\s+aguardo))\b.{0,40}"
        r"\b(instru\w*|ordens?|comandos?|pedidos?|tarefas?)\b"
        r"|\bpr[oó]xim\w*\s+(instru\w*|ordens?|comandos?|pedidos?|tarefas?)\b"
        r"|^\W*(ative|ativar|ativa|ligue|ligar|liga)\s+(o\s+)?(controle\s+do\s+)?"
        r"(comp[uú]t\w*|pc)(\s+us\w*)?\W*$", re.I)

    _ATIVAR_PC = re.compile(
        r"\b(ativa|ative|ativar|ativado|liga|ligue|ligar)\s+"
        r"(o\s+|no\s+|meu\s+|esse\s+|este\s+)?"
        r"(comp[uú]t\w*(\s*us\w*)?|pc)\b"
        # Em ingles (24/09/2026): "activate computer", "turn on computer use".
        r"|\b(activate|enable|turn\s+on|start)\s+(the\s+|my\s+)?"
        r"(computer(\s*us\w*)?|pc)\b", re.I)

    # ── FALAR SOBRE O COMPUTER USE NAO PODE LIGAR O COMPUTER USE ─────────
    #
    # Defeito real, 05/09/2026, gravando video com o cliente. O Diego disse:
    #   "NAO quero que voce ATIVE O COMPUTADOR, eu quero que voce EXPLIQUE
    #    pra rapaziadinha o poder que voce tem"
    # ...e o JARVIS minimizou a janela e comecou a usar o computador. A frase
    # tem as palavras do gatilho, e o gatilho nao enxergava a NEGACAO nem a
    # diferenca entre PEDIR e FALAR SOBRE.
    #
    # Sao duas travas separadas de proposito:
    #   `_ATIVAR_NEGADO`  -> "nao/sem/nem" perto do verbo de ativar;
    #   `_FALAR_SOBRE_PC` -> o pedido e de EXPLICACAO ("explica", "o que voce
    #                        consegue fazer", "quais as funcionalidades").
    # Qualquer uma das duas manda a frase para o cerebro, que RESPONDE — que
    # era exatamente o que ele tinha pedido.
    # PRECISA DIZER O QUE NAO E PARA ATIVAR (08/09/2026).
    #
    # Sem o objeto no fim, esta regra casava com "NAO USE acento em nome de
    # arquivo" — e o efeito era grave: a frase caia no ramo "so falando",
    # a espera pela tarefa era cancelada e o pedido do dono, ja com o computer
    # use ligado, escorria para o `dev_agent`. Ele viu a tarefa inteira ir
    # para o lugar errado por causa de duas palavras no fim do pedido.
    #
    # "nao use acento" nao fala do computador; "nao mexe no meu computador",
    # "sem ativar o computer use" e "nao liga o PC agora", sim. O objeto e o
    # que separa os dois.
    _ATIVAR_NEGADO = re.compile(
        r"\b(nao|não|nunca|jamais|sem|nem)\b[^.!?]{0,45}?"
        r"\b(ativa|ative|ativar|ativado|liga|ligue|ligar|mexe|mexer|usa|usar|use)\b"
        r"[^.!?]{0,30}?\b(computador|computer|conputer|compute|pc|maquina|máquina|"
        r"tela|mouse|teclado|meu pc)\b"
        r"|\b(don'?t|do not|never|without)\b[^.!?]{0,45}?"
        r"\b(activate|enable|turn on|use|touch)\b[^.!?]{0,30}?"
        r"\b(computer|pc|screen|mouse|keyboard)\b",
        re.I)
    _FALAR_SOBRE_PC = re.compile(
        r"\b(explica|explique|explicar|explicando|conta|conte|contar|contando|"
        r"apresenta|apresente|apresentar|descreve|descreva|descrever|"
        r"fala (sobre|de|do|da)|fale (sobre|de|do|da)|falar (sobre|de|do|da)|"
        r"da um resumo|de um resumo|um resumo|resume|resumir|"
        r"o que (voce|vc|tu) (pode|podes|consegue|sabe|faz)|"
        r"quais (sao|as|os)|que tipo de|"
        r"funcionalidade|funcionalidades|funcao nova|nova funcao|recursos|"
        r"como funciona|pra que serve|para que serve|serve para|"
        r"do que (voce|vc) e capaz|no que (voce|vc) ajuda|"
        r"explain|describe|tell me about|what can you do|how does it work)\b", re.I)

    # CORRECAO NO MEIO DO CAMINHO. O dono esta vendo a tela: se o JARVIS foi para o
    # lugar errado, ele fala e o JARVIS CORRIGE sem parar e sem recomecar. Vale so
    # enquanto o computer use roda; frase que nao casa aqui continua sendo
    # conversa normal ("o que e um orcamento?" no meio da tarefa, que aconteceu
    # no teste de 03/09, tem de continuar sendo respondida como pergunta).
    _CORRIGIR_PC = re.compile(
        r"\b(errado|erradinho|nao e (esse|essa|isso|ali|isso nao)|nao era|"
        r"o certo e|o correto e|na verdade|corrige|corrija|corrigir|"
        r"muda|mude|mudar|troca|troque|trocar|em vez de|ao inves de|"
        r"volta|volte|voltar|espera|espere|opa|calma|"
        r"clica|clique|aperta|abre|abra|entra|procura|pesquisa|desce|sobe|rola|"
        r"e (o|a|no|na)\s|tem que|precisa)\b", re.I)
    # O nome antigo, dito sem ativar: serve para ENSINAR a porta, nao para abrir.
    _USAR_PC = re.compile(
        # Mesma tolerancia do freio: "compute user", "computer use", "computador
        # user" — e como o Whisper escreve o que o dono fala.
        r"\b(comp[uú]t\w*\s*us\w*|usa(r)? o computador|use o computador|"
        r"assum[ei]r? o computador|toma conta do computador|mexe no computador|"
        r"faz sozinho|faca sozinho|faz tudo sozinho|modo automatico|"
        r"use the computer|take over the computer|control the computer)\b", re.I)
    _DESFAZER = re.compile(
        r"\b(desfaz|desfazer|desfaça|desfaca|reverte|reverter|volta como (estava|tava)|"
        r"voltar como (estava|tava)|volta tudo|deixa como (estava|tava)|"
        r"bota de volta|poe de volta|coloca de volta|traz de volta|restaura|restaurar|"
        r"recupera|recuperar)\b", re.I)
    # Pedido de clicar/selecionar algo que esta na tela: vai direto ao
    # olhar-e-clicar, sem o modelo escolher ferramenta.
    _CLICAR = re.compile(
        r"\b(clica|clique|clicar|cliqu[ei]|da um clique|dois cliques|clique duplo|"
        r"seleciona|selecione|selecionar|aperta o botao|aperte o botao|"
        r"aperta em|aperte em)\b", re.I)
    # A TAREFA DE VARIOS PASSOS NAO PODE SER SEQUESTRADA POR UMA REGRA LOCAL
    # (05/09/2026). Foi o que estragou "abre o meu bloco de notas E DIGITA um
    # script de calculadora": a palavra "digita" fez a regra local assumir a
    # frase inteira e chamar so o digitador — que nao abre programa nenhum e
    # nao tem ferramenta. O cerebro respondeu "nao consigo, nenhuma ferramenta
    # esta disponivel"... e essa frase foi DIGITADA dentro do bloco de notas.
    #
    # Quando ha mais de uma acao na frase, quem manda e o cerebro: ele agora
    # encadeia (abrir -> escrever -> salvar), um passo por vez.
    _TAREFA_DE_VARIOS_PASSOS = re.compile(
        r"\b(abre|abrir|abra|salva|salvar|salve|fecha|fechar|feche|cria|criar|crie|"
        r"manda|mandar|envia|enviar|pesquisa|pesquisar|busca|buscar|toca|tocar|"
        r"baixa|baixar|renomeia|renomear|copia|copiar|move|mover)\b", re.I)

    # ── "DESENHA UMA CASA" NAO E TRABALHO DO OLHAR-E-CLICAR (07/09/2026) ──
    #
    # Era: o modelo escolhia `usar_computador`, que manda uma foto da tela para
    # a VPS e traz UM movimento de mouse por volta, a ~5 s cada. Quatro voltas
    # depois, o desenho da casa eram quatro riscos. Desenho precisa de dezenas
    # de movimentos seguidos — isso nao pode ir passo a passo pela rede.
    #
    # Aqui a frase vai direto para `desenhar_na_tela`. Mesma decisao do
    # `_DIGITAR`: o que e certo sai daqui, na hora, de graca.
    #
    # `_DESENHAR_COM_ASSUNTO` exige o que vem DEPOIS do verbo ("desenha UMA
    # casa"), para "voce sabe desenhar?" continuar sendo pergunta e ser
    # respondida pelo cerebro. E `dezenh` esta na lista porque o ouvido erra:
    # mesma licao do "escreve" que o whisper transcreveu "descreve" (05/09).
    _DESENHAR = re.compile(r"\b(desenh\w*|dezenh\w*|rabisc\w*|ilustr\w*)\b", re.I)
    _SO_ABRIR = re.compile(r"\b(abre|abrir|abra|abri)\b", re.I)

    # ── PERGUNTA NAO E ORDEM (07/09/2026) ─────────────────────────────
    #
    # O Diego perguntou *"e verdade que voce sabe desenhar, cara?"* e depois
    # *"voce sabe desenhar o que, por exemplo?"* — e ouviu de volta "para eu
    # desenhar eu preciso mexer no seu computador, diga ativar computador". Ele
    # nao tinha mandado desenhar nada: estava PERGUNTANDO. A regra que ele deu:
    # *"o JARVIS precisa entender o que e uma pergunta e o que e pra realmente
    # fazer"*.
    #
    # O `_FALAR_SOBRE_PC` ja pegava "o que voce sabe fazer", mas nao pega
    # "voce sabe desenhar O QUE" — a mesma palavra, na outra ordem. E o
    # `_DESENHAR_COM_ASSUNTO` casava com "desenhar o que" porque o "o" esta na
    # lista de artigos.
    #
    # O que separa uma coisa da outra e simples: ORDEM tem ASSUNTO ("desenha
    # uma casa"); pergunta tem palavra de pergunta e nao tem assunto ("desenha
    # o que", "sabe desenhar?", "quais voce consegue desenhar").
    _PERGUNTA_DESENHO = re.compile(
        r"\b(o que|oque|o qu[eê]|que tipo|quais|qual|como|de que jeito|"
        r"e verdade|é verdade|sera que|será que|voce sabe|você sabe)\b", re.I)
    # "sabe/consegue/pode desenhar" — capacidade, nao pedido... a nao ser que
    # venha um assunto atras ("voce consegue desenhar uma casa?" e pedido).
    _PODE_DESENHAR = re.compile(
        r"\b(sabe|sabes|saberia|consegue|consegues|conseguiria|pode|podes|"
        r"poderia|da pra|dá pra|da para|dá para|capaz de|bom em|boa em)\b"
        r"[\s\w,]{0,25}?\b(desenh\w*|dezenh\w*|rabisc\w*|ilustr\w*)\b", re.I)
    # O ASSUNTO E O QUE VEM DEPOIS DO VERBO — e a lista e de quem NAO conta,
    # nao de quem conta (07/09/2026, defeito que custou um desenho inteiro).
    #
    # Antes eu listava os artigos que valiam como assunto: um, uma, o, a, meu,
    # esse... O Diego disse *"eu quero que voce desenhe VARIOS predios"* e
    # "varios" nao estava na lista. A frase deixou de ser desenho, caiu no
    # computer use, e o resultado foi uma folha com quatro retangulos pretos
    # vazios — desenhados um movimento de mouse por vez, que e exatamente o
    # problema que esta ferramenta nasceu para resolver.
    #
    # Lista de permissao em linguagem natural nunca fecha: sempre falta uma
    # palavra. Agora vale o contrario — DEPOIS do verbo, qualquer palavra e
    # assunto, MENOS as que fazem da frase uma pergunta (o que, qual, como),
    # os pronomes ("quais desenhos VOCE consegue fazer") e os adverbios soltos
    # ("desenha BEM?").
    _ASSUNTO_DO_DESENHO = re.compile(
        r"\b(desenh\w*|dezenh\w*|rabisc\w*|ilustr\w*)\b[\s,]+"
        r"(?!(?:o\s+)?qu[eê]\b|qual\b|quais\b|como\b|quando\b|onde\b|"
        r"quant\w*\b|voc[eê]\b|vc\b|tu\b|eu\b|ele\b|ela\b|"
        r"a\s+gente\b|o\s+senhor\b|senhor\b|"
        r"bem\b|mal\b|melhor\b|direito\b|mesmo\b|tamb[eé]m\b|ainda\b|"
        r"pra\s+mim\b|para\s+mim\b|sozinh\w*\b)"
        r"[a-zA-Zà-úÀ-Ú0-9]{2,}", re.I)
    # ── A REGRA GERAL: ORDEM TEM TAREFA (07/09/2026) ──────────────────
    #
    # O Diego cobrou duas vezes, e da segunda foi sobre o computer use:
    # *"eu faco pergunta sobre computer use e ele so fala 'para ativar diga
    # ativar computador'; ele precisa entender o que e pergunta"*.
    #
    # O que aconteceu: "a funcao do Computer User, o que e que voce pode fazer
    # pra mim?" tem as palavras "computer use" e caia no `_USAR_PC`, que
    # responde a recusa da porta. E o `_FALAR_SOBRE_PC` nao salvava porque ele
    # espera "o que VOCE pode" e o Diego disse "o que E QUE voce pode" — as
    # mesmas palavras, com duas no meio.
    #
    # Em vez de correr atras de cada jeito de perguntar, a conta e a mesma que
    # ja resolveu o desenho, so que geral: PERGUNTA tem palavra de pergunta e
    # NAO tem tarefa; ORDEM tem tarefa ("abre", "clica", "digita", "salva").
    # Assim "como voce usa o computador?" e conversa e "computer use, abre a
    # pasta de fotos" continua sendo ordem.
    _PALAVRA_DE_PERGUNTA = re.compile(
        r"\b(o que|oque|o qu[eê]|que tipo|que coisas|quais|qual|quantos|"
        r"como|de que jeito|quando|onde|por que|porqu[eê]|pra que|para que|"
        r"e verdade|é verdade|sera que|será que|me explica|explica|explique|"
        r"me resume|resume|resuma|me diz|me fala|me conta|fala sobre|"
        r"funcao do|funcoes do|função do|funções do|no modo geral|em geral|"
        r"serve para|serve pra|"
        # AS PERGUNTAS QUE FALTAVAM (08/09/2026). "que" sozinho nao esta na
        # lista (seria largo demais), entao "que horas sao" e "que dia e hoje"
        # nao contavam como pergunta — e, dentro da espera pela tarefa, "que
        # horas sao" tem tres palavras e virava ORDEM: ele tomava a tela para
        # responder o que sabe de cabeca. Estas entram uma a uma, e todas
        # continuam valendo so quando NAO ha verbo de tarefa na frase (ver
        # `_e_pergunta`), entao "tem como abrir o chrome" segue sendo ordem.
        # "voce consegue/voce pode" ficaram DE FORA: "voce consegue desenhar uma
        # casa pra mim?" e pedido, nao pergunta — a regra do desenho e "ordem
        # tem assunto", e po-las aqui reprovava esse caso no teste das 57.
        r"que horas|que dia|que data|quem|cad[êe]|tem como|d[áa] pra)\b",
        re.I)
    # A TAREFA. Note que "fazer" NAO esta aqui de proposito: "o que voce pode
    # FAZER" e a pergunta mais comum que existe, e ela nao manda fazer nada.
    _TEM_TAREFA_NA_TELA = re.compile(
        r"\b(abre|abrir|abra|fecha|fechar|feche|clica|clique|clicar|"
        r"digita|digite|digitar|escreve|escreva|preenche|preencher|"
        r"entra|entrar|acessa|acessar|procura|procurar|pesquisa|pesquisar|"
        r"baixa|baixar|salva|salvar|salve|copia|copiar|cola|colar|"
        r"move|mover|apaga|apagar|exclui|excluir|renomeia|renomear|"
        r"instala|instalar|configura|configurar|ajusta|ajustar|"
        r"aumenta|aumentar|diminui|diminuir|liga|ligar|desliga|desligar|"
        r"manda|mandar|envia|enviar|seleciona|selecionar|arrasta|arrastar|"
        r"organiza|organizar|coloca|colocar|poe|põe|faz login|fazer login|"
        r"minimiza|minimizar|maximiza|maximizar|restaura|restaurar|"
        r"rola|rolar|desce|descer|sobe|subir|atualiza|atualizar)\b",
        re.I)

    # Frase que COMECA mandando desenhar e ordem, ponto — mesmo com "o que"
    # depois ("me desenha o que voce quiser").
    # "NAO DESENHA NADA AGORA" e o contrario de um pedido. A janela e curta de
    # proposito — no maximo duas palavras entre a negacao e o verbo: "Nao, eu
    # quero que voce desenhe varios predios" tem quatro palavras no meio e E um
    # pedido (ali o "nao" corrige a frase anterior, nao nega o desenho). O
    # whisper nao escreve a virgula que separaria os dois casos, entao quem
    # separa e a distancia.
    _DESENHO_NEGADO = re.compile(
        r"\b(nao|não|nunca|jamais|nem)\b\s+(?:\w+\s+){0,2}?"
        r"(desenh|dezenh|rabisc|ilustr)", re.I)
    _COMECA_DESENHANDO = re.compile(
        r"^\s*(?:(?:jarvis|algieba|ei|oi|ola|olá|por favor|pf|entao|então|"
        r"cara|mano|agora|ai|aí)[,\s]+)*(?:me\s+|nos\s+)?"
        r"(desenh|dezenh|rabisc|ilustr|faz um desenho|faca um desenho|"
        r"faça um desenho)", re.I)

    # ── "ESSA CASA FICOU RUIM, MELHORA" (07/09/2026) ──────────────────
    #
    # No primeiro teste de verdade o Diego olhou o desenho e disse "melhora
    # essa casa". A frase foi parar no `editar_imagem`, que edita FOTO no
    # servidor: ele procurou uma imagem que nao existia e respondeu "arraste a
    # foto para a janela". Resposta certa para a ferramenta errada.
    #
    # Quem sabe o que ele quer sou eu: acabei de desenhar aquilo. A frase nao
    # diz o assunto ("melhora ESSA casa"), entao o assunto vem do
    # `ultimo_desenho()`. So vale por 30 minutos depois do desenho — passado
    # isso, "ficou ruim" pode ser sobre qualquer outra coisa.
    _MELHORAR_DESENHO = re.compile(
        r"\b(melhor\w*|caprich\w*|refaz|refa[cz]\w*|faz de novo|faca de novo|"
        r"de novo|tenta de novo|tente de novo|nao gostei|não gostei|"
        r"(ficou|ta|tá|esta|está) (muito )?(ruim|feio|horrivel|horrível|"
        r"esquisit\w*|estranho|torto))\b", re.I)

    # "Digita/escreve AI": digitar ao vivo na janela aberta, nao criar arquivo.
    # "digita" sozinho ja basta; "escreve"/"coda" so com o lugar junto, porque
    # "escreve uma mensagem pro Joao" e WhatsApp, nao teclado.
    _DIGITAR = re.compile(
        r"\b(digita|digite|digitar|digitando|vai codando|codando|vai escrevendo)\b|"
        r"\b(escreve|escreva|escrever|coda|codar|programa|programe)\b.{0,70}"
        r"\b(bloco de notas|notepad|editor|word|vscode|na tela|nesse arquivo|nessa janela|"
        r"aqui na tela|ai na tela|aí na tela|na frente)\b", re.I)

    # ── "SE EU DIGITAR EM DOLARES" NAO E ORDEM DE DIGITAR (08/09/2026) ────
    #
    # O `_DIGITAR` acima dispara com um "digitar" solto em qualquer lugar da
    # frase. O pedido real do dono foi:
    #
    #   "crie o arquivo cotacao.py ... e SE EU DIGITAR em dolares ele mostra
    #    quanto da em reais, convertendo enquanto eu digito"
    #
    # Ali "digitar" descreve o que o PROGRAMA vai fazer com o usuario, nao uma
    # ordem para o JARVIS teclar. Mesmo assim o atalho trocava `create_file`
    # por `digitar_na_tela`, e o JARVIS saia digitando o programa inteiro na
    # janela que estivesse na frente, em vez de salvar o arquivo.
    #
    # A conta: quem digita e o DONO ("eu digitar", "enquanto eu digito", "ao
    # digitar", "o usuario digita") nao conta. Some com esses trechos e so
    # depois pergunta se sobrou uma ordem de digitar. Assim "digita no bloco
    # de notas e salva o arquivo" continua digitando, como sempre.
    _DIGITAR_DO_USUARIO = re.compile(
        r"\b(?:eu|voc[eê]|usu[aá]rio|o usu[aá]rio|cliente|a pessoa|ele|ela)\s+"
        r"(?:digit\w+|escrev\w+)\b|\b(?:ao|quando|enquanto|conforme|se)\s+"
        r"(?:eu\s+|voc[eê]\s+)?digit\w+\b", re.I)

    def _manda_digitar(self, texto: str) -> bool:
        """True so quando o DONO esta mandando digitar na tela, agora."""
        limpo = self._DIGITAR_DO_USUARIO.sub(" ", str(texto or ""))
        return bool(self._DIGITAR.search(limpo))

    # ── A WEBCAM DO PROPRIO COMPUTADOR (04/09/2026) ───────────────────────
    #
    # "JARVIS, abre a minha webcam" falhou TRES vezes seguidas no teste, e sempre
    # por causa da escolha de ferramenta — nunca por causa da webcam:
    #   1a vez  o modelo chamou `vision_control` (que e o mouse por gestos) e
    #           inventou a acao -> "Acao invalida para Vision Control";
    #   2a vez  chamou `remote_media/webcam_start`, que abre a camera no
    #           painel do CELULAR: ele respondeu "webcam aberta" e na tela do
    #           computador nao aconteceu nada;
    #   3a vez  28 segundos para dizer "ferramenta indisponivel".
    #
    # Tres ferramentas diferentes falam em "webcam" nas descricoes, e escolher
    # entre elas nao e trabalho de modelo nenhum: e uma regra de UMA linha.
    # Mesma decisao que ja tinha sido tomada para as cameras do DVR e para a
    # hora — o que e certo, sai daqui, na hora, de graca.
    _RE_WEBCAM = re.compile(
        r"\b(web ?cam|camera do (computador|note|notebook|pc|laptop)|"
        r"camera frontal|minha camera)\b", re.I)
    _RE_WEBCAM_ABRIR = re.compile(
        r"\b(abre|abrir|abra|liga|ligar|ligue|mostra|mostrar|mostre|ativa|ativar|"
        r"ative|inicia|iniciar|quero ver|me mostra)\b", re.I)
    _RE_WEBCAM_FECHAR = re.compile(
        r"\b(fecha|fechar|feche|desliga|desligar|desligue|para|parar|pare|"
        r"encerra|encerrar|tira|tirar|some com)\b", re.I)

    # ── OLHAR PELA WEBCAM E OUTRA COISA (05/09/2026) ──────────────────────
    #
    # "Abra minha webcam" pede para MOSTRAR a camera na tela. "Da uma olhadinha
    # na minha webcam" pede para ELE OLHAR e dizer o que ve — sao intencoes
    # diferentes e a regra so cobria a primeira. A segunda ia ao modelo, que
    # tirou foto da TELA e ainda vazou JSON na fala (o Diego ouviu).
    #
    # Aqui ela vira local, e usa o caminho de VARIOS QUADROS
    # (`actions/webcam_ao_vivo.py`): quatro fotos em ~2 s numa grade so, para
    # dar conta de MOVIMENTO — uma foto sozinha nao mostra aceno nem alguem
    # entrando na frente.
    _RE_OLHAR_WEBCAM = re.compile(
        r"\b(olh\w*|da uma olhada|de uma olhada|olhadinha|v[eê]|vej\w*|ver\w*|"
        r"analis\w*|enxerg\w*|repar\w*|confer\w*|me diz o que|o que voce v[eê]|"
        r"quem (esta|ta)|estou|to (bonito|feio|arrumado))\b", re.I)

    def _quer_fechar_webcam(self, t: str) -> bool:
        """O pedido e de FECHAR a webcam? (`t` ja minusculo e sem acento)

        01/10/2026: "para" e "tira" estavam na lista de fechar como verbo, mas
        sao palavras comuns no meio da frase. "Abre a webcam PARA mim" fechava
        a webcam e respondia "Fechei a webcam, chefe"; "TIRA uma foto com a
        webcam" tambem. Agora vale o verbo que vem PRIMEIRO, e "tira uma foto"
        nao e fechar.
        """
        m_f = self._RE_WEBCAM_FECHAR.search(t)
        if not m_f:
            return False
        m_a = self._RE_WEBCAM_ABRIR.search(t)
        if m_a and m_a.start() < m_f.start():
            return False
        if re.search(r"\btir\w*\s+(uma\s+|um\s+)?(foto|print|selfie|imagem)", t):
            return False
        # "para" so e verbo quando abre a frase ("para a webcam"); no meio ela
        # e preposicao ("liga a camera para eu ver").
        if m_f.group(0) == "para" and m_f.start() > 0:
            return any(x.start() == 0 for x in self._RE_WEBCAM_FECHAR.finditer(t))
        return True

    def _rede_de_olhar_webcam(self, texto: str) -> bool:
        """Olha a webcam EM MOVIMENTO. True quando assumiu a frase."""
        import unicodedata as _u  # noqa: PLC0415
        t = _u.normalize("NFD", str(texto or "").strip().lower())
        t = "".join(c for c in t if _u.category(c) != "Mn")
        # SEM TETO APERTADO DE PALAVRAS. Era 14 e barrou o pedido real do
        # Diego, de 17: "analisa minha webcam e ve esse objeto aqui que eu
        # estou segurando na minha mao". Quem manda olhar a camera DIZ o que
        # quer que seja olhado, e isso alonga a frase por natureza. O que
        # segura a porta e falar em WEBCAM com verbo de OLHAR, nao o tamanho.
        if not t or len(t.split()) > 30 or not self._RE_WEBCAM.search(t):
            return False
        # Pedido de ABRIR/FECHAR nao e pedido de olhar — aquele ja tem regra.
        if self._quer_fechar_webcam(t):
            return False
        if not self._RE_OLHAR_WEBCAM.search(t):
            return False
        _voice_diag("webcam_olhar", str(texto)[:70])

        def _trabalho():
            try:
                from actions.webcam_ao_vivo import ver_webcam_ao_vivo  # noqa: PLC0415
                r = ver_webcam_ao_vivo(str(texto or ""), player=self.ui)
            except Exception as exc:
                _voice_diag("webcam_olhar_falhou", str(exc)[:110])
                r = "Nao consegui olhar a webcam agora, chefe."
            self._responder_curto(str(r))

        # Numa thread: sao ~2 s de captura mais a ida a VPS, e segurar aqui
        # deixaria o microfone surdo nesse tempo.
        threading.Thread(target=_trabalho, daemon=True, name="olhar-webcam").start()
        self._responder_curto("Deixa eu olhar aqui, chefe.")
        return True

    def _rede_da_webcam(self, texto: str) -> bool:
        """Webcam do PC por voz, sem modelo. True quando assumiu a frase."""
        import unicodedata as _u  # noqa: PLC0415
        t = _u.normalize("NFD", str(texto or "").strip().lower())
        t = "".join(c for c in t if _u.category(c) != "Mn")
        if not t or len(t.split()) > 9 or not self._RE_WEBCAM.search(t):
            return False
        # FECHAR PRIMEIRO: "fecha a webcam" tem as duas coisas na frase.
        if self._quer_fechar_webcam(t):
            try:
                self.ui.stop_camera_stream()
            except Exception as exc:
                _voice_diag("webcam_falhou", str(exc)[:80])
                self._responder_curto("Nao consegui fechar a webcam.")
                return True
            _voice_diag("webcam_local", "fechou")
            self._responder_curto("Fechei a webcam, chefe.")
            return True
        if not self._RE_WEBCAM_ABRIR.search(t):
            return False
        try:
            self.ui.start_camera_stream()
        except Exception as exc:
            _voice_diag("webcam_falhou", str(exc)[:80])
            self._responder_curto("Nao consegui abrir a webcam.")
            return True
        _voice_diag("webcam_local", "abriu")
        self._responder_curto("Webcam aberta, chefe.")
        return True

    # ── COWORK: O PROGRAMADOR DO JARVIS (05/10/2026) ───────────────────────
    #
    # O dono abre uma pasta de projeto (botao COWORK ou "abre o cowork na pasta
    # X") e pede: "faz um jogo da cobrinha", "a cor do botao esta errada". O
    # trabalho todo (mandar a pasta, aplicar, testar, corrigir) esta em
    # actions/cowork.py e leva minutos — roda numa thread, e no fim a frase do
    # resultado sai pelo `_responder_curto`, como o site.
    #
    # A REGRA LOCAL E ESTREITA DE PROPOSITO. Sem a palavra "cowork", so assume a
    # frase com um projeto JA ABERTO e um pedido claro de programacao ("muda a
    # COR do BOTAO", "o jogo ta bugado"). Sem projeto aberto, nada muda: "cria
    # um site" continua indo para o site de sempre.
    _RE_COWORK_NOME = re.compile(r"\b(?:co[\s-]?w[ou]rk\w*|cou?work\w*|kowork\w*|coworque)\b", re.I)
    _VERBOS_DEV = (r"(?:cria|crie|criar|faz|faca|fazer|monta|monte|desenvolve|desenvolva|programa|"
                   r"programe|implementa|implemente|adiciona|adicione|adicionar|coloca|coloque|colocar|"
                   r"poe|bota|bote|muda|mude|mudar|troca|troque|trocar|altera|altere|alterar|arruma|"
                   r"arrume|arrumar|corrige|corrija|corrigir|conserta|conserte|consertar|melhora|melhore|"
                   r"melhorar|remove|remova|tira|tire|aumenta|aumente|diminui|diminua|deixa|deixe|"
                   r"ajusta|ajuste|refaz|refaca|atualiza|atualize|inclui|inclua|acrescenta|acrescente)")
    _COISAS_DEV = (r"(?:jogo|game|app|aplicativo|sistema|programa|projeto|codigo|site|pagina|tela|"
                   r"botao|botoes|menu|cor|cores|fundo|layout|design|personagem|fase|fases|nivel|"
                   r"inimigo|inimigos|pontuacao|placar|pontos|vida|vidas|login|cadastro|formulario|"
                   r"banco de dados|funcao|animacao|efeito sonoro|fonte|titulo|logo|icone|cabecalho|"
                   r"rodape|tabela|grafico|calculadora|carrinho|modo escuro|tema|nave|cobra|bola|"
                   r"mapa|placar|ranking|tela inicial|tela de inicio|cena)")
    _RE_PEDIDO_DEV = re.compile(r"\b" + _VERBOS_DEV + r"\b(?:\W+\w+){0,6}?\W+" + _COISAS_DEV + r"\b")
    _RE_QUEIXA_DEV = re.compile(
        r"\b(?:(?:ta|esta|ficou|tao|estao|ficaram)\s+(?:muito\s+)?(?:bugad[oa]s?|errad[oa]s?|quebrad[oa]s?|"
        r"feios?|feias?|estranh[oa]s?|tort[oa]s?|travando|travad[oa]s?|lent[oa]s?)|"
        r"(?:deu|dando|da|aparece|apareceu)\s+(?:um\s+)?erro|"
        r"nao\s+(?:esta|ta|tao)\s+(?:funcionando|abrindo|carregando|aparecendo|mexendo|pulando)|"
        r"\bbug\b|bugou|travou)")
    # Assunto do COMPUTADOR, nao do projeto: "muda a cor do papel de parede".
    _RE_NAO_E_PROJETO = re.compile(
        r"\b(?:papel de parede|wallpaper|windows|computador|\bpc\b|notebook|volume|brilho|mouse|teclado|"
        r"navegador|chrome|edge|youtube|whatsapp|mensagem|e-?mail|spotify|musica|area de trabalho|"
        r"webcam|camera|microfone|sua voz|voce mesmo|seu nome|print|printa|captura|screenshot|"
        r"foto da tela|lembrete|alarme|agenda do|calendario)\b")
    _RE_PERGUNTA = re.compile(r"^(?:como|o que|qual|quais|quanto|voce sabe|voce consegue|da pra|"
                              r"da para|sera que|pode me explicar|me explica)\b")

    def _cowork(self):
        from actions.cowork import COWORK  # noqa: PLC0415
        return COWORK

    @staticmethod
    def _sem_acento_minusculo(texto: str) -> str:
        import unicodedata as _u  # noqa: PLC0415
        t = _u.normalize("NFD", str(texto or "").strip().lower())
        return "".join(c for c in t if _u.category(c) != "Mn")

    def _rede_do_cowork(self, texto: str) -> bool:
        """O Cowork assume a frase? Regra local, sem modelo. True = assumiu."""
        try:
            cw = self._cowork()
        except Exception:
            return False
        t = self._sem_acento_minusculo(texto)
        if not t:
            return False
        nucleo = re.sub(r"^\W*(?:(?:o|oi|ei|ok|beleza|entao|agora)\W+)*(?:" + _ALT_NOMES + r")?\W*", "", t)
        nucleo = re.sub(r"^(?:por favor\W+)?(?:(?:voce\s+)?(?:pode|poderia|consegue)\s+)?", "", nucleo)
        explicito = bool(self._RE_COWORK_NOME.search(t))
        if explicito:
            sem_nome = self._RE_COWORK_NOME.sub(" ", nucleo)
            sem_nome = re.sub(r"^\W*(?:(?:no|o|do|pelo|usando o|modo)\W+)*", "", sem_nome).strip(" ,.!?")
            pedido_dev = bool(self._RE_PEDIDO_DEV.search(t))
            if re.search(r"\b(?:desfaz|desfazer|desfaca|volta a (?:ultima )?mudanca|"
                         r"volta como estava|desfazer a ultima)\b", t) and not pedido_dev:
                self._cowork_em_thread(cw.desfazer)
                return True
            # "para" so cancela no COMECO ("para o cowork"); no meio e preposicao
            # ("faz um jogo para o cowork").
            if re.match(r"(?:o\s+)?(?:para|pare|parar|cancela|cancele|cancelar|interrompe|interrompa|chega)\b",
                        sem_nome) and not pedido_dev:
                self._responder_curto(cw.parar())
                return True
            # "fecha o cowork" / "sai do cowork": fecha o projeto (nao e pedido).
            if re.search(r"\b(?:fecha|fechar|feche|sai|sair|saia|desliga|desligar|desligue|encerra|"
                         r"encerrar|encerre|desativa|desativar)\b", nucleo) and not pedido_dev:
                # "fecha o JOGO" e a janela do projeto; o resto fecha o projeto.
                if re.search(r"\b(?:jogo|app|aplicativo|janela|programa|game)\b", nucleo):
                    self._cowork_em_thread(cw.fechar_janela)
                else:
                    self._cowork_em_thread(cw.fechar)
                return True
            m_pasta = re.search(r"\b(?:pasta|parte)\s+(?:d[eoa]s?\s+)?(.{2,40}?)"
                                r"(?=\s+e\s|\s*,|\s+pra\s|\s+para\s|\s*[.!?]|$)", t)
            if m_pasta and (pedido_dev or re.search(r"\b(?:abre|abrir|abra|usa|usar|use|seleciona|"
                                                    r"escolhe|trabalha|trabalhar|cria|criar|crie|muda|troca)\b", t)):
                nome_pasta = m_pasta.group(1)

                def _abre_e_faz():
                    r = self._cowork_abrir_pasta_falada(nome_pasta, t)
                    # "faz um jogo na pasta X": abre a pasta E faz o pedido.
                    if pedido_dev and r.startswith("Cowork aberto"):
                        self._cowork_iniciar(str(texto).strip())
                        return ""
                    return r
                self._cowork_em_thread(_abre_e_faz)
                return True
            if re.search(r"\b(?:roda|rodar|executa|executar|testa|testar|mostra|abre|abrir|abra)\b"
                         r"(?:\W+\w+){0,3}?\W+(?:projeto|jogo|app|aplicativo|sistema|programa|site)\b", t) \
                    and not pedido_dev:
                self._cowork_em_thread(cw.rodar)
                return True
            # So a palavra ("abre o cowork", "ativa o cowork"): escolher a pasta.
            sobra = re.sub(r"\b(?:abre|abrir|abra|ativa|ativar|ative|liga|ligar|inicia|iniciar|o|a|no|"
                           r"modo|pra mim|para mim|agora|ai|por favor)\b", " ", sem_nome).strip(" ,.!?")
            if len(sobra) < 3:
                if cw.pasta:
                    self._responder_curto(f"O Cowork está aberto no projeto {cw.pasta.name}. "
                                          "É só dizer o que você quer que eu faça.")
                else:
                    self.ui.cowork_escolher_pasta()
                    self._responder_curto("Escolha ou crie a pasta do projeto na janela que eu abri.")
                return True
            self._cowork_iniciar(str(texto).strip())
            return True

        # Sem a palavra "cowork": so com projeto aberto e pedido claro.
        if not cw.pasta or self._RE_NAO_E_PROJETO.search(nucleo) or self._RE_PERGUNTA.match(nucleo):
            return False
        if re.match(r"(?:roda|rodar|executa|testa|abre|mostra)\s+(?:o|a|de novo\s+)?\s*"
                    r"(?:projeto|jogo|app|aplicativo|sistema|programa)(?:\s+(?:de novo|denovo|ai|pra mim))?\W*$",
                    nucleo):
            self._cowork_em_thread(cw.rodar)
            return True
        if re.search(r"\b(?:desfaz|desfazer|desfaca)\b(?:\W+\w+){0,4}?\W+(?:mudanca|alteracao|isso|ultima)", t):
            self._cowork_em_thread(cw.desfazer)
            return True
        recente = cw.ocupado() or bool(cw._historico())
        if self._RE_PEDIDO_DEV.search(t) or (recente and self._RE_QUEIXA_DEV.search(t)):
            self._cowork_iniciar(str(texto).strip())
            return True
        return False

    def _cowork_em_thread(self, funcao) -> None:
        def _faz():
            try:
                r = funcao()
            except Exception as exc:
                r = f"O Cowork falhou: {str(exc)[:100]}"
            if r:
                self._responder_curto(str(r))
        threading.Thread(target=_faz, daemon=True, name="cowork-acao").start()

    def _cowork_abrir_pasta_falada(self, nome: str, frase: str) -> str:
        from actions.codex_remote import _resolver_pasta  # noqa: PLC0415
        cw = self._cowork()
        nome = re.sub(r"\b(?:do|da|no|na|pro|pra|para o|para a)?\s*co[\s-]?w[ou]rk\w*\b.*$", "", nome).strip(" ,.!?")
        pasta = _resolver_pasta(f"pasta {nome}") if nome else None
        if pasta is None:
            return "Não entendi qual pasta. Pode clicar no botão COWORK e escolher na janela."
        criar = bool(re.search(r"\b(?:cria|criar|crie|nova|novo)\b", frase))
        if not pasta.exists() and not criar:
            # nao existe na Area de Trabalho: tenta em Documentos\JARVIS Cowork
            alt = cw.pasta_de_projetos() / pasta.name
            if alt.is_dir():
                pasta = alt
            else:
                return (f"Não achei a pasta {pasta.name}. Se quiser uma nova, diga "
                        f"'cria a pasta {pasta.name} no cowork'.")
        if not pasta.exists():
            pasta = cw.pasta_de_projetos() / pasta.name
        return cw.abrir_pasta(pasta, criar=criar)

    def _cowork_iniciar(self, pedido: str, avisar: bool = True) -> str:
        """Comeca um pedido do Cowork numa thread. Devolve a frase de inicio."""
        cw = self._cowork()
        if cw.ocupado():
            msg = "Ainda estou trabalhando no pedido anterior do Cowork. Assim que terminar eu te aviso."
            if avisar:
                self._responder_curto(msg)
            return msg
        alvo = f"no projeto {cw.pasta.name}" if cw.pasta else "num projeto novo"
        inicio = (f"Beleza, vou trabalhar nisso {alvo}. Leva alguns minutos: eu programo, "
                  "testo e te aviso quando terminar.")
        ponte = getattr(self.ui, "_codex_relay", None)

        def _trabalho():
            _voice_diag("cowork_inicio", str(pedido)[:90])
            t0 = time.monotonic()
            try:
                r = cw.executar_pedido(pedido, ponte)
            except Exception as exc:
                r = f"O Cowork falhou: {str(exc)[:100]}"
            _voice_diag("cowork_fim", f"{time.monotonic() - t0:.0f}s {str(r)[:90]}")
            self._responder_curto(str(r))
        # O TRABALHO COMECA ANTES DA FALA, e a fala tambem vai para uma thread
        # (05/10/2026, queixa do Diego: "trava a tela por uns 5 segundos"). O
        # `_responder_curto` so devolve depois de FALAR a frase inteira (~8 s);
        # pedido vindo do campo do Cowork rodava isso na linha da janela.
        threading.Thread(target=_trabalho, daemon=True, name="cowork").start()
        if avisar:
            threading.Thread(target=self._responder_curto, args=(inicio,), daemon=True,
                             name="cowork-aviso").start()
        return inicio

    def _cowork_acao(self, acao: str, valor: str = "") -> None:
        """Botoes e campo do cartao do Cowork (chamado pela janela)."""
        cw = self._cowork()
        if acao == "pedido":
            # Chamado pela JANELA: nada aqui pode esperar (nem a fala).
            threading.Thread(target=self._cowork_iniciar, args=(str(valor or ""),),
                             daemon=True, name="cowork-pedido").start()
            return
        funcoes = {
            "pasta": lambda: cw.abrir_pasta(valor),
            "rodar": cw.rodar,
            "desfazer": cw.desfazer,
            "parar": cw.parar,
            "abrir_pasta": cw.abrir_pasta_no_explorer,
            "fechar": cw.fechar,
        }
        funcao = funcoes.get(str(acao or ""))
        if funcao is not None:
            self._cowork_em_thread(funcao)

    def _cowork_ferramenta(self, args: dict) -> str:
        """A ferramenta `cowork` (quando o cerebro decide por ela)."""
        cw = self._cowork()
        acao = str(args.get("acao") or "pedido").strip().lower()
        pasta = str(args.get("pasta") or "").strip()
        if pasta and acao in ("pasta", "pedido"):
            r = self._cowork_abrir_pasta_falada(pasta, pasta)
            if acao == "pasta" or not cw.pasta:
                return r
        if acao == "rodar":
            return cw.rodar()
        if acao == "desfazer":
            return cw.desfazer()
        if acao == "parar":
            return cw.parar()
        if acao == "pasta":
            self.ui.cowork_escolher_pasta()
            return "Abri a janela para o usuario escolher a pasta do projeto."
        pedido = str(args.get("pedido") or "").strip() or str(getattr(self, "_ultima_fala_do_dono", "") or "")
        return self._cowork_iniciar(pedido, avisar=False)

    # ── CRIAR SITE: SEM MODELO (22/09/2026, noite) ─────────────────────────
    #
    # Defeito que o Diego viu gravando: ele tinha acabado de criar e editar
    # uma imagem e pediu *"cria um site pra mim agora de um posto de gasolina,
    # um site bem moderno"*. O cerebro chamou `file_controller` com
    # `action=open` e ABRIU A IMAGEM ANTERIOR. Ele corrigiu ("nao, eu pedi pra
    # voce criar um site") e o cerebro so respondeu "abrindo arquivo para criar
    # o site" — sem chamar ferramenta nenhuma.
    #
    # O motivo e o mesmo da webcam: o historico recente estava cheio de
    # arquivo e imagem, e o modelo foi atras dele. Pedido de criar site nao
    # precisa de inteligencia para ser reconhecido — tem o verbo e tem a
    # palavra "site". Sai daqui direto para o servidor, como a imagem.
    _COISA_SITE = (r"(?P<coisa>sites?|website|web site|landing ?page|landing|"
                   r"paginas? (?:web|da internet|de internet|na internet|online|de vendas)|"
                   r"loja (?:virtual|online))\b")
    # ENTRE O VERBO E A PALAVRA "SITE" SO VALE ENCHIMENTO — artigo, "pra mim",
    # "agora", "moderno". Qualquer palavra de verdade no meio faz o pedido ser
    # OUTRA coisa: "cria uma IMAGEM pro meu site", "faz LOGIN no site", "faz o
    # DOWNLOAD do site". Lista do que PODE ficar no meio, e nao do que nao
    # pode: o que nao pode e infinito.
    _ENCHIMENTO_SITE = (r"(?:[\s,]+(?:pra|para|pro|mim|me|nos|a gente|gente|agora|ai|ja|"
                        r"rapidinho|rapido|por favor|favor|por gentileza|um|uma|o|a|"
                        r"meu|minha|nosso|nossa|seu|sua|novo|nova|outro|outra|lindo|"
                        r"linda|bonito|bonita|moderno|moderna|completo|completa|simples|"
                        r"profissional|voce|tipo|esse|essa|este|esta|so|la|aqui|entao|"
                        r"tambem|urgente|logo|hoje|mais|bem))*")
    _RE_CRIAR_SITE = re.compile(
        r"\b(?:cria|crie|criar|criasse|criem|faz|faca|facam|fazer|fizesse|monta|"
        r"monte|montar|montasse|desenvolve|desenvolva|desenvolver|gera|gere|gerar|"
        r"constroi|construa|construir|elabora|elabore|elaborar|produz|produza|"
        r"produzir)" + _ENCHIMENTO_SITE + r"[\s,]+" + _COISA_SITE, re.I)
    # "Quero um site" tambem e encomenda — mas so com UM/UMA: "quero o site do
    # banco" e "quero ver o site" sao outra coisa.
    _RE_QUERO_SITE = re.compile(
        r"\b(?:quero|queria|preciso de|gostaria de)\s+(?:um|uma)\s+"
        r"(?:novo\s+|nova\s+)?" + _COISA_SITE, re.I)
    # O que vem ANTES do verbo e transforma o pedido em conversa: negacao
    # ("nao quero que crie"), pergunta de como ("como criar um site") ou
    # falar SOBRE ("fala pra eles que voce cria sites"). A virgula corta: em
    # "nao, eu pedi pra voce criar um site" o "nao" e da frase anterior.
    _RE_SITE_NAO_E_PEDIDO = re.compile(
        r"\b(nao|nem|sem|como|quanto|explica|explique|fala|fale|conta|conte|"
        r"diz|diga|sabe|sabia)\b[^,.;!?]{0,25}$", re.I)

    def _pedido_de_site(self, texto: str) -> bool:
        """A frase MANDA criar um site? Regra local, sem modelo."""
        import unicodedata as _u  # noqa: PLC0415
        t = _u.normalize("NFD", str(texto or "").strip().lower())
        t = "".join(c for c in t if _u.category(c) != "Mn")
        if not t:
            return False
        achados = (list(self._RE_CRIAR_SITE.finditer(t))
                   + list(self._RE_QUERO_SITE.finditer(t)))
        for m in achados:
            if self._RE_SITE_NAO_E_PEDIDO.search(t[: m.start()]):
                continue
            # "Voce cria sites?" e pergunta sobre o que ele sabe fazer, nao
            # encomenda. Encomenda fala de UM site.
            fim = t.find("?", m.end())
            if m.group("coisa") == "sites" and fim != -1 and "pra mim" not in t:
                continue
            return True
        return False

    def _rede_do_site(self, texto: str) -> bool:
        """Cria o site no servidor, direto. True quando assumiu a frase."""
        if not self._pedido_de_site(texto):
            return False
        if getattr(self, "_site_em_andamento", False):
            self._responder_curto("Ainda estou montando o site anterior, chefe. "
                                  "Assim que ficar pronto eu te aviso.")
            return True
        _voice_diag("site_local", str(texto)[:80])
        self._site_em_andamento = True
        args = {"kind": "site", "description": str(texto).strip(),
                "project_name": "JarvisSite"}

        def _trabalho():
            try:
                r = self._executar_ferramenta_local("codex_remote", args)
            except Exception as exc:
                r = f"Nao consegui criar o site: {str(exc)[:90]}"
            finally:
                self._site_em_andamento = False
            self._responder_curto(str(r))

        # Numa thread: o site leva de 6 a 8 minutos no servidor, e segurar o
        # turno aqui deixaria o JARVIS surdo o tempo todo. O aviso "vou montar
        # o site, leva tantos minutos" sai la dentro, logo no comeco.
        threading.Thread(target=_trabalho, daemon=True, name="criar-site").start()
        return True

    # ── ABRIR PASTA E PROGRAMA: SEM MODELO (05/09/2026) ───────────────────
    #
    # Queixa do Diego, com o cronometro na mao: "abre minha pasta de downloads"
    # levou 29,3 s e depois 23,5 s. O caminho era este, e cada seta e uma ida ao
    # Codex de ~5 a 8 s:
    #     decidir a ferramenta (executar_tarefa) -> ESCREVER o comando de
    #     PowerShell -> executar -> contar que abriu.
    # Quatro voltas para um `explorer.exe Downloads`.
    #
    # Nada disso precisa de inteligencia: o nome da pasta esta na frase e o
    # Windows sabe onde ela fica. Mesma decisao ja tomada para a hora, as
    # cameras e a webcam — o que e deterministico sai daqui, na hora.
    #
    # A PORTA E ESTREITA DE PROPOSITO: frase curta, comecando com verbo de
    # abrir, e nada de "e" emendando outra tarefa ("abre o bloco de notas E
    # digita um script" e tarefa encadeada, tem de ir para o cerebro).
    # O VOCATIVO VEM DE MUITOS JEITOS (05/09/2026). O Whisper escreve "O JARVIS,
    # abre meu gerenciador de tarefas" — com artigo na frente — e a regra,
    # ancorada em "ted" puro, nao pegava: o pedido ia para o cerebro e custava
    # 11 s em vez de sair na hora. Agora aceita "o/oi/ei ted", "pode",
    # "por favor" e "quero que voce" antes do verbo.
    # O COMECO DA FRASE E QUASE SEMPRE ENFEITE. Ele se corrige em voz alta
    # ("Nao, fecha o gerenciador"), pede por favor, chama pelo nome — e cada
    # enfeite desses derrubava a regra e mandava o pedido para o Codex.
    _ENFEITE = (r"^\s*((nao|opa|espera|desculpa|na verdade|entao|ah|ai)[,!.\s]+)*"
                r"((o|oi|ei)\s+)?(" + _ALT_NOMES + r")?[,!.]?\s*"
                r"((nao|opa|espera|desculpa|na verdade|entao)[,!.\s]+)*"
                # "EU quero que voce abra..." ficava de fora por causa do "eu"
                # na frente (05/09/2026): o pedido caia no cerebro e voltava
                # como "precisa ativar o computador".
                r"(por favor[,\s]+)?(eu\s+)?(pode\s+|poderia\s+|quero que voce\s+|"
                r"quero que\s+|preciso que voce\s+|preciso que\s+|"
                r"gostaria que voce\s+|gostaria que\s+|vai\s+|va\s+)?")
    _RE_ABRIR = re.compile(
        _ENFEITE + r"(abre|abrir|abra|abri|mostra|mostrar|exibe|exibir|me abre|me abra)\b",
        re.I)
    # "PARTE" TAMBEM VALE (05/09/2026). O ouvido troca "pasta" por "parte" o
    # tempo todo — "abre minha PARTE de download" apareceu duas vezes no mesmo
    # teste. O corretor de fala NAO conserta isso de proposito (as duas sao
    # palavras de verdade e trocar uma pela outra estragaria "essa parte da
    # loja"), entao quem trata e aqui.
    #
    # E seguro porque o nome que vier depois ainda precisa SER UMA PASTA de
    # verdade: `_resolver_pasta` devolve None para "a parte de cima" e o pedido
    # segue para o cerebro, como antes.
    _RE_PASTA = re.compile(
        r"\b(?:pasta|pastas|parte)\s+(?:d[eoa]s?\s+)?(?:minha\s+|meu\s+|a\s+|o\s+)?"
        r"([\w\s\-]{2,40})", re.I)
    # Programas que o cliente pede pelo nome, com o nome que o Windows entende.
    _PROGRAMAS_CONHECIDOS = {
        "bloco de notas": "notepad", "notepad": "notepad", "bloco": "notepad",
        "calculadora": "calc", "calc": "calc",
        "explorador": "explorer", "explorador de arquivos": "explorer",
        "meu computador": "explorer", "este computador": "explorer",
        "paint": "mspaint", "word": "winword", "excel": "excel",
        "powerpoint": "powerpnt", "navegador": "chrome", "chrome": "chrome",
        "edge": "msedge", "gerenciador de tarefas": "taskmgr",
        "painel de controle": "control", "configuracoes": "ms-settings:",
        "prompt de comando": "cmd", "terminal": "wt", "powershell": "powershell",
        "lixeira": "shell:RecycleBinFolder",
        # Acrescentados em 05/09/2026, do uso real dele — cada nome aqui e uma
        # ida ao Codex (5 a 11 s) que deixa de acontecer.
        "gerenciador de dispositivos": "devmgmt.msc",
        "programas instalados": "ms-settings:appsfeatures",
        "bluetooth": "ms-settings:bluetooth", "wifi": "ms-settings:network-wifi",
        "wi fi": "ms-settings:network-wifi", "rede": "ms-settings:network",
        "impressoras": "ms-settings:printers", "som": "ms-settings:sound",
        "atualizacoes": "ms-settings:windowsupdate",
        "bloco de nota": "notepad", "notas": "notepad",
        "editor de registro": "regedit", "registro": "regedit",
        "monitor de recursos": "resmon", "servicos": "services.msc",
        "spotify": "spotify", "whatsapp": "whatsapp", "telegram": "telegram",
        "outlook": "outlook", "teams": "teams", "steam": "steam",
        "firefox": "firefox", "opera": "opera", "brave": "brave",
    }

    # FECHAR TAMBEM E TAREFA SIMPLES (05/09/2026). Nos testes do Diego, todo
    # "fecha o bloco de notas" custava 5 a 7 s no Codex enquanto o "abre" saia
    # na hora — porque so o abrir tinha regra local. Metade dos pedidos dele
    # numa sessao de teste eram fechar.
    _RE_FECHAR = re.compile(
        r"^\s*((nao|opa|espera|desculpa|na verdade|entao|ah|ai)[,!.\s]+)*"
        r"((o|oi|ei)\s+)?(" + _ALT_NOMES + r")?[,!.]?\s*"
        r"((nao|opa|espera|desculpa|na verdade|entao)[,!.\s]+)*"
        r"(por favor[,\s]+)?(pode\s+|poderia\s+|quero que voce\s+|quero que\s+)?"
        r"(fecha|fechar|feche|encerra|encerrar|encerre|sai d[oa]|sair d[oa]|"
        r"desliga|desligar|mata|matar)\b", re.I)
    # Palavras que nao ajudam a identificar o programa e atrapalham a busca.
    _RECHEIO = {"o", "a", "os", "as", "um", "uma", "meu", "minha", "meus", "minhas",
                "de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "em",
                "pra", "para", "mim", "por", "favor", "ai", "agora", "ja", "esse",
                "essa", "este", "esta", "programa", "aplicativo", "app", "janela"}

    @classmethod
    def _programa_conhecido(cls, alvo: str) -> str:
        """O programa que ele quis dizer, tolerando o jeito de falar.

        "gerenciador tarefas" (sem o "de") e "no bloco de notas" (com a
        preposicao colada) sao a MESMA coisa que os nomes da lista — mas a
        busca por texto exato nao casava, e o pedido ia para o Codex custando
        6 a 12 s. Aqui a comparacao e por PALAVRAS, sem o recheio: quem pediu
        acerta escrevendo do jeito que fala.
        """
        palavras = {p for p in str(alvo or "").split() if p not in cls._RECHEIO}
        if not palavras:
            return ""
        for nome, programa in cls._PROGRAMAS_CONHECIDOS.items():
            chave = {p for p in nome.split() if p not in cls._RECHEIO}
            if chave and (palavras == chave or palavras <= chave or chave <= palavras):
                return programa
        return ""

    def _alvo_do_pedido(self, t: str, regex) -> str:
        """O que sobra da frase depois do verbo, sem o recheio das pontas."""
        alvo = regex.sub(" ", t).strip()
        palavras = [p for p in alvo.split()]
        while palavras and palavras[0] in self._RECHEIO:
            palavras.pop(0)
        while palavras and palavras[-1] in self._RECHEIO:
            palavras.pop()
        return " ".join(palavras)


    def _rede_de_fechar(self, texto: str) -> bool:
        """Fecha um programa conhecido na hora, sem passar pelo cerebro."""
        import unicodedata as _u  # noqa: PLC0415
        t = _u.normalize("NFD", str(texto or "").strip().lower())
        t = "".join(c for c in t if _u.category(c) != "Mn")
        t = " ".join("".join(c if c.isalnum() else " " for c in t).split())
        t = _tira_vocativo(t)
        if not t or len(t.split()) > 8 or not self._RE_FECHAR.search(t):
            return False
        alvo = self._alvo_do_pedido(t, self._RE_FECHAR)
        # PASTA SE FECHA PELA JANELA, NAO PELO PROCESSO (05/09/2026).
        #
        # "fecha minha pasta de download" nao casava com programa nenhum, ia
        # para o cerebro (5 s), e o cerebro chamava o `fechar_app`, que procura
        # PROCESSO. Janela de pasta e do `explorer.exe` — protegido, e com
        # razao. Resultado: quatro vezes "o pasta de download nao esta aberto"
        # com a pasta aberta na cara dele.
        achado_pasta = self._RE_PASTA.search(t)
        if achado_pasta:
            nome_pasta = achado_pasta.group(1).strip()
            try:
                from actions import janelas_windows as _jw  # noqa: PLC0415
                from actions.usar_computador import _resolver_pasta  # noqa: PLC0415
                caminho = ""
                try:
                    caminho = str(_resolver_pasta(nome_pasta) or "")
                except Exception:
                    caminho = ""
                fechadas, achadas = _jw.fechar_pasta(nome_pasta, caminho)
            except Exception as exc:
                _voice_diag("fechar_pasta_falhou", f"{nome_pasta}: {str(exc)[:80]}")
                return False
            _voice_diag("fechar_pasta", f"{nome_pasta}: {fechadas} de {achadas}")
            if fechadas:
                self._responder_curto(f"Fechei a pasta {nome_pasta}, chefe.")
            elif achadas:
                self._responder_curto(
                    f"A pasta {nome_pasta} esta aberta, mas nao quis fechar, chefe.")
            else:
                self._responder_curto(f"A pasta {nome_pasta} nao esta aberta, chefe.")
            return True
        programa = self._programa_conhecido(alvo)
        if not programa:
            return False
        # Nomes de sistema (ms-settings:, shell:) nao se fecham por nome.
        if ":" in programa:
            return False
        try:
            from actions.fechar_app import fechar_app  # noqa: PLC0415
            r = fechar_app({"app_name": programa})
        except Exception as exc:
            _voice_diag("fechar_local_falhou", f"{alvo}: {str(exc)[:60]}")
            return False
        _voice_diag("fechar_local", programa)
        # O NOME QUE ELE OUVE E O NOME QUE ELE DISSE. A ferramenta devolve
        # "Fechei o taskmgr (1 janela(s))" — o nome interno do Windows, que nao
        # significa nada para o dono (05/09/2026, ele reparou na hora). Quando
        # deu certo, a frase sai com a palavra dele; se falhou, o recado da
        # ferramenta vale, porque ali o detalhe importa.
        recado = str(r or "")
        deu_certo = "fechei" in recado.lower() or "fechado" in recado.lower()
        self._responder_curto(f"Fechei o {alvo}, chefe." if deu_certo
                              else (recado or f"Nao consegui fechar o {alvo}."))
        return True

    def _rede_de_abrir(self, texto: str) -> bool:
        """Abre pasta ou programa conhecido na hora, sem passar pelo cerebro."""
        import unicodedata as _u  # noqa: PLC0415
        t = _u.normalize("NFD", str(texto or "").strip().lower())
        t = "".join(c for c in t if _u.category(c) != "Mn")
        t = " ".join("".join(c if c.isalnum() else " " for c in t).split())
        # O NOME NO FIM DA FRASE ESTRAGAVA A BUSCA DA PASTA: "abre minha pasta
        # de download, Sulafat" fazia o nome entrar junto com o da pasta
        # ("download sulafat") e nao achava caminho nenhum.
        t = _tira_vocativo(t)
        # O TETO DE PALAVRAS SOBE QUANDO E PASTA (05/09/2026). "Nao, eu quero
        # que voce abra minha pasta de download" tem nove palavras e ia para o
        # cerebro so por isso. Com o nome de uma pasta DE VERDADE na frase, o
        # pedido nao e ambiguo: `_resolver_pasta` ainda tem de achar a pasta,
        # senao o atalho desiste do mesmo jeito.
        _teto = 14 if self._RE_PASTA.search(t) else 8
        if not t or len(t.split()) > _teto or not self._RE_ABRIR.search(t):
            return False
        # Tarefa encadeada ("abre o bloco de notas e digita...") vai ao cerebro:
        # aqui so mora o pedido de UMA coisa.
        # PEDIDO DE DOIS PASSOS NAO E SO "ABRIR" (lista ampliada em 01/10/2026):
        # "abre o chrome E ENTRA no youtube" abria o Chrome e a segunda metade
        # sumia — "entra", "vai", "acessa", "coloca", "procura" nao estavam aqui.
        if re.search(r"\b(e|entao|depois|ai)\s+(me\s+)?(digit|escrev|salv|fech|toc|"
                     r"pesquis|mand|cri|entr|acess|vai\b|va\b|coloc|procur|busc|"
                     r"abr|lig|jog|assist|mostr|faz|fac|le\b|copi|cola|apag|"
                     r"mov|renomei|envi|grav|tir|edit|desenh|organiz|baix|instal|"
                     r"atualiz|rod|execut|inici|clic|selecion|aument|diminu|"
                     r"mud|troc|pon|bot|deix)", t):
            return False

        achado = self._RE_PASTA.search(t)
        if achado:
            nome = achado.group(1).strip()
            try:
                from actions.usar_computador import _resolver_pasta  # noqa: PLC0415
                caminho = _resolver_pasta(nome)
            except Exception:
                caminho = None
            if not caminho:
                return False          # nao achei: deixa o cerebro tentar
            try:
                import os  # noqa: PLC0415
                os.startfile(caminho)  # noqa: S606
            except Exception as exc:
                _voice_diag("abrir_local_falhou", f"{nome}: {str(exc)[:60]}")
                return False
            # CONFERIR ANTES DE DIZER QUE ABRIU (05/09/2026, pedido do Diego:
            # "ele tem que verificar se realmente esta fechado ou aberto pra
            # nao ficar inventando"). O `startfile` nao devolve erro se a
            # janela nao vier — quem sabe se veio e a tela.
            apareceu = False
            try:
                from actions import janelas_windows as _jw  # noqa: PLC0415
                fim = time.time() + 1.2
                while time.time() < fim:
                    if _jw.pastas_abertas(nome, caminho):
                        apareceu = True
                        break
                    time.sleep(0.1)
            except Exception:
                apareceu = True          # sem como conferir, nao acusa falha
            _voice_diag("abrir_local", f"pasta {caminho} janela={apareceu}")
            self._responder_curto(f"Abri a pasta {nome}, chefe." if apareceu else
                                  f"Mandei abrir a pasta {nome}, mas a janela nao "
                                  f"apareceu aqui, chefe.")
            return True

        # Programa conhecido: "abre o bloco de notas", "abre NO bloco de
        # notas", "abre o gerenciador tarefas" — a busca e por palavras, nao
        # por texto exato (ver `_programa_conhecido`).
        alvo = self._alvo_do_pedido(t, self._RE_ABRIR)
        programa = self._programa_conhecido(alvo)
        if not programa:
            return False
        try:
            from actions.open_app import open_app  # noqa: PLC0415
            r = open_app({"app_name": programa})
        except Exception as exc:
            _voice_diag("abrir_local_falhou", f"{alvo}: {str(exc)[:60]}")
            return False
        _voice_diag("abrir_local", f"programa {programa}")
        if "não" in str(r).lower() or "failed" in str(r).lower():
            self._responder_curto(f"Nao consegui abrir o {alvo}.")
        else:
            self._responder_curto(f"Abri o {alvo}, chefe.")
        return True


    def _em_ingles_se_preciso(self, frase: str) -> str:
        """Cliente em ingles: frase pronta que saiu em portugues ("Nao consegui
        abrir o ...") e traduzida antes de falar (26/09/2026). No portugues, ou
        se a ponte estiver fora, volta como veio."""
        try:
            if not _em_ingles():
                return frase
            from core.idioma import parece_portugues  # noqa: PLC0415
            if not parece_portugues(frase):
                return frase
            from core.cerebro_codex import CerebroCodex  # noqa: PLC0415
            ponte = getattr(self.ui, "_codex_relay", None)
            if ponte is None:
                return frase
            cerebro = CerebroCodex(ponte=ponte)
            if not cerebro.disponivel():
                return frase
            return cerebro._traduzir_para_ingles(frase) or frase
        except Exception:
            return frase

    def _responder_curto(self, frase: str) -> None:
        """Fala uma frase pronta e escreve no chat.

        O CHAT NAO PODE DEPENDER DA VOZ. Antes a escrita ia pendurada no
        primeiro som: se a voz falhasse, o dono perguntava a hora e nao
        recebia NADA — nem som, nem texto. Foi o que aconteceu no teste do
        Diego com "Que horas e agora?". Agora o texto sai de qualquer jeito, e
        so uma vez.
        """
        frase = self._em_ingles_se_preciso(frase)
        escrito = {"ok": False}

        def _escreve():
            if not escrito["ok"]:
                escrito["ok"] = True
                self.ui.write_log(f"{_nome_falante(self.ui)}: {frase}")

        try:
            self._falar_local(frase, ao_primeiro_som=_escreve)
        except Exception as exc:
            _voice_diag("resposta_curta_sem_voz", str(exc)[:80])
        _escreve()

    # ── A HORA NAO PRECISA DO MODELO (03/09/2026) ──────────────────────────
    #
    # "Que horas sao" era a pergunta mais comum do cliente da TJ e ainda custava
    # ~1,9 s de rede so para o modelo transformar uma hora que o computador ja
    # tinha numa frase bonita. O Diego autorizou trocar: a frase agora sai de um
    # molde daqui, em ~0,001 s, e vai direto para a voz.
    #
    # O QUE SE PERDE, escrito para quem vier depois nao achar que foi descuido:
    # o modelo nao escreve mais essa resposta, entao ela nao improvisa e nao
    # emenda no assunto anterior. Os moldes abaixo carregam o jeito do JARVIS —
    # "chefe" e uma pitada de humor — mas sao seis, nao infinitos.
    #
    # A PORTA E ESTREITA DE PROPOSITO. So passa pergunta CURTA que e SO sobre
    # hora ou data. Tudo que tiver verbo de acao, ou mais de sete palavras, ou
    # um horario dito pelo dono ("anota pras tres horas") vai pelo caminho
    # normal — quem erra para o lado de mandar ao modelo perde 1,9 s; quem erra
    # para o lado de responder sozinho responde a pergunta errada.
    _RE_SO_RELOGIO = re.compile(
        # Perguntas que comecam com o interrogativo: "que horas sao", "qual dia
        # e hoje", "quantas horas sao".
        r"^(?:e\s+)?(?:ai\s+)?(?:me\s+(?:diz|fala|informa)\s+)?"
        r"(?:que|qual|quantas)\s+"
        r"(?:horas?|dia|data|mes|ano|dia\s+da\s+semana)\b"
        r"|^(?:que|qual)\s+dia\s+(?:e|eh)\s+hoje"
        r"|^hoje\s+e\s+(?:que\s+)?dia"
        r"|^(?:em\s+)?que\s+(?:mes|ano)\s+(?:estamos|e)"
        r"|^(?:as\s+)?horas?\s*$"
        r"|^(?:que\s+)?dia\s+(?:da\s+semana\s+)?(?:e|eh)\s+hoje"
        # As formas sem o "que" na frente, que a 1a versao deixou passar. Elas
        # sao ancoradas no COMECO e terminam a frase (`\s*$`) de proposito:
        # solto, "qual e a hora" casaria dentro de "qual e a hora que o Carlos
        # chega", que e pergunta para o modelo, nao para o relogio.
        r"|^(?:me\s+)?(?:diz|fala|informa|da)\s+(?:a|o)\s+(?:hora|horas|data)\s*$"
        r"|^qual\s+(?:e\s+|eh\s+)?(?:a|o)\s+(?:hora|horas|data)\s*$"
        r"|^qual\s+(?:e\s+|eh\s+)?o\s+dia\s+(?:de\s+hoje\s*)?$"
    )
    # Se ele DITOU um horario, nao esta perguntando: esta mandando anotar.
    _RE_HORARIO_DITADO = re.compile(r"\b\d{1,2}\s*(?:h|horas?|:\d{2})\b")

    def _rede_do_relogio(self, texto: str, _so_a_ultima: bool = False) -> bool:
        """Hora e data sem ir na rede. Devolve True quando ja respondeu.

        A PERGUNTA COSTUMA VIR NO FIM DA FALA (05/09/2026). Medido no uso do
        Diego: "Estou bem, gracas a Deus. Que horas e agora?" custou 7,6 s de
        rede — a regex e ancorada no COMECO da frase, e ali o comeco era a
        resposta dele a uma pergunta anterior, nao a pergunta dele.
        Entao, se a fala inteira nao casar, a ULTIMA frase e tentada sozinha.
        """
        import unicodedata as _u  # noqa: PLC0415
        bruto = str(texto or "").strip()
        if not _so_a_ultima and bruto:
            import re as _re  # noqa: PLC0415
            pedacos = [p.strip() for p in _re.split(r"(?<=[.!?])\s+", bruto) if p.strip()]
            if len(pedacos) > 1 and self._rede_do_relogio(pedacos[-1], True):
                return True
        t = _u.normalize("NFD", str(texto or "").strip().lower())
        t = "".join(ch for ch in t if _u.category(ch) != "Mn")
        t = " ".join("".join(ch if ch.isalnum() or ch == ":" else " "
                            for ch in t).split())
        # O CHAMADO NA FRENTE NAO E A PERGUNTA (03/09/2026).
        #
        # "O JARVIS, que horas sao?" foi direto para o modelo e custou 3,4 s: a
        # regex e ancorada no comeco e o vocativo estava na frente dela. O meu
        # teste tinha o nome no FIM ("que horas sao agora JARVIS"), que passava —
        # e por isso nao pegou.
        #
        # A lista e curta de proposito: so interjeicao de chamado e os nomes
        # dele. "fala" e "escuta" ficam DE FORA porque sao parte da pergunta em
        # "me fala a hora" — tirar elas quebraria o caso que ja funcionava.
        # Os nomes saem de `_NOMES_ASSISTENTE`, que vem do `core.personas`: a
        # lista escrita a mao aqui so tinha jarvis/friday/ted e deixava a
        # pergunta ir para o modelo quando a voz escolhida era outra.
        # As interjeicoes de aprovacao entraram em 05/09/2026: "Perfeito, e que
        # dia e hoje?" ia para o modelo por causa da primeira palavra. Todas
        # elas sao enfeite puro — nenhuma muda o que ele esta perguntando.
        _CHAMADOS = {"o", "oi", "ei", "ai", "opa", "olha", "psiu", "entao", "e",
                     "ted", "perfeito", "certo", "beleza", "otimo", "legal",
                     "show", "massa", "ok", "okay", "obrigado", "obrigada",
                     "valeu", "isso", "entendi", "ta", "tá"} | _NOMES_ASSISTENTE
        palavras_t = t.split()
        while palavras_t and palavras_t[0] in _CHAMADOS:
            palavras_t.pop(0)
        while palavras_t and palavras_t[-1] in _NOMES_ASSISTENTE:
            palavras_t.pop()
        t = " ".join(palavras_t)
        if not t or len(palavras_t) > 7:
            return False
        if self._RE_HORARIO_DITADO.search(t):
            return False
        # Verbo de acao na frase: e pedido, nao pergunta de relogio.
        if any(f" {v} " in f" {t} " or t.startswith(v + " ")
               for v in ("anota", "anote", "marca", "marque", "agenda",
                         "agende", "lembra", "lembre", "abre", "abra",
                         "manda", "mande", "escreve", "escreva", "cria",
                         "crie", "coloca", "coloque", "poe", "ponha")):
            return False
        if not self._RE_SO_RELOGIO.search(t):
            return False

        import random  # noqa: PLC0415
        from actions.relogio_falado import agora_falado  # noqa: PLC0415
        dados = agora_falado(texto)
        falar = dados.get("falar_assim") or ""
        if not falar:
            return False
        # MES E ANO TEM RESPOSTA PROPRIA. O `agora_falado` so separa "hora" de
        # "data", entao "em que mes estamos?" voltava a data inteira — e o
        # molde de data ainda montava "Estamos em quinta-feira, tres de
        # setembro...", que nao e portugues. A data falada termina sempre em
        # "... de <mes> de <ano>", entao o pedaco certo sai dali.
        palavras = t.split()
        data_falada = dados.get("data_falada") or falar
        partes = data_falada.rsplit(" de ", 2)
        if "ano" in palavras and len(partes) == 3:
            frase = f"Estamos em {partes[2]}."
        elif "mes" in palavras and len(partes) == 3:
            frase = random.choice([
                f"Estamos em {partes[1]}.",
                f"{partes[1].capitalize()}.",
            ])
        elif dados.get("pedido") == "data":
            frase = random.choice([
                f"Hoje e {falar}.",
                f"{falar.capitalize()}.",
            ])
        else:
            frase = random.choice([
                f"Sao {falar}.",
                f"{falar.capitalize()}.",
                f"Marcando {falar}.",
            ])
        _voice_diag("relogio_sem_rede", f"{texto[:40]!r} -> {frase[:50]!r}")
        # O historico recebe os dois lados: sem isso, o "e amanha?" seguinte
        # chegaria ao modelo sem saber do que se fala.
        try:
            if self._openrouter is not None:
                self._openrouter.historico.append({"role": "user", "content": texto})
                self._openrouter.historico.append({"role": "assistant", "content": frase})
        except Exception:
            pass
        self._falar_local(
            frase,
            ao_primeiro_som=lambda: self.ui.write_log(
                f"{_nome_falante(self.ui)}: {frase}"))
        return True

    def _redes_do_pc(self, texto: str) -> bool:
        """Decisoes que nao podem ficar na mao do modelo (01/09/2026).

        1. "Para" enquanto o olhar-e-clicar roda: freio de mao. Mandar isso ao
           cerebro seria pedir a ele que interprete, e um clique errado a mais
           pode ser um "comprar".
        2. Resposta a uma pergunta do olhar-e-clicar ("posso pagar?"): vai
           direto para quem perguntou, nao vira conversa nova.
        3. "Sim"/"nao" para uma exclusao pendente, no modo local: a rede que
           ja existia para a Gemini nao era alcancada aqui.
        4. "Pode desfazer" ate 15 min depois de organizar ou excluir: vai
           direto na ferramenta. O modelo, com o historico longo, as vezes
           respondia "claro!" e nao chamava nada.
        5. "Clica na pasta X": o modelo escolhia computer_control(click), que
           clica onde o mouse esta e diz "cliquei". Aqui vai para o
           olhar-e-clicar, que enxerga a tela.
        Devolve True quando assumiu a frase.
        """
        t = str(texto or "").strip()
        if not t:
            return False
        if self._usando_pc:
            if self._PARAR_PC.search(t):
                self._parar_pc = True
                self.ui.write_log("SIS: parando o que estava fazendo na tela.")
                _voice_diag("usar_pc_parar", t[:60])
                return True
            if self._pergunta_pendente:
                # Pergunta de SIM OU NAO: so aceita o que parece resposta. Com
                # o portao de volume afrouxado, a fala da televisao passa a
                # chegar ate aqui — e uma frase de novela tomada como resposta
                # cancelaria o passo ("nao autorizou"). Agora ela e ignorada e
                # ele CONTINUA esperando o dono.
                if self._pergunta_sim_nao and not (
                        self._RESPOSTA_SIM_NAO.search(t) or _parece_duvida(t)):
                    _voice_diag("resposta_ignorada", t[:60])
                    return True
                self._resposta_do_dono = t
                _voice_diag("usar_pc_resposta", t[:60])
                return True
            # CORRIGIR SEM PARAR: o dono esta vendo a tela e diz "esta errado, o
            # certo e F", "nao e esse, e o de baixo", "desce mais". A frase entra
            # na fila de correcoes; o laco a manda no proximo passo, e ela vale
            # MAIS que o pedido original. Frase que nao parece correcao continua
            # sendo conversa normal.
            if self._CORRIGIR_PC.search(t):
                self._correcoes_pc.append(t.strip())
                self.ui.write_log(f"SIS: correcao no meio do caminho: {t[:80]}")
                _voice_diag("computer_user_correcao", t[:80])
                threading.Thread(
                    target=lambda: self._falar_local("Anotado, chefe."), daemon=True).start()
                return True
        if self._pending_delete_args and _delete_confirmation_decision(t):
            decisao = _delete_confirmation_decision(t)
            args = dict(self._pending_delete_args)
            rotulo = self._pending_delete_label or "item"
            self._pending_delete_args = None
            self._pending_delete_label = ""
            _voice_diag("exclusao_local", f"{decisao} {rotulo[:40]}")

            def _exclui():
                if decisao == "cancel":
                    r = f"Beleza, cancelei a exclusao de {rotulo}."
                else:
                    args["confirmed"] = True
                    self.ui.write_log(f"[file] confirmacao recebida; excluindo: {rotulo}")
                    r = file_controller(parameters=args, player=self.ui)
                    if "lixeira" in str(r).lower():
                        r = str(r) + " Se quiser, e so pedir para desfazer."
                self._falar_local(str(r), ao_primeiro_som=lambda: self.ui.write_log(f"{_nome_falante(self.ui)}: {r}"))
            threading.Thread(target=_exclui, daemon=True).start()
            return True
        if self._rede_do_video(t):
            return True
        if self._DESFAZER.search(t) and ha_algo_recente():
            _voice_diag("desfazer_direto", t[:60])

            def _desfaz():
                r = desfazer_ultima()
                try:
                    self._falar_local(str(r), ao_primeiro_som=lambda: self.ui.write_log(f"{_nome_falante(self.ui)}: {r}"))
                except Exception:
                    self.ui.write_log(f"{_nome_falante(self.ui)}: {r}")
            threading.Thread(target=_desfaz, daemon=True).start()
            return True
        if (self._manda_digitar(t) and not self._usando_pc
                and not self._TAREFA_DE_VARIOS_PASSOS.search(t)):
            _voice_diag("digitar_direto", t[:60])
            threading.Thread(target=self._digitar_ao_vivo, args=(t,), daemon=True).start()
            return True
        if self._PARAR_COMPUTER_USE.search(t):
            # Vale mesmo com nada rodando: e o dono dizendo "nao mexa mais no
            # meu computador". Marca o freio, guarda a hora e NAO liga nada.
            self._parar_pc = True
            self._parou_pc_em = time.monotonic()
            self._esperando_tarefa_pc_ate = 0.0
            self._pc_liberado_ate = 0.0
            _voice_diag("computer_user_parar", t[:60])
            self.ui.write_log("SIS: parando o computer use — e nao vou comecar outro.")
            # SO FALA SE NAO HAVIA NADA RODANDO. Com o laco em andamento quem
            # avisa e ele proprio, ao terminar ("Parei, como voce pediu. O
            # computador e seu de novo") — falar aqui tambem fazia o assistente
            # dizer que parou DUAS vezes, uma por cima da outra.
            if not self._usando_pc:
                threading.Thread(
                    target=lambda: self._falar_local("Certo, parei. Nao encosto mais no computador."),
                    daemon=True).start()
            return True
        # FALAR SOBRE O ASSUNTO NAO E DAR A ORDEM (05/09/2026). Vale para as
        # tres portas abaixo — ativar, a janela de espera e o `_USAR_PC` —,
        # entao a conferencia vem ANTES das tres. Devolve False de proposito:
        # a frase segue o caminho normal e o cerebro RESPONDE a pergunta.
        if not self._usando_pc and (self._FALAR_SOBRE_PC.search(t)
                                    or self._ATIVAR_NEGADO.search(t)):
            if (self._ATIVAR_PC.search(t) or self._USAR_PC.search(t)
                    or time.monotonic() < self._esperando_tarefa_pc_ate):
                _voice_diag("computer_user_so_falando", t[:80])
                # Se ele estava esperando a tarefa, desiste da espera: quem
                # pergunta o que o JARVIS faz nao esta mandando fazer nada.
                self._esperando_tarefa_pc_ate = 0.0
            return False

        # ── "ATIVAR COMPUTADOR": a UNICA porta do computer use ─────────────
        #
        # O Diego fechou assim em 04/09/2026: o dono fala a palavra, o JARVIS
        # pergunta o que fazer, e a PROXIMA frase e a tarefa. Sem passar por
        # aqui, nada mexe no computador — nem a rede de fala, nem o modelo.
        if not self._usando_pc and self._ATIVAR_PC.search(t):
            # ACABOU DE MANDAR PARAR: nao religa por eco. O proprio aviso falado
            # ("nao encosto mais no computador") volta pelo microfone, e uma
            # repeticao nervosa do dono ("para, para!") tambem chega em dobro —
            # 12 s de carencia evitam o vaivem de desligar e ligar sozinho.
            if time.monotonic() - self._parou_pc_em < 12:
                _voice_diag("computer_user_ativar_ignorado", t[:60])
                return True
            # LINUX NO WAYLAND (26/09/2026): o sistema nao deixa mexer no mouse
            # nem ler o ESC. Diz na hora o que falta, em vez de "ativar" e
            # passar minutos tentando clicar sem conseguir.
            try:
                from core.plataforma import bloqueio_do_mouse  # noqa: PLC0415
                _recado_x11 = bloqueio_do_mouse()
            except Exception:
                _recado_x11 = ""
            if _recado_x11:
                _voice_diag("computer_user_sem_x11", t[:60])
                threading.Thread(target=self._responder_curto, args=(_recado_x11,),
                                 daemon=True).start()
                return True
            resto = self._ATIVAR_PC.sub(" ", t).strip(" ,.!?")
            self._pc_liberado_ate = time.monotonic() + 120
            self._parar_pc = False
            if len(resto.split()) >= 3:
                # "Ativar computador, abre a pasta de fotos": ja veio com a tarefa.
                _voice_diag("computer_user_ativar_com_tarefa", resto[:60])
                self._esperando_tarefa_pc_ate = 0.0
                if self._quer_desenhar(resto):
                    # "ativar computador, desenha uma casa" e desenho, nao
                    # olhar-e-clicar: um risco por volta de rede nao e desenho.
                    threading.Thread(target=self._desenhar_ao_vivo,
                                     args=(resto,), daemon=True).start()
                    return True
                self._olhar_thread = threading.Thread(
                    target=self._olhar_e_clicar, args=(resto,), daemon=True)
                self._olhar_thread.start()
                return True
            _voice_diag("computer_user_ativar", t[:60])
            self._ativar_computer_use()
            return True

        # A frase logo depois do "ativar computador" e a tarefa — MAS SO SE FOR
        # UMA TAREFA (07/09/2026).
        #
        # Defeito que o Diego viu: ele disse "ativar computador", o JARVIS
        # perguntou o que fazer, e o ouvido transcreveu um ruido como "Atma".
        # Uma palavra solta virou tarefa: o JARVIS minimizou a janela, avisou
        # "nao mexa no mouse", rodou o laco, perguntou "o que voce quer que eu
        # faca no JARVIS?" e desistiu 56 s depois. *"Eu nao tinha nem passado a
        # tarefa pra ele."*
        #
        # Agora, se o que chegar nao tiver cara de tarefa, ele PEDE DE NOVO e
        # continua esperando, em vez de sair mexendo na tela por causa de um
        # ruido. Nada de tomar a tela por engano.
        if not self._usando_pc and time.monotonic() < self._esperando_tarefa_pc_ate:
            if self._PARAR_PC.search(t) or self._PARAR_COMPUTER_USE.search(t):
                self._esperando_tarefa_pc_ate = 0.0
                self.ui.write_log("SIS: computer use desativado antes de comecar.")
                return True
            if not self._parece_tarefa(t):
                # ── O LACO SEM SAIDA DA 1.0.25 (corrigido em 08/09/2026) ────
                #
                # Queixa do cliente: *"ele fala 'senhor, o que quer fazer'; eu
                # mando o que eu quero e ele manda algo parecido de novo. Ja
                # tentei fechar e abrir e nao muda nada."*
                #
                # Este ramo pedia a tarefa de novo E RENOVAVA a espera por mais
                # 45 s, sem contar as tentativas. Entao qualquer frase que nao
                # tivesse cara de tarefa — uma pergunta, um "obrigado", um
                # "deixa pra la" — caia aqui, era engolida antes do cerebro
                # (`return True`) e ganhava a MESMA frase de volta, para sempre.
                # Nao havia como sair a nao ser dizendo "para", que o cliente
                # nao tinha por que saber. Fechar o programa tambem nao curava:
                # bastava a palavra de ativacao aparecer de novo.
                #
                # Agora a espera tem FIM. Ele pede a tarefa no maximo duas
                # vezes; na terceira desiste calado, larga a espera e devolve a
                # frase para o caminho normal (`return False`) — que e onde ela
                # sempre deveria ter ido. Uma pergunta vira resposta, e nao mais
                # um pedido de tarefa repetido.
                self._tentativas_tarefa_pc += 1
                if self._tentativas_tarefa_pc > 2:
                    _voice_diag("computer_user_desistiu_da_espera", t[:60])
                    self.ui.write_log(
                        "SIS: desisti de esperar a tarefa; o computador segue livre.")
                    self._esperando_tarefa_pc_ate = 0.0
                    self._tentativas_tarefa_pc = 0
                    return False
                _voice_diag("computer_user_tarefa_ininteligivel", t[:60])
                self.ui.write_log(f"SIS: nao entendi a tarefa ({t[:40]!r}); continuo esperando.")
                self._esperando_tarefa_pc_ate = time.monotonic() + 45
                _repete = ("Nao peguei a tarefa. Pode repetir o que o senhor quer "
                           "que eu faca no computador?")
                threading.Thread(
                    target=lambda: self._falar_local(
                        _repete,
                        ao_primeiro_som=lambda: self.ui.write_log(
                            f"{_nome_falante(self.ui)}: {_repete}")),
                    daemon=True).start()
                return True
            self._esperando_tarefa_pc_ate = 0.0
            self._tentativas_tarefa_pc = 0
            _voice_diag("computer_user_tarefa_apos_ativar", t[:60])
            self._pc_liberado_ate = time.monotonic() + 120
            self._parar_pc = False
            if self._quer_desenhar(t) or self._quer_melhorar_desenho(t):
                threading.Thread(target=self._desenhar_ao_vivo, args=(t,),
                                 kwargs={"melhorar": self._quer_melhorar_desenho(t)},
                                 daemon=True).start()
                return True
            self._olhar_thread = threading.Thread(target=self._olhar_e_clicar, args=(t,), daemon=True)
            self._olhar_thread.start()
            return True

        # ── DESENHAR TAMBEM DEPENDE DA PORTA (07/09/2026) ────────────────
        #
        # Regra do Diego, depois de ver o JARVIS desenhar um palhaco so porque
        # ele perguntou se sabia desenhar: *"ele so deve desenhar quando eu
        # ativar o computador... ele nao pode desenhar so por palavras"*. E
        # coerente: desenhar toma o mouse por mais de um minuto, igual ao
        # computer use. Sem a palavra, ele ENSINA a porta e nao encosta em nada.
        #
        # Fica DEPOIS do bloco de ativar de proposito: "ativar computador,
        # desenha uma casa" e a frase logo apos o "ativar" ja sao tratadas la em
        # cima, e caem no desenhista pelo mesmo caminho.
        if not self._usando_pc and (self._quer_desenhar(t)
                                    or self._quer_melhorar_desenho(t)):
            melhorar = self._quer_melhorar_desenho(t)
            if time.monotonic() > self._pc_liberado_ate:
                _voice_diag("desenhar_sem_ativar", t[:60])
                _recado = _frase_no_idioma("Para eu desenhar eu preciso mexer no seu computador. "
                                           "Diga primeiro: ativar computador.")
                # ESCRITO TAMBEM, nao so falado: sem isto a recusa nao aparecia
                # na conversa e o Diego so ouvia, sem entender de onde vinha.
                threading.Thread(
                    target=lambda: self._falar_local(
                        _recado,
                        ao_primeiro_som=lambda: self.ui.write_log(
                            f"{_nome_falante(self.ui)}: {_recado}")),
                    daemon=True).start()
                return True
            _voice_diag("melhorar_desenho" if melhorar else "desenhar_direto", t[:60])
            threading.Thread(target=self._desenhar_ao_vivo, args=(t,),
                             kwargs={"melhorar": melhorar}, daemon=True).start()
            return True

        # Pediu para mexer na tela sem ativar: ensina a porta, nao liga nada.
        if (self._USAR_PC.search(t) or self._CLICAR.search(t)) and not self._usando_pc:
            # PERGUNTAR SOBRE O COMPUTER USE NAO E MANDAR USAR (07/09/2026).
            # "A funcao do Computer User, o que e que voce pode fazer pra mim?"
            # caia aqui e levava a recusa da porta como resposta, tres vezes
            # seguidas, ate o Diego dizer "NAO quero que voce mexa". Pergunta
            # volta para o cerebro, que EXPLICA.
            if self._e_pergunta(t):
                _voice_diag("computer_user_so_pergunta", t[:70])
                return False
            # ── O PORTAO VOLTOU (08/09/2026) ────────────────────────────────
            #
            # A linha `if time.monotonic() > self._pc_liberado_ate:` sumiu na
            # 1.0.25 quando a checagem de pergunta entrou no lugar dela, e as
            # quatro linhas de baixo ficaram DEPOIS de um `return` — codigo
            # morto que nunca mais rodou. O efeito nao era so o recado sumir:
            # sem esta comparacao, "mexe no computador" ou "clica ali" ligavam
            # o olhar-e-clicar NA HORA, sem nunca ter passado por "ativar
            # computador". E a regra que o Diego fechou em 04/09: sem a
            # palavra, nada encosta no computador do dono.
            if time.monotonic() > self._pc_liberado_ate:
                _voice_diag("computer_user_sem_ativar", t[:60])
                _porta = _frase_no_idioma("Para eu mexer no computador, diga primeiro: ativar computador.")
                threading.Thread(
                    target=lambda: self._falar_local(
                        _porta,
                        ao_primeiro_som=lambda: self.ui.write_log(
                            f"{_nome_falante(self.ui)}: {_porta}")),
                    daemon=True).start()
                return True
            _voice_diag("computer_user_direto", t[:60])
            self._parar_pc = False
            self._olhar_thread = threading.Thread(target=self._olhar_e_clicar, args=(t,), daemon=True)
            self._olhar_thread.start()
            return True
        return False

    def _parece_tarefa(self, texto: str) -> bool:
        """O que ele falou depois de "ativar computador" e mesmo uma tarefa?

        Tres palavras ja bastam ("abre o chrome", "desenha uma casa"). Menos do
        que isso so passa se houver um verbo de tarefa de verdade — assim
        "minimiza" continua valendo e "Atma", que foi um ruido transcrito, nao.
        """
        t = str(texto or "").strip()
        # PERGUNTA NAO E TAREFA (08/09/2026). A regra de "tres palavras ja
        # bastam" mandava "que horas sao?" para o olhar-e-clicar — o JARVIS
        # tomava a tela para responder uma pergunta que ele sabe de cabeca.
        # O `_e_pergunta` so acusa quando NAO ha verbo de tarefa na frase,
        # entao "abre a pasta de fotos pra mim?" continua sendo tarefa.
        if self._e_pergunta(t):
            return False
        if len(t.split()) >= 3:
            return True
        return bool(self._TEM_TAREFA_NA_TELA.search(t) or self._DESENHAR.search(t))

    # ── EDITAR VIDEO SEM PASSAR PELO CEREBRO (10/09/2026) ────────────────────
    #
    # Com um video carregado na janela e um pedido CLARO de edicao, o programa
    # chama a ferramenta direto — a mesma garantia que a foto ja tinha. Medido
    # no teste com o cerebro de verdade: numa rodada ele respondeu "o que voce
    # quer fazer com o arquivo?" em vez de chamar a ferramenta; noutra chamou e
    # disse "o video ficou pronto" quando a edicao so tinha COMECADO. Aqui a
    # frase dita e a da propria ferramenta, com o plano e o tempo certos. O
    # cerebro continua escrevendo a RECEITA, que e o que ele faz bem.
    #
    # "volume", "musica" e "texto" soltos NAO entram: com um video aberto o
    # dono ainda pode querer o volume do PC ou uma musica tocando.
    _VIDEO_PEDIDO = re.compile(
        r"\b(tik ?tok|reels?|shorts?|kwai|stor(y|ies)|vertical|horizontal|"
        r"9 ?(x|por|:) ?16|16 ?(x|por|:) ?9|quadrad[oa]|formato|sil[eê]ncios?|pausas?|"
        r"legendas?|espelh\w*|invert\w*|gira|girar|de cabe[cç]a pra baixo|acelera\w*|"
        r"c[aâ]mera lenta|velocidade|corta|corte|cortar|recorta\w*|edita|editar|edi[cç][aã]o|"
        r"m[uú]sica de fundo|trilha sonora|tira o som|sem som|efeitos?|preto e branco|"
        r"vintage|s[eé]pia|zoom|filtros?|transi[cç][aã]o|logo|marca d.?[aá]gua|junta|juntar|"
        r"comprim\w*|mais leve|deixa leve|t[ií]tulo|trem(e|er|endo))\b", re.I)
    _VIDEO_OUTRO_ALVO = re.compile(
        r"\b(youtube|site|pasta|planilha|e-?mail|navegador|chrome|foto|imagem|bloco de notas|"
        r"word|documento|lembrete|alarme|clima|not[ií]cia|leads?|whats ?app|mensagem)\b", re.I)
    _VIDEO_STATUS = re.compile(
        r"\b(terminou|terminar|ficou pronto|t[aá] pronto|est[aá] pronto|j[aá] (foi|acabou|ficou)|"
        r"quanto falta|andamento|como (est[aá]|t[aá]) (o|a) (v[ií]deo|edi[cç][aã]o))\b", re.I)
    _VIDEO_PARAR = re.compile(
        r"\b(para|pare|parar|cancela|cancelar|interrompe|interromper|desiste)\b"
        r".{0,25}\b(edi[cç][aã]o|v[ií]deo|editar)", re.I)
    _EXT_DE_VIDEO = (".mp4", ".mov", ".mkv", ".avi", ".wmv", ".webm", ".m4v", ".3gp")
    _EXT_DE_IMAGEM = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
    _POR_NO_VIDEO = re.compile(
        r"\b(coloc\w*|coloqu\w*|p[oõ]e|ponha|por|bot\w*|adicion\w*|inser\w*|insir\w*|"
        r"aplic\w*|usa|usar|use|junt\w*|mete|joga|inclu\w*|monta\w*)\b", re.I)
    # ORDEM, mesmo com "tá?" e "correto?" no meio (10/09/2026). O Diego mandou
    # um pedido de 36 s — "eu vou querer que voce edite esse video e adicione
    # essas duas imagens, ta? ... correto?" — e o ponto de interrogacao do
    # "ta?" fez a rede tratar tudo como pergunta.
    _ORDEM_DE_VIDEO = re.compile(
        r"\b(quero|queria|vou querer|preciso|edite|edita|coloque|coloca|adicione|adiciona|"
        r"corte|corta|deixe|deixa|fa[cç]a|faz|bote|bota|ponha|p[oõ]e|salve|salva|insira|"
        r"pode (cortar|colocar|adicionar|deixar|por|p[oô]r|botar|editar|salvar|tirar|fazer))\b",
        re.I)
    _FALA_DE_IMAGEM = re.compile(
        r"\b(imagem|imagens|foto|fotos|figura|figuras|logo|logos|banner|banners)\b", re.I)

    def _rede_do_video(self, t: str) -> bool:
        """Pedido de video que nao pode ficar na mao do modelo. True = assumiu."""
        s = str(t or "").strip()
        if not s:
            return False
        # O aviso de arquivo carregado passa por aqui antes de ir ao cerebro:
        # so anota a hora e o caminho, para a rede saber que o video e RECENTE
        # — e para lembrar dele quando o dono arrastar uma IMAGEM depois (a
        # janela guarda um arquivo so, e a imagem tomava o lugar do video).
        if s.startswith("[FILE_UPLOADED]"):
            # Um arquivo ("path=") ou varios soltos de uma vez ("extra=", o
            # resto do mesmo arraste). Caminho do Windows nao tem "|".
            caminhos = [x.strip() for x in re.findall(r"(?:path|extra)=([^|]+?)\s*(?=\||$)", s)]
            agora = time.monotonic()
            recentes = list(getattr(self, "_arquivos_recentes", []) or [])
            video_deste = ""
            for c in caminhos:
                if not c:
                    continue
                recentes.append((agora, c))
                if not video_deste and c.lower().endswith(self._EXT_DE_VIDEO):
                    video_deste = c
            if video_deste:
                self._video_carregado_em = agora
                self._ultimo_video = video_deste
            self._arquivos_recentes = recentes[-12:]
            # QUEM RESPONDE A CHEGADA DO VIDEO E O PROGRAMA (10/09/2026). O
            # Diego disse "vou te mandar um video pra editar", arrastou o video
            # e as duas imagens e ouviu "manda o video pra eu editar": o
            # cerebro leu o aviso entre colchetes como NOTA INTERNA e respondeu
            # a fala anterior. Video (ou imagem com um video recente) e
            # confirmado aqui, sem o modelo; o resto continua com ele.
            imgs = [c for c in caminhos if c.lower().endswith(self._EXT_DE_IMAGEM)]
            ultimo = str(getattr(self, "_ultimo_video", "") or "")
            video_recente = bool(ultimo and Path(ultimo).is_file()
                                 and agora - (getattr(self, "_video_carregado_em", None) or -1e9) < 3600)
            if video_deste or (imgs and video_recente):
                self._anotar_chegada([c for c in caminhos if c.lower().endswith(
                    self._EXT_DE_VIDEO + self._EXT_DE_IMAGEM)])
                return True
            return False
        try:
            from actions import editar_video as _ev  # noqa: PLC0415
        except Exception:
            return False
        # A ultima fala do dono, com hora: a confirmacao da chegada do video
        # usa para saber se ele ja tinha dito que queria EDITAR.
        self._fala_antes_do_arquivo = (time.monotonic(), s)
        rodando = _ev._em_andamento()
        fala_video = bool(re.search(r"v[ií]deo", s, re.I))
        acao = ""
        extra: dict = {}
        if rodando and self._VIDEO_PARAR.search(s):
            acao = "cancelar"
        elif ((rodando or fala_video) and self._VIDEO_STATUS.search(s)
              and not self._VIDEO_PEDIDO.search(s) and not re.search(r"youtube", s, re.I)):
            acao = "status"
        else:
            atual = str(getattr(self.ui, "current_file", "") or "")
            ultimo = str(getattr(self, "_ultimo_video", "") or "")
            desde = time.monotonic() - getattr(self, "_video_carregado_em", -1e9)
            n_palavras = len(s.split())
            if n_palavras > 250:
                return False
            # O que a frase pede de concreto. "escreve", "texto", "imagem" e
            # "foto" so contam com a palavra VIDEO junto: sem ela, "escreve um
            # texto" e outra coisa.
            especificos = [m.group(0).lower() for m in self._VIDEO_PEDIDO.finditer(s)]
            especificos = [m for m in especificos if not re.fullmatch(r"edita|editar|edi[cç][aã]o", m)]
            # E so com VERBO de fazer: "eu te mandei um video e duas imagens,
            # correto?" fala de imagem e de video e nao pede nada.
            if fala_video and (self._POR_NO_VIDEO.search(s) or self._ORDEM_DE_VIDEO.search(s)
                               or re.search(r"\bescrev", s, re.I)):
                especificos += re.findall(r"\b(?:escrev\w*|texto|frase|imagem|imagens|foto|fotos|figura)\b",
                                          s, re.I)
            imagens = self._imagens_da_edicao()
            if atual.lower().endswith(self._EXT_DE_VIDEO):
                if not (fala_video or desde < 1200):
                    return False
                if not (especificos or self._VIDEO_PEDIDO.search(s)):
                    return False
                if self._VIDEO_OUTRO_ALVO.search(s) and not fala_video:
                    return False
            elif (atual.lower().endswith(self._EXT_DE_IMAGEM) and ultimo and fala_video
                  and desde < 3600 and Path(ultimo).is_file()
                  and (self._POR_NO_VIDEO.search(s) or len(set(especificos)) >= 2)):
                # VIDEO ARRASTADO, DEPOIS A(S) IMAGEM(NS): "coloca essa imagem no
                # video". A janela so guarda um arquivo; o video vem da memoria.
                extra = {"video": ultimo, "imagem_arrastada": atual}
                especificos.append("imagem")
            else:
                # AINDA NAO HA VIDEO: "vou te mandar um video, corta os
                # silencios e deixa no formato do TikTok". O pedido fica
                # guardado e comeca sozinho quando o video chegar — sem isso
                # ele ouviria "recebi, o que voce quer?" do que ja tinha dito.
                ordem = bool(self._ORDEM_DE_VIDEO.search(s) or self._POR_NO_VIDEO.search(s))
                if fala_video and especificos and (ordem or len(set(especificos)) >= 2) \
                        and n_palavras <= 250:
                    self._pedido_video_pendente = (time.monotonic(), s)
                return False
            # FRASE LONGA so com assunto de video de verdade: a partir de 60
            # palavras e ditado ou conversa, a nao ser que fale do video e
            # peca pelo menos duas coisas dele (o pedido de 36 s do Diego).
            if n_palavras > 60 and not (fala_video and len(set(especificos)) >= 2):
                return False
            if imagens and (fala_video or self._FALA_DE_IMAGEM.search(s)):
                extra["imagens_arrastadas"] = imagens
            ordem = bool(self._ORDEM_DE_VIDEO.search(s)) or len(set(especificos)) >= 2
            if self._e_pergunta(s) and not ordem:
                return False
            # PERGUNTA DE CAPACIDADE NAO E PEDIDO: "voce consegue editar
            # video?" se responde. Mas "da pra por LEGENDA?" tem assunto — e a
            # regra do desenho, "ordem tem assunto" — e ai ele faz.
            if not especificos and re.search(
                    r"\b(voc[eê]|vc)\s+(consegue|sabe|pode)\b|\bd[aá] pra\b|\btem como\b|"
                    r"\b[eé] poss[ií]vel\b|\?", s, re.I):
                return False
            acao = "editar"
        _voice_diag("video_direto", f"{acao} <- {s[:70]!r}")

        def _faz():
            r = self._executar_ferramenta_local(
                "editar_video", {"acao": acao, "pedido": s, "_fala_do_dono": s, **extra})
            # O cerebro nao viu este turno (a rede passou por fora dele). Sem
            # isto, na fala seguinte ele achava o pedido PENDENTE: o Diego disse
            # "ficou top" com o video pronto e ouviu "vou cortar os silencios
            # agora" (10/09/2026).
            self._contar_ao_cerebro(s, str(r))
            try:
                self._falar_local(str(r), ao_primeiro_som=lambda: self.ui.write_log(
                    f"{_nome_falante(self.ui)}: {r}"))
            except Exception:
                self.ui.write_log(f"{_nome_falante(self.ui)}: {r}")
        threading.Thread(target=_faz, daemon=True).start()
        return True

    def _contar_ao_cerebro(self, fala: str, resposta: str) -> None:
        """Poe na CONVERSA do cerebro um turno que aconteceu por fora dele.

        As redes respondem sem o modelo; o diario ate registra, mas o modelo le
        a conversa antes da nota — e com a conversa parada no pedido, ele
        entende que o pedido ainda esta por fazer.
        """
        try:
            cer = self._openrouter
            if cer is None or not str(resposta or "").strip():
                return
            from core.cerebro_openrouter import MEMORIA_MAX  # noqa: PLC0415
            cer.historico.append({"role": "user", "content": str(fala or "")})
            cer.historico.append({"role": "assistant", "content": str(resposta)})
            del cer.historico[:-MEMORIA_MAX]
        except Exception:
            pass

    def _falar_do_video(self, frase: str) -> None:
        """A voz do editor quando o trabalho em segundo plano ACABA.

        Vai para o chat (antes so a voz saia; na tela ficava so a linha "SIS:
        video editado salvo em") e para a conversa do cerebro, marcada como
        aviso do sistema — e o que faz "ficou top" virar agradecimento, e nao
        "vou cortar os silencios agora".
        """
        self._contar_ao_cerebro(
            "[aviso do sistema, nao e fala do dono: a edicao de video que rodava em "
            "segundo plano ACABOU agora]", frase)
        try:
            self._falar_local(frase, ao_primeiro_som=lambda: self.ui.write_log(
                f"{_nome_falante(self.ui)}: {frase}"))
        except Exception:
            self.ui.write_log(f"{_nome_falante(self.ui)}: {frase}")

    # ── A CHEGADA DO VIDEO (10/09/2026) ─────────────────────────────────────
    #
    # Quem arrasta um arquivo por vez manda tres avisos em poucos segundos.
    # Responder a cada um seria "recebi o video", "recebi a imagem", "recebi a
    # imagem" — tres falas atropeladas. Os avisos se juntam ate ficar
    # `_ESPERA_DO_ARRASTE` segundos sem chegar nada, e sai UMA fala so.
    _ESPERA_DO_ARRASTE = 3.0
    _TRAVA_DO_LOTE = threading.Lock()
    _QUER_EDITAR = re.compile(
        r"edi[cç][aã]o|edit|cort|formato|legenda|tik ?tok|reels|shorts|sil[eê]ncio|efeito|"
        r"m[uú]sica|montag", re.I)

    def _anotar_chegada(self, caminhos: list) -> None:
        with self._TRAVA_DO_LOTE:
            lote = getattr(self, "_lote_de_arquivos", None)
            if lote is None:
                lote = self._lote_de_arquivos = {"arquivos": [], "ultimo": 0.0, "fio": None}
            lote["arquivos"].extend(caminhos)
            lote["ultimo"] = time.monotonic()
            if lote["fio"] is None:
                lote["inicio"] = lote["ultimo"]
                lote["fio"] = threading.Thread(target=self._responder_chegada, daemon=True)
                lote["fio"].start()

    @staticmethod
    def _quantos(n: int, feminino: bool) -> str:
        nomes = {2: ("dois", "duas"), 3: ("três", "três"), 4: ("quatro", "quatro"),
                 5: ("cinco", "cinco"), 6: ("seis", "seis")}
        return nomes.get(n, (str(n), str(n)))[1 if feminino else 0]

    def _frase_de_chegada(self, vids: list, imgs: list) -> str:
        import random  # noqa: PLC0415  (nao esta importado no topo deste arquivo)
        partes = []
        if vids:
            partes.append("o vídeo" if len(vids) == 1 else f"os {self._quantos(len(vids), False)} vídeos")
        if imgs:
            partes.append("a imagem" if len(imgs) == 1 else f"as {self._quantos(len(imgs), True)} imagens")
        if not vids and time.monotonic() - (getattr(self, "_ultimo_aviso_de_chegada", None) or -1e9) < 120:
            abre = ("Chegou mais uma imagem." if len(imgs) == 1
                    else f"Chegaram mais {self._quantos(len(imgs), True)} imagens.")
        else:
            abre = random.choice(("Recebi", "Peguei aqui")) + " " + " e ".join(partes) + "."
        t, antes = getattr(self, "_fala_antes_do_arquivo", None) or (-1e9, "")
        quer_editar = time.monotonic() - t < 300 and bool(self._QUER_EDITAR.search(antes))
        if quer_editar or imgs:
            return abre + " Me fala como você quer a edição."
        return abre + (" O que você quer que eu faça com ele?" if len(vids) == 1
                       else " O que você quer que eu faça com eles?")

    def _responder_chegada(self) -> None:
        lote = self._lote_de_arquivos
        while True:
            time.sleep(0.2)
            with self._TRAVA_DO_LOTE:
                if time.monotonic() - lote["ultimo"] >= self._ESPERA_DO_ARRASTE:
                    arquivos = list(dict.fromkeys(lote["arquivos"]))
                    inicio = lote.get("inicio", 0.0)
                    lote["arquivos"] = []
                    lote["fio"] = None
                    break
        vids = [c for c in arquivos if c.lower().endswith(self._EXT_DE_VIDEO)]
        imgs = [c for c in arquivos if c.lower().endswith(self._EXT_DE_IMAGEM)]
        if not (vids or imgs):
            return
        nomes = ", ".join(Path(c).name for c in arquivos)
        try:
            from memory.diario import anotar as _anotar  # noqa: PLC0415
            _anotar("arquivo", f"o dono arrastou para a sua janela: {nomes}")
        except Exception:
            pass
        aviso = (f"[o dono arrastou para a sua janela: {nomes} — os arquivos JA ESTAO com voce, "
                 f"nao peca para mandar de novo]")
        # O PEDIDO QUE ESTAVA ESPERANDO O VIDEO comeca agora.
        pend = getattr(self, "_pedido_video_pendente", None)
        ultimo = str(getattr(self, "_ultimo_video", "") or "")
        falta_imagem = False
        if pend and time.monotonic() - pend[0] < 300 and (vids or (ultimo and Path(ultimo).is_file())):
            imagens = self._imagens_da_edicao()
            # Pediu imagem e ela ainda nao chegou: espera a proxima leva.
            falta_imagem = not imagens and bool(self._FALA_DE_IMAGEM.search(pend[1]))
            if not falta_imagem:
                self._pedido_video_pendente = None
                _voice_diag("video_pendente", f"comecou com o video que chegou <- {pend[1][:60]!r}")
                args = {"acao": "editar", "pedido": pend[1], "_fala_do_dono": pend[1],
                        "video": vids[0] if vids else ultimo}
                if imagens:
                    args["imagens_arrastadas"] = imagens
                r = str(self._executar_ferramenta_local("editar_video", args))
                self._contar_ao_cerebro(aviso + " " + pend[1], r)
                try:
                    self._falar_local(r, ao_primeiro_som=lambda: self.ui.write_log(
                        f"{_nome_falante(self.ui)}: {r}"))
                except Exception:
                    self.ui.write_log(f"{_nome_falante(self.ui)}: {r}")
                return
        # ELE JA FALOU depois de arrastar (pediu a edicao antes destes 3 s):
        # perguntar "como voce quer?" agora seria falar por cima do pedido.
        # O cerebro ainda fica sabendo dos arquivos.
        if (getattr(self, "_fala_antes_do_arquivo", None) or (-1e9, ""))[0] > inicio:
            self._contar_ao_cerebro(aviso, "Arquivos recebidos.")
            return
        frase = self._frase_de_chegada(vids, imgs)
        if falta_imagem:
            frase = frase.split(".")[0] + ". Agora arrasta a imagem, que eu já começo o que você pediu."
        self._ultimo_aviso_de_chegada = time.monotonic()
        _voice_diag("arquivo_chegou", f"{len(vids)} video(s), {len(imgs)} imagem(ns) -> {frase[:60]!r}")
        self._contar_ao_cerebro(aviso, frase)
        try:
            self._falar_local(frase, ao_primeiro_som=lambda: self.ui.write_log(
                f"{_nome_falante(self.ui)}: {frase}"))
        except Exception:
            self.ui.write_log(f"{_nome_falante(self.ui)}: {frase}")

    def _imagens_da_edicao(self) -> list:
        """As imagens arrastadas para ESTA edicao, na ordem em que chegaram.

        Contam as que vieram depois do ultimo video (ou ate 10 min antes dele:
        tem quem arraste as imagens primeiro), e no maximo de 1 h atras. Uma
        logo de uma edicao antiga nao entra na nova.
        """
        agora = time.monotonic()
        t_video = getattr(self, "_video_carregado_em", None) or -1e9
        saida: list = []
        for t, c in list(getattr(self, "_arquivos_recentes", []) or []):
            if not str(c).lower().endswith(self._EXT_DE_IMAGEM):
                continue
            if agora - t > 3600 or t < t_video - 600:
                continue
            if c not in saida and Path(c).is_file():
                saida.append(c)
        return saida[-6:]

    def _e_pergunta(self, t: str) -> bool:
        """A frase PERGUNTA alguma coisa, em vez de mandar fazer?

        ORDEM TEM TAREFA. Palavra de pergunta sem verbo de tarefa e conversa —
        e conversa se responde, nao se executa nem se recusa.
        """
        if not self._PALAVRA_DE_PERGUNTA.search(t):
            return False
        return not self._TEM_TAREFA_NA_TELA.search(t)

    def _e_pergunta_de_desenho(self, t: str) -> bool:
        """"Voce sabe desenhar o que?" e pergunta; "desenha uma casa" e ordem.

        A conta e uma so: ORDEM TEM ASSUNTO. Se a frase diz O QUE desenhar, e
        pedido; se ela so fala em desenhar com palavra de pergunta em volta, e
        conversa e quem responde e o cerebro.
        """
        if self._COMECA_DESENHANDO.search(t):
            return False                     # "me desenha o que voce quiser"
        if self._e_pergunta(t):
            return True                      # "como voce desenha uma casa?"
        if self._ASSUNTO_DO_DESENHO.search(t):
            return False                     # disse o que quer: e pedido
        return bool(self._PERGUNTA_DESENHO.search(t)
                    or self._PODE_DESENHAR.search(t))

    def _quer_desenhar(self, texto: str) -> bool:
        """A frase e uma ORDEM de desenhar? (nao uma pergunta sobre desenhar)"""
        t = str(texto or "")
        if self._FALAR_SOBRE_PC.search(t) or self._ATIVAR_NEGADO.search(t):
            return False
        if self._e_pergunta_de_desenho(t):
            return False
        if self._DESENHO_NEGADO.search(t):
            return False
        cita_editor = bool(re.search(r"\b(paint|photoshop|gimp|krita)\b", t, re.I))
        # Frase que COMECA mandando desenhar ja e ordem por si so, mesmo sem
        # assunto nomeado ("me desenha o que voce quiser").
        if not (self._COMECA_DESENHANDO.search(t)
                or self._ASSUNTO_DO_DESENHO.search(t)
                or (self._DESENHAR.search(t) and cita_editor)):
            return False
        # "abre o paint E desenha uma casa" continua sendo minha: quem desenha
        # ja abre o Paint sozinho. Qualquer OUTRO verbo encadeado (salva, manda,
        # pesquisa) devolve a frase para o cerebro, que sabe encadear.
        sem_abrir = self._SO_ABRIR.sub(" ", t)
        return not self._TAREFA_DE_VARIOS_PASSOS.search(sem_abrir)

    def _quer_melhorar_desenho(self, texto: str) -> bool:
        """"Melhora essa casa" logo depois de eu ter desenhado uma casa."""
        t = str(texto or "")
        if self._FALAR_SOBRE_PC.search(t) or not self._MELHORAR_DESENHO.search(t):
            return False
        try:
            from actions.desenhar_na_tela import ultimo_desenho  # noqa: PLC0415
        except Exception:
            return False
        if not ultimo_desenho():
            return False
        # "melhora o desenho E manda no whatsapp" continua sendo do cerebro.
        return not self._TAREFA_DE_VARIOS_PASSOS.search(self._SO_ABRIR.sub(" ", t))

    def _olhar_o_desenho(self, imagem: bytes, pergunta: str) -> str:
        """Mostra a folha pronta ao Codex da VPS e traz o veredito dele.

        Ideia do Diego (07/09/2026): *"nao acha melhor os desenhos passarem
        pelo Codex CLI na VPS? Ele analisa cada imagem e corrige e melhora"*.
        Ele esta certo: ate aqui a critica olhava o PLANO, uma lista de
        numeros; o Codex olha o DESENHO, que e o que o dono ve. Custa ~5 s e so
        manda refazer quando reprova.

        `cru=True` porque quem le a resposta e um programa esperando JSON, nao
        uma pessoa — sem isso volta uma frase educada e o json.loads quebra.
        """
        try:
            if self._openrouter is None:
                return ""
            self._openrouter.ponte = getattr(self.ui, "_codex_relay", None)
            cerebro = self._openrouter._cerebro_codex(a_parte=True)
            if cerebro is None:
                return ""
            return cerebro.ver(imagem, pergunta, cru=True)
        except Exception as exc:
            self.ui.write_log(f"[desenhar] o Codex nao olhou: {str(exc)[:80]}")
            return ""

    def _desenhar_ao_vivo(self, pedido: str, melhorar: bool = False) -> None:
        """Monta o plano do desenho e desenha, fora do modelo."""
        self._parar_pc = False
        self._usando_pc = True
        try:
            # SEM AVISO AQUI: quem abre falando e o narrador, la dentro, e
            # so DEPOIS do plano pronto — assim ele ja diz O QUE vai desenhar.
            # Falar antes fazia duas frases sairem uma por cima da outra, a
            # mesma pedra do `_olhar_e_clicar` (03/09).
            if self._openrouter is None:
                from core.cerebro_openrouter import CerebroOpenRouter  # noqa: PLC0415
                self._openrouter = CerebroOpenRouter()
            self._openrouter.ponte = getattr(self.ui, "_codex_relay", None)
            self._openrouter.falar = self._falar_local
            argumentos = ({"melhorar": True, "critica": pedido} if melhorar
                          else {"pedido": pedido})
            r = desenhar_na_tela(argumentos, player=self.ui,
                                 gerar=self._openrouter.escrever_em_camadas,
                                 falar=self._falar_local,
                                 olhar=self._olhar_o_desenho,
                                 deve_parar=lambda: self._parar_pc)
        except Exception as exc:
            r = f"Nao consegui desenhar: {str(exc)[:80]}"
        finally:
            self._usando_pc = False
            # A PORTA FICA ABERTA MAIS 2 MINUTOS depois do desenho. "Ficou
            # ruim, melhora essa casa" vem logo em seguida e e a continuacao do
            # mesmo pedido — mandar ele dizer "ativar computador" de novo ali
            # seria burocracia, nao seguranca.
            self._pc_liberado_ate = time.monotonic() + 120
        try:
            self._falar_local(str(r), ao_primeiro_som=lambda: self.ui.write_log(f"{_nome_falante(self.ui)}: {r}"))
        except Exception:
            self.ui.write_log(f"{_nome_falante(self.ui)}: {r}")

    def _digitar_ao_vivo(self, pedido: str) -> None:
        """Gera o conteudo e digita na janela aberta, fora do modelo."""
        self._parar_pc = False
        self._usando_pc = True
        try:
            try:
                self._falar_local("Deixa comigo, vou digitar ai.")
            except Exception:
                pass
            if self._openrouter is None:
                from core.cerebro_openrouter import CerebroOpenRouter  # noqa: PLC0415
                self._openrouter = CerebroOpenRouter()
            self._openrouter.ponte = getattr(self.ui, "_codex_relay", None)
            self._openrouter.falar = self._falar_local
            r = digitar_na_tela({"pedido": pedido}, player=self.ui,
                                gerar=self._openrouter.escrever_texto_em_camadas,
                                deve_parar=lambda: self._parar_pc)
        except Exception as exc:
            r = f"Nao consegui digitar: {str(exc)[:80]}"
        finally:
            self._usando_pc = False
        try:
            self._falar_local(str(r), ao_primeiro_som=lambda: self.ui.write_log(f"{_nome_falante(self.ui)}: {r}"))
        except Exception:
            self.ui.write_log(f"{_nome_falante(self.ui)}: {r}")

    def _ativar_computer_use(self, pelo_botao: bool = False) -> None:
        """Liga o computer use e PERGUNTA o que fazer. A proxima frase (falada
        ou digitada) e a tarefa. Mesma porta do "ativar computador" de voz."""
        self._pc_liberado_ate = time.monotonic() + 120
        self._parar_pc = False
        self._esperando_tarefa_pc_ate = time.monotonic() + 45
        self._tentativas_tarefa_pc = 0
        self.ui.write_log("SIS: computer use ATIVADO — esperando a tarefa"
                          + (" (fale ou digite)." if pelo_botao else "."))
        _pedido = _frase_no_idioma("Certo, chefe. O que o senhor quer que eu faca no computador?")
        # O AVISO DE ABERTURA E GRAVADO ENQUANTO ELE DITA A TAREFA. Ver
        # `_preparar_aviso_do_computador` — e o que faz o "nao mexa no
        # mouse" sair inteiro, em vez de picotado pela rede.
        self._reservar_aviso_do_computador()

        def _perguntar_e_preparar():
            try:
                self._falar_local(
                    _pedido,
                    ao_primeiro_som=lambda: self.ui.write_log(
                        f"{_nome_falante(self.ui)}: {_pedido}"))
            finally:
                self._preparar_aviso_do_computador()

        threading.Thread(target=_perguntar_e_preparar, daemon=True).start()

    # ── BOTAO "COMPUTER USE" DO DOCK (08/10/2026) ──────────────────────────
    #
    # Pedido do Diego: "adicione um botao ativar computador, nome vai ser
    # computer use, quando clica ele pergunta o que fazer". O clique faz o
    # mesmo que dizer "ativar computador"; clicado de novo, desiste da espera;
    # com a tarefa rodando, para na hora (igual ao ESC).
    def _estado_computer_use(self) -> str:
        if self._usando_pc:
            return "trabalhando"
        if time.monotonic() < self._esperando_tarefa_pc_ate:
            return "esperando"
        return "livre"

    def _botao_computer_use(self) -> None:
        estado = self._estado_computer_use()
        _voice_diag("computer_user_botao", estado)
        if estado == "trabalhando":
            self._esc_no_computador(origem="botao")
            return
        if estado == "esperando":
            self._esperando_tarefa_pc_ate = 0.0
            self._pc_liberado_ate = 0.0
            self.ui.write_log("SIS: computer use desligado antes de comecar.")
            return
        self._ativar_computer_use(pelo_botao=True)

    # A FRASE DO "PAREI" (08/10/2026). Sai do disco, inteira e na hora — e
    # gravada uma vez por voz junto com o aviso de abertura. Ver
    # `_preparar_aviso_do_computador` e `core/fala_gravada.py`.
    _PAREI_PC = "Parei, senhor. O computador é seu de novo."

    def _esc_no_computador(self, origem: str = "esc") -> None:
        """ESC apertado (ou o botao) com o computer use rodando.

        Chamado da thread do freio, NO INSTANTE da tecla: manda o laco parar,
        corta a fala que estiver tocando (o aviso de 10 s seguia ate o fim) e
        ja diz que parou — sem esperar o laco terminar o passo dele.
        """
        if getattr(self, "_esc_avisado", False):
            return
        self._esc_avisado = True
        self._parar_pc = True
        self._parou_pc_em = time.monotonic()
        self._esperando_tarefa_pc_ate = 0.0
        self._pc_liberado_ate = 0.0
        _voice_diag("computer_user_esc", origem)
        try:
            self.ui.write_log("SIS: " + ("ESC" if origem == "esc" else "botao")
                              + " — computer use parado.")
        except Exception:
            pass
        try:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._drain_audio_queue)
        except Exception:
            pass
        self._fala_inteira_ate = 0.0
        _frase = _frase_no_idioma(self._PAREI_PC)

        def _dizer():
            try:
                if not self._falar_inteiro(
                        _frase, guardar=True,
                        ao_primeiro_som=lambda: self.ui.write_log(
                            f"{_nome_falante(self.ui)}: {_frase}")):
                    self._falar_local(_frase)
            except Exception:
                try:
                    self.ui.write_log(f"{_nome_falante(self.ui)}: {_frase}")
                except Exception:
                    pass

        threading.Thread(target=_dizer, daemon=True).start()

    def _olhar_e_clicar(self, tarefa: str) -> None:
        """Roda o olhar-e-clicar fora do modelo, com os mesmos freios."""
        self._parar_pc = False
        self._esc_avisado = False
        self._usando_pc = True
        try:
            # Sem aviso aqui: o `narrador_pc`, la dentro, ja abre falando o
            # aviso completo (o que vai fazer, que nao e para mexer no mouse e
            # como parar). Falar antes fazia as duas frases sairem coladas, uma
            # por cima da outra — foi o que aconteceu no Bob em 03/09.
            r = usar_computador({"tarefa": tarefa}, player=self.ui,
                                falar=self._falar_no_computador,
                                falar_aviso=self._avisar_do_computador,
                                falando=self._esta_falando,
                                perguntar=self._perguntar_ao_dono,
                                deve_parar=lambda: self._parar_pc,
                                correcoes=self._pegar_correcoes,
                                ao_esc=self._esc_no_computador)
        except Exception as exc:
            r = f"Nao consegui fazer isso na tela: {str(exc)[:80]}"
        finally:
            self._usando_pc = False
            self._pergunta_pendente = False
        # O ESC JA AVISOU NA HORA (08/10/2026): falar o "parei" de novo aqui
        # era a segunda frase, atrasada, que o Diego ouvia como demora.
        if getattr(self, "_esc_avisado", False):
            self._esc_avisado = False
            return
        try:
            self._falar_local(str(r), ao_primeiro_som=lambda: self.ui.write_log(f"{_nome_falante(self.ui)}: {r}"))
        except Exception:
            self.ui.write_log(f"{_nome_falante(self.ui)}: {r}")

    def _pegar_correcoes(self) -> list:
        """Entrega ao laco o que o dono corrigiu desde o passo anterior.

        Esvazia a fila ao entregar: correcao e para ser obedecida uma vez, nao
        para ficar repetindo em todo passo.
        """
        if not self._correcoes_pc:
            return []
        novas, self._correcoes_pc = self._correcoes_pc, []
        return novas

    def _perguntar_ao_dono(self, pergunta: str, espera_s: float = 25.0,
                           so_sim_ou_nao: bool = False):
        """Fala a pergunta e espera a resposta do dono (voz ou texto).

        Chamado de dentro do laco `usar_computador`, que roda numa thread.
        Devolve o texto respondido, ou None se ele ficou calado.
        """
        self._resposta_do_dono = None
        self._pergunta_sim_nao = bool(so_sim_ou_nao)
        self._pergunta_pendente = True
        try:
            try:
                self._falar_local(pergunta, ao_primeiro_som=lambda: self.ui.write_log(f"{_nome_falante(self.ui)}: {pergunta}"))
            except Exception:
                self.ui.write_log(f"{_nome_falante(self.ui)}: {pergunta}")
            limite = time.monotonic() + float(espera_s)
            while time.monotonic() < limite:
                if self._resposta_do_dono is not None or self._parar_pc:
                    break
                time.sleep(0.2)
            return self._resposta_do_dono
        finally:
            self._pergunta_pendente = False
            self._pergunta_sim_nao = False

    # Quanto tempo o microfone continua fechado DEPOIS que o JARVIS termina de
    # falar. Não é capricho: a caixa de som guarda som já entregue (medido:
    # 0,243 s de reserva) e a sala ainda devolve o eco. Se o microfone abrir
    # nesse instante, ele capta a própria voz do JARVIS, manda para o servidor,
    # e o servidor entende "o cliente está falando" e CANCELA a resposta —
    # foi o que o registro mostrou, 3 cancelamentos em 6 segundos.
    # Expira sozinho pelo relógio: não é uma trava que pode ficar presa.
    # ERA 0,45 s e nao bastava no modo local: a caixa de som ainda estava
    # devolvendo o fim da fala quando o microfone reabria, e o whisper
    # transcrevia a propria voz do JARVIS como se fosse o dono ("Tudinho", que
    # o Diego nunca disse). Um segundo cobre o eco da sala sem comer o comeco
    # da resposta dele — a pessoa leva mais que isso para reagir.
    # 1,0 s era demais: o Diego emendava a fala logo depois de o JARVIS calar e
    # a frase inteira caia nesse buraco — ele repetia e so entao era ouvido.
    # 0,35 s cobre o rabo do som na caixa; o resto do eco ja e barrado pelo piso
    # de voz (750) e pela trava `_is_speaking` durante a fala.
    # 0,35 -> 0,20 (05/09/2026): a caixa reserva 0,20 s e a fila ja esta vazia
    # quando o `set_speaking(False)` roda, entao o que sobrava aqui era so eco
    # de sala — e custava a primeira silaba do dono.
    _GUARDA_POS_FALA_S = 0.20

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if not value:
            self._mic_fechado_ate = time.monotonic() + self._GUARDA_POS_FALA_S
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def interrupt(self) -> None:
        """Stop JARVIS mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[JARVIS] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        # Aqui foi o CLIENTE que mandou calar a boca: abrir o microfone na hora
        # é justamente o que ele quer. A guarda anti-eco não vale neste caminho,
        # senão comeríamos o começo da frase dele.
        self._mic_fechado_ate = 0.0
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        clean = _frase_no_idioma(re.sub(r"\s+", " ", str(text or "")).strip())
        if not clean:
            return
        # GATILHO DE TESTE DA CONVERSA PELO CODEX (30/08/2026, ideia do Diego).
        #
        # Digitar "/codex <pergunta>" na caixa do JARVIS manda a pergunta para o
        # Codex na VPS em vez da Gemini, e escreve a resposta de volta no chat
        # com o tempo que levou. Serve para MEDIR se vale a pena trocar o
        # cerebro: a Gemini responde em 1 a 4 s quando esta boa, e um trabalho no
        # Codex levou 85 a 92 s hoje. Sem numero, e discussao.
        #
        # De proposito NAO e ferramenta: nao entra nas 28 declaracoes, nao pesa
        # ficha nenhuma por resposta e nao muda o caminho da voz.
        now = time.monotonic()
        # Workers e tratamento de erro podem convergir para a mesma fala. Não
        # envia a mesma solicitação duas vezes em uma janela curta.
        if _transcript_key(clean) == self._last_speak_text and now - self._last_speak_at < 4.0:
            self.ui.write_log("SYS: fala duplicada suprimida.")
            return
        self._last_speak_text = _transcript_key(clean)
        self._last_speak_at = now
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": clean}]},
                turn_complete=True
            ),
            self._loop
        )

    # ── Voz e jeito de ser, escolhidos pelo cliente na engrenagem ───────────
    _ARQ_PERSONA = "config/persona.json"

    JANELA_ACORDADO_S = 30.0

    def _mudou_so_com_nome(self, ligado: bool) -> None:
        """O cartao foi clicado: aplica agora e guarda para as proximas vezes."""
        self._so_com_nome = bool(ligado)
        # Ligar NAO acorda: se acordasse, o dono clicava e continuava sendo
        # ouvido por 30 s sem entender por que.
        self._acordado_ate = 0.0
        # Sem gravar: a escolha vale so ate fechar o programa.
        _voice_diag("so_com_nome", "ligado" if self._so_com_nome else "desligado")

    def _nomes_que_acordam(self) -> list[str]:
        """Como o dono pode chamar: o nome da voz de agora, e sempre "jarvis".

        "Vale para todas as vozes" foi o pedido — mas com a voz Lia escolhida o
        dono continua dizendo "Jarvis" por costume. Entao os dois valem: o nome
        proprio da voz e o nome da casa.
        """
        nomes = ["jarvis"]
        try:
            from core.personas import nome_da_ia  # noqa: PLC0415
            atual = nome_da_ia(
                (getattr(self.ui, "persona_atual", None) or {}).get("personagem", ""))
            if atual:
                nomes.append(str(atual).lower())
        except Exception:
            pass
        return nomes

    @staticmethod
    def _limpo_para_nome(texto: str) -> str:
        import unicodedata as _u  # noqa: PLC0415
        s = _u.normalize("NFKD", str(texto or "").lower())
        s = "".join(c for c in s if not _u.combining(c))
        return " ".join("".join(c if c.isalnum() else " " for c in s).split())

    def _passa_pelo_nome(self, texto: str):
        """Deixa passar? Devolve (passa, texto_sem_o_nome).

        Tres caminhos:
          desligado ............ passa tudo, como sempre foi
          janela aberta ........ passa tudo e RENOVA os 30 s
          janela fechada ....... so passa se o nome estiver na frase

        O nome e TIRADO do texto quando ele abre a conversa: "Jarvis, que horas
        sao" tem de chegar no cerebro como "que horas sao". Sem isso o modelo
        gasta a resposta cumprimentando de volta.

        A comparacao e por PALAVRA INTEIRA, sem acento. O whisper escreve
        "Jarvis", "Járvis", "JARVIS," e as vezes "Jarves" — as tres primeiras o
        `_limpo_para_nome` resolve; a quarta cai na semelhanca do
        `_corrigir_nomes`, que ja roda antes daqui.
        """
        import time as _t  # noqa: PLC0415
        if not self._so_com_nome:
            return True, texto
        agora = _t.monotonic()
        if agora < self._acordado_ate:
            self._acordar()
            return True, texto

        palavras = self._limpo_para_nome(texto).split()
        nomes = self._nomes_que_acordam()
        onde = next((i for i, w in enumerate(palavras) if w in nomes), -1)
        if onde < 0:
            return False, texto

        # O QUE VEIO ANTES DO NOME NAO EXISTE (02/09/2026).
        #
        # A primeira versao so cortava o nome quando ele estava nas tres
        # primeiras palavras; no resto dos casos mandava a frase INTEIRA. Mas o
        # whisper entrega o trecho todo de uma vez, e no teste do Diego a frase
        # foi um paragrafo inteiro terminando em "...por exemplo, o JARVIS." O
        # nome estava la, o portao abriu, e o paragrafo — que era ele
        # EXPLICANDO a funcao, nao pedindo nada — foi para o cerebro. Resultado:
        # o JARVIS disparou o `usar_computador` e comecou a clicar na tela.
        #
        # A regra que ele pediu e literal: "ele so pode responder a partir do
        # momento que eu chamar Jarvis". Entao corta na PRIMEIRA ocorrencia do
        # nome e joga fora tudo que veio antes, sempre.
        #
        # O preco: com o portao fechado, "manda um zap falando do jarvis" fica
        # sem nada depois do nome e vira chamado. Sai barato — dentro dos 30 s
        # a frase passa inteira e intacta, que e onde as conversas de verdade
        # acontecem.
        resto = " ".join(palavras[onde + 1:]).strip()
        self._acordar()
        if resto:
            return True, resto

        # SO O NOME: e chamado, nao pedido. Responde na hora com uma frase
        # curta e NAO acorda o cerebro — uma volta na rede para dizer "pois
        # nao" custaria 3 s e o dono ja estaria falando por cima.
        self._atender_chamado()
        return False, ""

    def _atender_chamado(self) -> None:
        """A resposta de quem foi chamado pelo nome e ainda nao ouviu o pedido."""
        import random  # noqa: PLC0415
        frase = _frase_no_idioma(random.choice(
            ["Pois nao, chefe.", "Estou aqui.", "Pode falar, chefe.",
             "Diga, chefe.", "As ordens."]))
        _voice_diag("atendeu_chamado", frase)
        try:
            self._falar_local(
                frase, ao_primeiro_som=lambda: self.ui.write_log(f"{_nome_falante(self.ui)}: {frase}"))
        except Exception as exc:
            _voice_diag("chamado_sem_voz", str(exc)[:100])

    def _acordar(self) -> None:
        import time as _t  # noqa: PLC0415
        self._acordado_ate = _t.monotonic() + self.JANELA_ACORDADO_S
        try:
            self.ui.acordado_por(self.JANELA_ACORDADO_S)
        except Exception:
            pass

    def _carregar_persona(self) -> dict:
        """Le a escolha salva. Volta ao padrao se o arquivo nao existir."""
        from core.personas import PERSONAGEM_PADRAO, PERSONALIDADE_PADRAO
        padrao = {"personagem": PERSONAGEM_PADRAO,
                  "personalidade": PERSONALIDADE_PADRAO,
                  "microfone": "nao-definido",
                  "saida": "nao-definido",
                  # Precisa estar AQUI para ser lido do arquivo: o `update`
                  # abaixo so aceita chaves que ja existem no padrao.
                  "so_com_nome": False,
                  # A saudacao de abertura. NASCE LIGADA: e ela que abre a
                  # conversa desde 05/09/2026, no lugar dos recados tecnicos.
                  "saudacao": True}
        try:
            caminho = Path(__file__).resolve().parent / self._ARQ_PERSONA
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            padrao.update({k: v for k, v in dados.items() if k in padrao})
        except Exception:
            pass
        return padrao

    def _salvar_persona(self, escolha: dict) -> None:
        try:
            caminho = Path(__file__).resolve().parent / self._ARQ_PERSONA
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(json.dumps(escolha, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        except Exception as exc:
            _voice_diag("persona_nao_salvou", str(exc)[:120])

    def _trocar_persona(self, tipo: str, chave: str) -> None:
        """Chamado pelo painel. Aplica na hora e guarda para as proximas vezes."""
        from core.personas import PERSONAGENS, PERSONALIDADES
        escolha = dict(getattr(self.ui, "persona_atual", None) or self._carregar_persona())
        amostra = ""
        try:
            if tipo == "personagem":
                if chave not in PERSONAGENS:
                    return
                from core.voz_local import VozLocal
                if self._voz_local is None:
                    self._voz_local = VozLocal()
                self._voz_local.voz = chave
                escolha["personagem"] = chave
                if self._openrouter is not None:
                    # Trocar a voz troca o nome dela: a IA passa a se chamar
                    # Nova, Lia, Onyx... e a responder por ele.
                    self._openrouter.personagem = chave
                    self._openrouter.definir_personalidade(
                        escolha.get("personalidade", "equilibrado"))
                nome = PERSONAGENS[chave]["nome"]
                self.ui.write_log(f"VOZ: agora e {nome}.")
                # A amostra fica para o FIM, depois de gravar a escolha. Ver o
                # comentario la embaixo: ela ja fez o cliente perder a troca.
                amostra = f"Pronto, chefe. Agora eu falo assim, com a voz {nome}."
            elif tipo == "personalidade":
                if chave not in PERSONALIDADES:
                    return
                from core.cerebro_openrouter import CerebroOpenRouter
                if self._openrouter is None:
                    self._openrouter = CerebroOpenRouter()
                    self._openrouter.executor = self._executar_ferramenta_local
                    self._openrouter.ferramentas = CerebroOpenRouter.converter_ferramentas(
                        TOOL_DECLARATIONS)
                self._openrouter.ponte = getattr(self.ui, "_codex_relay", None)
                self._openrouter.falar = self._falar_local
                self._openrouter.definir_personalidade(chave)
                escolha["personalidade"] = chave
                nome = PERSONALIDADES[chave]["nome"]
                self.ui.write_log(f"JEITO: agora sou {nome}.")
            elif tipo == "saida":
                from core.audio_dispositivos import tocar_teste
                escolha["saida"] = chave
                self._saida_escolhida = chave
                # Derruba o fluxo atual para o `_play_audio` reabrir no novo.
                self._trocar_saida = True
                r = tocar_teste(chave)
                if r["ok"]:
                    self.ui.write_log(
                        "SOM: toquei um bip nesse aparelho. Ouviu? Entao e esse. "
                        "Se nao ouviu, tente o proximo da lista.")
                else:
                    self.ui.write_log(f"SOM: esse aparelho recusou ({r['erro'][:60]}).")
            elif tipo == "microfone":
                from core.audio_dispositivos import testar as testar_mic
                r = testar_mic(chave)
                escolha["microfone"] = chave
                self._mic_escolhido = chave
                # Forca o `_listen_audio` a reabrir com o aparelho novo: zerar o
                # carimbo faz o vigia agir no proximo giro do laco.
                self._mic_ultimo_bloco = 0.0
                if r["ok"]:
                    self.ui.write_log(
                        f"MICROFONE: trocado — {r['blocos']} blocos recebidos, funcionando.")
                else:
                    self.ui.write_log(
                        f"MICROFONE: esse nao respondeu ({r['erro'][:60]}). "
                        "Escolha outro ou feche programas que usam o microfone.")
            elif tipo == "saudacao":
                # O painel manda True/False. Vale so para a PROXIMA abertura —
                # dizer isso e melhor do que deixar o dono achar que nao pegou.
                ligada = bool(chave)
                escolha["saudacao"] = ligada
                self.ui.write_log(
                    "SAUDACAO: ligada — ele te cumprimenta ao abrir." if ligada
                    else "SAUDACAO: desligada — ele abre calado.")
            else:
                return
        except Exception as exc:
            self.ui.write_log(f"ERR: nao consegui trocar — {type(exc).__name__}: {str(exc)[:110]}")
            return
        self.ui.persona_atual = escolha
        self._salvar_persona(escolha)
        _voice_diag("persona", f"{tipo}={chave}")

        # A AMOSTRA E O ULTIMO PASSO, E FALHA SOZINHA (01/09/2026).
        #
        # Ela ficava no meio da troca. Quando o laco de audio estava fechado, o
        # `RuntimeError: Event loop is closed` subia, o `except` la em cima
        # engolia tudo com um `return`, e a escolha do cliente NAO era gravada:
        # ele trocava para a Lia, ouvia um erro e, ao reabrir, continuava na voz
        # velha. Escolher e o pedido; ouvir a demonstracao e cortesia.
        if amostra:
            try:
                self._falar_local(amostra)
            except Exception as exc:
                _voice_diag("amostra_da_voz_falhou", f"{type(exc).__name__}: {str(exc)[:90]}")
                self.ui.write_log(
                    "VOZ: troquei, mas nao consegui tocar a amostra agora. "
                    "Fale comigo que voce ja ouve a voz nova.")

    # Ferramentas que demoram e precisam de aviso falado antes, senao o cliente
    # acha que travou. Medidas hoje: imagem e leads passam de um minuto.
    _AVISOS_DEMORADAS = {
        # PESQUISA AVISA ANTES (05/09/2026, pedido do Diego). Medido no uso dele:
        # "me da as principais noticias do dia" ficou 12,6 s em silencio e "qual
        # o proximo jogo do Santos" ficou **37,3 s** — porque a busca sai da
        # maquina e volta com o resultado, e ate voltar ninguem fala nada. O
        # dono nao sabe se pensou, travou ou nao entendeu.
        #
        # A frase e sorteada em `_aviso_de_pesquisa`: aviso sempre igual, dito
        # varias vezes por dia, vira tique e o ouvido para de escutar.
        "web_search":    "",     # o texto sai do `_aviso_de_pesquisa`
        # Os tempos aqui sao os mesmos do `_TEMPOS_DO_TRABALHO`, e o do
        # `codex_remote` e trocado em tempo de execucao pelo `kind` (imagem ou
        # site) — ver `_aviso_falado_de_demora`.
        "codex_remote":  "Beleza, chefe. Ja estou trabalhando nisso, leva de um a dois minutos.",
        "editar_imagem": "Certo, estou editando a imagem. Leva de um a dois minutos.",
        # Curto de proposito: logo depois vem a frase da propria ferramenta,
        # com o plano e o tempo ("vou cortar os silencios... leva 2 minutos").
        "editar_video":  "Certo, chefe. Vou preparar a edicao do video.",
        "buscar_leads":  "Pode deixar, estou procurando as empresas. Leva de tres a cinco minutos.",
        "dev_agent":     "Beleza, ja comecei. Isso pode levar alguns minutos.",
        "screen_process": "Deixa eu dar uma olhada.",
        # MEDIDO: 104 segundos para marcar a tela (01/09/2026). O Diego pediu a
        # marcacao, viu "Trabalhando na sua imagem..." no chat e ficou quase dois
        # minutos ouvindo silencio, sem saber se tinha travado. Aviso falado
        # resolve — e ele sai ANTES de o pedido ir para a VPS.
        # Serve para os dois caminhos: o portao acima pode transformar este
        # pedido num simples olhar. Prometer marcacao aqui seria mentira
        # metade das vezes.
        "marcar_na_tela": "Deixa eu dar uma olhada na sua tela.",
        # `usar_computador` NAO entra aqui de proposito (03/09/2026): quem abre
        # a boca agora e o `narrador_pc`, dentro da propria ferramenta, com o
        # aviso completo ("nao mexa no mouse nem no teclado, pode demorar
        # alguns minutos, fale 'para' para me interromper"). Anunciar aqui
        # tambem faria o JARVIS falar duas frases coladas, uma por cima da outra.
    }

    # O que ele diz ANTES de sair pesquisando. Sorteado para nao virar tique —
    # e o mesmo motivo da saudacao ter tres frases por faixa de horario.
    _AVISOS_DE_PESQUISA = (
        "Deixa comigo, vou procurar isso agora.",
        "Ja vou buscar, um instante.",
        "Pesquisando aqui, chefe. Ja te falo.",
        "Vou dar uma olhada na internet, so um momento.",
        "Certo, procurando agora mesmo.",
    )

    def _aviso_falado_de_demora(self, args: dict) -> str:
        """A mesma conta do `_aviso_de_demora`, dita em voz alta.

        Existe separado porque o texto do chat pode ser mais seco; o que ele
        FALA precisa soar como alguem avisando, nao como etiqueta de sistema.
        """
        tipo = str((args or {}).get("kind") or "").lower().strip()
        tempo = _TEMPOS_DO_TRABALHO.get(tipo)
        if not tempo:
            return ""
        if tipo == "site":
            return (f"Beleza, chefe. Vou montar o site no servidor — "
                    f"isso leva {tempo}. Te aviso quando ficar pronto.")
        if tipo == "leads":
            return (f"Pode deixar, ja estou procurando as empresas. "
                    f"Leva {tempo}.")
        return (f"Beleza, chefe. Ja estou criando a imagem — "
                f"leva {tempo}. Te aviso quando ficar pronta.")

    def _aviso_de_pesquisa(self, args: dict) -> str:
        """Avisa que vai pesquisar, dizendo O QUE quando cabe na frase.

        Dizer o assunto e melhor do que um aviso generico: e a prova de que ele
        entendeu o pedido. Mas so quando o termo e curto — repetir uma frase
        inteira em voz alta atrasa mais do que informa.
        """
        import random as _r  # noqa: PLC0415
        frase = _r.choice(self._AVISOS_DE_PESQUISA)
        alvo = str((args or {}).get("query") or (args or {}).get("q") or "").strip()
        if 0 < len(alvo) <= 40:
            return f"{frase[:-1]}: {alvo}."
        return frase

    def _executar_ferramenta_local(self, nome: str, args: dict) -> str:
        """Executa QUALQUER uma das 28 ferramentas, pelo caminho que ja existia.

        Antes eu tinha reimplementado 8 a mao aqui, e o resto o modelo nao tinha
        — entao ele MENTIA que tinha feito ("bloco de notas aberto" sem abrir
        nada). Agora chama o mesmo `_execute_tool` do caminho da Gemini, que e
        onde as 28 sempre estiveram.

        Roda numa thread, e `_execute_tool` e uma corrotina do laco de eventos:
        por isso o agendamento com `run_coroutine_threadsafe`.
        """
        import time as _t
        args = args or {}
        _voice_diag("ferr_local_inicio", f"{nome} {str(args)[:80]}")
        t0 = _t.monotonic()

        aviso = self._AVISOS_DEMORADAS.get(nome)
        if nome == "web_search":
            aviso = self._aviso_de_pesquisa(args)
        elif nome == "codex_remote":
            aviso = self._aviso_falado_de_demora(args) or aviso
        elif nome == "editar_video" and str(args.get("acao") or "").lower().strip() in (
                "status", "andamento", "cancelar", "parar"):
            aviso = ""      # "ja terminou?" e "para" respondem na hora
        if aviso:
            try:
                self._falar_local(aviso)
            except Exception:
                pass

        class _Chamada:
            """Imita o objeto que a Gemini entrega ao `_execute_tool`."""
            def __init__(self, nome, args):
                self.id = None
                self.name = nome
                self.args = args

        try:
            if not self._loop:
                return "O motor ainda nao subiu, tente de novo em instantes."
            futuro = asyncio.run_coroutine_threadsafe(
                self._execute_tool(_Chamada(nome, args)), self._loop)
            # Teto generoso: leads e imagem passam de um minuto de verdade.
            resposta = futuro.result(timeout=900)
            bruto = getattr(resposta, "response", None) or {}
            if isinstance(bruto, dict):
                # `_execute_tool` devolve ora {"result": ...}, ora um dicionario
                # com varios campos (hora_agora, por exemplo). Achatar ajuda o
                # modelo a ler sem inventar.
                if "result" in bruto and len(bruto) == 1:
                    saida = bruto["result"]
                else:
                    saida = "; ".join(f"{k}: {v}" for k, v in bruto.items())
            else:
                saida = bruto
        except Exception as exc:
            saida = f"A ferramenta falhou: {type(exc).__name__}: {str(exc)[:200]}"

        _voice_diag("ferr_local_fim", f"{nome} {_t.monotonic()-t0:.1f}s")
        # O DIARIO: "VOCE FEZ ISTO". Sem esta linha ele nao tem onde olhar
        # depois — e foi o que aconteceu em 05/09/2026: escreveu no bloco de
        # notas, salvou, e dez minutos depois respondeu "nao tenho como saber,
        # voce que digitou". Ver `memory/diario.py`.
        try:
            from memory.diario import anotar as _anotar  # noqa: PLC0415
            _anotar("fez", f"{nome} {str(args)[:160]}", str(saida)[:160])
        except Exception:
            pass
        return str(saida)[:6000]

    def _aquecer_local(self) -> None:
        """Carrega whisper e Kokoro — mas a SAUDACAO nao espera por isso."""
        try:
            # ── A SAUDACAO SAI PRIMEIRO (05/09/2026, pedido do Diego) ──────
            #
            # Medido antes: **12,3 s** do clique ate ele falar. Do log, 9,0 s
            # eram o whisper e o Kokoro carregando — e a saudacao NAO usa
            # nenhum dos dois, porque quem fala e a Gemini. Ele ficava nove
            # segundos calado esperando coisa que a frase dele nem precisa.
            #
            # Agora: le a escolha do cliente (um JSON, milissegundos), dispara
            # a saudacao numa thread e SO ENTAO carrega o resto. Vai em thread
            # para o carregamento pesado correr JUNTO com ele falando, em vez
            # de um esperar o outro.
            from core.voz_local import VozLocal
            if self._voz_local is None:
                self._voz_local = VozLocal()
            escolha = self._carregar_persona()
            self._voz_local.voz = escolha["personagem"]
            self._mic_escolhido = escolha.get("microfone", "nao-definido")
            self._saida_escolhida = escolha.get("saida", "nao-definido")
            self.ui.persona_atual = escolha
            # A CONEXAO DA VOZ COMECA A SUBIR ANTES DA SAUDACAO (05/09/2026).
            #
            # Nao bloqueia — so agenda. Vem antes da thread da saudacao porque
            # a saudacao e a PRIMEIRA fala e paga o aperto de mao sozinha (6,1 s
            # medidos no log de hoje); e porque aqui ja abre tambem a segunda
            # conexao, a da corrida, que e a rede contra a sessao travada.
            self._aquecer_boca()
            threading.Thread(target=self._saudar_na_abertura, daemon=True,
                             name="saudacao").start()

            if self._whisper is None:
                self._whisper, self._whisper_na_placa = _abrir_whisper()
            # O RETRATO DESTE PC NO REGISTRO (01/10/2026): cada cliente tem um
            # processador, uma placa e um microfone; sem esta linha, diagnosticar
            # "ele nao me entende" no PC de alguem comecava por adivinhar.
            try:
                import os as _os_pc  # noqa: PLC0415
                import platform as _plat  # noqa: PLC0415
                from core.perfil_audio import perfil as _perfil_audio  # noqa: PLC0415
                _voice_diag(
                    "perfil_do_pc",
                    f"{_plat.processor()[:40] or _plat.machine()} | "
                    f"{_os_pc.cpu_count()} nucleos | ouvido "
                    + ("large-v3 na placa de video" if getattr(self, "_whisper_na_placa", False)
                       else "small no processador + segunda opiniao da Gemini")
                    + f" | microfone: {_perfil_audio().resumo()}")
            except Exception:
                pass
            self._voz_local.disponivel()
            # A CONEXAO DA VOZ, ABERTA ANTES DA PRIMEIRA FALA (02/09/2026).
            #
            # As vozes de estudio vao pela rede, e abrir a conexao custa o
            # aperto de mao TLS: medido, 1,20 s ate o primeiro som contra 0,65 s
            # com a conexao ja de pe. Aqui ela sobe junto com o whisper e o
            # Kokoro, entao nem a PRIMEIRA frase paga — e a primeira e
            # justamente a que ele repara, porque acabou de abrir o programa.
            try:
                from core.voz_openai import Conexao as _ConVoz  # noqa: PLC0415
                _ConVoz.aquecer()
            except Exception as _exc_voz:
                _voice_diag("voz_nao_aqueceu", str(_exc_voz)[:100])
            # De novo, agora que o whisper terminou de carregar: se a saudacao
            # derrubou alguma das duas conexoes, esta chamada as reergue antes
            # de o dono falar a primeira frase. Ver `_aquecer_boca`.
            self._aquecer_boca()
            from core.cerebro_openrouter import CerebroOpenRouter
            if self._openrouter is None:
                self._openrouter = CerebroOpenRouter()
                self._openrouter.executor = self._executar_ferramenta_local
                self._openrouter.ferramentas = CerebroOpenRouter.converter_ferramentas(
                    TOOL_DECLARATIONS)
            # A ponte com a VPS: e por ela que ele pensa desde 05/09/2026.
            self._openrouter.ponte = getattr(self.ui, "_codex_relay", None)
            self._openrouter.falar = self._falar_local
            # O CEREBRO RAPIDO (Gemini do proprio cliente, 01/10/2026) ja abre a
            # conexao agora e a mantem viva: a primeira pergunta nao paga o
            # aperto de mao. Ver `core/cerebro_rapido.py`.
            try:
                from core import cerebro_rapido as _cr  # noqa: PLC0415
                _cr.aquecer()
            except Exception as _exc_cr:
                _voice_diag("cerebro_rapido_nao_aqueceu", str(_exc_cr)[:90])
            # A VISAO TAMBEM GANHA A PONTE: quando a cota da Gemini do cliente
            # acaba, quem enxerga a tela passa a ser o Codex da VPS, que nao tem
            # cota. Sem esta linha, "olha minha tela" morreria junto com a cota.
            try:
                from actions import screen_processor as _sp  # noqa: PLC0415
                _sp.definir_ponte(getattr(self.ui, "_codex_relay", None))
            except Exception as _exc_v:
                _voice_diag("visao_sem_ponte", str(_exc_v)[:90])
            # E O RESTO DAS FERRAMENTAS TAMBEM. Sete delas ainda falavam com a
            # Gemini na unha — gerar codigo, montar projeto, ler arquivo,
            # entender comando de sistema, achar voo, escrever script da area de
            # trabalho, achar elemento na tela. Com esta ponte, a cota da Gemini
            # deixa de derrubar qualquer uma delas.
            try:
                from core import gemini_socorro as _gs  # noqa: PLC0415
                _gs.definir_ponte(getattr(self.ui, "_codex_relay", None))
            except Exception as _exc_s:
                _voice_diag("socorro_sem_ponte", str(_exc_s)[:90])
            # A escolha do cliente ja foi lida la em cima (a saudacao precisava
            # dela); aqui so o que depende do cerebro, que acabou de nascer.
            # O personagem vai para ele porque e dali que sai o NOME PROPRIO da
            # IA no prompt (voz Nova = ela se chama Nova).
            self._openrouter.personagem = escolha["personagem"]
            self._openrouter.definir_personalidade(escolha["personalidade"])
            # (o "whisper, Kokoro e cerebro prontos" saiu do chat em 05/09/2026 —
            #  e recado de tecnico; quem fala com o dono e a saudacao, que a esta
            #  altura ja saiu.)
            _voice_diag("local_pronto", "whisper, voz e cerebro carregados")
        except Exception as exc:
            self.ui.write_log(f"LOCAL: falha ao preparar — {str(exc)[:120]}")

    # ── A SAUDACAO DE ABERTURA (05/09/2026, pedido do Diego) ──────────────
    #
    # Ela entrou no lugar de dois recados de tecnico que apareciam no chat
    # ("JARVIS pronto — modo local, sem Gemini" e "whisper, Kokoro e cerebro
    # prontos"). Quem abre a conversa agora e ele, falando.
    #
    # Tres cuidados que a frase carrega:
    #   1. A HORA DECIDE o cumprimento — dizer "bom dia" as nove da noite e o
    #      tipo de detalhe que faz o assistente parecer burro;
    #   2. ELA VARIA. Frase fixa vira ruido: na terceira abertura do dia
    #      ninguem mais escuta. Sorteia entre as opcoes da faixa de horario;
    #   3. UMA VEZ POR ABERTURA. O laco do modo local pode ser reentrado (troca
    #      de cerebro, reconexao) e o dono nao pode ser cumprimentado duas
    #      vezes seguidas.
    #
    # O NOME E O DA VOZ ESCOLHIDA, nao "JARVIS" na pedra: com a voz Nova ele se
    # chama Nova, e se apresentar com o nome errado na PRIMEIRA frase do dia e
    # o pior lugar possivel para errar isso.
    #
    # Liga e desliga na engrenagem (VOZ E JEITO -> SAUDACAO AO ABRIR); o valor
    # mora no `config/persona.json`, campo `saudacao`.
    _SAUDACOES = {
        "manha": ("Bom dia, senhor. {nome} as suas ordens.",
                  "Bom dia, senhor. Estou pronto para o que o senhor precisar.",
                  "Bom dia, senhor. Sistema no ar e tudo em ordem por aqui."),
        "tarde": ("Boa tarde, senhor. {nome} as suas ordens.",
                  "Boa tarde, senhor. Estou pronto quando o senhor quiser.",
                  "Boa tarde, senhor. Tudo em ordem por aqui; e so chamar."),
        "noite": ("Boa noite, senhor. {nome} as suas ordens.",
                  "Boa noite, senhor. Estou por aqui para o que o senhor precisar.",
                  "Boa noite, senhor. Sistema no ar; e so falar comigo."),
    }
    # As mesmas tres por faixa, para o cliente em ingles (24/09/2026).
    _SAUDACOES_EN = {
        "manha": ("Good morning, sir. {nome} at your service.",
                  "Good morning, sir. I'm ready for whatever you need.",
                  "Good morning, sir. Systems online and everything is in order."),
        "tarde": ("Good afternoon, sir. {nome} at your service.",
                  "Good afternoon, sir. Ready whenever you are.",
                  "Good afternoon, sir. Everything is in order here; just call me."),
        "noite": ("Good evening, sir. {nome} at your service.",
                  "Good evening, sir. I'm here for whatever you need.",
                  "Good evening, sir. Systems online; just talk to me."),
    }

    def _saudar_na_abertura(self) -> None:
        """Cumprimenta o dono uma vez, com a saudacao certa para a hora."""
        if getattr(self, "_ja_saudou", False):
            return
        self._ja_saudou = True
        escolha = getattr(self.ui, "persona_atual", None) or {}
        if not escolha.get("saudacao", True):
            _voice_diag("saudacao", "desligada pelo dono")
            return
        try:
            import random as _r  # noqa: PLC0415
            try:
                from core.personas import nome_da_ia  # noqa: PLC0415
                nome = nome_da_ia(escolha.get("personagem", "")) or "JARVIS"
            except Exception:
                nome = "JARVIS"
            hora = datetime.now().hour
            faixa = "manha" if 5 <= hora < 12 else ("tarde" if 12 <= hora < 18 else "noite")
            _tabela = self._SAUDACOES_EN if _em_ingles() else self._SAUDACOES
            frase = _r.choice(_tabela[faixa]).format(nome=nome)
            _voice_diag("saudacao", frase[:60])
            self._falar_local(
                frase,
                ao_primeiro_som=lambda: self.ui.write_log(
                    f"{_nome_falante(self.ui)}: {frase}"))
        except Exception as exc:
            _voice_diag("saudacao_falhou", str(exc)[:120])

    # ── VOZ UNICA E CONVERSA DIRETA (08/10/2026) ─────────────────────────────
    #
    # Pedido do Diego: "ainda ta demorando uns 4 segundos, nao tem como
    # diminuir para pelo menos 2?" e depois "deixa UMA conexao so para a voz
    # dele, e o resto (cerebro e tudo mais) com o Codex la na VPS; a voz tem de
    # ser o free (2.5) para responder o mais rapido possivel".
    #
    # Medido: a chave gratis so abriu mais 2 sessoes ao vivo com o JARVIS de pe
    # (3a em diante: "exceeded your current quota"). Ficam DUAS sessoes, sem
    # espelho: a `GeminiConversa` CONVERSA (a frase do dono vai crua e ela
    # responde falando — 1,1 a 1,6 s ate o 1o som) e a da `VozCharon` LE o
    # texto do cerebro (Codex/Gemini). Uma so fazendo as duas foi reprovada.

    _CONV_ACAO = re.compile(
        r"\b(abr[aei]\w*|abrir|fech\w*|cri[ae]\w*|criar|fa[cç]a\w*|fazer|faz\b|pesquis\w*|procur\w*|"
        r"busc\w*|encontr\w*|envi[ae]\w*|mand[ae]\w*|toc[ae]\w*|toque\w*|coloc\w*|bot[ae]r?\b|"
        r"lig[aue]\w*|deslig\w*|aument\w*|diminu\w*|abaix\w*|desenh\w*|edit\w*|escrev\w*|"
        r"digit\w*|salv[ae]\w*|apag\w*|delet\w*|exclu\w*|remov\w*|mov[ae]\w*|copi[ae]\w*|"
        r"instal\w*|desinstal\w*|baix[ae]\w*|organiz\w*|lembr[ae]\w*|agend\w*|marc[ae]\w*|"
        r"configur\w*|mud[ae]\w*|troc[ae]\w*|ativ[ae]\w*|desativ\w*|mostr\w*|leia\b|ler\b|"
        r"traduz\w*|calcul\w*|resum\w*|ger[ae]r?\b|minimiz\w*|maximiz\w*|reinici\w*|"
        r"inici[ae]\w*|execut\w*|rod[ae]r?\b|paus[ae]\w*|pul[ae]r?\b|grav[ae]\w*|film[ae]\w*|"
        r"fotograf\w*|compartilh\w*|conect\w*|acess\w*|naveg\w*|compr[ae]\w*|cancel\w*|"
        r"para\s+(a|o|de)\s+(m[uú]sica|v[ií]deo|tocar|gravar)|entr[ae]\s+(no|na|em))", re.I)
    _CONV_OBJETO = re.compile(
        r"\b(arquivos?|pastas?|programas?|aplicativos?|app|navegador|chrome|edge|firefox|youtube|"
        r"spotify|m[uú]sicas?|playlist|fotos?|imagens?|sites?|p[aá]ginas?|whats\w*|zap|"
        r"mensage\w*|e-?mails?|tela|janelas?|[aá]rea de trabalho|desktop|volume|brilho|wi-?fi|"
        r"bluetooth|impressora|documentos?|planilhas?|word|excel|bloco de notas|calculadora|"
        r"alarmes?|timer|cron[oô]metro|agenda|webcam|c[aâ]mera|microfone|cowork|projetos?|"
        r"c[oó]digo|computador|pc|notebook|lembretes?)\b", re.I)
    _CONV_INFO = re.compile(
        r"\b(not[ií]cias?|placar|jogo d[aeo]|resultado|pre[cç]os?|cota[cç][aã]o|d[oó]lar|euro|"
        r"bitcoin|bolsa|a[cç][oõ]es|quanto custa|quem ganhou|google|internet|previs[aã]o|clima|"
        r"temperatura|chov\w*|chuv\w*|graus|tr[aâ]nsito)\b"
        # "como vai estar o tempo amanha?" e previsao, nao conversa
        r"|\btempo\s+(hoje|amanh\w*|agora|l[aá] fora)"
        r"|\b(como|vai|est[aá]|t[aá])\s+(estar\s+|ficar\s+)?o\s+tempo\b", re.I)
    _CONV_MEMORIA = re.compile(
        r"\b(lembra\w*|anot\w*|guard[ae]\w*|esque[cç]\w*|anivers[aá]rio|minhas? tarefas?|"
        r"o que eu (te )?(falei|disse|pedi)|voc[eê] sabe (o meu|a minha|meu|minha)|qual (e |é )?(o )?meu)", re.I)
    _CONV_SALVE = re.compile(r"\bmand[ae]\w*\s+(um\s+|uns\s+)?(salve|abra[cç]o|beijo|oi|al[oô])", re.I)

    def _conversa_direta_pode(self, texto: str) -> bool:
        """A frase e CONVERSA (vai direto para a voz) ou pede algo (cerebro)?

        Na duvida, cerebro: ele e mais lento, mas faz. A conversa direta so
        conversa — nunca mexe no computador."""
        try:
            if _em_ingles():
                return False
            if self._usando_pc or time.monotonic() < self._esperando_tarefa_pc_ate:
                return False
            if getattr(self, "_pergunta_pendente", False):
                return False
            t = str(texto or "").strip()
            if not t or len(t.split()) > 30:
                return False
            if self._motor_voz_unica() is None:
                return False
            tl = t.lower()
            if self._CONV_OBJETO.search(tl) or self._CONV_INFO.search(tl) or self._CONV_MEMORIA.search(tl):
                return False
            if self._CONV_ACAO.search(tl) and not self._CONV_SALVE.search(tl):
                return False
            return True
        except Exception:
            return False

    def _motor_voz_unica(self):
        """A conexao unica da voz (`GeminiConversa`), refeita se a voz ou o
        jeito mudarem na engrenagem. None em ingles ou sem a voz da Gemini."""
        try:
            if _em_ingles():
                return None
        except Exception:
            pass
        ch = getattr(self, "_charon", None)
        if ch is None:
            return None
        m = getattr(self, "_voz_unica", None)
        if m is None or m.voz != ch.voz or m.jeito != ch.jeito:
            from core.gemini_voice import GeminiConversa  # noqa: PLC0415
            velho = m
            m = GeminiConversa(ch.voz, ch.jeito, self._instrucao_voz)
            self._voz_unica = m
            if velho is not None:
                # Fecha a sessao antiga: a chave gratis tem poucas vagas.
                try:
                    asyncio.run_coroutine_threadsafe(
                        velho._reset_persistent_session(), velho._garantir_laco())
                except Exception:
                    pass
        return m

    def _instrucao_voz(self) -> str:
        """Instrucao da voz unica, montada a cada conexao nova (hora e conversa
        recente sempre frescas). Enxuta de proposito: com o prompt inteiro do
        cerebro (12 mil letras) o primeiro som subia de ~1,4 para ~2,3 s."""
        from core.personas import PERSONALIDADES, PERSONALIDADE_PADRAO, nome_da_ia  # noqa: PLC0415
        escolha = getattr(self.ui, "persona_atual", None) or {}
        jeito = (PERSONALIDADES.get(escolha.get("personalidade") or "")
                 or PERSONALIDADES[PERSONALIDADE_PADRAO])
        nome = nome_da_ia(escolha.get("personagem", "")) or "JARVIS"
        try:
            from core.dono import resumo_para_prompt  # noqa: PLC0415
            dono = resumo_para_prompt()
        except Exception:
            dono = ""
        agora = datetime.now()
        dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
                "sexta-feira", "sábado", "domingo"]
        quando = f"{dias[agora.weekday()]}, {agora:%d/%m/%Y}, {agora:%H:%M}"
        hist = []
        try:
            cer = getattr(self, "_openrouter", None)
            for msg in (cer.historico if cer is not None else [])[-8:]:
                txt = re.sub(r"\[NOTA INTERNA.*?\]", "", str(msg.get("content") or ""), flags=re.S)
                txt = re.sub(r"\s+", " ", txt).strip()
                if not txt or txt.startswith(("AGORA SAO", "LEMBRETE", "PARE.")):
                    continue
                quem = "Dono" if msg.get("role") == "user" else "Você"
                hist.append(f"{quem}: {txt[:220]}")
        except Exception:
            pass
        partes = [
            f"Seu nome é {nome}. Você é o assistente pessoal do dono deste computador e está "
            f"FALANDO com ele em voz alta, ao vivo, em português do Brasil.",
            "A mensagem é o dono falando com você: responda falando, direto e curto — "
            "conversa: uma ou duas frases; pergunta de conhecimento, dica ou opinião: o conteúdo "
            "primeiro, até quarenta palavras. Fale como gente, natural. Nunca use lista, título, "
            "emoji nem risada escrita. Nunca repita uma fala sua do mesmo jeito. Não diga o nome "
            "dele a toda frase. Não cumprimente se ele não cumprimentou.",
            "AQUI VOCÊ SÓ CONVERSA: nunca diga que abriu, criou, mandou, pesquisou ou fez qualquer "
            "coisa no computador, e nunca prometa fazer. Se ele pedir uma ação, diga em poucas "
            "palavras para ele pedir de novo, que você faz.",
        ]
        if dono:
            partes.append("QUEM É O DONO:\n" + dono)
        partes.append("SEU JEITO DE SER:\n" + str(jeito.get("jeito") or ""))
        partes.append("AGORA: " + quando + ".")
        if hist:
            partes.append("CONVERSA RECENTE (só contexto; não repita):\n" + "\n".join(hist))
        return "\n\n".join(partes)

    def _conversar_direto(self, texto: str, t_ouvir: float, t0: float) -> bool:
        """A voz unica responde a conversa FALANDO. False = nada saiu (o
        cerebro assume a frase)."""
        import time as _t  # noqa: PLC0415
        motor = self._motor_voz_unica()
        if motor is None or self._loop is None or self.audio_in_queue is None:
            return False
        t1 = _t.monotonic()
        self._ultimo_ttfs = 0.0
        estado = {"bytes": 0, "t_som": 0.0}

        def _no_pedaco(raw: bytes) -> None:
            if not raw:
                return
            if not estado["t_som"]:
                estado["t_som"] = time.monotonic()
                self.set_speaking(True)
                self._ultimo_ttfs = _t.monotonic() - t1
                _voice_diag("charon_primeiro_som",
                            f"{self._ultimo_ttfs:.1f}s ate o 1o pedaco (conversa direta)")
            estado["bytes"] += len(raw)
            self._mic_fechado_ate = max(
                self._mic_fechado_ate,
                estado["t_som"] + estado["bytes"] / 2 / 24000 + self._GUARDA_POS_FALA_S)
            for pos in range(0, len(raw), 2400):
                self._loop.call_soon_threadsafe(
                    self.audio_in_queue.put_nowait, raw[pos: pos + 2400])

        _trava = self._trava_boca()
        _pegou = _trava.acquire(timeout=10)
        dito = ""
        try:
            dito = motor.speak_para_fila(texto, _no_pedaco, modo="conversar") or ""
        except Exception as exc:
            _voice_diag("conversa_direta_falhou", f"{type(exc).__name__}: {str(exc)[:100]}")
        finally:
            if _pegou:
                _trava.release()
        if not estado["bytes"]:
            return False
        dur = estado["bytes"] / 2 / 24000
        dito = re.sub(r"\s+", " ", str(dito or "")).strip()
        try:
            self.ui.write_log(f"{_nome_falante(self.ui)}: {dito or '...'}")
        except Exception:
            pass
        try:
            from memory.diario import anotar as _anotar  # noqa: PLC0415
            _anotar("voce", dito)
        except Exception:
            pass
        # O cerebro continua sabendo da conversa (contexto da proxima acao).
        try:
            cer = getattr(self, "_openrouter", None)
            if cer is not None and dito:
                cer.historico.append({"role": "user", "content": texto})
                cer.historico.append({"role": "assistant", "content": dito})
                del cer.historico[:-120]
        except Exception:
            pass
        _voice_diag("conversa_direta",
                    f"1o som {self._ultimo_ttfs:.1f}s, fala {dur:.1f}s: {dito[:70]!r}")
        _voice_diag("local_tempos",
                    f"direto ouvir={t_ouvir:.1f}s pensar=0.0s voz={self._ultimo_ttfs:.1f}s "
                    f"tocando={dur:.1f}s atraso={t_ouvir + self._ultimo_ttfs:.1f}s "
                    f"total={_t.monotonic() - t0:.1f}s")
        try:
            self.ui.write_log(f"LOCAL: ouvir {t_ouvir:.1f}s | conversa direta | "
                              f"voz {self._ultimo_ttfs:.1f}s")
        except Exception:
            pass
        return True

    def _aquecer_boca(self) -> None:
        """Abre as conexoes da VOZ antes de precisar delas (05/09/2026).

        Chamada em dois momentos, os dois fora do caminho da fala: ao abrir o
        programa e assim que o whisper entende a frase — ou seja, enquanto o
        cerebro pensa, que sao 1 a 2 s de graca.

        Por que vale: medido no log de 05/09, o primeiro som sai em 6,1 s com a
        conexao fria e em 1,2 a 2,0 s com ela ja de pe. O aperto de mao e o
        mesmo custo que a memoria [[jarvis-latencia-medida]] ja tinha achado nas
        vozes de estudio; aqui ele estava sobrando na Gemini.

        Nao levanta nada: aquecer e cortesia, e a fala sobe a conexao sozinha
        se isto falhar.
        """
        try:
            from core.voz_local import VozLocal  # noqa: PLC0415
            if self._voz_local is None:
                self._voz_local = VozLocal()
            from core.personas import PERSONAGENS as _PG  # noqa: PLC0415
            _base = (_PG.get(self._voz_local.voz) or {}).get("base", "")
            if _base == "__gemini__":
                from core.gemini_voice import VozCharon  # noqa: PLC0415
                _voz_g = (_PG.get(self._voz_local.voz) or {}).get("voz_gemini", "Charon")
                _jeito = ""
                try:
                    _jeito = str(getattr(self.ui, "persona_atual", {})
                                 .get("personalidade") or "")
                except Exception:
                    pass
                if self._charon is None:
                    self._charon = VozCharon(_voz_g, _jeito)
                self._charon.voz = _voz_g
                self._charon.jeito = _jeito
                self._charon.aquecer_em_fundo()
                # A conexao da CONVERSA DIRETA tambem fica de pe (e se renova
                # se ficou parada). Sao duas sessoes: ler e conversar.
                _conv = self._motor_voz_unica()
                if _conv is not None:
                    _conv.aquecer_em_fundo()
            elif _base == "__openai__":
                from core.voz_openai import Conexao as _ConVoz  # noqa: PLC0415
                _ConVoz.aquecer()
        except Exception as _exc_b:
            _voice_diag("boca_nao_aqueceu", str(_exc_b)[:110])

    def _avisar_do_computador(self, texto: str) -> None:
        """O aviso de abertura do computer use. Nao pode sair mudo.

        Uma unica frase por tarefa, e a mais importante delas: e ela que manda
        o dono tirar a mao do mouse.

        SAI INTEIRA, E GRAVADA (22/09/2026, noite). Queixa do Diego: *"quando
        ele comeca a falar para nao mexer no teclado ele fica falando cortando
        muito toda hora"*. Tocando conforme chega da Gemini, cada tropeco da
        rede virava um buraco na frase — e as 21:13 ela ainda parou no meio.
        Agora a frase fixa sai do disco (`core/fala_gravada.py`), e qualquer
        outra e gerada inteira ANTES de tocar. Se nada disso der, o caminho
        antigo continua de rede, com a voz de socorro.
        """
        try:
            from actions.narrador_pc import ABERTURA_PC  # noqa: PLC0415
            guardar = (str(texto or "").strip() == ABERTURA_PC.strip())
        except Exception:
            guardar = False
        try:
            if self._falar_inteiro(texto, guardar=guardar):
                return
        except Exception as exc:
            _voice_diag("fala_inteira_falhou", f"{type(exc).__name__}: {str(exc)[:90]}")
        self._falar_local(texto, nao_pode_calar=True)

    def _falar_no_computador(self, texto: str) -> None:
        """A fala do MEIO de uma tarefa na tela: lembretes e recados soltos.

        Inteira pelo mesmo motivo do aviso — o dono ouve a frase toda, sem
        buracos. Atrasar um lembrete em 3 s nao custa nada; picotar custa.
        """
        try:
            if self._falar_inteiro(texto):
                return
        except Exception as exc:
            _voice_diag("fala_inteira_falhou", f"{type(exc).__name__}: {str(exc)[:90]}")
        self._falar_local(texto)

    def _esta_falando(self) -> bool:
        """Ainda sai (ou esta para sair) voz pela caixa?

        O laco do computer use pergunta isto antes de cada passo. NAO usa a
        marca `_is_speaking` sozinha: ela ja ficou presa em True no passado, e
        aqui isso travaria a tarefa. Olha o que da para medir — fila com audio,
        lote tocado ha pouco, e o fim calculado da ultima fala inteira.
        """
        try:
            q = self.audio_in_queue
            if q is not None and not q.empty():
                return True
        except Exception:
            pass
        agora = time.monotonic()
        # A caixa guarda ~0,25 s e o tocador pega lotes de 0,2 s: o ultimo
        # lote sai da fila antes de o som acabar de verdade.
        if agora - float(getattr(self, "_ultimo_audio_tocado", 0.0) or 0.0) < 1.2:
            return True
        return agora < float(getattr(self, "_fala_inteira_ate", 0.0) or 0.0)

    def _trava_boca(self):
        """Uma fala de nuvem por vez na mesma sessao de voz.

        A sessao ao vivo da Gemini e UMA conversa: dois pedidos ao mesmo tempo
        misturam os audios. Com o aviso sendo gravado em segundo plano (ver
        `_preparar_aviso_do_computador`), passou a existir o risco real de duas
        falas pedirem a mesma sessao junto.
        """
        trava = getattr(self, "_trava_da_boca", None)
        if trava is None:
            trava = threading.RLock()
            self._trava_da_boca = trava
        return trava

    def _motor_de_fora(self):
        """A boca de nuvem da voz escolhida e a chave que identifica o SOM dela.

        Devolve (None, "") quando a voz e o Kokoro — ai nao ha rede, e a fala
        ja sai inteira pelo caminho de sempre. O motor e o MESMO objeto que o
        `_falar_local` usa (mesma sessao aquecida, mesma trava).
        """
        from core.voz_local import VozLocal  # noqa: PLC0415
        if self._voz_local is None:
            self._voz_local = VozLocal()
        from core.personas import PERSONAGENS as _PG  # noqa: PLC0415
        _p = _PG.get(self._voz_local.voz) or {}
        _base = _p.get("base", "")
        _jeito = ""
        try:
            _jeito = str(getattr(self.ui, "persona_atual", {}).get("personalidade") or "")
        except Exception:
            pass
        try:
            from core.velocidade_fala import VELOCIDADE as _VEL  # noqa: PLC0415
        except Exception:
            _VEL = 1.0
        if _base == "__openai__":
            from core.voz_openai import VozOpenAI  # noqa: PLC0415
            if (getattr(self, "_voz_api", None) is None
                    or self._voz_api.voz != self._voz_local.voz
                    or self._voz_api.jeito != _jeito):
                self._voz_api = VozOpenAI(
                    self._voz_local.voz, jeito=_jeito,
                    ponte=getattr(self.ui, "_codex_relay", None))
            return self._voz_api, f"openai|{self._voz_local.voz}|{_jeito}|{_VEL}"
        if _base == "__gemini__":
            from core.gemini_voice import VozCharon  # noqa: PLC0415
            _voz_g = _p.get("voz_gemini", "Charon")
            if self._charon is None:
                self._charon = VozCharon(_voz_g, _jeito)
            self._charon.voz = _voz_g
            self._charon.jeito = _jeito
            return self._charon, f"gemini|{self._voz_local.voz}|{_voz_g}|{_jeito}|{_VEL}"
        return None, ""

    # Uma fala e "inteira" quando tem pelo menos isto do tempo esperado. Para
    # GRAVAR a regua e mais alta: o que vai para o disco toca para sempre.
    _INTEIRA_PARA_TOCAR = 0.65
    _INTEIRA_PARA_GRAVAR = 0.80

    @staticmethod
    def _fala_esperada_s(texto: str) -> float:
        try:
            from core.velocidade_fala import VELOCIDADE as _VEL  # noqa: PLC0415
        except Exception:
            _VEL = 1.0
        return len(str(texto or "")) / 14.0 / max(1.0, _VEL)

    def _fala_completa(self, texto: str, dur: float, regua: float) -> bool:
        esperado = self._fala_esperada_s(texto)
        return esperado <= 2.0 or dur >= esperado * regua

    def _gerar_inteiro(self, motor, texto: str) -> bytes:
        """Gera a fala INTEIRA sem tocar nada. Refaz uma vez se vier cortada.

        Refazer aqui e seguro — ao contrario do caminho que toca conforme
        chega, onde repetir faria o dono ouvir a frase duas vezes (a armadilha
        de 30/08): nada saiu pela caixa ainda.
        """
        melhor = b""
        for tentativa in (1, 2):
            pedacos: list = []

            def _junta(raw, _p=pedacos):
                if raw:
                    _p.append(bytes(raw))

            trava = self._trava_boca()
            pegou = trava.acquire(timeout=25)
            try:
                motor.speak_para_fila(texto, _junta)
            except Exception as exc:
                _voice_diag("fala_inteira_erro",
                            f"tentativa {tentativa}: {type(exc).__name__} {str(exc)[:80]}")
            finally:
                if pegou:
                    trava.release()
            raw = b"".join(pedacos)
            if len(raw) > len(melhor):
                melhor = raw
            dur = len(raw) / 2 / 24000
            if raw and self._fala_completa(texto, dur, self._INTEIRA_PARA_TOCAR):
                return raw
            if tentativa == 1:
                _voice_diag("fala_inteira_cortada",
                            f"{dur:.1f}s para {len(str(texto))} letras "
                            f"(esperava ~{self._fala_esperada_s(texto):.1f}s) — gerando de novo")
        return melhor

    def _tocar_bruto(self, raw: bytes, ao_primeiro_som=None) -> float:
        """Poe a fala inteira na fila de uma vez. Devolve a duracao."""
        raw = bytes(raw[: len(raw) - (len(raw) % 2)])
        dur = len(raw) / 2 / 24000
        agora = time.monotonic()
        self._mic_fechado_ate = max(self._mic_fechado_ate,
                                    agora + dur + self._GUARDA_POS_FALA_S)
        # Folga do tocador (~0,5 s) antes do primeiro som.
        self._fala_inteira_ate = agora + dur + 0.8
        self.set_speaking(True)
        if ao_primeiro_som:
            try:
                ao_primeiro_som()
            except Exception:
                pass
        for pos in range(0, len(raw), 2400):
            self._loop.call_soon_threadsafe(self.audio_in_queue.put_nowait,
                                            raw[pos: pos + 2400])
        return dur

    # Quanto o aviso espera pela gravacao que ja esta em andamento.
    _ESPERA_AVISO_EM_GRAVACAO_S = 20.0

    def _falar_inteiro(self, texto: str, ao_primeiro_som=None,
                       guardar: bool = False) -> bool:
        """Fala a frase INTEIRA de uma vez, em vez de tocar conforme chega.

        `guardar=True`: frase fixa (o aviso do computer use). Tenta o disco
        primeiro; se nao houver, grava depois de gerar — desde que tenha saido
        completa.

        Devolve False quando nao da para falar assim (voz local, motor sem
        audio): quem chamou cai no caminho de sempre.
        """
        if not texto or self._loop is None or self.audio_in_queue is None:
            return False
        texto = _frase_no_idioma(texto)
        motor, chave = self._motor_de_fora()
        if motor is None:
            return False
        from core import fala_gravada  # noqa: PLC0415
        raw = fala_gravada.ler(chave, texto) if guardar else None
        origem = "gravada"
        if raw is None and guardar:
            # A gravacao comecou no "ativar computador" e ainda esta andando?
            # Espera por ela — gerar de novo em paralelo disputaria a mesma
            # sessao e custaria o dobro.
            ev = getattr(self, "_aviso_em_gravacao", None)
            if ev is not None and not ev.is_set():
                ev.wait(self._ESPERA_AVISO_EM_GRAVACAO_S)
                raw = fala_gravada.ler(chave, texto)
        if raw is None:
            origem = "gerada"
            raw = self._gerar_inteiro(motor, texto)
            if not raw:
                return False
            dur_g = len(raw) / 2 / 24000
            if guardar and self._fala_completa(texto, dur_g, self._INTEIRA_PARA_GRAVAR):
                if fala_gravada.gravar(chave, texto, raw):
                    _voice_diag("aviso_gravado", f"{dur_g:.1f}s guardados para as proximas tarefas")
        try:
            from memory.diario import anotar as _anotar  # noqa: PLC0415
            _anotar("voce", texto)
        except Exception:
            pass
        dur = self._tocar_bruto(raw, ao_primeiro_som)
        _voice_diag("fala_inteira", f"{origem} {dur:.1f}s ({len(str(texto))} letras)")
        return True

    def _reservar_aviso_do_computador(self) -> None:
        """Na hora do "ativar computador": marca que o aviso VAI ser gravado.

        E sincrono de proposito. A gravacao so comeca depois de a pergunta "o
        que o senhor quer que eu faca?" sair (as duas usam a mesma sessao de
        voz); se o dono responder por cima da pergunta, o aviso ja encontra a
        marca e espera a gravacao, em vez de gerar a mesma frase em paralelo.
        """
        try:
            from actions.narrador_pc import ABERTURA_PC  # noqa: PLC0415
            from core import fala_gravada  # noqa: PLC0415
            motor, chave = self._motor_de_fora()
            if motor is None or (fala_gravada.ler(chave, ABERTURA_PC) is not None
                                 and fala_gravada.ler(chave, _frase_no_idioma(self._PAREI_PC)) is not None):
                return
        except Exception:
            return
        ev = getattr(self, "_aviso_em_gravacao", None)
        if ev is None or ev.is_set():
            self._aviso_em_gravacao = threading.Event()

    def _preparar_aviso_do_computador(self) -> None:
        """Grava o aviso de abertura ENQUANTO o dono dita a tarefa.

        Roda na mesma thread da pergunta, logo depois dela. Da pergunta ate a
        tarefa chegar passam uns 5 a 10 s — tempo de sobra para gerar a frase
        fixa uma vez e guardar. Na primeira tarefa com cada voz ele ja sai
        inteiro; da segunda em diante, direto do disco, na hora.
        """
        ev = getattr(self, "_aviso_em_gravacao", None)
        if ev is None or ev.is_set() or getattr(self, "_aviso_gravando", False):
            return
        self._aviso_gravando = True
        try:
            from actions.narrador_pc import ABERTURA_PC  # noqa: PLC0415
            from core import fala_gravada  # noqa: PLC0415
            motor, chave = self._motor_de_fora()
            if motor is None:
                return
            # O aviso de abertura e o "parei" do ESC: os dois saem do disco.
            # O AVISO PRIMEIRO, E LIBERADO NA HORA: a tarefa espera so por ele
            # (o evento), nunca pelo "parei", que e gravado logo em seguida.
            for _fixa in (ABERTURA_PC, _frase_no_idioma(self._PAREI_PC)):
                if fala_gravada.ler(chave, _fixa) is None:
                    raw = self._gerar_inteiro(motor, _fixa)
                    dur = len(raw) / 2 / 24000 if raw else 0.0
                    if raw and self._fala_completa(_fixa, dur, self._INTEIRA_PARA_GRAVAR):
                        if fala_gravada.gravar(chave, _fixa, raw):
                            _voice_diag("aviso_gravado", f"{dur:.1f}s prontos ({len(_fixa)} letras)")
                    else:
                        _voice_diag("aviso_nao_gravado",
                                    f"{dur:.1f}s para {len(_fixa)} letras — incompleto")
                if _fixa is ABERTURA_PC:
                    ev.set()
        except Exception as exc:
            _voice_diag("aviso_nao_gravado", f"{type(exc).__name__}: {str(exc)[:80]}")
        finally:
            self._aviso_gravando = False
            ev.set()

    def _falar_local(self, texto: str, ao_primeiro_som=None,
                     nao_pode_calar: bool = False) -> None:
        """Fala pela fila normal de audio do JARVIS.

        `nao_pode_calar=True`: esta frase NAO pode virar texto mudo no chat.
        Vale para o aviso de abertura do computer use — "vou mexer no
        computador, nao toque no mouse". Se ele nao sai pela caixa, o dono nao
        fica sabendo que a tarefa comecou e encosta no mouse no meio dela. Ver
        a regra da engasgada la embaixo.

        Kokoro por padrao; Charon da Gemini se o cliente escolher na engrenagem.

        `ao_primeiro_som` e chamado quando o PRIMEIRO pedaco de audio entra na
        fila. Serve para escrever no chat na mesma hora em que a voz comeca, em
        vez de escrever 8 segundos antes — que era a reclamacao do Diego: "a
        fala tem que vir junto com o que ele responde no chat".
        """
        texto = _frase_no_idioma(texto)
        import time as _t
        _t_pedido = _t.monotonic()
        self._ultimo_ttfs = 0.0
        # O DIARIO guarda o que ELE respondeu, e nao so o que o dono falou:
        # sem os dois lados, "voce lembra o que voce me disse?" nao tem
        # resposta. Ver `memory/diario.py`.
        try:
            from memory.diario import anotar as _anotar  # noqa: PLC0415
            _anotar("voce", texto)
        except Exception:
            pass
        from core.voz_local import VozLocal
        if self._voz_local is None:
            self._voz_local = VozLocal()

        # CHARON: sessao ao vivo da Gemini usada SO COMO BOCA. Manda texto,
        # recebe audio. Nao sobe audio nenhum, que era o que queimava a cota.
        # VOZ DE FORA DO PC? (31/08/2026)
        #
        # Duas familias nao sao Kokoro: a Charon, da Gemini, e as treze vozes do
        # ChatGPT. O personagem diz qual e qual pelo campo `base`, e o resto do
        # caminho e identico — os dois motores tem o mesmo contrato
        # (`speak_para_fila`), entao nao precisa de dois codigos.
        from core.personas import PERSONAGENS as _PG  # noqa: PLC0415
        _base = (_PG.get(self._voz_local.voz) or {}).get("base", "")
        if _base in ("__gemini__", "__openai__"):
            try:
                if _base == "__openai__":
                    # A API da OpenAI entrega o audio EM FLUXO: o primeiro
                    # pedaco sai em ~1,1 s e ja toca, enquanto o resto ainda
                    # esta sendo gerado. A Charon leva 3,7 s para o primeiro som.
                    from core.voz_openai import VozOpenAI
                    # O jeito de ser tambem dirige a VOZ: brincalhao ri,
                    # ignorante resmunga, mordomo pausa. Sem isso a mesma
                    # entrega servia para todos, e soava de locutor.
                    _jeito = ""
                    try:
                        _jeito = str(getattr(self.ui, "persona_atual", {})
                                     .get("personalidade") or "")
                    except Exception:
                        pass
                    if (getattr(self, "_voz_api", None) is None
                            or self._voz_api.voz != self._voz_local.voz
                            or self._voz_api.jeito != _jeito):
                        # `ponte`: no PC do cliente nao ha chave, e a fala sai
                        # pela VPS. Aqui, na maquina do dono, a chave local
                        # existe e a ponte fica sem uso.
                        self._voz_api = VozOpenAI(
                            self._voz_local.voz, jeito=_jeito,
                            ponte=getattr(self.ui, "_codex_relay", None))
                        # Registra QUAL jeito a voz recebeu. O Diego reclamou que
                        # trocar o jeito nao mudava a fala; sem este registro nao
                        # da para saber se o problema e o codigo nao passar o
                        # jeito ou o modelo nao obedecer a direcao (01/09/2026).
                        _voice_diag("voz_estilo",
                                    f"voz={self._voz_local.voz} jeito={_jeito or '(vazio)'}")
                    _motor = self._voz_api
                else:
                    # AO-VIVO PRIMEIRO, modelo de fala na reserva (05/09/2026).
                    #
                    # Era so o modelo de fala desde 30/08, quando o Diego usava
                    # chave PAGA e pediu o caminho mais barato por ficha. No
                    # GRATUITO a conta se inverte — o ao-vivo e cobrado por
                    # SESSAO e serve o dia inteiro, o modelo de fala e cobrado
                    # por PEDIDO e morre na decima frase. E o mesmo caminho do
                    # repositorio de referencia (Mark-LII). Ver `VozCharon`.
                    from core.gemini_voice import VozCharon
                    # QUAL TIMBRE: vem do personagem escolhido (`voz_gemini`).
                    # Desde 05/09/2026 sao 11 vozes da Gemini na engrenagem, e
                    # nao mais uma; o `VozCharon` refaz a sessao sozinho quando
                    # o nome muda.
                    _voz_g = (_PG.get(self._voz_local.voz) or {}).get("voz_gemini", "Charon")
                    # O JEITO ESCOLHIDO TAMBEM VAI PARA A VOZ. Antes so as vozes
                    # de estudio recebiam a direcao de entrega; a da Gemini
                    # falava sempre igual, e trocar de jeito mudava as PALAVRAS
                    # sem mudar o TOM — que era metade do que o dono escolheu.
                    _jeito_v = ""
                    try:
                        _jeito_v = str(getattr(self.ui, "persona_atual", {})
                                       .get("personalidade") or "")
                    except Exception:
                        pass
                    if self._charon is None:
                        self._charon = VozCharon(_voz_g, _jeito_v)
                    if (self._charon.voz != _voz_g
                            or getattr(self._charon, "jeito", "") != _jeito_v):
                        _voice_diag("voz_estilo",
                                    f"voz={_voz_g} jeito={_jeito_v or '(vazio)'}")
                    self._charon.voz = _voz_g
                    self._charon.jeito = _jeito_v
                    _motor = self._charon

                # TOCA CONFORME CHEGA, nao depois de juntar tudo.
                #
                # Eu juntava todos os pedacos numa lista e so entao enfileirava:
                # o texto aparecia no chat e a voz saia 10 s depois (medido
                # 30/08, `falar 10.0s`). O servidor manda o audio em pedacos ao
                # longo da geracao — entregar o primeiro na hora corta a espera
                # para o tempo do primeiro pedaco, nao do ultimo.
                # `t_som` guarda QUANDO o primeiro pedaco saiu. A conta de
                # quando reabrir o microfone parte dali, e nao do fim da
                # geracao: com a fala saindo frase por frase, o fim da geracao
                # chega quando quase todo o audio ja tocou, e usar esse instante
                # deixava o microfone fechado tempo demais — era a queixa do
                # Diego de falar logo depois e ele nao ouvir.
                estado = {"bytes": 0, "comecou": False, "t_som": 0.0}

                def _no_pedaco(raw: bytes) -> None:
                    if not raw:
                        return
                    if not estado["comecou"]:
                        estado["comecou"] = True
                        estado["t_som"] = time.monotonic()
                        self.set_speaking(True)
                        # A ESPERA DE VERDADE FICA GUARDADA (05/09/2026).
                        #
                        # O `falar` que aparecia na tela somava DUAS coisas
                        # muito diferentes: a espera ate o primeiro som e o
                        # tempo do audio TOCANDO. O Diego leu "falar 31,7s" e
                        # entendeu 31 s de atraso — eram 22,1 s de espera (uma
                        # sessao travada) mais 9,6 s de fala normal. Separar os
                        # dois e o que permite saber se piorou ou melhorou.
                        self._ultimo_ttfs = _t.monotonic() - _t_pedido
                        _voice_diag("charon_primeiro_som",
                                    f"{self._ultimo_ttfs:.1f}s ate o 1o pedaco")
                        if ao_primeiro_som:
                            try:
                                ao_primeiro_som()
                            except Exception:
                                pass
                    estado["bytes"] += len(raw)
                    # Fecha o microfone JA, e vai empurrando o prazo a cada
                    # pedaco. Antes eu so fechava depois que TODO o audio
                    # chegava — e durante a fala o microfone ficava aberto,
                    # gravando a propria voz do JARVIS. Foi assim que apareceu
                    # um "Tudinho" que o Diego nunca disse.
                    self._mic_fechado_ate = max(
                        self._mic_fechado_ate,
                        estado["t_som"] + estado["bytes"] / 2 / 24000
                        + self._GUARDA_POS_FALA_S)
                    for pos in range(0, len(raw), 2400):
                        self._loop.call_soon_threadsafe(
                            self.audio_in_queue.put_nowait, raw[pos: pos + 2400])

                # Uma fala por vez na sessao (ver `_trava_boca`). O teto evita
                # que uma gravacao presa deixe o JARVIS mudo: passado dele,
                # fala assim mesmo, como era antes.
                _trava = self._trava_boca()
                _pegou = _trava.acquire(timeout=25)
                try:
                    _motor.speak_para_fila(texto, _no_pedaco)
                finally:
                    if _pegou:
                        _trava.release()
                if estado["bytes"]:
                    dur = estado["bytes"] / 2 / 24000
                    # QUAL DOS DOIS CAMINHOS FALOU. Sem isto, "a voz saiu
                    # estranha" nao tem resposta no registro: ao-vivo e reserva
                    # sao o mesmo timbre por caminhos diferentes, e so o log
                    # pode dizer qual atendeu (05/09/2026).
                    _caminho = ""
                    try:
                        import time as _tt  # noqa: PLC0415
                        from core.gemini_voice import VozCharon as _VC  # noqa: PLC0415
                        if _base == "__gemini__":
                            _caminho = (" pela reserva"
                                        if _tt.time() < _VC._vivo_ate else " ao vivo")
                    except Exception:
                        pass
                    _voice_diag("voz_usada",
                                f"{self._voz_local.voz}{_caminho} ({dur:.1f}s)"
                                + _contar_voz_da_nuvem(self._voz_local.voz, dur))
                    # A FALA SAIU CURTA DEMAIS PARA O TEXTO? (22/09/2026)
                    #
                    # Queixa do Diego: "ele nem terminou de falar a frase, parou
                    # no meio". A sessao ao vivo da Gemini as vezes encerra o
                    # turno com a fala pela metade — e defeito antigo (o
                    # `voz_corrida` aparece 287 vezes no registro desde
                    # 05/09/2026), mas ninguem MEDIA o quanto faltou, entao nao
                    # dava para saber se piorou, nem qual conserto adiantou.
                    #
                    # A conta e simples e nao muda nada no comportamento: a
                    # fala em portugues sai perto de 14 caracteres por segundo,
                    # e a velocidade global ja acelera. Menos de 65% do
                    # esperado e fala cortada.
                    try:
                        from core.velocidade_fala import VELOCIDADE as _VEL  # noqa: PLC0415
                        _esperado = len(str(texto)) / 14.0 / max(1.0, _VEL)
                        if _esperado > 2.0 and dur < _esperado * 0.65:
                            _voice_diag(
                                "voz_cortada",
                                f"{dur:.1f}s de audio para {len(str(texto))} letras "
                                f"(esperava ~{_esperado:.1f}s) — a fala saiu pela metade")
                    except Exception:
                        pass
                    # O CHAT MOSTRA O QUE ELA FALOU, nao o que eu mandei falar.
                    #
                    # O modelo de voz natural e, por baixo, um modelo de
                    # conversa: em texto longo ele as vezes acrescenta. Em
                    # 01/09/2026 o chat dizia "te mandei a imagem" e a voz disse
                    # que nao conseguia. Quem o dono ouviu foi a voz — entao e a
                    # voz que tem de aparecer escrita.
                    _dito = str(getattr(_motor, "ultima_transcricao", "") or "").strip()
                    if _dito and _dito.lower() != str(texto).strip().lower():
                        _voice_diag("voz_divergiu",
                                    f"pedi {len(str(texto).split())}p, falou {len(_dito.split())}p")
                        try:
                            self.ui.write_log(f"{_nome_falante(self.ui)}: {_dito}")
                        except Exception:
                            pass
                    self._mic_fechado_ate = max(
                        self._mic_fechado_ate,
                        (estado["t_som"] or time.monotonic()) + dur
                        + self._GUARDA_POS_FALA_S)
                    return
                raise RuntimeError("a voz de fora devolveu audio vazio")
            except Exception as exc:
                _voice_diag("voz_de_fora_falhou",
                            f"{self._voz_local.voz}: {type(exc).__name__} {str(exc)[:90]}")
                # SO A VOZ DA GEMINI (08/10/2026). Diego: "voltou com uma voz
                # local — essa voz local nao deve existir". Com a voz da Gemini
                # escolhida, nada de socorro da OpenAI nem da voz local: a
                # `VozCharon` ja tentou de novo em conexao nova; se nem assim
                # saiu, a resposta fica ESCRITA no chat.
                if _base == "__gemini__":
                    _voice_diag("voz_calada_neste_turno",
                                "a voz ao vivo nao saiu; resposta escrita (sem outra voz)")
                    try:
                        self.ui.write_log(f"{_nome_falante(self.ui)}: {texto}")
                        self.ui.write_log("SIS: a voz engasgou neste turno; "
                                          "a resposta ficou escrita.")
                    except Exception:
                        pass
                    return
                # A COTA DA VOZ DO CLIENTE ACABOU? Assume a NOSSA.
                #
                # Ate 01/09/2026 ele caia direto no Kokoro — gratis, mas e a voz
                # que o cliente NAO escolheu, e a diferenca e gritante no meio de
                # uma conversa. Agora a nossa voz de estudio entra no lugar, que
                # soa proxima; o Kokoro fica para quando nem ela responder.
                # ENGASGADA PASSAGEIRA NAO TROCA A VOZ (05/09/2026).
                #
                # O Diego: *"ele iniciou com outra voz"* — a Gemini emudeceu por
                # oito segundos na saudacao e quem falou foi a `onyx`. Quando a
                # falha e so mudez (sem erro nenhum do Google), a voz dele volta
                # em 15 s: trocar de pessoa por causa de UM turno e pior do que
                # ficar calado nesse turno. A resposta nao se perde — vai
                # escrita no chat.
                try:
                    from core.gemini_voice import VozCharon as _VC2  # noqa: PLC0415
                    _foi_engasgo = bool(getattr(_VC2, "mudez_passageira", False))
                except Exception:
                    _foi_engasgo = False
                # O AVISO DO COMPUTER USE NAO PODE FICAR MUDO (08/09/2026).
                #
                # A regra acima esta certa para CONVERSA: uma frase perdida se
                # le no chat. Mas o aviso de abertura e de seguranca — o Diego
                # falou "ativar computador", deu a ordem, a tarefa comecou e
                # nao saiu som nenhum (engasgada de 8 s no log das 01:09). Sem
                # o aviso ele nao sabe que pode tirar a mao do mouse. Aqui a
                # voz de estudio entra, mesmo sendo de outra pessoa: um aviso
                # com a voz errada e melhor do que aviso nenhum.
                if _foi_engasgo and not nao_pode_calar:
                    _voice_diag("voz_calada_neste_turno",
                                "engasgada passageira: nao troquei a voz")
                    try:
                        self.ui.write_log(f"{_nome_falante(self.ui)}: {texto}")
                        self.ui.write_log("SIS: a voz engasgou neste turno; "
                                          "a resposta ficou escrita.")
                    except Exception:
                        pass
                    return
                # SOCORRO DA OPENAI SEM CREDITO NAO E TENTADO DE NOVO (01/10/2026).
                # A conta ficou sem credito em 30/09: cada tropeco da Gemini pagava
                # uma ida a rede so para ouvir "no credits" antes da voz local.
                if _base == "__gemini__" and not getattr(self, "_socorro_openai_morto", False):
                    try:
                        from core.voz_openai import VozOpenAI  # noqa: PLC0415
                        # O SOCORRO NAO PODE TROCAR O SEXO DA VOZ (05/09/2026).
                        #
                        # Estava fixo em `onyx` — masculino — desde quando a
                        # unica voz de nuvem era a Charon, tambem masculina. Com
                        # as 11 da Gemini na engrenagem, cinco delas femininas,
                        # bastava um tropeco para o dono ouvir outra pessoa: ele
                        # conversava com a Sulafat, pediu "pode desfazer", a voz
                        # devolveu audio vazio e quem respondeu foi um homem.
                        #
                        # Trocar de timbre ja incomoda; trocar de SEXO soa como
                        # defeito grave, e e a primeira coisa que ele percebe.
                        _sexo_voz = str((_PG.get(self._voz_local.voz) or {})
                                        .get("sexo") or "m")
                        _voz_soc = "shimmer" if _sexo_voz == "f" else "onyx"
                        if (getattr(self, "_voz_socorro", None) is None
                                or getattr(self._voz_socorro, "voz", "") != _voz_soc):
                            self._voz_socorro = VozOpenAI(
                                _voz_soc, ponte=getattr(self.ui, "_codex_relay", None))
                        estado["bytes"] = 0
                        self._voz_socorro.speak_para_fila(texto, _no_pedaco)
                        if estado["bytes"]:
                            dur = estado["bytes"] / 2 / 24000
                            _voice_diag("voz_usada", f"socorro {_voz_soc} ({dur:.1f}s)")
                            self._mic_fechado_ate = max(
                                self._mic_fechado_ate,
                                (estado["t_som"] or time.monotonic()) + dur
                                + self._GUARDA_POS_FALA_S)
                            return
                    except Exception as exc2:
                        _voice_diag("voz_socorro_falhou", str(exc2)[:100])
                        try:
                            from core.cerebro_openrouter import _openai_morta  # noqa: PLC0415
                            if _openai_morta(str(exc2)):
                                self._socorro_openai_morto = True
                        except Exception:
                            pass
                if nao_pode_calar:
                    # O DIEGO, 08/09/2026: *"nao coloque nem uma voz local pelo
                    # amor de deus"*. Entao o socorro do aviso para na voz de
                    # ESTUDIO, que e de nuvem e soa proxima. O Kokoro nao entra
                    # nem aqui: se nem a de estudio veio, o aviso fica escrito
                    # e a faixa na tela continua avisando sozinha.
                    _voice_diag("aviso_sem_voz",
                                "socorro falhou; NAO usei a voz local")
                    try:
                        self.ui.write_log(f"{_nome_falante(self.ui)}: {texto}")
                    except Exception:
                        pass
                    return
                self.ui.write_log("SIS: voz indisponivel; falando com a voz local.")

        # O Kokoro nao conhece "charon" nem "onyx": nesses casos ele fala com a
        # voz padrao em vez de estourar.
        #
        # ATE 22/09/2026 ESTA LINHA PEDIA "alex", QUE NAO EXISTE MAIS — e o
        # Kokoro respondia "Voice alex not found". Ou seja: o socorro da voz
        # tambem falhava, e o dono ficava no silencio. Agora o nome vem do
        # `VOZ_PADRAO`, que e o unico lugar onde essa escolha mora.
        from core.voz_local import VOZ_PADRAO as _VOZ_RESERVA  # noqa: PLC0415
        bruto = self._voz_local.pcm(texto, voz=(None if _base not in ("__gemini__", "__openai__")
                                                else _VOZ_RESERVA))
        if not bruto:
            return
        # Prova de qual voz saiu de verdade. Sem isto, "a voz nao mudou" e
        # discussao: nao da para saber se o app usou a voz nova ou se a mistura
        # e que soa parecida.
        _voice_diag("voz_usada",
                    f"{self._voz_local.voz} ({len(bruto)/2/24000:.1f}s, "
                    f"pronto em {_t.monotonic() - _t_pedido:.1f}s)")
        if ao_primeiro_som:
            try:
                ao_primeiro_som()
            except Exception:
                pass
        # Fecha o microfone ate um pouco depois do fim desta fala: a duracao do
        # audio mais meio segundo de folga para o som morrer na sala.
        self._mic_fechado_ate = max(
            self._mic_fechado_ate,
            time.monotonic() + len(bruto) / 2 / 24000 + self._GUARDA_POS_FALA_S)
        self.set_speaking(True)
        for pos in range(0, len(bruto), 2400):
            self._loop.call_soon_threadsafe(self.audio_in_queue.put_nowait,
                                            bruto[pos: pos + 2400])

    def _responder_texto_local(self, texto: str) -> None:
        """Texto digitado no modo local: mesmo cerebro e mesma boca da voz."""
        import time as _t
        # As ferramentas leem daqui o que o dono pediu (hora_agora decide se
        # fala so a hora; o desligamento confere se a ordem estava na frase).
        # Faltava para o texto digitado — so a voz preenchia.
        self._ultima_fala_do_dono = texto
        # AS REGRAS LOCAIS VALEM PARA O QUE ELE DIGITA TAMBEM (05/09/2026).
        # Elas so estavam no caminho da voz: digitar "abre o bloco de notas" ia
        # para o cerebro e pagava a mesma escolha errada de ferramenta que a fala.
        if (self._rede_do_relogio(texto) or self._rede_de_olhar_webcam(texto)
                or self._rede_da_webcam(texto) or self._rede_do_cowork(texto)
                or self._rede_do_site(texto)
                or self._rede_de_abrir(texto) or self._rede_de_fechar(texto)):
            return
        try:
            from core.cerebro_openrouter import CerebroOpenRouter
            if self._openrouter is None:
                self._openrouter = CerebroOpenRouter()
                self._openrouter.executor = self._executar_ferramenta_local
                self._openrouter.ferramentas = CerebroOpenRouter.converter_ferramentas(
                    TOOL_DECLARATIONS)
            # A ponte com a VPS: e por ela que ele pensa desde 05/09/2026.
            self._openrouter.ponte = getattr(self.ui, "_codex_relay", None)
            self._openrouter.falar = self._falar_local
            t0 = _t.monotonic()
            resposta = self._openrouter.perguntar(texto)
            if not resposta:
                self.ui.write_log("LOCAL: o cerebro devolveu vazio.")
                return
            self._falar_local(resposta,
                              ao_primeiro_som=lambda: self.ui.write_log(f"{_nome_falante(self.ui)}: {resposta}"))
            _voice_diag("texto_local", f"{_t.monotonic()-t0:.1f}s")
        except Exception as exc:
            self.ui.write_log(f"LOCAL: falhou — {type(exc).__name__}: {str(exc)[:120]}")

    def _aprender_audio(self, tipo: str, pico) -> None:
        """Ensina o perfil do microfone: 'fala' aceita ou 'ruido' confirmado."""
        try:
            from core.perfil_audio import perfil as _perfil_audio  # noqa: PLC0415
            _perfil_audio().anotar(tipo, float(pico or 0.0))
        except Exception:
            pass

    def _segunda_opiniao(self, fala_pcm: bytes, motivo: str) -> str:
        """A Gemini do cliente confere a fala DUVIDOSA. So sem placa de video.

        01/10/2026, pedido do Diego para o JARVIS "escutar certo em cada PC".
        Sem NVIDIA o ouvido e o whisper small, que troca palavra e joga fora fala
        clara de microfone ruim. Ate 30/09 essas maquinas ouviam pela OpenAI do
        servidor; sem ela, a segunda opiniao vem da chave Gemini do proprio
        cliente — e so na fala duvidosa, para nao gastar a cota dele.
        """
        if getattr(self, "_whisper_na_placa", False):
            return ""
        try:
            import io as _io  # noqa: PLC0415
            import wave as _wave  # noqa: PLC0415
            from core.cerebro_rapido import transcrever as _transcrever  # noqa: PLC0415
            b = _io.BytesIO()
            with _wave.open(b, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SEND_SAMPLE_RATE)
                w.writeframes(fala_pcm)
            texto = _transcrever(b.getvalue(), _lingua_do_ouvido())
            if texto:
                _voice_diag("segunda_opiniao", f"{motivo}: {texto[:60]!r}")
            return texto
        except Exception as exc:
            _voice_diag("segunda_opiniao_falhou", str(exc)[:90])
            return ""

    def _ouvir_agora(self, fala_pcm: bytes, final: bool = False):
        """So o ouvido: PCM entra, texto sai. None quando nao valeu a fala.

        `final=True` e a passada que DECIDE a fala (nao a espiada adiantada):
        so ela ensina o perfil do microfone o que e barulho (01/10/2026).

        Separado de `_pipeline_local` em 03/09/2026 para poder rodar ANTES da
        hora — ver `_ouvir_adiantado`. A logica e os limites sao os mesmos de
        sempre; nada aqui foi afrouxado na mudanca.
        """
        import time as _t  # noqa: PLC0415
        dur = len(fala_pcm) / 2 / SEND_SAMPLE_RATE
        # 1) OUVIDO — whisper local. O modelo `base` transcreveu 7,8 s em
        #    0,7 s nesta maquina, perfeito, enquanto o servidor da Gemini
        #    devolvia "Oggi a Visc e hoje". Fica carregado entre as falas.
        t0 = _t.monotonic()
        if self._whisper is None:
            self._whisper, self._whisper_na_placa = _abrir_whisper()
        # numpy NAO esta importado no topo deste arquivo — importar aqui.
        import numpy as _np  # noqa: PLC0415
        amostras = (_np.frombuffer(fala_pcm, dtype=_np.int16)
                    .astype(_np.float32) / 32768.0)
        # DESVIO FIXO NO MICROFONE (17/09/2026). Ver `_tirar_grave`. So troca
        # o audio quando saiu coisa de verdade (> ~50 em int16): no microfone
        # limpo a fala segue intacta, byte a byte. A ponte recebe o audio
        # limpo tambem — e ela que ouve, nao adianta limpar so para o local.
        _limpas, _saiu = _tirar_grave(amostras)
        if _saiu > 0.0015:
            amostras = _limpas
            fala_pcm = (_np.clip(amostras, -1.0, 32767 / 32768.0) * 32768.0
                        ).astype(_np.int16).tobytes()
            if not getattr(self, "_avisou_grave", False):
                self._avisou_grave = True
                _voice_diag("grave_removido",
                            f"desvio/ronco de {_saiu * 32768:.0f} tirado da fala "
                            f"(so aviso uma vez)")

        # NORMALIZACAO (30/08/2026). O microfone escolhido entrega nivel
        # baixo (35 a 60 medidos), e o whisper com audio fraco troca palavra
        # parecida: "cria uma pasta" virava "queria uma passo".
        #
        # Provado que nao e o modelo: `small` e `medium` acertaram 3 de 3
        # frases identicas quando o audio estava limpo, e o `medium` custa
        # 3x mais tempo. Entao o caminho e melhorar o AUDIO, nao o modelo.
        #
        # Escala pelo pico para chegar perto de 0,9. Teto de 20x para nao
        # transformar sala silenciosa em chiado amplificado.
        _pico = float(_np.abs(amostras).max()) if amostras.size else 0.0
        # SOM DE LONGE NAO E O DONO. Televisao, celular tocando video,
        # conversa na sala ao lado: tudo chega fraco no microfone. O dono,
        # falando com o JARVIS, chega forte. Cortar aqui evita que ele
        # responda ao YouTube — foi o que aconteceu em 01/09/2026.
        # O PORTAO AFROUXA QUANDO ELE ACABOU DE PERGUNTAR (04/09/2026).
        #
        # Defeito real: no meio de uma tarefa o JARVIS perguntou "posso apagar?",
        # o Diego RESPONDEU — e a resposta foi descartada aqui, com pico 0,057
        # contra o piso de 0,09. Do lado dele: "eu respondi as duas vezes" e o
        # JARVIS parou assim mesmo. O piso existe para ele nao obedecer a
        # televisao; mas quando ELE fez uma pergunta e esta esperando, o que
        # chega e quase sempre a resposta, mesmo dita mais baixo (a pessoa
        # responde "sim" sem levantar a voz).
        #
        # O que segura o risco de a TV virar resposta e a outra metade do
        # conserto, no `_redes_do_pc`: com pergunta de sim-ou-nao pendente, so
        # texto que PARECA sim ou nao e aceito; o resto e ignorado e ele
        # continua esperando.
        # OS LIMITES SAO DESTE MICROFONE (01/10/2026). Eram 0,09 e 0,045 fixos,
        # medidos no aparelho do Diego em setembro — errados para microfone
        # fraco (a voz inteira virava "som de longe") e para sala barulhenta (o
        # barulho passava por fala). Ver `core/perfil_audio.py`.
        try:
            from core.perfil_audio import perfil as _perfil_audio  # noqa: PLC0415
            _lim_resgate, _lim_piso = _perfil_audio().limites()
        except Exception:
            _lim_resgate, _lim_piso = _PICO_DE_RESGATE, _PICO_MINIMO_DA_FALA
        _piso = (max(_lim_piso * 0.55, _lim_resgate * 1.05)
                 if getattr(self, "_pergunta_pendente", False) else _lim_piso)
        # ── O MICROFONE LONGE DA BOCA (08/09/2026, queixa do Diego) ──────────
        #
        # *"Quando eu coloco meu microfone um pouco mais longe ele nao entende
        # muito bem; so quando eu aproximo da boca."*
        #
        # Era este corte, e o registro nao deixa duvida: `som_de_longe` e o
        # filtro que mais derruba fala neste programa — 3.132 vezes no log
        # inteiro, mais do que todos os outros somados. O piso de 0,09 foi
        # medido com o microfone na posicao de sempre (a voz dele batia 0,110 a
        # 0,184). Afastar o microfone 30 cm derruba o pico para a casa dos
        # 0,05 — e a fala boa era jogada fora sem nunca chegar ao ouvido.
        #
        # Baixar o piso direto NAO servia: ele existe para o JARVIS nao
        # obedecer a televisao, e a TV chega justamente nessa faixa fraca.
        #
        # A saida e nao decidir so pelo VOLUME. Na faixa de resgate (0,045 ate
        # o piso) o audio agora e TRANSCRITO e quem decide e a CONFIANCA do
        # whisper, que separa os dois casos muito melhor do que o volume —
        # numeros do proprio uso dele:
        #     voz do dono ..... no_speech 0,00 a 0,19
        #     fantasma/TV ..... no_speech 0,74 a 0,79
        # Por isso a exigencia aqui e alta (`_sem_fala <= 0.35`, mais de uma
        # palavra): fala de longe passa, som de longe nao.
        #
        # Abaixo de 0,045 continua sendo descarte seco, sem transcrever — e
        # onde vive o ruido de sala, e rodar o whisper ali so custaria tempo.
        _resgate = False
        if 0.0 < _pico < _piso:
            # UM EM CADA QUATRO "quase resgate" e ouvido mesmo assim
            # (01/10/2026): se o perfil subir demais o limite, uma voz que as
            # vezes sai baixa nunca mais seria ouvida para ensinar o contrario.
            _conferir = False
            if final and _pico >= _lim_resgate * 0.6:
                self._explorar_n = getattr(self, "_explorar_n", 0) + 1
                _conferir = self._explorar_n % 4 == 0
            if _pico < _lim_resgate and not _conferir:
                _voice_diag("som_de_longe",
                            f"pico {_pico:.3f} < {_lim_resgate:.3f} "
                            f"({dur:.1f}s descartados)")
                return None
            _resgate = True
            _voice_diag("fala_de_longe",
                        f"pico {_pico:.3f} < {_piso:.3f} — vou ouvir e conferir")
        # ── OUVIDO NA VPS (14/09/2026, a mesma peca do TED) ──────────────────
        #
        # Cliente sem placa NVIDIA: o whisper local leva 2,0 a 3,6 s por frase
        # (o codificador trabalha 30 s de audio ate para um "sim") e erra nome e
        # palavra curta. A VPS devolve com o `gpt-4o-mini-transcribe`.
        #
        # Fica DEPOIS do corte de volume de proposito: chiado de sala abaixo de
        # 0,045 continua indo fora aqui, sem gastar a cota do aparelho na VPS.
        # Na maquina com placa (large-v3, 0,4 s) a ponte nao entra.
        #
        # O whisper local FICA como reserva. Ponte fora fica 3 min sem tentar;
        # senao cada fala pagaria 4 s de `wait_connected` antes do local.
        if (not getattr(self, "_whisper_na_placa", False)
                and getattr(self, "_stt_ponte_fora_ate", 0.0) < _t.time()):
            _ponte = getattr(self.ui, "_codex_relay", None)
            if _ponte is not None and hasattr(_ponte, "transcrever_pela_ponte"):
                t_ponte = _t.monotonic()
                for _tentativa in range(10):
                    try:
                        _txt = _ponte.transcrever_pela_ponte(fala_pcm,
                                                             taxa=SEND_SAMPLE_RATE,
                                                             idioma=_lingua_do_ouvido())
                        _voice_diag("ouvido_pela_ponte",
                                    f"{_t.monotonic()-t_ponte:.1f}s ({dur:.1f}s de fala)")
                        # `segs` vazio: as peneiras de CONFIANCA do whisper nao
                        # existem aqui (todas guardadas por `if segs:`). As de
                        # TEXTO — eco, legenda inventada — valem no chamador.
                        # Texto vazio volta vazio: rodar o local depois so
                        # custaria 2 s para ele inventar uma frase.
                        return {"texto": _corrigir_nomes(_txt), "segs": [],
                                "dur": dur, "sem_fala": 0.0, "prob": 0.0,
                                "t_ouvir": _t.monotonic() - t0, "pico": _pico}
                    except Exception as _exc:
                        _motivo = str(_exc)[:90]
                        # A ESPIADA E O TURNO SE CRUZAM. A VPS atende uma fala
                        # por aparelho e responde "ja existe uma fala sendo
                        # transcrita" — passa em menos de um segundo, e cair no
                        # local ali custaria 2 s. Espera e tenta (ate ~2,5 s).
                        if "existe uma fala" in _motivo and _tentativa < 9:
                            _t.sleep(0.25)
                            continue
                        if ("passa do teto" not in _motivo
                                and "existe uma fala" not in _motivo):
                            self._stt_ponte_fora_ate = _t.time() + 180.0
                        # VPS de antes do ingles (24/09/2026): ela transcreveria
                        # em portugues. Fica no whisper local por uma hora.
                        if "nao transcreve em" in _motivo:
                            self._stt_ponte_fora_ate = _t.time() + 3600.0
                        # CONTA DA OPENAI SEM CREDITO (01/10/2026): nao volta em
                        # 3 min. Uma hora fora, e cada fala vai direto ao local
                        # em vez de pagar a ida a VPS so para ouvir "no credits".
                        try:
                            from core.cerebro_openrouter import _openai_morta  # noqa: PLC0415
                            if _openai_morta(_motivo):
                                self._stt_ponte_fora_ate = _t.time() + 3600.0
                        except Exception:
                            pass
                        _voice_diag("ouvido_pela_ponte_fora", _motivo)
                        break
        if 0.0 < _pico < 0.9:
            _ganho = min(0.9 / _pico, 20.0)
            amostras = _np.clip(amostras * _ganho, -1.0, 1.0)
            _voice_diag("audio_normalizado",
                        f"pico {_pico:.3f} -> ganho {_ganho:.1f}x")
        segs, _info = self._whisper.transcribe(
            # BEAM 5 TAMBEM NO PROCESSADOR (13/09/2026). Era beam 1 fora da
            # placa, e o cliente sem NVIDIA ouvia "Feixe o Bloco de Notas" e
            # "Me fanatores". Medido num Ryzen 5 5600G: quem pesa e o
            # codificador (~2 s para qualquer frase, curta ou longa); o beam 5
            # so soma +0,3 s (2,01 -> 2,33 s). Na placa custa ~0,1 s.
            amostras, language=_lingua_do_ouvido(),
            beam_size=5,
            # VOCABULARIO. O whisper nao conhece nome proprio brasileiro:
            # o Diego falou "Cazé TV" e saiu "KZTV" e depois "Hazer TV".
            # `initial_prompt` inclina o reconhecimento para estas palavras
            # sem obrigar nada — e o jeito certo de ensinar nome proprio.
            # O prompt leva o NOME DA VOZ escolhida (14/09/2026): sem ele
            # "Leda" saia "Lida". Termos de uma maquina: config/vocabulario.txt.
            initial_prompt=_atualizar_vocabulario(_nome_falante(self.ui)),
            # `vad_filter` DESLIGADO (30/08, 19h). Eu tinha ligado para
            # cortar ruido, mas com o microfone novo (nivel 60, mais baixo)
            # ele passou a descartar a FALA INTEIRA: o registro so mostrava
            # "local_vazio, 3.8s de audio, nada transcrito". Cortar ruido nao
            # vale nada se corta o dono junto.
            condition_on_previous_text=False,
        )
        segs = list(segs)
        texto = _corrigir_nomes(" ".join(s.text for s in segs).strip())
        # Confianca media. `no_speech_prob` alto = provavelmente nao era fala;
        # `avg_logprob` muito baixo = o modelo chutou.
        if segs:
            _sem_fala = max(getattr(s, "no_speech_prob", 0.0) or 0.0 for s in segs)
            _prob = min(getattr(s, "avg_logprob", 0.0) or 0.0 for s in segs)
            # Limites afrouxados: com microfone de nivel baixo o whisper
            # devolve confianca pior mesmo acertando. Severo demais, ele
            # descarta a fala boa — que e pior do que deixar passar ruido.
            # 0,50 desde 01/09/2026 (era 0,85, frouxo demais).
            #
            # O whisper diz, em `no_speech_prob`, o quanto ele acha que
            # aquilo NAO era fala. Medido no uso real desta noite:
            #     voz do Diego ... 0,00 a 0,19
            #     fantasma ....... 0,74 a 0,79
            # O "Caze TV  E assim ficou pronto" tinha volume alto e teria
            # passado pelo pico — foi a confianca que o entregou. 0,50 fica
            # no meio do vao, longe dos dois lados.
            # FRASE LONGA AGUENTA MAIS (14/09/2026). No PC de um cliente
            # (HyperX QuadCast S) fala de verdade veio com sem_fala 0,50 a 0,65
            # e ia fora: "Isso aqui tudo funciona tambem?", "Tem duas mulheres
            # e as duas...", "Eu quero saber como fica...". As invencoes do
            # silencio sao curtas ("Obrigado.", "Hmmmm.", "AlfaMu") ou legenda,
            # e a legenda tem peneira propria. Por isso so frase de 4 palavras
            # ou mais ganha o limite de 0,70; a curta segue em 0,50.
            _limite_sem_fala = 0.70 if len(texto.split()) >= 4 else 0.50
            if _sem_fala > _limite_sem_fala or _prob < -1.6:
                _voice_diag("local_descartado",
                            f"sem_fala={_sem_fala:.2f} prob={_prob:.2f} {texto[:50]!r}")
                # Audio FORTE que o ouvido local nao entendeu: no PC sem placa,
                # pergunta a Gemini antes de jogar fora.
                if final and not _resgate and _pico >= _piso:
                    _outro = self._segunda_opiniao(fala_pcm, "descartado pelo local")
                    if _outro:
                        self._aprender_audio("fala", _pico)
                        return {"texto": _corrigir_nomes(_outro), "segs": [], "dur": dur,
                                "sem_fala": 0.0, "prob": 0.0,
                                "t_ouvir": _t.monotonic() - t0, "pico": _pico,
                                "resgate": False, "nuvem": True}
                if final:
                    self._aprender_audio("ruido", _pico)
                return None
            # FRASE CURTA EXIGE CONFIANCA ALTA (31/08/2026).
            #
            # Com o Diego calado apareceram "E ai" e "Andou?" — invencao do
            # whisper em cima de quase-silencio. Lista de frases nao resolve,
            # porque amanha o fantasma e outro; o que denuncia e a confianca.
            # Fala de verdade, mesmo curta, vem com numero bom.
            if len(texto.split()) <= 3 and (_sem_fala > 0.5 or _prob < -0.9):
                _voice_diag("fantasma_curto",
                            f"sem_fala={_sem_fala:.2f} prob={_prob:.2f} {texto[:40]!r}")
                if final and not _resgate and _pico >= _piso:
                    _outro = self._segunda_opiniao(fala_pcm, "fantasma curto")
                    if _outro:
                        self._aprender_audio("fala", _pico)
                        return {"texto": _corrigir_nomes(_outro), "segs": [], "dur": dur,
                                "sem_fala": 0.0, "prob": 0.0,
                                "t_ouvir": _t.monotonic() - t0, "pico": _pico,
                                "resgate": False, "nuvem": True}
                if final:
                    self._aprender_audio("ruido", _pico)
                return
            # A FALA DE LONGE PAGA MAIS CARO PARA PASSAR. Ela chegou aqui por
            # excecao (pico abaixo do piso), entao a confianca tem de ser de
            # fala mesmo — nao a de um som fraco que o whisper transformou em
            # palavra. Ver `_PICO_DE_RESGATE`.
            # PALAVRA SOZINHA DE QUEM FALA BAIXO (14/09/2026). Exigir duas
            # palavras barrava a resposta curta: no log do cliente "Sim.",
            # "JARVIS.", "Quero." e "Exatamente." vieram com sem_fala 0,02 a
            # 0,21 e prob -0,31 a -0,65, e foram recusados. Os fantasmas de uma
            # palavra ali tinham prob -0,98 ("Odys.") e -1,09 ("In."). Palavra
            # sozinha passa quando a confianca e de fala de verdade.
            _uma_palavra = len(texto.split()) < 2
            _palavra_boa = _uma_palavra and _sem_fala <= 0.25 and _prob >= -0.70
            if _resgate and (_sem_fala > 0.35 or _prob < -1.0
                             or (_uma_palavra and not _palavra_boa)):
                _voice_diag("resgate_recusado",
                            f"sem_fala={_sem_fala:.2f} prob={_prob:.2f} {texto[:40]!r}")
                if final:
                    self._aprender_audio("ruido", _pico)
                return None
            if _resgate:
                _voice_diag("resgate_aceito",
                            f"sem_fala={_sem_fala:.2f} prob={_prob:.2f} {texto[:40]!r}")
            # ACEITO, MAS COM DUVIDA, NO PC SEM PLACA (01/10/2026): o whisper
            # small acertou o suficiente para passar, mas a confianca diz que
            # ele chutou palavra. A Gemini do cliente confere; se ela ouvir
            # outra coisa, vale a dela.
            if (final and not getattr(self, "_whisper_na_placa", False)
                    and (_prob < -0.65 or (_resgate and _prob < -0.45))):
                _outro = self._segunda_opiniao(fala_pcm, f"duvida (prob {_prob:.2f})")
                if _outro:
                    texto = _corrigir_nomes(_outro)
        return {"texto": texto, "segs": segs, "dur": dur,
                "sem_fala": _sem_fala if segs else 0.0,
                "prob": _prob if segs else 0.0,
                "t_ouvir": _t.monotonic() - t0, "pico": _pico}

    # ── O WHISPER RODA DURANTE O RABICHO (03/09/2026) ──────────────────────
    #
    # O rabicho (0,8 s) existe para decidir que a frase acabou. O whisper
    # (~0,5 s) transcreve o que ja esta no buffer. Um nao depende do outro:
    # eram seriais so porque o codigo os chamava em fila.
    #
    # Agora, assim que a voz para por `_ESPERA_ADIANTADA_S`, uma thread comeca
    # a transcrever o buffer. Quando o rabicho fecha, se NINGUEM voltou a falar
    # desde entao, o texto ja esta pronto e a fala segue direto para o cerebro.
    #
    # A CONFERENCIA E POR `_ultima_voz_detectada`, nao por tamanho de buffer: o
    # buffer cresce durante o rabicho (ele engole o silencio), entao comparar
    # tamanho nunca casaria. O que importa e se entrou VOZ nova depois da
    # espiada — se entrou, o texto adiantado esta incompleto e vai fora.
    #
    # So guarda ACERTO. Descarte (som de longe, fantasma, confianca baixa) nao
    # e cacheado de proposito: assim o caminho normal refaz e registra o motivo
    # uma vez so, em vez de duas.
    # 0,30 -> 0,15 quando o rabicho caiu para 0,6 s. A conta e simples: a
    # espiada leva ~0,5 s, entao comecando em 0,30 ela so ficaria pronta em
    # 0,80 — depois de o prazo ter fechado em 0,60, e o trabalho ia todo fora.
    # Medido no registro: 0 aproveitamentos em 4 turnos, contra 11 enquanto o
    # rabicho era 0,8 s.
    #
    # Nao da para descer muito mais: entre uma palavra e outra ha silencio de
    # ~0,1 s, e ai a espiada dispararia no meio da frase, sem parar.
    _ESPERA_ADIANTADA_S = 0.15

    def _ouvir_adiantado(self) -> None:
        """Espia o buffer enquanto o rabicho ainda corre."""
        if not self._modo_local or self._local_ocupado or self._adiantado_rodando:
            return
        if not self._buf_local or not self._ultima_voz_detectada:
            return
        if (time.monotonic() - self._ultima_voz_detectada) < self._ESPERA_ADIANTADA_S:
            return
        # A MEDIDA E QUANTA VOZ ENTROU, nao que horas foi o ultimo barulho.
        # Com a TV ligada o carimbo de tempo mudava toda hora e a espiada era
        # jogada fora sempre; `_voz_falada_s` so cresce com bloco acima do piso.
        voz_agora = getattr(self, "_voz_falada_s", 0.0)
        if abs(voz_agora - getattr(self, "_adiantado_voz", -1.0)) < 0.02:
            return                      # ja espiei esta fala, e ela nao cresceu
        self._adiantado_voz = voz_agora
        self._adiantado_carimbo = self._ultima_voz_detectada
        fala = b"".join(self._buf_local)

        def _trabalho():
            try:
                r = self._ouvir_agora(fala)
                if r and r.get("texto"):
                    self._adiantado = (voz_agora, r)
            except Exception as exc:
                _voice_diag("adiantado_falhou", str(exc)[:90])
            finally:
                self._adiantado_rodando = False
                self._adiantado_pronto.set()

        self._adiantado_rodando = True
        self._adiantado_pronto.clear()
        threading.Thread(target=_trabalho, daemon=True).start()

    def _texto_adiantado(self):
        """O que a espiada achou, se ela ainda vale para esta fala.

        SE ELA AINDA ESTIVER CORRENDO para esta mesma fala, espera. Parece
        contra-intuitivo esperar quando o objetivo e ir rapido, mas a conta
        fecha: o que falta dela sao decimos, e comecar outra transcricao do zero
        custa os ~0,5 s inteiros. Era o que acontecia ate agora — a espiada
        terminava logo depois do prazo, ninguem esperava por ela, e o whisper
        rodava duas vezes na mesma fala.

        O teto de 2 s e so rede de seguranca: se a placa engasgar, o caminho
        normal assume em vez de o dono ficar esperando calado.
        """
        alvo = getattr(self, "_voz_ao_fechar", 0.0)
        # Ainda correndo, e sobre ESTA fala? Entao vale esperar os decimos que
        # faltam — recomecar do zero custa o tempo inteiro do whisper.
        if (self._adiantado is None and self._adiantado_rodando
                and abs(getattr(self, "_adiantado_voz", -1.0) - alvo) <= 0.08):
            self._adiantado_pronto.wait(2.0)
        guardado = self._adiantado
        self._adiantado = None
        if not guardado:
            return None
        voz_espiada, r = guardado
        # Duas casas de tolerancia = 2 blocos de 40 ms. Mais que isso, o dono
        # falou depois da espiada e o texto dela esta incompleto.
        if abs(voz_espiada - alvo) > 0.08:
            _voice_diag("adiantado_velho",
                        f"espiei {voz_espiada:.2f}s de voz, o turno tinha {alvo:.2f}s")
            return None
        return r

    def _pipeline_local(self, fala_pcm: bytes) -> None:
        """Ouvido, cerebro e boca SEM a Gemini.

        whisper local -> Codex na VPS -> Kokoro local. Roda numa thread propria:
        cada etapa bloqueia, e segurar o laco do microfone aqui engasgaria a
        captura da proxima fala.
        """
        import time as _t
        # O RELOGIO DO TURNO INTEIRO. Ele morava dentro do trecho do ouvido,
        # que virou o metodo `_ouvir_agora` — sem esta linha o `total=` do
        # registro estoura com NameError depois da primeira resposta.
        t0 = _t.monotonic()
        try:
            dur = len(fala_pcm) / 2 / SEND_SAMPLE_RATE
            # Fala curta demais e tosse, porta batendo, "ahn". Nao vale um job.
            # ERA 0,6 s e comia palavra de verdade: "sim", "nao", "para",
            # "JARVIS" cabem embaixo disso (queixa do Diego em 30/08/2026).
            # 0,35 s ainda derruba estalo de teclado e batida de mesa.
            if dur < 0.35:
                return

            # O ouvido pode ja ter rodado durante o rabicho. Ver
            # `_ouvir_adiantado`: quando vale, sao ~0,5 s a menos por fala.
            _r = self._texto_adiantado()
            if _r is not None:
                _voice_diag("ouvido_adiantado", f"{_r['t_ouvir']:.1f}s ja prontos")
                # A espiada nao pede segunda opiniao (ela roda varias vezes por
                # fala); a fala que ela entregou com duvida e conferida aqui,
                # uma vez so, no PC sem placa.
                if (_r.get("segs") and float(_r.get("prob") or 0.0) < -0.65
                        and not getattr(self, "_whisper_na_placa", False)):
                    _outro = self._segunda_opiniao(
                        fala_pcm, f"duvida (prob {float(_r.get('prob') or 0):.2f})")
                    if _outro:
                        _r = dict(_r, texto=_corrigir_nomes(_outro))
            else:
                _r = self._ouvir_agora(fala_pcm, final=True)
            if _r is None:
                return
            texto, segs, dur = _r["texto"], _r["segs"], _r["dur"]
            _sem_fala, _prob = _r["sem_fala"], _r["prob"]
            t_ouvir = _r["t_ouvir"]
            if not texto:
                _voice_diag("local_vazio", f"{dur:.1f}s de audio, nada transcrito")
                self._aprender_audio("ruido", _r.get("pico"))
                return
            # ECO DO VOCABULARIO. No silencio o whisper repete as palavras do
            # `initial_prompt` com toda a confianca, e o chat do Diego encheu de
            # "Caze TV" sozinho (31/08/2026). Isso nao e fala: e o prompt
            # voltando. Descarta antes de acordar o cerebro.
            if _e_eco_do_vocabulario(texto):
                _voice_diag("eco_do_vocabulario", f"descartado: {texto[:40]!r}")
                self._aprender_audio("ruido", _r.get("pico"))
                return
            # Legenda de YouTube que o whisper inventa no silencio.
            if _e_alucinacao_de_silencio(texto):
                _voice_diag("alucinacao_de_silencio", f"descartado: {texto[:40]!r}")
                self._aprender_audio("ruido", _r.get("pico"))
                return
            # Risada, "hum" e eco da caixa que voltam em outra escrita.
            if _e_outra_escrita(texto):
                _voice_diag("outra_escrita", f"descartado: {texto[:40]!r}")
                self._aprender_audio("ruido", _r.get("pico"))
                return
            # Cliente em ingles: ruido que voltou em outra lingua.
            if _e_outra_lingua(texto):
                _voice_diag("outra_lingua", f"descartado: {texto[:40]!r}")
                self._aprender_audio("ruido", _r.get("pico"))
                return
            # Dois fantasmas emendados ("Caze TV  E assim ficou pronto").
            if _so_fantasma(texto):
                _voice_diag("fantasmas_colados", f"descartado: {texto[:46]!r}")
                self._aprender_audio("ruido", _r.get("pico"))
                return
            # CONSERTO DA FALA MAL OUVIDA (05/09/2026).
            #
            # Queixa do Diego: "as vezes a voz chega no chat com palavra
            # errada — boa noite sai 'ba noit'". O `core/corretor_fala`
            # compara o que foi ouvido com o catalogo de expressoes da casa
            # PELO SOM, e so troca quando tem certeza. Custo medido: 0,5 ms
            # por frase, local, sem rede — nao aparece no relogio do turno.
            #
            # Vem DEPOIS dos filtros de fantasma (que foram calibrados no
            # texto cru do whisper) e ANTES do log, do portao do nome e das
            # redes locais — assim o chat, o `local_ouviu` e quem procura
            # palavra-chave veem todos a mesma frase, ja consertada.
            try:
                from core.corretor_fala import corrigir as _corrigir_fala  # noqa: PLC0415
                _novo, _trocas = (texto, []) if _em_ingles() else _corrigir_fala(texto)
                if _trocas:
                    _voice_diag("fala_corrigida",
                                " | ".join(f"{a!r} -> {b!r}" for a, b in _trocas))
                    texto = _novo
            except Exception as _exc:
                # Conserto e melhoria, nao pode derrubar a fala: se falhar,
                # segue com o texto do jeito que o whisper entregou.
                _voice_diag("corretor_falhou", str(_exc)[:90])

            # Guardada para a conferencia do desligamento (ver o ramo
            # shutdown_jarvis): so desliga se a ordem estiver na fala.
            self._ultima_fala_do_dono = texto
            self._aprender_audio("fala", _r.get("pico"))
            _voice_diag("local_ouviu",
                        f"{t_ouvir:.1f}s ({dur:.1f}s de fala) "
                        f"[conf {_sem_fala:.2f}/{_prob:.2f}] {texto[:70]!r}"
                        if segs else
                        f"{t_ouvir:.1f}s ({dur:.1f}s de fala) {texto[:70]!r}")
            # O PORTAO DA PALAVRA DE ATIVACAO, e ele vem ANTES de tudo.
            #
            # Antes do log tambem: com o portao ligado, o que nao e para ele
            # nao deve nem aparecer escrito no chat — o dono pediu que ficasse
            # "como se estivesse mutado". O registro fica no voice_session.log,
            # que e onde se confere se ele ouviu e ignorou de proposito.
            _passa, texto = self._passa_pelo_nome(texto)
            if not _passa:
                if texto:
                    _voice_diag("ignorado_sem_nome", f"{texto[:60]!r}")
                return

            self.ui.write_log(f"Voce: {texto}")
            # O DONO FALOU AGORA. No modo local a sessao da Gemini nao ve esta
            # fala, e era so ela que zerava este relogio — por isso o check-in
            # proativo achava que a casa estava vazia ha horas e ficava
            # perguntando "o que o senhor quer fazer" (08/09/2026).
            self._last_user_speech = _t.monotonic()
            try:
                from memory.diario import anotar as _anotar  # noqa: PLC0415
                _anotar("dono", texto)
            except Exception:
                pass

            # A BOCA ABRE ENQUANTO O CEREBRO PENSA (05/09/2026).
            #
            # O aperto de mao da voz custa de 1 a 5 s e acontecia DEPOIS de o
            # cerebro responder — em serie com tudo. Aqui ele corre junto com o
            # `pensar`, que sao 1 a 2 s, e some do relogio do dono. Nao bloqueia:
            # `aquecer_em_fundo` so agenda e volta. Ver `_aquecer_boca`.
            self._aquecer_boca()

            # REDE DO VISION CONTROL, antes do cerebro. Sem ela o pedido virava
            # conversa: o modelo respondia "ativado, chefe" e a camera nunca
            # abria. Ver `_rede_vision_local`.
            if self._rede_vision_local(texto, falar=True):
                _voice_diag("vision_por_voz", texto[:60])
                return
            # "Para", resposta a uma pergunta do olhar-e-clicar, e "desfaz".
            if self._redes_do_pc(texto):
                return
            # A HORA E A DATA NAO PRECISAM DO MODELO (05/09/2026).
            #
            # Medido no uso real do Diego: "que horas e agora" custou 9,2 s e
            # depois 7,8 s, e "que dia e hoje" 7,8 s — porque o modelo CHAMA a
            # ferramenta `hora_agora` mesmo com o relogio ja escrito na nota
            # interna, e cada chamada e uma volta inteira a mais. E a pergunta
            # que ele mais faz. Aqui sai em milissegundos.
            if self._rede_do_relogio(texto):
                return
            # A WEBCAM VEM ANTES DO ABRIR GENERICO (05/09/2026). "Abra minha
            # webcam" tem verbo de abrir e cairia na busca de programa; e,
            # quando ia ao modelo, ele escolhia `vision_control` — que e o
            # MOUSE POR GESTOS, nao a camera. Foi o que o Diego viu: pediu a
            # webcam e ganhou o Vision Control no painel lateral.
            if self._rede_de_olhar_webcam(texto) or self._rede_da_webcam(texto):
                return
            # CRIAR SITE vai direto ao servidor (22/09/2026). Pelo cerebro, com
            # uma imagem recem-criada no historico, ele ABRIU a imagem antiga em
            # vez de criar o site. Antes do abrir generico: "cria um site e
            # abre pra mim" e pedido de site. Ver `_rede_do_site`.
            # COWORK antes do site (05/10/2026): com um projeto aberto no Cowork,
            # "cria um site..." e trabalho NELE. Sem projeto, nada muda.
            if self._rede_do_cowork(texto) or self._rede_do_site(texto):
                return
            # ABRIR e FECHAR pasta/programa conhecido: instantaneo, sem cerebro.
            # Medido pelo Diego no TED: cada um destes custava de 5 a 29 s de
            # ida e volta ao Codex para um `Start-Process notepad`.
            if self._rede_de_abrir(texto) or self._rede_de_fechar(texto):
                return

            # CONVERSA DIRETA (08/10/2026). Conversa sem acao no PC nao passa
            # pelo cerebro: a propria voz (ao-vivo 2.5, gratis) responde
            # falando. Ver `_conversa_direta_pode` e `GeminiConversa`.
            if self._cerebro == "openrouter" and self._conversa_direta_pode(texto):
                if self._conversar_direto(texto, t_ouvir, t0):
                    return
                _voice_diag("conversa_direta_desistiu", "a frase vai pelo cerebro")

            # 2) CEREBRO
            if self._cerebro == "openrouter":
                t1 = _t.monotonic()
                from core.cerebro_openrouter import CerebroOpenRouter
                if self._openrouter is None:
                    self._openrouter = CerebroOpenRouter()
                    self._openrouter.executor = self._executar_ferramenta_local
                    self._openrouter.ferramentas = CerebroOpenRouter.converter_ferramentas(
                        TOOL_DECLARATIONS)
                self._openrouter.ponte = getattr(self.ui, "_codex_relay", None)
                self._openrouter.falar = self._falar_local
                try:
                    resposta = self._openrouter.perguntar(texto)
                except Exception as exc_or:
                    # REDE DE SEGURANCA: o OpenRouter gratis tem teto de ~7.866
                    # fichas POR PEDIDO e devolve 402 quando estoura. Antes disso
                    # o JARVIS simplesmente emudecia com um erro na tela.
                    #
                    # O Codex na VPS nao tem teto nenhum — so e lento (12 a 19 s
                    # medidos). Serve exatamente para isto: nao deixar o cliente
                    # na mao quando o rapido recusa.
                    _voice_diag("openrouter_falhou", str(exc_or)[:140])
                    self.ui.write_log("LOCAL: o cerebro rapido recusou; indo pelo servidor.")
                    try:
                        self._falar_local("So um instante, chefe.")
                    except Exception:
                        pass
                    from actions.codex_remote import remote_codex_job
                    resposta = remote_codex_job(
                        {"kind": "chat", "description": texto,
                         "project_name": "JarvisChat"}, player=self.ui)
                    t_pensar = _t.monotonic() - t1
                    if resposta:
                        self._falar_local(
                            resposta,
                            ao_primeiro_som=lambda: self.ui.write_log(f"{_nome_falante(self.ui)}: {resposta}"))
                    _voice_diag("local_tempos",
                                f"codex(reserva) ouvir={t_ouvir:.1f}s pensar={t_pensar:.1f}s")
                    return
                t_pensar = _t.monotonic() - t1
                if not resposta:
                    self.ui.write_log("LOCAL: o cerebro devolveu vazio.")
                    return
                t2 = _t.monotonic()
                # O texto sai JUNTO com o primeiro som, nao antes dele.
                self._falar_local(resposta,
                                  ao_primeiro_som=lambda: self.ui.write_log(f"{_nome_falante(self.ui)}: {resposta}"))
                t_falar = _t.monotonic() - t2
                # ESPERA e TOCANDO sao coisas diferentes — ver `_ultimo_ttfs`.
                # O que o dono sente como atraso e `ouvir + pensar + voz`; o
                # resto ja e ele ouvindo a resposta.
                _espera = getattr(self, "_ultimo_ttfs", 0.0) or t_falar
                _tocando = max(0.0, t_falar - _espera)
                _voice_diag("local_tempos",
                            f"openrouter ouvir={t_ouvir:.1f}s pensar={t_pensar:.1f}s "
                            f"voz={_espera:.1f}s tocando={_tocando:.1f}s "
                            f"atraso={t_ouvir + t_pensar + _espera:.1f}s "
                            f"total={_t.monotonic()-t0:.1f}s")
                self.ui.write_log(
                    f"LOCAL: ouvir {t_ouvir:.1f}s | pensar {t_pensar:.1f}s | "
                    f"voz {_espera:.1f}s  (falando {_tocando:.1f}s)")
                return

            if self._cerebro == "codex":
                # Sem tocar na cota da Gemini, mas lento: 12 a 19 s medidos,
                # porque cada pergunta sobe a CLI inteira num sandbox novo.
                t1 = _t.monotonic()
                from actions.codex_remote import remote_codex_job
                resposta = remote_codex_job(
                    {"kind": "chat", "description": texto, "project_name": "JarvisChat"},
                    player=self.ui,
                )
                t_pensar = _t.monotonic() - t1
                t2 = _t.monotonic()
                self.ui.write_log(f"{_nome_falante(self.ui)}: {resposta}")
                self._falar_local(resposta)          # 3) BOCA — Kokoro
                _voice_diag("local_tempos",
                            f"codex ouvir={t_ouvir:.1f}s pensar={t_pensar:.1f}s "
                            f"falar={_t.monotonic()-t2:.1f}s total={_t.monotonic()-t0:.1f}s")
                return

            # Cerebro Gemini: manda o texto do whisper pelo MESMO caminho do chat
            # digitado (`turn_complete=True` explicito). Isso mata de uma vez os
            # dois defeitos do dia — nao sobe audio nenhum, entao a cota por
            # segundo de audio nao corre; e o turno fecha na hora, entao acaba o
            # "nao responde pelo microfone". A resposta chega pelo `_receive_audio`,
            # que descarta o audio dela e manda o texto para o Kokoro falar.
            if not self.session or not self._loop:
                self.ui.write_log("LOCAL: sem sessao para pensar — repita em instantes.")
                return
            _voice_diag("local_pensar", f"gemini <- {texto[:70]!r}")
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"parts": [{"text": texto}]}, turn_complete=True),
                self._loop,
            )
        except Exception as exc:
            _voice_diag("local_erro", f"{type(exc).__name__}: {str(exc)[:160]}")
            self.ui.write_log(f"LOCAL: falhou — {type(exc).__name__}: {str(exc)[:120]}")
        finally:
            self._local_ocupado = False

    def _voz_local_teste(self, texto: str, voz=None) -> None:
        """Fala com a voz local (Kokoro) e joga na mesma fila de audio de sempre."""
        import time as _t
        try:
            from core.voz_local import VozLocal
            if self._voz_local is None:
                self._voz_local = VozLocal()
            t0 = _t.monotonic()
            bruto = self._voz_local.pcm(texto, voz=voz)
            levou = _t.monotonic() - t0
        except Exception as exc:
            self.ui.write_log(f"VOZ LOCAL: falhou — {type(exc).__name__}: {str(exc)[:140]}")
            return
        dur = len(bruto) / 2 / 24000
        nome = voz or getattr(self._voz_local, "voz", "jarvis")
        _voice_diag("voz_local", f"{nome} {dur:.1f}s de fala em {levou:.1f}s")
        self.ui.write_log(f"VOZ LOCAL ({nome}): {dur:.1f}s de fala gerados em {levou:.1f}s")
        # Entra na fila normal, entao respeita o botao de interromper e a caixa
        # de som escolhida — nao abre um canal de audio por fora.
        self.set_speaking(True)
        for pos in range(0, len(bruto), 2400):
            self._loop.call_soon_threadsafe(self.audio_in_queue.put_nowait,
                                            bruto[pos: pos + 2400])

    def _codex_chat_teste(self, pergunta: str) -> None:
        """Manda uma pergunta para o Codex da VPS e devolve com o tempo gasto."""
        import time as _t
        self.ui.write_log(f"CODEX: perguntando — {pergunta[:80]}")
        t0 = _t.monotonic()
        try:
            from actions.codex_remote import remote_codex_job
            resposta = remote_codex_job(
                {"kind": "chat", "description": pergunta, "project_name": "JarvisChat"},
                player=self.ui,
            )
        except Exception as exc:
            self.ui.write_log(f"CODEX: falhou — {type(exc).__name__}: {str(exc)[:140]}")
            return
        levou = _t.monotonic() - t0
        _voice_diag("codex_chat", f"{levou:.1f}s pergunta={pergunta[:60]!r}")
        self.ui.write_log(f"CODEX ({levou:.1f}s): {resposta}")

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()
        voice_style_prompt = (
            "\n\n[JARVIS VOICE STYLE]\n"
            "Speak primarily in Brazilian Portuguese with a natural, slightly informal tone. "
            "Use light slang only when it fits; do not overdo it. "
            "Vary greetings and confirmations so you do not sound repetitive. Examples: "
            "'Salve, chefe', 'Opa, chefe', 'Tô por aqui', 'Bora resolver isso'. "
            "Keep technical answers clear and useful, but less stiff. "
            "When the user asks for a shout-out to people watching a video, respond with upbeat energy, "
            "like: 'Salve, rapaziadinha...' and mention they are watching the boss show the system, "
            "always changing the wording naturally. "
            "Do not claim you changed your actual synthetic voice; vary your wording, rhythm, and mood.\n"
            "For each user request or tool result, answer exactly once. Never repeat the same sentence, "
            "paragraph, acknowledgement, error, or question in the same response.\n"
            "When a function response contains silent=true or batch_member=true, do not mention or "
            "acknowledge that individual response; acknowledge the batch result only once.\n"
        )

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[SESSION START — THIS CLOCK IS FROZEN]\n"
            f"This conversation started at: {time_str}\n"
            f"Time keeps passing while you talk; this value goes stale within minutes.\n"
            f"NEVER state the current time or date from this value or from memory.\n"
            f"ALWAYS call the hora_agora tool first when the user asks the time, the date "
            f"or the weekday, and whenever you need the current time for a reminder.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)
        parts.append(voice_style_prompt)

        cfg = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction=_com_idioma(_no_idioma("\n".join(parts))),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            # SEM realtime_input_config, de novo — e a razão está medida.
            #
            # Em 30/08 liguei ActivityHandling.NO_INTERRUPTION porque o servidor
            # cancelava as respostas o tempo todo. Resolveu o cancelamento e
            # criou um problema pior: com ele o servidor não descarta nada, ele
            # ENFILEIRA. O cliente falou 3 vezes seguidas e a resposta levou
            # 45,1 s, porque cada frase tinha de ser processada inteira antes da
            # seguinte. Registro: turnos fechados às 01:09:46, 01:10:07 e
            # 01:10:13, resposta só às 01:10:28.
            #
            # E o cancelamento, que era o motivo de ligar aquilo, já não
            # acontece: ele vinha de som da sala chegando ao servidor, e isso
            # foi fechado por três correções independentes — o piso do portão
            # subiu para 200 (acima do ventilador medido, 199), a fila de saída
            # é esvaziada quando o JARVIS começa a falar, e o microfone fica
            # fechado mais 0,45 s depois que ele termina. Com o microfone mudo
            # durante a fala dele, o servidor praticamente não tem o que
            # interromper.
            #
            # O que o cliente pediu — "ele só para quando termina ou quando
            # clicam em interromper" — continua valendo por outro caminho, que
            # não custa latência: o que o cliente fala por cima é GUARDADO e só
            # entregue quando o JARVIS termina (_entregar_fala_guardada), então
            # não corta a fala dele; e o botão de interromper continua sendo
            # local, sem depender do servidor.
            # Com o código guardado, a reconexão RETOMA a sessão anterior: o
            # servidor já tem as instruções e as ferramentas, e a conversa
            # continua de onde parou. Sem ele era tudo do zero a cada ~10 min,
            # que é o tempo de vida que o Google dá a uma sessão de voz.
            # DESFEITO em 13:35: sem o codigo, cada reconexao remonta a sessao do
            # zero e paga as ~10.600 fichas de instrucao + ferramentas de novo.
            # Com a cota do Diego no talo isso e caro demais para trocar por
            # semelhanca com o original.
            session_resumption=types.SessionResumptionConfig(
                handle=self._codigo_retomada
            ),
            # SEM realtime_input_config: o JARVIS original não define nada aqui,
            # e é ele que responde rápido. Toda tentativa nossa de ajustar a
            # detecção de fim de fala piorou — ora ele ficava mudo por minutos,
            # ora recebia a fala picotada e devolvia <noise> em vez das palavras.
            # Áudio nativo acumula contexto muito mais rápido que texto. A janela
            # deslizante mantém conversas longas responsivas sem mudar as ferramentas.
            # DESFEITO em 13:35: o padrao do original deixa o contexto crescer
            # muito mais, e audio nativo acumula rapido. Quanto maior o contexto,
            # mais o servidor mastiga por turno — e a cauda de latencia piorou
            # justamente depois que eu tirei estes numeros (24,4 s e 33,1 s,
            # contra 1,7 a 4 s com eles). Voltaram.
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=25000,
                sliding_window=types.SlidingWindow(target_tokens=8000),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )
        if self._enhanced_live:
            # O QUE O ORIGINAL TEM E NOS NAO TINHAMOS (30/08/2026).
            #
            # `proactive_audio` faz o modelo FICAR CALADO quando a fala nao e
            # dirigida a ele — conversa de fundo, alguem falando com outra
            # pessoa na sala. Sem isso o nosso respondia qualquer ruido, e cada
            # resposta dessas queima um turno inteiro (~10 mil fichas medidas).
            # Era uma das razoes de a cota gratis dele durar horas e a nossa nao.
            #
            # `enable_affective_dialog` faz ele ouvir o tom da voz e adaptar o
            # dele. Os dois so existem na api_version v1alpha; se o servidor
            # recusar, o laco de conexao desliga `_enhanced_live` e reconecta
            # na v1beta sozinho.
            cfg["enable_affective_dialog"] = True
            # `proactive_audio` FICOU DESLIGADO (30/08/2026, 13:17).
            #
            # Ele existe para o modelo CALAR quando acha que a fala nao e
            # dirigida a ele. Ligado, deu na hora o defeito que o Diego passou o
            # dia caçando: registro `servidor_ouviu 'O já vê que hora é agora.'
            # respondeu_audio=False` — ouviu a frase inteira, entendeu, e
            # escolheu nao responder.
            #
            # No original ele funciona porque o microfone dele so pega a sala em
            # conversa normal. No nosso, com tarefa remota de minutos rodando e o
            # microfone mandando a sala inteira sem parar, o modelo passa a
            # classificar a fala do dono como conversa de fundo.
            #
            # O que ele economizava era cota; o que ele custava era o JARVIS
            # mudo. Nao compensa. O `affective_dialog` fica, esse so melhora o tom.
            # cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)
        return types.LiveConnectConfig(**cfg)

    def _prepare_whatsapp_literal_confirmation(self, args: dict, result: str) -> str:
        """Reserva uma única confirmação literal para ações mutáveis do WhatsApp."""
        action = str((args or {}).get("action") or "send_text").lower().strip().replace("-", "_")
        literal_actions = {
            "send_text", "text", "send", "message", "mensagem", "enviar_mensagem", "mandar_mensagem",
            "send_multiple_texts", "send_text_batch", "send_batch", "enviar_varias_mensagens",
            "enviar_mensagens_separadas", "send_audio", "audio", "voice", "send_file", "file",
            "media", "send_media", "image", "video", "document", "arquivo", "send_folder",
            "folder", "pasta", "delete_last_sent", "delete_sent_messages", "delete_messages",
            "delete", "apagar_ultima", "apagar_ultima_enviada", "apagar_ultimas",
            "apagar_ultimas_enviadas", "apagar_mensagens",
        }
        if action not in literal_actions:
            return ""
        clean_result = re.sub(r"\s+", " ", str(result or "")).strip()
        if not clean_result:
            return ""
        if clean_result.lower().startswith("prontinho"):
            confirmation = clean_result
        else:
            confirmation = f"Prontinho, chefe. {clean_result}"
        confirmation = _collapse_immediate_transcript_repetition(confirmation)
        self._pending_literal_confirmation = confirmation
        self._suppress_model_audio_until_turn_complete = True
        return confirmation

    async def _enqueue_literal_confirmation(self, text: str) -> bool:
        """Sintetiza texto literal com Charon e o coloca na fila de áudio uma vez."""
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            return False
        try:
            from core.gemini_voice import VozCharon
            from difflib import SequenceMatcher
            if self._literal_voice is None:
                self._literal_voice = VozCharon()
            expected_key = _transcript_key(text)
            self._drain_audio_queue()
            self.ui.write_log(f"{_nome_falante(self.ui)}: {text}")
            if self._dashboard:
                asyncio.create_task(self._dashboard.broadcast({
                    "type": "log", "speaker": "jarvis", "text": text,
                    "ts": datetime.now().isoformat(),
                }))

            def on_chunk(raw: bytes) -> None:
                self.set_speaking(True)
                for offset in range(0, len(raw), 2400):
                    self.audio_in_queue.put_nowait(raw[offset: offset + 2400])

            transcript = await self._literal_voice.stream_audio_persistent(text, on_chunk)
            actual_key = _transcript_key(transcript)
            expected_words = max(1, len(expected_key.split()))
            actual_words = len(actual_key.split())
            similarity = SequenceMatcher(None, expected_key, actual_key).ratio() if actual_key else 1.0
            if actual_words > int(expected_words * 1.30) + 2 or similarity < 0.62:
                self.ui.write_log(f"ERR: transcrição literal divergente — {transcript[:160]}")
            return True
        except Exception as exc:
            self.ui.write_log(f"ERR: confirmação de voz literal — {str(exc)[:160]}")
            return False

    async def _warm_literal_voice(self) -> None:
        """Abre antecipadamente a sessão literal para não atrasar confirmações."""
        try:
            await asyncio.sleep(0.2)
            from core.gemini_voice import VozCharon
            if self._literal_voice is None:
                self._literal_voice = VozCharon()
            await self._literal_voice.warm_persistent_session()
            print("[JARVIS] 🔊 Sessão de confirmação literal pronta")
        except Exception as exc:
            print(f"[JARVIS] ⚠️ Falha ao pré-aquecer voz literal: {exc}")

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        # Qual ferramenta e quanto ela custou. Sem isto, "ferramenta=True" no
        # diagnostico nao diz NADA sobre onde o tempo foi parar — e em 30/08 o
        # Diego levou 34 s entre a pergunta e a resposta com ferramenta no meio.
        _t_ferr = time.monotonic()
        _voice_diag("ferramenta_inicio", name)
        self._ferramenta_atual = (name, _t_ferr)

        # CLIQUE E COISA DE QUEM ENXERGA (01/09/2026). O Diego pediu "clica na
        # pasta MuTron que esta na minha tela" e o modelo chamou
        # computer_control(click) — que clica onde o mouse ja esta, sem olhar
        # nada — e disse "cliquei". Nada aconteceu. Todo clique/selecao pedido
        # por voz vai para o olhar-e-clicar, que ve a tela; o computer_control
        # fica so com teclado.
        if name == "computer_control":
            _acao_cc = str(args.get("action") or "").lower().strip().replace("-", "_")
            if _acao_cc in {"click", "double_click", "right_click", "move", "drag",
                            "screen_click", "screen_find"}:
                _fala = str(getattr(self, "_ultima_fala_do_dono", "") or "").strip()
                _voice_diag("clique_virou_olhar", f"{_acao_cc} <- {_fala[:60]!r}")
                name = "usar_computador"
                args = {"tarefa": _fala or str(args.get("description") or "clicar no que o dono pediu")}

        # "DIGITA no bloco de notas" nao e criar arquivo (01/09/2026). O Diego
        # pediu para ver o codigo sendo digitado e o modelo chamou code_helper,
        # que salvou um .py na Area de Trabalho. Se a fala dele pede digitar
        # ao vivo, o pedido vai para o digitador, seja qual for a ferramenta
        # que o modelo escolheu.
        if name in ("code_helper", "file_controller"):
            _fala = str(getattr(self, "_ultima_fala_do_dono", "") or "").strip()
            _acao_fc = str(args.get("action") or "").lower()
            # DESENHAR NAO E CLICAR. Se a fala e "desenha uma casa" e ele
            # escolheu o olhar-e-clicar (ou o teclado), a escolha esta errada:
            # sai um risco por volta de rede. Vai para o desenhista.
            if self._quer_melhorar_desenho(_fala) and name in ("editar_imagem",
                                                               "codex_remote",
                                                               "usar_computador"):
                self.ui.write_log("[desenhar] 'melhora esse desenho' e no Paint, nao no servidor")
                _voice_diag("melhorar_em_vez_de_editar", _fala[:70])
                name = "desenhar_na_tela"
                args = {"melhorar": True, "critica": _fala}
            if self._quer_desenhar(_fala) and name in ("usar_computador",
                                                       "computer_control",
                                                       "vision_control"):
                self.ui.write_log("[desenhar] troquei o computer use pelo desenhista")
                _voice_diag("desenho_em_vez_de_clique", _fala[:70])
                name = "desenhar_na_tela"
                args = {"pedido": _fala}
            if self._manda_digitar(_fala) and (name == "code_helper" or _acao_fc in ("create_file", "write")):
                _voice_diag("criar_virou_digitar", f"{name} <- {_fala[:60]!r}")
                name = "digitar_na_tela"
                args = {"pedido": str(args.get("description") or args.get("content") or _fala),
                        "linguagem": str(args.get("language") or ""),
                        "janela": ""}

        # CORTAR/COMPRIMIR VIDEO E COM O EDITOR NOVO (10/09/2026). O modelo
        # ainda pode escolher o file_processor(trim) por habito; o editar_video
        # faz o mesmo corte e tudo o mais que vier junto no pedido, com aviso de
        # andamento e sem travar a conversa.
        if name == "file_processor":
            _acao_fp = str(args.get("action") or "").lower().strip()
            _arq_fp = str(args.get("file_path") or getattr(self.ui, "current_file", "") or "")
            if (_acao_fp in ("trim", "compress", "cut", "edit")
                    and Path(_arq_fp).suffix.lower() in {".mp4", ".mov", ".mkv", ".avi",
                                                         ".wmv", ".webm", ".m4v", ".3gp"}):
                _fala = str(getattr(self, "_ultima_fala_do_dono", "") or "").strip()
                _voice_diag("video_no_editor_novo", f"{_acao_fp} <- {_fala[:60]!r}")
                _pedido = _fala or str(args.get("instruction") or "")
                if _acao_fp == "trim" and (args.get("start") or args.get("end")):
                    _pedido += f" (manter do {args.get('start') or 0} ate {args.get('end') or 'o fim'})"
                if _acao_fp == "compress":
                    _pedido += " (deixar leve)"
                name = "editar_video"
                args = {"pedido": _pedido.strip() or "deixar leve", "video": _arq_fp}

        # Hora e data vêm SEMPRE do relógio da máquina, nunca da memória do
        # modelo. Sintoma que isto resolve: a hora era escrita uma única vez
        # nas instruções, quando a sessão conectava. O cliente perguntava a
        # hora às 14:12 e o JARVIS respondia 14:07 — a hora em que a conversa
        # havia começado. Parecia que ele estava atrasado; na verdade ele
        # estava lendo um relógio parado.
        if name == "hora_agora":
            # A HORA JA VAI ESCRITA COMO SE FALA.
            #
            # Antes voltava so "01:16" e o modelo leu "dezesseis horas e
            # dezesseis minutos" — a uma e dezesseis da madrugada (01/09/2026).
            # Digito e ambiguo para quem vai FALAR o resultado; frase pronta
            # nao tem o que interpretar.
            from actions.relogio_falado import agora_falado  # noqa: PLC0415
            # SO O QUE ELE PERGUNTOU (01/09/2026). "Que hora e agora?" voltava
            # hora, dia da semana e data inteira, e o modelo lia tudo. A
            # pergunta do dono decide: hora, data, ou as duas.
            _pergunta = (str(getattr(self, "_ultima_fala_do_dono", "") or "")
                         or str(getattr(self, "_current_input_transcript", "") or ""))
            dados = agora_falado(_pergunta)
            dados["instrucao"] = ("Diga ao dono exatamente o que esta em "
                                  "'falar_assim' e NADA ALEM disso: nao "
                                  "acrescente a data se ela nao estiver la. "
                                  "Nao converta, nao recalcule.")
            return types.FunctionResponse(id=fc.id, name=fc.name, response=dados)

        explicit_location = _explicit_known_location(self._current_input_transcript)
        if explicit_location and name == "file_controller":
            file_action = str(args.get("action") or "").lower().strip().replace("-", "_")
            if file_action == "create_folder":
                args["path"] = _rebase_known_destination(
                    args.get("path", ""), explicit_location,
                    self._current_input_transcript,
                    include_spoken_subfolder=False,
                )
            elif file_action == "create_file":
                args["path"] = _rebase_known_destination(
                    args.get("path", ""), explicit_location,
                    self._current_input_transcript,
                )
            elif file_action == "write":
                if not args.get("name") and args.get("path"):
                    args["name"] = Path(str(args.get("path"))).name
                args["path"] = _rebase_known_destination(
                    args.get("path", ""), explicit_location,
                    self._current_input_transcript,
                )
        if name == "computer_control":
            computer_action = str(args.get("action") or "").lower().strip().replace("-", "_")
            if explicit_location and computer_action == "save_as":
                args["destination"] = _rebase_known_destination(
                    args.get("destination") or args.get("folder") or args.get("path") or "",
                    explicit_location,
                    self._current_input_transcript,
                )
            keys = str(args.get("keys") or "").lower().replace(" ", "")
            if computer_action == "hotkey" and keys in {"ctrl+shift+s", "command+shift+s"}:
                if explicit_location:
                    self._pending_manual_save_destination = _rebase_known_destination(
                        "", explicit_location, self._current_input_transcript
                    )
                self._pending_manual_save_extension = _explicit_file_extension(self._current_input_transcript)
            elif (
                computer_action in {"type", "smart_type", "paste"}
                and self._pending_manual_save_destination
                and args.get("text")
            ):
                try:
                    from actions.computer_control import _resolve_save_target
                    typed_name = Path(str(args.get("text") or "")).name
                    target = _resolve_save_target(
                        self._pending_manual_save_destination,
                        typed_name,
                        self._pending_manual_save_extension,
                        False,
                    )
                    args["text"] = str(target)
                    self.ui.write_log(f"[Computer] destino Salvar como corrigido: {target}")
                except Exception as exc:
                    self.ui.write_log(f"[Computer] não consegui corrigir Salvar como: {str(exc)[:140]}")
                finally:
                    self._pending_manual_save_destination = ""
                    self._pending_manual_save_extension = ""

        print(f"[JARVIS] 🔧 {name}  {args}")
        if name == "vision_control":
            self._last_vision_tool_at = time.monotonic()
        if name != "save_memory":
            self.ui.set_state(_tool_task_state(name, args))

        # Trabalhos que rodam no servidor levam DE VERDADE um minuto ou mais
        # (criar imagem, criar site, buscar leads). Durante todo esse tempo o
        # JARVIS fica em silêncio absoluto, e do lado de cá isso é
        # indistinguível de ter travado: em 30/08 o cliente cronometrou "parou
        # e só respondeu depois de 1 minuto" — e era a imagem dele sendo
        # gerada, que ele mesmo elogiou depois. Não era defeito nenhum, mas o
        # silêncio sem explicação é.
        # "Ja terminou o video?" e "para a edicao" respondem na hora: o aviso de
        # "preparando a edicao" no painel seria mentira ali.
        if name in _TAREFAS_DEMORADAS and not (
                name == "editar_video" and str(args.get("acao") or "").lower().strip()
                in ("status", "andamento", "cancelar", "parar")):
            try:
                self.ui.write_log(f"SIS: {_aviso_de_demora(name, args)}")
            except Exception:
                pass
            _voice_diag("tarefa_demorada_inicio", name)

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."
                self._store_pending_delete(args, str(result))

            elif name == "whatsapp_bridge":
                r = await loop.run_in_executor(None, lambda: whatsapp_action(parameters=args, player=self.ui))
                result = r or "WhatsApp concluído."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 3.0
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Chefe, já estou analisando a imagem anterior. Segura só um instante."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    self._pending_vision = None
                    self._vision_cam_active = False
                    self._vision_close_pending = False
                    try:
                        r = await loop.run_in_executor(
                            None,
                            lambda: screen_process_action(
                                parameters=args,
                                response=None,
                                player=self.ui,
                                session_memory=None,
                            ),
                        )
                        result = r or "Chefe, capturei a imagem, mas não consegui formar uma resposta agora."
                    finally:
                        self._vision_busy = False
                        self._vision_last_time = _t_mod.monotonic()
                        # Hora em que a tela foi OLHADA de verdade. E o que
                        # libera a marcacao logo abaixo.
                        self._olhou_a_tela_em = _t_mod.monotonic()

            elif name == "marcar_na_tela":
                # OLHAR ANTES DE MARCAR (01/09/2026).
                #
                # Ele disse "da uma olhadinha na tela e procura o nome celular,
                # nao estou encontrando" e ela foi direto marcar: 104 segundos
                # de espera para uma coisa que respondida em voz levaria oito.
                #
                # A regra em texto do prompt nao segurou — testei e o modelo
                # pulou a etapa de olhar. Aqui e codigo: sem ter olhado a tela
                # ha pouco, o pedido de marcar vira um OLHAR, com a resposta de
                # onde o item esta e a oferta de marcar. Se ele insistir, a
                # chamada seguinte ja passa, porque a tela foi olhada agora.
                #
                # Se ele mandou marcar com todas as letras ("marca isso pra
                # mim"), nao ha o que descobrir: vai direto.
                import time as _t_mod
                _alvo = str(args.get("alvo") or args.get("target") or "").strip()
                _fala = str(getattr(self, "_ultima_fala_do_dono", "") or "").lower()
                _pediu = any(x in _fala
                             for x in ("marca", "marque", "circula", "circule",
                                       "mostra onde", "me mostre onde"))
                _olhou_ha = _t_mod.monotonic() - getattr(self, "_olhou_a_tela_em", 0.0)
                if not _pediu and _olhou_ha > 240.0 and not self._vision_busy:
                    self._vision_busy = True
                    try:
                        r = await loop.run_in_executor(
                            None,
                            lambda: screen_process_action(
                                parameters={"angle": "screen",
                                            "text": (f"Onde exatamente esta {_alvo} nesta tela? "
                                                     f"Responda em uma frase curta, dizendo o "
                                                     f"lugar com uma referencia que a pessoa "
                                                     f"consiga seguir com os olhos.")},
                                response=None, player=self.ui, session_memory=None),
                        )
                    finally:
                        self._vision_busy = False
                        self._vision_last_time = _t_mod.monotonic()
                        self._olhou_a_tela_em = _t_mod.monotonic()
                    result = ((r or "Olhei a tela, mas nao consegui localizar.")
                              + " Se mesmo assim voce nao achar, me fala que eu "
                                "circulo na imagem e te mostro.")
                else:
                    # Ele ja sabe onde e (ou mandou marcar): vai para a VPS.
                    # `run_in_executor` obrigatorio, senao o laco de eventos
                    # trava esperando por ele mesmo (erro de 30/08, que deu
                    # `programa_travou pior=200235ms`).
                    from actions.marcar_tela import marcar_na_tela as _marcar
                    self.ui.write_log("SIS: tirando o print e marcando pra voce...")
                    r = await loop.run_in_executor(
                        None, lambda: _marcar(args, player=self.ui, speak=self.speak))
                    result = r or "Nao consegui marcar a tela agora."

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "remote_media":
                if not self._dashboard:
                    result = "O Controle Remoto não está disponível agora."
                else:
                    result = await self._dashboard.remote_media_action(
                        str(args.get("action") or ""), announce=False
                    )

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "codex_remote":
                r = await loop.run_in_executor(
                    None,
                    lambda: remote_codex_job(parameters=args, player=self.ui, speak=self.speak),
                )
                result = r or "A tarefa remota terminou sem retorno."

            elif name == "cowork":
                # Volta na hora: o trabalho segue numa thread e fala sozinho no fim.
                r = await loop.run_in_executor(None, lambda: self._cowork_ferramenta(args))
                result = r or "Pedido do Cowork recebido."

            elif name == "executar_tarefa":
                # O Codex na VPS escreve o comando; o PC do cliente executa.
                # Assim uma ferramenta so substitui dezenas, e o que nao foi
                # previsto tambem funciona — sem programar ferramenta nova.
                from actions.tarefa_comando import (PEDIDO_AO_CODEX, executar,
                                                    _limpar_resposta)
                # NAO reimportar `remote_codex_job` aqui. Ele ja vem do topo do
                # arquivo (linha 83), e um `import` dentro da funcao torna o
                # nome LOCAL em TODA ela — inclusive no ramo `codex_remote`, la
                # em cima, que passa a estourar
                # "cannot access free variable 'remote_codex_job'" e derruba a
                # criacao de imagem e de site. Foi o erro de 31/08/2026.
                _tarefa = str(args.get("tarefa") or "").strip()
                if not _tarefa:
                    result = "Faltou dizer qual e a tarefa."
                else:
                    _voice_diag("tarefa_pedindo_comando", _tarefa[:90])
                    # CERTO PRIMEIRO, nao rapido primeiro.
                    #
                    # Ate 30/08/2026 quem escrevia o comando era o OpenRouter
                    # gratuito, por ser rapido. So que quando a cota do dia
                    # apertava ele caia nos modelos fracos e escrevia comando
                    # ERRADO — chegou a comer o tracinho do caminho e a tarefa
                    # falhava sem motivo aparente. Tarefa errada em 1,5 s nao
                    # serve para nada.
                    #
                    # Agora quem escreve e o Codex, do plano pago do dono:
                    # medido de 4,4 a 9,1 s ponta a ponta (media 6,4 s), sem
                    # teto de 50 pedidos por dia. O OpenRouter fica de reserva,
                    # para quando a VPS estiver fora do ar.
                    _cmd = ""
                    # O CEREBRO QUE JA ESTA NA CONVERSA ESCREVE (01/09/2026).
                    #
                    # Uma linha de PowerShell nao precisa de um agente inteiro na
                    # VPS: o cerebro em camadas (Gemini do cliente -> nossa
                    # OpenAI -> gratuito) escreve em 1 a 2 s, contra 6,4 s
                    # medidos indo ate o Codex. O Codex fica logo abaixo, para
                    # quando as tres camadas falharem, e segue dono de imagem,
                    # site e leads, que e onde ele ganha.
                    try:
                        _cmd = _limpar_resposta(
                            await loop.run_in_executor(
                                None,
                                lambda: self._openrouter.escrever_comando_em_camadas(_tarefa)))
                        if _cmd:
                            _voice_diag("tarefa_comando_por", "cerebro")
                    except Exception as _exc_c:
                        _voice_diag("tarefa_cerebro_falhou", str(_exc_c)[:110])
                    if not _cmd:
                      try:
                        # run_in_executor E OBRIGATORIO AQUI.
                        #
                        # Esta funcao e uma corrotina do laco de eventos, e o
                        # `remote_codex_job` bloqueia esperando outra corrotina
                        # DO MESMO LACO. Chamado direto, o laco espera por ele
                        # mesmo: o pedido nunca sai, o servidor nunca ve nada e
                        # so o prazo solta. Foi exatamente isso em 30/08/2026,
                        # `programa_travou pior=200235ms`. Os ramos de imagem e
                        # site sempre usaram o executor; este ficou de fora.
                        _bruto = await loop.run_in_executor(
                            None,
                            lambda: remote_codex_job(
                                {"kind": "chat",
                                 "description": PEDIDO_AO_CODEX + _tarefa,
                                 "project_name": "JarvisTarefa"},
                                player=self.ui, prazo=30))
                        _cmd = _limpar_resposta(_bruto)
                        _voice_diag("tarefa_comando_por", "codex")
                      except Exception as _exc_cmd:
                        _voice_diag("tarefa_codex_falhou", str(_exc_cmd)[:110])
                    _voice_diag("tarefa_comando", _cmd[:180])
                    self.ui.write_log(f"[comando] {_cmd[:120]}")
                    _r = executar(_cmd)
                    if _r.get("recusado"):
                        result = (f"Recusei esse comando por seguranca: "
                                  f"{_r['motivo']}. Nao executei nada.")
                    elif _r.get("ok"):
                        result = (_r.get("saida") or "Feito.")
                    else:
                        result = (f"O comando falhou: "
                                  f"{_r.get('motivo') or _r.get('saida') or 'erro'}")
                    _voice_diag("tarefa_resultado",
                                f"ok={_r.get('ok')} {str(result)[:110]}")

            elif name == "usar_computador":
                # OLHAR E CLICAR (01/09/2026). O laco bloqueia por minutos e
                # conversa com a VPS pelo relay — por isso vai ao executor
                # (ver a armadilha dos 200 s no ramo `executar_tarefa`).
                # A PORTA E UMA SO (04/09/2026): sem "ativar computador"
                # recente, nem o modelo mexe no computador. Antes ele podia
                # decidir sozinho assumir a tela, e o cliente quer o contrario:
                # so quando ele manda, e so depois de avisar.
                #
                # E A RECUSA SAI PELO CAMINHO NORMAL, nao por um `return` seco.
                # No Bob esta trava fazia `return result` com o texto cru — mas
                # `_execute_tool` promete devolver um `types.FunctionResponse`,
                # e o motor de voz manda o que voltar daqui direto para o
                # Google. Uma string solta ali quebra o envio da resposta da
                # ferramenta, e o sintoma seria o JARVIS emudecer no meio, nao um
                # "voce precisa ativar antes". Aqui a recusa vira `result` e
                # segue pelo mesmo fim de sempre. (Achado na revisao, 04/09.)
                if time.monotonic() > self._pc_liberado_ate:
                    _voice_diag("computer_user_modelo_sem_ativar", str(args)[:60])
                    result = ("Para eu mexer no computador o senhor precisa ativar antes: "
                              "e so dizer 'ativar computador'.")
                elif self._SO_ATIVAR_PC.search(str((args or {}).get("tarefa") or "")):
                    _voice_diag("computer_user_so_ativar", str(args)[:80])
                    self._ativar_computer_use()
                    result = ("Ativei o computer use e ja perguntei em voz ao dono o que ele "
                              "quer que eu faca. Nao diga mais nada agora.")
                else:
                    self._parar_pc = False
                    self._esc_avisado = False
                    self._usando_pc = True
                    try:
                        r = await loop.run_in_executor(None, lambda: usar_computador(
                            parameters=args, player=self.ui,
                            falar=self._falar_no_computador,
                            falar_aviso=self._avisar_do_computador,
                            falando=self._esta_falando,
                            perguntar=self._perguntar_ao_dono,
                            deve_parar=lambda: self._parar_pc,
                            correcoes=self._pegar_correcoes,
                            ao_esc=self._esc_no_computador))
                    finally:
                        self._usando_pc = False
                        self._pergunta_pendente = False
                    result = r or "Feito."
                    if getattr(self, "_esc_avisado", False):
                        self._esc_avisado = False
                        result = ("O dono apertou ESC e eu ja avisei em voz que parei. "
                                  "Nao repita: responda so 'Certo.'")

            elif name == "organizar_pasta":
                r = await loop.run_in_executor(None, lambda: organizar_pasta(parameters=args, player=self.ui))
                result = r or "Feito."

            elif name == "desfazer":
                r = await loop.run_in_executor(None, lambda: desfazer_tool(parameters=args, player=self.ui))
                result = r or "Feito."

            elif name == "digitar_na_tela":
                # Gera o conteudo pelo cerebro em camadas e digita ao vivo.
                self._parar_pc = False
                self._usando_pc = True
                try:
                    r = await loop.run_in_executor(None, lambda: digitar_na_tela(
                        parameters=args, player=self.ui,
                        gerar=self._openrouter.escrever_texto_em_camadas,
                        deve_parar=lambda: self._parar_pc))
                finally:
                    self._usando_pc = False
                result = r or "Feito."

            elif name == "desenhar_na_tela":
                # A MESMA PORTA DO COMPUTER USE (07/09/2026): desenhar toma o
                # mouse por mais de um minuto, entao tambem so acontece depois
                # de "ativar computador". A recusa vira `result` e segue pelo
                # caminho normal — devolver string solta aqui emudece o motor
                # de voz (armadilha ja catalogada no ramo do usar_computador).
                if time.monotonic() > self._pc_liberado_ate:
                    _voice_diag("desenhar_modelo_sem_ativar", str(args)[:60])
                    result = ("Para eu desenhar eu preciso mexer no seu computador. "
                              "O senhor pode dizer 'ativar computador' primeiro?")
                else:
                    self._parar_pc = False
                    self._usando_pc = True
                    try:
                        r = await loop.run_in_executor(None, lambda: desenhar_na_tela(
                            parameters=args, player=self.ui,
                            gerar=self._openrouter.escrever_em_camadas,
                            falar=self._falar_local,
                            olhar=self._olhar_o_desenho,
                            deve_parar=lambda: self._parar_pc))
                    finally:
                        self._usando_pc = False
                        self._pc_liberado_ate = time.monotonic() + 120
                    result = r or "Feito."

            elif name == "fechar_app":
                from actions.fechar_app import fechar_app as _fechar
                result = _fechar(args, player=self.ui)

            elif name == "editar_imagem":
                # A foto sai daqui mesmo: a que o cliente arrastou para a janela
                # ou escolheu no quadro de arquivo.
                edit_args = {
                    "kind": "image_edit",
                    "description": args.get("description") or "",
                    "imagem": args.get("imagem") or args.get("image") or "",
                    "project_name": args.get("project_name") or "JarvisImagem",
                }
                r = await loop.run_in_executor(
                    None,
                    lambda: remote_codex_job(parameters=edit_args, player=self.ui, speak=self.speak),
                )
                result = r or "A edição da imagem terminou sem retorno."

            elif name == "editar_video":
                # EDITAR VIDEO (10/09/2026). Tudo acontece AQUI: o cerebro so
                # escreve a receita (texto), o ffmpeg que vem no instalador faz
                # o trabalho, e o video nunca sai da maquina. A ferramenta volta
                # em segundos e o render segue em segundo plano, avisando pela
                # voz no fim. O import fica AQUI DENTRO de proposito: se a peca
                # nova falhar, so esta ferramenta falha — o JARVIS segue de pe.
                from actions.editar_video import editar_video as _editar_video  # noqa: PLC0415
                _args_video = dict(args)
                # A rede do video ja manda a frase certa; so o caminho do
                # cerebro precisa da ultima fala guardada.
                _args_video.setdefault("_fala_do_dono",
                                       str(getattr(self, "_ultima_fala_do_dono", "") or ""))
                # O que o dono arrastou antes (a janela so guarda um arquivo):
                # o video, para quando a janela ja mostra a imagem, e as
                # imagens desta edicao — no caminho do cerebro tambem.
                if time.monotonic() - getattr(self, "_video_carregado_em", -1e9) < 3600:
                    _args_video.setdefault("_video_lembrado",
                                           str(getattr(self, "_ultimo_video", "") or ""))
                if not _args_video.get("imagens_arrastadas"):
                    _args_video["imagens_arrastadas"] = self._imagens_da_edicao()
                # Uma edicao comecou (por qualquer caminho): o pedido que
                # esperava o video ja foi atendido e nao pode disparar depois.
                self._pedido_video_pendente = None
                _gerar = getattr(self._openrouter, "escrever_em_camadas", None)
                r = await loop.run_in_executor(None, lambda: _editar_video(
                    parameters=_args_video, player=self.ui, gerar=_gerar,
                    falar=self._falar_do_video, ouvido=self._ouvido_para_video,
                    ver=self._olhar_o_desenho))
                result = r or "Nao consegui editar o video agora."

            elif name == "buscar_leads":
                # A varredura roda no worker isolado da VPS; o cliente nunca
                # alcanca o servidor nem sabe como a coleta e feita.
                leads_args = {
                    "kind": "leads",
                    "description": args.get("description") or "",
                    "project_name": "JarvisLeads",
                    "destinos": args.get("destinos") or "",
                    "abrir": args.get("abrir"),
                }
                r = await loop.run_in_executor(
                    None,
                    lambda: remote_codex_job(parameters=leads_args, player=self.ui, speak=self.speak),
                )
                result = r or "A busca de leads terminou sem retorno."

            elif name == "dev_agent":
                # Keep old local behavior available until the relay is ready. Once
                # the private bridge is enabled, website/project requests are sent
                # to the isolated VPS worker instead of the client Gemini key.
                if getattr(self.ui, "_codex_remote_mode", False) and getattr(self.ui, "_codex_relay", None):
                    remote_args = {
                        "kind": "site",
                        "description": args.get("description") or "",
                        "project_name": args.get("project_name") or "JarvisProjeto",
                    }
                    r = await loop.run_in_executor(
                        None,
                        lambda: remote_codex_job(parameters=remote_args, player=self.ui, speak=self.speak),
                    )
                else:
                    r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                # O file_processor chama "ffmpeg" pelo nome, e no PC do cliente
                # nao ha ffmpeg no PATH: audio e video respondiam "ffmpeg not
                # found" com o programa DENTRO do instalador (10/09/2026).
                try:
                    from actions.editar_video import garantir_ffmpeg_no_path  # noqa: PLC0415
                    garantir_ffmpeg_no_path()
                except Exception:
                    pass
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "vision_control":
                r = await loop.run_in_executor(None, lambda: vision_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "shutdown_jarvis":
                # SO COM ORDEM CLARA.
                #
                # Em 01/09/2026 um ruido virou "E voce pode sair daqui" e o
                # modelo desligou o JARVIS no meio do trabalho. Fechar e a unica
                # coisa que o dono nao consegue desfazer falando — depois disso
                # o microfone nem existe mais. Entao a ordem tem de estar na
                # fala dele, escrita, nao na interpretacao do modelo.
                _ORDENS_DE_DESLIGAR = (
                    "desliga", "desligar", "desligue", "encerra", "encerrar",
                    "encerre", "fecha o programa", "fechar o programa",
                    "fecha voce", "pode fechar", "sai do ar", "tchau jarvis",
                    "ate amanha jarvis", "boa noite jarvis, pode desligar",
                )
                import unicodedata as _u2
                _fala = _u2.normalize("NFD", str(
                    getattr(self, "_ultima_fala_do_dono", "") or "").lower())
                _fala = "".join(c for c in _fala if _u2.category(c) != "Mn")
                if not any(o in _fala for o in _ORDENS_DE_DESLIGAR):
                    _voice_diag("desligamento_recusado", f"fala: {_fala[:60]!r}")
                    result = ("Voce quer mesmo que eu desligue? Se quiser, fala "
                              "'desliga o JARVIS' que eu fecho.")
                    self.ui.write_log("SIS: pedido de desligar sem ordem clara — perguntei antes.")
                else:
                    self.ui.write_log("SYS: Shutdown requested.")
                    self.speak("Ate logo, chefe.")
                    def _shutdown():
                        import time
                        time.sleep(1)
                        # O mesmo fecho da janela: a ponte do WhatsApp e os
                        # processos do JARVIS vao junto (01/10/2026).
                        _encerrar_tudo("pedido por voz")
                    threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        response_payload = {"result": result}
        if name == "whatsapp_bridge" and self._prepare_whatsapp_literal_confirmation(args, str(result)):
            response_payload.update({"silent": True, "literal_confirmation": True})

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response=response_payload
        )

    async def _coalesce_whatsapp_text_calls(self, function_calls) -> list[types.FunctionResponse] | None:
        """Consolida vários send_text do mesmo turno/contato em um único lote."""
        calls = list(function_calls or [])
        if len(calls) < 2:
            return None
        text_actions = {
            "send_text", "text", "send", "message", "mensagem",
            "enviar_mensagem", "mandar_mensagem",
        }
        parsed: list[tuple[object, dict, str, str]] = []
        for fc in calls:
            if str(getattr(fc, "name", "")) != "whatsapp_bridge":
                return None
            args = dict(getattr(fc, "args", None) or {})
            action = str(args.get("action") or "send_text").lower().strip().replace("-", "_")
            if action not in text_actions:
                return None
            to = str(args.get("to") or args.get("receiver") or args.get("contact") or args.get("target") or "").strip()
            message = str(args.get("message") or args.get("message_text") or args.get("text") or "").strip()
            if not to or not message:
                return None
            parsed.append((fc, args, to, message))
        recipients = {_norm_pt_command(item[2]) for item in parsed}
        if len(recipients) != 1:
            return None

        first_fc, first_args, to, _ = parsed[0]
        batch_args = dict(first_args)
        batch_args.update({
            "action": "send_multiple_texts",
            "to": to,
            "messages": [item[3] for item in parsed],
        })
        batch_args.pop("message", None)
        batch_args.pop("message_text", None)
        batch_args.pop("text", None)
        self.ui.set_state(_tool_task_state("whatsapp_bridge", batch_args))
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: whatsapp_action(parameters=batch_args, player=self.ui),
        )
        self._prepare_whatsapp_literal_confirmation(batch_args, str(result))
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        print(f"[JARVIS] 📦 {len(parsed)} chamadas WhatsApp consolidadas em um lote")

        responses: list[types.FunctionResponse] = []
        for index, (fc, _args, _to, _message) in enumerate(parsed):
            payload = {"result": result, "batch_count": len(parsed), "silent": True, "literal_confirmation": True} if index == 0 else {
                "result": "Incluída no mesmo lote já confirmado.",
                "silent": True,
                "batch_member": True,
            }
            responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response=payload))
        return responses

    # Quanto esperar sem áudio novo antes de dizer ao servidor que a fala
    # acabou. O portão de silêncio já segura 1,2 s depois da última voz, então
    # na prática o turno fecha ~1,4 s depois de o cliente parar de falar.
    # Quanto esperar, depois que o portão para de mandar voz, antes de dar o
    # empurrão de "acabei de falar". Era 0,15 s, o que fazia disto o mecanismo
    # PRINCIPAL de fechar turno — e aí ele disparava a cada ruído. Agora é uma
    # rede de segurança: no caminho normal quem fecha o turno é a detecção do
    # próprio servidor, em cima dos 3 s de silêncio que o portão manda depois
    # da fala. Só se ele não der sinal nenhum é que empurramos.
    _FECHAR_TURNO_S = 5.0
    # E nunca dois empurrões seguidos: foi a rajada que travou o servidor.
    _INTERVALO_FECHAMENTO_S = 20.0

    # Teto do que guardamos da fala do cliente enquanto o JARVIS fala:
    # 10 s a 16 kHz, 16 bits, mono.
    _GUARDAR_FALA_MAX_BYTES = 16000 * 2 * 10

    # DESLIGADO em 30/08, no mesmo dia em que foi criado, porque FAZ MAL.
    #
    # A ideia era boa: o que o cliente fala por cima do JARVIS não deveria ser
    # jogado fora. Mas a entrega chega ao servidor no exato instante em que ele
    # está começando a responder, e ele lê aquilo como "o cliente voltou a
    # falar" — e CANCELA a própria resposta. O registro mostrou a correlação em
    # todos os turnos, sem exceção:
    #     01:44:10.101  fala_guardada_entregue 2.0s
    #     01:44:11.690  servidor_interrompeu
    # Ou seja: para não perder a fala do cliente, eu passei a perder a resposta
    # do JARVIS. Troca ruim.
    #
    # O comportamento volta a ser o do JARVIS original: microfone mudo enquanto
    # ele fala, e pronto. Se um dia for reativado, a entrega precisa esperar o
    # turno inteiro terminar (turn_complete + fila de voz vazia), não só o
    # `_is_speaking` cair.
    _GUARDAR_FALA_POR_CIMA = False

    def _entregar_fala_guardada(self) -> None:
        """Manda ao servidor o que o cliente falou por cima da fala do JARVIS."""
        if not self._GUARDAR_FALA_POR_CIMA:
            if self._fala_guardada:
                self._fala_guardada = []
                self._fala_guardada_bytes = 0
            return
        if not self._fala_guardada:
            return
        with self._speaking_lock:
            falando = self._is_speaking
        # Espera ele terminar de verdade, incluindo o rabicho anti-eco.
        if falando or time.monotonic() < self._mic_fechado_ate:
            return
        if self._tool_busy or self.ui.muted or self._phone_active:
            # Não é hora: descarta em vez de entregar fora de contexto.
            self._fala_guardada.clear()
            self._fala_guardada_bytes = 0
            return
        pedacos = self._fala_guardada
        total = self._fala_guardada_bytes
        self._fala_guardada = []
        self._fala_guardada_bytes = 0
        for pedaco in pedacos:
            self._queue_realtime_audio(pedaco)
        _voice_diag("fala_guardada_entregue", f"{total/(16000*2):.1f}s")

    # De quanto em quanto tempo sem mandar NADA damos um sinal de vida.
    # O portão de silêncio corta todo o áudio quando ninguém fala, então numa
    # pausa de conversa o programa fica minutos sem enviar um único byte — e
    # conexão parada assim é derrubada em silêncio, sem erro e sem aviso.
    # Registro de 30/08: sessão conectada às 01:56:40, conversa boa (respostas
    # de 1,0 a 2,1 s), último sinal do servidor às 01:58:17, 51 s de pausa, e
    # quando o cliente voltou a falar não veio mais nada.
    _SINAL_DE_VIDA_S = 20.0

    async def _manter_conexao_viva(self) -> None:
        """Mantém a conexão aquecida durante as pausas da conversa."""
        # 20 ms de silêncio: pequeno demais para o servidor achar que é fala,
        # grande o bastante para o soquete não ser dado como abandonado.
        silencio = b"\x00\x00" * 320
        while True:
            await asyncio.sleep(5)
            sessao = self.session
            if sessao is None:
                continue
            parado = time.monotonic() - (self._ultimo_trafego or 0.0)
            if parado < self._SINAL_DE_VIDA_S:
                continue
            try:
                await sessao.send_realtime_input(
                    media={"data": silencio, "mime_type": f"audio/pcm;rate={SEND_SAMPLE_RATE}"}
                )
                # Conta como tráfego, mas de propósito NÃO arma o vigia: não
                # pedimos nada, então não há resposta a cobrar.
                self._ultimo_trafego = time.monotonic()
                _voice_diag("sinal_de_vida", f"parado_ha={parado:.0f}s")
            except Exception as exc:
                # Falhar aqui é informação: a conexão já não aceita nada.
                _voice_diag("sinal_de_vida_erro", f"{type(exc).__name__}: {str(exc)[:140]}")
                raise

    async def _fechar_turno_se_parou(self) -> None:
        """Avisa o servidor que a fala do cliente terminou.

        Este era o buraco: o portão de silêncio simplesmente PARA de mandar
        áudio quando o cliente cala a boca. A detecção automática de fim de
        fala do servidor trabalha em cima do áudio que chega — sem áudio, ela
        não tem o que analisar e o turno fica ABERTO esperando o resto de uma
        frase que nunca vem. Enquanto isso o servidor não responde nada, e tudo
        o que vier depois — inclusive o que o cliente DIGITA no chat — entra na
        fila atrás desse turno pendurado. Era exatamente o relato: "não
        respondeu nem no microfone nem quando eu digitei, e só respondeu dois
        minutos depois".

        A constante _FECHAR_TURNO_S existia desde antes, mas nada no programa a
        usava: `_turno_aberto` e `_turnos_fechados` só apareciam na linha de
        diagnóstico, que por isso mostrava "fechados=0" para sempre.
        """
        # RELIGADO em 30/08/2026, 13:31. ERRO MEU, e vale registrar o porque.
        #
        # Eu desliguei isto para ficar igual ao Mark-LI, que de fato nao manda
        # `audio_stream_end` em lugar nenhum. So que os dois desenhos so
        # funcionam INTEIROS, e eu misturei metade de cada um:
        #
        #   original : SEM portao + SEM aviso de fim  -> o servidor recebe audio
        #              o tempo todo, ve o silencio e decide sozinho. Funciona.
        #   nosso    : COM portao + COM aviso de fim  -> o portao para de mandar
        #              quando o dono cala, e o aviso supre o que o servidor
        #              deixou de receber. Funciona.
        #   o que eu fiz: COM portao + SEM aviso      -> o servidor fica sem
        #              audio E sem aviso. O turno nunca fecha e ele nao responde.
        #
        # Sintoma imediato (Diego, 13:30): "nao responde pelo microfone, mas se
        # eu digito no chat responde na mesma hora" — digitar manda
        # `turn_complete=True` explicito e nao depende disto. E a assinatura
        # dessa combinacao, e ja esta documentada tres vezes neste arquivo.
        #
        # REGRA: portao e aviso de fim de fala andam JUNTOS. Mexeu em um, olhe
        # o outro antes de reiniciar.
        if not self._turno_aberto or self.session is None:
            return
        agora = time.monotonic()
        # MEDIR DA ULTIMA VOZ, NAO DO ULTIMO BYTE ENVIADO (30/08/2026).
        #
        # Isto media `_ultimo_envio_de_voz`, que o `_send_realtime` carimba a
        # CADA bloco que sai. Fazia sentido quando o portao so deixava passar
        # fala. Depois que o microfone passou a mandar TUDO — como o JARVIS
        # original, para a deteccao do servidor ter silencio que analisar —
        # esse carimbo virou "o ultimo pedaco de silencio da sala", renovado a
        # cada 40 ms. Ou seja: `parado` nunca chegava aos 5 s e este empurrao
        # NUNCA disparava. Codigo morto sem parecer morto.
        #
        # E exatamente o mesmo engano que ja tinha sido corrigido no vigia com
        # `_envio_cobravel` ("bytes enviados virou o silencio da sala") — este
        # ponto ficou para tras.
        #
        # Sintoma no cliente (Diego, 30/08 12:57): falava e o JARVIS respondia
        # **5 minutos depois**, enquanto digitar no chat respondia na hora.
        # Registro: `turno=ABERTO fechados=0` a rodada inteira, com
        # `enviado=2552` e `pico=5740` — audio saindo, turno nunca fechado.
        #
        # `_ultima_voz_detectada` e carimbado em `_audio_com_voz` so quando ha
        # voz de verdade acima do limiar, que e o que "parou de falar" significa.
        if not self._ultima_voz_detectada:
            return
        # UM EMPURRAO POR FALA (corrigido 13:58, 30/08/2026).
        #
        # Faltava esta guarda e o efeito foi feio: como `_ultima_voz_detectada`
        # NAO avanca durante o silencio, uma vez passados os 5 s ficava passado
        # para sempre, e o empurrao se repetia a cada `_INTERVALO_FECHAMENTO_S`
        # ate o dono falar de novo. O registro mostra o mesmo silencio sendo
        # fechado tres vezes seguidas:
        #     n=4  parado_ha=5.09s
        #     n=5  parado_ha=25.19s
        #     n=6  parado_ha=45.29s
        # Doze fechamentos numa sessao. Mandar `audio_stream_end` em rajada faz
        # o servidor PARAR de reagir ao audio — ja esta documentado, e foi o
        # "ele parou de responder" de agora.
        #
        # Agora so fecha uma vez por fala: guarda de qual voz ja foi fechada e
        # so volta a empurrar quando o detector marcar voz NOVA.
        if self._ultima_voz_detectada == getattr(self, "_voz_ja_fechada", 0.0):
            return
        parado = agora - self._ultima_voz_detectada
        if parado < self._FECHAR_TURNO_S:
            return
        # O servidor JÁ REAGIU ao que mandamos (transcreveu, respondeu, o que
        # seja). Então a detecção de fala dele está funcionando e não precisa
        # do nosso empurrão — que, repetido, faz mal.
        if not self._envio_cobravel:
            self._turno_aberto = False
            return
        # Um empurrão de vez em quando, nunca em rajada. Em 30/08 mandei 5
        # `audio_stream_end` em 60 s (o portão abre e fecha a cada ruído) e o
        # servidor PAROU de reagir ao áudio: 5 turnos fechados, 61 s de silêncio
        # absoluto, nem transcrição. Foi o meu próprio conserto virando defeito.
        if (agora - self._ultimo_fechamento_em) < self._INTERVALO_FECHAMENTO_S:
            return
        self._turno_aberto = False
        self._ultimo_fechamento_em = agora
        # Marca ESTA fala como ja fechada: sem isto o empurrao vira rajada.
        self._voz_ja_fechada = self._ultima_voz_detectada
        try:
            await self.session.send_realtime_input(audio_stream_end=True)
            self._turnos_fechados += 1
            _voice_diag("turno_fechado", f"n={self._turnos_fechados} parado_ha={parado:.2f}s")
        except Exception as exc:
            # Falhar aqui não pode derrubar a sessão: no pior caso voltamos ao
            # comportamento antigo, que era esperar o servidor decidir sozinho.
            _voice_diag("fechar_turno_erro", f"{type(exc).__name__}: {str(exc)[:160]}")

    async def _send_realtime(self):
        try:
            while True:
                msg = await self.out_queue.get()
                await self.session.send_realtime_input(media=msg)
                self._mic_enviado += 1
                agora = time.monotonic()
                self._ultimo_envio_de_voz = agora
                self._ultimo_trafego = agora
                # Mandamos voz: para o servidor, a fala do cliente está EM
                # ANDAMENTO. Alguém precisa avisar quando ela termina.
                self._turno_aberto = True
                # Arma o relogio do vigia somente na PRIMEIRA voz depois de uma
                # resposta. Assim falar sem parar nao reinicia a contagem (era o
                # defeito antigo) e o silencio de quando ninguem estava falando
                # tambem nao entra na conta (era o defeito novo).
                # Quem arma o vigia agora é o DETECTOR DE VOZ, em
                # `_audio_com_voz`, e não o envio de bytes: com o microfone
                # mandando contínuo, contar bytes aqui equivalia a chamar o
                # silêncio da sala de "fala".
        except BaseException as exc:
            _voice_diag_exception("send_realtime_error", exc)
            raise

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            self._mic_capturado += 1
            # Carimbo do ultimo bloco que REALMENTE chegou. E o unico jeito de
            # saber que o microfone morreu: ver o vigia logo abaixo.
            self._mic_ultimo_bloco = time.monotonic()
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            # AS TRES CONDICOES DO ORIGINAL, e so elas (30/08/2026):
            #   not jarvis_speaking and not muted and not phone_active
            # Sairam daqui:
            #  - `_tool_busy`: o original NAO trava o microfone durante ferramenta.
            #    Era a trava que, presa, dava "voz morta, teclado vivo".
            #  - `_mic_fechado_ate`: o rabicho de silencio pos-fala era invencao
            #    nossa para evitar eco. Eco medido nesta maquina: 42, contra piso
            #    200 — nunca foi problema de verdade.
            # `_tool_busy` VOLTOU (30/08/2026, 13:17) — e nao e recuo do "igual
            # ao original". O Mark-LI nao tem tarefa remota: o `codex_remote`
            # manda um trabalho para a VPS e demora MINUTOS (medido hoje:
            # 13:12:29 a 13:15:05, com dois buracos de audio de 71 s e 87 s).
            # Sem esta trava, o microfone despeja a sala inteira no modelo
            # durante todo esse tempo — as fichas de entrada subiram de 10.755
            # para 14.491 em cinco minutos so com barulho de sala.
            #
            # A trava presa era o defeito antigo ("voz morta, teclado vivo"),
            # mas hoje ela tem rede: o `_vigia_sessao` solta sozinho depois de
            # 90 s e registra `mic_destravado`.
            if (jarvis_speaking or self._tool_busy or self.ui.muted
                    or self._phone_active):
                self._mic_bloqueado += 1
                # O que o cliente fala enquanto o JARVIS fala é descartado —
                # é o comportamento do JARVIS original, e voltou a ser o nosso.
                # Guardar para entregar depois foi tentado em 30/08 e o
                # registro provou que fazia mal: ver _GUARDAR_FALA_POR_CIMA.
                # O bloco abaixo só roda se aquela chave for religada.
                if (self._GUARDAR_FALA_POR_CIMA and jarvis_speaking
                        and not self.ui.muted
                        and not self._tool_busy and not self._phone_active):
                    if self._PORTAO_LIGADO:
                        guardar = self._audio_com_voz(indata)
                    else:
                        guardar = [indata.tobytes()]
                    for pedaco in guardar:
                        self._fala_guardada.append(pedaco)
                        self._fala_guardada_bytes += len(pedaco)
                    # Teto de ~10 s: se ele falou muito, vale o mais RECENTE.
                    while self._fala_guardada_bytes > self._GUARDAR_FALA_MAX_BYTES:
                        self._fala_guardada_bytes -= len(self._fala_guardada.pop(0))
                else:
                    self._preroll.clear()
                return
            # MODO LOCAL: nada sobe para a Gemini. O bloco e guardado aqui e a
            # fala inteira vai para o whisper quando o rabicho fechar. Ainda
            # chamamos `_audio_com_voz` porque e ele que detecta a voz e mantem
            # `_voz_ate` — so ignoramos o que ele devolve.
            if self._modo_local:
                self._audio_com_voz(indata)
                bruto_bloco = indata.tobytes()
                agora_l = time.monotonic()
                # RABICHO ANTI-ECO (30/08, 17h): eu tinha tirado isto ao igualar
                # ao Mark-LI, e no modo local ele faz falta — o Kokoro toca pela
                # caixa e o microfone pega o fim da propria fala do JARVIS, que
                # entra no whisper como palavra solta. Meio segundo depois de ele
                # calar resolve, sem comer o comeco da fala do dono.
                if agora_l < self._mic_fechado_ate:
                    self._mic_bloqueado += 1
                    return
                if agora_l <= self._voz_ate:
                    if not self._buf_local and self._pre_local:
                        self._buf_local.extend(self._pre_local)
                    self._buf_local.append(bruto_bloco)
                    # Teto de fala. Eram 950 blocos = 38 s, e o Diego fala
                    # explicacao de um minuto para o video — passando disso, o
                    # `pop(0)` come o COMECO da frase dele. 2000 blocos = 80 s,
                    # que ainda barra microfone aberto a esmo e custa 2,5 MB.
                    if len(self._buf_local) > 2000:
                        self._buf_local.pop(0)
                else:
                    # RABICHO DE TRAS: 25 blocos = 1 segundo (era 10 = 0,4 s).
                    #
                    # A deteccao so abre depois de `_BLOCOS_PARA_ABRIR` blocos
                    # acima do piso, e a primeira silaba sai mais baixa que o
                    # resto da frase — com o piso em 750 ela demora mais para
                    # cruzar. Tudo o que veio antes disso vive AQUI, e e este
                    # trecho que e colado na frente da fala.
                    #
                    # Meio segundo era curto e comia o comeco ("Ó Jarvis" virava
                    # "Jarvis"). Um segundo cobre com folga e custa 32 KB de RAM.
                    self._pre_local.append(bruto_bloco)
                    if len(self._pre_local) > 25:
                        self._pre_local.pop(0)
                return

            if self._PORTAO_LIGADO:
                pedacos = self._audio_com_voz(indata)
            else:
                pedacos = [indata.tobytes()]
            for data in pedacos:
                loop.call_soon_threadsafe(
                    self._queue_realtime_audio,
                    data,
                )

        last_error = ""
        while True:
            try:
                # A WASAPI RECUSA 16 kHz na maioria das placas ("Invalid sample
                # rate", PaErrorCode -9997): o aparelho roda em outra taxa e ela
                # nao converte sozinha. `WasapiSettings(auto_convert=True)` manda
                # o Windows converter — a MESMA solucao que ja existia para o
                # alto-falante, que eu esqueci de aplicar na entrada e deixei o
                # Diego sem microfone por alguns minutos.
                #
                # E se mesmo assim falhar, cai para o padrao do sistema: e melhor
                # ouvir pela MME do que nao ouvir.
                # Escolha do cliente (engrenagem) vence tudo. Se ele nunca
                # escolheu, procura sozinho um que FUNCIONE — testando, nao
                # adivinhando pelo nome.
                if getattr(self, "_mic_escolhido", "nao-definido") != "nao-definido":
                    _dispositivo = self._mic_escolhido
                else:
                    # ADIVINHAR PELO NOME NAO SERVE — provado em 30/08. Meu
                    # `_melhor_entrada_de_audio` escolhia o "Microfone (Realtek
                    # USB MIC)" pela WASAPI, e ele recusava com "Invalid number
                    # of channels": o aparelho tem 2 canais na WASAPI e o JARVIS
                    # pede 1. Enquanto isso o PADRAO do sistema funcionava.
                    #
                    # Agora quem escolhe e o `escolher_automatico`, que ABRE e
                    # MEDE os blocos de cada candidato. So confia no que capturou.
                    try:
                        from core.audio_dispositivos import escolher_automatico
                        _dispositivo, _porque = escolher_automatico()
                        _voice_diag("microfone_automatico", _porque[:120])
                    except Exception as _exc_auto:
                        _voice_diag("microfone_automatico_falhou", str(_exc_auto)[:110])
                        _dispositivo = None
                _ajustes = None
                if _dispositivo is not None:
                    # SO NA WASAPI (17/09/2026). O automatico tambem escolhe
                    # aparelho da MME, e ajuste de WASAPI nele da "Incompatible
                    # host API specific stream info" — o microfone escolhido
                    # nunca abria e caia sempre no padrao.
                    try:
                        if "WASAPI" in sd.query_hostapis(
                                sd.query_devices(_dispositivo)["hostapi"])["name"]:
                            _ajustes = sd.WasapiSettings(auto_convert=True)
                    except Exception:
                        _ajustes = None

                def _abrir(dispositivo, ajustes):
                    return sd.InputStream(
                        samplerate=SEND_SAMPLE_RATE,
                        channels=CHANNELS,
                        dtype="int16",
                        blocksize=MIC_CHUNK_SIZE,
                        callback=callback,
                        device=dispositivo,
                        extra_settings=ajustes,
                    )

                _mic_aberto = _dispositivo
                try:
                    _fluxo = _abrir(_dispositivo, _ajustes)
                    if _dispositivo is not None:
                        _voice_diag("microfone_escolhido",
                                    f"[{_dispositivo}] "
                                    f"{sd.query_devices(_dispositivo).get('name', '?')[:50]}")
                except Exception as _exc_dev:
                    _voice_diag("microfone_caiu_para_padrao",
                                f"{type(_exc_dev).__name__}: {str(_exc_dev)[:100]}")
                    _fluxo = _abrir(None, None)
                    _mic_aberto = None
                # O PERFIL E DO APARELHO, PELO NOME (01/10/2026): o numero do
                # dispositivo muda quando se pluga outro USB; o nome nao.
                try:
                    from core.perfil_audio import perfil as _perfil_audio  # noqa: PLC0415
                    _info_mic = (sd.query_devices(_mic_aberto) if _mic_aberto is not None
                                 else sd.query_devices(kind="input"))
                    _perfil_audio(str(_info_mic.get("name") or "padrao").strip())
                except Exception as _exc_perf:
                    _voice_diag("perfil_audio_falhou", str(_exc_perf)[:90])

                with _fluxo:
                    print("[JARVIS] 🎤 Mic stream open")
                    if last_error:
                        self.ui.write_log("SIS: Microfone de volta.")
                    last_error = ""
                    self._mic_ultimo_bloco = time.monotonic()
                    while True:
                        # Mede se o PROGRAMA travou. Este laço pede para
                        # dormir 0,1 s; se acordar muito depois disso, é porque
                        # alguma coisa segurou o Python e mais nada rodou nesse
                        # tempo — nem receber áudio da internet, nem entregá-lo
                        # à caixa de som. É o que separa "a internet engasgou"
                        # de "nós travamos". Sem esta medida, consertar o
                        # picote da voz é chute.
                        _antes = time.monotonic()
                        await asyncio.sleep(0.1)
                        # O JARVIS terminou de falar: entrega agora o que o
                        # cliente disse enquanto ele falava. Ver o callback.
                        self._entregar_fala_guardada()
                        _atraso = time.monotonic() - _antes - 0.1
                        # Guarda o PIOR atraso da janela e QUANTAS vezes travou.
                        # A versão anterior registrava só o primeiro e calava os
                        # 5 s seguintes — o que escondia exatamente o caso que
                        # interessa: travar muitas vezes em sequência. Sem estes
                        # dois números não dá para separar "a rede engasgou" de
                        # "o programa parou de rodar".
                        if _atraso > 0.25:
                            self._travadas_na_janela += 1
                            if _atraso > self._pior_travada:
                                self._pior_travada = _atraso
                        if (self._travadas_na_janela
                                and (_antes - self._ultimo_aviso_travou) > 5.0):
                            self._ultimo_aviso_travou = _antes
                            _voice_diag(
                                "programa_travou",
                                f"pior={self._pior_travada*1000:.0f}ms "
                                f"vezes={self._travadas_na_janela} em 5s")
                            self._travadas_na_janela = 0
                            self._pior_travada = 0.0
                        # Assim que o portão para de mandar voz, avisa o
                        # servidor que a fala acabou. Ver _fechar_turno_se_parou.
                        await self._fechar_turno_se_parou()

                        # MODO LOCAL: a fala acabou (passou do rabicho) e ha
                        # audio guardado -> whisper, Codex e Kokoro, tudo fora
                        # do laco para nao segurar o microfone.
                        # ENQUANTO O RABICHO CORRE, O WHISPER JA TRABALHA.
                        # Ver `_ouvir_adiantado`: quando a espiada vale, sao
                        # ~0,5 s a menos entre o dono calar e o texto aparecer.
                        if (self._modo_local and not self._local_ocupado
                                and self._buf_local
                                and time.monotonic() <= self._voz_ate):
                            self._ouvir_adiantado()

                        if (self._modo_local and not self._local_ocupado
                                and self._buf_local
                                and time.monotonic() > self._voz_ate):
                            fala = b"".join(self._buf_local)
                            self._buf_local = []
                            # QUANTO DA ESPERA FOI RABICHO — registrado para a
                            # proxima vez que ele disser "demora para minha voz
                            # chegar no chat". Sem este numero a conversa vira
                            # palpite: o ouvido leva 0,3 a 1,3 s e o rabicho
                            # pode levar 0,6 ou 2,6, e sao coisas diferentes de
                            # consertar.
                            _longo = self._voz_falada_s > self._FALA_LONGA_S
                            _voice_diag(
                                "turno_fechado",
                                f"voz={self._voz_falada_s:.1f}s "
                                f"rabicho={'longo' if _longo else 'curto'} "
                                f"({self._RABICHO_LONGO_S if _longo else self._RABICHO_S:.1f}s)")
                            # A conta da voz e por TURNO. O valor ANTES de zerar
                            # e a regua da espiada: se ela viu esta mesma
                            # quantidade de voz, cobriu a fala inteira.
                            self._voz_ao_fechar = self._voz_falada_s
                            self._voz_falada_s = 0.0
                            # `_adiantado_voz` NAO se zera aqui: e por ele que a
                            # thread do turno sabe se a espiada que ainda esta
                            # correndo e desta fala. Zerando, a espera nunca
                            # acontecia e o whisper rodava duas vezes.
                            self._local_ocupado = True
                            threading.Thread(target=self._pipeline_local,
                                             args=(fala,), daemon=True).start()

                        # VIGIA DO MICROFONE REMOVIDO em 30/08/2026, a pedido
                        # do Diego, e com razao.
                        #
                        # Eu o criei para um caso real: o Windows invalidou o
                        # aparelho e o PortAudio parou de chamar o callback sem
                        # levantar erro nenhum. Aconteceu UMA vez.
                        #
                        # Mas o remedio ficou pior que a doenca. Ele passou a
                        # oscilar — "parou de responder" / "de volta" / "parou"
                        # — porque reabrir o fluxo causa o proximo engasgo, que
                        # dispara a proxima reabertura. Realimentacao. Enchia a
                        # tela do cliente de aviso e derrubava a captura boa.
                        #
                        # O Mark-LI, que funciona, nao tem vigia nenhum aqui: ele
                        # abre o `sd.InputStream` e confia. Quando o aparelho
                        # morre de verdade, o `except` de baixo pega e reabre.
                        #
                        # Se o microfone parar mesmo, agora existe saida melhor e
                        # que nao briga com nada: a engrenagem tem o seletor de
                        # MICROFONE, com teste por aparelho.

            except asyncio.CancelledError:
                raise
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
                print(f"[JARVIS] ❌ Mic: {error}")
                _voice_diag_exception("microphone_error", e)
                if error != last_error:
                    self.ui.write_log(
                        "AVISO: microfone indisponível; tentando novamente sem interromper o JARVIS."
                    )
                    last_error = error
                await asyncio.sleep(5)

    # ── Vigia da conexão ──────────────────────────────────────────────────────
    # Sintoma que isto resolve: o JARVIS continuava aberto mas parava de
    # responder, e só voltava quando o cliente fechava e abria o programa.
    #
    # Causa: quando a conexão com o servidor de voz cai de um jeito silencioso
    # (a internet oscila, o servidor encerra a sessão por tempo, o Wi-Fi troca
    # de rede), o soquete não avisa que morreu. A espera por respostas fica
    # pendurada para sempre, nenhuma exceção acontece, e o laço que reconecta
    # nunca é acionado. Por fora tudo parece normal — só que ninguém responde.
    #
    # Solução: se o cliente mandou algo e passou tempo demais sem UMA resposta
    # sequer, damos a conexão por morta e forçamos a reconexão, que já existe.

    _ESPERA_MAXIMA_S = 45.0     # sem nenhuma resposta depois de um pedido escrito
    # Folgado de proposito. Com a cota da chave no limite, o servico chega a
    # demorar VARIOS MINUTOS para responder — visto ao vivo: 4 minutos. Um
    # limite curto aqui cortaria a resposta que ainda estava a caminho e
    # prenderia o JARVIS num ciclo de reconexao, pior que o defeito original.
    # Sem NADA vindo do servidor DEPOIS de o cliente falar. Eram 360 s, folga
    # posta na epoca em que o microfone mandava audio o tempo todo e o silencio
    # do servidor era normal. Agora so contamos a espera quando realmente
    # enviamos voz — entao ficar 90 s sem uma unica resposta significa sessao
    # morta. Isto acontece de verdade: quando a cota do minuto estoura, o
    # servidor para de processar mas NAO fecha a conexao; ela fica de pe e muda,
    # e o cliente falava por minutos sem retorno.
    # Quanto esperar depois de MANDAR voz e não voltar absolutamente nada.
    # Agora que a contagem começa no envio (e não no silêncio acumulado), este
    # tempo pode ser generoso: derrubar uma sessão viva custa a conversa
    # inteira, enquanto perceber uma conexão morta 45 s mais tarde custa pouco.
    # Uma fala que o modelo decide, com razão, não responder também arma este
    # relógio — mais um motivo para não ser apertado.
    _SILENCIO_MAXIMO_S = 90.0
    # Quanto esperar quando o servidor não deu sinal NENHUM depois de recebermos
    # áudio — nem a transcrição do que foi falado. Folgado de propósito: o caso
    # comum aqui não é conexão morta, é ruído que passou pelo portão e que o
    # servidor ignorou corretamente. Derrubar a sessão nessa hora custa a
    # conversa inteira e enche a tela de "Conexão perdida" à toa.
    _SEM_SINAL_MAXIMO_S = 240.0
    # A partir de quanta voz enviada sem NENHUM sinal de volta deixamos de dar
    # o benefício da dúvida ao servidor. Abaixo disto é ruído curto, e ignorá-lo
    # é o comportamento certo dele; acima, é frase de verdade sendo ignorada —
    # sessão travada, e aí esperar 4 minutos é inaceitável.
    _VOZ_MINIMA_PARA_COBRAR_S = 1.5
    # Ar morto tolerado quando o cliente falou de verdade e o servidor não deu
    # sinal NENHUM. Sessão do Live morre em silêncio; quanto antes percebermos,
    # menos tempo ele fala sozinho. A conversa não se perde: o código de
    # retomada é preservado e a reconexão leva ~1,2 s.
    # Subiu de 35 s para 60 s junto com a mudança acima. Agora que o relógio só
    # arma com voz reconhecida, um disparo à toa custa a conversa inteira e uma
    # conexão nova no limite do Google — enquanto esperar 25 s a mais no caso
    # raro de sessão morta não custa quase nada. O cliente reclamou exatamente
    # disto: "esse erro que aparece no chat, e quando acontece ele para de falar".
    _VOZ_IGNORADA_MAXIMO_S = 60.0

    def _marcar_pedido_pendente(self) -> None:
        """Registra que o cliente pediu algo e ainda não obteve resposta."""
        if self._aguardando_desde is None:
            self._aguardando_desde = time.monotonic()

    def _retrato(self) -> str:
        """Estado das travas, para o registro de diagnostico."""
        try:
            with self._speaking_lock:
                falando = self._is_speaking
        except Exception:
            falando = "?"
        parado = (time.monotonic() - self._ultimo_audio_tocado) if self._ultimo_audio_tocado else -1
        return (f"sessao={'sim' if self.session else 'NAO'} "
                f"falando={falando} ferramenta={self._tool_busy} "
                f"mudo={getattr(self.ui, 'muted', '?')} celular={self._phone_active} "
                f"fila_saida={self.out_queue.qsize() if self.out_queue else '-'} "
                f"fila_voz={self.audio_in_queue.qsize() if self.audio_in_queue else '-'} "
                f"sem_tocar_ha={parado:.0f}s "
                f"MIC captou={self._mic_capturado} bloqueado={self._mic_bloqueado} "
                f"na_fila={self._mic_na_fila} enviado={self._mic_enviado} "
                f"silencio_cortado={self._mic_silencio} "
                f"ruido={self._ruido_fundo:.0f} pico={self._nivel_pico:.0f} "
                f"limiar={max(self._ruido_fundo * self._fator_ruido, self._PISO_DE_VOZ):.0f} "
                f"TOKENS entrada={self._tokens_entrada} saida={self._tokens_saida} "
                f"retomada={'sim' if self._codigo_retomada else 'nao'} "
                f"turno={'ABERTO' if self._turno_aberto else 'fechado'} "
                f"fechados={self._turnos_fechados}")

    async def _vigia_sessao(self) -> None:
        ultimo_retrato = 0.0
        ultimo_aviso = 0.0
        while True:
            await asyncio.sleep(5)
            agora = time.monotonic()

            # De 30 em 30 segundos sem nada vindo do servidor, guarda uma foto
            # do estado. E o que permite descobrir a causa quando o cliente diz
            # "parou de responder" — sem isto, o registro fica vazio.
            calado = agora - (self._ultima_resposta or agora)
            if calado > 30 and (agora - ultimo_retrato) > 30:
                ultimo_retrato = agora
                _voice_diag("estado", f"calado_ha={calado:.0f}s {self._retrato()}")

            # 0) Microfone mudo ha tempo demais por causa de uma ferramenta.
            # Rede de seguranca: se qualquer caminho novo esquecer de religar o
            # microfone, o cliente nao fica falando sozinho para sempre.
            if self._tool_busy and self._tool_busy_desde:
                mudo_ha = agora - self._tool_busy_desde
                # ERA 90 s, e isso virou defeito (30/08/2026).
                #
                # Esta soltura existe para trava PRESA — o defeito antigo, de
                # quando o bloco da ferramenta nao tinha `finally`. Hoje tem, e
                # `_tool_busy` volta a False de qualquer jeito quando a ferramenta
                # termina. Com 90 s, o que ela fazia era soltar o microfone NO MEIO
                # de uma tarefa que ainda estava rodando: a edicao de imagem leva
                # 92 s (medido). Ai o microfone voltava a mandar audio para uma
                # sessao que ainda esperava a resposta da ferramenta, abrindo turno
                # novo por cima do turno pendurado — e a resposta da tarefa saia
                # atrasadissima ou nao saia. Era o "depois que ele termina a tarefa
                # demora muito ou nem responde" que o Diego relatou.
                #
                # 900 s = o teto de 15 min de um trabalho no servidor. Continua
                # sendo rede de seguranca de ultimo caso, sem competir com o
                # `finally`, que e quem solta no caminho normal.
                if mudo_ha >= 900:
                    self._tool_busy = False
                    self._tool_busy_desde = 0.0
                    _voice_diag("mic_destravado", f"{mudo_ha:.0f}s")
                    print(f"[JARVIS] Microfone estava mudo ha {mudo_ha:.0f}s — religado.")

            # 0b) Marcado como "falando" ha tempo demais sem tocar nada.
            # Enquanto isso durar, o microfone fica mudo — e o cliente acha que
            # o JARVIS o esta ignorando.
            try:
                with self._speaking_lock:
                    falando = self._is_speaking
            except Exception:
                falando = False
            if (falando and self._ultimo_audio_tocado
                    and (agora - self._ultimo_audio_tocado) > 20
                    and (not self.audio_in_queue or self.audio_in_queue.empty())):
                _voice_diag("fala_destravada", f"{agora - self._ultimo_audio_tocado:.0f}s")
                self.set_speaking(False)
                self._aquecer_audio = True

            # 1) O cliente escreveu algo e nada voltou.
            inicio = self._aguardando_desde
            if inicio is not None and (agora - inicio) >= self._ESPERA_MAXIMA_S:
                self._aguardando_desde = None
                await self._reconectar_por_silencio(agora - inicio, "pedido sem resposta")
                continue   # vigia desarmado: segue vigiando em vez de encerrar

            # 2) Conversando por VOZ não existe "pedido" para marcar, e era
            # justamente aí que o JARVIS ficava mudo sem ninguém perceber: a
            # conexão morria em silêncio e a regra acima nunca disparava.
            # Com o microfone ligado, o servidor devolve algo com frequência —
            # nem que seja a transcrição do que foi falado. Silêncio total por
            # muito tempo significa que a conexão morreu.
            if self.ui.muted or self._phone_active:
                continue
            # Desde que o microfone parou de mandar o silêncio da sala, ficar
            # sem resposta é o NORMAL: se ninguém falou, não há o que responder.
            # Cobrar resposta desse silêncio derrubaria a conexão de 6 em 6
            # minutos à toa, gastando cota e perdendo o fio da conversa.
            # Só é suspeito quando mandamos voz e nada voltou.
            envio = self._ultimo_envio_de_voz or 0.0
            # Enquanto uma ferramenta roda (pesquisa, imagem, WhatsApp) o
            # servidor fica calado DE PROPÓSITO: ele está esperando o resultado
            # que só nós podemos mandar. Cobrar resposta dele aqui derrubava a
            # sessão no meio do trabalho, e o cliente via o pedido evaporar.
            if self._tool_busy:
                self._envio_cobravel = 0.0
                continue
            cobravel  = self._envio_cobravel
            esperando = (agora - cobravel) if cobravel else 0.0
            # Deixa registrado POR QUE o vigia agiu ou não. Sem isto ficava
            # impossível saber se ele estava dormindo ou apenas achando, com
            # razão, que não havia nada a cobrar.
            if cobravel and esperando > 30 and (agora - ultimo_aviso) > 30:
                ultimo_aviso = agora
                _voice_diag(
                    "vigia",
                    f"calado={calado:.0f}s esperando_ha={esperando:.0f}s "
                    f"falei_ha={(agora - envio) if envio else -1:.0f}s "
                    f"limite={self._SILENCIO_MAXIMO_S:.0f}s",
                )
            # Conta desde a PRIMEIRA voz enviada depois da última resposta —
            # nunca do silêncio acumulado do servidor.
            #
            # O defeito que isto corrige, com prova no registro do cliente:
            # em 30/08 às 00:14:14 a sessão estava ociosa há 121 s (ninguém
            # tinha falado). Um estouro de ruído passou pelo portão do
            # microfone, a condição antiga "mandei voz depois da última
            # resposta" virou verdadeira com o contador JÁ em 121 s, e a
            # conexão caiu NA HORA — exatamente quando o cliente começava a
            # falar. Daí as três queixas de uma vez: ele desconecta sozinho,
            # demora, e depois de reconectar não responde mais.
            # DOIS relógios, porque são dois casos bem diferentes:
            #
            # 1) O servidor TRANSCREVEU a fala do cliente e depois emudeceu.
            #    Aí ele deve mesmo uma resposta, e 90 s é muito tempo.
            ouviu = self._servidor_ouviu_em
            if ouviu and (agora - ouviu) >= self._SILENCIO_MAXIMO_S:
                self._servidor_ouviu_em = 0.0
                self._envio_cobravel = 0.0
                await self._reconectar_por_silencio(agora - ouviu, "ouviu e não respondeu")
                continue   # vigia desarmado: segue vigiando em vez de encerrar
            # 2) Mandamos áudio e o servidor não deu sinal NENHUM — nem a
            #    transcrição. Pode ser conexão morta, mas também pode ser
            #    apenas ruído que passou pelo portão e que o servidor ignorou,
            #    com toda a razão. Como o segundo caso é comum e derrubar uma
            #    sessão viva custa a conversa inteira, este relógio é bem mais
            #    folgado. Em 30/08 era ele, com 90 s, que enchia a tela de
            #    "Conexão perdida" sem nenhum defeito de rede.
            # Quanta VOZ mandamos sem receber sinal nenhum de volta. Meio
            # segundo é ruído — o servidor ignorar aquilo é o certo. Uma frase
            # inteira ignorada é sessão travada, e aí não dá para esperar 4
            # minutos: foi o que aconteceu em 30/08, com 5 turnos fechados e o
            # servidor mudo, sem nem transcrever.
            # O medidor por BYTES saiu de cena. Ele fazia sentido enquanto o
            # portão só deixava passar fala; depois que o microfone passou a
            # enviar contínuo (como o original), "bytes enviados" virou o
            # silêncio da sala — e o vigia derrubava a sessão a cada 35 s do
            # cliente calado, dizendo "sem sinal apos 37.6s de voz".
            # Agora o relógio só está armado quando o DETECTOR reconheceu voz,
            # então um único limite basta: falamos e não veio sinal nenhum,
            # nem a transcrição. As respostas reais chegam em 1 a 3 s, e a
            # ferramenta demorada já está excluída lá em cima pelo `_tool_busy`.
            if cobravel and esperando >= self._VOZ_IGNORADA_MAXIMO_S:
                self._envio_cobravel = 0.0
                await self._reconectar_por_silencio(
                    esperando, "falei e o servidor nao deu sinal")
                continue   # vigia desarmado: segue vigiando em vez de encerrar

    async def _reconectar_por_silencio(self, parado: float, motivo: str) -> None:
        # VIGIA DESARMADO em 30/08/2026, a pedido do Diego.
        #
        # O Mark-LI, que aguenta horas na cota gratis, NAO TEM VIGIA NENHUM: ele
        # so reconecta quando a conexao da erro de verdade. O nosso derrubava a
        # sessao depois de 60 s sem sinal do servidor — e isso virava bola de
        # neve exatamente quando mais doia: cota estrangulada -> resposta demora
        # -> o vigia mata uma sessao VIVA -> abre outra -> bate no limite de
        # ABERTURA DE SESSAO -> 1011 -> repete. Registro de 30/08: 23 tentativas
        # de conexao no dia, e o Diego esperando 5 minutos por uma resposta que
        # ja estava a caminho quando o vigia cortou.
        #
        # Continua registrando para o diagnostico nao cegar — so nao reconecta.
        _voice_diag("vigia_teria_reconectado", f"{motivo} {parado:.0f}s (desarmado)")
        return

        print(f"[JARVIS] {motivo}: {parado:.0f}s — reconectando.")
        _voice_diag("session_watchdog_restart", f"{motivo} {parado:.0f}s")
        try:
            # "Conexão perdida" assustava o cliente e quase sempre era mentira:
            # na maioria das vezes a rede estava boa e a conversa continuava
            # inteira, porque o código de retomada é preservado. Dizer o que
            # realmente acontece vale mais do que alarmar.
            self.ui.write_log("SIS: Destravando a conversa — um instante, sem perder nada.")
        except Exception:
            pass
        # Derruba o grupo de tarefas: o laço de fora reconecta em seguida.
        raise ConnectionError("Sem resposta do servidor de voz; reconectando.")

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []
        turn_had_model_audio = False
        turn_had_tool_call = False

        try:
            while True:
                # ATENÇÃO ao ler este laço: `session.receive()` do SDK termina
                # A CADA TURNO COMPLETO (ele dá `break` no `turn_complete`), e
                # NÃO quando a sessão morre. É por isso que existe o `while
                # True` em volta: ele reabre o iterador para o turno seguinte.
                # Em 30/08 eu li errado, tratei o fim do `receive()` como
                # sessão encerrada, e o JARVIS passou a RECONECTAR A CADA
                # RESPOSTA — o que estourou a cota da chave em minutos.
                recebeu_neste_turno = False
                async for response in self.session.receive():
                    # Chegou qualquer coisa do servidor: a conexao esta viva.
                    recebeu_neste_turno = True
                    self._recebeu_algo = True
                    self._ultima_resposta = time.monotonic()
                    self._aguardando_desde = None
                    # Respondeu: desarma os dois relogios do vigia. A proxima
                    # voz que sair volta a armar.
                    self._envio_cobravel = 0.0
                    self._bytes_cobraveis = 0
                    if getattr(response, "data", None):
                        self._servidor_ouviu_em = 0.0

                    # O servidor AVISA quando vai encerrar a sessão, com o tempo
                    # que ainda resta. Este aviso nunca era lido — e por isso a
                    # sessão simplesmente morria em silêncio: o cliente falava,
                    # falava, e só depois de 91 s de nada o vigia percebia.
                    # Registro de 30/08: último sinal do servidor às 01:44:21,
                    # 22,9 s de fala enviados depois disso, nem transcrição de
                    # volta, reconexão só às 01:45:55.
                    # Agora reconectamos NA HORA do aviso, com o código de
                    # retomada em mãos: a conversa continua e o cliente não fica
                    # falando sozinho.
                    adeus = getattr(response, "go_away", None)
                    if adeus is not None:
                        resta = getattr(adeus, "time_left", None)
                        _voice_diag("servidor_vai_encerrar", f"resta={resta}")
                        raise ConnectionError(
                            "Sem resposta do servidor de voz; reconectando.")

                    # Guarda o código de retomada que o servidor manda de tempos
                    # em tempos. É o que faz a próxima conexão continuar esta
                    # sessão em vez de começar outra do zero.
                    atualizacao = getattr(response, "session_resumption_update", None)
                    if atualizacao is not None and getattr(atualizacao, "resumable", False):
                        novo = getattr(atualizacao, "new_handle", None)
                        if novo:
                            self._codigo_retomada = novo

                    # Gasto real informado pelo servidor.
                    uso = getattr(response, "usage_metadata", None)
                    if uso is not None:
                        try:
                            self._tokens_entrada = int(getattr(uso, "prompt_token_count", 0) or 0)
                            self._tokens_saida = int(getattr(uso, "response_token_count", 0) or 0)
                        except Exception:
                            pass

                    # Marque antes de processar server_content: alguns eventos podem
                    # carregar tool_call e turn_complete na mesma mensagem.
                    if response.tool_call and response.tool_call.function_calls:
                        turn_had_tool_call = True

                    if response.data:
                        # Mede o RITMO de chegada do áudio. É o que separa
                        # "a voz trava porque o som chega devagar pela internet"
                        # de "a voz trava porque nós tocamos mal". Sem medir,
                        # qualquer conserto aqui é chute.
                        _ag = time.monotonic()
                        if self._ritmo_inicio == 0.0:
                            self._ritmo_inicio = _ag
                            self._ritmo_bytes = 0
                            self._ritmo_maior_buraco = 0.0
                        elif self._ritmo_ultimo:
                            _buraco = _ag - self._ritmo_ultimo
                            if _buraco > self._ritmo_maior_buraco:
                                self._ritmo_maior_buraco = _buraco
                        self._ritmo_ultimo = _ag
                        self._ritmo_bytes += len(response.data)

                        if self._esperando_voz and self._falou_em:
                            _voice_diag(
                                "latencia_resposta",
                                f"{time.monotonic() - self._falou_em:.1f}s",
                            )
                            self._esperando_voz = False
                        self._tool_busy = False
                        if self._suppress_model_audio_until_turn_complete:
                            # A confirmação mutável do WhatsApp é falada pelo
                            # sintetizador literal. Qualquer paráfrase do modelo
                            # neste turno é descartada para impedir confirmação dupla.
                            pass
                        else:
                            # A resposta do modelo começou: a fase de ferramenta terminou.
                            # Marcar speaking aqui fecha a pequena janela antes do player
                            # consumir o primeiro bloco e impede eco/novo áudio nesse intervalo.
                            # O JARVIS vai começar a falar. O microfone para de
                            # captar agora, MAS o que já estava na fila de saída
                            # continuaria sendo enviado — inclusive o rabicho de
                            # 2 s de som da sala (ventilador, ar-condicionado,
                            # televisão). O servidor lê esse som como "o cliente
                            # voltou a falar" e CANCELA a própria resposta no
                            # meio. Era o "ele para de falar no meio da frase".
                            # Fora do turno o portão já barra o ventilador (o
                            # piso subiu para 260); aqui barramos o que ficou
                            # para trás.
                            if not self._is_speaking:
                                self._drain_realtime_queue()
                                self._turno_aberto = False
                            self.set_speaking(True)
                            turn_had_model_audio = True
                            if self._interrupted:
                                pass  # discard: interrupted
                            else:
                                if self._turn_done_event and self._turn_done_event.is_set():
                                    self._turn_done_event.clear()
                                # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                                # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                                _audio_data = response.data
                                _SLICE = 2400
                                # MODO LOCAL: joga fora o audio da Gemini. Ela
                                # continua sendo o cerebro (com as 28 ferramentas
                                # e a personalidade), mas quem FALA e o Kokoro, na
                                # transcricao que ela manda junto. Nenhum modelo ao
                                # vivo desta chave aceita saida em TEXTO — todos
                                # sao de audio nativo (testado em 30/08) — entao
                                # descartar o audio e a unica forma de trocar a boca.
                                if not self._modo_local:
                                    for _i in range(0, len(_audio_data), _SLICE):
                                        self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        # O servidor avisa quando CANCELA a resposta porque
                        # entendeu que o cliente voltou a falar. Este aviso
                        # nunca era lido: a fala já cancelada continuava saindo
                        # pela caixa de som e se misturava com a seguinte —
                        # era a voz "toda travando". Agora o que já não vale
                        # mais é jogado fora na hora.
                        if getattr(sc, "interrupted", False):
                            self._interrupted = True
                            self._drain_audio_queue()
                            self.set_speaking(False)
                            # Registra O QUE o servidor entendeu que ouviu. É a
                            # única forma de saber se ele foi interrompido pela
                            # voz do cliente, pelo ventilador, ou pela PRÓPRIA
                            # voz do JARVIS voltando pela caixa de som.
                            _voice_diag(
                                "servidor_interrompeu",
                                f"ouviu={(self._current_input_transcript or '(nada transcrito)')[:90]!r}",
                            )

                        if (
                            not self._suppress_model_audio_until_turn_complete
                            and sc.output_transcription
                            and sc.output_transcription.text
                        ):
                            _append_streaming_transcript(out_buf, sc.output_transcription.text)

                        if sc.input_transcription and sc.input_transcription.text:
                            # O servidor CONFIRMOU que ouviu fala de verdade.
                            # Só a partir daqui ele nos deve uma resposta — e
                            # só a partir daqui o vigia tem o direito de cobrar.
                            # Antes bastava termos ENVIADO áudio, e um ruído que
                            # passasse pelo portão armava o cronômetro: o
                            # servidor ignorava o ruído (certíssimo) e o vigia
                            # derrubava uma sessão viva 90 s depois. Era o
                            # "Conexão perdida" aparecendo à toa.
                            self._servidor_ouviu_em = time.monotonic()
                            # O servidor transcreveu a fala: o cliente falou
                            # AGORA. A partir daqui contamos a espera.
                            self._falou_em = time.monotonic()
                            self._esperando_voz = True
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                _append_streaming_transcript(in_buf, txt)
                                self._current_input_transcript = " ".join(in_buf).strip()
                                self._last_user_speech = time.monotonic()

                        if sc.turn_complete and self._ritmo_inicio:
                            # segundos de fala recebidos x tempo que levaram
                            # para chegar. folga positiva = chegou mais rápido
                            # do que se toca (a caixa nunca fica sem som);
                            # folga negativa = a caixa PRECISA esperar, e é
                            # exatamente isso que o cliente ouve como travada.
                            _fala = self._ritmo_bytes / (RECEIVE_SAMPLE_RATE * 2)
                            _janela = max(1e-6, (self._ritmo_ultimo or 0.0) - self._ritmo_inicio)
                            _voice_diag(
                                "ritmo_audio",
                                f"fala={_fala:.2f}s chegou_em={_janela:.2f}s "
                                f"folga={_fala - _janela:+.2f}s "
                                f"maior_buraco={self._ritmo_maior_buraco*1000:.0f}ms "
                                f"{'OK' if _fala - _janela > 0.3 else 'STARVA'}",
                            )
                            self._ritmo_inicio = 0.0
                            self._ritmo_ultimo = 0.0

                        if sc.turn_complete:
                            suppressed_model_turn = self._suppress_model_audio_until_turn_complete
                            self._suppress_model_audio_until_turn_complete = False
                            self._tool_busy = False
                            # Turno encerrado pelo servidor: ele não deve mais
                            # nada. Sem isto o vigia continuaria cobrando uma
                            # resposta que já veio (ou que o modelo decidiu,
                            # com razão, não dar).
                            self._servidor_ouviu_em = 0.0
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                turn_had_model_audio = False
                                turn_had_tool_call = False
                                continue

                            full_in = _collapse_immediate_transcript_repetition(" ".join(in_buf).strip())
                            # O QUE o servidor entendeu da fala do cliente.
                            # É a única medida que separa "o portão está
                            # cortando a voz dele" (chega picado ou vem <noise>)
                            # de "chegou inteiro e o modelo é que não respondeu".
                            # Sem isto, mexer no portão é chute.
                            _voice_diag(
                                "servidor_ouviu",
                                f"{(full_in or '(NADA)')[:110]!r} respondeu_audio={turn_had_model_audio} "
                                f"ferramenta={turn_had_tool_call}",
                            )
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            delete_confirmation_handled = False
                            if full_in:
                                delete_confirmation_handled = await self._handle_pending_delete_confirmation(full_in)
                                if delete_confirmation_handled:
                                    out_buf = []
                            in_buf = []

                            if delete_confirmation_handled:
                                continue

                            full_out = "" if suppressed_model_turn else _collapse_immediate_transcript_repetition(" ".join(out_buf).strip())
                            if full_out:
                                self.ui.write_log(f"{_nome_falante(self.ui)}: {full_out}")
                                # MODO LOCAL: a boca e o Kokoro. Vai para uma
                                # thread porque sintetizar bloqueia, e segurar o
                                # laco de recepcao aqui atrasaria o turno seguinte.
                                if self._modo_local:
                                    threading.Thread(target=self._falar_local,
                                                     args=(full_out,), daemon=True).start()
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            direct_voice_handled = False

                            # Pedido de edição de imagem FALADO. Esta rede já
                            # existia para o texto digitado, mas não para a voz
                            # — e o cliente via exatamente isto: pedia para
                            # editar a foto, o JARVIS respondia "bora, vou
                            # editar, leva um minutinho" e não começava nada,
                            # porque ele só conversou e nunca chamou a
                            # ferramenta. Agora a voz tem a mesma garantia.
                            if (full_in
                                    and not turn_had_tool_call
                                    and not self._edicao_em_curso
                                    and self._pedido_de_edicao_de_imagem(full_in)):
                                self._edicao_em_curso = True
                                _voice_diag("edicao_por_voz", full_in[:80])
                                asyncio.create_task(self._editar_imagem_falada(full_in))
                                direct_voice_handled = True

                            if full_in and not direct_voice_handled:
                                # Rota local de segurança: garante que Vision Control realmente ligue/desligue
                                # mesmo quando o modelo apenas responde em voz e não chama a ferramenta.
                                direct_voice_handled = await self._handle_direct_vision_command(
                                    full_in,
                                    speak_result=not bool(full_out),
                                    source="voice",
                                )

                            # Legacy guard: image injection into the native-audio Live session is disabled.
                            # Webcam/screen analysis now runs through actions.screen_processor synchronously
                            # and returns text as the tool result. This prevents Gemini Live 1007 crashes
                            # caused by sending inline images to an audio-only session.
                            if self._pending_vision:
                                print("[Vision] ⚠️  Legacy pending image suppressed to protect audio session")
                                self._pending_vision = None
                                self._vision_cam_active = False
                                self._vision_close_pending = False
                                self._vision_busy = False

                            retry_silent_turn = _should_retry_silent_voice_turn(
                                full_in,
                                full_out,
                                turn_had_model_audio,
                                turn_had_tool_call,
                                direct_voice_handled,
                            )
                            # O próximo evento já pertence a outro turno.
                            turn_had_model_audio = False
                            turn_had_tool_call = False

                            # Trava de segurança: nunca reenviar o mesmo texto
                            # duas vezes seguidas. Mesmo com o filtro acima, um
                            # transcrito repetido não pode virar um ciclo sem
                            # fim de mensagens ao servidor.
                            if retry_silent_turn and full_in == self._ultimo_reenvio:
                                retry_silent_turn = False
                                print("[JARVIS] Reenvio repetido ignorado.")

                            if retry_silent_turn:
                                self._ultimo_reenvio = full_in
                                # NAO reenviar agora. O servidor as vezes so
                                # esta atrasado, e reenviar na hora empilhava
                                # pedidos repetidos que ele respondia todos de
                                # uma vez - o cliente ouvia as proprias
                                # perguntas sendo respondidas em sequencia.
                                # A tarefa abaixo espera e desiste se a
                                # resposta chegar nesse meio tempo.
                                asyncio.create_task(
                                    self._reenviar_se_continuar_mudo(full_in)
                                )

                    if response.tool_call:
                        fn_responses = []
                        self._tool_busy = True
                        self._tool_busy_desde = time.monotonic()
                        self._drain_realtime_queue()
                        try:
                            coalesced = await self._coalesce_whatsapp_text_calls(
                                response.tool_call.function_calls
                            )
                            if coalesced is not None:
                                fn_responses.extend(coalesced)
                            else:
                                for fc in response.tool_call.function_calls:
                                    print(f"[JARVIS] 📞 {fc.name}")
                                    fr = await self._execute_tool(fc)
                                    fn_responses.append(fr)
                            await self.session.send_tool_response(
                                function_responses=fn_responses
                            )
                            literal_confirmation = self._pending_literal_confirmation
                            self._pending_literal_confirmation = ""
                            if literal_confirmation:
                                await self._enqueue_literal_confirmation(literal_confirmation)
                        finally:
                            _atual = getattr(self, "_ferramenta_atual", None)
                            if _atual:
                                _voice_diag("ferramenta_fim",
                                            f"{_atual[0]} levou {time.monotonic()-_atual[1]:.1f}s")
                                self._ferramenta_atual = None
                            # O microfone fica mudo enquanto a ferramenta roda.
                            # Antes, ele só voltava a ouvir quando o modelo
                            # respondia com ÁUDIO. Se a resposta viesse só como
                            # texto, ou não viesse, o microfone ficava desligado
                            # para sempre: o cliente falava e o JARVIS não
                            # reagia, mas respondia normalmente pelo teclado.
                            # Agora o microfone volta assim que a ferramenta
                            # termina, dê no que der.
                            self._tool_busy = False
                            # A resposta que vem agora sai enquanto o
                            # computador ainda digere o resultado: junta mais
                            # fôlego antes de falar, senão a voz pica.
                            self._aquecer_audio = True
                            self._folga_extra = True

                # Turno que não trouxe NENHUMA mensagem. Um turno normal sempre
                # traz pelo menos uma (nem que seja o `turn_complete`); zero
                # significa que o soquete já não entrega nada. Sem esta guarda o
                # `while True` fica reabrindo um iterador vazio para sempre —
                # laço quente, sem exceção, sem log, e o cliente falando
                # sozinho. Reconectar aqui é seguro porque só acontece quando
                # não veio nada mesmo.
                if not recebeu_neste_turno:
                    _voice_diag("receive_vazio", "sessao nao entrega mais nada")
                    raise ConnectionError("Sem resposta do servidor de voz; reconectando.")
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            _voice_diag_exception("receive_error", e)
            traceback.print_exc()
            raise

    # Folga antes de começar a falar. O áudio vem da internet e não chega em
    # ritmo constante: ora vêm três pedaços juntos, ora nada por um instante.
    # Tocando cada pedaço assim que chega, qualquer atraso vira um corte na voz
    # — foi o que deixou a fala "falhando muito". Guardando um punhado de
    # pedaços antes de começar, esses solavancos somem. O preço é começar a
    # falar ~0,2 s mais tarde, o que não se percebe.
    # Tempo sem nenhum áudio novo para considerar que a fala acabou, mesmo sem
    # o aviso do servidor. 1,2 s é bem mais que a folga entre pedaços de uma
    # mesma frase, então não corta a fala no meio.
    # Rede de segurança para quando o servidor não avisa que terminou de falar.
    # NÃO encurtar: com 1,2 s, uma pausa normal no meio da frase era confundida
    # com o fim da fala. O microfone reabria no meio da resposta, o JARVIS ouvia
    # a própria voz pela caixa de som e se interrompia sozinho — o cliente ouvia
    # a voz "travando toda hora". No caminho normal o servidor avisa e a trava
    # sai na hora; este tempo só existe para o caso em que ele não avisa.
    _FIM_DE_FALA_S = 3.0

    # No MODO LOCAL nada avisa que a fala acabou: nao existe o `turn_complete`
    # do servidor da Gemini. Entao a unica saida e este tempo de silencio — e
    # com 3 s o JARVIS ficava SURDO por tres segundos depois de cada resposta.
    #
    # Sintoma exato do Diego (30/08): "falo 'quero sim, pode fazer' e ele nao
    # inicia; falo de novo e ai vai". A primeira frase caia inteira no buraco.
    #
    # 0,4 s basta: no local a fila de audio ja esta vazia quando a fala termina,
    # entao o silencio comeca a contar imediatamente. Nao ha audio atrasado
    # chegando pela rede, que era o motivo dos 3 s no caminho da Gemini.
    # 0,4 -> 0,25 (05/09/2026). Aqui nao ha audio atrasado chegando pela rede,
    # entao 0,25 s ja distingue pausa de fim.
    _FIM_DE_FALA_LOCAL_S = 0.25

    # A VOZ DE NUVEM PRECISA DA ESPERA GRANDE, MESMO NO MODO LOCAL
    # (22/09/2026). Queixa do Diego: "no final a voz dele cortou toda, ele
    # falava, parava, falava, parava".
    #
    # O registro mostra o mecanismo, cinco vezes seguidas em 40 segundos:
    #     fim_de_fala_por_silencio 0.3s
    #     fim_de_fala_falso        voltou em 0.7s
    #     fim_de_fala_falso        voltou em 1.7s
    #     ...
    # E o medidor novo confirmou: 4,0 s de audio para 138 letras, quando o
    # esperado era 6,6 s.
    #
    # A causa e este numero. Os 0,25 s foram calibrados em 05/09 para o KOKORO,
    # que gera o audio na propria maquina — la a fila esvazia exatamente quando
    # a fala acaba, e 0,25 s ja separa pausa de fim. So que a voz que o Diego
    # usa hoje e a `puck`, que e a sessao AO VIVO DA GEMINI: o audio vem pela
    # internet, em pedacos, e uma pausa de virgula passa fácil de 0,25 s. O
    # comentario do `_FIM_DE_FALA_S` ja dizia isso — "com 1,2 s uma pausa
    # normal no meio da frase era confundida com o fim da fala" — mas a conta
    # olhava o MODO (local x Gemini) em vez de olhar DE ONDE VEM A VOZ.
    #
    # Agora olha a voz. Kokoro continua com 0,25 s (e o microfone reabre
    # rapido, como o Diego gosta); voz de nuvem usa a espera de rede.
    _FIM_DE_FALA_NUVEM_S = 1.2

    def _voz_e_de_nuvem(self) -> bool:
        """A voz de agora vem pela internet?

        A pergunta e feita PELO AVESSO de proposito: quem e local e so o
        Kokoro (as misturas e as vozes puras). Todo o resto — personagem da
        Gemini, voz de estudio da OpenAI, voz pela ponte da VPS — chega pela
        rede, em pedacos, com pausa entre eles.

        Perguntar "esta voz e de nuvem?" pela lista de personagens deixava de
        fora as vozes que NAO sao personagem (a `onyx` do socorro, por
        exemplo), e elas voltavam a ser tratadas como locais — que era
        exatamente o defeito que esta conta existe para consertar.
        """
        try:
            from core.voz_local import MISTURAS, PURAS  # noqa: PLC0415
            nome = str(getattr(self._voz_local, "voz", "") or "")
            return nome not in MISTURAS and nome not in PURAS
        except Exception:
            return True      # na duvida, a espera maior: corta menos fala

    def _espera_fim_de_fala(self) -> float:
        if not getattr(self, "_modo_local", False):
            return self._FIM_DE_FALA_S
        return (self._FIM_DE_FALA_NUVEM_S if self._voz_e_de_nuvem()
                else self._FIM_DE_FALA_LOCAL_S)
    # Fôlego antes da primeira palavra. Era 5 pedaços / 0,35 s, feito para
    # tapar os solavancos do áudio que vem pela internet. Quem realmente
    # resolve isso é a escrita em lote (ver _play_audio), copiada do JARVIS
    # original — que não espera nada e mesmo assim não pica. Manter a espera
    # alta em cima do lote só atrasava o começo de TODA resposta. Sobra um
    # fôlego mínimo, que custa 0,08 s e não se percebe.
    _FOLGA_INICIAL = 2
    _FOLGA_TEMPO_MAX = 0.12
    # Depois de uma ferramenta pesada (uma pesquisa na internet, por exemplo) o
    # computador ainda está ocupado processando o resultado, e o áudio chega em
    # solavancos. Com a folga normal a voz saía picotada — sintoma que o cliente
    # descreveu como "travou quando ele pesquisou o jogo". Juntar mais fôlego só
    # nesse caso resolve sem atrasar as respostas comuns.
    _FOLGA_POS_FERRAMENTA = 6
    _FOLGA_TEMPO_MAX_POS  = 0.35

    # Reserva de som DENTRO da placa. É diferente das folgas acima: aquelas
    # seguram o começo da fala, esta mantém a caixa tocando enquanto o próximo
    # pedaço não chegou. A WASAPI abre com 3 ms se ninguém pedir nada, e 3 ms
    # não cobrem nem um soluço de rede. Não atrasa a conversa: é pago uma vez,
    # no início de cada fala.
    _FOLGA_DA_CAIXA_S = 0.20

    @staticmethod
    @staticmethod
    def _melhor_entrada_de_audio():
        """Escolhe POR ONDE OUVIR, em vez de aceitar o padrao do Windows.

        Dois problemas reais, achados na maquina do Diego em 30/08/2026:

        1. O padrao entrava pela **MME**, a interface mais antiga do Windows —
           a mesma que ja tinha causado a voz picotada na SAIDA e foi trocada
           por WASAPI la. Na entrada tinha ficado como estava.

        2. A maquina tem um **"Virtual Mic (AudioRelay)"**, microfone virtual do
           app que espelha o celular. Quando o Windows troca o padrao para ele
           (o app abriu, fechou, o fone entrou), o microfone real simplesmente
           para de entregar bloco — e foi isso que gerou 14 reaberturas seguidas
           sem sucesso no registro.

        Escolher o aparelho de propriosito resolve os dois. Preferimos WASAPI e
        pulamos qualquer coisa virtual, de loopback ou de mixagem.

        Devolve o indice do dispositivo, ou None para deixar como estava.
        """
        PROIBIDOS = ("virtual", "loopback", "mixagem", "stereo mix", "cabo",
                     "vb-audio", "voicemeeter", "audiorelay", "mapeador")
        try:
            import sounddevice as _sd
            aparelhos = _sd.query_devices()
            apis = _sd.query_hostapis()

            def limpo(nome: str) -> bool:
                n = (nome or "").lower()
                return not any(x in n for x in PROIBIDOS)

            # Qual e o microfone que o Windows considera padrao, pelo NOME —
            # assim respeitamos a escolha do cliente, mas por uma via melhor.
            try:
                nome_padrao = (_sd.query_devices(kind="input") or {}).get("name", "")
            except Exception:
                nome_padrao = ""
            raiz = (nome_padrao.split("(")[-1].strip(") ") or nome_padrao).lower()

            melhor = None
            for indice, d in enumerate(aparelhos):
                if d.get("max_input_channels", 0) < 1:
                    continue
                nome = d.get("name", "")
                if not limpo(nome):
                    continue
                api = apis[d["hostapi"]].get("name", "")
                if "WASAPI" not in api:
                    continue
                # Mesmo aparelho que o cliente escolheu, porem pela WASAPI.
                if raiz and raiz in nome.lower():
                    return indice
                if melhor is None:
                    melhor = indice
            return melhor
        except Exception:
            return None

    @staticmethod
    def _melhor_saida_de_audio():
        """Escolhe por onde falar, preferindo a via moderna do Windows.

        O Python escolhe sozinho a interface MME, que e a mais antiga do
        Windows e exige pelo menos 90 ms de folga. O JARVIS entrega o som em
        pedacos de 43 ms — mais rapido do que o MME consegue tocar — e o
        resultado e a voz picotando o tempo todo, principalmente em caixas USB.

        A WASAPI, que todo Windows moderno tem, trabalha com 3 ms. Ela costuma
        recusar a taxa de 24 kHz que o JARVIS usa, mas aceita quando pedimos a
        conversao automatica.

        Devolve (dispositivo, ajustes) ou (None, None) para deixar como estava.
        """
        try:
            import sounddevice as _sd
            for indice, api in enumerate(_sd.query_hostapis()):
                if "WASAPI" not in api.get("name", ""):
                    continue
                saida = api.get("default_output_device", -1)
                if saida is None or saida < 0:
                    continue
                ajustes = _sd.WasapiSettings(auto_convert=True)
                _sd.check_output_settings(
                    device=saida, samplerate=RECEIVE_SAMPLE_RATE,
                    channels=CHANNELS, dtype="int16", extra_settings=ajustes,
                )
                return saida, ajustes
        except Exception:
            pass
        return None, None   # sem WASAPI utilizavel: segue com o padrao do sistema

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")
        stream = None
        last_error = ""
        self._aquecer_audio = True
        # A ESCOLHA DO CLIENTE VENCE. Ele sabe onde esta ouvindo; o programa nao.
        # No caso do Diego, o JARVIS escolhia os alto-falantes USB enquanto ele
        # ouvia pelo notebook via AudioRelay — falava certo, no lugar errado.
        _esc = getattr(self, "_saida_escolhida", "nao-definido")
        if _esc != "nao-definido":
            saida_pref = _esc
            ajustes_pref = None
            if _esc is not None:
                try:
                    if "WASAPI" in sd.query_hostapis(
                            sd.query_devices(_esc)["hostapi"])["name"]:
                        ajustes_pref = sd.WasapiSettings(auto_convert=True)
                except Exception:
                    ajustes_pref = None
            _voice_diag("saida_escolhida", str(_esc))
        else:
            saida_pref, ajustes_pref = self._melhor_saida_de_audio()
        if saida_pref is not None:
            print(f"[JARVIS] 🔊 Saída de áudio: WASAPI (dispositivo {saida_pref})")
            _voice_diag("speaker_backend", f"wasapi:{saida_pref}")
        else:
            _voice_diag("speaker_backend", "padrao")
        try:
            while True:
                # TROCA AO VIVO da saida. Sem isto, escolher no painel so valia
                # depois de reiniciar — o fluxo e aberto uma vez no arranque.
                # Eu marcava a troca numa variavel e nunca a lia; o Diego
                # clicava, ouvia o bip do teste, e a voz continuava saindo no
                # aparelho antigo.
                if getattr(self, "_trocar_saida", False):
                    self._trocar_saida = False
                    _esc2 = getattr(self, "_saida_escolhida", "nao-definido")
                    if _esc2 != "nao-definido":
                        saida_pref = _esc2
                        ajustes_pref = None
                        if _esc2 is not None:
                            try:
                                if "WASAPI" in sd.query_hostapis(
                                        sd.query_devices(_esc2)["hostapi"])["name"]:
                                    ajustes_pref = sd.WasapiSettings(auto_convert=True)
                            except Exception:
                                ajustes_pref = None
                    if stream is not None:
                        try:
                            stream.stop(); stream.close()
                        except Exception:
                            pass
                        stream = None
                    _voice_diag("saida_trocada_ao_vivo", str(saida_pref))

                if stream is None:
                    try:
                        try:
                            stream = sd.RawOutputStream(
                                samplerate=RECEIVE_SAMPLE_RATE,
                                channels=CHANNELS,
                                dtype="int16",
                                blocksize=CHUNK_SIZE,
                                device=saida_pref,
                                extra_settings=ajustes_pref,
                                # Sem este pedido, a WASAPI abre com a folga
                                # MÍNIMA da placa — medida em 3 ms nesta
                                # máquina. Três milissegundos de reserva não
                                # absorvem NADA: qualquer atraso da internet
                                # deixa a caixa sem som e a voz pica. Com 0,2 s
                                # de reserva o alto-falante continua tocando
                                # enquanto o próximo pedaço não chega, e o
                                # atraso só acontece uma vez, no começo da fala.
                                latency=self._FOLGA_DA_CAIXA_S,
                            )
                            stream.start()
                            try:
                                _voice_diag(
                                    "folga_da_caixa",
                                    f"pedi={self._FOLGA_DA_CAIXA_S:.2f}s "
                                    f"consegui={stream.latency:.3f}s",
                                )
                            except Exception:
                                pass
                        except Exception:
                            # A via moderna falhou neste computador: volta ao
                            # comportamento antigo em vez de ficar sem som.
                            if saida_pref is not None:
                                saida_pref, ajustes_pref = None, None
                                _voice_diag("speaker_backend", "fallback_padrao")
                            stream = sd.RawOutputStream(
                                samplerate=RECEIVE_SAMPLE_RATE,
                                channels=CHANNELS,
                                dtype="int16",
                                blocksize=CHUNK_SIZE,
                            )
                            stream.start()
                        if last_error:
                            self.ui.write_log("SIS: Saída de áudio disponível novamente.")
                        last_error = ""
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        error = f"{type(e).__name__}: {e}"
                        print(f"[JARVIS] ❌ Play: {error}")
                        _voice_diag_exception("speaker_error", e)
                        if error != last_error:
                            self.ui.write_log(
                                "AVISO: saída de áudio indisponível; tentando novamente sem interromper o JARVIS."
                            )
                            last_error = error
                        self._drain_audio_queue()
                        await asyncio.sleep(5)
                        continue
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    fim_do_turno = (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    )
                    # Rede de segurança: o "aviso de fim de turno" vem do
                    # servidor e nem sempre chega — basta a conexão oscilar ou
                    # a sessão reconectar no meio da fala. Sem ele, o JARVIS
                    # continuava marcado como FALANDO para sempre e o microfone,
                    # que fica mudo enquanto ele fala, nunca mais era ouvido.
                    # O cliente falava e nada acontecia, mas pelo teclado o
                    # JARVIS respondia normalmente. Se não chega áudio nenhum há
                    # um tempo, damos a fala por encerrada.
                    silencio = (
                        not fim_do_turno
                        and self._is_speaking
                        and self.audio_in_queue.empty()
                        and (time.monotonic() - self._ultimo_audio_tocado)
                            > self._espera_fim_de_fala()
                    )
                    if fim_do_turno or silencio:
                        if silencio:
                            self._destravou_em = time.monotonic()
                            # Um aviso por vez: se a marca de "falando" for
                            # religada logo em seguida (áudio do modelo que
                            # chega e é descartado), este bloco reentra a cada
                            # 0,1 s e enchia o registro de rajadas iguais,
                            # escondendo o resto.
                            if (self._destravou_em - self._ultimo_aviso_fim_fala) > 5.0:
                                self._ultimo_aviso_fim_fala = self._destravou_em
                                _voice_diag("fim_de_fala_por_silencio",
                                            f"{self._destravou_em - self._ultimo_audio_tocado:.1f}s")
                        self.set_speaking(False)
                        if self._turn_done_event:
                            self._turn_done_event.clear()
                        self._aquecer_audio = True   # próximo turno junta folga
                    continue
                # Voltou a chegar áudio logo depois de darmos a fala por
                # encerrada: era só uma pausa, não o fim. Fica registrado para
                # sabermos se o tempo de espera ainda está curto demais.
                if self._destravou_em and (time.monotonic() - self._destravou_em) < 5.0:
                    _voice_diag("fim_de_fala_falso",
                                f"voltou em {time.monotonic() - self._destravou_em:.1f}s")
                self._destravou_em = 0.0
                self.set_speaking(True)
                self._ultimo_audio_tocado = time.monotonic()

                # Junta uma folga antes da primeira palavra do turno. Depois
                # disso toca direto, para não atrasar o resto da frase.
                if self._aquecer_audio:
                    if self._folga_extra:
                        alvo, espera = self._FOLGA_POS_FERRAMENTA, self._FOLGA_TEMPO_MAX_POS
                    else:
                        alvo, espera = self._FOLGA_INICIAL, self._FOLGA_TEMPO_MAX
                    limite = time.monotonic() + espera
                    while (self.audio_in_queue.qsize() < alvo
                           and time.monotonic() < limite):
                        # Sai antes se o turno já acabou: frase curta não tem
                        # o que esperar.
                        if self._turn_done_event and self._turn_done_event.is_set():
                            break
                        await asyncio.sleep(0.02)
                    self._aquecer_audio = False
                    self._folga_extra = False
                # Junta num só envio todos os pedaços que já estão prontos.
                # Antes era uma ida e volta ao pool de threads POR PEDAÇO de
                # 50 ms — vinte por segundo. Com o computador ocupado (uma
                # pesquisa rodando, por exemplo) esse vaivém atrasa, o alto-
                # falante fica sem o que tocar e a voz sai picotada. O teto de
                # 200 ms preserva a interrupção rápida pelo ESC.
                lote = bytearray(chunk)
                while len(lote) < 9600:   # 9600 bytes = 200 ms a 24 kHz, 16 bits
                    try:
                        lote.extend(self.audio_in_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                # FECHANDO (05/10/2026): a janela ja fechou, nao fala mais nada.
                # Antes o JARVIS seguia falando sem janela ate o fim da limpeza.
                if _ENCERRANDO.is_set():
                    continue
                # O NUCLEO PULSA COM A VOZ DE VERDADE (05/10/2026). O volume de
                # cada fatia de 20 ms vai para a tela junto com a hora em que
                # ela vai tocar (o que ja esta na caixa toca antes). So faz a
                # conta e poe numa fila — nao mexe na janela daqui.
                try:
                    _avisar_voz = getattr(self.ui, "voz_do_jarvis", None)
                    if _avisar_voz is not None:
                        import numpy as _np_voz  # noqa: PLC0415
                        _fatia = RECEIVE_SAMPLE_RATE // 50
                        _amostras = _np_voz.frombuffer(bytes(lote[: len(lote) // 2 * 2]),
                                                       dtype=_np_voz.int16)
                        _n_fatias = len(_amostras) // _fatia
                        if _n_fatias:
                            _rms = _np_voz.sqrt(_np_voz.mean(
                                _amostras[: _n_fatias * _fatia].astype(_np_voz.float32)
                                .reshape(_n_fatias, _fatia) ** 2, axis=1))
                            _agora_voz = time.monotonic()
                            _ini_voz = max(_agora_voz + 0.10,
                                           getattr(self, "_voz_na_tela_ate", 0.0))
                            if _ini_voz > _agora_voz + 1.5:
                                _ini_voz = _agora_voz + 0.10
                            self._voz_na_tela_ate = _ini_voz + _n_fatias * 0.02
                            _avisar_voz([min(1.0, (float(v) / 4500.0) ** 0.8) for v in _rms],
                                        0.02, _ini_voz)
                except Exception:
                    pass
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        self._audio_executor, stream.write, bytes(lote)
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # ANTES aqui só se tratava RuntimeError. Mas quando a saída
                    # de áudio do Windows falha — fone desconectado, dispositivo
                    # padrão trocado, driver reiniciado — o erro é um
                    # PortAudioError, que NÃO é RuntimeError. Ele escapava, o
                    # grupo de tarefas caía e a sessão inteira era derrubada:
                    # o cliente via "SLEEP" e depois "JARVIS ativo", e o
                    # assistente ficava mudo. Agora só o alto-falante é
                    # reaberto; a conversa continua.
                    _voice_diag_exception("speaker_write_error", e)

                    # AVISO AO CLIENTE. `AUDCLNT_E_DEVICE_INVALIDATED` quer dizer
                    # que o Windows tirou o aparelho de som debaixo de nos —
                    # normalmente porque outro programa (AudioRelay, OBS,
                    # Discord) o tomou. Sem esta mensagem o cliente so ve o
                    # JARVIS emudecer.
                    #
                    # ATENCAO A INDENTACAO: este bloco tem de ficar DENTRO do
                    # `except`. Em 30/08 eu o colei um nivel acima, ele passou a
                    # rodar a cada volta do laco, leu a variavel `e` que nao
                    # existe fora do erro, deu NameError, e o fechamento do fluxo
                    # logo abaixo passou a FECHAR A CAIXA A CADA ITERACAO — a voz
                    # do JARVIS cortava no primeiro instante de cada frase.
                    try:
                        if "DEVICE_INVALIDATED" in str(e):
                            _agora_av = time.monotonic()
                            if (_agora_av - getattr(self, "_ultimo_aviso_caixa", 0.0)) > 30:
                                self._ultimo_aviso_caixa = _agora_av
                                self.ui.write_log(
                                    "SIS: O Windows trocou o aparelho de som — reabrindo. "
                                    "Se acontecer sempre, feche programas que capturam audio "
                                    "(AudioRelay, OBS, Discord) e fale comigo de novo.")
                    except Exception:
                        pass

                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                    stream = None
                    self._drain_audio_queue()
                    self.set_speaking(False)
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Startup greeting only.
        News is intentionally disabled at startup and is fetched only when the
        user explicitly asks for news/current events.
        """
        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── memory ───────────────────────────────────────────────────────────
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")

        from datetime import datetime
        time_str = datetime.now().strftime("%H:%M")

        # ── Phase 1: instant greeting — one simple sentence ──────────────────
        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""
        p1 = (
            f"Greet the user naturally, mention it is {time_str}, and say you are ready to help. "
            f"Do not mention news, headlines, briefing, searching, or fetching anything. "
            f"Use Brazilian Portuguese when appropriate, slightly informal but not exaggerated. "
            f"Vary the greeting every startup; examples of tone: 'Salve, chefe', 'Opa, chefe', 'Tô por aqui'. "
            f"One short sentence only. Do not call any tools.{lang_clause}{name_clause}"
        )

        await self.session.send_client_content(
            turns={"parts": [{"text": _com_idioma(_no_idioma(p1))}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Startup greeting sent. News briefing disabled.")

        # Notícias desativadas na inicialização.
        # O JARVIS só chama web_search/news quando o usuário pedir.

    async def _briefing_news_phase(self, lang: str) -> None:
        """
        Sends phase-2 (news) to Gemini ~1.5 s after phase-1 is dispatched so
        Gemini starts working on it while phase-1 audio is still playing.
        """
        lang_str = f" Respond in {lang}." if lang else ""

        # 1.5 s is enough for Gemini to finish generating phase-1 audio on its
        # side (turn_complete) while the greeting is still being played locally.
        await asyncio.sleep(1.5)

        if not self.session:
            return

        p2 = (
            "[BRIEFING] Call web_search with mode='news' and query='top world news today' "
            "to find actual recent news articles with real event headlines (not just website names). "
            "After the search, say ONE specific news event from the results in one sentence, "
            "with a natural slightly informal Brazilian Portuguese tone, "
            f"then say the full list is displayed on screen.{lang_str}"
        )

        await self.session.send_client_content(
            turns={"parts": [{"text": _com_idioma(_no_idioma(p2))}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 2 (news) sent.")


    # ── WhatsApp Bridge background workers ─────────────────────────────────────

    async def _watch_whatsapp_schedules(self) -> None:
        """Envia mensagens WhatsApp agendadas enquanto o JARVIS estiver aberto."""
        await asyncio.sleep(6.0)
        loop = asyncio.get_event_loop()
        while True:
            try:
                events = await loop.run_in_executor(None, lambda: poll_due_scheduled_messages(5))
                for ev in events or []:
                    if ev.get("ok"):
                        if str(ev.get("media_type") or "text") != "text":
                            phrase = f"Chefe, enviei o arquivo agendado para {ev.get('to')}."
                        else:
                            phrase = f"Chefe, enviei a mensagem agendada para {ev.get('to')}."
                    else:
                        err = str(ev.get("error") or "")
                        if any(x in err.lower() for x in ("not connected", "desconect", "offline", "connect whatsapp", "scan the qr")):
                            continue
                        phrase = f"Chefe, falhou o envio agendado para {ev.get('to')}: {ev.get('error')}"
                    self.ui.write_log("[WhatsApp agendamento] " + phrase)
                    if not self.ui.muted:
                        self.speak(phrase)
                    await asyncio.sleep(0.7)
            except Exception as e:
                self.ui.write_log(f"[WhatsApp agendamento] erro: {str(e)[:120]}")
            await asyncio.sleep(20.0)

    async def _watch_whatsapp_agent(self) -> None:
        """Processa o modo agente WhatsApp, quando ativado pelo usuário."""
        await asyncio.sleep(7.0)
        loop = asyncio.get_event_loop()
        while True:
            try:
                events = await loop.run_in_executor(None, lambda: poll_whatsapp_agent_events(20))
                for ev in events or []:
                    result = await loop.run_in_executor(None, lambda e=ev: process_whatsapp_agent_event(e))
                    if result and result.get("sent"):
                        self.ui.write_log(f"[WhatsApp agente] Respondeu para {result.get('to')}: {result.get('reply')}")
                        if result.get("notify") and not self.ui.muted:
                            self.speak(f"Chefe, o agente WhatsApp respondeu para {result.get('to')}.")
                            await asyncio.sleep(0.7)
            except Exception as e:
                self.ui.write_log(f"[WhatsApp agente] erro: {str(e)[:120]}")
            await asyncio.sleep(2.5)

    async def _watch_whatsapp_alerts(self) -> None:
        """Mostra e fala alertas de mensagens WhatsApp solicitados pelo usuário."""
        await asyncio.sleep(4.0)
        loop = asyncio.get_event_loop()
        while True:
            try:
                alerts = await loop.run_in_executor(None, lambda: poll_whatsapp_alerts(30))
                for a in alerts or []:
                    who = str(a.get("name") or a.get("chat_name") or "contato").strip()
                    chat = str(a.get("chat_name") or "").strip()
                    if re.match(r"^participante\s+\d+", who, re.I) or "@lid" in who or "@s.whatsapp.net" in who:
                        who = "participante do grupo"
                    text = str(a.get("text") or "mensagem sem texto visível").strip()
                    is_audio = (str(a.get("media_kind") or "").lower() == "audio" or "audio" in str(a.get("type") or "").lower() or "áudio" in text.lower() or "audio" in text.lower())
                    if is_audio:
                        media_path = str(a.get("media_path") or "").strip()
                        if media_path:
                            try:
                                text = await loop.run_in_executor(None, lambda p=media_path: transcribe_audio_path(p))
                            except Exception as e:
                                text = f"não consegui transcrever este áudio agora: {e}"
                        else:
                            text = "áudio recebido, mas o arquivo ainda está sendo baixado"
                    elif len(text) > 180:
                        text = text[:180].rstrip() + "..."
                    is_group = bool(a.get("is_group")) or str(a.get("jid") or "").endswith("@g.us")
                    if is_group:
                        line = f"[WhatsApp alerta] Grupo {chat} | {who}: {text}"
                        phrase = f"Chefe, nova mensagem no grupo {chat}, enviada por {who}: {text}"
                    else:
                        line = f"[WhatsApp alerta] Privado | {who}: {text}"
                        phrase = f"Chefe, nova mensagem no privado de {who}: {text}"
                    self.ui.write_log(line)
                    if not self.ui.muted:
                        self.speak(phrase)
                    await asyncio.sleep(0.7)
            except Exception:
                pass
            await asyncio.sleep(1.0)

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if alert and self.session:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": alert}]},
                        turn_complete=True,
                    )
                except Exception as e:
                    print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session:
                continue

            # ── O DEFEITO DA 1.0.25 (08/09/2026, queixa do cliente) ──────────
            #
            # "Ele fala 'senhor, o que quer fazer'; eu mando o que eu quero e
            # ele manda algo parecido de novo. Fechei e abri, nao muda nada."
            #
            # Era ISTO. No MODO LOCAL (que nasce ligado) a fala do dono vai pelo
            # whisper e NUNCA passa pela sessao da Gemini — e `_last_user_speech`
            # so era atualizado la dentro, na transcricao do servidor. Ou seja:
            # o relogio do silencio comecava a contar na abertura do programa e
            # NUNCA MAIS era zerado, por mais que o dono falasse a noite toda.
            #
            # Resultado: 15 min depois de abrir, `should_trigger` passava a dar
            # True para sempre e, a cada 10 min, um `[PROACTIVE_CHECK]` era
            # empurrado para dentro da sessao da Gemini. Ela respondia o unico
            # que da para responder sem contexto nenhum — "O que voce gostaria
            # de fazer agora, Gabriel?" — falando por cima e escrevendo no chat.
            # E pior: essa instrucao FICAVA no historico daquela sessao, entao o
            # que o dono digitasse depois vinha no mesmo molde ("algo parecido
            # de novo"). Reabrir o programa nao adiantava: 15 min depois voltava.
            #
            # Dois consertos, e os dois precisam existir:
            #   1. `_last_user_speech` agora e atualizado TAMBEM no caminho local
            #      (voz pelo whisper e texto digitado) — ver `_on_text_command` e
            #      o trecho do `local_ouviu`;
            #   2. no modo local o check-in nao roda, e por um motivo de fundo:
            #      quem conversa com o dono ali e o cerebro local, e a sessao da
            #      Gemini nao viu UMA palavra dessa conversa. Um "check-in" de
            #      quem nao acompanhou a conversa nao tem o que dizer de util —
            #      so atrapalha. Com "/local off" ele volta, ja com o relogio
            #      do silencio funcionando de verdade — e com "/cerebro gemini"
            #      a sessao VOLTA a ser quem conversa, entao ali ele tambem vale.
            if getattr(self, "_modo_local", False) and self._cerebro != "gemini":
                continue
            # O PORTAO DA PALAVRA DE ATIVACAO MANDA AQUI TAMBEM. Com "so com o
            # nome" ligado o dono pediu silencio ate chamar; falar sozinho e
            # exatamente o que ele desligou.
            if getattr(self, "_so_com_nome", False):
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory = await asyncio.to_thread(load_memory)
                prompt = self._proactive.build_prompt(memory)
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self._tool_busy and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        """O que o dono digita no celular, pelo QR Code do Controle Remoto.

        O DEFEITO ATE A v1.0.27 (22/09/2026, queixa do Diego): "leio o QR Code,
        vejo a webcam, vejo tudo; mas quando eu digito no chat do celular a
        mensagem nao chega nele".

        A causa esta nas linhas de baixo, como estavam antes: o texto SO era
        entregue quando existia `self.session` — que e a sessao ao vivo da
        GEMINI. E o JARVIS nasce em MODO LOCAL (`self._modo_local = True`, o
        padrao desde 30/08), e nesse modo `self.session` e None DE PROPOSITO.
        Entao ele esperava 8 s a toa e imprimia "Dropped command (no session)"
        num terminal que o dono nem ve. A webcam funcionava porque ela nao passa
        por aqui — vai direto pelo `remote_media_action`.

        O conserto e entregar no MESMO lugar em que cai o texto digitado na
        janela do PC: `_on_text_command`. Ele ja sabe escolher sozinho entre o
        cerebro local e a Gemini, ja trata os comandos de barra, as redes do
        Vision Control e as do computador — tudo que o chat do PC tem, o chat do
        celular passa a ter tambem.
        """
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                pelo_cerebro_local = (getattr(self, "_modo_local", False)
                                      and self._cerebro != "gemini")
                if not pelo_cerebro_local:
                    # So no modo Gemini faz sentido esperar a sessao abrir.
                    for _ in range(80):
                        if self.session:
                            break
                        await asyncio.sleep(0.1)
                # O chat do celular tem de aparecer na janela do PC tambem —
                # senao o dono do computador nao ve o que foi pedido de fora.
                try:
                    self.ui.write_log(f"[Celular]: {text}")
                except Exception:
                    pass
                try:
                    from memory.diario import anotar as _anotar  # noqa: PLC0415
                    _anotar("dono", text, "pelo celular")
                except Exception:
                    pass
                # `_on_text_command` mexe no laco por `run_coroutine_threadsafe`
                # e pode demorar (cerebro, voz): vai em thread, como o botao da
                # janela faz, para nao segurar esta fila.
                threading.Thread(target=self._on_text_command,
                                 args=(text,), daemon=True).start()
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── o chat da janela e o chat do celular sao O MESMO (22/09/2026) ───────

    # Recados de tecnico. Ficam na janela do PC e NAO vao para o celular: quem
    # esta no celular quer a conversa, nao o diagnostico.
    _SO_DA_JANELA = ("SIS:", "SYS:", "LOCAL:", "VOZES:", "VOZ:", "CEREBRO:",
                     "[VisionControl]", "[Dashboard]", "[JARVIS]", "[Web]")

    def _espelhar_chat_no_celular(self) -> None:
        """Faz toda linha do chat da janela aparecer tambem no celular.

        O PORQUE (queixa do Diego, 22/09/2026): pelo Controle Remoto ele via a
        webcam e a tela, mas a CONVERSA nao ia nem voltava. O envio do celular
        para o PC estava quebrado no `_process_dashboard_commands` (ver la); a
        volta nunca existiu no MODO LOCAL — os dois unicos `broadcast` de fala
        estavam dentro do caminho da Gemini e do caminho da voz literal, e o
        modo local nao passa por nenhum dos dois.

        Em vez de espalhar `broadcast` por doze lugares (e esquecer o
        decimo terceiro na proxima versao), o espelho fica num ponto so: o
        `write_log`, por onde TUDO que aparece no chat ja passa.
        """
        if self._dashboard is None:
            return
        # A MARCA FICA NA JANELA, NAO NO MOTOR. O motor e reconstruido inteiro
        # quando o vigia o reinicia (ver `runner` no `main`), e a janela e a
        # mesma o tempo todo. Duas consequencias, e as duas estao tratadas:
        #   1. embrulhar o `write_log` uma vez por reinicio faria o celular
        #      receber a mesma frase duas, tres, quatro vezes — por isso o
        #      embrulho e feito UMA vez so;
        #   2. o embrulho nao pode segurar o motor VELHO (dashboard e laco
        #      mortos), senao o celular emudece depois do primeiro reinicio —
        #      por isso ele pergunta a janela qual e o motor de agora.
        ui = self.ui
        ui._motor_atual = self
        if getattr(ui, "_write_log_original", None) is not None:
            return
        original = ui.write_log
        ui._write_log_original = original

        def write_log_espelhado(text: str):
            try:
                original(text)
            finally:
                try:
                    motor = getattr(ui, "_motor_atual", None)
                    if motor is not None:
                        motor._mandar_linha_ao_celular(str(text))
                except Exception:
                    pass

        ui.write_log = write_log_espelhado

    def _mandar_linha_ao_celular(self, linha: str) -> None:
        texto = (linha or "").strip()
        if not texto or self._dashboard is None or not self._loop:
            return
        if texto.startswith(self._SO_DA_JANELA):
            return
        # NADA SAI DUAS VEZES. O caminho da Gemini ja tinha `broadcast` proprio,
        # e agora o espelho tambem manda; sem esta trava a mesma frase chegaria
        # repetida no celular.
        agora = time.monotonic()
        ultimas = getattr(self, "_ultimas_ao_celular", None)
        if ultimas is None:
            ultimas = self._ultimas_ao_celular = {}
        if agora - ultimas.get(texto, 0.0) < 4.0:
            return
        ultimas[texto] = agora
        if len(ultimas) > 60:
            for velha in [k for k, v in ultimas.items() if agora - v > 30]:
                ultimas.pop(velha, None)
        quem = "jarvis"
        if texto.startswith(("Você:", "Voce:", "You:", "[Celular]:")):
            quem = "user"
        # Tira o "Nome: " da frente — o celular ja mostra de quem e a fala.
        corpo = texto.split(":", 1)[1].strip() if ":" in texto[:24] else texto
        if not corpo:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._dashboard.broadcast({
                    "type": "log", "speaker": quem, "text": corpo,
                    "ts": datetime.now().isoformat(),
                }),
                self._loop)
        except Exception:
            pass

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            # The relay connection is also the authenticated transport for the
            # private VPS Codex worker. It never exposes the VPS credential to
            # the client package.
            self.ui._codex_relay = getattr(self._dashboard, "_relay", None)
            self.ui._codex_remote_mode = bool(self.ui._codex_relay)
            self._dashboard.set_connect_callback(self._on_phone_connected)
            self._espelhar_chat_no_celular()
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            # MODO LOCAL: nao abre sessao da Gemini de jeito nenhum.
            #
            # Ela ficava aberta sem receber um byte e caia sozinha a cada ~9 min
            # (limite de vida de uma sessao ao vivo). Cada queda reimprimia
            # "JARVIS ativo" e refazia a saudacao — atropelando a fala do Diego
            # no meio. Registro de 30/08: 9 reconexoes numa hora, todas inuteis.
            #
            # So volta a conectar se ele escolher `/cerebro gemini`, que e o
            # unico caminho que tem as 28 ferramentas antigas.
            if self._modo_local and self._cerebro != "gemini":
                # ATENCAO: `_listen_audio` (microfone) e `_play_audio` (caixa)
                # rodam DENTRO do TaskGroup da sessao. Sem sessao eles nao
                # existem, e o modo local fica surdo e mudo. Por isso aqui NAO
                # basta pular a conexao: e preciso montar as filas e subir esses
                # dois por conta propria.
                try:
                    self.session = None
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()
                    self.ui.set_state("LISTENING")
                    # (o recado "JARVIS pronto — modo local, sem Gemini" saiu do chat em
                    #  05/09/2026: e informacao de tecnico, nao de dono. Quem abre a
                    #  conversa agora e a SAUDACAO, no fim do `_aquecer_local`; quem
                    #  quiser conferir tem o `logs/voice_session.log`.)
                    _voice_diag("gemini_desligada", "modo local: sessao nao aberta")
                    threading.Thread(target=self._aquecer_local, daemon=True).start()

                    # A PONTE DO WHATSAPP LIGA NA ABERTURA (23/09/2026). No
                    # modo local o vigia de alertas nao roda, e a ponte so
                    # subia quando alguem mandava mensagem — "estou
                    # conectado?" logo depois de abrir respondia NAO com o
                    # WhatsApp pareado. So liga se ja houver sessao salva.
                    def _ponte_whatsapp_na_abertura():
                        try:
                            from actions.whatsapp_bridge import garantir_ponte_se_tiver_sessao  # noqa: PLC0415
                            garantir_ponte_se_tiver_sessao()
                        except Exception:
                            pass
                    threading.Thread(target=_ponte_whatsapp_na_abertura, daemon=True).start()
                    async with asyncio.TaskGroup() as tg_local:
                        tg_local.create_task(self._listen_audio())
                        tg_local.create_task(self._play_audio())
                        tg_local.create_task(self._vigia_sessao())
                        # Fica aqui ate alguem trocar para `/cerebro gemini`.
                        while self._modo_local and self._cerebro != "gemini":
                            await asyncio.sleep(1)
                        raise _TrocouParaGemini()
                except* _TrocouParaGemini:
                    self.ui.write_log("SIS: religando a Gemini...")
                except* Exception as eg:
                    _voice_diag("modo_local_erro", str(eg)[:200])
                    await asyncio.sleep(3)
                continue

            try:
                print("[JARVIS] Connecting...")
                _voice_diag("session_connecting", LIVE_MODEL)
                self.ui.set_state("THINKING")
                self._sessao_vingou = False
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={
                        "api_version": "v1alpha" if self._enhanced_live else "v1beta"
                    }
                )

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    # Conexão nova: nenhum turno de fala está aberto nela.
                    self._turno_aberto    = False
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False
                    self._tool_busy            = False
                    self._aguardando_desde     = None
                    self._ultima_resposta      = time.monotonic()
                    self._suppress_model_audio_until_turn_complete = False
                    # Travas que ficavam com o valor da sessão ANTERIOR.
                    # Se a queda acontecia no meio de uma fala — que é
                    # justamente o que o vigia provocava — a sessão nova nascia
                    # com _is_speaking=True, e o microfone só manda áudio
                    # quando o JARVIS NÃO está falando: microfone morto para
                    # sempre. O cliente falava e nada acontecia, mas pelo
                    # teclado o JARVIS respondia. Era a queixa "depois que
                    # reconecta ele não responde mais".
                    self.set_speaking(False)
                    self._tool_busy_desde      = 0.0
                    self._envio_cobravel       = 0.0
                    self._bytes_cobraveis      = 0
                    self._recebeu_algo         = False
                    self._ultimo_trafego       = time.monotonic()
                    # Conexão nova: começa mandando tudo, como o original.
                    # Sem isto o JARVIS nasceria em modo de economia e o
                    # primeiro "oi" do cliente chegaria cortado.
                    self._ultima_voz_detectada = time.monotonic()
                    self._servidor_ouviu_em    = 0.0
                    self._ultimo_fechamento_em = 0.0
                    self._ultimo_envio_de_voz  = 0.0
                    self._destravou_em         = 0.0
                    self._fala_guardada        = []
                    self._fala_guardada_bytes  = 0
                    self._aquecer_audio        = True
                    self._folga_extra          = False
                    # Sem isto o relógio do áudio ficava em ZERO e as duas redes
                    # de segurança quebravam juntas: a do vigia era desligada
                    # pelo `and self._ultimo_audio_tocado` (0.0 é falso, então
                    # ela NUNCA destravava a fala presa) e a do player calculava
                    # "40020.4s" e disparava em rajada — 13 avisos em 2 s,
                    # registrado em 30/08 às 00:10:58.
                    self._ultimo_audio_tocado  = time.monotonic()
                    self._sessao_vingou        = True
                    self._pending_literal_confirmation = ""
                    self._pending_manual_save_destination = ""
                    self._pending_manual_save_extension = ""

                    print("[JARVIS] Connected.")
                    _voice_diag("session_connected", LIVE_MODEL)
                    # A espera entre tentativas cresce a cada erro (até 5 min,
                    # para não martelar o servidor quando a cota acaba). Ela
                    # PRECISA voltar ao normal aqui: sem isto, depois de uma
                    # sequência de erros o JARVIS continuava esperando minutos
                    # entre reconexões mesmo já funcionando — e o cliente via o
                    # assistente demorar 1 a 2 minutos para responder.
                    self._conn_backoff = 3
                    # A cota voltou: rearma o aviso falado para o proximo estouro.
                    self._avisou_cota = False
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: JARVIS online.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._manter_conexao_viva())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._vigia_sessao())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._warm_literal_voice())
                    if self._modo_local:
                        threading.Thread(target=self._aquecer_local, daemon=True).start()
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_proactive_mode())
                    tg.create_task(self._watch_whatsapp_alerts())
                    tg.create_task(self._watch_whatsapp_schedules())
                    tg.create_task(self._watch_whatsapp_agent())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Startup greeting only — no automatic news
                    #
                    # NO MODO LOCAL ELA NAO SAI (08/09/2026). Quem cumprimenta
                    # ali e o `_saudar_na_abertura`, com a voz e o jeito que o
                    # cliente escolheu ("Boa noite, senhor. Sistema no ar; e so
                    # falar comigo."). Esta aqui e a saudacao da Gemini, e ela
                    # empurra um turno para dentro da sessao pedindo "greet the
                    # user... address the user as {nome}" — que voltava alguns
                    # segundos depois, por cima, chamando o dono pelo primeiro
                    # nome: "O que voce gostaria de fazer agora, Gabriel?".
                    #
                    # Era a queixa do cliente da 1.0.25: duas aberturas, a
                    # segunda fora do personagem e atrasada o bastante para cair
                    # DEPOIS da primeira frase dele. E o pedido ficava no
                    # historico daquela sessao ("uma frase curta so, nao chame
                    # ferramenta nenhuma"), entao o que ele digitasse em seguida
                    # vinha no mesmo molde — o "algo parecido de novo".
                    #
                    # Regra que vale para as tres: no modo local a sessao da
                    # Gemini NAO fala sozinha. Ela existe para o chat digitado e
                    # para as ferramentas; a conversa e do cerebro local, e ela
                    # nao ouviu uma palavra dessa conversa.
                    if not self._briefing_sent and not self._modo_local:
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                # Achatado: sem isto, todo erro vindo do TaskGroup virava a
                # frase generica "unhandled errors in a TaskGroup" e NENHUM dos
                # testes abaixo casava. Ver _texto_do_erro.
                err_str = _texto_do_erro(e)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                _voice_diag_exception("session_error", e)
                traceback.print_exc()

                # Se estávamos tentando RETOMAR uma sessão, o código pode ter
                # vencido — o servidor guarda a sessão por tempo limitado. Nesse
                # caso a próxima tentativa vai do zero. Sem isto, um código
                # vencido prenderia o JARVIS num erro que se repete para sempre.
                # ...MAS só quando a conexão nem chegou a vingar. Se a sessão
                # estava DE PÉ e caiu depois (ou fomos nós que a derrubamos, no
                # vigia), o código continua válido e é ele que preserva o fio da
                # conversa. Jogá-lo fora em toda queda era o motivo de o JARVIS
                # voltar sem lembrar de nada — e de cada reconexão reenviar as
                # instruções e as 28 ferramentas, ~10.600 tokens, o que ainda
                # atrasava a primeira resposta depois de voltar.
                if self._codigo_retomada is not None and not self._sessao_vingou:
                    self._codigo_retomada = None
                    _voice_diag("retomada_descartada", "codigo recusado na conexao")

                # Estado sem volta: o motor por baixo já foi desligado e nada
                # mais consegue rodar. Insistir aqui não adianta — o registro do
                # cliente mostrou 50 tentativas seguidas dando este mesmo erro,
                # com o painel dizendo "JARVIS ativo" e o assistente mudo.
                # Saímos do laço para que o programa reconstrua o motor inteiro.
                # Recursos avancados recusados pelo servidor (a preview muda de
                # tempos em tempos): desliga e reconecta sem eles, como o original.
                if self._enhanced_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "affective" in err_str.lower()
                    or "proactiv" in err_str.lower()
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                ):
                    self._enhanced_live = False
                    self.ui.write_log(
                        "SIS: Recursos avançados de áudio indisponíveis — reconectando sem eles.")
                    continue

                if "cannot schedule new futures after shutdown" in err_str or \
                   "Event loop is closed" in err_str:
                    self.ui.write_log("SIS: Reiniciando o mecanismo de voz...")
                    raise RuntimeError("motor de voz precisa ser reconstruido") from e

                # Invalid API key — stop hammering the API, prompt re-configuration
                # Cota da chave de IA esgotada. Sem isto, o cliente via apenas
                # "PENSANDO" e "SLEEPING" se repetindo para sempre, sem nenhuma
                # explicacao, e achava que o JARVIS tinha quebrado. Nao adianta
                # insistir de 3 em 3 segundos: a cota so volta com o tempo ou
                # com a troca do plano.
                if ("exceeded your current quota" in err_str
                        or "RESOURCE_EXHAUSTED" in err_str
                        or "quota" in err_str.lower() and "billing" in err_str.lower()):
                    self.ui.set_state("SLEEPING")
                    self.ui.write_log(
                        "ATENÇÃO: o limite de uso da sua chave de IA acabou. "
                        "O JARVIS volta a falar assim que o limite for renovado "
                        "ou o plano da chave for ajustado."
                    )
                    # Antes daqui o JARVIS emudecia: o cliente falava e nao voltava
                    # nada, nem um aviso, e ele achava que o programa tinha quebrado.
                    # O gemini-2.5-flash-preview-tts e OUTRO balde de cota (10 pedidos
                    # por dia na faixa gratis), entao a voz Charon ainda sai para dar a
                    # noticia. Uma vez so — este trecho repete a cada nova tentativa de
                    # conexao, e sao 10 por dia no total.
                    if not getattr(self, "_avisou_cota", False):
                        self._avisou_cota = True
                        try:
                            # VOZ LOCAL (Kokoro) em vez da Charon do modelo de fala.
                            # A Charon tambem e da Gemini e tem teto de 10 pedidos
                            # por dia — dar a noticia de que a cota acabou usando a
                            # propria cota e apostar no que ja falhou. A local nao
                            # tem chave, nao tem teto e roda offline.
                            import asyncio as _a
                            from core.voz_local import VozLocal
                            if self._voz_local is None:
                                self._voz_local = VozLocal()
                            aviso = ("Chefe, o limite de uso da sua chave de inteligencia "
                                     "artificial acabou por hoje. Eu volto a atender assim "
                                     "que o limite renovar.")
                            bruto = await _a.to_thread(self._voz_local.pcm, aviso)
                            self.set_speaking(True)
                            for pos in range(0, len(bruto), 2400):
                                self.audio_in_queue.put_nowait(bruto[pos: pos + 2400])
                        except Exception as exc_aviso:
                            print(f"[JARVIS] aviso de cota mudo: {str(exc_aviso)[:100]}")
                    self._conn_backoff = min(getattr(self, "_conn_backoff", 30) * 2, 300)
                    print("[JARVIS] Cota da API esgotada — nova tentativa em "
                          f"{self._conn_backoff}s.")
                    self.set_speaking(False)
                    if self._dashboard:
                        try:
                            await self._dashboard.broadcast({"type": "status", "state": "sleeping"})
                        except Exception:
                            pass
                    await asyncio.sleep(self._conn_backoff)
                    continue

                if "API key not valid" in err_str or "1007" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                # "Sem resposta do servidor de voz" saiu desta lista de
                # propósito: é a reconexão que NÓS pedimos, tratada logo acima
                # com espera de 1 s. Deixá-la aqui dobrava a espera a cada
                # reconexão até 60 s — um minuto de JARVIS mudo, sem defeito
                # nenhum de rede. Antes isso passava despercebido porque o texto
                # do ExceptionGroup nunca casava com nada.
                is_net_err = (
                    "Sem resposta do servidor de voz" not in err_str
                    and any(k in err_str for k in (
                        "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                        "ConnectionRefusedError", "OSError", "Cannot connect",
                    ))
                )
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"REDE: Sem conexão — tentando de novo em {_conn_backoff}s."
                    )
                elif "Sem resposta do servidor de voz" in err_str:
                    # Reconexão que NÓS pedimos: a rede está boa, esperar mais
                    # só aumenta o tempo em que o cliente fica sem resposta.
                    self._conn_backoff = 1
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[JARVIS] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def _ja_existe_um_jarvis() -> bool:
    """Ja ha um JARVIS deste usuario aberto?

    Cadeado nomeado do Windows: o primeiro a criar vence, e o segundo descobre
    pelo `ERROR_ALREADY_EXISTS`. Nao depende de arquivo (que fica para tras se o
    programa for morto) nem da porta (que era justamente o que quebrava).

    O `Local\\` no nome deixa o cadeado por SESSAO: dois usuarios diferentes na
    mesma maquina continuam podendo abrir cada um o seu.
    """
    if sys.platform.startswith("linux"):
        # LINUX (26/09/2026): o mutex abaixo e so do Windows. Cadeado de
        # arquivo, solto sozinho quando o processo morre (ate por kill -9).
        # Arquivo proprio: o `.jarvis.lock` do jarvis.sh ja esta preso por ele.
        try:
            import fcntl  # noqa: PLC0415
            caminho = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                    ".jarvis-unico.lock")
            arq = open(caminho, "w")
            try:
                fcntl.flock(arq, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                arq.close()
                return True
            globals()["_CADEADO_DO_JARVIS"] = arq
            return False
        except Exception:
            return False
    try:
        import ctypes  # noqa: PLC0415
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        alca = k32.CreateMutexW(None, False, r"Local\JARVIS-CyberBot-unico")
        if ctypes.get_last_error() == 183:      # 183 = ERROR_ALREADY_EXISTS
            if _jarvis_anterior_preso():
                globals()["_CADEADO_DO_JARVIS"] = alca
                return False
            return True
        # A alca fica guardada de proposito: se o Python a recolher, o cadeado
        # abre e o proximo JARVIS entraria achando que e o primeiro.
        globals()["_CADEADO_DO_JARVIS"] = alca
        return False
    except Exception:
        # Sem cadeado e melhor que sem JARVIS.
        return False


# ── POR QUE NAO EXISTE MAIS UM "AQUECER A VOZ ANTES DA JANELA" ──────────────
#
# Eu escrevi um (05/09/2026): uma thread no comeco do `main()` que abria a
# sessao do ao-vivo em paralelo com o Qt subindo, para a saudacao nao pagar o
# aperto de mao. Medido, ele NAO se sustenta:
#
#   so a conexao aquecida ...... 4,3 s ate o primeiro som
#   conexao + geracao jogada fora 2,9 s   (mas a ordem inverteu: a saudacao
#                                          chegou antes e a frase descartada
#                                          rodou depois, gastando audio a toa)
#   tres aberturas seguidas .... 6,5 | 7,4 | 11,5 s  <- PIOR que sem nada
#
# A ultima linha e a que decide. Abrir e matar o programa varias vezes deixa
# sessoes penduradas do lado do servidor, e ai o gratuito passa a estrangular
# por CONCORRENCIA — o aquecimento vira o problema que ele queria resolver.
#
# O que ficou, e esse esta medido de verdade: a SAUDACAO nao espera mais o
# whisper e o Kokoro (9,0 s -> 6 ms para ela ser disparada). Ver `_aquecer_local`.


def main():
    # UM DE CADA VEZ (01/09/2026).
    #
    # Instalar por cima com o programa aberto, ou so clicar duas vezes no
    # atalho, subia um segundo JARVIS. Os dois disputavam a porta 8000 do painel
    # e o segundo entrava em laco de reinicio sem fim. O cliente ve um programa
    # quebrado; na verdade sao dois programas certos brigando.
    if _ja_existe_um_jarvis():
        try:
            import ctypes  # noqa: PLC0415
            ctypes.windll.user32.MessageBoxW(
                None,
                "O JARVIS ja esta aberto.\n\nProcure a janela dele na barra de "
                "tarefas. Se ela nao aparecer, feche o programa pelo Gerenciador "
                "de Tarefas e abra de novo.",
                "JARVIS CyberBot", 0x40)
        except Exception:
            print("[JARVIS] ja existe um JARVIS aberto.")
            try:  # Linux: aviso na tela, que o MessageBox acima e do Windows
                _subprocess.run(
                    ["notify-send", "JARVIS CyberBot",
                     _frase_no_idioma("O JARVIS ja esta aberto. Procure a janela dele.")],
                    timeout=5)
            except Exception:
                pass
        return

    _rastrear_cameras()
    ui = JarvisUI("face.png")

    def runner():
        """Mantém o motor de voz vivo pelo tempo que a janela estiver aberta.

        Antes, se `asyncio.run` terminasse por qualquer motivo, esta thread
        simplesmente acabava. A janela continuava aberta e respondendo, o painel
        até dizia "JARVIS ativo", mas o motor estava morto e o assistente nunca
        mais falava — só voltava quando o cliente fechava e abria o programa.
        Era o caso mais reclamado.

        Agora, se o motor cair, ele é reconstruído do zero: laço novo, sessão
        nova, tarefas novas.
        """
        ui.wait_for_api_key()
        tentativa = 0
        while True:
            jarvis = JarvisLive(ui)
            try:
                asyncio.run(jarvis.run())
                # Terminou sem erro: só acontece se pediram para encerrar.
                print("[JARVIS] Motor encerrado.")
                return
            except KeyboardInterrupt:
                print("\n🔴 Shutting down...")
                return
            except BaseException as e:
                _voice_diag_exception("engine_restart", e)
                print(f"[JARVIS] Motor caiu ({type(e).__name__}: {e}) — reiniciando.")

            tentativa += 1
            espera = min(3 * tentativa, 30)
            try:
                ui.set_state("SLEEPING")
                ui.write_log(f"SIS: Reiniciando o JARVIS sozinho em {espera}s...")
            except Exception:
                pass
            time.sleep(espera)
            try:
                ui.write_log("SIS: JARVIS reiniciado.")
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()
    # A janela fechou: fecha TUDO que e do JARVIS (01/10/2026). Ver abaixo.
    _encerrar_tudo("janela fechada")


# ── FECHAR = TUDO FECHA, SEMPRE (05/10/2026) ────────────────────────────────
#
# Pedido do Diego: "certifique que toda vez que fecha ele todos os processos
# dele finalizam". O caso que motivou, provado no PC dele: as 15:23 ele ligou o
# Vision Control (webcam) e fechou o JARVIS 13 s depois. O `os._exit` matou a
# linha que lia a camera NO MEIO da leitura; o driver da webcam ficou com um
# pedido pendente que nunca termina e o processo ficou preso com 1 linha
# esperando o driver — nem o Gerenciador de Tarefas mata. O Windows
# (CapabilityAccessManager\webcam) mostrava a camera "ainda em uso" desde
# 15:23:15. Esse preso segurava o cadeado de "um JARVIS so" e a porta 8000: o
# JARVIS novo nao abria ate reiniciar o PC.
#
# E o fechamento demorava: a voz seguia tocando SEM JANELA por 3 a 6 s
# (17:19:18 fechou, 17:19:24 ainda falava) enquanto a limpeza rodava.
#
# Agora, nesta ordem:
#   1. a voz corta na hora e camera nova nao abre (`_ENCERRANDO`);
#   2. em paralelo: cada camera e solta NA PROPRIA LINHA que a le (a leitura
#      seguinte solta e devolve vazio) e o Cowork para (cancela o trabalho na
#      VPS, fecha a janela e o programa do projeto);
#   3. varre o computador: todo processo cujo programa mora na pasta do JARVIS
#      (filho OU orfao: o node da ponte do WhatsApp e um deles), node rodando
#      script da pasta, navegador com perfil na pasta do JARVIS (a janela do
#      Cowork), o que o Cowork rodou na pasta do projeto, e os filhos de todos
#      esses. Programa que o dono pediu para abrir (Chrome dele, Bloco de
#      Notas...) continua aberto. A `_kill_old_bridge_port` ficou de fora: ela
#      e da subida — depois de matar o node ainda via o status.json recente,
#      achava a ponte viva e esperava 5 s a toa (fechar levava 6,2 s);
#   4. sai com TerminateProcess: nao roda a limpeza das DLLs (placa de video,
#      audio, camera), que e onde um fechamento comum pode travar para sempre.
# Rede de seguranca: se qualquer passo travar, em 10 s o processo sai mesmo.
import weakref as _weakref_fechar

_ENCERRANDO = threading.Event()
_CAMERAS_DO_JARVIS = _weakref_fechar.WeakSet()
_CAMERAS_TRAVA = threading.Lock()
_SOLTAR_TRAVA = threading.Lock()      # soltar de duas linhas ao mesmo tempo derruba


def _rastrear_cameras() -> None:
    """Toda `cv2.VideoCapture` do JARVIS passa a ficar registrada.

    Uma subclasse no lugar da original: quem chama `cv2.VideoCapture(...)`
    (Vision Control, camera da janela, painel, visao do cerebro) nao muda nada.
    Depois que o fechamento comeca, a leitura seguinte SOLTA a camera na propria
    linha que a usa e devolve vazio — soltar de outra linha no meio de uma
    leitura pode derrubar o programa."""
    try:
        import cv2  # noqa: PLC0415
        base = cv2.VideoCapture
    except Exception:
        return
    if getattr(base, "_jarvis_rastreia", False):
        return

    class _CameraDoJarvis(base):
        _jarvis_rastreia = True

        def __init__(self, *args, **kwargs):
            if _ENCERRANDO.is_set():
                args, kwargs = (), {}          # fechando: nao abre camera nova
            super().__init__(*args, **kwargs)
            self._jarvis_lendo = False
            self._jarvis_lida = time.monotonic()   # recem-aberta: meio segundo de folga
            with _CAMERAS_TRAVA:
                _CAMERAS_DO_JARVIS.add(self)

        def read(self, *args, **kwargs):
            # A marca vem ANTES de olhar o fechamento: quem fecha so solta de
            # fora uma camera que ninguem esta lendo.
            self._jarvis_lendo = True
            try:
                if _ENCERRANDO.is_set():
                    try:
                        self.release()
                    except Exception:
                        pass
                    return False, None
                return super().read(*args, **kwargs)
            finally:
                self._jarvis_lendo = False
                self._jarvis_lida = time.monotonic()

        def release(self, *args, **kwargs):
            with _SOLTAR_TRAVA:
                return super().release(*args, **kwargs)

    try:
        cv2.VideoCapture = _CameraDoJarvis
    except Exception as exc:
        _voice_diag("camera_sem_rastreio", str(exc)[:90])


def _cameras_abertas() -> list:
    with _CAMERAS_TRAVA:
        todas = list(_CAMERAS_DO_JARVIS)
    abertas = []
    for cam in todas:
        try:
            if cam.isOpened():
                abertas.append(cam)
        except Exception:
            pass
    return abertas


def _soltar_cameras(prazo: float) -> int:
    """Espera cada camera ser solta pela propria linha; solta o resto daqui.

    Devolve quantas tiveram de ser soltas a forca (0 = todas sairam certas)."""
    try:
        vc = sys.modules.get("actions.vision_control")
        ctl = getattr(vc, "_CONTROLLER", None) if vc else None
        if ctl is not None:
            ctl._stop.set()      # o laco do Vision Control sai e solta no `finally`
    except Exception:
        pass
    limite = time.monotonic() + prazo
    forcadas = 0
    resto = _cameras_abertas()
    while resto and time.monotonic() < limite:
        for cam in resto:
            # Ninguem le dela ha meio segundo (gerador do painel que ninguem
            # puxa mais, camera esquecida): solta daqui, ja.
            if (not getattr(cam, "_jarvis_lendo", False)
                    and time.monotonic() - getattr(cam, "_jarvis_lida", 0.0) > 0.5):
                try:
                    cam.release()
                    forcadas += 1
                except Exception:
                    pass
        time.sleep(0.05)
        resto = _cameras_abertas()
    # Sobrou: a leitura travou no driver. Sair com ela aberta e o que prendia o
    # processo; soltar daqui e o ultimo recurso.
    for cam in resto:
        try:
            cam.release()
        except Exception:
            pass
    return forcadas + len(resto)


def _parar_cowork(prazo: float) -> None:
    mod = sys.modules.get("actions.cowork")
    cw = getattr(mod, "COWORK", None) if mod else None
    if cw is None:
        return
    try:
        if cw.ocupado():
            cw.parar()           # a ponte manda `cw_cancelar` e a VPS para o Codex
            limite = time.monotonic() + prazo
            while cw.ocupado() and time.monotonic() < limite:
                time.sleep(0.05)
    except Exception:
        pass
    for passo in ("_parar_processo", "_parar_servidor"):
        try:
            getattr(cw, passo)()
        except Exception:
            pass


def _processos_do_jarvis(inicio_fechamento: float) -> list:
    """Todo processo que e do JARVIS — filho ou orfao — menos ele mesmo."""
    import os  # noqa: PLC0415  (este arquivo so importa `os` como `_os`)
    import psutil  # noqa: PLC0415
    pasta = os.path.normcase(str(Path(__file__).resolve().parent))
    perfil = "--user-data-dir=" + pasta
    eu = psutil.Process(os.getpid())
    minha_hora = eu.create_time()
    try:          # o atalho que abriu este JARVIS: os filhos dele sao do dono
        ancestrais = {p.pid for p in eu.parents()}
    except Exception:
        ancestrais = set()
    try:
        descendentes = {p.pid for p in eu.children(recursive=True)}
    except Exception:
        descendentes = set()
    projeto = ""
    try:
        mod = sys.modules.get("actions.cowork")
        cw = getattr(mod, "COWORK", None) if mod else None
        if cw is not None and getattr(cw, "pasta", None):
            projeto = os.path.normcase(str(Path(cw.pasta).resolve()))
    except Exception:
        pass
    alvos = []
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline", "create_time"]):
        try:
            info = p.info
            if info["pid"] == eu.pid:
                continue
            if (info.get("create_time") or 0) > inicio_fechamento:
                continue          # aberto DEPOIS do fechamento: e um JARVIS novo
            nome = (info.get("name") or "").lower()
            if nome.startswith(("unins", "setup")):
                continue          # desinstalador / instalador
            exe = os.path.normcase(info.get("exe") or "")
            linha = os.path.normcase(" ".join(info.get("cmdline") or []))
            do_jarvis = (exe.startswith(pasta + os.sep) or perfil in linha
                         or (nome.startswith("node") and pasta in linha))
            if not do_jarvis and projeto and info["pid"] in descendentes:
                try:              # o que o Cowork rodou na pasta do projeto
                    do_jarvis = os.path.normcase(p.cwd()).startswith(projeto)
                except Exception:
                    pass
            if not do_jarvis:
                continue
            # JARVIS anterior preso no driver (1 linha, mais velho que este):
            # ninguem o mata; esperar por ele so atrasaria o fechamento.
            if ("python" in nome and (info.get("create_time") or 0) < minha_hora
                    and p.num_threads() <= 1):
                continue
            alvos.append(p)
        except Exception:
            continue
    # Filhos de quem e do JARVIS tambem sao (navegador da ponte do WhatsApp,
    # o jogo que o programa do Cowork abriu...).
    vistos = {p.pid for p in alvos} | ancestrais | {eu.pid}
    for p in list(alvos):
        if p.pid in ancestrais:
            continue
        try:
            netos = p.children(recursive=True)
        except Exception:
            continue
        for n in netos:
            try:
                if n.pid not in vistos and n.create_time() <= inicio_fechamento:
                    vistos.add(n.pid)
                    alvos.append(n)
            except Exception:
                continue
    return alvos


def _matar_processos_do_jarvis(inicio_fechamento: float, prazo: float) -> int:
    try:
        import psutil  # noqa: PLC0415
        alvos = _processos_do_jarvis(inicio_fechamento)
        for p in alvos:
            try:
                p.kill()
            except Exception:
                pass
        if alvos:
            _, vivos = psutil.wait_procs(alvos, timeout=prazo)
            if vivos:
                _voice_diag("encerrar_processo_resistiu",
                            ", ".join(f"{v.pid}" for v in vivos)[:120])
        return len(alvos)
    except Exception as exc:
        _voice_diag("encerrar_filhos_falhou", str(exc)[:90])
        return 0


def _sair_de_vez(codigo: int = 0) -> None:
    """Sai SEM a limpeza das DLLs (o `os._exit` roda; o TerminateProcess nao)."""
    import os  # noqa: PLC0415
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.flush()
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes  # noqa: PLC0415
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            k32.TerminateProcess(k32.GetCurrentProcess(), codigo)
        except Exception:
            pass
    os._exit(codigo)


def _jarvis_anterior_preso() -> bool:
    """O cadeado esta com um JARVIS que ja fechou mas ficou PRESO no driver?

    Caso de 05/10 15:23: o processo antigo ficou com 1 linha esperando o driver
    da webcam, sem janela, e ninguem o mata (so reiniciando o PC). Ele segura o
    cadeado e o cliente via "O JARVIS ja esta aberto" para sempre. Um JARVIS de
    verdade tem dezenas de linhas; o preso tem 1. So libera se TODOS os JARVIS
    achados estao assim e tem mais de 20 s (um JARVIS que acabou de abrir ainda
    tem poucas linhas)."""
    try:
        import psutil  # noqa: PLC0415
        pasta = _os.path.normcase(_os.path.dirname(_os.path.abspath(__file__)))
        eu = _os.getpid()
        agora = time.time()
        presos = []
        for p in psutil.process_iter(["pid", "name", "exe", "create_time"]):
            info = p.info
            if info["pid"] == eu or "python" not in (info.get("name") or "").lower():
                continue
            if not _os.path.normcase(info.get("exe") or "").startswith(pasta + _os.sep):
                continue
            try:
                linhas = p.num_threads()
            except Exception:
                return False
            if linhas > 1 or agora - (info.get("create_time") or agora) < 20:
                return False      # tem JARVIS de verdade (ou abrindo agora)
            presos.append(p)
        if not presos:
            return False
        for p in presos:
            try:
                p.kill()
            except Exception:
                pass
        _voice_diag("jarvis_preso_ignorado",
                    f"PID {', '.join(str(p.pid) for p in presos)} preso no driver; "
                    f"abrindo assim mesmo")
        return True
    except Exception:
        return False


def _fechar_vozes() -> None:
    try:
        from core.gemini_voice import fechar_todas_as_sessoes  # noqa: PLC0415
        n = fechar_todas_as_sessoes(1.0)
        if n:
            _voice_diag("programa_encerrou", f"{n} sessao(oes) de voz fechada(s)")
    except Exception:
        pass


def _encerrar_tudo(motivo: str = "") -> None:
    """Fecha o JARVIS INTEIRO, sem deixar nada rodando para tras.

    01/10/2026, queixa do Diego: "quando o JARVIS ta aberto eu fecho e ele fica
    aberto ainda". Fechada a janela, a ponte do WhatsApp (o `node.exe
    server.js` da pasta do JARVIS) seguia rodando sozinha, e uma tarefa de
    fundo presa segurava o Python aberto sem janela nenhuma.

    05/10/2026: camera, Cowork, orfaos, saida que o driver nao segura e a voz
    que cortava so no fim. Ver o bloco "FECHAR = TUDO FECHA" acima.
    """
    inicio = time.time()
    _ENCERRANDO.set()
    _voice_diag("programa_encerrou", f"fechando tudo ({motivo or 'pedido'})")

    def _rede_de_seguranca() -> None:
        time.sleep(10.0)
        _voice_diag("programa_encerrou", "a limpeza passou de 10 s; saindo assim mesmo")
        _sair_de_vez()

    threading.Thread(target=_rede_de_seguranca, daemon=True, name="saida-garantida").start()

    forcadas: list = []
    passos = [
        threading.Thread(target=lambda: forcadas.append(_soltar_cameras(3.0)),
                         daemon=True, name="fechar-cameras"),
        threading.Thread(target=_parar_cowork, args=(1.5,), daemon=True, name="fechar-cowork"),
        # As sessoes da voz ao vivo: despedir do Google antes de sair, senao
        # elas seguem contando e a reabertura da "cota excedida" (08/10/2026).
        threading.Thread(target=_fechar_vozes, daemon=True, name="fechar-voz"),
    ]
    for passo in passos:
        passo.start()
    limite = time.monotonic() + 3.5
    for passo in passos:
        passo.join(max(0.0, limite - time.monotonic()))
    demorados = [passo.name for passo in passos if passo.is_alive()]
    n = _matar_processos_do_jarvis(inicio, 2.0)
    _voice_diag("programa_encerrou",
                f"limpo em {time.time() - inicio:.1f}s | {n} processo(s) do JARVIS fechado(s)"
                f" | cameras soltas a forca: {forcadas[0] if forcadas else '?'}"
                + (f" | nao terminou: {', '.join(demorados)}" if demorados else ""))
    _sair_de_vez()

if __name__ == "__main__":
    main()
