"""Outbound, multi-tenant JARVIS Remote Control bridge client."""

import asyncio
import base64
import hashlib
import json
import secrets
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import websockets

try:
    import certifi
except Exception:
    certifi = None


class PassoCancelado(RuntimeError):
    """O dono parou o computer use (ESC ou voz) enquanto a VPS decidia o passo."""


class RelayClient:
    def __init__(self, dashboard, base_dir: Path):
        self.dashboard = dashboard
        self.config_path = base_dir / "config" / "remote_bridge.json"
        self.config = self._load_config()
        self.device_id = self.config["device_id"]
        self.device_secret = self.config["device_secret"]
        self.public_origin = self.config.get("public_origin", "https://botzapbr.site").rstrip("/")
        self.ws_origin = self.public_origin.replace("https://", "wss://").replace("http://", "ws://")
        self.ws = None
        self.loop = None
        self.connected = False
        self.connected_event = threading.Event()
        self.send_lock = None
        self.last_pairing = None
        self.last_error = ""
        self.stream_tasks = {}
        self._job_waiters = {}
        self._job_transfers = {}
        # Uma fila por fala em andamento. O audio e consumido por uma thread
        # (a que toca o som), entao fila comum e nao future de asyncio.
        self._tts_filas = {}
        self._jobs_dir = base_dir / "logs" / "remote_jobs"

    def _load_config(self):
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if data.get("device_id") and data.get("device_secret"):
                return data
        except Exception:
            pass
        data = {
            "version": 1,
            "device_id": secrets.token_hex(16),
            "device_secret": secrets.token_urlsafe(48),
            "public_origin": "https://botzapbr.site",
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config_path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp.replace(self.config_path)
        return data

    def _save_config(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        temp.replace(self.config_path)

    def _reset_identity(self, reason: str = ""):
        self.config.update({
            "version": 1,
            "device_id": secrets.token_hex(16),
            "device_secret": secrets.token_urlsafe(48),
            "public_origin": self.public_origin,
        })
        self._save_config()
        self.device_id = self.config["device_id"]
        self.device_secret = self.config["device_secret"]
        self.last_pairing = None
        print(f"[JARVIS Relay] nova identidade remota gerada{': ' + reason if reason else ''}")

    @property
    def public_url(self):
        return f"{self.public_origin}/jarvis-remote/{self.device_id}"

    def pairing_url(self, key: str):
        return f"{self.public_url}#{urlencode({'key': key})}"

    def start(self, loop):
        self.loop = loop
        self.send_lock = asyncio.Lock()
        return loop.create_task(self._connection_loop())

    def publish_threadsafe(self, payload):
        if not self.loop or self.loop.is_closed():
            return None
        return asyncio.run_coroutine_threadsafe(self.send(payload), self.loop)

    def wait_connected(self, timeout: float = 5.0) -> bool:
        return self.connected_event.wait(max(0.0, float(timeout)))

    def _set_error(self, message: str):
        self.last_error = str(message or "").strip()
        if self.last_error:
            print(f"[JARVIS Relay] {self.last_error}")

    def _connect(self, url: str):
        headers = {"Authorization": f"Bearer {self.device_secret}"}
        ssl_context = None
        if url.lower().startswith("wss://") and certifi:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
        options = {
            "open_timeout": 15,
            "ping_interval": 20,
            "ping_timeout": 20,
            "max_size": 3 * 1024 * 1024,
        }
        if ssl_context:
            options["ssl"] = ssl_context
        try:
            return websockets.connect(url, additional_headers=headers, **options)
        except TypeError as exc:
            if "additional_headers" not in str(exc):
                raise
            return websockets.connect(url, extra_headers=headers, **options)

    def set_pairing(self, key: str, expires_at: float):
        self.last_pairing = {"type": "pairing", "key": key, "expires": int(expires_at * 1000)}
        future = self.publish_threadsafe(self.last_pairing)
        if future is None:
            return False
        try:
            return bool(future.result(timeout=3))
        except Exception:
            return False

    async def send(self, payload):
        if not self.connected or not self.ws:
            return False
        try:
            async with self.send_lock:
                await self.ws.send(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception:
            return False

    # TETO DA FALA QUE SOBE PARA A VPS (14/09/2026, trazido do TED). O relay
    # recusa mensagem maior que 2 MB (`maxPayload`) com um close do websocket,
    # nao com erro tratado. No pior caso (audio com chiado nao compacta):
    #     40 s x 16.000 x 2 bytes = 1,28 MB  ->  base64 = 1,71 MB   cabe
    #     60 s                    = 1,92 MB  ->  base64 = 2,56 MB   ESTOURA
    _TETO_STT_SEGUNDOS = 40.0

    def transcrever_pela_ponte(self, pcm: bytes, taxa: int = 16000,
                               timeout: float = 25.0, idioma: str = "pt") -> str:
        """Manda a fala para a VPS transcrever e devolve o texto.

        POR QUE EXISTE (14/09/2026). Num cliente sem placa NVIDIA (Ryzen 5
        5600G) o whisper local levava 2,0 a 3,6 s por frase — medido: quem pesa
        e o codificador, que trabalha 30 s de audio mesmo para um "sim" — errava
        o nome da voz ("Leda" saia "Lida" e "Leida") e jogava fora palavra
        curta. A VPS transcreve com o `gpt-4o-mini-transcribe`, a mesma peca
        que o TED usa desde 05/09 (`central-bots/jarvis-ouvido.js`).

        Sincrona de proposito: quem chama e a thread da fala. Estoura excecao
        quando nao da, e quem chama cai no whisper local, que continua no lugar
        como reserva. Devagar e ruim; surdo e inaceitavel.
        """
        import queue as _queue  # noqa: PLC0415
        import zlib  # noqa: PLC0415

        if not pcm:
            raise RuntimeError("sem audio para transcrever")
        segundos = len(pcm) / 2 / float(taxa or 16000)
        if segundos > self._TETO_STT_SEGUNDOS:
            # NAO CORTA A FALA: meia frase faz o cerebro responder a pergunta
            # errada com toda a confianca. Quem chamou vai para o local.
            raise RuntimeError(f"fala de {segundos:.0f}s passa do teto da ponte")
        if not self.loop or self.loop.is_closed():
            raise RuntimeError("a ponte ainda nao subiu")
        # 4 s e nao 8: o dono esta calado esperando. Ponte fora se descobre
        # rapido e a fala vai para o whisper local.
        if not self.wait_connected(timeout=4):
            raise RuntimeError(self.last_error or "a ponte esta offline")

        job_id = secrets.token_hex(16)
        chave = "stt:" + job_id
        fila = _queue.Queue()
        self._tts_filas[chave] = fila
        try:
            # zlib antes do base64: a internet do cliente costuma ter o ENVIO
            # bem mais estreito do que a descida.
            corpo = base64.b64encode(zlib.compress(pcm, 6)).decode("ascii")
            futuro = asyncio.run_coroutine_threadsafe(self.send({
                "type": "stt_pedido", "job_id": job_id,
                "taxa": int(taxa or 16000),
                "codec": "pcm16-zlib",
                "idioma": str(idioma or "pt"),
                "audio": corpo,
            }), self.loop)
            if not futuro.result(timeout=10):
                raise RuntimeError("nao consegui enviar o audio para a VPS")
            m = fila.get(timeout=float(timeout))
            if not m.get("ok"):
                raise RuntimeError(str(m.get("error") or "a VPS nao transcreveu"))
            # A VPS de antes de 24/09/2026 ignora o campo e transcreve em
            # portugues. So ela devolve o `idioma` que usou; sem ele, o texto
            # de um cliente em ingles viria errado — melhor o whisper local.
            if str(idioma or "pt") != "pt" and m.get("idioma") != idioma:
                raise RuntimeError(f"a VPS ainda nao transcreve em {idioma}")
            return str(m.get("texto") or "").strip()
        finally:
            self._tts_filas.pop(chave, None)

    def falar_pela_ponte(self, voz: str, texto: str, instrucoes: str,
                         on_chunk, timeout: float = 45.0,
                         velocidade: float = 0.0) -> float:
        """Manda o texto para a VPS falar e devolve o audio em pedacos.

        Sincrona de proposito: quem chama e a thread de audio. Devolve quantos
        segundos de audio vieram; zero significa que a VPS nao falou, e ai quem
        chamou cai na voz local.

        EM FLUXO. Cada pedaco vai para `on_chunk` no instante em que chega — e o
        que mantem o primeiro som em ~1,2 s. Juntar tudo para so entao tocar
        levaria a espera para 8 segundos e mataria a conversa.
        """
        import queue as _queue  # noqa: PLC0415
        if not self.loop or self.loop.is_closed():
            raise RuntimeError("a ponte de voz ainda nao subiu")
        if not self.wait_connected(timeout=8):
            raise RuntimeError(self.last_error or "a ponte de voz esta offline")

        job_id = secrets.token_hex(16)
        fila = _queue.Queue()
        self._tts_filas[job_id] = fila
        try:
            futuro = asyncio.run_coroutine_threadsafe(self.send({
                "type": "tts_submit", "job_id": job_id,
                "voz": str(voz or ""), "texto": str(texto or "")[:1200],
                "instrucoes": str(instrucoes or "")[:2000],
                # A PRESSA VAI NO PEDIDO, e nao no audio depois (05/09/2026).
                #
                # Ate hoje a VPS mandava a fala no ritmo natural e o PC do
                # cliente comprimia em 1,5x. Comprimir som come justamente o
                # que faz a voz soar gente: a micro-pausa antes do dado e a
                # respiracao entre as frases — o Diego ouviu e disse, com
                # razao, que continuava robotica. Com `velocidade` aqui, o
                # modelo JA GERA no ritmo certo, e o audio ainda sai mais curto
                # (mais barato, porque o que se paga e o audio de saida).
                "velocidade": float(velocidade or 0) or None,
            }), self.loop)
            if not futuro.result(timeout=10):
                raise RuntimeError("nao consegui enviar o texto para a VPS")

            bytes_vindos = 0
            proximo = 0
            limite = time.monotonic() + float(timeout)
            while True:
                m = fila.get(timeout=max(1.0, limite - time.monotonic()))
                tipo = str(m.get("type") or "")
                if tipo == "tts_chunk":
                    # A ordem importa: audio fora de ordem vira chiado.
                    if int(m.get("index", -1)) != proximo:
                        raise RuntimeError("os pedacos do audio vieram fora de ordem")
                    proximo += 1
                    bloco = base64.b64decode(str(m.get("data") or ""), validate=True)
                    bytes_vindos += len(bloco)
                    on_chunk(bloco)
                    continue
                if not m.get("ok"):
                    raise RuntimeError(str(m.get("error") or "a VPS nao conseguiu falar"))
                return bytes_vindos / 2 / 24000
        finally:
            self._tts_filas.pop(job_id, None)

    def pedir_passo_na_tela(self, job_id: str, tarefa: str, passo: int, jpeg: bytes,
                            largura: int, altura: int, historico: list,
                            timeout: float = 90.0, elementos: list | None = None,
                            contexto: dict | None = None, regras: list | None = None,
                            memoria: str = "", historico_seq: int | None = None,
                            cancelar=None) -> dict:
        """Manda a foto da tela e devolve o PROXIMO movimento, decidido na VPS.

        `cancelar()` (08/10/2026): o dono apertou ESC. A espera pela VPS (5 a
        7 s por passo, ate 95 s no pior caso) olha esse sinal 10 vezes por
        segundo e desiste na hora com `PassoCancelado` — antes o ESC so era
        visto DEPOIS de a VPS responder, e o JARVIS demorava para parar.

        Sincrona: quem chama e a thread da ferramenta `usar_computador`. A
        resposta chega em `cu_result` e cai na fila deste job (a mesma tabela
        das falas, com prefixo "cu:", para nao criar outro mapa).

        A foto vai inteira numa mensagem so (base64 de ~150 a 300 KB): o
        servidor de WebSocket aceita ate 100 MB por mensagem, e fatiar como a
        foto de edicao custaria mais idas e voltas do que o passo em si.
        """
        import queue as _queue  # noqa: PLC0415
        if not self.loop or self.loop.is_closed():
            raise RuntimeError("a ponte ainda nao subiu")
        if not self.wait_connected(timeout=8):
            raise RuntimeError(self.last_error or "a ponte esta offline")
        chave = "cu:" + str(job_id)
        fila = self._tts_filas.get(chave)
        if fila is None:
            fila = _queue.Queue()
            self._tts_filas[chave] = fila
        # O CORTE QUE SOBROU (achado em 23/09/2026). Em 22/09 a VPS passou a
        # aceitar 24 linhas de 1.800 caracteres — e ESTA linha continuou
        # mandando `[:200]` e `[-12:]`. Resultado: a saida de todo comando, o
        # texto das caixas de erro, os caminhos desta maquina e as regras da
        # tarefa chegavam cortados em 200 letras, e as regras fixas (as
        # PRIMEIRAS linhas) caiam fora do `[-12:]` a partir do 6o passo. O
        # conserto de 22/09 nunca chegou ao modelo. Agora o teto e o mesmo da
        # VPS, e as regras vao em campo proprio, que nao rola.
        pedido = {
            "type": "cu_step", "job_id": str(job_id), "passo": int(passo),
            "tarefa": str(tarefa or "")[:4000],
            "largura": int(largura), "altura": int(altura),
            "historico": [str(h)[:1800] for h in (historico or [])][-24:],
            # A lista NUMERADA dos elementos que existem na tela (o mesmo numero
            # esta desenhado na foto). E o que faz o Codex responder "clica no
            # elemento 12" em vez de chutar pixel. Ver `tela_visao.py`.
            "elementos": [str(e)[:160] for e in (elementos or [])][:60],
            "contexto": contexto or {},
            "imagem": base64.b64encode(jpeg).decode("ascii"),
        }
        if regras:
            pedido["regras"] = [str(r)[:4000] for r in regras][:12]
        if memoria:
            pedido["memoria"] = str(memoria)[:1500]
        if historico_seq is not None:
            pedido["historico_seq"] = int(historico_seq)
        futuro = asyncio.run_coroutine_threadsafe(self.send(pedido), self.loop)
        if not futuro.result(timeout=15):
            raise RuntimeError("nao consegui mandar a foto da tela para a VPS")
        if cancelar is None:
            m = fila.get(timeout=float(timeout))
        else:
            import time as _time  # noqa: PLC0415
            limite = _time.monotonic() + float(timeout)
            while True:
                try:
                    if cancelar():
                        raise PassoCancelado("o dono mandou parar")
                except PassoCancelado:
                    raise
                except Exception:
                    pass
                resta = limite - _time.monotonic()
                if resta <= 0:
                    raise _queue.Empty()
                try:
                    m = fila.get(timeout=min(0.1, resta))
                    break
                except _queue.Empty:
                    continue
        if not m.get("ok"):
            raise RuntimeError(str(m.get("error") or "a VPS nao decidiu o passo"))
        # A VPS nova manda `acoes` (ate 4 movimentos); a antiga manda `acao`
        # (um so). Os dois formatos passam — quem separa e `_lista_de_acoes`.
        if isinstance(m.get("acoes"), list):
            # vi / avaliacao / memoria: o que o modelo viu e anotou (VPS v3).
            return {"acoes": m["acoes"], "fala": m.get("fala") or "",
                    "vi": str(m.get("vi") or ""), "avaliacao": str(m.get("avaliacao") or ""),
                    "memoria": str(m.get("memoria") or ""), "v": m.get("v")}
        acao = m.get("acao")
        return acao if isinstance(acao, dict) else {}

    # ── COWORK (05/10/2026): o programador do JARVIS ───────────────────────
    #
    # Manda a COPIA do projeto (zip) e o pedido para o jarvis-cowork.js da VPS
    # e recebe de volta so o que mudou (outro zip). Mensagens proprias (cw_*):
    # nao passa pela fila dos sites/imagens/conversa, entao um trabalho de
    # varios minutos nao trava nada do resto.
    #
    # Sincrona e longa (minutos): quem chama e a thread do Cowork, nunca o laco
    # do audio. O base64 e a gravacao do arquivo acontecem nesta thread, e o
    # laco so repassa mensagens — a voz nao pode picotar por causa disto.
    _TETO_COWORK = 40 * 1024 * 1024

    def cowork_pela_ponte(self, dados: bytes, pedido: str, projeto: str, modo: str,
                          timeout: float = 1800.0, ao_progresso=None, cancelar=None) -> dict:
        import queue as _queue  # noqa: PLC0415
        if not self.loop or self.loop.is_closed():
            raise RuntimeError("a ponte com o servidor ainda nao subiu")
        if not self.wait_connected(timeout=15):
            raise RuntimeError(self.last_error or "a ponte com o servidor esta offline")
        if not dados or len(dados) > self._TETO_COWORK:
            raise RuntimeError("o projeto compactado passou do limite de 40 MB")
        job_id = secrets.token_hex(16)
        chave = "cw:" + job_id
        fila = _queue.Queue()
        self._tts_filas[chave] = fila
        destino = None
        arquivo = None

        def _avisa(texto):
            if ao_progresso and texto:
                try:
                    ao_progresso(str(texto))
                except Exception:
                    pass

        def _mandar(msg, prazo=30.0):
            fut = asyncio.run_coroutine_threadsafe(self.send(msg), self.loop)
            if not fut.result(timeout=prazo):
                raise RuntimeError("a conexão com o servidor caiu no envio")

        try:
            pedaco = 192 * 1024
            _avisa("enviando o projeto para o servidor")
            _mandar({"type": "cw_input_start", "job_id": job_id, "size": len(dados),
                     "sha256": hashlib.sha256(dados).hexdigest()})
            for i, p in enumerate(range(0, len(dados), pedaco)):
                if cancelar is not None and cancelar.is_set():
                    raise RuntimeError("cancelado")
                _mandar({"type": "cw_input_chunk", "job_id": job_id, "index": i,
                         "data": base64.b64encode(dados[p:p + pedaco]).decode("ascii")})
            _mandar({"type": "cw_input_end", "job_id": job_id})
            _mandar({"type": "cw_submit", "job_id": job_id, "description": str(pedido or "")[:8000],
                     "project_name": str(projeto or "Projeto")[:80],
                     "modo": "revisao" if modo == "revisao" else "trabalho"})
            enviado_em = time.monotonic()
            limite = enviado_em + float(timeout)
            confirmado = False
            # SINAL DE VIDA: o servidor novo manda "vivo" a cada 20 s. Com ele,
            # 90 s calado = o servidor reiniciou e o trabalho morreu (antes o PC
            # esperava meia hora a toa). Servidor sem o sinal: 15 min calado.
            ultimo_sinal = enviado_em
            tem_batimento = False
            cancel_enviado = False
            sem_conexao_desde = None
            esperado = recebido = proximo = 0
            hash_esperado = ""
            hasher = None
            while True:
                if cancelar is not None and cancelar.is_set() and not cancel_enviado:
                    cancel_enviado = True
                    try:
                        _mandar({"type": "cw_cancelar", "job_id": job_id}, prazo=10.0)
                    except Exception:
                        pass
                agora = time.monotonic()
                if agora > limite:
                    raise RuntimeError("o trabalho passou do tempo limite")
                # Servidor antigo (sem o jarvis-cowork.js) ignora as mensagens
                # cw_* em silencio: sem isto o JARVIS esperaria meia hora.
                if not confirmado and agora - enviado_em > 25.0:
                    raise RuntimeError("o servidor do JARVIS ainda não foi atualizado com o Cowork")
                if not self.connected:
                    sem_conexao_desde = sem_conexao_desde or agora
                    if agora - sem_conexao_desde > 90.0:
                        raise RuntimeError("a conexão com o servidor caiu durante o trabalho")
                else:
                    sem_conexao_desde = None
                calado = agora - ultimo_sinal
                if confirmado and ((tem_batimento and calado > 90.0) or calado > 900.0):
                    raise RuntimeError("o servidor parou de responder no meio do trabalho "
                                       "(provavelmente foi reiniciado); peça de novo")
                try:
                    m = fila.get(timeout=1.0)
                except _queue.Empty:
                    continue
                tipo = str(m.get("type") or "")
                ultimo_sinal = time.monotonic()
                if m.get("vivo"):
                    tem_batimento = True
                if tipo != "cw_input_ack":
                    confirmado = True
                if tipo == "cw_status":
                    pos = int(m.get("posicao") or 0)
                    if m.get("status") == "queued" and pos > 1:
                        _avisa(f"na fila do servidor (posição {pos})")
                elif tipo == "cw_progresso":
                    _avisa(m.get("texto"))
                elif tipo == "cw_result_start":
                    esperado = int(m.get("size") or 0)
                    if esperado <= 0 or esperado > 50 * 1024 * 1024:
                        raise RuntimeError("o servidor mandou um resultado de tamanho invalido")
                    hash_esperado = str(m.get("sha256") or "").lower()
                    self._jobs_dir.mkdir(parents=True, exist_ok=True)
                    destino = self._jobs_dir / f"cowork-{job_id}.zip"
                    arquivo = open(destino, "wb")
                    hasher = hashlib.sha256()
                    recebido = proximo = 0
                    _avisa("recebendo as mudanças")
                elif tipo == "cw_result_chunk":
                    if arquivo is None or int(m.get("index", -1)) != proximo:
                        raise RuntimeError("as mudancas chegaram fora de ordem")
                    bloco = base64.b64decode(str(m.get("data") or ""), validate=True)
                    recebido += len(bloco)
                    if recebido > esperado:
                        raise RuntimeError("as mudancas chegaram maiores que o anunciado")
                    arquivo.write(bloco)
                    hasher.update(bloco)
                    proximo += 1
                elif tipo == "cw_result_end":
                    if arquivo is not None:
                        arquivo.close()
                        arquivo = None
                    if recebido != esperado or (hash_esperado and hasher and hasher.hexdigest() != hash_esperado):
                        raise RuntimeError("as mudancas chegaram corrompidas")
                elif tipo == "cw_result":
                    if not m.get("ok"):
                        raise RuntimeError(str(m.get("error") or "o Cowork falhou no servidor"))
                    if destino is None or arquivo is not None or not destino.is_file():
                        raise RuntimeError("o servidor terminou sem mandar as mudancas")
                    return {"zip": str(destino), "resumo": str(m.get("resumo") or ""),
                            "alterados": int(m.get("alterados") or 0),
                            "removidos": int(m.get("removidos") or 0)}
        except Exception:
            # Desistiu (tempo, cancelado, conexao): avisa a VPS para nao seguir
            # gastando com um trabalho que ninguem vai receber.
            try:
                asyncio.run_coroutine_threadsafe(
                    self.send({"type": "cw_cancelar", "job_id": job_id}), self.loop).result(timeout=5)
            except Exception:
                pass
            if arquivo is not None:
                try:
                    arquivo.close()
                except Exception:
                    pass
                arquivo = None
            if destino is not None:
                try:
                    destino.unlink()
                except Exception:
                    pass
            raise
        finally:
            self._tts_filas.pop(chave, None)

    def pensar_pela_ponte(self, job_id: str, pergunta: str, historico: list,
                          ferramentas: list, contexto: dict | None = None,
                          resultado: str = "", timeout: float = 80.0,
                          modo: str = "", imagem: bytes | None = None) -> dict:
        """Manda a fala do dono e devolve a DECISAO do Codex na VPS.

        Desde 04/09/2026 e por aqui que o JARVIS pensa. Antes ele dependia das
        chaves gratuitas do cliente (Gemini com 429 a cada poucas perguntas,
        OpenRouter com 50 pedidos por dia); agora o cerebro roda no plano do
        Diego, sem cota e sem cobranca por uso.

        Devolve o dicionario ja validado la:
            {"fala": "..."}                                  so conversa
            {"fala": "...", "ferramenta": "nome", "args": {}} chamar ferramenta

        `resultado` e a SEGUNDA volta: o PC executou a ferramenta e manda o que
        aconteceu, para ele fechar com a frase final. E o mesmo desenho do
        computer use — decisao la, execucao aqui, sempre.
        """
        import queue as _queue  # noqa: PLC0415
        if not self.loop or self.loop.is_closed():
            raise RuntimeError("a ponte ainda nao subiu")
        if not self.wait_connected(timeout=8):
            raise RuntimeError(self.last_error or "a ponte esta offline")
        chave = "cb:" + str(job_id)
        fila = self._tts_filas.get(chave)
        if fila is None:
            fila = _queue.Queue()
            self._tts_filas[chave] = fila
        futuro = asyncio.run_coroutine_threadsafe(self.send({
            "type": "cb_pergunta", "job_id": str(job_id),
            "pergunta": str(pergunta or "")[:4000],
            "resultado": str(resultado or "")[:6000],
            "historico": [str(h)[:400] for h in (historico or [])][-8:],
            # 700 e nao 400: com 400, DEZ das 35 ferramentas chegavam cortadas
            # no meio da descricao — e ferramenta cuja descricao sumiu e, para o
            # modelo, ferramenta que nao existe ("a ferramenta necessaria nao
            # esta disponivel", 05/09/2026). O custo sao ~1.000 fichas a mais.
            "ferramentas": [str(f)[:700] for f in (ferramentas or [])][:40],
            "contexto": contexto or {},
            # `modo="texto"` avisa que o pedido e de CONTEUDO, nao de acao: a
            # VPS troca o prompt inteiro (sem JSON, sem ferramenta) e devolve o
            # texto puro. Sem isso, o modelo vestido de assistente respondia
            # "nao tenho ferramenta para digitar" — e o JARVIS digitava ISSO.
            "modo": str(modo or ""),
            # A FOTO, quando o pedido e de VISAO (`modo="imagem"`). Mesmo
            # caminho que o computer use ja usa para a tela: base64 numa
            # mensagem so. E o socorro de quando a cota da Gemini acaba.
            "imagem": (base64.b64encode(imagem).decode("ascii") if imagem else ""),
        }), self.loop)
        if not futuro.result(timeout=15):
            raise RuntimeError("nao consegui mandar a pergunta para a VPS")
        m = fila.get(timeout=float(timeout))
        if not m.get("ok"):
            raise RuntimeError(str(m.get("error") or "a VPS nao respondeu a pergunta"))
        resposta = m.get("resposta")
        return resposta if isinstance(resposta, dict) else {}

    def encerrar_conversa_na_ponte(self, job_id: str) -> None:
        """Solta a fila e avisa a VPS para fechar a thread do Codex."""
        self._tts_filas.pop("cb:" + str(job_id), None)
        if self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self.send({"type": "cb_fim", "job_id": str(job_id)}), self.loop)

    def encerrar_passos_na_tela(self, job_id: str) -> None:
        """Solta a fila do job e avisa a VPS para fechar a sessao do Codex."""
        self._tts_filas.pop("cu:" + str(job_id), None)
        if self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self.send({"type": "cu_end", "job_id": str(job_id)}), self.loop)

    def submit_codex_job(self, kind: str, description: str, project_name: str = "JarvisProjeto", timeout: float = 960.0,
                         imagem: bytes | None = None):
        """Submit a site/image job to the authenticated VPS bridge and wait for the file.

        When `imagem` is given, the picture is streamed first and the job is
        treated as an edit of that picture.
        """
        if not self.loop or self.loop.is_closed():
            raise RuntimeError("A ponte remota ainda não foi iniciada.")
        if not self.wait_connected(timeout=15):
            detail = self.last_error or "servidor remoto offline"
            raise RuntimeError(f"A ponte de geração está offline: {detail}")

        # A foto é fatiada e codificada AQUI, que roda fora do loop do áudio.
        # Fazer isto no loop deixava a voz do JARVIS picotada.
        pacote = None
        if imagem:
            if len(imagem) > 12 * 1024 * 1024:
                raise RuntimeError("A imagem é grande demais para ser enviada.")
            pedaco = 192 * 1024      # ~256 KB em base64, dentro do limite por mensagem
            pacote = {
                "inicio": {
                    "type": "job_input_start",
                    "size": len(imagem),
                    "sha256": hashlib.sha256(imagem).hexdigest(),
                },
                "blocos": [
                    {
                        "type": "job_input_chunk",
                        "index": i,
                        "data": base64.b64encode(imagem[p:p + pedaco]).decode("ascii"),
                    }
                    for i, p in enumerate(range(0, len(imagem), pedaco))
                ],
            }

        future = asyncio.run_coroutine_threadsafe(
            self._submit_codex_job(kind, description, project_name, timeout, pacote),
            self.loop,
        )
        try:
            return future.result(timeout=float(timeout) + 20.0)
        except Exception:
            future.cancel()
            raise

    async def _submit_codex_job(self, kind: str, description: str, project_name: str, timeout: float,
                                imagem: dict | None = None):
        job_id = secrets.token_hex(16)
        waiter = asyncio.get_running_loop().create_future()
        self._job_waiters[job_id] = waiter

        # Editar uma foto exige mandar a foto ANTES do pedido, em blocos.
        # Os blocos chegam aqui JA codificados: este laço roda no mesmo loop que
        # cuida do áudio do JARVIS, e converter megabytes para base64 aqui dentro
        # fazia a voz picotar. Aqui só resta enviar.
        if imagem:
            if not await self.send({**imagem["inicio"], "job_id": job_id}):
                self._job_waiters.pop(job_id, None)
                raise RuntimeError("Não foi possível enviar a imagem.")
            for bloco in imagem["blocos"]:
                if not await self.send({**bloco, "job_id": job_id}):
                    self._job_waiters.pop(job_id, None)
                    raise RuntimeError("A imagem não foi enviada por completo.")
                # Devolve a vez ao loop entre um bloco e outro, para o áudio
                # continuar fluindo enquanto a foto sobe.
                await asyncio.sleep(0)
            if not await self.send({"type": "job_input_end", "job_id": job_id}):
                self._job_waiters.pop(job_id, None)
                raise RuntimeError("A imagem não foi enviada por completo.")

        payload = {
            "type": "job_submit",
            "job_id": job_id,
            "kind": str(kind or "").strip().lower(),
            "description": str(description or "").strip()[:6000],
            "project_name": str(project_name or "JarvisProjeto").strip()[:80],
        }
        if not await self.send(payload):
            self._job_waiters.pop(job_id, None)
            raise RuntimeError("Não foi possível enviar a tarefa para a VPS.")
        try:
            return await asyncio.wait_for(waiter, timeout=max(30.0, float(timeout)))
        finally:
            self._job_waiters.pop(job_id, None)
            transfer = self._job_transfers.pop(job_id, None)
            if transfer:
                try:
                    transfer["file"].close()
                except Exception:
                    pass

    def _finish_job(self, job_id, value=None, error=None):
        waiter = self._job_waiters.get(job_id)
        if not waiter or waiter.done():
            return
        if error:
            waiter.set_exception(RuntimeError(str(error)))
        else:
            waiter.set_result(value)

    def _job_target(self, filename: str) -> Path:
        safe = Path(str(filename or "resultado.bin")).name
        if not safe or safe in {".", ".."}:
            safe = "resultado.bin"
        # Keep the customer-visible output path neutral. Internal worker names
        # never need to appear in the files delivered to the customer.
        root = Path.home() / "Desktop" / "JarvisResultados"
        root.mkdir(parents=True, exist_ok=True)
        candidate = root / safe
        if not candidate.exists():
            return candidate
        stem, suffix = candidate.stem, candidate.suffix
        for index in range(2, 1000):
            alternative = root / f"{stem}_{index}{suffix}"
            if not alternative.exists():
                return alternative
        raise RuntimeError("Não foi possível escolher um nome para o arquivo recebido.")

    async def _handle_job_message(self, message):
        job_id = str(message.get("job_id") or "")
        if not job_id:
            return
        message_type = str(message.get("type") or "")
        if message_type == "job_status":
            return
        if message_type == "job_result_start":
            try:
                size = int(message.get("size") or 0)
                if size <= 0 or size > 50 * 1024 * 1024:
                    raise ValueError("O arquivo remoto excede o limite permitido.")
                filename = Path(str(message.get("filename") or "resultado.bin")).name
                temp_dir = self._jobs_dir / job_id
                temp_dir.mkdir(parents=True, exist_ok=True)
                temp_path = temp_dir / "payload.part"
                handle = temp_path.open("wb")
                self._job_transfers[job_id] = {
                    "file": handle,
                    "temp_path": temp_path,
                    "filename": filename,
                    "mime": str(message.get("mime") or "application/octet-stream"),
                    "size": size,
                    "sha256": str(message.get("sha256") or "").lower(),
                    "received": 0,
                    "next_index": 0,
                    "done": False,
                }
            except Exception as exc:
                self._finish_job(job_id, error=exc)
            return
        if message_type == "job_result_chunk":
            state = self._job_transfers.get(job_id)
            if not state:
                return
            try:
                index = int(message.get("index"))
                if index != state["next_index"]:
                    raise ValueError("Ordem de partes do arquivo inválida.")
                chunk = base64.b64decode(str(message.get("data") or ""), validate=True)
                state["received"] += len(chunk)
                if state["received"] > state["size"]:
                    raise ValueError("O tamanho recebido não corresponde ao informado.")
                state["file"].write(chunk)
                state["next_index"] += 1
                # Este laço divide o loop com o áudio do JARVIS. Devolver a vez
                # entre um pedaço e outro evita que a voz picote enquanto o
                # arquivo pronto está descendo.
                await asyncio.sleep(0)
            except Exception as exc:
                try:
                    state["file"].close()
                except Exception:
                    pass
                self._finish_job(job_id, error=exc)
            return
        if message_type == "job_result_end":
            state = self._job_transfers.get(job_id)
            if not state:
                return
            try:
                state["file"].flush()
                state["file"].close()
                if state["received"] != state["size"]:
                    raise ValueError("O arquivo recebido ficou incompleto.")
                digest = await asyncio.to_thread(lambda: hashlib.sha256(state["temp_path"].read_bytes()).hexdigest())
                if state["sha256"] and digest != state["sha256"]:
                    raise ValueError("A verificação de integridade do arquivo falhou.")
                state["done"] = True
            except Exception as exc:
                self._finish_job(job_id, error=exc)
            return
        if message_type == "job_result":
            if not bool(message.get("ok")):
                self._finish_job(job_id, error=message.get("error") or "A tarefa remota falhou.")
                return
            state = self._job_transfers.get(job_id)
            if not state or not state.get("done"):
                return
            try:
                target = self._job_target(state["filename"])
                await asyncio.to_thread(target.write_bytes, state["temp_path"].read_bytes())
                self._finish_job(job_id, value={
                    "ok": True,
                    "path": str(target),
                    "filename": target.name,
                    "mime": state["mime"],
                    "size": state["received"],
                    "sha256": state["sha256"],
                })
            except Exception as exc:
                self._finish_job(job_id, error=exc)

    async def publish_event(self, event):
        safe = dict(event or {})
        if safe.get("type") in {"screen_frame", "webcam_frame"}:
            return
        if safe.get("type") == "screen_capture" and self.dashboard._screen_image:
            safe["data"] = base64.b64encode(self.dashboard._screen_image).decode("ascii")
        if safe.get("type") in {"screen_live_started", "webcam_started"}:
            stream = "screen" if safe["type"].startswith("screen") else "webcam"
            old = self.stream_tasks.pop(stream, None)
            if old:
                old.cancel()
            self.stream_tasks[stream] = asyncio.create_task(self._stream_frames(stream))
        if safe.get("type") in {"screen_live_stopped", "webcam_stopped"}:
            stream = "screen" if safe["type"].startswith("screen") else "webcam"
            task = self.stream_tasks.pop(stream, None)
            if task:
                task.cancel()
        await self.send({"type": "event", "event": safe})

    async def _connection_loop(self):
        delay = 1
        quick_disconnects = 0
        while True:
            query = urlencode({"device": self.device_id})
            url = f"{self.ws_origin}/jarvis-relay/device?{query}"
            reset_identity = False
            connected_at = 0.0
            try:
                async with self._connect(url) as ws:
                    self.ws = ws
                    self.connected = True
                    self.connected_event.set()
                    self.last_error = ""
                    connected_at = time.time()
                    delay = 1
                    if self.last_pairing and self.last_pairing["expires"] > int(time.time() * 1000):
                        await self.send(self.last_pairing)
                    async for raw in ws:
                        try:
                            message = json.loads(raw)
                        except Exception:
                            continue
                        await self._handle_message(message)
                    reset_identity = ws.close_code == 4000 and "replaced" in str(ws.close_reason or "").lower()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._set_error(f"offline: {type(exc).__name__}: {exc}")
            finally:
                lifetime = (time.time() - connected_at) if connected_at else 0.0
                if connected_at and lifetime < 4.0:
                    quick_disconnects += 1
                elif lifetime >= 15.0:
                    quick_disconnects = 0
                if quick_disconnects >= 2:
                    reset_identity = True
                self.connected = False
                self.connected_event.clear()
                self.ws = None
                for job_id in list(self._job_waiters):
                    self._finish_job(job_id, error="A ponte remota foi desconectada durante a tarefa.")
                for state in list(self._job_transfers.values()):
                    try:
                        state["file"].close()
                    except Exception:
                        pass
                self._job_transfers.clear()
                for task in list(self.stream_tasks.values()):
                    task.cancel()
                self.stream_tasks.clear()
                if reset_identity:
                    self._reset_identity("conexao remota instavel ou dispositivo duplicado")
                    quick_disconnects = 0
                    delay = 1
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)

    async def _handle_message(self, message):
        if message.get("type") == "ping":
            await self.send({"type": "pong", "at": message.get("at")})
            return
        if message.get("type") in {"tts_chunk", "tts_end"}:
            fila = self._tts_filas.get(str(message.get("job_id") or ""))
            if fila is not None:
                fila.put(message)
            return
        # Olhar e clicar: a resposta de UM passo (ver `pedir_passo_na_tela`).
        if message.get("type") == "cu_result":
            fila = self._tts_filas.get("cu:" + str(message.get("job_id") or ""))
            if fila is not None:
                fila.put(message)
            return
        # Ouvir: o texto da fala transcrita na VPS (ver `transcrever_pela_ponte`).
        if message.get("type") == "stt_result":
            fila = self._tts_filas.get("stt:" + str(message.get("job_id") or ""))
            if fila is not None:
                fila.put(message)
            return
        # Pensar: a decisao do Codex (ver `pensar_pela_ponte`).
        if message.get("type") == "cb_result":
            fila = self._tts_filas.get("cb:" + str(message.get("job_id") or ""))
            if fila is not None:
                fila.put(message)
            return
        # Cowork: tudo que vem com cw_ vai para a fila do trabalho (ver
        # `cowork_pela_ponte`). So repassa: decodificar e gravar e na thread dele.
        if str(message.get("type") or "").startswith("cw_"):
            fila = self._tts_filas.get("cw:" + str(message.get("job_id") or ""))
            if fila is not None:
                fila.put(message)
            return
        if message.get("type") in {"job_status", "job_result_start", "job_result_chunk", "job_result_end", "job_result"}:
            await self._handle_job_message(message)
            return
        if message.get("type") != "phone_command":
            return
        payload = message.get("payload") or {}
        kind = payload.get("type")
        if kind == "command":
            text = str(payload.get("text") or "").strip()
            if text:
                await self.dashboard._command_queue.put(text)
                if self.dashboard._wake_callback:
                    self.dashboard._wake_callback()
            return
        if kind == "system" and payload.get("confirmed") is True:
            result = await self.dashboard.remote_system_action(str(payload.get("action") or ""))
            await self.dashboard.broadcast({"type": "log", "speaker": "jarvis", "text": result})
            return
        if kind != "action":
            return
        action = str(payload.get("action") or "")
        if action == "screenshot":
            result = await self.dashboard.remote_media_action("screenshot", announce=False)
            await self.dashboard.broadcast({"type": "log", "speaker": "jarvis", "text": result})
        elif action in {"screen_live_start", "webcam_start"}:
            await self.dashboard.remote_media_action(action, announce=False)
        elif action in {"screen_live_stop", "webcam_stop"}:
            await self.dashboard.remote_media_action(action, announce=False)

    async def _stream_frames(self, stream):
        if stream == "screen":
            try:
                while self.connected and self.dashboard._screen_live_active:
                    data = await asyncio.to_thread(self.dashboard._capture_screen_jpeg)
                    await self.send({"type": "event", "event": {"type": "screen_frame", "data": base64.b64encode(data).decode("ascii")}})
                    await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                pass
            return

        internal_token = "relay-" + secrets.token_urlsafe(12)
        self.dashboard._tokens.add(internal_token)
        generation = self.dashboard._webcam_generation
        generator = self.dashboard._webcam_frames(internal_token, generation)
        def next_frame():
            try:
                return next(generator)
            except StopIteration:
                return None
        try:
            while self.connected and self.dashboard._webcam_active:
                chunk = await asyncio.to_thread(next_frame)
                if chunk is None:
                    break
                marker = chunk.find(b"\r\n\r\n")
                data = chunk[marker + 4:].rstrip(b"\r\n") if marker >= 0 else chunk
                await self.send({"type": "event", "event": {"type": "webcam_frame", "data": base64.b64encode(data).decode("ascii")}})
        except asyncio.CancelledError:
            pass
        finally:
            self.dashboard._tokens.discard(internal_token)
            try:
                generator.close()
            except Exception:
                pass
