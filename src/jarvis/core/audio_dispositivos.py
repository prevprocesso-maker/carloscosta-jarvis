"""Escolha do microfone: listar, TESTAR de verdade e lembrar.

Criado em 30/08/2026 depois de o microfone do Diego morrer e nenhuma tentativa
de reabrir resolver (14 reaberturas seguidas, uma a cada 8 s).

## O que se aprendeu naquele dia

1. **O padrao do Windows nao e confiavel.** A maquina dele tem um
   "Virtual Mic (AudioRelay)" — microfone virtual do app que espelha o celular.
   Quando o Windows troca o padrao para ele, o microfone real para de entregar
   bloco e nada no programa percebe.

2. **A WASAPI recusa 16 kHz** na maioria das placas ("Invalid sample rate",
   PaErrorCode -9997). Precisa de `WasapiSettings(auto_convert=True)`, a mesma
   solucao que ja existia para o alto-falante.

3. **Abrir sem erro NAO significa que funciona.** Testei tres aparelhos e todos
   abriram limpos entregando ZERO bloco. Por isso aqui o teste mede blocos que
   realmente chegaram, e nao se a chamada deu excecao.

4. **PortAudio trava.** Um `sd.rec()` simples ficou pendurado para sempre e teve
   de ser morto. Por isso todo teste roda em thread com prazo: nada aqui pode
   pendurar o JARVIS.
"""
from __future__ import annotations

import queue
import threading

# Nomes que nao servem de microfone de verdade: virtuais, mixagem, roteadores
# de audio e o "mapeador" generico do Windows.
# ERRO CORRIGIDO em 30/08/2026: eu escondia "virtual" e "audiorelay" achando
# que nao eram microfones de verdade. **Para o Diego eles SAO os de verdade** —
# ele usa o AudioRelay para o audio do PC sair no notebook dele, entao o
# aparelho virtual e o caminho real do som e da voz.
#
# Esconder aparelho do cliente e decidir por ele. Aqui so ficam de fora os que
# nao sao aparelho nenhum: o "mapeador" generico e o "driver primario", que sao
# apelidos do Windows para "use o padrao".
PROIBIDOS = (
    "mapeador", "sound mapper", "primary", "driver de captura",
    "driver de som prim",
)

TAXA = 16_000
BLOCO = 640


def _sd():
    import sounddevice as sd  # noqa: PLC0415
    return sd


def listar() -> list[dict]:
    """Microfones reais, um por aparelho fisico, preferindo a via moderna.

    O Windows mostra o MESMO microfone em quatro interfaces (MME, DirectSound,
    WASAPI, WDM-KS). Mostrar as quatro ao cliente e confusao garantida, entao
    guardamos so a melhor de cada nome, na ordem WASAPI > DirectSound > MME.
    """
    sd = _sd()
    try:
        aparelhos = sd.query_devices()
        apis = sd.query_hostapis()
    except Exception:
        return []

    ordem = {"WASAPI": 0, "DirectSound": 1, "MME": 2, "WDM-KS": 3}

    def peso(api_nome: str) -> int:
        for chave, valor in ordem.items():
            if chave.lower() in (api_nome or "").lower():
                return valor
        return 9

    melhores: dict[str, dict] = {}
    for indice, d in enumerate(aparelhos):
        if d.get("max_input_channels", 0) < 1:
            continue
        nome = (d.get("name") or "").strip()
        if not nome or any(x in nome.lower() for x in PROIBIDOS):
            continue
        api = apis[d["hostapi"]].get("name", "")
        chave = nome.lower()
        candidato = {
            "indice": indice,
            "nome": nome,
            "api": api,
            "peso": peso(api),
            "canais": d.get("max_input_channels", 1),
        }
        if chave not in melhores or candidato["peso"] < melhores[chave]["peso"]:
            melhores[chave] = candidato

    return sorted(melhores.values(), key=lambda x: x["peso"])


def testar(indice: int | None, segundos: float = 1.2, prazo: float = 6.0) -> dict:
    """Abre o aparelho e conta os blocos que REALMENTE chegaram.

    Devolve {ok, blocos, nivel, erro}. Roda em thread com prazo porque o
    PortAudio trava de vez em quando e nao pode levar o JARVIS junto.
    """
    resultado: queue.Queue = queue.Queue()

    def trabalho():
        sd = _sd()
        try:
            niveis: list[float] = []

            def cb(indata, frames, tempo, status):
                try:
                    import numpy as np  # noqa: PLC0415
                    # Sem o desvio fixo (DC): a entrada da placa-mae do
                    # cliente DESKTOP-HDU4A58 entrega 887 de desvio com a sala
                    # quieta, e o microfone "mais forte" era so o mais torto.
                    niveis.append(float(np.abs(indata - indata.mean()).mean()))
                except Exception:
                    niveis.append(0.0)

            ajustes = None
            if indice is not None:
                try:
                    # So faz sentido em WASAPI; nas outras vias e ignorado.
                    if "WASAPI" in sd.query_hostapis(
                            sd.query_devices(indice)["hostapi"])["name"]:
                        ajustes = sd.WasapiSettings(auto_convert=True)
                except Exception:
                    ajustes = None

            # A WASAPI exige o numero EXATO de canais do aparelho: pedir 1
            # num aparelho de 2 devolve "Invalid number of channels" e o
            # microfone parece quebrado quando so faltava esse detalhe.
            canais = 1
            if indice is not None:
                try:
                    canais = max(1, min(2, int(sd.query_devices(indice)["max_input_channels"])))
                except Exception:
                    canais = 1

            import time as _t
            with sd.InputStream(samplerate=TAXA, channels=canais, dtype="int16",
                                blocksize=BLOCO, callback=cb, device=indice,
                                extra_settings=ajustes):
                _t.sleep(segundos)
            resultado.put({
                "ok": len(niveis) > 0,
                "blocos": len(niveis),
                "nivel": (sum(niveis) / len(niveis)) if niveis else 0.0,
                "erro": "",
            })
        except Exception as exc:
            resultado.put({"ok": False, "blocos": 0, "nivel": 0.0,
                           "erro": f"{type(exc).__name__}: {str(exc)[:120]}"})

    t = threading.Thread(target=trabalho, daemon=True)
    t.start()
    try:
        return resultado.get(timeout=prazo)
    except queue.Empty:
        # A thread fica pendurada no PortAudio; e daemon, entao nao segura o
        # programa. O que importa e nao esperar por ela.
        return {"ok": False, "blocos": 0, "nivel": 0.0,
                "erro": "o aparelho travou e nao respondeu no prazo"}


def escolher_automatico() -> tuple[int | None, str]:
    """Acha um microfone que FUNCIONA, testando um por um.

    Testar e o ponto: na maquina do Diego tres aparelhos abriram sem erro
    nenhum e entregaram zero bloco. Adivinhar pelo nome nao serve.

    Devolve (indice, explicacao). `None` significa "use o padrao do sistema".
    """
    # Testa TODOS e fica com o de sinal mais forte, nao com o primeiro que
    # abre. Em 30/08 o primeiro a responder tinha nivel 60 e o outro 89 — e o
    # de nivel baixo fazia o whisper devolver vazio.
    candidatos = []
    for aparelho in listar():
        r = testar(aparelho["indice"])
        if r["ok"]:
            candidatos.append((r["nivel"], aparelho, r))

    r_padrao = testar(None)
    if r_padrao["ok"]:
        candidatos.append((r_padrao["nivel"],
                           {"indice": None, "nome": "padrao do sistema", "api": ""},
                           r_padrao))

    if candidatos:
        candidatos.sort(key=lambda x: x[0], reverse=True)
        nivel, ap, r = candidatos[0]
        return ap["indice"], (
            f"{ap['nome']} {ap['api']} — {r['blocos']} blocos, nivel {nivel:.0f} "
            f"(o mais forte entre {len(candidatos)})"
        )
    return None, "nenhum microfone respondeu; usando o padrao do sistema"


def listar_saidas() -> list[dict]:
    """Alto-falantes reais, um por aparelho, preferindo a via moderna.

    Existe porque o JARVIS escolhia sozinho para onde falar e escolhia errado no
    caso do Diego: ele ouve o PC pelo notebook, atraves do AudioRelay, e o
    JARVIS mandava a voz para os alto-falantes USB. Falava certo, no lugar
    errado — e parecia mudo.
    """
    sd = _sd()
    try:
        aparelhos = sd.query_devices()
        apis = sd.query_hostapis()
    except Exception:
        return []

    ordem = {"WASAPI": 0, "DirectSound": 1, "MME": 2, "WDM-KS": 3}

    def peso(api_nome: str) -> int:
        for chave, valor in ordem.items():
            if chave.lower() in (api_nome or "").lower():
                return valor
        return 9

    melhores: dict[str, dict] = {}
    for indice, d in enumerate(aparelhos):
        if d.get("max_output_channels", 0) < 1:
            continue
        nome = (d.get("name") or "").strip()
        if not nome or any(x in nome.lower() for x in PROIBIDOS):
            continue
        api = apis[d["hostapi"]].get("name", "")
        chave = nome.lower()
        cand = {"indice": indice, "nome": nome, "api": api, "peso": peso(api)}
        if chave not in melhores or cand["peso"] < melhores[chave]["peso"]:
            melhores[chave] = cand
    return sorted(melhores.values(), key=lambda x: x["peso"])


def tocar_teste(indice: int | None, prazo: float = 8.0) -> dict:
    """Toca um bip curto no aparelho, para o cliente ouvir e decidir.

    Testar a saida por medicao nao serve: so o ouvido do cliente sabe se o som
    chegou nele. Entao aqui a gente TOCA e ele diz.
    """
    resultado: queue.Queue = queue.Queue()

    def trabalho():
        sd = _sd()
        try:
            import numpy as np  # noqa: PLC0415
            taxa = 24000
            tempo = np.linspace(0, 0.7, int(taxa * 0.7), endpoint=False)
            tom = (0.3 * np.sin(2 * np.pi * 440 * tempo)).astype(np.float32)
            pcm = (tom * 32767).astype(np.int16).tobytes()
            ajustes = None
            if indice is not None:
                try:
                    if "WASAPI" in sd.query_hostapis(
                            sd.query_devices(indice)["hostapi"])["name"]:
                        ajustes = sd.WasapiSettings(auto_convert=True)
                except Exception:
                    ajustes = None
            with sd.RawOutputStream(samplerate=taxa, channels=1, dtype="int16",
                                    device=indice, extra_settings=ajustes) as s:
                s.write(pcm)
            resultado.put({"ok": True, "erro": ""})
        except Exception as exc:
            resultado.put({"ok": False, "erro": f"{type(exc).__name__}: {str(exc)[:110]}"})

    threading.Thread(target=trabalho, daemon=True).start()
    try:
        return resultado.get(timeout=prazo)
    except queue.Empty:
        return {"ok": False, "erro": "o aparelho travou e nao respondeu"}
