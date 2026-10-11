"""Tarefa por comando: o Codex escreve, o PC do cliente executa.

Desenho pedido pelo Diego em 30/08/2026, e a razao dele e boa:

- a conversa fica 100% no OpenRouter (rapido, 1 a 3 s);
- a TAREFA vai para o Codex na VPS, que escreve um comando de PowerShell;
- o comando roda aqui, na maquina do cliente.

Por que isso e melhor do que 28 ferramentas escritas a mao:

1. **Fichas.** As 28 declaracoes ocupavam ~6.000 fichas em TODO pedido, e a
   conta gratis do OpenRouter tem teto (chegou a 5.900). Uma ferramenta so
   ocupa ~100. Sobra espaco para a conversa.
2. **Alcance.** Ferramenta escrita a mao so faz o que foi previsto. Um comando
   faz qualquer coisa que o Windows faz — e quando falta algo, nao e preciso
   programar uma ferramenta nova.
3. **Verdade.** O comando devolve saida e codigo de retorno. Da para dizer o que
   ACONTECEU, em vez de o modelo inventar que fez — que foi o defeito que mais
   irritou o Diego hoje.

## O freio

Comando gerado por IA rodando na maquina do dono nao pode ir solto. Aqui:

- **lista de proibidos** por padrao de texto (formatar, apagar disco, desligar,
  mexer no registro critico, baixar-e-executar da internet);
- **so PowerShell**, sem `cmd /c` encadeado nem `Invoke-Expression` de texto
  vindo da rede;
- **prazo** de 90 s: comando pendurado nao trava o JARVIS;
- **tudo aparece no registro**, entao da para auditar o que rodou.

O freio e por PADRAO, nao por lista de permitidos: lista de permitidos seria
mais segura, mas impediria justamente o "qualquer coisa" que da valor a ideia.
E uma escolha consciente, e por isso o registro importa.
"""
from __future__ import annotations

import re
import subprocess

PRAZO_S = 90

# Padroes que NUNCA rodam, por mais que o modelo insista. Cada um aqui existe
# porque o estrago seria irreversivel ou deixaria a maquina inutilizavel.
PROIBIDOS = [
    # CUIDADO COM O \bformat\b SOLTO: ele casava com Format-Table,
    # Format-List e Format-Hex, que sao inofensivos e comuns. Em 30/08 o freio
    # recusou "quanta memoria RAM esta sendo usada" e "quais processos consomem
    # mais CPU" alegando "formatar disco". Falso positivo assim e pior que
    # inutil: ensina o cliente a nao confiar no aviso.
    (r"\bformat-volume\b|\bformat\s+[a-z]:", "formatar disco"),
    (r"\bremove-item\b[^|]*\b[a-z]:\\\s*(-recurse|$)", "apagar a raiz de um disco"),
    (r"\bremove-item\b[^|]*(windows|system32|program files)", "apagar pasta do sistema"),
    (r"\brd\b\s+/s|\brmdir\b\s+/s.*\b[a-z]:\\?\s*$", "apagar disco inteiro"),
    (r"\bdel\b\s+/[sf].*\b[a-z]:\\\*", "apagar disco inteiro"),
    (r"\bstop-computer\b|\bshutdown\b|\brestart-computer\b", "desligar ou reiniciar o PC"),
    (r"\bclear-disk\b|\bdiskpart\b", "mexer nas particoes"),
    (r"\bset-itemproperty\b[^|]*hklm:", "alterar o registro do sistema"),
    (r"\bnew-itemproperty\b[^|]*hklm:", "alterar o registro do sistema"),
    (r"\breg\b\s+(delete|add)\b", "alterar o registro do sistema"),
    (r"\binvoke-expression\b|\biex\b\s*\(", "executar texto como codigo"),
    (r"\b(irm|iwr|invoke-webrequest|curl|wget)\b[^|]*\|\s*(iex|invoke-expression)",
     "baixar da internet e executar"),
    (r"\bstart-process\b[^|]*\.(exe|msi|bat|cmd)\b[^|]*-verb\s+runas", "executar como administrador"),
    (r"\bnet\b\s+user\b.*\b/add\b", "criar usuario do Windows"),
    (r"\bdisable-\w*(firewall|defender)", "desligar a protecao do Windows"),
    (r"\bset-mppreference\b", "desligar o antivirus"),
    (r"\bcipher\b\s+/w", "apagar espaco livre de forma irreversivel"),
    (r"\bvssadmin\b.*delete", "apagar os pontos de restauracao"),
]

# Nunca encerrar estes por comando: derruba o Windows ou o proprio JARVIS.
PROIBIDOS.append(
    (r"\bstop-process\b[^|]*\b(explorer|winlogon|csrss|lsass|services|jarvis|pythonw)\b",
     "encerrar processo critico do sistema ou o proprio JARVIS")
)


# ── LINUX (26/09/2026, cliente do Big Linux) ───────────────────────────────
#
# O cerebro escrevia PowerShell tambem no Linux, e o `powershell` nem existe
# la: toda tarefa morria em "FileNotFoundError". No Linux o pedido e de UM
# comando bash, e o freio tem a mesma ideia do de cima com os perigos de la.
import sys as _sys

LINUX = _sys.platform.startswith("linux")

PROIBIDOS_LINUX = [
    (r"\brm\s+(-\w*\s+)*-\w*[rf]\w*\s+(-\w+\s+)*(/|/\*|~|~/|\$home|/home|/usr|/etc|/boot|/var|/bin|/lib)(\s|$|/?\*)",
     "apagar pasta do sistema ou a casa inteira"),
    (r"\bmkfs\b|\bwipefs\b|\bfdisk\b|\bparted\b|\bsgdisk\b", "mexer nas particoes"),
    (r"\bdd\b[^|;]*\bof=/dev/", "gravar direto no disco"),
    (r">\s*/dev/(sd|nvme|hd)", "gravar direto no disco"),
    (r"\b(shutdown|poweroff|reboot|halt)\b|systemctl\s+(poweroff|reboot|halt|suspend|hibernate)",
     "desligar ou reiniciar o PC"),
    (r"\b(sudo|su|pkexec|doas)\b", "executar como administrador"),
    (r"\bchmod\b[^|;]*\s-r\w*\s+[0-7]*\s*/(\s|$)|\bchown\b[^|;]*\s-r\w*[^|;]*\s/(\s|$)",
     "mudar permissao do sistema inteiro"),
    (r":\(\)\s*\{", "travar o computador"),
    (r"\b(curl|wget)\b[^|;]*\|\s*(ba|z|da)?sh\b", "baixar da internet e executar"),
    (r"\beval\b", "executar texto como codigo"),
    (r"\b(userdel|useradd|usermod|passwd|chpasswd)\b", "mexer nas contas do sistema"),
    (r"\b(iptables|nft|ufw|firewall-cmd)\b", "mexer no firewall"),
    (r"\b(pkill|killall|kill)\b[^|;]*\b(plasmashell|kwin\w*|sddm|systemd|pipewire|wireplumber|xwayland|xorg|python\w*|jarvis)",
     "encerrar processo critico do sistema ou o proprio JARVIS"),
    (r"\bkill\s+(-\d+\s+)?(-1|1)\b", "encerrar todos os processos"),
    (r"\bsystemctl\s+(--user\s+)?(stop|disable|mask)\b", "desligar servico do sistema"),
    (r"\bpacman\s+-r|\bpamac\s+remove|\bapt(-get)?\s+(remove|purge)", "desinstalar programa"),
]


def revisar(comando: str) -> tuple[bool, str]:
    """Diz se o comando pode rodar. Devolve (liberado, motivo)."""
    texto = " ".join(str(comando or "").split()).lower()
    if not texto:
        return False, "comando vazio"
    if len(texto) > 4000:
        return False, "comando longo demais"
    for padrao, motivo in (PROIBIDOS_LINUX if LINUX else PROIBIDOS):
        if re.search(padrao, texto):
            return False, motivo
    return True, ""


def _limpar_resposta(bruto: str) -> str:
    """Tira cerca de codigo e conversa fiada, deixando so o comando."""
    texto = str(bruto or "").strip()
    # O modelo costuma devolver em bloco ```powershell ... ```
    m = re.search(r"```(?:powershell|ps1|cmd|bat|bash|sh|shell|zsh)?\s*(.+?)```",
                  texto, re.S | re.I)
    if m:
        texto = m.group(1)
    linhas = [l.rstrip() for l in texto.splitlines()]
    # Fora linhas de explicacao em prosa e comentarios soltos.
    uteis = [l for l in linhas if l.strip() and not l.strip().startswith("#")]
    if not uteis:
        return ""
    # SO A PRIMEIRA LINHA. Antes daqui saiam todas coladas com quebra de linha,
    # e bastava o modelo emendar uma frase para o PowerShell engasgar (medido em
    # 30/08/2026: um "express \'GB livres\'" no fim derrubou a tarefa do disco C).
    # Continua nas linhas seguintes so quando a atual claramente pede emenda.
    comando = [uteis[0]]
    for anterior, atual in zip(uteis, uteis[1:]):
        emenda = anterior.rstrip().endswith(("|", "`", ";", "{", "(", ",", "\\", "&&"))
        emenda = emenda or atual.lstrip().startswith(("|", "}", ")"))
        if not emenda:
            break
        comando.append(atual)
    return "\n".join(comando).strip()


def executar(comando: str) -> dict:
    """Roda o comando no PowerShell, com prazo. Devolve o que aconteceu."""
    liberado, motivo = revisar(comando)
    if not liberado:
        return {"ok": False, "recusado": True, "motivo": motivo,
                "saida": "", "comando": comando}
    if LINUX:
        try:
            try:  # sem o QT_QPA_PLATFORM=xcb do JARVIS (ver open_app)
                from actions.open_app import _ambiente_do_programa  # noqa: PLC0415
                env = _ambiente_do_programa()
            except Exception:
                env = None
            import os  # noqa: PLC0415
            r = subprocess.run(["bash", "-c", comando], capture_output=True, text=True,
                               timeout=PRAZO_S, env=env, cwd=os.path.expanduser("~"),
                               stdin=subprocess.DEVNULL)
            saida = (r.stdout or "").strip()
            erro = (r.stderr or "").strip()
            return {"ok": r.returncode == 0, "recusado": False, "codigo": r.returncode,
                    "saida": (saida or erro)[:3000], "comando": comando}
        except subprocess.TimeoutExpired:
            return {"ok": False, "recusado": False, "motivo": f"passou de {PRAZO_S}s",
                    "saida": "", "comando": comando}
        except Exception as exc:
            return {"ok": False, "recusado": False,
                    "motivo": f"{type(exc).__name__}: {str(exc)[:150]}",
                    "saida": "", "comando": comando}
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", comando],
            capture_output=True, text=True, timeout=PRAZO_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        saida = (r.stdout or "").strip()
        erro = (r.stderr or "").strip()
        # O PowerShell devolve codigo 0 mesmo tendo IMPRIMIDO erro (erro
        # nao-terminante). Sem esta linha, "Expand-Archive : o caminho nao
        # existe" virava sucesso e o JARVIS respondia "pronto" (medido na
        # bateria de 30/08). Regra conservadora: so e falha quando nao veio
        # NADA de saida e veio erro — assim aviso de rotina nao derruba
        # comando que funcionou.
        return {
            "ok": r.returncode == 0 and not (erro and not saida),
            "recusado": False,
            "codigo": r.returncode,
            "saida": (saida or erro)[:3000],
            "comando": comando,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "recusado": False, "motivo": f"passou de {PRAZO_S}s",
                "saida": "", "comando": comando}
    except Exception as exc:
        return {"ok": False, "recusado": False,
                "motivo": f"{type(exc).__name__}: {str(exc)[:150]}",
                "saida": "", "comando": comando}


PEDIDO_AO_CODEX = (
    "Voce escreve UM comando de PowerShell para Windows 11 que realize a tarefa "
    "abaixo na maquina do usuario.\n"
    "\n"
    "REGRAS:\n"
    "- Responda APENAS com o comando, sem explicacao, sem cerca de codigo, sem "
    "comentario.\n"
    "- Um comando so. Pode usar ponto e virgula ou pipe, mas nada de script de "
    "varias linhas com funcao.\n"
    "- NAO use nada destrutivo: formatar, apagar disco, apagar pasta do Windows, "
    "desligar o PC, mexer no registro do sistema, desligar antivirus ou firewall.\n"
    "- Prefira o caminho mais simples e direto.\n"
    "- Caminhos do usuario ficam em $env:USERPROFILE (Desktop, Documents, etc).\n"
    "- Se a tarefa devolver dados, faca o comando IMPRIMIR o resultado.\n"
    # Tabela do PowerShell derruba a resposta pela metade: cada tipo de objeto
    # sai com o seu proprio cabecalho, e em 31/08/2026 o modelo leu o primeiro
    # bloco (CPU) e largou RAM e disco. Uma linha "rotulo: valor" por item nao
    # tem como passar batido.
    "- IMPRIMA EM LINHAS ROTULADAS, uma por item, no formato 'Rotulo: valor' "
    "(ex.: Write-Output \"CPU: 10%\"). NADA de tabela, Format-Table ou "
    "Select-Object solto com varias colunas.\n"
    "- Se o pedido tiver VARIAS partes (CPU, memoria e disco, por exemplo), "
    "o MESMO comando tem de trazer TODAS elas, cada uma na sua linha.\n"
    # Sem isto o Compress-Archive falha quando o zip ja existe, e o cliente
    # ve 'erro' numa tarefa que so precisava sobrescrever (medido em 30/08).
    "- Se o destino ja existir, SOBRESCREVA: use -Force no Compress-Archive, "
    "no Expand-Archive, no Copy-Item e no New-Item.\n"
    "- Crie a pasta de destino se ela nao existir.\n"
    "\n"
    "TAREFA: "
)

# O sistema de quem escreve o comando (usado pelo cerebro_openrouter).
SISTEMA_DO_COMANDO = (
    "Voce escreve comandos de PowerShell para Windows 11. Responda SO com o "
    "comando, uma linha, sem explicacao e sem cerca de codigo.")

if LINUX:
    SISTEMA_DO_COMANDO = (
        "Voce escreve comandos bash para Linux (BigLinux, baseado em Manjaro/Arch, "
        "com KDE Plasma 6). Responda SO com o comando, uma linha, sem explicacao "
        "e sem cerca de codigo.")
    PEDIDO_AO_CODEX = (
        "Voce escreve UM comando bash para Linux (BigLinux, baseado em Manjaro/Arch, "
        "com KDE Plasma 6) que realize a tarefa abaixo na maquina do usuario.\n"
        "\n"
        "REGRAS:\n"
        "- Responda APENAS com o comando, sem explicacao, sem cerca de codigo, sem "
        "comentario.\n"
        "- Um comando so. Pode usar ; && e pipe, mas nada de script de varias "
        "linhas com funcao.\n"
        "- SEM sudo, su ou pkexec: o comando roda como o usuario comum.\n"
        "- NAO use nada destrutivo: apagar pasta do sistema ou a casa inteira, "
        "formatar, mexer em particao, desligar o PC, desinstalar programa, mexer "
        "no firewall.\n"
        "- Pastas do usuario: use $(xdg-user-dir DESKTOP), $(xdg-user-dir DOWNLOAD), "
        "$(xdg-user-dir DOCUMENTS) etc., ou $HOME.\n"
        "- Para abrir arquivo, pasta ou site use xdg-open; programa grafico abre "
        "solto com setsid NOME >/dev/null 2>&1 &.\n"
        "- Programas do KDE: dolphin (arquivos), konsole (terminal), kate (texto), "
        "systemsettings (configuracoes), spectacle (captura de tela).\n"
        "- Volume: pactl; informacoes: free -h, df -h, lscpu, uptime, ip addr.\n"
        "- Se a tarefa devolver dados, faca o comando IMPRIMIR o resultado em "
        "linhas rotuladas, uma por item, no formato 'Rotulo: valor'.\n"
        "- Se o pedido tiver VARIAS partes, o MESMO comando traz TODAS.\n"
        "- Se o destino ja existir, SOBRESCREVA; crie a pasta de destino se "
        "ela nao existir (mkdir -p).\n"
        "\n"
        "TAREFA: "
    )
