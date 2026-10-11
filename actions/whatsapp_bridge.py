from __future__ import annotations

import json
import os
import zipfile
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import concurrent.futures
from pathlib import Path
from datetime import datetime, timedelta

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
BRIDGE_DIR = BASE_DIR / "whatsapp_bridge"
SERVER_JS = BRIDGE_DIR / "server.js"
PORT = int(os.environ.get("MARKXLVIII_WA_PORT") or os.environ.get("CYBERBOT_WA_PORT") or "8765")
BASE_URL = f"http://127.0.0.1:{PORT}"
# ── "EU ESTOU CONECTADO E ELE DIZ QUE NAO" (22/09/2026, queixa do Diego) ────
#
# Esta linha era um numero de versao ESCRITO A MAO, e ela nao batia com a
# versao do `whatsapp_bridge/server.js` que entregamos:
#
#     aqui .......... 2026-07-18-whatsapp-delete-batch-35
#     server.js ..... 2026-07-25-private-history-37
#
# Quem compara e o `_validate_status_payload`, e a resposta dele para versao
# diferente e categorica: `{"connected": False, "error": "Ponte WhatsApp antiga
# detectada"}`. Ou seja: com o WhatsApp conectado, pareado e ENVIANDO mensagem
# (o envio nao passa por essa conferencia), perguntar "estou conectado?"
# respondia NAO. E o `start_bridge` tambem nunca reconhecia a ponte que ja
# estava de pe, e a reiniciava a toa.
#
# O conserto nao e trocar o numero — e parar de ter dois numeros. A versao
# esperada passa a ser LIDA do proprio `server.js` que vai no pacote, entao
# eles nao tem como divergir de novo. A constante fica so como reserva para o
# caso de o arquivo nao poder ser lido.
_BRIDGE_VERSION_FALLBACK = "2026-09-23-uma-conexao-38"
_VERSAO_ESPERADA_CACHE: str | None = None


def _expected_bridge_version() -> str:
    """A versao do `server.js` DESTA instalacao (lida uma vez e guardada)."""
    global _VERSAO_ESPERADA_CACHE
    if _VERSAO_ESPERADA_CACHE:
        return _VERSAO_ESPERADA_CACHE
    versao = ""
    try:
        texto = SERVER_JS.read_text(encoding="utf-8", errors="ignore")[:4000]
        achado = re.search(r"""const\s+VERSION\s*=\s*['"]([^'"]+)['"]""", texto)
        if achado:
            versao = achado.group(1).strip()
    except Exception:
        versao = ""
    _VERSAO_ESPERADA_CACHE = versao or _BRIDGE_VERSION_FALLBACK
    return _VERSAO_ESPERADA_CACHE


# Mantido pelo nome antigo porque o resto do arquivo o usa; agora e so reserva.
EXPECTED_BRIDGE_VERSION = _BRIDGE_VERSION_FALLBACK
_proc = None
_DELETE_GUARD_LOCK = threading.Lock()
_DELETE_GUARD = {"signature": "", "at": 0.0, "result": ""}
_SEND_BATCH_GUARD_LOCK = threading.Lock()
_SEND_BATCH_GUARD = {"signature": "", "at": 0.0, "result": ""}
SCHEDULE_FILE = BRIDGE_DIR / "mark_xlviii_scheduled_messages.json"
AGENT_FILE = BRIDGE_DIR / "mark_xlviii_whatsapp_agent.json"
PENDING_SCHEDULE_FILE = BRIDGE_DIR / "mark_xlviii_pending_schedule.json"
AGENT_KNOWLEDGE_DIR = BRIDGE_DIR / "agent_knowledge"
BOOT_ID = str(int(time.time()))
WHATSAPP_OUTGOING_ZIP_DIR = BRIDGE_DIR / "outgoing_zips"
WHATSAPP_MAX_FILE_MB = int(os.environ.get("MARKXLVIII_WHATSAPP_MAX_FILE_MB") or os.environ.get("CYBERBOT_WHATSAPP_MAX_FILE_MB") or "100")
WHATSAPP_MAX_FILE_BYTES = WHATSAPP_MAX_FILE_MB * 1024 * 1024

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

LOCAL_TZ_NAME = os.environ.get("MARKXLVIII_LOCAL_TZ") or os.environ.get("CYBERBOT_LOCAL_TZ") or os.environ.get("CYBERBOT_TZ") or "America/Fortaleza"

def _now_local() -> datetime:
    """Data/hora real usada pelo Jarvis para WhatsApp e agendamentos.

    Não depende da data que a IA imaginar. Por padrão usa o fuso do Brasil
    sem horário de verão (America/Fortaleza), que é o fuso de Patos/PB.
    Quem vender para outro fuso pode definir MARKXLVIII_LOCAL_TZ.
    """
    try:
        if ZoneInfo:
            return datetime.now(ZoneInfo(LOCAL_TZ_NAME)).replace(tzinfo=None)
    except Exception:
        pass
    return datetime.now()

def _today_local_date():
    return _now_local().date()

def _local_now_context_pt() -> str:
    now = _now_local()
    dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
    try:
        return f"{dias[now.weekday()]}, {now.day:02d} de {meses[now.month-1]} de {now.year}, {now:%H:%M}"
    except Exception:
        return now.strftime("%d/%m/%Y %H:%M")


def _creationflags():
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def is_bridge_alive() -> bool:
    try:
        # 500 ms gerava falsos offline quando o Node estava salvando mídia ou
        # atualizando o banco local. A ponte é local; 4 s ainda é rápido, e
        # cobre a sincronizacao de historico, que segura o Node de uma linha so.
        r = requests.get(f"{BASE_URL}/status", timeout=4.0)
        return r.status_code == 200
    except Exception:
        # O processo pode estar VIVO e so ocupado: se o arquivo de status foi
        # escrito ha pouco, a ponte esta de pe (22/09/2026).
        return _fresh_status_file(20.0) is not None


def _kill_old_bridge_port():
    if os.name != "nt":
        return
    killed_pids: set[int] = set()
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            errors="ignore",
            timeout=5,
            creationflags=_creationflags(),
        )
        pids: set[str] = set()
        port_suffix = f":{PORT}"
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP":
                continue
            local_address, state, pid = parts[1], parts[3].upper(), parts[4]
            if local_address.endswith(port_suffix) and state == "LISTENING" and pid.isdigit():
                pids.add(pid)
        for pid in pids:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", pid],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=_creationflags(),
            )
            killed_pids.add(int(pid))
        if pids:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and is_bridge_alive():
                time.sleep(0.2)
    except Exception:
        pass

    # Uma tentativa que perde a disputa pela porta pode deixar um node.exe
    # órfão sem LISTENING. Limpa somente o server.js iniciado pelo runtime e
    # diretório do próprio JARVIS; outros processos Node do computador ficam
    # intocados. psutil é opcional para manter compatibilidade com instalações
    # antigas que não o incluam.
    try:
        import psutil
        node_path = _node_executable()
        expected_node = os.path.normcase(os.path.abspath(str(node_path or "")))
        expected_server = os.path.normcase(os.path.abspath(str(SERVER_JS)))
        expected_cwd = os.path.normcase(os.path.abspath(str(BRIDGE_DIR)))
        if not expected_node:
            return
        for proc in psutil.process_iter(["pid", "exe", "cmdline", "cwd"]):
            try:
                pid = int(proc.info.get("pid") or 0)
                exe = os.path.normcase(os.path.abspath(str(proc.info.get("exe") or "")))
                cmdline = [str(x) for x in (proc.info.get("cmdline") or [])]
                cwd = os.path.normcase(os.path.abspath(str(proc.info.get("cwd") or "")))
                if not pid or pid in killed_pids or exe != expected_node or not cmdline:
                    continue
                server_args = []
                for arg in cmdline[1:]:
                    if os.path.basename(arg).lower() == "server.js":
                        server_args.append(os.path.normcase(os.path.abspath(os.path.join(cwd or expected_cwd, arg))))
                if expected_server not in server_args or cwd != expected_cwd:
                    continue
                for child in proc.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
                proc.kill()
                killed_pids.add(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
                continue
        if killed_pids:
            try:
                psutil.wait_procs([psutil.Process(pid) for pid in killed_pids if psutil.pid_exists(pid)], timeout=3)
            except Exception:
                pass
    except Exception:
        pass




def _save_bridge_status_local(**extra) -> None:
    """Atualiza status.json mesmo quando o Node ainda não conseguiu subir.

    A janela do QR da interface lê este arquivo como fallback. Sem isso, quando
    faltavam dependências ou o Node encerrava antes de abrir a porta, a tela
    ficava presa em "PONTE WHATSAPP INICIANDO" sem explicar o motivo.
    """
    try:
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "ok": False,
            "bridge": "mark-xlviii-whatsapp-baileys",
            "version": EXPECTED_BRIDGE_VERSION,
            "bridge_root": str(BRIDGE_DIR),
            "connected": False,
            "has_qr": False,
            "updated_at": datetime.now().isoformat(),
        }
        payload.update(extra)
        (BRIDGE_DIR / "status.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _node_executable() -> str | None:
    private_node = BASE_DIR / "runtime" / "node" / ("node.exe" if os.name == "nt" else "node")
    if private_node.is_file():
        return str(private_node)
    """Retorna o executável do Node no Windows/Linux, ou None se não existir."""
    try:
        import shutil
        exe = shutil.which("node")
        if exe:
            return exe
    except Exception:
        pass
    return None


def _npm_executable() -> str | None:
    private_npm = BASE_DIR / "runtime" / "node" / ("npm.cmd" if os.name == "nt" else "npm")
    if private_npm.is_file():
        return str(private_npm)
    try:
        import shutil
        exe = shutil.which("npm")
        if exe:
            return exe
    except Exception:
        pass
    return None


def _deps_installed() -> bool:
    """Confere dependências mínimas do bridge.

    Não exige node_modules completo; só os pacotes essenciais para o QR subir.
    """
    needed = [
        BRIDGE_DIR / "node_modules" / "express",
        BRIDGE_DIR / "node_modules" / "qrcode",
        BRIDGE_DIR / "node_modules" / "@whiskeysockets" / "baileys",
    ]
    return all(p.exists() for p in needed)


def _ensure_node_dependencies(timeout_seconds: int = 240) -> tuple[bool, str]:
    """Instala dependências do WhatsApp Bridge se ainda não existirem.

    Roda dentro da thread acionada pelo botão/comando, então não trava a UI.
    Enquanto instala, a janela do QR mostra status via status.json.
    """
    if _deps_installed():
        return True, "Dependências WhatsApp OK."
    npm = _npm_executable()
    if not npm:
        msg = "npm não encontrado. Instale o Node.js LTS e rode 8_instalar_whatsapp_bridge.bat."
        _save_bridge_status_local(message=msg, error=msg, last_event="npm_missing")
        return False, msg
    if not (BRIDGE_DIR / "package.json").exists():
        msg = "package.json do WhatsApp Bridge não encontrado. Reinstale o módulo WhatsApp."
        _save_bridge_status_local(message=msg, error=msg, last_event="package_missing")
        return False, msg

    _save_bridge_status_local(
        message="Instalando dependências do WhatsApp Bridge. Aguarde; isso pode demorar alguns minutos na primeira vez.",
        last_event="installing_dependencies",
    )
    try:
        log_file = BRIDGE_DIR / "bridge_install.log"
        with open(log_file, "a", encoding="utf-8", errors="ignore") as out:
            out.write(f"\n[{datetime.now().isoformat()}] npm install iniciado pelo JARVIS\n")
            cmd = ["cmd", "/c", "npm", "install"] if os.name == "nt" else [npm, "install"]
            proc = subprocess.run(
                cmd,
                cwd=str(BRIDGE_DIR),
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=timeout_seconds,
                creationflags=_creationflags(),
            )
        if proc.returncode != 0:
            msg = "Falha ao instalar dependências do WhatsApp Bridge. Rode 8_instalar_whatsapp_bridge.bat e veja bridge_install.log."
            _save_bridge_status_local(message=msg, error=msg, last_event="install_failed")
            return False, msg
        if not _deps_installed():
            msg = "Instalação terminou, mas dependências essenciais não foram encontradas. Rode 8_instalar_whatsapp_bridge.bat."
            _save_bridge_status_local(message=msg, error=msg, last_event="install_incomplete")
            return False, msg
        _save_bridge_status_local(message="Dependências instaladas. Iniciando WhatsApp Bridge...", last_event="dependencies_installed")
        return True, "Dependências do WhatsApp Bridge instaladas."
    except subprocess.TimeoutExpired:
        msg = "Instalação das dependências demorou demais. Rode 8_instalar_whatsapp_bridge.bat manualmente."
        _save_bridge_status_local(message=msg, error=msg, last_event="install_timeout")
        return False, msg
    except Exception as e:
        msg = f"Erro ao instalar dependências do WhatsApp Bridge: {e}"
        _save_bridge_status_local(message=msg, error=msg, last_event="install_error")
        return False, msg

def _clean_local_whatsapp_files(kill_bridge: bool = False) -> str:
    """Limpeza local de emergência, usada só se a ponte não responder."""
    if kill_bridge:
        try:
            _kill_old_bridge_port()
        except Exception:
            pass
    removed = []
    try:
        import shutil
        for pth in [BRIDGE_DIR / "auth", BRIDGE_DIR / "whatsapp_web_profile"]:
            if pth.exists():
                shutil.rmtree(pth, ignore_errors=True)
                removed.append(pth.name)
        for pth in [BRIDGE_DIR / "wa_qr.png", BRIDGE_DIR / "status.json", BRIDGE_DIR / "qr.txt"]:
            if pth.exists():
                pth.unlink()
                removed.append(pth.name)
    except Exception as e:
        return f"Falha ao limpar sessão WhatsApp: {e}"
    return "Sessão local do WhatsApp limpa. Agora peça: conectar WhatsApp, para gerar um QR Code novo."


def disconnect_whatsapp_session() -> str:
    """Desconecta de verdade o WhatsApp vinculado.

    Diferente da limpeza local antiga, esta rotina primeiro chama o logout real
    do Baileys para encerrar o aparelho conectado também no celular. Só cai para
    limpeza local se a ponte estiver offline/travada.
    """
    if is_bridge_alive():
        try:
            r = requests.post(f"{BASE_URL}/logout", json={"full": True}, timeout=25)
            data = _json_or_error(r)
            if r.status_code < 400 and data.get("ok") is not False:
                return data.get("result") or "WhatsApp desconectado completamente. A sessão foi encerrada no Jarvis e no aparelho conectado. Para usar de novo, peça conectar WhatsApp e escaneie um novo QR Code."
            # Se o bridge respondeu erro, ainda limpamos localmente para não ficar preso.
            local = _clean_local_whatsapp_files(kill_bridge=True)
            return f"Tentei desconectar o WhatsApp pelo Baileys, mas a ponte retornou erro: {data.get('error') or r.text}. {local}"
        except Exception as e:
            local = _clean_local_whatsapp_files(kill_bridge=True)
            return f"A ponte WhatsApp não respondeu ao logout real ({e}). {local}"
    return _clean_local_whatsapp_files(kill_bridge=True)


def clean_whatsapp_session() -> str:
    """Compatibilidade: comando antigo de limpar/desconectar agora faz logout real."""
    return disconnect_whatsapp_session()


def start_bridge(force_restart: bool = False) -> str:
    global _proc
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)

    if is_bridge_alive() and not force_restart:
        st = get_status()
        versao_viva = str(st.get("version") or "")
        if versao_viva == _expected_bridge_version():
            return "WhatsApp bridge already running."
        # PONTE DE ANTES DA ATUALIZACAO (23/09/2026). O Node da ponte e um
        # processo separado e sobrevive a troca de versao do JARVIS. Antes,
        # com a versao diferente, este codigo tentava subir OUTRO Node na mesma
        # porta — que morria calado — e a ponte velha (com o defeito das duas
        # conexoes, consertado na 38) seguia no ar para sempre. Agora ela e
        # trocada pela nova. A sessao fica em `auth\`, entao nao pede QR.
        # So troca quando SABE a versao: sem resposta (ponte ocupada) nao mexe.
        if versao_viva:
            force_restart = True
        else:
            return "WhatsApp bridge already running."

    if force_restart:
        _kill_old_bridge_port()
        try:
            import shutil
            for p in [BRIDGE_DIR / "wa_qr.png", BRIDGE_DIR / "status.json"]:
                if p.exists():
                    p.unlink()
            # Mantém a sessão se existir; limpar auth só quando o usuário usar o BAT/botão de limpar.
            # Apagar auth em todo clique forçava QR novo sem necessidade e podia atrasar conexão.
        except Exception:
            pass
        time.sleep(0.8)

    if not SERVER_JS.exists():
        msg = "WhatsApp bridge não instalado. Reinstale o módulo WhatsApp."
        _save_bridge_status_local(message=msg, error=msg, last_event="server_missing")
        return msg

    node = _node_executable()
    if not node:
        msg = "Node.js não encontrado. Instale o Node.js LTS e rode 8_instalar_whatsapp_bridge.bat."
        _save_bridge_status_local(message=msg, error=msg, last_event="node_missing")
        return msg

    ok_deps, dep_msg = _ensure_node_dependencies()
    if not ok_deps:
        return dep_msg

    try:
        _save_bridge_status_local(
            message="Servidor local WhatsApp iniciando. Gerando QR Code...",
            last_event="starting_process",
        )
        cmd = [node, "server.js"]
        log_path = BRIDGE_DIR / "bridge.log"
        _proc = subprocess.Popen(
            cmd,
            cwd=str(BRIDGE_DIR),
            stdout=open(log_path, "a", encoding="utf-8", errors="ignore"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=_creationflags(),
        )

        # Aguarda um pouco a porta subir. Se o processo morrer, grava erro claro para a UI.
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if is_bridge_alive():
                return "Ponte WhatsApp iniciada. Aguarde o QR Code na tela."
            if _proc.poll() is not None:
                break
            time.sleep(0.4)

        if is_bridge_alive():
            return "Ponte WhatsApp iniciada. Aguarde o QR Code na tela."

        tail = ""
        try:
            if log_path.exists():
                lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-8:]
                tail = " | ".join(lines)[-500:]
        except Exception:
            tail = ""
        msg = "A ponte WhatsApp não conseguiu abrir a porta local."
        if tail:
            msg += f" Último log: {tail}"
        else:
            msg += " Rode 8_instalar_whatsapp_bridge.bat e tente novamente."
        _save_bridge_status_local(message=msg, error=msg, last_event="process_not_listening")
        return msg
    except FileNotFoundError:
        msg = "Node.js/npm não encontrado. Instale o Node.js LTS e rode 8_instalar_whatsapp_bridge.bat."
        _save_bridge_status_local(message=msg, error=msg, last_event="node_missing")
        return msg
    except Exception as e:
        msg = f"Não consegui iniciar a ponte WhatsApp: {e}"
        _save_bridge_status_local(message=msg, error=msg, last_event="start_exception")
        return msg

def _validate_status_payload(data: dict) -> dict:
    root = str(data.get("bridge_root") or "")
    if root and Path(root).resolve() != BRIDGE_DIR.resolve():
        return {"connected": False, "error": "Outra ponte WhatsApp antiga está usando a porta 8765. Clique em CONECTAR WHATSAPP para reiniciar."}
    # A versao esperada vem do `server.js` desta instalacao — ver a nota do
    # `_expected_bridge_version`. E a pasta ja foi conferida logo acima: se a
    # ponte que respondeu e a NOSSA, ela nao pode ser "uma ponte antiga".
    if str(data.get("version") or "") != _expected_bridge_version():
        if root and Path(root).resolve() == BRIDGE_DIR.resolve():
            # Mesma pasta, versao diferente: ponte desta instalacao rodando de
            # antes de uma atualizacao. Vale o que ela diz; so fica o aviso.
            data = dict(data)
            data["bridge_desatualizada"] = True
            return data
        return {"connected": False, "error": "Ponte WhatsApp antiga detectada. Clique em CONECTAR WHATSAPP para reiniciar a ponte."}
    return data


def _fresh_status_file(max_age_seconds: float = 45.0) -> dict | None:
    """Usa o status gravado pelo Node somente se ele for realmente recente.

    Eram 12 s. O Node reescreve o arquivo a cada 2,5 s QUANDO ESTA LIVRE;
    numa sincronizacao de historico ele passa facil de 12 s sem escrever, e
    o JARVIS declarava desconexao com o WhatsApp conectado (22/09/2026).
    """
    status_file = BRIDGE_DIR / "status.json"
    try:
        if not status_file.exists() or time.time() - status_file.stat().st_mtime > max_age_seconds:
            return None
        data = json.loads(status_file.read_text(encoding="utf-8", errors="ignore"))
        if not isinstance(data, dict):
            return None
        data = _validate_status_payload(data)
        data["status_source"] = "fresh_status_file"
        return data
    except Exception:
        return None


# A PONTE OCUPADA NAO E PONTE CAIDA (22/09/2026).
#
# O Node e de uma linha so: enquanto sincroniza historico, carrega contatos ou
# grava midia, ele nao atende a porta. Com `timeout=1.5` isso virava excecao, e
# o `status.json` tambem podia estar com mais de 12 s (o Node so o reescreve a
# cada 2,5 s quando esta livre). O resultado era "Bridge offline" — ou seja,
# "voce nao esta conectado" — com o WhatsApp conectado do outro lado.
#
# Agora: mais paciencia na porta (4 s), o arquivo vale por mais tempo, e uma
# queda so e declarada depois de DUAS falhas seguidas. Uma falha isolada
# devolve o ultimo estado conhecido, marcado como tal.
_ULTIMO_STATUS = {"data": None, "at": 0.0, "falhas": 0}
_STATUS_LOCK = threading.Lock()


def get_status() -> dict:
    try:
        r = requests.get(f"{BASE_URL}/status", timeout=4.0)
        data = _validate_status_payload(r.json())
        with _STATUS_LOCK:
            _ULTIMO_STATUS.update({"data": data, "at": time.time(), "falhas": 0})
        return data
    except Exception as exc:
        cached = _fresh_status_file()
        if cached is not None:
            with _STATUS_LOCK:
                _ULTIMO_STATUS.update({"data": cached, "at": time.time(), "falhas": 0})
            return cached
        with _STATUS_LOCK:
            _ULTIMO_STATUS["falhas"] += 1
            falhas = _ULTIMO_STATUS["falhas"]
            ultimo = _ULTIMO_STATUS["data"]
            idade = time.time() - (_ULTIMO_STATUS["at"] or 0.0)
        # Uma falha so, com um estado recente na mao: a ponte esta OCUPADA,
        # nao caida. Dizer "desconectado" aqui e o defeito que o Diego relatou.
        if falhas < 2 and isinstance(ultimo, dict) and idade < 120 and ultimo.get("connected"):
            saida = dict(ultimo)
            saida["status_source"] = "ultimo_conhecido"
            saida["ocupada"] = True
            return saida
        return {"connected": False,
                "error": f"Bridge offline ({type(exc).__name__})"}


def _wait_for_whatsapp_connection(seconds: float = 6.0) -> dict:
    deadline = time.monotonic() + max(0.0, seconds)
    latest = get_status()
    while not latest.get("connected") and time.monotonic() < deadline:
        # Logout e QR exigem ação do usuário; apenas quedas recuperáveis aguardam.
        if latest.get("fresh_login_required") or latest.get("has_qr") or latest.get("disconnect_reason") == "user_logged_out":
            break
        time.sleep(0.6)
        latest = get_status()
    return latest


def format_status() -> str:
    data = get_status()
    if not data.get("connected") and (
        data.get("disconnect_reason") == "connection_lost"
        or data.get("last_event") in {"starting", "started", "connecting", "disconnected"}
    ):
        data = _wait_for_whatsapp_connection(5.0)
    if data.get("connected"):
        contacts = data.get("contacts") or 0
        chats = data.get("chats") or 0
        msgs = data.get("messages") or 0
        return f"WhatsApp está conectado. Contatos carregados: {contacts}. Conversas carregadas: {chats}. Mensagens locais: {msgs}. Modo seguro ativo."
    if data.get("has_qr"):
        return "WhatsApp ainda não está conectado. O QR Code está disponível na tela para escanear."
    if data.get("fresh_login_required") or data.get("disconnect_reason") == "user_logged_out":
        return "WhatsApp não está conectado. A sessão antiga foi limpa; peça para conectar o WhatsApp e escaneie o novo QR Code."
    if data.get("error"):
        return f"WhatsApp não está conectado. Motivo: {data.get('error')}"
    msg = data.get("message") or "ponte WhatsApp iniciada, aguardando conexão."
    return f"WhatsApp não está conectado no momento: {msg}"


def _json_or_error(resp):
    try:
        return resp.json()
    except Exception:
        return {"ok": False, "error": resp.text}


def _get(path: str, params: dict | None = None) -> dict:
    start_bridge()
    r = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10)
    data = _json_or_error(r)
    if r.status_code >= 400 or data.get("ok") is False:
        raise RuntimeError(data.get("error") or r.text)
    return data


def _post(path: str, payload: dict | None = None, timeout: float = 55.0) -> dict:
    start_bridge()
    r = requests.post(f"{BASE_URL}{path}", json=payload or {}, timeout=timeout)
    data = _json_or_error(r)
    if r.status_code >= 400 or data.get("ok") is False:
        raise RuntimeError(data.get("error") or r.text)
    return data


def _post_send(payload: dict) -> str:
    kind = str(payload.get("type") or "text").lower()
    timeout = 135.0 if kind in {"file", "media", "image", "video", "document"} else (50.0 if kind == "audio" else 55.0)
    try:
        data = _post("/send", payload, timeout=timeout)
    except RuntimeError as exc:
        # Tenta novamente apenas quando o servidor recusou por estar nos poucos
        # segundos de reconexão. Timeout não é repetido, evitando duplicatas.
        if "not connected" not in str(exc).lower() or not _wait_for_whatsapp_connection(15.0).get("connected"):
            raise
        data = _post("/send", payload, timeout=timeout)
    return data.get("result", "WhatsApp message sent.")


def send_whatsapp_text(to: str, message: str) -> str:
    return _post_send({"to": to, "message": message, "type": "text"})


def send_whatsapp_text_batch(to: str, messages: list[str]) -> str:
    """Envia várias mensagens em uma operação lógica e retorna uma confirmação."""
    clean_messages = [re.sub(r"\s+", " ", str(item or "")).strip() for item in (messages or [])]
    clean_messages = [item for item in clean_messages if item][:50]
    if not to or not clean_messages:
        return "Para enviar várias mensagens, preciso do contato e dos textos separados."

    signature = json.dumps([_norm_name(to), clean_messages], ensure_ascii=False, separators=(",", ":"))
    now = time.monotonic()
    with _SEND_BATCH_GUARD_LOCK:
        if (
            signature == _SEND_BATCH_GUARD.get("signature")
            and now - float(_SEND_BATCH_GUARD.get("at") or 0.0) < 8.0
        ):
            return str(_SEND_BATCH_GUARD.get("result") or "O lote de mensagens já foi enviado.")

        sent = 0
        failures: list[str] = []
        for index, text in enumerate(clean_messages, start=1):
            try:
                _post_send({"to": to, "message": text, "type": "text"})
                sent += 1
            except Exception as exc:
                failures.append(f"{index}: {str(exc)[:120]}")

        if sent == len(clean_messages):
            result = f"{sent} mensagens enviadas para {to}."
        elif sent:
            result = f"Enviei {sent} de {len(clean_messages)} mensagens para {to}. {len(failures)} falharam."
        else:
            raise RuntimeError(f"Não consegui enviar o lote para {to}: {'; '.join(failures[:3])}")
        _SEND_BATCH_GUARD.update({"signature": signature, "at": time.monotonic(), "result": result})
        return result


def _make_voice_file(text: str) -> str:
    out = Path(tempfile.gettempdir()) / "mark_xlviii_whatsapp_voice.wav"
    try:
        import pyttsx3
        engine = pyttsx3.init()
        try:
            engine.setProperty("rate", 170)
            engine.setProperty("volume", 1.0)
        except Exception:
            pass
        engine.save_to_file(text, str(out))
        engine.runAndWait()
        for _ in range(30):
            if out.exists() and out.stat().st_size > 1000:
                return str(out)
            time.sleep(0.2)
        raise RuntimeError("voice file was not created")
    except Exception as e:
        raise RuntimeError(f"Could not create voice audio: {e}")


def send_whatsapp_audio(to: str, message: str) -> str:
    audio_path = _make_voice_file(message)
    return _post_send({"to": to, "message": message, "type": "audio", "audio_path": audio_path})



def _norm_name(value: str) -> str:
    return unicodedata.normalize("NFD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower().strip()


def _case_existing(path: Path) -> Path | None:
    try:
        path = path.expanduser()
        if path.exists():
            return path
        parts = path.parts
        if not parts:
            return None
        cur = Path(parts[0])
        for part in parts[1:]:
            if not cur.exists() or not cur.is_dir():
                return None
            found = None
            low = part.lower()
            try:
                for child in cur.iterdir():
                    if child.name.lower() == low:
                        found = child
                        break
            except Exception:
                return None
            if found is None:
                return None
            cur = found
        return cur if cur.exists() else None
    except Exception:
        return None


def _common_user_dirs() -> list[Path]:
    roots: list[Path] = []
    home = Path.home()
    userprofile = Path(os.environ.get("USERPROFILE") or str(home))
    onedrive_env = [os.environ.get("OneDrive"), os.environ.get("OneDriveConsumer"), os.environ.get("OneDriveCommercial")]
    bases = [userprofile, home, *(Path(x) for x in onedrive_env if x), userprofile / "OneDrive", home / "OneDrive"]
    folders = [
        "Downloads", "Download", "Desktop", "Área de Trabalho", "Area de Trabalho",
        "Documents", "Documentos", "Pictures", "Imagens", "Videos", "Vídeos", "Music", "Músicas"
    ]
    for b in bases:
        roots.append(b)
        for f in folders:
            roots.append(b / f)
    # keep order and remove duplicates
    out: list[Path] = []
    seen = set()
    for r in roots:
        try:
            k = str(r.resolve()).lower() if r.exists() else str(r).lower()
        except Exception:
            k = str(r).lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _sanitize_zip_file_name(name: str, default: str = "JARVIS_Arquivo.zip") -> str:
    raw = str(name or default).strip().strip('"').strip("'")
    raw = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', raw)
    raw = re.sub(r'\s+', ' ', raw).strip().strip('.')
    if not raw:
        raw = default
    if not raw.lower().endswith('.zip'):
        raw += '.zip'
    return raw


def _pc_control_state_for_media() -> dict:
    try:
        p = BASE_DIR / 'memory' / 'pc_control_state.json'
        if p.exists():
            data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _resolve_any_local_item_path(file_path: str) -> Path:
    """Resolve arquivo OU pasta para envio WhatsApp.

    O WhatsApp não envia diretório cru; diretórios serão compactados depois.
    Aceita caminho completo, atalhos como desktop/Cyber, nomes recentes do pc_control
    e busca global leve quando necessário.
    """
    raw = str(file_path or "").strip().strip('"').strip("'")
    state = _pc_control_state_for_media()
    ref_words = {"", "essa pasta", "esta pasta", "essa", "esse", "isso", "ultimo", "último", "ultima", "última", "arquivo atual", "pasta atual", "ultimo arquivo", "último arquivo", "ultima pasta", "última pasta"}
    if _norm_name(raw) in {_norm_name(x) for x in ref_words}:
        for key in ("last_compressed_file", "last_created_file", "last_created_folder", "last_opened", "last_found"):
            val = str(state.get(key) or "").strip()
            if val:
                p = Path(val).expanduser()
                try:
                    if p.exists():
                        return p.resolve()
                except Exception:
                    continue
        raise RuntimeError("Nenhum arquivo ou pasta recente encontrado para enviar. Informe o nome/caminho do item.")

    raw_exp = os.path.expandvars(raw)
    p = Path(raw_exp).expanduser()
    candidates: list[Path] = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.extend([BASE_DIR / p, Path.cwd() / p, Path.home() / p, Path(os.environ.get("USERPROFILE") or str(Path.home())) / p])
        parts = list(p.parts)
        if parts:
            first = _norm_name(parts[0])
            folder_map = {
                "download": "Downloads", "downloads": "Downloads", "baixados": "Downloads",
                "desktop": "Desktop", "area de trabalho": "Desktop", "areadetrabalho": "Desktop", "área de trabalho": "Desktop",
                "documentos": "Documents", "documents": "Documents",
                "imagem": "Pictures", "imagens": "Pictures", "picture": "Pictures", "pictures": "Pictures",
                "video": "Videos", "videos": "Videos", "musica": "Music", "musicas": "Music", "music": "Music",
            }
            mapped = folder_map.get(first)
            if mapped:
                rest = Path(*parts[1:]) if len(parts) > 1 else Path("")
                for root in _common_user_dirs():
                    if _norm_name(root.name) == _norm_name(mapped):
                        candidates.append(root / rest)
                    else:
                        candidates.append(root / mapped / rest)
        name = p.name
        if name:
            for root in _common_user_dirs():
                candidates.append(root / name)

    for c in candidates:
        found = _case_existing(c)
        if found and (found.is_file() or found.is_dir()):
            return found.resolve()

    # Usa a busca global robusta do pc_control quando o usuário informar só o nome da pasta/arquivo.
    try:
        from actions.pc_control import resolve_target, search_items
        direct = resolve_target(raw, scope='all', kind='any')
        if direct and Path(direct).exists():
            return Path(direct).resolve()
        found = search_items(raw, scope='all', kind='any', max_results=3, max_seconds=20.0)
        if len(found) == 1 and Path(found[0]).exists():
            return Path(found[0]).resolve()
        if len(found) > 1:
            names = '; '.join(f'{i+1}. {Path(x).name} ({x})' for i, x in enumerate(found[:5]))
            raise RuntimeError(f"Encontrei mais de um item com esse nome. Diga qual resultado deseja enviar: {names}")
    except RuntimeError:
        raise
    except Exception:
        pass

    raise RuntimeError(f"Arquivo ou pasta não encontrado no PC: {file_path}. Informe o caminho completo ou peça para eu procurar primeiro.")


def resolve_local_file_path(file_path: str) -> str:
    path = _resolve_any_local_item_path(file_path)
    if not path.is_file():
        raise RuntimeError(f"O caminho encontrado é uma pasta, não um arquivo: {path}. Para enviar pasta pelo WhatsApp, use envio de arquivo normalmente; ela será compactada em ZIP automaticamente.")
    return str(path)


def _zip_local_item_for_whatsapp(src: Path) -> Path:
    src = Path(src).resolve()
    WHATSAPP_OUTGOING_ZIP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_name = _sanitize_zip_file_name(f"{src.name}_{stamp}.zip")
    out_zip = WHATSAPP_OUTGOING_ZIP_DIR / zip_name
    with zipfile.ZipFile(out_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        if src.is_file():
            zf.write(src, arcname=src.name)
        else:
            base = src.parent
            for child in src.rglob('*'):
                try:
                    if child.is_symlink():
                        continue
                    if child.is_file():
                        zf.write(child, arcname=str(child.relative_to(base)))
                    elif child.is_dir() and not any(child.iterdir()):
                        zf.writestr(str(child.relative_to(base)).rstrip('/\\') + '/', '')
                except (PermissionError, OSError):
                    continue
                except Exception:
                    continue
    return out_zip.resolve()


def _prepare_whatsapp_media_path(file_path: str) -> tuple[Path, str]:
    src = _resolve_any_local_item_path(file_path)
    original_label = src.name
    prepared = _zip_local_item_for_whatsapp(src) if src.is_dir() else src
    try:
        size = prepared.stat().st_size
    except Exception:
        size = 0
    if WHATSAPP_MAX_FILE_BYTES and size > WHATSAPP_MAX_FILE_BYTES:
        mb = size / (1024 * 1024)
        raise RuntimeError(f"Arquivo muito grande para enviar pelo WhatsApp nesta configuração: {prepared.name} ({mb:.2f} MB). Limite atual: {WHATSAPP_MAX_FILE_MB} MB.")
    prefix = f"Compactei a pasta '{original_label}' em ZIP e " if src.is_dir() else ""
    return prepared, prefix


def send_whatsapp_file(to: str, file_path: str, caption: str = "") -> str:
    prepared, prefix = _prepare_whatsapp_media_path(file_path)
    result = _post_send({"to": to, "message": caption or "", "type": "file", "file_path": str(prepared), "caption": caption or ""})
    return prefix + result


def save_contact_alias(name: str, phone: str) -> str:
    data = _post("/alias", {"name": name, "phone": phone})
    return data.get("result", "Contato salvo.")


def _fmt_ts(ts):
    try:
        return time.strftime("%d/%m/%Y %H:%M", time.localtime(int(ts)))
    except Exception:
        return "sem horário"





def _read_gemini_key() -> str:
    cfg = BASE_DIR / "config" / "api_keys.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return str(data.get("gemini_api_key") or "").strip()
    except Exception:
        return ""


_GEMINI_MEDIA_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite",
)


def _generate_media_with_fallback(client, contents):
    """Evita perder áudio/imagem quando a cota de um único modelo esgota."""
    errors: list[str] = []
    for model in _GEMINI_MEDIA_MODELS:
        try:
            return client.models.generate_content(model=model, contents=contents)
        except Exception as exc:
            text = str(exc or "").strip().replace("\n", " ")
            errors.append(f"{model}: {text[:220]}")
    raise RuntimeError("Todos os modelos de mídia falharam. " + " | ".join(errors))


def _transcribe_audio_file(media_path: str) -> str:
    """Transcreve áudio recebido sem travar o Jarvis indefinidamente.

    A chamada de IA fica protegida por timeout; se a Gemini ou a rede travar,
    o JARVIS volta a responder e pede para tentar novamente.
    """
    path = Path(media_path or "").expanduser()
    if not path.exists() or not path.is_file():
        raise RuntimeError("Arquivo de áudio da mensagem não foi encontrado no PC.")
    api_key = _read_gemini_key()
    if not api_key:
        raise RuntimeError("Gemini API Key não encontrada para transcrever o áudio.")

    def _do_transcribe() -> str:
        import mimetypes
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        mime_type = mimetypes.guess_type(str(path))[0] or "audio/ogg"
        data = path.read_bytes()
        if len(data) < 20:
            return "O arquivo de áudio está vazio ou inválido."
        resp = _generate_media_with_fallback(
            client,
            [
                types.Part.from_bytes(data=data, mime_type=mime_type),
                "Transcreva exatamente o áudio em português do Brasil. Responda apenas com a transcrição. Se não entender, diga que o áudio está inaudível."
            ],
        )
        txt = str(getattr(resp, "text", "") or "").strip()
        return txt or "Não consegui transcrever o áudio."

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_do_transcribe)
    try:
        return future.result(timeout=35)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return "A transcrição demorou demais e foi interrompida para não travar o Jarvis. Tente novamente em alguns segundos."
    except Exception as e:
        raise RuntimeError(f"Falha ao transcrever áudio com Gemini: {e}")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _analyze_image_file(media_path: str) -> str:
    """Analisa uma imagem recebida pelo WhatsApp para o modo agente.

    Descreve objetos, contexto e texto visível de forma curta, suficiente
    para o agente responder ao cliente com base no conteúdo enviado.
    """
    path = Path(media_path or "").expanduser()
    if not path.exists() or not path.is_file():
        raise RuntimeError("Arquivo de imagem da mensagem não foi encontrado no PC.")
    api_key = _read_gemini_key()
    if not api_key:
        raise RuntimeError("Gemini API Key não encontrada para analisar a imagem.")

    def _do_analyze() -> str:
        import mimetypes
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        mime_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        data = path.read_bytes()
        if len(data) < 20:
            return "A imagem recebida está vazia ou inválida."
        resp = _generate_media_with_fallback(
            client,
            [
                types.Part.from_bytes(data=data, mime_type=mime_type),
                (
                    "Analise esta imagem recebida no WhatsApp. "
                    "Descreva objetivamente o que aparece, o contexto e qualquer texto legível. "
                    "Responda em português do Brasil, em até 8 linhas, sem inventar detalhes."
                ),
            ],
        )
        txt = str(getattr(resp, "text", "") or "").strip()
        return txt or "Não consegui analisar a imagem."

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_do_analyze)
    try:
        return future.result(timeout=35)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return "A análise da imagem demorou demais e foi interrompida para não travar o Jarvis."
    except Exception as e:
        raise RuntimeError(f"Falha ao analisar imagem com Gemini: {e}")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def transcribe_audio_path(media_path: str) -> str:
    """Transcreve um arquivo de áudio específico recebido pelo WhatsApp."""
    return _transcribe_audio_file(media_path)

def transcribe_last_audio(chat: str = "") -> str:
    data = _get("/messages/audio/last", {"chat": chat or ""})
    msg = data.get("message")
    if not msg:
        return "Não encontrei áudio recebido no histórico local do WhatsApp. Envie ou receba um áudio novo e tente novamente."
    media_path = msg.get("media_path") or ""
    context = _format_message_context(msg)
    if not media_path:
        return f"Encontrei um áudio {context}, em {_fmt_ts(msg.get('timestamp'))}, mas o arquivo ainda não foi baixado pela ponte. Aguarde alguns segundos e tente novamente. Status do download: {msg.get('media_download_status') or 'pendente'}."
    transcript = _transcribe_audio_file(media_path)
    msg_text = f"[transcrição do áudio] {transcript}"
    return f"Áudio recebido {context}, em {_fmt_ts(msg.get('timestamp'))}. Transcrição: {transcript}"

def _format_message_context(msg: dict) -> str:
    sender = msg.get("name") or msg.get("sender_name") or msg.get("sender_jid") or "contato"
    chat = msg.get("chat_name") or msg.get("jid") or "conversa"
    is_group = bool(msg.get("is_group")) or str(msg.get("jid") or "").endswith("@g.us")
    if is_group:
        return f'no grupo "{chat}", enviada por {sender}'
    return f'no privado de {sender}'

def latest_message(direction: str = "incoming", chat: str = "") -> str:
    data = _get("/messages/last", {"direction": direction or "incoming", "chat": chat or ""})
    msg = data.get("message")
    if not msg:
        return "Não encontrei mensagem real no histórico local do WhatsApp. A ponte lê mensagens novas após a conexão e o histórico que o WhatsApp liberar."
    context = _format_message_context(msg)
    text = msg.get("text") or f"[{msg.get('type', 'mensagem sem texto')}]"
    if str(msg.get("type") or "").lower().find("audio") >= 0 or str(msg.get("media_kind") or "").lower() == "audio" or "áudio" in text.lower() or "audio" in text.lower():
        if msg.get("media_path"):
            try:
                text = "[áudio transcrito] " + _transcribe_audio_file(str(msg.get("media_path")))
            except Exception as e:
                text = f"[áudio/voz recebido, mas ainda não consegui transcrever: {e}]"
        else:
            text = "[áudio/voz recebido; arquivo ainda baixando. Tente perguntar novamente em alguns segundos.]"
    return f"Última mensagem recebida {context}, em {_fmt_ts(msg.get('timestamp'))}: {text}"


def recent_messages(limit: int = 5, direction: str = "all", chat: str = "") -> str:
    data = _get("/messages/latest", {"limit": limit or 5, "direction": direction or "all", "chat": chat or ""})
    items = data.get("messages") or []
    if not items:
        return "Não encontrei mensagens reais recentes no histórico local do WhatsApp."
    lines = []
    for m in items:
        text = m.get("text") or f"[{m.get('type', 'mensagem sem texto')}]"
        if m.get("from_me"):
            lines.append(f"- Você ({_fmt_ts(m.get('timestamp'))}): {text}")
        else:
            context = _format_message_context(m)
            lines.append(f"- {context} ({_fmt_ts(m.get('timestamp'))}): {text}")
    return "Mensagens recentes do WhatsApp:\n" + "\n".join(lines)

def find_contact(query: str) -> str:
    data = _get("/contacts", {"q": query or "", "limit": 10})
    items = data.get("contacts") or []
    if not items:
        return (
            f"Não encontrei contato com nome '{query}'. "
            f"Contatos carregados: {data.get('total_contacts', 0)}. "
            f"Conversas carregadas: {data.get('total_chats', 0)}. "
            f"Grupos carregados: {data.get('total_groups', 0)}. "
            "Tente mandar ou receber uma mensagem desse contato, ou use o número com DDD."
        )
    lines = [f"- {c.get('name')} ({c.get('jid')})" for c in items]
    return "Contatos encontrados:\n" + "\n".join(lines)


def list_groups() -> str:
    data = _get("/groups", {"refresh": "1"})
    items = data.get("groups") or []
    if not items:
        return "Não encontrei grupos sincronizados no WhatsApp ainda. Aguarde a sincronização ou receba uma mensagem em algum grupo."
    lines = [f"- {g.get('name')} ({g.get('jid')})" for g in items]
    return "Grupos do WhatsApp encontrados:\n" + "\n".join(lines)



def leave_group(to: str = "", confirm: bool = False) -> str:
    data = _post("/groups/leave", {"to": to or "", "confirm": bool(confirm)})
    return data.get("result", "Solicitação para sair do grupo enviada.")

def refresh_aliases_from_messages() -> str:
    data = _post("/auto-alias-from-messages", {})
    return data.get("result", "Aliases atualizados a partir das mensagens.")


def _coerce_delete_count(value=None, raw_text: str = "") -> int:
    """Converte quantidade numérica ou falada em PT-BR, limitada a 50."""
    try:
        if value is not None and str(value).strip():
            return max(1, min(50, int(float(str(value).strip().replace(",", ".")))))
    except Exception:
        pass

    normalized = _norm_mode_text(raw_text or "")
    digit = re.search(r"\b(\d{1,3})\b", normalized)
    if digit:
        return max(1, min(50, int(digit.group(1))))

    units = {
        "uma": 1, "um": 1, "duas": 2, "dois": 2, "tres": 3, "quatro": 4,
        "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9,
    }
    direct = {
        "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14,
        "catorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17,
        "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30,
        "quarenta": 40, "cinquenta": 50,
    }
    words = normalized.split()
    for index, word in enumerate(words):
        if word in direct:
            count = direct[word]
            if count >= 20 and index + 2 < len(words) and words[index + 1] == "e":
                count += units.get(words[index + 2], 0)
            return max(1, min(50, count))
        if word in units:
            return units[word]
    return 1


def delete_sent_messages(to: str = "", count: int = 1, raw_text: str = "") -> str:
    count = _coerce_delete_count(count, raw_text)
    signature = f"{_norm_mode_text(to)}|{count}"
    now = time.monotonic()
    with _DELETE_GUARD_LOCK:
        # Impede que uma chamada duplicada do modelo apague o lote seguinte.
        if (
            signature == _DELETE_GUARD.get("signature")
            and now - float(_DELETE_GUARD.get("at") or 0.0) < 6.0
        ):
            return str(_DELETE_GUARD.get("result") or "Solicitação de exclusão já processada.")

        data = _post("/delete-last-sent", {"to": to or "", "count": count}, timeout=65.0)
        result = data.get("result", "Solicitação para apagar mensagens enviada.")
        _DELETE_GUARD.update({"signature": signature, "at": time.monotonic(), "result": result})
        return result


def delete_last_sent(to: str = "") -> str:
    """Compatibilidade com comandos e integrações antigos."""
    return delete_sent_messages(to=to, count=1)


def _clean_whatsapp_media_reference(raw_text: str) -> str:
    """Extrai nome/caminho de arquivo, imagem ou pasta de uma fala natural.

    Exemplos:
    - "O nome do arquivo é Cyber, está na minha área de trabalho" -> "Cyber"
    - "O nome da imagem é bola.jpg" -> "bola.jpg"
    - "a pasta Cyber que está na área de trabalho" -> "Cyber"
    """
    raw = _stt_join_schedule_words(str(raw_text or "")).strip().strip('"\'')
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw)
    raw = re.sub(r"\s*\.\s*", ".", raw)  # "bola . jpg" -> "bola.jpg"
    raw = re.sub(r"\barquivo\s+o\s+nome\b", "nome do arquivo", raw, flags=re.I)
    raw = re.sub(r"\bpasta\s+o\s+nome\b", "nome da pasta", raw, flags=re.I)
    raw = re.sub(r"\bimagem\s+o\s+nome\b", "nome da imagem", raw, flags=re.I)

    # Caminho Windows completo, se vier na fala/texto.
    m_path = re.search(r"([A-Za-z]:\\[^\n\r\"']+)", raw)
    if m_path:
        return m_path.group(1).strip(" .,;!?")

    # Nome com extensão explícita.
    m_ext = re.search(r"([\w\-. À-ÿ]+?\.(?:jpg|jpeg|png|gif|webp|bmp|mp4|mov|mkv|avi|webm|pdf|docx?|xlsx?|pptx?|txt|zip|rar|7z|mp3|wav|ogg|opus|m4a))\b", raw, flags=re.I)
    if m_ext:
        cand = m_ext.group(1).strip(" .,;!?\"'")
        cand = re.split(r"(?:nome\s+(?:do|da)?\s*(?:arquivo|imagem|foto|video|vídeo|documento|pasta)\s*(?:é|e|eh|se chama|chama|como)?|arquivo\s*(?:é|e|eh)?|imagem\s*(?:é|e|eh)?|foto\s*(?:é|e|eh)?)\s+", cand, flags=re.I)[-1]
        return cand.strip(" .,;!?\"'")

    # Captura nomes sem extensão em frases comuns de voz.
    patterns = [
        r"nome\s+(?:do|da)?\s*(?:arquivo|imagem|foto|video|vídeo|documento|pasta)\s*(?:é|e|eh|se chama|chama|como|vai ser)?\s+(.+?)(?:\s*,?\s*(?:que\s+)?(?:est[aá]|esta|fica)|\s+(?:na\s+minha|no\s+meu|na\s+area|na\s+área|area\s+de\s+trabalho|área\s+de\s+trabalho|para\s+o|pro\s+contato|no\s+whatsapp|whatsapp)|$)",
        r"(?:arquivo|imagem|foto|video|vídeo|documento|pasta)\s+(?:com\s+o\s+nome|chamada|chamado|nome)?\s*(?:é|e|eh|se chama|chama|como)?\s+(.+?)(?:\s*,?\s*(?:que\s+)?(?:est[aá]|esta|fica)|\s+(?:na\s+minha|no\s+meu|na\s+area|na\s+área|area\s+de\s+trabalho|área\s+de\s+trabalho|para\s+o|pro\s+contato|no\s+whatsapp|whatsapp)|$)",
    ]
    for pat in patterns:
        m = re.search(pat, raw, flags=re.I)
        if m:
            cand = m.group(1).strip(" .,;!?\"'")
            cand = re.sub(r"^(?:o|a|um|uma)\s+", "", cand, flags=re.I).strip()
            cand = re.sub(r"\s+(?:que|de|da|do|pode|enviar|envia|mande|manda)$", "", cand, flags=re.I).strip(" .,;!?")
            words = cand.split()
            if 1 <= len(words) <= 6:
                return cand

    # Frase curta isolada depois que o Jarvis perguntou "qual arquivo?".
    clean = raw
    clean = re.sub(r"\b(o|a|um|uma)\b", " ", clean, flags=re.I)
    clean = re.sub(r"\b(arquivo|imagem|foto|video|vídeo|documento|pasta|nome|é|e|eh|esta|está|que|de|da|do|na|minha|meu|área|area|trabalho|desktop|downloads?|whatsapp|contato|mark_xlviii|enviar|envia|mande|manda|pode|para|pro)\b", " ", clean, flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip(" .,;!?\"'")
    if clean and 1 <= len(clean.split()) <= 5:
        return clean
    return ""


def _media_path_from_whatsapp_params(params: dict, message: str = "") -> str:
    """Resolve file_path quando a IA manda o nome do arquivo dentro de message/text/raw_text."""
    direct_keys = (
        "file_path", "path", "file", "filename", "file_name", "arquivo", "imagem", "image",
        "foto", "video", "vídeo", "document", "documento", "folder", "pasta"
    )
    for key in direct_keys:
        val = str(params.get(key) or "").strip()
        if val:
            return val
    combined = " ".join(str(params.get(k) or "") for k in ("raw_text", "message", "message_text", "text", "body", "content", "caption", "query", "target_file", "name"))
    if message and message not in combined:
        combined += " " + str(message)
    return _clean_whatsapp_media_reference(combined)



def _get_no_start(path: str, params: dict | None = None) -> dict:
    try:
        # Os endpoints de fila podem coincidir com o processamento de mídia no
        # bridge. Uma margem pequena aqui causava ciclos de polling perdidos e
        # atrasava alertas/respostas automáticas mesmo com o WhatsApp conectado.
        r = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=1.5)
        data = _json_or_error(r)
        if r.status_code >= 400 or data.get("ok") is False:
            return {"ok": False, "error": data.get("error") or r.text}
        return data
    except Exception as e:
        return {"ok": False, "error": str(e)}


# A PONTE LIGA SOZINHA QUANDO HA SESSAO SALVA (23/09/2026).
#
# Ela so subia quando alguem mandava ou lia mensagem. Logo depois de abrir o
# JARVIS, "estou conectado?" consultava uma ponte que nem tinha sido ligada e
# a resposta era "WhatsApp nao esta conectado: Bridge offline" — com o
# WhatsApp pareado e a sessao salva em `auth\`. Agora o vigia de alertas (que
# roda desde a abertura) liga a ponte se existir sessao, no maximo uma
# tentativa por minuto. Sem sessao salva ele nao liga nada: quem nunca
# conectou nao ganha QR Code pulando na tela.
_AUTO_INICIO = {"ultima": 0.0}


def _sessao_salva() -> bool:
    try:
        return (BRIDGE_DIR / "auth" / "creds.json").is_file()
    except Exception:
        return False


def garantir_ponte_se_tiver_sessao() -> None:
    if is_bridge_alive() or not _sessao_salva():
        return
    agora = time.time()
    if agora - _AUTO_INICIO["ultima"] < 60:
        return
    _AUTO_INICIO["ultima"] = agora
    try:
        start_bridge()
    except Exception:
        pass


def poll_whatsapp_alerts(limit: int = 10) -> list[dict]:
    garantir_ponte_se_tiver_sessao()
    data = _get_no_start("/alerts/poll", {"limit": limit})
    if data.get("ok") is False:
        return []
    return data.get("alerts") or []


def _norm_mode_text(value: str) -> str:
    return unicodedata.normalize("NFD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower().strip()


def watch_whatsapp_messages(mode: str = "any", contact: str = "") -> str:
    raw_mode = _norm_mode_text(mode or "")
    contact = str(contact or "").strip()
    if raw_mode in {"todos", "tudo", "qualquer", "qualquer_pessoa", "any", "all", "geral", "todos_e_grupos"}:
        mode = "any"
    elif raw_mode in {"privado", "privados", "private", "somente_privados", "contatos", "contatos_privados", "so_privado"}:
        mode = "private"
    elif raw_mode in {"grupo", "grupos", "groups", "somente_grupos", "so_grupos", "group_only"} and not contact:
        mode = "groups"
    elif raw_mode in {"grupo_especifico", "specific_group", "group"}:
        mode = "group"
    elif raw_mode in {"contato", "contact", "specific_contact", "contato_especifico"}:
        mode = "contact"
    elif not raw_mode:
        mode = "contact" if contact else "any"
    else:
        mode = raw_mode

    if mode in {"contact", "group"} and not contact:
        # Avoid false failure when the model picks contact accidentally for a broad request.
        return "Para alerta específico, informe o nome do contato ou grupo. Para qualquer mensagem, diga: me avise sobre todas as mensagens do WhatsApp."

    payload = {"mode": mode or "any"}
    if contact:
        payload["contact"] = contact
    data = _post("/alerts/watch", payload)
    return data.get("result", "Alerta do WhatsApp atualizado.")


def watch_whatsapp_status() -> str:
    data = _get("/alerts/status")
    w = data.get("watch") or {}
    if not w.get("enabled"):
        return "Alertas de mensagens do WhatsApp estão desativados."
    mode = str(w.get("mode") or "").lower()
    if mode in {"any", "all"}:
        return "Alertas ativos: vou avisar mensagens privadas e mensagens de qualquer grupo do WhatsApp."
    if mode == "private":
        return "Alertas ativos: vou avisar somente mensagens privadas de contatos."
    if mode == "groups":
        return "Alertas ativos: vou avisar somente mensagens de grupos."
    names = ", ".join([(c.get("name") or c.get("query") or str(c)) for c in (w.get("contacts") or [])])
    if mode == "group":
        return f"Alertas ativos somente para o grupo: {names}."
    if mode == "contact":
        return f"Alertas ativos somente para o contato: {names}."
    return f"Alertas ativos. Modo: {mode}. Alvos: {names or 'todos'}."





def _open_whatsapp_qr_page() -> bool:
    """Open the local WhatsApp QR/status page without depending on the Mark UI."""
    url = f"{BASE_URL}/"
    try:
        if os.name == "nt":
            # Prefer Chrome; if Chrome is already open, Windows opens a new tab.
            subprocess.Popen(["cmd", "/c", "start", "", "chrome", url], cwd=str(BASE_DIR), creationflags=_creationflags())
            return True
    except Exception:
        pass
    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception:
        return False

def _try_open_whatsapp_overlay(player) -> bool:
    """Ask the PyQt UI to open the WhatsApp QR overlay from a worker thread.

    In the app, whatsapp_action receives JarvisUI, not MainWindow. Older builds
    looked for _open_whatsapp_sig directly on the player and therefore voice
    commands started the bridge but did not show the QR window. This resolver
    supports both JarvisUI and MainWindow safely.
    """
    if player is None:
        return False

    # New JarvisUI wrapper method.
    try:
        meth = getattr(player, "open_whatsapp_qr", None)
        if callable(meth):
            meth()
            return True
    except Exception:
        pass

    # Direct MainWindow signal.
    try:
        sig = getattr(player, "_open_whatsapp_sig", None)
        if sig is not None:
            sig.emit()
            return True
    except Exception:
        pass

    # JarvisUI._win signal.
    try:
        win = getattr(player, "_win", None)
        sig = getattr(win, "_open_whatsapp_sig", None)
        if sig is not None:
            sig.emit()
            return True
    except Exception:
        pass

    # Fallback for older UI builds. Best effort only.
    try:
        meth = getattr(player, "_open_whatsapp", None)
        if callable(meth):
            meth()
            return True
    except Exception:
        pass
    try:
        win = getattr(player, "_win", None)
        meth = getattr(win, "_open_whatsapp", None)
        if callable(meth):
            meth()
            return True
    except Exception:
        pass
    return False


def connect_whatsapp_from_voice(player=None) -> str:
    st = get_status()
    if st.get("connected"):
        _try_open_whatsapp_overlay(player)
        return "WhatsApp já está conectado."

    # Abre a janela primeiro para mostrar progresso/erros enquanto a ponte sobe.
    opened = _try_open_whatsapp_overlay(player)
    result = start_bridge(force_restart=True)

    low = _norm_mode_text(result)
    has_error = any(x in low for x in [
        "nao encontrado", "não encontrado", "falha", "erro", "nao consegui", "não consegui",
        "rode 8_instalar", "instale o node", "package", "npm"
    ])
    if not opened:
        opened = _open_whatsapp_qr_page()
    if has_error:
        return result
    if opened:
        return "Abrindo conexão do WhatsApp. Aguarde o QR Code aparecer na janela."
    return result + " Abra http://127.0.0.1:8765 para ver o QR Code."

# WhatsApp scheduled messages
# ─────────────────────────────────────────────────────────────

def _load_json_file(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8") or "null") or default
    except Exception:
        pass
    return default


def _save_json_file(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")



def _norm_schedule_raw(text: str) -> str:
    raw = unicodedata.normalize("NFD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return raw.lower()


def _compact_schedule_raw(text: str) -> str:
    return re.sub(r"[^a-z0-9:]+", "", _norm_schedule_raw(text))


def _is_internal_schedule_text(value: str) -> bool:
    """Bloqueia textos internos do sistema para nunca virarem conteúdo de agendamento."""
    raw = str(value or "").strip()
    low = _norm_schedule_raw(raw)
    compact = _compact_schedule_raw(raw)
    if not raw:
        return False
    internal_markers = (
        "[SYSTEM_EVENT_", "[LOCAL_ACTION_", "[WHATSAPP_", "[FILE_UPLOADED]",
        "SYSTEM_EVENT_WHATSAPP_STATUS", "LOCAL_ACTION_WHATSAPP", "WHATSAPP_SCHEDULE_NOTICE",
    )
    if any(raw.upper().startswith(x) or x.lower() in low for x in internal_markers):
        return True
    if "fraseobrigatoria" in compact or "respondaemportuguesdobrasil" in compact:
        return True
    if "systemeventwhatsappstatus" in compact or "localactionwhatsapp" in compact:
        return True
    return False


def _stt_join_schedule_words(text: str) -> str:
    """Corrige palavras picotadas pelo STT para o parser de agendamento."""
    t = str(text or "")
    repl = [
        (r"\bcon\s*te[uú]\s*do\b", "conteúdo"),
        (r"\bmen\s*sa\s*gem\b", "mensagem"),
        (r"\bhor[aá]\s*rio\b", "horário"),
        (r"\bcont?\s*ato\b", "contato"),
        (r"\bgen\s*ti\s*leza\b", "gentileza"),
        (r"\bcy\s*ber\s*bo\s*t\s*s?\b", "JARVIS"),
        (r"\bcy\s*ber\s*bot\b", "JARVIS"),
        (r"\bage\s*nde\b", "agende"),
        (r"\bage\s*ndo\b", "agendo"),
        (r"\bho\s*je\b", "hoje"),
        (r"\bbo\s*a\s*noite\b", "boa noite"),
        (r"\bva\s*mos\b", "vamos"),
        (r"\bgran\s*de\b", "grande"),
        (r"\bdi\s*a\b", "dia"),
        (r"\bo\s*l[aá]\b", "olá"),
    ]
    for pat, val in repl:
        t = re.sub(pat, val, t, flags=re.I)
    # Junta números falados separados: "2 2" -> "22", "1 9" -> "19".
    t = re.sub(r"(?<!\d)(\d)\s+(\d)(?!\d)", r"\1\2", t)
    return t


def _extract_time_from_pt_text(text: str) -> tuple[int, int] | None:
    """Entende horários falados pelo STT: '22 horas e 19 minutos', '2 2 hora s e 1 9 minutos', '22:19'."""
    low = _norm_schedule_raw(text)
    compact = _compact_schedule_raw(text)
    if "meianoite" in compact:
        mmid = re.search(r"meianoite(?:e)?(\d{1,2})?min", compact) or re.search(r"meianoite(?:e)?(\d{1,2})(?:minutos|minuto)?", compact)
        minute = int(mmid.group(1)) if mmid and mmid.group(1) else 0
        if 0 <= minute <= 59:
            return 0, minute
    m = re.search(r"(\d{1,2})\s*:\s*(\d{1,2})", low) or re.search(r"(\d{1,2}):(\d{1,2})", compact)
    if m:
        return int(m.group(1)), int(m.group(2))
    for pat in (r"(\d{1,2})horas?e?(\d{1,2})", r"(\d{1,2})h(\d{1,2})", r"as(\d{1,2})e(\d{1,2})", r"(\d{1,2})e(\d{1,2})min"):
        m = re.search(pat, compact)
        if m:
            return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{1,2})horas?", compact) or re.search(r"as(\d{1,2})", compact)
    if m:
        return int(m.group(1)), 0
    return None




def _is_bad_schedule_recipient(value: str) -> bool:
    """Evita que palavras de data/horário sejam salvas como destinatário."""
    raw = _stt_join_schedule_words(str(value or "")).strip(" .,:;!?\n\t\"'")
    compact = _compact_schedule_raw(raw)
    if not compact:
        return True
    bad_exact = {
        "hoje", "amanha", "amanhã", "data", "horario", "horário", "hora", "conteudo", "conteúdo",
        "texto", "mensagem", "confirmo", "confirmado", "sim", "nao", "não", "mim", "eu",
        "meianoite", "meia", "minutos", "minuto", "mesmocontato", "mesmo"
    }
    if compact in bad_exact:
        return True
    if compact.startswith(("hoje", "amanha", "data", "horario", "hora", "as", "às", "meianoite")):
        return True
    if re.fullmatch(r"\d{1,2}(:?\d{2})?", compact):
        return True
    return False



def _looks_like_mark_xlviii_spoken(value: str) -> bool:
    c = _compact_schedule_raw(value or "")
    aliases = {
        "mark_xlviii", "mark_xlviiis", "cyberbox", "cyberpot", "cyberboard", "cyberwatch",
        "mark_xlviiie", "cyberboat", "cyberbort", "ciberbot", "ciberbots", "ciberbox"
    }
    return c in aliases or (c.startswith("cyber") and len(c) <= 14) or (c.startswith("ciber") and len(c) <= 14)


def _message_is_schedule_noise(value: str) -> bool:
    """Detecta texto que veio de comandos/data/sistema e não deve virar conteúdo da mensagem."""
    if _is_internal_schedule_text(value):
        return True
    c = _compact_schedule_raw(value or "")
    if not c:
        return True
    noise = {
        "adata", "datade", "adatade", "datadehoje", "horario", "ohorario", "horarioe",
        "1minute", "oneminute", "meianoite", "confirmo", "confirmado", "contato", "omark_xlviii",
        "whatsappconectadocomsucesso", "whatsappjaconectado", "qrcodedowhatsappaberto",
    }
    if c in noise:
        return True
    if c.startswith(("adata", "datade", "horario", "ohorario", "contatoe", "confirm", "systemevent", "localaction")):
        return True
    if "fraseobrigatoria" in c or "respondaemportugues" in c:
        return True
    return False

def _clean_schedule_recipient(name: str) -> str:
    """Limpa destinatário capturado de fala natural/STT."""
    n = _stt_join_schedule_words(str(name or "")).strip(" .,:;!?\n\t\"'")
    n = re.sub(r"^(?:vai\s+ser|seria|sera|será|é|e|para\s+o\s+contato|o\s+contato\s+vai\s+ser)\s+", "", n, flags=re.I).strip(" .,:;!?")
    n = re.sub(r"^(?:contato|n[uú]mero)\s+(?:vai\s+ser|seria|sera|será|é|e)\s+", "", n, flags=re.I).strip(" .,:;!?")
    n = re.sub(r"^(?:o|a)\s+(?=cyber\s*bot|mark_xlviii|cyber\s*box|cyberbox|cyber\s*pot|cyberpot)", "", n, flags=re.I).strip(" .,:;!?")
    n = re.sub(r"\s+(?:e\s+)?(?:o\s+)?(?:conte[uú]do|texto|mensagem|hor[aá]rio|data)\b.*$", "", n, flags=re.I).strip(" .,:;!?")
    n = re.sub(r"\bCyber\s*Bots?\b", "JARVIS", n, flags=re.I)
    n = re.sub(r"\bCyberbot[s]?\b", "JARVIS", n, flags=re.I)
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"\s*,?\s+(?:a|o|e)$", "", n, flags=re.I).strip(" .,:;!?")
    compact_n = _compact_schedule_raw(n)
    if _looks_like_mark_xlviii_spoken(n) or compact_n in {"omark_xlviii", "amark_xlviii"} or ("mark_xlviii" in compact_n and len(compact_n) <= 14):
        return "JARVIS"
    if _is_bad_schedule_recipient(n):
        return ""
    return n.title().replace("Cyberbot", "JARVIS").replace("Cyberbots", "JARVIS").replace("Cyber Box", "JARVIS").replace("Cyber Pot", "JARVIS")

def _clean_schedule_message(msg: str) -> str:
    """Limpa conteúdo sem cortar frases legítimas como 'é hoje o grande dia'."""
    if _is_internal_schedule_text(msg):
        return ""
    m = _stt_join_schedule_words(str(msg or "")).strip()
    m = re.sub(r"^[\s,;:.!?\-–—\"']+", "", m)
    m = re.sub(r"[\"']+$", "", m).strip()
    m = re.sub(r"^(?:é|e|eh|seria|vai\s+ser|o\s+texto\s+é|a\s+mensagem\s+é|o\s+conte[uú]do\s+é|conte[uú]do\s+é)\s+", "", m, flags=re.I).strip()
    # Remove só trechos de comando que foram colados DEPOIS do conteúdo. Não remove a palavra
    # "hoje" quando ela faz parte da mensagem: "Vamos lá, é hoje o grande dia".
    m = re.sub(r"\s*(?:,|\.|;)?\s*(?:o\s+)?contato\s+(?:vai\s+ser|seria|sera|será|é|e)\s+.+$", "", m, flags=re.I).strip()
    m = re.sub(r"\s*(?:,|\.|;)?\s*(?:para\s+o\s+)?contato\s+.+$", "", m, flags=re.I).strip()
    m = re.sub(r"\s*(?:,|\.|;)?\s*(?:na\s+)?data\s+(?:de\s+)?(?:hoje|amanh[ãa]|dia\s+\d{1,2})\b.*$", "", m, flags=re.I).strip()
    m = re.sub(r"\s*(?:,|\.|;)?\s*(?:o\s+)?hor[aá]rio\b.*$", "", m, flags=re.I).strip()
    m = re.sub(r"\s*(?:,|\.|;)?\s+[àa]s\s+\d{1,2}(?::\d{1,2})?.*$", "", m, flags=re.I).strip()
    m = re.sub(r"\s+([,.;:!?])", r"\1", m)
    m = re.sub(r"([,.;:!?])(?=\S)", r"\1 ", m)
    m = re.sub(r"\s+", " ", m).strip(" \t\n\r,;:.!?-\"'")
    if _message_is_schedule_noise(m):
        return ""
    if m:
        m = m[0].upper() + m[1:]
    return m



_MEDIA_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".mkv", ".avi", ".webm",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".zip", ".rar", ".7z",
    ".mp3", ".wav", ".ogg", ".opus", ".m4a"
}


def _guess_schedule_media_type(text: str = "", file_path: str = "") -> str:
    raw = _norm_schedule_raw((text or "") + " " + (file_path or ""))
    ext = Path(str(file_path or "")).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"} or any(w in raw for w in ("imagem", "foto", "figura", "print")):
        return "image"
    if ext in {".mp4", ".mov", ".mkv", ".avi", ".webm"} or "video" in raw or "vídeo" in str(text or "").lower():
        return "video"
    if ext in {".mp3", ".wav", ".ogg", ".opus", ".m4a"} or "audio" in raw or "áudio" in str(text or "").lower():
        return "audio_file"
    if ext in _MEDIA_EXTS or any(w in raw for w in ("documento", "arquivo", "pdf", "planilha")):
        return "file"
    return "text"


def _extract_possible_file_name(text: str) -> str:
    """Extrai nome de arquivo de fala natural: 'bola.jpg', 'imagem bola', 'arquivo contrato pdf'."""
    raw = _stt_join_schedule_words(str(text or "")).strip()
    if not raw:
        return ""
    # Primeiro pega nome com extensão explícita. Pega somente o trecho final antes da extensão,
    # evitando capturar horário/frases: "23:47 o nome da imagem é bola.jpg" -> "bola.jpg".
    m = re.search(r"(.{0,80}?\.(?:jpg|jpeg|png|gif|webp|bmp|mp4|mov|mkv|avi|webm|pdf|docx?|xlsx?|pptx?|txt|zip|rar|7z|mp3|wav|ogg|opus|m4a))\b", raw, flags=re.I)
    if m:
        cand = re.sub(r"\s+", " ", m.group(1)).strip(" .,:;!?\"'")
        cand = re.split(r"(?:nome\s+da\s+(?:imagem|foto|video|vídeo|arquivo|documento)\s+(?:e|é|esta|está|como)?|arquivo\s+(?:e|é|esta|está|como)?|imagem\s+(?:e|é|esta|está|como)?|foto\s+(?:e|é|esta|está|como)?|video\s+(?:e|é|esta|está|como)?|documento\s+(?:e|é|esta|está|como)?)\s+", cand, flags=re.I)[-1]
        ext = Path(cand).suffix
        stem = cand[:-len(ext)] if ext else cand
        words = [w for w in re.split(r"\s+", stem.strip()) if w]
        if len(words) > 3:
            stem = " ".join(words[-3:])
        cand = (stem.strip(" .,:;!?\"'") + ext).strip()
        return cand
    # Depois pega após 'nome da imagem/arquivo é/está como'.
    patterns = [
        r"nome\s+(?:da\s+)?(?:imagem|foto|video|vídeo|documento|arquivo)\s+(?:e|é|esta|está|como|vai\s+ser|seria)?\s*(?:como)?\s+(.+?)(?:\s+(?:hoje|amanh[ãa]|as|às|horario|horário|para|no\s+grupo|grupo|contato|com\s+legenda)|$)",
        r"(?:imagem|foto|video|vídeo|documento|arquivo)\s+(?:chamada|chamado|com\s+nome|nome)?\s*(?:e|é|esta|está|como)?\s+(.+?)(?:\s+(?:hoje|amanh[ãa]|as|às|horario|horário|para|no\s+grupo|grupo|contato|com\s+legenda)|$)",
    ]
    for pat in patterns:
        mm = re.search(pat, raw, flags=re.I)
        if mm:
            cand = mm.group(1).strip(" .,:;!?\"'")
            cand = re.sub(r"\s+(?:jpg|jpeg|png|pdf|mp4|docx|txt)$", lambda x: "." + x.group(0).strip(), cand, flags=re.I)
            cand = re.sub(r"\s+", " ", cand).strip()
            # Evita pegar frases completas como conteúdo textual.
            if 1 <= len(cand.split()) <= 5:
                return cand
    return ""


def _resolve_schedule_file_safely(file_path: str) -> str:
    """Resolve arquivo para agendamento; aceita nome sem extensão procurando por stem nas pastas comuns."""
    fp = str(file_path or "").strip()
    if not fp:
        return ""
    try:
        return resolve_local_file_path(fp)
    except Exception:
        pass
    raw = fp.strip().strip('"').strip("'")
    stem = Path(raw).stem.lower() if Path(raw).suffix else raw.lower()
    stem = _norm_name(stem)
    if not stem:
        return ""
    for root in _common_user_dirs():
        if not root.exists() or not root.is_dir():
            continue
        try:
            # Primeiro procura só no topo para ser leve.
            for child in root.iterdir():
                if child.is_file() and _norm_name(child.stem) == stem and child.suffix.lower() in _MEDIA_EXTS:
                    return str(child)
            # Depois faz busca controlada.
            for child in root.rglob("*"):
                if child.is_file() and _norm_name(child.stem) == stem and child.suffix.lower() in _MEDIA_EXTS:
                    return str(child)
        except Exception:
            continue
    raise RuntimeError(f"Arquivo não encontrado no PC para agendamento: {file_path}. Informe o caminho completo ou envie o arquivo pela área de upload.")

def _is_schedule_negative_confirmation(text: str) -> bool:
    low = _norm_schedule_raw(text)
    compact = _compact_schedule_raw(text)
    negative_markers = ("naoconfirmo", "naoconfirmado", "naoesta", "naotacerto", "naotacorreto", "errado", "tacerrado", "naoagendar", "cancela", "cancelar")
    return any(x in compact for x in negative_markers) or any(x in low for x in ("não confirmo", "nao confirmo", "não está certo", "ta errado", "tá errado", "está errado"))

def _extract_schedule_parts_from_raw(raw_text: str) -> dict:
    if _is_internal_schedule_text(raw_text):
        return {}
    raw = _stt_join_schedule_words(str(raw_text or "").strip())
    if not raw:
        return {}
    low = _norm_schedule_raw(raw)
    compact = _compact_schedule_raw(raw)
    out = {}
    if "amanha" in compact:
        out["date"] = "amanhã"
    elif "hoje" in compact:
        out["date"] = "hoje"
    # Entende correção do usuário: "dia 27". Usa mês/ano atuais no fuso local do Brasil.
    # Isso evita a IA trocar "hoje" por amanhã quando ela estiver em UTC.
    m_day = re.search(r"\bdia\s+(\d{1,2})\b", low)
    if m_day:
        try:
            dd = int(m_day.group(1))
            nowd = _today_local_date()
            candidate = datetime(nowd.year, nowd.month, dd)
            # Se o dia já passou e o usuário não disse hoje, assume o próximo mês.
            if candidate.date() < nowd and "hoje" not in compact:
                month = nowd.month + 1
                year = nowd.year
                if month > 12:
                    month = 1; year += 1
                candidate = datetime(year, month, dd)
            out["date"] = candidate.strftime("%Y-%m-%d")
        except Exception:
            pass
    tm = _extract_time_from_pt_text(raw)
    if tm:
        hh, mm = tm
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            out["time"] = f"{hh:02d}:{mm:02d}"
    # Primeiro procura padrões específicos de destinatário, para não capturar "para mim".
    m = None
    specific_patterns = [
        r"(?:o\s+)?contato\s+(?:vai\s+ser|seria|sera|será|é|e)\s+(.+?)(?:\s+(?:hoje|amanha|amanhã|as|às|horario|horário|texto|conteudo|conteúdo|mensagem\s+vai|e\s+o\s+conteudo|e\s+o\s+conteúdo)|$)",
        r"(?:para\s+o\s+meu\s+)?contato\s+(.+?)(?:\s+(?:hoje|amanha|amanhã|as|às|horario|horário|texto|conteudo|conteúdo|mensagem\s+vai|e\s+o\s+conteudo|e\s+o\s+conteúdo)|$)",
        r"(?:para\s+o\s+)?(?:numero|n[uú]mero)\s+(.+?)(?:\s+(?:hoje|amanha|amanhã|as|às|horario|horário|texto|conteudo|conteúdo|mensagem\s+vai|e\s+o\s+conteudo|e\s+o\s+conteúdo)|$)",
        r"(?:para\s+)(JARVIS|Cyberbots|Cyber Bot|Cyber Bots|Cyberbox|Cyberpot)(?:\s|$)",
    ]
    for pat in specific_patterns:
        m = re.search(pat, raw, flags=re.I) or re.search(pat, low, flags=re.I)
        if m:
            break
    if not m:
        m = re.search(r"para\s+(?:o\s+|a\s+)?(?:contato\s+|numero\s+|n[uú]mero\s+)?(.+?)(?:\s+(?:hoje|amanha|amanhã|as|às|horario|horário|o\s+horario|texto|conteudo|conteúdo|mensagem\s+vai|e\s+o\s+conteudo)|$)", low, flags=re.I)
    if m:
        name = m.group(1).strip(" .,:;!?\n\t")
        name = re.sub(r"^mim\s+(?:por\s+)?gentileza\s+para\s+(?:o\s+)?contato\s+", "", name, flags=re.I)
        name = re.sub(r"^mim\s*,?\s*(?:por\s+)?gentileza\s*,?\s*para\s+(?:o\s+)?contato\s+", "", name, flags=re.I)
        name = re.sub(r"^meu\s+contato\s+", "", name, flags=re.I)
        name = re.sub(r"\s+e\s+o\s+conte[uú]do.*$", "", name, flags=re.I)
        name = _clean_schedule_recipient(name)
        if name and len(name) <= 80:
            out["to"] = name
    if not out.get("to"):
        mm_to = re.search(r"(?:o\s+)?contato\s+(?:vai\s+ser|seria|sera|será|é|e)\s+(.+?)(?:\s+(?:hoje|amanha|amanhã|as|às|horario|horário|texto|conteudo|conteúdo)|$)", raw, flags=re.I)
        if mm_to:
            cleaned_to = _clean_schedule_recipient(mm_to.group(1))
            if cleaned_to and len(cleaned_to) <= 80:
                out["to"] = cleaned_to
    msg = ""
    patterns = [
        r"(?:conteudo|conteúdo)\s+(?:da\s+)?(?:mensagem\s+)?(?:vai\s+ser|sera|é|e)\s+(.+)$",
        r"(?:texto)\s+(?:da\s+)?(?:mensagem\s+)?(?:vai\s+ser|sera|é|e)\s+(.+)$",
        r"(?:a\s+mensagem\s+(?:é|e|vai\s+ser))\s+(.+)$",
        r"(?:mensagem\s+vai\s+ser)\s+(.+)$",
    ]
    for pat in patterns:
        mm = re.search(pat, raw, flags=re.I)
        if mm:
            msg = mm.group(1).strip(" .\n\t")
            break
    if msg:
        # Não cortar "hoje" quando faz parte do conteúdo: "é hoje o grande dia".
        msg = re.sub(r"\s*(?:,|\.|;)?\s*(?:na\s+)?data\s+(?:de\s+)?(?:hoje|amanh[ãa]|dia\s+\d{1,2}).*$", "", msg, flags=re.I).strip(" .")
        msg = re.sub(r"\s*(?:,|\.|;)?\s*(?:o\s+)?hor[aá]rio.*$", "", msg, flags=re.I).strip(" .")
        msg = _clean_schedule_message(msg)
        if msg:
            out["message"] = msg

    media_type = _guess_schedule_media_type(raw, "")
    possible_file = _extract_possible_file_name(raw)
    if media_type != "text" or possible_file:
        out["media_type"] = media_type if media_type != "text" else "file"
        if possible_file:
            out["file_path"] = possible_file
        # Se o usuário disser "conteúdo é uma imagem/arquivo", isso não é texto a enviar; é mídia.
        if out.get("message") and _compact_schedule_raw(out["message"]) in {"imagem", "foto", "video", "vídeo", "documento", "arquivo"}:
            out.pop("message", None)
    return out

def _parse_schedule_dt(schedule_at: str = "", date: str = "", time_text: str = "") -> datetime:
    """Parser de data/hora blindado contra erro de fuso/IA.

    Regras importantes:
    - "hoje" sempre usa a data real local (_now_local), nunca a data inferida pelo modelo.
    - "amanhã" sempre usa +1 dia local.
    - Se o usuário explicitou "hoje" e o horário já passou, não joga para amanhã em silêncio.
    - Se não houver data explícita e o horário já passou, aí sim agenda para amanhã.
    """
    raw = str(schedule_at or "").strip()
    d_raw = str(date or "").strip()
    t_raw = str(time_text or "").strip()
    now = _now_local()

    # Normalização da data separada.
    d_norm = _compact_schedule_raw(d_raw)
    explicit_today = d_norm in {"hoje", "today"}
    explicit_tomorrow = d_norm in {"amanha", "amanhã", "tomorrow"}
    explicit_date_value = None
    if d_raw and not explicit_today and not explicit_tomorrow:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                explicit_date_value = datetime.strptime(d_raw[:10], fmt)
                break
            except Exception:
                pass

    # Se schedule_at veio do modelo, mas date/time também vieram do texto do usuário,
    # preferimos date/time porque pode conter "hoje" corrigido pelo usuário.
    if raw and not (t_raw and (explicit_today or explicit_tomorrow or explicit_date_value)):
        raw2 = raw.replace("T", " ").replace("Z", "").strip()
        compact = _compact_schedule_raw(raw2)
        raw_today = "hoje" in compact
        raw_tomorrow = "amanha" in compact

        # ISO/data numérica só é aceita quando o texto do usuário não disse "hoje" separado.
        if not raw_today and not raw_tomorrow:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M"):
                try:
                    val = raw2[:19] if "%S" in fmt else raw2[:16]
                    return datetime.strptime(val, fmt)
                except Exception:
                    pass

        parsed_time = _extract_time_from_pt_text(raw2)
        if parsed_time:
            hh, mm = parsed_time
            if raw_tomorrow:
                base = now + timedelta(days=1)
            else:
                base = now
            dt = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
            # Só joga para amanhã se o usuário NÃO disse hoje.
            if dt <= now and not raw_today and not raw_tomorrow:
                dt += timedelta(days=1)
            if raw_today and dt < now.replace(second=0, microsecond=0):
                raise RuntimeError("O horário informado para hoje já passou. Informe um horário futuro ou diga amanhã.")
            return dt

    # Monta usando date + time.
    t = t_raw.lower().replace("horas", ":00").replace("hora", ":00").replace("h", ":")
    t = re.sub(r"[^0-9:]", "", t)
    if t.endswith(":"):
        t += "00"
    if not t:
        raise RuntimeError("Informe o horário da mensagem agendada, exemplo: 21:00.")
    if re.fullmatch(r"\d{1,2}", t):
        t += ":00"
    parts = t.split(":")
    hh = int(parts[0]); mm = int(parts[1] or 0)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise RuntimeError("Horário inválido. Use HH:MM, por exemplo 21:00.")

    if explicit_today or not d_raw:
        base = now
    elif explicit_tomorrow:
        base = now + timedelta(days=1)
    elif explicit_date_value is not None:
        base = explicit_date_value
    else:
        raise RuntimeError("Data inválida. Use hoje, amanhã ou YYYY-MM-DD.")

    dt = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if explicit_today and dt < now.replace(second=0, microsecond=0):
        raise RuntimeError("O horário informado para hoje já passou. Informe um horário futuro ou diga amanhã.")
    if not d_raw and dt <= now:
        dt += timedelta(days=1)
    return dt

def _load_schedules() -> list[dict]:
    data = _load_json_file(SCHEDULE_FILE, [])
    return data if isinstance(data, list) else []


def _save_schedules(items: list[dict]) -> None:
    _save_json_file(SCHEDULE_FILE, items)


def _load_pending_schedule() -> dict:
    data = _load_json_file(PENDING_SCHEDULE_FILE, {})
    if not isinstance(data, dict):
        return {}
    # Rascunho pendente é estado de conversa, não deve voltar depois que fecha/abre o JARVIS.
    # Também expira rapidamente para não prender comandos normais em modo agendamento.
    try:
        # Rascunho antigo sem boot_id ou de outra execução nunca deve voltar.
        if (not data.get("boot_id")) or str(data.get("boot_id")) != BOOT_ID:
            _save_json_file(PENDING_SCHEDULE_FILE, {})
            return {}
        if _is_internal_schedule_text(data.get("message") or "") or _message_is_schedule_noise(str(data.get("message") or "")) and str(data.get("message") or ""):
            _save_json_file(PENDING_SCHEDULE_FILE, {})
            return {}
        created = str(data.get("created_at") or "")
        if created:
            dt = datetime.fromisoformat(created.replace("Z", ""))
            if (_now_local() - dt).total_seconds() > 900:
                _save_json_file(PENDING_SCHEDULE_FILE, {})
                return {}
    except Exception:
        pass
    return data


def _save_pending_schedule(data: dict) -> None:
    if data:
        data = dict(data)
        data["boot_id"] = BOOT_ID
    _save_json_file(PENDING_SCHEDULE_FILE, data or {})



def _norm_for_schedule_compare(value: str) -> str:
    return _compact_schedule_raw(_stt_join_schedule_words(str(value or "")))


def _schedule_draft_score(d: dict) -> int:
    to = _clean_schedule_recipient(str(d.get("to") or ""))
    msg = _clean_schedule_message(str(d.get("message") or ""))
    score = 0
    if to and not _is_bad_schedule_recipient(to):
        score += 100
    if d.get("schedule_at") or (d.get("date") and d.get("time")) or d.get("time"):
        score += 80
    if str(d.get("media_type") or "text") != "text":
        if d.get("file_path"):
            score += 90
    else:
        if msg:
            score += min(120, len(msg) * 3)
            # Conteúdos muito curtos ou picotados valem menos quando já havia frase maior.
            if len(msg) < 8:
                score -= 30
    if _is_bad_schedule_recipient(to):
        score -= 120
    if _message_is_schedule_noise(msg):
        score -= 80
    return score


def _format_pending_schedule_confirmation(d: dict) -> str:
    to = _clean_schedule_recipient(str(d.get("to") or ""))
    media_type = str(d.get("media_type") or "text").strip().lower()
    schedule_at = str(d.get("schedule_at") or "").strip()
    try:
        dt = _parse_schedule_dt(schedule_at, str(d.get("date") or ""), str(d.get("time") or ""))
        when = dt.strftime("%d/%m/%Y às %H:%M")
    except Exception:
        when = schedule_at or "horário pendente"
    if media_type != "text":
        fname = Path(str(d.get("file_path") or "arquivo")).name
        cap = _clean_schedule_message(str(d.get("caption") or ""))
        cap_txt = f' com legenda "{cap}"' if cap else ""
        return f'Confirma agendar envio do arquivo "{fname}" para {to} em {when}{cap_txt}? Responda confirmo para agendar. Não enviei nada agora.'
    msg = _clean_schedule_message(str(d.get("message") or ""))
    return f'Confirma agendar mensagem para {to} em {when} com o conteúdo: "{msg}"? Responda confirmo para agendar. Não enviei nenhuma mensagem agora.'


def _should_keep_existing_schedule(existing: dict, new_draft: dict) -> bool:
    """Protege rascunho bom contra segunda chamada ruim da IA no mesmo turno."""
    if not existing or not existing.get("ready"):
        return False
    old_to = _clean_schedule_recipient(str(existing.get("to") or ""))
    new_to = _clean_schedule_recipient(str(new_draft.get("to") or ""))
    old_msg = _clean_schedule_message(str(existing.get("message") or ""))
    new_msg = _clean_schedule_message(str(new_draft.get("message") or ""))
    old_norm = _norm_for_schedule_compare(old_msg)
    new_norm = _norm_for_schedule_compare(new_msg)
    # Nunca troca um contato válido por palavra de data/horário, como "Hoje".
    if old_to and (not new_to or _is_bad_schedule_recipient(new_to)):
        return True
    # Nunca troca JARVIS por "Hoje", "O JARVIS" ruim etc.
    if old_to and new_to and _norm_for_schedule_compare(new_to) in {"hoje", "amanha", "data", "horario"}:
        return True
    # Se já tinha conteúdo completo, não deixa uma chamada seguinte cortar para prefixo.
    if old_msg and new_msg:
        if len(old_msg) >= 12 and len(new_msg) + 6 < len(old_msg):
            if new_norm and (old_norm.startswith(new_norm) or new_norm in old_norm):
                return True
        # Exemplo real: "Vamos lá, é hoje o grande dia" não pode virar "Va mos lá, é".
        if old_norm and new_norm and old_norm != new_norm and new_norm in old_norm and len(new_norm) < len(old_norm):
            return True
    # Score final: só troca se o novo rascunho for claramente melhor.
    return _schedule_draft_score(existing) > _schedule_draft_score(new_draft) + 20

def schedule_whatsapp_message(to: str = "", message: str = "", schedule_at: str = "", date: str = "", time_text: str = "", confirm: bool = False, raw_text: str = "", file_path: str = "", media_type: str = "", caption: str = "") -> str:
    """Cria somente um rascunho de agendamento.

    Segurança: esta função NUNCA envia e NUNCA agenda definitivamente.
    O agendamento só entra na fila quando confirm_pending_schedule() é chamado.
    Isso evita o bug em que o modelo mandava a mensagem na hora ou agendava sem confirmação.
    """
    pending = _load_pending_schedule()
    if _is_schedule_negative_confirmation(raw_text or message or ""):
        _save_pending_schedule({})
        return "Agendamento em preparação cancelado. Nenhuma mensagem foi enviada nem agendada."
    if _is_internal_schedule_text(raw_text) or _is_internal_schedule_text(message):
        # Status interno do WhatsApp/Jarvis nunca pode ser salvo como texto da mensagem.
        raw_text = ""
        message = ""
    extracted = _extract_schedule_parts_from_raw(raw_text or "")
    extracted_from_message = _extract_schedule_parts_from_raw(message or "")
    # Se o usuário começa um novo agendamento explícito, não reaproveita rascunho antigo/stale.
    raw_compact = _compact_schedule_raw(raw_text or "")
    if raw_compact and any(x in raw_compact for x in ("agendar", "agende", "agendo", "agendamento", "programar", "programa", "agentemensagem", "agendamensagem")):
        if not _compact_schedule_raw(raw_text or "") in {"confirmo", "confirmado", "sim"}:
            pending = {}
    to_candidate = _clean_schedule_recipient(to or extracted.get("to") or "")
    pending_to = _clean_schedule_recipient(str(pending.get("to") or ""))
    if not to_candidate and raw_text and _looks_like_mark_xlviii_spoken(raw_text):
        to_candidate = "JARVIS"
    if not to_candidate and pending_to:
        to = pending_to
    elif to_candidate:
        to = to_candidate
    else:
        to = ""
    media_type = (media_type or extracted.get("media_type") or extracted_from_message.get("media_type") or pending.get("media_type") or "text").strip().lower()
    file_path = str(file_path or extracted.get("file_path") or extracted_from_message.get("file_path") or pending.get("file_path") or "").strip()
    caption = _clean_schedule_message(caption or pending.get("caption") or "")
    new_message_candidate = _clean_schedule_message(extracted.get("message") or extracted_from_message.get("message") or message or "")
    pending_message = _clean_schedule_message(str(pending.get("message") or ""))
    if new_message_candidate and pending_message:
        # Se o novo texto é só prefixo do antigo, mantém o antigo completo.
        nnew = _norm_for_schedule_compare(new_message_candidate)
        nold = _norm_for_schedule_compare(pending_message)
        if nnew and nold and nnew in nold and len(nnew) + 6 < len(nold):
            message = pending_message
        else:
            message = new_message_candidate
    else:
        message = new_message_candidate or pending_message
    if media_type != "text" and not file_path:
        maybe_file = _extract_possible_file_name(raw_text or message or caption)
        if maybe_file:
            file_path = maybe_file
    if media_type != "text" and message:
        msg_compact = _compact_schedule_raw(message)
        # Frases como "uma imagem, o nome da imagem está como bola" são descrição do arquivo, não legenda.
        if any(x in msg_compact for x in ("umaimagem", "umafoto", "umvideo", "umdocumento", "umarquivo", "nomedaimagem", "nomedoarquivo")):
            message = ""
    if file_path:
        media_type = _guess_schedule_media_type(raw_text or message or caption, file_path)
        if media_type == "text":
            media_type = "file"
    # Se já existe rascunho e o usuário respondeu só o texto da mensagem
    # (ex.: "olá, boa noite"), salva esse texto como conteúdo, não como envio imediato.
    if not message and pending and raw_text:
        candidate = _stt_join_schedule_words(raw_text).strip(" .")
        ccompact = _compact_schedule_raw(candidate)
        if candidate and len(candidate) >= 2 and not _extract_time_from_pt_text(candidate) and not _looks_like_mark_xlviii_spoken(candidate) and not any(x in ccompact for x in ("agendar", "agenda", "agende", "agendo", "horario", "hora", "confirmo", "confirmado", "contato", "mark_xlviii", "cyber", "ciber")):
            if _guess_schedule_media_type(candidate, file_path) != "text":
                media_type = _guess_schedule_media_type(candidate, file_path)
                possible_file = _extract_possible_file_name(candidate)
                if possible_file:
                    file_path = possible_file
            else:
                message = _clean_schedule_message(candidate)
    schedule_at = (schedule_at or pending.get("schedule_at") or "").strip()
    # Data e hora extraídas da fala do usuário têm prioridade absoluta sobre data sugerida pela IA.
    date = (extracted.get("date") or date or pending.get("date") or "").strip()
    time_text = (extracted.get("time") or extracted_from_message.get("time") or time_text or pending.get("time") or "").strip()
    if raw_text and (extracted.get("time") or extracted.get("date")):
        # Evita aceitar ISO errado do modelo, como amanhã, quando o usuário disse "hoje".
        schedule_at = ""
    if not schedule_at and raw_text and (extracted.get("time") or extracted.get("date")) and not (date and time_text):
        schedule_at = raw_text
    # Resolve arquivo quando for mídia. Para agendamento de mídia, mensagem vira legenda opcional.
    resolved_file = ""
    if media_type != "text":
        if file_path:
            try:
                resolved_file = _resolve_schedule_file_safely(file_path)
                file_path = resolved_file
            except Exception:
                # Mantém o rascunho, mas informa que falta localizar o arquivo.
                resolved_file = ""
        if message and not caption:
            # Se foi uma frase curta tipo "boa noite" junto com mídia, usa como legenda.
            caption = message
        # Se a mensagem for só o nome do arquivo, não usa como legenda.
        if message and file_path and _norm_name(Path(file_path).stem) in _norm_name(message):
            caption = ""
            message = ""
    draft = {"to": to, "message": message, "caption": caption, "media_type": media_type, "file_path": file_path, "schedule_at": schedule_at, "date": date, "time": time_text, "created_at": _now_local().isoformat(), "ready": False}
    _save_pending_schedule(draft)
    missing = []
    if not to: missing.append("contato ou número")
    if media_type != "text":
        if not file_path: missing.append("arquivo/imagem/vídeo/documento")
        elif not resolved_file: missing.append("arquivo localizado no PC")
    else:
        if not message: missing.append("conteúdo da mensagem")
    if not (schedule_at or (date and time_text) or time_text): missing.append("data/horário")
    if missing:
        return "Agendamento em preparação. Falta: " + ", ".join(missing) + ". Não enviei nenhuma mensagem agora."
    try:
        dt = _parse_schedule_dt(schedule_at, date, time_text)
    except Exception as e:
        _save_pending_schedule({**draft, "ready": False})
        return f"Agendamento em preparação. {e} Não enviei nenhuma mensagem agora."
    when = dt.strftime("%d/%m/%Y às %H:%M")
    ready_draft = {**draft, "schedule_at": dt.isoformat(timespec="minutes"), "ready": True}
    if _should_keep_existing_schedule(pending, ready_draft):
        _save_pending_schedule(pending)
        return _format_pending_schedule_confirmation(pending)
    _save_pending_schedule(ready_draft)
    if media_type != "text":
        fname = Path(file_path).name
        cap_txt = f' com legenda "{caption}"' if caption else ""
        return f'Confirma agendar envio do arquivo "{fname}" para {to} em {when}{cap_txt}? Responda confirmo para agendar. Não enviei nada agora.'
    return f'Confirma agendar mensagem para {to} em {when} com o conteúdo: "{message}"? Responda confirmo para agendar. Não enviei nenhuma mensagem agora.'


def _finalize_schedule_from_pending(p: dict) -> str:
    to = _clean_schedule_recipient(str(p.get("to") or ""))
    message = _clean_schedule_message(str(p.get("message") or ""))
    caption = _clean_schedule_message(str(p.get("caption") or ""))
    media_type = str(p.get("media_type") or "text").strip().lower()
    file_path = str(p.get("file_path") or "").strip()
    schedule_at = str(p.get("schedule_at") or "").strip()
    if media_type != "text":
        if not to or not file_path or not schedule_at:
            return "O agendamento de arquivo está incompleto. Informe destino, arquivo e horário novamente."
        file_path = _resolve_schedule_file_safely(file_path)
    elif not to or not message or not schedule_at:
        return "O agendamento pendente está incompleto. Informe contato, horário e conteúdo novamente."
    dt = _parse_schedule_dt(schedule_at)
    items = _load_schedules()
    job_id = "wa-" + str(int(time.time()))
    items.append({
        "id": job_id, "to": to, "message": message, "caption": caption, "media_type": media_type,
        "file_path": file_path, "schedule_at": dt.isoformat(timespec="minutes"), "status": "pending", "created_at": _now_local().isoformat()
    })
    _save_schedules(items)
    _save_pending_schedule({})
    when = dt.strftime("%d/%m/%Y às %H:%M")
    if media_type != "text":
        fname = Path(file_path).name
        cap_txt = f' Legenda: "{caption}".' if caption else ""
        return f'Arquivo "{fname}" agendado para {to} em {when}.{cap_txt} O JARVIS precisa estar aberto e o WhatsApp conectado nesse horário.'
    return f'Mensagem agendada para {to} em {when}. Conteúdo: "{message}". O JARVIS precisa estar aberto e o WhatsApp conectado nesse horário.'


def clear_pending_schedule() -> str:
    _save_pending_schedule({})
    return "Agendamento em preparação cancelado. Nenhuma mensagem foi enviada nem agendada."


def confirm_pending_schedule() -> str:
    p = _load_pending_schedule()
    if not p or not p.get("ready"):
        return "Não encontrei agendamento pendente para confirmar. Primeiro informe contato, horário e conteúdo da mensagem."
    return _finalize_schedule_from_pending(p)

def list_scheduled_messages() -> str:
    items = [x for x in _load_schedules() if x.get("status") == "pending"]
    if not items:
        return "Não há mensagens agendadas pendentes."
    lines = []
    for x in items[:20]:
        try:
            dt = datetime.fromisoformat(x.get("schedule_at", ""))
            when = dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            when = x.get("schedule_at", "")
        if str(x.get("media_type") or "text") != "text":
            lines.append(f'- {x.get("id")}: arquivo {Path(str(x.get("file_path") or "")).name} para {x.get("to")} em {when}')
        else:
            lines.append(f'- {x.get("id")}: para {x.get("to")} em {when}: {x.get("message")}')
    return "Mensagens agendadas:\n" + "\n".join(lines)


def cancel_scheduled_message(schedule_id: str = "") -> str:
    items = _load_schedules()
    pending = [x for x in items if x.get("status") == "pending"]
    if not pending:
        return "Não há mensagens agendadas para cancelar."
    target = str(schedule_id or "").strip()
    if not target and len(pending) == 1:
        target = pending[0].get("id", "")
    changed = False
    for x in items:
        if x.get("id") == target:
            x["status"] = "cancelled"
            x["cancelled_at"] = datetime.now().isoformat()
            changed = True
    _save_schedules(items)
    return "Agendamento cancelado." if changed else "Não encontrei esse agendamento."


def poll_due_scheduled_messages(limit: int = 5) -> list[dict]:
    now = _now_local()
    items = _load_schedules()
    events = []
    changed = False
    for x in items:
        if len(events) >= limit:
            break
        if x.get("status") != "pending":
            continue
        try:
            dt = datetime.fromisoformat(x.get("schedule_at", ""))
        except Exception:
            continue
        if dt <= now:
            try:
                if str(x.get("media_type") or "text") != "text":
                    send_whatsapp_file(x.get("to", ""), x.get("file_path", ""), x.get("caption", "") or x.get("message", ""))
                    sent_desc = Path(str(x.get("file_path") or "arquivo")).name
                else:
                    send_whatsapp_text(x.get("to", ""), x.get("message", ""))
                    sent_desc = x.get("message")
                x["status"] = "sent"
                x["sent_at"] = datetime.now().isoformat()
                events.append({"ok": True, "to": x.get("to"), "message": sent_desc, "id": x.get("id"), "media_type": x.get("media_type", "text")})
            except Exception as e:
                x["status"] = "failed"
                x["error"] = str(e)
                x["failed_at"] = datetime.now().isoformat()
                events.append({"ok": False, "to": x.get("to"), "message": x.get("message"), "id": x.get("id"), "error": str(e)})
            changed = True
    if changed:
        _save_schedules(items)
    return events


# ─────────────────────────────────────────────────────────────
# WhatsApp business agent
# ─────────────────────────────────────────────────────────────

def _load_agent_config() -> dict:
    data = _load_json_file(AGENT_FILE, {})
    if not isinstance(data, dict):
        return {}
    # Segurança: não reativar modo agente de uma execução antiga sem pedido explícito.
    # Se o usuário ativar o agente nesta execução, agent_enable grava o BOOT_ID atual.
    if data.get("enabled") and data.get("boot_id") != BOOT_ID:
        data["enabled"] = False
        data["disabled_at"] = datetime.now().isoformat()
        data["disabled_reason"] = "Desativado automaticamente ao iniciar nova sessão do JARVIS para evitar agente responder sozinho."
        try:
            _save_json_file(AGENT_FILE, data)
        except Exception:
            pass
    return data


def _save_agent_config(data: dict) -> None:
    _save_json_file(AGENT_FILE, data or {})


def _read_text_file_safely(file_path: str) -> tuple[str, str]:
    resolved = resolve_local_file_path(file_path)
    pth = Path(resolved)
    text = pth.read_text(encoding="utf-8", errors="replace")
    if len(text.strip()) < 20:
        raise RuntimeError("O arquivo de conhecimento está vazio ou muito curto.")
    return str(pth), text[:30000]


def _persist_agent_knowledge_file(resolved: str) -> str:
    """Copia o TXT para uma pasta permanente do JARVIS para sobreviver a reinício."""
    src = Path(resolved)
    AGENT_KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", src.name or "base_agente.txt")
    dst = AGENT_KNOWLEDGE_DIR / safe
    if dst.exists():
        dst = AGENT_KNOWLEDGE_DIR / (str(int(time.time())) + "_" + safe)
    try:
        shutil.copy2(src, dst)
        return str(dst)
    except Exception:
        return str(src)


def agent_load_knowledge(file_path: str) -> str:
    resolved, text = _read_text_file_safely(file_path)
    stored = _persist_agent_knowledge_file(resolved)
    cfg = _load_agent_config()
    cfg.update({"knowledge_file": stored, "original_knowledge_file": resolved, "knowledge_text": text, "knowledge_loaded_at": datetime.now().isoformat()})
    _save_agent_config(cfg)
    return f"Conhecimento do agente carregado e salvo no sistema: {Path(stored).name}. Agora posso ativar o agente WhatsApp."


def _norm_agent_mode(mode: str, target: str = "") -> str:
    raw = _norm_mode_text(mode or "")
    if raw in {"todos", "tudo", "any", "all", "geral", "qualquer"}: return "any"
    if raw in {"privado", "privados", "private", "contatos", "contatos privados"}: return "private"
    if raw in {"grupo", "grupos", "groups", "somente grupos"} and not target: return "groups"
    if raw in {"grupo especifico", "specific group"}: return "group"
    if raw in {"contato", "contact", "specific contact", "contato especifico"}: return "contact"
    if not raw:
        return "contact" if target else "private"
    return raw


def agent_enable(mode: str = "private", target: str = "", file_path: str = "", notify: bool = False) -> str:
    cfg = _load_agent_config()
    if file_path:
        resolved, text = _read_text_file_safely(file_path)
        stored = _persist_agent_knowledge_file(resolved)
        cfg["knowledge_file"] = stored
        cfg["original_knowledge_file"] = resolved
        cfg["knowledge_text"] = text
        cfg["knowledge_loaded_at"] = datetime.now().isoformat()
    if not cfg.get("knowledge_text"):
        return "Para ativar o agente, primeiro selecione/envie um arquivo TXT com as informações da empresa e diga: carregar este TXT como conhecimento do agente."
    mode = _norm_agent_mode(mode, target)
    if mode in {"contact", "group"} and not target:
        return "Para agente específico, informe o nome do contato ou grupo."
    cfg.update({"enabled": True, "mode": mode, "target": target, "notify": bool(notify), "responded": cfg.get("responded") or {}, "enabled_at": datetime.now().isoformat(), "enabled_since_ts": int(time.time()), "boot_id": BOOT_ID})
    _save_agent_config(cfg)
    # Agente e leitura em voz alta são funções separadas.
    # Por padrão o agente responde em silêncio no WhatsApp; só haverá fala se o usuário ativar alertas separadamente.
    if not bool(notify):
        try:
            watch_whatsapp_messages("off", "")
        except Exception:
            pass
    label = {"any":"todos", "private":"somente privados", "groups":"somente grupos", "contact":f"contato {target}", "group":f"grupo {target}"}.get(mode, mode)
    silent = " Modo silencioso: não vou ler mensagens em voz alta, só responder no WhatsApp." if not bool(notify) else " Vou avisar quando o agente responder."
    return f"Agente WhatsApp ativado em modo {label}. Ele responderá com base no arquivo {Path(cfg.get('knowledge_file','')).name}.{silent}"


def agent_disable() -> str:
    cfg = _load_agent_config()
    cfg["enabled"] = False
    cfg["disabled_at"] = datetime.now().isoformat()
    _save_agent_config(cfg)
    return "Agente WhatsApp desativado."


def agent_status() -> str:
    cfg = _load_agent_config()
    if not cfg.get("enabled"):
        if cfg.get("knowledge_file"):
            return f"Agente WhatsApp está desativado. Conhecimento carregado: {Path(cfg.get('knowledge_file')).name}."
        return "Agente WhatsApp está desativado e sem arquivo de conhecimento carregado."
    return f"Agente WhatsApp ativo. Modo: {cfg.get('mode')}. Alvo: {cfg.get('target') or 'todos do modo'}. Arquivo: {Path(cfg.get('knowledge_file','')).name}."



def _agent_match_target(ev: dict, target: str) -> bool:
    t = _norm_mode_text(target or "")
    if not t:
        return False
    vals = [ev.get("name"), ev.get("chat_name"), ev.get("sender_jid"), ev.get("jid")]
    for v in vals:
        n = _norm_mode_text(v or "")
        if n and (n == t or n in t or t in n):
            return True
    return False

def _agent_matches(cfg: dict, ev: dict) -> bool:
    if not cfg.get("enabled"):
        return False
    mode = str(cfg.get("mode") or "private").lower()
    is_group = bool(ev.get("is_group")) or str(ev.get("jid") or "").endswith("@g.us")
    if mode == "any": return True
    if mode == "private": return not is_group
    if mode == "groups": return is_group
    target = str(cfg.get("target") or "")
    if mode == "contact": return (not is_group) and _agent_match_target(ev, target)
    if mode == "group": return is_group and _agent_match_target(ev, target)
    return False


def _agent_generate_reply(cfg: dict, ev: dict) -> str:
    api_key = _read_gemini_key()
    if not api_key:
        raise RuntimeError("Gemini API Key não encontrada para o agente WhatsApp.")
    from google import genai
    client = genai.Client(api_key=api_key)
    knowledge = str(cfg.get("knowledge_text") or "")[:30000]
    is_group = bool(ev.get("is_group")) or str(ev.get("jid") or "").endswith("@g.us")
    sender = ev.get("name") or "cliente"
    chat = ev.get("chat_name") or "conversa"
    text = str(ev.get("text") or "").strip()
    media_kind = str(ev.get("media_kind") or "").lower().strip()
    media_path = str(ev.get("media_path") or "").strip()

    media_context = ""
    if media_kind == "audio" and media_path:
        try:
            transcript = _transcribe_audio_file(media_path)
            media_context = f"Tipo de conteúdo recebido: áudio\nTranscrição do áudio: {transcript}"
            text = f"[áudio transcrito] {transcript}"
        except Exception as e:
            media_context = f"Tipo de conteúdo recebido: áudio\nFalha ao transcrever o áudio: {e}"
    elif media_kind == "image" and media_path:
        try:
            image_desc = _analyze_image_file(media_path)
            caption = "" if text.startswith("[") else text
            media_context = "Tipo de conteúdo recebido: imagem\n"
            if caption:
                media_context += f"Legenda/texto da imagem: {caption}\n"
            media_context += f"Análise da imagem: {image_desc}"
        except Exception as e:
            media_context = f"Tipo de conteúdo recebido: imagem\nFalha ao analisar a imagem: {e}"

    message_context = text
    if media_context:
        message_context = (message_context + "\n" + media_context).strip()

    prompt_text = f"""
Você é um agente comercial de WhatsApp da empresa do usuário. Responda em português do Brasil.
Use SOMENTE as informações do conhecimento abaixo; se não souber, peça contato humano ou diga que vai verificar.
Se a mensagem for spam, promoção aleatória, link suspeito ou não parecer conversa de cliente, responda apenas: NAO_RESPONDER.
Nunca prometa preço, prazo ou disponibilidade que não esteja no conhecimento.
Se o cliente enviar áudio, use a transcrição do áudio para entender o pedido e responder normalmente.
Se o cliente enviar imagem, use a análise da imagem e qualquer legenda visível para responder normalmente.
Se for grupo, comece com o nome do remetente de forma natural, exemplo: "{sender}, ...".
Se for privado, responda direto e de forma educada.
Seja curto: no máximo 3 frases.

[CONHECIMENTO DA EMPRESA]
{knowledge}

[CONTEXTO]
Tipo: {'grupo' if is_group else 'privado'}
Conversa/grupo: {chat}
Remetente: {sender}
Conteúdo recebido: {message_context}

Resposta do agente:
""".strip()
    # Reutiliza a mesma cadeia de fallback usada para áudio e imagem. Assim, a
    # automação não para quando um modelo específico está temporariamente sem
    # cota ou indisponível.
    resp = _generate_media_with_fallback(client, prompt_text)
    reply = str(getattr(resp, "text", "") or "").strip()
    reply = re.sub(r"\s+", " ", reply).strip()
    if not reply or reply.upper().startswith("NAO_RESPONDER") or reply.upper().startswith("NÃO_RESPONDER"):
        return ""
    return reply[:900]


def process_whatsapp_agent_event(ev: dict) -> dict | None:
    cfg = _load_agent_config()
    if not _agent_matches(cfg, ev):
        return None
    try:
        since_ts = int(float(cfg.get("enabled_since_ts") or 0))
        ev_ts = int(float(ev.get("timestamp") or 0))
        if since_ts and ev_ts and ev_ts < since_ts - 5:
            return None
    except Exception:
        pass
    responded = cfg.get("responded") or {}
    ev_id = str(ev.get("id") or (str(ev.get("jid")) + str(ev.get("timestamp"))))
    if responded.get(ev_id):
        return None
    text = str(ev.get("text") or "").strip()
    media_kind = str(ev.get("media_kind") or "").lower().strip()
    media_path = str(ev.get("media_path") or "").strip()
    has_supported_media = media_kind in {"audio", "image"} and bool(media_path)
    if not text and not has_supported_media:
        return None
    if text.startswith("[") and "áudio transcrito" not in text.lower() and "audio transcrito" not in text.lower() and not has_supported_media:
        return None
    reply = _agent_generate_reply(cfg, ev)
    responded[ev_id] = {"at": datetime.now().isoformat(), "reply": reply[:200], "media_kind": media_kind}
    cfg["responded"] = responded
    _save_agent_config(cfg)
    if not reply:
        return {"ok": True, "skipped": True, "reason": "mensagem não parecia de cliente"}
    target = ev.get("jid") if ev.get("is_group") else (ev.get("sender_jid") or ev.get("jid"))
    send_whatsapp_text(str(target), reply)
    return {"ok": True, "sent": True, "to": ev.get("chat_name") or ev.get("name") or target, "reply": reply, "notify": bool(cfg.get("notify", True)), "is_group": bool(ev.get("is_group")), "media_kind": media_kind}


def poll_whatsapp_agent_events(limit: int = 10) -> list[dict]:
    """Busca eventos do agente de forma robusta.

    A fila interna do Node é mantida, mas também fazemos uma varredura nas
    mensagens recentes do WhatsApp. Isso evita o bug em que o modo agente fica
    ativo, porém não responde porque a fila/seen do bridge ficou presa após
    mudança visual, reinício ou ativação silenciosa.
    """
    cfg = _load_agent_config()
    if not cfg.get("enabled"):
        return []
    since_ts = 0
    try:
        since_ts = int(float(cfg.get("enabled_since_ts") or 0))
    except Exception:
        since_ts = 0
    out: list[dict] = []
    seen: set[str] = set()

    def add_ev(ev: dict):
        if not ev:
            return
        ev_id = str(ev.get("id") or (str(ev.get("jid")) + str(ev.get("timestamp"))))
        if ev_id in seen:
            return
        try:
            ev_ts = int(float(ev.get("timestamp") or 0))
            if since_ts and ev_ts and ev_ts < since_ts - 5:
                return
        except Exception:
            pass
        seen.add(ev_id)
        out.append(ev)

    # 1) fila oficial do bridge
    try:
        data = _get_no_start("/agent/poll", {"limit": limit})
        if data.get("ok") is not False:
            for ev in data.get("messages") or []:
                add_ev(ev)
    except Exception:
        pass

    # 2) varredura das mensagens recentes como fallback premium
    try:
        data = _get_no_start("/messages/latest", {"limit": max(30, int(limit or 10)), "direction": "incoming"})
        if data.get("ok") is not False:
            for m in data.get("messages") or []:
                if m.get("from_me"):
                    continue
                ev = {
                    "id": f"{m.get('jid','')}:{m.get('sender_jid','')}:{m.get('id') or m.get('timestamp','')}",
                    "name": m.get("name") or m.get("sender_name") or "contato",
                    "chat_name": m.get("chat_name") or m.get("name") or m.get("jid") or "conversa",
                    "jid": m.get("jid"),
                    "sender_jid": m.get("sender_jid"),
                    "text": str(m.get("text") or "")[:1200],
                    "is_group": bool(m.get("is_group")) or str(m.get("jid") or "").endswith("@g.us"),
                    "chat_type": m.get("chat_type") or ("group" if str(m.get("jid") or "").endswith("@g.us") else "private"),
                    "type": m.get("type") or "message",
                    "media_kind": m.get("media_kind") or "",
                    "media_path": m.get("media_path") or "",
                    "timestamp": m.get("timestamp") or int(time.time()),
                    "created_at": datetime.now().isoformat(),
                }
                add_ev(ev)
    except Exception:
        pass

    return out[:max(1, int(limit or 10))]


def whatsapp_action(parameters: dict, player=None) -> str:
    params = parameters or {}
    action = str(params.get("action", "send_text")).lower().strip()
    to = str(params.get("to") or params.get("receiver") or params.get("contact") or params.get("target") or params.get("group") or params.get("name") or "").strip()
    message = str(
        params.get("message")
        or params.get("message_text")
        or params.get("text")
        or params.get("body")
        or params.get("content")
        or params.get("conteudo")
        or params.get("conteúdo")
        or ""
    ).strip()
    result = ""
    try:

        # TRAVA ABSOLUTA: se existe rascunho de agendamento, nenhuma tentativa de envio normal
        # pode sair na hora. Ela vira complemento do rascunho e volta pedindo confirmação.
        if action in {"send_text", "text", "send", "send_multiple_texts", "send_text_batch", "send_batch", "send_audio", "audio", "voice", "send_file", "file", "media", "image", "video", "document", "arquivo", ""}:
            pending = _load_pending_schedule()
            if pending and (pending.get("to") or pending.get("message") or pending.get("schedule_at") or pending.get("date") or pending.get("time") or pending.get("ready")):
                raw = str(params.get("raw_text") or params.get("text") or message or to or "")
                if (not _is_schedule_negative_confirmation(raw)) and _compact_schedule_raw(raw) in {"confirmo", "confirmado", "sim", "podeconfirmar", "podeagendar"}:
                    action = "confirm_schedule"
                else:
                    action = "schedule_message"
                    params = {**params, "action": "schedule_message", "to": to, "message": message, "raw_text": raw}

        if action in {"schedule_message", "agendar_mensagem", "schedule_whatsapp", "agendar_whatsapp"}:
            # Segurança: schedule_message apenas prepara/solicita confirmação. Nunca envia e nunca agenda definitivo.
            if not params.get("file_path") and player is not None:
                try:
                    cur_file = str(getattr(player, "current_file", "") or "").strip()
                    raw_for_media = " ".join(str(x or "") for x in [params.get("raw_text"), params.get("message"), params.get("caption"), params.get("type"), params.get("media_type")])
                    if cur_file and _guess_schedule_media_type(raw_for_media, cur_file) != "text":
                        params["file_path"] = cur_file
                except Exception:
                    pass
            schedule_result = schedule_whatsapp_message(
                to=to,
                message=message,
                schedule_at=str(params.get("schedule_at") or params.get("datetime") or params.get("when") or ""),
                date=str(params.get("date") or ""),
                time_text=str(params.get("time") or params.get("hour") or ""),
                confirm=False,
                raw_text=str(params.get("raw_text") or params.get("text") or ""),
                file_path=str(params.get("file_path") or params.get("path") or ""),
                media_type=str(params.get("media_type") or params.get("type") or ""),
                caption=str(params.get("caption") or ""),
            )
            result = "AGENDAMENTO_PREPARADO_OK: " + schedule_result
        elif action in {"confirm_schedule", "confirmar_agendamento"}:
            confirm_result = confirm_pending_schedule()
            if confirm_result.startswith("Mensagem agendada"):
                result = "AGENDAMENTO_CONFIRMADO_OK: " + confirm_result
            else:
                result = confirm_result
        elif action in {"list_schedules", "listar_agendamentos", "mensagens_agendadas"}:
            result = list_scheduled_messages()
        elif action in {"cancel_schedule", "cancelar_agendamento", "cancel_pending_schedule", "cancelar_preparacao_agendamento"}:
            if _load_pending_schedule() and not str(params.get("schedule_id") or params.get("id") or "").strip():
                result = clear_pending_schedule()
            else:
                result = cancel_scheduled_message(str(params.get("schedule_id") or params.get("id") or ""))
        elif action in {"agent_load_knowledge", "carregar_conhecimento_agente", "load_agent_file"}:
            file_path = str(params.get("file_path") or params.get("path") or "").strip()
            if not file_path and player is not None:
                try: file_path = str(player.current_file or "").strip()
                except Exception: file_path = ""
            if not file_path:
                result = "Selecione ou envie um arquivo TXT com as informações da empresa para carregar o agente."
            else:
                result = agent_load_knowledge(file_path)
        elif action in {"agent_enable", "ativar_agente", "enable_agent", "agent_start"}:
            file_path = str(params.get("file_path") or params.get("path") or "").strip()
            if not file_path and player is not None:
                try: file_path = str(player.current_file or "").strip()
                except Exception: file_path = ""
            result = agent_enable(str(params.get("mode") or "private"), str(params.get("contact") or params.get("target") or to or ""), file_path, bool(params.get("notify", False)))
        elif action in {"agent_disable", "desativar_agente", "disable_agent", "agent_stop"}:
            result = agent_disable()
        elif action in {"agent_status", "status_agente"}:
            result = agent_status()

        elif action in {"disconnect", "disconnect_whatsapp", "desconectar", "desconectar_whatsapp", "logout", "sair_whatsapp", "encerrar_sessao", "encerrar_whatsapp"}:
            result = disconnect_whatsapp_session()
        elif action in {"clean_session", "limpar_sessao", "reset_session", "reset_whatsapp", "limpar_whatsapp"}:
            result = clean_whatsapp_session()
        elif action in {"start", "connect", "qr"}:
            result = connect_whatsapp_from_voice(player)
        elif action in {"status", "check", "connected", "is_connected"}:
            # Ponte desligada NAO quer dizer WhatsApp desconectado: liga,
            # espera a sessao salva reconectar (ate 12 s) e so entao responde.
            if not is_bridge_alive() and _sessao_salva():
                start_bridge()
                _wait_for_whatsapp_connection(12.0)
            result = format_status()
        elif action in {"last_message", "latest_message", "last", "ultima_mensagem"}:
            result = latest_message(str(params.get("direction") or "incoming"), to)
        elif action in {"recent_messages", "messages", "historico", "ultimas_mensagens"}:
            result = recent_messages(int(params.get("limit") or 5), str(params.get("direction") or "all"), to)
        elif action in {"transcribe_audio", "transcribe_last_audio", "transcrever_audio", "conteudo_audio", "audio_content", "ouvir_audio"}:
            result = transcribe_last_audio(to or message)
        elif action in {"find_contact", "search_contact", "contact", "contacts", "buscar_contato"}:
            result = find_contact(to or message)
        elif action in {"groups", "list_groups", "grupos", "listar_grupos"}:
            result = list_groups()
        elif action in {"watch_messages", "alert_messages", "notify_messages", "avisar_mensagens", "monitor_messages", "watch_whatsapp"}:
            mode = str(params.get("mode") or params.get("scope") or "").lower().strip()
            contact = str(params.get("contact") or params.get("name") or to or "").strip()
            spoken = _norm_mode_text(" ".join(str(x or "") for x in [mode, contact, message]))
            if not mode:
                if any(x in spoken for x in ["somente grupos", "so grupos", "apenas grupos", "qualquer grupo", "todos os grupos"]):
                    mode = "groups"
                elif any(x in spoken for x in ["somente privado", "so privado", "apenas privado", "contatos privados", "mensagem privada"]):
                    mode = "private"
                elif contact:
                    mode = "group" if "grupo" in spoken else "contact"
                else:
                    mode = "any"
            # If the model accidentally sends mode=contact/group without a target for a broad request,
            # keep it useful instead of throwing an error.
            if mode in {"contact", "contacts", "contato", "group", "grupo"} and not contact:
                if any(x in spoken for x in ["qualquer", "todos", "todas", "alguem", "qualquer pessoa", "grupo ou privado", "grupos ou privado"]):
                    mode = "any"
            result = watch_whatsapp_messages(mode, contact)
        elif action in {"stop_watch", "stop_alerts", "desativar_alertas", "parar_alertas"}:
            result = watch_whatsapp_messages("off", "")
        elif action in {"watch_status", "alert_status", "status_alertas"}:
            result = watch_whatsapp_status()
        elif action in {"leave_group", "sair_grupo", "sair_do_grupo", "exit_group", "group_leave", "confirm_leave_group", "confirmar_saida_grupo"}:
            confirm = bool(params.get("confirm") or params.get("confirmed") or action in {"confirm_leave_group", "confirmar_saida_grupo"} or str(params.get("confirmation") or "").lower() in {"sim", "confirmo", "confirmado", "true"})
            result = leave_group(to or message, confirm)
        elif action in {"refresh_aliases", "auto_alias", "sincronizar_nomes", "atualizar_apelidos"}:
            result = refresh_aliases_from_messages()
        elif action in {
            "delete_last_sent", "delete_sent_messages", "delete_messages", "delete",
            "apagar_ultima", "apagar_ultima_enviada", "apagar_ultimas",
            "apagar_ultimas_enviadas", "apagar_mensagens",
        }:
            raw_delete_text = " ".join(str(params.get(key) or "") for key in ("raw_text", "message", "text"))
            count_value = (
                params.get("count")
                if params.get("count") is not None
                else params.get("limit", params.get("quantity", params.get("quantidade")))
            )
            count = _coerce_delete_count(count_value, raw_delete_text)
            result = delete_sent_messages(to, count, raw_delete_text)
        elif action in {"save_contact", "save_alias", "contact_alias", "salvar_contato"}:
            name = str(params.get("name") or params.get("alias") or to or "").strip()
            phone = str(params.get("phone") or params.get("number") or message or "").strip()
            result = save_contact_alias(name, phone)
        elif action in {"send_multiple_texts", "send_text_batch", "send_batch", "enviar_varias_mensagens", "enviar_mensagens_separadas"}:
            raw_messages = params.get("messages") or params.get("texts") or params.get("items") or []
            if isinstance(raw_messages, str):
                raw_messages = [part.strip() for part in re.split(r"\n+|\s*\|\s*", raw_messages) if part.strip()]
            if not isinstance(raw_messages, list):
                raw_messages = []
            if message and not raw_messages:
                raw_messages = [message]
            result = send_whatsapp_text_batch(to, [str(item) for item in raw_messages])
        elif action in {"send_file", "file", "media", "send_media", "image", "video", "document", "arquivo", "send_folder", "folder", "pasta", "zip_and_send", "compactar_enviar", "zipar_enviar"}:
            file_path = _media_path_from_whatsapp_params(params, message)
            if not file_path and player is not None:
                try:
                    file_path = str(player.current_file or "").strip()
                except Exception:
                    file_path = ""
            caption = str(params.get("caption") or "").strip()
            if not to or not file_path:
                result = "Para enviar arquivo, preciso do contato e do nome/caminho do arquivo, imagem, vídeo, documento ou pasta."
            else:
                result = send_whatsapp_file(to, file_path, caption)
        elif action in {"send_audio", "audio", "voice"}:
            if not to or not message:
                result = "Recipient and message are required for WhatsApp audio."
            else:
                result = send_whatsapp_audio(to, message)
        elif action in {"send_text", "text", "send", "message", "mensagem", "enviar_mensagem", "mandar_mensagem", ""}:
            if not to or not message:
                result = "Recipient and message are required for WhatsApp text."
            else:
                result = send_whatsapp_text(to, message)
        else:
            if not to or not message:
                result = f"Ação WhatsApp desconhecida ou incompleta: {action}. Recipient and message are required for WhatsApp text."
            else:
                result = send_whatsapp_text(to, message)
    except Exception as e:
        result = f"AÇÃO WHATSAPP FALHOU: {e}. A mensagem/ação NÃO foi concluída. Clique em CONECTAR WHATSAPP para reiniciar a ponte e tente novamente."
    if player:
        player.write_log(f"[whatsapp] {result}")
    return result
