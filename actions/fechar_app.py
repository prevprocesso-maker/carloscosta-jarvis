"""Fechar programas pelo nome.

Criado em 30/08/2026. O JARVIS tinha `open_app` para ABRIR e **nada** para
fechar. O Diego pedia "fecha o bloco de notas", nenhuma ferramenta era chamada,
e o modelo respondia "Fechado" — mentira, porque sem ferramenta ele inventa que
fez em vez de dizer que nao consegue.

Fecha com educacao primeiro (pede para a janela fechar, como clicar no X) e so
usa forca se o programa ignorar. Assim o programa salva o que precisa salvar.
"""
from __future__ import annotations

import time

# Nunca encostar nestes: derrubar qualquer um deixa o Windows quebrado ou o
# proprio JARVIS mudo. A lista e por prefixo do nome do processo, minusculo.
PROTEGIDOS = (
    "system", "idle", "csrss", "wininit", "winlogon", "services", "lsass",
    "smss", "svchost", "dwm", "explorer", "fontdrvhost", "sihost", "ctfmon",
    "registry", "memory compression", "audiodg", "runtimebroker",
    "jarvis-cyberbot", "pythonw", "python",
    # O MESMO CUIDADO NO MAC (05/09/2026). Sem estes, um "fecha o localizador"
    # derrubaria o Finder, a barra ou a propria sessao do cliente — o
    # equivalente exato de matar o `explorer.exe` no Windows.
    "finder", "dock", "windowserver", "loginwindow", "systemuiserver",
    "coreaudiod", "launchd", "kernel_task", "controlcenter", "spotlight",
)

# Nomes falados x processo de verdade. Sem isto, "bloco de notas" nao casa com
# `notepad.exe` e o JARVIS diz que nao achou o que esta bem na frente dele.
APELIDOS = {
    "bloco de notas": ["notepad", "notepad++"],
    "notepad": ["notepad", "notepad++"],
    "gerenciador de tarefas": ["taskmgr"],
    "gerenciador": ["taskmgr"],
    "calculadora": ["calculatorapp", "calc", "win32calc"],
    "navegador": ["chrome", "msedge", "firefox"],
    "chrome": ["chrome"],
    "google chrome": ["chrome"],
    "edge": ["msedge"],
    "firefox": ["firefox"],
    "explorador de arquivos": ["explorer"],
    "paint": ["mspaint"],
    "spotify": ["spotify"],
    "discord": ["discord"],
    "steam": ["steam"],
    "whatsapp": ["whatsapp"],
    "word": ["winword"],
    "excel": ["excel"],
    "vscode": ["code"],
    "visual studio code": ["code"],
    "obs": ["obs64", "obs32"],
    "audiorelay": ["audiorelay", "audiorelay-backend"],
}


def _normalizar(texto: str) -> str:
    import re
    import unicodedata
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"\.exe$", "", t)).strip()


def _alvos(nome: str) -> list[str]:
    limpo = _normalizar(nome)
    if not limpo:
        return []
    if limpo in APELIDOS:
        return APELIDOS[limpo]
    # Apelido que aparece dentro da frase ("fecha o meu bloco de notas ai").
    for apelido, processos in APELIDOS.items():
        if apelido in limpo:
            return processos
    return [limpo]


# ── LINUX (26/09/2026, cliente do Big Linux) ───────────────────────────────
#
# Duas falhas no log do cliente: "close the File Manager" nao achava nada (o
# processo se chama `dolphin`), e "close Chrome Dolphin" FECHOU O CHROME — o
# `pnome in alvo` casava "chrome" dentro de "chrome dolphin". No Linux o alvo
# agora vem do nome generico, do atalho do menu (o mesmo que o open_app usa) e
# das palavras exatas; e se casarem programas diferentes, fecha so o aberto
# por ultimo, que e o que o dono acabou de pedir para abrir.
_PROTEGIDOS_LINUX = {
    "plasmashell", "kwin_wayland", "kwin_x11", "xwayland", "xorg", "systemd", "sddm",
    "pipewire", "pipewire-pulse", "wireplumber", "dbus-daemon", "dbus-broker",
    "kded6", "kded5", "ksmserver", "krunner", "kglobalacceld",
    "polkit-kde-authentication-agent-1", "python", "python3", "bash", "sh", "sshd",
    "tailscaled", "xdg-desktop-portal", "kactivitymanagerd", "baloo_file",
    "org_kde_powerdevil", "kioworker", "kiod6",
}

_GRUPOS_LINUX = {
    ("file manager", "files", "file explorer", "explorer", "folder", "downloads",
     "download", "documents", "desktop", "pictures", "music", "videos", "pasta",
     "arquivos", "gerenciador arquivos", "dolphin"):
        ["dolphin", "nautilus", "nemo", "thunar", "pcmanfm"],
    ("terminal", "console", "konsole"): ["konsole", "gnome-terminal-server", "kgx",
                                         "xfce4-terminal", "tilix", "alacritty", "kitty",
                                         "xterm"],
    ("calculator", "calculadora", "calc"): ["kcalc", "gnome-calculator", "qalculate-qt",
                                             "qalculate-gtk", "galculator", "speedcrunch"],
    ("text editor", "notepad", "editor", "bloco notas", "kate", "kwrite"):
        ["kate", "kwrite", "gnome-text-editor", "gedit", "mousepad", "xed"],
    ("settings", "system settings", "configuracoes"): ["systemsettings",
                                                       "gnome-control-center"],
    ("task manager", "system monitor"): ["plasma-systemmonitor", "ksysguard",
                                         "gnome-system-monitor"],
    ("pdf", "okular"): ["okular", "evince", "atril"],
    ("image viewer", "gwenview", "photos"): ["gwenview", "eog", "loupe"],
    ("vivaldi",): ["vivaldi-bin"],
    ("chrome", "google chrome"): ["chrome"],
    ("brave",): ["brave"],
    ("firefox",): ["firefox"],
    ("screenshot", "spectacle"): ["spectacle"],
}


def _navegador_padrao_linux() -> list[str]:
    """Os processos do navegador padrao ("close the browser")."""
    import subprocess  # noqa: PLC0415
    try:
        r = subprocess.run(["xdg-settings", "get", "default-web-browser"],
                           capture_output=True, text=True, timeout=5)
        d = r.stdout.strip().lower()
    except Exception:
        d = ""
    for chave, procs in (("vivaldi", ["vivaldi-bin"]), ("brave", ["brave"]),
                         ("chrome", ["chrome"]), ("chromium", ["chromium"]),
                         ("firefox", ["firefox"]), ("edge", ["msedge"])):
        if chave in d:
            return procs
    return ["vivaldi-bin", "brave", "chrome", "firefox"]


def _alvos_linux(nome: str) -> tuple[list[str], list[str], list[str]]:
    """(nomes de processo exatos, pedacos da linha de comando, scripts) do nome.

    `scripts`: os programas do Big Linux que sao script (a Big Store e
    `bash /usr/sbin/big-store` rodando o BigBashView por baixo) — o processo
    se chama "bash", entao o que identifica e o nome do script na linha."""
    import os  # noqa: PLC0415
    import shlex  # noqa: PLC0415
    from actions.open_app import _chave_linux, _achar_atalho, _ler_atalho  # noqa: PLC0415
    chave = _chave_linux(nome)
    nomes: list[str] = []
    linhas: list[str] = []
    scripts: list[str] = []
    if chave in ("browser", "web browser", "internet", "navegador", "web"):
        nomes += _navegador_padrao_linux()
    for grupo, procs in _GRUPOS_LINUX.items():
        if chave in grupo:
            nomes += procs
    if not nomes:
        atalho = _achar_atalho(nome)
        if atalho is not None:
            if "__" in atalho.stem:
                # web app do Big Linux: cada um roda com o proprio perfil, e o
                # nome do atalho esta no --user-data-dir
                linhas.append(atalho.stem)
            else:
                try:
                    partes = shlex.split((_ler_atalho(atalho) or {}).get("Exec", ""))
                except ValueError:
                    partes = []
                if partes:
                    if os.path.basename(partes[0]) == "flatpak":
                        # o id do app e o ultimo pedaco com dois pontos, sem "--"
                        linhas += [p for p in partes
                                   if p.count(".") >= 2 and not p.startswith("-")][-1:]
                    else:
                        # "SoftwareRender big-store": o programa e o 2o pedaco
                        if os.path.basename(partes[0]).lower() == "softwarerender" and len(partes) > 1:
                            partes = partes[1:]
                        base = os.path.basename(partes[0])
                        nomes += [base, base + "-bin"]
                        scripts.append(base)
    for palavra in chave.split():
        if len(palavra) >= 3:
            nomes.append(palavra)
    return list(dict.fromkeys(nomes)), linhas, scripts


def _fechar_no_linux(nome: str) -> str:
    import os  # noqa: PLC0415
    import psutil  # noqa: PLC0415
    nomes, linhas, scripts = _alvos_linux(nome)
    if not nomes and not linhas and not scripts:
        return f"Nao sei qual programa e {nome}."
    eu, uid = os.getpid(), os.getuid()
    achados = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "uids", "create_time", "ppid"]):
        try:
            info = proc.info
            if info["pid"] == eu or (info.get("uids") and info["uids"].real != uid):
                continue
            pnome = str(info.get("name") or "").lower()
            partes = info.get("cmdline") or []
            cmd = " ".join(partes)
            e_script = any(os.path.basename(t) in scripts for t in partes[:2])
            if pnome in _PROTEGIDOS_LINUX and not e_script:
                continue
            if pnome in nomes or e_script or any(pedaco in cmd for pedaco in linhas):
                achados.append(proc)
        except Exception:
            continue
    if not achados:
        return f"Nao encontrei o {nome} aberto, nao havia o que fechar."

    def _app(p):
        partes = p.info.get("cmdline") or []
        for t in partes[:2]:
            if os.path.basename(t) in scripts:
                return os.path.basename(t)
        return str(p.info.get("name") or "").lower()

    # programas diferentes casaram ("Chrome Dolphin"): so o aberto por ultimo
    por_app: dict = {}
    for p in achados:
        por_app.setdefault(_app(p), []).append(p)
    if len(por_app) > 1:
        mais_novo = max(por_app, key=lambda a: max(p.info.get("create_time") or 0
                                                   for p in por_app[a]))
        achados = por_app[mais_novo]
    alvo_nome = _app(achados[0]).replace("-bin", "")
    # so as raizes: o navegador fecha as abas dele sozinho, com educacao
    pids = {p.pid for p in achados}
    raizes = [p for p in achados if p.info.get("ppid") not in pids] or achados
    # os filhos de cada raiz, guardados ANTES: quem roda pendurado num script
    # (o BigBashView da Big Store) fica orfao e aberto se so a raiz morrer
    filhos = []
    for p in raizes:
        try:
            filhos += p.children(recursive=True)
        except Exception:
            pass
    for p in raizes:
        try:
            p.terminate()
        except Exception:
            pass
    _, vivos = psutil.wait_procs(raizes, timeout=4.0)
    for p in vivos:
        try:
            p.kill()
        except Exception:
            pass
    sobra = [f for f in filhos if f.is_running()]
    for f in sobra:
        try:
            f.terminate()
        except Exception:
            pass
    _, vivos = psutil.wait_procs(sobra, timeout=3.0)
    for f in vivos:
        try:
            f.kill()
        except Exception:
            pass
    time.sleep(0.5)
    restam = []
    for p in raizes:
        try:
            if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                restam.append(p)
        except Exception:
            pass
    if restam:
        return f"Pedi para o {alvo_nome} fechar, mas ele continua aberto."
    return (f"Fechei o {alvo_nome}." if len(raizes) == 1
            else f"Fechei o {alvo_nome} ({len(raizes)} janelas).")


def fechar_app(parameters: dict | None = None, player=None, **_) -> str:
    """Fecha um programa pelo nome. Devolve o que REALMENTE aconteceu."""
    import psutil  # noqa: PLC0415

    params = parameters or {}
    nome = str(params.get("app_name") or params.get("nome") or params.get("name") or "").strip()
    if not nome:
        return "Faltou dizer qual programa fechar."

    import sys  # noqa: PLC0415
    if sys.platform.startswith("linux"):
        try:
            return _fechar_no_linux(nome)
        except Exception as e:
            return f"Nao consegui fechar o {nome}: {str(e)[:80]}"

    alvos = _alvos(nome)
    achados = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pnome = _normalizar(proc.info.get("name") or "")
            if not pnome or any(pnome.startswith(x) for x in PROTEGIDOS):
                continue
            if any(a in pnome or pnome in a for a in alvos):
                achados.append(proc)
        except Exception:
            continue

    if not achados:
        # NAO ACHEI PROCESSO. ANTES DE DIZER QUE NAO ESTA ABERTO, OLHE A TELA.
        #
        # (05/09/2026, o Diego, com a pasta Downloads aberta na frente dele
        # ouvindo quatro vezes "nao esta aberto": *"quando ouve um erro desse
        # ele tem que verificar se realmente esta fechado ou aberto pra nao
        # ficar inventando"*.)
        #
        # Janela de pasta nao tem processo proprio — ela pertence ao
        # `explorer.exe`, que esta nos PROTEGIDOS para o JARVIS nunca derrubar a
        # area de trabalho. Procurar processo ali era procurar o que nao existe.
        try:
            from actions import janelas_windows as _jw  # noqa: PLC0415
            fechadas, achadas = _jw.fechar_pasta(nome)
            # "pasta de download" ja traz a palavra; "download" sozinho, nao.
            comonome = nome if "pasta" in _normalizar(nome) else f"pasta {nome}"
            if fechadas:
                return (f"Fechei a {comonome}." if fechadas == 1
                        else f"Fechei {fechadas} janelas da {comonome}.")
            if achadas:
                return (f"A {comonome} esta aberta, mas nao quis fechar — "
                        f"deve ter algo pendente nela.")
            titulo = _jw.alguma_janela_com(nome)
            if titulo:
                return (f"Nao achei um programa chamado {nome} para fechar, mas ha "
                        f"uma janela aberta chamada \"{titulo[:60]}\".")
        except Exception:
            pass
        # Sem artigo: serve para "o Chrome" e para "a pasta de download" —
        # antes saia "O pasta de download nao esta aberto", que alem de errado
        # soava a robo.
        return f"Nao encontrei nenhuma janela de {nome} — nao havia o que fechar."

    # Educado primeiro: pede para fechar, como clicar no X. O programa salva o
    # que precisa. So depois de 3 s e que insiste.
    for p in achados:
        try:
            p.terminate()
        except Exception:
            pass
    vivos = []
    try:
        _, vivos = psutil.wait_procs(achados, timeout=3.0)
    except Exception:
        vivos = []
    for p in vivos:
        try:
            p.kill()
        except Exception:
            pass

    time.sleep(0.3)
    # CONFERIR. Dizer "fechei" sem olhar foi exatamente o defeito que esta
    # ferramenta existe para corrigir.
    restantes = 0
    for p in achados:
        try:
            if p.is_running():
                restantes += 1
        except Exception:
            pass

    fechados = len(achados) - restantes
    if fechados and not restantes:
        # "(1 janela(s))" e recado de programador. O dono ouve isso em voz alta.
        return (f"Fechei o {nome}." if fechados == 1
                else f"Fechei o {nome} ({fechados} janelas).")
    if fechados:
        return f"Fechei {fechados} de {len(achados)}; o resto nao quis fechar."
    return f"Nao consegui fechar o {nome} — o Windows recusou."
