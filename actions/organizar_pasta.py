# -*- coding: utf-8 -*-
"""Organiza uma pasta por tipo de arquivo — e DESFAZ, se o dono pedir.

Por que existe (01/09/2026)
---------------------------
"JARVIS, organiza minha pasta Downloads" e o pedido; "pode desfazer" e o que
vem dez segundos depois, quando ele abre a pasta e nao acha o arquivo que
estava na cara. Organizar sem poder voltar e armadilha, entao aqui cada
movimento fica anotado num diario, e desfazer e ler o diario de tras para
frente.

O diario fica em `config/organizacoes/`, um arquivo por organizacao. Desfazer
so mexe no arquivo que continua exatamente onde foi deixado — se o dono ja
moveu ou apagou, esse fica quieto e e relatado.

Ja existia um `organize_desktop` em `file_controller.py`, em ingles, so para a
Area de Trabalho e sem volta. Este substitui aquele para o uso falado.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DIARIOS = BASE / "config" / "organizacoes"

# Nome da subpasta -> extensoes. Em portugues, porque e o dono quem vai abrir.
CATEGORIAS = {
    "Imagens":       {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
                      ".ico", ".heic", ".tif", ".tiff", ".psd", ".raw"},
    "Videos":        {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm",
                      ".m4v", ".mpg", ".mpeg", ".3gp"},
    "Musicas":       {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a",
                      ".opus"},
    "Documentos":    {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".epub",
                      ".md"},
    "Planilhas":     {".xls", ".xlsx", ".csv", ".ods", ".xlsm"},
    "Apresentacoes": {".ppt", ".pptx", ".odp", ".key"},
    "Compactados":   {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
                      ".iso"},
    "Instaladores":  {".exe", ".msi", ".msix", ".apk", ".appx"},
    "Codigos":       {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                      ".cpp", ".c", ".h", ".java", ".cs", ".go", ".rs", ".sh",
                      ".ps1", ".bat", ".sql", ".lua"},
}
OUTROS = "Outros"

# O que NAO se mexe: download pela metade, arquivo de sistema, atalho.
_PULAR_EXT = {".crdownload", ".part", ".tmp", ".partial", ".download", ".lnk",
              ".url", ".ini"}
_PULAR_NOME = {"desktop.ini", "thumbs.db"}


def _falado(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


# ---------------------------------------------------------------------------
# "Essa pasta aqui" — descobrir QUAL e
# ---------------------------------------------------------------------------
# O dono aponta com a boca: "organiza essa pasta que esta aberta". Ate 02/09
# isso simplesmente nao existia — o `_resolver_pasta` recebia "essa pasta" e
# devolvia `C:\Users\...\Desktop\essa pasta`, um caminho inventado que nao
# existe, e o JARVIS respondia "nao achei a pasta". Parecia que a ferramenta
# estava quebrada; ela so estava obedecendo ao pe da letra.
#
# Duas maneiras de descobrir, nesta ordem:
#
#   1. PERGUNTAR AO WINDOWS. O Explorer publica as janelas abertas por COM
#      (`Shell.Application`). E instantaneo, exato, de graca, e devolve o
#      caminho real — sem chute e sem rede.
#   2. OLHAR A TELA. Se nao ha janela do Explorer (a pasta esta dentro de outro
#      programa, ou e uma caixa de dialogo), tira a foto e manda para o Codex
#      na VPS, que le o caminho na barra de endereco. Custa ~5 s e uma ida a
#      rede, por isso e a segunda e nao a primeira.
#
# O que vier dos dois e CONFERIDO no disco antes de valer. Caminho que o Codex
# leu errado da barra de endereco nao pode virar uma organizacao no lugar
# errado.

# As palavras de apontar E as de ligacao. As de ligacao precisam estar aqui
# porque a regra exige que TODAS as palavras da frase sejam conhecidas: sem
# "a" e "que", "a pasta que esta aberta" era reprovada e ia parar no
# `_resolver`, que devolvia None e o dono ouvia "nao achei essa pasta".
# Nenhuma delas e nome de pasta, entao incluir e seguro: "essa pasta Fotos"
# continua caindo fora por causa de "fotos".
_APONTANDO = (
    "essa esta esse este isso isto aqui ali la ai aberta aberto atual "
    "dessa desta nessa nesta mesma corrente pasta janela tela frente "
    "a o as os um uma que de da do dos das em na no nos nas "
    "ta esta estao aberta"
)


def _e_apontando(nome: str) -> bool:
    """A frase aponta para a tela em vez de nomear uma pasta?"""
    import unicodedata as _u
    s = _u.normalize("NFKD", str(nome or "").lower())
    s = "".join(c for c in s if not _u.combining(c))
    palavras = "".join(c if c.isalnum() else " " for c in s).split()
    if not palavras:
        return False
    conhecidas = set(_APONTANDO.split())
    # TODAS as palavras tem de ser de apontar. "essa pasta Fotos" nomeia uma
    # pasta de verdade e nao pode cair aqui.
    return all(w in conhecidas for w in palavras)


def _pasta_do_explorer() -> Path | None:
    """A pasta da janela do Explorer que esta na frente, ou a ultima aberta."""
    try:
        import win32com.client  # noqa: PLC0415
        import win32gui         # noqa: PLC0415
        import urllib.parse     # noqa: PLC0415
    except Exception:
        return None
    try:
        frente = win32gui.GetForegroundWindow()
    except Exception:
        frente = 0
    achadas: list[tuple[bool, Path]] = []
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        for janela in shell.Windows():
            try:
                url = str(janela.LocationURL or "")
                if not url.startswith("file:"):
                    continue          # "Este Computador", Painel de Controle
                caminho = Path(urllib.parse.unquote(url[8:]).replace("/", "\\"))
                if not caminho.is_dir():
                    continue
                na_frente = False
                try:
                    na_frente = int(janela.HWND) == int(frente)
                except Exception:
                    pass
                achadas.append((na_frente, caminho))
            except Exception:
                continue
    except Exception:
        return None
    if not achadas:
        return None
    # A da frente ganha; sem ela, a ultima da lista (a aberta mais recentemente).
    for na_frente, caminho in achadas:
        if na_frente:
            return caminho
    return achadas[-1][1]


def _pasta_pela_foto(player=None, log=None) -> Path | None:
    """Manda a foto da tela ao Codex da VPS e pergunta que pasta e essa."""
    relay = getattr(player, "_codex_relay", None) if player is not None else None
    if relay is None or not hasattr(relay, "pedir_passo_na_tela"):
        return None
    try:
        import secrets  # noqa: PLC0415
        from actions.usar_computador import _foto_da_tela  # noqa: PLC0415
        foto, largura, altura, _lt, _at = _foto_da_tela()
    except Exception as exc:
        if log:
            log(f"[organizar_pasta] nao consegui fotografar a tela: {str(exc)[:90]}")
        return None
    job = secrets.token_hex(8)
    pedido = (
        "Olhe esta foto da tela e diga QUAL PASTA esta aberta nela. Procure a "
        "barra de endereco do Explorer, o titulo da janela ou o caminho escrito "
        "em qualquer lugar. Responda AGORA, num passo so, com "
        "acao=concluido e fala=<o caminho completo da pasta, comecando pela "
        "letra do disco>. Nao clique em nada. Se nao houver pasta nenhuma "
        "visivel na tela, responda acao=falhou."
    )
    try:
        resposta = relay.pedir_passo_na_tela(
            job_id=job, tarefa=pedido, passo=1, jpeg=foto,
            largura=largura, altura=altura, historico=[], timeout=60.0)
    except Exception as exc:
        if log:
            log(f"[organizar_pasta] a VPS nao respondeu: {str(exc)[:90]}")
        return None
    finally:
        try:
            relay.encerrar_passos_na_tela(job)
        except Exception:
            pass
    if not isinstance(resposta, dict):
        return None
    dito = str(resposta.get("fala") or resposta.get("texto") or "").strip()
    if log:
        log(f"[organizar_pasta] o Codex leu na tela: {dito[:90]!r}")
    # CONFERE NO DISCO. O que ele leu e um palpite tirado de pixels; so vira
    # caminho depois que o proprio Windows diz que existe.
    import re as _re  # noqa: PLC0415
    for candidato in _re.findall(r"[A-Za-z]:[\\/][^\n\"'<>|]*", dito) or [dito]:
        try:
            alvo = Path(candidato.strip().strip("\"'.,"))
            if alvo.is_dir():
                return alvo
        except Exception:
            continue
    return None


def _pasta_na_tela(player=None, log=None) -> Path | None:
    achada = _pasta_do_explorer()
    if achada is not None:
        if log:
            log(f"[organizar_pasta] pasta aberta no Explorer: {achada}")
        return achada
    if log:
        log("[organizar_pasta] nenhuma janela do Explorer; vou olhar a tela.")
    return _pasta_pela_foto(player, log)


def _resolver(nome: str) -> Path | None:
    """'downloads', 'area de trabalho', 'pasta Fotos' ou um caminho completo."""
    from actions.codex_remote import _resolver_pasta  # noqa: PLC0415
    bruto = str(nome or "").strip()
    if not bruto:
        return None
    p = _resolver_pasta(bruto)
    if p is None:
        return None
    # SO VALE O QUE EXISTE. O `_resolver_pasta` monta um caminho a partir do
    # que ouviu, e para texto que nao e nome de pasta ele monta bobagem —
    # "essa pasta" virava `Desktop\essa pasta`. Um caminho inventado passava
    # daqui e so morria la na frente, com "nao achei a pasta", que faz o dono
    # achar que a ferramenta esta quebrada.
    try:
        if not p.is_dir():
            return None
    except Exception:
        return None
    # Raiz de disco e casa do usuario inteira sao grandes demais para
    # organizar de uma vez — e o dono raramente quer isso de verdade.
    try:
        if p.resolve() == Path.home().resolve() or p.resolve().parent == p.resolve():
            return None
    except Exception:
        return None
    return p


def _categoria(arquivo: Path) -> str:
    ext = arquivo.suffix.lower()
    for nome, exts in CATEGORIAS.items():
        if ext in exts:
            return nome
    return OUTROS


def _destino_livre(pasta: Path, nome: str) -> Path:
    alvo = pasta / nome
    if not alvo.exists():
        return alvo
    raiz, ext = Path(nome).stem, Path(nome).suffix
    for n in range(2, 1000):
        alvo = pasta / f"{raiz} ({n}){ext}"
        if not alvo.exists():
            return alvo
    raise RuntimeError("nome repetido demais")


def _diarios_de(pasta: Path | None) -> list[Path]:
    """Os diarios ainda NAO desfeitos, do mais novo para o mais velho."""
    DIARIOS.mkdir(parents=True, exist_ok=True)
    saida = []
    for arq in sorted(DIARIOS.glob("*.json"), reverse=True):
        try:
            d = json.loads(arq.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("desfeito"):
            continue
        if pasta is not None and Path(d.get("pasta", "")) != pasta:
            continue
        saida.append(arq)
    return saida


def organizar(pasta: Path) -> str:
    if not pasta.is_dir():
        return f"Nao achei a pasta {pasta}."
    movidos, por_categoria, pulados = [], {}, 0
    criadas = set()
    for item in sorted(pasta.iterdir()):
        try:
            if item.is_dir() or item.name.startswith("."):
                continue
            if item.name.lower() in _PULAR_NOME or item.suffix.lower() in _PULAR_EXT:
                continue
            cat = _categoria(item)
            destino_dir = pasta / cat
            if not destino_dir.exists():
                destino_dir.mkdir()
                criadas.add(str(destino_dir))
            destino = _destino_livre(destino_dir, item.name)
            shutil.move(str(item), str(destino))
            movidos.append({"de": str(item), "para": str(destino)})
            por_categoria[cat] = por_categoria.get(cat, 0) + 1
        except Exception:
            # Arquivo aberto em outro programa, sem permissao, etc. Fica.
            pulados += 1
    # Pasta de categoria que ficou vazia (tudo falhou) nao precisa existir.
    for c in list(criadas):
        try:
            if not any(Path(c).iterdir()):
                Path(c).rmdir()
        except Exception:
            pass
    if not movidos:
        return (f"A pasta {pasta.name} ja esta organizada: nao havia arquivo "
                f"solto para mover." + (f" {pulados} nao deu para mover." if pulados else ""))
    DIARIOS.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    diario = DIARIOS / f"{carimbo}-{re.sub(r'[^A-Za-z0-9]+', '_', pasta.name)[:30]}.json"
    diario.write_text(json.dumps({
        "pasta": str(pasta), "quando": carimbo, "movimentos": movidos,
        "pastas_criadas": sorted(criadas), "desfeito": False,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    partes = [f"{n} em {c}" for c, n in sorted(por_categoria.items(), key=lambda x: -x[1])]
    resumo = ", ".join(partes[:6])
    if len(partes) > 6:
        resumo += " e mais"
    texto = (f"Pronto. Organizei a pasta {pasta.name}: "
             f"{_falado(len(movidos), 'arquivo', 'arquivos')} em "
             f"{_falado(len(por_categoria), 'pasta', 'pastas')} — {resumo}.")
    if pulados:
        texto += f" {_falado(pulados, 'arquivo ficou', 'arquivos ficaram')} onde estava, por estar em uso."
    texto += " Se quiser voltar como estava, e so me pedir."
    return texto


def desfazer(pasta: Path | None) -> str:
    diarios = _diarios_de(pasta)
    if not diarios:
        onde = f" da pasta {pasta.name}" if pasta else ""
        return f"Nao tenho nenhuma organizacao{onde} para desfazer."
    arq = diarios[0]
    d = json.loads(arq.read_text(encoding="utf-8"))
    voltaram, ficaram = 0, 0
    for mov in reversed(d.get("movimentos", [])):
        de, para = Path(mov["de"]), Path(mov["para"])
        try:
            if para.exists() and not de.exists():
                de.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(para), str(de))
                voltaram += 1
            else:
                ficaram += 1
        except Exception:
            ficaram += 1
    for c in d.get("pastas_criadas", []):
        try:
            p = Path(c)
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        except Exception:
            pass
    d["desfeito"] = True
    d["desfeito_em"] = datetime.now().strftime("%Y%m%d-%H%M%S")
    arq.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    nome = Path(d.get("pasta", "")).name or "a pasta"
    texto = f"Desfeito. {_falado(voltaram, 'arquivo voltou', 'arquivos voltaram')} para o lugar em {nome}."
    if ficaram:
        texto += (f" {_falado(ficaram, 'arquivo', 'arquivos')} nao "
                  f"{'voltou' if ficaram == 1 else 'voltaram'} porque ja tinha sido mexido depois.")
    return texto


def organizar_pasta(parameters: dict | None = None, player=None, **_ignorado) -> str:
    """Entrada da ferramenta. `acao`: organizar (padrao) ou desfazer."""
    p = parameters or {}
    acao = str(p.get("acao") or p.get("action") or "organizar").strip().lower()
    acao = {"organize": "organizar", "undo": "desfazer", "voltar": "desfazer",
            "reverter": "desfazer", "restaurar": "desfazer"}.get(acao, acao)
    nome = str(p.get("pasta") or p.get("path") or p.get("folder") or "")

    def _log(msg: str) -> None:
        if player is not None and hasattr(player, "write_log"):
            try:
                player.write_log(msg)
            except Exception:
                pass

    if acao == "desfazer":
        pasta = _resolver(nome) if nome.strip() else None
        r = desfazer(pasta)
    else:
        # SEM NOME, OU APONTANDO PARA A TELA: e a pasta que esta aberta.
        #
        # Antes, nome vazio caia direto em Downloads. O dono dizia "organiza
        # essa pasta" olhando para outra coisa e o JARVIS mexia na Downloads
        # dele, calado. Errar de pasta ao MOVER arquivo e pior do que nao
        # fazer nada.
        pasta = None
        if not nome.strip() or _e_apontando(nome):
            pasta = _pasta_na_tela(player, _log)
        if pasta is None and nome.strip() and not _e_apontando(nome):
            pasta = _resolver(nome)
        if pasta is None:
            if not nome.strip() or _e_apontando(nome):
                r = ("Nao consegui ver que pasta e essa. Abra a pasta no "
                     "Explorer e me peca de novo, ou diga o nome dela — "
                     "Downloads, Documentos, Area de Trabalho.")
            else:
                r = ("Nao achei essa pasta, ou ela e grande demais para "
                     "organizar de uma vez. Diga uma pasta como Downloads, "
                     "Documentos ou a Area de Trabalho.")
        else:
            t0 = time.monotonic()
            r = organizar(pasta)
            r_t = time.monotonic() - t0
            if r_t > 1.0 and player is not None and hasattr(player, "write_log"):
                try:
                    player.write_log(f"[organizar_pasta] {r_t:.1f}s")
                except Exception:
                    pass
    try:
        if player is not None and hasattr(player, "write_log"):
            player.write_log(f"[organizar_pasta] {r}")
    except Exception:
        pass
    return r
